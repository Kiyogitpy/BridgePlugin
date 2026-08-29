# routines/mahogany_homes.py
#
# Runs Adept-tier Mahogany Homes contracts unattended: request a contract via the NPC Contact
# spell, travel to the assigned homeowner's house via teleport tablets, remove/build/repair
# every hotspot, turn the contract in, repeat. See
# docs/superpowers/specs/2026-08-27-mahogany-homes-design.md for the full design and the
# recording/decompile evidence behind every constant below.
#
# Every WorldArea/object-id/tier fact this routine depends on lives server-side in
# MahoganyHomesProvider.java (ported from the decompiled reference plugin) and is read fresh
# every tick via GET /mahogany-homes -- this file holds no duplicate copy of that data.
#
# NOT YET LIVE-TESTED as of this file's creation. HOTSPOT_CLICK_COOLDOWN_S/TURN_IN_CLICK_
# COOLDOWN_S below are first-draft guesses (no recording measured Mahogany Homes' own action
# duration the way golem_crafting.py's GOLEM_SIDE_TIMEOUT_S was measured at 11.8s/side) --
# expect to retune them after the first live run, the same way that constant was corrected
# from an initial guess once real timing was observed.

import time

import banker
import controller
import debug_log
import travel
import worldmap

# InterfaceID.MagicSpellbook.NPC_CONTACT (packed 14286961 == 218<<16 | 113) -- confirmed both
# against runelite-api-1.12.33.jar (javap -v) and against all four recordings, where every
# "Last-tier contract" click lands on this exact widget. See the design doc's "Contract
# request" section for the full derivation.
NPC_CONTACT_GROUP = 218
NPC_CONTACT_CHILD = 113

# F6 opens the Magic tab -- MEASURED in the recordings (0.6s before every NPC Contact click,
# e.g. recordings/2026-08-27_174045_mahogny-homes.log:2181-2186).
MAGIC_TAB_KEY = "F6"

CONTACT_RETRY_COOLDOWN_S = 6.0     # spell cast -> assignment chat measured at ~4.4s in the
                                    # recordings; this just stops re-casting while waiting.
# MEASURED 2026-08-29 against the four hand-played recordings (427 consecutive hotspot actions,
# varbits 10554-10561, bulk contract-assignment writes excluded): the human runs a median 2.39s
# between actions with p25 0.60s and p75 3.00s. Ours was a flat 2.5s cooldown producing a 3.80s
# median at TAU -- about 1.6x slower.
#
# The spread is not noise, it is two different actions (user, 2026-08-29): "the remove action is
# basically instant so we can just reclick after that, but after building there is a slight
# animation delay after exp drop where we can't click". p25 of 0.60s is exactly ONE game tick --
# the Remove -> Build chain -- and p75 carries the build's animation tail. A single flat value
# cannot express that, so it pays the build's price for every remove.
HOTSPOT_CLICK_COOLDOWN_S = 0.6      # after a REMOVE only -- one tick, the human's p25
HOTSPOT_BUILD_COOLDOWN_S = 2.4      # after a Build or Repair -- animation tail past the XP drop
# ...plus the WALK, because the game queues an interaction and performs it ON ARRIVAL. Both
# constants above are the ACTION's own duration and nothing else, so a click on a hotspot a few
# tiles away expired long before the action could even begin. USER 2026-08-29: "are we checking
# if we click remove on a furniture but we are a few tiles away do we count that as removed
# before we arrive?" -- we were. Same omission as the door-open budget, and the same fix.
HOTSPOT_WALK_ALLOWANCE_S = 0.7      # per tile to the hotspot; 0.6s is one walked tile per tick
HOTSPOT_COOLDOWN_MAX_S = 8.0        # ceiling, so a genuinely unreachable hotspot still gives up
TURN_IN_CLICK_COOLDOWN_S = 2.0      # first-draft guess, see module docstring
LADDER_CLICK_COOLDOWN_S = 4.0       # a floor change takes a game tick plus the climb animation;
                                    # without this the next tick would immediately re-click.
# tiles -- skip teleporting if already this close. RAISED 40 -> 150 on 2026-08-28: this is
# meant to mean "are we in a different city", and 40 could not, because the Falador tablet lands
# 74 tiles from Larry's own standing tile. Standing in Falador having just arrived, the gate
# still said "too far" and only the mh_teleported_this_contract latch stopped a second tablet --
# so a single wrong latch (which happened, see TELEPORT_CONFIRM_TIMEOUT_S) meant teleporting
# Falador -> Falador once a tick. The four destinations in CITY_TELEPORTS are 248+ tiles apart
# at their closest (Falador 2965,3379 to Varrock 3213,3424), and the worst intra-city landing is
# ~75, so 150 separates them with room on both sides.
TRAVEL_TELEPORT_DISTANCE = 150
# Arrival tolerance for a walk that must end up INSIDE a building. travel.walk_to defaults to
# WALK_ARRIVAL_TOLERANCE (3), and LIVE 2026-08-28 that returned True from (1766,3624) -- pressed
# against Mariah's shut front door, three tiles from the standing tile, underRoof still False. A
# walk that "arrives" outside the door never stalls, so travel.py's door recovery never runs and
# the next tick goes back to clicking a hotspot it cannot reach. 1 forces the walk to actually
# press into the doorway, which is what triggers the door to be opened.
HOUSE_ARRIVAL_TOLERANCE = 1

# MEASURED live 2026-08-28 from recordings/2026-08-28_173427_mh-live-run5: a tablet teleport
# takes 3.8s from the click to the region change (varbit 12393 -> anim 4069 -> anim 4071 ->
# spotanim 678 -> LOADING -> LOGGED_IN). The confirmation used to be a single human_delay(2.5,
# 3.2) followed by one position read, which landed ~0.4s BEFORE the player arrived -- so a
# teleport that worked perfectly read as a failure and the retry broke another tablet. Polled,
# not slept: a teleport that lands early costs no extra wait.
TELEPORT_CONFIRM_TIMEOUT_S = 10.0
TELEPORT_CONFIRM_POLL_S = 0.3
# ... and a hard cap on top, because a confirmation that is wrong for ANY reason (out of
# charges, a dialogue eating the click) otherwise spends one tablet per tick forever. The
# Falador tablet lands 74 tiles from Larry's standing tile -- past TRAVEL_TELEPORT_DISTANCE --
# so the latch below is the ONLY thing preventing a second teleport, and a cap is the only
# thing preventing 155 of them. Live run 5 burned three before it was stopped by hand.
TELEPORT_MAX_ATTEMPTS = 2

# ... and the same ceiling on NPC Contact casts that register no contract. See
# request_contract_phase; 5 leaves room for a genuinely dropped click without becoming a loop.
CONTACT_MAX_CASTS = 5

# The homeowner's cup of tea. Rate-limited and capped for the same reason as everything else that
# repeats: without both, an option that stays on screen is clicked once per tick forever.
TEA_CLICK_COOLDOWN_S = 1.5
TEA_MAX_CLICKS = 3

# The multi-choice dialogue OPTION list. Confirmed from recorder output ("widget 219.1 slot 1").
# The tea match MUST be scoped to it -- see _accept_tea_if_offered for the deadlock that scoping
# it by text alone caused.
DIALOG_OPTION_GROUP = 219

# Talking to the homeowner repeats, so it needs a CEILING and not only a cooldown -- a cooldown
# only sets the RATE of an unbounded loop. Generous: a real turn-in costs a click or two plus a
# retry when the first right-click misses.
TURN_IN_MAX_CLICKS = 12

TELEPORT_MIN_DISPLACEMENT = 30      # tiles a teleport must move us before we believe it
                                    # happened. Comfortably above any walk a single tick could
                                    # produce, and far below the hundreds of tiles every teleport
                                    # in CITY_TELEPORTS actually covers.
# Bounds every bulk /objects scan this routine makes. A whole-scene walk costs ~124ms of
# client-thread time in a Falador house (9266 objects, MEASURED 2026-08-29) and buys nothing:
# every hotspot, ladder and door we care about is inside the contract house. Comfortably wider
# than HOME_ARRIVAL_TILES so a hotspot stays visible from the approach.
OBJECT_SCAN_RADIUS = 16

HOME_ARRIVAL_TILES = 12             # every home's WorldArea is ~10x10 with standingLocation
                                    # roughly central, so this covers the whole house plus a
                                    # margin: "close enough that a missing hotspot means the
                                    # wrong FLOOR, not the wrong street".

# How many CONSECUTIVE ticks a phase may spend making no progress before it safety-stops instead
# of retrying silently forever. Deliberately not small: a transient miss (a slow scene load, the
# camera still turning, an NPC standing on the aim pixel) is normal and must not false-trigger,
# and at the engine's poll cadence 15 ticks is tens of seconds, not an instant trip. Every one of
# these counters resets to 0 on any real progress, so it only fires on a genuine hang.
STUCK_TICK_LIMIT = 15

# How many clicks one hotspot may absorb WITHOUT its varbit value moving before we conclude we
# cannot actually reach it from where we are standing, and climb down to try the other staircase.
#
# This exists for NOELLA, the only home of the twelve with two staircases (four ladder ids) and,
# per the user 2026-08-28, an upper floor split into two DISCONNECTED halves: "you have to fix one
# upstairs climb down take the other ladder fix that upstairs". /objects reports the whole scene
# on the player's current plane, so a hotspot in the far half is plainly visible from the near one
# -- work_phase finds it, clicks it, and nothing ever says stop, because seeing the object is
# exactly what resets mh_work_stuck.
#
# A successful action always moves the hotspot's varbit, so a value that does not move is a click
# achieving nothing. FIRST-DRAFT VALUE -- like HOTSPOT_CLICK_COOLDOWN_S it wants a live session to
# confirm; too low risks climbing away from a hotspot that was merely slow to register.
HOTSPOT_UNREACHABLE_CLICKS = 5

