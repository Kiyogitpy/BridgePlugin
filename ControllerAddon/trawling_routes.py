"""Deep sea trawling: shoal definitions and the shoals' fixed patrol routes.

Pure data and pure lookups -- no bridge, no client, no imports from this project. Shoal paths
are PREDETERMINED and cyclical (OSRS Wiki, Deep sea trawling rev 15297127: "The movements of
each shoal are predetermined, making their path, speed, stopping points, and time spent still
cyclical in nature"), so the stop points are a table rather than something to discover live.

Stops are stored as the Wiki's own raw tile markers -- (regionId, regionX, regionY) -- and
converted here, so no coordinate is transcribed by hand. Source: the "Routes" section of each
shoal's Wiki page (Giant krill shoal rev 15237362, Haddock shoal rev 15293151); the marker
"label" is the visiting order.

THE TABLE IS AN ACCELERATOR, NEVER TRUTH. The live shoal object in /objects decides where the
shoal actually is; these stops only aim the boat ahead of it while it is in transit, and give
routines/deep_sea_trawling.py somewhere to sail when no shoal is in the loaded scene. If the
shoal turns up somewhere else, the ordinary follow corrects on the next tick.

NOT MEASURED LIVE. See the spec's live-verification checklist item 8: log the shoal's tile at
each stop for one lap and diff it against stops_world().
"""

from typing import Dict, List, NamedTuple, Tuple

# The bound exists to catch a SCRAMBLED stop order, not to pin down a boat's real
# speed (nobody has measured that). With the Wiki's own label order the implied per-leg
# speeds are tight and plausible -- great_sound 1.18 avg / 1.57 max, simian_sea 1.47/1.78,
# sunset_bay 1.49/2.52, turtle_belt 1.27/1.58 -- while a wrong order teleports across the
# route box and lands far above 3.
MAX_BOAT_TILES_PER_TICK = 3.0


class ShoalSpec(NamedTuple):
    key: str
    name: str
    object_ids: Tuple[int, ...]     # the shoal's own clickbox loc ids
    mixed_ids: Tuple[int, ...]      # mixed shoals that replace it 10% of the time
    fishing_level: int
    stop_ticks: int                 # ticks the shoal sits still at each stop
    depths: Tuple[str, ...]         # ("shallow",) means no depth management is needed


class Route(NamedTuple):
    lap_ticks: int
    markers: Tuple[Tuple[int, int, int], ...]   # (regionId, regionX, regionY), visiting order


# Cache config/loc ids (dump 5bc2ff1e): sailing_shoal_clickbox_* 59734..59742.
SHOALS: Dict[str, ShoalSpec] = {
    "giant_krill": ShoalSpec(
        key="giant_krill", name="Giant krill shoal",
        object_ids=(59734,), mixed_ids=(59740,),          # 59740 = shimmering shoal
        fishing_level=69, stop_ticks=150, depths=("shallow",)),
    "haddock": ShoalSpec(
        key="haddock", name="Haddock shoal",
        object_ids=(59735,), mixed_ids=(59740,),
        fishing_level=73, stop_ticks=120, depths=("shallow",)),
}


