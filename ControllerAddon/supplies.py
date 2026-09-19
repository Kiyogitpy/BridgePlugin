# supplies.py -- what a slayer task needs carried: teleport charges, food, prayer potions, and
# whether it all fits. PURE: callers hand in item names; nothing here reads the bridge.
# Added 2026-09-19, slayer spec §7.9.
#
# Supplies are PER TASK (USER 2026-09-19): "a lot more for tasks that mostly rely on prayer to
# negate damage, 5-6 full prayer pots ... if it's nechryaels there is unavoidable damage, we
# should bring at least 15 food". Those two figures anchor the profiles; the rest are proposals
# recorded per task in docs/slayer-sites/decisions.md.
#
# The prayer-dose estimate is Slayer Best in Bank's drain-rate model (FreeArcanes, BSD-2,
# PotionDoseEstimator.prayerDoses -- ported as a formula, the plugin is not vendored; survey in
# docs/slayer-sites/slayer-best-in-bank-survey.md). It is grounded in game mechanics: a dose
# restores floor(level/4) + 7 (8 for a super restore) and worn Prayer bonus raises "resistance".
# Its FOOD estimate is deliberately NOT ported: it scales with trip length only and knows nothing
# about which tasks hurt, which is exactly what the user's per-task numbers do know.

import math
import re
from collections import namedtuple

INVENTORY_SLOTS = 28
UNLIMITED = 10 ** 9            # an eternal or never-spent copy: never the limiting factor
_SUFFIX = re.compile(r"\s*\((\d+)\)\s*$")

Supplies = namedtuple("Supplies", ("food", "prayer_potions", "extras"), defaults=((),))

PROFILES = {
    "PRAYER": Supplies(food=5, prayer_potions=6),
    "PRAYER_PLUS_DAMAGE": Supplies(food=15, prayer_potions=6),
    "SAFESPOT": Supplies(food=8, prayer_potions=2),
    "TRIVIAL": Supplies(food=3, prayer_potions=0),
}

PROTECTION_POINTS_PER_MIN = 58.0   # a protection prayer plus an offensive one
ORDINARY_POINTS_PER_MIN = 38.0
MIN_DOSES, MAX_DOSES = 1, 32       # one trip's worth; a longer task restocks (spec §7.5)
DOSES_PER_POTION = 4


def charges(name):
    """`Slayer ring (8)` -> 8, `Ring of dueling(3)` -> 3. None when there is no numeric suffix
    (an Ectophial, a `Slayer ring (eternal)`)."""
    m = _SUFFIX.search(name or "")
    return int(m.group(1)) if m else None


def base_name(name) -> str:
    return _SUFFIX.sub("", name or "").strip().lower()


def _is(name, base) -> bool:
    """`name` is a copy of `base`: the same name once the charge count is stripped, or that name
    plus a bracketed variant (`Slayer ring (eternal)`). NOT a bare prefix test -- "prayer" must
    not match "Prayer book", nor "ring" every ring in the bank (task-5 review)."""
    stripped, base = base_name(name), base.strip().lower()
    return stripped == base or stripped.startswith(base + " (")


def _copy_charges(name, unlimited):
    """Charges in one copy. A copy with NO count is worth nothing unless the caller says this
    item never runs out, or it is an `(eternal)` variant. The suffix alone cannot tell the two
    apart: an Ectophial and an UNCHARGED `Amulet of glory` both lack one, and calling the glory
    unlimited would have a routine skip a restock and then fail to teleport (task-5 review)."""
    c = charges(name)
    if c is not None:
        return c
    return UNLIMITED if unlimited or "(eternal)" in (name or "").lower() else 0


def teleports_left(base, carried, banked, unlimited=False):
    """Charges of `base` across what we carry/wear (`carried`, names) and the bank (`banked`,
    (name, quantity) pairs, or None when the bank has never been seen). Unknown is None, never 0.
    `unlimited=True` is the caller's statement that a count-less copy never runs out (Ectophial)."""
    total = 0
    for name in carried or ():
        if _is(name, base):
            total += _copy_charges(name, unlimited)
    if banked is not None:
        for name, quantity in banked:
            if _is(name, base):
                total += _copy_charges(name, unlimited) * max(0, quantity or 0)
    if total >= UNLIMITED:
        return UNLIMITED
    if banked is None:
        return total if total > 0 else None
    return total


def lowest_charge(names, base):
    """The copy to take/use first, so partly spent rings are finished off rather than stranded."""
    matches = [(charges(n), n) for n in names or () if _is(n, base) and charges(n) is not None]
    return min(matches)[1] if matches else None


def prayer_doses_needed(kills, prayer_level, prayer_bonus=0, protection=True,
                        seconds_per_kill=18.0, downtime_s=0.0, super_restore=False) -> int:
    duration = seconds_per_kill * kills + downtime_s * max(0, kills - 1)
    per_minute = PROTECTION_POINTS_PER_MIN if protection else ORDINARY_POINTS_PER_MIN
    resistance = 60 + 2 * prayer_bonus
    drained = duration * per_minute / resistance
    restore = prayer_level // 4 + (8 if super_restore else 7)
    doses = math.ceil(max(0.0, drained - prayer_level) / restore)
    return max(MIN_DOSES, min(MAX_DOSES, doses))


def prayer_potions_for(profile, doses_needed, cap=8) -> int:
    if profile.prayer_potions == 0:
        return 0
    return min(cap, max(profile.prayer_potions, math.ceil(doses_needed / DOSES_PER_POTION)))


def doses_carried(names, potion) -> int:
    return sum(charges(n) or 0 for n in names or () if _is(n, potion))


def slots_needed(profile, prayer_potions, carry_count, displaced_gear, teleport_items) -> int:
    return profile.food + prayer_potions + len(profile.extras) + carry_count + displaced_gear + teleport_items


def fits(profile, prayer_potions, carry_count, displaced_gear, teleport_items) -> bool:
    return slots_needed(profile, prayer_potions, carry_count, displaced_gear, teleport_items) <= INVENTORY_SLOTS
