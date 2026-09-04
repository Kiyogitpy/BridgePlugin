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

import re
import time

import bridge_events
import controller
import debug_log
import settings
import travel

DEFAULT_CONFIG = {
    "use_large_remains": True,    # 56 Agility rubble shortcut to the east pocket
    "repair_pouches": True,       # NPC Contact -> Dark Mage between rounds when degraded
    "min_uncharged_cells": 5,     # restock from the cell table between rounds below this
    "craft_at_fragments": 120,    # round-start mining batch before the first bench pass
                                  # (wiki Strategies rev 15279817: ~120 frags without a
                                  # colossal pouch, 165-170 with; mine ONCE big, then
                                  # work fragments down over the round)
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

REPAIR_FRAGMENTS = 12      # historical (wiki phrasing); the gate is the charged cell
REPAIR_MAX_DISTANCE = 12   # only chase a repair this close -- on a mass world a
                           # cross-arena trip loses the race (live 2026-09-01: tile
                           # sniped mid-walk, routine turned around at the rubble)
PORTAL_MIN_SECONDS = 8     # don't chase a huge-remains portal closing sooner than this
PORTAL_MIN_FREE = 8        # free capacity (slots + pouch space) a trip must be worth
ALTAR_MIN_SECONDS = 6      # don't ENTER an altar rotating away sooner than this (was 10;
                           # the walk-aware gate below covers the run, this is teleport slack)
ARENA_DIRECT_CLICK_TILES = 20   # arena-side targets this close are CLICKED, not walked to first
ALTAR_ENTER_MARGIN_S = 3.0 # ...and the run to the guardian portal must fit before it too


# ---------------------------------------------------------------------------
# Pure logic: phase derivation and selection
# ---------------------------------------------------------------------------

def side_area(st):
    """None / 'altar' / 'pocket': altar rooms and the huge-remains mine are SEPARATE
    map regions (MEASURED live 2026-09-01: Fire altar region 10315 at ~(2574,4849),
    portal mine region 10827 at ~(2715,4835)), so the reader's region-keyed
    inMainTemple reads FALSE inside them mid-round. The HUD varbits still serve there
    (guardianPower read 66/87 inside the Fire altar), so a live power reading with
    inMainTemple false means a side area of OUR round, never 'left the temple'."""
    if st is None or st.get("inMainTemple"):
        return None
    if (st.get("currentAltar") or "UNKNOWN") != "UNKNOWN":
        return "altar"
    if st.get("guardianPower", -1) >= 0:
        return "pocket"
    return None


def round_state(st):
    """One of: unavailable / outside / lobby / waiting / active / complete / idle.
    Derived from raw reader fields (the Java isRoundActive() helpers do not serialize)."""
    if st is None or st.get("error"):
        return "unavailable"
    if side_area(st):
        return "active"
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


def choose_cell_tile(barriers, cell_tier=None, skip=()):
    """Where the charged cell goes, best energy first (wiki "Gaining energy", rev
    15303509): STRENGTHEN a live barrier exactly one tier below the cell (7/13/22
    energy both types), else RECHARGE the lowest-tier live barrier (2/5/9/15 -- a
    lesser cell on a stronger barrier heals it, USER 2026-09-03: "dump our cell"),
    else PLACE on an INACTIVE tile (2). `skip` holds (x, y) of tiles that ignored
    our clicks (a barrier at full health has nothing to recharge); they are tried
    again once the memory expires. None when nothing usable is loaded."""
    barriers = [b for b in barriers or []
                if ((b.get("location") or {}).get("x"), (b.get("location") or {}).get("y")) not in skip]
    live = [b for b in barriers if b.get("state") in BARRIER_LIVE_ORDER]
    if cell_tier in BARRIER_LIVE_ORDER:
        want = BARRIER_LIVE_ORDER.index(cell_tier) - 1
        below = [b for b in live if BARRIER_LIVE_ORDER.index(b["state"]) == want]
        if below:
            return below[0]
    if live:
        return min(live, key=lambda b: BARRIER_LIVE_ORDER.index(b["state"]))
    for b in barriers:
        if b.get("state") == "INACTIVE":
            return b
    return None


ENERGY_BALANCE_GAP = 40     # once one energy type leads by this much, the trip goes to
                            # the LAGGING type's altar even if it is the lower tier
                            # (USER 2026-09-02: "balance catalytic and elemental if
                            # possible, keeping priority but balance it out" -- run 45
                            # ended 25/209). A full trip is ~78 energy, so 40 is half a
                            # trip: small gaps still take the higher-tier altar.
ALTAR_TRIP_MAX_POWER = 96   # no new altar trip this close to 100%: run 45's trip at 99%
                            # came back to a finished round with 40 stones and a cell


def choose_altar(st, rc_level, blocked=()):
    """Which ACTIVE altar to visit: the HIGHER-TIER usable one -- more xp per essence
    (USER 2026-09-01: "prefer altars that give more experience") -- UNLESS one energy
    type leads by ENERGY_BALANCE_GAP or more, in which case the lagging type's altar
    wins when it is usable (USER 2026-09-02). Below the gap, balance is only the
    tiebreak. Level/quest gates unchanged. rc_level None means "cannot read the
    level" and gates nothing. None = neither active altar is usable right now."""
    def usable(name):
        if not name or name == "UNKNOWN" or name in blocked:
            return False
        level = ALTAR_LEVELS.get(name)
        if level is None:
            return False
        return rc_level is None or rc_level >= level

    elemental = st.get("elementalAltar") or "UNKNOWN"
    catalytic = st.get("catalyticAltar") or "UNKNOWN"
    e_energy = st.get("elementalEnergy", 0)
    c_energy = st.get("catalyticEnergy", 0)
    prefer_elemental = e_energy <= c_energy
    candidates = [n for n in (elemental, catalytic) if usable(n)]
    if not candidates:
        return None
    if abs(e_energy - c_energy) >= ENERGY_BALANCE_GAP:
        lagging = elemental if prefer_elemental else catalytic
        if lagging in candidates:
            return lagging
    return max(candidates,
               key=lambda n: (ALTAR_LEVELS.get(n, 0),
                              (n == elemental) == prefer_elemental))


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
    """True when a held pouch has actually LOST capacity. Deliberately NOT the
    reader's `repairNeeded` flag: MEASURED live 2026-09-01, Medium and Large read
    repairNeeded=true while at FULL capacity (6/6, 9/9) -- that flag tracks
    accumulated decay toward the next break, not lost capacity -- and the routine
    was casting repair on healthy pouches (USER: "why are we repairing full bags?")."""
    held = _held_pouches(api)
    return bool(held) and any(
        p.get("capacity", 0) < p.get("maximumCapacity", 0) for p in held)


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


def choose_action(st, cfg, pouch_space=None, disabled=(), crafting=False,
                  batch_done=False):
    """The one in-round action for this tick, priority-ordered (spec: 'in_round priority
    ladder'). Pure; the runtime passes `disabled` for rungs it has learned cannot work
    right now (missing cell-tile objects, blocked altars, a spent portal budget), and
    `crafting` when the bench is visibly mid-craft (keeps this rung chosen while
    fragments burn down below the start threshold -- live 2026-09-01: without it, one
    bench click then straight back over the rubble to mine, every cycle)."""
    inv = st.get("inventory") or {}
    barriers = st.get("barriers") or []

    # Being inside a side area overrides the ladder -- finish what we are inside of.
    # (miningArea is region-gated to the temple, so inside the portal pocket it reads
    # NONE -- side_area's power-with-no-temple signal is what detects the pocket.)
    if (st.get("currentAltar") or "UNKNOWN") != "UNKNOWN" and "altar_trip" not in disabled:
        return "altar_trip"
    if ((st.get("miningArea") == "WEST_HUGE_REMAINS" or side_area(st) == "pocket")
            and "portal_trip" not in disabled):
        return "portal_trip"

    # Portal talismans are round-scoped junk occupying real slots (USER 2026-09-01:
    # "portal talismans can be dropped") -- one Drop click each, ahead of everything.
    if sum((inv.get("portalTalismans") or {}).values()) > 0:
        return "drop_talismans"

    if ("repair_barrier" not in disabled and find_repairable(barriers) is not None
            and (inv.get("chargedCellTier") or "NONE") != "NONE"):
        # USER 2026-09-01: "repair can only be done with a charged cell" -- without
        # one the game silently omits the Repair option (live tick 116: menu showed
        # only ground actions), the same silent-refusal shape as golem's no-fur.
        return "repair_barrier"
    # Stones are turned in BEFORE a portal trip (USER 2026-09-01), and a trip is
    # only worth it with real capacity to fill -- "basically full inventory, it's
    # pointless going into portal".
    if total_stones(inv) > 0:
        return "spend_stones"
    if ("portal_trip" not in disabled and st.get("guardianEssencePortalOpen")
            and st.get("guardianEssencePortalSecondsRemaining", -1) >= PORTAL_MIN_SECONDS
            and inv.get("emptySlots", 0) + (pouch_space or 0) >= PORTAL_MIN_FREE):
        return "portal_trip"
    # A cell or an empty cell pouch must not pull us OUT of the large-remains pocket
    # before the batch is mined (run 44: a WEAK cell carried over from the previous
    # round made place_cell outrank mining at power 10 and the bot climbed the rubble
    # to the barrier ring instead of mining the batch). Both wait for batch_done.
    in_pocket = st.get("miningArea") == "EAST_LARGE_REMAINS" and not batch_done
    # A charged cell becomes a RIFT GUARDIAN first (Assemble at an essence pile with the
    # chisel): 2x+1 energy of ONE type -- 6/15/27/45 -- against 7/13/22 both-types for
    # strengthening a barrier, and the one-type payout is the natural energy balancer.
    # The human benchmark (recordings/2026-09-03_061331_guaridianoftherift-benchmark,
    # 7545 RC xp in 8m53s) assembled 8 guardians to 8 barrier placements. Only while
    # the reader can see the guardian count and it is under the limit (10).
    if ("create_guardian" not in disabled and not in_pocket
            and (inv.get("chargedCellTier") or "NONE") != "NONE"
            and inv.get("chiselPresent", True)
            and 0 <= st.get("activeGuardians", -1) < st.get("guardianLimit", -1)):
        return "create_guardian"
    if ("place_cell" not in disabled and not in_pocket
            and (inv.get("chargedCellTier") or "NONE") != "NONE"):
        return "place_cell"
    # Imbuing only charges a CARRIED uncharged cell (USER 2026-09-01: "gotta grab
    # cells to get them charged") -- restock before heading for an altar empty-handed.
    if ("restock_cells" not in disabled and not in_pocket
            and inv.get("unchargedCells", 0) == 0 and inv.get("emptySlots", 0) > 0):
        return "restock_cells"
    # USER 2026-09-01 rotation: mine the batch ONCE per game, then cycle
    # "craft all the essence, fill the bags, then altar" -- an altar trip goes out
    # with FULL bags (or when there is nothing left to craft), and the mine rung is
    # dead for the rest of the game once the batch has been seen (`batch_done`).
    essence = inv.get("guardianEssence", 0)
    frags = inv.get("guardianFragments", 0)
    # The trip goes out MAXIMALLY loaded (USER 2026-09-01: "filled up bag but didn't
    # resume crafting until inventory full"): bags full alone is not enough -- keep
    # crafting until the inventory is full too, or the fragments run out.
    if "altar_trip" not in disabled and essence > 0 and (
            frags == 0
            or (pouch_space == 0 and inv.get("emptySlots", 0) == 0)
            or (pouch_space is None and essence >= cfg["altar_at_essence"])):
        return "altar_trip"
    # The mine->craft switch is TIMED to the altar rotation as well as the batch
    # target (USER 2026-09-01: "0:30 until ALTAR spawns, not game"): with ~30s to
    # the next pair opening, run to the bench so a full load is ready the moment
    # it appears -- but only once a batch WORTH crafting exists (live 2026-09-01:
    # the round-start window yanked it off the remains at ~40 frags; USER: "why
    # did we just climb out of mining?").
    rotation = st.get("altarRotationSecondsRemaining", -1)
    if (frags >= cfg["craft_at_fragments"]
            or (frags > 0 and (
                crafting or batch_done
                or (frags >= ALTAR_PREP_MIN_FRAGS and 0 <= rotation <= ALTAR_PREP_S)))
            # A FULL inventory with essence and bag space always goes to the craft
            # handler -- its fill logic empties into the bags. Without this, a
            # restart (latch lost) with frags under the thresholds fell through to
            # MINE on a full inventory (USER 2026-09-01: "we went to mine but we
            # had full inventory and some shards left?? makes no sense").
            or (inv.get("emptySlots", 0) == 0 and essence > 0
                and (pouch_space or 0) > 0)):
        return "craft"
    if batch_done:
        return "idle"
    return "mine"


# ---------------------------------------------------------------------------
# Runtime constants
# ---------------------------------------------------------------------------

# LIVE-VERIFY block: every name/action below is a research-derived first draft (no
# recording exists). The spec's Live-verification checklist tracks these. Names are
# lowercase because _find_object_by_name lowercases; actions keep in-game casing.
GREAT_GUARDIAN_NAME = "great guardian"          # NPC, substring match
POWER_UP_ACTION = "Power-up"
WORKBENCH_NAME = "workbench"                    # exact
WORKBENCH_ACTION = "Work-at"
DEPOSIT_POOL_NAME = "deposit pool"              # exact
DEPOSIT_ACTION = "Deposit-runes"
UNCHARGED_CELLS_NAME = "uncharged cells"        # exact -- table near the entry barrier
TAKE_ACTION = "Take"
ENTRY_BARRIER_NAME = "barrier"                  # exact
ENTRY_BARRIER_ACTIONS = ("Pass", "Quick-pass")
ENTRY_BARRIER_OPEN_ID = 43700                   # gotr_barrier -- passable
ENTRY_BARRIER_IDS = (43700, 43849, 53263)       # + gotr_barrier_closed, gotr_barrier_noentry
PORTAL_NAME = "portal"                          # exact -- altar exit portals
HUGE_PORTAL_ID = 43729                          # the huge-remains portal (human recording
                                                # "Enter -> object Portal (43729)"); by ID
                                                # because the temple has OTHER Portals with
                                                # Enter -- 43692 at (3615,9469) in the lobby
                                                # was the nearest one in run 49 and the trip
                                                # walked at it for 15 ticks
HUGE_POCKET_EXIT_ID = 38044                     # the pocket's exit portal (same recording)
ALTAR_NAME = "altar"                            # exact -- inside the altar room
ALTAR_IMBUE_ACTION = "Craft-rune"
EXIT_PORTAL_ACTIONS = ("Use", "Enter", "Exit")
GUARDIAN_REMAINS_NAMES = ("guardian remains", "guardian parts")   # exact, both
LARGE_REMAINS_NAME = "large guardian remains"   # exact -- behind the 56 Agility rubble
HUGE_REMAINS_NAME = "huge guardian remains"     # exact -- inside the portal pocket
RUBBLE_NAME = "rubble"                          # exact
RUBBLE_ACTION = "Climb"
POCKET_MIN_X = 3636         # east large-remains pocket: rubble line measured at
                            # (3634,9503) arena-side / (3636,9503) pocket-side
GREAT_GUARDIAN_TILE = (3615, 9503)   # its platform, centre of the barrier ring (measured)
MINE_ACTION = "Mine"
CELL_TILE_PLACE_OPTIONS = ("Place-cell", "Recharge", "Strengthen")
ESSENCE_PILE_ELEMENTAL_ID = 43722    # "Essence pile (elemental)", Assemble -- human recording
ESSENCE_PILE_CATALYTIC_ID = 43723    # "Essence pile (catalytic)"
ASSEMBLE_ACTION = "Assemble"
CELL_TILE_REPAIR_OPTIONS = ("Repair",)
PORTAL_GUARDIAN_PREFIX = "guardian of "         # "Guardian of Air" etc., + "Enter"
PORTAL_GUARDIAN_ACTION = "Enter"

# NPC Contact: spell widget proven live by mahogany_homes; the Dark Mage row in the
# contact interface (group 75) is LIVE-VERIFY -- _find_dark_mage_widget scans children.
MAGIC_TAB_KEY = "F6"
NPC_CONTACT_GROUP = 218
NPC_CONTACT_CHILD = 113
DARK_MAGE_CONTACT_GROUP = 75
DARK_MAGE_SCAN_CHILDREN = 40

POUCH_NAMES = ("colossal pouch", "giant pouch", "large pouch", "medium pouch",
               "small pouch")

# Ceilings (spec table). Counters reset where success is DETECTED (_reset_counters),
# never where the attempt is made -- the mahogany_homes contact-cast lesson.
STATE_UNAVAILABLE_LIMIT = 20
OUTSIDE_STREAK_LIMIT = 8       # consecutive "outside" reads before stopping (~6s)
ENTER_MAX_CLICKS = 6
ENTER_RETRY_S = 5.0            # min gap between Pass attempts (a queued pass takes a beat)
CONTACT_MAX_CASTS = 4          # per between-round window; give-up, not a stop
CONTACT_STUCK_LIMIT = 8
CONTACT_RESOLVE_S = 15.0       # scan for the Dark Mage row this long after a cast
ALTAR_ENTER_MAX = 5
IMBUE_MAX_CLICKS = 8
IMBUE_CONFIRM_S = 4.0
POWERUP_MAX_CLICKS = 6
CELL_MAX_CLICKS = 6
CELL_TILE_TRIES = 2            # clicks on ONE tile before trying another (see place_cell_phase)
RESTOCK_MAX_CLICKS = 6
DEPOSIT_MAX_CLICKS = 6
DEPOSIT_COMMIT_S = 2.5         # a verified Deposit-runes click, plus the walk (see _walk_budget)
GUARDIAN_MISSING_LIMIT = 15
MINE_STALL_TICKS = 25
MINE_RECLICK_S = 5.0           # leave an in-progress mine alone this long after a delta
MINE_COMMIT_S = 6.0            # trust one verified Mine click through walk + wind-up
CRAFT_COMMIT_S = 6.0           # same for a verified Work-at click
ENTER_COMMIT_S = 5.0           # same for a verified portal/guardian Enter click
CLIMB_COMMIT_S = 4.0           # same for a verified rubble Climb click
POWERUP_COMMIT_S = 6.0         # one Power-up turns in ALL stones -- walk + turn-in time
RUBBLE_CLICK_RANGE = 8         # walk toward the rubble beyond this; click only close
ALTAR_CLICK_RANGE = 8          # same inside an altar room: the Cosmic altar stands 20
                               # tiles from its entry portal (MEASURED run 42, player
                               # (2122,4833) vs altar (2142,4833)) and at that range the
                               # projected point is off the model -- hover never matched,
                               # menu ['Cancel'] only, 6 wasted clicks toward the ceiling
ACTION_COMMIT_S = 4.0          # generic commit for _approach_or_click sites
ALTAR_PREP_S = 30              # mine->craft switch this many seconds before the next
                               # altar pair opens (USER: "0:30 until ALTAR spawns")
ALTAR_PREP_MIN_FRAGS = 80      # ...but only with a batch worth benching; keep mining
                               # through an altar window on a small pile
WORKBENCH_STALL_TICKS = 20
CRAFT_RECLICK_S = 4.0
CRAFT_STICKY_S = 8.0           # a craft with a delta this recent holds the craft rung
PORTAL_TRIP_MAX_TICKS = 60     # forces an exit from the pocket, not a stop
PORTAL_RUN_S_PER_TILE = 0.35   # running ~2 tiles/tick; the portal gate budgets the run
PORTAL_ARRIVAL_MARGIN_S = 2.0  # ...plus the click/teleport itself
PORTAL_SKIP_BLOCK_S = 15.0     # a skipped spawn stays skipped (it closes in <25s anyway)
ACTIVE_SETTLE_S = 5.0          # between-round ACTIONS wait this long after the last
                               # active read -- a portal/altar teleport hides the HUD
                               # for 2+ ticks (power -1 -> "idle"), and the 2-tick
                               # streak alone let run 37 exit the huge-remains pocket
                               # 3s after entering it (run 35 did the same, unlogged)
ACTION_BLOCK_S = 30.0          # how long a learned-unworkable rung stays disabled

# --- event-driven outcomes (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md)
# Varbits the bridge pushes as VARBIT_CHANGED for this routine (VarbitID, runelite-api 1.12.38):
VB_IS_PLAYING = 13691
VB_FINISHED_GAME = 13688
VB_CATALYTIC_EARNED = 13685
VB_ELEMENTAL_EARNED = 13686
VB_PORTAL_VISIBLE = 13687
VB_MINING_COUNT = 5667
VARBIT_SUBSCRIPTIONS = (VB_IS_PLAYING, VB_FINISHED_GAME, VB_CATALYTIC_EARNED,
                        VB_ELEMENTAL_EARNED, VB_PORTAL_VISIBLE, VB_MINING_COUNT)
CHAIN_MAX_ACTIONS = 4          # a tick chains straight into the next rung after an outcome lands,
CHAIN_MAX_S = 15.0             # bounded so the engine's safety/focus/break gates stay responsive
ACTION_AFTER_STOP_S = 2.5      # once PLAYER_STOPPED lands at the target, the action itself gets this
MINE_AFTER_STOP_S = 4.0        # long (mining wind-up is 2-3 ticks before the first fragment)
CRAFT_AFTER_STOP_S = 3.0
WALK_START_BUDGET_S = 0.8      # after a walk click: wait for PLAYER_MOVING (or /player.moving) this long
PREROUND_MINE_BUDGET_S = 2.5   # a pre-round Mine attempt is REFUSED by the game -- short wait, re-try


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def read_state():
    """One fresh GOTR snapshot, or None for 'cannot tell'. None must defer, never act."""
    st = controller.get_api("minigames", "guardians-of-the-rift")
    if not st or st.get("error"):
        return None
    return st


_pouch_cache = {"at": 0.0, "api": None}


def read_pouches():
    """The pouch reader walks widget trees ON THE CLIENT THREAD, and one tick can
    ask for it 3-4 times (ladder + fill + empty + repair check) -- a 0.4s TTL cache
    keeps that to ~1 real read per tick without staling across ticks."""
    now = time.time()
    if now - _pouch_cache["at"] < 0.4:
        return _pouch_cache["api"]
    api = controller.get_api("storage", "essence-pouches")
    if not api or api.get("error"):
        api = None
    _pouch_cache["at"] = now
    _pouch_cache["api"] = api
    return api


_rc_cache = {"level": None, "at": 0.0}


def _rc_level():
    """Live Runecraft level via the m8aq skills reader, cached 10 minutes. None =
    cannot read (gates nothing in choose_altar). The reader's exact shape is
    LIVE-VERIFY, so extraction is defensive/recursive."""
    if _rc_cache["level"] is not None and time.time() - _rc_cache["at"] < 600:
        return _rc_cache["level"]
    api = controller.get_api("skills", "skills")
    if not api or api.get("error"):
        return None
    level = _find_level(api, "runecraft")
    if level is not None:
        _rc_cache["level"] = level
        _rc_cache["at"] = time.time()
    return level


def _find_level(node, skill):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and skill in k.lower():
                if isinstance(v, int):
                    return v
                if isinstance(v, dict):
                    for key in ("level", "baseLevel", "realLevel", "staticLevel"):
                        if isinstance(v.get(key), int):
                            return v[key]
        label = ""
        for key in ("skill", "name", "type"):
            if isinstance(node.get(key), str):
                label = node[key].lower()
                break
        if skill in label:
            for key in ("level", "baseLevel", "realLevel", "staticLevel"):
                if isinstance(node.get(key), int):
                    return node[key]
        for v in node.values():
            found = _find_level(v, skill)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_level(v, skill)
            if found is not None:
                return found
    return None


def _find_object_by_name(names, action=None, radius=40, exact=False):
    """Nearest object whose name matches (exact or substring, case-insensitive),
    optionally requiring an action substring. None when nothing matches."""
    if isinstance(names, str):
        names = (names,)
    names = tuple(n.lower() for n in names)
    best = None
    # interactable=True: every object this routine hunts offers a menu action, and the
    # server-side filter is what keeps the per-tick scan off the client's frame budget.
    for obj in controller.get_objects(radius=radius, interactable=True):
        name = (obj.get("name") or "").lower()
        if exact:
            if name not in names:
                continue
        elif not any(n in name for n in names):
            continue
        if action is not None:
            actions = [a for a in (obj.get("actions") or []) if a]
            if not any(action.lower() in a.lower() for a in actions):
                continue
        d = obj.get("distance", 999)
        if best is None or d < best.get("distance", 999):
            best = obj
    return best


def _find_object_by_id(obj_id, radius=60):
    """Nearest object with this id, or None."""
    best = None
    for obj in controller.get_objects(radius=radius, interactable=True):
        if obj.get("id") != obj_id:
            continue
        d = obj.get("distance", 999)
        if best is None or d < best.get("distance", 999):
            best = obj
    return best


def _object_at_tile(x, y, option=None, radius=40):
    """The object standing on one world tile (optionally offering `option`) -- how
    barrier cell tiles from the reader's world-point list become clickable entities."""
    for obj in controller.get_objects(radius=radius):
        if obj.get("x") != x or obj.get("y") != y:
            continue
        if option is None:
            return obj
        actions = [a.lower() for a in (obj.get("actions") or []) if a]
        if any(option.lower() in a for a in actions):
            return obj
    return None


def _find_npc_by_name(sub):
    sub = sub.lower()
    best = None
    for npc in controller.get_npcs():
        if sub in (npc.get("name") or "").lower():
            d = npc.get("distance", 999)
            if best is None or d < best.get("distance", 999):
                best = npc
    return best


def _refresh_for(entity):
    idx = entity.get("index")
    if idx is not None:
        return controller.npc_index_refresh_target(idx)
    return controller.object_refresh_target(
        entity.get("x"), entity.get("y"), entity.get("plane", 0), entity.get("id"))


def _reresolve(entity):
    idx = entity.get("index")
    if idx is not None:
        return controller.get_npc_by_index(idx) or entity
    if entity.get("id") is not None:
        return controller.get_object_at(entity.get("x"), entity.get("y"),
                                        entity.get("plane", 0),
                                        entity.get("id")) or entity
    return entity


def _click_escalating(ctx, entity, options):
    """Aimed click with hover verification; a withheld/failed click re-resolves the
    entity and picks the option by name off the right-click menu -- the golem-plinth
    tier that beats players camped on the pixel. Mass worlds make this the default."""
    if isinstance(options, str):
        options = (options,)
    if ctx.events is not None:
        # The mark BEFORE the click: only events after it can satisfy the outcome wait.
        ctx.state.data["gotr_click_mark"] = ctx.events.mark()
    res = controller.click_entity(ctx.ser, entity, refresh_target=_refresh_for(entity),
                                  expected_action=options)
    if res and not getattr(res, "withheld", False):
        return True
    fresh = _reresolve(entity)
    for option in options:
        # State-dependent verbs (Place-cell vs Recharge vs Strengthen): the menu only
        # offers the applicable one, and click_menu_action returns False on a miss, so
        # trying in order finds it without knowing the state.
        if controller.click_menu_action(ctx.ser, fresh, option, offset=(0, 0),
                                        refresh_target=_refresh_for(fresh)):
            return True
    # Both tiers failed -- usually an aim point outside the window (camera facing
    # away after a teleport or relogin). The recovery lives HERE, once, because every
    # site that lacked it eventually hit its ceiling (Power-up run 48, exits run 56,
    # the cell tile run 58): turn the camera toward it and walk closer.
    _bring_into_view(ctx, fresh)
    return False


def _inv_now():
    """Fresh inventory counts from the GOTR reader (one HTTP read)."""
    return (read_state() or {}).get("inventory") or {}


def _energy_varbit(payload):
    return payload.get("varbitId") in (VB_ELEMENTAL_EARNED, VB_CATALYTIC_EARNED)


def _after_stop(seconds):
    """shorten= for _await: the walk is over when PLAYER_STOPPED lands; the action gets `seconds`."""
    return {"PLAYER_STOPPED": seconds}


# Outcome confirms -- one fresh HTTP read each, compared against the pre-click snapshot.
# Cell barriers are GroundObjects (m8aq readBarriers), NOT varbits, so a cell/repair outcome is
# the cell or the fragments leaving the INVENTORY; the energy varbits ride along as a trigger.
def _confirm_cell_gone():
    return (_inv_now().get("chargedCellTier") or "NONE") == "NONE"


def _confirm_repaired(frags_before):
    st2 = read_state() or {}
    inv2 = st2.get("inventory") or {}
    return (inv2.get("guardianFragments", 0) < frags_before
            or bool(st2.get("barriers") and find_repairable(st2["barriers"]) is None))


def _confirm_fewer_stones(stones_before):
    return total_stones(_inv_now()) < stones_before


def _confirm_more_cells(cells_before):
    return _inv_now().get("unchargedCells", 0) > cells_before


CELL_SIGNALS = {"INVENTORY_CHANGED": None, "VARBIT_CHANGED": _energy_varbit}


def _await(ctx, key, budget_s, signals, confirm, reaction=True, shorten=None):
    """The replacement for _commit's fixed window: block until the click's OUTCOME -- a listed
    UDP signal confirmed by one HTTP read -- or `budget_s`, whichever first (at the budget the
    HTTP read decides alone, exactly the old polling). A hit sets gotr_outcome_hit so tick()
    chains into the next rung instead of handing back to the engine loop. `key` keeps the
    _committed() contract for any re-entrant check while the wait is in progress.
    2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md."""
    data = ctx.state.data
    data[key] = time.time() + budget_s
    res = bridge_events.await_outcome(ctx.events, signals, budget_s, confirm=confirm,
                                      since=data.pop("gotr_click_mark", None),
                                      reaction=reaction, shorten=shorten)
    data[key] = 0.0
    if res:
        data["gotr_outcome_hit"] = True
        debug_log.log("gotr", f"{key[5:]}: outcome via {res.signal or 'http at budget'} "
                              f"after {res.latency_s:.2f}s")
    else:
        debug_log.log("gotr", f"{key[5:]}: no outcome within {budget_s:.1f}s")
    return res


def _committed(data, key):
    """True while a recent VERIFIED click's commit window is open -- the click
    resolved to the right action (hover-checked), so re-clicking buys nothing
    (USER 2026-09-01: "if our click resolves correctly do we really need to keep
    reclicking?"). Deltas/arrival re-gate as before once the window closes."""
    return time.time() < data.get(key, 0)


def _commit(data, key, seconds):
    data[key] = time.time() + seconds


ENTER_ECHO_TIMEOUT_S = 1.5


def _enter_resolved(before, expected_option, label):
    """Poll /last-action briefly after an Enter click for the game's own verdict on
    what the click became. True when a NEW action with `expected_option` registered;
    False (logged with the actual resolution) when it became something else; None on
    timeout -- treated as "cannot tell", so the commit stands as before. Added after
    run 61: two guardian Enters landed as clean clicks, never teleported, and the same
    guardian stayed active -- the log could not say what the game did with them."""
    deadline = time.time() + ENTER_ECHO_TIMEOUT_S
    while time.time() < deadline:
        after = controller.get_last_action() or {}
        if after and not (before and after.get("tick") == before.get("tick")
                          and after.get("option") == before.get("option")
                          and after.get("target") == before.get("target")):
            option = (after.get("option") or "").lower()
            if expected_option.lower() in option:
                return True
            debug_log.log("!", f"gotr: {label} click resolved to "
                               f"{after.get('option')!r} on {after.get('target')!r}")
            return False
        time.sleep(0.15)
    return None


def _walk_budget(entity):
    """Seconds the RUN to `entity` takes -- every commit window adds this, because the
    game performs a queued click on ARRIVAL and a flat window expired while the player
    was still walking. MEASURED run 52: the Deposit Pool was re-clicked within 8s on 21 of
    41 clicks and the huge remains on 17 of 37 -- the cursor re-flying and re-clicking a
    target we were already walking to, once a tick (USER: "we seem to be missing a lot of
    clicks while moving")."""
    p = controller.get_player() or {}
    if None in (p.get("x"), p.get("y"), entity.get("x"), entity.get("y")):
        return 0.0
    tiles = max(abs(p["x"] - entity["x"]), abs(p["y"] - entity["y"]))
    return tiles * PORTAL_RUN_S_PER_TILE


def _hold_action(data, action, seconds):
    """Pin the ladder to `action` for `seconds` -- a verified Enter click is walking
    us to a portal, and the next tick's snapshot must not re-rank it away (run 38:
    portal_trip clicked with 10s left, the timer read 7s next tick, place_cell won
    the ladder and its click cancelled the walk)."""
    data["gotr_hold_action"] = action
    data["gotr_hold_until"] = time.time() + seconds


def _held_action(data, disabled, st=None):
    hold = data.get("gotr_hold_action")
    if not hold or time.time() >= data.get("gotr_hold_until", 0) or hold in disabled:
        return None
    inv = (st or {}).get("inventory") or {}
    # A hold is dropped the moment its precondition is gone -- the cell was placed,
    # the stones turned in -- so it never pins the ladder to a rung with no work.
    if hold in ("place_cell", "create_guardian") and (inv.get("chargedCellTier") or "NONE") == "NONE":
        return None
    if hold == "repair_barrier" and find_repairable((st or {}).get("barriers")) is None:
        return None
    if hold == "spend_stones" and total_stones(inv) == 0:
        return None
    return hold


def _craft_in_progress(data, inv):
    """True while the bench is visibly producing (recent essence/fragment delta,
    stamped by _reset_counters) and fragments remain to feed it."""
    return (inv.get("guardianFragments", 0) > 0
            and time.time() - data.get("gotr_craft_delta_at", 0) < CRAFT_STICKY_S)


def _climb_rubble(ctx):
    """Click the rubble shortcut (queued: the game walks to it and climbs on arrival --
    the human recording clicks Climb from across the arena and it lands fine). True
    when a rubble was clicked or approached."""
    data = ctx.state.data
    if _committed(data, "gotr_climb_commit"):
        return True    # a verified Climb is already queued -- let it play out
    rubble = _find_object_by_name(RUBBLE_NAME, action=RUBBLE_ACTION, exact=True)
    if rubble is None:
        return False
    # WALK FIRST, CLICK CLOSE (live 2026-09-01, run 26: a long-range Climb from a
    # bad camera angle projected behind the side panel and produced a ~40s storm
    # of withheld clicks and ESCs -- USER: "this is a mess"). At range the
    # projection is unreliable; within a few tiles it is clean and central.
    p = controller.get_player() or {}
    if p.get("x") is not None:
        dist = max(abs(p["x"] - rubble.get("x", 0)), abs(p["y"] - rubble.get("y", 0)))
        if dist > RUBBLE_CLICK_RANGE:
            debug_log.log("gotr", "heading for the rubble shortcut")
            _direct_walk(ctx, rubble.get("x"), rubble.get("y"))
            controller.human_delay(0.5, 0.9)
            return True
    debug_log.log("gotr", "climbing the rubble shortcut")
    side_before = (p.get("x") or 0) >= POCKET_MIN_X
    if _click_escalating(ctx, rubble, (RUBBLE_ACTION,)):
        # Budget the walk to the rubble too (the game climbs on arrival): a flat
        # window re-clicked the rubble mid-approach, re-queueing the same climb. The
        # outcome is the player standing on the OTHER side of the rubble line.
        tiles = 0
        if p.get("x") is not None:
            tiles = max(abs(p["x"] - rubble.get("x", 0)), abs(p["y"] - rubble.get("y", 0)))
        _await(ctx, "gotr_climb_commit", CLIMB_COMMIT_S + tiles * PORTAL_RUN_S_PER_TILE,
               {"PLAYER_STOPPED": None, "ANIMATION_CHANGED": None},
               lambda: (((controller.get_player() or {}).get("x") or 0) >= POCKET_MIN_X) != side_before)
    else:
        # Projection off the window (camera facing away) skips BOTH click tiers
        # silently -- live 2026-09-01 run 11 looped on this every tick with zero
        # progress. Walk toward the rubble instead; the pull-in fractions always
        # find an on-screen tile, and the next tick's click is aimable.
        debug_log.log("gotr", "rubble is off-screen -- walking toward it")
        return _direct_walk(ctx, rubble.get("x"), rubble.get("y"))
    controller.mark_busy(3.0)
    if not data.get("gotr_outcome_hit"):
        controller.human_delay(0.7, 1.2)
    return True


def _bring_into_view(ctx, entity):
    """Both click tiers were skipped because the object projects OFF THE WINDOW
    (camera facing away). MEASURED run 41 at the round end: twelve 'aim point is
    outside the RuneLite window' skips on the Uncharged cells table spent the
    restock budget and prep started with 4 cells. Turn the camera toward it and
    walk closer -- the next tick's click is aimable."""
    sx = entity.get("screenX")
    if sx is not None:
        controller.rotate_camera_to_reveal(ctx.ser, sx)
    _walk_toward(ctx, entity)


_walk_memo = {"target": None, "player": None, "stuck": 0}
WALK_STUCK_CLICKS = 2      # same target, player not moved since the last click, twice


def _direct_walk(ctx, x, y):
    """One direct viewport shift-click toward (x, y), pulled in toward the player until
    the tile projects on screen. The static collision map has NO DATA inside the arena
    (MEASURED 2026-09-01: reachable_from(3618,9483) == 1 tile), so travel.walk_to can
    never route mid-arena walks -- but the ground there is open and flat, so a plain
    force-walk click is all that is needed.

    A click that provably did nothing (same target, player on the same tile as at the
    previous click) is counted, and past WALK_STUCK_CLICKS the viewport is skipped for
    the minimap hop: run 46 clicked the chatbox scrollbar 30 times in a row toward the
    Cosmic altar because that overlay was not a registered blocker yet (USER: "chat ui
    scrollbar softlocked us in altar"). Blocker lists will never be complete; the
    player not moving is the ground truth."""
    p = controller.get_player() or {}
    px, py = p.get("x"), p.get("y")
    if px is None or x is None:
        return False
    if _walk_memo["target"] == (x, y) and _walk_memo["player"] == (px, py):
        _walk_memo["stuck"] += 1
    else:
        _walk_memo["stuck"] = 0
    _walk_memo["target"] = (x, y)
    _walk_memo["player"] = (px, py)
    if _walk_memo["stuck"] >= WALK_STUCK_CLICKS:
        mm = controller.get_minimap_walk_point(x, y, 0)
        if mm.get("onMinimap") and mm.get("screenX") is not None:
            debug_log.log("gotr", f"walk toward ({x},{y}) is not moving us -- minimap hop")
            controller.click_entity(ctx.ser, {"name": "minimap", "screenX": mm["screenX"],
                                              "screenY": mm["screenY"]},
                                    offset=(0, 0), check_ui_blockers=False)
            return True
        debug_log.log("gotr", f"walk toward ({x},{y}) is not moving us -- turning the camera")
        controller.rotate_camera_to_reveal(ctx.ser, 0)
    for frac in (1.0, 0.6, 0.35):
        tx = int(round(px + (x - px) * frac))
        ty = int(round(py + (y - py) * frac))
        if (tx, ty) == (px, py):
            continue
        point = controller.get_ground_walk_point(tx, ty, 0)
        if not point.get("onScreen"):
            continue
        if controller.is_point_blocked_by_ui(point.get("screenX", -1),
                                             point.get("screenY", -1)) is not None:
            # Projected under the minimap / inventory panel / chat strip: the click
            # would be swallowed by the overlay, not walk anywhere. MEASURED live
            # 2026-09-02 (run 39): the same pixel inside the minimap circle clicked
            # once a tick for two minutes, player never moved. Pull in instead.
            continue
        if travel.walk_to_exact_tile(ctx, tx, ty, 0, wait_for_arrival=False):
            return True
    # Camera facing away: every viewport candidate projects off-screen. The MINIMAP
    # is camera-proof -- live 2026-09-01 run 33, this exact silent per-tick failure
    # walked nowhere and spent the craft stall ceiling in 24 ticks.
    mm = controller.get_minimap_walk_point(x, y, 0)
    if mm.get("onMinimap") and mm.get("screenX") is not None:
        debug_log.log("gotr", f"viewport blind toward ({x},{y}) -- minimap hop")
        controller.click_entity(ctx.ser, {"name": "minimap", "screenX": mm["screenX"],
                                          "screenY": mm["screenY"]},
                                offset=(0, 0), check_ui_blockers=False)
        return True
    return False


def _walk_toward(ctx, entity):
    """Direct viewport clicks inside the temple; travel.walk_to only elsewhere.

    NEVER map-route inside the temple: the static map's arena data is stale garbage
    -- mostly absent ("no walking route"), but where a route DOES resolve it fights
    live collision hop by hop. MEASURED run 8 tick 9 (2026-09-01): eleven zigzag hop
    clicks incl. a minimap hop and two live re-routes, crossing the arena after a
    rubble climb -- USER: "really strange movement after we climb the rubble". The
    arena is open ground; one force-walk click is the human move. When the walk
    would CROSS the rubble line, climb the rubble instead -- an agility obstacle
    neither the map nor the game's own click-to-walk can path across. (An earlier
    draft climbed on ANY failed walk: that sent a mid-arena walk INTO the pocket,
    stranding spend_stones outside the ~15-tile NPC streaming radius.)"""
    p = controller.get_player() or {}
    px, x = p.get("x"), entity.get("x")
    if p.get("regionId") == 14484:
        if (ctx.state.config.get("use_large_remains") and px is not None
                and x is not None and (px >= POCKET_MIN_X) != (x >= POCKET_MIN_X)):
            return _climb_rubble(ctx)
        return _direct_walk(ctx, x, entity.get("y"))
    if travel.walk_to(ctx, entity):
        return True
    if px is None or x is None:
        return False
    return _direct_walk(ctx, x, entity.get("y"))


def _arena_direct_click_ok(entity):
    """True when `entity` can simply be clicked from here and the game will path to it:
    both of us on the arena side of the rubble (region 14484), within
    ARENA_DIRECT_CLICK_TILES. The human benchmark clicks the bench, pool and tiles from
    across the arena; our reach check walked first (a ~5-10s detour per cycle: live
    collision only covers 14 tiles, so anything farther read as unreachable)."""
    p = controller.get_player() or {}
    if p.get("regionId") != 14484 or None in (p.get("x"), p.get("y"), entity.get("x"), entity.get("y")):
        return False
    if (p["x"] >= POCKET_MIN_X) != (entity["x"] >= POCKET_MIN_X):
        return False
    return max(abs(p["x"] - entity["x"]), abs(p["y"] - entity["y"])) <= ARENA_DIRECT_CLICK_TILES


def _approach_or_click(ctx, entity, options, counter_key, max_clicks, stop_reason,
                       signals=None, confirm=None, budget_s=None,
                       after_stop_s=ACTION_AFTER_STOP_S):
    """The standard gated click: ceiling -> can_reach -> walk when unreachable ->
    escalating click -> WAIT FOR THE OUTCOME (signals + confirm; see _await). Without an
    outcome spec the old fixed commit window is kept. A routeless walk ALSO spends the
    counter (every loop needs a ceiling). True only when a click was issued."""
    data = ctx.state.data
    if _committed(data, counter_key + "_commit"):
        return False
    if data.get(counter_key, 0) >= max_clicks:
        ctx.state.trigger_safety_stop(f"gotr: {stop_reason}")
        return False
    reach = travel.can_reach(entity)
    if reach is False and not _arena_direct_click_ok(entity):
        if not _walk_toward(ctx, entity):
            data[counter_key] = data.get(counter_key, 0) + 1
        return False
    data[counter_key] = data.get(counter_key, 0) + 1
    budget = (budget_s if budget_s is not None else ACTION_COMMIT_S) + _walk_budget(entity)
    if _click_escalating(ctx, entity, options):
        if signals is None and confirm is None:
            _commit(data, counter_key + "_commit", budget)
        else:
            _await(ctx, counter_key + "_commit", budget, signals or "INVENTORY_CHANGED",
                   confirm, shorten=_after_stop(after_stop_s))
    return True


def _dialogue():
    d = controller.get_api("ui", "dialogue")
    if not d or d.get("error"):
        return {}
    return d


def _dialogue_open(d):
    # Mirrors giants_foundry: the m8aq Dialogue reader has no open/isOpen field -- any
    # populated npcText/playerText/itemText/options stage counts as open.
    if not d:
        return False
    for key in ("open", "isOpen", "visible"):
        if isinstance(d.get(key), bool):
            return d[key]
    dtype = d.get("type")
    if dtype and dtype != "NONE":
        return True
    return bool(d.get("npcText") or d.get("playerText") or d.get("itemText")
                or d.get("options"))


def _dialogue_options(d):
    out = []
    for o in d.get("options") or []:
        out.append(o if isinstance(o, str) else (o.get("text") or ""))
    return out


def _choose_option(ctx, d, substrings):
    """Press the number key of the first dialogue option matching any substring
    (case-insensitive). False when nothing matched."""
    for i, text in enumerate(_dialogue_options(d)):
        low = text.lower()
        if any(s in low for s in substrings):
            controller.key_tap(ctx.ser, str(i + 1))
            return True
    return False


def _runes_in_inventory():
    for i in controller.get_inventory() or []:
        if (i.get("name") or "").lower().endswith(" rune"):
            return True
    return False


def _pouch_items():
    out = []
    for i in controller.get_inventory() or []:
        name = (i.get("name") or "").lower()
        if any(p in name for p in POUCH_NAMES):
            out.append(i)
    return out


def _pouch_state_by_word(api):
    """{'small': {...}, 'medium': {...}, ...} from the pouch reader, or {}."""
    out = {}
    for key, v in ((api or {}).get("pouches") or {}).items():
        if isinstance(v, dict):
            out[key.lower()] = v
    return out


def _fill_pouches(ctx, resume=None):
    # Plain left click: USER 2026-09-01, the pouch's left-click default is
    # context-smart -- "Fill" while it has space, "Empty" once full. The flip side
    # (MEASURED live run 21, ticks 300-301): a DUPLICATE fill pass -- the reader
    # lags the clicks by a tick -- left-clicks FULL pouches, which EMPTIES them
    # straight back out. So each pass consults per-pouch state and skips pouches
    # already where they should be.
    # The inventory tab is NOT necessarily open: the pouch-repair cast presses F6
    # (magic tab) and nothing switched back, so run 49 clicked the spellbook 40
    # times in a row believing it was filling pouches. Confirmed on inventoryTabOpen.
    controller.open_inventory_tab(ctx.ser)
    states = _pouch_state_by_word(read_pouches())
    for pouch in _pouch_items():
        word = (pouch.get("name") or "").lower().split(" ")[0]
        ps = states.get(word)
        if ps is not None and ps.get("storedEssence", 0) >= ps.get("capacity", 0):
            continue    # full -- a click would EMPTY it
        controller.click_entity(ctx.ser, pouch, offset=(0, 0),
                                check_ui_blockers=False)
        controller.human_delay(0.15, 0.35)
    if resume is not None:
        # USER 2026-09-01: "after filling the last pouch instantly reclick the last
        # action" -- chain straight back into mining/crafting, no verification.
        obj = _find_object_by_name(resume[0], action=resume[1], exact=True)
        if obj is not None:
            controller.click_entity(ctx.ser, obj, offset=(0, 0),
                                    refresh_target=_refresh_for(obj))


def _empty_pouches(ctx):
    # Mirror image of _fill_pouches: a click on an already-EMPTY pouch would FILL
    # it from the loose essence just freed, shuffling essence forever at the altar.
    controller.open_inventory_tab(ctx.ser)
    states = _pouch_state_by_word(read_pouches())
    for pouch in _pouch_items():
        word = (pouch.get("name") or "").lower().split(" ")[0]
        ps = states.get(word)
        if ps is not None and ps.get("storedEssence", 0) <= 0:
            continue    # empty -- a click would FILL it back up
        controller.click_entity(ctx.ser, pouch, offset=(0, 0),
                                check_ui_blockers=False)
        controller.human_delay(0.15, 0.35)


def _await_xp(field, before, timeout_s):
    """Poll /player for a skill-XP increase -- the sub-tick 'did that work' signal.
    True on increase; False on timeout; True immediately when the field is absent
    (un-rebuilt client -- cannot confirm, so don't block)."""
    if before is None:
        controller.human_delay(1.0, 1.5)
        return True
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        now = (controller.get_player() or {}).get(field)
        if now is not None and now > before:
            return True
        time.sleep(0.3)
    return False


def _find_dark_mage_widget():
    """The Dark Mage row of the NPC Contact interface. Child id unknown (LIVE-VERIFY):
    scan the group's first DARK_MAGE_SCAN_CHILDREN children for the text."""
    for child in range(DARK_MAGE_SCAN_CHILDREN):
        w = controller.get_widget(DARK_MAGE_CONTACT_GROUP, child)
        if w and w.get("clickable") and "dark mage" in (w.get("text") or "").lower():
            return w
    return None


# ---------------------------------------------------------------------------
# Between rounds
# ---------------------------------------------------------------------------

def enter_arena_phase(ctx, st):
    """South of the entry barrier: pass it. Reset lives in _reset_counters (inArena)."""
    data = ctx.state.data
    now = time.time()

    # A round in progress LOCKS the barrier -- every Pass answers with a MESBOX
    # ("It looks like the rift is already being guarded. You must wait until they
    # are done to join.", MEASURED live 2026-09-01 after a world hop: six good
    # Quick-pass clicks, six refusals, safety stop). The lock is the BARRIER OBJECT'S
    # OWN ID (cache config/loc): 43700 gotr_barrier is passable, 43849
    # gotr_barrier_closed is the red locked one, 53263 gotr_barrier_noentry is the
    # 200-player cap before a start. The earlier heuristic -- any live cell barrier
    # in the ring means a round is running -- was wrong on a themed world, where
    # the ring stays charged between games: run 67 waited 47 minutes in front of a
    # passable 43700 (USER: "never enters?").
    barrier_obj = None
    for bid in ENTRY_BARRIER_IDS:
        barrier_obj = _find_object_by_id(bid, radius=20)
        if barrier_obj is not None:
            break
    if barrier_obj is not None and barrier_obj.get("id") != ENTRY_BARRIER_OPEN_ID:
        data["gotr_enter_clicks"] = 0
        if now - data.get("gotr_enter_wait_logged", 0) > 30:
            debug_log.log("gotr", f"barrier {barrier_obj.get('id')} is locked -- waiting "
                                  "to join the next game")
            data["gotr_enter_wait_logged"] = now
        controller.human_delay(2.0, 4.0)
        return

    if data.get("gotr_enter_clicks", 0) >= ENTER_MAX_CLICKS:
        ctx.state.trigger_safety_stop("gotr: could not pass the entry barrier")
        return
    if now - data.get("gotr_enter_click_at", 0) < ENTER_RETRY_S:
        controller.human_delay(0.6, 1.2)
        return
    data["gotr_enter_click_at"] = now
    if barrier_obj is None:
        for act in ENTRY_BARRIER_ACTIONS:
            barrier_obj = _find_object_by_name(ENTRY_BARRIER_NAME, action=act, exact=True)
            if barrier_obj is not None:
                break
    if barrier_obj is None:
        debug_log.log("gotr", "entry barrier not visible -- waiting")
        controller.human_delay(1.0, 2.0)
        return
    data["gotr_enter_clicks"] = data.get("gotr_enter_clicks", 0) + 1
    _click_escalating(ctx, barrier_obj, ENTRY_BARRIER_ACTIONS)
    controller.mark_busy(2.0)
    controller.human_delay(0.7, 1.2)


def between_rounds_phase(ctx, st):
    """waiting/complete/idle: repair pouches, restock cells, deposit strays, idle.
    One action per tick, priority-ordered."""
    data = ctx.state.data
    cfg = ctx.state.config
    inv = st.get("inventory") or {}

    if data.get("gotr_between_streak", 0) < 2:
        # One between-round read could be a transitional misread -- look again first.
        controller.human_delay(0.4, 0.8)
        return

    if st.get("miningArea") == "WEST_HUGE_REMAINS":
        # The round ended with us inside the huge-remains room (USER: "stuck in
        # portal room") -- prep-mining from in here targets remains THROUGH the
        # wall and clicks the same pixel forever. Leave first: its portal if one
        # stands, else walk out toward the arena centre.
        if not _committed(data, "gotr_exit_commit"):
            exit_portal = _find_object_by_name(PORTAL_NAME, radius=12, exact=True)
            if exit_portal is not None:
                if _click_escalating(ctx, exit_portal, EXIT_PORTAL_ACTIONS):
                    _commit(data, "gotr_exit_commit", ENTER_COMMIT_S + _walk_budget(exit_portal))
            else:
                _direct_walk(ctx, GREAT_GUARDIAN_TILE[0], GREAT_GUARDIAN_TILE[1])
        controller.human_delay(0.5, 0.9)
        return

    # NO bench work in prep -- USER 2026-09-01: "we try to craft in prep phase?
    # that doesn't work, we need shards to craft. All we do is collect uncharged
    # and go to mining spot and wait for game to start." (An earlier 0:30-to-start
    # bench branch lived here; the bench is inert between rounds, so it's gone.)
    now = time.time()

    # Once the pocket is taken, HOLD it (USER 2026-09-01: prep should wait for the
    # game start IN the mining area, not bounce out for chores) -- paced mine
    # attempts only; pre-start refusals ("There's no need to do that right now")
    # cost nothing and the first real swing lands the moment the rift opens.
    p = controller.get_player() or {}
    if cfg.get("use_large_remains") and (p.get("x") or 0) >= POCKET_MIN_X:
        if (now - data.get("gotr_mine_delta_at", 0) >= MINE_RECLICK_S
                and not _committed(data, "gotr_mine_commit")):
            node = _find_object_by_name(LARGE_REMAINS_NAME, action=MINE_ACTION,
                                        exact=True)
            if node is not None and travel.can_reach(node) is not False:
                if _click_escalating(ctx, node, (MINE_ACTION,)):
                    _commit(data, "gotr_mine_commit", MINE_COMMIT_S + _walk_budget(node))
        controller.human_delay(1.0, 2.0)
        return

    # Housekeeping -- only while still on the arena side of the rubble.
    if cfg.get("repair_pouches") and not data.get("gotr_repair_disabled"):
        pouches = read_pouches()
        if pouches is not None and not pouches_degraded(pouches):
            # Success detection for the repair loop -- reset lives HERE, where the
            # pouch reader proves it, not in the attempt handler.
            data["gotr_contact_casts"] = 0
            data["gotr_contact_stuck"] = 0
        elif (pouches_degraded(pouches)
                and data.get("gotr_contact_casts", 0) < CONTACT_MAX_CASTS):
            _repair_pouches(ctx)
            return

    if (inv.get("unchargedCells", 0) < cfg["min_uncharged_cells"]
            and data.get("gotr_restock_clicks", 0) < RESTOCK_MAX_CLICKS):
        if inv.get("emptySlots", 0) == 0 and inv.get("guardianEssence", 0) > 0:
            # A full inventory of LEFTOVER essence (wiped at the next round start
            # anyway) blocked the Take: run 55 left prep with 1 cell. Make a slot --
            # into the pouches if they have room, else drop one essence.
            if (pouch_free_space(read_pouches()) or 0) > 0:
                _fill_pouches(ctx)
            else:
                _drop_one(ctx, "guardian essence")
            return
        if inv.get("emptySlots", 0) > 0:
            _restock_cells(ctx, st)
            return

    if _runes_in_inventory():
        _deposit_runes(ctx)
        return
    data["gotr_deposit_clicks"] = 0

    mine_phase(ctx, st)


def _repair_pouches(ctx):
    """NPC Contact -> Dark Mage -> 'repair' dialogue option. CONTACT_MAX_CASTS is a
    give-up (degraded pouches still work at reduced capacity), never a safety stop."""
    data = ctx.state.data
    d = _dialogue()
    if _dialogue_open(d):
        if _dialogue_options(d):
            # USER 2026-09-01, twice: the repair choice is OPTION 2, literally.
            # A "repair" text match was tried first and picked option 3 (another
            # line containing the word), wrong branch, re-cast loop, F1. Press 2.
            controller.key_tap(ctx.ser, "2")
        else:
            # Spoken stages (the Dark Mage's lines) advance with SPACE.
            controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.5, 0.9)
        return

    if time.time() - data.get("gotr_contact_cast_at", 0) < CONTACT_RESOLVE_S:
        mage = _find_dark_mage_widget()
        if mage is not None:
            controller.click_entity(ctx.ser, mage, offset=(0, 0),
                                    check_ui_blockers=False)
            controller.human_delay(0.8, 1.3)
        return

    controller.key_tap(ctx.ser, MAGIC_TAB_KEY)
    controller.human_delay(0.3, 0.5)
    spell = controller.get_widget(NPC_CONTACT_GROUP, NPC_CONTACT_CHILD)
    if not spell or not spell.get("clickable"):
        stuck = data.get("gotr_contact_stuck", 0) + 1
        data["gotr_contact_stuck"] = stuck
        debug_log.log("gotr", f"NPC Contact widget not clickable ({stuck}x) -- "
                              "Lunar spellbook active?")
        if stuck >= CONTACT_STUCK_LIMIT:
            # Permanent for the session: a widget that is never clickable means the
            # spellbook is wrong, and re-probing it every round costs real actions.
            debug_log.log("gotr", "NPC Contact never clickable -- pouch repair "
                                  "disabled for this session")
            data["gotr_repair_disabled"] = True
        return
    data["gotr_contact_stuck"] = 0
    data["gotr_contact_casts"] = data.get("gotr_contact_casts", 0) + 1
    data["gotr_contact_cast_at"] = time.time()
    debug_log.log("gotr", "casting NPC Contact to repair pouches")
    controller.click_entity(ctx.ser, spell, offset=(0, 0), check_ui_blockers=False)
    controller.human_delay(0.8, 1.3)
    # F6 left the MAGIC tab up; every inventory click afterwards would hit the
    # spellbook (run 49). Back to the inventory as soon as the cast is away.
    controller.open_inventory_tab(ctx.ser)


