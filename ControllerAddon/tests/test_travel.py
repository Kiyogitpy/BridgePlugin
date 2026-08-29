"""Tests for travel.py's route-based walker.

walk_to routes through worldmap.find_path (a BFS over the static, pre-baked, world-wide
collision map) rather than the separately-installed Shortest Path plugin this used to depend
on. Covered here: route_to's plane/scene-escape guards, hop distance/index selection off a
route, detecting a shut door on the route against LIVE collision (blocked_crossing_on_route /
first_door_on_route), opening it and confirming (open_door_toward), reachability (reachable_set
/ can_reach) for callers that need to know before they walk, and the SHIFT-click / minimap-vs-
viewport rules for an individual hop. The walking machinery underneath (_hop_to, click_entity,
the Arduino) is stubbed out where a test isn't exercising it directly; none of these tests touch
a bridge or a serial port.
"""

import os
import sys

import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import travel


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr(travel.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def no_live_bridge(monkeypatch):
    """Keep the suite hermetic. get_collision reaches the RUNNING CLIENT over HTTP when a test
    does not stub it, so results depended on where the character happened to be standing --
    MEASURED 2026-08-29: two door tests passed alone and failed in a full run, and the same test
    flipped between runs as the live player moved between houses.

    The default is the documented "client reports nothing" answer, which is also the safe one:
    every consumer of get_collision (route_to, _collision_flags, can_reach, ...) reads an empty
    result as "cannot tell" rather than "blocked". Tests that want a populated grid override it,
    exactly as several already do."""
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=2: [])


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = type("S", (), {"data": {}})()


# --- walk_to_exact_tile's non-blocking mode (2026-08-23) -----------------------------
#
# Added so a routine can click its target while still walking, which is what a human does --
# MEASURED at 0.6-1.0s between the walk click and the object click, four times out of four, in
# recordings/2026-08-23_194302_golem-carving. Blocking for exact arrival first is safe but
# structurally slower than a human, and the block is what makes an over-tight confirmation
# timeout destructive rather than merely slow. See CLAUDE.md's "Sub-tick data and clicking while
# moving".


def _stub_walk_click(monkeypatch, player_tile, arrival_tile=None):
    """Stubs the bridge/serial calls walk_to_exact_tile makes. Returns the get_player call log;
    `arrival_tile` (if given) is what get_player reports from the 2nd call on, simulating arrival."""
    calls = []
    seq = {"n": 0}

    def fake_get_player():
        seq["n"] += 1
        calls.append(seq["n"])
        if arrival_tile is not None and seq["n"] > 1:
            return dict(arrival_tile)
        return dict(player_tile)

    monkeypatch.setattr(controller, "get_player", fake_get_player)
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 100, "screenY": 100})
    monkeypatch.setattr(controller, "get_ground_walk_screen_pos", lambda x, y, p: (100, 100))
    monkeypatch.setattr(controller, "key_down", lambda ser, k: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(travel.time, "sleep",
                        lambda d: pytest.fail(f"non-blocking walk must not sleep (slept {d}s)"))
    return calls


class _Ctx:
    """Minimal stand-in for controller.Context -- walk_to_exact_tile only ever reads ctx.ser."""
    ser = None


def test_walk_to_exact_tile_can_fire_and_forget_without_waiting_for_arrival(monkeypatch):
    """The whole point: return as soon as the click is out, so the caller can issue its target
    click while the player is still walking."""
    calls = _stub_walk_click(monkeypatch, {"x": 10, "y": 10, "plane": 0})

    assert travel.walk_to_exact_tile(_Ctx(), 12, 12, 0, wait_for_arrival=False) is True
    assert len(calls) == 1, \
        "only the already-standing-there pre-check may read the player; no arrival poll"


def test_the_non_blocking_walk_still_early_outs_when_already_standing_there(monkeypatch):
    """The pre-check is the one get_player read that must survive -- clicking a walk to the tile
    you are already on is wasted motion whether or not the caller intends to block."""
    clicks = []
    _stub_walk_click(monkeypatch, {"x": 12, "y": 12, "plane": 0})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(e) or True)

    assert travel.walk_to_exact_tile(None, 12, 12, 0, wait_for_arrival=False) is True
    assert clicks == [], "no click may go out when the player is already on the target tile"


