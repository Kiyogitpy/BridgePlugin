"""Pure tests for trawling_routes.py. Every coordinate here is hand-computed from the OSRS Wiki's
own tile-marker tables (Giant krill shoal, rev 15237362) with
worldX = (regionId >> 8) * 64 + regionX, worldY = (regionId & 0xFF) * 64 + regionY."""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import trawling_routes as tr


def test_world_of_matches_the_region_packing():
    # region 6452 = (25, 52) chunks -> base (1600, 3328)
    assert tr.world_of(6452, 16, 51) == (1616, 3379)
    # region 6196 = (24, 52) chunks -> base (1536, 3328)
    assert tr.world_of(6196, 38, 10) == (1574, 3338)


def test_great_sound_stops_are_the_wiki_order():
    stops = tr.stops_world("giant_krill", "great_sound")
    assert len(stops) == 7
    assert stops[0] == (1616, 3379)     # Wiki label 1
    assert stops[1] == (1580, 3387)     # label 2
    assert stops[3] == (1574, 3338)     # label 4
    assert stops[6] == (1580, 3365)     # label 7


def test_nearest_stop_returns_index_point_and_distance():
    idx, point, dist = tr.nearest_stop("giant_krill", "great_sound", 1576, 3340)
    assert idx == 3
    assert point == (1574, 3338)
    assert dist == math.hypot(2, 2)


def test_next_stop_wraps_to_the_start_of_the_lap():
    assert tr.next_stop("giant_krill", "great_sound", 6) == (0, (1616, 3379))
    assert tr.next_stop("giant_krill", "great_sound", 0) == (1, (1580, 3387))


def test_route_bounds_and_membership():
    assert tr.route_bounds("giant_krill", "great_sound") == (1560, 3338, 1616, 3387)
    assert tr.in_route_area("giant_krill", "great_sound", 1580, 3360)
    assert tr.in_route_area("giant_krill", "great_sound", 1550, 3330, margin=24)
    assert not tr.in_route_area("giant_krill", "great_sound", 3200, 3200, margin=24)


def test_leg_ticks_is_the_lap_minus_the_stops():
    # 1234 tick lap, 7 stops of 150 ticks -> 184 moving ticks over 7 legs
    assert tr.leg_ticks("giant_krill", "great_sound") == 184 / 7


def test_every_stored_route_is_traversable_at_a_plausible_boat_speed():
    """The evidence that the Wiki's label 1..7 really is the VISITING order: with the stops in
    that order, every leg's implied speed lands in a tight, plausible band (Great Sound 1.18
    tiles/tick average and 1.57 worst leg, Simian Sea 1.47/1.78, Sunset Bay 1.49/2.52, Turtle Belt
    1.27/1.58). A wrong ordering would scatter these well past any real boat speed."""
    for (shoal, route) in tr.ROUTES:
        stops = tr.stops_world(shoal, route)
        budget = tr.leg_ticks(shoal, route) * tr.MAX_BOAT_TILES_PER_TICK
        for i, here in enumerate(stops):
            nxt = stops[(i + 1) % len(stops)]
            assert math.dist(here, nxt) <= budget, f"{shoal}/{route} leg {i}"


def test_all_shoal_ids_includes_the_mixed_shoal_that_replaces_it():
    assert tr.all_shoal_ids("giant_krill") == (59734, 59740)


def test_every_route_key_names_a_known_shoal():
    for (shoal, _route) in tr.ROUTES:
        assert shoal in tr.SHOALS
