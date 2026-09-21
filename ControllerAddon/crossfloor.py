# crossfloor.py -- walking routes that change floor: worldmap's same-plane floods joined by the
# Shortest Path transports (ladders, stairs, cave mouths, trapdoors, agility shortcuts).
# PURE: no HTTP, no client state, no clicking. legs.walk_across executes what this returns.
#
# Added 2026-09-19 (slayer spec §8.1). Every walking primitive we had stays on one plane, and the
# slayer sites are mostly underground. The algorithm below was SPIKED against the real map before
# it was written, and two of its constants exist because the first spike failed without them:
#
# - ORIGIN_TOLERANCE. A transport's Origin is the tile Shortest Path stands on, read off ITS
#   collision map, and ours disagrees often enough to matter: MEASURED 2026-09-19, 141 usable
#   transports have an origin no flood on our map ever touches (Lumbridge's Ladder 16684 at
#   (3229,3212,1) is one). An origin counts as reached from any flooded tile within 2 of it, and
#   that tile travels with the Link as `approach` so the executor walks where the search meant.
#   (The first spike's NO ROUTE to the Stronghold Slayer Cave was blamed on this; that origin is
#   in fact reachable today, and the uninformed search running out of expansions is as likely.)
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
# 160 since 2026-09-20 (was 40): with dominated nodes skipped (_dominated) an expansion is a NEW
# area, not a re-flood, so the budget buys far more ground for the same money -- MEASURED an
# impossible trip costs about what it did at the old 40 without pruning (~1 s). 120 finds the
# dusty-key route from the ladder's foot; from the surface teleport it needs 160.
MAX_EXPANSIONS = 160
# _dominated only prunes within this of the earlier node; the outer 32 tiles of a flood are a
# rim band where a landing keeps its own flood. MEASURED: 128 and 160 give identical routes on
# all 27 report sites x 4 passes; 64 prunes too little to find the dusty-key route.
DOMINANCE_RADIUS = 128
LINK_COST = 6            # tiles-equivalent for the click, the animation and the load
GOAL_TOLERANCE = 12
ORIGIN_TOLERANCE = 2
START_TOLERANCE = 3      # a teleport can land us ON its own object (a fairy ring); see walkable_start
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


def in_wilderness(tile) -> bool:
    """The Wilderness's bounding box, surface and its y+6400 underground twin. Deliberately
    generous: a safe tile wrongly inside it (Ferox Enclave) costs one refused shortcut."""
    x, y = tile[0], tile[1]
    if 2944 <= x <= 3391 and (3525 <= y <= 3967 or 3525 + UNDERGROUND_DY <= y <= 3967 + UNDERGROUND_DY):
        return True
    # The Wilderness Slayer Cave lies EAST of that box: its stairs at (3293,3749) land on
    # (3406,10145). MEASURED 2026-09-20, they were the only transports leaving the surface
    # Wilderness for a tile this called safe. Its own box, not a wider one: the Slayer Tower
    # basement (3412,9932,3) sits at the same x just south and must stay usable.
    return 3328 <= x <= 3470 and 10040 <= y <= 10190


# The Ferox Enclave barriers -- the boundary between that safe enclave and the OPEN Wilderness,
# in both directions. MEASURED 2026-09-21: eight rows use them, and every destination is a tile
# in_wilderness calls Wilderness. They were allowed because in_wilderness deliberately calls
# FEROX ITSELF Wilderness (it sits inside the bounding box), so enters_wilderness' "already
# inside" exemption covered them. Both routines now PARK at Ferox during a break, and neither ever
# arrives or leaves on foot -- always a teleport -- so refusing these costs nothing and closes the
# only walking route from a place we deliberately stand into the Wilderness.
FEROX_BARRIER_IDS = (39652, 39653)


def enters_wilderness(step) -> bool:
    """The ditch by name, or ANY transport that starts outside the Wilderness and lands in it.
    FINAL REVIEW 2026-09-19: filtering the ditch alone left 13 usable links that cross the line
    (Trollheim's rocky handholds, a cave opening at (3294,3749), ...)."""
    if step.object_id in FEROX_BARRIER_IDS:
        return True
    return step.enters_wilderness_ditch or (in_wilderness(step.dest) and not in_wilderness(step.origin))


