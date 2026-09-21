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
    # TRAVEL hands plan() what the account can PROVE (legs.requirement_values: varbits, item ids,
    # coins). By default a test proves nothing; a test about those reads fakes its own.
    monkeypatch.setattr(legs, "requirement_values", lambda: {
        "varbit_values": {}, "varplayer_values": {}, "owned_item_ids": None, "currency_held": None})


REAL_PACK = routine._pack
REAL_WEAR = routine._wear


@pytest.fixture(autouse=True)
def _pack_is_already_met(monkeypatch):
    """The BANK step (routine._pack, 2026-09-21) runs before TRAVEL. Every test written before it
    is about travel or the fight, so by default the pack is already met; the tests ABOUT packing
    put the real function back with monkeypatch.setattr(routine, "_pack", REAL_PACK)."""
    monkeypatch.setattr(routine, "_pack", lambda ctx, state, tasks_cfg, player: False)
    # ...and RESTOCK (2026-09-21) asks combat_core whether supplies ran out before every fight
    # tick. By default they have not; the restock tests say otherwise.
    monkeypatch.setattr(combat_core, "out_of_critical_supplies", lambda ctx: False)
    # ...and WEARING a task's required gear / restoring the base gear (2026-09-21). By default
    # everything is already worn and there is no saved base.
    monkeypatch.setattr(routine, "_wear", lambda ctx, state: False)
    monkeypatch.setattr(routine, "_base_gear", lambda ctx: None)
    monkeypatch.setattr(routine, "_capture_base_gear", lambda ctx, state: None)


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


def _carry(monkeypatch, names=(), ids=()):
    """Inventory as the bridge really serves it: a list of {"name", "qty", "id"}; equipment empty."""
    items = [{"name": n, "qty": 1, "id": i} for n, i in zip(names, list(ids) + [None] * len(names))]
    monkeypatch.setattr(controller, "get_inventory", lambda: list(items))
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)      # no pouch, unless a test says so
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 70})

def test_travel_runs_one_leg_per_tick_and_keeps_its_cursor(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    route = [legs.walk(2740, 3351, 0), legs.fairy("BLR", "Legends"), legs.walk(2700, 3330, 0)]
    planned = []
    monkeypatch.setattr(slayer_routes, "plan", lambda task, player, carried, skill_levels=None, proven=None: planned.append(task) or (route, ""))
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
    """PHASE-A REVIEW: a real route never answers FINISHED at cursor 0 -- it answers DONE per leg
    and FINISHED after the last. Resetting the counter on DONE made this stop unreachable and the
    re-plan loop unbounded; the old test hid it by faking FINISHED every call."""
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    plans = []
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: plans.append(1) or ([legs.walk(1, 1, 0)], ""))

    def run_next(ctx, legs_, cursor, log="travel"):
        return (legs.DONE, cursor + 1) if cursor < len(legs_) else (legs.FINISHED, cursor)
    monkeypatch.setattr(legs, "run_next", run_next)
    ctx = _Ctx()
    for _ in range(8):
        routine.tick(ctx)
        if ctx.state.stops:
            break
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
    """combat_core.maybe_loot reads loot["minValue"] (0 = no value looting) and loot["items"] (the
    whitelist). The first draft wrote "minLootValue", a key nothing reads."""
    seen = []
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(dict(ctx.state.config["loot"])))
    ctx = _Ctx()
    ctx.state.config["loot"] = {"minValue": 5000, "items": ["Mystic gloves (dark)"]}
    routine.tick(ctx)
    assert seen[0] == {"minValue": 0, "items": ["Mystic gloves (dark)"]}
    assert ctx.state.config["loot"]["minValue"] == 5000, "the panel config is untouched"

