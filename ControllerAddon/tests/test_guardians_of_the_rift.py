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
