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
      observe(marker)  -> style flips, attacks = 0 (a count other than 4 is logged as a resync)
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
