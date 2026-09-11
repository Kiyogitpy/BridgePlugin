"""Vale Totems state, re-derived from GET /api/minigames/vale-totems.

PURE: no HTTP, no client, no serial port, so every rule below is table-testable.

WHY THIS MODULE EXISTS AT ALL: the vendored m8aq reader already computes the interesting
questions -- isFullyCarved(), isFullyDecorated(), getMissingNearbySpirits() -- but they are
METHODS, and Gson serializes FIELDS. None of them reach the JSON. Same trap CLAUDE.md records
for GOTR's round phase and Wintertodt's isOperational.
"""
from dataclasses import dataclass, field

# nearbyValue, straight off the reader's Spirit enum -- and the same number the carve menu's
# options are ordered by. CONFIRMED three ways: the human recording's 14 builds, the cache's
# enum, and totem-fletching's fixed A..E option order.
SPIRIT_VALUES = {"BUFFALO": 1, "JAGUAR": 2, "EAGLE": 3, "SNAKE": 4, "SCORPION": 5}

# A carved segment stores the spirit as nearbyValue + 9 (BUFFALO 10 .. SCORPION 14). This is
# TotemService.getCarvedAnimalsStatus's `progressKey = animal + 9` and m8aq's carvedValue table.
CARVED_OFFSET = 9

# Ent visits a totem survives, by wood (reader's Wood.maximumDurability).
WOOD_DURABILITY = {"OAK": 2, "WILLOW": 3, "MAPLE": 4, "YEW": 5, "MAGIC": 6, "REDWOOD": 7}

MAX_DECORATIONS = 4

# The reader's own OFFERING_CAP (ValeTotems.java, "the documented soft cap"): a build on a
# capped pile is refused by the game, and that is the ONLY reason a pile is ever claimed.
# USER 2026-09-10: "skip picking up vale offerings since they stack up on the floor and i
# can pick them later saving the routine some time".
OFFERING_CAP = 15_000


@dataclass
class Site:
    number: int
    base_value: int
    wood: str
    base_carved: bool
    segment_values: tuple
    decorations: int
    nearby_spirits: list = field(default_factory=list)
    durability_remaining: int = 0
    unclaimed_offerings: int = 0


@dataclass
class State:
    sites: dict
    research_points: int
    total_unclaimed: int
    at_vale_totems: bool


def parse_state(payload):
    """Parse the reader's snapshot, or None when it cannot be trusted.

    None covers three cases that must never be mistaken for "nothing to do": the endpoint
    being unavailable (get_api returns None on an un-rebuilt client or a bridge that is down),
    the reader throwing (an "error" key instead of its fields), and a malformed body."""
    if not isinstance(payload, dict) or "error" in payload:
        return None
    raw_sites = payload.get("sites")
    if not isinstance(raw_sites, list) or not raw_sites:
        return None
    sites = {}
    for raw in raw_sites:
        number = raw.get("siteNumber")
        if number is None:
            continue
        sites[number] = Site(
            number=number,
            base_value=raw.get("baseValue", 0),
            wood=raw.get("wood") or "NONE",
            base_carved=bool(raw.get("baseCarved")),
            segment_values=tuple((raw.get(k) or {}).get("value", 0)
                                 for k in ("bottom", "middle", "top")),
            decorations=raw.get("decorations", 0),
            nearby_spirits=[s for s in (raw.get("nearbySpirits") or [])
                            if s in SPIRIT_VALUES],
            durability_remaining=raw.get("durabilityRemaining", 0),
            unclaimed_offerings=raw.get("unclaimedOfferings", 0),
        )
    if not sites:
        return None
    return State(sites=sites,
                 research_points=payload.get("researchPoints", 0),
                 total_unclaimed=payload.get("totalUnclaimedOfferings", 0),
                 at_vale_totems=bool(payload.get("atValeTotems")))


