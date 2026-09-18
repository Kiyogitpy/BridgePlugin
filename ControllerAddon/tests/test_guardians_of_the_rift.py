import os
import sys
import time

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
    # Truly outside the temple the HUD is gone: power -1 (measured in the lobby
    # pre-round and outside; a live power with inMainTemple false is a SIDE AREA).
    assert gotr.round_state(make_state(inMainTemple=False,
                                       guardianPower=-1)) == "outside"


# --- side areas (MEASURED live 2026-09-01: altar rooms and the portal mine are
# separate map regions, so inMainTemple reads false inside them mid-round) ----

def test_side_area_altar_room():
    # Fire altar, region 10315: currentAltar set, inMainTemple false, power live.
    st = make_state(inMainTemple=False, inArena=False, currentAltar="FIRE",
                    guardianPower=66)
    assert gotr.side_area(st) == "altar"
    assert gotr.round_state(st) == "active"


def test_side_area_portal_pocket():
    # Huge-remains mine, region 10827: no altar, miningArea NONE (region-gated),
    # but the HUD still serves -- power >= 0 is the discriminator.
    st = make_state(inMainTemple=False, inArena=False, guardianPower=87,
                    miningArea="NONE")
    assert gotr.side_area(st) == "pocket"
    assert gotr.round_state(st) == "active"
    assert gotr.choose_action(st, dict(gotr.DEFAULT_CONFIG)) == "portal_trip"


def test_side_area_none_when_in_temple_or_hud_dead():
    assert gotr.side_area(make_state()) is None
    assert gotr.side_area(make_state(inMainTemple=False, guardianPower=-1)) is None


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


def test_choose_cell_tile_recharge_beats_placing():
    # Recharging a live barrier pays 2-15 energy; placing on an empty tile pays 2 (wiki
    # "Gaining energy") -- a live tile wins over an INACTIVE one (USER: "dump our cell").
    tiles = [barrier("INACTIVE", 1, 1), barrier("WEAK", 2, 2)]
    assert gotr.choose_cell_tile(tiles)["state"] == "WEAK"
    assert gotr.choose_cell_tile([barrier("INACTIVE", 1, 1)])["state"] == "INACTIVE"


def test_choose_cell_tile_recharges_lowest_live_tier():
    tiles = [barrier("STRONG"), barrier("MEDIUM"), barrier("OVERCHARGED")]
    assert gotr.choose_cell_tile(tiles)["state"] == "MEDIUM"


def test_choose_cell_tile_ignores_broken_and_noop():
    tiles = [barrier("BROKEN"), barrier("BROKEN_REPAIRABLE"), barrier("INACTIVE_NO_OP")]
    assert gotr.choose_cell_tile(tiles) is None
    assert gotr.choose_cell_tile([]) is None
    assert gotr.choose_cell_tile(None) is None


# --- choose_altar ----------------------------------------------------------

def test_choose_altar_prefers_higher_tier():
    # USER 2026-09-01: "prefer altars that give more experience" -- the higher-tier
    # altar wins while the energy gap is under ENERGY_BALANCE_GAP (CHAOS 35 > AIR 1).
    # USER 2026-09-02 revised: past the gap the lagging type balances (see
    # test_choose_altar_balances_past_the_gap).
    st = make_state(elementalEnergy=50, catalyticEnergy=20)
    assert gotr.choose_altar(st, 99) == "CHAOS"
    st = make_state(elementalEnergy=20, catalyticEnergy=50)
    assert gotr.choose_altar(st, 99) == "CHAOS"
    st = make_state(elementalAltar="FIRE", catalyticAltar="MIND",
                    elementalEnergy=30, catalyticEnergy=0)
    assert gotr.choose_altar(st, 99) == "FIRE"    # FIRE 14 > MIND 2


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


def pstate(stored, capacity, in_inv=True, repair=False, max_capacity=None):
    return {"storedEssence": stored, "capacity": capacity,
            "maximumCapacity": capacity if max_capacity is None else max_capacity,
            "repairNeeded": repair, "inInventory": in_inv}


def test_pouches_degraded():
    assert gotr.pouches_degraded(None) is False
    assert gotr.pouches_degraded({}) is False
    # Degraded = capacity actually LOST, never the repairNeeded flag: MEASURED live
    # 2026-09-01, pouches at full capacity read repairNeeded=true (the flag tracks
    # decay toward the next break) and the routine repaired healthy pouches.
    api = pouch_api(SMALL=pstate(0, 3), GIANT=pstate(0, 6, max_capacity=9))
    assert gotr.pouches_degraded(api) is True
    api = pouch_api(SMALL=pstate(0, 3), GIANT=pstate(0, 9, repair=True))
    assert gotr.pouches_degraded(api) is False    # the live false-positive, pinned
    api = pouch_api(GIANT=pstate(0, 6, in_inv=False, max_capacity=9))
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
                    inventory=make_inv(chargedCellTier="WEAK",
                                       elementalGuardianStones=3))
    assert gotr.choose_action(st, CFG) == "repair_barrier"


