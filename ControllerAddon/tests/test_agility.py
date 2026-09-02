import os
import sys
from itertools import combinations

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import agility_courses as ac

W = ac.WILDERNESS


# --- course data pinned to recordings/2026-09-02_032621_wilderness-agility ---------

def test_wilderness_step_order_ids_and_actions():
    assert [s.key for s in W.steps] == ["pipe", "ropeswing", "stones", "log", "rocks"]
    assert [s.object_id for s in W.steps] == [23137, 23132, 23556, 23542, 23640]
    assert [s.action for s in W.steps] == [
        "Squeeze-through", "Swing-on", "Cross", "Walk-across", "Climb"]
    assert W.tag is not None and W.tag.object_id == 53224 and W.tag.action == "Tag"
    assert W.tag.confirm == "ticket"
    assert [s.fail_mode for s in W.steps] == ["none", "pit", "knockback", "pit", "none"]
    assert W.steps[4].xp_certain is False          # rocks: 498.9 only on a clean lap


def test_wilderness_pit_ladder_pinned_live_2026_09_02():
    assert W.pit_ladder == {"id": 17385, "x": 3005, "y": 10363, "plane": 0,
                            "action": "Climb-up"}
    assert W.first_pit_step().key == "ropeswing"


def test_start_boxes_are_pairwise_disjoint():
    steps = W.all_steps()
    for a, b in combinations(steps, 2):
        overlap = (a.start.x1 <= b.start.x2 and b.start.x1 <= a.start.x2
                   and a.start.y1 <= b.start.y2 and b.start.y1 <= a.start.y2)
        assert not overlap, f"{a.key} start box overlaps {b.key}"


@pytest.mark.parametrize("tile,key", [
    ((3004, 3936), "pipe"),        # recorded Squeeze-through click tile
    ((3004, 3950), "ropeswing"),   # recorded Swing-on click tile
    ((3004, 3949), "ropeswing"),   # pipe exit tile: the swing is next
    ((3005, 3958), "stones"),      # recorded Cross click tile
    ((2996, 3960), "log"),         # recorded Walk-across click tile
    ((2996, 3945), "rocks"),       # recorded Climb click tile
    ((2994, 3945), "rocks"),       # second recorded Climb click tile
    ((2995, 3933), "tag"),         # recorded Tag click tile
    ((2996, 3933), "tag"),
])
def test_step_for_position_matches_recorded_click_tiles(tile, key):
    step = ac.step_for_position(W, tile[0], tile[1], 0)
    assert step is not None and step.key == key


def test_every_end_box_lies_inside_the_next_start_box():
    steps = W.all_steps()
    for i, s in enumerate(steps):
        nxt = steps[(i + 1) % len(steps)]
        assert W.next_step(s) is nxt
        for x in range(s.end.x1, s.end.x2 + 1):
            for y in range(s.end.y1, s.end.y2 + 1):
                assert nxt.start.contains(x, y, 0), f"{s.key} end ({x},{y}) not in {nxt.key} start"


def test_zone_and_pit():
    assert ac.in_zone(W, 2996, 3945, 0)
    assert ac.in_zone(W, 3004, 3936, 0)
    assert not ac.in_zone(W, 2998, 3916, 0)        # south of the entrance Door
    assert not ac.in_zone(W, 3005, 10362, 0)       # dungeon
    assert ac.in_pit(W, 3005, 10362, 0)            # live tile beside ladder 17385
    assert not ac.in_pit(W, 3005, 3962, 0)
    assert not ac.in_zone(W, 2996, 3945, 1)        # wrong plane


def test_step_for_position_none_off_course():
    assert ac.step_for_position(W, 2998, 3916, 0) is None
    assert ac.step_for_position(W, 3005, 10362, 0) is None


# --- pure helpers ----------------------------------------------------------------

