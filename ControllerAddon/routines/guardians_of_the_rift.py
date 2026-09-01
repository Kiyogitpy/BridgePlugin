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
PORTAL_NAME = "portal"                          # exact -- huge-remains portal AND altar exit
ALTAR_NAME = "altar"                            # exact -- inside the altar room
ALTAR_IMBUE_ACTION = "Craft-rune"
EXIT_PORTAL_ACTIONS = ("Use", "Enter", "Exit")
GUARDIAN_REMAINS_NAMES = ("guardian remains", "guardian parts")   # exact, both
LARGE_REMAINS_NAME = "large guardian remains"   # exact -- behind the 56 Agility rubble
HUGE_REMAINS_NAME = "huge guardian remains"     # exact -- inside the portal pocket
RUBBLE_NAME = "rubble"                          # exact
RUBBLE_ACTION = "Climb"
MINE_ACTION = "Mine"
CELL_TILE_PLACE_OPTIONS = ("Place-cell", "Recharge", "Strengthen")
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
ENTER_MAX_CLICKS = 6
CONTACT_MAX_CASTS = 4          # per between-round window; give-up, not a stop
CONTACT_STUCK_LIMIT = 8
CONTACT_RESOLVE_S = 15.0       # scan for the Dark Mage row this long after a cast
ALTAR_ENTER_MAX = 5
IMBUE_MAX_CLICKS = 8
IMBUE_CONFIRM_S = 4.0
POWERUP_MAX_CLICKS = 6
CELL_MAX_CLICKS = 6
RESTOCK_MAX_CLICKS = 6
GUARDIAN_MISSING_LIMIT = 15
MINE_STALL_TICKS = 25
MINE_RECLICK_S = 5.0           # leave an in-progress mine alone this long after a delta
WORKBENCH_STALL_TICKS = 20
CRAFT_RECLICK_S = 4.0
PORTAL_TRIP_MAX_TICKS = 60     # forces an exit from the pocket, not a stop
ACTION_BLOCK_S = 30.0          # how long a learned-unworkable rung stays disabled


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def read_state():
    """One fresh GOTR snapshot, or None for 'cannot tell'. None must defer, never act."""
    st = controller.get_api("minigames", "guardians-of-the-rift")
    if not st or st.get("error"):
        return None
    return st


def read_pouches():
    api = controller.get_api("storage", "essence-pouches")
    if not api or api.get("error"):
        return None
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
    for obj in controller.get_objects(radius=radius):
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
    res = controller.click_entity(ctx.ser, entity, refresh_target=_refresh_for(entity),
                                  expected_action=options)
    if res and not getattr(res, "withheld", False):
        return True
    fresh = _reresolve(entity)
    return bool(controller.click_menu_action(
        ctx.ser, fresh, options[0], offset=(0, 0),
        refresh_target=_refresh_for(fresh)))


def _approach_or_click(ctx, entity, options, counter_key, max_clicks, stop_reason):
    """The standard gated click: ceiling -> can_reach -> walk when unreachable ->
    escalating click. A routeless walk ALSO spends the counter (every loop needs a
    ceiling). True only when a click was issued."""
    data = ctx.state.data
    if data.get(counter_key, 0) >= max_clicks:
        ctx.state.trigger_safety_stop(f"gotr: {stop_reason}")
        return False
    reach = travel.can_reach(entity)
    if reach is False:
        if not travel.walk_to(ctx, entity):
            data[counter_key] = data.get(counter_key, 0) + 1
        return False
    data[counter_key] = data.get(counter_key, 0) + 1
    _click_escalating(ctx, entity, options)
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


def _fill_pouches(ctx):
    # Explicit menu Fill: a pouch's LEFT-click default varies with state, and the
    # fur-pouch lesson says never trust the default for container items.
    for pouch in _pouch_items():
        controller.click_menu_action(ctx.ser, pouch, "Fill", offset=(0, 0),
                                     check_ui_blockers=False)
        controller.human_delay(0.3, 0.6)


def _empty_pouches(ctx):
    for pouch in _pouch_items():
        controller.click_menu_action(ctx.ser, pouch, "Empty", offset=(0, 0),
                                     check_ui_blockers=False)
        controller.human_delay(0.3, 0.6)


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
    if data.get("gotr_enter_clicks", 0) >= ENTER_MAX_CLICKS:
        ctx.state.trigger_safety_stop("gotr: could not pass the entry barrier")
        return
    barrier_obj = None
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
    controller.human_delay(1.5, 2.5)


