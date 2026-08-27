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
  └─ UDP event stream on port 50555 (PLAYER_DIED, NPC_DIED, LEVEL_UP, HP_CHANGED, CHAT_MESSAGE,
        TICK, OBJECT_SPAWNED/DESPAWNED, GROUND_ITEM_SPAWNED/DESPAWNED, XP_GAINED, ...)
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
  └─ bridge_events.py  — live mirror of the UDP stream + a game-tick clock; lets a routine
                          act ON a tick boundary instead of polling and sleeping margins
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
- `ctx.events` — a `bridge_events.EventStream`, or **`None`**. Added 2026-08-21. A live mirror of
  the UDP stream plus a game-tick clock, so a routine can wait for a pushed event and act ON a
  tick boundary instead of polling HTTP and sleeping a conservative margin. Every consumer MUST
  tolerate `None` and keep a working fallback — it is absent whenever the engine did not start a
  listener. Two hard rules:
  - **It is an accelerator, never a source of truth.** It may answer "has X happened yet" for
    timing. Anything acted on — which entity to click, whether an action succeeded — is still
    confirmed against HTTP. UDP is lossy and these routines run unattended for hours on a real
    account, so a dropped packet must cost latency, never a wrong click.
  - **`wait_for_event` does not consume what it matches**, and the buffer holds ~10 ticks. Call
    `ctx.events.clear()` immediately before the action whose result you are about to wait for, or
    an identical event from a previous action can satisfy the wait. See `retrieve_trap`.
  Useful methods: `wait_for_event(types, match=, timeout_s=)` → payload or `None` on timeout
  (a normal outcome, not an error); `wait_ticks(n)`; `deadline_after_ticks(n)` → a wall-clock
  deadline to record and overlap your own work against, rather than sleeping inline;
  `clear()`; and `ticks` (`phase_ms`, `ms_until_next`, `is_stale`).
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

- **`TickClock` is no longer a self-registered EventBus subscriber** (changed 2026-08-21). It is
  bumped explicitly by `ControllerPlugin.onGameTick` via `tickClock.bump()`, because RuneLite does
  not guarantee subscriber ordering and the emitted `TICK` payload must carry the number for the
  tick it is announcing, not the previous one. Anyone adding a `GameTick` handler needs to know
  this: there is one writer. Dropping the self-registration also fixed a latent leak — `shutDown()`
  never unregistered it, so every plugin disable/enable cycle left another instance subscribed.
