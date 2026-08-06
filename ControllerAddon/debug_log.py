# debug_log.py
#
# Structured, persistent debug logging for controller.py and its routines -- see
# docs/superpowers/specs/2026-08-02-controller-debug-logger-design.md for the full design
# rationale. Replaces ad-hoc print(f"[tag] ...") calls scattered across controller.py, travel.py,
# login.py, and routines/*.py with one drop-in log() call that both keeps today's console output
# and, when enabled, appends a timestamped copy to a persistent per-run file -- so a single file
# can be handed over after a run to reconstruct exactly what the controller decided and what the
# bridge observed, instead of relying on whatever's still in the terminal scrollback.
#
# No new dependencies -- matches the codebase's existing plain-module style (nothing else here
# uses Python's logging package).

import os
import time
import traceback

STALL_THRESHOLD_S = 10  # seconds with no position/animation change before flagging "possibly stuck"

_enabled = False
_file = None
_tick_count = 0

_last_state_data = {}
_last_pos = None
_last_anim = None
_last_moved_at = None
_stalled = False

_UNSET = object()


def init(enabled: bool) -> None:
    """Call once at the top of main(), before the main loop starts. When enabled, opens a fresh
    logs/debug_YYYY-MM-DD_HH-MM-SS.log for this run (never overwritten -- old runs stay available
    for comparison). When not enabled, log()/log_exception() fall back to their current
    console-only behavior (identical to the print() calls they replace) and tick() becomes a
    no-op, so there's no extra bridge round-trips or filesystem work paid for a feature that isn't
    turned on."""
    global _enabled, _file
    _enabled = enabled
    if not enabled:
        return
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    path = os.path.join(logs_dir, time.strftime("debug_%Y-%m-%d_%H-%M-%S.log"))
    _file = open(path, "a", encoding="utf-8")
    print(f"[debug_log] logging this run to {path}")


def current_tick() -> int:
    """The tick number the next log()/tick() prefix will use -- for callers that want to
    reference "since tick=N" in their own message (see api_get's bridge-failure logging)."""
    return _tick_count


def _prefix() -> str:
    now = time.time()
    ts = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now % 1 * 1000):03d}"
    return f"[{ts} tick={_tick_count}]"


def _write_file(line: str) -> None:
    if _file is None:
        return
    _file.write(line + "\n")
    _file.flush()  # low-rate debug output, not a hot path -- flushing per line so a crash
                     # doesn't lose the last few lines is worth the (negligible) cost here.


def log(tag: str, message: str) -> None:
    """Drop-in replacement for print(f"[{tag}] {message}") -- always prints to console (today's
    behavior, unchanged), additionally appending a timestamped copy to the open log file when
    logging is enabled."""
    line = f"{_prefix()} [{tag}] {message}"
    print(line)
    _write_file(line)


def log_exception(tag: str, exc: BaseException) -> None:
    """Writes a full traceback to the log file only (when enabled) -- not the console. The
    caller (main()'s wrapper around routine.tick/setup/on_event) re-raises exc right after this,
    and Python's own default uncaught-exception handler already prints that traceback to the
    console when the process exits, so printing it here too would just duplicate it."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    _write_file(f"{_prefix()} [{tag}] EXCEPTION\n{tb}")


def tick(ctx) -> None:
    """Call first thing in every main-loop iteration, before any of the early-continue branches,
    so an enabled run leaves a trace of every single tick regardless of which branch the rest of
    that iteration takes (not logged in, window unfocused, on break, routine actually ticking,
    ...).

    Still increments the tick counter when logging isn't enabled -- log() calls made from
    elsewhere (e.g. api_get's bridge-failure lines) print to console regardless of the flag, and
    their tick=N prefix stays meaningful this way. The rest is a no-op, though: the snapshot needs
    its own get_player()/get_combat()/get_state() calls, on top of whatever the routine itself
    already does -- extra bridge round-trips every ~0.3s loop iteration (see
    STATE_POLL_INTERVAL) aren't worth paying for a feature that isn't turned on."""
    global _tick_count, _last_pos, _last_anim, _last_moved_at, _stalled, _last_state_data
    _tick_count += 1
    if not _enabled:
        return

    import controller  # local import: controller.py imports this module back, deferred to
                         # avoid circularity (same convention as main()'s own `import login`)

    player = controller.get_player()
    combat = controller.get_combat()
    state = controller.get_state()

    pos = (player.get("x"), player.get("y"), player.get("plane"))
    anim = player.get("animation")
    routine_path = getattr(ctx.state, "active_routine_path", None)

    parts = [
        f"pos=({pos[0]},{pos[1]},{pos[2]})",
        f"hp={player.get('hp')}/{player.get('hpReal')}",
        f"anim={anim}",
    ]
    if combat.get("inCombat"):
        ratio, scale = combat.get("targetHpRatio"), combat.get("targetHpScale")
        pct = f"{int(ratio / scale * 100)}%" if ratio is not None and scale else "?"
        parts.append(f"inCombat=True target={combat.get('targetName')}({pct})")
    else:
        parts.append("inCombat=False")
    parts.append(f"dialogOpen={state.get('dialogOpen')}")
    parts.append(f"focused={state.get('windowFocused')}")
    parts.append(f"routine={routine_path}")
    _write_file(f"{_prefix()} [snapshot] {' '.join(parts)}")

    # State-diff: ctx.state.data is each routine's own free-form bookkeeping dict -- its keys are
    # effectively the routine's internal state machine. Without this, a stuck routine just looks
    # like an identical snapshot repeating with no visibility into *why* it thinks it should keep
    # doing that.
    data = getattr(ctx.state, "data", {}) or {}
    for key, value in data.items():
        if _last_state_data.get(key, _UNSET) != value:
            _write_file(f"{_prefix()} [state] {key}: {_last_state_data.get(key)} -> {value}")
    _last_state_data = dict(data)

    # Stall detection: fires once when position AND animation have both been unchanged for
    # longer than STALL_THRESHOLD_S while a routine is actually supposed to be active (not on
    # break, not safety-stopped -- both legitimately hold still for a while). Only re-arms once
    # something actually changes, so a stall that persists for minutes still produces exactly one
    # line, not a repeat every tick.
    routine_active = not ctx.state.safety_stopped and not ctx.state.on_break()
    now = time.time()
    if _last_moved_at is None or not routine_active:
        _last_moved_at = now
        _stalled = False
    elif pos != _last_pos or anim != _last_anim:
        _last_moved_at = now
        _stalled = False
    elif not _stalled and now - _last_moved_at > STALL_THRESHOLD_S:
        log("!", f"possibly stuck: no position/animation change for {now - _last_moved_at:.1f}s (routine={routine_path})")
        _stalled = True
    _last_pos, _last_anim = pos, anim
