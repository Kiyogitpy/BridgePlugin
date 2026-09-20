"""slayer_loadout.build: a task plus what is carried and banked becomes a withdraw list.

PURE -- nothing here reads the bridge or clicks. Plan task C1
(docs/superpowers/plans/2026-09-19-slayer-travel-bank-loadout.md).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import slayer_loadout as sl


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


# The panel's own keys (ConfigExporter.java: food.name, potions.prayerPotion).
PANEL = {"food": {"enabled": True, "name": "Shark", "eatAt": 50},
         "potions": {"prayerEnabled": True, "prayerPotion": "Prayer potion"}}

# A bank that holds plenty of everything, name (lowercased) -> quantity.
BANK = {"shark": 500, "prayer potion(4)": 80, "prayer potion(3)": 2, "teleport to house": 40,
        "ring of dueling(8)": 6, "ring of dueling(3)": 1, "slayer ring (8)": 2, "slayer ring (2)": 1,
        "rock hammer": 1, "antipoison(4)": 9, "taverley teleport": 30, "ectophial": 1}


def _want(loadout):
    return {w["name"]: w["quantity"] for w in loadout.withdraw}


def test_the_users_nechryael_loadout_is_fifteen_food_and_six_potions():
    """USER 2026-09-19: 'if it's nechryaels there is unavoidable damage, we should bring at least
    15 food', and 5-6 full prayer potions for a task that leans on prayer."""
    lo = sl.build("Nechryael", 150, PANEL, prayer_level=70, prayer_bonus=10, carried={}, banked=BANK)
    assert lo.problems == []
    want = _want(lo)
    assert want["Shark"] == 15
    assert want["Prayer potion(4)"] >= 6
    assert lo.slots <= 28


def test_an_aya_task_is_three_food_and_no_potions():
    lo = sl.build("Cows", 20, PANEL, prayer_level=70, prayer_bonus=10, carried={}, banked=BANK)
    want = _want(lo)
    assert want["Shark"] == 3
    assert not any("potion" in name.lower() for name in want)
    assert want["Ectophial"] == 1, "Alice's farm is reached by Ectophial: the trip's one teleport item"
    assert lo.problems == []
    no_ecto = sl.build("Cows", 20, PANEL, 70, 10, {}, {k: v for k, v in BANK.items() if k != "ectophial"})
    assert any("ectophial" in p.lower() for p in no_ecto.problems), "a task we cannot reach says so"


def test_what_is_already_carried_is_not_withdrawn_again():
    carried = {"shark": 10, "prayer potion(4)": 6, "teleport to house": 3, "ring of dueling(5)": 1,
               "slayer ring (6)": 1}
    lo = sl.build("Nechryael", 150, PANEL, 70, 10, carried=carried, banked=BANK)
    want = _want(lo)
    assert want["Shark"] == 5, "15 wanted, 10 held"
    assert "Teleport to house" not in want and not any("dueling" in n.lower() for n in want)
    assert not any("slayer ring" in n.lower() for n in want)


def test_a_part_used_potion_counts_by_its_doses():
    """Six full potions is 24 doses. Holding five (4)s and two (2)s is 24 doses: nothing to fetch."""
    carried = {"prayer potion(4)": 5, "prayer potion(2)": 2}
    lo = sl.build("Black demons", 5, PANEL, 99, 30, carried=carried, banked=BANK)
    assert not any("prayer potion" in n.lower() for n in _want(lo))


def test_the_teleport_copy_taken_is_the_most_spent_one():
    """supplies.lowest_charge: finish a part-used ring off rather than strand it in the bank."""
    lo = sl.build("Nechryael", 150, PANEL, 70, 10, carried={}, banked=BANK)
    want = _want(lo)
    assert want.get("Slayer ring (2)") == 1 and "Slayer ring (8)" not in want
    assert want.get("Ring of dueling(3)") == 1


def test_what_a_task_needs_carried_is_on_the_list():
    assert _want(sl.build("Gargoyles", 150, PANEL, 70, 10, {}, BANK))["Rock hammer"] == 1
    kalphite = _want(sl.build("Kalphite", 150, PANEL, 70, 10, {}, BANK))
    assert kalphite["Antipoison(4)"] == 1


def test_an_item_the_bank_does_not_hold_is_a_named_problem_not_a_silent_gap():
    bank = dict(BANK)
    del bank["rock hammer"]
    lo = sl.build("Gargoyles", 150, PANEL, 70, 10, {}, bank)
    assert any("rock hammer" in p.lower() for p in lo.problems)
    assert "Rock hammer" not in _want(lo), "never ask the banker for what is not there"


def test_a_short_stack_is_a_problem_that_says_how_short():
    bank = dict(BANK, shark=4)
    lo = sl.build("Nechryael", 150, PANEL, 70, 10, {}, bank)
    assert any("shark" in p.lower() and "15" in p and "4" in p for p in lo.problems)


def test_a_bank_never_seen_is_not_an_empty_bank():
    """bank_memory: None means UNKNOWN. Nothing can be called missing, so there is no 'missing'
    problem -- the list is built on faith and `unchecked` says so."""
    lo = sl.build("Gargoyles", 150, PANEL, 70, 10, carried={}, banked=None)
    assert lo.problems == []
    assert lo.unchecked is True
    assert _want(lo)["Rock hammer"] == 1 and _want(lo)["Shark"] == 5
    seen = sl.build("Gargoyles", 150, PANEL, 70, 10, carried={}, banked=BANK)
    assert seen.unchecked is False


