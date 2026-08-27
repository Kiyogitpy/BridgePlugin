# controller.py
#
# Generic engine for the RuneLite HTTP/UDP bridge. controller.py owns the
# *plumbing*: talking to the bridge (HTTP state + UDP events), driving the
# Arduino mouse/keyboard, the panic key, and cross-cutting concerns like
# safety stops and human-like breaks.
#
# It knows nothing about slayer, mining, or any other activity. What to
# actually do is supplied by a "routine" -- a separate .py file, passed as a
# required command-line argument (each Controller-family RuneLite plugin
# owns its own settings screen now, so there's no in-game dropdown to fall
# back to):
#
#     python controller.py routines/slayer.py
#     python controller.py routines/mining.py
#
# ---------------------------------------------------------------------------
# Routine contract
# ---------------------------------------------------------------------------
# A routine is a plain Python module. It may define any of the following:
#
#   DEFAULT_CONFIG = {...}
#       Optional. A config dict the routine falls back to if it doesn't need
#       anything from the bridge's GET /config (which only reflects the
#       Controller plugin's combat/slayer panel today).
#
#   def get_config(bridge_config: dict | None) -> dict:
#       Optional. Called once per cycle with whatever GET /config returns
#       (None if the bridge hasn't responded yet). Return the config dict the
#       rest of the routine should use. If omitted, controller.py uses
#       bridge_config when available, else DEFAULT_CONFIG.
#
#   def setup(ctx: Context) -> None:
#       Optional. Called once, after the serial connection is open, before
#       the main loop starts.
#
#   def tick(ctx: Context) -> None:
#       Required. Called once per loop iteration to do the actual work.
#       Only called while logged in, the game window is focused, no safety
#       stop is active, the resolved config's "enabled" flag is true, and
#       the routine isn't on a break.
#
#   def on_event(ctx: Context, event_type: str, payload: dict | None) -> None:
#       Optional. Called once for every UDP event the bridge sends (e.g.
#       "PLAYER_DIED", "NPC_DIED", "LEVEL_UP", "HP_CHANGED"), regardless of
#       whether the routine is currently ticking.
#
#   def before_break(ctx: Context) -> None:
#       Optional. Called once, immediately before a break starts -- i.e. on the
#       tick where ControllerState.break_due() first goes true, under the same
#       gates tick() runs under (logged in, focused, not safety-stopped). For
#       leaving the world in a state that survives being unattended for the
#       break's 3-12 minutes: routines/chinchompa_hunting.py picks its four box
#       traps back up here rather than leaving them laid. The break starts after
#       this returns, so a slow teardown just delays the break by that much.
#       Never called while already on a break, and never more than once per break.
#
# ctx (a Context instance) is what routines use to act:
#
#   ctx.ser         -- open serial.Serial connection; pass to click_entity,
#                      move_to_human, key_tap, etc.
#   ctx.state       -- the shared ControllerState (ctx.state.config is the
#                      resolved config for this cycle; ctx.state.data is a
#                      plain dict routines can use for their own bookkeeping,
#                      e.g. a current target id)
#
# Everything else a routine needs -- get_player(), get_npcs(), get_objects(),
# click_entity(), move_to_human(), consume_first(), ... -- lives at module
# level in controller.py. Routines just `import controller` and call them.

import argparse
import ctypes
import importlib.util
import json
import math
import os
import random
import select
import socket
import sys
import threading
import time
from dataclasses import dataclass

import keyboard  # panic-key / manual trigger
import requests
import serial
import win32gui
import winsound  # pause/resume acknowledgment beep -- stdlib, Windows-only like win32gui above

import debug_log
import human_move  # v2 movement engine; only reached when settings.HUMAN_MOVE_V2 is on
from settings import *  # noqa: F401,F403 -- all tunables collected in settings.py


# ---------------- panic button ----------------
def panic_exit():
    print("\n[!] Emergency exit triggered (F1 pressed).")
    winsound.Beep(PANIC_BEEP_HZ, PANIC_BEEP_DURATION_MS)
    os._exit(0)  # hard kill, works even if a thread is stuck


def _panic_watchdog() -> None:
    """Blocks on the panic hotkey and exits the moment it fires -- runs forever on its own
    daemon thread (see start_panic_listener), same shape as _pause_watchdog.

    CORRECTED 2026-08-27 (user direction: "make sure they are built the same as pause but just
    exits instead of pauses"): this used to ALSO register a plain keyboard.add_hotkey('f1', ...)
    callback alongside this same wait-loop, both wired to panic_exit -- harmless only because
    os._exit is idempotent-ish (the second call, if ever reached, exits an already-dead
    process), but an inconsistent shape once pause could no longer do the same double-wiring
    (see _pause_watchdog's own note on why). Unified to the single wait-loop mechanism."""
    while True:
        keyboard.wait('f1')
        panic_exit()


def start_panic_listener():
    threading.Thread(target=_panic_watchdog, daemon=True).start()


# ---------------- pause toggle ----------------
def toggle_pause(state: "ControllerState"):
    state.paused = not state.paused
    if state.paused:
        debug_log.log("!", "Paused (F2 pressed). Automation frozen -- press F2 to resume.")
        winsound.Beep(PAUSE_BEEP_HZ, PAUSE_BEEP_DURATION_MS)
    else:
        debug_log.log("!", "Resumed (F2 pressed).")
        winsound.Beep(RESUME_BEEP_HZ, PAUSE_BEEP_DURATION_MS)


def _pause_watchdog(state: "ControllerState") -> None:
    """Blocks on the pause hotkey and toggles state.paused each time it fires -- runs forever on
    its own daemon thread (see start_pause_listener).

    CORRECTED 2026-08-27 (user report): a bare keyboard.add_hotkey('f2', ...) callback -- what
    this used to be -- stopped firing once RuneLite, rather than the terminal/IDE, had window
    focus. start_panic_listener's F1 handling already has a second, redundant path for exactly
    this reason: a dedicated thread blocking on keyboard.wait, which was confirmed to keep
    working focus-independently. Pause can't use the same belt-and-suspenders shape panic does
    (add_hotkey AND a wait-loop both wired to the same action) -- toggling isn't idempotent like
    panic_exit's hard os._exit is, so firing both for one keypress would immediately cancel back
    out. This is the wait-loop alone, replacing add_hotkey rather than duplicating it."""
    while True:
        keyboard.wait('f2')
        toggle_pause(state)


def start_pause_listener(state: "ControllerState"):
    threading.Thread(target=lambda: _pause_watchdog(state), daemon=True).start()


# ---------------- bridge HTTP helpers ----------------
_bridge_failing = False
_bridge_fail_count = 0
# Reused for the process lifetime instead of requests.get()'s implicit one-off connection --
# CONFIRMED this endpoint is polled many times a second (see ObjectProvider.java's own comment
# on /objects/ /tiles/ /ground-items), so a shared Session's TCP connection reuse meaningfully
# cuts per-call overhead to the local bridge.
_session = requests.Session()