- **A `@Subscribe` method MUST be named `on<EventSimpleName>` — this is enforced, not stylistic.**
  RuneLite's `EventBus.register()` does `checkArgument(method.getName().equals("on" +
  parameterClazz.getSimpleName()))`, and the throw escapes the whole `register(this)` loop before
  the subscriber map is built. So ONE misnamed handler stops the ENTIRE plugin from registering —
  every other handler dies with it and the client comes up with no bridge at all. CONFIRMED
  2026-08-21 against `client-1.12.36.jar` in the Gradle cache, after a review caught an
  `onXpGained(StatChanged)` that would have shipped this. The corollary: you cannot have two
  independently-named handlers for one event type, so a second consumer of an event has to be
  folded into the existing method (see `onStatChanged`, which serves both LEVEL_UP and XP_GAINED).
- **`UdpEventSender.send()` makes two SYNCHRONOUS blocking socket writes on the client thread.**
  Anything emitted at high frequency stutters the game. This is why the object/ground-item spawn
  events are capped by `SPAWN_EVENT_CAP_PER_TICK` (40, shared across all four types): a scene swap
  replays the whole scene as spawn events in one tick — verified against the decompiled
  `GameEventManager.simulateGameEvents` — which without a cap is hundreds of blocking writes on
  RuneLite's render thread. Note a `GameState`-based guard does NOT work for this: `GameState` is
  already `LOGGED_IN` at the CLICK-HERE-TO-PLAY screen (see `LoginProvider.java`), and scene swaps
  from ordinary walking never change it at all. Dropped events past the cap are intentional — the
  Python mirror is an accelerator with an HTTP fallback.

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

**`click_menu_action` GAINED `refresh_target` on 2026-08-27** (it forwards to the underlying
`click_entity`, so a right-click now re-aims mid-flight like any other). The warning below
describes why it was needed and still describes what happens if you DON'T pass one: Added 2026-08-23 after breaking it live. The bare-click path hides stale coordinates
because `click_entity` re-aims mid-flight from an un-cached endpoint; the menu path has no such
parameter, so it right-clicks exactly the pixel you give it. Escalating a hijacked plinth click to
`click_menu_action` using the object from the `/objects` list fetched at the top of the tick — i.e.
before the player walked — produced a menu of pure ground options (`['Cancel', 'Mark Safespot',
'Examine', 'Walk here']`): a right-click on bare tile, with the object never involved. Re-resolve
through `get_object_at`/`get_npc_by_index` immediately before calling it. Also pass `offset=(0, 0)`
for a small target: the default `(-7, +7)` nudge is by itself enough to slide off a plinth.

**This is the PREFERRED pattern for every new click site — reach for it before
`wait_until_player_idle()`.** Stated as a standing directive 2026-08-23 (user): a single-entity
accessor is not tick-limited, so a click can be aimed while the player is still walking, and that
is what closes the gap to human speed. The shape is:

1. Fire the walk **without waiting for arrival** (no blocking exact-arrival poll).
2. Issue the target click a beat later, passing a `*_refresh_target` factory **and**
   `refresh_legs` — the un-cached endpoint behind the factory re-resolves the target's live screen
   position at each leg, so the aim stays correct even though the player (and camera) moved after
   the click was decided.
3. Confirm the outcome on a pushed/un-gated signal (XP_GAINED, a varbit, a state endpoint), not
   on a fixed sleep.

**Measured against a human, 2026-08-23** (`recordings/2026-08-23_194302_golem-carving.*`, captured
via `recorder.py` — see "Recording a human session" above): a human clicks the target object
**0.6–1.0s after the walk click**, from a tile they have not reached yet, four times out of four,
and every one of those interactions resolved correctly — the game queues the interaction and
performs it on arrival. Waiting for stillness first is therefore not a correctness requirement,
it is pure latency we were adding on top of the game's own cadence.

The legacy pattern — `walk_to_exact_tile`/`wait_until_player_idle()` blocking until the player has
fully stopped, then resolving a screen position once — is *safe* but structurally slower than a
human, and its blocking walk is what makes an over-tight confirmation timeout destructive rather
than merely slow (a timeout re-walks and interrupts the action that had genuinely started; see
`routines/golem_crafting.py`'s craft_phase notes). Prefer the accessor + `refresh_legs` path for
anything new; leave existing blocking call sites alone unless their throughput is actually a
problem.

Note the endpoints for this already exist and are already un-cached — `/object/at`,
`/ground-item/at`, `/npc/by-index`, `/npc/nearest`, `/player`, `/combat` — so adopting this
needs **no Java change and no client rebuild**, only the Python call site. Check whether the
accessor you need is already there before proposing a new provider.

**Movement realism batch (added 2026-08-27, Python-only, no rebuild):**
- **Pointer-scale calibration persists across restarts** (`.hm2_calibration.json` next to
  human_move.py, gitignored, atomic write, every I/O failure swallowed). It is a property of
  the MACHINE, not the session, but used to reset to 1.0 every process start, so the first flights
  under-traveled ~26% and the residual corrections cleaned up the difference. Tests isolate it
  via an autouse fixture — never let a test write the real file.
- **Startup warns if Windows "Enhance pointer precision" is on** (`warn_if_pointer_acceleration`,
  SPI_GETMOUSE via ctypes) — acceleration makes px-per-unit speed-dependent, which the single
  scalar calibration cannot express. Warn-only, never a hard stop.
- **Session tempo**: durations now share an OU-drifting log-offset (`HM2_TEMPO_SIGMA`/
  `HM2_TEMPO_TAU_S`, see their comment for the five-recording measurement: lag-1 ~0.15, 2-min
  block means wandering 2.4x the i.i.d. expectation). Sessions start in a random mood.
- **Handedness was MEASURED AND KILLED** the same pass — signed path curvature flipped sign
  between sessions (left-bulge 39-64%). v2's zero-mean curve draw is correct; do not add a bias.
- **Click hold times** re-fitted from 589 human down→up pairs (`CLICK_HOLD_RANGE_S`): median
  85ms, log-sigma 0.266, replacing a geometry whose median sat at 70ms with a truncated tail.
- **In-wait fidgeting** (same day, after diffing 13.1 active human minutes against 164 active
  bot minutes): cadence, pause distributions and tick-phase all already matched the human —
  the ONE loud remaining signature was stillness (human cursor busy 80% of active play, never
  still >4.5s; bot busy 13%, still-period p90 11.8s). `human_delay` draws ≥2s are now split
  into measured human-length still segments (median 1.5s, capped 4s) separated by small
  out-and-back drifts. The fidget is POSITION-INVARIANT (returns exactly to its origin) so
  hover-parking through a wait still works; it skips when a right-click menu is open (moving
  off one closes it), the window is unfocused, or an abort is set; it only ever lengthens a
  wait, never shortens it; and it arms only via `register_fidget_serial(ser)` in main() — an
  import without the Arduino keeps the old plain sleep. KNOWN BLIND SPOT, deliberate:
  routines' own 0.2-0.5s-per-iteration poll loops never enter this path; Phase B (a
  background fidgeter behind a global action lock) exists as a design only.
  **VERIFIED live 2026-08-27** (recordings/2026-08-27_023951_phase-a-verify, 3.8 active min of
  slayer): freeze tail dead (max 103s → 8.5s, p90 11.8s → 7.1s), settles firing correctly, no
  interference — but the residual p90 sat exactly at the then-4-9s idle-move timer, i.e. the
  TIMER was the new ceiling. `maybe_idle_mouse_move` therefore no longer runs on wall-clock at
  all: it triggers on time since the last DELIBERATE cursor movement (`_last_mouse_activity`,
  bumped at move_to_human entry, so clicks/walks/fidgets all reset it) with the budget drawn
  from the same measured still-segment distribution — an actively-clicking routine never pays
  a pre-action drift (the old timer fired unconditionally before every tick, so naively
  tightening it would have taxed every action), while genuine idle fills in at human cadence.
  NOT yet re-measured live; next live session, re-run the same diff on a 30+ active-minute
  recording — if still-p90 stays >4-5s after this, the remaining freezes are the poll-loop
  blind spot and Phase B is what's left.
- The fitting scripts live in the session scratchpad, not the repo; the numbers and method are
  recorded in the settings comments. Reach segmentation: movement bursts split on 150ms
  stillness, kept if ≥30px and 80-3000ms; stillness stats count only ACTIVE play (a click
  within 30s on both sides), so scheduled breaks don't contaminate them.

**Moving-target aiming hardening (added 2026-08-27, needs a client rebuild for the Java parts):**
- **`npc_index_refresh_target`'s deadband now keys on every motion source, not just the NPC's
  tile.** The 2026-08-27 tile-only hold (added to suppress hull-animation jitter) was itself a
  bug for click-while-moving: when the PLAYER walks, the NPC's tile never changes while its
  projection slides continuously, so the hold served a stale point for the whole flight —
  literally "clicks land behind". `/npc/by-index` now also reports `npcLocalX/Y`,
  `playerLocalX/Y` (per-frame interpolated LocalPoints) and `cameraYaw/Pitch/Scale`;
  `_npc_view_key` folds them into the hold key (quantized by `NPC_HOLD_LOCAL_QUANTUM`). A fully
  still scene still holds through animation jitter; any real motion re-resolves. Degrades to
  tile-only against an un-rebuilt client (fields absent → None in the key).
- **`click_entity` takes one last look before the mouse-down** (`_settle_on_target_before_click`):
  the flight's final re-aim happens at a submovement boundary, so the settle + click tail
  (~100-300ms) was blind. One corrective move fires only past `PRECLICK_MAX_DRIFT_PX` (18px —
  generous because mid-flight refreshes deliberately drop the caller offset, see move_to_human's
  v2 dispatch), so stationary targets pay nothing. Skipped during a simulated misclick on
  purpose — settling would un-miss the deliberate miss; the corrective reclick gets the full
  treatment instead.
- **Verify-then-click via `GET /hover`** (MenuStateProvider): the default left-click action +
  target under the cursor RIGHT NOW — the top (highest-index) live menu entry, no menu open
  needed. Opt-in per call site: `click_entity(..., expected_action="Attack",
  expected_target="Gargoyle")` reads it before committing; on mismatch it re-aims once via
  refresh_target, and withholds the click if the hover still disagrees — prevention instead of
  `/last-action` post-hoc detection, and the first primitive that beats a player camped on the
  pixel WITHOUT the right-click menu escalation. `hover_matches` never blocks when it cannot
  verify (endpoint missing on an old build, or a real menu open). A withheld click returns the
  falsy `CLICK_WITHHELD` sentinel, not plain `False` — every existing truthiness check keeps
  working, and a caller with an escalation path tells "pixel contested" from "aim failed" via
  `getattr(result, "withheld", False)`. `expected_action` also takes a sequence (any match
  passes) for state-dependent options the caller doesn't choose. Adopted same day: slayer.py's
  two attack clicks (`expected_action="Attack"` + the live target name) and golem_crafting's
  plinth clicks (`GOLEM_PLINTH_OPTIONS`), where a withhold folds into the existing `hijacked`
  flag — it spends the same fast-retry budget, escalates to the same menu tier, and past the
  budget falls through to the same timeout/miss counting, so every measured bound (including
  the out-of-fur recovery, which counts on `gc_side_misses` climbing) is preserved. None of the
  adoptions have run live yet.


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

## Recording a human session before writing a routine

`ControllerAddon/recorder.py` + `providers/RecorderProvider.java` (added 2026-08-23, user request)
capture a **human** playing an activity by hand, so a new routine can be written by reading the log
instead of guessing the activity's mechanics and live-debugging them into existence. Prefer this
over starting a new routine from scratch.

```
python -u recorder.py sunstone-mining      # from ControllerAddon/, then go play. Ctrl+C stops.
```

Three files land in `ControllerAddon/recordings/` (gitignored):
`<stamp>_<label>.jsonl` (every record, raw), `.log` (the readable narrative — the one you read to
build), `.summary` (object/npc/anim/varbit ids, xp, net inventory).

- **Requires a client rebuild + restart** (`./gradlew.bat run`) before `/recorder/*` exists — it's
  compiled into the Java plugin. `recorder.py` detects the 404 and says so rather than failing
  obscurely.
- **Safe to run alongside anything.** It imports only `settings`+`requests` — deliberately *not*
  `controller.py` — so it never touches the Arduino serial port, same rationale as
  `mcp_debug_server.py`.
- **Endpoints**: `GET /recorder/arm?on=true&label=X` (arming clears the buffer and resets seq),
  `GET /recorder/events?since=<seq>&limit=<n>`, `GET /recorder/status`. Disarmed by default — every
  listener returns immediately, so an unused recorder costs nothing.

**Why the capture is Java-side and not a Python poll of `/last-action`:** that endpoint holds only
the most recent click (two clicks inside one poll interval and the first is gone); a
MenuOptionClicked's `param0`/`param1` are *scene* coordinates that only the client thread can
convert to a real world tile (and inside an instance need `WorldPoint.fromLocalInstance` — see the
instancing gotcha above, already handled here); and outcome correlation needs events observed as
they fire, not inferred from end state.

**Outcome correlation is the point.** The hard part of every routine here isn't "what to click", it's
"how do I know the click worked". So each action opens a 5-tick (~3s) window, and every ambient
record landing inside it (animation, spotanim, ground gfx, the clicked object transforming, XP,
inventory delta, chat, varbit flip) is tagged `causedBy: <action seq>`. The narrative log nests
those under the click. A despawn+spawn pair on one tile renders as a single `became` line — that's
a depleting rock / opening door / lighting brazier, usually the best success-signal available.
An action with **no** observed outcome is called out explicitly; that's the case needing a
precondition or retry in the eventual routine.

Records are append-only with monotonic `seq` (the `?since=` cursor depends on it) — actions are
*not* held back until their window closes; `recorder.py` does the grouping at render time.

**Every record carries `t` (ms since arm), `wall` (absolute epoch ms), `tick`, and `tickAge`.**
`tick` is a mirror of `client.getTickCount()` refreshed on GameTick, because records are also
created on the AWT thread (mouse/key listeners) where touching the client isn't safe — so it can be
stale in a way that *looks* valid. `tickAge` (ms since that tick fired) makes staleness visible;
`t` is the authoritative ordering key, and the narrative flags any action whose `tickAge >= 600`.

Volume guards, all load-bearing: ambient records are capped to 12 tiles; other players' animations
are dropped entirely (a crowded bank would otherwise dominate); object spawn/despawn is suppressed
for 1.5s after any non-`LOGGED_IN` state, because `GameObjectSpawned` fires for *every* object in
the scene on each region load and would evict the real recording out of the ring buffer. Mouse
moves are batched into polyline records rather than one per sample. A `dropped` counter surfaces
any hole in the recording, loudly.

**Keystroke privacy**: the key listener sets `isEnabledOnLoginScreen() == false` and drops
everything unless the game state is `LOGGED_IN`, so an account password can't land in a plaintext
recording. Note this does *not* cover a bank PIN typed while already logged in.

### Findings from the first live recording (Falconry, 2026-08-23) — all fixed, don't re-derive

- **`MenuAction.WALK`'s `param0`/`param1` are CANVAS coordinates, not scene coordinates.** Recorded
  values were identical to `canvasX/canvasY` (400,216), and 400 is outside the 104-tile scene grid.
  Every other tile-based action (objects, ground items) *does* use scene coords. The walk
  destination is only knowable from `getLocalDestinationLocation()` **after** the click is
  processed, so it's read on the next tick and emitted as a `walkdest` record attributed back to
  the action. Don't try to decode a walk target from the menu event.
- **`AnimationChanged` never fires for an NPC that spawns already animating** — the animation is
  part of its initial state, not a change to it. The Gyr Falcon spawns mid-swoop, so its animation
  was entirely absent while the player's and the kebbit's were present. NPC animations therefore
  come from a per-tick diff in `diffNearbyNpcs`, not the event; the event is kept for the local
  player only (always fires there, and gives sub-tick timing for free). This costs no fidelity —
  unlike screen position, which animation is playing is genuinely tick-quantised server-side.
- **NPCs transform in place: same index, different id.** The Dashing kebbit *becomes* the Gyr Falcon
  at index 12323 — no spawn, no despawn, so an id-change check is the only way to see it. Emitted as
  `npcchange`. Assume this pattern anywhere an NPC "turns into" something.
- **A first-event baseline silently eats one unit of everything.** The first `ItemContainerChanged`
  and the first `StatChanged` per skill were being consumed to establish a diff baseline, so 7
  catches summarised as "+6 Bones / Hunter +936" — exactly one catch short. `seedBaselines()` now
  captures inventory and all-skill XP at arm time.
- **Varbit noise is defined by fraction-of-ticks, not total count.** Three varps changing every tick
  (101/101/61 over ~100 ticks) buried the real signals. A flat count-12 cutoff hid the noise but
  *also* hid `varbit 14192` (14 changes — the falcon-has-a-catch flag, the most useful value in the
  session). `noise_cutoff` scales with elapsed ticks instead. Suppression affects only the narrative;
  the `.jsonl` and `.summary` always keep everything.
- Renderer gotcha: the Java fields are camelCase (`targetX`, `playerPlane`). Building the key as
  `targetx` renders every position as `?` rather than raising — the capture looked broken when it
  was perfect.
- **A MINIMAP click is not a `WALK`.** It fires `MenuAction.CANCEL` with `param0`/`param1` both 0
  and no target at all — indistinguishable from dismissing a right-click menu, from the event
  alone. CONFIRMED live 2026-08-23 (full Falconry bank run): every long-distance leg of a 5-region
  trip logged as a meaningless "Cancel" with the destination lost, which is exactly the data a
  travel routine needs. Both `WALK` and `CANCEL` now speculatively resolve a destination on the
  next tick, and a **destination-changed** comparison decides which it was (a real menu cancel
  moves nothing). Don't try to classify it by canvas position — the minimap moves between
  fixed/resizable layouts.
- **Decode packed widget ids.** RuneLite packs interfaces as `(group << 16) | child`; the raw
  integer is useless (`25362456`), the pair is not (`387.24` = ring slot on the worn-equipment
  interface). `param0` rides along as the slot index *within* the component (inventory/bank slot),
  which is a different thing from the child. `fmt_widget` does this. Verified against the bank run:
  `12.45` bank Empty-containers, `15.3` bank container (+slot), `149.0` inventory (+slot),
  `387.16/.18/.24` cape/weapon/ring equipment slots.

## Clicking interface elements (`/widget`)

`WidgetProvider.java` + `controller.get_widget(group, child, index=None)` (added 2026-08-23) give a
clickable screen position for **any** interface element by group/child id. Before it, the only
reachable interface elements were the handful `BankProvider` hard-codes — and `/equipment` reports
what is *worn* but carries **no screen position**, so a routine physically could not click a worn
item. That blocked `routines/falconry.py`, which must un-wield a Dramen staff after a fairy-ring
hop because the falconry glove needs the same weapon slot.

The intended workflow is: **record yourself doing the thing, read the decoded `group.child` out of
`recorder.py`'s log, address it here.** That is why this is generic rather than more named buttons.

- Returned dict has `screenX`/`screenY` (the element's *centre*), so it goes straight into
  `click_entity`/`click_menu_action` — always with `check_ui_blockers=False`, same as every other
  interface click (see `travel.teleport_to_ferox` for the rotate-forever loop that flag causes).
- `found:false` (wrong id) is deliberately distinct from `clickable:false` (right id, interface
  closed). Both return HTTP 200 — a closed interface is a normal state for a polling caller.
- `GET /widget/children?group=G&child=C` lists populated dynamic children, for finding which index
  holds what without guessing.
- Keyboard shortcuts still beat clicking a tab: `E` opens worn equipment.

## Current state of routines/falconry.py

Written 2026-08-23 **entirely from a recorded human run** (`recordings/2026-08-23_121236_falconry-
entire-run.*`) rather than from guesswork — the first routine built that way, and the reference for
how the recorder is meant to pay off. Hunts Dashing kebbits and does the full Castle Wars bank round
trip. **Not yet run live.**

Signals it keys off, all established from the recording rather than assumed:
- **`varbit 14192`**: `5` = falcon is sitting on a kill and must be retrieved, `0` = it can be
  thrown. Verified by pairing, not by guessing at meaning — exactly two changes per successful
  catch across two recordings (54/27 and 14/7), never moving on a miss.
- **The falcon has no animation at all**, so there is nothing to wait on visually — confirmed
  across both recordings once NPC animation capture was known-working.
- A **miss is normal** (~40% of throws: 46 attempts → 27 catches), never treated as an error.
- **Bank trigger is free inventory slots.** Fur/meat go to their pouches until 28 each, then spill;
  post-spill each catch costs ~2 slots. Count slots from the live inventory list, **never** from
  distinct item names — 11 Bones is 11 slots, not 1 (the bug that made the recorder report 22 free
  while the game refused fur pickups).
- `Quick-falcon` on Matthias does the whole 500gp transaction in one click; the human's preceding
  `Talk-to` contributed nothing.

The bank trip dispatches on **current region**, not a stored step counter — `ctx.state.data` is
wiped on restart while the character's position is not, so a counter desyncs exactly mid-trip.

**Loadout requirement (a deliberate deviation from the recording):** ring of dueling, Ardougne
cloak and Dramen staff must be in the **inventory**, not worn. The human used the worn-equipment
interface; this uses `click_menu_action` (right-click → read `/menu` → pick by name), the pattern
already proven live here, which needs a screen position that `/equipment` does not provide.

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

**craft_phase was rebuilt 2026-08-23 from a hand-played recording** (`recordings/2026-08-23_
194302_golem-carving`) after it failed live, and everything below is MEASURED — do not re-derive
it by reasoning:

- **One side takes ~11.8s** (11.9/11.9/11.7/11.7 across four sides). The first `progress` bump
  lands **+2.1s** after the click even from a tile you already stand on; bumps then arrive every
  ~2.4s (values 2→4→6→8) and the side completes with the chat line "You finish crafting the golem
  from this angle." `GOLEM_SIDE_TIMEOUT_S` was **4.0s** — under half the action it was timing.
- **A timeout here is destructive, not merely slow.** `carve_plan` re-picks a side from the
  player's new position on the next tick and walks away, interrupting a carve that had genuinely
  started and resetting its progress to 0. That is why the old value produced an unbroken retry
  loop rather than just a sluggish routine. Because `_await_side_done` resets its deadline on
  every progress bump, a real carve can never reach the timeout — **every timeout is a click that
  produced zero progress**, never a slow carve. Useful when reading logs.
- **The side carved is the tile you walked TO, not the tile you clicked from** — confirmed 3/3 by
  resolving `GOLEM_2_{NORTH,EAST,SOUTH,WEST}_PROGRESS` against `runelite-api-1.12.36.jar` and
  matching each against the walk destination in flight. The game queues the interaction and
  performs it on arrival, which is what makes clicking mid-walk safe. The human clicks the object
  **0.6-1.0s** after the walk click, never waiting to arrive.
- **A bare left-click on the plinth gets hijacked by whatever is on top of the aim pixel.**
  CONFIRMED live with the recorder running alongside the routine: a failing click was logged as
  `Walk here -> DoveHorn (level-115)` instead of `Shape-golem`. Roughly **30-40% of carve attempts
  fail this way** in a populated world. `/last-action` reports what a click actually resolved to,
  so a wrong action is provable in ~1.5s instead of inferred after the timeout.
- **The fast retry is capped at ONE per side, and the cap is load-bearing.** Retrying immediately
  re-clicks essentially the same pixel; the routine locked into a 20-attempt loop clicking
  (1093,766) while the clickbox centre sat at (1100,759), on a tile that had carved fine three
  minutes earlier. The old timeout wait was **accidentally load-bearing** — it let the camera and
  the world drift so each retry aimed slightly differently. A second consecutive hijack therefore
  falls through to the full wait on purpose. Never "optimize" that budget upward.
- **The hijacks are OTHER PLAYERS CAMPED ON THE SIDE TILES, and the aim was never wrong.**
  RESOLVED 2026-08-23 by dumping a frame (`GET /screenshot`) and marking the reported clickbox
  centre on it: the crosshair sits correctly on the plinth, but other players' models are drawn
  over it. `GET /players` at the same moment had `DoveHorn` standing on the south plinth's exact
  east tile — the same player, camped there for 30+ minutes of logs — and `M0T1S` beside the
  north plinth. Measured failure rate by side over 110 attempts: **east 67%, west 53%, south 12%,
  north 8%** (48% overall). The east/west skew is simply where those particular players were
  standing; it is not geometry, and a future run with a different crowd will skew differently.
  This also explains the 20-attempt lockup — a parked player never moves, so the geometry the
  retry depends on never drifts.
  **The fix is to stop relying on the default left-click action**: `click_menu_action` (right-click,
  read `/menu`, pick "Start-golem"/"Shape-golem"/"Insert-core" by name) is immune to whatever is
  drawn on top, which is exactly why `chinchompa_hunting.py`'s `recover_dropped_trap` uses it for
  ground items on tiles chinchompas wander across — see that routine's note on the asymmetry
  between a bare click and a menu selection. NOT yet implemented; the retry path above is only a
  mitigation.
- **The click escalation is now three tiers, and each tier exists for a different failure.**
  (1) bare left-click with `refresh_target`+`refresh_legs` — fast, human-looking, and what works
  when nothing is in the way; (2) ONE fast retry when `/last-action` proves the click resolved
  wrongly; (3) `click_menu_action` picking the option by name, which is the only tier that beats a
  player standing on the pixel. Tier 3 was added after the routine HALTED live 2026-08-23 21:04 —
  five consecutive failures on the south plinth's east side, safety-stopped and down until
  restarted, because a player was stacked on our own tile and no amount of waiting made them move.
  VERIFIED live: bare click hijacked -> fast retry hijacked -> menu selection landed and the carve
  proceeded, with zero menu misses once tier 3 re-resolved its position (see the
  `click_menu_action` gotcha above for the mistake that cost a deploy).
- **Known limitation in that mitigation: `gc_side_hijacks` is not keyed per side.** It only resets
  on a click that was NOT hijacked, so once any side hijacks, the next side's FIRST hijack already
  reads as "again" and skips its own fast retry — observed live 2026-08-23 20:50:44, where the
  west side's first attempt fell straight through to the full wait. This is safe (it errs toward
  the slow, proven path) and merely leaves value unclaimed: in practice the fast path helps about
  the first hijack after each success rather than one per side. The fix is to key the counter by
  side, or clear it when the planned side changes — deliberately not done unsupervised, since the
  budget cap is the thing standing between this and the 20-click spin it replaced.
- **A Goat Pit fur run legitimately stops golem production for ~15 minutes.** MEASURED
  2026-08-23 22:23-22:38: bank fur exhausted -> `hunt_phase` -> 166 Telekinetic Grab casts before
  "pit is now filled with goats -- harvesting", then a 46-tile Shortest Path walk back to the
  mine. Nothing is wrong during that window and the routine recovers on its own. Worth knowing
  before diagnosing a "stall": any watchdog keyed on golems-per-hour needs a threshold above this,
  or it fires every time fur runs out. A log that stops GROWING is the unambiguous stuck signal;
  golem count alone is not.
- **`Insert-core` needs the SAME escalation as a carve — fixing only the carve moves the failure
  here.** LIVE HALT 2026-08-24 02:17: with carving fixed, the identical hijack relocated to the
  one unprotected click on the same object. Signature: click positions alternating between two
  values across five attempts (the click resolves to "Walk here", the player moves, the next tick
  walks back). It strands a WHOLE golem — all four sides carved, progress 5, only the last step
  failing. Now escalates the same way, VERIFIED live 02:39 completing a golem that would have
  stranded.
- **ROOT CAUSE of every "starting golem ... never registered" halt: NO FUR. `Start-golem` is not
  offered at all when you are out of fur** — identified by the user 2026-08-24 after seven of my
  hypotheses had died. Everything fits: fur is inventory-wide so BOTH plinths refuse at once; the
  game gives no rejection message so the option is simply ABSENT from the right-click menu
  (`['Cancel','Mark Safespot','Examine','Walk here']` every time); it strikes only at progress 0
  because a part-carved golem can still be Shape-golem'd without fur; and a restart appears to fix
  it only because restarting forces a bank trip that refills fur. CONFIRMED on the fix's first
  deploy: the bank held **15 Wyrmscraig goat fur** while the routine carried none.
  Two things conspired. `has_start_materials` checks sunstone and cores and **never fur**. And
  `gc_fur_stock` is a local estimate this file's own comments already warned "can read >0 while
  the pouch is genuinely empty" — its one authoritative correction, `GOLEM_CHAT_NOT_ENOUGH_FUR`,
  only ever fires on **Insert-core**, so nothing catches an out-of-fur condition at the start
  step. craft_phase now treats a start refusal WITH sunstone and cores in hand as exactly that:
  zero the stale estimate and go to bank_phase (which restocks, and falls through to hunt_phase
  when the bank is empty too).
  **Lesson: when the game silently declines to OFFER an action, the missing ingredient is one the
  precondition check does not cover.** Read the full recipe before theorising about clicks — the
  absent menu entry was saying "you cannot do this", not "you missed".
- **Start-step failures: what was RULED OUT before the cause was found, so nobody re-derives it.** Three halts at
  "starting golem", progress 0 (21:04, 22:04, 02:00, 02:27, 02:34). Each of these was killed by
  measurement, not reasoning:
  - *Bad aim point* — NO. A frame dump with the reported centre marked shows the crosshair sitting
    squarely on the plinth (an Empty plinth renders as a dark hexagonal socket; it is easy to
    mistake for bare ground at low zoom).
  - *Missing materials* — NO. 15 Sunstone + 4 cores present at the time of failure.
  - *Another player occupying the plinth* — NO. `/players` showed nobody adjacent.
  - *Wrong start side* — NO. East and west failed identically, so `choose_start_side`'s new
    `avoid` set (added the same night) does not address it.
  The one condition every failure shared: **inventory at 28/28**. Unproven as a cause. Pit spikes
  are now shed before both mining and crafting (`_shed_pit_junk`), which is justified on budget
  grounds anyway — leftovers were also capping mining at 15 sunstone against the 20 budget. If a
  halt recurs on a NON-full inventory, that hypothesis is dead too and the next place to look is
  ObjectProvider's clickbox reporting or an unidentified Start-golem precondition.
- **One specific tile, (2597, 2257), intermittently fails to project — the north plinth's EAST
  approach tile.** MEASURED 2026-08-24 over a 2.5h run: **10 of 10** `couldn't walk to ...` walk
  failures were on that tile and no other, across four separate occurrences (03:37, 04:01, 05:30,
  06:09). `walk_to_exact_tile` returns False only when `get_ground_walk_point` reports the tile
  not on screen — yet checking `/ground/walk` for that exact tile seconds after one failure
  reported `onScreen:true` at (1150,621). So it is transient and tile-specific, not camera-wide.
  Survivable today (the approach back-off switches to the other plinth and production continues),
  but it is the cleanest remaining lead in this routine: something about that one tile's
  projection fails while its three neighbours never do. Start at `get_ground_walk_point` /
  `CombatProvider`'s `/ground/walk`, not at the routine.
- **Process note worth heeding: six fixes were made to what looked like one problem, and each
  relocated the failure instead of removing it** (carve timeout -> hijack retry -> retry guard ->
  menu escalation -> stale aim -> stillness -> menu-miss degrade -> side avoidance -> insert
  escalation). That pattern is the signal to stop patching and question the model. Every tier
  built re-aims at the SAME pixel `/object/at` reports, so when that pixel is unusable, all tiers
  fail identically — better retries cannot fix a bad target.
- Insert-core was observed succeeding from the **west** tile while `/golem` reports the south
  plinth's `insertSide` as `north` (core consumed 0.3s after the click, too fast to have walked).
  Either the insert side is not a standing requirement or `insertSide` means something else. Not
  resolved.

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
