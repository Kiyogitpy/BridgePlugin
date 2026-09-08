import pytest

import controller
import vale_totems as vt
import vale_totem_route as route
from routines import vale_totems as vtr_routine


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)


def test_default_config_is_yew():
    cfg = vtr_routine.get_config(None)
    assert cfg["wood"] == "YEW"
    assert cfg["log"] == "Yew logs"
    assert cfg["bow"] == "Yew longbow (u)"


def test_the_bridge_config_wins_over_the_default():
    cfg = vtr_routine.get_config({"valeTotemsWood": "MAPLE"})
    assert cfg["wood"] == "MAPLE"
    assert cfg["log"] == "Maple logs"
    assert cfg["bow"] == "Maple longbow (u)"


def test_an_unknown_wood_falls_back_to_the_default_rather_than_crashing():
    cfg = vtr_routine.get_config({"valeTotemsWood": "PLASTIC"})
    assert cfg["wood"] == "YEW"


def test_read_state_returns_none_when_the_endpoint_is_unavailable():
    assert vtr_routine.read_state() is None


def test_tick_safety_stops_when_the_player_is_outside_the_valley(monkeypatch):
    payload = {"sites": [{"siteNumber": n, "baseValue": 0, "wood": "NONE",
                          "baseCarved": False,
                          "bottom": {"value": 0}, "middle": {"value": 0}, "top": {"value": 0},
                          "decorations": 0, "nearbySpirits": [], "durabilityRemaining": 0,
                          "unclaimedOfferings": 0} for n in range(1, 9)],
               "researchPoints": 0, "totalUnclaimedOfferings": 0, "atValeTotems": False}
    monkeypatch.setattr(controller, "get_api", lambda g, r: payload)
    stops = []
    ctx = _ctx(monkeypatch, stops)
    vtr_routine.tick(ctx)
    assert stops, "a player outside the valley must stop, not improvise -- there is no router"


def _ctx(monkeypatch, stops):
    class _State:
        def __init__(self):
            self.data = {}

        def trigger_safety_stop(self, reason):
            stops.append(reason)

    class _Ctx:
        def __init__(self):
            self.ser = object()
            self.state = _State()
            self.events = None
            self.config = vtr_routine.get_config(None)

    return _Ctx()


def _one_site(**kw):
    base = {"siteNumber": 6, "baseValue": 0, "wood": "NONE", "baseCarved": False,
            "bottom": {"value": 0}, "middle": {"value": 0}, "top": {"value": 0},
            "decorations": 0, "nearbySpirits": ["BUFFALO", "JAGUAR", "SNAKE"],
            "durabilityRemaining": 0, "unclaimedOfferings": 0}
    base.update(kw)
    return vt.parse_state({"sites": [base], "researchPoints": 0,
                           "totalUnclaimedOfferings": 0, "atValeTotems": True}).sites[6]


def test_site_object_looks_up_the_offerings_pile_by_its_own_id(monkeypatch):
    seen = {}

    def fake(x, y, plane, obj_id, radius=None, inst=False):
        seen.update(x=x, y=y, obj_id=obj_id)
        return {"screenX": 1, "screenY": 2, "id": obj_id}

    monkeypatch.setattr(controller, "get_object_at", fake)
    obj = vtr_routine.site_object(6, "offerings")
    assert seen["obj_id"] == route.SITES[6]["offerings_id"] == 57050
    assert (seen["x"], seen["y"]) == route.SITES[6]["offerings"]
    assert obj is not None


def test_claim_confirms_on_the_pile_emptying_not_on_the_click(monkeypatch):
    site = _one_site(unclaimedOfferings=65)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    reads = iter([65, 65, 0])
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            unclaimedOfferings=next(reads))}})())
    assert vtr_routine.claim(_ctx_only(), site) is True


def test_claim_reports_failure_when_the_pile_never_empties(monkeypatch):
    site = _one_site(unclaimedOfferings=65)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            unclaimedOfferings=65)}})())
    assert vtr_routine.claim(_ctx_only(), site) is False


def test_build_confirms_on_the_base_value_leaving_zero(monkeypatch):
    site = _one_site()
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            baseValue=4, wood="YEW")}})())
    assert vtr_routine.build(_ctx_only(), site) is True


def test_a_refused_click_is_not_reported_as_success(monkeypatch):
    site = _one_site()
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: False)
    assert vtr_routine.build(_ctx_only(), site) is False


def _ctx_only():
    class _State:
        def __init__(self):
            self.data = {}

        def trigger_safety_stop(self, reason):
            raise AssertionError("unexpected safety stop: %s" % reason)

    class _Ctx:
        def __init__(self):
            self.ser = object()
            self.state = _State()
            self.events = None
            self.config = vtr_routine.get_config(None)

    return _Ctx()


import skillmulti


def test_choose_spirit_presses_the_number_derived_from_the_matched_widget(monkeypatch):
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_CARVE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 17, "screenX": 5, "screenY": 6})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is True
    assert keys == ["3"], "option 3 == child 17, derived from the widget not from a table"


