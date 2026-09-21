import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import aya_tasks
import slayer_routes as sr


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


def test_the_plugins_teleport_strings_are_parsed_not_guessed():
    assert sr.parse("Fairy ring: AJQ") == ("fairy", "AJQ")
    assert sr.parse("Fairy ring: blr") == ("fairy", "BLR")
    assert sr.parse("Ectophial: Empty") == ("item", "ectophial")
    assert sr.parse("Ornate Jewellery Box: Cooking Guild") == ("box", "Cooking Guild")
    # Was "unsupported -- paged dialogue: phase B" until 2026-09-20: legs.dialogue_item now runs the
    # ring's paged menu (golem_crafting had already proven it live).
    assert sr.parse("Slayer Ring: Morytania Slayer Tower (Option 2)") == ("dialogue_item", ("slayer ring", "slayer tower"))
    assert sr.parse("Gnome Glider: Sindarpos") == ("glider", "sindarpos")
    assert sr.parse("Construction cape: Taverley (Option 3)")[0] == "unsupported", "no leg runs a cape teleport"
    assert sr.parse("")[0] == "unsupported"


def test_the_box_is_parsed_regardless_of_which_item_offers_it():
    """The box serves a destination no matter which teleport ITEM names it -- the box itself,
    a glory amulet, a skills necklace -- and a trailing '(Option N)' paging suffix is stripped."""
    assert sr.parse("Ornate Jewellery Box: Cooking Guild") == ("box", "Cooking Guild")
    assert sr.parse("Glory amulet: Draynor Village (Option 3)") == ("box", "Draynor Village")
    assert sr.parse("Skill's Necklace: Mining Guild") == ("box", "Mining Guild")
    assert sr.parse("Glory amulet: Karamja") == ("box", "Karamja")
    assert sr.parse("Glory amulet: Nowhere in particular")[0] == "unsupported"


def test_the_nearest_ring_is_measured_by_walking_not_by_the_crow():
    """From the bears site south-west of the Legends' Guild the BLR ring is a ~45 tile walk."""
    ring = sr.nearest_ring((2700, 3321, 0))
    assert ring is not None and ring.origin == (2740, 3351, 0)
    assert sr.nearest_ring((3221, 9600, 0)) is None, "no overworld ring is walkable from a random cave tile"


def test_cave_slimes_from_the_bears_site_is_walk_ring_walk():
    route, why = sr.plan("Cave slimes", {"x": 2700, "y": 3321, "plane": 0}, carried={})
    assert why == "" and [leg["kind"] for leg in route] == ["walk", "fairy", "walk"]
    walk_in, hop, walk_out = route
    assert walk_in["dest"] == [2740, 3351, 0]
    assert hop["code"] == "AJQ" and hop["expect"] == "Dorgesh" and hop["dest"] == [2735, 5221, 0]
    assert hop["ring_tile"] == [2740, 3351, 0] and hop["ring_id"] == 29495
    assert walk_out["dest"] == [2730, 5234, 0]


