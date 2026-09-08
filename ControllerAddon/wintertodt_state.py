"""Pure decision functions for routines/wintertodt.py.

PURE MEANS PURE: every function here takes plain data and returns plain data -- no controller
calls, no HTTP, no clock. That is what lets the whole decision layer be table-tested with no
client, the way hunllef.StyleTracker and the Gauntlet's craft_plan are, and it is where the
first live session's corrections belong.

Design: docs/superpowers/specs/2026-09-08-wintertodt-design.md
"""

import wintertodt_ids as w

BRAZIER_KEYS = ("SOUTH_WEST", "NORTH_WEST", "NORTH_EAST", "SOUTH_EAST")


def _braziers(reader):
    return ((reader or {}).get("braziers") or {})


def brazier_status(reader, key) -> str:
    return str((_braziers(reader).get(key) or {}).get("status") or "UNKNOWN")


def pyromancer_status(reader, key) -> str:
    return str((_braziers(reader).get(key) or {}).get("pyromancerStatus") or "UNKNOWN")


def brazier_operational(entry) -> bool:
    """The reader's Java isOperational(), recomputed.

    Gson serializes FIELDS, not the reader's helper METHODS, so `operational` is simply absent
    from the JSON -- verified against the live response. Same trap CLAUDE.md records for GOTR's
    round phase."""
    entry = entry or {}
    return (entry.get("status") == "LIT"
            and entry.get("pyromancerStatus") == "ALIVE")


def warmth_percent(reader):
    """Warmth as a percentage, or None when it cannot be read.

    `warmth` is a raw 0-1000 scale. None rather than 0 for an unreadable snapshot is
    load-bearing: 0 means "about to die" and would fire the emergency rung against a bridge
    blip. This is the m8aq readers' own None-is-not-empty rule."""
    if not isinstance(reader, dict):
        return None
    raw = reader.get("warmth")
    if raw is None:
        return None
    return raw / 10.0


def round_active(reader) -> bool:
    return bool((reader or {}).get("roundActive"))


def reward_eligible(reader) -> bool:
    """The reader's Java isRewardEligible(), recomputed -- also absent from the JSON."""
    points = (reader or {}).get("points")
    return points is not None and points >= w.REWARD_THRESHOLD


def choose_brazier(reader, current=None, preferred="SOUTH_WEST"):
    """Which brazier to work, from the HUD alone -- no scene scan.

    Order: the one we are already working (stickiness beats churn), then any operational one,
    then the configured preference. A brazier whose pyromancer is INCAPACITATED cannot be relit
    at all (wiki), and healing is out of scope, so we abandon it rather than idle beside it."""
    braziers = _braziers(reader)
    if not braziers:
        return None
    if current and brazier_operational(braziers.get(current)):
        return current
    if current and pyromancer_status(reader, current) != "INCAPACITATED" \
            and brazier_status(reader, current) in ("UNLIT", "BROKEN"):
        # Ours is down but fixable by us -- keep it rather than walking to another.
        return current
    for key in BRAZIER_KEYS:
        if brazier_operational(braziers.get(key)):
            return key
    for key in BRAZIER_KEYS:
        if pyromancer_status(reader, key) != "INCAPACITATED":
            return preferred if preferred in braziers else key
    return preferred if preferred in braziers else None
