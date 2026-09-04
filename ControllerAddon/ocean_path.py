# ocean_path.py
#
# Obstacle-aware routing for sailing.py's boat repositioning, built on top of TileProvider's
# /tiles collision data (walkable/blocked, from RuneLite's own CollisionDataFlag.BLOCK_MOVEMENT_FULL
# map) -- CONFIRMED live 2026-08-02: open water reads walkable, a nearby landmass reads blocked in
# a tight cluster, so this data source correctly distinguishes sailable water from land. See
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md's "Design update: obstacle-aware
# route planning" section for the full design.
#
# Only covers the currently loaded scene (~104x104 tiles around the player, one plane) -- fine
# here since repositioning targets (from routines/shipwreck_salvaging.py's find_nearby_wreck) are
# bounded by maxSailDistance (60 tiles), well within that range.

import heapq
import math

import controller

_FALLBACK_CLEARANCE_TILES = 2  # dilation radius used when the boat's true bounds aren't
                                # available (not aboard a boat, or an older bridge build without
                                # PlayerProvider's boatWidth/boatHeight fields). CONFIRMED live
                                # 2026-08-02 as a reasonable default in its own right: the original
                                # guess (4, half the boat's ~8-tile WorldView footprint) sealed off
                                # routing entirely in a real dense island cluster (2708 of 10816
                                # loaded tiles blocked -- dilating by 4 shrank passable water to
                                # ~2700 tiles, disconnected, so find_ocean_route returned None even
                                # for the boat's own current position). 2 keeps a real margin while
                                # still leaving usable channels; the stuck-detection safety net in
                                # sailing.py covers cases this margin turns out too tight for.
                                # _boat_clearance_tiles can now compute a similarly large radius from
                                # the boat's real bounds -- the retry-ladder in find_ocean_route falls
                                # back to this constant when that happens, for exactly this reason.

# 16-directional grid movement -- the standard 8 compass offsets plus 8 "knight-move" offsets
# ((+-1,+-2)/(+-2,+-1)), which approximate the 22.5-degree angles a square grid can't represent as
# single unit steps. This is still just the route-PLANNING grid, not a model of the boat's real
# heading options -- steer_toward's hops get compass-snapped to ~8 headings regardless of how many
# directions this grid uses (see steer_toward), so the payoff is purely straighter/tighter routes
# around obstacles (less zig-zag overshoot than 8-directional's 45-degree-only diagonals), not
# finer boat control. UNVERIFIED live -- not yet observed producing a measurably tighter route
# through a real narrow channel; flagged for confirmation same as this file's other constants.
_NEIGHBOR_OFFSETS = [
    (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1),
    (-2, -1), (-2, 1), (2, -1), (2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2),
]
_DIAGONAL_COST = 1.4142135
_STRAIGHT_COST = 1.0
_KNIGHT_COST = 2.2360680

# For each knight offset, the two flanking one-step cells it "jumps past" -- CONFIRMED by
# inspection, not live: without this check a knight move could cut straight through a blocked tile
# sitting between its start and end (the same corner-cutting risk that 2-tile-diagonal jumps always
# carry, but the existing 8-directional moves never had to guard against since they only ever
# jump 1 tile). Both flanking cells must be passable for the knight move to be allowed.
_KNIGHT_CHECK_CELLS = {
    (-2, -1): ((-1, 0), (-1, -1)), (-2, 1): ((-1, 0), (-1, 1)),
    (2, -1): ((1, 0), (1, -1)), (2, 1): ((1, 0), (1, 1)),
    (-1, -2): ((0, -1), (-1, -1)), (-1, 2): ((0, 1), (-1, 1)),
    (1, -2): ((0, -1), (1, -1)), (1, 2): ((0, 1), (1, 1)),
}


