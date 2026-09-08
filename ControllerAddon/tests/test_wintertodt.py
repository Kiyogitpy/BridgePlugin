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
                "roundEndReads", "notAtArenaReads"):
        assert key in wt.DEFAULT_CONFIG, key


def test_min_doses_default_is_below_a_full_potions_worth_of_doses():
    # Fix round 2 (Critical 2b): must be strictly less than DOSES_PER_POTION, or a single drink
    # (which removes exactly one dose) always re-crosses the threshold it just relieved.
    assert wt.DEFAULT_CONFIG["minDoses"] < wt.DOSES_PER_POTION


def test_take_concoctions_default_matches_the_typical_one_dose_shortfall():
    # Fix round 2 (Minor 11): was 10 against a typical need of 1-2 doses (minDoses=2,
    # DOSES_PER_POTION=4) -- taking a whole Take-10 batch each time leaves several unfinished
    # potions occupying inventory slots indefinitely.
    assert wt.DEFAULT_CONFIG["takeConcoctions"] == 1


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


def test_walk_to_calls_travel_walk_to_with_a_target_dict_not_positional_x_y_plane(monkeypatch):
    """Fix round 2 (Critical 1). travel.walk_to(ctx, target: dict, tolerance=, goal_adjacent=)
    takes a DICT; the pre-fix code called travel.walk_to(ctx, x, y, plane) POSITIONALLY, so
    `target` inside walk_to was a bare int and every call raised AttributeError the instant
    route_to touched `target.get("plane")`. Exercises the REAL travel.walk_to end to end
    (following tests/test_travel.py's own `walker` fixture pattern: only worldmap.find_path and
    travel._hop_to are stubbed, plus controller.*, so route selection and the walk loop
    genuinely run) -- a test that monkeypatched wt._walk_to itself, or travel.walk_to, would be
    structurally blind to a wrong-arity call, which is exactly how this shipped. This test
    FAILS against the pre-fix `_walk_to` with an AttributeError."""
    ctx = _Ctx()
    state = {"pos": (3200, 3200, 0)}
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": state["pos"][0], "y": state["pos"][1],
                                 "plane": state["pos"][2]})

    def fake_hop(ctx_, x, y, plane):
        state["pos"] = (x, y, plane)
        return True

    route = [(3200 + i, 3200) for i in range(10)]
    monkeypatch.setattr(wt.travel.worldmap, "find_path", lambda *a, **k: route)
    monkeypatch.setattr(wt.travel, "_hop_to", fake_hop)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_collision", lambda *a, **k: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)

    obj = {"x": 3209, "y": 3200, "plane": 0}
    assert wt._walk_to(ctx, obj) is True
    assert state["pos"][0] >= 3200


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

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None, **kwargs):
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
    # Fix round 2 (Minor 11): the default takeConcoctions is now 1 (typical need), so this
    # test -- which specifically exercises the Take-10 bulk path -- configures it explicitly
    # rather than relying on the default to pick it.
    ctx = _Ctx(config=dict(wt.DEFAULT_CONFIG, takeConcoctions=10))
    scene = [{"id": 29320, "name": "Crate", "actions": [w.CRATE_TAKE_10], "distance": 2,
              "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None, **kwargs):
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


def test_kit_phase_defers_the_potion_restock_rather_than_spinning_a_full_inventory(monkeypatch):
    """Fix round 2 (Critical 2c), defense in depth. between_rounds_phase calls kit_phase
    DIRECTLY (bypassing derive_phase's own full-inventory defer for a potion-only deficit), so
    kit_phase itself must not spend its ceiling on a Take-concoction click a full inventory
    guarantees the game will refuse. This would FAIL pre-fix: the ceiling counter incremented
    unconditionally before any click was even attempted, so KIT_MAX_TAKES calls with a full
    inventory would trip a safety stop instead of a harmless no-op."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])  # no crate reachable
    full_items = [{"id": 0, "name": n} for n in
                 ("Bronze axe", "Knife", "Tinderbox", "Hammer")] + \
                [{"id": w.ITEM_ROOT, "name": "Bruma root"}] * 24
    assert len(full_items) == 28
    for _ in range(wt.KIT_MAX_TAKES + 1):
        assert wt.kit_phase(ctx, full_items) is False
    assert ctx.state.safety_stopped is False
    assert ctx.state.data.get("wt_kit_takes", 0) == 0


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

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None, **kwargs):
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

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None, **kwargs):
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


def test_chop_phase_real_click_path_confirms_when_roots_actually_increase(monkeypatch):
    """Exercises the REAL wt._click_object / outcome_waits.await_long, mocking only
    controller.* -- this is what would have caught the Task 6 `.ok` crash, which every
    mocked-helper test above is structurally blind to.

    Fix round 2 (Important 8): confirm now means "we gathered something", so the mocked
    inventory actually has to grow a root count for this to pass -- unlike the pre-fix
    `ws.inventory_full()` confirm, which a merely-full inventory (see the next test) already
    satisfies with no root ever gathered."""
    ctx = _Ctx()
    roots = {"id": w.OBJ_ROOTS, "name": "Bruma roots", "actions": ["Chop"],
             "distance": 2, "x": 5, "y": 5, "plane": 0, "onScreen": True,
             "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [roots])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda *a, **k: [{"id": w.ITEM_ROOT, "name": "Bruma root"}])
    monkeypatch.setattr(controller, "get_api", lambda *a, **k: {"roundActive": True})
    assert wt.chop_phase(ctx, []) is True


def test_chop_phase_does_not_falsely_confirm_when_entered_already_full_of_other_items(monkeypatch):
    """Fix round 2 (Important 8) regression: the old confirm (`ws.inventory_full`) was already
    true when entered full of NON-root items (every slot spent on tools/potions, say), so a
    click that gathered nothing still reported success. This would FAIL against the pre-fix
    confirm, which is trivially true here from the very first pre-check with zero roots ever
    chopped. `stop=` (round ended) releases the wait quickly instead of burning the full 90s
    chop budget in this test."""
    ctx = _Ctx()
    roots = {"id": w.OBJ_ROOTS, "name": "Bruma roots", "actions": ["Chop"],
             "distance": 2, "x": 5, "y": 5, "plane": 0, "onScreen": True,
             "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [roots])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    full_non_root_inventory = [{"id": 999, "name": "junk"}] * 28
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: full_non_root_inventory)
    monkeypatch.setattr(controller, "get_api", lambda *a, **k: {"roundActive": False})
    assert wt.chop_phase(ctx, full_non_root_inventory) is False


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


def test_use_on_reflects_a_withheld_first_click_instead_of_always_true(monkeypatch):
    """Fix round 2 (Minor 10). The first click carries the "Use" hover gate and can be withheld
    (a falsy CLICK_WITHHELD-style return, modelled here as False) -- the pre-fix `_use_on`
    returned True unconditionally regardless of what either click did."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    calls = {"second": 0}

    def fake_click_entity(ser, entity, **kwargs):
        if kwargs.get("expected_action") == "Use":
            return False          # withheld
        calls["second"] += 1
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    item_a = {"id": 0, "name": "Knife", "screenX": 1, "screenY": 1}
    item_b = {"id": w.ITEM_ROOT, "name": "Bruma root", "screenX": 2, "screenY": 2}
    assert wt._use_on(ctx, item_a, item_b) is False
    assert calls["second"] == 0     # the second click must never fire after a withheld first


def test_use_on_reflects_a_refused_second_click(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)

    def fake_click_entity(ser, entity, **kwargs):
        return kwargs.get("expected_action") == "Use"   # first lands, second refused

    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    item_a = {"id": 0, "name": "Knife", "screenX": 1, "screenY": 1}
    item_b = {"id": w.ITEM_ROOT, "name": "Bruma root", "screenX": 2, "screenY": 2}
    assert wt._use_on(ctx, item_a, item_b) is False


def test_use_on_returns_true_only_when_both_clicks_land(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    item_a = {"id": 0, "name": "Knife", "screenX": 1, "screenY": 1}
    item_b = {"id": w.ITEM_ROOT, "name": "Bruma root", "screenX": 2, "screenY": 2}
    assert wt._use_on(ctx, item_a, item_b) is True


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

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None, **kwargs):
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


# --- Task 8: light / fix / dodge --------------------------------------------------------------

def test_light_phase_targets_the_unlit_id_and_escalates_to_the_menu_when_hijacked(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
              "distance": 2, "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    tries = {"bare": 0, "menu": 0}
    monkeypatch.setattr(wt, "_click_object",
                        lambda *a, **k: tries.__setitem__("bare", tries["bare"] + 1) or False)
    monkeypatch.setattr(wt, "_click_menu",
                        lambda *a, **k: tries.__setitem__("menu", tries["menu"] + 1) or True)
    # _await must be stubbed too, or the menu branch falls through to the real
    # bridge_events.await_outcome and spins against a fake clock for the whole budget.
    monkeypatch.setattr(wt, "_await", lambda *a, **k: True)
    assert wt.light_phase(ctx, {}, "SOUTH_WEST") is True
    # A Wintertodt brazier on an official world is the most crowded object in the game;
    # golem_crafting MEASURED a 30-40% bare-click hijack rate on a far quieter one.
    assert tries["menu"] == 1


def test_light_phase_stops_after_its_ceiling(monkeypatch):
    """Fix round 1 (finding 2): the ceiling only ever counts a REAL attempt -- a brazier we can
    see and click that never confirms. An absent target is NOT an attempt (see the ABSENT-
    object test below, which pins the opposite case), so this scene keeps the brazier visible
    every tick and makes both click paths genuinely fail instead."""
    ctx = _Ctx()
    scene = [{"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
              "distance": 2, "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    monkeypatch.setattr(wt, "_click_object", lambda *a, **k: False)
    monkeypatch.setattr(wt, "_click_menu", lambda *a, **k: False)
    for _ in range(wt.LIGHT_MAX_CLICKS + 1):
        wt.light_phase(ctx, {}, "SOUTH_WEST")
    assert ctx.state.safety_stopped is True


def test_light_phase_with_the_target_absent_does_not_walk_the_counter_to_a_safety_stop(monkeypatch):
    """Fix round 1 (finding 2): another player relighting/fixing first is the NORMAL case on a
    busy world and the object simply vanishes from /objects -- that must never be counted as a
    failed attempt. Only controller.* is mocked; the reader stays at its hermetic-fixture
    default ({}, i.e. brazier status UNKNOWN), so this is the REAL wt.light_phase/_brazier_
    action path start to finish. Would have tripped the pre-fix code, which incremented the
    ceiling counter unconditionally, before ever checking whether an object was found."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    for _ in range(wt.LIGHT_MAX_CLICKS * 3):
        wt.light_phase(ctx, {}, "SOUTH_WEST")
    assert ctx.state.safety_stopped is False


def test_fix_phase_targets_the_broken_id(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": w.OBJ_BRAZIER_BROKEN, "name": "Brazier", "actions": ["Fix"],
              "distance": 2, "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}
    monkeypatch.setattr(wt, "_click_object",
                        lambda c, o, action, budget_s, confirm, signals=None:
                        seen.setdefault("id", o["id"]) or True)
    assert wt.fix_phase(ctx, {}, "SOUTH_WEST") is True
    assert seen["id"] == w.OBJ_BRAZIER_BROKEN


def test_dodge_phase_steps_off_the_tile_and_forgets_the_snow(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["wt_snow_tile"] = (10, 20, 0)
    ctx.state.data["wt_snow_at"] = wt._now()
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 10, "y": 20, "plane": 0})
    walked = {}
    monkeypatch.setattr(wt, "_force_walk",
                        lambda c, x, y, p: walked.setdefault("to", (x, y)) or True)
    assert wt.dodge_phase(ctx) is True
    assert walked["to"] != (10, 20)
    assert "wt_snow_tile" not in ctx.state.data


# --- real-path tests (Task 6's `.ok` lesson): only controller.* mocked, so the real
# wt._click_object / wt._click_menu / wt._await -> outcome_waits.await_pre ->
# bridge_events.Outcome actually execute. A stubbed-helper test above is structurally blind to
# a crash inside those real call chains -- Task 6 shipped exactly that, green, on every mocked
# test it had. -------------------------------------------------------------------------------

def test_light_phase_real_bare_click_path_confirms_via_await_pre(monkeypatch):
    """The bare click succeeds and the reader already reads LIT -- exercises the REAL
    wt._click_object -> outcome_waits.await_pre -> bridge_events.Outcome, never touching the
    menu escalation."""
    ctx = _Ctx()
    obj = {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
           "distance": 2, "x": 1, "y": 1, "plane": 0, "onScreen": True,
           "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [obj])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_api",
                        lambda *a, **k: {"braziers": {"SOUTH_WEST": {"status": "LIT"}}})
    assert wt.light_phase(ctx, {}, "SOUTH_WEST") is True


def test_light_phase_confirms_against_the_object_actually_clicked_not_just_the_reader_key(
        monkeypatch):
    """Fix round 2 (Important 6). `done` is keyed to the CONFIGURED compass slot (SOUTH_WEST
    here), but the reader (m8aq's Wintertodt.java BrazierState) carries no world tile for any
    brazier, and `_nearest(_objects(...))` picks whichever matching brazier is closest -- not
    necessarily the SOUTH_WEST one `done` is watching. Reproduces the mismatch: the reader for
    SOUTH_WEST never leaves UNLIT (a reader-only confirm could never fire), but the object
    actually clicked (a fixed id+tile) vanishes from /objects after the click -- proof of a real
    transition at the brazier that was genuinely worked. This would FAIL against the pre-fix
    confirm (`done` alone), which can never become true here. Only controller.* is mocked, so
    the real wt._brazier_action confirm path runs."""
    ctx = _Ctx()
    obj = {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
           "distance": 2, "x": 5, "y": 5, "plane": 0, "onScreen": True,
           "screenX": 10, "screenY": 10}
    scene = {"objs": [obj]}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: list(scene["objs"]))

    def fake_click_entity(ser, entity, **kwargs):
        scene["objs"] = []          # the clicked brazier's own id/tile is gone -- it transitioned
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    # The reader for SOUTH_WEST never moves off UNLIT -- proves the pass comes from the
    # object-tile check, not the HUD.
    monkeypatch.setattr(controller, "get_api",
                        lambda *a, **k: {"braziers": {"SOUTH_WEST": {"status": "UNLIT"}}})
    assert wt.light_phase(ctx, {}, "SOUTH_WEST") is True


def test_light_phase_real_menu_escalation_path_confirms_via_await(monkeypatch):
    """The bare click is refused (hijacked by whatever is drawn over the pixel) -- exercises
    the REAL wt._click_menu -> controller.click_menu_action and the REAL wt._await ->
    outcome_waits.await_pre, with only controller.* mocked."""
    ctx = _Ctx()
    obj = {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
           "distance": 2, "x": 1, "y": 1, "plane": 0, "onScreen": True,
           "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [obj])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: False)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_api",
                        lambda *a, **k: {"braziers": {"SOUTH_WEST": {"status": "LIT"}}})
    assert wt.light_phase(ctx, {}, "SOUTH_WEST") is True


def test_fix_phase_real_bare_click_path_confirms_via_await_pre(monkeypatch):
    """Fix round 1 (finding 1): re-pointed at the REAL post-repair status. Repairing a brazier
    does NOT light it -- the wiki: a broken brazier "can then be relit after it is fixed" -- so
    the reader starts at BROKEN and the click's own side effect moves it to UNLIT, never to
    LIT. The original stub read "LIT" from the start, which coincidentally agreed with the
    pre-fix (wrong) want_status="LIT" and so could not have caught it; with a real BROKEN ->
    UNLIT transition, the old confirm (status == "LIT") could never be satisfied and this test
    would fail against it."""
    ctx = _Ctx()
    obj = {"id": w.OBJ_BRAZIER_BROKEN, "name": "Brazier", "actions": ["Fix"],
           "distance": 2, "x": 1, "y": 1, "plane": 0, "onScreen": True,
           "screenX": 10, "screenY": 10}
    status = {"val": "BROKEN"}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [obj])
    monkeypatch.setattr(controller, "get_api",
                        lambda *a, **k: {"braziers": {"SOUTH_WEST": {"status": status["val"]}}})

    def fake_click_entity(ser, entity, **kwargs):
        status["val"] = "UNLIT"     # what a genuine repair actually produces
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    assert wt.fix_phase(ctx, {}, "SOUTH_WEST") is True


def test_fix_phase_when_already_unlit_resets_the_counter_without_a_click(monkeypatch):
    """Fix round 1 (finding 1 + 2): another player fixed the brazier first -- the reader
    already reads UNLIT (never LIT) and the object has already vanished from /objects. This
    must read as success and reset the counter with NO click at all. Only controller.* is
    mocked (get_objects, get_api), so this is the real wt.fix_phase -> _brazier_action path.
    Would fail two ways against the pre-fix code: want_status="LIT" could never recognise
    UNLIT as done, and the counter incremented unconditionally before checking for an object,
    so repeated calls would climb straight to a safety stop instead of resetting to 0."""
    ctx = _Ctx()
    ctx.state.data["wt_fix_clicks"] = 9
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_api",
                        lambda *a, **k: {"braziers": {"SOUTH_WEST": {"status": "UNLIT"}}})
    assert wt.fix_phase(ctx, {}, "SOUTH_WEST") is True
    assert ctx.state.data["wt_fix_clicks"] == 0


def test_light_phase_menu_escalation_does_not_report_success_when_confirm_times_out(monkeypatch):
    """Fix round 1 (finding 3, Minor): the menu branch must reflect its own confirm's outcome,
    exactly like the bare-click branch already does. Only controller.* is mocked, so the REAL
    wt._click_menu -> controller.click_menu_action and the REAL wt._await ->
    outcome_waits.await_pre -> bridge_events.Outcome execute, with the reader genuinely never
    leaving UNLIT -- the confirm never lands and the budget really expires (against the fake
    clock). Pre-fix, this returned True unconditionally once the menu click itself resolved,
    which is exactly how both Criticals masqueraded as success from the dispatcher's view."""
    ctx = _Ctx()
    obj = {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
           "distance": 2, "x": 1, "y": 1, "plane": 0, "onScreen": True,
           "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [obj])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: False)      # bare refused
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)  # menu resolves
    monkeypatch.setattr(controller, "get_api",
                        lambda *a, **k: {"braziers": {"SOUTH_WEST": {"status": "UNLIT"}}})
    assert wt.light_phase(ctx, {}, "SOUTH_WEST") is False


# --- Task 9: tick, before_break and the stuck watch -------------------------------------------

def test_tick_dispatches_to_the_phase_derive_phase_chose(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(wt, "snapshot", lambda c: ({"atWintertodt": True}, [], "SOUTH_WEST"))
    monkeypatch.setattr(wt.ws, "derive_phase", lambda **k: "chop")
    called = {}
    monkeypatch.setattr(wt, "chop_phase", lambda c, items: called.setdefault("chop", True))
    wt.tick(ctx)
    assert called.get("chop") is True


def test_tick_outside_the_arena_safety_stops_once_the_streak_is_sustained(monkeypatch):
    """Fix round 2 (Important 5): outside-the-arena is now a SUSTAINED streak too (see
    test_tick_tolerates_a_lone_unreadable_read_without_stopping below for the tolerant half).
    `wt.snapshot` is stubbed here (as it always was), which bypasses the real streak-accumulating
    logic snapshot() normally does -- so the streak is seeded directly to demonstrate tick()
    correctly turns a sustained "stop" verdict into an actual safety stop."""
    ctx = _Ctx()
    ctx.state.data["wt_outside_streak"] = wt.DEFAULT_CONFIG["notAtArenaReads"]
    monkeypatch.setattr(wt, "snapshot", lambda c: ({"atWintertodt": False}, [], None))
    wt.tick(ctx)
    assert ctx.state.safety_stopped is True


def test_tick_tolerates_a_lone_unreadable_read_without_stopping(monkeypatch):
    """Fix round 2 (Important 5): a single bridge blip must cost latency, never end an
    unattended run. `_reader()` collapses an unreachable/None `get_api` reply to `{}` exactly
    like a genuine "not at Wintertodt" read -- this would FAIL against the pre-fix code, which
    safety-stopped on the very first such read."""
    ctx = _Ctx()
    monkeypatch.setattr(wt, "snapshot", lambda c: ({}, [], None))
    wt.tick(ctx)
    assert ctx.state.safety_stopped is False


def test_tick_real_path_tolerates_bridge_blips_then_stops_once_sustained(monkeypatch):
    """The real wt.snapshot -> _reader() -> controller.get_api path, only controller.* mocked.
    Every read is unreadable (get_api returns None, which `_reader()`'s own `or {}` collapses),
    so this proves the sustained streak is actually accumulated and consulted end to end, not
    just plumbed through a stub."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    threshold = int(wt.DEFAULT_CONFIG["notAtArenaReads"])
    for _ in range(threshold - 1):
        wt.tick(ctx)
    assert ctx.state.safety_stopped is False
    wt.tick(ctx)
    assert ctx.state.safety_stopped is True


def test_the_stuck_watch_warns_then_stops_on_an_unchanged_signature(hermetic):
    ctx = _Ctx()
    wt._stuck_watch(ctx, "same")
    hermetic["t"] += wt.STALL_WARN_S + 1
    wt._stuck_watch(ctx, "same")
    assert ctx.state.safety_stopped is False       # warn first
    hermetic["t"] += wt.STALL_STOP_S
    wt._stuck_watch(ctx, "same")
    assert ctx.state.safety_stopped is True


def test_the_stuck_watch_resets_when_anything_moves(hermetic):
    ctx = _Ctx()
    wt._stuck_watch(ctx, "a")
    hermetic["t"] += wt.STALL_STOP_S + 1
    wt._stuck_watch(ctx, "b")
    assert ctx.state.safety_stopped is False


def test_progress_signature_moves_when_points_move():
    a = wt._progress_signature({"points": 100, "energyPercent": 50}, [])
    b = wt._progress_signature({"points": 125, "energyPercent": 50}, [])
    assert a != b


def test_before_break_plays_an_active_round_out(monkeypatch, hermetic):
    """Points are all-or-nothing at 500, so a break landing mid-round throws the round away.

    Fix round 2 (Important 3): before_break's own round-over test is now the SUSTAINED
    `wt_round_over_streak` derive_phase itself uses, maintained by the real snapshot() inside
    tick() -- not a second, independent _reader() read taken by before_break. This fake_tick
    plays snapshot's bookkeeping role directly (streak 0 while active, climbing once it is
    not) so the test can drive it without a real bridge."""
    ctx = _Ctx()
    ticks = {"n": 0}

    def fake_tick(c):
        ticks["n"] += 1
        hermetic["t"] += 1.0
        if ticks["n"] < 3:
            c.state.data["wt_round_over_streak"] = 0
        else:
            c.state.data["wt_round_over_streak"] = ticks["n"] - 2

    monkeypatch.setattr(wt, "tick", fake_tick)
    wt.before_break(ctx)
    threshold = wt.DEFAULT_CONFIG["roundEndReads"]
    # 2 ticks while the round is still active, plus `threshold` more before the sustained
    # streak clears the gate.
    assert ticks["n"] == 2 + threshold


def test_before_break_gives_up_at_its_ceiling(monkeypatch, hermetic):
    ctx = _Ctx()
    monkeypatch.setattr(wt, "tick", lambda c: hermetic.__setitem__("t", hermetic["t"] + 5.0))
    monkeypatch.setattr(wt, "_reader", lambda: {"atWintertodt": True, "roundActive": True})
    wt.before_break(ctx)     # must return rather than spin forever


# --- real-path test (the Task 6 `.ok` lesson, restated in this task's own brief): the two
# tick() tests above stub wt.snapshot / wt.ws.derive_phase / wt.chop_phase for convenience, which
# means they cannot see a wrong-arity lambda inside _PHASES, or a crash inside the real click
# chain it calls -- the table lookup and the call both happen through names those tests never
# let run for real. This one mocks only controller.*, so the REAL wintertodt_state.derive_phase
# and the REAL _PHASES table execute end to end.
#
# Fix round 1, Finding 4: this test's earlier docstring overclaimed that it also catches a
# WRONG/DEAD phase string in _PHASES -- it does not. `tick()` looks the handler up with `.get`
# and only calls it `if handler:`, so a phase with no matching key resolves to `handler = None`
# and is a silent no-op: no exception, `wt_phase` still gets set, `safety_stopped` stays False,
# every assertion below still passes exactly as it does today. See
# test_phases_table_covers_every_phase_derive_phase_can_return for the check that actually
# covers a dead/missing key. -------------------------------------------------------------------

def test_tick_real_dispatch_path_reaches_chop_phase_with_only_controller_mocked(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api", lambda *a, **k: {
        "atWintertodt": True, "roundActive": True, "warmth": 1000, "energyPercent": 100,
        "braziers": {},
    })
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [
        {"id": 0, "name": n} for n in
        ("Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(4)")
    ])
    roots = {"id": w.OBJ_ROOTS, "name": "Bruma roots", "actions": ["Chop"],
             "distance": 2, "x": 5, "y": 5, "plane": 0, "onScreen": True,
             "screenX": 10, "screenY": 10}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [roots])
    # click_entity refused rather than mocked to succeed: chop_phase must reach the real click
    # primitive and come back False with no exception and no safety stop -- a wrong-ARITY lambda
    # in _PHASES (a TypeError firing right here) or a broken real chop_phase call chain would
    # blow up right here, something none of the mocked-chop_phase tests above can ever exercise.
    # (It would NOT catch a dead/missing _PHASES key -- see the docstring above.)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: False)
    wt.tick(ctx)
    assert ctx.state.data.get("wt_phase") == "chop"
    assert ctx.state.safety_stopped is False


def test_before_break_real_tick_path_plays_a_round_out_with_only_controller_mocked(monkeypatch):
    """Same lesson applied to before_break: its own two tests above stub wt.tick itself, so
    they cannot see before_break failing to import/call the real tick correctly. Here tick is
    untouched -- only controller.* is mocked -- and the round ends the instant roundActive
    reads False, which is derive_phase's own real "between_rounds" / kit / chop routing doing
    its job rather than a canned fake_tick."""
    ctx = _Ctx()
    rounds = {"active": True}
    calls = {"n": 0}

    def fake_get_api(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            rounds["active"] = False
        return {"atWintertodt": True, "roundActive": rounds["active"], "warmth": 1000,
                "energyPercent": 100, "braziers": {}}

    monkeypatch.setattr(controller, "get_api", fake_get_api)
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [
        {"id": 0, "name": n} for n in
        ("Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(4)")
    ])
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    wt.before_break(ctx)
    assert calls["n"] >= 2
    assert ctx.state.safety_stopped is False


# --- fix round 1 ---------------------------------------------------------------------------

def test_before_break_paces_itself_even_when_the_phase_handler_does_no_io(monkeypatch, hermetic):
    """Finding 1 (Critical). A phase handler that never calls _sleep -- chop_phase with no
    roots in view is exactly this, it returns False with no I/O at all -- must not turn
    before_break into an unthrottled busy loop hammering the bridge for up to
    BREAK_DEFER_MAX_S. The loop has to pace ITSELF once per iteration regardless of what
    tick() did, the same way between_rounds_phase already paces itself with
    outcome_waits.tick_pause.

    Pre-fix this would hang forever: nothing in the loop body ever touches the fake clock (this
    fake tick() deliberately doesn't, and neither did the old before_break outside of calling
    tick()), so `_now() - started < BREAK_DEFER_MAX_S` never becomes false. A hard iteration
    ceiling turns that into a clean, fast assertion failure instead of a stuck test run --
    exactly the "loop ran 200,000 iterations with the fake clock never advancing" the reviewer
    proved live."""
    ctx = _Ctx()
    calls = {"n": 0}
    ITERATION_CEILING = 5000     # comfortably above the ~700 a correctly-paced loop needs

    def fake_tick(c):
        calls["n"] += 1
        if calls["n"] > ITERATION_CEILING:
            raise AssertionError(
                "before_break ran past %d iterations with a no-op tick() and an unmoving "
                "clock -- it is not pacing itself" % ITERATION_CEILING)
        # No _sleep call here on purpose: this is chop_phase-with-no-roots, the ordinary case
        # that started the busy loop live.

    monkeypatch.setattr(wt, "tick", fake_tick)
    monkeypatch.setattr(wt, "_reader", lambda: {"atWintertodt": True, "roundActive": True})
    wt.before_break(ctx)
    assert calls["n"] < ITERATION_CEILING
    # It looped for the whole ceiling, paced by its OWN sleep between iterations -- not by
    # anything tick() did, which this fake_tick deliberately never provides.
    assert calls["n"] > 100


def test_progress_signature_ignores_warmth_and_energy_because_they_move_without_us():
    """Finding 2 (Important). Warmth regenerates passively (~8%/min, wiki) and energy falls
    from OTHER players damaging the boss -- neither moves because OUR routine did anything, so
    either one drifting the signature lets a genuine wedge hide behind it and defeats
    _stuck_watch entirely. Only points and inventory composition are ours to move."""
    a = wt._progress_signature({"points": 100, "warmth": 500, "energyPercent": 80}, [])
    b = wt._progress_signature({"points": 100, "warmth": 900, "energyPercent": 10}, [])
    assert a == b


def test_the_stuck_watch_stands_down_during_between_rounds(monkeypatch, hermetic):
    """Finding 3 (Important). Between rounds `points` reads -1 and the inventory is static, so
    with warmth/energy already dropped from the signature (Finding 2) an unusually long gap
    before the next round starts would trip STALL_STOP_S against a perfectly healthy run,
    unless the watch itself stands down while the derived phase is "between_rounds" -- the
    same in-round-only shape a sibling routine in this repo already uses for its own stuck
    watch. Real wt.tick() dispatch (only wt.ws.derive_phase and wt.between_rounds_phase are
    stubbed, to hold the phase steady without needing a real between-round HUD read)."""
    ctx = _Ctx()
    monkeypatch.setattr(wt, "snapshot",
                        lambda c: ({"atWintertodt": True, "points": -1, "roundActive": False},
                                   [], "SOUTH_WEST"))
    monkeypatch.setattr(wt.ws, "derive_phase", lambda **k: "between_rounds")
    monkeypatch.setattr(wt, "between_rounds_phase", lambda c, reader, items: True)
    for _ in range(50):
        wt.tick(ctx)
        hermetic["t"] += 5.0            # 250s total, comfortably past STALL_STOP_S (180s)
    assert ctx.state.safety_stopped is False


def test_phases_table_covers_every_phase_derive_phase_can_return():
    """Finding 4 (Important, test quality). `tick()` records `data['wt_phase']` and logs the
    phase change BEFORE ever consulting `_PHASES`, and the lookup is `_PHASES.get(phase)` -- a
    phase string with no matching key resolves to `handler = None`, and `if handler:` just
    skips it. No exception, no safety stop, no visible symptom at all: a DEAD key is a silent
    no-op, which is exactly what the reviewer proved by scratch injection against
    test_tick_real_dispatch_path_reaches_chop_phase_with_only_controller_mocked above (that
    test only ever drives the "chop" branch, so it cannot see a missing "kit" or "warmth" entry
    either).

    This is a static check instead, and it derives the expected phase set from
    ws.derive_phase's OWN source -- every quoted string literal it can `return` -- rather than
    retyping a second hardcoded list that could silently drift out of step with the first."""
    import inspect
    import re

    source = inspect.getsource(ws.derive_phase)
    returned = set(re.findall(r'return\s+"([a-z_]+)"', source))
    expected = returned - {"stop"}      # "stop" is handled by tick() itself, before the table
    assert len(expected) >= 5, "sanity: the regex should have found most of the real ladder"
    assert set(wt._PHASES.keys()) == expected


def test_before_break_reads_defensively_when_the_reader_itself_raises(monkeypatch, hermetic):
    """Finding 5 (Minor). Everything else in this file treats a bad bridge read as "can't
    tell" rather than letting the exception fly (_reader() itself already does `or {}`;
    kit_deficit/needs_drink/etc. all tolerate an empty/partial reader). before_break's own call
    to _reader() must be just as forgiving -- an exception there must not propagate out of the
    before_break hook and crash the engine's break machinery."""
    ctx = _Ctx()
    monkeypatch.setattr(wt, "_reader", lambda: (_ for _ in ()).throw(RuntimeError("bridge blew up")))
    wt.before_break(ctx)     # must not raise
    assert ctx.state.safety_stopped is False


def test_the_routine_is_wired_into_the_gui_dropdown():
    # A routine file alone is only launchable from the command line. Both the Java enum entry
    # and this key are required, per CLAUDE.md's routine contract.
    assert controller.ROUTINE_FILES["WINTERTODT"] == "routines/wintertodt.py"


def test_the_java_enum_has_a_matching_constant():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / \
        "src/main/java/com/example/controller/RoutineChoice.java"
    assert "WINTERTODT(" in src.read_text(encoding="utf-8")
