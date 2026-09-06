# routines/deep_sea_trawling.py
#
# Deep sea trawling: park the boat at a shoal and OPERATE the trawling net once per game tick.
#
# Why one click per tick: the game rolls for a catch every 3 ticks while a net is operating in
# range, but clicking the net every tick makes it roll every 2 ticks -- a flat +50% catch rate
# (OSRS Wiki, Deep sea trawling rev 15297127, "1-tick clicking"). That burst is the whole point
# of this routine, and it is why the net is clicked IN PLACE (controller.click_human, ~92ms,
# measured in giants_foundry) rather than re-flown per click: a fresh click_entity flight costs
# ~1s and cannot hold a 0.6s cadence at all.
#
# Written 2026-09-06 from the Wiki, the decoded cache (dump 5bc2ff1e) and runelite-api 1.12.38.
# There is NO human recording and NOTHING here has been run live -- see the design spec's
# "Live-verification checklist", which is the first session's worklist:
#   docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md
#
# Game facts this rests on:
#   loc 59755 "Rope trawling net"  op1=Operate op2=Raise op3=Lower op4=Collect-from op5=Modify
#       (59756/59757/59758 linen/hemp/cotton; 59759..59766 the 3x8 port/starboard variants)
#   loc 59734 "Giant krill shoal"  a 6x6 object out at sea; 59740 the shimmering shoal that
#       replaces it 10% of the time with identical movement and stop timers
#   inv 968   the shared 250-fish net container, per player  -> GET /trawling
#   Every roll grants a small SAILING xp drop; a SUCCESSFUL roll also grants FISHING xp. The
#       Sailing drop is therefore a pushed, sub-tick HEARTBEAT proving "the net is operating in
#       range" -- it is the range test, not distance to a moving 6x6 object.
#   Fish in the nets are DISCARDED on disembark, teleport, world-hop or a forced logout. This
#       routine therefore never hops worlds, never teleports, and collects before every break.
#
# Preconditions (checked by _startup_checks, each with its own one-line stop reason):
#   - standing on your boat, which has a trawling net built
#   - a CREWMATE on the helm. Helmsmanship lets a crewmate hold the helm so the player can stand
#     at another facility while the player still clicks the heading (Wiki, Crew Management).
#     Without one the player must stand at the helm to steer and cannot trawl at the same time.
#   - Fishing level >= the configured shoal's requirement
#   - the boat is on the configured route
#
# Run with:
#     python controller.py routines/deep_sea_trawling.py

import time
from typing import NamedTuple, Optional

import bridge_events
import controller
import debug_log
import outcome_waits
import sailing
import trawling_routes

# --------------------------------------------------------------------------- config

DEFAULT_CONFIG = {
    "shoal": "giant_krill",
    "route": "great_sound",
    # The distance past which derive_phase decides to re-approach -- a HYSTERESIS THRESHOLD, NOT
    # a parking distance (F-I7). sailing.sail_to is aimed at the shoal's own tile and returns as
    # soon as it is inside _dynamic_brake_tiles(), floored at SAIL_ARRIVAL_TOLERANCE (5), so the
    # boat parks 0-5 tiles from the shoal centre whatever this is set to. UNVERIFIED (spec
    # checklist #3): if this guess is too LARGE, derive_phase keeps answering "trawl" while the
    # heartbeat says we are out of range, and the approach recovery the APPROACH_MAX_TRIES stop
    # message names is never actually attempted. The heartbeat, not this number, decides whether
    # we are catching.
    "trawlRange": 8,
    "netFullAt": 240,            # collect here, not at the 250 cap
    "collectTo": "cargo",        # "cargo" | "inventory"
    "stopWhenCargoFull": True,
    # F-C3: without this key the engine's `config.get("breaks", {})` is empty, ControllerState.
    # break_due({}) is False at its first line, and so is maybe_toggle_break -- i.e. the routine
    # takes NO breaks at all (an anti-detection regression on an hours-long activity) and
    # before_break, the answer to the discard-on-forced-logout hazard, is unreachable dead code.
    # Same shape and key names as mahogany_homes / giants_foundry, the other hours-long routines.
    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {},
}

# The engine's safety check runs BEFORE tick(), so an inventory-full stop would win the race
# against our own collect. Same reason mining.py forces it off.
_FORCED_SAFETY = {"stopOnInventoryFull": False}

# Net depth (19206/19208) and "carrying cargo" (19134). The engine posts these on load and after
# every login; on_event and the burst both key on them.
VARBIT_SUBSCRIPTIONS = [19206, 19208, 19134]

NET_NAME_FRAGMENT = "trawling net"
NET_OBJECT_IDS = tuple(range(59755, 59767))     # rope/linen/hemp/cotton x 2x5 + 3x8 port/starboard

# --------------------------------------------------------------------------- clock indirection

def _now():
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


# --------------------------------------------------------------------------- state


class TrawlSnapshot(NamedTuple):
    # px is None means THE CLIENT TOLD US NOTHING this read (a dropped /player, a LOADING frame,
    # the login screen) -- it is "cannot tell", never a fact about the world. tick_with_snapshot
    # does nothing at all on such a snapshot, and _startup_checks defers on it (F-C2).
    px: Optional[int]
    py: Optional[int]
    plane: Optional[int]
    # None when /sailing could not be read -- "cannot tell", NOT "stopped" (F-M6).
    boat_speed: Optional[float]
    navigating: bool
    net: Optional[dict]
    net_depth: Optional[int]
    shoal: Optional[dict]
    shoal_distance: Optional[float]
    net_fish: Optional[int]
    cargo_used: Optional[int]
    cargo_capacity: Optional[int]
    ticks_since_sailing_xp: float
    in_route_area: bool