def test_no_food_or_potion_name_in_the_panel_is_a_problem():
    lo = sl.build("Nechryael", 150, {"food": {}, "potions": {}}, 70, 10, {}, BANK)
    assert any("food" in p.lower() for p in lo.problems)
    assert any("prayer potion" in p.lower() for p in lo.problems)


def test_a_pack_that_does_not_fit_is_refused():
    """Already-held junk counts: 12 unrelated items plus a 15-food, 6-potion trip cannot fit."""
    carried = {f"junk {i}": 1 for i in range(12)}
    lo = sl.build("Nechryael", 150, PANEL, 70, 10, carried=carried, banked=BANK, keep_carried=True)
    assert lo.slots > 28
    assert any("28" in p for p in lo.problems)


def test_keep_names_everything_the_trip_uses_so_the_banker_deposits_the_rest():
    lo = sl.build("Gargoyles", 150, PANEL, 70, 10, {}, BANK)
    keep = [k.lower() for k in lo.keep]
    for name in ("shark", "prayer potion", "rock hammer", "teleport to house", "ring of dueling", "slayer ring"):
        assert name in keep, name


def test_an_unknown_task_has_no_loadout():
    lo = sl.build("Dark beasts", 100, PANEL, 70, 10, {}, BANK)
    assert lo.withdraw == [] and any("no site" in p.lower() for p in lo.problems)


def test_the_bank_memory_adapter_keeps_unknown_unknown():
    class Mem:
        def __init__(self, seen, items):
            self.seen, self.items = seen, items
    assert sl.banked_from_memory(Mem(False, [])) is None
    assert sl.banked_from_memory(None) is None
    got = sl.banked_from_memory(Mem(True, [{"name": "Shark", "quantity": 5, "id": 385},
                                            {"name": "Shark", "quantity": 2, "id": 385}]))
    assert got == {"shark": 7}


# --- 2026-09-20: found by sweeping Aya's 24 tasks on the live account --------------------------

def test_ice_coolers_stack_so_thirty_five_of_them_are_one_slot():
    """Lizards need count + 5 ice coolers. They are STACKABLE; counting each as a slot said the
    pack needed 37 of 28 and refused a trivial task."""
    bank = dict(BANK, **{"ice cooler": 500})
    lo = sl.build("Lizards", 30, PANEL, 70, 10, {}, bank)
    assert _want(lo)["Ice cooler"] == 35
    assert lo.slots <= 10 and lo.problems == []


def test_a_teleport_the_house_serves_is_not_a_missing_item():
    """Scorpions list 'Skill's necklace: Mining Guild' first. The PLANNER serves that through the
    ornate jewellery box (it reads the destination, not the item), so the pack must not call the
    necklace missing -- the loadout reads teleports with the planner's own parse()."""
    lo = sl.build("Scorpions", 30, PANEL, 70, 10, {}, BANK)
    assert not any("necklace" in p.lower() or "teleport:" in p.lower() for p in lo.problems), lo.problems
    assert not any("necklace" in w["name"].lower() for w in lo.withdraw)


def test_a_teleport_no_leg_can_run_is_never_packed(monkeypatch):
    """A site whose only teleports no leg can run (a construction cape, a re-directed tablet):
    nothing to withdraw, and the pack says so once rather than asking the banker for a
    'Construction cape'. Synthetic since 2026-09-20 -- every real Aya task now has a runnable one."""
    from collections import namedtuple
    Site = namedtuple("Site", "teleports target areas location")
    site = Site(("Construction cape: Taverley (Option 3)", "Re-direct house tab (Option 2)"),
                (2863, 9878, 0), ((2856, 9867, 17, 19, 0),), "somewhere")
    monkeypatch.setattr(sl.slayer_tasks, "_site", lambda task: site)
    lo = sl.build("Imaginary task", 30, PANEL, 70, 10, {}, BANK)
    assert not any("cape" in w["name"].lower() or "re-direct" in w["name"].lower() for w in lo.withdraw)
    assert any("no usable teleport" in p.lower() for p in lo.problems)


def test_a_glider_task_packs_the_ring_that_reaches_the_pilot():
    """Wolves: 'Gnome Glider: Sindarpos'. The glider is not an item, but the route to its pilot
    starts with a slayer ring to the Stronghold -- THAT is what the trip must carry."""
    want = _want(sl.build("Wolves", 30, PANEL, 70, 10, {}, BANK))
    assert want.get("Slayer ring (2)") == 1


def test_a_spell_teleport_packs_its_runes_as_stacks():
    """Suqahs: Moonclan Teleport. The runes are what the trip must carry -- two casts' worth, so
    one unconfirmed landing does not strand the routine -- and each rune is ONE slot."""
    bank = dict(BANK, **{"law rune": 300, "astral rune": 900, "earth rune": 5000})
    lo = sl.build("Suqahs", 60, PANEL, 70, 10, {}, bank)
    want = _want(lo)
    assert (want["Law rune"], want["Astral rune"], want["Earth rune"]) == (2, 4, 4)
    assert lo.problems == [] and lo.slots <= 28
    short = sl.build("Suqahs", 60, PANEL, 70, 10, {}, dict(BANK, **{"law rune": 300, "earth rune": 50}))
    assert any("astral rune" in p.lower() for p in short.problems)