def test_action_repair_needs_charged_cell():
    # USER 2026-09-01: "repair can only be done with a charged cell" -- without one
    # the game omits the Repair option entirely (live tick 116, run 12).
    st = make_state(barriers=[barrier("BROKEN_REPAIRABLE")],
                    inventory=make_inv(guardianFragments=12))
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


def test_action_altar_only_when_maximally_loaded():
    # USER rotation: the trip goes out with bags AND inventory full (or nothing
    # left to craft) -- bags full alone kept crafting the inventory full first
    # ("filled up bag but didn't resume crafting until inventory full").
    st = make_state(inventory=make_inv(guardianEssence=3))
    assert gotr.choose_action(st, CFG, pouch_space=5) == "altar_trip"  # frags == 0
    st = make_state(inventory=make_inv(guardianEssence=3, guardianFragments=40))
    assert gotr.choose_action(st, CFG, pouch_space=5,
                              batch_done=True) == "craft"   # keep crafting first
    assert gotr.choose_action(st, CFG, pouch_space=0,
                              batch_done=True) == "craft"   # bags full, inv NOT full
    st = make_state(inventory=make_inv(guardianEssence=3, guardianFragments=40,
                                       emptySlots=0))
    assert gotr.choose_action(st, CFG, pouch_space=0) == "altar_trip"  # all full


def test_action_batch_latch():
    # Mine ONCE per game: with the latch set, fragments still craft below the
    # start threshold, and an empty inventory idles instead of re-mining.
    st = make_state(inventory=make_inv(guardianFragments=40))
    assert gotr.choose_action(st, CFG, pouch_space=5) == "mine"
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=True) == "craft"
    st = make_state(inventory=make_inv())
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=True) == "idle"
    assert gotr.choose_action(st, CFG, pouch_space=5) == "mine"


def test_action_craft_at_fragment_threshold():
    at = CFG["craft_at_fragments"]
    st = make_state(inventory=make_inv(guardianFragments=at))
    assert gotr.choose_action(st, CFG) == "craft"
    st = make_state(inventory=make_inv(guardianFragments=at - 1))
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


# --- _direct_walk: a candidate tile projected under a UI overlay is skipped ----
# MEASURED live 2026-09-02 (gotr_live39): the force-walk toward the rubble projected
# inside the minimap circle and the same pixel was clicked once a tick for two
# minutes with the player never moving.

class _Ctx:
    def __init__(self):
        self.ser = object()
        self.events = None
        self.state = type("S", (), {"data": {}, "config": dict(gotr.DEFAULT_CONFIG)})()


def test_direct_walk_skips_ui_blocked_candidates(monkeypatch):
    monkeypatch.setattr(gotr, "_await_moving", lambda c: True)   # walk sites only test the click
    import controller
    import travel
    walked = []
    hops = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3600, "y": 9500})
    # Every candidate projects on screen; only the full-distance one is under the minimap.
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane: {"onScreen": True, "screenX": x, "screenY": y})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui",
                        lambda x, y: {"x": 0} if x == 3620 else None)
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, wait_for_arrival=True: walked.append((x, y)) or True)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: hops.append((x, y)) or {"onMinimap": False})
    assert gotr._direct_walk(_Ctx(), 3620, 9500) is True
    assert walked == [(3612, 9500)]      # the 0.6 pull-in, not the blocked full tile
    assert hops == []


def test_direct_walk_falls_back_to_minimap_when_every_candidate_is_blocked(monkeypatch):
    monkeypatch.setattr(gotr, "_await_moving", lambda c: True)   # walk sites only test the click
    import controller
    import travel
    clicks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3600, "y": 9500})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane: {"onScreen": True, "screenX": x, "screenY": y})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui", lambda x, y: {"x": 0})
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not click")))
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": True, "screenX": 10, "screenY": 10})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("name")) or True)
    assert gotr._direct_walk(_Ctx(), 3620, 9500) is True
    assert clicks == ["minimap"]


# --- the ladder hold: a verified Enter click pins its rung (run 38: portal_trip
# clicked with 10s left, the timer read 7s next tick, place_cell cancelled the walk) --

def test_held_action_pins_until_expiry():
    data = {}
    gotr._hold_action(data, "portal_trip", 5.0)
    assert gotr._held_action(data, set()) == "portal_trip"
    assert gotr._held_action(data, {"portal_trip"}) is None      # disabled rungs win
    data["gotr_hold_until"] = 0
    assert gotr._held_action(data, set()) is None