def get_config(bridge_config):
    """Plain DEFAULT_CONFIG -- this routine reads nothing from GET /config, so wiring it into the
    plugin needs only the RoutineChoice enum entry and controller.ROUTINE_FILES, no
    ControllerConfig section (CLAUDE.md, "The routine contract")."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["safety"] = dict(cfg.get("safety") or {})
    cfg["safety"].update(_FORCED_SAFETY)
    cfg["breaks"] = dict(cfg.get("breaks") or {})
    return cfg


def shoal_ids(cfg):
    return trawling_routes.all_shoal_ids(cfg["shoal"])


BOAT_MOVING_SPEED = 0.05     # tiles/tick below which the boat counts as stopped


def derive_phase(snap: TrawlSnapshot, cfg: dict) -> str:
    """Pure. Every phase comes from LIVE state, never from a stored step -- ctx.state.data is
    wiped on restart while the boat's position, its nets and its cargo are not."""
    if snap.net is None or snap.net_fish is None:
        return "lost"

    if snap.cargo_used is not None and snap.cargo_capacity is not None \
            and snap.cargo_used >= snap.cargo_capacity and cfg["stopWhenCargoFull"]:
        # F-I1: a Collect-from into a FULL hold moves NOTHING, so with collectTo == "cargo" the
        # collect can never confirm -- it would burn COLLECT_TRIES and stop with the wrong
        # reason while up to 239 fish sat in the nets. The hold being full is the end of the
        # run: done_phase collects what the INVENTORY can still take, raises the net and stops.
        # Collecting to the inventory is the one destination a full hold does not block.
        if snap.net_fish > 0 and cfg["collectTo"] == "inventory":
            return "collect"
        return "done"

    if snap.net_fish >= cfg["netFullAt"]:
        return "collect"

    if snap.shoal is None:
        # F-M4: being outside the route area is NOT "we are not aboard a boat" -- travel_phase
        # owns that case and has the message that names the route. Routing it to "lost" here
        # made travel_phase's own branch dead code AND told the user the wrong thing.
        return "travel"

    if snap.boat_speed is None or snap.boat_speed > BOAT_MOVING_SPEED:
        # F-M6: an unreadable /sailing must not read as "stopped". Preferring approach is the
        # safe direction -- it re-parks rather than trawling while the boat drifts.
        return "approach"
    if snap.shoal_distance is None or snap.shoal_distance > cfg["trawlRange"]:
        return "approach"

    if not snap.net_depth:
        return "lower"

    return "trawl"


# --------------------------------------------------------------------------- reading the world


def _obj_distance(obj):
    """An object's reported distance, or +inf when it carries none -- never 0, which would make
    an unmeasurable object beat every real one."""
    dist = (obj or {}).get("distance")
    return float(dist) if isinstance(dist, (int, float)) else float("inf")


def find_net(objects):
    """The trawling net facility on our own deck: the NEAREST match, ids before names.

    Matched by ID first because /objects reports ObjectComposition.getName() raw, so a name can
    carry a <col=...> tag (the shoal's does), and the 12 net variants (4 tiers x 2x5 /
    3x8-port / 3x8-starboard) are all tabulated; the name fragment is only a fallback for a
    variant the cache dump did not have.

    NEAREST, not first-in-scan-order (F-I2): ObjectProvider.walkWorldViewRecursively iterates
    every WorldEntity in the loaded scene, i.e. EVERY boat, not just ours. Deep sea trawling is
    a shared ocean and other players park at the same shoal, so a first-match pick can return
    THEIR net -- and then the whole burst, lower_phase and collect_phase aim at their boat with
    nothing erroring, presenting as "the heartbeat never starts". Mirrors find_shoal below."""
    by_id = [o for o in objects or [] if o.get("id") in NET_OBJECT_IDS]
    if by_id:
        return min(by_id, key=_obj_distance)
    by_name = [o for o in objects or [] if NET_NAME_FRAGMENT in (o.get("name") or "").lower()]
    if by_name:
        return min(by_name, key=_obj_distance)
    return None


def find_shoal(objects, cfg, px, py):
    """The nearest shoal of the configured type, plus its distance from the player. Returns
    (None, None) when none is in the scene -- the shoal is a 6x6 loc out on the ocean and
    /objects walks the top-level scene as well as the boat's nested WorldView, so both the net
    and the shoal come out of one call in comparable top-level world coordinates."""
    wanted = shoal_ids(cfg)
    best = None
    best_dist = None
    for obj in objects or []:
        if obj.get("id") not in wanted:
            continue
        if px is None or obj.get("x") is None:
            continue
        dist = ((obj["x"] - px) ** 2 + (obj["y"] - py) ** 2) ** 0.5
        if best_dist is None or dist < best_dist:
            best, best_dist = obj, dist
    return best, best_dist


def _built_net_depth(trawling):
    """The depth of the net slot that is actually built. `nets` reports both facility slots and
    an unbuilt one reads hotspotId 0 -- reading slot 0 blindly would call a built starboard net
    'raised' forever."""
    for entry in (trawling.get("nets") or []):
        if entry.get("hotspotId"):
            return entry.get("depth")
    return None


def _log_net_entries_once(ctx, trawling):
    """Log the raw `nets` entry the first time a run sees one (F-M3).

    Net depth is used as a BOOLEAN -- derive_phase treats depth == 0 as raised, lower_phase
    confirms on bool(_built_net_depth(...)) -- and spec live-verify #5 says the real values are
    unknown. If "raised" is actually a nonzero value the burst runs with the net up and the only
    symptom is out_of_range; if depth encodes shallow/deep rather than up/down, lower_phase can
    never confirm. One log line makes either failure visible immediately instead of costing a
    session."""
    state = getattr(ctx, "state", None)
    data = getattr(state, "data", None)
    if data is None or data.get("dst_nets_logged") or not trawling.get("nets"):
        return
    data["dst_nets_logged"] = True
    debug_log.log("trawling", f"LIVE-VERIFY net depth: nets={trawling.get('nets')!r} "
                              f"netFish={trawling.get('netFish')!r}")


