# routines/firemaking.py
#
# Burn a configured log type with a tinderbox, one log per tick, restocking from a bank
# when configured. Modeled on routines/mining.py's shape (poll isIdle, act, repeat), but is
# this project's first routine to click two inventory items in sequence (tinderbox, then
# log) rather than one world object/NPC/menu button.
#
# Mechanics (from the local OSRS wiki mirror, osrs-wiki-data-master/raw/F/Firemaking.txt and
# raw/F/Fire.txt):
#   - Lighting is "use tinderbox on log" (or log on tinderbox) -- a plain left-click on the
#     tinderbox already resolves to "Use" (it has no more-prioritized default action to
#     compete with), same reasoning as this project's other tool items. If that's not true
#     for your account/settings, add it to Controller Settings -> Clicking -> Forced Actions,
#     same escape hatch travel.py documents for the ring of dueling.
#   - Below Firemaking level 43, lighting can fail (crowdsourced success chance runs from
#     ~65/256 at level 1 to 256/256 at level 43+, regardless of log tier) with no animation
#     and no log lost -- retrying is just clicking again. tick() gets this for free: if the
#     two clicks didn't start an animation, get_combat().isIdle is still True on the very next
#     tick, so the same two clicks just fire again.
#   - After a successful light, the player auto-walks one step away (west, or east/south/north
#     if blocked) before the next log can be lit -- get_combat().isIdle already accounts for
#     this (isIdle = not moving AND no animation AND no interact target), so waiting for
#     isIdle covers both the light animation and the step-away.
#   - "Power firemaking" (queuing the next light before finishing the walk-away step) is
#     deliberately NOT implemented here -- waiting for full isIdle between logs is simpler and
#     safer, matching this routine's "pretty simple" scope.
#
# Inventory-click quirks handled here (see tick() below for the actual code):
#   - GameStateProvider.java documents inventory item screenX/screenY as only valid while the
#     Inventory sidebar tab is selected, so tick() calls controller.open_inventory_tab(ctx.ser)
#     (a no-op if already open) before reading tinderbox/log positions.
#   - In resizable-viewport layouts the inventory panel itself is a registered UI-blocker rect
#     (RESIZABLE_VIEWPORT_INVENTORY_CONTAINER / ..._BOTTOM_LINE_INVENTORY_CONTAINER in
#     GameStateProvider.java) -- exactly where the tinderbox and log live -- so both
#     click_entity() calls pass check_ui_blockers=False, same fix tormented_demon.py already
#     established for inventory-item clicks.
#   - The bridge polls roughly every half a game tick, and neither inventory click makes
#     isMoving true the way clicking a distant object would, so tick() sleeps briefly after the
#     second click to avoid re-reading isIdle == True and firing a duplicate click pair before
#     the light has registered server-side.
#
# Run with:
#     python controller.py routines/firemaking.py

import random
import time

import banker
import controller
import debug_log

# Firemaking level required to burn each log type -- Firemaking.txt's "List of combustible
# logs" table. Used only for a one-time-per-activation sanity check run from inside tick()
# (stop early with a clear message instead of silently retrying forever against a log the
# account isn't leveled for yet) -- not needed by the burn loop itself, which already handles
# the success-chance rolls above by retrying every tick until isIdle flips.
LOG_LEVEL_REQUIREMENTS = {
    "logs": 1,
    "achey tree logs": 1,
    "oak logs": 15,
    "willow logs": 30,
    "teak logs": 35,
    "jatoba logs": 40,
    "arctic pine logs": 42,
    "maple logs": 45,
    "mahogany logs": 50,
    "yew logs": 60,
    "blisterwood logs": 62,
    "camphor logs": 66,
    "magic logs": 75,
    "ironwood logs": 80,
    "redwood logs": 90,
    "rosewood logs": 92,
}