def find_ocean_route(start: dict, target: dict) -> list | None:
    """A* route from start to target ({"x", "y", "plane"} each) over /tiles' collision data,
    simplified to a handful of corner waypoints. Returns None if either point falls outside the
    currently loaded scene or is itself on a blocked tile, if nothing in the scene is even blocked
    (no detour needed), or if no route exists at all -- callers should fall back to steering
    directly at the target in every None case, same as sailing.py's behavior before this module
    existed."""
    if start["plane"] != target["plane"]:
        return None

    tiles = controller.get_tiles()
    plane = start["plane"]
    loaded = {(t["x"], t["y"]) for t in tiles if t.get("plane") == plane}
    blocked = {(t["x"], t["y"]) for t in tiles if t.get("blocked") and t.get("plane") == plane}
    if not blocked:
        return None

    start_xy = (start["x"], start["y"])
    target_xy = (target["x"], target["y"])
    if start_xy not in loaded or target_xy not in loaded:
        return None
    if start_xy in blocked or target_xy in blocked:
        # Genuinely standing on/aiming at land isn't a routing problem this module can solve.
        return None

    clearance_tiles = _boat_clearance_tiles(controller.get_player())
    passable = _build_passable_set(tiles, plane, blocked, clearance_tiles)
    # The endpoints are always allowed even if they fall inside another obstacle's clearance
    # dilation -- CONFIRMED live 2026-08-02: excluding them made find_ocean_route return None for
    # the boat's own current position after it ran aground once (a dense island cluster easily
    # puts the boat's actual position within BOAT_CLEARANCE_TILES of SOME nearby land), which meant
    # every "replan from where we're stuck" attempt fell straight back to the same doomed direct
    # line that got it stuck in the first place. Only intermediate waypoints need to stay clear.
    passable.add(start_xy)
    passable.add(target_xy)

    raw_path = _astar(start_xy, target_xy, passable)
    if raw_path is None and clearance_tiles > _FALLBACK_CLEARANCE_TILES:
        # The true-bounds radius can disconnect a dense island cluster outright (see
        # _FALLBACK_CLEARANCE_TILES's comment) -- a tighter-but-usable route beats no route.
        passable = _build_passable_set(tiles, plane, blocked, _FALLBACK_CLEARANCE_TILES)
        passable.add(start_xy)
        passable.add(target_xy)
        raw_path = _astar(start_xy, target_xy, passable)
    if raw_path is None:
        return None

    waypoints = _smooth_path(_simplify_path(raw_path), passable)
    # Drop the starting point (the boat is already there); keep the rest, including the final
    # target -- sail_to steers through each of these in turn.
    return [{"x": x, "y": y, "plane": start["plane"]} for x, y in waypoints[1:]]


def _boat_clearance_tiles(player: dict) -> int:
    """Isotropic dilation radius (in tiles) around blocked tiles, sized to the boat's own true
    hull footprint (PlayerProvider's boatWidth/boatHeight -- RuneLite's own WorldEntityConfig
    bounds, see data/decompiled/sailing-plugin's TrueTileIndicator for the reference usage this is
    modeled on) instead of a guessed constant. Uses the larger of width/height, halved and rounded
    up -- sized to the hull's largest side rather than its diagonal, a deliberate under-
    approximation; this dilation still has no orientation awareness, same as before (see the
    2026-08-02 design note on scope: full oriented-rectangle collision was explicitly out of
    scope). Falls back to the original hand-tuned guess when the boat's true bounds aren't
    available (not aboard a boat, an older bridge build without these fields, a non-positive
    value, or an implausibly large value that likely indicates a unit-conversion bug upstream)."""
    width = player.get("boatWidth")
    height = player.get("boatHeight")
    if width is None or height is None or width <= 0 or height <= 0:
        return _FALLBACK_CLEARANCE_TILES
    largest = max(width, height)
    if largest > 100:  # implausible for a real boat hull -- likely a unit-conversion bug upstream
        return _FALLBACK_CLEARANCE_TILES
    return max(1, math.ceil(largest / 2))


