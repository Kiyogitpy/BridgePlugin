# routines/golem_crafting.py
#
# Golem Crafting routine, overall loop (CORRECTED 2026-08-08, live session, user-specified):
#   bank (stock fur, pouched) -> mine a batch of sunstone -> chisel PART of it into cores ->
#   craft up to one golem per core (auto-consumes fur too) -> bank again only once the tracked
#   fur stock actually runs low, otherwise straight back to mining -- with a FUR TRIP (falconry at
#   Piscatoris, see fur_trip_phase) if the bank itself has no usable fur left at all.
#
# REPLACED 2026-09-06 (USER: "instead of doing goat hunting to get furs merge falconry as the
# method of furs"): the Wyrmscraig Goat Pit hunt (Telekinetic Grab, spikes, a pit to line and
# clear) is gone. Dashing kebbit fur is 2720 XP per golem against goat fur's lower value, and the
# wiki's own guide for this method is: pouch empty -> fairy ring AKQ -> catch kebbits -> teleport
# back. The measured goat-pit facts live in git history (see CLAUDE.md's golem_crafting section).
# Every wait in this file is event-driven since the same day -- see the constants block below.
#
# Fur is withdrawn in FULL and kept compacted in the fur pouch throughout -- mining, chiselling,
# AND crafting -- so it never competes with raw sunstone for inventory slots (CONFIRMED live this
# was a real problem: carrying 14+ loose fur while mining left too few slots to reach a full
# batch). REWRITTEN 2026-08-20: it is never unpacked at all -- Insert-core consumes its 1 fur
# straight from the pouch in the same click that consumes the core (see craft_phase's own
# docstring and this file's header note on Insert-core below). See _run_bank_trip's docstring for
# why it's withdrawn loose and compacted only after the bank closes.
#
# Full design and research notes: docs/superpowers/specs/2026-08-07-golem-crafting-design.md.
# Golem Crafting is a member-only Crafting method on Wyrmscraig, released 2026-07-29.
#
# CORRECTED 2026-08-08 (MCP live debug session, full end-to-end verification): the plinth/carve
# mechanic this file originally implemented was reverse-engineered from a live wiki fetch and
# turned out to be substantially wrong in several ways once actually tested in-game:
#   - The right-click option is "Start-golem" (first side) / "Shape-golem" (remaining sides),
#     never "Carve" -- and the object's own name changes as it progresses: "Empty plinth" ->
#     "Unfinished golem" (after the first side) -> "Unpowered golem" (after all 4 sides) -> back
#     to "Empty plinth" once a golem is completed and ready for the next one.
#   - Each of the 4 sides must be carved from a DIFFERENT adjacent tile around the object --
#     repeating the same tile/screen position (this file's original 4x-identical-click loop)
#     gets "You've already crafted this side of the golem." with no progress and no error
#     signal available synchronously (the confirmation chat message can arrive many ticks after
#     the click, since it's server-side action-queue-delayed -- see on_event below).
#   - Material cost is front-loaded: the FIRST side ("Start-golem") consumes 4 raw Sunstone, all
#     deducted on that one click -- CONFIRMED by the user 2026-08-20, correcting this comment's
#     earlier claim of 1 (which was self-flagged as n=1 and was wrong). The remaining 3 sides
#     ("Shape-golem") consume nothing further. A golem therefore costs 4 raw sunstone + 1 core,
#     and since a core is 1 raw sunstone chiselled 1-for-1, 5 sunstone-equivalents in total --
#     which is why SUNSTONE_PER_TRIP (20) and CORES_PER_TRIP (4) balance to exactly 4 golems.
#     Note this means abandoning a half-carved golem destroys 4 sunstone.
#   - The finishing move is "Insert-core", which auto-consumes both 1 Sunstone core AND the best
#     available fur (straight from the fur pouch) in one click, and awards Crafting XP. It is
#     accepted from exactly ONE side per plinth, fixed: the northern plinth (object 62351) takes
#     it from its south side, the southern one (62352) from its north. This is no longer
#     discovered by trial and error -- GolemProvider reports it as `insertSide` on GET /golem.
#   - Which sides are already carved is read from per-side game state variables via GET /golem,
#     NOT tracked locally. See
#     docs/superpowers/specs/2026-08-20-golem-crafting-varbit-overhaul-design.md.
#   - The gem a finished golem awards goes straight into the gem bag; nothing is left on the
#     ground unless the bag is full (see GOLEM_LOOT_GROUND_MARKER).
# REWRITTEN 2026-08-20 to read GET /golem's own state variables directly instead of modeling any
# of this by hand -- see craft_phase's own docstring and docs/superpowers/specs/2026-08-20-golem-
# crafting-varbit-overhaul-design.md for the current mechanic.
#
# Requirements assumed already met (out of scope for this routine, see the design's Scope):
# Crafting 60, "Fallen From Grace" quest complete, already own a fur pouch (any size).
#
# Deliberately NOT implemented: the XP-drop tick-timing speed-up trick for plinth carving
# (always uses the safe, un-optimized cadence); acquiring a fur pouch; sourcing fur from
# anywhere but the bank or a falconry trip.
#
# Run with:
#     python controller.py routines/golem_crafting.py

import math
import time

import banker
import controller
import debug_log
import outcome_waits
import travel

# routines/falconry.py supplies the Piscatoris travel legs and the hunting primitives the fur trip
# reuses. controller.py loads a routine by file path, so its siblings are reachable either as a
# bare module (routines/ on sys.path, the tests' arrangement) or through the routines package.
try:
    import falconry
except ImportError:  # pragma: no cover
    from routines import falconry

# Highest fur-quality first (routines/golem_crafting.py-design.md's "Fur table"). A pure module
# constant, not read from bridge config -- ConfigExporter.exportGolemCrafting deliberately
# doesn't export this list (the player never chooses a fur, same reasoning
# BlastFurnaceBarType's BAR_TYPES table isn't Java-exposed either, see the design's section 3).
# Keeping this a constant that every phase reads directly (rather than config.get("furPriority"))
# means behavior is identical whether get_config() below returns the live bridge config (which
# has no "furPriority" key at all) or DEFAULT_CONFIG.
FUR_PRIORITY = [
    "Moonlight antelope fur", "Sunlight antelope fur", "Dashing kebbit fur",
    "Kyatt fur", "Dark kebbit fur", "Fox fur", "Spotted kebbit fur",
    "Graahk fur", "Larupia fur", "Feldip weasel fur", "Common kebbit fur",
    "Desert devil fur", "Polar kebbit fur", "Wyrmscraig goat fur",
]

# Same reasoning as FUR_PRIORITY above -- ConfigExporter.exportGolemCrafting doesn't export
# maxObjectDistance either, so every phase's find_object() call uses this constant directly
# rather than config.get("maxObjectDistance", ...), which would silently fall back to this same
# value anyway whenever the live bridge config is in effect.
MAX_OBJECT_DISTANCE = 15

# A single empty get_objects()/get_npcs() poll can just mean a transient bridge timeout (see
# banker.py's find_nearby_bank docstring for the documented live incident this guards against),
# not "genuinely not there" -- tolerate a few consecutive misses before handle_missing_target
# below gives up and lets the caller safety-stop. CORRECTED 2026-08-08 (final whole-branch
# review): mine_phase/craft_phase/hunt_phase used to hard safety-stop on the very first empty
# poll, which could end a whole session on one slow /objects or /npcs call.
MAX_CONSECUTIVE_MISSES = 5

CRAFTING_LEVEL_REQUIRED = 60

# --- Event-driven outcomes (2026-09-06, docs/superpowers/specs/2026-09-06-golem-crafting-events-
# and-falconry-design.md). The golem varbits GolemProvider reads (runelite-api 1.12.38 VarbitID,
# javap-verified): a side's state varbit flips when it is carved, its progress varbit climbs while
# it carves, the station varbit is the plinth's overall progress, GOLEM_CRAFTING_COUNT the total.
# Subscribed so each is pushed as VARBIT_CHANGED; every await below still confirms on /golem.
VB_STATION = {"north": 15728, "south": 15733}
VB_SIDE_STATE = {
    "north": {"north": 15729, "east": 15730, "south": 15731, "west": 15732},
    "south": {"north": 15734, "east": 15735, "south": 15736, "west": 15737},
}
VB_SIDE_PROGRESS = {
    "north": {"north": 15739, "east": 15740, "south": 15741, "west": 15742},
    "south": {"north": 15743, "east": 15744, "south": 15745, "west": 15746},
}
VB_CRAFTING_COUNT = 15738
VB_FALCON_HAS_CATCH = 14192     # falconry.VARBIT_FALCON_HAS_CATCH, for the fur trip
VARBIT_SUBSCRIPTIONS = (
    tuple(VB_STATION.values())
    + tuple(v for sides in VB_SIDE_STATE.values() for v in sides.values())
    + tuple(v for sides in VB_SIDE_PROGRESS.values() for v in sides.values())
    + (VB_CRAFTING_COUNT, VB_FALCON_HAS_CATCH)
)
# How long an event-driven await blocks for a pushed signal before re-reading HTTP anyway (UDP
# is lossy): the click echo and the golem confirms keep their old poll cadence as the ceiling,
# so a lost packet costs the same latency it did before events existed, never more.
EVENT_SLICE_S = 0.3

# Stall watch (2026-09-06, same shape as mastering_mixology/GOTR): once per tick() an unchanged
# progress signature for STALL_WARN_S logs a `[!]` line, for STALL_STOP_S safety-stops. The
# longest legitimate silence is a walk leg of the fur trip, which moves the player -- and the
# signature includes the player's tile, so walking counts as progress.
STALL_WARN_S = 120.0
STALL_STOP_S = 300.0
# Phases whose handler may run the NEXT phase inside the same tick() once it flips gc_phase.
# chisel -> craft and bank -> mine/fur_trip used to cost an engine-loop iteration each with the
# player standing still. mine and craft are one bounded action per call BY DESIGN (the engine's
# safety/break/PvP gates keep their cadence between swings and carves) and never chain.
TICK_CHAIN_FROM = ("chisel", "bank")
TICK_CHAIN_MAX = 2


def _events_of(ctx):
    return getattr(ctx, "events", None) if ctx is not None else None


def _await_player_stopped(ctx, timeout_s: float) -> bool:
    """True once the player is standing still, within `timeout_s`.

    2026-09-06: PLAYER_STOPPED is pushed per frame by the bridge's motion tracker and /player
    carries the same tracker's `moving` flag, so with both available this is an event-driven
    await confirmed on that flag. A client whose /player has no `moving` key (pre-2026-09-04
    build) falls back to controller.wait_until_player_idle, the local-position poll the
    pre-hover shipped with -- same result, just 0.15s-quantised."""
    events = _events_of(ctx)
    player = controller.get_player() or {}
    if events is None or "moving" not in player:
        return controller.wait_until_player_idle(timeout_s=timeout_s)
    if player.get("moving") is False:
        return True
    out = outcome_waits.await_pre(
        ctx, "PLAYER_STOPPED", timeout_s,
        confirm=lambda: (controller.get_player() or {}).get("moving") is False,
        reaction=False, now_fn=time.time, sleep_fn=time.sleep,
    )
    return bool(out)


def _wait_slice(events, since, signals, slice_s):
    """Block up to `slice_s` for a signal newer than `since` (an EventStream mark), then hand
    back the new mark. With no stream: sleep the slice, return None. The caller re-reads HTTP
    after every slice regardless -- the signal only makes that read happen SOONER."""
    if events is None:
        time.sleep(slice_s)
        return None
    events.await_outcome(signals, slice_s, confirm=None, since=since, reaction=False,
                         now_fn=time.time, sleep_fn=time.sleep)
    return events.mark()

# CORRECTED 2026-08-08 (live session, user request): mine_phase used to target the ~22
# "Sunstone rocks" spots scattered around the monolith, deliberately switching between whichever
# ones were nearest each swing (a "momentum" mechanic). CONFIRMED live this constantly sent the
# character running between rocks several tiles apart, re-targeting yet another rock before the
# current walk/swing had even resolved (get_combat's isIdle races) -- "running to a node it just
# clicked another node". This was first fixed by dropping to the monolith alone (id 62216, single
# fixed-position instance -- see the "mining" anchor comment above), which sidesteps the bug
# entirely rather than patching the race, at the cost of being the only mineable spot.
#
# CORRECTED 2026-08-08 (live session, user report): the monolith alone mines noticeably slower --
# CONFIRMED live-observed -- so this switched to "Sunstone rocks" instead (see
# find_mining_target below), WITHOUT reviving the momentum-switching that caused the original
# bug: mine_phase always targets whichever single rock is closest and just keeps re-clicking it,
# the same one-target-at-a-time idiom mining.py itself uses. It only ever moves on to a different
# rock once the current one is no longer the closest match returned (i.e. genuinely depleted or
# out of range), never proactively mid-approach -- the switching itself was the bug, not the
# existence of multiple rock spots.
#
# CONFIRMED 2026-08-24 (user's own play, two recorded sessions + user directive): Sunstone's
# in-game "momentum" mechanic means mining speed itself depends on click cadence -- a human who
# reclicks within the same ~1.5-2s window the swing actually resolves in stays fast (occasional
# bonus-ore procs, "Your momentum allows you to break off another chunk of sunstone."); any
# hesitation between swings costs real momentum, not just wall-clock time. The routine's own
# recorded cadence was measured at roughly 2x the human's (~3.5-4s/rock vs ~1.6s/rock) even
# though find_mining_target always had a target ready -- so the lost time was overwhelmingly
# click_entity's OWN move-then-click travel, paid AFTER the swing was already confirmed done,
# not detection latency. The user's own technique, confirmed against the recordings: stand where
# 4 "sunstone rocks" tiles are all in reach (this spot's 4 respawn in a fixed, deterministic
# ~3-tick round-robin -- see recordings/2026-08-24_154518_golem-mining2-and-chisel.jsonl's
# objspawn sequence), and pre-hover the mouse on the NEXT ready rock while the current swing is
# still resolving, so only a click -- not a move-then-click -- has to fire once confirmation
# arrives. See mine_phase's own pre-hover step below, built on the existing
# controller.hover_entity primitive (already used this exact way by
# routines/mastering_mixology.py's speedup functions).
MINING_TARGET_NAMES = ("sunstone rocks",)

# CONFIRMED LIVE 2026-08-08 (MCP debug session): exact inventory item names for the raw ore, the
# chiselled core, and the tool used to turn one into the other.
RAW_SUNSTONE_NAME = "Sunstone"
SUNSTONE_CORE_NAME = "Sunstone core"
CHISEL_NAME = "Chisel"
# ADDED 2026-08-08 (user report): a better chisel -- CONFIRMED live preferred over the plain
# Chisel whenever both are owned, falling back to CHISEL_NAME only if this one isn't carried.
# BANK_KEEP_ITEMS below already protects it without needing its own entry there -- deposit_
# inventory's keepItems match is substring-based (see banker.deposit_inventory's own comment),
# and "chisel" (CHISEL_NAME.lower(), already in that list) is itself a substring of this name.
JEWELLERS_CHISEL_NAME = "Jeweller's chisel"