def read_snapshot(ctx, cfg, ticks_since_sailing_xp: float = 0.0) -> TrawlSnapshot:
    """One record of everything a phase decision needs.

    Every field can come back None: controller.api_get has a 0.3s timeout and returns None on
    ANY failure, so one dropped response makes the world look empty. `px is None` is this
    routine's single "the client told us nothing" signal (F-C2) -- callers treat such a snapshot
    as "cannot tell" and do nothing, rather than concluding we are not aboard a boat."""
    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")

    sail = controller.api_get("/sailing") or {}
    objects = controller.get_objects() or []
    trawling = controller.get_trawling() or {}
    _log_net_entries_once(ctx, trawling)

    net = find_net(objects)
    shoal, shoal_dist = find_shoal(objects, cfg, px, py)
    cargo = trawling.get("cargo") or {}
    speed = sail.get("boatSpeed")

    return TrawlSnapshot(
        px=px, py=py, plane=plane,
        boat_speed=float(speed) if isinstance(speed, (int, float)) else None,
        navigating=bool(sail.get("navigating")),
        net=net,
        net_depth=_built_net_depth(trawling),
        shoal=shoal, shoal_distance=shoal_dist,
        net_fish=trawling.get("netFish"),
        cargo_used=cargo.get("used"), cargo_capacity=cargo.get("capacity"),
        ticks_since_sailing_xp=ticks_since_sailing_xp,
        in_route_area=(px is not None and trawling_routes.in_route_area(
            cfg["shoal"], cfg["route"], px, py)),
    )


def net_refresh_target(net):
    """A refresh_target for the net. The boat is stopped while trawling, so the net's top-level
    world tile is stable and /object/at can re-resolve its projection as the camera moves."""
    return controller.object_refresh_target(net["x"], net["y"], net.get("plane", 0), net["id"])


# --------------------------------------------------------------------------- the burst

# Ticks without a Sailing XP drop before we conclude the shoal has left. The game rolls every 3
# ticks unclicked and every 2 clicked, so 6 is two missed rolls' worth of slack -- tight enough
# to start chasing within ~4s of the shoal moving, loose enough to ride a dropped UDP packet.
RANGE_LOST_TICKS = 6
# With no pushed heartbeat, "still catching" is the net count growing. Catches are stochastic
# (~10.5% per roll at Fishing 75, one roll per 2 ticks), so this window has to be much wider
# than RANGE_LOST_TICKS: 100 ticks is ~50 rolls, and the chance of a genuine catch drought that
# long is under 0.5%.
NO_EVENTS_STALL_TICKS = 100
BURST_MAX_S = 120.0          # longer than any shoal's stop (krill 90s); a ceiling, not a timer
AIM_REFRESH_TICKS = 12       # re-aim on a schedule even if nothing looks like it moved
NET_POLL_TICKS = 8           # how often the burst spends an HTTP read on /trawling.netFish
CHECK_TICKS = 5              # how often _dismiss_dialogue_if_open is polled -- _interrupted
                              # (pause / safety stop / break_due) itself runs every tick, not
                              # gated by this
CLICK_PHASE_S = (0.02, 0.12)  # jitter after the tick boundary so clicks are not all at +0ms


def _walk_to_net(ctx, net):
    """Stand at the net. The deck of a sloop is a few tiles across, so this is one short click,
    not a route. Returns True once the click was issued -- the game queues the interaction and
    performs it on arrival, so the burst's first Operate can follow immediately."""
    return bool(controller.click_entity(ctx.ser, net, refresh_target=net_refresh_target(net),
                                        simulate_mistakes=False))


def _dismiss_dialogue_if_open(ctx):
    """The engine's own stray-dialogue check cannot run while the burst holds the tick, and a
    level-up ('click here to continue') swallows every click behind it. Returns True if one was
    dismissed."""
    state = controller.get_state() or {}
    if state.get("dialogOpen"):
        controller.key_tap(ctx.ser, "SPACE")
        return True
    return False


def _interrupted(ctx, cfg):
    state = ctx.state
    if getattr(state, "safety_stopped", False) or getattr(state, "paused", False):
        return True
    try:
        # break_due TAKES the breaks config (the corrupted_gauntlet crash of 2026-09-06: it is
        # not a no-arg call). The `or {}` is load-bearing, not decorative: break_due(None) raises
        # AttributeError rather than treating "no breaks configured" as falsy, and a caller can
        # still hand us a cfg without the key. DEFAULT_CONFIG does carry one -- see F-C3 there.
        return bool(state.break_due(cfg.get("breaks") or {}))
    except Exception:
        return False