def _restock_cells(ctx, st):
    data = ctx.state.data
    table = _find_object_by_name(UNCHARGED_CELLS_NAME, exact=True)
    if table is None:
        debug_log.log("gotr", "uncharged-cells table not visible")
        return
    reach = travel.can_reach(table)
    if reach is False and not _arena_direct_click_ok(table):
        if not _walk_toward(ctx, table):
            data["gotr_restock_clicks"] = data.get("gotr_restock_clicks", 0) + 1
        return
    if _committed(data, "gotr_restock_commit"):
        return    # a verified Take is still walking us to the table
    data["gotr_restock_clicks"] = data.get("gotr_restock_clicks", 0) + 1
    cells = (st.get("inventory") or {}).get("unchargedCells", 0)
    budget = ACTION_COMMIT_S + _walk_budget(table)
    if _click_escalating(ctx, table, (TAKE_ACTION,)):
        _await(ctx, "gotr_restock_commit", budget, "INVENTORY_CHANGED",
               lambda: _confirm_more_cells(cells), shorten=_after_stop(ACTION_AFTER_STOP_S))
    if not data.get("gotr_outcome_hit"):
        controller.human_delay(0.5, 0.9)


def _deposit_runes(ctx):
    data = ctx.state.data
    if _committed(data, "gotr_deposit_commit"):
        return    # a verified Deposit-runes click is still walking us there
    if data.get("gotr_deposit_clicks", 0) >= DEPOSIT_MAX_CLICKS:
        ctx.state.trigger_safety_stop("gotr: rune deposit never registered at the pool")
        return
    pool = _find_object_by_name(DEPOSIT_POOL_NAME, exact=True)
    if pool is None:
        data["gotr_deposit_clicks"] = data.get("gotr_deposit_clicks", 0) + 1
        debug_log.log("gotr", "deposit pool not visible")
        return
    reach = travel.can_reach(pool)
    if reach is False and not _arena_direct_click_ok(pool):
        if not _walk_toward(ctx, pool):
            data["gotr_deposit_clicks"] = data.get("gotr_deposit_clicks", 0) + 1
        return
    data["gotr_deposit_clicks"] = data.get("gotr_deposit_clicks", 0) + 1
    budget = DEPOSIT_COMMIT_S + _walk_budget(pool)
    if _click_escalating(ctx, pool, (DEPOSIT_ACTION,)):
        _await(ctx, "gotr_deposit_commit", budget, "INVENTORY_CHANGED",
               lambda: not _runes_in_inventory(), shorten=_after_stop(ACTION_AFTER_STOP_S))
    if not data.get("gotr_outcome_hit"):
        controller.human_delay(1.0, 1.6)


