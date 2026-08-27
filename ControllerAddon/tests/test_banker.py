import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import banker
import controller


class _Ctx:
    def __init__(self, ser=None):
        self.ser = ser


def test_switch_bank_tab_skips_click_when_already_on_tab(monkeypatch):
    monkeypatch.setattr(controller, "get_bank",
                        lambda fresh=False: {"currentTab": 3, "tabs": []})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1))
    assert banker.switch_bank_tab(_Ctx(), 3)
    assert clicked == []


def test_switch_bank_tab_returns_false_when_tab_button_not_found(monkeypatch):
    monkeypatch.setattr(controller, "get_bank",
                        lambda fresh=False: {"currentTab": 0, "tabs": []})
    assert not banker.switch_bank_tab(_Ctx(), 8)


def test_switch_bank_tab_retries_click_until_current_tab_confirms_the_switch(monkeypatch):
    """CONFIRMED bug this fixes: switch_bank_tab used to click once and return True
    unconditionally, with no confirmation the click actually landed. Here currentTab only
    flips to the target tab after the SECOND click -- a single-attempt caller would have
    wrongly reported success after the first (dropped/missed) click."""
    state = {"currentTab": 0, "calls": 0}

    def fake_get_bank(fresh=False):
        return {
            "currentTab": state["currentTab"],
            "tabs": [{"index": 8, "screenX": 500, "screenY": 500}],
        }

    def fake_click_entity(ser, entity, **kw):
        state["calls"] += 1
        if state["calls"] >= 2:
            state["currentTab"] = 8

    monkeypatch.setattr(controller, "get_bank", fake_get_bank)
    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    assert banker.switch_bank_tab(_Ctx(), 8)
    assert state["calls"] == 2


def test_switch_bank_tab_gives_up_after_repeated_misses(monkeypatch):
    """A genuine, persistent failure (not just one dropped click) must still surface as False
    -- the fix is retrying a bounded number of times, not trusting blindly forever."""
    monkeypatch.setattr(controller, "get_bank",
                        lambda fresh=False: {"currentTab": 0,
                                              "tabs": [{"index": 8, "screenX": 500, "screenY": 500}]})
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicks.append(1))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    assert not banker.switch_bank_tab(_Ctx(), 8)
    assert len(clicks) == 3
