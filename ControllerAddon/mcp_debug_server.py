# mcp_debug_server.py
#
# MCP server (stdio transport) exposing the RuneLite HTTP bridge and controller.py's
# mouse/keyboard action primitives as MCP tools -- see
# docs/superpowers/specs/2026-08-06-mcp-debug-server-design.md for the full design.
#
# REQUIRES: controller.py must NOT be running while this server is running -- both hold
# the same Arduino serial port exclusively, only one process can have it open at a time.
#
# Setup: pip install mcp
# Run directly for a first smoke test: python mcp_debug_server.py
# Then register in .mcp.json (repo root) for use from Claude Code.

import sys
import time

import serial
from mcp.server.fastmcp import FastMCP

import controller
import debug_log
from settings import SERIAL_PORT, SERIAL_BAUD


def _patch_debug_log_to_stderr() -> None:
    """debug_log.log()/log_exception() call print(), which defaults to stdout -- fatal for a
    stdio-transport MCP server, since stdout is reserved for JSON-RPC protocol traffic.
    controller.py's own action functions (click_human, right_click_human, key_tap, ...) call
    debug_log.log() internally, so this must run before any tool touches those. Overriding
    `print` as an attribute on debug_log's own module namespace redirects only that module's
    prints to stderr -- Python resolves a bare `print(...)` call against the enclosing module's
    globals before falling back to builtins, so this doesn't touch print() anywhere else."""
    def _print_to_stderr(*args, **kwargs) -> None:
        kwargs["file"] = sys.stderr
        print(*args, **kwargs)

    debug_log.print = _print_to_stderr


_patch_debug_log_to_stderr()

controller.set_dpi_aware()  # must happen before any cursor-position query, same reasoning as
                             # controller.py's own main()

try:
    ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
except serial.SerialException as exc:
    print(
        f"Could not open {SERIAL_PORT}: {exc}\n"
        f"Is controller.py already running? Stop it before starting the debug MCP server -- "
        f"only one process can hold the serial port at a time.",
        file=sys.stderr,
    )
    raise SystemExit(1)

mcp = FastMCP("osrs-debug")

import requests
import settings


def _wrap_bridge_response(response: "requests.Response") -> dict:
    """Turns a raw requests.Response into either its decoded JSON body or a {"error": ...}
    dict -- never raises. Used by _bridge_get_impl. Deliberately separate from
    controller.api_get, which swallows failures for the bot's own tick-to-tick resilience;
    a debugging tool wants a failed/timed-out request to be visibly an error instead."""
    if not (200 <= response.status_code < 300):
        return {"error": f"bridge returned HTTP {response.status_code}"}
    try:
        return response.json()
    except ValueError as exc:
        return {"error": f"invalid JSON from bridge: {exc}"}


def _bridge_get_impl(path: str, params: dict | None, requests_get=requests.get) -> dict:
    """requests_get is injected so tests can stub the network call without touching the real
    bridge -- production code always calls this with the default (requests.get)."""
    try:
        response = requests_get(f"{settings.API_BASE}{path}", params=params, timeout=2.0)
    except Exception as exc:
        return {"error": str(exc)}
    return _wrap_bridge_response(response)


@mcp.tool()
def bridge_get(path: str, params: dict | None = None) -> dict:
    """GET an endpoint on the RuneLite HTTP bridge (e.g. "/player", "/menu", "/npcs",
    "/npc/nearest" with params={"id": 1234}) and return its decoded JSON body. Works for any
    endpoint registered by src/main/java/com/example/providers/*.java, including ones added
    after this tool was written. Returns {"error": "..."} instead of raising if the bridge is
    unreachable, times out, returns a non-2xx status, or returns invalid JSON."""
    return _bridge_get_impl(path, params)


def _require_within_window(x: int, y: int) -> dict | None:
    """None if (x, y) is inside the RuneLite window; otherwise an error dict in the same
    {"ok": False, "error": ...} shape every action tool returns, so callers can
    `if (err := _require_within_window(x, y)) is not None: return err`."""
    if controller.is_point_within_window(x, y):
        return None
    return {"ok": False, "error": f"({x}, {y}) is outside the RuneLite window"}


@mcp.tool()
def get_cursor_position() -> dict:
    """Current physical cursor position, e.g. {"x": 512, "y": 340}."""
    x, y = controller.get_cursor_pos()
    return {"x": x, "y": y}


@mcp.tool()
def move_mouse(x: int, y: int) -> dict:
    """Move the cursor to (x, y) with the same human-like Bezier-path movement controller.py's
    routines use. Rejects targets outside the RuneLite window (returns {"ok": False, "error":
    ...}) rather than moving there."""
    if (err := _require_within_window(x, y)) is not None:
        return err
    controller.move_to_human(ser, x, y)
    return {"ok": True}


