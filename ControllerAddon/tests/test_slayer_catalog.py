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
                                    {"Taverley teleport": 3}, skill_levels={"Agility": 77})
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


def test_plan_uses_xerics_talisman_by_its_direct_menu_option():
    """MEASURED, from a recording, in routines/mahogany_homes.py (live for hours): the talisman's
    destination is a DIRECT right-click option named after the place ("Xeric's Glade" there).
    "Xeric's Heart" is its sibling -- the plain item leg, no new mechanism."""
    legs_, why = slayer_routes.plan("Dagannoth", {"x": 3200, "y": 3200, "plane": 0},
                                    {"xeric's talisman": 1})
    assert why == "" and [l["kind"] for l in legs_] == ["item", "walk"]
    assert legs_[0]["option"] == "Xeric's Heart" and legs_[0]["dest"] == [1644, 3673, 0]


def test_plan_names_what_it_cannot_do_yet():
    """The desert amulet's Kalphite cave teleport has no measured mechanism anywhere in the repo:
    with no fairy ring in walking range and no tablet, the routine must STOP naming both."""
    # Deep in the Dwarven Mine: no fairy ring within walking range (nearest_ring is None there).
    legs_, why = slayer_routes.plan("Kalphite", {"x": 3000, "y": 9800, "plane": 0},
                                    {"desert amulet 4": 1})
    assert legs_ is None and "Desert amulet 4" in why


def test_a_talisman_line_for_another_destination_is_not_sent_to_xerics_heart():
    assert slayer_routes.parse("Xeric's talisman: Xeric's Heart (Option 4)") == ("item", "xeric's talisman")
    assert slayer_routes.parse("Xeric's talisman: Xeric's Glade")[0] == "unsupported"
    assert slayer_routes.parse("Ectophial") == ("item", "ectophial"), "an item with one destination is untouched"


def test_suqahs_are_reached_by_casting_moonclan_teleport():
    legs_, why = slayer_routes.plan("Suqahs", {"x": 3200, "y": 3200, "plane": 0},
                                    {"law rune": 1, "astral rune": 2, "earth rune": 2})
    assert why == "" and [l["kind"] for l in legs_] == ["spell", "walk"]
    assert legs_[0]["widget"] == [218, 116] and legs_[0]["dest"] == [2113, 3915, 0]


def test_a_task_both_masters_assign_is_offered_both_registries_teleports():
    """Kalphite is in Aya's registry AND Kuradal's catalog, same cave. Aya's list alone stops at
    the desert amulet (no leg); the catalog adds fairy ring BIQ, which a carried tablet serves
    through the house ring."""
    mine = {"x": 3000, "y": 9800, "plane": 0}        # no fairy ring in walking range
    none, why = slayer_routes.plan("Kalphite", mine, {})
    assert none is None and "BIQ" in why and "Desert amulet" in why, "both were considered"
    legs_, why = slayer_routes.plan("Kalphite", mine, {"teleport to house": 2})
    assert why == "" and [l["kind"] for l in legs_][:3] == ["poh_enter", "poh_pool", "poh_ring"]
    # ...and from Lumbridge an OVERWORLD ring is a walk away, which beats the house.
    near, _ = slayer_routes.plan("Kalphite", {"x": 3200, "y": 3200, "plane": 0}, {})
    assert [l["kind"] for l in near] == ["walk", "fairy", "walk"] and near[1]["code"] == "BIQ"


def test_the_great_conch_and_karuulm_rings_have_a_confirm_text():
    """The wiki's Location text per code; AJQ's matches what was MEASURED on the Confirm button,
    which is what makes it a usable source. A wrong guess is a stop before the hop, never a trip."""
    assert slayer_routes.FAIRY_EXPECT["CJQ"] == "Conch"
    assert slayer_routes.FAIRY_EXPECT["CIR"] == "Karuulm"


