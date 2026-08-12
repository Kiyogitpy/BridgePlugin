# mcp_debug_server.py
#
# MCP server (stdio transport) exposing the RuneLite HTTP bridge as read-only MCP tools -- see
# docs/superpowers/specs/2026-08-06-mcp-debug-server-design.md for the original design.
#
# REMOVED 2026-08-12: every Arduino-serial-backed action tool (move_mouse, left_click,
# right_click, left_click_at, right_click_at, key_tap, type_text, left/right_mouse_down/up,
# key_down/up, release_all, drag, click_menu_entry) -- this server is now purely read-only
# (bridge_get, get_cursor_position, poll_events), none of which ever touch the serial port, so
# it can stay connected for the whole session, including while controller.py is running and
# holds the port. There is no longer any "stop one before starting the other" conflict to
# manage. If live click/mouse debugging is needed again, drive it through controller.py itself
# or reintroduce action tools in a separate, deliberately serial-holding process.
#
# UDP_PORT (50555) -- controller.py's own main() also binds this for its event stream, and only
# one process can hold it -- is still bound lazily, on first poll_events() call rather than at
# import (see _ensure_udp_listener()), so starting this server doesn't preempt controller.py's
# bind if controller.py hasn't started yet either. Whichever process's bind attempt actually
# runs first still wins; poll_events() reports the loser's failure as {"error": ...} rather than
# crashing.
#
# Setup: pip install mcp
# Run directly for a first smoke test: python mcp_debug_server.py
# Then register in .mcp.json (repo root) for use from Claude Code.

import queue
import sys
import threading
import time

from mcp.server.fastmcp import FastMCP

import controller
from settings import UDP_PORT

controller.set_dpi_aware()  # must happen before any cursor-position query, same reasoning as
                             # controller.py's own main()

_event_queue: "queue.Queue" = queue.Queue()
_udp_socket = None
_udp_error: str | None = None
_udp_listener_started = False


def _ensure_udp_listener() -> None:
    """Lazily binds UDP_PORT and starts the background drain thread on first poll_events()
    call, instead of at import time -- starting this server shouldn't preempt controller.py's
    own UDP bind (controller.py needs its own listener for real routine logic, not just logging
    -- see handle_generic_events/routine.on_event in its main loop) if controller.py hasn't
    grabbed the port yet. A failed bind isn't fatal here either; poll_events() surfaces it as
    {"error": ...} via _udp_error, same as before."""
    global _udp_socket, _udp_error, _udp_listener_started
    if _udp_listener_started:
        return
    _udp_listener_started = True
    try:
        _udp_socket = controller.make_udp_listener(UDP_PORT)
    except OSError as exc:
        _udp_error = str(exc)
        print(f"UDP event listener not available: {exc}", file=sys.stderr)
        return
    threading.Thread(target=_event_listener_loop, daemon=True).start()


def _event_listener_loop() -> None:
    """Runs in a daemon thread from the first poll_events() call. If controller.poll_udp ever
    raises (e.g. recvfrom raising ConnectionResetError/OSError after certain network conditions
    on Windows), the thread must not just die silently -- _udp_socket would stay non-None and
    poll_events() would keep taking its success branch forever, returning {"events": []} with
    no signal that the listener actually stopped working. So on any exception: record it in
    _udp_error, clear _udp_socket (poll_events()'s existing `if _udp_socket is None:` check
    then catches it), and stop -- there's no point continuing to poll a socket we've given up
    on."""
    global _udp_socket, _udp_error
    while True:
        try:
            for event_type, payload in controller.poll_udp(_udp_socket):
                _event_queue.put((event_type, payload))
        except Exception as exc:
            _udp_error = f"listener stopped: {exc}"
            print(f"UDP event listener stopped unexpectedly: {exc}", file=sys.stderr)
            _udp_socket = None
            return
        time.sleep(0.05)

mcp = FastMCP("osrs-debug")

import requests
import settings


def _wrap_bridge_response(response: "requests.Response") -> dict:
    """Turns a raw requests.Response into either its decoded JSON body or a {"error": ...}
    dict -- never raises. Used by _bridge_get_impl. Deliberately separate from
    controller.api_get, which swallows failures for the bot's own tick-to-tick resilience;
    a debugging tool wants a failed/timed-out request to be visibly an error instead.

    Runs the same controller._scale_screen_coords pass controller.api_get applies -- CONFIRMED
    live 2026-08-08 (MCP debug session): every screenX/screenY (and window/canvas/mouse
    screen-position field) the bridge returns is in the RuneLite/Java client's own "user space",
    not this (DPI-aware) process's real physical pixels -- see controller.get_dpi_scale's
    docstring for the full story."""
    if not (200 <= response.status_code < 300):
        return {"error": f"bridge returned HTTP {response.status_code}"}
    try:
        return controller._scale_screen_coords(response.json(), controller.get_dpi_scale())
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
    unreachable, times out, returns a non-2xx status, or returns invalid JSON.

    Some endpoints (e.g. "/npcs", "/players", "/objects", "/tiles", "/inventory",
    "/ground-items") return a JSON array rather than an object -- FastMCP would otherwise
    unpack a returned list into separate content blocks (or none at all for an empty list), so
    array bodies are wrapped here as {"items": [...]}."""
    body = _bridge_get_impl(path, params)
    return body if isinstance(body, dict) else {"items": body}


@mcp.tool()
def get_cursor_position() -> dict:
    """Current physical cursor position, e.g. {"x": 512, "y": 340}. Read-only OS query -- does
    not touch the serial port."""
    x, y = controller.get_cursor_pos()
    return {"x": x, "y": y}


@mcp.tool()
def poll_events() -> dict:
    """Drain and return every bridge UDP event (e.g. "PLAYER_DIED", "NPC_DIED", "LEVEL_UP",
    "HP_CHANGED") received since the last call to this tool, or since server startup if this
    is the first call. A background thread continuously drains the UDP socket into an
    in-memory queue from server startup, so events aren't lost between calls the way reading
    the OS socket buffer only at call time would risk. Returns {"error": "..."} instead if the
    UDP listener failed to start (e.g. the port was already in use)."""
    _ensure_udp_listener()
    if _udp_socket is None:
        return {"error": f"UDP listener not available: {_udp_error}"}
    events = []
    while not _event_queue.empty():
        event_type, payload = _event_queue.get_nowait()
        events.append({"type": event_type, "payload": payload})
    return {"events": events}


if __name__ == "__main__":
    mcp.run()
