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


# --- choose_action ---------------------------------------------------------

CFG = dict(gotr.DEFAULT_CONFIG)


def test_action_inside_altar_always_altar_trip():
    st = make_state(currentAltar="AIR",
                    inventory=make_inv(elementalGuardianStones=5))
    assert gotr.choose_action(st, CFG) == "altar_trip"


def test_action_inside_huge_pocket_continues_portal_trip():
    st = make_state(miningArea="WEST_HUGE_REMAINS")
    assert gotr.choose_action(st, CFG) == "portal_trip"


def test_action_repair_beats_everything_outside():
    st = make_state(barriers=[barrier("BROKEN_REPAIRABLE")],
                    guardianEssencePortalOpen=True,
                    guardianEssencePortalSecondsRemaining=20,
                    inventory=make_inv(guardianFragments=12,
                                       elementalGuardianStones=3))
    assert gotr.choose_action(st, CFG) == "repair_barrier"


def test_action_repair_needs_12_fragments():
    st = make_state(barriers=[barrier("BROKEN_REPAIRABLE")],
                    inventory=make_inv(guardianFragments=11))
    assert gotr.choose_action(st, CFG) == "mine"


def test_action_portal_when_open_with_room():
    st = make_state(guardianEssencePortalOpen=True,
                    guardianEssencePortalSecondsRemaining=20)
    assert gotr.choose_action(st, CFG) == "portal_trip"


def test_action_portal_skipped_when_closing_or_full():
    st = make_state(guardianEssencePortalOpen=True,
                    guardianEssencePortalSecondsRemaining=3)
    assert gotr.choose_action(st, CFG) == "mine"
    st = make_state(guardianEssencePortalOpen=True,
                    guardianEssencePortalSecondsRemaining=20,
                    inventory=make_inv(emptySlots=0))
    assert gotr.choose_action(st, CFG, pouch_space=0) == "mine"


def test_action_stones_then_cell():
    st = make_state(inventory=make_inv(catalyticGuardianStones=2,
                                       chargedCellTier="STRONG"))
    assert gotr.choose_action(st, CFG) == "spend_stones"
    st = make_state(barriers=[barrier("INACTIVE")],
                    inventory=make_inv(chargedCellTier="STRONG"))
    assert gotr.choose_action(st, CFG) == "place_cell"


def test_action_altar_at_threshold_or_full_pouches():
    st = make_state(inventory=make_inv(guardianEssence=20))
    assert gotr.choose_action(st, CFG) == "altar_trip"
    st = make_state(inventory=make_inv(guardianEssence=3))
    assert gotr.choose_action(st, CFG, pouch_space=0) == "altar_trip"
    assert gotr.choose_action(st, CFG, pouch_space=5) == "mine"
    assert gotr.choose_action(st, CFG, pouch_space=None) == "mine"


def test_action_craft_at_fragment_threshold():
    st = make_state(inventory=make_inv(guardianFragments=60))
    assert gotr.choose_action(st, CFG) == "craft"
    st = make_state(inventory=make_inv(guardianFragments=59))
    assert gotr.choose_action(st, CFG) == "mine"


def test_action_disabled_rungs_are_skipped():
    st = make_state(barriers=[barrier("BROKEN_REPAIRABLE"), barrier("INACTIVE")],
                    inventory=make_inv(guardianFragments=12, chargedCellTier="WEAK"))
    assert gotr.choose_action(st, CFG,
                              disabled={"repair_barrier", "place_cell"}) == "mine"
    st = make_state(inventory=make_inv(guardianEssence=25))
    assert gotr.choose_action(st, CFG, disabled={"altar_trip"}) == "mine"


# --- _reset_counters -------------------------------------------------------

def test_reset_counters_enter_and_contact():
    data = {"gotr_enter_clicks": 3, "gotr_contact_casts": 2, "gotr_contact_stuck": 1}
    gotr._reset_counters(data, make_state(), "active")
    assert data["gotr_enter_clicks"] == 0
    assert data["gotr_contact_casts"] == 0
    assert data["gotr_contact_stuck"] == 0


def test_reset_counters_mine_delta():
    data = {"gotr_mine_stall": 7, "gotr_last_frags": 5, "gotr_last_mined": 5,
            "gotr_last_essence": 0}
    st = make_state(minedFragmentsThisGame=6,
                    inventory=make_inv(guardianFragments=6))
    gotr._reset_counters(data, st, "active")
    assert data["gotr_mine_stall"] == 0
    assert data["gotr_mine_delta_at"] > 0


def test_reset_counters_craft_delta_on_fragments_down():
    data = {"gotr_craft_stall": 5, "gotr_last_frags": 20, "gotr_last_mined": 20,
            "gotr_last_essence": 0}
    st = make_state(minedFragmentsThisGame=20,
                    inventory=make_inv(guardianFragments=10, guardianEssence=10))
    gotr._reset_counters(data, st, "active")
    assert data["gotr_craft_stall"] == 0


def test_reset_counters_stones_and_cell():
    data = {"gotr_powerup_clicks": 3, "gotr_imbue_clicks": 4, "gotr_last_stones": 5,
            "gotr_last_cell": "STRONG", "gotr_cell_clicks": 2}
    st = make_state(inventory=make_inv())   # stones now 0, cell now NONE
    gotr._reset_counters(data, st, "active")
    assert data["gotr_powerup_clicks"] == 0
    assert data["gotr_cell_clicks"] == 0


def test_reset_counters_imbue_on_stone_gain():
    data = {"gotr_imbue_clicks": 4, "gotr_last_stones": 0}
    st = make_state(inventory=make_inv(elementalGuardianStones=5))
    gotr._reset_counters(data, st, "active")
    assert data["gotr_imbue_clicks"] == 0


def test_reset_counters_altar_change():
    data = {"gotr_altar_enters": 3, "gotr_exit_clicks": 2, "gotr_imbue_clicks": 1,
            "gotr_last_altar": "UNKNOWN"}
    gotr._reset_counters(data, make_state(currentAltar="AIR"), "active")
    assert data["gotr_altar_enters"] == 0
    assert data["gotr_exit_clicks"] == 0


def test_reset_counters_repair_when_no_broken_tile():
    data = {"gotr_repair_clicks": 4}
    gotr._reset_counters(data, make_state(barriers=[barrier("WEAK")]), "active")
    assert data["gotr_repair_clicks"] == 0


def test_reset_counters_portal():
    data = {"gotr_portal_enters": 3, "gotr_portal_ticks": 10}
    gotr._reset_counters(data, make_state(miningArea="WEST_HUGE_REMAINS"), "active")
    assert data["gotr_portal_enters"] == 0
    data = {"gotr_portal_ticks": 10}
    gotr._reset_counters(data, make_state(miningArea="NONE"), "active")
    assert data["gotr_portal_ticks"] == 0


def test_action_handlers_cover_every_ladder_result():
    for name in ("repair_barrier", "portal_trip", "spend_stones", "place_cell",
                 "altar_trip", "craft", "mine"):
        assert name in gotr.ACTION_HANDLERS
