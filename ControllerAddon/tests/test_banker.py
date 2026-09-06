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


# --- open_bank's click tracks the booth through the flight, and its resolve window
# --- budgets the walk ------------------------------------------------------------------
#
# USER 2026-08-30, watching a live bank trip (mh_live29 02:51:11): "very stale". The log shows
# the whole shape: booth clicked at (1122,591), the player then walks toward it, the 3.0s
# resolve window expires MID-WALK, the loop waits for idle and clicks the booth again at
# (1265,758) -- 140px from the first aim -- ~9 seconds for what should be one click. Two
# defects: the click flies with no refresh_target, so its aim is whatever the pre-flight
# snapshot said (the same trap as every other stale click this repo documents); and the flat
# 3.0s window cannot cover the walk the game performs before opening the bank, so the retry
# races -- and can interrupt -- a click that was about to work ("whenever you time a click, ask
# whether the player has to walk there first").

def test_open_bank_click_carries_a_refresh_target(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    kwargs_seen = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: kwargs_seen.append(kw) or True)

    banker.open_bank(_Ctx(), _bank(distance=4), timeout_s=0.5)

    assert kwargs_seen and kwargs_seen[0].get("refresh_target") is not None, \
        "the booth click must re-aim mid-flight -- a snapshot aim goes stale while walking"


def test_open_bank_resolve_budget_scales_with_the_walk():
    near = banker._click_resolve_budget(1)
    far = banker._click_resolve_budget(8)
    assert far > near, "a click from 8 tiles away must get more time than one from 1 tile"
    assert far >= 3.0 + 8 * 0.5, f"8 tiles of walking cannot resolve inside {far}s"
    assert banker._click_resolve_budget(40) <= 12.0, "a genuinely dead click must still fail fast"


# --- the first booth click happens while still running in ------------------------------
#
# USER 2026-08-30 (mh_live37 04:08:57-04:09:13, watching live): the walk to the bank ran its
# last ~10 tiles as THREE separate short viewport shift-clicks with flights between them, then
# an idle wait, then the booth click -- "weird clicks around the bank instead of just updating
# the bank booth while running there and clicking it". The human clicks the booth from a hop
# away and lets the game finish the approach. The wait-for-idle-before-EVERY-click rule dates
# to 2026-08-12 Mixology, when a click had no way to keep its aim true while moving;
# refresh_target does exactly that now, so the FIRST click goes out mid-run and only RETRIES
# (which mean something already went wrong) still wait for stillness.

def test_open_bank_first_click_does_not_wait_for_idle(monkeypatch):
    idles = []
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: idles.append(1) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(banker, "is_open", lambda: False)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(len(idles)) or True)

    banker.open_bank(_Ctx(), _bank(distance=6), timeout_s=0.5)

    assert clicks, "should have clicked the booth"
    assert clicks[0] == 0, \
        f"the FIRST booth click must fly while still running in -- {clicks[0]} idle waits first"


def test_open_bank_retries_still_wait_for_stillness(monkeypatch):
    idles = []
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: idles.append(1) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(banker, "is_open", lambda: False)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(len(idles)) or True)

    banker.open_bank(_Ctx(), _bank(distance=1), timeout_s=8.0)

    assert len(clicks) >= 2, "the harness never opens the bank, so a retry must happen"
    assert clicks[1] >= 1, "a RETRY means the first click failed -- stillness first, as before"


def test_bank_ui_buttons_never_simulate_a_misclick(monkeypatch):
    # A toggle clicked twice (misclick + corrective reclick) ends up where it started --
    # the search box opened and closed and "adam" was typed into nothing (gf_live30).
    seen = []
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True, "searchButton": {"screenX": 1123, "screenY": 1006},
        "quantityButtons": {"one": {"screenX": 719, "screenY": 1006}}})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: seen.append(k.get("simulate_mistakes")) or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "type_text", lambda ser, text: None)

    class _Ctx:
        ser = None
    banker.search_bank(_Ctx(), "adamantite")
    banker.clear_bank_search(_Ctx())
    banker.set_quantity_mode(_Ctx(), "one")
    assert seen == [False, False, False]



# MEASURED live 2026-09-05 at the Grand Exchange: two booth ids share the name "Grand Exchange
# booth"; 10061 offers Exchange/Collect (the clerk), 10060 offers Bank/Collect. Name-only
# matching picked the nearer Exchange booth and open_bank clicked "Exchange" forever.
def test_find_nearby_bank_skips_exchange_booth(monkeypatch):
    objs = [
        {"name": "Grand Exchange booth", "id": 10061, "distance": 2, "visible": True,
         "actions": ["Exchange", "Collect"]},
        {"name": "Grand Exchange booth", "id": 10060, "distance": 5, "visible": True,
         "actions": ["Bank", "Collect"]},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: objs)
    assert banker.find_nearby_bank({})["id"] == 10060


def test_find_nearby_bank_keeps_chest_and_actionless(monkeypatch):
    objs = [
        {"name": "Bank chest", "distance": 3, "visible": True, "actions": ["Use"]},
        {"name": "Bank booth", "distance": 1, "visible": True},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: objs)
    assert banker.find_nearby_bank({})["name"] == "Bank booth"
    objs.pop()
    assert banker.find_nearby_bank({})["name"] == "Bank chest"


# {"all": True} withdraw entries (2026-09-05, firemaking): one shift-click instead of a click
# per unit, and no quantity-mode click when every entry is shift-withdrawn.
def test_withdraw_loadout_all_entry_shift_withdraws_once(monkeypatch):
    calls = []
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: calls.append(name) or True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, mode: pytest.fail("quantity mode not expected"))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: pytest.fail("per-unit click not expected"))
    banker.withdraw_loadout(None, {"withdraw": [{"name": "Willow logs", "quantity": 27, "all": True}]})
    assert calls == ["Willow logs"]
