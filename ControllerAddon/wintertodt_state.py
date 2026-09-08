"""Pure decision functions for routines/wintertodt.py.

PURE MEANS PURE: every function here takes plain data and returns plain data -- no controller
calls, no HTTP, no clock. That is what lets the whole decision layer be table-tested with no
client, the way hunllef.StyleTracker and the Gauntlet's craft_plan are, and it is where the
first live session's corrections belong.

Design: docs/superpowers/specs/2026-09-08-wintertodt-design.md
"""

import re
import wintertodt_ids as w

BRAZIER_KEYS = ("SOUTH_WEST", "NORTH_WEST", "NORTH_EAST", "SOUTH_EAST")
INVENTORY_SIZE = 28
_DOSE_RE = re.compile(r"\((\d)\)\s*$")


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
    # Prefer the preferred brazier if it's present and not incapacitated.
    if preferred in braziers and pyromancer_status(reader, preferred) != "INCAPACITATED":
        return preferred
    # Otherwise, return the first brazier whose pyromancer is not incapacitated.
    for key in BRAZIER_KEYS:
        if pyromancer_status(reader, key) != "INCAPACITATED":
            return key
    # All pyromancers incapacitated, return preferred if present as last resort.
    return preferred if preferred in braziers else None


def count_item(items, item_id) -> int:
    return sum(1 for i in (items or []) if i.get("id") == item_id)


def has_tool(items, name) -> bool:
    """Substring match on the item NAME, the banker.py keepItems convention.

    Deliberately loose: "axe" also matches a Dragon axe or a battleaxe, and any of those chops
    bruma roots. The arena crate hands out a Bronze axe, so the exact name is not knowable
    offline anyway."""
    needle = name.lower()
    return any(needle in (i.get("name") or "").lower() for i in (items or []))


def potion_doses(items) -> int:
    """Total rejuvenation DOSES held, not potions.

    Each potion holds 4 and each dose restores 30% warmth, so doses are the unit that matters.
    The game writes the dose count into the name as "Rejuvenation potion(3)"; a name with no
    suffix counts as one, which is the safe direction (it under-counts, so we restock early)."""
    total = 0
    for item in (items or []):
        name = (item.get("name") or "").lower()
        if w.POTION_NAME not in name or "(unf)" in name:
            continue
        match = _DOSE_RE.search(name)
        total += int(match.group(1)) if match else 1
    return total


def kit_deficit(items, min_doses) -> list:
    """Everything missing from the kit, as names the routine can act on.

    Ordered tools-first so the axe (which gates the whole loop) is fetched before potions."""
    missing = [tool for tool in w.TOOL_NAMES if not has_tool(items, tool)]
    if potion_doses(items) < min_doses:
        missing.append("potion")
    return missing


def needs_drink(reader, drink_at) -> bool:
    """True only when warmth is READABLE and below the threshold.

    Unreadable answers False: warmth_percent returns None there, and treating None as 0 would
    fire the emergency rung on a bridge blip."""
    percent = warmth_percent(reader)
    return percent is not None and percent < drink_at


def inventory_full(items, size=INVENTORY_SIZE) -> bool:
    return len(items or []) >= size


def feed_item(items, *, fletch, energy_percent, feed_roots_near_end, feed_roots_below_energy):
    """Which item id to feed the brazier, or None when nothing should be fed yet.

    THE DEADLOCK GUARD LIVES HERE, and it is the invariant the spec calls out: `feed` and
    `fletch` must between them accept every state a full inventory can be in. A feed rung that
    accepted only kindling, paired with fletch=False, would strand a full inventory of roots --
    chopping blocked by the full inventory, fletching disabled, and nothing willing to spend
    them. So roots are feedable whenever fletching will not happen: either it is switched off,
    or the round is too near its end to be worth the fletch (a root is 10 points, and 10 beats
    the 0 an unfed root is worth when the round ends)."""
    if count_item(items, w.ITEM_KINDLING):
        return w.ITEM_KINDLING
    if not count_item(items, w.ITEM_ROOT):
        return None
    if not fletch:
        return w.ITEM_ROOT
    near_end = (feed_roots_near_end
                and energy_percent is not None
                and 0 <= energy_percent <= feed_roots_below_energy)
    return w.ITEM_ROOT if near_end else None


