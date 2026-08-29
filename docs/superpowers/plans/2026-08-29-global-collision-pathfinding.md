# Global Collision Pathfinding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Shortest Path and travel.py's door heuristics with a world-wide per-edge collision bitmap read from Python, giving every walk a real route on every plane before it takes a step.

**Architecture:** A new pure-Python module `worldmap.py` lazily indexes a 3.6 MB RoaringBitmap collision map (all four planes) and runs cardinal BFS over it. `travel.py` walks that route, and because it has a route, the door question collapses to "which shut door stands on the next step" — deleting ~700 lines of heuristics. Object-interaction reachability (`travel.can_reach`) deliberately stays on live collision flags; the static map cannot answer it.

**Tech Stack:** Python 3 (stdlib only — `struct`, `gzip`, `collections.deque`), pytest. No Java change, no client rebuild, no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-29-global-collision-pathfinding-design.md`

## Global Constraints

- **Run everything from `ControllerAddon/`.** Tests are `python -m pytest tests/`.
- **The asset goes in `ControllerAddon/maps/`, NOT `ControllerAddon/data/`.** The repo `.gitignore` line 11 is a bare `data/`, which matches a directory named `data` at any depth — `git check-ignore -v ControllerAddon/data/collision-map.gz` confirms it would be silently ignored. `ControllerAddon/maps/` is not ignored.
- **Ethan's map packing, verbatim:** `(x & 16383) | ((y & 16383) << 14) | (plane << 28)`, east-edge flag `1 << 30`, and **a set bit means PASSABLE**. Vitalite's map and CacheMapDumper's output both use different packing AND inverted polarity — never mix them.
- **`travel.can_reach` and `GET /collision` must not change.** They are the only thing that answers object-interaction adjacency correctly (the JEFF measurement in the spec).
- **Every Python test must be hermetic.** The `no_live_bridge` autouse fixture in `tests/test_travel.py` exists because live-bridge fallthrough made door tests pass or fail depending on where the character was standing.
- **Before any relaunch:** `python -m py_compile` every touched file, then run the full suite. Pytest alone is not sufficient — some modules are imported by no test.

---

### Task 1: `worldmap.py` — map asset, indexer, edge reads

**Files:**
- Create: `ControllerAddon/maps/collision-map.gz` (copied asset, 3.6 MB)
- Create: `ControllerAddon/worldmap.py`
- Test: `ControllerAddon/tests/test_worldmap.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `worldmap.north/east/south/west(x, y, plane) -> bool`,
  `worldmap.passable(x, y, plane, dx, dy) -> bool`,
  `worldmap.bits_per_plane() -> dict[int, int]`,
  `worldmap.Indexer` (class attributes `X_MASK`, `Y_MASK`, `Y_SHIFT`, `PLANE_SHIFT`, `EAST_FLAG`, `SET_MEANS_PASSABLE`, classmethod `packed(x, y, plane) -> int`).

- [ ] **Step 1: Copy the map asset into the repo**

```bash
cd /c/Users/Kiyo/Documents/Httpbridge/ControllerAddon
mkdir -p maps
cp ../data/decompiled/PacketUtils/src/main/resources/com/example/EthanApiPlugin/PathFinding/map maps/collision-map.gz
ls -la maps/collision-map.gz
git check-ignore -v maps/collision-map.gz || echo "NOT IGNORED - good"
```

Expected: the file is ~3,684,258 bytes and `git check-ignore` prints "NOT IGNORED - good".

- [ ] **Step 2: Write the failing test**

Create `ControllerAddon/tests/test_worldmap.py`:

```python
"""Tests for worldmap.py, the global collision map and pathfinder.

Every number here was MEASURED against the real asset on 2026-08-29 and is reproduced in
docs/superpowers/specs/2026-08-29-global-collision-pathfinding-design.md. This suite touches
no bridge and no serial port -- it is the first pathing code in this repo that can be tested
without a running client.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import worldmap


# MEASURED 2026-08-29. Doubles as a corruption check on the 3.6 MB asset: any re-dump or a
# truncated checkout changes these immediately. Planes 1-3 carrying MORE bits than plane 0 is
# not a bug -- upper planes are mostly open sky, and no data defaults to passable.
EXPECTED_BITS_PER_PLANE = {0: 10208962, 1: 22268102, 2: 24304088, 3: 25406620}

CARDINALS = ((0, 1), (0, -1), (1, 0), (-1, 0))


def test_asset_matches_the_measured_bit_counts():
    assert worldmap.bits_per_plane() == EXPECTED_BITS_PER_PLANE


def test_upper_planes_carry_real_wall_geometry():
    """ROSS's upper floor, the geometry measured live 2026-08-29: the player at (2617,3317)
    is walled off to the east and south, which is what Shortest Path cannot tell us at all."""
    assert worldmap.north(2617, 3317, 1) is True
    assert worldmap.east(2617, 3317, 1) is False
    assert worldmap.south(2617, 3317, 1) is False
    assert worldmap.west(2617, 3317, 1) is True


def test_no_data_defaults_to_passable():
    """Open sky above an outdoor tile. This is a KNOWN LIMITATION, pinned so nobody "fixes" a
    route that leaks through the void without realising the map allows it by design."""
    for plane in (1, 2):
        for dx, dy in CARDINALS:
            assert worldmap.passable(3213, 3424, plane, dx, dy) is True


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
    assert worldmap.Indexer.packed(0, 0, 0) == 0
    assert worldmap.Indexer.packed(1, 0, 0) == 1
    assert worldmap.Indexer.packed(0, 1, 0) == 1 << 14
    assert worldmap.Indexer.packed(0, 0, 1) == 1 << 28
    assert worldmap.Indexer.SET_MEANS_PASSABLE is True
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_worldmap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'worldmap'`

- [ ] **Step 4: Write the implementation**

Create `ControllerAddon/worldmap.py`:

