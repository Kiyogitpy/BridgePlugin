import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
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
    assert sr.parse("Ornate Jewellery Box: Cooking Guild")[0] == "unsupported", "the house is phase B"
    assert sr.parse("Slayer Ring: Morytania Slayer Tower (Option 2)")[0] == "unsupported", "paged dialogue: phase B"
    assert sr.parse("")[0] == "unsupported"


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
    assert route[0]["item"] == "ectophial" and route[0]["option"] == "Empty"
    no_item, why = sr.plan("Zombies", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert no_item is None and "Ectophial" in why


def test_every_unusable_teleport_is_named_in_the_reason():
    route, why = sr.plan("Minotaurs", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None
    assert "Ornate Jewellery Box" in why and "Skull sceptre" in why


def test_a_task_with_no_site_has_no_plan():
    route, why = sr.plan("Nechryael", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None and "catalog" in why
    assert sr.plan("Cave slimes", {}, carried={}) == (None, "the player's position is unknown")


def test_an_unverified_ring_code_still_plans_because_the_dial_refuses_a_wrong_name():
    """hop_fairy_ring safety-stops when the Confirm text does not contain `expect`, BEFORE it
    teleports -- so a wrong guess in FAIRY_EXPECT costs a stop, never a trip to the wrong place."""
    for code in ("AJQ", "AJR", "BLR", "DLQ"):
        assert sr.FAIRY_EXPECT[code]
