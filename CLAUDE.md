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
- **MCP debug server** (`osrs-debug` in `.mcp.json`): read-only bridge queries
  (`bridge_get`/`get_cursor_position`/`poll_events`) only — no mouse/keyboard action tools, so it
  never touches the Arduino serial port and can stay connected for the whole session, including
  while `controller.py` is running. (Action tools were removed 2026-08-12; if live click/mouse
  debugging is needed again, drive it through `controller.py` itself.)
- **Tests**: `python -m pytest tests/` from `ControllerAddon/`.
- **Looking at what the client is actually drawing** (added 2026-08-21): `GET /screenshot?name=<label>`
  dumps the current rendered frame to `~/.runelite/bridge-screenshots/` and returns its absolute
  path plus every coordinate-space measurement needed to interpret it (`imageWidth/Height`,
  `realWidth/Height`, `stretchedWidth/Height`, `canvasWidth/Height`, `canvasScreenX/Y`,
  `stretchScaleX/Y`, `gameState`, `loginIndex`). Read the PNG straight off disk. `GET
  /widgets/dump` lists every visible widget with ids, text, actions, and both canvas and screen
  bounds; `?scan=true` brute-forces `getWidget(group, child)` over a bounded id range instead of
  walking `getWidgetRoots()`. **Use both before concluding a screen "isn't a widget"** — a root
  that isn't attached to the root list is invisible to the walk but still resolves by id, so the
  walk alone is not proof. Both are read-only and cost nothing when unused. NumPy and Pillow are
  already installed, so measuring a dumped frame needs no new dependency.

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
- `before_break(ctx) -> None` — optional; runs once on the tick a break first comes due, **before
  the break starts**, under the same gates `tick` runs under. Added 2026-08-20 (user requirement
  for `routines/chinchompa_hunting.py`: "make it so it picks up the trap before the break
  function") because breaks are toggled *before* `tick` and `tick` is skipped for the whole
  break, so a routine otherwise physically cannot clean up after one begins — four laid box traps
  would sit out a 3-12 minute break unattended. The break starts once this returns, so a slow
  teardown just delays it. Backed by the side-effect-free predicate `ControllerState.break_due()`;
  routines without the hook are unaffected. There is **no** equivalent hook for a safety stop or
  for process exit (the panic key is a hard `os._exit` by design) — only breaks are covered.
- `SKIP_STRAY_DIALOG_CHECK = True` — optional module-level flag; opts out of the engine-level
  `check_stray_dialog` (dismisses a "click here to continue" dialogue via Space before every
  tick). Added 2026-08-14 for `routines/pest_control.py`: that check only presses Space once per
  tick, so a multi-stage dialogue only advances one stage per call instead of clearing in one
  shot — fine for a routine whose own state detection tolerates a lingering dialogue, not useful
  otherwise. Off (check runs) by default for every other routine.

`ctx.state.data` is a plain dict for a routine's own bookkeeping (current target, phase, etc.) —
**it resets to `{}`** whenever the routine file changes or the process restarts. It is NOT
persisted across restarts; a routine's own in-game progress (mid-craft state, partially-filled
inventory, etc.) very much is, so the two can desync after a restart — see the golem_crafting.py
notes below for a real example of this causing a bug.

**Wiring a new routine into the GUI is a required step, not optional polish.** A routine file
alone is only launchable via `python controller.py routines/<name>.py --routine <NAME>` from the
command line — CONFIRMED live 2026-08-14 (`pest_control.py` shipped without this and could only
be started that way). To make it selectable from the RuneLite plugin's own Controller Settings
panel (so `controllerEnabled` + picking it from the Routine dropdown is enough, no command line
needed), both of these are required, and need a client restart (Java-side change) to take effect:
1. Add an entry to the `RoutineChoice` enum
   (`src/main/java/com/example/controller/RoutineChoice.java`).
2. Add a matching key to `ROUTINE_FILES` in `controller.py` (the enum name, e.g. `PEST_CONTROL`,
   mapping to `"routines/pest_control.py"`).

