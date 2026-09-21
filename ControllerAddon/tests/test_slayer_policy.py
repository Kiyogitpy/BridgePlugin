"""slayer_policy: the DECISIONS of the bank, loot and gear phases, with none of the clicking.

Plan tasks C2 (bank verdict), D2 (loot tiers), C3 (base gear). Pure functions only; the phases
that act on these answers are not built and wait for a live run.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import slayer_loadout
import slayer_policy as sp


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


def _loadout(withdraw=(), problems=(), slots=20):
    return slayer_loadout.Loadout(list(withdraw), [], slots, list(problems), False)


NEED_FOOD = [{"name": "Shark", "quantity": 5}]


# --- C2: bank, carry on, skip or stop ------------------------------------------------------------

def test_a_met_loadout_with_room_to_loot_carries_on():
    assert sp.bank_verdict(_loadout(), free_slots=8, tasks_cfg={}, tablets_total=40)[0] == sp.GO


def test_an_unmet_loadout_banks_once():
    verdict, why = sp.bank_verdict(_loadout(NEED_FOOD), 8, {}, 40)
    assert verdict == sp.BANK and "Shark" in why


def test_a_loadout_still_unmet_after_a_bank_visit_stops_naming_the_item_and_never_loops():
    verdict, why = sp.bank_verdict(_loadout(NEED_FOOD), 8, {}, 40, visited_bank=True)
    assert verdict == sp.STOP and "Shark" in why


def test_too_little_room_to_loot_is_a_bank_trip_but_only_before_the_visit():
    assert sp.bank_verdict(_loadout(), free_slots=2, tasks_cfg={}, tablets_total=40)[0] == sp.BANK
    # Kalphite packs to 3 free slots (pinned in test_slayer_catalog): after the visit that is
    # simply the trip, not a reason to bank forever.
    assert sp.bank_verdict(_loadout(), 3, {}, 40, visited_bank=True)[0] == sp.GO


def test_a_missing_item_skips_a_skippable_task_and_stops_an_unskippable_one():
    missing = _loadout(problems=["rock hammer: no Rock hammer carried or banked"])
    verdict, why = sp.bank_verdict(missing, 8, {}, 40, skippable=True)
    assert verdict == sp.SKIP and "rock hammer" in why.lower()
    verdict, why = sp.bank_verdict(missing, 8, {}, 40, skippable=False)
    assert verdict == sp.STOP and "rock hammer" in why.lower()


def test_running_low_on_house_tablets_stops_with_what_to_buy():
    """The house is the travel hub; it never buys anything, so it says so and stops."""
    verdict, why = sp.bank_verdict(_loadout(), 8, {"minHouseTeleports": 5}, tablets_total=4)
    assert verdict == sp.STOP and "Teleport to house" in why and "4" in why and "5" in why
    assert sp.bank_verdict(_loadout(), 8, {}, tablets_total=4)[0] == sp.STOP, "the default floor is 5"
    assert sp.bank_verdict(_loadout(), 8, {"minHouseTeleports": 0}, 0)[0] == sp.GO


def test_an_unknown_tablet_count_is_not_zero():
    """bank never seen: nothing is known to be short, so nothing stops."""
    assert sp.bank_verdict(_loadout(), 8, {}, tablets_total=None)[0] == sp.GO


# --- D2: loot tiers ------------------------------------------------------------------------------

LIMITS = {"makeRoomValue": 50_000, "mustLootValue": 150_000}


def _loot(value, **kw):
    args = dict(stackable_held=False, free_slots=0, hp_missing=0, food="Shark", food_held=3,
                one_dose_potion=None, droppable=None, tasks_cfg=LIMITS)
    args.update(kw)
    return sp.loot_decision(value, **args)


def test_a_free_slot_or_a_stack_already_held_just_takes_it():
    assert _loot(100, free_slots=1) == (sp.TAKE, None)
    assert _loot(100, stackable_held=True) == (sp.TAKE, None)


def test_a_cheap_drop_into_a_full_inventory_is_left():
    assert _loot(49_999, hp_missing=30) == (sp.LEAVE, None)


def test_a_make_room_drop_eats_only_when_hp_is_actually_missing():
    assert _loot(60_000, hp_missing=25) == (sp.EAT_THEN_TAKE, "Shark")
    assert _loot(60_000, hp_missing=0) == (sp.LEAVE, None), "never waste a food at full HP for 60k"
    assert _loot(60_000, hp_missing=25, food_held=0) == (sp.LEAVE, None)


def test_a_must_loot_drop_is_always_taken_eat_then_sip_then_drop():
    assert _loot(200_000, hp_missing=0) == (sp.EAT_THEN_TAKE, "Shark"), "at full HP too"
    assert _loot(200_000, food_held=0, one_dose_potion="Prayer potion(1)") == (sp.DRINK_THEN_TAKE, "Prayer potion(1)")
    assert _loot(200_000, food_held=0, droppable="Antipoison(2)") == (sp.DROP_THEN_TAKE, "Antipoison(2)")
    assert _loot(200_000, food_held=0) == (sp.LEAVE, None), "nothing safe to give up"


def test_the_thresholds_default_to_the_plans_numbers():
    assert sp.loot_decision(60_000, False, 0, 25, "Shark", 3, None, None, tasks_cfg=None) == (sp.EAT_THEN_TAKE, "Shark")
    assert sp.loot_decision(149_999, False, 0, 0, "Shark", 3, None, None, tasks_cfg={}) == (sp.LEAVE, None)


def test_the_cheapest_consumable_is_never_gear_a_teleport_or_a_required_item():
    inventory = [{"name": "Shark", "qty": 1}, {"name": "Prayer potion(3)", "qty": 1},
                 {"name": "Antipoison(2)", "qty": 1}, {"name": "Slayer ring (4)", "qty": 1},
                 {"name": "Rock hammer", "qty": 1}, {"name": "Abyssal whip", "qty": 1},
                 {"name": "Teleport to house", "qty": 3}]
    prices = {"shark": 800, "prayer potion(3)": 7000, "antipoison(2)": 300, "slayer ring (4)": 1,
              "rock hammer": 1, "abyssal whip": 1_500_000, "teleport to house": 1}
    pick = sp.cheapest_consumable(inventory, food="Shark", protected=("rock hammer", "slayer ring"),
                                  value_of=lambda name: prices.get(name.lower()))
    assert pick == "Antipoison(2)"
    # A required antipoison (kalphite) is protected too: then the shark is the cheapest left.
    pick = sp.cheapest_consumable(inventory, "Shark", ("rock hammer", "slayer ring", "antipoison"),
                                  lambda name: prices.get(name.lower()))
    assert pick == "Shark"
    assert sp.cheapest_consumable([{"name": "Abyssal whip", "qty": 1}], "Shark", (), lambda n: 1) is None


def test_one_dose_potion_finds_a_prayer_potion_on_its_last_sip():
    inv = [{"name": "Prayer potion(4)"}, {"name": "Prayer potion(1)"}, {"name": "Shark"}]
    assert sp.one_dose_potion(inv, "Prayer potion") == "Prayer potion(1)"
    assert sp.one_dose_potion([{"name": "Prayer potion(2)"}], "Prayer potion") is None
    assert sp.one_dose_potion(inv, None) is None


# --- C3: base gear -------------------------------------------------------------------------------

# The shape of a REAL GET /equipment (2026-09-20): a dict keyed by slot.
WORN = {"weapon": {"qty": 1, "name": "Arclight", "id": 19675},
        "shield": {"qty": 1, "name": "Dragon defender", "id": 12954},
        "helmet": {"qty": 1, "name": "Slayer helmet (i)", "id": 26674}}


def test_base_gear_round_trips_through_a_file_keyed_by_account(tmp_path):
    path = sp.base_gear_path("8783289279948231245", root=str(tmp_path))
    assert path.endswith("8783289279948231245-base-gear.json")
    assert sp.save_base_gear(path, WORN) is True
    assert sp.load_base_gear(path) == {"weapon": "Arclight", "shield": "Dragon defender",
                                       "helmet": "Slayer helmet (i)"}
    assert json.load(open(path, encoding="utf-8"))["gear"]["weapon"] == "Arclight"


def test_no_account_hash_means_no_file_so_gear_is_never_saved_under_the_wrong_key(tmp_path):
    assert sp.base_gear_path(None, root=str(tmp_path)) is None
    assert sp.base_gear_path("-1", root=str(tmp_path)) is None
    assert sp.save_base_gear(None, WORN) is False
    assert sp.load_base_gear(None) is None
    assert sp.load_base_gear(str(tmp_path / "missing.json")) is None


def test_an_empty_equipment_read_is_never_saved_over_a_good_base(tmp_path):
    """/equipment answers {} when the bridge is down; that must not become 'the user wears nothing'."""
    path = sp.base_gear_path("1", root=str(tmp_path))
    sp.save_base_gear(path, WORN)
    assert sp.save_base_gear(path, {}) is False
    assert sp.load_base_gear(path)["weapon"] == "Arclight"


def test_the_base_is_only_captured_while_no_gear_task_is_active():
    class Spec:
        def __init__(self, worn):
            self.worn = worn
    assert sp.may_capture_base(has_task=False, spec=None)
    assert sp.may_capture_base(has_task=True, spec=Spec(()))
    assert not sp.may_capture_base(has_task=True, spec=Spec(("mirror shield",))), "a mirror shield is NOT base gear"


def test_gear_to_restore_is_the_slots_that_differ_from_the_base():
    base = {"weapon": "Arclight", "shield": "Dragon defender", "helmet": "Slayer helmet (i)"}
    now = dict(WORN, shield={"qty": 1, "name": "Mirror shield", "id": 4156})
    assert sp.gear_to_restore(base, now) == {"shield": "Dragon defender"}
    assert sp.gear_to_restore(base, WORN) == {}
    bare = {k: v for k, v in WORN.items() if k != "helmet"}
    assert sp.gear_to_restore(base, bare) == {"helmet": "Slayer helmet (i)"}
    assert sp.gear_to_restore(None, WORN) == {}, "no base known: restore nothing"


# --- the client has NOT been rebuilt yet (2026-09-21) -------------------------------------------

LIVE_TASKS = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10,
              "skipTasks": [], "maxSkips": 8, "pointsReserve": 60}


def test_every_new_setting_falls_back_to_its_default_on_an_un_rebuilt_client():
    """The verbatim `tasks` block GET /slayer-config answers today: the three settings added on
    2026-09-20 (minHouseTeleports, makeRoomValue, mustLootValue) are NOT there until the client is
    rebuilt. Absent must mean the documented default, never zero -- a zero make-room value would
    have the routine eat a shark for a 1gp bone."""
    assert "makeRoomValue" not in LIVE_TASKS and "minHouseTeleports" not in LIVE_TASKS
    assert sp.bank_verdict(_loadout(), 8, LIVE_TASKS, tablets_total=4)[0] == sp.STOP, "the floor is 5"
    assert sp.bank_verdict(_loadout(), 8, LIVE_TASKS, tablets_total=9)[0] == sp.GO
    assert sp.loot_decision(1, False, 0, 30, "Shark", 3, None, None, tasks_cfg=LIVE_TASKS) == (sp.LEAVE, None)
    assert sp.loot_decision(60_000, False, 0, 30, "Shark", 3, None, None, tasks_cfg=LIVE_TASKS)[0] == sp.EAT_THEN_TAKE
    assert sp.loot_decision(200_000, False, 0, 0, "Shark", 3, None, None, tasks_cfg=LIVE_TASKS)[0] == sp.EAT_THEN_TAKE
