import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import granite_3t as g


def _item(name, x=1700, y=800, slot=0):
    return {"name": name, "qty": 1, "slot": slot, "screenX": x, "screenY": y}


class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(g.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.state = _State()
        self.ser = object()
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic: nothing here may reach the running client or the serial port."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def _cfg(**over):
    cfg = dict(g.DEFAULT_CONFIG)
    cfg["rockTiles"] = [[10, 20, 0], [11, 20, 0], [12, 20, 0], [13, 20, 0]]
    cfg["rockId"] = 1
    cfg.update(over)
    return cfg


def test_find_item_matches_a_substring_case_insensitively():
    inv = [_item("Teak logs"), _item("Knife")]
    assert g.find_item(inv, "knife")["name"] == "Knife"


def test_log_count_counts_every_matching_slot():
    inv = [_item("Teak logs", slot=1), _item("Teak logs", slot=2), _item("Knife", slot=0)]
    assert g.log_count(inv, "teak logs") == 2


def test_geometry_refuses_when_the_knife_is_missing():
    ok, msg = g.check_geometry(_cfg(), [_item("Teak logs")])
    assert ok is False
    assert "knife" in msg.lower()


def test_geometry_refuses_on_more_than_one_log_and_says_why(monkeypatch):
    inv = [_item("Knife", slot=0), _item("Teak logs", slot=1), _item("Teak logs", slot=2)]
    ok, msg = g.check_geometry(_cfg(), inv)
    assert ok is False
    assert "make-x" in msg.lower()


def test_geometry_refuses_when_a_rock_will_not_resolve(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    ok, msg = g.check_geometry(_cfg(), [_item("Knife", slot=0), _item("Teak logs", slot=1)])
    assert ok is False
    assert "rock" in msg.lower()


def test_geometry_refuses_when_the_rocks_are_too_far_from_the_log_AND_reports_the_pixels(
        monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 400, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_cfg(maxReachPx=220), inv)
    assert ok is False
    assert "1340" in msg          # |1740 - 400| -- the measurement must be IN the message
    assert "220" in msg


def test_geometry_passes_and_still_reports_the_measurement(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1640, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_cfg(maxReachPx=220), inv)
    assert ok is True
    assert "100" in msg


def test_setup_safety_stops_without_a_tick_clock(monkeypatch):
    """The one place this repo's 'always tolerate ctx.events is None' rule inverts: there is
    no fallback, and degrading silently to a 5-tick miner would be worse than stopping."""
    ctx = _Ctx()
    ctx.events = None
    ctx.state.config = _cfg()
    monkeypatch.setattr(g, "check_geometry", lambda cfg, inv: (True, "fine"))
    g.setup(ctx)
    assert ctx.state.safety_stopped is True
    assert "tick" in ctx.state.safety_reason.lower()
