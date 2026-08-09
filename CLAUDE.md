# BridgePlugin

## What this is

A RuneLite (Old School RuneScape) automation plugin ("Controller") paired with a Python
controller that drives a physical Arduino-connected mouse/keyboard over serial — not
software-injected input, to avoid detection. The Java plugin exposes an HTTP + UDP bridge
reporting live game state; the Python side reads that state and decides what to click.

## Architecture

```
RuneLite client (Java plugin, src/main/java/com/example/)
  └─ HTTP bridge on localhost:8080 (BridgeHttpServer.java, providers/*Provider.java)
  └─ UDP event stream on port 50555 (PLAYER_DIED, NPC_DIED, LEVEL_UP, HP_CHANGED, CHAT_MESSAGE, ...)
        ↕
Python controller (ControllerAddon/)
  └─ controller.py     — engine: bridge polling, Arduino serial driving, panic key,
                          safety stops, human-movement realism (see settings.py)
  └─ routines/*.py     — one file per activity (slayer, mining, golem_crafting, ...);
                          controller.py loads whichever is passed on the command line
  └─ banker.py         — shared banking helpers (deposit, withdraw, pouches/bags)
  └─ travel.py         — shared walking helpers (Shortest Path plugin integration,
                          minimap/viewport hop-clicking)
  └─ login.py          — auto-login if the bridge reports logged out
  └─ debug_log.py      — the `[category] message` log lines seen in routine output
  └─ mcp_debug_server.py — exposes bridge/controller primitives as MCP tools (see .mcp.json),
                            for live interactive debugging outside a running routine
```

The Java side's `providers/` classes are the only thing that knows about RuneLite's internal
API; everything they expose becomes a JSON field on some bridge endpoint (`GET /player`,
`/objects`, `/inventory`, `/bank`, `/npcs`, `/combat`, `/ground-items`, `/menu`, ...). The Python
side never touches RuneLite internals directly — only these HTTP endpoints.

## Running things

- **RuneLite client**: `./gradlew.bat run` (from repo root). Launches via
  `ExamplePluginTest.main`, which loads the Controller plugin and starts the bridge.
- **A routine**: `python -u controller.py routines/<name>.py` (from `ControllerAddon/`). Runs
  until the panic key (F1) is pressed, a safety stop triggers, or the process is killed.
- **MCP debug server** (`osrs-debug` in `.mcp.json`): read-only bridge queries plus
  `move_mouse`/`click_entity`/`click_menu_entry` for live interactive debugging *without* a
  routine running — but it needs the same Arduino serial port `controller.py` does, so only one
  of the two can be running at a time (see the workflow below).
- **Tests**: `python -m pytest tests/` from `ControllerAddon/`.

## The routine contract

Every file in `routines/` is a plain Python module `controller.py` loads dynamically. It may
define (see `controller.py`'s own header comment for the authoritative version):

- `DEFAULT_CONFIG = {...}` — optional fallback config if the routine doesn't read from the
  bridge's `GET /config`.
- `get_config(bridge_config: dict | None) -> dict` — optional; resolves the config to use.
- `setup(ctx) -> None` — optional; runs once after the serial connection opens.
- `tick(ctx) -> None` — **required**; runs once per loop iteration, only while logged in,
  focused, no safety stop active, and not on a break.
- `on_event(ctx, event_type: str, payload: dict | None) -> None` — optional; runs for every UDP
  event regardless of whether `tick` is currently running.

`ctx.state.data` is a plain dict for a routine's own bookkeeping (current target, phase, etc.) —
**it resets to `{}`** whenever the routine file changes or the process restarts. It is NOT
persisted across restarts; a routine's own in-game progress (mid-craft state, partially-filled
inventory, etc.) very much is, so the two can desync after a restart — see the golem_crafting.py
notes below for a real example of this causing a bug.

## Known environment gotchas

- **Windows HiDPI scaling**: RuneLite's own AWT windows report `screenX`/`screenY` at 96 DPI
  under a bare `java.exe` launch (no DPI-aware manifest), regardless of the display's actual
  scaling. `controller.py` compensates by scaling every bridge response through
  `get_dpi_scale()`/`_scale_screen_coords()`. If clicks are consistently off by a fixed ratio,
  suspect this before anything else.
