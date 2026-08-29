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

  1. GENUINELY absent data (no container at all for that key) now defaults to PASSABLE, and THIS
     REVERSED with the 2026-08-29 asset swap (CacheMapDumper replacing the old EthanApi dump, see
     .superpowers/sdd/2026-08-29-global-collision-pathfinding/map-swap-report.md) -- it is NOT the
     reader's unchanged neutral behaviour, despite an earlier version of this note claiming that.
     `_Roaring.contains()` for a missing key is unchanged (always False); what that False MEANS
     flipped along with Indexer.SET_MEANS_PASSABLE: old asset (True), a missing key read BLOCKED;
     new asset (False), a missing key reads PASSABLE. Concrete consequence, MEASURED: a coverage
     hole now produces a confident route through nothing instead of failing fast --
     `find_path(4500, 200, 0, 4525, 225)` returns a clean 51-tile route across coordinates that
     are not in the game at all. Two things keep this from being as bad as it sounds: the new dump
     DOES fence real coastlines (Port Sarim->Musa Point, Falador->open sea and Lumbridge->off-map
     void all still return None on both the old and new map), and the exposure is confined to a
     walk whose START tile sits inside a genuinely unmapped region -- a route that only passes
     near real, mapped terrain stays bounded by that terrain's actual walls.

     Do not conflate this with a SEPARATE mechanism, the "unfloored plane 1-3 reads blocked"
     finding: the asset EXPLICITLY MARKS most outdoor plane 1-3 space as blocked (a real bit is
     set, not merely absent) -- MEASURED at five separate outdoor plane-1/2 spots, none under any
     building, all blocked on all four cardinals (see test_unfloored_upper_planes_default_to_
     blocked). The old asset explicitly marked that same space PASSABLE (which is what used to
     make this item read "a missing exterior wall upstairs lets a route leak through the void").
     So: genuinely ABSENT data now OVER-connects (the reversal above, a new exposure); explicitly
     MARKED open sky now UNDER-connects (the opposite of the old failure mode) -- two different
     mechanisms with opposite effects on a route. The walker's stall detection is still the
     backstop for the under-connect case.
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


