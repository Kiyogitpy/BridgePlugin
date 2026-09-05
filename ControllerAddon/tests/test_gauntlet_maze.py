import gauntlet_maze as m
import instance_nav

BX, BY = 13056, 5632


def test_outward_and_right():
    assert m.outward_from((100, 100), (100, 110)) == (0, 1)      # start north of the barrier
    assert m.outward_from((100, 100), (89, 101)) == (-1, 0)
    assert m.right_of((0, 1)) == (1, 0) and m.right_of((1, 0)) == (0, -1)
    assert m.right_of((0, -1)) == (-1, 0) and m.right_of((-1, 0)) == (0, 1)


def test_route_matches_the_spec_for_start_north_of_boss():
    # spec: S=(2,3) north of B=(3,3); rows down, cols right. outward=(0,1) means "up" = row-1.
    def grid(offset):
        r, o = offset
        return (2 - o, 3 + r)
    entered = [grid(off) for off, act, _ in m.route()]
    assert entered == [(2, 4), (1, 4), (1, 3), (1, 2), (2, 2), (3, 2), (4, 2), (4, 3), (4, 4), (3, 4)]
    reveals = [grid(rv) for _, _, rv in m.route() if rv]
    assert reveals == [(0, 4), (0, 3), (0, 2)]
    assert len(m.route()) == 10


def test_room_centre_rotates_with_outward():
    s = (1000, 1000)
    assert m.room_centre(s, 16, (0, 1), (1, 0)) == (1016, 1000)     # right of S when outward is north
    assert m.room_centre(s, 16, (0, 1), (0, 1)) == (1000, 1016)
    assert m.room_centre(s, 16, (1, 0), (1, 0)) == (1000, 984)      # outward east: right is south
    assert m.room_of((1015, 1001), s, 16, (0, 1)) == (1, 0)
    assert m.room_of(s, s, 16, (0, 1)) == (0, 0)


def test_component_and_centre():
    size = 16
    flags = [0] * (size * size)
    from travel import BLOCK_MOVEMENT_OBJECT
    for gy in range(size):
        flags[gy * size + 8] |= BLOCK_MOVEMENT_OBJECT        # a wall down the middle
    imap = instance_nav.InstanceMap(BX, BY, 1, size, flags)
    comp = m.component(imap, (BX + 2, BY + 2))
    assert (BX + 7, BY + 15) in comp and (BX + 9, BY + 2) not in comp
    assert len(comp) == 8 * 16
    assert m.component_centre(comp) == (BX + 3, BY + 7) or m.component_centre(comp) == (BX + 4, BY + 8)


def test_node_between_picks_the_midpoint_node():
    nodes = [{"instX": 100, "instY": 108, "id": 35999}, {"instX": 100, "instY": 92, "id": 35999}, {"instX": 130, "instY": 100, "id": 35999}]
    assert m.node_between(nodes, (100, 100), (100, 116), 16)["instY"] == 108
    assert m.node_between(nodes, (100, 100), (100, 84), 16)["instY"] == 92
    assert m.node_between(nodes, (100, 100), (116, 100), 16) is None       # nothing near (108,100)


def test_edge_open_uses_the_grid(monkeypatch):
    imap = instance_nav.InstanceMap(BX, BY, 1, 8, [0] * 64)
    assert m.edge_open(imap, (BX + 1, BY + 1), (BX + 6, BY + 6))
    monkeypatch.setattr(instance_nav, "find_path", lambda *a, **kw: None)
    assert not m.edge_open(imap, (BX + 1, BY + 1), (BX + 6, BY + 6))
