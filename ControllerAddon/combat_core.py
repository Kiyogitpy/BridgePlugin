# combat_core.py
#
# The shared fight loop: pick a target from a config's monster list, fight it, keep
# prayer/food/potions topped up, loot, bury bones, bank when needed. Two routines drive it:
#   routines/combat.py  -- config straight from the Controller Settings panel (GET /slayer-config)
#   routines/slayer.py  -- the task orchestrator, which substitutes the current task's monsters
#                          into that config (combat_config) and delegates each FIGHT tick here.
# Moved here from routines/slayer.py 2026-09-18 (git mv; the history is that file's).
# See com.example.controller.ControllerConfig / ConfigExporter for the config shape.

import math
import random
import time

import banker
import controller
import debug_log
import settings
import travel
import worldmap


# Re-aim checkpoints for an engage click. MEASURED 2026-08-24 with bench_hover.py against a moving
# NPC: refresh_legs=3 recovered ~74% of the target's in-flight drift for a 613ms flight, while
# refresh_legs=6 cost 1140ms for no measurable accuracy gain -- 3 is the sweet spot and fits
# inside a human's own measured 0.6-1.0s click-after-walk budget. Only the v1 engine uses this;
# the v2 engine (human_move.py) re-aims at its own submovement boundaries and ignores it.
ENGAGE_REFRESH_LEGS = 3

def on_event(ctx, event_type, payload):
    if event_type == "CHAT_MESSAGE" and payload and is_bracelet_broken_message(payload.get("message")):
        ctx.state.data["bracelet_check_at"] = 0.0     # check the gloves slot on the next tick
    if event_type == "NPC_DIED" and payload:
        # Matched by instance index, not type id -- a different monster of
        # the same species dying nearby must not clear our current target.
        if payload.get("index") == ctx.state.data.get("current_target_index"):
            ctx.state.data["current_target_index"] = None


def fight_tick(ctx):
    config = ctx.state.config

    ammo = controller.get_ammo_count()
    if ammo is not None and ammo <= 0:
        ctx.state.trigger_safety_stop("out of ammo")
        return

    bank_cfg = config.get("bank", {})
    if bank_cfg.get("enabled") and needs_banking(ctx):
        was_critical = out_of_critical_supplies(ctx)
        reason = "out of critical supplies" if was_critical else "inventory full"
        debug_log.log("bank", f"{reason}, banking")
        ctx.state.data["current_target_index"] = None  # don't try to resume a fight through a bank trip
        # deposit_inventory only ever reads keepItems off the config dict passed in -- it has no
        # idea protected_supply_names() exists. CONFIRMED live 2026-08-06 (user's Slayer ring,
        # herb sack, seed box, bonecrusher, ash sanctifier, and ectoplasmator all got deposited
        # on the very first bank trip after adding them to protected_supply_names(): that
        # function is only ever consulted by has_bankable_loot, for the "is banking even worth
        # it" decision -- it was never wired into what actually gets deposited once a trip
        # starts). Merge it into the keepItems this call site actually passes down instead of
        # trusting the two to somehow already agree.
        merged_bank_cfg = bank_config(config)
        # Auto-pick whatever food/prayer restore is actually in the bank rather than relying
        # solely on Controller Settings' explicit withdraw list -- CONFIRMED (user request
        # 2026-08-06), and directly closes the gap that let a bank trip "succeed" without ever
        # restocking anything (see out_of_critical_supplies/BANK_NO_PROGRESS_MAX above). Gated
        # on the same enabled flags maintain_supplies itself already checks, so this doesn't
        # withdraw food for a routine that has food handling turned off entirely.
        # Walk back to the tile we left from -- USER-REPORTED 2026-09-07 ("ends up stuck at
        # Ferox"). Opt-in because banker's default only walks home from the Ferox teleport
        # path, which left a trip to a bank inside maxBankDistance (40) simply fighting on
        # next to the bank. A slayer task is a place; every other bank_up caller re-derives
        # its own position afterwards and must not pay for this walk.
        merged_bank_cfg["returnToSpot"] = True
        banker.bank_up(ctx, merged_bank_cfg)

        if was_critical and out_of_critical_supplies(ctx):
            # The trip completed but the emergency is still unresolved -- CONFIRMED live
            # 2026-08-06: happens when Controller Settings' bank withdraw list doesn't actually
            # include food/prayer potions (or is empty), so there's nothing to restock even
            # though the trip itself succeeds every time. Unbounded, this loops forever: open
            # bank, deposit nothing new, close, re-trigger next tick, repeat -- wasted clicks
            # with zero path back to safety. A few consecutive no-progress trips is a strong
            # enough signal that this needs a human to fix the config, not another retry.
            stuck = ctx.state.data.get("bank_no_progress_count", 0) + 1
            ctx.state.data["bank_no_progress_count"] = stuck
            if stuck >= BANK_NO_PROGRESS_MAX:
                ctx.state.trigger_safety_stop(
                    "out of critical supplies and repeated bank trips aren't restocking them -- "
                    "check Controller Settings' bank withdraw list"
                )
        else:
            ctx.state.data["bank_no_progress_count"] = 0
        return

    combat = controller.get_combat()
    maintain_defensive_prayer(ctx, combat)
    maintain_offensive_prayer(ctx, combat)
    maintain_prayer_potions(ctx)
    maintain_supplies(ctx)
    maintain_bracelet(ctx)

    # Still owed a walk home from a bank trip -- USER-REPORTED 2026-09-07 ("ends up stuck at
    # Ferox"). banker.bank_up walks back itself, but a walk that gives up partway (a long
    # cross-map return is exactly where WALK_MAX_STALLS' cumulative budget of 4 runs out) now
    # KEEPS the anchor instead of discarding it, and this is what spends the retry. Placed after
    # the prayer/food maintenance above so a return trip can't starve eating or prayer, and
    # before loot/safespot/engage so we don't commit to a fight at the bank we were about to
    # walk away from. Costs nothing on a normal tick: with no anchor recorded return_to_spot
    # returns immediately.
    if ctx.state.data.get(banker.RETURN_LOCATION_KEY) is not None:
        ctx.state.data["current_target_index"] = None
        banker.return_to_spot(ctx)
        return

    if config.get("loot", {}).get("enabled"):
        maybe_loot(ctx)

    maintain_safespot(ctx)
    engage_target(ctx, combat)


def inventory_full() -> bool:
    return len(controller.get_inventory()) >= settings.INVENTORY_SIZE


# Consecutive bank trips that completed but left a critical-supply emergency unresolved before
# the routine stops and asks a human to fix the withdraw list. Referenced by fight_tick's bank
# branch since 2026-08-06; DEFINED only on 2026-09-18 (it raised NameError before that).
BANK_NO_PROGRESS_MAX = 3


def bank_config(config: dict) -> dict:
    """The bank_up config this routine banks with. Extracted 2026-09-21 so the break path banks
    on exactly the same terms as an ordinary trip -- two copies of this would drift.

    deposit_inventory only ever reads keepItems off the config dict passed in -- it has no idea
    protected_supply_names() exists. CONFIRMED live 2026-08-06 (user's Slayer ring, herb sack,
    seed box, bonecrusher, ash sanctifier and ectoplasmator all got deposited on the very first
    bank trip after adding them to protected_supply_names(): that function is only ever consulted
    by has_bankable_loot, for the "is banking even worth it" decision -- it was never wired into
    what actually gets deposited once a trip starts). Merge it into the keepItems the call site
    actually passes down instead of trusting the two to somehow already agree.

    autoFood/autoPrayer let the banker pick whatever food or prayer restore is really in the bank
    rather than relying solely on the explicit withdraw list (USER 2026-08-06) -- gated on the same
    enabled flags maintain_supplies itself checks, so a routine with food handling off withdraws
    none.
    """
    bank_cfg = config.get("bank", {})
    merged = dict(bank_cfg)
    merged["keepItems"] = sorted(
        {*(n.lower() for n in bank_cfg.get("keepItems", [])), *protected_supply_names(config)}
    )
    merged["autoFood"] = config.get("food", {}).get("enabled", False)
    merged["autoPrayer"] = config.get("potions", {}).get("prayerEnabled", False)
    return merged


