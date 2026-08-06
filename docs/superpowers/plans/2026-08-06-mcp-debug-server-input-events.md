# MCP Debug Server: Held-Input Primitives + UDP Event Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `ControllerAddon/mcp_debug_server.py` with separated mouse/key down-up primitives (plus a `drag()` convenience and a `release_all()` safety net) and a `poll_events()` tool surfacing the bridge's UDP push event stream — closing the two gaps identified after the base MCP debug server shipped.

**Architecture:** Same single-file server, extended in place. Held-state tracking is two new module-level sets (`_held_buttons`, `_held_keys`) updated by paired down/up tools. The event stream is a module-level `queue.Queue` filled by one daemon thread (started at server startup, reusing `controller.poll_udp`'s existing parsing) and drained by `poll_events()`.

**Tech Stack:** Same as the base server — Python 3.13, official `mcp` SDK, `pytest` for the pure-logic tests, `ControllerAddon/controller.py`'s existing primitives (`mouse_left_down`/`up`, `mouse_right_down`/`up`, `key_down`/`up`, `make_udp_listener`, `poll_udp`).

## Global Constraints

- Every coordinate-taking tool must reject out-of-window targets via `_require_within_window` before any serial write — `drag`'s both endpoints included, same as every tool in the base server.
- `drag`'s `button` parameter must be `"left"` or `"right"` only; anything else returns `{"ok": false, "error": "button must be 'left' or 'right'"}` immediately, before any window check.
- `drag` must release its button in a `finally` block, so an exception raised between the down and up moves still releases it rather than leaving it stuck down.
- The UDP listener's startup failure must be soft: if it can't bind, the server still starts, `bridge_get` and every action tool still work, and `poll_events()` returns `{"error": "..."}` instead of silently returning an always-empty list forever.
- Do not add: scroll/zoom (doesn't exist at the firmware layer), any of `click_entity`/`hover_entity`/`consume_first`/`open_inventory_tab`/`open_prayer_tab`/the camera-nudge helpers (explicitly declined — a debugging tool wants precise control, not the bot's own decision-making), or any screenshot/visual capability (still doesn't exist anywhere in this codebase).

---

## File Structure

- **Modify: `ControllerAddon/mcp_debug_server.py`** — add held-state tracking + 6 primitive tools + `release_all()` (Task 1), `drag()` (Task 2), the UDP listener startup + `poll_events()` tool (Task 3).
- **Modify: `ControllerAddon/tests/test_mcp_debug_server.py`** — add tests for all of the above, following the existing file's `monkeypatch`-on-`controller` style.
- **Modify: `AGENTS.md`** — update the "Debugging via the MCP server" section's tool list (Task 3's last step).

No new files. Current `mcp_debug_server.py` is 263 lines (base server, already on `dev`); current test file is 200 lines, 20 tests.

---

## Task 1: Held-state primitives — mouse/key down-up + release_all

**Files:**
- Modify: `ControllerAddon/mcp_debug_server.py`
- Modify: `ControllerAddon/tests/test_mcp_debug_server.py`

**Interfaces:**
- Consumes: `controller.mouse_left_down(ser)`, `controller.mouse_left_up(ser)`, `controller.mouse_right_down(ser)`, `controller.mouse_right_up(ser)`, `controller.key_down(ser, key)`, `controller.key_up(ser, key)` (all pre-existing in `controller.py`), module-level `ser` from the base server.
- Produces: module-level `_held_buttons: set[str]`, `_held_keys: set[str]`; MCP tools `left_mouse_down()`, `left_mouse_up()`, `right_mouse_down()`, `right_mouse_up()`, `key_down(key: str)`, `key_up(key: str)`, `release_all()`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_mcp_debug_server.py`:

```python
def test_left_mouse_down_tracks_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_left_down", lambda ser: None)
    s._held_buttons.clear()
    result = s.left_mouse_down()
    assert result == {"ok": True}
    assert "left" in s._held_buttons


def test_left_mouse_up_clears_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_left_up", lambda ser: None)
    s._held_buttons.clear()
    s._held_buttons.add("left")
    result = s.left_mouse_up()
    assert result == {"ok": True}
    assert "left" not in s._held_buttons


def test_right_mouse_down_tracks_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_right_down", lambda ser: None)
    s._held_buttons.clear()
    result = s.right_mouse_down()
    assert result == {"ok": True}
    assert "right" in s._held_buttons


def test_right_mouse_up_clears_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_right_up", lambda ser: None)
    s._held_buttons.clear()
    s._held_buttons.add("right")
    result = s.right_mouse_up()
    assert result == {"ok": True}
    assert "right" not in s._held_buttons


def test_key_down_tracks_held_key(monkeypatch):
    monkeypatch.setattr(s.controller, "key_down", lambda ser, key: None)
    s._held_keys.clear()
    result = s.key_down("LEFT")
    assert result == {"ok": True}
    assert "LEFT" in s._held_keys


def test_key_up_clears_held_key(monkeypatch):
    monkeypatch.setattr(s.controller, "key_up", lambda ser, key: None)
    s._held_keys.clear()
    s._held_keys.add("LEFT")
    result = s.key_up("LEFT")
    assert result == {"ok": True}
    assert "LEFT" not in s._held_keys


def test_release_all_releases_tracked_buttons_and_keys(monkeypatch):
    released = []
    monkeypatch.setattr(s.controller, "mouse_left_up", lambda ser: released.append("mouse_left_up"))
    monkeypatch.setattr(s.controller, "mouse_right_up", lambda ser: released.append("mouse_right_up"))
    monkeypatch.setattr(s.controller, "key_up", lambda ser, key: released.append(f"key_up:{key}"))
    s._held_buttons.clear()
    s._held_buttons.update({"left", "right"})
    s._held_keys.clear()
    s._held_keys.update({"LEFT", "SHIFT"})
    result = s.release_all()
    assert result["ok"] is True
    assert set(result["released_buttons"]) == {"left", "right"}
    assert set(result["released_keys"]) == {"LEFT", "SHIFT"}
    assert s._held_buttons == set()
    assert s._held_keys == set()
    assert set(released) == {"mouse_left_up", "mouse_right_up", "key_up:LEFT", "key_up:SHIFT"}


def test_release_all_noop_when_nothing_held():
    s._held_buttons.clear()
    s._held_keys.clear()
    result = s.release_all()
    assert result == {"ok": True, "released_buttons": [], "released_keys": []}
```

Note: `_held_buttons`/`_held_keys` are shared mutable module state, not reset automatically between tests the way `monkeypatch` attributes are — every test above explicitly clears them first, so test order/isolation doesn't matter. Keep this pattern for any further tests touching these sets.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: FAIL — `AttributeError: module 'mcp_debug_server' has no attribute '_held_buttons'` (and similar for the new tool names).

- [ ] **Step 3: Implement the held-state primitives**

Add to `ControllerAddon/mcp_debug_server.py`, after the end of the existing `type_text` tool's function body and before the `MENU_POLL_INTERVAL_S = 0.05` line:

```python
_held_buttons: set[str] = set()
_held_keys: set[str] = set()


@mcp.tool()
def left_mouse_down() -> dict:
    """Press and hold the left mouse button at the cursor's current position, without
    releasing. Tracked so release_all() can clean it up if the matching left_mouse_up() call
    never happens."""
    controller.mouse_left_down(ser)
    _held_buttons.add("left")
    return {"ok": True}


@mcp.tool()
def left_mouse_up() -> dict:
    """Release the left mouse button."""
    controller.mouse_left_up(ser)
    _held_buttons.discard("left")
    return {"ok": True}


@mcp.tool()
def right_mouse_down() -> dict:
    """Press and hold the right mouse button at the cursor's current position, without
    releasing. Tracked so release_all() can clean it up if the matching right_mouse_up() call
    never happens."""
    controller.mouse_right_down(ser)
    _held_buttons.add("right")
    return {"ok": True}


@mcp.tool()
def right_mouse_up() -> dict:
    """Release the right mouse button."""
    controller.mouse_right_up(ser)
    _held_buttons.discard("right")
    return {"ok": True}


@mcp.tool()
def key_down(key: str) -> dict:
    """Press and hold a key (e.g. "LEFT", "RIGHT", "SHIFT"), without releasing -- for camera
    rotation or any other held-key gesture. Tracked so release_all() can clean it up if the
    matching key_up() call never happens."""
    controller.key_down(ser, key)
    _held_keys.add(key)
    return {"ok": True}


@mcp.tool()
def key_up(key: str) -> dict:
    """Release a key pressed via key_down()."""
    controller.key_up(ser, key)
    _held_keys.discard(key)
    return {"ok": True}


@mcp.tool()
def release_all() -> dict:
    """Release every mouse button and key currently tracked as held (via left_mouse_down/
    right_mouse_down/key_down) -- a safety net for a forgotten _up() call or an interrupted
    debugging session. Safe to call with nothing held; returns what it released."""
    released_buttons = []
    for button in list(_held_buttons):
        if button == "left":
            controller.mouse_left_up(ser)
        else:
            controller.mouse_right_up(ser)
        _held_buttons.discard(button)
        released_buttons.append(button)

    released_keys = []
    for key in list(_held_keys):
        controller.key_up(ser, key)
        _held_keys.discard(key)
        released_keys.append(key)

    return {"ok": True, "released_buttons": released_buttons, "released_keys": released_keys}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (28 passed — 20 existing + 8 new)

- [ ] **Step 5: Manual live verification**

With RuneLite running, `controller.py` stopped, server running (`python ControllerAddon/mcp_debug_server.py`):

- Call `key_down("LEFT")`, wait ~1s, confirm the camera is visibly rotating, then call `key_up("LEFT")` and confirm it stops.
- Call `left_mouse_down()` without a matching `left_mouse_up()`, then call `release_all()` — confirm the button visibly releases (e.g. no stuck held-click behavior in the client) and the returned `released_buttons` includes `"left"`.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/mcp_debug_server.py ControllerAddon/tests/test_mcp_debug_server.py
git commit -m "Add held mouse/key down-up MCP tools + release_all safety net"
```

---

## Task 2: drag() tool

**Files:**
- Modify: `ControllerAddon/mcp_debug_server.py`
- Modify: `ControllerAddon/tests/test_mcp_debug_server.py`

**Interfaces:**
- Consumes: `_held_buttons`, `_require_within_window`, `controller.mouse_left_down`/`mouse_left_up`/`mouse_right_down`/`mouse_right_up`, `controller.move_to_human` (all from Task 1 / the base server).
- Produces: MCP tool `drag(x1: int, y1: int, x2: int, y2: int, button: str = "left") -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_mcp_debug_server.py`:

```python
def test_drag_rejects_invalid_button():
    result = s.drag(0, 0, 10, 10, button="middle")
    assert result == {"ok": False, "error": "button must be 'left' or 'right'"}


def test_drag_rejects_out_of_window_start(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: (x, y) != (-1, -1))
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: move_calls.append((x, y)))
    result = s.drag(-1, -1, 10, 10)
    assert result["ok"] is False
    assert move_calls == []


