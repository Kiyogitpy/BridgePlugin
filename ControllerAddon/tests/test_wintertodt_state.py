"""ControllerAddon/wintertodt_state.py -- the pure decision layer.

Pure means these tests need no client, no bridge and no serial port: every function takes plain
data and returns plain data. This is where the first live session's corrections will land, so
each fact is pinned to its source in the spec.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wintertodt_state as ws


def reader(**over):
    """A /api/minigames/wintertodt snapshot with everything nominal, overridable per test."""
    base = {
        "atWintertodt": True,
        "hudVisible": True,
        "energyVisible": True,
        "warmthVisible": True,
        "brazierStatusVisible": True,
        "roundActive": True,
        "points": 120,
        "energyPercent": 80,
        "warmth": 1000,
        "respawnDelay": 0,
        "secondsUntilRoundStart": 0,
        "rewardPool": 3,
        "warningNeeded": False,
        "areaAttackActive": False,
        "braziers": {
            key: {"brazier": key, "status": "LIT", "pyromancerStatus": "ALIVE"}
            for key in ws.BRAZIER_KEYS
        },
    }
    braziers = over.pop("braziers", None)
    base.update(over)
    if braziers:
        for key, entry in braziers.items():
            base["braziers"][key] = {"brazier": key, **entry}
    return base


def test_warmth_is_a_0_to_1000_scale_not_a_percentage():
    assert ws.warmth_percent(reader(warmth=1000)) == 100.0
    assert ws.warmth_percent(reader(warmth=455)) == 45.5


def test_warmth_percent_of_an_unreadable_snapshot_is_none_not_zero():
    # Zero would read as "about to die" and fire the emergency rung against a bridge blip.
    assert ws.warmth_percent({}) is None
    assert ws.warmth_percent(None) is None


def test_operational_is_recomputed_in_python_because_gson_omits_the_java_method():
    assert ws.brazier_operational({"status": "LIT", "pyromancerStatus": "ALIVE"}) is True
    assert ws.brazier_operational({"status": "LIT", "pyromancerStatus": "INCAPACITATED"}) is False
    assert ws.brazier_operational({"status": "UNLIT", "pyromancerStatus": "ALIVE"}) is False
    assert ws.brazier_operational(None) is False


def test_choose_brazier_sticks_with_the_one_we_are_already_working():
    snap = reader()
    assert ws.choose_brazier(snap, current="NORTH_EAST") == "NORTH_EAST"


def test_choose_brazier_leaves_a_brazier_whose_pyromancer_is_down():
    # A downed pyromancer means the brazier CANNOT be relit (wiki), and healing is out of scope,
    # so staying put would idle us for the rest of the round.
    snap = reader(braziers={"NORTH_EAST": {"status": "UNLIT",
                                           "pyromancerStatus": "INCAPACITATED"}})
    assert ws.choose_brazier(snap, current="NORTH_EAST") != "NORTH_EAST"


def test_choose_brazier_falls_back_to_the_preferred_one_when_none_is_operational():
    snap = reader(braziers={key: {"status": "BROKEN", "pyromancerStatus": "ALIVE"}
                            for key in ws.BRAZIER_KEYS})
    assert ws.choose_brazier(snap, current=None, preferred="SOUTH_WEST") == "SOUTH_WEST"


def test_choose_brazier_returns_none_when_the_hud_says_nothing():
    assert ws.choose_brazier({"braziers": {}}, current=None) is None


def test_round_active_is_the_readers_own_field_not_our_guess():
    assert ws.round_active(reader(roundActive=True)) is True
    assert ws.round_active(reader(roundActive=False)) is False
    assert ws.round_active({}) is False


def test_choose_brazier_keeps_current_when_unlit_with_alive_pyromancer():
    # Branch 2 positive case: current is down but fixable (we can light it).
    snap = reader(braziers={
        "NORTH_EAST": {"status": "UNLIT", "pyromancerStatus": "ALIVE"},
        "SOUTH_WEST": {"status": "LIT", "pyromancerStatus": "ALIVE"}
    })
    assert ws.choose_brazier(snap, current="NORTH_EAST") == "NORTH_EAST"


def test_choose_brazier_prefers_non_first_preferred_over_first_key():
    # Test hygiene: preferred should be respected even when it's not SOUTH_WEST.
    snap = reader(braziers={key: {"status": "BROKEN", "pyromancerStatus": "ALIVE"}
                            for key in ws.BRAZIER_KEYS})
    assert ws.choose_brazier(snap, current=None, preferred="NORTH_EAST") == "NORTH_EAST"


def test_choose_brazier_regression_prefers_alive_over_preferred_incapacitated():
    # Finding 1 regression: three incapacitated (including preferred), one alive.
    snap = reader(braziers={
        "SOUTH_WEST": {"status": "UNLIT", "pyromancerStatus": "INCAPACITATED"},
        "NORTH_WEST": {"status": "UNLIT", "pyromancerStatus": "INCAPACITATED"},
        "SOUTH_EAST": {"status": "UNLIT", "pyromancerStatus": "INCAPACITATED"},
        "NORTH_EAST": {"status": "BROKEN", "pyromancerStatus": "ALIVE"}
    })
    # Must return NORTH_EAST (the alive one), not SOUTH_WEST (preferred but incapacitated).
    assert ws.choose_brazier(snap, current=None, preferred="SOUTH_WEST") == "NORTH_EAST"


def test_reward_eligible_uses_the_threshold():
    assert ws.reward_eligible(reader(points=500)) is True
    assert ws.reward_eligible(reader(points=501)) is True
    assert ws.reward_eligible(reader(points=499)) is False
    assert ws.reward_eligible(reader(points=0)) is False
    assert ws.reward_eligible({}) is False


def inv(*names):
    """An inventory of {'id','name'} dicts. Dose counts ride in the name, as the game shows."""
    out = []
    for n in names:
        if n == "root":
            out.append({"id": ws.w.ITEM_ROOT, "name": "Bruma root"})
        elif n == "kindling":
            out.append({"id": ws.w.ITEM_KINDLING, "name": "Bruma kindling"})
        else:
            out.append({"id": 0, "name": n})
    return out


def test_kit_deficit_names_every_missing_tool():
    assert set(ws.kit_deficit(inv(), min_doses=0)) == {"axe", "knife", "tinderbox", "hammer"}
    full = inv("Bronze axe", "Knife", "Tinderbox", "Hammer")
    assert ws.kit_deficit(full, min_doses=0) == []


def test_potion_doses_counts_doses_not_potions():
    items = inv("Rejuvenation potion(4)", "Rejuvenation potion(2)")
    assert ws.potion_doses(items) == 6


def test_an_undosed_potion_name_counts_as_one_dose():
    assert ws.potion_doses(inv("Rejuvenation potion")) == 1


def test_kit_deficit_reports_potions_when_under_the_minimum():
    items = inv("Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(1)")
    assert ws.kit_deficit(items, min_doses=4) == ["potion"]
    assert ws.kit_deficit(items, min_doses=1) == []


def test_needs_drink_is_false_when_warmth_cannot_be_read():
    # Unreadable must never fire the emergency rung -- see warmth_percent's None contract.
    assert ws.needs_drink({}, drink_at=45) is False
    assert ws.needs_drink(reader(warmth=400), drink_at=45) is True
    assert ws.needs_drink(reader(warmth=600), drink_at=45) is False


def test_feed_item_prefers_kindling():
    items = inv("root", "root", "kindling")
    assert ws.feed_item(items, fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_KINDLING


def test_feed_item_accepts_roots_when_fletching_is_off():
    # THE DEADLOCK GUARD. With fletch=False nothing else will ever spend a root: chopping is
    # blocked by the full inventory and the fletch rung is disabled. feed and fletch must
    # between them accept every state a full inventory can be in.
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=False, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_ROOT


def test_feed_item_accepts_roots_near_the_round_end():
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=10,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_ROOT


def test_feed_item_holds_roots_back_mid_round_when_fletching():
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None


def test_feed_item_returns_none_with_nothing_feedable():
    assert ws.feed_item(inv("Knife"), fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None


# --- Finding A: unfinished potions must be excluded ---


def test_potion_doses_excludes_unfinished_potions():
    # Unfinished potions are crafting ingredients, not drinkable doses.
    # This matters: counting them would make kit_deficit report warmth satisfied when the routine
    # has nothing to drink, and running out of warmth forfeits the entire round's points.
    assert ws.potion_doses(inv("Rejuvenation potion (unf)")) == 0
    # Mixed inventory: only the finished potion counts as a dose.
    items = inv("Rejuvenation potion(4)", "Rejuvenation potion (unf)")
    assert ws.potion_doses(items) == 4


# --- Finding B: energy_percent=None or negative must not trigger near_end ---


def test_feed_item_returns_none_when_energy_percent_is_none():
    # Between rounds the HUD is down and energy_percent is None or omitted.
    # This must NOT satisfy the "near the end" condition, or roots would be fed unfletched
    # mid-round and lose more than half the points per root.
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=None,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None


def test_feed_item_returns_none_when_energy_percent_is_negative():
    # HUD reports -1 for energy when down.
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=-1,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None


# --- Finding C: boundary for feed_roots_below_energy is inclusive ---


def test_feed_item_boundary_at_feed_roots_below_energy():
    # With feed_roots_below_energy=15:
    # - energy_percent=15 (AT the boundary) should feed roots
    # - energy_percent=16 (just above) should NOT feed roots
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=15,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_ROOT
    assert ws.feed_item(items, fletch=True, energy_percent=16,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None


# --- Finding D: direct tests for named interfaces ---


def test_count_item_sums_occurrences():
    items = inv("root", "root", "kindling", "Knife")
    assert ws.count_item(items, ws.w.ITEM_ROOT) == 2
    assert ws.count_item(items, ws.w.ITEM_KINDLING) == 1
    assert ws.count_item(items, 0) == 1  # the Knife has id 0
    assert ws.count_item(items, 999) == 0  # not present


def test_has_tool_uses_substring_match():
    # Deliberately loose: "axe" matches any item with "axe" in the name.
    # This is intentional because any axe chops bruma roots, and the arena
    # crate hands out a Bronze axe whose exact name is not knowable offline.
    items = inv("Dragon axe", "Battleaxe", "Bronze axe")
    assert ws.has_tool(items, "axe") is True
    assert ws.has_tool(items, "dragon") is True
    assert ws.has_tool(items, "battle") is True
    assert ws.has_tool(items, "bronze") is True
    assert ws.has_tool(items, "spade") is False


def test_inventory_full_checks_capacity():
    # Inventory is full at size 28 (the default).
    assert ws.inventory_full(inv(*(["root"] * 27))) is False
    assert ws.inventory_full(inv(*(["root"] * 28))) is True
    assert ws.inventory_full(inv(*(["root"] * 29))) is True
    # Empty inventory is not full.
    assert ws.inventory_full(inv()) is False
    # Can use a custom size.
    assert ws.inventory_full(inv("Knife", "Axe"), size=2) is True
    assert ws.inventory_full(inv("Knife"), size=2) is False


CFG = {
    "preferredBrazier": "SOUTH_WEST",
    "drinkAtWarmth": 45,
    "minDoses": 4,
    "takeConcoctions": 10,
    "fletch": True,
    "feedRootsNearRoundEnd": True,
    "feedRootsBelowEnergy": 15,
    "dodgeSnowfall": True,
    "roundEndReads": 3,
}

KIT = ["Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(4)"]


def phase(**over):
    args = {
        "reader": over.pop("reader", reader()),
        "items": over.pop("items", inv(*KIT)),
        "config": over.pop("config", CFG),
        "brazier": over.pop("brazier", "SOUTH_WEST"),
        "snow_on_tile": over.pop("snow_on_tile", False),
        "round_over_streak": over.pop("round_over_streak", 0),
    }
    args.update(over)
    return ws.derive_phase(**args)


def test_outside_the_arena_is_a_stop_because_this_routine_owns_no_travel():
    # Sustained: this call supplies the streak already past the threshold, since a single
    # unconfirmed read is now a transient no-op -- see the two tests below.
    assert phase(reader=reader(atWintertodt=False), outside_streak=5) == "stop"


def test_a_single_unreadable_read_does_not_stop_the_run():
    """Fix round 2 (Important 5). `_reader()` collapses BOTH a bridge blip and a genuine
    "not at Wintertodt" read to the same falsy/`atWintertodt: False` shape, so a single failed
    HTTP call must not be a terminal stop. Pinned to "chop" specifically (not just != "stop"),
    since chop_phase touches neither `reader` nor the arena and is a safe no-op to ride out an
    unconfirmed read on."""
    assert phase(reader={}, outside_streak=0) == "chop"
    assert phase(reader=reader(atWintertodt=False), outside_streak=1) == "chop"


def test_the_outside_arena_streak_honours_its_own_config_threshold():
    cfg = dict(CFG, notAtArenaReads=2)
    assert phase(reader=reader(atWintertodt=False), outside_streak=1, config=cfg) == "chop"
    assert phase(reader=reader(atWintertodt=False), outside_streak=2, config=cfg) == "stop"


def test_warmth_outranks_a_full_inventory_and_a_dead_brazier():
    # 0% warmth is instant death and forfeits the whole round's points, so nothing outranks it
    # except restocking the potion it needs.
    cold = reader(warmth=200, braziers={"SOUTH_WEST": {"status": "UNLIT",
                                                       "pyromancerStatus": "ALIVE"}})
    assert phase(reader=cold, items=inv(*KIT, *(["root"] * 23))) == "warmth"


def test_kit_outranks_warmth_when_there_is_no_potion_to_drink():
    cold = reader(warmth=200)
    assert phase(reader=cold, items=inv("Bronze axe", "Knife", "Tinderbox", "Hammer")) == "kit"


def test_kit_does_not_outrank_warmth_when_a_drinkable_dose_is_still_held():
    """Fix round 2 (Critical 2a). Holding any dose at all is not an emergency -- kit only
    preempts warmth when there is genuinely nothing left to drink. MEASURED live: warmth fell
    740->220 across 13 ticks with THREE drinkable doses held, because minDoses(4 in this test's
    CFG) == DOSES_PER_POTION(4) meant every single drink dropped the held count below the
    threshold and re-armed the pre-fix condition (`not cold or "potion" in deficit`) --
    "potion" is in deficit here (3 < 4) and cold is True, so the pre-fix code returns "kit" and
    this test FAILS against it."""
    cold = reader(warmth=220)
    items = inv("Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(3)")
    assert phase(reader=cold, items=items, config=CFG) == "warmth"


def test_kit_does_not_spin_the_potion_restock_when_the_inventory_has_no_room():
    """Fix round 2 (Critical 2c). Not cold, potion-only deficit, inventory full of roots --
    Take-concoction is guaranteed refused with no free slot, so this must defer to fletch
    (which frees a slot) rather than spinning. This would FAIL against the pre-fix condition
    (`deficit and (not cold or "potion" in deficit)`), which returns "kit" here regardless of
    room: deficit=["potion"] and not cold is True."""
    items = inv("Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(1)",
                *(["root"] * 23))
    assert len(items) == 28
    assert phase(items=items, config=CFG) == "fletch"


def test_dodge_beats_working_but_only_when_enabled():
    assert phase(snow_on_tile=True) == "dodge"
    cfg = dict(CFG, dodgeSnowfall=False)
    assert phase(snow_on_tile=True, config=cfg) != "dodge"


def test_an_unlit_brazier_is_relit_even_mid_inventory():
    # Wiki: "The brazier should be relit immediately any time it is extinguished while you are
    # nearby ... even if it interrupts cutting or fletching."
    snap = reader(braziers={"SOUTH_WEST": {"status": "UNLIT", "pyromancerStatus": "ALIVE"}})
    assert phase(reader=snap, items=inv(*KIT, *(["root"] * 23))) == "light"


def test_an_unlit_brazier_with_a_downed_pyromancer_is_not_relightable():
    snap = reader(braziers={"SOUTH_WEST": {"status": "UNLIT",
                                           "pyromancerStatus": "INCAPACITATED"}})
    # Pinned to the exact value, not just != "light" -- a regression that returned some
    # other wrong phase would otherwise slip through unnoticed.
    assert phase(reader=snap, items=inv(*KIT)) == "chop"


def test_a_broken_brazier_is_fixed():
    snap = reader(braziers={"SOUTH_WEST": {"status": "BROKEN", "pyromancerStatus": "ALIVE"}})
    assert phase(reader=snap) == "fix"


def test_holding_kindling_feeds():
    assert phase(items=inv(*KIT, "kindling")) == "feed"


def test_a_full_inventory_of_roots_fletches():
    assert phase(items=inv(*KIT, *(["root"] * 23))) == "fletch"


def test_a_full_inventory_of_roots_feeds_when_fletching_is_off():
    # The deadlock guard again, this time through the ladder rather than feed_item alone.
    cfg = dict(CFG, fletch=False)
    assert phase(items=inv(*KIT, *(["root"] * 23)), config=cfg) == "feed"


def test_an_empty_inventory_chops():
    assert phase(items=inv(*KIT)) == "chop"


def test_the_round_end_test_is_sustained_not_a_single_read():
    # The transitional-snapshot family: a teleport/HUD blip reads exactly like a round end for
    # a tick or two. GOTR paid for this six separate times.
    over = reader(roundActive=False, points=-1, energyPercent=-1)
    # Pinned to the exact value, not just != "between_rounds" -- a regression that returned
    # some other wrong phase would otherwise slip through unnoticed.
    assert phase(reader=over, round_over_streak=1) == "chop"
    assert phase(reader=over, round_over_streak=3) == "between_rounds"


def test_derive_phase_tolerates_a_none_config():
    # This module's whole point is being a pure layer callable independently of any
    # particular caller, so it should not assume the caller normalises config for it.
    assert phase(config=None) == "chop"


def test_cold_with_a_potion_in_hand_but_missing_a_tool_still_goes_to_warmth():
    # The compound kit-vs-warmth condition, tested in the other direction: an urgent drink
    # must not be delayed to fetch an axe when a potion is already in hand.
    cold = reader(warmth=200)
    items = inv("Knife", "Tinderbox", "Hammer", "Rejuvenation potion(4)")  # no axe
    assert phase(reader=cold, items=items) == "warmth"
