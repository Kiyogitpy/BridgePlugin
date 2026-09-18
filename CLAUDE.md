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
        TICK, OBJECT_SPAWNED/DESPAWNED, GROUND_ITEM_SPAWNED/DESPAWNED, XP_GAINED, and since
        2026-09-04: VARBIT_CHANGED (subscribed ids only), WIDGET_LOADED/CLOSED, MENU_CLICKED,
        GRAPHIC_CHANGED, HITSPLAT, PLAYER_MOVING/STOPPED (per-frame), NPC_SPAWNED/DESPAWNED, PROJECTILE_SPAWNED (2026-09-05),
        GRAPHICS_OBJECT_CREATED (2026-09-06, ground spotanims: id/x/y/plane/tick, spawn-budgeted), ...)
        ↕
Python controller (ControllerAddon/)
  └─ controller.py     — engine: bridge polling, Arduino serial driving, panic key,
                          safety stops, human-movement realism (see settings.py)
  └─ routines/*.py     — one file per activity (combat, slayer, mining, golem_crafting, ...);
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
  `ExamplePluginTest.main`, which loads the Controller plugin and starts the bridge -- and,
  when the `../ZombieLite` checkout exists (branch `no-packets` on the user's fork, an
  overlay-only boss-helper bundle), builds and loads every ZombieLite plugin into the SAME
  client. **The no-packets rule is absolute**: nothing loaded next to the bridge may inject
  input -- the "physical Arduino, never software-injected" rule in "What this is" applies to
  every plugin. Upstream ZombieLite commits are cherry-picked onto `no-packets` (skip any
  that touch `com.salvation.packets` or re-add an input-sending plugin), read-only-ness is
  judged per FILE (a plugin that draws and clicks keeps its drawing, loses its clicking),
  and `./gradlew.bat checkZombieLite` must pass after every pull or `latest.release` bump.
  Zombie Prayer is indicators-only (its three acting call sites removed, each marked
  `no-packets:`); Kiyo's Weapon Tracker addon lives in ZombieLite as the CANONICAL copy.
  Full history, commit ids and the cherry-pick workflow: **`docs/zombielite.md`**.
- **The VM**: the client, bridge, controller.py AND the Arduino all run inside a
  VirtualBox guest (`OSRS`, Win10 22H2, guest user `Kiyo`/`osrs`) so the host's physical
  mouse stays free; the Leonardo is captured by a USB filter and is **COM3 in the guest**
  (`BRIDGE_SERIAL_PORT`). Login fingerprint parity with the host is maintained (same SMBIOS
  UUID via `--hardware-uuid`, `-XX:ActiveProcessorCount=32 -Xmx8541700096`, same Temurin
  11.0.30; no Jagex Launcher -- `credentials.properties` auto-login, keep the host's
  RuneLite CLOSED while the VM runs or the two fight over one refresh token). Drive a whole
  session from the host with **`run-vm.bat`** -- bare = PANEL MODE (routine picked in the
  GUI, hot-swapped live), `run-vm.bat <routine>` pins one. It starts the VM, re-adds the
  NAT forwards (bridge probe host 127.0.0.1:18080, SSH 2222), launches the client, waits
  for full login, focuses the window and starts the routine. **Python edits on the host are
  LIVE in the guest** through the `Z:` share (no sync; logs land in the host's own
  `ControllerAddon\logs`); only GRADLE runs from the synced `C:\work\` mirror.
  `tools/vmexec.py` executes guest commands over SSH -- `--interactive` for anything
  touching the desktop/cursor (plain SSH has no window station); put guest commands in a
  `.ps1` on the share and invoke by UNC path (two measured quoting traps otherwise).
  `tools/vm-set-controller-config.ps1` writes panel config -- it refuses while the client
  runs, and that guard is load-bearing. Host-side bridge reads honour `BRIDGE_API_BASE`
  (`.mcp.json` points the MCP server at the 18080 forward); `poll_events` stays guest-only.
  F1 panic must be pressed inside the VM window. Everything else -- provisioning scripts,
  SSH setup, screen geometry (guest desktop 1920x974, windowed-maximized, never set
  `GUI/Fullscreen`), profile copying (`profiles2` does NOT sync -- re-copy after changing
  plugin settings on the host), the guest-console-over-the-viewport click eater, cold-start
  verification -- is in **`docs/vm.md`**; read it before touching any VM tooling.
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
- **Pathfinding**: `ControllerAddon/worldmap.py` holds a world-wide per-edge collision map
  (all four planes) and answers `find_path`/`reachable_from` offline in milliseconds. It is
  the only routing engine; Shortest Path was removed 2026-08-29. Doors are baked open in that
  map by design -- `travel.open_door_toward` finds the shut one standing on the route. The map
  CANNOT tell furniture from a wall, so `travel.can_reach` still answers object-interaction
  adjacency from live collision flags, and must not be moved onto the map.

## Offline OSRS research MCP

The `osrs-wiki` MCP server in `.mcp.json` provides the local OSRS reference corpus. **Use it
before external web lookups or broad filesystem searches** whenever the question involves Wiki
gameplay information, decoded game-cache definitions, interfaces/scripts/symbols, RuneLite source,
or Plugin Hub source. It runs entirely from these already-built local databases:

- `wiki.sqlite` — revision-pinned OSRS Wiki pages, redirects, equipment requirements, and
  structured NPC/object/ground-item spawns.
- `cache.sqlite` — decoded game-cache configurations, interfaces, scripts, and symbols; it does
  not include binary assets such as models, maps, sprites, or audio.
- `runelite-code.sqlite` — revision-pinned Java from RuneLite API/client/HTTP API, Plugin Hub
  tooling, and Plugin Hub plugins.

Choose tools by source: use `search_unified` for a combined Wiki + cache query, `search_wiki`
plus `get_wiki_page`/`get_wiki_sections`/`get_wiki_section` for game mechanics and Wiki prose,
and `search_spawns` for NPC/object/ground-item coordinates. Use `search_cache` for raw client
definitions (optionally restricting `kind`, for example `config/obj`, `config/loc`,
`config/varbit`, `interface`, or `script`), then call `get_cache_entry` with the returned exact
kind and ID. Use `search_code` for RuneLite or Plugin Hub implementations (optionally restrict
to `runelite-api`, `runelite-client`, `runelite-http-api`, `pluginhub-tooling`, or
`pluginhub/<internalName>`), then use `get_code_entry` with the returned case-sensitive kind and
ID. Keep queries short and distinctive; this is lexical full-text search, not semantic retrieval.

## Reference implementations in `data/decompiled/`

**Documentation only — nothing here is on our build path.** These are decompiled or vendored
third-party projects kept for reading. Before writing a new subsystem from scratch, check
whether one of them already solves it; two of the four below were found only after the
equivalent had already been hand-built and live-debugged for several sessions.

| Directory | What it is | Reach for it when |
|---|---|---|
| `PacketUtils/` (EthanApi) | `EthanApiPlugin/PathFinding/GlobalCollisionMap` + `PathingTesting` — a world-wide per-edge collision bitmap and a BFS over it, plus an entity/widget query API | You need a walking route, especially on a plane Shortest Path cannot reach |
| `Vitalite/api/.../services/pathfinder/` | A 21k-LOC pathing system: local vs global collision, A*/BFS/JPS/flow-field, transports with requirements, teleports, sailing | You need transports, teleports, or a worked example of local-vs-global collision |
| `CacheMapDumper-main/` | Generates a collision map from an OSRS cache pulled from OpenRS2. Writes RoaringBitmap + gzip, with a Swing viewer | The static map goes stale after a game update, or you need extra per-tile data baked in |
| `rl-suite-main/` | Deobfuscates a RuneLite injected-client jar into readable source; publishes packet tables (ids, names, lengths, field layouts) for revs 235-240 as TSV in `data/` | You need to read client internals the API does not expose. **See the caveat below before touching the packet half.** |
| `mahogany-homes-plugin/`, `shortest-path-plugin/`, `sailing-plugin/`, `target-true-tile/` | Sideloaded Plugin Hub jars | Porting a fact the plugin already knows — copy its SOURCE, not your reconstruction of it (see the 231.6 dialogue-widget lesson) |
| `client-1.12.33/`, `injected-client-1.12.33/` | The RuneLite client itself | Settling an API question against the real jar rather than assuming |