@mcp.tool()
def left_click() -> dict:
    """Left-click at the cursor's current position."""
    controller.click_human(ser)
    return {"ok": True}


@mcp.tool()
def right_click() -> dict:
    """Right-click at the cursor's current position."""
    controller.right_click_human(ser)
    return {"ok": True}


@mcp.tool()
def left_click_at(x: int, y: int) -> dict:
    """move_mouse(x, y) then left-click, as one call. Same out-of-window rejection as
    move_mouse."""
    if (err := _require_within_window(x, y)) is not None:
        return err
    controller.move_to_human(ser, x, y)
    controller.click_human(ser)
    return {"ok": True}


@mcp.tool()
def right_click_at(x: int, y: int) -> dict:
    """move_mouse(x, y) then right-click, as one call. Same out-of-window rejection as
    move_mouse."""
    if (err := _require_within_window(x, y)) is not None:
        return err
    controller.move_to_human(ser, x, y)
    controller.right_click_human(ser)
    return {"ok": True}


@mcp.tool()
def key_tap(key: str) -> dict:
    """Tap a single key (e.g. "ESC", "SPACE", "w", "1") via the Arduino, same key-name
    conventions as controller.py's own key_tap."""
    controller.key_tap(ser, key)
    return {"ok": True}


@mcp.tool()
def type_text(text: str, press_enter: bool = False) -> dict:
    """Tap each character of `text` in sequence (spaces mapped to the "SPACE" key name); if
    press_enter is True, taps "ENTER" afterward too. Covers both controller.py's type_text
    (press_enter=False, e.g. the bank search box) and type_digits_and_enter (press_enter=True,
    e.g. an "Enter amount" chatbox prompt) use cases with one tool."""
    for ch in text:
        controller.key_tap(ser, "SPACE" if ch == " " else ch)
    if press_enter:
        controller.key_tap(ser, "ENTER")
    return {"ok": True}


MENU_POLL_INTERVAL_S = 0.05
MENU_POLL_TIMEOUT_S = 0.6


def _match_menu_entry(menu: dict, option: str, target_hint: str | None) -> dict | None:
    """Same matching rule as controller.click_menu_action: case-insensitive substring match on
    `option`, and on `target` too if target_hint is given. Returns the first matching entry
    dict, or None if nothing matches (including when the menu has no entries)."""
    option_lower = option.lower()
    target_lower = target_hint.lower() if target_hint else None
    return next(
        (e for e in menu.get("entries", [])
         if option_lower in (e.get("option") or "").lower()
         and (target_lower is None or target_lower in (e.get("target") or "").lower())),
        None,
    )


@mcp.tool()
def click_menu_entry(x: int, y: int, option: str, target_hint: str | None = None) -> dict:
    """Right-click (x, y), read back the resulting context menu, and left-click whichever entry's
    option matches `option` (case-insensitive substring, e.g. "take"). If target_hint is given,
    it must also substring-match the entry's target text -- narrows to the right entry when
    multiple things share a click point (e.g. a ground item under an NPC).

    Returns {"ok": True, "matched": {...entry...}} on success. On failure -- the menu never
    opened, or nothing matched -- dismisses the menu (moves the cursor clear + ESC, so it
    doesn't sit open blocking the next click) and returns {"ok": False, "error": ...,
    "available_options": [...]} listing what WAS on the menu, so a failed match is diagnosable
    instead of a bare failure."""
    if (err := _require_within_window(x, y)) is not None:
        return err

    controller.move_to_human(ser, x, y)
    controller.right_click_human(ser)

    deadline = time.time() + MENU_POLL_TIMEOUT_S
    menu = controller.get_menu()
    while not menu.get("open") and time.time() < deadline:
        time.sleep(MENU_POLL_INTERVAL_S)
        menu = controller.get_menu()

    if not menu.get("open"):
        return {"ok": False, "error": "menu never opened", "available_options": []}

    match = _match_menu_entry(menu, option, target_hint)
    if match is None:
        available = [e.get("option") for e in menu.get("entries", [])]
        cx, cy = controller.get_cursor_pos()
        controller.move_to_human(ser, *controller.pick_dismiss_point(cx, cy))
        controller.key_tap(ser, "ESC")
        return {"ok": False, "error": f"no '{option}' entry found", "available_options": available}

    controller.move_to_human(ser, match["screenX"], match["screenY"])
    controller.click_human(ser)
    return {"ok": True, "matched": match}


if __name__ == "__main__":
    mcp.run()
