"""3-tick granite mining ("3t4g") at the Cape Conch mine.

Design: docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md -- read it before
changing any timing constant here. NOT YET RUN LIVE; the spec's live-verification checklist is
the first session's worklist.

THE SHAPE, and why it is not gathering.py. Every other routine here is reactive: click, wait on
a pushed signal, confirm over HTTP. This one is a SCHEDULE. Per cycle, with the critical tick
numbered T and the next one pinned at T + CYCLE_TICKS:

    tick T+1 (dead)  select the knife; resolve the NEXT rock's screen point over HTTP;
                     fly to the log and PARK the cursor there
    tick T+2 (dead)  nothing -- the cursor rests on the log
    tick T+3 (crit)  click IN PLACE on the log (no flight) -> the 3-tick action fires;
                     ONE short flight to the rock; click

THE CYCLE IS COUNTED IN TICKS, NOT INFERRED. `run_cycle` records the bridge tick number its
critical click landed on and the next cycle targets `that + CYCLE_TICKS` through
`tick_cycle.at_tick`. An earlier draft used `at_phase`, which lands on the NEXT boundary
whenever it is called -- so the period was however long `prepare()`'s knife flight, log flight
and HTTP read happened to take, rounded up. A fast prepare gave a 2-tick cycle, which re-fires
knife-on-log before the skilling timer has expired and yields nothing at all.

Two clicks in one 600ms tick, but only ONE flight, because item selection in OSRS is
client-side: clicking the knife sends no packet, so the selection and the flight to the log are
both paid during the dead ticks. That is the entire reason this is affordable on a physical
mouse -- two aimed flights would be ~510ms median and over budget at p90, against ~347ms for
one. See the spec's "The insight that makes it feasible on our hardware".

The rocks' projections MOVE as the player walks between them, so the next rock's point is
re-resolved every cycle -- during the dead ticks, where 1200ms of slack absorbs an HTTP read.
The knife and log points are resolved once per chain: the inventory fills from low slots
upward, so with those two in the first slots the granite stacks above them and they do not
move.
"""

import math
import random
import time

import controller
import debug_log
import tick_cycle

CATEGORY = "granite3t"

