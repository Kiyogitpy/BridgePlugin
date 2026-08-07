# MCP debug server — design

Status: approved, not yet implemented.

## Purpose

Give Claude a first-class way to live-debug this bot against a running RuneLite client: query bridge state and perform mouse/keyboard actions through typed MCP tools, instead of ad hoc `curl`/`WebFetch` calls and one-off Python verification scripts. Builds directly on the autonomous-debugging loop AGENTS.md already documents (right-click -> poll `/menu` -> click a matched entry) and the `debug_log.py` work from the previous session — this is the next piece toward debugging without relaying screen contents or clicking things by hand.

## Non-goals

- Log tailing / process management (starting or stopping `controller.py`, tailing `debug_log.py` output) — explicitly out of scope for this iteration. May be revisited later.
- Screenshot/visual verification — a separate, later piece toward the same autonomous-debugging goal (actual screen vision, not local data, is the remaining blocker there, not this).
- Sharing the serial port with `controller.py` while it's running. The two are mutually exclusive by design (see below), not something this iteration tries to solve.
- Human-like mistake simulation (misclicks, idle drift, breaks). Those exist in `controller.py` to make the *bot* look human during real automation; a debugging tool wants precise, predictable actions instead.

## Architecture

A new standalone script, `ControllerAddon/mcp_debug_server.py`, speaking MCP over stdio, registered as a project-level server in a new `.mcp.json` at the repo root (committed — this is a single-developer repo, and `.mcp.json` only names the command to launch, not machine-specific values). Registering it there means it's available automatically in this and future Claude Code sessions with no per-session setup.

The server does `import controller` and reuses its existing helpers rather than reimplementing them:

- `controller.api_get` for the HTTP GET plumbing to the Java bridge (`API_BASE` from `settings.py`, currently `http://localhost:8080`)
- `controller.set_dpi_aware`, `controller.get_cursor_pos` for the Windows DPI/cursor primitives
- `controller.move_to_human`, `controller.click_human`, `controller.right_click_human`, `controller.key_tap`, `controller.type_text` for action primitives
- `controller.get_menu`, `controller.is_point_within_window` for the menu-polling and click-safety checks

Importing `controller.py` as a module is safe: everything stateful (opening the serial port, starting the panic-key listener thread) happens inside `main()`, guarded by `if __name__ == "__main__":`, which the MCP server never calls. The module-level code only defines functions/classes and reads `settings.py`.

### Serial port ownership

