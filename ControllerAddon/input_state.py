"""Input state shared by EVERY copy of controller.py in the process.

WHY THIS MODULE EXISTS
----------------------
controller.py is run as a script (`python controller.py routines/x.py`), so it lives in
sys.modules as `__main__`. Every routine and shared module then does `import controller`, which
loads THE SAME FILE A SECOND TIME under the name `controller`. Python caches modules by name, so
those are two distinct module objects with two distinct copies of every module-level variable.

PROVEN 2026-08-30, not inferred:

    same module object?  False
    main_like id: 2920848537488   imported id: 2920848676144

The engine loop lives in `__main__` and calls maybe_idle_mouse_move, which read `__main__`'s
copy of `_last_mouse_activity`. Routine clicks go through `controller`, bumping the OTHER copy.
So the engine never saw routine activity at all: its timer was permanently stale and the idle
drift fired as soon as its drawn still-budget elapsed, over and over, right through active work.
MEASURED that night -- drifts every 1.3-6s while the routine was clicking hotspots, each one
walking the cursor off target so the next click had to fly it back. USER: "when we flick i think
the idle function drags back the mouse".

This module is only ever imported BY NAME, never run as a script, so there is exactly one of it
however many times controller.py is loaded. Anything that must be shared across those copies
belongs here -- not in controller.py.
"""

import time

# When the mouse was last used deliberately: a move, or a click that moved nothing. The idle
# drift measures stillness from here.
_last_mouse_activity = time.monotonic()

# Until when an action is in flight, so the idle drift must hold off. Waiting a couple of seconds
# for a queued interaction to complete genuinely IS stillness, and the drawn still-segment budget
# is ~1.5s -- without this the drift fires between bursts and drags the cursor off target.
_busy_until = 0.0


def mark_mouse_activity() -> None:
    """The mouse was just used deliberately -- a move, or a click that moved nothing."""
    global _last_mouse_activity
    _last_mouse_activity = time.monotonic()


def seconds_since_mouse_activity() -> float:
    return time.monotonic() - _last_mouse_activity


def mark_busy(seconds: float) -> None:
    """Declare an action in flight for the next `seconds`, holding the idle drift off.

    Only ever EXTENDS the window: two overlapping actions must not let the shorter one cancel
    the longer one's suppression."""
    global _busy_until
    _busy_until = max(_busy_until, time.monotonic() + max(0.0, seconds))


def is_busy() -> bool:
    return time.monotonic() < _busy_until


def reset() -> None:
    """Test hook -- put both markers back to "idle right now"."""
    global _last_mouse_activity, _busy_until
    _last_mouse_activity = time.monotonic()
    _busy_until = 0.0

# The Arduino firmware protocol version probed by controller.main() -- mirrored here because
# controller.py is loaded twice (as __main__ and as `controller`) and the routine-side copy
# never sees main()'s module-level assignment. Read via controller._firmware_protocol().
firmware_protocol = 2
