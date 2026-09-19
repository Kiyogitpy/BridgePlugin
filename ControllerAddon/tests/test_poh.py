import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import poh
import travel


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


class _State:
    def __init__(self):
        self.data = {}
        self.stops = []

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self):
        self.ser = None
        self.state = _State()
        self.events = None


def _quiet(monkeypatch):
    monkeypatch.setattr(poh.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


PLAYER = {"x": 1933, "y": 7105, "plane": 1, "instPlane": 0, "inInstance": True,
          "instX": 11669, "instY": 1449, "regionId": 7791}

# Two pool tiles: template x/y put them 65 tiles apart and both report the house's constant
# lying `distance` -- only instX/instY tell them apart.
POOL_NEAR = {"id": 29239, "x": 1935, "y": 7106, "plane": 1, "instX": 11671, "instY": 1447,
             "distance": 2147483647, "clickable": True, "screenX": 700, "screenY": 420}
POOL_FAR = {"id": 29238, "x": 2000, "y": 7106, "plane": 1, "instX": 11700, "instY": 1449,
            "distance": 2147483647, "clickable": True, "screenX": 720, "screenY": 440}
BOX = {"id": 29156, "x": 1934, "y": 7105, "plane": 1, "instX": 11750, "instY": 1449,
       "distance": 2147483647, "clickable": True, "screenX": 640, "screenY": 400}


# -------------------------------------------------------------------------------- find / in_house

def test_find_picks_by_instance_distance_and_ignores_the_lying_distance_field(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [POOL_FAR, BOX, POOL_NEAR])
    assert poh.find(poh.POOL_IDS, player=PLAYER) is POOL_NEAR


def test_find_skips_unclickable_and_screenless_records(monkeypatch):
    unclickable = dict(POOL_NEAR, clickable=False)
    no_screen = dict(POOL_FAR, screenX=None)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [unclickable, no_screen])
    assert poh.find(poh.POOL_IDS, player=PLAYER) is None


def test_in_house_needs_both_an_instance_and_a_house_object(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    assert poh.in_house({"inInstance": False, "instX": 1, "instY": 1}) is False
    assert poh.in_house({"inInstance": True, "instX": 1, "instY": 1}) is False
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [BOX])
    assert poh.in_house(PLAYER) is True


