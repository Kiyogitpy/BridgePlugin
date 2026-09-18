import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import transports


def _link(origin, dest=None):
    hits = [s for s in transports.load_links() if s.origin == origin and (dest is None or s.dest == dest)]
    assert hits, f"no transport from {origin}"
    return hits[0]


# --- the hub entrances the slayer sites depend on (spec §8.1, MEASURED against the jar) ---------

def test_the_stronghold_slayer_cave_entrance_is_a_link():
    step = _link((2430, 3424, 0), (2429, 9824, 0))
    assert (step.option, step.target, step.object_id) == ("Enter", "Cave", 26709)
    assert step.kind == transports.LINK


def test_the_catacombs_statue_and_a_multi_word_target_parse():
    step = _link((1639, 3673, 0), (1666, 10050, 0))
    assert (step.option, step.target, step.object_id) == ("Investigate", "Statue", 27785)
    back = _link((1665, 10051, 0), (1639, 3673, 0))
    assert (back.option, back.target, back.object_id) == ("Climb-up", "Vine ladder", 28894)


def test_the_slayer_tower_basement_ladder_lands_on_plane_three():
    """The basement is NOT plane 0 underground like most dungeons -- the table says plane 3."""
    step = _link((3417, 3536, 0))
    assert step.dest == (3412, 9932, 3)
    assert (step.option, step.object_id) == ("Climb-down", 30191)


def test_row_count_floor_so_an_empty_or_truncated_table_fails_loudly():
    assert len(transports.load_links()) >= 5000
    assert len(transports.load_agility_shortcuts()) >= 500


# --- classification --------------------------------------------------------------------------

def test_a_plain_door_is_classified_as_a_door_not_a_link():
    """worldmap bakes doors OPEN and travel.open_door_toward handles shut ones, so a route search
    must be able to leave the table's 350-odd 'Open Door' rows out."""
    door = _link((3097, 3107, 0), (3098, 3107, 0))
    assert door.option == "Open" and door.kind == transports.DOOR
    assert all(s.kind != transports.DOOR for s in transports.load_links(include_doors=False))


def test_the_wilderness_ditch_is_flagged():
    ditches = [s for s in transports.load_links() if s.target == "Wilderness Ditch"]
    assert ditches and all(s.enters_wilderness_ditch for s in ditches)
    assert not _link((2430, 3424, 0)).enters_wilderness_ditch


def test_a_row_with_no_object_id_keeps_none_rather_than_a_guess():
    step = _link((2795, 2793, 0), (2795, 2797, 1))
    assert step.option == "Climb-up" and step.target == "Staircase" and step.object_id is None


# --- requirements fail CLOSED ----------------------------------------------------------------

def test_agility_shortcuts_carry_their_level_and_need_it_proven():
    shortcut = next(s for s in transports.load_agility_shortcuts() if s.skills.get("Agility", 0) >= 50)
    level = shortcut.skills["Agility"]
    assert not transports.usable(shortcut, skill_levels=None), "unknown levels must not pass"
    assert not transports.usable(shortcut, skill_levels={"Agility": level - 1})
    if not (shortcut.quests or shortcut.items or shortcut.varbits or shortcut.varplayers):
        assert transports.usable(shortcut, skill_levels={"Agility": level})


def test_quest_item_and_varbit_requirements_fail_closed_when_unknown():
    links = transports.load_links()
    quest_row = next(s for s in links if s.quests and not (s.items or s.varbits or s.varplayers or s.skills))
    assert not transports.usable(quest_row)
    assert transports.usable(quest_row, completed_quests=set(quest_row.quests))

    item_row = next(s for s in links if s.items)
    assert not transports.usable(item_row, completed_quests=set(item_row.quests),
                                 skill_levels={k: 99 for k in item_row.skills}), \
        "an Items requirement is symbolic (MACHETE=1); with no owned-item names given it must not pass"

    var_row = next(s for s in links if s.varbits and not (s.items or s.quests or s.skills or s.varplayers))
    assert not transports.usable(var_row), "an unread varbit is unknown, and unknown fails"
    vid, op, value = var_row.varbits[0]
    good = {"=": value, ">": value + 1, "<": value - 1}.get(op, value)
    assert transports.usable(var_row, varbit_values={v: good for v, _, _ in var_row.varbits}) or len(var_row.varbits) > 1


def test_a_plain_ladder_needs_nothing():
    assert transports.usable(_link((2884, 3398, 0), (2884, 9798, 0)))      # Taverley Dungeon ladder


# --- fairy rings -----------------------------------------------------------------------------

def test_fairy_ring_codes_resolve_to_their_landing_tile():
    rings = transports.load_fairy_rings()
    assert rings.exit_for("AJQ") == (2735, 5221, 0)          # Dorgesh-Kaan south dungeon (cave bugs, slimes)
    assert rings.exit_for("ajq") == (2735, 5221, 0)          # case and spacing do not matter
    assert rings.exit_for("A J Q") == (2735, 5221, 0)
    assert rings.exit_for("CJQ") is not None                  # the Great Conch (gryphons)
    assert rings.exit_for("ZZZ") is None
    assert rings.requirements_for("AJQ").quests == ("Death to the Dorgeshuun",)


def test_a_multi_code_row_is_a_dial_sequence_not_a_set_of_aliases():
    """"A I R - D L R - D J Q - A J S" is the Fairy Queen's hideout, reached by dialling four
    codes in a row. AIR and DLR are ordinary rings of their own. The first draft of the parser
    read that row as aliases and silently re-pointed AIR at the hideout -- and the first draft of
    THIS test asserted the same wrong thing. The file is the authority."""
    rings = transports.load_fairy_rings()
    assert rings.exit_for("AIR") == (2700, 3247, 0)
    assert rings.exit_for("DLR") == (2213, 3099, 0)
    assert rings.sequences[("AIR", "DLR", "DJQ", "AJS")] == (2328, 4426, 0)
    assert rings.exit_for("AIR") != rings.sequences[("AIR", "DLR", "DJQ", "AJS")]


def test_fairy_ring_entrances_are_tiles_with_a_ring_object():
    rings = transports.load_fairy_rings()
    assert len(rings.entrances) >= 45
    zanaris = next(e for e in rings.entrances if e.origin == (2412, 4434, 0))
    assert (zanaris.option, zanaris.target, zanaris.object_id) == ("Configure", "Fairy ring", 29560)


# --- banks -----------------------------------------------------------------------------------

def test_banks_load_and_the_nearest_one_is_found_on_the_same_plane():
    banks = transports.load_banks()
    assert len(banks) >= 250
    near = transports.nearest_bank(3270, 3165, 0)
    assert near is not None and near.info == "Al Kharid"
    assert transports.nearest_bank(3270, 3165, 3) is None or transports.nearest_bank(3270, 3165, 3).tile[2] == 3


# --- the header guard ------------------------------------------------------------------------

def test_a_table_whose_header_changed_refuses_to_load(tmp_path):
    bad = tmp_path / "transports.tsv"
    bad.write_text("# From\tTo\tThing\n1 2 0\t3 4 0\tOpen Door 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Origin"):
        transports.load_links(directory=str(tmp_path))
