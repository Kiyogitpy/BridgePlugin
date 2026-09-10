import pytest

import controller
import vale_totems as vt
import vale_totem_route as route
from routines import vale_totems as vtr_routine
import bank_memory

_REAL_WALK_TO_SITE = vtr_routine._walk_to_site   # the autouse fixture stubs it; leg tests want it back
_REAL_DEPOSIT_JUNK = vtr_routine._deposit_junk
_REAL_PREHOVER = vtr_routine._prehover_first_action   # the autouse fixture stubs it too


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)
    monkeypatch.setattr(bank_memory, "load", lambda names=None: None)
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {"open": True, "items": []})
    monkeypatch.setattr(vtr_routine, "_await_bank_slot", lambda name: True)
    monkeypatch.setattr(vtr_routine, "_deposit_junk", lambda ctx: None)
    # tick() now walks the next leg and pre-hovers the first target itself; neither belongs
    # in a test of the site logic, and both touch the serial port.
    monkeypatch.setattr(vtr_routine, "_walk_to_site", lambda ctx, n: None)
    monkeypatch.setattr(vtr_routine, "_prehover_first_action", lambda ctx, n: None)
    # The direct leg's viewport click: off-screen by default, so every existing walk_leg test
    # keeps exercising the waypoint hops it was written against.
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": False})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui", lambda x, y: None)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": False})
    monkeypatch.setattr(controller, "is_point_on_minimap_orb", lambda x, y: None)
    # face_toward drives the Arduino (middle-mouse drag); off by default so existing walk_leg
    # tests exercise the waypoint hops. The camera tests below override it explicitly.
    monkeypatch.setattr(controller, "face_toward",
                        lambda ser, tx, ty, px=None, py=None: False)
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda ctx, point, shift: pytest.fail("unexpected viewport walk click"))


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
            # controller.Context has NO .config -- every routine reads ctx.state.config, and
            # a fixture that invents ctx.config is why 32 routine tests passed against a shape
            # the engine never provides. See CLAUDE.md: a stub that lies about a shape is
            # worse than no stub.
            self.config = vtr_routine.get_config(None)

        def trigger_safety_stop(self, reason):
            stops.append(reason)

    class _Ctx:
        def __init__(self):
            self.ser = object()
            self.state = _State()
            self.events = None

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
            self.config = vtr_routine.get_config(None)

        def trigger_safety_stop(self, reason):
            raise AssertionError("unexpected safety stop: %s" % reason)

    class _Ctx:
        def __init__(self):
            self.ser = object()
            self.state = _State()
            self.events = None

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
    # The key is re-pressed once when nothing carves (see the re-press tests), so the
    # assertion is about WHICH spirit, not how many presses.
    assert chosen and set(chosen) == {"EAGLE"}, "carve used the stale trio instead of re-reading"


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
    # One bow in hand: the one click is confirmed once it has gone on (1 -> 2).
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 1 if name == "Yew longbow (u)" else 0)
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


def _fake_clock(monkeypatch):
    """The blast_furnace fake-clock convention -- walk_leg's arrival poll would otherwise cost
    LEG_ARRIVAL_POLL_S of real wall clock in every walk test."""
    now = {"t": 0.0}
    monkeypatch.setattr(vtr_routine, "_now", lambda: now["t"])
    monkeypatch.setattr(vtr_routine, "_sleep",
                        lambda s: now.__setitem__("t", now["t"] + s))
    return now


def _standing_at(monkeypatch, x, y):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": x, "y": y, "plane": 0})


def test_walk_leg_resumes_ahead_of_the_player_never_behind(monkeypatch):
    """A leg is resumed mid-way far more often than started clean -- after a bank trip
    (which leaves the player beside the booth ON leg 8->7), after a restart. The booth
    (1416,3350) sits between the first two markers, 11 tiles from (1415,3339) and 19 from
    (1435,3346); with the stand tile not rendered, the far click is the marker AHEAD."""
    _fake_clock(monkeypatch)
    points = route.leg_waypoints(8, 7)
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": (x, y) != route.SITES[7]["stand"],
                                                     "screenX": 500, "screenY": 300, "tile": (x, y)})
    clicks = []

    def click(ctx, point, shift):
        clicks.append(point["tile"])
        _standing_at(monkeypatch, *route.SITES[7]["stand"])

    monkeypatch.setattr(travel, "_click_walk_point", click)
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    _standing_at(monkeypatch, *route.BANK["stand"])
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7) is True
    assert points[0] not in clicks, "the resumed leg must not walk backwards"
    assert all(x > route.BANK["stand"][0] for x, y in clicks), "every click is ahead (east)"


def test_walk_leg_waits_for_arrival_rather_than_returning_on_the_last_click(monkeypatch):
    """C3: _hop_to releases a long hop with HOP_NEXT_CLICK_LEAD_TILES still to run, so the
    player is still moving when the last hop returns."""
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane, **k: True)
    _standing_at(monkeypatch, *route.SITES[6]["stand"])
    # Never arrives -> the poll is spent and the leg is reported as a failure, not a success.
    assert vtr_routine.walk_leg(_ctx_only(), 6, 5) is False
    assert clock["t"] >= vtr_routine.LEG_ARRIVAL_POLL_S


def test_the_arrival_tolerance_sits_above_travels_early_release():
    """The two numbers are coupled: _hop_to returns with HOP_NEXT_CLICK_LEAD_TILES still to
    run, so a tolerance at or below it can never see a completed walk as an arrival."""
    assert vtr_routine.ARRIVAL_TOLERANCE > travel.HOP_NEXT_CLICK_LEAD_TILES


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


def _shift_keys(monkeypatch, log):
    monkeypatch.setattr(controller, "key_down", lambda ser, k: log.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: log.append(("up", k)))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)


def test_empty_basket_is_a_shift_left_click_for_empty(monkeypatch):
    """MEASURED 2026-09-09: even the OPEN basket's field menu has no top-level Empty (a
    submenu option /menu cannot list) and a plain left click hovers Fill. The recording's
    field Empty is SHIFT held ~0.25s then a left click on "Open log basket" (CC_OP Empty,
    sub-option id 65540), three times out of three."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: pytest.fail("the menu cannot reach Empty"))
    log = []
    _shift_keys(monkeypatch, log)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: log.append(("click", k.get("expected_action"))) or True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    assert log == [("down", "SHIFT"), ("click", "Empty"), ("up", "SHIFT")]


def test_a_withheld_shift_click_falls_back_to_one_ungated_shift_click(monkeypatch):
    """The gate reads the un-shifted default on some clients; the inventory delta is then the
    only verdict, and SHIFT stays held for the fallback and is released after it."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    log = []
    _shift_keys(monkeypatch, log)
    replies = iter([controller.CLICK_WITHHELD, True])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: log.append(("click", k.get("expected_action"))) or next(replies))
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    assert log == [("down", "SHIFT"), ("click", "Empty"), ("click", None), ("up", "SHIFT")]


def test_shift_is_released_even_when_the_click_raises(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    log = []
    _shift_keys(monkeypatch, log)

    def boom(*a, **k):
        raise RuntimeError("serial died")

    monkeypatch.setattr(controller, "click_entity", boom)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    with pytest.raises(RuntimeError):
        vtr_routine.empty_basket(_ctx_only())
    assert log[-1] == ("up", "SHIFT"), "a held SHIFT would turn every later click into a force-walk"


def test_empty_basket_is_a_no_op_without_a_basket(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Knife"}])
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: pytest.fail("clicked with no basket"))
    assert vtr_routine.empty_basket(_ctx_only()) is False


def _at_the_bank(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": route.BANK["stand"][0], "y": route.BANK["stand"][1],
                                 "plane": 0})
    monkeypatch.setattr(travel, "_hop_to",
                        lambda ctx, x, y, plane, **k: True)


def test_bank_trip_withdraws_and_fills_for_every_round(monkeypatch):
    _at_the_bank(monkeypatch)
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


def test_bank_trip_walks_to_the_booth_before_looking_for_it(monkeypatch):
    """C4b: the booth is 70-80 tiles from sites 2-5 and outside the loaded scene there, so
    get_object_at cannot resolve it from where the routine stands. Nothing used to walk."""
    hops = []
    monkeypatch.setattr(travel, "_hop_to",
                        lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    # Site 8's stand tile: one read for the direct attempt's own "not at the booth" check,
    # one for the hop that follows; then the booth.
    positions = iter([{"x": 1397, "y": 3329, "plane": 0}] * 2)

    def player():
        try:
            return next(positions)
        except StopIteration:
            return {"x": route.BANK["stand"][0], "y": route.BANK["stand"][1], "plane": 0}

    monkeypatch.setattr(controller, "get_player", player)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2, "onScreen": True, "distance": 21})
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(banker, "is_open", lambda: False)          # the direct click never opened it
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: True)
    monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    assert vtr_routine.bank_trip(_ctx_only()) is True
    assert hops == [route.BANK["stand"]], "the hop is the fallback when the direct click fails"


def test_bank_trip_clicks_the_booth_directly_from_the_site_when_it_is_on_screen(monkeypatch):
    """USER 2026-09-10: "click the banker directly after the decoration is done. that way we
    save a bit of time not doing hop -> click bank". The booth is ~21 tiles from site 8; the
    game walks there and opens on arrival, so no hop and no idle wait."""
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped to the booth"))
    _park_at(monkeypatch, 8)
    booth = {"id": route.BANK["object_id"], "screenX": 700, "screenY": 300, "onScreen": True,
             "distance": 21}
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: booth)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    clicks = []
    opened = {"v": False}

    def click(ser, ent, **k):
        clicks.append(ent.get("id"))
        opened["v"] = True
        return True

    monkeypatch.setattr(controller, "click_entity", click)
    monkeypatch.setattr(banker, "is_open", lambda: opened["v"])
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: True)
    monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    assert vtr_routine.bank_trip(_ctx_only()) is True
    assert clicks == [route.BANK["object_id"]]


