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
# walk_to routes through the (separately installed) Shortest Path hub
# plugin: it posts a PluginMessage asking it to compute an obstacle-aware
# route, polls for the result, then walks that route by repeatedly clicking
# a waypoint some tiles ahead via the minimap and letting the game's own
# pathfinder handle each short hop -- the look-ahead distance is randomized
# per hop (never below WALK_HOP_MIN_TILES) rather than a fixed number, so
# consecutive walks don't look identical. If Shortest Path isn't installed,
# didn't finish in time, or the route turned out to be unusable, this falls
# back to a straight-line greedy walk toward the target (same hop-and-click
# trick, no obstacle awareness -- fine over the open terrain around Ferox
# Enclave, not a real substitute for the computed route).
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
WALK_ARRIVAL_TOLERANCE = 3   # tiles; close enough to call a hop, or the whole trip, arrived
WALK_HOP_TIMEOUT_S = 8.0     # give up waiting on a single hop after this long
WALK_MAX_HOPS = 60           # hard cap so a bad target can't loop forever

COLLISION_SCAN_RADIUS = 10   # tiles of live collision fetched per hop. Big enough to see the
                             # doors along one hop, small enough that a 21x21 response per hop
                             # stays cheap. Beyond it we simply hop and let the stall catch it.
WALK_MAX_STALLS = 4          # consecutive no-progress passes before giving up on the walk.
                             # Deliberately small: with a real route, a stall means something
                             # the route does not know about, and repeating it does not help.
SCENE_HALF_WIDTH = 52        # the loaded scene is 104x104 tiles. Used ONLY to reject an
                             # upper-plane route that escapes into the open sky the map treats
                             # as walkable -- see route_to and the design doc's risk 1.

# --- closed doors -------------------------------------------------------------------------
#
# Shortest Path CANNOT route us through a shut door, and it does not know that it can't.
# MEASURED 2026-08-28 by decoding the plugin's own collision-map.zip: of the nine door tiles a
# human is recorded opening across the four Mahogany Homes recordings, SEVEN are marked passable
# on every edge -- the collision dump was generated with doors treated as open. Falador's
# (3037,3347) was re-checked live, with the player standing beside that very door, and reads
# passable too. So the pathfinder emits a route straight through a closed door, and because none
# of those door ids appear in transports.tsv either, the route carries no transport step for
# _use_transport to click. The walker hop-clicks past the door, the player wedges against it, and
# _hop_to returns True on its timeout regardless -- so the loop recomputes the identical hop and
# burns WALK_MAX_HOPS x WALK_HOP_TIMEOUT_S (up to 8 minutes) before walk_to finally returns False.
#
# The recovery below is deliberately STALL-TRIGGERED rather than proactive: it only ever runs on a
# pass where the player failed to get closer, which today is a walk that fails outright. Nothing
# that currently works can regress -- worst case it costs one extra scan on a walk already lost.
# tiles from the player, for the ROUTELESS fallback only. RAISED 4 -> 8 on 2026-08-29: MEASURED
# outside NORMAN's house in Falador, the two shut doors stood 6 and 7 tiles away, so a radius of 4
# found NO candidates at all and the recovery returned None -- the real reason nothing was ever
# opened there. The old tightness dated from when selection was a distance GUESS and a wide net
# was dangerous; a candidate must now prove, from the client's own collision entry, that one of
# its blocked edges actually crosses our path, so the radius can span a house without opening
# anything unrelated. Widen further only on the same kind of evidence.
DOOR_SEARCH_RADIUS = 8
# How far outside the straight-line box between us and the goal a doorway may sit and still count
# as the way through -- the walker's path is not a perfect line.
DOOR_SPAN_MARGIN = 1
DOOR_ROUTE_SEARCH_RADIUS = 10 # ...but WITH a route, the route itself is the correctness check, so
                              # the search can reach the door the route actually goes through.
                              # LIVE 2026-08-28: the blocking door sat 6 tiles away, invisible to
                              # the 4-tile radius.
DOOR_ROUTE_LOOKAHEAD = 14     # route steps ahead to consider. Enough to cover the way out of a
                              # room without reacting to a door most of a journey away.
DOOR_ROUTE_ADJACENCY = 1      # a door is "on" a step if it is on it or beside it -- a WallObject
                              # is anchored to one side of the tile boundary it blocks.
DOOR_MAX_OPENS_PER_WALK = 4   # a house door, a room door and a spare. A walk needing more than
                              # this is wedged on something that isn't a door, and should fail
                              # rather than wander a building opening everything.
