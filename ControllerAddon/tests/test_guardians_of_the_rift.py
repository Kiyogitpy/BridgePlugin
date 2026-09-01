import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import guardians_of_the_rift as gotr


def make_inv(**over):
    inv = {"guardianFragments": 0, "guardianEssence": 0, "unchargedCells": 10,
           "chargedCellTier": "NONE", "catalyticGuardianStones": 0,
           "elementalGuardianStones": 0, "polyelementalGuardianStones": 0,
           "polycatalyticGuardianStones": 0, "chiselPresent": False, "emptySlots": 10}
    inv.update(over)
    return inv


def make_state(**over):
    st = {"hudVisible": True, "participating": True, "finishedGame": False,
          "banned": False, "guardianPower": 50, "altarRotationSecondsRemaining": 60,
          "elementalEnergy": 0, "catalyticEnergy": 0,
          "elementalAltar": "AIR", "catalyticAltar": "CHAOS",
          "guardianEssencePortalOpen": False,
          "guardianEssencePortalSecondsRemaining": -1,
          "inMainTemple": True, "inArena": True, "currentAltar": "UNKNOWN",
          "miningArea": "NONE", "minedFragmentsThisGame": 0,
          "inventory": make_inv(), "barriers": []}
    st.update(over)
    return st


def barrier(state, x=3615, y=9500):
    return {"location": {"x": x, "y": y, "plane": 0}, "state": state}


# --- round_state -----------------------------------------------------------

def test_round_state_unavailable():
    assert gotr.round_state(None) == "unavailable"
    assert gotr.round_state({"error": "boom"}) == "unavailable"


def test_round_state_outside():
    assert gotr.round_state(make_state(inMainTemple=False)) == "outside"


def test_round_state_lobby():
    assert gotr.round_state(make_state(inArena=False)) == "lobby"


def test_round_state_waiting():
    assert gotr.round_state(make_state(guardianPower=0)) == "waiting"


def test_round_state_active():
    assert gotr.round_state(make_state(guardianPower=50)) == "active"
    assert gotr.round_state(make_state(guardianPower=1)) == "active"
    assert gotr.round_state(make_state(guardianPower=99)) == "active"


def test_round_state_complete():
    assert gotr.round_state(make_state(finishedGame=True)) == "complete"
    assert gotr.round_state(make_state(guardianPower=100)) == "complete"


def test_round_state_idle_when_not_participating():
    assert gotr.round_state(make_state(participating=False, guardianPower=0)) == "idle"


def test_round_state_idle_on_unreadable_power():
    assert gotr.round_state(make_state(guardianPower=-1)) == "idle"


# --- stones / barriers -----------------------------------------------------

def test_total_stones_sums_all_four():
    inv = make_inv(catalyticGuardianStones=2, elementalGuardianStones=3,
                   polyelementalGuardianStones=1, polycatalyticGuardianStones=4)
    assert gotr.total_stones(inv) == 10
    assert gotr.total_stones(make_inv()) == 0


def test_find_repairable():
    assert gotr.find_repairable([]) is None
    assert gotr.find_repairable([barrier("BROKEN"), barrier("WEAK")]) is None
    hit = gotr.find_repairable([barrier("BROKEN"), barrier("BROKEN_REPAIRABLE")])
    assert hit is not None and hit["state"] == "BROKEN_REPAIRABLE"


def test_choose_cell_tile_prefers_inactive():
    tiles = [barrier("WEAK"), barrier("INACTIVE"), barrier("OVERCHARGED")]
    assert gotr.choose_cell_tile(tiles)["state"] == "INACTIVE"


def test_choose_cell_tile_recharges_lowest_live_tier():
    tiles = [barrier("STRONG"), barrier("MEDIUM"), barrier("OVERCHARGED")]
    assert gotr.choose_cell_tile(tiles)["state"] == "MEDIUM"


def test_choose_cell_tile_ignores_broken_and_noop():
    tiles = [barrier("BROKEN"), barrier("BROKEN_REPAIRABLE"), barrier("INACTIVE_NO_OP")]
    assert gotr.choose_cell_tile(tiles) is None
    assert gotr.choose_cell_tile([]) is None
    assert gotr.choose_cell_tile(None) is None


# --- choose_altar ----------------------------------------------------------

def test_choose_altar_prefers_lower_energy_type():
    st = make_state(elementalEnergy=100, catalyticEnergy=20)
    assert gotr.choose_altar(st, 99) == "CHAOS"
    st = make_state(elementalEnergy=20, catalyticEnergy=100)
    assert gotr.choose_altar(st, 99) == "AIR"


def test_choose_altar_ties_go_elemental():
    st = make_state(elementalEnergy=50, catalyticEnergy=50)
    assert gotr.choose_altar(st, 99) == "AIR"


def test_choose_altar_falls_back_when_level_gated():
    st = make_state(elementalAltar="AIR", catalyticAltar="BLOOD",
                    elementalEnergy=100, catalyticEnergy=0)
    # BLOOD needs 77; a level-60 account falls back to the elemental altar
    assert gotr.choose_altar(st, 60) == "AIR"


def test_choose_altar_falls_back_when_blocked():
    st = make_state(elementalAltar="AIR", catalyticAltar="COSMIC",
                    elementalEnergy=100, catalyticEnergy=0)
    assert gotr.choose_altar(st, 60, blocked=("COSMIC",)) == "AIR"


def test_choose_altar_none_when_nothing_usable():
    st = make_state(elementalAltar="UNKNOWN", catalyticAltar="BLOOD")
    assert gotr.choose_altar(st, 60) is None


def test_choose_altar_unknown_rc_level_does_not_gate():
    st = make_state(elementalAltar="FIRE", catalyticAltar="BLOOD",
                    elementalEnergy=100, catalyticEnergy=0)
    assert gotr.choose_altar(st, None) == "BLOOD"


# --- pouch readers ---------------------------------------------------------

def pouch_api(**pouches):
    return {"pouches": pouches}


def pstate(stored, capacity, in_inv=True, repair=False):
    return {"storedEssence": stored, "capacity": capacity, "maximumCapacity": capacity,
            "repairNeeded": repair, "inInventory": in_inv}


def test_pouches_degraded():
    assert gotr.pouches_degraded(None) is False
    assert gotr.pouches_degraded({}) is False
    api = pouch_api(SMALL=pstate(0, 3), GIANT=pstate(0, 9, repair=True))
    assert gotr.pouches_degraded(api) is True
    api = pouch_api(GIANT=pstate(0, 9, in_inv=False, repair=True))
    assert gotr.pouches_degraded(api) is False


def test_pouch_free_space():
    assert gotr.pouch_free_space(None) is None
    assert gotr.pouch_free_space({}) is None
    api = pouch_api(SMALL=pstate(1, 3), LARGE=pstate(0, 7),
                    COLOSSAL=pstate(0, 40, in_inv=False))
    assert gotr.pouch_free_space(api) == 9
    assert gotr.pouch_free_space(pouch_api(SMALL=pstate(0, 3, in_inv=False))) == 0


def test_pouch_stored():
    assert gotr.pouch_stored(None) == 0
    api = pouch_api(SMALL=pstate(3, 3), LARGE=pstate(5, 7),
                    GIANT=pstate(9, 9, in_inv=False))
    assert gotr.pouch_stored(api) == 8
