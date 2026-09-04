# Event-driven outcomes: reacting to the client instead of the tick

Date: 2026-09-04. Status: approved design, not yet implemented.

## Why

USER (2026-09-04): "I want to move away as much as possible from on tick information ...
It will speed up our reaction speed significantly. I don't mean we completely patch away the
old things, keep it, but if we can add more sub tick providers."

MEASURED on `logs/gotr_live76.log` (one full Guardians of the Rift round, 607s, 417 gaps
between consecutive log lines): 82% of the round is spent in gaps over 1s, 67% over 2s, 48%
over 4s. The biggest gaps follow a verified click: the routine opens a commit window
(`ACTION_COMMIT_S + walk budget`, 4-8s) and hands back to the engine loop, which polls HTTP
once per iteration until the window expires. The outcome (inventory delta, XP drop, varbit
flip, arrival) lands well before that, and nothing reacts to it. The idle-drift lines show the
cursor still for 4s+ many times per round.

Reaction chain today: server tick -> client processes the packet -> our Java handler -> UDP ->
Python listener (20ms poll) -> **the routine only looks at the next engine-loop iteration**,
and most confirmations are HTTP polls at 0.15-0.6s, two-stable-reads idle waits, or fixed
sleeps.

What is NOT the bottleneck: the bridge (15-120ms per request) and the UDP path (already
asynchronous on the Java side since 2026-09-01, 20ms poll on the Python side).

## Scope

Three pieces, in this order:

1. **Java**: new client-event-driven UDP events plus one genuinely sub-tick signal (player
   motion from per-frame interpolation). Additive; nothing existing changes shape.
2. **Python shared**: one `await_outcome` primitive, an event-wakeable engine loop, varbit
   subscription plumbing.
3. **GOTR conversion**: every commit window becomes an outcome wait; actions chain inside one
   tick; measured against runs 75/76 and the human benchmark.

The existing machinery stays. `ctx.events` remains an accelerator and never a source of
truth (CLAUDE.md rule); every wait falls back to the HTTP read the routine uses today.

Rejected alternatives: polling HTTP faster (every request runs on the client thread, so 20
requests a second is the stutter the UDP cap exists to avoid) and a long-poll `/await`
endpoint (lossless, but ties up the HTTP server thread and needs a condition language in
Java for the same latency).

## Section 1: Java events

All emitted from `@Subscribe` handlers in `ControllerPlugin` (method names MUST be
`on<EventSimpleName>`, see CLAUDE.md) through the existing asynchronous `UdpEventSender`.
Every new payload carries `tick` (from `tickClock`) and `atMillis`
(`System.currentTimeMillis()`), so Python can measure its own reaction latency.

| Event | RuneLite source | Payload | Purpose |
|---|---|---|---|
| `VARBIT_CHANGED` | `VarbitChanged` | `varbitId`, `varpId`, `value` | Round state, cell tiers, barrier state, agility busy flag, hotspot state -- all HTTP-polled today |
| `WIDGET_LOADED` / `WIDGET_CLOSED` | `WidgetLoaded` / `WidgetClosed` | `groupId` | GOTR HUD (group 746 = `InterfaceID.GotrHud`, 0x02ea) returning after a teleport; dialogues; bank; skill-select |
| `MENU_CLICKED` | `MenuOptionClicked` | the `/last-action` fields (`option`, `target`, `id`, `menuAction`, `param0`, `param1`, `itemId`) | Instant "the game accepted the click and it resolved to X" |
| `GRAPHIC_CHANGED` | `GraphicChanged`, local player only | `graphic` (spotanim id) | Teleport flash, spell cast |
| `HITSPLAT` | `HitsplatApplied`, local player or the actor it is interacting with | `amount`, `hitsplatType`, `actor` (`"player"` or npc index/id/name) | Combat routines later; cheap now |
| `PLAYER_MOVING` / `PLAYER_STOPPED` | `ClientTick` frame diff of `getLocalLocation()` | `x`, `y`, `plane`, `localX`, `localY` | Sub-tick arrival: the interpolation stops before the tile updates |

