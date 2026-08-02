# Controller debug logger — design

## Problem

Bug-fixing on `ControllerAddon/controller.py` and its routines has been slow to converge: the
only signal today is ~90 ad-hoc `print(f"[tag] ...")` calls scattered across `controller.py`,
`travel.py`, `login.py`, and `routines/*.py`. Nothing is timestamped, nothing is persisted, and
there's no way to correlate "the bot did X" with "the game state was Y at that moment" after the
fact — by the time a bug is reported, the evidence has scrolled out of the terminal. This makes
the debugging loop (user hits a bug → describes it from memory → guesswork) much slower than it
needs to be.

Goal: a structured, persistent debug log that captures both what the controller *decided* and
what the bridge *observed* at that moment, so a single file can be handed over after a run and
used to reconstruct exactly what happened — especially for spotting routines that got stuck or
crashed silently.

## Architecture

New module `ControllerAddon/debug_log.py`. No new dependencies — matches the codebase's existing
plain-module style (nothing else here uses Python's `logging` package). Holds module-level state:
current tick counter, run start time, and an optional open file handle.

- `init(enabled: bool) -> None` — called once at the top of `main()`. If `enabled`, creates
  `ControllerAddon/logs/` if missing and opens `logs/debug_YYYY-MM-DD_HH-MM-SS.log` for the
  run. If not enabled, `log()`/`tick()` still print to console (matching today's behavior) but
  never touch the filesystem.
- `tick(ctx) -> None` — called first thing in every `while True:` loop iteration, before any of
  the early-`continue` branches. Increments the tick counter, then unconditionally writes one
  `[snapshot]` line (see below). This is what guarantees every single loop iteration leaves a
  trace, regardless of which branch the rest of that iteration takes (not logged in, window
  unfocused, on break, routine actually ticking, etc.).
- `log(tag: str, message: str) -> None` — drop-in replacement for `print(f"[{tag}] {message}")`.
  Always prints to console (unchanged terminal experience); additionally appends to the open file
  when logging is enabled. Every line, from every call, is prefixed
  `[HH:MM:SS.mmm tick=N]` using the current tick counter.
- `log_exception(tag: str, exc: BaseException) -> None` — formats and writes a full traceback
  block (via `traceback.format_exception`), used by the new exception wrapper around routine
  entry points (see below).

## What gets captured

### Snapshot line (every tick, unconditional)

Pulls only cheap, already-used endpoints — `get_state()`, `get_player()`, `get_combat()` — never
the heavy scene-walk endpoints (`/objects`, `/npcs`, `/tiles`, `/ground-items`) that are known to
cause client stutter when polled frequently (see `tools/bridge-dashboard.html`'s existing
`HEAVY_ENDPOINTS` pause-by-default handling).

Fields, drawn from the actual JSON shapes returned by `PlayerProvider`/`CombatProvider`/
`GameStateProvider`:

```
[14:03:41.203 tick=142] [snapshot] pos=(3199,3420,0) hp=27/30 anim=808 inCombat=True target=crab(45%) dialogOpen=False focused=True routine=gemstone_crab
```

- `pos` — `player.x/y/plane`
- `hp` — `player.hp/hpReal`
- `anim` — `player.animation`
- `inCombat`/`target`/target hp — `combat.inCombat`, `combat.targetName`, `combat.targetHpRatio`
  (omitted when not in combat)
- `dialogOpen`, `focused` — `state.dialogOpen`, `state.windowFocused`
- `routine` — the active routine's file path relative to `ControllerAddon/` (same value already
  used by today's `print(f"[routine] running {desired_path}")`, e.g. `routines/gemstone_crab.py`)

### State-diff line (on change)

`ctx.state.data` is each routine's own free-form bookkeeping dict — its keys are effectively the
routine's internal state machine (e.g. `gemstone_crab`'s `tunnel_retry_at`, `shipwreck_salvaging`
or `travel`'s route/waypoint index). `debug_log.tick()` keeps a shallow copy from the previous
tick and logs only keys that changed:

```
[14:03:41.204 tick=142] [state] tunnel_retry_at: None -> 1780441421.0
```

Without this, a stuck routine just looks like an identical snapshot repeating with no visibility
into *why* it thinks it should keep doing that.

### Action lines (centralized at the input layer)

Rather than relying on every routine to remember to narrate its own clicks, the three actual
input functions — `click_entity`, `click_human`, `key_tap` in `controller.py` — get one `log()`
call added directly inside them. This guarantees complete action visibility regardless of which
routine or helper triggered it:

```
[14:03:41.980 tick=142] [action] click_entity target=crab(id=1234) at (512,331)
[14:03:42.100 tick=142] [action] key_tap SPACE
```

### Stall detection (automatic, on threshold)

`debug_log.tick()` tracks the last tick at which player position or animation changed. If neither
has changed for longer than a fixed threshold (`STALL_THRESHOLD_S = 10`, a module constant, not
configurable via bridge config) while a routine is active (not on break, not safety-stopped), it
emits one `[!]` line. After firing, it won't fire again until position/animation change at least
once (ending the stall) and then stay unchanged past the threshold again — so a stall that
persists for minutes still produces exactly one line, not a repeat every tick:

```
[14:03:55.001 tick=185] [!] possibly stuck: no position/animation change for 12.9s (routine=gemstone_crab)
```

This is the single highest-value line for spotting "stuck" at a glance — grep for `[!]` to jump
straight to the interesting moment instead of reading the full tick-by-tick snapshot dump.

### Bridge failures (on state change)

`api_get()` currently swallows all request exceptions and returns `None` silently — if the Java
plugin restarts or the RuneLite client closes mid-run, every `get_*()` wrapper quietly starts
returning empty/default values, which can look identical to "the routine is stuck" from the log
alone. `api_get()` gets a `log()` call on the first failure and again on recovery (edge-triggered,
not every tick):

```
[14:04:01.442 tick=201] [!] bridge unreachable: GET /player failing since tick=198
[14:04:03.100 tick=208] [routine] bridge recovered after 6 failed requests
```

### Exceptions (crash-preserving)

`routine.tick(ctx)`, `routine.setup(ctx)`, and `routine.on_event(ctx, ...)` calls in `main()` get
wrapped in `try/except Exception`. On catch: `debug_log.log_exception(...)` writes the full
traceback to the file, then the exception is re-raised — the process still crashes exactly like
today (so it's still immediately obvious something broke), but now the file has the evidence
instead of a lost terminal scrollback.

## Toggle & file lifecycle

- `--debug-log` CLI flag on `controller.py`, alongside the existing `routine` positional arg.
  Also honors a `DEBUG_LOG=1` environment variable as an equivalent (`args.debug_log or
  os.environ.get("DEBUG_LOG") == "1"`), since it's a one-line check and env vars are convenient
  for scripted/detached runs.
- One fresh file per run: `ControllerAddon/logs/debug_YYYY-MM-DD_HH-MM-SS.log`. Never overwritten
  — old runs stay available for comparison.
- `ControllerAddon/logs/` added to `.gitignore`.

## Migration scope

All ~90 existing `print(f"[tag] ...")` call sites across `controller.py`, `travel.py`,
`login.py`, and every file in `routines/` (`gemstone_crab.py`, `mining.py`,
`shipwreck_salvaging.py`, `slayer.py`, `tormented_demon.py`) get mechanically converted to
`debug_log.log("tag", f"...")`, preserving the exact existing tag text so the format stays
recognizable to anyone used to today's console output.

## Testing

No existing test suite covers `ControllerAddon/` (it's a live-hardware-driving bot, not
unit-testable in the traditional sense). Verification is manual:
- Run `python controller.py --debug-log` against a live RuneLite session and confirm
  `logs/debug_*.log` is created, contains snapshot lines every tick, and console output is
  unchanged from today.
- Deliberately trigger one stall (e.g. pause a routine mid-walk) and confirm the `[!] possibly
  stuck` line appears once, not repeatedly.
- Deliberately raise an exception inside a routine's `tick()` (temporary test edit) and confirm
  the traceback lands in the file before the process exits.