# --- plank sack ---------------------------------------------------------------------------
#
# PORTED 2026-08-28 from MahoganyHomesPlugin.countPlankSackVarb, which the original port missed
# alongside npcUpstairs. The sack (item 24882) holds up to 28 planks and keeps a SEPARATE varbit
# per plank type, so a routine must read the one matching the plank it actually carries.
#
# This is why the sack is safe here when golem_crafting's fur pouch was not: the count is a real
# varbit read fresh from the client, not a local running estimate that can silently drift from
# reality. The module docstring's original "no plank sack, to avoid the fur-pouch failure mode"
# reasoning simply does not apply to a container the game will tell you the contents of.
PLANK_SACK_VARBITS = {
    "plank": 18171,
    "oak plank": 18172,
    "teak plank": 18173,
    "mahogany plank": 18174,
}
PLANK_SACK_CAPACITY = 28      # MEASURED: the recording's second Fill stops at varbit 18173 == 28
PLANK_SACK_MAX_FILLS = 4      # withdraw/fill rounds before giving up. 28 planks needs at most two
                              # from empty; the extra headroom is for a part-full sack, and the
                              # bound is what stops an unbounded loop against a bank out of stock.


def plank_sack_count(plank_name: str, inventory: list | None = None) -> int:
    """How many planks of `plank_name` are in the plank sack right now, or 0.

    Gated on actually CARRYING the sack: the varbit keeps its last value after the sack is
    banked, so reading it unconditionally would credit us planks we do not have -- which is
    precisely the stale-estimate trap the sack was originally left out to avoid."""
    varbit = PLANK_SACK_VARBITS.get((plank_name or "").lower())
    if varbit is None:
        return 0
    inv = inventory if inventory is not None else controller.get_inventory()
    if not any(banker.PLANK_SACK_NAME in (i.get("name") or "").lower() for i in inv):
        return 0
    return controller.get_varbit(varbit) or 0


def planks_available(inventory: list, plank_name: str) -> int:
    """Loose planks plus sacked ones -- what the reference plugin's own supply check counts."""
    return count_item(inventory, plank_name) + plank_sack_count(plank_name, inventory)


def _withdraw_all(ctx, name: str) -> None:
    """Withdraw as much of `name` as the inventory will hold, in ONE shift-click.

    CORRECTED after the 2026-08-28 live run: this first went through withdraw_loadout with
    quantity 28, and that helper sets quantity mode to "one" and clicks once PER UNIT -- 28
    clicks at a bank booth for a withdrawal that only had room for ten, most of them logged as
    "Skipping click on Teak plank -- aim point is outside the RuneLite window". banker's own
    withdraw_all_shift is built for exactly this case ("as much of this single item as fits"),
    is a single shift-click, and POLLS for the item actually arriving instead of sleeping --
    which also closes the documented race where closing the bank too early cancels the
    withdrawal outright."""
    banker.withdraw_all_shift(ctx, name, fresh=True)


def _load_plank_sack(ctx, plank_name: str) -> None:
    """Fill the plank sack, then leave the inventory full of loose planks too.

    The loop the user described and the recording confirms (recordings/2026-08-27_174045_
    mahogny-homes.log:812-870): withdraw-all -> Fill -> withdraw-all -> Fill -> withdraw-all,
    with varbit 18173 stepping 9 -> 26 -> 28. Bank must already be OPEN: Fill is only offered on
    an inventory item while a bank interface is up (see banker.fill_plank_sack)."""
    if not banker.has_plank_sack():
        return

    last = None
    for _ in range(PLANK_SACK_MAX_FILLS):
        count = plank_sack_count(plank_name)
        if count >= PLANK_SACK_CAPACITY:
            break
        if last is not None and count <= last:
            # The sack took nothing last round -- the bank is out of planks. Without this the
            # loop would keep withdrawing nothing and filling nothing until MAX_FILLS.
            debug_log.log("mahogany_homes",
                          f"plank sack stuck at {count}/{PLANK_SACK_CAPACITY} -- bank is out of "
                          f"{plank_name}")
            break
        last = count
        _withdraw_all(ctx, plank_name)
        banker.fill_plank_sack(ctx)

    # Top the inventory back up so we carry a full load of loose planks ON TOP of the full sack.
    _withdraw_all(ctx, plank_name)
    debug_log.log("mahogany_homes",
                  f"plank sack at {plank_sack_count(plank_name)}/{PLANK_SACK_CAPACITY}")

# The homes whose upper floor is split into DISCONNECTED halves, so that reaching one hotspot can
# require climbing back down and taking a different staircase. Deliberately an explicit list and
# NOT inferred from `len(ladderObjectIds) > 2`: two staircases do not imply a disconnected floor,
# and applying the unreachable guard to the other eleven homes would risk climbing away from a
# hotspot that was merely slow -- a new failure mode in eleven houses to fix a quirk in one.
# NOELLA is the only home reported (user, 2026-08-28) to have this layout.
SPLIT_UPPER_FLOOR_HOMES = {"NOELLA"}

# Which teleport item gets a player near each home's city, as (item name substring, menu option).
# A menu option of None means the item's own left-click default already teleports -- true for
# every "<City> teleport" tablet, whose default action is "Break". Xeric's talisman is the
# exception: its default is NOT the destination we want, so it has to go through the right-click
# menu and pick "Xeric's Glade" by name (confirmed in the recording's own action label,
# "Xeric's Glade -> widget 149.0 [Xeric's talisman]", against "Break -> widget 149.0 [Varrock
# teleport]" for the tablets). Xeric's Glade is the closest teleport to Barbara/Leela/Mariah's
# homes (see Mahogany Homes.txt's "Recommended setups") -- same city grouping as the reference
# plugin's Home enum.
CITY_TELEPORTS = {
    "JESS": ("ardougne teleport", None),
    "NOELLA": ("ardougne teleport", None),
    "ROSS": ("ardougne teleport", None),
    "LARRY": ("falador teleport", None),
    "NORMAN": ("falador teleport", None),
    "TAU": ("falador teleport", None),
    "BARBARA": ("xeric's talisman", "Xeric's Glade"),
    "LEELA": ("xeric's talisman", "Xeric's Glade"),
    "MARIAH": ("xeric's talisman", "Xeric's Glade"),
    "BOB": ("varrock teleport", None),
    "JEFF": ("varrock teleport", None),
    "SARAH": ("varrock teleport", None),
}

# Everything that must survive bank_phase's deposit-first step. Matched as a case-insensitive
# SUBSTRING by banker.deposit_inventory (so "plank" covers Teak/Mahogany/Oak plank alike). The
# configured plankName and every runeTargets key are appended on top of this at call time.
KEEP_ITEMS = [
    "hammer", "saw", "rune pouch", "xeric's talisman",
    "varrock teleport", "ardougne teleport", "falador teleport",
    "plank", "steel bar",
]