`ANIMATION_CHANGED` and `TARGET_CHANGED` keep their names and payloads but are emitted from
`AnimationChanged` (local player only) and `InteractingChanged` (local player as source)
instead of the GameTick poll. The GameTick handler keeps HP/prayer/TICK.

### Varbit subscription filter

`VarbitChanged` fires for hundreds of varps on login and scene load; unfiltered it would evict
useful events from the 512-slot queue. So:

- `GET /events/subscribe?varbits=a,b,c&varps=x,y` sets the emitted set (REPLACE semantics;
  no params or empty means emit none). Returns the resulting sets.
- `GET /events/subscriptions` reads them back.
- `VarbitChanged` carries both `varbitId` (-1 when a raw varp changed) and `varpId`; a
  subscription matches either.
- Implemented as a small pure class (`EventSubscriptions`) with `AtomicReference<Set>`
  swaps, so the handler's check is a lock-free read and the class is unit-testable.

The other new events are low-frequency and unfiltered. `WIDGET_LOADED` on a scene load is a
handful of groups, not hundreds.

### Motion tracker

`PlayerMotionTracker` (pure logic, unit-tested): fed the local player's `LocalPoint` once per
`ClientTick` (50Hz client cycle, independent of FPS). A change from the previous frame is
motion; `STOPPED_AFTER_FRAMES` (2, about 40ms) consecutive unchanged frames after motion is a
stop. Walking moves ~4 local units per client tick and running ~8, so a single unchanged frame
mid-movement does not happen at either speed; the two-frame debounce is for safety. Emits
`PLAYER_MOVING` on the first changed frame after stillness and `PLAYER_STOPPED` on the
debounced stop. A null player, plane change or a jump larger than `TELEPORT_JUMP_UNITS`
(e.g. 2 tiles in one frame -- a teleport or scene reload, not a walk) resets the tracker
without emitting MOVING, but does emit STOPPED if it was moving.

`/player` gains `moving` (boolean) from the same tracker, so the HTTP fallback exists.

### Tests (Gradle)

- `EventSubscriptionsTest`: replace semantics, empty clears, varbit/varp match, parse of the
  query string.
- `PlayerMotionTrackerTest`: still -> moving on first delta; moving -> stopped after the
  debounce; a teleport jump resets without MOVING; null resets.

Emission itself is verified live with `recorder.py` and the MCP `poll_events` tool.

## Section 2: Python shared

### `await_outcome`

On `EventStream` in `bridge_events.py`, plus module-level `await_outcome(events, ...)` that
takes `ctx.events` (possibly None).

```
mark = ctx.events.mark()             # before the click
click...
res = await_outcome(ctx.events,
    signals={"XP_GAINED": lambda p: p.get("skill") == "Runecraft",
             "INVENTORY_CHANGED": None},
    budget_s=base + walk_budget,
    confirm=lambda: read_delta(),    # one HTTP read; truthy = it worked
    since=mark)
```

Semantics:

- Every pumped event gets a monotonic sequence number; `mark()` returns the current one and
  `since=` restricts matching to later events. This replaces the "clear() before the action"
  rule without discarding anything `on_event` still needs. `clear()` keeps working.
- Pumps the socket every `EVENT_POLL_INTERVAL_S` (20ms) until a listed signal (type in
  `signals`, predicate None or truthy on the payload) lands after `mark`.
- On a signal: call `confirm()` once. Truthy -> return a truthy `Outcome(hit=True,
  signal=<type>, payload=<dict>, confirmed=<value>, latency_s=<since call>)`. Falsy -> keep
  waiting (UDP is an accelerator, HTTP is truth). A signal that arrives while confirm is
  running is seen on the next scan.
- On budget expiry: call `confirm()` one last time; return `Outcome(hit=bool(value),
  signal=None, ...)`. That is exactly today's behaviour, so a dropped packet costs latency,
  never correctness.