def test_no_usable_teleport_stops_with_the_reason(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Minotaurs", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    ctx = _Ctx()
    routine.tick(ctx)
    assert "Ornate Jewellery Box" in ctx.state.stops[0] and "minotaurs" in ctx.state.stops[0].lower()


def test_chasing_a_monster_a_few_tiles_out_of_the_rectangle_is_still_the_fight(monkeypatch):
    """PHASE-A REVIEW: the plugin's rectangles are tight (cave slimes 14x8). Two tiles outside must
    not flip the routine into TRAVEL and re-run a teleport route."""
    state = slayer_state.from_bridge(_task("Banshees"))
    inside_margin = {"x": 3431 - 4, "y": 3545, "plane": 0}
    far = {"x": 3431 - 20, "y": 3545, "plane": 0}
    assert routine.derive_phase(state, TASKS, inside_margin) == routine.FIGHT
    assert routine.derive_phase(state, TASKS, far) == routine.TRAVEL


def test_the_carried_map_holds_item_ids_so_a_lit_lantern_can_be_told_from_an_unlit_one(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Cave slimes", count=17))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2700, "y": 3321, "plane": 0})
    ran = []
    monkeypatch.setattr(legs, "run_next", lambda *a, **k: ran.append(1) or (legs.DONE, 1))
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: ([legs.walk(1, 1, 0)], ""))
    _carry(monkeypatch, names=["Bullseye lantern"], ids=[4548])          # UNLIT: same name
    ctx = _Ctx()
    routine.tick(ctx)
    assert ran == [] and "light source" in ctx.state.stops[0]
    _carry(monkeypatch, names=["Bullseye lantern"], ids=[4550])          # lit
    ctx = _Ctx()
    routine.tick(ctx)
    assert ran == [1] and ctx.state.stops == []


def test_travel_hands_the_planner_what_the_account_proves(monkeypatch):
    """The whirlpool's varbit and a dusty key open gated transports; plan() refuses a teleport
    whose onward walk it cannot prove, so TRAVEL must pass the proof along."""
    seen = {}
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 77})
    monkeypatch.setattr(legs, "requirement_values", lambda: {"varbit_values": {3759: 3}})

    def plan(task, player, carried, skill_levels=None, proven=None):
        seen.update(skill_levels=skill_levels, proven=proven)
        return None, "stop here"
    monkeypatch.setattr(slayer_routes, "plan", plan)
    routine.tick(_Ctx())
    assert seen == {"skill_levels": {"Agility": 77}, "proven": {"varbit_values": {3759: 3}}}


def test_a_carried_rune_pouchs_runes_are_carried_and_a_banked_pouchs_are_not(monkeypatch):
    """The real GET /rune-pouch of 2026-09-20 reported its runes with carried=false."""
    _carry(monkeypatch, names=["Law rune"])
    pouch = {"carried": False, "total": 9, "runes": [{"quantity": 8949, "name": "Cosmic rune", "id": 564}]}
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: pouch)
    assert "cosmic rune" not in routine._carried()
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: dict(pouch, carried=True))
    carried = routine._carried()
    assert carried["cosmic rune"] == 8949 and carried["law rune"] == 1


# --- 2026-09-21, self-review: the FIGHT phase predates Kuradal's catalog -------------------------

def test_a_kuradal_task_with_nothing_to_fight_walks_back_to_its_own_site(monkeypatch):
    """_fight looked the site up in aya_tasks ONLY, so for a Kuradal task it stopped with 'its
    site is not in the catalog' -- untrue since slayer_catalog arrived. It now reads the same
    site record at_site and plan() read, and walks to the WALK GOAL."""
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Nechryael", count=150))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3412, "y": 9962, "plane": 3})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: None)
    walks = []
    monkeypatch.setattr(legs, "walk_across", lambda ctx, goal, log="travel": walks.append(tuple(goal)) or True)
    ctx = _Ctx()
    for _ in range(routine.NO_TARGET_TICKS + 1):
        routine.tick(ctx)
    assert walks == [(3412, 9962, 3)]
    assert ctx.state.stops == []


def test_a_kuradal_task_loots_by_the_panels_value_not_ayas_whitelist_only_rule(monkeypatch):
    """'Aya's monsters drop nothing worth a click' was applied to EVERY task. Gargoyles and
    nechryael drop plenty: a Kuradal task keeps the panel's minValue."""
    seen = []
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Nechryael", count=150))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3412, "y": 9962, "plane": 3})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(dict(ctx.state.config["loot"])))
    ctx = _Ctx()
    ctx.state.config["loot"] = {"minValue": 5000, "items": ["Rune boots"]}
    routine.tick(ctx)
    assert seen[0]["minValue"] == 5000 and seen[0]["items"] == ["Rune boots"]
    assert callable(seen[0].get("makeRoom")), "...and a catalog task may give a slot up for a big drop"


# --- 2026-09-21: the BANK step before TRAVEL (plan task C2), acting through banker.bank_up --------

PANEL = {"food": {"name": "Shark"}, "potions": {"prayerPotion": "Prayer potion"},
         "bank": {"keepItems": ["rune pouch"], "bankNames": ["Bank chest"]}}
BANKED = {"shark": 500, "prayer potion(4)": 80, "teleport to house": 40, "ring of dueling(8)": 6,
          "ectophial": 1}
