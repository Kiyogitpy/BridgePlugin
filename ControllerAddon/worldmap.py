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