def test_held_create_guardian_drops_when_the_shared_cap_fills():
    # First live run, 2026-09-18: a hold taken at guardians=6/10 rode the count up to 9/10
    # then 10/10 (activeGuardians is a SHARED count across the whole GOTR world) across three
    # ticks of the SAME held action -- every further Assemble click was then a guaranteed no-op
    # (someone else filled the last slot), burning all CELL_MAX_CLICKS into a "rift guardian
    # never created" safety stop instead of falling through to place_cell like the ladder itself
    # does once the cap is full (see test_action_guardian_before_cell_when_room elsewhere in this
    # file, which covers the ladder's own version of the same condition).
    data = {}
    st = make_state(activeGuardians=6, guardianLimit=10,
                    inventory=make_inv(chargedCellTier="STRONG"))
    gotr._hold_action(data, "create_guardian", 5.0)
    assert gotr._held_action(data, set(), st) == "create_guardian"
    st["activeGuardians"] = 10          # someone else's guardian filled the last slot
    assert gotr._held_action(data, set(), st) is None
    # A charged cell with room in the limit still holds normally.
    st["activeGuardians"] = 9
    assert gotr._held_action(data, set(), st) == "create_guardian"


# --- NPC Contact must never act on the wrong NPC's dialogue (USER 2026-09-18, watching live:
# "the astral contact spell kept contacting kuradell instead of the correct one") ---

def test_dialogue_wrong_npc_detects_a_different_speaker():
    assert gotr._dialogue_wrong_npc({"npcName": "Kuradal"}) is True
    assert gotr._dialogue_wrong_npc({"npcName": "Dark Mage"}) is False
    assert gotr._dialogue_wrong_npc({"npcName": "the Dark Mage"}) is False   # substring match
    # Not yet rendered this tick is NOT evidence of the wrong NPC -- see the function's own note.
    assert gotr._dialogue_wrong_npc({"npcName": ""}) is False
    assert gotr._dialogue_wrong_npc({}) is False


def test_contact_resolving_window():
    data = {}
    assert gotr._contact_resolving(data) is False
    data["gotr_contact_cast_at"] = time.time()
    assert gotr._contact_resolving(data) is True
    data["gotr_contact_cast_at"] = time.time() - gotr.CONTACT_RESOLVE_S - 1
    assert gotr._contact_resolving(data) is False


def test_suppress_stray_dialog_check_self_heals_once_the_wrong_dialogue_closes(monkeypatch):
    # The engine's generic check_stray_dialog must stay off for as long as a confirmed-wrong-NPC
    # dialogue is still open (else it would race back in and blind-advance a stranger's dialogue
    # the moment _contact_resolving's window lapses) but must NOT stay off for the rest of the
    # session once it actually closes -- _repair_pouches is not guaranteed to run again soon.
    data = {"gotr_wrong_npc_dialogue": True}
    monkeypatch.setattr(gotr, "_dialogue", lambda: {"npcName": "Kuradal", "npcText": "hello"})
    assert gotr._suppress_stray_dialog_check(data) is True
    assert data["gotr_wrong_npc_dialogue"] is True
    monkeypatch.setattr(gotr, "_dialogue", lambda: {})
    assert gotr._suppress_stray_dialog_check(data) is False
    assert data["gotr_wrong_npc_dialogue"] is False


def test_action_cell_does_not_leave_the_pocket_before_the_batch():
    # Run 44: a WEAK cell carried over into the new round outranked mining at power 10.
    st = make_state(miningArea="EAST_LARGE_REMAINS",
                    inventory=make_inv(chargedCellTier="WEAK", unchargedCells=0,
                                       guardianFragments=30))
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=False) == "mine"
    # Once the batch is done (or outside the pocket) the cell rung is back.
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=True) == "place_cell"
    st["miningArea"] = "NONE"
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=False) == "place_cell"


def test_choose_altar_balances_past_the_gap():
    # USER 2026-09-02: keep the tier priority, but balance when one type runs away.
    st = make_state(elementalAltar="AIR", catalyticAltar="DEATH",
                    elementalEnergy=25, catalyticEnergy=209)
    assert gotr.choose_altar(st, 99) == "AIR"          # lagging type wins past the gap
    st.update(elementalEnergy=180, catalyticEnergy=209)
    assert gotr.choose_altar(st, 99) == "DEATH"        # within the gap: higher tier
    st.update(elementalEnergy=25, catalyticEnergy=209, elementalAltar="BLOOD",
              catalyticAltar="MIND")
    assert gotr.choose_altar(st, 99) == "BLOOD"        # lagging AND higher tier
    st.update(elementalAltar="AIR", catalyticAltar="DEATH", elementalEnergy=209,
              catalyticEnergy=25)
    assert gotr.choose_altar(st, 99) == "DEATH"        # catalytic lagging
    # A lagging altar we cannot enter falls back to the tier rule.
    assert gotr.choose_altar(st, 99, blocked=("DEATH",)) == "AIR"


# --- the career point carry (2026-09-19) -----------------------------------
# Reward points are two pools and a Rewards Guardian search costs ONE OF EACH (wiki
# "Rewards"), so the quantity to balance is the banked tally, not this game's energy --
# which resets to 0/0 every game. Ground truth for the shape: DatBear's GOTR Helper
# balances on `banked + current/100`.

