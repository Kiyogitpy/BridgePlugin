"""sl.build: a task plus what is carried and banked becomes a withdraw list.

PURE -- nothing here reads the bridge or clicks. Plan task C1
(docs/superpowers/plans/2026-09-19-slayer-travel-bank-loadout.md).
"""
import math
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
    """supplies.lowest_charge: finish a part-used ring off rather than strand it in the bank.

    AMENDED 2026-09-21: still true of a task's own teleport, no longer true of the ESCAPE ring.
    That one is now taken FULL -- see test_the_escape_ring_is_taken_FULL_not_nearly_spent for the
    live run where taking a (3), then a (2), then a (1) ended with no ring at all."""
    lo = sl.build("Nechryael", 150, PANEL, 70, 10, carried={}, banked=BANK)
    want = _want(lo)
    assert want.get("Slayer ring (2)") == 1 and "Slayer ring (8)" not in want
    assert want.get("Ring of dueling(8)") == 1, "the escape ring is the exception"


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


def test_an_empty_panel_is_only_a_problem_when_the_BANK_cannot_answer_it():
    """SUPERSEDED 2026-09-21 by the USER: "do it any food any prayer but not wilderness food."

    An empty panel used to be a flat stop, and on the live account BOTH names were empty strings,
    so every task stopped at the pack without ever reaching a bank. It now chooses -- so the
    problem fires only when there is genuinely nothing ordinary to choose.

    A bank holding nothing but Wilderness supplies is exactly that case, and it is the real one:
    the account holds over a thousand blighted anglerfish."""
    empty_panel = {"food": {}, "potions": {}}

    chosen = sl.build("Nechryael", 150, empty_panel, 70, 10, {}, BANK)
    assert not any("is chosen in the panel" in p for p in chosen.problems),         "the bank could answer, so nothing to complain about"

    only_wilderness = {"blighted anglerfish": 1319, "blighted super restore(4)": 149}
    stuck = sl.build("Nechryael", 150, empty_panel, 70, 10, {}, only_wilderness)
    assert any("food" in p.lower() for p in stuck.problems)
    assert any("prayer" in p.lower() for p in stuck.problems)
    assert not any("blighted" in w["name"].lower() for w in stuck.withdraw)


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


def test_a_worn_requirement_is_fetched_when_it_is_neither_worn_nor_carried():
    bank = dict(BANK, **{"mirror shield": 1})
    assert _want(sl.build("Basilisks", 150, PANEL, 70, 10, {}, bank))["Mirror shield"] == 1
    worn = sl.build("Basilisks", 150, PANEL, 70, 10, {"mirror shield": 1}, bank)
    assert "Mirror shield" not in _want(worn)
    none = sl.build("Basilisks", 150, PANEL, 70, 10, {}, BANK)
    assert any("mirror shield" in p.lower() for p in none.problems)


def test_blighted_supplies_are_refused_because_no_site_here_is_in_the_wilderness():
    """FOUND 2026-09-21 in the user's real bank: 1319 Blighted anglerfish, 149 Blighted super
    restore(4), and only 27 ordinary Super restore(4). Blighted items can ONLY be consumed in the
    Wilderness (wiki: Blighted), and every site in this routine is outside it -- the catalog
    asserts that. Packing one means carrying food that cannot be eaten when HP drops, so it is a
    named problem BEFORE the trip, not a discovery at the site."""
    bank = dict(BANK, **{"blighted anglerfish": 1319, "blighted super restore(4)": 149})
    panel = {"food": {"name": "Blighted anglerfish"}, "potions": {"prayerPotion": "Blighted super restore"}}
    lo = sl.build("Nechryael", 150, panel, 70, 10, {}, bank)
    assert any("blighted" in p.lower() and "wilderness" in p.lower() for p in lo.problems), lo.problems
    assert not any("blighted" in w["name"].lower() for w in lo.withdraw), "never withdrawn"
    ok = sl.build("Nechryael", 150, PANEL, 70, 10, {}, BANK)
    assert not any("blighted" in p.lower() for p in ok.problems)


