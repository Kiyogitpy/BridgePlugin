import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import crossfloor
import transports

# These run against the REAL collision map and the vendored Shortest Path tables: both are local
# files, and the routes below were MEASURED with a spike on 2026-09-19 before this was written.


def _infos(route):
    return [s.step.object_info for s in route.links]


def test_the_stronghold_slayer_cave_is_one_link_from_the_slayer_ring_landing():
    """The table's origin tile (2430,3424) is the cave mouth itself, which our map marks solid.
    Without ORIGIN_TOLERANCE the flood never touches it and the search finds nothing -- the first
    spike failed exactly that way with the entrance two tiles from the start."""
    route = crossfloor.find_route((2431, 3422, 0), (2420, 9785, 0))
    assert route is not None
    assert _infos(route) == ["Enter Cave 26709"]
    assert isinstance(route.steps[0], crossfloor.Walk) and route.steps[0].tiles <= 5
    assert route.steps[-1].dest[2] == 0 and route.steps[-1].dest[1] > 9000


def test_the_slayer_tower_basement_is_the_ladder_and_lands_on_plane_three():
    route = crossfloor.find_route((3421, 3537, 0), (3434, 9938, 3))
    assert route is not None and _infos(route) == ["Climb-down Ladder 30191"]
    assert route.steps[-1].dest[2] == 3


def test_the_catacombs_are_entered_by_the_statue():
    route = crossfloor.find_route((1644, 3673, 0), (1686, 10062, 0))
    assert route is not None and _infos(route) == ["Investigate Statue 27785"]


def test_the_slayer_tower_top_floor_takes_two_staircases():
    route = crossfloor.find_route((3421, 3537, 0), (3432, 3540, 2))
    assert route is not None
    assert [l.step.dest[2] for l in route.links] == [1, 2]
    assert all(l.step.option == "Climb-up" for l in route.links)


def test_a_same_plane_trip_uses_no_links():
    route = crossfloor.find_route((2899, 3553, 0), (2864, 3590, 0))      # Burthorpe -> Death Plateau
    assert route is not None and route.links == []
    assert len(route.steps) == 1 and isinstance(route.steps[0], crossfloor.Walk)


def test_a_cluster_centre_goal_resolves_to_a_reachable_tile_nearby():
    """Site coordinates from research are cluster centres and are often not walkable tiles."""
    route = crossfloor.find_route((2431, 3422, 0), (2420, 9785, 0))
    dest = route.steps[-1].dest
    assert max(abs(dest[0] - 2420), abs(dest[1] - 9785)) <= crossfloor.GOAL_TOLERANCE


def test_a_start_on_a_fairy_rings_own_tile_still_routes():
    """MEASURED: a flood from the Zanaris ring's tile is one tile -- the ring is an object and the
    static map cannot tell an object from a wall -- but the player stands exactly there after the
    teleport. Without walkable_start every fairy-ring route was NO ROUTE in 0.00s."""
    ring = (2412, 4434, 0)
    assert crossfloor.walkable_start(ring) != ring
    assert max(abs(a - b) for a, b in zip(crossfloor.walkable_start(ring), ring)) <= crossfloor.START_TOLERANCE
    assert crossfloor.find_route(ring, (2400, 4440, 0)) is not None
    assert crossfloor.walkable_start((2431, 3422, 0)) == (2431, 3422, 0), "an ordinary tile is left alone"


def test_an_impossible_trip_is_none_and_fast():
    t0 = time.perf_counter()
    assert crossfloor.find_route((3221, 3218, 0), (3178, 2447, 0)) is None    # Lumbridge -> Great Conch
    assert time.perf_counter() - t0 < 8.0


def test_the_usable_link_set_never_contains_doors_or_the_wilderness_ditch():
    links = crossfloor.usable_links()
    assert links
    assert all(s.kind != transports.DOOR for s in links)
    assert not any(s.enters_wilderness_ditch for s in links)


def test_no_usable_link_carries_the_player_into_the_wilderness():
    """FINAL REVIEW: the ditch is not the only way in -- 13 other rows crossed the line."""
    links = crossfloor.usable_links(skill_levels={"Agility": 99, "Strength": 99, "Ranged": 99})
    crossing = [s for s in links if crossfloor.in_wilderness(s.dest) and not crossfloor.in_wilderness(s.origin)]
    assert crossing == []
    assert crossfloor.in_wilderness((3100, 3700, 0)) and crossfloor.in_wilderness((3100, 10100, 0))
    assert not crossfloor.in_wilderness((3412, 9932, 3)), "the Slayer Tower basement is east of it"
    assert not crossfloor.in_wilderness((3087, 3500, 0)), "Edgeville is south of the ditch"
    assert any(crossfloor.in_wilderness(s.origin) for s in links), "links that stay inside or LEAVE are kept"


def test_agility_shortcuts_only_join_the_link_set_when_the_level_is_proven():
    none = crossfloor.usable_links()
    assert not any(s.skills for s in none), "with no levels given, nothing skill-gated is usable"
    high = crossfloor.usable_links(skill_levels={"Agility": 99})
    assert any(s.skills.get("Agility") for s in high)
    off = crossfloor.usable_links(skill_levels={"Agility": 99}, use_agility=False)
    assert len(off) < len(high)


def test_max_links_zero_means_walking_only():
    assert crossfloor.find_route((2431, 3422, 0), (2420, 9785, 0), max_links=0) is None
    assert crossfloor.find_route((2899, 3553, 0), (2864, 3590, 0), max_links=0) is not None