def test_the_decorate_at_the_bank_site_turns_the_camera_toward_the_booth_when_a_trip_is_due(monkeypatch):
    """USER 2026-09-10: "while decorating we can adjust camera if needed and click the
    banker directly after the decoration is done"."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())   # 8 builds owed
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)           # nothing in hand
    site = vt.parse_state(_full_state(baseValue=4, wood="YEW", baseCarved=True,
                                      bottom={"value": 10}, middle={"value": 11},
                                      top={"value": 13}, decorations=0)).sites[8]
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: site)
    monkeypatch.setattr(vt, "next_action", lambda st, **k: ("decorate", None))
    seen = {}

    def decorate(ctx, st, after_click=None):
        seen["hook"] = after_click
        monkeypatch.setattr(vt, "next_action", lambda s2, **k: ("done", None))
        return True

    monkeypatch.setattr(vtr_routine, "decorate", decorate)
    assert vtr_routine.work_site(_ctx_only(), 8) is True
    assert callable(seen["hook"]), "site 8 with a trip due: the decorate gets the camera hook"
    faced = []
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": False})
    monkeypatch.setattr(controller, "face_toward",
                        lambda ser, tx, ty, px=None, py=None: faced.append((tx, ty)) or True)
    seen["hook"]()
    assert faced == [route.BANK["tile"]]

    # ...and at a site the booth cannot be reached from, the camera faces the NEXT site
    # instead (MEASURED vt_markers9: the leg's opening drags overlap the decorations).
    seen.clear()
    faced.clear()
    monkeypatch.setattr(vt, "next_action", lambda st, **k: ("decorate", None))
    assert vtr_routine.work_site(_ctx_only(), 6) is True
    seen["hook"]()
    assert faced == [route.SITES[route.next_in_ring(6)]["stand"]]


def test_the_decorate_camera_hook_sends_nothing_when_the_next_site_is_already_on_screen(monkeypatch):
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True})
    monkeypatch.setattr(controller, "face_toward",
                        lambda *a, **k: pytest.fail("a rendered tile needs no drag"))
    vtr_routine._decorate_camera_hook(_ctx_only(), 6)()


def test_the_decorate_hook_runs_after_the_click_and_before_its_confirm(monkeypatch):
    order = []

    def click_site(ctx, n, which, budget, confirm, option=None, after_click=None):
        order.append("click")
        if after_click:
            after_click()
        order.append("confirm")
        return True

    monkeypatch.setattr(vtr_routine, "_click_site", click_site)
    monkeypatch.setattr(vtr_routine, "bow_count", lambda ctx, wood=None: 4)
    site = vt.parse_state(_full_state(baseValue=4, wood="YEW", baseCarved=True,
                                      bottom={"value": 10}, middle={"value": 11},
                                      top={"value": 13}, decorations=0)).sites[8]
    assert vtr_routine.decorate(_ctx_only(), site, after_click=lambda: order.append("camera")) is True
    assert order == ["click", "camera", "confirm"]


def test_bank_trip_closes_the_interface_with_the_token_the_firmware_understands(monkeypatch):
    """C2: Firmware.ino's keyFromToken only knows "ESC" -- "escape" returns 0 and sends
    NOTHING, which looks exactly like a keypress that landed."""
    _at_the_bank(monkeypatch)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: True)
    monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: True)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    vtr_routine.bank_trip(_ctx_only())
    assert keys == ["ESC"] * vtr_routine.BANK_ROUNDS
    assert "escape" not in keys


def test_bank_trip_logs_a_failed_fill_instead_of_swallowing_it(monkeypatch, capsys):
    """M4: an unfilled basket leaves the round's logs loose, so the NEXT withdraw has fewer
    free slots than it expects and the failure surfaces one step later, misattributed."""
    _at_the_bank(monkeypatch)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: True)
    monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: False)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    assert vtr_routine.bank_trip(_ctx_only()) is True
    assert "did not fill the basket" in capsys.readouterr().out


def test_work_site_runs_the_ladder_until_the_site_is_done(monkeypatch):
    calls = []
    seq = iter([("claim", None), ("build", None), ("carve", "EAGLE"),
                ("decorate", None), ("done", None)])
    monkeypatch.setattr(vt, "next_action", lambda site, **k: next(seq))
    monkeypatch.setattr(vtr_routine, "claim", lambda c, s: calls.append("claim") or True)
    monkeypatch.setattr(vtr_routine, "build", lambda c, s: calls.append("build") or True)
    monkeypatch.setattr(vtr_routine, "carve", lambda c, s: calls.append("carve") or True)
    monkeypatch.setattr(vtr_routine, "decorate",
                        lambda c, s, after_click=None: calls.append("decorate") or True)
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
    assert len(calls) == 1 + vtr_routine.SITE_ACTION_RETRIES, "one retry, then the visit ends"


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


# ---------------------------------------------------------------------------------------
# Final-review fix wave, 2026-09-08. Every test below pins a defect the per-task reviews
# could not see, and several of them fail against the code as it was reviewed.
# ---------------------------------------------------------------------------------------


def _full_state(**overrides):
    """Eight unbuilt sites, at the vale, unless told otherwise."""
    site = dict(baseValue=0, wood="NONE", baseCarved=False,
                bottom={"value": 0}, middle={"value": 0}, top={"value": 0},
                decorations=0, nearbySpirits=["BUFFALO", "JAGUAR", "SNAKE"],
                durabilityRemaining=0, unclaimedOfferings=0)
    site.update(overrides)
    return {"sites": [dict(site, siteNumber=n) for n in range(1, 9)],
            "researchPoints": 0, "totalUnclaimedOfferings": 0, "atValeTotems": True}


def _park_at(monkeypatch, site_number):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": route.SITES[site_number]["stand"][0],
                                 "y": route.SITES[site_number]["stand"][1], "plane": 0})


def _running_tick(monkeypatch, stops, payload=None, at=6):
    """A ctx plus every seam tick() touches, parked at site `at` with nothing to do."""
    body = payload if payload is not None else _full_state()
    monkeypatch.setattr(controller, "get_api", lambda g, r: body)
    _park_at(monkeypatch, at)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda ctx: True)
    monkeypatch.setattr(vtr_routine, "bank_trip",
                        lambda ctx: pytest.fail("bank_trip must not be attempted here"))
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    monkeypatch.setattr(vtr_routine, "_walk_to_site", lambda ctx, n: None)
    monkeypatch.setattr(vtr_routine, "_prehover_first_action", lambda ctx, n: None)
    return _ctx(monkeypatch, stops)


# --- C1: the routine reads its config from ctx.state.config -----------------------------

def test_the_routine_reads_its_config_from_ctx_state_config(monkeypatch):
    """controller.Context.__init__ sets ser/state/events and NOTHING else -- there is no
    ctx.config. Every other routine in the repo reads ctx.state.config. The fixture now
    matches the real Context exactly (no .config attribute at all), so a relapse raises
    AttributeError here rather than on the first live tick."""
    stops = []
    ctx = _running_tick(monkeypatch, stops, at=6)
    assert not hasattr(ctx, "config"), "the fixture must match controller.Context"
    seen = []
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: seen.append(name) or 5)
    vtr_routine.tick(ctx)
    assert stops == []
    # The names came out of ctx.state.config, so they are the configured wood's.
    assert "Yew logs" in seen


def test_setup_does_not_read_config_because_the_engine_has_not_resolved_it_yet():
    """The engine calls setup() BEFORE resolve_config() populates state.config
    (controller.py:3540 vs :3545), so a setup that reads the wood raises on the first load
    however it spells the lookup."""
    class _BareState:
        data = {}          # no .config: exactly what setup() is handed live

    class _BareCtx:
        ser = object()
        state = _BareState()
        events = None

    vtr_routine.setup(_BareCtx())      # must not raise


# --- C4b / I1: where the bank may be attempted from -------------------------------------

def test_no_bank_trip_from_a_site_the_booth_cannot_be_seen_from(monkeypatch):
    """C4b: from sites 2-5 the booth is 70-80 tiles away and outside the loaded scene, so
    get_object_at answers nothing and the trip could only ever fail -- which used to be a
    safety stop. Running out of logs anywhere but the bank leg is now simply carried."""
    for site in (1, 2, 3, 4, 5, 6):
        stops = []
        ctx = _running_tick(monkeypatch, stops, at=site)   # bank_trip fails the test if called
        vtr_routine.tick(ctx)
        assert stops == [], "out of logs at site %d must not stop the run" % site


def test_the_bank_leg_is_the_only_one_that_passes_the_booth():
    """The booth's stand tile is one hop (<= travel.WALK_HOP_MAX_TILES) from site 8's stand
    tile and beyond the minimap's 28-tile reach from every other site, which is the whole
    evidence BANK_SITES rests on -- so it is pinned here rather than only asserted in a
    comment. (With the 2026-09-10 markers the 8->7 leg no longer passes the booth itself;
    its first marker is 11 tiles from it, which is how the resume rejoins the leg.)"""
    bank = route.BANK["stand"]
    dist = {n: vtr_routine._tiles_between(*bank, *route.SITES[n]["stand"]) for n in route.RING}
    assert dist[8] <= travel.WALK_HOP_MAX_TILES
    assert all(d > 28 for n, d in dist.items() if n != 8), dist
    assert min(vtr_routine._tiles_between(*bank, *p) for p in route.LEGS[(8, 7)]) <= 12
    # Only 8: the booth is one hop from site 8's stand tile and ~37 tiles (off the minimap,
    # back along the leg just walked) from site 7's. MEASURED live 2026-09-09: the site-7
    # trip fired one site after a full bank at 8 and safety-stopped the run.
    assert set(vtr_routine.BANK_SITES) == {8}


def test_the_bank_trip_is_attempted_at_the_bank_site_when_logs_run_short(monkeypatch):
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    _park_at(monkeypatch, 8)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda ctx: True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    tried = []
    monkeypatch.setattr(vtr_routine, "bank_trip", lambda ctx: tried.append(1) or True)
    vtr_routine.tick(_ctx(monkeypatch, stops))
    assert tried == [1]
    assert stops == []


def test_only_a_bank_trip_that_was_actually_attempted_and_failed_stops_the_run(monkeypatch):
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    _park_at(monkeypatch, 8)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda ctx: True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    monkeypatch.setattr(vtr_routine, "bank_trip", lambda ctx: False)
    vtr_routine.tick(_ctx(monkeypatch, stops))
    assert stops and "bank trip" in stops[0]


def test_the_site_is_worked_before_the_bank_check(monkeypatch):
    """I1: holding 0 logs and 4 bows at a built, carved totem used to go banking instead of
    decorating it -- the decorations need BOWS, and we had them."""
    stops = []
    payload = _full_state(baseValue=4, wood="YEW", baseCarved=True, decorations=0,
                          durabilityRemaining=5)
    monkeypatch.setattr(controller, "get_api", lambda g, r: payload)
    _park_at(monkeypatch, 8)
    order = []
    monkeypatch.setattr(vtr_routine, "work_site",
                        lambda ctx, n: order.append("work") or True)
    monkeypatch.setattr(vtr_routine, "bank_trip",
                        lambda ctx: order.append("bank") or True)
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda ctx: True)
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: 0 if "logs" in name else 4)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    vtr_routine.tick(_ctx(monkeypatch, stops))
    assert order[0] == "work", "the totem must be decorated before any bank decision"


# --- C4a: the fletch is budgeted ---------------------------------------------------------

def test_the_fletch_is_skipped_when_every_log_is_owed_to_a_build(monkeypatch):
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    _park_at(monkeypatch, 6)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    monkeypatch.setattr(vtr_routine, "bank_trip", lambda ctx: True)
    # 8 unbuilt sites owe 8 build logs; we hold 3, so not one of them may become a bow.
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: 3 if "logs" in name else 0)
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda ctx: pytest.fail("fletched the logs the builds need"))
    vtr_routine.tick(_ctx(monkeypatch, stops))


def test_the_fletch_is_marked_due_once_the_builds_are_covered(monkeypatch):
    """It is DUE at the site and FIRED by walk_leg once the move command is out -- USER
    2026-09-09, "can only fletch after we give the move command". This used to assert that
    tick() fletched here, which is exactly the standstill fletch the next walk click cancels."""
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    _park_at(monkeypatch, 6)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    monkeypatch.setattr(vtr_routine, "bank_trip", lambda ctx: True)
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: 40 if "logs" in name else 0)
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda ctx: pytest.fail("fletched before the move command"))
    ctx = _ctx(monkeypatch, stops)
    vtr_routine.tick(ctx)
    assert ctx.state.data.get("vt_fletch_due") is True


# --- I2: the varbits every await_pre here waits on --------------------------------------

def test_the_eight_sites_varbit_blocks_are_subscribed():
    """Without these the bridge pushes no VARBIT_CHANGED at all (EventsProvider filters on
    the subscribed set), so every await_pre waits its whole budget and confirms only on the
    HTTP read at the deadline. Site 1's block starts at ENT_TOTEMS_SITE_1_BASE = 17611 and
    the blocks are 18 apart -- MEASURED with javap over runelite-api 1.12.38."""
    subs = set(vtr_routine.VARBIT_SUBSCRIPTIONS)
    for base in (17611, 17629, 17647, 17665, 17683, 17701, 17719, 17737):
        assert base in subs, base                       # baseValue: built, and in what wood
        assert base + 1 in subs, base                   # baseCarved: the carve confirm
        assert {base + 3, base + 4, base + 5} <= subs   # LOW/MID/TOP, the segment values
        assert base + 6 in subs, base                   # decorations: the decorate confirm
        assert base + 11 in subs, base                  # the offerings pile: the claim confirm
    assert len(subs) == 8 * 12
    # 17613 is ENT_TOTEMS_SITE_1_BASE_MULTILOC, not the block's start -- subscribing from
    # there would have missed BASE and BASE_CARVED, the two ids the build confirm rides on.
    assert min(subs) == 17611


# --- I3: never leave a group-270 prompt standing ----------------------------------------

def test_a_withheld_carve_escapes_the_prompt_it_refused_to_answer(monkeypatch):
    """I3: a stranded interface EATS THE NEXT WORLD CLICK (motherlode_mine, mlm_live8). The
    carve and fletch prompts are the same widget group, so a wrong title is still an open
    interface, and it survives into the leg walk."""
    monkeypatch.setattr(skillmulti, "showing", lambda t: False)
    monkeypatch.setattr(skillmulti, "prompt_title", lambda: skillmulti.TITLE_MAKE)
    monkeypatch.setattr(skillmulti, "is_open", lambda: True)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is False
    assert keys == ["ESC"]


def test_nothing_is_escaped_when_no_prompt_is_open(monkeypatch):
    monkeypatch.setattr(skillmulti, "showing", lambda t: False)
    monkeypatch.setattr(skillmulti, "prompt_title", lambda: "")
    monkeypatch.setattr(skillmulti, "is_open", lambda: False)
    monkeypatch.setattr(controller, "key_tap",
                        lambda ser, k: pytest.fail("pressed a key at nothing"))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is False


def test_a_fletch_that_finds_no_option_escapes_the_menu_it_opened(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Yew logs", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(skillmulti, "showing", lambda t: True)
    monkeypatch.setattr(skillmulti, "item_widget", lambda p: None)
    monkeypatch.setattr(skillmulti, "prompt_title", lambda: skillmulti.TITLE_MAKE)
    monkeypatch.setattr(skillmulti, "is_open", lambda: True)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.start_fletching(_ctx_only()) is False
    assert keys == ["ESC"]


# --- I4: one failed hop is not a broken route -------------------------------------------

def test_a_failed_leg_is_retried_before_it_stops_the_run(monkeypatch):
    monkeypatch.setattr(vtr_routine, "_walk_to_site", _REAL_WALK_TO_SITE)
    """I4: _hop_to returns False for a point it cannot project AT ALL -- a loading screen, a
    panel drawn over the minimap, the camera mid-swing. Hard-stopping the run on one of
    those throws away a working routine."""
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1450, "y": 3400, "plane": 0})
    monkeypatch.setattr(vtr_routine, "at_site", lambda n: False)
    monkeypatch.setattr(vtr_routine, "walk_leg", lambda ctx, a, b, **kw: False)
    ctx = _ctx(monkeypatch, stops)
    ctx.state.data["vt_site"] = 5
    ctx.state.data["vt_from"] = 6
    for attempt in range(1, vtr_routine.LEG_MAX_ATTEMPTS):
        vtr_routine.tick(ctx)
        assert stops == [], "stopped on attempt %d of %d" % (
            attempt, vtr_routine.LEG_MAX_ATTEMPTS)
    vtr_routine.tick(ctx)
    assert stops and "attempts" in stops[0]


def test_a_leg_that_succeeds_clears_the_retry_count(monkeypatch):
    monkeypatch.setattr(vtr_routine, "_walk_to_site", _REAL_WALK_TO_SITE)
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1450, "y": 3400, "plane": 0})
    monkeypatch.setattr(vtr_routine, "at_site", lambda n: False)
    outcomes = iter([False, True])
    monkeypatch.setattr(vtr_routine, "walk_leg", lambda ctx, a, b, **kw: next(outcomes))
    ctx = _ctx(monkeypatch, stops)
    ctx.state.data["vt_site"] = 5
    vtr_routine.tick(ctx)
    vtr_routine.tick(ctx)
    assert ctx.state.data["vt_leg_tries"] == 0


# --- I5: the stall watch cannot reset itself --------------------------------------------

def test_the_stall_signature_ignores_other_sites_because_ents_move_them():
    """I5: ents walk the whole ring every 48s and each visit knocks a totem's durability
    down, so a signature spanning all eight sites changes on its own every few minutes --
    which cleared the stall counter of a routine wedged in front of one site."""
    before = vt.parse_state(_full_state())
    after = vt.parse_state(_full_state())
    after.sites[3].decorations = 2       # an ent, or another player, moved a DIFFERENT site
    assert (vtr_routine.progress_signature(before, 6)
            == vtr_routine.progress_signature(after, 6))
    # ...while our own site advancing, and walking on to the next site, are both progress.
    after.sites[6].decorations = 1
    assert (vtr_routine.progress_signature(before, 6)
            != vtr_routine.progress_signature(after, 6))
    assert (vtr_routine.progress_signature(before, 6)
            != vtr_routine.progress_signature(before, 5))


def test_a_wedged_site_eventually_safety_stops(monkeypatch):
    stops = []
    ctx = _running_tick(monkeypatch, stops, at=6)
    for _ in range(vtr_routine.STALL_STOP_TICKS + 2):
        if stops:
            break
        ctx.state.data["vt_site"] = 6        # pin the site: nothing is advancing
        vtr_routine.tick(ctx)
    assert stops and "no progress at site 6" in stops[0]


# --- I6: a restart lands on the nearest site, not RING[0] --------------------------------

def test_a_restart_picks_the_nearest_site_not_the_first_in_the_ring(monkeypatch):
    """I6: min(RING, key=lambda n: 0 if at_site(n) else 1) keeps the FIRST minimum, so a
    player standing on no stand tile at all was placed at RING[0] = 6 and walked leg 7->6
    from wherever it happened to be, across terrain no waypoint covers."""
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": route.SITES[2]["stand"][0] + 2,
                                 "y": route.SITES[2]["stand"][1] + 2, "plane": 0})
    assert vtr_routine.nearest_site() == 2


def test_a_player_far_from_the_ring_stands_by_rather_than_walking(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    assert vtr_routine.nearest_site() is None
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(vtr_routine, "walk_leg",
                        lambda ctx, a, b: pytest.fail("walked from off the ring"))
    vtr_routine.tick(_ctx(monkeypatch, stops))
    assert stops == [], "standing by is not a safety stop"


def test_every_point_of_every_leg_is_inside_the_restart_search_radius():
    """SITE_SEARCH_RADIUS has to cover the whole ring, or a restart mid-leg stands by
    forever instead of resuming."""
    for (a, b), points in route.LEGS.items():
        for x, y in points:
            nearest = min(vtr_routine._tiles_between(x, y, *route.SITES[n]["stand"])
                          for n in route.RING)
            assert nearest <= vtr_routine.SITE_SEARCH_RADIUS, ((a, b), (x, y), nearest)


# --- I7: the engine's breaks config survives get_config ----------------------------------

def test_get_config_keeps_the_bridge_keys_so_before_break_can_fire():
    """I7: the engine reads breaks/safety out of the same dict get_config returns, so
    returning only the four wood keys means ControllerState.break_due never sees a breaks
    block at all and before_break can never fire."""
    cfg = vtr_routine.get_config({"valeTotemsWood": "MAPLE",
                                  "breaks": {"enabled": True, "minMinutes": 3},
                                  "safety": {"maxRuntimeHours": 6}})
    assert cfg["breaks"] == {"enabled": True, "minMinutes": 3}
    assert cfg["safety"] == {"maxRuntimeHours": 6}
    # ...and the wood keys still win over anything the bridge sent under the same names.
    assert cfg["wood"] == "MAPLE" and cfg["log"] == "Maple logs"


def test_get_config_survives_a_bridge_that_answers_nothing():
    cfg = vtr_routine.get_config(None)
    assert cfg["wood"] == "YEW"


# --- M3: the basket is matched case-insensitively ----------------------------------------

def test_find_basket_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "open log BASKET", "screenX": 1, "screenY": 1}])
    assert vtr_routine.find_basket() is not None


# --- 2026-09-09 live fixes: the basket counts, and a full basket ends the bank trip --------

def test_the_lap_check_counts_the_logs_in_the_basket(monkeypatch):
    """vt_live1: 28 in the basket + 19 loose against a 30-log lap, and the check counted only
    the loose 19 -- a 37-tile trip back to the booth one site after banking there."""
    stops = []
    # Five sites finished, three unbuilt: a 15-log lap.
    payload = _full_state(baseValue=4, wood="YEW", baseCarved=True, decorations=4,
                          durabilityRemaining=5)
    for site in payload["sites"][:3]:
        site.update(baseValue=0, wood="NONE", baseCarved=False, decorations=0,
                    durabilityRemaining=0)
    monkeypatch.setattr(controller, "get_api", lambda g, r: payload)
    _park_at(monkeypatch, 8)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda ctx: True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    monkeypatch.setattr(vtr_routine, "bank_trip",
                        lambda ctx: pytest.fail("the basket holds 28 -- no trip is due"))
    ctx = _ctx(monkeypatch, stops)
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 28
    vtr_routine.tick(ctx)
    assert stops == []


def test_an_unknown_basket_counts_as_empty_so_the_first_lap_still_banks(monkeypatch):
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    _park_at(monkeypatch, 8)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda ctx: True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    tried = []
    monkeypatch.setattr(vtr_routine, "bank_trip", lambda ctx: tried.append(1) or True)
    vtr_routine.tick(_ctx(monkeypatch, stops))
    assert tried == [1]


def _basket_ctx(monkeypatch, counts):
    """count_item answers the next value from `counts` on every call, holding the last."""
    # OPEN, because only 28142 log_basket_open carries Empty (live + cache, 2026-09-09) --
    # these tests are about the fill/empty ESTIMATE, not about opening.
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, ent, opt, **k: True)
    _shift_keys(monkeypatch, [])
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    seq = list(counts)
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: seq.pop(0) if len(seq) > 1 else seq[0])
    return _ctx_only()


def test_fill_basket_adds_what_left_the_inventory_to_the_estimate(monkeypatch):
    ctx = _basket_ctx(monkeypatch, [21, 0, 0])      # 21 loose before, 0 after
    vtr_routine.fill_basket(ctx)
    assert vtr_routine.basket_logs(ctx.state.data) == 21


def test_a_fill_that_moves_nothing_means_the_basket_is_full(monkeypatch):
    """The recorder showed the Fill entry resolving correctly and the game answering
    "The basket is full." -- the user had filled it by hand before the run."""
    ctx = _basket_ctx(monkeypatch, [21, 21, 21])
    vtr_routine.fill_basket(ctx)
    assert vtr_routine.basket_logs(ctx.state.data) == vtr_routine.BASKET_CAPACITY


def test_empty_basket_subtracts_what_arrived_and_zeroes_on_nothing(monkeypatch):
    # 27 free slots in the fixture's inventory; 21 arrived, fewer than could fit, so the
    # basket handed over everything it had: empty now, whatever the estimate said.
    ctx = _basket_ctx(monkeypatch, [0, 21, 21])
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 28
    vtr_routine.empty_basket(ctx)
    assert vtr_routine.basket_logs(ctx.state.data) == 0
    ctx = _basket_ctx(monkeypatch, [0, 0, 0])
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 7
    vtr_routine.empty_basket(ctx)
    assert vtr_routine.basket_logs(ctx.state.data) == 0


def test_bank_trip_stops_its_rounds_once_the_basket_is_full(monkeypatch):
    """vt_live1: rounds 2 and 3 against an already-full basket drew "The basket is full."
    and "You don't have enough inventory space." -- ~15s of nothing."""
    _at_the_bank(monkeypatch)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    withdrawals = []
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, fresh=False: withdrawals.append(name) or True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    fills = []

    def fill(ctx):
        fills.append(1)
        ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = vtr_routine.BASKET_CAPACITY
        return False        # nothing moved

    monkeypatch.setattr(vtr_routine, "fill_basket", fill)
    assert vtr_routine.bank_trip(_ctx_only()) is True
    assert fills == [1]
    assert withdrawals == ["Yew logs"], "a full basket ends the trip with the round's logs in hand"


def test_empty_basket_opens_a_shut_basket_first(monkeypatch):
    """MEASURED live 2026-09-09: a right-click on the SHUT basket offers no Empty at all
    (['Cancel','Examine','Destroy','Open','Use','Check','Wear','Fill']), and the cache agrees
    -- only 28142 log_basket_open carries Empty."""
    names = ["Log basket", "Log basket", "Open log basket", "Open log basket"]
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": names[min(len(names) - 1, calls["n"])],
                                  "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    calls = {"n": 0}
    chosen = []

    def menu(ser, ent, opt, **k):
        chosen.append(tuple(opt))
        calls["n"] += 2      # the basket reads as OPEN from the next look on
        return True

    monkeypatch.setattr(controller, "click_menu_action", menu)
    _shift_keys(monkeypatch, [])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: chosen.append(("left", k.get("expected_action"))) or True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    assert chosen and chosen[0] == ("open",), "did not open the shut basket first"
    assert ("left", "Empty") in chosen[1:], "never tried to empty it"


def test_empty_basket_does_not_reopen_an_already_open_basket(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    chosen = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, opt, **k: chosen.append(tuple(opt)) or True)
    _shift_keys(monkeypatch, [])
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    assert not chosen, "re-opened a basket that was already open"


# --- 2026-09-09 live: the fletch must follow the move command, never precede it ------------

def test_the_site_only_marks_a_fletch_due_it_does_not_start_one(monkeypatch):
    """USER 2026-09-09: "can only fletch after we give the move command". A knife-on-log
    started at a standstill is cancelled by the next walk click, so tick() must not fire one
    while it is still standing at the site."""
    stops = []
    ctx = _running_tick(monkeypatch, stops, at=6)
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda c: pytest.fail("fletched before the move command"))
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 10)
    vtr_routine.tick(ctx)
    assert ctx.state.data.get("vt_fletch_due") is True


def test_with_nothing_to_far_click_the_fallback_is_one_hop_toward_the_stand_tile(monkeypatch):
    """USER 2026-09-10: "click straight near the totems no small hops or shortcuts". When
    neither the stand tile nor a marker projects, the one fallback is a minimap hop AT the
    stand tile (travel pulls it in toward the player when it is beyond reach) -- never the
    markers one by one -- and the fletch follows that move."""
    order = []

    def hop(ctx, x, y, plane, **k):
        order.append(("hop", x, y))
        _standing_at(monkeypatch, *route.SITES[7]["stand"])
        return True

    monkeypatch.setattr(travel, "_hop_to", hop)
    _standing_at(monkeypatch, *route.SITES[8]["stand"])
    vtr_routine.walk_leg(_ctx_only(), 8, 7, after_first_hop=lambda: order.append(("fletch",)))
    assert order == [("hop",) + route.SITES[7]["stand"], ("fletch",)]


def test_the_hook_strings_when_there_is_nothing_worth_fletching(monkeypatch):
    """USER 2026-09-10: "nor is it stringing the bows". Live (vt_markers2) the later legs held
    5 logs all owed to builds and 6 unstrung bows: fletch budget 0, below the 8-bow stringing
    threshold, so the leg walked with the hands idle. Stringing is free XP on the same walk
    and a strung bow decorates just as well, so with nothing to fletch the spool is used."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: {"Yew logs": 5, "Yew longbow (u)": 6}.get(name, 0))
    monkeypatch.setattr(vt, "fletch_budget", lambda state, logs, bows: 0)
    monkeypatch.setattr(vtr_routine, "find_spool", lambda: {"name": "Bow string spool"})
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda c: pytest.fail("fletched a log a build is owed"))
    strung = []
    monkeypatch.setattr(vtr_routine, "start_stringing", lambda c: strung.append(1) or True)
    ctx = _ctx_only()
    ctx.state.data["vt_fletch_due"] = True
    assert vtr_routine._fletch_on_the_move(ctx) == "string"
    assert strung == [1]