DOOR_PROGRESS_EPSILON = 1.0   # tiles of headway a hop must make to count as progress. Not 0:
                              # arriving a tile off the aim point is ordinary, not a stall.
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
# this edge" and "you cannot see past this edge".
#
# MEASURED live 2026-08-28 by opening ONE door at Barbara's house and re-reading the same doorway
# before and after -- the only comparison that actually answers anything here:
#
#   SHUT   Door 7452 at (1746,3534)  flags 4104  = MOVEMENT_EAST|LOS_EAST
#          east neighbour (1747,3534)      65664 = MOVEMENT_WEST|LOS_WEST   (reciprocal)
#   OPEN   Door 7453 at (1747,3534)  flags 16416 = MOVEMENT_SOUTH|LOS_SOUTH
#          (1746,3534) now 0, and (1747,3534) no longer carries WEST -- the doorway is CLEAR
#
# Three things follow, and all three matter:
#  1. Movement and line of sight are always set together for the SAME edge.
#  2. A blocked edge is recorded on BOTH of its tiles, as opposite directions -- it is a genuine
#     EDGE, not a property of a tile.
#  3. Opening a door CLEARS the doorway edge and the leaf comes to rest blocking a PERPENDICULAR
#     edge -- and the object itself moves one tile and changes id (7452 shut -> 7453 open).
#
# So the flags do track door state, but only if you compare the same DOORWAY; comparing two
# different doors' own tiles tells you nothing (an earlier pass at Lumbridge did exactly that,
# saw a shut door and an open one both reading 1026, and wrongly concluded the flags were useless
# -- 1026 was the open door's LEAF edge, not its doorway).
#
# None of which needs classifying at all, because the useful question is narrower: does one of
# this door's blocked edges lie between us and where we are going? See _door_separates. A shut
# door across our path answers yes; the same door once open answers no, for free.
BLOCK_MOVEMENT_NORTH = 2
BLOCK_MOVEMENT_EAST = 8
BLOCK_MOVEMENT_SOUTH = 32
BLOCK_MOVEMENT_WEST = 128
BLOCK_MOVEMENT_OBJECT = 256
BLOCK_MOVEMENT_FLOOR_DECORATION = 262144
BLOCK_MOVEMENT_FLOOR = 2097152
BLOCK_MOVEMENT_FULL = 2359552
BLOCK_LINE_OF_SIGHT_NORTH = 1024
BLOCK_LINE_OF_SIGHT_EAST = 4096
BLOCK_LINE_OF_SIGHT_SOUTH = 16384
BLOCK_LINE_OF_SIGHT_WEST = 65536
BLOCK_LINE_OF_SIGHT_FULL = 131072
# Deliberately NOT including BLOCK_MOVEMENT_FULL: it is a COMPOSITE constant (2359552 =
# BLOCK_MOVEMENT_FLOOR 2097152 | BLOCK_MOVEMENT_FLOOR_DECORATION 262144 | BLOCK_MOVEMENT_OBJECT
# 256), so ORing it in makes a bare floor decoration read as an obstruction. Caught live the same
# day on Lumbridge's Trapdoor, flags 262144, which is a hole in the floor and blocks no edge at all.
DOOR_BLOCKING_FLAGS = (
    BLOCK_MOVEMENT_NORTH | BLOCK_MOVEMENT_EAST | BLOCK_MOVEMENT_SOUTH | BLOCK_MOVEMENT_WEST
    | BLOCK_MOVEMENT_OBJECT
    | BLOCK_LINE_OF_SIGHT_NORTH | BLOCK_LINE_OF_SIGHT_EAST | BLOCK_LINE_OF_SIGHT_SOUTH
    | BLOCK_LINE_OF_SIGHT_WEST | BLOCK_LINE_OF_SIGHT_FULL
)


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
    """
    end = min(len(route) - 1, start + lookahead)
    for i in range(start, end):
        ax, ay = route[i]
        bx, by = route[i + 1]
        if not _passable(flags.get((ax, ay)), flags.get((bx, by)), bx - ax, by - ay):
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
    """
    crossing = blocked_crossing_on_route(route, flags, start, lookahead)
    if crossing is None:
        return None
    # Require known flags on BOTH tiles to report a door. An unknown flag means "cannot tell",
    # which must never produce a click -- left-clicking an OPEN door CLOSES it, turning a
    # working walk into a wedged one. This is stricter than the edge-blocking rule (either side
    # proves a shut door when both are known) but correctness demands it when acting on missing
    # data could produce a worse outcome than doing nothing.
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


def _door_separates(door: dict, px: int, py: int, gx: int, gy: int):
    """Does one of this door's blocked edges lie BETWEEN (px,py) and (gx,gy)?

    This is the question the two broken heuristics were both groping at. A door's blocked edge is
    a crossing between two specific tiles; the door is in our way only if we have to cross it.
    Direction-agnostic on purpose -- the edge is symmetric, so the same doorway matches whether we
    approach from inside or out.

    None when the client reports no flags ("cannot tell", never "no")."""
    flags = door.get("collisionFlags")
    if flags is None:
        return None
    dx0, dy0 = door.get("x"), door.get("y")

    # A full-TILE blocker (BLOCK_MOVEMENT_OBJECT, e.g. TAU's GameObject door at flags 131328)
    # carries no edge bits at all, so the edge loop below can never match it. What separates us
    # from the goal is its whole tile lying on the way -- which is literally what the route says:
    # Shortest Path routed (3045,3349) -> (3046,3348) -> (3046,3347) -> (3046,3346) straight
    # through that tile, because its collision map believes doors are open.
    if flags & TILE_BLOCKING_FLAGS:
        # Not our own tile, and not the goal's. CONFIRMED live 2026-08-29 in JESS's house: this
        # branch claimed a door reported on the PLAYER'S OWN TILE (2617,3294) and looped -- click,
        # "still shut after 2s", click. You cannot be blocked by the tile you are standing on, and
        # the goal tile being furniture is the normal case (_routable_goal already retargets it),
        # not a door in the way. `collisionFlags` is the aggregate for an object's TILE rather
        # than for that object alone, so a wall door sharing a tile with anything else reads
        # BLOCK_MOVEMENT_OBJECT and reaches here in the first place.
        if (dx0, dy0) in ((px, py), (gx, gy)):
            return False
        return _within_span(dx0, px, gx) and _within_span(dy0, py, gy)

    for bit, dx, dy in DOOR_EDGE_BLOCKS:
        if not flags & bit:
            continue
        if dx:
            lo, hi = sorted((dx0, dx0 + dx))
            crosses = (px <= lo and gx >= hi) or (px >= hi and gx <= lo)
            along = _within_span(dy0, py, gy)
        else:
            lo, hi = sorted((dy0, dy0 + dy))
            crosses = (py <= lo and gy >= hi) or (py >= hi and gy <= lo)
            along = _within_span(dx0, px, gx)
        # BOTH halves are required. A blocked edge spans exactly ONE tile, so sitting on the right
        # axis proves nothing by itself -- MEASURED live 2026-08-29 outside NORMAN's house, where
        # Door 24057 SEVEN tiles west of us reported "separates" purely because its y-crossing sat
        # between our y and the goal's. It belonged to a different building.
        if crosses and along:
            return True
    return False


def _within_span(value: int, a: int, b: int) -> bool:
    """Is `value` within a..b, allowing DOOR_SPAN_MARGIN either side?

    The margin exists because the walker does not travel a perfect straight line between the two
    points, so the doorway it actually goes through can sit a tile outside the box they span."""
    lo, hi = sorted((a, b))
    return lo - DOOR_SPAN_MARGIN <= value <= hi + DOOR_SPAN_MARGIN


# Tile-level blockers, as opposed to the EDGE bits above. A tile carrying either of these
# cannot be stood on at all. FLOOR_DECORATION is deliberately absent -- Lumbridge's trapdoor
# reads 262144 and is perfectly walkable.
TILE_BLOCKING_FLAGS = BLOCK_MOVEMENT_OBJECT | BLOCK_MOVEMENT_FLOOR
GOAL_NEIGHBOUR_RADIUS = 2      # how far around an unwalkable goal to look for a stand-in


def _tile_blocked(flags):
    """Can this tile be stood on? None when the client did not report flags."""
    if flags is None:
        return None
    return bool(flags & TILE_BLOCKING_FLAGS)