Only add a dedicated `ControllerConfig.java` toggle/section on top of this (see the "Secondary
switch on top of launching controller.py with routines/X.py" entries for `golem_crafting.py`,
`gemstone_crab.py`, etc.) if the routine actually reads its config from the bridge's `GET
/config` — a routine using a plain `DEFAULT_CONFIG`/`get_config` that ignores `bridge_config`
(same pattern as `gemstone_crab.py`, `mining.py`, `pest_control.py`) needs nothing beyond the two
steps above.

## Known environment gotchas

- **Instanced areas break forward world-coordinate conversion — check for this first in any new
  provider that reads or writes a `WorldPoint`/`LocalPoint`.** Hit four separate times so far
  (`PlayerProvider`, `NPCProvider`, `ObjectProvider`, `GroundItemProvider`, all fixed 2026-08-14):
  reading a position *out* of the client (`LocalPoint` → real-world `WorldPoint`) needs
  `WorldPoint.fromLocalInstance()`, not `RuneLiteUtil.toTopLevelWorldPoint`/
  `getTopLevelWorldLocation`/a bare `tile.getWorldLocation()` alone (none of those handle classic
  instance-template chunk remapping, only (at best) a nested-WorldView case like a Sailing boat)
  — CONFIRMED live via Pest Control (an instanced arena) reporting every x/y in the ~13000s
  instead of the real ~2600s map coordinates. Going the *other* direction (a known real-world
  tile → a clickable screen position, e.g. `CombatProvider`'s `/minimap/walk`/`/ground/walk`,
  both built on `LocalPoint.fromWorld()`) is **not fixed and not easily fixable the same way** —
  CONFIRMED live these fail to resolve even the player's own current tile while inside an
  instance. `pest_control.py` works around this entirely by never doing forward world-coordinate
  resolution inside an instanced arena: it clicks a hardcoded absolute screen pixel on the
  minimap instead (see that routine's own header comment), and `/object/at`'s and
  `/ground-item/at`'s designs both deliberately filter the already-corrected bulk `/objects`/
  `/ground-items` lists rather than attempting a fresh forward lookup (see their own doc
  comments). If a future routine needs to click a known world tile while inside an instance and
  neither of those patterns fits, that forward-direction fix is still an open problem, not a
  solved one. `/players` (`PvpProvider`) hasn't been checked for this bug yet — assume it has the
  same gap until verified, same as every other provider not in the four already fixed.
- **Windows HiDPI scaling**: RuneLite's own AWT windows report `screenX`/`screenY` at 96 DPI
  under a bare `java.exe` launch (no DPI-aware manifest), regardless of the display's actual
  scaling. `controller.py` compensates by scaling every bridge response through
  `get_dpi_scale()`/`_scale_screen_coords()`. If clicks are consistently off by a fixed ratio,
  suspect this before anything else.
- **Two different pixel spaces, and mixing them costs you the stretch factor.** Widget bounds
  (`Widget.getBounds()`) and everything `Perspective` projects are in the game's **real**
  (pre-stretch) render resolution — `client.getRealDimensions()` — and must go through
  `RuneLiteUtil.canvasToScreen`, which applies `getStretchScale()` before adding the canvas's
  screen origin. Frames captured via `DrawManager` (`GET /screenshot`) are the opposite: they
  come back in **post-stretch canvas** space, the same size as `canvas.getWidth()/getHeight()`,
  so a pixel measured off a dumped frame maps to screen by adding `canvasScreenX/Y` and nothing
  else. Passing a frame-measured pixel to `canvasToScreen` scales it a second time — a live 2.06x
  error in the geometry this was confirmed in. `GET /screenshot` reports `imageWidth/Height`
  alongside both `realWidth/Height` and `stretchedWidth/Height` specifically so this can be
  settled by comparison instead of assumption.
- **The OSRS login screen has a layout rule; it is not a fraction of the canvas.** The login UI
  is drawn at a **fixed pixel size in real (pre-stretch) render space, horizontally centred and
  top-anchored** — so a button is at `(realWidth / 2, <fixed y>)`, fed to `canvasToScreen`. This
  was measured 2026-08-21 across four geometries (aspect 1.76 → 4.91, stretch 2.06x → none): the
  "Play Now" text top stayed at real y 241.1 ± 0.1 throughout, and a vertically-centred layout
  would have shifted it by 82px. It replaced a fraction of the canvas's *stretched* size, which
  was confirmed broken live — a window resize moved the reported point ~80px off target. Two
  things defeat a canvas fraction: the login art is letterboxed at some window sizes (142px bars
  each side at 2529x1377, none at 1202x682), and the UI doesn't scale with the canvas at all.
  See `LoginProvider.java` for the derivation table and the known `loginIndex` values (10 = Play
  Now, 24 = message dialog with an "Ok" button, e.g. after a disconnect). **These screens are
  genuinely not widgets** — re-confirmed against the current client by both dump modes — but the
  screens immediately after them are (CLICK HERE TO PLAY is widget 378.77), so check before
  assuming either way.
- **Simulated human "mistakes" are intentional, not bugs.** `controller.click_entity` has a
  `MISTAKE_MISCLICK_CHANCE` (~4.5%, see `settings.py`) chance of deliberately aiming beside the
  target instead of on it, logged as `[!] Misclick -- aimed beside X instead of on it`. This is
  anti-detection humanization, not a targeting bug — code that reacts to a click's outcome must
  handle this (see golem_crafting.py's mining reclick gate for an example of a bug this caused:
  waiting forever for an inventory change that a misclick will never produce).
- **Arduino serial port**: only one process can hold the COM port at a time. The MCP debug
  server no longer touches it at all (read-only bridge queries only, see above) — the only real
  contender is a stray/leftover `controller.py` process.
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
- **A Shortest Path route is only useful if we can follow it — never inherit that plugin's own
  panel settings.** `_use_transport` can do exactly one thing: click a single menu option on an
  object or NPC. Every teleport, and every transport that opens a destination picker (canoes,
  charter ships, minecarts), produces an edge with no `objectId` to click — and before 2026-08-21
  travel.py reacted to that by trying to *walk* to the tile on the far side. CONFIRMED live that
  day: a `walk_to` from Civitas illa Fortis returned a route whose first edge was a
  `QUETZAL_WHISTLE` to (1697, 3140), i.e. a minimap click at a tile **454 tiles away**, purely
  because the user's own Shortest Path panel had quetzals enabled. `travel.TRANSPORT_POLICY` is
  now sent in full with every request and disables everything we can't execute; the same target
  went from a 90-step route with an undriveable teleport to a 526-step walkable one whose only
  two transports were an ordinary boat and a door. Add a key to that policy — don't loosen it —
  when new execution code lands, and see `_transport_is_executable` for the second line of
  defence.
- **Shortest Path's config override is sticky AND session-global.** The `config` map on its
  `("shortestpath", "path")` PluginMessage is copied into a *static* field that the plugin only
  ever replaces (when a non-empty map arrives) and never clears. Two consequences: a partial
  override from one request silently stays in force for every later one — which is why
  `travel.transport_config()` sends the whole policy every time rather than just the keys it
  wants changed — and the override also shadows the human's own manual use of the plugin for the
  rest of the client session. Restarting the client is currently the only way to clear it. If
  that ever needs fixing, the shape would be having the bridge snapshot the real config values
  through `ConfigManager` once and restore them; nothing does that today.
- **A finished path is not necessarily *your* path, and `walk_to` doesn't always ask for one.**
  Shortest Path swaps its `Pathfinder` in asynchronously after the message, so a poll landing in
  between reads the *previous* walk's finished route — complete, `done`, and to somewhere else
  entirely. `/shortest-path/result` therefore echoes the running pathfinder's own `start` and
  `targets`, and `_request_computed_path` only accepts a result whose targets match what it asked
  for (a build too old to report them degrades to the old done-is-enough behaviour). It also
  returns `reached`/`terminationReason` (`TARGET_REACHED` / `SEARCH_EXHAUSTED` / `CUTOFF_REACHED`
  / `CANCELLED`), because the plugin hands back its best *partial* route when it can't arrive and
  that is otherwise indistinguishable on the wire from one that does. Separately, `walk_to` skips
  the pathfinder entirely for same-plane targets within `SHORT_HOP_TILES` (25) and goes straight
  to `_walk_greedy` — so "it never called Shortest Path" is expected for a short hop, not a bug.
  Pass `min_path_tiles=0` to force a computed route anyway. Deliberately not a large threshold:
  `_walk_greedy` is obstacle-blind and every extra tile of gate is another chance to wedge
  against a wall it can't route around.

## Sub-tick data and clicking while moving

Most game state is genuinely capped at OSRS's own 0.6s server tick — HP, true tile position,
target switches, which animation is playing — no client-side trick gets around that; the
information doesn't exist any faster server-side no matter how it's read. But on-screen
*position* data is not tick-capped: a moving actor's `LocalPoint` is smoothly interpolated by
the client every render frame, and RuneLite's `ClientTick` event (fires once per frame, not once
per game tick) is the general mechanism for reading that. This codebase's only current use of it
is `NpcTrackerRegistry.onClientTick`'s animation-*frame* diffing (catches a repeating attack
animation restarting even when the animation *id* itself never changes between reps — a
`GameTick`-only check would structurally miss this, not just see it late).

On the bridge side, only 6 providers (`NPCProvider`, `ObjectProvider`, `TileProvider`,
`GroundItemProvider`, `GraphicsObjectProvider`, `BankProvider`) are gated by `TickCache` — a
deliberate perf optimization for the bulk list endpoints (`/npcs`, `/objects`, ...), not a
platform limitation, and it already has a `?fresh=true` bypass. Single-entity lookups
(`/npc/by-index`, `/npc/nearest`, `/object/at`, `/player`, `/combat`) were never cached at all —
every poll already reads the client's current interpolated position fresh, however often you
ask.

This is why routines gate clicks behind `wait_until_player_idle()` (wait for the player to fully
stop before trusting a resolved screen position) rather than clicking while moving: most click
call sites resolve a screen position once and never re-check it, not because the position data
itself is too slow to get. `move_to_human`/`click_entity` already had a `refresh_target`
parameter for this (a zero-arg callable re-resolving a target's current screen position — used
by `slayer.py`/`tormented_demon.py`/`golem_crafting.py`, each hand-writing its own lambda) but it
only re-aimed once, partway through a flight. `refresh_legs` (added 2026-08-14, purely additive —
`None`/default reproduces every existing caller's exact prior behavior bit-for-bit) generalizes
this into multiple re-aim checkpoints toward interpolated waypoints (only the final leg lands
exactly on the target; earlier legs travel a fraction of the remaining distance, so a re-aim
mid-flight can still correct the approach), specifically so a routine could eventually click
without waiting for full stillness first. Reviewed via an independent second pass that caught
and fixed two real bugs in the first draft (leg 0 was flying the *entire* distance to a stale
snapshot before ever re-aiming, and per-leg jitter was being redrawn instead of fixed, adding
real wasted motion even for a stationary target) — see `_move_to_human_tracking`'s own docstring
for the full account. Covered by unit tests in `tests/test_controller.py`, but **not yet
exercised against a live client**, and no routine has adopted it yet as of this note. See
`npc_index_refresh_target`/`ground_tile_refresh_target`/`object_refresh_target`/
`ground_item_refresh_target` (the last two added 2026-08-14 alongside `/object/at` and
`/ground-item/at`, the object/ground-item equivalents of `/npc/by-index` — see "Instanced areas
break forward world-coordinate conversion" above for why both are implemented by filtering the
already-corrected bulk `/objects`/`/ground-items` lists rather than a fresh forward lookup) for
ready-made `refresh_target` factories instead of hand-writing the lambda at each call site.

**XP gain is another sub-tick, general-purpose signal — not specific to any one routine.** Added
2026-08-17 (user directive, generalized from `pyramid_plunder.py`'s tomb-door confirmation):
`client.getSkillExperience(Skill.X)` reflects the moment the client's own `StatChanged` event
fires — this is the exact signal the game's own XP Drop plugin uses
(`data/decompiled/.../xpdrop/XpDropPlugin.java#onStatChanged`) — and is not gated by `TickCache`
or the 0.6s server tick the way bulk `/objects`/`/npcs` scans or `roomNumber`-style state are.
Any skilling action that grants XP on success (thieving, mining, fishing, crafting, ...) can use
this as a fast, definitive "did that attempt actually succeed" check instead of polling chat
messages, waiting out a fixed delay, or re-reading tick-capped object/state fields.

