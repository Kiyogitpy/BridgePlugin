# MCP Debug Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ControllerAddon/mcp_debug_server.py`, an MCP server (stdio transport) that exposes the RuneLite HTTP bridge and `controller.py`'s mouse/keyboard action primitives as MCP tools, so live bot debugging happens through typed tool calls instead of ad hoc `curl`/`WebFetch` and one-off verification scripts.

**Architecture:** A single-file stdio MCP server that `import controller` and reuses its existing helpers (`api_get`, `set_dpi_aware`, `get_cursor_pos`, `move_to_human`, `click_human`/`right_click_human`, `key_tap`/`type_text`, `get_menu`, `is_point_within_window`) rather than reimplementing the serial protocol or human-movement logic. It opens its own exclusive serial connection at startup — `controller.py` must not be running concurrently. Registered project-wide via `.mcp.json` at the repo root.

**Tech Stack:** Python 3.13, official `mcp` SDK (stdio transport, `FastMCP` server), `pytest` for the pure-logic unit tests, reusing `pyserial`/`requests` already used by `controller.py`.

## Global Constraints

- Server must not write anything to `stdout` other than MCP protocol traffic (stdio transport requirement) — `debug_log`'s prints must be redirected to `stderr` for this process.
- `controller.py` must not be running while this server holds the serial port — startup must fail loudly and clearly if the port is busy, not with a raw `pyserial` traceback.
- `bridge_get` must surface bridge errors as `{"error": ...}` rather than swallowing them the way `controller.py`'s own `get_*()` wrappers do (spec: read side must show failures, not hide them).
- Every click/move-to-coordinate tool must reject targets outside the RuneLite window via `controller.is_point_within_window` before writing to serial.
- `type_text(text, press_enter=False)` replaces both of `controller.py`'s `type_text`/`type_digits_and_enter` with one tool (spec explicitly chose one tool over two).
- No panic-key/emergency-stop mechanism for this server (spec: out of scope — the operator is watching every tool call happen).
- No log-tailing or `controller.py` process-management tools (spec: explicitly out of scope for this iteration).

---

## File Structure

- **Create: `ControllerAddon/mcp_debug_server.py`** — the whole server: stdout-safety patch, serial startup, all tool definitions (`bridge_get`, cursor/click/key primitives, `click_menu_entry`), and the two pure helper functions extracted for unit testing (`_wrap_bridge_response`, `_match_menu_entry`).
- **Create: `ControllerAddon/tests/test_mcp_debug_server.py`** — pytest unit tests for the pure logic (menu matching, bridge-error wrapping, window-bounds gating), using `unittest.mock` to stub out `controller`'s serial/HTTP calls. No live hardware or game client needed to run these.
- **Create: `.mcp.json`** (repo root) — registers `mcp_debug_server.py` as a project MCP server.
- **Modify: `AGENTS.md`** — add a short pointer section noting the MCP debug server exists, what it's for, and the "stop `controller.py` first" constraint, next to the existing "Autonomous live debugging" section it builds on.

`ControllerAddon/tests/` doesn't exist yet — created by Task 2's first step.

---

## Task 1: Server skeleton — stdout safety, serial startup, no tools yet

**Files:**
- Create: `ControllerAddon/mcp_debug_server.py`

**Interfaces:**
- Consumes: `controller.set_dpi_aware()`, `controller.SERIAL_PORT`/`controller.SERIAL_BAUD` (via `from settings import *` inside `controller`), `serial.Serial`
- Produces: module-level `mcp = FastMCP("osrs-debug")` instance (later tasks register tools on it); module-level `ser: serial.Serial` opened at import time; `_patch_debug_log_to_stderr() -> None`

This task proves the server can start, hold the serial port, and fail cleanly if it can't — before any tool logic exists to complicate debugging a startup failure.

- [ ] **Step 1: Install the MCP SDK**

Run: `pip install mcp`

Confirm it's importable:

Run: `python -c "import mcp; from mcp.server.fastmcp import FastMCP; print('ok')"`
Expected: `ok`

