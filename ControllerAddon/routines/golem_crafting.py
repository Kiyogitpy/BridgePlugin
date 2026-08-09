# routines/golem_crafting.py
#
# Golem Crafting routine, overall loop (CORRECTED 2026-08-08, live session, user-specified):
#   bank (stock fur, pouched) -> mine a batch of sunstone -> chisel PART of it into cores ->
#   craft up to one golem per core (auto-consumes fur too) -> bank again only once the tracked
#   fur stock actually runs low, otherwise straight back to mining -- with a detour to the Goat
#   Pit if the bank itself has no usable fur left at all.
#
# Fur is withdrawn in FULL and kept compacted in the fur pouch for the mine/chisel legs (so it
# never competes with raw sunstone for inventory slots -- CONFIRMED live this was a real
# problem: carrying 14+ loose fur while mining left too few slots to reach a full batch),
# unpacked only once per crafting batch, right before the first golem needs it, and re-packed
# before heading back out. See _run_bank_trip's docstring for the full reasoning, and
# craft_phase's for exactly where the pack/unpack points are.
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
#   - Material cost is front-loaded, not per-side and not core/fur-based: the FIRST side
#     ("Start-golem") consumes exactly 1 raw Sunstone (not a core!) if available, or fails with
#     "You don't have enough sunstone to assemble a golem." otherwise; the remaining 3 sides
#     ("Shape-golem") consume nothing further (confirmed once, n=1 -- if a future live run ever
#     sees a second side also consume sunstone, that assumption needs revisiting).
#   - The finishing move is "Insert-core" (not two separate core-then-fur clicks) -- CONFIRMED
#     live it auto-consumes both 1 Sunstone core AND the best available fur in inventory in one
#     click, dropping an uncut gem gift on the ground and awarding Crafting XP. It also only
#     works from a specific side ("You can't insert a sunstone core from this side of the
#     golem." from the wrong one) -- confirmed working from the same tile "Start-golem" was
#     used from; not confirmed whether that's coincidental or the actual rule, so craft_phase
#     below retries every untried side for Insert-core rather than assuming it's always the
#     start tile.
# See GOLEM_ADJACENT_OFFSETS/GOLEM_STATE_NAMES/on_event below for how this is now modeled.
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

import random
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

# CONFIRMED LIVE 2026-08-08 (MCP debug session): the crafting station's own displayed name
# changes as a golem progresses -- "Empty plinth" (nothing started) -> "Unfinished golem" (1-3
# sides carved) -> "Unpowered golem" (all 4 sides carved, needs Insert-core) -> back to "Empty
# plinth" once a golem is completed. find_golem_object() below matches any of these, replacing
# the original single-substring PLINTH_NAME (which stopped matching the moment carving started,
# since none of the later names contain "plinth" at all).
#
# CORRECTED 2026-08-08 (user report: "the plinth got crafted into a base in a previous loop and
# is now stuck"): a plinth doesn't actually reset to "Empty plinth" once it's been used at least
# once -- CONFIRMED live it instead reads "Golem base" from then on (same id, same tile,
# functionally identical to "Empty plinth" -- Start-golem works on it the same way). Missing
# this name entirely made find_golem_object's candidate filter (below) exclude the plinth
# outright once it reached this state -- not a transient miss handle_missing_target's retry
# budget could ever recover from, since the object was never going to reappear under a name
# this tuple recognized. Both of Wyrmscraig's plinths were observed in this state simultaneously
# after enough golems had been crafted at each, so this isn't a one-off fluke tied to one plinth.
GOLEM_STATE_NAMES = ("Empty plinth", "Golem base", "Unfinished golem", "Unpowered golem")

# CONFIRMED LIVE 2026-08-08 (MCP debug session): the 4 world-tile offsets (dx, dy) around the
# plinth/golem that were actually walked to and successfully carved from, one side each, in this
# order. OSRS world y increases northward. There is no confirmed requirement that sides must be
# carved in this specific order or that these are the ONLY 4 valid tiles -- just that 4 distinct
# adjacent tiles are needed and repeating one gets rejected (see craft_phase). If a future live
# run finds one of these tiles blocked/unreachable at a different plinth instance, this may need
# to become a live-computed "any of the object's walkable adjacent tiles" list instead of a fixed
# offset table.
GOLEM_ADJACENT_OFFSETS = ((0, 1), (1, 0), (0, -1), (-1, 0))  # N, E, S, W

# CONFIRMED live 2026-08-08 (user report): which of the 4 sides Insert-core actually accepts is
# fixed per plinth instance (each is independently oriented), not a single universal answer --
# the northern plinth (id 62351, see the "plinth" location anchor's own comment) takes it from
# its south side; the southern one (id 62352) takes it from its north side. Keyed by id (not
# position -- see find_golem_object's own docstring for why id is the stable identity here)
# so craft_phase's insert step can go straight to the right side instead of the original blind
# GOLEM_CHAT_WRONG_INSERT_SIDE trial-and-error across all 4. Any plinth id not in this table
# still falls back to that trial-and-error (see the insert-offset lookup below).
GOLEM_INSERT_OFFSET_BY_ID = {
    62351: (0, -1),  # northern plinth -> insert from the south
    62352: (0, 1),   # southern plinth -> insert from the north
}