- **Simulated human "mistakes" are intentional, not bugs.** `controller.click_entity` has a
  `MISTAKE_MISCLICK_CHANCE` (~4.5%, see `settings.py`) chance of deliberately aiming beside the
  target instead of on it, logged as `[!] Misclick -- aimed beside X instead of on it`. This is
  anti-detection humanization, not a targeting bug — code that reacts to a click's outcome must
  handle this (see golem_crafting.py's mining reclick gate for an example of a bug this caused:
  waiting forever for an inventory change that a misclick will never produce).
- **Arduino / MCP server / controller.py serial port conflict**: only one process can hold the
  COM port at a time. Read-only bridge HTTP queries don't need it and work regardless.
- **`ctx.state.data` reset on restart** (see above) can desync from real in-game state that
  persists — always consider whether a routine needs to reconcile against a live bridge read
  (e.g. an object's actual name) rather than trusting its own resumed bookkeeping.
- **Always shift+click (`key_down`/`key_up` "SHIFT" around the click) when walking to a plain
  ground tile, never a bare click.** A ground point close to any interactive object can resolve
  to that OBJECT's own action instead of walking there if their clickable areas overlap on
  screen — CONFIRMED live (golem_crafting.py's hunt_phase, 2026-08-09): a plain click near the
  Goat Pit while walking back to a standing tile cleared/unlined it instead of just walking.
  Shift+click is OSRS's own standard force-walk shortcut (forces "Walk here" regardless of
  what's under the cursor) and looks like any other single click, so there's no reason not to
  use it for every walk-to-a-tile click, not just ones near a known-risky object. See
  `golem_crafting.py`'s `walk_to_exact_tile` for the reference implementation.

## Live-debugging workflow

Use this loop by default when debugging a routine live against the running RuneLite client — it
was confirmed to work well over an hours-long session that took `golem_crafting.py` from broken
to reliably running unattended.

1. **Check for a serial-port conflict first.** Confirm no stray process still holds the Arduino:
   `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"`.
2. **Launch the routine in the background, unbuffered, logged to a file:**
   `python -u controller.py routines/<name>.py *> <logfile> 2>&1`.
3. **Poll the log with `Read`, not tight loops.** 20-90s gaps between checks are fine — this is a
   live session, not a race.
4. **Trust the user's live observations over your own read of the log.** If the log looks clean
   to you but the user reports something wrong, keep digging for the actual mechanism rather than
   concluding it's already fixed.
5. **Before every relaunch: `py_compile` every touched file, then run the test suite.** Pytest
   alone is not sufficient — some modules (e.g. `banker.py`) aren't imported by any test, so a
   syntax error there won't surface in `pytest`, only in a direct compile check.
6. **After relaunching, actually watch it work through the specific phase that was broken.**
   Compiling clean and "the reasoning sounds right" are not verification.
7. **Iterate in small, targeted fixes**, one reported symptom at a time, rather than large
   rewrites.

**Distinguish infra/hardware failures from code bugs**: a `serial.SerialException` crash mid-run
can mean the Arduino has dropped off the USB bus entirely, not a code bug. Check
`[System.IO.Ports.SerialPort]::getportnames()` (empty = gone) and `Get-PnpDevice -Class Ports`
(look for "Status: Unknown" on the Arduino Leonardo) before assuming it's a logic bug. A
reseat/power-cycle fixes this — no amount of code changes will.

**Before any live click/mouse debugging**, verify: RuneLite window is focused (`GET /state` →
`windowFocused`), Windows mouse acceleration ("Enhance pointer precision") is off, and the
process is DPI-aware (already handled by `mcp_debug_server.py`/`controller.py` at import time).

## Current state of routines/golem_crafting.py

As of the last session, this routine was extensively live-debugged and left in a working state:
mines Sunstone rocks to a budget, chisels cores, crafts golems on either of Wyrmscraig's two
plinths (tracked by object id, not position — see that file's own comments for why), inserts
cores/fur, loots gems, and cycles bank ↔ mine ↔ craft on its own. Several other routines
(`slayer.py`, `mining.py`, `blast_furnace.py`, etc.) exist and were not touched this session —
don't assume they share golem_crafting.py's specific fixes (sticky targeting, inventory-change
reclick gates, etc.) unless verified.