def test_the_non_blocking_walk_still_reports_an_unprojectable_tile(monkeypatch):
    """False still means "can't be clicked at all", which is what the caller's miss counter and
    hop-closer fallback key off -- that contract must not change with the new mode."""
    _stub_walk_click(monkeypatch, {"x": 10, "y": 10, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": False})

    assert travel.walk_to_exact_tile(None, 12, 12, 0, wait_for_arrival=False) is False


def test_walk_to_exact_tile_uses_the_ground_tile_refresh_target_factory(monkeypatch):
    """Regression: walk_to_exact_tile used to hand-roll its own lambda duplicating
    ground_tile_refresh_target's body instead of calling the factory. Assert the factory itself
    gets called so a future edit can't silently reintroduce the duplicate."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                         lambda x, y, plane: {"onScreen": True})
    monkeypatch.setattr(controller, "key_down", lambda ser, key: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, key: None)

    factory_calls = []
    real_factory = controller.ground_tile_refresh_target
    def _spy_factory(x, y, plane):
        factory_calls.append((x, y, plane))
        return real_factory(x, y, plane)
    monkeypatch.setattr(controller, "ground_tile_refresh_target", _spy_factory)

    click_kwargs = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, e, **kw: click_kwargs.append(kw) or True)

    travel.walk_to_exact_tile(_Ctx(), 12, 12, 0, wait_for_arrival=False)
    assert factory_calls == [(12, 12, 0)]
    assert click_kwargs and click_kwargs[0].get("refresh_target") is not None


# --- hop distance -------------------------------------------------------------------

def test_hop_distance_stays_inside_the_measured_minimap_range():
    """MEASURED live 2026-08-28 from (3037,3345): /minimap/walk reported onMinimap out to 28
    tiles in all eight directions, failing first on the DIAGONALS at 30 (a corner is sqrt(2)x
    further from the centre than a cardinal at the same tile distance). So the safe bound for a
    hop that must work in any direction is 28, and the max must keep real margin under it -- the
    minimap's reach shrinks if the player zooms it in."""
    assert travel.WALK_HOP_MAX_TILES <= 24
    assert travel.WALK_HOP_MIN_TILES < travel.WALK_HOP_MAX_TILES


def test_an_unreachable_hop_point_is_pulled_in_rather_than_abandoning_the_walk(monkeypatch):
    """Raising WALK_HOP_MAX_TILES makes every walk lean harder on the minimap's reach, which is a
    user setting we don't control. Before, a point off both screen and minimap aborted the whole
    walk. Pulling the aim in toward the player and retrying costs one poll and keeps the walk
    alive, so a zoomed-in minimap degrades to shorter hops instead of a failed trip."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": False})
    seen = []

    def minimap(x, y, p):
        seen.append((x, y))
        # Only points within 12 tiles of the player are on this (zoomed-in) minimap.
        near = max(abs(x - 3000), abs(y - 3000)) <= 12
        return {"onMinimap": near, "screenX": 50, "screenY": 50}

    monkeypatch.setattr(controller, "get_minimap_walk_point", minimap)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    assert travel._hop_to(_Ctx(), 3024, 3000, 0) is True
    assert len(seen) > 1, "gave up on the first off-minimap point instead of pulling it in"
    assert max(abs(seen[-1][0] - 3000), abs(seen[-1][1] - 3000)) <= 12


def test_a_hop_point_that_is_unreachable_at_every_distance_still_gives_up(monkeypatch):
    """The pull-in must stay bounded -- an unreachable target has to fail, not loop."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, p: {"onScreen": False})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": False})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    assert travel._hop_to(_Ctx(), 3024, 3000, 0) is False