ROUTES: Dict[Tuple[str, str], Route] = {
    # --- Giant krill (90s stops) ---------------------------------------------------------
    # Great Sound is the v1 default: 7 stops inside ~56x49 tiles, so every stop is inside the
    # 104x104 loaded scene from anywhere on the route and the shoal is essentially always in
    # /objects.
    ("giant_krill", "great_sound"): Route(1234, (
        (6452, 16, 51), (6196, 44, 59), (6196, 25, 36), (6196, 38, 10),
        (6196, 44, 51), (6196, 24, 42), (6196, 44, 37))),
    ("giant_krill", "simian_sea"): Route(1196, (
        (11048, 12, 48), (11049, 30, 5), (11048, 54, 48), (11304, 23, 31),
        (11304, 13, 12), (11047, 43, 63), (11048, 35, 28))),
    ("giant_krill", "sunset_bay"): Route(1250, (
        (5934, 31, 5), (6189, 33, 56), (6189, 40, 24), (6188, 54, 56),
        (5933, 50, 16), (5933, 22, 8), (5933, 25, 41))),
    ("giant_krill", "turtle_belt"): Route(1410, (
        (11815, 19, 29), (11815, 0, 49), (11816, 27, 6), (11815, 49, 35),
        (12071, 9, 16), (11815, 41, 6), (11814, 24, 45), (11559, 61, 10))),

    # --- Haddock (72s stops) -------------------------------------------------------------
    # These routes span five or more regions, so the shoal LEAVES the loaded scene mid-leg and
    # the follow has to fall back on the table. Not the v1 default for that reason.
    ("haddock", "anglerfishs_light"): Route(1303, (
        (11301, 1, 9), (11045, 20, 43), (10789, 53, 10), (11044, 23, 36),
        (11044, 18, 10), (10788, 14, 20), (10789, 25, 51), (11045, 31, 63))),
    ("haddock", "sea_of_shells"): Route(1674, (
        (12068, 13, 2), (11812, 7, 9), (11812, 30, 61), (12068, 33, 47),
        (12324, 25, 0), (12323, 14, 16), (12322, 24, 38), (12066, 51, 22),
        (11810, 60, 28), (11810, 31, 45))),
}


def world_of(region_id: int, rx: int, ry: int) -> Tuple[int, int]:
    """A Wiki tile marker -> absolute world coordinates. A region id packs the 64x64 chunk as
    (chunkX << 8) | chunkY."""
    return ((region_id >> 8) * 64 + rx, (region_id & 0xFF) * 64 + ry)


def _route(shoal: str, route: str) -> Route:
    try:
        return ROUTES[(shoal, route)]
    except KeyError:
        raise KeyError(f"no route {route!r} for shoal {shoal!r}; known: "
                       f"{sorted(k for k in ROUTES if k[0] == shoal)}")


def stops_world(shoal: str, route: str) -> List[Tuple[int, int]]:
    return [world_of(*m) for m in _route(shoal, route).markers]


def nearest_stop(shoal: str, route: str, x: float, y: float):
    stops = stops_world(shoal, route)
    best = min(range(len(stops)),
               key=lambda i: (stops[i][0] - x) ** 2 + (stops[i][1] - y) ** 2)
    bx, by = stops[best]
    return best, (bx, by), ((bx - x) ** 2 + (by - y) ** 2) ** 0.5


def next_stop(shoal: str, route: str, index: int):
    stops = stops_world(shoal, route)
    nxt = (index + 1) % len(stops)
    return nxt, stops[nxt]


def route_bounds(shoal: str, route: str) -> Tuple[int, int, int, int]:
    stops = stops_world(shoal, route)
    xs = [s[0] for s in stops]
    ys = [s[1] for s in stops]
    return (min(xs), min(ys), max(xs), max(ys))


def in_route_area(shoal: str, route: str, x: float, y: float, margin: int = 24) -> bool:
    minx, miny, maxx, maxy = route_bounds(shoal, route)
    return (minx - margin) <= x <= (maxx + margin) and (miny - margin) <= y <= (maxy + margin)


def leg_ticks(shoal: str, route: str) -> float:
    """Expected ticks the shoal spends travelling between two consecutive stops."""
    r = _route(shoal, route)
    n = len(r.markers)
    return (r.lap_ticks - n * SHOALS[shoal].stop_ticks) / n


def all_shoal_ids(shoal: str) -> Tuple[int, ...]:
    """Every loc id that counts as "our shoal", including the mixed shoal that replaces it 10%
    of the time (Wiki: mixed shoals keep the original's movements and stop timers)."""
    spec = SHOALS[shoal]
    return spec.object_ids + spec.mixed_ids