def test_a_task_whose_last_walk_cannot_be_routed_is_refused_before_any_teleport_is_spent():
    """Gryphons: CJQ is dialable, but the cave entrance is in no table, so the walk leg could
    never succeed. Planning it would spend the hop and strand the routine on the Great Conch
    with a task active. `routable=False` in the catalog must stop the plan, naming why."""
    legs_, why = slayer_routes.plan("Gryphons", {"x": 3200, "y": 3200, "plane": 0},
                                    {"teleport to house": 5})
    assert legs_ is None and "recording" in why
    # Standing AT the site is still just a walk: nothing is spent.
    at_site, _ = slayer_routes.plan("Gryphons", {"x": 3222, "y": 8861, "plane": 0}, {})
    assert [l["kind"] for l in at_site] == ["walk"]


def test_no_teleport_is_spent_toward_a_walk_this_account_cannot_make():
    """The catalog tests prove routability for the USER's account. The planner must prove it for
    whoever is running: under 70 Agility (and with no dusty key) there is no way from Taverley to
    the black demons, and landing there with a task active is a strand, not a route."""
    lumbridge = {"x": 3200, "y": 3200, "plane": 0}
    tablet = {"Taverley teleport": 3}
    ok, why = slayer_routes.plan("Black demons", lumbridge, tablet, skill_levels={"Agility": 77})
    assert ok is not None and why == ""
    none, why = slayer_routes.plan("Black demons", lumbridge, tablet, skill_levels={"Agility": 50})
    assert none is None and "no walking route" in why and "Taverley teleport" in why
    # The dusty key, proven by item id, opens the other way in -- for the same low-Agility account.
    keyed, why = slayer_routes.plan("Black demons", lumbridge, tablet, skill_levels={"Agility": 50},
                                    proven={"owned_item_ids": {1590}})
    assert keyed is not None and why == ""


def test_what_the_account_proves_reaches_the_routability_check():
    """Waterfiends: the whirlpool needs varbit 3759 > 1. Unread, the walk from Barbarian Outpost
    cannot be proven and the house-box hop is refused; read (the account: 3), it plans."""
    lumbridge, tablets = {"x": 3200, "y": 3200, "plane": 0}, {"teleport to house": 5}
    none, why = slayer_routes.plan("Waterfiends", lumbridge, tablets)
    assert none is None and "no walking route" in why
    legs_, why = slayer_routes.plan("Waterfiends", lumbridge, tablets, proven={"varbit_values": {3759: 3}})
    assert why == "" and legs_[-1]["kind"] == "walk"


# --- 2026-09-20: found by sweeping Aya's 24 tasks on the live account --------------------------

def test_the_registrys_own_spelling_of_a_slayer_ring_destination_is_understood():
    """Aya's registry says 'Slayer Ring: Morytania Slayer Tower (Option 2)' -- capital R, a
    region prefix, a paging suffix. Banshees and crawling hands could not plan."""
    assert slayer_routes.parse("Slayer Ring: Morytania Slayer Tower (Option 2)") ==         ("dialogue_item", ("slayer ring", "slayer tower"))
    assert slayer_routes.parse("Slayer ring: Fremennik Slayer Dungeon (Option 3)")[1][1] == "fremennik slayer dungeon"
    legs_, why = slayer_routes.plan("Banshees", {"x": 3200, "y": 3200, "plane": 0}, {"slayer ring (8)": 1})
    assert why == "" and legs_[0]["kind"] == "dialogue_item" and legs_[0]["option"] == "slayer tower"


def test_a_plain_teleport_tablet_is_an_item_leg_with_its_landing_from_the_vendored_table():
    """'Varrock teleport' / 'Lassar teleport': the table calls them 'Varrock tablet' / 'Lassar
    tablet'; the in-game item is '<x> teleport' and its default action is Break (MEASURED in
    routines/mahogany_homes.py). Rats and icefiends could not plan."""
    assert slayer_routes.parse("Varrock teleport") == ("item", "varrock teleport")
    legs_, why = slayer_routes.plan("Rats", {"x": 2700, "y": 3300, "plane": 0}, {"varrock teleport": 5})
    assert why == "" and legs_[0]["kind"] == "item"
    assert (legs_[0]["item"], legs_[0]["option"], legs_[0]["dest"]) == ("varrock teleport", "Break", [3213, 3424, 0])
    ice, why = slayer_routes.plan("Icefiends", {"x": 2700, "y": 3300, "plane": 0}, {"lassar teleport": 2})
    assert why == "" and ice[0]["dest"] == [3004, 3470, 0]
    assert slayer_routes.parse("Nonsense teleport")[0] == "unsupported", "only what the table knows"


