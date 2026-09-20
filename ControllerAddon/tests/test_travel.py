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
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 100, "screenY": 100})
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
                        lambda x, y, p, inst=False: {"onScreen": False})

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
                        lambda x, y, p, inst=False: {"onScreen": False})
    seen = []

    def minimap(x, y, p, inst=False):
        seen.append((x, y))
        # Only points within 12 tiles of the player are on this (zoomed-in) minimap.
        near = max(abs(x - 3000), abs(y - 3000)) <= 12
        return {"onMinimap": near, "screenX": 50, "screenY": 50}

    monkeypatch.setattr(controller, "get_minimap_walk_point", minimap)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    assert travel._hop_to(_Ctx(), 3024, 3000, 0) is True
    assert len(seen) > 1, "gave up on the first off-minimap point instead of pulling it in"
    assert max(abs(seen[-1][0] - 3000), abs(seen[-1][1] - 3000)) <= 12


def test_a_minimap_hop_aim_on_an_orb_is_pulled_in_off_it(monkeypatch):
    """MEASURED live 2026-09-09 (vale_totems vt_live1): a westward hop's aim point at screen
    (1966,151) was the Hitpoints orb drawn over the minimap circle's left edge. /minimap/walk
    called it onMinimap, the click was eaten by the orb, and four identical clicks moved the
    player nowhere. The orb rects come from /state's minimapOrbs; an aim inside one is
    rejected and the pull-in moves the next aim toward the centre, off the orbs."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": False})
    # The HP orb, 57x34 at (1910,155); the far aim projects onto it, closer aims do not.
    monkeypatch.setattr(controller, "get_state",
                        lambda: {"minimapOrbs": [{"x": 1910, "y": 155, "width": 57, "height": 34}]})

    def minimap(x, y, p, inst=False):
        far = max(abs(x - 3000), abs(y - 3000)) >= 20
        return {"onMinimap": True, "screenX": 1966 if far else 2050, "screenY": 170}

    monkeypatch.setattr(controller, "get_minimap_walk_point", minimap)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.append(entity) or True)

    assert travel._hop_to(_Ctx(), 2976, 3000, 0) is True
    assert clicked and clicked[0]["screenX"] == 2050, "clicked the orb instead of pulling in"


def test_a_minimap_hop_on_an_orb_with_no_orb_rects_is_left_alone(monkeypatch):
    """An un-rebuilt client answers no minimapOrbs; the hop must behave exactly as before."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": False})
    monkeypatch.setattr(controller, "get_state", lambda: {})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 1966, "screenY": 170})
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.append(entity) or True)
    assert travel._hop_to(_Ctx(), 2976, 3000, 0) is True
    assert clicked[0]["screenX"] == 1966


def test_a_hop_point_that_is_unreachable_at_every_distance_still_gives_up(monkeypatch):
    """The pull-in must stay bounded -- an unreachable target has to fail, not loop."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, p, inst=False: {"onScreen": False})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": False})
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
                        lambda x, y, p, inst=False: {"onScreen": on_screen, "screenX": 10, "screenY": 10})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 20, "screenY": 20})
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

def test_prefer_viewport_clicks_a_rendered_far_tile_in_the_viewport(monkeypatch):
    """USER 2026-09-06 (Corrupted Gauntlet): rooms are in view, so an instance hop clicks the
    viewport at any length; the minimap is only the fallback for a tile off screen or under a
    UI panel."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 2, "screenY": 2})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui", lambda x, y: None)
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicked.append(e) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: clicked.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: clicked.append(("up", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3220, 3200, 0, prefer_viewport=True)

    points = [c for c in clicked if isinstance(c, dict)]
    assert points and points[0].get("onScreen"), f"viewport expected: {clicked[:3]}"
    assert ("down", "SHIFT") in clicked                     # a viewport walk is always shift-clicked


def test_prefer_viewport_falls_back_to_the_minimap_when_the_tile_is_under_a_panel(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 2, "screenY": 2})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui", lambda x, y: {"x": 0, "y": 0})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicked.append(e) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, k: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3220, 3200, 0, prefer_viewport=True)

    points = [c for c in clicked if isinstance(c, dict)]
    assert points and points[0].get("onMinimap")


def test_a_long_hop_prefers_the_minimap_even_when_the_tile_is_rendered(monkeypatch):
    asked = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: asked.append("ground") or {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: asked.append("minimap") or {"onMinimap": True, "screenX": 2, "screenY": 2})
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
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 2, "screenY": 2})
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
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point", lambda x, y, p, inst=False: {"onMinimap": False})
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


# MEASURED LIVE 2026-08-29 at MARIAH's, the second silent deadlock of the session. A multi-tile
# object is reported at ONE anchor tile, so the anchor's own eight neighbours can all be blocked
# while the object's OTHER half stands beside a perfectly walkable tile. Table 40002 was reported
# at (1769,3622) with every neighbour blocked (Chairs north and south, its own blocking east and
# west), while (1768,3622) -- the same object's west half, flags 131328 -- is cardinally adjacent
# to walkable (1767,3622). USER: "can reach on west side of that table".
#
# can_reach said False, work_phase took its unreachable branch, and because the branch's walk goal
# was already within HOUSE_ARRIVAL_TOLERANCE the whole tick became a no-op that logged nothing and
# advanced no counter -- 79 ticks of total silence the first time, 46s the second.

def test_can_reach_reaches_a_multi_tile_object_by_its_other_half(monkeypatch):
    cells = {(1766, 3622): 0, (1767, 3622): 0, (1767, 3623): 0, (1766, 3621): 0,
             # the Table: anchor plus its west half, both OBJECT-blocked
             (1769, 3622): travel.BLOCK_MOVEMENT_OBJECT,
             (1768, 3622): travel.BLOCK_MOVEMENT_OBJECT,
             # Chairs boxing the anchor in north and south
             (1769, 3621): travel.BLOCK_MOVEMENT_OBJECT,
             (1769, 3623): travel.BLOCK_MOVEMENT_OBJECT,
             (1770, 3622): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1767, "y": 3622, "plane": 0})

    reach = travel.reachable_set(1767, 3622, 0)
    assert (1768, 3622) not in reach and (1769, 3622) not in reach

    assert travel.can_reach({"x": 1769, "y": 3622, "plane": 0}, reach) is True,         "the object's west half is beside a walkable tile, so the object is reachable"