def derive_phase(*, reader, items, config, brazier, snow_on_tile, round_over_streak,
                  outside_streak=0) -> str:
    """The ladder, as a pure function of live state. Never reads a stored phase.

    Order is the design, and each rung's position has a reason:

      stop      -- we are not in the arena and this routine owns no travel.
      kit       -- a missing axe idles the whole loop, and a missing potion disarms `warmth`.
                   Runs mid-round as well as between rounds for exactly that reason.
      warmth    -- 0% is instant death and forfeits every point of the round. Outranks all
                   work below it. Only `kit` beats it, and only because you cannot drink a
                   potion you do not have.
      dodge     -- cheap, and the 3x3 attack is the biggest single warmth hit.
      light/fix -- 25 points each, and the wiki says relighting is worth interrupting anything.
      feed      -- spend what we hold.
      fletch    -- turn a full inventory of roots into kindling.
      chop      -- the default during a round.

    `round_over_streak` is a SUSTAINED count of consecutive inactive reads, not a boolean: a
    single transitional snapshot (HUD down, points -1) looks exactly like a round end and is
    the failure family that cost GOTR six live bugs.

    `outside_streak` is the same shape applied to the reader itself (fix round 2, Important 5):
    `_reader()` collapses BOTH a bridge blip and a genuine "not at Wintertodt" read to the same
    falsy/`atWintertodt: False` shape, so a single failed HTTP call must not be a terminal stop
    -- only a SUSTAINED run of reads that fail to confirm `atWintertodt` concludes "stop"."""
    config = config or {}
    if not (reader or {}).get("atWintertodt"):
        if outside_streak >= config.get("notAtArenaReads", 5):
            return "stop"
        # Ride it out rather than act on a single bad read -- chop_phase touches neither
        # `reader` nor the arena, so it is a safe no-op when there is truly nothing to click.
        return "chop"

    deficit = kit_deficit(items, config.get("minDoses", 2))
    cold = needs_drink(reader, config.get("drinkAtWarmth", 45))
    have_doses = potion_doses(items)
    tool_deficit = [d for d in deficit if d != "potion"]
    potion_only_deficit = bool(deficit) and not tool_deficit
    full = inventory_full(items)

    # A missing TOOL always idles the whole loop (an axe gates chopping outright) and is worth
    # restocking regardless of warmth.
    if tool_deficit and not cold:
        return "kit"

    if potion_only_deficit and not full:
        # Fix round 2 (Critical 2a): kit preempts warmth only when there is genuinely nothing
        # left to drink -- holding any dose at all is not an emergency. MEASURED live: warmth
        # fell 740->220 across 13 ticks with THREE drinkable doses held, because
        # minDoses==DOSES_PER_POTION meant every single drink dropped the held count below the
        # threshold and put "potion" back in deficit, re-arming this branch unconditionally on
        # every drink regardless of how many doses were still in hand.
        if not cold or have_doses == 0:
            return "kit"
    # Fix round 2 (Critical 2c): when the ONLY deficit is "potion" and there is no free slot
    # (the normal state for most of a round), a Take-concoction click is guaranteed to be
    # refused -- fall through instead of spending the kit ceiling on it. Whatever frees a slot
    # (feed/fletch/chop, below) gets a turn instead; a tool deficit is unaffected, since a
    # missing tool is worth attempting to restock regardless of room.

    if tool_deficit and cold and have_doses == 0:
        return "kit"          # nothing to drink, and a tool is missing too -- must restock
    if cold:
        return "warmth"       # warmth_phase itself safety-stops when there is truly no dose

    if snow_on_tile and config.get("dodgeSnowfall", True):
        return "dodge"

    if not round_active(reader):
        if round_over_streak >= config.get("roundEndReads", 3):
            return "between_rounds"
        # Below the streak threshold this is a suspected transitional misread, not a
        # confirmed round end -- even if a brazier is genuinely broken on this same tick,
        # we return "chop" rather than falling through to the light/fix checks below.
        # This self-heals on the next tick once `roundActive` reads true again; acting on
        # a single inactive read is exactly the failure family the sustained streak exists
        # to prevent, so don't "fix" this by reordering the branches.
        return "chop"          # ride out a transitional read rather than acting on it

    if brazier:
        status = brazier_status(reader, brazier)
        alive = pyromancer_status(reader, brazier) != "INCAPACITATED"
        if status == "BROKEN" and alive:
            return "fix"
        if status == "UNLIT" and alive:
            return "light"

    feed = feed_item(
        items,
        fletch=config.get("fletch", True),
        energy_percent=(reader or {}).get("energyPercent"),
        feed_roots_near_end=config.get("feedRootsNearRoundEnd", True),
        feed_roots_below_energy=config.get("feedRootsBelowEnergy", 15),
    )
    if feed is not None and brazier_status(reader, brazier) == "LIT":
        return "feed"

    if config.get("fletch", True) and count_item(items, w.ITEM_ROOT) and inventory_full(items):
        return "fletch"

    return "chop"