# --- ground walk clicks must force-walk with SHIFT -------------------------------------
#
# CLAUDE.md has carried this rule since golem_crafting hit it in 2026-08: a plain click on a
# ground tile near any interactive object can resolve to that OBJECT's action instead of walking,
# because their clickable areas overlap on screen. Only walk_to_exact_tile was obeying it.
#
# CONFIRMED live 2026-08-29 (recordings/2026-08-29_012522_mh-live-run12): _hop_to PREFERS a
# viewport ground click over the minimap whenever the tile is rendered, so most hops took the
# unshifted path -- and two of them landed as "Chop down" ("You need an axe...") and "Cook"
# ("You haven't got anything to cook."). A hijacked hop leaves the player somewhere it did not
# intend, the next tick recomputes and walks back, and that is the back-and-forth the user saw.

def _hop_harness(monkeypatch, on_screen=True):
    keys = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": on_screen, "screenX": 10, "screenY": 10})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": True, "screenX": 20, "screenY": 20})
    monkeypatch.setattr(controller, "key_down", lambda ser, k: keys.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: keys.append(("up", k)))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: keys.append(("click", entity)) or True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return keys


def test_a_viewport_hop_click_is_shift_clicked(monkeypatch):
    keys = _hop_harness(monkeypatch, on_screen=True)
    travel._hop_to(_Ctx(), 3203, 3200, 0)      # short enough for the viewport path
    kinds = [k[0] for k in keys]
    assert kinds[:3] == ["down", "click", "up"], f"ground hop must force-walk: {kinds[:4]}"
    assert keys[0][1] == "SHIFT" and keys[2][1] == "SHIFT"


def test_a_minimap_hop_click_needs_no_shift(monkeypatch):
    # The minimap has no world objects on it to hijack the click, and SHIFT there means nothing.
    keys = _hop_harness(monkeypatch, on_screen=False)
    travel._hop_to(_Ctx(), 3203, 3200, 0)      # short enough for the viewport path
    assert [k[0] for k in keys][0] == "click", f"minimap hop should click directly: {keys}"


# --- minimap for distance, viewport only for short precise hops ------------------------
#
# User directive 2026-08-29: "use minimap for walking long distances instead of viewport walk,
# only small distance accurate clicks should be viewport."
#
# _hop_to used to prefer the viewport whenever the tile was rendered at all, with the minimap
# only as a fallback for tiles beyond render distance. With WALK_HOP_MIN/MAX_TILES at 16-24 that
# meant nearly every hop of a long route took a viewport click into a scene full of interactive
# scenery -- see the "Chop down" / "Cook" hijacks measured the same day. The minimap has nothing
# on it to hijack a click; precision is what it lacks, and precision is only wanted at the end.

