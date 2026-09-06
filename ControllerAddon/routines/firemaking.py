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
#   - "Power firemaking" (queuing the next light before finishing the walk-away step) IS what
#     burn_chain does since 2026-09-05 -- see the TICK-PERFECT BURNING comment below. The
#     isIdle wait at the top of tick() only gates the walk to the starter tile and banking.
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
#   - A light is confirmed on the pushed Firemaking XP drop (burn_chain), never on a sleep or
#     on isIdle -- neither inventory click makes isMoving true, so an idle read right after the
#     pair says nothing about whether the light registered.
#
# Run with:
#     python controller.py routines/firemaking.py

import time

import banker
import bridge_events
import controller
import debug_log
import travel

# TICK-PERFECT BURNING (USER 2026-09-05: "make firemaking as tick perfect as possible").
# Wiki (Game tick/Action lengths): lighting any log is 4 ticks. Wiki (Firemaking training):
# "when correctly timed, the player can move to another square in between lighting the logs"
# -- i.e. the next tinderbox-on-log can be queued during the west step the game takes on the
# tick the fire appears, so the next light starts on the very next tick with none wasted.
# The bot's version of that rhythm:
#   - the Firemaking XP_GAINED UDP event is the "fire lit" signal (it fires on the same tick
#     as the fire; /skills' xp is the HTTP confirm). No isIdle wait: isIdle only goes true
#     after the west step, which is exactly the tick a human spends queuing the next light.
#   - the cursor is PARKED on the tinderbox for the whole 4-tick light, so the first click of
#     the pair is in place (click_human, ~0.1s) and only the second is a flight, one slot over
#     to the next log (~0.2s). Both land inside the 0.6s tick the XP event opened.
#   - the whole inventory is burned inside one tick() call (like mining.py's chain): the
#     engine loop's ~1.1s per iteration would otherwise cost nearly two ticks per log.
# Below level 43 a light can fail, but the game RETRIES on its own (the player keeps
# attempting until success), so "no XP inside 4 ticks" is only a failed CLICK when the player
# is also idle -- an animating player is still lighting and just gets more time. A player who
# is idle with no XP re-clicks; three of those in a row (or the game's own "can't light a fire
# here" line, e.g. another player's fire on our tile) steps one tile west and carries on.
XP_SKILL = "Firemaking"
LIGHT_BUDGET_S = 3.4            # 4 ticks (2.4s) + a full tick of UDP/HTTP slack
LIGHT_MAX_S = 15.0              # cap on the auto-retry extension while the player animates
QUEUE_DELAY_S = (0.04, 0.14)    # after the XP event, before the next tinderbox click
CHAIN_MAX_S = 75.0              # hand control back to the engine at least this often
FAIL_STEP_AFTER = 3             # idle-with-no-XP attempts before stepping one tile west
CANT_LIGHT_TEXT = "can't light a fire here"

# GE "starter tiles" (USER, 2026-09-05): two tiles at the Grand Exchange, one row apart. A fire
# lit here walks the player one tile WEST per log, so a full inventory burns a straight
# east-to-west line from the starter tile with no repositioning at all -- then bank, come back,
# and burn the NEXT line. The rows alternate after every bank (3475, then 3474, then 3475 ...)
# so the new line never starts on top of the previous line's still-burning fires.
STARTER_TILES = ((3179, 3475), (3179, 3474))
STARTER_PLANE = 0
STARTER_WALK_TRIES = 3          # walks that fail to land exactly before we light from wherever we are
STARTER_HOP_DISTANCE = 10       # farther than this: route with travel.walk_to first, exact tile after

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
        "withdraw": [{"name": "Willow logs", "quantity": 27, "all": True}],
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
    if config is None:
        config = DEFAULT_CONFIG
    return mark_withdraw_all(config)


