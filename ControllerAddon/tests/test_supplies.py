import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import supplies


def test_charges_reads_the_trailing_count_in_both_spacings():
    assert supplies.charges("Slayer ring (8)") == 8
    assert supplies.charges("Ring of dueling(3)") == 3
    assert supplies.charges("Games necklace(1)") == 1
    assert supplies.charges("Slayer ring (eternal)") is None, "eternal has no charges to count"
    assert supplies.charges("Ectophial") is None
    assert supplies.charges("Prayer potion(4)") == 4
    assert supplies.charges(None) is None


def test_base_name_strips_the_suffix_and_case():
    assert supplies.base_name("Slayer ring (8)") == "slayer ring"
    assert supplies.base_name("Ring of dueling(3)") == "ring of dueling"
    assert supplies.base_name("Slayer ring (eternal)") == "slayer ring (eternal)"


def test_teleports_left_sums_inventory_and_bank():
    banked = [("Slayer ring (8)", 2), ("Slayer ring (3)", 1), ("Shark", 100)]
    assert supplies.teleports_left("slayer ring", ["Slayer ring (2)"], banked) == 2 + 16 + 3


def test_an_unseen_bank_is_unknown_not_zero():
    assert supplies.teleports_left("slayer ring", [], None) is None
    assert supplies.teleports_left("slayer ring", ["Slayer ring (2)"], None) == 2, "what we carry is still known"
    assert supplies.teleports_left("slayer ring", [], []) == 0, "a SEEN bank with none is a real zero"


def test_an_unlimited_copy_answers_unlimited():
    assert supplies.teleports_left("slayer ring", ["Slayer ring (eternal)"], []) == supplies.UNLIMITED
    assert supplies.teleports_left("slayer ring", [], [("Slayer ring (eternal)", 1)]) == supplies.UNLIMITED
    assert supplies.teleports_left("ectophial", ["Ectophial"], [], unlimited=True) == supplies.UNLIMITED


def test_an_uncharged_copy_is_zero_teleports_not_unlimited():
    """An uncharged `Amulet of glory` has no count either. Calling it unlimited would have the
    routine skip its restock and then fail to teleport out."""
    assert supplies.teleports_left("amulet of glory", ["Amulet of glory"], []) == 0
    assert supplies.teleports_left("amulet of glory", ["Amulet of glory"], [("Amulet of glory(4)", 2)]) == 8
    assert supplies.teleports_left("amulet of glory", ["Amulet of glory"], None) is None


def test_a_banked_quantity_of_zero_counts_for_nothing():
    assert supplies.teleports_left("slayer ring", [], [("Slayer ring (8)", 0)]) == 0


def test_matching_is_by_whole_name_not_by_prefix():
    names = ["Prayer book", "Ring of wealth (5)", "Prayer potion(3)", "Ring of dueling(2)"]
    assert supplies.doses_carried(names, "prayer") == 0
    assert supplies.doses_carried(names, "prayer potion") == 3
    assert supplies.lowest_charge(names, "ring") is None
    assert supplies.lowest_charge(names, "ring of dueling") == "Ring of dueling(2)"


def test_lowest_charge_is_used_first_so_partly_spent_rings_get_finished():
    names = ["Slayer ring (8)", "Shark", "Slayer ring (2)", "Slayer ring (5)"]
    assert supplies.lowest_charge(names, "slayer ring") == "Slayer ring (2)"
    assert supplies.lowest_charge(["Shark"], "slayer ring") is None


def test_the_users_two_anchor_figures_are_the_profiles():
    """USER 2026-09-19: prayer-tanked tasks want 5-6 full prayer potions; nechryael need at
    least 15 food because death spawns hit through prayer."""
    assert supplies.PROFILES["PRAYER"].prayer_potions == 6
    assert supplies.PROFILES["PRAYER_PLUS_DAMAGE"].food == 15
    assert supplies.PROFILES["PRAYER_PLUS_DAMAGE"].prayer_potions == 6
    assert supplies.PROFILES["TRIVIAL"] == supplies.Supplies(food=3, prayer_potions=0)
    assert supplies.PROFILES["SAFESPOT"] == supplies.Supplies(food=8, prayer_potions=2)


def test_prayer_doses_follow_the_drain_rate_model():
    """Slayer Best in Bank's PotionDoseEstimator (BSD-2): drain = duration * pointsPerMinute /
    resistance, resistance = 60 + 2*prayerBonus, a full bar is credited first, a dose restores
    floor(level/4) + 7 (8 for super restore). Hand-computed:
      20 kills * 18s = 360s; 360 * 58 / 60 = 348 drained; minus level 70 = 278; / (17 + 7 = 24) = 11.58 -> 12."""
    assert supplies.prayer_doses_needed(20, prayer_level=70) == 12
    # +30 prayer bonus doubles resistance: 360*58/120 = 174; -70 = 104; /24 = 4.33 -> 5
    assert supplies.prayer_doses_needed(20, prayer_level=70, prayer_bonus=30) == 5
    # no protection prayer: 38 points/min -> 360*38/60 = 228; -70 = 158; /24 = 6.58 -> 7
    assert supplies.prayer_doses_needed(20, prayer_level=70, protection=False) == 7
    # super restore restores one more per dose: 278 / 25 = 11.12 -> 12
    assert supplies.prayer_doses_needed(20, prayer_level=70, super_restore=True) == 12
    assert supplies.prayer_doses_needed(1, prayer_level=99) == 1, "clamped to at least one dose"
    assert supplies.prayer_doses_needed(200, prayer_level=70) == 32, "clamped to one trip's worth"


def test_potion_count_never_drops_below_the_users_floor_and_is_capped():
    prayer = supplies.PROFILES["PRAYER"]
    assert supplies.prayer_potions_for(prayer, doses_needed=5) == 6, "the user's floor wins over a low estimate"
    assert supplies.prayer_potions_for(prayer, doses_needed=32) == 8, "32 doses = 8 potions, the cap"
    assert supplies.prayer_potions_for(prayer, doses_needed=26) == 7
    assert supplies.prayer_potions_for(supplies.PROFILES["TRIVIAL"], doses_needed=20) == 0, "a no-prayer task stays at zero"


def test_doses_carried_counts_doses_not_potions():
    names = ["Prayer potion(4)", "Prayer potion(4)", "Prayer potion(2)", "Super restore(3)", "Shark"]
    assert supplies.doses_carried(names, "prayer potion") == 10
    assert supplies.doses_carried(names, "super restore") == 3


def test_packing_is_checked_against_28_slots():
    heavy = supplies.PROFILES["PRAYER_PLUS_DAMAGE"]
    assert supplies.slots_needed(heavy, 6, carry_count=0, displaced_gear=0, teleport_items=3) == 15 + 6 + 3
    assert supplies.fits(heavy, 6, carry_count=0, displaced_gear=0, teleport_items=3)
    assert not supplies.fits(heavy, 8, carry_count=3, displaced_gear=2, teleport_items=3), "15+8+3+2+3 = 31"