def test_effective_energies_without_a_banked_tally_is_the_old_behaviour():
    st = make_state(elementalEnergy=40, catalyticEnergy=90)
    assert gotr.effective_energies(st, None) == (40, 90)
    assert gotr.effective_energies(st, ()) == (40, 90)


def test_effective_energies_carries_the_banked_difference_at_100_per_point():
    st = make_state(elementalEnergy=40, catalyticEnergy=90)
    # Banked elemental lead of 2 points = 200 energy, applied to the elemental side.
    assert gotr.effective_energies(st, (7, 5)) == (240, 90)
    # Banked catalytic lead of 2 points goes on the catalytic side, both stay >= 0.
    assert gotr.effective_energies(st, (5, 7)) == (40, 290)
    # A balanced tally changes nothing.
    assert gotr.effective_energies(st, (5, 5)) == (40, 90)


def test_effective_energies_clamps_the_carry():
    # The clamp bounds a bad parse; it cannot change a decision, because every consumer
    # is a sign/threshold test on the difference.
    st = make_state(elementalEnergy=0, catalyticEnergy=0)
    assert gotr.effective_energies(st, (900, 0)) == (gotr.CARRY_MAX_ENERGY, 0)
    assert gotr.effective_energies(st, (0, 900)) == (0, gotr.CARRY_MAX_ENERGY)


def test_choose_altar_balances_against_the_banked_tally():
    # The exact case tonight's logs show going wrong: this game is dead level, so the
    # old rule takes the higher tier (DEATH, catalytic) every time -- and because the
    # tier table is lopsided (elemental tops out at FIRE 14, seven catalytic altars sit
    # above it) that is what drifts the hoard catalytic, game after game.
    st = make_state(elementalAltar="AIR", catalyticAltar="DEATH",
                    elementalEnergy=0, catalyticEnergy=0)
    assert gotr.choose_altar(st, 99) == "DEATH"
    # With a banked catalytic lead the lagging elemental altar wins instead.
    assert gotr.choose_altar(st, 99, banked=(50, 56)) == "AIR"
    # A banked ELEMENTAL lead sends it back to the catalytic altar -- which here is also
    # the higher tier, so this only proves the carry does not fight the tier rule.
    assert gotr.choose_altar(st, 99, banked=(56, 50)) == "DEATH"
    # A balanced tally reproduces the tier rule exactly.
    assert gotr.choose_altar(st, 99, banked=(50, 50)) == "DEATH"
    # The carry does not override the level/quest gates.
    assert gotr.choose_altar(st, 99, banked=(50, 56), blocked=("AIR",)) == "DEATH"


def test_choose_altar_carry_and_this_games_energy_combine():
    # A banked deficit is repaid across the game, not in one trip: once this game has
    # already put 200 energy into the lagging type, the carry is settled and the tier
    # rule resumes.
    st = make_state(elementalAltar="AIR", catalyticAltar="DEATH",
                    elementalEnergy=0, catalyticEnergy=0)
    assert gotr.choose_altar(st, 99, banked=(50, 52)) == "AIR"
    st.update(elementalEnergy=200)
    assert gotr.choose_altar(st, 99, banked=(50, 52)) == "DEATH"


def test_choose_guardian_pile_follows_the_banked_tally():
    # A rift guardian is the single largest one-sided payout (2x+1 = 6/15/27/45), so it
    # steers on the same tally.
    st = make_state(elementalEnergy=0, catalyticEnergy=0)
    assert gotr.choose_guardian_pile(st, (50, 56)) == gotr.ESSENCE_PILE_ELEMENTAL_ID
    assert gotr.choose_guardian_pile(st, (56, 50)) == gotr.ESSENCE_PILE_CATALYTIC_ID
    # Unknown tally keeps the per-game rule.
    st.update(elementalEnergy=90, catalyticEnergy=10)
    assert gotr.choose_guardian_pile(st) == gotr.ESSENCE_PILE_CATALYTIC_ID
    # ...and a banked elemental deficit outweighs a small in-game elemental lead.
    assert gotr.choose_guardian_pile(st, (50, 52)) == gotr.ESSENCE_PILE_ELEMENTAL_ID


def test_parse_reward_summary_reads_the_end_of_game_message():
    # Regex shape copied from DatBear's REWARD_POINT_PATTERN, with the colour markup
    # made optional so a tag-stripped CHAT_MESSAGE payload parses too.
    marked = ("You have earned 4 elemental energy and 3 catalytic energy. "
              "Total elemental energy: <col=ff0000>1,234</col>, "
              "Total catalytic energy: <col=ff0000>1,200</col>.")
    assert gotr.parse_reward_summary(marked) == (1234, 1200)
    stripped = ("Total elemental energy: 57, Total catalytic energy: 66.")
    assert gotr.parse_reward_summary(stripped) == (57, 66)