def bank_before_break(ctx) -> None:
    """The engine's before_break hook: spend the break AT A BANK, not among the monsters.

    tick() is SKIPPED for the whole break -- 3 to 12 minutes with this project's default settings
    -- so nothing eats, nothing drinks and nothing retaliates. Standing among aggressive monsters
    for that long is a death and an item loss, which is what an unattended run must not risk. This
    hook runs fully live, before the break starts, and the break waits for it to return.

    So: let the target go, bank on the routine's own terms with returnToSpot FALSE (the break is
    taken at the bank, which is safe and where the loot belongs anyway), then put the return anchor
    back. combat_core's own per-tick return_to_spot -- which already handles the Ferox case with a
    teleport the trip withdrew, because Ferox is a sealed island on the collision map -- walks home
    once the break ends.

    It does NOTHING when banking is switched off (there is no safe place to go, so the break is
    taken where we stand, exactly as before), when a return trip is already under way, or when the
    trip fails -- inventing a way home from an unknown position would send the routine walking off
    after the break. UNVERIFIED LIVE as of 2026-09-21."""
    config = ctx.state.config or {}
    if not (config.get("bank") or {}).get("enabled"):
        return
    if ctx.state.data.get(banker.RETURN_LOCATION_KEY) is not None:
        return
    spot = travel.save_location()
    debug_log.log("bank", "a break is due -- banking first and taking it there")
    ctx.state.data["current_target_index"] = None
    merged = bank_config(config)
    merged["enabled"] = True
    merged["returnToSpot"] = False
    if banker.bank_up(ctx, merged) and spot is not None:
        ctx.state.data[banker.RETURN_LOCATION_KEY] = spot
        ctx.state.data[banker.RETURN_ATTEMPTS_KEY] = 0


def needs_banking(ctx) -> bool:
    if out_of_critical_supplies(ctx):
        return True

    if not inventory_full():
        return False

    if not has_bankable_loot(ctx.state.config):
        # Full, but only with supplies we brought on purpose (food, potions,
        # the ring, the bag, whatever's on the keep list) -- there's nothing
        # to gain from a bank trip, we'd just deposit and rewithdraw the same
        # things. This is also what stops banking from firing immediately on
        # a trip that simply started with a full inventory of supplies.
        return False

    return True


def out_of_critical_supplies(ctx) -> bool:
    """True if the player is low on HP or Prayer with nothing left in inventory to restore
    it -- CONFIRMED live 2026-08-06 (user report: low hp, low prayer, empty food AND potions,
    still just fighting on). Before this, needs_banking only ever fired on a full inventory, so
    running dry on consumables mid-task had no path back to a bank at all -- maintain_supplies /
    maintain_defensive_prayer's consume_first() calls just silently no-op on an empty category
    (see consume_first's own early-return), so the routine would keep fighting, drinking and
    eating nothing, until something actually killed the character. Checked independently of
    inventory fullness, since supplies can run out with plenty of inventory space still free."""
    config = ctx.state.config
    player = controller.get_player()
    consumables = controller.get_consumables()

    food_cfg = config.get("food", {})
    if food_cfg.get("enabled"):
        hp = player.get("hp")
        if hp is not None and hp <= food_cfg.get("eatAt", 0) and not consumables.get("food"):
            return True

    prayer_cfg = config.get("prayer", {})
    potions_cfg = config.get("potions", {})
    if potions_cfg.get("prayerEnabled"):
        current_prayer = player.get("prayer")
        restore_at = prayer_cfg.get("restoreAt", 18)
        if current_prayer is not None and current_prayer <= restore_at and not consumables.get("prayer"):
            return True

    return False


def has_bankable_loot(config: dict) -> bool:
    # Substring match, not exact -- item names carry dose/charge suffixes
    # (e.g. "Super restore(4)") that a configured preference like "Super
    # restore" won't equal exactly. Matches the same convention
    # ConsumablesProvider uses server-side.
    protected_names = protected_supply_names(config)
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if name and not any(protected in name for protected in protected_names):
            return True
    return False


def protected_supply_names(config: dict) -> set:
    names = {n.lower() for n in config.get("bank", {}).get("keepItems", [])}

    food_name = config.get("food", {}).get("name")
    if food_name:
        names.add(food_name.lower())

    prayer_potion = config.get("potions", {}).get("prayerPotion")
    if prayer_potion:
        names.add(prayer_potion.lower())

    combat_potion = config.get("potions", {}).get("combatPotion")
    if combat_potion:
        names.add(combat_potion.lower())

    names.add(travel.RING_OF_DUELING_NAME)
    names.add(travel.ROYAL_SEED_POD_NAME)
    names.add(banker.LOOTING_BAG_NAME)
    names.add(banker.HERB_SACK_NAME)
    names.add(banker.SEED_BOX_NAME)

    # Standing Slayer gear -- brought on every trip, never task loot -- CONFIRMED (user request
    # 2026-08-06): protected by base name, deliberately without any charge-count suffix (e.g.
    # "slayer ring", not "slayer ring (8)") since has_bankable_loot/deposit_inventory already
    # match by substring -- a charged item's actual inventory name carries its current charge
    # count (Slayer ring (8), Ring of dueling(3), ...), a different string each time it ticks
    # down, so protecting the exact charge-suffixed name seen at any one moment would stop
    # matching the same physical ring the very next time its charge changes.
    names.add("slayer ring")
    names.add("bonecrusher")
    names.add("ash sanctifier")
    names.add("ectoplasmator")
    names.update(SLAYER_BRACELET_NAMES)   # spares for maintain_bracelet
    return names


# ---------------- config-driven targeting ----------------
def _singularize(word: str) -> str:
    """Best-effort strip of a plural task-category label (e.g. as picked from
    the Controller Settings search bar's SlayerMonsters.java list) down to a
    form likely to appear inside the real, singular NPC name it actually
    covers -- "Bloodvelds" -> "bloodveld", "Zombies" -> "zombie", "Lizardmen"
    -> "lizardman" (irregular -men/-man plural). Slayer task category labels
    are frequently NOT literal NPC names at all (there's no NPC actually
    named "Lizardmen" -- the real ones are "Lizardman shaman", "Lizardman
    brute", etc.), so pick_target below matches substrings against this
    stripped form rather than requiring an exact name match."""
    w = word.lower().strip()
    if w.endswith("men") and not w.endswith("omen"):
        return w[:-3] + "man"
    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def only_ids(config: dict) -> set:
    """Ids a target MUST have, or an empty set meaning "no opinion".

    Distinct from `targets`, which is ADDITIVE (an id there matches a monster the names miss).
    This one SUBTRACTS: it is how a task says "these monsters and no others", which names alone
    cannot express when two different monsters share one.
    """
    return set(config.get("onlyNpcIds") or ())


def resolve_targets(config: dict):
    ids = set(config.get("targets") or [])
    names = set()
    for n in (config.get("targetNames") or []):
        n_lower = n.lower().strip()
        if not n_lower:
            continue
        names.add(n_lower)
        names.add(_singularize(n_lower))
    return ids, names


def combat_config(base_config: dict, target_names, task_npc_ids=None) -> dict:
    """The panel config with a task's monsters substituted in -- what the Slayer task
    orchestrator hands fight_tick for each FIGHT tick (spec §5).

    `targets` (ids) is emptied on purpose: ConfigExporter.resolveTargetIds only ever fills it
    with ids of NPCs currently in the scene, so names are the durable signal and a stale id list
    from the panel would fight the wrong monster. A marked safespot belongs to one place, so it
    is forced off for a task fight. The panel config is copied, never mutated."""
    cfg = dict(base_config or {})
    cfg["targetNames"] = list(target_names or ())
    cfg["targets"] = []
    # `task_npc_ids` is the opposite of `targets`: an ALLOW-LIST, and the only way to say "the
    # weak one, not the strong one with the same name". USER 2026-09-21, watching the cave bugs
    # task: "Cave bugs that are significantly more powerful -- with a combat level of 96.
    # Routine should skip these altogether and instead focus on the lower level ones."
    #
    # MEASURED in the cache: 481 `swamp_cave_bug` is level 6 with 5 hitpoints, 483
    # `molanisk_giant_cave_bug` is level 96 with 93 hitpoints and 84 defence -- and BOTH are
    # named exactly "Cave bug" with an Attack option, both in the Dorgesh-Kaan South Dungeon.
    # The session logs show the routine attacking both (`id=481` and `id=483`). Aya's registry
    # already knew the answer (npc_ids for cave bugs is (481,)); nothing was reading it.
    #
    # Left OUT of the config entirely when empty, so a task with no id data (all of Kuradal's)
    # is byte-for-byte what it was.
    if task_npc_ids:
        cfg["onlyNpcIds"] = list(task_npc_ids)
    safespot = dict(cfg.get("safespot") or {})
    safespot["enabled"] = False
    safespot["marked"] = False
    cfg["safespot"] = safespot
    return cfg


# Targets are ranked by WALKING distance, not the bridge's `distance` (a straight-line
# WorldPoint.distanceTo that ignores walls). MEASURED live 2026-09-18 in the Kalphite cave (plane
# 2, player 3487,9508): a Kalphite Worker 10 tiles away in a straight line was 112 steps round the
# tunnel walls (the user's "35 sec walk"), while one 16 tiles away -- outside maxTargetDistance
# 15, so never even a candidate -- was 27 steps. Where the offline map answers, this step budget
# REPLACES maxTargetDistance: walking steps are never fewer than the straight-line tiles, so the
# old limit would still have thrown the 27-step worker away. The flood is cardinal-only, so it
# overcounts diagonal routes a little; that affects the budget, not the ranking much.
DEFAULT_MAX_TARGET_WALK_STEPS = 40