def test_a_long_hop_prefers_the_minimap_even_when_the_tile_is_rendered(monkeypatch):
    asked = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: asked.append("ground") or {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: asked.append("minimap") or {"onMinimap": True, "screenX": 2, "screenY": 2})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicked.append(e) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: clicked.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: clicked.append(("up", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3220, 3200, 0)          # 20 tiles -- a normal route hop

    points = [c for c in clicked if isinstance(c, dict)]
    assert points and points[0].get("onMinimap"), (
        f"a 20-tile hop must go via the minimap, not the viewport: {clicked[:3]}")


def test_a_short_hop_still_uses_the_viewport_for_precision(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": True, "screenX": 2, "screenY": 2})
    seq = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: seq.append(("click", e)) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: seq.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: seq.append(("up", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3203, 3200, 0)          # 3 tiles -- precision matters

    kinds = [s[0] for s in seq]
    assert kinds[:3] == ["down", "click", "up"], f"short hop should shift-click the viewport: {kinds}"
    assert seq[1][1].get("onScreen") is True


def test_a_long_hop_falls_back_to_the_viewport_off_the_minimap(monkeypatch):
    # A zoomed-in minimap shrinks its reach; a rendered tile is still better than giving up.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point", lambda x, y, p: {"onMinimap": False})
    seq = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: seq.append(("click", e)) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: seq.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: seq.append(("up", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3220, 3200, 0)

    assert [s[0] for s in seq][:3] == ["down", "click", "up"]


# --- reachability decided up front, instead of discovered by failing five clicks ----------
#
# MEASURED over one full ROSS contract 2026-08-29: 15 of 27 hotspot clicks -- 56% -- achieved
# nothing, across three stuck events of five wasted clicks each. USER: "every time we have to walk
# through a door now we have to get stuck on the outside 5 clicks in a row this is unacceptable."

def _grid(cells):
    """cells: {(x, y): flags} -> a /collision style response."""
    return [{"x": x, "y": y, "plane": 0, "flags": f} for (x, y), f in cells.items()]


def test_reachable_set_stops_at_a_blocked_edge(monkeypatch):
    # A wall along y=1|2: every y=2 tile blocks south, every y=1 tile blocks north.
    cells = {}
    for x in range(0, 4):
        cells[(x, 2)] = travel.BLOCK_MOVEMENT_SOUTH
        cells[(x, 1)] = travel.BLOCK_MOVEMENT_NORTH
        cells[(x, 3)] = 0
        cells[(x, 0)] = 0
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))

    reach = travel.reachable_set(0, 3, 0)
    assert (3, 2) in reach, "north of the wall is reachable"
    assert (0, 1) not in reach and (0, 0) not in reach, "south of the wall is not"


def test_reachable_set_is_empty_when_the_client_cannot_tell(monkeypatch):
    # Empty must read as "don't know" at every call site, never as "nothing is reachable".
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: [])
    assert travel.reachable_set(0, 0, 0) == set()


def test_can_reach_counts_standing_NEXT_TO_the_goal(monkeypatch):
    # A hotspot's own tile is furniture -- unwalkable -- so adjacency is what matters.
    cells = {(0, 0): 0, (1, 0): 0, (2, 0): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    assert travel.can_reach({"x": 2, "y": 0, "plane": 0}) is True


def test_can_reach_returns_None_when_unknown(monkeypatch):
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    assert travel.can_reach({"x": 5, "y": 5, "plane": 0}) is None


def test_can_reach_counts_DIAGONAL_adjacency(monkeypatch):
    # MEASURED live 2026-08-29 upstairs at JEFF's: player at (3241,3447), hotspot at (3242,3446),
    # every CARDINAL neighbour of the hotspot furniture. A cardinal-only test called it
    # unreachable and work_phase refused to click a thing the character was standing beside.
    # USER: "wtf it is reachable hes standing next to it".
    cells = {(3241, 3447): 0,
             (3241, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3447): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3445): travel.BLOCK_MOVEMENT_OBJECT,
             (3243, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3446): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3241, "y": 3447, "plane": 1})

    assert travel.can_reach({"x": 3242, "y": 3446, "plane": 1}) is True


# --- adjacency is NOT reachability: the EDGE decides -------------------------------------
#
# Two live geometries from 2026-08-29 that look identical by tile distance and must resolve
# oppositely. Distance alone cannot separate them; the collision edge can.