def test_parse_reward_summary_ignores_everything_else():
    assert gotr.parse_reward_summary("") is None
    assert gotr.parse_reward_summary(None) is None
    assert gotr.parse_reward_summary("The rift will become active in 30 seconds.") is None
    # A half-message (only one of the two totals) must not half-latch.
    assert gotr.parse_reward_summary("Total elemental energy: 57.") is None


def test_direct_walk_escalates_to_minimap_when_the_player_does_not_move(monkeypatch):
    # Run 46: the chatbox scrollbar swallowed the same walk click 30 times in a row.
    import controller
    import travel
    walked, hops = [], []
    gotr._walk_memo.update(target=None, player=None, stuck=0)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3600, "y": 9500})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane: {"onScreen": True, "screenX": x, "screenY": y})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui", lambda x, y: None)
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, wait_for_arrival=True: walked.append((x, y)) or True)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": True, "screenX": 5, "screenY": 5})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: hops.append(ent.get("name")) or True)
    ctx = _Ctx()
    monkeypatch.setattr(gotr, "_await_moving", lambda c: True)   # walk sites only test the click
    assert gotr._direct_walk(ctx, 3620, 9500)   # first click
    assert gotr._direct_walk(ctx, 3620, 9500)   # same target, not moved: stuck 1
    assert hops == [] and len(walked) == 2
    assert gotr._direct_walk(ctx, 3620, 9500)   # stuck 2 -> minimap instead of the pixel
    assert hops == ["minimap"] and len(walked) == 2


def test_stuck_watch_warns_then_stops_without_progress(monkeypatch):
    import controller
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2122, "y": 4833})
    stops = []
    ctx = _Ctx()
    monkeypatch.setattr(gotr, "_await_moving", lambda c: True)   # walk sites only test the click
    ctx.state.trigger_safety_stop = lambda reason: stops.append(reason)
    st = make_state(currentAltar="COSMIC")
    for _ in range(gotr.STUCK_STOP_TICKS):
        assert gotr._stuck_watch(ctx, st, "altar_trip") is False
    assert gotr._stuck_watch(ctx, st, "altar_trip") is True
    assert "no progress" in stops[0]
    # Any progress signal resets it: here the inventory changed.
    st["inventory"] = make_inv(guardianEssence=5)
    assert gotr._stuck_watch(ctx, st, "altar_trip") is False
    assert ctx.state.data["gotr_stuck_ticks"] == 0
    # The idle rung never counts (it legitimately stands still for a minute).
    for _ in range(gotr.STUCK_STOP_TICKS + 1):
        assert gotr._stuck_watch(ctx, st, "idle") is False


def test_before_break_plays_the_round_out_then_returns(monkeypatch):
    # Run 50: a break that began mid-round inside an altar room cost the round AND a
    # full extra game at the barrier. before_break ticks until the round is over.
    import controller
    # One transitional "idle" read (an altar-entry teleport) must NOT end the wait.
    transitional = make_state(guardianPower=-1, elementalAltar="UNKNOWN",
                              catalyticAltar="UNKNOWN")
    reads = iter([make_state(guardianPower=50), transitional, make_state(guardianPower=70)]
                 + [make_state(guardianPower=100, finishedGame=True)] * 20)
    monkeypatch.setattr(gotr, "read_state", lambda: next(reads))
    ticks = []
    monkeypatch.setattr(gotr, "tick", lambda ctx: ticks.append(1))
    monkeypatch.setattr(gotr.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "get_objects", lambda **k: [])
    ctx = _Ctx()
    monkeypatch.setattr(gotr, "_await_moving", lambda c: True)   # walk sites only test the click
    ctx.state.safety_stopped = False
    gotr.before_break(ctx)
    # 3 ticks while active/transitional, then BREAK_INACTIVE_READS-1 more before it
    # accepts the round as over (each non-terminal read still ticks once).
    assert len(ticks) == 3 + gotr.BREAK_INACTIVE_READS - 1


def test_before_break_is_quick_between_rounds(monkeypatch):
    import controller
    monkeypatch.setattr(gotr, "read_state", lambda: make_state(guardianPower=0))
    ticks = []
    monkeypatch.setattr(gotr, "tick", lambda ctx: ticks.append(1))
    monkeypatch.setattr(gotr.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "get_objects", lambda **k: [])
    ctx = _Ctx()
    monkeypatch.setattr(gotr, "_await_moving", lambda c: True)   # walk sites only test the click
    ctx.state.safety_stopped = False
    gotr.before_break(ctx)
    assert len(ticks) < gotr.BREAK_INACTIVE_READS


def test_choose_hop_world_prefers_listed_order_and_skips_current():
    rows = [{"world": 301}, {"world": 478}, {"world": 459}, {"world": 534}]
    assert gotr.choose_hop_world(rows, 301) == 459
    assert gotr.choose_hop_world(rows, 459) == 478
    assert gotr.choose_hop_world([{"world": 301}], 301) is None