def test_a_super_restore_is_counted_as_a_super_restore_not_a_prayer_potion():
    """The account has NO prayer potions at all -- 27 Super restore(4) instead, measured from the
    real bank 2026-09-21 -- so the panel's choice has to be a super restore, and the pack must
    withdraw it by that name.

    MEASURED, and the reason there is no super_restore flag threaded through here: at these
    numbers it would change nothing. prayer_doses_needed saturates at MAX_DOSES (32) for any task
    over ~40 kills, and prayer_potions_for caps at 8, so a dose that restores one point more
    lands on the same count. The invariant below is what actually matters."""
    bank = dict(BANK, **{"super restore(4)": 27})
    panel = {"food": {"name": "Shark"}, "potions": {"prayerPotion": "Super restore"}}
    lo = sl.build("Nechryael", 150, panel, 70, 10, {}, bank)
    want = _want(lo)
    assert "Super restore(4)" in want and lo.problems == []
    plain = sl.build("Nechryael", 150, PANEL, 70, 10, {}, BANK)
    assert want["Super restore(4)"] <= _want(plain)["Prayer potion(4)"], "a stronger dose is never MORE potions"


# --- 2026-09-21: ANY food, ANY prayer restore, never a Wilderness one ----------------------------
# USER: "do it any food any prayer but not wilderness food". The panel's two names were a hard
# blocker -- both were empty strings on the live account and every task stopped at the pack.

def _panel(food=None, potion=None):
    cfg = {"food": {"enabled": True}, "potions": {"prayerEnabled": True}}
    if food is not None:
        cfg["food"]["name"] = food
    if potion is not None:
        cfg["potions"]["prayerPotion"] = potion
    return cfg


def test_with_no_food_named_it_picks_one_out_of_the_bank():
    """An empty panel used to be a STOP. It now chooses, because the user does not care which food
    it takes as long as it is not a Wilderness one."""
    banked = {"cooked karambwan": 376, "tuna": 561}

    out = sl.build("cows", 20, _panel(), 70, 0, {}, banked)

    taken = {w["name"].lower() for w in out.withdraw}
    assert "cooked karambwan" in taken, "the better food of the two it actually has"
    assert not any("food is chosen" in p for p in out.problems)


def test_it_refuses_a_blighted_food_even_when_that_is_all_there_is():
    """The account holds 1319 blighted anglerfish and they CANNOT be eaten outside the Wilderness.
    Picking the best-healing thing in the bank would pick exactly the one that does not work."""
    banked = {"blighted anglerfish": 1319, "blighted manta ray": 200}

    out = sl.build("cows", 20, _panel(), 70, 0, {}, banked)

    assert not any("blighted" in w["name"].lower() for w in out.withdraw)
    assert any("food" in p.lower() for p in out.problems), "and it says so rather than packing none"


def test_a_food_named_in_the_panel_still_wins():
    """Choosing for the user is a fallback, not an override -- an explicit choice is still a choice."""
    banked = {"cooked karambwan": 376, "shark": 50}

    out = sl.build("cows", 20, _panel(food="Shark"), 70, 0, {}, banked)

    assert any(w["name"].lower() == "shark" for w in out.withdraw)


def test_a_blighted_food_named_in_the_panel_falls_through_to_a_real_one():
    """It used to name the problem and pack nothing. Saying what is wrong AND carrying on with
    something edible is strictly better."""
    banked = {"blighted anglerfish": 1319, "cooked karambwan": 376}

    out = sl.build("cows", 20, _panel(food="Blighted anglerfish"), 70, 0, {}, banked)

    assert any("WILDERNESS" in p for p in out.problems), "still says why"
    assert any(w["name"].lower() == "cooked karambwan" for w in out.withdraw), "and still packs"


def test_the_prayer_restore_is_chosen_the_same_way():
    banked = {"super restore(4)": 7, "prayer potion(4)": 20}

    out = sl.build("nechryael", 150, _panel(), 70, 0, {}, banked)

    taken = {w["name"].lower() for w in out.withdraw}
    assert "prayer potion(4)" in taken, "the one it has enough of, rather than the nicer one it does not"


def test_a_blighted_restore_is_never_chosen():
    banked = {"blighted super restore(4)": 149}

    out = sl.build("nechryael", 150, _panel(), 70, 0, {}, banked)

    assert not any("blighted" in w["name"].lower() for w in out.withdraw)