def test_a_digsite_pendant_mounted_in_the_house_serves_bats_and_skeletons():
    """USER 2026-09-20: 'There is a digsite pendant mounted in poh'. The registry line has NO
    colon -- 'Digsite pendant (Option 1)' -- and the vendored house table has the mounted object
    with the direct action 'Digsite' landing on (3341,3445,0)."""
    assert slayer_routes.parse("Digsite pendant (Option 1)") == ("poh_mounted", "digsite pendant")
    mine = {"x": 3000, "y": 9800, "plane": 0}
    for task in ("Skeletons", "Bats"):
        legs_, why = slayer_routes.plan(task, mine, {"teleport to house": 3})
        assert why == "", (task, why)
        assert [l["kind"] for l in legs_] == ["poh_enter", "poh_pool", "poh_mounted", "walk"], task
        assert legs_[2]["option"] == "Digsite" and legs_[2]["dest"] == [3341, 3445, 0]
    none, why = slayer_routes.plan("Skeletons", mine, {})
    assert none is None and "house tablet" in why


def test_wolves_are_reached_by_the_gnome_glider_from_the_grand_tree():
    """'Gnome Glider: Sindarpos'. The pilot stands on top of the Grand Tree, a walk and three
    ladders from the Stronghold slayer ring landing -- so the route is ring, walk UP the tree,
    glider, walk. It needs the ring carried; the whole onward walk is proven before any hop."""
    lumbridge = {"x": 3200, "y": 3200, "plane": 0}
    assert slayer_routes.parse("Gnome Glider: Sindarpos") == ("glider", "sindarpos")
    legs_, why = slayer_routes.plan("Wolves", lumbridge, {"slayer ring (8)": 1})
    assert why == "", why
    assert [l["kind"] for l in legs_] == ["dialogue_item", "walk", "glider", "walk"]
    assert legs_[1]["dest"] == [2465, 3501, 3] and legs_[2]["widget"] == [138, 7]
    assert legs_[2]["dest"] == [2850, 3498, 0]
    none, why = slayer_routes.plan("Wolves", lumbridge, {})
    assert none is None and "slayer ring" in why.lower()


def test_dwarves_route_through_the_house_once_fishing_contest_is_proven_by_its_varplayer():
    """The tunnel stairs are gated on VARPLAYER 11 == 5, not on a quest name -- which is why an
    earlier check that 'proved the quest' by name still found no route. legs.requirement_values
    reads varplayers, so at run time the gate is provable; unread, the hop is refused."""
    mine, tablets = {"x": 3000, "y": 9800, "plane": 0}, {"teleport to house": 3}
    none, why = slayer_routes.plan("Dwarves", mine, tablets)
    assert none is None and "no walking route" in why
    legs_, why = slayer_routes.plan("Dwarves", mine, tablets, proven={"varplayer_values": {11: 5}})
    assert why == "" and [l["kind"] for l in legs_] == ["poh_enter", "poh_pool", "poh_box", "walk"]
    assert legs_[2]["option"] == "Burthorpe"
    tab, why = slayer_routes.plan("Dwarves", mine, {"taverley teleport": 2}, proven={"varplayer_values": {11: 5}})
    assert why == "" and tab[0]["kind"] == "item" and tab[0]["dest"] == [2893, 3465, 0]


def test_zanaris_is_the_house_fairy_rings_own_menu_action():
    """'Fairy ring: Zanaris' is not a three-letter code: it is the ring's ZANARIS ACTION. MEASURED
    LIVE 2026-09-20 on the house ring (29228): 'Zanaris / Configure / Last-destination /
    Favourites / Remove'. So it is the same leg as the mounted pendant -- a named house object
    plus a menu action -- and a real code still parses as a code."""
    assert slayer_routes.parse("Fairy ring: Zanaris") == ("poh_mounted", "fairy ring")
    assert slayer_routes.parse("Fairy ring: BIQ") == ("fairy", "BIQ")
    legs_, why = slayer_routes.plan("Mutated zygomites", {"x": 3000, "y": 9800, "plane": 0},
                                    {"teleport to house": 3}, skill_levels={"Agility": 77})
    assert why == "" and [l["kind"] for l in legs_] == ["poh_enter", "poh_pool", "poh_mounted", "walk"]
    assert (legs_[2]["item"], legs_[2]["option"], legs_[2]["dest"]) == ("fairy ring", "Zanaris", [2412, 4434, 0])


