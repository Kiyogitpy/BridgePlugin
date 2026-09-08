"""routines/wintertodt.py.

Hermetic: every bridge read is stubbed and the module clock is faked, so a budget that would
wait seconds live expires instantly (the test_blast_furnace / test_motherlode_mine pattern).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import wintertodt as wt
import wintertodt_ids as w


class _State:
    def __init__(self, config=None):
        self.config = dict(config or wt.DEFAULT_CONFIG)
        self.data = {}
        self.safety_stopped = False
        self.stop_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.stop_reason = reason


class _Ctx:
    def __init__(self, config=None):
        self.state = _State(config)
        self.ser = object()
        self.events = None


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    """No test may reach the running client, and no test may really sleep."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(wt, "_now", lambda: clock["t"])
    monkeypatch.setattr(wt, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(controller, "get_api", lambda *a, **k: {})
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 1, "y": 2, "plane": 0})
    monkeypatch.setattr(controller, "get_varbit", lambda *a, **k: 0)
    return clock


def test_default_config_carries_every_key_derive_phase_reads():
    for key in ("preferredBrazier", "drinkAtWarmth", "minDoses", "takeConcoctions",
                "fletch", "feedRootsNearRoundEnd", "feedRootsBelowEnergy", "dodgeSnowfall",
                "roundEndReads"):
        assert key in wt.DEFAULT_CONFIG, key


def test_get_config_ignores_the_bridge_because_there_is_no_config_section():
    assert wt.get_config({"anything": 1}) == wt.DEFAULT_CONFIG


def test_varbit_subscriptions_cover_warmth_and_the_round_timer():
    assert w.VARBIT_WARMTH in wt.VARBIT_SUBSCRIPTIONS
    assert w.VARBIT_ROUND_TIMER in wt.VARBIT_SUBSCRIPTIONS


def test_objects_filters_by_id_and_action_never_by_name(monkeypatch):
    # The name collision guard: an unlit brazier and a broken one are both "Brazier".
    scene = [
        {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"], "distance": 3},
        {"id": w.OBJ_BRAZIER_BROKEN, "name": "Brazier", "actions": ["Fix"], "distance": 1},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    found = wt._objects(ids={w.OBJ_BRAZIER_UNLIT})
    assert [o["id"] for o in found] == [w.OBJ_BRAZIER_UNLIT]


def test_nearest_picks_the_smallest_distance():
    objs = [{"distance": 7}, {"distance": 2}, {"distance": 5}]
    assert wt._nearest(objs)["distance"] == 2
    assert wt._nearest([]) is None


def test_on_event_records_a_snowfall_spawn_on_our_own_tile(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 10, "y": 20, "plane": 0})
    wt.on_event(ctx, "OBJECT_SPAWNED", {"id": w.OBJ_SNOW, "x": 10, "y": 20, "plane": 0})
    assert wt._snow_on_tile(ctx) is True


def test_on_event_ignores_a_snowfall_spawn_somewhere_else(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 10, "y": 20, "plane": 0})
    wt.on_event(ctx, "OBJECT_SPAWNED", {"id": w.OBJ_SNOW, "x": 99, "y": 99, "plane": 0})
    assert wt._snow_on_tile(ctx) is False


def test_a_recorded_snowfall_expires_so_a_missed_despawn_cannot_pin_the_dodge_rung(hermetic):
    ctx = _Ctx()
    ctx.state.data["wt_snow_tile"] = (10, 20, 0)
    ctx.state.data["wt_snow_at"] = wt._now()
    hermetic["t"] += wt.SNOW_MEMORY_S + 1
    assert wt._snow_on_tile(ctx) is False


def test_kit_phase_clicks_the_crate_whose_id_hands_out_the_missing_tool(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": cid, "name": "Crate", "actions": [act], "distance": 2,
              "x": 1, "y": 1, "plane": 0}
             for cid, (act, _item) in w.CRATE_TOOLS.items()]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    clicked = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        clicked["id"] = obj["id"]
        clicked["action"] = action
        return True

    monkeypatch.setattr(wt, "_click_object", fake_click)
    # Everything present except the knife.
    items = [{"id": 0, "name": n} for n in
             ("Bronze axe", "Tinderbox", "Hammer", "Rejuvenation potion(4)")]
    assert wt.kit_phase(ctx, items) is True
    assert clicked["id"] == 29317
    assert clicked["action"] == "Take-knife"


def test_kit_phase_takes_concoctions_in_bulk(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": 29320, "name": "Crate", "actions": [w.CRATE_TAKE_10], "distance": 2,
              "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        seen["action"] = action
        return True                      # a real True: kit_phase's result is asserted `is True`

    monkeypatch.setattr(wt, "_click_object", fake_click)
    items = [{"id": 0, "name": n} for n in ("Bronze axe", "Knife", "Tinderbox", "Hammer")]
    assert wt.kit_phase(ctx, items) is True
    assert seen["action"] == w.CRATE_TAKE_10


def test_kit_phase_stops_the_run_after_too_many_fruitless_takes(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    for _ in range(wt.KIT_MAX_TAKES + 1):
        wt.kit_phase(ctx, [])
    assert ctx.state.safety_stopped is True
    assert "kit" in ctx.state.stop_reason.lower()


def test_warmth_phase_drinks_a_dose_and_confirms_on_warmth_rising(monkeypatch):
    ctx = _Ctx()
    calls = {"n": 0}
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(wt, "_click_inventory_item",
                        lambda c, item, action, budget_s, confirm: calls.__setitem__("n", 1) or True)
    items = [{"id": 0, "name": "Rejuvenation potion(4)", "slot": 3}]
    assert wt.warmth_phase(ctx, items) is True
    assert calls["n"] == 1


def test_warmth_phase_with_no_potion_is_a_safety_stop_not_a_silent_loop(monkeypatch):
    ctx = _Ctx()
    assert wt.warmth_phase(ctx, []) is False
    assert ctx.state.safety_stopped is True
