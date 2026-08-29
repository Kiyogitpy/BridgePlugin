"""Tests for worldmap.py, the global collision map and pathfinder.

RE-MEASURED 2026-08-29 against the CacheMapDumper replacement asset (the original EthanApi dump
predates the Varlamore region and had ZERO data there -- see
.superpowers/sdd/2026-08-29-global-collision-pathfinding/map-swap-report.md for the coverage
measurement that motivated the swap). Route lengths, door/furniture geometry and reachable_from
distances all came back IDENTICAL to the pre-swap numbers for every region the old asset already
covered -- the new dump appears to be the same underlying source data, just repackaged (a
different RoaringBitmap container layout, WITH run containers, and the opposite bit polarity).
The one behaviour that is NOT identical is `EXPECTED_BLOCKED_BITS_PER_PLANE`'s meaning and
`test_unfloored_upper_planes_default_to_blocked`, both called out at their own definitions below.
This suite touches no bridge and no serial port -- it is the first pathing code in this repo that
can be tested without a running client.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import worldmap


# MEASURED 2026-08-29 against the CacheMapDumper asset. Doubles as a corruption check: any re-dump
# or a truncated checkout changes these immediately.
#
# RENAMED from EXPECTED_BITS_PER_PLANE, and the MEANING INVERTS along with the map's own polarity
# (Indexer.SET_MEANS_PASSABLE is now False -- see worldmap.Indexer's docstring): a set bit here
# means the edge is BLOCKED, not passable. Planes 1-3 carrying far more bits than plane 0 is still
# not a bug, but the reason changed: the OLD asset explicitly marked nearly all of the open sky
# above ground level as PASSABLE (SET, old polarity), which made an absent/unfloored upper-plane
# tile read as walkable -- documented as a known leak ("no data defaults to passable... lets a
# route leak through the void"). The NEW asset explicitly marks nearly all of that same open sky
# as BLOCKED instead (see test_unfloored_upper_planes_default_to_blocked) -- so the huge plane 1-3
# counts now come from real, deliberate "you cannot stand here, there is no floor" data rather
# than a blanket "assume passable" one. Confirmed this is real asset data, not a reader bug: a
# genuinely-absent container (a key with no entry at all) still resolves to passable under the new
# polarity, exactly as the code says it should.
EXPECTED_BLOCKED_BITS_PER_PLANE = {0: 10902143, 1: 23429671, 2: 23756697, 3: 23915422}

CARDINALS = ((0, 1), (0, -1), (1, 0), (-1, 0))


def test_asset_matches_the_measured_bit_counts():
    assert worldmap.bits_per_plane() == EXPECTED_BLOCKED_BITS_PER_PLANE


def test_upper_planes_carry_real_wall_geometry():
    """ROSS's upper floor, the geometry measured live 2026-08-29: the player at (2617,3317)
    is walled off to the east and south, which is what Shortest Path cannot tell us at all."""
    assert worldmap.north(2617, 3317, 1) is True
    assert worldmap.east(2617, 3317, 1) is False
    assert worldmap.south(2617, 3317, 1) is False
    assert worldmap.west(2617, 3317, 1) is True


def test_unfloored_upper_planes_default_to_blocked():
    """REVERSED FINDING, 2026-08-29: this used to be test_no_data_defaults_to_passable, pinning
    the OLD asset's documented leak ("open sky above an outdoor tile reads as walkable"). The new
    CacheMapDumper asset does the OPPOSITE: every one of these outdoor plane-1/2 spots -- none of
    them under any building -- now reads BLOCKED on all four cardinals. Confirmed this is real
    asset data and not a reader bug (a genuinely-absent container key resolves to passable exactly
    as worldmap.py's own logic says it should, see the map-swap report); the new dump evidently
    marks unfloored upper-plane tiles as explicitly blocked rather than leaving them unmarked.
    This is arguably a FIX of the old leak, not a regression -- but it is a real behaviour change
    from what this suite's docstring used to assert as measured fact, so it is called out here
    rather than silently folded into a renumbered test. worldmap.py's own module docstring (item
    1) has been updated to match."""
    for plane in (1, 2):
        for x, y in ((3213, 3424), (3100, 3300), (3200, 3200), (2700, 3500), (3000, 3000)):
            for dx, dy in CARDINALS:
                assert worldmap.passable(x, y, plane, dx, dy) is False


@pytest.mark.parametrize("x,y", [(3037, 3347), (1746, 3534)])
def test_doors_are_baked_open(x, y):
    """Falador's and Barbara's doors. Deliberate -- CacheMapDumper's Exclusion enum does this.
    The whole model is: route straight through the door, then open it when you reach it."""
    for dx, dy in CARDINALS:
        assert worldmap.passable(x, y, 0, dx, dy) is True


@pytest.mark.parametrize("x,y", [(2617, 3318), (1749, 3536)])
def test_furniture_tiles_are_solid(x, y):
    """ROSS's Broken Range and Barbara's hotspot. A hotspot's own tile can never be routed TO,
    which is why find_path grows a goal_adjacent mode in Task 2."""
    for dx, dy in CARDINALS:
        assert worldmap.passable(x, y, 0, dx, dy) is False


def test_south_and_west_are_read_off_the_neighbour():
    """The map stores only NORTH and EAST per tile; the other two are the neighbour's bits."""
    assert worldmap.south(2617, 3317, 1) == worldmap.north(2617, 3316, 1)
    assert worldmap.west(2617, 3317, 1) == worldmap.east(2616, 3317, 1)