# PLANK SACK: supported as of 2026-08-28 (user report: "we never fill the plank sack"). The
# first draft left it out to dodge golem_crafting's stale fur-pouch-estimate failure -- that
# reasoning was wrong here, and the reference plugin had the feature all along
# (MahoganyHomesPlugin.countPlankSackVarb, missed by the original port alongside npcUpstairs).
# The sack's contents are a real VARBIT read fresh from the client, never a local estimate, so
# the drift that broke the fur pouch cannot happen. It adds 28 planks of capacity on top of the
# inventory, and the game spends them straight from the sack -- no Empty step. See
# PLANK_SACK_VARBITS and _load_plank_sack.
#
# It costs ONE inventory slot, which the budget below already had spare.
#
# SLOT BUDGET (28 inventory slots). The original comment here assumed 3 slots for the 3 tablet
# types; teleport TABLETS DO NOT STACK, so 8 of each was 24 slots on its own and the loadout
# could never fit. Corrected 2026-08-27 by the final whole-branch review. The arithmetic, so the
# next person can check it rather than re-derive it:
#
#   hammer                 1
#   saw                    1
#   rune pouch             1
#   Xeric's talisman       1
#   astral runes           1   (runes stack -- one slot no matter how many)
#   cosmic runes           1
#   varrock teleport   x3  3   (tablets DO NOT stack -- one slot each)
#   ardougne teleport  x3  3
#   falador teleport   x3  3
#   steel bar          x2  2   (bars do not stack; no Adept home needs more than ONE, so 2 is
#                               already a spare -- see MahoganyHomesProvider's Home table)
#                        ----
#                         17
#   teak plank        x10 10   -> 27 used, 1 slot deliberately left free for anything picked
#                        ----   up mid-contract (the tea, a stray drop) so a full inventory can
#                         27    never wedge an interaction.
#
# 10 planks no longer covers an Adept contract's worst case (~16-17) in one trip, and that is
# fine BY DESIGN as of this fix: work_phase now checks the CURRENT hotspot's own cost rather
# than the whole remaining contract's, so it works every hotspot it can afford and only banks
# when the next single one is unaffordable. Partial progress instead of an all-or-nothing trip.
DEFAULT_CONFIG = {
    "enabled": True,
    "tier": "adept",
    "plankName": "Teak plank",
    "restock": {
        # minPlanks is the BETWEEN-contract gate only (needs_bank_trip). Mid-contract shortfalls
        # are caught per-hotspot by work_phase instead, so this only needs to be above the
        # largest single hotspot cost (B4 = 4 planks).
        "minPlanks": 5,
        "targetPlanks": 10,
        "minSteelBars": 1,
        "targetSteelBars": 2,
        # 1, not 2: only 3 of each tablet are carried, and a contract uses at most one, so a
        # threshold of 2 would force a bank trip after a single teleport. 1 means "restock once
        # you are down to your last tablet", which still cannot strand us -- needs_bank_trip is
        # evaluated between every contract, before the tablet is needed.
        "minTabletCount": 1,
        "tabletTargets": {
            "varrock teleport": 3,
            "ardougne teleport": 3,
            "falador teleport": 3,
        },
        "minRuneCount": 30,
        "runeTargets": {
            "astral rune": 300,
            "cosmic rune": 300,
        },
    },
    "homeTeleport": "varrock teleport",
    "bank": {
        "bankNames": ["Bank booth", "Bank chest", "Bank counter", "Grand Exchange booth"],
        "maxBankDistance": 40,
    },
    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_mahogany_homes_config()
    if config is None:
        return DEFAULT_CONFIG

    # Only "enabled"/"breaks"/"safety" come from the bridge (ConfigExporter.exportMahoganyHomes)
    # -- everything else (restock thresholds, plank name, teleport mapping) is routine-specific
    # tuning that isn't worth a RuneLite GUI control, same reasoning golem_crafting.py's
    # "locations" re-injection comment gives for its own config.
    merged = dict(DEFAULT_CONFIG)
    merged["enabled"] = config.get("enabled", DEFAULT_CONFIG["enabled"])
    merged["breaks"] = config.get("breaks", DEFAULT_CONFIG["breaks"])
    merged["safety"] = config.get("safety", DEFAULT_CONFIG["safety"])
    return merged


def setup(ctx):
    debug_log.log("mahogany_homes", "reminder: RuneLite's menu-entry swapper must have Remove/"
                                     "Build/Repair set as the left-click default on Mahogany "
                                     "Homes hotspot objects, and NPC Contact must already be "
                                     "configured to request a last-tier (Adept) contract in one "
                                     "click -- this routine does not set up either.")


def tier_number(name: str) -> int:
    return {"beginner": 1, "novice": 2, "adept": 3, "expert": 4}.get((name or "").lower(), 0)


def pick_next_hotspot(hotspots: list, reach: set | None = None, plane: int | None = None):
    """The next hotspot to work, preferring one we can actually REACH.

    MEASURED live 2026-08-29 upstairs at JEFF's: of three hotspots needing attention, 39994
    (3242,3446) was walled off while 39995 and 39996 were both reachable -- and there was no shut
    door anywhere in range, so no amount of door logic could help. Taking them in bare list order
    fixated on the one unreachable hotspot, walked at it, and wedged the obstacle-blind greedy
    walker, while reachable work sat two tiles away. Order within each group is preserved, so
    behaviour is unchanged whenever everything is reachable (the normal case).

    `reach` is a {(x, y): step distance} dict from worldmap.reachable_from -- costs no HTTP call
    and answers on any plane, unlike the old travel.reachable_set it replaced. None -- or an
    empty dict, which is what "cannot tell" looks like -- falls back to plain list order. Both a
    set and this dict answer `in` and truthiness the same way, so travel.can_reach (below) does
    not need to know which one it was handed."""
    pending = [h for h in hotspots if h.get("needsAttention")]
    if not pending:
        return None
    if not reach:
        return pending[0]

    def _rank(hotspot) -> int:
        """0 = reachable right now, 1 = another floor, 2 = this floor but walled off.

        The gap between 0 and 1 is what stops the routine ping-ponging. USER 2026-08-29: "why do
        we click the door inbetween each remove / build" -- with cross-floor ranked the same as
        reachable, it alternated between working a ground-floor hotspot and attempting the
        upstairs one, and every attempt re-opened the self-closing door at ROSS's on the way to a
        ladder it could not reach. Finish this floor, THEN climb once.

        2 sorts last but is still returned when nothing better exists, so a walled-off contract
        falls through to the door/walk recovery rather than reporting "no work left" and turning
        in an unfinished contract."""
        loc = hotspot.get("location")
        if not loc or loc.get("x") is None:
            return 0             # unknown -- do not deprioritise on a guess
        answer = travel.can_reach(loc, reach, plane=plane)
        if answer is True:
            return 0
        return 1 if answer is None else 2

    return min(pending, key=_rank)


def find_object_by_id(objects: list, object_id: int):
    matches = [o for o in objects if o.get("id") == object_id]
    if not matches:
        return None
    return min(matches, key=lambda o: o.get("distance", float("inf")))


def deficit(current: int, target: int) -> int:
    return max(0, target - current)


def count_item(inventory: list, name: str) -> int:
    # "qty", NOT "quantity" -- GET /inventory (InventoryProvider.java) emits the stack size under
    # "qty", and plank_make.py's own item.get("qty", 1) is the established precedent for exactly
    # this. Reading "quantity" here always fell through to the default of 1, which happened to be
    # harmless only because every item counted at the time was non-stacking; the moment runes
    # (which very much stack) got counted it would have silently reported 1 of 300.
    name_lower = name.lower()
    return sum(
        item.get("qty", 1)
        for item in inventory
        if (item.get("name") or "").lower() == name_lower
    )


def rune_pouch_counts(rune_pouch: dict) -> dict:
    counts = {}
    for slot in rune_pouch.get("runes", []):
        name = (slot.get("name") or "").lower()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + slot.get("quantity", 0)
    return counts


def needs_bank_trip(ctx, config: dict, state: dict) -> bool:
    restock = config.get("restock", {})
    inventory = controller.get_inventory()
    plank_name = config.get("plankName", "Teak plank")

    if deficit(planks_available(inventory, plank_name), restock.get("minPlanks", 0)) > 0:
        return True
    if deficit(count_item(inventory, "Steel bar"), restock.get("minSteelBars", 0)) > 0:
        return True

    min_tablets = restock.get("minTabletCount", 0)
    for tablet_name in restock.get("tabletTargets", {}):
        if deficit(count_item(inventory, tablet_name), min_tablets) > 0:
            return True

    min_runes = restock.get("minRuneCount", 0)
    rune_names = list(restock.get("runeTargets", {}))
    if rune_names:
        pouch = rune_pouch_counts(controller.get_rune_pouch())
        for rune_name in rune_names:
            if deficit(available_runes(inventory, pouch, rune_name), min_runes) > 0:
                return True

    return False


def available_runes(inventory: list, pouch_counts: dict, rune_name: str) -> int:
    """Loose runes in inventory PLUS whatever is in the rune pouch. OSRS draws a spell's runes
    from either automatically, so both count -- and bank_phase now withdraws LOOSE runes (it
    deliberately builds no rune-pouch-filling machinery, see its own comment), which a
    pouch-only check would never see. Checking the pouch alone therefore forced a bank trip on a
    player already carrying hundreds of the rune in hand."""
    return count_item(inventory, rune_name) + pouch_counts.get(rune_name.lower(), 0)


def bank_phase(ctx, config: dict, state: dict) -> None:
    bank_config = config.get("bank", {})
    bank_obj = banker.find_nearby_bank(bank_config)
    if bank_obj is None:
        home_teleport = config.get("homeTeleport", "varrock teleport")
        item = next(
            (i for i in controller.get_inventory() if home_teleport in (i.get("name") or "").lower()),
            None,
        )
        if item is None:
            ctx.state.trigger_safety_stop(
                f"out of {home_teleport} and no bank in range for Mahogany Homes restocking"
            )
            return
        debug_log.log("mahogany_homes", f"breaking {home_teleport} to reach a bank")
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        controller.human_delay(2.5, 3.2)
        return

    # Walk into range before clicking. banker.open_bank has its own approach step, but it
    # minimap-hops, and a minimap hop cannot open a closed door -- which is the usual thing
    # between this routine and the bank, because it banks from INSIDE the contract house.
    # travel.walk_to routes off the global collision map unconditionally now (no more
    # short-hop/obstacle-blind split to steer around), and it is the only walker carrying the
    # door-opening recovery.
    if bank_obj.get("distance", 0) > banker.BANK_APPROACH_TILES:
        debug_log.log("mahogany_homes",
                      f"bank is {bank_obj.get('distance')} tiles away -- walking to it")
        travel.walk_to(ctx, {"x": bank_obj.get("x"), "y": bank_obj.get("y"),
                             "plane": bank_obj.get("plane", 0)})
        bank_obj = banker.find_nearby_bank(bank_config) or bank_obj

    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank for Mahogany Homes restocking")
        return

    restock = config.get("restock", {})
    plank_name = config.get("plankName", "Teak plank")

    # Deposit first. Two reasons: the loadout below is sized against a nearly-empty inventory
    # (see DEFAULT_CONFIG's slot budget), so anything that accumulated mid-contract would push
    # it over 28; and it is the routine's only recovery path if inventory ever ends up in a
    # state nothing else anticipated.
    #
    # skipContainerDeposit -- banker's generic "empty every container into the bank" step would
    # empty the RUNE POUCH, which is a keepItem precisely because we want its contents. This
    # routine manages no container that needs emptying, so the step has nothing to do here.
    keep = list(KEEP_ITEMS) + [plank_name] + list(restock.get("runeTargets", {}))
    banker.deposit_inventory(ctx, {"keepItems": keep, "skipContainerDeposit": True})

    _withdraw_up_to(ctx, "Steel bar", restock.get("targetSteelBars", 0))
    # Bank still OPEN here on purpose -- Fill is only offered on the sack while a bank interface
    # is up (see banker.fill_plank_sack). With a sack, _load_plank_sack does the plank withdrawal
    # itself in whole-inventory shift-clicks and ends with the inventory topped back up, so
    # running the per-unit _withdraw_up_to first would just be ten wasted clicks.
    if banker.has_plank_sack():
        _load_plank_sack(ctx, plank_name)
    else:
        _withdraw_up_to(ctx, plank_name, restock.get("targetPlanks", 0))
    for tablet_name, target in restock.get("tabletTargets", {}).items():
        _withdraw_up_to(ctx, tablet_name, target)
    _withdraw_runes(ctx, restock)

    banker.close_bank(ctx)
    controller.human_delay(0.3, 0.6)

    # A bank trip that leaves us still short is the one failure this phase cannot fix by
    # repeating -- the bank is simply out of something. Bounded so it surfaces as a diagnosable
    # safety stop instead of an unbounded bank-open/withdraw-nothing/bank-close loop.
    if needs_bank_trip(ctx, config, state):
        stuck = ctx.state.data.get("mh_bank_stuck", 0) + 1
        ctx.state.data["mh_bank_stuck"] = stuck
        debug_log.log("mahogany_homes",
                      f"bank trip {stuck} left us still under the restock thresholds "
                      f"-- bank probably out of stock")
        if stuck >= STUCK_TICK_LIMIT:
            ctx.state.trigger_safety_stop(
                f"Mahogany Homes: {stuck} consecutive bank trips failed to reach the restock "
                f"thresholds -- the bank is out of planks/bars/tablets/runes"
            )
        return

    ctx.state.data["mh_bank_stuck"] = 0
    # A bank trip almost always ends somewhere else entirely (find_nearby_bank, or breaking the
    # home teleport), so the "already teleported for this contract" latch has to be released --
    # otherwise travel_phase would refuse to teleport back and try to WALK from the bank to the
    # house. Only reachable now that mid-contract banking actually happens (see tick()'s C3 fix);
    # before it, a bank diversion was overwritten before bank_phase ever ran.
    ctx.state.data["mh_teleported_this_contract"] = False
    ctx.state.data["mh_phase"] = "request_contract" if not state.get("hasContract") else "travel"


def _withdraw_up_to(ctx, name: str, target: int) -> None:
    have = count_item(controller.get_inventory(), name)
    need = deficit(have, target)
    if need <= 0:
        return
    banker.withdraw_loadout(ctx, {"withdraw": [{"name": name, "quantity": need}]}, fresh=True)


def _withdraw_runes(ctx, restock: dict) -> None:
    """Runes deliberately do NOT go through _withdraw_up_to. That helper is withdraw_loadout's
    per-unit click loop (one click per unit), which is right for 10 planks and catastrophic for
    300 astral runes. Runes stack, so one shift-click takes the whole bank stack into a single
    inventory slot -- the slot budget is identical whether that lands 300 or 30,000.

    Counted against loose inventory PLUS the rune pouch (available_runes), matching what
    needs_bank_trip gates on, so a pouch that already holds the target withdraws nothing."""
    targets = restock.get("runeTargets", {})
    if not targets:
        return
    inventory = controller.get_inventory()
    pouch = rune_pouch_counts(controller.get_rune_pouch())
    for rune_name, target in targets.items():
        if deficit(available_runes(inventory, pouch, rune_name), target) <= 0:
            continue
        banker.withdraw_all_shift(ctx, rune_name, fresh=True)


def request_contract_phase(ctx, config: dict, state: dict) -> None:
    if state.get("hasContract"):
        ctx.state.data["mh_contact_casts"] = 0
        return

    # A cast that lands but registers no contract must be bounded. LIVE RUN 2026-08-28: the
    # provider only learned the active contract from a GAMEMESSAGE, while the line that recovers
    # an in-progress contract after a client restart is a DIALOG -- so hasContract stayed false
    # and this re-cast NPC Contact once per tick for a contract we already held. That provider
    # bug is fixed; this is the loop it exposed, and the same reasoning as TELEPORT_MAX_ATTEMPTS:
    # anything that spends a resource on repeat needs a ceiling, not just a cooldown.
    casts = ctx.state.data.get("mh_contact_casts", 0)
    if casts >= CONTACT_MAX_CASTS:
        ctx.state.trigger_safety_stop(
            f"Mahogany Homes: cast NPC Contact {casts} times and no contract ever registered "
            f"-- the bridge is not seeing the assignment/reminder chat line"
        )
        return

    last_cast = ctx.state.data.get("mh_contact_cast_at", 0.0)
    if time.time() - last_cast < CONTACT_RETRY_COOLDOWN_S:
        return

    controller.key_tap(ctx.ser, MAGIC_TAB_KEY)
    controller.human_delay(0.3, 0.5)

    widget = controller.get_widget(NPC_CONTACT_GROUP, NPC_CONTACT_CHILD)
    if not widget or not widget.get("clickable"):
        stuck = ctx.state.data.get("mh_contact_stuck", 0) + 1
        ctx.state.data["mh_contact_stuck"] = stuck
        debug_log.log("mahogany_homes",
                      f"NPC Contact widget {NPC_CONTACT_GROUP}.{NPC_CONTACT_CHILD} not clickable "
                      f"({stuck}x) -- Lunar spellbook active?")
        if stuck >= STUCK_TICK_LIMIT:
            ctx.state.trigger_safety_stop(
                f"Mahogany Homes: NPC Contact widget {NPC_CONTACT_GROUP}.{NPC_CONTACT_CHILD} has "
                f"been unclickable for {stuck} consecutive ticks -- is the Lunar spellbook active "
                f"and the spell unfiltered?"
            )
        return

    ctx.state.data["mh_contact_stuck"] = 0
    debug_log.log("mahogany_homes", "casting NPC Contact for a new contract")
    controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False)
    ctx.state.data["mh_contact_cast_at"] = time.time()
    ctx.state.data["mh_contact_casts"] = casts + 1


