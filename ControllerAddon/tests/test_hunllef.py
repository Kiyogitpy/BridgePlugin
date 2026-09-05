"""hunllef.py: pure Hunllef-fight logic. Spec: docs/superpowers/specs/2026-09-05-hunllef-fight-design.md."""
import hunllef as h
import gauntlet_ids as g
import instance_nav
from travel import BLOCK_MOVEMENT_OBJECT

# The Corrupted Hunllef's animation sequence from recordings/2026-09-05_014024_corrupted-gauntlet-
# humanrun1 (ticks since varbit 9177 went to 1, animation id), and the human's protection-prayer
# clicks (tick, Prayer enum name). Copied here so the test is hermetic.
ANIMS = [(7.0, 8419), (11.9, 8419), (16.9, 8419), (22.0, 8419), (24.0, 8754), (27.0, 8419), (32.0, 8419), (37.0, 8419), (42.1, 8419), (44.0, 8755), (47.0, 8418), (52.0, 8419), (57.0, 8419), (62.0, 8419), (64.0, 8754), (67.0, 8419), (72.0, 8419), (77.0, 8419), (82.0, 8419), (84.0, 8755), (87.0, 8419), (92.0, 8419), (97.0, 8419), (102.0, 8419), (104.0, 8754), (107.0, 8418), (112.0, 8419), (117.0, 8419), (122.0, 8419), (124.0, 8755), (127.0, 8419), (132.0, 8419), (137.0, 8419), (142.0, 8419), (144.0, 8754), (147.0, 8419), (152.0, 8418), (157.0, 8419), (162.0, 8419), (164.0, 8755), (167.0, 8419), (172.0, 8419), (177.0, 8419), (182.0, 8419), (184.0, 8754), (187.0, 8419), (192.0, 8419), (197.0, 8419), (202.0, 8419), (204.0, 8755), (207.0, 8419), (212.0, 8419), (217.0, 8418), (222.0, 8419), (224.0, 8754), (227.0, 8419), (232.0, 8419), (237.0, 8419), (242.0, 8419), (244.0, 8755), (247.0, 8419), (252.0, 8419), (257.0, 8419), (262.0, 8419), (264.0, 8754), (267.0, 8419), (272.0, 8419), (277.0, 8419), (282.0, 8419), (284.0, 8755), (287.0, 8418), (292.0, 8419), (297.0, 8419), (302.0, 8420), (307.0, 8419), (309.0, 8754), (312.0, 8419), (317.0, 8419), (322.0, 8419), (327.0, 8419), (329.0, 8755), (332.0, 8419), (337.0, 8419), (342.0, 8419), (347.0, 8419), (349.0, 8754), (352.0, 8419), (357.0, 8418), (362.0, 8419), (367.0, 8419), (369.0, 8755), (372.0, 8419), (377.0, 8419), (382.1, 8419), (387.0, 8419), (389.0, 8754), (392.0, 8419), (397.0, 8419), (402.0, 8419), (407.0, 8419), (409.0, 8755), (412.0, 8419), (417.0, 8419), (422.0, 8418), (427.0, 8419), (429.0, 8754), (432.0, 8419), (437.0, 8419), (442.0, 8419), (447.0, 8419), (449.0, 8755), (452.0, 8419), (457.0, 8419), (462.0, 8419), (467.0, 8419), (469.0, 8754), (472.0, 8419), (477.0, 8419)]
PRAYER_CLICKS = [(24.1, "PROTECT_FROM_MAGIC"), (34.9, "PROTECT_FROM_MAGIC"), (45.3, "PROTECT_FROM_MISSILES"), (65.7, "PROTECT_FROM_MAGIC"), (86.5, "PROTECT_FROM_MISSILES"), (104.4, "PROTECT_FROM_MAGIC"), (125.4, "PROTECT_FROM_MISSILES"), (144.5, "PROTECT_FROM_MAGIC"), (164.8, "PROTECT_FROM_MISSILES"), (184.7, "PROTECT_FROM_MAGIC"), (189.5, "PROTECT_FROM_MAGIC"), (204.6, "PROTECT_FROM_MISSILES"), (226.9, "PROTECT_FROM_MAGIC"), (235.1, "PROTECT_FROM_MAGIC"), (236.1, "PROTECT_FROM_MAGIC"), (244.1, "PROTECT_FROM_MISSILES"), (265.0, "PROTECT_FROM_MAGIC"), (279.5, "PROTECT_FROM_MAGIC"), (285.0, "PROTECT_FROM_MISSILES"), (317.5, "PROTECT_FROM_MAGIC"), (329.2, "PROTECT_FROM_MISSILES"), (348.8, "PROTECT_FROM_MAGIC"), (355.1, "PROTECT_FROM_MAGIC"), (374.4, "PROTECT_FROM_MISSILES"), (389.5, "PROTECT_FROM_MAGIC"), (400.1, "PROTECT_FROM_MAGIC"), (409.4, "PROTECT_FROM_MISSILES"), (429.4, "PROTECT_FROM_MAGIC"), (449.6, "PROTECT_FROM_MISSILES"), (469.4, "PROTECT_FROM_MAGIC")]


