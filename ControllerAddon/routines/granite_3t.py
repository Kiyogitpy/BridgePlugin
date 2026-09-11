"""3-tick granite mining ("3t4g") at the Cape Conch mine.

Design: docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md. THE CYCLE BELOW IS
MEASURED FROM USER'S OWN RECORDINGS (2026-09-09, recordings/2026-09-09_081631_simple3t and
_082746_simple3t-take2), not from the wiki's prose; see CYCLE_TICKS for the tick-by-tick facts.

THE SHAPE, and why it is not gathering.py. Every other routine here is reactive: click, wait on
a pushed signal, confirm over HTTP. This one is a SCHEDULE. Per cycle, with the critical tick
numbered T and the next one pinned at T + CYCLE_TICKS:

    dead time        resolve the NEXT rock's screen point over HTTP (skipping a depleted one);
                     select the knife; fly to the log and PARK the cursor there
    tick T (crit)    click IN PLACE on the log (no flight) -> knife-on-log starts the fletch and
                     sets the skilling timer; ONE short flight to the rock; ONE click, in the
                     SAME client tick -> the character walks to the rock, the fletch is
                     displaced, and the mine rolls three ticks after the fletch
    tick T+3         the Mining xp arrives with this tick's packet; this is the next critical
                     tick, and the next knife-on-log is clicked inside it, after the packet

Two clicks in one 600ms tick, but only ONE flight, because item selection in OSRS is
client-side: clicking the knife sends no packet, so the selection and the flight to the log are
both paid during the dead ticks. A cycle whose rock click slips into the next tick pays at
FOUR ticks (measured) -- it never loses the log, so there is no abandon path anywhere here.

THE CYCLE IS COUNTED IN TICKS, NOT INFERRED. `run_cycle` records the bridge tick number its
log click landed on and the next cycle targets `that + CYCLE_TICKS` through `tick_cycle.at_tick`.
"""
import math
import os
import random
import sys
import time

# Run as a SCRIPT (`python routines/granite_3t.py`, the probe) and this file's directory is
# sys.path[0], so ControllerAddon/ itself is not importable and `import controller` dies. The
# engine loads this module by path with ControllerAddon/ already on the path, where this is a
# no-op. MEASURED live 2026-09-08: the probe was the first thing run at the mine and it
# traceback'd on the import before printing a single rock.
if __package__ in (None, "") and os.path.dirname(__file__) == os.path.abspath(sys.path[0]):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import banker
import controller
import debug_log
import tick_cycle
import travel
import worldmap

CATEGORY = "granite3t"

# The whole geometry is USER's own camera: zoomed in, tilted, the rocks beside the inventory.
# The engine's login camera reset (overhead + zoom out) would undo it; see controller.main.
KEEP_CAMERA = True