LUMBRIDGE = {"x": 3200, "y": 3200, "plane": 0}


class _Memory:
    def __init__(self, items):
        self.seen = items is not None
        self.items = [{"name": n, "quantity": q} for n, q in (items or {}).items()]


def _packing(monkeypatch, inventory, banked, task="Cows", count=20):
    """The REAL _pack, over a fake world. `inventory` is the bridge's own shape."""
    calls = {"bank": []}
    monkeypatch.setattr(routine, "_pack", REAL_PACK)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(task, count=count))
    monkeypatch.setattr(controller, "get_player", lambda: dict(LUMBRIDGE))
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory))
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)
    monkeypatch.setattr(controller, "get_skills", lambda: {"Prayer": {"level": 70}})
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 70})
    monkeypatch.setattr(routine.bank_memory, "load", lambda names=None: _Memory(banked))
    monkeypatch.setattr(routine.banker, "bank_up", lambda ctx, cfg: calls["bank"].append(cfg) or True)
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: ([legs.walk(1, 1, 0)], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.DONE, cursor + 1))
    ctx = _Ctx()
    ctx.state.config = dict(PANEL)
    return ctx, calls


def _inv(*pairs):
    return [{"name": n, "qty": q, "id": None} for n, q in pairs]


def test_a_pack_that_is_short_banks_once_with_the_loadout_and_does_not_travel_that_tick(monkeypatch):
    ctx, calls = _packing(monkeypatch, _inv(("Bones", 1)), BANKED)
    routine.tick(ctx)
    assert len(calls["bank"]) == 1 and ctx.state.stops == []
    cfg = calls["bank"][0]
    want = {w["name"]: w["quantity"] for w in cfg["withdraw"]}
    assert want["Shark"] == 3 and want["Ectophial"] == 1 and want["Teleport to house"] == 2
    assert cfg["returnToSpot"] is False and cfg["enabled"] is True
    assert "rune pouch" in cfg["keepItems"], "the panel's own keep list is kept"
    assert "shark" in cfg["keepItems"] and "ectophial" in cfg["keepItems"]
    assert cfg["bankNames"] == ["Bank chest"], "the panel's bank settings ride along"
    assert not ctx.state.data.get("slayer_route"), "no journey was planned on the banking tick"


def test_a_met_pack_travels_at_once_and_is_not_asked_again(monkeypatch):
    held = _inv(("Shark", 1), ("Shark", 1), ("Shark", 1), ("Ectophial", 1), ("Teleport to house", 5),
                ("Ring of dueling(8)", 1))
    ctx, calls = _packing(monkeypatch, held, BANKED)
    routine.tick(ctx)
    assert calls["bank"] == [] and ctx.state.data.get("slayer_route"), "straight to TRAVEL"
    assert ctx.state.data["slayer_pack_ok"] == ("cows", 12)


def test_still_short_after_the_bank_visit_is_a_stop_naming_the_items_never_a_loop(monkeypatch):
    ctx, calls = _packing(monkeypatch, _inv(("Bones", 1)), BANKED)
    routine.tick(ctx)                       # banks
    routine.tick(ctx)                       # the inventory never changed: still short
    assert len(calls["bank"]) == 1, "banked ONCE"
    assert ctx.state.stops and "still short" in ctx.state.stops[0] and "Shark" in ctx.state.stops[0]


def test_an_item_nobody_holds_stops_before_any_bank_trip_and_never_spends_points(monkeypatch):
    """The plan allows a SKIP here; bank memory can be stale and a skip costs 30 points, so a
    missing item is always a STOP (ruling 2026-09-21)."""
    ctx, calls = _packing(monkeypatch, _inv(), {k: v for k, v in BANKED.items() if k != "ectophial"})
    routine.tick(ctx)
    assert calls["bank"] == [] and "ectophial" in ctx.state.stops[0].lower()


def test_an_empty_panel_now_banks_for_whatever_the_bank_holds(monkeypatch):
    """SUPERSEDED 2026-09-21 by the USER: "do it any food any prayer but not wilderness food."

    This used to assert a STOP. That was the single biggest blocker on the live account: both panel
    names were empty strings, so no task ever reached a bank. The pack now chooses a food itself
    and gets on with the trip."""
    ctx, calls = _packing(monkeypatch, _inv(), BANKED)
    ctx.state.config = {"food": {}, "potions": {}}

    routine.tick(ctx)

    assert calls["bank"], "it goes, instead of stopping"
    assert ctx.state.stops == []