# ---------------------------------------------------------------------------
# In round
# ---------------------------------------------------------------------------

def repair_barrier_phase(ctx, st):
    """Repair a BROKEN_REPAIRABLE cell tile (12 fragments -> 25 energy both types)."""
    data = ctx.state.data
    tile = find_repairable(st.get("barriers") or [])
    if tile is None:
        return
    loc = tile.get("location") or {}
    obj = _object_at_tile(loc.get("x"), loc.get("y"))
    if obj is None:
        if not data.get("gotr_no_cell_objects"):
            debug_log.log("gotr", "cell tile not present in /objects -- disabling cell "
                                  "actions (GroundObject gap, see spec checklist)")
        data["gotr_no_cell_objects"] = True
        return
    frags = (st.get("inventory") or {}).get("guardianFragments", 0)
    if _approach_or_click(ctx, obj, CELL_TILE_REPAIR_OPTIONS, "gotr_repair_clicks",
                          CELL_MAX_CLICKS, "barrier repair never registered",
                          signals="INVENTORY_CHANGED", confirm=lambda: _confirm_repaired(frags)):
        _hold_action(data, "repair_barrier", ACTION_COMMIT_S)
        controller.mark_busy(2.0)
        if not data.get("gotr_outcome_hit"):
            controller.human_delay(0.5, 0.9)