def test_drag_rejects_out_of_window_end(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: (x, y) != (999, 999))
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: move_calls.append((x, y)))
    result = s.drag(0, 0, 999, 999)
    assert result["ok"] is False
    assert move_calls == []


def test_drag_presses_moves_and_releases_left_button(monkeypatch):
    events = []
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: events.append(("move", x, y)))
    monkeypatch.setattr(s.controller, "mouse_left_down", lambda ser: events.append(("down",)))
    monkeypatch.setattr(s.controller, "mouse_left_up", lambda ser: events.append(("up",)))
    s._held_buttons.clear()
    result = s.drag(1, 2, 3, 4)
    assert result == {"ok": True}
    assert events == [("move", 1, 2), ("down",), ("move", 3, 4), ("up",)]
    assert "left" not in s._held_buttons


def test_drag_releases_button_even_if_move_raises(monkeypatch):
    events = []
    move_calls = []
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)

    def move_maybe_raise(ser, x, y, **kwargs):
        move_calls.append((x, y))
        events.append(("move", x, y))
        if len(move_calls) == 2:  # the move to (x2, y2)
            raise RuntimeError("boom")

    monkeypatch.setattr(s.controller, "move_to_human", move_maybe_raise)
    monkeypatch.setattr(s.controller, "mouse_right_down", lambda ser: events.append(("down",)))
    monkeypatch.setattr(s.controller, "mouse_right_up", lambda ser: events.append(("up",)))
    s._held_buttons.clear()
    raised = False
    try:
        s.drag(1, 2, 3, 4, button="right")
    except RuntimeError:
        raised = True
    assert raised, "expected RuntimeError to propagate"
    assert ("up",) in events
    assert "right" not in s._held_buttons
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: FAIL — `AttributeError: module 'mcp_debug_server' has no attribute 'drag'`.