def test_a_bank_with_nothing_but_wilderness_food_still_stops(monkeypatch):
    """The other half of the same change: choosing is not the same as settling. Blighted supplies
    cannot be eaten at any site in the catalog, so a bank holding only those is still a stop."""
    # The real bank MINUS its ordinary food, so the stop is about the food and not about the
    # teleports BANKED also supplies.
    wilderness_only = {k: v for k, v in BANKED.items() if k != "shark"}
    wilderness_only["blighted anglerfish"] = 1319
    ctx, calls = _packing(monkeypatch, _inv(), wilderness_only)
    ctx.state.config = {"food": {}, "potions": {}}

    routine.tick(ctx)

    assert calls["bank"] == []
    assert "food" in ctx.state.stops[0].lower()


def test_a_bank_never_seen_is_visited_on_faith_then_judged_on_what_it_really_holds(monkeypatch):
    ctx, calls = _packing(monkeypatch, _inv(), None)
    routine.tick(ctx)
    assert len(calls["bank"]) == 1 and ctx.state.stops == [], "unknown is not empty: go and look"


def test_near_the_site_the_pack_is_never_reconsidered(monkeypatch):
    """Two sharks eaten and a monster chased out of the rectangle must not send the routine to a
    bank mid-task. Running out AT the site is the RESTOCK phase's job, not this one's."""
    ctx, calls = _packing(monkeypatch, _inv(("Shark", 1)), BANKED)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3655, "y": 3525, "plane": 0})   # by Alice's farm
    routine.tick(ctx)
    assert calls["bank"] == [] and ctx.state.stops == []


def test_a_journey_under_way_is_never_turned_back_to_the_bank(monkeypatch):
    ctx, calls = _packing(monkeypatch, _inv(("Bones", 1)), BANKED)
    ctx.state.data.update({"slayer_streak_seen": 12, "slayer_route": [legs.walk(1, 1, 0)],
                           "slayer_route_for": ("cows", 12), "slayer_leg": 0})
    routine.tick(ctx)
    assert calls["bank"] == []


def test_a_bank_trip_that_keeps_failing_is_a_stop(monkeypatch):
    ctx, calls = _packing(monkeypatch, _inv(("Bones", 1)), BANKED)
    monkeypatch.setattr(routine.banker, "bank_up", lambda ctx, cfg: calls["bank"].append(cfg) and False)
    for _ in range(routine.BANK_MAX_FAILS + 2):
        routine.tick(ctx)
        if ctx.state.stops:
            break
    assert len(calls["bank"]) == routine.BANK_MAX_FAILS and "bank" in ctx.state.stops[0].lower()


# --- 2026-09-21: RESTOCK (plan task D1) -- out of food or prayer AT the site --------------------

AT_THE_FARM = {"x": 3630, "y": 3530, "plane": 0}


def _restocking(monkeypatch, out_of_supplies=True, banked=BANKED, bank_ok=True):
    ctx, calls = _packing(monkeypatch, _inv(("Ectophial", 1), ("Teleport to house", 2), ("Ring of dueling(8)", 1)), banked)
    world = {"player": dict(AT_THE_FARM)}
    calls["fight"] = []
    monkeypatch.setattr(controller, "get_player", lambda: dict(world["player"]))
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "out_of_critical_supplies", lambda ctx: out_of_supplies)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: calls["fight"].append(1))

    def bank_up(ctx, cfg):
        calls["bank"].append(cfg)
        world["player"] = {"x": 3150, "y": 3635, "plane": 0}          # Ferox: the ring of dueling
        return bank_ok
    monkeypatch.setattr(routine.banker, "bank_up", bank_up)
    return ctx, calls, world


def test_out_of_supplies_at_the_site_leaves_banks_to_the_same_pack_and_does_not_fight(monkeypatch):
    ctx, calls, _ = _restocking(monkeypatch)
    ctx.state.data["current_target_index"] = 4412
    routine.tick(ctx)
    assert calls["fight"] == [] and len(calls["bank"]) == 1
    want = {w["name"]: w["quantity"] for w in calls["bank"][0]["withdraw"]}
    assert want["Shark"] == 3, "the SAME profile the task was packed to"
    assert ctx.state.data["slayer_restocks"] == 1 and ctx.state.data.get("current_target_index") is None
    assert ctx.state.data.get("slayer_restocking") is None, "done: the next tick travels back"
    assert ctx.state.stops == []


