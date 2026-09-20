"""The Kuradal catalog is DATA; these tests are what keep the data honest.

Plan E1 (docs/superpowers/plans/2026-09-19-slayer-travel-bank-loadout.md): every FIGHT task has an
entry; every entry starts with a teleport; profile + carried needs + teleports fit the inventory;
no site in the Wilderness unless tagged; every `routable` walk is a real crossfloor route.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aya_tasks
import controller
import crossfloor
import slayer_catalog
import slayer_routes
import slayer_tasks
import supplies


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


FIGHT_TASKS = sorted(t for t, d in slayer_tasks.KURADAL.items() if d == slayer_tasks.FIGHT)

# What the user's account proves today, read live 2026-09-20 (legs.skill_levels /
# legs.requirement_values): Agility 77 and the Ancient Cavern whirlpool varbit.
ACCOUNT = {"skill_levels": {"Agility": 77}, "varbit_values": {3759: 3}}


def test_every_kuradal_fight_task_has_an_entry_and_nothing_else_does():
    assert sorted(slayer_catalog.CATALOG) == FIGHT_TASKS
    assert len(FIGHT_TASKS) == 22


@pytest.mark.parametrize("key", FIGHT_TASKS)
def test_an_entry_is_complete(key):
    spec = slayer_catalog.get(key)
    assert spec.key == key and spec.name and spec.location
    assert len(spec.target) == 3 and spec.areas
    assert spec.teleports, "every route starts with a teleport"
    assert len(spec.landing) == 3
    assert spec.profile in supplies.PROFILES and spec.profile != "TRIVIAL"
    assert any(slayer_catalog.contains(spec, *spec.target) for _ in (0,)), "the target sits in its own area"


@pytest.mark.parametrize("key", FIGHT_TASKS)
def test_no_site_is_in_the_wilderness(key):
    """Spec 7.8. crossfloor's classifier, not a bare y >= 3520: the Wilderness has an underground
    twin and a slayer cave east of its box."""
    spec = slayer_catalog.get(key)
    assert not crossfloor.in_wilderness(spec.target), key
    assert not crossfloor.in_wilderness(spec.landing), key
    assert spec.wilderness is False


@pytest.mark.parametrize("key", FIGHT_TASKS)
def test_the_loadout_fits_the_inventory(key):
    """The plan's rule: food + full prayer potions + what the task needs carried + the teleport
    item actually used (one -- alternatives stay in the bank) + a house tablet and an escape ring
    fit 28 slots."""
    assert slayer_catalog.slots_used(slayer_catalog.get(key)) <= supplies.INVENTORY_SLOTS, key


def test_which_tasks_leave_less_than_the_loot_reserve_is_a_known_short_list():
    """Not a failure, a FACT for phase C/D to plan around: a PRAYER_PLUS_DAMAGE task that also
    carries an item and a teleport leaves 3 free slots, one under the 4 the loot hook wants."""
    tight = {k for k in FIGHT_TASKS
             if supplies.INVENTORY_SLOTS - slayer_catalog.slots_used(slayer_catalog.get(k))
             < slayer_catalog.LOOT_RESERVE_SLOTS}
    assert tight == {"kalphite"}, tight


@pytest.mark.parametrize("key", [k for k in FIGHT_TASKS if slayer_catalog.get(k).routable])
def test_a_routable_entry_really_routes_from_its_landing(key):
    """The routes report, as an assertion, with what the account proves today."""
    spec = slayer_catalog.get(key)
    route = crossfloor.find_route(spec.landing, spec.target, **ACCOUNT)
    assert route is not None, f"{key}: no route from {spec.landing} to {spec.target}"
    end = route.steps[-1].dest
    assert slayer_catalog.contains(spec, *end), f"{key}: the route ends at {end}, outside the kill area"


def test_the_entries_not_marked_routable_say_why():
    unroutable = {k: slayer_catalog.get(k).note for k in FIGHT_TASKS if not slayer_catalog.get(k).routable}
    assert set(unroutable) == {"gryphons"}, unroutable
    assert "recording" in unroutable["gryphons"]


def test_the_users_decisions_are_the_data():
    """docs/slayer-sites/decisions.md, spot-checked where the user spoke."""
    assert slayer_catalog.get("nechryael").profile == "PRAYER_PLUS_DAMAGE"      # 15 food: death spawns
    assert slayer_catalog.get("waterfiends").profile == "SAFESPOT"
    assert slayer_catalog.get("elves").location == "Lletya"                      # chosen despite 3 elves
    assert "mirror shield" in slayer_catalog.get("basilisks").worn
    demons = slayer_catalog.get("black demons")
    assert demons.target == (2852, 9784, 0) and "pipe" in demons.note           # USER 2026-09-20
    assert slayer_catalog.get("greater demons") is None                          # STOP, never fought here


def test_lookup_tolerates_case_and_a_singular_slip_like_aya_tasks_does():
    assert slayer_catalog.get("Black Demons").key == "black demons"
    assert slayer_catalog.get("black demon").key == "black demons"
    assert slayer_catalog.get("cows") is None and aya_tasks.get("cows") is not None


# --- the routine reads the catalog through the functions it already calls -----------------------

def test_at_site_and_where_answer_for_a_kuradal_task():
    assert slayer_tasks.at_site("Black demons", {"x": 2852, "y": 9784, "plane": 0}) is True
    assert slayer_tasks.at_site("Black demons", {"x": 2893, "y": 3465, "plane": 0}) is False
    assert slayer_tasks.at_site("Boss", {"x": 1, "y": 1, "plane": 0}) is None, "no site known: still None"
    assert "Taverley Dungeon" in slayer_tasks.where("Black demons")
    assert slayer_tasks.at_site("Cows", {"x": 1, "y": 1, "plane": 0}) is False, "Aya's tasks are untouched"


def test_plan_walks_back_when_already_near_a_kuradal_site():
    legs_, why = slayer_routes.plan("Black demons", {"x": 2860, "y": 9790, "plane": 0}, {})
    assert why == "" and [l["kind"] for l in legs_] == ["walk"]
    assert legs_[0]["dest"] == [2852, 9784, 0]


def test_plan_uses_a_carried_tablet_for_a_kuradal_task():
    """The Taverley tablet is an inventory item with a direct option -- the mechanism the
    Ectophial already uses."""
    legs_, why = slayer_routes.plan("Black demons", {"x": 3200, "y": 3200, "plane": 0},
                                    {"Taverley teleport": 3})
    assert why == "" and [l["kind"] for l in legs_] == ["item", "walk"]
    assert legs_[0]["dest"] == [2893, 3465, 0]


def test_plan_uses_a_carried_slayer_ring_through_its_destination_dialogue():
    """12 of the 22 start with a slayer ring. Its paged destination menu was PROVEN LIVE in
    golem_crafting (2026-09-14), so the leg reuses that pattern instead of waiting for a recording."""
    legs_, why = slayer_routes.plan("Hellhounds", {"x": 3200, "y": 3200, "plane": 0},
                                    {"slayer ring (8)": 1})
    assert why == "" and [l["kind"] for l in legs_] == ["dialogue_item", "walk"]
    assert legs_[0]["option"] == "stronghold" and legs_[0]["dest"] == [2431, 3422, 0]
    tower, _ = slayer_routes.plan("Nechryael", {"x": 3200, "y": 3200, "plane": 0}, {"slayer ring (2)": 1})
    assert tower[0]["option"] == "slayer tower" and tower[1]["dest"] == [3412, 9962, 3]
    none, why = slayer_routes.plan("Hellhounds", {"x": 3200, "y": 3200, "plane": 0}, {})
    assert none is None and "not carried" in why


def test_plan_names_what_it_cannot_do_yet():
    """Xeric's talisman opens its own INTERFACE, not a chat menu, and has never been measured:
    the routine must STOP naming it, not guess."""
    legs_, why = slayer_routes.plan("Dagannoth", {"x": 3200, "y": 3200, "plane": 0},
                                    {"xeric's talisman": 1})
    assert legs_ is None and "Xeric's talisman" in why
