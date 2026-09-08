# 3-tick granite mining — design

Written 2026-09-08 from the OSRS Wiki (Tick manipulation rev 15292712, Pay-to-play Mining
training rev 15297907, Granite rev 15276608, Granite rocks rev 15276607), plus a read of this
repo's own input pipeline (`settings.py`'s HM2 movement model, `controller.click_entity`,
`bridge_events.TickModel`).

**THERE IS NO HUMAN RECORDING AND NOTHING HERE HAS BEEN RUN LIVE.** Every number below is
either MEASURED from a source named inline or is a first-draft guess; the "Live-verification
checklist" at the end is the first session's worklist. The expected outcome — the pattern from
every routine built this way in this repo — is that geometry and tick-phase facts are wrong
before any logic is.

USER's decisions, and they shape everything:

- **Knife + teak/mahogany logs** as the 3-tick item, and **the log is not consumed** (USER's
  answer; teak and mahogany have no knife-fletch product, so the action starts, sets the
  skilling timer and makes nothing). This is the single largest scope reduction in the design:
  there is **no supply loop, no restock, and no bank trip**.
- **Cape Conch mine**, so there is **no desert heat** and nothing to maintain to stay alive.
- **A fixed rock rotation captured once into config**, not a per-tick `/objects` scan.
- **Drop a full inventory at once**, not between mines.
- **Resync and carry on** after a desync — an ordinary missed tick never stops the run.
- **A deliberate, drawn miss rate**, because a machine that never misses a 3-tick cycle is
  itself the signature.
- **USER sets the client geometry up** (small client, zoomed in, camera south); the routine
  measures it and refuses to run when it is out of budget.

## Why this cannot be gathering.py

`gathering.py` and every other routine in this repo is **reactive**: click, then wait on a
pushed signal confirmed by HTTP. The tick boundary is where a click *lands*, never where it
*aims*. 3-tick granite is the opposite — it is a **schedule**. Two clicks must fall inside one
named 600ms window, and being right about *what* to click buys nothing if the click is 80ms
late. Nothing in this codebase schedules input inside a tick, which is why this is a new module
and not a `gathering.Profile`.

`gathering.py` is **not modified by this work.** The routine reuses `controller.drop_items`,
`controller.move_to_human`, `controller.click_human`, `debug_log` and `bridge_events`, and
nothing else.

## The mechanic

From Tick manipulation §"3-tick Mining" and §"The skilling timer", and P2P Mining training
§"Levels 45–99: Granite > Setup and tips":

- Mining normally runs off the pickaxe's speed (4 ticks for adamant, 5 for the movement case).
  An action that **sets the skilling timer to 3** bypasses it. Knife-on-teak-log is such an
  action.
- **The 3-tick action must be started on the same tick as moving to the next rock.** If the
  action starts first, the player waits three ticks for the timer to hit zero and the cycle is
  wasted.
- Rocks give **two rolls when moved to** (the same mechanic as player-planted trees): the
  second roll only resolves when the first fails, so moving between rocks raises the yield
  rate. This is why the rotation exists at all — mining one rock in place forfeits it.
- The processed item (here: the log) must be the **only** one of its kind loose in the
  inventory, or a Make-X interface opens and eats a tick.
- Granite rocks respawn in **5 seconds** and give **50 xp** at Mining 45. The wiki quotes
  ~116,000 xp/hr at level 70 and ~132,000 at 99 for this method.

## The insight that makes it feasible on our hardware

Our reach time is MEASURED in `settings.py`: median **170ms at 52px** (`HM2_MED_REF_MS`,
`HM2_D_REF`), log-sigma **0.368** (`HM2_SIGMA`, so p90 ≈ 270ms), **+120.5ms per doubling of
distance** (`HM2_SLOPE_MS_PER_BIT`), floor 90ms. A click hold is **50–145ms**
(`CLICK_HOLD_RANGE_S`, median 85). So one aimed click costs ~**255ms median / ~420ms p90**.

Two aimed clicks in one 600ms tick is therefore ~510ms median and over budget at p90. But the
critical tick does not need two *flights* — only two *clicks*:

**Item selection in OSRS is client-side.** Clicking the knife to "Use" it sends nothing; the
packet is sent by the click on the target. So the selection, and the flight to the log, both
happen during the two dead ticks of the previous cycle, and the cursor is already parked on the
log when the critical tick opens.

The cycle, with the knife and the log in fixed adjacent inventory slots. The critical tick is
numbered T and the next one is pinned at **T + 3** — counted in ticks by
`tick_cycle.at_tick`, never inferred from how long the dead-tick work took:

| tick | action | cursor cost |
|---|---|---|
| T+1 (dead) | click the knife (selects it, no packet); fly to the log and park | two short flights across 600ms |
| T+2 (dead) | nothing; the cursor rests on the log | free |
| **T+3 (critical)** | `click_human` **in place** on the log (no flight) → the 3-tick action fires; one short flight to the rock; click | **~460ms median, ~700ms p90** — see below |

**The 92ms in-place figure is not the tick cost of a click, and an earlier draft of this spec
double-charged because of it.** The 92ms CLAUDE.md records (giants_foundry) is
reading-to-click-LANDING, i.e. roughly to mouse-DOWN. As wall-clock OCCUPANCY of the tick —
which is what the budget arithmetic needs — `click_human` is a 50–145ms hold (median 85) plus
a 30–90ms trailing `human_delay`: **~145ms median, ~235ms worst case, and the hold distribution
has no hard ceiling.** The old sum `60 + 92 + 270 + 145 = 567` charged the click cost two
different ways in one line (92 for one click, 145 for the other).

Honestly restated, with `logClickPhaseMs` 60:

| | phase | log click | flight to rock | rock click | total |
|---|---|---|---|---|---|
| median | 60 | 145 | 170 | 145 | **520ms** |
| p90 | 60 | ~200 | 270 | ~200 | **~730ms** |

So the median cycle fits in 600ms and the p90 cycle **does not** — which is why `Window.room_for`
exists and why some cycles are expected to abandon. `tick_cycle.CLICK_COST_MS` books the 235ms
worst case rather than the median, deliberately: the guard being conservative costs one abandoned
cycle, the guard being optimistic desyncs the whole chain.

**Whether an over-budget RELEASE actually matters is unknown**, and neither this spec nor the
bench answers it: if the game acts on mouse-DOWN, only the press has to land inside the tick and
the trailing hold/delay is free; if it acts on release, the whole 145–235ms is on the budget.
That question is now item 6 of the live-verification checklist.

**This is the load-bearing claim of the whole design**, and it is arithmetic, not measurement,
until the bench in Task 1 says otherwise. It also explains why the client geometry is a hard
precondition rather than advice: at 500px the flight alone is ~560ms (log2(500/52) ≈ 3.27
doublings × 120.5ms, plus the 170ms base) and the tick is gone. The wiki independently gives
the same advice to humans — "make the game client as small as possible… zoom in and turn the
camera south so that there is as little distance from the inventory to the rocks as possible".

## Modules

Two new Python modules, split so the timing engine is testable with no client, no bridge and no
serial port. No Java.

### ControllerAddon/tick_cycle.py (new)

The tick-phase scheduler. Pure functions over an injected clock (`now_fn`/`sleep_fn`), the
fake-clock fixture pattern from `tests/test_blast_furnace.py`.

- `at_tick(events, target_tick, target_ms, *, now_fn, sleep_fn, max_wait_ticks)` — sleep until
  a NAMED tick is current, then `target_ms` into it; `"missed"` when that tick has already
  passed, so the caller resyncs rather than firing late. **This is what pins the cycle to three
  ticks.** `at_phase` alone cannot: it lands on the next boundary whenever it happens to be
  called, so the period is however long the caller's own work took — a fast `prepare()` gives a
  2-tick cycle, which re-fires knife-on-log before the skilling timer has expired and yields
  nothing.
- `current_tick(events, *, now)` — the bridge's tick number, or None when the clock is unusable.
- `at_phase(events, target_ms, *, now_fn, sleep_fn)` — sleep until the next tick boundary plus
  `target_ms`. Used only by the FIRST cycle of a chain, which has no previous critical tick to
  count from. Built on `EventStream.ticks` (`TickModel.phase_ms` / `ms_until_next`), which is
  anchored on the bridge's own `atMillis` — Java's `System.currentTimeMillis()` at the real tick
  boundary — not on when Python drained the packet. That anchoring is exactly why this is
  possible at all; see `TickModel.phase_ms`'s own docstring.
- `timed_click(ser, point, *, now_fn)` — move to a pre-resolved `(x, y)` and click, returning
  elapsed ms. **No HTTP anywhere in this call.** `click_entity` cannot be used: with a
  `refresh_target` it reads the bridge at departure, once per leg and again at the settle, which
  is the "~1s per click" CLAUDE.md already records for deep-sea-trawling.