def test_row_visible_uses_list_bounds():
    b = {"x": 0, "y": 100, "width": 200, "height": 300}
    assert gotr._row_visible({"screenY": 250}, b)
    assert not gotr._row_visible({"screenY": 50}, b)
    assert not gotr._row_visible({"screenY": 450}, b)


def test_world_rows_ignore_the_player_count_column():
    rows = [{"world": 459, "bounds": {"x": 2236}}, {"world": 409, "bounds": {"x": 2117}},
            {"world": 459, "bounds": {"x": 2117}}]
    assert [r["bounds"]["x"] for r in gotr._world_rows(rows)] == [2117, 2117]
    assert gotr._row_for(rows, 459)["bounds"]["x"] == 2117
    assert gotr.choose_hop_world(rows, 465) == 409


def test_choose_cell_tile_strengthen_then_recharge_then_place():
    tiles = [barrier("STRONG", 1, 1), barrier("MEDIUM", 2, 2), barrier("OVERCHARGED", 3, 3),
             barrier("INACTIVE", 4, 4)]
    assert gotr.choose_cell_tile(tiles, "STRONG")["state"] == "MEDIUM"       # strengthen
    assert gotr.choose_cell_tile(tiles, "WEAK")["state"] == "MEDIUM"         # recharge lowest
    assert gotr.choose_cell_tile(tiles, "MEDIUM", skip={(2, 2)})["state"] == "STRONG"
    assert gotr.choose_cell_tile([barrier("INACTIVE", 4, 4)], "WEAK")["state"] == "INACTIVE"
    assert gotr.choose_cell_tile([], "WEAK") is None


def test_action_guardian_before_cell_when_room(monkeypatch):
    st = make_state(activeGuardians=4, guardianLimit=10,
                    inventory=make_inv(chargedCellTier="STRONG", chiselPresent=True))
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=True) == "create_guardian"
    st["activeGuardians"] = 10
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=True) == "place_cell"
    st["activeGuardians"] = -1     # HUD cannot say: never assemble blind
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=True) == "place_cell"
    st["activeGuardians"] = 2
    assert gotr.choose_action(st, CFG, pouch_space=5, batch_done=True,
                              disabled={"create_guardian"}) == "place_cell"


def test_choose_guardian_pile_follows_the_lagging_energy():
    assert gotr.choose_guardian_pile(make_state(elementalEnergy=10, catalyticEnergy=90)) == gotr.ESSENCE_PILE_ELEMENTAL_ID
    assert gotr.choose_guardian_pile(make_state(elementalEnergy=90, catalyticEnergy=10)) == gotr.ESSENCE_PILE_CATALYTIC_ID


# --- event-driven outcomes (2026-09-04) ------------------------------------------------

import bridge_events
import controller
import travel


class _ECtx:
    """_Ctx plus the fields the outcome path touches: ctx.events and the safety flag."""
    def __init__(self, events=None):
        self.ser = object()
        self.events = events
        self.state = type("S", (), {"data": {}, "config": dict(gotr.DEFAULT_CONFIG),
                                    "safety_stopped": False})()
        self.state.trigger_safety_stop = lambda reason: setattr(self.state, "safety_stopped", True)


class _FakeEvents:
    """Stand-in EventStream: mark() counts, await_outcome returns a canned Outcome."""
    def __init__(self, outcome):
        self.outcome = outcome
        self.marks = 0
        self.calls = []

    def mark(self):
        self.marks += 1
        return self.marks

    def await_outcome(self, signals, budget_s, confirm=None, since=None, reaction=True,
                      shorten=None, now_fn=None, sleep_fn=None):
        self.calls.append({"signals": signals, "budget": budget_s, "since": since,
                           "shorten": shorten, "confirm": confirm, "reaction": reaction})
        return self.outcome


def test_await_marks_hit_and_clears_the_commit(monkeypatch):
    ctx = _ECtx(_FakeEvents(bridge_events.Outcome(True, "INVENTORY_CHANGED", {}, True, 0.4)))
    ctx.state.data["gotr_click_mark"] = 7
    res = gotr._await(ctx, "gotr_x_commit", 5.0, {"INVENTORY_CHANGED": None}, lambda: True)
    assert res and ctx.state.data["gotr_outcome_hit"] is True
    assert ctx.state.data["gotr_x_commit"] == 0.0
    assert ctx.events.calls[0]["since"] == 7 and "gotr_click_mark" not in ctx.state.data


def test_await_without_a_hit_leaves_no_chain_flag(monkeypatch):
    ctx = _ECtx(_FakeEvents(bridge_events.Outcome(False, None, None, None, 5.0)))
    assert not gotr._await(ctx, "gotr_x_commit", 5.0, "INVENTORY_CHANGED", lambda: None)
    assert not ctx.state.data.get("gotr_outcome_hit")


def test_click_escalating_takes_a_mark_before_the_click(monkeypatch):
    ctx = _ECtx(_FakeEvents(None))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    assert gotr._click_escalating(ctx, {"x": 1, "y": 2, "id": 5}, ("Mine",)) is True
    assert ctx.state.data["gotr_click_mark"] == 1