DEFAULT_CONFIG = {
    "enabled": True,

    # WHICH MINE. Everything site-specific below (rock ids, rotation, home, bank, what gets
    # mined) is overridden per site from SITES by get_config; the values that follow are the
    # Cape Conch ones and double as the fixed-rotation reference.
    "site": "cape_conch",

    # THE EASTERN 2x2 CLUSTER AT CAPE CONCH, IN ROW-ALTERNATING ORDER -- USER's own rotation,
    # read off both 2026-09-09 recordings. The stand tiles are (3255,2353) and (3255,2354); a
    # rock on the OTHER row from where the player stands is diagonal, and the game steps the
    # player onto that row to mine it, which is what buys the rock's second roll ("two rolls
    # for resources if moving up to them", wiki). Alternating rows every cycle makes every
    # transition a step. A cardinally adjacent rock also paid at 3 ticks in the recording
    # (tick 72160), so the step is about yield, not about the cycle working at all.
    #
    # Four rocks against a 5s respawn: a 1.8s cycle returns to a rock after 7.2s.
    # USER 2026-09-09, back at Cape Conch after the gem mine's long walks: "there is 4 granite
    # right next to me only mine these 4 ... make sure we always move 1 tile to mine". The
    # western line: three rocks in the row north of the stand tile (3248,2349) and one two
    # tiles east. Three of them are adjacent from that tile, so an ORDER cannot force the step
    # by itself -- forceStep below does: the rotation skips any rock adjacent to the tile the
    # player is standing on, and from every stand tile here at least one rock is 2-3 away.
    # USER's own order, recorded 2026-09-09 (recordings/2026-09-09_103509_order): B -> D -> A
    # -> C. The recording also shows WHY it always moves: the game walks to a tile CARDINALLY
    # beside a rock before mining it (diagonal is not enough -- the player stepped from
    # (3249,2349) to (3248,2349) for B and from (3247,2349) two tiles to (3249,2349) for C), so
    # in this order the next rock is never the one straight north of the stand tile. Strict
    # order, no forceStep: the order itself is the guarantee.
    "rockTiles": [[3248, 2350, 0], [3250, 2349, 0], [3247, 2350, 0], [3249, 2350, 0]],
    "forceStep": False,
    # THE REAL 3T4G DROP (wiki, Tick manipulation): "starting the 3-tick action and clicking to
    # move to the next rock, then both quickly dropping the ore and clicking the rock again on
    # the same tick". The ore from the PREVIOUS cycle (it arrived with the xp) is shift-dropped
    # in the tick after the fletch, while the character is walking, and the rock is clicked
    # again in that same tick because the drop interrupts the queued mine. False falls back to
    # dropping a full inventory with drop_burst. USER 2026-09-09: "is this not feasible?"
    # OFF after measuring it: with the drop + re-click, 27 of 43 cycles paid at 3 (g3t_live42/46)
    # against 16 of 17 without (g3t_live39/40), and a lost first roll costs 4 ticks where a full
    # inventory sweep costs ~3.5s per 26 cycles. The failing cycles' timings did not separate
    # from the good ones in the log; the suspect is a re-click processed in the roll's own tick,
    # which starts a fresh mine on the pickaxe timer (exactly the "paid 7" seen). Left in as a
    # switch for another look with the recorder alongside.
    "dropBetweenMines": False,
    # Whether the rock is clicked again after that drop. The wiki says yes; MEASURED g3t_live42
    # the re-click landing late in its tick cost the 3-tick roll on ~a third of cycles (16 of 26
    # paid at 3 against 12 of 13 without the per-cycle drop). EXPERIMENT 2026-09-09: off, to
    # learn whether a drop interrupts the queued mine at all. ANSWERED g3t_live43: it does --
    # without the re-click the mines restarted on the pickaxe timer (10 pays landed 2 ticks
    # into the NEXT cycle) and three rocks never paid. So the re-click stays, and the cursor is
    # parked on the ore straight after the rock press so the drop is an in-place click.
    "dropReclick": True,
    # The drop clicks are PLAIN left clicks: USER 2026-09-09 swapped the granite's left click
    # to Drop with the Menu Entry Swapper after a shift-click came out as "Use" (the game's
    # own shift-click-drop setting is not what this account relies on). True holds SHIFT.
    "dropWithShift": False,
    # Granite rocks share one object id per variant; the probe prints it alongside each tile.
    # get_object_at needs it, and matching by id rather than name is what keeps a DEPLETED rock
    # (a different id, offering no actions) from resolving as a live one.
    "rockIds": [11387],         # MEASURED live 2026-09-08 at Cape Conch
    "rockName": "granite rocks",    # what the probe lists
    # A site whose rockTiles is EMPTY runs the DYNAMIC rotation: the nearest live rock each
    # cycle, from a /objects scan of this radius. A rock further than stepMaxTiles from the
    # player cannot be mined in the same tick as the fletch (the walk takes more than one step),
    # so it is walked to first and the fletch starts one step out -- the guide's own rule.
    "scanRadius": 12,
    "stepMaxTiles": 2,
    # A region the player must be in, or the run stops before clicking anything. None = no gate.
    "regionId": None,
    # What THIS ACTIVITY produces: safe to find in the inventory at startup, and what a full
    # inventory drops or deposits.
    "minedItems": ["granite", "sandstone"],

    "knifeName": "knife",
    "logName": "teak logs",
    # Never dropped, and allowed in the inventory at startup. gathering.py's mining profile
    # keeps "pickaxe" for the same reason: nothing here checks whether the pickaxe is worn or
    # carried, and a carried one binned on the first full inventory ends the run ~60s later with
    # a stall message naming the wrong cause. "log basket" is here because USER carries one as a
    # spare-log safety net (2026-09-08). Note the basket does NOT feed the routine -- nothing
    # refills the loose log from it, so an emptied log slot is still a stop, by design.
    "keepItems": ["pickaxe", "log basket", "gem bag", "gem sack"],

    # WHERE IN THE CRITICAL TICK THE LOG CLICK LANDS, drawn per cycle from this range (ms after
    # the bridge's tick anchor). Two constraints set it. The floor: a fletch processed one tick
    # EARLY -- while the previous mine's timer is still positive -- cancels the mine and lets the
    # fletch complete and eat the log, and the 2026-09-08 attempt at 25ms did exactly that
    # (the bridge anchors on GameTick, ~20ms after the packet, and the packet itself jitters).
    # The ceiling: the rock click has to be PRESSED before the tick ends, and the flight from
    # the log slot to the furthest rock costs ~200ms at flightSpeedScale 0.45. USER's own
    # successful fletches landed anywhere from 21ms to 582ms in; the bot has no reason to be
    # near either edge.
    "fletchPhaseMs": [150, 320],

    # An OPTIONAL extra-strict cap on the reach. The real limit is DERIVED from the tick budget
    # in check_geometry (tick_cycle.max_reach_px); this only ever tightens it, never loosens it.
    # None disables the cap.
    "maxReachPx": None,

    # Flight-time multiplier for the two clicks this routine makes, passed straight through to
    # move_to_human's duration_scale -- see tick_cycle.PRACTISED_FLIGHT_SCALE for why a
    # repeated fixed-target reach is legitimately faster than settings.HM2_*'s general fit.
    # MEASURED LIVE 2026-09-08: the rocks sit 157-234px from the log slot at USER's layout.
    "flightSpeedScale": 0.22,
    # Deliberate imperfection: a drawn fraction of cycles run one tick late (paying at 4, which
    # is what USER's own slower take looks like). A machine that never misses a 3-tick cycle is
    # itself the signature -- EVENT_MOUSE_CLICK_V2 carries inter-press timing to the server at
    # 1ms resolution (CLAUDE.md, telemetry).
    # OFF (2026-09-09): the xp-driven cycle already varies on its own -- roll failures pay at 7,
    # inventories drop, re-entries realign -- and the deliberate one-tick pause was visible
    # (USER: "it pauses briefly") and paid 7 on the cycle after it once in two.
    "missChance": 0.0,

    # THE GREAT CONCH BANK, ~381 map steps NW of the mine (bank chest 57958 open / 57959
    # closed, options Bank+Collect; wiki "Bank chest (The Great Conch)"). A WORKING cycle never
    # consumes a log, so this is paid only after mistakes -- if it starts recurring, cycles are
    # not displacing the fletch and the per-cycle outcome lines are where to look.
    # STAND OUTSIDE THE BANK, not on it. MEASURED LIVE 2026-09-08: the bank building's interior
    # is an ISOLATED POCKET on our static collision map -- find_path from every inside tile to
    # the mine answers None, while (3182,2422) answers 381 steps. Same family as the enclosed
    # components CLAUDE.md records for Ferox Enclave. The chests are 3-6 tiles from here, which
    # open_bank handles.
    "bankTile": [3182, 2422, 0],
    # Where the rotation is worked from, and where a bank trip returns to. From CONFIG rather
    # than "wherever the run started": a routine restarted at the bank would otherwise adopt the
    # bank as home and never walk back to the rocks.
    "mineTile": [3248, 2349, 0],
    "restockLogs": 27,          # one loose + 26 for the basket

    # "drop" (shift-drop everything not kept) or "deposit" (walk to depositTile, Deposit-All
    # each stack, Empty the gem bag, close, resume).
    "fullInventory": "drop",
    "depositTile": None,
    "depositId": None,
    "xpStallStopS": 60,

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 20,
        "everyMaxMinutes": 40,
        "durationMinMinutes": 2,
        "durationMaxMinutes": 8,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        # OFF: this routine drops a full inventory itself. check_safety runs BEFORE tick() and
        # would otherwise win the race -- the same reason gathering.get_config forces it off.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

# Per-site overrides, merged over DEFAULT_CONFIG by get_config. An EMPTY rockTiles means the
# dynamic rotation.
SITES = {
    # The fixed 2x2 rotation the defaults above describe.
    "cape_conch": {},
    # THE UNDERGROUND SHILO VILLAGE GEM MINE (USER 2026-09-09: "swapping place to gem mine since
    # it will make us have to drop less"). 48 gem rocks (ids 11380/11381, cache `gemrock1`/
    # `gemrock`, op1 Mine) on a 59.4s respawn, so a fixed four-rock rotation is impossible here
    # and the routine roams: nearest live rock each cycle, the previous tile excluded so every
    # cycle steps. An OPEN gem bag absorbs sapphire and better (60 each); opal, jade and topaz
    # (105/128 of successes, wiki) still land loose, so the inventory fills every ~40 cycles and
    # goes to the mine's own Bank Deposit Chest (loc 10530 at (2842,9382), MEASURED by
    # karambwan_fishing 2026-09-06) rather than the floor. There is no bank to restock logs from
    # -- a lost log is a stop here, so the guide's advice stands: an ADAMANT pickaxe, whose
    # 4-tick roll lets a broken cycle right itself.
    "gem_mine": {
        "rockTiles": [],
        "rockIds": [11380, 11381],
        "rockName": "gem rocks",
        "regionId": 11410,
        "mineTile": None,
        "bankTile": None,
        "minedItems": ["uncut", "clue geode"],
        # "gem sack" too: USER's is the upgraded sack ("Open gem sack" on /inventory, MEASURED
        # live 2026-09-09), and a keep list naming only the bag would have deposited it.
        "keepItems": ["pickaxe", "log basket", "gem bag", "gem sack"],
        "fullInventory": "deposit",
        "depositTile": [2842, 9382, 0],
        "depositId": 10530,
    },
}


def site_config(cfg):
    merged = dict(cfg)
    merged.update(SITES.get(cfg.get("site")) or {})
    return merged


# --------------------------------------------------------------------------------------------
# The cycle
# --------------------------------------------------------------------------------------------

# THREE, MEASURED FROM USER'S OWN RECORDINGS (2026-09-09, simple3t and simple3t-take2), and
# the number is about WHEN THE XP LANDS, not about the name of the method:
#
#   * A knife-on-log clicked during client tick N pays its Mining xp with the packet that OPENS
#     tick N+3, every time the cycle works.
#   * The next knife-on-log therefore belongs in tick N+3 -- after that packet, never before
#     it. The wiki: the fletch "sets the timer to three if the timer is not positive"; a fletch
#     processed one tick early lands while the mine's timer is still running, cancels the mine
#     (no xp) and then runs to completion (the log becomes a Teak stock). That is exactly what
#     the CYCLE_TICKS = 3 attempt of 2026-09-08 did, because it clicked the log 25ms after the
#     bridge's tick anchor -- inside the tick model's jitter.
#   * USER's take 1 clicked every fletch in tick N+4 (their own "my 3 tick is a bit too slow"),
#     and a previous commit fit CYCLE_TICKS = 4 to that. Take 2 has real 3-tick cycles.
CYCLE_TICKS = 3

# THE NEXT ROCK IS NOT CLICKED UNTIL THE MINING XP HAS LANDED (USER 2026-09-09: "lets not click
# another rock until we get a xp notifier"). USER's own slow full run (recordings/2026-09-09_
# 092238_simple3t-full-run-slow) is exactly that shape and paid at 3 ticks in 25 of 29 cycles:
# fletch + rock in one tick, wait for the drop, then the next pair. So the cycle is no longer
# PINNED to a tick count; it is driven by the XP_GAINED event, and CYCLE_TICKS above is what a
# paying cycle measures, not a schedule. A roll that fails is not a failure of ours -- the
# pickaxe keeps rolling every four ticks (adamant, the guide's choice for that reason) and the
# xp lands later; only a mine that has paid nothing for XP_WAIT_MAX_TICKS is treated as not
# running at all and re-clicked.
XP_WAIT_MAX_TICKS = 14
# Reaction between the xp drop and the next fletch. Short on purpose: the xp EVENT proves the
# boundary has passed (the early-fletch hazard of 2026-09-08 was a click sent BEFORE it), and
# every ms spent here is a ms the rock flight does not have -- MEASURED g3t_live33, a 140-320ms
# reaction plus a 360-780ms flight put the rock press in the NEXT tick on 5 of 6 cycles, and
# those paid at 4-12 ticks on the pickaxe's own timer instead of 3. MEASURED g3t_live34: a
# press at client phase 546-624 STILL paid at 4-8 -- the upload latency puts the last ~60ms of
# the client tick into the server's next one, so the press has to land by ~450ms.
# NOT ZERO. MEASURED g3t_live54: the three cycles that fletched 21-23ms after the xp packet
# all paid at 7, and every one of the 13 that fletched at 40ms or later paid at 3 -- a fletch
# sent in the same breath as the packet that carried the drop does not take. USER: "is it
# because we aren't waiting for a new tick?" -- in effect, yes.
XP_REACTION_MS = (45, 95)
# A fire with no drop to react to (chain start, a resync, a mine that never paid) is aligned to
# the next boundary plus this, instead of landing wherever the loop happened to be. MEASURED
# g3t_live49 recorder: the two re-entries fletched at phase 341 and 540 and both missed, while
# the 22 paying cycles all fletched at 101-241 with the rock press by ~500.
REENTRY_PHASE_MS = (40, 90)
LATE_PAY_REST_TICKS = 0     # the "stop for one tick" theory was built on the off-by-one above

# THE MINE CLICK LANDS IN THE SAME CLIENT TICK AS THE FLETCH, and that is measured too: every
# same-tick pair in take 2 paid at 3 ticks, and both pairs whose Mine slipped into the next tick
# paid at 4. A 4-tick cycle is the worst a slow flight costs -- never a log -- which is why
# there is no "abandon the rock click" branch anywhere below. Fire, always.
#
# What the recorder says about press vs release (checklist item 6, settled): a WORLD click fires
# its menu action 1-23ms after mouse DOWN; an INVENTORY click fires ~10ms after mouse UP (the
# client waits for the release to tell a click from a drag). So the log click costs its whole
# hold, and the rock click only has to be PRESSED inside the tick.

# The knife and the log are clicked thousands of times an hour at points that are constant for
# a whole chain. aim_radius=0 makes pick_aim_point short-circuit and land on the IDENTICAL pixel
# every time -- CLAUDE.md's fidget-anchor lesson is explicit that an exact repeated coordinate is
# a tell however human the timing is, and EVENT_MOUSE_MOVE puts the whole trajectory on the wire
# at 20Hz whether a click happens or not. An inventory slot is 36x32, so 5px costs nothing.
AIM_RADIUS_PX = 5

# tick() is not called during a scheduled break, an F2 pause, a window unfocus or a reconnect
# wait, so no Mining xp can arrive -- and the XP stall watch is wall-clock. A gap longer than
# this stands the stall clock down instead of stopping a perfectly healthy run with a message
# that is simply false. wintertodt.py stands its watch down between rounds for the same reason.
TICK_GAP_RESEED_S = 5.0

# setup() runs BEFORE the engine's login and focus gates (controller.py: setup at 3539,
# is_fully_logged_in at 3552, windowFocused at 3558), so at the login screen /inventory answers
# [] and no TICK packet has arrived yet. The startup assertions are therefore DEFERRED into
# tick() and retried; this bounds the deferral so a genuinely misconfigured run does not idle
# forever.
STARTUP_DEFER_MAX_S = 60.0

# controller.get_inventory() returns [] on ANY bridge error, so an empty read means "cannot
# tell", never "the items are gone". A 200ms hiccup, a client GC pause or a LOADING screen must
# cost latency, not the run (CLAUDE.md's standing rule: network drops are ridden out).
EMPTY_INVENTORY_STOP = 20

# The two once-per-chain gates below `_run_chain`'s inventory read both return from tick()
# WITHOUT running a cycle, and both sit BEFORE _stall_check -- so a gate that never passes is a
# silent forever-loop with one debug line per engine iteration and no terminal condition at all
# (the XP stall watch is never reached to notice that no xp is arriving). Same shape and same
# answer as EMPTY_INVENTORY_STOP: ride out a transient, stop on a persistent one.
#
# Neither failure is exotic. open_inventory_tab reads /state's `inventoryTabOpen`, which is None
# -> False whenever the bridge read fails; and controller.get_hover() returns None on a bridge
# error AND on any client built without MenuStateProvider -- so on a client that has not been
# rebuilt the hover gate failing is the DEFAULT outcome, and this routine needs a rebuild anyway
# for its Routine-dropdown entry.
INVENTORY_TAB_STOP = 15
KNIFE_HOVER_STOP = 15

# Per-cycle outcome bookkeeping: every OUTCOME_SUMMARY_EVERY cycles one line says how many paid
# at 3 ticks, at 4, and not at all -- the same three numbers the analysis of USER's recordings
# was judged by, so a live run can be read the same way.
OUTCOME_SUMMARY_EVERY = 25


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def get_config(bridge_config):
    """DEFAULT_CONFIG with the chosen site's overrides -- this routine has no ControllerConfig
    section, same as gemstone_crab.py and pest_control.py."""
    return site_config(DEFAULT_CONFIG)


def rock_ids(cfg):
    ids = cfg.get("rockIds")
    if not ids and cfg.get("rockId") is not None:
        ids = [cfg["rockId"]]
    return set(ids or [])


def dynamic_rotation(cfg):
    """True when rockTiles is empty: pick the nearest live rock each cycle instead of walking a
    fixed list."""
    return not (cfg.get("rockTiles") or [])


def find_item(inv, name_substring):
    needle = (name_substring or "").lower()
    for item in inv or []:
        if needle in (item.get("name") or "").lower():
            return item
    return None


def log_count(inv, name_substring):
    needle = (name_substring or "").lower()
    return sum(1 for item in (inv or []) if needle in (item.get("name") or "").lower())


def keep_names(cfg):
    """Every substring the drop must never match: the knife, the log, and `keepItems`."""
    keep = [(cfg.get("knifeName") or "").lower(), (cfg.get("logName") or "").lower()]
    keep += [(k or "").lower() for k in (cfg.get("keepItems") or [])]
    return tuple(k for k in keep if k)


def byproduct_names(cfg):
    """Everything THIS ACTIVITY produces, which is therefore safe to drop and safe to find in
    the inventory at startup.

    MEASURED LIVE 2026-09-08: the startup guard refused to start over a Sandstone (2kg) and a
    Teak stock. Both are this routine's own litter -- the quarry drops sandstone alongside
    granite, and a Teak stock is what an UNINTERRUPTED fletch leaves behind when a cycle
    overruns. Refusing to start over the debris of the previous run is the guard being right
    about the rule and wrong about the case; it exists to protect a carried pickaxe or a
    teleport tablet, not to demand a spotless inventory."""
    fletch_byproduct = (cfg["logName"] or "").lower().replace(" logs", "").replace(" log", "")
    mined = [(m or "").lower() for m in (cfg.get("minedItems") or []) if m]
    return mined + ([f"{fletch_byproduct} stock"] if fletch_byproduct else [])


def check_inventory_is_clean(cfg, inv):
    """(ok, message). Refuse to start when the inventory holds anything the drop would bin.

    The keep list alone is not enough protection: it only knows the names it was told about, and
    the failure it guards is silent and delayed -- the item is dropped on the floor of a busy
    mine about a minute in, mining then earns nothing, and the XP stall watch stops the run 60s
    later blaming contested rocks. Naming the offending items and refusing is the honest
    response; `keepItems` is the escape hatch."""
    allowed = keep_names(cfg) + tuple(byproduct_names(cfg))
    unexpected = sorted({(item.get("name") or "?") for item in (inv or [])
                         if not any(a in (item.get("name") or "").lower() for a in allowed)})
    if unexpected:
        return False, ("the inventory holds items this routine would DROP on the first full "
                       f"inventory: {', '.join(unexpected)} -- bank them, or add them to "
                       "keepItems")
    return True, "inventory holds only the knife, the log, keepItems and this mine's byproducts"


def rock_points(cfg):
    """Live screen points for every configured rock tile, or None when any will not resolve.

    None means "cannot aim", never "no rock there" -- the caller refuses rather than guessing."""
    if dynamic_rotation(cfg):
        # The nearest few live rocks stand in for the rotation: the reach that matters is the
        # one the next cycles will actually make.
        rocks = live_rocks(cfg)[:4]
        return [(int(r["screenX"]), int(r["screenY"])) for r in rocks] or None
    points = []
    for tile in cfg.get("rockTiles") or []:
        point = resolve_rock(cfg, tile)
        if point is None:
            return None
        points.append(point)
    return points or None


def check_geometry(cfg, inv):
    """(ok, message). The message ALWAYS carries the measurement, pass or fail, so a refusal
    tells USER how much to zoom in rather than merely that something is wrong."""
    knife = find_item(inv, cfg["knifeName"])
    if knife is None:
        return False, f"no {cfg['knifeName']!r} in the inventory"

    logs = log_count(inv, cfg["logName"])
    if logs == 0 and find_item(inv, LOG_BASKET_NAME) is None:
        return False, (f"no {cfg['logName']!r} in the inventory and no log basket to draw a "
                       f"spare from")
    if logs == 0:
        return True, "no loose log yet -- will draw one from the log basket"
    if logs > 1:
        # NOT a refusal any more: ensure_one_loose_log drops back to one on the first tick, and
        # a restock deliberately comes home with a pile. MEASURED LIVE 2026-09-08 -- the first
        # bank trip returned with 26 loose logs and this guard stopped the run one basket-Fill
        # short of working. The rule it states is still true; it is just not a reason to refuse.
        return True, (f"{logs} x {cfg['logName']!r} loose -- will drop back to exactly one "
                      f"(more than one opens a Make-X interface and eats a tick)")

    log = find_item(inv, cfg["logName"])
    if log.get("screenX") is None:
        return False, f"{cfg['logName']!r} has no screen position (inventory tab closed?)"

    if not dynamic_rotation(cfg) and len(cfg.get("rockTiles") or []) < 2:
        return False, "fewer than 2 rockTiles configured -- the rotation needs the movement "\
                      "stall between rocks to buy the second roll"

    points = rock_points(cfg)
    if points is None:
        if dynamic_rotation(cfg):
            return False, f"no live {cfg.get('rockName', 'rock')} in view"
        return False, "at least one rock in rockTiles does not resolve on screen"

    reach = max(math.hypot(px - log["screenX"], py - log["screenY"]) for px, py in points)
    flight = tick_cycle.estimate_flight_ms(reach, cfg.get("flightSpeedScale") or 1.0)

    # The limit is DERIVED from the tick budget, not hand-picked. MEASURED LIVE 2026-09-08:
    # `maxReachPx` shipped at 220 while the arithmetic only allows 82, so the routine would
    # have accepted a geometry that could not hit a single tick. `maxReachPx` survives only as
    # an optional HARD CAP for a user who wants to be stricter than the budget -- never looser.
    # The rock click only has to be PRESSED inside the tick (a world click fires its menu action
    # 1-23ms after mouse DOWN -- MEASURED from USER's recordings 2026-09-09), so the budget is
    # the tick minus the latest fletch phase minus the in-place log click, and nothing for the
    # rock click's own hold.
    scale = cfg.get("flightSpeedScale") or 1.0
    phase_hi = float((cfg.get("fletchPhaseMs") or (150, 320))[1])
    budget = tick_cycle.GAME_TICK_MS - phase_hi - tick_cycle.CLICK_COST_MEDIAN_MS
    limit = tick_cycle.max_reach_px(budget, scale)
    cap = cfg.get("maxReachPx")
    if cap:
        limit = min(limit, cap)

    msg = (f"furthest rock is {reach:.0f}px from the log; the {tick_cycle.GAME_TICK_MS:.0f}ms "
           f"tick allows {limit:.0f}px ({budget:.0f}ms of flight), and this reach costs "
           f"~{flight:.0f}ms")
    if reach > limit and dynamic_rotation(cfg):
        # ADVISORY ONLY when roaming: the reach depends on where the player happens to stand
        # (MEASURED live 2026-09-09: 338px at one spot, 553px a few tiles away), a far rock is
        # walked to by far_rock_cycle, and a near rock past the budget merely pays at 4.
        return True, msg + " -- over budget from here; the nearest rocks still run, the rest are walked to"
    if reach > limit:
        return False, (msg + " -- SHRINK THE CLIENT WINDOW first (the dominant lever), then "
                             "zoom OUT so the whole rotation clusters tightly, then turn the "
                             "camera so the cluster sits nearest the inventory. Zooming IN "
                             "makes this worse: it magnifies the spread between the rocks, and "
                             "the furthest one sets this number.")
    return True, msg


def setup(ctx):
    """SETUP MUST NOT SAFETY-STOP. It runs BEFORE the engine's login and focus gates
    (controller.py: `routine.setup(ctx)` at 3539, `login.is_fully_logged_in()` at 3552,
    `windowFocused` at 3558), so at the login screen `/inventory` answers `[]` and no TICK
    packet has arrived -- BOTH startup assertions would fail and the run would be permanently
    stopped before it began. CLAUDE.md records the same bite for GOTR run 47 ("the first
    logged-in tick after an auto-login served an empty inventory").

    So all this does is record that the checks are owed; `_startup_check` runs them from tick()
    and retries until they can be ANSWERED."""
    ctx.state.data["g3t_startup_pending"] = True
    ctx.state.data["g3t_startup_since"] = _now()
    debug_log.log(CATEGORY, "startup checks deferred to the first tick (setup runs before the "
                            "engine's login and focus gates)")


# --------------------------------------------------------------------------------------------
# Keeping exactly ONE loose log in the inventory
# --------------------------------------------------------------------------------------------

LOG_BASKET_NAME = "log basket"
BASKET_EMPTY_OPTIONS = ("empty", "empty-to-bank")
BASKET_REFILL_BUDGET_S = 6.0
BASKET_REFILL_TRIES = 3


def loose_logs(inv, cfg):
    needle = (cfg["logName"] or "").lower()
    return [i for i in (inv or []) if needle in (i.get("name") or "").lower()]


def _basket_action(ctx, basket, options):
    """Take `options` on the basket. Empty is a SHIFT-CLICK on this account (USER 2026-09-09:
    "gotta shift click the log basket to empty it" -- a Menu Entry Swapper shift swap), and
    MEASURED live the same day the basket's plain menu is Cancel/Examine/Destroy/Open/Use/
    Check/Wear/Fill with NO Empty in it at all, so the menu is only the fallback for a client
    without that swap. Fill stays a menu selection."""
    if "empty" in options:
        controller.key_down(ctx.ser, "SHIFT")
        try:
            ok = controller.click_entity(ctx.ser, basket, offset=(0, 0), check_ui_blockers=False,
                                         simulate_mistakes=False)
        finally:
            controller.key_up(ctx.ser, "SHIFT")
        if ok:
            return True
    return controller.click_menu_action(ctx.ser, basket, options, offset=(0, 0),
                                        check_ui_blockers=False)


def _await(pred, budget_s):
    deadline = _now() + budget_s
    while _now() < deadline:
        if pred():
            return True
        _sleep(0.3)
    return pred()


def ensure_one_loose_log(ctx, cfg):
    """Keep EXACTLY ONE loose log in the inventory. True when that holds.

    THE MECHANIC, and it is the whole reason this routine works (USER, 2026-09-08): "we
    interrupt the fletching with another task therefore it doesnt get consumed". The log click
    STARTS a fletch, setting the skilling timer to 3; the rock click on the same tick starts
    mining, and per the wiki's own rule -- "by starting the timer on a different action, players
    clear any existing timer" -- that clears the pending fletch before it completes. No product,
    no consumption, one log forever.

    MEASURED the hard way the same day: on the first live run every cycle desynced at the
    room_for guard, so the rock click never fired, the fletch completed unopposed and the teak
    log became a Teak stock. I read that as "the log is consumed, this needs a supply loop" and
    was wrong -- consumption is the SYMPTOM of a cycle that failed to interrupt.

    So this runs only after a MISTAKE, which is exactly what the wiki means by carrying spares
    in a log basket.

    DRAWING ONE LOG OUT OF A BASKET TAKES FOUR STEPS, and the roundabout shape is the game's,
    not ours (USER): the basket has no "give me one" option, and Empty dumps ALL of them. So:
    empty the basket, drop exactly ONE log on the floor, FILL the basket again (which sweeps
    every remaining loose log back in), then pick the dropped one up. An earlier draft just
    dropped the 25 extras and left them to despawn -- this keeps the whole stack.
    """
    inv = controller.get_inventory() or []
    if not inv:
        return False                      # cannot tell; the caller's empty-read guard owns this

    logs = loose_logs(inv, cfg)
    if len(logs) == 1:
        return True

    basket = find_item(inv, LOG_BASKET_NAME)

    if len(logs) > 1:
        # More than one loose log opens a Make-X interface on the next knife-on-log and eats a
        # tick. Put them back in the basket rather than on the floor when we can.
        if basket is not None:
            debug_log.log(CATEGORY, f"{len(logs)} loose logs -- dropping one, refilling the "
                                    f"basket, then picking the dropped one back up")
            return _one_via_floor(ctx, cfg, basket, logs)
        debug_log.log(CATEGORY, f"{len(logs)} loose logs and no basket -- dropping the extras")
        controller.drop_items(ctx.ser, logs[1:])
        return len(loose_logs(controller.get_inventory() or [], cfg)) == 1

    if basket is None:
        return False                      # no loose log and nothing to draw on -- caller stops

    debug_log.log(CATEGORY, "out of loose logs -- emptying the log basket")
    if not _basket_action(ctx, basket, BASKET_EMPTY_OPTIONS):
        debug_log.log(CATEGORY, "could not select Empty on the log basket")
        return False
    if not _await(lambda: loose_logs(controller.get_inventory() or [], cfg),
                  BASKET_REFILL_BUDGET_S):
        debug_log.log(CATEGORY, "the basket produced no logs")
        return False

    inv = controller.get_inventory() or []
    basket = find_item(inv, LOG_BASKET_NAME)
    return _one_via_floor(ctx, cfg, basket, loose_logs(inv, cfg))


def _one_via_floor(ctx, cfg, basket, logs):
    """Drop one log, sweep the rest back into the basket, pick the dropped one up."""
    if not logs:
        return False
    dropped = logs[0]
    if not controller.drop_items(ctx.ser, [dropped]):
        debug_log.log(CATEGORY, "could not drop a log to the floor")
        return False

    if basket is not None and loose_logs(controller.get_inventory() or [], cfg):
        if _basket_action(ctx, basket, ("fill",)):
            _await(lambda: not loose_logs(controller.get_inventory() or [], cfg),
                   BASKET_REFILL_BUDGET_S)
        else:
            debug_log.log(CATEGORY, "could not Fill the basket -- the extras stay loose")

    # A FULL BASKET ABSORBS NOTHING, and without this that is an infinite loop: Fill succeeds,
    # takes zero logs, and we come back next tick with the same pile. Extra loose logs are not
    # merely untidy -- a second one opens Make-X on the next knife-on-log and eats a tick -- so
    # whatever the basket would not take goes on the floor.
    still = loose_logs(controller.get_inventory() or [], cfg)
    if len(still) > 1:
        debug_log.log(CATEGORY, f"the basket would not take {len(still) - 1} logs -- dropping "
                                f"them so knife-on-log cannot open Make-X")
        controller.drop_items(ctx.ser, still[1:])

    player = controller.get_player() or {}
    item = None
    for _ in range(3):
        item = controller.get_ground_item_at(player.get("x"), player.get("y"),
                                             player.get("plane", 0), dropped.get("id"))
        if item:
            break
        _sleep(0.3)
    if not item:
        debug_log.log(CATEGORY, "the dropped log is not on the ground yet")
        return False

    controller.click_menu_action(ctx.ser, item, ("take",), check_ui_blockers=False)
    _await(lambda: loose_logs(controller.get_inventory() or [], cfg), BASKET_REFILL_BUDGET_S)
    return len(loose_logs(controller.get_inventory() or [], cfg)) == 1



# --------------------------------------------------------------------------------------------
# Restocking logs from The Great Conch bank
# --------------------------------------------------------------------------------------------

BANK_ARRIVAL_TILES = 6
RESTOCK_MAX_TRIPS = 6


def _tile_of(p):
    return (p.get("x"), p.get("y"), p.get("plane", 0))


def _dist(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def restock_phase(ctx, cfg):
    """Walk to the bank, withdraw logs, walk back. True while the trip is in progress.

    WHY THIS EXISTS AT ALL, given a working cycle never consumes a log: the log basket cannot
    dispense one. MEASURED LIVE 2026-09-08 -- a basket in the inventory offers
    Open/Use/Check/Wear/Fill/Destroy and NO Empty, closed or open, because Empty is a
    bank-context option the bank interface substitutes in (the same fact banker.empty_log_basket
    already records from the cache). So once the loose log is gone, the 26 spares in the basket
    are unreachable at the mine and only a bank fixes it. USER: "if you run out of logs make
    sure you get to a bank restock and go back".
    """
    data = ctx.state.data
    home = data.get("g3t_home_tile") or tuple(cfg["mineTile"])

    # The ceiling counts CONSECUTIVE failed trips, not trips ever made. It is a "the bank is
    # out, or the withdraw is not landing" detector, and a routine that restocks successfully
    # and mines for another twenty minutes has demonstrably not hit that -- resetting on
    # progress is what lets this run unattended for hours instead of stopping after six
    # perfectly good trips.
    trips = data.get("g3t_restock_trips", 0)
    if trips >= RESTOCK_MAX_TRIPS:
        ctx.state.trigger_safety_stop(
            f"{trips} bank trips and still no loose {cfg['logName']!r} -- the bank is out, or "
            f"the withdraw is not landing")
        return True

    here = _tile_of(controller.get_player() or {})
    bank_tile = tuple(cfg["bankTile"])

    if _dist(here, bank_tile) > BANK_ARRIVAL_TILES:
        debug_log.log(CATEGORY, f"restock: walking to the bank at {bank_tile} "
                                f"({_dist(here, bank_tile)} tiles away)")
        travel.walk_to(ctx, {"x": bank_tile[0], "y": bank_tile[1], "plane": bank_tile[2]})
        return True

    # TRY EVERY NEARBY BANK OBJECT, not just the closest. MEASURED LIVE 2026-09-08: the
    # inventory panel is dragged into the middle of the screen for this routine (the whole
    # geometry argument depends on the log slot being near the rocks), and at the bank that
    # panel sits over the nearest chest's clickbox -- open_bank refused with "aim point is
    # behind a UI panel" and rotated the camera instead. The Great Conch bank has seven
    # bankable objects spread over several tiles, so the one behind the panel is not the only
    # one; walking a tile would also work but trying the next object is cheaper and surer.
    banks = [o for o in (controller.get_objects(interactable=True) or [])
             if any(a.lower() == "bank" for a in (o.get("actions") or []))
             and (o.get("distance") or 99) <= 12]
    banks.sort(key=lambda o: o.get("distance") or 99)
    if not banks:
        debug_log.log(CATEGORY, "restock: at the bank tile but no bankable object in view")
        return True
    for candidate in banks[:4]:
        if banker.open_bank(ctx, candidate):
            break
        debug_log.log(CATEGORY, f"restock: {candidate.get('name')} at "
                                f"({candidate.get('x')},{candidate.get('y')}) would not open "
                                f"-- trying the next one")
    else:
        debug_log.log(CATEGORY, "restock: no bank object would open")
        return True

    data["g3t_restock_trips"] = trips + 1
    # Everything the mine produced goes back, so the withdraw has room and the trip home starts
    # clean. keepItems protects the pickaxe and the basket.
    banker.deposit_inventory(ctx, {"keepItems": list(keep_names(cfg))})
    banker.search_and_withdraw(ctx, cfg["logName"], int(cfg.get("restockLogs") or 27),
                               contains=True)
    banker.close_bank(ctx)

    debug_log.log(CATEGORY, f"restock: withdrew logs, walking back to {home}")
    data["g3t_restocking"] = False
    data["g3t_returning"] = True
    return True


def _step_out(ctx, tile):
    """One direct minimap click at `tile`, bypassing the router.

    For when the static map says there is no route but the game plainly disagrees -- standing
    inside a building whose interior is an isolated component on our snapshot. worldmap can be
    wrong about a doorway; the game's own pathing is not."""
    point = controller.get_minimap_walk_point(tile[0], tile[1], tile[2])
    if not point or not point.get("onMinimap"):
        return False
    controller.click_entity(ctx.ser, point, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False)
    return True


def return_phase(ctx, cfg):
    """Walk back to the rocks. True while still travelling.

    HOME COMES FROM CONFIG, NOT FROM ctx.state.data. MEASURED LIVE 2026-09-08: this read
    data["g3t_home_tile"], which a restart wipes, and returned False the instant it was missing
    -- so a routine restarted at the bank fell straight through to the startup check, refused
    because the rocks do not resolve from there, and idle-drifted for seven minutes without one
    line of explanation. The player's position survives a restart; ctx.state.data does not."""
    home = tuple(cfg["mineTile"])
    here = _tile_of(controller.get_player() or {})
    if here[0] is None:
        return True                       # cannot tell; try again next tick rather than mine
    if _dist(here, home) <= 2:
        debug_log.log(CATEGORY, "back at the rocks")
        ctx.state.data["g3t_returning"] = False
        return False

    if worldmap.find_path(here[0], here[1], here[2], home[0], home[1],
                          goal_adjacent=True) is None:
        out = tuple(cfg["bankTile"])
        debug_log.log(CATEGORY, f"no map route home from {here} -- stepping out to {out}")
        if not _step_out(ctx, out):
            debug_log.log(CATEGORY, "could not step out: no minimap point for the bank tile")
        _sleep(2.0)
        return True

    travel.walk_to(ctx, {"x": home[0], "y": home[1], "plane": home[2]})
    return True


def _startup_check(ctx, cfg):
    """True once the startup assertions have PASSED. False means either "deferred, ask again
    next tick" or "refused and stopped" -- tick() returns on both.

    An empty inventory or a stale tick clock is "cannot tell yet", not "failed": both are what
    the login screen looks like. Only STARTUP_DEFER_MAX_S of that turns into a stop, and the
    stop names which of the two could not be read."""
    data = ctx.state.data
    now = _now()
    since = data.setdefault("g3t_startup_since", now)

    # NOT BEING AT THE ROCKS IS "NOT YET", NOT "CANNOT". MEASURED LIVE 2026-09-08: a restart
    # while standing at the bank refused with "at least one rock does not resolve on screen" --
    # perfectly true, and exactly what a routine holding a fresh armful of logs and a walk home
    # ahead of it looks like. The walk-home leg in _run_chain is what fixes it, and it cannot
    # run while startup keeps stopping the routine.
    here = _tile_of(controller.get_player() or {})
    if cfg.get("mineTile") and here[0] is not None \
            and _dist(here, tuple(cfg["mineTile"])) > BANK_ARRIVAL_TILES:
        if not data.get("g3t_startup_away_logged"):
            data["g3t_startup_away_logged"] = True
            debug_log.log(CATEGORY, "startup deferred: not at the rocks yet -- walking back "
                                    "before the geometry can be measured")
        data["g3t_startup_since"] = now      # the walk is not part of the defer budget
        return False
    data.pop("g3t_startup_away_logged", None)

    inv = controller.get_inventory() or []
    clock_live = ctx.events is not None and not ctx.events.ticks.is_stale(now)
    if not inv or not clock_live:
        if now - since >= STARTUP_DEFER_MAX_S:
            unread = []
            if not inv:
                unread.append("the inventory still reads empty (logged in? bridge up?)")
            if not clock_live:
                # The ONE place this repo's "always tolerate ctx.events is None" rule inverts.
                # There is no fallback: without the tick clock there is no tick phase, and
                # without the phase this is an ordinary 5-tick miner with extra steps.
                # Degrading to that silently would be worse than stopping, so do not "fix" this
                # into a polling fallback.
                unread.append("the UDP tick clock is absent or stale (was controller.py "
                              "started with --no-events?)")
            ctx.state.trigger_safety_stop(
                f"3-tick granite could not start after {now - since:.0f}s: " +
                " and ".join(unread))
        return False

    for check in (check_geometry, check_inventory_is_clean):
        ok, msg = check(cfg, inv)
        debug_log.log(CATEGORY, f"startup: {msg}")
        if not ok:
            ctx.state.trigger_safety_stop(f"3-tick granite cannot start: {msg}")
            return False

    data["g3t_index"] = 0
    data["g3t_logs"] = log_count(inv, cfg["logName"])
    data["g3t_startup_pending"] = False
    rotation = "nearest live rock" if dynamic_rotation(cfg) else f"{len(cfg['rockTiles'])} rocks"
    debug_log.log(CATEGORY, f"ready: {rotation}, {data['g3t_logs']} log(s)")
    return True


def next_rock_index(index, count):
    return (index + 1) % count if count else 0


def rock_refresh_target(cfg, tile, blockers=None):
    """A zero-arg callable answering the rock's LIVE screen point (or None), over every id in
    rockIds -- controller.object_refresh_target for a rock whose live id is one of several.
    With `blockers`, a point behind a UI rect answers None (move_to_human then keeps its last
    aim) rather than steering the click onto the sidebar."""
    x, y, plane = tile[0], tile[1], tile[2] if len(tile) > 2 else 0
    ids = sorted(rock_ids(cfg))

    def _refresh():
        for rock_id in ids:
            obj = controller.get_object_at(x, y, plane, rock_id)
            if obj and obj.get("screenX") is not None:
                point = (int(obj["screenX"]), int(obj["screenY"]))
                if blockers and _behind_ui(point, blockers):
                    return None
                return point
        return None
    return _refresh


def _ui_blockers():
    return (controller.get_state() or {}).get("uiBlockers") or []


def _behind_ui(point, blockers):
    """MEASURED live 2026-09-09 (g3t_live31): a rock whose projection sat under the sidebar's
    tab strip was clicked as "Sailing Options", the panel opened over the inventory, and the
    next four knife clicks hit "Facilities". timed_click bypasses click_entity's blocker check
    by design, so the rocks are filtered here instead."""
    x, y = point
    for rect in blockers:
        rx, ry = rect.get("x", 0), rect.get("y", 0)
        if rx <= x < rx + rect.get("width", 0) and ry <= y < ry + rect.get("height", 0):
            return True
    return False


def resolve_rock(cfg, tile):
    """The rock's live screen point, or None when it does not resolve.

    None means "not a live rock right now": get_object_at matches by id, and a DEPLETED granite
    rock is a different id (11391 "Rocks", no actions), so this is also the depletion test. The
    projection moves every cycle because the PLAYER moves between rocks, so it is re-read every
    cycle -- during the dead ticks, where 1200ms of slack absorbs an HTTP read."""
    x, y, plane = tile[0], tile[1], tile[2]
    for rock_id in sorted(rock_ids(cfg)):
        obj = controller.get_object_at(x, y, plane, rock_id)
        if obj and obj.get("screenX") is not None:
            return int(obj["screenX"]), int(obj["screenY"])
    return None


def next_live_rock(cfg, index, player=None, exclude=None):
    """(point, index) of the first rock at or after `index` in the rotation that resolves --
    and, with forceStep and a player tile, that is NOT adjacent to where the player stands, so
    mining it means a step (USER: "make sure we always move 1 tile to mine"; the wiki's second
    roll comes only "if moving up to them"). A rotation with nothing but adjacent live rocks
    falls back to the first live one rather than idling. `exclude` is the index just clicked:
    its rock is about to deplete on its own pay and is never re-picked (MEASURED g3t_live39:
    picked again while the other three were still on respawn, then clicked as it emptied).
    (None, index) when none resolves.

    THIS IS CALLED AT FIRE TIME, not during the wait. USER: "if we mine 4 rocks the first one
    will always respawn when we mine the last" -- granite's 5s respawn against three 1.8s
    cycles means the next rock in order is back by the time the drop lands, and not before."""
    tiles = cfg.get("rockTiles") or []
    fallback = None
    for offset in range(len(tiles)):
        i = (index + offset) % len(tiles)
        if exclude is not None and i == exclude:
            continue
        point = resolve_rock(cfg, tiles[i])
        if point is None:
            continue
        if cfg.get("forceStep") and player and player.get("x") is not None:
            if max(abs(tiles[i][0] - player["x"]), abs(tiles[i][1] - player["y"])) < 2:
                if fallback is None:
                    fallback = (point, i)
                continue
        return point, i
    return fallback if fallback else (None, index)


def live_rocks(cfg, player=None):
    """Every live rock in the scan radius, nearest first: id in rockIds, offering Mine, with a
    screen point. A depleted rock is a different id and never appears here.

    `distance` is the bridge's own Chebyshev distance from the player; when a player dict is
    given it is recomputed from that, so a caller holding a fresher position than the tick-cached
    scan can rank by it."""
    objs = controller.get_objects(radius=cfg.get("scanRadius") or 12, interactable=True) or []
    ids = rock_ids(cfg)
    blockers = _ui_blockers()
    rocks = []
    seen = set()
    for o in objs:
        if o.get("id") not in ids or o.get("screenX") is None:
            continue
        if _behind_ui((o["screenX"], o["screenY"]), blockers):
            continue
        if not any((a or "").lower() == "mine" for a in (o.get("actions") or [])):
            continue
        tile = (o.get("x"), o.get("y"))
        if tile in seen:
            continue
        seen.add(tile)
        if player and player.get("x") is not None:
            o = dict(o)
            o["distance"] = max(abs(o["x"] - player["x"]), abs(o["y"] - player["y"]))
        rocks.append(o)
    rocks.sort(key=lambda o: (o.get("distance") if o.get("distance") is not None else 99))
    return rocks


def pick_rock(cfg, player, avoid_tile=None):
    """The nearest live rock, never the one just mined.

    Excluding the last tile is what makes every cycle a STEP: a failed roll leaves that rock
    live, and re-clicking it from where we stand forfeits the second roll the move buys."""
    for rock in live_rocks(cfg, player):
        if avoid_tile is not None and (rock.get("x"), rock.get("y")) == tuple(avoid_tile):
            continue
        return rock
    return None


def mined_tile_from_last_action(cfg, player, since_tick=None):
    """The world tile of the rock the game ACTUALLY resolved the last Mine click to, or None.

    The tile we aimed at is not the tile we mined: adjacent gem rocks' clickboxes overlap and
    the click resolves to whichever the client draws on top. MEASURED live 2026-09-09
    (g3t_live29 recorder): 9 of 69 rock clicks landed on a rock depleted ~700ms earlier -- the
    rock OUR previous cycle had just mined, re-picked because the exclusion held the aimed
    tile. /last-action carries the click's scene coordinates; world = the scene base + them."""
    last = controller.get_last_action() or {}
    if (last.get("option") or "").lower() != "mine" or last.get("id") not in rock_ids(cfg):
        return None
    if since_tick is not None and last.get("tick") is not None and last["tick"] < since_tick:
        return None
    bx, by = player.get("baseX"), player.get("baseY")
    if bx is None or by is None or last.get("param0") is None:
        return None
    return (bx + int(last["param0"]), by + int(last["param1"]))


def prepare(ctx, cfg, knife_point, log_point):
    """The DEAD-TICK half of a cycle: select the knife, park the cursor on the log.

    Called only once the next rock HAS resolved, so a knife selection is never left live with
    nothing to spend it on. The 2026-09-08 version selected first and, when the rock then failed
    to resolve, "consumed" the selection by clicking the log -- which IS a knife-on-log with no
    rock click behind it, i.e. a fletch that runs to completion and eats the log. The bot's own
    recording shows runs of those two ticks apart. Resolve, then select, and there is nothing
    to consume.

    The knife click is a client-side SELECTION and sends no packet, which is why both flights
    can live outside the critical tick."""
    scale = cfg.get("flightSpeedScale") or 1.0
    tick_cycle.timed_click(ctx.ser, knife_point, aim_radius=AIM_RADIUS_PX,
                           duration_scale=scale, now_fn=_now)
    controller.move_to_human(ctx.ser, int(log_point[0]), int(log_point[1]),
                             aim_radius=AIM_RADIUS_PX, duration_scale=scale)


def run_cycle(ctx, cfg, log_point, rock_point, rock_refresh=None):
    """The critical pair, fired NOW: log click in place (the cursor was parked there by arm),
    ONE flight, ONE rock click. "ok" | "no_clock".

    The caller has just seen the previous cycle's xp land (or is starting a chain), so this is
    the tick to fletch in; there is no schedule to wait for. THE ORDER IS FIXED -- log, then
    rock: an OSRS item selection is consumed by the next click on ANY target, so a rock click
    first would issue "Use Knife -> Gem rocks" and leave the log click bare. There is no
    abandon and no late branch: once the log is clicked a fletch is running and the rock click
    is the only thing that displaces it; a slow flight costs a 4-tick pay, a skipped click
    costs the log."""
    opened_at = _now()
    fired = tick_cycle.current_tick(ctx.events, now=opened_at)
    phase = tick_cycle.tick_phase_ms(ctx.events, opened_at)
    if fired is None or phase is None:
        return "no_clock"
    scale = cfg.get("flightSpeedScale") or 1.0
    in_place_ms = tick_cycle.click_in_place(ctx.ser, now_fn=_now)
    flight_ms = tick_cycle.timed_click(ctx.ser, rock_point, aim_radius=3,
                                       duration_scale=scale, now_fn=_now,
                                       refresh_target=rock_refresh,
                                       refresh_legs=1 if rock_refresh else None)
    spent = phase + (_now() - opened_at) * 1000.0
    ctx.state.data["g3t_fired_tick"] = fired
    ctx.state.data["g3t_fired_at"] = (opened_at, phase)
    _note_cycle(ctx, fired, phase, in_place_ms, flight_ms, spent)
    return "ok"


def pay_ticks(fired_at, seen_at):
    """How many ticks after the fletch the drop landed, from WALL TIME: the fletch's phase plus
    the ms until the xp was seen, in ticks, rounded. The xp is dispatched ~20ms before its
    boundary, so the sum sits just under a whole number of ticks and rounds to it. No tick
    counters anywhere -- MEASURED g3t_live57/58, every counter-based version was off by one in
    one direction or the other depending on whether the TICK event had been pumped."""
    if not fired_at:
        return None
    opened_at, phase = fired_at
    return int(round((phase + (seen_at - opened_at) * 1000.0 + 25.0) / tick_cycle.GAME_TICK_MS))


def wait_for_mining_xp(ctx, mark, timeout_s):
    """The Mining XP_GAINED that arrives after `mark`, or None at the timeout.

    Not EventStream.wait_for_event: that scans the whole buffer and would answer with the
    previous cycle's drop. since(mark) only sees what arrived after the click."""
    if ctx.events is None:
        return None
    deadline = _now() + timeout_s
    while True:
        for event_type, payload in ctx.events.since(mark):
            if event_type == "XP_GAINED" and (payload or {}).get("skill") == "Mining":
                return payload or {}
        if _now() >= deadline:
            return None
        _sleep(0.005)       # the drop is the cycle's starting gun; 20ms of poll was 20ms of flight


FAR_ROCK_WALK_BUDGET_S = 3.5    # <= 12 tiles at a run is 6 ticks; 6.0 was three lost cycles per miss
FAR_ROCK_POLL_S = 0.05


def armed_on_log(cfg):
    """True when the knife is SELECTED and the cursor rests on the log: /hover then reads
    "Use  Knife -> Teak logs". The bridge has no item-selection state of its own, and this
    question decides the whole cycle: MEASURED live 2026-09-09 (g3t_live29 recorder) every
    desync was followed by a blind re-prepare whose knife click landed with the knife still
    selected ("Use Knife -> Knife"), after which the log click SELECTED the log and the rock
    click used the log on the rock -- two to three garbage cycles per desync, most of the
    unpaid cycles of that run. None means the hover could not be read (fail open: prepare)."""
    hover = controller.get_hover()
    if not hover:
        return None
    if hover.get("menuOpen"):
        return False
    target = (hover.get("target") or "").lower()
    if "->" not in target:
        return False
    selected, _, under = target.partition("->")
    return ((cfg.get("knifeName") or "knife").lower() in selected
            and (cfg.get("logName") or "").lower() in under)


def arm(ctx, cfg, knife_point, log_point):
    """prepare, then PROVE it: the knife selected and the cursor on the log. Up to two
    passes, because a stale selection (knife or log) is spent by the first knife click and
    only the second one selects. False when it still cannot be proved."""
    for attempt in range(2):
        if armed_on_log(cfg):
            return True
        if attempt == 1:
            controller.open_inventory_tab(ctx.ser)     # a switched sidebar tab looks the same
        prepare(ctx, cfg, knife_point, log_point)
    ok = armed_on_log(cfg)
    if ok is None:
        return True          # unreadable hover: the old blind behaviour, once per cycle
    if not ok:
        debug_log.log(CATEGORY, "could not arm the knife on the log (hover disagrees twice)")
    return bool(ok)


def far_rock_cycle(ctx, cfg, knife_point, log_point, rock):
    """A rock more than stepMaxTiles away: walk to it first, and fletch ONE STEP OUT.

    The guide's rule for the gem mine, verbatim: "click the far rock that you're supposed to go
    to next, and once your true tile goes over the [one-tile-out] marker start your knife-log
    animation and continue like normal". So: click the rock (the walk starts), select the knife
    and park on the log while walking, poll the true tile, and the moment it is within
    stepMaxTiles of the rock fire the ordinary pair -- log in place, one flight, rock.

    "ok" | "desync"."""
    scale = cfg.get("flightSpeedScale") or 1.0
    tile = (rock.get("x"), rock.get("y"))
    step = int(cfg.get("stepMaxTiles") or 2)
    # A FRESH point for the walk click: the scan's projection is tick-cached and the player was
    # walking when it was taken -- MEASURED live 2026-09-09, a stale one resolved to "Walk
    # here" beside the rock and the wait below then spent its whole budget.
    refresh = rock_refresh_target(cfg, (tile[0], tile[1], rock.get("plane", 0)))
    start = refresh()
    if start is None:
        return "desync"
    tick_cycle.timed_click(ctx.ser, start, aim_radius=3, duration_scale=scale, now_fn=_now,
                           refresh_target=refresh, refresh_legs=2)
    if not arm(ctx, cfg, knife_point, log_point):
        return "desync"
    deadline = _now() + FAR_ROCK_WALK_BUDGET_S
    while _now() < deadline:
        p = controller.get_player() or {}
        if p.get("x") is not None and max(abs(p["x"] - tile[0]), abs(p["y"] - tile[1])) <= step:
            break
        _sleep(FAR_ROCK_POLL_S)
    else:
        debug_log.log(CATEGORY, f"far rock {tile}: never got within {step} tiles -- resyncing")
        return "desync"
    point = refresh()
    if point is None:
        debug_log.log(CATEGORY, f"far rock {tile}: gone before we arrived -- resyncing")
        return "desync"
    opened_at = _now()
    fired = tick_cycle.current_tick(ctx.events, now=opened_at)
    phase = tick_cycle.tick_phase_ms(ctx.events, opened_at)
    if fired is None or phase is None:
        return "desync"
    in_place_ms = tick_cycle.click_in_place(ctx.ser, now_fn=_now)
    flight_ms = tick_cycle.timed_click(ctx.ser, point, aim_radius=3, duration_scale=scale,
                                       now_fn=_now, refresh_target=refresh, refresh_legs=2)
    spent = phase + (_now() - opened_at) * 1000.0
    ctx.state.data["g3t_fired_tick"] = fired
    ctx.state.data["g3t_fired_at"] = (opened_at, phase)
    debug_log.log(CATEGORY, f"far rock {tile}: fletched one step out")
    _note_cycle(ctx, fired, phase, in_place_ms, flight_ms, spent)
    return "ok"


def _note_cycle(ctx, fired, phase, in_place_ms, flight_ms, spent_ms):
    """One line per cycle, and the record the outcome accounting joins the xp to."""
    same_tick = spent_ms < tick_cycle.GAME_TICK_MS
    verdict = "same tick" if same_tick else "NEXT tick -- expect a 4-tick pay"
    data = ctx.state.data
    dead = data.pop("g3t_dead", None)
    fl = tick_cycle.LAST_FLIGHT
    breakdown = (f" [move {fl.get('move_ms', 0):.0f} settle {fl.get('settle_ms', 0):.0f}"
                 f"{'*' if fl.get('settled') else ''} click {fl.get('click_ms', 0):.0f}]")
    debug_log.log(CATEGORY,
                  f"cycle fired on tick {fired}: log click at phase {phase:.0f}ms "
                  f"({in_place_ms:.0f}ms), rock click +{flight_ms:.0f}ms{breakdown}, "
                  f"{spent_ms:.0f}ms into the tick ({verdict})"
                  + (f"; dead time: {dead}" if dead else ""))
    pending = data.setdefault("g3t_pending", [])
    pending.append({"tick": fired, "same_tick": same_tick})
    del pending[:-8]
    data["g3t_cycles"] = data.get("g3t_cycles", 0) + 1


def _inventory_full():
    return len(controller.get_inventory() or []) >= getattr(controller, "INVENTORY_SIZE", 28)


def full_inventory_phase(ctx, cfg):
    """True when this tick was spent dropping. Knife + log leaves 26 slots, so this comes due
    roughly every 26 cycles (~50s).

    controller.drop_items is the right primitive and its contract matters here: it returns the
    CONFIRMED inventory delta, not the number of clicks issued, and it polls DROP_CONFIRM_S
    before deciding -- a verification window shorter than the game's 0.6s tick reads a good drop
    as a failure. It also holds SHIFT once across the burst rather than tapping it per item,
    which is a wire-visible difference (EVENT_KEYBOARD carries every press with its gap)."""
    if not _inventory_full():
        return False

    keep = keep_names(cfg)
    items = [i for i in (controller.get_inventory() or [])
             if not any(k in (i.get("name") or "").lower() for k in keep)]
    if not items:
        # drop_items would return True vacuously on an empty list, so this tick would keep
        # "succeeding" forever -- and tick() returns at this gate before _stall_check, so the
        # XP stall watch could never catch it.
        ctx.state.trigger_safety_stop(
            "inventory full but every item is on the keep list -- nothing can be dropped")
        return True
    if (cfg.get("fullInventory") or "drop") == "deposit":
        deposit_phase(ctx, cfg, items)
        return True
    debug_log.log(CATEGORY, f"inventory full -- dropping {len(items)} items")
    dropped = drop_burst(ctx, items, shift=bool(cfg.get("dropWithShift")))
    if not dropped and not controller.drop_items(ctx.ser, items):
        ctx.state.trigger_safety_stop("inventory still full after dropping")
    return True


DROP_HOP_SCALE = 0.45           # the 74px hop between slots, a practised short reach
DROP_HOP_PAUSE_S = (0.02, 0.09) # USER 2026-09-09 on 0.3 / 0-20ms: "bit too fast dropping"
DROP_CONFIRM_S = 1.5


def drop_burst(ctx, items, shift=False):
    """Drop `items` the way a human clicks down an inventory: one short hop and one fast click
    per slot, row by row, left to right (USER 2026-09-09: "quick and human looking").

    NOT a glide with a click burst (mastering_mixology's herb sweep): MEASURED g3t_live49, the
    eased glide bunches its clicks at each row's ends and every second slot survived (12 of 26
    left, alternating); the slower 5-7Hz sweep of g3t_live48 only worked by over-clicking.

    Only the given items are ever clicked, so the knife, the log, the basket and the sack are
    never under the cursor. `shift` holds SHIFT across the burst for an account that relies on
    the game's shift-click-drop; USER's left click is a Drop swap. True when the inventory
    shrank; False hands over to controller.drop_items' verified per-item path."""
    todo = sorted((i for i in items if i.get("screenX") is not None and i.get("slot") is not None),
                  key=lambda i: i["slot"])
    if not todo:
        return False
    before = len(controller.get_inventory() or [])
    if shift:
        controller.key_down(ctx.ser, "SHIFT")
    try:
        for item in todo:
            controller.move_to_human(ctx.ser, int(item["screenX"]), int(item["screenY"]),
                                     aim_radius=3, duration_scale=DROP_HOP_SCALE)
            tick_cycle.fast_click(ctx.ser)
            pause = random.uniform(*DROP_HOP_PAUSE_S)
            if pause:
                _sleep(pause)
    finally:
        if shift:
            controller.key_up(ctx.ser, "SHIFT")
    deadline = _now() + DROP_CONFIRM_S
    while _now() < deadline:
        if len(controller.get_inventory() or []) < before:
            return True
        _sleep(0.2)
    debug_log.log(CATEGORY, "the drop burst dropped nothing -- falling back to the per-item drop")
    return False


# --------------------------------------------------------------------------------------------
# Depositing at the gem mine's Bank Deposit Chest
# --------------------------------------------------------------------------------------------

DEPOSIT_GROUP = 192             # the deposit-box interface (karambwan_fishing, motherlode)
DEPOSIT_INV_CHILD = 24          # its inventory grid
DEPOSIT_CLOSE_CHILD = 1
DEPOSIT_ACTION = "Deposit"
DEPOSIT_ALL_ACTION = "Deposit-All"
GEM_BAG_NAMES = ("gem bag", "gem sack")     # the sack is the bag + tote, same Empty
GEM_BAG_EMPTY_OPTIONS = ("empty",)
DEPOSIT_CLICK_RANGE = 10        # further than this (or off screen): walk first
DEPOSIT_OPEN_BUDGET_S = 4.0
DEPOSIT_ITEM_BUDGET_S = 2.0
DEPOSIT_FAIL_STOP = 10


def _deposit_open():
    return bool((controller.get_widget(DEPOSIT_GROUP, DEPOSIT_INV_CHILD) or {}).get("clickable"))


def _deposit_children():
    data = controller.get_widget_children(DEPOSIT_GROUP, DEPOSIT_INV_CHILD) or []
    if isinstance(data, dict):
        data = data.get("children") or []
    return [c for c in data if isinstance(c, dict) and c.get("itemName")]


def _find_deposit_chest(cfg):
    objs = controller.get_objects(radius=30, interactable=True) or []
    chests = [o for o in objs if o.get("id") == cfg.get("depositId")]
    chests.sort(key=lambda o: o.get("distance") if o.get("distance") is not None else 99)
    return chests[0] if chests else None


def _deposit_fail(ctx, what):
    n = ctx.state.data.get("g3t_deposit_fails", 0) + 1
    ctx.state.data["g3t_deposit_fails"] = n
    debug_log.log(CATEGORY, f"deposit: {what} ({n})")
    if n >= DEPOSIT_FAIL_STOP:
        ctx.state.trigger_safety_stop(f"deposit: {what} {n} times running")


def _interface_click(ctx, target, option):
    """One left click gated on /hover reporting `option`, the right-click menu as the fallback
    -- karambwan_fishing's _click_option, whose account has these swapped to left clicks."""
    ok = controller.click_entity(ctx.ser, target, expected_action=option, offset=(0, 0),
                                 check_ui_blockers=False, simulate_mistakes=False)
    if ok:
        return True
    return bool(controller.click_menu_action(ctx.ser, target, option, offset=(0, 0),
                                             check_ui_blockers=False))


def deposit_phase(ctx, cfg, items):
    """Walk to the deposit chest, Deposit-All every stack not on the keep list, Empty the gem
    bag, close, and let the chain resume where it stands. True while the trip is in progress.

    The guide banks once per lap at this chest; the routine banks when the inventory is full,
    which on the gem mine's opal/jade/topaz mix is every ~40 cycles. Deposit-All per stack
    rather than the "Deposit inventory" button, because that button would bank the knife, the
    log and the gem bag too."""
    tile = cfg.get("depositTile")
    if not tile:
        ctx.state.trigger_safety_stop("fullInventory is 'deposit' but no depositTile is set")
        return True
    target = {"x": tile[0], "y": tile[1], "plane": tile[2] if len(tile) > 2 else 0}
    chest = _find_deposit_chest(cfg)
    if chest is None or (chest.get("distance") or 99) > DEPOSIT_CLICK_RANGE:
        debug_log.log(CATEGORY, "deposit: walking to the chest")
        travel.walk_to(ctx, target, goal_adjacent=True)
        return True

    if not _deposit_open():
        refresh = controller.object_refresh_target(chest["x"], chest["y"], chest.get("plane", 0),
                                                   chest["id"])
        if not controller.click_entity(ctx.ser, chest, refresh_target=refresh,
                                       simulate_mistakes=False):
            _deposit_fail(ctx, "could not click the chest")
            return True
        if not _await(_deposit_open, DEPOSIT_OPEN_BUDGET_S + 0.6 * (chest.get("distance") or 0)):
            _deposit_fail(ctx, "the deposit interface did not open")
            return True

    keep = keep_names(cfg)
    for _ in range(len(items) + 2):
        loose = {(i.get("name") or "").lower() for i in (controller.get_inventory() or [])
                 if not any(k in (i.get("name") or "").lower() for k in keep)}
        if not loose:
            break
        child = next((c for c in _deposit_children()
                      if (c.get("itemName") or "").lower() in loose), None)
        if child is None:
            _deposit_fail(ctx, "a stack is not in the deposit grid")
            break
        name = (child.get("itemName") or "").lower()
        if not _interface_click(ctx, child, DEPOSIT_ALL_ACTION):
            _deposit_fail(ctx, f"could not Deposit-All the {name}")
            break
        _await(lambda n=name: n not in {(i.get("name") or "").lower()
                                        for i in (controller.get_inventory() or [])},
               DEPOSIT_ITEM_BUDGET_S)

    bag = next((c for c in _deposit_children()
                if any(n in (c.get("itemName") or "").lower() for n in GEM_BAG_NAMES)), None)
    if bag is not None:
        if not controller.click_menu_action(ctx.ser, bag, GEM_BAG_EMPTY_OPTIONS, offset=(0, 0),
                                            check_ui_blockers=False):
            debug_log.log(CATEGORY, "deposit: could not Empty the gem bag -- carrying on")
        else:
            _sleep(0.6)

    for _ in range(3):
        if not _deposit_open():
            break
        controller.key_tap(ctx.ser, "ESC")
        if _await(lambda: not _deposit_open(), 1.5):
            break
        button = controller.get_widget(DEPOSIT_GROUP, DEPOSIT_CLOSE_CHILD) or {}
        if button.get("clickable"):
            controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False,
                                    simulate_mistakes=False)
            _await(lambda: not _deposit_open(), 1.5)
    if _deposit_open():
        _deposit_fail(ctx, "the deposit interface would not close")
        return True
    if not _inventory_full():
        ctx.state.data["g3t_deposit_fails"] = 0
        debug_log.log(CATEGORY, "deposit: done")
    return True


CHAIN_MAX_S = 45.0      # hand control back to the engine at least this often


def resync(ctx):
    """Drop the pin and re-enter on a clean tick boundary.

    USER's decision, and the reason there is no desync ceiling: a missed tick is an ordinary
    event in 3-tick mining -- a human's rate dips and recovers -- so this never stops the run.
    The XP stall watch in tick() is the only thing that does.

    Clearing the movement abort is cheap insurance: nothing in this routine SETS it, but the
    flag is process-global and STICKY, so anything else that leaves it set would silently kill
    every flight for the rest of the session."""
    controller.clear_movement_abort()
    ctx.state.data["g3t_fired_tick"] = None
    if ctx.events is not None:
        ctx.events.wait_ticks(1, now_fn=_now, sleep_fn=_sleep)
    else:
        _sleep(tick_cycle.GAME_TICK_MS / 1000.0)


def _stall_check(ctx, cfg, now):
    """True when the run should stop. Keyed on Mining XP, NOT on desyncs.

    This routine's failure mode is exceptionally silent: a perfectly-aimed click landing in the
    wrong tick looks identical in the log to a good one, so without this a contested rock or a
    nudged camera would click a dead pixel for hours. CLAUDE.md's standing lesson -- a silent
    loop is worse than a crash."""
    data = ctx.state.data
    xp = (controller.get_player() or {}).get("miningXp")
    if xp is None:
        return False
    if data.get("g3t_xp") is None or xp > data["g3t_xp"]:
        data["g3t_xp"] = xp
        data["g3t_xp_at"] = now
        return False
    since = now - (data.get("g3t_xp_at") or now)
    if since >= cfg["xpStallStopS"]:
        ctx.state.trigger_safety_stop(
            f"no Mining xp for {since:.0f}s -- the rocks may be contested, the camera moved, "
            f"or the client is stuttering")
        return True
    return False


def _knife_click_is_a_selection(ctx, knife_point):
    """(ok, detail). Run ONCE PER CHAIN, never per cycle -- it costs a flight and an HTTP read,
    and both belong outside the critical tick.

    A bare left-click on an inventory item is whatever the account's Menu Entry Swapper made it,
    and this account demonstrably swaps items (CLAUDE.md's karambwan and giants_foundry
    sections). A swap that made the knife's left click "Drop" would bin the knife on cycle 1,
    and every cycle afterwards would look perfect in the log while clicking an empty slot.
    /hover reports what a left click would do RIGHT NOW, so the cursor has to be over the knife
    first -- which is where prepare() wants it anyway.

    Deliberately fails CLOSED, unlike controller.hover_matches, which never blocks when it
    cannot verify: everywhere else in this repo an unverifiable hover degrades to
    click-then-check-/last-action, and there is no post-hoc check here that would ever notice a
    dropped knife."""
    controller.move_to_human(ctx.ser, int(knife_point[0]), int(knife_point[1]),
                             aim_radius=AIM_RADIUS_PX)
    hover = controller.get_hover() or {}
    if hover.get("menuOpen"):
        return False, "a right-click menu is open over the knife"
    option = (hover.get("option") or "").strip()
    if not option:
        return False, "/hover could not say what a left click on the knife would do"
    if "use" not in option.lower():
        return False, (f"a left click on the knife resolves to {option!r}, not Use -- a Menu "
                       f"Entry Swapper entry has changed it")
    return True, option


def tick(ctx):
    """Gap-aware wrapper around one chain of cycles.

    tick() is NOT called during a scheduled break, an F2 pause, an unfocused window or a
    reconnect wait, so no Mining xp can arrive across any of them -- and _stall_check is
    wall-clock. Without this the first check after a 2-8 minute break computed a several-hundred
    second stall and safety-stopped a perfectly healthy run with a message that was simply
    false. wintertodt.py stands its own watch down between rounds for the same reason."""
    data = ctx.state.data
    now = _now()
    last = data.get("g3t_last_tick_at")
    if last is not None and now - last > TICK_GAP_RESEED_S:
        data["g3t_xp_at"] = now
        debug_log.log(CATEGORY,
                      f"stall clock stood down after a {now - last:.0f}s gap between ticks "
                      f"(break, pause, unfocused window or reconnect -- tick() does not run "
                      f"during any of them)")
    try:
        _run_chain(ctx)
    finally:
        data["g3t_last_tick_at"] = _now()


def _reseed_stall(ctx):
    """Stand the Mining-XP stall watch down for this tick.

    The watch is wall-clock and only resets on rising xp, so any tick spent NOT cycling -- a
    bank walk, a basket refill, an unreadable position -- looks identical to a stall. MEASURED
    LIVE 2026-09-08: an 80s round trip to the bank tripped "no Mining xp for 80s -- the rocks
    may be contested", which was true and entirely beside the point. Same shape as the gap
    reseed in tick(), which covers breaks and pauses; this covers work we chose to do."""
    ctx.state.data["g3t_xp_at"] = _now()


def _hold_one_log(ctx, cfg):
    """True when exactly one loose log is in hand. False means this tick was spent getting
    there -- topping up from the basket, or a leg of the bank trip."""
    data = ctx.state.data
    if ensure_one_loose_log(ctx, cfg):
        data["g3t_log_refills"] = 0
        # Re-baseline: whatever the count was before, we deliberately hold exactly one now, and
        # the consumption warning downstream compares against this. Without it, resolving a
        # 26-log surplus reads as "25 logs consumed".
        data["g3t_logs"] = 1
        data["g3t_restock_trips"] = 0     # a log in hand means the last trip did its job
        return True
    fails = data.get("g3t_log_refills", 0) + 1
    data["g3t_log_refills"] = fails
    if fails >= BASKET_REFILL_TRIES:
        # The basket cannot dispense a single log outside a bank (see restock_phase), so the
        # only fix is the trip. Reset so the trip gets a clean slate.
        data["g3t_log_refills"] = 0
        if not cfg.get("bankTile"):
            ctx.state.trigger_safety_stop(
                f"no loose {cfg['logName']!r} left and this site has no bank to restock from")
            return False
        # Hand the trip to restock_phase via a flag rather than calling it here, so the
        # walk-home leg above stands aside for the whole journey.
        data["g3t_restocking"] = True
        debug_log.log(CATEGORY, "no logs and the basket cannot dispense one -- restocking")
    return False


def _run_chain(ctx):
    cfg = ctx.state.config
    data = ctx.state.data

    # A RESTOCK OWNS THE MOVEMENT WHILE IT IS RUNNING. MEASURED LIVE 2026-09-08: with the
    # walk-home check unconditionally first, the routine walked to the bank, was immediately
    # told it was not at the rocks, and walked back -- never once opening the bank. Whoever is
    # travelling has to be the one deciding where to travel.
    if data.get("g3t_restocking"):
        # The trip earns no Mining xp by definition, and the stall watch is wall-clock: without
        # this the walk itself trips "no Mining xp for 157s", which is exactly what happened
        # live on 2026-09-08. The walk-home and refill paths were already reseeded; this one
        # was missed, and it is the longest of the three.
        _reseed_stall(ctx)
        restock_phase(ctx, cfg)
        return

    # THE WALK HOME COMES BEFORE EVERYTHING, INCLUDING THE STARTUP CHECK. Startup measures the
    # geometry against the rocks, which only resolve at the mine, so a restart at the bank
    # refused ("at least one rock does not resolve") and returned -- and the walk that would
    # have fixed it sat below the refusal and never ran. MEASURED LIVE 2026-09-08.
    player = controller.get_player() or {}
    here = _tile_of(player)
    if here[0] is None:
        # AN UNREADABLE POSITION IS "CANNOT TELL", NOT "CARRY ON": /player reads empty for a
        # tick right after the bank interface closes, and cycling on that read means cycling at
        # the bank. Same rule as the empty /inventory read below.
        _reseed_stall(ctx)
        return
    if cfg.get("regionId") is not None and player.get("regionId") != cfg["regionId"]:
        # No travel here, deliberately -- the gem mine is entered by a Karamja-gloves teleport
        # and left by another, so "walk there" has no meaning. Same rule as wintertodt.py.
        ctx.state.trigger_safety_stop(
            f"not in region {cfg['regionId']} (player reads {player.get('regionId')}) -- start "
            f"this routine standing inside the mine")
        return
    if cfg.get("mineTile") and _dist(here, tuple(cfg["mineTile"])) > BANK_ARRIVAL_TILES:
        _reseed_stall(ctx)
        if return_phase(ctx, cfg):
            return

    if data.get("g3t_startup_pending") and not _startup_check(ctx, cfg):
        return

    inv = controller.get_inventory() or []
    if not inv:
        # AN EMPTY READ MEANS "CANNOT TELL", NEVER "THE ITEMS ARE GONE": controller.get_inventory
        # returns [] on ANY bridge error (`api_get(...) or []`), so a 200ms hiccup, a client GC
        # pause or a momentary LOADING screen would otherwise end an unattended run hours before
        # anyone looked. Counted so a genuinely emptied inventory is still caught eventually.
        empties = data.get("g3t_empty_reads", 0) + 1
        data["g3t_empty_reads"] = empties
        if empties >= EMPTY_INVENTORY_STOP:
            ctx.state.trigger_safety_stop(
                f"/inventory has read empty {empties} ticks running -- the bridge is down, the "
                f"client is not logged in, or the inventory really is empty")
        else:
            debug_log.log(CATEGORY, f"empty /inventory read ({empties}) -- riding it out")
        return
    data["g3t_empty_reads"] = 0

    # THE LOG SURPLUS IS RESOLVED BEFORE THE FULL-INVENTORY DROP, and the order is load-bearing.
    # MEASURED LIVE 2026-09-08: the first restock came back with 27 logs, which fills the
    # inventory -- and every one of them is on the keep list, so full_inventory_phase found
    # nothing droppable and safety-stopped a run that was one basket-Fill away from working.
    # A pile of logs is a known, resolvable state, not a stuck one.
    # WALK HOME WHENEVER WE ARE NOT AT THE ROCKS, derived from POSITION rather than from a
    # stored "returning" flag. MEASURED LIVE 2026-09-08: a restart at the bank lost the flag and
    # the routine cheerfully tried to cycle there, logging "rock did not resolve" for all four
    # rocks forever. ctx.state.data does not survive a restart; the player's position does.
    if not _hold_one_log(ctx, cfg):
        _reseed_stall(ctx)
        return

    if full_inventory_phase(ctx, cfg):
        return


    knife = find_item(inv, cfg["knifeName"])
    if knife is None:
        ctx.state.trigger_safety_stop(f"the {cfg['knifeName']!r} is gone from the inventory")
        return

    # EXACTLY ONE loose log, refilled from the basket when a mistake has eaten one. A working
    # cycle never consumes a log (the rock click interrupts the fletch), so reaching into the
    # basket means something went wrong earlier -- which is precisely what the wiki means by
    # carrying spares "in case of mistakes". See ensure_one_loose_log.

    log = find_item(inv := (controller.get_inventory() or []), cfg["logName"])
    if log is None or log.get("screenX") is None:
        return

    # Checked once per chain (so within CHAIN_MAX_S, not "in seconds"). The constraint this
    # module exists to protect is the CRITICAL TICK, not the cycle: the chain loop below does
    # read /inventory once per cycle for the full-inventory check, which is affordable because
    # it happens between cycles. What must never happen is an HTTP read between the log click
    # and the rock click. USER says teak/mahogany are not consumed by knife-on-log; if that is
    # wrong, the worst case is one chain of clicks landing on an empty slot (harmless with a
    # knife selected) before this stops the run. Spec live-checklist item 1 verifies exactly it.
    # A FALLING LOG COUNT IS A DIAGNOSTIC, NOT A STOP. A cycle that lands both clicks never
    # consumes a log; one that fails to interrupt does. So losing logs means cycles are not
    # completing, and the occupancy log is where to look -- ensure_one_loose_log above has
    # already topped us back up from the basket.
    logs = log_count(inv, cfg["logName"])
    before = data.get("g3t_logs")
    if before is not None and logs < before:
        data["g3t_logs_burned"] = data.get("g3t_logs_burned", 0) + (before - logs)
        debug_log.log(CATEGORY,
                      f"a log was consumed ({before} -> {logs}, {data['g3t_logs_burned']} total) "
                      f"-- a cycle failed to interrupt the fletch; check the occupancy lines")
    data["g3t_logs"] = logs

    knife_point = (int(knife["screenX"]), int(knife["screenY"]))
    log_point = (int(log["screenX"]), int(log["screenY"]))
    tiles = cfg.get("rockTiles") or []
    dynamic = dynamic_rotation(cfg)

    # Once per chain, both outside the critical tick. Every other inventory-clicking site in
    # this repo opens the tab first (CLAUDE.md, gf_live27: "after a pause/resume the stats tab
    # was up and the right-click offered View Attack guide").
    if not controller.open_inventory_tab(ctx.ser):
        fails = data.get("g3t_tab_fails", 0) + 1
        data["g3t_tab_fails"] = fails
        if fails >= INVENTORY_TAB_STOP:
            ctx.state.trigger_safety_stop(
                f"the inventory tab could not be confirmed open {fails} ticks running -- "
                f"/state is unreadable (bridge down?) or the tab hotkey is not landing. Nothing "
                f"has been clicked since; without this the routine would idle forever")
        else:
            debug_log.log(CATEGORY, f"could not confirm the inventory tab is open ({fails}) -- "
                                    f"not starting a chain this tick")
        return
    data["g3t_tab_fails"] = 0

    ok, detail = _knife_click_is_a_selection(ctx, knife_point)
    if not ok:
        fails = data.get("g3t_hover_fails", 0) + 1
        data["g3t_hover_fails"] = fails
        if fails >= KNIFE_HOVER_STOP:
            ctx.state.trigger_safety_stop(
                f"the knife hover check has failed {fails} ticks running ({detail}) -- either "
                f"the bridge cannot be read, or this client was built without "
                f"MenuStateProvider so /hover does not exist (rebuild it), or a Menu Entry "
                f"Swapper entry really has changed the knife's left click")
        else:
            debug_log.log(CATEGORY, f"refusing to run a chain ({fails}): {detail}")
        return
    data["g3t_hover_fails"] = 0
    # Per chain: nothing is known about the previous chain's selection state, and the first
    # cycle simply fires -- there is no pay to wait for yet.
    data["g3t_pending"] = []
    started = _now()
    pending = None              # everything the next fire needs, prepared during the xp wait
    while True:
        now = _now()
        if ctx.state.safety_stopped or getattr(ctx.state, "paused", False) \
                or now - started >= CHAIN_MAX_S:
            return          # F2 is only polled between engine iterations: honour it here too

        aligned_by_xp = pending is not None and pending.get("after_xp")
        if pending is None:
            if _stall_check(ctx, cfg, now):
                return
            pending = _prep_next(ctx, cfg, tiles, dynamic)
            if pending is None:
                resync(ctx)
                continue
        if pending.get("full"):
            return          # next tick drops
        if pending.get("stop"):
            return
        if not aligned_by_xp and ctx.events is not None:
            ctx.events.wait_ticks(1, now_fn=_now, sleep_fn=_sleep)
            _sleep(random.uniform(*REENTRY_PHASE_MS) / 1000.0)

        # ---- FIRE: NOTHING BETWEEN THE XP AND THIS BUT THE REACTION DRAW -------------------
        # Every bridge read that used to sit here (stall check, inventory, hover, resolve) is
        # done in _prep_next during the previous cycle's wait. MEASURED g3t_live36: ~100ms of
        # reads between the drop and the log click put the rock press at phase 400-430, and
        # every one of those paid 4; USER's own presses land by ~260-400 and pay 3.
        controller.mark_busy(6.0)
        if pending.get("fixed"):
            # Resolve the rock NOW, in rotation order, never the one just clicked: one uncached
            # read per candidate (~20ms), and the first one in order is usually live.
            t_res = _now()
            last = data.get("g3t_last_index")
            start_at = 0 if last is None else (last + 1) % len(tiles)
            point, index = next_live_rock(cfg, start_at, pending.get("player") or {},
                                          exclude=last)
            if point is None:
                debug_log.log(CATEGORY, "no rock in the rotation is live -- waiting a tick")
                resync(ctx)
                pending = None
                continue
            tile = tuple(tiles[index])
            # NO re-aim and NO settle on the fixed rotation: the player is standing still when
            # the pair fires, so the point resolved 20ms ago is the point, and every ms between
            # the fletch and the press is exposure to the server boundary -- MEASURED
            # g3t_live51 recorder, three cycles whose mine started 3 ticks late (paid 7) with
            # both clicks inside the client tick.
            pending.update(tile=tile, index=index, fresh=point, refresh=None,
                           dead=(pending["dead"] or "") +
                           f"; rock {tile[0]},{tile[1]} resolved in {(_now() - t_res) * 1000:.0f}ms")
            data["g3t_last_index"] = index
        mark = ctx.events.mark() if ctx.events is not None else None
        if pending["far"] is not None:
            result = far_rock_cycle(ctx, cfg, pending["knife_point"], pending["log_point"],
                                    pending["far"])
        else:
            data["g3t_dead"] = pending["dead"]
            result = run_cycle(ctx, cfg, pending["log_point"], pending["fresh"],
                               rock_refresh=pending["refresh"])
            if pending["index"] is not None:
                data["g3t_index"] = next_rock_index(pending["index"], len(tiles))
        if result != "ok":
            resync(ctx)
            pending = None
            continue
        t_fired = _now()
        fired_tile = pending["tile"]

        # ---- DEAD TIME, tick +1: drop the previous cycle's ore and re-click the rock ----------
        if cfg.get("dropBetweenMines") and pending.get("tile") is not None:
            ore = newest_mined_item(cfg, controller.get_inventory() or [])
            if ore is not None:
                refresh = pending.get("refresh") or rock_refresh_target(cfg, pending["tile"],
                                                                        _ui_blockers())
                spent = drop_between_mines(ctx, cfg, ore, refresh)
                debug_log.log(CATEGORY, f"dropped {ore.get('name')} (slot {ore.get('slot')})"
                                        f"{' and re-clicked the rock' if cfg.get('dropReclick', True) else ''}"
                                        f" in {spent:.0f}ms")

        # ---- DEAD TIME, tick +2: the game's verdict on that click, then the NEXT rock, armed --
        if _stall_check(ctx, cfg, _now()):
            return
        pending = _prep_next(ctx, cfg, tiles, dynamic)

        # ---- USER: no other rock until the xp notifier ----------------------------------------
        if mark is None:
            _sleep(CYCLE_TICKS * tick_cycle.GAME_TICK_MS / 1000.0)
            continue
        xp = wait_for_mining_xp(ctx, mark, XP_WAIT_MAX_TICKS * tick_cycle.GAME_TICK_MS / 1000.0)
        if xp is None:
            debug_log.log(CATEGORY,
                          f"no Mining xp within {XP_WAIT_MAX_TICKS} ticks of the rock click on "
                          f"{fired_tile[:2]} -- the mine is not running, moving on")
            data["g3t_unpaid"] = data.get("g3t_unpaid", 0) + 1
            continue
        # The bridge stamps XP_GAINED with the tick of the packet that carried it (unlike the
        # recorder's labels, which run one low). MEASURED g3t_live34-37: every "paid 4" under a
        # +1 here had arrived 1360-1410ms after a rock click made ~420ms into its tick -- three
        # ticks after the fletch to the millisecond. Do not add one.
        t_seen = _now()
        # WHICH TICK THE DROP LANDED IN, AND THE FLOOR FROM ITS BOUNDARY -- with no tick-field
        # arithmetic. The xp is dispatched ~20ms BEFORE the GameTick that anchors the tick
        # model, so seen late in a tick it belongs to the boundary about to be pumped: wait for
        # that TICK event, then the fletch phase counts from it. Seen early in a tick, the
        # boundary already passed. MEASURED g3t_live57: comparing against the xp's own tick
        # field passed at once, the anchor was still the old tick, and every fletch went out
        # at phase 1-11 -- the exact miss of g3t_live54 (fletches at 21-23 all paid 7).
        landed = xp.get("tick")
        if ctx.events is not None:
            seen_tick = tick_cycle.current_tick(ctx.events, now=t_seen)
            seen_phase = tick_cycle.tick_phase_ms(ctx.events, t_seen)
            if seen_tick is not None and seen_phase is not None:
                if seen_phase > 400:
                    settle_deadline = _now() + 0.15
                    while _now() < settle_deadline:
                        ctx.events.pump(now=_now())
                        current = tick_cycle.current_tick(ctx.events, now=_now())
                        if current is None or current > seen_tick:
                            break
                        _sleep(0.003)
                    landed = seen_tick + 1
                else:
                    landed = seen_tick
        fired = data.get("g3t_fired_tick")
        pay = pay_ticks(data.get("g3t_fired_at"), t_seen)
        key = "g3t_paid3" if pay == 3 else "g3t_paid4" if pay == 4 else "g3t_paid_other"
        data[key] = data.get(key, 0) + 1
        debug_log.log(CATEGORY, f"+{xp.get('delta')} Mining xp on tick {landed}: paid "
                                f"{pay} ticks after the fletch on {fired} "
                                f"({(t_seen - t_fired) * 1000:.0f}ms after the rock click)")
        cycles = data.get("g3t_cycles", 0)
        if cycles and cycles % OUTCOME_SUMMARY_EVERY == 0 and data.get("g3t_summary_at") != cycles:
            data["g3t_summary_at"] = cycles
            p3, p4 = data.get("g3t_paid3", 0), data.get("g3t_paid4", 0)
            po, un = data.get("g3t_paid_other", 0), data.get("g3t_unpaid", 0)
            debug_log.log(CATEGORY, f"after {cycles} cycles: {p3} paid at 3 ticks, {p4} at 4, "
                                    f"{po} later, {un} never")
        if pending is not None:
            pending["after_xp"] = True
            if pending.get("count", 0) + 1 >= getattr(controller, "INVENTORY_SIZE", 28):
                # THE DROP THAT JUST LANDED FILLED THE LAST SLOT. The prep counted 27 before the
                # xp; firing now gets "Your inventory is too full to hold any more granite" and
                # a 14-tick wait for a mine that never runs -- MEASURED g3t_live51 recorder,
                # twice per inventory. Drop now, then re-prep.
                debug_log.log(CATEGORY, "that drop filled the inventory -- dropping before the "
                                        "next fletch")
                full_inventory_phase(ctx, cfg)
                pending = None
                continue
        lo, hi = XP_REACTION_MS
        target_phase = random.uniform(lo, hi)
        phase_now = tick_cycle.tick_phase_ms(ctx.events, _now()) if ctx.events is not None else None
        if phase_now is not None and phase_now < target_phase:
            pause = (target_phase - phase_now) / 1000.0
        else:
            pause = 0.0
        if pay != CYCLE_TICKS:
            pause += LATE_PAY_REST_TICKS * tick_cycle.GAME_TICK_MS / 1000.0
        if random.random() < cfg.get("missChance", 0.0):
            debug_log.log(CATEGORY, "deliberate miss -- reacting a tick late")
            pause += tick_cycle.GAME_TICK_MS / 1000.0
        _sleep(pause)


# How far into the next CLIENT tick the drop click goes. NOT near the boundary: the server's
# input cut-off sits ~100-150ms into the client tick (the same offset that let a phase-25
# fletch cancel the previous mine on 2026-09-08), so a drop at phase 5-30 was processed in the
# SAME server tick as the fletch and rock click and every cycle of g3t_live44 paid nothing.
# 150-230 puts the drop, and the re-click ~250ms after it, in the server tick after the pair.
DROP_BETWEEN_PHASE_MS = (260, 320)     # EXPERIMENT g3t_live47: 150-230 still lost ~a third of first rolls


def newest_mined_item(cfg, inv):
    """The most recently mined item: the highest-slot inventory entry matching minedItems."""
    names = [(m or "").lower() for m in (cfg.get("minedItems") or []) if m]
    mined = [i for i in (inv or []) if i.get("slot") is not None and i.get("screenX") is not None
             and any(n in (i.get("name") or "").lower() for n in names)]
    return max(mined, key=lambda i: i["slot"]) if mined else None


def drop_between_mines(ctx, cfg, ore, rock_refresh):
    """The tick after the fletch: shift-drop `ore`, then click the rock again, both inside that
    one tick. Returns ms spent, for the log. Nothing here is confirmed -- the full-inventory
    sweep remains the safety net for anything this misses."""
    scale = cfg.get("flightSpeedScale") or 1.0
    # PARK ON THE ORE BEFORE THE BOUNDARY, so the drop itself costs no flight -- MEASURED
    # g3t_live42: drop-plus-re-click as two flights took 400-570ms from the boundary and the
    # latest re-clicks cost the 3-tick roll.
    controller.move_to_human(ctx.ser, int(ore["screenX"]), int(ore["screenY"]),
                             aim_radius=AIM_RADIUS_PX, duration_scale=scale)
    if ctx.events is not None:
        ctx.events.wait_ticks(1, now_fn=_now, sleep_fn=_sleep)
    _sleep(random.uniform(*DROP_BETWEEN_PHASE_MS) / 1000.0)
    t0 = _now()
    if cfg.get("dropWithShift"):
        controller.key_down(ctx.ser, "SHIFT")
    try:
        tick_cycle.click_in_place(ctx.ser, now_fn=_now)
    finally:
        if cfg.get("dropWithShift"):
            controller.key_up(ctx.ser, "SHIFT")
    if cfg.get("dropReclick", True):
        point = rock_refresh() if rock_refresh else None
        if point is not None:
            tick_cycle.timed_click(ctx.ser, point, aim_radius=3, duration_scale=scale,
                                   now_fn=_now, refresh_target=rock_refresh, refresh_legs=1)
    return (_now() - t0) * 1000.0


def _prep_next(ctx, cfg, tiles, dynamic):
    """Everything the next fire needs, gathered DURING the previous cycle's xp wait: the slot
    points (re-read -- the panel moved mid-chain once), the game's verdict on the last rock
    click, the next rock, the knife armed on the log, and the rock's fresh point. Returns the
    pending dict, or None when there is nothing to fire at (the caller waits a tick)."""
    data = ctx.state.data
    inv = controller.get_inventory() or []
    out = {"far": None, "index": None, "full": False, "stop": False, "dead": None,
           "count": len(inv)}
    if len(inv) >= getattr(controller, "INVENTORY_SIZE", 28):
        out["full"] = True
        return out
    knife_item = find_item(inv, cfg["knifeName"])
    log_item = find_item(inv, cfg["logName"])
    if knife_item is None or log_item is None or log_item.get("screenX") is None \
            or knife_item.get("screenX") is None:
        out["stop"] = True          # the top of the next tick re-checks and stops if they are gone
        return out
    knife_point = (int(knife_item["screenX"]), int(knife_item["screenY"]))
    log_point = (int(log_item["screenX"]), int(log_item["screenY"]))
    out["knife_point"], out["log_point"] = knife_point, log_point
    out["player"] = controller.get_player() or {}    # read here, not in the critical path
    t0 = _now()

    if dynamic:
        player = controller.get_player() or {}
        mined = mined_tile_from_last_action(cfg, player, data.get("g3t_fired_tick"))
        if mined is not None:
            data["g3t_last_rock"] = mined
        rock = pick_rock(cfg, player, data.get("g3t_last_rock"))
        if rock is None:
            debug_log.log(CATEGORY, "no live rock in the scan -- waiting a tick")
            return None
        data["g3t_last_rock"] = (rock.get("x"), rock.get("y"))
        tile = (rock.get("x"), rock.get("y"), rock.get("plane", 0))
        if ((rock.get("distance") or 0) > int(cfg.get("stepMaxTiles") or 2)
                and not armed_on_log(cfg)):
            # A far rock is walked to FIRST (its click would spend a live selection as "Use
            # Knife -> Gem rocks" and walk nowhere), so this branch only runs with nothing
            # selected; armed, the ordinary cycle fletches now and the walk follows.
            out["far"], out["tile"] = rock, tile
            return out
    else:
        # FIXED ROTATION: the rock is chosen AT FIRE TIME (see next_live_rock); here only the
        # arming happens, and the fire step resolves the rock in order.
        t1 = _now()
        if not arm(ctx, cfg, knife_point, log_point):
            return None
        out.update(tile=None, refresh=None, fresh=None, fixed=True,
                   dead=f"arm {(_now() - t1) * 1000:.0f}ms")
        return out
    t1 = _now()
    if not arm(ctx, cfg, knife_point, log_point):
        return None
    t2 = _now()
    refresh = rock_refresh_target(cfg, tile, _ui_blockers())
    fresh = refresh()
    if fresh is None:
        debug_log.log(CATEGORY, f"rock {tile[:2]} vanished or slid behind the UI -- re-picking")
        return None
    out.update(tile=tile, refresh=refresh, fresh=fresh,
               dead=(f"rock {tile[0]},{tile[1]}; pick {(t1 - t0) * 1000:.0f}ms, arm "
                     f"{(t2 - t1) * 1000:.0f}ms, re-resolve {(_now() - t2) * 1000:.0f}ms"))
    return out


def _probe():
    """Read-only geometry probe, for capturing rockTiles and checking the setup without
    launching the routine. Imports controller only -- never the serial port -- so it is safe
    alongside a running routine, same posture as bank_memory.py's own __main__.

        python routines/granite_3t.py --probe
    """
    inv = controller.get_inventory() or []
    player = controller.get_player() or {}
    print(f"player at ({player.get('x')},{player.get('y')},{player.get('plane')})")
    for item in inv:
        slot = item.get("slot") if item.get("slot") is not None else "?"
        name = item.get("name") or "?"
        print(f"  slot {slot:>2}  {name:<20} "
              f"({item.get('screenX')},{item.get('screenY')})")
    cfg = site_config(DEFAULT_CONFIG)
    needle = (cfg.get("rockName") or "rock").lower()
    granite = [o for o in (controller.get_objects(interactable=True) or [])
               if needle in (o.get("name") or "").lower()]
    print(f"\n{len(granite)} {needle} object(s) in range (region {player.get('regionId')}):")
    for obj in sorted(granite, key=lambda o: o.get("distance", 9999)):
        print(f"  [{obj.get('x')}, {obj.get('y')}, {obj.get('plane')}]  "
              f"id={obj.get('id')} dist={obj.get('distance')} "
              f"screen=({obj.get('screenX')},{obj.get('screenY')})")
    print("\n" + check_geometry(cfg, inv)[1])


if __name__ == "__main__":
    _probe()
