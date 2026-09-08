"""3-tick granite mining ("3t4g") at the Cape Conch mine.

Design: docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md -- read it before
changing any timing constant here. NOT YET RUN LIVE; the spec's live-verification checklist is
the first session's worklist.

THE SHAPE, and why it is not gathering.py. Every other routine here is reactive: click, wait on
a pushed signal, confirm over HTTP. This one is a SCHEDULE. Per 3-tick cycle:

    tick T+1 (dead)  select the knife; resolve the NEXT rock's screen point over HTTP;
                     fly to the log and PARK the cursor there
    tick T+2 (dead)  nothing -- the cursor rests on the log
    tick T  (crit)   click IN PLACE on the log (no flight) -> the 3-tick action fires;
                     ONE short flight to the rock; click

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

    # Startup refuses above this. The flight is the whole cost of the critical tick, and it
    # scales with distance: settings.HM2_SLOPE_MS_PER_BIT is +120.5ms per DOUBLING.
    "maxReachPx": 220,
    # Where in the critical tick the log click lands. 60 leaves ~190ms of slack under the p90
    # tail (60 + 92 + 270 + 145 = 567). A guess; fit it from the logged per-cycle occupancy.
    "logClickPhaseMs": 60,
    # LIVE-VERIFY (spec checklist item 2): the wiki says the action must start "on the same tick
    # as moving to the next rock", and a click on tick T causes movement on T+1. If the yield
    # rate is poor at 0, try -1.
    "rockClickTickOffset": 0,
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
    cfg = ctx.state.config
    inv = controller.get_inventory() or []

    ok, msg = check_geometry(cfg, inv)
    debug_log.log(CATEGORY, f"geometry: {msg}")
    if not ok:
        ctx.state.trigger_safety_stop(f"3-tick granite cannot start: {msg}")
        return

    # The ONE place this repo's "always tolerate ctx.events is None" rule inverts. There is no
    # fallback: without the tick clock there is no tick phase, and without the phase this is an
    # ordinary 5-tick miner with extra steps. Degrading to that silently would be worse than
    # stopping, so do not "fix" this into a polling fallback.
    if ctx.events is None or ctx.events.ticks.is_stale(_now()):
        ctx.state.trigger_safety_stop(
            "3-tick granite needs the UDP tick clock and it is absent or stale "
            "(is the bridge up? was controller.py started with --no-events?)")
        return

    ctx.state.data["g3t_index"] = 0
    ctx.state.data["g3t_logs"] = log_count(inv, cfg["logName"])
    debug_log.log(CATEGORY, f"ready: {len(cfg['rockTiles'])} rocks, "
                            f"{ctx.state.data['g3t_logs']} log(s)")


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