def travel_phase(ctx, config: dict, state: dict) -> None:
    home_name = state.get("homeName")
    if ctx.state.data.get("mh_last_home") != home_name:
        ctx.state.data["mh_last_home"] = home_name
        ctx.state.data["mh_teleported_this_contract"] = False
        ctx.state.data["mh_teleport_attempts"] = 0

    target = state.get("standingLocation") or {}
    player = controller.get_player()
    if _chebyshev(player, target) > TRAVEL_TELEPORT_DISTANCE and not ctx.state.data.get("mh_teleported_this_contract"):
        entry = CITY_TELEPORTS.get(home_name)
        if entry is None:
            ctx.state.trigger_safety_stop(f"unrecognized Mahogany Homes home {home_name!r}")
            return
        teleport_name, menu_option = entry

        attempts = ctx.state.data.get("mh_teleport_attempts", 0)
        if attempts >= TELEPORT_MAX_ATTEMPTS:
            ctx.state.trigger_safety_stop(
                f"{teleport_name} did not move us on {attempts} attempts toward {home_name} "
                f"-- stopping rather than breaking the whole stack or walking cross-country"
            )
            return
        ctx.state.data["mh_teleport_attempts"] = attempts + 1

        # request_contract_phase leaves the MAGIC tab open (F6, for NPC Contact), and an
        # inventory item is not on screen while it is -- LIVE RUN 2026-08-28: the talisman
        # right-click opened an empty menu (['Cancel']) because it landed on the spellbook.
        # open_inventory_tab confirms the switch against /state rather than firing a key and
        # hoping.
        controller.open_inventory_tab(ctx.ser)

        item = next(
            (i for i in controller.get_inventory() if teleport_name in (i.get("name") or "").lower()),
            None,
        )
        if item is None:
            debug_log.log("mahogany_homes", f"no {teleport_name} in inventory -- banking")
            ctx.state.data["mh_phase"] = "bank"
            return

        if menu_option is None:
            debug_log.log("mahogany_homes", f"breaking {teleport_name} toward {home_name}")
            clicked = controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        else:
            # The item's left-click default is NOT the destination we want (Xeric's talisman),
            # so pick the option by name off the right-click menu instead.
            debug_log.log("mahogany_homes",
                          f"{teleport_name} -> {menu_option} toward {home_name}")
            clicked = controller.click_menu_action(ctx.ser, item, menu_option,
                                                   check_ui_blockers=False)

        # Latch on having actually MOVED, never on the click returning true. LIVE RUN 2026-08-28:
        # this was set unconditionally one line after a click that had plainly failed, so
        # travel_phase believed it had teleported and fell through to walking -- a 715-tile route
        # from Ardougne to Hosidius, on foot, which had to be stopped by hand. A click can also be
        # accepted and still not teleport (out of charges, a dialogue eating it), so the click's
        # own return value is not sufficient evidence either.
        moved = _await_teleport(player)
        if clicked and moved:
            ctx.state.data["mh_teleported_this_contract"] = True
        else:
            debug_log.log("mahogany_homes",
                          f"{teleport_name} didn't take us anywhere -- retrying rather than "
                          f"walking to {home_name}")
        return

    if not travel.walk_to(ctx, target, goal_adjacent=False):
        debug_log.log("mahogany_homes", f"still walking toward {home_name}'s standing tile")
        return

    ctx.state.data["mh_phase"] = "work"


def _await_teleport(before: dict) -> bool:
    """Poll until the player is TELEPORT_MIN_DISPLACEMENT tiles from `before`, or give up.

    Polled rather than slept because the two failure directions are wildly asymmetric: waiting
    too long costs a couple of idle seconds, while reading too early costs a TABLET and starts a
    retry loop (see TELEPORT_CONFIRM_TIMEOUT_S -- that is exactly what live run 5 did)."""
    deadline = time.time() + TELEPORT_CONFIRM_TIMEOUT_S
    while True:
        if _chebyshev(controller.get_player(), before) > TELEPORT_MIN_DISPLACEMENT:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(TELEPORT_CONFIRM_POLL_S)


def _chebyshev(a: dict, b: dict) -> int:
    return max(abs(a.get("x", 0) - b.get("x", 0)), abs(a.get("y", 0) - b.get("y", 0)))