def spend_stones_phase(ctx, st):
    """Use held guardian stones on the Great Guardian (2-3 energy each, 5 RC xp)."""
    data = ctx.state.data
    guardian = _find_npc_by_name(GREAT_GUARDIAN_NAME)
    if guardian is None:
        # NPCs are only STREAMED to the client within ~15 tiles, so from the pocket
        # or the temple edge the guardian legitimately is not in the scene (MEASURED
        # 2026-09-01: /npcs held 7 rows, none the guardian, from 22 tiles out; the
        # first draft counted 15 misses in 7s standing still and safety-stopped).
        # Walk toward its platform and look again; the ceiling still catches a
        # guardian that never appears.
        miss = data.get("gotr_guardian_missing", 0) + 1
        data["gotr_guardian_missing"] = miss
        debug_log.log("gotr", f"Great Guardian not visible ({miss}x) -- heading to "
                              "the arena centre")
        if miss >= GUARDIAN_MISSING_LIMIT:
            ctx.state.trigger_safety_stop("gotr: the Great Guardian is never visible")
            return
        _walk_toward(ctx, {"x": GREAT_GUARDIAN_TILE[0], "y": GREAT_GUARDIAN_TILE[1]})
        controller.human_delay(0.5, 1.0)
        return
    data["gotr_guardian_missing"] = 0
    # RIGHT-CLICK menu Power-up, no reachability gate -- USER 2026-09-01: "right
    # click guardian and power up, seems our click isn't going through". The
    # guardian's platform adjacency reads False from live collision forever (the
    # can_reach gate walked in a loop and never clicked), Power-up works from
    # range (the game walks you in and performs it on arrival), and on a mass
    # world its pixels are buried under other players -- the menu path is the
    # only tier that beats both at once.
    if _committed(data, "gotr_powerup_commit"):
        return    # USER: "one right click charge is enough" -- it turns in all
                  # stones; the walk + turn-in just takes a few ticks to land.
    if data.get("gotr_powerup_clicks", 0) >= POWERUP_MAX_CLICKS:
        ctx.state.trigger_safety_stop("gotr: power-up never registered")
        return
    data["gotr_powerup_clicks"] = data.get("gotr_powerup_clicks", 0) + 1
    stones_before = total_stones(st.get("inventory") or {})
    if ctx.events is not None:
        # This site bypasses _click_escalating, so it takes its own pre-click mark.
        data["gotr_click_mark"] = ctx.events.mark()
    if controller.click_menu_action(ctx.ser, guardian, POWER_UP_ACTION, offset=(0, 0),
                                    refresh_target=_refresh_for(guardian)):
        _await(ctx, "gotr_powerup_commit", POWERUP_COMMIT_S, CELL_SIGNALS,
               lambda: _confirm_fewer_stones(stones_before),
               shorten=_after_stop(ACTION_AFTER_STOP_S))
    else:
        # Off-window projection (run 48: six skipped right-clicks after a relogin
        # left the camera facing away, then the ceiling) -- turn and walk toward it.
        data.pop("gotr_click_mark", None)
        _bring_into_view(ctx, guardian)
    controller.mark_busy(2.0)
    if not data.get("gotr_outcome_hit"):
        controller.human_delay(0.5, 0.9)


