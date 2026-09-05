"""Pure logic for the Corrupted Hunllef fight -- no bridge, no serial, no sleeps.

Spec: docs/superpowers/specs/2026-09-05-hunllef-fight-design.md. Every constant here was measured
in recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1 or read from the game cache; the
routine (routines/corrupted_gauntlet.py) does the I/O and calls in here for every decision, so
this module is where the fight is TESTED.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import gauntlet_ids as g

RANGED = "RANGED"
MAGIC = "MAGIC"
PROTECT_FOR = {RANGED: "PROTECT_FROM_MISSILES", MAGIC: "PROTECT_FROM_MAGIC"}
ATTACKS_PER_STYLE = 4


class StyleTracker:
    """Which attack style the Hunllef is on, from its animations.

    Measured shape (spec, "Style alternates at a MARKER animation"): four attack-like animations
    (8419 standard, 8418 prayer-disabling), then a marker two ticks later (8754 "switch style" or
    8755 tornado cast, alternating), then the next style's first attack three ticks after that.
    Ranged first. The stomp (8420) sits outside the count. So:
      observe(attack)  -> attacks += 1
      observe(marker)  -> style flips PARITY TIMES (I1, final-fix-brief.md), attacks = 0 (a count
                          other than 4 is logged as a resync)
    A marker's flip is not unconditional: attacks/ATTACKS_PER_STYLE elapsed periods (rounded, at
    least 1) means that many flips actually happened since the tracker last saw a marker, and an
    even count of missed markers cancels back to no net change -- see observe()'s own comment.
    next_style_due() is true from the 4th attack until the marker: the coming style is known
    five ticks before its first attack lands, which is when the routine switches prayer.
    """

    def __init__(self):
        self.style = RANGED
        self.attacks = 0
        self.markers = 0
        self.resyncs = 0
        self._last_snapshot_anim = None

    def observe(self, anim) -> str | None:
        if anim in g.ANIM_ATTACK_LIKE:
            self.attacks += 1
            return "attack"
        if anim in g.ANIM_MARKER:
            if self.attacks != ATTACKS_PER_STYLE:
                self.resyncs += 1
            # I1, final-fix-brief.md: a marker that arrives after MORE than one style period's
            # worth of attacks means one or more PREVIOUS markers were missed (lossy UDP, or a
            # snapshot read landing between two attacks) -- style flips once per elapsed period,
            # so an unconditional single flip here is only correct when exactly one period
            # elapsed. An EVEN number of missed periods must net to NO flip (two flips cancel
            # out), or the tracker ends up inverted from the real game state until the next
            # marker inverts it back (or doesn't, depending on parity) -- i.e. wrong roughly half
            # the time after any missed marker. flips is never 0 (max(1, ...)): a marker with
            # attacks == 0 still means at least the flip it is itself announcing.
            flips = max(1, round(self.attacks / ATTACKS_PER_STYLE))
            if flips % 2 == 1:
                self.style = MAGIC if self.style == RANGED else RANGED
            self.attacks = 0
            self.markers += 1
            return "marker"
        return None

    def observe_snapshot(self, anim) -> str | None:
        """Feed from a per-tick /npcs read: only a CHANGE of animation counts (the same attack
        animation is reported for several ticks). Misses an attack when the idle (-1) between two
        attacks falls between two reads; the marker resyncs that."""
        if anim == self._last_snapshot_anim:
            return None
        self._last_snapshot_anim = anim
        return self.observe(anim)

    def next_style_due(self) -> bool:
        return self.attacks >= ATTACKS_PER_STYLE

    def upcoming_style(self) -> str:
        if self.next_style_due():
            return MAGIC if self.style == RANGED else RANGED
        return self.style

    def expected_prayer(self) -> str:
        return PROTECT_FOR[self.upcoming_style()]


FOOTPRINT_ORIGIN = "centre"   # what /npcs instX/instY is for a size-5 NPC; live checklist item 1


@dataclass
class FightSnapshot:
    """One game tick's worth of bridge reads, in instance space."""
    hp: int
    prayer: int
    me: tuple
    plane: int
    my_anim: int
    hunllef: dict | None
    tornadoes: list
    hazards: dict
    prayers_active: dict
    weapon: str | None
    inventory: list
    target_id: int | None

    @classmethod
    def from_bridge(cls, player, npcs, objects, prayers, equipment, inventory, combat):
        hun = None
        tornadoes = []
        for n in npcs or []:
            nid = n.get("id")
            if nid in g.HUNLLEF and hun is None:
                hun = {"id": nid, "index": n.get("index"), "x": n.get("instX"), "y": n.get("instY"),
                       "anim": n.get("animationId"), "hp_ratio": n.get("hpRatio"), "entity": n}
            elif nid in g.TORNADO:
                tornadoes.append((n.get("instX"), n.get("instY")))
        hazards = {(o.get("instX"), o.get("instY")): o.get("id")
                   for o in objects or [] if o.get("id") in g.HAZARD_TILES}
        weapon = (equipment or {}).get("weapon") or {}
        return cls(
            hp=int(player.get("hp") or 0), prayer=int(player.get("prayer") or 0),
            me=(player.get("instX"), player.get("instY")), plane=int(player.get("plane") or 0),
            my_anim=player.get("animation", -1), hunllef=hun, tornadoes=tornadoes, hazards=hazards,
            prayers_active=(prayers or {}).get("active") or {}, weapon=weapon.get("name") or None,
            inventory=list(inventory or []), target_id=(combat or {}).get("targetId"))

    def hunllef_centre(self, origin=None):
        if self.hunllef is None or self.hunllef["x"] is None:
            return None
        x, y = self.hunllef["x"], self.hunllef["y"]
        if (origin or FOOTPRINT_ORIGIN) == "sw":
            half = g.HUNLLEF_SIZE // 2
            return (x + half, y + half)
        return (x, y)

    def fish(self):
        return [i for i in self.inventory if i.get("name") in g.FISH_NAMES]

    def egniol(self):
        return [i for i in self.inventory if str(i.get("name", "")).startswith(g.EGNIOL_PREFIX)]

    def weapon_kind(self):
        if self.weapon in g.BOW_NAMES:
            return "bow"
        if self.weapon in g.STAFF_NAMES:
            return "staff"
        return None

    def inventory_weapon(self, kind):
        names = g.BOW_NAMES if kind == "bow" else g.STAFF_NAMES
        for name in names:                       # perfected first
            for item in self.inventory:
                if item.get("name") == name:
                    return item
        return None