def _routable_goal(goal: dict) -> dict:
    """`goal`, or the nearest walkable tile to it when `goal` itself cannot be stood on.

    MEASURED live 2026-08-29 at TAU's house: standingLocation (3047,3345) carries
    BLOCK_MOVEMENT_OBJECT -- furniture stands on the home's own standing tile -- so Shortest Path
    returns 0 steps to it, walk_to falls through to the obstacle-blind greedy walker and wedges
    against the wall. One tile north, (3047,3346), routes in 4 steps. The same is true of every
    hotspot tile, which is furniture by definition.

    Prefers the candidate nearest the goal, tie-broken by nearest to the player, so the stand-in
    is somewhere we would have walked through anyway. Returns `goal` untouched whenever the
    client reports nothing (older build, tile outside the loaded scene) -- never guess."""
    gx, gy, plane = goal.get("x"), goal.get("y"), goal.get("plane", 0)
    if gx is None:
        return goal

    tiles = controller.get_collision(gx, gy, plane, radius=GOAL_NEIGHBOUR_RADIUS) or []
    if not tiles:
        return goal

    by_tile = {(t["x"], t["y"]): t.get("flags") for t in tiles}
    if _tile_blocked(by_tile.get((gx, gy))) is not True:
        return goal        # walkable, or unknown -- either way leave it alone

    player = controller.get_player()
    px, py = player.get("x", gx), player.get("y", gy)
    candidates = [
        (tx, ty) for (tx, ty), flags in by_tile.items()
        if _tile_blocked(flags) is False and (tx, ty) != (gx, gy)
    ]
    if not candidates:
        return goal

    tx, ty = min(candidates, key=lambda t: (max(abs(t[0] - gx), abs(t[1] - gy)),
                                            max(abs(t[0] - px), abs(t[1] - py))))
    debug_log.log("travel", f"({gx},{gy}) can't be stood on -- aiming at ({tx},{ty}) instead")
    return {"x": tx, "y": ty, "plane": plane}


def _door_blocks(obj: dict):
    """Does this object's own tile obstruct movement or sight right now?

    NOT an open/shut test -- see DOOR_BLOCKING_FLAGS above for the measurement that killed that
    idea. Callers must already have established the door is shut from its `actions`; this only
    separates a shut door that is genuinely in the way from one merely standing nearby.

    None means "cannot tell", not "no": an older client build has no `collisionFlags` field, and
    every caller must keep working against one (same contract as `actions`)."""
    flags = obj.get("collisionFlags")
    if flags is None:
        return None
    return bool(flags & DOOR_BLOCKING_FLAGS)

PATH_REQUEST_TIMEOUT_S = 6.0   # give up waiting for Shortest Path to finish computing
PATH_POLL_INTERVAL_S = 0.3

# Below this many tiles (Chebyshev, same plane only) walk_to doesn't bother asking Shortest Path
# at all and goes straight to _walk_greedy: the request plus up to PATH_REQUEST_TIMEOUT_S of
# polling costs more than the walk itself over a short hop. Deliberately NOT a large number --
# _walk_greedy is obstacle-blind, so every tile of gate raises the odds of wedging against a wall
# it can't route around. 25 covers the "already basically there" case (a safespot return, a
# re-approach after a bank trip) without gambling on open terrain.
SHORT_HOP_TILES = 25

# Every ShortestPathConfig option the plugin recognises, so a typo in an override can be caught
# here instead of being silently dropped (the plugin ignores unknown keys, which would leave the
# baseline quietly in force while the caller believed it had opted in). Taken from
# ShortestPathConfig's own method names in the installed jar.
SHORTEST_PATH_CONFIG_KEYS = frozenset({
    "avoidWilderness", "useAgilityShortcuts", "useGrappleShortcuts", "useBoats", "useCanoes",
    "useCharterShips", "useShips", "useFairyRings", "useGnomeGliders", "useHotAirBalloons",
    "useMagicCarpets", "useMagicMushtrees", "useMinecarts", "useQuetzals", "useSpiritTrees",
    "useTeleportationItems", "useTeleportationLevers", "useTeleportationPortals",
    "useTeleportationSpells", "useTeleportationSpellsHome", "useTeleportationMinigames",
    "useWildernessObelisks", "useSeasonalTransports", "includeBankPath", "currencyThreshold",
    "cancelInstead", "recalculateDistance", "reachedDistance", "unreachableTargetDistance",
    "calculationCutoff", "usePoh", "usePohFairyRing", "usePohSpiritTree",
    "useTeleportationPortalsPoh", "pohJewelleryBoxTier", "usePohMountedItems",
    "costAgilityShortcuts", "costBoats", "costCanoes", "costCharterShips", "costFairyRings",
    "costGnomeGliders", "costGrappleShortcuts", "costHotAirBalloons", "costMagicCarpets",
    "costMagicMushtrees", "costMinecarts", "costNonConsumableTeleportationItems",
    "costQuetzalWhistle", "costQuetzals", "costSeasonalTransports", "costShips",
    "costSpiritTrees", "costTeleportationBoxes", "costTeleportationLevers",
    "costTeleportationMinigames", "costTeleportationPortals", "costTeleportationSpells",
    "costTeleportationSpellsHome", "costWildernessObelisks",
})

# The transport policy sent with EVERY path request, in full.
#
# Two reasons it's explicit rather than "let the plugin use whatever the user ticked in its own
# panel". First, a route is only useful if we can actually follow it, and _use_transport can only
# do one thing: click a single menu option on an object or NPC. Every teleport and every
# destination-picker transport produces an edge we'd have to walk across instead, which for a
# teleport means minimap-clicking a tile on the far side of the map -- so inheriting a panel with
# teleports enabled silently breaks routes. Second, the plugin's override map is STICKY: it only
# replaces the map when a non-empty one arrives and never clears it, so a partial override from
# one walk would leak into every later walk in the session. Sending the whole map every time is
# what makes each request independent of the last.
#
# On = what _use_transport can execute today (ordinary doors/gates/ladders/stairs need no key at
# all -- TransportType.TRANSPORT has no toggle and is always on). Off = everything else, until
# there's code that can actually drive it. Routines opt individual keys back on per call via
# walk_to(..., transports={...}).
TRANSPORT_POLICY = {
    # Single-click object/NPC transports -- executable.
    "useAgilityShortcuts": True,
    "useGrappleShortcuts": True,
    "useBoats": True,
    "useShips": True,

    # Clickable, but each opens a destination picker _use_transport can't drive.
    "useCanoes": False,
    "useCharterShips": False,
    "useMinecarts": False,

    # Teleports: no object to click at all, or a code/menu UI on top of one.
    "useTeleportationItems": "None",
    "useTeleportationSpells": False,
    "useTeleportationSpellsHome": False,
    "useTeleportationLevers": False,
    "useTeleportationPortals": False,
    "useTeleportationMinigames": False,
    "useFairyRings": False,
    "useSpiritTrees": False,
    "useGnomeGliders": False,
    "useHotAirBalloons": False,
    "useMagicCarpets": False,
    "useMagicMushtrees": False,
    "useQuetzals": False,
    "useWildernessObelisks": False,

    # Player-owned house routing -- needs a house teleport plus in-house navigation.
    "usePoh": False,
    "usePohFairyRing": False,
    "usePohSpiritTree": False,
    "usePohMountedItems": False,
    "useTeleportationPortalsPoh": False,

    # Leagues/seasonal-only transports, which don't exist on a main.
    "useSeasonalTransports": False,
}