def test_a_restock_that_fails_halfway_keeps_banking_instead_of_walking_back_empty(monkeypatch):
    """bank_up can teleport to Ferox and THEN fail to open the bank. The player is no longer at
    the site, so the phase reads TRAVEL -- and without the flag the routine would travel back to
    the fight with nothing to eat."""
    ctx, calls, _ = _restocking(monkeypatch, bank_ok=False)
    routine.tick(ctx)                                   # at the site: starts the restock, fails
    assert ctx.state.data["slayer_restocking"] == ("cows", 12)
    routine.tick(ctx)                                   # now at Ferox: STILL the restock
    assert len(calls["bank"]) == 2 and not ctx.state.data.get("slayer_route")


def test_the_fifth_restock_of_one_task_is_a_stop_naming_it(monkeypatch):
    ctx, calls, world = _restocking(monkeypatch)
    ctx.state.data.update({"slayer_streak_seen": 12, "slayer_restocks": routine.RESTOCK_MAX_PER_TASK})
    routine.tick(ctx)
    assert calls["bank"] == [] and "restock" in ctx.state.stops[0].lower() and "cows" in ctx.state.stops[0].lower()


def test_a_restock_the_bank_cannot_supply_stops_naming_what_ran_out(monkeypatch):
    ctx, calls, _ = _restocking(monkeypatch, banked=dict(BANKED, shark=0))
    routine.tick(ctx)
    assert calls["bank"] == [] and "shark" in ctx.state.stops[0].lower()


def test_with_supplies_left_the_fight_goes_on(monkeypatch):
    ctx, calls, _ = _restocking(monkeypatch, out_of_supplies=False)
    routine.tick(ctx)
    assert calls["fight"] == [1] and calls["bank"] == []


# --- 2026-09-21: WEARING what a task requires, and putting the base gear back (plan task C3) ------

BASE = {"weapon": {"name": "Arclight", "id": 19675, "qty": 1},
        "shield": {"name": "Dragon defender", "id": 12954, "qty": 1},
        "helmet": {"name": "Slayer helmet (i)", "id": 26674, "qty": 1}}


def _gearing(monkeypatch, task, worn, inventory, player=None):
    """The REAL _wear over a fake wardrobe: equipping moves an item from the inventory to its slot
    and drops whatever was there back into the inventory, as the game does."""
    world = {"worn": {k: dict(v) for k, v in worn.items()}, "inv": list(inventory), "equips": []}
    monkeypatch.setattr(routine, "_wear", REAL_WEAR)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(task, count=150))
    monkeypatch.setattr(controller, "get_player", lambda: dict(player or LUMBRIDGE))
    monkeypatch.setattr(controller, "get_equipment", lambda: {k: dict(v) for k, v in world["worn"].items()})
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": n, "qty": 1, "id": None} for n in world["inv"]])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)
    slots = {"mirror shield": "shield", "v's shield": "shield", "boots of stone": "boots", "dragon defender": "shield",
             "leaf-bladed battleaxe": "weapon", "godsword": "weapon"}

    def equip(ctx, name):
        item = next((n for n in world["inv"] if name.lower() in n.lower()), None)
        if item is None:
            return False
        slot = slots[name.lower()]
        world["inv"].remove(item)
        if slot in world["worn"]:
            world["inv"].append(world["worn"][slot]["name"])
        world["worn"][slot] = {"name": item, "id": None, "qty": 1}
        if slot == "shield" and "godsword" in (world["worn"].get("weapon", {}).get("name") or "").lower():
            world["inv"].append(world["worn"].pop("weapon")["name"])       # a 2h weapon comes off
        world["equips"].append(item)
        return True
    monkeypatch.setattr(routine.gear, "equip_from_inventory", equip)
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 77})
    monkeypatch.setattr(legs, "requirement_values", lambda: {"varbit_values": {}, "varplayer_values": {}})
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: ([legs.walk(1, 1, 0)], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.DONE, cursor + 1))
    return _Ctx(), world


def test_a_required_item_in_the_pack_is_put_ON_before_travelling(monkeypatch):
    """'Carried or worn' passes the requirement check, but a mirror shield in the inventory does
    nothing against a basilisk. It is worn before the journey starts -- one equip per tick."""
    ctx, world = _gearing(monkeypatch, "Basilisks", BASE, ["Mirror shield", "Shark"])
    routine.tick(ctx)
    assert world["equips"] == ["Mirror shield"] and not ctx.state.data.get("slayer_route")
    assert world["worn"]["shield"]["name"] == "Mirror shield" and "Dragon defender" in world["inv"]
    routine.tick(ctx)
    assert ctx.state.data.get("slayer_route"), "now it travels"
    assert ctx.state.stops == []