def test_xp_threshold_accounts_for_fractional_and_pending_xp():
    pipe, swing = W.steps[0], W.steps[1]
    assert ac.xp_threshold(pipe, 1000.0, 0.0) == pytest.approx(1011.5)
    # pipe XP still in flight when the swing is clicked: 12.5 pending + 20 - 1
    assert ac.xp_threshold(swing, 1000.0, 12.5) == pytest.approx(1031.5)


def test_step_budget_scales_with_walk_and_caps():
    log = W.steps[3]                              # 10.0s measured
    assert ac.step_budget_s(log, 0) == pytest.approx(20.0)
    assert ac.step_budget_s(log, 5) == pytest.approx(23.0)
    assert ac.step_budget_s(log, 100) == pytest.approx(ac.BUDGET_CAP_S)


@pytest.mark.parametrize("kw,expected", [
    (dict(xp_gained=True, saw_busy=False, busy_now=0, in_end=False, in_pit=False, timed_out=False), "success"),
    (dict(xp_gained=False, saw_busy=True, busy_now=0, in_end=True, in_pit=False, timed_out=False), "success"),
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=False, in_pit=True, timed_out=False), "fell"),
    (dict(xp_gained=False, saw_busy=False, busy_now=0, in_end=False, in_pit=True, timed_out=False), "fell"),
    (dict(xp_gained=False, saw_busy=True, busy_now=0, in_end=False, in_pit=False, timed_out=False), "failed"),
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=False, in_pit=False, timed_out=False), "pending"),
    (dict(xp_gained=False, saw_busy=False, busy_now=0, in_end=False, in_pit=False, timed_out=False), "pending"),
    (dict(xp_gained=False, saw_busy=False, busy_now=0, in_end=False, in_pit=False, timed_out=True), "no_click"),
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=False, in_pit=False, timed_out=True), "failed"),
    # busy still 1 but the player is already in the end box with no XP yet: keep waiting
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=True, in_pit=False, timed_out=False), "pending"),
])
def test_classify_truth_table(kw, expected):
    assert ac.classify(**kw) == expected


def test_chebyshev():
    assert ac.chebyshev((3004, 3936), (3004, 3949)) == 13
    assert ac.chebyshev((2996, 3945), (3005, 3936)) == 9


# =============================== engine =========================================

import types

import controller
import travel
import agility


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Scene:
    """Scripted bridge. Each call to advance() moves to the next reading; the last reading
    repeats forever. A reading is dict(xp=, busy=, pos=(x,y,plane), hp=)."""

    def __init__(self, readings):
        self.readings = list(readings)
        self.i = 0
        self.clicks = []          # (object_id, action, kind)
        self.walks = []           # target dicts
        self.eats = 0
        self.objects = []
        self.inventory = []

    def cur(self):
        return self.readings[min(self.i, len(self.readings) - 1)]

    def advance(self):
        self.i += 1

    # bridge stand-ins
    def get_api(self, group, reader):
        assert (group, reader) == ("skills", "skills")
        return {"skills": {"AGILITY": {"experience": self.cur()["xp"]}}}

    def get_varbit(self, vid):
        assert vid == ac.BUSY_VARBIT
        return self.cur()["busy"]

    def get_player(self):
        x, y, p = self.cur()["pos"]
        if x is None:
            return {}
        return {"x": x, "y": y, "plane": p, "hp": self.cur().get("hp", 90)}


def _obj(step, x=None, y=None):
    return {"id": step.object_id, "name": step.name.title(), "x": x or step.start_tile[0],
            "y": y or step.start_tile[1] + 3, "plane": 0, "distance": 3,
            "screenX": 500, "screenY": 500, "actions": [step.action]}