# Used only if a Controller plugin build predating this routine's wiring is running (so
# GET /golem-crafting-config 404s and get_golem_crafting_config() returns None) -- keeps this
# routine runnable standalone, same as every other routine's DEFAULT_CONFIG fallback.
DEFAULT_CONFIG = {
    "enabled": True,
    # Kept here for documentation/standalone completeness -- see the FUR_PRIORITY/
    # MAX_OBJECT_DISTANCE constants above for why the actual phase code doesn't read these two
    # keys out of config.
    "furPriority": FUR_PRIORITY,
    "maxObjectDistance": MAX_OBJECT_DISTANCE,

    "bank": {"teleportFallback": False},

    # The falconry fur trip (see fur_trip_phase). hireCoins: Quick-falcon is 500gp per falcon and
    # a lost falcon needs a re-hire. tripMaxMinutes: the safety cap on a trip whose pouch never
    # fills (a crowded world). returnTeleport: "slayer ring" (lands in the Wyrmscraig Cavern,
    # exit via the Cave object) or "necklace of passage" (lands on the surface east of the
    # cathedral). dropMeat: no meat pouch is carried, so raw kebbit would fill the inventory.
    "furTrip": {"hireCoins": 2000, "tripMaxMinutes": 25, "returnTeleport": "slayer ring",
                "dropMeat": True},

    # World-tile anchors ({"x", "y", "plane"}, same shape as travel.save_location()'s output)
    # near each area -- this is what lets handle_missing_target (see below) walk the player back
    # to an area on its own whenever the relevant object isn't found nearby, e.g. after banking,
    # instead of just retrying a few times and safety-stopping. CONFIRMED LIVE 2026-08-08 (MCP
    # debug session) -- real Wyrmscraig coordinates, not guesses:
    #   mining: the sunstone monolith (id 62216) -- a single fixed-position instance, unlike the
    #     many "Sunstone rocks" spots, so it's a stable anchor regardless of which rocks happen
    #     to be depleted/others-in-use when the routine arrives.
    #   plinth: one of Wyrmscraig's two Empty plinths (id 62352); the other (id 62351, ~4 tiles
    #     away at 2596,2257) works just as well -- picked arbitrarily.
    #   cavernExit: the Wyrmscraig Cavern's "Cave"/Exit object (loc 62220), where the slayer
    #     ring's return leg has to walk to. UNKNOWN until the first live trip (not in the offline
    #     spawn data); fur_trip_phase records it in ctx.state.data the first time it is seen.
    #   bank: the Bank chest (id 62390) near the mining/plinth cluster -- ADDED because the
    #     Goat Pit sits ~65 tiles from it, well outside banker.find_nearby_bank's default
    #     40-tile radius, so a hunt trip needed an anchor to actually get back to it. Safe to
    #     hardcode: Wyrmscraig has exactly one bank (per the design doc's own "Key facts" --
    #     unlike Ferox/most other banking spots, there's no ambiguity about which one this is).
    "locations": {
        "mining": {"x": 2599, "y": 2248, "plane": 0},
        "plinth": {"x": 2595, "y": 2253, "plane": 0},
        "cavernExit": None,
        "bank": {"x": 2587, "y": 2260, "plane": 0},
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
    config = controller.get_golem_crafting_config()
    if config is None:
        return DEFAULT_CONFIG

    # locations isn't part of ConfigExporter.exportGolemCrafting's output (see the "locations"
    # comment in DEFAULT_CONFIG above) -- CORRECTED 2026-08-08 (final whole-branch review): the
    # live bridge config used to be returned as-is, silently dropping this key whenever the
    # Controller plugin is running, which made handle_missing_target's location-anchor walking
    # permanently unreachable in the actual deployed configuration. Same fix shape as
    # tormented_demon.py's get_config re-injecting DEFAULT_NPC_IDS.
    config = dict(config)
    config["locations"] = DEFAULT_CONFIG["locations"]
    # Same reasoning: the fur-trip block is not exported by the Java panel either.
    config.setdefault("furTrip", DEFAULT_CONFIG["furTrip"])
    # The fur trip fills the inventory by design (bones, meat, spilled fur) and check_safety runs
    # BEFORE tick(), so the engine's full-inventory stop would win the race -- same as falconry.py.
    safety = dict(config.get("safety") or {})
    safety["stopOnInventoryFull"] = False
    config["safety"] = safety
    return config


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check, run from inside tick() (never setup()
    -- controller.py calls routine.setup(ctx) at routine-load time, BEFORE the
    login.is_fully_logged_in() gate, so /skills could be queried pre-login and false-trigger a
    permanent safety stop on an account that's actually fine once logged in). Gated by a
    ctx.state.data flag so it runs exactly once per routine-activation -- state.data is reset to
    {} by controller.py whenever the active routine file changes. Same shape as
    blast_furnace.py's/firemaking.py's own check_level_requirement."""
    crafting = controller.get_skills().get("Crafting") or {}
    level = crafting.get("level")
    if level is not None and level < CRAFTING_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Crafting level {level} is below the {CRAFTING_LEVEL_REQUIRED} required for Golem Crafting"
        )


def find_item(name: str):
    """First inventory item whose name matches `name` exactly (case-insensitive) -- exact
    rather than substring match, same reasoning as blast_furnace.py's find_item: a substring
    match here would wrongly match "Sunstone" against "Sunstone core" (and vice versa isn't a
    risk since RAW_SUNSTONE_NAME is a prefix of SUNSTONE_CORE_NAME, but an exact match makes
    both lookups unambiguous either way)."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
        None,
    )


# The four sides in clockwise order. Index arithmetic below depends on this ordering.
SIDES = ("north", "east", "south", "west")

# CONFIRMED by the user 2026-08-20: "Start-golem" deducts 4 raw sunstone on that first click,
# not 1 -- this module's header comment previously claimed 1 (self-flagged there as n=1) and was
# wrong. Matches the reference plugin's own hasGolemMaterials() gate (sunstoneCount < 4 blocks a
# start). Each golem also needs 1 core, itself 1 raw sunstone chiselled 1-for-1, so a golem
# costs 5 sunstone-equivalents and SUNSTONE_PER_TRIP=20 / CORES_PER_TRIP=4 balances exactly.
SUNSTONE_PER_GOLEM = 4
CORES_PER_GOLEM = 1


def side_order(start: str, insert_side: str) -> list:
    """All four sides from `start`, rotating in whichever direction reaches `insert_side` last.

    Direction rule is the reference plugin's isClockwise: clockwise when the clockwise distance
    to the insert side is at least the counter-clockwise one. Note this returns all four sides
    regardless of whether they are carved -- carve_plan does the filtering."""
    start_index = SIDES.index(start)
    insert_index = SIDES.index(insert_side)
    clockwise = (insert_index - start_index) % 4
    counter = (start_index - insert_index) % 4
    step = 1 if clockwise >= counter else 3
    return [SIDES[(start_index + i * step) % 4] for i in range(4)]


def can_end_on_insert(start: str, insert_side: str) -> bool:
    """True iff a full four-side rotation from `start` finishes on `insert_side`.

    Four steps from `start` land on `start + 3` (clockwise) or `start + 1` (counter-clockwise),
    so this holds only when `start` is one of `insert_side`'s two neighbours -- starting ON the
    insert side, or opposite it, makes ending there impossible. Both plinths insert from a
    north/south side, so east and west are the only valid starts for either. That is exactly why
    the reference plugin's StartDirection enum contains only EAST and WEST; the restriction reads
    as arbitrary until this is derived."""
    return side_order(start, insert_side)[-1] == insert_side


# Consecutive failures on one plinth's start side before preferring the other one. Two, not one:
# a single failure is ordinary (a deliberate misclick, a player wandering through the aim point),
# and switching on every miss would just thrash between the two sides.
SIDE_AVOID_THRESHOLD = 2


def _sides_to_avoid(fail_counts: dict, station_name: str) -> set:
    """Sides of `station_name` that have failed enough times in a row to be worth stepping away
    from. Keyed per plinth: the two have independent geometry, so a bad angle on one says nothing
    about the other."""
    prefix = f"{station_name}:"
    return {key[len(prefix):] for key, count in (fail_counts or {}).items()
            if key.startswith(prefix) and count >= SIDE_AVOID_THRESHOLD}


def choose_start_side(station: dict, player_x: int, player_y: int, avoid=()) -> str:
    """Whichever valid start side (east/west, see can_end_on_insert) is nearer to the player,
    preferring one not in `avoid`.

    Both produce a correct four-side order, so the choice is free -- making it adaptive is what
    keeps plinth alternation cheap: arriving from the other plinth, carving begins on the near
    side instead of walking around the golem first.

    `avoid` added 2026-08-24 after THREE live halts with one shape: "starting golem from east side
    of south plinth" at progress 0, five failures, safety stop (21:04, 22:04, 02:00). Frozen at the
    third: our player on the east tile, /object/at reporting the plinth clickable:true with a
    clickbox at screen (1158,771), and a right-click there returning only ground options
    (['Cancel','Mark Safespot','Examine','Walk here']). The reported clickbox CENTRE is simply not
    on the clickable shape from that angle, so every tier of the escalation chain re-aims at the
    same unusable pixel. No retry strategy fixes a bad aim point -- standing somewhere else does,
    and on a fresh golem this choice is free. Never returns None: if both sides are avoided,
    carving from a bad one still beats not carving, and the ordinary miss limits stay the
    backstop."""
    candidates = [s for s in SIDES if can_end_on_insert(s, station["insertSide"])]
    preferred = [s for s in candidates if s not in avoid]
    if preferred:
        candidates = preferred
    return min(
        candidates,
        key=lambda s: max(
            abs(station["sides"][s]["x"] - player_x),
            abs(station["sides"][s]["y"] - player_y),
        ),
    )


def carve_plan(station: dict, player_x: int, player_y: int, avoid=()) -> list:
    """The sides still needing carving, in the order to carve them.

    Reading `done` from the varbits is what makes a resumed run correct for free: whatever the
    game says is uncarved is what gets carved, with no dependence on ctx.state.data surviving a
    restart. On a resumed golem whose insert side is already carved the plan will NOT end on the
    insert side -- that is legal, and craft_phase walks there afterwards. Ending on the insert
    side is a best-effort saving on fresh golems, never a correctness requirement."""
    order = side_order(choose_start_side(station, player_x, player_y, avoid=avoid),
                       station["insertSide"])
    return [side for side in order if not station["sides"][side]["done"]]


# How long to leave a plinth alone after it refuses to start a golem, and how many consecutive
# back-offs (with no golem completing in between) before treating it as a real anomaly. SEVEN
# hypotheses for that refusal were killed by measurement -- see CLAUDE.md's "what has been RULED
# OUT" -- so the cause is unknown, but every occurrence recovered on a plain restart, which means
# it is transient. Backing off lets the routine discover that on its own instead of ending an
# unattended session; the limit stops the back-off becoming its own infinite mine<->craft loop.
STATION_BLOCK_S = 150.0
STATION_BLOCK_LIMIT = 6


def _block_station(ctx, station: dict) -> None:
    """Mark a plinth unusable for STATION_BLOCK_S and send the routine off to mine meanwhile.
    Safety-stops only if this keeps happening with nothing completing in between."""
    blocked = dict(ctx.state.data.get("gc_blocked_stations") or {})
    blocked[station["name"]] = time.time() + STATION_BLOCK_S
    ctx.state.data["gc_blocked_stations"] = blocked
    ctx.state.data["gc_side_misses"] = 0
    ctx.state.data["gc_phase"] = "mine"
    streak = ctx.state.data.get("gc_block_streak", 0) + 1
    ctx.state.data["gc_block_streak"] = streak
    debug_log.log(
        "golem crafting",
        f"{station['name']} plinth refused to start a golem -- backing off for "
        f"{STATION_BLOCK_S:.0f}s and mining meanwhile ({streak}/{STATION_BLOCK_LIMIT})",
    )
    if streak >= STATION_BLOCK_LIMIT:
        ctx.state.trigger_safety_stop(
            f"both plinths refused to start a golem {STATION_BLOCK_LIMIT} times in a row with "
            f"nothing completing in between; station state: {station}"
        )


def _blocked_station_names(ctx) -> set:
    now = time.time()
    return {name for name, until in (ctx.state.data.get("gc_blocked_stations") or {}).items()
            if until > now}


def choose_station(stations: list, last_station, player_x: int, player_y: int, blocked=()):
    """Which plinth to work on next, or None if `stations` is empty.

    Finishing an in-progress golem takes priority, and the more advanced of two in-progress ones
    goes first -- its 4 sunstone are already spent and unrecoverable, so the shorter the window
    before it completes, the less a crash can strand. When neither is in progress, alternate away
    from the just-completed plinth (see the routine's alternation note) rather than waiting out
    its reset. `last_station` being None -- a fresh start or a restart -- needs no reconciliation:
    either plinth is correct, so fall through to whichever is nearer."""
    if not stations:
        return None
    # A blocked plinth is skipped -- unless every one is blocked, in which case carving from a
    # difficult plinth still beats not carving, and the miss limits remain the backstop.
    unblocked = [s for s in stations if s.get("name") not in blocked]
    if unblocked:
        stations = unblocked
    in_progress = [s for s in stations if s.get("progress", 0) > 0]
    if in_progress:
        return max(in_progress, key=lambda s: s.get("progress", 0))
    if last_station is not None:
        others = [s for s in stations if s.get("name") != last_station]
        if others:
            return others[0]
    return min(stations, key=lambda s: max(abs(s["x"] - player_x), abs(s["y"] - player_y)))


def has_start_materials(raw_sunstone: int, cores: int) -> bool:
    """Whether a NEW golem can be started. Counts, not mere existence -- see SUNSTONE_PER_GOLEM:
    holding 1-3 raw sunstone passed the old existence check, fired a doomed "Start-golem" and
    burned a full confirmation cycle before the rejection routed to mine."""
    return raw_sunstone >= SUNSTONE_PER_GOLEM and cores >= CORES_PER_GOLEM


def find_object(name: str):
    """Closest visible object whose name contains `name`, within MAX_OBJECT_DISTANCE -- same
    filter shape as blast_furnace.py's find_object/mining.py's pick_rock/banker.py's
    find_nearby_bank, kept local per this codebase's established convention.

    CORRECTED 2026-08-08 (MCP live debug session): passes radius=MAX_OBJECT_DISTANCE to
    controller.get_objects() -- CONFIRMED live this file's object lookups were doing a full,
    unbounded scene walk (ObjectProvider.java's own module comment already documents this as a
    measurable source of client stutter, which is exactly why the radius param exists) on every
    single call, several times a tick, and the resulting slowness was enough to blow api_get's
    default 0.3s timeout on /objects every tick -- every phase's object lookup (find_object,
    find_mining_target, and a golem-station lookup since replaced by GET /golem -- see this
    module's header comment) was silently retrying past this on every call, never actually
    finding anything, "wrong location?" safety-stops firing even while standing right next to
    the target.
    Every caller here already filters by this same distance bound afterward, so passing it
    through changes nothing about the result, only how much of the scene has to be walked to
    produce it."""
    name_lower = name.lower()
    candidates = [
        o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
        if name_lower in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def find_mining_target(committed_pos=None, exclude_pos=None):
    """A single object in MINING_TARGET_NAMES ("Sunstone rocks") to click -- sticky, not just
    "whichever is closest right now": if `committed_pos` (an (x, y) world tile) is given and
    still among the visible candidates, THAT one is returned regardless of whether something
    else is now nearer, only falling back to the closest candidate otherwise. Returns None if
    nothing is in range at all.

    `exclude_pos`, if given, drops that exact (x, y) tile from consideration even if it is still
    reporting as a ready "sunstone rocks" object -- ADDED 2026-08-24 for mine_phase's pre-hover
    step (see that function's own comment): right after clicking a rock, its own depletion has
    not necessarily propagated to the bridge's object list yet, so picking "the next rock to
    hover" without excluding the one just clicked can hand back the SAME tile.

    CORRECTED 2026-08-08 (live session, user report, second pass): the first version of this
    always returned the plain closest match, re-derived fresh on every call -- CONFIRMED live
    this reintroduced the exact "running to a node it just clicked another node" bug the
    monolith-only detour existed to sidestep in the first place: while still walking toward a
    rock several tiles away, the player's own movement changes which OTHER rock is nearest from
    tick to tick, and since nothing tracked which one was already committed to, mine_phase would
    happily redirect toward whichever became closest, abandoning the walk in progress every time.
    Sticking to `committed_pos` until it's actually gone (mined out, or no longer in range) means
    a walk in progress is never abandoned just because something else happened to become
    nearer -- mine_phase only moves on once the rock it was already going for is confirmed gone."""
    candidates = [
        o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
        if (o.get("name") or "").lower() in MINING_TARGET_NAMES
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
        and (exclude_pos is None or (o.get("x"), o.get("y")) != exclude_pos)
    ]
    if not candidates:
        return None

    if committed_pos is not None:
        current = next((o for o in candidates if (o.get("x"), o.get("y")) == committed_pos), None)
        if current is not None:
            return current

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


# A location anchor is a best-effort hint, not a guarantee the target is actually there once
# arrived (e.g. one of MINING_TARGET_NAMES/GOAT_PIT_NAME turning out to be a wrong
# guess, or the anchor coordinates themselves being off) -- give up after this many walk attempts
# rather than walking-and-retrying forever. ADDED 2026-08-08 (final whole-branch review, second
# pass): the first version of handle_missing_target below had no cap on this path at all.
#
# CORRECTED 2026-08-08 (live session): bumped from 3 -- CONFIRMED live walking to the bank
# anchor from ~65 tiles away, the walk occasionally declares "arrived" (within
# WALK_ARRIVAL_TOLERANCE) a bit short of where find_nearby_bank can actually detect the bank
# object, costing one wasted attempt with no real progress. 3 wasn't always enough headroom to
# still land a couple of genuinely-progressing walks after an unlucky one; this is a blunt
# mitigation, not a fix for travel.py's own walking algorithm (out of this routine's scope).
#
# NOTE (global-collision-pathfinding migration): this comment used to also cite a second cause --
# a retried Shortest Path request getting rejected ("Shortest Path unavailable") if the
# separately-installed Shortest Path plugin still considered the previous request active.
# That plugin, and its concurrent-request rejection, are both gone: travel.walk_to now routes off
# worldmap.py's own offline BFS over a pre-baked collision map, which has no "still busy" state
# to reject a retry against. The arrival-tolerance mismatch above is the one measured cause that
# still applies today, and is reason enough on its own to keep this headroom.
MAX_WALK_ATTEMPTS = 6

# ADDED 2026-08-08 (user request): a bank found by find_nearby_bank within its 40-tile radius
# but too far off-screen to click directly (see open_bank's caller in bank_phase) gets a few
# direct minimap-click hops straight at its own real position first, same trick travel.py's
# own _hop_to uses for a route hop beyond render distance -- CONFIRMED that idiom already works
# for cross-country travel, and it's a cheaper, more targeted fix here than immediately handing
# off to handle_missing_target's full walk (travel.walk_to, routed off worldmap.py's own BFS over
# a pre-baked collision map -- not the old, now-deleted Shortest Path plugin this comment used to
# name) toward the (possibly-approximate) "bank" anchor tile: clicking the bank object's actual
# coordinates walks straight at the real thing. Capped, same reasoning as MAX_WALK_ATTEMPTS above
# -- a bank that never gets closer after a few hops (e.g. genuinely unreachable, or the click
# isn't landing) should fall through to the anchor-walk fallback instead of hopping at the same
# spot forever.
MAX_BANK_MINIMAP_HOPS = 3

# How far to search for a genuinely walkable substitute tile when a configured location anchor
# turns out to sit on a blocked tile (inside/on an object) -- ADDED 2026-08-09 (user request,
# live report: a walk toward the "mining" anchor got stuck retrying a route to an unwalkable
# tile for 2.5+ minutes). 8 tiles comfortably covers an anchor that's just slightly off (e.g.
# centered on the object it's meant to stand next to) without wandering far enough to end up
# somewhere no longer useful for whatever the anchor was for.
WALKABLE_ANCHOR_SEARCH_RADIUS = 8


def _nearest_walkable_tile(target: dict) -> dict:
    """Return `target` unchanged if it's already walkable, otherwise the closest walkable tile
    within WALKABLE_ANCHOR_SEARCH_RADIUS tiles on the same plane -- or `target` itself if no
    walkable tile was found nearby (the caller's walk just fails the same way it already could
    have, no worse off than before this existed). CONFIRMED live the "mining" anchor
    (2599, 2248) sits directly on a blocked tile; travel.walk_to has no way to know that on its
    own and will retry a route to an unreachable tile forever, which is exactly the failure
    mode this exists to route around at the source instead of inside travel.py (shared
    infrastructure well outside this routine's scope)."""
    tx, ty, plane = target["x"], target["y"], target.get("plane", 0)
    tiles = controller.get_tiles()
    exact = next((t for t in tiles if t.get("x") == tx and t.get("y") == ty and t.get("plane") == plane), None)
    if exact is not None and exact.get("walkable"):
        return target

    candidates = [
        t for t in tiles
        if t.get("plane") == plane and t.get("walkable")
        and abs(t.get("x", 0) - tx) <= WALKABLE_ANCHOR_SEARCH_RADIUS
        and abs(t.get("y", 0) - ty) <= WALKABLE_ANCHOR_SEARCH_RADIUS
    ]
    if not candidates:
        return target

    candidates.sort(key=lambda t: (t["x"] - tx) ** 2 + (t["y"] - ty) ** 2)
    nearest = candidates[0]
    return {"x": nearest["x"], "y": nearest["y"], "plane": plane}


def handle_missing_target(ctx, config: dict, location_key: str, miss_counter_key: str, walk_counter_key: str) -> bool:
    """Shared not-found handling for mine_phase/craft_phase/hunt_phase's object lookups. Returns
    True if the caller should just return this tick and let the next tick retry; False once
    genuinely exhausted, meaning the caller should safety-stop itself with its own specific
    message (this function never calls trigger_safety_stop directly).

    ADDED 2026-08-08 (final whole-branch review). Two layers, tried in order:
      1. Tolerate up to MAX_CONSECUTIVE_MISSES consecutive empty polls before doing anything else
         -- a single empty poll can just mean a transient bridge timeout, or (CONFIRMED live
         2026-08-08, live session, user-observed) a brief real gap in the scene right after an
         object changes state (e.g. craft_phase's own Start-golem click: the "Empty plinth"
         despawning and the new "Unfinished golem" spawning aren't guaranteed atomic from the
         bridge's point of view, so a poll landing in between can briefly see neither). Either
         way it's not "genuinely not there", and CORRECTED 2026-08-08 (live session, user report:
         two live safety-stops in a row from exactly this) -- walking immediately on the very
         first miss, as this used to for any location with a configured anchor, actively made it
         worse here: the player was already standing right at the plinth, so travel.walk_to's own
         hop/camera movement while "recovering" from a gap that would have closed on its own
         within a poll or two instead kept the object out of view for the whole 6-attempt budget.
      2. Once that tolerance is exhausted, if a location anchor is configured for `location_key`
         (DEFAULT_CONFIG["locations"]), walk toward it via travel.walk_to and retry, up to
         MAX_WALK_ATTEMPTS times -- resets the miss counter each time so a target that's still
         missing after arriving gets its own fresh run of misses, not one inherited from before
         the walk. Once MAX_WALK_ATTEMPTS is exhausted, gives up (returns False) rather than
         walking forever -- CORRECTED 2026-08-08 (final whole-branch review, second pass): this
         path originally had no exit at all, so a configured anchor whose target genuinely isn't
         there (e.g. a wrong object name, or the anchor coordinates being off) walked-and-retried
         indefinitely with no safety-stop. With no anchor configured at all, exhausting the poll
         tolerance above is the end of the line -- straight to False.

    Callers are responsible for resetting both `ctx.state.data[miss_counter_key]` and
    `ctx.state.data[walk_counter_key]` to 0 once the target IS found again, so a later,
    unrelated miss doesn't inherit a stale near-threshold count from an earlier search."""
    misses = ctx.state.data.get(miss_counter_key, 0) + 1
    ctx.state.data[miss_counter_key] = misses
    if misses < MAX_CONSECUTIVE_MISSES:
        debug_log.log("golem crafting", f"{location_key} not found (miss {misses}/{MAX_CONSECUTIVE_MISSES}) -- retrying")
        return True

    target = (config.get("locations") or {}).get(location_key)
    if target is None:
        return False

    walks = ctx.state.data.get(walk_counter_key, 0) + 1
    ctx.state.data[walk_counter_key] = walks
    if walks <= MAX_WALK_ATTEMPTS:
        debug_log.log("golem crafting", f"{location_key} not found nearby -- walking toward the configured location (attempt {walks}/{MAX_WALK_ATTEMPTS})")
        # ADDED 2026-08-09 (user report: a walk toward "mining" got stuck for 2.5+ minutes,
        # repeated identical clicks with zero movement): CONFIRMED live the configured (2599,
        # 2248) anchor tile itself is blocked/unwalkable (sits inside/on an object) -- travel.
        # walk_to has no way to know that and just kept retrying a route to a tile that can
        # never actually be reached. Substituting the closest genuinely walkable tile within
        # WALKABLE_ANCHOR_SEARCH_RADIUS fixes the anchor itself rather than special-casing the
        # walk logic (travel.py is shared infrastructure well outside this routine's scope).
        travel.walk_to(ctx, _nearest_walkable_tile(target), goal_adjacent=False)
        ctx.state.data[miss_counter_key] = 0
        return True
    debug_log.log("golem crafting", f"{location_key} still not found after {MAX_WALK_ATTEMPTS} walk attempts toward the configured location")
    return False


# How much raw+chiselled sunstone to hold per trip, and how many of those become cores --
# CORRECTED 2026-08-08 (live session, user-specified batch shape): mine 20, chisel 4 of them into
# cores, leaving 16 raw sunstone. CORRECTED 2026-08-20: at the confirmed cost of 4 raw sunstone
# per "Start-golem" (see SUNSTONE_PER_GOLEM above), 16 raw covers exactly 4 golems with ZERO
# reserve, not "plenty" as this comment previously (and wrongly) claimed -- 4 golems x 4 raw each
# x 4 cores = 20 sunstone-equivalents in, 20 out, no slack either way. This is exact, not
# generous, which is worth recording so these two constants aren't "fixed" later on the mistaken
# belief they're padded. This is a budget on raw_count + cores_have TOGETHER, not on raw sunstone
# alone -- see mine_phase's docstring for why that distinction is what fixes the overmining bug.
SUNSTONE_PER_TRIP = 20

# How long mine_phase will wait for raw_count to rise after a click before giving up on it and
# clicking again -- see mine_phase's own comment on its inventory-change gate for why a bound is
# needed at all (a click that lands nowhere useful, e.g. click_entity's own simulated near-miss,
# would otherwise wait for an inventory change that's never coming). Long enough to cover a
# genuine walk-and-swing to a distant rock (MAX_OBJECT_DISTANCE tiles away) without mistaking
# that for a stalled click.
MINE_RECLICK_TIMEOUT_S = 8.0

# How far (px) a pre-hovered rock's screen position may have drifted since the hover before
# mine_phase treats it as stale and falls back to a normal move+click instead of a bare click --
# ADDED 2026-08-24 alongside the pre-hover step (see MINING_TARGET_NAMES's own comment). Loose
# enough to absorb ordinary camera/interpolation jitter between ticks (tens of px, observed live)
# but tight enough that a real drift doesn't fire a click into empty ground.
HOVER_STALE_PX = 25

# Reaction delay used for the fast (already-hovering) click path -- deliberately shorter than the
# 0.15-0.5s used before a fresh move_to_human, since a human who is already resting their cursor
# on the next rock reacts to the confirmation almost immediately (recordings/2026-08-24_154518_
# golem-mining2-and-chisel.log: successive click timestamps track the swing's own resolution
# within ~0.1-0.3s once the mouse doesn't have to travel first).
HOVER_CLICK_REACTION_S = (0.05, 0.2)

# How long the pre-hover waits for the player to actually stop walking before it resolves and
# hovers the next rock (see mine_phase's own comment for the bug this fixes). Deliberately short:
# the rocks mine_phase picks are close, so a genuine walk between them is well under this, and if
# it does time out the pre-hover is simply skipped -- costing one normal move+click next tick,
# which is exactly what happened before this optimisation existed.
HOVER_IDLE_TIMEOUT_S = 2.0


# The `skill` value XP_GAINED carries for a mining swing. ControllerPlugin.onStatChanged sends
# Skill.getName() verbatim, which is the DISPLAY name ("Mining"), not the enum constant
# ("MINING") -- VERIFIED 2026-08-23 by disassembling Skill.class out of runelite-api-1.12.33.jar
# in the Gradle cache, where every constant carries both strings, rather than assuming which one
# getName() returns. Get this wrong and the match silently never fires: the gate would fall back
# to raw_count forever and look like it worked.
MINING_SKILL_NAME = "Mining"


def _mining_xp_seen(ctx) -> bool:
    """Whether a Mining XP_GAINED event has arrived since mine_phase last cleared the buffer.

    A zero-timeout PEEK at bridge_events' mirror, never a wait: mine_phase performs one bounded
    action per call so the engine's safety/break/PvP checks keep their cadence between swings,
    and blocking here for even one timeout would stall all of them on every unresolved swing.
    wait_for_event with timeout_s=0 pumps the socket once, scans, and returns immediately.

    Returns False when ctx.events is None (the engine started no listener -- see the routine
    contract in CLAUDE.md); mine_phase's inventory-count check is the fallback that carries the
    gate on its own in that case, exactly as it did before this signal existed."""
    if ctx.events is None:
        return False
    return ctx.events.wait_for_event(
        "XP_GAINED",
        match=lambda payload: payload.get("skill") == MINING_SKILL_NAME,
        timeout_s=0.0,
    ) is not None


def _mine_swing_pending(last_raw, raw_count, mining_xp_seen, last_click_time, now) -> bool:
    """Whether the swing mine_phase last clicked for is still unresolved -- i.e. whether to hold
    off re-clicking. Pure, so the whole gate is testable without a client or a serial port.

    Two independent proofs that the swing resolved, in the order they are trusted:

    1. `mining_xp_seen` -- the game's OWN answer. XP is granted at the instant the swing
       succeeds; the client's StatChanged fires there and ControllerPlugin.onStatChanged pushes
       it straight out over UDP, so this needs no HTTP round trip and does not care where the
       ore ended up.
    2. `raw_count > last_raw` -- the original inference, kept as the fallback. UDP is lossy by
       design (see the routine contract's "accelerator, never a source of truth"), and a dropped
       XP packet must cost latency, never a stalled trip.

    `last_raw is None` means nothing has been clicked yet this trip, so there is no swing to be
    pending. Past MINE_RECLICK_TIMEOUT_S nothing is pending either: click_entity's deliberate
    simulated misclick (see CLAUDE.md) can land a click that produces neither signal, ever, and
    an unbounded wait would trade "clicks too often" for "freezes forever" the first time that
    happens."""
    if last_raw is None:
        return False
    if mining_xp_seen or raw_count > last_raw:
        return False
    return now - last_click_time < MINE_RECLICK_TIMEOUT_S


def _hop_toward_point(ctx, x: int, y: int, plane: int) -> bool:
    """One single hop toward a world tile -- same viewport-then-minimap idiom as travel.py's own
    _hop_to, but fire-and-forget (no wait for arrival). Shared by _step_toward_plinth (see below)
    and mine_phase's own off-screen fallback (see mine_phase's docstring for why a found-but-
    too-far-to-click monolith needs this same trick the bank fallback in bank_phase already
    uses). Returns False (does nothing) if the tile isn't projectable onto either the viewport or
    the minimap right now."""
    point = controller.get_ground_walk_point(x, y, plane)
    if not point.get("onScreen"):
        point = controller.get_minimap_walk_point(x, y, plane)
        if not point.get("onMinimap"):
            return False

    controller.click_entity(ctx.ser, point, offset=(0, 0), check_ui_blockers=False)
    return True


def _step_toward_plinth(ctx, config: dict) -> None:
    """One single hop toward the "plinth" location anchor -- fire-and-forget (no wait for
    arrival): this only exists to interrupt an in-progress mining swing the instant mine_phase's
    budget is hit (see that function's own docstring), not to actually get there -- craft_phase's
    own station lookup handles the rest of the approach once chisel_phase is done.
    Does nothing if the plinth isn't a configured anchor."""
    target = (config.get("locations") or {}).get("plinth")
    if target is None:
        return
    _hop_toward_point(ctx, target["x"], target["y"], target.get("plane", 0))


def mine_phase(ctx, config: dict) -> None:
    """Click the sunstone monolith until raw_count + cores_have reaches SUNSTONE_PER_TRIP (or the
    inventory fills up first, as a safety cap), then hand off to chisel_phase. One swing per call,
    gated on raw_count actually rising since the last click (see below) -- same shape as
    mining.py's own single-rock loop, not a single blocking loop, so the engine's between-tick
    safety/break/PvP checks still run at their normal cadence throughout a long mining stretch.

    CORRECTED 2026-08-08 (live session, user request): used to also target the ~22 "Sunstone
    rocks" spots around the monolith, deliberately switching targets between swings (a "momentum"
    mechanic per the design doc) -- CONFIRMED live this sent the character running between rocks
    several tiles apart and re-targeted yet another one before the current walk/swing had even
    resolved. The monolith alone is a single fixed-position object, so this now just re-clicks
    it in place -- no target list, no switching, no walking, and the whole class of bug is gone
    rather than patched.

    CORRECTED 2026-08-08 (live session, user report): the stopping check used to be raw_count
    alone (>= SUNSTONE_PER_TRIP) -- CONFIRMED live this overmined whenever a previous trip ended
    with cores still unspent (e.g. craft_phase bailing out early on "out of usable fur" before
    using any of them): raw_count had dropped well below 20 but raw_count + cores_have (the real
    sunstone budget -- chiselling converts one to the other 1-for-1, it never changes that sum)
    was already at or near it, so mine_phase kept mining more on top, overflowing the inventory
    and leaving no room to empty the fur pouch afterward. Checking the combined count instead
    means a trip that resumes with leftover cores mines only enough raw to top the budget back
    up, not a fresh 20 regardless of what's already there.

    CORRECTED 2026-08-08 (live session, user report, second pass): the budget check used to sit
    AFTER the isIdle gate below -- CONFIRMED live the sunstone monolith (unlike a depleting
    "Sunstone rocks" spot) keeps the mining animation going for a long, uninterrupted stretch
    with no natural idle gap in between, so isIdle stayed False for minutes on end and the budget
    was never even checked until whatever eventually broke the streak -- 24 sunstone banked
    before it noticed, 4 over target. Checking the budget FIRST, every single call regardless of
    isIdle, means gc_phase flips to "chisel" on the very next tick() after crossing it.

    REWRITTEN 2026-08-23 (user request): the reclick gate keys off the Mining XP_GAINED event
    rather than inferring the swing's outcome from raw sunstone appearing in the inventory. See
    _mine_swing_pending/_mining_xp_seen and the gate's own comment block below.

    CORRECTED 2026-08-08 (live session, user request): moving the check earlier still isn't
    enough on its own -- the character is still mid-swing right up until something actually
    interrupts it, and chisel_phase has no reason to be at the mining spot at all. So the moment
    the budget is hit, this clicks a step toward the plinth (see _step_toward_plinth) instead of
    just flipping gc_phase and hoping for the best: the movement itself breaks the mining
    animation immediately, same as a player manually walking off would, and it's a step in a
    direction the routine needs to cover anyway before craft_phase can do anything."""
    raw_count = _count_named(RAW_SUNSTONE_NAME)
    cores_have = _count_named(SUNSTONE_CORE_NAME)
    if raw_count + cores_have >= SUNSTONE_PER_TRIP or len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        debug_log.log("golem crafting", f"have {raw_count} sunstone + {cores_have} cores -- moving to chisel phase")
        _step_toward_plinth(ctx, config)
        ctx.state.data["gc_phase"] = "chisel"
        ctx.state.data["gc_mine_target_pos"] = None
        ctx.state.data["gc_mine_last_raw_count"] = None
        ctx.state.data["gc_mine_last_click_time"] = 0.0
        ctx.state.data["gc_mine_hover_pos"] = None
        ctx.state.data["gc_mine_hover_screen"] = None
        return

    # CORRECTED 2026-08-08 (live session, user report: "still reclicks when already clicked a
    # node" -- user's own fix, replacing an isIdle-based settle-poll tried first): isIdle isn't a
    # reliable "is the last click still in flight" signal here -- it's raced this whole session
    # (see this function's own CORRECTED-history above), and a settle-poll on it just moved the
    # race around rather than closing it. Whether raw_count has actually gone up since the last
    # click is ground truth instead: if it hasn't, the last swing hasn't resolved yet (still
    # walking there, still mid-swing, or it whiffed and the game's own auto-repeat is about to
    # try again on its own) -- either way, clicking again right now is redundant "click all over"
    # noise, not progress. Once it rises, the previous click is spent and it's fine to evaluate
    # a new one.
    #
    # REWRITTEN 2026-08-23 (user request): raw_count is now the FALLBACK, not the primary signal.
    # Mining XP is the game's own direct answer to the same question -- it is granted at the
    # instant the swing succeeds, the client's StatChanged fires there, and ControllerPlugin
    # pushes it straight out as XP_GAINED over UDP (see CLAUDE.md's "XP gain is another sub-tick,
    # general-purpose signal"). That is strictly better evidence than inferring success from an
    # item appearing: it needs no HTTP round trip, and it holds regardless of what the swing
    # actually yielded or where it went. raw_count stays because UDP is lossy by design and
    # ctx.events can be None -- the routine contract's "accelerator, never a source of truth"
    # means a dropped packet must cost latency, never a stalled trip. Both live in
    # _mine_swing_pending, which is pure so the whole gate is unit-testable.
    #
    # CORRECTED 2026-08-08 (live session, user follow-up: "if misclick was true for last click
    # reclick"): click_entity's own simulate_mistakes feature (a deliberate, randomized near-miss
    # aimed beside the target -- see its own comment, this is intentional human-mimicry, not a
    # bug) means a "click" here can execute cleanly yet land nowhere useful, and raw_count then
    # never rises no matter how long this waits -- an unconditional wait-for-inventory-change
    # gate would just trade "clicks too often" for "freezes forever" the first time that happens.
    # MINE_RECLICK_TIMEOUT_S bounds the wait: long enough to cover a genuine walk-and-swing to a
    # distant rock, short enough that a wasted click (misclick, missed swing, whatever) gets
    # retried instead of stalling the whole trip.
    last_raw = ctx.state.data.get("gc_mine_last_raw_count")
    last_click_time = ctx.state.data.get("gc_mine_last_click_time", 0.0)
    if _mine_swing_pending(last_raw, raw_count, _mining_xp_seen(ctx),
                           last_click_time, time.time()):
        return

    committed_pos = ctx.state.data.get("gc_mine_target_pos")
    rock = find_mining_target(committed_pos)
    if rock is None:
        ctx.state.data["gc_mine_target_pos"] = None
        if handle_missing_target(ctx, config, "mining", "gc_mine_misses", "gc_mine_walks"):
            return
        ctx.state.trigger_safety_stop("no sunstone rocks found nearby -- wrong location?")
        return
    ctx.state.data["gc_mine_misses"] = 0
    ctx.state.data["gc_mine_walks"] = 0
    ctx.state.data["gc_mine_target_pos"] = (rock.get("x"), rock.get("y"))

    debug_log.log("golem crafting", f"mining {rock.get('name')} (dist={rock.get('distance')})")
    if ctx.events is not None:
        # pump() BEFORE clear(), not clear() on its own -- clear() only empties the mirror, so a
        # Mining XP packet belonging to the PREVIOUS swing that is still sitting unread in the
        # socket would simply be pumped in afterwards and resolve the swing we are about to
        # issue, before it has even landed. Pumping first pulls it into the mirror so clear()
        # can actually drop it. Nothing is lost from the routine's own event dispatch: pump()
        # also queues into the stream's pending list, which only main()'s drain() empties.
        ctx.events.pump()
        ctx.events.clear()

    # ADDED 2026-08-24 (user directive, "pre hover the next ore before exp drop then just click
    # when that hits" -- see MINING_TARGET_NAMES's own comment above for the full rationale):
    # if the previous call already rested the mouse on THIS exact rock while its predecessor's
    # swing was still resolving, only a click has to fire now -- skip click_entity's own
    # move_to_human entirely, provided the rock's screen position hasn't drifted meaningfully
    # since the hover (camera/interpolation jitter). A stale hover just falls through to the
    # normal move+click path below, no worse off than before this existed.
    hover_pos = ctx.state.data.get("gc_mine_hover_pos")
    hover_screen = ctx.state.data.get("gc_mine_hover_screen")
    already_hovering = (
        hover_pos == (rock.get("x"), rock.get("y"))
        and hover_screen is not None
        and rock.get("screenX") is not None
        and rock.get("screenY") is not None
        and math.hypot(rock["screenX"] - hover_screen[0], rock["screenY"] - hover_screen[1]) <= HOVER_STALE_PX
        # The rock not having drifted is only half the picture -- something else (e.g.
        # _shed_pit_junk dropping items, or the engine's own idle-mouse nudge) could have moved
        # the CURSOR away from the hover point without the rock itself moving at all. Checking
        # the cursor's actual current position is what makes this safe to blind-click.
        and math.hypot(*(a - b for a, b in zip(controller.get_cursor_pos(), hover_screen))) <= HOVER_STALE_PX
    )
    if already_hovering:
        controller.human_delay(*HOVER_CLICK_REACTION_S)
        controller.click_human(ctx.ser)
        clicked = True
    else:
        controller.human_delay(0.15, 0.5)
        clicked = controller.click_entity(ctx.ser, rock)

    ctx.state.data["gc_mine_hover_pos"] = None
    ctx.state.data["gc_mine_hover_screen"] = None

    if not clicked:
        # ADDED 2026-08-08 (live session): find_mining_target's MAX_OBJECT_DISTANCE radius can
        # report a rock as "found" while it's still off the actual rendered viewport -- CONFIRMED
        # live for this same problem with the monolith right after a fresh bank trip, before
        # anything had moved the player closer. Same fix as bank_phase's own minimap-hop
        # fallback for the exact same class of problem.
        _hop_toward_point(ctx, rock["x"], rock["y"], rock.get("plane", 0))
        return

    ctx.state.data["gc_mine_last_raw_count"] = raw_count
    ctx.state.data["gc_mine_last_click_time"] = time.time()

    # Rest the mouse on a DIFFERENT ready rock right now, while this swing plays out on the
    # server, so the click that resolves it only has to fire, not travel -- see the pre-hover
    # comment above. exclude_pos guards against the rock just clicked still reporting as ready
    # (its own depletion may not have propagated to the bridge's object list yet).
    # FIXED 2026-08-24 (user report: "we hover before we stop moving so the cursor ends up
    # wrong"). This runs immediately after click_entity, when the player is usually still WALKING
    # to the rock it just clicked -- and a walking player invalidates every screen position
    # continuously, because the camera follows them. The original code hovered straight away and
    # then recorded next_rock's PREDICTED screen point as the anchor, so the next tick measured
    # staleness against a value nothing had ever verified. With HOVER_STALE_PX at 25 a drift small
    # enough to pass that gate is still easily enough to sit off a rock's clickbox, and the fast
    # path then fired click_human into bare ground.
    #
    # Waiting costs nothing here: the swing this hover is meant to overlap lasts seconds AFTER
    # the player arrives, so the window it exploits is still wide open. The rock is re-resolved
    # after the wait because the position read before the walk is stale by definition.
    if not _await_player_stopped(ctx, HOVER_IDLE_TIMEOUT_S):
        return  # still moving -- any position resolved now is already stale; next tick clicks normally

    next_rock = find_mining_target(exclude_pos=(rock.get("x"), rock.get("y")))
    if (next_rock is not None
            and next_rock.get("screenX") is not None
            and next_rock.get("screenY") is not None
            and controller.hover_entity(ctx.ser, next_rock)):
        # Record where the cursor ACTUALLY ended up, not where it was predicted to. The aim jitter
        # alone (ENTITY_AIM_RADIUS) and hover_entity's own offset already move the landing point
        # off next_rock's centre, so the prediction was never the right anchor for a staleness
        # check even with a perfectly stationary player.
        ctx.state.data["gc_mine_hover_pos"] = (next_rock.get("x"), next_rock.get("y"))
        ctx.state.data["gc_mine_hover_screen"] = controller.get_cursor_pos()


# How many sunstone cores to chisel per mining trip (== golems per trip, one core each) --
# CORRECTED 2026-08-08 (live session, user request): chisel_phase used to convert EVERY raw
# sunstone into a core, which left nothing raw to ever start a NEW golem with ("Start-golem"
# consumes raw sunstone specifically, not a core -- see this module's header comment).
# User-specified batch shape: mine SUNSTONE_PER_TRIP (20) sunstone, chisel 4 into cores (leaving
# 16 raw), craft 4 golems. CORRECTED 2026-08-20: at the confirmed cost of 4 raw per golem (see
# SUNSTONE_PER_GOLEM above), that's 4 golems x 4 raw = all 16 remaining raw sunstone used, 0
# carried into the next trip -- not "1 raw + 1 core each" / partial carry-over as this comment
# previously (and wrongly) claimed. The batch is exact: 20 sunstone-equivalents mined, 20 spent
# (16 raw on Start-golem + 4 cores), then mine a fresh batch and repeat.
CORES_PER_TRIP = 4

# How long to wait, after a chisel click, for the specific conversion to actually be reflected
# in the bridge's inventory snapshot -- ADDED 2026-08-08 (live session, user report): a flat
# fixed sleep here ("let the action register server-side before the next isIdle poll") wasn't
# reliably long enough on every attempt -- CONFIRMED live: chisel_phase occasionally re-issued
# a chisel-then-item click pair against an item that had already converted to a core by the
# time the click fired (the previous call's `find_item(RAW_SUNSTONE_NAME)` result had gone
# stale between being fetched and the bridge's next per-tick-cached snapshot reflecting the
# conversion), landing a pointless chisel-on-core click. Polling for the actual core count to
# increase before returning closes this instead of hoping a fixed delay was long enough.
CHISEL_CONFIRM_TIMEOUT_S = 3.0

# chisel_phase chains every core of the batch inside ONE tick() call (2026-09-06): the human
# chisels four in a row without a pause, and the engine loop's ~1s between ticks was the only
# thing spacing them. Bounded so the engine's pause/safety gates stay responsive.
CHISEL_CHAIN_MAX_S = 12.0


def _count_named(name: str) -> int:
    name_lower = name.lower()
    return sum(1 for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower)


def chisel_phase(ctx, config: dict) -> None:
    """Chisel raw sunstone into cores, up to CORES_PER_TRIP total (not "until none are left" --
    see that constant's docstring for why some must stay raw), item-then-item two-click idiom
    same as firemaking.py's tinderbox+log. One chisel action per call, gated on isIdle -- craft
    once CORES_PER_TRIP is reached or raw sunstone runs out first."""
    if not controller.get_combat().get("isIdle", True):
        return

    controller.open_inventory_tab(ctx.ser)

    # 2026-09-06: every core of the batch in one call, each confirmed on INVENTORY_CHANGED (the
    # core appearing is the truth, the event only says when to look). Pause/safety re-checked
    # between cores, same as process_phase-style loops elsewhere.
    deadline = time.time() + CHISEL_CHAIN_MAX_S
    while True:
        if ctx.state.safety_stopped or ctx.state.paused:
            return

        cores_have = _count_named(SUNSTONE_CORE_NAME)
        if cores_have >= CORES_PER_TRIP:
            debug_log.log("golem crafting", f"chiselled {cores_have} cores -- moving to craft phase")
            ctx.state.data["gc_phase"] = "craft"
            return

        raw = find_item(RAW_SUNSTONE_NAME)
        if raw is None:
            debug_log.log("golem crafting", f"no raw sunstone left ({cores_have}/{CORES_PER_TRIP} cores chiselled) -- moving to craft phase")
            ctx.state.data["gc_phase"] = "craft"
            return

        chisel = find_item(JEWELLERS_CHISEL_NAME) or find_item(CHISEL_NAME)
        if chisel is None:
            ctx.state.trigger_safety_stop("no chisel in inventory")
            return

        if time.time() >= deadline:
            return  # hand the tick back; re-enters here next tick

        debug_log.log("golem crafting", f"chiselling sunstone into a core ({cores_have}/{CORES_PER_TRIP})")
        mark = outcome_waits.mark(ctx)
        controller.human_delay(0.15, 0.35)
        controller.click_entity(ctx.ser, chisel, check_ui_blockers=False)
        controller.human_delay(0.1, 0.25)
        controller.click_entity(ctx.ser, raw, check_ui_blockers=False)

        landed = outcome_waits.await_pre(
            ctx, "INVENTORY_CHANGED", CHISEL_CONFIRM_TIMEOUT_S,
            confirm=lambda before=cores_have: _count_named(SUNSTONE_CORE_NAME) > before,
            since=mark, now_fn=time.time, sleep_fn=time.sleep,
        )
        if not landed:
            debug_log.log("golem crafting", "chisel click didn't register within timeout -- retrying next tick")
            return


# CHAT_MESSAGE substring (lowercased) this routine still reacts to. REWRITTEN 2026-08-20: every
# other golem-crafting chat message this used to watch for (side-done, side-already-done,
# not-enough-sunstone, wrong-insert-side, overall-complete) is superseded by a /golem varbit read
# -- see craft_phase/on_event below. Fur is the one exception: fur count is NOT one of the game's
# exposed state variables, so this rejection message (CONFIRMED live 2026-08-08, MCP debug
# session, as the exact text the game sent back) remains the only authoritative fur signal there
# is. gc_fur_stock is only ever a locally-tracked estimate (set from a bank withdrawal's qty,
# decremented by 1 per completed golem), never corrected against what hunt_phase actually catches,
# so it can read >0 while the pouch is genuinely empty -- see on_event's handling of this message
# and craft_phase's use of the "gc_fur_exhausted" flag it sets.
GOLEM_CHAT_NOT_ENOUGH_FUR = "you need to dress the golem in furs from hunted creatures."

# CONFIRMED by the user 2026-08-20: the gift a finished golem awards goes straight into the gem
# bag -- there is nothing on the ground to pick up, so the old three-attempt ground-item retry
# loop (and its 2.0s settle) is gone. The game's own message names the destination, though, and
# a FULL gem bag drops the gift to the ground instead. Detecting that from the message costs
# nothing on the common path, where scanning the ground every golem cost ~2s and up to 3 clicks.
GOLEM_LOOT_GROUND_MARKER = "leaves a gift on the ground"

# FINDING C (2026-08-20 whole-branch review): the "nothing on the ground to pick up" comment
# above is only true on the common path -- a full gem bag still drops the gift to the ground, and
# the pickup that follows can miss it (a name-filter mismatch between the chat message's parsed
# item name and the ground item's actual name, e.g. "uncut rubies" vs "Uncut ruby"; or the
# deliberate ~4.5% MISTAKE_MISCLICK_CHANCE near-miss, see CLAUDE.md) with no way to tell that
# apart from success. This restores a bounded retry (same count, 3, as the three-attempt loop
# this file's REWRITTEN 2026-08-20 header note says was deleted) around a fresh ground re-check
# each attempt, rather than firing a single click and trusting it landed.
GOLEM_GEM_PICKUP_ATTEMPTS = 3

# Large fur pouch's capacity -- once hit, further fur spills into loose inventory instead of the
# pouch, which is exactly the signal the fur trip uses to know the pouch is full without any
# dedicated capacity-check action (CONFIRMED live at the goat pit, 2026-08-09).
FUR_POUCH_CAPACITY = 28


def on_event(ctx, event_type, payload) -> None:
    """Reacts to the one golem-crafting chat signal that isn't available from /golem's varbits,
    and separately captures hunt_phase's own two chat signals -- controller.py's main loop drains
    UDP events and calls this before every tick() (see controller.py's main()), so a message that
    arrives between two tick() calls is caught here and available on craft_phase's next call.

    REWRITTEN 2026-08-20 (see docs/superpowers/specs/2026-08-20-golem-crafting-varbit-overhaul-
    design.md): craft_phase's side/insert confirmation now polls /golem's own varbits directly
    (see _await_side_done/_await_golem_complete below) instead of waiting on a chat message, so
    the gc_last_chat/gc_last_chat_seq generic capture-and-compare machinery this docstring used
    to describe is gone. Fur count is the one exception -- it is NOT exposed by any golem state
    variable, so GOLEM_CHAT_NOT_ENOUGH_FUR (the game's own rejection message) remains the only
    authoritative fur signal there is. Set directly into a "gc_fur_exhausted" flag craft_phase
    checks itself, rather than a generic chat channel with nothing left to read it now that every
    other golem-crafting chat message this used to watch for is superseded by a varbit read.

    The goat-pit chat capture that used to live here went with hunt_phase (2026-09-06)."""
    if event_type != "CHAT_MESSAGE" or not payload:
        return
    message = (payload.get("message") or "").lower()

    if GOLEM_CHAT_NOT_ENOUGH_FUR in message:
        # gc_fur_stock is only ever a locally-tracked estimate (set from a bank withdrawal's qty,
        # decremented by 1 per completed golem), never corrected against what hunt_phase actually
        # catches, so it can read >0 while the pouch is genuinely empty -- this message is the
        # game's own authoritative answer. See craft_phase's own use of this flag for why it's
        # checked both before attempting an insert and right after one fails to confirm.
        ctx.state.data["gc_fur_exhausted"] = True

    if GOLEM_LOOT_GROUND_MARKER in message:
        # Gem bag is full -- the gift fell on the ground. craft_phase picks it up once and banks
        # -- see its own CRITICAL FIX 2026-08-20 comment for why that pickup runs on the NEXT
        # craft_phase call, against gc_last_station, rather than in the same call this flag is
        # read (on_event can't drain this message until the next main-loop iteration either way).
        ctx.state.data["gc_gem_bag_full"] = True
        # Parsed out of "... for you: <qty> x <item name>." (the game's own message format --
        # see docs/superpowers/specs/2026-08-20-golem-crafting-varbit-overhaul-design.md) so the
        # pickup below can filter by name instead of taking any ground item merely near the
        # right tile. rfind rather than a regex: this codebase has no other chat-parsing regex
        # to match the idiom of, and " x " is not expected to appear anywhere in the three fixed
        # destination phrases (see GOLEM_LOOT_GROUND_MARKER's siblings), only right before the
        # item name.
        item_name = None
        marker_idx = message.rfind(" x ")
        if marker_idx != -1:
            item_name = message[marker_idx + 3:].rstrip(".").strip()
        ctx.state.data["gc_gem_bag_full_item"] = item_name


# How long to wait for a side's `done` varbit to flip after a Shape-golem click, PROVIDED the
# side isn't still visibly climbing (see GOLEM_SIDE_MAX_WAIT_S below). Far shorter than the 15s
# chat-message timeout this replaces: the varbit moves on the tick the server processes the
# action, so a click that genuinely landed and is just still animating shows the per-side
# `progress` field rising, and only a truly stalled value past this deadline means the click
# didn't land.
# CORRECTED 2026-08-23 from 4.0s, using a hand-played recording rather than an estimate
# (recordings/2026-08-23_194302_golem-carving): a side's FIRST observable `progress` bump lands
# +2.1s after the click even when the player is already standing on the tile, and the carve walk
# is now fired without waiting for arrival, so the walk's remaining flight time lands inside this
# window too. At 4.0s the first bump arrived just PAST the deadline whenever any walking was
# left -- and a timeout here is not a harmless extra wait: carve_plan re-picks a side from the
# player's new position on the next tick and walks away, INTERRUPTING the carve that had
# genuinely started and resetting its progress to 0. CONFIRMED live 2026-08-23 as an unbroken
# retry loop that safety-stopped after 5 attempts with only the first side ever completing.
GOLEM_SIDE_TIMEOUT_S = 8.0

# FINDING D (2026-08-20 whole-branch review): GOLEM_SIDE_TIMEOUT_S was picked without live
# confirmation of how long a single side carve actually takes -- in-repo evidence on this is
# contradictory, and if a real carve runs longer than 4.0s, EVERY side would time out, charge a
# spurious gc_side_misses tick, and re-walk/re-click mid-carve, turning this branch's headline
# "fast confirmation" saving into extra clicks. _await_side_done extends its deadline for as long
# as the side's own `progress` value keeps climbing between polls (genuine evidence the carve is
# underway), rather than guessing the right fixed constant. This is the hard cap on that
# extension, so a side that is climbing but never actually finishes (a genuine anomaly, not a slow
# carve) still terminates instead of waiting forever.
# CORRECTED 2026-08-23 from 15.0s: a full side is ~11.8s from the click (MEASURED at 11.9/11.9/
# 11.7/11.7 across four sides), and with the walk now in flight during that window the realistic
# worst case is ~15s -- i.e. the old cap sat exactly ON the value it was meant to bound. This only
# bites once `progress` has stopped climbing entirely, so headroom costs nothing in the normal
# case and only decides how long a genuinely stuck side waits before being called stuck.
GOLEM_SIDE_MAX_WAIT_S = 25.0

# Insert-core gets a little longer -- it consumes a core and a fur and plays a longer animation
# before the station's progress varbit resets.
# Re-aim checkpoints for the plinth click while the player is still walking toward the side tile
# (see _click_station). refresh_target alone only re-aims ONCE, partway through the flight, which
# is enough for a camera drift against a stationary player but not for a player who is crossing
# tiles during the flight. 3 legs is the smallest value that actually tracks; see CLAUDE.md's
# "Sub-tick data and clicking while moving" for why the un-cached /object/at read behind
# object_refresh_target is what makes this possible at all.
GOLEM_CLICK_REFRESH_LEGS = 3

# How long to leave between firing the carve walk and clicking the plinth. Straight off the
# recording: the human's four walk-click -> object-click gaps were 1.0s, 1.0s, 0.6s and 0.6s.
# Not a settle margin -- the player is deliberately still moving when the click lands.
GOLEM_WALK_TO_CLICK_GAP_S = (0.6, 1.0)

# Every option a legitimate plinth click can resolve to. craft_phase does not get to choose --
# the game picks based on the station's own state -- so all three are accepted and only something
# OUTSIDE this set proves the click went somewhere it shouldn't have.
GOLEM_PLINTH_OPTIONS = ("Start-golem", "Shape-golem", "Insert-core")

# How long to wait for the game to report what the plinth click resolved to before giving up and
# falling back to the ordinary confirmation. Deliberately short: this is a fast NEGATIVE check,
# and "no answer yet" must never be read as failure (see _click_resolved_wrongly).
GOLEM_CLICK_ECHO_TIMEOUT_S = 1.5
GOLEM_CLICK_ECHO_POLL_S = 0.1

# How many times in a row the fast hijack retry may fire for one side before deferring to the
# ordinary confirmation wait. One, and the bound is the whole point -- see the REGRESSION FIX note
# at the call site. An immediate retry re-clicks the same geometry, so on its own it converts an
# occasional stall into a deterministic lock; the slow path's wait is what lets the world move.
# There is deliberately no larger "hijack limit" any more: with a budget of one, consecutive
# hijacks degrade to exactly the pre-existing behaviour and are already bounded by
# GOLEM_SIDE_MISS_LIMIT, so a second counter would only add a second way to safety-stop.
GOLEM_FAST_RETRY_BUDGET = 1


def _click_resolved_wrongly(before: dict, after: dict, object_id: int):
    """Whether a plinth click provably resolved to the WRONG action. Tri-state, and the third
    state is the whole point:

      False -- provably right: a golem option, on the plinth we aimed at.
      True  -- provably wrong: the game recognised some other action instead.
      None  -- unknown: nothing new has been processed yet.

    CONFIRMED live 2026-08-23 (recordings/2026-08-23_201416_routine-carve-drops, captured with
    the recorder running alongside the routine): a bare left-click on the plinth is resolved by
    the game against whatever is on top of that pixel, and another PLAYER standing there turns it
    into "Walk here" on them --

        20:14:34.056  Walk here  ->                        <- the fire-and-forget walk
        20:14:35.034  Walk here  -> DoveHorn (level-115)   <- the plinth click, hijacked

    -- after which the routine waited out the entire GOLEM_SIDE_TIMEOUT_S for a carve that was
    never going to start. It cannot be a slow carve: _await_side_done resets its deadline on every
    `progress` bump and bumps arrive ~2.4s apart, so a genuine carve can never reach that timeout.
    Every timeout is a click that produced zero progress.

    `None` must never be treated as failure. /last-action reports an action being RECOGNISED, not
    completing (see controller.get_last_action), and a click that has not been processed yet looks
    identical to one that never will -- calling that a miss would re-introduce exactly the
    destructive early-abort that made the 4.0s timeout so damaging.

    The `tick` comparison is load-bearing for the same reason FINDING A made _await_side_done
    transition-based: /last-action holds only the MOST RECENT click, so a successful PREVIOUS
    carve is byte-identical to this one succeeding. Only a genuinely new action counts."""
    if not after:
        return None
    if before and (after.get("tick") == before.get("tick")
                   and after.get("option") == before.get("option")
                   and after.get("id") == before.get("id")):
        return None
    if after.get("option") in GOLEM_PLINTH_OPTIONS and after.get("id") == object_id:
        return False
    return True


def _await_click_echo(before: dict, object_id: int, ctx=None, since=None):
    """Poll /last-action briefly for the game's own verdict on the plinth click just issued.
    Returns _click_resolved_wrongly's tri-state; None on timeout, which hands control back to the
    ordinary confirmation path unchanged.

    2026-09-06: with `ctx.events`, MENU_CLICKED -- the same MenuOptionClicked pushed as an event
    -- wakes the read instead of a 0.1s poll; /last-action stays the truth (the tri-state and
    "None is not failure" rule are unchanged). `since` is the mark the caller took BEFORE the
    click, so an echo from an earlier click never counts."""
    events = _events_of(ctx)
    mark = since if since is not None else (events.mark() if events is not None else None)
    deadline = time.time() + GOLEM_CLICK_ECHO_TIMEOUT_S
    while time.time() < deadline:
        verdict = _click_resolved_wrongly(before, controller.get_last_action(), object_id)
        if verdict is not None:
            return verdict
        if events is None:
            time.sleep(GOLEM_CLICK_ECHO_POLL_S)
        else:
            mark = _wait_slice(events, mark, {"MENU_CLICKED": None},
                               min(EVENT_SLICE_S, max(0.0, deadline - time.time())))
    return None


GOLEM_INSERT_TIMEOUT_S = 6.0

# How often to re-read /golem while awaiting a confirmation. Well under the 0.6s game tick, since
# the endpoint is deliberately not TickCache-gated (see GolemProvider's own comment).
GOLEM_CONFIRM_POLL_S = 0.2

# Consecutive carve-side failures overall before treating it as a real anomaly. CORRECTED
# 2026-08-20 (whole-branch review, comment-only fix -- FINDING F): this used to say "at the SAME
# side", which is wrong -- gc_side_misses is a single un-keyed counter, not reset or tracked per
# side, so 5 failures spread across up to 4 different sides trip this identically to 5 failures on
# one side. Behavior is fine as-is; only the comment was wrong. With ground truth available a
# stuck side is genuinely abnormal, not the routine desync the old 16-attempt budget existed to
# absorb, so this is an assertion rather than a retry allowance.
GOLEM_SIDE_MISS_LIMIT = 5

# Same reasoning as GOLEM_SIDE_MISS_LIMIT, for the Insert-core step -- an unbounded retry here
# would hang the routine silently, which is the exact bug class this overhaul exists to remove.
GOLEM_INSERT_MISS_LIMIT = 5

# CRITICAL FIX 2026-08-20 (code review of the varbit-overhaul rewrite): craft_phase's walk-to-tile
# and click-the-plinth steps used to return on failure with no counter incremented at all -- a
# byte-identical retry every following tick, forever, with nothing to make progress or escalate.
# Reachable in normal play: craft_phase is entered from chisel_phase at the mining anchor, ~10
# tiles from any station tile, and whether a station tile projects onto the viewport from there
# depends on camera yaw, which nothing on that path changes. _walk_to_station_tile/
# _click_station_tracked below bound those two failure modes the same way GOLEM_SIDE_MISS_LIMIT/
# GOLEM_INSERT_MISS_LIMIT already bound the confirmation-side ones.
GOLEM_APPROACH_MISS_LIMIT = 5

# IMPORTANT FIX (same review): get_golem() can return None on nothing worse than api_get's own
# 0.3s timeout -- CLAUDE.md documents this exact routine blowing that timeout under load -- so a
# single miss must not immediately safety-stop asserting "rebuild the client", which is very
# likely the wrong diagnosis for what's usually a transient bridge hiccup. Same
# MAX_CONSECUTIVE_MISSES-style tolerance as handle_missing_target's own poll-tolerance layer
# (see that function's docstring), kept as its own constant since this gates a hard safety-stop
# rather than a location-anchor walk.
GOLEM_STATE_MISS_LIMIT = 5


def walk_to_exact_tile(ctx, x: int, y: int, plane: int, wait_for_arrival: bool = True) -> bool:
    """Walk to the exact world tile (x, y) and wait until standing exactly on it. Deliberately
    NOT travel.py's walk_to/_hop_to: those consider landing within WALK_ARRIVAL_TOLERANCE
    (3 tiles) of the target "arrived", which is useless for either of this function's two
    callers -- craft_phase's golem sides are only 1-2 tiles apart from each other (a 3-tile-
    tolerant arrival could leave the player on the WRONG side while still reporting success),
    and hunt_phase's fixed north-of-the-pit standing tile needs to be exact for its own
    north-side-exclusion geometry (is_valid_grab_target) to mean anything. Returns False only
    if the tile can't be projected onto the screen at all (out of render range -- shouldn't
    happen for a tile adjacent to an already-visible golem/pit); a timed-out wait still returns
    True, since callers re-check position fresh afterward either way.

    RENAMED 2026-08-08 from walk_to_golem_side -- originally craft_phase-specific, generalized
    (no logic change) once hunt_phase needed the exact same exact-arrival behavior for its own
    fixed standing tile rather than duplicating this polling loop.

    Holds Shift during the click -- ADDED 2026-08-09 (user report: "accidentally clicked the
    pit and started emptying it" while walking back near it): a plain click at a ground point
    close to an interactive object (the Goat Pit is only 2 tiles from hunt_phase's standing
    tile) can resolve to the OBJECT's own action instead of walking there, if their clickable
    areas overlap on screen -- CONFIRMED live this cleared the pit (removing its lining) mid
    walk-back. First tried an explicit right-click "Walk here" menu selection instead, but per
    user feedback that looked visually different/out of place on craft_phase's own frequent
    golem-side walks, which have no such overlap risk to guard against in the first place.
    Shift+click is OSRS's own standard force-walk shortcut (same mechanic a human player already
    uses for exactly this situation) -- forces the "Walk here" action regardless of what's under
    the cursor, in a single click that looks the same as any other, so it's safe to use
    unconditionally for both callers instead of needing a per-caller opt-in.

    ADDED 2026-08-10 (user report: camera-vs-true-tile discrepancy causing misclicks): passes
    refresh_target so move_to_human's mid-flight re-aim re-queries this tile's live screen
    position instead of trusting the single snapshot taken before the click started -- same
    staleness fix already proven live for cast_telekinetic_grab's moving-NPC targets, applied
    here for camera movement instead of target movement (see get_ground_walk_screen_pos).

    FINDING E (2026-08-20 whole-branch review): early-outs with no click at all if the player is
    already standing exactly on (x, y, plane) -- craft_phase's insert-side walk is NORMALLY a
    no-op by design (the carve order ends on the insert side, see carve_plan's docstring), but
    without this check every golem still paid for a full human-paced shift+click flight plus this
    function's own 0.6s first poll for a walk that was never needed. Safe for hunt_phase's fixed
    standing-tile walk too -- reporting True immediately when already there is exactly what that
    caller wants as well, just skipping straight to it instead of clicking and then discovering
    the same thing a poll later."""
    # MOVED 2026-08-20 to travel.walk_to_exact_tile, verbatim, once routines/chinchompa_hunting.py
    # needed the identical exact-arrival walk (it has to stand on the precise tile it lays a box
    # trap on). Kept as a delegating wrapper rather than replacing this module's ~15 call sites:
    # the docstring above is this behaviour's whole provenance record, and test_golem_crafting.py
    # patches `gc.walk_to_exact_tile` by name in several craft_phase tests, so the name has to
    # stay a real attribute of this module either way.
    return travel.walk_to_exact_tile(ctx, x, y, plane, wait_for_arrival=wait_for_arrival)


def _station_by_name(state: dict, name: str):
    return next((s for s in state.get("stations", []) if s.get("name") == name), None)


def _find_station_object(station: dict, objects: list):
    """The plinth GameObject for `station`, matched by object id, out of an already-fetched
    `objects` list.

    Matched by id rather than by name: the object's name changes as the golem progresses (Empty
    plinth -> Unfinished golem -> Unpowered golem) while its id does not, so an id match needs no
    state-name table and cannot be confused by a state transition mid-lookup.

    Takes `objects` rather than fetching its own -- see craft_phase's own comment on why /objects
    is resolved exactly once per call and threaded through here and _any_station_visible instead
    of each independently paying for it."""
    return next(
        (o for o in objects if o.get("id") == station["objectId"]),
        None,
    )


def _any_station_visible(stations: list, objects: list) -> bool:
    """Whether at least one of `stations`' plinth GameObjects is currently within
    MAX_OBJECT_DISTANCE, per an already-fetched `objects` list (see craft_phase). Needed because
    GolemProvider reports both stations unconditionally regardless of whether the player is
    actually in the golem-crafting area or anywhere near either plinth (CONFIRMED by code
    review) -- craft_phase's `stations` list can never be empty, so this is the check that
    actually detects "in the area, but nothing to click right now", the state `not stations` was
    originally meant to (and never could) catch."""
    visible_ids = {o.get("id") for o in objects}
    return any(s.get("objectId") in visible_ids for s in stations)


def _click_station(ctx, station, objects: list, use_menu: bool = False) -> bool:
    """Click the plinth (resolved out of the already-fetched `objects` list), hopping closer
    first if it isn't on screen. Returns False if the click did not go out, in which case the
    caller should retry next tick.

    A plain left-click is enough -- its default action already resolves to Start-golem /
    Shape-golem / Insert-core in each respective state, so there's no need for
    click_menu_action's right-click + menu-read + select round trip."""
    obj = _find_station_object(station, objects)
    if obj is None:
        _hop_toward_point(ctx, station["x"], station["y"], station.get("plane", 0))
        return False
    if use_menu:
        # Stop moving before trusting any screen position. CONFIRMED live 2026-08-23 21:24: an
        # escalation right-clicked bare tile even though it had just re-resolved the plinth,
        # because the player was still walking -- the position was read at 21:24:47.571 and the
        # cursor landed at 21:24:47.771, 200ms and roughly a third of a tile later, 6px off the
        # object. This is already the slow path, so the wait costs the fast one nothing. See
        # wait_until_player_idle's own docstring -- it exists for precisely this call shape.
        # click_menu_action GAINED refresh_target/refresh_legs 2026-08-27 (see its own docstring)
        # and now re-aims mid-flight like a bare click, but the idle-wait stays: it was ALSO
        # covering "click_menu_action aims exactly once" (now false too), yet removing a wait
        # this codebase's own notes call load-bearing on a live, unattended routine is a separate,
        # live-verified change -- not bundled into this factory-adoption pass.
        controller.wait_until_player_idle()
        # Re-resolve the plinth's CURRENT screen position first. `objects` was fetched at the top
        # of craft_phase, before the walk, so its screenX/screenY is stale by however far the
        # player travelled. CONFIRMED live 2026-08-23 21:12: the first menu escalations opened a
        # menu of pure ground options (['Cancel', 'Mark Safespot', 'Examine', 'Walk here']) --
        # a right-click on bare tile, not on the plinth at all. offset=(0,0) for the same reason:
        # click_menu_action's default (-7, +7) nudge is enough to slide off a small plinth.
        fresh = controller.get_object_at(
            obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
            radius=MAX_OBJECT_DISTANCE,
        )
        if fresh is not None:
            obj = fresh
        refresh = controller.object_refresh_target(
            obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
            radius=MAX_OBJECT_DISTANCE,
        )
        # Right-click and pick the option BY NAME out of /menu, instead of trusting whatever the
        # default left-click action resolves to. LIVE HALT 2026-08-23 21:04: another player was
        # standing on the plinth's east tile -- players do not block each other, so they stack on
        # OUR tile -- and every bare left-click resolved to them ("Walk here -> DoveHorn"), five
        # in a row, until the routine safety-stopped and stayed down. This is exactly the case
        # click_menu_action's own docstring was written for. All three options are offered because
        # craft_phase does not get to choose which one the game presents for the station's current
        # state; click_menu_action tries a sequence in priority order against the one menu it
        # already reads back, so this costs no extra right-click.
        if controller.click_menu_action(ctx.ser, obj, GOLEM_PLINTH_OPTIONS, offset=(0, 0),
                                         refresh_target=refresh,
                                         refresh_legs=GOLEM_CLICK_REFRESH_LEGS):
            return True
        # The menu route is a best-effort ESCALATION, so a miss degrades to the ordinary click
        # rather than being charged as a hard miss. LIVE HALT 2026-08-23 22:04: five consecutive
        # menu misses safety-stopped a routine that had just produced 22 golems in 36 minutes on
        # its bare-click path. The plinth is ringed by four unnamed clickable marker objects, one
        # per side tile (62359-62362 around the north plinth, 62355-62358 around the south), and
        # the one standing on OUR OWN tile competes for the click -- a frame dump with both
        # centres marked showed the empty plinth's reported clickbox centre landing on bare ground
        # at that camera angle, so the menu contained only the marker's "Examine". Falling through
        # costs one wasted right-click; not falling through costs the session.
        debug_log.log("golem crafting",
                      f"menu had no golem option for {station['name']} plinth "
                      f"-- falling back to an ordinary click")
        use_menu = False
    refresh = controller.object_refresh_target(
        obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
        radius=MAX_OBJECT_DISTANCE,
    )
    # expected_action (added 2026-08-27): verify via /hover that the default left-click is one
    # of the plinth's own options BEFORE committing the click -- prevention for the exact
    # player-camped-on-the-pixel hijack the /last-action echo below detects after the fact.
    # The full option set, because craft_phase does not get to choose which one the game offers
    # (see GOLEM_PLINTH_OPTIONS' own comment). A withheld click comes back as the falsy
    # CLICK_WITHHELD sentinel and must NOT hop -- the aim was fine, the pixel is contested --
    # so it propagates to the caller's hijack budget instead.
    res = controller.click_entity(ctx.ser, obj, refresh_target=refresh,
                                  refresh_legs=GOLEM_CLICK_REFRESH_LEGS,
                                  expected_action=GOLEM_PLINTH_OPTIONS)
    if not res:
        if getattr(res, "withheld", False):
            return res
        _hop_toward_point(ctx, station["x"], station["y"], station.get("plane", 0))
        return False
    return True


def _click_station_tracked(ctx, station, miss_key: str, objects: list,
                           use_menu: bool = False) -> bool:
    """_click_station with a bounded miss counter -- see GOLEM_APPROACH_MISS_LIMIT's own comment
    for why an uncounted click failure is exactly the same unbounded-loop shape as an uncounted
    walk failure. _click_station already hops toward the station on a miss; this adds the
    counting and the eventual safety-stop escalation once hopping alone hasn't fixed it."""
    res = _click_station(ctx, station, objects, use_menu=use_menu)
    if res:
        ctx.state.data[miss_key] = 0
        return True
    if getattr(res, "withheld", False):
        # NOT an aim miss: the aim landed and hover verification proved the pixel is contested
        # (or the option absent). Counting it here would let a parked player walk the routine
        # into the safety stop; the caller's hijack budget bounds it instead, so it propagates.
        return res
    misses = ctx.state.data.get(miss_key, 0) + 1
    ctx.state.data[miss_key] = misses
    debug_log.log(
        "golem crafting",
        f"click on {station['name']} plinth missed (miss {misses}/{GOLEM_APPROACH_MISS_LIMIT})",
    )
    if misses >= GOLEM_APPROACH_MISS_LIMIT:
        ctx.state.trigger_safety_stop(
            f"clicking {station['name']} plinth failed {GOLEM_APPROACH_MISS_LIMIT} times in a "
            f"row; station state: {station}"
        )
    return False


def _walk_to_station_tile(ctx, station, tile: dict, miss_key: str,
                          wait_for_arrival: bool = True) -> bool:
    """walk_to_exact_tile toward `tile`, with a bounded miss counter (`miss_key`) and a
    _hop_toward_point fallback toward the station's own tile on failure -- see
    GOLEM_APPROACH_MISS_LIMIT's own comment for why an uncounted failure here is exactly the
    no-progress spin this overhaul exists to remove. walk_to_exact_tile only returns False when
    the target tile can't be projected onto the viewport at all (see its own docstring), meaning
    the camera/position genuinely needs to change before the next attempt can possibly succeed --
    which is exactly what the hop provides.

    `wait_for_arrival=False` (2026-08-23) fires the walk and returns immediately so the caller can
    click the plinth while the player is still moving -- the carve step's mode. The side that gets
    carved is the tile walked TO, not the tile clicked from: MEASURED 3/3 in
    recordings/2026-08-23_194302_golem-carving, by resolving GOLEM_2_{NORTH,EAST,SOUTH,WEST}_
    PROGRESS against runelite-api-1.12.36.jar and matching each against the walk destination in
    flight at the moment of the click. The game queues the interaction and performs it on arrival.
    The insert step keeps the blocking default -- it is normally a no-op walk (the carve order
    ends on the insert side) so there is no latency there to reclaim."""
    if walk_to_exact_tile(ctx, tile["x"], tile["y"], tile.get("plane", 0),
                          wait_for_arrival=wait_for_arrival):
        ctx.state.data[miss_key] = 0
        return True
    misses = ctx.state.data.get(miss_key, 0) + 1
    ctx.state.data[miss_key] = misses
    debug_log.log(
        "golem crafting",
        f"couldn't walk to ({tile['x']}, {tile['y']}) near {station['name']} plinth "
        f"(miss {misses}/{GOLEM_APPROACH_MISS_LIMIT}) -- hopping closer",
    )
    if misses >= GOLEM_APPROACH_MISS_LIMIT:
        # Flag it rather than deciding here: whether an unreachable approach tile is fatal depends
        # on whether a golem is already part-carved, which only craft_phase knows. LIVE HALT
        # 2026-08-24 03:37 stopped the session outright on target (2597,2257) -- and /ground/walk
        # reported that same tile onScreen at (1150,621) moments later, i.e. it was transient.
        ctx.state.data["gc_approach_exhausted"] = (
            f"couldn't reach a tile near {station['name']} plinth after "
            f"{GOLEM_APPROACH_MISS_LIMIT} attempts; target ({tile['x']}, {tile['y']}, "
            f"plane {tile.get('plane', 0)})"
        )
        ctx.state.data[miss_key] = 0
        return False
    _hop_toward_point(ctx, station["x"], station["y"], station.get("plane", 0))
    return False


def _handle_approach_exhausted(ctx, station: dict, fresh_golem: bool) -> None:
    """Decide what an exhausted approach means, now that _walk_to_station_tile only reports it.

    Fresh plinth: nothing is committed, and the condition has been observed transient (see that
    function's own note), so back off and come back. Part-carved: its 4 sunstone are already
    spent and abandoning the golem destroys them, so stop loudly instead."""
    reason = ctx.state.data.pop("gc_approach_exhausted", None)
    if not reason:
        return
    if fresh_golem:
        _block_station(ctx, station)
        return
    ctx.state.trigger_safety_stop(reason)


def _await_side_done(station_name: str, side: str, timeout_s: float, already_done: bool = False,
                     ctx=None, since=None) -> bool:
    """Poll /golem until `side` shows a genuine False -> True TRANSITION on its `done` varbit, or
    timeout. True only on that transition.

    This replaces the old 15s chat-message wait. The varbit flips on the tick the server
    processes the action, so a real success is seen almost immediately and a timeout genuinely
    means the click did not land -- most often a deliberate MISTAKE_MISCLICK_CHANCE near-miss,
    which produces no game action at all (see CLAUDE.md). Retrying is safe precisely because the
    varbit says unambiguously whether the previous click counted.

    `already_done` is the caller's own pre-click snapshot of this exact same field (craft_phase
    passes `station["sides"][side]["done"]` as read BEFORE the click went out). FINDING A
    (2026-08-20 whole-branch review): an ABSOLUTE-state read of "done == True" is not evidence a
    click did anything if that was already true beforehand -- the same reasoning
    _await_golem_complete applies to `progress`/`totalCrafted` below, generalized here. In
    practice craft_phase's own carve_plan filtering means the one real call site never actually
    passes already_done=True (plan only ever contains sides that already read not-done), so this
    is a defensive assertion against a future call site or a stale/desynced pre-click read, not a
    behavior change on the current path: if it ever does fire, no post-click reading can prove
    anything and this correctly always reports failure instead of a false positive.

    FINDING D (2026-08-20 whole-branch review): also extends its own deadline, up to the
    GOLEM_SIDE_MAX_WAIT_S hard cap, for as long as `side`'s own `progress` value keeps climbing
    between polls -- see GOLEM_SIDE_TIMEOUT_S's own comment for why a fixed short timeout alone
    risks charging a miss against a carve that is simply still animating. Only a stalled,
    unchanged progress value past the (possibly-extended) deadline is treated as a real miss.

    IMPORTANT FIX 2026-08-20 (code review): a None from get_golem() mid-poll `continue`s rather
    than returning False -- a single dropped request (the same transient timeout
    GOLEM_STATE_MISS_LIMIT tolerates in craft_phase) used to abandon the poll early and score it
    as a genuine miss, which could charge a spurious gc_side_misses tick against an action that
    actually succeeded."""
    if already_done:
        return False
    # 2026-09-06: the side's own state and progress varbits are pushed as VARBIT_CHANGED, so
    # each poll slice ends the instant one lands and /golem is read right then; without a
    # stream the slice is the old GOLEM_CONFIRM_POLL_S sleep, byte for byte. Fidgets between
    # slices (position-invariant) keep an ~11.8s carve from being a frozen cursor.
    events = _events_of(ctx)
    signals = {"VARBIT_CHANGED": outcome_waits.varbit_in(
        (VB_SIDE_STATE[station_name][side], VB_SIDE_PROGRESS[station_name][side]))}
    mark = since if since is not None else (events.mark() if events is not None else None)
    hard_deadline = time.time() + GOLEM_SIDE_MAX_WAIT_S
    deadline = min(time.time() + timeout_s, hard_deadline)
    last_progress = None
    next_fidget = time.time() + outcome_waits.still_segment_s()
    while time.time() < deadline:
        if events is None:
            time.sleep(GOLEM_CONFIRM_POLL_S)
        else:
            mark = _wait_slice(events, mark, signals, GOLEM_CONFIRM_POLL_S)
        state = controller.get_golem()
        if state is None:
            continue
        station = _station_by_name(state, station_name)
        if station is None:
            continue
        side_state = station["sides"][side]
        if side_state.get("done"):
            return True
        progress = side_state.get("progress", 0)
        if last_progress is not None and progress > last_progress:
            # Genuinely still carving -- push the deadline back out instead of letting a slow but
            # real carve get charged as a miss, capped by hard_deadline so a side that climbs
            # forever without ever finishing still terminates.
            deadline = min(time.time() + timeout_s, hard_deadline)
        last_progress = progress
        if events is not None and time.time() >= next_fidget and deadline - time.time() > 1.0:
            controller.fidget_once()
            next_fidget = time.time() + outcome_waits.still_segment_s()
    return False


def _await_golem_complete(
    station_name: str, timeout_s: float, before_total_crafted: int, before_progress: int,
    ctx=None, since=None
) -> bool:
    """Poll /golem until a genuine completion TRANSITION is observed -- `totalCrafted`
    incrementing past `before_total_crafted`, or the station's own `progress` going from
    `before_progress` (expected non-zero -- see craft_phase's own pre-click snapshot) back to 0.
    True only on one of those two transitions; a reading that merely matches the pre-click
    baseline never counts, no matter how many polls repeat it.

    FINDING A (2026-08-20 whole-branch review): the previous version tested ABSOLUTE state
    (`progress == 0`) rather than a transition. That is unsafe on its own: this function's first
    poll fires GOLEM_CONFIRM_POLL_S after the click, which can easily still be BEFORE the server
    has processed the click at all, so it can observe the very same pre-click reading and report a
    golem complete that never was. Snapshotting both `totalCrafted` and the station's `progress`
    immediately before the Insert-core click (craft_phase does this) and requiring an actual
    change from either baseline -- the design spec's own two clauses, see
    docs/superpowers/specs/2026-08-07-golem-crafting-design.md -- closes this: a poll that merely
    matches the pre-click reading can never satisfy either condition.

    See _await_side_done's docstring for why a None from get_golem() mid-poll continues polling
    (IMPORTANT FIX 2026-08-20) rather than returning False immediately."""
    # 2026-09-06: GOLEM_CRAFTING_COUNT and the station's own progress varbit are the pushed
    # signals; the two-transition rule on /golem stays the truth (see _await_side_done).
    events = _events_of(ctx)
    signals = {"VARBIT_CHANGED": outcome_waits.varbit_in((VB_CRAFTING_COUNT, VB_STATION[station_name]))}
    mark = since if since is not None else (events.mark() if events is not None else None)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if events is None:
            time.sleep(GOLEM_CONFIRM_POLL_S)
        else:
            mark = _wait_slice(events, mark, signals, GOLEM_CONFIRM_POLL_S)
        state = controller.get_golem()
        if state is None:
            continue
        if state.get("totalCrafted", before_total_crafted) > before_total_crafted:
            return True
        station = _station_by_name(state, station_name)
        if station and before_progress != 0 and station.get("progress", 0) == 0:
            return True
    return False


def _near_station_tile(item: dict, station: dict) -> bool:
    return (
        abs(item.get("x", 9999) - station["x"]) <= 1
        and abs(item.get("y", 9999) - station["y"]) <= 1
    )


def _collect_golem_gift(ctx, last_station: dict, item_name) -> bool:
    """Pick up the gift a gem-bag-full completion dropped near `last_station`, retrying up to
    GOLEM_GEM_PICKUP_ATTEMPTS times with a FRESH `/ground-items` re-check each attempt. Returns
    True once the item is confirmed gone from the ground (a genuine pickup, or it was already
    gone before this ever ran), False if it's still there after every attempt is exhausted.

    FINDING C (2026-08-20 whole-branch review): replaces a single click whose return value was
    discarded and whose candidates were filtered by an EXACT lowercased name match against
    on_event's own chat-parsed string. Two ways that silently lost loot: the parsed name can
    legitimately mismatch the ground item's real name (a golem awarding "2 x Uncut rubies" parses
    to "uncut rubies", but the ground item is "Uncut ruby" -- zero candidates, loop body never
    runs), or the single click can hit the deliberate ~4.5% MISTAKE_MISCLICK_CHANCE near-miss (see
    CLAUDE.md -- intentional humanisation, not a bug, but code reacting to a click's outcome must
    tolerate it). Either way the caller used to clear the flag and bank unconditionally, leaving
    the gem to despawn. This is the ONLY path by which loot can be permanently lost, and the
    code it replaced retried three times with a fresh ground re-check for exactly this reason --
    see GOLEM_GEM_PICKUP_ATTEMPTS's own comment.

    A name-filter miss (candidates by name empty, but something IS sitting on the station tile)
    falls back to a position-only match rather than giving up -- logged loudly so a real name
    mismatch is visible in the log, but still recoverable instead of silently abandoning the item.
    """
    name_lower = item_name.lower() if item_name else None
    for attempt in range(1, GOLEM_GEM_PICKUP_ATTEMPTS + 1):
        near = [i for i in controller.get_ground_items(fresh=True) if _near_station_tile(i, last_station)]
        if not near:
            # Nothing left near the plinth -- either this attempt's predecessor already picked it
            # up, or (on the very first attempt) there was never anything to collect.
            return True

        candidates = [i for i in near if name_lower is None or (i.get("name") or "").lower() == name_lower]
        if not candidates:
            debug_log.log(
                "golem crafting",
                f"gem-bag-full pickup: no ground item named {item_name!r} near "
                f"{last_station['name']} plinth (found {[i.get('name') for i in near]}) -- "
                "falling back to a position-only match",
            )
            candidates = near

        target = candidates[0]
        debug_log.log(
            "golem crafting",
            f"gem-bag-full pickup attempt {attempt}/{GOLEM_GEM_PICKUP_ATTEMPTS}: {target.get('name')}",
        )
        refresh = controller.ground_item_refresh_target(
            target.get("x"), target.get("y"), target.get("plane", 0), target.get("id"),
        )
        controller.click_menu_action(ctx.ser, target, "take", target_hint=target.get("name"),
                                      offset=(0, 0), refresh_target=refresh,
                                      refresh_legs=GOLEM_CLICK_REFRESH_LEGS)
        controller.human_delay(0.6, 1.0)

    still_there = any(
        _near_station_tile(i, last_station) for i in controller.get_ground_items(fresh=True)
    )
    if still_there:
        debug_log.log(
            "golem crafting",
            f"gem-bag-full pickup: gave up after {GOLEM_GEM_PICKUP_ATTEMPTS} attempts -- item "
            f"likely lost near {last_station['name']} plinth",
        )
    return not still_there


def craft_phase(ctx, config: dict) -> None:
    """Carve and complete one golem per call, driven entirely by /golem's varbits.

    REWRITTEN 2026-08-20 (see docs/superpowers/specs/2026-08-20-golem-crafting-varbit-overhaul-
    design.md). The previous implementation tracked carved sides in ctx.state.data, which resets
    on every process restart and every edit to this file while the plinth's real progress
    persists server-side. That desync was absorbed by a 16-attempt retry budget, camera nudges
    and eventually abandoning the golem -- and since Start-golem's 4 sunstone are spent up front,
    each abandonment destroyed a full golem's worth of material. Reading each side's `done`
    varbit removes the desync outright, so none of that machinery is needed.

    Three savings per golem come with it: the carve order ends on the insert side (no extra walk
    before Insert-core), confirmation is a varbit poll rather than a 15s chat timeout, and a
    completed plinth is left immediately for the other one rather than waited on (its gem goes
    straight to the gem bag, so there is nothing to pick up -- see the gem-bag handling).

    Fur is the one exception -- it is NOT one of /golem's exposed state variables, so
    GOLEM_CHAT_NOT_ENOUGH_FUR (captured by on_event into the "gc_fur_exhausted" flag) remains the
    only authoritative signal that an Insert-core attempt was rejected for lack of fur, checked
    both before attempting an insert and right after one fails to confirm (see below).

    IMPORTANT FIX 2026-08-20 (code review): a None from get_golem() -- api_get returns None on
    ANY exception, including its own 0.3s timeout, which CLAUDE.md documents this exact routine
    blowing under load -- now tolerates GOLEM_STATE_MISS_LIMIT consecutive misses before
    safety-stopping, the same tolerance handle_missing_target's own poll layer gives every other
    lookup in this file, rather than asserting "rebuild the client" off a single transient blip."""
    state = controller.get_golem()
    if state is None:
        misses = ctx.state.data.get("gc_golem_state_misses", 0) + 1
        ctx.state.data["gc_golem_state_misses"] = misses
        if misses < GOLEM_STATE_MISS_LIMIT:
            debug_log.log(
                "golem crafting",
                f"/golem unavailable (miss {misses}/{GOLEM_STATE_MISS_LIMIT}) -- retrying, "
                "likely a transient bridge timeout",
            )
            return
        ctx.state.trigger_safety_stop(
            f"GET /golem unavailable for {GOLEM_STATE_MISS_LIMIT} consecutive attempts -- "
            "either a transient bridge issue that never cleared, or the client needs "
            "rebuilding/restarting (./gradlew.bat run) so GolemProvider is loaded"
        )
        return
    ctx.state.data["gc_golem_state_misses"] = 0

    # CRITICAL FIX 2026-08-20 (code review): must run here, at the very top -- not at the end of
    # the completion that sets the flag, where the original version of this block lived. on_event
    # only drains queued UDP events once per main-loop iteration, strictly BEFORE tick() runs (see
    # controller.py's poll_udp/main()), and _await_golem_complete blocks *inside this same
    # function* on a plain time.sleep poll that never drains UDP. So the "leaves a gift on the
    # ground" message announcing a golem's completion cannot reach on_event -- and therefore
    # cannot set gc_gem_bag_full -- until the NEXT craft_phase call, one iteration after the
    # completion that set gc_last_station. By the time this flag is finally visible, `station`
    # local to a same-call check would already be whatever choose_station picks next -- which, by
    # design, alternates to the OTHER plinth -- so searching around a freshly-chosen `station`
    # instead of gc_last_station would look at the wrong tile, find nothing, clear the flag, and
    # bank with the real gem still sitting uncollected at the plinth that actually dropped it.
    # That is exactly the permanent loot loss this whole mechanism exists to prevent.
    #
    # Checked here, before choose_station or any walk runs, the player is still standing exactly
    # where the completing call left them (that call's only remaining actions after completion
    # are bookkeeping and a return -- nothing moves), so gc_last_station's own coordinates are
    # still the correct place to search, and on_event has always already drained the message by
    # the time this call starts. gc_last_station can be absent on a fresh start or a process
    # restart that lost ctx.state.data after the flag was set server-side-adjacent; in that case
    # there is nothing to search against, so the flag is cleared without attempting a pickup
    # rather than crashing on a None station.
    if ctx.state.data.get("gc_gem_bag_full"):
        last_station = _station_by_name(state, ctx.state.data.get("gc_last_station"))
        item_name = ctx.state.data.get("gc_gem_bag_full_item")
        if last_station is None:
            debug_log.log(
                "golem crafting",
                "gem bag full but no gc_last_station to search (fresh start/restart) -- "
                "clearing without a pickup",
            )
        else:
            debug_log.log("golem crafting", "gem bag full -- collecting the dropped gift and banking")
            _collect_golem_gift(ctx, last_station, item_name)
        ctx.state.data["gc_gem_bag_full"] = False
        ctx.state.data["gc_gem_bag_full_item"] = None
        ctx.state.data["gc_phase"] = "bank"
        return

    stations = state.get("stations", [])
    # CRITICAL FIX 2026-08-20 (code review): GolemProvider reports both stations unconditionally,
    # regardless of whether the player is actually in the golem-crafting area or anywhere near
    # either plinth (confirmed by review) -- `not stations` can therefore never be true on its
    # own, and used to make handle_missing_target's fallback below permanently dead code. Gate on
    # state that actually varies instead: `inArea` (exposed by GolemProvider, unused until now)
    # catches "not even in the area", and _any_station_visible catches "in the area, but nothing
    # currently projects onto the viewport" -- the exact condition that used to fall through to a
    # byte-identical no-op return every tick, forever. `not stations` is kept as a defensive extra
    # condition in case that provider guarantee ever changes, not as the real gate.
    #
    # PERFORMANCE FIX 2026-08-20 (code review): /objects is resolved exactly once here and
    # threaded through both this gate (_any_station_visible) and the click path below
    # (_click_station_tracked -> _click_station/_find_station_object), rather than each fetching
    # its own -- controller.py's own comment documents /objects measuring 390-420ms in Wyrmscraig
    # (this routine's own location, ~2900 objects in radius) versus under 20ms for every other
    # bridge endpoint, so paying for it twice per tick directly worked against this routine's
    # single biggest complaint (speed).
    objects = controller.get_objects(radius=MAX_OBJECT_DISTANCE)
    if not stations or not state.get("inArea", True) or not _any_station_visible(stations, objects):
        if handle_missing_target(ctx, config, "plinth", "gc_plinth_misses", "gc_plinth_walks"):
            return
        ctx.state.trigger_safety_stop("golem crafting area/plinths not found nearby")
        return
    ctx.state.data["gc_plinth_misses"] = 0
    ctx.state.data["gc_plinth_walks"] = 0

    player = controller.get_player()
    player_x, player_y = player.get("x", 0), player.get("y", 0)
    station = choose_station(
        stations, ctx.state.data.get("gc_last_station"), player_x, player_y,
        blocked=_blocked_station_names(ctx)
    )
    fresh_golem = station.get("progress", 0) == 0

    if fresh_golem:
        raw = _count_named(RAW_SUNSTONE_NAME)
        cores = _count_named(SUNSTONE_CORE_NAME)
        if not has_start_materials(raw, cores):
            fur_stock = ctx.state.data.get("gc_fur_stock", 0)
            if fur_stock < CORES_PER_TRIP:
                debug_log.log("golem crafting", f"batch done ({raw} sunstone, {cores} cores), fur low ({fur_stock}) -- banking")
                ctx.state.data["gc_phase"] = "bank"
            else:
                debug_log.log("golem crafting", f"batch done ({raw} sunstone, {cores} cores), fur ok ({fur_stock}) -- mining")
                ctx.state.data["gc_phase"] = "mine"
            return
        if ctx.state.data.get("gc_fur_stock", 0) <= 0:
            debug_log.log("golem crafting", "out of usable fur for a new golem -- moving to bank phase")
            ctx.state.data["gc_phase"] = "bank"
            return

    plan = carve_plan(station, player_x, player_y,
                      avoid=_sides_to_avoid(ctx.state.data.get("gc_side_fail_counts"),
                                            station["name"]))

    # FINDING B (2026-08-20 whole-branch review): fresh_golem (progress == 0, i.e. no golem in
    # progress on this plinth) and an EMPTY carve plan (every side's own `done` reads True) are
    # mutually exclusive by design -- a golem that hasn't started can't already have any side
    # carved. Nobody has verified live whether the per-side varbits actually get cleared when a
    # golem completes; if they don't, this exact contradictory combination is how that would
    # first show up. Left unguarded, this falls straight through to the Insert-core block below
    # and left-clicks a plinth whose default action in a truly fresh state is Start-golem, not
    # Insert-core -- silently spending 4 raw sunstone on a phantom "insert" that (with FINDING A
    # unfixed) also used to report a phantom completion and loop forever, burning 4 more sunstone
    # every lap with zero slack in the trip budget to absorb it. Treat it as the impossible state
    # it is: stop with the full station dict on record rather than guessing.
    if fresh_golem and not plan:
        ctx.state.trigger_safety_stop(
            f"golem crafting: {station['name']} plinth reads progress == 0 (fresh golem) but "
            "every side's `done` varbit already reads True -- contradictory state that should "
            "be impossible; if this fires live it most likely means the per-side varbits are "
            f"NOT cleared when a golem completes. station state: {station}"
        )
        return

    if plan:
        side = plan[0]
        tile = station["sides"][side]
        # Fire-and-forget: the plinth click below goes out while the player is still walking,
        # which is what a human does -- MEASURED at 0.6-1.0s between the walk click and the object
        # click, 4 times out of 4, in recordings/2026-08-23_194302_golem-carving. The game queues
        # the interaction and carves the side we are walking TO (confirmed 3/3, see
        # _walk_to_station_tile's docstring), so waiting to arrive first buys no correctness at
        # all -- it only adds latency, and the block is what made an over-tight
        # GOLEM_SIDE_TIMEOUT_S destructive rather than merely slow.
        if not _walk_to_station_tile(ctx, station, tile, "gc_side_walk_misses",
                                     wait_for_arrival=False):
            _handle_approach_exhausted(ctx, station, fresh_golem)
            return
        verb = "starting golem" if fresh_golem else "shaping golem"
        debug_log.log("golem crafting", f"{verb} from {side} side of {station['name']} plinth ({4 - len(plan)}/4 done)")
        controller.human_delay(*GOLEM_WALK_TO_CLICK_GAP_S)
        # The mark precedes the click so the side's own varbit event (and the MENU_CLICKED echo)
        # are the ones matched, never a previous side's.
        mark = outcome_waits.mark(ctx)
        before_action = controller.get_last_action()
        # A side whose bare click already got hijacked once goes straight to the menu route --
        # the cheap click has been shown not to work against whatever is sitting on that pixel.
        escalate = ctx.state.data.get("gc_side_hijacks", 0) >= GOLEM_FAST_RETRY_BUDGET
        if escalate:
            debug_log.log("golem crafting",
                          f"{side} side: bare click was hijacked -- selecting the action from the "
                          f"right-click menu instead")
        res = _click_station_tracked(ctx, station, "gc_side_click_misses", objects,
                                     use_menu=escalate)
        withheld = (not res) and getattr(res, "withheld", False)
        if not res and not withheld:
            return
        # Ask the game what that click actually resolved to before committing to the full
        # confirmation wait. A bare left-click is resolved against whatever sits on top of the
        # aim pixel, and another PLAYER standing there turns it into "Walk here" on them --
        # CONFIRMED live 2026-08-23, see _click_resolved_wrongly. Retrying immediately costs one
        # tick; waiting it out costs GOLEM_SIDE_TIMEOUT_S for a carve that never started.
        # A WITHHELD click (added 2026-08-27) is the same signal caught BEFORE the click by
        # hover verification -- no wasted click, no ~1.5s echo wait -- so it feeds the identical
        # escalation below. Every downstream bound still holds: first withhold spends the fast
        # retry, the second escalates to the menu route, and if THAT falls back and is withheld
        # again (the option genuinely absent -- the out-of-fur shape) the flow drops into
        # _await_side_done's timeout exactly as before, so gc_side_misses still climbs toward
        # the out-of-fur recovery instead of spinning.
        hijacked = withheld or _await_click_echo(before_action, station["objectId"], ctx=ctx, since=mark) is True
        already_retried = ctx.state.data.get("gc_side_hijacks", 0) >= GOLEM_FAST_RETRY_BUDGET
        if hijacked and not already_retried:
            ctx.state.data["gc_side_hijacks"] = ctx.state.data.get("gc_side_hijacks", 0) + 1
            debug_log.log(
                "golem crafting",
                f"{side} side click was hijacked (resolved to another target) -- retrying now",
            )
            return
        if hijacked and not escalate:
            # REGRESSION FIX 2026-08-23, caught live within minutes of deploying the fast path:
            # retrying immediately re-clicks essentially the same pixel, because nothing about the
            # geometry has changed in ~1.6s. The routine locked into a 20-attempt loop on the north
            # plinth's east side, clicking (1093,766) every time while the clickbox centre sat at
            # (1100,759) -- and the same tile and object had carved fine three minutes earlier.
            # The old timeout wait turned out to be accidentally load-bearing: it gave the camera
            # and the world time to drift, so each retry aimed somewhere slightly different and
            # eventually connected. Falling through to it here is what supplies that drift, and it
            # makes a spin structurally impossible rather than merely bounded.
            debug_log.log(
                "golem crafting",
                f"{side} side click was hijacked again -- falling back to the full wait so the "
                f"camera and the world can move before the next attempt",
            )
        else:
            ctx.state.data["gc_side_hijacks"] = 0
        if not _await_side_done(station["name"], side, GOLEM_SIDE_TIMEOUT_S,
                                already_done=station["sides"][side]["done"], ctx=ctx, since=mark):
            debug_log.log("golem crafting", f"{side} side didn't register within {GOLEM_SIDE_TIMEOUT_S}s -- retrying next tick")
            # Remember WHICH side failed, so a start side we cannot actually click from stops
            # being chosen (see choose_start_side's `avoid`). Three live halts came from
            # re-aiming at the same unusable pixel until the miss limit ran out.
            fails = dict(ctx.state.data.get("gc_side_fail_counts") or {})
            key = f"{station['name']}:{side}"
            fails[key] = fails.get(key, 0) + 1
            ctx.state.data["gc_side_fail_counts"] = fails
            ctx.state.data["gc_side_misses"] = ctx.state.data.get("gc_side_misses", 0) + 1
            if ctx.state.data["gc_side_misses"] >= GOLEM_SIDE_MISS_LIMIT:
                if fresh_golem:
                    # ROOT CAUSE (user, 2026-08-24): **Start-golem is not offered at all when you
                    # have no fur.** Sunstone and cores are in hand -- has_start_materials just
                    # checked them -- so the only remaining ingredient is fur, and the game says
                    # nothing because the option is simply absent rather than rejected. That is
                    # why the right-click menu showed only ['Cancel','Mark Safespot','Examine',
                    # 'Walk here'], why BOTH plinths refused at once (fur is inventory-wide), why
                    # it only ever struck at progress 0 (a part-carved golem shapes fine without
                    # fur), and why a restart appeared to fix it (a restart forces a bank trip).
                    #
                    # gc_fur_stock cannot be trusted here: this file's own comments record that it
                    # is a local estimate which can "read >0 while the pouch is genuinely empty",
                    # and its one authoritative correction (GOLEM_CHAT_NOT_ENOUGH_FUR) only ever
                    # fires on Insert-core. So zero it and go restock -- bank_phase refills from
                    # the bank, and falls through to hunt_phase when the bank is empty too.
                    debug_log.log(
                        "golem crafting",
                        f"{station['name']} plinth will not start a golem though sunstone and "
                        f"cores are in hand -- out of fur, going to bank",
                    )
                    ctx.state.data["gc_fur_stock"] = 0
                    ctx.state.data["gc_side_misses"] = 0
                    ctx.state.data["gc_phase"] = "bank"
                    return
                # A golem already part-carved is different: its 4 sunstone are spent and
                # abandoning it destroys them, so a stuck side there stays a hard stop.
                ctx.state.trigger_safety_stop(
                    f"{station['name']} plinth's {side} side never registered after "
                    f"{GOLEM_SIDE_MISS_LIMIT} attempts; station state: {station}"
                )
            return
        ctx.state.data["gc_side_misses"] = 0
        ctx.state.data["gc_block_streak"] = 0
        # This side worked, so it is not a bad angle -- clear its streak rather than letting an
        # old run of failures keep steering us away from a side that is fine now.
        fails = dict(ctx.state.data.get("gc_side_fail_counts") or {})
        fails.pop(f"{station['name']}:{side}", None)
        ctx.state.data["gc_side_fail_counts"] = fails
        return

    # All four sides carved -- insert the core. The carve order ended here on a fresh golem, so
    # this walk is normally a no-op; it only actually moves on a resumed golem whose insert side
    # was already carved before this session started.
    insert_side = station["insertSide"]
    tile = station["sides"][insert_side]
    if not _walk_to_station_tile(ctx, station, tile, "gc_insert_walk_misses"):
        _handle_approach_exhausted(ctx, station, fresh_golem=False)
        return

    if find_item(SUNSTONE_CORE_NAME) is None:
        debug_log.log("golem crafting", "sunstone core went missing before inserting -- moving to mine phase")
        ctx.state.data["gc_phase"] = "mine"
        return
    if ctx.state.data.get("gc_fur_stock", 0) <= 0:
        debug_log.log("golem crafting", "fur ran out before inserting -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        return
    if ctx.state.data.get("gc_fur_exhausted"):
        # Checked before every insert attempt, not just after one fails -- see this function's
        # own docstring and on_event's handling of GOLEM_CHAT_NOT_ENOUGH_FUR for why this flag,
        # not gc_fur_stock, is the authoritative signal. Without this check a stale-positive
        # gc_fur_stock (never corrected against what hunt_phase actually catches) would make
        # every Insert-core attempt fail and retry forever, the exact unbounded-loop bug class
        # this overhaul exists to remove.
        debug_log.log("golem crafting", "game already rejected insert-core for lack of fur -- moving to bank phase")
        ctx.state.data["gc_fur_stock"] = 0
        ctx.state.data["gc_fur_exhausted"] = False
        ctx.state.data["gc_insert_misses"] = 0
        ctx.state.data["gc_phase"] = "bank"
        return

    debug_log.log("golem crafting", f"inserting core + fur from {insert_side} side of {station['name']} plinth")
    # FINDING A (2026-08-20 whole-branch review): snapshotted immediately BEFORE the click, not
    # after -- _await_golem_complete needs the genuine pre-click baseline to tell a real
    # completion apart from a poll that just happens to observe the same state the click started
    # from. See that function's own docstring.
    before_total_crafted = state.get("totalCrafted", 0)
    before_progress = station.get("progress", 0)
    # Same escalation the carve step gets. LIVE HALT 2026-08-24 02:17: once carving was fixed the
    # identical hijack simply moved here, and Insert-core had no defence -- the click positions
    # alternated between (1118,653) and (1217,764) over five attempts, i.e. the click resolved to
    # "Walk here", the player moved, the next tick walked back, oscillating until the miss limit
    # ran out with all four sides carved and a golem's materials stranded on the plinth.
    insert_escalate = ctx.state.data.get("gc_insert_hijacks", 0) >= GOLEM_FAST_RETRY_BUDGET
    if insert_escalate:
        debug_log.log("golem crafting",
                      "insert click was hijacked -- selecting Insert-core from the right-click "
                      "menu instead")
    mark = outcome_waits.mark(ctx)
    before_action = controller.get_last_action()
    res = _click_station_tracked(ctx, station, "gc_insert_click_misses", objects,
                                 use_menu=insert_escalate)
    insert_withheld = (not res) and getattr(res, "withheld", False)
    if not res and not insert_withheld:
        return
    # Same fold as the carve step (2026-08-27): a withheld click is the hijack detected BEFORE
    # the click by hover verification, so it spends the same budget -- and past the budget it
    # falls through to _await_golem_complete's timeout and the existing miss counting, bounded
    # exactly as before.
    if (insert_withheld or _await_click_echo(before_action, station["objectId"], ctx=ctx, since=mark) is True) \
            and not insert_escalate:
        ctx.state.data["gc_insert_hijacks"] = ctx.state.data.get("gc_insert_hijacks", 0) + 1
        debug_log.log("golem crafting",
                      "insert click was hijacked (resolved to another target) -- retrying now")
        return
    ctx.state.data["gc_insert_hijacks"] = 0
    if not _await_golem_complete(
        station["name"], GOLEM_INSERT_TIMEOUT_S, before_total_crafted, before_progress,
        ctx=ctx, since=mark
    ):
        if ctx.state.data.get("gc_fur_exhausted"):
            # IMPORTANT FIX 2026-08-20 (code review): this branch cannot actually fire under the
            # current single-threaded event dispatch -- on_event only runs from the main loop
            # between ticks, never while craft_phase is blocked inside _await_golem_complete's own
            # sleep loop, and the pre-insert check above already consumes the flag first on the
            # very next call. Kept anyway as cheap defence per Ruling 1 (both checks were asked
            # for) in case that dispatch assumption ever changes, not because it's expected to
            # trigger today.
            debug_log.log("golem crafting", "insert-core rejected -- out of fur, moving to bank phase")
            ctx.state.data["gc_fur_stock"] = 0
            ctx.state.data["gc_fur_exhausted"] = False
            ctx.state.data["gc_insert_misses"] = 0
            ctx.state.data["gc_phase"] = "bank"
            return
        debug_log.log("golem crafting", f"golem didn't complete within {GOLEM_INSERT_TIMEOUT_S}s -- retrying next tick")
        # Same reasoning as gc_side_misses above -- bounded rather than unbounded, since with
        # ground truth available a stuck insert is a genuine anomaly, not desync to absorb.
        ctx.state.data["gc_insert_misses"] = ctx.state.data.get("gc_insert_misses", 0) + 1
        if ctx.state.data["gc_insert_misses"] >= GOLEM_INSERT_MISS_LIMIT:
            ctx.state.trigger_safety_stop(
                f"Insert-core on {station['name']} plinth never completed after "
                f"{GOLEM_INSERT_MISS_LIMIT} attempts; station state: {station}"
            )
        return

    debug_log.log("golem crafting", f"golem finished on {station['name']} plinth")
    # Alternate: record which plinth just finished so choose_station picks the other one next
    # call, rather than waiting out this one's reset.
    ctx.state.data["gc_last_station"] = station["name"]
    # This bookkeeping runs unconditionally, regardless of where the gift landed -- the golem is
    # confirmed complete by this point, so it consumed its fur either way. gc_fur_stock has no
    # game-state fallback to self-correct a skipped decrement against (that's exactly why
    # gc_fur_exhausted exists as a backstop), and gc_insert_misses carrying over would drift
    # toward a false safety-stop on the next golem. See the gem-bag-full check near the top of
    # this function (CRITICAL FIX 2026-08-20) for why that check can no longer live here.
    ctx.state.data["gc_fur_stock"] = max(0, ctx.state.data.get("gc_fur_stock", 0) - 1)
    ctx.state.data["gc_insert_misses"] = 0


# Never deposited during a golem-crafting bank trip -- the pouch/tools are needed for the next
# mine/chisel/craft cycle, and leftover raw sunstone/cores (if fur ran out before either did)
# need to survive the trip so the next craft_phase call can still use them.
#
# CORRECTED 2026-08-08 (live session, user report): the Hammer and the open gem bag were
# getting deposited every bank trip along with the raw sunstone -- CONFIRMED live, both are
# meant to stay in inventory permanently (the gem bag auto-catches every uncut gem a finished
# golem drops, same as fur pouch/coal bag's own "keep it, don't bank it" treatment elsewhere in
# this codebase; the hammer is part of the player's fixed loadout). "fur" (a substring of every
# FUR_PRIORITY name) is also kept defensively -- craft_phase consumes fur straight from the
# pouch now (see that function's own comment) so there normally shouldn't be any loose fur at
# deposit time at all, but this is a cheap backstop against losing any that ends up loose anyway
# (e.g. caught while hunting before the pouch is opened).
#
# CORRECTED 2026-08-08 (live session, user report): raw sunstone itself wasn't on this list --
# harmless as long as bank_phase was ONLY ever reached once craft_phase's own end-of-batch check
# had already confirmed raw sunstone (or cores) was gone (its original, only intended path), but
# CONFIRMED live that's no longer the only path: a fur shortage alone (find_available_fur_in_bank
# finding nothing usable, or the fur pouch's fill/empty step silently not landing) also routes to
# "bank" via that same end-of-batch branch, and can do so with raw sunstone and cores still very
# much unused -- depositing them there would strand a half-supplied batch and force mining a
# fresh 20 from scratch next trip for no reason. Kept unconditionally now, the same as cores.
BANK_KEEP_ITEMS = [
    banker.FUR_POUCH_NAME, CHISEL_NAME.lower(),
    RAW_SUNSTONE_NAME.lower(), SUNSTONE_CORE_NAME.lower(),
    "hammer", "gem bag", "fur", "rune pouch",
]

# CORRECTED 2026-08-08 (live session, user request): fur is withdrawn in FULL (Withdraw-All,
# not a capped budget) and immediately compacted into the fur pouch (large: 28 capacity, so no
# overflow risk taking the whole stack) rather than carried loose. This replaces the original
# design's "a fixed inventory-slot budget (e.g. 14, half an inventory)" -- that budget existed
# specifically because loose fur was competing with raw sunstone for inventory slots during
# mining; pouching removes that competition entirely (fur costs exactly 1 slot -- the pouch
# itself -- regardless of how much is stocked), so there's no longer a reason to under-withdraw.
# REWRITTEN 2026-08-20: never unpacked at all -- Insert-core (see craft_phase) consumes its 1 fur
# straight from the still-packed pouch in the same click that consumes the core. This enables:
# only detour to the bank when the tracked "gc_fur_stock" count actually runs low
# (< CORES_PER_TRIP), not every single batch the way an always-empty-after-banking design would.


def find_available_fur_in_bank(bank: dict) -> str:
    """Highest-priority (per FUR_PRIORITY) fur type with qty > 0 in the open bank, or None."""
    bank_items = {(i.get("name") or "").lower(): i.get("qty", 0) for i in bank.get("items", [])}
    return next((f for f in FUR_PRIORITY if bank_items.get(f.lower(), 0) > 0), None)


# How long to wait, after the withdraw-all click, for the fur to actually show up in inventory
# before deciding the click didn't land -- same idea as CHISEL_CONFIRM_TIMEOUT_S, see this
# function's own comment on the withdrawal for what this closes.
WITHDRAW_CONFIRM_TIMEOUT_S = 3.0


def _run_bank_trip(ctx, config: dict) -> None:
    """The actual deposit/withdraw logic, run once a bank is confirmed open (by either branch
    of bank_phase below). Deposits everything except BANK_KEEP_ITEMS (uncut gems and any
    leftover raw sunstone per the design's bank-phase bullet -- anything not explicitly kept),
    then withdraws the entire stack of the best available fur type, straight into loose
    inventory slots -- bank_phase itself compacts it into the fur pouch right after this
    function returns and the bank closes (see that function's own comment for why the pouch
    fill has to happen there and not here: CONFIRMED live the pouch's right-click menu doesn't
    even offer "Fill" while a bank interface is open -- it shows Deposit-1/5/10/X/All instead --
    so this has to leave the fur loose and let bank_phase's post-close step handle compacting
    it), or falls through to the hunt phase if the bank has none of FUR_PRIORITY at all.

    CORRECTED 2026-08-08 (live session, user request): withdraws the FULL stack (Withdraw-All)
    rather than a capped budget -- pouching (once compacted, post-close) removes the old
    loose-fur-vs-sunstone inventory-slot competition entirely, so there's no reason to
    under-withdraw. gc_fur_stock is set to the amount withdrawn here (not added to whatever
    was left, since bank_phase is only ever entered once that's already run low -- see
    craft_phase's own end-of-batch check) so it stays a reasonably accurate count of how much
    fur is actually stocked for craft_phase's bank-or-mine decision after each batch.

    CORRECTED 2026-08-08 (MCP live debug session): searches the bank for "fur" ONCE instead of
    scanning the whole unfiltered bank to pick a fur type and then searching AGAIN for that
    specific name -- CONFIRMED live on this account's real (large, unsorted) bank: a bare
    unfiltered bank read reports far off-screen screenX/screenY for anything not on the first
    visible page (e.g. "Sunlight antelope fur" at screenY=5271), which the original version's
    withdraw call (banker.withdraw_loadout, clicking that raw off-screen position with no
    search step) silently failed against every time -- craft_phase would then find no fur,
    bounce back here, and repeat the same doomed click forever, no error, no safety-stop.
    Searching "fur" (a substring of every FUR_PRIORITY name) surfaces every fur type actually
    in the bank at once, laid out from slot 0 (see search_bank's docstring) -- so
    find_available_fur_in_bank can pick the best one AND get a guaranteed-on-screen position
    for it from that single filtered read, with no second per-item search needed (unlike
    banker.search_and_withdraw, which is built for a single already-chosen item name and
    searches for it fresh -- not needed here since "fur" already surfaces all of them)."""
    banker.deposit_inventory(ctx, {"keepItems": BANK_KEEP_ITEMS})

    # 2026-09-06: a fur trip comes home with the pouch full (gc_fur_stock 28) and the fur in the
    # POUCH, not the bank -- searching the bank then would find nothing and start another trip.
    # bank_phase reached with fur already stocked (a fur trip's return, a gem-bag-full visit)
    # deposits and goes straight back to mining.
    if ctx.state.data.get("gc_fur_stock", 0) >= CORES_PER_TRIP:
        debug_log.log("golem crafting", f"fur already stocked ({ctx.state.data.get('gc_fur_stock')}) -- banking done, moving to mine phase")
        ctx.state.data["gc_phase"] = "mine"
        return

    if not banker.search_bank(ctx, "fur"):
        debug_log.log("golem crafting", "bank search for fur failed -- moving to mine phase")
        ctx.state.data["gc_phase"] = "mine"
        return

    bank = controller.get_bank()
    fur_name = find_available_fur_in_bank(bank)
    if fur_name is None:
        banker.clear_bank_search(ctx)
        # ADDED 2026-08-09 (user request): raw sunstone/cores are normally kept through a bank
        # trip (see BANK_KEEP_ITEMS's own comment -- protects a half-supplied mining batch), but
        # that protection stops being worth the inventory space once there's genuinely no fur
        # anywhere and a trip is starting: the hunt needs the slots, and we're already standing
        # at the open bank right now, so banking them (not dropping -- still fully recoverable
        # next mine_phase visit) costs nothing extra.
        debug_log.log("golem crafting", "bank has none of the usable fur types -- banking spare sunstone/cores and kitting up for a falconry trip")
        trip_keep_items = [k for k in BANK_KEEP_ITEMS
                           if k not in (RAW_SUNSTONE_NAME.lower(), SUNSTONE_CORE_NAME.lower())]
        banker.deposit_inventory(ctx, {"keepItems": trip_keep_items})
        # The travel kit is withdrawn HERE, while the bank is open (fur_trip_phase's "kit" leg
        # only ever routes back to bank_phase to get here).
        if not _withdraw_kit(ctx, config):
            return
        _start_trip(ctx)
        ctx.state.data["gc_phase"] = "fur_trip"
        return

    slot = next((s for s in bank.get("items", []) if (s.get("name") or "").lower() == fur_name.lower()), None)
    qty = slot.get("qty", 0) if slot else 0

    if qty > 0:
        debug_log.log("golem crafting", f"withdrawing all {qty}x {fur_name}")
        banker.set_quantity_mode(ctx, "all")
        mark = outcome_waits.mark(ctx)
        controller.click_entity(ctx.ser, slot)
        # ADDED 2026-08-08 (live session, user report): used to trust click_entity's return value
        # alone and set gc_fur_stock = qty unconditionally -- CONFIRMED live this isn't reliable
        # enough on its own here: click_entity reported a clean landing (no misclick) with the
        # quantity-mode button click immediately before it, yet the fur never actually appeared
        # in inventory (the mode click may have landed on the wrong button after its own
        # recovery-retry, silently withdrawing something other than the full stack, or nothing at
        # all pending an "X" prompt that was never answered). gc_fur_stock then read as fully
        # stocked all the way through an entire mine/chisel batch with zero real fur behind it,
        # surfacing only as "no 'empty' entry in menu" once craft_phase finally tried to use it --
        # several minutes and a wasted mining trip later. Polling inventory for the fur to
        # actually show up closes that gap at the source instead.
        # 2026-09-06: INVENTORY_CHANGED is the signal, the fur in the inventory the truth.
        landed = bool(outcome_waits.await_pre(
            ctx, "INVENTORY_CHANGED", WITHDRAW_CONFIRM_TIMEOUT_S,
            confirm=lambda: any((i.get("name") or "").lower() == fur_name.lower()
                                for i in controller.get_inventory()),
            since=mark, reaction=False, now_fn=time.time, sleep_fn=time.sleep,
        ))
        if landed:
            ctx.state.data["gc_fur_stock"] = qty
        else:
            debug_log.log("golem crafting", f"withdraw-all on {fur_name} didn't land in inventory -- will retry next bank trip")
            ctx.state.data["gc_fur_stock"] = 0

    banker.clear_bank_search(ctx)
    debug_log.log("golem crafting", "banking done -- moving to mine phase")
    ctx.state.data["gc_phase"] = "mine"


def bank_phase(ctx, config: dict) -> None:
    """Bank near Wyrmscraig if a bank is in walking distance; otherwise teleport to Ferox
    Enclave via the ring of dueling if Teleport If No Bank Nearby is enabled. Sized like
    blast_furnace.py's run_trip / banker.bank_up (one full bounded bank visit per call).

    Fur selection needs to inspect the ALREADY-OPEN bank's actual contents (find_available_
    fur_in_bank), which banker.bank_up's single deposit+withdraw+close call can't do (it needs
    the withdraw list decided up front). So the local-bank branch below opens the bank itself
    and calls _run_bank_trip directly, same manual pattern blast_furnace.py's run_trip uses
    instead of bank_up.

    CORRECTED 2026-08-08 (live session, user request): no longer empties the fur pouch after
    closing the bank -- _run_bank_trip now deliberately fills it (see that function's own
    docstring for the pouched-fur design). REWRITTEN 2026-08-20: it is never unpacked again after
    this, by anything -- Insert-core (see craft_phase) consumes its 1 fur straight from the
    still-packed pouch. This includes fur caught while HUNTING, which routes into the pouch the
    same way (see hunt_phase) and stays there, compacted, indefinitely.

    CORRECTED 2026-08-07 (caught in task review): the Ferox teleport-fallback branch cannot
    do that same dynamic fur scan -- it has to go through banker.bank_up (its private Ferox
    path isn't meant to be called directly), so it can only ever deposit everything and
    withdraw nothing dynamic, same acknowledged limitation blast_furnace.py's own module
    header documents for the coal bag ("the Ferox teleport fallback cannot fill the coal
    bag"). An earlier version of this docstring claimed the very next bank_phase call would
    then find Ferox's own bank chest nearby and take the local-bank branch instead -- that's
    wrong: banker._bank_via_ferox walks the player straight back to wherever they started
    (via travel.walk_to, using the location saved before teleporting) once it's done, not
    left standing at Ferox, so the next call is back at square one, not next to a bank. Per
    the design doc, Wyrmscraig has its own bank chest roughly central to the crafting/mining/
    hunting areas, so the local-bank branch above is expected to be the normal path in
    practice -- landing in the Ferox branch below at all means that bank genuinely wasn't
    found nearby. Rather than loop forever depositing-and-walking-back without ever
    restocking fur, this now stops with a clear message once the Ferox round-trip completes.

    CORRECTED 2026-08-08 (MCP live debug session): walks toward the "bank" location anchor
    (via handle_missing_target, same as mine_phase/craft_phase/hunt_phase's own lookups) before
    falling through to the Ferox teleport fallback -- CONFIRMED live the Goat Pit sits ~65
    tiles from the bank, well past banker.find_nearby_bank's default 40-tile search radius, so
    without this a hunt trip's own bank_phase call would either wrongly teleport off Wyrmscraig
    entirely (if teleportFallback is enabled) or safety-stop (if not), even with the bank
    genuinely reachable on foot.
    """
    player = controller.get_player() or {}
    if (player.get("y") or 0) >= CAVERN_MIN_Y:
        # A restart (gc_phase defaults to "bank") while still underground after a slayer-ring
        # return: the bank anchor is on the surface, 6400 tiles of y away, and the collision map
        # cannot route across that. Finish the trip's cavern leg instead.
        debug_log.log("golem crafting", "in the Wyrmscraig Cavern -- resuming the fur trip's exit leg")
        _trip(ctx)["done"] = True
        ctx.state.data["gc_phase"] = "fur_trip"
        return

    bank_obj = banker.find_nearby_bank({})
    if bank_obj is None:
        if handle_missing_target(ctx, config, "bank", "gc_bank_misses", "gc_bank_walks"):
            return
        # Walk attempts toward the configured anchor exhausted -- fall through to the Ferox
        # teleport fallback below, same as this phase's original behavior before the anchor
        # existed (bank_obj stays None, so neither this branch's early-return nor the
        # gc_bank_misses/gc_bank_walks reset below run).
    else:
        if not banker.open_bank(ctx, bank_obj):
            # CORRECTED 2026-08-08 (live session): find_nearby_bank's 40-tile radius can return
            # an object that isn't actually on-screen/render-visible yet (too far to click),
            # which open_bank's click then fails against ("aim point is outside the RuneLite
            # window") -- CONFIRMED live right after a fresh routine start ~25 tiles from the
            # bank. Try a direct minimap-click hop straight at the bank object's own position
            # first (see MAX_BANK_MINIMAP_HOPS's docstring) before falling back to the anchor
            # walk and, failing that, the safety stop.
            hops = ctx.state.data.get("gc_bank_minimap_hops", 0)
            if hops < MAX_BANK_MINIMAP_HOPS:
                point = controller.get_minimap_walk_point(bank_obj.get("x"), bank_obj.get("y"), bank_obj.get("plane"))
                if point.get("onMinimap"):
                    ctx.state.data["gc_bank_minimap_hops"] = hops + 1
                    debug_log.log("golem crafting", f"bank found but off-screen -- minimap-clicking toward it (hop {hops + 1}/{MAX_BANK_MINIMAP_HOPS})")
                    controller.click_entity(ctx.ser, point, offset=(0, 0), check_ui_blockers=False)
                    controller.human_delay(0.6, 1.0)
                    return
            if handle_missing_target(ctx, config, "bank", "gc_bank_misses", "gc_bank_walks"):
                return
            ctx.state.trigger_safety_stop("failed to open the bank near Wyrmscraig even after walking toward it")
            return
        ctx.state.data["gc_bank_misses"] = 0
        ctx.state.data["gc_bank_walks"] = 0
        ctx.state.data["gc_bank_minimap_hops"] = 0
        _run_bank_trip(ctx, config)
        banker.close_bank(ctx)
        controller.human_delay(0.3, 0.6)
        # CORRECTED 2026-08-08 (live session, user correction): the fur pouch can only be
        # filled AFTER the bank closes -- CONFIRMED live its right-click menu offers no "Fill"
        # entry at all while a bank interface is open (Deposit-1/5/10/X/All instead). Compacts
        # whatever _run_bank_trip just withdrew (loose, by necessity -- see that function's own
        # docstring) into the pouch for the walk back out to mining.
        if banker.has_fur_pouch():
            banker.fill_fur_pouch(ctx)
        return

    if config.get("bank", {}).get("teleportFallback"):
        if not banker.bank_up(ctx, {"keepItems": BANK_KEEP_ITEMS, "teleportFallback": True}):
            ctx.state.trigger_safety_stop("couldn't reach a bank near Wyrmscraig, and the Ferox teleport fallback failed")
            return
        # The Ferox trip above can only deposit, never withdraw fur dynamically (see docstring),
        # and it walks the player straight back afterward rather than leaving them at a
        # findable bank -- so there is no useful next state to advance to from here. Stop
        # rather than silently repeating a deposit-only round trip forever.
        ctx.state.trigger_safety_stop(
            "no bank found near Wyrmscraig for golem crafting -- the Ferox teleport fallback can "
            "only deposit items, not withdraw fur, so it can't recover from this on its own. "
            "Check you're actually on Wyrmscraig and its bank chest is reachable."
        )
        return

    ctx.state.trigger_safety_stop("no bank found near Wyrmscraig, and Teleport If No Bank Nearby is off")


# How many drop passes to make over a junk item before accepting whatever is left. Bounded
# because this runs unattended and an item that genuinely cannot be dropped must not spin.
DROP_MAX_PASSES = 3


def _drop_all(ctx, name: str) -> None:
    """Drop every inventory item matching `name`, re-reading the inventory between passes and
    retrying whatever survived.

    USER REPORT 2026-08-24: wooden spikes were still in the inventory after a fur session. The
    drop was a single fire-and-forget pass with no verification, and two things routinely defeat
    it -- click_entity's deliberate MISTAKE_MISCLICK_CHANCE near-miss (~4.5% per click, so a
    4-spike drop leaves one behind about one time in six; CONFIRMED live 22:38, 4 clicked and 1
    survived), and any interruption mid-pass (a disconnect landed in exactly that window at
    01:28:06). Nothing retried what was left, so it sat there until some later hunt happened to
    clear it.

    Left-click-to-drop depends on "Drop" being set in Controller Settings -> Clicking -> Forced
    Actions -- the same client-side convention routines/shipwreck_salvaging.py's drop_salvage
    documents. Verified working for both Goat horn and Wooden spikes by the run's own behaviour:
    every hunt after a drop pass had to re-take spikes from the supply, which only happens when
    none are carried.

    check_ui_blockers=False for the reason drop_salvage records: an inventory item's own screen
    position is naturally inside the floating inventory panel, so the world-click blocker check
    flags every one of them and rotates the camera forever instead of clicking."""
    name_lower = name.lower()
    for _ in range(DROP_MAX_PASSES):
        remaining = [i for i in controller.get_inventory()
                     if name_lower in (i.get("name") or "").lower()]
        if not remaining:
            return
        for item in remaining:
            controller.click_entity(ctx.ser, item, check_ui_blockers=False)  # "Drop"
            controller.human_delay(0.2, 0.4)



# =============================================================================================
# The fur trip: falconry at Piscatoris (2026-09-06)
# docs/superpowers/specs/2026-09-06-golem-crafting-events-and-falconry-design.md, section 3.
#
# Entered from _run_bank_trip when the bank holds no usable fur (the kit is withdrawn there, at the
# open bank). Dispatched on LIVE position every call -- trip_leg() -- so a restart resumes wherever
# the character actually is; ctx.state.data["gc_trip"] holds only hints (kit withdrawn, start time,
# catches). The Piscatoris legs and the hunting primitives are routines/falconry.py's own (recorded
# 2026-08-23); the return leg is the slayer ring into the Wyrmscraig Cavern and the Cave/Exit
# object back to the surface. NONE of the new legs has run live -- the spec's worklist is the first
# session's checklist.
# =============================================================================================

KIT_ITEMS = ("ardougne cloak",)          # + the configured return teleport (no Dramen staff:
                                          # fairy rings stopped needing one, USER 2026-09-06)
RETURN_TELEPORT_ITEMS = {"slayer ring": "slayer ring", "necklace of passage": "necklace of passage"}
# Wyrmscraig's SURFACE regions (x 2540-2640 -> region x 39-41, y 2180-2290 -> region y 34-35).
WYRMSCRAIG_REGIONS = frozenset({10018, 10019, 10274, 10275, 10530, 10531})
# The cavern is the surface plus 6400 in y (Broken golem at 2573,8650; cavern centre 2590,8615).
CAVERN_MIN_Y = 8000
CAVERN_EXIT_NAME = "cave"       # loc 62220 wyrmscraig_cave_exit: name "Cave", op1 "Exit" (cache)
CAVERN_EXIT_ACTION = "exit"
CAVERN_SCAN_LIMIT = 12          # full-scene scans with no exit and no known anchor before stopping
KIT_CONFIRM_S = 3.0             # a bank withdraw click -> the item in the inventory
DIALOGUE_S = 3.0                # slayer ring Teleport -> the destination dialogue
LEAVE_CAVERN_S = 6.0            # Cave/Exit click -> surface (plus the walk to it)
POUCH_OPEN_MARKER = "(open)"    # "Large fur pouch (open)" (cache obj 29470) vs "Large fur pouch"


def _trip(ctx) -> dict:
    return ctx.state.data.setdefault("gc_trip", {})


def _fur_trip_cfg(config: dict) -> dict:
    merged = dict(DEFAULT_CONFIG["furTrip"])
    merged.update((config or {}).get("furTrip") or {})
    return merged


def _start_trip(ctx) -> None:
    ctx.state.data["gc_trip"] = {"kit": True, "done": False, "started": time.time(),
                                 "catches": 0, "cavern_scans": 0}


def trip_leg(player: dict, trip: dict) -> str:
    """Which leg of the fur trip the character is on, from LIVE position alone (plus the trip's
    two hints): "kit" | "depart" | "fairy_ring" | "walk_in" | "hunt" | "return" | "cavern" |
    "home" | "unknown". Pure, so the whole dispatch table is unit-tested."""
    region = player.get("regionId")
    y = player.get("y") or 0
    if y >= CAVERN_MIN_Y:
        return "cavern"
    if region == falconry.REGION_FALCONRY:
        if trip.get("done"):
            return "return"
        if y >= falconry.SOUTH_OF_STILE_Y:
            return "walk_in"     # north of the fence: the stile is still ahead (falconry's own rule)
        return "hunt"
    if region in (falconry.REGION_MONASTERY, falconry.REGION_FAIRY_RING):
        return "fairy_ring"
    if region == falconry.REGION_FAIRY_LANDING:
        return "walk_in"
    if region in WYRMSCRAIG_REGIONS:
        if trip.get("done"):
            return "home"
        if trip.get("kit"):
            return "depart"
        return "kit"
    return "unknown"


def _item(sub: str):
    sub = sub.lower()
    return next((i for i in controller.get_inventory() if sub in (i.get("name") or "").lower()), None)


def _has_item(sub: str) -> bool:
    return _item(sub) is not None


def _coins() -> int:
    coins = _item("coins")
    return coins.get("qty", 0) if coins else 0


def _withdraw_kit(ctx, config: dict) -> bool:
    """Withdraw the travel kit at the OPEN bank: Ardougne cloak, the configured
    return teleport, and coins up to hireCoins. Each withdrawal is confirmed on INVENTORY_CHANGED
    + the item in the inventory; a kit item the bank does not hold is a safety stop naming it (a
    trip without it strands the character)."""
    cfg = _fur_trip_cfg(config)
    return_item = RETURN_TELEPORT_ITEMS.get(cfg.get("returnTeleport"), "slayer ring")
    for sub in KIT_ITEMS + (return_item,):
        if _has_item(sub):
            continue
        mark = outcome_waits.mark(ctx)
        ok = banker.search_and_withdraw(ctx, sub, 1, contains=True)
        landed = ok and outcome_waits.await_pre(
            ctx, "INVENTORY_CHANGED", KIT_CONFIRM_S, confirm=lambda sub=sub: _has_item(sub),
            since=mark, reaction=False, now_fn=time.time, sleep_fn=time.sleep)
        if not landed:
            ctx.state.trigger_safety_stop(
                f"fur trip: '{sub}' is not in the bank -- needed to reach Piscatoris and back")
            return False

    need = int(cfg.get("hireCoins", 2000)) - _coins()
    if need > 0 and not _withdraw_coins(ctx, need):
        ctx.state.trigger_safety_stop(
            f"fur trip: could not withdraw {need} coins for Quick-falcon (500gp per falcon)")
        return False
    return True


def _withdraw_coins(ctx, amount: int) -> bool:
    """Withdraw-X `amount` coins from the open bank (search, right-click, typed amount) --
    mastering_mixology's Withdraw-X shape; search_and_withdraw would click once per coin."""
    if not banker.search_bank(ctx, "coins"):
        return False
    slot = next((i for i in controller.get_bank().get("items", [])
                 if "coins" in (i.get("name") or "").lower() and i.get("qty", 0) > 0), None)
    if slot is None:
        banker.clear_bank_search(ctx)
        return False
    before = _coins()
    if not controller.click_menu_action(ctx.ser, slot, "Withdraw-X", check_ui_blockers=False):
        banker.clear_bank_search(ctx)
        return False
    # The "Enter amount" chatbox needs a moment to take keyboard focus (CONFIRMED live in the
    # mixology bank, 2026-08-12: digits typed immediately were dropped).
    controller.human_delay(0.4, 0.6)
    mark = outcome_waits.mark(ctx)
    controller.type_digits_and_enter(ctx.ser, str(amount))
    landed = outcome_waits.await_pre(
        ctx, "INVENTORY_CHANGED", KIT_CONFIRM_S, confirm=lambda: _coins() > before,
        since=mark, reaction=False, now_fn=time.time, sleep_fn=time.sleep)
    banker.clear_bank_search(ctx)
    return bool(landed)


def _hunt_done(ctx, cfg: dict, trip: dict) -> bool:
    """The trip's end: LOOSE dashing kebbit fur in the inventory means the pouch holds its 28
    (the same spill signal the goat pit used, CONFIRMED live 2026-08-09), or the time cap. Sets
    gc_fur_stock either way -- 28 on a full pouch, the retrieve count otherwise."""
    if _has_item(falconry.FUR_NAME):
        debug_log.log("golem crafting", "fur spilled into the inventory -- the fur pouch is full, trip done")
        ctx.state.data["gc_fur_stock"] = FUR_POUCH_CAPACITY
        return True
    started = trip.get("started")
    if started is None:
        trip["started"] = time.time()
        return False
    if time.time() - started > float(cfg.get("tripMaxMinutes", 25)) * 60.0:
        catches = int(trip.get("catches", 0))
        debug_log.log("golem crafting", f"fur trip time cap reached with {catches} catches -- heading home")
        ctx.state.data["gc_fur_stock"] = catches
        return True
    return False


def _ensure_pouch_open(ctx) -> bool:
    """Fur only routes into an OPEN fur pouch (wiki; the goat pit hunt opened it the same way).
    The open state is the item's own name ("... fur pouch (open)", cache obj 29470). Returns
    True if it acted this tick."""
    pouch = _item(banker.FUR_POUCH_NAME)
    if pouch is None or POUCH_OPEN_MARKER in (pouch.get("name") or "").lower():
        return False
    debug_log.log("golem crafting", "opening the fur pouch so catches route straight in")
    controller.open_inventory_tab(ctx.ser)
    mark = outcome_waits.mark(ctx)
    if not controller.click_menu_action(ctx.ser, pouch, "Open", check_ui_blockers=False):
        return True
    outcome_waits.await_pre(
        ctx, "INVENTORY_CHANGED", KIT_CONFIRM_S,
        confirm=lambda: POUCH_OPEN_MARKER in ((_item(banker.FUR_POUCH_NAME) or {}).get("name") or "").lower(),
        since=mark, reaction=False, now_fn=time.time, sleep_fn=time.sleep)
    return True


def _dialogue_options() -> list:
    """The REAL options of the open chat menu, in number-key order (key = position + 1) -- the
    m8aq Dialogue reader returns the TITLE as child 0 then blank trailing children (MEASURED
    live in Giants' Foundry 2026-09-03; see giants_foundry._dialogue_options)."""
    d = controller.get_api("ui", "dialogue")
    if not d or d.get("error"):
        return []
    raw = [o if isinstance(o, str) else (o.get("text") or "") for o in (d.get("options") or [])]
    raw = raw[1:] if raw else raw
    return [o for o in raw if o.strip()]


def _choose_dialogue_option(ctx, needle: str, budget_s: float = DIALOGUE_S) -> bool:
    """Wait for a chat menu whose options contain `needle` and press its number key."""
    deadline = time.time() + budget_s
    while time.time() < deadline:
        options = _dialogue_options()
        for i, text in enumerate(options):
            if needle.lower() in text.lower():
                controller.key_tap(ctx.ser, str(i + 1))
                return True
        time.sleep(0.1)
    debug_log.log("golem crafting", f"no dialogue option containing '{needle}' appeared within {budget_s}s")
    return False


def _at_wyrmscraig() -> bool:
    player = controller.get_player() or {}
    return (player.get("y") or 0) >= CAVERN_MIN_Y or player.get("regionId") in WYRMSCRAIG_REGIONS


def _teleport_home(ctx, cfg: dict) -> bool:
    """Slayer ring / necklace of passage home to Wyrmscraig.

    LEFT-click first (USER 2026-09-06: "for inventory teleports ive changed them so we just
    click the item and we teleport to the right spot"), verified through GET /hover so an
    un-swapped item (default "Wear") withholds instead of being worn; the right-click menu
    ("Wyrmscraig" direct, else `Teleport`/`Rub` -- cache obj 11866 iop3) is the fallback. Either
    path may open the numbered destination dialogue, whose "Wyrmscraig" entry is picked by
    number key. Confirmed on the player standing in the cavern band or a surface Wyrmscraig
    region."""
    return_item = RETURN_TELEPORT_ITEMS.get(cfg.get("returnTeleport"), "slayer ring")
    item = _item(return_item)
    if item is None:
        ctx.state.trigger_safety_stop(f"fur trip: no '{return_item}' in the inventory to get home with")
        return False
    controller.open_inventory_tab(ctx.ser)
    mark = outcome_waits.mark(ctx)
    debug_log.log("golem crafting", f"teleporting home with the {return_item}")
    clicked = controller.click_entity(ctx.ser, item, check_ui_blockers=False, simulate_mistakes=False,
                                      expected_action=("Wyrmscraig", "Teleport", "Rub"))
    if clicked:
        # A swapped left-click either lands directly or opens the destination dialogue.
        deadline = time.time() + DIALOGUE_S
        while time.time() < deadline and not _at_wyrmscraig():
            options = _dialogue_options()
            hit = next((i for i, t in enumerate(options) if "wyrmscraig" in t.lower()), None)
            if hit is not None:
                controller.key_tap(ctx.ser, str(hit + 1))
                break
            time.sleep(0.1)
    elif not controller.click_menu_action(ctx.ser, item, "Wyrmscraig", check_ui_blockers=False):
        if not controller.click_menu_action(ctx.ser, item, ("Teleport", "Rub"), check_ui_blockers=False):
            debug_log.log("golem crafting", f"{return_item}: no Teleport/Rub entry in its menu -- retrying next tick")
            return False
        if not _choose_dialogue_option(ctx, "wyrmscraig"):
            return False
    landed = outcome_waits.await_pre(
        ctx, ("PLAYER_STOPPED", "PLAYER_MOVING"), falconry.TELEPORT_BUDGET_S, confirm=_at_wyrmscraig,
        since=mark, reaction=False, now_fn=time.time, sleep_fn=time.sleep)
    if not landed:
        debug_log.log("golem crafting", "the return teleport did not land at Wyrmscraig within the budget -- retrying next tick")
    return bool(landed)


def _find_cavern_exit():
    """The Cave/Exit object anywhere in the LOADED SCENE (no radius: the cavern is one scene and
    this runs once per trip), nearest first. None when not loaded/visible."""
    candidates = [
        o for o in controller.get_objects(interactable=True)
        if (o.get("name") or "").lower() == CAVERN_EXIT_NAME
        and any(CAVERN_EXIT_ACTION == (a or "").lower() for a in (o.get("actions") or []))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _leave_cavern(ctx, config: dict, trip: dict) -> bool:
    """Walk to and click the cavern's Cave/Exit object; True once the player is back on the
    surface. Records the exit's tile (ctx.state.data["gc_cavern_exit"]) the first time it is
    seen so later trips walk there before scanning; with nothing visible and no anchor, the
    scan count is bounded by CAVERN_SCAN_LIMIT."""
    exit_obj = _find_cavern_exit()
    known = ctx.state.data.get("gc_cavern_exit") or (config.get("locations") or {}).get("cavernExit")
    if exit_obj is not None:
        tile = {"x": exit_obj.get("x"), "y": exit_obj.get("y"), "plane": exit_obj.get("plane", 0)}
        ctx.state.data["gc_cavern_exit"] = tile
        refresh = controller.object_refresh_target(tile["x"], tile["y"], tile["plane"], exit_obj.get("id"))
        mark = outcome_waits.mark(ctx)
        if not controller.click_entity(ctx.ser, exit_obj, refresh_target=refresh):
            # Off-screen (the exit can be 40+ tiles from the ring's landing): walk toward it, the
            # next call clicks it once it projects.
            debug_log.log("golem crafting", f"cavern exit at ({tile['x']}, {tile['y']}) is off-screen -- walking to it")
            travel.walk_to(ctx, tile, goal_adjacent=True)
            return False
        left = outcome_waits.await_pre(
            ctx, ("PLAYER_STOPPED", "PLAYER_MOVING"),
            LEAVE_CAVERN_S + outcome_waits.walk_budget(exit_obj),
            confirm=lambda: ((controller.get_player() or {}).get("y") or 0) < CAVERN_MIN_Y,
            since=mark, reaction=False, now_fn=time.time, sleep_fn=time.sleep)
        return bool(left)
    if known:
        debug_log.log("golem crafting", f"cavern exit not in view -- walking to the recorded exit at ({known.get('x')}, {known.get('y')})")
        travel.walk_to(ctx, known, goal_adjacent=True)
        return False
    scans = int(trip.get("cavern_scans", 0)) + 1
    trip["cavern_scans"] = scans
    debug_log.log("golem crafting", f"no Cave/Exit object in the loaded scene ({scans}/{CAVERN_SCAN_LIMIT}) and no recorded exit")
    if scans >= CAVERN_SCAN_LIMIT:
        ctx.state.trigger_safety_stop(
            "fur trip: cannot find the Wyrmscraig Cavern exit (no 'Cave'/'Exit' object in the "
            "loaded scene) -- set locations.cavernExit from a live read")
    return False


def fur_trip_phase(ctx, config: dict) -> None:
    """One bounded action per call on whichever leg trip_leg() says we are on (see the block
    comment above). Legs 1-4 reuse falconry.py's own travel functions; the hunt is
    falconry.hunt_step; the end condition and the return are this routine's."""
    cfg = _fur_trip_cfg(config)
    trip = _trip(ctx)
    player = controller.get_player() or {}
    leg = trip_leg(player, trip)
    if leg != ctx.state.data.get("gc_trip_leg"):
        ctx.state.data["gc_trip_leg"] = leg
        debug_log.log("golem crafting", f"fur trip: leg={leg} region={player.get('regionId')} tile=({player.get('x')}, {player.get('y')})")

    if leg == "kit":
        # The kit is withdrawn by _run_bank_trip at the open bank; bank_phase gets us there.
        ctx.state.data["gc_phase"] = "bank"
        return
    if leg == "depart":
        falconry._use_teleport(ctx, falconry.ARDOUGNE_CLOAK, falconry.OPT_KANDARIN_MONASTERY,
                               expect_region=falconry.REGION_MONASTERY)
        return
    if leg == "fairy_ring":
        falconry._leg_fairy_ring(ctx)
        return
    if leg == "walk_in":
        if falconry._ensure_hands_free(ctx):
            return
        falconry._leg_walk_home(ctx)
        return
    if leg == "hunt":
        if _hunt_done(ctx, cfg, trip):
            trip["done"] = True
            return
        if not falconry.hands_free():
            falconry._ensure_hands_free(ctx)
            return
        if _ensure_pouch_open(ctx):
            return
        result = falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=bool(cfg.get("dropMeat", True)))
        if result == "retrieved":
            trip["catches"] = int(trip.get("catches", 0)) + 1
        return
    if leg == "return":
        _teleport_home(ctx, cfg)
        return
    if leg == "cavern":
        _leave_cavern(ctx, config, trip)
        return
    if leg == "home":
        if banker.find_nearby_bank({}) is not None:
            debug_log.log("golem crafting", "fur trip complete -- back at the Wyrmscraig bank")
            ctx.state.data["gc_trip"] = {}
            ctx.state.data["gc_phase"] = "bank"
            return
        target = (config.get("locations") or {}).get("bank")
        if target is None:
            ctx.state.trigger_safety_stop("fur trip: no bank anchor configured to walk home to")
            return
        travel.walk_to(ctx, _nearest_walkable_tile(target), goal_adjacent=False)
        return
    ctx.state.trigger_safety_stop(
        f"fur trip: unexpected position (region {player.get('regionId')}, tile "
        f"({player.get('x')}, {player.get('y')})) -- not on any known leg")


PHASES = {
    "mine": mine_phase,
    "chisel": chisel_phase,
    "craft": craft_phase,
    "bank": bank_phase,
    "fur_trip": fur_trip_phase,
}


def _progress_signature(ctx):
    """Everything that moves while the routine is making progress: the phase, the inventory
    (names and counts), the plinths' progress/side/total varbits, the fur estimate and the
    player's tile (a fur-trip walk leg changes nothing else for a minute at a time)."""
    inventory = {}
    for item in controller.get_inventory() or []:
        name = item.get("name") or ""
        inventory[name] = inventory.get(name, 0) + 1
    golem = controller.get_golem() or {}
    stations = tuple(
        (st.get("name"), st.get("progress"),
         tuple(sorted((side, bool(v.get("done"))) for side, v in (st.get("sides") or {}).items())))
        for st in golem.get("stations") or []
    )
    player = controller.get_player() or {}
    return (
        ctx.state.data.get("gc_phase"),
        tuple(sorted(inventory.items())),
        golem.get("totalCrafted"),
        stations,
        ctx.state.data.get("gc_fur_stock"),
        (player.get("x"), player.get("y")),
    )


def _stuck_watch(ctx) -> None:
    """Once per tick(): log `[!]` at STALL_WARN_S of an unchanged progress signature, stop at
    STALL_STOP_S. A handler that returns every tick with nothing happening (a plinth that never
    projects, an object that never appears, a trip leg whose teleport never lands) used to look
    exactly like a healthy run in the log."""
    data = ctx.state.data
    sig = _progress_signature(ctx)
    now = time.time()
    if sig != data.get("gc_stall_sig"):
        data["gc_stall_sig"] = sig
        data["gc_stall_since"] = now
        data["gc_stall_warned"] = False
        return
    still = now - data.get("gc_stall_since", now)
    if still >= STALL_STOP_S:
        ctx.state.trigger_safety_stop(
            f"golem crafting: no progress for {int(still)}s in phase '{data.get('gc_phase')}'"
        )
    elif still >= STALL_WARN_S and not data.get("gc_stall_warned"):
        data["gc_stall_warned"] = True
        debug_log.log("!", f"golem crafting: STUCK? no progress for {int(still)}s in phase '{data.get('gc_phase')}'")


def tick(ctx):
    config = ctx.state.config

    if not ctx.state.data.get("gc_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["gc_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    _stuck_watch(ctx)
    if ctx.state.safety_stopped:
        return

    # CORRECTED 2026-08-08 (live session, user request): starts at "bank" rather than "mine" --
    # a fresh activation should stock fur FIRST (withdrawn in full and pouched, see
    # _run_bank_trip's docstring), so mine_phase never has to compete with loose fur for
    # inventory slots even on the very first mining trip of a session.
    #
    # 2026-09-06: a handler in TICK_CHAIN_FROM that flips gc_phase hands straight on to the new
    # phase inside this same tick (bounded by TICK_CHAIN_MAX and the pause/safety/break flags).
    for _ in range(TICK_CHAIN_MAX + 1):
        phase = ctx.state.data.get("gc_phase", "bank")
        handler = PHASES.get(phase)
        if handler is None:
            ctx.state.trigger_safety_stop(f"unknown golem crafting phase '{phase}'")
            return

        handler(ctx, config)
        if ctx.state.safety_stopped or ctx.state.paused:
            return
        break_due = getattr(ctx.state, "break_due", None)
        if callable(break_due) and break_due((config or {}).get("breaks") or {}):
            return
        if phase not in TICK_CHAIN_FROM or ctx.state.data.get("gc_phase", "bank") == phase:
            return
