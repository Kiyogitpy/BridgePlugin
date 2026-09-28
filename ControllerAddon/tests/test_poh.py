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
    # enter() lets a pending interaction resolve before breaking a tablet (2026-09-21), which
    # reads /player. Nothing is moving in these worlds; the tests about it fake their own.
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **k: True)


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
    monkeypatch.setattr(controller, "get_varbit", lambda varbit_id: 0)     # 4744 = 0: lands INSIDE


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


def test_the_house_is_recognised_from_the_player_record_alone(monkeypatch):
    """PHASE-B REVIEW: proving a house by scanning 3,674 objects (1.1 MB) every 0.3 s, behind a
    1 s timeout whose [] reads like "no house", would have burned tablets after a GOOD teleport."""
    scans = []
    monkeypatch.setattr(controller, "get_objects", lambda **kw: scans.append(1) or [])
    assert poh.in_house(PLAYER) is True                                   # the measured record
    assert poh.in_house(dict(PLAYER, regionId=1234)) is True, "another POH region: the template box decides"
    assert poh.in_house({"inInstance": False, "x": 1933, "y": 7105, "regionId": 7791}) is False
    assert scans == [], "no scene scan, ever"


def test_an_instance_that_is_not_a_house_is_not_a_house():
    """Raids, Pest Control, the Gauntlet and a Sailing boat are instances too."""
    assert poh.is_house_player({"inInstance": True, "x": 2657, "y": 2639, "plane": 0, "regionId": 10536}) is False
    assert poh.is_house_player({"inInstance": True}) is False
    assert poh.is_house_player(None) is False and poh.is_house_player({}) is False


def test_in_house_defaults_to_a_fresh_player_read_and_fails_closed_on_nothing(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    assert poh.in_house() is False
    monkeypatch.setattr(controller, "get_player", lambda: dict(PLAYER))
    assert poh.in_house() is True


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


# ------------------------------------------------------------------- phase-B review: the guards

def _tablet(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"name": "Teleport to house", "qty": 143, "id": 8013, "screenX": 1357, "screenY": 614}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)


def test_a_landing_that_never_confirms_costs_ONE_tablet_not_three(monkeypatch):
    """The travel phase retries a FAILED leg up to three times; a tablet is spent by the click."""
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "ENTER_BUDGET_S", 0.05)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    _tablet(monkeypatch)
    # The break CONSUMES a tablet, which is the whole reason the cooldown exists -- and since
    # 2026-09-21 the cooldown checks exactly that, so the fake has to model it. A world whose
    # inventory never changes would look like "the tablet was never spent" and rightly retry.
    held = {"qty": 143}
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"name": "Teleport to house", "qty": held["qty"], "id": 8013,
         "screenX": 1357, "screenY": 614}])
    breaks = []

    def break_it(ser, entity, **kw):
        breaks.append(1)
        held["qty"] -= 1
        return True

    monkeypatch.setattr(controller, "click_entity", break_it)
    ctx = _Ctx()
    for _ in range(3):
        assert poh.enter(ctx) is False
    assert len(breaks) == 1 and ctx.state.data["poh_tablets_used"] == 1


def test_a_tablet_that_was_never_actually_spent_is_retried(monkeypatch):
    """FOUND LIVE 2026-09-21: the routine used Ferox's Pool of Refreshment and broke a tablet
    ~3 s later while that interaction was still resolving; the house never confirmed and the
    task stopped. The cooldown exists so an unconfirmable LANDING cannot cost three tablets --
    it has nothing to say about a break that never happened."""
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "ENTER_BUDGET_S", 0.05)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    _tablet(monkeypatch)                       # a stock that never drops: nothing is consumed
    breaks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: breaks.append(1) or True)
    ctx = _Ctx()
    for _ in range(3):
        assert poh.enter(ctx) is False
    assert len(breaks) == 3, "a free attempt must not be rationed like a spent one"


def test_a_house_teleport_that_lands_outside_is_refused_before_a_tablet_is_spent(monkeypatch):
    """vendor/teleportation_items.tsv: varbit 4744 = 1 -> a plain Break lands OUTSIDE at the portal,
    where in_house() is False forever."""
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_varbit", lambda varbit_id: 1)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    _tablet(monkeypatch)
    breaks = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: breaks.append(1) or True)
    assert poh.enter(_Ctx()) is False and breaks == []