- [ ] **Step 2: Write `mcp_debug_server.py`'s stdout-safety patch and serial startup**

```python
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


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 3: Verify the stdout patch actually redirects**

Run: `python -c "
import mcp_debug_server as s
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    import debug_log
    debug_log.log('test', 'hello')
print('stdout captured:', repr(buf.getvalue()))
"`

Expected: `stdout captured: ''` (empty — the "hello" line went to stderr, visible separately in the terminal, not captured into `buf`).

Note: run this from `ControllerAddon/` (or with it on `PYTHONPATH`) since `mcp_debug_server.py` does bare `import controller`/`import debug_log`/`import settings`, matching this codebase's existing convention (see `controller.py`'s own `import debug_log`, `from settings import *`).

- [ ] **Step 4: Verify serial-busy failure is clear, not a raw traceback**

With `controller.py` (or anything else) already holding `SERIAL_PORT` open, run:

Run: `python mcp_debug_server.py`
Expected: exits with the `"Could not open COM6: ... Is controller.py already running?"` message on stderr, exit code 1 — not a bare `SerialException` traceback.

Then stop whatever was holding the port and confirm a clean run:

Run: `python mcp_debug_server.py`
Expected: process starts and blocks (stdio server waiting for a client) — Ctrl+C to stop it for now, no tools exist yet to call.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/mcp_debug_server.py
git commit -m "Add MCP debug server skeleton: stdout-safety patch + serial startup"
```

---

## Task 2: `bridge_get` tool

**Files:**
- Create: `ControllerAddon/tests/test_mcp_debug_server.py`
- Modify: `ControllerAddon/mcp_debug_server.py`

**Interfaces:**
- Consumes: `settings.API_BASE` (via `import settings` — reused directly here rather than through `controller.api_get`, since that function's silent-failure behavior is exactly what this tool must NOT have, per the Global Constraints)
- Produces: `_wrap_bridge_response(response: requests.Response) -> dict`, `_bridge_get_impl(path: str, params: dict | None) -> dict` (pure-ish, takes an injected `requests_get` callable for testability), MCP tool `bridge_get(path: str, params: dict | None = None) -> dict`

`ControllerAddon/tests/` doesn't exist yet; created here via an `__init__.py`-less `tests/` dir (plain pytest test discovery, no package needed).

- [ ] **Step 1: Write the failing tests**

```python
# ControllerAddon/tests/test_mcp_debug_server.py
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# mcp_debug_server opens a real serial port at import time -- stub serial.Serial before
# importing it, so these pure-logic tests run without any hardware attached.
import serial
serial.Serial = MagicMock(return_value=MagicMock())

import mcp_debug_server as s  # noqa: E402


def _fake_response(status_code=200, json_body=None, raise_exc=None):
    resp = MagicMock()
    resp.status_code = status_code
    if raise_exc is not None:
        resp.json.side_effect = raise_exc
    else:
        resp.json.return_value = json_body
    return resp


def test_wrap_bridge_response_success_passes_json_through():
    resp = _fake_response(200, {"loggedIn": True})
    assert s._wrap_bridge_response(resp) == {"loggedIn": True}


def test_wrap_bridge_response_non_2xx_returns_error_with_status():
    resp = _fake_response(500, {"ignored": True})
    result = s._wrap_bridge_response(resp)
    assert result == {"error": "bridge returned HTTP 500"}


def test_wrap_bridge_response_bad_json_returns_error():
    resp = _fake_response(200, raise_exc=ValueError("no JSON"))
    result = s._wrap_bridge_response(resp)
    assert result["error"].startswith("invalid JSON")


def test_bridge_get_impl_wraps_request_exception():
    def raising_get(url, params=None, timeout=None):
        raise ConnectionError("refused")

    result = s._bridge_get_impl("/state", None, requests_get=raising_get)
    assert result == {"error": "refused"}


