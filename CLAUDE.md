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
- **Routing engine replaced 2026-08-29: Shortest Path (the separately-installed RuneLite plugin)
  is gone, along with roughly 700 lines of door/reachability heuristics it made necessary.**
  `ControllerAddon/worldmap.py` — an offline, world-wide per-edge collision bitmap covering all
  four planes, ported from EthanApi's `GlobalCollisionMap` — is now the **only** routing engine.
  `travel.route_to`/`travel.walk_to` call `worldmap.find_path` directly (0.4-3ms for an in-house
  route, 151ms for a 314-step cross-city one), no bridge call needed for the route itself, and it
  fixes the exact gap Shortest Path had: it routes on ANY plane, where Shortest Path's own dumped
  collision map was ground level only (see the NORMAN's/JEFF's/ROSS's measurements below, and the
  mahogany_homes.py section for the live session that forced this). The static map bakes doors
  OPEN, deliberately, same as Shortest Path's own dump was (`CacheMapDumper`'s `Exclusion` list
  does it on purpose) — so a computed route runs straight through a doorway, and
  `travel.open_door_toward(ctx, goal)` (built on `first_door_on_route`/`blocked_crossing_on_route`)
  finds and opens whichever shut door the route actually crosses. That one call replaced the
  three-tier escalation described in the bullets below (`_door_separates`/`_within_span`,
  `open_frontier_door`, `open_nearest_shut_door`) — those tiers existed only because there was no
  route to ask; given a real route the door question is nearly trivial and works identically on
  every floor. **What did NOT move onto the static map:** object-interaction adjacency ("can I
  click this hotspot/ladder from here") is still answered from LIVE `GET /collision` flags via
  `travel.can_reach`/`travel.reachable_set`, because the static map bakes an object's own blocking
  into the same bits as a wall's and structurally cannot tell furniture from a wall — see JEFF's
  measurement in the mahogany_homes.py section, still the reason. **Transport execution is gone
  too, not rebuilt:** `worldmap.find_path` has no transport graph at all, so `route_to`'s own
  docstring says plainly "no transports" — a routine that needs a teleport now owns that itself
  (`mahogany_homes.py`'s `CITY_TELEPORTS`, or the standalone `teleport_to_ferox`/`use_fountain`
  already in `travel.py`). The bullets below describe the Shortest-Path-era machinery as it was
  measured and built; keep reading them for the MEASURED game geometry (door ids, coordinates,
  collision flags), which is still true of the game — not for the function names, which have moved
  onto `worldmap.find_path`/`travel.open_door_toward`/`travel.first_door_on_route`/
  `travel.blocked_crossing_on_route`.

- **Historical, pre-2026-08-29 — Shortest Path's own transport graph could route through a
  transport we had no way to execute.** `_use_transport` could only click a single menu option on
  an object or NPC; any teleport, or any transport opening a destination picker (canoes, charter
  ships, minecarts), produced an edge with no clickable `objectId`. CONFIRMED live: a `walk_to`
  from Civitas illa Fortis once returned a route whose first edge was a `QUETZAL_WHISTLE` to a tile
  **454 tiles away**, purely because the user's own Shortest Path panel had quetzals enabled —
  `travel.TRANSPORT_POLICY`, sent with every request, was the fix at the time. Moot now: the new
  routing engine has no transport graph to leak a bad edge from in the first place, and Shortest
  Path's separate sticky, session-global config-override bug (a quirk of that plugin's own
  `PluginMessage` handling) went away with the plugin.