# --- 2026-09-21 LIVE: the bank trip deposited the routine's own means of working ----------------

def test_the_rune_pouch_and_its_runes_are_ALWAYS_kept():
    """FOUND LIVE, first real bank trip. USER: "it deposited the rune pouch all the prayer pots and
    our slayer ring??"

    The keep list held only what THIS task needs, and zombies needs none of it -- so the trip
    correctly deposited everything else, including the rune pouch. But NPC Contact is how the
    routine gets its NEXT task, and the pouch carries the runes for it. Depositing it strands the
    run: it would finish zombies and then be unable to ask for anything.

    These are the routine's own tools rather than any task's supplies, so they are kept for every
    task, always."""
    out = sl.build("cows", 20, PANEL, 70, 10, carried={}, banked=BANK)

    keep = {k.lower() for k in out.keep}
    assert "rune pouch" in keep
    for rune in ("astral rune", "cosmic rune", "air rune", "dust rune"):
        assert rune in keep, rune


def test_carrying_the_pouch_DOES_cost_the_pack_a_slot():
    """REWRITTEN 2026-09-21 against a live measurement, not reverted.

    This asserted the opposite, reasoning that counting a carried pouch "would make every pack
    read one slot larger and could refuse a trip that fits". The fear is inverted: a rune pouch
    in the inventory occupies a slot whether or not the pack fetched it, and NOT counting it is
    what let a pack accept a trip that did not fit. MEASURED on the first Kuradal task (elves
    x139): the pack reported "26 of 28" against a real 28 of 28, and the two it had lost were
    exactly this -- carried essentials and carried travel kit. It then set off, filled the
    inventory, and could not fit the teleport crystal that was the only way to the site."""
    with_pouch = sl.build("cows", 20, PANEL, 70, 10, carried={"rune pouch": 1}, banked=BANK)
    without = sl.build("cows", 20, PANEL, 70, 10, carried={}, banked=BANK)

    assert with_pouch.slots == without.slots + 1


def test_the_escape_ring_and_house_teleports_were_already_kept_and_still_are():
    """A regression guard around the change: the things the pack fetches must not fall off the
    keep list while adding the ones it does not."""
    out = sl.build("cows", 20, PANEL, 70, 10, carried={}, banked=BANK)

    keep = {k.lower() for k in out.keep}
    assert any("ring of dueling" in k for k in keep)
    assert any("teleport to house" in k for k in keep)


def test_a_teleport_that_needs_no_fetching_is_preferred_over_one_that_does():
    """FOUND LIVE 2026-09-21, twice. USER: "why do we need to bank? ... we have all the
    components on us."

    _teleport walks the task's teleports IN ORDER and packs the first it can run. For icefiends
    that order begins with "Lassar teleport", which was in the bank, so it packed a bank trip --
    even though the very next entry is the jewellery box, which the account reaches with the house
    tablets it already carries and which costs no inventory slot at all.

    Order should not decide this. A teleport needing nothing fetched wins over one that does,
    wherever it sits in the list; only if none is free does the fetch happen."""
    carried = {"teleport to house": 119}
    banked = dict(BANK, **{"lassar teleport": 20})

    out = sl.build("icefiends", 17, PANEL, 70, 10, carried=carried, banked=banked)

    taken = {w["name"].lower() for w in out.withdraw}
    assert not any("lassar" in n for n in taken), "the box is free; nothing to fetch"


def test_a_teleport_IS_fetched_when_no_free_one_exists():
    """The preference is not a refusal. With no house tablet there is no box route, so the listed
    tablet is packed exactly as before."""
    out = sl.build("icefiends", 17, PANEL, 70, 10, carried={},
                   banked=dict(BANK, **{"lassar teleport": 20}))

    taken = {w["name"].lower() for w in out.withdraw}
    assert any("lassar" in n for n in taken)


# --- 2026-09-21: a TRAVEL KIT, so one bank trip stocks many tasks --------------------------------

