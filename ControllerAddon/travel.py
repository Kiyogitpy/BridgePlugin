# travel.py
#
# Location memory + long-distance travel. Lives alongside controller.py
# (not in routines/) since it's shared infrastructure other routines/
# banker.py import, the same reason `import controller` already works from
# any routine file.
#
# What's here:
#   save_location()      -- record the current world tile before leaving
#   teleport_to_ferox()  -- ring of dueling -> Ferox Enclave
#   use_fountain()       -- click Ferox's free HP/prayer/special fountain
#   walk_to(target)      -- get back to a saved location
#   walk_to_exact_tile() -- shift+click force-walk onto ONE specific tile and
#                           wait until standing exactly on it (no tolerance)
#
# walk_to routes through worldmap.find_path -- a BFS over a static, pre-baked, world-wide
# collision map (worldmap.py), not the separately-installed Shortest Path plugin this used to
# depend on. route_to asks for that route; walk_to then walks it by repeatedly clicking a
# waypoint some tiles ahead via the minimap and letting the game's own pathfinder handle each
# short hop -- the look-ahead distance is randomized per hop (never below WALK_HOP_MIN_TILES)
# rather than a fixed number, so consecutive walks don't look identical. The static map treats
# every door as open, so a route can run straight through one that's actually shut; walk_to
# catches that against LIVE collision (GET /collision) before each hop and opens the door in the
# way (see the "closed doors" section below, and open_door_toward for the standalone version of
# the same check). There is no obstacle-blind fallback any more: a target with no route at all
# now returns False rather than hop-clicking a wall for up to eight minutes.
#
# ASSUMPTIONS TO VERIFY IN-GAME:
#   - The ring of dueling's Ferox Enclave option is a direct right-click
#     option on the item (no "Rub" submenu first). For a plain left-click
#     to resolve to it, add "Ferox Enclave" to Controller Settings ->
#     Clicking -> Forced Actions. (Confirmed correct.)

import math
import random
import time

import controller
import debug_log
import worldmap

RING_OF_DUELING_NAME = "ring of dueling"
FOUNTAIN_NAME = "Pool of Refreshment"
FOUNTAIN_MAX_DISTANCE = 15

# Royal seed pod: used as the wilderness panic-escape teleport specifically
# because (recalled, not re-verified in-game this session) it still works
# while teleblocked, unlike most other teleports -- the whole point of an
# escape item used only when a PKer is already in attack range.
ROYAL_SEED_POD_NAME = "royal seed pod"

# How far ahead each hop aims. RAISED 2026-08-28 from 10-16 (user request: "click a bit further
# than we are today by like 5-10 tiles so we don't stop between each click") -- a hop ends with the
# player standing still while the next click is aimed and flown, so longer hops mean fewer of those
# stops over the same distance.
#
# The old 16 was guessing at the minimap's reach. MEASURED live 2026-08-28 from (3037,3345):
# /minimap/walk reports onMinimap out to 28 tiles in ALL EIGHT directions, and fails first on the
# DIAGONALS at 30 -- a corner sits sqrt(2)x further from the minimap's centre than a cardinal at
# the same tile distance, so the diagonals are what bound this, not the cardinals (which were
# still on at 32). 24 keeps four tiles of margin under that measured 28, and _hop_to's pull-in
# retry (see HOP_PULL_IN_FRACTIONS) covers the case the margin doesn't: a player who has zoomed
# their own minimap in.
WALK_HOP_MIN_TILES = 16      # never aim a hop shorter than this
WALK_HOP_MAX_TILES = 24      # ...or longer than this, to stay safely within minimap range

# Fractions of the way to a hop point that _hop_to will settle for, in order, when the point
# itself isn't on screen or the minimap. 1.0 is the requested point, so an aim that works costs
# nothing extra.
# At or under this many tiles a hop uses a VIEWPORT click, which lands on the exact projected
# tile; beyond it the minimap is used instead (see _hop_to's docstring for the measurement that
# drove the inversion). First-draft value -- it wants a live pass to tune, the same way the hop
# distances themselves were measured rather than guessed.
HOP_VIEWPORT_MAX_TILES = 6

HOP_PULL_IN_FRACTIONS = (1.0, 0.66, 0.4)

# Release a hop's arrival wait with this many tiles still to run, so the NEXT hop's aim + mouse
# flight (~1.5-2s: 0.6s poll quantization + walk_to's collision/door bookkeeping + the flight)
# happens WHILE the player covers them, instead of after the player has arrived and stopped.
# USER-REPORTED 2026-08-30 ("we wait so long the player runs to the tile we clicked before we
# click the next point resulting in a weird quick stop") and MEASURED in the same night's
# recordings: 0.6-1.8s dead stops at EVERY hop boundary, because WALK_ARRIVAL_TOLERANCE (3
# tiles, ~0.9s of running at 2 tiles/tick) is less runway than the next click needs. 6 tiles is
# ~1.8s. Only hops LONGER than this get the early release -- a short precision hop releasing
# instantly would just spam clicks at its own destination.
HOP_NEXT_CLICK_LEAD_TILES = 6
WALK_ARRIVAL_TOLERANCE = 3   # tiles; close enough to call a hop, or the whole trip, arrived
WALK_HOP_TIMEOUT_S = 8.0     # give up waiting on a single hop after this long
WALK_MAX_HOPS = 60           # hard cap so a bad target can't loop forever

COLLISION_SCAN_RADIUS = 10   # tiles of live collision fetched per hop. Big enough to see the
                             # doors along one hop, small enough that a 21x21 response per hop
                             # stays cheap. Beyond it we simply hop and let the stall catch it.
WALK_MAX_REROUTES = 4        # times one walk may re-ask for a route with a tile excluded because
                             # LIVE collision refuses it. The static map cannot know what stands on
                             # a tile right now -- MEASURED at TAU's, where an OPENED diagonal door
                             # leaves a full-tile blocker on a route tile. Small: each reroute
                             # excludes one tile, and a place needing five of them is not a stale
                             # map, it is a target we should stop walking at.
WALK_MAX_STALLS = 4          # total no-progress passes over the WHOLE walk before giving up --
                             # NOT "consecutive": `stalls` is only ever incremented in walk_to's
                             # loop, never reset when a later hop makes real progress, so this is
                             # a cumulative budget across the walk's life (itself bounded by
                             # WALK_MAX_HOPS=60), not a run of back-to-back stalls. Comment fixed
                             # rather than the code during the final review pass: the cumulative
                             # behaviour is a defensible, already-small safety bound on its own
                             # (a walk that stalls 4 times total, however spread out, is still
                             # heading somewhere the route did not expect), and loosening it to
                             # "reset on progress" is a real behaviour change that wants a live
                             # session to verify rather than a documentation fix.
                             # Deliberately small: with a real route, a stall means something
                             # the route does not know about, and repeating it does not help.
SCENE_HALF_WIDTH = 52        # the loaded scene is 104x104 tiles. Used to reject an upper-plane
                             # route that escapes into open sky -- see route_to. Originally added
                             # because the old map had NO DATA above ground outside a building and
                             # absent data read PASSABLE there, so a single missing exterior wall
                             # on plane 1+ let BFS escape into the void. That specific mechanism is
                             # gone since the 2026-08-29 map swap: unfloored plane 1-3 space is now
                             # explicitly marked BLOCKED (MEASURED, Falador plane 1 over a 61x61
                             # box: 5396 passable edges on the old map, 492 on the new), so this is
                             # now belt-and-braces rather than load-bearing. NOT a reason to drop
                             # it: genuinely absent data (no container at all for that key, as
                             # opposed to explicitly-marked open sky) still reads PASSABLE under
                             # the new polarity too -- see worldmap.py's module docstring item 1 --
                             # so a real coverage hole could still produce exactly this leak.

# --- closed doors -------------------------------------------------------------------------
#
# The global collision map (worldmap.py) treats every door as open -- MEASURED 2026-08-28 by
# decoding Shortest Path's own collision-map.zip, of the nine door tiles a human is recorded
# opening across four Mahogany Homes recordings, SEVEN read passable on every edge -- so a
# computed route can run straight through a door that is actually shut. blocked_crossing_on_route
# / first_door_on_route (below) are what catch that: the first step of the route that LIVE
# collision (GET /collision, not the static map) says we cannot actually cross.
DOOR_ROUTE_LOOKAHEAD = 14     # route steps ahead to consider. Enough to cover the way out of a
                              # room without reacting to a door most of a journey away.
DOOR_MAX_OPENS_PER_WALK = 4   # a house door, a room door and a spare. A walk needing more than
                              # this is wedged on something that isn't a door, and should fail
                              # rather than wander a building opening everything.
DOOR_CONFIRM_POLL_S = 0.3     # how often to re-read the doorway while waiting for it to clear
DOOR_OPEN_WAIT_S = 2.0        # a door opens in a tick or two; long enough that the next hop
                              # isn't clicked while the door is still shut. This is the budget for
                              # the OPEN ITSELF only -- the walk to the door is added on top, see
                              # DOOR_WALK_ALLOWANCE_S.