def _walk_steps(flood: dict, npc: dict):
    """Fewest walking steps to stand on or beside `npc`, or None when the flood never got there.
    Its own tile plus the 8 around it: the reported tile of a large NPC is its SW corner."""
    x, y = npc.get("x"), npc.get("y")
    if x is None or y is None:
        return None
    best = None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            steps = flood.get((x + dx, y + dy))
            if steps is not None and (best is None or steps < best):
                best = steps
    return best


def _walk_flood(player: dict | None, config: dict, max_steps: int):
    """{tile: steps} from the player, or None when walking distance does not apply here -- no
    player, an instance (template coords do not index the world map), safespotting (we attack
    from range, so the walk to the monster is not the cost), or a flood that went nowhere."""
    if not player or player.get("inInstance"):
        return None
    if (config.get("safespot") or {}).get("enabled"):
        return None
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is None or py is None or plane is None:
        return None
    side = 2 * max_steps + 1
    flood = worldmap.reachable_from(px, py, plane, radius=max_steps, max_nodes=side * side)
    return flood if len(flood) > 1 else None


def pick_target(npcs: list, config: dict, player: dict | None = None):
    ids, names = resolve_targets(config)
    if not ids and not names:
        return None
    allowed = only_ids(config)

    max_dist = config.get("maxTargetDistance", 15)
    max_steps = config.get("maxTargetWalkSteps", DEFAULT_MAX_TARGET_WALK_STEPS)
    skip_in_combat = config.get("skipNpcInCombat", True)
    flood = _walk_flood(player, config, max_steps)

    candidates = []
    for npc in npcs:
        if not npc.get("attackable"):
            continue
        npc_name = (npc.get("name") or "").lower()
        if npc.get("id") not in ids and not any(n in npc_name for n in names if n):
            continue
        # The name matched but the id is not one this task fights: a same-named monster of a
        # different strength. See combat_config's task_npc_ids.
        if allowed and npc.get("id") not in allowed:
            continue

        dist = npc.get("distance", 9999)
        if dist < 0:
            continue
        if flood is None:
            if dist > max_dist:
                continue
            cost = dist
        else:
            cost = _walk_steps(flood, npc)
            if cost is None or cost > max_steps:
                continue

        if skip_in_combat and npc.get("inCombat") and not npc.get("interactingPlayer"):
            continue

        candidates.append((cost, dist, npc))

    if not candidates:
        return None

    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2]


# Prayer level requirements, mirroring com.example.controller.DefensivePrayerChoice /
# OffensivePrayerChoice on the Java side (keep both in sync if either changes) --
# standard OSRS prayer levels, not re-verified against a live client but stable/
# well-known. Used to skip a manually-picked prayer the account hasn't unlocked yet
# (rather than looping forever re-clicking a locked icon) and to resolve the three
# Auto entries below.
PRAYER_LEVELS = {
    "PROTECT_FROM_MAGIC": 37,
    "PROTECT_FROM_MISSILES": 40,
    "PROTECT_FROM_MELEE": 43,
    "STEEL_SKIN": 28,
    "ULTIMATE_STRENGTH": 31,
    "INCREDIBLE_REFLEXES": 34,
    "CHIVALRY": 60,
    "PIETY": 70,
    "EAGLE_EYE": 44,
    "DEADEYE": 62,
    "RIGOUR": 74,
    "MYSTIC_LORE": 27,
    "MYSTIC_MIGHT": 45,
    "AUGURY": 77,
}

# Best-to-worst per combat style -- AUTO_* resolves to the first entry the account's
# prayer level actually covers. DEADEYE is deliberately not in AUTO_RANGED's ladder:
# unlocking it needs a Royal Titans scroll drop on top of the level requirement,
# which this level-only check can't verify -- accounts that have it unlocked can
# pick it directly instead of via Auto.
AUTO_PRAYER_LADDERS = {
    "AUTO_MELEE": ["PIETY", "CHIVALRY", "STEEL_SKIN"],
    "AUTO_RANGED": ["RIGOUR", "EAGLE_EYE"],
    "AUTO_MAGIC": ["AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"],
}

# maintain_offensive_prayer gives up trying to click the flick-off icon (and just clears its
# own tracking) after this many ticks of the icon lookup failing, rather than staying stuck
# unable to ever detect "off" again.
OFFENSIVE_PRAYER_STUCK_ON_TICKS = 3

# Re-aim checkpoints for the loot right-click's flight. The flight is ~1s and the player is
# usually moving during it, so a few mid-flight corrections are what keep the cursor on a small
# icon; see controller.click_menu_action's refresh_target note.
LOOT_REFRESH_LEGS = 3

# maybe_loot gives up on a ground item after this many click-and-wait attempts each still ending
# with the item sitting there unpicked (see LOOT_PENDING_TIMEOUT_TICKS -- an "attempt" is a full
# click-then-wait-for-resolution cycle, not a per-tick reclick) -- CONFIRMED live 2026-08-05: a
# Grimy ranarr weed dropped in a Gryphon cave room got re-clicked 40+ ticks straight with the
# player's position/animation never changing at all (not even an attempted walk), never picked
# up. GroundItemProvider's "clickable" flag is screen-visibility only (onScreen && alive), with
# no pathfinding/reachability check behind it, so a stuck ground item (e.g. dropped on a tile
# that isn't actually reachable from here) looks identical to a normal one and gets re-targeted
# forever with no error to detect otherwise.
LOOT_STUCK_MAX_ATTEMPTS = 6

# How many ticks maybe_loot waits, after clicking Take on an item, for it to actually disappear
# from the ground before concluding that attempt failed and it's safe to click again. Generous
# enough to cover a genuine multi-tile walk-and-pickup, not just an adjacent one -- CONFIRMED live
# 2026-08-06: this replaced an earlier position-change-based "still walking" inference (comparing
# player position tick to tick) after it both missed cases (this python loop's polling isn't
# tick-locked to the game's own tick, so two checks landing within the same game tick can see
# identical position even mid-walk, letting a second click slip through and cancel the first) and
# needed separate handling to generalize across different items on the same tile. Waiting on the
# item's own presence on the ground is unambiguous and needs none of that.
LOOT_PENDING_TIMEOUT_TICKS = 10

# TileItem.getOwnership() value RuneLite itself uses to mean "belongs to another player, not
# takeable by us yet" -- see GroundItemProvider.java's "ownership" field comment (grounded in
# RuneLite's own bundled Ground Items plugin, GroundItemsPlugin.shouldDisplayItem's TAKEABLE
# case: `ownership != 2`). Shared (non-instanced) areas like this Slayer cave put other real
# players' own kill loot in our client's scene data right alongside ours; without this filter
# maybe_loot can't tell the two apart and will try to click drops that were never ours.
GROUND_ITEM_OWNERSHIP_OTHER = 2

# engage_target gives up on a committed target and re-picks/re-clicks after this many
# consecutive ticks of literally zero player movement while not in combat -- CONFIRMED live
# 2026-08-05: an engaging click on a distant (dist=7) Gryphon drifted badly during cursor
# travel (refresh_target re-aiming mid-move at an NPC that itself moved), landing ~143px from
# the original aim point and missing the NPC entirely. current_target_index gets committed
# unconditionally once click_entity doesn't abort, with no check that combat actually started,
# so the routine froze on that target permanently -- it's still "alive" (never hit) and no
# NPC_DIED ever fires, so the still-alive branch just returns every tick forever. A real
# engagement always starts moving toward the target or entering combat within a couple ticks,
# so "not in combat AND hasn't moved at all" for several ticks straight only happens when the
# engaging click missed -- never during a normal walk-up, which shows position changing tick
# to tick.
ENGAGE_STUCK_MAX_TICKS = 5

# engage_target only runs the ENGAGE_STUCK_MAX_TICKS check within this many seconds of
# committing to a target -- see the check's own comment in engage_target for why: it exists to
# catch a missed *initial* engaging click (which always shows up within a few ticks), not to
# keep re-litigating an already-successful, ongoing fight where a routine mid-fight eat/drink can
# otherwise be mistaken for a failed engagement.
ENGAGE_STUCK_WINDOW_S = 10

# Backstop past ENGAGE_STUCK_WINDOW_S: if a commitment has NEVER once shown real combat evidence
# (combat.inCombat or the target's own interactingPlayer) by this many seconds after committing,
# give it up regardless of the window-expiry "trust the fight" shortcut above -- CONFIRMED live
# 2026-08-06 in a packed Wilderness Slayer cave (many Turoths in melee range of each other): the
# per-tick stuck-ticks check inside the window resets on *any* position change, not just real
# combat progress, and getting jostled by the crowd changed the player's tile just often enough
# to keep resetting the counter before it ever reached ENGAGE_STUCK_MAX_TICKS -- with zero actual
# combat the entire time (combat.inCombat stayed False, the target's interactingPlayer stayed
# False). Once the window expired, the shortcut started trusting the fight unconditionally and
# never checked anything again -- current_target_index stayed committed to an adjacent,
# attackable, never-fought Turoth (hpRatio -1, i.e. "never damaged" rather than 0/"dead", so
# still_alive kept reading True) and the routine just stood there idle for minutes. Requiring at
# least one real engagement signal by this deadline -- not just "some tile changed at some
# point" -- catches this without reintroducing the original eat/drink false-positive: a fight
# that's actually landed a hit sets engage_ever_confirmed and is exempted from this check for the
# rest of that commitment, same as today.
ENGAGE_NEVER_CONFIRMED_TIMEOUT_S = 20