def test_starts_ranged_and_flips_on_each_marker():
    t = h.StyleTracker()
    assert t.style == h.RANGED and t.expected_prayer() == "PROTECT_FROM_MISSILES"
    for _ in range(4):
        assert t.observe(8419) == "attack"
    assert t.next_style_due() and t.expected_prayer() == "PROTECT_FROM_MAGIC"
    assert t.observe(8754) == "marker"
    assert t.style == h.MAGIC and t.attacks == 0 and not t.next_style_due()
    for a in (8418, 8419, 8419, 8419):
        t.observe(a)
    assert t.observe(8755) == "marker"       # the tornado cast is a marker too
    assert t.style == h.RANGED and t.resyncs == 0


def test_stomp_and_idle_are_ignored_and_short_counts_resync():
    t = h.StyleTracker()
    assert t.observe(8420) is None and t.observe(-1) is None and t.attacks == 0
    t.observe(8419); t.observe(8419)
    t.observe(8754)                           # marker after only 2 attacks
    assert t.style == h.MAGIC and t.resyncs == 1 and t.attacks == 0


def test_marker_resync_uses_parity_of_missed_style_periods():
    """I1: a missed marker (attacks silently accumulating past ATTACKS_PER_STYLE because the
    marker event that would have reset it never arrived) must not always apply a single
    unconditional flip -- that inverts the tracker's style whenever an EVEN number of style
    periods were actually missed. flips = max(1, round(attacks / ATTACKS_PER_STYLE)); only
    flips % 2 == 1 applies a flip. Per final-fix-brief.md's own formula: attacks=8 (two periods)
    is even -> no net flip; attacks=3 or 5 (one period, rounded) is odd -> one flip; attacks=12
    (three periods) is odd -> one flip.

    NOTE: the brief's own worked-example parenthetical says "12 -> none", which contradicts the
    formula it states in the same sentence (max(1, round(12/4)) = 3, and 3 %% 2 == 1 -- confirmed
    by direct computation). Implemented per the formula, which also matches the physical model
    (one flip per elapsed 4-attack period, so 3 periods is an odd, net-flipping count) --
    flagged as a brief inconsistency rather than guessed around."""
    for attacks, expect_flip in ((3, True), (5, True), (8, False), (12, True)):
        t = h.StyleTracker()
        t.attacks = attacks
        style_before = t.style
        t.observe(8754)
        flipped = t.style != style_before
        assert flipped == expect_flip, (attacks, t.style, style_before)
        assert t.resyncs == 1 and t.attacks == 0     # every one of these counts is != 4


def test_observe_snapshot_counts_transitions_only():
    t = h.StyleTracker()
    t.observe_snapshot(8419); t.observe_snapshot(8419)     # same value twice: one attack
    assert t.attacks == 1
    t.observe_snapshot(-1); t.observe_snapshot(8419)       # back to idle then attack again
    assert t.attacks == 2


