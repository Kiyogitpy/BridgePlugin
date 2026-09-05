"""instance_nav: BFS over a /collision?all=true grid in instance space (Gauntlet sub-project 1,
docs/superpowers/specs/2026-09-05-gauntlet-instance-plumbing-design.md)."""
import pytest

import controller
import instance_nav
from travel import (BLOCK_MOVEMENT_EAST, BLOCK_MOVEMENT_NORTH, BLOCK_MOVEMENT_OBJECT,
                    BLOCK_MOVEMENT_SOUTH, BLOCK_MOVEMENT_WEST)

BASE_X, BASE_Y = 13056, 5632


def make_map(size=8, walls=None, blocked=None):
    """A size x size grid at BASE_X/BASE_Y. `walls` is {(x, y): flags} in GRID coords, `blocked`
    a set of grid tiles carrying BLOCK_MOVEMENT_OBJECT."""
    flags = [0] * (size * size)
    for (gx, gy), f in (walls or {}).items():
        flags[gy * size + gx] |= f
    for gx, gy in (blocked or ()):
        flags[gy * size + gx] |= BLOCK_MOVEMENT_OBJECT
    return instance_nav.InstanceMap(BASE_X, BASE_Y, 1, size, flags)


def P(gx, gy):
    return (BASE_X + gx, BASE_Y + gy)


def test_flags_at_maps_instance_space_onto_the_grid():
    imap = make_map(walls={(2, 3): 7})
    assert imap.flags_at(*P(2, 3)) == 7
    assert imap.flags_at(*P(0, 0)) == 0
    assert imap.flags_at(BASE_X - 1, BASE_Y) is None
    assert imap.flags_at(BASE_X + 8, BASE_Y) is None


def test_straight_corridor():
    imap = make_map()
    path = instance_nav.find_path(imap, P(0, 0), P(5, 0))
    assert path[0] == P(0, 0) and path[-1] == P(5, 0)
    assert len(path) == 6


def test_one_sided_wall_edge_blocks_both_directions():
    # A wall on the EAST edge of (2,0), recorded only on that tile (a one-sided grid).
    imap = make_map(walls={(2, 0): BLOCK_MOVEMENT_EAST})
    assert not imap.edge_open(*P(2, 0), 1, 0)
    assert not imap.edge_open(*P(3, 0), -1, 0)
    assert imap.edge_open(*P(1, 0), 1, 0)


def test_diagonal_needs_both_corner_routes():
    # Moving (0,0)->(1,1): both (1,0) and (0,1) must be standable with all four edges open.
    imap = make_map(blocked={(1, 0)})
    assert not imap.step_ok(*P(0, 0), 1, 1)
    imap = make_map(walls={(0, 0): BLOCK_MOVEMENT_NORTH})
    assert not imap.step_ok(*P(0, 0), 1, 1)
    assert imap.step_ok(*P(0, 0), 1, 0)
    imap = make_map()
    assert imap.step_ok(*P(0, 0), 1, 1)


def test_routes_around_a_wall_line():
    # Vertical wall on the east edge of x=3 for y in 0..5; the gap is at y=6.
    walls = {(3, y): BLOCK_MOVEMENT_EAST for y in range(6)}
    imap = make_map(walls=walls)
    path = instance_nav.find_path(imap, P(0, 0), P(7, 0))
    assert path is not None
    assert any(y == BASE_Y + 6 for _, y in path)
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        assert max(abs(x2 - x1), abs(y2 - y1)) == 1


def test_unreachable_returns_none_and_avoid_excludes_tiles():
    walls = {(3, y): BLOCK_MOVEMENT_EAST for y in range(8)}
    assert instance_nav.find_path(make_map(walls=walls), P(0, 0), P(7, 0)) is None
    imap = make_map()
    direct = instance_nav.find_path(imap, P(0, 0), P(2, 0))
    assert P(1, 0) in direct
    detour = instance_nav.find_path(imap, P(0, 0), P(2, 0), avoid={P(1, 0)})
    assert detour is not None and P(1, 0) not in detour


def test_goal_adjacent_stops_beside_a_blocked_goal():
    imap = make_map(blocked={(4, 4)})
    assert instance_nav.find_path(imap, P(0, 0), P(4, 4)) is None
    path = instance_nav.find_path(imap, P(0, 0), P(4, 4), goal_adjacent=True)
    assert path is not None
    end = path[-1]
    assert abs(end[0] - P(4, 4)[0]) + abs(end[1] - P(4, 4)[1]) == 1


def test_load_reads_the_bridge_grid(monkeypatch):
    monkeypatch.setattr(controller, "get_collision_grid",
                        lambda plane=None: {"baseX": 1, "baseY": 2, "plane": 1, "size": 2, "flags": [0, 5, 0, 0]})
    imap = instance_nav.InstanceMap.load(1)
    assert imap.flags_at(2, 2) == 5
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: None)
    assert instance_nav.InstanceMap.load(1) is None