def trawl_burst(ctx, cfg, net) -> str:
    """Operate the net once per game tick for as long as the shoal stays in range.

    Returns "out_of_range" (the heartbeat stopped -- the shoal has moved on), "net_full",
    "interrupted" (pause / safety stop / a break came due), "net_click_failed" (the opening
    click on the net was refused or withheld -- counted by trawl_phase, F-M11) or "budget".

    It also records, in ctx.state.data["dst_burst_progress"], whether this burst produced ANY
    evidence of catching -- a Sailing roll, or the net's fish count growing. trawl_phase's
    approach ceiling keys on that rather than on the return value (F-C1): out_of_range is the
    NORMAL, designed end of every healthy stop (a krill stop is 150 ticks / 90s, shorter than
    BURST_MAX_S), so counting it as a failed approach stopped a perfect run after ~10 minutes.

    Three deliberate choices, each of which a previous routine paid for the hard way:
      - Aim ONCE, then click IN PLACE. The boat is stopped and the player is standing at the
        net, so its projection is static; a fresh click_entity flight per tick costs ~1s and
        cannot hold a 0.6s cadence (the mahogany-homes hotspot-burst lesson).
      - NO simulated misclick. A sloop deck is a few tiles across and MISCLICK_OFFSET_PX
        (20-45px) is wider than the net, so a "miss" lands on another facility or walks the
        player off the net -- the same rule banker.py's bank buttons and
        sailing._FIXTURE_CLICK_KWARGS follow.
      - The HEARTBEAT is the range test, not distance. Distance to a moving 6x6 object is a
        guess; a Sailing XP drop is the game's own answer to "is this net catching".
    """
    data = ctx.state.data
    data.setdefault("dst_rolls", 0)
    data.setdefault("dst_catches", 0)
    data["dst_burst_progress"] = False

    if not _click_net_to_stand_at_it(ctx, net):
        debug_log.log("trawling", "couldn't click the net to stand at it (refused, or /hover "
                                  "did not read 'Operate' on it)")
        return "net_click_failed"

    refresh = net_refresh_target(net)
    controller.hover_entity(ctx.ser, net, refresh_target=refresh)

    events = getattr(ctx, "events", None)
    mark = events.mark() if events is not None else None

    started = _now()
    ticks = 0
    ticks_since_roll = 0
    last_aim_tick = 0
    last_net_fish = None     # last polled /trawling.netFish, for the no-events stall fallback
    last_growth_tick = 0     # tick at which netFish last went up (or was first observed)

    while _now() - started < BURST_MAX_S:
        outcome_waits.tick_pause(ctx, 1, phase=CLICK_PHASE_S, now_fn=_now, sleep_fn=_sleep)
        controller.click_human(ctx.ser)
        ticks += 1

        rolled = False
        moved = False
        if events is not None:
            batch = events.since(mark)
            mark = events.mark()
            for ev_type, payload in batch:
                if ev_type in ("PLAYER_MOVING", "PLAYER_STOPPED"):
                    # Free -- these are already in the batch we read for the heartbeat.
                    moved = True
                    continue
                if ev_type != "XP_GAINED":
                    continue
                skill = (payload or {}).get("skill") or ""
                if skill.lower() == "sailing":
                    rolled = True
                    data["dst_rolls"] += 1
                    data["dst_burst_progress"] = True
                elif skill.lower() == "fishing":
                    data["dst_catches"] += 1
        ticks_since_roll = 0 if rolled else ticks_since_roll + 1

        if _interrupted(ctx, cfg):
            return "interrupted"

        if ticks % CHECK_TICKS == 0 and _dismiss_dialogue_if_open(ctx):
            controller.hover_entity(ctx.ser, net, refresh_target=refresh)
            last_aim_tick = ticks

        # Re-aim when the player has moved, and on a fixed schedule regardless.
        #
        # NO /player READ HERE (F-I8). The spec's own choice #4 is "the burst must not spend its
        # tick budget on HTTP", and a per-iteration /player (15-120ms measured) on top of the
        # click (~92ms) and its phase pad can cross the 600ms boundary -- at which point
        # wait_ticks(1) aligns to the NEXT boundary and a click is skipped, dropping that
        # window's roll cadence from 2 ticks back toward 3, i.e. exactly the +50% this routine
        # exists to capture. The boat is stopped and the player stands at the net for the whole
        # burst, so the tile essentially never changes; PLAYER_MOVING/PLAYER_STOPPED say when it
        # does, for free. With no event stream there is no movement signal at all and
        # AIM_REFRESH_TICKS is the only re-aim -- acceptable, because a standing player's
        # projection only moves with the camera, which that schedule already covers.
        due_for_refresh = (ticks - last_aim_tick) >= AIM_REFRESH_TICKS
        if moved or due_for_refresh:
            controller.hover_entity(ctx.ser, net, refresh_target=refresh)
            last_aim_tick = ticks

        if ticks % NET_POLL_TICKS == 0:
            fish = (controller.get_trawling() or {}).get("netFish")
            if fish is not None and fish >= cfg["netFullAt"]:
                debug_log.log("trawling", f"nets at {fish} -- collecting")
                return "net_full"
            # A None read (bridge hiccup mid-burst) must not manufacture a false stall -- only
            # a REAL count that fails to grow counts against the no-events fallback below.
            if fish is not None:
                if last_net_fish is not None and fish > last_net_fish:
                    # The net filling IS the heartbeat when no events arrive (F-C1's ceiling
                    # keys on this in the ctx.events is None mode, where dst_rolls never grows).
                    data["dst_burst_progress"] = True
                if last_net_fish is None or fish > last_net_fish:
                    last_growth_tick = ticks
                last_net_fish = fish

        # Two independent modes, each with its own out-of-range signal -- deliberately not an
        # if/elif chain sharing one condition. With no pushed heartbeat the Sailing XP drop
        # (ticks_since_roll) never arrives at all, so it can never fire "out_of_range" on its
        # own; the net-growth stall is that mode's own equivalent signal, not a fallback bolted
        # onto the same branch.
        if events is None:
            if ticks - last_growth_tick >= NO_EVENTS_STALL_TICKS:
                debug_log.log(
                    "trawling",
                    f"no net-count growth for {ticks - last_growth_tick} ticks -- "
                    "the shoal has moved")
                return "out_of_range"
        if events is not None and ticks_since_roll >= RANGE_LOST_TICKS:
            debug_log.log("trawling",
                          f"no Sailing xp for {ticks_since_roll} ticks -- the shoal has moved")
            return "out_of_range"

    return "budget"


# --------------------------------------------------------------------------- phases