def test_replay_of_the_human_kill_predicts_every_prayer_click():
    """For every protection click the human made, the tracker fed with the Hunllef's animations
    up to that instant expects exactly that prayer -- including the predictive window (after the
    4th attack, before the marker) and the re-activations after prayer-disable hits."""
    t = h.StyleTracker()
    i = 0
    for tick, name in PRAYER_CLICKS:
        while i < len(ANIMS) and ANIMS[i][0] <= tick:
            t.observe(ANIMS[i][1])
            i += 1
        assert t.expected_prayer() == name, (tick, name, t.style, t.attacks)
    for tick, anim in ANIMS[i:]:
        t.observe(anim)
    assert t.markers == sum(1 for _, a in ANIMS if a in g.ANIM_MARKER)
    assert t.resyncs == 0


def test_predictive_window_opens_on_the_fourth_attack():
    t = h.StyleTracker()
    for tick, anim in ANIMS:
        t.observe(anim)
        if tick == 22.0:
            assert t.next_style_due() and t.upcoming_style() == h.MAGIC
            assert t.expected_prayer() == "PROTECT_FROM_MAGIC"
            break


def _player(**kw):
    p = {"hp": 80, "prayer": 50, "instX": 13100, "instY": 5700, "plane": 1, "animation": -1}
    p.update(kw); return p

def _npc(id_, x, y, anim=-1, index=61584):
    return {"id": id_, "index": index, "instX": x, "instY": y, "animationId": anim, "hpRatio": 90,
            "name": "Corrupted Hunllef" if id_ in g.HUNLLEF else "x", "screenX": 500, "screenY": 400}

def _obj(id_, x, y):
    return {"id": id_, "instX": x, "instY": y, "objectType": "GroundObject", "x": 1900, "y": 5600}

def _inv(*names):
    return [{"slot": i, "id": 1, "name": n, "qty": 1, "screenX": 10 * i, "screenY": 5} for i, n in enumerate(names)]

def _snap(**kw):
    args = dict(player=_player(), npcs=[_npc(9036, 13105, 5700, 8419)], objects=[], prayers={"active": {}},
                equipment={"weapon": {"name": "Corrupted bow (perfected)"}},
                inventory=_inv("Corrupted staff (perfected)", "Paddlefish", "Egniol potion (2)"),
                combat={"targetId": 9036})
    args.update(kw)
    return h.FightSnapshot.from_bridge(**args)


def test_snapshot_reads_the_hunllef_tornadoes_and_hazards_in_instance_space():
    s = _snap(npcs=[_npc(9036, 13105, 5700, 8419), _npc(9039, 13098, 5701, index=7), _npc(9040, 13110, 5710, index=8)],
              objects=[_obj(36048, 13100, 5700), _obj(36047, 13101, 5700), _obj(12345, 13102, 5700)])
    assert s.hunllef["id"] == 9036 and s.hunllef["anim"] == 8419 and s.hunllef_centre() == (13105, 5700)
    assert s.tornadoes == [(13098, 5701)]
    assert s.hazards == {(13100, 5700): 36048, (13101, 5700): 36047}
    assert s.me == (13100, 5700) and s.target_id == 9036


def test_snapshot_item_helpers():
    s = _snap()
    assert [i["name"] for i in s.fish()] == ["Paddlefish"]
    assert [i["name"] for i in s.egniol()] == ["Egniol potion (2)"]
    assert s.weapon_kind() == "bow"
    assert s.inventory_weapon("staff")["name"] == "Corrupted staff (perfected)"
    assert s.inventory_weapon("bow") is None
    assert _snap(equipment={"weapon": None}).weapon_kind() is None


def test_snapshot_without_hunllef_and_with_dead_id():
    assert _snap(npcs=[]).hunllef is None
    assert _snap(npcs=[_npc(9038, 1, 1)]).hunllef["id"] == 9038


def test_snapshot_sw_origin_shifts_the_centre():
    s = _snap()
    assert s.hunllef_centre(origin="sw") == (13107, 5702)


# Task 5: footprint, danger, flood, pick_safe_tile
CFG = {"escape_radius": 6, "attack_range": 7, "tornado_danger": 2, "tornado_avoid": 3}
BX, BY = 13056, 5632


