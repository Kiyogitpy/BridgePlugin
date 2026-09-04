"""Hermetic tests for ocean_path.py (added 2026-09-04). /tiles is stubbed with a small grid."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import controller
import ocean_path


def _grid(size, blocked):
    return [{"x": x, "y": y, "plane": 0, "blocked": (x, y) in blocked}
            for x in range(size) for y in range(size)]


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(controller, "get_tiles", lambda **kw: [])


def _pt(x, y):
    return {"x": x, "y": y, "plane": 0}


def _segment_clear(a, b, blocked, clearance):
    """Every tile a straight segment touches stays `clearance` tiles away from every blocked one."""
    steps = max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 4 + 1
    for i in range(steps + 1):
        t = i / steps
        x = int(round(a[0] + (b[0] - a[0]) * t))
        y = int(round(a[1] + (b[1] - a[1]) * t))
        for bx, by in blocked:
            if max(abs(x - bx), abs(y - by)) <= clearance and (x, y) not in (a, b):
                return False
    return True


def test_open_water_route_is_a_single_waypoint(monkeypatch):
    blocked = {(0, 0)}   # something has to be blocked or the planner declines to route at all
    monkeypatch.setattr(controller, "get_tiles", lambda **kw: _grid(30, blocked))
    route = ocean_path.find_ocean_route(_pt(4, 4), _pt(25, 11))   # slope 1/3: zig-zag on the grid
    assert route == [_pt(25, 11)], route


def test_route_around_a_wall_keeps_only_the_corners_and_stays_clear(monkeypatch):
    size = 40
    blocked = {(20, y) for y in range(0, 26)}   # a wall with open water above y=25
    monkeypatch.setattr(controller, "get_tiles", lambda **kw: _grid(size, blocked))
    start, target = _pt(5, 10), _pt(35, 10)
    route = ocean_path.find_ocean_route(start, target)
    assert route is not None and route[-1] == target
    assert len(route) <= 4, f"line-of-sight smoothing should leave a handful of corners, got {len(route)}"
    clearance = ocean_path._FALLBACK_CLEARANCE_TILES
    pts = [(start["x"], start["y"])] + [(w["x"], w["y"]) for w in route]
    for a, b in zip(pts, pts[1:]):
        assert _segment_clear(a, b, blocked, clearance), (a, b)
    assert max(y for _, y in pts) >= 26 + clearance


def test_smoothing_never_cuts_through_the_clearance_zone():
    passable = {(x, y) for x in range(10) for y in range(10)} - {(5, 4), (5, 5), (5, 6)}
    path = [(2, 5), (3, 6), (4, 7), (5, 8), (6, 7), (7, 6), (8, 5)]
    smoothed = ocean_path._smooth_path(path, passable)
    assert smoothed[0] == path[0] and smoothed[-1] == path[-1]
    assert len(smoothed) == 3 and smoothed[1] == (5, 8)


def test_smoothing_collapses_a_clear_zigzag():
    passable = {(x, y) for x in range(12) for y in range(12)}
    path = [(0, 0), (2, 1), (3, 1), (5, 2), (6, 2), (8, 3), (9, 3), (11, 4)]
    assert ocean_path._smooth_path(path, passable) == [(0, 0), (11, 4)]