def test_tick_chains_while_outcomes_land_and_stops_at_the_cap(monkeypatch):
    ctx = _ECtx()
    runs = []

    def fake_once(c):
        runs.append(1)
        c.state.data["gotr_outcome_hit"] = True
    monkeypatch.setattr(gotr, "_tick_once", fake_once)
    gotr.tick(ctx)
    assert len(runs) == gotr.CHAIN_MAX_ACTIONS


def test_tick_does_not_chain_without_an_outcome(monkeypatch):
    ctx = _ECtx()
    runs = []
    monkeypatch.setattr(gotr, "_tick_once", lambda c: runs.append(1))
    gotr.tick(ctx)
    assert runs == [1]


def test_tick_chain_respects_the_time_cap(monkeypatch):
    ctx = _ECtx()
    clock = [0.0]
    monkeypatch.setattr(gotr.time, "time", lambda: clock[0])
    runs = []

    def fake_once(c):
        runs.append(1)
        c.state.data["gotr_outcome_hit"] = True
        clock[0] += gotr.CHAIN_MAX_S
    monkeypatch.setattr(gotr, "_tick_once", fake_once)
    gotr.tick(ctx)
    assert len(runs) == 1


def test_varbit_subscriptions_cover_the_round_and_energy_signals():
    assert {13691, 13688, 13685, 13686, 13687, 5667} <= set(gotr.VARBIT_SUBSCRIPTIONS)


def _outcome_events(hit=True):
    return _FakeEvents(bridge_events.Outcome(hit, "INVENTORY_CHANGED" if hit else None, {}, hit, 0.3))


def test_approach_or_click_awaits_the_given_outcome(monkeypatch):
    ctx = _ECtx(_outcome_events())
    monkeypatch.setattr(gotr.travel, "can_reach", lambda e: True)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 1.0)
    confirm = lambda: True
    assert gotr._approach_or_click(ctx, {"x": 1, "y": 1}, ("Take",), "gotr_restock_clicks", 6,
                                   "never", signals="INVENTORY_CHANGED", confirm=confirm) is True
    call = ctx.events.calls[0]
    assert call["signals"] == "INVENTORY_CHANGED" and call["confirm"] is confirm
    assert call["budget"] == gotr.ACTION_COMMIT_S + 1.0
    assert call["shorten"] == {"PLAYER_STOPPED": gotr.ACTION_AFTER_STOP_S}
    assert ctx.state.data["gotr_outcome_hit"] is True


def test_approach_or_click_without_an_outcome_spec_keeps_the_legacy_commit(monkeypatch):
    ctx = _ECtx(_outcome_events())
    monkeypatch.setattr(gotr.travel, "can_reach", lambda e: True)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 0.0)
    assert gotr._approach_or_click(ctx, {"x": 1, "y": 1}, ("Take",), "gotr_k", 6, "never") is True
    assert ctx.events.calls == [] and ctx.state.data["gotr_k_commit"] > 0


def test_restock_confirm_is_more_uncharged_cells(monkeypatch):
    ctx = _ECtx(_outcome_events())
    st = make_state(inventory=make_inv(unchargedCells=3))
    monkeypatch.setattr(gotr, "_find_object_by_name", lambda *a, **k: {"x": 1, "y": 1, "name": "Uncharged cells"})
    monkeypatch.setattr(gotr.travel, "can_reach", lambda e: True)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 0.0)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(gotr, "_inv_now", lambda: make_inv(unchargedCells=8))
    gotr._restock_cells(ctx, st)
    assert ctx.events.calls[0]["confirm"]() is True
    monkeypatch.setattr(gotr, "_inv_now", lambda: make_inv(unchargedCells=3))
    assert not ctx.events.calls[0]["confirm"]()


def test_powerup_confirm_is_fewer_stones(monkeypatch):
    ctx = _ECtx(_outcome_events())
    st = make_state(inventory=make_inv(elementalGuardianStones=20))
    monkeypatch.setattr(gotr, "_find_npc_by_name", lambda s: {"index": 3, "name": "The Great Guardian"})
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "mark_busy", lambda s: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(gotr, "_inv_now", lambda: make_inv(elementalGuardianStones=0))
    gotr.spend_stones_phase(ctx, st)
    call = ctx.events.calls[0]
    assert "VARBIT_CHANGED" in call["signals"] and call["confirm"]() is True
    assert call["since"] == 1 and "gotr_click_mark" not in ctx.state.data


def test_confirm_mined_and_crafted_read_fresh_state(monkeypatch):
    monkeypatch.setattr(gotr, "read_state", lambda: make_state(
        inventory=make_inv(guardianFragments=40, guardianEssence=3), minedFragmentsThisGame=40))
    assert gotr._confirm_mined(30, 30) is True
    assert gotr._confirm_mined(40, 40) is False
    assert gotr._confirm_crafted(0, 45) is True        # essence up / fragments down
    assert gotr._confirm_crafted(3, 40) is False


