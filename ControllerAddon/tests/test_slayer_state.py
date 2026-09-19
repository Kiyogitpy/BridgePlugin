import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import slayer_state

CFG = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10,
       "maxSkips": 8, "pointsReserve": 60}


@pytest.mark.parametrize("streak,master", [
    (0, "AYA"), (8, "AYA"), (9, "KURADAL"), (10, "AYA"), (19, "KURADAL"),
    (48, "AYA"), (49, "KURADAL"), (99, "KURADAL"), (100, "AYA"),
])
def test_the_milestone_master_gets_every_task_whose_completion_is_a_multiple(streak, master):
    """USER: 'do 9 tasks with Aya then swap to Kuradal ... like the 20th task would be Kuradal'.
    `streak` is tasks COMPLETED, so the task about to be taken is number streak + 1."""
    assert slayer_state.next_master(streak, CFG) == master


def test_a_fifty_interval_is_a_settings_change_not_a_rewrite():
    cfg = dict(CFG, milestoneEvery=50, milestoneMaster="KONAR")
    assert slayer_state.next_master(9, cfg) == "AYA"
    assert slayer_state.next_master(49, cfg) == "KONAR"


def test_defaults_apply_when_the_panel_block_is_missing():
    assert slayer_state.next_master(9, {}) == "KURADAL"
    assert slayer_state.next_master(3, None) == "AYA"


def test_from_bridge_keeps_unavailable_distinct_from_no_task():
    assert slayer_state.from_bridge(None) is None, "bridge/endpoint unavailable: the caller does NOTHING"
    idle = slayer_state.from_bridge({"count": 0, "hasTask": False, "streak": 9, "points": 415, "masterId": 2, "taskId": 0})
    assert idle.has_task is False and idle.count == 0 and idle.streak == 9 and idle.task is None


def test_an_undecoded_name_is_absent_on_the_wire_and_reads_as_none():
    """Gson drops null map values, so taskName is simply missing when the DB lookup failed."""
    state = slayer_state.from_bridge({"count": 27, "hasTask": True, "taskId": 4135, "streak": 19, "points": 0, "masterId": 2})
    assert state.has_task and state.task is None and state.task_id == 4135
    named = slayer_state.from_bridge({"count": 27, "hasTask": True, "taskName": "Banshees", "taskId": 4135,
                                      "streak": 19, "points": 0, "masterId": 2})
    assert named.task == "banshees"


def test_aya_is_never_contacted_while_a_task_is_active():
    """The one invariant (spec F2): asking Aya to REPLACE a task resets the streak to zero."""
    active = slayer_state.from_bridge({"count": 12, "hasTask": True, "taskName": "Gargoyles", "streak": 19, "points": 0})
    with pytest.raises(slayer_state.StreakGuardError):
        slayer_state.assert_may_contact("AYA", active, CFG)
    idle = slayer_state.from_bridge({"count": 0, "hasTask": False, "streak": 19, "points": 0})
    slayer_state.assert_may_contact("AYA", idle, CFG)
    slayer_state.assert_may_contact("KURADAL", active, CFG)      # rewards / cancel through the milestone master is fine


def test_the_guard_follows_the_configured_filler_master_and_refuses_an_unknown_state():
    active = slayer_state.from_bridge({"count": 12, "hasTask": True, "streak": 19, "points": 0})
    cfg = dict(CFG, fillerMaster="KONAR")
    with pytest.raises(slayer_state.StreakGuardError):
        slayer_state.assert_may_contact("KONAR", active, cfg)
    slayer_state.assert_may_contact("AYA", active, cfg)          # not the filler under this config
    with pytest.raises(slayer_state.StreakGuardError):
        slayer_state.assert_may_contact("AYA", active, None)     # None = defaults, where Aya IS the filler
    with pytest.raises(slayer_state.StreakGuardError):
        slayer_state.assert_may_contact("KURADAL", None, CFG)    # bridge unavailable: contact nobody


def test_skipping_respects_the_points_reserve_and_the_per_slot_ceiling():
    assert slayer_state.can_skip(200, 0, CFG) == (True, "")
    ok, why = slayer_state.can_skip(80, 0, CFG)
    assert not ok and "points" in why, "80 - 30 would leave 50, under the 60 reserve"
    assert slayer_state.can_skip(90, 0, CFG)[0], "90 - 30 = 60, exactly the reserve"
    ok, why = slayer_state.can_skip(500, 8, CFG)
    assert not ok and "8" in why
    assert slayer_state.can_skip(500, 2, {})[0], "defaults: 8 skips, 60 reserve"