def test_a_bank_visit_tops_up_the_travel_kit_not_just_this_task():
    """USER 2026-09-21: "we should have all teleports on us instead of doing banking runs after
    each. like slayer ring, ectophial, House teleport etc."

    Right: the pack only ever fetched the ONE teleport this task needed, so the next task with a
    different teleport meant another trip. Since we are standing at a bank anyway, the marginal
    cost of the others is a few slots and no extra walking."""
    carried = {"teleport to house": 119}
    banked = dict(BANK, **{"slayer ring(8)": 22, "ectophial": 1, "xeric's talisman": 1,
                           "ring of dueling(8)": 15})

    out = sl.build("cows", 20, PANEL, 70, 10, carried=carried, banked=banked)

    taken = " | ".join(w["name"].lower() for w in out.withdraw)
    for wanted in ("slayer ring", "ectophial", "xeric's talisman"):
        assert wanted in taken, wanted


def test_the_kit_is_never_deposited_once_carried():
    carried = {"teleport to house": 119, "slayer ring(8)": 1, "ectophial": 1}

    out = sl.build("cows", 20, PANEL, 70, 10, carried=carried, banked=BANK)

    keep = " | ".join(out.keep)
    assert "slayer ring" in keep and "ectophial" in keep


def test_a_kit_item_already_carried_is_not_fetched_again():
    carried = {"teleport to house": 119, "ectophial": 1}
    banked = dict(BANK, **{"ectophial": 1})

    out = sl.build("cows", 20, PANEL, 70, 10, carried=carried, banked=banked)

    assert sum(1 for w in out.withdraw if "ectophial" in w["name"].lower()) == 0


def test_the_kit_gives_way_as_the_task_itself_gets_heavier():
    """The task's own food, potions and required items come first; the kit only ever uses slots
    nothing else wanted, and stops short of the reserve the loot hook needs.

    Measured against the two ends of the range: a 20-kill Aya task carries 8 slots of its own and
    takes the whole kit; a 150-kill nechryael trip carries 26 and takes what still fits. Neither
    is pushed past the inventory, which is the outcome that matters -- walking to a bank again
    later beats refusing a trip that would have worked."""
    banked = dict(BANK, **{"slayer ring(8)": 22, "ectophial": 1, "xeric's talisman": 1})

    def kit_of(task, count):
        out = sl.build(task, count, PANEL, 70, 10, carried={}, banked=banked)
        assert not any("the pack needs" in p for p in out.problems), task
        return [w["name"] for w in out.withdraw
                if any(k in w["name"].lower() for k in ("slayer ring", "ectophial", "xeric"))]

    light, heavy = kit_of("cows", 20), kit_of("nechryael", 150)

    assert len(light) == 3, "a small task has room for all of it"
    assert len(heavy) < len(light), "a heavy one takes only what fits"


def test_a_kit_item_the_bank_does_not_have_is_not_a_problem():
    """The kit is opportunistic. Missing one must never read as a reason to stop -- unlike a task's
    own required item, which always does."""
    out = sl.build("cows", 20, PANEL, 70, 10, carried={"teleport to house": 119}, banked=BANK)

    assert not any("slayer ring" in p.lower() or "xeric" in p.lower() for p in out.problems)


def test_the_escape_ring_is_taken_FULL_not_nearly_spent():
    """FOUND LIVE 2026-09-21. USER: "it should be able to get both from banks im not helping it."

    The escape ring was fetched with prefer_spent=True, which takes the LOWEST-charge copy -- good
    housekeeping for an ordinary teleport, since partial charges are otherwise dead weight. For
    the ESCAPE ring it is the wrong call: the account holds 15 Ring of dueling(8) and the pack kept
    taking a (3), then a (2), then a (1), so it ran out every few teleports and needed another bank
    trip to fix. That run ended with no ring at all.

    Running out is not an inconvenience here, it is a dead end -- banker's own stop says "no bank
    in range, and no ring of dueling left to reach one". Take the fullest one."""
    banked = dict(BANK, **{"ring of dueling(1)": 1, "ring of dueling(8)": 15})

    out = sl.build("cows", 20, PANEL, 70, 10, carried={"teleport to house": 119}, banked=banked)

    rings = [w["name"] for w in out.withdraw if "dueling" in w["name"].lower()]
    assert rings == ["Ring of dueling(8)"], rings