def _grid(size=32, blocked=()):
    flags = [0] * (size * size)
    for gx, gy in blocked:
        flags[gy * size + gx] |= BLOCK_MOVEMENT_OBJECT
    return instance_nav.InstanceMap(BX, BY, 1, size, flags)


def G(gx, gy):
    return (BX + gx, BY + gy)


def _fight(me=G(10, 10), hun=G(16, 10), tornadoes=(), hazards=()):
    npcs = [_npc(9036, *hun, 8419)] + [_npc(9039, x, y, index=100 + i) for i, (x, y) in enumerate(tornadoes)]
    objs = [_obj(36048, x, y) for x, y in hazards]
    return _snap(player=_player(instX=me[0], instY=me[1]), npcs=npcs, objects=objs)


def test_footprint_and_margin_are_forbidden():
    c = G(16, 10)
    assert h.forbidden_near_hunllef(G(16, 10), c) and h.forbidden_near_hunllef(G(18, 12), c)
    assert h.forbidden_near_hunllef(G(19, 10), c)          # margin tile, Chebyshev 3
    assert not h.forbidden_near_hunllef(G(20, 10), c)      # Chebyshev 4: allowed


def test_in_danger_reasons():
    assert h.in_danger(_fight(), CFG) is None
    assert h.in_danger(_fight(hazards=[G(10, 10)]), CFG) == "hazard"
    assert h.in_danger(_fight(tornadoes=[G(11, 11)]), CFG) == "tornado"
    assert h.in_danger(_fight(tornadoes=[G(13, 13)]), CFG) is None
    assert h.in_danger(_fight(me=G(15, 10)), CFG) == "under_hunllef"     # inside the 5x5
    assert h.in_danger(_fight(me=G(13, 10)), CFG) is None                # margin tile: not danger


def test_flood_respects_blocked_tiles_and_max_steps():
    # A 3-tall wall of blocked tiles at x=11, y=9..11, directly east of the start.
    imap = _grid(blocked=[(11, 9), (11, 10), (11, 11)])
    d = h.flood(imap, G(10, 10), 3, blocked=set())
    assert d[G(10, 10)] == 0
    assert G(11, 10) not in d                 # blocked tile never entered
    assert d[G(10, 13)] == 3                  # three straight steps north
    assert G(10, 14) not in d                 # four steps: past max_steps
    assert G(12, 10) not in d                 # rounding the wall takes four steps
    assert G(12, 12) not in d                 # (10,11)->(11,12) would cut the blocked corner (11,11): refused
    assert d[G(11, 12)] == 3                  # (10,10)->(10,11)->(10,12)->(11,12): three cardinal steps


def test_pick_safe_tile_avoids_hazards_footprint_and_tornadoes_and_stays_in_range():
    imap = _grid()
    snap = _fight(me=G(10, 10), hun=G(16, 10), tornadoes=[G(8, 10)], hazards=[G(10, 10), G(11, 10), G(9, 10)])
    t = h.pick_safe_tile(snap, imap, CFG)
    assert t is not None
    assert t not in snap.hazards
    assert not h.forbidden_near_hunllef(t, snap.hunllef_centre())
    assert h.cheb(t, G(8, 10)) >= CFG["tornado_avoid"]
    assert h.in_attack_range(t, snap.hunllef_centre(), CFG["attack_range"])
    assert h.cheb(t, G(10, 10)) <= CFG["escape_radius"]


def test_pick_safe_tile_prefers_distance_from_tornadoes_then_nearness():
    imap = _grid()
    snap = _fight(me=G(10, 10), hun=G(20, 10), tornadoes=[G(10, 12)])
    t = h.pick_safe_tile(snap, imap, CFG)
    assert t[1] < G(10, 10)[1]          # moved away from the tornado (north of us), not toward it