FOOTPRINT_RADIUS = g.HUNLLEF_SIZE // 2   # 2: the 5x5 is centre +-2
FOOTPRINT_MARGIN = 1                     # one tile more, so it can step toward us without a stomp
STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def forbidden_near_hunllef(tile, centre):
    """The stomp guard: inside the footprint OR on the margin ring. Every walk target the routine
    ever picks passes through here (user requirement 2026-09-05: a stomp is up to 68)."""
    return centre is not None and cheb(tile, centre) <= FOOTPRINT_RADIUS + FOOTPRINT_MARGIN


def in_attack_range(tile, centre, attack_range):
    return centre is not None and cheb(tile, centre) - FOOTPRINT_RADIUS <= attack_range


def in_danger(snap, cfg):
    """Why we must move THIS tick, or None."""
    if snap.me in snap.hazards:
        return "hazard"
    for t in snap.tornadoes:
        if cheb(snap.me, t) <= cfg["tornado_danger"]:
            return "tornado"
    c = snap.hunllef_centre()
    if c is not None and cheb(snap.me, c) <= FOOTPRINT_RADIUS:
        return "under_hunllef"
    return None


def flood(imap, start, max_steps, blocked):
    """Tiles reachable from start in <= max_steps 8-way steps over the live grid, never entering
    `blocked`. Returns {tile: steps}. The Hunllef room is small; this is a few hundred tiles."""
    dist = {start: 0}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        d = dist[(x, y)]
        if d >= max_steps:
            continue
        for dx, dy in STEPS:
            nxt = (x + dx, y + dy)
            if nxt in dist or nxt in blocked or not imap.step_ok(x, y, dx, dy):
                continue
            dist[nxt] = d + 1
            queue.append(nxt)
    return dist