def choose_guardian_pile(st):
    """Which essence pile to assemble at: the one whose energy type LAGS (a guardian pays
    energy in its own type only), elemental on a tie."""
    if st.get("elementalEnergy", 0) <= st.get("catalyticEnergy", 0):
        return ESSENCE_PILE_ELEMENTAL_ID
    return ESSENCE_PILE_CATALYTIC_ID


def create_guardian_phase(ctx, st):
    """Turn the charged cell into a rift guardian at the lagging type's essence pile.
    Success is the cell leaving the inventory (the same reset as place_cell)."""
    data = ctx.state.data
    if _committed(data, "gotr_guardian_clicks_commit"):
        return    # a verified Assemble is walking us there -- nothing to look up yet
    pile_id = choose_guardian_pile(st)
    pile = (_find_object_by_id(pile_id)
            or _find_object_by_name("essence pile (elemental)" if pile_id == ESSENCE_PILE_ELEMENTAL_ID
                                    else "essence pile (catalytic)", exact=True))
    if pile is None:
        misses = data.get("gotr_pile_misses", 0) + 1
        data["gotr_pile_misses"] = misses
        if misses >= 2:
            debug_log.log("gotr", "essence pile not visible twice -- falling back to the barrier")
            data["gotr_guardian_block_until"] = time.time() + ACTION_BLOCK_S
            data["gotr_pile_misses"] = 0
        return
    data["gotr_pile_misses"] = 0
    if _approach_or_click(ctx, pile, (ASSEMBLE_ACTION,), "gotr_guardian_clicks",
                          CELL_MAX_CLICKS, "rift guardian never created",
                          signals=CELL_SIGNALS, confirm=_confirm_cell_gone):
        _hold_action(data, "create_guardian", ACTION_COMMIT_S)
        controller.mark_busy(2.0)
        if not data.get("gotr_outcome_hit"):
            controller.human_delay(0.5, 0.9)


def place_cell_phase(ctx, st):
    """Spend the charged cell: place on an INACTIVE tile, else recharge the weakest."""
    data = ctx.state.data
    now = time.time()
    tried = {k: v for k, v in (data.get("gotr_cell_tried") or {}).items() if v > now}
    data["gotr_cell_tried"] = tried
    held = (st.get("inventory") or {}).get("chargedCellTier")
    tile = choose_cell_tile(st.get("barriers") or [], held, skip=set(tried))
    if tile is None:
        if data.get("gotr_cell_defer_logged") != held:
            debug_log.log("gotr", f"no usable cell tile for the held {held} cell -- "
                                  "deferring placement")
            data["gotr_cell_defer_logged"] = held
        data["gotr_cell_block_until"] = now + ACTION_BLOCK_S
        return
    loc = tile.get("location") or {}
    obj = _object_at_tile(loc.get("x"), loc.get("y"))
    if obj is None:
        if not data.get("gotr_no_cell_objects"):
            debug_log.log("gotr", "cell tile not present in /objects -- disabling cell "
                                  "actions (GroundObject gap, see spec checklist)")
        data["gotr_no_cell_objects"] = True
        return
    # A tile that ignored CELL_TILE_TRIES clicks (a barrier at full health has nothing
    # to recharge -- run 69, MEDIUM on a STRONG tile twice) is skipped for a while
    # and the next-best tile is tried, instead of spending the ceiling on it.
    key = (loc.get("x"), loc.get("y"))
    hits = data.setdefault("gotr_cell_tile_hits", {})
    if data.get("gotr_cell_last_tile") != key:
        data["gotr_cell_last_tile"] = key
        hits[key] = 0
    if hits.get(key, 0) >= CELL_TILE_TRIES:
        tried[key] = now + ACTION_BLOCK_S
        hits[key] = 0
        debug_log.log("gotr", f"cell tile {key} ignored {CELL_TILE_TRIES} clicks -- trying another")
        return
    if _approach_or_click(ctx, obj, CELL_TILE_PLACE_OPTIONS, "gotr_cell_clicks",
                          CELL_MAX_CLICKS, "charged cell never consumed",
                          signals=CELL_SIGNALS, confirm=_confirm_cell_gone):
        hits[key] = hits.get(key, 0) + 1
        # The click queues a walk to the tile; hold the ladder so a portal opening
        # 2s later does not cancel it (run 40 tick 469/470: cell still in hand).
        _hold_action(data, "place_cell", ACTION_COMMIT_S)
        controller.mark_busy(2.0)
        if not data.get("gotr_outcome_hit"):
            controller.human_delay(0.5, 0.9)