# ADDED 2026-08-08 (live session, user report: "stuck in a loop"): a hard cap on how many extra
# Shape-golem attempts craft_phase will make beyond the normal 4 once every tracked offset is
# already marked done but the golem's own name still isn't "Unpowered golem" (see that fallback's
# own comment). Without this a genuinely stuck/corrupted plinth state (CONFIRMED live possible --
# this exact plinth had been interrupted mid-progress by many earlier test runs today) retried
# forever with no safety-stop.
#
# CORRECTED 2026-08-08 (user request): doubled from 8 -- the first GOLEM_EXTRA_SIDE_MISSES_BEFORE_
# ROTATE attempts are plain retries (give the chat-message-desync theory, see this cap's own
# use below, a fair unperturbed shot), and only the remaining attempts nudge the camera as a
# different approach. Bumping the cap to 16 rather than keeping it at 8 gives that rotated phase
# its own real run of attempts instead of just one last toss before giving up.
GOLEM_EXTRA_SIDE_ATTEMPTS_CAP = 16

# How many extra attempts to let run plain (no camera nudge) before switching to nudging the
# camera each attempt -- see GOLEM_EXTRA_SIDE_ATTEMPTS_CAP's own comment.
GOLEM_EXTRA_SIDE_MISSES_BEFORE_ROTATE = 8

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