def between_rounds_phase(ctx, st):
    """waiting/complete/idle: repair pouches, restock cells, deposit strays, idle.
    One action per tick, priority-ordered."""
    data = ctx.state.data
    cfg = ctx.state.config
    inv = st.get("inventory") or {}

    if cfg.get("repair_pouches"):
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
            and inv.get("emptySlots", 0) > 0
            and data.get("gotr_restock_clicks", 0) < RESTOCK_MAX_CLICKS):
        _restock_cells(ctx)
        return

    if _runes_in_inventory():
        _deposit_runes(ctx)
        return

    controller.human_delay(1.0, 2.5)


def _repair_pouches(ctx):
    """NPC Contact -> Dark Mage -> 'repair' dialogue option. CONTACT_MAX_CASTS is a
    give-up (degraded pouches still work at reduced capacity), never a safety stop."""
    data = ctx.state.data
    d = _dialogue()
    if _dialogue_open(d):
        if not _choose_option(ctx, d, ("repair",)):
            controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.5, 0.9)
        return

    if time.time() - data.get("gotr_contact_cast_at", 0) < CONTACT_RESOLVE_S:
        mage = _find_dark_mage_widget()
        if mage is not None:
            controller.click_entity(ctx.ser, mage, offset=(0, 0),
                                    check_ui_blockers=False)
            controller.human_delay(1.2, 1.8)
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
            debug_log.log("gotr", "giving up on pouch repair this window")
            data["gotr_contact_casts"] = CONTACT_MAX_CASTS
        return
    data["gotr_contact_stuck"] = 0
    data["gotr_contact_casts"] = data.get("gotr_contact_casts", 0) + 1
    data["gotr_contact_cast_at"] = time.time()
    debug_log.log("gotr", "casting NPC Contact to repair pouches")
    controller.click_entity(ctx.ser, spell, offset=(0, 0), check_ui_blockers=False)
    controller.human_delay(1.2, 1.8)


def _restock_cells(ctx):
    data = ctx.state.data
    table = _find_object_by_name(UNCHARGED_CELLS_NAME, exact=True)
    if table is None:
        debug_log.log("gotr", "uncharged-cells table not visible")
        return
    reach = travel.can_reach(table)
    if reach is False:
        travel.walk_to(ctx, table)
        return
    data["gotr_restock_clicks"] = data.get("gotr_restock_clicks", 0) + 1
    _click_escalating(ctx, table, (TAKE_ACTION,))
    controller.human_delay(0.8, 1.4)


def _deposit_runes(ctx):
    pool = _find_object_by_name(DEPOSIT_POOL_NAME, exact=True)
    if pool is None:
        debug_log.log("gotr", "deposit pool not visible")
        return
    reach = travel.can_reach(pool)
    if reach is False:
        travel.walk_to(ctx, pool)
        return
    _click_escalating(ctx, pool, (DEPOSIT_ACTION,))
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
    if _approach_or_click(ctx, obj, CELL_TILE_REPAIR_OPTIONS, "gotr_repair_clicks",
                          CELL_MAX_CLICKS, "barrier repair never registered"):
        controller.mark_busy(2.0)
        controller.human_delay(0.8, 1.4)


def spend_stones_phase(ctx, st):
    """Use held guardian stones on the Great Guardian (2-3 energy each, 5 RC xp)."""
    data = ctx.state.data
    guardian = _find_npc_by_name(GREAT_GUARDIAN_NAME)
    if guardian is None:
        miss = data.get("gotr_guardian_missing", 0) + 1
        data["gotr_guardian_missing"] = miss
        debug_log.log("gotr", f"Great Guardian not visible ({miss}x)")
        if miss >= GUARDIAN_MISSING_LIMIT:
            ctx.state.trigger_safety_stop("gotr: the Great Guardian is never visible")
        return
    data["gotr_guardian_missing"] = 0
    if _approach_or_click(ctx, guardian, (POWER_UP_ACTION,), "gotr_powerup_clicks",
                          POWERUP_MAX_CLICKS, "power-up never registered"):
        controller.mark_busy(2.0)
        controller.human_delay(0.8, 1.4)


