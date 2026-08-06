# Per-NPC tracker framework — design

Status: approved, not yet implemented.

## Purpose

Add a generic per-entity event-tracking layer to the RuneLite plugin, modeled on the community
"Salvation" plugin's `TrackerPlugin` (see `data/salvation_plugin examples/plugins/tracker/`) --
first groundwork toward eventually automating mechanics-heavy PvM (raids/bosses), not just
Slayer trash and Sailing. The concrete gap this closes: `ControllerPlugin.java`'s existing event
code (`HP_CHANGED`, `ANIMATION_CHANGED`, `PRAYER_CHANGED`, `NPC_DIED`, `CHAT_MESSAGE`, etc., all
pushed via `UdpEventSender`) only ever watches the *player's own* state. Nothing today says
"watch *this specific* NPC and tell me when *it* hitsplats or changes animation" -- which is
exactly what a boss-mechanic tracker needs (an individual monster's attack tell), and exactly
what Salvation's `TrackerPlugin` provides for its own per-boss packages.

This iteration builds the generic attach/detach/fan-out mechanism and proves it live against a
real, already-running fight. It does not implement any specific boss's mechanics.

## Non-goals

- Any specific boss/encounter's mechanic logic. No boss is wired up in this iteration -- see
  Purpose. A real boss gets its own tracker + spec later, once one is chosen.
- Rendering/overlays. Salvation's `TrackerPlugin` spends roughly half its code on `NpcOverlay`/
  `SceneOverlay`/`TopOverlay`/`WidgetOverlay` wiring and a `shouldRender` hook for drawing things
  on screen for a human to see. This bot runs autonomously with nobody watching the client (see
  AGENTS.md's "Autonomous live debugging" section) -- none of that is built.
- `GameObjectListener`/`GroundObjectListener`/`GraphicsObjectListener`/`ProjectileListener`/
  `SpotAnimationsListener`/`OverheadTextListener`/`MenuListener`/`SoundEffectListener`/
  `DamagePredictor`. Only `AnimationListener`, `HitsplatListener`, and a per-tracker tick
  callback are built now -- the three a boss-mechanic tracker almost always needs first. The
  remaining interfaces follow the same pattern (a new marker interface + one more `instanceof`
  check + one more `@Subscribe`) and get added later, one at a time, as a real boss actually
  needs them -- not spent on speculative surface area with nothing live to verify it against.
- Changes to `ControllerPlugin.java`'s existing player-state event code. That code works for its
  existing purpose and isn't touched.
- Any Python-side consumption logic. The proof of done is the new UDP events showing up via
  `poll_events`, not a routine reacting to them -- see "Testing".

## Architecture

New package `com.example.tracker`, additive alongside the existing `providers`/`bridge`/`util`/
`controller` packages.

- **`NpcTracker`** -- empty marker interface (concrete trackers implement it plus whichever
  listener interfaces they need); one `default void onDetach() {}` for optional cleanup.
- **`NpcTrackerFactory`** -- `boolean isValidTrackingTarget(NPC)` + `NpcTracker newInstance(NPC)`.
  Multiple factories can be registered independently; each spawned NPC is checked against every
  registered factory, so one NPC can end up with more than one tracker attached (matches
  Salvation's own `trackNpc`, which does the same).
- **Listener interfaces** (`com.example.tracker`):
  - `AnimationListener { void onAnimationChanged(int animationId); }`
  - `HitsplatListener { void onHitsplat(Hitsplat hitsplat); }`
  - `TrackerTickListener { void onTrackerTick(int tick); }` -- fires only while attached; for a
    tracker's own per-tick bookkeeping (e.g. ticks since last seen attacking). No UDP push by
    default -- a concrete tracker that wants to broadcast something on tick can call
    `UdpEventSender` itself.
- **`NpcTrackerRegistry`** -- the RuneLite `@Subscribe` hub. Follows the same wiring pattern as
  `SailingProvider` (the one existing provider that also needs `EventBus` access, not just HTTP):
  plain-constructed in `ControllerPlugin.startUp()` as `new NpcTrackerRegistry(client,
  eventBus).register()`, where `register()` itself calls `eventBus.register(this)` -- not
  Guice `@Inject`, which is how the plugin's own fields (like `config`, `client`) are wired but
  not how any provider is. Holds `Map<NPC, List<NpcTracker>>`.
  - `addFactory(NpcTrackerFactory)` -- adds a factory to the registry (named distinctly from the
    no-arg `register()` above, which wires this class itself onto the event bus -- same verb,
    different purpose, kept separate to avoid an overloaded-name mix-up during implementation).
  - `onNpcSpawned` -- checks every registered factory against the new NPC; attaches a new tracker
    instance per match.
  - `onNpcChanged` (transforms/phase changes) -- re-validates every registered factory against
    the NPC; detaches trackers whose factory no longer matches, attaches newly-matching ones.
    Mirrors Salvation's own `onNpcChanged` handling.
  - `onNpcDespawned` -- calls `onDetach()` on each attached tracker for that NPC, then removes it
    from the map.
  - `onClientTick` -- **not** a subscription to the raw `AnimationChanged` RuneLite event.
    Confirmed against Salvation's actual `NpcTracker` (`data/salvation_plugin
    examples/plugins/tracker/trackers/NpcTracker.java:193-216`): it doesn't subscribe to
    `AnimationChanged` for NPCs either, it polls `npc.getAnimation()`/`npc.getAnimationFrame()`
    on every `ClientTick` (far more frequent than `GameTick`) and diffs against the
    last-seen value itself, including an `animationFrame < lastAnimationFrame` check to catch
    the same animation id restarting (e.g. an attack repeating back-to-back). For each NPC with
    at least one attached `AnimationListener`, the registry does the same: polls, diffs, and
    only on an actual change calls `onAnimationChanged` on every attached `AnimationListener`
    for that NPC and pushes `NPC_TRACKED_ANIMATION_CHANGED` (npc index/id/name + the new
    animation id) via the existing `UdpEventSender`. Trackers still only see `onAnimationChanged`
    when something actually changed -- the polling/diffing is an internal registry detail, not
    a new listener type.
  - `onHitsplatApplied` -- a real, direct RuneLite event subscription (confirmed in
    `TrackerPlugin.java`'s own handler: `event.getActor()` / `event.getHitsplat()`), unlike
    animation. If the event's actor is a tracked NPC, calls `onHitsplat` on every attached
    tracker that implements `HitsplatListener`, then pushes `NPC_TRACKED_HITSPLAT` (npc
    index/id/name + hitsplat type/amount) via `UdpEventSender`.
  - `onGameTick` -- calls `onTrackerTick` on every attached tracker implementing
    `TrackerTickListener`. Separate cadence from the `ClientTick` animation polling above --
    once per game tick, for a tracker's own bookkeeping, not detection.
  - `onGameStateChanged(HOPPING)` -- clears the whole map (mirrors Salvation's own cleanup on
    world hop).
- **`CurrentTargetTracker`** (the proof tracker, registered as one `NpcTrackerFactory`) --
  `isValidTrackingTarget` matches whatever NPC is currently interacting with the local player
  (i.e. whatever is being fought right now). Implements all three listener interfaces. Needs no
  boss-specific config, and is true for the live Slayer fight already running against this
  session's client -- see Testing.

**Data flow:** RuneLite events (`NpcSpawned`/`NpcChanged`/`NpcDespawned`/`AnimationChanged`/
`HitsplatApplied`/`GameTick`/`GameStateChanged`) -> `NpcTrackerRegistry` (attach/detach + per-
listener fan-out) -> attached tracker's callback -> `UdpEventSender.send(type, payload)` ->
existing UDP socket (port 50555, unchanged) -> `poll_events` (MCP server) / `controller.py`'s own
UDP loop, whichever currently holds the port -- same transport already live-verified this
session, no changes to it.

## Error handling

- Each listener callback invocation (`onAnimationChanged`, `onHitsplat`, `onTrackerTick`) is
  wrapped in try/catch inside the registry -- one tracker throwing (bad logic, unexpected null)
  logs once and does not take down the RuneLite event thread or any other attached tracker.
  Matches this codebase's existing "one bad provider can't break the others" convention (see
  `BridgeProvider`'s `onClient` wrapper).
- NPCs are keyed by object reference (`Map<NPC, ...>`), the same assumption Salvation's
  `BoatTracker`/`TrackerPlugin` make, relying on `NpcDespawned` firing reliably for cleanup.
- `UdpEventSender` already swallows send failures internally (catches, logs to stderr, never
  throws) -- reused as-is, no new handling needed there.

## Testing

This codebase has zero existing Java unit tests -- `ExamplePluginTest.java` is a dev harness that
launches the real client (`./gradlew run`'s actual entry point), not a test. JUnit 4.12 is
already a declared but unused test dependency (`build.gradle`), though, and nothing about
`net.runelite.api.NPC`/`Hitsplat` requires a mocking library to stub -- they're plain interfaces.

Two tiers:

1. **Unit tests (new for this codebase's Java side)**, hand-written stub `NPC`/`Hitsplat`
   objects, no live client needed: `NpcTrackerRegistry`'s attach/detach bookkeeping, multi-
   factory/multi-listener fan-out, re-validation on `NpcChanged`, and cleanup on despawn/world-
   hop. This is real, non-trivial logic worth covering fast and deterministically rather than
   only through the live client.
2. **Live verification (this codebase's existing convention for anything RuneLite-event-bus-
   wired)** -- unit tests can't prove RuneLite's real event bus actually calls these `@Subscribe`
   methods correctly. Steps: `./gradlew compileJava` clean; relaunch the client; confirm
   `CurrentTargetTracker` attaches during a live Slayer fight (already running this session) and
   that `NPC_TRACKED_ANIMATION_CHANGED`/`NPC_TRACKED_HITSPLAT` actually appear via the MCP
   server's `poll_events` tool, matching AGENTS.md's "verify live, not just from reading the
   code" convention used for every other bridge/event feature in this project.

Done when: both tiers pass, and at least one real `NPC_TRACKED_HITSPLAT` and one real
`NPC_TRACKED_ANIMATION_CHANGED` have been observed live via `poll_events` during an actual fight.