def test_an_unreadable_setting_fails_closed_and_spends_no_tablet(monkeypatch):
    """Phase-B fix-wave re-review 2026-09-28: the first draft carried on when varbit 4744 was
    unreadable. Every other varbit gate here fails CLOSED on unknown."""
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_varbit", lambda varbit_id: None)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    _tablet(monkeypatch)
    breaks = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: breaks.append(1) or True)
    assert poh.enter(_Ctx()) is False and breaks == []


def _spending_tablets(monkeypatch, lands=False):
    """A tablet stack that really drops by one per Break; `lands` flips in_house on the break."""
    held = {"qty": 10, "in": False}
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"name": "Teleport to house", "qty": held["qty"], "id": 8013, "screenX": 1357, "screenY": 614}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(poh, "in_house", lambda player=None: held["in"])

    def break_it(ser, entity, **kw):
        held["qty"] -= 1
        held["in"] = lands
        return True
    monkeypatch.setattr(controller, "click_entity", break_it)
    return held


def test_a_confirmed_landing_ends_the_cooldown(monkeypatch):
    """Otherwise a re-plan through the house within 60 s was refused as 'never confirmed'."""
    _quiet(monkeypatch)
    _spending_tablets(monkeypatch, lands=True)
    ctx = _Ctx()
    assert poh.enter(ctx) is True
    assert not poh.landing_pending(ctx) and "poh_tablet_at" not in ctx.state.data


def test_a_spent_unconfirmed_tablet_is_pending_until_the_cooldown_runs_out(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "ENTER_BUDGET_S", 0.05)
    _spending_tablets(monkeypatch, lands=False)
    ctx = _Ctx()
    assert poh.enter(ctx) is False and poh.landing_pending(ctx)
    ctx.state.data["poh_tablet_at"] -= poh.TABLET_COOLDOWN_S + 1
    assert not poh.landing_pending(ctx)
    assert not poh.landing_pending(_Ctx()), "no tablet ever broken: nothing pending"


def test_a_tablet_never_actually_spent_is_not_pending(monkeypatch):
    """enter() retries a break that consumed nothing (FOUND LIVE 2026-09-21), so the leg must
    count that as a failure to retry, not wait on a landing that cannot come."""
    _quiet(monkeypatch)
    monkeypatch.setattr(poh, "ENTER_BUDGET_S", 0.05)
    monkeypatch.setattr(poh, "in_house", lambda player=None: False)
    _tablet(monkeypatch)                                 # a stock that never drops
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    ctx = _Ctx()
    assert poh.enter(ctx) is False and not poh.landing_pending(ctx)


def test_the_classic_house_template_chunks_count_as_the_house():
    """vendor/teleportation_items.tsv lands inside the house at (1923,5709), region 7769 -- not
    measured on this account, but a house built there must still confirm."""
    assert poh.is_house_player({"inInstance": True, "x": 1923, "y": 5709, "plane": 0, "regionId": 7769})
    assert poh.is_house_player({"inInstance": True, "x": 1900, "y": 5700, "plane": 0, "regionId": 1})
    assert not poh.is_house_player({"inInstance": False, "x": 1923, "y": 5709, "regionId": 7769})


def test_outside_the_house_needs_a_real_position():
    assert poh.is_outside_house({"x": 3143, "y": 3442, "plane": 0, "inInstance": False})
    assert not poh.is_outside_house({}) and not poh.is_outside_house(None)
    assert not poh.is_outside_house(dict(PLAYER))


def test_a_failed_entry_click_closes_the_menu_it_opened(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_player", lambda: dict(PLAYER))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [BOX])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 590, "child": 5, "index": 8, "screenX": 776, "screenY": 415,
         "text": "<col=ccccff>H:</col> Cooking Guild", "actions": ["Cooking Guild"]}])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: False)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    assert poh.use_box(_Ctx(), "Cooking Guild") is False
    assert keys == ["ESC"]


def test_leaving_the_house_is_what_confirms_a_box_hop(monkeypatch):
    """Our own vendored tables put the Mining Guild landing 28 tiles apart; 'no longer in the
    house' is unambiguous."""
    _quiet(monkeypatch)
    reads = iter([dict(PLAYER), {}, {"x": 3046, "y": 9735, "plane": 0, "inInstance": False}])
    monkeypatch.setattr(controller, "get_player", lambda: next(reads))
    assert poh.await_left_house(2.0) is True
    monkeypatch.setattr(controller, "get_player", lambda: dict(PLAYER))
    assert poh.await_left_house(0.05) is False