def carved_spirits(site) -> set:
    """The spirits already carved into this totem's three segments."""
    carved = set()
    for name, value in SPIRIT_VALUES.items():
        if value + CARVED_OFFSET in site.segment_values:
            carved.add(name)
    return carved


def missing_spirits(site) -> list:
    """Nearby spirits not yet carved, in the reader's own order.

    The wiki is explicit that the nearby spirits can change WHILE the player is carving, so
    this must be re-derived from a fresh read before every carve rather than computed once at
    build time."""
    done = carved_spirits(site)
    return [s for s in site.nearby_spirits if s not in done]


def offering_capped(site) -> bool:
    return site.unclaimed_offerings >= OFFERING_CAP


def needs_build(site) -> bool:
    return site.base_value == 0


def needs_carve(site) -> bool:
    """The SERVER owns "carving is finished" -- baseCarved, not the segment count.

    A totem can show three carved segments while baseCarved is still false; trusting the
    segments would leave the totem unfinished and the decorate step would refuse."""
    return not needs_build(site) and not site.base_carved


def needs_decorate(site) -> bool:
    return (not needs_build(site) and site.base_carved
            and site.decorations < MAX_DECORATIONS)


# One log builds the totem, four more become the decorations. Wiki: 40 logs for all eight.
LOGS_PER_TOTEM = 5


def site_is_finished(site) -> bool:
    """Built, carved and fully decorated -- nothing left to do but let the ents visit."""
    return (not needs_build(site) and site.base_carved
            and site.decorations >= MAX_DECORATIONS)


# HOW LONG A STANDING TOTEM IS WORTH WAITING FOR, in ent visits still in it. Ents move every
# 80 ticks (48s, wiki) and each visit costs one durability, so a durability reading is very
# nearly a countdown in 48-second units.
#
# WHY THIS EXISTS (USER 2026-09-11, on moving up to magic logs: "the time they stay up has
# increased so we need to adapt so we dont just run around in circles"): a lap is ~276s
# (MEASURED run 9, eight sites including the bank trip). A YEW totem holds 5 visits, ~240s, so
# the ring always came back to a collapsed totem and always had work. MAGIC holds 6, ~288s --
# the ring arrives ~12s EARLY, finds nothing to do, and does not come back for another 276s,
# so every site then sits collapsed and earning nothing for most of the lap. The cost is not
# the running; it is the dead ground behind us.
DEFAULT_WAIT_DURABILITY = 1


def hold_due(site, *, wait_durability: int = DEFAULT_WAIT_DURABILITY) -> bool:
    """True when the only thing left at this site is waiting for an ent to fell the totem.

    Deliberately narrow. It answers "nothing to click here AND the totem is about to fall",
    never "nothing to click here" -- a totem with visits left is another ~48s each, and
    standing through those is worse than walking the ring and coming back.
    """
    if not site_is_finished(site) or offering_capped(site):
        return False
    return site.durability_remaining <= wait_durability


def next_action(site, *, logs: int, bows: int):
    """The single next thing to do at `site`, as (action, argument).

    A LADDER, re-derived from live state every call rather than a script -- which is what lets
    a still-standing totem with durability left fall straight through to ("done", None) and be
    passed. The recording rebuilds every site every lap only because a yew totem's 5 durability
    is spent by the time the ~4.4-minute lap comes round; that is a property of the lap time,
    not a rule.

    A CAPPED pile is claimed before anything else, because a build on a capped pile is refused
    by the game; a pile below the cap is left on the floor for the user (2026-09-10).

    Running out of a material yields ("done", None), NOT a retry -- the caller walks on and the
    bank rung deals with it. A rung that cannot progress must never hold the routine in place.
    """
    if offering_capped(site):
        return ("claim", None)
    if needs_build(site):
        return ("build", None) if logs > 0 else ("done", None)
    if needs_carve(site):
        missing = missing_spirits(site)
        if missing:
            return ("carve", missing[0])
        # baseCarved is false but nothing nearby is missing: the spirits rotated under us.
        # Fall through rather than guess -- the next read will show the new trio.
        return ("done", None)
    if needs_decorate(site):
        return ("decorate", None) if bows > 0 else ("done", None)
    return ("done", None)


