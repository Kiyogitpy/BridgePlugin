"""Tests for falconry.py: the refresh_target adoption (2026-08-27) and the event-driven
primitives shared with golem_crafting's fur trip (2026-09-06)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import bridge_events
import falconry
import controller


class _Ctx:
    ser = None
    events = None


def test_hire_falcon_passes_refresh_target(monkeypatch):
    matthias = {"index": 3, "x": 100, "y": 200, "plane": 0, "name": "Matthias", "distance": 2}
    monkeypatch.setattr(falconry, "_first_inventory_item",
                         lambda sub: {"qty": falconry.DEFAULT_CONFIG["minCoins"]})
    monkeypatch.setattr(falconry, "_nearest_npc", lambda name, max_distance: matthias)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    # The hire is confirmed on the glove appearing in the weapon slot (2026-09-06).
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Falconer's glove"}})
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    assert falconry._hire_falcon(_Ctx()) is True
    assert seen.get("refresh_target") is not None


def test_leg_fairy_ring_passes_refresh_target(monkeypatch):
    """The fairy ring is a real-world object read from the TickCache-gated bulk /objects list, so
    its resolved screen position can be stale by up to 600ms before the click even fires and its
    projection moves with the camera -- same rationale as every other newly-fixed
    click_menu_action object call site in this branch (see pyramid_plunder.py's quick_leave)."""
    ring = {"id": falconry.FAIRY_RING_ID, "x": 100, "y": 200, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    falconry._leg_fairy_ring(_Ctx())

    assert seen.get("refresh_target") is not None


# --- Event-driven primitives (2026-09-06, shared with golem_crafting's fur trip) ---------------

class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(falconry.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _EvCtx:
    def __init__(self, events):
        self.events = events
        self.ser = object()
        self.state = _State()


def _stream(monkeypatch, batches):
    queue = list(batches)

    def poll_udp(_sock):
        if not queue:
            return []
        out = []
        for ev in queue.pop(0):
            if len(ev) > 2 and ev[2] is not None:
                ev[2]()
            out.append((ev[0], ev[1]))
        return out
    monkeypatch.setattr(controller, "poll_udp", poll_udp)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    clock = {"t": 100.0}
    monkeypatch.setattr(falconry, "_now", lambda: clock["t"])
    monkeypatch.setattr(falconry, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ctx = _EvCtx(bridge_events.EventStream(sock=None))
    ctx.clock = clock
    return ctx


def test_throw_confirms_a_catch_on_the_falcon_varbit(monkeypatch):
    varbit = {"v": 0}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["v"])
    kebbit = {"index": 7, "name": "Dashing kebbit", "distance": 3}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [kebbit])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": 14192, "value": 5, "tick": 2},
          lambda: varbit.__setitem__("v", 5))],
    ])
    assert falconry._throw_falcon(ctx, falconry.DEFAULT_CONFIG) is True
    assert ctx.state.data["failed_throws"] == 0
    assert ctx.clock["t"] - 100.0 < 1.0


def test_a_miss_is_counted_not_treated_as_an_error(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    kebbit = {"index": 7, "name": "Dashing kebbit", "distance": 3}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [kebbit])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [])
    assert falconry._throw_falcon(ctx, falconry.DEFAULT_CONFIG) is False
    assert ctx.state.data["failed_throws"] == 1
    assert not ctx.state.safety_stopped


def test_retrieve_returns_once_the_varbit_clears(monkeypatch):
    varbit = {"v": 5}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["v"])
    falcon = {"index": 7, "name": "Gyr Falcon", "distance": 2}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [falcon])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": 14192, "value": 0, "tick": 3},
          lambda: varbit.__setitem__("v", 0))],
    ])
    assert falconry._retrieve_falcon(ctx, falconry.DEFAULT_CONFIG) is True
    assert ctx.clock["t"] - 100.0 < 1.0


def test_hunt_step_retrieves_before_anything_else(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 5)
    falcon = {"index": 7, "name": "Gyr Falcon", "distance": 2}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [falcon])
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Bones", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicks.append(e.get("name")) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=True) == "retrieved"
    assert clicks == ["Gyr Falcon"]


def test_hunt_step_drops_meat_only_when_asked(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Raw dashing kebbit", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Falconer's glove"}})
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    import travel
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicks.append(e.get("name")) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, k: None)
    ctx = _stream(monkeypatch, [])
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=True) == "dropped"
    assert clicks == ["Raw dashing kebbit"]
    clicks.clear()
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=False) == "walked"
    assert clicks == []


def test_hunt_step_hires_when_no_glove_is_worn(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Coins", "qty": 2000}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": None})
    matthias = {"index": 3, "name": "Matthias", "distance": 4}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [matthias])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    seen = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: seen.append(option) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=True) == "hired"
    assert seen == [falconry.OPT_QUICK_FALCON]


def test_ensure_hands_free_removes_a_shield_through_the_equipment_widget(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": None, "shield": {"name": "Rune kiteshield"}, "gloves": None})
    calls = []
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": True, "screenX": 5, "screenY": 5,
                                                          "group": group, "child": child})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: calls.append((e["group"], e["child"])) or True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    ctx = _EvCtx(None)
    assert falconry._ensure_hands_free(ctx) is True
    assert calls == [(387, falconry.EQUIPMENT_SHIELD_CHILD)]


def test_ensure_hands_free_ignores_the_falconers_glove_in_the_weapon_slot(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Falconer's glove"}, "shield": None, "gloves": None})
    ctx = _EvCtx(None)
    assert falconry.hands_free() is True
    assert falconry._ensure_hands_free(ctx) is False


def test_use_teleport_waits_for_the_expected_region(monkeypatch):
    region = {"v": 9528}
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": region["v"]})
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Ardougne cloak 4", "screenX": 1, "screenY": 1}])
    # Inventory teleports are a hover-verified LEFT-click first (USER 2026-09-06).
    seen = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: seen.append(kw.get("expected_action")) or True)
    monkeypatch.setattr(controller, "click_menu_action", _menu_forbidden)
    ctx = _stream(monkeypatch, [
        [("PLAYER_STOPPED", {"tick": 9}, lambda: region.__setitem__("v", 10290))],
    ])
    assert falconry._use_teleport(ctx, falconry.ARDOUGNE_CLOAK, falconry.OPT_KANDARIN_MONASTERY,
                                  expect_region=10290) is True
    assert seen == [falconry.OPT_KANDARIN_MONASTERY]
    assert ctx.clock["t"] - 100.0 < falconry.TELEPORT_BUDGET_S


def _menu_forbidden(*a, **kw):
    raise AssertionError("the right-click menu must not be used here")


def test_use_teleport_falls_back_to_the_menu_when_the_left_click_is_withheld(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": 10290})
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Ardougne cloak 4", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: controller.CLICK_WITHHELD)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: menu.append(option) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry._use_teleport(ctx, falconry.ARDOUGNE_CLOAK, falconry.OPT_KANDARIN_MONASTERY,
                                  expect_region=10290) is True
    assert menu == [falconry.OPT_KANDARIN_MONASTERY]
