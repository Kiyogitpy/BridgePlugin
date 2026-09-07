"""Hermetic tests for cargo_hold.py (added 2026-09-07).

Every widget id here was captured live on 2026-09-07 from a 3x8 boat's cargo hold:
943 = InterfaceID.SAILING_BOAT_CARGOHOLD, 944 = ..._SIDE. Nothing here touches the client.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import cargo_hold
import controller


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = type("S", (), {"data": {}, "config": {}})()
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", lambda *a, **k: {"found": False})
    monkeypatch.setattr(controller, "get_widget_children", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)


def _widgets(mapping):
    """mapping: {(group, child): payload}"""
    def get_widget(group, child, index=None):
        return mapping.get((group, child), {"found": False})
    return get_widget


def test_is_open_reads_the_title_widget(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: Queen Death",
                   "screenX": 10, "screenY": 10},
    }))
    assert cargo_hold.is_open() is True


def test_is_open_is_false_when_the_interface_is_absent():
    assert cargo_hold.is_open() is False


def test_read_usage_parses_used_and_capacity(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 4): {"found": True, "text": "12"},
        (943, 5): {"found": True, "text": "160"},
    }))
    assert cargo_hold.read_usage() == (12, 160)


def test_read_usage_returns_none_when_unreadable():
    """Unreadable means 'cannot tell', never 'empty' -- the caller must not conclude the hold
    has room from a failed read."""
    assert cargo_hold.read_usage() == (None, None)


def test_read_usage_tolerates_thousands_separators(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 4): {"found": True, "text": "1,024"},
        (943, 5): {"found": True, "text": "2,000"},
    }))
    assert cargo_hold.read_usage() == (1024, 2000)


def test_salvage_rows_only_returns_salvage(monkeypatch):
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Plundered salvage", "itemQty": 40,
         "clickable": True, "screenX": 5, "screenY": 5},
        {"index": 1, "itemName": "Mahogany plank", "itemQty": 3,
         "clickable": True, "screenX": 6, "screenY": 6},
    ])
    rows = cargo_hold.salvage_rows()
    assert [r["itemName"] for r in rows] == ["Plundered salvage"]


def test_deposit_salvage_clicks_the_salvage_button_not_the_inventory_one(monkeypatch):
    """943.14 is Deposit Salvage; 943.15 is Deposit Inventory and would bank the chisel."""
    clicked = []
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
        (943, 15): {"found": True, "clickable": True, "screenX": 15, "screenY": 15},
    }))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, w, **kw: clicked.append(w["screenX"]) or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    assert cargo_hold.deposit_salvage(_Ctx()) is True
    assert clicked == [14]


def test_deposit_salvage_reports_failure_when_salvage_remains(monkeypatch):
    """The authoritative full signal: a Deposit Salvage that leaves salvage behind means the
    hold would not take it, whatever 943.4/943.5 say."""
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
    }))
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage"}])
    assert cargo_hold.deposit_salvage(_Ctx()) is False


def test_interface_clicks_skip_the_ui_blocker_check_and_misclicks(monkeypatch):
    seen = {}
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
    }))
    monkeypatch.setattr(controller, "click_entity", lambda ser, w, **kw: seen.update(kw) or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    cargo_hold.deposit_salvage(_Ctx())
    assert seen.get("check_ui_blockers") is False
    assert seen.get("simulate_mistakes") is False


def test_continue_box_is_detected_by_presence_not_by_its_wording(monkeypatch):
    """The 229 text is tier-specific ("...any more PLUNDERED salvage"), so never match on it."""
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (229, 4): {"found": True, "clickable": True, "text": "Click here to continue",
                   "screenX": 1, "screenY": 1},
    }))
    assert controller.continue_box_open() is True


def test_continue_box_absent_when_the_widget_is_not_clickable(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (229, 4): {"found": True, "clickable": False},
    }))
    assert controller.continue_box_open() is False