```python
"""The global collision map: one bit per tile-EDGE, world-wide, on all four planes.

Ported from EthanApi's GlobalCollisionMap (data/decompiled/PacketUtils/, documentation only).
This exists because Shortest Path CANNOT ROUTE ON PLANE 1 AT ALL -- measured 2026-08-29, every
plane-1 target returns active:false/done:false forever, because its dumped collision map is
ground level only. Upstairs we therefore had no route, and with no route travel.py fell through
to an obstacle-blind greedy walker and a pile of door heuristics standing in for the route it
could not compute.

Pure and offline: no bridge, no serial port, no client. That makes it the first pathing code
here that can be unit-tested against real data instead of against wherever the character
happens to be standing.

THREE THINGS TO KNOW BEFORE TRUSTING A ROUTE FROM THIS (all MEASURED, see the design doc):

  1. No data defaults to PASSABLE. Planes 1-3 are mostly open sky and read as walkable
     everywhere outside a building, so a missing exterior wall upstairs lets a route leak
     through the void. The walker's stall detection is the backstop.
  2. An object's own blocking is baked into the same bits as a wall's, so this map CANNOT tell
     furniture from a wall. It must never be used to answer "can I interact with that object
     from here" -- travel.can_reach does that from LIVE collision flags, where
     BLOCK_MOVEMENT_OBJECT and the directional MOVEMENT_* bits stay distinguishable.
  3. Doors are baked OPEN, deliberately (CacheMapDumper's Exclusion enum is the curated list
     that does it). Route straight through the door; open it when you get there.

The map is a static snapshot. A game update that moves a wall makes a route wrong, and the
symptom is a walk that stalls at one specific tile. CacheMapDumper can regenerate it -- see the
design doc's "Deferred" section, and keep Indexer as the single place that knows the layout.
"""

import gzip
import os
import struct
from collections import deque

MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps", "collision-map.gz")


class Indexer:
    """Where the bit layout and the polarity live, and the ONLY place that should know them.

    There are three of these maps in data/decompiled/ and they look alike while meaning
    opposite things:

      EthanApi (this one)  x & 16383 | y << 14 | plane << 28, bit 30 = east   set = PASSABLE
      Vitalite map.dat     x &  8191 | y << 13 | plane << 28, bit 30 = east   set = BLOCKED
      CacheMapDumper out   12-bit x (base 480), 14-bit y, 2-bit plane          set = BLOCKED

    Reading one with another's indexer produces a map that is subtly wrong rather than
    obviously broken, so switching source means changing this class and nothing else.
    """

    X_MASK = 16383
    Y_MASK = 16383
    Y_SHIFT = 14
    PLANE_SHIFT = 28
    EAST_FLAG = 1 << 30
    SET_MEANS_PASSABLE = True

    # A RoaringBitmap container key is the top 16 bits of the value, so plane (value bits
    # 28-29) lands at key bits 12-13. Used by bits_per_plane, which reads only the header.
    PLANE_KEY_SHIFT = 12
    PLANE_KEY_MASK = 3

    @classmethod
    def packed(cls, x: int, y: int, plane: int) -> int:
        return (x & cls.X_MASK) | ((y & cls.Y_MASK) << cls.Y_SHIFT) | (plane << cls.PLANE_SHIFT)


class _Roaring:
    """Minimal reader for RoaringBitmap's portable serialization.

    Deliberately LAZY: it indexes the container header once (~0.13s) and binary-searches or
    bit-tests directly in the 78 MB buffer, rather than materialising 82 million integers.
    """

    SERIAL_COOKIE_NO_RUNCONTAINER = 12346
    ARRAY_CONTAINER_MAX = 4096

    def __init__(self, data: bytes):
        self._data = data
        cookie, size = struct.unpack_from("<II", data, 0)
        if cookie != self.SERIAL_COOKIE_NO_RUNCONTAINER:
            raise ValueError(
                f"unsupported RoaringBitmap cookie {cookie}; this reader handles only the "
                f"no-run-container portable format ({self.SERIAL_COOKIE_NO_RUNCONTAINER})"
            )
        self._containers = {}
        descriptive = 8
        offsets = 8 + size * 4
        for i in range(size):
            key, card_minus_one = struct.unpack_from("<HH", data, descriptive + i * 4)
            offset = struct.unpack_from("<I", data, offsets + i * 4)[0]
            self._containers[key] = (card_minus_one + 1, offset)

    def contains(self, value: int) -> bool:
        entry = self._containers.get((value >> 16) & 0xFFFF)
        if entry is None:
            return False
        cardinality, offset = entry
        low = value & 0xFFFF
        if cardinality <= self.ARRAY_CONTAINER_MAX:
            lo, hi = 0, cardinality
            while lo < hi:
                mid = (lo + hi) // 2
                probe = struct.unpack_from("<H", self._data, offset + mid * 2)[0]
                if probe < low:
                    lo = mid + 1
                elif probe > low:
                    hi = mid
                else:
                    return True
            return False
        word = struct.unpack_from("<Q", self._data, offset + (low >> 6) * 8)[0]
        return bool((word >> (low & 63)) & 1)

    def cardinality_by_key_bits(self, shift: int, mask: int) -> dict:
        """Set bits grouped by a field of the container key. Header-only, so it is cheap."""
        counts = {}
        for key, (cardinality, _offset) in self._containers.items():
            bucket = (key >> shift) & mask
            counts[bucket] = counts.get(bucket, 0) + cardinality
        return counts


_MAP = None


def _map() -> _Roaring:
    global _MAP
    if _MAP is None:
        with gzip.open(MAP_PATH, "rb") as handle:
            _MAP = _Roaring(handle.read())
    return _MAP


def bits_per_plane() -> dict:
    """Set bits per plane. A corruption check on the asset, and the evidence that upper planes
    carry real data (p1-p3 each hold more than twice plane 0's)."""
    return _map().cardinality_by_key_bits(Indexer.PLANE_KEY_SHIFT, Indexer.PLANE_KEY_MASK)


def north(x: int, y: int, plane: int) -> bool:
    return _map().contains(Indexer.packed(x, y, plane))


def east(x: int, y: int, plane: int) -> bool:
    return _map().contains(Indexer.packed(x, y, plane) | Indexer.EAST_FLAG)


def south(x: int, y: int, plane: int) -> bool:
    return north(x, y - 1, plane)


def west(x: int, y: int, plane: int) -> bool:
    return east(x - 1, y, plane)


def passable(x: int, y: int, plane: int, dx: int, dy: int) -> bool:
    """Can we step from (x, y) to (x+dx, y+dy)? Cardinal steps only -- the map stores no
    diagonal, and the BFS in find_path is cardinal for the same reason."""
    if dx == 1:
        return east(x, y, plane)
    if dx == -1:
        return west(x, y, plane)
    if dy == 1:
        return north(x, y, plane)
    if dy == -1:
        return south(x, y, plane)
    raise ValueError(f"not a cardinal step: ({dx}, {dy})")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_worldmap.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/maps/collision-map.gz ControllerAddon/worldmap.py ControllerAddon/tests/test_worldmap.py
git commit -m "$(printf 'feat(worldmap): global all-plane collision map with a lazy Roaring reader\n\nPorted from EthanApi. Plane 1-3 data is what Shortest Path structurally\ncannot provide. Bit counts pinned as an asset corruption check.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>')"
```

---

### Task 2: `worldmap.find_path` and `reachable_from`

**Files:**
- Modify: `ControllerAddon/worldmap.py` (append)
- Test: `ControllerAddon/tests/test_worldmap.py` (append)

**Interfaces:**
- Consumes: `worldmap.passable` from Task 1.
- Produces:
  - `find_path(sx, sy, plane, gx, gy, *, goal_adjacent=False, max_nodes=MAX_SEARCH_NODES) -> list[tuple[int, int]] | None` — the route **including the start tile**, or `None` when no walking route exists.
  - `reachable_from(x, y, plane, *, radius=REACH_RADIUS, max_nodes=REACH_MAX_NODES) -> dict[tuple[int, int], int]` — tile → step distance, including the start at 0.
  - Constants `MAX_SEARCH_NODES = 250_000`, `REACH_RADIUS = 14`, `REACH_MAX_NODES = 2000`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_worldmap.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_worldmap.py -v -k "route or reachable or furniture or adjacent or node_cap"`
Expected: FAIL with `AttributeError: module 'worldmap' has no attribute 'find_path'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/worldmap.py`:

```python
# A 314-step cross-city route explored 68k nodes in 151ms, so this bounds a genuinely hopeless
# search at roughly 3.5x that -- generous for any real trip, and it makes "no route" a fast,
# definite answer instead of the multi-minute wedge that a routeless walk used to be.
MAX_SEARCH_NODES = 250_000

REACH_RADIUS = 14        # a Mahogany Homes house fits well inside this
REACH_MAX_NODES = 2000   # hard bound so a bad grid cannot spin