@pytest.fixture
def scene(monkeypatch):
    sc = Scene([dict(xp=1000, busy=0, pos=(3004, 3936, 0))])
    clock = FakeClock()
    monkeypatch.setattr(agility, "_now", clock.now)
    # every sleep also advances the scripted scene by one reading
    def _sleep(s):
        clock.sleep(s)
        sc.advance()
    monkeypatch.setattr(agility, "_sleep", _sleep)
    monkeypatch.setattr(controller, "get_api", sc.get_api)
    monkeypatch.setattr(controller, "get_varbit", sc.get_varbit)
    monkeypatch.setattr(controller, "get_player", sc.get_player)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: list(sc.objects))
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, plane, obj_id, radius=None:
                        next((o for o in sc.objects if o["id"] == obj_id), None))
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda x, y, plane, obj_id, radius=None: (lambda: (500, 500)))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: sc.clicks.append(
                            (ent["id"], kw.get("expected_action"), "left")) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **kw: sc.clicks.append(
                            (ent["id"], option, "menu")) or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "consume_first",
                        lambda ser, cat: (sc.__setattr__("eats", sc.eats + 1) or True)
                        if sc.inventory else False)
    # a reading may carry its own "inv" list so a test can script an inventory change mid-wait
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: list(sc.cur().get("inv", sc.inventory)))
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, **kw: sc.walks.append(dict(target)) or True)
    sc.clock = clock
    return sc


def make_ctx():
    state = controller.ControllerState()
    state.config = agility.get_config(None)
    return types.SimpleNamespace(ser=None, state=state, events=None)


def test_default_config_flags():
    cfg = agility.get_config({"anything": 1})
    assert cfg["course"] == "wilderness"
    assert cfg["pvpSafety"]["fleeEnabled"] is False
    assert cfg["safety"]["stopOnInventoryFull"] is False
    assert cfg["safety"]["stopOnDeath"] is True


def test_run_step_success_on_xp(scene):
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert scene.clicks == [(23137, "Squeeze-through", "left")]
    assert ctx.state.data["pending_xp"] == 0.0


def test_run_step_success_on_end_box_sets_pending_xp(scene):
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    # XP never arrives inside the window, but busy drops with the player at the exit
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3942, 0)),
                      dict(xp=1000, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert ctx.state.data["pending_xp"] == pytest.approx(12.5)


def test_pending_xp_blocks_late_drop_from_completing_next_step(scene):
    swing = W.steps[1]
    scene.objects = [_obj(swing)]
    # the pipe's late +12 lands while we wait on the swing; busy never rises; then timeout
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3950, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3950, 0))]
    ctx = make_ctx()
    ctx.state.data["pending_xp"] = 12.5
    assert agility.run_step(ctx, W, swing) == "no_click"


def test_run_step_times_out_when_player_unreadable(scene):
    """A persistent /player failure (get_player() -> {}) must still hit the step budget --
    `px is None` cannot be an unconditional `continue`, or the poll loop never terminates."""
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    scene.readings = [dict(xp=1000, busy=0, pos=(None, None, None))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, pipe) == "no_click"


