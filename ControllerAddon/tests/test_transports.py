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


# --- the Microbot table, merged through an adapter (plan E-pre, 2026-09-20) --------------------
#
# Microbot's fork of Shortest Path's transports.tsv (vendor/microbot/, commit 8bcc652) is newer and
# larger than the jar's copy and HAS the Karuulm Slayer Dungeon. Its columns differ (Item IDs are
# numeric, plus Currency / isMembers / Varplayers) and its object cell is `Option;Target;id`.
# MEASURED before writing these: 5731 Microbot rows, 5168 ours, 5043 shared by
# (origin, dest, object id), 649 only in Microbot, 99 only in ours, no object cell without ';'.

def _ours_only():
    return transports.load_links(microbot=False)


def test_the_karuulm_elevator_comes_from_the_microbot_table():
    """Ours has no Karuulm row at all (grep 34359 -> 0); Microbot's section has 29."""
    assert not [s for s in _ours_only() if s.object_id == 34359]
    step = _link((1311, 3806, 0), (1311, 10189, 0))
    assert (step.option, step.target, step.object_id) == ("Activate", "Elevator", 34359)
    assert step.kind == transports.LINK and step.source == transports.MICROBOT
    back = _link((1311, 10189, 0), (1311, 3806, 0))
    assert (back.option, back.target, back.object_id) == ("Exit", "Cave exit", 34514)


def test_a_microbot_object_cell_is_normalised_to_the_space_form():
    """Every consumer (legs.trailing_int, crossfloor's keys, the logs) reads ONE format."""
    assert transports.normalise_object_info("Enter;Cave Entrance;3759") == "Enter Cave Entrance 3759"
    assert transports.normalise_object_info("Climb-up Ladder 1") == "Climb-up Ladder 1"
    assert transports.split_object_info("Enter Cave Entrance 3759") == ("Enter", "Cave Entrance", 3759)
    step = _link((1311, 3806, 0), (1311, 10189, 0))
    assert step.object_info == "Activate Elevator 34359"


def test_no_microbot_option_contains_whitespace_so_the_space_form_is_unambiguous():
    """The space form puts the option FIRST and splits on whitespace; an option with a space in
    it would be mis-split silently. Pinned against the data, not assumed."""
    for step in transports.load_microbot_links():
        assert " " not in step.option, step.object_info


def test_every_shortest_path_row_still_resolves_identically():
    """The merge ADDS; it never replaces or reorders one of our rows."""
    ours = _ours_only()
    merged = transports.load_links()
    assert len(merged) >= len(ours) + 600
    fields = ("origin", "dest", "object_info", "skills", "items", "quests", "varbits", "varplayers",
              "duration", "display", "kind", "source")
    for a, b in zip(ours, merged):
        assert all(getattr(a, f) == getattr(b, f) for f in fields), (a, b)
    assert all(s.source == transports.SHORTEST_PATH for s in ours)


def test_a_row_both_tables_hold_is_not_double_counted():
    """Keyed by (origin, dest, object id). Our own duplicates (26 keys) stay as they are."""
    ours = _ours_only()
    merged = transports.load_links()
    key = lambda s: (s.origin, s.dest, s.object_id)
    our_keys = {key(s) for s in ours}
    for k in our_keys:
        assert sum(1 for s in merged if key(s) == k) == sum(1 for s in ours if key(s) == k)
    assert all(key(s) not in our_keys for s in merged if s.source == transports.MICROBOT)


def test_microbot_item_ids_are_numeric_and_fail_closed():
    """The Taverley dusty-key gate: Microbot's row carries item 1590 in `Item IDs`. Ours holds
    the SAME key (origin, dest, 2623) with the symbolic clause `DUSTY_KEY=1`, so the merge keeps
    our row and the Microbot copy is only reachable through load_microbot_links -- which is
    where the numeric grammar is pinned. `A B;C` is (A or B) and C, as Transport.java splits it."""
    merged = _link((2924, 9803, 0), (2923, 9803, 0))
    assert merged.source == transports.SHORTEST_PATH and merged.items == "DUSTY_KEY=1"
    gate = next(s for s in transports.load_microbot_links()
                if s.origin == (2924, 9803, 0) and s.dest == (2923, 9803, 0))
    assert gate.object_id == 2623 and gate.item_ids == ((1590,),) and gate.items == ""
    assert not transports.usable(gate)
    assert not transports.usable(gate, owned_item_ids={1591})
    assert transports.usable(gate, owned_item_ids={1590})
    assert transports.parse_item_ids("1 2;3") == ((1, 2), (3,))
    assert transports.parse_item_ids("") == ()


def test_microbot_currency_fails_closed_until_the_coins_are_proven():
    """The Al Kharid toll gate is `Open Gate 44050` in ours and `Pay-toll(10gp);Gate;2786` in
    Microbot's -- different object ids, so BOTH survive the merge (the executor finds an object
    by id, and a stale id fails cleanly)."""
    ours = _link((3267, 3227, 0), (3268, 3227, 0))
    assert ours.object_id == 44050 and ours.source == transports.SHORTEST_PATH
    toll = next(s for s in transports.load_links()
                if s.origin == (3267, 3227, 0) and s.dest == (3268, 3227, 0) and s.object_id == 2786)
    assert toll.source == transports.MICROBOT
    assert toll.option.startswith("Pay-toll") and toll.currency == (10, "Coins")
    assert not transports.usable(toll)
    assert not transports.usable(toll, currency_held={"Coins": 9})
    assert transports.usable(toll, currency_held={"Coins": 10})
    assert transports.usable(_link((1311, 3806, 0), (1311, 10189, 0)))     # the elevator needs nothing


def test_a_microbot_table_whose_header_changed_refuses_to_load(tmp_path):
    ours = os.path.join(transports.VENDOR_DIR, "transports.tsv")
    (tmp_path / "transports.tsv").write_bytes(open(ours, "rb").read())
    (tmp_path / "microbot").mkdir()
    (tmp_path / "microbot" / "transports.tsv").write_text(
        "# Origin\tDestination\tmenuOption menuTarget objectID\tItems\n1 2 0\t3 4 0\tOpen;Door;1\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="Item IDs"):
        transports.load_links(directory=str(tmp_path))
