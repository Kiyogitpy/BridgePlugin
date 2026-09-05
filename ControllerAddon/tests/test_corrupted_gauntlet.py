"""routines/corrupted_gauntlet.py -- the I/O layer over hunllef.py. Bridge fully stubbed."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import hunllef as h
import gauntlet_ids as g
import corrupted_gauntlet as cg


@pytest.fixture(autouse=True)
def no_live_bridge(monkeypatch):
    for name in ("get_player", "get_npcs", "get_objects", "get_prayers", "get_equipment", "get_inventory",
                 "get_combat", "get_ground_walk_point", "get_minimap_walk_point", "get_collision_grid"):
        monkeypatch.setattr(controller, name, lambda *a, _n=name, **kw: pytest.fail(f"live bridge call {_n}"))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


class _State:
    def __init__(self):
        self.data = {}
        self.safety_stopped = False
        self.stops = []

    def trigger_safety_stop(self, msg):
        self.safety_stopped = True
        self.stops.append(msg)


class _Ctx:
    def __init__(self, events=None):
        self.ser = object()
        self.state = _State()
        self.events = events


def _npc(id_, x=13105, y=5700, anim=-1, index=1):
    return {"id": id_, "index": index, "instX": x, "instY": y, "animationId": anim, "hpRatio": 90,
            "name": "Corrupted Hunllef", "screenX": 500, "screenY": 400}


def test_derive_phase():
    data = {}
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_BARRIER}]) == "enter"
    assert cg.derive_phase(data, npcs=[_npc(9036)], objects=[]) == "fight"
    assert data["cg_fight_seen"] is True
    assert cg.derive_phase(data, npcs=[_npc(9038)], objects=[]) == "fight"         # dying, still the fight phase
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "loot"
    data["cg_chest_opened"] = True
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "done"
    assert cg.derive_phase({}, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "unknown"   # no fight seen this run
    assert cg.derive_phase({}, npcs=[], objects=[]) == "unknown"


def test_read_snapshot_composes_the_seven_reads(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 70, "prayer": 40, "instX": 1, "instY": 2, "plane": 1, "animation": -1})
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [_npc(9036)])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_prayers", lambda: {"active": {"PROTECT_FROM_MISSILES": True}})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (perfected)"}})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": 9036})
    s = cg.read_snapshot()
    assert s.hp == 70 and s.hunllef["id"] == 9036 and s.weapon_kind() == "bow" and s.target_id == 9036
    monkeypatch.setattr(controller, "get_player", lambda: {})
    assert cg.read_snapshot() is None


def test_feed_tracker_prefers_events_and_falls_back_to_snapshot_transitions():
    t = h.StyleTracker()
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036, anim=8419)], [], {}, {}, [], {})
    cg.feed_tracker(t, snap, [("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": -1}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9040, "animationId": 8419})])
    assert t.attacks == 2                                   # two Hunllef attacks; the rat's is ignored
    t2 = h.StyleTracker()
    cg.feed_tracker(t2, snap, None)                         # no events stream: snapshot transition
    assert t2.attacks == 1
    cg.feed_tracker(t2, snap, None)
    assert t2.attacks == 1                                  # same animation again: no new attack


def test_do_prayer_opens_the_tab_and_clicks_the_icon(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "icons": {"PROTECT_FROM_MAGIC": {"screenX": 1, "screenY": 2}}, "active": {"PROTECT_FROM_MAGIC": True}})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: clicks.append((entity, kw)) or True)
    ctx = _Ctx()
    assert cg.do_prayer(ctx, None, "PROTECT_FROM_MAGIC", cg.DEFAULT_CONFIG)
    entity, kw = clicks[0]
    assert entity["screenX"] == 1 and kw["check_ui_blockers"] is False and kw["simulate_mistakes"] is False and kw["offset"] == (0, 0)


def test_do_prayer_marks_a_missing_icon_unusable(monkeypatch):
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "icons": {}, "active": {}})
    ctx = _Ctx()
    assert not cg.do_prayer(ctx, None, "RIGOUR", cg.DEFAULT_CONFIG)
    assert "RIGOUR" in ctx.state.data["cg_unusable_prayers"]


def test_do_escape_shift_clicks_a_safe_tile_on_screen(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: {"baseX": 13056, "baseY": 5632, "plane": 1, "size": 32, "flags": [0] * 1024})
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, plane, inst=False: {"onScreen": True, "screenX": 5, "screenY": 6, "_t": (x, y, inst)})
    monkeypatch.setattr(cg.travel, "_click_walk_point", lambda ctx, point, shift: clicks.append((point, shift)))
    snap = h.FightSnapshot.from_bridge({"instX": 13066, "instY": 5642, "plane": 1, "hp": 50, "prayer": 30},
                                       [_npc(9036, 13072, 5642, 8419)], [{"id": 36048, "instX": 13066, "instY": 5642}], {}, {}, [], {})
    assert cg.do_escape(_Ctx(), snap, "hazard", cg.DEFAULT_CONFIG)
    point, shift = clicks[0]
    assert shift is True and point["_t"][2] is True
    tx, ty = point["_t"][0], point["_t"][1]
    assert not h.forbidden_near_hunllef((tx, ty), (13072, 5642)) and (tx, ty) != (13066, 5642)


def test_do_escape_returns_false_without_a_map(monkeypatch):
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: None)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2, "plane": 1}, [_npc(9036)], [], {}, {}, [], {})
    assert not cg.do_escape(_Ctx(), snap, "tornado", cg.DEFAULT_CONFIG)


def test_do_eat_clicks_a_fish_after_opening_the_inventory(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: calls.append("inv") or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(("click", entity["name"], kw.get("expected_action"))) or True)
    inv = [{"slot": 0, "name": "Paddlefish", "screenX": 1, "screenY": 1}, {"slot": 1, "name": "Corrupted paddlefish", "screenX": 2, "screenY": 1}]
    snap = h.FightSnapshot.from_bridge({"hp": 30, "instX": 1, "instY": 2}, [_npc(9036)], [], {}, {}, inv, {})
    assert cg.do_eat(_Ctx(), snap, None, cg.DEFAULT_CONFIG)
    assert calls == ["inv", ("click", "Corrupted paddlefish", "Eat")]


def test_do_attack_escalates_a_withheld_click_to_the_menu(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda index: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append("click") or controller.CLICK_WITHHELD)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: calls.append(("menu", option)) or True)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036)], [], {}, {}, [], {})
    assert cg.do_attack(_Ctx(), snap, None, cg.DEFAULT_CONFIG)
    assert calls == ["click", ("menu", "Attack")]


def test_enter_phase_has_attempt_ceiling(monkeypatch):
    clicks = []
    inv = [{"name": f"Corrupted paddlefish", "screenX": 1, "screenY": 1} for _ in range(10)]
    inv.append({"name": "Egniol potion", "screenX": 2, "screenY": 2})
    inv.append({"name": "Corrupted staff (perfected)", "screenX": 3, "screenY": 3})  # staff in inventory
    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 70, "prayer": 40, "instX": 1, "instY": 2, "plane": 1, "animation": -1})
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "active": {"PROTECT_FROM_MISSILES": True}, "icons": {}})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (perfected)"}})
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [{"id": g.OBJ_BARRIER, "screenX": 500, "screenY": 400}])
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])  # no Hunllef spawned
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": -1})
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: clicks.append(option) or True)
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    def fake_confirm(ctx, check, budget_s=None):
        return False  # never confirms Hunllef spawn
    monkeypatch.setattr(cg, "_confirm", fake_confirm)
    ctx = _Ctx()
    # Call enter_phase three times: should click the barrier three times
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 1 and clicks[0] == g.BARRIER_QUICK_PASS
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 2
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 3
    # Fourth call should not click and should safety-stop
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 3  # no new click
    assert ctx.state.safety_stopped is True
    assert any("barrier quick-pass did not start" in s for s in ctx.state.stops)


def test_loot_phase_has_attempt_ceiling(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [{"id": g.OBJ_REWARD_CHEST, "screenX": 500, "screenY": 400}])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    # Test: click_entity returns False 5 times -> no flag set, safety stop
    click_count = [0]
    def fake_click(ser, entity, **kw):
        click_count[0] += 1
        return False
    monkeypatch.setattr(controller, "click_entity", fake_click)
    ctx = _Ctx()
    for i in range(5):
        cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.data.get("cg_chest_opened") is None
    assert ctx.state.safety_stopped is True
    assert any("could not open the reward chest" in s for s in ctx.state.stops)
    # Test: click_entity returns True once -> flag set after one call
    ctx2 = _Ctx()
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    cg.loot_phase(ctx2, cg.DEFAULT_CONFIG)
    assert ctx2.state.data.get("cg_chest_opened") is True
    assert ctx2.state.safety_stopped is False