# A door is opened by walking to it and then opening it: the click lands, the player walks, and
# only then does the door swing. So the confirmation budget has to cover the WALK, and a flat 2s
# does not. USER 2026-08-29, on the loop at ROSS's: "we clicked away before waiting for us to walk
# to the door and open it". MEASURED that run: the door was 4 tiles off, i.e. ~2.4s of walking at
# OSRS's 1 tile per 0.6s tick, so the 2s budget expired BEFORE the player even arrived. The
# recorder proves the click was good -- it registered as `Open Door` on id 40178 -- and the two
# clicks that followed resolved to `Walk here`, because by then the player had moved off the
# pixel we re-aimed at. So a working click was reported as a failure and then interrupted by its
# own retry, three times over.
DOOR_WALK_ALLOWANCE_S = 0.7   # per tile between us and the door. 0.6s is one walking tile per
                              # game tick; the extra covers click-to-first-step latency.
DOOR_OPEN_WAIT_MAX_S = 9.0    # ceiling, so a door that genuinely never opens still fails fast.
DOOR_FAILURE_MEMORY_S = 1800.0  # how long a door that refused to open is deprioritized when
                              # another candidate route exists -- see open_door_toward. It only
                              # reorders PREFERENCE; a door that is the sole way in is still
                              # clicked (a shut door is to be OPENED, never routed around).
                              # Was 120s; raised 2026-08-30 (mh_live39): JESS's dead door at
                              # (2622,3293) fails identically on EVERY visit (6 measured), and
                              # visits arrive ~6 min apart, so a 2-minute memory expired between
                              # them and the same ~5s discovery was re-paid each contract. The
                              # cost of over-remembering is only that a once-held door ranks
                              # behind a working alternative -- and when no alternative exists
                              # it is clicked regardless, so a long memory cannot strand a walk.
_door_failures: dict = {}     # (x, y, plane) -> time.time() of the last confirmed open failure

# A door is a WallObject -- ObjectProvider walks those separately from GameObjects for exactly
# this reason (see its own comment: "Doors, gates, and fences are WallObjects"). Matching the
# TYPE as well as the action is what keeps the chests, cupboards and drawers that fill a Mahogany
# Homes house -- all of which also offer "Open" -- out of the candidate set.
# A door is not always a WallObject. MEASURED live 2026-08-29 at TAU's house, Falador: the
# entrance the player needs is `Door id=24050 type=GameObject (3046,3348) actions=['Open']`,
# one tile away, while an unrelated WallObject door with the SAME id sat four tiles north. With
# only WallObject accepted, the door in the way was invisible to every branch of the search and
# the routine simply could not get in. The name + "Open" action filter is what keeps this tight
# -- nearby GameObjects such as Drawers also offer "Open" and are excluded by name.
DOOR_OBJECT_TYPES = ("WallObject", "GameObject")
DOOR_NAME_HINTS = ("door", "gate", "curtain")   # "curtain" taken from Vitalite's own list
DOOR_OPEN_ACTION = "open"

# net.runelite.api.CollisionDataFlag, mirrored from the client. ObjectProvider reports the whole
# entry for an object's TILE as `collisionFlags`; these are the bits meaning "you cannot get past
# this edge". The full derivation (opening one door at Barbara's and re-reading the same doorway
# before/after) is folded into first_door_on_route's own docstring now that DOOR_BLOCKING_FLAGS
# and the line-of-sight constants it was built from are gone -- see that docstring for the
# measurement. The short version relevant to what's still here: a blocked edge is recorded on
# BOTH of its tiles, as opposite directions (DOOR_EDGE_BLOCKS below relies on exactly this).
BLOCK_MOVEMENT_NORTH = 2
BLOCK_MOVEMENT_EAST = 8
BLOCK_MOVEMENT_SOUTH = 32
BLOCK_MOVEMENT_WEST = 128
BLOCK_MOVEMENT_OBJECT = 256
BLOCK_MOVEMENT_FLOOR = 2097152


# (bit, dx, dy) -- the crossing each movement bit blocks, from the tile that carries it.
# MEASURED live 2026-08-28: the block is recorded on BOTH tiles of the edge as opposite
# directions (Barbara's door at (1746,3534) reads MOVEMENT_EAST while (1747,3534) reads
# MOVEMENT_WEST), so either tile alone is enough to know the crossing is shut.
DOOR_EDGE_BLOCKS = (
    (BLOCK_MOVEMENT_NORTH, 0, 1),
    (BLOCK_MOVEMENT_SOUTH, 0, -1),
    (BLOCK_MOVEMENT_EAST, 1, 0),
    (BLOCK_MOVEMENT_WEST, -1, 0),
)


def blocked_crossing_on_route(route, flags, start: int = 0,
                              lookahead: int = DOOR_ROUTE_LOOKAHEAD):
    """The first pair of consecutive route tiles we cannot step between, or None.

    `route` is [(x, y), ...] from worldmap.find_path and `flags` is {(x, y): collision flags}
    from GET /collision. The map routes straight THROUGH doors by design, so a blocked crossing
    on a freshly computed route is very nearly a definition of "a shut door is here".

    _passable is the right test rather than _edge_open, and the difference is load-bearing: it
    also refuses a destination tile carrying TILE_BLOCKING_FLAGS, which is the only way the
    full-tile doors are visible at all. MEASURED at Falador TAU's -- a GameObject door with
    flags 131328 (BLOCK_LINE_OF_SIGHT_FULL | BLOCK_MOVEMENT_OBJECT) and no edge bits whatever.

    An UNKNOWN crossing is skipped, not reported. `flags` only ever covers COLLISION_SCAN_RADIUS
    (10) tiles around the player while `lookahead` looks DOOR_ROUTE_LOOKAHEAD (14) steps ahead, so
    on any route with 11+ remaining steps the crossing sitting at the scan window's own edge was
    _always_ unknown -- and because _passable(None, ...) is False, that unknown crossing used to
    be reported as blocked on every such hop. Two real costs: a wasted get_objects fetch on every
    hop of every walk (first_door_on_route's both-known guard would discard it a moment later
    anyway), and -- worse -- a transient collision outage mid-poll inside _open_door's `recheck`
    could make this return a "still blocked" crossing for a door that had genuinely opened,
    reporting a working open as a failure. Matches _hop_index's own both-known rule for exactly
    the same reason (see its comment) and COLLISION_SCAN_RADIUS's: past the scan window, hop and
    let the stall recovery act instead of guessing.
    """
    end = min(len(route) - 1, start + lookahead)
    for i in range(start, end):
        ax, ay = route[i]
        bx, by = route[i + 1]
        fa, fb = flags.get((ax, ay)), flags.get((bx, by))
        if fa is None or fb is None:
            continue    # cannot tell -- outside the scan window, not necessarily blocked
        if not _passable(fa, fb, bx - ax, by - ay):
            return (ax, ay), (bx, by)
    return None