def test_the_fletch_hook_rechecks_the_budget_against_live_state(monkeypatch):
    """The flag was set at the previous site; a log owed to a build must never become a bow."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)   # no logs at all
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda c: pytest.fail("fletched with nothing to spare"))
    ctx = _ctx_only()
    ctx.state.data["vt_fletch_due"] = True
    assert vtr_routine._fletch_on_the_move(ctx) is False


def test_a_minimap_point_counts_as_a_far_click_when_nothing_is_rendered(monkeypatch):
    """MEASURED 2026-09-09: viewport-only picks were 11-18 tiles, 3-5 bows. The minimap
    reaches 28 tiles whatever is rendered."""
    points = route.leg_waypoints(8, 7)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": (x, y) == points[0],
                                                     "screenX": 1900, "screenY": 200})
    clicks = []
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda ctx, point, shift: clicks.append(shift))
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    seq = iter([_stand(8), _stand(8)] + [_stand(7)] * 20)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7) is True
    assert clicks == [False], "one minimap click (no SHIFT on the minimap)"


def test_the_hook_fires_on_every_stretch_long_enough_to_be_worth_it(monkeypatch):
    """USER 2026-09-10: "no stringing on that long walk?" -- a 48-tile first stretch toward
    site 1 ran with idle hands because the action waited for the stand-tile click, 9 tiles
    from the end. The action starts on any stretch of HOOK_MIN_STRETCH_TILES or more and on
    the final one; a later click restarts it (the hook re-derives its budget live)."""
    _fake_clock(monkeypatch)
    player = {"xy": route.BANK["stand"]}
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": player["xy"][0], "y": player["xy"][1], "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {
                            "onScreen": vtr_routine._tiles_between(x, y, *player["xy"]) <= 20,
                            "screenX": 500, "screenY": 300, "tile": (x, y)})
    order = []

    def click(ctx, point, shift):
        order.append(("click", point["tile"]))
        player["xy"] = point["tile"]

    monkeypatch.setattr(travel, "_click_walk_point", click)
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7, after_first_hop=lambda: order.append(("fletch",))) is True
    stand = route.SITES[7]["stand"]
    assert order[0][0] == "click" and order[0][1] != stand and order[1] == ("fletch",),         "the first stretch (~20 tiles) gets the action"
    assert order[-2] == ("click", stand) and order[-1] == ("fletch",),         "...and the final stretch restarts it"


def test_a_short_stretch_that_is_not_final_does_not_start_the_action(monkeypatch):
    fired = []
    tiles = vtr_routine.HOOK_MIN_STRETCH_TILES - 1
    assert vtr_routine._hook_due(tiles, final=False) is False
    assert vtr_routine._hook_due(tiles, final=True) is True
    assert vtr_routine._hook_due(vtr_routine.HOOK_MIN_STRETCH_TILES, final=False) is True


def test_the_far_click_runs_the_straight_line_toward_the_stand_before_any_marker(monkeypatch):
    """USER 2026-09-10: "we click the tile marker 20 instead of just running straight to
    totem ... they are guidelines not meant as a hop"; "we saw the totem spot on screen".
    MEASURED: from the booth, site 7's stand tile (37 tiles) projected ABOVE the viewport's
    top edge (isOnScreen is a bounds test) while the ground short of it was on screen. The
    click goes to the farthest on-screen point on the straight line toward the stand; a
    marker is only a fallback when nothing on that line projects."""
    _fake_clock(monkeypatch)
    player = {"xy": route.BANK["stand"]}
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": player["xy"][0], "y": player["xy"][1], "plane": 0})
    # Rendered within 25 tiles of the player -- the stand (37) is not, marker 20 (19) is.
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {
                            "onScreen": vtr_routine._tiles_between(x, y, *player["xy"]) <= 25,
                            "screenX": 500, "screenY": 300, "tile": (x, y)})
    clicks = []

    def click(ctx, point, shift):
        clicks.append(point["tile"])
        player["xy"] = point["tile"]

    monkeypatch.setattr(travel, "_click_walk_point", click)
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7) is True
    stand = route.SITES[7]["stand"]
    first = clicks[0]
    assert first != (1435, 3346), "marker 20 is a guide, not a hop"
    assert 20 <= vtr_routine._tiles_between(*route.BANK["stand"], *first) <= 25, first
    # ...and ON the straight line booth -> stand (within a tile of it).
    bx, by = route.BANK["stand"]
    dx, dy = stand[0] - bx, stand[1] - by
    t = ((first[0] - bx) * dx + (first[1] - by) * dy) / (dx * dx + dy * dy)
    assert abs(first[0] - (bx + t * dx)) <= 1 and abs(first[1] - (by + t * dy)) <= 1
    assert clicks[-1] == stand


def test_a_marker_already_reached_is_never_the_far_click(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers2, legs 6->5 and 5->4): the stretch to the first
    marker ended within ARRIVAL_TOLERANCE of it, the stand tile was neither rendered nor on
    the minimap, and the next pick chose the SAME marker -- still rendered, three tiles away
    -- five times in five seconds, each click ending the fletch. F1. A marker the player has
    already reached is not a far click; with nothing farther reachable the hops take over,
    and they start at the marker AFTER the one already reached."""
    _fake_clock(monkeypatch)
    points = route.leg_waypoints(5, 4)          # (1422,3307), then the stand (1413,3287)
    _standing_at(monkeypatch, points[0][0] + 2, points[0][1] + 1)      # 2 tiles from marker 0
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": (x, y) == points[0],
                                                     "screenX": 500, "screenY": 300})
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda *a, **k: pytest.fail("clicked the marker already reached"))
    hops = []

    def hop(ctx, x, y, plane, **k):
        hops.append((x, y))
        _standing_at(monkeypatch, *route.SITES[4]["stand"])
        return True

    monkeypatch.setattr(travel, "_hop_to", hop)
    assert vtr_routine.walk_leg(_ctx_only(), 5, 4) is True
    assert hops == [route.SITES[4]["stand"]]