At server startup: call `controller.set_dpi_aware()` once (must happen before any cursor-position query, same reasoning as `controller.py`'s own `main()`), then open `serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)` and hold it for the process's lifetime.

**`controller.py` must not be running at the same time as this server** — both want exclusive access to the same COM port, and only one process can hold it. This is a deliberate, accepted constraint (see the brainstorming discussion): the existing workflow already stops the bot before running one-off verification scripts, so this doesn't change the operator's workflow, it just replaces the one-off script with a persistent, reusable tool.

If opening the serial port fails (e.g. `SerialException`, most likely because `controller.py` is already holding it), the server must fail its startup with a clear, actionable message — not a raw pyserial traceback — e.g. `"Could not open {SERIAL_PORT}: is controller.py already running? Stop it before starting the debug MCP server."` This should surface at server-start time, not lazily on the first action tool call, so a failed launch is immediately obvious rather than surfacing as a confusing error on whatever tool call happens to run first.

## Tools

### State (read-only)

**`bridge_get(path: str, params: dict | None = None) -> dict`**

Generic passthrough: `GET {API_BASE}{path}` with `params`, JSON-decoded and returned. Works for every current endpoint (`/player`, `/state`, `/menu`, `/npcs`, `/objects`, `/inventory`, `/bank`, `/sailing`, `/equipment`, `/config`, `/npc/nearest`, `/npc/by-index`, etc. — see every `server.register("/...", ...)` call across `src/main/java/com/example/providers/*.java` for the authoritative list) and any future one added on the Java side, with zero MCP-server code changes.

Deliberately does **not** reuse `controller.api_get`'s failure handling as-is: that function silently swallows exceptions and returns `None` (turned into `{}`/`[]` by each `get_*()` wrapper) because a running bot needs to degrade gracefully tick over tick. A debugging tool wants the opposite — a failed/timed-out request should come back as a visible error, e.g. `{"error": "<exception message>"}`, so a bridge outage is immediately obvious rather than looking like "the endpoint returned empty."

Implementation: a small local wrapper around `requests.get` (not `controller.api_get`) that catches exceptions and turns them into that `{"error": ...}` shape, plus non-2xx status codes into the same shape with the status code included.

### Actions

All actions operate through the serial connection opened at startup, using `controller`'s existing human-like-movement primitives (Bezier path, easing, jitter) — same visual motion as the bot's real automation, just triggered on demand instead of by a routine's `tick()`.

- **`get_cursor_position() -> {"x": int, "y": int}`** — wraps `controller.get_cursor_pos()`. Useful to confirm where the cursor actually is before/after an action, independent of anything else.
- **`move_mouse(x: int, y: int) -> {"ok": bool, "error"?: str}`** — `controller.move_to_human(ser, x, y)`. Checks `controller.is_point_within_window(x, y)` first; if false, returns an error instead of moving, same reasoning `click_entity` already applies.
- **`left_click() -> {"ok": true}`** / **`right_click() -> {"ok": true}`** — click at the current cursor position (`controller.click_human` / `controller.right_click_human`). No target check (nothing to check — the cursor's already wherever it is).
- **`left_click_at(x: int, y: int) -> {"ok": bool, "error"?: str}`** / **`right_click_at(x: int, y: int) -> {"ok": bool, "error"?: str}`** — `move_mouse` then the corresponding click, as one call. Same window-bounds check as `move_mouse`, applied before moving.
- **`key_tap(key: str) -> {"ok": true}`** — `controller.key_tap(ser, key)`.
- **`type_text(text: str, press_enter: bool = False) -> {"ok": true}`** — taps each character via `controller.key_tap` (spaces mapped to `"SPACE"`, matching `controller.type_text`'s existing behavior); if `press_enter` is true, taps `"ENTER"` afterward too (covers both `controller.py`'s existing `type_text` and `type_digits_and_enter` use cases with one tool instead of two near-duplicate ones).
- **`click_menu_entry(x: int, y: int, option: str, target_hint: str | None = None) -> {"ok": bool, "matched"?: {...}, "available_options"?: [str], "error"?: str}`** — the high-level loop: window-bounds check, right-click `(x, y)`, poll `GET /menu` until `open` or a timeout (same `MENU_POLL_INTERVAL_S`/`MENU_POLL_TIMEOUT_S` constants as `controller.click_menu_action`), match an entry by case-insensitive substring on `option` (and `target_hint` against the entry's `target`, if given), click the match. On no match or a menu that never opened, dismiss it the same way `controller.click_menu_action` does (move off it + `ESC`) and return `ok: false` with `available_options` listing what *was* on the menu, so a failed match is diagnosable instead of a bare `false`.

Every action tool logs through `debug_log.log(...)` the same way `controller.py`'s own action functions do (they already do this internally, e.g. `click_human` logs `"action"`/`click_human at {pos}`) — no extra work needed, this falls out of reusing the same functions.

## Error handling

- Serial-port-busy at startup: fail loudly with the actionable message above (see "Serial port ownership").
- Bridge unreachable (`bridge_get` and any action tool that internally polls `/menu`): surfaced as `{"error": ...}` rather than swallowed.
- Click/move targets outside the RuneLite window: rejected before any serial write, via `controller.is_point_within_window`, returning an error rather than clicking blind onto the desktop or another window.
- No panic-key/emergency-stop mechanism is added for this server. `controller.py`'s F1 panic key exists to interrupt an unattended automated routine mid-tick; this server only ever acts on an explicit tool call the operator is watching happen, so that failure mode doesn't apply here.

## Setup / registration

- Add `mcp` (the official Python MCP SDK) as a new dependency — no `requirements.txt` exists in this repo today (dependencies are installed directly into the system Python: `pyserial`, `requests`, `keyboard`, `pywin32`), so this follows the same pattern: `pip install mcp`, documented in a short comment at the top of `mcp_debug_server.py` alongside its other prerequisites, rather than introducing a new dependency-management convention as a side effect of this change.
- New `.mcp.json` at the repo root registering the server (command: `python`, args: `["ControllerAddon/mcp_debug_server.py"]`, or equivalent `uv`/venv invocation if one gets introduced later — not currently the case here).
- Document the "`controller.py` must be stopped first" constraint in this new file's own module docstring, not just this spec — so it's discoverable without having read the design doc.

## Testing

No unit tests — this is inherently a live-hardware, live-game-client tool; nothing here is meaningfully testable without both running. Verification plan, in order:

1. Run `python ControllerAddon/mcp_debug_server.py` directly at a terminal first (not yet wired into `.mcp.json`) to catch import errors or a serial-open failure immediately, with a clear stack trace to work from.
2. With RuneLite + the plugin running and `controller.py` **not** running, register the server in `.mcp.json` and confirm Claude Code lists its tools.
3. Call `bridge_get(path="/state")` first — read-only, no hardware side effects, confirms the Java-bridge round trip alone works before touching anything that moves the mouse.
4. Call `move_mouse` to a known-safe on-canvas point and visually confirm the cursor actually moves there.
5. Call `click_menu_entry` against a known right-clickable entity (e.g. an inventory item) end-to-end, confirming a real menu opens, the right entry gets matched, and the click lands — the same live-verification bar AGENTS.md already holds the underlying primitives to (its two documented bugs — the canvas/screen coordinate conversion and the ~22px header band — were only caught this way, not by reading the code).

This mirrors this repo's existing "verify live, not just from reading the code" convention rather than introducing a new testing philosophy for this one piece.
