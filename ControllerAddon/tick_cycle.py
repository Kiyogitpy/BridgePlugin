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

# TWO DIFFERENT CLICK COSTS, BECAUSE A CLICK COSTS TWO DIFFERENT THINGS.
#
# click_human is: press, a CLICK_HOLD_RANGE_S hold (50-145ms), release, then a trailing
# human_delay(0.03, 0.09). Which part is on the tick's budget depends on what the click IS:
#
#   * A click that DELAYS FOLLOWING WORK (the in-place log click, whose release and trailing
#     pause both happen before the flight can start) costs the whole thing: hold + tail.
#   * The LAST click of the tick (the rock click) only has to LAND. Its trailing pause is our
#     own pacing and happens after mouse-up, when the game already has the click -- charging it
#     against the tick is charging for time the game does not care about.
#
# MEASURED LIVE 2026-09-08, and this was a real bug: booking the tail against the final click
# made the guard demand ~472ms while the geometry gate promised 237ms, so startup passed and
# then EVERY SINGLE CYCLE abandoned at room_for -- 50 desyncs, 0 completed cycles, before the
# first fix. The gate and the guard have to price the same thing.
#
# Upper bounds here, medians below: these feed Window.room_for, which is a GUARD, and being
# pessimistic costs one abandoned cycle where being optimistic desyncs the whole chain.
CLICK_HOLD_MAX_MS = settings.CLICK_HOLD_RANGE_S[1] * 1000.0          # 145
CLICK_TAIL_MAX_MS = 90.0                                             # human_delay(0.03, 0.09)
CLICK_COST_MS = CLICK_HOLD_MAX_MS + CLICK_TAIL_MAX_MS                # delays following work
CLICK_LANDING_COST_MS = CLICK_HOLD_MAX_MS                            # only has to land

# The MEDIAN cost of a click, for PLANNING rather than guarding. CLICK_COST_MS above books the
# worst case on purpose -- a guard that is optimistic desyncs a whole chain, where a guard that
# is pessimistic costs one abandoned cycle. But feeding that same worst case into the geometry
# check makes it demand the impossible: 600 - 60 - 235 - 235 leaves 70ms for the flight, which is
# below HM2_MIN_MS (90), so NO geometry on earth would ever pass. MEASURED 2026-09-08 while
# setting the routine up live -- the check refused a legitimate 60px setup. Same split the rest
# of this repo uses: plan on the median, guard on the tail.
CLICK_HOLD_MEDIAN_MS = 85.0        # CLAUDE.md's fitted median hold
CLICK_TAIL_MEDIAN_MS = 60.0        # the middle of human_delay(0.03, 0.09)
CLICK_COST_MEDIAN_MS = CLICK_HOLD_MEDIAN_MS + CLICK_TAIL_MEDIAN_MS   # 145, delays what follows
CLICK_LANDING_MEDIAN_MS = CLICK_HOLD_MEDIAN_MS                       # 85, only has to land


# A PRACTISED reach is faster than the fitted model, and this is the one activity where saying
# so is honest rather than convenient. settings.HM2_* was fitted across general human reaching:
# varied, unfamiliar targets, where most of the time goes into the corrective submovements that
# HM2_UNDERSHOOT_* and HM2_LATERAL_ERROR model. 3-tick granite reaches for the SAME two pixels
# thousands of times an hour; a human doing that for an hour has the movement in muscle memory
# and the correction phase largely disappears. USER 2026-09-08: "a human with known X Y
# cordinate most likely speed up too after some practice."
#
# So scaling the flight down here imitates the human being imitated MORE closely, not less --
# but only for a repeated fixed-target reach. Do NOT reach for this to make an ordinary routine
# faster; there the general fit is the right model and this would be a straightforward tell.
PRACTISED_FLIGHT_SCALE = 0.65