def test_can_reach_is_false_through_a_wall_even_at_distance_one(monkeypatch):
    # ROSS's exterior wall: player (2617,3319) north of it, Broken Range (2617,3318) south.
    # Chebyshev distance 1. USER: "stuck outside ross again... this is absurd".
    cells = {}
    for x in (2616, 2617, 2618):
        cells[(x, 3319)] = travel.BLOCK_MOVEMENT_SOUTH
        cells[(x, 3318)] = travel.BLOCK_MOVEMENT_NORTH
        cells[(x, 3320)] = 0
    cells[(2617, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_OBJECT
    cells[(2618, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_WEST
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2617, "y": 3319, "plane": 0})

    reach = travel.reachable_set(2617, 3319, 0)
    assert (2617, 3319) in reach and (2617, 3318) not in reach
    assert travel.can_reach({"x": 2617, "y": 3318, "plane": 0}, reach) is False, \
        "a wall on the shared edge means unreachable, however close we stand"


def test_can_reach_is_true_diagonally_when_a_corner_route_is_open(monkeypatch):
    # JEFF's upstairs: player (3241,3447), hotspot (3242,3446) whose cardinal neighbours are all
    # furniture -- unwalkable, but with no wall on the edges, so the corner route reaches.
    # USER: "wtf it is reachable hes standing next to it".
    cells = {(3241, 3447): 0,
             (3241, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3447): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3445): travel.BLOCK_MOVEMENT_OBJECT,
             (3243, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3446): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3241, "y": 3447, "plane": 1})

    reach = travel.reachable_set(3241, 3447, 1)
    assert travel.can_reach({"x": 3242, "y": 3446, "plane": 1}, reach) is True


