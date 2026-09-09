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


def test_walk_leg_hops_every_recorded_waypoint_in_order(monkeypatch):
    _fake_clock(monkeypatch)
    hops = []
    monkeypatch.setattr(travel, "_hop_to",
                        lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    # Standing on site 6's stand tile, i.e. the start of the leg: every waypoint is walked.
    _standing_at(monkeypatch, *route.SITES[6]["stand"])
    # The arrival poll reads the player's position too, so it has to end up at site 5 for the
    # leg to be an arrival -- move the player there once the last hop has been issued.
    end = route.SITES[5]["stand"]

    def hop(ctx, x, y, plane, **k):
        hops.append((x, y))
        if len(hops) == len(route.leg_waypoints(6, 5)):
            _standing_at(monkeypatch, *end)
        return True

    monkeypatch.setattr(travel, "_hop_to", hop)
    assert vtr_routine.walk_leg(_ctx_only(), 6, 5) is True
    assert hops == list(route.leg_waypoints(6, 5))


def test_walk_leg_stops_at_the_first_hop_that_fails(monkeypatch):
    _fake_clock(monkeypatch)
    hops = []

    def hop(ctx, x, y, plane, **k):
        hops.append((x, y))
        return len(hops) < 3

    monkeypatch.setattr(travel, "_hop_to", hop)
    _standing_at(monkeypatch, *route.SITES[6]["stand"])
    assert vtr_routine.walk_leg(_ctx_only(), 6, 5) is False
    assert len(hops) == 3


def test_walk_leg_resumes_from_the_waypoint_nearest_the_player(monkeypatch):
    """C3: a leg is resumed mid-way far more often than started clean -- after a bank trip
    (which leaves the player standing ON leg 8->7), after a restart, after a retried hop.
    Replaying from waypoint 0 is a minimap click BACK toward the site we just left."""
    _fake_clock(monkeypatch)
    points = route.leg_waypoints(8, 7)
    hops = []
    monkeypatch.setattr(travel, "_hop_to",
                        lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    # The bank's stand tile: on the 8->7 leg, beside waypoint index 2.
    _standing_at(monkeypatch, *route.BANK["stand"])
    vtr_routine.walk_leg(_ctx_only(), 8, 7)
    assert hops == list(points[2:]), "the resumed leg must not walk backwards"
    assert points[0] not in hops


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
    positions = iter([{"x": 1397, "y": 3329, "plane": 0}])   # site 8's stand tile

    def player():
        try:
            return next(positions)
        except StopIteration:
            return {"x": route.BANK["stand"][0], "y": route.BANK["stand"][1], "plane": 0}

    monkeypatch.setattr(controller, "get_player", player)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: True)
    monkeypatch.setattr(vtr_routine, "fill_basket", lambda ctx: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    assert vtr_routine.bank_trip(_ctx_only()) is True
    assert hops == [route.BANK["stand"]]


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
    """Leg 8->7's recorded waypoints straddle the booth's stand tile and no other leg comes
    near it, which is the whole evidence BANK_SITES rests on -- so it is pinned here rather
    than only asserted in a comment."""
    import math
    bank = route.BANK["stand"]
    near = {leg for leg, pts in route.LEGS.items()
            if any(math.dist(p, bank) <= 8 for p in pts)}
    assert near == {(8, 7)}
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
    ctx = _basket_ctx(monkeypatch, [0, 21, 21])
    ctx.state.data[vtr_routine.BASKET_LOGS_KEY] = 28
    vtr_routine.empty_basket(ctx)
    assert vtr_routine.basket_logs(ctx.state.data) == 7
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
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    assert chosen and chosen[0] == ("open",), "did not open the shut basket first"
    assert any("empty" in o[0] for o in chosen[1:]), "never tried to empty it"


def test_empty_basket_does_not_reopen_an_already_open_basket(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    chosen = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, opt, **k: chosen.append(tuple(opt)) or True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    assert chosen and "open" not in chosen[0], "re-opened a basket that was already open"


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


def test_walk_leg_fires_the_hook_after_the_first_hop_not_before(monkeypatch):
    order = []
    monkeypatch.setattr(travel, "_hop_to",
                        lambda ctx, x, y, plane, **k: order.append(("hop", x, y)) or True)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": route.SITES[7]["stand"][0],
                                 "y": route.SITES[7]["stand"][1], "plane": 0})
    vtr_routine.walk_leg(_ctx_only(), 8, 7,
                         after_first_hop=lambda: order.append(("fletch",)))
    assert order[0][0] == "hop", "the move command must go out first"
    assert order[1] == ("fletch",), "the fletch must follow the FIRST hop, not the last"


def test_the_fletch_hook_rechecks_the_budget_against_live_state(monkeypatch):
    """The flag was set at the previous site; a log owed to a build must never become a bow."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: _full_state())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 0)   # no logs at all
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda c: pytest.fail("fletched with nothing to spare"))
    ctx = _ctx_only()
    ctx.state.data["vt_fletch_due"] = True
    assert vtr_routine._fletch_on_the_move(ctx) is False


def test_the_fletch_hook_is_a_no_op_when_nothing_is_due(monkeypatch):
    monkeypatch.setattr(vtr_routine, "start_fletching",
                        lambda c: pytest.fail("fletched with no flag set"))
    assert vtr_routine._fletch_on_the_move(_ctx_only()) is False