`PlayerProvider` exposes each skill's cumulative XP the same way (`hitpointsXp` was the existing
precedent; `thievingXp` was added alongside this note) — add more `data.put("<skill>Xp",
client.getSkillExperience(Skill.<SKILL>))` lines there for whichever skill a routine needs, same
pattern. **Requires a RuneLite client rebuild + restart** (`./gradlew.bat run`) before a newly
added skill's XP field shows up on `GET /player` — it's compiled into the Java plugin, not
something the Python side can pick up on its own.

Python-side usage pattern (see `pyramid_plunder.py`'s `attempt_door_click`): snapshot
`controller.get_player().get("<skill>Xp")` immediately before the click, then poll for an
increase inside the same short wait loop already used for other post-click signals (position
jump, confirmation chat message, ...) — an XP increase is proof the underlying skill check
succeeded, sub-tick and often faster than any other available signal.

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

## Current state of routines/chinchompa_hunting.py

Written 2026-08-20 (design: `docs/superpowers/specs/2026-08-20-chinchompa-hunting-design.md`).
Box-traps Carnivorous chinchompas with 4 traps and picks every trap back up via the new
`before_break` hook. First run live 2026-08-21 in the Red chinchompa hunting ground (an
underground cave, and **not** instanced — forward world→screen conversion works there, so
`walk_to_exact_tile` is safe; verified before the first run). That session replaced most of the
guesswork in this routine with measurements:

- **Interacting with a trap is a plain left-click, not a menu selection.** The user swapped the
  client-side menu entries so the left-click action is correct in every trap state, so
  `click_menu_action`/`target_hint`/`TRAP_STATE_ACTIONS` are gone. The old `target_hint` guess was
  wrong anyway: a trap's **displayed name depends on its state** — a full one is a `Shaking box`,
  a set or collapsed one is a `Box trap` — so it missed on every single retrieval and only worked
  via the option-name-only fallback. Nothing matches on the name any more; identity is by object
  id (`classify_trap`).
- **A trap object appears in `/objects` ~1.9s AFTER the trap leaves the inventory** (≈3 game
  ticks, the setup animation), measured over three consecutive lays. Confirming a lay on the pack
  decrement alone — which is what `_await_trap_laid` used to do — hands control back inside that
  window, the next tick reads the tile as still empty, and the routine lays a *second* trap on it.
  See `_await_trap_laid`; it now waits for the object and nothing else.
- **Checking a full trap is TWO animations with a deceptive ~0.66s idle gap between them:** 5212
  (check / pick up) then 5208 (the game re-laying it automatically), ~6s end to end. `animation
  == -1` is therefore NOT a valid "action finished" signal on its own — only *sustained* idle is
  (`_await_action_finished`, `ACTION_SETTLE_S`). Returning during that gap let the next tick
  interrupt the re-lay, so the trap stayed in the pack and the tile stayed bare; over a few
  catches every trap ended up in the inventory with nothing on the ground.
- **A left-click keeps a *set* trap** (picks it up, no re-lay) but *re-lays* a full or collapsed
  one. That asymmetry is why `retrieve_trap` confirms on "the tile no longer holds the state we
  clicked on" rather than on the trap reaching the inventory — only one of the two outcomes
  involves the inventory at all — and it is what lets `before_break` still clear the ground.
- **An unreachable plan tile used to be retried forever, and it was the single biggest throughput
  drain found.** `block_tile` keys off the game's refusal chat message, which never arrives when
  the player cannot physically get onto the tile to attempt the lay — so `lay_trap` just logged
  "didn't reach" and gave up, every tick, at ~10s an action. CONFIRMED live 2026-08-21: two plan
  tiles were walls, burning ~200 actions an hour and holding the catch rate at roughly half
  baseline. `lay_trap` now counts consecutive failed walks per tile and blocks after
  `UNREACHABLE_BLOCK_THRESHOLD` (4), and the plan rebuilds onto a spare offset; catch gaps went
  from 28-96s back to ~10s immediately. The threshold is deliberately not 1 — a chinchompa
  standing on a tile blocks the walk transiently — and arriving clears the streak. Root cause of
  the bad geometry: repeated restarts let the plan adopt scattered leftover traps until the
  anchor drifted somewhere with walls for neighbours, so a **clean start from a deliberate anchor
  is worth doing** after a run of restarts.
- **Known, deliberately unfixed: collapsed traps sometimes time out before they are reset.**
  `work_queue` ranks `failed` last, so a collapsed trap waits behind catches, dropped-trap
  recoveries and empty slots; some wait long enough to time out and drop as an item, which then
  costs two actions (recover + re-lay) instead of one (reset). MEASURED 2026-08-21 over 115
  consecutive actions: 65 catches, 33 resets, 9 lays, 8 recoveries — so the waste is ~7%.
  Promoting `failed` above `empty` would recover most of it, **but** `full` + `failed` already
  account for 98 of those 115 actions, which is exactly the condition that starved the empty slot
  before (12 catches on 3 traps of a 4-trap plan). Left alone on purpose: a 7% loss is not worth
  re-risking a whole trap slot, and both previous reorderings made on reasoning rather than
  measurement regressed throughput 45-85%. Measure over a long run before touching this.
- **Do not try to reclaim the animation tail — it was measured and is strictly worse.** The
  obvious speed idea is that once the re-laid trap object is back on the ground (~1.3s before the
  animation ends) it has already left the pack, so the re-lay is committed and the next action
  can start. Tried 2026-08-21: the catch cycle went from ~9.7s to a **median of 18s**, with 6
  dropped retrieve clicks and 7 lay timeouts where the full wait had none. The trap being placed
  is not the same as the player being free — a click issued while the player is still animating
  is dropped by the game, and the retries cost far more than the wait saves. Throughput here is
  bound by the game's own ~6s two-part animation, and the routine is already saturated (~0.5s
  idle between actions), so there is very little left to win.
