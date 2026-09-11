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
import input_state
import settings

GAME_TICK_MS = bridge_events.GAME_TICK_MS
LATE_TICK_POLL_MS = 15.0     # see at_tick: how quickly a late TICK packet is noticed

# A PRACTISED 3-TICK CLICK IS A FAST CLICK, and the two clicks of the critical tick are the one
# place in this repo where that matters. controller.click_human is built for ordinary play: a
# 50-145ms hold plus a 30-90ms trailing pause, ~166ms MEASURED live 2026-09-08 for the in-place
# log click alone. Inside a 600ms tick that pause is pure loss -- it happens after mouse-up, so
# the game already has the click, and all it does is delay the flight that follows.
#
# A human hammering two clicks into one tick does not pause between them either, so a short hold
# and no trailing pause is the more faithful imitation here, not a shortcut. Everywhere else in
# the codebase click_human remains right.
FAST_HOLD_RANGE_S = (0.035, 0.075)


def fast_click(ser):
    """click_human with a practised-cadence hold and no trailing pause. See FAST_HOLD_RANGE_S."""
    input_state.mark_mouse_activity()
    controller.mouse_left_down(ser)
    controller.human_delay(*FAST_HOLD_RANGE_S)
    controller.mouse_left_up(ser)


# WHAT A CLICK COSTS THE TICK. The in-place log click DELAYS the flight that follows it: its
# whole hold is on the budget (an inventory click fires on mouse UP -- MEASURED from USER's
# recordings 2026-09-09, the client waits for the release to tell a click from a drag). The
# rock click that ends the tick only has to be PRESSED inside it (a world click fires its menu
# action 1-23ms after mouse DOWN, same recordings), so nothing is booked for it.
#
# There is no per-cycle guard any more -- the routine fires the rock click regardless, because
# a late click costs a 4-tick pay and a skipped one costs the log -- so these only feed the
# geometry check, which plans on the median.
CLICK_HOLD_MAX_MS = FAST_HOLD_RANGE_S[1] * 1000.0                    # 75
CLICK_TAIL_MAX_MS = 0.0                                              # fast_click has no tail
CLICK_COST_MS = CLICK_HOLD_MAX_MS + CLICK_TAIL_MAX_MS
CLICK_HOLD_MEDIAN_MS = 55.0        # middle of FAST_HOLD_RANGE_S
CLICK_TAIL_MEDIAN_MS = 0.0
CLICK_COST_MEDIAN_MS = CLICK_HOLD_MEDIAN_MS + CLICK_TAIL_MEDIAN_MS


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
        #
        # AND POLL FAST WHILE WAITING FOR THE VERY NEXT TICK. MEASURED live 2026-09-09
        # (g3t_live27/29): fletches landing at phase ~583 instead of the drawn 150-320. When
        # the target tick's packet arrives a few ms LATER than the model's boundary, `current`
        # is still target-1 while the model's phase has already wrapped to ~0 -- so
        # ms_until_next reads ~600 and this slept a whole extra tick, waking with the target
        # tick nearly spent. A late packet has to be noticed within a few ms, not a tick.
        if current == target_tick - 1:
            step_ms = min(events.ticks.ms_until_next(now) + 1.0, LATE_TICK_POLL_MS)
        else:
            step_ms = min(events.ticks.ms_until_next(now) + 1.0, GAME_TICK_MS)
        sleep_fn(max(step_ms, 5.0) / 1000.0)


def estimate_flight_ms(dist_px, scale=1.0):
    """The fitted HM2 reach model's MEDIAN time for a distance, for budget arithmetic only.

    Not a prediction of any individual flight -- HM2_SIGMA is 0.368, so the p90 is ~1.6x this.

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
    fast_click(ser)
    return (now_fn() - t0) * 1000.0


SETTLE_PX = 25.0      # a landing further than this from the live target gets one short correction
LAST_FLIGHT = {}      # the breakdown of the most recent timed_click, for the routine's log line


def timed_click(ser, point, *, aim_radius=0, duration_scale=1.0, now_fn=time.time,
                refresh_target=None, refresh_legs=None):
    """Fly to `point` and click. Returns elapsed ms.

    `duration_scale` is passed through to move_to_human. See PRACTISED_FLIGHT_SCALE.

    With no `refresh_target` this makes NO HTTP call at all: the caller resolved the point
    during the dead ticks and hands it here as plain numbers. With one, the flight re-aims at
    each leg through it and takes one last look before the press -- each look is a single
    uncached /object/at (~20ms), which the critical tick CAN afford, and MEASURED live
    2026-09-09 (g3t_live31 recorder) it cannot afford to skip: the player is mid-step when the
    rock click lands and the camera pans with them, so a point resolved 300ms earlier is off
    the rock and the click resolved to "Walk here" in 9 of 12 cycles."""
    t0 = now_fn()
    controller.move_to_human(ser, int(point[0]), int(point[1]), aim_radius=aim_radius,
                             duration_scale=duration_scale, refresh_target=refresh_target,
                             refresh_legs=refresh_legs)
    t1 = now_fn()
    settled = False
    if refresh_target is not None:
        fresh = refresh_target()
        if fresh is not None:
            cur = controller.get_cursor_pos()
            if math.hypot(fresh[0] - cur[0], fresh[1] - cur[1]) > SETTLE_PX:
                settled = True
                controller.move_to_human(ser, int(fresh[0]), int(fresh[1]), aim_radius=2,
                                         duration_scale=duration_scale)
    t2 = now_fn()
    fast_click(ser)
    t3 = now_fn()
    LAST_FLIGHT.update(move_ms=(t1 - t0) * 1000.0, settle_ms=(t2 - t1) * 1000.0,
                       settled=settled, click_ms=(t3 - t2) * 1000.0)
    return (t3 - t0) * 1000.0