- [ ] **Step 3: Implement `drag`**

Add to `ControllerAddon/mcp_debug_server.py`, after the `release_all()` tool from Task 1 and before the `MENU_POLL_INTERVAL_S = 0.05` line:

```python
@mcp.tool()
def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left") -> dict:
    """Press `button` down at (x1, y1), move precisely to (x2, y2), then release -- for
    drag gestures (e.g. splitting an inventory stack, dragging a map). `button` must be
    "left" or "right". Rejects either endpoint outside the RuneLite window before touching
    serial. Always releases the button even if the move between down and up raises, so a
    failed drag can't leave it stuck down."""
    if button not in ("left", "right"):
        return {"ok": False, "error": "button must be 'left' or 'right'"}
    if (err := _require_within_window(x1, y1)) is not None:
        return err
    if (err := _require_within_window(x2, y2)) is not None:
        return err

    down = controller.mouse_left_down if button == "left" else controller.mouse_right_down
    up = controller.mouse_left_up if button == "left" else controller.mouse_right_up

    controller.move_to_human(ser, x1, y1, aim_radius=0, overshoot_chance=0)
    down(ser)
    _held_buttons.add(button)
    try:
        controller.move_to_human(ser, x2, y2, aim_radius=0, overshoot_chance=0)
    finally:
        up(ser)
        _held_buttons.discard(button)
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (33 passed — 28 from Task 1 + 5 new)

- [ ] **Step 5: Manual live verification**

With RuneLite running, `controller.py` stopped, server running:

- Get two inventory item slot coordinates via `bridge_get(path="/inventory")`, then call `drag` from one slot to another (inventory reordering, or a stackable-item split if the target supports it) — confirm the drag visibly happens and lands correctly.
- Call `drag` with an out-of-window endpoint — confirm it returns the window-rejection error without moving the cursor.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/mcp_debug_server.py ControllerAddon/tests/test_mcp_debug_server.py
git commit -m "Add drag MCP tool with tested button-release-on-exception safety"
```

