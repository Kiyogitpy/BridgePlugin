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

import debug_log
from settings import *  # noqa: F401,F403 -- all tunables collected in settings.py


# ---------------- panic button ----------------
def panic_exit():
    print("\n[!] Emergency exit triggered (F1 pressed).")
    os._exit(0)  # hard kill, works even if a thread is stuck


def start_panic_listener():
    keyboard.add_hotkey('f1', panic_exit)

    def watchdog():
        keyboard.wait('f1')
        panic_exit()

    threading.Thread(target=watchdog, daemon=True).start()


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
    params = {"fresh": "true"} if fresh else None
    return api_get("/npcs", params=params) or []


def get_players():
    return api_get("/players") or []


def get_objects(radius=None, fresh=False):
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


def get_tiles(fresh=False):
    params = {"fresh": "true"} if fresh else None
    return api_get("/tiles", params=params) or []


def get_spellbook():
    return api_get("/spellbook") or {"visible": False}


def get_menu():
    return api_get("/menu") or {"open": False}


def get_consumables():
    return api_get("/consumables") or {"food": [], "prayer": []}


def get_ground_items(fresh=False):
    params = {"fresh": "true"} if fresh else None
    return api_get("/ground-items", params=params) or []


def get_inventory():
    return api_get("/inventory") or []


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


def get_equipment():
    return api_get("/equipment") or {}


def get_graphics_objects(fresh=False):
    params = {"fresh": "true"} if fresh else None
    return api_get("/graphics-objects", params=params) or []


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


def request_shortest_path(x: int, y: int, plane: int):
    """Ask the (separately installed) Shortest Path plugin to compute a
    route to (x, y, plane). requested=False if that plugin isn't
    installed/running -- see get_shortest_path_result to poll for it."""
    return api_get("/shortest-path/request", params={"x": x, "y": y, "plane": plane}) or {"requested": False}


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


def move_rel_arduino(ser: serial.Serial, dx: int, dy: int, *, max_step: int = 20) -> None:
    while dx != 0 or dy != 0:
        sx = max(-max_step, min(max_step, dx))
        sy = max(-max_step, min(max_step, dy))
        send_line(ser, f"M {sx} {sy}")
        dx -= sx
        dy -= sy
        time.sleep(random.uniform(0.0005, 0.0015))  # small random jitter


def mouse_left_down(ser: serial.Serial) -> None:
    send_line(ser, "MLD")


def mouse_left_up(ser: serial.Serial) -> None:
    send_line(ser, "MLU")


def mouse_left_click(ser: serial.Serial) -> None:
    send_line(ser, "MLC")


def click_human(ser: serial.Serial) -> None:
    debug_log.log("action", f"click_human at {get_cursor_pos()}")
    mouse_left_down(ser)
    time.sleep(random.uniform(0.045, 0.11))
    mouse_left_up(ser)
    time.sleep(random.uniform(0.03, 0.09))


def mouse_right_down(ser: serial.Serial) -> None:
    send_line(ser, "MRD")


def mouse_right_up(ser: serial.Serial) -> None:
    send_line(ser, "MRU")


def mouse_right_click(ser: serial.Serial) -> None:
    send_line(ser, "MRC")


def right_click_human(ser: serial.Serial) -> None:
    debug_log.log("action", f"right_click_human at {get_cursor_pos()}")
    mouse_right_down(ser)
    time.sleep(random.uniform(0.045, 0.11))
    mouse_right_up(ser)
    time.sleep(random.uniform(0.03, 0.09))


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
        time.sleep(random.uniform(0.05, 0.12))
    key_tap(ser, "ENTER")
    time.sleep(random.uniform(0.2, 0.4))


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
        time.sleep(random.uniform(0.05, 0.12))
    time.sleep(random.uniform(0.2, 0.4))


INVENTORY_TAB_KEY = "w"
PRAYER_TAB_KEY = "r"


def open_inventory_tab(ser: serial.Serial) -> None:
    if get_state().get("inventoryTabOpen"):
        return
    key_tap(ser, INVENTORY_TAB_KEY)
    time.sleep(random.uniform(0.2, 0.35))


def open_prayer_tab(ser: serial.Serial) -> None:
    """Tap the prayer-tab hotkey unless it's already open -- PrayerProvider's "icons"
    are only populated while it is (see its "tabOpen" flag)."""
    if get_prayers().get("tabOpen"):
        return
    key_tap(ser, PRAYER_TAB_KEY)
    time.sleep(random.uniform(0.2, 0.35))


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

    curve_mag = clamp(dist * random.uniform(0.15, 0.35), 8.0, 300.0)
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