def test_a_far_click_on_an_orb_is_not_taken(monkeypatch):
    points = route.leg_waypoints(8, 7)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 1966, "screenY": 151})
    monkeypatch.setattr(controller, "is_point_on_minimap_orb", lambda x, y: {"x": 1910})
    monkeypatch.setattr(travel, "_click_walk_point", lambda *a, **k: pytest.fail("clicked an orb"))
    hops = []
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane, **k: hops.append(1) or True)
    monkeypatch.setattr(controller, "get_player", lambda: _stand(8))
    _fake_clock(monkeypatch)
    vtr_routine.walk_leg(_ctx_only(), 8, 7)
    assert hops


def test_the_fletch_flag_survives_the_leg_and_is_cleared_at_the_site(monkeypatch):
    """Every far click ends the running fletch, so the flag must still be up for the next
    stretch to restart it; it is cleared once the site is reached."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 40 if "logs" in name else 0)
    fired = []
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda c: fired.append(1) or True)
    ctx = _ctx_only()
    ctx.state.data["vt_fletch_due"] = True
    assert vtr_routine._fletch_on_the_move(ctx) == "fletch"
    assert vtr_routine._fletch_on_the_move(ctx) == "fletch", "a later call still answers"
    assert fired == [1, 1]
    stops = []
    ctx2 = _running_tick(monkeypatch, stops, at=6)
    ctx2.state.data["vt_fletch_due"] = True
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    vtr_routine.tick(ctx2)
    assert ctx2.state.data.get("vt_fletch_due") is False, "cleared on arrival, before the site work"


def test_the_fletch_hook_is_a_no_op_when_nothing_is_due(monkeypatch):
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda c: pytest.fail("fletched with no flag set"))
    assert vtr_routine._fletch_on_the_move(_ctx_only()) is False


# --- 2026-09-09: one far click per leg, then hands off, or the fletch never lands ----------

def _stand(site):
    return {"x": route.SITES[site]["stand"][0], "y": route.SITES[site]["stand"][1], "plane": 0}


def test_a_rendered_stand_tile_gets_one_viewport_click_and_no_hops(monkeypatch):
    """The human's shape: one Walk here on the far tile, knife, then NOTHING until the site.
    Every waypoint hop the bot used to click ended the fletch before its first bow."""
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 500, "screenY": 300})
    clicks, order = [], []
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda ctx, point, shift: clicks.append((point, shift)) or order.append("walk"))
    monkeypatch.setattr(travel, "_hop_to",
                        lambda *a, **k: pytest.fail("hopped -- that click would end the fletch"))
    positions = iter([_stand(8), {"x": 1420, "y": 3340, "plane": 0}, _stand(7), _stand(7)])
    monkeypatch.setattr(controller, "get_player", lambda: next(positions))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7,
                                after_first_hop=lambda: order.append("fletch")) is True
    assert len(clicks) == 1 and clicks[0][1] is True, "exactly one SHIFT viewport click"
    assert order == ["walk", "fletch"], "the move goes out first, the fletch right after"


def test_a_stalled_far_click_is_retried_in_place_bounded_and_the_hook_fires_once(monkeypatch):
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 500, "screenY": 300})
    clicks = []
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: clicks.append(1))
    hops = []
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    # Never moves off site 8's stand tile: every click stalls, each target is banned in turn,
    # then the stand-tile hop is the fallback, all inside DIRECT_LEG_MAX_STRETCHES.
    monkeypatch.setattr(controller, "get_player", lambda: _stand(8))
    clock = {"t": 0.0}
    monkeypatch.setattr(vtr_routine, "_now", lambda: clock["t"])
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    fired = []
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7, after_first_hop=lambda: fired.append(1)) is False
    assert len(clicks) + len(hops) == vtr_routine.DIRECT_LEG_MAX_STRETCHES
    assert fired, "the stand-tile click, which stalled first, still started the action"


def test_an_off_screen_stand_tile_skips_the_direct_click_entirely(monkeypatch):
    _fake_clock(monkeypatch)
    hops = []
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    monkeypatch.setattr(controller, "get_player", lambda: _stand(8))
    vtr_routine.walk_leg(_ctx_only(), 8, 7)
    assert hops and set(hops) == {route.SITES[7]["stand"]},         "with nothing rendered the stand-tile hop is the only way there"


def test_the_direct_click_aims_at_the_farthest_rendered_waypoint_when_the_site_is_not(monkeypatch):
    """MEASURED 2026-09-09: the next stand tile is 20-57 tiles off and not rendered, so aiming
    only there fell back to the hops every leg. A waypoint 10-20 tiles out is; 12s of running
    there is 6 bows. Rendering here is distance-based (anything within 20 tiles of the player
    is on screen), so each stretch reaches the farthest marker the camera can see and the
    8->7 leg is three chained clicks: 18, 20 and 18 tiles."""
    points = route.leg_waypoints(8, 7)
    player = {"xy": route.SITES[8]["stand"]}
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": player["xy"][0], "y": player["xy"][1], "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {
                            "onScreen": vtr_routine._tiles_between(x, y, *player["xy"]) <= 20,
                            "screenX": 500, "screenY": 300, "tile": (x, y)})
    clicks = []

    def click(ctx, point, shift):
        clicks.append(point["tile"])
        player["xy"] = point["tile"]          # the run to the clicked tile, elided

    monkeypatch.setattr(travel, "_click_walk_point", click)
    hops = []
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    fired = []
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7, after_first_hop=lambda: fired.append(1)) is True
    assert clicks[-1] == route.SITES[7]["stand"]
    prev = route.SITES[8]["stand"]
    for tile in clicks:
        assert 8 <= vtr_routine._tiles_between(*prev, *tile) <= 20, (prev, tile)
        prev = tile
    assert fired == [1] * len(clicks), "the fletch is (re)started on every long stretch"
    assert hops == [], "no hop click while a far click can still reach"


def test_the_fletch_is_stopped_at_the_build_reserve_by_reclicking_the_walk(monkeypatch):
    """USER 2026-09-09: "gotta leave a few logs in the inventory to actually craft the
    totems too" -- the make prompt has no quantity, so the stretch itself must end the fletch
    once only the build reserve is left, with a click that keeps the walk."""
    # Every resolution is a fresh dict stamped with the tile and a serial, so the test can
    # tell "the same TILE, re-resolved" from "the same PIXEL, reused".
    resolved = {"n": 0}

    def ground(x, y, p, inst=False):
        resolved["n"] += 1
        return {"onScreen": True, "screenX": 500 + resolved["n"], "screenY": 300,
                "tile": (x, y), "serial": resolved["n"]}

    monkeypatch.setattr(controller, "get_ground_walk_point", ground)
    clicks = []
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: clicks.append(point))
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    seq = iter([_stand(8), _stand(8), _stand(8), {"x": 1420, "y": 3340, "plane": 0},
                {"x": 1430, "y": 3342, "plane": 0}, {"x": 1440, "y": 3342, "plane": 0}]
               + [_stand(7)] * 20)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    logs = iter([6, 4, 3, 3, 3, 3, 3, 3])          # loose logs falling as bows are made
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: next(logs))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.LOG_RESERVE_KEY] = 3
    assert vtr_routine.walk_leg(ctx, 8, 7, after_first_hop=lambda: "fletch") is True
    assert len(clicks) == 2, "the walk click, then ONE re-click at the reserve"
    assert clicks[0]["tile"] == clicks[1]["tile"], "the re-click aims at the same TILE"
    # MEASURED live 2026-09-10 (vt_markers4, 6->5): re-clicking the same PIXEL after the
    # player has run six tiles maps to a tile far beyond the target -- the run overshot site 5
    # by 17 tiles and stalled. The tile is re-resolved for the player's new position.
    assert clicks[1]["serial"] > clicks[0]["serial"], "the re-click re-resolves the tile"


def _fletch_stretch(monkeypatch, logs_seq, bows_seq):
    """One far click at the stand with a fletch running; count_item answers logs then bows."""
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 500, "screenY": 300,
                                                     "tile": (x, y)})
    clicks = []
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: clicks.append(point["tile"]))
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    seq = iter([_stand(8), _stand(8), _stand(8), {"x": 1420, "y": 3340, "plane": 0},
                {"x": 1430, "y": 3342, "plane": 0}, {"x": 1440, "y": 3342, "plane": 0}]
               + [_stand(7)] * 20)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    logs, bows = iter(logs_seq), iter(bows_seq)
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: next(logs) if "logs" in name else next(bows))
    monkeypatch.setattr(vtr_routine, "find_spool", lambda: {"name": "Bow string spool"})
    strung = []
    monkeypatch.setattr(vtr_routine, "start_stringing", lambda c: strung.append(1) or True)
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    return clicks, strung


def test_the_build_reserve_switches_the_fletch_to_stringing_instead_of_reclicking(monkeypatch):
    """USER 2026-09-10: "instead of clicking to interupt the fletch we can use the spool to
    start stringing to interupt it instead. that way no new walk click will be needed and it
    can be done mid walk"."""
    clicks, strung = _fletch_stretch(monkeypatch, logs_seq=[6, 4, 3, 3, 3, 3], bows_seq=[2] * 12)
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.LOG_RESERVE_KEY] = 3
    assert vtr_routine.walk_leg(ctx, 8, 7, after_first_hop=lambda: "fletch") is True
    assert len(clicks) == 1, "the walk click only -- the spool is the interruption"
    assert strung == [1]


