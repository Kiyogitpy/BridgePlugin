# controller.py
#
# Generic engine for the RuneLite HTTP/UDP bridge. controller.py owns the
# *plumbing*: talking to the bridge (HTTP state + UDP events), driving the
# Arduino mouse/keyboard, the panic key, and cross-cutting concerns like
# safety stops and human-like breaks.
#
# It knows nothing about slayer, mining, or any other activity. What to
# actually do is supplied by a "routine" -- a separate .py file. Normally
# you just run:
#
#     python controller.py
#
# and controller.py picks the routine for you based on the "Profile"
# dropdown in the RuneLite Controller Settings panel (see PROFILE_ROUTINES
# below, which maps each Profile enum value to a routine file). Switching
# the dropdown in-game swaps the running routine live, no restart needed.
#
# You can also pin a specific routine on the command line, which ignores the
# dropdown entirely -- handy while writing a new routine that isn't wired
# into Profile.java yet:
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

API_BASE = "http://localhost:8080"
UDP_PORT = 50555
SERIAL_PORT = os.environ.get("BRIDGE_SERIAL_PORT", "COM6")
SERIAL_BAUD = 115200

CONFIG_POLL_INTERVAL = 1.0   # how often GET /config is refreshed
STATE_POLL_INTERVAL = 0.3    # pace of the main decision loop
INVENTORY_SIZE = 28


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


def get_objects():
    return api_get("/objects") or []


def get_consumables():
    return api_get("/consumables") or {"food": [], "prayer": []}


def get_ground_items():
    return api_get("/ground-items") or []


def get_inventory():
    return api_get("/inventory") or []


def get_config():
    return api_get("/config")


def is_logged_in():
    return get_state().get("loggedIn", False)


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


# ---------------- Human-like trajectory generation ----------------
def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def ease_in_out(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


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

    curve_mag = clamp(dist * random.uniform(0.10, 0.25), 8.0, 220.0)
    curve_sign = random.choice([-1.0, 1.0])

    c1_t = random.uniform(0.20, 0.38)
    c2_t = random.uniform(0.62, 0.82)

    c1x = sx + dx * c1_t + perp_x * curve_mag * curve_sign * random.uniform(0.6, 1.1)
    c1y = sy + dy * c1_t + perp_y * curve_mag * curve_sign * random.uniform(0.6, 1.1)
    c2x = sx + dx * c2_t - perp_x * curve_mag * curve_sign * random.uniform(0.4, 1.0)
    c2y = sy + dy * c2_t - perp_y * curve_mag * curve_sign * random.uniform(0.4, 1.0)

    ex2 = ex + random.uniform(-0.4, 0.4)
    ey2 = ey + random.uniform(-0.4, 0.4)

    return Bezier((sx, sy), (c1x, c1y), (c2x, c2y), (ex2, ey2))


def estimate_duration_ms(dist: float) -> float:
    base = 90 + 0.55 * dist
    base = base ** 0.92
    jitter = random.uniform(0.85, 1.15)
    return clamp(base * jitter, 110, 950)


def micro_pause_chance(dist: float) -> bool:
    if dist < 350:
        return random.random() < 0.05
    return random.random() < 0.12


# ---------------- Aim radius + optional overshoot ----------------
DEFAULT_AIM_RADIUS = 10
DEFAULT_OVERSHOOT_PX = 12
DEFAULT_OVERSHOOT_CHANCE = 0.25


def pick_aim_point(x: int, y: int, radius: int) -> tuple:
    if radius <= 0:
        return x, y
    return x + random.randint(-radius, radius), y + random.randint(-radius, radius)


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
) -> None:
    start = get_cursor_pos()
    aim = pick_aim_point(x, y, aim_radius)

    over = maybe_overshoot_point(start, aim, overshoot_px=overshoot_px, chance=overshoot_chance)
    if over is not None:
        _move_to_human_exact(ser, over[0], over[1], fps=fps)
        time.sleep(random.uniform(0.01, 0.05))
        _move_to_human_exact(ser, aim[0], aim[1], fps=max(90, fps - 25))
    else:
        _move_to_human_exact(ser, aim[0], aim[1], fps=fps)


