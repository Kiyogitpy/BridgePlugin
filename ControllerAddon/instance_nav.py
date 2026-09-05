"""Routing inside an INSTANCE, where worldmap.py's static map is meaningless.

Added 2026-09-05 (Gauntlet sub-project 1, docs/superpowers/specs/2026-09-05-gauntlet-instance-
plumbing-design.md). Coordinates here are INSTANCE-SPACE (`instX`/`instY` on the bridge, the
client's own scene base plus scene offset) -- unique per scene tile and stable while the scene is
loaded, which for the Gauntlet (one region, 7768, plane 1) is the whole run. The map is one
`/collision?all=true` read (CollisionGrid.java: flat, row-major by y, index = sy * size + sx),
so a room-scale route costs one HTTP call plus a BFS over at most 104x104 tiles.

Edge semantics are travel.py's (both tiles' opposite movement bits refuse a crossing; a tile
carrying TILE_BLOCKING_FLAGS cannot be stood on). A diagonal step needs BOTH orthogonal corner
routes fully open -- the game's own rule, and the conservative reading of it. No doors, no
transports: the Gauntlet has neither. Empty answers mean "cannot tell", never "blocked":
InstanceMap.load returns None and find_path returns None, and walk_to then declines to click.
"""
from __future__ import annotations

from collections import deque

import controller
import debug_log
import travel
from travel import (BLOCK_MOVEMENT_EAST, BLOCK_MOVEMENT_NORTH, BLOCK_MOVEMENT_SOUTH,
                    BLOCK_MOVEMENT_WEST, TILE_BLOCKING_FLAGS)

CARDINALS = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAGONALS = ((1, 1), (1, -1), (-1, 1), (-1, -1))


class InstanceMap:
    def __init__(self, base_x: int, base_y: int, plane: int, size: int, flags: list):
        self.base_x = base_x
        self.base_y = base_y
        self.plane = plane
        self.size = size
        self.flags = flags

    @classmethod
    def load(cls, plane: int | None = None):
        grid = controller.get_collision_grid(plane)
        if not grid:
            return None
        size = int(grid.get("size") or 104)
        flags = grid.get("flags") or []
        if len(flags) < size * size:
            return None
        return cls(int(grid["baseX"]), int(grid["baseY"]), int(grid.get("plane", plane or 0)), size, flags)

    def flags_at(self, x: int, y: int):
        sx, sy = x - self.base_x, y - self.base_y
        if not (0 <= sx < self.size and 0 <= sy < self.size):
            return None
        return self.flags[sy * self.size + sx]

    def standable(self, x: int, y: int) -> bool:
        f = self.flags_at(x, y)
        return f is not None and not (f & TILE_BLOCKING_FLAGS)

    def edge_open(self, x: int, y: int, dx: int, dy: int) -> bool:
        """Cardinal edge from (x, y) toward (dx, dy): refused if EITHER tile records the wall."""
        a = self.flags_at(x, y)
        b = self.flags_at(x + dx, y + dy)
        if a is None or b is None:
            return False
        if dx == 1:
            return not (a & BLOCK_MOVEMENT_EAST or b & BLOCK_MOVEMENT_WEST)
        if dx == -1:
            return not (a & BLOCK_MOVEMENT_WEST or b & BLOCK_MOVEMENT_EAST)
        if dy == 1:
            return not (a & BLOCK_MOVEMENT_NORTH or b & BLOCK_MOVEMENT_SOUTH)
        return not (a & BLOCK_MOVEMENT_SOUTH or b & BLOCK_MOVEMENT_NORTH)

    def step_ok(self, x: int, y: int, dx: int, dy: int) -> bool:
        """Can the player step from (x, y) by (dx, dy)? Destination must be standable; a
        cardinal needs its edge open; a diagonal needs both corner routes standable and open."""
        nx, ny = x + dx, y + dy
        if not self.standable(nx, ny):
            return False
        if dx == 0 or dy == 0:
            return self.edge_open(x, y, dx, dy)
        cx, cy = x + dx, y          # corner via the x step
        kx, ky = x, y + dy          # corner via the y step
        via_x = (self.standable(cx, cy) and self.edge_open(x, y, dx, 0)
                 and self.edge_open(cx, cy, 0, dy))
        via_y = (self.standable(kx, ky) and self.edge_open(x, y, 0, dy)
                 and self.edge_open(kx, ky, dx, 0))
        return via_x and via_y