_CARDINAL_STEPS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def find_path(sx: int, sy: int, plane: int, gx: int, gy: int, *,
              goal_adjacent: bool = False, max_nodes: int = MAX_SEARCH_NODES):
    """Shortest cardinal route from (sx, sy) to (gx, gy) on `plane`, INCLUDING the start tile.
    None when no walking route exists within `max_nodes`.

    `None` is a first-class answer, not an error. It is the thing this whole change buys: a
    walk with no route now fails in milliseconds instead of hop-clicking a wall until
    WALK_MAX_HOPS x WALK_HOP_TIMEOUT_S runs out (up to 8 minutes, measured).

    SAME PLANE ONLY. There is no ladder edge, no boat, no teleport in this map -- crossing a
    floor is the routine's job (mahogany_homes._climb), and a cross-plane request is a caller
    bug rather than a long search, so it is not attempted.

    `goal_adjacent` stops on any tile cardinally adjacent to the goal. Needed because an
    object's own tile is furniture by definition and can never be routed onto -- MEASURED at
    ROSS's Broken Range and Barbara's hotspot, both of which read solid on every edge. Note
    this asks only "can I get NEXT to it", never "can I interact with it": the static map
    cannot tell furniture from a wall, so travel.can_reach owns that question on live flags.

    Cardinal-only, matching the map's edge model. The client walks its own diagonals, so this
    route is LONGER than the walk the player actually performs -- which is why travel picks
    each hop by distance from the player rather than by index along the route.
    """
    start = (sx, sy)
    goal = (gx, gy)

    if goal_adjacent:
        def arrived(tile):
            return abs(tile[0] - gx) + abs(tile[1] - gy) == 1
    else:
        def arrived(tile):
            return tile == goal

    previous = {start: None}
    queue = deque((start,))
    explored = 0

    while queue:
        current = queue.popleft()
        explored += 1
        if explored > max_nodes:
            return None
        if arrived(current):
            route = []
            tile = current
            while tile is not None:
                route.append(tile)
                tile = previous[tile]
            route.reverse()
            return route
        x, y = current
        for dx, dy in _CARDINAL_STEPS:
            neighbour = (x + dx, y + dy)
            if neighbour not in previous and passable(x, y, plane, dx, dy):
                previous[neighbour] = current
                queue.append(neighbour)
    return None


def reachable_from(x: int, y: int, plane: int, *, radius: int = REACH_RADIUS,
                   max_nodes: int = REACH_MAX_NODES) -> dict:
    """Every tile walkable from (x, y) within `radius`, as {(x, y): step distance}.

    One bounded flood answering N reachability questions at once, replacing N separate ones.
    Returned as a dict rather than a set so callers can also read distance; it is a drop-in
    for anything that only did `in` and truthiness tests against travel.reachable_set.

    Doors are baked open in this map, so "reachable" here means "reachable, possibly through a
    door you will have to open on the way" -- which is the right semantics for choosing what to
    work on next. A tile that is genuinely walled off with no door is what comes back missing.
    """
    start = (x, y)
    distances = {start: 0}
    queue = deque((start,))

    while queue and len(distances) < max_nodes:
        cx, cy = queue.popleft()
        step = distances[(cx, cy)] + 1
        for dx, dy in _CARDINAL_STEPS:
            neighbour = (cx + dx, cy + dy)
            if neighbour in distances:
                continue
            if max(abs(neighbour[0] - x), abs(neighbour[1] - y)) > radius:
                continue
            if passable(cx, cy, plane, dx, dy):
                distances[neighbour] = step
                queue.append(neighbour)
    return distances
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_worldmap.py -v`
Expected: PASS, 22 tests.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/worldmap.py ControllerAddon/tests/test_worldmap.py
git commit -m "$(printf 'feat(worldmap): cardinal BFS with goal_adjacent and a bounded reachability flood\n\nNo route is now a first-class answer returned in milliseconds, replacing\na routeless walk that hop-clicked a wall for up to 8 minutes.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>')"
```

---

### Task 3: `travel.first_door_on_route`

**Files:**
- Modify: `ControllerAddon/travel.py` (add near the existing DOOR_* constants, around line 148)
- Test: `ControllerAddon/tests/test_travel.py` (append)

**Interfaces:**
- Consumes: existing `travel._passable(flags_from, flags_to, dx, dy)`, `travel.DOOR_OBJECT_TYPES`, `travel.DOOR_OPEN_ACTION`.
- Produces:
  - `travel.DOOR_NAME_HINTS` widened to `("door", "gate", "curtain")`.
  - `travel.blocked_crossing_on_route(route, flags, start=0, lookahead=DOOR_ROUTE_LOOKAHEAD) -> tuple | None` returning `((ax, ay), (bx, by))`.
  - `travel.first_door_on_route(route, plane, objects, flags, start=0, lookahead=DOOR_ROUTE_LOOKAHEAD) -> dict | None`.
  - `route` is a list of `(x, y)` tuples as returned by `worldmap.find_path`; `flags` is `{(x, y): int}`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_travel.py`:

```python
# --- route-based door detection ------------------------------------------------------------

BLOCK_MOVEMENT_NORTH = 2
BLOCK_MOVEMENT_EAST = 8
BLOCK_MOVEMENT_SOUTH = 32
BLOCK_MOVEMENT_WEST = 128
BLOCK_MOVEMENT_OBJECT = 256


def _door(x, y, name="Door", actions=("Open",), object_type="WallObject",
          flags=BLOCK_MOVEMENT_NORTH, plane=0):
    return {"id": 7452, "name": name, "objectType": object_type, "actions": list(actions),
            "collisionFlags": flags, "x": x, "y": y, "plane": plane}


def _open_grid(tiles, blocked=()):
    """Flags for a set of tiles, all freely walkable except where stated."""
    grid = {tile: 0 for tile in tiles}
    for tile, value in blocked:
        grid[tile] = value
    return grid


def test_finds_the_edge_door_standing_on_the_blocked_crossing():
    """Barbara's shape: the crossing between two route tiles is shut, and the door object sits
    on one of them. MEASURED live -- a shut door records its blocked edge on BOTH tiles."""
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    door = _door(1746, 3534)
    assert travel.first_door_on_route(route, 0, [door], flags) is door


def test_finds_the_full_tile_door_standing_on_a_route_tile():
    """Falador TAU's, MEASURED 2026-08-29: a GameObject door with flags 131328 -- LOS-full plus
    BLOCK_MOVEMENT_OBJECT and NO edge bits at all. Caught because you cannot step onto its
    tile, not because any edge is marked."""
    route = [(3046, 3349), (3046, 3348), (3046, 3347)]
    flags = _open_grid(route, blocked=[((3046, 3348), BLOCK_MOVEMENT_OBJECT)])
    door = _door(3046, 3348, object_type="GameObject", flags=131328)
    assert travel.first_door_on_route(route, 0, [door], flags) is door


def test_ignores_an_open_door_because_clicking_one_CLOSES_it():
    """The correction we keep over Vitalite's name-only match. A shut door and an open one are
    both WallObjects named Door (Falador 24050 shut / 24051 open); only the action separates
    them, and left-clicking the open one turns a working walk into a wedged one."""
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    open_door = _door(1746, 3534, actions=("Close",))
    assert travel.first_door_on_route(route, 0, [open_door], flags) is None