def test_a_diagonal_behind_a_wall_does_not_count(monkeypatch):
    # The permissive version of the diagonal rule was wrong at ROSS's: (2616,3319)/(2618,3319)
    # are diagonal to the hotspot but on the far side of the wall.
    cells = {}
    for x in (2616, 2617, 2618):
        cells[(x, 3319)] = travel.BLOCK_MOVEMENT_SOUTH
        cells[(x, 3318)] = travel.BLOCK_MOVEMENT_NORTH
    cells[(2617, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_OBJECT
    cells[(2618, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_WEST
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2618, "y": 3319, "plane": 0})

    reach = travel.reachable_set(2618, 3319, 0)
    assert travel.can_reach({"x": 2617, "y": 3318, "plane": 0}, reach) is False


# --- route-based door detection ------------------------------------------------------------

BLOCK_MOVEMENT_NORTH = 2
BLOCK_MOVEMENT_EAST = 8
BLOCK_MOVEMENT_SOUTH = 32
BLOCK_MOVEMENT_WEST = 128
BLOCK_MOVEMENT_OBJECT = 256


def _route_door(x, y, name="Door", actions=("Open",), object_type="WallObject",
                 flags=BLOCK_MOVEMENT_NORTH, plane=0):
    return {"id": 7452, "name": name, "objectType": object_type, "actions": list(actions),
            "collisionFlags": flags, "x": x, "y": y, "plane": plane}


def _open_grid(tiles, blocked=()):
    """Flags for a set of tiles, all freely walkable except where stated."""
    grid = {tile: 0 for tile in tiles}
    for tile, value in blocked:
        grid[tile] = value
    return grid


def test_finds_the_edge_door_standing_on_the_blocked_crossing():
    """Barbara's shape: the crossing between two route tiles is shut, and the door object sits
    on one of them. MEASURED live -- a shut door records its blocked edge on BOTH tiles."""
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    door = _route_door(1746, 3534)
    assert travel.first_door_on_route(route, 0, [door], flags) is door


def test_finds_the_full_tile_door_standing_on_a_route_tile():
    """Falador TAU's, MEASURED 2026-08-29: a GameObject door with flags 131328 -- LOS-full plus
    BLOCK_MOVEMENT_OBJECT and NO edge bits at all. Caught because you cannot step onto its
    tile, not because any edge is marked."""
    route = [(3046, 3349), (3046, 3348), (3046, 3347)]
    flags = _open_grid(route, blocked=[((3046, 3348), BLOCK_MOVEMENT_OBJECT)])
    door = _route_door(3046, 3348, object_type="GameObject", flags=131328)
    assert travel.first_door_on_route(route, 0, [door], flags) is door


def test_ignores_an_open_door_because_clicking_one_CLOSES_it():
    """The correction we keep over Vitalite's name-only match. A shut door and an open one are
    both WallObjects named Door (Falador 24050 shut / 24051 open); only the action separates
    them, and left-clicking the open one turns a working walk into a wedged one."""
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    open_door = _route_door(1746, 3534, actions=("Close",))
    assert travel.first_door_on_route(route, 0, [open_door], flags) is None


def test_finds_a_curtain():
    """Taken from Vitalite's name list, which we were missing."""
    route = [(1746, 3533), (1746, 3534)]
    flags = _open_grid(route, blocked=[((1746, 3533), BLOCK_MOVEMENT_NORTH),
                                       ((1746, 3534), BLOCK_MOVEMENT_SOUTH)])
    curtain = _route_door(1746, 3534, name="Curtain")
    assert travel.first_door_on_route(route, 0, [curtain], flags) is curtain


def test_ignores_furniture_that_also_offers_open():
    """Drawers and cupboards fill every Mahogany Homes house and all offer Open. A live scan
    beside Falador's door returned Drawers and a Cupboard within 7 tiles."""
    route = [(1746, 3533), (1746, 3534)]
    flags = _open_grid(route, blocked=[((1746, 3533), BLOCK_MOVEMENT_NORTH),
                                       ((1746, 3534), BLOCK_MOVEMENT_SOUTH)])
    drawers = _route_door(1746, 3534, name="Drawers")
    assert travel.first_door_on_route(route, 0, [drawers], flags) is None


def test_returns_none_when_the_whole_route_is_clear():
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route)
    assert travel.first_door_on_route(route, 0, [_route_door(1746, 3534)], flags) is None


def test_ignores_a_door_on_another_plane():
    route = [(2616, 3317), (2617, 3317)]
    flags = _open_grid(route, blocked=[((2616, 3317), BLOCK_MOVEMENT_EAST)])
    downstairs = _route_door(2616, 3317, plane=0)
    assert travel.first_door_on_route(route, 1, [downstairs], flags) is None


def test_unknown_flags_never_invent_a_door():
    """An absent collision reading means "cannot tell", never "blocked" -- an un-rebuilt or
    out-of-scene answer must behave as before, not conjure a door to click."""
    route = [(1746, 3533), (1746, 3534)]
    assert travel.first_door_on_route(route, 0, [_route_door(1746, 3534)], {}) is None


def test_a_half_known_crossing_yields_no_door():
    """Deliberately strict: when only ONE side of a crossing has a collision reading we decline
    to name a door. An unknown reading must never produce a click -- left-clicking an open door
    CLOSES it, turning a working walk into a wedged one."""
    route = [(1746, 3533), (1746, 3534)]
    flags = {(1746, 3533): BLOCK_MOVEMENT_NORTH}      # (1746,3534) unknown
    assert travel.first_door_on_route(route, 0, [_route_door(1746, 3534)], flags) is None


def test_blocked_crossing_reports_the_first_one_only():
    route = [(10, 10), (10, 11), (10, 12), (10, 13)]
    flags = _open_grid(route, blocked=[((10, 11), BLOCK_MOVEMENT_NORTH),
                                       ((10, 12), BLOCK_MOVEMENT_NORTH)])
    assert travel.blocked_crossing_on_route(route, flags) == ((10, 11), (10, 12))


# --- route-following walker ----------------------------------------------------------------

@pytest.fixture
def walker(monkeypatch):
    """A walker whose hops teleport the player straight to the aim point, so tests exercise
    route selection and door handling rather than the Arduino."""
    state = {"pos": (3200, 3200, 0), "hops": [], "clicked": []}

    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": state["pos"][0], "y": state["pos"][1],
                                 "plane": state["pos"][2]})

    def fake_hop(ctx, x, y, plane):
        state["hops"].append((x, y))
        state["pos"] = (x, y, plane)
        return True

    monkeypatch.setattr(travel, "_hop_to", fake_hop)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, obj, **kw: state["clicked"].append(obj))
    return state


