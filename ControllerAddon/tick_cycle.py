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


def estimate_flight_ms(dist_px):
    """The fitted HM2 reach model's MEDIAN time for a distance, for budget arithmetic only.

    Not a prediction of any individual flight -- HM2_SIGMA is 0.368, so the p90 is ~1.6x this.
    Window.room_for is deliberately fed this median and given a caller-side margin rather than
    a p90, because refusing every cycle whose worst case might overrun would refuse most of
    them."""
    if dist_px <= 1:
        return 0.0
    doublings = math.log2(max(float(dist_px), 1.0) / settings.HM2_D_REF)
    return max(settings.HM2_MIN_MS,
               settings.HM2_MED_REF_MS + doublings * settings.HM2_SLOPE_MS_PER_BIT)


def click_in_place(ser, *, now_fn=time.time):
    """Click wherever the cursor already is. Returns elapsed ms.

    This is the primitive that makes the critical tick affordable: the log click costs no
    flight at all because the cursor was parked on it during the previous cycle's dead ticks."""
    t0 = now_fn()
    controller.click_human(ser)
    return (now_fn() - t0) * 1000.0


def timed_click(ser, point, *, aim_radius=0, now_fn=time.time):
    """Fly to a PRE-RESOLVED (x, y) and click. Returns elapsed ms.

    NO HTTP ANYWHERE IN THIS CALL, which is why it is not controller.click_entity: that one
    reads the bridge at departure, once per refresh leg and again at the settle -- the ~1s per
    click CLAUDE.md already records for deep-sea-trawling. Inside the critical tick there is no
    room for a single round trip, so the caller resolves the point during the dead ticks and
    hands it here as plain numbers."""
    t0 = now_fn()
    controller.move_to_human(ser, int(point[0]), int(point[1]), aim_radius=aim_radius)
    controller.click_human(ser)
    return (now_fn() - t0) * 1000.0


class Window:
    """How much of one 600ms tick a cycle has spent.

    `phase_ms` is where in the tick the window was OPENED -- the cycle starts part way into the
    tick by design (logClickPhaseMs), so that time is already spent before the first click."""

    def __init__(self, opened_at, phase_ms, budget_ms=GAME_TICK_MS):
        self.opened_at = opened_at
        self.phase_ms = phase_ms
        self.budget_ms = budget_ms

    def spent_ms(self, now):
        return self.phase_ms + (now - self.opened_at) * 1000.0

    def remaining_ms(self, now):
        return self.budget_ms - self.spent_ms(now)

    def room_for(self, estimate_ms, now):
        return self.remaining_ms(now) >= estimate_ms