# The other half of that backstop, and the one that was missing. Past ENGAGE_STUCK_WINDOW_S a
# commitment that HAS connected was trusted forever -- `return` with no deadline of any kind --
# so a fight that ends WITHOUT the NPC dying (it loses aggro, wanders off, a re-attack never
# lands) left the routine committed to a monster it was not fighting, for as long as that monster
# stayed in the scene.
#
# FOUND LIVE 2026-09-21, gargoyles. USER: "why are we idle?" Last attack 07:31:03 on index 29328;
# four minutes later the log held nothing but idle drift, one eat and three prayer sips, while
# /npcs still listed 29328 three tiles away, /combat said inCombat:false isIdle:true
# animation:-1, and ELEVEN gargoyles within 13 tiles went untouched.
#
# So past the window the commitment is still trusted, but only while the player is doing
# SOMETHING -- in combat, moving, or mid-animation. This is deliberately long: the window exists
# because a mid-fight eat or drink makes inCombat misread (2026-08-06), and those last a tick or
# two, nowhere near this. It is read off the `combat` dict already in hand, so it costs no extra
# request. Giving up early merely re-clicks the same target, which is harmless; the bug it
# replaces was an unbounded hang.
ENGAGE_IDLE_GIVE_UP_S = 12

# maybe_loot ignores minLootValue for these grimy herbs whenever an open herb sack is carried --
# it auto-stores them for free regardless of GE value, so the value gate only exists to avoid
# clutter for someone who isn't carrying one. Sourced from the herb sack's own storage list
# (data/raw/H/Herb sack.txt). Guam, marrentill, tarromin, and harralander are deliberately
# excluded -- too low-value to bother with even given the free storage.
HERB_SACK_BYPASS_NAMES = {
    "grimy ranarr weed", "grimy toadflax", "grimy irit leaf", "grimy avantoe", "grimy kwuarm",
    "grimy huasca", "grimy snapdragon", "grimy cadantine", "grimy lantadyme", "grimy dwarf weed",
    "grimy torstol",
}

# Same reasoning as HERB_SACK_BYPASS_NAMES, for herb and tree seeds whenever an open seed box is
# carried. Sourced from the wiki's Herb patch/Tree patch seed tables (data/raw/H/Herb
# patch_Seeds.txt, data/raw/T/Tree patch_Seeds.txt) -- fruit tree seeds are a separate wiki
# category and deliberately not included here.
SEED_BOX_BYPASS_NAMES = {
    "guam seed", "marrentill seed", "tarromin seed", "harralander seed", "ranarr seed",
    "toadflax seed", "irit seed", "avantoe seed", "kwuarm seed", "huasca seed",
    "snapdragon seed", "cadantine seed", "lantadyme seed", "dwarf weed seed", "torstol seed",
    "acorn", "willow seed", "maple seed", "yew seed", "magic seed",
}

# maintain_safespot walks back to the marked safespot tile once the player is more than this
# many tiles off of it -- 0 means "must be standing on the exact tile", matching the point of a
# safespot (travel.walk_to's own default 3-tile WALK_ARRIVAL_TOLERANCE is tuned for bank-trip-scale
# travel and could still leave the player inside a melee monster's reach).
SAFESPOT_RETURN_TOLERANCE = 0

# maintain_safespot refuses to attempt a walk-back at all if the marked tile is more than this
# many tiles away -- CONFIRMED live 2026-08-06: a safespot marked while inside the Blast Furnace
# minigame (an instanced area) got stored as a raw instance-template coordinate hundreds of tiles
# from anywhere real (SafespotMarker.java's WorldPoint.fromScene wasn't instance-aware; fixed to
# use WorldPoint.fromLocalInstance instead), and maintain_safespot blindly tried to walk there --
# blocking the entire tick() loop (no eating, no prayer, nothing) for minutes while repeatedly
# re-clicking the same unreachable minimap point. A real safespot return is never this far (it's
# correcting a few tiles of drift from looting or a pull attack, not crossing the map); a distance
# this large means the stored tile is stale/wrong, not a legitimate anchor to chase.
SAFESPOT_MAX_RETURN_DISTANCE = 30


def maintain_safespot(ctx):
    """Walks back to the marked safespot tile whenever the player has drifted off of it -- e.g.
    after stepping out to loot something, or getting dragged by a monster's pull attack. Runs
    every tick regardless of combat state (not just when picking a fresh target), since that's
    what actually catches a mid-fight drag rather than just anchoring at the start of each
    fight."""
    config = ctx.state.config
    safespot = config.get("safespot", {})
    if not safespot.get("enabled") or not safespot.get("marked"):
        return

    # Don't override an in-progress loot pickup with a competing walk-back click -- CONFIRMED
    # live 2026-08-06 (user report: bot abandoned a loot item mid-pickup to walk back to the
    # safespot). maybe_loot tracks the ground item it's currently attempting via
    # loot_stuck_key/loot_stuck_attempts; while that's still actively being retried (below
    # LOOT_STUCK_MAX_ATTEMPTS), let it finish or give up on its own rather than stomping the
    # click. Once maybe_loot itself has given up on a genuinely unreachable item (attempts hits
    # the cap), stop deferring -- that key never clears on its own since the item never leaves
    # the ground, so waiting for it to clear would deadlock maintain_safespot forever.
    if ctx.state.data.get("loot_stuck_key") is not None \
            and ctx.state.data.get("loot_stuck_attempts", 0) < LOOT_STUCK_MAX_ATTEMPTS:
        return

    player = controller.get_player()
    if player.get("x") is None:
        return

    tile = {"x": safespot["x"], "y": safespot["y"], "plane": safespot["plane"]}
    if player.get("x") == tile["x"] and player.get("y") == tile["y"] and player.get("plane") == tile["plane"]:
        ctx.state.data["safespot_too_far_warned"] = False
        return

    dist = math.hypot(tile["x"] - player.get("x"), tile["y"] - player.get("y"))
    if tile["plane"] != player.get("plane") or dist > SAFESPOT_MAX_RETURN_DISTANCE:
        if not ctx.state.data.get("safespot_too_far_warned"):
            debug_log.log("safespot", f"marked safespot ({tile['x']},{tile['y']},{tile['plane']}) is "
                                       f"{dist:.0f} tiles away or on a different plane -- refusing to "
                                       f"auto-walk there, this looks like a stale/wrong mark. Re-mark it "
                                       f"from your current location, or turn off Use Safespot.")
            ctx.state.data["safespot_too_far_warned"] = True
        return
    ctx.state.data["safespot_too_far_warned"] = False

    debug_log.log("safespot", f"drifted from safespot (at ({player.get('x')},{player.get('y')}), "
                               f"safespot is ({tile['x']},{tile['y']})) -- returning")
    # Don't try to resume a fight through a walk this long -- same reasoning as needs_banking's
    # bank trip. Without this, engage_target sees an old, now-meaningless committed target/position
    # (recorded before this multi-tick walk started) and just sits there "not in combat, not
    # moving" for ENGAGE_STUCK_MAX_TICKS before it notices and re-engages on its own -- CONFIRMED
    # live 2026-08-06 (user report: had to manually re-click the enemy after returning to a
    # safespot). Clearing this here means engage_target is free to pick/click a fresh target the
    # very next tick, no artificial delay.
    ctx.state.data["current_target_index"] = None
    ctx.state.data["engage_last_pos"] = None
    ctx.state.data["engage_stuck_ticks"] = 0
    travel.walk_to(ctx, tile, tolerance=SAFESPOT_RETURN_TOLERANCE, goal_adjacent=False)


def resolve_combat_prayer(combat_prayer: str, prayer_level):
    """Resolve a Defensive/Offensive Prayer config value down to a concrete prayer name
    (a key into PrayerProvider's active/icons maps), or None if nothing should be active
    right now. Auto (Melee/Ranged/Magic) walks its ladder and returns the first entry the
    account's level covers. A manually-picked prayer the account hasn't unlocked yet also
    resolves to None -- surfaced once via the caller's own warning, rather than silently
    retried forever."""
    if not combat_prayer or combat_prayer == "NONE":
        return None

    ladder = AUTO_PRAYER_LADDERS.get(combat_prayer)
    if ladder is not None:
        if prayer_level is None:
            return None
        for candidate in ladder:
            if prayer_level >= PRAYER_LEVELS.get(candidate, 999):
                return candidate
        return None

    required = PRAYER_LEVELS.get(combat_prayer)
    if required is not None and (prayer_level is None or prayer_level < required):
        return None

    return combat_prayer