def test_eight_unstrung_bows_switch_the_fletch_to_stringing_mid_walk(monkeypatch):
    """USER 2026-09-10: "lower it to fire at 8 bows in inventory"."""
    clicks, strung = _fletch_stretch(monkeypatch, logs_seq=[20] * 12,
                                     bows_seq=[5, 7, vtr_routine.STRING_ABOVE_BOWS, 9, 9, 9])
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.LOG_RESERVE_KEY] = 3
    assert vtr_routine.walk_leg(ctx, 8, 7, after_first_hop=lambda: "fletch") is True
    assert len(clicks) == 1 and strung == [1]


def test_the_reserve_still_reclicks_the_walk_when_no_spool_is_carried(monkeypatch):
    clicks, strung = _fletch_stretch(monkeypatch, logs_seq=[6, 4, 3, 3, 3, 3], bows_seq=[2] * 12)
    monkeypatch.setattr(vtr_routine, "find_spool", lambda: None)
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.LOG_RESERVE_KEY] = 3
    assert vtr_routine.walk_leg(ctx, 8, 7, after_first_hop=lambda: "fletch") is True
    assert len(clicks) == 2 and strung == []


def test_the_fletch_hook_sets_the_reserve_it_started_against(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())    # 8 builds owed
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 40 if "logs" in name else 0)
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda c: True)
    ctx = _ctx_only()
    ctx.state.data["vt_fletch_due"] = True
    assert vtr_routine._fletch_on_the_move(ctx) == "fletch"
    assert ctx.state.data[vtr_routine.LOG_RESERVE_KEY] == 8


# --- 2026-09-09: a site click that resolved to the wrong action gets one menu retry --------

def _site_click_world(monkeypatch, last_option, menu_result=True):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1, "screenY": 2, "id": i})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    monkeypatch.setattr(controller, "get_last_action", lambda: {"option": last_option})
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, opt, **k: menu.append(opt) or menu_result)
    return menu


def test_a_claim_that_resolved_to_walk_here_is_retried_through_the_menu(monkeypatch):
    """MEASURED 2026-09-09: the bare click missed the pile at sites 5-7 about one time in six
    and /last-action read "Walk here". The menu tier picks Claim by name."""
    menu = _site_click_world(monkeypatch, "Walk here")
    # The pile empties only once the MENU click has gone out -- the bare click missed.
    monkeypatch.setattr(controller, "get_api", lambda g, r: _pile_payload(8, 0 if menu else 65))
    site = vt.parse_state(_pile_payload(8, 65)).sites[8]
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.claim(_ctx_only(), site) is True
    assert menu == ["Claim"]


def test_a_claim_that_registered_but_did_not_confirm_is_not_retried(monkeypatch):
    menu = _site_click_world(monkeypatch, "Claim")
    monkeypatch.setattr(controller, "get_api", lambda g, r: _pile_payload(8, 65))
    site = vt.parse_state(_pile_payload(8, 65)).sites[8]
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.claim(_ctx_only(), site) is False
    assert menu == [], "a registered Claim that changed nothing is not a miss to retry"


def _stepping_clock():
    t = {"v": 0.0}

    def now():
        t["v"] += 0.5
        return t["v"]
    return now


def _pile_payload(n, unclaimed):
    return {"sites": [{"siteNumber": n, "baseValue": 0, "wood": "NONE", "baseCarved": False,
                       "bottom": {"value": 0}, "middle": {"value": 0}, "top": {"value": 0},
                       "decorations": 0, "nearbySpirits": ["BUFFALO", "JAGUAR", "SNAKE"],
                       "durabilityRemaining": 0, "unclaimedOfferings": unclaimed}],
            "researchPoints": 0, "totalUnclaimedOfferings": unclaimed, "atValeTotems": True}


def test_the_basket_is_emptied_below_a_totems_worth_not_only_at_zero(monkeypatch):
    """USER 2026-09-09: "running with full log basket empty inventory? better to empty so we
    can craft some bows"."""
    stops = []
    ctx = _running_tick(monkeypatch, stops, at=6)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 2 if "logs" in name else 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: {"name": "Open log basket"})
    emptied = []
    monkeypatch.setattr(vtr_routine, "empty_basket", lambda c: emptied.append(1) or True)
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 28
    vtr_routine.tick(ctx)
    assert emptied == [1]


def test_a_basket_known_to_be_empty_is_not_clicked(monkeypatch):
    stops = []
    ctx = _running_tick(monkeypatch, stops, at=6)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: {"name": "Open log basket"})
    monkeypatch.setattr(vtr_routine, "empty_basket",
                        lambda c: pytest.fail("shift-clicked a basket known to be empty"))
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 0
    vtr_routine.tick(ctx)


def test_decorate_is_one_click_confirmed_on_every_bow_going_on(monkeypatch):
    """USER 2026-09-09: one Decorate click runs all four (recording: four animations after a
    single click). Clicking per decoration only landed on a loop already running."""
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1, "screenY": 2, "id": i})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: clicks.append(1) or True)
    monkeypatch.setattr(controller, "get_last_action", lambda: {"option": "Decorate"})
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 4 if "longbow" in name else 0)
    reads = iter([0, 1, 2, 3, 4, 4, 4, 4])
    site_payload = lambda d: {"sites": [{"siteNumber": 8, "baseValue": 4, "wood": "YEW",
                                          "baseCarved": True, "bottom": {"value": 10},
                                          "middle": {"value": 11}, "top": {"value": 13},
                                          "decorations": d, "nearbySpirits": ["BUFFALO", "JAGUAR", "SNAKE"],
                                          "durabilityRemaining": 5, "unclaimedOfferings": 0}],
                              "researchPoints": 0, "totalUnclaimedOfferings": 0, "atValeTotems": True}
    monkeypatch.setattr(controller, "get_api", lambda g, r: site_payload(next(reads)))
    site = vt.parse_state(site_payload(0)).sites[8]
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.decorate(_ctx_only(), site) is True
    assert clicks == [1], "one click for all four decorations"


def test_carve_answers_the_reopening_prompt_three_times_on_one_totem_click(monkeypatch):
    """USER 2026-09-10: "we dont have to click inbetween choosing each totem piece like 1 2 3
    the menu will pop up after we choose one until we chose all three"."""
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: clicks.append(1) or True)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(vtr_routine, "site_object", lambda n, w: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(vtr_routine, "_await_menu", lambda ctx: True)      # it keeps reopening

    class _Site:
        number = 7
        nearby_spirits = ["BUFFALO", "JAGUAR", "SNAKE"]
        segment_values = (0, 0, 0)
        base_carved = False

    carved = []

    def choose(ctx, spirit):
        carved.append(spirit)
        vals = list(_Site.segment_values)
        vals[len(carved) - 1] = vt.SPIRIT_VALUES[spirit] + vt.CARVED_OFFSET
        _Site.segment_values = tuple(vals)
        return True

    monkeypatch.setattr(vtr_routine, "choose_spirit", choose)
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: _Site)
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.carve(_ctx_only(), _Site) is True
    assert clicks == [1], "one Carve click for the whole trio"
    assert carved == ["BUFFALO", "JAGUAR", "SNAKE"]