**`rl-suite-main`'s packet tables are not a licence to send packets.** This project drives a
physical Arduino over serial *specifically* because the input is not software-injected (see
"What this is"). Adopting packet-based interaction would reverse that decision, and it is the
user's call to make explicitly — not something to reach for mid-task because it is convenient.
The deobfuscation half carries no such caveat.

### Collision maps: three of them, and they must never be mixed

All three are per-edge bitmaps storing only NORTH and EAST per tile (south/west are read off
the neighbour), and all three bake **doors as OPEN** — deliberately. `CacheMapDumper`'s
`Exclusion` enum is the hand-curated list that does it (guild doors, city gates, arches, prison
doors), so doors-passable is a decision, not a dump artifact. The model everywhere is: route
straight through the door, then detect and open it when you reach it.

| Map | Packing | Polarity | Readable from Python |
|---|---|---|---|
| EthanApi `PathFinding/map` | `x & 16383 \| y<<14 \| plane<<28`, bit 30 = east | **set = passable** | Yes — RoaringBitmap portable format |
| Vitalite `map.dat` | `x & 8191 \| y<<13 \| plane<<28`, bit 30 = east | **set = blocked** | No — Java-serialised `SparseBitSet` |
| CacheMapDumper output | 12-bit x (base 480), 14-bit y, 2-bit plane; data bit 0 = north, 1 = east | **set = blocked** | Yes — RoaringBitmap + gzip |

Same shape, opposite meanings. Reading one with another's indexer produces a map that is
subtly wrong rather than obviously broken.

MEASURED 2026-08-29 against EthanApi's map: 3.6 MB gzipped / 78 MB decompressed, 82.2 M set
bits, per-plane p0 10.2 M / p1 22.3 M / p2 24.3 M / p3 25.4 M — **upper planes carry real
data**, which is the thing Shortest Path structurally cannot give us. Pure-Python BFS over it
costs 0.13 s to load and 0.4-3 ms for in-house routes, 151 ms for a 314-step cross-city one.

Two properties to know before trusting a route:

- **No data defaults to PASSABLE.** Verified at Varrock square on planes 1 and 2 (open sky above
  an outdoor tile) — all four edges passable. Upstairs, a missing exterior wall lets a BFS leak
  through the void.
- **An object's own blocking is baked into the same bits as a wall's**, so the static map cannot
  tell furniture from a wall. VERIFIED at JEFF's upper floor: the player stood diagonally beside
  a hotspot and interacted fine, while the map called every route to it blocked. **This is why
  `travel.can_reach` stays on the LIVE `collisionFlags` from `ObjectProvider`** — only there do
  `BLOCK_MOVEMENT_OBJECT` (256) and the directional `MOVEMENT_*` bits stay distinguishable.
  Vitalite makes the same split (`LocalCollisionMap.all()` uses the global static map for
  ordinary coordinates and live flags only for instanced ones).

`CacheMapDumper` packs **up to 5 data bits per tile and uses only two**, and
`Dumper.processRegion` already reads `getWallOrDoor()` and `getInteractType()`. So a "door on
this tile" bit and an "object-blocked, not wall-blocked" bit are available if the live check
ever proves insufficient.

### Doors: the model that replaced ours

Vitalite's `WalkerPath.handlePassThroughObjects` is the whole door algorithm, and it is smaller
than any of the three tiers we built: **when the next step is not reachable, find an object
named `door`/`gate`/`curtain` (excluding `trapdoor`) standing on the player's tile or on the
next step's tile, and interact with it.** No orientation, no edge-bit matching, no span tests —
having a real route is what makes the question trivial. It covers both shapes we measured
(Barbara's edge doors and Falador's `GameObject` 24050) because both sit *on* a route tile.

**One correction to make when copying it:** Vitalite matches on name only and interacts with
action index 0. Left-clicking an *open* door **CLOSES** it, turning a working walk into a wedged
one — a shut door and an open one are both `WallObject`s named `Door` with per-door ids
(Falador 24050 shut / 24051 open), and only the `Open` action separates them. Keep the action
check. Do take `curtain`, which our name list was missing.

See `docs/superpowers/specs/2026-08-29-global-collision-pathfinding-design.md` for the design
this feeds.

## What the game client sends home

