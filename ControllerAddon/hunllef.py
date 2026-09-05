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
