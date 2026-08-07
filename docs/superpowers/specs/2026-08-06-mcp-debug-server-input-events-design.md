# MCP debug server: held-input primitives + UDP event stream — design

Status: approved, not yet implemented.

Addendum to `docs/superpowers/specs/2026-08-06-mcp-debug-server-design.md` (the base MCP debug
server, already implemented on `dev`). This adds two capabilities identified as gaps once the
base server was in use: separated mouse/key down-up (with a drag convenience and a safety net)
and the bridge's UDP event stream, neither of which the base server exposed.

## Purpose

The base server's action tools only expose combined down+up gestures (`left_click`,
`right_click`, `key_tap`) and the bridge's request/response state (`bridge_get`). Two real
capabilities of the underlying automation stack were left unreachable:

- `controller.py` has separate `mouse_left_down`/`mouse_left_up`,
  `mouse_right_down`/`mouse_right_up`, `key_down`/`key_up` primitives (used internally for
  things like holding an arrow key to rotate the camera) that the base server never wrapped —
  no held-key camera rotation, no click-and-drag, through MCP.
- The Java bridge pushes events over UDP (`PLAYER_DIED`, `NPC_DIED`, `LEVEL_UP`,
  `HP_CHANGED`, `NPC_TRACKED_ANIMATION_CHANGED`, `NPC_TRACKED_HITSPLAT`, etc. — see
  `controller.py`'s `poll_udp`/`handle_generic_events`) that `controller.py`'s own main loop
  consumes every tick. Nothing on the MCP side reads this stream at all.

## Non-goals

- Scroll wheel / camera zoom — doesn't exist anywhere in this stack yet, not even in the
  Arduino firmware's command vocabulary (`M`, `MLD/MLU/MLC`, `MRD/MRU/MRC`, `KT/KD/KU`). Out
  of scope for this addendum; would need firmware changes first.
- Higher-level bot behaviors (`click_entity`'s aim-jitter/UI-blocker-avoidance,
  `hover_entity`, `consume_first`, `open_inventory_tab`/`open_prayer_tab`, the camera-nudge
  helpers) — explicitly declined when this addendum was scoped. A debugging tool wants
  precise, low-level control; the bot's own decision-making logic stays out, same reasoning
  as the base spec's non-goals.
- Screenshot/visual capture — still doesn't exist anywhere in the codebase, Java or Python.
  Unchanged from the base spec's non-goals.

## Architecture

Both additions live in the same `ControllerAddon/mcp_debug_server.py` file the base server
already occupies — no new files. Held-state tracking is two new module-level sets
(`_held_buttons`, `_held_keys`); the event stream is a module-level `queue.Queue` plus one
daemon thread, both started at server startup alongside the existing serial-port open.

### Held-state tracking

`left_mouse_down()`/`left_mouse_up()`/`right_mouse_down()`/`right_mouse_up()` and
`key_down(key)`/`key_up(key)` each call the matching `controller.py` primitive, then update
`_held_buttons`/`_held_keys` to reflect what's now held. `release_all()` iterates whatever is
currently tracked, releases each one via the same primitives, clears the sets, and returns
what it released — a no-op returning empty lists if nothing was held. This is a safety net
for a forgotten down/up pair or an interrupted debugging session, not a guarantee against
every possible failure mode (e.g. it can't help if the whole process is killed) — a bounded,
honest safety net, not a promise the game can never end up with something stuck.

### drag

`drag(x1, y1, x2, y2, button="left")`: `button` must be `"left"` or `"right"` — anything else
returns `{"ok": false, "error": "button must be 'left' or 'right'"}` immediately, before any
window check or serial write. Otherwise: checks `_require_within_window` on both endpoints
before touching serial, moves to `(x1, y1)`, presses `button` down (via the matching
`_down` primitive, updating `_held_buttons`), moves precisely (`aim_radius=0`, matching the
base server's established precision convention for its other coordinate-taking tools) to
`(x2, y2)`, then releases in a `finally` block — so an exception raised mid-move (e.g. a
future `refresh_target` failure, or anything else `move_to_human` might raise) still releases
the button rather than leaving it stuck down.

### UDP event stream

At server startup, after the serial port opens: call `controller.make_udp_listener(UDP_PORT)`
(same `UDP_PORT` `controller.py`'s own main loop uses, from `settings.py`) and start one
daemon thread whose loop is:

```python
while True:
    for event_type, payload in controller.poll_udp(sock):
        _event_queue.put((event_type, payload))
    time.sleep(0.05)
```

Reusing `controller.poll_udp`'s existing non-blocking-drain-and-parse logic rather than
reimplementing UDP framing. `poll_events()` drains the queue non-blocking (`while not
_event_queue.empty(): ...`) and returns `{"events": [{"type": ..., "payload": ...}, ...]}` —
accumulating in a background thread rather than draining the OS socket buffer only at call
time means events aren't lost if `poll_events()` isn't called for a while between other tool
calls.

**Startup-failure handling differs from the serial port's.** The serial port is load-bearing
(every action tool needs it) and its base-spec behavior is to fail the whole server startup
loudly if it can't open. The UDP listener is additive — `bridge_get` and every click/key tool
work fine without it. If `make_udp_listener` fails (e.g. the port's already bound by a stray
process), the server still starts; the listener thread never starts either, and
`poll_events()` returns `{"error": "UDP listener not available: <reason>"}` instead of
silently returning an always-empty event list forever.

## Tools

- `left_mouse_down()` / `left_mouse_up()` / `right_mouse_down()` / `right_mouse_up()` — no
  args, act at the current cursor position, same convention as `left_click()`/`right_click()`.
  Each returns `{"ok": true}`.
- `key_down(key: str)` / `key_up(key: str)` — same key-name convention as the base server's
  `key_tap`. Each returns `{"ok": true}`.
- `drag(x1: int, y1: int, x2: int, y2: int, button: str = "left") -> dict` — `{"ok": true}` on
  success, or the `_require_within_window` error dict if either endpoint is out of bounds
  (checked before any serial write).
- `release_all() -> dict` — `{"ok": true, "released_buttons": [...], "released_keys": [...]}`.
- `poll_events() -> dict` — `{"events": [{"type": str, "payload": dict | None}, ...]}`, or
  `{"error": str}` if the UDP listener never started.

## Error handling

- Every coordinate-taking tool here (`drag`'s two endpoints) goes through the same
  `_require_within_window` gate as the base server's tools — no exceptions to that
  established rule.
- `drag`'s `finally`-block release means a mid-drag exception still leaves `_held_buttons`
  accurate and the physical button released, rather than compounding a bug with a stuck
  button.
- The UDP listener's startup failure is soft (documented above) — it must never prevent
  `bridge_get` or any action tool from working.

## Testing

Held-state tracking (`_held_buttons`/`_held_keys` bookkeeping) and `release_all`'s
iterate-and-clear logic are pure enough to unit test with the same `monkeypatch`-on-`controller`
approach the existing test suite already uses — no hardware needed. `drag`'s window-bounds
gate and its `finally`-release-on-exception behavior are unit-testable the same way (monkeypatch
`controller.move_to_human` to raise on the second call, assert the button still gets released).

The UDP event stream's background thread and real socket behavior are not meaningfully
unit-testable without either a real bridge or a fair amount of socket-mocking scaffolding that
isn't worth building for a debugging tool — verify live instead: with RuneLite running and
`controller.py` stopped, trigger a real event (e.g. take damage, log out) and confirm
`poll_events()` reports it. This mirrors the base spec's own live-verification-over-mocking
convention for anything that fundamentally depends on the live game/hardware.