def maintain_defensive_prayer(ctx, combat: dict):
    """Keeps a single defensive (Protect from X) prayer on for the whole fight --
    LAZY behavior (see docs/superpowers/specs/2026-07-29-split-defensive-offensive-prayer-design.md),
    unlike maintain_offensive_prayer's flick-on/flick-off. Also drives prayer-potion
    upkeep, since that's independent of which prayer(s) are actually selected."""
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    if not prayer_cfg.get("enabled"):
        return

    prayers = controller.get_prayers()
    player = controller.get_player()

    raw_defensive_prayer = prayer_cfg.get("defensivePrayer", "NONE")
    defensive_prayer = resolve_combat_prayer(raw_defensive_prayer, player.get("prayerReal"))

    # combat.inCombat (Player.getInteracting() != null) can read False for a tick during a
    # non-combat action like eating/drinking -- CONFIRMED by user report of prayer flicking off
    # mid-fight right when eating/drinking. Same flakiness engage_target already works around
    # by preferring "is our tracked target still alive" over this flag -- OR it in here too so
    # a live tracked target holds prayer on through the same momentary misread.
    still_fighting = bool(combat.get("inCombat")) or ctx.state.data.get("current_target_index") is not None

    if prayer_cfg.get("useQuickPrayers"):
        want_on = still_fighting
        is_on = prayers.get("quickPrayerEnabled", False)
        if want_on != is_on:
            ox, oy = prayers.get("quickOrbScreenX"), prayers.get("quickOrbScreenY")
            if ox is not None and oy is not None:
                controller.click_entity(ctx.ser, {"screenX": ox, "screenY": oy}, offset=(0, 0))
    elif defensive_prayer:
        # `holdDefensive` keeps the prayer on for the WHOLE trip instead of flicking it off the
        # moment there is no target. USER 2026-09-21, having watched the first gargoyle task do
        # exactly that: "Leave the protection on for the duration of the task. since its a very
        # short time between fights anyway."
        #
        # MEASURED in that run: off at 07:16:23, the next gargoyle engaged at 07:16:24, back on at
        # 07:16:26 -- about 2.5 s unprotected at melee range on EVERY kill, plus two clicks a kill
        # over a 183-kill task. Nothing but routines/slayer.py sets this key, and only for a
        # catalog task that names its own prayer, so with it absent this is the lazy flick it has
        # always been and combat.py is unchanged.
        want_on = still_fighting or bool(prayer_cfg.get("holdDefensive"))
        is_on = prayers.get("active", {}).get(defensive_prayer, False)
        if want_on != is_on:
            # Prayer icons only get live screen coords while the Prayer tab is open --
            # switch to it, then re-fetch (the "prayers" snapshot above was taken before
            # we knew we'd need it, so its "icons" may still be from whatever tab was
            # open a moment ago).
            controller.open_prayer_tab(ctx.ser)
            icon = controller.get_prayers().get("icons", {}).get(defensive_prayer)
            if icon:
                debug_log.log("prayer", f"{'turning on' if want_on else 'turning off'} {defensive_prayer}")
                controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    elif raw_defensive_prayer in (None, "", "NONE"):
        if not ctx.state.data.get("warned_no_defensive_prayer"):
            debug_log.log("prayer", "usePrayer is on with useQuickPrayers off, but Defensive Prayer is "
                                     "set to None in Controller Settings -- nothing to toggle. Pick a prayer there "
                                     "(e.g. Protect from Melee) or enable Use Quick Prayers instead.")
            ctx.state.data["warned_no_defensive_prayer"] = True
    elif not ctx.state.data.get("warned_defensive_prayer_level_too_low"):
        # Either a manually-picked prayer this account hasn't unlocked, or an Auto ladder
        # with nothing on it affordable yet -- either way, nothing usable right now. Surfaced
        # once rather than resolve_combat_prayer's None silently looking identical to "nothing
        # configured", which would otherwise be confusing to debug from the logs alone.
        debug_log.log("prayer", f"Defensive Prayer is set to {raw_defensive_prayer} in Controller "
                                 f"Settings, but nothing in that selection is usable yet at prayer level "
                                 f"{player.get('prayerReal')} -- nothing to toggle until it's leveled up.")
        ctx.state.data["warned_defensive_prayer_level_too_low"] = True


def maintain_prayer_potions(ctx):
    """Drink a prayer restore when prayer runs low. Gated ONLY on the potions toggle --
    USER-REPORTED 2026-09-18 ("why arent we using prayer pots?"): this used to live at the end of
    maintain_defensive_prayer, behind its `prayer.enabled` early return, so a player praying by
    hand (Use Prayer off, prayer potions on) was never topped up -- live at 28/77 prayer with four
    Prayer potions in the inventory. Who switches prayers on has nothing to do with whether the
    points need restoring."""
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    potions_cfg = config.get("potions", {})
    if potions_cfg.get("prayerEnabled"):
        player = controller.get_player()
        restore_at = prayer_cfg.get("restoreAt", 18)
        current_prayer, prayer_max = player.get("prayer"), player.get("prayerReal")
        if current_prayer is None:
            return

        # Drinking at the exact same prayer point every time is a tell -- roll a fresh
        # threshold somewhere in [restore_at, restore_at + spread] instead, so some fights
        # get topped up right at the floor and others earlier, same as a real player
        # glancing at the orb rather than watching it like a hawk. Sticks until a drink
        # actually happens, then re-rolls for next time.
        threshold = ctx.state.data.get("prayer_drink_threshold")
        if threshold is None:
            # Clamp below prayer_max -- otherwise a low real Prayer level (restore_at + spread
            # exceeding max) would make should_drink permanently True instead of "occasionally
            # a bit early".
            spread_max = restore_at + settings.PRAYER_RESTORE_SPREAD
            if prayer_max:
                spread_max = min(spread_max, prayer_max)
            threshold = random.uniform(restore_at, max(restore_at, spread_max))
            ctx.state.data["prayer_drink_threshold"] = threshold

        should_drink = current_prayer <= threshold
        if should_drink and controller.consume_first(ctx.ser, "prayer"):
            debug_log.log("prayer", f"drank prayer restore (prayer={current_prayer}, threshold={threshold:.0f})")
            ctx.state.data["prayer_drink_threshold"] = None


def maintain_offensive_prayer(ctx, combat: dict):
    """Flicks a single offensive (stat-boosting) prayer on right before the player's
    own attack lands and off once it registers -- LAZY_FLICK-style behavior (see
    docs/superpowers/specs/2026-07-29-split-defensive-offensive-prayer-design.md),
    scoped down from the full damage-prediction system a reference community fork
    (ZombieLite) uses for the same idea, since this project has no equivalent and
    can't reliably hit tick-perfect timing through external input anyway. Requires
    the Attack Timer Metronome plugin to be loaded -- if combat.ticksUntilNextAttack
    is ever absent, this is a permanent no-op (never guesses a fallback timing)."""
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    if not prayer_cfg.get("enabled"):
        return

    raw_offensive_prayer = prayer_cfg.get("offensivePrayer", "NONE")
    player = controller.get_player()
    offensive_prayer = resolve_combat_prayer(raw_offensive_prayer, player.get("prayerReal"))
    if not offensive_prayer:
        return

    ticks_until_attack = combat.get("ticksUntilNextAttack")
    if ticks_until_attack is None:
        return

    prayers = controller.get_prayers()
    is_on = prayers.get("active", {}).get(offensive_prayer, False)
    hp_xp = player.get("hitpointsXp")
    flicked_on_tick = ctx.state.data.get("offensive_prayer_on_since_tick")

    if not is_on:
        # Triggers a couple of ticks out rather than right on the last one -- live timing
        # measurements (open-tab + click round trip through the bridge) showed 1-2 game
        # ticks (525-1121ms) between the decision and the click actually landing, so firing
        # at ticks_until_attack == 1 routinely landed the boost a tick after the attack it
        # was meant to cover. 2 ticks of lead covers the observed worst case with a little
        # margin to spare.
        if ticks_until_attack <= 2 and combat.get("inCombat"):
            controller.open_prayer_tab(ctx.ser)
            icon = controller.get_prayers().get("icons", {}).get(offensive_prayer)
            if icon:
                debug_log.log("prayer", f"flicking on {offensive_prayer}")
                controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
                ctx.state.data["offensive_prayer_on_since_tick"] = combat.get("tick")
                ctx.state.data["offensive_prayer_hp_xp_at_flick"] = hp_xp
        return

    hp_xp_at_flick = ctx.state.data.get("offensive_prayer_hp_xp_at_flick")
    hit_landed = hp_xp is not None and hp_xp_at_flick is not None and hp_xp > hp_xp_at_flick
    tick_passed = flicked_on_tick is not None and combat.get("tick") is not None and combat.get("tick") > flicked_on_tick
    # is_on but we never tracked flicking it on ourselves (e.g. a script restart mid-flick) --
    # we have no idea how long it's actually been on, so treat it the same as "time to turn
    # it off" rather than leaving it lit with nothing ever able to notice.
    untracked = flicked_on_tick is None

    if hit_landed or tick_passed or untracked:
        controller.open_prayer_tab(ctx.ser)
        icon = controller.get_prayers().get("icons", {}).get(offensive_prayer)
        cleared = False
        if icon:
            reason = "hit landed" if hit_landed else ("untracked" if untracked else "attack tick passed")
            debug_log.log("prayer", f"flicking off {offensive_prayer} ({reason})")
            controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
            cleared = True
        elif tick_passed and combat.get("tick") - flicked_on_tick >= OFFENSIVE_PRAYER_STUCK_ON_TICKS:
            # The icon lookup has failed for several ticks running -- clear our own tracking
            # anyway rather than staying permanently unable to ever detect "off" again. The
            # prayer may stay lit in-game a tick or two longer than intended, but that beats
            # draining prayer points forever with nothing able to notice.
            debug_log.log("prayer", f"giving up on flicking off {offensive_prayer} -- icon not found for "
                                     f"{OFFENSIVE_PRAYER_STUCK_ON_TICKS}+ ticks, clearing tracked state anyway")
            cleared = True

        if cleared:
            ctx.state.data["offensive_prayer_on_since_tick"] = None
            ctx.state.data["offensive_prayer_hp_xp_at_flick"] = None


