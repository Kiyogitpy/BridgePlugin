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
def api_get(path, params=None, timeout=0.3):
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def get_state():
    return api_get("/state") or {}


def get_player():
    return api_get("/player") or {}


def get_combat():
    return api_get("/combat") or {}


def get_prayers():
    return api_get("/prayers") or {}


def get_npcs():
    return api_get("/npcs") or []


def get_players():
    return api_get("/players") or []


def get_objects():
    return api_get("/objects") or []


def get_consumables():
    return api_get("/consumables") or {"food": [], "prayer": []}


def get_ground_items():
    return api_get("/ground-items") or []


def get_inventory():
    return api_get("/inventory") or []


def get_bank():
    return api_get("/bank") or {"open": False}


def get_sailing():
    return api_get("/sailing") or {
        "navigating": False,
        "facilitiesTabOpen": False,
        "takeDownSails": {},
        "setSail": {},
    }


def get_equipment():
    return api_get("/equipment") or {}


def get_graphics_objects():
    return api_get("/graphics-objects") or []


def get_tormented_demon_config():
    return api_get("/tormented-demon-config")


def get_minimap_walk_point(x: int, y: int, plane: int):
    return api_get("/minimap/walk", params={"x": x, "y": y, "plane": plane}) or {"onMinimap": False}


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
    mouse_left_down(ser)
    time.sleep(random.uniform(0.045, 0.11))
    mouse_left_up(ser)
    time.sleep(random.uniform(0.03, 0.09))


def key_tap(ser: serial.Serial, key: str) -> None:
    send_line(ser, f"KT {key}")


def key_down(ser: serial.Serial, key: str) -> None:
    send_line(ser, f"KD {key}")


def key_up(ser: serial.Serial, key: str) -> None:
    send_line(ser, f"KU {key}")


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

    print(f"[!] Aim point is behind a UI panel -- rotating camera ({key}) to work around it")
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

    print(f"[!] Target is off-screen -- rotating camera ({key}) to bring it into view")
    nudge_camera_yaw(ser, key)


# NOTE: every provider (NPCProvider, GroundItemProvider, InventoryProvider,
# ConsumablesProvider, PrayerProvider, ObjectProvider) already returns
# *absolute* screen coordinates (canvas offset baked in server-side) -- do
# not add the canvas offset again here.
def click_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None,
                  check_ui_blockers=True, simulate_mistakes=True) -> bool:
    x, y = entity.get("screenX"), entity.get("screenY")
    if x is None or y is None:
        return False

    aim_x, aim_y = int(x) + offset[0], int(y) + offset[1]

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
        print(f"[!] Misclick -- aimed beside {entity.get('name')} instead of on it")

    if not is_point_within_window(aim_x, aim_y):
        print(f"[!] Skipping click on {entity.get('name')} -- aim point is outside the RuneLite window")
        return False

    if check_ui_blockers:
        blocker = is_point_blocked_by_ui(aim_x, aim_y)
        if blocker is not None:
            rotate_camera_away_from_ui(ser, blocker)
            return False

    move_to_human(
        ser,
        aim_x,
        aim_y,
        aim_radius=ENTITY_AIM_RADIUS,
        refresh_target=refresh_target,
    )
    if _movement_abort.is_set():
        return False
    click_human(ser)
    return True


def hover_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None) -> bool:
    """Move the cursor onto `entity` (same aim jitter as click_entity) without
    clicking -- for anticipatory hovering over something a routine already
    knows it'll need next, while it's otherwise idle-waiting on the current
    action to resolve (e.g. drifting toward the tunnel while a crab is still
    being fought). Unlike click_entity this is just a courtesy move, not a
    real interaction attempt: it silently no-ops (no camera nudge, no log)
    if the point isn't safely reachable right now, rather than trying to
    work around it.
    """
    x, y = entity.get("screenX"), entity.get("screenY")
    if x is None or y is None:
        return False

    aim_x, aim_y = int(x) + offset[0], int(y) + offset[1]
    if not is_point_within_window(aim_x, aim_y) or is_point_blocked_by_ui(aim_x, aim_y) is not None:
        return False

    move_to_human(ser, aim_x, aim_y, aim_radius=ENTITY_AIM_RADIUS, refresh_target=refresh_target)
    return not _movement_abort.is_set()


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
    return click_entity(ser, items[0])