def test_indexer_packs_the_documented_layout():
    """RE-MEASURED 2026-08-29 for the CacheMapDumper layout, which packs x with a BASE of 480
    (`(x - 480) & 0xFFF`) rather than the old EthanApi layout's bare `x & mask` -- see
    worldmap.Indexer's own docstring. Note packed(0, 0, 0) is no longer 0 under this layout (it
    wraps to 3616, since 0 - 480 is negative); the origin for this map is (480, 0, 0)."""
    assert worldmap.Indexer.packed(480, 0, 0) == 0
    assert worldmap.Indexer.packed(481, 0, 0) == 1
    assert worldmap.Indexer.packed(480, 1, 0) == 1 << 12
    assert worldmap.Indexer.packed(480, 0, 1) == 1 << 26


def test_set_means_passable_is_actually_honoured_not_decorative(monkeypatch):
    """north()/east() used to return the raw bit and never read SET_MEANS_PASSABLE at all, so
    flipping the constant changed nothing -- a future map with inverted polarity (this codebase's
    own Indexer docstring names Vitalite's map.dat as exactly that: same shape, SET means BLOCKED)
    would silently produce an inverted map. Asserting the constant's VALUE (as the old version of
    this test did) passes regardless of whether the code honours it; this asserts the BEHAVIOUR
    instead, against a real, known bit from the bundled asset (see
    test_upper_planes_carry_real_wall_geometry): worldmap.north(2617, 3317, 1) is True under the
    real (False, since the 2026-08-29 map swap -- see Indexer's docstring) polarity. Flip the flag
    and the same real bit must now read as impassable."""
    assert worldmap.north(2617, 3317, 1) is True
    monkeypatch.setattr(worldmap.Indexer, "SET_MEANS_PASSABLE", True)
    assert worldmap.north(2617, 3317, 1) is False


import time


# MEASURED 2026-08-29. BFS on unit-weight edges gives a unique shortest LENGTH regardless of
# neighbour visiting order, so these are stable even if the particular tiles chosen differ.
def test_routes_upstairs_where_shortest_path_returns_nothing():
    path = worldmap.find_path(2617, 3317, 1, 2617, 3316)
    assert path is not None
    assert len(path) == 4
    assert path[0] == (2617, 3317)
    assert path[-1] == (2617, 3316)


def test_routes_from_outside_a_house_to_inside_it():
    """Straight through Ross's front door, which the map treats as open."""
    path = worldmap.find_path(2614, 3319, 0, 2616, 3318)
    assert path is not None and len(path) == 30


def test_routes_across_a_city():
    path = worldmap.find_path(3013, 3355, 0, 3037, 3346)
    assert path is not None and len(path) == 42


def test_routes_between_cities():
    path = worldmap.find_path(2965, 3379, 0, 3213, 3424)
    assert path is not None and len(path) == 314


def test_a_cross_city_route_stays_fast_enough_to_run_every_hop():
    started = time.time()
    worldmap.find_path(2965, 3379, 0, 3213, 3424)
    assert time.time() - started < 1.0


def test_a_furniture_goal_has_no_exact_route_but_an_adjacent_one():
    """ROSS's Broken Range at (2617,3318) is furniture, so nothing can route ONTO it. This is
    what travel._routable_goal used to paper over; goal_adjacent replaces it."""
    assert worldmap.find_path(2614, 3319, 0, 2617, 3318) is None
    adjacent = worldmap.find_path(2614, 3319, 0, 2617, 3318, goal_adjacent=True)
    assert adjacent is not None and len(adjacent) == 4