def work_phase(ctx, config: dict, state: dict) -> None:
    hotspots = state.get("hotspots") or []

    # One bounded flood per tick, for pick_next_hotspot's RANKING only. worldmap answers on ANY
    # plane, which travel.reachable_set could only do via the client's own scene -- and it costs
    # no HTTP call at all. Doors are baked open in that map, so a hotspot behind a shut door reads
    # as reachable-with-a-door-to-open, which is the right answer for choosing what to work on
    # next. Empty when the client cannot tell, which every consumer reads as "don't know".
    # NOTE: the reachability GATE below (hotspot_at / can_reach) deliberately does NOT reuse this
    # -- see its own comment for why a static-map flood cannot be trusted to decide "can we
    # actually interact with this right now".
    player = controller.get_player() or {}
    player_plane = player.get("plane") or 0
    reach = (worldmap.reachable_from(player["x"], player["y"], player_plane)
             if player.get("x") is not None else {})

    target = pick_next_hotspot(hotspots, reach, player_plane)
    if target is None:
        # "Nothing needs attention" only means the contract is DONE if we are standing in the
        # house we just worked. CONFIRMED live 2026-08-29: from (1739,3549), outside Barbara's
        # area, every hotspot varbit read 0 with needsAttention false -- the varbits carry the
        # contract's state only while the house is loaded -- and this read it as a finished
        # contract and went to turn_in, trying to claim a reward for work it had not done.
        if not _at_the_home(state):
            debug_log.log("mahogany_homes",
                          f"no hotspot data for {state.get('homeName')} from out here -- "
                          f"going to the house before deciding anything")
            ctx.state.data["mh_phase"] = "travel"
            return
        ctx.state.data["mh_phase"] = "turn_in"
        return

    # Cost of THIS ONE hotspot, not of the whole remaining contract. Comparing against
    # state["requiredMaterials"] (the sum over every hotspot still needing attention) meant that
    # being 2 planks short of a 14-plank contract sent us to the bank before touching any of the
    # 90% we could already afford. `material`/`quantity` come from MahoganyHomesProvider's own
    # per-slot HotspotType, added for exactly this.
    inventory = controller.get_inventory()
    plank_name = config.get("plankName", "Teak plank")
    if not _can_afford(inventory, plank_name, state, target):
        ctx.state.data["mh_phase"] = "bank"
        return

    # Bounded scan. MEASURED 2026-08-29: an unbounded /objects walks the WHOLE scene -- 9266
    # objects, 124ms of client-thread work -- and this runs every tick, which the user felt as
    # lag spikes while the cursor was in flight. A hotspot is inside the contract house and
    # HOME_ARRIVAL_TILES away at most, so the whole-scene walk was never buying anything here.
    objects = controller.get_objects(radius=OBJECT_SCAN_RADIUS)
    obj = find_object_by_id(objects, target["objectId"])
    if obj is None:
        _approach_missing_hotspot(ctx, config, state, target)
        return

    # Seeing the object at all is the progress signal -- reset before the cooldown gate, not
    # after the click, so a stuck streak clears the moment we are actually in position.
    ctx.state.data["mh_work_stuck"] = 0

    # Count clicks that achieve nothing. A successful action always moves the hotspot's varbit, so
    # the (objectId, value) pair changing IS progress and clears the count; a pair that stays put
    # across several clicks means the object is visible but not actually reachable from here.
    # /objects reports the whole scene on our own plane, so on a split upper floor (NOELLA) the
    # far half's hotspots are fully visible from the near half -- see SPLIT_UPPER_FLOOR_HOMES.
    signature = (target["objectId"], target.get("value"))
    if ctx.state.data.get("mh_hotspot_signature") != signature:
        ctx.state.data["mh_hotspot_signature"] = signature
        ctx.state.data["mh_hotspot_clicks"] = 0

    # The DOOR comes first, before the first click at this hotspot. USER 2026-08-29: "why when we
    # come to the house do we not prioritise opening the god danm door, should be the first thing
    # we click if its closed". travel.walk_to already resolves doors before stepping off, but
    # work_phase does not necessarily walk at all: /objects returns the whole scene on our plane,
    # so a hotspot behind a shut front door is plainly VISIBLE and was clicked straight away --
    # the door only got resolved via the unreachable branch below, five wasted clicks and ~6s
    # later. MEASURED live 2026-08-29 at MARIAH's: exactly that, twice in one contract.
    # Runs once per hotspot state (the same signature latch), and costs nothing when the way is
    # already clear -- the edge test returns None without asking for a route unless a shut door
    # actually lies near the line we would travel.
    # REACHABILITY IS DECIDED BEFORE THE FIRST CLICK, not discovered by failing five of them.
    # /objects returns the whole scene on our plane, so a hotspot behind a wall or a shut door is
    # plainly visible and used to be clicked from wherever we stood; only after
    # HOTSPOT_UNREACHABLE_CLICKS failures (~20s) did anything try the door. MEASURED over one full
    # ROSS contract 2026-08-29: 15 of 27 hotspot clicks -- 56% -- achieved nothing, across three
    # separate stuck events. USER: "every time we have to walk through a door now we have to get
    # stuck on the outside 5 clicks in a row this is unacceptable."
    #
    # travel.can_reach flood-fills the client's own collision grid, so the answer is known up
    # front and on ANY plane (Shortest Path returns nothing upstairs). It returns None when the
    # client cannot tell, and that is deliberately treated as "carry on and click" -- an older
    # build behaves exactly as it did before rather than refusing to work at all.
    hotspot_at = target.get("location")
    # NOTE: there is deliberately NO "but we are standing right next to it, just click" override
    # here any more. It was added after a walk-in-place loop at JEFF's and it is what produced the
    # opposite failure at ROSS's: the player stood at (2617,3319) with the hotspot at (2617,3318),
    # Chebyshev distance 1 -- and the exterior wall runs exactly along that edge, so "adjacent"
    # meant "clicking through a wall". Raw tile distance cannot tell those two cases apart;
    # travel.can_reach now asks whether the EDGE is open, which separates them correctly and
    # answers both live cases right.
    # The GATE needs LIVE collision, never the static worldmap flood. worldmap.reachable_from
    # treats every door as OPEN (deliberately -- see worldmap.py), so its flood walks straight
    # through a shut one: MEASURED from ROSS's outside tile (2614,3319), it returns 447 tiles
    # including (2616,3318) and (2618,3318) -- the hotspot's own cardinal neighbours on the FAR
    # side of the shut front door. can_reach(hotspot_at, reach) built from that flood can answer
    # True from outside the house, and this gate would never fire. This is exactly the design
    # doc's split of responsibility: worldmap answers WHERE TO WALK (a shut door is just a route
    # you have not opened yet); travel.can_reach answers WHETHER AN OBJECT CAN BE INTERACTED WITH
    # RIGHT NOW, and that has to come from travel.reachable_set's LIVE GET /collision read, which
    # reports the door's real (shut) edge. Do not "simplify" this back to the worldmap flood --
    # that is precisely the bug this comment documents.
    live_reach = (travel.reachable_set(player["x"], player["y"], player_plane)
                  if player.get("x") is not None else set())
    if hotspot_at and travel.can_reach(hotspot_at, live_reach, plane=player_plane) is False:
        # Not reachable: open the door standing between us and it, whether it lies straight ahead
        # or behind us. Then walk -- and do not spend a click this tick.
        # ONE call now. This used to be three tiers -- open_frontier_door, then the straight-line
        # open_doorway_to, then a blunt open_nearest_shut_door that opened something and hoped.
        # All three existed because there was no route to ask; with a route the door is simply
        # the one standing on it, and that works on any plane.
        travel.open_door_toward(ctx, hotspot_at)
        travel.walk_to(ctx, _same_floor_walk_goal(state, target, hotspot_at),
                       tolerance=HOUSE_ARRIVAL_TOLERANCE)
        return

    if ctx.state.data.get("mh_door_checked") != signature:
        ctx.state.data["mh_door_checked"] = signature
        if hotspot_at:
            travel.open_door_toward(ctx, hotspot_at)

    if ctx.state.data.get("mh_hotspot_clicks", 0) >= HOTSPOT_UNREACHABLE_CLICKS:
        split_floor = state.get("homeName") in SPLIT_UPPER_FLOOR_HOMES
        player_plane = (controller.get_player() or {}).get("plane") or 0
        upstairs = player_plane > 0

        if split_floor and upstairs:
            last_climb = ctx.state.data.get("mh_last_ladder_click", 0.0)
            if time.time() - last_climb < LADDER_CLICK_COOLDOWN_S:
                return
            ladder = _pick_ladder(state, None)     # nearest to US -- we want off this floor
            if ladder is not None:
                ctx.state.data["mh_hotspot_clicks"] = 0
                ctx.state.data["mh_hotspot_signature"] = None
                _climb(ctx, ladder, "Climb-down",
                       f"hotspot {target['objectId']} is visible but unreachable after "
                       f"{HOTSPOT_UNREACHABLE_CLICKS} clicks -- climbing down to take "
                       f"{state.get('homeName')}'s other staircase",
                       from_plane=(controller.get_player() or {}).get("plane"))
                return

        # Not a split-floor problem: we are simply not able to reach the thing we can see --
        # standing outside the house is the ordinary case. WALK, because walking is the only
        # thing that runs travel.py's door recovery, and a shut front door is exactly what puts
        # a visible hotspot out of reach. LIVE 2026-08-28: without this the routine clicked a
        # Table two tiles away through a wall, every 3s, indefinitely -- and mh_work_stuck could
        # never catch it, because seeing the object is what resets that counter.
        ctx.state.data["mh_hotspot_clicks"] = 0
        ctx.state.data["mh_hotspot_signature"] = None
        debug_log.log("mahogany_homes",
                      f"hotspot {target['objectId']} visible but unreachable after "
                      f"{HOTSPOT_UNREACHABLE_CLICKS} clicks -- walking to it "
                      f"in {state.get('homeName')}'s house (resolves a shut door first)")
        # Head for the HOTSPOT, not the house's standing tile. MEASURED live 2026-08-29 at
        # NORMAN's: standingLocation (3038,3344) sits on the SAME side of the shut door 24050
        # (3037,3347, blocking the 3346|3347 crossing) as the player, so a walk there crosses no
        # doorway at all -- the door recovery correctly finds nothing between us and that tile,
        # and we end up standing outside exactly as before. The hotspot is what we actually need
        # to reach, and using it as the goal is what makes the doorway between us and it visible
        # to the walker (which now resolves doors BEFORE stepping off -- see travel.walk_to).
        # Falls back to the standing tile when `location` is absent (object outside the loaded
        # scene, or an un-rebuilt client), rather than walking nowhere.
        # Two DIFFERENT tiles, for two different questions. MEASURED live 2026-08-29 at
        # Barbara's: the hotspot's own tile (1749,3536) holds furniture, so Shortest Path returns
        # no route to it whatsoever (0 steps on 5 of 5 calls) and walk_to falls through to the
        # obstacle-blind greedy walker, which wedges -- that is what made the previous attempt
        # loop click-click-walk forever. The standing tile routes in 6 steps, but it sits on the
        # SAME side of the door as we do, so a walk there never reveals the doorway.
        # So: resolve the door against the hotspot we cannot reach, then walk to the tile we can.
        hotspot_at = target.get("location")
        if hotspot_at:
            # We have ALREADY proven we cannot reach this hotspot, so open whatever door stands
            # between us and it. This used to be three tiers -- open_frontier_door (named the
            # exact door on the boundary of the region we were stuck in, from the collision flood
            # fill), then the straight-line open_doorway_to, then a blunt open_nearest_shut_door
            # that opened something and hoped, reached only when the first two found nothing at
            # all (MEASURED live 2026-08-29 outside ROSS's house: player on the grass at
            # (2614,3319), Broken Range inside at (2617,3318), the only way in a door at
            # (2610,3316) that is a DETOUR WEST of both of us -- neither of the first two tiers
            # could see it). travel.open_door_toward replaces all three: it asks the global map
            # for a route to the hotspot and opens whichever door actually lies on it, which
            # finds the same detour door for free and needs no separate last-resort tier.
            travel.open_door_toward(ctx, hotspot_at)

        # Normally the STANDING tile: the hotspot's own tile is usually furniture and unroutable
        # (Barbara's, above), so it is the tile we can actually walk to. The ONE exception is
        # being on a different floor from it -- `standingLocation` is always reported at plane 0,
        # so from upstairs that is a cross-plane request, and Shortest Path answers it with a
        # **Climb-down Ladder TRANSPORT** that carries us DOWN and away from the work. MEASURED
        # live 2026-08-29 at JESS's, where all seven hotspots are on plane 1: "following a
        # 14-tile route" -> "using transport: Climb-down Ladder" -> "used the transport but
        # didn't arrive where expected" -> "target is on a different plane -- can't walk there
        # yet", then the whole cycle again, ~90s a pass. Only then is the hotspot the better
        # goal, and only if it is on OUR floor. Deliberately narrow: widening this to every
        # same-plane case regresses the Barbara's/NORMAN's behaviour the standing tile exists for.
        walk_goal = state.get("standingLocation") or {}
        if (walk_goal.get("plane") is not None and walk_goal.get("plane") != player_plane
                and hotspot_at and hotspot_at.get("plane") == player_plane):
            walk_goal = hotspot_at
        travel.walk_to(ctx, walk_goal, tolerance=HOUSE_ARRIVAL_TOLERANCE)
        return

    last_click = ctx.state.data.get("mh_last_hotspot_click", 0.0)
    cooldown = ctx.state.data.get("mh_hotspot_cooldown", HOTSPOT_CLICK_COOLDOWN_S)
    if time.time() - last_click < cooldown:
        return

    # How long to wait before the NEXT click depends on what this one is, not on an average of
    # both -- see HOTSPOT_CLICK_COOLDOWN_S. A build target is named "<thing> space".
    #
    # ...and on how far we have to WALK to it. The game queues the interaction and performs it on
    # ARRIVAL (the same fact golem_crafting's carve notes record: "the side carved is the tile you
    # walked TO"), so until the player gets there the action has not started and the varbit cannot
    # move. Without this the cooldown expired mid-walk, we re-clicked -- re-queueing, sometimes
    # re-pathing -- and every one of those clicks counted as "no progress", so
    # HOTSPOT_UNREACHABLE_CLICKS fired on a hotspot that was perfectly reachable and work_phase
    # walked away from it. MEASURED live 2026-08-29 at ROSS's: five `Repair Broken Range` actions
    # all registering correctly in the recorder against the right object, no varbit movement, then
    # the unreachable branch. Capped so a hotspot that truly cannot be reached still gives up.
    base = HOTSPOT_CLICK_COOLDOWN_S if _is_instant_action(obj) else HOTSPOT_BUILD_COOLDOWN_S
    ctx.state.data["mh_hotspot_cooldown"] = min(
        base + (obj.get("distance") or 0) * HOTSPOT_WALK_ALLOWANCE_S,
        HOTSPOT_COOLDOWN_MAX_S,
    )
    debug_log.log("mahogany_homes", f"working hotspot {target['objectId']} (varb {target['varb']})")
    controller.click_entity(
        ctx.ser, obj,
        refresh_target=controller.object_refresh_target(obj["x"], obj["y"], obj["plane"], obj["id"]),
        refresh_legs=2,
    )
    ctx.state.data["mh_last_hotspot_click"] = time.time()
    ctx.state.data["mh_hotspot_clicks"] = ctx.state.data.get("mh_hotspot_clicks", 0) + 1