def test_can_reach_does_not_extend_through_a_WALL_blocked_neighbour(monkeypatch):
    """The footprint extension above must key on BLOCK_MOVEMENT_OBJECT and nothing else.

    A WALL blocks with the directional MOVEMENT_* bits and never carries 256 -- ROSS's exterior
    wall reads 65664/16416 -- so extending onto a wall tile would put back the exact "clicking
    through a wall" regression the edge test was added to kill. USER on that one: "stuck outside
    ross again... this is absurd"."""
    cells = {(2617, 3320): 0,
             (2617, 3319): travel.BLOCK_MOVEMENT_SOUTH,
             # the wall tile between us and the goal -- directional bits ONLY, no OBJECT bit
             (2617, 3318): travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_SOUTH,
             (2617, 3317): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2617, "y": 3320, "plane": 0})

    reach = travel.reachable_set(2617, 3320, 0)
    assert travel.can_reach({"x": 2617, "y": 3317, "plane": 0}, reach) is not True,         "a wall-blocked tile is not another half of the goal object"


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


def test_finds_a_portcullis_and_a_barrier_from_the_microbot_name_list():
    """Ported 2026-09-20 from Microbot's Rs2DoorClassifier (barrier, stile, portcullis, archway,
    cattlegate, fence) -- pure strings, broader than the Vitalite list we had."""
    route = [(1746, 3533), (1746, 3534)]
    flags = _open_grid(route, blocked=[((1746, 3533), BLOCK_MOVEMENT_NORTH),
                                       ((1746, 3534), BLOCK_MOVEMENT_SOUTH)])
    for name in ("Portcullis", "Barrier", "Archway", "Stile"):
        obj = _route_door(1746, 3534, name=name)
        assert travel.first_door_on_route(route, 0, [obj], flags) is obj, name


def test_fence_is_a_door_name_only_as_a_whole_word():
    """`fence` inside `Defence` is the trap Microbot's own regex guards against."""
    assert travel.is_door_name("Fence")
    assert travel.is_door_name("Broken fence")
    assert not travel.is_door_name("Defence cape rack")
    assert travel.is_door_name("Cattlegate") and travel.is_door_name("Large door")
    assert not travel.is_door_name("Drawers") and not travel.is_door_name("")


