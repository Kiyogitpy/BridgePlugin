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
import wintertodt_state as ws


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


# --- fix round 1: Outcome has no .ok, only .hit/__bool__ -----------------------------------
# These two exercise the REAL _click_object / _click_inventory_item, mocking only controller.*
# and letting outcome_waits.await_pre / bridge_events.Outcome run for real against the faked
# clock -- every other test in this file mocks wt._click_object / wt._click_inventory_item
# themselves, which is exactly why `outcome and outcome.ok` (an AttributeError on every genuine
# hit, since Outcome's __slots__ are hit/signal/payload/confirmed/latency_s) shipped green.

def test_click_object_real_success_path_does_not_crash_on_a_genuine_hit(monkeypatch):
    ctx = _Ctx()
    obj = {"id": 1, "name": "Crate", "x": 1, "y": 1, "plane": 0, "distance": 2,
           "onScreen": True, "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    assert wt._click_object(ctx, obj, "Take", 1.0, lambda: True) is True


def test_click_inventory_item_real_success_path_does_not_crash_on_a_genuine_hit(monkeypatch):
    ctx = _Ctx()
    item = {"id": 0, "name": "Rejuvenation potion(4)", "screenX": 5, "screenY": 5, "slot": 0}
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    assert wt._click_inventory_item(ctx, item, "Drink", 1.0, lambda: True) is True


# --- fix round 1: _take_concoctions cold-start deadlock -------------------------------------

def test_take_concoctions_cold_start_reaches_a_drinkable_dose_not_a_pile_of_unf(monkeypatch):
    """Empty kit, crate available every tick: stage 1 must stop taking unfinished potions once
    enough are held to cover minDoses, so the chain falls through to picking a herb and
    combining. Pre-fix, stage 1 is unconditional and always wins (a successful click returns
    True immediately), so this would spin forever adding unfinished potions and never reach a
    drinkable dose -- reproducing the reviewer's "28 unf, 0 herb, 0 doses" finding."""
    ctx = _Ctx()
    inv = {"items": []}

    def fake_get_objects(*a, **k):
        return [
            {"id": 29320, "name": "Crate", "actions": [w.CRATE_TAKE_10], "distance": 2,
             "x": 1, "y": 1, "plane": 0, "onScreen": True},
            {"id": w.OBJ_SPROUTING_ROOTS, "name": "Sprouting Roots", "actions": ["Pick"],
             "distance": 2, "x": 1, "y": 1, "plane": 0, "onScreen": True},
        ]

    def fake_click_entity(ser, entity, **kwargs):
        eid = entity.get("id")
        if eid == 29320:
            inv["items"].append({"id": w.ITEM_UNF, "name": "Rejuvenation potion (unf)"})
        elif eid == w.OBJ_SPROUTING_ROOTS:
            inv["items"].append({"id": w.ITEM_HERB, "name": "Bruma herb"})
        elif eid == w.ITEM_UNF:
            # the second _use_on click: consume one herb + one unf, produce a finished dose
            for target in (w.ITEM_HERB, w.ITEM_UNF):
                for i, it in enumerate(inv["items"]):
                    if it.get("id") == target:
                        del inv["items"][i]
                        break
            inv["items"].append({"id": 999, "name": "Rejuvenation potion(4)"})
        return True

    monkeypatch.setattr(controller, "get_objects", fake_get_objects)
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: list(inv["items"]))
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", fake_click_entity)

    for _ in range(6):
        wt._take_concoctions(ctx, list(inv["items"]))

    assert w.ITEM_UNF not in [i.get("id") for i in inv["items"]]
    assert w.ITEM_HERB not in [i.get("id") for i in inv["items"]]
    assert ws.potion_doses(inv["items"]) >= wt.DEFAULT_CONFIG["minDoses"]
    assert ws.count_item(inv["items"], w.ITEM_UNF) < 28


def test_kit_phase_falls_back_to_take_concoction_when_take_10_is_not_offered(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": 29320, "name": "Crate", "actions": ["Take-concoction"], "distance": 2,
              "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        seen["action"] = action
        return True

    monkeypatch.setattr(wt, "_click_object", fake_click)
    items = [{"id": 0, "name": n} for n in ("Bronze axe", "Knife", "Tinderbox", "Hammer")]
    assert wt.kit_phase(ctx, items) is True
    assert seen["action"] == "Take-concoction"


# --- Task 7: chop / fletch / feed ------------------------------------------------------------

def test_chop_phase_confirms_on_the_games_own_inventory_full_line(monkeypatch):
    ctx = _Ctx()
    roots = {"id": w.OBJ_ROOTS, "name": "Bruma roots", "actions": ["Chop"],
             "distance": 2, "x": 5, "y": 5, "plane": 0}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [roots])
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        seen["signals"] = signals
        seen["action"] = action
        return True

    monkeypatch.setattr(wt, "_click_object", fake_click)
    assert wt.chop_phase(ctx, []) is True
    assert seen["action"] == "Chop"
    # The chat line is the pushed signal; the inventory read is the confirm.
    assert any("CHAT" in str(s) for s in seen["signals"])


