import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import teleports


# --- parsing the vendored table -----------------------------------------------------------

def test_parses_a_known_row_out_of_the_real_vendored_table():
    """Ring of dueling -> Ferox Enclave is the row banker.py has been hand-rolling all along
    (travel.RING_OF_DUELING_NAME + a hardcoded destination), so it is the honest canary that the
    vendored table says what we think it says."""
    ferox = [t for t in teleports.load()
             if t.display == "Ring of dueling: Ferox Enclave"]
    assert len(ferox) == 1
    tp = ferox[0]

    assert tp.dest == (3151, 3635, 0)
    assert tp.item_name == "Ring of dueling"
    assert tp.destination_name == "Ferox Enclave"
    # Every charge variant counts, (8) down to (1) -- this is the OR list, and matching only the
    # full-charge id would miss a ring that has been used.
    assert 2552 in tp.item_ids and 2566 in tp.item_ids
    assert tp.max_wilderness_level == 20


def test_header_is_asserted_so_an_upstream_column_reorder_fails_loudly(tmp_path):
    """The columns differ BETWEEN Shortest Path's own transport files (teleportation_spells.tsv
    puts Items second, teleportation_items.tsv puts it fourth), so a silent re-order would
    mis-parse every row into plausible-looking nonsense rather than erroring. Same discipline as
    M8aqProviderTest.knownPathsAreStable."""
    bad = tmp_path / "t.tsv"
    bad.write_text("# Destination\tItems\tSkills\n3151 3635 0\t2552=1\t\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        teleports.load(path=str(bad))


def test_skips_rows_with_no_destination_or_no_item():
    """20 of the 340 rows are blanks/section markers carrying neither. A teleport we cannot
    locate or cannot own is not a candidate."""
    for tp in teleports.load():
        assert tp.dest is not None
        assert tp.item_ids


def test_table_is_not_silently_truncated():
    """A floor, not an exact count, so re-vendoring a newer table doesn't fail the build --
    same shape as M8aqProviderTest's MINIMUM_EXPECTED_READERS assertion."""
    assert len(teleports.load()) >= 300


# --- ownership ----------------------------------------------------------------------------

def test_owned_matches_any_one_of_the_or_alternatives():
    tps = [teleports.Teleport(dest=(1, 2, 0), item_ids=frozenset({2552, 2566}),
                              display="Ring of dueling: X", item_name="Ring of dueling",
                              destination_name="X", duration=4, max_wilderness_level=20,
                              consumable=False)]
    # Only the nearly-spent ring, not the full one.
    assert teleports.owned(tps, {2566}) == tps
    assert teleports.owned(tps, {9999}) == []


# --- choosing -----------------------------------------------------------------------------

def _tp(dest, name="Item: Dest", wild=20, ids=(1,)):
    return teleports.Teleport(dest=dest, item_ids=frozenset(ids), display=name,
                              item_name=name.split(":")[0].strip(),
                              destination_name=name.split(":")[-1].strip(),
                              duration=4, max_wilderness_level=wild, consumable=True)


def test_picks_the_destination_that_leaves_the_shortest_walk():
    near, far = _tp((10, 10, 0), "A: near"), _tp((99, 99, 0), "B: far")
    lengths = {(10, 10, 0): 12, (99, 99, 0): 140}

    best = teleports.choose([far, near], route_len=lengths.get, walk_len=300)
    assert best is near


def test_walks_rather_than_burning_a_teleport_for_a_trivial_saving():
    """A tablet is worth more than 20 tiles of walking."""
    tp = _tp((10, 10, 0))
    assert teleports.choose([tp], route_len=lambda d: 80, walk_len=90) is None


def test_skips_a_teleport_that_cannot_be_used_this_deep_in_the_wilderness():
    """Krystilia's tasks are IN the wilderness, so this is the live case, not a corner one: a
    level-20 teleport simply does not fire at level 25 and the click would be wasted."""
    tp = _tp((10, 10, 0), wild=20)
    assert teleports.choose([tp], route_len=lambda d: 5, walk_len=300,
                            wilderness_level=25) is None
    assert teleports.choose([tp], route_len=lambda d: 5, walk_len=300,
                            wilderness_level=15) is tp


def test_skips_a_destination_the_router_cannot_reach():
    """route_len returns None when worldmap has no route from that landing spot -- teleporting
    somewhere we then cannot walk out of is strictly worse than not teleporting."""
    tp = _tp((10, 10, 0))
    assert teleports.choose([tp], route_len=lambda d: None, walk_len=300) is None


def test_skips_player_specific_house_destinations():
    """The three 'Teleport to House tablet' rows carry whatever coordinates the table's author
    had; a player's actual house could be anywhere, so the destination is not a fact about us."""
    tp = _tp((1923, 5709, 0), "Teleport to House tablet")
    assert teleports.choose([tp], route_len=lambda d: 1, walk_len=300) is None


def test_no_candidates_is_not_an_error():
    assert teleports.choose([], route_len=lambda d: 1, walk_len=300) is None