def test_finds_a_curtain():
    """Taken from Vitalite's name list, which we were missing."""
    route = [(1746, 3533), (1746, 3534)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    curtain = _door(1746, 3534, name="Curtain")
    assert travel.first_door_on_route(route, 0, [curtain], flags) is curtain


def test_ignores_furniture_that_also_offers_open():
    """Drawers and cupboards fill every Mahogany Homes house and all offer Open. A live scan
    beside Falador's door returned Drawers and a Cupboard within 7 tiles."""
    route = [(1746, 3533), (1746, 3534)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    drawers = _door(1746, 3534, name="Drawers")
    assert travel.first_door_on_route(route, 0, [drawers], flags) is None


def test_returns_none_when_the_whole_route_is_clear():
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route)
    assert travel.first_door_on_route(route, 0, [_door(1746, 3534)], flags) is None


def test_ignores_a_door_on_another_plane():
    route = [(2616, 3317), (2617, 3317)]
    flags = _open_grid(route, blocked=[((2616, 3317), BLOCK_MOVEMENT_EAST)])
    downstairs = _door(2616, 3317, plane=0)
    assert travel.first_door_on_route(route, 1, [downstairs], flags) is None


def test_unknown_flags_never_invent_a_door():
    """An absent collision reading means "cannot tell", never "blocked" -- an un-rebuilt or
    out-of-scene answer must behave as before, not conjure a door to click."""
    route = [(1746, 3533), (1746, 3534)]
    assert travel.first_door_on_route(route, 0, [_door(1746, 3534)], {}) is None


def test_blocked_crossing_reports_the_first_one_only():
    route = [(10, 10), (10, 11), (10, 12), (10, 13)]
    flags = _open_grid(route, blocked=[((10, 11), BLOCK_MOVEMENT_NORTH),
                                       ((10, 12), BLOCK_MOVEMENT_NORTH)])
    assert travel.blocked_crossing_on_route(route, flags) == ((10, 11), (10, 12))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_travel.py -v -k "door_on_route or crossing or curtain or furniture_that"`
Expected: FAIL with `AttributeError: module 'travel' has no attribute 'first_door_on_route'`

- [ ] **Step 3: Write the implementation**

In `ControllerAddon/travel.py`, change `DOOR_NAME_HINTS` (currently line 149) to:

```python
DOOR_NAME_HINTS = ("door", "gate", "curtain")   # "curtain" taken from Vitalite's own list
```

Then add, immediately after the `DOOR_EDGE_BLOCKS` tuple:

```python
def blocked_crossing_on_route(route, flags, start: int = 0,
                              lookahead: int = DOOR_ROUTE_LOOKAHEAD):
    """The first pair of consecutive route tiles we cannot step between, or None.

    `route` is [(x, y), ...] from worldmap.find_path and `flags` is {(x, y): collision flags}
    from GET /collision. The map routes straight THROUGH doors by design, so a blocked crossing
    on a freshly computed route is very nearly a definition of "a shut door is here".

    _passable is the right test rather than _edge_open, and the difference is load-bearing: it
    also refuses a destination tile carrying TILE_BLOCKING_FLAGS, which is the only way the
    full-tile doors are visible at all. MEASURED at Falador TAU's -- a GameObject door with
    flags 131328 (BLOCK_LINE_OF_SIGHT_FULL | BLOCK_MOVEMENT_OBJECT) and no edge bits whatever.
    """
    end = min(len(route) - 1, start + lookahead)
    for i in range(start, end):
        ax, ay = route[i]
        bx, by = route[i + 1]
        if not _passable(flags.get((ax, ay)), flags.get((bx, by)), bx - ax, by - ay):
            return (ax, ay), (bx, by)
    return None


def first_door_on_route(route, plane: int, objects: list, flags: dict, start: int = 0,
                        lookahead: int = DOOR_ROUTE_LOOKAHEAD):
    """The shut door standing on the first blocked crossing of `route`, or None.

    This is the whole door algorithm now, and it replaced roughly 700 lines of heuristics
    (_door_separates, _within_span, reachable_set/open_frontier_door, open_nearest_shut_door
    and a three-tier escalation). None of those were wrong exactly -- they were all proxies for
    a route we could not compute. Given a real route the question becomes trivial and, unlike
    every one of those tiers, it works identically on any plane.

    The shape is Vitalite's WalkerPath.handlePassThroughObjects: find a door/gate/curtain
    standing on the player's tile or the next step's tile. Two deliberate deviations:

      * Vitalite matches on NAME ONLY and interacts with action index 0. We require an "Open"
        action, because left-clicking an OPEN door CLOSES it -- a shut door and an open one are
        both WallObjects named Door with per-door ids (Falador 24050 shut / 24051 open), so
        only the action separates them, and getting it wrong wedges a walk that was working.
      * Matching the object TYPE too, because chests, cupboards and drawers all offer "Open"
        and fill every Mahogany Homes house.

    An absent flags entry means "cannot tell" and yields no door, never a speculative click.
    """
    crossing = blocked_crossing_on_route(route, flags, start, lookahead)
    if crossing is None:
        return None
    tiles = set(crossing)

    for obj in objects or []:
        if obj.get("plane") != plane or obj.get("objectType") not in DOOR_OBJECT_TYPES:
            continue
        if (obj.get("x"), obj.get("y")) not in tiles:
            continue
        name = (obj.get("name") or "").lower()
        if not any(hint in name for hint in DOOR_NAME_HINTS):
            continue
        actions = obj.get("actions")
        if not actions or not any((a or "").lower() == DOOR_OPEN_ACTION for a in actions):
            continue
        return obj
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_travel.py -v`
Expected: PASS — the 9 new tests plus every pre-existing one.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/travel.py ControllerAddon/tests/test_travel.py
git commit -m "$(printf 'feat(travel): find the shut door on a route instead of guessing at geometry\n\nVitalite s model, plus an Open-action check it lacks -- clicking an open\ndoor closes it. Works on any plane, unlike every tier it replaces.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>')"
```

---

### Task 4: Route-following walker and the new `walk_to`

**Files:**
- Modify: `ControllerAddon/travel.py` (replace `walk_to` at lines 535-583; add helpers beside it)
- Test: `ControllerAddon/tests/test_travel.py` (append)

**Interfaces:**
- Consumes: `worldmap.find_path` (Task 2), `travel.first_door_on_route` (Task 3), existing `_hop_to`, `_within_tolerance`, `_crossed`, `controller.get_player`, `controller.get_objects`, `controller.get_collision`.
- Produces:
  - `travel.route_to(target, *, goal_adjacent=False) -> list[dict] | None` — route as `[{"x", "y", "plane"}, ...]`.
  - `travel.walk_to(ctx, target, tolerance=WALK_ARRIVAL_TOLERANCE, transports=None, min_path_tiles=None, goal_adjacent=True) -> bool` — same call shape as today.
  - `travel.open_door_toward(ctx, goal) -> bool`.
  - Constants `COLLISION_SCAN_RADIUS = 10`, `WALK_MAX_STALLS = 4`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_travel.py`:

```python
# --- route-following walker ----------------------------------------------------------------

@pytest.fixture
def walker(monkeypatch):
    """A walker whose hops teleport the player straight to the aim point, so tests exercise
    route selection and door handling rather than the Arduino."""
    state = {"pos": (3200, 3200, 0), "hops": [], "clicked": []}

    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": state["pos"][0], "y": state["pos"][1],
                                 "plane": state["pos"][2]})

    def fake_hop(ctx, x, y, plane):
        state["hops"].append((x, y))
        state["pos"] = (x, y, plane)
        return True

    monkeypatch.setattr(travel, "_hop_to", fake_hop)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, obj, **kw: state["clicked"].append(obj))
    return state


def test_walk_to_follows_a_route_and_arrives(walker, monkeypatch):
    route = [(3200 + i, 3200) for i in range(30)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    assert travel.walk_to(_Ctx(), {"x": 3229, "y": 3200, "plane": 0}) is True
    assert walker["hops"]


def test_walk_to_fails_fast_when_no_route_exists(walker, monkeypatch):
    """The point of the whole change. Before, a routeless walk hop-clicked a wall for up to 8
    minutes; now it is a definite answer in milliseconds."""
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: None)
    assert travel.walk_to(_Ctx(), {"x": 3229, "y": 3200, "plane": 0}) is False
    assert walker["hops"] == []