- `Window` — tracks how much of the 600ms budget a cycle has spent, and answers
  `room_for(estimate_ms)`, fed `estimate_flight_ms(dist) + CLICK_COST_MS` (the flight's median
  plus the click's WORST case). **A cycle that cannot fit its remaining flight-and-click is
  ABANDONED BEFORE THE FLIGHT BEGINS** — it is simply not started. An earlier draft described
  this as aborting via `controller.request_movement_abort()` and the v3 firmware `X` flush;
  that call was a no-op (no flight is in progress, and `resync` cleared the flag immediately
  afterwards) and has been removed. Do not "restore" it: `X` is only legal from inside
  `move()`'s own flight loop, which is exactly where this code is not.

### ControllerAddon/routines/granite_3t.py (new)

The routine. Startup assertions, rotation, cycle loop, drop handling, desync accounting,
stuck watch.

### Java

**None required for v1.** The routine is launchable as
`python controller.py routines/granite_3t.py`. Adding it to `RoutineChoice.java` and
`ROUTINE_FILES` (the two steps CLAUDE.md documents as required for panel selection) is deferred
to a follow-up task so that the first live session needs no client rebuild.

## Configuration

`DEFAULT_CONFIG` only — this routine does not read `GET /config`, same as `gemstone_crab.py` and
`pest_control.py`, so it needs no `ControllerConfig.java` section.

```
rockTiles:            [(x,y,plane), x4]   # captured once at Cape Conch, cycled in order
rockId:               int                 # the granite rock's object id, for get_object_at
knifeName, logName:   str                 # substring match, this repo's convention
maxReachPx:           220                 # startup refuses above this, reporting the measurement
logClickPhaseMs:      60                  # where in the critical tick the log click lands
keepItems:            ["pickaxe"]         # never dropped, and see "Startup assertions" 5
rockClickTickOffset:  0                   # LIVE-VERIFY (see checklist item 2); 0 = the rock
                                          #   click shares the log click's tick, -1 = it leads
                                          #   by one, inverting the order within the cycle
missChance:           0.03                # deliberate one-tick-late rock click
fullInventory:        "drop"
xpStallStopS:         60
```

`logClickPhaseMs` of 60 is a GUESS and the table above says why it is a tight one: the median
cycle finishes at ~520ms of the 600ms tick and the p90 does not finish at all. The routine logs
measured per-cycle occupancy (one line per cycle plus a median/max summary every 25) so it can
be fitted rather than guessed.

## Startup assertions

All of these **refuse to start**, with the measurement in the log, rather than degrading — the
shape `deob_guard.py` already uses:

1. A knife matching `knifeName` and **exactly one** log matching `logName`. More than one log
   means a Make-X interface will eat a tick. Both are found by name; their slots are USER's
   arrangement and only matter because adjacent slots make the parked-cursor flight short.
2. All four `rockTiles` resolve to on-screen points.
3. `max |rock − log|` in pixels is within `maxReachPx`, **reported as a number either way** so a
   refusal tells USER how much to zoom.
4. `ctx.events` is not `None` and `ctx.events.ticks.is_stale()` is false.
5. The inventory holds NOTHING but the knife, the log, `keepItems` and granite. The drop
   shift-drops everything not on the keep list, so a carried pickaxe, teleport tablet, food or
   piece of jewellery would be binned on the floor of a busy mine about a minute into the run —
   and the failure is silent and delayed: mining then earns nothing and the XP stall watch stops
   the run 60s later blaming contested rocks. The refusal NAMES the offending items.

**These run from `tick()`, not from `setup()`, and that is load-bearing.** `setup()` is called
BEFORE the engine's login and focus gates (`controller.py`: `routine.setup(ctx)` at 3539,
`login.is_fully_logged_in()` at 3552, `windowFocused` at 3558). At the login screen
`get_inventory()` answers `[]` and no TICK packet has arrived, so assertions 1 and 4 would BOTH
fail and the run would be permanently safety-stopped before it began — the bite CLAUDE.md
already records for GOTR run 47. So `setup()` only records that the checks are owed; `tick()`
runs them and **defers quietly** (no stop) while the inventory reads empty or the clock is
stale, because those mean "cannot tell yet". `STARTUP_DEFER_MAX_S` (60s) bounds the deferral so
a genuinely misconfigured run refuses loudly instead of idling forever.

Assertion 4 is the one place in this repo where CLAUDE.md's standing rule — *every consumer
must tolerate `ctx.events is None` and keep a working fallback* — becomes a hard requirement
instead. There is no fallback: without the tick clock there is no tick phase, and without the
phase this routine is an ordinary 5-tick miner with extra steps. Stated explicitly here so a
future reader does not "fix" it into a silent polling fallback.