def random_viewport_point():
    """A random absolute screen point somewhere inside the 3D game viewport
    (not the minimap, inventory, or any other UI chrome), or None if the
    viewport isn't known yet (e.g. still loading)."""
    state = get_state()
    canvas_x, canvas_y = state.get("canvasScreenX"), state.get("canvasScreenY")
    vp_x_off, vp_y_off = state.get("viewportXOffset"), state.get("viewportYOffset")
    vp_w, vp_h = state.get("viewportWidth"), state.get("viewportHeight")
    if None in (canvas_x, canvas_y, vp_x_off, vp_y_off, vp_w, vp_h) or vp_w <= 0 or vp_h <= 0:
        return None
    x = canvas_x + vp_x_off + random.randint(0, vp_w - 1)
    y = canvas_y + vp_y_off + random.randint(0, vp_h - 1)
    return x, y


def maybe_misclick(ctx, chance: float = MISTAKE_RANDOM_CLICK_CHANCE) -> bool:
    """With `chance` probability, click a random point in the game viewport
    instead of doing anything purposeful this tick -- a stray misclick (an
    empty patch of ground, a random tree, whatever happens to be under the
    cursor) rather than every click in a session being deliberate. Engine-
    level like check_stray_dialog, so every routine gets it for free.

    Returns True if it fired (caller should skip its real action this tick
    and let the next tick carry on normally), False otherwise.
    """
    if random.random() > chance:
        return False

    point = random_viewport_point()
    if point is None:
        return False

    x, y = point
    if is_point_blocked_by_ui(x, y):
        return False

    print("[!] Misclick -- clicked a random point instead of the intended target")
    click_entity(ctx.ser, {"screenX": x, "screenY": y, "name": "(random point)"}, offset=(0, 0))
    return True


def maybe_idle_mouse_move(ctx, state: "ControllerState") -> None:
    """Every IDLE_MOUSE_MOVE_MIN/MAX_INTERVAL_S seconds or so, drift the
    cursor a short distance from wherever it already is, slowly, with no
    click. A small nearby nudge (not a jump clear across the viewport at
    normal click speed) reads as a resting hand shifting slightly, not a
    purposeful move -- the whole point of this being "idle". Purely cosmetic
    movement -- it always runs before the caller's real action for this
    tick, never instead of it (unlike maybe_misclick, which deliberately
    eats a tick), so it never delays or replaces an actual click.
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
        print(f"[!] Safety stop: {reason}. Automation paused -- fix the issue and restart.")

    def print_safety_reminder(self):
        now = time.time()
        if now - self._safety_reminder_at > 30:
            print(f"[!] Still stopped: {self.safety_reason}")
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
            print(f"[break] taking a {duration_min:.1f}-minute break")

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
            print(f"[!] PvP threat: {other.get('name')} (combat {other_combat}) is within attack range")
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
        print("[!] Dismissing a stray dialogue box (Space)")
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
    args = parser.parse_args()

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
            events = poll_udp(udp)
            handle_generic_events(state, events)
            if routine is not None and hasattr(routine, "on_event"):
                for event_type, payload in events:
                    routine.on_event(ctx, event_type, payload)

            bridge_config = state.refresh_bridge_config()

            if args.routine:
                desired_path = args.routine
            else:
                routine_key = (bridge_config or {}).get("routine")
                desired_path = ROUTINE_FILES.get(routine_key) if routine_key else None
                if desired_path is None and routine_key != last_warned_routine:
                    if routine_key is not None and routine_key != "NONE":
                        print(f"[!] No routine registered for '{routine_key}' "
                              f"-- add it to ROUTINE_FILES in controller.py.")
                    last_warned_routine = routine_key

            if desired_path is None:
                time.sleep(0.5)
                continue

            if desired_path != active_path:
                routine = load_routine(routine_full_path(desired_path))
                active_path = desired_path
                state.data = {}
                print(f"[routine] running {desired_path}")
                if hasattr(routine, "setup"):
                    routine.setup(ctx)

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
            if not maybe_misclick(ctx):
                routine.tick(ctx)

            if config.get("breaks", {}).get("microBreaks") and random.random() < 0.15:
                time.sleep(random.uniform(0.3, 1.2))

            time.sleep(STATE_POLL_INTERVAL)


if __name__ == "__main__":
    main()