DEFAULT_CONFIG = {
    "enabled": True,

    # Captured once at the mine -- see the probe at the bottom of this file.
    "rockTiles": [],            # [[x, y, plane], x4], cycled in order
    # Granite rocks share one object id per variant; the probe prints it alongside each tile.
    # get_object_at needs it, and matching by id rather than name is what keeps a DEPLETED rock
    # (a different id, offering no actions) from resolving as a live one.
    "rockId": None,

    "knifeName": "knife",
    "logName": "teak logs",
    # Never dropped, and required to be the ONLY unexpected thing in the inventory at startup.
    # gathering.py's mining profile keeps "pickaxe" for exactly this reason: nothing here checks
    # whether the pickaxe is worn or carried, and a carried one binned on the first full
    # inventory ends the run ~60s later with a stall message that names the wrong cause.
    "keepItems": ["pickaxe"],

    # Startup refuses above this. The flight is the whole cost of the critical tick, and it
    # scales with distance: settings.HM2_SLOPE_MS_PER_BIT is +120.5ms per DOUBLING.
    "maxReachPx": 220,
    # Where in the critical tick the log click lands. The honest budget for the critical tick is
    # 60 (phase) + ~145 (in-place click_human: an 85ms median hold plus its 30-90ms trailing
    # delay) + ~270 (p90 flight at this geometry) + ~145 (the rock click) = ~620ms, i.e. the p90
    # cycle does NOT fit and is expected to abandon at the room_for guard sometimes. The median
    # (60 + 145 + 170 + 145 = 520) does fit. A guess; fit it from the per-cycle occupancy this
    # routine logs.
    "logClickPhaseMs": 60,
    # Deliberate imperfection: a drawn fraction of cycles place the rock click one tick late.
    # A machine that never misses a 3-tick cycle is itself the signature -- EVENT_MOUSE_CLICK_V2
    # carries inter-press timing to the server at 1ms resolution (CLAUDE.md, telemetry).
    "missChance": 0.03,

    "fullInventory": "drop",
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


# THE WHOLE POINT OF THE ROUTINE'S NAME. The critical click of cycle N+1 is pinned to the tick
# CYCLE_TICKS after the critical click of cycle N -- not to "the next boundary after prepare()
# finished", which is what an at_phase-only cycle degrades to.
CYCLE_TICKS = 3

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

# Per-cycle occupancy bookkeeping -- spec live-checklist item 3 is fitting logClickPhaseMs from
# these numbers, and without them the first live session has no instrument for the one figure it
# exists to measure. Shape borrowed from the engine's own `[engine] loop:` summary line.
OCCUPANCY_WINDOW = 60
OCCUPANCY_SUMMARY_EVERY = 25


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def get_config(bridge_config):
    """DEFAULT_CONFIG only -- this routine has no ControllerConfig section, same as
    gemstone_crab.py and pest_control.py."""
    return DEFAULT_CONFIG


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


# The only thing the routine EXPECTS to accumulate. Anything else in the inventory at startup is
# a thing USER put there and would lose.
MINED_ITEM = "granite"


def check_inventory_is_clean(cfg, inv):
    """(ok, message). Refuse to start when the inventory holds anything the drop would bin.

    The keep list alone is not enough protection: it only knows the names it was told about, and
    the failure it guards is silent and delayed -- the item is dropped on the floor of a busy
    mine about a minute in, mining then earns nothing, and the XP stall watch stops the run 60s
    later blaming contested rocks. Naming the offending items and refusing is the honest
    response; `keepItems` is the escape hatch."""
    allowed = keep_names(cfg) + (MINED_ITEM,)
    unexpected = sorted({(item.get("name") or "?") for item in (inv or [])
                         if not any(a in (item.get("name") or "").lower() for a in allowed)})
    if unexpected:
        return False, ("the inventory holds items this routine would DROP on the first full "
                       f"inventory: {', '.join(unexpected)} -- bank them, or add them to "
                       "keepItems")
    return True, "inventory holds only the knife, the log, keepItems and granite"


def rock_points(cfg):
    """Live screen points for every configured rock tile, or None when any will not resolve.

    None means "cannot aim", never "no rock there" -- the caller refuses rather than guessing."""
    points = []
    for tile in cfg.get("rockTiles") or []:
        x, y, plane = tile[0], tile[1], tile[2]
        obj = controller.get_object_at(x, y, plane, cfg.get("rockId"))
        if not obj or obj.get("screenX") is None:
            return None
        points.append((int(obj["screenX"]), int(obj["screenY"])))
    return points or None


def check_geometry(cfg, inv):
    """(ok, message). The message ALWAYS carries the measurement, pass or fail, so a refusal
    tells USER how much to zoom in rather than merely that something is wrong."""
    knife = find_item(inv, cfg["knifeName"])
    if knife is None:
        return False, f"no {cfg['knifeName']!r} in the inventory"

    logs = log_count(inv, cfg["logName"])
    if logs == 0:
        return False, f"no {cfg['logName']!r} in the inventory"
    if logs > 1:
        return False, (f"{logs} x {cfg['logName']!r} in the inventory -- there must be exactly "
                       f"one, or knife-on-log opens a Make-X interface and eats a tick")

    log = find_item(inv, cfg["logName"])
    if log.get("screenX") is None:
        return False, f"{cfg['logName']!r} has no screen position (inventory tab closed?)"

    if len(cfg.get("rockTiles") or []) < 2:
        return False, "fewer than 2 rockTiles configured -- the rotation needs the movement "\
                      "stall between rocks to buy the second roll"

    points = rock_points(cfg)
    if points is None:
        return False, "at least one rock in rockTiles does not resolve on screen"

    reach = max(math.hypot(px - log["screenX"], py - log["screenY"]) for px, py in points)
    flight = tick_cycle.estimate_flight_ms(reach)
    limit = cfg["maxReachPx"]
    msg = (f"furthest rock is {reach:.0f}px from the log (limit {limit}px), "
           f"~{flight:.0f}ms median flight")
    if reach > limit:
        return False, msg + " -- zoom in, shrink the client, or turn the camera south"
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


def _startup_check(ctx, cfg):
    """True once the startup assertions have PASSED. False means either "deferred, ask again
    next tick" or "refused and stopped" -- tick() returns on both.

    An empty inventory or a stale tick clock is "cannot tell yet", not "failed": both are what
    the login screen looks like. Only STARTUP_DEFER_MAX_S of that turns into a stop, and the
    stop names which of the two could not be read."""
    data = ctx.state.data
    now = _now()
    since = data.setdefault("g3t_startup_since", now)

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
    debug_log.log(CATEGORY, f"ready: {len(cfg['rockTiles'])} rocks, "
                            f"{data['g3t_logs']} log(s)")
    return True


def next_rock_index(index, count):
    return (index + 1) % count if count else 0


def prepare(ctx, cfg, knife_point, log_point, rock_tile):
    """The DEAD-TICK half of a cycle. Returns the next rock's live screen point, or None.

    Three things happen here and the order is load-bearing:
      1. Click the knife. In OSRS this is a client-side SELECTION and sends no packet, which is
         exactly why it can live outside the critical tick.
      2. Park the cursor on the log, so the critical tick's first click costs no flight. This is
         UNCONDITIONAL so run_cycle's click_in_place assumption ("cursor is on the log") holds
         even if the rock resolve fails transiently.
      3. Re-resolve the rock's screen point over HTTP. The projection moves every cycle because
         the PLAYER moves between rocks; the 1200ms of dead time is where that read is
         affordable.
    """
    tick_cycle.timed_click(ctx.ser, knife_point, aim_radius=AIM_RADIUS_PX, now_fn=_now)
    controller.move_to_human(ctx.ser, int(log_point[0]), int(log_point[1]),
                             aim_radius=AIM_RADIUS_PX)

    x, y, plane = rock_tile[0], rock_tile[1], rock_tile[2]
    obj = controller.get_object_at(x, y, plane, cfg.get("rockId"))
    if not obj or obj.get("screenX") is None:
        return None

    return int(obj["screenX"]), int(obj["screenY"])


def _flight_budget(from_point, to_point):
    """Median flight PLUS the click that follows it, in ms.

    estimate_flight_ms's own docstring says it is a MEDIAN and the caller must add a margin.
    Booking only the flight (the first draft) let clicks through that then spent a further
    50-145ms hold and a 30-90ms trailing delay, landed in the NEXT tick, and were recorded as
    "ok" -- precisely the failure the room_for guard exists to prevent. tick_cycle.CLICK_COST_MS
    is the upper end of that pair, deliberately."""
    dist = math.hypot(to_point[0] - from_point[0], to_point[1] - from_point[1])
    return tick_cycle.estimate_flight_ms(dist) + tick_cycle.CLICK_COST_MS


def _deliberate_miss(cfg):
    """DELIBERATE IMPERFECTION. A human running 3t4g misses cycles and their rate visibly dips
    and recovers; a machine that never misses is itself the signature, and per CLAUDE.md's
    telemetry notes EVENT_MOUSE_CLICK_V2 carries inter-press timing to the server at 1ms
    resolution independently of everything else. This is the honest replacement for the engine's
    MISTAKE_MISCLICK_CHANCE, which is turned OFF everywhere in this routine: a simulated
    near-miss plus its corrective reclick would wreck the cycle outright, and in the inventory it
    would drop the log."""
    return random.random() < cfg.get("missChance", 0.0)


def _log_occupancy(ctx, result, phase, in_place_ms, flight_ms, occupancy_ms):
    """One line per cycle, plus a periodic summary -- the instrument the spec's live-checklist
    item 3 ("fit logClickPhaseMs from the measured ms per cycle") needs and which nothing
    previously produced: click_in_place and timed_click both returned elapsed ms and every call
    site threw them away. Summary shape borrowed from the engine's own `[engine] loop:` line."""
    debug_log.log(CATEGORY,
                  f"cycle {result}: opened at phase {phase:.0f}ms, in-place {in_place_ms:.0f}ms, "
                  f"flight+click {flight_ms:.0f}ms, occupancy {occupancy_ms:.0f}ms "
                  f"of {tick_cycle.GAME_TICK_MS}ms")
    data = ctx.state.data
    history = data.setdefault("g3t_occupancy", [])
    history.append(occupancy_ms)
    del history[:-OCCUPANCY_WINDOW]
    count = data.get("g3t_cycles", 0) + 1
    data["g3t_cycles"] = count
    if count % OCCUPANCY_SUMMARY_EVERY == 0 and history:
        ordered = sorted(history)
        debug_log.log(CATEGORY,
                      f"occupancy over the last {len(ordered)} of {count} cycles: "
                      f"median {ordered[len(ordered) // 2]:.0f}ms, max {ordered[-1]:.0f}ms")


def _open_window(ctx, cfg, target_tick):
    """Sleep to this cycle's click phase. Returns (phase, tick_number, opened_at).

    `phase` is None (unusable clock) or the string "missed" (the target tick is already past)
    on failure, in which case the other two are None. When `target_tick` is None this is the
    FIRST cycle of a chain -- there is no previous critical tick to count from, so it lands on
    the next boundary via at_phase and reports whichever tick that turned out to be.

    One clock read, shared between the tick number and the window's open time: they describe the
    same instant, and reading twice would put a stray draw between them."""
    if target_tick is None:
        phase = tick_cycle.at_phase(ctx.events, cfg["logClickPhaseMs"],
                                    now_fn=_now, sleep_fn=_sleep)
    else:
        phase = tick_cycle.at_tick(ctx.events, target_tick, cfg["logClickPhaseMs"],
                                   now_fn=_now, sleep_fn=_sleep)
    if phase is None or phase == "missed":
        return phase, None, None
    opened_at = _now()
    return phase, tick_cycle.current_tick(ctx.events, now=opened_at), opened_at


def run_cycle(ctx, cfg, knife_point, log_point, rock_point, target_tick=None):
    """The CRITICAL TICK. "ok" | "miss" | "desync" | "no_clock".

    No HTTP between the two clicks -- that is the invariant the whole module exists to protect.

    `target_tick` is the bridge tick number the LOG click must land on, or None for the first
    cycle of a chain. On "ok" the tick that was actually fired on is recorded in
    ctx.state.data["g3t_fired_tick"] and tick() pins the next cycle at that + CYCLE_TICKS; every
    other result clears it, so the next cycle re-enters on a clean boundary instead of
    inheriting a period that is now wrong.

    THE ORDER WITHIN THE TICK IS FIXED -- log click, then rock click -- AND CANNOT BE FLIPPED BY
    CONFIG. A `rockClickTickOffset` of -1 (rock click first) was implemented and deleted: it
    cannot work while a knife selection is live, because an OSRS item selection is consumed by
    the next click on ANY target, so clicking the rock first issues "Use Knife -> Granite rock"
    (which does nothing), spends the selection, and leaves the following in-place log click a
    bare left-click with nothing selected -- knife-on-log never fires either and the cycle
    yields nothing while being recorded "ok". See _consume_knife_selection, which states the
    same rule. If a live session shows this ordering is wrong, the fix is to restructure
    prepare() so the knife is selected AFTER the rock click; that is a design change, not a
    config flip."""
    phase, fired, opened_at = _open_window(ctx, cfg, target_tick)
    if phase is None:
        return "no_clock"
    if phase == "missed":
        debug_log.log(CATEGORY, f"desync: tick {target_tick} was already past when the cycle "
                                f"opened -- resyncing rather than firing a tick late")
        return "desync"

    window = tick_cycle.Window(opened_at=opened_at, phase_ms=phase)
    in_place_ms = tick_cycle.click_in_place(ctx.ser, now_fn=_now)

    if _deliberate_miss(cfg):
        debug_log.log(CATEGORY, "deliberate miss -- placing the rock click a tick late")
        if ctx.events is not None:
            ctx.events.wait_ticks(1, now_fn=_now, sleep_fn=_sleep)
        flight_ms = tick_cycle.timed_click(ctx.ser, rock_point, aim_radius=3, now_fn=_now)
        _log_occupancy(ctx, "miss", phase, in_place_ms, flight_ms, window.spent_ms(_now()))
        return "miss"

    need = _flight_budget(log_point, rock_point)
    now = _now()
    if not window.room_for(need, now):
        # A click that slips into the NEXT tick is worse than no click at all: it starts the
        # next mine at the wrong phase and desyncs every cycle after it. The flight is simply
        # NOT STARTED -- there is nothing to abort, and request_movement_abort() is only legal
        # from inside a flight loop anyway (the v3 firmware `X` contract).
        debug_log.log(CATEGORY,
                      f"desync: {window.spent_ms(now):.0f}ms of the tick spent with "
                      f"~{need:.0f}ms of flight+click still needed -- abandoning the rock click")
        _log_occupancy(ctx, "desync", phase, in_place_ms, 0.0, window.spent_ms(now))
        return "desync"

    flight_ms = tick_cycle.timed_click(ctx.ser, rock_point, aim_radius=3, now_fn=_now)
    ctx.state.data["g3t_fired_tick"] = fired
    _log_occupancy(ctx, "ok", phase, in_place_ms, flight_ms, window.spent_ms(_now()))
    return "ok"


def _inventory_full():
    return len(controller.get_inventory() or []) >= getattr(controller, "INVENTORY_SIZE", 28)


def full_inventory_phase(ctx, cfg):
    """True when this tick was spent dropping. Knife + log leaves 26 slots, so this comes due
    roughly every 26 cycles (~60-70s).

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
        # XP stall watch could never catch it. Unreachable while knifeName/logName are the
        # fixed defaults and granite matches neither, but this is the one path that bypasses
        # the routine's only terminal condition.
        ctx.state.trigger_safety_stop(
            "inventory full but every item is on the keep list -- nothing can be dropped")
        return True
    debug_log.log(CATEGORY, f"inventory full -- dropping {len(items)} granite")
    if not controller.drop_items(ctx.ser, items):
        ctx.state.trigger_safety_stop("inventory still full after dropping the granite")
    return True


CHAIN_MAX_S = 45.0      # hand control back to the engine at least this often


def resync(ctx):
    """Give up on the current cycle and re-enter on a clean tick boundary.

    USER's decision, and the reason there is no desync ceiling: a missed tick is an ordinary
    event in 3-tick mining -- a human's rate dips and recovers -- so this never stops the run.
    The XP stall watch in tick() is the only thing that does.

    Clearing the movement abort is cheap insurance, kept deliberately: nothing in this routine
    SETS it any more (a desync declines to start the flight rather than aborting one mid-air --
    the firmware's `X` flush is only legal from inside move()'s own flight loop), but the flag
    is process-global and STICKY, so anything else that leaves it set would silently kill every
    flight for the rest of the session."""
    controller.clear_movement_abort()
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


def _consume_knife_selection(ctx):
    """prepare() has already SELECTED the knife and parked the cursor on the log, so a cycle
    abandoned after it leaves the selection live -- and the NEXT cycle's knife click would then
    DESELECT it, silently wasting that cycle too (M13). Firing knife-on-log consumes it: the
    cursor is already on the log, so this costs one in-place click and no flight, and starting a
    3-tick timer with no rock click is harmless because the caller resyncs anyway."""
    tick_cycle.click_in_place(ctx.ser, now_fn=_now)


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


def _run_chain(ctx):
    cfg = ctx.state.config
    data = ctx.state.data

    if data.get("g3t_startup_pending") and not _startup_check(ctx, cfg):
        return

    if full_inventory_phase(ctx, cfg):
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

    knife = find_item(inv, cfg["knifeName"])
    log = find_item(inv, cfg["logName"])
    if knife is None or log is None:
        ctx.state.trigger_safety_stop(
            f"the {cfg['knifeName']!r} or {cfg['logName']!r} is gone from the inventory")
        return

    # Checked once per chain (so within CHAIN_MAX_S, not "in seconds"). The constraint this
    # module exists to protect is the CRITICAL TICK, not the cycle: the chain loop below does
    # read /inventory once per cycle for the full-inventory check, which is affordable because
    # it happens between cycles. What must never happen is an HTTP read between the log click
    # and the rock click. USER says teak/mahogany are not consumed by knife-on-log; if that is
    # wrong, the worst case is one chain of clicks landing on an empty slot (harmless with a
    # knife selected) before this stops the run. Spec live-checklist item 1 verifies exactly it.
    logs = log_count(inv, cfg["logName"])
    if logs < (data.get("g3t_logs") or logs):
        ctx.state.trigger_safety_stop(
            f"the {cfg['logName']!r} count fell {data['g3t_logs']} -> {logs}: knife-on-log IS "
            f"consuming the log, so this routine needs a supply loop it does not have")
        return
    data["g3t_logs"] = logs

    knife_point = (int(knife["screenX"]), int(knife["screenY"]))
    log_point = (int(log["screenX"]), int(log["screenY"]))
    tiles = cfg["rockTiles"]

    if not tiles:
        ctx.state.trigger_safety_stop("no rockTiles configured -- capture them with the probe")
        return

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

    # Cleared per chain: ticks pass between chains, so a fired tick from the previous one is no
    # longer a period this cycle can be counted from. The first cycle re-enters via at_phase.
    data["g3t_fired_tick"] = None
    started = _now()
    while True:
        now = _now()
        if ctx.state.safety_stopped or now - started >= CHAIN_MAX_S:
            return
        if _stall_check(ctx, cfg, now):
            return
        if _inventory_full():
            return          # next tick drops

        # Hold off the idle drift for the whole cycle: it is documented in CLAUDE.md as having
        # fired mid-work before, and here it would drag the cursor off the parked log and break
        # the cycle with nothing in the log to say so.
        controller.mark_busy(3.0)

        index = data.get("g3t_index", 0) % len(tiles)
        rock_point = prepare(ctx, cfg, knife_point, log_point, tiles[index])
        if rock_point is None:
            debug_log.log(CATEGORY, f"rock {tiles[index]} did not resolve -- resyncing")
            _consume_knife_selection(ctx)
            data["g3t_fired_tick"] = None
            resync(ctx)
            data["g3t_index"] = next_rock_index(index, len(tiles))
            continue

        fired = data.get("g3t_fired_tick")
        result = run_cycle(ctx, cfg, knife_point, log_point, rock_point,
                           None if fired is None else fired + CYCLE_TICKS)
        data["g3t_index"] = next_rock_index(index, len(tiles))
        if result != "ok":
            data["g3t_fired_tick"] = None
            resync(ctx)



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
    granite = [o for o in (controller.get_objects(interactable=True) or [])
               if "granite" in (o.get("name") or "").lower()]
    print(f"\n{len(granite)} granite object(s) in range:")
    for obj in sorted(granite, key=lambda o: o.get("distance", 9999)):
        print(f"  [{obj.get('x')}, {obj.get('y')}, {obj.get('plane')}]  "
              f"id={obj.get('id')} dist={obj.get('distance')} "
              f"screen=({obj.get('screenX')},{obj.get('screenY')})")
    cfg = dict(DEFAULT_CONFIG)
    if cfg.get("rockTiles"):
        print("\n" + check_geometry(cfg, inv)[1])


if __name__ == "__main__":
    _probe()
