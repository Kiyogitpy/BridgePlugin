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


def test_an_impossible_trip_is_none_and_fast():
    t0 = time.perf_counter()
    assert crossfloor.find_route((3221, 3218, 0), (3178, 2447, 0)) is None    # Lumbridge -> Great Conch
    assert time.perf_counter() - t0 < 8.0


def test_the_usable_link_set_never_contains_doors_or_the_wilderness_ditch():
    links = crossfloor.usable_links()
    assert links
    assert all(s.kind != transports.DOOR for s in links)
    assert not any(s.enters_wilderness_ditch for s in links)


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