def test_the_door_walk_action_is_the_highest_priority_one_and_never_a_close():
    """Microbot's DOOR_ACTION_PRIORITY: pay-toll before pick-lock before ... before open; a
    Close/Shut is never chosen, so an OPEN door (only Close left) answers None."""
    assert travel.door_walk_action(["Close", "Open"]) == "Open"
    assert travel.door_walk_action(["Open", "Pick-lock"]) == "Pick-lock"
    assert travel.door_walk_action(["Pay-toll(10gp)", "Open"]) == "Pay-toll(10gp)"
    assert travel.door_walk_action(["Close"]) is None
    assert travel.door_walk_action(["Shut", None]) is None
    assert travel.door_walk_action(["Examine"]) is None
    assert travel.door_walk_action(None) is None


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
    N steps ahead would silently produce a hop far shorter than the measured 16-24 window.

    This route is deliberately L-shaped (20 tiles north, then east) so that a genuine tile
    DISTANCE never exceeds WALK_HOP_MAX_TILES anywhere along it -- every point past the corner
    sits exactly 19 tiles from the start, however far east it goes. An UPPER-bound-only assertion
    cannot catch a step-INDEX regression here: with `randint` mocked to always return
    WALK_HOP_MAX_TILES (24), a buggy "advance 24 route-steps" implementation lands at route[24] =
    (3205,3219), only 5 tiles past the corner -- and that point is ALSO only 19 tiles from the
    start, so it would pass an upper bound just as the correct answer does. The lower bound below
    is what actually distinguishes them: the real distance-based hop keeps advancing past that
    point (nothing along the rest of the route ever exceeds the 24-tile budget), all the way to
    route[38] = (3219,3219) -- 14 tiles further than the step-index answer."""
    route = [(3200, 3200 + i) for i in range(20)] + [(3200 + i, 3219) for i in range(1, 20)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(travel.random, "randint", lambda lo, hi: hi)
    travel.walk_to(_Ctx(), {"x": 3218, "y": 3219, "plane": 0})
    first_x, first_y = walker["hops"][0]
    assert max(abs(first_x - 3200), abs(first_y - 3200)) <= travel.WALK_HOP_MAX_TILES
    assert first_x > 3205, (
        f"hop only reached x={first_x} -- that is where a STEP-INDEX-bounded implementation "
        f"(24 route-steps from the start) would stop, not a genuinely DISTANCE-bounded one, "
        f"which has nothing along this route to stop it short of the full 39-tile path")


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


def test_a_door_on_the_route_is_opened_before_hopping(walker, monkeypatch):
    # Renamed (final review, "misleading test names"): this test's name used to claim a STALL
    # triggers the door open, but doors are resolved BEFORE stepping off on every pass (see
    # walk_to's own comment: "the priority should be determine door situation - then go in"), so
    # the door here is found and opened on the very FIRST iteration, before `stalls` is ever
    # incremented -- no stall occurs anywhere in this test.
    #
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


def test_door_opening_is_capped_so_a_stuck_wedge_still_fails(walker, monkeypatch):
    """PORTED from the pre-worldmap suite (`git show 3fb5b41:ControllerAddon/tests/test_travel.py`,
    `test_door_opening_is_capped_so_a_wedge_that_is_not_a_door_still_fails`), which exercised the
    now-deleted `_walk_greedy` directly. Retargeted at `walk_to`, the only place
    DOOR_MAX_OPENS_PER_WALK is still enforced: if the obstruction is reported as a door but
    clicking it never actually clears the crossing (a "wedge" -- it matches every door heuristic,
    but the game never opens it, e.g. another player holding it, or a click that keeps missing),
    the walk must still give up rather than spend DOOR_MAX_OPENS_PER_WALK re-opening the identical
    door forever. The cap is what bounds that -- this is the untested half of it (the tests above
    only ever exercise a door that DOES open)."""
    route = [(3200 + i, 3200) for i in range(20)]
    door = _route_door(3200 + 1, 3200, plane=0, flags=BLOCK_MOVEMENT_EAST)
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    # The crossing NEVER clears -- clicking this "door" is a wedge, not a real fix.
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0,
         "flags": BLOCK_MOVEMENT_EAST if (tx, ty) == (3200 + 1, 3200) else 0}
        for tx, ty in route])
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane: True)   # never moves

    assert travel.walk_to(_Ctx(), {"x": 3219, "y": 3200, "plane": 0}) is False
    assert len(walker["clicked"]) == travel.DOOR_MAX_OPENS_PER_WALK, (
        f"expected exactly {travel.DOOR_MAX_OPENS_PER_WALK} door-open attempts, got "
        f"{len(walker['clicked'])}")


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


# --- _open_door's confirmation budget: distance-scaled, capped, and honestly reported -------
#
# PORTED from the pre-worldmap suite (`git show 3fb5b41:ControllerAddon/tests/test_travel.py`),
# where these targeted the now-deleted `_open_blocking_door` (found the door itself by searching
# from the player, then opened it). `_open_door(ctx, door, recheck)` is the direct successor --
# it no longer does the finding, only the click-and-confirm -- so these are retargeted straight
# at it: pass the door in directly (matching how every current caller, `open_door_toward` and
# `walk_to`, actually uses it) and a `recheck` that never returns None or reports a crossing, so
# the loop is forced to run out its full budget -- which is what makes `sum(slept)` a direct
# read of the budget these tests exist to check.
#
# USER 2026-08-29, on the loop at ROSS's house: "we clicked away before waiting for us to walk to
# the door and open it". A door is opened by walking to it and THEN opening it, so a flat 2s
# budget expires before the player has even arrived at a door a few tiles off.

def test_the_open_budget_scales_with_the_distance_to_the_door(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1740, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    door = _route_door(1746, 3534, flags=BLOCK_MOVEMENT_EAST)   # 6 tiles from the player

    travel._open_door(_Ctx(), door, lambda: door)   # never confirms -- forces the full budget

    # 6 tiles of walking is ~4.2s on its own -- the old flat 2.0s expired before arrival.
    assert sum(slept) > travel.DOOR_OPEN_WAIT_S, (
        f"a door 6 tiles off needs the walk budgeted too, slept only {sum(slept)}s")
    assert sum(slept) >= 6 * travel.DOOR_WALK_ALLOWANCE_S, (
        f"slept {sum(slept)}s, less than the walk alone")


def test_the_open_budget_is_capped_so_a_stuck_door_still_fails_fast(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1700, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    door = _route_door(1746, 3534, flags=BLOCK_MOVEMENT_EAST)   # 46 tiles from the player

    travel._open_door(_Ctx(), door, lambda: door)

    assert sum(slept) <= travel.DOOR_OPEN_WAIT_MAX_S + travel.DOOR_CONFIRM_POLL_S, (
        f"the budget must be capped, slept {sum(slept)}s")


def test_a_door_underfoot_still_confirms_quickly(monkeypatch):
    # Zero distance means no walk to budget -- the fast path must stay fast.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1746, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    door = _route_door(1746, 3534, flags=BLOCK_MOVEMENT_EAST)   # same tile as the player

    travel._open_door(_Ctx(), door, lambda: door)

    assert sum(slept) <= travel.DOOR_OPEN_WAIT_S + travel.DOOR_WALK_ALLOWANCE_S + 0.31, (
        f"zero distance should add almost nothing to the budget, slept {sum(slept)}s")


def test_a_door_that_never_opens_is_reported_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1744, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    logged = []
    monkeypatch.setattr(travel.debug_log, "log", lambda cat, msg: logged.append(msg))
    door = _route_door(1746, 3534, flags=BLOCK_MOVEMENT_EAST)

    travel._open_door(_Ctx(), door, lambda: door)

    assert any("still shut" in m for m in logged), f"a failed open must say so: {logged}"


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


# MEASURED LIVE 2026-08-29 at TAU's house, Falador -- the run's third safety stop. TAU's door is
# DIAGONAL, which the game cannot model as a WallObject on a cardinal edge, so it is a GameObject
# blocking its WHOLE tile both open and shut. OPENING it does not clear a tile, it MOVES the
# full-tile blocker: door 24051 ("Close") stood on (3046,3347) reading 131841
# (BLOCK_LINE_OF_SIGHT_FULL | BLOCK_MOVEMENT_OBJECT). The static map bakes doors open and knows
# nothing of where a leaf rests, so find_path routed through that tile; _hop_index refused the
# very first crossing against live flags, the hop collapsed onto the player's own tile, and
# walk_to returned False with "route makes no further progress" -- 35 tiles from the bank.
#
# The detour is free: re-routing with that one tile avoided is the SAME 51 steps live.

def test_walk_to_reroutes_around_a_tile_blocked_only_in_LIVE_collision(walker, monkeypatch):
    walker["pos"] = (3047, 3347, 0)
    leaf = (3046, 3347)

    through = [(3047, 3347), leaf, (3046, 3348), (3045, 3348), (3044, 3348)]
    around = [(3047, 3347), (3047, 3348), (3046, 3348), (3045, 3348), (3044, 3348)]

    def fake_find_path(sx, sy, plane, gx, gy, goal_adjacent=False, avoid=None, **kw):
        return around if (avoid and leaf in avoid) else through

    monkeypatch.setattr(travel.worldmap, "find_path", fake_find_path)
    # The leaf tile is object-blocked; everything else is clear.
    cells = {t: 0 for t in through + around}
    cells[leaf] = travel.BLOCK_MOVEMENT_OBJECT
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=6: _grid(cells))

    assert travel.walk_to(_Ctx(), {"x": 3044, "y": 3348, "plane": 0}) is True
    assert leaf not in walker["hops"], "must not hop onto the tile the open door leaf occupies"
    assert walker["hops"], "should have walked the detour rather than giving up"


def test_walk_to_does_not_route_around_a_shut_door_it_should_open(walker, monkeypatch):
    """MEASURED LIVE 2026-08-30 at ROSS's, and a regression the reroute itself introduced.

    ROSS's front door at (2610,3316) is the ONLY way in. The walker correctly identified it and
    tried to open it three times (DOOR_MAX_OPENS_PER_WALK); each attempt timed out. With the door
    budget spent, the reroute then excluded that tile as "blocked in live collision" -- removing
    the single tile the route needs, so no route existed at all and the walk failed harder than
    before the reroute was added.

    A shut door is to be OPENED, not routed around. The distinction is exact and comes from the
    object's own actions: TAU's blocker was an OPEN door's leaf ("Close"), which is genuinely
    immovable furniture and SHOULD be avoided; ROSS's is a shut door ("Open"), which should not.
    """
    walker["pos"] = (2612, 3316, 0)
    door_tile = (2610, 3316)
    route = [(2612, 3316), (2611, 3316), door_tile] + [(2609 - i, 3316) for i in range(18)]

    avoided = {}

    def fake_find_path(sx, sy, plane, gx, gy, goal_adjacent=False, avoid=None, **kw):
        if avoid:
            avoided["set"] = set(avoid)
            return None
        return route

    monkeypatch.setattr(travel.worldmap, "find_path", fake_find_path)
    cells = {t: 0 for t in route}
    cells[door_tile] = travel.BLOCK_MOVEMENT_OBJECT
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=6: _grid(cells))
    # A SHUT door stands on it -- the thing _door_on_route finds.
    monkeypatch.setattr(travel, "_door_on_route",
                        lambda route_, plane, flags, start=0: {
                            "id": 40178, "name": "Door", "x": door_tile[0], "y": door_tile[1],
                            "plane": 0, "actions": ["Open"]})
    monkeypatch.setattr(travel, "_open_door", lambda ctx, door, recheck: False)

    travel.walk_to(_Ctx(), {"x": 2592, "y": 3316, "plane": 0})

    assert door_tile not in avoided.get("set", set()),         "a shut door must be opened, never excluded from the route"


def test_walk_to_still_fails_when_no_detour_exists(walker, monkeypatch):
    """The reroute must not become an infinite retry. When avoiding the blocked tile leaves no
    route at all, walk_to still returns a definite False."""
    walker["pos"] = (3047, 3347, 0)
    leaf = (3046, 3347)
    through = [(3047, 3347), leaf] + [(3046 - i, 3348) for i in range(20)]

    def fake_find_path(sx, sy, plane, gx, gy, goal_adjacent=False, avoid=None, **kw):
        return None if (avoid and leaf in avoid) else through

    monkeypatch.setattr(travel.worldmap, "find_path", fake_find_path)
    cells = {t: 0 for t in through}
    cells[leaf] = travel.BLOCK_MOVEMENT_OBJECT
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=6: _grid(cells))

    assert travel.walk_to(_Ctx(), {"x": 3027, "y": 3348, "plane": 0}) is False


def test_walk_to_gives_up_after_the_stall_budget_when_no_door_explains_it(walker, monkeypatch):
    """walk_to's STALL branch, previously uncovered. `test_a_door_on_the_route_is_opened_before_
    hopping` (née test_a_stall_opens_the_door_on_the_route) does NOT reach this branch -- its door
    is found and opened on the very first iteration, before `stalls` is ever incremented, because
    doors are resolved BEFORE stepping off on every pass. This is the genuine stall case: the
    route is completely clear (no blocked crossing anywhere, so _door_on_route never finds
    anything to open) and _hop_to never actually moves the player -- e.g. a hop that keeps
    resolving to the wrong click, or a player who is stuck for a reason the collision map cannot
    see. walk_to must give up once `stalls` exceeds WALK_MAX_STALLS rather than spinning for the
    full WALK_MAX_HOPS budget."""
    route = [(3200 + i, 3200) for i in range(20)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])   # no door anywhere
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0, "flags": 0} for tx, ty in route])   # entirely clear
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane: True)   # never moves

    assert travel.walk_to(_Ctx(), {"x": 3219, "y": 3200, "plane": 0}) is False
    assert walker["clicked"] == [], "no door was ever found -- nothing should have been clicked"


def test_a_door_click_re_aims_while_we_walk_to_it(monkeypatch):
    """USER 2026-08-30: "i think the door isnt updated when we try to click it, nearly always
    lands somewhere else".

    Exactly right, and it is the same class of bug as the hotspot burst. _open_door clicked with
    a BARE click_entity -- no refresh_target -- so it aimed at the pixel the door occupied when
    the ROUTE was computed, while the player was still walking toward it. By the time the cursor
    arrived the projection had slid, and the click landed on whatever was now under it.

    This very likely explains the door failures measured the same night: ROSS's front door logged
    three consecutive "still shut" timeouts (2.7s, 4.1s, 6.2s budgets, all spent) while the
    recorder showed clicks registering -- and a stale aim produces precisely that, a click that
    goes somewhere harmless and a doorway that never opens.

    object_refresh_target re-resolves the door's live screen position from /object/at, which is
    un-cached, so the aim tracks through the flight. CLAUDE.md calls this the preferred pattern
    for every click site where the player may be moving."""
    kwargs = {}
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3048, "y": 3348, "plane": 0})
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda x, y, plane, obj_id, **kw: ("refresh", x, y, plane, obj_id))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: kwargs.update(kw) or True)

    door = {"id": 24050, "name": "Door", "x": 3046, "y": 3348, "plane": 0,
            "actions": ["Open"], "screenX": 100, "screenY": 100}

    travel._open_door(_Ctx(), door, lambda: None)

    assert kwargs.get("refresh_target") == ("refresh", 3046, 3348, 0, 24050),         "a door click must track the door's live screen position while we walk to it"


# MEASURED LIVE 2026-08-30 at BARBARA's. The Bed hotspot 40013 sits at (1749,3539) inside the
# bedroom. Its four neighbours, by LIVE collision:
#
#   (1749,3540) north  flags 16416   edge CLOSED   <- the bedroom's north WALL
#   (1750,3539) east   flags 65664   edge CLOSED
#   (1749,3538) south  flags 135432  edge OPEN     <- inside the bedroom
#   (1748,3539) west   flags 132354  edge OPEN     <- inside the bedroom
#
# worldmap.find_path(goal_adjacent=True) stops at ANY tile cardinally adjacent to the goal, so it
# happily returned a two-step route ending on (1749,3540) -- it never had to cross the wall, so
# the route is valid by the MAP's rules. But you cannot interact with the bed from there, so
# can_reach kept answering False and the routine sat outside the wall until the ceiling stopped
# it. ADJACENCY IS NOT INTERACTABILITY, and the static map cannot tell a wall from furniture.
#
# interaction_tiles answers the question that actually matters: which tiles can I act on this
# thing FROM. Walking to one of those routes through the bedroom door (7452 at (1748,3537)),
# which is then an ordinary door on the route for open_door_toward to find.

def test_interaction_tiles_only_returns_tiles_with_an_open_edge_to_the_goal(monkeypatch):
    cells = {(1749, 3539): 138510,   # the bed itself
             (1749, 3540): 16416,    # north: wall between it and the bed
             (1750, 3539): 65664,    # east: wall
             (1749, 3538): 135432,   # south: open
             (1748, 3539): 132354}   # west: open
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=6: _grid(cells))

    tiles = travel.interaction_tiles({"x": 1749, "y": 3539, "plane": 0})

    assert (1749, 3538) in tiles and (1748, 3539) in tiles
    assert (1749, 3540) not in tiles, "the bedroom's north wall is not an approach tile"
    assert (1750, 3539) not in tiles


def test_interaction_tiles_is_empty_when_the_client_cannot_tell(monkeypatch):
    """Same contract as every other live-collision consumer: empty means "cannot tell", and the
    caller must fall back rather than refuse to work."""
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: [])
    assert travel.interaction_tiles({"x": 1749, "y": 3539, "plane": 0}) == []


# --- the next hop is clicked BEFORE arrival, not after it ------------------------------
#
# USER 2026-08-30: "sometimes we wait so long the player runs to the tile we clicked before we
# click the next point resulting in a weird quick stop". MEASURED in two recordings the same
# night (2026-08-29_231600_mh-collision-live, runs 23 and 24): the player froze for 0.6-1.8s at
# EVERY hop boundary -- e.g. a 1.8s dead stop at (3246,3422) at 01:50:32.9 -- because _hop_to's
# wait only released within WALK_ARRIVAL_TOLERANCE (3 tiles, ~0.9s of running) of the aim, while
# the next click needs ~1.5-2s to land (0.6s poll quantization + walk_to's collision/door
# bookkeeping + the human mouse flight). 0.9s of runway < 2s of overhead, so the player beat the
# click to the tile and stood there. A human clicks the next minimap point several tiles early;
# run 23's tick trace shows the game happily replacing the queued destination mid-run.

def _hop_wait_harness(monkeypatch, start, aim, step=2):
    """Player runs from `start` toward `aim` `step` tiles per poll; returns the poll positions."""
    polled = []
    pos = {"pos": start}

    def get_player():
        x, y = pos["pos"]
        ax, ay = aim
        polled.append((x, y))
        dx = max(-step, min(step, ax - x))
        dy = max(-step, min(step, ay - y))
        pos["pos"] = (x + dx, y + dy)
        return {"x": x, "y": y, "plane": 0}

    monkeypatch.setattr(controller, "get_player", get_player)
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, p, inst=False: {"onScreen": False})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 20, "screenY": 20})
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return polled


def test_a_long_hop_releases_with_enough_runway_to_aim_the_next_click(monkeypatch):
    polled = _hop_wait_harness(monkeypatch, start=(3000, 3000), aim=(3020, 3000))
    assert travel._hop_to(_Ctx(), 3020, 3000, 0) is True
    lx, ly = polled[-1]
    remaining = max(abs(3020 - lx), abs(3000 - ly))
    assert remaining > travel.WALK_ARRIVAL_TOLERANCE, (
        f"released only {remaining} tiles out -- the next click's flight (~1.5-2s) outruns "
        f"{remaining} tiles of running and the player stops dead at the hop boundary")
    assert remaining <= travel.HOP_NEXT_CLICK_LEAD_TILES


def test_a_short_hop_still_waits_for_real_arrival(monkeypatch):
    """A hop no longer than the lead would otherwise release instantly and spam clicks."""
    polled = _hop_wait_harness(monkeypatch, start=(3000, 3000), aim=(3005, 3000))
    assert travel._hop_to(_Ctx(), 3005, 3000, 0) is True
    lx, ly = polled[-1]
    assert max(abs(3005 - lx), abs(3000 - ly)) <= travel.WALK_ARRIVAL_TOLERANCE


# --- open_door_toward must route to a tile it could ACT from, not merely one adjacent ---
#
# MEASURED live 2026-08-30 at JEFF's (mh_live25): hotspot 39991 at (3241,3447), player at
# (3242,3448). The hotspot's NORTH neighbour (3241,3448) is cardinally adjacent AND one step
# from the player -- so goal_adjacent routing stopped there, the one-step route crossed nothing,
# and open_door_toward found NO door, 15 ticks in a row, until the safety stop -- while the only
# real way in ran through the shut inner Door 11775 at (3238,3450). The wall between
# (3241,3448) and the hotspot is exactly the ADJACENCY-IS-NOT-INTERACTABILITY case can_reach and
# interaction_tiles already encode; the door resolution kept the blind spot. USER: "it doesnt
# want to open the inner door, opened the outer door fine".

_JEFF_FLAGS = {
    (3242, 3448): 16416,   # player tile: south edge walled (toward the hotspot's east neighbour)
    (3241, 3448): 16416,   # hotspot's north neighbour: south edge walled -- the adjacency trap
    (3241, 3447): 132354,  # the hotspot itself (furniture + north edge)
    (3242, 3447): 1026,    # east neighbour: open edge to the hotspot -- an interaction tile
    (3240, 3447): 1026,    # west neighbour: open edge -- an interaction tile
    (3241, 3446): 16416,   # south neighbour: open edge north -- an interaction tile
    (3238, 3450): 16416,   # the shut inner door's tile: south edge blocked
    (3238, 3449): 1026,    # far side of the door edge
}

_JEFF_DOOR_ROUTE = [(3242, 3448), (3241, 3449), (3240, 3450), (3239, 3450), (3238, 3450),
                    (3238, 3449), (3239, 3449), (3239, 3448), (3239, 3447), (3240, 3447)]


def test_open_door_toward_finds_the_door_when_an_adjacent_tile_is_walled_off(walker, monkeypatch):
    walker["pos"] = (3242, 3448, 0)
    door = _route_door(3238, 3450, flags=16416)

    def fake_find_path(px, py, plane, gx, gy, goal_adjacent=False, avoid=None):
        if goal_adjacent and (gx, gy) == (3241, 3447):
            # BFS's shortest adjacent tile is the walled-off north neighbour, one step away.
            return [(3242, 3448), (3241, 3448)]
        if (gx, gy) in {(3242, 3447), (3240, 3447), (3241, 3446)}:
            # An interaction tile routes the long way round, through the shut door's edge.
            return _JEFF_DOOR_ROUTE[:-1] + [(gx, gy)]
        return None

    monkeypatch.setattr(travel.worldmap, "find_path", fake_find_path)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])

    def get_collision_mock(x, y, plane, radius=2):
        if walker["clicked"]:      # the click opened the door: its edge clears
            flags = {**_JEFF_FLAGS, (3238, 3450): 0, (3238, 3449): 0}
        else:
            flags = _JEFF_FLAGS
        return [{"x": tx, "y": ty, "plane": 0, "flags": flags.get((tx, ty), 0)}
                for tx, ty in set(list(flags) + _JEFF_DOOR_ROUTE)]

    monkeypatch.setattr(controller, "get_collision", get_collision_mock)

    assert travel.open_door_toward(_Ctx(), {"x": 3241, "y": 3447, "plane": 0}) is True
    assert door in walker["clicked"], (
        "goal_adjacent routing stopped at the walled-off adjacent tile and never saw the door")


def test_interaction_tiles_excludes_neighbours_that_cannot_be_stood_on(monkeypatch):
    """An open EDGE to the goal is not enough -- the tile itself must be standable, or it is a
    useless walk goal. MEASURED live 2026-08-30 at JESS's upstairs (mh_live31 03:08, safety
    stop): hotspot 40175 at (2615,3296) is ringed by other furniture -- its east neighbour
    (2616,3296) reads flags 2104334 (BLOCK_MOVEMENT_FLOOR + edges, no west bit), so the edge
    test called it an interaction tile, the routine chose it as the walk goal, and every route
    to a floor-blocked tile was (correctly) None: 15 ticks of 'no walking route', then the
    stop. The only real approaches are DIAGONAL (the JEFF pattern) -- an empty list here sends
    the caller to its fallback instead of marching at furniture."""
    goal = {"x": 2615, "y": 3296, "plane": 1}
    flags = {
        (2615, 3296): 2229506,   # the hotspot itself
        (2616, 3296): 2104334,   # furniture: floor-blocked, but its west EDGE reads open
        (2614, 3296): 2229506,   # furniture
        (2615, 3295): 2228480,   # furniture
        (2615, 3297): 16416,     # standable, but the shared (south) edge is a wall
    }
    assert travel.interaction_tiles(goal, flags) == []


# --- a door's confirm budget does not expire while the player is still WALKING to it ----
#
# LIVE 2026-08-30, three times in one night at JESS's upstairs (mh_live27 02:26:49, mh_live30
# 02:56:41, mh_live34 03:43:37): the door at (2622,3293) is 2 tiles from the player -- budget
# 3.4s -- but the approach detours around a full-tile furniture blocker at (2622,3292) that the
# static map does not know about, so the real walk is 4+ tiles. Every click registered
# correctly (recorder-verified); the budget expired mid-detour, "still shut" was logged about a
# door that was still being walked to, and the re-resolution's click CANCELLED the queued open.
# Route-length budgeting cannot fix this (the map lacks the very furniture causing the detour),
# so the confirm is condition-based instead: while the player is still moving, the wait extends
# up to DOOR_OPEN_WAIT_MAX_S; only stillness plus a shut doorway spends the budget.

def _door_confirm_harness(monkeypatch, positions, opens_after_polls):
    """get_player walks through `positions` (then holds the last); the door reads shut for
    `opens_after_polls` recheck calls, then open."""
    state = {"polls": 0, "pos": 0}

    def get_player():
        i = min(state["pos"], len(positions) - 1)
        state["pos"] += 1
        x, y = positions[i]
        return {"x": x, "y": y, "plane": 0}

    def recheck():
        state["polls"] += 1
        return None if state["polls"] > opens_after_polls else door

    door = _route_door(2622, 3293, flags=BLOCK_MOVEMENT_NORTH)
    monkeypatch.setattr(controller, "get_player", get_player)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    return door, recheck


def test_door_confirm_keeps_waiting_while_the_player_is_still_walking(monkeypatch):
    # 2 tiles away -> budget 3.4s (~12 polls), but the detour walk means the door only opens
    # after 20 polls -- and the player is moving the whole time.
    # _crossed also reads get_player once per poll, so the walk list is consumed two
    # positions per loop -- keep it long enough to outlast the 20 shut polls.
    walk = [(2622, 3291)] + [(2622 - i % 3, 3291 + i % 2) for i in range(80)]
    door, recheck = _door_confirm_harness(monkeypatch, walk, opens_after_polls=20)
    assert travel._open_door(_Ctx(), door, recheck) is True, \
        "the budget expired on a door the player was still walking to"


def test_door_confirm_still_fails_fast_when_the_player_never_moves(monkeypatch):
    # A click that never moved the player is a dead click: the budget (plus a brief stillness
    # allowance) must end it -- and the hard ceiling must hold even if movement never stops.
    door, recheck = _door_confirm_harness(monkeypatch, [(2622, 3291)], opens_after_polls=10 ** 6)
    assert travel._open_door(_Ctx(), door, recheck) is False


# --- a door that would not open is deprioritized, not retried first --------------------
#
# LIVE 2026-08-30, FOUR visits to JESS's upstairs across runs 27/30/34/37: the static map's
# shortest route crosses the door at (2622,3293), the game ACCEPTS the Open click and never
# takes a single step toward it (the same accepts-then-does-nothing invariant, applied to a
# door -- run 37's motion-aware confirm proved the player stands perfectly still through the
# whole budget), and the SECOND resolution's door at (2617,3294) then works, every time. ~5s
# paid per visit to rediscover the same dead door. So _open_door records a failed door
# (tile-keyed, short TTL), and open_door_toward prefers a candidate route whose door is not
# recently-failed -- but when every candidate runs through failed doors it still clicks one,
# because A SHUT DOOR IS TO BE OPENED, NEVER ROUTED AROUND (the ROSS regression: the only way
# in must never be excluded).

@pytest.fixture(autouse=True)
def _clean_door_failures():
    travel._door_failures.clear()
    yield
    travel._door_failures.clear()


def _two_door_geometry(monkeypatch, walker):
    """Goal with two live interaction tiles; the shorter candidate route crosses door A at
    (2622,3293), the longer crosses door B at (2617,3294). Both doors block the edge their
    route crosses; neither ever opens (clicks in these tests are inert)."""
    walker["pos"] = (2622, 3291, 1)
    door_a = _route_door(2622, 3293, flags=BLOCK_MOVEMENT_NORTH, plane=1)
    door_b = dict(_route_door(2617, 3294, flags=BLOCK_MOVEMENT_NORTH, plane=1), id=7454)
    # Interaction tiles of the goal (2620,3296): its east neighbour (2621,3296) -- reached by
    # the SHORT route through door A -- and its south neighbour (2620,3295), reached by the
    # LONGER route through door B.
    route_a = [(2622, 3291), (2622, 3292), (2622, 3293), (2622, 3294), (2622, 3295),
               (2621, 3296)]
    route_b = [(2622, 3291), (2621, 3291), (2620, 3292), (2619, 3293), (2617, 3294),
               (2617, 3295), (2618, 3295), (2619, 3295), (2620, 3295)]

    def fake_find_path(px, py, plane, gx, gy, goal_adjacent=False, avoid=None):
        if (gx, gy) == (2621, 3296):
            return route_a
        if (gx, gy) == (2620, 3295):
            return route_b
        return None

    flags = {t: 0 for t in route_a + route_b}
    flags.update({(2620, 3296): 0, (2621, 3296): 0, (2620, 3295): 0,
                  (2622, 3293): BLOCK_MOVEMENT_NORTH,      # door A's blocked crossing
                  (2617, 3294): BLOCK_MOVEMENT_NORTH})     # door B's blocked crossing
    monkeypatch.setattr(travel.worldmap, "find_path", fake_find_path)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door_a, door_b])
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=2: [
                            {"x": tx, "y": ty, "plane": 1, "flags": f}
                            for (tx, ty), f in flags.items()])
    return door_a, door_b


def test_a_door_that_failed_to_open_is_not_the_first_choice_next_time(walker, monkeypatch):
    door_a, door_b = _two_door_geometry(monkeypatch, walker)
    goal = {"x": 2620, "y": 3296, "plane": 1}

    travel.open_door_toward(_Ctx(), goal)          # picks the shorter route: door A, which fails
    assert walker["clicked"] and walker["clicked"][0] is door_a
    walker["clicked"].clear()

    travel.open_door_toward(_Ctx(), goal)          # door A is now known-bad: go straight to B
    assert walker["clicked"] and walker["clicked"][0] is door_b, \
        f"the door that would not open was retried first again: {walker['clicked']}"


def test_when_every_candidate_door_has_failed_one_is_still_clicked(walker, monkeypatch):
    door_a, door_b = _two_door_geometry(monkeypatch, walker)
    goal = {"x": 2620, "y": 3296, "plane": 1}
    travel.open_door_toward(_Ctx(), goal)          # A fails
    travel.open_door_toward(_Ctx(), goal)          # B fails
    walker["clicked"].clear()

    travel.open_door_toward(_Ctx(), goal)
    assert walker["clicked"], \
        "every door recently failed, but a shut door is to be OPENED, never routed around"


def test_a_minimap_hop_click_skips_the_ui_blocker_check(monkeypatch):
    # GameStateProvider has reported the minimap rects as UI blockers since 2026-09-02 (a
    # force-walk aimed INSIDE the minimap circle clicked nothing for two minutes). A minimap hop
    # is aimed at that very rect on purpose, so it must opt out of the check -- with it on,
    # click_entity refuses the click and nudges the camera, and the walk never starts. The
    # viewport (ground) click keeps the check: that is what catches the chatbox scrollbar.
    kwargs = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": False, "screenX": 10, "screenY": 10})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 20, "screenY": 20})
    monkeypatch.setattr(controller, "key_down", lambda ser, k: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, k: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: kwargs.append(kw) or True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    travel._hop_to(_Ctx(), 3203, 3200, 0)
    assert kwargs and kwargs[0].get("check_ui_blockers") is False

    kwargs.clear()
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 10, "screenY": 10})
    travel._hop_to(_Ctx(), 3203, 3200, 0)
    assert kwargs and kwargs[0].get("check_ui_blockers", True) is True



def test_hop_to_inst_reads_inst_position_and_forwards_the_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1900, "y": 5600, "instX": 13060, "instY": 5640, "plane": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane, inst=False: (calls.append(("mm", x, y, inst)) or
                                                          {"onMinimap": True, "screenX": 5, "screenY": 5}))
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, inst=False: (calls.append(("gd", x, y, inst)) or
                                                          {"onScreen": False}))
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: calls.append(("click", shift)))
    monkeypatch.setattr(travel.time, "sleep", lambda s: None)
    monkeypatch.setattr(travel, "WALK_HOP_TIMEOUT_S", 0.0)

    assert travel._hop_to(_Ctx(), 13080, 5640, 1, inst=True)
    # The origin for the short-hop test and pull-in is the INSTANCE position (20 tiles away =>
    # minimap), and every lookup carries inst=True.
    assert ("mm", 13080, 5640, True) in calls
    assert all(c[3] is True for c in calls if c[0] in ("mm", "gd"))
    assert ("click", False) in calls


def test_hop_to_inst_short_hop_uses_ground_point_and_waits_in_instance_space(monkeypatch):
    calls = []
    positions = iter([
        {"x": 1900, "y": 5600, "instX": 13060, "instY": 5640, "plane": 1},   # origin read
        {"x": 1900, "y": 5600, "instX": 13061, "instY": 5640, "plane": 1},   # first wait poll: still walking
        {"x": 1900, "y": 5600, "instX": 13064, "instY": 5640, "plane": 1},   # second poll: arrived (within tolerance)
    ])
    last = {"p": None}

    def fake_player():
        try:
            last["p"] = next(positions)
        except StopIteration:
            pass
        return last["p"]

    monkeypatch.setattr(controller, "get_player", fake_player)
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, inst=False: (calls.append(("gd", x, y, inst)) or
                                                          {"onScreen": True, "screenX": 5, "screenY": 5}))
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane, inst=False: (calls.append(("mm", x, y, inst)) or
                                                          {"onMinimap": False}))
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: calls.append(("click", shift)))
    sleeps = []
    monkeypatch.setattr(travel.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(travel, "WALK_HOP_TIMEOUT_S", 6.0)

    assert travel._hop_to(_Ctx(), 13065, 5640, 1, inst=True)
    # 5 tiles in instance space => the SHORT branch: a ground (viewport) point, shift-clicked,
    # and the lookup carries inst=True.
    assert ("gd", 13065, 5640, True) in calls
    assert ("click", True) in calls
    assert not any(c[0] == "mm" for c in calls)
    # Correct wait: poll 1 reads instX 13061 (still walking), poll 2 reads 13064 (within
    # WALK_ARRIVAL_TOLERANCE of 13065) and releases -- exactly two 0.6s sleeps. A wait that read
    # x/y (1900, never within tolerance) would run all ten sleeps to the 6.0s timeout.
    assert len(sleeps) == 2, sleeps
    assert last["p"]["instX"] == 13064


# --- Fairy rings, generic (moved out of routines/falconry.py 2026-09-16) ------------------------
#
# USER: "The fairy ring implementation we made for golem should probably be implemented as a
# generic function for fairy hopping inside travel.py". It was never falconry's: golem_crafting's
# fur trip was calling falconry._leg_fairy_ring -- one routine reaching into another routine's
# PRIVATE travel leg -- and nothing in the mechanic knows about kebbits. These pin the parts that
# are expensive knowledge: the dial arithmetic, and the Confirm-button guard.

def test_fairy_dial_rotations_never_takes_the_long_way_round():
    """Each dial is 0-3 and one CW click adds 1, so three forward is one backward."""
    assert travel.fairy_dial_rotations(0, "A", "ADCB") == []
    assert travel.fairy_dial_rotations(0, "D", "ADCB") == ["cw"]
    assert travel.fairy_dial_rotations(0, "C", "ADCB") == ["cw", "cw"]
    assert travel.fairy_dial_rotations(0, "B", "ADCB") == ["ccw"]
    for start in range(4):
        for letter in "ADCB":
            steps = travel.fairy_dial_rotations(start, letter, "ADCB")
            assert len(steps) <= 2, (start, letter, steps)


def test_fairy_dial_rotations_refuses_what_it_cannot_know():
    """A letter not on this dial is a typo'd code, caught before any click; an unreadable varbit
    must not be guessed at -- the stale-varbit bug is what happens when a dial is misread."""
    assert travel.fairy_dial_rotations(0, "Z", "ADCB") is None
    assert travel.fairy_dial_rotations(None, "A", "ADCB") is None


def test_the_middle_dial_order_is_ILKJ_not_ILJK():
    """MEASURED live 2026-09-14, and the cost of getting it wrong is a trip to another continent:
    "ILJK" looked plausible, dialled what should have been AKQ, and the Confirm button read back
    "Cave south of Dorgesh-Kaan" (AJQ) -- which is how the real order was found."""
    assert travel.FAIRY_DIAL_LETTERS == ("ADCB", "ILKJ", "PSRQ")
    assert travel.FAIRY_DIAL_LETTERS[1].index("K") == 2


def test_confirm_names_the_stop_is_what_licences_the_teleport():
    assert travel.confirm_names_the_stop("Piscatoris Hunter area", "Piscatoris") is True
    assert travel.confirm_names_the_stop("Cave south of Dorgesh-Kaan", "Piscatoris") is False
    assert travel.confirm_names_the_stop("Invalid location", "Piscatoris") is False
    assert travel.confirm_names_the_stop("", "Piscatoris") is False
    assert travel.confirm_names_the_stop(None, "Piscatoris") is False


def test_hop_fairy_ring_is_usable_for_a_destination_falconry_never_heard_of(monkeypatch):
    """The point of the move: any routine can now dial any code without importing falconry."""
    ring = {"id": travel.FAIRY_RING_ID, "x": 1, "y": 2, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(travel.outcome_waits, "await_pre", lambda *a, **kw: True)
    monkeypatch.setattr(travel.time, "sleep", lambda s: None)
    # DKR = a different ring entirely; dials start at A/I/P and must reach D, K, R.
    dials = [0, 0, 0]
    want = ("D", "K", "R")

    def api_get(path, params=None, **kw):
        if path != "/varbit":
            return None
        return {"value": dials[travel.FAIRY_DIAL_VARBITS.index(params["id"])]}
    monkeypatch.setattr(controller, "api_get", api_get)

    def get_widget(group, child, index=None):
        spelled = "".join(travel.FAIRY_DIAL_LETTERS[i][dials[i]] for i in range(3))
        text = "Mort Myre Swamp" if spelled == "DKR" else "Invalid location"
        return {"found": True, "clickable": True, "child": child,
                "text": text if child == travel.FAIRY_CONFIRM_CHILD else "",
                "screenX": 1, "screenY": 1}
    monkeypatch.setattr(controller, "get_widget", get_widget)

    confirmed = []

    def click_entity(ser, entity, **kw):
        child = entity.get("child")
        if child in travel.FAIRY_ROTATE_CW:
            i = travel.FAIRY_ROTATE_CW.index(child)
            dials[i] = (dials[i] + 1) % 4
        elif child in travel.FAIRY_ROTATE_CCW:
            i = travel.FAIRY_ROTATE_CCW.index(child)
            dials[i] = (dials[i] - 1) % 4
        elif child == travel.FAIRY_CONFIRM_CHILD:
            confirmed.append(True)
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)

    class _S:
        def __init__(self):
            self.data = {}
            self.safety_stopped = False
            self.safety_reason = None

        def trigger_safety_stop(self, reason):
            self.safety_stopped = True
            self.safety_reason = reason

    class _C:
        ser = None
        events = None

        def __init__(self):
            self.state = _S()

    ctx = _C()
    assert travel.hop_fairy_ring(ctx, "DKR", "Mort Myre") is True
    assert not ctx.state.safety_stopped
    assert "".join(travel.FAIRY_DIAL_LETTERS[i][dials[i]] for i in range(3)) == "DKR"
    assert confirmed
    for i, letter in enumerate(want):
        assert travel.FAIRY_DIAL_LETTERS[i][dials[i]] == letter