def test_in_house_defaults_to_a_fresh_player_read_and_fails_closed_on_nothing(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    touched = []
    monkeypatch.setattr(controller, "get_objects", lambda **kw: touched.append(1) or [])
    assert poh.in_house() is False
    assert touched == [], "an empty player read must not even reach the scene scan"


# --------------------------------------------------------------------------------------- enter

def test_enter_already_inside_touches_nothing(monkeypatch):
    monkeypatch.setattr(poh, "in_house", lambda player=None: True)
    touched = []
    monkeypatch.setattr(controller, "get_inventory", lambda: touched.append(1) or [])
    assert poh.enter(_Ctx()) is True
    assert touched == []


def test_enter_with_no_tablet_is_a_clean_false(monkeypatch):
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Shark", "qty": 5}])
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert poh.enter(_Ctx()) is False
    assert clicked == []


def test_enter_breaks_the_tablet_and_confirms_arrival(monkeypatch):
    _quiet(monkeypatch)
    calls = {"n": 0}

    def in_house(player=None):
        calls["n"] += 1
        return calls["n"] > 1
    monkeypatch.setattr(poh, "in_house", in_house)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Teleport to house", "screenX": 10, "screenY": 10}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append((entity.get("name"), kw.get("expected_action")))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    ctx = _Ctx()
    assert poh.enter(ctx) is True
    assert clicks == [("Teleport to house", "Break")]
    assert ctx.state.data["poh_tablets_used"] == 1


def test_enter_gives_up_after_the_budget_when_it_never_confirms(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Teleport to house", "screenX": 10, "screenY": 10}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(poh, "ENTER_BUDGET_S", 0.05)
    assert poh.enter(_Ctx()) is False


def test_enter_falls_back_to_the_menu_on_a_withheld_click(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Teleport to house", "screenX": 10, "screenY": 10}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: controller.CLICK_WITHHELD)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.append(option) or True)
    monkeypatch.setattr(poh, "ENTER_BUDGET_S", 0.05)
    ctx = _Ctx()
    assert poh.enter(ctx) is False        # in_house never confirms in this test
    assert menu == ["Break"]
    assert ctx.state.data["poh_tablets_used"] == 1, "the tablet is consumed by the click, confirmed or not"


def test_enter_returns_false_when_both_the_click_and_the_menu_fallback_fail(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Teleport to house", "screenX": 10, "screenY": 10}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: controller.CLICK_WITHHELD)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: False)
    ctx = _Ctx()
    assert poh.enter(ctx) is False
    assert "poh_tablets_used" not in ctx.state.data


# ------------------------------------------------------------------------------------ drink_pool

FULL_PLAYER = {"prayer": 77, "prayerReal": 77, "runEnergy": 100}
LOW_PLAYER = {"prayer": 40, "prayerReal": 77, "runEnergy": 100}


def test_drink_pool_skips_entirely_when_full(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(FULL_PLAYER))
    touched = []
    monkeypatch.setattr(poh, "find", lambda ids, player=None: touched.append(1))
    assert poh.drink_pool(_Ctx()) is True
    assert touched == []


def test_drink_pool_skips_when_every_stat_is_unknown(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    touched = []
    monkeypatch.setattr(poh, "find", lambda ids, player=None: touched.append(1))
    assert poh.drink_pool(_Ctx()) is True
    assert touched == []


def test_drink_pool_drinks_when_low_and_confirms_the_rise(monkeypatch):
    _quiet(monkeypatch)
    calls = {"n": 0}

    def get_player():
        calls["n"] += 1
        return dict(LOW_PLAYER, prayer=(77 if calls["n"] > 1 else 40))
    monkeypatch.setattr(controller, "get_player", get_player)
    pool = dict(POOL_NEAR)
    monkeypatch.setattr(poh, "find", lambda ids, player=None: pool)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: "REFRESH")
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append((entity, kw.get("expected_action"), kw.get("refresh_target")))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    assert poh.drink_pool(_Ctx()) is True
    assert clicks == [(pool, "Drink", "REFRESH")]


def test_drink_pool_with_no_pool_is_true_with_no_input(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(LOW_PLAYER))
    monkeypatch.setattr(poh, "find", lambda ids, player=None: None)
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert poh.drink_pool(_Ctx()) is True
    assert clicked == []


def test_drink_pool_is_true_even_when_prayer_never_rises(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_player", lambda: dict(LOW_PLAYER))
    monkeypatch.setattr(poh, "find", lambda ids, player=None: dict(POOL_NEAR))
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(poh, "POOL_BUDGET_S", 0.05)
    assert poh.drink_pool(_Ctx()) is True


# -------------------------------------------------------------------------- box_entry / box_is_locked

DUMP = [
    {"group": 590, "child": 5, "index": 8, "screenX": 500, "screenY": 300,
     "text": "<col=ccccff>H:</col> Cooking Guild", "actions": ["Cooking Guild"]},
    {"group": 590, "child": 2, "index": 7, "screenX": 500, "screenY": 320,
     "text": "<col=ccccff>3:</col> Ferox Enclave", "actions": ["Ferox Enclave"]},
    {"group": 590, "child": 2, "index": 8, "screenX": 500, "screenY": 340,
     "text": "<col=ccccff>4:</col> <str>Fortis Colosseum</str>", "actions": ["Fortis Colosseum"]},
    {"group": 590, "child": 5, "index": 3, "screenX": 500, "screenY": 360, "text": "Skills necklace"},
    {"group": 590, "child": 1, "index": 11, "screenX": 500, "screenY": 380, "text": "", "actions": ["Close"]},
]


def test_box_entry_finds_the_destination_case_insensitively():
    entry = poh.box_entry(DUMP, "cooking guild")
    assert entry is not None and entry["child"] == 5 and entry["index"] == 8


def test_box_entry_refuses_a_heading_with_no_actions():
    assert poh.box_entry(DUMP, "Skills necklace") is None


def test_box_entry_refuses_the_locked_destination():
    assert poh.box_entry(DUMP, "Fortis Colosseum") is None


def test_box_entry_refuses_two_matches_rather_than_guessing():
    dup = DUMP + [dict(DUMP[0])]
    assert poh.box_entry(dup, "Cooking Guild") is None


def test_box_is_locked_true_only_for_the_struck_through_entry():
    assert poh.box_is_locked(DUMP, "Fortis Colosseum") is True
    assert poh.box_is_locked(DUMP, "Cooking Guild") is False
    assert poh.box_is_locked(DUMP, "Nonexistent Place") is False


# --------------------------------------------------------------------------------------- use_box

def test_use_box_opens_the_menu_then_clicks_the_entry(monkeypatch):
    box = dict(BOX)
    monkeypatch.setattr(poh, "find", lambda ids, player=None: box)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: "REFRESH")
    menu_calls = []

    def click_menu_action(ser, entity, option, **kw):
        menu_calls.append((entity, option, kw.get("refresh_target")))
        return True
    monkeypatch.setattr(controller, "click_menu_action", click_menu_action)
    entry = DUMP[0]
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [entry])
    entity_clicks = []

    def click_entity(ser, e, **kw):
        entity_clicks.append((e, kw.get("offset"), kw.get("check_ui_blockers"),
                              kw.get("simulate_mistakes"), kw.get("expected_action")))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    assert poh.use_box(_Ctx(), "Cooking Guild") is True
    assert menu_calls == [(box, poh.BOX_MENU_OPTION, "REFRESH")]
    assert entity_clicks == [(entry, (0, 0), False, False, "Cooking Guild")]


def test_use_box_locked_destination_escapes_and_fails(monkeypatch):
    monkeypatch.setattr(poh, "find", lambda ids, player=None: dict(BOX))
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [DUMP[2]])
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert poh.use_box(_Ctx(), "Fortis Colosseum") is False
    assert keys == ["ESC"]
    assert clicked == []


def test_use_box_gives_up_when_the_destination_never_appears(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "find", lambda ids, player=None: dict(BOX))
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(poh, "BOX_MENU_BUDGET_S", 0.05)
    assert poh.use_box(_Ctx(), "Cooking Guild") is False
    assert keys == ["ESC"]


def test_use_box_with_no_box_in_view_is_false(monkeypatch):
    monkeypatch.setattr(poh, "find", lambda ids, player=None: None)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    menu = []
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: menu.append(1) or True)
    assert poh.use_box(_Ctx(), "Cooking Guild") is False
    assert keys == [] and menu == []


# --------------------------------------------------------------------------------------- use_ring

def test_use_ring_forwards_the_house_rings_own_id(monkeypatch):
    seen = {}

    def hop(ctx, code, expect, **kw):
        seen.update(kw, code=code, expect=expect)
        return True
    monkeypatch.setattr(travel, "hop_fairy_ring", hop)
    assert poh.use_ring(_Ctx(), "AJQ", "Dorgesh") is True
    assert seen == {"log": "poh", "ring_ids": (poh.RING_ID,), "code": "AJQ", "expect": "Dorgesh"}