class EthanApiIndexer:
    """RETAINED FOR REFERENCE ONLY -- NOT IN USE. This was `Indexer` until the 2026-08-29 map
    swap (collision-map.gz predates the Varlamore region and reads zero data there, see
    docs/superpowers/sdd/2026-08-29-global-collision-pathfinding/). It matched EthanApi's
    GlobalCollisionMap dump. Kept, under an unambiguous name, purely so a future reader of this
    file (or of git blame) can see what the old asset's layout was without having to dig it back
    out of history -- and so it can never again be confused with the layout actually in use.

    There are three of these maps in data/decompiled/ and they look alike while meaning
    opposite things:

      EthanApi (this one)  x & 16383 | y << 14 | plane << 28, bit 30 = east   set = PASSABLE
      Vitalite map.dat     x &  8191 | y << 13 | plane << 28, bit 30 = east   set = BLOCKED
      CacheMapDumper out   12-bit x (base 480), 14-bit y, 2-bit plane          set = BLOCKED

    Reading one with another's indexer produces a map that is subtly wrong rather than
    obviously broken, so switching source means changing the active Indexer class and nothing
    else -- which is exactly what happened: see `Indexer`, below, for the layout now in use.
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


class Indexer:
    """Where the bit layout and the polarity live, and the ONLY place that should know them.

    CacheMapDumper's layout, in use since the 2026-08-29 map swap (see EthanApiIndexer, above,
    for what this replaced and why). MEASURED/SPECIFIED against the asset's own generator:

      12-bit x, base 480 (x - 480) at shift 0
      14-bit y at shift 12
      2-bit plane at shift 26
      data bit 0 = NORTH blocked (the base packed value, no marker bit)
      data bit 1 = EAST blocked, marked by bit 28

    SET_MEANS_PASSABLE is False here -- the opposite polarity from EthanApiIndexer's map. A set
    bit in THIS map means the edge is BLOCKED. north()/east() compare against this flag rather
    than the raw bit specifically so this swap needed no change to either of them (see
    test_set_means_passable_is_actually_honoured_not_decorative).

    Reading one map with another's indexer produces a map that is subtly wrong rather than
    obviously broken, so switching source means changing this class (or picking a different one
    entirely) and nothing else.
    """

    X_BASE = 480
    X_MASK = 0xFFF
    Y_MASK = 0x3FFF
    Y_SHIFT = 12
    PLANE_SHIFT = 26
    PLANE_MASK = 3
    EAST_FLAG = 1 << 28
    SET_MEANS_PASSABLE = False

    # A RoaringBitmap container key is the top 16 bits of the value (value >> 16). Plane sits at
    # value bits 26-27, which lands at key bits 10-11 -- key = value >> 16, so key bit i is value
    # bit (i + 16); plane's low bit (value bit 26) is key bit 10. The east marker (value bit 28)
    # lands at key bit 12 and does not collide with the plane field. Used by bits_per_plane,
    # which reads only the header.
    PLANE_KEY_SHIFT = 10
    PLANE_KEY_MASK = 3

    @classmethod
    def packed(cls, x: int, y: int, plane: int) -> int:
        return (((x - cls.X_BASE) & cls.X_MASK)
                | ((y & cls.Y_MASK) << cls.Y_SHIFT)
                | ((plane & cls.PLANE_MASK) << cls.PLANE_SHIFT))


class _Roaring:
    """Minimal reader for RoaringBitmap's portable serialization.

    Deliberately LAZY: it indexes the container header once (~0.13s) and binary-searches or
    bit-tests directly in the buffer, rather than materialising every integer.

    Handles BOTH portable-format cookies. The original asset (EthanApi's dump) never emitted a
    run container, so only SERIAL_COOKIE_NO_RUNCONTAINER (12346, a bare `cookie, size` pair of
    u32s) was ever seen. CacheMapDumper's replacement DOES emit run containers -- cookie
    253243451, whose low 16 bits (`cookie & 0xFFFF`) are SERIAL_COOKIE_RUNCONTAINER (12347) and
    whose size is packed into the high bits (`(cookie >> 16) + 1`) instead of following as its
    own u32. That packed cookie is what forced this class to branch on format at all.
    """

    SERIAL_COOKIE_NO_RUNCONTAINER = 12346
    SERIAL_COOKIE_RUNCONTAINER = 12347
    ARRAY_CONTAINER_MAX = 4096
    # RoaringBitmap only omits the offset header below this container count; every asset this
    # reader has ever seen (thousands of containers) is far above it, so the omitted-offset path
    # is not implemented -- an asset that small would need it added, not silently mis-parsed.
    NO_OFFSET_THRESHOLD = 4

    def __init__(self, data: bytes):
        self._data = data
        cookie = struct.unpack_from("<I", data, 0)[0]

        if (cookie & 0xFFFF) == self.SERIAL_COOKIE_RUNCONTAINER:
            size = (cookie >> 16) + 1
            if size < self.NO_OFFSET_THRESHOLD:
                raise ValueError(
                    f"container count {size} is below NO_OFFSET_THRESHOLD "
                    f"({self.NO_OFFSET_THRESHOLD}); the omitted-offset-header layout isn't "
                    f"implemented"
                )
            run_flag_bytes = (size + 7) // 8
            run_flags_offset = 4
            run_flag_bitmap = data[run_flags_offset:run_flags_offset + run_flag_bytes]
            descriptive = run_flags_offset + run_flag_bytes

            def is_run(i: int) -> bool:
                return bool((run_flag_bitmap[i >> 3] >> (i & 7)) & 1)
        elif cookie == self.SERIAL_COOKIE_NO_RUNCONTAINER:
            size = struct.unpack_from("<I", data, 4)[0]
            descriptive = 8

            def is_run(i: int) -> bool:
                return False
        else:
            raise ValueError(
                f"unsupported RoaringBitmap cookie {cookie}; this reader handles only the "
                f"portable format's two cookies ({self.SERIAL_COOKIE_NO_RUNCONTAINER}, "
                f"{self.SERIAL_COOKIE_RUNCONTAINER})"
            )

        self._containers = {}
        offsets = descriptive + size * 4
        for i in range(size):
            key, card_minus_one = struct.unpack_from("<HH", data, descriptive + i * 4)
            offset = struct.unpack_from("<I", data, offsets + i * 4)[0]
            cardinality = card_minus_one + 1
            if is_run(i):
                kind = "run"
            elif cardinality <= self.ARRAY_CONTAINER_MAX:
                kind = "array"
            else:
                kind = "bitmap"
            self._containers[key] = (cardinality, offset, kind)

    def _contains_run(self, offset: int, low: int) -> bool:
        """RUN container: u16 n_runs, then n_runs * (u16 start, u16 length_minus_1), sorted by
        start and non-overlapping -- so binary search for the last run starting at or before
        `low`, then check it covers `low`."""
        n_runs = struct.unpack_from("<H", self._data, offset)[0]
        runs_offset = offset + 2
        lo, hi = 0, n_runs
        while lo < hi:
            mid = (lo + hi) // 2
            start = struct.unpack_from("<H", self._data, runs_offset + mid * 4)[0]
            if start > low:
                hi = mid
            else:
                lo = mid + 1
        if lo == 0:
            return False
        start, length_minus_1 = struct.unpack_from("<HH", self._data, runs_offset + (lo - 1) * 4)
        return start <= low <= start + length_minus_1

    def contains(self, value: int) -> bool:
        entry = self._containers.get((value >> 16) & 0xFFFF)
        if entry is None:
            return False
        cardinality, offset, kind = entry
        low = value & 0xFFFF
        if kind == "run":
            return self._contains_run(offset, low)
        if kind == "array":
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
        for key, (cardinality, _offset, _kind) in self._containers.items():
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
    # == Indexer.SET_MEANS_PASSABLE, not the bare bit -- see the class's own docstring: three
    # maps in data/decompiled/ share this shape while meaning OPPOSITE things, so a future swap
    # to an inverted-polarity source (e.g. Vitalite's, where SET means BLOCKED) is a one-line
    # change to that flag instead of a silent, subtly-wrong map everywhere this function is used.
    return _map().contains(Indexer.packed(x, y, plane)) == Indexer.SET_MEANS_PASSABLE


def east(x: int, y: int, plane: int) -> bool:
    return _map().contains(Indexer.packed(x, y, plane) | Indexer.EAST_FLAG) == Indexer.SET_MEANS_PASSABLE


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
            if len(distances) >= max_nodes:
                break
            neighbour = (cx + dx, cy + dy)
            if neighbour in distances:
                continue
            if max(abs(neighbour[0] - x), abs(neighbour[1] - y)) > radius:
                continue
            if passable(cx, cy, plane, dx, dy):
                distances[neighbour] = step
                queue.append(neighbour)
    return distances