## The cycle loop

`tick()` chains cycles rather than running one per engine iteration: the engine loop costs ~1s
(`LoopStats`, CLAUDE.md), which is longer than the whole cycle. Bounded by `CHAIN_MAX_S` (45s)
so safety checks, breaks and `PLAYER_DIED` still get their turn — the same shape as
`gathering.tick` and `giants_foundry`.

Because `tick()` is held, `on_event` never runs during a chain. Events are read with
`EventStream.since(mark)` and **`mark` is advanced to `ctx.events.mark()` immediately after
every read** — the Hunllef C2 rule: re-taking a fresh `mark()` at the top of each iteration
reads a sequence number the previous iteration's own tick wait already pumped past, silently
dropping everything that arrived during the wait.

`controller.mark_busy` is called across every cycle. `maybe_idle_mouse_move` — the idle drift —
is documented in CLAUDE.md as having fired mid-work before, and here it would drag the cursor
off the parked log and break the cycle with nothing in the log to say so.

## Desync

USER's decision: **resync and carry on, with no ceiling on desyncs.**

A cycle is judged by the Mining XP delta over the cycle (`miningXp` on `/player`, which is
sub-tick — the same signal `gathering.py` uses). A cycle that produced nothing is not itself an
error: granite has a real failure rate and the second roll is a bonus, not a guarantee. So the
desync detector is the *window*, not the yield: a cycle whose measured occupancy overran the
600ms budget, or whose log click could not be placed at its phase, pauses to the next clean
boundary and re-enters. It logs one line carrying the measured phase and occupancy.

**One belt, and it is deliberately not a desync ceiling.** A stuck watch on Mining XP:
`xpStallStopS` (60s) with **no XP at all** safety-stops. **The clock only counts time during
which `tick()` was actually running**: breaks are on by default (20–40 min apart, 2–8 min long)
and `tick()` is not called during one, nor during an F2 pause, a window unfocus or a
reconnect wait, so no XP can arrive across any of them. A gap over `TICK_GAP_RESEED_S` (5s)
between `tick()` calls re-seeds the stall clock and logs one line saying so —
`wintertodt.py` stands its own watch down between rounds for the same reason.

An EMPTY `/inventory` read is likewise never a stop on its own: `controller.get_inventory()`
returns `[]` on any bridge error, so a 200ms hiccup, a client GC pause or a LOADING screen would
otherwise end an unattended run with a false message. `EMPTY_INVENTORY_STOP` (20 consecutive)
still catches a genuinely emptied inventory. That is not "stop after N misses" — an
ordinary desync, or a run of them, never trips it. It fires when the rocks are contested, the
camera has moved, or the client is stuttering, i.e. when the cursor is clicking a dead pixel.
CLAUDE.md's standing lesson applies: a silent loop is worse than a crash, and this routine's
failure mode is *especially* silent, because a perfectly-aimed click into the wrong tick looks
identical in the log to a good one.

## Full inventory

Knife + log leaves 26 slots, so a drop comes due roughly every 26 cycles ≈ 60–70s. Handled by
`controller.drop_items`, which CLAUDE.md records as returning the **confirmed** delta and
polling `DROP_CONFIRM_S` — a verification window shorter than a server tick reads a good drop as
a failure. The knife and the log are on the keep list by name.

`get_config` forces `safety.stopOnInventoryFull` off, for the reason `gathering.get_config`
already documents: `check_safety` runs before `tick` and would otherwise win the race.

