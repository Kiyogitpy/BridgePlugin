import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import aya_tasks
import combat_core
import controller
import gear
import legs
import npc_contact
import slayer_routes
import slayer_state
import slayer as routine

TASKS = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10, "skipTasks": "",
         "maxSkips": 8, "pointsReserve": 60}
CONFIG = {"tasks": TASKS, "targetNames": ["Cow"], "safespot": {"enabled": True}}


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """HERMETIC, enforced. See tests/test_npc_contact.py: a test that forgot to fake a bridge read
    read the LIVE client instead."""
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


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
    assert routine.derive_phase(s(_task("Banshees")), TASKS, {"x": 3200, "y": 3200, "plane": 0}) == routine.TRAVEL
    assert routine.derive_phase(s(_task("Nechryael")), TASKS, {"x": 1, "y": 1, "plane": 0}) == routine.TRAVEL
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
    _carry(monkeypatch)
    monkeypatch.setattr(slayer_routes, "plan",
                        lambda *a, **k: (None, "Fairy ring: BLR: no fairy ring within a 150-tile walk"))
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


def test_the_fight_never_banks_on_its_own(monkeypatch):
    """Spec 7.5 / final review: with the panel's bank on, fight_tick teleports to a bank by itself
    and the next tick safety-stops there, off-site."""
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    seen = []
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(dict(ctx.state.config["bank"])))
    ctx = _Ctx()
    ctx.state.config["bank"] = {"enabled": True, "returnToSpot": True}
    routine.tick(ctx)
    assert seen == [{"enabled": False, "returnToSpot": True}]
    assert ctx.state.config["bank"]["enabled"] is True, "the panel config itself is untouched"


def test_the_bridges_list_shaped_skip_list_reaches_the_phase(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bloodveld", points=300))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    used = []
    monkeypatch.setattr(npc_contact, "cancel_task", lambda ctx, master, cfg: used.append(master) or True)
    ctx = _Ctx()
    ctx.state.config["tasks"] = dict(TASKS, skipTasks=["Bloodveld"])
    routine.tick(ctx)
    assert used == ["KURADAL"]


def test_the_requirement_reads_wait_out_the_cooldown(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    reads = []
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: reads.append(1))
    monkeypatch.setattr(npc_contact, "get_task", lambda *a: None)
    ctx = _Ctx()
    routine.tick(ctx)
    routine.tick(ctx)
    assert len(reads) == 1, "the second tick is inside the cooldown and reads nothing"


def _carry(monkeypatch, names=()):
    import gear
    monkeypatch.setattr(gear, "carried_names", lambda: list(names))
    monkeypatch.setattr(gear, "worn_names", lambda: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": n, "qty": 1} for n in names])
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 70})


def test_travel_runs_one_leg_per_tick_and_keeps_its_cursor(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    route = [legs.walk(2740, 3351, 0), legs.fairy("BLR", "Legends"), legs.walk(2700, 3330, 0)]
    planned = []
    monkeypatch.setattr(slayer_routes, "plan", lambda task, player, carried, skill_levels=None: planned.append(task) or (route, ""))
    ran = []
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": ran.append(cursor) or (legs.DONE, cursor + 1))
    ctx = _Ctx()
    routine.tick(ctx)
    routine.tick(ctx)
    assert ran == [0, 1] and planned == ["bears"], "planned once, then the cursor advances a leg per tick"
    assert ctx.state.stops == []


def test_a_missing_required_item_stops_before_any_teleport(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Cave slimes", count=17))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2700, "y": 3321, "plane": 0})
    _carry(monkeypatch, names=["Teleport to house"])
    ran = []
    monkeypatch.setattr(legs, "run_next", lambda *a, **k: ran.append(1) or (legs.DONE, 1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert ran == [] and "light source" in ctx.state.stops[0]


def test_a_leg_that_keeps_failing_is_a_stop_that_names_the_leg(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: ([legs.fairy("BLR", "Legends")], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.FAILED, cursor))
    ctx = _Ctx()
    for _ in range(routine.TRAVEL_MAX_FAILS + 1):
        routine.tick(ctx)
    assert ctx.state.stops and "fairy ring BLR" in ctx.state.stops[0]


def test_endless_progress_is_bounded(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: ([legs.fairy("BLR", "Legends")], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.PROGRESS, cursor))
    ctx = _Ctx()
    for _ in range(routine.TRAVEL_MAX_PROGRESS + 2):
        routine.tick(ctx)
    assert ctx.state.stops and "fairy ring BLR" in ctx.state.stops[0]


def test_a_finished_route_replans_once_then_stops(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    plans = []
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: plans.append(1) or ([legs.walk(1, 1, 0)], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.FINISHED, cursor))
    ctx = _Ctx()
    for _ in range(4):
        routine.tick(ctx)
    assert len(plans) == 2 and ctx.state.stops and "not reached" in ctx.state.stops[0]


def _fighting(monkeypatch, target_index):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: ctx.state.data.__setitem__("current_target_index", target_index))


def test_nothing_to_fight_for_long_walks_back_to_the_site_then_stops(monkeypatch):
    """Targets wander and respawn at the site tile; 'stranded with nothing to fight' must not look
    like idling (docs/routine-notes.md)."""
    _fighting(monkeypatch, None)
    walks = []
    monkeypatch.setattr(legs, "walk_across", lambda ctx, goal, log="travel": walks.append(tuple(goal)) or True)
    ctx = _Ctx()
    for _ in range(routine.NO_TARGET_TICKS * routine.NO_TARGET_WALKS + 5):
        routine.tick(ctx)
        if ctx.state.stops:
            break
    assert walks == [(3442, 3542, 0)] * routine.NO_TARGET_WALKS
    assert "banshees" in ctx.state.stops[0].lower() and "nothing to fight" in ctx.state.stops[0]


def test_having_a_target_resets_the_watch(monkeypatch):
    _fighting(monkeypatch, 4412)
    ctx = _Ctx()
    ctx.state.data["slayer_no_target"] = 29
    routine.tick(ctx)
    assert ctx.state.data["slayer_no_target"] == 0


def test_an_aya_task_loots_only_the_whitelist(monkeypatch):
    seen = []
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(ctx.state.config["loot"]["minLootValue"]))
    ctx = _Ctx()
    ctx.state.config["loot"] = {"minLootValue": 5000, "names": ["Mystic gloves (dark)"]}
    routine.tick(ctx)
    assert seen[0] >= 10 ** 9 and ctx.state.config["loot"]["minLootValue"] == 5000


def test_no_usable_teleport_stops_with_the_reason(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Minotaurs", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    ctx = _Ctx()
    routine.tick(ctx)
    assert "Ornate Jewellery Box" in ctx.state.stops[0] and "minotaurs" in ctx.state.stops[0].lower()
