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


# --- open_bank approaches a distant bank instead of clicking at it -------------------
#
# CONFIRMED live 2026-08-28 (mahogany_homes run 4, safety-stopped on "failed to open the bank"):
# find_nearby_bank accepts a booth up to maxBankDistance (40) away, and open_bank then clicked
# it from wherever it stood. Standing inside Mariah's house in Hosidius, the booth 21 tiles away
# through a wall still reported onScreen -- projection has no notion of occlusion -- and its
# projected point landed on the chatbox tab strip at the bottom of the viewport, so all five
# clicks resolved to "Switch tab" on widget 162.19 instead of the bank. Two things made it a
# dead loop rather than a slow success: _refresh_bank_object only re-resolves within 15 tiles,
# so every retry re-aimed at the identical stale pixel; and nothing ever moved the player.

def _bank(distance, on_screen=True, x=1747, y=3598, plane=0):
    return {"id": 25808, "name": "Bank booth", "distance": distance, "onScreen": on_screen,
            "visible": True, "x": x, "y": y, "plane": plane, "screenX": 549, "screenY": 1358}


def _quiet(monkeypatch):
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])


def test_open_bank_walks_toward_a_bank_that_is_too_far_to_click(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": True, "screenX": 70, "screenY": 80})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=21), timeout_s=0.5)

    assert clicks, "should have done something"
    assert all(c.get("onMinimap") for c in clicks), \
        f"a bank 21 tiles away must be walked to, not clicked at: {clicks}"


def test_open_bank_clicks_a_bank_it_is_already_standing_at(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    walked = []
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: walked.append((x, y)) or {"onMinimap": True})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=2), timeout_s=0.5)

    assert walked == [], "a bank 2 tiles away needs no approach walk"
    assert clicks and clicks[0]["id"] == 25808


def test_open_bank_still_clicks_a_far_bank_the_minimap_cannot_reach(monkeypatch):
    # The minimap only reaches ~28 tiles, and a bank beyond it must not deadlock the loop --
    # fall back to the old behaviour (click it and let the game walk there).
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": False})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=35), timeout_s=0.5)

    assert clicks and clicks[0]["id"] == 25808


def test_open_bank_does_not_click_an_offscreen_bank_the_minimap_cannot_reach(monkeypatch):
    # Unchanged pre-existing behaviour: an off-screen aim point is outside the RuneLite window,
    # so clicking it is refused anyway -- spending the timeout is better than a guaranteed miss.
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": False})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=35, on_screen=False), timeout_s=0.5)

    assert clicks == []