SAIL_MAX_TRIES = 3
APPROACH_MAX_TRIES = 6
LOWER_TRIES = 3
COLLECT_TRIES = 3
NET_CLICK_TRIES = 5          # consecutive bursts whose opening net click never landed (F-M11)
NET_DEPTH_VARBIT = 19206
STALL_WARN_S = 180.0
STALL_STOP_S = 420.0
TICK_CHAIN_MAX = 4
# Both confirms budget the WALK (F-I5): the game performs a queued interaction ON ARRIVAL, and
# NET_OBJECT_IDS includes the 3x8 port/starboard variants where the net can be most of a deck
# away. At 0.6s/tile an 8-tile walk is 4.8s, past the old flat 4.0s lower budget -- so a click
# that lowered the net first time read as a failure, burned LOWER_TRIES and stopped the run.
# This is the ROSS door failure verbatim ("whenever you time a click, ask whether the player has
# to walk there first"); the *_MAX_S caps keep a net that genuinely never responds failing fast.
COLLECT_CONFIRM_S = 6.0
COLLECT_CONFIRM_MAX_S = 16.0
LOWER_CONFIRM_S = 4.0
LOWER_CONFIRM_MAX_S = 12.0
# How close the shoal (or the boat) must be to a ROUTE STOP before we believe it is AT that stop
# (F-M12). This is a shoal->stop-marker distance and has nothing to do with cfg["trawlRange"],
# which is a boat->shoal hysteresis threshold; the two were the same number by coincidence.
STOP_MATCH_TILES = 8
# Route stops sailed to while no shoal ever appeared, before giving up (F-M10). One full lap of
# the longest v1 route (turtle_belt, 8 stops) plus margin -- past that the ids, the route or the
# Wiki stop table are wrong and more sailing cannot help.
TRAVEL_MAX_STOPS = 10
# Consecutive "lost" reads before stopping (F-C2). controller.api_get has a 0.3s timeout and
# returns None on ANY failure, so ONE dropped /trawling or /objects response used to end an
# eight-hour unattended run irreversibly. This repo's standing doctrine is the opposite:
# "network drops are NOT routine bugs; the routine has to ride them out, not stop"
# (giants_foundry gf_live_tier6, GOTR). Same shape as GOTR's outside-streak.
LOST_CONFIRM_READS = 3


def _fish_in_nets(snap=None):
    """Fish currently in the nets, or None when that cannot be read. Reads /trawling only when
    no snapshot is to hand (every caller in this file has one, which keeps the tests hermetic)."""
    if snap is not None:
        return snap.net_fish
    return (controller.get_trawling() or {}).get("netFish")


def _stop(ctx, reason, snap=None):
    """Safety-stop with `reason`, naming any fish still in the nets (F-M9).

    Fish left in the nets are DISCARDED on disembark, teleport, world-hop or a forced logout, so
    a stop that leaves the user unaware of them can silently cost up to 240 fish. Appending here
    rather than at each call site is what makes it impossible to forget at a new one."""
    left = _fish_in_nets(snap)
    if isinstance(left, int) and left > 0:
        reason = f"{reason} -- {left} fish are still in the nets, collect them before you " \
                 f"disembark"
    # trigger_safety_stop appends its own ". Automation paused -- fix the issue and restart."
    ctx.state.trigger_safety_stop(reason)


def _confirm_budget(base_s, max_s, entity):
    """base + the walk to `entity`, capped. See COLLECT_CONFIRM_S above (F-I5)."""
    return min(max_s, base_s + outcome_waits.walk_budget(entity))


def travel_phase(ctx, cfg, snap) -> bool:
    """No shoal in the loaded scene. Head for a route stop -- the next one after the one we last
    fished if we know it (the table is an accelerator: we arrive with the shoal rather than
    behind it), otherwise the nearest.

    Once we ARRIVE at the stop we were heading for and still no shoal has appeared, move on
    round the route rather than re-sailing to the same tile forever (F-M10) -- and give up after
    TRAVEL_MAX_STOPS of that, because a full lap with no shoal means the ids, the route or the
    stop table are wrong, not that the boat needs another leg."""
    if not snap.in_route_area:
        _stop(ctx, f"not on the {cfg['route']} route -- start this routine with the boat there",
              snap)
        return False

    data = ctx.state.data
    heading = data.get("dst_travel_target")
    if heading is None:
        last = data.get("dst_last_stop_index")
        if last is None:
            idx, point, _dist = trawling_routes.nearest_stop(cfg["shoal"], cfg["route"],
                                                             snap.px, snap.py)
        else:
            idx, point = trawling_routes.next_stop(cfg["shoal"], cfg["route"], last)
    else:
        idx, point = heading, trawling_routes.stops_world(cfg["shoal"], cfg["route"])[heading]
        if _tile_distance(snap.px, snap.py, point) <= STOP_MATCH_TILES:
            data["dst_travel_stops"] = data.get("dst_travel_stops", 0) + 1
            if data["dst_travel_stops"] >= TRAVEL_MAX_STOPS:
                _stop(ctx, f"sailed {data['dst_travel_stops']} {cfg['route']} route stops "
                           f"without ever seeing a {trawling_routes.SHOALS[cfg['shoal']].name} "
                           f"-- wrong shoal, wrong route, or the stop table is stale", snap)
                return False
            idx, point = trawling_routes.next_stop(cfg["shoal"], cfg["route"], idx)

    data["dst_travel_target"] = idx
    debug_log.log("trawling", f"no shoal in sight -- sailing to stop {idx} {point}")
    return _sail(ctx, cfg, {"x": point[0], "y": point[1], "plane": snap.plane or 0}, snap)


def _tile_distance(x, y, point) -> float:
    if x is None or y is None:
        return float("inf")
    return ((point[0] - x) ** 2 + (point[1] - y) ** 2) ** 0.5