def _can_afford(inventory: list, plank_name: str, state: dict, target: dict) -> bool:
    """Cost of THIS ONE hotspot, not of the whole remaining contract. Comparing against
    state["requiredMaterials"] (the sum over every hotspot still needing attention) meant that
    being 2 planks short of a 14-plank contract sent us to the bank before touching any of the
    90% we could already afford.

    `material`/`quantity` come from MahoganyHomesProvider's own per-slot HotspotType, added for
    exactly this -- and they only exist on a REBUILT client. Against an older bridge the fields
    are absent, and a `quantity` defaulting to 0 would read as "this hotspot is free": fail-open,
    clicking hotspots we cannot pay for and silently achieving nothing. So an absent field falls
    back to the old whole-contract check instead, which is merely conservative."""
    quantity = target.get("quantity")
    if quantity is None:
        required = state.get("requiredMaterials") or {}
        have_planks = planks_available(inventory, plank_name)
        have_bars = count_item(inventory, "Steel bar")
        if (deficit(have_planks, required.get("planks", 0)) > 0
                or deficit(have_bars, required.get("steelBars", 0)) > 0):
            debug_log.log(
                "mahogany_homes",
                f"per-hotspot cost missing from /mahogany-homes (client not rebuilt?) -- fell "
                f"back to the whole-contract check and we're short (have {have_planks} planks/"
                f"{have_bars} bars, need {required.get('planks', 0)}/"
                f"{required.get('steelBars', 0)}) -- banking",
            )
            return False
        return True

    is_bar = target.get("material") == "STEEL_BAR"
    material_name = "Steel bar" if is_bar else plank_name
    # Planks in the SACK count: the game consumes them straight from it, no Empty needed.
    # CONFIRMED in the recordings -- varbit 18173 ticks down 28->27->25->23 in step with each
    # hotspot's cost, alongside the hotspot varbit, the build animation and the Construction XP
    # drop (recordings/2026-08-27_174045_mahogny-homes.log:1648). Counting loose planks only
    # would send us to the bank with a full sack in hand.
    have = count_item(inventory, "Steel bar") if is_bar else planks_available(inventory, plank_name)
    if deficit(have, quantity) > 0:
        debug_log.log(
            "mahogany_homes",
            f"short on {material_name} for hotspot {target['objectId']} "
            f"(have {have}, need {quantity}) -- banking",
        )
        return False
    return True


def _pick_ladder(state: dict, near: dict | None):
    """The home's nearest ladder to `near` (a {"x","y"} dict), or to the player when `near` is
    None or has no coordinates.

    Choosing by proximity to the HOTSPOT rather than to the player is what makes NOELLA work: its
    two staircases lead to two disconnected halves of the upper floor, so the nearest ladder to us
    is a coin flip on whether it reaches the hotspot at all. Every other home has one staircase or
    none, where the two choices coincide -- so this is strictly better, never different, elsewhere.
    """
    ladder_ids = state.get("ladderObjectIds") or []
    if not ladder_ids:
        return None

    ladders = [o for o in controller.get_objects(radius=OBJECT_SCAN_RADIUS)
               if o.get("id") in ladder_ids]
    if not ladders:
        return None

    if near and near.get("x") is not None:
        return min(ladders, key=lambda o: _chebyshev(o, near))
    return min(ladders, key=lambda o: o.get("distance", float("inf")))


def _climb(ctx, ladder, option, why: str, from_plane: int | None = None) -> None:
    """Click one ladder, respecting the shared climb cooldown. `option` is a single menu option
    when the direction is known, or a sequence to let the ladder decide."""
    debug_log.log("mahogany_homes", why)

    # A LADDER WE CANNOT REACH MUST NOT BE CLICKED, for exactly the reason a hotspot must not be:
    # the game ACCEPTS an interaction it cannot path to and then silently does nothing. MEASURED
    # live 2026-08-29 at ROSS's: player (2618,3315), ladder 16683 at (2617,3315) -- cardinally
    # adjacent, but (2618,3315) carries BLOCK_MOVEMENT_WEST (65664), so the shared edge is a wall.
    # `menu-select Climb-up Ladder` registered three times, the player never changed floor, and
    # the run safety-stopped. Adjacency is not reachability; the EDGE decides.
    #
    # This is the same gate work_phase applies to hotspots. It belongs at EVERY world-object click
    # site, not one at a time -- that is why this bug kept reappearing somewhere new.
    here = controller.get_player() or {}
    if here.get("x") is not None and ladder.get("x") is not None:
        # LIVE collision, not the static worldmap flood -- same reasoning as work_phase's own
        # hotspot gate (see its comment for the measured proof): worldmap.reachable_from treats
        # every door as open, so it can call a door-blocked ladder "reachable" and this gate would
        # never fire. travel.reachable_set reads GET /collision, which reports the door's real,
        # currently-shut edge -- worldmap answers where to walk, travel.can_reach answers whether
        # we can interact with something right now, and those need different data sources.
        reach = travel.reachable_set(here["x"], here["y"], here.get("plane") or 0)
        if travel.can_reach(ladder, reach, plane=here.get("plane") or 0) is False:
            debug_log.log("mahogany_homes",
                          f"ladder {ladder.get('id')} at ({ladder.get('x')},{ladder.get('y')}) "
                          f"is walled off from here -- opening the way instead of clicking it")
            # ONE call now -- see work_phase's own note on collapsing the three door tiers into
            # travel.open_door_toward.
            travel.open_door_toward(ctx, ladder)
            travel.walk_to(ctx, ladder, tolerance=HOUSE_ARRIVAL_TOLERANCE)
            ctx.state.data["mh_last_ladder_click"] = time.time()
            return

    # NOTE: no unconditional door resolution here. There used to be one, and once the
    # reachability gate above existed it became pure harm: with the ladder already reachable it
    # still ran the straight-line edge test, which false-positives on whatever door happens to
    # share an axis with us -- at ROSS's that is the front door BEHIND us, so every climb attempt
    # opened it and stepped outside. USER 2026-08-29: "why do we click the door inbetween each
    # remove / build" and "still doesnt make much sense since we can reach them so why would we
    # step out". MEASURED from inside the house: can_reach(ladder) was True and the door was
    # opened anyway. The gate above is the only place door work belongs -- it runs exactly when
    # the ladder cannot be reached, and not otherwise.

    # Click the ladder's own reported point -- its clickbox CENTRE. USER 2026-08-29: "revert the
    # ladder to click center".
    #
    # A tile-corner aim was tried the same day and REVERTED, because it broke ladders that were
    # already working. The two houses differ in a way that matters: at MARIAH's the reported
    # centroid and the tile centre from /ground/walk were the SAME point ((1249,709) vs
    # (1249,707)), i.e. the clickbox was effectively the whole tile and the point landed on bare
    # floorboards. At ROSS's, ladder 16683 reports (1263,316) against a tile centre of
    # (1360,386) -- ~100px apart, and the centroid is squarely ON the model. So overriding the
    # centroid unconditionally aimed a good click away from its target, and the corner rotation
    # then burned the whole stuck budget getting nowhere. Do not reintroduce a blanket override;
    # if a specific ladder needs a different point, it needs evidence for that ladder.
    #
    # offset=(0, 0) is what makes this the CENTRE: click_menu_action's default (-7, +7) nudge is
    # applied to whatever pixel it is handed, and +7 is DOWNWARD in screen space.
    controller.click_menu_action(ctx.ser, ladder, option, offset=(0, 0), check_ui_blockers=False)
    ctx.state.data["mh_last_ladder_click"] = time.time()
    # The plane we climbed FROM. _approach_missing_hotspot compares it against the live plane to
    # tell a climb that actually moved us from one that only opened a menu -- see the reset there.
    ctx.state.data["mh_ladder_click_plane"] = from_plane