def altar_trip_phase(ctx, st):
    """Outside: enter the active portal guardian for the energy type we have less of.
    Inside (currentAltar set): _work_altar imbues, empties pouches, and exits."""
    data = ctx.state.data
    if (st.get("currentAltar") or "UNKNOWN") != "UNKNOWN":
        _work_altar(ctx, st)
        return

    if _committed(data, "gotr_enter_commit"):
        return    # a verified Enter is teleporting us -- don't re-click
    if st.get("guardianPower", -1) >= ALTAR_TRIP_MAX_POWER:
        if not data.get("gotr_late_trip_logged"):
            debug_log.log("gotr", f"guardian at {st.get('guardianPower')}% -- too late "
                                  "for another altar trip this round")
            data["gotr_late_trip_logged"] = True
        controller.human_delay(1.0, 2.0)
        return
    rotation = st.get("altarRotationSecondsRemaining", -1)
    if 0 <= rotation < ALTAR_MIN_SECONDS:
        debug_log.log("gotr", "altar pair about to rotate -- waiting to enter")
        controller.human_delay(1.0, 2.0)
        return

    cfg = ctx.state.config
    blocked = (tuple(cfg.get("blocked_altars") or ())
               + tuple(data.get("gotr_blocked_altars") or ()))
    altar = choose_altar(st, _rc_level(), blocked)
    if altar is None:
        if ((st.get("elementalAltar") or "UNKNOWN") == "UNKNOWN"
                and (st.get("catalyticAltar") or "UNKNOWN") == "UNKNOWN"):
            # HUD unparsed THIS snapshot (transitional tick) -- wait, don't embargo.
            # The old 30s block here fired on these and made altar entries look
            # random (USER 2026-09-01: "water/death was up but didn't go in, seems
            # to go for air and mind only").
            controller.human_delay(0.4, 0.8)
            return
        debug_log.log("gotr", "no enterable active altar -- deferring altar trips")
        data["gotr_altar_block_until"] = time.time() + ACTION_BLOCK_S
        return

    if data.get("gotr_altar_enters", 0) >= ALTAR_ENTER_MAX:
        # An entry that never lands is a quest-gated portal (Cosmic/Law/Death/Blood
        # entry is refused with no chat we parse) or a hopelessly contested pixel --
        # block THIS altar for the session and re-pick, never stop the run.
        debug_log.log("gotr", f"{altar} portal never entered -- blocking it this session")
        data.setdefault("gotr_blocked_altars", []).append(altar)
        data["gotr_altar_enters"] = 0
        return
    portal = _find_object_by_name(PORTAL_GUARDIAN_PREFIX + altar.lower(),
                                  action=PORTAL_GUARDIAN_ACTION)
    if portal is None:
        # Not a stop: the pair rotates on its own. Defer and let the ladder re-pick.
        debug_log.log("gotr", f"portal guardian for {altar} not visible -- deferring")
        data["gotr_altar_block_until"] = time.time() + ACTION_BLOCK_S
        return
    # The game performs the Enter on ARRIVAL: with less time on the rotation timer
    # than the run to the guardian, the pair has rotated before we get there (run 60:
    # clicked at 11s with a 5s walk, arrived to a new pair, waited out another cycle
    # holding 21 essence). Wait for the fresh pair instead, but walk closer meanwhile.
    if 0 <= rotation < _walk_budget(portal) + ALTAR_ENTER_MARGIN_S:
        debug_log.log("gotr", f"{altar} portal is {int(_walk_budget(portal) / PORTAL_RUN_S_PER_TILE)} "
                              f"tiles off with {rotation}s on the timer -- closing in, waiting")
        if _walk_budget(portal) > ALTAR_ENTER_MARGIN_S:
            _direct_walk(ctx, portal.get("x"), portal.get("y"))
        controller.human_delay(0.8, 1.4)
        return
    reach = travel.can_reach(portal)
    if reach is False:
        if not _walk_toward(ctx, portal):
            data["gotr_altar_enters"] = data.get("gotr_altar_enters", 0) + 1
        return
    data["gotr_altar_enters"] = data.get("gotr_altar_enters", 0) + 1
    debug_log.log("gotr", f"entering the {altar} portal")
    before = controller.get_last_action()
    budget = ENTER_COMMIT_S + _walk_budget(portal)
    if (_click_escalating(ctx, portal, (PORTAL_GUARDIAN_ACTION,))
            and _enter_resolved(before, PORTAL_GUARDIAN_ACTION, f"{altar} Enter") is not False):
        _commit(data, "gotr_enter_commit", budget)
        _hold_action(data, "altar_trip", budget)
    controller.mark_busy(3.0)
    controller.human_delay(0.7, 1.2)


def _too_far(entity, limit):
    """True when the player stands more than `limit` tiles from `entity` (Chebyshev);
    False when either position is unreadable, so an unreadable tick clicks as before."""
    p = controller.get_player() or {}
    if None in (p.get("x"), p.get("y"), entity.get("x"), entity.get("y")):
        return False
    return max(abs(p["x"] - entity["x"]), abs(p["y"] - entity["y"])) > limit


def _work_altar(ctx, st):
    """Inside an altar room: imbue loose essence (confirmed on runecraftXp), empty
    pouches, imbue again, then leave through the exit portal."""
    data = ctx.state.data
    inv = st.get("inventory") or {}
    if inv.get("guardianEssence", 0) > 0:
        if data.get("gotr_imbue_clicks", 0) >= IMBUE_MAX_CLICKS:
            ctx.state.trigger_safety_stop("gotr: imbuing never registered at the altar")
            return
        altar_obj = (_find_object_by_name(ALTAR_NAME, action=ALTAR_IMBUE_ACTION,
                                          exact=True)
                     or _find_object_by_name(ALTAR_NAME, exact=True))
        if altar_obj is None:
            debug_log.log("gotr", "altar object not visible inside the altar room")
            return
        if _too_far(altar_obj, ALTAR_CLICK_RANGE):
            # WALK FIRST, CLICK CLOSE -- the rubble rule (see ALTAR_CLICK_RANGE).
            debug_log.log("gotr", "altar is far from the entrance -- walking in")
            _direct_walk(ctx, altar_obj.get("x"), altar_obj.get("y"))
            controller.human_delay(0.5, 0.9)
            return
        xp_before = (controller.get_player() or {}).get("runecraftXp")
        data["gotr_imbue_clicks"] = data.get("gotr_imbue_clicks", 0) + 1
        _click_escalating(ctx, altar_obj, (ALTAR_IMBUE_ACTION,))
        controller.mark_busy(2.5)
        _await_xp("runecraftXp", xp_before, IMBUE_CONFIRM_S)
        return

    if pouch_stored(read_pouches()) > 0:
        _empty_pouches(ctx)
        return

    if _committed(data, "gotr_exit_commit"):
        return    # a verified exit click is teleporting us out
    if data.get("gotr_exit_clicks", 0) >= ALTAR_ENTER_MAX:
        ctx.state.trigger_safety_stop("gotr: could not leave the altar room")
        return
    exit_portal = None
    for act in EXIT_PORTAL_ACTIONS:
        exit_portal = _find_object_by_name(PORTAL_NAME, action=act, exact=True)
        if exit_portal is not None:
            break
    if exit_portal is None:
        exit_portal = _find_object_by_name(PORTAL_NAME, exact=True)
    if exit_portal is None:
        debug_log.log("gotr", "exit portal not visible in the altar room")
        return
    if _too_far(exit_portal, ALTAR_CLICK_RANGE):
        debug_log.log("gotr", "exit portal is far -- walking toward it")
        _direct_walk(ctx, exit_portal.get("x"), exit_portal.get("y"))
        controller.human_delay(0.5, 0.9)
        return
    data["gotr_exit_clicks"] = data.get("gotr_exit_clicks", 0) + 1
    if _click_escalating(ctx, exit_portal, EXIT_PORTAL_ACTIONS):
        _commit(data, "gotr_exit_commit", ENTER_COMMIT_S + _walk_budget(exit_portal))
    controller.mark_busy(3.0)
    controller.human_delay(0.7, 1.1)


def portal_trip_phase(ctx, st):
    """The huge-remains portal: enter while open, mine guardian essence inside, fill
    pouches, and leave when full (or the trip ceiling forces an exit)."""
    data = ctx.state.data
    inv = st.get("inventory") or {}

    if st.get("miningArea") == "WEST_HUGE_REMAINS" or side_area(st) == "pocket":
        data["gotr_portal_ticks"] = data.get("gotr_portal_ticks", 0) + 1
        space = pouch_free_space(read_pouches())
        # Fill pouches only once the INVENTORY is full (USER 2026-09-01: "let entire
        # inventory fill up then fill the bags then start mining again" -- matches the
        # human recording, where every Fill follows an inventory-too-full chat).
        if (inv.get("emptySlots", 0) == 0 and inv.get("guardianEssence", 0) > 0
                and space is not None and space > 0):
            _fill_pouches(ctx, resume=(HUGE_REMAINS_NAME, MINE_ACTION))
            data["gotr_mine_delta_at"] = time.time()   # the resume click IS mining
            return
        full = inv.get("emptySlots", 0) == 0 and (space is None or space == 0)
        if full or data["gotr_portal_ticks"] >= PORTAL_TRIP_MAX_TICKS:
            if _committed(data, "gotr_exit_commit"):
                return
            exit_portal = (_find_object_by_id(HUGE_POCKET_EXIT_ID)
                           or _find_object_by_name(PORTAL_NAME, exact=True))
            if exit_portal is None:
                debug_log.log("gotr", "exit portal not visible in the huge-remains pocket")
                return
            if _click_escalating(ctx, exit_portal, EXIT_PORTAL_ACTIONS):
                _commit(data, "gotr_exit_commit", ENTER_COMMIT_S + _walk_budget(exit_portal))
            controller.mark_busy(3.0)
            controller.human_delay(0.7, 1.1)
            return
        if time.time() - data.get("gotr_mine_delta_at", 0) < MINE_RECLICK_S:
            return    # actively mining -- leave it alone
        if _committed(data, "gotr_mine_commit"):
            return    # a verified Mine click is still walking us to the remains
        node = _find_object_by_name(HUGE_REMAINS_NAME, action=MINE_ACTION, exact=True)
        if node is None:
            debug_log.log("gotr", "huge remains not visible inside the pocket")
            return
        if _click_escalating(ctx, node, (MINE_ACTION,)):
            _commit(data, "gotr_mine_commit", MINE_COMMIT_S + _walk_budget(node))
        controller.mark_busy(2.0)
        controller.human_delay(0.6, 1.0)
        return

    # Outside: enter the portal while the HUD says it is open.
    if _committed(data, "gotr_enter_commit"):
        return    # a verified Enter is teleporting us -- don't re-click
    if data.get("gotr_portal_enters", 0) >= ALTAR_ENTER_MAX:
        debug_log.log("gotr", "portal never entered -- giving up on this spawn")
        data["gotr_portal_block_until"] = time.time() + ACTION_BLOCK_S
        data["gotr_portal_enters"] = 0
        return
    portal = _find_object_by_id(HUGE_PORTAL_ID)
    if portal is None:
        debug_log.log("gotr", "huge-remains portal not visible -- waiting")
        controller.human_delay(0.5, 0.9)
        return
    # The portal spawns at a random spot and stays ~25s: only go when the RUN
    # there fits inside what is left (run 38: clicked with 10s left from across
    # the arena, never arrived, and the ladder thrashed on the way).
    p = controller.get_player() or {}
    secs = st.get("guardianEssencePortalSecondsRemaining", -1)
    if None not in (p.get("x"), p.get("y"), portal.get("x"), portal.get("y")) and secs >= 0:
        tiles = max(abs(p["x"] - portal["x"]), abs(p["y"] - portal["y"]))
        if secs < tiles * PORTAL_RUN_S_PER_TILE + PORTAL_ARRIVAL_MARGIN_S:
            debug_log.log("gotr", f"portal {tiles} tiles away with {secs}s left -- "
                                  "too far, skipping this spawn")
            data["gotr_portal_block_until"] = time.time() + PORTAL_SKIP_BLOCK_S
            return
    reach = travel.can_reach(portal)
    if reach is False:
        if not _walk_toward(ctx, portal):
            data["gotr_portal_enters"] = data.get("gotr_portal_enters", 0) + 1
        return
    data["gotr_portal_enters"] = data.get("gotr_portal_enters", 0) + 1
    before = controller.get_last_action()
    budget = ENTER_COMMIT_S + _walk_budget(portal)
    if (_click_escalating(ctx, portal, (PORTAL_GUARDIAN_ACTION,))
            and _enter_resolved(before, PORTAL_GUARDIAN_ACTION, "huge-portal Enter") is not False):
        _commit(data, "gotr_enter_commit", budget)
        _hold_action(data, "portal_trip", budget)
    controller.mark_busy(3.0)
    controller.human_delay(0.7, 1.2)


def craft_phase(ctx, st):
    """The Workbench pass: deposit runes, fill pouches from loose essence, then keep
    the bench working fragments -> essence. Progress deltas are detected in
    _reset_counters, which stamps gotr_craft_delta_at and zeroes the stall counter."""
    data = ctx.state.data
    if _runes_in_inventory():
        _deposit_runes(ctx)
        return
    data["gotr_deposit_clicks"] = 0
    inv = st.get("inventory") or {}
    # Fill only on a FULL inventory (the bench stops itself there; the human fills
    # then immediately re-works the bench) -- see portal_trip_phase's same gate.
    if inv.get("emptySlots", 0) == 0 and inv.get("guardianEssence", 0) > 0:
        space = pouch_free_space(read_pouches())
        if space is not None and space > 0:
            _fill_pouches(ctx, resume=(WORKBENCH_NAME, WORKBENCH_ACTION))
            _commit(data, "gotr_craft_commit", CRAFT_COMMIT_S)
            return
    if time.time() - data.get("gotr_craft_delta_at", 0) < CRAFT_RECLICK_S:
        return    # crafting is progressing -- let it run
    if _committed(data, "gotr_craft_commit"):
        return    # a verified Work-at click is still playing out
    if data.get("gotr_craft_stall", 0) >= WORKBENCH_STALL_TICKS:
        ctx.state.trigger_safety_stop("gotr: workbench crafting never progressed")
        return
    bench = _find_object_by_name(WORKBENCH_NAME, exact=True)
    if bench is None:
        data["gotr_craft_stall"] = data.get("gotr_craft_stall", 0) + 1
        debug_log.log("gotr", "workbench not visible -- waiting")
        controller.human_delay(1.0, 2.0)
        return
    reach = travel.can_reach(bench)
    if reach is False and not _arena_direct_click_ok(bench):
        if not _walk_toward(ctx, bench):
            data["gotr_craft_stall"] = data.get("gotr_craft_stall", 0) + 1
        return
    data["gotr_craft_stall"] = data.get("gotr_craft_stall", 0) + 1
    if _click_escalating(ctx, bench, (WORKBENCH_ACTION,)):
        _commit(data, "gotr_craft_commit", CRAFT_COMMIT_S + _walk_budget(bench))
    controller.mark_busy(3.0)
    controller.human_delay(1.0, 1.6)


