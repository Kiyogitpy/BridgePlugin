import pytest

import controller
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