def mark_withdraw_all(config: dict) -> dict:
    """Every bank withdraw entry becomes a single SHIFT-click "withdraw all" (USER 2026-09-05):
    the whole trip is "fill the inventory with logs", so the panel's quantity is a ceiling the
    free slots already impose, and 27 single clicks on the same slot is neither quick nor
    human. Applied Python-side so the bridge's own config shape needs no Java change."""
    bank = config.get("bank") or {}
    entries = [dict(e, all=True) for e in bank.get("withdraw", []) if e.get("name")]
    config = dict(config)
    config["bank"] = dict(bank, withdraw=entries)
    return config


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
                advance_starter(ctx)
        else:
            ctx.state.trigger_safety_stop("out of logs and banking is disabled")
        return

    if not ctx.state.data.get("fm_positioned"):
        if not go_to_starter_tile(ctx):
            return  # walk fired (or failed) -- re-check next tick, isIdle gates the walk itself

    burn_chain(ctx, config)


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def firemaking_xp():
    return ((controller.get_skills() or {}).get(XP_SKILL) or {}).get("xp")


def _find_in(items, name: str):
    name_lower = (name or "").lower()
    return next((i for i in items if name_lower in (i.get("name") or "").lower()), None)


def _cant_light_seen(ctx, since) -> bool:
    """The game's own refusal ("You can't light a fire here.") since `since` -- read off the
    buffered stream, because on_event never runs while burn_chain holds the tick."""
    if ctx.events is None or since is None:
        return False
    for event_type, payload in ctx.events.since(since):
        message = ((payload or {}).get("message") or "").lower()
        if event_type == "CHAT_MESSAGE" and CANT_LIGHT_TEXT in message:
            return True
    return False


def step_west(ctx) -> None:
    player = controller.get_player() or {}
    if player.get("x") is None:
        return
    x, y = player["x"] - 1, player["y"]
    debug_log.log("firemaking", f"can't light here -- stepping west to ({x},{y})")
    travel.walk_to_exact_tile(ctx, x, y, player.get("plane", 0))


def _await_light(ctx, since, xp_base, t_click):
    """The pushed Firemaking XP drop confirmed by /skills, budgeted at LIGHT_BUDGET_S -- and
    extended (up to LIGHT_MAX_S) while the player is still animating, which is the game's own
    low-level retry, not a failed click."""
    def confirm():
        xp = firemaking_xp()
        return xp if (xp is not None and xp_base is not None and xp > xp_base) else None

    signals = {"XP_GAINED": lambda p: p.get("skill") == XP_SKILL}
    res = bridge_events.await_outcome(ctx.events, signals, LIGHT_BUDGET_S, confirm=confirm,
                                      since=since, reaction=False, now_fn=_now, sleep_fn=_sleep)
    while not res and _now() - t_click < LIGHT_MAX_S \
            and not controller.get_combat().get("isIdle", True):
        budget = min(LIGHT_BUDGET_S, LIGHT_MAX_S - (_now() - t_click))
        res = bridge_events.await_outcome(ctx.events, signals, budget, confirm=confirm,
                                          since=since, reaction=False, now_fn=_now, sleep_fn=_sleep)
    return res


def burn_chain(ctx, config: dict) -> int:
    """Burn logs back to back until the inventory runs out (or CHAIN_MAX_S), one tick-aligned
    light per XP drop -- see the module comment. Returns the number of logs lit."""
    tinderbox_name = config.get("tinderboxName", "tinderbox")
    log_name = config.get("logName", "logs")
    start = _now()
    lit = 0
    fails = 0
    parked = False          # cursor resting on the tinderbox from the previous cycle

    while _now() - start < CHAIN_MAX_S:
        inv = controller.get_inventory()
        tinderbox = _find_in(inv, tinderbox_name)
        log = _find_in(inv, log_name)
        if tinderbox is None or log is None:
            break

        xp_base = firemaking_xp()
        since = ctx.events.mark() if ctx.events is not None else None
        if parked:
            controller.click_human(ctx.ser)
        else:
            controller.click_entity(ctx.ser, tinderbox, check_ui_blockers=False)
        controller.click_entity(ctx.ser, log, check_ui_blockers=False)
        t_click = _now()
        # Rest on the tinderbox for the 4-tick light, so the next pair opens with an in-place
        # click. The tinderbox never moves in the inventory, so the parked aim stays valid.
        parked = bool(controller.hover_entity(ctx.ser, tinderbox, check_ui_blockers=False))

        res = _await_light(ctx, since, xp_base, t_click)
        if res:
            lit += 1
            fails = 0
            gained = res.confirmed - xp_base if xp_base is not None else res.confirmed
            debug_log.log("firemaking", f"lit {log.get('name')} (+{gained} xp, "
                                        f"{_now() - t_click:.2f}s after the click)")
            controller.human_delay(*QUEUE_DELAY_S)
            continue

        fails += 1
        parked = False      # a failed pair is not trusted to have left the cursor anywhere useful
        if _cant_light_seen(ctx, since) or fails >= FAIL_STEP_AFTER:
            step_west(ctx)
            fails = 0
        else:
            debug_log.log("firemaking", f"no XP from that click ({fails}/{FAIL_STEP_AFTER}) -- re-clicking")
    return lit