def test_choose_spirit_refuses_when_the_fletch_prompt_is_the_one_on_screen(monkeypatch):
    # THE trap: both prompts are widget group 270. Pressing a number here would fletch.
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_MAKE)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is False
    assert keys == []


def test_choose_spirit_refuses_when_no_prompt_can_be_read(monkeypatch):
    monkeypatch.setattr(skillmulti, "showing", lambda t: False)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is False
    assert keys == []


def test_choose_spirit_clicks_the_widget_when_no_number_can_be_derived(monkeypatch):
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_CARVE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 99, "screenX": 5, "screenY": 6})
    keys = []
    clicks = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    monkeypatch.setattr(controller, "click_entity",
                        lambda *a, **k: clicks.append(a) or True)
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is True
    assert keys == [] and clicks


def test_carve_rereads_the_spirits_before_choosing(monkeypatch):
    # The wiki says the trio can rotate mid-carve. carve() must pick from a FRESH read, not
    # from the site object it was handed.
    stale = _one_site(baseValue=4, wood="YEW",
                      nearbySpirits=["BUFFALO", "JAGUAR", "SNAKE"])
    fresh = _one_site(baseValue=4, wood="YEW",
                      nearbySpirits=["EAGLE", "SCORPION", "SNAKE"])
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: fresh}})())
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    chosen = []
    monkeypatch.setattr(vtr_routine, "choose_spirit",
                        lambda ctx, s: chosen.append(s) or True)
    monkeypatch.setattr(vtr_routine, "_await_menu", lambda ctx: True)
    vtr_routine.carve(_ctx_only(), stale)
    assert chosen == ["EAGLE"], "carve used the stale trio instead of re-reading"


def test_count_item_counts_slots_not_distinct_names(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Yew logs"}, {"name": "Yew logs"},
                                 {"name": "Knife"}])
    assert vtr_routine.count_item("Yew logs") == 2


def test_decorate_confirms_on_the_decoration_count_rising(monkeypatch):
    site = _one_site(baseValue=4, wood="YEW", baseCarved=True, decorations=1)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            baseValue=4, wood="YEW", baseCarved=True,
                            decorations=2)}})())
    assert vtr_routine.decorate(_ctx_only(), site) is True


def test_start_fletching_answers_the_make_prompt_never_the_carve_prompt(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Yew logs", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_CARVE)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.start_fletching(_ctx_only()) is False
    assert keys == [], "answered the carve prompt as if it were the fletch prompt"


def test_start_fletching_derives_the_bow_option_from_the_menu(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Yew logs", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_MAKE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 17, "screenX": 5, "screenY": 6})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.start_fletching(_ctx_only()) is True
    assert keys == ["3"]


def test_start_fletching_never_presses_space(monkeypatch):
    # Space repeats the LAST CRAFTED item -- session state we do not own. A fresh login or one
    # manual fletch of something else and it silently makes the wrong thing all run.
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Yew logs", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_MAKE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 17, "screenX": 5, "screenY": 6})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    vtr_routine.start_fletching(_ctx_only())
    assert "space" not in [k.lower() for k in keys]


import travel


def test_at_site_is_true_within_tolerance_of_the_stand_tile(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1477, "y": 3331, "plane": 0})
    assert vtr_routine.at_site(6) is True
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1400, "y": 3300, "plane": 0})
    assert vtr_routine.at_site(6) is False


def test_walk_leg_hops_every_recorded_waypoint_in_order(monkeypatch):
    hops = []
    monkeypatch.setattr(travel, "_hop_to",
                        lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1438, "y": 3307, "plane": 0})
    assert vtr_routine.walk_leg(_ctx_only(), 6, 5) is True
    assert hops == list(route.leg_waypoints(6, 5))


def test_walk_leg_stops_at_the_first_hop_that_fails(monkeypatch):
    hops = []

    def hop(ctx, x, y, plane, **k):
        hops.append((x, y))
        return len(hops) < 3

    monkeypatch.setattr(travel, "_hop_to", hop)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 0, "y": 0, "plane": 0})
    assert vtr_routine.walk_leg(_ctx_only(), 6, 5) is False
    assert len(hops) == 3


def test_walk_leg_refuses_a_leg_with_no_recorded_path(monkeypatch):
    monkeypatch.setattr(travel, "_hop_to",
                        lambda *a, **k: pytest.fail("should not hop an unrecorded leg"))
    assert vtr_routine.walk_leg(_ctx_only(), 6, 3) is False


import banker