def approach_phase(ctx, cfg, snap) -> bool:
    """The shoal is in the scene but we are out of range, or the boat is still moving. Sail at
    the shoal's LIVE tile -- the object in /objects is the truth, the route table only fills in
    when the shoal is not visible at all (travel_phase)."""
    idx, _point, dist = trawling_routes.nearest_stop(cfg["shoal"], cfg["route"],
                                                     snap.shoal["x"], snap.shoal["y"])
    if dist <= cfg["trawlRange"]:
        ctx.state.data["dst_last_stop_index"] = idx
    target = {"x": snap.shoal["x"], "y": snap.shoal["y"], "plane": snap.plane or 0}
    return _sail(ctx, cfg, target)


def _sail(ctx, cfg, target) -> bool:
    data = ctx.state.data
    if sailing.sail_to(ctx, target):
        data["dst_sail_fails"] = 0
        return True
    data["dst_sail_fails"] = data.get("dst_sail_fails", 0) + 1
    debug_log.log("trawling",
                  f"couldn't sail to {target} ({data['dst_sail_fails']}/{SAIL_MAX_TRIES})")
    if data["dst_sail_fails"] >= SAIL_MAX_TRIES:
        _stop(ctx, "the boat couldn't reach the shoal after repeated attempts")
    return False


def lower_phase(ctx, cfg, snap) -> bool:
    """Put the net in the water. The net loc's op3 is Lower, so this is a named menu entry, not
    the left-click default (which is Operate)."""
    net = snap.net
    budget = _confirm_budget(LOWER_CONFIRM_S, LOWER_CONFIRM_MAX_S, net)
    for _ in range(LOWER_TRIES):
        controller.click_menu_action(ctx.ser, net, "Lower",
                                     refresh_target=net_refresh_target(net), offset=(0, 0))
        out = outcome_waits.await_pre(
            ctx, ("VARBIT_CHANGED",), budget,
            confirm=lambda: bool(_built_net_depth(controller.get_trawling() or {})),
            now_fn=_now, sleep_fn=_sleep)
        if out:
            debug_log.log("trawling", "net lowered")
            return True
    _stop(ctx, "the trawling net never went down", snap)
    return False


COLLECT_KEYS = {"inventory": "1", "cargo": "2"}


def _answer_collect_dialogue(ctx, cfg, destination=None):
    """Collect-from asks where the fish should go. Press the number key for the requested
    destination; a Space also advances the plain 'you collect N fish' line that follows.

    LIVE-VERIFY (spec checklist #7): the interface and the exact option text are UNCONFIRMED.
    Option 1 is assumed to be the inventory and option 2 the cargo hold; if the live dialogue
    disagrees, fix COLLECT_KEYS, not the caller."""
    key = COLLECT_KEYS.get(destination or cfg["collectTo"], "2")
    controller.key_tap(ctx.ser, key)
    _sleep(controller.sample_human_delay(0.3, 0.5))
    controller.key_tap(ctx.ser, "SPACE")


def _net_emptied():
    """True only when /trawling actually SAYS the nets are empty.

    netFish is None when the container could not be read at all, and TrawlingProvider preserves
    that distinction on purpose -- controller.get_trawling's own docstring says "do not collapse
    it with `or 0`". The old confirm did exactly that, so one bridge hiccup inside the 6s window
    reported "nets emptied" with nothing moved (F-I6). None here means "cannot confirm", which
    is falsy, so the wait simply continues."""
    fish = (controller.get_trawling() or {}).get("netFish")
    return fish is not None and fish == 0


def _cargo_is_full():
    """Whether /trawling reports the hold with no room left.

    SECONDARY signal only: `capacity` is the item container's declared max (240) and may exceed
    a given boat's real built hold, so the AUTHORITATIVE full signal is a Collect-from that
    moved nothing (F-I1b). This just tells the two "nothing moved" causes apart."""
    cargo = (controller.get_trawling() or {}).get("cargo") or {}
    used, capacity = cargo.get("used"), cargo.get("capacity")
    return used is not None and capacity is not None and used >= capacity


def _collect_attempt(ctx, cfg, snap, destination) -> bool:
    """One Collect-from click and its confirm. Shared by collect_phase and done_phase."""
    net = snap.net
    controller.click_menu_action(ctx.ser, net, "Collect-from",
                                 refresh_target=net_refresh_target(net), offset=(0, 0))
    _answer_collect_dialogue(ctx, cfg, destination)
    return bool(outcome_waits.await_pre(
        ctx, ("INVENTORY_CHANGED", "VARBIT_CHANGED"),
        _confirm_budget(COLLECT_CONFIRM_S, COLLECT_CONFIRM_MAX_S, net),
        confirm=_net_emptied, now_fn=_now, sleep_fn=_sleep))


def collect_phase(ctx, cfg, snap) -> bool:
    destination = cfg["collectTo"]
    for _ in range(COLLECT_TRIES):
        if _collect_attempt(ctx, cfg, snap, destination):
            debug_log.log("trawling", "nets emptied")
            return True
        # F-I1: a Collect-from into a FULL hold moves nothing, and no number of retries changes
        # that -- COLLECT_TRIES would be burned and the run stopped with "couldn't empty the
        # trawling nets", naming the wrong cause while up to 239 fish stayed in the nets. Hand
        # control back instead: derive_phase reads "done" on the next snapshot, and done_phase
        # takes what the inventory can still hold before it raises the net.
        if destination == "cargo" and _cargo_is_full():
            debug_log.log("trawling", "the cargo hold is full -- nothing more fits in it")
            return True
    _stop(ctx, "couldn't empty the trawling nets", snap)
    return False