def starter_tile(ctx) -> tuple:
    """(x, y) of the starter tile for the CURRENT inventory. Index lives in ctx.state.data, so a
    restart begins at STARTER_TILES[0] again -- a restart already loses which row was last
    burned, and the fires from a partial line have burned out long before the walk back."""
    idx = ctx.state.data.get("fm_starter_idx", 0) % len(STARTER_TILES)
    return STARTER_TILES[idx]


def advance_starter(ctx) -> None:
    """Called after every successful bank: alternate to the other row and require a fresh walk
    to it before the next log is lit."""
    ctx.state.data["fm_starter_idx"] = (ctx.state.data.get("fm_starter_idx", 0) + 1) % len(STARTER_TILES)
    ctx.state.data["fm_positioned"] = False
    ctx.state.data["fm_starter_tries"] = 0
    x, y = starter_tile(ctx)
    debug_log.log("firemaking", f"banked -- next line starts at ({x},{y})")


def go_to_starter_tile(ctx) -> bool:
    """True once standing exactly on the current starter tile (sets fm_positioned). False when
    a walk was just issued or could not be issued -- the caller returns and the next tick's
    isIdle check waits the walk out. After STARTER_WALK_TRIES walks that did not land exactly,
    gives up on positioning for this inventory and lights from wherever the player stands: a
    misplaced line is a worse-looking one, not a stuck routine."""
    x, y = starter_tile(ctx)
    player = controller.get_player()
    if player.get("x") == x and player.get("y") == y and player.get("plane", 0) == STARTER_PLANE:
        ctx.state.data["fm_positioned"] = True
        ctx.state.data["fm_starter_tries"] = 0
        return True

    tries = ctx.state.data.get("fm_starter_tries", 0)
    if tries >= STARTER_WALK_TRIES:
        debug_log.log("firemaking", f"[!] couldn't reach starter tile ({x},{y}) after {tries} walks "
                                    f"-- burning from ({player.get('x')},{player.get('y')}) instead")
        ctx.state.data["fm_positioned"] = True
        return True
    ctx.state.data["fm_starter_tries"] = tries + 1

    px, py = player.get("x"), player.get("y")
    far = px is None or py is None or max(abs(px - x), abs(py - y)) > STARTER_HOP_DISTANCE
    debug_log.log("firemaking", f"walking to starter tile ({x},{y}) (try {tries + 1})")
    if far:
        # Out of viewport range for an exact-tile click: route there first, the exact click
        # finishes the job on a later tick (walk_to's own arrival tolerance is 3 tiles).
        travel.walk_to(ctx, {"x": x, "y": y, "plane": STARTER_PLANE}, goal_adjacent=False)
        return False
    if travel.walk_to_exact_tile(ctx, x, y, STARTER_PLANE, wait_for_arrival=False):
        return False
    # Tile not projectable from here -- fall back to a routed walk.
    travel.walk_to(ctx, {"x": x, "y": y, "plane": STARTER_PLANE}, goal_adjacent=False)
    return False


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