def _approach_missing_hotspot(ctx, config: dict, state: dict, target: dict) -> None:
    """The hotspot object isn't in /objects. That is ambiguous in a way that matters: /objects
    only reports the player's CURRENT plane, and most homes put hotspots upstairs while
    standingLocation is always plane 0 -- so "not found" can mean "wrong floor", not just "not
    close enough yet". Try the home's own ladders (MahoganyHomesProvider ships their object ids
    per Home) before falling back to walking.

    "Climb-up"/"Climb-down" are passed as a SEQUENCE to one right-click menu read, so whichever
    direction the ladder actually offers from this floor is the one taken -- no need to guess
    the hotspot's plane, which the endpoint doesn't report.

    Walking is tried FIRST while still outside the house: a ladder can already be in the loaded
    scene from well down the street, and climbing one before arriving would just be noise. Only
    once we are standing in the house does an unfound hotspot mean "wrong floor". _chebyshev
    ignores plane on purpose, so being upstairs still reads as "at the house"."""
    player = controller.get_player()

    # Changing FLOOR is progress, so it clears the not-reachable counter. Without this the count
    # is really "ticks since the hotspot appeared", and a climb that works still safety-stops:
    # LIVE 2026-08-29 at MARIAH's, a Climb-down that went through on the third attempt was
    # followed by the stop firing two ticks later anyway: three attempts at
    # LADDER_CLICK_COOLDOWN_S apiece had already spent the 15-tick budget, so the climb that
    # actually worked never got credit for it.
    # Keyed on the plane actually CHANGING rather than on the menu click being taken, so an
    # up-then-down loop cannot reset the counter forever and stall the safety stop.
    # NOT popped on read: a climb takes a tick or two to land, so the tick right after the click
    # still reports the old plane. LIVE 2026-08-29 the marker was consumed on that tick and the
    # descent -- which did happen -- went unnoticed, so the stop fired anyway. It is cleared only
    # once it has actually answered the question.
    climbed_from = ctx.state.data.get("mh_ladder_click_plane")
    if climbed_from is not None and player.get("plane") != climbed_from:
        ctx.state.data["mh_work_stuck"] = 0
        ctx.state.data["mh_ladder_click_plane"] = None

    stuck = ctx.state.data.get("mh_work_stuck", 0) + 1
    ctx.state.data["mh_work_stuck"] = stuck
    if stuck >= STUCK_TICK_LIMIT:
        ctx.state.trigger_safety_stop(
            f"Mahogany Homes: hotspot object {target.get('objectId')} at "
            f"{state.get('homeName')}'s house has not been reachable for {stuck} consecutive "
            f"ticks (walking and ladder-climbing both tried)"
        )
        return

    standing = state.get("standingLocation") or {}
    at_the_house = _chebyshev(player, standing) <= HOME_ARRIVAL_TILES

    # Which FLOOR the hotspot is on, when the bridge could find the object in the scene (it scans
    # every plane, unlike /objects -- see MahoganyHomesProvider.locateHotspotObjects). None means
    # either an un-rebuilt client or an object outside render distance; both fall back to the old
    # "offer both directions and take whichever the ladder gives" guess.
    target_plane = target.get("plane")
    player_plane = player.get("plane")
    same_floor = (target_plane is not None and player_plane is not None
                  and target_plane == player_plane)

    ladder = None
    # A hotspot KNOWN to be on this floor is never a ladder problem -- climbing would leave the
    # floor the work is on and strand us a storey away from a hotspot we could have walked to.
    # (On a split upper floor that judgement is made in work_phase instead, where the object is
    # actually visible; this branch only runs when it is not.)
    if at_the_house and not same_floor:
        # By proximity to the HOTSPOT, not to us -- see _pick_ladder.
        ladder = _pick_ladder(state, target.get("location"))

    if ladder is not None:
        last_climb = ctx.state.data.get("mh_last_ladder_click", 0.0)
        if time.time() - last_climb < LADDER_CLICK_COOLDOWN_S:
            return
        # With a known plane the direction is decided, not guessed: a sequence would still work in
        # a two-storey house, but a three-storey one can be climbed the wrong way and then has to
        # guess its way back.
        if target_plane is not None and player_plane is not None:
            option = "Climb-up" if target_plane > player_plane else "Climb-down"
            where = f"on plane {target_plane}, we're on {player_plane}"
        else:
            option = ["Climb-up", "Climb-down"]
            where = "on an unknown floor"
        _climb(ctx, ladder, option,
               f"hotspot {target['objectId']} {where} -- taking ladder {ladder['id']} "
               f"({stuck}x since the last worked hotspot)",
               from_plane=player_plane)
        return

    debug_log.log(
        "mahogany_homes",
        f"hotspot object {target['objectId']} not visible yet ({stuck}x, no ladder to try) "
        f"-- walking toward {state.get('homeName')}'s house",
    )
    travel.walk_to(ctx, standing, tolerance=HOUSE_ARRIVAL_TOLERANCE, goal_adjacent=False)


def turn_in_phase(ctx, config: dict, state: dict) -> None:
    # A contract with work left in it is not a contract to claim. LIVE 2026-08-29 at ROSS's: the
    # phase latched to "turn_in" one tick after a single hotspot was built -- work_phase only
    # moves here when pick_next_hotspot returns None, so one transient empty read was enough --
    # and then talked to Ross once every ~3s indefinitely while SIX of his seven hotspots still
    # reported needsAttention. Nothing re-checked, so it could not recover on its own.
    # This is the mirror of work_phase's own "no hotspot data from out here" guard, and the two
    # conditions are mutually exclusive on the same data, so they cannot ping-pong.
    if _at_the_home(state) and pick_next_hotspot(state.get("hotspots") or []) is not None:
        debug_log.log("mahogany_homes",
                      f"{state.get('homeName')} still has hotspots needing attention -- "
                      f"back to work rather than claiming a reward for it")
        ctx.state.data["mh_phase"] = "work"
        return

    # A ceiling, not just a cooldown -- see the teleport/NPC-Contact caps for why anything that
    # repeats needs one. The counter is per-home, so a normal turn-in (which changes homeName)
    # always starts fresh.
    if ctx.state.data.get("mh_turnin_home") != state.get("homeName"):
        ctx.state.data["mh_turnin_home"] = state.get("homeName")
        ctx.state.data["mh_turnin_clicks"] = 0

    if ctx.state.data.get("mh_turnin_clicks", 0) >= TURN_IN_MAX_CLICKS:
        # No trailing "Automation paused..." here -- the engine appends its own, and saying it
        # twice is what the first version of this message did. The count covers BOTH ways this
        # phase can spend an attempt (talking, and climbing toward the homeowner), because either
        # repeating without the contract clearing means the same thing: it is not progressing.
        ctx.state.trigger_safety_stop(
            f"Mahogany Homes: {TURN_IN_MAX_CLICKS} attempts to reach and turn in with "
            f"{state.get('npcName')} at {state.get('homeName')}'s house without the contract "
            f"clearing"
        )
        return

    npc_id = state.get("npcId")
    npc = controller.get_npc_nearest(npc_id)
    if npc is None:
        # WALKING CANNOT CHANGE FLOOR. LIVE 2026-08-29 at ROSS's: with every hotspot built and
        # the player still upstairs, this walked at `standingLocation` -- which is always plane 0
        # -- from plane 1, and the walker clicked the identical pixel (1144,724) about thirty
        # times without the player moving. work_phase's ladder handling could never help: it
        # lives in _approach_missing_hotspot, and with no hotspots left work_phase does not run.
        # So the descent has to be owned here too.
        player = controller.get_player() or {}
        player_plane = player.get("plane")
        owner_plane = _homeowner_plane(state)

        if player_plane is not None and player_plane != owner_plane:
            last_climb = ctx.state.data.get("mh_last_ladder_click", 0.0)
            if time.time() - last_climb < LADDER_CLICK_COOLDOWN_S:
                return
            ladder = _pick_ladder(state, None)     # nearest to US -- we want off this floor
            if ladder is not None:
                # Counts against the same ceiling: both paths mean "the turn-in is not
                # progressing", and an unbounded climb loop is exactly what this phase must not
                # be able to do.
                ctx.state.data["mh_turnin_clicks"] = ctx.state.data.get("mh_turnin_clicks", 0) + 1
                _climb(ctx, ladder,
                       "Climb-up" if owner_plane > player_plane else "Climb-down",
                       f"{state.get('npcName')} is on plane {owner_plane}, we're on "
                       f"{player_plane} -- taking ladder {ladder['id']} to turn in",
                       from_plane=player_plane)
                return

        debug_log.log("mahogany_homes", f"homeowner npc {npc_id} not visible -- walking to standing tile")
        travel.walk_to(ctx, state.get("standingLocation") or {}, goal_adjacent=False)
        return

    # NOTE: _accept_tea_if_offered is deliberately NOT called here -- see tick(), which calls it
    # unconditionally because the offer can arrive and vanish on a tick this phase never runs on.
    last_click = ctx.state.data.get("mh_last_turnin_click", 0.0)
    if time.time() - last_click < TURN_IN_CLICK_COOLDOWN_S:
        return

    debug_log.log("mahogany_homes", f"turning in the contract with {state.get('npcName')}")
    controller.click_menu_action(ctx.ser, npc, "Talk-to")
    ctx.state.data["mh_last_turnin_click"] = time.time()
    ctx.state.data["mh_turnin_clicks"] = ctx.state.data.get("mh_turnin_clicks", 0) + 1