# Used only if FiremakingPlugin isn't loaded/enabled in RuneLite (so /firemaking-config comes
# back None) -- keeps this routine runnable standalone, same as tormented_demon.py.
DEFAULT_CONFIG = {
    "enabled": True,
    "logName": "Willow logs",
    "tinderboxName": "Tinderbox",

    "bank": {
        "enabled": False,
        "teleportFallback": False,
        "keepItems": ["tinderbox"],
        "withdraw": [{"name": "Willow logs", "quantity": 27}],
    },

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
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_firemaking_config()
    return config if config is not None else DEFAULT_CONFIG


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check -- see LOG_LEVEL_REQUIREMENTS above.
    Deliberately run from inside tick() rather than a setup() hook: controller.py's main loop
    calls routine.setup(ctx) at routine-load time, BEFORE the login.is_fully_logged_in() gate,
    so /skills could be queried pre-login (returning zeros/stale data) and false-trigger a
    permanent trigger_safety_stop() on an account that's actually fine once logged in. tick()
    only ever runs while fully logged in (per that same gate), so the level reading here is
    reliable. Gated by a ctx.state.data flag so it runs exactly once per routine-activation --
    state.data is reset to {} by controller.py whenever the active routine file changes."""
    log_name = (config.get("logName") or "").strip().lower()
    required = LOG_LEVEL_REQUIREMENTS.get(log_name)
    if required is None:
        return  # unknown/custom log name -- can't validate, don't block

    firemaking = controller.get_skills().get("Firemaking") or {}
    level = firemaking.get("level")
    if level is not None and level < required:
        ctx.state.trigger_safety_stop(
            f"Firemaking level {level} is below the {required} required for {config.get('logName')}"
        )


def tick(ctx):
    config = ctx.state.config

    if not ctx.state.data.get("firemaking_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["firemaking_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    if not controller.get_combat().get("isIdle", True):
        return  # still lighting the last log, or stepping away from the new fire

    # Inventory item screenX/screenY are only valid while the Inventory sidebar tab is
    # selected (GameStateProvider.java) -- make sure it is before reading/clicking items below.
    controller.open_inventory_tab(ctx.ser)

    tinderbox = find_item(config.get("tinderboxName", "tinderbox"))
    log = find_item(config.get("logName", "logs"))

    if tinderbox is None:
        # Banking can't fix this -- the bank withdraw loadout only ever contains the log
        # (banker.py's keepItems just prevents depositing a tinderbox already held, it never
        # withdraws one), so banking here would just loop forever rediscovering the same
        # problem. Stop regardless of the bank setting.
        ctx.state.trigger_safety_stop("no tinderbox in inventory")
        return

    if log is None:
        bank_cfg = config.get("bank", {})
        if bank_cfg.get("enabled"):
            debug_log.log("firemaking", "out of logs -- banking")
            if not banker.bank_up(ctx, bank_cfg):
                ctx.state.trigger_safety_stop("couldn't reach a bank to restock logs")
        else:
            ctx.state.trigger_safety_stop("out of logs and banking is disabled")
        return

    debug_log.log("firemaking", f"lighting {log.get('name')}")
    time.sleep(random.uniform(0.15, 0.35))
    controller.click_entity(ctx.ser, tinderbox, check_ui_blockers=False)
    time.sleep(random.uniform(0.1, 0.25))
    controller.click_entity(ctx.ser, log, check_ui_blockers=False)
    # Let the light register server-side before the next isIdle poll -- neither inventory
    # click makes isMoving true the way clicking a distant object would, so without this the
    # very next ~0.3s poll can still read isIdle == True and fire a duplicate click pair.
    time.sleep(random.uniform(0.6, 1.0))


def find_item(name: str):
    """First inventory item whose name contains `name` (case-insensitive substring, same
    idiom as travel.py/banker.py) -- e.g. a logName of "Logs" will also match "Achey tree
    logs" if both are present. Pick specific enough logName/tinderboxName values to avoid
    that ambiguity; not solved here, same limitation every other substring match in this
    codebase already has."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if name_lower in (i.get("name") or "").lower()),
        None,
    )
