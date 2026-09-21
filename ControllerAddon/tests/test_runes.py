"""Combination runes satisfy the elements they are made of.

FOUND LIVE 2026-09-21, one command before the first live run: the account carries 11,477 DUST
runes and zero plain air runes, and `npc_contact.missing_requirements` answered "NPC Contact needs
2 air rune(s) per cast (have 0)". A dust rune IS an air rune and an earth rune, so the cast would
have worked and the routine refused to make it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import runes


def test_a_dust_rune_counts_as_an_air_rune():
    """The exact live case. Dust = air + earth, and the account has nothing else that casts."""
    carried = {"dust rune": 11477, "astral rune": 1571, "cosmic rune": 8845}

    assert runes.available(carried, "air rune") == 11477
    assert runes.available(carried, "earth rune") == 11477
    assert runes.available(carried, "water rune") == 0, "dust is not water"
    assert runes.available(carried, "fire rune") == 0


def test_every_combination_rune_feeds_both_of_its_elements():
    """The six combinations, each pinned. Getting one backwards would refuse a cast the account
    can afford, which is exactly the failure this module exists to prevent."""
    for combo, elements in (("mist rune", ("air", "water")), ("dust rune", ("air", "earth")),
                            ("mud rune", ("water", "earth")), ("smoke rune", ("air", "fire")),
                            ("steam rune", ("water", "fire")), ("lava rune", ("earth", "fire"))):
        carried = {combo: 50}
        for element in ("air", "water", "earth", "fire"):
            expected = 50 if element in elements else 0
            assert runes.available(carried, f"{element} rune") == expected, (combo, element)


def test_plain_and_combination_runes_add_up():
    carried = {"air rune": 30, "dust rune": 12, "smoke rune": 5}
    assert runes.available(carried, "air rune") == 47


def test_a_non_elemental_rune_is_only_ever_itself():
    """Astral, cosmic, law, nature. Nothing combines into these, so a lookup must not invent one."""
    carried = {"astral rune": 1571, "dust rune": 11477}
    assert runes.available(carried, "astral rune") == 1571
    assert runes.available(carried, "law rune") == 0


def test_counting_is_case_insensitive_and_survives_an_unknown_name():
    assert runes.available({"Dust Rune": 8}, "Air rune") == 8
    assert runes.available({"banana": 3}, "air rune") == 0
    assert runes.available(None, "air rune") == 0