def tile_is_safe(snap, tile, cfg):
    """Whether `tile` passes pick_safe_tile's DESTINATION filters -- off hazards, outside the
    footprint+margin, at least tornado_avoid from every tornado -- against the CURRENT snapshot.

    (I5, final-fix-brief.md) Deliberately does not check attack range or flood reachability: this
    exists so a previously-picked escape target can be judged "still good enough to keep walking
    to" cheaply, every tick, without re-running the flood -- see do_escape's target stickiness."""
    if tile in snap.hazards:
        return False
    if forbidden_near_hunllef(tile, snap.hunllef_centre()):
        return False
    tor = min((cheb(tile, t) for t in snap.tornadoes), default=99)
    if tor < cfg["tornado_avoid"]:
        return False
    return True


def pick_safe_tile(snap, imap, cfg, require_in_range=False):
    """The tile to run to: reachable within escape_radius, off hazards, outside the footprint and
    its margin, at least tornado_avoid from every tornado; preferring distance from tornadoes,
    then being in attack range, then nearness to us. None when nothing qualifies.

    (C1, final-fix-brief.md) The footprint+margin square is a DESTINATION filter only, applied in
    the selection loop below via `forbidden_near_hunllef` (through `tile_is_safe`) -- it must
    NEVER be passed to flood()'s `blocked`, which refuses to enter a blocked tile at all. From any
    tile with Chebyshev <= FOOTPRINT_RADIUS+FOOTPRINT_MARGIN of the centre (exactly `in_danger`'s
    under_hunllef case, i.e. every tile this function is ever asked to escape FROM), blocking the
    whole square trapped the flood on its own start tile and this returned None unconditionally --
    the fight's escape action could never actually escape a stomp."""
    centre = snap.hunllef_centre()
    blocked = set(snap.hazards)
    reach = flood(imap, snap.me, cfg["escape_radius"], blocked)
    best, best_key = None, None
    for tile, steps in reach.items():
        if tile == snap.me or not tile_is_safe(snap, tile, cfg):
            continue
        tor = min((cheb(tile, t) for t in snap.tornadoes), default=99)
        ranged = in_attack_range(tile, centre, cfg["attack_range"]) if centre is not None else True
        if require_in_range and not ranged:
            continue
        key = (-min(tor, 6), 0 if ranged else 1, steps)
        if best_key is None or key < best_key:
            best, best_key = tile, key
    return best


def desired_weapon(hunllef_id, cfg):
    prayer = g.HUNLLEF_PRAYER_BY_ID.get(hunllef_id)
    if prayer == "RANGED":
        return "staff"
    if prayer == "MAGIC":
        return "bow"
    return cfg.get("melee_weapon", "bow")


def offensive_prayer_for(kind, cfg, unusable):
    for name in cfg.get("offensive_prayers", {}).get(kind, []):
        if name not in unusable:
            return name
    return None


def eat_due(snap, cfg):
    return snap.hp <= cfg["eat_at"] and bool(snap.fish())


def drink_due(snap, cfg):
    if not snap.egniol():
        return False
    return snap.prayer <= cfg["egniol_at"] or (snap.hp <= cfg["eat_at"] and not snap.fish())


def ladder(snap, tracker, cfg, unusable_prayers=frozenset()):
    """The one action for this tick. Order is the spec's: escape > protection prayer > weapon >
    offensive prayer > eat/drink > attack > reposition > idle."""
    if snap.hunllef is None:
        return ("no_hunllef", None)
    if in_danger(snap, cfg):
        return ("escape", in_danger(snap, cfg))
    expected = tracker.expected_prayer()
    if not snap.prayers_active.get(expected):
        return ("prayer", expected)
    want = desired_weapon(snap.hunllef["id"], cfg)
    if snap.weapon_kind() != want and snap.inventory_weapon(want) is not None:
        return ("wield", want)
    offensive = offensive_prayer_for(snap.weapon_kind() or want, cfg, unusable_prayers)
    if offensive and not snap.prayers_active.get(offensive):
        return ("prayer", offensive)
    if eat_due(snap, cfg):
        return ("eat", None)
    if drink_due(snap, cfg):
        return ("drink", None)
    if snap.target_id not in g.HUNLLEF:
        return ("attack", None)
    centre = snap.hunllef_centre()
    if centre is not None:
        if (forbidden_near_hunllef(snap.me, centre)
                or not in_attack_range(snap.me, centre, cfg["attack_range"])):
            return ("reposition", None)
    return ("idle", None)