def test_pick_safe_tile_escapes_from_under_the_hunllef():
    """C1: the stomp guard (forbidden_near_hunllef) must be a DESTINATION filter only -- flood
    itself must still be able to LEAVE the start tile even when the start tile is inside the
    footprint+margin square, or every tile with Chebyshev <= FOOTPRINT_RADIUS+FOOTPRINT_MARGIN of
    the centre (exactly `in_danger`'s under_hunllef case) would return None from pick_safe_tile
    and the routine could never escape a stomp."""
    imap = _grid(size=32)
    centre = G(16, 16)
    for d in (0, 1, 2):
        snap = _fight(me=(centre[0] + d, centre[1]), hun=centre)
        t = h.pick_safe_tile(snap, imap, CFG)
        assert t is not None, f"Chebyshev {d} from the centre: no escape tile found"
        assert h.cheb(t, centre) >= 4


def test_pick_safe_tile_finds_the_one_gap_in_a_hazard_ring():
    """C1: standing inside the footprint with the whole margin-ring hazarded except one gap tile,
    the flood must route THROUGH the (non-hazard) footprint/margin tiles to reach the gap -- the
    old code blocked the entire footprint+margin square from traversal, so it could never reach
    the gap at all."""
    imap = _grid(size=32)
    centre = G(16, 16)
    gap = (centre[0] + 4, centre[1])       # first non-forbidden tile straight east (Chebyshev 4)
    ring = [(centre[0] + dx, centre[1] + dy)
            for dx in range(-4, 5) for dy in range(-4, 5)
            if max(abs(dx), abs(dy)) == 4 and (centre[0] + dx, centre[1] + dy) != gap]
    snap = _fight(me=centre, hun=centre, hazards=ring)
    t = h.pick_safe_tile(snap, imap, CFG)
    assert t == gap, t


def test_pick_safe_tile_returns_none_when_everything_is_forbidden():
    # A 7x7 grid centred on (3,3) genuinely has no tile at Chebyshev >= 4 from it: the farthest
    # local coordinate the grid holds is a corner at (0,0)/(0,6)/(6,0)/(6,6), each exactly
    # Chebyshev 3 away, one short of clearing FOOTPRINT_RADIUS+FOOTPRINT_MARGIN (3) -- so EVERY
    # tile the grid can offer is forbidden, independent of C1's fix (verified: max(|x-3|,|y-3|)
    # over the whole 7x7 grid is 3). This is NOT a claim that the whole footprint+margin blocks
    # traversal (see C1, final-fix-brief.md: it must not) -- it is a claim about this grid's
    # extent. NOTE: the brief names an 8x8 grid for this fixture, but a size-8 grid's corner (at
    # local (7,3) etc.) is Chebyshev 4 from (3,3) and IS reachable/unforbidden -- confirmed live
    # against the fixed pick_safe_tile, which returns a real tile on a size-8 grid. 7 is the
    # largest size for which the claimed fact ("no tile at Chebyshev >= 4") actually holds; the
    # size-32 assertion below is what proves the same me/hun setup escapes once the grid is not
    # the thing constraining it.
    imap = _grid(size=7)
    snap = _fight(me=G(3, 3), hun=G(3, 3))
    assert h.pick_safe_tile(snap, imap, CFG) is None
    imap32 = _grid(size=32)
    assert h.pick_safe_tile(snap, imap32, CFG) is not None


def test_require_in_range_filters():
    imap = _grid()
    snap = _fight(me=G(2, 2), hun=G(30, 30))
    assert h.pick_safe_tile(snap, imap, CFG, require_in_range=True) is None
    assert h.pick_safe_tile(snap, imap, CFG) is not None


# Task 6: decisions and the ladder
FULL_CFG = dict(CFG, eat_at=45, egniol_at=15, melee_weapon="bow",
                offensive_prayers={"bow": ["RIGOUR", "DEADEYE", "EAGLE_EYE"], "staff": ["AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"]})


def _ready(**kw):
    """A snapshot where nothing needs doing: praying right, right weapon, fed, attacking, in range."""
    base = dict(player=_player(hp=80, prayer=50, instX=BX + 10, instY=BY + 10),
                npcs=[_npc(9036, BX + 16, BY + 10, 8419)],
                prayers={"active": {"PROTECT_FROM_MISSILES": True, "AUGURY": True}},
                equipment={"weapon": {"name": "Corrupted staff (perfected)"}},
                inventory=_inv("Corrupted bow (perfected)", "Paddlefish", "Egniol potion (2)"),
                combat={"targetId": 9036}, objects=[])
    base.update(kw)
    return h.FightSnapshot.from_bridge(**base)