- **Doors: `collisionFlags` + `GET /collision` are the ground truth, and the useful question is
  per-EDGE, not per-door.** `ObjectProvider` reports `collisionFlags` on every object (the client's
  own `CollisionData` entry for that object's tile) and `CollisionProvider` serves
  `GET /collision?x=&y=&plane=&radius=` for ARBITRARY tiles, including empty ones -- needed because
  the doorway you are trying to walk through usually has no object on your side of it.

  MEASURED live by opening Barbara's two doors one at a time and re-reading the SAME doorway:

  | | shut | open |
  |---|---|---|
  | outer | id 7452 @ (1746,3534) `mE\|sE` (4104) | id **7453** @ (1747,3534) `mS\|sS`, doorway tiles -> **0** |
  | inner | id 7452 @ (1748,3537) `mS\|sS` (16416) | id **7453** @ (1748,3536) `mW\|sW`, doorway tile -> **0** |

  Five facts, all confirmed twice and still exactly what `travel.py`'s `DOOR_BLOCKING_FLAGS`/
  `DOOR_EDGE_BLOCKS` encode:
  1. Movement and line-of-sight bits are ALWAYS set together for the same edge (`mE|sE`).
  2. A blocked edge is recorded on BOTH its tiles as opposite directions -- it is a genuine EDGE,
     not a tile property, so either side is enough to know the crossing is shut.
  3. Opening a door CLEARS the doorway edge outright (to 0) and the leaf comes to rest blocking a
     PERPENDICULAR edge.
  4. The door object MOVES one tile and CHANGES ID when it opens (7452 shut -> 7453 open).
  5. A shut door's flags are identical to the plain wall segments beside it (Barbara's read 4104
     either side), so collision can never IDENTIFY a door -- `actions` ("Open" vs "Close") does
     that, and always did.

  **Do not compare two different doors' flags to infer state** -- an earlier pass did exactly that
  at Lumbridge (a shut door and a different open one both read 1026), concluded the flags carried
  no state, and wrote that up as fact. 1026 was the open door's LEAF edge. Comparing one doorway
  before and after is the only comparison that answers anything.

  What the code actually asks is narrower and needs no classification at all
  (`travel.blocked_crossing_on_route`/`first_door_on_route`, which replaced `_door_separates`):
  **is there a step of the computed ROUTE we cannot cross, and is a shut door standing on it?** A
  shut door across the path answers yes; the same door once open answers no, for free -- and
  because the question is asked against a real route rather than a straight-line comparison, it no
  longer misses a door that is off to the side or behind a detour the way the old single-axis
  `_door_separates` test structurally could (this used to need a separate escalation to a computed
  path for exactly that case; a route is now asked for up front, every time). Two traps worth
  knowing: `BLOCK_MOVEMENT_FULL` (2359552) is a COMPOSITE of `FLOOR | FLOOR_DECORATION | OBJECT`,
  so ORing it into a mask makes a bare floor decoration read as an obstruction (caught on
  Lumbridge's Trapdoor, 262144) -- `DOOR_BLOCKING_FLAGS` omits it; and an absent `collisionFlags`
  means "cannot tell", never "no".

- **A door is NOT always a WallObject, and not every door blocks an EDGE.** MEASURED live
  2026-08-29 at TAU's house, Falador, from the tile the routine was stuck on:

      Door id=24050 type=GameObject (3046,3348) d=1 actions=['Open'] flags=131328  <- the way in
      Door id=24050 type=WallObject (3046,3353) d=4 actions=['Open'] flags=4104    <- unrelated

  Same id, different object type -- `travel.DOOR_OBJECT_TYPES` is `("WallObject", "GameObject")`
  for exactly this (the name + "Open" action filter is what keeps it tight: nearby Drawers also
  offer "Open" and are excluded by name). Its collision is different in KIND too: `131328 =
  BLOCK_LINE_OF_SIGHT_FULL (131072) | BLOCK_MOVEMENT_OBJECT (256)`, with no movement edge bits at
  all -- a full-tile blocker like this separates only when its whole destination TILE is refused,
  which is what `blocked_crossing_on_route`'s destination-tile check (not just the edge test)
  catches; the route that exposed it ran (3045,3349) -> (3046,3348) -> (3046,3347) -> (3046,3346)
  straight through the door tile. The line-of-sight bit is the only flag family that marks this
  door at all, which is why LOS is in the mask and not just movement.

- **A hotspot's or ladder's own tile is usually not routable at all** (furniture stands on it),
  and aiming a route at the nearby standing tile instead can hide the doorway you actually need,
  because both the standing tile and the player often sit on the same side of the door. MEASURED
  live at Barbara's: the hotspot tile (1749,3536) itself reported unroutable (furniture) on 5 of 5
  checks while its neighbours routed fine. `travel.route_to(goal, goal_adjacent=True)` -- which
  `open_door_toward` always passes -- routes to a tile BESIDE the target rather than onto it, so
  the door question is resolved against the actual goal (not a nearby stand-in) by construction.
  The same TAU session found `standingLocation` itself can be unwalkable (furniture on
  (3047,3345)) -- another reason routing is asked for against the real goal, not a fallback tile.

- **Historical: three separate heuristic bugs at NORMAN's house (Falador), 2026-08-29, all fixed
  by moving to real routes rather than by patching the heuristics further.** A radius-limited door
  search missed doors standing 6-7 tiles away; a single-axis "is this edge between us and the
  goal" test false-positived on an unrelated `Door 24057` at (3027,3344), seven tiles off the
  direct line; and a walk aimed at `standingLocation` (3038,3344) crossed no doorway because it
  sat on the SAME side of the shut door 24050 (3037,3347) as the player. None of these failure
  modes exist against a real BFS route: `DOOR_ROUTE_LOOKAHEAD` (14 route steps) has no fixed-radius
  blind spot, and a route that actually reaches the goal is definitionally on the correct side of
  every door it opens.

- **Opening a door is CONFIRMED, not slept off, and the confirmation must budget the WALK to the
  door as well as the open itself.** A door is a WallObject whose id encodes its state and changes
  when it swings (shut -> shut+1, e.g. Barbara's 7452 -> 7453, Falador's 24050 -> 24051) and it
  also moves a tile, so the object you clicked is not there afterwards to re-read -- the doorway
  is, so `travel._open_door` polls the blocked EDGE until it clears (`DOOR_CONFIRM_POLL_S`, budget
  `DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S` capped at `DOOR_OPEN_WAIT_MAX_S`) and logs
  `still shut after Ns -- another player may be holding it, or the click missed` otherwise. MEASURED
  live at ROSS's: a flat 2.0s budget was less than the ~2.4s of walking to a door four tiles off,
  so a click that DID register as `Open Door` on id 40178 got reported as a failure and interrupted
  by its own retry, three times over -- fixed by adding the per-tile walk allowance. Also MEASURED
  at ROSS's front door (40178): it swings shut BEHIND you, so re-reading the doorway can answer
  "still shut" about a door that worked -- the confirmation also succeeds on `_crossed()` (the
  player having crossed the door's blocked edge since the click), because getting through is the
  outcome that matters, not the doorway's state at poll time.
  **PARTLY SUPERSEDED 2026-08-30.** A good share of those "still shut" timeouts was not the door
  swinging at all: `_open_door` clicked with a BARE `click_entity` and so aimed at the pixel the
  door occupied when the ROUTE was computed, while the player was still walking toward it. The
  recorder showed every click registering as `Open Door` because the MENU resolved correctly --
  but a stale aim still lands somewhere useless. It now passes `refresh_target`; see "A click's
  DEPARTURE is aimed too" above. Both effects are real; do not assume a "still shut" log means
  the door closed behind you until the aim has been ruled out.

- **ADJACENCY IS NOT INTERACTABILITY, and `goal_adjacent` does not give you a tile you can act
  from.** `worldmap.find_path(goal_adjacent=True)` stops at ANY tile cardinally adjacent to the
  goal — and it never has to cross a wall to get there, so the route is perfectly valid by the
  static map's own rules while the tile it lands on cannot touch the target at all. MEASURED live
  2026-08-30 at BARBARA's: the Bed hotspot at (1749,3539) has its north and east edges walled
  (16416 / 65664) and only its south and west open (135432 / 132354), both inside the bedroom.
  The walker parked on the north tile, `can_reach` went on (correctly) answering False, and the
  routine sat there until its ceiling stopped it. `travel.interaction_tiles(goal)` answers the
  question that actually matters — which tiles have an OPEN EDGE to the target — from live
  collision, and walking to one of those routes through the bedroom door, which
  `open_door_toward` then finds as an ordinary door on the route. Empty means "cannot tell";
  fall back rather than refuse to work.

- **A route can cross a tile that live collision refuses, so `walk_to` re-routes around it --
  EXCEPT when a shut door is standing there.** The static map is not live: it cannot know what is
  standing on a tile right now, and it bakes doors OPEN. MEASURED live 2026-08-30 at TAU's,
  Falador, where the door is DIAGONAL — which the game cannot express as a `WallObject` on a
  cardinal edge, so it is a `GameObject` blocking its WHOLE TILE both open and shut. OPENING it
  therefore does not clear a tile, it MOVES a full-tile blocker: door 24051 ("Close") sat on
  (3046,3347) reading 131841, the route ran straight through, `_hop_index` refused the first
  crossing and the walk died one tile from the door. `worldmap.find_path` now takes `avoid=` and
  `walk_to` feeds it tiles it has learned are really blocked (`WALK_MAX_REROUTES`, 4). The detour
  is normally free — at TAU's it was the SAME 51 steps.
  **The exception is load-bearing and cost a regression to find:** at ROSS's the only way in is a
  SHUT door at (2610,3316), and after the door budget was spent the reroute excluded that very
  tile, leaving no route at all — failing HARDER than before the reroute existed. A shut door is
  to be OPENED, never routed around. `_door_on_route` is the discriminator and it is exact: it
  only ever returns a door offering "Open", so TAU's open leaf ("Close") is avoided and ROSS's
  shut door is not.

- **MINIMAP for distance, viewport only for short precise hops -- and every viewport walk click
  is SHIFT-clicked.** User directive 2026-08-29. `_hop_to` used to prefer a viewport click
  whenever the tile was rendered, with the minimap only as a fallback beyond render distance;
  with `WALK_HOP_MIN/MAX_TILES` at 16-24 that made nearly every hop of a long route a viewport
  click into a scene full of interactive scenery. CONFIRMED live the same day
  (`recordings/2026-08-29_012522_mh-live-run12`): hops resolved as `Chop down` ("You need an
  axe") and `Cook` ("You haven't got anything to cook") instead of walking, which strands the
  player somewhere it did not choose and makes the next pass walk back -- the user saw this as
  clicking back and forth. The minimap has nothing drawn on it to hijack a click; precision is
  what it lacks, and precision is only wanted at the END of a walk (exact-tile work does not come
  through `_hop_to` at all -- `walk_to_exact_tile` handles that, and was the ONLY caller obeying
  CLAUDE.md's own shift-click rule). Now: `HOP_VIEWPORT_MAX_TILES` (6, first draft, wants a live
  tuning pass) decides, and `_click_walk_point` holds SHIFT for the viewport case.

- **The minimap reaches 28 tiles, and the DIAGONALS are what bound it.** MEASURED live 2026-08-28
  from (3037,3345) by walking `/minimap/walk` outward in all eight directions: `onMinimap` held to
  28 everywhere, then failed at 30 on the four diagonals while the cardinals were still on at 32 —
  a corner sits √2× further from the minimap's centre than a cardinal at the same tile distance.
  `WALK_HOP_MIN/MAX_TILES` were raised 10-16 → 16-24 on this measurement (user request: fewer
  stops between clicks, since each hop ends with the player standing still while the next click is
  aimed and flown). 24 keeps margin under 28 because the minimap's reach is a **user setting** —
  zooming it in shrinks it — and `_hop_to` now pulls an unreachable aim point in toward the player
  and retries (`HOP_PULL_IN_FRACTIONS`) instead of abandoning the whole walk, so a zoomed-in
  minimap degrades to shorter hops rather than a failed trip. Re-measure before raising further.

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

## Current state of routines/agility.py

Written 2026-09-02 from a human recording (`recordings/2026-09-02_032621_wilderness-agility.*`,
one lap) plus live bridge reads the same day. A generic lap engine over per-course data in
`ControllerAddon/agility_courses.py` (only WILDERNESS shipped; a rooftop is a data entry).
Design: `docs/superpowers/specs/2026-09-02-agility-runner-design.md`. **Not yet run live**;
the spec's "Live-verification checklist" is the first session's worklist — it needs one
deliberate ropeswing fail and one log fail to confirm the dungeon recovery walk-back.

Signals are un-gated on purpose (user directive): Agility XP from `/api/skills/skills`,
the game's own `busy` varbit 12393 (1 while an obstacle animates, 0 when done -- the signal
that confirms Rocks on a forfeited lap, where the 498.9 bonus is 0), the player tile from
`/player`. The step is re-derived from the player's tile every time
(`agility_courses.step_for_position`, disjoint START boxes pinned by test), so nothing
load-bearing lives in `ctx.state.data`. Steps are chained inside one `tick` (the human's
next click lands 0.2s after the previous XP drop; the engine loop's ~1.1s would triple
that). `pvpSafety.fleeEnabled` is False for this routine (crowded course, user decision).
Dungeon ladder pinned live: id 17385 at (3005,10363), `Climb-up`.

## Arduino firmware protocol (v3)

`firmware/Firmware.ino` speaks protocol v3 as of 2026-08-27 (see its own header block for the
full derivation). Purely additive over v1/v2 — a v2 firmware silently ignores every v3 command,
so either side can be upgraded first. The new commands: `X` flush (discard every queued
command now; the active segment always finishes), `V` version query (responds `HB 3`), `R`
reset (flush including the active segment, release every held button/key), `W <n>` scroll wheel.
Plus a 60s stuck-input watchdog: if any button/key is held with no serial line arriving for 60s,
everything is released — the panic path is a hard process kill that cannot send key-ups, so this
is the only thing standing between that and a button held forever.

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
  import without the Arduino keeps the old plain sleep. **CAVEAT FOUND 2026-08-30 and NOT yet
  fixed: `main()` runs in `__main__`, so it arms THAT copy's `_fidget_ser`, while a routine
  calling `controller.human_delay` reaches the other copy, where it is still `None`. So in-wait
  fidgeting has never actually fired for a routine-side wait** — see "controller.py exists
  twice", below. The live verification recorded below measured freeze TAILS, which this would
  not have shown. KNOWN BLIND SPOT, deliberate:
  routines' own 0.2-0.5s-per-iteration poll loops never enter this path; Phase B (a
  background fidgeter behind a global action lock) exists as a design only.
  **VERIFIED live 2026-08-27** (recordings/2026-08-27_023951_phase-a-verify, 3.8 active min of
  slayer): freeze tail dead (max 103s → 8.5s, p90 11.8s → 7.1s), settles firing correctly, no
  interference — but the residual p90 sat exactly at the then-4-9s idle-move timer, i.e. the
  TIMER was the new ceiling. `maybe_idle_mouse_move` therefore no longer runs on wall-clock at
  all: it triggers on time since the last DELIBERATE cursor movement, with the budget drawn
  from the same measured still-segment distribution.
  **CORRECTED 2026-08-30 — the sentence above was NOT true in practice, for two separate
  reasons, and both are now fixed.** (a) The marker lived in `controller.py`, which is loaded
  TWICE in a running process (see "controller.py exists twice", below), so the engine read one
  copy while every routine click bumped the other: the trigger never saw routine activity at
  all and fired continuously through active play. It now lives in `input_state.py`. (b) Even
  once shared, the drawn budget can be ~1s, which is well inside the ordinary gap between two of
  our own actions, so it still drifted mid-work — MEASURED 14 drifts per 48 clicks, 5 of them
  after under 2.5s. `IDLE_MOVE_MIN_STILL_S` (4.0) is now a floor beneath the drawn budget; the
  same measurement this feature rests on says the human is NEVER still longer than 4.5s, so a
  drift at 1.3s was out-moving them rather than imitating them. AFTER: 7 drifts, stillness
  `[4.0, 4.4, 4.4, 17.7, 4.0, 4.5, 4.5]`s, none under 4s. USER-REPORTED throughout as the cursor
  being "dragged back" mid-work; `maybe_idle_mouse_move` now LOGS every drift, because one that
  moves the cursor with no click is otherwise invisible in a log full of clicks — which is
  exactly how it hid.
  Routines can also declare themselves busy (`controller.mark_busy(seconds)`) — belt and braces,
  and NOT sufficient on its own: marking work_phase busy left `_climb` unmarked and the drifts
  simply reappeared there. Prefer the floor; reach for `mark_busy` only for a genuinely long
  pending action.
  Still not re-measured on a 30+ active-minute recording; if still-p90 stays >4-5s the remaining
  freezes are the poll-loop blind spot and Phase B is what's left.
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

## Current state of routines/mahogany_homes.py

Written 2026-08-27 from four human recordings (`recordings/2026-08-27_{174045,175143,180346,
181548}_mahogny-homes{,2,3,4}.*`) plus the decompiled reference plugin
(`data/decompiled/mahogany-homes-plugin/`, sideloaded Plugin Hub jar, documentation only).
Runs Adept-tier contracts: NPC Contact spell (widget 218.113, confirmed against both the
recordings and `runelite-api-1.12.33.jar`) requests a contract with no dialogue to navigate,
`MahoganyHomesProvider.java` tracks which home/tier is active purely from the assignment chat
message (no varbit exists for this), teleport tablets/Xeric's talisman get the player to the
right city, and a plain left-click on each hotspot's fixed object id (one id per home per slot,
stable across its whole Remove-then-Build lifecycle) does the work, relying on the user's own
client-side menu swap rather than any hijack-escalation machinery. **Not yet run live** — every
widget/interface id this depends on was verified against the recordings and the real
`runelite-api` jar rather than guessed, but `HOTSPOT_CLICK_COOLDOWN_S`/`TURN_IN_CLICK_COOLDOWN_S`
(both first-draft guesses, see the routine's own module docstring) and the overall phase timing
need a live session to confirm, the same way golem_crafting's own click cadence was corrected
after its first live run.

**Three gaps closed 2026-08-28, two of them things the reference plugin had that the original
port missed** — worth knowing before porting from a decompile again, because all three were
found by diffing our data against the reference rather than by reading our own code:
- **`npcUpstairs`** (reference `Home.isNpcUpstairs`) — true for JESS and NORMAN only. A full
  re-diff of every other Home field (object ids, per-slot HotspotType, ladders, and the
  slot↔varbit alignment against `Hotspot`'s authoritative object→varbit map) found **zero**
  other differences across all 12 homes. `teleportItems` is deliberately not ported (the routine
  uses its own simplified `CITY_TELEPORTS`) and `hint` is a non-functional UI string.
- **The plank sack** (reference `MahoganyHomesPlugin.countPlankSackVarb`). Item 24882, one
  varbit per plank type — 960/18171, 8778/18172, **8780 (teak)/18173**, 8782/18174 — capacity 28.
  The original "no plank sack, to avoid golem_crafting's stale fur-pouch estimate" reasoning was
  simply wrong: the count is a **varbit read fresh from the client**, not a running local
  estimate, so it cannot drift. Two facts MEASURED from the recordings, not assumed: `Fill` is
  only offered while a **bank interface is open** (same mechanic as `banker.fill_fur_pouch`, and
  the recording logs it as `CC_OP` on widget 15.3 slot 0), and the game **spends planks straight
  out of the sack with no Empty step** — varbit 18173 ticks 28→27→25→23 in step with each
  hotspot's cost, alongside the hotspot varbit, the build animation and the Construction XP drop.
  So sack planks count toward affordability; counting only loose ones banked with a full sack.
- **Which FLOOR a hotspot is on.** `/objects` only ever reports the player's current plane, so
  "not found" could not distinguish "upstairs" from "not close enough", and the routine guessed
  by right-clicking a ladder with both `Climb-up`/`Climb-down`. `MahoganyHomesProvider` now scans
  **every** plane of the loaded scene for the hotspot object ids and reports each hotspot's
  `location`/`plane`, so the direction is decided rather than guessed. Ladders are now chosen by
  proximity to the **hotspot**, not to the player — which matters for **NOELLA**, the only home
  of the twelve with two staircases and (per the user) a **disconnected** upper floor: "you have
  to fix one upstairs climb down take the other ladder fix that upstairs". Because `/objects`
  returns the whole scene on the current plane, the far half's hotspots are plainly visible from
  the near half, so work_phase would click an unreachable hotspot forever — and seeing the object
  is exactly what resets `mh_work_stuck`, so the safety stop never fired. Guarded by
  `HOTSPOT_UNREACHABLE_CLICKS`, scoped to `SPLIT_UPPER_FLOOR_HOMES` (NOELLA alone) rather than
  inferred from ladder count: two staircases do not imply a disconnected floor, and applying the
  guard everywhere would risk climbing away from a merely-slow build in eleven other houses. See
`docs/superpowers/specs/2026-08-27-mahogany-homes-design.md` for the full design and
`docs/superpowers/plans/2026-08-27-mahogany-homes.md` for the implementation history.

**Routing engine replaced 2026-08-29, later the same day as the session below.** Shortest Path
is gone — deleted along with roughly 700 lines of door/reachability heuristics it made
necessary (`_door_separates`, `_within_span`, `open_frontier_door`, `open_nearest_shut_door`,
the three-tier door escalation, and the live-HTTP `travel.reachable_set` flood fill this routine
used to call directly). `ControllerAddon/worldmap.py` — a world-wide per-edge collision map
covering all four planes, ported from EthanApi's GlobalCollisionMap and answering
`find_path`/`reachable_from` offline in milliseconds, no bridge call — is now the **only**
routing engine, and it fixes the exact gap Shortest Path had: it routes on plane 1+ (Shortest
Path's dumped collision map was ground level only). Doors are baked OPEN in the map by design
(`CacheMapDumper`'s `Exclusion` list), so a computed route goes straight through one; the door
question is answered afterward, from the route itself, by
`first_door_on_route`/`blocked_crossing_on_route` in `travel.py` — the first blocked crossing on
the route is very nearly the definition of "a shut door is here" on ANY plane, and it replaces
every one of the tiers named above with one idea. `travel.open_door_toward(ctx, goal)` is the
single call site every consumer below now uses. The one thing that did **not** move onto the
static map: `travel.can_reach` (object-interaction adjacency — "can I click this from here") is
still answered from **live** `GET /collision` flags, because the static map bakes an object's
own blocking into the same bits as a wall's and structurally cannot tell furniture from a wall
(the JEFF finding below). Every MEASURED geometry in this section — ROSS, JEFF, Barbara, TAU —
is still the evidence the design rests on; they are now also encoded as pinned fixtures in
`ControllerAddon/tests/test_worldmap.py` and `tests/test_travel.py` rather than living only
here. The bullets below describe the machinery as it was BUILT that day, on top of Shortest
Path; read them for the measurements, not for the current API names, which the "ONE call now"
notes inline correct where they matter most.

**Second live session, 2026-08-29 — three bugs, all MEASURED. Don't re-derive them.**

- **A widget text match MUST be scoped to a widget GROUP, or the chatbox answers for you.**
  `_accept_tea_if_offered` substring-matched `"cuppa"` over the whole `/widgets/dump`, and the
  chatbox (**group 162**, identifiable by its own All/Game/Public/Private tab labels) keeps
  `"Aaah, nothing like a nice cuppa tea!"` and `"You drink the tea, you feel energized!"` in
  scrollback for the rest of the session once any tea has been drunk. Chat history never clears,
  so the check returned True on EVERY tick and `tick()` never reached a phase handler at all --
  three clicks aimed at a chat line, then SPACE forever, with `dialogOpen` false throughout. A
  hard deadlock, not a slow path: the routine could not have made progress in any circumstance.
  Note the earlier screenX/screenY guard did NOT exclude these -- chat lines have real screen
  positions. Dialogue OPTIONS live in **group 219** (confirmed from recorder output,
  `widget 219.1 slot 1`); `DIALOG_OPTION_GROUP` is now what the match is scoped to. Verified by
  replaying both matchers against the live widget tree that deadlocked: old 3 matches, new 0.
- **A ladder is clicked at its own reported CENTRE. A tile-corner aim was tried on 2026-08-29 and
  REVERTED the same day** (user: *"revert the ladder to click center"*) -- it fixed one house and
  broke another, so don't reach for it again without per-ladder evidence. What is real is that an
  object's reported point is its **clickbox centroid** (`RuneLiteUtil.shapeCoords` ->
  `pointInsideShape`, which prefers the area centroid), and how useful that is varies by model:
  - MARIAH's upper-floor ladder: centroid `(1249,709)` vs the tile centre straight from
    `/ground/walk` `(1249,707)` -- the SAME point, i.e. the clickbox is effectively the whole TILE
    while the climbable model occupies only the top of it. A marked frame dump shows the point on
    bare floorboards. Right-clicking it returned ground options only
    (`['Cancel','Mark Safespot','Walk here']`) 13 times, then a safety stop.
  - ROSS's ladder 16683: centroid `(1263,316)` vs tile centre `(1360,386)` -- ~100px apart, and
    the centroid is squarely ON the model. Here the centroid was already right, and overriding it
    with a corner aimed a good click away; the rotation then spent the whole stuck budget.
  The user's fix for the MARIAH-shaped case is to **skew the camera** so top-floor ladders don't
  need a special aim at all. Do pass `offset=(0, 0)` -- `click_menu_action`'s default `(-7, +7)`
  nudge is DOWNWARD in screen space and is enough to slide off a small target.
- **Changing floor is progress and must clear `mh_work_stuck`.** With the corner fix in, the climb
  worked and the run STILL safety-stopped two ticks later: three attempts at
  `LADDER_CLICK_COOLDOWN_S` apiece had already spent the 15-tick budget. Keyed on the plane
  actually CHANGING (not on the menu click being taken) so an up-then-down loop cannot reset the
  counter forever. **The marker is not consumed on read** -- a climb takes a tick or two to land,
  so the tick right after the click still reports the old plane; consuming it there lost a descent
  that genuinely happened and stopped the run anyway.
- **`turn_in_phase` had no re-validation and no ceiling, and looped forever.** LIVE 2026-08-29 at
  ROSS's: the phase latched to `"turn_in"` one tick after a single hotspot was built -- work_phase
  moves there whenever `pick_next_hotspot` returns None, so one transient empty read is enough --
  and then talked to Ross every ~3s indefinitely while SIX of his seven hotspots still reported
  `needsAttention`. Nothing re-checked, so it could not recover. turn_in_phase now bounces back to
  `"work"` whenever we are at the home and a hotspot still needs attention (the mirror of
  work_phase's own "no hotspot data from out here" guard; the two conditions are mutually
  exclusive on the same data, so they cannot ping-pong), and `TURN_IN_MAX_CLICKS` (12, per home)
  safety-stops rather than talking forever -- **a cooldown only sets the RATE of an unbounded
  loop**, the same lesson as the teleport-tablet and NPC-Contact caps.
- **The door is resolved BEFORE the first hotspot click, not after five wasted ones.** USER
  2026-08-29: *"why when we come to the house do we not prioritise opening the god danm door,
  should be the first thing we click if its closed"*. `travel.walk_to` already resolves doors
  before stepping off -- but work_phase does not necessarily walk at all: `/objects` returns the
  whole scene on our plane, so a hotspot behind a shut front door is plainly VISIBLE and was
  clicked immediately, and the door only got resolved through the unreachable branch,
  `HOTSPOT_UNREACHABLE_CLICKS` (5) clicks and ~6s later. MEASURED live at MARIAH's: exactly that,
  twice in one contract. work_phase now calls `travel.open_door_toward` (the straight-line
  `open_doorway_to` this originally shipped with was later folded into that one route-based
  call, see the routing-engine note above) once per hotspot state (latched on the same
  `(objectId, value)` signature as the click counter), which costs nothing when the way is
  clear.
- **A door is opened by WALKING to it and then opening it, so the confirmation must budget the
  walk.** `DOOR_OPEN_WAIT_S` was a flat 2.0s, which is less than the ~2.4s of walking to a door
  four tiles off (one tile per 0.6s tick). USER 2026-08-29 on the loop at ROSS's: *"we clicked
  away before waiting for us to walk to the door and open it"*. The recorder settles it -- the
  FIRST click registered as `Open Door` on id 40178 and was good; the budget expired before the
  player arrived, and the two retries then resolved to `Walk here` because the player had moved
  off the pixel being re-aimed at. So a working click was reported as a failure and interrupted by
  its own retry, three times over. The budget is now
  `DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S`, capped by `DOOR_OPEN_WAIT_MAX_S` so a door
  that genuinely never opens still fails fast. This is the same rough edge the 2026-08-28 notes
  recorded as "seen once on the Varrock bank door, harmless, not yet tuned" -- it is not harmless;
  it loops. **Do not read `Pick-lock` in a door's actions as "locked and unopenable"** -- that was
  my own wrong first reading of this failure, and the click was landing fine.
- **WALKING CANNOT CHANGE FLOOR, and `turn_in_phase` did not know that.** USER-REPORTED
  2026-08-29 as "stuck upstairs": at ROSS's, with every hotspot built and the player still on
  plane 1, the phase saw "homeowner npc not visible" and walked at `standingLocation` -- which is
  ALWAYS reported at plane 0. The walker clicked the identical pixel (1144,724) about thirty
  times and the player never moved. work_phase's ladder handling could not help: it lives in
  `_approach_missing_hotspot`, and with no hotspots left work_phase does not run at all. The
  descent is now owned by turn_in_phase too, and counts against `TURN_IN_MAX_CLICKS` so it cannot
  loop. Which floor the homeowner is on comes from **`npcUpstairs`** (`_homeowner_plane`), not
  from `standingLocation.plane` -- the latter is plane 0 even for JESS and NORMAN, the two homes
  whose owner is upstairs.
  **General lesson: any "walk to X" recovery in this routine needs to ask whether X is even on
  our floor first.** Two separate loops this session were a cross-plane walk that could never
  succeed.
- **A travel unit test must not fall through to a live call.** The original concern here was a
  door test whose edge geometry did not resolve escalating to a Shortest Path HTTP request, so it
  passed or failed depending on what the running client happened to answer. `worldmap.find_path`
  is pure and offline (no bridge, no serial port, no client), which removed that failure mode by
  construction rather than by pinning test geometry around it -- see "Both Python test suites are
  now hermetic", below, for the fixture-based half of this (`travel.can_reach`'s own live
  `/collision` fallback still needs one).
- **Every climb resolves the doorway to the LADDER first, because the game accepts an object
  interaction it cannot path to and then silently does nothing.** USER 2026-08-29: *"there is a
  door we can open to get to the ladder"*, and *"just use line of sight that works on second
  floor"*. MEASURED at ROSS's upper floor: the player stood at (2617,3317), ONE tile from `Door
  1540` at (2616,3317) whose blocked SOUTH edge (flags 16416 = `BLOCK_MOVEMENT_SOUTH |
  BLOCK_LINE_OF_SIGHT_SOUTH`) was the only thing between them and the ladder at (2617,3315). All
  TWELVE `Climb-down` clicks registered in the recorder against the correct object -- the aim was
  never wrong -- and the player never took a single step. `_climb` now flood-fills from
  `worldmap.reachable_from` and asks `travel.can_reach` whether the ladder is reachable from
  there; if not, it calls `travel.open_door_toward(ctx, ladder)` (which routes to the ladder via
  `worldmap.find_path` and opens whichever door the route crosses) before ever clicking the
  ladder. VERIFIED live the same run: door opened, ladder climbed, contract turned in -- all
  inside ONE tick, replacing a loop that had ended three runs.
  This is also the general shape for upstairs: Shortest Path's route branch was useless on plane
  1 (its dumped collision map was ground level only), which is the reason `worldmap.py` exists at
  all -- `GET /collision` always worked fine on any plane, and now the offline map does too.
- **A safety-stop reason must not repeat the engine's own suffix.** `trigger_safety_stop` appends
  ". Automation paused -- fix the issue and restart." itself; the first turn-in ceiling message
  included that sentence too and printed it twice.
- **A "walk to X" recovery must pick X on OUR OWN FLOOR -- `standingLocation` is always plane 0.**
  Hit twice on 2026-08-29 in two different phases, so treat it as the standing rule rather than
  two bugs. In `work_phase`'s unreachable branch, MEASURED live at JESS's (the home where all
  seven hotspots are upstairs): the goal was `standingLocation`, Shortest Path answered the
  cross-plane request with a **`Climb-down Ladder` TRANSPORT**, and the walker rode it DOWN and
  away from the work -- `"using transport: Climb-down Ladder"` -> `"used the transport but didn't
  arrive where expected"` -> `"target is on a different plane -- can't walk there yet"`, then the
  whole cycle again, ~90s of climbing up and down per pass. It still finished the contract, so
  this shows up as waste rather than a halt, which is exactly why it is easy to miss in a log.
  The branch now walks to the HOTSPOT's own tile whenever `hotspot.plane == player.plane`, and
  falls back to the standing tile otherwise (the original outside-the-house case, where the
  hotspot's own tile is usually furniture and unroutable -- see Barbara's, above).
- **A click's COOLDOWN must budget the walk, because the game performs a queued interaction on
  ARRIVAL.** USER 2026-08-29: *"are we checking if we click remove on a furniture but we are a few
  tiles away do we count that as removed before we arrive?"* -- we were.
  `HOTSPOT_CLICK_COOLDOWN_S` (0.6, Remove) and `HOTSPOT_BUILD_COOLDOWN_S` (2.4, Build/Repair) are
  the ACTION's own duration and nothing else, so a click on a hotspot a few tiles off expired
  while the player was still walking. We re-clicked (re-queueing, sometimes re-pathing), the
  varbit never moved, and after `HOTSPOT_UNREACHABLE_CLICKS` such clicks work_phase declared a
  perfectly reachable hotspot "unreachable" and walked away. MEASURED at ROSS's: five
  `Repair Broken Range` actions ALL registering correctly in the recorder against the right
  object, zero varbit movement, then the unreachable branch. Now
  `base + distance * HOTSPOT_WALK_ALLOWANCE_S`, capped by `HOTSPOT_COOLDOWN_MAX_S`. This is the
  same omission as the door-open budget -- **whenever you time a click, ask whether the player
  has to walk there first.**
- **A door click needs no fallback tier any more, because it is answered from a real ROUTE, not
  a heuristic.** MEASURED that night at ROSS's: player (2614,3319) on the grass north of the
  house, Broken Range at (2617,3318) inside, and the only way in a door at (2610,3316) whose
  blocked edge (2610|2611) has BOTH of us to its EAST -- a straight-line "is this edge between us
  and the goal" test answers "not between us" and is wrong, because the door is a **DETOUR**, not
  a point on the direct line. That night's fix was a three-tier escalation
  (`open_frontier_door` -> `open_doorway_to` -> a blunt `open_nearest_shut_door` last resort) built
  because there was no route to ask and a detour is invisible to a straight-line test. All three
  are gone: `travel.open_door_toward` asks `worldmap.find_path` for an actual BFS route to the
  goal and opens whichever shut door the route crosses (`first_door_on_route`, see the
  routing-engine note above) -- a detour door is on the route exactly as much as a straight-ahead
  one, so the same ROSS geometry is found with the one call, for free, with no tier order to get
  right. VERIFIED live the same run: door opened at (2610,3316), hotspot 40164 completed, on to
  40165.
- **REACHABILITY IS DECIDED BEFORE THE FIRST CLICK -- click-then-discover was the whole problem,
  not the door logic specifically.** `/objects` returns the whole scene on the player's plane, so
  a hotspot behind a wall, a shut door or a ceiling is plainly VISIBLE -- and work_phase used to
  click it from wherever it stood, discovering it could not reach the thing only after
  `HOTSPOT_UNREACHABLE_CLICKS` failures (~20s). MEASURED over one full ROSS contract 2026-08-29:
  **15 of 27 hotspot clicks -- 56% -- achieved nothing**, across three stuck events. USER: *"every
  time we have to walk through a door now we have to get stuck on the outside 5 clicks in a row
  this is unacceptable."*
  A flood fill decides reachability up front instead: `worldmap.reachable_from(x, y, plane)`
  walks the STATIC map's cardinal edges offline (no HTTP call at all, any plane -- this is what
  replaced the live `GET /collision`-based `travel.reachable_set()` this routine originally called
  here) and `travel.can_reach(hotspot, reach, plane=player_plane)` answers whether the hotspot is
  reachable from that set, still checking the actual click-adjacency edge against LIVE flags
  (see the routing-engine note above for why that half stays live). `pick_next_hotspot(hotspots,
  reach, plane)` then prefers a hotspot we can actually get to. Two hard-won rules survive from
  that night, both still true of `can_reach` today:
  - **Count ALL EIGHT neighbours, not the four cardinals.** MEASURED upstairs at JEFF's: the
    player stood at (3241,3447), DIAGONALLY adjacent to the hotspot at (3242,3446) whose every
    cardinal neighbour is furniture. A cardinal-only test called it unreachable and the routine
    walked in place forever instead of clicking a thing it was standing beside -- strictly worse
    than the waste it replaced. USER: *"wtf it is reachable hes standing next to it"*. A diagonal
    is accepted only when one of its two orthogonal corner routes has an open edge (the game's own
    rule), not by an unconditional point-blank override -- an earlier override shaped that way
    PRODUCED the ROSS failure below by accepting a diagonal into a wall; it is gone.
  - **An empty fill means "cannot tell", never "nothing is reachable"** -- an un-rebuilt client or
    a tile outside the loaded scene must behave exactly as before, not refuse to work.
  The fill is cardinal-only on purpose, so it can only UNDER-report; the failure mode is the old
  behaviour, never a confident walk into a wall. And unlike Shortest Path it works on ANY plane --
  ground truth for why `worldmap.py` exists at all.
- **A hotspot can be genuinely unreachable with NO door anywhere.** MEASURED upstairs at JEFF's:
  of three hotspots needing attention one was walled off and there was no shut door in range at
  all. USER: *"there is no doors but we are on the second floor"*. Bare list order fixated on it,
  walked, and wedged the greedy walker; reachable work sat two tiles away. Hence
  `pick_next_hotspot`'s reachability preference -- and note it still returns an unreachable
  hotspot when EVERY one is unreachable, so a walled-off contract falls through to the door/walk
  recovery rather than reporting "no work left" and going to turn_in on an unfinished contract.
- **Both Python test suites are now hermetic (`_no_live_bridge` autouse fixtures).** `get_collision`
  reaches the RUNNING CLIENT when a test does not stub it, so a result depended on where the
  character happened to be standing -- the same door test passed alone and failed in a full run,
  and flipped between runs as the live player moved house. (`request_shortest_path` and
  `get_shortest_path_result`, the other two live calls this originally warned about, are gone
  along with Shortest Path itself -- `worldmap.find_path` needs no live call at all, which is what
  made the routing HALF of this hermetic by construction rather than by fixture; `get_collision`
  is what remains to stub, for `travel.can_reach`'s live fallback.) Mark a test
  `@pytest.mark.real_bridge_helper` (registered in `tests/conftest.py`) only when it is testing a
  controller bridge helper itself.
- **The reachability gate belongs at EVERY world-object click site, not one at a time.** The
  invariant is: **the game ACCEPTS a click on anything you can see -- through walls, through
  floors -- and then silently does nothing.** Fixing only work_phase's hotspot clicks moved the
  failure to the next ungated site rather than removing it. MEASURED live 2026-08-29 at ROSS's:
  player (2618,3315), ladder 16683 at (2617,3315), cardinally ADJACENT -- but (2618,3315) carries
  `BLOCK_MOVEMENT_WEST` (65664), so the shared edge is a wall. `menu-select Climb-up Ladder`
  registered THREE times in the recorder, the player never changed floor, and the run
  safety-stopped. USER: *"i mean at this point am i going to have to hardcode each door?"* -- no:
  none of the four failures that night were door-specific. Gated today: hotspots (work_phase),
  ladders (`_climb`). **Still ungated: the homeowner NPC in `turn_in_phase`.**
- **ADJACENCY IS NOT REACHABILITY -- the EDGE decides, and raw tile distance cannot tell the two
  cases apart.** Two live geometries the same night, identical at Chebyshev distance 1, opposite
  answers:
  - ROSS's, player (2617,3319) / Broken Range (2617,3318): the exterior wall runs on exactly that
    edge (y=3319 blocks south, y=3318 blocks north). Unreachable.
  - JEFF's upstairs, player (3241,3447) / hotspot (3242,3446): diagonal, every cardinal neighbour
    furniture but no wall on the edges. Reachable -- the character interacted fine.
  An earlier "the model never vetoes a point-blank click" override was added for the second case
  and PRODUCED the first (clicking through a wall); it is gone. `can_reach` now tests the movement
  bits on the shared edge for cardinals, and for diagonals requires one of the two orthogonal
  corner routes to be open -- the game's own rule. Accepting diagonals unconditionally was also
  wrong at ROSS's, where (2616,3319)/(2618,3319) are diagonal to the hotspot but behind the wall.
- **A reachable set is (x, y) with NO plane in it, and the floors share a coordinate space.**
  MEASURED at ROSS's: a plane-1 hotspot was judged "reachable" against a plane-0 flood fill purely
  because its x/y landed on a walkable ground-floor tile, so work_phase preferred it over the
  ground-floor hotspots and went ladder-hunting. `can_reach` takes the fill's `plane` and returns
  **None** for a cross-floor question -- the honest answer, since reaching another floor is a
  ladder problem, not a reachability one.
- **`pick_next_hotspot` ranks: reachable here (0) > another floor (1) > this floor but walled off
  (2).** The 0/1 gap is what stops the ping-pong. USER 2026-08-29: *"why do we click the door
  inbetween each remove / build"* -- with cross-floor ranked equal to reachable, it alternated
  ground-floor work with upstairs attempts, and each attempt re-opened ROSS's self-closing front
  door on the way to a ladder. Finish the floor, then climb once. Rank 2 is still RETURNED when
  nothing better exists, so a walled-off contract reaches the door/walk recovery instead of
  reporting "no work left" and turning in an unfinished contract.
- **A door that swings shut behind you cannot be confirmed by re-reading the doorway.** MEASURED
  at ROSS's front door (40178): the recorder shows EVERY click registering as `Open Door` -- eight
  across one session -- while the confirmation logged `still shut` and spent the whole budget, up
  to 6.9s each time. USER: *"this door is always closed, so when we open it it instantly closes
  behind us"*. Both confirmation loops now also succeed on `_crossed()` -- the player having
  crossed the door's blocked edge since the click. Getting through is the outcome; the doorway's
  state at poll time is not.
- **Do not leave an unconditional door resolution beside a reachability gate.** `_climb` kept an
  earlier `open_doorway_to(ladder)` call after the gate was added, so with the ladder ALREADY
  reachable it still ran the straight-line edge test -- which false-positives on whatever door
  shares an axis, at ROSS's the front door BEHIND us. That is what walked the character outside
  between each Remove/Build. USER: *"still doesnt make much sense since we can reach them so why
  would we step out"*. MEASURED from inside the house: `can_reach(ladder)` was True and the door
  was opened anyway. Door work belongs only inside the gate.
- **Shortest Path could not route on plane 1 at all -- this measurement is WHY it was replaced.**
  MEASURED with 20s waits, that night: every plane-1 target returned `active:false, done:false`
  and never completed, and a plane-0 target requested while standing upstairs came back
  `steps=1, SEARCH_EXHAUSTED` where the single "step" was the player's own current tile (its
  dumped collision map was ground level only). So upstairs there was no route, which meant the
  door recovery had nothing to work with and `walk_to` fell straight through to an obstacle-blind
  greedy walker -- CONFIRMED live: eleven identical `Walk here` clicks at canvas (554,331) with
  the player never moving, while the client displayed "destination could not be reached". Fixed
  2026-08-29 by `worldmap.py`, whose own module docstring opens with this exact measurement as
  its reason to exist: the static map covers all four planes, so an upstairs route is now no
  different from a ground-floor one.

**Live session 2026-08-30, first run against the global collision map. Nine routing/aiming bugs.
The single most useful thing to carry forward is the SHAPE four of them shared.**

- **A RECOVERY THAT SITS BEHIND AN EARLY-RETURNING GATE CANNOT RUN.** `work_phase` checks
  `travel.can_reach` and RETURNS when it is False. Three separate recoveries lived *after* that
  gate, in the `HOTSPOT_UNREACHABLE_CLICKS` branch — NOELLA's split-floor climb-down, NORMAN's
  "head for the hotspot, not the standing tile", and the ladder handling — so on the very homes
  they were written for they could never execute. They only ever fired when reachability was
  UNKNOWN (`None`), which is exactly why **their unit tests passed while the live case wedged**:
  the tests exercised the None path, not the False path. When adding a recovery, ask which gate
  returns before it, and write the test against the answer the live failure actually produces.
- **`standingLocation` is a per-home HINT, not a tile you can stand on** (LEELA's is solid on all
  four edges), and it is ALWAYS reported at plane 0 — so no walk should ever demand an exact
  route to it, and a walk aimed at it from an upper floor cannot succeed in any circumstance.
  `travel_phase` now climbs first when the planes differ.
- **Every loop needs a ceiling, and the ones that lacked them were the ones that hurt.**
  `travel_phase`'s walk branch and `work_phase`'s unreachable branch both logged nothing and
  counted nothing — two live stalls of 79 ticks and 46s with ZERO output, indistinguishable from
  a healthy idle routine. Both are now counted and safety-stop. A silent loop is worse than a
  crash: it looks like it is working.

**Hotspot clicks are a BURST, not a click, and the numbers come from the user's own recordings.**
MEASURED across four human sessions (n=1194 hotspot actions): median gap between clicks **0.20s**,
p25 0.18s, 339 of 500 bursts are more than one click on the same option (mean 2.46, max 8), and
Remove->Build median **2.06s**. The bot on the same activity managed 3.89s / 3.62s, because the
gate was a fixed wall-clock cooldown that always paid the worst case. So the gate is now a
SIGNAL:

- Keep clicking until the hotspot's varbit moves, then **carry straight on into the next action**
  (Remove -> Build is the same object id) rather than handing back to the ~1.1s engine loop.
- **Construction XP is the "the build landed" signal** (USER: "remove is nearly instant so we can
  keep clicking until we get experience drop") — Remove pays no XP, Build/Repair does, and
  `constructionXp` on `/player` is sub-tick. The varbit is the secondary "an action landed,
  re-aim" trigger.
- **Aim once, then click IN PLACE** (`click_human`), which is what the human's 0.20s cadence
  actually is — re-flying the cursor for every repeat costs 1.0-1.1s each. But **re-aim whenever
  the player has moved or the model changed**: standing still the projection cannot slide, moving
  it slides continuously. USER: "if we continuously click we gotta update the object x y otherwise
  while we move we click away".
- **Do NOT stop the burst because the player is walking.** The ROSS reasoning (re-clicking
  mid-walk re-queues and re-paths) applies to a re-AIMED click on a later tick, not to an in-place
  repeat, and the human recordings spam straight through the approach. A burst spent walking must
  also not count as a failed attempt, or the unreachable guard walks away from a hotspot we are
  still on our way to.
- NOT yet verified at scale: the best post-change sample is n=5.

**First long live session, 2026-08-28 — everything below was MEASURED, don't re-derive it.**

- **The active contract is read off the NPC dialogue WIDGET (231.6) on a GameTick, not off chat.**
  This is a verbatim port of the reference plugin's `checkForAssignmentDialog`, and it replaced a
  chat-event parse that could never work. The *assignment* does arrive as a GAMEMESSAGE and parses
  fine; the line that re-states an ALREADY-ACTIVE contract — the one that recovers state after a
  restart mid-contract — does not. Symptom: restart with a contract in hand, `hasContract` stays
  false, and `request_contract_phase` re-casts NPC Contact once per tick forever over a contract
  the account already holds. Three separate hypotheses died before the user said "why don't we just
  copy the mahogany home plugin, it recognises the contract" — all three were about the chat event
  (`ChatMessageType.DIALOG` being filtered out; RuneLite embedding the speaker in `getMessage()` as
  `Amy|You're currently on...`; the game's non-breaking spaces, which Java's `\s` does not match).
  Each was real, each was fixed, and none of them was sufficient. **The widget carries the full,
  untruncated, already-assembled text with no speaker prefix and no message-type question to get
  wrong.** Lesson for the next port from a decompile: when the reference plugin reads a different
  SOURCE for the same fact, copy the source, don't reconstruct the fact from a nearer one.
  `GET /mahogany-homes` now also returns **`lastChat`** — what the provider last parsed and whether
  it matched — added after the third dead hypothesis. Instrument the boundary sooner than that.
- **`WorldPoint.fromLocalInstance(client, local)` stamps the WORLD VIEW's current plane on the
  result — pass the plane explicitly when scanning planes you are not standing on.** This silently
  broke `MahoganyHomesProvider`'s all-planes hotspot scan in the one direction that matters: every
  hotspot found upstairs reported `plane: 0` while the player stood downstairs, so `work_phase` read
  "same floor as me", never tried a staircase, and clicked at a hotspot through a ceiling until the
  unreachable guard safety-stopped the run (CONFIRMED live at Larry's, Falador: `/objects` did not
  contain 40098/40099 at all, while the scan happily reported both at plane 0). Use the
  three-argument overload. This applies to ANY future multi-plane scan, not just this provider.
- **A tablet teleport takes 3.8s from click to region change** (varbit 12393 → anim 4069 → anim 4071
  → spotanim 678 → LOADING → LOGGED_IN), measured off `recordings/2026-08-28_173427_mh-live-run5`.
  The confirmation was a single `human_delay(2.5, 3.2)` and one position read — landing ~0.4s BEFORE
  arrival — so a teleport that worked read as a failure and the retry broke another tablet. It is
  now POLLED (`_await_teleport`, `TELEPORT_CONFIRM_TIMEOUT_S`): reading too early costs a tablet,
  waiting too long costs idle seconds, and that asymmetry decides it.
- **`TRAVEL_TELEPORT_DISTANCE` is 150, not 40, and the number is load-bearing.** It means "are we in
  a different city", and 40 could not express that: the Falador tablet lands **74 tiles** from
  Larry's own standing tile, so standing in Falador having just arrived, the gate still said
  "teleport". The only thing preventing a second tablet was the `mh_teleported_this_contract` latch
  — far too thin a thread, and when the latch was wrong (above) it teleported Falador→Falador once a
  tick. The four destinations in `CITY_TELEPORTS` are 248+ tiles apart at their closest (Falador
  2965,3379 → Varrock 3213,3424) and the worst intra-city landing is ~75, so 150 has room both ways.
- **Anything that spends a consumable on repeat needs a CEILING, not just a cooldown.** Two loops in
  one session burned real resources before being stopped by hand (three teleport tablets; NPC
  Contact casts). Hence `TELEPORT_MAX_ATTEMPTS` (2, then safety-stop rather than walk cross-country)
  and `CONTACT_MAX_CASTS` (5). A cooldown only sets the RATE of an unbounded loop.
- **The initial `mh_phase` is derived from live state, never defaulted to `"bank"`.** It used to be
  `data.get("mh_phase") or "bank"`, and with a contract in progress nothing below overrode it
  (`"bank"` deliberately does not auto-promote, to preserve work_phase's mid-contract diversion) —
  so EVERY restart mid-contract went banking. Live, that meant restarting inside Mariah's house and
  going for the Hosidius bank booth 21 tiles away through a wall.
- **`bank_phase` walks to a bank more than `banker.BANK_APPROACH_TILES` (8) away, via
  `travel.walk_to`, before opening it.** `banker.open_bank` gained its own minimap-hop approach the
  same day, but a minimap hop cannot open a closed door, and this routine banks from inside the
  contract house. Two things made the old behaviour a dead loop rather than a slow success:
  `find_nearby_bank` accepts anything within `maxBankDistance` (40) while `_refresh_bank_object`
  only re-resolves within **15**, so every retry re-aimed at the identical stale pixel; and the
  booth's projection landed on the chatbox tab strip, so all five clicks resolved to "Switch tab".

**The collision map `worldmap.py` reads is a static snapshot, not a live one.** A game update
that moves a wall makes a route wrong; the symptom is a walk that stalls at one specific tile,
not a crash. Regenerating it was deliberately deferred rather than done as part of this rewrite
(swapping mid-plan would have invalidated every pinned per-plane bit count and route-length
fixture the earlier tasks encoded). USER (2026-08-29) dumped a fresh map with `CacheMapDumper`
anyway, staged at `ControllerAddon/map-output/collision-map.gz`, and MEASURED it does **not**
load with today's reader: its cookie's low 16 bits are 12347 (`SERIAL_COOKIE` *with* run
containers -- `CacheMapDumper` calls `runOptimize()` before serializing), and `worldmap._Roaring`
currently accepts only the no-run-container cookie. Swapping to that dump is therefore not the
ten-line `Indexer` change the design doc originally assumed; it needs three things: (1)
run-container support in `worldmap._Roaring` -- the run-flag bitmap sits between the cookie and
the descriptive header, plus a third container type; (2) an `Indexer` change to the dump's own
layout -- 12-bit x (base 480), 14-bit y, 2-bit plane, and the polarity is **INVERTED** from
today's map (a set bit means BLOCKED, not passable); (3) re-measurement of every pinned fixture in
`ControllerAddon/tests/test_worldmap.py`, since the per-plane bit counts and all four route
lengths there come from the current (EthanApi) map, not this one. Not done here -- recorded as
its own follow-up task.