def place_cell_phase(ctx, st):
    """Spend the charged cell: place on an INACTIVE tile, else recharge the weakest."""
    data = ctx.state.data
    tile = choose_cell_tile(st.get("barriers") or [])
    if tile is None:
        debug_log.log("gotr", "no usable cell tile visible -- deferring cell placement")
        data["gotr_cell_block_until"] = time.time() + ACTION_BLOCK_S
        return
    loc = tile.get("location") or {}
    obj = _object_at_tile(loc.get("x"), loc.get("y"))
    if obj is None:
        if not data.get("gotr_no_cell_objects"):
            debug_log.log("gotr", "cell tile not present in /objects -- disabling cell "
                                  "actions (GroundObject gap, see spec checklist)")
        data["gotr_no_cell_objects"] = True
        return
    if _approach_or_click(ctx, obj, CELL_TILE_PLACE_OPTIONS, "gotr_cell_clicks",
                          CELL_MAX_CLICKS, "charged cell never consumed"):
        controller.mark_busy(2.0)
        controller.human_delay(0.8, 1.4)


def altar_trip_phase(ctx, st):
    """Outside: enter the active portal guardian for the energy type we have less of.
    Inside (currentAltar set): _work_altar imbues, empties pouches, and exits."""
    data = ctx.state.data
    if (st.get("currentAltar") or "UNKNOWN") != "UNKNOWN":
        _work_altar(ctx, st)
        return

    rotation = st.get("altarRotationSecondsRemaining", -1)
    if 0 <= rotation < ALTAR_MIN_SECONDS:
        debug_log.log("gotr", "altar pair about to rotate -- waiting to enter")
        controller.human_delay(1.0, 2.0)
        return

    cfg = ctx.state.config
    altar = choose_altar(st, _rc_level(), tuple(cfg.get("blocked_altars") or ()))
    if altar is None:
        debug_log.log("gotr", "no enterable active altar -- deferring altar trips")
        data["gotr_altar_block_until"] = time.time() + ACTION_BLOCK_S
        return

    if data.get("gotr_altar_enters", 0) >= ALTAR_ENTER_MAX:
        ctx.state.trigger_safety_stop("gotr: altar portal never entered")
        return
    portal = _find_object_by_name(PORTAL_GUARDIAN_PREFIX + altar.lower(),
                                  action=PORTAL_GUARDIAN_ACTION)
    if portal is None:
        # Not a stop: the pair rotates on its own. Defer and let the ladder re-pick.
        debug_log.log("gotr", f"portal guardian for {altar} not visible -- deferring")
        data["gotr_altar_block_until"] = time.time() + ACTION_BLOCK_S
        return
    reach = travel.can_reach(portal)
    if reach is False:
        travel.walk_to(ctx, portal)
        return
    data["gotr_altar_enters"] = data.get("gotr_altar_enters", 0) + 1
    debug_log.log("gotr", f"entering the {altar} portal")
    _click_escalating(ctx, portal, (PORTAL_GUARDIAN_ACTION,))
    controller.mark_busy(3.0)
    controller.human_delay(1.5, 2.5)


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
        xp_before = (controller.get_player() or {}).get("runecraftXp")
        data["gotr_imbue_clicks"] = data.get("gotr_imbue_clicks", 0) + 1
        _click_escalating(ctx, altar_obj, (ALTAR_IMBUE_ACTION,))
        controller.mark_busy(2.5)
        _await_xp("runecraftXp", xp_before, IMBUE_CONFIRM_S)
        return

    if pouch_stored(read_pouches()) > 0:
        _empty_pouches(ctx)
        return

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
    data["gotr_exit_clicks"] = data.get("gotr_exit_clicks", 0) + 1
    _click_escalating(ctx, exit_portal, EXIT_PORTAL_ACTIONS)
    controller.mark_busy(3.0)
    controller.human_delay(1.2, 2.0)