def test_a_carried_item_teleport_is_used_when_the_task_lists_one():
    route, why = sr.plan("Zombies", {"x": 3200, "y": 3200, "plane": 0}, carried={"ectophial": 1})
    assert why == "" and [leg["kind"] for leg in route] == ["item", "walk"]
    # "Teleport", MEASURED LIVE 2026-09-21 -- this line said "Empty", which was never run.
    assert route[0]["item"] == "ectophial" and route[0]["option"] == "Teleport"
    no_item, why = sr.plan("Zombies", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert no_item is None and "Ectophial" in why


def test_minotaurs_with_a_tablet_routes_through_the_house_box():
    """minotaurs' first teleport is 'Ornate Jewellery Box: Cooking Guild' -- with a house
    tablet carried, that becomes the house-box leg chain."""
    route, why = sr.plan("Minotaurs", {"x": 3200, "y": 3200, "plane": 0},
                         carried={"teleport to house": 1})
    assert why == ""
    assert [leg["kind"] for leg in route] == ["poh_enter", "poh_pool", "poh_box", "walk"]
    assert route[2]["option"] == "Cooking Guild" and route[2]["dest"] == list(sr.BOX_LANDINGS["Cooking Guild"])


def test_minotaurs_without_a_tablet_names_the_tablet_in_the_reason():
    route, why = sr.plan("Minotaurs", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None
    assert "house tablet" in why


def test_cave_slimes_from_lumbridge_with_a_tablet_uses_the_house_ring():
    """Lumbridge Swamp Caves: no overworld ring is within any reasonable walk from underground
    (same class of position as nearest_ring's own 'random cave tile' test), so with a tablet the
    house ring is what dials AJQ instead."""
    assert sr.nearest_ring((3210, 9569, 0)) is None
    route, why = sr.plan("Cave slimes", {"x": 3210, "y": 9569, "plane": 0},
                         carried={"teleport to house": 1})
    assert why == ""
    assert [leg["kind"] for leg in route] == ["poh_enter", "poh_pool", "poh_ring", "walk"]
    ring_leg = route[2]
    assert ring_leg["code"] == "AJQ" and ring_leg["expect"] == "Dorgesh" and ring_leg["dest"] == [2735, 5221, 0]
    assert route[3]["dest"] == [2730, 5234, 0]


def test_cave_slimes_from_the_bears_site_still_prefers_the_overworld_ring_over_the_house():
    """Even carrying a house tablet, a walkable overworld ring wins -- the house is a fallback,
    never preferred (unchanged from test_cave_slimes_from_the_bears_site_is_walk_ring_walk)."""
    route, why = sr.plan("Cave slimes", {"x": 2700, "y": 3321, "plane": 0},
                         carried={"teleport to house": 1})
    assert why == "" and [leg["kind"] for leg in route] == ["walk", "fairy", "walk"]


def test_a_player_already_in_the_house_gets_the_house_route_with_no_tablet_at_all(monkeypatch):
    """inInstance=True means poh.enter's own no-op-when-already-inside makes a tablet moot, and
    nearest_ring must not even be consulted (its template x/y mean nothing inside an instance) --
    a fairy-teleport task (cave slimes) proves both at once."""
    house_player = {"x": 1933, "y": 7105, "plane": 1, "inInstance": True,
                    "instX": 11669, "instY": 1449}

    def refuse(*a, **k):
        raise AssertionError("nearest_ring must not be consulted while already in the house")
    monkeypatch.setattr(sr, "nearest_ring", refuse)
    route, why = sr.plan("Cave slimes", house_player, carried={})
    assert why == ""
    assert [leg["kind"] for leg in route] == ["poh_enter", "poh_pool", "poh_ring", "walk"]


def test_a_player_in_the_house_still_gets_the_box_route_with_no_tablet():
    house_player = {"x": 1933, "y": 7105, "plane": 1, "inInstance": True,
                    "instX": 11669, "instY": 1449}
    route, why = sr.plan("Minotaurs", house_player, carried={})
    assert why == ""
    assert [leg["kind"] for leg in route] == ["poh_enter", "poh_pool", "poh_box", "walk"]


def test_every_unusable_teleport_is_named_in_the_reason():
    route, why = sr.plan("Minotaurs", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None
    assert "Ornate Jewellery Box" in why and "Skull sceptre" in why


def test_a_task_with_no_site_has_no_plan():
    # A SKIP task has no site anywhere. (Nechryael stood here until Kuradal's catalog arrived.)
    route, why = sr.plan("Dark beasts", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None and "catalog" in why
    # ...and a task WITH a site whose teleport no leg can run yet stops naming that teleport.
    route, why = sr.plan("Nechryael", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None and "Slayer ring: Slayer Tower" in why
    assert sr.plan("Cave slimes", {}, carried={}) == (None, "the player's position is unknown")


def test_an_unverified_ring_code_still_plans_because_the_dial_refuses_a_wrong_name():
    """hop_fairy_ring safety-stops when the Confirm text does not contain `expect`, BEFORE it
    teleports -- so a wrong guess in FAIRY_EXPECT costs a stop, never a trip to the wrong place."""
    for code in ("AJQ", "AJR", "BLR", "DLQ"):
        assert sr.FAIRY_EXPECT[code]


def test_the_ectophials_menu_action_is_Teleport_measured_live():
    """MEASURED LIVE 2026-09-21, first real travel leg. The registry said "Empty" and the routine
    stopped with the whole menu printed:

        No 'Empty' entry in menu for Ectophial (['Cancel', 'Examine', 'Drop', 'Use', 'Teleport'])

    "Empty" was a guess that had been carried since the row was written, under a comment calling
    the Ectophial the PROVEN mechanism that the other rows were modelled on. It had never been
    run. Every other row in ITEMS is still unverified for exactly the same reason, so this test
    exists as much to mark that as to pin the value."""
    name, option, landing = sr.ITEMS["ectophial"]

    assert option == "Teleport"
    assert name == "ectophial"
    assert landing == (3660, 3522, 0)


# --- 2026-09-21 LIVE: icefiends banked for a tablet it did not need ------------------------------

def test_the_jewellery_box_reaches_edgeville_monastery():
    """USER 2026-09-21: "shortest path currently gives us a very easy option. take house tablet
    then exit jewlry box: C edgeville monastery".

    The landing is the vendored teleportation_boxes.tsv row, and the walk from it is MEASURED at
    71 tiles -- against 160 from Edgeville and 277 from Falador Park.

    The KEY is "Monastery", from the USER's own screenshot of the box: the combat bracelet's third
    entry reads "C: Monastery", where C is the keybind and "Monastery" is the label. The vendored
    TSV calls the same row "C: Edgeville Monastery", which is Shortest Path's naming and NOT what
    the interface says -- taking the TSV at its word would have looked for an entry that does not
    exist. poh._box_matches compares the widget ACTION exactly, so "Monastery" also cannot collide
    with the glory's "Edgeville"."""
    assert sr.BOX_LANDINGS["Monastery"] == (3052, 3488, 0)


def test_icefiends_can_be_reached_with_a_house_tablet_and_no_bank_trip():
    """FOUND LIVE. The vendored task data lists exactly ONE way into Ice Mountain:

        "locations": [{"name": "Ice Mountain", "teleports": ["Lassar teleport"]}]

    The planner only ever tries the teleports a task lists, so with no Lassar tablet carried it
    gave up -- and the pack went to fetch one, teleporting to Ferox to do it. Meanwhile the account
    was carrying 119 house tablets, which reach the same place through the jewellery box.

    This is the vendored list being incomplete rather than the planner being wrong, so the fix is
    an entry in aya_tasks._EXTRA_TELEPORTS, the seam that already exists for exactly this."""
    task = aya_tasks.get("icefiends")

    assert any("Monastery" in t for t in task.teleports)
    assert "Lassar teleport" in task.teleports, "the listed one is still offered first"