def test_an_ordinary_teleport_still_uses_up_its_part_charges():
    """The change is to the escape ring alone. A task's own teleport item keeps draining the
    nearly-spent copies first, which is what stops a bank filling with 1-charge leftovers."""
    banked = dict(BANK, **{"slayer ring(1)": 1, "slayer ring(8)": 22})

    out = sl.build("bloodveld", 150, PANEL, 70, 10, carried={"teleport to house": 119},
                   banked=banked)

    rings = [w["name"] for w in out.withdraw if "slayer ring" in w["name"].lower()]
    assert rings and all("(1)" in r for r in rings), rings


def test_no_teleport_is_packed_when_the_site_is_already_reachable_on_foot():
    """FOUND LIVE 2026-09-21. The routine teleported to Varrock, walked to the sewer manhole, and
    stopped there on an unrelated bug. On RESTART -- which wipes the in-progress journey, since it
    lives in memory -- the pack re-planned from scratch, decided it needed another Varrock
    teleport, and set off 40 tiles to a bank to fetch one while standing on the entrance.

    A teleport is for covering distance. When the site is already reachable from here without one,
    there is nothing to fetch, and `skip_teleport` is how the caller says so -- it has the player's
    position and the planner's answer, and the loadout does not."""
    carried = {"teleport to house": 119}
    banked = dict(BANK, **{"varrock teleport": 48})

    walked = sl.build("rats", 27, PANEL, 70, 10, carried, banked, skip_teleport=True)
    flown = sl.build("rats", 27, PANEL, 70, 10, carried, banked)

    assert not any("varrock" in w["name"].lower() for w in walked.withdraw)
    assert any("varrock" in w["name"].lower() for w in flown.withdraw), "still packed when needed"


def test_skipping_the_teleport_does_not_skip_the_rest_of_the_pack():
    """Food, the escape ring and the house tablets are not travel -- they are still wanted."""
    out = sl.build("rats", 27, PANEL, 70, 10, {"teleport to house": 119}, BANK, skip_teleport=True)

    keep = " | ".join(out.keep)
    assert "ring of dueling" in keep and "teleport to house" in keep


# --- 2026-09-21 LIVE: the first Kuradal task stopped on a bank that was not actually short ------

def test_the_bank_is_read_by_DOSE_not_only_by_full_potions():
    """FOUND LIVE 2026-09-21, the very first Kuradal task (gargoyles x183, streak 29 -> 30).

        [!] Safety stop: Slayer: gargoyles x183 -- cannot be packed -- prayer potions:
            need 8 more Super restore(4), bank has 7

    The real bank holds 7 Super restore(4) AND a (3), five (2)s and three (1)s: 16 containers,
    44 doses. A part-used potion CARRIED was already counted by its doses; a part-used potion
    BANKED was invisible, because the fetch only ever looked at `<base>(4)`. That asymmetry --
    not a real shortage -- is what stopped the run.
    """
    banked = {"super restore(4)": 7, "super restore(3)": 1, "super restore(2)": 5,
              "super restore(1)": 3, "cooked karambwan": 376, "rock hammer": 1,
              "ring of dueling(8)": 6, "slayer ring (8)": 2, "teleport to house": 114}

    out = sl.build("Gargoyles", 183, _panel(), 77, 0, {}, banked)

    assert out.problems == [], out.problems
    want = _want(out)
    assert want["Super restore(4)"] == 7, "every full one first"
    assert want["Super restore(3)"] == 1, "then the fullest part-used one, to fill the slot budget"
    assert "Super restore(2)" not in want, "and no further: the trip has room for 8 containers"


def test_a_thin_bank_is_packed_anyway_rather_than_refused():
    """AMENDED 2026-09-21 by the user, told the bank's best eight containers came to ~22 doses
    against a 24-dose floor: "Use up the ones in the inventory first then if they are out safety
    stop." Four one-dose potions is a poor trip; it is not a reason to refuse to go."""
    banked = {"super restore(1)": 4, "cooked karambwan": 376, "rock hammer": 1,
              "ring of dueling(8)": 6, "slayer ring (8)": 2, "teleport to house": 114}

    out = sl.build("Gargoyles", 183, _panel(), 77, 0, {}, banked)

    assert out.problems == [], out.problems
    assert _want(out)["Super restore(1)"] == 4, "it takes every one of them"