def test_what_is_already_worn_costs_nothing(monkeypatch):
    ctx, world = _gearing(monkeypatch, "Aberrant spectres", BASE, ["Shark"])
    routine.tick(ctx)
    assert world["equips"] == [] and ctx.state.data.get("slayer_route"), "the slayer helmet IS the nose peg"


def test_a_shield_that_knocks_a_two_handed_weapon_off_is_a_stop_not_a_fist_fight(monkeypatch):
    """The user's OPEN gear decision (plan E0): a one-handed weapon beside the mirror shield.
    Until it is made, the routine must never walk into basilisks bare-handed."""
    two_handed = dict(BASE, weapon={"name": "Armadyl godsword", "id": 11802, "qty": 1})
    two_handed.pop("shield")
    ctx, world = _gearing(monkeypatch, "Basilisks", two_handed, ["Mirror shield"])
    routine.tick(ctx)
    assert ctx.state.stops and "weapon" in ctx.state.stops[0].lower() and "basilisks" in ctx.state.stops[0].lower()


def test_a_requirement_that_cannot_be_worn_is_a_stop_naming_it(monkeypatch):
    ctx, world = _gearing(monkeypatch, "Basilisks", BASE, ["Shark"])
    routine.tick(ctx)
    assert ctx.state.stops and "mirror shield" in ctx.state.stops[0].lower()


def test_an_aya_task_wears_nothing_special(monkeypatch):
    ctx, world = _gearing(monkeypatch, "Cows", BASE, ["Shark"])
    routine.tick(ctx)
    assert world["equips"] == [] and ctx.state.data.get("slayer_route")


def test_the_base_gear_is_put_back_before_the_next_task_is_asked_for(monkeypatch, tmp_path):
    """The defender the mirror shield displaced rides in the inventory for the whole task. With
    the task done it goes back on BEFORE the next master is contacted."""
    ctx, world = _gearing(monkeypatch, "Basilisks", dict(BASE, shield={"name": "Mirror shield", "id": 4156, "qty": 1}),
                          ["Dragon defender", "Shark"])
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    contacted = []
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: contacted.append(master) or "stop here")
    monkeypatch.setattr(routine, "_base_gear", lambda ctx: {"weapon": "Arclight", "shield": "Dragon defender",
                                                           "helmet": "Slayer helmet (i)"})
    routine.tick(ctx)
    assert world["equips"] == ["Dragon defender"] and contacted == [], "gear first, the master after"
    routine.tick(ctx)
    assert contacted, "restored: now it asks for the next task"


def test_displaced_base_gear_is_on_the_keep_list_so_the_bank_never_swallows_it():
    class Lo:
        withdraw, keep = [], ["shark"]
    cfg = routine._bank_config({"bank": {"keepItems": ["rune pouch"]}}, Lo,
                               base={"weapon": "Arclight", "shield": "Dragon defender"})
    assert "dragon defender" in cfg["keepItems"] and "arclight" in cfg["keepItems"]
    assert "rune pouch" in cfg["keepItems"] and "shark" in cfg["keepItems"]


def test_an_empty_equipment_slot_is_an_empty_dict_not_a_missing_key(monkeypatch):
    """FOUND LIVE 2026-09-21, the verbatim GET /equipment of an account in ranged gear: an empty
    slot is {} -- PRESENT with no name. The two-handed check tested `"weapon" in equipment`, which
    is True either way, so it could never have fired. The account's Toxic blowpipe IS two-handed,
    so this is the exact case it was written for."""
    live = {"weapon": {"qty": 1, "name": "Toxic blowpipe", "id": 12926}, "shield": {},
            "gloves": {"qty": 1, "name": "Barrows gloves", "id": 7462},
            "helmet": {"qty": 1, "name": "Slayer helmet (i)", "id": 26674}}
    assert routine._holds(live, "weapon") and not routine._holds(live, "shield")
    assert not routine._holds({}, "weapon") and not routine._holds(None, "weapon")

    # ...and end to end: the blowpipe comes off when the mirror shield goes on, and that STOPS.
    world = {"eq": {k: dict(v) for k, v in live.items()}}
    monkeypatch.setattr(routine, "_wear", REAL_WEAR)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Basilisks", count=150))
    monkeypatch.setattr(controller, "get_player", lambda: dict(LUMBRIDGE))
    monkeypatch.setattr(controller, "get_equipment", lambda: {k: dict(v) for k, v in world["eq"].items()})
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Mirror shield", "qty": 1, "id": 4156}])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)

    def equip(ctx, name):
        world["eq"]["shield"] = {"qty": 1, "name": "Mirror shield", "id": 4156}
        world["eq"]["weapon"] = {}                     # two-handed: it comes off
        return True
    monkeypatch.setattr(routine.gear, "equip_from_inventory", equip)
    ctx = _Ctx()
    routine.tick(ctx)
    assert ctx.state.stops and "weapon" in ctx.state.stops[0].lower()