- **Traps left on the ground time out and despawn**, dropping as a `Box trap` *ground item*
  (`/ground-items`, not `/objects`) — MEASURED as a steady leak of ~1-2 traps an hour, since a
  set trap that never catches anything is never touched and so eventually times out. Recovered by
  `dropped_traps_by_tile`/`recover_dropped_trap`, which pick the item back up rather than paving
  the tile with a fresh trap from the pack. That pickup goes through the right-click menu (`Take
  Box trap`) and **must not** be "simplified" to a bare left-click: that was tried first and
  failed on its first live attempt (the item despawned instead), because these items lie on tiles
  the chinchompas wander across and a click on a shared pixel resolves to Attack. Note the
  asymmetry with `retrieve_trap`, which *does* use a bare click — the menu entries are swapped
  client-side for trap OBJECTS, but a ground ITEM still has an ordinary Take option.
- This is also why `block_tile` now
  refuses to blacklist a tile one of our own traps is standing on: striking such a tile off the
  plan means never returning for that trap, and it despawns. A run lost two traps exactly that
  way before the double-lay driving those refusals was fixed.

Two things it does differently from the other routines, on purpose:
- **Trap identity is a tile, not a remembered trap.** It re-reads `/objects` every tick and
  classifies whatever box trap is standing on each of its own planned tiles — which also settles
  ownership (a trap on any other tile belongs to someone else) and survives the `ctx.state.data`
  reset, since a plan built from scratch adopts traps already laid near the anchor.
- **No banking phase at all.** Box traps and red chinchompas both stack, so the inventory
  genuinely cannot fill — there is nothing to bank, and adding a bank cycle would be dead code.