def estimate_duration_ms(dist: float) -> float:
    base = 90 + 0.55 * dist
    base = base ** 0.92
    # Real click-to-click recordings (aim trainer and live OSRS play) run
    # ~4-4.5x longer than this base estimate, but part of that gap is
    # decision/scan latency before the hand starts moving, not slower
    # movement itself -- take roughly half the gap here.
    base *= 1.9
    jitter = random.uniform(0.85, 1.15)
    return clamp(base * jitter, 200, 1800)


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


def pick_aim_point(x: int, y: int, radius: int) -> tuple:
    if radius <= 0:
        return x, y
    # Uniform over the disc (sqrt of a uniform draw), not the bounding square --
    # a square jitter can land up to radius*sqrt(2) away, which can miss a
    # small clickbox even though it looks "within radius".
    r = radius * math.sqrt(random.random())
    theta = random.uniform(0, 2 * math.pi)
    return int(round(x + r * math.cos(theta))), int(round(y + r * math.sin(theta)))


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
    """
    start = get_cursor_pos()
    aim = pick_aim_point(x, y, aim_radius)

    over = maybe_overshoot_point(start, aim, overshoot_px=overshoot_px, chance=overshoot_chance)
    if over is not None:
        _move_to_human_exact(ser, over[0], over[1], fps=fps, duration_scale=duration_scale)
        if _movement_abort.is_set():
            return
        time.sleep(random.uniform(0.01, 0.05))
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


def _move_to_human_exact(ser: serial.Serial, x: int, y: int, *, fps: int = 140, duration_scale: float = 1.0) -> None:
    sx, sy = get_cursor_pos()
    ex, ey = x, y
    dist = math.hypot(ex - sx, ey - sy)
    if dist < 2:
        return

    bez = build_bezier_path((sx, sy), (ex, ey))
    duration_ms = estimate_duration_ms(dist) * duration_scale
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
            time.sleep(random.uniform(0.03, 0.10))

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
            time.sleep(random.uniform(0.02, 0.05))
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
    time.sleep(random.uniform(0.15, 0.35))
    key_up(ser, key)
    time.sleep(random.uniform(0.1, 0.2))


def open_spellbook_if_needed(ser: serial.Serial) -> None:
    """Idempotent -- taps T (this game's Magic-tab keybind) only if Telekinetic Grab isn't
    already visible, so a hunt_phase call that finds the tab already open doesn't needlessly
    re-tap it every single cast."""
    if get_spellbook().get("visible"):
        return
    key_tap(ser, "T")
    time.sleep(random.uniform(0.3, 0.5))


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
    time.sleep(random.uniform(0.15, 0.3))
    # ADDED 2026-08-09 (user report: "the data is too slow because the goats move around a lot,
    # we click the ground 70% of the time"): target's screenX/screenY are a snapshot from
    # whenever the caller fetched the NPC list, already stale by the time this click actually
    # executes for a fast-moving goat. refresh_target re-queries this SPECIFIC NPC's live
    # position by index mid-flight (same mechanism slayer.py's engage_target already uses for
    # moving combat targets) rather than trusting the one snapshot for the whole click.
    npc_index = target.get("index")
    refresh = (lambda: get_npc_screen_pos_by_index(npc_index)) if npc_index is not None else None
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
def click_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None,
                  check_ui_blockers=True, simulate_mistakes=True, button: str = "left") -> bool:
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
    )
    if _movement_abort.is_set():
        return False
    if button == "right":
        right_click_human(ser)
    else:
        click_human(ser)

    if missed:
        _reclick_after_misclick(ser, entity, offset, refresh_target, check_ui_blockers, button)

    return True


def _reclick_after_misclick(ser: serial.Serial, entity: dict, offset, refresh_target,
                             check_ui_blockers: bool, button: str) -> None:
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
    time.sleep(random.uniform(*MISCLICK_RECLICK_DELAY_S))

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
    move_to_human(ser, aim_x, aim_y, aim_radius=ENTITY_AIM_RADIUS, refresh_target=refresh_target)
    if _movement_abort.is_set():
        return
    if button == "right":
        right_click_human(ser)
    else:
        click_human(ser)


def hover_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None,
                  check_ui_blockers=True) -> bool:
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

    move_to_human(ser, aim_x, aim_y, aim_radius=ENTITY_AIM_RADIUS, refresh_target=refresh_target)
    return not _movement_abort.is_set()


MENU_POLL_INTERVAL_S = 0.05
MENU_POLL_TIMEOUT_S = 0.6


def click_menu_action(ser: serial.Serial, entity: dict, option: str, target_hint: str = None,
                       offset=(-7, 7), check_ui_blockers=True) -> bool:
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
    """
    if not click_entity(ser, entity, offset=offset, check_ui_blockers=check_ui_blockers,
                         simulate_mistakes=False, button="right"):
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
    option_lower = option.lower()
    target_lower = target_hint.lower() if target_hint else None

    def _target_ok(entry: dict) -> bool:
        return target_lower is None or target_lower in (entry.get("target") or "").lower()

    entries = menu.get("entries", [])
    match = next(
        (e for e in entries if option_lower == (e.get("option") or "").lower() and _target_ok(e)),
        None,
    )
    if match is None:
        match = next(
            (e for e in entries if option_lower in (e.get("option") or "").lower() and _target_ok(e)),
            None,
        )

    if match is None:
        debug_log.log("!", f"No '{option}' entry in menu for {entity.get('name')} "
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


def maybe_idle_mouse_move(ctx, state: "ControllerState") -> None:
    """Every IDLE_MOUSE_MOVE_MIN/MAX_INTERVAL_S seconds or so, drift the
    cursor a short distance from wherever it already is, slowly, with no
    click. A small nearby nudge (not a jump clear across the viewport at
    normal click speed) reads as a resting hand shifting slightly, not a
    purposeful move -- the whole point of this being "idle". Purely cosmetic
    movement -- it always runs before the caller's real action for this
    tick and never replaces it.
    """
    now = time.time()
    if state.next_idle_move_at is None:
        state.next_idle_move_at = now + random.uniform(IDLE_MOUSE_MOVE_MIN_INTERVAL_S, IDLE_MOUSE_MOVE_MAX_INTERVAL_S)
        return
    if now < state.next_idle_move_at:
        return

    cx, cy = get_cursor_pos()
    point = pick_aim_point(cx, cy, IDLE_MOUSE_MOVE_RADIUS_PX)
    if is_point_within_window(*point):
        move_to_human(ctx.ser, point[0], point[1], duration_scale=IDLE_MOUSE_MOVE_SPEED_SCALE)

    state.next_idle_move_at = now + random.uniform(IDLE_MOUSE_MOVE_MIN_INTERVAL_S, IDLE_MOUSE_MOVE_MAX_INTERVAL_S)


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

    def __init__(self, ser: serial.Serial, state: ControllerState):
        self.ser = ser
        self.state = state


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
    """
    if get_state().get("dialogOpen"):
        debug_log.log("!", "Dismissing a stray dialogue box (Space)")
        key_tap(ctx.ser, "SPACE")
        time.sleep(random.uniform(0.3, 0.6))


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
}


def resolve_config(routine, bridge_config):
    if hasattr(routine, "get_config"):
        return routine.get_config(bridge_config)
    if bridge_config is not None:
        return bridge_config
    return getattr(routine, "DEFAULT_CONFIG", None)


def main():
    import login  # local import: login.py imports controller back, deferred to avoid circularity

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
    start_panic_listener()
    udp = make_udp_listener(UDP_PORT)
    state = ControllerState()

    with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
        time.sleep(2.0)  # Pro Micro reset

        ctx = Context(ser, state)
        routine = None
        active_path = None
        last_warned_routine = "__unset__"

        while True:
            debug_log.tick(ctx)

            events = poll_udp(udp)
            handle_generic_events(state, events)
            if routine is not None and hasattr(routine, "on_event"):
                for event_type, payload in events:
                    try:
                        routine.on_event(ctx, event_type, payload)
                    except Exception as exc:
                        debug_log.log_exception("on_event", exc)
                        raise

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

            state.maybe_toggle_break(config.get("breaks", {}))
            if state.on_break():
                time.sleep(1.0)
                continue

            check_stray_dialog(ctx)
            maybe_idle_mouse_move(ctx, state)
            try:
                routine.tick(ctx)
            except Exception as exc:
                debug_log.log_exception("tick", exc)
                raise

            if config.get("breaks", {}).get("microBreaks") and random.random() < 0.15:
                time.sleep(random.uniform(0.3, 1.2))

            time.sleep(STATE_POLL_INTERVAL)


if __name__ == "__main__":
    main()