def test_goal_adjacent_succeeds_immediately_when_already_beside_the_goal():
    """Barbara's, the live geometry from 2026-08-29: the player stood one tile from a hotspot
    it could not reach. Being adjacent is the routing answer; whether the EDGE is open is
    travel.can_reach's question, not this one's."""
    path = worldmap.find_path(1750, 3536, 0, 1749, 3536, goal_adjacent=True)
    assert path == [(1750, 3536)]


def test_a_walled_off_goal_returns_none_rather_than_a_wrong_route():
    assert worldmap.find_path(1750, 3536, 0, 1749, 3536) is None


def test_the_node_cap_bounds_a_hopeless_search():
    assert worldmap.find_path(2965, 3379, 0, 3213, 3424, max_nodes=100) is None


def test_reachable_from_returns_distances_including_the_start():
    reach = worldmap.reachable_from(2617, 3317, 1)
    assert reach[(2617, 3317)] == 0
    assert (2617, 3316) in reach
    assert reach[(2617, 3316)] == 3
    # East and south are walled off at this tile; nothing beyond them is in the fill.
    assert (2618, 3317) not in reach


def test_reachable_from_respects_its_radius():
    reach = worldmap.reachable_from(3213, 3424, 0, radius=3, max_nodes=10_000)
    assert reach
    for x, y in reach:
        assert max(abs(x - 3213), abs(y - 3424)) <= 3


def test_reachable_from_never_exceeds_its_node_cap():
    """The cap is a HARD bound -- the neighbour loop used to overshoot it by up to 4."""
    for cap in (1, 5, 37):
        reach = worldmap.reachable_from(3213, 3424, 0, radius=50, max_nodes=cap)
        assert len(reach) <= cap


@pytest.mark.parametrize("sx,sy,plane,gx,gy", [
    (2617, 3317, 1, 2617, 3316),
    (2614, 3319, 0, 2616, 3318),
    (3013, 3355, 0, 3037, 3346),
    (2965, 3379, 0, 3213, 3424),
])
def test_every_returned_route_is_contiguous_and_walkable(sx, sy, plane, gx, gy):
    """Length alone cannot catch a BFS that returns the wrong tiles. Each consecutive pair
    must be a cardinal neighbour AND the edge between them must actually be passable."""
    path = worldmap.find_path(sx, sy, plane, gx, gy)
    assert path is not None
    assert path[0] == (sx, sy)
    assert path[-1] == (gx, gy)
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        dx, dy = bx - ax, by - ay
        assert abs(dx) + abs(dy) == 1, f"{(ax, ay)} -> {(bx, by)} is not a cardinal step"
        assert worldmap.passable(ax, ay, plane, dx, dy), f"{(ax, ay)} -> {(bx, by)} is blocked"