def test_walk_to_follows_a_route_and_arrives(walker, monkeypatch):
    route = [(3200 + i, 3200) for i in range(30)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    assert travel.walk_to(_Ctx(), {"x": 3229, "y": 3200, "plane": 0}) is True
    assert walker["hops"]


def test_walk_to_fails_fast_when_no_route_exists(walker, monkeypatch):
    """The point of the whole change. Before, a routeless walk hop-clicked a wall for up to 8
    minutes; now it is a definite answer in milliseconds."""
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: None)
    assert travel.walk_to(_Ctx(), {"x": 3229, "y": 3200, "plane": 0}) is False
    assert walker["hops"] == []


def test_hops_are_bounded_by_distance_not_by_step_index(walker, monkeypatch):
    """A cardinal BFS route is longer than the diagonal walk the client performs, so indexing
    N steps ahead would silently produce a hop far shorter than the measured 16-24 window."""
    route = [(3200, 3200 + i) for i in range(20)] + [(3200 + i, 3219) for i in range(1, 20)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(travel.random, "randint", lambda lo, hi: hi)
    travel.walk_to(_Ctx(), {"x": 3218, "y": 3219, "plane": 0})
    first_x, first_y = walker["hops"][0]
    assert max(abs(first_x - 3200), abs(first_y - 3200)) <= travel.WALK_HOP_MAX_TILES


def test_a_hop_stops_before_a_shut_door_instead_of_clicking_past_it(walker, monkeypatch):
    """Clicking past a shut door is what produced the wedge-and-re-click loop."""
    route = [(3200, 3200 + i) for i in range(20)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(travel.random, "randint", lambda lo, hi: hi)
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0,
         "flags": BLOCK_MOVEMENT_NORTH if (tx, ty) == (3200, 3204) else 0}
        for tx, ty in route])
    travel.walk_to(_Ctx(), {"x": 3200, "y": 3219, "plane": 0})
    assert walker["hops"][0] == (3200, 3204)


def test_a_hop_is_not_truncated_by_tiles_outside_the_collision_window(walker, monkeypatch):
    """Collision is fetched only within COLLISION_SCAN_RADIUS of the player, but hops aim 16-24
    tiles ahead. _passable answers False when EITHER tile's flags are None, so treating an
    unknown reading as a wall cut every hop down to the scan radius -- halving walking
    efficiency wherever the bridge answered at all. Unknown must mean "past our window", not
    "blocked".

    This mocks get_collision the way the real endpoint behaves: it honours the radius, so tiles
    beyond it are simply absent from the returned flags.
    """
    route = [(3200, 3200 + i) for i in range(30)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(travel.random, "randint", lambda lo, hi: hi)
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0, "flags": 0}
        for tx, ty in route
        if max(abs(tx - x), abs(ty - y)) <= radius])
    travel.walk_to(_Ctx(), {"x": 3200, "y": 3229, "plane": 0})
    first_x, first_y = walker["hops"][0]
    reach = max(abs(first_x - 3200), abs(first_y - 3200))
    assert reach > travel.COLLISION_SCAN_RADIUS, (
        f"first hop reached only {reach} tiles -- truncated to the collision scan window")
    assert reach <= travel.WALK_HOP_MAX_TILES


def test_a_stall_opens_the_door_on_the_route(walker, monkeypatch):
    # Route long enough that target is beyond WALK_ARRIVAL_TOLERANCE (3 tiles).
    # Player starts at (3200, 3200), target at (3219, 3200) = 19 tiles away.
    route = [(3200 + i, 3200) for i in range(20)]
    door = _route_door(3200 + 1, 3200, plane=0)
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    # Blocked edge at step 1: BLOCK_MOVEMENT_EAST at x=3201 (cannot move EAST from there)
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0,
         "flags": BLOCK_MOVEMENT_EAST if (tx, ty) == (3200 + 1, 3200) else 0}
        for tx, ty in route])
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane: True)   # never moves
    travel.walk_to(_Ctx(), {"x": 3219, "y": 3200, "plane": 0})
    assert door in walker["clicked"]


