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

import controller

BOAT_CLEARANCE_TILES = 4  # dilation radius around blocked tiles -- half the boat's own ~8-tile
                           # WorldView footprint (confirmed live, PlayerProvider's
                           # debugWorldViewSizeX/Y), so a route's center-line staying clear of a
                           # blocked tile doesn't mean the boat's actual hull does too. Initial
                           # estimate, not yet live-tuned.

# 8-directional grid movement -- fine-grained enough for the compass-snapped heading steer_toward
# already handles; the grid is only used to find a route around obstacles, not to model the boat's
# exact heading options.
_NEIGHBOR_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_DIAGONAL_COST = 1.4142135
_STRAIGHT_COST = 1.0


def find_ocean_route(start: dict, target: dict) -> list | None:
    """A* route from start to target ({"x", "y", "plane"} each) over /tiles' collision data,
    simplified to a handful of corner waypoints. Returns None if either point falls outside the
    currently loaded scene, if nothing in the scene is even blocked (no detour needed), or if no
    route exists at all -- callers should fall back to steering directly at the target in every
    None case, same as sailing.py's behavior before this module existed."""
    if start["plane"] != target["plane"]:
        return None

    tiles = controller.get_tiles()
    blocked = {(t["x"], t["y"]) for t in tiles if t.get("blocked") and t.get("plane") == start["plane"]}
    if not blocked:
        return None

    passable = _build_passable_set(tiles, start["plane"], blocked)

    start_xy = (start["x"], start["y"])
    target_xy = (target["x"], target["y"])
    if start_xy not in passable or target_xy not in passable:
        return None

    raw_path = _astar(start_xy, target_xy, passable)
    if raw_path is None:
        return None

    waypoints = _simplify_path(raw_path)
    # Drop the starting point (the boat is already there); keep the rest, including the final
    # target -- sail_to steers through each of these in turn.
    return [{"x": x, "y": y, "plane": start["plane"]} for x, y in waypoints[1:]]


def _build_passable_set(tiles, plane, blocked):
    """Every loaded tile that ISN'T within BOAT_CLEARANCE_TILES (Chebyshev) of a blocked tile --
    dilating outward from each blocked tile is cheaper than testing every candidate tile against
    every blocked tile."""
    passable = {(t["x"], t["y"]) for t in tiles if t.get("plane") == plane}
    clearance_zone = set()
    for (bx, by) in blocked:
        for dx in range(-BOAT_CLEARANCE_TILES, BOAT_CLEARANCE_TILES + 1):
            for dy in range(-BOAT_CLEARANCE_TILES, BOAT_CLEARANCE_TILES + 1):
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
            step_cost = _DIAGONAL_COST if dx != 0 and dy != 0 else _STRAIGHT_COST
            tentative_g = g_score[current] + step_cost
            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + _octile_heuristic(neighbor, target_xy)
                heapq.heappush(open_heap, (f_score, neighbor))

    return None


def _octile_heuristic(a, b):
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return max(dx, dy) + 0.4142135 * min(dx, dy)


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