# Only Remove is instant (user, 2026-08-29: "the remove action is basically instant so we can
# just reclick after that"; and, correcting an earlier guess of mine, "repair should be similar
# to build"). Build and Repair both run an animation past the XP drop that swallows clicks.
INSTANT_HOTSPOT_ACTION = "remove"


def _same_floor_walk_goal(state: dict, target: dict, hotspot_at: dict | None) -> dict:
    """Where to WALK when we cannot reach the hotspot.

    Normally the home's standing tile: a hotspot's own tile is furniture and unroutable, so the
    standing tile is the one we can actually get to. The exception is being on a different floor
    from it -- `standingLocation` is always reported at plane 0, so from upstairs that is a
    cross-plane request and Shortest Path answers it with a Climb-down Ladder TRANSPORT that
    carries us away from the work (MEASURED at JESS's)."""
    goal = state.get("standingLocation") or {}
    player_plane = (controller.get_player() or {}).get("plane") or 0
    if (goal.get("plane") is not None and goal.get("plane") != player_plane
            and hotspot_at and hotspot_at.get("plane") == player_plane):
        return hotspot_at
    return goal


def _homeowner_plane(state: dict) -> int:
    """Which floor the homeowner is standing on.

    `standingLocation` is always reported at plane 0, so it cannot answer this on its own --
    `npcUpstairs` is the field that does (true for JESS and NORMAN only, ported from the
    reference plugin's Home.isNpcUpstairs)."""
    return 1 if state.get("npcUpstairs") else 0


def _at_the_home(state: dict) -> bool:
    """Are we actually at the contract house, close enough to trust its hotspot varbits?

    Prefers the home's own WorldArea (rebuilt clients report `area`), falling back to distance
    from standingLocation. Fails OPEN when neither is reported -- an older client build should
    behave exactly as it did before rather than refusing to ever turn a contract in."""
    player = controller.get_player()
    px, py = player.get("x"), player.get("y")
    if px is None:
        return True

    area = state.get("area")
    if area and area.get("x") is not None:
        return (area["x"] <= px < area["x"] + area.get("width", 0)
                and area["y"] <= py < area["y"] + area.get("height", 0))

    standing = state.get("standingLocation")
    if standing and standing.get("x") is not None:
        return _chebyshev(player, standing) <= HOME_ARRIVAL_TILES
    return True


def _is_instant_action(obj: dict) -> bool:
    """Can we reclick immediately after acting on this hotspot object?

    Decided from the object's own left-click action (`actions[0]`, impostor-resolved by
    ObjectProvider) rather than its name, because the name cannot separate Repair from Remove --
    both act on named furniture like "Hat stand", while only Build acts on a "<thing> space".

    Fails SLOW when the client does not report actions (older build): clicking too early loses
    the click outright and costs a whole retry, while waiting needlessly costs a moment."""
    actions = obj.get("actions")
    if not actions:
        return False
    return (actions[0] or "").lower() == INSTANT_HOTSPOT_ACTION


def _accept_tea_if_offered(ctx) -> bool:
    # Scoped to the dialogue OPTION list, and that scoping is load-bearing -- a text match over
    # the whole widget tree is not merely imprecise here, it DEADLOCKS the routine. LIVE
    # 2026-08-29 (run18): the chatbox (group 162) keeps "Aaah, nothing like a nice cuppa tea!"
    # and "You drink the tea, you feel energized!" in its scrollback for the rest of the session
    # once any tea has been drunk, and those lines carry real screen coordinates, so the
    # screenX/screenY guard below did not exclude them. Chat history never clears, so this
    # returned True on EVERY tick and tick() never reached a phase handler at all: three clicks
    # aimed at a chat line, then SPACE forever, with `dialogOpen` false the whole time.
    #
    # A screen position is still part of the match. LIVE 2026-08-29 (earlier): the text match
    # alone hit a widget carrying "cuppa" with no usable coordinates -- present in the interface
    # tree but not on screen -- so this announced itself and attempted a click on every tick,
    # with click_entity's guard refusing it each time.
    option = next(
        (w for w in controller.get_widgets_dump()
         if w.get("group") == DIALOG_OPTION_GROUP
         and "cuppa" in (w.get("text") or "").lower()
         and w.get("screenX") is not None and w.get("screenY") is not None),
        None,
    )
    if option is None:
        ctx.state.data["mh_tea_clicks"] = 0
        return False

    # Bounded and rate-limited. CONFIRMED live 2026-08-29: with neither, this re-clicked the same
    # option every tick for as long as the dialogue stayed open, and the caller went on to
    # right-click the homeowner across the screen in the SAME tick -- which could only miss,
    # because an open dialogue eats the Talk-to menu. The user saw it as clicking back and forth.
    last = ctx.state.data.get("mh_tea_click_at", 0.0)
    if time.time() - last < TEA_CLICK_COOLDOWN_S:
        return True                       # still settling -- but the dialogue IS up, so stop here

    clicks = ctx.state.data.get("mh_tea_clicks", 0)
    if clicks >= TEA_MAX_CLICKS:
        # Never spend the whole session on a cuppa. Space is the engine's own way past a
        # "click here to continue", and costs nothing if this turns out not to be one.
        debug_log.log("mahogany_homes",
                      f"tea dialogue still up after {clicks} clicks -- pressing Space instead")
        controller.key_tap(ctx.ser, "SPACE")
        ctx.state.data["mh_tea_click_at"] = time.time()
        return True

    debug_log.log("mahogany_homes", "accepting the offered cup of tea")
    controller.click_entity(ctx.ser, option, offset=(0, 0), check_ui_blockers=False)
    ctx.state.data["mh_tea_click_at"] = time.time()
    ctx.state.data["mh_tea_clicks"] = clicks + 1
    return True


PHASE_HANDLERS = {
    "bank": bank_phase,
    "request_contract": request_contract_phase,
    "travel": travel_phase,
    "work": work_phase,
    "turn_in": turn_in_phase,
}


def tick(ctx) -> None:
    config = ctx.state.config
    if not config.get("enabled", True):
        return

    state = controller.get_mahogany_homes_state()

    # Unconditional, and NOT inside turn_in_phase, because the completion chat line and the
    # homeowner's tea offer arrive on the SAME tick: by the time hasContract flips false, tick()
    # has already left the "turn_in" phase, so a call sited inside that phase can never fire for
    # the tick the dialogue is actually up on. Cheap -- no-ops immediately when nothing matches.
    # A dialogue on screen blocks every menu, so nothing else this tick can succeed -- and
    # trying anyway is what produced the corner-to-corner clicking.
    if _accept_tea_if_offered(ctx):
        return

    phase = ctx.state.data.get("mh_phase")
    if phase is None:
        # Fresh process, or the routine file changed -- ctx.state.data has just been wiped, so
        # by definition there is no work_phase diversion to preserve here. This used to default
        # to "bank", and with a contract already in progress nothing below overrode it ("bank"
        # deliberately does not auto-promote), so EVERY restart mid-contract went banking.
        # CONFIRMED live 2026-08-28: restarted inside Mariah's house carrying 19 planks and 2
        # bars, it went straight for the Hosidius bank booth 20 tiles away through a wall.
        # Derive it from live state instead; the no-contract branch just below re-derives it
        # again anyway, so this only decides the has-contract case.
        phase = "bank" if needs_bank_trip(ctx, config, state) else "travel"

    if state.get("hasContract") and not _tier_matches(config, state):
        # Should be unreachable live (NPC Contact is configured to request Adept), but an
        # unenforced constraint is one nobody notices breaking. There is no way to abandon a
        # contract in-game, so all we can do is refuse to work it and say so.
        if phase != "request_contract":
            debug_log.log(
                "mahogany_homes",
                f"contract tier {state.get('contractTier')} is not the configured "
                f"{config.get('tier')} tier -- refusing to work it",
            )
        ctx.state.data["mh_phase"] = "request_contract"
        return

    if not state.get("hasContract"):
        if needs_bank_trip(ctx, config, state):
            phase = "bank"
        else:
            phase = "request_contract"
    elif phase == "request_contract":
        # ONLY request_contract auto-promotes. "bank" must NOT: work_phase's own mid-contract
        # "I can't afford the next hotspot" diversion sets mh_phase to "bank", and promoting it
        # back to "travel" here discarded that diversion before bank_phase ever ran once --
        # a livelock of work -> divert -> overridden -> work. bank_phase sets the phase itself
        # once it has actually restocked.
        phase = "travel"
    elif phase == "work" and needs_bank_trip(ctx, config, state):
        # FINISH THE CONTRACT BEFORE RESTOCKING. USER 2026-08-29: "another big nono was finishing
        # barbaras house then before handing in the contract go to bank and then back again
        # wasting a tp ... finish the contract first then bank". MEASURED live that run at
        # Barbara's: every hotspot was already built, and this still diverted to the Varrock bank
        # and teleported back before turning in -- a whole round trip plus a tablet, to top up
        # planks that were not needed yet. Turning in first costs nothing: hasContract then flips
        # false and the branch above sends us to the bank anyway, from wherever we end up.
        #
        # Gated on being AT the home, because the hotspot varbits only carry the contract's state
        # while the house is loaded -- from out of town every hotspot reads "nothing needs
        # attention", and without the gate that would suppress every legitimate restock. Same
        # reasoning as work_phase's own "no hotspot data from out here" guard.
        if _at_the_home(state) and pick_next_hotspot(state.get("hotspots") or []) is None:
            debug_log.log("mahogany_homes",
                          "low on supplies, but this contract is finished -- turning it in "
                          "before banking")
        else:
            debug_log.log("mahogany_homes", "running low mid-contract -- banking")
            phase = "bank"

    ctx.state.data["mh_phase"] = phase
    PHASE_HANDLERS[phase](ctx, config, state)


def _tier_matches(config: dict, state: dict) -> bool:
    """Global constraint: this routine only works Adept contracts. A contractTier of 0 means the
    provider could not parse a tier at all (e.g. an older client build) -- permissive there
    rather than deadlocking on a field that isn't being reported."""
    reported = state.get("contractTier") or 0
    if reported == 0:
        return True
    return reported == tier_number(config.get("tier", "adept"))