def trawl_phase(ctx, cfg, snap) -> bool:
    data = ctx.state.data
    rolls_before = data.get("dst_rolls", 0)
    result = trawl_burst(ctx, cfg, snap.net)

    if result == "net_click_failed":
        data["dst_net_clicks"] = data.get("dst_net_clicks", 0) + 1
        if data["dst_net_clicks"] >= NET_CLICK_TRIES:
            _stop(ctx, f"the trawling net could not be clicked {NET_CLICK_TRIES} times running "
                       f"-- the aim is refused, or /hover never reads 'Operate' on it", snap)
        return False
    data["dst_net_clicks"] = 0

    # F-C1: the ceiling counts approaches that NEVER RE-ESTABLISHED THE HEARTBEAT, which is what
    # its stop message claims -- not the way the burst exited. out_of_range is the DESIGNED,
    # correct end of every stop (a Giant krill stop is 150 ticks / 90s, inside BURST_MAX_S), so
    # counting every one of them stopped a perfect run after six healthy stops, ~10 minutes in.
    # Progress is a Sailing roll during the burst, or -- in the ctx.events is None mode, where
    # dst_rolls can never grow -- the net's fish count growing (dst_burst_progress).
    progressed = (data.get("dst_rolls", 0) > rolls_before
                  or bool(data.get("dst_burst_progress")))
    if progressed:
        data["dst_approaches"] = 0
    elif result == "out_of_range":
        data["dst_approaches"] = data.get("dst_approaches", 0) + 1
        if data["dst_approaches"] >= APPROACH_MAX_TRIES:
            _stop(ctx, "the nets stopped catching and the boat couldn't get back to the shoal",
                  snap)
            return False
    return result in ("net_full", "out_of_range", "budget")


def _inventory_room() -> int:
    """Free inventory slots, or 0 when the inventory cannot be read (the conservative direction:
    done_phase then just raises the net instead of clicking a collect that cannot fit)."""
    inv = controller.get_inventory()
    if not isinstance(inv, list):
        return 0
    return max(0, 28 - len(inv))


def done_phase(ctx, cfg, snap) -> bool:
    """The terminal path: the cargo hold is full.

    Reachable at last (F-I1) -- with collectTo "cargo" derive_phase used to answer "collect"
    here, the collect could never confirm against a full hold, and the run stopped on
    "couldn't empty the trawling nets" with the net still down and up to 239 fish aboard.

    So: take what the INVENTORY can still hold, raise the net, and stop with a reason naming
    both the full hold and whatever is left in the nets."""
    left = snap.net_fish
    if left and _inventory_room() > 0:
        debug_log.log("trawling", f"cargo hold full -- collecting {left} fish to the inventory")
        _collect_attempt(ctx, cfg, snap, "inventory")

    controller.click_menu_action(ctx.ser, snap.net, "Raise",
                                 refresh_target=net_refresh_target(snap.net), offset=(0, 0))
    catches = ctx.state.data.get("dst_catches", 0)
    # _stop appends whatever is STILL in the nets, re-read after the inventory collect above --
    # so the one message names both the full hold and the fish that could not be moved.
    _stop(ctx, f"the cargo hold is full and the net is raised -- {catches} catches this run",
          snap._replace(net_fish=_fish_in_nets()))
    return False


PHASES = {
    "travel": travel_phase,
    "approach": approach_phase,
    "lower": lower_phase,
    "trawl": trawl_phase,
    "collect": collect_phase,
    "done": done_phase,
}


# --------------------------------------------------------------------------- watchdog


def _progress_signature(snap, phase):
    return (phase, snap.net_fish, snap.cargo_used, snap.px, snap.py)


def _stuck_watch(ctx, cfg, snap, phase):
    data = ctx.state.data
    sig = _progress_signature(snap, phase)
    if data.get("dst_sig") != sig:
        data["dst_sig"] = sig
        data["dst_sig_at"] = _now()
        data["dst_warned"] = False
        return
    stalled = _now() - data.get("dst_sig_at", _now())
    if stalled >= STALL_STOP_S:
        _stop(ctx, f"nothing has changed for {int(stalled)}s in phase {phase}")
    elif stalled >= STALL_WARN_S and not data.get("dst_warned"):
        data["dst_warned"] = True
        debug_log.log("trawling", f"[!] trawling: STUCK? {int(stalled)}s in phase {phase}")


# --------------------------------------------------------------------------- routine contract


def setup(ctx):
    """Bookkeeping only. The preconditions deliberately do NOT run here (F-I4): the engine calls
    setup() the instant the routine loads -- BEFORE login.is_fully_logged_in() and before the
    focus gate -- so at the login screen or mid-LOADING every read is empty, _startup_checks
    correctly defers, and it would never be called again for the life of the process, silently
    skipping every precondition. tick() runs it behind the dst_checked latch instead, the same
    shape as guardians_of_the_rift."""
    ctx.state.data.setdefault("dst_started_at", _now())
    debug_log.log("trawling", "setup -- preconditions deferred to the first tick")


def _fishing_level():
    """Boosted (else base) Fishing level from /skills, or None when it cannot be read.

    None means "cannot tell -- do not filter", never 0: shipwreck_salvaging's level check once
    failed CLOSED on an unreadable /skills, turned every wreck into "above my level" and idled
    the routine until it was restarted by hand."""
    fishing = (controller.get_skills() or {}).get("Fishing") or {}
    for key in ("boosted", "level"):
        value = fishing.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def _startup_checks(ctx, cfg) -> bool:
    """The preconditions. Returns True once they have actually RUN (whether or not they passed),
    False while the client is still answering with nothing.

    Deferred on an unreadable read -- the first logged-in tick after an auto-login can serve an
    empty scene, and stopping on that is the GOTR 'no pickaxe with a pickaxe equipped' bug."""
    snap = read_snapshot(ctx, cfg)
    if snap.px is None:
        return False
    if snap.net is None:
        _stop(ctx, "start this routine standing on your boat, which needs a trawling net built",
              snap)
        return True
    if not snap.navigating:
        # NOTE what this can and cannot establish (F-I3). SailingProvider defines `navigating`
        # as WorldView.getYellowClickAction() == CLICK_ACTION_SET_HEADING -- "a tile click
        # currently resolves to steering", a property of THIS player's click mode. It is NOT a
        # crewmate readout; the bridge has no such field, and a real crewmate check would need a
        # new provider field. So the reason names what was OBSERVED and only suggests the cause.
        # Spec live-verify #11 asks whether this flag discriminates the case at all.
        _stop(ctx, "the boat is not in steering mode; assign a crewmate to the helm so the boat "
                   "can be steered while you stand at the net", snap)
        return True
    spec = trawling_routes.SHOALS[cfg["shoal"]]
    level = _fishing_level()
    if level is not None and level < spec.fishing_level:
        _stop(ctx, f"Fishing {int(level)} is below the {spec.fishing_level} a "
                   f"{spec.name} needs", snap)
        return True
    if not snap.in_route_area:
        _stop(ctx, f"not on the {cfg['route']} route -- start this routine with the boat there",
              snap)
    return True