def test_hops_are_bounded_by_distance_not_by_step_index(walker, monkeypatch):
    """A cardinal BFS route is longer than the diagonal walk the client performs, so indexing
    N steps ahead would silently produce a hop far shorter than the measured 16-24 window."""
    route = [(3200, 3200 + i) for i in range(20)] + [(3200 + i, 3219) for i in range(1, 20)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(travel.random, "randint", lambda lo, hi: hi)
    travel.walk_to(_Ctx(), {"x": 3218, "y": 3219, "plane": 0})
    first_x, first_y = walker["hops"][0]
    assert max(abs(first_x - 3200), abs(first_y - 3200)) <= travel.WALK_HOP_MAX_TILES


def test_a_hop_stops_before_a_shut_door_instead_of_clicking_past_it(walker, monkeypatch):
    """Clicking past a shut door is what produced the wedge-and-re-click loop."""
    route = [(3200, 3200 + i) for i in range(20)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(travel.random, "randint", lambda lo, hi: hi)
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0,
         "flags": BLOCK_MOVEMENT_NORTH if (tx, ty) == (3200, 3204) else 0}
        for tx, ty in route])
    travel.walk_to(_Ctx(), {"x": 3200, "y": 3219, "plane": 0})
    assert walker["hops"][0] == (3200, 3204)


def test_a_stall_opens_the_door_on_the_route(walker, monkeypatch):
    route = [(3200, 3200), (3200, 3201), (3200, 3202)]
    door = _door(3200, 3201, plane=0)
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0,
         "flags": BLOCK_MOVEMENT_NORTH if (tx, ty) == (3200, 3201) else 0}
        for tx, ty in route])
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane: True)   # never moves
    travel.walk_to(_Ctx(), {"x": 3200, "y": 3202, "plane": 0})
    assert door in walker["clicked"]


def test_open_door_toward_clicks_the_door_between_us_and_a_goal(walker, monkeypatch):
    route = [(3200, 3200), (3200, 3201), (3200, 3202)]
    door = _door(3200, 3201, plane=0)
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0,
         "flags": BLOCK_MOVEMENT_NORTH if (tx, ty) == (3200, 3201) else 0}
        for tx, ty in route])
    assert travel.open_door_toward(_Ctx(), {"x": 3200, "y": 3202, "plane": 0}) is True
    assert door in walker["clicked"]


def test_an_upper_plane_route_that_leaves_the_loaded_scene_is_discarded(walker, monkeypatch):
    """Risk 1 from the design doc. Above ground the map has no data outside a building and no
    data reads as PASSABLE, so one missing exterior wall lets BFS escape into open sky. A route
    that wanders past the 104x104 scene is not a path, it is the hole in the data."""
    walker["pos"] = (3200, 3200, 1)
    escaping = [(3200, 3200 + i) for i in range(80)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: escaping)
    assert travel.route_to({"x": 3200, "y": 3279, "plane": 1}) is None


def test_the_same_route_is_accepted_on_the_ground_floor(walker, monkeypatch):
    """Plane 0 is densely mapped, so a long route there is ordinary and must not be rejected."""
    walker["pos"] = (3200, 3200, 0)
    long_route = [(3200, 3200 + i) for i in range(80)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: long_route)
    assert travel.route_to({"x": 3200, "y": 3279, "plane": 0}) is not None


