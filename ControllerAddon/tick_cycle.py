"""Scheduling input INSIDE a game tick.

Every other routine in this repo is REACTIVE: click, then wait on a pushed signal confirmed by
HTTP, and the tick boundary is where the click LANDS. 3-tick granite is the opposite -- two
clicks must fall inside one named 600ms window, and being right about WHAT to click buys
nothing if the click is 80ms late. This module is the scheduler that makes that expressible.

Everything here takes now_fn/sleep_fn so it is testable with no client, no bridge and no serial
port (the fake-clock fixture pattern from tests/test_blast_furnace.py).

The clock underneath is bridge_events.TickModel, which is anchored on the bridge's own
`atMillis` -- Java's System.currentTimeMillis() at the REAL tick boundary -- rather than on when
Python happened to drain the packet. That anchoring is the only reason sub-tick scheduling is
possible at all; see TickModel.phase_ms's own docstring.

A stale or absent clock is answered with None, never with a guess. Callers must refuse to run:
without a tick phase this whole approach degrades to an ordinary 5-tick miner with extra steps,
and doing that silently would be worse than stopping.
"""

import math
import time

import bridge_events
import controller
import settings

GAME_TICK_MS = bridge_events.GAME_TICK_MS


def tick_phase_ms(events, now):
    """Milliseconds since the last tick boundary, or None when the clock cannot be trusted."""
    if events is None:
        return None
    if events.ticks.is_stale(now):
        return None
    return events.ticks.phase_ms(now)


def at_phase(events, target_ms, *, now_fn=time.time, sleep_fn=time.sleep):
    """Sleep until the NEXT tick boundary plus `target_ms`, returning the phase actually woken
    at (or None when the clock is unusable, in which case nothing was slept).

    Always the NEXT boundary, even when `target_ms` is still ahead in the current tick: a caller
    building a fixed-length cycle needs a predictable period more than it needs the odd tick
    saved."""
    if events is None:
        return None
    now = now_fn()
    events.pump(now=now)
    now = now_fn()
    if events.ticks.is_stale(now):
        return None
    sleep_fn((events.ticks.ms_until_next(now) + target_ms) / 1000.0)
    return tick_phase_ms(events, now_fn())