def test_bridge_get_impl_builds_correct_url_and_returns_json():
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _fake_response(200, {"open": True})

    result = s._bridge_get_impl("/menu", {"id": 5}, requests_get=fake_get)
    assert result == {"open": True}
    assert calls == [(f"{s.settings.API_BASE}/menu", {"id": 5})]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pip install pytest` (one-time), then `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: FAIL — `AttributeError: module 'mcp_debug_server' has no attribute '_wrap_bridge_response'` (and similar for the other new names).

- [ ] **Step 3: Implement `bridge_get`**

Add to `ControllerAddon/mcp_debug_server.py`, after the `mcp = FastMCP("osrs-debug")` line and before the `if __name__ == "__main__":` block:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Manual smoke test against the real bridge**

With RuneLite + the plugin running (and, per the Global Constraints, `controller.py` NOT running so this server can hold the serial port), run:

Run: `python mcp_debug_server.py` (from `ControllerAddon/`, leave it running)

In a second terminal, use the MCP inspector or a quick manual client to call `bridge_get(path="/state")` — or, simpler for a first check, temporarily add a `print(_bridge_get_impl("/state", None), file=sys.stderr)` right before `mcp.run()`, run once, then remove it.

Expected: a real JSON dict back (e.g. containing `"loggedIn"`, `"windowFocused"` keys), not an `{"error": ...}`.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/mcp_debug_server.py ControllerAddon/tests/test_mcp_debug_server.py
git commit -m "Add bridge_get MCP tool with tested error wrapping"
```

---

## Task 3: Cursor and click/key primitive tools

**Files:**
- Modify: `ControllerAddon/mcp_debug_server.py`
- Modify: `ControllerAddon/tests/test_mcp_debug_server.py`

**Interfaces:**
- Consumes: `controller.get_cursor_pos()`, `controller.move_to_human(ser, x, y)`, `controller.click_human(ser)`, `controller.right_click_human(ser)`, `controller.key_tap(ser, key)`, `controller.is_point_within_window(x, y)`, module-level `ser` from Task 1
- Produces: `_require_within_window(x: int, y: int) -> dict | None` (returns an error dict if out of bounds, else `None`), MCP tools `get_cursor_position()`, `move_mouse(x, y)`, `left_click()`, `right_click()`, `left_click_at(x, y)`, `right_click_at(x, y)`, `key_tap(key)`, `type_text(text, press_enter=False)`

- [ ] **Step 1: Write the failing tests for the window-bounds gate**

Append to `ControllerAddon/tests/test_mcp_debug_server.py`:

```python
def test_require_within_window_returns_none_when_inside(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    assert s._require_within_window(100, 100) is None


def test_require_within_window_returns_error_when_outside(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: False)
    result = s._require_within_window(9999, 9999)
    assert result == {"ok": False, "error": "(9999, 9999) is outside the RuneLite window"}


def test_move_mouse_rejects_out_of_window_target(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: False)
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y: move_calls.append((x, y)))
    result = s.move_mouse(50, 50)
    assert result["ok"] is False
    assert move_calls == []  # never attempted the move


def test_move_mouse_moves_when_inside_window(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y: move_calls.append((x, y)))
    result = s.move_mouse(50, 50)
    assert result == {"ok": True}
    assert move_calls == [(50, 50)]


def test_type_text_maps_space_and_optional_enter(monkeypatch):
    taps = []
    monkeypatch.setattr(s.controller, "key_tap", lambda ser, key: taps.append(key))
    s.type_text("ab c", press_enter=True)
    assert taps == ["a", "b", "SPACE", "c", "ENTER"]


