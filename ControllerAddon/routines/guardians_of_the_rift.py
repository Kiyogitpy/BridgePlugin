# routines/guardians_of_the_rift.py
#
# Guardians of the Rift, unattended, on mass/official worlds: mine guardian fragments,
# craft guardian essence at the Workbench (filling essence pouches, depositing crafted
# runes in the Deposit Pool), imbue at whichever altar pair is active (choosing the
# energy type we have LESS of), power the Great Guardian with the stones, and spend the
# charged cell on the barrier hub. Between rounds: repair degraded essence pouches via
# NPC Contact (Lunar spellbook) and restock uncharged cells. Reward points are hoarded
# -- the Rewards Guardian is never touched.
#
# Design: docs/superpowers/specs/2026-09-01-guardians-of-the-rift-design.md
# NO RECORDING EXISTS for this activity (user decision, 2026-09-01): every constant in
# the LIVE-VERIFY block below is a research-derived first draft. The spec's
# "Live-verification checklist" section is the first live session's worklist.
#
# Run with:
#     python controller.py routines/guardians_of_the_rift.py
#
# Ground truth at runtime is GET /api/minigames/guardians-of-the-rift (the vendored m8aq
# reader). Gson serializes FIELDS, so the reader's Java convenience methods
# (isRoundActive() etc.) are absent from the JSON -- round_state() below re-derives them
# from the raw fields. Nothing load-bearing lives in ctx.state.data: every tick
# re-derives from the live snapshot, so restarts resume correctly.

import time

import controller
import debug_log
import travel