def test_desired_weapon_is_the_style_it_is_not_praying_against():
    assert h.desired_weapon(9036, FULL_CFG) == "staff"     # praying ranged -> hit it with magic
    assert h.desired_weapon(9037, FULL_CFG) == "bow"
    assert h.desired_weapon(9035, FULL_CFG) == "bow"       # melee prayer: config default
    assert h.desired_weapon(9038, FULL_CFG) == "bow"


def test_offensive_prayer_skips_unusable_ones():
    assert h.offensive_prayer_for("bow", FULL_CFG, set()) == "RIGOUR"
    assert h.offensive_prayer_for("bow", FULL_CFG, {"RIGOUR"}) == "DEADEYE"
    assert h.offensive_prayer_for("staff", FULL_CFG, {"AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"}) is None


def test_ladder_order():
    t = h.StyleTracker()                                   # RANGED -> expects PROTECT_FROM_MISSILES
    assert h.ladder(_ready(), t, FULL_CFG)[0] == "idle"
    assert h.ladder(_ready(npcs=[]), t, FULL_CFG)[0] == "no_hunllef"
    assert h.ladder(_ready(objects=[_obj(36048, BX + 10, BY + 10)]), t, FULL_CFG)[0] == "escape"
    assert h.ladder(_ready(prayers={"active": {"AUGURY": True}}), t, FULL_CFG) == ("prayer", "PROTECT_FROM_MISSILES")
    assert h.ladder(_ready(npcs=[_npc(9037, BX + 16, BY + 10)]), t, FULL_CFG) == ("wield", "bow")
    assert h.ladder(_ready(prayers={"active": {"PROTECT_FROM_MISSILES": True}}), t, FULL_CFG) == ("prayer", "AUGURY")
    assert h.ladder(_ready(player=_player(hp=40, instX=BX + 10, instY=BY + 10)), t, FULL_CFG)[0] == "eat"
    assert h.ladder(_ready(player=_player(prayer=10, instX=BX + 10, instY=BY + 10)), t, FULL_CFG)[0] == "drink"
    assert h.ladder(_ready(combat={"targetId": None}), t, FULL_CFG)[0] == "attack"
    assert h.ladder(_ready(player=_player(instX=BX + 13, instY=BY + 10)), t, FULL_CFG)[0] == "reposition"   # margin ring
    assert h.ladder(_ready(player=_player(instX=BX + 30, instY=BY + 10)), t, FULL_CFG)[0] == "reposition"   # out of range


def test_ladder_escape_beats_everything_and_prayer_beats_weapon():
    t = h.StyleTracker()
    s = _ready(objects=[_obj(36048, BX + 10, BY + 10)], prayers={"active": {}}, npcs=[_npc(9037, BX + 16, BY + 10)],
               player=_player(hp=20, instX=BX + 10, instY=BY + 10), combat={"targetId": None})
    assert h.ladder(s, t, FULL_CFG)[0] == "escape"


def test_ladder_predictive_prayer_switch():
    t = h.StyleTracker()
    for _ in range(4):
        t.observe(8419)
    assert h.ladder(_ready(), t, FULL_CFG) == ("prayer", "PROTECT_FROM_MAGIC")


def test_eat_and_drink_fallbacks():
    assert not h.eat_due(_ready(inventory=_inv("Corrupted bow (perfected)")), FULL_CFG)          # no fish
    low = _ready(player=_player(hp=30, instX=BX + 10, instY=BY + 10), inventory=_inv("Egniol potion (1)"))
    assert h.drink_due(low, FULL_CFG)                                                             # hp low, no fish -> egniol
    assert not h.drink_due(_ready(), FULL_CFG)
    assert h.ladder(_ready(inventory=_inv("Corrupted bow (perfected)"), player=_player(hp=30, instX=BX + 10, instY=BY + 10)), h.StyleTracker(), FULL_CFG)[0] == "idle"