def mine_phase(ctx, st):
    """Mine guardian remains/parts at the temple edges (or the large-remains pocket
    behind the rubble when configured). Deltas detected in _reset_counters."""
    data = ctx.state.data
    cfg = ctx.state.config
    if time.time() - data.get("gotr_mine_delta_at", 0) < MINE_RECLICK_S:
        return    # actively mining
    if _committed(data, "gotr_mine_commit"):
        return    # a verified Mine click is still playing out (walk + wind-up)
    if data.get("gotr_mine_stall", 0) >= MINE_STALL_TICKS:
        ctx.state.trigger_safety_stop("gotr: mining never produced fragments")
        return
    node = None
    if cfg.get("use_large_remains"):
        node = _find_object_by_name(LARGE_REMAINS_NAME, action=MINE_ACTION, exact=True)
    if node is None:
        node = _find_object_by_name(GUARDIAN_REMAINS_NAMES, action=MINE_ACTION,
                                    exact=True)
    if node is None:
        data["gotr_mine_stall"] = data.get("gotr_mine_stall", 0) + 1
        debug_log.log("gotr", "no minable remains visible -- waiting")
        controller.human_delay(1.0, 2.0)
        return
    reach = travel.can_reach(node)
    if reach is False:
        # The pocket's remains are NEVER routable from outside (enclosed on the map),
        # so head straight for the rubble -- adjacency doesn't matter, the click queues
        # the walk + climb (human recording: Climb clicked from across the arena).
        if cfg.get("use_large_remains") and node.get("name", "").lower() == LARGE_REMAINS_NAME:
            if _climb_rubble(ctx):
                return
        if not _walk_toward(ctx, node):
            data["gotr_mine_stall"] = data.get("gotr_mine_stall", 0) + 1
        return
    data["gotr_mine_stall"] = data.get("gotr_mine_stall", 0) + 1
    if _click_escalating(ctx, node, (MINE_ACTION,)):
        _commit(data, "gotr_mine_commit", MINE_COMMIT_S + _walk_budget(node))
    controller.mark_busy(2.0)
    controller.human_delay(0.6, 1.0)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def _reset_counters(data, st, state):
    """Counters reset where success is DETECTED (read back from the fresh snapshot),
    never where the attempt was made -- the mahogany_homes contact-cast lesson."""
    inv = st.get("inventory") or {}
    now = time.time()

    if st.get("inArena"):
        data["gotr_enter_clicks"] = 0
    if state == "active":
        # The between-round window is over; next window starts a fresh repair budget.
        data["gotr_contact_casts"] = 0
        data["gotr_contact_stuck"] = 0

    frags = inv.get("guardianFragments", 0)
    mined = st.get("minedFragmentsThisGame", 0)
    essence = inv.get("guardianEssence", 0)
    # The FIRST snapshot only seeds the baselines: against the -1 defaults it read as a
    # mine AND a craft delta, and MINE_RECLICK_S/CRAFT_RECLICK_S then held the rung for
    # 4-5s of nothing at the start of every run (MEASURED runs 38-42, first tick).
    seeded = "gotr_last_frags" in data
    if seeded and (frags > data.get("gotr_last_frags", -1)
                   or mined > data.get("gotr_last_mined", -1)):
        data["gotr_mine_delta_at"] = now
        data["gotr_mine_stall"] = 0
    if seeded and (essence > data.get("gotr_last_essence", -1)
                   or frags < data.get("gotr_last_frags", 1 << 30)):
        data["gotr_craft_delta_at"] = now
        data["gotr_craft_stall"] = 0
    if inv.get("unchargedCells", 0) > data.get("gotr_last_uncharged", -1):
        data["gotr_restock_clicks"] = 0
    data["gotr_last_frags"] = frags
    data["gotr_last_mined"] = mined
    data["gotr_last_essence"] = essence
    data["gotr_last_uncharged"] = inv.get("unchargedCells", 0)

    stones = total_stones(inv)
    if stones < data.get("gotr_last_stones", 0):
        data["gotr_powerup_clicks"] = 0
    if stones > data.get("gotr_last_stones", -1):
        data["gotr_imbue_clicks"] = 0
    data["gotr_last_stones"] = stones

    tier = inv.get("chargedCellTier") or "NONE"
    if tier == "NONE" and data.get("gotr_last_cell") not in (None, "NONE"):
        data["gotr_cell_clicks"] = 0
        data["gotr_guardian_clicks"] = 0
        # The cell was consumed: the per-tile hit counter starts over (run 71: a hit
        # from the previous round's placement on the same tile made the FIRST click of
        # the next cell read as "ignored 2 clicks").
        data["gotr_cell_tile_hits"] = {}
        data["gotr_cell_last_tile"] = None
    data["gotr_last_cell"] = tier

    if find_repairable(st.get("barriers") or []) is None:
        data["gotr_repair_clicks"] = 0

    current_altar = st.get("currentAltar") or "UNKNOWN"
    if current_altar != data.get("gotr_last_altar", "UNKNOWN"):
        data["gotr_altar_enters"] = 0
        data["gotr_exit_clicks"] = 0
        data["gotr_imbue_clicks"] = 0
    data["gotr_last_altar"] = current_altar

    if st.get("miningArea") == "WEST_HUGE_REMAINS" or side_area(st) == "pocket":
        data["gotr_portal_enters"] = 0
    else:
        data["gotr_portal_ticks"] = 0


STATE_LOG_EVERY_S = 20.0
STUCK_WARN_TICKS = 25      # in-round ticks with NO progress signal before a loud [!] line
                           # (15 fired on legitimate 13s walks to a far portal, run 62)
STUCK_STOP_TICKS = 60      # ...and before a safety stop (~55s); breaks cannot freeze a wedge


def _progress_key(st, action):
    """Everything that changes when the bot is actually getting somewhere: the player
    tile, the chosen rung, every inventory count, and which side area we are in. A
    tick whose key equals the previous one made no progress."""
    inv = st.get("inventory") or {}
    p = controller.get_player() or {}
    return (p.get("x"), p.get("y"), action, inv.get("guardianFragments"),
            inv.get("guardianEssence"), total_stones(inv), inv.get("chargedCellTier"),
            inv.get("unchargedCells"), inv.get("emptySlots"), st.get("currentAltar"),
            st.get("miningArea"))


def _stuck_watch(ctx, st, action):
    """USER 2026-09-02: "you need to monitor and catch this" -- run 46 clicked the
    chatbox scrollbar 30 times toward the Cosmic altar and nothing in the log said
    anything a filter could catch. This is the generic net: in-round, any rung but
    idle, no progress signal for STUCK_WARN_TICKS logs a [!] line (which the live
    monitor keys on) and STUCK_STOP_TICKS safety-stops. True when stopped."""
    data = ctx.state.data
    if action == "idle":
        data["gotr_stuck_ticks"] = 0
        return False
    key = _progress_key(st, action)
    if key == data.get("gotr_progress_key"):
        data["gotr_stuck_ticks"] = data.get("gotr_stuck_ticks", 0) + 1
    else:
        data["gotr_stuck_ticks"] = 0
    data["gotr_progress_key"] = key
    n = data["gotr_stuck_ticks"]
    if n >= STUCK_STOP_TICKS:
        ctx.state.trigger_safety_stop(
            f"gotr: no progress for {n} ticks during {action} (player not moving, "
            "inventory unchanged)")
        return True
    if n >= STUCK_WARN_TICKS and n % 5 == 0:
        debug_log.log("!", f"gotr: STUCK? no progress for {n} ticks during {action} "
                           f"at {key[0]},{key[1]}")
    return False


def _log_state(data, st, state, action):
    """One line whenever the (state, action) pair changes, and a heartbeat every
    STATE_LOG_EVERY_S otherwise -- so a stretch of the log with no clicks still says
    WHICH rung the ladder chose and what the snapshot looked like (run 35: 63s of
    idle drift after a cell restock, then an enter/exit of the huge-remains portal
    6s apart, and nothing in the log to say why)."""
    inv = st.get("inventory") or {}
    now = time.time()
    key = (state, action)
    if key == data.get("gotr_log_key") and now - data.get("gotr_log_at", 0) < STATE_LOG_EVERY_S:
        return
    data["gotr_log_key"] = key
    data["gotr_log_at"] = now
    debug_log.log(
        "gotr",
        f"state={state} action={action} power={st.get('guardianPower')} "
        f"rot={st.get('altarRotationSecondsRemaining')} "
        f"altars={st.get('elementalAltar')}/{st.get('catalyticAltar')} "
        f"energy={st.get('elementalEnergy')}/{st.get('catalyticEnergy')} "
        f"portal={'open' if st.get('guardianEssencePortalOpen') else 'shut'}/"
        f"{st.get('guardianEssencePortalSecondsRemaining')} "
        f"frags={inv.get('guardianFragments')} ess={inv.get('guardianEssence')} "
        f"stones={total_stones(inv)} cell={inv.get('chargedCellTier')} "
        f"guardians={st.get('activeGuardians')}/{st.get('guardianLimit')} "
        f"uncharged={inv.get('unchargedCells')} slots={inv.get('emptySlots')} "
        f"area={st.get('miningArea')}/{st.get('currentAltar')} "
        f"arena={st.get('inArena')} temple={st.get('inMainTemple')}")


def setup(ctx):
    # setup() runs BEFORE login (engine calls it right after the serial port opens):
    # live 2026-09-01 run 32 launched against a login screen, read an EMPTY
    # inventory, and safety-stopped on "no pickaxe". The real checks run from the
    # first tick (tick only runs while logged in) via _startup_checks.
    debug_log.log("gotr", "setup -- prerequisite checks deferred to first tick")


def _startup_checks(ctx):
    """True once the checks have actually RUN. The first logged-in tick after an
    auto-login can still serve an EMPTY inventory (run 47: safety-stopped on "no
    pickaxe" with a Rune pickaxe equipped) -- an empty read means "not loaded yet",
    so the checks defer to a later tick instead of judging it."""
    inv = controller.get_inventory() or []
    eq = controller.get_equipment() or {}
    if not inv and not any(isinstance(v, dict) and v.get("name") for v in eq.values()):
        return False
    names = [(i.get("name") or "").lower() for i in inv]
    eq_names = [((eq.get(slot) or {}).get("name") or "").lower() for slot in eq]
    if not any("pickaxe" in n for n in names + eq_names):
        ctx.state.trigger_safety_stop("gotr: no pickaxe in inventory or equipped")
        return True
    held = [n for n in names if any(p in n for p in POUCH_NAMES)]
    debug_log.log("gotr", f"setup ok -- pouches held: {held or 'none'}")
    # setup() runs BEFORE main() resolves ctx.state.config (crashed live 2026-09-01),
    # so resolve our own -- get_config ignores bridge_config anyway.
    if get_config(None).get("repair_pouches"):
        magic = controller.get_api("player", "magic")
        book = ""
        if isinstance(magic, dict) and not magic.get("error"):
            for key in ("spellbook", "book", "activeSpellbook"):
                if isinstance(magic.get(key), str):
                    book = magic[key].lower()
                    break
        if book and "lunar" not in book:
            # Spec: degrade to a warning and disable repair, never a hard stop. An
            # unreadable reader (book == "") gates nothing.
            debug_log.log("gotr", f"spellbook is '{book}', not Lunar -- pouch repair "
                                  "disabled for this session")
            ctx.state.data["gotr_repair_disabled"] = True
    return True


# ---------------------------------------------------------------------------
# World + camera setup (2026-09-03)
# ---------------------------------------------------------------------------

# The dedicated Guardians of the Rift worlds (wiki "Runecraft > Official worlds", rev
# 15294012), European ones first. USER 2026-09-03 after seven straight failed rounds on a
# non-themed world: "gotta be on a guardians of the rift world, otherwise i dont think we
# finish a game". Hopping is done through the in-game World Switcher with real clicks.
GOTR_WORLDS = (409, 459, 464, 507, 522, 478, 490, 492, 445, 425, 534)   # 409 first: USER
HOP_MAX_ATTEMPTS = 3
HOP_SCROLL_MAX = 40            # scroll steps hunting for the row before giving up
HOP_CONFIRM_TIMEOUT_S = 20.0


def _world_rows(rows):
    """Only the WORLD-NUMBER column. The switcher list has two numeric columns -- world
    number and player count -- and a player count of 459 is indistinguishable from world
    459 by text alone (MEASURED 2026-09-03: "459" appeared twice, the first one in the
    right-hand column). The world column is the leftmost one."""
    rows = [r for r in rows or [] if isinstance(r, dict)]
    xs = [r["bounds"]["x"] for r in rows if isinstance(r.get("bounds"), dict)]
    if not xs:
        return rows
    left = min(xs)
    return [r for r in rows if not isinstance(r.get("bounds"), dict)
            or r["bounds"]["x"] <= left + 4]


def choose_hop_world(rows, current, preferred=GOTR_WORLDS):
    """The first preferred world present in the switcher rows (any hidden state -- a
    hidden row just needs scrolling), or None."""
    present = {r.get("world") for r in _world_rows(rows)}
    for w in preferred:
        if w != current and w in present:
            return w
    return None


def _row_for(rows, world):
    for r in _world_rows(rows):
        if r.get("world") == world:
            return r
    return None


def _row_visible(row, bounds):
    if not row or not bounds:
        return False
    y = row.get("screenY", -1)
    return bounds["y"] + 4 <= y <= bounds["y"] + bounds["height"] - 4


def _ensure_gotr_world(ctx):
    """Hop to a GOTR world through the World Switcher if we are not on one. True when on
    a GOTR world at the end (or when the bridge cannot say, so nothing blocks)."""
    w = controller.get_world()
    current = w.get("world")
    if current is None:
        return True    # un-rebuilt client: cannot tell, do not block
    if current in GOTR_WORLDS:
        debug_log.log("gotr", f"on GOTR world {current}")
        return True
    for attempt in range(1, HOP_MAX_ATTEMPTS + 1):
        debug_log.log("gotr", f"world {current} is not a GOTR world -- hopping (attempt {attempt})")
        if _hop_via_switcher(ctx):
            current = (controller.get_world() or {}).get("world")
            if current in GOTR_WORLDS:
                debug_log.log("gotr", f"now on GOTR world {current}")
                return True
        controller.human_delay(2.0, 3.0)
    ctx.state.trigger_safety_stop("gotr: could not hop to a Guardians of the Rift world")
    return False