def test_the_prehover_tracks_the_totem_until_the_player_stops(monkeypatch):
    """USER 2026-09-10: "when we get towards the totem the click while moving often misses"
    -- "the cursor hover while approaching". The hover flight ends while the player is still
    running the last tiles in, so the cursor rests where the totem WAS. Until /player says
    the run is over, the hover follows the live projection whenever it has drifted."""
    positions = iter([(500, 300), (520, 290), (540, 280), (541, 280), (541, 280)])
    monkeypatch.setattr(vtr_routine, "site_object", lambda n, w: {"screenX": 500, "screenY": 300})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: lambda: next(positions))
    hovers, moves = [], []
    monkeypatch.setattr(controller, "hover_entity", lambda ser, obj, **k: hovers.append(1) or True)
    cursor = {"xy": (493, 307)}                   # where the first hover parked (offset -7,+7)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: cursor["xy"])

    def move(ser, x, y, **k):
        moves.append((x, y))
        cursor["xy"] = (x, y)

    monkeypatch.setattr(controller, "move_to_human", move)
    moving = iter([True, True, True, False, False, False])
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1, "y": 1, "plane": 0, "moving": next(moving)})
    site = vt.parse_state(_full_state()).sites[6]
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: site)
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    _REAL_PREHOVER(_ctx_only(), 6)
    assert hovers == [1]
    assert len(moves) >= 2, "the cursor followed the sliding projection while the player ran"
    assert abs(moves[-1][0] - (540 - 7)) <= 3 and abs(moves[-1][1] - (280 + 7)) <= 3


def test_the_first_action_prehovered_is_the_totem_when_the_pile_is_below_the_cap(monkeypatch):
    hovered = []
    monkeypatch.setattr(vtr_routine, "site_object",
                        lambda n, w: hovered.append(w) or {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "hover_entity", lambda *a, **k: True)
    site = vt.parse_state(_full_state(unclaimedOfferings=65)).sites[6]
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: site)
    _REAL_PREHOVER(_ctx_only(), 6)
    assert hovered == ["totem"]


def test_decorate_with_no_bows_does_not_click(monkeypatch):
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: pytest.fail("clicked with no bows"))
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    site = vt.parse_state({"sites": [{"siteNumber": 8, "baseValue": 4, "wood": "YEW",
                                      "baseCarved": True, "bottom": {"value": 10},
                                      "middle": {"value": 11}, "top": {"value": 13},
                                      "decorations": 1, "nearbySpirits": [], "durabilityRemaining": 5,
                                      "unclaimedOfferings": 0}],
                           "researchPoints": 0, "totalUnclaimedOfferings": 0, "atValeTotems": True}).sites[8]
    assert vtr_routine.decorate(_ctx_only(), site) is False


def test_the_fletching_knife_is_found_and_preferred(monkeypatch):
    """USER 2026-09-09: "new knife name fletching knife" -- the Vale Research Exchange reward."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Fletching knife", "screenX": 2, "screenY": 2}])
    assert vtr_routine.find_knife()["name"] == "Fletching knife"
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1}])
    assert vtr_routine.find_knife()["name"] == "Knife"


def test_the_next_leg_starts_inside_the_same_tick(monkeypatch):
    """USER 2026-09-09: "compared to the human its very slow" -- ~3s of engine round trip
    between the last site action and the leg's first click, at every site."""
    stops = []
    ctx = _running_tick(monkeypatch, stops, at=6)
    walked = []
    monkeypatch.setattr(vtr_routine, "_walk_to_site", lambda c, n: walked.append(n))
    vtr_routine.tick(ctx)
    assert walked == [route.next_in_ring(6)]


def test_a_stalling_far_click_bans_only_its_target_not_the_leg(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers4, leg 1->8): a viewport click straight at site 8's
    stand tile 46 tiles off moved the player 5 tiles and stopped -- the game's pathfinder
    gives up on that line (the river) -- and the whole leg was marked "hops from now on",
    although its own markers would have far-clicked fine. Only the target that stalled is
    remembered; the next attempt far-clicks the farthest OTHER candidate."""
    points = route.leg_waypoints(6, 5)
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 500, "screenY": 300,
                                                     "tile": (x, y)})
    far = []
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: far.append(point["tile"]))
    hops = []
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    monkeypatch.setattr(controller, "get_player", lambda: _stand(6))     # never moves
    clock = {"t": 0.0}
    monkeypatch.setattr(vtr_routine, "_now", lambda: clock["t"])
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ctx = _ctx_only()
    vtr_routine.walk_leg(ctx, 6, 5)
    banned = ctx.state.data[vtr_routine.NO_DIRECT_KEY][str((6, 5))]
    assert far[0] == route.SITES[5]["stand"] and banned[0] == list(route.SITES[5]["stand"])
    assert far[1] != route.SITES[5]["stand"], "after the stand stalls, the next candidate is tried"
    assert len(far) + len(hops) == vtr_routine.DIRECT_LEG_MAX_STRETCHES


def test_a_marker_behind_the_player_is_never_the_far_click(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers4, leg 8->7 from the booth): the first stretch
    reached the middle marker (1435,3346); the stand tile was not rendered, the marker just
    reached was (rightly) skipped, and the pick fell to (1415,3339) -- 20 tiles BACK west,
    toward the bank it had just left. Candidates are cut at the player's position along
    the leg: the stand tile plus the markers still ahead."""
    _fake_clock(monkeypatch)
    points = route.leg_waypoints(8, 7)
    _standing_at(monkeypatch, 1436, 3347)                     # beside the middle marker
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": (x, y) != route.SITES[7]["stand"],
                                                     "screenX": 500, "screenY": 300, "tile": (x, y)})
    clicks = []

    def click(ctx, point, shift):
        clicks.append(point["tile"])
        _standing_at(monkeypatch, *route.SITES[7]["stand"])

    monkeypatch.setattr(travel, "_click_walk_point", click)
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7) is True
    assert points[0] not in clicks
    assert all(x >= 1436 for x, y in clicks), "every click is ahead of the player"


def test_an_empty_that_fills_every_free_slot_leaves_the_basket_unknown_not_empty(monkeypatch):
    """USER 2026-09-09: "just ignored a totem for some reason, had 17 logs in logbasket none
    in inventory" -- a slots-worth moved out of an unknown basket was recorded as 'now empty'."""
    inv = [{"name": "Open log basket", "screenX": 1, "screenY": 1}] + [{"name": "x"}] * 20  # 7 free
    state = {"clicked": False}
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: inv + [{"name": "Maple logs"}] * 7 if state["clicked"] else inv)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    _shift_keys(monkeypatch, [])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: state.__setitem__("clicked", True) or True)
    ctx = _ctx_only()
    vtr_routine.empty_basket(ctx)                 # 7 logs arrived: exactly the free slots
    assert ctx.state.data.get(vtr_routine.BASKET_LOGS_KEY) is None, "unknown, NOT zero"


def test_an_empty_that_moves_fewer_than_the_free_slots_means_the_basket_is_empty(monkeypatch):
    inv = [{"name": "Open log basket", "screenX": 1, "screenY": 1}] + [{"name": "x"}] * 20
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    _shift_keys(monkeypatch, [])
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    counts = iter([0, 3, 3, 3, 3])
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: next(counts))
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 28
    vtr_routine.empty_basket(ctx)
    assert ctx.state.data[vtr_routine.BASKET_LOGS_KEY] == 0


def test_above_eight_bows_the_leg_strings_instead_of_fletching(monkeypatch):
    """USER 2026-09-09: "if we have above say 8 bows (two totems) instead of fletching more
    during a long run use the spool to string the bows"."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: {"Yew logs": 20, "Yew longbow (u)": 9, "Yew longbow": 0}.get(name, 0))
    monkeypatch.setattr(vtr_routine, "find_spool", lambda: {"name": "Bow string spool"})
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda c: pytest.fail("fletched with 9 bows in hand"))
    strung = []
    monkeypatch.setattr(vtr_routine, "start_stringing", lambda c: strung.append(1) or True)
    ctx = _ctx_only()
    ctx.state.data["vt_fletch_due"] = True
    assert vtr_routine._fletch_on_the_move(ctx) == "string"
    assert strung == [1]


def test_below_eight_bows_the_leg_fletches_as_before(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: {"Yew logs": 20, "Yew longbow (u)": 3}.get(name, 0))
    monkeypatch.setattr(vtr_routine, "find_spool", lambda: {"name": "Bow string spool"})
    monkeypatch.setattr(vtr_routine, "start_stringing", lambda c: pytest.fail("strung with 3 bows"))
    monkeypatch.setattr(vtr_routine, "start_fletching", lambda c: True)
    ctx = _ctx_only()
    ctx.state.data["vt_fletch_due"] = True
    assert vtr_routine._fletch_on_the_move(ctx) == "fletch"


def test_strung_bows_count_as_decorations(monkeypatch):
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: {"Yew longbow (u)": 1, "Yew longbow": 3}.get(name, 0))
    assert vtr_routine.bow_count(_ctx_only()) == 4


def _spool_world(monkeypatch, *, prompt_title="", strings=False):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Bow string spool", "screenX": 1, "screenY": 1},
                                 {"name": "Yew longbow (u)", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    monkeypatch.setattr(controller, "get_last_action", lambda: {"option": "Use", "target": "x"})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    monkeypatch.setattr(skillmulti, "is_open", lambda: bool(prompt_title))
    monkeypatch.setattr(skillmulti, "prompt_title", lambda: prompt_title)
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == prompt_title)
    counts = iter([1] + ([0] * 20 if strings else [1] * 20))
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: next(counts))
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    return keys


def test_a_spool_that_strings_nothing_is_marked_dead_only_on_the_second_strike(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers4): the FIRST use strung nothing within the budget
    and the spool was written off for the whole session, so every later leg fletched with 19
    unstrung bows in hand (USER: "we have like 16 bows ... and we decide to fletch more")."""
    _spool_world(monkeypatch)
    ctx = _ctx_only()
    assert vtr_routine.start_stringing(ctx) is False
    assert ctx.state.data.get(vtr_routine.SPOOL_DEAD_KEY) is None, "one strike is not dead"
    assert vtr_routine.start_stringing(ctx) is False
    assert ctx.state.data.get(vtr_routine.SPOOL_DEAD_KEY) is True


def test_space_follows_the_bow_click_even_when_no_prompt_was_seen(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers5 vs 6): the string only ever landed when Space
    was pressed after the bow click; twice the prompt was not detected inside the wait and
    nothing was pressed, and the spool struck out. The human presses Space ~0.8s after the
    bow click regardless; Space with nothing open does nothing, so it is pressed blind. Only a
    MAKE-titled menu (several products) needs the derived number instead."""
    keys = _spool_world(monkeypatch, prompt_title="", strings=True)
    ctx = _ctx_only()
    assert vtr_routine.start_stringing(ctx) is True
    assert keys == ["SPACE"]


def test_work_site_retries_a_failed_action_once_before_walking_on(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers4, site 4): one build click that did not land ended
    the visit and the totem was skipped for the whole lap (USER: "skipped a totem")."""
    site = vt.parse_state(_full_state()).sites[4]
    done = vt.parse_state(_full_state(baseValue=4, wood="YEW", baseCarved=True,
                                      bottom={"value": 10}, middle={"value": 11},
                                      top={"value": 13}, decorations=4)).sites[4]
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: site)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    attempts = []

    def build(ctx, s):
        attempts.append(1)
        if len(attempts) == 2:
            monkeypatch.setattr(vtr_routine, "_site_now", lambda n: done)
        return len(attempts) == 2

    monkeypatch.setattr(vtr_routine, "build", build)
    assert vtr_routine.work_site(_ctx_only(), 4) is True
    assert attempts == [1, 1]


def _memory(**payload):
    """A REAL bank_memory.BankMemory -- a stub answering quantity() independently of seen/items
    encoded a shape the class cannot produce and hid the substring-match bug below."""
    return bank_memory.BankMemory(payload)