def test_chop_phase_with_no_roots_in_view_does_not_stop_the_run(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    assert wt.chop_phase(ctx, []) is False
    assert ctx.state.safety_stopped is False


def test_chop_phase_real_click_path_confirms_on_a_full_inventory(monkeypatch):
    """Exercises the REAL wt._click_object / outcome_waits.await_pre, mocking only controller.*
    -- this is what would have caught the Task 6 `.ok` crash, which every mocked-helper test
    above is structurally blind to."""
    ctx = _Ctx()
    roots = {"id": w.OBJ_ROOTS, "name": "Bruma roots", "actions": ["Chop"],
             "distance": 2, "x": 5, "y": 5, "plane": 0, "onScreen": True,
             "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [roots])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [{"id": 0}] * 28)
    assert wt.chop_phase(ctx, []) is True


def test_fletch_phase_uses_the_knife_on_a_root(monkeypatch):
    ctx = _Ctx()
    used = {}
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(wt, "_use_knife_on_root",
                        lambda c, knife, root: used.setdefault("ok", True) or True)
    monkeypatch.setattr(wt, "_await", lambda *a, **k: True)
    items = [{"id": 0, "name": "Knife", "slot": 0},
             {"id": w.ITEM_ROOT, "name": "Bruma root", "slot": 1}]
    assert wt.fletch_phase(ctx, items) is True
    assert used["ok"] is True


def test_fletch_phase_without_a_knife_falls_through_rather_than_looping(monkeypatch):
    ctx = _Ctx()
    items = [{"id": w.ITEM_ROOT, "name": "Bruma root", "slot": 1}]
    assert wt.fletch_phase(ctx, items) is False


def test_fletch_phase_real_use_on_path_confirms_when_roots_are_gone(monkeypatch):
    """Exercises the REAL wt._use_knife_on_root (-> wt._use_on) and the REAL wt._await
    (-> outcome_waits.await_pre), mocking only controller.*."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [])
    items = [{"id": 0, "name": "Knife", "slot": 0, "screenX": 1, "screenY": 1},
             {"id": w.ITEM_ROOT, "name": "Bruma root", "slot": 1, "screenX": 2, "screenY": 2}]
    assert wt.fletch_phase(ctx, items) is True


def test_feed_phase_clicks_the_lit_brazier_and_never_the_unlit_twin(monkeypatch):
    ctx = _Ctx()
    scene = [
        {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
         "distance": 1, "x": 1, "y": 1, "plane": 0},
        {"id": w.OBJ_BRAZIER_LIT, "name": "Burning brazier", "actions": ["Feed"],
         "distance": 4, "x": 9, "y": 9, "plane": 0},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        seen["id"] = obj["id"]
        return True                      # feed_phase returns this straight through

    monkeypatch.setattr(wt, "_click_object", fake_click)
    items = [{"id": w.ITEM_KINDLING, "name": "Bruma kindling"}]
    assert wt.feed_phase(ctx, {}, items, "SOUTH_WEST") is True
    # The NEARER object is the unlit one; picking by name or by distance would take it.
    assert seen["id"] == w.OBJ_BRAZIER_LIT


def test_feed_phase_real_click_path_never_clicks_the_unlit_twin(monkeypatch):
    """Exercises the REAL wt._click_object, mocking only controller.* -- the same nearer-unlit-
    twin geometry as the test above, but through the genuine click/confirm path rather than a
    stubbed `_click_object` that could silently accept the wrong object id."""
    ctx = _Ctx()
    scene = [
        {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
         "distance": 1, "x": 1, "y": 1, "plane": 0, "onScreen": True,
         "screenX": 3, "screenY": 3},
        {"id": w.OBJ_BRAZIER_LIT, "name": "Burning brazier", "actions": ["Feed"],
         "distance": 4, "x": 9, "y": 9, "plane": 0, "onScreen": True,
         "screenX": 20, "screenY": 20},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    clicked = {}

    def fake_click_entity(ser, entity, **kwargs):
        clicked["id"] = entity.get("id")
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [])
    items = [{"id": w.ITEM_KINDLING, "name": "Bruma kindling"}]
    assert wt.feed_phase(ctx, {}, items, "SOUTH_WEST") is True
    assert clicked["id"] == w.OBJ_BRAZIER_LIT
