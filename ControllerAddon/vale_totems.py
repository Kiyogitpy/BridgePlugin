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