---

## Task 3: UDP event stream (poll_events) + AGENTS.md update

**Files:**
- Modify: `ControllerAddon/mcp_debug_server.py`
- Modify: `ControllerAddon/tests/test_mcp_debug_server.py`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: `controller.make_udp_listener(port)`, `controller.poll_udp(sock)` (both pre-existing in `controller.py`), `settings.UDP_PORT`.
- Produces: module-level `_udp_socket` (a bound UDP socket, or `None` if binding failed), `_udp_error: str | None`, `_event_queue: queue.Queue`, MCP tool `poll_events() -> dict`.

- [ ] **Step 1: Write the failing tests**

First, update the test file's import/stub section at the top (needed so importing `mcp_debug_server` during test collection doesn't attempt a real UDP bind or spin up a thread that calls unmocked `select.select` on a mock socket). Modify the top of `ControllerAddon/tests/test_mcp_debug_server.py`:

```python
import os
import queue
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# mcp_debug_server opens a real serial port AND a real UDP socket at import time -- stub both
# before importing it, so these pure-logic tests run without any hardware or network binding.
import serial
serial.Serial = MagicMock(return_value=MagicMock())

import controller
controller.make_udp_listener = MagicMock(return_value=MagicMock())
controller.poll_udp = MagicMock(return_value=[])

import mcp_debug_server as s  # noqa: E402
```

