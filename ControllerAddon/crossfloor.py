# crossfloor.py -- walking routes that change floor: worldmap's same-plane floods joined by the
# Shortest Path transports (ladders, stairs, cave mouths, trapdoors, agility shortcuts).
# PURE: no HTTP, no client state, no clicking. legs.walk_across executes what this returns.
#
# Added 2026-09-19 (slayer spec §8.1). Every walking primitive we had stays on one plane, and the
# slayer sites are mostly underground. The algorithm below was SPIKED against the real map before
# it was written, and two of its constants exist because the first spike failed without them:
#
# - ORIGIN_TOLERANCE. A transport's Origin is the tile Shortest Path stands on, read off ITS
#   collision map. On OURS the Stronghold Slayer Cave's origin (2430,3424) is the cave mouth and
#   reads solid, so a flood never touches it: the first spike found NO ROUTE to a cave two tiles
#   from the start. An origin counts as reached from any flooded tile within 2 of it.
# - GOAL_TOLERANCE. Site coordinates from research are cluster centres, often not walkable.
#
# A* rather than Dijkstra because uninformed search burns its expansions on the nearest stairs
# (Lumbridge castle, the Gnome Stronghold's trees) before it reaches the one link that matters.
# The heuristic folds OSRS's dungeon convention -- a dungeon is drawn at (x, y + 6400) on plane 0
# -- so the dark hole's landing reads as "next to the swamp-cave goal", and penalises a plane
# mismatch so ladders that climb AWAY from the goal sort last.
#
# Each expansion is ONE bounded worldmap.reachable_from flood, which answers "how far is every
# link origin and the goal from here" at once (the same trick banker's return planner uses).
# MEASURED: hub routes 0.06-0.5s, a hopeless one ~2s.
#
# Known limits, on purpose: no-data reads PASSABLE on the static map, so a flood can leak through
# a coverage hole -- the radius and node caps bound that, and tools/slayer_route_report.py prints
# every chosen site's route for a human to sanity-check once. Quest-gated links stay unusable
# until something supplies completed quests (requirements fail closed).

import heapq
import itertools

import transports
import worldmap

FLOOD_RADIUS = 160
FLOOD_NODES = 40_000
MAX_LINKS = 4
MAX_EXPANSIONS = 40
LINK_COST = 6            # tiles-equivalent for the click, the animation and the load
GOAL_TOLERANCE = 12
ORIGIN_TOLERANCE = 2
PLANE_PENALTY = 40
UNDERGROUND_DY = 6400
_CELL = 64


class Walk:
    __slots__ = ("dest", "tiles")

    def __init__(self, dest, tiles):
        self.dest = dest
        self.tiles = tiles

    def __repr__(self):
        return f"Walk({self.tiles} tiles -> {self.dest})"


class Link:
    __slots__ = ("step", "approach")

    def __init__(self, step, approach):
        self.step = step
        self.approach = approach

    def __repr__(self):
        return f"Link({self.step.object_info!r} {self.step.origin} -> {self.step.dest})"


class Route:
    __slots__ = ("steps", "cost")

    def __init__(self, steps, cost):
        self.steps = list(steps)
        self.cost = cost

    @property
    def links(self):
        return [s for s in self.steps if isinstance(s, Link)]

    def __repr__(self):
        return f"Route(cost={self.cost}, {self.steps})"


def heuristic(tile, goal) -> int:
    dx = abs(tile[0] - goal[0])
    dy = min(abs(tile[1] - goal[1]),
             abs(tile[1] + UNDERGROUND_DY - goal[1]),
             abs(tile[1] - UNDERGROUND_DY - goal[1]))
    return max(dx, dy) + PLANE_PENALTY * abs(tile[2] - goal[2])


def usable_links(skill_levels=None, completed_quests=None, varbit_values=None,
                 varplayer_values=None, owned_item_names=None, use_agility=True) -> list:
    """Every transport this account may take: no doors, no Wilderness ditch, requirements proven."""
    steps = list(transports.load_links(include_doors=False))
    if use_agility:
        steps += transports.load_agility_shortcuts()
    return [s for s in steps
            if not s.enters_wilderness_ditch and s.kind != transports.DOOR
            and transports.usable(s, skill_levels=skill_levels, completed_quests=completed_quests,
                                  varbit_values=varbit_values, varplayer_values=varplayer_values,
                                  owned_item_names=owned_item_names)]


def _index(links):
    index = {}
    for s in links:
        index.setdefault((s.origin[2], s.origin[0] // _CELL, s.origin[1] // _CELL), []).append(s)
    return index


def _candidates(index, tile):
    x, y, plane = tile
    out = []
    for cx in range((x - FLOOD_RADIUS) // _CELL, (x + FLOOD_RADIUS) // _CELL + 1):
        for cy in range((y - FLOOD_RADIUS) // _CELL, (y + FLOOD_RADIUS) // _CELL + 1):
            out.extend(index.get((plane, cx, cy), ()))
    return out


def _nearest(flood, xy, tolerance):
    """(walk distance, tile) for `xy` itself or the closest flooded tile within `tolerance`."""
    d = flood.get(xy)
    if d is not None:
        return d, xy
    best = None
    for dx in range(-tolerance, tolerance + 1):
        for dy in range(-tolerance, tolerance + 1):
            tile = (xy[0] + dx, xy[1] + dy)
            near = flood.get(tile)
            if near is None:
                continue
            key = (max(abs(dx), abs(dy)), near)
            if best is None or key < best[0]:
                best = (key, near, tile)
    return (best[1] + 1, best[2]) if best else (None, None)


def find_route(start, goal, *, skill_levels=None, completed_quests=None, varbit_values=None,
               varplayer_values=None, owned_item_names=None, use_agility=True,
               max_links=MAX_LINKS):
    """Cheapest walk-and-transport route from `start` to within GOAL_TOLERANCE of `goal`, or None."""
    index = _index(usable_links(skill_levels, completed_quests, varbit_values, varplayer_values,
                                owned_item_names, use_agility)) if max_links > 0 else {}
    counter = itertools.count()
    goal_node = ("GOAL",)
    queue = [(heuristic(start, goal), next(counter), 0, tuple(start), ())]
    seen = set()
    expansions = 0

    while queue:
        _, _, cost, tile, steps = heapq.heappop(queue)
        if tile == goal_node:
            return Route(steps, cost)
        if tile in seen:
            continue
        seen.add(tile)
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            return None

        flood = worldmap.reachable_from(tile[0], tile[1], tile[2],
                                        radius=FLOOD_RADIUS, max_nodes=FLOOD_NODES)
        if tile[2] == goal[2]:
            d, at = _nearest(flood, (goal[0], goal[1]), GOAL_TOLERANCE)
            if d is not None:
                total = cost + d
                heapq.heappush(queue, (total, next(counter), total, goal_node,
                                       steps + (Walk((at[0], at[1], goal[2]), d),)))

        if sum(1 for s in steps if isinstance(s, Link)) >= max_links:
            continue

        best = {}
        for step in _candidates(index, tile):
            d, at = _nearest(flood, (step.origin[0], step.origin[1]), ORIGIN_TOLERANCE)
            if d is None:
                continue
            key = (step.object_info, step.dest)
            if key not in best or d < best[key][0]:
                best[key] = (d, at, step)
        for d, at, step in best.values():
            if step.dest in seen:
                continue
            g = cost + d + LINK_COST
            approach = (at[0], at[1], tile[2])
            heapq.heappush(queue, (g + heuristic(step.dest, goal), next(counter), g, step.dest,
                                   steps + (Walk(approach, d), Link(step, approach))))
    return None