# ============================================================================================
# COVERAGE REGRESSION -- added 2026-08-29 (map-swap task). The defect this whole task fixed was
# never caught by any test: the old EthanApi asset predates the Varlamore region and had ZERO
# data there (0/400 sampled edges passable, over a 60x60 box around each golem-region anchor),
# which made every walk_to in routines/golem_crafting.py fail before ever clicking. Nothing here
# checked that the map actually COVERS the tiles the routines depend on -- it only checked that a
# handful of specific, already-known-good regions behaved correctly. This is what closes that gap:
# every hard-coded world-tile constant found in routines/*.py that is actually passed to
# travel.walk_to (and therefore worldmap.find_path/reachable_from underneath it) gets asserted
# against real map coverage nearby.
#
# Gathered from:
#   - routines/golem_crafting.py's DEFAULT_CONFIG["locations"] (mining/plinth/goatPit/
#     spikesSupply/bank) -- the exact anchors the original bug report measured as 0/400.
#   - routines/falconry.py's TILE_* module constants (HUNT/FALCONER/BANK/FAIRY_RING/STILE),
#     which are the only other module-level hard-coded {"x","y","plane"} dicts in routines/ that
#     are actually passed to travel.walk_to.
#
# Deliberately NOT included:
#   - routines/mahogany_homes.py's CITY_TELEPORTS -- checked and confirmed to carry no
#     coordinates at all (it maps home name -> (teleport item name, optional NPC name) only;
#     every real coordinate that routine uses comes live off the bridge, never hard-coded).
#   - routines/pest_control.py's stagingBoxCorner1/2 and voidKnightTile -- real x/y values, but
#     sampled live INSIDE Pest Control's instanced arena, which is never passed to
#     travel.walk_to/worldmap (grepped: pest_control.py never calls either). The routine works
#     around the instancing gotcha in CLAUDE.md by clicking a hardcoded minimap PIXEL instead of
#     doing any world-coordinate routing, so these are not really "map anchors" in the sense this
#     regression test cares about, and adding them would test nothing this map is used for.
#
# The check is "is there real coverage NEAR this anchor", not "is the anchor tile itself
# walkable" -- five of the five golem_crafting anchors are deliberately the OBJECT's own tile
# (the sunstone monolith, an Empty plinth, the Goat Pit, the Spikes supply object, the Bank
# chest -- see that file's own DEFAULT_CONFIG comment), and an object's own tile is furniture by
# definition, unwalkable on every edge, exactly like every other furniture tile already pinned
# elsewhere in this suite (test_furniture_tiles_are_solid). Asserting the exact anchor tile is
# walkable would fail against CORRECT data. What actually distinguishes "the map has no data
# here" (the real 2026-08-14 bug) from "this exact tile is furniture, but the ground around it is
# real" is whether the SURROUNDING area has any passable edge at all -- which is exactly what the
# original bug report's own 60x60-box sampling measured.
ROUTINE_ANCHORS = [
    ("golem_crafting.mining", 2599, 2248, 0),
    ("golem_crafting.plinth", 2595, 2253, 0),
    ("golem_crafting.goatPit", 2572, 2195, 0),
    ("golem_crafting.spikesSupply", 2578, 2202, 0),
    ("golem_crafting.bank", 2587, 2260, 0),
    ("falconry.TILE_HUNT", 2388, 3590, 0),
    ("falconry.TILE_FALCONER", 2379, 3603, 0),
    ("falconry.TILE_BANK", 2443, 3083, 0),
    ("falconry.TILE_FAIRY_RING", 2655, 3229, 0),
    ("falconry.TILE_STILE", 2370, 3621, 0),
]

ANCHOR_COVERAGE_RADIUS = 10  # a dense scan of a 21x21 box -- comfortably bigger than any single
                             # furniture object, well inside the 60x60 box the original bug used


@pytest.mark.parametrize("name,x,y,plane", ROUTINE_ANCHORS, ids=[a[0] for a in ROUTINE_ANCHORS])
def test_every_hardcoded_routine_anchor_has_nearby_map_coverage(name, x, y, plane):
    found = False
    for dx in range(-ANCHOR_COVERAGE_RADIUS, ANCHOR_COVERAGE_RADIUS + 1):
        for dy in range(-ANCHOR_COVERAGE_RADIUS, ANCHOR_COVERAGE_RADIUS + 1):
            if any(worldmap.passable(x + dx, y + dy, plane, cdx, cdy) for cdx, cdy in CARDINALS):
                found = True
                break
        if found:
            break
    assert found, (
        f"{name} at ({x},{y},{plane}) has NO passable edge anywhere within "
        f"{ANCHOR_COVERAGE_RADIUS} tiles -- this is exactly the coverage hole that made every "
        f"walk_to using this anchor fail instantly against the old asset"
    )


def test_the_golem_region_route_that_originally_motivated_this_task_now_exists():
    """The literal repro from the bug report: worldmap.find_path(2599, 2248, 0, 2597, 2257)
    used to return None purely because the old asset had zero data for the whole region.

    That EXACT call still returns None on the new asset too -- but for an unrelated, legitimate
    reason: (2599, 2248) is the sunstone monolith's own tile (see ROUTINE_ANCHORS' comment,
    above), which is furniture and has literally no outgoing edge to step off from -- confirmed
    directly below (all four cardinals False), and independently already known live (golem_
    crafting.py's own _nearest_walkable_tile exists specifically because "the configured (2599,
    2248) anchor tile itself is blocked/unwalkable"). A BFS cannot leave a tile with zero open
    edges no matter how complete the surrounding map is, so no data fix could ever make that
    exact call succeed -- this was true before the swap and remains true after it.

    What the swap actually fixes is proven by routing from the nearest WALKABLE tile instead
    (exactly what golem_crafting.py's handle_missing_target already substitutes in this case):
    that route now exists, is short, and is fast -- none of which was possible against the old
    asset (0/400 sampled edges passable anywhere in a 60x60 box around either endpoint)."""
    assert worldmap.find_path(2599, 2248, 0, 2597, 2257) is None
    for dx, dy in CARDINALS:
        assert worldmap.passable(2599, 2248, 0, dx, dy) is False

    path = worldmap.find_path(2601, 2248, 0, 2597, 2257)
    assert path is not None
    assert len(path) == 20