def api_get(path, params=None, timeout=0.3):
    global _bridge_failing, _bridge_fail_count
    try:
        resp = _session.get(f"{API_BASE}{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        result = _scale_screen_coords(resp.json(), get_dpi_scale())
        if _bridge_failing:
            # Recovery is edge-triggered too, not logged on every successful request -- api_get()
            # is called many times per tick (every get_*() wrapper), so without this a bridge
            # restart would print a burst of "recovered" lines instead of exactly one.
            debug_log.log("routine", f"bridge recovered after {_bridge_fail_count} failed requests")
            _bridge_failing = False
            _bridge_fail_count = 0
        return result
    except Exception:
        # Bridge failures currently look identical to "the routine is stuck" from the console
        # alone -- api_get() swallows every request exception silently, so if the Java plugin
        # restarts or the RuneLite client closes mid-run, every get_*() wrapper quietly starts
        # returning empty/default values with no signal that anything changed. Edge-triggered
        # (only on the OK -> failing transition) so a bad connection doesn't spam one line per
        # failed request -- every get_*() wrapper calls this, several times per tick.
        _bridge_fail_count += 1
        if not _bridge_failing:
            _bridge_failing = True
            debug_log.log("!", f"bridge unreachable: GET {path} failing since tick={debug_log.current_tick()}")
        return None


def get_state():
    return api_get("/state") or {}


def get_player():
    return api_get("/player") or {}


def get_varbit(varbit_id: int):
    """Raw client.getVarbitValue(varbit_id) passthrough (PlayerProvider's /varbit) -- generic,
    not tied to any one routine's data model, same "expose the primitive, let the Python side
    interpret it" idiom /player's other fields already follow. ADDED 2026-08-22 (user
    directive, Blast Furnace live session) so a routine can read a game-tracked value (e.g. the
    official Blast Furnace plugin's own bar-dispenser-ready-count varbit, see
    routines/blast_furnace.py's BAR_VARBITS) instead of guessing whether an action worked from
    timing alone. Returns None if the value couldn't be read (bad/missing id, or the request
    failed) -- callers should treat that as "can't verify", not "value is zero"."""
    resp = api_get("/varbit", params={"id": varbit_id}) or {}
    return resp.get("value")


def _player_activity_snapshot():
    """(localX, localY) -- the player's own local position, sampled multiple times a tick while
    walking (RuneLite interpolates it smoothly, not just once per game tick) and held exactly
    constant the instant the player is truly stationary. Two consecutive identical snapshots is
    the direct signal that it's safe to trust a screen position for a click right now.

    REPLACES an animation+poseAnimation+x/y snapshot (removed 2026-08-13) -- CONFIRMED via user
    directive 2026-08-13 ("replace it with my method, it checks local X and Y ... a better
    standing still check") that the animation/pose fields made this check unnecessarily slow:
    they gate on whether an *action* is still playing, not on whether the click target's screen
    position is stale, which is the only thing callers here actually care about (see
    wait_until_player_idle's own docstring)."""
    p = get_player()
    return (p.get("localX"), p.get("localY"))


def wait_until_player_idle(timeout_s: float = 5.0, stable_reads: int = 2,
                            poll_interval_s: float = 0.15) -> bool:
    """Polls until the player's activity snapshot (see _player_activity_snapshot) holds
    unchanged across `stable_reads` consecutive polls, or timeout_s elapses. Returns True once
    idle, False if still busy/moving when the timeout hits.

    The point of this check is purely "is a screen position we resolved going to still be valid
    by the time the click lands" -- a moving player invalidates screen coordinates continuously,
    so callers about to trust one (resolving/clicking a world object) should wait for this first.
    It is NOT a check for "is an action's animation still playing"; an action performed from a
    fixed standing tile (a lever pull, mixing a potion, etc.) never moves localX/localY, so this
    returns True immediately even mid-animation -- that's intentional, see the 2026-08-13 change
    below.

    REPLACES an earlier camera-yaw/pitch-based settle check (wait_until_camera_settled/
    is_camera_settled, removed 2026-08-12) that turned out NOT to be the right signal --
    CONFIRMED live: this minigame's camera retargets almost every tick as the player character
    turns to face whatever it's about to interact with next, so cameraYaw==cameraYawTarget held
    on nearly every poll even while the net on-screen position kept drifting tick over tick
    (user report: "bunch of wild clicks still camera thing seems not solved" even after gating
    every world-object click on that signal). That was replaced 2026-08-12 with an
    animation+poseAnimation+x/y snapshot ("lets scrap the camera bullshit ... We go with
    animation instead"), which was in turn replaced 2026-08-13 with local-position-only (see
    _player_activity_snapshot) -- CONFIRMED via user directive: the animation/pose fields made
    every wait pay for an action's full animation length even when the player's on-screen
    position (the only thing a caller here is protecting) hadn't moved at all."""
    debug_log.log(
        "movement",
        f"wait_until_player_idle started: timeout={timeout_s}s, "
        f"stable_reads={stable_reads}, poll_interval={poll_interval_s}s",
    )
    deadline = time.time() + timeout_s
    last, stable = None, 0
    while time.time() < deadline:
        snap = _player_activity_snapshot()
        if snap == last:
            stable += 1
            if stable >= stable_reads:
                debug_log.log("movement", f"player IDLE: local={snap}")
                return True
        else:
            if last is not None:
                debug_log.log("movement", f"player MOVED: {last} -> {snap}")
            stable = 0
        last = snap
        time.sleep(poll_interval_s)
    debug_log.log("movement", f"wait_until_player_idle TIMED OUT: last local={last}")
    return False


def get_last_action():
    """GET /last-action (see LastActionProvider.java) -- the most recent MenuOptionClicked the
    client actually processed: {tick, option, target, id, menuAction, param0, param1, itemId}.
    Added to the bridge 2026-08-12 for Mastering Mixology but never actually wired to a Python
    caller until this wrapper (2026-08-13) -- confirms WHAT a click resolved to (the right
    option on the right target) immediately, from the game's own event, rather than inferring it
    after the fact from a side effect. Deliberately not a "wait for" helper like
    wait_for_chat_message -- this reports an ACTION being recognized, not one completing; pair it
    with wait_for_chat_message (or an inventory/state poll) for that."""
    return api_get("/last-action") or {}


def get_last_chat_message():
    return api_get("/last-chat-message") or {}


def wait_for_chat_message(predicate, since_tick=None, timeout_s=5.0, poll_interval_s=0.15):
    """Poll /last-chat-message (see LastChatMessageProvider.java) until a message newer than
    since_tick satisfies predicate(message_dict), or timeout_s elapses. Returns the matching
    message dict, or None on timeout.

    Added 2026-08-13 (Mastering Mixology, user directive): the game itself announces exactly
    when an action completes ("You finish concentrating the X.", "You collect some X from the
    mixing vessel.") -- this lets a caller confirm that directly from the game's own text
    instead of inferring completion from a side effect (an item id appearing in inventory) that
    could just as easily be caused by something else. `since_tick` should be the caller's own
    pre-action baseline (a get_last_chat_message() read taken right before acting) so a stale
    message left over from before the action can't false-positive."""
    debug_log.log(
        "mixology",
        f"wait_for_chat_message started: since_tick={since_tick}, timeout={timeout_s}s",
    )
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = get_last_chat_message()
        tick = msg.get("tick")
        if tick is not None and (since_tick is None or tick > since_tick) and predicate(msg):
            debug_log.log("mixology", f"wait_for_chat_message matched: {msg}")
            return msg
        time.sleep(poll_interval_s)
    debug_log.log("mixology", "wait_for_chat_message TIMED OUT")
    return None


def get_combat():
    return api_get("/combat") or {}


def get_prayers():
    return api_get("/prayers") or {}


def get_npcs(fresh=False):
    """Bulk NPC scan -- TickCache-gated (see CLAUDE.md's "Sub-tick data" section), so this can
    return a stale-by-up-to-a-tick snapshot. Prefer get_npc_by_index (re-checking a specific
    instance already committed to) or get_npc_screen_pos (nearest of a known type) when the
    caller already knows what it's looking for -- both are uncached single-entity lookups.
    Reserve this bulk call for a genuine area scan where the target isn't known yet (e.g.
    "whichever cow is closest")."""
    params = {"fresh": "true"} if fresh else None
    return api_get("/npcs", params=params) or []


def get_players():
    return api_get("/players") or []


def get_objects(radius=None, fresh=False):
    # Bulk scan -- TickCache-gated (see CLAUDE.md's "Sub-tick data" section). Prefer
    # get_object_at when the caller already knows the specific tile+id it's looking for (a fixed
    # object already committed to -- a plinth, a stile, a bank booth): that's an uncached
    # single-entity lookup, cheaper than this and never stale by a tick. Reserve this bulk call
    # for a genuine area scan where the target isn't known yet (e.g. "whichever sunstone rocks
    # spot is nearest and ready").
    #
    # radius (tiles, optional): bounds the scene walk to a box around the player instead of the
    # whole loaded scene. CONFIRMED live 2026-08-02 -- building screen-projection data for every
    # object in the entire scene, every game tick, regardless of whether any caller actually cares
    # about it, was a real source of client stutter. Every caller here already filters results by
    # its own distance check afterward, so passing that same bound through costs nothing.
    #
    # timeout=1.0, not api_get's default 0.3 -- CONFIRMED live 2026-08-08: even a radius-bounded
    # /objects call still builds clickbox/screen-projection data for every object in range, and an
    # object-dense area (Wyrmscraig: ~2900 objects within a 40-tile radius) measured 390-420ms for
    # this specific endpoint while every other endpoint (e.g. /state) measured under 20ms. Sharing
    # the same 300ms budget as those cheap endpoints meant /objects timed out on every single tick
    # here, and a timed-out call fails-safe to an empty list indistinguishable from "genuinely
    # nothing nearby" -- golem_crafting's bank_phase read that as no bank at all and safety-stopped
    # even standing right next to one. 1.0s comfortably covers the measured worst case with
    # headroom, without the caller having to wait meaningfully longer than that if the bridge is
    # genuinely down.
    #
    # fresh=True bypasses the bridge's per-tick cache for this one call -- see get_bank's fresh
    # kwarg for the motivating case.
    params = {}
    if radius is not None:
        params["radius"] = radius
    if fresh:
        params["fresh"] = "true"
    return api_get("/objects", params=params or None, timeout=1.0) or []


def get_object_at(x: int, y: int, plane: int, obj_id: int, radius=None):
    """Exact lookup by tile position + object id (uncached, unlike get_objects) -- the object
    equivalent of get_npc_by_index, for re-checking/re-aiming at a specific object already
    committed to mid-approach (e.g. while the player is still moving toward it). Matches by
    position+id rather than a live coordinate-to-LocalPoint conversion -- see
    ObjectProvider.java's getObjectAt for why. Same generous timeout as get_objects -- this
    walks the same scene."""
    params = {"x": x, "y": y, "plane": plane, "id": obj_id}
    if radius is not None:
        params["radius"] = radius
    data = api_get("/object/at", params=params, timeout=1.0)
    if not data or not data.get("found"):
        return None
    return data


def get_object_screen_pos_at(x: int, y: int, plane: int, obj_id: int, radius=None):
    data = get_object_at(x, y, plane, obj_id, radius=radius)
    if data is None:
        return None
    sx, sy = data.get("screenX"), data.get("screenY")
    if sx is None or sy is None:
        return None
    return int(sx), int(sy)


def get_tiles(fresh=False):
    """Bulk tile scan (walkability, collision, paint/model data) -- TickCache-gated, and only
    ever covers the player's CURRENT plane (see TileProvider.java's own computeTiles). Prefer
    get_tile_at when the caller already knows the specific tile it's checking (e.g. re-verifying
    a single anchor's walkability) -- that's an uncached single-tile lookup, cheaper than
    scanning the whole plane. Reserve this bulk call for a genuine area scan (e.g.
    golem_crafting.py's _nearest_walkable_tile searching a radius around a blocked anchor)."""
    params = {"fresh": "true"} if fresh else None
    return api_get("/tiles", params=params) or []


def get_tile_at(x: int, y: int, plane: int):
    """Exact lookup by world tile position (uncached, unlike get_tiles) -- the tile equivalent
    of get_object_at/get_ground_item_at. Added 2026-08-24 alongside /tile/at (TileProvider.java).
    Returns None if not found (e.g. a plane other than the player's current one -- see the
    Java-side docstring for why that's a real, inherited limitation, not a bug here)."""
    data = api_get("/tile/at", params={"x": x, "y": y, "plane": plane})
    if not data or not data.get("found"):
        return None
    return data


def get_spellbook():
    return api_get("/spellbook") or {"visible": False}


def get_menu():
    return api_get("/menu") or {"open": False}


def get_hover():
    """What a left-click would do RIGHT NOW: the default (top) entry of the client's live menu,
    read without opening any menu (MenuStateProvider's /hover, added 2026-08-27). None when the
    endpoint doesn't exist yet -- requires a client rebuild -- or the bridge is down."""
    return api_get("/hover")


def _hover_field_matches(expected, haystack: str) -> bool:
    """expected may be None (skip), one string, or a sequence of strings -- ANY match passes.
    The sequence form exists for state-dependent actions the caller doesn't get to choose
    (golem_crafting's GOLEM_PLINTH_OPTIONS: the game picks Start-/Shape-golem/Insert-core from
    the plinth's own state, and only something outside that set proves a wrong click)."""
    if expected is None:
        return True
    if isinstance(expected, str):
        expected = (expected,)
    return any(e.lower() in haystack for e in expected)


def hover_matches(expected_action=None, expected_target=None,
                  tries: int = None, retry_delay_s: float = None) -> bool:
    """True when the current hover's default action/target contain the expected strings
    (case-insensitive substring match, tags already stripped server-side; each expectation may
    also be a sequence of acceptable strings -- see _hover_field_matches).

    Deliberately never blocks when it CANNOT verify: /hover missing (un-rebuilt client) or a
    real right-click menu open (left-click semantics are the menu's, not the world's) both
    return True, degrading to the old click-then-check-/last-action behavior. A mismatch is
    re-read a few times first -- the hover text updates a render frame after the cursor stops,
    so the first read after a settle can be one frame stale."""
    if tries is None:
        tries = HOVER_VERIFY_TRIES
    if retry_delay_s is None:
        retry_delay_s = HOVER_VERIFY_RETRY_DELAY_S
    for attempt in range(tries):
        data = get_hover()
        if not data:
            return True
        if data.get("menuOpen"):
            return True
        option = (data.get("option") or "").lower()
        target = (data.get("target") or "").lower()
        if _hover_field_matches(expected_action, option) and _hover_field_matches(expected_target, target):
            return True
        if attempt < tries - 1:
            time.sleep(retry_delay_s)
    return False


def get_widget(group: int, child: int, index: int = None):
    """A clickable screen position for an interface element, by group/child id (WidgetProvider).

    The returned dict carries screenX/screenY like any other clickable entity, so it can be passed
    straight to click_entity/click_menu_action -- pass check_ui_blockers=False, same as every other
    interface click in this codebase (see travel.teleport_to_ferox for why: an interface element is
    always "behind a UI panel" by definition, and rotating the camera can never clear it).

    `index` addresses a dynamic child within a container (an inventory or bank slot). Get the ids
    by recording yourself doing the thing and reading them out of recorder.py's log, which decodes
    the packed id into group.child for exactly this purpose.

    Always returns a dict: {"found": False} when the widget doesn't exist, and found-but
    clickable:False when it exists with no drawn area (its interface is closed) -- those are
    different problems and worth telling apart."""
    params = {"group": group, "child": child}
    if index is not None:
        params["index"] = index
    return api_get("/widget", params=params) or {"found": False}


def get_widget_children(group: int, child: int):
    """Every populated dynamic child of a container widget, for discovering which index holds
    what. Returns a list (empty if the widget is absent or has no children)."""
    body = api_get("/widget/children", params={"group": group, "child": child}) or {}
    return body.get("children") or []


def get_active_tab():
    """The sidebar tab index currently on screen (WidgetProvider's /widget/active-tab --
    VarClientInt.INVENTORY_TAB), the same authoritative per-tick signal PrayerProvider already
    trusts for its own tab (see that file's comment: a widget group's own non-null/hidden state
    is NOT reliable evidence of which tab is actually showing -- it can stay loaded from an
    earlier session even while a different tab is the one visible). Returns None when the
    endpoint doesn't exist yet (un-rebuilt client) or the bridge is down -- callers must treat
    that as "can't verify", not "wrong tab", same degrade-gracefully stance as hover_matches."""
    data = api_get("/widget/active-tab")
    if not data:
        return None
    return data.get("tab")


def ensure_ui_state(action_fn, check_fn, tries: int = 3, retry_delay_s=(0.2, 0.35)) -> bool:
    """Generic verify-then-retry wrapper for a UI action (a tab hotkey tap, a widget click)
    whose success can be confirmed by re-reading live state. Returns True immediately if
    check_fn() already holds -- the idempotent-skip idiom open_inventory_tab/open_prayer_tab
    already used -- otherwise calls action_fn(), waits, and re-checks, up to `tries` times.

    Added because a single unconfirmed tap/click was being trusted as success outright
    throughout this codebase (banker.switch_bank_tab clicked a bank tab once and returned True
    unconditionally; golem_crafting.py blind-tapped the inventory hotkey with no check at all).
    A dropped keypress, a simulated misclick (click_entity's MISTAKE_MISCLICK_CHANCE), or a
    stray click landing on the wrong element all used to desync a routine's assumption about
    what's on screen -- surfacing later as a confusing, unrelated safety stop instead of being
    caught and retried here where the cause is still known.

    check_fn() returning None means the signal can't be read at all (e.g. an un-rebuilt client
    missing a new endpoint) -- fails OPEN in that case (fires the action once and trusts it,
    same degrade-gracefully stance as hover_matches) rather than blocking every caller on a
    feature that doesn't exist yet."""
    state = check_fn()
    if state is None:
        action_fn()
        return True
    if state:
        return True
    for _ in range(tries):
        action_fn()
        human_delay(*retry_delay_s)
        if check_fn():
            return True
    return False


def click_widget(ser: serial.Serial, group: int, child: int, index: int = None,
                  verify_fn=None, tries: int = 3, offset=(0, 0)) -> bool:
    """Click an interface element by group/child id (see get_widget) -- the same two calls
    (get_widget then click_entity) every existing widget click site already makes by hand,
    offered here as one call for new sites that want click-landed verification for free instead
    of hand-rolling it (see banker.switch_bank_tab for the same idiom applied to bank tabs).

    verify_fn (optional, zero-arg, same Optional[bool] contract as ensure_ui_state's check_fn)
    confirms the click actually landed and retries via ensure_ui_state if not. Without it, this
    is just get_widget + a single click_entity call, same as every existing call site."""
    widget = get_widget(group, child, index=index)
    if not widget.get("clickable") or widget.get("screenX") is None:
        return False

    def do_click():
        click_entity(ser, widget, offset=offset, check_ui_blockers=False)

    if verify_fn is None:
        do_click()
        return True

    return ensure_ui_state(do_click, verify_fn, tries=tries)


def get_consumables():
    return api_get("/consumables") or {"food": [], "prayer": []}


def get_ground_items(fresh=False):
    """Bulk ground-item scan -- TickCache-gated (see CLAUDE.md's "Sub-tick data" section).
    Prefer get_ground_item_at when the caller already knows the specific tile+id it's looking
    for (an item already committed to looting): that's an uncached single-entity lookup.
    Reserve this bulk call for a genuine area scan where the target isn't known yet."""
    params = {"fresh": "true"} if fresh else None
    return api_get("/ground-items", params=params) or []


def get_ground_item_at(x: int, y: int, plane: int, item_id: int):
    """Exact lookup by tile position + item id (uncached, unlike get_ground_items) -- the
    ground-item equivalent of get_object_at/get_npc_by_index, for re-checking/re-aiming at a
    specific item already committed to looting mid-approach (e.g. while the player is still
    moving toward it). See GroundItemProvider.java's getGroundItemAt for the matching logic."""
    data = api_get("/ground-item/at", params={"x": x, "y": y, "plane": plane, "id": item_id})
    if not data or not data.get("found"):
        return None
    return data


def get_ground_item_screen_pos_at(x: int, y: int, plane: int, item_id: int):
    data = get_ground_item_at(x, y, plane, item_id)
    if data is None:
        return None
    sx, sy = data.get("screenX"), data.get("screenY")
    if sx is None or sy is None:
        return None
    return int(sx), int(sy)


def get_inventory():
    return api_get("/inventory") or []


def get_rune_pouch():
    """Rune pouch contents (InventoryProvider./rune-pouch, added 2026-08-27).

    A rune pouch is a CONTAINER, so get_inventory reports only the pouch item and says nothing
    about the runes inside -- which left a routine unable to see it was about to run out until a
    cast actually failed. The contents live in varbits, not an ItemContainer.

    Returns {"carried": bool, "runes": [{"slot", "type", "quantity", "id"?, "name"?}], "total"}.
    `carried` is reported separately from an empty `runes` list on purpose: every underlying
    varbit reads 0 both when the pouch is empty and when no pouch is held at all, and those are
    different problems -- one is "restock", the other is "you forgot the pouch".

    `id`/`name` are best-effort (see the provider's own comment); a slot always carries its raw
    `type` and `quantity`, so counting runes works even if the name lookup does not.

    Returns an empty dict against a client too old to have the endpoint, so callers must treat a
    missing "carried" key as "cannot tell" rather than "no pouch"."""
    return api_get("/rune-pouch") or {}


def rune_pouch_count(name_substring: str):
    """Units of a named rune in the pouch, or None if that cannot be determined.

    None and 0 are deliberately different: None means the endpoint is missing, the name lookup
    failed, or no pouch is carried -- none of which are evidence of an empty pouch. Only a real
    zero reading returns 0."""
    pouch = get_rune_pouch()
    if not pouch or not pouch.get("carried"):
        return None
    wanted = name_substring.lower()
    for rune in pouch.get("runes", []):
        rune_name = (rune.get("name") or "").lower()
        if rune_name and wanted in rune_name:
            return rune.get("quantity", 0)
    # A pouch that resolves names but doesn't hold this one genuinely has none of it.
    if any(r.get("name") for r in pouch.get("runes", [])):
        return 0
    return None


def get_bank(fresh=False):
    # fresh=True bypasses the bridge's per-tick cache for this one call -- pass it right after an
    # action that just changed bank state (a withdrawal/deposit) instead of waiting out a full
    # game tick for BankProvider's cache to naturally reflect it. See banker.withdraw_loadout's
    # own fresh parameter for the motivating call site.
    params = {"fresh": "true"} if fresh else None
    return api_get("/bank", params=params) or {"open": False}


def get_sailing():
    return api_get("/sailing") or {
        "navigating": False,
        "facilitiesTabOpen": False,
        "sailToggle": {},
        "boatSpeed": None,
    }


def get_pyramid_plunder():
    return api_get("/pyramid-plunder") or {"inRun": False, "roomNumber": 0, "timerTicks": 0, "trapActive": False}


def get_equipment():
    return api_get("/equipment") or {}


def get_graphics_objects(fresh=False):
    """Bulk ground-anchored spotanim scan (e.g. a telegraphed AoE, Mastering Mixology's
    Agitator/Alembic flash cue) -- TickCache-gated. Prefer get_graphics_object_at when the
    caller already knows the specific tile+id it's watching for (a known timing cue at a known
    station/tile) -- that's an uncached single-entity lookup. Reserve this bulk call for a
    genuine area scan where the effect's id/position isn't already known."""
    params = {"fresh": "true"} if fresh else None
    return api_get("/graphics-objects", params=params) or []


def get_graphics_object_at(x: int, y: int, plane: int, obj_id: int):
    """Exact lookup by tile position + graphics-object id (uncached, unlike get_graphics_objects)
    -- the graphics-object equivalent of get_object_at/get_ground_item_at. Added 2026-08-24
    alongside /graphics-object/at (GraphicsObjectProvider.java). NOT confirmed correct inside an
    instance -- see that endpoint's own docstring on why it inherits the bulk endpoint's
    unverified coordinate handling there."""
    data = api_get("/graphics-object/at", params={"x": x, "y": y, "plane": plane, "id": obj_id})
    if not data or not data.get("found"):
        return None
    return data


def get_tormented_demon_config():
    return api_get("/tormented-demon-config")


def get_firemaking_config():
    return api_get("/firemaking-config")


def get_skills():
    return api_get("/skills") or {}


def get_blast_furnace_config():
    return api_get("/blast-furnace-config")


def get_golem_crafting_config():
    return api_get("/golem-crafting-config")


def get_mastering_mixology_config():
    return api_get("/mastering-mixology-config")


def get_mixology_state():
    return api_get("/mixology-state") or {
        "inLab": False, "orders": [], "hopper": {"mox": 0, "aga": 0, "lye": 0},
        "stations": {}, "digweed": {},
    }


def get_golem():
    """Live golem-crafting state from GolemProvider, or None if the endpoint is unavailable.

    Deliberately returns None rather than an empty default (unlike get_mixology_state above):
    routines/golem_crafting.py treats a missing /golem as a hard stop with a "rebuild the client"
    message, because the alternative -- falling back to the old blind guess-and-check path --
    would mean two divergent code paths for the same phase, which is exactly what produced the
    desync bug this endpoint exists to fix."""
    return api_get("/golem")


def get_minimap_walk_point(x: int, y: int, plane: int):
    return api_get("/minimap/walk", params={"x": x, "y": y, "plane": plane}) or {"onMinimap": False}


def get_ground_walk_point(x: int, y: int, plane: int):
    return api_get("/ground/walk", params={"x": x, "y": y, "plane": plane}) or {"onScreen": False}


def get_ground_walk_screen_pos(x: int, y: int, plane: int):
    """refresh_target callable for a stationary world tile -- same shape as
    get_npc_screen_pos_by_index, but for a fixed (x, y, plane) instead of a moving NPC. The tile
    itself never moves, but its *screen* projection depends on the camera, which can shift
    (auto-follow scroll, momentum from a prior nudge_camera_yaw) during move_to_human's travel
    time -- this lets the final approach re-aim at the tile's current screen position instead of
    trusting a snapshot taken before the camera moved."""
    data = get_ground_walk_point(x, y, plane)
    if not data.get("onScreen"):
        return None
    x, y = data.get("screenX"), data.get("screenY")
    if x is None or y is None:
        return None
    return int(x), int(y)


def request_shortest_path(x: int, y: int, plane: int, config: dict | None = None):
    """Ask the (separately installed) Shortest Path plugin to compute a
    route to (x, y, plane). requested=False if that plugin isn't
    installed/running -- see get_shortest_path_result to poll for it.

    `config` is a per-request override of that plugin's own settings, keyed by
    its ShortestPathConfig option names (useFairyRings, useTeleportationItems,
    avoidWilderness, calculationCutoff, ...) -- see travel.TRANSPORT_POLICY,
    which is where the values actually come from. Sent as cfg.-prefixed query
    params so a config key can never collide with x/y/plane."""
    params = {"x": x, "y": y, "plane": plane}
    for key, value in (config or {}).items():
        params[f"cfg.{key}"] = _config_param_value(value)
    return api_get("/shortest-path/request", params=params) or {"requested": False}


def _config_param_value(value):
    """Shortest Path's own override map only reads Boolean/Integer/String values, and the bridge
    re-derives which of those a param is from its text -- so booleans have to go out lowercase
    ("true"), not Python's str(True) == "True", which would parse as a plain string and be
    silently ignored by a boolean config getter."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def get_shortest_path_result():
    return api_get("/shortest-path/result") or {"active": False, "done": False, "steps": []}


def get_config():
    return api_get("/config")


def get_slayer_config():
    return api_get("/slayer-config") or {}


def get_npc_screen_pos(npc_id: int):
    """Fresh (screenX, screenY) for the nearest NPC of this *type*, or None if
    none is found. Fine for picking a new target, but NOT for re-checking one
    you've already committed to -- in a pack of identical monsters this can
    return a different individual than the one you started attacking. See
    get_npc_screen_pos_by_index for that case."""
    data = api_get("/npc/nearest", params={"id": npc_id})
    if not data or not data.get("found"):
        return None
    x, y = data.get("screenX"), data.get("screenY")
    if x is None or y is None:
        return None
    return int(x), int(y)


def get_npc_by_index(npc_index: int):
    """Fresh data for the exact NPC instance (stable per-monster identifier,
    distinct from its shared type id), or None if it's no longer found (e.g.
    it died or despawned). Use this to re-check/re-aim at a specific target
    already committed to -- unlike get_npc_screen_pos (by type), this can
    never resolve to a different individual of the same species."""
    data = api_get("/npc/by-index", params={"index": npc_index})
    if not data or not data.get("found"):
        return None
    return data


def get_npc_screen_pos_by_index(npc_index: int):
    data = get_npc_by_index(npc_index)
    if data is None:
        return None
    x, y = data.get("screenX"), data.get("screenY")
    if x is None or y is None:
        return None
    return int(x), int(y)


# ---------------- refresh_target factories ----------------
# Added 2026-08-14. move_to_human/click_entity's refresh_target parameter has existed for a
# while, but every caller that used it (slayer.py, tormented_demon.py, golem_crafting.py) hand-
# rolled its own zero-arg lambda wrapping one of the two functions above -- these just give that
# lambda a name so future routines reuse it instead of re-deriving it. Purely additive: existing
# inline lambdas keep working exactly as before, nothing here changes their behavior.
def npc_index_refresh_target(npc_index: int):
    """refresh_target factory for tracking a specific committed-to NPC instance through a
    click's flight -- by index, so it can never resolve to a different individual of the same
    species (see get_npc_by_index).

    DEADBANDED ON THE NPC'S TILE, not on its pixels (2026-08-27). NPCProvider aims at
    Actor.getConvexHull(), which is recomputed every frame, so an animating monster's reported
    aim point moves even when the monster does not. MEASURED live against a Gargoyle that never
    changed tile: the point jittered p50 7.2px, p90 35.2px, max 55.4px frame to frame. That is
    above human_move's HM2_REPLAN_MIN_DRIFT_PX (6.0) on more than half of all polls, so
    move() kept replanning its approach as the cursor arrived -- USER-REPORTED as "some weird
    defects at the very end of targeting".

    Aiming at the hull is still right; re-aiming at ANIMATION is not. A human looks at a
    monster, decides where it is, and clicks -- they do not re-target as its arms swing. So this
    returns a held point and only re-resolves when the NPC's actual world tile changes, which is
    when a human would look again. A monster that walks is still tracked; one that merely
    animates is ignored.

    DEADBAND KEY WIDENED 2026-08-27 (same day, follow-up): tile-only holding was itself a bug
    for the click-while-moving pattern -- when the PLAYER is the thing moving, the NPC's tile
    never changes while its screen projection slides continuously, so the hold served a stale
    point for the whole flight and the click landed behind. The key now covers every motion
    source that actually moves the projection: the NPC's tile AND interpolated LocalPoint, the
    player's own LocalPoint, and the camera -- all from the same /npc/by-index response (fields
    added to NPCProvider the same day; absent on an older client build, where this degrades to
    the original tile-only key). LocalPoints are quantized (NPC_HOLD_LOCAL_QUANTUM) so only
    real motion re-resolves; a stationary scene still holds through hull-animation jitter.

    NOTE the state lives in the closure, so a factory is good for ONE click's flight -- which is
    how every caller already uses it (built inline at the call site). Reusing one across clicks
    would hold a stale point for as long as the whole scene stayed still.
    """
    held = {"key": None, "point": None}

    def refresh():
        data = get_npc_by_index(npc_index)
        if data is None:
            return None
        x, y = data.get("screenX"), data.get("screenY")
        if x is None or y is None:
            return None
        key = _npc_view_key(data)
        if held["point"] is not None and key == held["key"]:
            return held["point"]
        held["key"] = key
        held["point"] = (int(x), int(y))
        return held["point"]

    return refresh


def _npc_view_key(data: dict) -> tuple:
    """Everything that moves an NPC's screen projection, folded into one comparable key --
    see npc_index_refresh_target. Fields missing from an older client build come through as
    None, so the key degrades gracefully toward the original tile-only behavior."""
    def quant(field):
        v = data.get(field)
        return None if v is None else int(v) // NPC_HOLD_LOCAL_QUANTUM

    return (
        (data.get("x"), data.get("y"), data.get("plane")),
        (quant("npcLocalX"), quant("npcLocalY")),
        (quant("playerLocalX"), quant("playerLocalY")),
        (data.get("cameraYaw"), data.get("cameraPitch"), data.get("cameraScale")),
    )


def ground_tile_refresh_target(x: int, y: int, plane: int):
    """refresh_target factory for tracking a fixed world tile's current screen projection
    through a click's flight -- the tile itself never moves, but its screen position shifts
    with the camera (see get_ground_walk_screen_pos)."""
    return lambda: get_ground_walk_screen_pos(x, y, plane)


def object_refresh_target(x: int, y: int, plane: int, obj_id: int, radius=None):
    """refresh_target factory for tracking a specific world object's current screen projection
    through a click's flight -- added 2026-08-14 alongside /object/at (ObjectProvider.java) so a
    routine clicking an object (a bank booth, a tree, a plinth, ...) can be moving at the same
    time, the same way npc_index_refresh_target already covers NPCs. Objects don't move, but
    their screen position shifts with the camera exactly like a ground tile's does, and a
    multiloc object's clickbox/name can change between the click's decision and the moment the
    cursor actually arrives (see buildObjectData's own comment on impostor variants)."""
    return lambda: get_object_screen_pos_at(x, y, plane, obj_id, radius=radius)


def ground_item_refresh_target(x: int, y: int, plane: int, item_id: int):
    """refresh_target factory for tracking a specific ground item's current screen projection
    through a click's flight -- added 2026-08-14 alongside /ground-item/at
    (GroundItemProvider.java), same role object_refresh_target plays for objects. Items don't
    move, but their screen position shifts with the camera exactly like an object's or ground
    tile's does."""
    return lambda: get_ground_item_screen_pos_at(x, y, plane, item_id)


def is_logged_in():
    return get_state().get("loggedIn", False)


def get_login_status():
    return api_get("/login/status") or {"gameState": "UNKNOWN", "loggedIn": False}


def get_ammo_count():
    player = get_player()
    if "ammoQty" in player:
        return player["ammoQty"]
    if "weaponQty" in player:
        return player["weaponQty"]
    return None

# ---------------- UDP event stream ----------------
def make_udp_listener(port: int = UDP_PORT) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # (I5, 2026-08-21) Bump past Windows' 64KB default: the spawn-event budget
    # (ControllerPlugin.SPAWN_EVENT_CAP_PER_TICK) allows up to 40 OBJECT_/GROUND_ITEM_ packets per
    # tick, and routines that never opted into ctx.events (or that block for seconds between
    # pump() calls) only drain the socket once per main-loop iteration -- the kernel silently
    # drops on overflow, and a dropped packet is not necessarily a harmless spawn: PLAYER_DIED
    # shares the same socket.
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    s.bind(("127.0.0.1", port))
    s.setblocking(False)
    return s


def poll_udp(s: socket.socket) -> list:
    """Return all pending (event_type, payload_dict_or_None) messages without blocking.

    BridgePlugin sends bare "TYPE" for payload-less events and "TYPE|<json>"
    otherwise (see UdpEventSender) -- there is no "RUNELITE_EVENT|" prefix.
    """
    events = []
    while True:
        ready, _, _ = select.select([s], [], [], 0)
        if not ready:
            break
        data, _addr = s.recvfrom(8192)
        raw = data.decode("utf-8", errors="ignore")
        if "|" in raw:
            event_type, payload_json = raw.split("|", 1)
            try:
                payload = json.loads(payload_json)
            except ValueError:
                payload = None
        else:
            event_type, payload = raw, None
        events.append((event_type, payload))
    return events


def find_runelite_window(title_substring: str = "RuneLite"):
    hwnds = []

    def enum_windows(hwnd, _):
        if title_substring in win32gui.GetWindowText(hwnd):
            hwnds.append(hwnd)

    win32gui.EnumWindows(enum_windows, None)
    return hwnds[0] if hwnds else None


# ---------------- Windows cursor position ----------------
user32 = ctypes.WinDLL("user32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _windows_pointer_acceleration_enabled():
    """The live "Enhance pointer precision" setting: True/False, or None when it can't be read
    (non-Windows, API failure). SPI_GETMOUSE returns [threshold1, threshold2, acceleration];
    the third element is 0 with the checkbox off, nonzero with it on."""
    try:
        params = (ctypes.c_int * 3)()
        if not ctypes.windll.user32.SystemParametersInfoW(0x0003, 0, ctypes.byref(params), 0):
            return None
        return params[2] != 0
    except Exception:
        return None


def warn_if_pointer_acceleration() -> bool:
    """Loud startup warning when Windows pointer acceleration is on (added 2026-08-27). The
    whole movement stack assumes a LINEAR px-per-mouse-unit ratio -- human_move's _px_per_unit
    calibration is a single scalar -- and acceleration makes that ratio speed-dependent, so
    every fast flight over- or under-travels and _learn_scale thrashes between contradictory
    measurements instead of converging. This was previously a manual checklist item in the
    live-debugging docs; a human forgetting it produced clicks that are consistently off by a
    speed-dependent amount, which looks exactly like a calibration bug. Warn-only rather than a
    hard stop: reading the setting can race a user actively changing it, and a headless test
    environment has no pointer to be wrong about."""
    accel = _windows_pointer_acceleration_enabled()
    if accel:
        debug_log.log("!", "Windows 'Enhance pointer precision' (pointer acceleration) is ON -- "
                           "every planned mouse path will mis-travel by a speed-dependent amount "
                           "and the pointer-scale calibration cannot converge. Turn it off in "
                           "Settings > Devices > Mouse > Additional mouse options > Pointer Options.")
    return bool(accel)


_dpi_scale_cache = None


def get_dpi_scale() -> float:
    """Ratio between this (DPI-aware, see set_dpi_aware) process's real physical pixels and the
    RuneLite/Java client's own reported screen coordinates -- CONFIRMED live 2026-08-08 (MCP
    debug session): the Java client (launched via `./gradlew.bat run`, a bare java.exe with no
    DPI-aware manifest) reports AWT screen coordinates (Component.getLocationOnScreen,
    MouseInfo, Toolkit.getScreenSize) in its own device-independent "user space" -- this is a
    fixed property of the JDK 9+ HiDPI model, NOT a fixable "DPI-unaware app" bug: neither a JVM
    system property (-Dsun.java2d.dpiaware=true), an explicit native
    SetProcessDpiAwarenessContext/SetProcessDPIAware call from Java (via JNA, in
    ExamplePluginTest.main, before any window is created), nor an OS-level per-executable
    HIGHDPIAWARE compatibility override changed the ratio at all -- it stayed exactly the
    display's scale factor (confirmed 1.25 on a 125%-scaled display: moving the real OS cursor
    to physical (600, 500) had RuneLite's own /state report the mouse at (480, 400), every
    single time, regardless of which of those three fixes was in effect). So every screenX/
    screenY (and window/canvas/mouse screen-position field) the bridge returns is in that
    "user space" and must be scaled up by this ratio before it's a real physical-pixel target
    for the Arduino-driven cursor -- applied centrally in api_get() via _scale_screen_coords,
    not at each of click_entity/hover_entity/click_menu_action's call sites.

    Cached for the process lifetime -- the display's DPI doesn't change mid-session, and this
    would otherwise cost a GetDC/GetDeviceCaps/ReleaseDC round trip on every single api_get()
    call.
    """
    global _dpi_scale_cache
    if _dpi_scale_cache is None:
        try:
            hdc = user32.GetDC(None)
            dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            user32.ReleaseDC(None, hdc)
            _dpi_scale_cache = dpi_x / 96.0 if dpi_x else 1.0
        except Exception:
            _dpi_scale_cache = 1.0
    return _dpi_scale_cache


# Field names that are unambiguously screen-pixel positions/sizes wherever they appear in a
# bridge response, at any nesting depth -- safe to scale by exact key match. Deliberately NOT
# a blind "x"/"y" match: those bare names are also used throughout the bridge for WORLD tile
# coordinates (e.g. /player, /objects' own "x"/"y"), which must never be scaled.
_SCREEN_COORD_KEYS = {
    "screenX", "screenY",
    "windowX", "windowY", "windowWidth", "windowHeight",
    "canvasScreenX", "canvasScreenY", "canvasWidth", "canvasHeight",
    "mouseScreenX", "mouseScreenY",
}


def _scale_screen_coords(obj, scale: float):
    """Recursively multiplies every _SCREEN_COORD_KEYS field (and each uiBlockers entry's own
    x/y/width/height -- ambiguously named like world coordinates, but a fixed, known shape from
    GameStateProvider, so safe to special-case) by `scale` in place. See get_dpi_scale's
    docstring for why this needs to exist at all. Mutates and returns `obj` so api_get can just
    do `result = _scale_screen_coords(result, get_dpi_scale())`."""
    if scale == 1.0:
        return obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _SCREEN_COORD_KEYS and isinstance(value, (int, float)):
                obj[key] = int(round(value * scale))
            elif key == "uiBlockers" and isinstance(value, list):
                for blocker in value:
                    if isinstance(blocker, dict):
                        for bk in ("x", "y", "width", "height"):
                            if isinstance(blocker.get(bk), (int, float)):
                                blocker[bk] = int(round(blocker[bk] * scale))
            else:
                _scale_screen_coords(value, scale)
    elif isinstance(obj, list):
        for item in obj:
            _scale_screen_coords(item, scale)
    return obj


def get_cursor_pos() -> tuple:
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(pt.x), int(pt.y)


# ---------------- Serial protocol to Arduino ----------------
def send_line(ser: serial.Serial, s: str) -> None:
    ser.write((s + "\n").encode("ascii", errors="ignore"))


# Firmware protocol level, set ONCE by probe_firmware_protocol() in main(). 2 = pre-V legacy
# (the firmware ignores unknown lines and answers nothing). Everything v3-only -- the X flush
# in human_move.move, scroll_wheel, the startup R -- must gate on this rather than on graceful
# degradation: an IGNORED X means the queued motion plays anyway, which would silently corrupt
# the mover's rewound position bookkeeping.
FIRMWARE_PROTOCOL = 2


def probe_firmware_protocol(ser) -> int:
    """Ask the firmware its protocol level ("V" -> "HB <n>"). Called once at startup, never
    mid-flight (it consumes the input buffer). The negative path is the 1 s read timeout set
    at port open; any failure at all reports legacy rather than raising."""
    try:
        ser.reset_input_buffer()
        send_line(ser, "V")
        line = ser.readline().decode("ascii", "replace").strip()
        if line.startswith("HB "):
            return max(2, int(line.split()[1]))
    except Exception:
        pass
    return 2


def scroll_wheel(ser: serial.Serial, clicks: int) -> None:
    """Scroll the wheel `clicks` detents (positive = away from the user). Queued firmware-side,
    so it keeps program order with motion and clicks. Silently unavailable pre-v3."""
    if FIRMWARE_PROTOCOL >= 3 and clicks:
        send_line(ser, f"W {max(-127, min(127, int(clicks)))}")


def move_rel_arduino(ser: serial.Serial, dx: int, dy: int, *, max_step: int = 20) -> None:
    while dx != 0 or dy != 0:
        sx = max(-max_step, min(max_step, dx))
        sy = max(-max_step, min(max_step, dy))
        send_line(ser, f"M {sx} {sy}")
        dx -= sx
        dy -= sy
        human_delay(0.0005, 0.0015)  # small random jitter


def mouse_left_down(ser: serial.Serial) -> None:
    send_line(ser, "MLD")


def mouse_left_up(ser: serial.Serial) -> None:
    send_line(ser, "MLU")


def mouse_left_click(ser: serial.Serial) -> None:
    send_line(ser, "MLC")


def click_human(ser: serial.Serial) -> None:
    debug_log.log("action", f"click_human at {get_cursor_pos()}")
    mouse_left_down(ser)
    human_delay(*CLICK_HOLD_RANGE_S)
    mouse_left_up(ser)
    human_delay(0.03, 0.09)


def mouse_right_down(ser: serial.Serial) -> None:
    send_line(ser, "MRD")


def mouse_right_up(ser: serial.Serial) -> None:
    send_line(ser, "MRU")


def mouse_right_click(ser: serial.Serial) -> None:
    send_line(ser, "MRC")


def right_click_human(ser: serial.Serial) -> None:
    debug_log.log("action", f"right_click_human at {get_cursor_pos()}")
    mouse_right_down(ser)
    human_delay(*CLICK_HOLD_RANGE_S)
    mouse_right_up(ser)
    human_delay(0.03, 0.09)


def key_tap(ser: serial.Serial, key: str) -> None:
    debug_log.log("action", f"key_tap {key}")
    send_line(ser, f"KT {key}")


def key_down(ser: serial.Serial, key: str) -> None:
    send_line(ser, f"KD {key}")


def key_up(ser: serial.Serial, key: str) -> None:
    send_line(ser, f"KU {key}")


def type_digits_and_enter(ser: serial.Serial, text: str) -> None:
    """Type each character of `text` (expected to be a plain digit string, e.g. a coin
    amount) via key_tap, then press Enter -- for chatbox "Enter amount" numeric prompts
    (e.g. the Blast Furnace coffer's Use action, see routines/blast_furnace.py).

    ASSUMPTION, NOT YET VERIFIED LIVE: that the Arduino firmware accepts single-digit-
    character key names ("0".."9") the same way it already accepts named keys like "ESC"/
    "SPACE" elsewhere in this file, and that "ENTER" is a valid key name for it. Verify with
    a small manual test (e.g. typing an amount into any existing "Enter amount" prompt)
    before relying on this for an unattended coffer top-up -- same epistemic honesty as this
    project's other live-verification-needed assumptions (see travel.py's Forced Actions
    notes, tormented_demon.py's reflection caveats)."""
    for ch in text:
        key_tap(ser, ch)
        human_delay(0.05, 0.12)
    key_tap(ser, "ENTER")
    human_delay(0.2, 0.4)


def type_text(ser: serial.Serial, text: str) -> None:
    """Type each character of `text` via key_tap -- letters and spaces, no trailing Enter, for
    a live-filtering text field like the bank's search box (see banker.py's search_bank) rather
    than type_digits_and_enter's numeric-chatbox-prompt use case.

    ASSUMPTION, NOT YET VERIFIED LIVE: that the Arduino firmware accepts single-letter key names
    ("a".."z") the same way it accepts the single-digit key names type_digits_and_enter uses
    (itself still flagged unverified there) and named keys like "SPACE" elsewhere in this file.
    Verify live (type something into the bank search box, confirm the right characters actually
    appear) before trusting this for anything unattended."""
    for ch in text:
        key = "SPACE" if ch == " " else ch
        key_tap(ser, key)
        human_delay(0.05, 0.12)
    human_delay(0.2, 0.4)


INVENTORY_TAB_KEY = "w"
PRAYER_TAB_KEY = "r"


def open_inventory_tab(ser: serial.Serial) -> bool:
    """Idempotent-skip if already open, otherwise taps the hotkey and RETRIES until
    inventoryTabOpen actually confirms the switch (see ensure_ui_state) -- a single unconfirmed
    tap used to be trusted as success (CONFIRMED live bug: golem_crafting.py's own blind
    key_tap(ctx.ser, "w") calls, which this now replaces)."""
    return ensure_ui_state(
        lambda: key_tap(ser, INVENTORY_TAB_KEY),
        lambda: get_state().get("inventoryTabOpen"),
    )


def open_prayer_tab(ser: serial.Serial) -> bool:
    """Tap the prayer-tab hotkey unless it's already open, retrying until PrayerProvider's
    "tabOpen" confirms the switch (see ensure_ui_state) -- its "icons" are only populated while
    that's true."""
    return ensure_ui_state(
        lambda: key_tap(ser, PRAYER_TAB_KEY),
        lambda: get_prayers().get("tabOpen"),
    )


# ---------------- Human-like trajectory generation ----------------
def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def ease_in_out(t: float) -> float:
    # Real click-to-click recordings show speed peaking near t=1/3 (a fast
    # ballistic ramp-up, then a longer decel tail) rather than the symmetric
    # peak-at-0.5 bell a plain smoothstep produces. This is the CDF of
    # t**2 * (1-t)**4, whose density peaks at t=1/3.
    return 35 * t**3 - 105 * t**4 + 126 * t**5 - 70 * t**6 + 15 * t**7


@dataclass
class Bezier:
    p0: tuple
    p1: tuple
    p2: tuple
    p3: tuple

    def point(self, t: float) -> tuple:
        x0, y0 = self.p0
        x1, y1 = self.p1
        x2, y2 = self.p2
        x3, y3 = self.p3
        u = 1.0 - t
        tt = t * t
        uu = u * u
        uuu = uu * u
        ttt = tt * t
        x = uuu * x0 + 3 * uu * t * x1 + 3 * u * tt * x2 + ttt * x3
        y = uuu * y0 + 3 * uu * t * y1 + 3 * u * tt * y2 + ttt * y3
        return x, y


def build_bezier_path(start: tuple, end: tuple) -> Bezier:
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    dist = math.hypot(dx, dy)

    if dist < 1e-6:
        perp_x, perp_y = 0.0, 0.0
    else:
        perp_x = -dy / dist
        perp_y = dx / dist

    # User directive 2026-08-14: reduced from (0.15-0.35 fraction, 300.0 cap) -- raw mouse
    # movement/path is never transmitted to OSRS's servers (packet-based protocol, only
    # discrete action results and their timing are), so the curve's exact bow has no bearing on
    # server-side detection; what actually matters there (click timing via estimate_duration_ms,
    # aim-point variance via pick_aim_point/misclicks) is untouched by this change. Kept
    # deliberately nonzero rather than a straight line, for screen-recording/visual plausibility.
    profile = get_human_profile()
    curve_mag = clamp(dist * random.uniform(profile.curve_mag_low, profile.curve_mag_high), 8.0, 150.0)
    curve_sign = random.choice([-1.0, 1.0])
    # Real aim-trainer recordings show ~2/3 of moves cross back over the
    # straight line at least once -- about half here, since c2 is only one
    # of two places a reversal can come from (the other being end-jitter).
    c2_sign = -1.0 if random.random() < 0.50 else 1.0

    c1_t = random.uniform(0.20, 0.38)
    c2_t = random.uniform(0.62, 0.82)

    c1x = sx + dx * c1_t + perp_x * curve_mag * curve_sign * random.uniform(0.6, 1.1)
    c1y = sy + dy * c1_t + perp_y * curve_mag * curve_sign * random.uniform(0.6, 1.1)
    c2x = sx + dx * c2_t + perp_x * curve_mag * curve_sign * c2_sign * random.uniform(0.4, 1.0)
    c2y = sy + dy * c2_t + perp_y * curve_mag * curve_sign * c2_sign * random.uniform(0.4, 1.0)

    ex2 = ex + random.uniform(-0.4, 0.4)
    ey2 = ey + random.uniform(-0.4, 0.4)

    return Bezier((sx, sy), (c1x, c1y), (c2x, c2y), (ex2, ey2))


def estimate_duration_ms(dist: float, min_ms: float = MOVE_MIN_DURATION_MS) -> float:
    """min_ms is the duration floor. It dominates completely below ~130px, which is every leg of
    a refresh_legs tracking flight -- see TRACKING_LEG_MIN_DURATION_MS for why those legs pass a
    lower one, and why the default is right for every ordinary click movement."""
    base = 90 + 0.55 * dist
    base = base ** 0.92
    # Real click-to-click recordings (aim trainer and live OSRS play) run
    # ~4-4.5x longer than this base estimate, but part of that gap is
    # decision/scan latency before the hand starts moving, not slower
    # movement itself -- take roughly half the gap here.
    base *= 1.9
    jitter = random.uniform(0.85, 1.15)
    return clamp(base * jitter, min_ms, 1800)


def micro_pause_chance(dist: float) -> bool:
    if dist < 350:
        return random.random() < 0.05
    return random.random() < 0.12


# ---------------- Aim radius + optional overshoot ----------------
# Tunable defaults (DEFAULT_AIM_RADIUS, DEFAULT_OVERSHOOT_PX,
# DEFAULT_OVERSHOOT_CHANCE, MOVING_TARGET_MIN_DIST,
# MOVING_TARGET_MIDFLIGHT_FRACTION) live in settings.py.

# ---------------- movement abort signal ----------------
# A move+click can take up to ~1s (see estimate_duration_ms). Without this, a
# safety stop firing mid-flight (see ControllerState.trigger_safety_stop) would
# sit unnoticed until the current animation finished and the main loop came
# back around. _move_to_human_exact checks this between steps and bails
# immediately instead. A plain module-level flag rather than threading a
# callback through every click_entity/move_to_human call site.
_movement_abort = threading.Event()


def request_movement_abort():
    _movement_abort.set()


def clear_movement_abort():
    _movement_abort.clear()


# ---------------- Humanization profile (session-level parameter drift) ----------------
@dataclass
class HumanProfile:
    aim_sigma_factor: float
    curve_mag_low: float
    curve_mag_high: float
    delay_skew: float
    rerolled_at: float
    reroll_after_s: float


def _roll_human_profile() -> "HumanProfile":
    return HumanProfile(
        aim_sigma_factor=random.uniform(*AIM_SIGMA_FACTOR_RANGE),
        curve_mag_low=random.uniform(*CURVE_MAG_LOW_RANGE),
        curve_mag_high=random.uniform(*CURVE_MAG_HIGH_RANGE),
        delay_skew=random.uniform(*DELAY_SKEW_RANGE),
        rerolled_at=time.monotonic(),
        reroll_after_s=random.uniform(*PROFILE_REROLL_MINUTES_RANGE) * 60.0,
    )


_human_profile: "HumanProfile | None" = None


def get_human_profile() -> "HumanProfile":
    global _human_profile
    if _human_profile is None or (
        time.monotonic() - _human_profile.rerolled_at >= _human_profile.reroll_after_s
    ):
        _human_profile = _roll_human_profile()
    return _human_profile


def sample_human_delay(min_s: float, max_s: float) -> float:
    # Fat right tail instead of random.uniform's hard ceiling -- a real human occasionally
    # pauses much longer than usual (distraction, re-reading a dialog). No hard ceiling above
    # max_s, but min_s IS a hard floor, never violated -- several real call sites' lower bound
    # is not an aesthetic minimum but a documented fix for a real bug (e.g. banker.py's
    # search_bank delay was widened specifically because a too-short wait let the previous
    # search's stale result get clicked, silently withdrawing the wrong item; see that
    # call site's own comment). Sigma scales with the range's own log-width instead of a fixed
    # relative spread, so a narrow tick-critical range (e.g. 0.6-1.0s) stays tight while a
    # wide, deliberately-erratic range (e.g. 0.2-10.0s) keeps real spread -- a fixed relative
    # spread made narrow ranges dangerously likely to land below their documented safe minimum.
    # Enforced via rejection sampling rather than clamping: a clamp produces a point-mass spike
    # at the floor value (a hard-edge artifact, the same failure mode pick_aim_point's own
    # Gaussian-jitter rewrite was designed to avoid), rejection sampling doesn't -- draws are a
    # genuine continuous distribution conditioned on being >= min_s. Numerically validated
    # (2026-08-14) against every real call site in this codebase: zero violations of min_s in
    # 20,000 draws each, including the narrowest tick-critical ranges; worst-case rejection
    # loop averaged ~1.07 draws, max 4, so the retry cost is negligible.
    # See docs/superpowers/specs/2026-08-14-humanization-overhaul-design.md.
    profile = get_human_profile()
    log_min = math.log(max(min_s, 1e-4))
    log_max = math.log(max(max_s, min_s + 1e-4))
    mu = (log_min + log_max) / 2.0
    sigma = clamp((log_max - log_min) / 4.0 + 0.05 * profile.delay_skew, 0.08, 0.6)
    for _ in range(50):
        draw = random.lognormvariate(mu, sigma)
        if draw >= min_s:
            return draw
    return min_s  # pathological fallback; not expected to trigger (see validation note above)


# ---------------- In-wait fidgeting (added 2026-08-27, see settings' own block) ----------------
_fidget_ser = None
_fidgeting = False


def register_fidget_serial(ser) -> None:
    """Hand the in-wait fidgeter the serial connection. Called once from main(); anything that
    imports controller without owning the Arduino (tests, analysis tools) never registers, and
    human_delay stays a plain sleep for them."""
    global _fidget_ser
    _fidget_ser = ser


def _maybe_fidget_during_wait() -> None:
    """One small out-and-back resting-hand drift from wherever the cursor is. Position-
    invariant on purpose -- the return leg lands exactly on the origin (aim_radius=0) so a
    cursor parked on a future click target is still parked there afterwards. Skipped whenever
    it could interfere: unfocused window, an open right-click menu (moving off one closes it),
    a movement abort, or a target outside the window. Never raises -- a fidget must not be
    able to break the wait it decorates."""
    ser = _fidget_ser
    if ser is None or _movement_abort.is_set():
        return
    if random.random() > IDLE_FIDGET_CHANCE:
        return
    try:
        if not get_state().get("windowFocused"):
            return
        if get_menu().get("open"):
            return
        ox, oy = get_cursor_pos()
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(*IDLE_FIDGET_RADIUS_PX)
        tx = int(round(ox + dist * math.cos(angle)))
        ty = int(round(oy + dist * math.sin(angle)))
        if not is_point_within_window(tx, ty):
            return
        scale = random.uniform(1.2, 2.2)      # a resting-hand shift, not a click-speed traverse
        move_to_human(ser, tx, ty, aim_radius=3, duration_scale=scale)
        if _movement_abort.is_set():
            return
        time.sleep(random.uniform(0.08, 0.35))
        move_to_human(ser, ox, oy, aim_radius=0, duration_scale=scale)
    except Exception:
        pass


def _sleep_with_fidgets(total_s: float) -> None:
    """Sleep `total_s` as a sequence of human-length still segments separated by fidgets --
    the still-segment distribution is the MEASURED human one (median 1.5s, p90 2.3s, capped at
    4s), so even a 30s wait never shows the frozen-cursor signature. Fidget travel time only
    ever EXTENDS the total wait, never shortens it -- min_s floors elsewhere stay honored."""
    global _fidgeting
    deadline = time.perf_counter() + total_s
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        seg = random.lognormvariate(math.log(IDLE_FIDGET_STILL_MEDIAN_S), IDLE_FIDGET_STILL_SIGMA)
        seg = max(0.05, min(remaining, min(IDLE_FIDGET_MAX_STILL_S, seg)))
        time.sleep(seg)
        if deadline - time.perf_counter() > 1.0:   # enough room for an out-and-back
            _fidgeting = True
            try:
                _maybe_fidget_during_wait()
            finally:
                _fidgeting = False


def human_delay(min_s: float, max_s: float) -> None:
    """Drop-in replacement for time.sleep(random.uniform(min_s, max_s)) -- same call shape,
    fat-tailed instead of uniform (see sample_human_delay). Draws of at least
    IDLE_FIDGET_MIN_SLEEP_S are additionally broken up by position-invariant fidgets (see
    _sleep_with_fidgets); the cursor ends the wait where it began it, so callers relying on a
    parked cursor are unaffected. Short draws -- including every down/up hold inside a click --
    stay a plain sleep."""
    delay = sample_human_delay(min_s, max_s)
    if delay < IDLE_FIDGET_MIN_SLEEP_S or _fidget_ser is None or _fidgeting:
        time.sleep(delay)
        return
    _sleep_with_fidgets(delay)


def pick_aim_point(x: int, y: int, radius: int) -> tuple:
    if radius <= 0:
        return x, y
    # 2D Gaussian jitter, not a uniform disc -- user directive 2026-08-14: the previous
    # sqrt(random())-based approach was uniform *density* over the disc, but still had a hard
    # mathematical edge (nothing ever landed even 1px beyond `radius`), which is itself a more
    # artificial, more distinctive signature than real human aim error -- a human's error
    # doesn't have a cliff, it has a tail that thins out smoothly and indefinitely. sigma is
    # tuned so most draws land within roughly the old radius (similar practical click
    # precision to before), with the tail extending smoothly past it instead of stopping dead.
    # Magnitude soft-clamped at 2.5x radius purely as a numerical safety net against a truly
    # pathological outlier missing the target's clickable area entirely -- far enough out that
    # a normal draw essentially never reaches it, not a perceptible boundary the way the old
    # hard cutoff was.
    profile = get_human_profile()
    sigma = radius / profile.aim_sigma_factor
    dx = random.gauss(0, sigma)
    dy = random.gauss(0, sigma)
    mag = math.hypot(dx, dy)
    max_mag = radius * 2.5
    if mag > max_mag:
        scale = max_mag / mag
        dx *= scale
        dy *= scale
    return int(round(x + dx)), int(round(y + dy))


def pick_dismiss_point(x: int, y: int, distance: int = 200) -> tuple:
    """Pick a point roughly `distance` px from (x, y) in a random direction -- for moving the
    cursor clear of something (e.g. a stale context menu) rather than jittering near it like
    pick_aim_point. Tries a few random directions for one that stays inside the RuneLite window;
    falls back to the canvas center (always safely inside it) if none do."""
    for _ in range(4):
        angle = random.uniform(0, 2 * math.pi)
        candidate = (int(round(x + distance * math.cos(angle))), int(round(y + distance * math.sin(angle))))
        if is_point_within_window(*candidate):
            return candidate
    state = get_state()
    return (state.get("canvasScreenX", x) + state.get("canvasWidth", 0) // 2,
            state.get("canvasScreenY", y) + state.get("canvasHeight", 0) // 2)


def maybe_overshoot_point(start, aim, *, overshoot_px=DEFAULT_OVERSHOOT_PX, chance=DEFAULT_OVERSHOOT_CHANCE):
    if overshoot_px <= 0 or chance <= 0:
        return None
    if random.random() > chance:
        return None

    sx, sy = start
    ax, ay = aim
    dx = ax - sx
    dy = ay - sy
    dist = math.hypot(dx, dy)

    if dist < 40:
        return None

    ux = dx / dist
    uy = dy / dist
    mag = overshoot_px * random.uniform(0.6, 1.1)

    ox = int(round(ax + ux * mag))
    oy = int(round(ay + uy * mag))
    return ox, oy


# When the cursor last started a deliberate movement -- the idle-drift trigger measures
# stillness from here (see maybe_idle_mouse_move). Bumped at move_to_human entry, so every
# click, walk, hover and in-wait fidget resets it.
_last_mouse_activity = time.monotonic()


def move_to_human(
    ser: serial.Serial,
    x: int,
    y: int,
    *,
    fps: int = 190,
    aim_radius: int = DEFAULT_AIM_RADIUS,
    overshoot_px: int = DEFAULT_OVERSHOOT_PX,
    overshoot_chance: float = DEFAULT_OVERSHOOT_CHANCE,
    duration_scale: float = 1.0,
    refresh_target=None,
    refresh_legs: int = None,
) -> None:
    """
    refresh_target, if given, is a zero-arg callable returning the target's current
    (x, y) screen position, or None if it's no longer found. For moving targets
    (NPCs), a long flight is split so the final approach re-aims at where the
    target actually is now instead of a stale snapshot from when the move
    started -- otherwise a walking NPC can drift out from under the click.

    duration_scale multiplies the travel time _move_to_human_exact would
    otherwise pick for the distance involved -- 1.0 (the default) is normal
    click-speed movement; pass something higher (e.g. from
    maybe_idle_mouse_move) for a deliberately slower, more idle-looking drift.

    refresh_legs, if given together with refresh_target (and the flight is at least
    MOVING_TARGET_MIN_DIST), splits the flight into that many legs toward interpolated
    waypoints instead of the single mid-flight checkpoint above, re-aiming before each one
    after the first (see _move_to_human_tracking for exactly how the waypoints are computed
    and why) -- added 2026-08-14 for clicking without first waiting on wait_until_player_idle:
    a single checkpoint narrows the staleness window from "whole flight" to "the last stretch
    since that one checkpoint", which is enough for a target that's merely walking, but not
    necessarily enough to track the PLAYER's own continued movement all the way to physical
    arrival. None (default, or <=1) is a no-op -- every existing caller's behavior is completely
    unchanged unless it opts in by passing this explicitly. See
    npc_index_refresh_target/ground_tile_refresh_target for ready-made refresh_target factories
    instead of hand-writing the lambda each time. Implemented and reviewed (including an
    independent second pass) but not yet exercised against a live client as of 2026-08-14.
    """
    global _last_mouse_activity
    _last_mouse_activity = time.monotonic()

    # v2 engine dispatch (settings.HUMAN_MOVE_V2, off by default). This is the ONLY edit the v2
    # rollout makes to the existing movement path -- every click_entity/hover_entity call site
    # inherits it through this one branch, and turning the flag off restores v1 exactly. See
    # human_move.py and docs/superpowers/specs/2026-08-24-human-mouse-movement-v2-design.md.
    # refresh_legs is intentionally ignored by v2: its submovement boundaries are already the
    # re-aim points, so it has no artificial legs to configure.
    if HUMAN_MOVE_V2:
        # v2 plans its own submovement structure, but the AIM-POINT randomization still lives
        # here: without this, every v2 flight landed exactly on the reported pixel, silently
        # dropping the deliberate anti-detection jitter every caller's aim_radius asks for.
        # The offset is drawn ONCE per flight and carried through refresh_target, not redrawn
        # per refresh -- same rationale as _move_to_human_tracking's fixed per-flight jitter
        # (redrawing per re-aim adds wasted wander even for a stationary target).
        aim = pick_aim_point(x, y, aim_radius)
        off_x, off_y = aim[0] - x, aim[1] - y
        v2_refresh = refresh_target
        if refresh_target is not None and (off_x or off_y):
            def v2_refresh():
                updated = refresh_target()
                if updated is None:
                    return None
                return updated[0] + off_x, updated[1] + off_y
        # Retracting queued-but-unplayed motion on a re-aim only means anything if the firmware
        # understands 'X' (protocol v3, see probe_firmware_protocol) -- a pre-v3 board silently
        # ignores unknown lines, so sending it there would just log a no-op while queued motion
        # keeps playing out from under a fresh aim. flush stays None for every device that hasn't
        # been reflashed, which is what keeps this seam bit-for-bit inert until Task 4 lands.
        flush = None
        if FIRMWARE_PROTOCOL >= 3:
            def flush():
                send_line(ser, "X")
        human_move.move(ser, aim[0], aim[1], get_cursor_pos=get_cursor_pos, send_line=send_line,
                        abort=_movement_abort, duration_scale=duration_scale,
                        refresh_target=v2_refresh, flush_queue=flush)
        return

    start = get_cursor_pos()

    if refresh_target is not None and refresh_legs is not None and refresh_legs > 1:
        # Same distance floor as the single-checkpoint branch below (MOVING_TARGET_MIN_DIST) --
        # a short flight finishes too fast for multiple checkpoints to be worth the extra bridge
        # round-trips, so it falls through to the normal path instead.
        if math.hypot(x - start[0], y - start[1]) >= MOVING_TARGET_MIN_DIST:
            _move_to_human_tracking(ser, start, x, y, fps=fps, aim_radius=aim_radius,
                                     duration_scale=duration_scale, refresh_target=refresh_target,
                                     legs=refresh_legs)
            return

    aim = pick_aim_point(x, y, aim_radius)

    over = maybe_overshoot_point(start, aim, overshoot_px=overshoot_px, chance=overshoot_chance)
    if over is not None:
        _move_to_human_exact(ser, over[0], over[1], fps=fps, duration_scale=duration_scale)
        if _movement_abort.is_set():
            return
        human_delay(0.01, 0.05)
        aim = _reaim(aim, refresh_target, aim_radius)
        _move_to_human_exact(ser, aim[0], aim[1], fps=max(90, fps - 25), duration_scale=duration_scale)
        return

    dist = math.hypot(aim[0] - start[0], aim[1] - start[1])
    if refresh_target is not None and dist >= MOVING_TARGET_MIN_DIST:
        mid_x = start[0] + (aim[0] - start[0]) * MOVING_TARGET_MIDFLIGHT_FRACTION
        mid_y = start[1] + (aim[1] - start[1]) * MOVING_TARGET_MIDFLIGHT_FRACTION
        _move_to_human_exact(ser, int(round(mid_x)), int(round(mid_y)), fps=fps, duration_scale=duration_scale)
        if _movement_abort.is_set():
            return
        aim = _reaim(aim, refresh_target, aim_radius)
        _move_to_human_exact(ser, aim[0], aim[1], fps=max(110, fps - 15), duration_scale=duration_scale)
        return

    _move_to_human_exact(ser, aim[0], aim[1], fps=fps, duration_scale=duration_scale)


def _reaim(fallback_aim: tuple, refresh_target, aim_radius: int) -> tuple:
    if refresh_target is None:
        return fallback_aim
    updated = refresh_target()
    if updated is None:
        return fallback_aim
    return pick_aim_point(updated[0], updated[1], aim_radius)


def _move_to_human_tracking(ser: serial.Serial, start: tuple, x: int, y: int, *, fps: int,
                             aim_radius: int, duration_scale: float, refresh_target, legs: int) -> None:
    """Generalizes move_to_human's existing single-midflight-checkpoint re-aim (see
    MOVING_TARGET_MIDFLIGHT_FRACTION) into `legs` sequential hops toward interpolated waypoints,
    re-aiming via refresh_target before each hop after the first. Added 2026-08-14 specifically
    for clicking without waiting on wait_until_player_idle first: a single checkpoint is enough
    for a merely-walking target, but tracking the PLAYER's own continued movement all the way to
    physical arrival benefits from more frequent correction. Each leg re-queries the target's
    actual current screen position (via whatever refresh_target resolves -- an NPC index, a
    world tile, etc.), which is itself a function of wherever the player/camera currently are,
    so this corrects for our own movement exactly the same way it corrects for the target's.

    Each of the first `legs - 1` hops targets a waypoint interpolated between the cursor's
    current position and the freshly-resolved aim (cursor + (aim - cursor) / legs_remaining) --
    NOT the full distance -- so a stale snapshot only ever costs one leg's worth of travel
    before the next re-aim corrects course; only the final leg lands exactly on the target.
    Flying the full distance on every leg and only correcting afterward (an earlier version of
    this function did exactly that, caught in review before ever running live) defeats the
    entire point: it means "arrive, then twitch-correct in place," not "track en route."

    The aim-radius jitter offset is drawn ONCE, before the loop, and re-applied to every
    refreshed (x, y) rather than redrawn per leg (pick_aim_point draws uniformly over the disc
    on every call) -- redrawing would add real, wasted motion even for a perfectly stationary
    target (two independent draws inside a radius-3 disc average ~2.7px apart, and
    _move_to_human_exact's duration estimate floors at 200ms even for a 2-3px correction),
    directly working against clicking sooner, this function's whole purpose.

    Once refresh_target() returns None (target no longer resolvable), stops calling it again for
    the remaining legs -- rather than spending a bridge round-trip on every one likely to fail
    the same way -- and finishes the flight toward the last position that was actually resolved.

    Deliberately NOT a change to _move_to_human_exact's own per-step loop (which runs a single
    pre-computed Bezier curve toward a fixed endpoint, no bridge polling inside it) -- that would
    mean a bridge round-trip on every one of its ~18+ per-click steps. Chaining whole
    _move_to_human_exact hops toward interpolated waypoints instead reuses the exact same proven
    building block move_to_human already calls, just more times in sequence, at whatever
    granularity `legs` asks for.

    Doesn't combine with maybe_overshoot_point's humanized overshoot-and-correct -- that's a
    separate, orthogonal randomization the other two branches use; move_to_human's caller falls
    through to this function before overshoot is ever considered. Not yet exercised against a
    live client as of 2026-08-14 -- reviewed via static analysis and an independent second pass,
    not yet confirmed live."""
    target_x, target_y = x, y
    jitter_x, jitter_y = pick_aim_point(x, y, aim_radius)
    jitter_dx, jitter_dy = jitter_x - x, jitter_y - y

    cursor_x, cursor_y = start
    target_lost = False
    for leg in range(legs):
        if _movement_abort.is_set():
            return

        if leg > 0:
            human_delay(0.01, 0.05)
            cursor_x, cursor_y = get_cursor_pos()
            if not target_lost and refresh_target is not None:
                updated = refresh_target()
                if updated is None:
                    target_lost = True
                else:
                    target_x, target_y = updated

        aim_x, aim_y = target_x + jitter_dx, target_y + jitter_dy
        legs_remaining = legs - leg
        if legs_remaining <= 1:
            waypoint = (int(round(aim_x)), int(round(aim_y)))
        else:
            waypoint = (
                int(round(cursor_x + (aim_x - cursor_x) / legs_remaining)),
                int(round(cursor_y + (aim_y - cursor_y) / legs_remaining)),
            )

        # Matches the single-checkpoint MOVING_TARGET branch's own final-approach fps (this
        # function generalizes that branch, not the separate overshoot branch's fps value).
        final_leg = leg == legs - 1
        leg_fps = max(110, fps - 15) if final_leg else fps
        # Only the final leg is a discrete "land on the target" movement; the rest are mid-
        # gesture corrections, so they don't pay the full per-hop floor. Without this the floor
        # alone made a `legs`-way flight cost legs * MOVE_MIN_DURATION_MS regardless of distance
        # -- see TRACKING_LEG_MIN_DURATION_MS for the measurements.
        leg_floor = MOVE_MIN_DURATION_MS if final_leg else TRACKING_LEG_MIN_DURATION_MS
        _move_to_human_exact(ser, waypoint[0], waypoint[1], fps=leg_fps, duration_scale=duration_scale,
                              min_duration_ms=leg_floor)


def _move_to_human_exact(ser: serial.Serial, x: int, y: int, *, fps: int = 140, duration_scale: float = 1.0,
                          min_duration_ms: float = MOVE_MIN_DURATION_MS) -> None:
    sx, sy = get_cursor_pos()
    ex, ey = x, y
    dist = math.hypot(ex - sx, ey - sy)
    if dist < 2:
        return

    bez = build_bezier_path((sx, sy), (ex, ey))
    duration_ms = estimate_duration_ms(dist, min_ms=min_duration_ms) * duration_scale
    steps = max(18, int((duration_ms / 1000.0) * fps))
    t0 = time.perf_counter()

    pause_mid = micro_pause_chance(dist)
    pause_step = random.randint(int(steps * 0.35), int(steps * 0.7)) if pause_mid else -1

    for i in range(1, steps + 1):
        if _movement_abort.is_set():
            return

        t = i / steps
        te = ease_in_out(t)

        tx, ty = bez.point(te)

        jitter = clamp(dist / 900.0, 0.3, 1.2)
        if t > 0.75:
            jitter *= 0.55  # settles down on final approach, matching recorded late-third tremor
        tx += random.uniform(-jitter, jitter)
        ty += random.uniform(-jitter, jitter)

        cx, cy = get_cursor_pos()
        dx = int(round(tx)) - cx
        dy = int(round(ty)) - cy

        if t < 0.35:
            max_step = 26
        elif t < 0.80:
            max_step = 18
        else:
            max_step = 10

        dx = max(-80, min(80, dx))
        dy = max(-80, min(80, dy))

        if dx or dy:
            move_rel_arduino(ser, dx, dy, max_step=max_step)

        if i == pause_step:
            human_delay(0.03, 0.10)

        target_time = t0 + (t * (duration_ms / 1000.0))
        sleep_s = target_time - time.perf_counter()
        if sleep_s > 0:
            time.sleep(sleep_s)

    for _ in range(40):
        if _movement_abort.is_set():
            return

        cx, cy = get_cursor_pos()
        dx = ex - cx
        dy = ey - cy
        if abs(dx) <= 1 and abs(dy) <= 1:
            break
        dx = max(-4, min(4, dx))
        dy = max(-4, min(4, dy))
        move_rel_arduino(ser, dx, dy, max_step=4)
        time.sleep(0.004)


def sweep_and_click(ser: serial.Serial, x: int, y: int, *, click_rate_hz=(5.0, 7.0),
                     fps: int = 140, duration_scale: float = 1.0, min_clicks: int = 0) -> None:
    """Glide toward (x, y) the same eased/bezier/jittered way _move_to_human_exact does, but
    fire left-clicks throughout the glide at a randomized click_rate_hz cadence instead of
    waiting until the destination is reached before clicking at all.

    Added 2026-08-13 (user directive, mastering_mixology.py's grimy-herb cleaning): a human
    sweeping their mouse across a dense run of targets (e.g. a row of inventory items) spam-
    clicks WHILE moving, they don't move-then-click-then-move-again for each one. move_to_human
    and click_human are each already built on the same low-level primitives this needs
    (move_rel_arduino for movement, mouse_left_down/up for a click) -- this just interleaves
    both within one loop instead of calling them as two separate, fully-sequential blocking
    calls. Not a threading concern: movement and click commands are just separate serial
    messages: nothing stops one Python loop from sending both.

    CORRECTED 2026-08-13 (user report: "we arent clicking fast enough"): duration used to be
    purely estimate_duration_ms(dist), which is realistic mouse-TRAVEL time for the distance
    involved -- but a 4-slot inventory row only spans ~260px, which estimate_duration_ms puts at
    ~250-300ms total. Even at the fast end of click_rate_hz (7/sec, ~140ms between clicks), that
    barely fits 2 clicks before the glide's already over -- the click rate was never the
    bottleneck, the glide was just ending before it could fire more. min_clicks, when given,
    floors the duration at whatever it takes to fit that many clicks at the SLOW end of
    click_rate_hz (a real spam-click burst, not truncated by a short travel distance)."""
    debug_log.log("action", f"sweep_and_click toward ({x},{y}) from {get_cursor_pos()}")

    sx, sy = get_cursor_pos()
    dist = math.hypot(x - sx, y - sy)
    bez = build_bezier_path((sx, sy), (x, y)) if dist >= 2 else None

    min_click_burst_ms = (min_clicks / click_rate_hz[0]) * 1000.0 if min_clicks > 0 else 0.0
    duration_ms = max(estimate_duration_ms(dist), 250, min_click_burst_ms) * duration_scale
    steps = max(18, int((duration_ms / 1000.0) * fps))
    t0 = time.perf_counter()
    next_click_at = t0 + random.uniform(1.0 / click_rate_hz[1], 1.0 / click_rate_hz[0])

    for i in range(1, steps + 1):
        if _movement_abort.is_set():
            return

        t = i / steps
        te = ease_in_out(t)

        if bez is not None:
            tx, ty = bez.point(te)
            jitter = clamp(dist / 900.0, 0.3, 1.2)
            if t > 0.75:
                jitter *= 0.55
            tx += random.uniform(-jitter, jitter)
            ty += random.uniform(-jitter, jitter)

            cx, cy = get_cursor_pos()
            dx = max(-80, min(80, int(round(tx)) - cx))
            dy = max(-80, min(80, int(round(ty)) - cy))
            if dx or dy:
                move_rel_arduino(ser, dx, dy, max_step=18)

        now = time.perf_counter()
        if now >= next_click_at:
            mouse_left_down(ser)
            human_delay(0.02, 0.05)
            mouse_left_up(ser)
            next_click_at = time.perf_counter() + random.uniform(1.0 / click_rate_hz[1], 1.0 / click_rate_hz[0])

        target_time = t0 + (t * (duration_ms / 1000.0))
        sleep_s = target_time - time.perf_counter()
        if sleep_s > 0:
            time.sleep(sleep_s)


# ---------------- clicking bridge entities ----------------
def is_point_within_window(x: int, y: int) -> bool:
    """False if (x, y) falls outside the actual RuneLite window. A provider's
    "clickable" flag only checks against the 3D viewport as RuneLite itself
    understands it; a target computed just before/after a camera rotation (or
    one that's only barely onScreen) can still resolve to a point beyond the
    client entirely -- e.g. onto the desktop or another window -- and that
    must never be blind-clicked into."""
    state = get_state()
    wx, wy = state.get("windowX"), state.get("windowY")
    ww, wh = state.get("windowWidth"), state.get("windowHeight")
    if wx is None or wy is None or ww is None or wh is None:
        return True  # can't verify -- fail open rather than block every click
    return wx <= x < wx + ww and wy <= y < wy + wh


def is_point_blocked_by_ui(x: int, y: int):
    """Returns the UI panel rect covering (x, y), or None if it's clear. See
    GameStateProvider's "uiBlockers" (e.g. the resizable layouts' inventory
    panel) -- entities' screenX/Y only account for viewport bounds, not
    whatever's drawn on top of it."""
    for rect in get_state().get("uiBlockers", []):
        rx, ry = rect.get("x", 0), rect.get("y", 0)
        rw, rh = rect.get("width", 0), rect.get("height", 0)
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return rect
    return None


def nudge_camera_yaw(ser: serial.Serial, key: str) -> None:
    """Hold the given arrow key just long enough for a small camera turn,
    then release -- shared by both camera-nudge use cases below."""
    key_down(ser, key)
    human_delay(0.15, 0.35)
    key_up(ser, key)
    human_delay(0.1, 0.2)


def open_spellbook_if_needed(ser: serial.Serial) -> None:
    """Idempotent -- taps T (this game's Magic-tab keybind) only if Telekinetic Grab isn't
    already visible, so a hunt_phase call that finds the tab already open doesn't needlessly
    re-tap it every single cast."""
    if get_spellbook().get("visible"):
        return
    key_tap(ser, "T")
    human_delay(0.3, 0.5)


def cast_telekinetic_grab(ser: serial.Serial, target: dict) -> bool:
    """Click Telekinetic Grab, then click the target -- same two-click 'use X on Y' shape as
    any item-on-entity interaction elsewhere in this codebase. Returns False if the spell
    isn't visible (spellbook not open/wrong spellbook active) or either click misses; True
    only means both clicks landed, not that the cast actually resolved -- the caller (hunt_
    phase) still has to watch chat for the real confirmation/rejection, same reasoning
    golem_crafting.py's craft_phase already documents for why a click landing isn't the same
    as the game having accepted it."""
    open_spellbook_if_needed(ser)
    spell = get_spellbook()
    if not spell.get("visible"):
        return False
    # CORRECTED 2026-08-09 (user report: safety-stopped over "out of Earth runes" instead of
    # law/air): simulate_mistakes' humanized aim-beside-target miss (~20-45px, see
    # MISCLICK_OFFSET_PX) is fine for a world object -- a miss there just lands on empty ground,
    # harmless. Spell icons in the spellbook are packed close together, so that same miss can
    # land squarely on an ADJACENT spell instead, silently casting something else entirely with
    # different rune costs. Disabled here, same reasoning click_menu_action's own right-click
    # already disables it for menu-sensitive clicks.
    if not click_entity(ser, spell, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False):
        return False
    human_delay(0.15, 0.3)
    # ADDED 2026-08-09 (user report: "the data is too slow because the goats move around a lot,
    # we click the ground 70% of the time"): target's screenX/screenY are a snapshot from
    # whenever the caller fetched the NPC list, already stale by the time this click actually
    # executes for a fast-moving goat. refresh_target re-queries this SPECIFIC NPC's live
    # position by index mid-flight (same mechanism slayer.py's engage_target already uses for
    # moving combat targets) rather than trusting the one snapshot for the whole click.
    npc_index = target.get("index")
    refresh = npc_index_refresh_target(npc_index) if npc_index is not None else None
    # CORRECTED 2026-08-09 (autonomous session, root-caused from hunt_test15.log: 60 straight
    # Grab-cast attempts, zero pit fills, with "drifted off the hunt standing tile" recurring
    # throughout -- the signature of casts resolving as ground-walks instead of landing on the
    # goat). click_entity's default offset=(-7, 7) is deliberately sized to land somewhere on a
    # "largeish" NPC sprite (see slayer.py's engage_target comment) -- fine for the Gryphons/
    # combat targets it was tuned against, but a Wyrmscraig goat's sprite is much smaller and its
    # screenX/screenY is the same ground-tile anchor point every provider uses (confirmed in
    # NPCProvider/RuneLiteUtil.screenCoords, no model-height adjustment), so that offset was very
    # plausibly landing off the goat's clickable area onto bare ground behind/beside it -- with
    # the spell already selected, a ground click just cancels the selection and walks there,
    # which matches the observed drift exactly. offset=(0, 0) here, same fix already applied to
    # ground items for the same "default offset assumes a bigger sprite" reason.
    return click_entity(ser, target, offset=(0, 0), refresh_target=refresh)


def rotate_camera_away_from_ui(ser: serial.Serial, blocker: dict) -> None:
    """A human just spins the camera when something's obscured -- nudge it
    away from whichever side the blocking panel sits on, then let the caller
    re-resolve the target's (now-shifted) screen position on its next
    attempt. The panel is screen-anchored but a target's projected position
    depends on camera yaw, so repeated nudges in the same direction are
    guaranteed to eventually clear it even if this first guess is backwards."""
    state = get_state()
    canvas_center_x = state.get("canvasScreenX", 0) + state.get("canvasWidth", 0) / 2
    blocker_center_x = blocker.get("x", 0) + blocker.get("width", 0) / 2
    key = "LEFT" if blocker_center_x >= canvas_center_x else "RIGHT"

    debug_log.log("!", f"Aim point is behind a UI panel -- rotating camera ({key}) to work around it")
    nudge_camera_yaw(ser, key)


def rotate_camera_to_reveal(ser: serial.Serial, x) -> None:
    """A target can be "visible" (the engine projected some point for it)
    without being "clickable" (that point falls outside the viewport) -- e.g.
    right after climbing through a tunnel, facing a new direction. Nudge the
    camera back toward it: same self-correcting logic as
    rotate_camera_away_from_ui, just steering toward a point instead of away
    from a rect."""
    state = get_state()
    canvas_center_x = state.get("canvasScreenX", 0) + state.get("canvasWidth", 0) / 2
    key = "RIGHT" if x < canvas_center_x else "LEFT"

    debug_log.log("!", f"Target is off-screen -- rotating camera ({key}) to bring it into view")
    nudge_camera_yaw(ser, key)


# NOTE: every provider (NPCProvider, GroundItemProvider, InventoryProvider,
# ConsumablesProvider, PrayerProvider, ObjectProvider) already returns
# *absolute* screen coordinates (canvas offset baked in server-side) -- do
# not add the canvas offset again here.
class _ClickWithheld:
    """Falsy click_entity result for a click withheld by hover verification, so a caller can
    tell it apart from every other False (aim off-screen, UI blocker, abort, target lost)
    without breaking the many existing `if click_entity(...)` truthiness checks. Check it via
    `getattr(result, "withheld", False)` -- a plain bool has no attributes, so the same
    expression is safe on every return value. Semantically a DETECTED hijack: the aim landed,
    but whatever owns the pixel would take the click (see golem_crafting's escalation tiers,
    the caller this exists for)."""
    withheld = True

    def __bool__(self):
        return False

    def __repr__(self):
        return "CLICK_WITHHELD"


CLICK_WITHHELD = _ClickWithheld()


def click_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None,
                  check_ui_blockers=True, simulate_mistakes=True, button: str = "left",
                  refresh_legs: int = None, expected_action=None,
                  expected_target=None):
    """expected_action/expected_target (added 2026-08-27, both optional and opt-in): verify
    BEFORE the click that the hover's default action matches -- see hover_matches; each may be
    one string or a sequence of acceptable strings. On a persistent mismatch after one
    corrective re-aim, the click is withheld and the falsy CLICK_WITHHELD sentinel returned
    (all other failures still return plain False), converting the whole hijack/drift failure
    class (a player camped on the pixel, the target slipped out from under the cursor) from
    /last-action post-hoc detection into prevention. Left-button clicks only -- a right-click
    opens a menu that is its own confirmation."""
    x, y = entity.get("screenX"), entity.get("screenY")
    if x is None or y is None:
        return False

    aim_x, aim_y = int(x) + offset[0], int(y) + offset[1]

    missed = False
    if simulate_mistakes and random.random() < MISTAKE_MISCLICK_CHANCE:
        # Simulate a near-miss -- aimed at the ground/tile beside the target instead of on
        # it (e.g. clicked next to the crab instead of the crab itself), rather than a wild
        # click somewhere unrelated in the viewport. Applies everywhere (including mid-combat)
        # rather than checking combat state first, which would mean an extra bridge round-trip
        # on every single click_entity call -- not worth it for a miss this mild; the next tick
        # just re-clicks normally.
        angle = random.uniform(0, 2 * math.pi)
        miss_dist = random.uniform(*MISCLICK_OFFSET_PX)
        aim_x += int(round(miss_dist * math.cos(angle)))
        aim_y += int(round(miss_dist * math.sin(angle)))
        missed = True
        debug_log.log("!", f"Misclick -- aimed beside {entity.get('name')} instead of on it")

    if not is_point_within_window(aim_x, aim_y):
        debug_log.log("!", f"Skipping click on {entity.get('name')} -- aim point is outside the RuneLite window")
        return False

    if check_ui_blockers:
        blocker = is_point_blocked_by_ui(aim_x, aim_y)
        if blocker is not None:
            rotate_camera_away_from_ui(ser, blocker)
            return False

    debug_log.log("action", f"click_entity target={entity.get('name')}(id={entity.get('id')}) at ({aim_x},{aim_y})")
    move_to_human(
        ser,
        aim_x,
        aim_y,
        aim_radius=ENTITY_AIM_RADIUS,
        refresh_target=refresh_target,
        refresh_legs=refresh_legs,
    )
    if _movement_abort.is_set():
        return False

    # A deliberate simulated miss skips BOTH the settle and the hover verification -- each
    # would "correct" the miss the moment it was made, defeating the humanization. The
    # corrective reclick below is the one that gets the full treatment.
    if not missed:
        _settle_on_target_before_click(ser, refresh_target)
        if _movement_abort.is_set():
            return False
        if (expected_action or expected_target) and button != "right" \
                and not _verify_hover_before_click(ser, expected_action, expected_target,
                                                   refresh_target):
            debug_log.log("!", f"withholding click on {entity.get('name')} -- hover never "
                               f"matched action={expected_action!r} target={expected_target!r}")
            return CLICK_WITHHELD

    if button == "right":
        right_click_human(ser)
    else:
        click_human(ser)

    if missed:
        _reclick_after_misclick(ser, entity, offset, refresh_target, check_ui_blockers, button,
                                refresh_legs, expected_action, expected_target)

    return True


def _settle_on_target_before_click(ser: serial.Serial, refresh_target) -> None:
    """One last look at the target between the flight ending and the mouse-down (added
    2026-08-27). move()'s final re-aim happens at its last submovement boundary, so the settle
    tail plus click delay (~100-300ms) was blind -- while the player walks, easily tens of px
    of drift, i.e. exactly 'clicks land behind walkers'. Compares the cursor against the
    target's LIVE position and issues one short corrective move only past PRECLICK_MAX_DRIFT_PX
    (generous enough to absorb aim jitter plus the caller offset that refreshes deliberately
    drop -- see move_to_human's v2 dispatch -- so a stationary target never pays extra motion).
    A None refresh means the target died/despawned: click the last-known point anyway, same as
    move()'s own target_lost path."""
    if refresh_target is None:
        return
    updated = refresh_target()
    if updated is None:
        return
    tx, ty = int(updated[0]), int(updated[1])
    cur = get_cursor_pos()
    if math.hypot(tx - cur[0], ty - cur[1]) <= PRECLICK_MAX_DRIFT_PX:
        return
    if not is_point_within_window(tx, ty):
        return
    debug_log.log("action", f"pre-click settle -- target drifted to ({tx},{ty}), cursor at {cur}")
    move_to_human(ser, tx, ty, aim_radius=2, refresh_target=refresh_target)


def _verify_hover_before_click(ser: serial.Serial, expected_action, expected_target,
                                refresh_target) -> bool:
    """hover_matches with one corrective re-aim in between: the first mismatch may just mean
    the target drifted off the cursor, so re-resolve and re-settle once before giving up."""
    if hover_matches(expected_action, expected_target):
        return True
    if refresh_target is None:
        return False
    updated = refresh_target()
    if updated is None:
        return False
    tx, ty = int(updated[0]), int(updated[1])
    if not is_point_within_window(tx, ty):
        return False
    debug_log.log("action", f"hover mismatch -- re-aiming at ({tx},{ty}) before deciding")
    move_to_human(ser, tx, ty, aim_radius=2, refresh_target=refresh_target)
    if _movement_abort.is_set():
        return False
    return hover_matches(expected_action, expected_target)


def _reclick_after_misclick(ser: serial.Serial, entity: dict, offset, refresh_target,
                             check_ui_blockers: bool, button: str, refresh_legs: int = None,
                             expected_action: str = None, expected_target: str = None) -> None:
    """Corrective follow-up after click_entity's own simulated near-miss: a human who clicks
    empty ground beside a target notices nothing happened and clicks again almost immediately,
    they don't just give up and stand there. Without this, every caller that cares about the
    click's actual effect has to build its own detect-and-retry logic around the fact that a
    miss executes cleanly but produces nothing -- golem_crafting.py alone has three separate
    workarounds for exactly this (MINE_RECLICK_TIMEOUT_S's inventory-change gate, the golem
    fast-path's "misclick we don't correct for" fix, the fur-stock trust bug). Centralizing the
    correction here means those keep working as safety nets but rarely actually need to trigger.

    Never itself rolls another simulated miss -- a human's corrective second click is the one
    they're actually paying attention to. Re-resolves the target's live position via
    refresh_target when given, same staleness handling as any other re-aim (see
    walk_to_exact_tile's get_ground_walk_screen_pos for why a snapshot from before this function's
    own reaction-delay sleep can't be trusted as-is)."""
    human_delay(*MISCLICK_RECLICK_DELAY_S)

    x, y = entity.get("screenX"), entity.get("screenY")
    if refresh_target is not None:
        updated = refresh_target()
        if updated is not None:
            x, y = updated
    if x is None or y is None:
        return

    aim_x, aim_y = int(x) + offset[0], int(y) + offset[1]
    if not is_point_within_window(aim_x, aim_y):
        return
    if check_ui_blockers and is_point_blocked_by_ui(aim_x, aim_y) is not None:
        return

    debug_log.log("action", f"Reclick -- correcting misclick on {entity.get('name')} at ({aim_x},{aim_y})")
    move_to_human(ser, aim_x, aim_y, aim_radius=ENTITY_AIM_RADIUS, refresh_target=refresh_target,
                  refresh_legs=refresh_legs)
    if _movement_abort.is_set():
        return
    # The corrective click is the one the human is actually paying attention to -- it gets the
    # same settle + verification the primary click would have.
    _settle_on_target_before_click(ser, refresh_target)
    if _movement_abort.is_set():
        return
    if (expected_action or expected_target) and button != "right" \
            and not _verify_hover_before_click(ser, expected_action, expected_target,
                                               refresh_target):
        debug_log.log("!", f"withholding reclick on {entity.get('name')} -- hover never matched")
        return
    if button == "right":
        right_click_human(ser)
    else:
        click_human(ser)


def hover_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None,
                  check_ui_blockers=True, refresh_legs: int = None) -> bool:
    """Move the cursor onto `entity` (same aim jitter as click_entity) without
    clicking -- for anticipatory hovering over something a routine already
    knows it'll need next, while it's otherwise idle-waiting on the current
    action to resolve (e.g. drifting toward the tunnel while a crab is still
    being fought), OR for resting the mouse on a time-critical click target
    ahead of time so only a click (not a move-then-click) has to fire the
    instant it's actually needed (see mastering_mixology.py's speedup
    functions). Unlike click_entity this is just a courtesy move, not a
    real interaction attempt: it silently no-ops (no camera nudge, no log)
    if the point isn't safely reachable right now, rather than trying to
    work around it.

    check_ui_blockers matches click_entity's own flag (added 2026-08-13) --
    some UIs (e.g. Mastering Mixology's own panels) legitimately overlap
    world objects this needs to hover, the same reason click_entity call
    sites there already pass check_ui_blockers=False.
    """
    x, y = entity.get("screenX"), entity.get("screenY")
    if x is None or y is None:
        return False

    aim_x, aim_y = int(x) + offset[0], int(y) + offset[1]
    if not is_point_within_window(aim_x, aim_y):
        return False
    if check_ui_blockers and is_point_blocked_by_ui(aim_x, aim_y) is not None:
        return False

    move_to_human(ser, aim_x, aim_y, aim_radius=ENTITY_AIM_RADIUS, refresh_target=refresh_target,
                  refresh_legs=refresh_legs)
    return not _movement_abort.is_set()


MENU_POLL_INTERVAL_S = 0.05
MENU_POLL_TIMEOUT_S = 0.6


def click_menu_action(ser: serial.Serial, entity: dict, option, target_hint: str = None,
                       offset=(-7, 7), check_ui_blockers=True, refresh_target=None,
                       refresh_legs: int = None) -> bool:
    # option: str | Sequence[str] -- see the docstring's own note on the sequence form.
    """Right-click `entity`, read back the resulting context menu via GET /menu, and left-click
    whichever entry's option matches `option` (case-insensitive substring, e.g. "take") -- picks
    a specific menu action by name instead of trusting whatever left-click's default action
    resolves to.

    Needed whenever something else can share the same click point and isn't guaranteed to lose
    the default-action race -- e.g. a ground item on a tile an NPC is currently standing on: a
    plain click_entity() there can resolve to the NPC's default action (Attack) instead of the
    item's Take, with no error or misclick logged, just a pickup that silently never happens. See
    routines/slayer.py's maybe_loot for the original case this was built for. The right-click +
    menu-read + read-back-and-select primitives this composes were CONFIRMED-working live
    2026-08-06 (docs/superpowers/plans/2026-08-06-right-click-menu-state.md Task 3).

    `target_hint`, if given, is matched (case-insensitive substring) against the entry's target
    text too -- narrows to the right entry when a tile has more than one "Take"-able thing on it.

    Returns False -- backing out with ESC rather than leaving the menu open over an unintended
    entry -- if the menu never opened or nothing matched `option`/`target_hint`, so the caller can
    retry next tick or fall back instead of assuming a click landed somewhere sensible.

    `option` may also be a SEQUENCE of option names (2026-08-20, for
    routines/chinchompa_hunting.py), tried in priority order against the one menu this already
    reads back -- no extra right-click. That routine needs it because the correct retrieval
    action differs per box-trap state ("Check" a full one, "Dismantle" an empty/collapsed one)
    and, for the collapsed case, is not something this codebase can confirm without a live
    client: giving the exact action first and plausible siblings after it means a wrong guess
    degrades into "picked the right entry anyway" instead of a routine that no-ops forever. A
    plain str behaves exactly as before.

    A `target_hint` that matches nothing is likewise retried WITHOUT the hint before giving up
    (same menu, no extra click) -- an over-narrow hint should cost precision, not the whole
    interaction.

    `refresh_target`/`refresh_legs` (2026-08-27) are handed straight to the underlying
    click_entity, so the RIGHT-click can re-aim mid-flight exactly like an ordinary left click
    already could. This used to be the one interaction in the codebase with no correction
    available once the flight had started, which made it structurally fragile for anything small
    or anything clicked while the player was moving -- CLAUDE.md carried a standing warning never
    to hand it a position resolved before a walk.

    CONFIRMED live 2026-08-27, slayer looting: resolve-to-right-click took ~1.0s, and over that
    window the player's movement and the camera's trailing animation drifted a ground item's tiny
    icon out from under the aim point. Signature is a menu of pure ground options -- "No 'take'
    entry in menu for Granite maul (['Cancel', 'Mark Safespot', 'Walk here'])" -- three times in
    one session, each costing a ~7s retry. Note the window had just doubled: human-timed v2
    movement roughly doubled flight duration versus the engine before it, so this got worse
    before it got fixed.

    Purely additive -- omitting both reproduces the previous behaviour exactly.
    """
    if not click_entity(ser, entity, offset=offset, check_ui_blockers=check_ui_blockers,
                         simulate_mistakes=False, button="right",
                         refresh_target=refresh_target, refresh_legs=refresh_legs):
        return False

    deadline = time.time() + MENU_POLL_TIMEOUT_S
    menu = get_menu()
    while not menu.get("open") and time.time() < deadline:
        time.sleep(MENU_POLL_INTERVAL_S)
        menu = get_menu()

    if not menu.get("open"):
        debug_log.log("!", f"Menu never opened after right-clicking {entity.get('name')}")
        return False

    # Exact match on `option` preferred over substring -- CONFIRMED live 2026-08-08 (MCP debug
    # session): a plain substring search for "Mine" silently matched "Examine" instead (every
    # object's menu has an Examine entry, and "examine" contains "mine"), resolving to the wrong
    # action with no error. Falls back to substring only if no exact match exists, which is still
    # needed for e.g. "take" matching "Take-5"/"Take-10"/"Take-X" when there's no bare "Take".
    options = [option] if isinstance(option, str) else list(option)
    options_lower = [o.lower() for o in options]
    target_lower = target_hint.lower() if target_hint else None

    entries = menu.get("entries", [])

    def _find(use_hint: bool, exact: bool):
        """First entry matching any of `options_lower`, scanning options in priority order
        (option 0 exhausts the whole menu before option 1 is considered) so a caller's stated
        preference wins over menu ordering."""
        for opt in options_lower:
            for entry in entries:
                entry_option = (entry.get("option") or "").lower()
                if exact:
                    if entry_option != opt:
                        continue
                elif opt not in entry_option:
                    continue
                if use_hint and target_lower is not None \
                        and target_lower not in (entry.get("target") or "").lower():
                    continue
                return entry
        return None

    match = _find(use_hint=True, exact=True) or _find(use_hint=True, exact=False)
    if match is None and target_lower is not None:
        match = _find(use_hint=False, exact=True) or _find(use_hint=False, exact=False)
        if match is not None:
            debug_log.log("!", f"target_hint '{target_hint}' matched nothing for "
                                f"{entity.get('name')} -- falling back to '{match.get('option')} "
                                f"{match.get('target')}' on option name alone")

    if match is None:
        debug_log.log("!", f"No '{'/'.join(options)}' entry in menu for {entity.get('name')} "
                            f"({[e.get('option') for e in menu.get('entries', [])]}) -- closing it")
        # Move well clear of the menu rather than relying on ESC alone -- CONFIRMED live
        # 2026-08-06 (user observation, watching the client directly): a menu that isn't
        # definitively gone keeps rendering on top of the item's screen position, blocking every
        # subsequent click there (right or left) until it's actually closed. Moving off it is
        # the same mechanism a human uses to dismiss a context menu without picking an entry.
        # ESC still goes out too, for belt-and-suspenders -- moving away costs nothing extra to
        # pair with it.
        cx, cy = get_cursor_pos()
        move_to_human(ser, *pick_dismiss_point(cx, cy), aim_radius=10)
        key_tap(ser, "ESC")
        return False

    debug_log.log("action", f"menu-select {match.get('option')} {match.get('target')}")
    move_to_human(ser, match["screenX"], match["screenY"], aim_radius=2)
    click_human(ser)
    return True


def consume_first(ser: serial.Serial, category: str) -> bool:
    """Click the first inventory item ConsumablesProvider tags as `category` (e.g. "food",
    "prayer"). Only resolves item screen coords while the Inventory tab is open, so switch
    to it first -- same reasoning as open_prayer_tab for prayer icons. Checks for a matching
    item BEFORE switching tabs, so running out of food/potions doesn't steal the tab away
    from something else that needs it (e.g. offensive prayer flicking on the Prayer tab)
    every single tick for nothing."""
    if not get_consumables().get(category, []):
        return False
    open_inventory_tab(ser)
    items = get_consumables().get(category, [])
    if not items:
        return False
    # check_ui_blockers=False: an inventory item's own screen position is naturally inside
    # the floating inventory panel that uiBlockers flags -- same fix as every other
    # inventory-item click in this codebase (see shipwreck_salvaging.py's drop_salvage).
    return click_entity(ser, items[0], check_ui_blockers=False)


def _draw_idle_still_segment() -> float:
    """Next allowed still-period length, from the SAME measured human distribution the in-wait
    fidgeter uses (median 1.5s, p90 2.3s, capped at the human's observed 4.5s maximum)."""
    seg = random.lognormvariate(math.log(IDLE_FIDGET_STILL_MEDIAN_S), IDLE_FIDGET_STILL_SIGMA)
    return min(IDLE_FIDGET_MAX_STILL_S, seg)


def maybe_idle_mouse_move(ctx, state: "ControllerState") -> None:
    """Drift the cursor a short distance from wherever it already is, slowly, with no click --
    a resting hand shifting slightly, not a purposeful move.

    RETUNED 2026-08-27 after the phase-a-verify live measurement: this used to fire on a
    15-20s wall-clock timer regardless of what the mouse was doing, which both let the cursor
    freeze for multiples of the human's entire stillness range AND (had the timer simply been
    tightened) would have inserted slow drifts ahead of REAL actions, taxing throughput. It now
    triggers on time since the last deliberate cursor movement (`_last_mouse_activity`, bumped
    by move_to_human -- so clicks, walks and the in-wait fidgets all reset it), with the budget
    drawn from the measured human still-segment distribution. While the routine is actively
    driving the mouse this never fires at all; only genuine idle gets filled in.
    `state.next_idle_move_at` holds the drawn still BUDGET in seconds, not an absolute time.
    """
    if state.next_idle_move_at is None:
        state.next_idle_move_at = _draw_idle_still_segment()
        return
    if time.monotonic() - _last_mouse_activity < state.next_idle_move_at:
        return

    cx, cy = get_cursor_pos()
    point = pick_aim_point(cx, cy, IDLE_MOUSE_MOVE_RADIUS_PX)
    if is_point_within_window(*point):
        move_to_human(ctx.ser, point[0], point[1], duration_scale=IDLE_MOUSE_MOVE_SPEED_SCALE)

    state.next_idle_move_at = _draw_idle_still_segment()


# ---------------- controller state ----------------
class ControllerState:
    """Cross-cutting state shared by the engine and every routine.

    `config` is whatever this cycle's resolve_config() produced -- routines
    should read from it, not from the bridge directly. `data` is a free-form
    dict routines can use for their own bookkeeping (e.g. a current target
    id) without controller.py needing to know about it.
    """

    def __init__(self):
        self.bridge_config = None
        self._bridge_config_fetched_at = 0.0
        self.config = None
        self.data = {}
        self.active_routine_path = None  # set by main() on routine switch -- read by
                                           # debug_log.tick()'s per-tick snapshot line

        self.start_time = time.time()
        self.paused = False
        self.safety_stopped = False
        self.safety_reason = None
        self._safety_reminder_at = 0.0
        self.next_break_at = None
        self.break_until = None
        self.next_idle_move_at = None

        clear_movement_abort()

    def refresh_bridge_config(self, force: bool = False):
        now = time.time()
        if force or self.bridge_config is None or now - self._bridge_config_fetched_at >= CONFIG_POLL_INTERVAL:
            cfg = get_config()
            if cfg is not None:
                self.bridge_config = cfg
                self._bridge_config_fetched_at = now
        return self.bridge_config

    def trigger_safety_stop(self, reason: str):
        if self.safety_stopped:
            return
        self.safety_stopped = True
        self.safety_reason = reason
        request_movement_abort()
        debug_log.log("!", f"Safety stop: {reason}. Automation paused -- fix the issue and restart.")

    def print_safety_reminder(self):
        now = time.time()
        if now - self._safety_reminder_at > 30:
            debug_log.log("!", f"Still stopped: {self.safety_reason}")
            self._safety_reminder_at = now

    def break_due(self, breaks_cfg: dict) -> bool:
        """True exactly when the next maybe_toggle_break() call would START a break (as opposed
        to being mid-break, or still counting down toward one). Deliberately side-effect free --
        it never schedules, never starts and never ends a break -- so main() can ask "is a break
        about to begin?" and give the routine a chance to tidy up first, without changing when
        the break itself actually happens.

        ADDED 2026-08-20 for routines/chinchompa_hunting.py (user requirement: "make it so it
        picks up the trap before the break function"). Breaks are toggled BEFORE routine.tick()
        in the main loop, and tick() is skipped for the whole break, so without this a routine
        physically cannot clean up after a break has started -- four laid box traps would sit
        out a 3-12 minute break unattended. See main()'s before_break dispatch."""
        if not breaks_cfg.get("enabled"):
            return False
        if self.break_until is not None:
            return False  # already on one (or one just ended -- maybe_toggle_break reschedules)
        if self.next_break_at is None:
            return False  # first call for this config; maybe_toggle_break only schedules
        return time.time() >= self.next_break_at

    def maybe_toggle_break(self, breaks_cfg: dict):
        if not breaks_cfg.get("enabled"):
            self.next_break_at = None
            self.break_until = None
            return

        now = time.time()

        if self.break_until is not None:
            if now >= self.break_until:
                self.break_until = None
                self._schedule_next_break(breaks_cfg, now)
            return

        if self.next_break_at is None:
            self._schedule_next_break(breaks_cfg, now)
            return

        if now >= self.next_break_at:
            duration_min = random.uniform(
                breaks_cfg.get("durationMinMinutes", 3),
                breaks_cfg.get("durationMaxMinutes", 12),
            )
            self.break_until = now + duration_min * 60
            debug_log.log("break", f"taking a {duration_min:.1f}-minute break")

    def _schedule_next_break(self, breaks_cfg: dict, now: float):
        wait_min = random.uniform(
            breaks_cfg.get("everyMinMinutes", 45),
            breaks_cfg.get("everyMaxMinutes", 75),
        )
        self.next_break_at = now + wait_min * 60

    def on_break(self) -> bool:
        return self.break_until is not None and time.time() < self.break_until


class Context:
    """Everything a routine needs that isn't a bare module-level function."""

    def __init__(self, ser: serial.Serial, state: ControllerState, events=None):
        self.ser = ser
        self.state = state
        # Live view of the bridge's UDP stream (bridge_events.EventStream), or None when the
        # engine did not start one. Routines MUST tolerate None -- it is an accelerator, and
        # every path that uses it needs a working fallback without it.
        self.events = events


def check_safety(state: ControllerState, config: dict):
    safety = config.get("safety", {})

    max_hours = safety.get("maxRuntimeHours", 0)
    if max_hours and (time.time() - state.start_time) / 3600 >= max_hours:
        state.trigger_safety_stop(f"max runtime of {max_hours}h reached")
        return

    if safety.get("stopOnInventoryFull") and len(get_inventory()) >= INVENTORY_SIZE:
        state.trigger_safety_stop("inventory full")


def check_pvp_threat(ctx, state: ControllerState, config: dict):
    """Flee via royal seed pod if another player is within the current
    wilderness level's attack range. Engine-level, like check_safety above --
    runs every tick regardless of which routine is active, so no routine
    needs its own copy of this.

    Range formula: attackable if abs(their combat level - ours) <= our
    current wilderness level. This is the commonly-cited approximation of
    the game's real level-range calculation -- recalled, not re-verified
    in-game, and doesn't account for a skulled attacker's extended range, so
    treat it as a reasonable default rather than an exact rule.
    """
    import travel  # local import: travel.py imports controller back (same reason login's import is deferred in main())

    pvp_cfg = config.get("pvpSafety", {})
    if not pvp_cfg.get("fleeEnabled") or state.safety_stopped:
        return

    player = get_player()
    if not player.get("inWilderness"):
        return

    wilderness_level = player.get("wildernessLevel")
    my_combat = player.get("combatLevel")
    if wilderness_level is None or my_combat is None:
        return

    for other in get_players():
        other_combat = other.get("combatLevel")
        if other_combat is None:
            continue
        if abs(other_combat - my_combat) <= wilderness_level:
            debug_log.log("!", f"PvP threat: {other.get('name')} (combat {other_combat}) is within attack range")
            travel.flee_royal_seed_pod(ctx)
            state.trigger_safety_stop(f"fled from {other.get('name')} in the wilderness")
            return


def check_stray_dialog(ctx):
    """Dismiss a "click here to continue"-style dialogue via Space, e.g. one
    left over from a misclick on scenery sharing a pixel with an intended
    target (see routines/gemstone_crab.py's Priority Objects note). Engine-
    level like check_safety above, so any routine gets this for free instead
    of a stray dialogue silently eating every click after it until a human
    notices. Deliberately does not touch a multiple-choice dialogue (GET
    /state's "dialogOpen" already excludes DIALOG_OPTION) -- continuing that
    blind would mean guessing a choice.

    Only presses Space once per call, so a genuinely multi-stage dialogue
    (several consecutive "click to continue" pages) only advances one stage per
    tick rather than being fully cleared in one shot -- user report 2026-08-14,
    Pest Control: not wrong, but not useful there either, since that routine's
    own region-based phase detection doesn't depend on a dialogue being fully
    dismissed to keep working. A routine can opt out entirely by setting
    SKIP_STRAY_DIALOG_CHECK = True at module level (see the main loop above,
    and routines/pest_control.py for the reference case) rather than this
    function trying to guess how many stages to clear.
    """
    if get_state().get("dialogOpen"):
        debug_log.log("!", "Dismissing a stray dialogue box (Space)")
        key_tap(ctx.ser, "SPACE")
        human_delay(0.3, 0.6)


def handle_generic_events(state: ControllerState, events: list):
    """Engine-level event handling that applies no matter which routine is loaded."""
    for event_type, payload in events:
        if event_type == "PLAYER_DIED":
            stop_on_death = ((state.config or {}).get("safety") or {}).get("stopOnDeath", True)
            if stop_on_death:
                state.trigger_safety_stop("player died")




def routine_full_path(rel_path: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel_path)


def load_routine(path: str):
    spec = importlib.util.spec_from_file_location("routine", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load routine from '{path}'")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "tick"):
        raise SystemExit(f"Routine '{path}' must define a tick(ctx) function")

    return module


# Maps the RoutineChoice enum (RuneLite HttpBridge plugin's "Routine" dropdown) to a routine
# file, relative to this file's directory. Add an entry here for every value you add to
# RoutineChoice.java. Only consulted when no routine is pinned on the command line.
ROUTINE_FILES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
    "GEMSTONE_CRAB": "routines/gemstone_crab.py",
    "TORMENTED_DEMON": "routines/tormented_demon.py",
    "SHIPWRECK_SALVAGING": "routines/shipwreck_salvaging.py",
    "FIREMAKING": "routines/firemaking.py",
    "BLAST_FURNACE": "routines/blast_furnace.py",
    "GOLEM_CRAFTING": "routines/golem_crafting.py",
    "MASTERING_MIXOLOGY": "routines/mastering_mixology.py",
    "PEST_CONTROL": "routines/pest_control.py",
    "NMZ": "routines/nmz.py",
    "PYRAMID_PLUNDER": "routines/pyramid_plunder.py",
    "CHINCHOMPA_HUNTING": "routines/chinchompa_hunting.py",
    "FALCONRY": "routines/falconry.py",
    "PLANK_MAKE": "routines/plank_make.py",
}


def resolve_config(routine, bridge_config):
    if hasattr(routine, "get_config"):
        return routine.get_config(bridge_config)
    if bridge_config is not None:
        return bridge_config
    return getattr(routine, "DEFAULT_CONFIG", None)


def main():
    import login  # local import: login.py imports controller back, deferred to avoid circularity
    import bridge_events  # local import: bridge_events imports controller back, same reason

    parser = argparse.ArgumentParser(description="Generic RuneLite bridge controller")
    parser.add_argument(
        "routine",
        nargs="?",
        default=None,
        help=(
            "Path to a routine .py file, e.g. routines/slayer.py. Pins the controller to that "
            "routine and ignores the HttpBridge plugin's Routine dropdown -- handy while "
            "developing a routine that isn't wired into ROUTINE_FILES yet. Omit this to pick "
            "the routine live from the RuneLite HttpBridge plugin's Routine setting instead."
        ),
    )
    parser.add_argument(
        "--debug-log",
        action="store_true",
        help=(
            "Persist a structured debug log to logs/debug_<timestamp>.log (per-tick snapshots, "
            "state-diffs, stall detection, full exception tracebacks) on top of today's console "
            "output -- see docs/superpowers/specs/2026-08-02-controller-debug-logger-design.md. "
            "Equivalent to setting the DEBUG_LOG=1 environment variable."
        ),
    )
    args = parser.parse_args()
    debug_log.init(args.debug_log or os.environ.get("DEBUG_LOG") == "1")

    set_dpi_aware()  # must happen before any cursor/window coordinate queries below,
                      # otherwise Windows silently virtualizes them to 96 DPI while
                      # RuneLite's Java window reports/receives real physical pixels
    warn_if_pointer_acceleration()
    start_panic_listener()
    udp = make_udp_listener(UDP_PORT)
    event_stream = bridge_events.EventStream(udp)
    state = ControllerState()
    start_pause_listener(state)

    with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
        time.sleep(2.0)  # Pro Micro reset

        global FIRMWARE_PROTOCOL
        FIRMWARE_PROTOCOL = probe_firmware_protocol(ser)
        debug_log.log("serial", f"firmware protocol v{FIRMWARE_PROTOCOL}"
                                + ("" if FIRMWARE_PROTOCOL >= 3 else " (no flush/wheel/reset)"))
        if FIRMWARE_PROTOCOL >= 3:
            send_line(ser, "R")   # clean slate: releases anything a crashed predecessor left held

        register_fidget_serial(ser)   # arms in-wait fidgeting; see human_delay

        ctx = Context(ser, state, events=event_stream)
        routine = None
        active_path = None
        last_warned_routine = "__unset__"

        while True:
            debug_log.tick(ctx)

            # drain(), NOT pump() -- pump() only reports what arrived on THIS call, and a
            # blocking helper (wait_for_event/wait_ticks/deadline_after_ticks) used elsewhere in
            # a tick() call pumps the socket in its own wait loop too. drain() is the only method
            # that hands back everything pumped anywhere since the last drain(), so nothing seen
            # mid-wait (e.g. PLAYER_DIED during retrieve_trap's 3s HTTP confirm loop) is silently
            # dropped before reaching handle_generic_events/on_event (C1, 2026-08-21).
            events = event_stream.drain()
            handle_generic_events(state, events)
            if routine is not None and hasattr(routine, "on_event"):
                for event_type, payload in events:
                    try:
                        routine.on_event(ctx, event_type, payload)
                    except Exception as exc:
                        debug_log.log_exception("on_event", exc)
                        raise

            if state.paused:
                time.sleep(0.5)
                continue

            bridge_config = state.refresh_bridge_config()

            if args.routine:
                desired_path = args.routine
            else:
                routine_key = (bridge_config or {}).get("routine")
                desired_path = ROUTINE_FILES.get(routine_key) if routine_key else None
                if desired_path is None and routine_key != last_warned_routine:
                    if routine_key is not None and routine_key != "NONE":
                        debug_log.log("!", f"No routine registered for '{routine_key}' "
                                            f"-- add it to ROUTINE_FILES in controller.py.")
                    last_warned_routine = routine_key

            if desired_path is None:
                time.sleep(0.5)
                continue

            if desired_path != active_path:
                routine = load_routine(routine_full_path(desired_path))
                active_path = desired_path
                state.active_routine_path = desired_path
                state.data = {}
                debug_log.log("routine", f"running {desired_path}")
                if hasattr(routine, "setup"):
                    try:
                        routine.setup(ctx)
                    except Exception as exc:
                        debug_log.log_exception("setup", exc)
                        raise

            config = resolve_config(routine, bridge_config)
            if config is None:
                time.sleep(0.5)
                continue
            state.config = config

            if not login.is_fully_logged_in():
                login.attempt_login(ctx)
                time.sleep(login.LOGIN_RETRY_INTERVAL_S)
                continue

            if not get_state().get("windowFocused", True):
                # Don't blind-click into a backgrounded window (e.g. user alt-tabbed).
                time.sleep(0.5)
                continue

            check_safety(state, config)
            check_pvp_threat(ctx, state, config)
            if state.safety_stopped:
                state.print_safety_reminder()
                time.sleep(2.0)
                continue

            if not config.get("enabled", True):
                time.sleep(0.5)
                continue

            breaks_cfg = config.get("breaks", {})
            # Last chance for the routine to put the world back in a state it's happy to leave
            # unattended for the next 3-12 minutes -- runs BEFORE the break is toggled, so
            # whatever it does still happens with the routine fully live (logged in, focused, no
            # safety stop -- every gate above has already passed at this point). The break then
            # starts however long the teardown took. Routines that don't define before_break are
            # completely unaffected. See ControllerState.break_due and
            # routines/chinchompa_hunting.py's own before_break (picking its box traps back up).
            if hasattr(routine, "before_break") and state.break_due(breaks_cfg):
                try:
                    routine.before_break(ctx)
                except Exception as exc:
                    debug_log.log_exception("before_break", exc)
                    raise

            state.maybe_toggle_break(breaks_cfg)
            if state.on_break():
                time.sleep(1.0)
                continue

            if not getattr(routine, "SKIP_STRAY_DIALOG_CHECK", False):
                check_stray_dialog(ctx)
            maybe_idle_mouse_move(ctx, state)
            try:
                routine.tick(ctx)
            except Exception as exc:
                debug_log.log_exception("tick", exc)
                raise

            if config.get("breaks", {}).get("microBreaks") and random.random() < 0.15:
                human_delay(0.3, 1.2)

            time.sleep(STATE_POLL_INTERVAL)


if __name__ == "__main__":
    main()