def _build_passable_set(tiles, plane, blocked, clearance_tiles):
    """Every loaded tile that ISN'T within clearance_tiles (Chebyshev) of a blocked tile --
    dilating outward from each blocked tile is cheaper than testing every candidate tile against
    every blocked tile."""
    passable = {(t["x"], t["y"]) for t in tiles if t.get("plane") == plane}
    clearance_zone = set()
    for (bx, by) in blocked:
        for dx in range(-clearance_tiles, clearance_tiles + 1):
            for dy in range(-clearance_tiles, clearance_tiles + 1):
                clearance_zone.add((bx + dx, by + dy))
    return passable - clearance_zone


def _astar(start_xy, target_xy, passable):
    open_heap = [(0.0, start_xy)]
    came_from = {}
    g_score = {start_xy: 0.0}

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current == target_xy:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        cx, cy = current
        for dx, dy in _NEIGHBOR_OFFSETS:
            neighbor = (cx + dx, cy + dy)
            if neighbor not in passable:
                continue
            check_cells = _KNIGHT_CHECK_CELLS.get((dx, dy))
            if check_cells is not None:
                if not all((cx + ox, cy + oy) in passable for ox, oy in check_cells):
                    continue
                step_cost = _KNIGHT_COST
            else:
                step_cost = _DIAGONAL_COST if dx != 0 and dy != 0 else _STRAIGHT_COST
            tentative_g = g_score[current] + step_cost
            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + _heuristic(neighbor, target_xy)
                heapq.heappush(open_heap, (f_score, neighbor))

    return None


def _heuristic(a, b):
    # Plain Euclidean distance, not the old octile estimate -- CONFIRMED by inspection: octile
    # assumes only straight/diagonal moves and would overestimate (become inadmissible) now that a
    # knight move covers displacement (1,2)/(2,1) for less cost (sqrt(5) ~= 2.236) than the
    # equivalent straight+diagonal combo octile assumes (2 + sqrt(2)-1 ~= 2.414). Euclidean distance
    # never exceeds any move's real cost regardless of move set (every step's cost >= its straight-
    # line length here), so it stays admissible without needing to special-case the knight moves.
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _smooth_path(path, passable):
    """String-pulling (added 2026-09-04): from each kept point, jump straight to the FARTHEST
    later point the segment can reach without leaving `passable` (the already-dilated water
    set), and keep only those. _simplify_path alone keeps every grid-direction change, and an
    A* line at any angle the 16-direction grid cannot draw exactly (say slope 1/3) is a run of
    straight/knight alternations -- one "corner" every tile or two, each of which cost
    steer_toward a hop click and a poll for a bend the compass-snapped heading could not
    follow anyway. A segment is tested tile by tile (four samples per tile), so a smoothed leg
    never cuts through the clearance zone the grid route avoided."""
    if len(path) <= 2:
        return path
    smoothed = [path[0]]
    i = 0
    last = len(path) - 1
    while i < last:
        j = last
        while j > i + 1 and not _line_clear(path[i], path[j], passable):
            j -= 1
        smoothed.append(path[j])
        i = j
    return smoothed


def _line_clear(a, b, passable) -> bool:
    """Every tile the straight segment a->b touches (sampled four times per tile) is passable."""
    steps = max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 4 + 1
    for k in range(steps + 1):
        t = k / steps
        x = int(round(a[0] + (b[0] - a[0]) * t))
        y = int(round(a[1] + (b[1] - a[1]) * t))
        if (x, y) not in passable:
            return False
    return True


def _simplify_path(path):
    """Keep only the path's corner points (where the direction of travel changes) plus the first
    and last point -- a handful of waypoints instead of one per tile."""
    if len(path) <= 2:
        return path

    simplified = [path[0]]
    for i in range(1, len(path) - 1):
        prev_dx = path[i][0] - simplified[-1][0]
        prev_dy = path[i][1] - simplified[-1][1]
        next_dx = path[i + 1][0] - path[i][0]
        next_dy = path[i + 1][1] - path[i][1]
        if _sign(prev_dx) != _sign(next_dx) or _sign(prev_dy) != _sign(next_dy):
            simplified.append(path[i])
    simplified.append(path[-1])
    return simplified


def _sign(v):
    return (v > 0) - (v < 0)