def test_open_door_toward_clicks_the_door_between_us_and_a_goal(walker, monkeypatch):
    """The door is clicked, and the test fixture makes the click succeed by clearing the flags."""
    route = [(3200 + i, 3200) for i in range(20)]
    door = _route_door(3200 + 1, 3200, plane=0)
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    # Blocked edge from (3200, 3200) to (3201, 3200): set BLOCK_MOVEMENT_WEST at destination.
    # Collision starts blocked but clears when the test records a click (simulating door opening).
    collision_state = [{"x": tx, "y": ty, "plane": 0,
                        "flags": BLOCK_MOVEMENT_WEST if (tx, ty) == (3200 + 1, 3200) else 0}
                       for tx, ty in route]
    def get_collision_mock(x, y, plane, radius=2):
        # After the first click, the door opens (flags clear).
        if len(walker["clicked"]) > 0:
            return [{"x": tx, "y": ty, "plane": 0, "flags": 0} for tx, ty in route]
        return collision_state

    monkeypatch.setattr(controller, "get_collision", get_collision_mock)
    assert travel.open_door_toward(_Ctx(), {"x": 3219, "y": 3200, "plane": 0}) is True
    assert door in walker["clicked"]


def test_open_door_toward_confirms_via_crossing_when_the_door_shuts_behind_you(walker, monkeypatch):
    """ROSS's front door swings shut behind you -- MEASURED live 2026-08-29: the recorder shows
    every click registering as `Open Door` while re-reading the doorway kept saying "still shut",
    spending the whole confirmation budget on a door that had already worked. _crossed is the
    fix: getting THROUGH the door counts as success even though the blocked edge never reads
    open again at poll time -- it is the same collision reading throughout this test."""
    route = [(3200 + i, 3200) for i in range(20)]
    door = _route_door(3200 + 1, 3200, plane=0, flags=BLOCK_MOVEMENT_WEST)
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    # Collision NEVER clears -- unlike the test above, the door reads shut before AND after.
    blocked = [{"x": tx, "y": ty, "plane": 0,
               "flags": BLOCK_MOVEMENT_WEST if (tx, ty) == (3200 + 1, 3200) else 0}
              for tx, ty in route]
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: blocked)

    def fake_click(ser, obj, **kw):
        walker["clicked"].append(obj)
        # The click walks us to the door and through it -- simulate arriving on the far side.
        walker["pos"] = (3200 + 2, 3200, 0)

    monkeypatch.setattr(controller, "click_entity", fake_click)
    assert travel.open_door_toward(_Ctx(), {"x": 3219, "y": 3200, "plane": 0}) is True
    assert door in walker["clicked"]


def test_an_upper_plane_route_that_leaves_the_loaded_scene_is_discarded(walker, monkeypatch):
    """Risk 1 from the design doc. Above ground the map has no data outside a building and no
    data reads as PASSABLE, so one missing exterior wall lets BFS escape into open sky. A route
    that wanders past the 104x104 scene is not a path, it is the hole in the data."""
    walker["pos"] = (3200, 3200, 1)
    escaping = [(3200, 3200 + i) for i in range(80)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: escaping)
    assert travel.route_to({"x": 3200, "y": 3279, "plane": 1}) is None


def test_the_same_route_is_accepted_on_the_ground_floor(walker, monkeypatch):
    """Plane 0 is densely mapped, so a long route there is ordinary and must not be rejected."""
    walker["pos"] = (3200, 3200, 0)
    long_route = [(3200, 3200 + i) for i in range(80)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: long_route)
    assert travel.route_to({"x": 3200, "y": 3279, "plane": 0}) is not None


def test_open_door_toward_does_nothing_when_the_way_is_clear(walker, monkeypatch):
    route = [(3200, 3200), (3200, 3201)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_route_door(3200, 3201, plane=0)])
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0, "flags": 0} for tx, ty in route])
    assert travel.open_door_toward(_Ctx(), {"x": 3200, "y": 3201, "plane": 0}) is False
    assert walker["clicked"] == []