def test_the_pool_waits_for_run_energy_too(monkeypatch):
    """With only run low, breaking out on a full prayer bar returned before the player reached the
    pool, and the next leg's click cancelled the drink."""
    _quiet(monkeypatch)
    reads = iter([dict(PLAYER, prayer=77, prayerReal=77, runEnergy=20),      # needs a drink
                  dict(PLAYER, prayer=77, prayerReal=77, runEnergy=20),      # walking to the pool
                  dict(PLAYER, prayer=77, prayerReal=77, runEnergy=100)])    # drunk
    seen = []

    def get_player():
        record = next(reads)
        seen.append(record["runEnergy"])
        return record
    monkeypatch.setattr(controller, "get_player", get_player)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [POOL_NEAR])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    assert poh.drink_pool(_Ctx()) is True
    assert seen[-1] == 100, "it kept polling until run energy read full"


# --- 2026-09-20: an item MOUNTED in the house (USER: "there is a digsite pendant mounted in poh") --

PENDANT = {"id": 33418, "name": "Digsite Pendant", "actions": ["Digsite", "Fossil Island", "Remove"],
           "clickable": True, "screenX": 400, "screenY": 300, "x": 1950, "y": 7060, "plane": 1,
           "instX": 11670, "instY": 1450, "distance": 2147483647}


def test_a_mounted_item_is_found_by_name_and_action_not_by_a_guessed_id(monkeypatch):
    """The vendored table lists the mounted pendant under ids 33417-33419 (one per unlock tier).
    Which one a given house holds is not knowable offline, so the object is matched by NAME and by
    OFFERING the action, the way travel.py identifies a door."""
    junk = dict(PENDANT, name="Mounted glory", actions=["Edgeville", "Remove"])
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 11669, "instY": 1449})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [junk, PENDANT])
    assert poh.find_named("digsite pendant", "Digsite") is PENDANT
    assert poh.find_named("digsite pendant", "Lithkren") is None, "not unlocked on this pendant"
    assert poh.find_named("xeric", "Digsite") is None


def test_use_mounted_takes_the_destination_from_the_menu(monkeypatch):
    seen = []
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 11669, "instY": 1449})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [PENDANT])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, oid: "refresh")
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, obj, action, **kw: seen.append((obj["name"], action, kw.get("refresh_target"))) or True)
    assert poh.use_mounted(_Ctx(), "digsite pendant", "Digsite") is True
    assert seen == [("Digsite Pendant", "Digsite", "refresh")], "the MENU entry: a left-click may be another destination"


# --- a mounted item that offers a MENU, not one action per destination (FOUND LIVE 2026-09-21) --
#
# The bats task planned "mounted digsite pendant: Digsite" and the pendant was right there in the
# house -- offering ['Teleport menu', 'Configuration', 'Remove']. find_named requires the action to
# be PRESENT, so it matched nothing and the leg failed three times on an object that was working.

MENU_PENDANT = dict(PENDANT, actions=["Teleport menu", "Configuration", "Remove"])


# The teleport interface as the client really draws it, MEASURED LIVE 2026-09-21 by dumping
# widgets while it was open. Note the TITLE also contains the word "Digsite" -- a plain substring
# search over the dump picks it instead of the destination, which is why matching is anchored on
# the row number.
MENU_DUMP = [
    {"group": 187, "child": 0, "index": 1, "screenX": 985, "screenY": 305,
     "text": "Digsite Pendant teleport"},
    {"group": 187, "child": 3, "index": 0, "screenX": 989, "screenY": 357,
     "text": "<col=735a28>1</col>: Digsite"},
    {"group": 187, "child": 3, "index": 1, "screenX": 989, "screenY": 390,
     "text": "<col=735a28>2</col>: Fossil Island"},
    {"group": 187, "child": 3, "index": 2, "screenX": 989, "screenY": 423,
     "text": "<col=735a28>3</col>: Lithkren"},
    {"group": 187, "child": 3, "index": 3, "screenX": 989, "screenY": 456,
     "text": "<col=735a28>4</col>: Nowhere"},
]


