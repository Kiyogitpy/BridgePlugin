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


if __name__ == "__main__":
    mcp.run()