def maintain_supplies(ctx):
    config = ctx.state.config
    food_cfg = config.get("food", {})
    potions_cfg = config.get("potions", {})

    player = controller.get_player()
    hp, hp_max = player.get("hp"), player.get("hpReal")

    if food_cfg.get("enabled") and hp is not None:
        should_eat = hp <= food_cfg.get("eatAt", 0)
        mistake = False
        if not should_eat and hp_max and hp >= hp_max * settings.MISTAKE_OVEREAT_MIN_HP_FRACTION \
                and random.random() < settings.MISTAKE_OVEREAT_CHANCE:
            should_eat, mistake = True, True  # eats a bite we didn't actually need, e.g. right up near full hp

        if should_eat and controller.consume_first(ctx.ser, "food"):
            debug_log.log("supplies", f"{'(mistake) ' if mistake else ''}ate food (hp={hp})")
            return  # avoid eating and drinking in the same tick

    if potions_cfg.get("combatEnabled"):
        # The bridge doesn't expose buff/potion timers yet (no varbit/timer
        # endpoint), so we can't tell when a combat potion has expired.
        # Left as a no-op until a BuffProvider-style endpoint exists.
        pass


# ---------------- slayer bracelets ----------------
# Bracelet of slaughter / expeditious bracelet are worn in the GLOVES slot and crumble when their
# charges run out, leaving that slot empty. maintain_bracelet puts a spare from the inventory back
# on. USER request 2026-09-18. Triggered by the crumble chat message (an accelerator) and by a
# slow periodic /equipment read (the source of truth, so a dropped UDP packet only costs time).
# RECORDED live 2026-09-18 (expeditious): the charge that breaks it says "Your expeditious
# bracelet helps you progress your slayer task faster. It then crumbles to dust." (the one before
# it: "... It has 1 charge left."). The match stays loose (bracelet + crumble) to cover slaughter's
# own wording unrecorded. There is NO charge varbit/varp: across both kills only varp 394 (task
# count, -2 per kill with expeditious) and per-kill counters moved -- Item Charges plugins read
# the chat, so chat is the only early signal there is.
SLAYER_BRACELET_NAMES = ("bracelet of slaughter", "expeditious bracelet")
BRACELET_CHECK_INTERVAL_S = 30.0    # periodic fallback when no chat message arrived
BRACELET_RETRY_S = 3.0              # re-check this soon after a Wear click, to confirm it took


def is_bracelet_broken_message(message) -> bool:
    text = (message or "").lower()
    return "bracelet" in text and "crumble" in text


def _is_slayer_bracelet(name) -> bool:
    name = (name or "").lower()
    return any(b in name for b in SLAYER_BRACELET_NAMES)


def maintain_bracelet(ctx):
    """Re-wear a slayer bracelet once the worn one has crumbled. Only acts on an EMPTY gloves
    slot, so real gloves the player chose to wear are never swapped off."""
    if not ctx.state.config.get("bracelets", {}).get("reequip", True):
        return
    data = ctx.state.data
    now = time.time()
    if now < data.get("bracelet_check_at", 0.0):
        return
    data["bracelet_check_at"] = now + BRACELET_CHECK_INTERVAL_S

    gloves = controller.get_equipment().get("gloves") or {}
    if gloves.get("id") is not None:
        if _is_slayer_bracelet(gloves.get("name")):
            data["bracelet_worn_name"] = gloves["name"].lower()
        return

    spares = [i for i in controller.get_inventory() if _is_slayer_bracelet(i.get("name"))]
    if not spares:
        return
    # Same kind as the one that just broke first, so a mixed inventory keeps doing what the
    # player set it up to do.
    last = data.get("bracelet_worn_name") or ""
    spares.sort(key=lambda i: (i.get("name") or "").lower() != last)
    spare = spares[0]

    debug_log.log("bracelet", f"gloves slot empty -- wearing {spare.get('name')}")
    controller.open_inventory_tab(ctx.ser)
    fresh = next((i for i in controller.get_inventory()
                  if i.get("slot") == spare.get("slot") and _is_slayer_bracelet(i.get("name"))), spare)
    controller.click_entity(ctx.ser, fresh, check_ui_blockers=False, simulate_mistakes=False,
                            expected_action="Wear")
    data["bracelet_check_at"] = now + BRACELET_RETRY_S


