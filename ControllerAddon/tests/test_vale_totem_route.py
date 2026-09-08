import math

import vale_totem_route as r


def test_every_site_is_present_with_the_offerings_id_four_above_the_base():
    assert sorted(r.SITES) == [1, 2, 3, 4, 5, 6, 7, 8]
    for n, site in r.SITES.items():
        assert site["offerings_id"] == site["base_id"] + 4, n


def test_the_ring_visits_every_site_once_and_wraps():
    assert sorted(r.RING) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert r.next_in_ring(6) == 5
    assert r.next_in_ring(1) == 8
    assert r.next_in_ring(7) == 6


def test_there_is_a_leg_for_every_ring_step():
    for a in r.RING:
        b = r.next_in_ring(a)
        assert r.leg_waypoints(a, b), "no waypoints for leg %d->%d" % (a, b)


def test_every_leg_starts_and_ends_beside_its_sites():
    # The recorded path starts where the human stood at A and ends where they stood at B.
    for (a, b), pts in r.LEGS.items():
        assert math.dist(pts[0], r.SITES[a]["stand"]) <= 4, (a, b)
        assert math.dist(pts[-1], r.SITES[b]["stand"]) <= 4, (a, b)


def test_no_hop_exceeds_minimap_range():
    # travel.WALK_HOP_MAX_TILES is 24 and the measured minimap reach is 28; the extracted
    # waypoints max out around 12, so anything near the cap means a typo.
    for leg, pts in r.LEGS.items():
        for p, q in zip(pts, pts[1:]):
            assert math.dist(p, q) <= 20, (leg, p, q)