(This replaces the existing `import os`, `import sys`, `from unittest.mock import MagicMock`, `sys.path.insert(...)`, `import serial`, `serial.Serial = MagicMock(...)`, `import mcp_debug_server as s` lines at the top of the file with the version above — same structure, plus the `queue` import and the two new `controller` stub lines before `import mcp_debug_server as s`.)

Then append the new tests to the end of the file:

```python
def test_poll_events_returns_error_when_listener_unavailable(monkeypatch):
    monkeypatch.setattr(s, "_udp_socket", None)
    monkeypatch.setattr(s, "_udp_error", "port already in use")
    result = s.poll_events()
    assert result == {"error": "UDP listener not available: port already in use"}


def test_poll_events_drains_queued_events(monkeypatch):
    monkeypatch.setattr(s, "_udp_socket", object())  # any non-None sentinel
    test_queue = queue.Queue()
    test_queue.put(("PLAYER_DIED", None))
    test_queue.put(("HP_CHANGED", {"hp": 10}))
    monkeypatch.setattr(s, "_event_queue", test_queue)
    result = s.poll_events()
    assert result == {"events": [
        {"type": "PLAYER_DIED", "payload": None},
        {"type": "HP_CHANGED", "payload": {"hp": 10}},
    ]}
    assert test_queue.empty()


def test_poll_events_returns_empty_list_when_nothing_queued(monkeypatch):
    monkeypatch.setattr(s, "_udp_socket", object())
    monkeypatch.setattr(s, "_event_queue", queue.Queue())
    result = s.poll_events()
    assert result == {"events": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: FAIL — `AttributeError: module 'mcp_debug_server' has no attribute '_udp_socket'` (and similar).

- [ ] **Step 3: Implement the UDP listener startup and `poll_events`**

First, in `ControllerAddon/mcp_debug_server.py`, update the top import block: change

```python
import random
import sys
import time
```

to

```python
import queue
import random
import sys
import threading
import time
```

and change

```python
from settings import SERIAL_PORT, SERIAL_BAUD
```

to

```python
from settings import SERIAL_PORT, SERIAL_BAUD, UDP_PORT
```

Then, after the existing serial-port `try`/`except serial.SerialException` block (right after the `raise SystemExit(1)` line, ending that block) and before the `mcp = FastMCP("osrs-debug")` line, add:

```python
_event_queue: "queue.Queue" = queue.Queue()
_udp_error: str | None = None

try:
    _udp_socket = controller.make_udp_listener(UDP_PORT)
except OSError as exc:
    _udp_socket = None
    _udp_error = str(exc)
    print(f"UDP event listener not available: {exc}", file=sys.stderr)


def _event_listener_loop() -> None:
    while True:
        for event_type, payload in controller.poll_udp(_udp_socket):
            _event_queue.put((event_type, payload))
        time.sleep(0.05)


if _udp_socket is not None:
    threading.Thread(target=_event_listener_loop, daemon=True).start()
```

Finally, add the `poll_events` tool at the end of the file, after `click_menu_entry`'s function body and before the `if __name__ == "__main__":` line:

```python
@mcp.tool()
def poll_events() -> dict:
    """Drain and return every bridge UDP event (e.g. "PLAYER_DIED", "NPC_DIED", "LEVEL_UP",
    "HP_CHANGED") received since the last call to this tool, or since server startup if this
    is the first call. A background thread continuously drains the UDP socket into an
    in-memory queue from server startup, so events aren't lost between calls the way reading
    the OS socket buffer only at call time would risk. Returns {"error": "..."} instead if the
    UDP listener failed to start (e.g. the port was already in use)."""
    if _udp_socket is None:
        return {"error": f"UDP listener not available: {_udp_error}"}
    events = []
    while not _event_queue.empty():
        event_type, payload = _event_queue.get_nowait()
        events.append({"type": event_type, "payload": payload})
    return {"events": events}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (36 passed — 33 from Tasks 1-2 + 3 new)