def transport_config(overrides: dict | None = None) -> dict:
    """TRANSPORT_POLICY with `overrides` applied, as a fresh dict. Raises ValueError on a key
    Shortest Path wouldn't recognise -- it ignores unknown keys silently, so a typo would leave
    the baseline in force while the caller believed it had opted into something."""
    unknown = set(overrides or {}) - SHORTEST_PATH_CONFIG_KEYS
    if unknown:
        raise ValueError(f"not a Shortest Path config key: {', '.join(sorted(unknown))}")
    config = dict(TRANSPORT_POLICY)
    config.update(overrides or {})
    return config


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


def route_to(target: dict, *, goal_adjacent: bool = False):
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
                               goal_adjacent=goal_adjacent)
    if tiles is None:
        return None

    # Upstairs, refuse a route that wanders outside the loaded scene. The map has NO DATA above
    # ground outside a building and no data reads as PASSABLE, so a single missing exterior wall
    # on plane 1+ lets BFS escape into open sky and come back down somewhere convenient -- a
    # shortcut through nothing. The loaded scene is 104x104 tiles centred near the player, so a
    # legitimate indoor route on an upper floor never approaches this bound.
    if plane > 0:
        for x, y in tiles:
            if max(abs(x - px), abs(y - py)) > SCENE_HALF_WIDTH:
                debug_log.log("travel", f"discarding a plane-{plane} route that leaves the "
                                        f"loaded scene at ({x},{y}) -- the map treats open sky "
                                        f"as walkable, so this is not a real path")
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
    controller.click_entity(ctx.ser, door)

    waited = 0.0
    while waited < budget:
        time.sleep(DOOR_CONFIRM_POLL_S)
        waited += DOOR_CONFIRM_POLL_S
        if recheck() is None or _crossed(door, here_x, here_y):
            return True

    debug_log.log("travel", f"the {door.get('name')} at ({dx},{dy}) is still shut after "
                            f"{budget:.1f}s -- another player may be holding it, or the "
                            f"click missed")
    return False


def open_door_toward(ctx, goal: dict) -> bool:
    """Open the shut door standing between the player and `goal`. True if one was clicked.

    Replaces open_doorway_to, open_frontier_door and open_nearest_shut_door -- three tiers that
    existed only because there was no route to ask. Costs nothing when the way is clear.

    goal_adjacent because the tile you need a DOOR for and the tile you can WALK to are usually
    different: a hotspot's own tile is furniture and unroutable, while aiming at the standing
    tile instead hides the doorway (both it and the player sit on the same side of the door).
    """
    route = route_to(goal, goal_adjacent=True)
    if not route or len(route) < 2:
        return False
    player = controller.get_player() or {}
    px, py = player.get("x"), player.get("y")
    if px is None:
        return False
    plane = player.get("plane") or 0
    door = _door_on_route(route, plane, _collision_flags(px, py, plane))
    if door is None:
        return False
    return _open_door(ctx, door,
                      lambda: _door_on_route(route, plane, _collision_flags(px, py, plane)))


def walk_to(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE,
            transports: dict | None = None, min_path_tiles: float | None = None,
            goal_adjacent: bool = True) -> bool:
    """Walk to `target`, following a route computed from the global collision map.

    True once within `tolerance` tiles of the target. **False now means something**: there is
    no walking route at all, returned in milliseconds. That replaces the old behaviour where a
    routeless walk fell through to an obstacle-blind greedy walker and hop-clicked a wall until
    WALK_MAX_HOPS x WALK_HOP_TIMEOUT_S ran out -- up to 8 minutes, measured.

    `transports` and `min_path_tiles` are accepted and ignored; they configured Shortest Path,
    which no longer exists. They are kept so the 39 existing call sites keep working, and are
    removed from those call sites and from this signature in a later pass.

    `goal_adjacent` defaults True because most targets here are objects standing on their own
    (unroutable) tile. Pass False when the target really is a tile to stand on.
    """
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
                refreshed = route_to(target, goal_adjacent=goal_adjacent)
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
            refreshed = route_to(target, goal_adjacent=goal_adjacent)
            if refreshed is None:
                return False
            route = refreshed
            index = _nearest_step_index(route, px, py)
        last_pos = (px, py)

        hop = route[_hop_index(route, index, px, py, flags)]
        if (hop["x"], hop["y"]) == (px, py):
            # The route says we are already as far as this hop can take us and we have not
            # arrived -- the remaining gap is not walkable. Fail rather than spin.
            debug_log.log("travel", "route makes no further progress toward the target")
            return False
        if not _hop_to(ctx, hop["x"], hop["y"], hop["plane"]):
            return False

    return False


def _request_computed_path(target: dict, config: dict):
    """Ask Shortest Path for a route to target and poll for the result.

    Returns (steps, reached, termination_reason): a list of {"x", "y", "plane"} waypoints in
    travel order, whether the plugin's own pathfinder actually got to the target, and its
    PathTerminationReason (TARGET_REACHED / SEARCH_EXHAUSTED / CUTOFF_REACHED / CANCELLED).
    steps is None if the plugin isn't available or didn't finish in time.

    The result is matched against the target we asked for before it's accepted. Shortest Path
    replaces its Pathfinder asynchronously, so an early poll can still be reading the PREVIOUS
    walk's finished path -- and before this check, that path was followed as if it were ours,
    sending the player off toward wherever the last routine was going. A build too old to report
    its targets degrades to the old done-is-enough behaviour rather than refusing to path."""
    ack = controller.request_shortest_path(target["x"], target["y"], target["plane"], config=config)
    if not ack.get("requested"):
        return None, False, None

    waited = 0.0
    while waited < PATH_REQUEST_TIMEOUT_S:
        time.sleep(PATH_POLL_INTERVAL_S)
        waited += PATH_POLL_INTERVAL_S

        result = controller.get_shortest_path_result()
        if not result.get("active"):
            return None, False, None
        if not result.get("done") or not _result_is_for(result, target):
            continue

        steps = result.get("steps") or []
        return steps or None, bool(result.get("reached", True)), result.get("terminationReason")

    return None, False, None


def _result_is_for(result: dict, target: dict) -> bool:
    """Whether a finished path is the one we asked for. True when the result carries no target
    info at all -- see _request_computed_path on the older-build fallback."""
    targets = result.get("targets")
    if targets is None:
        return True
    return any(
        t.get("x") == target["x"] and t.get("y") == target["y"] and t.get("plane") == target["plane"]
        for t in targets
    )