def test_open_door_toward_does_nothing_when_the_way_is_clear(walker, monkeypatch):
    route = [(3200, 3200), (3200, 3201)]
    monkeypatch.setattr(travel.worldmap, "find_path", lambda *a, **kw: route)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3200, 3201, plane=0)])
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [
        {"x": tx, "y": ty, "plane": 0, "flags": 0} for tx, ty in route])
    assert travel.open_door_toward(_Ctx(), {"x": 3200, "y": 3201, "plane": 0}) is False
    assert walker["clicked"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_travel.py -v -k "walker or route_to or open_door_toward or hops_are or stall_opens"`
Expected: FAIL — `AttributeError: module 'travel' has no attribute 'open_door_toward'`

- [ ] **Step 3: Add the import and constants**

At the top of `ControllerAddon/travel.py`, beside the other imports, add:

```python
import worldmap
```

Beside the existing `WALK_*` constants (around line 78), add:

```python
COLLISION_SCAN_RADIUS = 10   # tiles of live collision fetched per hop. Big enough to see the
                             # doors along one hop, small enough that a 21x21 response per hop
                             # stays cheap. Beyond it we simply hop and let the stall catch it.
WALK_MAX_STALLS = 4          # consecutive no-progress passes before giving up on the walk.
                             # Deliberately small: with a real route, a stall means something
                             # the route does not know about, and repeating it does not help.
SCENE_HALF_WIDTH = 52        # the loaded scene is 104x104 tiles. Used ONLY to reject an
                             # upper-plane route that escapes into the open sky the map treats
                             # as walkable -- see route_to and the design doc's risk 1.
```

- [ ] **Step 4: Replace `walk_to` and add the helpers**

Replace the whole of `walk_to` (lines 535-583, through the end of `_is_short_hop`) with:

```python
def route_to(target: dict, *, goal_adjacent: bool = False):
    """A walking route to `target` as [{"x", "y", "plane"}, ...], or None.

    Same plane only, and no transports -- see worldmap.find_path. `goal_adjacent` routes to a
    tile BESIDE the target, which is what you want for anything standing on furniture (a
    hotspot, a ladder, a bank booth): those tiles are solid and nothing can route onto them.
    """
    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is None:
        return None
    if target.get("plane") is not None and target["plane"] != plane:
        debug_log.log("travel", f"target is on plane {target['plane']} and we are on {plane} "
                                f"-- walking cannot change floor")
        return None
    tiles = worldmap.find_path(px, py, plane, target["x"], target["y"],
                               goal_adjacent=goal_adjacent)
    if tiles is None:
        return None

    # Upstairs, refuse a route that wanders outside the loaded scene. The map has NO DATA above
    # ground outside a building and no data reads as PASSABLE, so a single missing exterior wall
    # on plane 1+ lets BFS escape into open sky and come back down somewhere convenient -- a
    # shortcut through nothing. The loaded scene is 104x104 tiles centred near the player, so a
    # legitimate indoor route on an upper floor never approaches this bound.
    if plane > 0:
        for x, y in tiles:
            if max(abs(x - px), abs(y - py)) > SCENE_HALF_WIDTH:
                debug_log.log("travel", f"discarding a plane-{plane} route that leaves the "
                                        f"loaded scene at ({x},{y}) -- the map treats open sky "
                                        f"as walkable, so this is not a real path")
                return None

    return [{"x": x, "y": y, "plane": plane} for x, y in tiles]


def _collision_flags(x: int, y: int, plane: int, radius: int = COLLISION_SCAN_RADIUS) -> dict:
    """Live collision for a square around (x, y), as {(x, y): flags}. Empty when the client
    cannot answer, which every consumer must read as "cannot tell" rather than "blocked"."""
    return {(t["x"], t["y"]): t.get("flags")
            for t in (controller.get_collision(x, y, plane, radius=radius) or [])}


def _nearest_step_index(route: list, px: int, py: int) -> int:
    """Where the player actually is along the route.

    Keyed on POSITION rather than on a marching counter, which is what the old
    _walk_along_path used. A counter advances whether or not the player moved, so a stalled
    walk kept aiming further down a route it was no longer travelling."""
    best, best_distance = 0, None
    for i, step in enumerate(route):
        distance = max(abs(step["x"] - px), abs(step["y"] - py))
        if best_distance is None or distance < best_distance:
            best, best_distance = i, distance
    return best


def _hop_index(route: list, start: int, px: int, py: int, flags: dict) -> int:
    """How far along the route to aim, bounded by DISTANCE and stopping at any shut crossing.

    Distance, not step count: a cardinal BFS route is longer than the diagonal walk the client
    performs, so counting N steps ahead would produce a hop well short of the measured 16-24
    tile window that WALK_HOP_MIN/MAX_TILES encode.

    Stopping at a shut crossing is Vitalite's rule, and it matters: hop-clicking PAST a shut
    door is exactly what leaves the player wedged against it with the walker none the wiser.
    """
    limit = random.randint(WALK_HOP_MIN_TILES, WALK_HOP_MAX_TILES)
    furthest = start
    for i in range(start, len(route) - 1):
        ax, ay = route[i]["x"], route[i]["y"]
        bx, by = route[i + 1]["x"], route[i + 1]["y"]
        if flags and not _passable(flags.get((ax, ay)), flags.get((bx, by)), bx - ax, by - ay):
            break
        if max(abs(bx - px), abs(by - py)) > limit:
            break
        furthest = i + 1
    return furthest


def _door_on_route(route: list, plane: int, flags: dict, start: int = 0):
    """The shut door on the route ahead of `start`, or None. Fetches /objects only once a
    blocked crossing has actually been found, so a clear walk pays nothing for this."""
    tiles = [(step["x"], step["y"]) for step in route]
    if blocked_crossing_on_route(tiles, flags, start) is None:
        return None
    objects = controller.get_objects(radius=COLLISION_SCAN_RADIUS) or []
    return first_door_on_route(tiles, plane, objects, flags, start)


def _open_door(ctx, door: dict, recheck) -> bool:
    """Click a shut door and CONFIRM it, rather than sleeping and hoping.

    `recheck` is a zero-argument callable returning the door still blocking the way, or None.

    Two things this gets right that a fixed wait did not, both MEASURED live 2026-08-29:

      * The budget includes the WALK. A door is opened by walking to it and then opening it,
        and DOOR_OPEN_WAIT_S alone (2.0s) is less than the ~2.4s of walking to a door four
        tiles off. At ROSS's the FIRST click registered as `Open Door` and was good; the budget
        expired before the player arrived, and the two retries then resolved to `Walk here`
        because the player had moved off the pixel being re-aimed at. A working click was
        reported as a failure and interrupted by its own retry, three times over.
      * Getting THROUGH counts as success (_crossed). ROSS's front door swings shut behind you,
        so re-reading the doorway answers "still shut" about a door that worked -- eight clicks
        across one session, every one registering correctly, every one logged as a failure.
    """
    here = controller.get_player() or {}
    here_x, here_y = here.get("x"), here.get("y")
    dx, dy = door.get("x"), door.get("y")
    if here_x is not None and dx is not None:
        tiles = max(abs(here_x - dx), abs(here_y - dy))
    else:
        tiles = door.get("distance") or 0
    budget = min(DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S, DOOR_OPEN_WAIT_MAX_S)

    debug_log.log("travel", f"opening the {door.get('name')} at ({dx},{dy}) in the way "
                            f"({tiles} tiles off, waiting up to {budget:.1f}s)")
    controller.click_entity(ctx.ser, door)

    waited = 0.0
    while waited < budget:
        time.sleep(DOOR_CONFIRM_POLL_S)
        waited += DOOR_CONFIRM_POLL_S
        if recheck() is None or _crossed(door, here_x, here_y):
            return True

    debug_log.log("travel", f"the {door.get('name')} at ({dx},{dy}) is still shut after "
                            f"{budget:.1f}s -- another player may be holding it, or the "
                            f"click missed")
    return True


def open_door_toward(ctx, goal: dict) -> bool:
    """Open the shut door standing between the player and `goal`. True if one was clicked.

    Replaces open_doorway_to, open_frontier_door and open_nearest_shut_door -- three tiers that
    existed only because there was no route to ask. Costs nothing when the way is clear.

    goal_adjacent because the tile you need a DOOR for and the tile you can WALK to are usually
    different: a hotspot's own tile is furniture and unroutable, while aiming at the standing
    tile instead hides the doorway (both it and the player sit on the same side of the door).
    """
    route = route_to(goal, goal_adjacent=True)
    if not route or len(route) < 2:
        return False
    player = controller.get_player() or {}
    px, py = player.get("x"), player.get("y")
    if px is None:
        return False
    plane = player.get("plane") or 0
    door = _door_on_route(route, plane, _collision_flags(px, py, plane))
    if door is None:
        return False
    return _open_door(ctx, door,
                      lambda: _door_on_route(route, plane, _collision_flags(px, py, plane)))


def walk_to(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE,
            transports: dict | None = None, min_path_tiles: float | None = None,
            goal_adjacent: bool = True) -> bool:
    """Walk to `target`, following a route computed from the global collision map.

    True once within `tolerance` tiles of the target. **False now means something**: there is
    no walking route at all, returned in milliseconds. That replaces the old behaviour where a
    routeless walk fell through to an obstacle-blind greedy walker and hop-clicked a wall until
    WALK_MAX_HOPS x WALK_HOP_TIMEOUT_S ran out -- up to 8 minutes, measured.

    `transports` and `min_path_tiles` are accepted and ignored; they configured Shortest Path,
    which no longer exists. They are kept so the 39 existing call sites keep working, and are
    removed from those call sites and from this signature in a later pass.

    `goal_adjacent` defaults True because most targets here are objects standing on their own
    (unroutable) tile. Pass False when the target really is a tile to stand on.
    """
    route = route_to(target, goal_adjacent=goal_adjacent)
    if route is None:
        debug_log.log("travel", f"no walking route to ({target['x']},{target['y']}) "
                                f"-- not attempting the walk")
        return False

    doors_opened = 0
    stalls = 0
    last_pos = None

    for _ in range(WALK_MAX_HOPS):
        player = controller.get_player() or {}
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False
        if _within_tolerance(px, py, target["x"], target["y"], tolerance):
            return True

        flags = _collision_flags(px, py, plane)
        index = _nearest_step_index(route, px, py)

        # Doors are resolved BEFORE stepping off, not after a stall (user directive
        # 2026-08-29: "the priority should be determine door situation - then go in"). Waiting
        # for a stall costs a whole hop timeout to notice and is spent wedged against the door
        # in plain view; at NORMAN's it never fired at all, because the walk "arrived" outside.
        if doors_opened < DOOR_MAX_OPENS_PER_WALK:
            door = _door_on_route(route, plane, flags, index)
            if door is not None:
                doors_opened += 1
                _open_door(ctx, door,
                           lambda: _door_on_route(route, plane,
                                                  _collision_flags(px, py, plane), index))
                refreshed = route_to(target, goal_adjacent=goal_adjacent)
                if refreshed is None:
                    return False
                route = refreshed
                last_pos = None
                continue

        if last_pos == (px, py):
            stalls += 1
            if stalls > WALK_MAX_STALLS:
                debug_log.log("travel", f"stalled {stalls} times short of "
                                        f"({target['x']},{target['y']}) -- giving up")
                return False
            refreshed = route_to(target, goal_adjacent=goal_adjacent)
            if refreshed is None:
                return False
            route = refreshed
            index = _nearest_step_index(route, px, py)
        last_pos = (px, py)

        hop = route[_hop_index(route, index, px, py, flags)]
        if (hop["x"], hop["y"]) == (px, py):
            # The route says we are already as far as this hop can take us and we have not
            # arrived -- the remaining gap is not walkable. Fail rather than spin.
            debug_log.log("travel", "route makes no further progress toward the target")
            return False
        if not _hop_to(ctx, hop["x"], hop["y"], hop["plane"]):
            return False

    return False
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_travel.py -v`
Expected: PASS for the new walker tests. **Pre-existing Shortest Path tests will still pass** — nothing has been deleted yet.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/travel.py ControllerAddon/tests/test_travel.py
git commit -m "$(printf 'feat(travel): follow a real route, resolve doors before stepping off\n\nwalk_to now returns False in milliseconds when no route exists, replacing\nan obstacle-blind fallback that hop-clicked a wall for up to 8 minutes.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>')"
```

---

### Task 5: Delete the Shortest Path stack and the door heuristics

**Files:**
- Modify: `ControllerAddon/travel.py` (deletions)
- Modify: `ControllerAddon/tests/test_travel.py` (delete the tests for deleted functions)
- Modify: `ControllerAddon/controller.py` (delete the shortest-path helpers)
- Delete: `src/main/java/com/example/providers/ShortestPathProvider.java`
- Modify: `src/main/java/com/example/bridge/BridgeHttpServer.java` (or wherever the provider is registered)

**Interfaces:**
- Consumes: Task 4's `walk_to` / `open_door_toward` as the only remaining entry points.
- Produces: nothing new. `travel.can_reach`, `travel.reachable_set`, `_edge_open`, `_passable`, `_tile_blocked` and every `_hop_to` / `_click_walk_point` helper **survive untouched**.

- [ ] **Step 1: Confirm what still references the doomed symbols**

```bash
cd /c/Users/Kiyo/Documents/Httpbridge/ControllerAddon
grep -rn "_walk_greedy\|_walk_along_path\|_request_computed_path\|_result_is_for\|transport_config\|TRANSPORT_POLICY\|_use_transport\|_transport_is_executable\|_find_transport_object\|_find_transport_npc\|_find_next_transport_index\|_is_short_hop\|SHORT_HOP_TILES\|_routable_goal\|_find_blocking_door\|_open_blocking_door\|_door_separates\|_within_span\|_shut_doors_in_range\|open_frontier_door\|open_nearest_shut_door\|open_doorway_to" --include=*.py .
```

Expected: hits in `travel.py`, `tests/test_travel.py`, and `routines/mahogany_homes.py`. **The mahogany_homes hits are Task 6's job — leave them and expect that file's tests to fail until then.**

- [ ] **Step 2: Delete from `travel.py`**

Remove these definitions entirely, together with their module-level constants and comments:

- `_door_separates`, `_within_span`, `_tile_blocked`'s caller `_routable_goal`, `_door_blocks`
- `_shut_doors_in_range`, `_find_blocking_door`, `_open_blocking_door`
- `open_frontier_door`, `open_nearest_shut_door`, `open_doorway_to`
- `_walk_greedy`, `_walk_along_path`, `_is_short_hop`
- `_request_computed_path`, `_result_is_for`, `transport_config`, `TRANSPORT_POLICY`,
  `_use_transport`, `_transport_is_executable`, `_find_transport_object`,
  `_find_transport_npc`, `_find_next_transport_index`, `TRANSPORT_INTERACT_TIMEOUT_S`
- Constants `SHORT_HOP_TILES`, `DOOR_SEARCH_RADIUS`, `DOOR_SPAN_MARGIN`,
  `DOOR_ROUTE_SEARCH_RADIUS`, `DOOR_ROUTE_ADJACENCY`, `DOOR_PROGRESS_EPSILON`,
  `GOAL_NEIGHBOUR_RADIUS`

**Keep** `_tile_blocked`, `_edge_open`, `_passable`, `reachable_set`, `can_reach`,
`REACH_SCAN_RADIUS`, `REACH_MAX_TILES`, `DOOR_ROUTE_LOOKAHEAD`, `DOOR_MAX_OPENS_PER_WALK`,
`DOOR_CONFIRM_POLL_S`, `DOOR_OPEN_WAIT_S`, `DOOR_WALK_ALLOWANCE_S`, `DOOR_OPEN_WAIT_MAX_S`,
`DOOR_OBJECT_TYPES`, `DOOR_NAME_HINTS`, `DOOR_OPEN_ACTION`, `DOOR_BLOCKING_FLAGS`,
`DOOR_EDGE_BLOCKS`, `_crossed`, and every collision-flag constant.

- [ ] **Step 3: Delete from `controller.py`**

```bash
grep -n "shortest" controller.py
```

Delete `request_shortest_path` and `get_shortest_path_result` and any `/shortest-path` constant.

- [ ] **Step 4: Delete the obsolete tests**

In `tests/test_travel.py`, delete the `_result` helper, every test under the transport-policy
and Shortest Path handshake sections, and the two `monkeypatch.setattr(controller,
"request_shortest_path"/"get_shortest_path_result", ...)` lines inside the `no_live_bridge`
fixture. **Keep the fixture itself and its `get_collision` stub** — it is what makes the suite
hermetic. Update the module docstring to describe the route-based walker.

- [ ] **Step 5: Delete the Java provider**

```bash
cd /c/Users/Kiyo/Documents/Httpbridge
git rm src/main/java/com/example/providers/ShortestPathProvider.java
grep -rn "ShortestPathProvider\|shortest-path" src/main/java/
```

Remove every line the grep reports (registration, imports, route table entries).

- [ ] **Step 6: Verify**

```bash
cd ControllerAddon
python -m py_compile travel.py controller.py worldmap.py
python -m pytest tests/test_travel.py tests/test_worldmap.py -v
cd .. && ./gradlew.bat compileJava
```

Expected: Python compiles and both suites pass; Gradle compiles clean.
`tests/test_mahogany_homes.py` is expected to FAIL here — Task 6 fixes it.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(printf 'refactor: delete Shortest Path and the door heuristics it made necessary\n\nRemoves ~700 lines: the transport stack, the greedy walker, and the three\ndoor tiers that were all proxies for a route we could not compute.\ncan_reach stays on live collision flags -- the static map cannot answer it.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>')"
```

---

### Task 6: Migrate `routines/mahogany_homes.py`

**Files:**
- Modify: `ControllerAddon/routines/mahogany_homes.py` (lines 390-427, 760-790, 852-860, 920-945, 1060-1085)
- Test: `ControllerAddon/tests/test_mahogany_homes.py`

**Interfaces:**
- Consumes: `travel.open_door_toward`, `travel.walk_to`, `worldmap.reachable_from`, and the unchanged `travel.can_reach`.
- Produces: no new public API. `pick_next_hotspot(hotspots, reach, plane)` keeps its exact signature — `reach` is now a dict from `worldmap.reachable_from` instead of a set from `travel.reachable_set`, and both support `in` and truthiness identically.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_mahogany_homes.py`:

```python
def test_pick_next_hotspot_accepts_a_distance_dict_as_the_reach_map(monkeypatch):
    """worldmap.reachable_from returns {(x, y): distance} where travel.reachable_set returned a
    set. Both answer `in` and truthiness the same way, so the ranking is unchanged."""
    reachable = {"x": 10, "y": 10, "plane": 0}
    walled_off = {"x": 99, "y": 99, "plane": 0}
    hotspots = [
        {"objectId": 1, "needsAttention": True, "location": walled_off},
        {"objectId": 2, "needsAttention": True, "location": reachable},
    ]
    monkeypatch.setattr(mahogany_homes.travel, "can_reach",
                        lambda loc, reach, plane=None: loc == reachable)
    chosen = mahogany_homes.pick_next_hotspot(hotspots, {(10, 10): 0, (10, 11): 1}, 0)
    assert chosen["objectId"] == 2


def test_pick_next_hotspot_still_returns_a_walled_off_hotspot_when_nothing_else_is_left():
    """Rank 2 sorts last but is still RETURNED, so a walled-off contract falls through to the
    door/walk recovery rather than reporting no work left and turning in unfinished."""
    walled_off = {"x": 99, "y": 99, "plane": 0}
    hotspots = [{"objectId": 1, "needsAttention": True, "location": walled_off}]
    assert mahogany_homes.pick_next_hotspot(hotspots, {(10, 10): 0}, 0)["objectId"] == 1
```

- [ ] **Step 2: Run the test to verify the suite fails**

Run: `cd ControllerAddon && python -m pytest tests/test_mahogany_homes.py -v`
Expected: FAIL — `AttributeError: module 'travel' has no attribute 'reachable_set'` (or `open_frontier_door`) from the module's existing call sites.

- [ ] **Step 3: Swap the reachability source**

Add `import worldmap` beside the existing `import travel`. Then in `work_phase` (around line 768) replace:

```python
    reach = (travel.reachable_set(player["x"], player["y"], player_plane)
             if player.get("x") is not None else set())
```

with:

```python
    # One bounded flood per tick, shared by the choice of hotspot and the reachability gate.
    # worldmap answers on ANY plane, which travel.reachable_set could only do via the client's
    # own scene -- and it costs no HTTP call at all. Doors are baked open in that map, so a
    # hotspot behind a shut door reads as reachable-with-a-door-to-open, which is the right
    # answer for choosing what to work on next.
    reach = (worldmap.reachable_from(player["x"], player["y"], player_plane)
             if player.get("x") is not None else {})
```

And in `_climb` (around line 1077) replace:

```python
        reach = travel.reachable_set(here["x"], here["y"], here.get("plane") or 0)
```

with:

```python
        reach = worldmap.reachable_from(here["x"], here["y"], here.get("plane") or 0)
```

- [ ] **Step 4: Collapse the three door tiers to one call**

In `work_phase`'s unreachable branch (around line 855) replace:

```python
        if not travel.open_frontier_door(ctx, hotspot_at):
            travel.open_doorway_to(ctx, hotspot_at)
```

with:

```python
        # ONE call now. This used to be three tiers -- open_frontier_door, then the
        # straight-line open_doorway_to, then a blunt open_nearest_shut_door that opened
        # something and hoped. All three existed because there was no route to ask; with a
        # route the door is simply the one standing on it, and that works on any plane.
        travel.open_door_toward(ctx, hotspot_at)
```

At the pre-click door check (around line 861) replace `travel.open_doorway_to(ctx, hotspot_at)`
with `travel.open_door_toward(ctx, hotspot_at)`.

At the stuck branch (around line 937) replace:

```python
            if not travel.open_frontier_door(ctx, hotspot_at):
                if not travel.open_doorway_to(ctx, hotspot_at):
                    travel.open_nearest_shut_door(ctx, hotspot_at)
```

with:

```python
            travel.open_door_toward(ctx, hotspot_at)
```

In `_climb` (around line 1081) replace:

```python
            if not travel.open_frontier_door(ctx, ladder):
                travel.open_doorway_to(ctx, ladder)
```

with:

```python
            travel.open_door_toward(ctx, ladder)
```

- [ ] **Step 5: Drop the dead `min_path_tiles` arguments**

```bash
cd ControllerAddon && grep -rn "min_path_tiles" routines/ *.py
```

Delete `min_path_tiles=0` from every call — it configured Shortest Path's short-hop threshold,
which no longer exists. Leave `tolerance=` arguments alone.

- [ ] **Step 6: Run the tests**

Run: `cd ControllerAddon && python -m pytest tests/ -v`
Expected: PASS, the whole suite.

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/routines/mahogany_homes.py ControllerAddon/tests/test_mahogany_homes.py
git commit -m "$(printf 'refactor(mahogany_homes): route-based reachability and one door call\n\nThree door tiers collapse to open_door_toward. Reachability comes from the\nglobal map, which answers on any plane and costs no HTTP call.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>')"
```

---

### Task 7: Audit every walking call site, then document

**Files:**
- Modify: `ControllerAddon/routines/{chinchompa_hunting,falconry,golem_crafting,slayer}.py` as the audit requires
- Modify: `ControllerAddon/travel.py` (drop the vestigial parameters)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `travel.walk_to(ctx, target, tolerance=WALK_ARRIVAL_TOLERANCE, goal_adjacent=True)` — the vestigial `transports` and `min_path_tiles` parameters are gone.

- [ ] **Step 1: List every call site**

```bash
cd ControllerAddon
grep -rn "travel\.walk_to\|walk_to_exact_tile" --include=*.py . | grep -v "^./tests/"
```

Expected: 39 sites across `travel.py` and five routines.

- [ ] **Step 2: Classify each one**

For each `travel.walk_to` call, decide whether the target is **a tile to stand on**
(`goal_adjacent=False`) or **an object standing on its own solid tile**
(`goal_adjacent=True`, the default). Bank booths, hotspots, ladders and NPCs are objects;
a `save_location()` tile or a safespot is a tile. Add the explicit argument wherever the
answer is False.

`walk_to_exact_tile` is unchanged and needs no edit.

- [ ] **Step 3: Remove the vestigial parameters**

```bash
grep -rn "transports=" --include=*.py .
```

Delete those arguments, then change the `walk_to` signature to:

```python
def walk_to(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE,
            goal_adjacent: bool = True) -> bool:
```

and delete the paragraph in its docstring describing the ignored parameters.

- [ ] **Step 4: Verify everything compiles and passes**

```bash
cd ControllerAddon
python -m py_compile travel.py controller.py worldmap.py banker.py routines/*.py
python -m pytest tests/ -v
```

Expected: clean compile, full suite green. Note `banker.py` is compiled explicitly because no
test imports it, so pytest alone would not catch a syntax error there.

- [ ] **Step 5: Update CLAUDE.md**

In the Mahogany Homes section, replace the door-machinery bullets (`_door_separates`,
`open_frontier_door`, the three-tier ordering, the "REACHABILITY IS DECIDED BEFORE THE FIRST
CLICK" entry's `travel.reachable_set` references) with a short pointer to `worldmap.py` and
`travel.open_door_toward`, and note that **Shortest Path is gone**. Keep every MEASURED fact —
the ROSS, JEFF, Barbara and TAU geometries are still the evidence this design rests on, and the
new tests encode them. Add to the "Running things" section:

```markdown
- **Pathfinding**: `ControllerAddon/worldmap.py` holds a world-wide per-edge collision map
  (all four planes) and answers `find_path`/`reachable_from` offline in milliseconds. It is
  the only routing engine; Shortest Path was removed 2026-08-29. Doors are baked open in that
  map by design -- `travel.open_door_toward` finds the shut one standing on the route. The map
  CANNOT tell furniture from a wall, so `travel.can_reach` still answers object-interaction
  adjacency from live collision flags, and must not be moved onto the map.
```

- [ ] **Step 6: Commit**

```bash
cd /c/Users/Kiyo/Documents/Httpbridge
git add -A
git commit -m "$(printf 'docs: record the pathfinding replacement and audit every walk call site\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>')"
```

---

## Live verification (after Task 7, with the user present)

The suite cannot prove this works — every previous pathing regression here was found live. Run
Mahogany Homes and watch for the four failures this replaces:

1. **A contract in a house with a shut front door.** Expect one `opening the Door ... in the
   way` before the first hotspot click, not after five wasted ones. The 2026-08-29 baseline was
   15 of 27 hotspot clicks achieving nothing.
2. **An upstairs contract (JESS or NORMAN, whose owner is upstairs).** Expect a route on plane
   1 where Shortest Path previously returned nothing and the greedy walker clicked the same
   pixel eleven times.
3. **A walled-off hotspot with no door (JEFF's upper floor).** Expect `pick_next_hotspot` to
   take the reachable work first rather than fixating.
4. **A walk with genuinely no route.** Expect `no walking route to (x,y)` within milliseconds
   instead of a multi-minute wedge.

Watch also for the risk the spec accepts: a plane > 0 route that leaks through the void would
show as a hop toward a tile outside the building. If that appears, the fix is the loaded-scene
bound named in the spec, not a change to the BFS.