def portal_trip_phase(ctx, st):
    """The huge-remains portal: enter while open, mine guardian essence inside, fill
    pouches, and leave when full (or the trip ceiling forces an exit)."""
    data = ctx.state.data
    inv = st.get("inventory") or {}

    if st.get("miningArea") == "WEST_HUGE_REMAINS":
        data["gotr_portal_ticks"] = data.get("gotr_portal_ticks", 0) + 1
        space = pouch_free_space(read_pouches())
        if inv.get("guardianEssence", 0) > 0 and space is not None and space > 0:
            _fill_pouches(ctx)
            return
        full = inv.get("emptySlots", 0) == 0 and (space is None or space == 0)
        if full or data["gotr_portal_ticks"] >= PORTAL_TRIP_MAX_TICKS:
            exit_portal = _find_object_by_name(PORTAL_NAME, exact=True)
            if exit_portal is None:
                debug_log.log("gotr", "exit portal not visible in the huge-remains pocket")
                return
            _click_escalating(ctx, exit_portal, EXIT_PORTAL_ACTIONS)
            controller.mark_busy(3.0)
            controller.human_delay(1.2, 2.0)
            return
        if time.time() - data.get("gotr_mine_delta_at", 0) < MINE_RECLICK_S:
            return    # actively mining -- leave it alone
        node = _find_object_by_name(HUGE_REMAINS_NAME, action=MINE_ACTION, exact=True)
        if node is None:
            debug_log.log("gotr", "huge remains not visible inside the pocket")
            return
        _click_escalating(ctx, node, (MINE_ACTION,))
        controller.mark_busy(2.0)
        controller.human_delay(1.0, 1.8)
        return

    # Outside: enter the portal while the HUD says it is open.
    if data.get("gotr_portal_enters", 0) >= ALTAR_ENTER_MAX:
        debug_log.log("gotr", "portal never entered -- giving up on this spawn")
        data["gotr_portal_block_until"] = time.time() + ACTION_BLOCK_S
        data["gotr_portal_enters"] = 0
        return
    portal = _find_object_by_name(PORTAL_NAME, action=PORTAL_GUARDIAN_ACTION,
                                  exact=True)
    if portal is None:
        debug_log.log("gotr", "huge-remains portal not visible -- waiting")
        controller.human_delay(0.8, 1.4)
        return
    reach = travel.can_reach(portal)
    if reach is False:
        travel.walk_to(ctx, portal)
        return
    data["gotr_portal_enters"] = data.get("gotr_portal_enters", 0) + 1
    _click_escalating(ctx, portal, (PORTAL_GUARDIAN_ACTION,))
    controller.mark_busy(3.0)
    controller.human_delay(1.5, 2.5)


def craft_phase(ctx, st):
    """The Workbench pass: deposit runes, fill pouches from loose essence, then keep
    the bench working fragments -> essence. Progress deltas are detected in
    _reset_counters, which stamps gotr_craft_delta_at and zeroes the stall counter."""
    data = ctx.state.data
    if _runes_in_inventory():
        _deposit_runes(ctx)
        return
    inv = st.get("inventory") or {}
    if inv.get("guardianEssence", 0) > 0:
        space = pouch_free_space(read_pouches())
        if space is not None and space > 0:
            _fill_pouches(ctx)
            return
    if time.time() - data.get("gotr_craft_delta_at", 0) < CRAFT_RECLICK_S:
        return    # crafting is progressing -- let it run
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
    if reach is False:
        travel.walk_to(ctx, bench)
        return
    data["gotr_craft_stall"] = data.get("gotr_craft_stall", 0) + 1
    _click_escalating(ctx, bench, (WORKBENCH_ACTION,))
    controller.mark_busy(3.0)
    controller.human_delay(2.0, 3.0)


def mine_phase(ctx, st):
    """Mine guardian remains/parts at the temple edges (or the large-remains pocket
    behind the rubble when configured). Deltas detected in _reset_counters."""
    data = ctx.state.data
    cfg = ctx.state.config
    if time.time() - data.get("gotr_mine_delta_at", 0) < MINE_RECLICK_S:
        return    # actively mining
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
        if cfg.get("use_large_remains"):
            rubble = _find_object_by_name(RUBBLE_NAME, action=RUBBLE_ACTION, exact=True)
            if rubble is not None and travel.can_reach(rubble) is not False:
                _click_escalating(ctx, rubble, (RUBBLE_ACTION,))
                controller.mark_busy(3.0)
                controller.human_delay(1.5, 2.5)
                return
        travel.walk_to(ctx, node)
        return
    data["gotr_mine_stall"] = data.get("gotr_mine_stall", 0) + 1
    _click_escalating(ctx, node, (MINE_ACTION,))
    controller.mark_busy(2.0)
    controller.human_delay(1.0, 1.8)
