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
    monkeypatch.setattr(cargo_hold, "HOLD_ACTION_BUDGET_S", 0.02)   # I7 poll -- keep the test fast
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
    }))
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage"}])
    assert cargo_hold.deposit_salvage(_Ctx()) is False


# --- I7: poll instead of sleep-then-read-once ------------------------------------------------

def test_deposit_salvage_polls_and_succeeds_once_the_inventory_actually_clears(monkeypatch):
    """An early read must not be trusted -- the inventory takes a couple of polls to clear."""
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
    }))
    reads = {"n": 0}

    def _inv():
        reads["n"] += 1
        return [{"name": "Plundered salvage"}] if reads["n"] < 3 else []
    monkeypatch.setattr(controller, "get_inventory", _inv)
    assert cargo_hold.deposit_salvage(_Ctx()) is True
    assert reads["n"] >= 3


def test_deposit_salvage_returns_hold_unknown_when_the_interface_is_not_open():
    """'Cannot tell', never 'full' -- see stow_phase's own forced_full computation."""
    assert cargo_hold.deposit_salvage(_Ctx()) is cargo_hold.HOLD_UNKNOWN
    assert bool(cargo_hold.deposit_salvage(_Ctx())) is False   # still falsy


def test_deposit_salvage_returns_hold_unknown_when_the_button_never_registers(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": False},   # not clickable -- _click returns False
    }))
    assert cargo_hold.deposit_salvage(_Ctx()) is cargo_hold.HOLD_UNKNOWN


def test_hold_unknown_is_distinct_from_a_real_false():
    """The whole point of the sentinel: `is False` must not match it."""
    assert cargo_hold.HOLD_UNKNOWN is not False
    assert (cargo_hold.HOLD_UNKNOWN is False) is False


def test_withdraw_salvage_polls_before_giving_up(monkeypatch):
    monkeypatch.setattr(cargo_hold, "HOLD_ACTION_BUDGET_S", 0.02)   # keep the "never arrives" path fast
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Plundered salvage", "itemQty": 14,
         "clickable": True, "screenX": 7, "screenY": 7},
    ])
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    reads = []
    monkeypatch.setattr(controller, "get_inventory", lambda: reads.append(1) or [])
    assert cargo_hold.withdraw_salvage(_Ctx()) is False
    assert len(reads) >= 2, "must poll more than once before giving up"


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


# --- withdraw_salvage / open_hold / close_hold -------------------------------------------
# Added after code review flagged these three as untested by the brief's own fixed test list
# (11 tests, Step 5). withdraw_salvage in particular is where _CLICK_KWARGS must NOT be
# splatted into click_menu_action -- that function has no simulate_mistakes parameter, so
# splatting is a runtime TypeError. Nothing in the original 11 would have caught a future
# "cleanup" that reintroduced the splat; the first test below is the regression guard for it.

_OPEN_TITLE = {"found": True, "clickable": True, "text": "Cargo Hold: X",
               "screenX": 1, "screenY": 1}


def test_withdraw_salvage_calls_click_menu_action_correctly(monkeypatch):
    """The load-bearing regression guard: click_menu_action's signature has no
    simulate_mistakes parameter, so **_CLICK_KWARGS would raise TypeError if splatted here.
    Assert the actual option string and kwargs passed, including that key's absence."""
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Plundered salvage", "itemQty": 14,
         "clickable": True, "screenX": 7, "screenY": 7},
    ])
    calls = []

    def fake_click_menu_action(ser, entity, option, **kwargs):
        calls.append((option, kwargs))
        return True

    monkeypatch.setattr(controller, "click_menu_action", fake_click_menu_action)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage"}])
    cargo_hold.withdraw_salvage(_Ctx())
    assert len(calls) == 1
    option, kwargs = calls[0]
    assert option == "Withdraw-All"
    assert kwargs.get("check_ui_blockers") is False
    assert "simulate_mistakes" not in kwargs


def test_withdraw_salvage_returns_false_with_no_salvage_rows(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [])
    assert cargo_hold.withdraw_salvage(_Ctx()) is False


def test_withdraw_salvage_returns_false_when_interface_not_open():
    # _no_live_bridge's default get_widget returns {"found": False} for everything, so 943.1
    # never reads clickable and is_open() is False -- withdraw_salvage must bail before ever
    # touching the grid or clicking anything.
    assert cargo_hold.withdraw_salvage(_Ctx()) is False


def test_withdraw_salvage_returns_true_when_salvage_actually_arrives(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Plundered salvage", "itemQty": 14,
         "clickable": True, "screenX": 7, "screenY": 7},
    ])
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage"}])
    assert cargo_hold.withdraw_salvage(_Ctx()) is True


def test_withdraw_salvage_returns_false_when_nothing_arrives(monkeypatch):
    """A menu click that registered but moved nothing (e.g. the inventory was already full)
    must not be reported as a success."""
    monkeypatch.setattr(cargo_hold, "HOLD_ACTION_BUDGET_S", 0.02)   # I7 poll -- keep the test fast
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Plundered salvage", "itemQty": 14,
         "clickable": True, "screenX": 7, "screenY": 7},
    ])
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    assert cargo_hold.withdraw_salvage(_Ctx()) is False