def find_object(name: str):
    """Closest visible object whose name contains `name`, within MAX_OBJECT_DISTANCE -- same
    filter shape as blast_furnace.py's find_object/mining.py's pick_rock/banker.py's
    find_nearby_bank, kept local per this codebase's established convention.

    CORRECTED 2026-08-08 (MCP live debug session): passes radius=MAX_OBJECT_DISTANCE to
    controller.get_objects() -- CONFIRMED live this file's object lookups were doing a full,
    unbounded scene walk (ObjectProvider.java's own module comment already documents this as a
    measurable source of client stutter, which is exactly why the radius param exists) on every
    single call, several times a tick, and the resulting slowness was enough to blow api_get's
    default 0.3s timeout on /objects every tick -- every phase using find_object/find_golem_
    object/find_rocks was silently retrying past this on every call, never actually finding
    anything, "wrong location?" safety-stops firing even while standing right next to the target.
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


def find_golem_object(committed_id=None):
    """A single visible plinth/golem object in any of GOLEM_STATE_NAMES, within
    MAX_OBJECT_DISTANCE -- see that constant's docstring for why an exact-name-set match
    replaced the original single-substring find_object(PLINTH_NAME) call.

    Sticky, same idea as find_mining_target's own committed_pos, but keyed on the object's `id`
    rather than its (x, y) -- CONFIRMED live 2026-08-08 (live session, user report) an (x, y)-
    keyed version of this still lost track of an in-progress golem: Wyrmscraig has two plinths
    ~4 tiles apart, and the SAME physical plinth's own reported (x, y) can shift by a tile
    between its "Empty plinth"/"Unfinished golem"/"Unpowered golem" states (multi-tile object
    anchor quirk), so a position-keyed match failed to re-find it the very next tick after its
    first "Start-golem" click, read that as "a different plinth now", and started fresh progress
    on the OTHER plinth instead -- discarding the first one's pending confirmation entirely. `id`
    (CONFIRMED live stable across all 3 of a plinth's states, unlike (x, y)) doesn't have that
    problem. If given and still among the visible candidates, that id's object is returned
    regardless of whether something else is now nearer -- see this function's docstring history
    for why "closer to the other plinth this tick" must never be a reason to switch mid-golem.

    CORRECTED 2026-08-08 (user report: "wants to swap to the other plinth while still in
    crafting animation"): a committed golem can drop out of `candidates` for a tick or two
    entirely -- not just have something else become nearer -- if the crafting animation right
    after Start-golem transiently reports a name outside GOLEM_STATE_NAMES or leaves it briefly
    not `visible` (same class of gap as the Empty-plinth-despawning/Unfinished-golem-spawning
    race handle_missing_target's own docstring already documents). CONFIRMED live this used to
    fall through to `candidates[0]` in that case -- the closest OTHER candidate, i.e. the second
    plinth -- and craft_phase read the id mismatch as "a different plinth now" and abandoned the
    committed one mid-animation. Returning None instead when a commitment was given but not
    found lets the caller's existing transient-miss tolerance (handle_missing_target's
    MAX_CONSECUTIVE_MISSES, same as every other lookup in this file) absorb a brief animation
    blip, rather than silently substituting a different object with a different id."""
    candidates = [
        o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
        if (o.get("name") or "") in GOLEM_STATE_NAMES
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None

    if committed_id is not None:
        return next((o for o in candidates if o.get("id") == committed_id), None)

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


def find_mining_target(committed_pos=None):
    """A single object in MINING_TARGET_NAMES ("Sunstone rocks") to click -- sticky, not just
    "whichever is closest right now": if `committed_pos` (an (x, y) world tile) is given and
    still among the visible candidates, THAT one is returned regardless of whether something
    else is now nearer, only falling back to the closest candidate otherwise. Returns None if
    nothing is in range at all.

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
# arrived (e.g. one of MINING_TARGET_NAMES/GOLEM_STATE_NAMES/GOAT_PIT_NAME turning out to be a wrong
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
        travel.walk_to(ctx, target)
        ctx.state.data[miss_counter_key] = 0
        return True
    debug_log.log("golem crafting", f"{location_key} still not found after {MAX_WALK_ATTEMPTS} walk attempts toward the configured location")
    return False


# How much raw+chiselled sunstone to hold per trip, and how many of those become cores --
# CORRECTED 2026-08-08 (live session, user-specified batch shape): mine 20, chisel 4 of them into
# cores, leaving 16 raw sunstone (enough for 4 golems' "Start-golem" cost with plenty of reserve).
# This is a budget on raw_count + cores_have TOGETHER, not on raw sunstone alone -- see
# mine_phase's docstring for why that distinction is what fixes the overmining bug.
SUNSTONE_PER_TRIP = 20

# How long mine_phase will wait for raw_count to rise after a click before giving up on it and
# clicking again -- see mine_phase's own comment on its inventory-change gate for why a bound is
# needed at all (a click that lands nowhere useful, e.g. click_entity's own simulated near-miss,
# would otherwise wait for an inventory change that's never coming). Long enough to cover a
# genuine walk-and-swing to a distant rock (MAX_OBJECT_DISTANCE tiles away) without mistaking
# that for a stalled click.
MINE_RECLICK_TIMEOUT_S = 8.0


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
    own find_golem_object lookup handles the rest of the approach once chisel_phase is done.
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
    if last_raw is not None and raw_count <= last_raw and time.time() - last_click_time < MINE_RECLICK_TIMEOUT_S:
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
    time.sleep(random.uniform(0.15, 0.5))
    if not controller.click_entity(ctx.ser, rock):
        # ADDED 2026-08-08 (live session): find_mining_target's MAX_OBJECT_DISTANCE radius can
        # report a rock as "found" while it's still off the actual rendered viewport -- CONFIRMED
        # live for this same problem with the monolith right after a fresh bank trip, before
        # anything had moved the player closer. Same fix as bank_phase's own minimap-hop
        # fallback for the exact same class of problem.
        _hop_toward_point(ctx, rock["x"], rock["y"], rock.get("plane", 0))
        return

    ctx.state.data["gc_mine_last_raw_count"] = raw_count
    ctx.state.data["gc_mine_last_click_time"] = time.time()


# How many sunstone cores to chisel per mining trip (== golems per trip, one core each) --
# CORRECTED 2026-08-08 (live session, user request): chisel_phase used to convert EVERY raw
# sunstone into a core, which left nothing raw to ever start a NEW golem with ("Start-golem"
# consumes 1 raw sunstone specifically, not a core -- see this module's header comment).
# User-specified batch shape: mine SUNSTONE_PER_TRIP (20) sunstone, chisel 4 into cores (leaving
# 16 raw), craft 4 golems (1 raw + 1 core each, 4 of the 16 raw used, 12 carried into the next
# trip), then mine a fresh batch and repeat.
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
    time.sleep(random.uniform(0.15, 0.35))
    controller.click_entity(ctx.ser, chisel, check_ui_blockers=False)
    time.sleep(random.uniform(0.1, 0.25))
    controller.click_entity(ctx.ser, raw, check_ui_blockers=False)

    deadline = time.time() + CHISEL_CONFIRM_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(0.2)
        if _count_named(SUNSTONE_CORE_NAME) > cores_have:
            return
    debug_log.log("golem crafting", "chisel click didn't register within timeout -- retrying next tick")


# CHAT_MESSAGE substrings (lowercased) this routine reacts to -- see on_event below for why a
# chat-driven signal is needed at all (the game confirms/rejects a golem action on its own
# server-queued schedule, several ticks after the click, not synchronously). Every one of these
# was CONFIRMED live 2026-08-08 (MCP debug session) as the exact text the game sent back.
GOLEM_CHAT_SIDE_DONE = "you finish crafting the golem from this angle."
GOLEM_CHAT_SIDE_ALREADY_DONE = "you've already crafted this side of the golem."
GOLEM_CHAT_NOT_ENOUGH_SUNSTONE = "you don't have enough sunstone to assemble a golem."
GOLEM_CHAT_WRONG_INSERT_SIDE = "you can't insert a sunstone core from this side of the golem."
GOLEM_CHAT_COMPLETE = "you have crafted"  # "...N golem(s) on Wyrmscraig." -- prefix match
# ADDED 2026-08-08 (user report): the real rejection when Insert-core is clicked with no usable
# fur left in the pouch -- gc_fur_stock is only ever a locally-tracked estimate (set from a bank
# withdrawal's qty, decremented by 1 per completed golem), never corrected against what hunt_phase
# actually catches, so it can read >0 while the pouch is genuinely empty. This message is the
# game's own authoritative answer; see its handling in craft_phase's "insert" branch below for why
# it needs to be recognized at all, not just handled once seen.
GOLEM_CHAT_NOT_ENOUGH_FUR = "you need to dress the golem in furs from hunted creatures."

# CONFIRMED live (user session) -- Goat Pit hunting mechanic, replacing the original wrong
# plain-click-lures/harvests assumption. See docs/superpowers/specs/2026-08-08-goat-pit-
# telekinetic-grab-design.md for the full mechanic writeup.
GOAT_CHAT_PIT_FULL = "the pit is now filled with goats."
# Rune name varies (law/air), so this matches on the stable surrounding text rather than a
# single fixed string -- both substrings must be present.
GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS = ("you do not have enough", "to cast this spell")
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
# actually reaching the pit -- bumped by the requested extra 3-5s.
GOAT_GRAB_COOLDOWN_S = 8.0


def on_event(ctx, event_type, payload) -> None:
    """Captures the golem-crafting confirmation/rejection chat messages into
    ctx.state.data["gc_last_chat"]/["gc_last_chat_seq"] as they arrive -- controller.py's main
    loop drains UDP events and calls this before every tick() (see controller.py's main()), so
    a message that arrives between two tick() calls is caught here and available to craft_phase
    on its next call. gc_last_chat_seq is a monotonic counter, not just the text, so craft_phase
    can tell "no new message since I started waiting" apart from "a new message arrived that
    happens to repeat old text" -- comparing the text alone couldn't distinguish those. Ignores
    every other chat message (this routine has no other use for chat) so unrelated game/spam
    messages can't overwrite a still-unconsumed golem-crafting one.

    ADDED 2026-08-08 (Goat Pit hunting redesign): also captures hunt_phase's own two chat
    signals (pit-full, rune-shortage) into a SEPARATE gc_last_hunt_chat/_seq pair, not the
    golem-crafting one above -- controller.py only ever calls one on_event per routine module
    (see its own dispatch, `hasattr(routine, "on_event")`), so both concerns have to live in
    this one function, but craft_phase and hunt_phase are mutually exclusive via gc_phase and
    keeping their chat state in separate keys avoids any future ambiguity if that ever changes."""
    if event_type != "CHAT_MESSAGE" or not payload:
        return
    message = (payload.get("message") or "").lower()

    if any(known in message for known in (
        GOLEM_CHAT_SIDE_DONE, GOLEM_CHAT_SIDE_ALREADY_DONE, GOLEM_CHAT_NOT_ENOUGH_SUNSTONE,
        GOLEM_CHAT_WRONG_INSERT_SIDE, GOLEM_CHAT_COMPLETE, GOLEM_CHAT_NOT_ENOUGH_FUR,
    )):
        ctx.state.data["gc_last_chat"] = message
        ctx.state.data["gc_last_chat_seq"] = ctx.state.data.get("gc_last_chat_seq", 0) + 1

    is_pit_full = GOAT_CHAT_PIT_FULL in message
    is_rune_short = all(part in message for part in GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS)
    if is_pit_full or is_rune_short:
        ctx.state.data["gc_last_hunt_chat"] = message
        ctx.state.data["gc_last_hunt_chat_seq"] = ctx.state.data.get("gc_last_hunt_chat_seq", 0) + 1


# How long craft_phase waits for a chat confirmation before giving up on one attempt and
# retrying -- CONFIRMED live 2026-08-08: real confirmations arrived within a few seconds of
# the click every time observed; generous margin for a slower tick under load.
GOLEM_ACTION_TIMEOUT_S = 15.0


def walk_to_exact_tile(ctx, x: int, y: int, plane: int) -> bool:
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

    CORRECTED 2026-08-09 (user report: "accidentally clicked the pit and started emptying it"
    while walking back near it): a plain click at a ground point close to an interactive object
    (the Goat Pit is only 2 tiles from hunt_phase's standing tile) can resolve to the OBJECT's
    own action instead of walking there, if their clickable areas overlap on screen -- CONFIRMED
    live this cleared the pit (removing its lining) mid walk-back. Explicit "Walk here" menu
    selection guarantees the intended action regardless of what's nearby, same reasoning
    controller.click_menu_action already documents for any click point something else can share."""
    point = controller.get_ground_walk_point(x, y, plane)
    if not point.get("onScreen"):
        return False
    if not controller.click_menu_action(ctx.ser, point, "walk", offset=(0, 0)):
        return False
    waited = 0.0
    while waited < 8.0:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        if player.get("x") == x and player.get("y") == y:
            return True
    return True


def craft_phase(ctx, config: dict) -> None:
    """Craft one golem per call to gc_phase="craft" overall (carve all 4 sides of a plinth from
    4 distinct adjacent tiles, then insert-core) -- sized like blast_furnace.py's run_trip (one
    bounded unit of work per call), so the engine's between-tick safety/break/PvP checks still
    run on a long crafting stretch. tick() keeps re-entering this phase until it sets gc_phase
    to "bank" itself once materials run out.

    CORRECTED 2026-08-08 (MCP live debug session, full end-to-end verification): replaces this
    function's original model, which was wrong in every particular once actually tested -- see
    this module's header comment for the full list of what was wrong and what's confirmed
    correct instead. In short: 4 distinct adjacent tiles (not 4 identical clicks), a chat-message-
    driven confirmation loop (not a synchronous click-and-assume-success loop, since the game
    confirms/rejects several ticks after the click -- see on_event), 1 raw Sunstone consumed
    once up front (not cores/fur consumed per side), and a single Insert-core click that
    auto-consumes both the core and the best fur together (not two separate insert clicks).

    This phase is a small state machine tracked in ctx.state.data, all keyed off
    "gc_craft_golem_id" (the plinth/golem object's own `id` -- CONFIRMED live stable across its
    whole Empty plinth -> Unfinished golem -> Unpowered golem progression, unlike its (x, y),
    which can shift by a tile between those states -- see find_golem_object's own docstring for
    why that distinction is what fixes a real live bug -- so this is how a fresh start is told
    apart from a resumed one):
      gc_craft_sides_done: list of (dx, dy) offsets already confirmed carved for this golem.
      gc_craft_insert_tried: list of (dx, dy) offsets Insert-core has already been rejected
        from (see GOLEM_CHAT_WRONG_INSERT_SIDE -- confirmed live it's side-dependent, but not
        confirmed which specific side is always correct, so every untried side gets a turn).
      gc_craft_awaiting: {"kind": "side"|"insert", "offset": (dx,dy), "since_seq", "started_at"}
        while a click's chat confirmation hasn't arrived yet, else None.
    """
    golem = find_golem_object(ctx.state.data.get("gc_craft_golem_id"))
    if golem is None:
        if handle_missing_target(ctx, config, "plinth", "gc_plinth_misses", "gc_plinth_walks"):
            return
        ctx.state.trigger_safety_stop("no plinth/golem found nearby")
        return
    ctx.state.data["gc_plinth_misses"] = 0
    ctx.state.data["gc_plinth_walks"] = 0

    if ctx.state.data.get("gc_craft_golem_id") != golem.get("id"):
        # A different plinth instance, or this one just reset to "Empty plinth" after a
        # previous golem completed -- start fresh tracking for it.
        ctx.state.data["gc_craft_golem_id"] = golem.get("id")
        ctx.state.data["gc_craft_sides_done"] = []
        ctx.state.data["gc_craft_insert_tried"] = []
        ctx.state.data["gc_craft_awaiting"] = None
        ctx.state.data["gc_craft_extra_side_attempts"] = 0

    awaiting = ctx.state.data.get("gc_craft_awaiting")
    if awaiting is not None:
        seen_seq = ctx.state.data.get("gc_last_chat_seq", 0)
        if seen_seq <= awaiting["since_seq"]:
            if time.time() - awaiting["started_at"] > GOLEM_ACTION_TIMEOUT_S:
                debug_log.log("golem crafting", f"no confirmation after {GOLEM_ACTION_TIMEOUT_S}s -- retrying")
                ctx.state.data["gc_craft_awaiting"] = None
            return  # still waiting on a chat confirmation -- nothing else to do this call

        message = ctx.state.data.get("gc_last_chat", "")
        offset = awaiting["offset"]
        ctx.state.data["gc_craft_awaiting"] = None

        if awaiting["kind"] == "side":
            if GOLEM_CHAT_SIDE_DONE in message or GOLEM_CHAT_SIDE_ALREADY_DONE in message:
                # Either a real success, or confirmation this side was already done (e.g. after
                # a process restart lost our in-memory tracking) -- either way it's spent now.
                sides = ctx.state.data.get("gc_craft_sides_done", [])
                if offset not in sides:
                    sides.append(offset)
                ctx.state.data["gc_craft_sides_done"] = sides
                debug_log.log("golem crafting", f"side confirmed ({len(sides)}/4): {message.strip()}")
            elif GOLEM_CHAT_NOT_ENOUGH_SUNSTONE in message:
                debug_log.log("golem crafting", "not enough raw sunstone to start a golem -- moving to mine phase")
                ctx.state.data["gc_phase"] = "mine"
            else:
                debug_log.log("golem crafting", f"unexpected response to golem side click: {message.strip()}")
        else:  # kind == "insert"
            if GOLEM_CHAT_WRONG_INSERT_SIDE in message:
                tried = ctx.state.data.get("gc_craft_insert_tried", [])
                tried.append(offset)
                ctx.state.data["gc_craft_insert_tried"] = tried
                debug_log.log("golem crafting", f"can't insert-core from this side -- trying another (tried {len(tried)}/4)")
            elif GOLEM_CHAT_NOT_ENOUGH_FUR in message:
                # The game's own answer overrides our locally-tracked gc_fur_stock estimate --
                # see that field's docstring and GOLEM_CHAT_NOT_ENOUGH_FUR's own comment for why
                # the tracked count can read >0 while the pouch is actually empty. Zeroing it here
                # (rather than leaving it for the next batch-end check) means the very next
                # craft_phase call routes to bank via the existing gc_fur_stock<=0 gate instead of
                # retrying the identical doomed Insert-core click.
                debug_log.log("golem crafting", "out of fur (game rejected insert-core) -- correcting tracked fur stock to 0")
                ctx.state.data["gc_fur_stock"] = 0
                ctx.state.data["gc_phase"] = "bank"
                return
            elif GOLEM_CHAT_COMPLETE in message:
                debug_log.log("golem crafting", "golem finished -- ready for the next one")
                # ADDED 2026-08-08 (live session, user report): the plinth stays unresponsive for
                # about 2 seconds after a golem finishes before it resets to "Empty plinth" and
                # can start a new one -- CONFIRMED live. Rather than spend that unavoidable dead
                # time uselessly hammering find_golem_object every tick against a plinth that
                # isn't ready yet, use it to pick up whatever the golem dropped right where it
                # stood, then a short settle sleep -- same "Take" idiom as slayer.py's own
                # ground-item pickup (right-click + explicit menu selection, not a plain
                # click_entity, since a plain click's default action isn't reliably "Take").
                drops = [
                    i for i in controller.get_ground_items()
                    if abs(i.get("x", 9999) - golem.get("x", 0)) <= 1
                    and abs(i.get("y", 9999) - golem.get("y", 0)) <= 1
                ]
                for drop in drops:
                    drop_key = (drop.get("id"), drop.get("x"), drop.get("y"))
                    debug_log.log("golem crafting", f"picking up {drop.get('name')}")
                    # CORRECTED 2026-08-08 (live session, user report: "it missed looting its
                    # gem, we want always the loot"): a single click_menu_action attempt isn't
                    # reliable enough on its own -- CONFIRMED live one attempt landed on the bare
                    # tile's own menu (Cancel/Mark Safespot/Walk here) instead of the item's
                    # (Take/Examine/Cancel), same class of race slayer.py's own maybe_loot
                    # documents (the camera/model rendering trails the logical tile position by
                    # about a tick right after a state change -- here, the golem just finished).
                    # Retries a few times, re-fetching the item's position fresh each attempt
                    # (not reusing the same possibly-stale drop dict) and stopping as soon as it's
                    # actually gone from the ground -- confirmed picked up, not just clicked at.
                    for _ in range(3):
                        fresh = next(
                            (i for i in controller.get_ground_items()
                             if (i.get("id"), i.get("x"), i.get("y")) == drop_key),
                            None,
                        )
                        if fresh is None:
                            break  # gone -- picked up (or despawned/looted by something else)
                        controller.click_menu_action(ctx.ser, fresh, "take", target_hint=fresh.get("name"), offset=(0, 0))
                        # 0.6-1.0s, not the 0.4-0.6s this used to be -- CONFIRMED live 2026-08-08
                        # (user report: a successful take still got re-clicked) 0.4s can be shorter
                        # than a single game tick (0.6s), so the very next iteration's fresh-lookup
                        # could poll before the game had processed the click that just succeeded,
                        # still see the item sitting there, and fire a second take on top of it.
                        # Matches the same click-then-wait-a-tick-before-rechecking idiom already
                        # used elsewhere in this file (e.g. the minimap-hop retry above).
                        time.sleep(random.uniform(0.6, 1.0))
                # CORRECTED 2026-08-08 (live session, user report): 1.0s here still weighed in
                # too early against the plinth's own ~2s reset delay -- CONFIRMED live. Bumped to
                # 2.0s (on top of whatever the loot loop above already spent).
                time.sleep(2.0)

                ctx.state.data["gc_craft_sides_done"] = []
                ctx.state.data["gc_craft_insert_tried"] = []
                ctx.state.data["gc_craft_golem_id"] = None  # force a fresh-start detection next call
                # One fur consumed per golem -- see _run_bank_trip's docstring for why this
                # tracked count (not a live inventory/pouch read) drives the end-of-batch
                # bank-or-mine decision below.
                ctx.state.data["gc_fur_stock"] = max(0, ctx.state.data.get("gc_fur_stock", 0) - 1)
            else:
                debug_log.log("golem crafting", f"unexpected response to insert-core: {message.strip()}")
        return

    sides_done = ctx.state.data.get("gc_craft_sides_done", [])

    # CORRECTED 2026-08-08 (live session, user report: "we are stuck in a loop"): this gate used
    # to be `len(sides_done) < 4` -- our OWN click-confirmation count -- rather than the golem
    # object's own reported name. CONFIRMED live these can desync: a plinth left mid-progress
    # from an earlier interrupted run/process restart still has its real carved-side state
    # server-side, but ctx.state.data resets to sides_done=[] on every restart, so this session's
    # 4 tracked offsets don't necessarily correspond to the 4 real remaining sides -- one real
    # side can get (correctly) reported "already crafted" more than once while another genuinely
    # untried side never gets attempted at all. The result: sides_done reached 4 while the golem
    # was still actually "Unfinished golem", and every subsequent Insert-core attempt failed with
    # "no 'insert-core' entry in menu" forever (that option only exists on "Unpowered golem").
    # Gating on the object's own name instead is self-correcting -- it only ever proceeds to
    # Insert-core once the game itself says the golem is actually ready.
    if golem.get("name") != "Unpowered golem":
        if not sides_done:
            # Only checked before the FIRST side -- confirmed live the material cost is
            # front-loaded (1 raw sunstone on "Start-golem", nothing further per side), so
            # there's no need to re-check core/fur availability between sides; only that we
            # won't strand a half-carved golem without enough to ever finish it.
            #
            # CORRECTED 2026-08-08 (live session, user request): a raw-sunstone/core shortage
            # means this whole mining trip's batch is done -- decide bank vs. mine from the
            # tracked fur stock, not from bank_phase's original uniform "always go to mine"
            # default. Routes to "mine" for the shortage itself regardless (bank_phase has no
            # way to restock sunstone -- Wyrmscraig's bank only ever holds fur), same as before.
            if find_item(RAW_SUNSTONE_NAME) is None or find_item(SUNSTONE_CORE_NAME) is None:
                fur_stock = ctx.state.data.get("gc_fur_stock", 0)
                if fur_stock < CORES_PER_TRIP:
                    debug_log.log("golem crafting", f"batch done, fur stock low ({fur_stock}) -- moving to bank phase")
                    ctx.state.data["gc_phase"] = "bank"
                else:
                    debug_log.log("golem crafting", f"batch done, fur stock still sufficient ({fur_stock}) -- moving to mine phase")
                    ctx.state.data["gc_phase"] = "mine"
                return

            # CORRECTED 2026-08-08 (live session, live-testing discovery: "we don't actually have
            # to empty the furs, we can use them from within the fur pouch"): Insert-core auto-
            # consumes the best available fur straight out of the pouch, same as it would from
            # loose inventory -- CONFIRMED live across three full golems this session with the
            # pouch never once emptied. The unpack-once-per-batch step (banker.empty_fur_pouch)
            # this replaced existed only because that wasn't known yet; removing it means there's
            # never any loose fur to lose track of or re-pouch, so gc_fur_stock (already tracked
            # for the batch-end bank-or-mine decision above) is now also the sole source of truth
            # for "is there fur to use", in place of a live pick_best_fur_in_inventory() scan that
            # only ever found anything after that now-removed unpack step.
            if ctx.state.data.get("gc_fur_stock", 0) <= 0:
                debug_log.log("golem crafting", "out of usable fur for a new golem -- moving to bank phase")
                ctx.state.data["gc_phase"] = "bank"
                return

            # ADDED 2026-08-08 (live session, live-testing discovery): "Start-golem" on an Empty
            # plinth -- unlike every later "Shape-golem" click -- auto-completes whichever side
            # it's clicked from as a side effect of starting the golem, regardless of which of
            # the 4 tiles that happens to be. CONFIRMED live across all 3 golems this session:
            # the chat response to this very first click was always GOLEM_CHAT_SIDE_ALREADY_DONE
            # ("you've already crafted this side"), never GOLEM_CHAT_SIDE_DONE -- meaning the
            # walk_to_exact_tile call below (to GOLEM_ADJACENT_OFFSETS[0] specifically, since
            # sides_done is still empty here) was carving a side that was going to be free no
            # matter where it was clicked from. Skipping the walk entirely and clicking
            # Start-golem from wherever the routine already is saves a full walk+confirm round
            # trip on every golem for no loss -- GOLEM_ADJACENT_OFFSETS[0] is still recorded
            # below purely as bookkeeping (which of the 4 offsets is "spent"), not because the
            # routine actually stood there.
            debug_log.log("golem crafting", "starting golem (0/4 done)")
            if not controller.click_menu_action(ctx.ser, golem, "golem"):
                # CORRECTED 2026-08-08 (live session, user report: "stuck now, probably a
                # misclick we don't correct for"): CONFIRMED live this looped forever -- unlike
                # shaping/inserting (which always walk_to_exact_tile first, landing adjacent and
                # on-screen), this fast-path deliberately clicks from wherever the routine
                # already is (see the comment above) with no positioning step at all, so a golem
                # that resets off-screen after the routine arrives from somewhere else (e.g.
                # straight from chisel_phase) hit "aim point is outside the RuneLite window"
                # every single tick with nothing ever correcting the position. Hopping toward the
                # golem's own tile -- the same off-screen fallback mine_phase/bank_phase already
                # use -- fixes it instead of retrying the identical failing click indefinitely.
                debug_log.log("golem crafting", "Start-golem click missed/no matching menu entry -- hopping closer and retrying next tick")
                _hop_toward_point(ctx, golem["x"], golem["y"], golem.get("plane", 0))
                return

            ctx.state.data["gc_craft_awaiting"] = {
                "kind": "side", "offset": GOLEM_ADJACENT_OFFSETS[0],
                "since_seq": ctx.state.data.get("gc_last_chat_seq", 0),
                "started_at": time.time(),
            }
            return

        offset = next((o for o in GOLEM_ADJACENT_OFFSETS if o not in sides_done), None)
        is_extra_attempt = offset is None
        if is_extra_attempt:
            # All 4 tracked offsets are already marked done, yet the golem's own name says
            # otherwise (see the gate comment above) -- this session's offset<->real-side mapping
            # has desynced from the game's actual state. Cycle back through them again rather
            # than crashing (next() on an empty generator) or getting stuck: each retry still
            # goes through the normal chat-confirmation path below, so a genuinely-untried real
            # side eventually gets hit and reported as a real completion, not another "already
            # done".
            #
            # CORRECTED 2026-08-08 (live session, user report: "stuck in a loop"): indexing by
            # `len(sides_done)` here was itself the bug, not just this branch's existence --
            # CONFIRMED live sides_done stays fixed at exactly 4 forever once every offset is in
            # it (the "already done" handler only appends an offset the FIRST time it sees it),
            # so `len(sides_done) % 4` always computed the same index -- this retried the SAME
            # offset every single time, forever, rather than actually cycling. A dedicated
            # counter that increments every retry (capped, see GOLEM_EXTRA_SIDE_ATTEMPTS_CAP)
            # fixes both the infinite loop and the "never actually tries a different offset" bug
            # in one move.
            attempts = ctx.state.data.get("gc_craft_extra_side_attempts", 0)
            if attempts >= GOLEM_EXTRA_SIDE_ATTEMPTS_CAP:
                ctx.state.trigger_safety_stop(
                    f"golem at plinth id={golem.get('id')} never reached Unpowered golem after "
                    f"{GOLEM_EXTRA_SIDE_ATTEMPTS_CAP} extra Shape-golem attempts beyond the "
                    "normal 4 -- likely a stuck/corrupted plinth state, needs a manual look"
                )
                return
            offset = GOLEM_ADJACENT_OFFSETS[attempts % len(GOLEM_ADJACENT_OFFSETS)]
            debug_log.log("golem crafting", f"golem still not ready for Insert-core after 4 tracked sides -- retrying offset {offset} (extra attempt {attempts + 1}/{GOLEM_EXTRA_SIDE_ATTEMPTS_CAP})")
            # ADDED 2026-08-08 (user report: a run that hit the safety-stop below turned out to be
            # a genuine misclick earlier in this golem's session, not a truly stuck plinth -- the
            # offset<->real-side mapping had desynced, and re-walking to the same 4 fixed tiles
            # under the SAME camera angle every cycle reproduced the identical "already crafted"
            # menu resolution 8 times in a row, never a real completion. Nudging the camera changes
            # what's actually under the click -- a genuinely different attempt instead of a
            # mechanical repeat of one that already isn't working.
            #
            # CORRECTED 2026-08-08 (user request): only once misses reach
            # GOLEM_EXTRA_SIDE_MISSES_BEFORE_ROTATE, not every extra attempt from the start -- the
            # first batch of plain retries give the chat-message-desync theory (this branch's own
            # docstring) a fair, unperturbed shot; only once those alone haven't worked does every
            # further attempt also nudge the camera.
            if attempts >= GOLEM_EXTRA_SIDE_MISSES_BEFORE_ROTATE:
                controller.nudge_camera_yaw(ctx.ser, "LEFT" if attempts % 2 else "RIGHT")
        if not walk_to_exact_tile(ctx, golem["x"] + offset[0], golem["y"] + offset[1], golem.get("plane", 0)):
            debug_log.log("golem crafting", f"couldn't walk to golem side offset {offset} -- retrying next tick")
            return

        golem = find_golem_object(ctx.state.data.get("gc_craft_golem_id"))  # fresh lookup -- screen position changed after walking
        if golem is None:
            debug_log.log("golem crafting", "golem no longer found after walking to a side -- retrying next tick")
            return

        debug_log.log("golem crafting", f"shaping golem from offset {offset} ({len(sides_done)}/4 done)")
        if not controller.click_menu_action(ctx.ser, golem, "golem"):
            debug_log.log("golem crafting", "Shape-golem click missed/no matching menu entry -- retrying next tick")
            return

        # ADDED 2026-08-08 (user report: "when we misclick we don't count it as a try?"): only
        # burns an extra attempt now that a click has genuinely landed (a real menu selection
        # happened and a real chat response is coming) -- previously this incremented the instant
        # the routine entered the extra-attempt branch above, so a failed walk, a golem that went
        # missing after walking, or a plain missed click (no matching menu entry -- nothing
        # happened in-game at all) each silently burned budget identical to a real "already
        # crafted" rejection, shrinking the CAP's actual headroom for genuine attempts.
        if is_extra_attempt:
            ctx.state.data["gc_craft_extra_side_attempts"] = ctx.state.data.get("gc_craft_extra_side_attempts", 0) + 1

        ctx.state.data["gc_craft_awaiting"] = {
            "kind": "side", "offset": offset,
            "since_seq": ctx.state.data.get("gc_last_chat_seq", 0),
            "started_at": time.time(),
        }
        return

    tried = ctx.state.data.get("gc_craft_insert_tried", [])
    # Go straight to the known-correct side for this plinth (see GOLEM_INSERT_OFFSET_BY_ID) if it
    # hasn't already been rejected -- only falls back to trying the other 3 blind if that lookup
    # is somehow wrong for this instance, or the plinth id isn't one of the two known ones.
    known_offset = GOLEM_INSERT_OFFSET_BY_ID.get(golem.get("id"))
    if known_offset is not None and known_offset not in tried:
        offset = known_offset
    else:
        offset = next((o for o in GOLEM_ADJACENT_OFFSETS if o not in tried), None)
    if offset is None:
        ctx.state.trigger_safety_stop("Insert-core was rejected from all 4 sides of the golem")
        return

    core = find_item(SUNSTONE_CORE_NAME)
    if core is None:
        debug_log.log("golem crafting", "sunstone core went missing before inserting -- moving to mine phase")
        ctx.state.data["gc_phase"] = "mine"
        return
    # CORRECTED 2026-08-08 (live session, live-testing discovery): fur is consumed straight from
    # the pouch (see the batch-loop comment above), so gc_fur_stock is the only availability
    # signal there is here -- there's no loose fur item left to look up a name from.
    if ctx.state.data.get("gc_fur_stock", 0) <= 0:
        debug_log.log("golem crafting", "fur ran out before inserting -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        return

    if not walk_to_exact_tile(ctx, golem["x"] + offset[0], golem["y"] + offset[1], golem.get("plane", 0)):
        debug_log.log("golem crafting", f"couldn't walk to insert-core side offset {offset} -- retrying next tick")
        return

    golem = find_golem_object(ctx.state.data.get("gc_craft_golem_id"))  # fresh lookup -- screen position changed after walking
    if golem is None:
        debug_log.log("golem crafting", "golem no longer found before inserting core -- retrying next tick")
        return

    debug_log.log("golem crafting", f"inserting core + fur from offset {offset}")
    if not controller.click_menu_action(ctx.ser, golem, "insert-core"):
        debug_log.log("golem crafting", "Insert-core click missed/no matching menu entry -- retrying next tick")
        return

    ctx.state.data["gc_craft_awaiting"] = {
        "kind": "insert", "offset": offset,
        "since_seq": ctx.state.data.get("gc_last_chat_seq", 0),
        "started_at": time.time(),
    }


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
# Unpacked once per crafting batch (craft_phase, right before the first golem needs it) and
# re-packed once the batch ends (also craft_phase, before heading back to mine or bank) -- see
# that function's own comments for the full mine -> chisel -> craft -> (mine | bank) loop this
# enables: only detour to the bank when the tracked "gc_fur_stock" count actually runs low
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
    docstring for the pouched-fur design), and unpacking it here would immediately undo that.
    craft_phase owns unpacking now, once per crafting batch, right before the first golem needs
    it -- including fur caught while HUNTING, which routes into the pouch the same way (see
    hunt_phase) and stays there, compacted, until that same craft_phase unpack point.

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
                    time.sleep(random.uniform(0.6, 1.0))
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
        time.sleep(random.uniform(0.3, 0.6))
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
    time.sleep(random.uniform(0.4, 0.7))
    return find_item(WOODEN_SPIKES_NAME) is not None


# How long to wait for the Goat Pit's menu to switch from "Clear" to "Line" before giving up and
# proceeding anyway -- ADDED 2026-08-09 (user request). Generous margin: the collection animation
# was observed to "take a while", and proceeding too early is exactly the bug this guards against,
# so this errs toward patience over a tight bound.
PIT_CLEAR_TIMEOUT_S = 20.0


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
        time.sleep(random.uniform(0.6, 1.0))
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
                time.sleep(random.uniform(0.3, 0.5))
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
        controller.cast_telekinetic_grab(ctx.ser, target)
        if target.get("index") is not None:
            cooldowns[target["index"]] = now + GOAT_GRAB_COOLDOWN_S
        time.sleep(random.uniform(0.6, 1.0))

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
        return

    # Pit is full -- harvest.
    pit = find_object(GOAT_PIT_NAME)  # fresh lookup, same idiom as craft_phase's re-checks
    if pit is None:
        debug_log.log("golem crafting", "pit no longer found before harvesting -- retrying next tick")
        return
    controller.click_entity(ctx.ser, pit)
    time.sleep(random.uniform(0.6, 1.0))

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
    time.sleep(random.uniform(0.2, 0.4))
    for horn in [i for i in controller.get_inventory()
                 if GOAT_HORN_NAME.lower() in (i.get("name") or "").lower()]:
        controller.click_entity(ctx.ser, horn, check_ui_blockers=False)  # "Drop" (confirmed
                                                                          # live Forced Action)
        time.sleep(random.uniform(0.2, 0.4))

    spilled_fur = any(
        (i.get("name") or "").lower() in {f.lower() for f in FUR_PRIORITY}
        for i in controller.get_inventory()
    )
    ctx.state.data["gc_pit_lined"] = False
    ctx.state.data["gc_pit_filled"] = False
    if spilled_fur:
        debug_log.log("golem crafting", "fur pouch full -- skipping bank, heading to mine phase")
        for spikes in [i for i in controller.get_inventory()
                       if WOODEN_SPIKES_NAME.lower() in (i.get("name") or "").lower()]:
            controller.click_entity(ctx.ser, spikes, check_ui_blockers=False)  # "Drop"
            time.sleep(random.uniform(0.2, 0.4))
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
