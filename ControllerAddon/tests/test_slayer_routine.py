import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import combat_core
import controller
import npc_contact
import slayer_state
import slayer as routine

TASKS = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10, "skipTasks": "",
         "maxSkips": 8, "pointsReserve": 60}
CONFIG = {"tasks": TASKS, "targetNames": ["Cow"], "safespot": {"enabled": True}}


class _State:
    def __init__(self):
        self.data, self.stops, self.config = {}, [], dict(CONFIG)

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self):
        self.ser, self.events, self.state = None, None, _State()


def _task(name, count=20, streak=12, points=200):
    return {"count": count, "hasTask": count > 0, "taskName": name, "taskId": 1, "streak": streak, "points": points}


def test_phase_is_derived_from_live_state_alone():
    s = slayer_state.from_bridge
    assert routine.derive_phase(s(_task(None, count=0)), TASKS, {}) == routine.ACQUIRE
    assert routine.derive_phase(s(_task("Drakes")), TASKS, {}) == routine.SKIP
    assert routine.derive_phase(s(_task("Greater demons")), TASKS, {}) == routine.STOP
    assert routine.derive_phase(s(_task("Banshees")), TASKS, {"x": 3440, "y": 3545, "plane": 0}) == routine.FIGHT
    assert routine.derive_phase(s(_task("Banshees")), TASKS, {"x": 3200, "y": 3200, "plane": 0}) == routine.GO_THERE
    assert routine.derive_phase(s(_task("Nechryael")), TASKS, {"x": 1, "y": 1, "plane": 0}) == routine.GO_THERE
    assert routine.derive_phase(s({"count": 9, "hasTask": True, "taskId": 77}), TASKS, {}) == routine.STOP, \
        "a task whose NAME did not decode is never cancelled and never fought"


def test_an_unavailable_slayer_state_does_nothing(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: None)
    called = []
    monkeypatch.setattr(npc_contact, "get_task", lambda *a: called.append(1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert called == [] and ctx.state.stops == []


def test_acquire_asks_the_master_the_streak_calls_for(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    asked = []

    def get_task(ctx, master, cfg):
        asked.append(master)
        return slayer_state.from_bridge(_task("Bears", count=15))
    monkeypatch.setattr(npc_contact, "get_task", get_task)
    for streak, master in ((12, "AYA"), (19, "KURADAL"), (9, "KURADAL"), (20, "AYA")):
        monkeypatch.setattr(controller, "get_slayer_task", lambda s=streak: _task(None, count=0, streak=s))
        routine.tick(_Ctx())
        assert asked[-1] == master


def test_a_missing_requirement_is_a_safety_stop_naming_it(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: "NPC Contact needs 1 astral rune(s) per cast (have 0)")
    cast = []
    monkeypatch.setattr(npc_contact, "get_task", lambda *a: cast.append(1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert cast == [] and "astral" in ctx.state.stops[0]


def test_failed_casts_have_a_ceiling_because_each_one_spends_runes(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    monkeypatch.setattr(routine.time, "time", lambda: 1e9)
    attempts = []
    monkeypatch.setattr(npc_contact, "get_task", lambda ctx, master, cfg: attempts.append(1))
    ctx = _Ctx()
    for _ in range(routine.CONTACT_MAX_CASTS + 2):
        ctx.state.data["slayer_contact_at"] = 0.0
        routine.tick(ctx)
    assert len(attempts) == routine.CONTACT_MAX_CASTS
    assert ctx.state.stops and "NPC Contact" in ctx.state.stops[0]


def test_an_unknown_dialogue_menu_is_reported_in_the_stop_message(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)

    def get_task(ctx, master, cfg):
        ctx.state.data["npc_contact_unknown_menu"] = "Yes, replace my task. | No."
        return None
    monkeypatch.setattr(npc_contact, "get_task", get_task)
    ctx = _Ctx()
    routine.tick(ctx)
    assert ctx.state.stops and "replace my task" in ctx.state.stops[0], "an unknown menu stops at once, no retry"


def test_skip_cancels_through_the_milestone_master_and_counts_per_slot(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Drakes", points=300))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    used = []
    monkeypatch.setattr(npc_contact, "cancel_task", lambda ctx, master, cfg: used.append(master) or True)
    routine.tick(_Ctx())
    assert used == ["KURADAL"], "rewards are never opened through the filler master"


def test_skip_without_the_points_is_a_safety_stop_not_a_fight(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Drakes", points=70))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    cancelled = []
    monkeypatch.setattr(npc_contact, "cancel_task", lambda *a: cancelled.append(1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert cancelled == [] and "drakes" in ctx.state.stops[0].lower() and "points" in ctx.state.stops[0]


def test_stop_names_the_task(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Greater demons"))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    ctx = _Ctx()
    routine.tick(ctx)
    assert "greater demons" in ctx.state.stops[0].lower()


def test_away_from_the_site_it_stops_and_says_where_to_go(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    ctx = _Ctx()
    routine.tick(ctx)
    assert "bears" in ctx.state.stops[0].lower() and "Legends" in ctx.state.stops[0]


def test_at_the_site_it_fights_with_the_tasks_monsters_and_restores_the_config(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    seen = []
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(
        (list(ctx.state.config["targetNames"]), ctx.state.config["safespot"]["enabled"])))
    ctx = _Ctx()
    routine.tick(ctx)
    assert seen == [(["banshees"], False)]
    assert ctx.state.config["targetNames"] == ["Cow"], "the panel config comes back even if the fight raises"
    assert ctx.state.stops == []


def test_a_new_task_resets_the_per_slot_counters(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees", streak=13))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: None)
    ctx = _Ctx()
    ctx.state.data.update({"slayer_casts": 2, "slayer_skips": 3, "slayer_streak_seen": 12})
    routine.tick(ctx)
    assert ctx.state.data["slayer_casts"] == 0 and ctx.state.data["slayer_skips"] == 0