The keep list is `knifeName` + `logName` + `keepItems` (default `["pickaxe"]`, matching
`gathering.py`'s mining profile). Startup assertion 5 above is the other half of this: the keep
list only knows the names it was told about, so anything unexpected is refused at startup rather
than silently binned later.

## Humanization

- **No simulated misclicks anywhere in the cycle** (`simulate_mistakes=False`). A corrective
  reclick mid-cycle is a wrecked cycle, and in the inventory a near-miss plus its reclick drops
  the log — the same rule `banker.py` and `blast_furnace.py` already follow for interface and
  container clicks.
- **`missChance`** instead: a drawn fraction of cycles place the rock click one tick late on
  purpose. This is the honest replacement for the misclick simulation — a human 3t player's
  rate visibly dips and recovers, and CLAUDE.md's telemetry section is explicit that
  `EVENT_MOUSE_CLICK_V2` carries inter-press timing to the server at 1ms resolution
  independently of anything else, so a flawless 1.8s cadence for six hours is on the wire.
- The existing session tempo drift (`HM2_TEMPO_SIGMA`/`HM2_TEMPO_TAU_S`) applies to the flights
  unchanged and supplies further variance for free.
- The in-wait fidget needs **no** suppression, and it is worth saying why rather than adding a
  guard that does nothing: `controller._maybe_fidget_during_wait` only fires on a `human_delay`
  draw of 2s or more, and the only `human_delay` inside a cycle is `click_human`'s own 50–145ms
  hold. So the parked cursor cannot be nudged by it. It remains free to run during the drop and
  between chains, which is where the stillness it exists to break up actually occurs.
- **There is no `standTile` and no travel.** The routine assumes the player is standing at the
  rotation; the startup assertions fail if the rocks do not resolve on screen. Walking back
  from a drift would be new machinery for a case the four-adjacent-rock rotation does not
  produce, and the XP stall watch already stops a run that has wandered.

## Testing

- `tests/test_tick_cycle.py` — the scheduler against a fake clock and a **real** `EventStream`
  fed through a scripted `controller.poll_udp`, so `mark`/`since` semantics are what is
  exercised. Lesson carried from `test_mastering_mixology.py`: feed each event from the click
  that causes it; a script that queues a later verdict up front delivers it during an earlier
  wait, which cannot happen live.
- `tests/test_granite_3t.py` — rotation order, each startup refusal, drop-at-full, desync
  re-entry, and that the knife and log are never dropped.
- An autouse `_no_live_bridge` fixture in both. `tests/test_banker.py` was caught reaching the
  running client and drifting; both suites here must be hermetic.

## Live-verification checklist

The first session's worklist, in the order the failures would bite:

1. **Does knife-on-teak-log leave the log intact, and set the timer with no Make-X interface?**
   USER says yes; it is item 1 because the entire no-supply-loop scope rests on it. The routine
   re-counts the log **between chains and at every drop**, and safety-stops loudly if the count
   has fallen rather than running dry unnoticed. At one log per cycle a consumed log would be
   gone within seconds, so a between-chain check is early enough to catch it on the first
   session.

**On where HTTP is allowed.** The constraint is the **critical tick**, not the cycle. The two
dead ticks carry 1200ms of slack, and the routine needs them: the four rocks' screen
projections change every cycle because the player *moves* between them, so the next rock's
point is re-resolved with `controller.get_object_at` during tick T+1, alongside the knife
selection. What must never happen is an HTTP read between the log click and the rock click.
The knife and log points, by contrast, are resolved once per chain — items fill the inventory
from low slots upward, so with the knife and log in the first two slots the granite stacks
above them and their screen positions do not move.
2. **`rockClickTickOffset`.** The wiki says the action must start "on the same tick as moving to
   the next rock". A click on tick T causes movement on T+1, so "moving" may mean the tick the
   walk executes rather than the tick it is clicked. If the yield rate is poor at offset 0, try
   −1. This is the highest-risk unknown in the design.
3. **Real click-window occupancy on this hardware.** The routine logs one line per cycle
   (`cycle ok: opened at phase Xms, in-place Yms, flight+click Zms, occupancy Wms of 600ms`)
   plus a median/max summary every 25 cycles; fit `logClickPhaseMs` from it. If p90 occupancy
   exceeds ~500ms the arithmetic above has failed, and the honest answer is the 4-tick celastrus
   variant (2400ms per cycle), not tuning.
4. **Does the second roll actually land?** Compare granite/hour against the wiki's ~116k xp/hr
   at 70. A rotation that is not buying the movement stall will read close to plain mining.
5. Whether the parked cursor survives a full cycle untouched — i.e. that `mark_busy` and the
   fidget suppression are sufficient.
6. **Does the game act on mouse-DOWN or on mouse-UP?** The whole budget above turns on it: if
   the press is what counts, the click's 50–145ms hold and 30–90ms trailing delay are free and
   `CLICK_COST_MS` is over-conservative by ~235ms per click; if the release is what counts, the
   p90 cycle genuinely does not fit and `logClickPhaseMs` has to come down. Measurable by
   deliberately placing a rock click so its press lands inside the tick and its release does not,
   and reading whether the mine started. Neither the spec nor `tick_bench.py` answers this today.
7. **`rockTiles` and `rockId` have no config surface.** There is no `ControllerConfig.java`
   section for this routine, so the panel's dropdown entry ships with an empty rotation and
   selecting it stops immediately with "no rockTiles configured -- capture them with the probe".
   That is safe and well-messaged, but it means the tiles must be captured with
   `python routines/granite_3t.py --probe` at the mine and pasted into `DEFAULT_CONFIG` in the
   Python file before a first run.