# --- 2026-09-21: loot tiers (plan task D2) ------------------------------------------------------

BIG = {"name": "Abyssal whip", "id": 4151, "totalValue": 1_500_000, "stackable": False}
MID = {"name": "Rune platebody", "id": 1127, "totalValue": 60_000, "stackable": False}
SMALL = {"name": "Rune dagger", "id": 1213, "totalValue": 15_000, "stackable": False}
COINS = {"name": "Coins", "id": 995, "totalValue": 40_000, "stackable": True}


def _room(monkeypatch, inventory, hp=(99, 99), consumables=None, task="Nechryael"):
    acts = {"eat": [], "drink": [], "drop": []}
    monkeypatch.setattr(controller, "get_inventory", lambda: [dict(i) for i in inventory])
    monkeypatch.setattr(controller, "get_player", lambda: {"hp": hp[0], "hpReal": hp[1]})
    monkeypatch.setattr(controller, "get_consumables", lambda: consumables or {"food": [], "prayer": []})
    monkeypatch.setattr(controller, "consume_first",
                        lambda ser, category: acts["eat" if category == "food" else "drink"].append(category) or True)
    monkeypatch.setattr(controller, "drop_items", lambda ser, items: acts["drop"].extend(i["name"] for i in items) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, item, **kw: acts["drink"].append(item["name"]) or True)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: None)
    ctx = _Ctx()
    ctx.state.config = dict(PANEL, tasks={"makeRoomValue": 50_000, "mustLootValue": 150_000})
    ctx.state.data["slayer_task"] = task
    return ctx, acts


def _junk(n, name="Junk"):
    return [{"name": f"{name} {i}", "id": 900 + i, "qty": 1} for i in range(n)]


def test_a_cheap_drop_into_a_full_inventory_is_left(monkeypatch):
    ctx, acts = _room(monkeypatch, _junk(28), hp=(50, 99))
    assert routine._make_room(ctx, SMALL) is False
    assert acts == {"eat": [], "drink": [], "drop": []}


def test_a_make_room_drop_eats_only_when_hp_is_missing(monkeypatch):
    held = _junk(27) + [{"name": "Shark", "id": 385, "qty": 1}]
    ctx, acts = _room(monkeypatch, held, hp=(50, 99), consumables={"food": [{"name": "Shark"}]})
    assert routine._make_room(ctx, MID) is True and acts["eat"] == ["food"]
    full_hp, acts2 = _room(monkeypatch, held, hp=(99, 99), consumables={"food": [{"name": "Shark"}]})
    assert routine._make_room(full_hp, MID) is False and acts2["eat"] == []


def test_a_must_loot_drop_is_taken_even_at_full_health(monkeypatch):
    held = _junk(27) + [{"name": "Shark", "id": 385, "qty": 1}]
    ctx, acts = _room(monkeypatch, held, hp=(99, 99), consumables={"food": [{"name": "Shark"}]})
    assert routine._make_room(ctx, BIG) is True and acts["eat"] == ["food"]


def test_a_must_loot_drop_with_no_food_finishes_a_one_dose_potion(monkeypatch):
    held = _junk(27) + [{"name": "Prayer potion(1)", "id": 143, "qty": 1}]
    ctx, acts = _room(monkeypatch, held, hp=(99, 99))
    assert routine._make_room(ctx, BIG) is True and acts["drink"] == ["Prayer potion(1)"]


def test_a_must_loot_drop_with_nothing_to_consume_drops_the_cheapest_consumable(monkeypatch):
    held = _junk(26) + [{"name": "Antipoison(2)", "id": 177, "qty": 1},
                        {"name": "Prayer potion(4)", "id": 2434, "qty": 1}]
    ctx, acts = _room(monkeypatch, held, hp=(99, 99))
    assert routine._make_room(ctx, BIG) is True and acts["drop"] == ["Antipoison(2)"]


def test_nothing_safe_to_give_up_leaves_even_a_huge_drop(monkeypatch):
    ctx, acts = _room(monkeypatch, _junk(28), hp=(99, 99))
    assert routine._make_room(ctx, BIG) is False
    assert acts == {"eat": [], "drink": [], "drop": []}


