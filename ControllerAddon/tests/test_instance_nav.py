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


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = type("S", (), {"data": {}})()


def _fake_world(monkeypatch, imap, start, hop_effect, player_plane=None):
    """Stub the bridge: the player stands at `start` (instance space) and every _hop_to call
    moves it per hop_effect(target) -> new position. `player_plane` lets a test give /player a
    different (TEMPLATE) plane than the grid's (LIVE) one -- see C1: walk_to must route and hop
    on the grid's plane, never /player's. Returns (hops, grid_plane_calls, hop_planes): the
    (x, y) tuples clicked, the `plane` kwarg passed to get_collision_grid on each call, and the
    plane passed to each _hop_to call."""
    pos = {"p": start}
    grid_plane_calls = []

    def fake_grid(plane=None):
        grid_plane_calls.append(plane)
        return {"baseX": imap.base_x, "baseY": imap.base_y, "plane": imap.plane,
                "size": imap.size, "flags": imap.flags}

    monkeypatch.setattr(controller, "get_collision_grid", fake_grid)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"instX": pos["p"][0], "instY": pos["p"][1],
                                 "plane": imap.plane if player_plane is None else player_plane,
                                 "x": -1, "y": -1})
    hops = []
    hop_planes = []

    def fake_hop(ctx, x, y, plane, inst=False):
        assert inst is True
        hops.append((x, y))
        hop_planes.append(plane)
        pos["p"] = hop_effect((x, y))
        return True

    monkeypatch.setattr(instance_nav.travel, "_hop_to", fake_hop)
    return hops, grid_plane_calls, hop_planes


def test_walk_to_hops_along_the_route_and_arrives(monkeypatch):
    imap = make_map(size=40)
    hops, grid_calls, _hop_planes = _fake_world(monkeypatch, imap, P(0, 0), lambda t: t)
    assert instance_nav.walk_to(_Ctx(), *P(30, 0))
    assert hops and hops[-1] == P(30, 0)
    assert all(max(abs(b[0] - a[0]), abs(b[1] - a[1])) <= instance_nav.travel.WALK_HOP_MAX_TILES
               for a, b in zip([P(0, 0)] + hops, hops))
    # C1: InstanceMap.load() must be called with NO plane -- CollisionProvider then defaults to
    # the LIVE plane (view.getWorldLocation().getPlane()), never /player's TEMPLATE plane.
    assert grid_calls and all(p is None for p in grid_calls)


def test_walk_to_uses_the_grids_plane_not_players_template_plane(monkeypatch):
    # /player's plane is WorldPoint.fromLocalInstance's TEMPLATE plane; the collision grid (built
    # from the LIVE WorldView) can legitimately disagree. walk_to must hop on the grid's plane.
    imap = make_map(size=8)
    hops, _grid_calls, hop_planes = _fake_world(monkeypatch, imap, P(0, 0), lambda t: t,
                                                player_plane=2)
    assert instance_nav.walk_to(_Ctx(), *P(5, 0), tolerance=0)
    assert hops
    assert hop_planes and all(p == imap.plane for p in hop_planes)
    assert imap.plane != 2


def test_walk_to_returns_false_without_a_map_or_route(monkeypatch):
    imap = make_map(walls={(3, y): BLOCK_MOVEMENT_EAST for y in range(8)})
    hops, _grid_calls, _hop_planes = _fake_world(monkeypatch, imap, P(0, 0), lambda t: t)
    assert not instance_nav.walk_to(_Ctx(), *P(7, 0))
    assert hops == []
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: None)
    assert not instance_nav.walk_to(_Ctx(), *P(7, 0))


def test_walk_to_reroutes_around_a_tile_that_refuses(monkeypatch):
    # The map says (1,0) is fine; the "game" never moves us when we aim through it.
    imap = make_map()
    refused = P(1, 0)

    def effect(target):
        return P(0, 0) if target == refused else target

    hops, _grid_calls, _hop_planes = _fake_world(monkeypatch, imap, P(0, 0), effect)
    monkeypatch.setattr(instance_nav.travel, "WALK_HOP_MAX_TILES", 1)
    assert instance_nav.walk_to(_Ctx(), *P(2, 0), tolerance=0)
    assert refused in hops                       # tried it once
    assert hops[-1] == P(2, 0)


def test_walk_to_gives_up_after_the_hop_ceiling(monkeypatch):
    imap = make_map(size=40)
    hops, _grid_calls, _hop_planes = _fake_world(monkeypatch, imap, P(0, 0), lambda t: P(0, 0))
    monkeypatch.setattr(instance_nav, "WALK_MAX_HOPS", 3)
    assert not instance_nav.walk_to(_Ctx(), *P(30, 0))
    assert len(hops) <= 3 + instance_nav.travel.WALK_MAX_REROUTES