def first_door_on_route(route, plane: int, objects: list, flags: dict, start: int = 0,
                        lookahead: int = DOOR_ROUTE_LOOKAHEAD):
    """The shut door standing on the first blocked crossing of `route`, or None.

    This is the whole door algorithm now, and it replaced roughly 700 lines of heuristics
    (_door_separates, _within_span, reachable_set/open_frontier_door, open_nearest_shut_door
    and a three-tier escalation). None of those were wrong exactly -- they were all proxies for
    a route we could not compute. Given a real route the question becomes trivial and, unlike
    every one of those tiers, it works identically on any plane.

    The shape is Vitalite's WalkerPath.handlePassThroughObjects: find a door/gate/curtain
    standing on the player's tile or the next step's tile. Two deliberate deviations:

      * Vitalite matches on NAME ONLY and interacts with action index 0. We require an "Open"
        action, because left-clicking an OPEN door CLOSES it -- a shut door and an open one are
        both WallObjects named Door with per-door ids (Falador 24050 shut / 24051 open), so
        only the action separates them, and getting it wrong wedges a walk that was working.
      * Matching the object TYPE too, because chests, cupboards and drawers all offer "Open"
        and fill every Mahogany Homes house.

    An absent flags entry means "cannot tell" and yields no door, never a speculative click.

    Why collision FLAGS are trustworthy here at all -- MEASURED live 2026-08-28 by opening ONE
    door at Barbara's house and re-reading the same doorway before and after (the only comparison
    that actually answers anything: comparing two DIFFERENT doors' own tiles tells you nothing --
    an earlier pass at Lumbridge did exactly that, saw a shut door and an open one both reading
    1026, and wrongly concluded the flags were useless; 1026 was the open door's LEAF edge, not
    its doorway):

        SHUT   Door 7452 at (1746,3534)  flags 4104  = MOVEMENT_EAST|LOS_EAST
               east neighbour (1747,3534)      65664 = MOVEMENT_WEST|LOS_WEST   (reciprocal)
        OPEN   Door 7453 at (1747,3534)  flags 16416 = MOVEMENT_SOUTH|LOS_SOUTH
               (1746,3534) now 0, and (1747,3534) no longer carries WEST -- doorway CLEAR

    Three things follow, and all three matter:
      1. Movement and line of sight are always set together for the SAME edge.
      2. A blocked edge is recorded on BOTH of its tiles, as opposite directions -- it is a
         genuine EDGE, not a property of a tile (DOOR_EDGE_BLOCKS relies on exactly this).
      3. Opening a door CLEARS the doorway edge and the leaf comes to rest blocking a
         PERPENDICULAR edge -- and the object itself moves one tile and changes id (7452 shut ->
         7453 open).

    None of which needs classifying at all, because the useful question is narrower: does one of
    this door's blocked edges lie on the route we're following? A shut door on the route answers
    yes; the same door once open answers no -- which is exactly blocked_crossing_on_route's job.
    """
    crossing = blocked_crossing_on_route(route, flags, start, lookahead)
    if crossing is None:
        return None
    # Require known flags on BOTH tiles to report a door. blocked_crossing_on_route now already
    # skips an unknown crossing rather than returning one, so this is belt and braces rather than
    # the only thing standing between an unknown reading and a click -- kept because an unknown
    # flag must never produce one: left-clicking an OPEN door CLOSES it, turning a working walk
    # into a wedged one, and correctness demands it when acting on missing data could produce a
    # worse outcome than doing nothing.
    a, b = crossing
    if flags.get(a) is None or flags.get(b) is None:
        return None
    tiles = set(crossing)

    for obj in objects or []:
        if obj.get("plane") != plane or obj.get("objectType") not in DOOR_OBJECT_TYPES:
            continue
        if (obj.get("x"), obj.get("y")) not in tiles:
            continue
        name = (obj.get("name") or "").lower()
        if not any(hint in name for hint in DOOR_NAME_HINTS):
            continue
        actions = obj.get("actions")
        if not actions or not any((a or "").lower() == DOOR_OPEN_ACTION for a in actions):
            continue
        return obj
    return None


# Tile-level blockers, as opposed to the EDGE bits above. A tile carrying either of these
# cannot be stood on at all. FLOOR_DECORATION is deliberately absent -- Lumbridge's trapdoor
# reads 262144 and is perfectly walkable.
TILE_BLOCKING_FLAGS = BLOCK_MOVEMENT_OBJECT | BLOCK_MOVEMENT_FLOOR


def save_location():
    """Snapshot the current world tile, or None if position isn't available."""
    player = controller.get_player()
    x, y, plane = player.get("x"), player.get("y"), player.get("plane")
    if x is None:
        return None
    return {"x": x, "y": y, "plane": plane}


def task_location(config: dict, category: str):
    """Wilderness spawn location for a Krystilia slayer task category (see
    SlayerLocations.java on the Java side), as chosen via the Controller
    Settings sidebar's per-target location dropdown when a category has more
    than one known spot -- or None if this category has no location data at
    all. Shape: {"x", "y", "plane", "wildernessLevel", "note", "optionCount"}.
    `category` is the task category name (e.g. "Ice giants"), not a specific
    monster name -- see SlayerMonsters.TASKS on the Java side for that
    mapping if you only have a monster name on hand."""
    return (config.get("taskLocations") or {}).get(category)


def teleport_to_ferox(ctx) -> bool:
    ring = next(
        (i for i in controller.get_inventory() if RING_OF_DUELING_NAME in (i.get("name") or "").lower()),
        None,
    )
    if ring is None:
        debug_log.log("travel", "no ring of dueling in inventory")
        return False

    debug_log.log("travel", "teleporting to Ferox Enclave")
    # check_ui_blockers=False: inventory-item click, same reasoning as every other inventory
    # click in this codebase (see banker.py's coal bag notes) -- CONFIRMED live 2026-08-06 (user
    # session: bot stuck at 8hp repeatedly failing this exact click): without it, the ring's own
    # screen position (always inside the inventory panel) gets flagged as "behind a UI panel",
    # and rotating the camera to work around it does nothing for a flat 2D panel -- it just
    # loops forever, rotating and re-blocking, never actually clicking.
    if not controller.click_entity(ctx.ser, ring, check_ui_blockers=False):
        return False

    controller.human_delay(3.0, 3.6)  # teleport animation + loading transition
    return True