def test_mine_signals_filter_the_mining_skill_and_count_varbit():
    xp = gotr.MINE_SIGNALS["XP_GAINED"]
    assert xp({"skill": "Mining"}) and not xp({"skill": "Runecraft"})
    vb = gotr.MINE_SIGNALS["VARBIT_CHANGED"]
    assert vb({"varbitId": gotr.VB_MINING_COUNT}) and not vb({"varbitId": 1})


def test_await_mine_stamps_the_delta_clock_on_a_hit(monkeypatch):
    ctx = _ECtx(_FakeEvents(bridge_events.Outcome(True, "XP_GAINED", {"skill": "Mining"}, True, 1.0)))
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 2.0)
    st = make_state(inventory=make_inv(guardianFragments=10))
    gotr._await_mine(ctx, {"x": 1, "y": 1}, st)
    assert ctx.state.data["gotr_mine_delta_at"] > 0
    assert ctx.events.calls[0]["budget"] == gotr.MINE_COMMIT_S + 2.0
    assert ctx.events.calls[0]["shorten"] == {"PLAYER_STOPPED": gotr.MINE_AFTER_STOP_S}


def test_enter_uses_the_menu_verdict_then_awaits_the_teleport(monkeypatch):
    verdict = bridge_events.Outcome(True, "MENU_CLICKED", {"option": "Enter", "target": "Portal"}, True, 0.1)
    tele = bridge_events.Outcome(True, "WIDGET_LOADED", {"groupId": 746}, True, 2.0)
    outcomes = iter([verdict, tele])

    class _Ev(_FakeEvents):
        def await_outcome(self, *a, **k):
            self.calls.append(k)
            return next(outcomes)
    ctx = _ECtx(_Ev(None))
    monkeypatch.setattr(controller, "get_last_action", lambda: None)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 1.0)
    assert gotr._enter(ctx, {"x": 1, "y": 1}, ("Enter",), "gotr_enter_commit", "test", lambda: True) is True
    assert ctx.events.calls[0]["reaction"] is False              # the verdict is not an action
    assert ctx.state.data["gotr_outcome_hit"] is True


def test_enter_gives_up_when_the_click_became_something_else(monkeypatch):
    ctx = _ECtx(_FakeEvents(bridge_events.Outcome(True, "MENU_CLICKED", {"option": "Walk here", "target": ""}, True, 0.1)))
    monkeypatch.setattr(controller, "get_last_action", lambda: None)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 0.0)
    assert gotr._enter(ctx, {"x": 1, "y": 1}, ("Enter",), "gotr_enter_commit", "test", lambda: True) is False
    assert len(ctx.events.calls) == 1


def test_hud_widget_predicate_and_teleport_confirms(monkeypatch):
    assert gotr._hud_widget({"groupId": 746}) and not gotr._hud_widget({"groupId": 162})
    monkeypatch.setattr(gotr, "read_state", lambda: make_state(currentAltar="AIR", inArena=False))
    assert gotr._confirm_in_altar() and not gotr._confirm_back_in_arena()
    monkeypatch.setattr(gotr, "read_state", lambda: make_state(miningArea="WEST_HUGE_REMAINS"))
    assert gotr._confirm_in_pocket()
    monkeypatch.setattr(gotr, "read_state", lambda: make_state())
    assert gotr._confirm_back_in_arena() and not gotr._confirm_in_pocket()


def test_await_moving_returns_at_once_when_already_moving(monkeypatch):
    ctx = _ECtx(_FakeEvents(None))
    monkeypatch.setattr(controller, "get_player", lambda: {"moving": True})
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    assert gotr._await_moving(ctx) is True
    assert ctx.events.calls == []


def test_await_moving_waits_for_player_moving_otherwise(monkeypatch):
    ctx = _ECtx(_FakeEvents(bridge_events.Outcome(True, "PLAYER_MOVING", {}, True, 0.2)))
    monkeypatch.setattr(controller, "get_player", lambda: {"moving": False})
    assert gotr._await_moving(ctx) is True
    assert ctx.events.calls[0]["signals"] == "PLAYER_MOVING"
    assert ctx.events.calls[0]["budget"] == gotr.WALK_START_BUDGET_S


def test_direct_walk_awaits_motion_after_its_click(monkeypatch):
    ctx = _ECtx()
    gotr._walk_memo.update({"target": None, "player": None, "stuck": 0})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3600, "y": 9500})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 500, "screenY": 400})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui", lambda x, y: None)
    monkeypatch.setattr(travel, "walk_to_exact_tile", lambda *a, **k: True)
    waited = []
    monkeypatch.setattr(gotr, "_await_moving", lambda c: waited.append(1) or True)
    assert gotr._direct_walk(ctx, 3610, 9500) is True
    assert waited == [1]