def max_reach_px(budget_ms, scale=1.0):
    """The furthest a flight may travel and still fit `budget_ms`, i.e. the inverse of
    estimate_flight_ms. Returns 0.0 when even the model's floor does not fit.

    This exists because a hand-picked pixel limit is a guess that does not follow from the
    budget, and MEASURED LIVE 2026-09-08 the guess was wrong by a factor of three: the config
    said 220px while the arithmetic only allows 82px, so a geometry the routine would have
    accepted could not have hit a single tick. Deriving it means the refusal message can tell
    the user the real target instead of an invented one."""
    budget_ms = budget_ms / (scale or 1.0)
    if budget_ms <= settings.HM2_MIN_MS:
        return 0.0
    doublings = (budget_ms - settings.HM2_MED_REF_MS) / settings.HM2_SLOPE_MS_PER_BIT
    return settings.HM2_D_REF * (2.0 ** doublings)


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
    saved.

    This is the FIRST cycle of a chain only -- it has no previous critical tick to count from.
    Every cycle after it must go through at_tick, or the cycle period is whatever the caller's
    own work happened to take rather than a fixed number of ticks.

    THE SLEEP CROSSES A TICK BOUNDARY, SO THE STREAM IS PUMPED AGAIN AFTER IT. The pump before
    the sleep only anchors the boundary we are aiming at; without the second one `events.ticks
    .tick` still holds the tick we LEFT, and a caller reading current_tick() right after this
    returns records the wrong tick number -- which pins the next cycle one tick early and makes
    a 3-tick cycle fire as a 2-tick one. "Returns the phase actually woken at" is only honest if
    the tick number is actually current too."""
    if events is None:
        return None
    now = now_fn()
    events.pump(now=now)
    now = now_fn()
    if events.ticks.is_stale(now):
        return None
    sleep_fn((events.ticks.ms_until_next(now) + target_ms) / 1000.0)
    now = now_fn()
    events.pump(now=now)
    return tick_phase_ms(events, now_fn())


def current_tick(events, *, now=None):
    """The bridge's tick number, or None when the clock is unusable.

    `now` is injectable for the same reason everything else here is: a caller running on a
    patched clock must not silently fall back to the real one."""
    if events is None:
        return None
    if events.ticks.is_stale(time.time() if now is None else now):
        return None
    return events.ticks.tick


def at_tick(events, target_tick, target_ms, *, now_fn=time.time, sleep_fn=time.sleep,
            max_wait_ticks=6):
    """Sleep until tick `target_tick` is current, then `target_ms` into it.

    Returns the phase actually woken at, None when the clock is unusable, or the string
    "missed" when `target_tick` is already in the past -- the caller must resync rather than
    fire late, because a click placed in the wrong tick desyncs every cycle after it.

    at_phase cannot express this: it lands on the NEXT boundary whenever it is called, so the
    cycle period ends up being however long the caller's own work took. That is exactly the
    C1 defect this exists to close -- a 3-tick cycle has to be counted in TICKS, not inferred
    from how long an HTTP read happened to take.

    The wait is bounded by `max_wait_ticks` so a clock that keeps answering but stops ADVANCING
    (a wedged listener, a client paused under a modal) cannot hang the routine: that returns
    None, same as a stale clock, and the caller refuses rather than guessing."""
    if events is None:
        return None
    deadline = now_fn() + (max_wait_ticks + 1) * GAME_TICK_MS / 1000.0
    while True:
        now = now_fn()
        events.pump(now=now)
        now = now_fn()
        if events.ticks.is_stale(now):
            return None
        current = events.ticks.tick
        if current is None:
            return None
        if current > target_tick:
            return "missed"
        if current == target_tick:
            phase = events.ticks.phase_ms(now)
            if phase < target_ms:
                sleep_fn((target_ms - phase) / 1000.0)
            return tick_phase_ms(events, now_fn())
        if now >= deadline:
            return None
        # Never sleep past one boundary in a single step: a TICK packet has to be pumped every
        # step or the loop cannot see the tick it is waiting for arrive. The floor keeps a
        # boundary that has just passed (ms_until_next == 0) from spinning.
        step_ms = min(events.ticks.ms_until_next(now) + 1.0, GAME_TICK_MS)
        sleep_fn(max(step_ms, 5.0) / 1000.0)


def estimate_flight_ms(dist_px, scale=1.0):
    """The fitted HM2 reach model's MEDIAN time for a distance, for budget arithmetic only.

    Not a prediction of any individual flight -- HM2_SIGMA is 0.368, so the p90 is ~1.6x this.
    Window.room_for is deliberately fed this median and given a caller-side margin rather than
    a p90, because refusing every cycle whose worst case might overrun would refuse most of
    them.

    `scale` multiplies the result, matching move_to_human's own `duration_scale`, so a caller
    that flies faster gets a budget that agrees with what it will actually do. See
    PRACTISED_FLIGHT_SCALE for when that is legitimate."""
    if dist_px <= 1:
        return 0.0
    doublings = math.log2(max(float(dist_px), 1.0) / settings.HM2_D_REF)
    return scale * max(settings.HM2_MIN_MS,
                       settings.HM2_MED_REF_MS + doublings * settings.HM2_SLOPE_MS_PER_BIT)


def click_in_place(ser, *, now_fn=time.time):
    """Click wherever the cursor already is. Returns elapsed ms.

    This is the primitive that makes the critical tick affordable: the log click costs no
    flight at all because the cursor was parked on it during the previous cycle's dead ticks."""
    t0 = now_fn()
    controller.click_human(ser)
    return (now_fn() - t0) * 1000.0


def timed_click(ser, point, *, aim_radius=0, duration_scale=1.0, now_fn=time.time):
    """Fly to a PRE-RESOLVED (x, y) and click. Returns elapsed ms.

    `duration_scale` is passed through to move_to_human. See PRACTISED_FLIGHT_SCALE.

    NO HTTP ANYWHERE IN THIS CALL, which is why it is not controller.click_entity: that one
    reads the bridge at departure, once per refresh leg and again at the settle -- the ~1s per
    click CLAUDE.md already records for deep-sea-trawling. Inside the critical tick there is no
    room for a single round trip, so the caller resolves the point during the dead ticks and
    hands it here as plain numbers."""
    t0 = now_fn()
    controller.move_to_human(ser, int(point[0]), int(point[1]), aim_radius=aim_radius,
                             duration_scale=duration_scale)
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
