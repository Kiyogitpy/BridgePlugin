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