- [ ] **Step 5: Manual live verification**

With RuneLite running, `controller.py` stopped, server running:

- Call `poll_events()` once to confirm it returns `{"events": []}` (or a real event list) rather than an error — confirms the UDP socket bound successfully.
- Trigger a real bridge event (e.g. take damage in combat, or log out to trigger a state-change event) and call `poll_events()` again — confirm the triggered event appears.

- [ ] **Step 6: Update AGENTS.md's tool list**

In `AGENTS.md`, find the "Debugging via the MCP server" section (currently reads: "Everything above ... is also available as typed MCP tools -- `bridge_get`, `get_cursor_position`, `move_mouse`, `left_click`/`right_click`, `left_click_at`/`right_click_at`, `key_tap`, `type_text`, `click_menu_entry` -- via ..."). Update that tool list to also include the new tools, and add a pointer to this addendum's spec. Replace:

```markdown
Everything above (querying bridge state, right-clicking, reading `/menu`, clicking a matched
entry) is also available as typed MCP tools -- `bridge_get`, `get_cursor_position`,
`move_mouse`, `left_click`/`right_click`, `left_click_at`/`right_click_at`, `key_tap`,
`type_text`, `click_menu_entry` -- via `ControllerAddon/mcp_debug_server.py`, registered in
`.mcp.json`. Prefer these tools over raw `curl`/`WebFetch` and one-off verification scripts
when they're available (i.e. this server is registered and reachable) -- same underlying
primitives, no risk of forgetting `set_dpi_aware()` the way a one-off script can (see the DPI
gotcha two paragraphs up).
```

with:

```markdown
Everything above (querying bridge state, right-clicking, reading `/menu`, clicking a matched
entry) is also available as typed MCP tools -- `bridge_get`, `get_cursor_position`,
`move_mouse`, `left_click`/`right_click`, `left_click_at`/`right_click_at`,
`left_mouse_down`/`left_mouse_up`, `right_mouse_down`/`right_mouse_up`, `drag`,
`key_tap`, `key_down`/`key_up`, `type_text`, `release_all`, `click_menu_entry`,
`poll_events` -- via `ControllerAddon/mcp_debug_server.py`, registered in `.mcp.json`. Prefer
these tools over raw `curl`/`WebFetch` and one-off verification scripts when they're available
(i.e. this server is registered and reachable) -- same underlying primitives, no risk of
forgetting `set_dpi_aware()` the way a one-off script can (see the DPI gotcha two paragraphs
up).
```

And after the existing line `See `docs/superpowers/specs/2026-08-06-mcp-debug-server-design.md` for the full design.` at the end of that section, add:

```markdown

Held-input primitives (`left_mouse_down`/`up`, `right_mouse_down`/`up`, `key_down`/`up`,
`drag`, `release_all`) and the UDP event stream (`poll_events`) are documented in
`docs/superpowers/specs/2026-08-06-mcp-debug-server-input-events-design.md`.
```

- [ ] **Step 7: Run the full test suite one more time**

Run: `pytest ControllerAddon/tests/test_mcp_debug_server.py -v`
Expected: PASS (36 passed) — confirms the AGENTS.md-only change in this step didn't break anything (it shouldn't touch any code, but this catches any accidental edit elsewhere).

- [ ] **Step 8: Commit**

```bash
git add ControllerAddon/mcp_debug_server.py ControllerAddon/tests/test_mcp_debug_server.py AGENTS.md
git commit -m "Add UDP event stream (poll_events) and document the expanded tool list"
```