def _hop_via_switcher(ctx):
    """Open the logout tab, the World Switcher, scroll to a GOTR row, click it, and wait
    for the hop. Every element comes from GET /world (WorldProvider)."""
    w = controller.get_world()
    if not w.get("switcherOpen"):
        tab = w.get("logoutTab")
        if tab is None:
            debug_log.log("gotr", "logout tab button not visible")
            return False
        controller.click_entity(ctx.ser, dict(tab, name="logout tab"), offset=(0, 0),
                                check_ui_blockers=False)
        controller.human_delay(0.6, 0.9)
        w = controller.get_world()
        btn = w.get("worldSwitcherButton")
        if btn is None:
            debug_log.log("gotr", "World Switcher button not visible on the logout tab")
            return False
        controller.click_entity(ctx.ser, dict(btn, name="World Switcher"), offset=(0, 0),
                                check_ui_blockers=False)
        controller.human_delay(1.0, 1.5)
        w = controller.get_world()
        if not w.get("switcherOpen"):
            debug_log.log("gotr", "World Switcher list did not open")
            return False
    target = choose_hop_world(w.get("rows"), w.get("world"))
    if target is None:
        debug_log.log("gotr", "no GOTR world in the switcher list")
        return False
    bounds = w.get("listBounds")
    row = None
    # Scroll the list with the wheel, cursor parked over it, until the row is inside it.
    for _ in range(HOP_SCROLL_MAX):
        row = _row_for(w.get("rows"), target)
        if row is not None and _row_visible(row, bounds):
            break
        if row is None or bounds is None:
            return False
        centre_y = bounds["y"] + bounds["height"] // 2
        distance = row["screenY"] - centre_y
        direction = 1 if distance > 0 else -1
        # ~40 screen px per wheel detent at this stretch; big jumps first, fine steps near.
        detents = max(2, min(15, abs(distance) // 40))
        cx = bounds["x"] + bounds["width"] // 2
        controller.move_to_human(ctx.ser, cx, centre_y, aim_radius=6)
        controller.scroll_wheel(ctx.ser, -direction * detents)
        controller.human_delay(0.3, 0.5)
        w = controller.get_world()
        bounds = w.get("listBounds") or bounds
    else:
        debug_log.log("gotr", f"world {target} row never scrolled into view")
        return False
    debug_log.log("gotr", f"switching to world {target}")
    controller.click_entity(ctx.ser, dict(row, name=f"world {target}"), offset=(0, 0),
                            check_ui_blockers=False)
    deadline = time.time() + HOP_CONFIRM_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(1.0)
        st = controller.get_state() or {}
        if st.get("gameState") == "LOGGED_IN" and (controller.get_world() or {}).get("world") == target:
            return True
    debug_log.log("gotr", f"hop to {target} did not complete in {HOP_CONFIRM_TIMEOUT_S:.0f}s")
    return False


_RIFT_STARTS_RE = re.compile(r"rift will become active in (\d+) (second|minute)")


def on_event(ctx, event_type, payload):
    """Stamp the round-start countdown off the game's own chat (the DatBear helper
    plugin's method -- 'The rift will become active in 30 seconds.' etc.); the prep
    schedule keys the bench run off it."""
    if event_type != "CHAT_MESSAGE":
        return
    msg = ((payload or {}).get("message") or (payload or {}).get("text") or "")
    m = _RIFT_STARTS_RE.search(msg)
    if m:
        n = int(m.group(1)) * (60 if m.group(2) == "minute" else 1)
        ctx.state.data["gotr_round_starts_at"] = time.time() + n


def _leave_side_area(ctx):
    """Click the exit Portal of whatever side room we are standing in (altar rooms and
    the huge-remains pocket both have one). True when a portal was found and clicked
    or walked toward; False when no exit portal is in the scene."""
    data = ctx.state.data
    if _committed(data, "gotr_exit_commit"):
        return True
    exit_portal = (_find_object_by_id(HUGE_POCKET_EXIT_ID)
                   or _find_object_by_name(PORTAL_NAME, exact=True))
    if exit_portal is None or not any(
            a for a in (exit_portal.get("actions") or [])
            if a and any(x.lower() in a.lower() for x in EXIT_PORTAL_ACTIONS)):
        return False
    debug_log.log("gotr", "leaving the side area through its exit portal")
    if _too_far(exit_portal, ALTAR_CLICK_RANGE):
        _direct_walk(ctx, exit_portal.get("x"), exit_portal.get("y"))
        controller.human_delay(0.5, 0.9)
        return True
    if _click_escalating(ctx, exit_portal, EXIT_PORTAL_ACTIONS):
        _commit(data, "gotr_exit_commit", ENTER_COMMIT_S + _walk_budget(exit_portal))
    controller.human_delay(0.7, 1.1)
    return True


BREAK_DEFER_MAX_S = 720.0      # play an active round out before a break, at most this long
BREAK_INACTIVE_READS = 6       # consecutive non-active reads (~6-8s) that mean the round is over


def before_break(ctx):
    """Engine hook: a break is about to start. Play the ACTIVE round out first (tick()
    keeps running here, so the round finishes normally), then make sure we are not
    inside a side area. MEASURED run 50: a break began at 08:25:45 with the bot inside
    an altar room; the round ended during the 8-minute break, the altar exit then
    dropped it in the LOBBY with 39 stones and a cell wiped, and it waited a whole
    extra game at the barrier. A break that starts at the round end costs only the
    break; one that starts mid-round costs the round as well."""
    deadline = time.time() + BREAK_DEFER_MAX_S
    played = False
    inactive = 0
    while time.time() < deadline:
        st = read_state()
        if st is not None and round_state(st) == "active":
            inactive = 0
        else:
            # A portal/altar teleport hides the HUD for a few ticks and that snapshot
            # reads as "idle" -- the same transitional family as ACTIVE_SETTLE_S. The
            # first version broke out on ONE such read and started the break inside
            # the Chaos altar 5s after entering it (run 51, 09:33:19). Sustained only.
            inactive += 1
            if inactive >= BREAK_INACTIVE_READS:
                break
        if getattr(ctx.state, "safety_stopped", False):
            return
        if not played:
            debug_log.log("gotr", "break due -- playing the round out first")
            played = True
        tick(ctx)
        time.sleep(0.2)
    st = read_state()
    if st is not None and (side_area(st)
                           or (st.get("currentAltar") or "UNKNOWN") != "UNKNOWN"):
        # Leave the altar room / portal pocket so the break is spent in the arena.
        debug_log.log("gotr", "break due -- leaving the side area first")
        for _ in range(3):
            if not _leave_side_area(ctx):
                break
            controller.human_delay(3.0, 4.0)


def tick(ctx):
    """One engine tick = one rung, PLUS every rung whose predecessor's outcome landed, up to
    CHAIN_MAX_ACTIONS / CHAIN_MAX_S (the mining/agility chaining shape). Every ceiling and the
    stuck watch count a chained action exactly as a ticked one."""
    data = ctx.state.data
    deadline = time.time() + CHAIN_MAX_S
    for _ in range(CHAIN_MAX_ACTIONS):
        data["gotr_outcome_hit"] = False
        _tick_once(ctx)
        if (not data.get("gotr_outcome_hit") or ctx.state.safety_stopped
                or time.time() >= deadline):
            return


def _tick_once(ctx):
    data = ctx.state.data
    if not data.get("gotr_checked"):
        if not _startup_checks(ctx):
            controller.human_delay(0.4, 0.8)
            return
        data["gotr_checked"] = True
        if ctx.state.safety_stopped:
            return
        if not _ensure_gotr_world(ctx):
            return
    st = read_state()
    if st is None:
        data["gotr_no_state"] = data.get("gotr_no_state", 0) + 1
        if data["gotr_no_state"] >= STATE_UNAVAILABLE_LIMIT:
            ctx.state.trigger_safety_stop(
                "gotr: /api/minigames/guardians-of-the-rift unavailable -- "
                "rebuild + restart the client?")
        return
    data["gotr_no_state"] = 0

    if st.get("banned"):
        ctx.state.trigger_safety_stop("gotr: the GOTR ban flag is set on this account")
        return

    if not (st.get("inventory") or {}).get("inventoryPresent", True):
        # A teleport/loading tick serves a snapshot with the inventory container
        # ABSENT -- every count is a zero that means nothing. Acting on it is how
        # the ladder "climbed down to mine" with full pouches one tick after a
        # portal exit (run 18, 2026-09-01). Defer; the real inventory is back next
        # tick. Fifth member of the transitional-snapshot bug family.
        controller.human_delay(0.3, 0.6)
        return

    state = round_state(st)
    _reset_counters(data, st, state)

    if state == "outside":
        # The altar-EXIT teleport's loading tick reads as "outside" for a snapshot
        # (no temple region, no currentAltar, HUD unparsed -- run 9, 2026-09-01:
        # imbue -> exit portal -> instant safety stop). Only a SUSTAINED outside
        # reading stops the run.
        data["gotr_outside_streak"] = data.get("gotr_outside_streak", 0) + 1
        if data["gotr_outside_streak"] >= 3 and _leave_side_area(ctx):
            # Inside an altar room / the portal pocket AFTER the round ended: the HUD
            # is gone, so side_area() cannot recognise it and the streak would stop
            # the run (run 57 started inside the Earth altar this way). An exit
            # Portal in the scene is the tell -- take it.
            data["gotr_outside_streak"] = 0
            return
        if data["gotr_outside_streak"] >= OUTSIDE_STREAK_LIMIT:
            ctx.state.trigger_safety_stop(
                "gotr: start at the Temple of the Eye (lobby or arena)")
        return
    data["gotr_outside_streak"] = 0
    if state == "lobby":
        _log_state(data, st, state, "enter_arena")
        enter_arena_phase(ctx, st)
        return
    if state in ("waiting", "complete", "idle"):
        # A loading-screen tick during an area transition reads as "idle" for one
        # snapshot (live 2026-09-01: two stray Astral Contact casts fired inside the
        # portal mine) -- the streak makes between-round ACTIONS wait out one tick.
        data["gotr_between_streak"] = data.get("gotr_between_streak", 0) + 1
        if time.time() - data.get("gotr_active_at", 0) < ACTIVE_SETTLE_S:
            # HUD hidden by a teleport/loading tick straight after an active read:
            # a transitional snapshot, not the end of the round. Defer.
            _log_state(data, st, state, "settling")
            controller.human_delay(0.3, 0.6)
            return
        if data["gotr_between_streak"] >= 2:
            # A new game is forming: re-arm the once-per-game mining batch.
            data["gotr_batch_done"] = False
            data["gotr_late_trip_logged"] = False
        _log_state(data, st, state, "between_rounds")
        between_rounds_phase(ctx, st)
        return
    data["gotr_between_streak"] = 0
    data["gotr_active_at"] = time.time()

    cfg = ctx.state.config

    # In-round the mine rung is DEAD once the batch has been mined (USER
    # 2026-09-01: "after prep phase never go mine again, just wait for next prep").
    # The latch is set BELOW, the first time the ladder chooses "craft" this game
    # -- i.e. the batch target (or the altar-timed switch) was actually reached.
    # An unconditional in-round latch idled a mid-round join with 0 frags for the
    # whole round (run 37), and keying it on the reader's mined count latched on
    # the FIRST fragment and dragged the bot back over the rubble with 3 frags
    # (run 38). Re-armed for the next game in the between-round branch above.
    # A RESTART loses the latch while the game keeps the fragments -- the reader's
    # own per-game mined count reaching the batch target re-latches it, so a
    # restart mid-round does not go back over the rubble with 81 frags in hand.
    if st.get("minedFragmentsThisGame", 0) >= cfg["craft_at_fragments"]:
        data["gotr_batch_done"] = True

    # Active round: one prioritized action, with learned-unworkable rungs disabled.
    now = time.time()
    disabled = set()
    if data.get("gotr_no_cell_objects"):
        disabled |= {"repair_barrier", "place_cell"}
    if now < data.get("gotr_cell_block_until", 0):
        disabled.add("place_cell")
    if now < data.get("gotr_guardian_block_until", 0):
        disabled.add("create_guardian")
    if now < data.get("gotr_altar_block_until", 0):
        disabled.add("altar_trip")
    if now < data.get("gotr_portal_block_until", 0):
        disabled.add("portal_trip")
    if now < data.get("gotr_restock_block_until", 0):
        disabled.add("restock_cells")
    tile = find_repairable(st.get("barriers") or [])
    if tile is not None:
        p = controller.get_player() or {}
        loc = tile.get("location") or {}
        if None not in (p.get("x"), p.get("y"), loc.get("x"), loc.get("y")):
            if max(abs(p["x"] - loc["x"]), abs(p["y"] - loc["y"])) > REPAIR_MAX_DISTANCE:
                disabled.add("repair_barrier")
    action = choose_action(st, cfg, pouch_space=pouch_free_space(read_pouches()),
                           disabled=disabled,
                           crafting=_craft_in_progress(data, st.get("inventory") or {}),
                           batch_done=bool(data.get("gotr_batch_done")))
    held = _held_action(data, disabled, st)
    if held is not None and held != action:
        action = held
    # Stall ceilings measure CONSECUTIVE futility within one pursuit -- a rung that
    # is not currently chosen must not keep its old count (live run 33: craft stall
    # accumulated across unrelated stretches and a 24-tick camera blindness spent
    # what was left, stopping a healthy run).
    if action != "craft":
        data["gotr_craft_stall"] = 0
    if action != "mine":
        data["gotr_mine_stall"] = 0
    if action == "craft" and not data.get("gotr_batch_done"):
        debug_log.log("gotr", "mining batch complete -- the mine rung is dead until "
                              "the next game")
        data["gotr_batch_done"] = True
    _log_state(data, st, state, action)
    if _stuck_watch(ctx, st, action):
        return
    ACTION_HANDLERS[action](ctx, st)


def drop_talismans_phase(ctx, st):
    """Drop every portal talisman in the inventory (menu Drop -- a bare left click
    on a talisman would try to enter its portal)."""
    controller.open_inventory_tab(ctx.ser)
    for i in controller.get_inventory() or []:
        if "portal talisman" in (i.get("name") or "").lower():
            controller.click_menu_action(ctx.ser, i, "Drop", offset=(0, 0),
                                         check_ui_blockers=False)
            controller.human_delay(0.15, 0.35)


def _drop_one(ctx, name_sub):
    """Menu-Drop the first inventory item whose name contains name_sub."""
    controller.open_inventory_tab(ctx.ser)
    for i in controller.get_inventory() or []:
        if name_sub in (i.get("name") or "").lower():
            debug_log.log("gotr", f"dropping one {i.get('name')} to free a slot")
            controller.click_menu_action(ctx.ser, i, "Drop", offset=(0, 0),
                                         check_ui_blockers=False)
            controller.human_delay(0.3, 0.5)
            return True
    return False


def idle_phase(ctx, st):
    """Batch mined, nothing craftable, no stones/cell/portal: wait for the next
    portal spawn or altar rotation rather than re-mining (USER: mine once per game)."""
    controller.human_delay(1.0, 2.0)


def restock_cells_phase(ctx, st):
    data = ctx.state.data
    if data.get("gotr_restock_clicks", 0) >= RESTOCK_MAX_CLICKS:
        # Give-up, not a stop: rank the rung out for a while; the counter resets in
        # _reset_counters when the cell count actually rises.
        data["gotr_restock_block_until"] = time.time() + ACTION_BLOCK_S
        data["gotr_restock_clicks"] = 0
        return
    _restock_cells(ctx, st)


ACTION_HANDLERS = {
    "repair_barrier": repair_barrier_phase,
    "portal_trip": portal_trip_phase,
    "spend_stones": spend_stones_phase,
    "place_cell": place_cell_phase,
    "create_guardian": create_guardian_phase,
    "restock_cells": restock_cells_phase,
    "altar_trip": altar_trip_phase,
    "craft": craft_phase,
    "mine": mine_phase,
    "idle": idle_phase,
    "drop_talismans": drop_talismans_phase,
}