def maybe_loot(ctx):
    config = ctx.state.config
    loot_cfg = config.get("loot", {})

    whitelist = [w.lower() for w in loot_cfg.get("items", [])]
    min_value = loot_cfg.get("minValue", 0)

    ground_items = controller.get_ground_items()
    inventory = controller.get_inventory()
    herb_sack_open = any("open herb sack" in (i.get("name") or "").lower() for i in inventory)
    seed_box_open = any("open seed box" in (i.get("name") or "").lower() for i in inventory)

    ground_keys = {(i.get("id"), i.get("x"), i.get("y")) for i in ground_items}

    # loot_pending_key is the item our most recent Take click targeted, still presumed
    # in-flight (walking there, or the pickup hasn't registered yet) until it either
    # disappears from the ground (resolved -- success) or LOOT_PENDING_TIMEOUT_TICKS pass
    # with it still sitting there (resolved -- this attempt failed). CONFIRMED live 2026-08-06:
    # position-change was tried as a proxy for "still walking" before this, but it's an
    # inference, not ground truth -- it can both misfire (this python loop's polling isn't
    # tick-locked to the game, so two checks can land within the same game tick and see
    # identical position even though a real walk is in progress, letting a second click through
    # too early and cancelling the first) and doesn't generalize cleanly across items (it was
    # keyed to loot_stuck_key, so switching to a second item on the same square skipped the
    # check entirely). Whether the specific item is still on the ground is the actual thing we
    # care about and is unambiguous, so wait on that directly instead of inferring from motion.
    pending_key = ctx.state.data.get("loot_pending_key")
    still_pending = False
    if pending_key is not None:
        if pending_key not in ground_keys:
            # Gone -- picked up (or despawned/out of range either way). Resolved either way:
            # clear both the pending click and the stuck-attempt counter for it.
            ctx.state.data["loot_pending_key"] = None
            ctx.state.data["loot_pending_ticks"] = 0
            ctx.state.data["loot_stuck_key"] = None
            ctx.state.data["loot_stuck_attempts"] = 0

            # Re-engage the current combat target with a fresh click if the detour to grab this
            # actually broke off the fight -- CONFIRMED (user request 2026-08-06): walking a few
            # tiles to loot can leave current_target_index still committed server-side (see
            # engage_target's own 1v1-lock comment) without auto-attack actually resuming once
            # back in range, so the character can end up standing next to a still-alive,
            # still-committed target doing nothing until some other trigger re-clicks it. Gated
            # on combat having actually lapsed rather than firing unconditionally, so the common
            # case (item was adjacent, the fight never stopped) doesn't waste a click re-clicking
            # a target that's already mid-swing.
            current_target_index = ctx.state.data.get("current_target_index")
            if current_target_index is not None and not controller.get_combat().get("inCombat"):
                target_npc = next(
                    (n for n in controller.get_npcs() if n.get("index") == current_target_index),
                    None,
                )
                if target_npc is not None and target_npc.get("hpRatio") != 0:
                    debug_log.log("combat", f"re-engaging {target_npc.get('name')} "
                                             f"(index={current_target_index}) after loot pickup")
                    controller.human_delay(0.15, 0.6)
                    # Same uncached re-resolve as the fresh-engage path below -- target_npc came
                    # from the TickCache-gated get_npcs(), so its screen position is stale.
                    fresh_npc = controller.get_npc_by_index(current_target_index)
                    if fresh_npc is not None and fresh_npc.get("screenX") is not None:
                        target_npc = fresh_npc
                    if controller.click_entity(
                        ctx.ser,
                        target_npc,
                        refresh_target=controller.npc_index_refresh_target(current_target_index),
                        refresh_legs=ENGAGE_REFRESH_LEGS,
                        expected_action="Attack",
                        expected_target=target_npc.get("name"),
                    ):
                        ctx.state.data["engage_committed_at"] = time.time()
                        ctx.state.data["engage_last_pos"] = None
                        ctx.state.data["engage_stuck_ticks"] = 0
                        ctx.state.data["engage_ever_confirmed"] = False
        else:
            pending_ticks = ctx.state.data.get("loot_pending_ticks", 0) + 1
            if pending_ticks < LOOT_PENDING_TIMEOUT_TICKS:
                # Might still be walking there -- don't click anything loot-related (this item
                # or a different one) until this resolves one way or the other.
                ctx.state.data["loot_pending_ticks"] = pending_ticks
                still_pending = True
            else:
                # Timed out still sitting there -- this attempt failed (missed click, genuinely
                # unreachable, whatever). Free the pending slot so a retry -- or a different
                # item -- can go through; loot_stuck_attempts (bumped when this item was
                # originally clicked, below) still enforces the overall give-up.
                ctx.state.data["loot_pending_key"] = None
                ctx.state.data["loot_pending_ticks"] = 0

    # (id, x, y) identifies a specific dropped item on a specific tile -- if it's no longer
    # among what's currently on the ground (picked up, despawned, or just out of range now),
    # drop the stuck-attempt tracking for it rather than let a stale count carry over onto
    # an unrelated future item that happens to reuse the same key.
    stuck_key = ctx.state.data.get("loot_stuck_key")
    if stuck_key is not None and stuck_key not in ground_keys:
        ctx.state.data["loot_stuck_key"] = None
        ctx.state.data["loot_stuck_attempts"] = 0

    if not still_pending:
        for item in ground_items:
            if not item.get("clickable"):
                continue
            if item.get("ownership") == GROUND_ITEM_OWNERSHIP_OTHER:
                # Someone else's kill loot in this shared (non-instanced) cave -- visible in our
                # own client scene the same as our own drops, but not ours to take until their
                # private-loot timer expires. CONFIRMED live 2026-08-06 (user report): the bot
                # was attempting these, which either silently fail or (worse) risk annoying a
                # real nearby player. See GroundItemProvider.java's ownership comment for the
                # matching RuneLite "Takeable" filter this mirrors.
                continue

            name = (item.get("name") or "").lower()
            name_match = bool(whitelist) and any(w in name for w in whitelist)
            # totalValue is GE price * quantity, computed server-side (GroundItemProvider).
            value_match = min_value > 0 and item.get("totalValue", 0) >= min_value
            herb_bypass = herb_sack_open and name in HERB_SACK_BYPASS_NAMES
            seed_bypass = seed_box_open and name in SEED_BOX_BYPASS_NAMES

            if name_match or value_match or herb_bypass or seed_bypass:
                # MAKE ROOM (2026-09-21, spec §7.10): with a FULL inventory the caller may decide
                # whether this drop is worth giving something up for, and do it. ABSENT = today's
                # behaviour exactly -- the take is attempted regardless, as it always was (pinned
                # by test). Every threshold and every click lives in the caller; this stays dumb.
                make_room = loot_cfg.get("makeRoom")
                if callable(make_room) and inventory_full() and not make_room(item):
                    continue

                key = (item.get("id"), item.get("x"), item.get("y"))
                same_target = key == ctx.state.data.get("loot_stuck_key")

                if same_target and ctx.state.data.get("loot_stuck_attempts", 0) >= LOOT_STUCK_MAX_ATTEMPTS:
                    # Same tile, still here after repeated attempts -- treat as unreachable and
                    # move on instead of clicking it forever (see LOOT_STUCK_MAX_ATTEMPTS above).
                    continue

                if name_match:
                    reason = "whitelist"
                elif value_match:
                    reason = f"worth {item.get('totalValue')}gp"
                elif herb_bypass:
                    reason = "herb sack open"
                else:
                    reason = "seed box open"
                debug_log.log("loot", f"picking up {item.get('name')} ({reason})")
                controller.human_delay(0.2, 0.5)
                # Re-fetch this item's screen position AFTER the delay above, not before --
                # CONFIRMED (user insight 2026-08-06, the well-known OSRS "true tile" quirk):
                # the player's logical world tile (what GroundItemProvider's screen-position
                # math is ultimately anchored to) updates instantly on the tick boundary, but
                # the camera and player-model rendering trail behind it, smoothly animating to
                # catch up over roughly the following tick -- so a screenX/screenY computed even
                # a fraction of a second ago can already be stale relative to wherever the
                # camera has since animated to. Clicking with `item` (fetched at the top of this
                # function, before this loop and before the delay above) compounded that with
                # our own added staleness on top of the engine's. A ground item's small icon has
                # far less tolerance for that drift than an NPC's larger sprite, which is why
                # this matters here specifically and didn't for click_entity elsewhere. Fetching
                # fresh right before the click -- after the delay instead of before it -- turns
                # that same delay into a camera-settle window instead of working against it.
                # CORRECTED 2026-08-24: this re-fetched via controller.get_ground_items(), which
                # is TickCache-gated -- so "fresh" was only ever fresh to the current game tick,
                # up to 600ms old, which is precisely the staleness the comment above sets out to
                # avoid. get_ground_item_at hits /ground-item/at instead: an uncached
                # single-entity lookup, and exactly what get_ground_items' own docstring tells
                # callers to prefer once they know the tile and id (which `key` is).
                #
                # This mattered even more here than for an NPC click, back when click_menu_action
                # had no refresh_target parameter and right-clicked precisely the pixel it was
                # handed -- re-resolving immediately beforehand was the only correction available.
                # Fixed 2026-08-27 (see the refresh_target= passed below); re-resolving here still
                # matters as the position fed into that factory, it just isn't the only defense
                # anymore. Symptom when it was stale, seen live: "No 'take' entry in menu ...
                # ['Cancel', 'Mark Safespot', 'Walk here']" -- a right-click on bare ground.
                fresh_item = controller.get_ground_item_at(
                    key[1], key[2], item.get("plane", 0), key[0]
                )
                click_target = fresh_item if fresh_item is not None else item
                # Right-click + explicit "Take" menu selection, not a plain left click_entity --
                # a ground item can share its click point with an NPC (very common mid-fight, an
                # enemy dies right on top of its own drop, or a new one walks over it), and
                # left-click's default action there resolves to the NPC (Attack), not the item
                # (Take), with no misclick logged -- it just silently never gets picked up.
                # click_menu_action reads the menu back and picks the "Take" entry by name
                # instead of trusting whichever entity wins the default-action race.
                # CONFIRMED-working live 2026-08-06 (see controller.click_menu_action's own
                # docstring).
                #
                # offset=(0, 0) -- CONFIRMED live 2026-08-06: click_entity's usual default,
                # offset=(-7, 7), is sized for clicking somewhere-on-a-largeish-NPC-sprite, not
                # dead center. A ground item's icon is much smaller, so that same offset
                # regularly landed just off it and hit the bare tile instead -- opening the
                # *tile's* menu (Cancel/Mark Safespot/Walk here) rather than the item's
                # (Take/Examine/Cancel). 5 of 6 right-clicks on a Kwuarm seed missed this way
                # before one finally landed dead-on. Aiming exactly at the item's own
                # screenX/screenY (GroundItemProvider's precise anchor point) instead of
                # jittering off it fixes that.
                # refresh_target (2026-08-27): the re-resolve above is only accurate at the
                # instant it happens, and the right-click lands ~1.0s later -- during a fight the
                # player is moving and the camera trails, so a ground item's small icon drifts out
                # from under the aim point. MEASURED live: 3 misses in one session, each reading
                # back a menu of pure ground options and costing a ~7s retry. click_menu_action
                # can now re-aim mid-flight like any other click.
                controller.click_menu_action(
                    ctx.ser, click_target, "take",
                    target_hint=click_target.get("name"), offset=(0, 0),
                    refresh_target=controller.ground_item_refresh_target(
                        key[1], key[2], item.get("plane", 0), key[0]),
                    refresh_legs=LOOT_REFRESH_LEGS,
                )

                if same_target:
                    ctx.state.data["loot_stuck_attempts"] = ctx.state.data.get("loot_stuck_attempts", 0) + 1
                else:
                    ctx.state.data["loot_stuck_key"] = key
                    ctx.state.data["loot_stuck_attempts"] = 1
                ctx.state.data["loot_pending_key"] = key
                ctx.state.data["loot_pending_ticks"] = 0
                break

    if loot_cfg.get("buryBones"):
        for item in controller.get_inventory():
            name = (item.get("name") or "").lower()
            if "bones" in name:
                debug_log.log("loot", f"burying {item.get('name')}")
                # check_ui_blockers=False: inventory-item click, same reasoning as every
                # other inventory click in this codebase (see banker.py's coal bag notes).
                controller.click_entity(ctx.ser, item, check_ui_blockers=False)
                break