def test_run_step_fell_into_dungeon(scene):
    swing = W.steps[1]
    scene.objects = [_obj(swing)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3950, 0)),
                      dict(xp=1000, busy=1, pos=(3005, 3953, 0)),
                      dict(xp=1000, busy=0, pos=(2998, 10352, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, swing) == "fell"


def test_run_step_knockback_failed(scene):
    stones = W.steps[2]
    scene.objects = [_obj(stones)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3005, 3958, 0)),
                      dict(xp=1000, busy=1, pos=(3003, 3960, 0)),
                      dict(xp=1000, busy=0, pos=(3005, 3958, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, stones) == "failed"


def test_run_step_rocks_forfeited_lap_completes_on_end_box_without_pending(scene):
    rocks = W.steps[4]
    scene.objects = [_obj(rocks)]
    scene.readings = [dict(xp=1000, busy=0, pos=(2996, 3945, 0)),
                      dict(xp=1000, busy=1, pos=(2995, 3940, 0)),
                      dict(xp=1000, busy=0, pos=(2995, 3933, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, rocks) == "success"
    assert ctx.state.data["pending_xp"] == 0.0        # xp_certain False: never pend 498.9


def test_run_step_no_object(scene):
    ctx = make_ctx()
    scene.objects = []
    assert agility.run_step(ctx, W, W.steps[0]) == "no_object"
    assert scene.clicks == []


def test_withheld_click_escalates_to_menu(scene, monkeypatch):
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: scene.clicks.append((ent["id"], "withheld", "left"))
                        or controller.CLICK_WITHHELD)
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert scene.clicks[-1] == (23137, "Squeeze-through", "menu")


def test_tick_chains_steps_within_one_tick(scene):
    """pipe done -> swing clicked in the SAME tick, no engine-loop latency in between."""
    pipe, swing = W.steps[0], W.steps[1]
    scene.objects = [_obj(pipe), _obj(swing)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),   # resolve: pipe
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0)),   # pipe success
                      dict(xp=1012, busy=1, pos=(3005, 3953, 0)),   # swing in progress
                      dict(xp=1032, busy=0, pos=(3005, 3958, 0))]   # swing success
    ctx = make_ctx()
    agility.tick(ctx)
    ids = [c[0] for c in scene.clicks]
    assert ids[:2] == [23137, 23132]


def test_tick_outside_course_safety_stops(scene):
    scene.readings = [dict(xp=1000, busy=0, pos=(2998, 3916, 0))]
    ctx = make_ctx()
    agility.tick(ctx)
    assert ctx.state.safety_stopped and "outside the course" in ctx.state.safety_reason


def test_failed_step_retries_and_ceiling_stops(scene):
    stones = W.steps[2]
    ctx = make_ctx()
    ctx.state.data["attempts"] = agility.STEP_MAX_ATTEMPTS - 1
    assert agility.handle_outcome(ctx, W, stones, "failed") is False
    assert ctx.state.safety_stopped and "stones" in ctx.state.safety_reason


def test_failed_step_walks_back_to_start_when_displaced(scene):
    stones = W.steps[2]
    scene.readings = [dict(xp=1000, busy=0, pos=(3000, 3962, 0))]   # not in stones' start box
    ctx = make_ctx()
    assert agility.handle_outcome(ctx, W, stones, "failed") is True
    assert scene.walks and (scene.walks[-1]["x"], scene.walks[-1]["y"]) == (3005, 3958)
    assert ctx.state.data["attempts"] == 1


def test_no_object_ceiling_stops(scene):
    ctx = make_ctx()
    ctx.state.data["no_object_ticks"] = agility.NO_OBJECT_MAX_TICKS - 1
    assert agility.handle_outcome(ctx, W, W.steps[0], "no_object") is False
    assert ctx.state.safety_stopped


def test_resolve_step_off_box_walks_to_nearest_start(scene, monkeypatch):
    # a tile inside the zone but in no start box (mid-course grass)
    scene.readings = [dict(xp=1000, busy=0, pos=(3000, 3950, 0))]
    monkeypatch.setattr(agility, "_route_len",
                        lambda a, b: ac.chebyshev(a, b))
    ctx = make_ctx()
    assert agility.resolve_step(ctx, W) is None
    assert scene.walks and (scene.walks[-1]["x"], scene.walks[-1]["y"]) == (3004, 3950)
    assert ctx.state.data["no_step_ticks"] == 1


def test_events_used_only_until_first_match(scene):
    """wait_for_event does not consume: after one match the engine must fall back to _sleep."""
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    calls = []

    class Ev:
        def clear(self):
            calls.append("clear")

        def wait_for_event(self, types, match=None, timeout_s=0.1):
            calls.append("wait")
            agility._sleep(timeout_s)
            return {"skill": "Agility", "delta": 12}

    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3944, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    ctx.events = Ev()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert calls.count("wait") == 1 and calls[0] == "clear"
