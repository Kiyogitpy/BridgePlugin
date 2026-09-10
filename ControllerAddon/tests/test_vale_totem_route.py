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


def test_every_leg_ends_on_the_destination_stand_tile():
    # The markers are click TARGETS, not the human's positions: a leg's last click is the
    # stand tile itself, so arrival needs no separate approach.
    for (a, b), pts in r.LEGS.items():
        assert pts[-1] == r.SITES[b]["stand"], (a, b)


def test_legs_are_the_users_markers():
    """USER 2026-09-10: ground markers of 'the most optimal path', one click per stretch.
    The (3, 2) leg is the LOG BASKET / NO BANK line; the bank branch markers are not used."""
    assert r.LEGS[(7, 6)] == ((1453, 3336), (1466, 3320), (1477, 3331))
    assert r.LEGS[(3, 2)] == ((1369, 3294), (1346, 3318))
    assert r.LEGS[(8, 7)] == ((1415, 3339), (1435, 3346), (1453, 3340))
    assert r.LEGS[(6, 5)] == ((1468, 3314), (1455, 3313), (1437, 3305))


def test_no_click_exceeds_the_minimap_hop_range():
    # Each marker is ONE click from the previous one, and the longest stretch (3->2's second
    # click, 24 tiles) sits exactly at travel.WALK_HOP_MAX_TILES.
    for leg, pts in r.LEGS.items():
        prev = r.SITES[leg[0]]["stand"]
        for q in pts:
            assert max(abs(prev[0] - q[0]), abs(prev[1] - q[1])) <= 24, (leg, prev, q)
            prev = q