Full analysis, read from the rl-suite deobfuscation 2026-09-07 (packet-by-packet detail,
the fingerprint parity table, the recorder's coverage of it): **`docs/telemetry.md`**.
The facts that shape day-to-day work:

- **Login fingerprint**: OS/Java versions, max heap, CPU count, SMBIOS UUID (via `wmic
  csproduct get UUID`), plus the process's and PARENT process's executable names and any
  `-javaagent` by name. Keep the launch shape constant per account; never add a
  `-javaagent` to the run task. There is NO MAC-address, screen-size or hypervisor read --
  a VM leaks only as a CHANGED identity, so parity (UUID, cores, heap, JDK) is the
  ceiling. `GET /client-version` reports the live fingerprint; the bridge binds loopback
  only since 2026-09-07 (`BRIDGE_BIND_ALL=1` for the VM guest).
- **The whole cursor trajectory is transmitted at 20 Hz** (`EVENT_MOUSE_MOVE`, including
  off-canvas as -1), every press with 1 ms inter-press timing, every keypress with its
  rhythm, scrolls, window focus gain/loss, arrow-key camera moves. "No click happened"
  never means "nothing was sent" -- stillness, snap moves, an exact-pixel return and the
  cursor leaving the window are all server-visible.
- Rules that fall out: (1) treat every cursor sample as transmitted -- measure with
  `tools/humanity_report.py`, always comparing THE SAME ACTIVITY human-vs-bot, and run any
  new metric over a real capture before pinning its expected value; (2) timing drawn from
  a fitted human distribution (`settings.py`) is fine on the wire, a CONSTANT is a
  fingerprint -- keys as much as clicks; (3) keep the login fingerprint constant per
  machine; (4) re-derive after every client build bump: `python tools/deobfuscate.py` does
  the whole thing, `ControllerAddon/deob_guard.py` refuses to drive input against an
  unanalyzed build, and `tools/telemetry_fingerprint.py` diffs the telemetry surface
  between builds (1.12.37 -> 1.12.38: nothing collected changed).
- The plugin list, overlays, ZombieLite, the bridge, the UDP stream and the serial port
  are in NO packet -- the input stream is the only channel that carries our behaviour,
  which is exactly the channel the physical Arduino feeds. RuneLite's own telemetry goes
  to runelite.net, not Jagex.

## Vendored m8aq readers (`GET /api/...`)

Added 2026-08-29. `src/main/java/net/runelite/client/plugins/m8aq/api/` is a **byte-identical
copy of RLitePlus/m8aq-plugins' reader tree**, kept in its ORIGINAL package on purpose, and
`com.example.providers.M8aqProvider` is the only adapter between it and our bridge. This is the
one place in the repo where third-party source sits on the build path -- everything else under
`data/decompiled/` is still documentation only.

66 endpoints, `GET /api/<group>/<reader>` (kebab-cased), plus `GET /api/all` (every reader in one
snapshot, which is `export/GameOutput`) and `GET /api/index` (the live path list). Read
`/api/index` rather than hardcoding a path list from memory. Python: `controller.get_api(group,
reader)`, `get_api_all()`, `get_api_index()`.

What it adds that we never had: every storage container (rune pouch, herb sack, log basket,
gem/coal bag, seed box, fish barrel, looting bag, essence pouches, potion storage, seed vault,
group/house storage, death recovery), run energy/weight/destination (`player/movement`),
poison/venom/antifire/stamina timers (`player/status-effects`), `player/magic`,
`player/transportation`, all of `runtime/`, `social/`, `trade/` (GE, shops, player trade),
`progression/` (quests, diaries, collection log, combat achievements), `ui/dialogue`,
`ui/interfaces`, and nine minigames including Wintertodt, Blast Furnace, Giants' Foundry, GOTR
and Soul Wars.

Five things to know before touching any of it:

- **Never edit a file under `api/`.** The no-edits rule is what makes `tools/sync-m8aq.sh` a
  clean overwrite instead of a conflict-ridden rewrite of 71 files, and it is the whole reason
  the package was not renamed to `com.example`. Wrap behaviour in `M8aqProvider` instead. See
  `src/main/java/net/runelite/client/plugins/m8aq/VENDORED.md` for the pinned upstream commit.
- **Discovery is reflection, deliberately.** `ReaderCatalog` finds every public class with a
  `public static State getState(Client)` via a Guava classpath scan, so a reader added upstream
  becomes an endpoint on the next client rebuild with **no code change**. That is the payoff for
  vendoring verbatim. The cost: a scan that walks nothing yields zero endpoints and no error --
  a bridge that is up and serves none of this. Hence the startup count log, the `stderr` warning
  below `MINIMUM_EXPECTED_READERS`, and `M8aqProviderTest`'s floor assertion. MEASURED under the
  Gradle test classloader: 67 discovered, 66 endpoints.
- **Namespaced under `/api/` because eight names collide.** `Bank`, `Inventory`, `Equipment`,
  `Prayer`, `Npcs`, `Skills`, `GroundItems` and `TileObjects` are all readers here AND existing
  endpoints of ours with a different JSON shape. The two coexist and mean different things --
  `/bank` is not `/api/storage/bank`.
- **No `TickCache`, on purpose.** These are varbit and item-container reads, not the bulk scene
  scans that cache exists to make cheap; each request reads fresh on the client thread like
  `/player` and `/object/at`. A reader that throws returns an `{"reader", "error"}` dict rather
  than a 500, because several are interface-dependent and legitimately unhappy with their
  interface closed -- so on the Python side `None` means "bridge/endpoint unavailable" and an
  `error` key means "this reader failed", and neither means "empty container".
- **Serialization needed no Gson adapters, and that was checked, not assumed.** Every field is a
  primitive, `String`, enum, `WorldPoint`, `List<ItemSlot>` or `LocalPoint` (which stores
  `worldView` as an `int`, not a reference); the heavy `Widget` fields are already `transient`,
  which Gson honours; the one lambda field (`EssencePouches.Pouch.degradationReader`) lives on an
  enum, which Gson emits by name. Re-check this after a sync that adds a field type.

The real standing risk is **not** the sync mechanism, it is that reader field names become a
Python-facing contract we do not control: an upstream rename silently changes JSON keys.
`/api/index` makes a moved *endpoint* visible and `M8aqProviderTest.knownPathsAreStable` fails
the build on one, but nothing catches a renamed field. Diff `/api/all` after every sync.

Upstream has **no LICENSE file** (confirmed via the GitHub API, 2026-08-29).

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
  **Added 2026-09-04 (event-driven outcomes,
  `docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md`):** `mark()` + `since=`
  replace the clear-before-the-action rule; `await_outcome(signals, budget_s, confirm=,
  since=, shorten=)` is THE way to wait for a click's outcome -- a listed UDP signal, confirmed
  by one HTTP read, with the HTTP read deciding alone at the budget, padded by a human
  reaction draw (`settings.REACTION_DELAY_S`, fitted from the GOTR benchmark). The
  module-level `bridge_events.await_outcome(ctx.events, ...)` handles `None` by polling.
  `controller.py --no-events` is the A/B control. Routines may declare `VARBIT_SUBSCRIPTIONS`/
  `VARP_SUBSCRIPTIONS`; the engine posts them on load and after every login. The engine loop
  wakes on any non-TICK event (`EventStream.wait_any`) and logs its own iteration time
  (`LoopStats`, `[engine] loop: ...` every 60s) -- read that line before assuming the loop's
  cost. `log_gaps.py <log>` shows where a run's time went.
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

- **A bridge query value is only safe if it has no characters `requests` will percent-encode.**
  `BridgeHttpServer.parseQuery` read `getRawQuery()` and split on `&`/`=` without ever
  URL-decoding, so a `%2C` (what `requests` turns a comma into when you pass `params=`) arrived
  literally. CONFIRMED live 2026-09-13: `subscribe_varbits` sent `varbits=5558%2C5556%2C2086`,
  `EventSubscriptions.parseIds` found no separator and returned an **empty set** — so *every*
  routine with two or more `VARBIT_SUBSCRIPTIONS` had been running with varbit events entirely
  off, and one with a single id worked (no comma), which is what hid it for so long. It failed
  **silently**: HTTP 200, a well-formed `{"varbits":[],"varps":[]}` reply, so the "bridge has no
  /events/subscribe" warning never fired either. The cost is pure latency, never a wrong click —
  which is exactly why nothing caught it: `await_pre` just falls through to `confirm()`-at-the-
  deadline and spends its **full budget**. MEASURED: motherlode ladder climbs at 11–12s against a
  6s budget, 5.7–6.0s once subscribed. `parseQuery` now decodes (fixes every `registerQuery`
  endpoint — a space, `&`, `=` or `+` in any value was equally broken) but **that half needs a
  client rebuild**; `subscribe_varbits` also puts its CSV in the path where `requests` cannot
  re-encode it, which is what makes it work on an un-rebuilt client. Check
  `GET /events/subscriptions` (plural — the read-only one) to see what the bridge actually holds.
  **`GET /events/subscribe` with no params CLEARS the set**, so it is not a read.
- **`get_dpi_scale()` caches for the process lifetime, and Windows lies about DPI until
  `set_dpi_aware()` runs.** `LOGPIXELSX` reports a virtualized 96 to a DPI-unaware process, so any
  caller that measures first locks the scale to 1.0 and leaves every bridge `screenX`/`screenY`
  unscaled. CONFIRMED live 2026-09-13: `main()` ran `deob_guard.ensure()` — which makes its own
  `api_get()`, and `api_get` ends in `_scale_screen_coords(..., get_dpi_scale())` — two lines
  before `set_dpi_aware()`. On a 125% display every click then landed at 1/1.25 of the correct
  offset (short, up-left) and routines read that as "the interaction did not register", retrying
  forever on an object they were never touching. **Invisible at 100% scaling**, where 1.0 is right
  anyway — so it only ever showed up on the laptop. `get_dpi_scale()` now calls `set_dpi_aware()`
  itself before measuring; don't reintroduce a measurement that trusts statement order.
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
  **RESOLVED for the Gauntlet 2026-09-05 (sub-project 1): the bridge now carries a second,
  INSTANCE-SPACE coordinate pair.** `instX`/`instY` (base + scene, `InstanceCoords.java`) on
  `/player` (+ `inInstance`, `baseX`, `baseY`), `/npcs`, `/npc/*`, `/objects`, `/object/at`,
  `/ground-items`, `/ground-item/at` and `/collision` tiles; `inst=true` on `/ground/walk`,
  `/minimap/walk`, `/object/at`, `/ground-item/at`, `/collision` resolves x/y by ARITHMETIC
  against the world view's base (never `LocalPoint.fromWorld`); `/collision?all=true` is the
  whole plane as one grid. `ControllerAddon/instance_nav.py` (`InstanceMap`, `find_path`,
  `walk_to`) is the instance routing engine; `worldmap.py` stays the overworld one. Template
  `x`/`y` keep their meaning and are still what a hardcoded tile in a routine matches. Live
  status: see the spec's checklist -- fill in what was verified here.
  **Fixed 2026-09-05 (final-review C1): `/player`'s `plane` is the TEMPLATE plane, not
  necessarily the plane the player stands on** -- `instPlane` (`topLevel.getPlane()`, 0 when the
  view is null) is the LIVE one, and `instance_nav.walk_to` loads `InstanceMap` with no plane
  argument at all so `CollisionProvider` defaults to the LIVE plane and hops on the grid's own
  `plane`, never `/player`'s.
  `/npcs`/`/npc/*` also carry `overheadPrayer`/`overheadPrayerId`/`overheadArchiveId` (the
  Hunllef's protection-prayer overhead, decoded only once a live check has paired an archive id
  with each of its states -- see NPCProvider's own comment); `gauntlet_ids.py`'s module docstring
  documents the `NPC_SPAWNED`/`NPC_DESPAWNED`/`PROJECTILE_SPAWNED` UDP event payload shapes.
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
- **A click's DEPARTURE is aimed too, not just its landing -- and `refresh_target` now governs
  both.** `click_entity` used to begin every flight at the caller's `entity["screenX"/"screenY"]`
  and only consult `refresh_target` mid-flight. For a caller holding a snapshot -- anything read
  from the bulk `/objects` or `/npcs` scan at the top of a tick -- that means setting off toward
  where the target WAS and being hauled forward partway there. MEASURED live 2026-08-30 in
  `mahogany_homes`'s hotspot burst, which re-clicks the same object while the player runs: every
  re-aim departed toward the tick-old projection. USER, watching it on screen: *"as we move the
  mouse have to flick more but we flick back for some reason after we click"* -- the flick BACK is
  the departure toward the stale pixel, and the correction forward is the mid-flight refresh
  doing its job. `click_entity` now resolves the initial aim through `refresh_target` when one is
  given, falling back to the caller's coordinates when it cannot answer. **Passing a
  `refresh_target` is therefore the whole fix at a call site** -- do not re-resolve the position
  yourself first. The corollary for anything that does NOT pass one: it is aiming at whatever
  snapshot it was handed, so re-resolve through `/object/at` / `/npc/by-index` immediately before
  clicking (this is the same trap `click_menu_action` documents below).

- **Simulated human "mistakes" are intentional, not bugs.** `controller.click_entity` has a
  `MISTAKE_MISCLICK_CHANCE` (~4.5%, see `settings.py`) chance of deliberately aiming beside the
  target instead of on it, logged as `[!] Misclick -- aimed beside X instead of on it`. This is
  anti-detection humanization, not a targeting bug — code that reacts to a click's outcome must
  handle this (see golem_crafting.py's mining reclick gate for an example of a bug this caused:
  waiting forever for an inventory change that a misclick will never produce).
- **`controller.py` EXISTS TWICE in a running process, so its module-level state is duplicated.**
  It is run as a script (`python controller.py routines/x.py`), so it lives in `sys.modules` as
  `__main__` — and every routine and shared module then does `import controller`, which loads the
  same file AGAIN under the name `controller`. Python caches by NAME, so those are two distinct
  module objects. PROVEN 2026-08-30 by loading it both ways and comparing identity, after the
  numbers in a log stopped adding up (an idle-drift line claiming 3.1s of stillness one second
  after a click).
  The engine loop lives in `__main__`; routine calls go through `controller`. So **any
  module-level variable one of them writes, the other cannot see.** Confirmed casualties:
  `_last_mouse_activity` and `_busy_until` (the idle drift never saw routine activity at all, and
  drifted straight through active play), and `_fidget_ser` (in-wait fidgeting has never fired for
  a routine-side wait). `FIRMWARE_PROTOCOL` is safe only because nothing outside `controller.py`
  reads it.
  **Anything that must be shared across those copies belongs in its own module** —
  `input_state.py` exists for exactly this and is only ever imported by name, so there is one of
  it however many times `controller.py` is loaded. `tests/test_controller.py` has a test that
  fails the moment shared input state migrates back into `controller.py`.
  THE AUDIT IS NOT FINISHED: only the three variables above were checked. This class of bug fails
  SILENTLY — nothing errors, the state simply never arrives — so the remaining module-level
  mutable state in `controller.py` is worth a deliberate sweep rather than waiting for the next
  symptom.

- **The panic key can go dead SILENTLY under load, so it has two independent paths.**
  USER-REPORTED live 2026-08-30, mid-run: F1 and F2 both stopped responding while the routine was
  still driving the mouse. `keyboard.wait()` rides a Windows LOW-LEVEL HOOK, and Windows silently
  REMOVES such a hook when its callback overruns `LowLevelHooksTimeout` (default 300ms; confirmed
  not overridden on this machine). Once dropped it is gone for the life of the process, with no
  error anywhere. The suspected trigger was the hotspot burst having just become much busier — a
  tight click loop making two extra HTTP calls per click is exactly the GIL pressure that starves
  such a callback — though that specific causation is inferred, not proven.
  `_async_key_watchdog` polls `GetAsyncKeyState` on a daemon thread instead: no hook, so nothing
  to lose, and it keeps working when the foreground window is elevated. F1 runs BOTH paths
  (`panic_exit` is a hard `os._exit`, so a double fire is harmless); F2 runs ONLY the polled one
  and is edge-triggered, because toggling is not idempotent and two paths would cancel each press
  out. Panic HAD this redundancy until 2026-08-27, when it was unified to one mechanism for
  symmetry with pause — **that unification is what removed the protection this failure needed.**
  Do not re-unify them.

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
- **Routing, doors and walking -- the rules.** (The measured history behind every one of
  these -- door flag tables, Barbara's/TAU's/ROSS's/NORMAN's live geometries, the Shortest
  Path era -- is **`docs/routing-and-doors.md`**; read it before changing `travel.py` or
  `worldmap.py`.)
  - `worldmap.py` (offline per-edge collision map, all four planes, ~ms per route) is the
    ONLY routing engine -- Shortest Path is gone. It has NO transport graph (a routine owns
    its own teleports) and bakes doors OPEN, deliberately: route first, then
    `travel.open_door_toward(ctx, goal)` finds and opens whichever shut door the route
    actually crosses (`first_door_on_route`/`blocked_crossing_on_route`).
  - The static map cannot tell furniture from a wall, so object-interaction adjacency
    ("can I click this from here") stays on LIVE collision flags: `travel.can_reach` /
    `reachable_set` / `interaction_tiles`. Do not move it onto the map.
  - Doors: collision flags are ground truth and the useful question is per-EDGE. A shut
    door's flags are identical to the wall beside it, so `actions` ("Open" vs "Close")
    identifies a door, never flags -- and never compare two DIFFERENT doors' flags to infer
    state. A door is not always a WallObject: a diagonal door is a GameObject blocking its
    whole TILE both open and shut. Opening moves the leaf and CHANGES the object id.
  - Opening a door is CONFIRMED (the blocked edge cleared, or the player crossed it --
    some doors swing shut behind you), never slept off, and the budget includes the WALK to
    the door; the click passes `refresh_target` so the aim tracks the approach.
  - `walk_to` re-routes around tiles live collision refuses (`find_path(avoid=)`,
    `WALK_MAX_REROUTES`) -- EXCEPT a shut door standing on the route, which is to be
    OPENED, never routed around (routing around it can leave no route at all).
  - ADJACENCY IS NOT INTERACTABILITY: `goal_adjacent=True` can park you on a walled-off
    neighbour. Walk to `travel.interaction_tiles(goal)` (tiles with an OPEN EDGE to the
    target, from live collision); empty means "cannot tell", fall back rather than refuse.
  - MINIMAP for distance, viewport only for short precise hops (`HOP_VIEWPORT_MAX_TILES`),
    and every viewport walk click is SHIFT-clicked. The NEXT hop is clicked BEFORE arrival
    (`HOP_NEXT_CLICK_LEAD_TILES`) so the player never dead-stops at hop boundaries. The
    minimap reaches 28 tiles (its DIAGONALS bound it; it shrinks if the user zooms it) and
    the ORBS drawn over the circle eat clicks -- `minimapOrbs` on `/state`; `_hop_to`
    rejects a covered/unreachable aim point and pulls in toward the player.
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
- **`GET /slayer` (2026-09-18) is the ONLY reader of slayer task state.** varps 394 (kills
  remaining) / 395 (task id) / 4258 / 2096 and varbits 4067 (master) / 4068 (points) / 4069
  (streak) / 4723, decoded through DB tables 113/115/116 exactly as RuneLite's own
  `SlayerPlugin.updateTask()` does. Completion is `count` going to 0 -- the plugin no longer
  parses chat for it, and neither do we. `taskName` is `null` (not `""`) when the DB lookup
  failed, so "no task" and "could not decode" stay distinguishable. An un-rebuilt client 404s
  it (`/slayer-config` is not a path prefix of `/slayer`), and `controller.get_slayer_task()`
  returns `None` -- a routine must do NOTHING on `None`. `GET /varp?id=` is the varp twin of
  `/varbit`, same null-means-unreadable rule.

## Routine status index

Per-routine live-session history, measured facts and open worklists live in
**`docs/routine-notes.md`** — read a routine's section there BEFORE working on it;
those facts were measured live and must not be re-derived. Cross-references in this
file of the form "see the X.py section/notes" point there. One-line status:

| Routine | Status |
|---|---|
| `corrupted_gauntlet.py` | Lobby -> gather runs unattended (live 2026-09-06); craft/enter/fight phases written but UNRUN |
| `guardians_of_the_rift.py` | Full rounds unattended; beat the human benchmark (run 75). Event-driven rewrite of 2026-09-04 not yet run live |
| `deep_sea_trawling.py` | NOT run live; needs client rebuild. Banking is an unbuilt sub-project |
| `wintertodt.py` | NOT run live; needs client rebuild |
| `vale_totems.py` | At human parity live (2026-09-10); magic-log hold pass partially verified 2026-09-11; banked pre-fletched decorations added 2026-09-16, NOT run live |
| `granite_3t.py` | Cycle settled from recordings 2026-09-09; NOT run live in that form |
| `mining_guild_iron.py` | Live-debugged to 1674 ore/hr (2026-09-11); 52-min unattended run clean |
| `blast_furnace.py` | Live: gold 24-25s/trip, adamantite 2-trip cycle ~48s (2026-09-04) |
| `giants_foundry.py` | Full loop unattended live (2026-09-03/04), incl. metal substitution |
| `mastering_mixology.py` | Was working live 2026-08-13; 2026-09-06 event-driven rewrite NOT yet run live |
| `motherlode_mine.py` | Whole loop verified live 2026-09-07 (mine/hopper/sack/bank); repairs and breaks unrun |
| `mining.py` / `woodcutting.py` (`gathering.py`) | Shared loop; mining live-proven, woodcutting first live run OK 2026-09-08 |
| `shipwreck_salvaging.py` | Last live 2026-08-05; three offline passes since NOT run live, needs client rebuild |
| `agility.py` | Written from a recording 2026-09-02; NOT run live |
| `falconry.py` | Written from a recording 2026-08-23; 2026-09-06 event-driven pass NOT run live |
| `golem_crafting.py` | Was running unattended live; 2026-09-06 event-driven + falconry fur-trip rewrite and 2026-09-14 single-tile mining rewrite NOT run live |
| `karambwan_fishing.py` | Full loop verified live 2026-09-06; later left-click/AFK passes NOT run live |
| `combat.py` (`combat_core.py`) | The old "Slayer / Combat": bank-trip return rebuilt 2026-09-07, pinned in tests, NOT run live; split out 2026-09-18 |
| `slayer.py` | STUB (safety-stops, points at the 2026-09-18 spec) until the task orchestrator ships; `GET /slayer`, `/varp`, `dialogue.py`, `legs.py`, `gear.py` are built |
| `chinchompa_hunting.py` | Working live 2026-08-21; known 7% collapsed-trap waste left deliberately |
| `mahogany_homes.py` | Extensively live-debugged (~40 contracts/hr over 7h, 2026-08-30) |

## Arduino firmware protocol (v3)

`firmware/Firmware.ino` speaks protocol v3 as of 2026-08-27 (see its own header block for the
full derivation). Purely additive over v1/v2 — a v2 firmware silently ignores every v3 command,
so either side can be upgraded first. The new commands: `X` flush (discard every queued
command now; the active segment always finishes), `V` version query (responds `HB 3`), `R`
reset (flush including the active segment, release every held button/key), `W <n>` scroll wheel.
Plus a 60s stuck-input watchdog: if any button/key is held with no serial line arriving for 60s,
everything is released — the panic path is a hard process kill that cannot send key-ups, so this
is the only thing standing between that and a button held forever.

**`KT`/`KD`/`KU` UPPERCASE every single letter** (`keyFromName` maps `a-z -> A-Z` before
`Keyboard.write`), so anything typed through `type_text` arrives uppercase — "adam" -> "ADAM".
VERIFIED ON HARDWARE 2026-09-07 by typing into Notepad in the OSRS VM (confirmed the whole
`KT`/single-letter path works, which `type_text`'s docstring had flagged unverified). Harmless
for the bank search box (case-insensitive) and chat; it would BREAK a case-sensitive field, so
a typed username/password login must never rely on it (we sign in from credentials.properties
instead). Fix belongs in the firmware if a case-sensitive need ever arises.

**The contract that matters most: `X` drops queued button/key commands too, not just motion, so
it is only ever legal from inside `move()`'s flight loop, where nothing but segments are queued.**
A future agent adding a serial write from a background thread, or a drag primitive, has no reason
to look in `human_move.py` for this — the constraint lives in the firmware's own comment and here,
not in any type signature. Sending `X` while a click or key-down is queued would silently discard
it.

`controller.FIRMWARE_PROTOCOL` (set once at startup by `probe_firmware_protocol`) gates every v3
feature, and it is a hard probe rather than graceful degradation on purpose: an ignored `X` on a
v2 board doesn't fail loudly — the queued motion just plays out anyway while `move()`'s Python-side
bookkeeping (`cur`/`queued_ms`) has already rewound as if it didn't. That produces silently wrong
clicks (aimed from a stale position estimate), not a visible failure, which is a far worse outcome
than refusing to use `X` at all against an unprobed or pre-v3 device.

`ControllerAddon/firmware_bench.py` is the flash-day checklist — run it after every reflash before
trusting the device. It opens the same COM port `controller.py` does, so **never run it alongside
controller.py** (same one-process-per-port constraint as the general Arduino serial port gotcha
above).

**VERIFIED ON HARDWARE 2026-08-27, first flash: `ALL PASS`, including `--watchdog`.** So every v3
firmware command has now actually executed on the device — `V` answered `HB 3`, `X` cut a 375px
uninterrupted travel down to 112px (fraction 0.30 against a 0.50 bound) with 0px of drift after,
`R` released a held SHIFT, and the 60s watchdog released one on its own after 65s of silence.
Note the bench's flush check is deliberately scale-free (it measures an uninterrupted baseline in
the same run and bounds the flushed travel as a fraction of it) because `S` moves raw HID mouse
units, not pixels: an earlier absolute-pixel window PASSED on a device where `X` did nothing at
all. The 375px over 500 units also puts this machine at 0.75 px/unit, within 2% of the 0.737 in
`.hm2_calibration.json`. What is still unverified is the ROUTINE-level behavior, not the firmware:
no live session has yet run a moving-target routine with flushing enabled. Success signal to watch
for is `_settle_on_target_before_click`'s corrective move firing less often; the failure signal is
any cursor stall on a flushed flight, which would mean the reduced lookahead described below is
biting in practice.

**Open follow-up (not yet verified live):** `HM2_FLUSH_COMMIT_MS` (22.0, in `settings.py`) plus
the rewind's "first emission at or past `commit_ms`" selection rule double-count the active
segment, so the rewind lands one-sidedly ~18ms / one segment AHEAD of what the firmware actually
played (reproduced deterministically on 8/8 seeds against the test rig, 2026-08-27). Left alone
deliberately, pending real hardware measurement — see the ruling in
`docs/superpowers/plans/2026-08-27-firmware-v3-flush.md`. The direction is benign (the next replan
undershoots into the residual corrections rather than overshooting), it does NOT accumulate across
multiple flushes within one flight, and the second-order cost is real: firmware buffer depth drops
from 54ms to ~36ms for the remainder of a flushed flight only, which the firmware's own
`CHAIN_MAX_LAG_US` (50ms) absorbs without a phase restart. The next live session after a flash
should measure the real post-flush commit latency; the candidate one-line fix, if it's warranted,
is to select the LAST emission with `t_ms <= commit_ms` instead of the first one at or past it —
that restores exactly the semantics `settings.py`'s own comment originally claimed. Do not change
`HM2_FLUSH_COMMIT_MS` or the selection rule without that measurement in hand.

## Sub-tick data and clicking while moving

Full history (the refresh_legs design, movement-realism fitting sessions, fidget/idle
tuning, aiming hardening): **`docs/movement-and-clicking.md`**. The working rules:

- Most game state is capped at OSRS's 0.6s server tick, but SINGLE-ENTITY lookups
  (`/npc/by-index`, `/npc/nearest`, `/object/at`, `/ground-item/at`, `/player`, `/combat`)
  are never tick-cached and read the client's per-frame interpolated position fresh. Only
  the bulk list endpoints go through `TickCache` (`?fresh=true` bypasses it).
- **The PREFERRED pattern for every new click site** (user directive 2026-08-23): fire the
  walk WITHOUT waiting for arrival; click the target a beat later passing a
  `*_refresh_target` factory + `refresh_legs` (the aim re-resolves per leg while the
  player and camera move); confirm the outcome on a pushed/un-gated signal (XP_GAINED, a
  varbit, a state endpoint), never a fixed sleep. MEASURED from a human recording: they
  click the object 0.6-1.0s after the walk click, mid-walk -- the game queues the
  interaction and performs it on arrival, so waiting for stillness is pure added latency.
  The legacy `wait_until_player_idle()` pattern is safe but slower; leave existing
  blocking call sites alone unless their throughput is actually a problem. The endpoints
  already exist and are un-cached -- no Java change needed to adopt this.
- **A click's DEPARTURE is aimed through `refresh_target` too** -- passing one is the
  whole fix at a call site. Anything NOT passing one aims at whatever snapshot it was
  handed: re-resolve through `/object/at` / `/npc/by-index` immediately before clicking.
- **`click_menu_action` right-clicks exactly the pixel you give it.** It takes
  `refresh_target` (forwarded to `click_entity`); without one, re-resolve first -- a
  tick-old object dict right-clicks bare ground. Pass `offset=(0, 0)` for small targets
  (the default `(-7, +7)` nudge slides off a plinth).
- **Verify-then-click via `GET /hover`**: `click_entity(..., expected_action=...,
  expected_target=...)` reads the default left-click action under the cursor before
  committing; a withheld click returns the falsy `CLICK_WITHHELD` sentinel
  (`getattr(result, "withheld", False)` separates "pixel contested" from "aim failed"),
  and `hover_matches` never blocks when it cannot verify. The gate reads the cursor's
  position RIGHT NOW, so NEVER put it on clicks issued while still walking up to the
  target (the Gauntlet and Motherlode both paid for this).
- `_settle_on_target_before_click` takes one last corrective look before mouse-down
  (skipped during a simulated misclick, on purpose); a click already on its target is
  made IN PLACE (`CLICK_IN_PLACE_RADIUS_PX`) instead of re-flying.
- **XP gain is a sub-tick, general-purpose success signal**: `<skill>Xp` fields on
  `/player` (add more in `PlayerProvider`; client rebuild required). Snapshot before the
  click, poll for the increase inside the existing wait -- proof the skill check
  succeeded, faster than chat or tick-capped state.
- **Movement realism is FITTED from human recordings, never reasoned** -- session tempo
  drift, click holds, in-wait fidgeting (`controller.fidget_once()` from routine-side
  waits; the fidget returns NEAR its anchor, never to the exact pixel -- an exact return
  is itself a tell), idle drift floored at `IDLE_MOVE_MIN_STILL_S` (the human is never
  still >4.5s). Validate any humanization change by comparing recordings of the SAME
  activity human-vs-bot; the routine log cannot see this class of signature. Shared input
  state lives in `input_state.py`, never module-level in controller.py (which is loaded
  twice -- see the gotcha).

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

**`dropped` means records evicted BEFORE the reader took them, since 2026-09-05.** The first
Corrupted Gauntlet recording (26695 records, seq 0..26694 verified gap-free, the whole Hunllef
fight present) ended with `!! 6695 records were dropped` -- exactly 26695 minus the 20000-slot
ring, because eviction counted whether or not recorder.py had already drained the record. Any
recording longer than the ring raised a false "this recording has a gap". `RecordRing` (extracted
from `RecorderProvider`, pinned by `RecordRingTest`) tracks the highest seq handed out and only
counts an eviction past it. **Before believing a drop warning, check `seq` continuity in the
`.jsonl`** -- a real hole is a gap there, nothing else is.

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
recording.

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

## Bank memory (`GET /bank/memory`, `ControllerAddon/bank_memory.py`)

Added 2026-09-07 (USER: "we can actually see what teleport items we have in bank by storing a
snapshot of our bank locally ... means we can poll which best method to travel back with").
Modelled on the Plugin Hub **Bank Memory** plugin (Lazyfaith/runelite-bank-memory-plugin), whose
mechanic is copied rather than reconstructed. **Sub-project 1 of 2: the travel chooser that
consumes it is NOT built.** Needs a client rebuild; no routine reads it yet.

**Why this is not `/bank`.** `BankProvider` builds its item list from `BANK_ITEM_CONTAINER`'s
dynamic children and skips `child.isHidden()`, so it can only report what the interface is
DRAWING: items on a numbered tab you are not viewing are hidden, and so is every non-matching
item while a bank **search** is active. `giants_foundry.py` searches `"adam"` before withdrawing,
so for as long as that search is up `/bank` describes a bank containing only adamant items. The
vendored m8aq `storage/bank` reader has the same limitation from the other direction
(`containerLoaded` false once the interface closes). The `InventoryID.BANK` **ItemContainer**
that `BankMemoryProvider` subscribes to has neither problem: it is the true contents, every tab,
whatever the interface happens to be showing.

- **The capture is one subscriber and it is free.** `onItemContainerChanged` filtered to
  `InventoryID.BANK` fires on every open, deposit and withdraw. Two consequences worth knowing:
  the memory **self-heals during a banking trip** with no invalidation logic anywhere, and it
  records the user's own manual banking whenever the client is running -- `controller.py` does
  not have to be.
- **PLACEHOLDERS ARE THE TRAP, and the check is not obvious.** A bank placeholder sits in the
  container with quantity 0 under an id that **canonicalizes to a different id**
  (`itemManager.canonicalize(id) != id`), so without the check a placeholder left by an item you
  ran out of still answers "yes, you have one" -- and a routine would withdraw nothing, then try
  to teleport home on an empty slot. Taken verbatim from `BankSave.fromCurrentBank`, along with
  the `-1` / `BANK_FILLER` filter. Quantity 0 is also dropped outright.
- **`BankSnapshot` is a pure value with no client references** so the filtering rules are
  unit-testable without a client (`BankSnapshotTest`) -- the same extraction `RecordRing` got out
  of `RecorderProvider`, for the same reason.
- **"Never seen" is NOT "empty", and the distinction is load-bearing.** A bank we have never
  opened answers `seen=false, items=null`; a genuinely empty one answers `seen=true, items=[]`.
  `bank_memory.quantity()` returns **None** for the first and **0** for the second. Collapsing
  them lets a routine conclude it owns no teleport when it has simply never looked. Same rule the
  m8aq readers already draw.
- **Serving it touches neither the client thread nor `TickCache`** -- the snapshot is
  already-resolved plain data, so unlike `/bank` it is cheap to poll. Persisted to
  `~/.runelite/bridge-bank-memory/<accountHash>.json` (same convention as
  `ScreenshotProvider`'s `bridge-screenshots`), written on a daemon executor behind a dirty flag
  with an atomic tmp+move, never on the client thread: a bank trip fires the handler a dozen
  times in seconds and this user's bank is ~950 items (~60-80KB). `WRITE_DEBOUNCE_MS` (3000) is a
  FIRST-DRAFT GUESS wanting one live session to fit. A null account hash (logged out, or -1) is
  kept in memory and never written, so a file can never be persisted under the wrong key.
- **`unregister()` is called from `ControllerPlugin.shutDown`** -- most providers here never
  unregister, which is exactly the leak this file already records biting `TickClock` (every
  disable/enable cycle left another subscriber alive). This one owns a subscription AND a writer
  thread, so it is held in a field.
- **An UNREBUILT client answers `/bank/memory` with `{"open": false}`, not a 404** -- MEASURED
  live 2026-09-07 before the rebuild. `com.sun.net.httpserver` matches by LONGEST PREFIX, so with
  no `/bank/memory` context registered the request falls through to `/bank`'s handler and returns
  a 200 of the wrong shape. `/widget` + `/widget/children` were confirmed coexisting on the same
  live bridge, so once registered `/bank/memory` wins the match. The wrong-shaped 200 reads as
  "never seen" rather than "empty bank", and that is pinned by test.
- **Python: one fetch, many questions.** `bank_memory.load()` makes a single HTTP call and
  returns an object answering `has()`, `quantity()`, `find()`, `seen`, `age_s`; asking about
  twelve teleport names must not cost twelve round trips. `load(names=[...])` pushes the filter
  to the bridge for a narrow repeated poll, and `itemCount` deliberately stays the size of the
  WHOLE bank so a filtered reply can never be mistaken for a small one. Name matching is
  case-insensitive SUBSTRING, matching `banker.py`'s `keepItems` convention -- with the trap that
  `"teleport"` matches every teleport and `"rune"` matches runite bars.
- **Staleness policy lives with the caller**, deliberately: the provider only reports *when* it
  captured. `python bank_memory.py [name ...]` is the read-only live probe (imports
  `controller` only, never the serial port, so it is safe alongside a running routine).

**First live session's checklist** -- nothing below has been run against a real client, and #3 is
the headline claim:
1. `ItemContainerChanged` fires for BANK on open, and again on every withdraw.
2. The snapshot holds items from ALL tabs while a single numbered tab is displayed.
3. The snapshot survives an active bank **search** (search `"adam"`, confirm everything is still
   there) -- this is the whole correctness argument over `/bank`.
4. A placeholder for an item held 0 of does not appear.
5. A fresh client reloads the file and answers correctly BEFORE the first bank visit.
6. `client.getAccountHash()` is non-null and stable on this setup.

## Teleporting home from a bank trip (`ControllerAddon/teleports.py`)

Full build history, the corrected single-hop -> graph design, live verification and the
first-session checklist: **`docs/teleports.md`**. Shape:

- Shortest Path's own transport TSVs are vendored byte-identical
  (`ControllerAddon/vendor/`, header names ASSERTED since column order differs between
  files): teleportation items, jewellery boxes, POH portals. Item rows key on ITEM IDS,
  which joins directly with bank memory; the Items column is pure OR (charge variants of
  one item); wilderness level is filtered (a teleport does not fire above its own level,
  and Krystilia tasks make this a common case, not a corner).
- **`teleports.py` is PURE** -- no HTTP, no client state, no execution. `plan(start, goal,
  owned_ids, route_len, ...)` is a bounded BFS over hops (`MAX_HOPS` 2) returning legs --
  house-tablet -> POH -> jewellery-box chains included. Varbit gates (house location 2187,
  inside/outside 4744) FAIL CLOSED on unknown; an ornate box disables the mounted glory
  (ported from the decompile). Walking is the default: a teleport must beat the walk by
  `MIN_SAVING_TILES` after `TELEPORT_COST_TILES` -- both first-draft guesses.
  `unreachable()` is a separate question from `plan()` returning None (walk vs.
  stop-and-say-so). The no-data VOID is one connected walkable component, so approach
  walks are bounded (`APPROACH_MAX_TILES`/`APPROACH_MAX_NODES`/`GOAL_FLOOD_RADIUS`) --
  without them one decision cost 106s and invented routes; with them 9.9s, VERIFIED LIVE.
- **Wired into `banker`**: the plan is made INSIDE the open bank
  (`_withdraw_return_teleport`), executed by `_use_return_teleport` (which pops the plan
  itself -- pinned by test), the landing CONFIRMED not slept off; any failed leg falls
  back to the walk home, which is the whole safety argument. Every routine opting into
  `returnToSpot` gets it. POH contents come from the bridge's `poh` config block
  (Controller Settings, everything OFF by default -- an unconfigured account never routes
  through a house).
- **Ferox Enclave is a sealed 413-tile island on the collision map** (~30 of 210
  destinations similarly enclosed) -- walking home from there has been impossible since
  worldmap replaced Shortest Path, which is why slayer's bank trips stranded there.
- **The actual CLICKING of a box/portal leg has never run live** -- the destination picker
  is answered by matching the TSV's destination name in the widget dump, unproven. Fairy
  rings/spirit trees/gliders are not vendored yet; `plan()` runs once per bank trip only.

## Clicking interface elements (`/widget`)

`WidgetProvider.java` + `controller.get_widget(group, child, index=None)` (added 2026-08-23) give a
clickable screen position for **any** interface element by group/child id. Before it, the only
reachable interface elements were the handful `BankProvider` hard-codes — and `/equipment` reports
what is *worn* but carries **no screen position**, so a routine physically could not click a worn
item. That blocked `routines/falconry.py`, which at the time had to un-wield a Dramen staff after
a fairy-ring hop because the falconry glove needs the same weapon slot (the staff requirement is
gone from the game and from the code since 2026-09-06; worn-item clicks through this endpoint are
now what `karambwan_fishing.py` uses for its two worn teleports).

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

## Building a routine with a subagent-driven plan (the Gauntlet, 2026-09-05)

The Corrupted Gauntlet was built in one day as three spec -> plan -> subagent-executed
sub-projects (~85 commits), Haiku implementers, Sonnet per-task reviews, Fable whole-branch
final reviews (USER: "do fable final review"). What the process itself taught, so the next one
does not re-learn it:

- **Plan-authored tests were wrong more often than the code they tested.** Every one of these
  was a test asserting a plausible fact the spec's sources contradicted: OR-logic diagonals for
  walking (`e3eff2f`), 50 shards for a basic weapon (`9e1c9ff`), a flood-fill claim about an
  8x8 corner (`39da441`), a start-centre fixture that was not walled and so flooded the whole
  grid (`92d2872`), a hop test that passed with the walker doing nothing, and a bowl order the
  code never promised. **When a test and a MEASURED fact disagree, check the wiki/recording/cache
  before changing the code to satisfy the test**, and record the ruling in the plan's ledger.
- **Implementers deviate in patterned ways; the task review is what catches them.** Seen here:
  relaxing a geometry rule to make a test pass; "efficiency" cascades that run several sub-state
  transitions in one call (`3b0181f` restored one transition per call -- the cascade executed
  `bowl_make` before the player reached the bowl); adding a plausible precondition the spec never
  stated (the shard gate); an `IdentityHashMap` that kept every projectile alive for the process
  lifetime (`b19ea69`, Guava `MapMaker().weakKeys()` instead). Brief the reviewer on the spec's
  facts, not just the diff.
- **Never run two implementers in the same checkout, and commit before dispatching one.** Two
  agents share one git index; and an implementer told to "leave the tree clean" reverted an
  UNCOMMITTED edit of mine to the recorder ("restored to committed state"), which then had to be
  redone (`8b0a7cc`). Uncommitted work in the tree while an implementer runs is work at risk.
- **Final whole-branch reviews found what task reviews structurally cannot**: every Critical in
  the three final reviews was a cross-task interaction (the `mark`/`since` event loss between
  the tick wait and the read, `pick_safe_tile`'s stomp guard blocking traversal, `craft_plan`
  re-queueing done steps every tick, `before_break` teleporting a mid-run instance home). Budget
  a final review per sub-project and a fix wave after it; the fix wave itself introduced two
  bugs (`7f055e0`), so re-review the fix wave too.
- **Everything here was reasoned from a recording and the cache, none of it run live.** The
  first live session is the three specs' checklists in order, and the expected outcome is that
  geometry facts (room pitch, size-5 NPC centre-vs-corner) and widget fields are wrong before any
  logic is.

## Live-debugging workflow

Use this loop by default when debugging a routine live against the running RuneLite client — it
was confirmed to work well over an hours-long session that took `golem_crafting.py` from broken
to reliably running unattended.

1. **Check for a serial-port conflict first.** Confirm no stray process still holds the Arduino:
   `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"`.
2. **Launch the routine in the background, unbuffered, logged to a file in `logs/`:**
   `python -u controller.py routines/<name>.py *> logs/<logfile> 2>&1`. Live-session logs
   (`mh_live*.log`, `recorder_run*.err`, ...) belong in `ControllerAddon/logs/` (gitignored,
   same place `debug_log.py` writes its own `debug_*.log`), never loose in `ControllerAddon/`
   — 159 of them were swept into `logs/` on 2026-08-31.
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