def test_find_basket_matches_either_open_or_shut(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    assert vtr_routine.find_basket() is not None
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Log basket", "screenX": 1, "screenY": 1}])
    assert vtr_routine.find_basket() is not None
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Knife"}])
    assert vtr_routine.find_basket() is None


def test_empty_basket_uses_the_named_menu_option_not_a_bare_click(monkeypatch):
    # A bare left click races Fill / Wear / Check, and Fill is a silent no-op that looks like
    # success -- the same reason banker.empty_log_basket selects its option by name.
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, opt, **k: calls.append(opt) or True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    # A SEQUENCE of candidate names, the banker.empty_log_basket idiom -- click_menu_action
    # tries each against the one menu it reads back.
    assert calls and any("empty" in o.lower() for o in calls[0])


def test_empty_basket_is_a_no_op_without_a_basket(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Knife"}])
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: pytest.fail("clicked with no basket"))
    assert vtr_routine.empty_basket(_ctx_only()) is False


def test_bank_trip_withdraws_and_fills_for_every_round(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    withdrawals = []
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, fresh=False: withdrawals.append(name) or True)
    fills = []
    monkeypatch.setattr(vtr_routine, "fill_basket",
                        lambda ctx: fills.append(1) or True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    assert vtr_routine.bank_trip(_ctx_only()) is True
    assert withdrawals == ["Yew logs"] * vtr_routine.BANK_ROUNDS
    # The last round has nowhere to put the logs, so it does not fill.
    assert len(fills) == vtr_routine.BANK_ROUNDS - 1


def test_work_site_runs_the_ladder_until_the_site_is_done(monkeypatch):
    calls = []
    seq = iter([("claim", None), ("build", None), ("carve", "EAGLE"),
                ("decorate", None), ("done", None)])
    monkeypatch.setattr(vt, "next_action", lambda site, **k: next(seq))
    monkeypatch.setattr(vtr_routine, "claim", lambda c, s: calls.append("claim") or True)
    monkeypatch.setattr(vtr_routine, "build", lambda c, s: calls.append("build") or True)
    monkeypatch.setattr(vtr_routine, "carve", lambda c, s: calls.append("carve") or True)
    monkeypatch.setattr(vtr_routine, "decorate",
                        lambda c, s: calls.append("decorate") or True)
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: _one_site())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 10)
    vtr_routine.work_site(_ctx_only(), 6)
    assert calls == ["claim", "build", "carve", "decorate"]


def test_work_site_stops_after_a_failed_action_rather_than_retrying_forever(monkeypatch):
    calls = []
    monkeypatch.setattr(vt, "next_action", lambda site, **k: ("build", None))
    monkeypatch.setattr(vtr_routine, "build", lambda c, s: calls.append("build") or False)
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: _one_site())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 10)
    vtr_routine.work_site(_ctx_only(), 6)
    assert len(calls) == 1


def test_work_site_is_bounded_even_when_every_action_reports_success(monkeypatch):
    calls = []
    monkeypatch.setattr(vt, "next_action", lambda site, **k: ("carve", "EAGLE"))
    monkeypatch.setattr(vtr_routine, "carve", lambda c, s: calls.append(1) or True)
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: _one_site())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 10)
    vtr_routine.work_site(_ctx_only(), 6)
    assert len(calls) == vtr_routine.SITE_MAX_ACTIONS


def test_progress_signature_changes_when_any_site_advances():
    a = vt.parse_state({"sites": [{"siteNumber": 1, "baseValue": 0, "wood": "NONE",
                                   "baseCarved": False, "bottom": {"value": 0},
                                   "middle": {"value": 0}, "top": {"value": 0},
                                   "decorations": 0, "nearbySpirits": [],
                                   "durabilityRemaining": 0, "unclaimedOfferings": 0}],
                        "researchPoints": 0, "totalUnclaimedOfferings": 0,
                        "atValeTotems": True})
    b = vt.parse_state({"sites": [{"siteNumber": 1, "baseValue": 4, "wood": "YEW",
                                   "baseCarved": False, "bottom": {"value": 0},
                                   "middle": {"value": 0}, "top": {"value": 0},
                                   "decorations": 0, "nearbySpirits": [],
                                   "durabilityRemaining": 5, "unclaimedOfferings": 0}],
                        "researchPoints": 0, "totalUnclaimedOfferings": 0,
                        "atValeTotems": True})
    assert vtr_routine.progress_signature(a) != vtr_routine.progress_signature(b)


def test_the_routine_is_wired_into_the_gui_map():
    import controller as c
    assert c.ROUTINE_FILES.get("VALE_TOTEMS") == "routines/vale_totems.py"


def test_the_enum_and_the_map_agree():
    import pathlib
    import re
    import controller as c
    java = pathlib.Path(__file__).resolve().parents[2] / "src/main/java/com/example/controller/RoutineChoice.java"
    # [A-Z0-9_]+, not the brief's [A-Z_]+: GRANITE_3T (landed on this branch concurrently,
    # commit a580778) is a real enum entry with a digit in its name, and the map-vs-enum
    # invariant this test checks has to hold for it too.
    names = set(re.findall(r"^\t([A-Z0-9_]+)\(", java.read_text(), re.M))
    assert "VALE_TOTEMS" in names
    assert set(c.ROUTINE_FILES) <= names