def flee_royal_seed_pod(ctx) -> bool:
    """Emergency wilderness escape: use the royal seed pod. A plain
    left-click resolves through whatever menu option the pod actually uses
    (recalled as "Rub", not re-verified) -- add that option to Controller
    Settings -> Clicking -> Forced Actions if a plain click doesn't already
    resolve to it, same as Ferox Enclave above."""
    pod = next(
        (i for i in controller.get_inventory() if ROYAL_SEED_POD_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pod is None:
        debug_log.log("travel", "no royal seed pod in inventory -- cannot flee!")
        return False

    debug_log.log("travel", "PvP threat detected -- using royal seed pod to escape")
    # check_ui_blockers=False -- same inventory-panel UI-blocker loop as teleport_to_ferox's ring
    # click above; this one's the emergency PvP flee path, so it matters just as much here.
    if not controller.click_entity(ctx.ser, pod, check_ui_blockers=False):
        return False

    controller.human_delay(3.0, 3.6)  # teleport animation + loading transition
    return True


def use_fountain(ctx) -> bool:
    fountain = next(
        (
            o for o in controller.get_objects()
            if FOUNTAIN_NAME.lower() in (o.get("name") or "").lower()
            and o.get("visible", True)
            and 0 <= o.get("distance", 9999) <= FOUNTAIN_MAX_DISTANCE
        ),
        None,
    )
    if fountain is None:
        debug_log.log("travel", f"{FOUNTAIN_NAME} not found nearby")
        return False

    debug_log.log("travel", f"using {fountain.get('name')}")
    if not controller.click_entity(ctx.ser, fountain):
        return False

    controller.human_delay(1.0, 1.5)
    return True


def route_to(target: dict, *, goal_adjacent: bool = False, avoid: set | None = None):
    """A walking route to `target` as [{"x", "y", "plane"}, ...], or None.

    Same plane only, and no transports -- see worldmap.find_path. `goal_adjacent` routes to a
    tile BESIDE the target, which is what you want for anything standing on furniture (a
    hotspot, a ladder, a bank booth): those tiles are solid and nothing can route onto them.
    """
    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is None:
        return None
    if target.get("plane") is not None and target["plane"] != plane:
        debug_log.log("travel", f"target is on plane {target['plane']} and we are on {plane} "
                                f"-- walking cannot change floor")
        return None
    tiles = worldmap.find_path(px, py, plane, target["x"], target["y"],
                               goal_adjacent=goal_adjacent, avoid=avoid)
    if tiles is None:
        return None

    # Upstairs, refuse a route that wanders outside the loaded scene. This guard predates the
    # 2026-08-29 map swap, when the map had NO DATA above ground outside a building and absent
    # data read PASSABLE -- a single missing exterior wall on plane 1+ let BFS escape into open
    # sky and come back down somewhere convenient, a shortcut through nothing. The new asset
    # explicitly marks most unfloored plane 1-3 space BLOCKED (MEASURED, Falador plane 1 over a
    # 61x61 box: 5396 passable edges on the old map, 492 on the new -- see SCENE_HALF_WIDTH's own
    # comment), so that specific leak is far rarer now. The guard stays as belt-and-braces rather
    # than the load-bearing check it used to be: genuinely absent data (as opposed to explicitly
    # marked open sky) still reads PASSABLE under the new polarity too, so a real coverage hole
    # could still produce this exact shortcut. The loaded scene is 104x104 tiles centred near the
    # player, so a legitimate indoor route on an upper floor never approaches this bound.
    if plane > 0:
        for x, y in tiles:
            if max(abs(x - px), abs(y - py)) > SCENE_HALF_WIDTH:
                debug_log.log("travel", f"discarding a plane-{plane} route that leaves the "
                                        f"loaded scene at ({x},{y}) -- a coverage hole or open "
                                        f"sky can still read as walkable, so this is not a "
                                        f"real path")
                return None

    return [{"x": x, "y": y, "plane": plane} for x, y in tiles]


def _collision_flags(x: int, y: int, plane: int, radius: int = COLLISION_SCAN_RADIUS) -> dict:
    """Live collision for a square around (x, y), as {(x, y): flags}. Empty when the client
    cannot answer, which every consumer must read as "cannot tell" rather than "blocked"."""
    return {(t["x"], t["y"]): t.get("flags")
            for t in (controller.get_collision(x, y, plane, radius=radius) or [])}


def _nearest_step_index(route: list, px: int, py: int) -> int:
    """Where the player actually is along the route.

    Keyed on POSITION rather than on a marching counter, which is what the old
    _walk_along_path used. A counter advances whether or not the player moved, so a stalled
    walk kept aiming further down a route it was no longer travelling."""
    best, best_distance = 0, None
    for i, step in enumerate(route):
        distance = max(abs(step["x"] - px), abs(step["y"] - py))
        if best_distance is None or distance < best_distance:
            best, best_distance = i, distance
    return best


def _hop_index(route: list, start: int, px: int, py: int, flags: dict) -> int:
    """How far along the route to aim, bounded by DISTANCE and stopping at any shut crossing.

    Distance, not step count: a cardinal BFS route is longer than the diagonal walk the client
    performs, so counting N steps ahead would produce a hop well short of the measured 16-24
    tile window that WALK_HOP_MIN/MAX_TILES encode.

    Stopping at a shut crossing is Vitalite's rule, and it matters: hop-clicking PAST a shut
    door is exactly what leaves the player wedged against it with the walker none the wiser.
    """
    limit = random.randint(WALK_HOP_MIN_TILES, WALK_HOP_MAX_TILES)
    furthest = start
    for i in range(start, len(route) - 1):
        ax, ay = route[i]["x"], route[i]["y"]
        bx, by = route[i + 1]["x"], route[i + 1]["y"]
        fa = flags.get((ax, ay))
        fb = flags.get((bx, by))
        # BOTH sides must be KNOWN before a crossing may cut a hop short. flags covers only
        # COLLISION_SCAN_RADIUS tiles around the player while hops aim 16-24, and _passable
        # answers False when either flag is None -- so treating unknown as blocked truncated
        # every hop to the scan radius. Past the window we hop and let the stall recovery act.
        if fa is not None and fb is not None and not _passable(fa, fb, bx - ax, by - ay):
            break
        if max(abs(bx - px), abs(by - py)) > limit:
            break
        furthest = i + 1
    return furthest


def _door_on_route(route: list, plane: int, flags: dict, start: int = 0):
    """The shut door on the route ahead of `start`, or None. Fetches /objects only once a
    blocked crossing has actually been found, so a clear walk pays nothing for this."""
    tiles = [(step["x"], step["y"]) for step in route]
    if blocked_crossing_on_route(tiles, flags, start) is None:
        return None
    objects = controller.get_objects(radius=COLLISION_SCAN_RADIUS) or []
    return first_door_on_route(tiles, plane, objects, flags, start)


def _open_door(ctx, door: dict, recheck) -> bool:
    """Click a shut door and CONFIRM it, rather than sleeping and hoping.

    `recheck` is a zero-argument callable returning the door still blocking the way, or None.

    Two things this gets right that a fixed wait did not, both MEASURED live 2026-08-29:

      * The budget includes the WALK. A door is opened by walking to it and then opening it,
        and DOOR_OPEN_WAIT_S alone (2.0s) is less than the ~2.4s of walking to a door four
        tiles off. At ROSS's the FIRST click registered as `Open Door` and was good; the budget
        expired before the player arrived, and the two retries then resolved to `Walk here`
        because the player had moved off the pixel being re-aimed at. A working click was
        reported as a failure and interrupted by its own retry, three times over.
      * Getting THROUGH counts as success (_crossed). ROSS's front door swings shut behind you,
        so re-reading the doorway answers "still shut" about a door that worked -- eight clicks
        across one session, every one registering correctly, every one logged as a failure.
    """
    here = controller.get_player() or {}
    here_x, here_y = here.get("x"), here.get("y")
    dx, dy = door.get("x"), door.get("y")
    if here_x is not None and dx is not None:
        tiles = max(abs(here_x - dx), abs(here_y - dy))
    else:
        tiles = door.get("distance") or 0
    budget = min(DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S, DOOR_OPEN_WAIT_MAX_S)

    debug_log.log("travel", f"opening the {door.get('name')} at ({dx},{dy}) in the way "
                            f"({tiles} tiles off, waiting up to {budget:.1f}s)")
    # RE-AIM THROUGH THE FLIGHT. This was a bare click_entity, so it aimed at the pixel the door
    # occupied when the ROUTE was computed -- while the player was still walking toward it. By the
    # time the cursor arrived the projection had slid and the click landed on whatever was now
    # under it. USER 2026-08-30: "i think the door isnt updated when we try to click it, nearly
    # always lands somewhere else". That also fits the door failures measured the same night --
    # ROSS's front door timing out three times over (2.7s, 4.1s, 6.2s budgets, all spent) while
    # the recorder showed clicks registering: a stale aim produces exactly that, a click that goes
    # somewhere harmless and a doorway that never opens.
    #
    # A door's id changes when it swings (24050 shut -> 24051 open), so the refresh stops
    # resolving the moment it opens -- which is fine and is what we want: by then the click has
    # already been committed, and click_entity keeps its last good aim when a refresh returns
    # nothing.
    controller.click_entity(
        ctx.ser, door,
        refresh_target=controller.object_refresh_target(door.get("x"), door.get("y"),
                                                        door.get("plane", 0), door.get("id")),
        refresh_legs=2,
    )

    # The budget does NOT expire while the player is still WALKING toward the door. The
    # per-tile allowance above assumes the straight-line distance is the walk, and live it is
    # sometimes not: MEASURED three times in one night at JESS's upstairs (2026-08-30, mh_live27
    # 02:26:49 / mh_live30 02:56:41 / mh_live34 03:43:37), the door at (2622,3293) sits 2 tiles
    # from the player -- budget 3.4s -- but the approach detours around full-tile furniture at
    # (2622,3292) that the static map does not know, so the real walk is 4+ tiles. Every click
    # registered correctly (recorder-verified); the budget expired mid-detour, and the
    # re-resolution's next click CANCELLED the queued open it was reporting as failed. A
    # route-length budget cannot fix this -- the map lacks the very furniture causing the
    # detour -- so the confirm extends while the player keeps moving, and only stillness (3
    # polls, ~0.9s) past the budget spends it. DOOR_OPEN_WAIT_MAX_S stays the hard ceiling.
    waited = 0.0
    last_pos = (here_x, here_y)
    still_polls = 0
    while True:
        time.sleep(DOOR_CONFIRM_POLL_S)
        waited += DOOR_CONFIRM_POLL_S
        if recheck() is None or _crossed(door, here_x, here_y):
            return True
        now = controller.get_player() or {}
        pos = (now.get("x"), now.get("y"))
        if pos[0] is None or pos == last_pos:
            still_polls += 1
        else:
            still_polls = 0
            last_pos = pos
        if waited >= DOOR_OPEN_WAIT_MAX_S:
            break
        if waited >= budget and still_polls >= 3:
            break

    debug_log.log("travel", f"the {door.get('name')} at ({dx},{dy}) is still shut after "
                            f"{waited:.1f}s -- another player may be holding it, or the "
                            f"click missed")
    return False


def open_door_toward(ctx, goal: dict) -> bool:
    """Open the shut door standing between the player and `goal`. True if one was clicked.

    Replaces open_doorway_to, open_frontier_door and open_nearest_shut_door -- three tiers that
    existed only because there was no route to ask. Costs nothing when the way is clear.

    goal_adjacent because the tile you need a DOOR for and the tile you can WALK to are usually
    different: a hotspot's own tile is furniture and unroutable, while aiming at the standing
    tile instead hides the doorway (both it and the player sit on the same side of the door).

    But ADJACENCY IS NOT INTERACTABILITY, here as everywhere: goal_adjacent stops at ANY
    cardinally adjacent tile, and a neighbour on OUR side of a wall yields a route that crosses
    nothing -- so no door is ever found. MEASURED live 2026-08-30 at JEFF's (mh_live25): hotspot
    39991 at (3241,3447), player at (3242,3448); the hotspot's north neighbour (3241,3448) is
    adjacent AND one step from the player, but its shared edge with the hotspot is a wall
    (16416) -- open_door_toward answered False 15 ticks straight while the only way in ran
    through the shut inner Door 11775 at (3238,3450), and the run safety-stopped. So route to a
    tile with an OPEN LIVE EDGE to the goal (interaction_tiles -- the same split can_reach rests
    on) whenever live collision can name one, and keep goal_adjacent as the fallback for when it
    cannot ("empty means cannot tell, never refuse to work").
    """
    candidates = []
    for tx, ty in interaction_tiles(goal):
        candidate = route_to({"x": tx, "y": ty, "plane": goal.get("plane")})
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=len)
    fallback = route_to(goal, goal_adjacent=True)
    if fallback is not None:
        candidates.append(fallback)
    if not candidates:
        return False
    player = controller.get_player() or {}
    px, py = player.get("x"), player.get("y")
    if px is None:
        return False
    plane = player.get("plane") or 0
    flags = _collision_flags(px, py, plane)

    # PREFER A ROUTE WHOSE DOOR HAS NOT JUST REFUSED TO OPEN. MEASURED live 2026-08-30, FOUR
    # visits to JESS's upstairs (runs 27/30/34/37): the shortest route's door at (2622,3293)
    # takes an accepted Open click and the game never walks a single step toward it (run 37's
    # motion-aware confirm proved perfect stillness through the whole budget) -- while the
    # next candidate's door at (2617,3294) works, every time. Without this memory the same
    # dead door was rediscovered for ~5s per visit. When EVERY candidate's door is
    # recently-failed, the first is still clicked: A SHUT DOOR IS TO BE OPENED, NEVER ROUTED
    # AROUND (the ROSS regression -- the only way in must never be excluded), and another
    # player may simply have been holding it.
    now = time.time()
    picked = None
    for route in candidates:
        if len(route) < 2:
            continue
        door = _door_on_route(route, plane, flags)
        if door is None:
            continue
        failed_at = _door_failures.get((door.get("x"), door.get("y"), plane), 0.0)
        if now - failed_at >= DOOR_FAILURE_MEMORY_S:
            picked = (route, door)
            break
        if picked is None:
            picked = (route, door)      # remember the first, in case every door is blacklisted
    if picked is None:
        return False
    route, door = picked
    opened = _open_door(ctx, door,
                        lambda: _door_on_route(route, plane, _collision_flags(px, py, plane)))
    if not opened:
        _door_failures[(door.get("x"), door.get("y"), plane)] = time.time()
    return opened


def walk_to(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE,
            goal_adjacent: bool = True) -> bool:
    """Walk to `target`, following a route computed from the global collision map.

    True once within `tolerance` tiles of the target. **False now means something**: there is
    no walking route at all, returned in milliseconds. That replaces the old behaviour where a
    routeless walk fell through to an obstacle-blind greedy walker and hop-clicked a wall until
    WALK_MAX_HOPS x WALK_HOP_TIMEOUT_S ran out -- up to 8 minutes, measured.

    `goal_adjacent` defaults True because most targets here are objects standing on their own
    (unroutable) tile. Pass False when the target really is a tile to stand on.
    """
    avoid: set = set()
    reroutes = 0

    route = route_to(target, goal_adjacent=goal_adjacent)
    if route is None:
        debug_log.log("travel", f"no walking route to ({target['x']},{target['y']}) "
                                f"-- not attempting the walk")
        return False

    doors_opened = 0
    stalls = 0
    last_pos = None

    for _ in range(WALK_MAX_HOPS):
        player = controller.get_player() or {}
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if _within_tolerance(px, py, target["x"], target["y"], tolerance):
            return True

        flags = _collision_flags(px, py, plane)
        index = _nearest_step_index(route, px, py)

        # Doors are resolved BEFORE stepping off, not after a stall (user directive
        # 2026-08-29: "the priority should be determine door situation - then go in"). Waiting
        # for a stall costs a whole hop timeout to notice and is spent wedged against the door
        # in plain view; at NORMAN's it never fired at all, because the walk "arrived" outside.
        if doors_opened < DOOR_MAX_OPENS_PER_WALK:
            door = _door_on_route(route, plane, flags, index)
            if door is not None:
                doors_opened += 1
                _open_door(ctx, door,
                           lambda: _door_on_route(route, plane,
                                                  _collision_flags(px, py, plane), index))
                refreshed = route_to(target, goal_adjacent=goal_adjacent, avoid=avoid)
                if refreshed is None:
                    return False
                route = refreshed
                last_pos = None
                continue

        if last_pos == (px, py):
            stalls += 1
            if stalls > WALK_MAX_STALLS:
                debug_log.log("travel", f"stalled {stalls} times short of "
                                        f"({target['x']},{target['y']}) -- giving up")
                return False
            refreshed = route_to(target, goal_adjacent=goal_adjacent, avoid=avoid)
            if refreshed is None:
                return False
            route = refreshed
            index = _nearest_step_index(route, px, py)
        last_pos = (px, py)

        hop = route[_hop_index(route, index, px, py, flags)]
        if (hop["x"], hop["y"]) == (px, py):
            # The route cannot take us any further and we have not arrived. Before giving up, ask
            # WHY: the static map is not live, so the usual cause is something standing on a route
            # tile that the map cannot know about. MEASURED live 2026-08-29 at TAU's, Falador --
            # a DIAGONAL door, which the game models as a GameObject blocking its WHOLE tile
            # rather than a WallObject on an edge, so OPENING it does not clear a tile, it moves a
            # full-tile blocker onto another one (24051 on (3046,3347), flags 131841). The route
            # ran through that tile, _hop_index refused the very first crossing, and the walk died
            # one tile from the door, 35 tiles short of the bank it then tried to open anyway.
            #
            # So: exclude the tile live collision actually refuses and ask for the route again.
            # The detour is normally free -- at TAU's it is the SAME 51 steps. Deliberately
            # general rather than door-specific: the map cannot tell furniture from a wall
            # anywhere, so "re-route around what live collision refuses" covers the whole class.
            crossing = blocked_crossing_on_route(
                [(step["x"], step["y"]) for step in route], flags, index)
            # A SHUT DOOR IS TO BE OPENED, NEVER ROUTED AROUND. MEASURED live 2026-08-30 at
            # ROSS's, a regression this reroute introduced: its front door at (2610,3316) is the
            # ONLY way in, the walker had already tried three times to open it (the
            # DOOR_MAX_OPENS_PER_WALK budget) and each timed out -- and the reroute then excluded
            # that very tile, leaving no route at all and failing harder than before.
            #
            # The discriminator is the object's own actions, and it is exact: TAU's blocker was
            # an OPEN door's leaf ("Close"), immovable furniture that SHOULD be avoided; ROSS's
            # is a shut door ("Open"), which should not. _door_on_route already answers precisely
            # that question -- it only ever returns a door offering "Open".
            if crossing is not None and _door_on_route(route, plane, flags, index) is not None:
                debug_log.log("travel",
                              f"({crossing[1][0]},{crossing[1][1]}) is a shut door we could not "
                              f"open -- not routing around it")
                crossing = None
            if crossing is not None and reroutes < WALK_MAX_REROUTES:
                _, blocked_tile = crossing
                if blocked_tile not in avoid:
                    avoid.add(blocked_tile)
                    reroutes += 1
                    debug_log.log("travel",
                                  f"({blocked_tile[0]},{blocked_tile[1]}) is blocked in live "
                                  f"collision but not on the map -- re-routing around it")
                    refreshed = route_to(target, goal_adjacent=goal_adjacent, avoid=avoid)
                    if refreshed is not None:
                        route = refreshed
                        last_pos = None
                        continue
            debug_log.log("travel", "route makes no further progress toward the target")
            return False
        if not _hop_to(ctx, hop["x"], hop["y"], hop["plane"]):
            return False

    return False


def _crossed(door: dict, px0: int, py0: int) -> bool:
    """Has the player crossed this door's blocked edge since it was clicked?

    The durable success signal for opening a door is GETTING THROUGH IT, not the doorway reading
    open at the moment we happen to poll. MEASURED live 2026-08-29 at ROSS's front door (40178):
    the recorder shows every single click registering as `Open Door` -- eight of them across the
    session -- while our confirmation kept logging "still shut" and spending the whole budget, up
    to 6.9s a time. These house doors swing shut behind you, so by the time the poll comes round
    the door is legitimately closed again and the old question answers "no" to a door that worked.
    """
    here = controller.get_player() or {}
    px, py = here.get("x"), here.get("y")
    if px is None:
        return False
    dx0, dy0 = door.get("x"), door.get("y")
    flags = door.get("collisionFlags")
    if dx0 is None or flags is None:
        return False
    for bit, ex, ey in DOOR_EDGE_BLOCKS:
        if not flags & bit:
            continue
        if ex:
            lo, hi = sorted((dx0, dx0 + ex))
            if (px0 <= lo and px >= hi) or (px0 >= hi and px <= lo):
                return True
        else:
            lo, hi = sorted((dy0, dy0 + ey))
            if (py0 <= lo and py >= hi) or (py0 >= hi and py <= lo):
                return True
    return False


# Reachability is DECIDED, not discovered by failing. See reachable_set.
REACH_SCAN_RADIUS = 14        # tiles around the player to flood-fill. A Mahogany Homes house
                              # fits well inside this, and one /collision call covers it.
REACH_MAX_TILES = 900         # hard bound on the flood fill, so a bad grid cannot spin


def _edge_open(flags_from, flags_to, dx, dy) -> bool:
    """Is the EDGE between two adjacent tiles open, ignoring whether the far tile can be stood on?

    Separate from _passable because an object's own tile is furniture by definition -- unwalkable
    -- yet we still have to know whether we can reach ACROSS that edge to interact with it. Wall
    or no wall is the whole question; standability is not."""
    if flags_from is None or flags_to is None:
        return False
    if dx == 1:
        return not (flags_from & BLOCK_MOVEMENT_EAST or flags_to & BLOCK_MOVEMENT_WEST)
    if dx == -1:
        return not (flags_from & BLOCK_MOVEMENT_WEST or flags_to & BLOCK_MOVEMENT_EAST)
    if dy == 1:
        return not (flags_from & BLOCK_MOVEMENT_NORTH or flags_to & BLOCK_MOVEMENT_SOUTH)
    return not (flags_from & BLOCK_MOVEMENT_SOUTH or flags_to & BLOCK_MOVEMENT_NORTH)


def _passable(flags_from, flags_to, dx, dy) -> bool:
    """Can we step from a tile with `flags_from` to its neighbour with `flags_to`?

    A blocked edge is recorded on BOTH tiles as opposite directions (MEASURED, see CLAUDE.md), so
    either side is enough to refuse -- checking both is belt and braces against a one-sided grid.
    Unknown flags (None) mean "cannot tell", and are treated as impassable so the caller falls
    back to its old behaviour rather than walking into a wall it cannot see."""
    if flags_from is None or flags_to is None:
        return False
    if flags_to & TILE_BLOCKING_FLAGS:
        return False                      # cannot stand there at all
    if dx == 1:
        return not (flags_from & BLOCK_MOVEMENT_EAST or flags_to & BLOCK_MOVEMENT_WEST)
    if dx == -1:
        return not (flags_from & BLOCK_MOVEMENT_WEST or flags_to & BLOCK_MOVEMENT_EAST)
    if dy == 1:
        return not (flags_from & BLOCK_MOVEMENT_NORTH or flags_to & BLOCK_MOVEMENT_SOUTH)
    return not (flags_from & BLOCK_MOVEMENT_SOUTH or flags_to & BLOCK_MOVEMENT_NORTH)


def reachable_set(px: int, py: int, plane: int, radius: int = REACH_SCAN_RADIUS):
    """Every tile we can WALK to from (px,py) without opening anything, as a set of (x,y).

    A cardinal-only flood fill over the client's own collision grid. Empty when the client cannot
    answer (older build, tile outside the loaded scene), which every caller must read as "don't
    know" and fall back on -- never as "nothing is reachable".

    Why this exists: work_phase used to click a hotspot from wherever it stood, because /objects
    returns the WHOLE scene on our plane and a hotspot behind a wall or a shut door is plainly
    visible. It then discovered it could not reach the thing only after HOTSPOT_UNREACHABLE_CLICKS
    failures. MEASURED over one full ROSS contract 2026-08-29: 15 of 27 hotspot clicks -- 56% --
    achieved nothing, three separate stuck events of five wasted clicks each. USER: "every time we
    have to walk through a door now we have to get stuck on the outside 5 clicks in a row this is
    unacceptable."

    Cardinal-only on purpose: it can only ever UNDER-report reachability (a purely diagonal
    squeeze reads as blocked), so the failure mode is the old behaviour, never a confident walk
    into a wall. And unlike Shortest Path this works on any plane -- upstairs the pathfinder
    returns nothing at all."""
    tiles = controller.get_collision(px, py, plane, radius=radius) or []
    if not tiles:
        return set()

    flags = {(t["x"], t["y"]): t.get("flags") for t in tiles}
    if (px, py) not in flags:
        return set()

    seen = {(px, py)}
    queue = [(px, py)]
    while queue and len(seen) < REACH_MAX_TILES:
        x, y = queue.pop()
        here = flags.get((x, y))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (x + dx, y + dy)
            if nxt in seen or nxt not in flags:
                continue
            if _passable(here, flags.get(nxt), dx, dy):
                seen.add(nxt)
                queue.append(nxt)
    return seen


def interaction_tiles(goal: dict, flags: dict | None = None) -> list:
    """Tiles we could actually ACT on `goal` from, as [(x, y), ...], nearest-first by nothing in
    particular -- the caller picks. Empty when the client cannot tell.

    ADJACENCY IS NOT INTERACTABILITY. worldmap.find_path(goal_adjacent=True) stops at ANY tile
    cardinally adjacent to the goal, and it never has to cross the wall to get there, so the
    route is perfectly valid by the static map's own rules -- while the tile it lands on cannot
    touch the goal at all. MEASURED live 2026-08-30 at BARBARA's: the Bed at (1749,3539) has its
    north and east edges walled (16416 / 65664) and only its south and west open (135432 /
    132354), and the walker parked on the north tile and sat there until the ceiling stopped it.
    The static map cannot tell a wall from furniture; live collision can, which is the same split
    can_reach already rests on.

    Cardinals only. A diagonal can genuinely reach (see can_reach's JEFF measurement) but makes a
    poor WALK target: it is the tile the route should end on that matters here, and a cardinal
    neighbour with an open edge is unambiguous."""
    gx, gy = goal.get("x"), goal.get("y")
    if gx is None:
        return []
    plane = goal.get("plane", 0)
    if flags is None:
        tiles = controller.get_collision(gx, gy, plane, radius=2) or []
        flags = {(t["x"], t["y"]): t.get("flags") for t in tiles}
    if not flags:
        return []      # cannot tell -- the caller must fall back, not refuse to work

    goal_flags = flags.get((gx, gy))
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        near = (gx + dx, gy + dy)
        near_flags = flags.get(near)
        if near_flags is None:
            continue
        # The tile must be STANDABLE, not merely edge-open -- an open edge from another piece
        # of furniture is not a place to walk to. MEASURED live 2026-08-30 at JESS's upstairs
        # (mh_live31 03:08): hotspot 40175's east neighbour (2616,3296) is floor-blocked
        # furniture (2104334) with no west edge bit, so it passed the edge test, became the
        # walk goal, and every route to it was rightly None -- 15 ticks of "no walking route",
        # then a safety stop. Furniture-ringed hotspots like that one are reached DIAGONALLY
        # (the JEFF pattern), which is can_reach's job at click time, not a walk goal's.
        #
        # FLOOR only, deliberately NOT BLOCK_MOVEMENT_OBJECT: Barbara's bed's live-verified
        # interaction tiles ((1749,3538) 135432 / (1748,3539) 132354, both walked to live on
        # 2026-08-30) carry the 256 bit and are pinned in test_travel.py -- filtering OBJECT
        # would regress the exact case interaction_tiles was built for, while every tile in
        # JESS's furniture ring carries FLOOR.
        if near_flags & BLOCK_MOVEMENT_FLOOR:
            continue
        if _edge_open(near_flags, goal_flags, -dx, -dy):
            out.append(near)
    return out


def can_reach(goal: dict, reach: set | None = None, flags: dict | None = None,
              plane: int | None = None, _extend: bool = True) -> bool | None:
    """Can we stand ON or NEXT TO `goal`? None when the client could not tell.

    Adjacent counts because a hotspot's own tile is furniture by definition -- unwalkable -- and
    standing beside it is what the game requires anyway.

    `plane` is the floor the `reach` set was built on. A reachable set is a set of (x, y) with no
    plane in it, so comparing it against a goal on ANOTHER floor is meaningless -- and silently
    wrong, because the two floors share a coordinate space. MEASURED live 2026-08-29 at ROSS's: a
    plane-1 hotspot was judged "reachable" against a plane-0 flood fill purely because its x/y
    happened to land on a walkable ground-floor tile, so work_phase preferred it over the
    ground-floor hotspots and went ladder-hunting. A cross-floor question returns None ("cannot
    tell from here"), which is the honest answer -- reaching another floor is a ladder problem,
    not a reachability one."""
    gx, gy = goal.get("x"), goal.get("y")
    if gx is None:
        return None
    if plane is not None and goal.get("plane") is not None and goal["plane"] != plane:
        return None
    if reach is None:
        player = controller.get_player() or {}
        if player.get("x") is None:
            return None
        reach = reachable_set(player["x"], player["y"], player.get("plane") or 0)
    if not reach:
        return None

    # ADJACENCY IS NOT REACHABILITY -- the EDGE between the tile and the object has to be open.
    # MEASURED live 2026-08-29 outside ROSS's: the player stood at (2617,3319) with the Broken
    # Range at (2617,3318), Chebyshev distance 1, and this returned True -- but that pair is
    # exactly where the exterior wall runs (y=3319 blocks south, y=3318 blocks north). Proximity
    # said yes, the wall said no, and the routine clicked through a wall. USER: "stuck outside
    # ross again... this is absurd".
    #
    # Cardinal neighbours are checked against that edge. Diagonals are accepted without an edge
    # test, which is deliberate: MEASURED at JEFF's, the player interacted successfully from
    # (3241,3447) with the hotspot at (3242,3446) whose every cardinal neighbour is furniture, so
    # a diagonal genuinely does reach. Being permissive there costs at worst a wasted click; being
    # strict there cost a walk-in-place loop.
    if flags is None:
        tiles = controller.get_collision(gx, gy, goal.get("plane", 0), radius=2) or []
        flags = {(t["x"], t["y"]): t.get("flags") for t in tiles}

    goal_flags = flags.get((gx, gy))
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        near = (gx + dx, gy + dy)
        if near not in reach:
            continue
        # Step from `near` TOWARD the goal: the reverse of (dx, dy).
        if _edge_open(flags.get(near), goal_flags, -dx, -dy):
            return True

    # A diagonal reaches only if one of its two orthogonal corner routes is open -- the same rule
    # the game uses. Accepting diagonals unconditionally was WRONG at ROSS's: (2616,3319) and
    # (2618,3319) are diagonal to the hotspot but on the far side of the exterior wall, so the
    # permissive version still answered "reachable" about something behind a wall.
    for dx in (-1, 1):
        for dy in (-1, 1):
            near = (gx + dx, gy + dy)
            if near not in reach:
                continue
            via_x, via_y = (gx + dx, gy), (gx, gy + dy)
            if (_edge_open(flags.get(near), flags.get(via_x), 0, -dy)
                    and _edge_open(flags.get(via_x), goal_flags, -dx, 0)):
                return True
            if (_edge_open(flags.get(near), flags.get(via_y), -dx, 0)
                    and _edge_open(flags.get(via_y), goal_flags, 0, -dy)):
                return True

    # A MULTI-TILE OBJECT IS REPORTED AT ONE ANCHOR TILE, so every neighbour of that anchor can be
    # blocked while the object's OTHER half stands beside a perfectly walkable tile. MEASURED live
    # 2026-08-29 at MARIAH's: Table 40002 reported at (1769,3622) with all eight neighbours
    # blocked -- Chairs north and south, its own blocking east and west -- while (1768,3622), the
    # same object's west half, is cardinally adjacent to walkable (1767,3622). USER: "can reach on
    # west side of that table". Returning False there is a FALSE NEGATIVE about an object the
    # player can walk up to and click, and it cost two silent deadlocks in one session.
    #
    # Scoped to BLOCK_MOVEMENT_OBJECT and nothing else, which is exactly what a second tile of the
    # same object looks like: a WALL blocks with the directional MOVEMENT_* bits and NEVER carries
    # 256 (ROSS's exterior wall reads 65664/16416), so this cannot resurrect the "clicking through
    # a wall" regression the edge test above exists to kill -- see
    # test_can_reach_does_not_extend_through_a_WALL_blocked_neighbour.
    #
    # ONE level deep, never recursive: it answers "is this object touchable", not "flood the
    # furniture". Being permissive here costs at worst a wasted click, which work_phase's own
    # HOTSPOT_UNREACHABLE_CLICKS counter already bounds; being strict cost a frozen routine.
    if _extend:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            part = (gx + dx, gy + dy)
            part_flags = flags.get(part)
            if part_flags is None or not (part_flags & BLOCK_MOVEMENT_OBJECT):
                continue
            if can_reach({"x": part[0], "y": part[1], "plane": goal.get("plane")},
                         reach, flags, plane, _extend=False) is True:
                return True
    return False


def _click_walk_point(ctx, point: dict, shift: bool) -> None:
    """Click a walk destination, holding SHIFT for a viewport click so it force-walks.

    Shift+click is OSRS's own "Walk here" shortcut and looks like any other single click, so
    there is no reason not to use it for every ground-tile walk rather than only ones near a
    known-risky object."""
    if not shift:
        # check_ui_blockers=False (2026-09-04): GameStateProvider has listed the minimap rects
        # as UI blockers since the 2026-09-02 rebuild (a force-walk projected INSIDE the minimap
        # circle clicked nothing for two minutes). A minimap hop is aimed at that rect on
        # purpose, and with the check on click_entity refuses it and nudges the camera instead
        # -- the walk never starts. The viewport click below keeps the check: it is what catches
        # the chatbox scrollbar and the inventory panel.
        controller.click_entity(ctx.ser, point, offset=(0, 0), check_ui_blockers=False)
        return
    controller.key_down(ctx.ser, "SHIFT")
    try:
        controller.click_entity(ctx.ser, point, offset=(0, 0))
    finally:
        controller.key_up(ctx.ser, "SHIFT")


def _hop_to(ctx, x: int, y: int, plane: int, inst: bool = False, prefer_viewport: bool = False) -> bool:
    """Click the hop point and wait for arrival (or timeout) before
    returning. False only if the point isn't projectable onto the minimap
    at all; a timed-out wait still returns True so the caller just
    recomputes the next hop from wherever the player actually ended up.

    MINIMAP for distance, viewport only for short precise hops (user directive
    2026-08-29: "use minimap for walking long distances instead of viewport
    walk, only small distance accurate clicks should be viewport").

    This inverts the original preference, which reached for the viewport
    whenever the tile was rendered at all and used the minimap only beyond
    render distance. That was chosen in 2026-08-06 for precision -- a viewport
    click aims at the real projected tile, while the minimap is coarse -- but
    precision is only wanted at the END of a walk, and exact-tile work does not
    come through here at all (walk_to_exact_tile handles that). Meanwhile
    WALK_HOP_MIN/MAX_TILES are 16-24, so in practice nearly every hop of a long
    route was a viewport click into a scene full of interactive scenery.
    CONFIRMED live 2026-08-29: hops resolved as "Chop down" and "Cook" instead
    of walking, which strands the player somewhere it did not choose and makes
    the next pass walk back -- visible as clicking back and forth. The minimap
    has nothing drawn on it to hijack a click."""
    # Aim at the requested point first, then at progressively closer ones. The minimap's reach is
    # a user setting we don't control (zooming it in shrinks it), and before this an aim point
    # outside it abandoned the entire walk. Pulling in costs one extra poll and degrades a
    # too-ambitious hop into a shorter one instead of a failed trip -- which is what makes the
    # measured WALK_HOP_MAX_TILES above safe to run near its real limit rather than far under it.
    origin = controller.get_player()
    # inst=True (2026-09-05, instance_nav): x/y and the origin are INSTANCE-SPACE, and every
    # point lookup carries the flag through to the bridge. Nothing else about the hop changes.
    if inst:
        ox, oy = origin.get("instX"), origin.get("instY")
    else:
        ox, oy = origin.get("x"), origin.get("y")
    point = None
    on_ground = False        # viewport click (needs SHIFT) vs minimap click (does not)
    aim_x, aim_y = x, y

    # prefer_viewport (USER 2026-09-06, Corrupted Gauntlet: "doing minimap hops between rooms is
    # also not needed since we can see it in the viewport"): a rendered, un-occluded tile is
    # clicked in the viewport at ANY hop length, pulled in by the same fractions when the far
    # point is off screen or under a UI panel; the minimap stays the fallback below. The hijack
    # risk that made the minimap the default for the overworld is covered by the SHIFT
    # force-walk every viewport hop already carries.
    if prefer_viewport and ox is not None:
        for fraction in HOP_PULL_IN_FRACTIONS:
            ax = int(round(ox + (x - ox) * fraction))
            ay = int(round(oy + (y - oy) * fraction))
            if (ax, ay) == (ox, oy):
                break
            ground_point = controller.get_ground_walk_point(ax, ay, plane, inst=inst)
            if ground_point.get("onScreen") and not controller.is_point_blocked_by_ui(
                    ground_point.get("screenX", -1), ground_point.get("screenY", -1)):
                point = ground_point
                on_ground = True
                aim_x, aim_y = ax, ay
                break

    for fraction in (() if point is not None else HOP_PULL_IN_FRACTIONS):
        if fraction < 1.0:
            if ox is None:
                break
            aim_x = int(round(ox + (x - ox) * fraction))
            aim_y = int(round(oy + (y - oy) * fraction))
            if (aim_x, aim_y) == (ox, oy):
                break     # pulled all the way back onto ourselves; nothing left to try

        short = (ox is not None
                 and max(abs(aim_x - ox), abs(aim_y - oy)) <= HOP_VIEWPORT_MAX_TILES)
        if short:
            ground_point = controller.get_ground_walk_point(aim_x, aim_y, plane, inst=inst)
            if ground_point.get("onScreen"):
                point = ground_point
                on_ground = True
                break

        minimap_point = controller.get_minimap_walk_point(aim_x, aim_y, plane, inst=inst)
        if minimap_point.get("onMinimap"):
            point = minimap_point
            break

        # Off the minimap (its reach is a user setting -- zooming in shrinks it) and too far for
        # the short-hop rule: a rendered tile still beats giving up on the walk.
        if not short:
            ground_point = controller.get_ground_walk_point(aim_x, aim_y, plane, inst=inst)
            if ground_point.get("onScreen"):
                point = ground_point
                on_ground = True
                break

    if point is None:
        debug_log.log("travel", "hop point isn't on screen or the minimap at any distance, giving up")
        return False

    # SHIFT for a VIEWPORT click, which is OSRS's own force-walk shortcut. CLAUDE.md has carried
    # this rule since golem_crafting hit it, but only walk_to_exact_tile was obeying it -- and
    # this function PREFERS the viewport whenever the tile is rendered, so most hops took the
    # unshifted path. CONFIRMED live 2026-08-29: hops landed as "Chop down" ("You need an axe")
    # and "Cook" ("You haven't got anything to cook") instead of walking. A hijacked hop leaves
    # the player somewhere it did not intend, the next pass recomputes and walks back, and that
    # is the visible back-and-forth clicking. A minimap click has no world objects on it to be
    # hijacked by, and SHIFT there means nothing, so it is left alone.
    _click_walk_point(ctx, point, shift=on_ground)

    # Long hops release EARLY -- with HOP_NEXT_CLICK_LEAD_TILES still to run -- so the next
    # hop's aim and mouse flight overlap the remaining tiles instead of starting after the
    # player has already arrived and stopped (the measured 0.6-1.8s freeze at every hop
    # boundary, see the constant's comment). The game replaces a queued destination on the next
    # click, so clicking again mid-run is exactly what a human does. A hop no longer than the
    # lead keeps the old exact-ish wait: releasing it instantly would spam clicks.
    hop_dist = None if ox is None else max(abs(aim_x - ox), abs(aim_y - oy))
    if hop_dist is not None and hop_dist > HOP_NEXT_CLICK_LEAD_TILES:
        release = HOP_NEXT_CLICK_LEAD_TILES
    else:
        release = WALK_ARRIVAL_TOLERANCE

    waited = 0.0
    while waited < WALK_HOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        if inst:
            px, py = player.get("instX"), player.get("instY")
        else:
            px, py = player.get("x"), player.get("y")
        # Against the point actually clicked, not the one asked for -- otherwise a pulled-in hop
        # would always wait out the full timeout instead of returning on arrival.
        if px is not None and max(abs(px - aim_x), abs(py - aim_y)) <= release:
            break

    return True


def _within_tolerance(x1: float, y1: float, x2: float, y2: float, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= tolerance


EXACT_TILE_POLL_INTERVAL_S = 0.6   # one game tick -- the soonest the player's own tile can change
EXACT_TILE_TIMEOUT_S = 8.0         # generous for the short, in-render-range hops this is for


def walk_to_exact_tile(ctx, x: int, y: int, plane: int, wait_for_arrival: bool = True) -> bool:
    """Walk to the EXACT world tile (x, y, plane) and wait until standing exactly on it.

    Deliberately not walk_to/_hop_to above: those treat landing within WALK_ARRIVAL_TOLERANCE
    (3 tiles) as arrival, which is useless whenever a tile's identity is the whole point --
    golem_crafting.py's golem sides are 1-2 tiles apart (a 3-tile-tolerant arrival can report
    success while standing on the WRONG side), and chinchompa_hunting.py has to be standing on
    the precise tile it means to lay a box trap on, since "Lay" always targets the player's own
    tile and nothing else.

    Returns False only if the tile can't be projected onto the screen at all (out of render
    range); a timed-out wait still returns True, since callers re-check position fresh afterward
    either way.

    EXTRACTED 2026-08-20 from routines/golem_crafting.py, unchanged, once a second routine needed
    the identical behaviour -- see that file's own walk_to_exact_tile (now a delegating wrapper)
    for the full provenance of each piece below. In short:
      - Early-out with no click at all when already standing there (2026-08-20 review finding E):
        the common case for both callers is a walk that turns out to be a no-op, and paying for a
        full human-paced flight plus a poll to discover that is pure waste.
      - Shift held around the click (2026-08-09 user report, CONFIRMED live): a plain click at a
        ground point near an interactive object can resolve to that OBJECT's action instead of
        walking -- it cleared the Goat Pit's lining mid walk-back. Shift+click is OSRS's own
        force-walk shortcut, looks like any other click, so it's used unconditionally. This is
        the codebase rule for every walk-to-a-tile click (see CLAUDE.md), not a per-caller opt-in.
      - refresh_target (2026-08-10 user report): the tile doesn't move but its screen projection
        does (camera auto-follow/momentum) during move_to_human's flight time, so the final
        approach re-aims at the tile's live position instead of a pre-click snapshot.

    `wait_for_arrival=False` (added 2026-08-23, user directive) fires the walk and returns as soon
    as the click is out, so the caller can issue its NEXT click while the player is still moving.
    That is what a human does: MEASURED across a full hand-played golem carve
    (recordings/2026-08-23_194302_golem-carving), the human clicked the target object 0.6-1.0s
    after each walk click, from a tile they had not reached yet, 4 times out of 4 -- and the side
    that got carved was the tile they were walking TO, not the one they clicked from (confirmed by
    resolving GOLEM_2_{NORTH,EAST,SOUTH,WEST}_PROGRESS against runelite-api-1.12.36.jar). The game
    queues the interaction and performs it on arrival, so blocking here buys no correctness -- it
    is pure added latency, and worse, the block is what turns an over-tight confirmation timeout
    into an action-destroying re-walk rather than a harmless extra wait. Pair it with a
    `*_refresh_target` factory plus `refresh_legs` on the following click so the aim is corrected
    mid-flight against un-cached position reads. See CLAUDE.md's "Sub-tick data and clicking while
    moving" for the standing preference this implements.

    Default is True, which reproduces every existing caller's behaviour exactly.
    """
    player = controller.get_player()
    if player.get("x") == x and player.get("y") == y and player.get("plane", 0) == plane:
        return True
    point = controller.get_ground_walk_point(x, y, plane)
    if not point.get("onScreen"):
        return False
    refresh = controller.ground_tile_refresh_target(x, y, plane)
    controller.key_down(ctx.ser, "SHIFT")
    controller.click_entity(ctx.ser, point, offset=(0, 0), refresh_target=refresh)
    controller.key_up(ctx.ser, "SHIFT")
    if not wait_for_arrival:
        return True
    waited = 0.0
    while waited < EXACT_TILE_TIMEOUT_S:
        time.sleep(EXACT_TILE_POLL_INTERVAL_S)
        waited += EXACT_TILE_POLL_INTERVAL_S
        player = controller.get_player()
        if player.get("x") == x and player.get("y") == y:
            return True
    return True


def maybe_approach_before_click(ctx, entity: dict, already_engaged: bool = False,
                                 chance: float = controller.APPROACH_TILE_CHANCE,
                                 via_minimap: bool = True) -> bool:
    """With `chance` probability, click a ground tile partway between the
    player and `entity` instead of clicking the entity itself. A real player
    often closes some of the distance to something a few tiles off manually
    rather than always relying on the game's own click-to-walk-then-interact
    -- doing the latter 100% of the time is a tell.

    via_minimap picks how that intermediate tile gets clicked: True (default)
    uses the same minimap-hop trick as _hop_to, appropriate for a target far
    enough that the approach tile may not even be rendered in the viewport.
    Pass False for something close and on-screen (e.g. closing the last few
    tiles to a monster mid-approach) -- a real player closing that kind of
    short distance clicks the ground in front of them, not the minimap.
    False here silently declines (same as an off-minimap point would) if the
    tile isn't actually on-screen, so the caller falls back to clicking the
    entity directly.

    This is only for *approaching* something, never appropriate once you're
    already committed to it -- pass already_engaged=True (e.g. from
    get_combat().inCombat) whenever the caller might already be mid-fight
    with this exact entity: isIdle can read True for a single tick between
    attack animations while inCombat stays True for the whole fight, and
    without this a routine re-evaluating on that idle tick could wander off
    to some tile mid-fight instead of just continuing the attack.

    Returns True if it clicked an approach tile instead (the caller should
    skip its own click this tick -- get_combat().isIdle will read False
    while walking, so the routine's usual "already busy" check naturally
    holds off the real click until a later tick, once the player has closed
    the distance). Returns False if already_engaged, if it rolled against
    approaching, or if entity/player position data wasn't available --
    caller should click the entity as normal.
    """
    if already_engaged:
        return False

    x, y, plane = entity.get("x"), entity.get("y"), entity.get("plane")
    dist = entity.get("distance")
    if x is None or y is None or plane is None or dist is None:
        return False
    if dist < controller.APPROACH_TILE_MIN_DISTANCE:
        return False
    if random.random() > chance:
        return False

    player = controller.get_player()
    px, py = player.get("x"), player.get("y")
    if px is None:
        return False

    frac = random.uniform(controller.APPROACH_TILE_FRACTION_MIN, controller.APPROACH_TILE_FRACTION_MAX)
    hop_x = int(round(px + (x - px) * frac))
    hop_y = int(round(py + (y - py) * frac))

    if via_minimap:
        point = controller.get_minimap_walk_point(hop_x, hop_y, plane)
        if not point.get("onMinimap"):
            return False
    else:
        point = controller.get_ground_walk_point(hop_x, hop_y, plane)
        if not point.get("onScreen"):
            return False

    debug_log.log("travel", f"closing some distance to {entity.get('name')} before the final click")
    # Same force-walk reasoning as _hop_to, and this one aims deliberately at a tile part-way to
    # an entity -- i.e. near something interactive by construction.
    _click_walk_point(ctx, point, shift=not via_minimap)
    return True
