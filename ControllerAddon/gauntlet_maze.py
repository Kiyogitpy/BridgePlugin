"""Room geometry and the user's route for the Corrupted Gauntlet prep -- pure.

Spec: docs/superpowers/specs/2026-09-05-gauntlet-prep-design.md ("The route", "Room model").
Rooms are addressed as (right, outward) offsets from the start room S; the boss room is
(0, -1). OUTWARD is the unit cardinal from the Barrier toward S's centre, RIGHT is OUTWARD
rotated clockwise. Everything is instance space (sub-project 1): template coordinates repeat
between rooms and cannot address a room.
"""
from __future__ import annotations

import instance_nav
from hunllef import cheb, flood

# (room offset, action, reveal offset): the user's route, for any start side.
ROUTE = [((1, 0), "enter", None), ((1, 1), "enter", (1, 2)), ((0, 1), "enter", (0, 2)),
         ((-1, 1), "enter", (-1, 2)), ((-1, 0), "enter", None), ((-1, -1), "enter", None),
         ((-1, -2), "enter", None), ((0, -2), "enter", None), ((1, -2), "enter", None),
         ((1, -1), "enter", None)]


def route():
    return list(ROUTE)


def outward_from(barrier, start_centre):
    dx, dy = start_centre[0] - barrier[0], start_centre[1] - barrier[1]
    if abs(dx) >= abs(dy):
        return (1 if dx > 0 else -1, 0)
    return (0, 1 if dy > 0 else -1)


def right_of(outward):
    return (outward[1], -outward[0])


def room_centre(start_centre, pitch, outward, offset):
    r, o = offset
    right = right_of(outward)
    return (start_centre[0] + pitch * (r * right[0] + o * outward[0]),
            start_centre[1] + pitch * (r * right[1] + o * outward[1]))


def room_of(tile, start_centre, pitch, outward):
    dx, dy = tile[0] - start_centre[0], tile[1] - start_centre[1]
    right = right_of(outward)
    r = (dx * right[0] + dy * right[1]) / pitch
    o = (dx * outward[0] + dy * outward[1]) / pitch
    return (int(round(r)), int(round(o)))


def component(imap, start, limit=2000):
    return set(flood(imap, start, limit, blocked=set()))


def component_centre(tiles):
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    return (int(round(sum(xs) / len(xs))), int(round(sum(ys) / len(ys))))


def node_between(nodes, centre_a, centre_b, pitch):
    mid = ((centre_a[0] + centre_b[0]) // 2, (centre_a[1] + centre_b[1]) // 2)
    best, best_d = None, None
    for n in nodes or []:
        pos = (n.get("instX"), n.get("instY"))
        if pos[0] is None:
            continue
        d = cheb(pos, mid)
        if d < pitch // 2 and (best_d is None or d < best_d):
            best, best_d = n, d
    return best


def edge_open(imap, centre_a, centre_b):
    return instance_nav.find_path(imap, centre_a, centre_b) is not None