def neighbours_of(x: int, y: int) -> list:
    return [(x + dx, y + dy) for dx, dy in CARDINALS + DIAGONALS]


def find_path(imap: InstanceMap, start: tuple, goal: tuple, goal_adjacent: bool = False,
              avoid: set | None = None):
    """BFS from start to goal over the instance map. Returns the tile list including start, or
    None. With goal_adjacent the search ends on any cardinal neighbour of the goal (the goal
    itself is usually furniture). `avoid` tiles are never entered."""
    start = (int(start[0]), int(start[1]))
    goal = (int(goal[0]), int(goal[1]))
    avoid = avoid or set()
    if imap.flags_at(*start) is None:
        return None

    def done(t):
        if goal_adjacent:
            return abs(t[0] - goal[0]) + abs(t[1] - goal[1]) == 1
        return t == goal

    if done(start):
        return [start]
    prev = {start: None}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for dx, dy in CARDINALS + DIAGONALS:
            nxt = (x + dx, y + dy)
            if nxt in prev or nxt in avoid:
                continue
            if not imap.step_ok(x, y, dx, dy):
                continue
            prev[nxt] = (x, y)
            if done(nxt):
                path = [nxt]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            queue.append(nxt)
    return None

WALK_MAX_HOPS = 40           # a Gauntlet room-to-room walk is 2-4 hops; 40 is a wedge, not a walk


def player_inst():
    """(instX, instY, plane) from /player, or None when the bridge cannot say."""
    p = controller.get_player() or {}
    x, y = p.get("instX"), p.get("instY")
    if x is None or y is None:
        return None
    return int(x), int(y), int(p.get("plane") or 0)


def walk_to(ctx, x: int, y: int, tolerance: float = travel.WALK_ARRIVAL_TOLERANCE) -> bool:
    """Walk to an instance-space tile: reload the map, route from the player's instX/instY,
    hop along the route through travel._hop_to(inst=True) (minimap beyond
    HOP_VIEWPORT_MAX_TILES, shift-clicked viewport inside it, the lead-click release, the
    blocker opt-outs -- all inherited). A hop that leaves the player on the same tile marks the
    route's next tile as refused and re-routes (`avoid`, bounded by travel.WALK_MAX_REROUTES).
    False when there is no map, no route, or WALK_MAX_HOPS is spent."""
    goal = (int(x), int(y))
    avoid: set = set()
    reroutes = 0
    hops = 0
    while hops < WALK_MAX_HOPS:
        here = player_inst()
        if here is None:
            debug_log.log("inav", "no instance position from /player -- cannot walk")
            return False
        px, py, plane = here
        if max(abs(px - goal[0]), abs(py - goal[1])) <= tolerance:
            return True
        imap = InstanceMap.load(plane)
        if imap is None:
            debug_log.log("inav", "no collision grid from the bridge -- cannot route")
            return False
        path = find_path(imap, (px, py), goal, avoid=avoid)
        if path is None:
            debug_log.log("inav", f"no route from {(px, py)} to {goal} (avoiding {len(avoid)} tiles)")
            return False
        # Farthest route point within one hop, so a long room crossing is 1-2 minimap clicks.
        # BFS steps one tile at a time, so the Chebyshev distance to path[idx] is always <= idx,
        # which is already capped at WALK_HOP_MAX_TILES -- no pull-back loop needed.
        idx = min(len(path) - 1, travel.WALK_HOP_MAX_TILES)
        target = path[idx]
        hops += 1
        if not travel._hop_to(ctx, target[0], target[1], plane, inst=True):
            return False
        after = player_inst()
        if after is not None and (after[0], after[1]) == (px, py) and len(path) > 1:
            # Did not move at all: the first step is refused by something the map cannot see
            # (another player, a live blocker). Same rule as travel.walk_to's reroute, and a
            # shut door cannot be the cause here -- instances handled here have none.
            reroutes += 1
            if reroutes > travel.WALK_MAX_REROUTES:
                debug_log.log("inav", f"still on {(px, py)} after {reroutes} reroutes -- giving up")
                return False
            avoid.add(path[1])
            debug_log.log("inav", f"hop to {target} did not move us; avoiding {path[1]} and re-routing")
    debug_log.log("inav", f"spent {WALK_MAX_HOPS} hops without reaching {goal}")
    return False
