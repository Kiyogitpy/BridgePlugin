# routines/slayer.py
#
# Combat/slayer routine: pick a target from the Controller Settings panel's
# monster list, fight it, keep prayer/food/potions topped up, loot, bury
# bones. Config comes straight from the bridge's GET /config (i.e. whatever
# is set in the RuneLite "Controller" side panel) -- see
# com.example.controller.ControllerConfig / ConfigExporter for the shape.
#
# Run with:
#     python controller.py routines/slayer.py

import math
import random
import time

import banker
import controller
import debug_log
import settings
import travel


# GET /slayer-config already includes the master switch (SlayerConfigProvider reads it off
# ControllerConfig too) alongside every slayer-specific setting -- bridge_config (GET /config)
# isn't needed here.
def get_config(bridge_config):
    return controller.get_slayer_config()


def on_event(ctx, event_type, payload):
    if event_type == "NPC_DIED" and payload:
        # Matched by instance index, not type id -- a different monster of
        # the same species dying nearby must not clear our current target.
        if payload.get("index") == ctx.state.data.get("current_target_index"):
            ctx.state.data["current_target_index"] = None


def tick(ctx):
    config = ctx.state.config

    ammo = controller.get_ammo_count()
    if ammo is not None and ammo <= 0:
        ctx.state.trigger_safety_stop("out of ammo")
        return

    bank_cfg = config.get("bank", {})
    if bank_cfg.get("enabled") and needs_banking(ctx):
        debug_log.log("bank", "inventory full, banking")
        ctx.state.data["current_target_index"] = None  # don't try to resume a fight through a bank trip
        banker.bank_up(ctx, bank_cfg)
        return

    combat = controller.get_combat()
    maintain_defensive_prayer(ctx, combat)
    maintain_offensive_prayer(ctx, combat)
    maintain_supplies(ctx)

    if config.get("loot", {}).get("enabled"):
        maybe_loot(ctx)

    maintain_safespot(ctx)
    engage_target(ctx, combat)


def inventory_full() -> bool:
    return len(controller.get_inventory()) >= settings.INVENTORY_SIZE


def needs_banking(ctx) -> bool:
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


def pick_target(npcs: list, config: dict):
    ids, names = resolve_targets(config)
    if not ids and not names:
        return None

    max_dist = config.get("maxTargetDistance", 15)
    skip_in_combat = config.get("skipNpcInCombat", True)

    candidates = []
    for npc in npcs:
        if not npc.get("attackable"):
            continue
        npc_name = (npc.get("name") or "").lower()
        if npc.get("id") not in ids and not any(n in npc_name for n in names if n):
            continue

        dist = npc.get("distance", 9999)
        if dist < 0 or dist > max_dist:
            continue

        if skip_in_combat and npc.get("inCombat") and not npc.get("interactingPlayer"):
            continue

        candidates.append(npc)

    if not candidates:
        return None

    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


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

# maybe_loot gives up on a ground item after this many consecutive pickup attempts on the exact
# same tile without it disappearing -- CONFIRMED live 2026-08-05: a Grimy ranarr weed dropped in
# a Gryphon cave room got re-clicked 40+ ticks straight with the player's position/animation never
# changing at all (not even an attempted walk), never picked up. GroundItemProvider's "clickable"
# flag is screen-visibility only (onScreen && alive), with no pathfinding/reachability check behind
# it, so a stuck ground item (e.g. dropped on a tile that isn't actually reachable from here) looks
# identical to a normal one and gets re-targeted forever with no error to detect otherwise.
LOOT_STUCK_MAX_ATTEMPTS = 6

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
    travel.walk_to(ctx, tile, tolerance=SAFESPOT_RETURN_TOLERANCE)


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
        want_on = still_fighting
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

    potions_cfg = config.get("potions", {})
    if potions_cfg.get("prayerEnabled"):
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


def maybe_loot(ctx):
    config = ctx.state.config
    loot_cfg = config.get("loot", {})

    whitelist = [w.lower() for w in loot_cfg.get("items", [])]
    min_value = loot_cfg.get("minValue", 0)

    ground_items = controller.get_ground_items()
    inventory = controller.get_inventory()
    herb_sack_open = any("open herb sack" in (i.get("name") or "").lower() for i in inventory)
    seed_box_open = any("open seed box" in (i.get("name") or "").lower() for i in inventory)

    # (id, x, y) identifies a specific dropped item on a specific tile -- if it's no longer
    # among what's currently on the ground (picked up, despawned, or just out of range now),
    # drop the stuck-attempt tracking for it rather than let a stale count carry over onto
    # an unrelated future item that happens to reuse the same key.
    stuck_key = ctx.state.data.get("loot_stuck_key")
    if stuck_key is not None and stuck_key not in {(i.get("id"), i.get("x"), i.get("y")) for i in ground_items}:
        ctx.state.data["loot_stuck_key"] = None
        ctx.state.data["loot_stuck_attempts"] = 0

    for item in ground_items:
        if not item.get("clickable"):
            continue

        name = (item.get("name") or "").lower()
        name_match = bool(whitelist) and any(w in name for w in whitelist)
        # totalValue is GE price * quantity, computed server-side (GroundItemProvider).
        value_match = min_value > 0 and item.get("totalValue", 0) >= min_value
        herb_bypass = herb_sack_open and name in HERB_SACK_BYPASS_NAMES
        seed_bypass = seed_box_open and name in SEED_BOX_BYPASS_NAMES

        if name_match or value_match or herb_bypass or seed_bypass:
            key = (item.get("id"), item.get("x"), item.get("y"))
            if key == ctx.state.data.get("loot_stuck_key") \
                    and ctx.state.data.get("loot_stuck_attempts", 0) >= LOOT_STUCK_MAX_ATTEMPTS:
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
            time.sleep(random.uniform(0.2, 0.5))
            controller.click_entity(ctx.ser, item)

            if key == ctx.state.data.get("loot_stuck_key"):
                ctx.state.data["loot_stuck_attempts"] = ctx.state.data.get("loot_stuck_attempts", 0) + 1
            else:
                ctx.state.data["loot_stuck_key"] = key
                ctx.state.data["loot_stuck_attempts"] = 1
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
        still_alive = any(
            n.get("index") == current_target_index and n.get("hpRatio") != 0
            for n in npcs
        )
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
            if time.time() - committed_at >= ENGAGE_STUCK_WINDOW_S:
                return

            player = controller.get_player()
            pos = (player.get("x"), player.get("y"), player.get("plane"))
            if bool(combat.get("inCombat")) or pos != ctx.state.data.get("engage_last_pos"):
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

    if config.get("onlyAttackWhenIdle") and not combat.get("isIdle", True):
        return

    target = pick_target(npcs, config)
    if not target:
        return

    debug_log.log("combat", f"attacking {target.get('name')} "
                             f"(id={target['id']}, index={target['index']}, dist={target.get('distance')})")
    time.sleep(random.uniform(0.15, 0.6))
    target_index = target["index"]
    if controller.click_entity(
        ctx.ser,
        target,
        refresh_target=lambda: controller.get_npc_screen_pos_by_index(target_index),
    ):
        ctx.state.data["current_target_index"] = target_index
        ctx.state.data["engage_committed_at"] = time.time()