def _move_to_human_exact(ser: serial.Serial, x: int, y: int, *, fps: int = 140) -> None:
    sx, sy = get_cursor_pos()
    ex, ey = x, y
    dist = math.hypot(ex - sx, ey - sy)
    if dist < 2:
        return

    bez = build_bezier_path((sx, sy), (ex, ey))
    duration_ms = estimate_duration_ms(dist)
    steps = max(18, int((duration_ms / 1000.0) * fps))
    t0 = time.perf_counter()

    pause_mid = micro_pause_chance(dist)
    pause_step = random.randint(int(steps * 0.35), int(steps * 0.7)) if pause_mid else -1

    for i in range(1, steps + 1):
        t = i / steps
        te = ease_in_out(t)

        tx, ty = bez.point(te)

        if t > 0.65:
            jitter = clamp(dist / 900.0, 0.2, 1.2)
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
# NOTE: every provider (NPCProvider, GroundItemProvider, InventoryProvider,
# ConsumablesProvider, PrayerProvider, ObjectProvider) already returns
# *absolute* screen coordinates (canvas offset baked in server-side) -- do
# not add the canvas offset again here.
def click_entity(ser: serial.Serial, entity: dict, offset=(-7, 7)) -> bool:
    x, y = entity.get("screenX"), entity.get("screenY")
    if x is None or y is None:
        return False

    move_to_human(ser, int(x) + offset[0], int(y) + offset[1], aim_radius=3, overshoot_chance=0.25)
    click_human(ser)
    return True


def consume_first(ser: serial.Serial, items: list) -> bool:
    if not items:
        return False
    return click_entity(ser, items[0])


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

    logout_hp = safety.get("emergencyLogoutHp", 0)
    if logout_hp:
        hp = get_player().get("hp")
        if hp is not None and hp <= logout_hp:
            state.trigger_safety_stop(f"hp dropped to {hp} (emergency threshold {logout_hp})")
            return

    if safety.get("stopOnInventoryFull") and len(get_inventory()) >= INVENTORY_SIZE:
        state.trigger_safety_stop("inventory full")


def handle_generic_events(state: ControllerState, events: list):
    """Engine-level event handling that applies no matter which routine is loaded."""
    for event_type, payload in events:
        if event_type == "PLAYER_DIED":
            stop_on_death = ((state.config or {}).get("safety") or {}).get("stopOnDeath", True)
            if stop_on_death:
                state.trigger_safety_stop("player died")


# ---------------- routine loading ----------------
# Maps the ControllerConfig.Profile enum (RuneLite "Profile" dropdown) to a
# routine file, relative to this file's directory. Add an entry here for
# every value you add to Profile.java.
PROFILE_ROUTINES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
}


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


def resolve_config(routine, bridge_config):
    if hasattr(routine, "get_config"):
        return routine.get_config(bridge_config)
    if bridge_config is not None:
        return bridge_config
    return getattr(routine, "DEFAULT_CONFIG", None)


def main():
    parser = argparse.ArgumentParser(description="Generic RuneLite bridge controller")
    parser.add_argument(
        "routine",
        nargs="?",
        default=None,
        help=(
            "Path to a routine .py file, e.g. routines/mining.py. Pins the "
            "controller to that routine and ignores the in-game Profile "
            "dropdown -- handy while developing a routine that isn't wired "
            "into Profile.java / PROFILE_ROUTINES yet. Omit this to let the "
            "RuneLite Controller Settings panel's Profile dropdown pick the "
            "routine via PROFILE_ROUTINES."
        ),
    )
    args = parser.parse_args()

    start_panic_listener()
    udp = make_udp_listener(UDP_PORT)
    state = ControllerState()

    with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
        time.sleep(2.0)  # Pro Micro reset

        ctx = Context(ser, state)
        routine = None
        active_path = None
        last_warned_profile = "__unset__"

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
                profile_key = (bridge_config or {}).get("profile")
                desired_path = PROFILE_ROUTINES.get(profile_key) if profile_key else None
                if desired_path is None and profile_key != last_warned_profile:
                    if profile_key is not None:
                        print(f"[!] No routine registered for profile '{profile_key}' "
                              f"-- add it to PROFILE_ROUTINES in controller.py.")
                    last_warned_profile = profile_key

            if desired_path is None:
                time.sleep(0.5)
                continue

            if desired_path != active_path:
                routine = load_routine(routine_full_path(desired_path))
                active_path = desired_path
                state.data = {}
                print(f"[profile] running {desired_path}")
                if hasattr(routine, "setup"):
                    routine.setup(ctx)

            config = resolve_config(routine, bridge_config)
            if config is None:
                time.sleep(0.5)
                continue
            state.config = config

            if not is_logged_in():
                time.sleep(1.0)
                continue

            if not get_state().get("windowFocused", True):
                # Don't blind-click into a backgrounded window (e.g. user alt-tabbed).
                time.sleep(0.5)
                continue

            check_safety(state, config)
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

            routine.tick(ctx)

            if config.get("breaks", {}).get("microBreaks") and random.random() < 0.15:
                time.sleep(random.uniform(0.3, 1.2))

            time.sleep(STATE_POLL_INTERVAL)


if __name__ == "__main__":
    main()
