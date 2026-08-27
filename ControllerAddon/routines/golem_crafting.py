# routines/golem_crafting.py
#
# Golem Crafting routine, overall loop (CORRECTED 2026-08-08, live session, user-specified):
#   bank (stock fur, pouched) -> mine a batch of sunstone -> chisel PART of it into cores ->
#   craft up to one golem per core (auto-consumes fur too) -> bank again only once the tracked
#   fur stock actually runs low, otherwise straight back to mining -- with a detour to the Goat
#   Pit if the bank itself has no usable fur left at all.
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
# anywhere but the bank or the Goat Pit.
#
# Run with:
#     python controller.py routines/golem_crafting.py

import math
import time

import banker
import controller
import debug_log
import travel

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

# CONFIRMED LIVE 2026-08-08 (MCP debug session): a "Spikes supply" GameObject stands right next
# to Geoff at the Goat Pit -- a plain click resolves to "Take" (confirmed via /menu:
# Take/Take-5/Take-10/Take-X, with "Take" as the default/top entry), handing over one wooden
# spike per click. This replaces the original design's bank-withdrawal path for spikes (see
# _ensure_wooden_spikes below) -- there's no reason to walk all the way back to the bank for
# something sitting right at the pit.
SPIKES_SUPPLY_NAME = "Spikes supply"

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact item/object names for the Goat Pit hunting loop
# (the inventory item name spikes actually show up as, the pit object itself, and goat NPCs).
WOODEN_SPIKES_NAME = "Wooden spikes"
GOAT_PIT_NAME = "Goat pit"
GOAT_NAME = "goat"

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
    #   goatPit / spikesSupply: the Goat Pit itself and the Spikes supply object beside Geoff,
    #     ~60 tiles south of the mining/plinth cluster -- travel.walk_to covers this in multiple
    #     hops (WALK_HOP_MIN/MAX_TILES), same as any other multi-hop route in this codebase.
    #   bank: the Bank chest (id 62390) near the mining/plinth cluster -- ADDED because the
    #     Goat Pit sits ~65 tiles from it, well outside banker.find_nearby_bank's default
    #     40-tile radius, so a hunt trip needed an anchor to actually get back to it. Safe to
    #     hardcode: Wyrmscraig has exactly one bank (per the design doc's own "Key facts" --
    #     unlike Ferox/most other banking spots, there's no ambiguity about which one this is).
    "locations": {
        "mining": {"x": 2599, "y": 2248, "plane": 0},
        "plinth": {"x": 2595, "y": 2253, "plane": 0},
        "goatPit": {"x": 2572, "y": 2195, "plane": 0},
        "spikesSupply": {"x": 2578, "y": 2202, "plane": 0},
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


def is_valid_grab_target(pit: dict, goat: dict) -> bool:
    """South, east, or west of the pit only -- north is the player's own fixed standing side
    (CONFIRMED live, account owner), so a goat there can never be a valid Grab target no matter
    how close it is."""
    return goat.get("y", 0) <= pit.get("y", 0)


def goat_in_grab_range(player: dict, goat: dict) -> bool:
    """Telekinetic Grab's cast range is 10 tiles (CONFIRMED live, account owner) -- a target
    beyond that makes the character auto-walk toward it instead of just casting in place, which
    would pull the player off the fixed north tile hunt_phase depends on for is_valid_grab_
    target's own north-side exclusion to keep meaning anything."""
    dx = abs(goat.get("x", 0) - player.get("x", 0))
    dy = abs(goat.get("y", 0) - player.get("y", 0))
    return max(dx, dy) <= GRAB_RANGE_TILES


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
# anchor from ~65 tiles away, travel.walk_to's own Shortest Path route occasionally declares
# "arrived" (within WALK_ARRIVAL_TOLERANCE) a bit short of where find_nearby_bank can actually
# detect the bank object, and a retried Shortest Path request right after can get rejected
# ("Shortest Path unavailable") if the plugin still considers the previous one active -- either
# way costs one wasted attempt with no real progress. 3 wasn't always enough headroom to still
# land a couple of genuinely-progressing walks after an unlucky one; this is a blunt mitigation,
# not a fix for travel.py's own walking algorithm (out of this routine's scope).
MAX_WALK_ATTEMPTS = 6

# ADDED 2026-08-08 (user request): a bank found by find_nearby_bank within its 40-tile radius
# but too far off-screen to click directly (see open_bank's caller in bank_phase) gets a few
# direct minimap-click hops straight at its own real position first, same trick travel.py's
# own _hop_to uses for a route hop beyond render distance -- CONFIRMED that idiom already works
# for cross-country travel, and it's a cheaper, more targeted fix here than immediately handing
# off to handle_missing_target's full Shortest-Path walk toward the (possibly-approximate)
# "bank" anchor tile: clicking the bank object's actual coordinates walks straight at the real
# thing. Capped, same reasoning as MAX_WALK_ATTEMPTS above -- a bank that never gets closer after
# a few hops (e.g. genuinely unreachable, or the click isn't landing) should fall through to the
# anchor-walk fallback instead of hopping at the same spot forever.
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
        travel.walk_to(ctx, _nearest_walkable_tile(target))
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

    controller.click_entity(ctx.ser, point, offset=(0, 0))
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


def _shed_pit_junk(ctx, phase_label: str) -> None:
    """Drop leftover Wooden spikes -- they are pit-only and useless anywhere else.

    _ensure_wooden_spikes takes them from the supply AT the pit and nothing outside hunt_phase
    uses them, so any still carried are leftovers from a drop pass that did not fully land
    (_drop_all at hunt exit is the primary cleanup, but it is one-shot and the routine may not
    hunt again for an hour).

    Called from BOTH mine_phase and craft_phase, because craft_phase is reachable without passing
    through mine_phase at all -- has_start_materials only needs 4 raw + 1 core, which a part-used
    batch already satisfies. OBSERVED live 2026-08-24: 4 leftover spikes left the inventory at
    28/28, which both capped mining at 15 sunstone against the 20 budget AND rode into a craft
    phase where every Start-golem attempt failed with a menu offering no golem option -- plinth
    correctly rendered and aimed at, materials present, nobody else nearby. Whether the full
    inventory CAUSES that is unproven; shedding pit-only junk is justified on budget grounds
    regardless."""
    if not _count_named(WOODEN_SPIKES_NAME):
        return
    controller.key_tap(ctx.ser, "w")   # inventory tab -- same idiom hunt_phase's own drop uses
    controller.human_delay(0.2, 0.4)
    debug_log.log("golem crafting", f"dropping leftover pit spikes before {phase_label}")
    _drop_all(ctx, WOODEN_SPIKES_NAME)


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
    _shed_pit_junk(ctx, "mining")

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
    if not controller.wait_until_player_idle(timeout_s=HOVER_IDLE_TIMEOUT_S):
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

    debug_log.log("golem crafting", f"chiselling sunstone into a core ({cores_have}/{CORES_PER_TRIP})")
    controller.human_delay(0.15, 0.35)
    controller.click_entity(ctx.ser, chisel, check_ui_blockers=False)
    controller.human_delay(0.1, 0.25)
    controller.click_entity(ctx.ser, raw, check_ui_blockers=False)

    deadline = time.time() + CHISEL_CONFIRM_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(0.2)
        if _count_named(SUNSTONE_CORE_NAME) > cores_have:
            return
    debug_log.log("golem crafting", "chisel click didn't register within timeout -- retrying next tick")


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

# CONFIRMED live (user session) -- Goat Pit hunting mechanic, replacing the original wrong
# plain-click-lures/harvests assumption. See docs/superpowers/specs/2026-08-08-goat-pit-
# telekinetic-grab-design.md for the full mechanic writeup.
GOAT_CHAT_PIT_FULL = "the pit is now filled with goats."
# Rune name varies (law/air), so this matches on the stable surrounding text rather than a
# single fixed string -- both substrings must be present.
GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS = ("you do not have enough", "to cast this spell")
# ROOT-CAUSED 2026-08-09 (autonomous session, hunt_test17.log diagnostic logging): the lining
# applied once at the start of a hunt visit wears out well before the pit fills -- every Grab
# cast after the first couple came back with this exact chat line, CONFIRMED live as the actual
# reason hunt_phase's 60-cast cap kept getting exhausted with zero fills (cast_telekinetic_grab
# itself was returning True the whole time; the click lands, the game just rejects the catch).
# The plan's original edge-case question ("if we don't get 28 furs within those 5 spikes, do we
# recognize this and grab new spikes to line with?") was never actually wired to a mid-cycle
# reline -- only the pre-hunt and post-harvest relining paths existed.
GOAT_CHAT_NEED_NEW_SPIKES = "you need to replace the spikes in the pit."
GOAT_HORN_NAME = "Goat horn"  # always drops per trapped goat, never useful -- dropped on harvest
FUR_POUCH_CAPACITY = 28  # large fur pouch's capacity -- once hit, further fur spills into loose
                         # inventory instead of the pouch, which is exactly the signal used below
                         # to know the pouch is full without any dedicated capacity-check action
GRAB_RANGE_TILES = 10  # Telekinetic Grab's cast range -- a farther target makes the character
                       # auto-walk toward it, pulling the player off the fixed standing tile the
                       # whole north-side-only geometry below depends on
# ADDED 2026-08-09 (user request): a goat that was just cast on keeps traveling toward the pit
# for a few seconds, not vanishing instantly -- without a cooldown it stayed the closest
# candidate and got re-targeted over and over instead of spreading casts across the several
# goats that can be en route to the pit at once.
# CORRECTED 2026-08-09 (user report, live): 4s still let some goats get re-clicked before
# actually reaching the pit -- bumped to 8s, then to 10s (perfect for a goat cast at max range,
# GRAB_RANGE_TILES, which takes the longest to actually reach the pit).
GOAT_GRAB_COOLDOWN_S = 10.0


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

    ADDED 2026-08-08 (Goat Pit hunting redesign): also captures hunt_phase's own two chat
    signals (pit-full, rune-shortage) into a SEPARATE gc_last_hunt_chat/_seq pair, not the
    golem-crafting fur flag above -- controller.py only ever calls one on_event per routine module
    (see its own dispatch, `hasattr(routine, "on_event")`), so both concerns have to live in
    this one function, but craft_phase and hunt_phase are mutually exclusive via gc_phase and
    keeping their chat state in separate keys avoids any future ambiguity if that ever changes."""
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

    is_pit_full = GOAT_CHAT_PIT_FULL in message
    is_rune_short = all(part in message for part in GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS)
    is_spikes_worn = GOAT_CHAT_NEED_NEW_SPIKES in message
    if is_pit_full or is_rune_short or is_spikes_worn:
        ctx.state.data["gc_last_hunt_chat"] = message
        ctx.state.data["gc_last_hunt_chat_seq"] = ctx.state.data.get("gc_last_hunt_chat_seq", 0) + 1


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


def _await_click_echo(before: dict, object_id: int):
    """Poll /last-action briefly for the game's own verdict on the plinth click just issued.
    Returns _click_resolved_wrongly's tri-state; None on timeout, which hands control back to the
    ordinary confirmation path unchanged."""
    deadline = time.time() + GOLEM_CLICK_ECHO_TIMEOUT_S
    while time.time() < deadline:
        verdict = _click_resolved_wrongly(before, controller.get_last_action(), object_id)
        if verdict is not None:
            return verdict
        time.sleep(GOLEM_CLICK_ECHO_POLL_S)
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
        # object. The bare-click path tolerates that via refresh_legs re-aiming mid-flight;
        # click_menu_action aims exactly once, so its aim is only trustworthy from a standstill.
        # This is already the slow path, so the wait costs the fast one nothing. See
        # wait_until_player_idle's own docstring -- it exists for precisely this call shape.
        controller.wait_until_player_idle()
        # Re-resolve the plinth's CURRENT screen position first. `objects` was fetched at the top
        # of craft_phase, before the walk, so its screenX/screenY is stale by however far the
        # player travelled -- the bare-click path hides that with refresh_target/refresh_legs
        # re-aiming mid-flight, but click_menu_action has no such parameter and would right-click
        # the stale pixel. CONFIRMED live 2026-08-23 21:12: the first menu escalations opened a
        # menu of pure ground options (['Cancel', 'Mark Safespot', 'Examine', 'Walk here']) --
        # a right-click on bare tile, not on the plinth at all. offset=(0,0) for the same reason:
        # click_menu_action's default (-7, +7) nudge is enough to slide off a small plinth.
        fresh = controller.get_object_at(
            obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
            radius=MAX_OBJECT_DISTANCE,
        )
        if fresh is not None:
            obj = fresh
        # Right-click and pick the option BY NAME out of /menu, instead of trusting whatever the
        # default left-click action resolves to. LIVE HALT 2026-08-23 21:04: another player was
        # standing on the plinth's east tile -- players do not block each other, so they stack on
        # OUR tile -- and every bare left-click resolved to them ("Walk here -> DoveHorn"), five
        # in a row, until the routine safety-stopped and stayed down. This is exactly the case
        # click_menu_action's own docstring was written for. All three options are offered because
        # craft_phase does not get to choose which one the game presents for the station's current
        # state; click_menu_action tries a sequence in priority order against the one menu it
        # already reads back, so this costs no extra right-click.
        if controller.click_menu_action(ctx.ser, obj, GOLEM_PLINTH_OPTIONS, offset=(0, 0)):
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


def _await_side_done(station_name: str, side: str, timeout_s: float, already_done: bool = False) -> bool:
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
    hard_deadline = time.time() + GOLEM_SIDE_MAX_WAIT_S
    deadline = min(time.time() + timeout_s, hard_deadline)
    last_progress = None
    while time.time() < deadline:
        time.sleep(GOLEM_CONFIRM_POLL_S)
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
    return False


def _await_golem_complete(
    station_name: str, timeout_s: float, before_total_crafted: int, before_progress: int
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
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(GOLEM_CONFIRM_POLL_S)
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
        controller.click_menu_action(ctx.ser, target, "take", target_hint=target.get("name"), offset=(0, 0))
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
    _shed_pit_junk(ctx, "crafting")
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
        hijacked = withheld or _await_click_echo(before_action, station["objectId"]) is True
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
        if not _await_side_done(station["name"], side, GOLEM_SIDE_TIMEOUT_S, already_done=station["sides"][side]["done"]):
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
    if (insert_withheld or _await_click_echo(before_action, station["objectId"]) is True) \
            and not insert_escalate:
        ctx.state.data["gc_insert_hijacks"] = ctx.state.data.get("gc_insert_hijacks", 0) + 1
        debug_log.log("golem crafting",
                      "insert click was hijacked (resolved to another target) -- retrying now")
        return
    ctx.state.data["gc_insert_hijacks"] = 0
    if not _await_golem_complete(
        station["name"], GOLEM_INSERT_TIMEOUT_S, before_total_crafted, before_progress
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
# need to survive the trip so the next craft_phase call can still use them. Wooden spikes
# specifically CONFIRMED LIVE 2026-08-08 (MCP debug session): the bank won't even accept a
# deposit of them at all (not just "we'd rather keep them") -- consistent with spikes now being
# sourced from the Spikes supply object next to Geoff (see SPIKES_SUPPLY_NAME) rather than the
# bank, since a bank-unstorable item was never going to be a bankable supply to begin with.
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
    banker.FUR_POUCH_NAME, CHISEL_NAME.lower(), WOODEN_SPIKES_NAME.lower(),
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
        # anywhere and a hunt trip is starting: hunt_phase needs room for Goat horns, and we're
        # already standing at the open bank right now, so banking them (not dropping -- still
        # fully recoverable next mine_phase visit) costs nothing extra.
        debug_log.log("golem crafting", "bank has none of the usable fur types -- banking spare sunstone/cores before hunting")
        hunt_keep_items = [k for k in BANK_KEEP_ITEMS
                            if k not in (RAW_SUNSTONE_NAME.lower(), SUNSTONE_CORE_NAME.lower())]
        banker.deposit_inventory(ctx, {"keepItems": hunt_keep_items})
        ctx.state.data["gc_phase"] = "hunt"
        return

    slot = next((s for s in bank.get("items", []) if (s.get("name") or "").lower() == fur_name.lower()), None)
    qty = slot.get("qty", 0) if slot else 0

    if qty > 0:
        debug_log.log("golem crafting", f"withdrawing all {qty}x {fur_name}")
        banker.set_quantity_mode(ctx, "all")
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
        deadline = time.time() + WITHDRAW_CONFIRM_TIMEOUT_S
        landed = False
        while time.time() < deadline:
            if any((i.get("name") or "").lower() == fur_name.lower() for i in controller.get_inventory()):
                landed = True
                break
            time.sleep(0.2)
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
                    controller.click_entity(ctx.ser, point, offset=(0, 0))
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


def _ensure_wooden_spikes(ctx, config: dict) -> bool:
    """Take wooden spikes from the Spikes supply object next to Geoff if not already carried.
    CORRECTED 2026-08-08 (MCP live debug session): the original design assumed spikes had to be
    withdrawn from the bank, same as any other supply -- live testing found a "Spikes supply"
    object standing right next to Geoff at the Goat Pit itself, whose default left-click
    resolves to "Take" (see SPIKES_SUPPLY_NAME's comment). Walking back to the bank for
    something sitting right at the pit made no sense once that was confirmed, so this now takes
    directly from the supply instead.

    Same handle_missing_target retry/walk pattern as mine_phase/craft_phase/hunt_phase's own
    object lookups (not a bespoke bank-or-bust check like the old version), since hunt_phase can
    call this before the player has actually reached the pit area. Returns True once wooden
    spikes are confirmed in inventory; returns False either to tell the caller to just retry
    next tick, or because handle_missing_target gave up and this already called
    trigger_safety_stop -- callers don't need to distinguish the two, same as every other
    handle_missing_target call site in this module."""
    if find_item(WOODEN_SPIKES_NAME) is not None:
        return True

    supply = find_object(SPIKES_SUPPLY_NAME)
    if supply is None:
        if handle_missing_target(ctx, config, "spikesSupply", "gc_spikes_misses", "gc_spikes_walks"):
            return False
        ctx.state.trigger_safety_stop("out of wooden spikes and no Spikes supply found nearby")
        return False
    ctx.state.data["gc_spikes_misses"] = 0
    ctx.state.data["gc_spikes_walks"] = 0

    # CORRECTED 2026-08-09 (user request): "Take-5" instead of a plain click -- a plain click's
    # default action only ever gave 1 spike at a time (CONFIRMED live: hunt_phase's own relining
    # consumes 1 per cycle, so a 1-at-a-time take meant a Spikes supply round trip every single
    # cycle). Taking 5 at once covers 5 relining cycles per visit instead of 1.
    debug_log.log("golem crafting", "taking 5 wooden spikes from the Spikes supply")
    controller.click_menu_action(ctx.ser, supply, "take-5")
    controller.human_delay(0.4, 0.7)
    return find_item(WOODEN_SPIKES_NAME) is not None


# How long to wait for the Goat Pit's menu to switch from "Clear" to "Line" before giving up and
# proceeding anyway -- ADDED 2026-08-09 (user request). Generous margin: the collection animation
# was observed to "take a while", and proceeding too early is exactly the bug this guards against,
# so this errs toward patience over a tight bound.
PIT_CLEAR_TIMEOUT_S = 20.0


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


def _pit_ready_to_reline(ctx, pit: dict) -> bool:
    """Right-click the Goat Pit and check whether its menu currently offers "Line" (collection
    finished) rather than "Clear" (still processing) -- CONFIRMED live (user observation) this
    is the pit's own authoritative done-collecting signal. Peeks only: never selects an entry,
    dismissing the menu the same move-away-then-ESC way controller.click_menu_action does when
    nothing matches, so checking never risks accidentally triggering whatever IS on the menu."""
    if not controller.click_entity(ctx.ser, pit, simulate_mistakes=False, button="right"):
        return False
    deadline = time.time() + controller.MENU_POLL_TIMEOUT_S
    menu = controller.get_menu()
    while not menu.get("open") and time.time() < deadline:
        time.sleep(controller.MENU_POLL_INTERVAL_S)
        menu = controller.get_menu()
    if not menu.get("open"):
        return False
    ready = any("line" == (e.get("option") or "").lower() for e in menu.get("entries", []))
    cx, cy = controller.get_cursor_pos()
    controller.move_to_human(ctx.ser, *controller.pick_dismiss_point(cx, cy), aim_radius=10)
    controller.key_tap(ctx.ser, "ESC")
    return ready


# Consecutive empty-goat polls required before treating the pit as genuinely cleared -- guards
# against declaring "cleared" off a single early poll taken right after lining, before any wild
# goat has registered in the bridge's NPC list yet. ADDED 2026-08-08 (final whole-branch
# review).
GOAT_NO_GOAT_CLEAR_STREAK = 3

# Hard cap on hunt-phase actions (originally lure/harvest clicks; now Telekinetic Grab casts --
# see hunt_phase's own docstring for the 2026-08-08 rewrite) per activation -- guards the
# opposite failure: if a cast/click never actually resolves as expected, this phase would
# repeat forever with no progress. ADDED 2026-08-08 (final whole-branch review).
MAX_HUNT_ACTIONS = 60


# Consecutive Telekinetic Grab casts that never went out before treating the spellbook as
# genuinely unreachable. cast_telekinetic_grab returns False for exactly this case ("spellbook not
# open/wrong spellbook active"); hunt_phase used to DISCARD that return, so an unopenable
# spellbook burned all MAX_HUNT_ACTIONS casts over ~7 minutes and then reported the generic "Grab
# may not be resolving as expected" -- LIVE 2026-08-24 04:38, where /spellbook said visible:false
# and the inventory tab was open the entire time. Small but not 1: a single False can be a
# transient miss or a tab still animating.
SPELL_FAILURE_LIMIT = 4


def _note_cast_failed(ctx) -> None:
    """Record a Grab cast that never actually went out, and stop with the REAL reason once it is
    clearly not transient. Named separately from the generic hunt-action budget so the safety-stop
    message can say "the spellbook will not open" instead of guessing at rune costs or goat
    behaviour."""
    streak = ctx.state.data.get("gc_cast_failures", 0) + 1
    ctx.state.data["gc_cast_failures"] = streak
    if streak >= SPELL_FAILURE_LIMIT:
        ctx.state.trigger_safety_stop(
            f"Telekinetic Grab could not be cast {SPELL_FAILURE_LIMIT} times in a row -- the "
            f"spellbook will not open (GET /spellbook reports visible:false). Check the Magic-tab "
            f"keybind: open_spellbook_if_needed taps 'T'."
        )


def _note_cast_landed(ctx) -> None:
    ctx.state.data["gc_cast_failures"] = 0


def hunt_phase(ctx, config: dict) -> None:
    """Entered only when bank_phase finds no usable fur at all. Traps Wyrmscraig goats by
    casting Telekinetic Grab on them (not by clicking them directly -- see this function's own
    history below for the wrong assumption this replaces), harvests the pit once full, and
    repeats (relining every single cycle) until either no valid goats remain nearby or the fur
    pouch hits capacity. One bounded action per call, same shape as mine_phase/chisel_phase.

    CORRECTED 2026-08-08 (user session, live-verified): replaces this function's entire
    original model. The original assumed a plain left-click on a wild goat resolves to "Lure"
    (and the same click on a trapped goat resolves to "Harvest") -- CONFIRMED WRONG live. The
    real mechanic (see docs/superpowers/specs/2026-08-08-goat-pit-telekinetic-grab-design.md
    for the full writeup):
      1. Line the pit with wooden spikes (unchanged mechanically, now happens after walking to
         the fixed standing tile rather than before, so lining always happens from the same
         spot the whole cycle depends on).
      2. Stand on the tile 2 tiles north of the pit -- the player's own fixed side. Only goats
         to the south, east, or west (is_valid_grab_target) within 10 tiles
         (goat_in_grab_range) are ever valid Grab targets; a farther target would make the
         character auto-walk toward it, abandoning the fixed tile.
      3. Cast Telekinetic Grab (controller.cast_telekinetic_grab) on the nearest valid goat,
         repeatedly, until the chat message "The pit is now filled with goats." arrives
         (captured by on_event into gc_last_hunt_chat/_seq, same chat-driven-confirmation
         idiom craft_phase already uses for golem-crafting messages).
      4. Click the pit to harvest (not individual goats) -- always yields a Goat horn (useless,
         dropped) and a 1/4-per-trapped-goat chance of Wyrmscraig goat fur (routed into the
         already-open fur pouch, same as before).
      5. Relines every single cycle (not once per hunt visit) before repeating from step 3.
         ROOT-CAUSED 2026-08-09 (autonomous session): a single lining also wears out mid-visit,
         well before the pit fills -- "You need to replace the spikes in the pit." (chat, caught
         via GOAT_CHAT_NEED_NEW_SPIKES) means every subsequent cast lands but is rejected by the
         game, not that the cast itself is broken. This clears gc_pit_lined so the very next call
         re-lines from the same standing tile (no re-walk) before resuming casts -- this, not the
         click mechanics, was the real reason MAX_HUNT_ACTIONS kept exhausting with zero fills.
      6. If the fur pouch is at capacity (28), any further fur spills into loose inventory
         instead of the pouch -- CONFIRMED live this is the signal used to know it's full,
         rather than any dedicated capacity-check action. Once seen, drops any leftover wooden
         spikes (no longer needed this trip) and skips bank_phase entirely, going straight to
         mine_phase (fully re-stocked already -- nothing to deposit or withdraw for fur).

    Rune handling: no pre-check of the rune pouch's contents (it isn't inventory-visible the
    way find_item can see loose runes -- see the spec's Research method section for why).
    Instead, casting is attempted and the "you do not have enough ... to cast this spell"
    rejection (GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS) safety-stops immediately, same pattern as the
    fur/sunstone shortage handling in craft_phase.

    Still bounded by MAX_HUNT_ACTIONS/gc_hunt_actions, same safety net the original click-based
    version had -- a Grab cast that never actually resolves is the same "clicks forever with no
    progress" risk class a bad click resolution always was, so the cap carries over unchanged.
    """
    if not _ensure_wooden_spikes(ctx, config):
        return

    pit = find_object(GOAT_PIT_NAME)
    if pit is None:
        if handle_missing_target(ctx, config, "goatPit", "gc_pit_misses", "gc_pit_walks"):
            return
        ctx.state.trigger_safety_stop("Goat Pit not found nearby")
        return
    ctx.state.data["gc_pit_misses"] = 0
    ctx.state.data["gc_pit_walks"] = 0

    if not ctx.state.data.get("gc_hunt_standing"):
        target = {"x": pit["x"], "y": pit["y"] + 2, "plane": pit.get("plane", 0)}
        if not walk_to_exact_tile(ctx, target["x"], target["y"], target["plane"]):
            debug_log.log("golem crafting", "couldn't walk to the hunt standing tile -- retrying next tick")
            return
        ctx.state.data["gc_hunt_standing"] = True
        # CORRECTED 2026-08-09 (user report: "didn't press the pit to line it"): `pit` was
        # captured BEFORE the walk above, so its screenX/screenY were stale by the time the
        # lining click below used them -- walk_to_exact_tile's own polling loop takes real time
        # and can pan the camera, so a click against the pre-walk position landed nowhere near
        # the pit's actual on-screen spot post-walk. Re-fetching fresh here, same "always
        # re-check before a critical click" pattern craft_phase already uses after its own
        # walk_to_exact_tile calls.
        pit = find_object(GOAT_PIT_NAME)
        if pit is None:
            debug_log.log("golem crafting", "pit no longer found after walking to the standing tile -- retrying next tick")
            return

    if not ctx.state.data.get("gc_pit_lined"):
        # CORRECTED 2026-08-09 (user correction): lining doesn't need "use spikes on pit" --
        # merely carrying wooden spikes (already guaranteed by _ensure_wooden_spikes above) is
        # enough; a plain click on the Goat Pit itself does the lining. The previous click-
        # spikes-then-click-pit sequence was never actually needed and, combined with the stale
        # `pit` reference above, was why the pit click wasn't visibly landing.
        debug_log.log("golem crafting", "lining the Goat Pit")
        if not controller.click_entity(ctx.ser, pit):
            debug_log.log("golem crafting", "lining click missed/blocked -- retrying next tick")
            return
        controller.human_delay(0.6, 1.0)
        ctx.state.data["gc_pit_lined"] = True
        ctx.state.data["gc_pit_filled"] = False

        if banker.has_fur_pouch() and not ctx.state.data.get("gc_pouch_opened_this_hunt"):
            pouch = find_item(banker.FUR_POUCH_NAME.title()) or next(
                (i for i in controller.get_inventory() if banker.FUR_POUCH_NAME in (i.get("name") or "").lower()),
                None,
            )
            if pouch is not None:
                debug_log.log("golem crafting", "opening fur pouch so catches route straight in")
                controller.click_entity(ctx.ser, pouch, check_ui_blockers=False)
                controller.human_delay(0.3, 0.5)
            ctx.state.data["gc_pouch_opened_this_hunt"] = True
        return

    if not ctx.state.data.get("gc_pit_filled"):
        player = controller.get_player()
        # ADDED 2026-08-09 (user report: "sent a spell on a goat behind a tree and ran away from
        # our designated tile... creates an endless loop"): a goat blocked by scenery (a tree,
        # here) apparently isn't a valid Grab target even within range -- CONFIRMED live the
        # character auto-walked toward it instead of casting in place, the same failure mode
        # goat_in_grab_range's own docstring already flags for an out-of-range target, just
        # triggered by line-of-sight instead of distance. Once off the standing tile, every
        # subsequent goat looked "closest" from the wrong spot and casting kept silently failing
        # there forever. Re-asserting position here, every single call (not just once via
        # gc_hunt_standing), makes getting back the top priority over casting anything -- this
        # check runs before goat selection, so a drifted player never even attempts a cast from
        # the wrong tile.
        expected_x, expected_y = pit["x"], pit["y"] + 2
        if player.get("x") != expected_x or player.get("y") != expected_y:
            debug_log.log("golem crafting", "drifted off the hunt standing tile -- walking back before casting again")
            walk_to_exact_tile(ctx, expected_x, expected_y, pit.get("plane", 0))
            return

        # ADDED 2026-08-09 (user request): a goat that was just Grab-cast on doesn't vanish or
        # move out of range instantly -- it's still the closest candidate for a few seconds while
        # it travels toward the pit, so without this the very next cast re-targeted the SAME goat
        # over and over instead of spreading casts across the several goats that can be traveling
        # toward the pit at once (CONFIRMED live: id=16298 targeted repeatedly in a row).
        # Short-lived cooldown by NPC index, not identity/position (a goat's own position changes
        # every tick while it's being pulled in, so anything position-keyed would never actually
        # match the same goat twice).
        cooldowns = ctx.state.data.get("gc_hunt_grab_cooldowns", {})
        now = time.time()
        cooldowns = {idx: expiry for idx, expiry in cooldowns.items() if expiry > now}
        ctx.state.data["gc_hunt_grab_cooldowns"] = cooldowns
        valid_goats = [
            n for n in controller.get_npcs()
            if GOAT_NAME in (n.get("name") or "").lower()
            and n.get("visible", True)
            and is_valid_grab_target(pit, n)
            and goat_in_grab_range(player, n)
        ]
        # "Area genuinely cleared" is judged against valid_goats (ignores cooldown) -- a goat
        # that's merely on cooldown while it travels toward the pit is still there, not cleared,
        # and must never count toward this streak (it would otherwise falsely trigger "no valid
        # goats left -- moving to bank phase" every time several goats got Grab-cast in a row).
        if not valid_goats:
            streak = ctx.state.data.get("gc_hunt_no_goat_streak", 0) + 1
            ctx.state.data["gc_hunt_no_goat_streak"] = streak
            if streak < GOAT_NO_GOAT_CLEAR_STREAK:
                debug_log.log("golem crafting", f"no valid goats visible ({streak}/{GOAT_NO_GOAT_CLEAR_STREAK}) -- confirming the area is actually cleared")
                return
            debug_log.log("golem crafting", "no valid goats left nearby -- moving to bank phase")
            ctx.state.data["gc_phase"] = "bank"
            ctx.state.data["gc_pit_lined"] = False
            ctx.state.data["gc_pit_filled"] = False
            ctx.state.data["gc_hunt_standing"] = False
            ctx.state.data["gc_pouch_opened_this_hunt"] = False
            ctx.state.data["gc_hunt_no_goat_streak"] = 0
            ctx.state.data["gc_hunt_actions"] = 0
            ctx.state.data["gc_hunt_grab_cooldowns"] = {}
            return
        ctx.state.data["gc_hunt_no_goat_streak"] = 0

        goats = [n for n in valid_goats if n.get("index") not in cooldowns]
        if not goats:
            # Every currently-valid goat is on cooldown (all recently cast on, still traveling
            # toward the pit) -- not "cleared", just nothing new to cast on yet this tick.
            debug_log.log("golem crafting", "all nearby goats on cooldown -- waiting for one to become castable again")
            return

        # Same safety net the original click-based version had (MAX_HUNT_ACTIONS/gc_hunt_actions
        # existed to guard "a click that never actually resolves clicks forever with no
        # progress") -- a Grab cast that never actually lands (spellbook not opening, wrong
        # widget, etc.) is the exact same risk class for this mechanic, so the cap carries over
        # unchanged rather than being dropped along with the click-based approach it originally
        # guarded.
        actions = ctx.state.data.get("gc_hunt_actions", 0) + 1
        ctx.state.data["gc_hunt_actions"] = actions
        if actions > MAX_HUNT_ACTIONS:
            ctx.state.trigger_safety_stop(
                f"hunt phase exceeded {MAX_HUNT_ACTIONS} Grab-cast attempts without the pit "
                "filling -- Telekinetic Grab may not be resolving as expected (see hunt_phase's "
                "docstring)"
            )
            return

        goats.sort(key=lambda n: n.get("distance", 9999))
        target = goats[0]
        debug_log.log("golem crafting", f"casting Telekinetic Grab on {target.get('name')} (dist={target.get('distance')})")
        if controller.cast_telekinetic_grab(ctx.ser, target):
            _note_cast_landed(ctx)
        else:
            # The clicks never went out -- almost always the spellbook not being open. Counting
            # this as an ordinary hunt action would spend the whole budget before saying so.
            _note_cast_failed(ctx)
            if ctx.state.safety_stopped:
                return
        if target.get("index") is not None:
            cooldowns[target["index"]] = now + GOAT_GRAB_COOLDOWN_S
        controller.human_delay(0.6, 1.0)

        seq = ctx.state.data.get("gc_last_hunt_chat_seq", 0)
        if seq > ctx.state.data.get("gc_hunt_chat_seen_seq", 0):
            ctx.state.data["gc_hunt_chat_seen_seq"] = seq
            message = ctx.state.data.get("gc_last_hunt_chat", "")
            if all(part in message for part in GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS):
                # CORRECTED 2026-08-09 (user report): this fired once over "not enough Earth
                # runes" -- traced to a misclick on the spell icon landing on a different spell
                # entirely (now fixed, see cast_telekinetic_grab's simulate_mistakes=False). The
                # rune actually named in the game's own message is included here rather than
                # assumed to be law/air, so a repeat of this (or any other unexpected cause)
                # says exactly what the game said instead of a guess that could be wrong again.
                ctx.state.trigger_safety_stop(f"can't cast Telekinetic Grab: {message.strip()}")
                return
            if GOAT_CHAT_PIT_FULL in message:
                debug_log.log("golem crafting", "pit is now filled with goats -- harvesting")
                ctx.state.data["gc_pit_filled"] = True
                ctx.state.data["gc_hunt_actions"] = 0
            elif GOAT_CHAT_NEED_NEW_SPIKES in message:
                # ROOT-CAUSED 2026-08-09 (autonomous session, hunt_test17.log): a single lining
                # only lasts a handful of catches before wearing out mid-visit -- CONFIRMED live
                # this was the actual reason hunt_phase kept exhausting MAX_HUNT_ACTIONS with zero
                # pit fills (every cast after the first couple landed fine but was rejected by the
                # game with this exact message). Clearing gc_pit_lined re-enters the lining branch
                # above on the very next call -- no re-walk needed, gc_hunt_standing stays True and
                # the player is still on the fixed tile, so this only re-clicks the pit (guarded by
                # _ensure_wooden_spikes at the top of hunt_phase already keeping spikes stocked).
                debug_log.log("golem crafting", "pit's spikes wore out mid-hunt -- relining before casting again")
                ctx.state.data["gc_pit_lined"] = False
        return

    # Pit is full -- harvest.
    pit = find_object(GOAT_PIT_NAME)  # fresh lookup, same idiom as craft_phase's re-checks
    if pit is None:
        debug_log.log("golem crafting", "pit no longer found before harvesting -- retrying next tick")
        return
    controller.click_entity(ctx.ser, pit)
    controller.human_delay(0.6, 1.0)

    # ADDED 2026-08-09 (user report: "we still try to catch goats while the animation is going"):
    # collecting the trapped goats isn't instant -- CONFIRMED live the pit's own right-click menu
    # still offers "Clear" while that's in progress, only switching to "Line" once it's actually
    # done, which is the authoritative "finished collecting" signal (more reliable than a fixed
    # guess at how long the animation takes). Blocks here -- nothing else in hunt_phase runs
    # again until this returns, so no further Grab casts or inventory handling can overlap with
    # the collection animation.
    debug_log.log("golem crafting", "waiting for the pit to finish collecting")
    deadline = time.time() + PIT_CLEAR_TIMEOUT_S
    while time.time() < deadline and not _pit_ready_to_reline(ctx, pit):
        time.sleep(1.0)

    controller.key_tap(ctx.ser, "w")  # switch to Inventory -- CONFIRMED live (same idiom
                                       # chisel_phase already uses) needed before clicking
                                       # inventory items; a still-open Magic tab from Grab-
                                       # casting would otherwise leave Goat horns unclickable.
    controller.human_delay(0.2, 0.4)
    _drop_all(ctx, GOAT_HORN_NAME)   # "Drop" -- confirmed live Forced Action

    spilled_fur = any(
        (i.get("name") or "").lower() in {f.lower() for f in FUR_PRIORITY}
        for i in controller.get_inventory()
    )
    ctx.state.data["gc_pit_lined"] = False
    ctx.state.data["gc_pit_filled"] = False
    if spilled_fur:
        debug_log.log("golem crafting", "fur pouch full -- skipping bank, heading to mine phase")
        _drop_all(ctx, WOODEN_SPIKES_NAME)
        ctx.state.data["gc_fur_stock"] = FUR_POUCH_CAPACITY
        ctx.state.data["gc_hunt_standing"] = False
        ctx.state.data["gc_pouch_opened_this_hunt"] = False
        ctx.state.data["gc_hunt_actions"] = 0
        ctx.state.data["gc_phase"] = "mine"


PHASES = {
    "mine": mine_phase,
    "chisel": chisel_phase,
    "craft": craft_phase,
    "bank": bank_phase,
    "hunt": hunt_phase,
}


def tick(ctx):
    config = ctx.state.config

    if not ctx.state.data.get("gc_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["gc_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    # CORRECTED 2026-08-08 (live session, user request): starts at "bank" rather than "mine" --
    # a fresh activation should stock fur FIRST (withdrawn in full and pouched, see
    # _run_bank_trip's docstring), so mine_phase never has to compete with loose fur for
    # inventory slots even on the very first mining trip of a session.
    phase = ctx.state.data.get("gc_phase", "bank")
    handler = PHASES.get(phase)
    if handler is None:
        ctx.state.trigger_safety_stop(f"unknown golem crafting phase '{phase}'")
        return

    handler(ctx, config)