def _walk_along_path(ctx, steps: list, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    """Walk a computed route by repeatedly aiming a random number of tiles
    ahead along it (not straight-line toward the final target) and clicking
    via the minimap, same hop-and-click trick as _walk_greedy but following
    an actual obstacle-aware route instead of a straight line.

    Some steps carry a "transport" (see ShortestPathProvider.describeTransport
    -- a door, gate, staircase, ladder, cave entrance, or an NPC that ferries
    you, e.g. a boat captain): minimap-clicking past those doesn't work, since
    the tile on the far side often isn't reachable by ordinary walking at all
    (a different floor, a boat's deck, a cave's own coordinate space). When
    the next transport is within the upcoming hop, this walks right up to it
    instead of hopping past it, then hands off to _use_transport."""
    index = 0
    doors_opened = 0
    last_pos = None
    last_aim = None

    for _ in range(WALK_MAX_HOPS):
        player = controller.get_player()
        px, py = player.get("x"), player.get("y")
        if px is None:
            return False

        if _within_tolerance(px, py, target["x"], target["y"], tolerance):
            return True

        # Same stall recovery as _walk_greedy, and needed here for the same reason: a route can
        # run straight through a shut door WITHOUT carrying a transport step for it, because
        # Shortest Path's collision map believes the door tile is passable and transports.tsv has
        # no entry for ordinary house doors (see the DOOR_* constants). `index` marches on whether
        # or not the player actually moved, so without this the hops keep aiming further down a
        # route the player is no longer travelling. The goal passed is the waypoint last aimed at,
        # not the final target -- it is the one that is reliably on the player's own plane.
        if last_pos == (px, py) and last_aim is not None:
            if (doors_opened < DOOR_MAX_OPENS_PER_WALK
                    and _open_blocking_door(ctx, last_aim, route=steps)):
                doors_opened += 1
                last_pos = None
                continue
        last_pos = (px, py)

        hop_index = min(index + random.randint(WALK_HOP_MIN_TILES, WALK_HOP_MAX_TILES), len(steps) - 1)
        transport_index = _find_next_transport_index(steps, index)

        if transport_index is not None and transport_index <= hop_index:
            origin_index = transport_index - 1
            if origin_index > index:
                origin = steps[origin_index]
                last_aim = origin
                if not _hop_to(ctx, origin["x"], origin["y"], origin["plane"]):
                    return False

            waypoint = steps[transport_index]
            transport = waypoint["transport"]

            if not _transport_is_executable(transport):
                # Walking through an unusable transport is only sane when there's something
                # physically there to walk into. For a teleport (or any edge with no object at
                # all) the far side is somewhere else entirely, so the "walk through it anyway"
                # fallback below would minimap-click a tile across the map. Give up on the route
                # instead and let walk_to fall back to a direct walk.
                debug_log.log("travel", f"route needs a transport we can't drive "
                                        f"({transport.get('type')}: {transport.get('displayInfo')}) "
                                        f"-- abandoning the computed route")
                return False

            if not _use_transport(ctx, transport, waypoint):
                debug_log.log("travel", "couldn't use the transport automatically -- trying to walk through it anyway")
                last_aim = waypoint
                if not _hop_to(ctx, waypoint["x"], waypoint["y"], waypoint["plane"]):
                    return False

            index = transport_index
            continue

        waypoint = steps[hop_index]
        last_aim = waypoint
        if not _hop_to(ctx, waypoint["x"], waypoint["y"], waypoint["plane"]):
            return False
        index = hop_index

    return False


def _find_next_transport_index(steps: list, from_index: int):
    """Index of the next step carrying transport info after from_index, or
    None if the rest of the route is ordinary walking."""
    for i in range(from_index + 1, len(steps)):
        if steps[i].get("transport"):
            return i
    return None


def _transport_is_executable(transport: dict) -> bool:
    """Whether _use_transport can actually drive this edge: it clicks one menu option on one
    object or NPC, so it needs both an id and a name to find. Deliberately keyed on that rather
    than on the transport's type or its isTeleport flag -- a teleport lever IS a clickable
    object, and a boat that somehow arrives without object info is NOT, whatever its type says."""
    return transport.get("objectId") is not None and bool(transport.get("objectName"))


TRANSPORT_INTERACT_TIMEOUT_S = 8.0


def _use_transport(ctx, transport: dict, waypoint: dict) -> bool:
    """Interact with a transport edge instead of trying to walk through it.

    Only handles the "menu option on a specific object or NPC" case
    (transport["objectId"]/["objectName"] present, from ShortestPathProvider
    parsing e.g. "Open Door 9398" or "Travel Holgart 7789") -- transports
    that instead rely on an inventory item, a spell, or a UI (fairy ring
    codes, spirit trees, minecarts, teleport tablets) aren't covered here and
    this returns False for those so the caller falls back to a plain walk
    attempt. The same object/NPC name+id can belong to either a game object
    or an NPC (boat "captains" use this exact same tsv column), so both are
    tried."""
    object_id = transport.get("objectId")
    name = transport.get("objectName")
    option = transport.get("objectOption")
    if object_id is None or not name:
        debug_log.log("travel", f"transport '{transport.get('displayInfo') or transport.get('type')}' "
                                 f"has no object/NPC info -- can't auto-use it yet (item/spell/UI-based transports "
                                 f"aren't handled)")
        return False

    entity = _find_transport_object(object_id) or _find_transport_npc(name)
    if entity is None:
        debug_log.log("travel", f"couldn't find '{option} {name}' ({transport.get('type')}) nearby")
        return False

    debug_log.log("travel", f"using transport: {option} {name} ({transport.get('type')})")
    if not controller.click_entity(ctx.ser, entity):
        return False

    # transport["duration"] is in game ticks (0.6s each) per transports.tsv; add slack for
    # loading transitions (floor changes, boat departures) that a fixed tick count won't cover.
    time.sleep(max(transport.get("duration", 1), 1) * 0.6 + random.uniform(0.8, 1.4))

    waited = 0.0
    while waited < TRANSPORT_INTERACT_TIMEOUT_S:
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is not None and plane == waypoint["plane"] and _within_tolerance(px, py, waypoint["x"], waypoint["y"]):
            return True
        time.sleep(0.6)
        waited += 0.6

    debug_log.log("travel", "used the transport but didn't arrive where expected -- continuing from wherever we ended up")
    return True


def _find_transport_object(object_id: int):
    candidates = [
        o for o in controller.get_objects()
        if o.get("id") == object_id and o.get("clickable")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _find_transport_npc(name: str):
    name_lower = name.lower()
    candidates = [
        n for n in controller.get_npcs()
        if name_lower in (n.get("name") or "").lower() and n.get("screenX") is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


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


def _shut_doors_in_range(px: int, py: int, plane: int, radius: int) -> list:
    """Every SHUT door/gate within `radius` of the player, on the player's plane.

    "Shut" is the object's own menu action ("Open" vs "Close") and nothing else -- see
    _find_blocking_door for why neither name nor id can answer it. A door is a WallObject whose
    id encodes its state and CHANGES when it swings (measured: 7452 -> 7453, 24050 -> 24051), so
    an id is not even stable across the transition."""
    doors = []
    for obj in controller.get_objects(radius=radius) or []:
        if obj.get("plane") != plane or obj.get("objectType") not in DOOR_OBJECT_TYPES:
            continue
        name = (obj.get("name") or "").lower()
        if not any(hint in name for hint in DOOR_NAME_HINTS):
            continue
        actions = obj.get("actions")
        if not actions or not any((a or "").lower() == DOOR_OPEN_ACTION for a in actions):
            continue
        ox, oy = obj.get("x"), obj.get("y")
        if ox is None or max(abs(ox - px), abs(oy - py)) > radius:
            continue
        doors.append(obj)
    return doors


def _find_blocking_door(goal: dict, route: list | None = None):
    """The closed door most likely standing between the player and `goal`, or None.

    When a `route` is supplied it is the ONLY thing that decides which door: the first closed
    door standing on (or beside) the next DOOR_ROUTE_LOOKAHEAD steps. That is both more precise
    and more correct than any distance rule, because Shortest Path's collision map believes doors
    are passable, so a route to somewhere behind a shut door runs straight THROUGH it.

    LIVE 2026-08-28 is why this exists. Stuck at (1771,3622), two tiles from a hotspot at
    (1769,3622) but walled off from it, with three closed doors within six tiles -- and the old
    "the door must be no further from the goal than the player is" rule rejected every one of
    them. When you are walled off two tiles from your goal, the door that actually gets you there
    is FURTHER away in a straight line, not nearer. The pathfinder had it right all along: it
    routed through the door tile (1765,3624) in 12 steps.

    "Closed" is decided by the object's own menu actions, NOT by its name or id. That distinction
    is load-bearing: a closed door and an open one are both WallObjects named "Door", with per-door
    ids (Falador 24050 shut / 24051 open, Varrock 11775, Ardougne 40178), so nothing but the action
    separates them without a hardcoded id table that would need an entry for every door in the
    game. A shut door offers "Open"; an open one offers "Close" -- and left-clicking THAT would
    shut the door we just walked through, turning a working walk into a wedged one.

    An `actions` field absent entirely means the client predates it (see ObjectProvider.
    buildObjectData): return None rather than guessing from the name, so an un-rebuilt client is
    exactly as stuck as it is today and never worse.

    Candidates must also be no further from `goal` than the player is, so a door behind us is
    never opened -- otherwise the recovery could ping-pong between the two doors of one room.
    Equal distance counts as a candidate rather than being excluded: a WallObject is anchored to a
    tile and reports that tile's coordinates, so the door the player is pressed right up against
    is frequently reported on the player's OWN tile, tying exactly. Excluding ties would blind the
    recovery to precisely the door most likely to be the one in the way.
    """
    player = controller.get_player()
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is None or plane != goal.get("plane"):
        return None

    radius = DOOR_ROUTE_SEARCH_RADIUS if route else DOOR_SEARCH_RADIUS
    doors = _shut_doors_in_range(px, py, plane, radius)

    if not doors:
        return None

    # Every door here is already known SHUT from its actions. Where the client also reports
    # collision, use it to drop the ones obstructing nothing -- direct evidence in place of the
    # geometry guess below, which was wrong at both Mariah's and Barbara's.
    blocking = [d for d in doors if _door_blocks(d)]
    told = any(_door_blocks(d) is not None for d in doors)
    if told:
        doors = blocking
        if not doors:
            return None

    if route:
        # Look ahead from the step nearest the PLAYER, not from the head of the list. A stalled
        # _walk_along_path has already advanced its own index past where the player actually
        # stands (it advances whether or not the hop arrived), so slicing the route by that index
        # skips the very door that is blocking us -- caught by test, 2026-08-28.
        start = min(
            range(len(route)),
            key=lambda i: max(abs(route[i]["x"] - px), abs(route[i]["y"] - py)),
        )
        for step in route[start:start + DOOR_ROUTE_LOOKAHEAD]:
            for door in doors:
                if (max(abs(door["x"] - step["x"]), abs(door["y"] - step["y"]))
                        <= DOOR_ROUTE_ADJACENCY):
                    return door
        return None

    if told:
        # Prefer a door whose blocked EDGE actually lies between us and the goal -- the
        # precise version of the question both earlier heuristics were groping at.
        separating = [d for d in doors if _door_separates(d, px, py, goal["x"], goal["y"])]
        if separating:
            return min(separating, key=lambda d: math.hypot(d["x"] - px, d["y"] - py))
        # Nothing crosses our path, so no door is in the way and we say so. This used to fall
        # back to "the nearest door that obstructs anything", which VERIFIED live 2026-08-28
        # is a false positive: with Barbara's OUTER door open and the way in genuinely clear,
        # it still returned the INNER door 3 tiles away -- a wasted click, and worse a True
        # return, which resets the walker's stall tracking and hides why it is really stuck.
        return None

    # No route and no collision data -- fall back to proximity, only safe while it stays tight.
    player_gap = math.hypot(goal["x"] - px, goal["y"] - py)
    best, best_gap = None, player_gap
    for door in doors:
        gap = math.hypot(goal["x"] - door["x"], goal["y"] - door["y"])
        if gap <= best_gap:
            best, best_gap = door, gap
    return best


def _open_blocking_door(ctx, goal: dict, route: list | None = None) -> bool:
    """Try to clear a stalled walk by opening the closed door in the way. True if one was clicked.

    A plain left-click, not click_menu_action: a shut door's FIRST option is "Open" (every door
    click in the recordings is a GAME_OBJECT_FIRST_OPTION), and _find_blocking_door has already
    proved that option is on this object, so there is nothing for a right-click to disambiguate.

    check_ui_blockers is left at its default, matching _use_transport -- which already clicks
    doors and gates for the transports Shortest Path DOES know about. The False used elsewhere in
    this file is for INVENTORY/interface clicks (the ring, the seed pod); passing it for a world
    object skips a check that exists for good reason (see teleport_to_ferox's rotate-forever loop
    in CLAUDE.md).
    """
    goal = _routable_goal(goal)
    door = _find_blocking_door(goal, route=route)

    if door is None and route is None:
        # Shut doors around us but none across the straight line: the way in is a DETOUR, and a
        # single-axis edge test structurally cannot see one. MEASURED live 2026-08-29 at
        # Barbara's -- player (1750,3536), hotspot (1749,3536) ONE tile west through a wall, two
        # shut doors nearby and neither between two adjacent tiles. Both "no" answers were
        # correct; the question was too narrow. The pathfinder can answer it, because its
        # collision map treats doors as passable and it routes straight through the doorway.
        # Narrow trigger on purpose: a walk with no shut doors near it never pays for a request.
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is not None and _shut_doors_in_range(px, py, plane, DOOR_SEARCH_RADIUS):
            steps, _reached, _reason = _request_computed_path(goal, transport_config())
            if steps:
                route = steps
                door = _find_blocking_door(goal, route=steps)

    if door is None:
        return False

    dx, dy = door.get("x"), door.get("y")

    # Budget the WALK to the door, not just the swing -- see DOOR_WALK_ALLOWANCE_S.
    here = controller.get_player() or {}
    here_x, here_y = here.get("x"), here.get("y")
    if here_x is not None and dx is not None:
        tiles = max(abs(here_x - dx), abs(here_y - dy))
    else:
        tiles = door.get("distance") or 0
    budget = min(DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S, DOOR_OPEN_WAIT_MAX_S)

    debug_log.log("travel", f"opening the {door.get('name')} at ({dx},{dy}) in the way "
                            f"({tiles} tiles off, waiting up to {budget:.1f}s)")
    controller.click_entity(ctx.ser, door)

    # CONFIRM, don't sleep and hope. A door is a WallObject whose id encodes its state -- every
    # pair measured is shut -> shut+1 (Barbara's 7452 -> 7453 twice, Falador's 24050 -> 24051) --
    # so it also MOVES a tile and stops being the object we clicked. The durable signal is the
    # one we actually care about: re-read the doorway and the blocked edge is gone. A fixed wait
    # both burned time when the door opened quickly and continued in silence when it never opened.
    waited = 0.0
    while waited < budget:
        time.sleep(DOOR_CONFIRM_POLL_S)
        waited += DOOR_CONFIRM_POLL_S
        # Either the doorway cleared, or we are already through it -- see _crossed.
        if _find_blocking_door(goal, route=route) is None or _crossed(door, here_x, here_y):
            return True

    debug_log.log("travel", f"the {door.get('name')} at ({dx},{dy}) is still shut after "
                            f"{budget:.1f}s -- another player may be holding it, or the "
                            f"click missed")
    return True


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


def can_reach(goal: dict, reach: set | None = None, flags: dict | None = None,
              plane: int | None = None) -> bool | None:
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
    return False


def open_frontier_door(ctx, goal: dict) -> bool:
    """Open the shut door that would EXTEND the reachable area toward `goal`.

    This is the precise version of the door question, and it needs no straight-line reasoning at
    all: flood-fill what we can walk to, then find a shut door whose own blocked edge has one side
    inside that area and the other side outside. That door is, by construction, a way out of the
    region we are trapped in -- whether it lies straight ahead or is a detour behind us, which is
    exactly the case `_door_separates` structurally cannot see (MEASURED outside ROSS's: the only
    way in was a door with BOTH player and hotspot to the east of its blocked edge).

    Ties are broken by how close the door's far side gets us to the goal, so with several ways out
    we take the one that heads the right way. Returns True if a door was clicked."""
    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is None:
        return False

    reach = reachable_set(px, py, plane)
    if not reach:
        return False

    gx, gy = goal.get("x"), goal.get("y")
    candidates = []
    for door in _shut_doors_in_range(px, py, plane, DOOR_SEARCH_RADIUS):
        dx0, dy0 = door.get("x"), door.get("y")
        flags = door.get("collisionFlags")
        if flags is None:
            continue
        for bit, ex, ey in DOOR_EDGE_BLOCKS:
            if not flags & bit:
                continue
            near, far = (dx0, dy0), (dx0 + ex, dy0 + ey)
            # One side in, the other out -- the door is on the frontier of where we can go.
            if (near in reach) == (far in reach):
                continue
            outside = far if near in reach else near
            candidates.append((max(abs(outside[0] - gx), abs(outside[1] - gy)), door))

    if not candidates:
        return False

    candidates.sort(key=lambda c: c[0])
    door = candidates[0][1]
    dx, dy = door.get("x"), door.get("y")
    tiles = max(abs(px - dx), abs(py - dy))
    budget = min(DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S, DOOR_OPEN_WAIT_MAX_S)

    debug_log.log("travel", f"opening the {door.get('name')} at ({dx},{dy}) -- it is the way out "
                            f"of where we are ({tiles} tiles off, up to {budget:.1f}s)")
    controller.click_entity(ctx.ser, door)

    waited = 0.0
    while waited < budget:
        time.sleep(DOOR_CONFIRM_POLL_S)
        waited += DOOR_CONFIRM_POLL_S
        still = [d for d in _shut_doors_in_range(px, py, plane, DOOR_SEARCH_RADIUS)
                 if (d.get("x"), d.get("y")) == (dx, dy)]
        if not still or _crossed(door, px, py):
            return True
    debug_log.log("travel", f"the {door.get('name')} at ({dx},{dy}) is still shut after "
                            f"{budget:.1f}s")
    return True


def open_nearest_shut_door(ctx, goal: dict) -> bool:
    """LAST RESORT: open the nearest shut door to us, without proving it is in the way.

    **Not for regular use** (USER 2026-08-29: "we cannot use the cannot reach fallback for regular
    usage, this has to be last resort fallback"). `open_frontier_door` is what answers the door
    question properly -- it names the exact door on the boundary of the region we are stuck in --
    and this only exists for the case where even that finds nothing. In run28, before the frontier
    test existed, this blunt tier was firing three or four times per contract and was effectively
    the primary mechanism; that is the thing it must never be again.

    It is deliberately permissive and must only ever be called from a path that has ALREADY
    established it is stuck -- an earlier attempt to make the ordinary pre-walk check this loose
    was reverted as a false positive (see CLAUDE.md).

    Why the strict test is not enough on its own: it is a single-axis "is this edge between us and
    the goal" question, so it structurally cannot see a door that is a DETOUR. MEASURED live
    2026-08-29 outside ROSS's house -- player (2614,3319) on the grass north of the building, the
    Broken Range at (2617,3318) inside it, and the only way in a door at (2610,3316) whose blocked
    edge (2610|2611) has BOTH of us to the east of it. The straight-line answer "not between us"
    was correct and useless; the route branch, which is what normally catches a detour, had
    returned no route at all that moment. The user watched it stand outside and never try the one
    door in sight.

    Once we are stuck, a wasted door click costs a second and being wrong costs nothing we still
    have; not trying costs the whole contract. Returns True if a door was clicked."""
    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is None:
        return False

    doors = _shut_doors_in_range(px, py, plane, DOOR_SEARCH_RADIUS)
    if not doors:
        return False

    # Nearest to US, not to the goal: we have to reach it before we can open it, and a detour
    # door is by definition further from the goal than we are.
    door = min(doors, key=lambda d: max(abs(d["x"] - px), abs(d["y"] - py)))
    dx, dy = door.get("x"), door.get("y")
    tiles = max(abs(px - dx), abs(py - dy))
    budget = min(DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S, DOOR_OPEN_WAIT_MAX_S)

    debug_log.log("travel", f"stuck -- opening the nearest shut {door.get('name')} at "
                            f"({dx},{dy}), {tiles} tiles off, waiting up to {budget:.1f}s")
    controller.click_entity(ctx.ser, door)

    waited = 0.0
    while waited < budget:
        time.sleep(DOOR_CONFIRM_POLL_S)
        waited += DOOR_CONFIRM_POLL_S
        # The door MOVES a tile and changes id when it swings, so "is a shut door still standing
        # on that exact tile" is the durable check -- same reasoning as _open_blocking_door's.
        still = [d for d in _shut_doors_in_range(px, py, plane, DOOR_SEARCH_RADIUS)
                 if (d.get("x"), d.get("y")) == (dx, dy)]
        if not still:
            return True

    debug_log.log("travel", f"the {door.get('name')} at ({dx},{dy}) is still shut after "
                            f"{budget:.1f}s")
    return True


def open_doorway_to(ctx, goal: dict) -> bool:
    """Public form of the door recovery: open the shut door standing between the player and
    `goal`, if there is one. True if a door was clicked.

    Exists because the tile you need a DOOR for and the tile you can WALK to are often not the
    same tile. MEASURED live 2026-08-29 at Barbara's: the hotspot at (1749,3536) is furniture, so
    Shortest Path returns no route to it at all (0 steps, 5 calls out of 5) -- but its neighbours
    and the home's standing tile route fine. Asking the walker to head for the hotspot therefore
    just wedges the obstacle-blind greedy fallback, while asking it to head for the standing tile
    hides the doorway (both tiles are on the same side of it). Resolve the door against the thing
    you cannot reach; walk to the thing you can."""
    return _open_blocking_door(ctx, goal)


def _walk_greedy(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    """Straight-line walk toward target with no obstacle awareness -- the
    fallback when Shortest Path isn't available. True once within
    `tolerance` tiles of the target, False if it gives up (different
    plane, hop point off the minimap, or WALK_MAX_HOPS exhausted)."""
    target_x, target_y, target_plane = target["x"], target["y"], target["plane"]
    doors_opened = 0
    last_dist = None

    for _ in range(WALK_MAX_HOPS):
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if plane != target_plane:
            debug_log.log("travel", "target is on a different plane -- can't walk there yet")
            return False

        if _within_tolerance(px, py, target_x, target_y, tolerance):
            return True

        dist = math.hypot(target_x - px, target_y - py)

        # A hop that bought no ground means something is in the way, and the single thing this
        # obstacle-blind walker can be wedged against that it can also clear is a shut door (see
        # the DOOR_* constants for why Shortest Path never warns us about one). Without this the
        # loop just re-clicks the identical hop until WALK_MAX_HOPS runs out.
        if last_dist is not None and dist >= last_dist - DOOR_PROGRESS_EPSILON:
            if doors_opened < DOOR_MAX_OPENS_PER_WALK:
                # Ask the pathfinder where the way through actually is. This walker has no route
                # of its own -- walk_to sends anything under SHORT_HOP_TILES straight here -- and
                # LIVE 2026-08-28 the failing walk was only FIVE tiles, so there was no route to
                # consult and the proximity fallback picked nothing. Shortest Path routes through
                # the shut door (its collision map thinks doors are passable), which is exactly
                # the information needed.
                steps, _reached, _reason = _request_computed_path(target, transport_config())
                if _open_blocking_door(ctx, target, route=steps):
                    doors_opened += 1
                    last_dist = None   # the door cost a pass; judge progress from after it
                    continue
        last_dist = dist
        hop_dist = min(dist, random.randint(WALK_HOP_MIN_TILES, WALK_HOP_MAX_TILES))
        hop_x = int(round(px + (target_x - px) / dist * hop_dist))
        hop_y = int(round(py + (target_y - py) / dist * hop_dist))

        if not _hop_to(ctx, hop_x, hop_y, plane):
            return False

    return False


def _click_walk_point(ctx, point: dict, shift: bool) -> None:
    """Click a walk destination, holding SHIFT for a viewport click so it force-walks.

    Shift+click is OSRS's own "Walk here" shortcut and looks like any other single click, so
    there is no reason not to use it for every ground-tile walk rather than only ones near a
    known-risky object."""
    if not shift:
        controller.click_entity(ctx.ser, point, offset=(0, 0))
        return
    controller.key_down(ctx.ser, "SHIFT")
    try:
        controller.click_entity(ctx.ser, point, offset=(0, 0))
    finally:
        controller.key_up(ctx.ser, "SHIFT")


def _hop_to(ctx, x: int, y: int, plane: int) -> bool:
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
    ox, oy = origin.get("x"), origin.get("y")
    point = None
    on_ground = False        # viewport click (needs SHIFT) vs minimap click (does not)
    aim_x, aim_y = x, y

    for fraction in HOP_PULL_IN_FRACTIONS:
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
            ground_point = controller.get_ground_walk_point(aim_x, aim_y, plane)
            if ground_point.get("onScreen"):
                point = ground_point
                on_ground = True
                break

        minimap_point = controller.get_minimap_walk_point(aim_x, aim_y, plane)
        if minimap_point.get("onMinimap"):
            point = minimap_point
            break

        # Off the minimap (its reach is a user setting -- zooming in shrinks it) and too far for
        # the short-hop rule: a rendered tile still beats giving up on the walk.
        if not short:
            ground_point = controller.get_ground_walk_point(aim_x, aim_y, plane)
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

    waited = 0.0
    while waited < WALK_HOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        px, py = player.get("x"), player.get("y")
        # Against the point actually clicked, not the one asked for -- otherwise a pulled-in hop
        # would always wait out the full timeout instead of returning on arrival.
        if px is not None and _within_tolerance(px, py, aim_x, aim_y):
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