def test_the_bank_out_of_the_current_wood_steps_down_to_the_next(monkeypatch):
    """USER 2026-09-09: "When we run out of yew logs swap to maple logs"."""
    _at_the_bank(monkeypatch)
    monkeypatch.setattr(bank_memory, "load", lambda names=None: _memory(
        seen=True, items=[{"name": "Maple logs", "quantity": 5374, "id": 1517}]))
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    withdrawn = []
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, fresh=False: withdrawn.append(name) or True)
    monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    ctx = _ctx_only()
    assert vtr_routine.bank_trip(ctx) is True
    assert withdrawn and withdrawn[0] == "Maple logs"
    assert ctx.state.config["wood"] == "MAPLE"
    assert ctx.state.config["bow"] == "Maple longbow (u)"
    assert ctx.state.data[vtr_routine.WOOD_KEY] == "MAPLE"


def test_a_name_that_merely_contains_the_log_name_does_not_count_as_stock(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers1): the bank held NO Yew logs but did hold one
    "Abyssal lantern (yew logs)", bank_memory.quantity's substring match counted it as 1, the
    fallback never fired, the withdraw found nothing and the run safety-stopped with 13 Maple
    logs in hand. Stock is the EXACT item name."""
    _at_the_bank(monkeypatch)
    memory = bank_memory.BankMemory({"seen": True, "items": [
        {"name": "Abyssal lantern (yew logs)", "quantity": 1, "id": 26842},
        {"name": "Yew pyre logs", "quantity": 23, "id": 3446},
        {"name": "Maple logs", "quantity": 5374, "id": 1517}]})
    monkeypatch.setattr(bank_memory, "load", lambda names=None: memory)
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    withdrawn = []
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, fresh=False: withdrawn.append(name) or True)
    monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    ctx = _ctx_only()
    assert vtr_routine.bank_trip(ctx) is True
    assert withdrawn and withdrawn[0] == "Maple logs"
    assert ctx.state.config["wood"] == "MAPLE"


def test_the_logs_in_hand_decide_the_wood_after_a_restart(monkeypatch):
    """MEASURED live 2026-09-10 (vt_markers3): run 2 had switched to maple at the bank, the
    restart wiped that (it lived in ctx.state.data) and the panel said YEW again, so with 16
    Maple logs and 8 maple bows in hand every site read "Yew logs: 0", claimed and walked on
    -- three sites, nothing built (USER: "its not built one in 3 hops")."""
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: {"Maple logs": 16, "Maple longbow (u)": 8}.get(name, 0))
    ctx = _ctx_only()                                   # config: the panel's YEW
    vtr_routine._wood_override(ctx)
    assert ctx.state.config["wood"] == "MAPLE"
    assert ctx.state.config["log"] == "Maple logs"
    assert ctx.state.data[vtr_routine.WOOD_KEY] == "MAPLE"


def test_the_configured_wood_is_kept_while_its_logs_are_in_hand(monkeypatch):
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: {"Yew logs": 3, "Maple logs": 16}.get(name, 0))
    ctx = _ctx_only()
    vtr_routine._wood_override(ctx)
    assert ctx.state.config["wood"] == "YEW"


def test_work_site_logs_one_outcome_line_per_site(monkeypatch):
    """USER 2026-09-10: "Add a log for finishing totems too" -- the site work was invisible
    in the log except as clicks, so three sites skipped for want of logs looked like three
    sites finished."""
    lines = []
    monkeypatch.setattr(vtr_routine.debug_log, "log", lambda cat, msg: lines.append(msg))
    site = vt.parse_state(_full_state()).sites[4]            # unbuilt, nothing to claim
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: site)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    assert vtr_routine.work_site(_ctx_only(), 4) is True
    outcome = [l for l in lines if l.startswith("site 4:")]
    assert len(outcome) == 1
    assert "no Yew logs" in outcome[0] and "unbuilt" in outcome[0], outcome


def test_the_wood_switch_survives_the_engine_re_resolving_the_config():
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.WOOD_KEY] = "MAPLE"
    ctx.state.config = vtr_routine.get_config(None)          # the panel says YEW again
    vtr_routine._wood_override(ctx)
    assert ctx.state.config["log"] == "Maple logs"


def test_decorating_counts_only_bows_of_the_totems_own_wood(monkeypatch):
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: {"Yew longbow (u)": 6, "Maple longbow (u)": 2}.get(name, 0))
    ctx = _ctx_only()
    assert vtr_routine.bow_count(ctx, "MAPLE") == 2
    assert vtr_routine.bow_count(ctx, "YEW") == 6
    assert vtr_routine.bow_count(ctx) == 6            # the wood being worked is yew


def test_a_failed_bank_trip_with_logs_in_hand_is_not_a_stop(monkeypatch):
    stops = []
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    _park_at(monkeypatch, 8)
    monkeypatch.setattr(vtr_routine, "work_site", lambda ctx, n: True)
    monkeypatch.setattr(vtr_routine, "_walk_to_site", lambda ctx, n: None)
    monkeypatch.setattr(vtr_routine, "_prehover_first_action", lambda ctx, n: None)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 4 if "logs" in name else 0)
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: None)
    monkeypatch.setattr(vtr_routine, "bank_trip", lambda ctx: False)
    vtr_routine.tick(_ctx(monkeypatch, stops))
    assert stops == [], "four logs in hand: carry on, try the bank next pass"


def test_a_minimap_far_click_stays_within_the_measured_reach(monkeypatch):
    """USER 2026-09-09: "long leg hop clicked outside of minimap window resulting in small
    hops" -- onMinimap said yes for a 38-tile point that projected outside the circle."""
    points = route.leg_waypoints(8, 7)          # up to 54 tiles from site 8's stand tile
    asked = []
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: asked.append((x, y)) or {"onMinimap": True, "screenX": 2273, "screenY": 248})
    clicks = []
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: clicks.append(point))
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: True)
    seq = iter([_stand(8), _stand(8)] + [_stand(7)] * 30)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    vtr_routine.walk_leg(_ctx_only(), 8, 7)
    s8 = route.SITES[8]["stand"]
    assert all(max(abs(x - s8[0]), abs(y - s8[1])) <= travel.WALK_HOP_MAX_TILES for x, y in asked),         "a point beyond the minimap's reach must not even be asked for"
    assert clicks


def test_the_wood_never_switches_on_an_empty_or_unseen_memory(monkeypatch):
    """Host 2026-09-09: a filtered read answered "0 Yew logs" with 129 in the bank."""
    # Never seen (no items at all), and seen-but-empty: neither is evidence the wood ran out.
    for mem in (_memory(seen=False), _memory(seen=True, items=[])):
        _at_the_bank(monkeypatch)
        monkeypatch.setattr(bank_memory, "load", lambda names=None, m=mem: m)
        monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: {"screenX": 1, "screenY": 2})
        monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
        withdrawn = []
        monkeypatch.setattr(banker, "withdraw_all_shift",
                            lambda ctx, name, fresh=False: withdrawn.append(name) or True)
        monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: True)
        monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
        ctx = _ctx_only()
        vtr_routine.bank_trip(ctx)
        assert withdrawn and withdrawn[0] == "Yew logs", "stayed on yew"
        assert ctx.state.data.get(vtr_routine.WOOD_KEY) is None