def test_type_text_no_enter_by_default(monkeypatch):
    taps = []
    monkeypatch.setattr(s.controller, "key_tap", lambda ser, key: taps.append(key))
    s.type_text("hi")
    assert taps == ["h", "i"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: FAIL — `AttributeError: module 'mcp_debug_server' has no attribute '_require_within_window'` (and similar for `move_mouse`, `type_text`).

- [ ] **Step 3: Implement the primitive tools**

Add to `ControllerAddon/mcp_debug_server.py`, after the `bridge_get` tool:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Manual live verification**

With RuneLite running, `controller.py` stopped, server running (`python mcp_debug_server.py`):

- Call `get_cursor_position()` — confirm the returned `{x, y}` matches where the cursor visually is.
- Call `move_mouse` with a point well inside the game canvas — confirm the cursor visibly moves there.
- Call `move_mouse` with a point far outside the window (e.g. `(-100, -100)`) — confirm it returns `{"ok": False, "error": ...}` and the cursor does NOT move.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/mcp_debug_server.py ControllerAddon/tests/test_mcp_debug_server.py
git commit -m "Add cursor/click/key primitive MCP tools"
```

---

## Task 4: `click_menu_entry` tool

**Files:**
- Modify: `ControllerAddon/mcp_debug_server.py`
- Modify: `ControllerAddon/tests/test_mcp_debug_server.py`

**Interfaces:**
- Consumes: `controller.get_menu()`, `controller.right_click_human`, `controller.move_to_human`, `controller.click_human`, `controller.key_tap`, `controller.get_cursor_pos`, `controller.pick_dismiss_point`, `_require_within_window` from Task 3, `time.time`/`time.sleep`
- Produces: `_match_menu_entry(menu: dict, option: str, target_hint: str | None) -> dict | None` (pure), MCP tool `click_menu_entry(x, y, option, target_hint=None) -> dict`

`_match_menu_entry` mirrors the matching logic inside `controller.click_menu_action` (`ControllerAddon/controller.py:970-978`) exactly, extracted as a standalone pure function so it's unit-testable without any live menu.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_mcp_debug_server.py`:

```python
def test_match_menu_entry_matches_by_option_substring():
    menu = {"open": True, "entries": [
        {"index": 0, "option": "Attack", "target": "Goblin", "screenX": 10, "screenY": 20},
        {"index": 1, "option": "Examine", "target": "Goblin", "screenX": 10, "screenY": 35},
    ]}
    match = s._match_menu_entry(menu, "attack", None)
    assert match == menu["entries"][0]


def test_match_menu_entry_matches_by_option_and_target_hint():
    menu = {"open": True, "entries": [
        {"index": 0, "option": "Take", "target": "Coins", "screenX": 1, "screenY": 2},
        {"index": 1, "option": "Take", "target": "Bones", "screenX": 3, "screenY": 4},
    ]}
    match = s._match_menu_entry(menu, "take", "bones")
    assert match == menu["entries"][1]


def test_match_menu_entry_returns_none_when_nothing_matches():
    menu = {"open": True, "entries": [{"index": 0, "option": "Cancel", "target": "", "screenX": 0, "screenY": 0}]}
    assert s._match_menu_entry(menu, "attack", None) is None


def test_click_menu_entry_rejects_out_of_window_origin(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: False)
    result = s.click_menu_entry(x=-1, y=-1, option="attack")
    assert result["ok"] is False
    assert "outside the RuneLite window" in result["error"]


def test_click_menu_entry_reports_available_options_on_no_match(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y: None)
    monkeypatch.setattr(s.controller, "right_click_human", lambda ser: None)
    monkeypatch.setattr(s.controller, "click_human", lambda ser: None)
    monkeypatch.setattr(s.controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(s.controller, "get_cursor_pos", lambda: (5, 5))
    monkeypatch.setattr(s.controller, "pick_dismiss_point", lambda x, y: (200, 200))
    monkeypatch.setattr(s.controller, "get_menu", lambda: {
        "open": True, "entries": [{"index": 0, "option": "Cancel", "target": "", "screenX": 0, "screenY": 0}],
    })
    result = s.click_menu_entry(x=10, y=10, option="attack")
    assert result["ok"] is False
    assert result["available_options"] == ["Cancel"]


def test_click_menu_entry_clicks_the_match(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y: moves.append((x, y)))
    monkeypatch.setattr(s.controller, "right_click_human", lambda ser: None)
    monkeypatch.setattr(s.controller, "click_human", lambda ser: clicks.append(True))
    monkeypatch.setattr(s.controller, "get_menu", lambda: {
        "open": True, "entries": [{"index": 0, "option": "Attack", "target": "Goblin", "screenX": 77, "screenY": 88}],
    })
    moves, clicks = [], []
    result = s.click_menu_entry(x=10, y=10, option="attack")
    assert result == {"ok": True, "matched": {"index": 0, "option": "Attack", "target": "Goblin", "screenX": 77, "screenY": 88}}
    assert (77, 88) in moves
    assert clicks == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: FAIL — `AttributeError: module 'mcp_debug_server' has no attribute '_match_menu_entry'` (and similar for `click_menu_entry`).

- [ ] **Step 3: Implement `click_menu_entry`**

Add to `ControllerAddon/mcp_debug_server.py`, after the Task 3 tools, plus a `time` import at the top of the file alongside the existing imports:

```python
import time

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Manual live end-to-end verification**

With RuneLite running, `controller.py` stopped, server running:

- Right-click an inventory item's screen coordinates (get them first via `bridge_get(path="/inventory")`) and call `click_menu_entry` with an `option` you know is on that item's menu (e.g. `"drop"` or `"examine"` — pick something non-destructive). Confirm the menu opens, the correct entry highlights/gets clicked, and the expected effect happens (verify via another `bridge_get` call, e.g. re-checking `/inventory`, per the spec's "verify through a state endpoint, not by asking the user" convention).
- Call it again with an `option` that's deliberately NOT on that menu. Confirm it returns `{"ok": False, "available_options": [...]}` listing the real options, and that the menu visibly closes rather than staying open.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/mcp_debug_server.py ControllerAddon/tests/test_mcp_debug_server.py
git commit -m "Add click_menu_entry MCP tool with tested match logic"
```

---

## Task 5: Register the server and document it

**Files:**
- Create: `.mcp.json`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: nothing new (wires up what Tasks 1-4 built)
- Produces: nothing consumed by later tasks — this is the last task

- [ ] **Step 1: Create `.mcp.json`**

```json
{
  "mcpServers": {
    "osrs-debug": {
      "command": "python",
      "args": ["ControllerAddon/mcp_debug_server.py"]
    }
  }
}
```

- [ ] **Step 2: Add a pointer section to `AGENTS.md`**

Insert after the existing "Autonomous live debugging: acting, not just reading" section (after `ControllerAddon/AGENTS.md`'s current last line, ending "...so cursor moves land in the wrong place even though the target coordinates themselves are correct."):

```markdown

## Debugging via the MCP server

Everything above (querying bridge state, right-clicking, reading `/menu`, clicking a matched
entry) is also available as typed MCP tools -- `bridge_get`, `move_mouse`, `left_click`/
`right_click`, `left_click_at`/`right_click_at`, `key_tap`, `type_text`, `click_menu_entry` --
via `ControllerAddon/mcp_debug_server.py`, registered in `.mcp.json`. Prefer these tools over
raw `curl`/`WebFetch` and one-off verification scripts when they're available (i.e. this
server is registered and reachable) -- same underlying primitives, no risk of forgetting
`set_dpi_aware()` the way a one-off script can (see the DPI gotcha two paragraphs up).

**`controller.py` must not be running at the same time as this server** -- both need
exclusive access to the same Arduino serial port. Stop `controller.py` before debugging
through these tools; restart it afterward for real automation runs.

See `docs/superpowers/specs/2026-08-06-mcp-debug-server-design.md` for the full design.
```

- [ ] **Step 3: Verify the full tool list is reachable end-to-end**

With RuneLite running, `controller.py` stopped:

Run: `python ControllerAddon/mcp_debug_server.py`

From Claude Code (which reads `.mcp.json` from the repo root automatically on session start), confirm the `osrs-debug` server's tools appear in the tool list, and call `bridge_get(path="/state")` through the actual MCP connection (not the manual smoke-test hack from Task 2) to confirm the registration wiring itself works, not just the underlying functions.

- [ ] **Step 4: Run the full test suite one more time**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (17 passed) — confirms nothing in Task 5's doc/config changes broke the importable module.

- [ ] **Step 5: Commit**

```bash
git add .mcp.json AGENTS.md
git commit -m "Register the debug MCP server in .mcp.json and document it in AGENTS.md"
```