def test_an_item_the_task_needs_is_never_dropped_for_loot(monkeypatch):
    """Gargoyles: the rock hammer finishes them. Dropping it to make room for loot would end the
    task. The teleport home and the house tablets are protected the same way."""
    held = _junk(25) + [{"name": "Rock hammer", "id": 4162, "qty": 1},
                        {"name": "Teleport to house", "id": 8013, "qty": 3},
                        {"name": "Slayer ring (2)", "id": 11871, "qty": 1}]
    ctx, acts = _room(monkeypatch, held, hp=(99, 99), task="Gargoyles")
    assert routine._make_room(ctx, BIG) is False, "nothing droppable: the rest is gear or the way home"
    assert acts["drop"] == []


def test_a_stack_already_held_needs_no_room_at_all(monkeypatch):
    held = _junk(27) + [{"name": "Coins", "id": 995, "qty": 5000}]
    ctx, acts = _room(monkeypatch, held, hp=(99, 99))
    assert routine._make_room(ctx, COINS) is True
    assert acts == {"eat": [], "drink": [], "drop": []}, "it simply stacks"


def test_the_fight_passes_the_hook_only_for_a_catalog_task(monkeypatch):
    """Aya's tasks loot the whitelist only, so there is nothing to make room FOR."""
    seen = []
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Nechryael", count=150))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3412, "y": 9962, "plane": 3})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(ctx.state.config["loot"]))
    ctx = _Ctx()
    ctx.state.config = dict(ctx.state.config, loot={"minValue": 5000, "items": []})
    routine.tick(ctx)
    assert callable(seen[0].get("makeRoom"))
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Cows", count=20))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3630, "y": 3530, "plane": 0})
    routine.tick(ctx)
    assert seen[1].get("makeRoom") is None and seen[1]["minValue"] == 0


# --- 2026-09-21: a BREAK must not be taken standing next to the monster --------------------------

def _breaking(monkeypatch, at_site=True, task="Nechryael", teleport=True):
    calls = {"teleport": []}
    tile = {"x": 3412, "y": 9962, "plane": 3} if at_site else {"x": 3200, "y": 3200, "plane": 0}
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(task, count=150))
    monkeypatch.setattr(controller, "get_player", lambda: dict(tile))
    monkeypatch.setattr(routine.travel, "teleport_to_ferox",
                        lambda ctx: calls["teleport"].append(1) or teleport)
    ctx = _Ctx()
    ctx.state.data.update({"current_target_index": 4412, "slayer_route": [legs.walk(1, 1, 0)],
                           "slayer_route_for": ("nechryael", 12), "slayer_leg": 0})
    return ctx, calls


def test_a_break_at_the_site_leaves_for_somewhere_nothing_attacks(monkeypatch):
    """tick() is skipped for the WHOLE break (3-12 minutes on this account), so nothing eats and
    nothing prays. Standing next to a nechryael for that long is a death. before_break runs fully
    live, before the break starts, so it teleports out -- Ferox Enclave, which is safe and has a
    pool and a bank."""
    ctx, calls = _breaking(monkeypatch)
    routine.before_break(ctx)
    assert calls["teleport"] == [1]
    assert ctx.state.data.get("current_target_index") is None, "the fight is let go of first"
    assert not ctx.state.data.get("slayer_route"), "the way back is re-planned after the break"


def test_a_break_while_travelling_or_banking_costs_nothing(monkeypatch):
    """Away from the site nothing is attacking us, so a break needs no teleport and no charge."""
    ctx, calls = _breaking(monkeypatch, at_site=False)
    routine.before_break(ctx)
    assert calls["teleport"] == []
    assert ctx.state.data.get("slayer_route"), "the journey is left exactly as it was"


def test_a_break_with_no_task_does_nothing(monkeypatch):
    ctx, calls = _breaking(monkeypatch, task=None)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    routine.before_break(ctx)
    assert calls["teleport"] == []


def test_a_failed_escape_does_not_stop_the_run(monkeypatch):
    """No ring of dueling left: the break happens anyway, exactly as it does today. Saying so in
    the log is all that can be done -- refusing the break is not this hook's call."""
    ctx, calls = _breaking(monkeypatch, teleport=False)
    routine.before_break(ctx)
    assert calls["teleport"] == [1] and ctx.state.stops == []


def test_a_break_never_runs_while_the_bridge_is_silent(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: None)
    ctx, calls = _breaking(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: None)
    routine.before_break(ctx)
    assert calls["teleport"] == []
