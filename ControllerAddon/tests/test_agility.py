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