def test_heuristic_folds_the_underground_offset_and_penalises_the_wrong_plane():
    surface = (2430, 3424, 0)
    cave_goal = (2420, 9785, 0)
    assert crossfloor.heuristic(surface, cave_goal) < 100, "y+6400 is the same place underground"
    assert crossfloor.heuristic((3421, 3537, 1), (3421, 3537, 0)) == crossfloor.PLANE_PENALTY


# --- 2026-09-20: requirements reach the search, and a link may land on an unroutable tile -------

BARBARIAN_OUTPOST = (2520, 3571, 0)
WATERFIENDS = (1750, 5350, 0)


def _objects(route):
    return [s.step.object_id for s in route.steps if isinstance(s, crossfloor.Link)]


def test_a_link_that_lands_on_an_isolated_tile_is_not_a_dead_end():
    """MEASURED: the whirlpool lands on (1768,5366,0), a tile whose flood is ONE tile, like 149
    of the usable links' destinations. The player really stands there and steps off (walk_across
    already does); the search has to plan from the walkable neighbour too, or every such link is
    a dead end -- which is why Waterfiends read NO ROUTE even with varbit 3759 supplied."""
    assert crossfloor.find_route(BARBARIAN_OUTPOST, WATERFIENDS) is None, "3759 unread must fail closed"
    route = crossfloor.find_route(BARBARIAN_OUTPOST, WATERFIENDS, varbit_values={3759: 3})
    assert route is not None and _objects(route) == [25274]
    assert not crossfloor.find_route(BARBARIAN_OUTPOST, WATERFIENDS, varbit_values={3759: 1})


def test_the_dusty_key_gate_routes_black_demons_without_agility():
    """The map does NOT bake this gate open (find_path across it is None) and the row is an
    'Open' between adjacent tiles, so it used to be dropped as a plain door. With the key proven
    by ITEM ID -- all an inventory read can prove -- it is the route for an account under 80
    Agility."""
    landing, site = (2884, 9798, 0), (2860, 9835, 0)          # the foot of Taverley's ladder
    assert crossfloor.find_route(landing, site, max_expansions=300) is None, "no key, no route"
    route = crossfloor.find_route(landing, site, owned_item_ids={1590}, max_expansions=300)
    assert route is not None and _objects(route) == [2623]
    # MEASURED before dominance pruning: 527 against 45 in a straight line took ~250 expansions,
    # 133 of 193 of them tiles already inside an earlier flood (the ladder alone has four rows
    # landing within two tiles of each other). With dominated nodes skipped, the DEFAULT budget
    # finds it -- from the ladder's foot and from the surface teleport.
    default = crossfloor.find_route(landing, site, owned_item_ids={1590})
    assert default is not None and default.cost == route.cost
    surface = crossfloor.find_route((2893, 3465, 0), site, owned_item_ids={1590})
    assert surface is not None and _objects(surface) == [16680, 2623]


def test_a_dominated_node_is_skipped_but_a_cheaper_landing_is_not():
    """Dominance is about COST, not place: a link that lands inside an already-flooded area more
    cheaply than walking there is exactly what a shortcut is, and must still be expanded."""
    taverley, demons = (2893, 3465, 0), (2852, 9784, 0)
    walked = crossfloor.find_route(taverley, demons, owned_item_ids={1590})
    piped = crossfloor.find_route(taverley, demons, skill_levels={"Agility": 77})
    assert piped is not None and 16509 in _objects(piped)
    assert walked is None or piped.cost < walked.cost


def test_usable_links_pass_item_ids_and_currency_through():
    toll = lambda links: [s for s in links if s.object_id == 2786 and s.currency]
    assert not toll(crossfloor.usable_links())
    assert toll(crossfloor.usable_links(currency_held={"Coins": 10}))


# --- 2026-09-20: two defects parked by the final review ---------------------------------------

def test_a_transport_that_goes_nowhere_is_not_offered():
    """The Mining Guild's ladders 30367 and 17386 have origin == dest in the vendored data, so
    legs.landed() can never be True for them: a leg that burns its whole budget and fails."""
    links = crossfloor.usable_links(skill_levels={"Mining": 99})
    assert not [s for s in links if s.origin == s.dest]
    assert [s for s in links if s.object_id == 30191], "ordinary ladders are untouched"


def test_the_wilderness_slayer_cave_counts_as_wilderness_and_the_slayer_tower_does_not():
    """The cave's stairs (3293,3749) land at (3406,10145): EAST of the Wilderness box, whose
    underground twin stops at x 3391. MEASURED: they were the only transports leaving the surface
    Wilderness for a tile the classifier called safe. The box cannot simply be widened -- the
    Slayer Tower basement (3412,9932,3) sits at the same x just south of it."""
    assert crossfloor.in_wilderness((3406, 10145, 0))
    stairs = [s for s in crossfloor.transports.load_links() if s.object_id == 40390 and s.dest == (3406, 10145, 0)]
    assert stairs
    assert not crossfloor.in_wilderness((3412, 9932, 3)), "Slayer Tower basement"
    tower = next(s for s in crossfloor.transports.load_links() if s.object_id == 30191)
    assert not crossfloor.enters_wilderness(tower)
    # walking out of Ferox-side safety into the cave must be refused like the ditch is
    outside = next((s for s in crossfloor.transports.load_links()
                    if s.dest == (3406, 10145, 0) and not crossfloor.in_wilderness(s.origin)), None)
    assert outside is None or crossfloor.enters_wilderness(outside)
