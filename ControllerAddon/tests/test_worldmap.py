"""Tests for worldmap.py, the global collision map and pathfinder.

Every number here was MEASURED against the real asset on 2026-08-29 and is reproduced in
docs/superpowers/specs/2026-08-29-global-collision-pathfinding-design.md. This suite touches
no bridge and no serial port -- it is the first pathing code in this repo that can be tested
without a running client.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import worldmap


# MEASURED 2026-08-29. Doubles as a corruption check on the 3.6 MB asset: any re-dump or a
# truncated checkout changes these immediately. Planes 1-3 carrying MORE bits than plane 0 is
# not a bug -- upper planes are mostly open sky, and no data defaults to passable.
EXPECTED_BITS_PER_PLANE = {0: 10208962, 1: 22268102, 2: 24304088, 3: 25406620}

CARDINALS = ((0, 1), (0, -1), (1, 0), (-1, 0))


def test_asset_matches_the_measured_bit_counts():
    assert worldmap.bits_per_plane() == EXPECTED_BITS_PER_PLANE


def test_upper_planes_carry_real_wall_geometry():
    """ROSS's upper floor, the geometry measured live 2026-08-29: the player at (2617,3317)
    is walled off to the east and south, which is what Shortest Path cannot tell us at all."""
    assert worldmap.north(2617, 3317, 1) is True
    assert worldmap.east(2617, 3317, 1) is False
    assert worldmap.south(2617, 3317, 1) is False
    assert worldmap.west(2617, 3317, 1) is True


def test_no_data_defaults_to_passable():
    """Open sky above an outdoor tile. This is a KNOWN LIMITATION, pinned so nobody "fixes" a
    route that leaks through the void without realising the map allows it by design."""
    for plane in (1, 2):
        for dx, dy in CARDINALS:
            assert worldmap.passable(3213, 3424, plane, dx, dy) is True


@pytest.mark.parametrize("x,y", [(3037, 3347), (1746, 3534)])
def test_doors_are_baked_open(x, y):
    """Falador's and Barbara's doors. Deliberate -- CacheMapDumper's Exclusion enum does this.
    The whole model is: route straight through the door, then open it when you reach it."""
    for dx, dy in CARDINALS:
        assert worldmap.passable(x, y, 0, dx, dy) is True


@pytest.mark.parametrize("x,y", [(2617, 3318), (1749, 3536)])
def test_furniture_tiles_are_solid(x, y):
    """ROSS's Broken Range and Barbara's hotspot. A hotspot's own tile can never be routed TO,
    which is why find_path grows a goal_adjacent mode in Task 2."""
    for dx, dy in CARDINALS:
        assert worldmap.passable(x, y, 0, dx, dy) is False


def test_south_and_west_are_read_off_the_neighbour():
    """The map stores only NORTH and EAST per tile; the other two are the neighbour's bits."""
    assert worldmap.south(2617, 3317, 1) == worldmap.north(2617, 3316, 1)
    assert worldmap.west(2617, 3317, 1) == worldmap.east(2616, 3317, 1)


def test_indexer_packs_the_documented_layout():
    assert worldmap.Indexer.packed(0, 0, 0) == 0
    assert worldmap.Indexer.packed(1, 0, 0) == 1
    assert worldmap.Indexer.packed(0, 1, 0) == 1 << 14
    assert worldmap.Indexer.packed(0, 0, 1) == 1 << 28
    assert worldmap.Indexer.SET_MEANS_PASSABLE is True


import time


# MEASURED 2026-08-29. BFS on unit-weight edges gives a unique shortest LENGTH regardless of
# neighbour visiting order, so these are stable even if the particular tiles chosen differ.
def test_routes_upstairs_where_shortest_path_returns_nothing():
    path = worldmap.find_path(2617, 3317, 1, 2617, 3316)
    assert path is not None
    assert len(path) == 4
    assert path[0] == (2617, 3317)
    assert path[-1] == (2617, 3316)


def test_routes_from_outside_a_house_to_inside_it():
    """Straight through Ross's front door, which the map treats as open."""
    path = worldmap.find_path(2614, 3319, 0, 2616, 3318)
    assert path is not None and len(path) == 30


def test_routes_across_a_city():
    path = worldmap.find_path(3013, 3355, 0, 3037, 3346)
    assert path is not None and len(path) == 42


def test_routes_between_cities():
    path = worldmap.find_path(2965, 3379, 0, 3213, 3424)
    assert path is not None and len(path) == 314


def test_a_cross_city_route_stays_fast_enough_to_run_every_hop():
    started = time.time()
    worldmap.find_path(2965, 3379, 0, 3213, 3424)
    assert time.time() - started < 1.0


def test_a_furniture_goal_has_no_exact_route_but_an_adjacent_one():
    """ROSS's Broken Range at (2617,3318) is furniture, so nothing can route ONTO it. This is
    what travel._routable_goal used to paper over; goal_adjacent replaces it."""
    assert worldmap.find_path(2614, 3319, 0, 2617, 3318) is None
    adjacent = worldmap.find_path(2614, 3319, 0, 2617, 3318, goal_adjacent=True)
    assert adjacent is not None and len(adjacent) == 4


def test_goal_adjacent_succeeds_immediately_when_already_beside_the_goal():
    """Barbara's, the live geometry from 2026-08-29: the player stood one tile from a hotspot
    it could not reach. Being adjacent is the routing answer; whether the EDGE is open is
    travel.can_reach's question, not this one's."""
    path = worldmap.find_path(1750, 3536, 0, 1749, 3536, goal_adjacent=True)
    assert path == [(1750, 3536)]


def test_a_walled_off_goal_returns_none_rather_than_a_wrong_route():
    assert worldmap.find_path(1750, 3536, 0, 1749, 3536) is None


def test_the_node_cap_bounds_a_hopeless_search():
    assert worldmap.find_path(2965, 3379, 0, 3213, 3424, max_nodes=100) is None


def test_reachable_from_returns_distances_including_the_start():
    reach = worldmap.reachable_from(2617, 3317, 1)
    assert reach[(2617, 3317)] == 0
    assert (2617, 3316) in reach
    assert reach[(2617, 3316)] == 3
    # East and south are walled off at this tile; nothing beyond them is in the fill.
    assert (2618, 3317) not in reach


def test_reachable_from_respects_its_radius():
    reach = worldmap.reachable_from(3213, 3424, 0, radius=3, max_nodes=10_000)
    assert reach
    for x, y in reach:
        assert max(abs(x - 3213), abs(y - 3424)) <= 3