def test_open_hold_returns_true_immediately_when_already_open(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))

    def fail_find_facility(*a, **k):
        raise AssertionError("find_facility must not be called when the hold is already open")

    monkeypatch.setattr(cargo_hold.sailing, "find_facility", fail_find_facility)
    assert cargo_hold.open_hold(_Ctx()) is True


def test_open_hold_returns_false_when_no_hold_on_our_boat(monkeypatch):
    monkeypatch.setattr(cargo_hold.sailing, "find_facility", lambda *a, **k: None)
    assert cargo_hold.open_hold(_Ctx()) is False


def test_open_hold_clicks_the_found_facility_and_confirms_it_opened(monkeypatch):
    hold_obj = {"screenX": 50, "screenY": 50}
    monkeypatch.setattr(cargo_hold.sailing, "find_facility", lambda *a, **k: hold_obj)
    state = {"n": 0}

    def fake_get_widget(group, child, index=None):
        if (group, child) == (943, 1):
            state["n"] += 1
            # Closed on the pre-check (call 1, before find_facility is even consulted);
            # open from the first poll onward (call 2+), simulating the click landing.
            return _OPEN_TITLE if state["n"] >= 2 else {"found": False}
        return {"found": False}

    monkeypatch.setattr(controller, "get_widget", fake_get_widget)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, w, **kw: clicked.append(w) or True)
    assert cargo_hold.open_hold(_Ctx()) is True
    assert clicked == [hold_obj]


def test_close_hold_returns_true_immediately_when_already_closed(monkeypatch):
    # _no_live_bridge's default get_widget means is_open() is False from the start --
    # close_hold must recognise that and never send a keypress at all.
    key_taps = []
    monkeypatch.setattr(controller, "key_tap", lambda *a, **k: key_taps.append(a))
    assert cargo_hold.close_hold(_Ctx()) is True
    assert key_taps == []


def test_click_null_checks_both_screen_coordinates(monkeypatch):
    """Minor fix: _click used to check screenX only -- a widget carrying one but not the other
    (a malformed/partial read) would still be clicked at whatever garbage screenY it had."""
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14},   # no screenY
    }))
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, w, **kw: clicked.append(w) or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    cargo_hold.deposit_salvage(_Ctx())
    assert not clicked


# --- C2: deposit_item -- the sink for KEEPERS (943.14 only ever deposits salvage) -----------

def test_deposit_item_deposits_the_named_item_and_confirms_it_dropped(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Rune scimitar", "itemQty": 1,
         "clickable": True, "screenX": 9, "screenY": 9},
    ])
    calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: calls.append((option, kw)) or True)
    reads = {"n": 0}

    def _inv():
        reads["n"] += 1
        return [{"name": "Rune scimitar"}] if reads["n"] < 2 else []
    monkeypatch.setattr(controller, "get_inventory", _inv)
    assert cargo_hold.deposit_item(_Ctx(), "Rune scimitar") is True
    assert calls == [("Deposit-All", {"offset": (0, 0), "check_ui_blockers": False})]


def test_deposit_item_matches_exact_name_not_substring(monkeypatch):
    """A substring match here could pick up a DIFFERENT item that merely shares a keepItems
    substring with the intended one -- deposit_item must never do that."""
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Rune scimitar", "clickable": True, "screenX": 1, "screenY": 1},
        {"index": 1, "itemName": "Rune cannonball", "clickable": True, "screenX": 2, "screenY": 2},
    ])
    clicked = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: clicked.append(entity["itemName"]) or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Rune scimitar"}])
    cargo_hold.deposit_item(_Ctx(), "rune")   # would substring-match BOTH if not exact
    assert clicked == []


def test_deposit_item_returns_false_when_the_item_is_not_in_the_side_panel(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [])
    assert cargo_hold.deposit_item(_Ctx(), "Rune scimitar") is False


def test_deposit_item_returns_false_when_the_hold_is_not_open():
    # _no_live_bridge's default get_widget means is_open() is False -- deposit_item must bail
    # before ever touching the side panel.
    assert cargo_hold.deposit_item(_Ctx(), "Rune scimitar") is False


def test_deposit_item_returns_false_with_an_empty_name():
    assert cargo_hold.deposit_item(_Ctx(), "") is False
    assert cargo_hold.deposit_item(_Ctx(), None) is False


def test_deposit_item_does_not_splat_click_kwargs_into_click_menu_action(monkeypatch):
    """The same regression guard as withdraw_salvage's: click_menu_action has no
    simulate_mistakes parameter, so **_CLICK_KWARGS would raise TypeError if splatted here."""
    monkeypatch.setattr(cargo_hold, "HOLD_ACTION_BUDGET_S", 0.02)   # the inventory never
                                                                     # shrinks in this fixture --
                                                                     # only the call shape matters
    monkeypatch.setattr(controller, "get_widget", _widgets({(943, 1): _OPEN_TITLE}))
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Rune scimitar", "clickable": True, "screenX": 1, "screenY": 1},
    ])
    calls = []

    def fake_click_menu_action(ser, entity, option, **kwargs):
        calls.append((option, kwargs))
        return True
    monkeypatch.setattr(controller, "click_menu_action", fake_click_menu_action)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Rune scimitar"}])
    cargo_hold.deposit_item(_Ctx(), "Rune scimitar")
    assert len(calls) == 1
    option, kwargs = calls[0]
    assert option == "Deposit-All"
    assert "simulate_mistakes" not in kwargs