DEFAULT_CONFIG = {
    "use_large_remains": False,   # 56 Agility rubble shortcut to the east pocket
    "repair_pouches": True,       # NPC Contact -> Dark Mage between rounds when degraded
    "min_uncharged_cells": 5,     # restock from the cell table between rounds below this
    "craft_at_fragments": 60,     # start a workbench pass at/above this many fragments
    "altar_at_essence": 20,       # head for an altar when loose essence >= this
    "blocked_altars": [],         # altar enum names this account cannot enter
                                  # (quest-gated: COSMIC, LAW, DEATH, BLOOD)

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # No Controller Settings panel section (gemstone_crab pattern) -- config lives here.
    return DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Pure logic: game tables and thresholds
# ---------------------------------------------------------------------------

# Runecraft level to imbue at each altar (wiki "Charging cells" table, rev 15303509).
ALTAR_LEVELS = {"AIR": 1, "MIND": 2, "WATER": 5, "EARTH": 9, "FIRE": 14, "BODY": 20,
                "COSMIC": 27, "CHAOS": 35, "NATURE": 44, "LAW": 54, "DEATH": 65,
                "BLOOD": 77}

# Live barrier tiers, weakest first -- recharge targets the lowest.
BARRIER_LIVE_ORDER = ["WEAK", "MEDIUM", "STRONG", "OVERCHARGED"]

REPAIR_FRAGMENTS = 12      # fragments consumed repairing a broken cell tile (wiki)
PORTAL_MIN_SECONDS = 8     # don't chase a huge-remains portal closing sooner than this
ALTAR_MIN_SECONDS = 10     # don't ENTER an altar rotating away sooner than this


# ---------------------------------------------------------------------------
# Pure logic: phase derivation and selection
# ---------------------------------------------------------------------------

def round_state(st):
    """One of: unavailable / outside / lobby / waiting / active / complete / idle.
    Derived from raw reader fields (the Java isRoundActive() helpers do not serialize)."""
    if st is None or st.get("error"):
        return "unavailable"
    if not st.get("inMainTemple"):
        return "outside"
    if not st.get("inArena"):
        return "lobby"
    power = st.get("guardianPower", -1)
    if st.get("finishedGame") or power >= 100:
        return "complete"
    if st.get("participating"):
        if power == 0:
            return "waiting"
        if 0 < power < 100:
            return "active"
    return "idle"


def total_stones(inv):
    return (inv.get("catalyticGuardianStones", 0)
            + inv.get("elementalGuardianStones", 0)
            + inv.get("polyelementalGuardianStones", 0)
            + inv.get("polycatalyticGuardianStones", 0))


def find_repairable(barriers):
    """The first cell tile offering Repair, or None."""
    for b in barriers or []:
        if b.get("state") == "BROKEN_REPAIRABLE":
            return b
    return None


def choose_cell_tile(barriers):
    """Where the charged cell goes: first INACTIVE tile (place, 2 energy both types),
    else the lowest-tier live barrier (recharge). None when nothing usable is loaded."""
    barriers = barriers or []
    for b in barriers:
        if b.get("state") == "INACTIVE":
            return b
    live = [b for b in barriers if b.get("state") in BARRIER_LIVE_ORDER]
    if not live:
        return None
    return min(live, key=lambda b: BARRIER_LIVE_ORDER.index(b["state"]))


def choose_altar(st, rc_level, blocked=()):
    """Which ACTIVE altar to visit: the type whose earned energy is LOWER (one point per
    100 energy per type -- balance is what makes points), falling back to the other when
    level- or quest-gated. rc_level None means "cannot read the level" and gates nothing.
    None = neither active altar is usable right now."""
    def usable(name):
        if not name or name == "UNKNOWN" or name in blocked:
            return False
        level = ALTAR_LEVELS.get(name)
        if level is None:
            return False
        return rc_level is None or rc_level >= level

    elemental = st.get("elementalAltar") or "UNKNOWN"
    catalytic = st.get("catalyticAltar") or "UNKNOWN"
    prefer_elemental = st.get("elementalEnergy", 0) <= st.get("catalyticEnergy", 0)
    order = (elemental, catalytic) if prefer_elemental else (catalytic, elemental)
    for name in order:
        if usable(name):
            return name
    return None


# ---------------------------------------------------------------------------
# Pure logic: essence pouches (/api/storage/essence-pouches shape:
# {"pouches": {"SMALL": {"storedEssence": n, "capacity": n, "repairNeeded": bool,
#  "inInventory": bool, ...}, ...}, ...})
# ---------------------------------------------------------------------------

def _held_pouches(api):
    pouches = (api or {}).get("pouches")
    if not isinstance(pouches, dict):
        return None
    return [p for p in pouches.values() if isinstance(p, dict) and p.get("inInventory")]


def pouches_degraded(api):
    """True when any pouch in the inventory reports repairable degradation."""
    held = _held_pouches(api)
    return bool(held) and any(p.get("repairNeeded") for p in held)


def pouch_free_space(api):
    """Free essence capacity across held pouches; None = reader unavailable
    ("cannot tell" -- callers must not treat it as 0)."""
    held = _held_pouches(api)
    if held is None:
        return None
    return sum(max(0, p.get("capacity", 0) - p.get("storedEssence", 0)) for p in held)


def pouch_stored(api):
    """Essence sitting inside held pouches (0 when the reader is unavailable)."""
    held = _held_pouches(api)
    return sum(p.get("storedEssence", 0) for p in held) if held else 0


def choose_action(st, cfg, pouch_space=None, disabled=()):
    """The one in-round action for this tick, priority-ordered (spec: 'in_round priority
    ladder'). Pure; the runtime passes `disabled` for rungs it has learned cannot work
    right now (missing cell-tile objects, blocked altars, a spent portal budget)."""
    inv = st.get("inventory") or {}
    barriers = st.get("barriers") or []

    # Being inside a side area overrides the ladder -- finish what we are inside of.
    if (st.get("currentAltar") or "UNKNOWN") != "UNKNOWN" and "altar_trip" not in disabled:
        return "altar_trip"
    if st.get("miningArea") == "WEST_HUGE_REMAINS" and "portal_trip" not in disabled:
        return "portal_trip"

    if ("repair_barrier" not in disabled and find_repairable(barriers) is not None
            and inv.get("guardianFragments", 0) >= REPAIR_FRAGMENTS):
        return "repair_barrier"
    if ("portal_trip" not in disabled and st.get("guardianEssencePortalOpen")
            and st.get("guardianEssencePortalSecondsRemaining", -1) >= PORTAL_MIN_SECONDS
            and (inv.get("emptySlots", 0) > 0 or (pouch_space or 0) > 0)):
        return "portal_trip"
    if total_stones(inv) > 0:
        return "spend_stones"
    if "place_cell" not in disabled and (inv.get("chargedCellTier") or "NONE") != "NONE":
        return "place_cell"
    essence = inv.get("guardianEssence", 0)
    if "altar_trip" not in disabled and (
            essence >= cfg["altar_at_essence"]
            or (pouch_space == 0 and essence > 0)):
        return "altar_trip"
    if inv.get("guardianFragments", 0) >= cfg["craft_at_fragments"]:
        return "craft"
    return "mine"