def test_being_genuinely_OUT_of_prayer_restore_is_the_safety_stop():
    """The other half of the same ruling: nothing carried and nothing in the bank IS the stop. A
    PRAYER task fought with no prayer is how an unattended account dies."""
    banked = {"blighted super restore(4)": 149, "cooked karambwan": 376, "rock hammer": 1,
              "ring of dueling(8)": 6, "slayer ring (8)": 2, "teleport to house": 114}

    out = sl.build("Gargoyles", 183, _panel(), 77, 0, {}, banked)

    assert any("prayer restore" in p.lower() or "out of" in p.lower()
               for p in out.problems), out.problems
    assert not any("blighted" in w["name"].lower() for w in out.withdraw)


def test_what_is_still_carried_counts_before_the_bank_is_asked():
    """"Use up the ones in the inventory first": eight containers already held is a full budget,
    however few doses are left in them, so the thin bank is not touched at all."""
    carried = {"super restore(1)": 8}
    banked = {"super restore(1)": 3, "cooked karambwan": 376, "rock hammer": 1,
              "ring of dueling(8)": 6, "slayer ring (8)": 2, "teleport to house": 114}

    out = sl.build("Gargoyles", 183, _panel(), 77, 0, carried, banked)

    assert not any("restore" in w["name"].lower() for w in out.withdraw), _want(out)
    assert out.problems == [], out.problems


def test_the_potion_slot_budget_is_never_topped_up_past_its_own_slots():
    """FOUND LIVE 2026-09-21, the bank trip immediately after the fix above.

        [bank] withdrawing 1x Super restore(3)   ... the trip is packed, 8 containers ...
        [!] Safety stop: gargoyles x183 -- still short after a full bank visit: 1 Super restore(2)

    The trip set aside 8 slots and came back with 8 containers holding 31 doses. Asking for the
    32nd dose asks for a NINTH container there is no room for, and slayer_policy turns any
    withdraw list surviving a bank visit into a STOP -- so the run stopped one dose under an
    estimate that is itself capped (supplies.MAX_DOSES).

    The fetch is bounded by BOTH the dose deficit and the slots the trip actually reserved.
    """
    carried = {"super restore(4)": 7, "super restore(3)": 1, "cooked karambwan": 5,
               "rock hammer": 1, "ring of dueling(7)": 1, "slayer ring (1)": 1,
               "teleport to house": 114}
    banked = {"super restore(2)": 5, "super restore(1)": 3, "cooked karambwan": 375,
              "ring of dueling(8)": 6, "slayer ring (8)": 2, "teleport to house": 114}

    out = sl.build("Gargoyles", 183, _panel(), 77, 0, carried, banked)

    assert not any("restore" in w["name"].lower() for w in out.withdraw), _want(out)
    assert out.problems == [], out.problems


def test_a_part_used_potion_still_leaves_room_for_the_rest_of_the_budget():
    """The cap is on the budget, not on fetching: holding two of eight potions still fetches six."""
    carried = {"super restore(4)": 2}
    banked = {"super restore(4)": 20, "cooked karambwan": 376, "rock hammer": 1,
              "ring of dueling(8)": 6, "slayer ring (8)": 2, "teleport to house": 114}

    out = sl.build("Gargoyles", 183, _panel(), 77, 0, carried, banked)

    assert _want(out)["Super restore(4)"] == 6


# --- the way there is withdrawn before the food (FOUND LIVE 2026-09-21) -----------------------