def requirement_ids():
    """(varbit ids, varplayer ids) that ANY link names -- 32 and 10 at the 2026-09-20 vendoring.
    What legs.requirement_values reads from the bridge once per journey, so a gate like the
    Ancient Cavern whirlpool (varbit 3759 > 1) can be PROVEN instead of failing closed."""
    steps = list(transports.load_links(include_doors=False)) + list(transports.load_agility_shortcuts())
    return ({v for s in steps for v, _, _ in s.varbits},
            {v for s in steps for v, _, _ in s.varplayers})


_pass_through_cache = []


def pass_through_doors() -> list:
    """DOOR rows that are really TRANSPORTS: the game carries the player TWO or more tiles across
    them, and the static map will not walk that gap.

    worldmap bakes ordinary doors open, which is why door rows are left out of the search. But
    MEASURED 2026-09-20, the map blocks 83 of the 291 door rows. 65 are ONE-tile doors, where
    'Open' only opens the leaf and does not carry you across -- travel.py's job, and offering
    them here would swap a working long route for a short one that fails at run time. The other
    18 are genuine pass-throughs (Tree Door, Pyramid entrance, Guild door): clicking them moves
    the player two tiles. Without them the Grand Tree's ground floor is a sealed 20-tile island
    and the gnome glider pilot on its top floor is unreachable. Computed once."""
    if not _pass_through_cache:
        found = []
        for step in transports.load_links():
            if step.kind != transports.DOOR:
                continue
            if max(abs(step.origin[0] - step.dest[0]), abs(step.origin[1] - step.dest[1])) < 2:
                continue
            if worldmap.find_path(step.origin[0], step.origin[1], step.origin[2],
                                  step.dest[0], step.dest[1], max_nodes=400) is None:
                found.append(step)
        _pass_through_cache.append(found)
    return list(_pass_through_cache[0])


def usable_links(skill_levels=None, completed_quests=None, varbit_values=None,
                 varplayer_values=None, owned_item_names=None, use_agility=True,
                 owned_item_ids=None, currency_held=None) -> list:
    """Every transport this account may take: no doors, nothing that ENTERS the Wilderness,
    requirements proven."""
    steps = list(transports.load_links(include_doors=False)) + pass_through_doors()
    if use_agility:
        steps += transports.load_agility_shortcuts()
    return [s for s in steps
            if not enters_wilderness(s)
            and s.origin != s.dest      # two Mining Guild ladder rows go nowhere: landed() could never be True
            and transports.usable(s, skill_levels=skill_levels, completed_quests=completed_quests,
                                  varbit_values=varbit_values, varplayer_values=varplayer_values,
                                  owned_item_names=owned_item_names, owned_item_ids=owned_item_ids,
                                  currency_held=currency_held)]


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


def walkable_start(start, toward=None):
    """`start`, or the nearest tile within START_TOLERANCE that the map can actually leave.

    MEASURED 2026-09-19 (tools/slayer_route_report.py): a flood from the Zanaris fairy ring
    (2412,4434) or the CJQ ring (3178,2447) is ONE tile. The ring is an object, the static map
    bakes an object's blocking into the same bits as a wall's, and yet the player really does
    stand on that tile after the teleport. Stepping off it is one ordinary walk click.

    Among equally near candidates the one nearest `toward` (the goal) wins: 137 usable transport
    destinations are isolated like this, and after a two-tile hop a floodable tile back on the
    ORIGIN side of the obstacle just crossed is as near as one on the far side."""
    start = tuple(start)
    if len(worldmap.reachable_from(start[0], start[1], start[2], radius=2, max_nodes=30)) > 1:
        return start
    best = None
    for dx in range(-START_TOLERANCE, START_TOLERANCE + 1):
        for dy in range(-START_TOLERANCE, START_TOLERANCE + 1):
            tile = (start[0] + dx, start[1] + dy, start[2])
            if len(worldmap.reachable_from(tile[0], tile[1], tile[2], radius=4, max_nodes=60)) < 9:
                continue
            # Nearness FIRST: a tile 3 away can be the far side of a wall, and this check sees only
            # local openness, not a connection back to the start. The goal only breaks ties within
            # one ring; legs.walk_across's bounce-back guard covers what that leaves (re-review).
            key = (max(abs(dx), abs(dy)), heuristic(tile, toward) if toward else 0, abs(dx) + abs(dy))
            if best is None or key < best[0]:
                best = (key, tile)
    return best[1] if best else start