def test_a_spell_is_not_planned_without_its_runes():
    """Moonclan Teleport costs 1 law, 2 astral, 2 earth (cache obj 9111). Casting without them
    clicks and does nothing, and the leg only failed by never landing. plan() now refuses up
    front and NAMES the rune. An earth staff stands in for the earth runes."""
    lumbridge = {"x": 3200, "y": 3200, "plane": 0}
    none, why = slayer_routes.plan("Suqahs", lumbridge, {"law rune": 5, "astral rune": 1, "earth rune": 9})
    assert none is None and "astral rune" in why and "2" in why
    full = {"law rune": 5, "astral rune": 9, "earth rune": 9}
    assert slayer_routes.plan("Suqahs", lumbridge, full)[0][0]["kind"] == "spell"
    staff = {"law rune": 5, "astral rune": 9, "mystic earth staff": 1}
    assert slayer_routes.plan("Suqahs", lumbridge, staff)[0][0]["kind"] == "spell"


# --- 2026-09-20, self-review: what a Kuradal task needs is CHECKED before any teleport ----------

def test_a_kuradal_tasks_requirements_stop_the_trip_before_any_teleport():
    """FOUND reviewing the day's work across pieces: slayer_tasks.required_items only knew Aya's
    registry, so every Kuradal task reported nothing missing -- the routine would teleport to
    basilisks with no mirror shield, to gargoyles with no rock hammer. The catalog already had
    the requirements as prose; they are now checkable alternatives."""
    assert slayer_tasks.missing_items("Gargoyles", 150, {}) and "rock hammer" in slayer_tasks.missing_items("Gargoyles", 150, {})[0].lower()
    assert slayer_tasks.missing_items("Gargoyles", 150, {"rock hammer": 1}) == []
    shield = slayer_tasks.missing_items("Basilisks", 150, {})
    assert shield and "mirror shield" in shield[0].lower()
    assert slayer_tasks.missing_items("Basilisks", 150, {"v's shield": 1}) == [], "V's shield serves too"
    assert slayer_tasks.missing_items("Aberrant spectres", 150, {"slayer helmet (i)": 1}) == []
    assert slayer_tasks.missing_items("Aberrant spectres", 150, {"nose peg": 1}) == []
    assert slayer_tasks.missing_items("Aberrant spectres", 150, {"abyssal whip": 1})
    assert slayer_tasks.missing_items("Kurask", 150, {"leaf-bladed battleaxe": 1}) == []
    assert slayer_tasks.missing_items("Kurask", 150, {"broad bolts": 500}) == []


def test_a_task_both_masters_assign_is_held_to_the_stricter_list():
    """Kalphite is in Aya's registry (no gear note) AND the catalog (antipoison: the soldiers'
    poison ignores prayer). The stricter list applies whoever assigned it."""
    assert any("antipoison" in m.lower() for m in slayer_tasks.missing_items("Kalphite", 40, {}))
    assert slayer_tasks.missing_items("Kalphite", 40, {"superantipoison(2)": 1}) == []


def test_a_requirement_nobody_can_check_by_name_is_not_invented():
    """Waterfiends want 'a ranged kit' -- which weapon is the USER's open decision (E0). It is
    shown as a note, never enforced as an item name we made up."""
    assert slayer_tasks.missing_items("Waterfiends", 150, {}) == []
    assert slayer_tasks.missing_items("Cows", 20, {}) == [], "an Aya task with no gear note is untouched"
    lit = slayer_tasks.missing_items("Cave horrors", 150, {"witchwood icon": 1})
    assert lit and "light" in lit[0].lower(), "cave horrors still need a LIT light source, by item id"
    assert slayer_tasks.missing_items("Cave horrors", 150, {"witchwood icon": 1, "kandarin headgear 3": 1}) == []