def test_the_teleport_interface_rows_are_parsed_and_the_title_is_not_one():
    assert [label for label, _ in poh.menu_rows(MENU_DUMP)] == [
        "Digsite", "Fossil Island", "Lithkren", "Nowhere"]
    assert poh.menu_entry(MENU_DUMP, "Digsite")["screenY"] == 357
    assert poh.menu_entry(MENU_DUMP, "Lithkren")["screenY"] == 423
    assert poh.menu_entry(MENU_DUMP, "Nonesuch") is None


def test_a_menu_only_mounted_item_is_used_through_its_teleport_interface(monkeypatch):
    clicked, picked = [], []
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 11669, "instY": 1449})
    monkeypatch.setattr(controller, "get_objects",
                        lambda radius=None, fresh=False: [MENU_PENDANT])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, oid: "refresh")
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, obj, action, **kw: clicked.append(action) or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: MENU_DUMP)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: picked.append((ent["screenX"], ent["screenY"])) or True)
    monkeypatch.setattr(poh.dialogue, "choose",
                        lambda ctx, needle, **kw: pytest.fail("the interface answered already"))

    assert poh.use_mounted(_Ctx(), "digsite pendant", "Digsite") is True
    assert clicked == ["Teleport menu"], "the object's OWN spelling of the menu entry"
    assert picked == [(989, 357)], "the Digsite ROW, not the title that also says Digsite"


def test_the_direct_action_still_wins_when_the_item_offers_one(monkeypatch):
    """The house fairy ring offers 'Zanaris' directly. That path must not start paying for a
    dialogue it does not need."""
    chosen = []
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 11669, "instY": 1449})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [PENDANT])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, oid: "refresh")
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, obj, action, **kw: True)
    monkeypatch.setattr(poh.dialogue, "choose",
                        lambda ctx, needle, **kw: chosen.append(needle) or True)

    assert poh.use_mounted(_Ctx(), "digsite pendant", "Digsite") is True
    assert chosen == [], "no dialogue when the destination is its own action"


def test_a_menu_that_never_offers_the_destination_closes_what_it_opened(monkeypatch):
    """LEAVE NOTHING OPEN. MEASURED 2026-09-21: with group 187 still up from a previous attempt,
    the retry's right-click landed on the interface instead of the pendant."""
    keys = []
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 11669, "instY": 1449})
    monkeypatch.setattr(controller, "get_objects",
                        lambda radius=None, fresh=False: [MENU_PENDANT])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, oid: "refresh")
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, obj, action, **kw: True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: MENU_DUMP)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(poh, "MENU_BUDGET_S", 0.01)
    monkeypatch.setattr(poh.dialogue, "choose", lambda ctx, needle, **kw: False)
    assert poh.use_mounted(_Ctx(), "digsite pendant", "Karamja") is False
    assert "ESC" in keys


def test_a_refused_menu_click_also_closes_the_interface(monkeypatch):
    keys = []
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 11669, "instY": 1449})
    monkeypatch.setattr(controller, "get_objects",
                        lambda radius=None, fresh=False: [MENU_PENDANT])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, oid: "refresh")
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, obj, action, **kw: False)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    assert poh.use_mounted(_Ctx(), "digsite pendant", "Digsite") is False
    assert keys == ["ESC"], "a failed right-click must not leave an interface up for the retry"


def test_the_menu_entry_is_matched_whatever_its_capitalisation(monkeypatch):
    """MEASURED: the pendant says 'Teleport menu' and the Ornate Jewellery Box says
    'Teleport Menu'."""
    box = dict(PENDANT, name="Ornate Jewellery Box",
               actions=["Teleport Menu", "Mining Guild", "Remove"])
    clicked = []
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 11669, "instY": 1449})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [box])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, oid: "refresh")
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, obj, action, **kw: clicked.append(action) or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])
    monkeypatch.setattr(poh, "MENU_BUDGET_S", 0.01)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(poh.dialogue, "choose", lambda ctx, needle, **kw: True)
    assert poh.use_mounted(_Ctx(), "ornate jewellery box", "Edgeville") is True
    assert clicked == ["Teleport Menu"]


def test_use_mounted_without_the_object_in_view_is_false(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"instX": 1, "instY": 1})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [])
    assert poh.use_mounted(_Ctx(), "digsite pendant", "Digsite") is False