def _dominated(floods, tile, cost) -> bool:
    """An earlier node already WALKS to `tile` for no more than `cost`, from close by.

    MEASURED 2026-09-20 on the dusty-key route: 133 of 193 expansions were tiles already inside an
    earlier flood -- Taverley's ladder alone is four rows landing within two tiles of each other,
    each re-flooded from scratch. Such a node can push nothing the earlier one did not already
    push at least as cheaply (walking distances obey the triangle inequality), so it is skipped
    and does not spend budget. COST decides, never place: a link that lands inside a flooded area
    more cheaply than walking there is what a shortcut IS, and is expanded as before.

    Only within DOMINANCE_RADIUS of the earlier node's own tile: floods stop at FLOOD_RADIUS and a
    link's landing is the only way the search reaches past that rim, so a node near the rim keeps
    its own flood even when the walk to it is cheaper."""
    for centre, base, flood in floods:
        if centre[2] != tile[2]:
            continue
        if max(abs(centre[0] - tile[0]), abs(centre[1] - tile[1])) > DOMINANCE_RADIUS:
            continue
        walk = flood.get((tile[0], tile[1]))
        if walk is not None and base + walk <= cost:
            return True
    return False


def find_route(start, goal, *, skill_levels=None, completed_quests=None, varbit_values=None,
               varplayer_values=None, owned_item_names=None, use_agility=True,
               max_links=MAX_LINKS, owned_item_ids=None, currency_held=None,
               max_expansions=None):
    """A cheap walk-and-transport route from `start` to within GOAL_TOLERANCE of `goal`, or None.

    Best-first under an expansion budget, NOT a shortest-path guarantee: PLANE_PENALTY is far
    above LINK_COST on purpose (it is what keeps the search off staircases that climb away from
    the goal), which makes the heuristic inadmissible. Between two valid routes it may return the
    slightly dearer one.

    `max_expansions` (default MAX_EXPANSIONS) lets a caller ask for a deeper search. The case
    that motivated it -- Taverley's ladder to the black demons through the dusty-key gate, 527
    against 45 in a straight line -- is found at the default since dominance pruning; every
    IMPOSSIBLE trip pays the whole budget, so raise it only for a route known to exist."""
    budget = MAX_EXPANSIONS if max_expansions is None else max_expansions
    index = _index(usable_links(skill_levels, completed_quests, varbit_values, varplayer_values,
                                owned_item_names, use_agility, owned_item_ids=owned_item_ids,
                                currency_held=currency_held)) if max_links > 0 else {}
    start = walkable_start(start, toward=goal)
    counter = itertools.count()
    goal_node = ("GOAL",)
    queue = [(heuristic(start, goal), next(counter), 0, tuple(start), ())]
    seen = set()
    floods = []
    expansions = 0

    while queue:
        _, _, cost, tile, steps = heapq.heappop(queue)
        if tile == goal_node:
            return Route(steps, cost)
        if tile in seen:
            continue
        seen.add(tile)

        # A link may LAND on a tile the static map cannot leave: MEASURED 2026-09-20, 149 usable
        # links do (the whirlpool's (1768,5366,0) floods to ONE tile), and every one of them was a
        # dead end here. The player really stands there and steps off -- legs.walk_across already
        # does -- so plan from the same walkable neighbour it will step to.
        if steps:
            foothold = walkable_start(tile, toward=goal)
            cost += max(abs(foothold[0] - tile[0]), abs(foothold[1] - tile[1]))
            tile = foothold
        if _dominated(floods, tile, cost):
            continue
        expansions += 1
        if expansions > budget:
            return None
        flood = worldmap.reachable_from(tile[0], tile[1], tile[2],
                                        radius=FLOOD_RADIUS, max_nodes=FLOOD_NODES)
        floods.append((tile, cost, flood))
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