def test_the_withdraw_list_puts_the_journey_before_the_bulk_consumables():
    """The first Kuradal task ever run (elves x139) withdrew food, then potions, then the
    `Teleport crystal (1)` -- and the inventory filled on slot 28, so the only way to Lletya was
    the one thing that did not fit. Running out of food is a RESTOCK; running out of the way
    there is a stranding, so they must not compete for the last slot on equal terms."""
    pack = sl._Pack(carried={}, banked=None)
    pack._take("cooked karambwan", 15, bulk=True)
    pack._take("teleport crystal (1)", 1)
    pack._take("prayer potion(4)", 8, bulk=True)
    pack._take("ring of dueling(8)", 1)
    pack.withdraw.sort(key=lambda w: bool(w.get("bulk")))
    assert [w["name"] for w in pack.withdraw] == [
        "Teleport crystal (1)", "Ring of dueling(8)", "Cooked karambwan", "Prayer potion(4)"],         "non-bulk first, and stable within each half"


def test_food_and_prayer_restores_are_marked_bulk_and_a_teleport_is_not():
    pack = sl._Pack(carried={}, banked=None)
    pack.exact("food", "cooked karambwan", 3, bulk=True)
    pack.one_of("escape ring", ("ring of dueling",), prefer_spent=False)
    by_name = {w["name"].lower(): w for w in pack.withdraw}
    assert by_name["cooked karambwan"]["bulk"] is True
    assert any(not w.get("bulk") for w in pack.withdraw), "the ring is not a bulk consumable"


# --- the pack fits itself into the inventory (FOUND LIVE 2026-09-21) --------------------------
#
# The first Kuradal task ever run (elves x139) wanted 15 food, 8 prayer potions, a teleport
# crystal and the six-item travel kit: 30 slots in a 28-slot inventory, so it could NEVER fit
# however it was ordered. Worse, the pack did not know -- it read "26 of 28" against a real
# 28 of 28, because a kit item ALREADY CARRIED was kept but never counted.

def test_a_kit_item_already_carried_still_costs_a_slot():
    """one_of() counts a slot for a kit item it FETCHES; the already-carried branch skipped
    straight past it. Being carried is exactly why it occupies a slot."""
    empty = sl._Pack(carried={}, banked={})
    sl._top_up_kit(empty)
    held = sl._Pack(carried={n: 1 for n in sl.TRAVEL_KIT}, banked={})
    sl._top_up_kit(held)
    assert held.slots == len(sl.TRAVEL_KIT), "every carried kit item is a slot"
    assert held.withdraw == [], "and none of them needs fetching"


def test_trim_food_frees_slots_and_sheds_the_surplus_already_carried():
    pack = sl._Pack(carried={"shark": 15}, banked={"shark": 100})
    pack.exact("food", "shark", 15, bulk=True)
    assert pack.slots == 15 and "shark" in pack.keep
    freed = pack.trim_food(2)
    assert freed == 2 and pack.slots == 13
    # Reducing the target alone changes nothing while more is already carried, and banker has no
    # deposit-N: the food comes off the keep list so the deposit banks it all, then 13 come back.
    assert "shark" not in pack.keep
    assert [(w["name"], w["quantity"]) for w in pack.withdraw] == [("Shark", 13)]


def test_trim_food_only_shrinks_the_withdraw_when_nothing_is_carried():
    pack = sl._Pack(carried={}, banked={"shark": 100})
    pack.exact("food", "shark", 15, bulk=True)
    assert pack.trim_food(3) == 3
    assert "shark" in pack.keep, "nothing carried, so nothing to shed"
    assert [(w["name"], w["quantity"]) for w in pack.withdraw] == [("Shark", 12)]


def test_the_trim_has_a_floor_so_fits_never_becomes_nothing_to_eat():
    pack = sl._Pack(carried={}, banked={"shark": 100})
    pack.exact("food", "shark", 15, bulk=True)
    freed = pack.trim_food(99)
    floor = max(sl.FOOD_TRIM_MIN, math.ceil(15 * sl.FOOD_TRIM_FLOOR))
    assert 15 - freed == floor
    assert floor >= sl.FOOD_TRIM_MIN


def test_a_pack_that_cannot_fit_even_after_trimming_is_still_a_problem():
    """The floor holding means the trip is refused, which is the right answer for a pack that
    genuinely cannot be carried."""
    pack = sl._Pack(carried={}, banked={"shark": 100})
    pack.exact("food", "shark", 6, bulk=True)
    pack.trim_food(20)
    assert pack.slots >= sl.FOOD_TRIM_MIN