- `confirm=None` means the signal alone is the outcome (for signals that ARE the truth, e.g.
  `MENU_CLICKED`, which is the game's own verdict).
- `events is None`: poll `confirm()` every `FALLBACK_POLL_S` (0.25) until truthy or budget.
- **Reaction padding.** After a confirmed hit, sleep `max(0, draw - confirm_time)` where
  `draw` is `sample_human_delay(*REACTION_DELAY_S)`; `REACTION_DELAY_S = (0.15, 0.35)` in
  `settings.py`. Basis: the agility human recording (next click 0.2s after the XP drop). A
  plan task fits it from the GOTR human benchmark
  (`recordings/2026-09-03_061331_guaridianoftherift-benchmark.*`, inventory-change-to-next-
  click pairs) before the first live run. `reaction=False` skips it for callers that are not
  about to act (e.g. a wait whose next step is another wait).
- `Outcome` is falsy when `hit` is False, so `if not res:` reads naturally.

### Engine loop wake-on-event

`main()`'s closing `time.sleep(STATE_POLL_INTERVAL)` becomes
`event_stream.wait_any(timeout_s=STATE_POLL_INTERVAL)`: pumps every 20ms and returns early
when any event other than `TICK` arrives. TICK is excluded on purpose -- waking on it would
align every tick handler, and so every click, to the server tick boundary. Events pumped
here stay in `_pending` for the next `drain()`.

The first implementation task instruments the loop: `debug_log` a `[engine] loop iteration
N.NNs (tick N.NNs)` line whenever an iteration exceeds `LOOP_SLOW_LOG_S` (1.0), plus a
running mean every 60s. The ~1.1s per-iteration figure in CLAUDE.md is remembered, not
measured, and that iteration is the reaction floor whenever a routine returns to the loop.

### Varbit subscription plumbing

- `controller.subscribe_varbits(varbits, varps=())` -> `GET /events/subscribe`. A 404 (old
  client) logs once per process and returns None.
- Routine contract addition: optional module attribute `VARBIT_SUBSCRIPTIONS = (ids...)` and
  `VARP_SUBSCRIPTIONS`. The engine posts them when a routine is loaded and again after every
  login (beside `login.ensure_camera_after_login`), so a client restart cannot silently drop
  the subscription. Documented in `controller.py`'s header comment and CLAUDE.md.

### `--no-events`

`controller.py --no-events` skips creating the listener (`ctx.events = None`). Same build,
both modes, same world: the A/B for the measurement below.

### Tests (`tests/test_bridge_events.py`)

Fake socket + injected `now_fn`/`sleep_fn`:

- signal then confirm truthy -> hit with the signal name and latency;
- signal, confirm falsy, then budget -> falls back to the final confirm;
- no events at all -> final confirm decides;
- `since` excludes an identical event pumped before the mark;
- padding applied through the injected sleep, reduced by confirm time, skipped with
  `reaction=False`;
- `events is None` -> polling fallback at `FALLBACK_POLL_S`;
- `wait_any` returns early on a real event, does not wake on TICK, times out otherwise, and
  leaves the events in `_pending` for `drain()`.

## Section 3: GOTR conversion

### Outcome per rung

The rung code around the click (ceilings, `can_reach`, walk, `_click_escalating`, holds) is
unchanged. What changes is the commit: `_commit(data, key, seconds)` + `_committed` becomes
an `await_outcome` call right after the verified click, with the rung's own signals and
confirm.

| Rung | Signals | Confirm |
|---|---|---|
| spend_stones (Power-up) | `INVENTORY_CHANGED` | `total_stones` decreased |
| place_cell / create_guardian | `INVENTORY_CHANGED`, energy varbits 13685/13686 | `chargedCellTier == NONE` |
| repair_barrier | `INVENTORY_CHANGED` (fragments spent) | no repairable tile, or fragments decreased |
| deposit runes | `INVENTORY_CHANGED` | no runes |
| restock cells | `INVENTORY_CHANGED` | `unchargedCells` increased |
| craft (Work-at) | `INVENTORY_CHANGED`, `XP_GAINED` | essence up or fragments down |
| mine | `INVENTORY_CHANGED`, `XP_GAINED` (Mining) | fragments up |
| imbue at altar | `XP_GAINED` (Runecraft) | `runecraftXp` increased |
| altar / portal / exit Enter | `MENU_CLICKED` (verdict, replaces `_enter_resolved`'s poll), then HUD `WIDGET_CLOSED`/`WIDGET_LOADED` (746) or `GRAPHIC_CHANGED` | `regionId` / `currentAltar` / `side_area` changed |
| walks (`_direct_walk`, `_walk_toward`) | `PLAYER_STOPPED` | player tile |

Cell barriers are GroundObjects, not varbits (m8aq `GuardiansOfTheRift.readBarriers`), so
their signal is the cell or the fragments leaving the inventory; the energy varbits ride along
as an extra trigger.

The walk share of a commit (`_walk_budget`, tiles x 0.35s) becomes: wait for `PLAYER_STOPPED`
(budget = the same estimate, as a ceiling), then the action's own short budget. A click on a
target 15 tiles away is confirmed on arrival, not on a distance estimate.

Which varbits to subscribe comes from the m8aq `GuardiansOfTheRift` reader
(`VarbitID.GOTR_*` used there) plus the cell-barrier varbits; the plan task lists the exact
ids by reading `runelite-api`'s `VarbitID` in the Gradle cache, not from memory.

### Chaining inside one tick

When an outcome lands, `tick` re-reads state and runs the next rung immediately (the
mining/agility pattern) instead of returning to the engine loop. `CHAIN_MAX_ACTIONS` (4) and
`CHAIN_MAX_S` (15) cap a tick so the engine's safety, focus and break gates stay responsive.
Every ceiling and the stuck watch count exactly as before; a chained action is an action.

### Settling

`ACTIVE_SETTLE_S` (5s) after a teleport becomes "HUD group 746 reloaded" (`WIDGET_LOADED`
after the matching `WIDGET_CLOSED`) once the live check confirms that is the mechanism. The
timer stays as the fallback either way. The between-round `settling` log line stays.

### Measurement

Baseline: runs 75/76 -- 5732 earned Runecraft XP per round, cycle times 80/100/79/47/69s,
48% of the round in gaps over 4s. Human benchmark: 4395 earned + 3150 bonus in 8m53s.

- `ControllerAddon/log_gaps.py <log>`: the gap histogram and top-N gaps with the preceding
  line (the script run during this design, made permanent).
- XP per round from the recorder's `xp` records, same parse as the 2026-09-03 session.
- A/B on the same world: `--no-events` vs default, one round each, before and after the
  GOTR conversion.

Success: the over-4s share drops materially and XP per round rises, with no new safety
stops attributable to the change.

### Live-verification list (first session)

1. HUD widget behaviour across a portal teleport: `WIDGET_CLOSED`/`WIDGET_LOADED` pair, or
   merely hidden? Decides the settling signal.
2. `PLAYER_STOPPED` at run speed: one event per stop, none mid-run.
3. `MENU_CLICKED` arrives before `/last-action` would have answered.
4. UDP queue drop count (`stderr` "UDP event queue full") on a mass world with the GOTR
   varbits subscribed.
5. Reaction padding fit from the human benchmark.

### Tests (`tests/test_guardians_of_the_rift.py`)

- Each rung's outcome spec as a pure function (`outcome_for(rung, st_before)` returning
  signals + confirm) against a fake event stream: hit, falsy confirm, budget fallback.
- The chain cap (actions and seconds).
- Stream None: the rung behaves as today.

## Risks

- **Reacting too fast is a signature.** Padding is the mitigation; the fit is a plan task.
- **UDP drops under load.** Every wait falls back to the HTTP read at the budget; the drop
  counter is on the verification list.
- **`controller.py` exists twice** (CLAUDE.md). `bridge_events` is a single module, and the
  engine holds the only `EventStream`; `await_outcome` reads the stream it is handed, never
  module state in `controller`.
- **Subscription lost on client restart.** Re-posted after every login.