def logs_needed_for_lap(state, bows: int = 0) -> int:
    """Logs required to finish every site that still has work outstanding.

    Used only to decide whether a bank trip is due, so it counts the WORK LEFT rather than
    assuming a full lap: a site already built needs its decorations but not its build log.

    `bows` already in hand cover decorations one for one and are subtracted -- MEASURED
    2026-09-09 on the host: 20 bows and 4 logs in hand read as "40 logs short" and sent the
    routine banking with four free slots, where a four-log round and a stale bank position
    turned into a safety stop."""
    builds = 0
    decorations = 0
    for site in state.sites.values():
        if site_is_finished(site):
            continue
        if needs_build(site):
            builds += 1
            decorations += MAX_DECORATIONS
        else:
            decorations += max(0, MAX_DECORATIONS - site.decorations)
    return builds + max(0, decorations - bows)


def builds_needed(state) -> int:
    """Sites that still need a totem erected -- one LOG each, and a log a bow cannot replace."""
    return sum(1 for site in state.sites.values()
               if not site_is_finished(site) and needs_build(site))


def bows_needed(state) -> int:
    """Decorations still outstanding across the ring, which is also the bow count.

    logs_needed_for_lap already charges an unbuilt site LOGS_PER_TOTEM (1 build + 4
    decorations) and a built one its missing decorations, so the bows are simply that total
    minus the one build log each unbuilt site owes."""
    return max(0, logs_needed_for_lap(state) - builds_needed(state))


def lap_builds(state) -> int:
    """Build logs the LAP AHEAD owes: one per site, whatever is standing this instant.

    Every standing totem will have collapsed by the time the ring returns to it -- that is
    what a lap IS -- so the builds outstanding right now systematically under-count it."""
    return len(state.sites)


def lap_decorations(state) -> int:
    """Decorations the LAP AHEAD owes: MAX_DECORATIONS per site, same reasoning."""
    return len(state.sites) * MAX_DECORATIONS


def fletch_budget(state, logs: int, bows: int) -> int:
    """How many more bows are worth fletching right now. 0 means "do not fire the fletch".

    THE DEADLOCK THIS EXISTS TO PREVENT (final review 2026-09-08): start_fletching picks a make
    option with NO quantity, so the game keeps fletching until the next click interrupts it --
    8-14 bows over a 15-25s leg where the design budgets 4. Every bow is a log already spent,
    and `build` needs a LOG specifically, so a routine that fletches whatever it is holding
    walks itself into "plenty of bows, no logs" and can never build again. That is a genuine
    deadlock, not waste.

    So the build logs are RESERVED first: only logs beyond the ones the builds owe may become
    bows. The fletch itself is still not stopped mid-action -- the next site's click interrupts
    it, which is how the human plays -- this only decides whether to start one.

    BOTH HALVES COUNT THE LAP, NOT THE INSTANT (2026-09-11, MEASURED live -- see
    test_a_finished_RING_STILL_FLETCHES...). Keyed on the work outstanding right now, this
    returned 0 for the whole of lap 2 with magic logs, because every totem still read complete:
    the routine cut nothing on any leg and then rebuilt site 5 with 1 of 4 decorations holding
    13 logs and no bows. A totem standing on its last visit is work that has not happened yet,
    and a snapshot lands either side of the game's 45-second durability tick by luck. The
    reserve moved with it: keyed on the instant, a fully-standing ring reserves NOTHING and
    every log becomes a bow -- the same deadlock from the other end.

    This is the lesson _bank_trip_due already learned as LAP_UNITS (CLAUDE.md, run 17).
    """
    want = max(0, lap_decorations(state) - bows)
    spare = max(0, logs - lap_builds(state))
    return min(want, spare)