def engage_target(ctx, combat: dict):
    config = ctx.state.config
    npcs = controller.get_npcs()

    # Tracked by instance index (stable per-monster identifier), not the
    # shared type id -- in a pack of identical monsters, "any NPC of this
    # type is alive" can't tell your target apart from its neighbors.
    current_target_index = ctx.state.data.get("current_target_index")
    if current_target_index is not None:
        target_npc = next((n for n in npcs if n.get("index") == current_target_index), None)
        still_alive = target_npc is not None and target_npc.get("hpRatio") != 0
        if still_alive:
            # Committed until this one dies. Some areas enforce strict 1v1
            # combat and reject a second target with "you are already
            # attacking a different monster" -- combat.inCombat can read
            # False for a tick between attack animations even while the zone
            # still has us locked, so don't use it to decide whether to
            # re-engage; only "is our tracked target still alive" matters.
            #
            # ...unless the engaging click itself missed the NPC (see
            # ENGAGE_STUCK_MAX_TICKS above) -- give up the commitment once we've gone
            # several ticks straight with no combat AND no movement at all, since a
            # real engagement always starts walking toward the target or fighting it
            # within a couple ticks.
            #
            # Tracked once up front so both the per-tick check below and the
            # ENGAGE_NEVER_CONFIRMED_TIMEOUT_S backstop can use the same "did we see real combat
            # evidence" signal.
            connected = bool(combat.get("inCombat")) or bool(target_npc.get("interactingPlayer"))
            if connected:
                ctx.state.data["engage_ever_confirmed"] = True

            # Only checked within ENGAGE_STUCK_WINDOW_S of committing to this target --
            # CONFIRMED live 2026-08-06 (user report: had to manually re-click the target after
            # eating/drinking mid-fight): combat.inCombat's own flakiness during a non-combat
            # action (see maintain_defensive_prayer's still_fighting fix for the same root cause)
            # combined with a stationary safespot fighter never satisfying the "OR moved" half of
            # this check either, meaning a routine mid-fight sip could accumulate stuck_ticks and
            # falsely conclude the engagement failed, deep into an already-successful fight. This
            # detection exists only to catch a missed *initial* click, which always shows up
            # within a few ticks of committing -- past the window, trust the fight and stop
            # checking, exactly like the code did before ENGAGE_STUCK_MAX_TICKS existed.
            committed_at = ctx.state.data.get("engage_committed_at", 0)
            elapsed = time.time() - committed_at
            if elapsed >= ENGAGE_STUCK_WINDOW_S:
                # ENGAGE_NEVER_CONFIRMED_TIMEOUT_S backstop -- CONFIRMED live 2026-08-06: in a
                # packed room, position alone (the "OR moved" half of the per-tick check below)
                # can keep resetting engage_stuck_ticks from crowd jostling with zero actual
                # combat, so a fight that never once connected can still survive past this
                # window and get trusted forever by the old unconditional `return` here. Only
                # skip re-checking if we've EITHER seen real combat evidence at some point, OR
                # we're still within the more generous backstop deadline (a legitimate walk-up
                # to a distant target can take a while to land its first hit).
                # Doing anything at all -- fighting, walking, mid-swing -- keeps the
                # commitment; a dead stop for ENGAGE_IDLE_GIVE_UP_S releases it. See that
                # constant for the live hang this catches.
                busy = (connected or bool(combat.get("isMoving"))
                        or (combat.get("animation") not in (None, -1)))
                if busy:
                    ctx.state.data["engage_idle_since"] = 0
                    idle_for = 0.0
                else:
                    since = ctx.state.data.get("engage_idle_since") or time.time()
                    ctx.state.data["engage_idle_since"] = since
                    idle_for = time.time() - since
                if idle_for >= ENGAGE_IDLE_GIVE_UP_S:
                    debug_log.log("combat", f"giving up on target index={current_target_index} -- "
                                             f"committed {elapsed:.0f}s ago and in combat at some "
                                             f"point, but {idle_for:.0f}s since ANY sign of life "
                                             f"(no combat, no movement, no animation) -- the fight "
                                             f"ended without the monster dying")
                elif ctx.state.data.get("engage_ever_confirmed") or elapsed < ENGAGE_NEVER_CONFIRMED_TIMEOUT_S:
                    return
                else:
                    debug_log.log("combat", f"giving up on target index={current_target_index} -- "
                                         f"committed {elapsed:.0f}s ago and never once showed "
                                         f"real combat evidence (inCombat/interactingPlayer), "
                                         f"only whatever kept resetting the early stuck-tick "
                                         f"counter (e.g. crowd jostling) -- treating the "
                                         f"engaging click as missed")
            else:
                player = controller.get_player()
                pos = (player.get("x"), player.get("y"), player.get("plane"))
                # CONFIRMED live 2026-08-06 (user report: still had to reclick after eating/drinking
                # while safespotting, even after the freshness-window fix above) -- combat.inCombat
                # (Player.getInteracting() != null) apparently isn't the only place this flakiness
                # shows up; a safespot fighter never moves either, so that alone left no escape hatch
                # once inCombat misread. target_npc's own interactingPlayer (NPC.getInteracting() ==
                # local, a completely independent field read off the NPC rather than the player) is
                # ORed in here too -- both would have to misread on the same tick for this to still
                # false-positive, which is far less likely than either one alone.
                if connected or pos != ctx.state.data.get("engage_last_pos"):
                    ctx.state.data["engage_last_pos"] = pos
                    ctx.state.data["engage_stuck_ticks"] = 0
                    return

                stuck_ticks = ctx.state.data.get("engage_stuck_ticks", 0) + 1
                if stuck_ticks < ENGAGE_STUCK_MAX_TICKS:
                    ctx.state.data["engage_stuck_ticks"] = stuck_ticks
                    return

                debug_log.log("combat", f"giving up on target index={current_target_index} -- "
                                         f"not in combat and hasn't moved for {stuck_ticks} ticks "
                                         f"straight, engaging click probably missed")
        ctx.state.data["current_target_index"] = None
        ctx.state.data["engage_last_pos"] = None
        ctx.state.data["engage_stuck_ticks"] = 0
        ctx.state.data["engage_committed_at"] = 0
        ctx.state.data["engage_ever_confirmed"] = False
        ctx.state.data["engage_idle_since"] = 0

    if config.get("onlyAttackWhenIdle") and not combat.get("isIdle", True):
        return

    # Don't steal a fresh engaging click out from under an in-progress loot pickup --
    # CONFIRMED live 2026-08-06 (user report: still hasty reclicks making looting fail
    # sometimes, observed 2-3 tiles into a walk toward the loot with the new combat target even
    # farther away). maintain_safespot already defers to loot state for this same reason
    # (see its own comment), but engage_target never did. A player mid-walk to a lootable item
    # whose previous combat target just died or got abandoned would otherwise get immediately
    # redirected onto a fresh (possibly farther) target here, cancelling the loot walk before it
    # lands. loot_pending_key always resolves within LOOT_PENDING_TIMEOUT_TICKS one way or the
    # other (picked up or given up), so this can only ever delay a fresh engagement by that much
    # -- nothing about picking a *new* target is as time-critical as an already-committed fight,
    # which is why only this path (not the still-alive-and-committed logic above) needs the
    # guard.
    if ctx.state.data.get("loot_pending_key") is not None:
        return

    target = pick_target(npcs, config, controller.get_player())
    if not target:
        return

    debug_log.log("combat", f"attacking {target.get('name')} "
                             f"(id={target['id']}, index={target['index']}, dist={target.get('distance')})")
    controller.human_delay(0.15, 0.6)
    target_index = target["index"]
    # Re-resolve from the UNCACHED single-entity endpoint immediately before clicking.
    # pick_target chose from controller.get_npcs(), which is TickCache-gated, so its
    # screenX/screenY can be a full game tick (600ms) stale -- and these monsters move. Identified
    # 2026-08-24 while chasing a live "still a lot of misses" report: the cursor was landing
    # within 1px of the coordinate it was given, so the coordinate itself was the problem.
    # /npc/by-index is not cached (CLAUDE.md, "Sub-tick data and clicking while moving"), so this
    # costs one round trip and removes the staleness the click was inheriting.
    fresh = controller.get_npc_by_index(target_index)
    if fresh is not None and fresh.get("screenX") is not None:
        target = fresh
    if controller.click_entity(
        ctx.ser,
        target,
        refresh_target=controller.npc_index_refresh_target(target_index),
        refresh_legs=ENGAGE_REFRESH_LEGS,
        expected_action="Attack",
        expected_target=target.get("name"),
    ):
        ctx.state.data["current_target_index"] = target_index
        ctx.state.data["engage_committed_at"] = time.time()
        ctx.state.data["engage_ever_confirmed"] = False