def on_event(ctx, event_type, payload):
    """Only used between bursts -- the engine never dispatches this while tick() is held, which
    is why trawl_burst reads ctx.events.since(mark) itself."""
    if event_type == "XP_GAINED" and (payload or {}).get("skill", "").lower() == "sailing":
        ctx.state.data["dst_last_sailing_xp_at"] = _now()


def tick(ctx):
    cfg = ctx.state.config or DEFAULT_CONFIG
    data = ctx.state.data
    if not data.get("dst_checked"):
        # The latch is set only once the checks actually RAN (F-I4) -- an attempt that deferred
        # on an unreadable read must be retried on a later tick, not silently skipped forever.
        if not _startup_checks(ctx, cfg):
            controller.human_delay(0.4, 0.8)
            return
        data["dst_checked"] = True
        if getattr(ctx.state, "safety_stopped", False):
            return
    for _ in range(TICK_CHAIN_MAX):
        snap = read_snapshot(ctx, cfg, ticks_since_sailing_xp=_ticks_since_heartbeat(ctx))
        if not tick_with_snapshot(ctx, cfg, snap):
            return
        if _interrupted(ctx, cfg):
            return


def _ticks_since_heartbeat(ctx):
    """DIAGNOSTIC ONLY -- trawl_burst owns the real out-of-range heartbeat (its own Sailing-xp
    tick count / net-growth stall, scoped to a single burst). This one only feeds
    TrawlSnapshot.ticks_since_sailing_xp for a human reading the phase-change log line between
    bursts; nothing here should ever wire it into a phase or safety decision."""
    at = ctx.state.data.get("dst_last_sailing_xp_at")
    return 0.0 if at is None else (_now() - at) / 0.6


def tick_with_snapshot(ctx, cfg, snap) -> bool:
    data = ctx.state.data
    if snap.px is None:
        # The client told us NOTHING this read -- a dropped /player, a LOADING frame, the login
        # screen. That is "cannot tell", not a fact about the world (F-C2): do nothing at all,
        # and in particular do not let it count toward the lost streak below.
        return False

    phase = derive_phase(snap, cfg)
    if phase != data.get("dst_phase"):
        debug_log.log("trawling", f"phase={phase} netFish={snap.net_fish} "
                                  f"shoalDist={snap.shoal_distance} "
                                  f"ticksSinceXp={snap.ticks_since_sailing_xp:.0f}")
        data["dst_phase"] = phase
    if phase != "travel":
        # A shoal turned up: the travel lap counter and its target start fresh (F-M10).
        data["dst_travel_stops"] = 0
        data["dst_travel_target"] = None
    _stuck_watch(ctx, cfg, snap, phase)
    if getattr(ctx.state, "safety_stopped", False):
        return False

    if phase == "lost":
        # Confirmed over LOST_CONFIRM_READS consecutive reads before it ends the run (F-C2).
        # One dropped HTTP response yields net=None / netFish=None, which reads as "lost", and
        # an irreversible first-read stop then ended an eight-hour unattended run -- with the
        # nets' fish discarded by the forced logout that follows.
        data["dst_lost_reads"] = data.get("dst_lost_reads", 0) + 1
        if data["dst_lost_reads"] < LOST_CONFIRM_READS:
            debug_log.log("trawling", f"no boat/net/net-container in this read "
                                      f"({data['dst_lost_reads']}/{LOST_CONFIRM_READS}) -- "
                                      f"riding it out")
            return False
        _stop(ctx, "start this routine standing on your boat, which needs a trawling net built",
              snap)
        return False
    data["dst_lost_reads"] = 0
    return bool(PHASES[phase](ctx, cfg, snap))


def before_break(ctx):
    """Empty the nets before a break: fish left in them are DISCARDED on a forced logout or a
    disconnect, and a break is minutes long (same reason chinchompa_hunting picks its traps up
    here).

    THE SECOND-ORDER HAZARD THIS EXISTS FOR (F-C3): a scheduled break is 3-12 minutes of the
    routine sitting idle, which is long enough to trip OSRS's own ~5-minute idle logout. That IS
    a forced logout, and a forced logout DOES discard the nets -- so this collect is what stands
    between a perfectly ordinary break and up to 240 lost fish. It is also why DEFAULT_CONFIG
    must carry a "breaks" block at all: with none, the engine's break_due({}) is always False,
    this hook is never called, and the mitigation is dead code that its own test still passes.

    net_fish is None means "cannot tell" -- try the collect anyway (F-I6). Skipping one is the
    expensive direction; a needless Collect-from on an empty net costs a click."""
    cfg = ctx.state.config or DEFAULT_CONFIG
    snap = read_snapshot(ctx, cfg)
    if snap.net is not None and (snap.net_fish is None or snap.net_fish > 0):
        collect_phase(ctx, cfg, snap)