def test_junk_in_the_inventory_is_deposited_on_the_bank_trip(monkeypatch):
    """Host 2026-09-09: a mis-aimed withdraw pulled 13 Molten glass; nothing ever banked it."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Molten glass"}, {"name": "Yew logs"},
                                 {"name": "Open log basket"}, {"name": "Bow string spool"}])
    calls = []
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: calls.append(cfg))
    _REAL_DEPOSIT_JUNK(_ctx_only())
    assert calls and "logs" in calls[0]["keepItems"] and calls[0]["skipContainerDeposit"]


def test_nothing_is_deposited_when_everything_is_kept(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Yew logs"}, {"name": "Yew longbow (u)"},
                                 {"name": "Fletching knife"}, {"name": "Vale offerings"}])
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: pytest.fail("deposited kept items"))
    _REAL_DEPOSIT_JUNK(_ctx_only())


def test_the_bank_slot_is_trusted_only_once_two_reads_agree(monkeypatch):
    reads = iter([{"items": [{"name": "Yew logs", "screenX": 650, "screenY": 574}]},
                  {"items": [{"name": "Yew logs", "screenX": 650, "screenY": 226}]},
                  {"items": [{"name": "Yew logs", "screenX": 650, "screenY": 226}]}])
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: next(reads))
    monkeypatch.setattr(vtr_routine, "_now", _stepping_clock())
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine._await_bank_slot("Yew logs") is True


# --- 2026-09-10: face the camera toward the leg before looking for a viewport click ----------
#
# USER's recording (2026-09-10_075943): "i can click much further by nudging the camera in the
# direction of the totem and click the totem directly instead of minimap hop". Six middle-mouse
# drags, each right before a far viewport click at the top of the screen.

def _leg_8_to_7_positions(monkeypatch):
    seq = iter([_stand(8), _stand(8)] + [_stand(7)] * 20)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))


def test_the_far_click_faces_the_camera_at_the_stand_tile_when_it_is_off_screen(monkeypatch):
    faced = []
    monkeypatch.setattr(controller, "face_toward",
                        lambda ser, tx, ty, px=None, py=None: faced.append((tx, ty)) or True)
    # Rendered only once the camera has been turned toward it.
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": bool(faced),
                                                     "screenX": 700, "screenY": 120})
    clicks = []
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda ctx, point, shift: clicks.append(shift))
    _leg_8_to_7_positions(monkeypatch)
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7) is True
    assert faced == [route.SITES[7]["stand"]], "faced the next site's stand tile, once"
    assert clicks == [True], "then ONE shift viewport click"


def test_the_far_click_prefers_the_viewport_over_the_minimap_for_the_same_point(monkeypatch):
    """The human clicks the world, not the minimap, once the camera faces the leg."""
    monkeypatch.setattr(controller, "face_toward", lambda ser, tx, ty, px=None, py=None: True)
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 700, "screenY": 120})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p, inst=False: {"onMinimap": True, "screenX": 1900, "screenY": 200})
    clicks = []
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda ctx, point, shift: clicks.append(shift))
    _leg_8_to_7_positions(monkeypatch)
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7) is True
    assert clicks == [True], "viewport (SHIFT) wins when both can reach the point"


def test_the_far_click_does_not_turn_the_camera_when_the_stand_tile_is_already_rendered(monkeypatch):
    monkeypatch.setattr(controller, "face_toward",
                        lambda *a, **k: pytest.fail("turned the camera for a tile already on screen"))
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 700, "screenY": 120})
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: None)
    _leg_8_to_7_positions(monkeypatch)
    assert vtr_routine.walk_leg(_ctx_only(), 8, 7) is True


def _carve_fixture(monkeypatch, land_after_presses):
    """A carve whose key LANDS only once `land_after_presses` numbers have been pressed --
    the dropped-key race MEASURED 2026-09-10 (recording vt-markers7-approach, site 4)."""
    site = _one_site(baseValue=4, wood="YEW", nearbySpirits=["EAGLE", "SCORPION", "SNAKE"])
    carved = _one_site(baseValue=4, wood="YEW", baseCarved=True,
                       bottom={"value": 12, "spirit": "EAGLE"},
                       middle={"value": 14, "spirit": "SCORPION"},
                       top={"value": 13, "spirit": "SNAKE"},
                       nearbySpirits=["EAGLE", "SCORPION", "SNAKE"])
    pressed = []
    monkeypatch.setattr(vtr_routine, "_site_now",
                        lambda n: carved if len(pressed) >= land_after_presses else site)
    monkeypatch.setattr(vtr_routine, "site_object", lambda n, w: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(vtr_routine, "choose_spirit", lambda ctx, s: pressed.append(s) or True)
    monkeypatch.setattr(vtr_routine, "_await_menu", lambda ctx: True)
    monkeypatch.setattr(outcome_waits, "await_pre",
                        lambda ctx, signals, budget, confirm, **k: bool(confirm()))
    monkeypatch.setattr(controller, "get_player", lambda: {"animation": -1})
    return site, pressed


def test_the_first_carve_key_waits_for_the_build_animation_to_end(monkeypatch):
    """MEASURED runs 9-13: every dropped first key went out while the player was still
    animating; the landed ones came after. The key waits for animation -1, up to a bound."""
    site, pressed = _carve_fixture(monkeypatch, land_after_presses=1)
    anim = iter([827, 827, -1] + [-1] * 20)
    reads = []
    monkeypatch.setattr(controller, "get_player", lambda: reads.append(1) or {"animation": next(anim)})
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    assert vtr_routine.carve(_ctx_only(), site) is True
    assert len(reads) >= 3, "polled until the animation ended"
    assert pressed == ["EAGLE"]


import outcome_waits


def test_a_carve_key_that_did_not_carve_is_pressed_again_once(monkeypatch):
    """MEASURED 2026-09-10 (vt-markers7-approach, site 4): the Carve click registered, the
    number was pressed 0.4s later while the build animation was still playing, and the game
    dropped the selection -- busy varbit 12393 went to 1 with no carve behind it, for six
    seconds, until a whole new Carve click. The human's own recording shows the identical
    signature twice (keys at +0.4s dropped) and recovers each time by pressing the same
    number again ~1s later, with no new click. So the routine does the same: one re-press."""
    site, pressed = _carve_fixture(monkeypatch, land_after_presses=2)
    assert vtr_routine.carve(_ctx_only(), site) is True
    assert pressed == ["EAGLE", "EAGLE"]


def test_a_carve_key_that_landed_is_not_pressed_twice(monkeypatch):
    site, pressed = _carve_fixture(monkeypatch, land_after_presses=1)
    assert vtr_routine.carve(_ctx_only(), site) is True
    assert pressed == ["EAGLE"]


def test_the_basket_is_emptied_on_the_leg_once_the_fletch_stops_at_the_reserve(monkeypatch):
    """The top-up used to wait for the site (~2s in front of the totem, MEASURED run
    vt_markers8: basket click, delta wait, then the pre-hover flight it delayed). The fletch
    has already stopped at the reserve by then and the hands are idle for the rest of the
    stretch, so the Empty goes out there, on the move -- and a later stretch's hook can
    fletch the logs it brought."""
    resolved = {"n": 0}

    def ground(x, y, p, inst=False):
        resolved["n"] += 1
        return {"onScreen": True, "screenX": 500 + resolved["n"], "screenY": 300,
                "tile": (x, y), "serial": resolved["n"]}

    monkeypatch.setattr(controller, "get_ground_walk_point", ground)
    events = []
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda ctx, point, shift: events.append("walk"))
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    seq = iter([_stand(8), _stand(8), _stand(8), {"x": 1420, "y": 3340, "plane": 0},
                {"x": 1430, "y": 3342, "plane": 0}, {"x": 1440, "y": 3342, "plane": 0}]
               + [_stand(7)] * 20)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    logs = iter([6, 4, 3] + [3] * 30)              # loose logs falling as bows are made
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: next(logs))
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: {"name": "Open log basket"})
    monkeypatch.setattr(vtr_routine, "empty_basket", lambda ctx: events.append("empty") or True)
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.LOG_RESERVE_KEY] = 3
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 10
    assert vtr_routine.walk_leg(ctx, 8, 7, after_first_hop=lambda: "fletch") is True
    assert events == ["walk", "walk", "empty"], "the re-click ends the fletch, THEN the basket"


def test_a_known_empty_basket_is_not_emptied_on_the_leg(monkeypatch):
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 500,
                                                     "screenY": 300, "tile": (x, y)})
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: None)
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    seq = iter([_stand(8), _stand(8), _stand(8), {"x": 1420, "y": 3340, "plane": 0}]
               + [_stand(7)] * 20)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    logs = iter([6, 3] + [3] * 30)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: next(logs))
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: {"name": "Open log basket"})
    monkeypatch.setattr(vtr_routine, "empty_basket",
                        lambda ctx: pytest.fail("a basket known to be empty was emptied"))
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.LOG_RESERVE_KEY] = 3
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 0
    assert vtr_routine.walk_leg(ctx, 8, 7, after_first_hop=lambda: "fletch") is True


def _spool_leg(monkeypatch, basket_estimate):
    """A fletch stretch that reaches the reserve with unstrung bows in hand and a basket."""
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p, inst=False: {"onScreen": True, "screenX": 500,
                                                     "screenY": 300, "tile": (x, y)})
    events = []
    monkeypatch.setattr(travel, "_click_walk_point",
                        lambda ctx, point, shift: events.append("walk"))
    monkeypatch.setattr(travel, "_hop_to", lambda *a, **k: pytest.fail("hopped"))
    seq = iter([_stand(8), _stand(8), _stand(8), {"x": 1420, "y": 3340, "plane": 0},
                {"x": 1430, "y": 3342, "plane": 0}] + [_stand(7)] * 20)
    monkeypatch.setattr(controller, "get_player", lambda: next(seq))
    logs, bows = iter([6, 3] + [3] * 30), iter([4] * 30)
    monkeypatch.setattr(vtr_routine, "count_item",
                        lambda name: next(logs) if "logs" in name else next(bows))
    monkeypatch.setattr(vtr_routine, "find_spool", lambda: {"name": "Bow string spool"})
    monkeypatch.setattr(vtr_routine, "find_basket", lambda: {"name": "Open log basket"})
    monkeypatch.setattr(vtr_routine, "empty_basket", lambda ctx: events.append("empty") or True)
    monkeypatch.setattr(vtr_routine, "start_stringing", lambda c: events.append("string") or True)
    monkeypatch.setattr(vtr_routine, "_sleep", lambda s: None)
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.LOG_RESERVE_KEY] = 3
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = basket_estimate
    assert vtr_routine.walk_leg(ctx, 8, 7, after_first_hop=lambda: "fletch") is True
    return events


def test_a_basket_known_to_hold_logs_is_emptied_before_the_spool_not_after(monkeypatch):
    """MEASURED 2026-09-10 (vt_markers9, leg 4->3): an Empty ends a running action -- the
    next string slot after the at-site Empty never fired. So the Empty goes out BEFORE the
    spool: it is the interruption, and the stringing then runs untouched."""
    assert _spool_leg(monkeypatch, basket_estimate=10) == ["walk", "empty", "string"]


def test_an_unknown_basket_is_not_emptied_ahead_of_the_spool(monkeypatch):
    """Unknown means the fletch might not be ended by the Empty AND no log might arrive to
    cover the one the spool's 1.5s could cost the reserve; the spool alone is the safe end."""
    assert _spool_leg(monkeypatch, basket_estimate=None) == ["walk", "string"]


def test_the_site_click_does_not_wait_for_the_stop_within_the_early_click_distance(monkeypatch):
    """USER 2026-09-10: "the hover actually feels a bit pointless unless we actually click
    with it". Within EARLY_CLICK_TILES the settle wait is skipped and the first action fires
    from the tracked hover while the player is still running in."""
    stops = []
    ctx = _ctx(monkeypatch, stops)
    site = _one_site(baseValue=4, wood="YEW", baseCarved=True, decorations=4,
                     bottom={"value": 10}, middle={"value": 11}, top={"value": 13})
    state = type("S", (), {"sites": {6: site}, "at_vale_totems": True})()
    monkeypatch.setattr(vtr_routine, "read_state", lambda: state)
    monkeypatch.setattr(vtr_routine, "nearest_site", lambda: 6)
    sx, sy = route.SITES[6]["stand"]
    _standing_at(monkeypatch, sx + 1, sy)                     # one tile out, still running
    monkeypatch.setattr(vtr_routine, "work_site", lambda c, n, during_decorate=None: True)
    monkeypatch.setattr(vtr_routine, "_basket_top_up_due", lambda c: False)
    monkeypatch.setattr(vtr_routine, "bow_count", lambda c, wood=None: 0)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **k: pytest.fail("waited for the stop inside the early-click distance"))
    vtr_routine.tick(ctx)
    assert not stops


def test_the_site_click_still_waits_for_the_stop_further_out(monkeypatch):
    stops = []
    ctx = _ctx(monkeypatch, stops)
    site = _one_site(baseValue=4, wood="YEW", baseCarved=True, decorations=4,
                     bottom={"value": 10}, middle={"value": 11}, top={"value": 13})
    state = type("S", (), {"sites": {6: site}, "at_vale_totems": True})()
    monkeypatch.setattr(vtr_routine, "read_state", lambda: state)
    monkeypatch.setattr(vtr_routine, "nearest_site", lambda: 6)
    sx, sy = route.SITES[6]["stand"]
    _standing_at(monkeypatch, sx + vtr_routine.EARLY_CLICK_TILES + 1, sy)
    monkeypatch.setattr(vtr_routine, "work_site", lambda c, n, during_decorate=None: True)
    monkeypatch.setattr(vtr_routine, "_basket_top_up_due", lambda c: False)
    monkeypatch.setattr(vtr_routine, "bow_count", lambda c, wood=None: 0)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)
    waited = []
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **k: waited.append(k) or True)
    vtr_routine.tick(ctx)
    assert waited


def test_a_viewport_point_on_the_canvas_edge_is_not_a_far_click(monkeypatch):
    """MEASURED 2026-09-10 (vt_markers12): a point projected on the canvas's top row passed
    isOnScreen and the click's jitter landed on the window frame -- the player never moved."""
    monkeypatch.setattr(controller, "get_state",
                        lambda: {"canvasScreenX": 0, "canvasScreenY": 23,
                                 "canvasWidth": 2529, "canvasHeight": 1377})
    assert vtr_routine._viewport_click_ok({"onScreen": True, "screenX": 1245, "screenY": 23}) is False
    assert vtr_routine._viewport_click_ok({"onScreen": True, "screenX": 1245, "screenY": 300}) is True
    assert vtr_routine._viewport_click_ok({"onScreen": True, "screenX": 2520, "screenY": 300}) is False


def test_the_far_click_skips_an_edge_projection_for_the_next_candidate(monkeypatch):
    monkeypatch.setattr(controller, "get_state",
                        lambda: {"canvasScreenX": 0, "canvasScreenY": 23,
                                 "canvasWidth": 2529, "canvasHeight": 1377})
    sx, sy = route.SITES[5]["stand"]

    def ground(x, y, p, inst=False):
        edge = (x, y) == (sx, sy)
        return {"onScreen": True, "screenX": 1245, "screenY": 23 if edge else 400, "tile": (x, y)}

    monkeypatch.setattr(controller, "get_ground_walk_point", ground)
    target = vtr_routine._far_click_target(5, route.leg_waypoints(6, 5), *route.SITES[6]["stand"])
    assert target is not None and (target[0], target[1]) != (sx, sy)


def test_empty_basket_measures_the_move_in_free_slots_so_a_running_fletch_cannot_zero_the_estimate(monkeypatch):
    """MEASURED runs 11-13: with a fletch running, a log turned into a bow between the read
    and the Empty, the loose-log delta came out one short of the free slots, and the basket
    was recorded EMPTY with ~17 logs in it -- the lap then ran dry a site early."""
    state = {"clicked": False}
    tools = [{"name": "Open log basket", "screenX": 1, "screenY": 1}, {"name": "Fletching knife"}]
    # before: 3 logs, 1 bow, 22 free. after: one log became a bow AND 22 moved in: 24 logs,
    # 2 bows, 0 free -- the loose delta reads 21, the free-slot delta the true 22.
    before = tools + [{"name": "Yew logs"}] * 3 + [{"name": "Yew longbow (u)"}]
    after = tools + [{"name": "Yew logs"}] * 24 + [{"name": "Yew longbow (u)"}] * 2
    monkeypatch.setattr(controller, "get_inventory", lambda: after if state["clicked"] else before)
    monkeypatch.setattr(vtr_routine, "open_basket", lambda ctx: True)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda *a, **k: state.__setitem__("clicked", True) or True)
    _shift_keys(monkeypatch, [])
    monkeypatch.setattr(outcome_waits, "await_pre",
                        lambda ctx, signals, budget, confirm, **k: bool(confirm()))
    ctx = _ctx_only()
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 28
    assert vtr_routine.empty_basket(ctx) is True
    assert ctx.state.data[vtr_routine.BASKET_LOGS_KEY] == 28 - 22, "22 slots filled: 6 left, not 0"
