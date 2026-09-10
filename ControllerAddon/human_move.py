"""Human mouse movement v2 -- an alternative to controller.move_to_human's original engine.

OFF by default (settings.HUMAN_MOVE_V2). controller.move_to_human dispatches here when it is on;
nothing else in the codebase calls into this module, and with the flag off the v1 path runs
byte-for-byte as before.

WHAT THIS FIXES, and how it was established
-------------------------------------------
recorder.py captured a human hand-playing golem crafting and the routine playing the same
activity, so the mover is the only thing that differs between the two datasets. Measured over
click-anchored reaches (human n=37, bot n=196):

                            human      bot
  timing explained by dist  0.280     0.512     <- the signature
  straightness              0.787     0.963     <- bot paths are too clean
  4+ submovements             42%        2%     <- bot collapsed onto 1-2
  time-to-peak               0.261    0.269     <- already correct, left alone
  median duration ms           432      184

The headline problem is NOT insufficient randomness. It is that the bot's randomness is
STRUCTURED: `duration = f(distance) * uniform(0.85, 1.15)` makes timing a function of distance.
Simulating that formula alone gives a log-duration sigma of 0.154 against the human's 0.675, so
the engine is simultaneously too predictable AND too tightly distributed. Widening the jitter
would fix only the second half.

Three changes follow, and one deliberate non-change:
  1. duration is drawn lognormally with only a WEAK distance term (see settings HM2_*),
  2. the path is an S-curve with tremor rather than a near-straight Bezier,
  3. submovements are emergent -- a deliberately undershooting primary reach plus corrections --
     instead of maybe_overshoot_point's single 42% coin flip,
  4. ease_in_out's velocity profile is UNCHANGED; it was measured already correct.

Requires Firmware.c v2 (the `S` segment command). Under v1 firmware `S` lines are ignored
entirely and the cursor will not move.

Design notes and the full derivation:
docs/superpowers/specs/2026-08-24-human-mouse-movement-v2-design.md
"""

import json
import math
import os
import random
import time

from settings import (
    HM2_CURVE_FRACTION, HM2_D_REF, HM2_FLUSH_COMMIT_MS, HM2_LATERAL_ERROR, HM2_LOOKAHEAD_SEGMENTS,
    HM2_LATERAL_DECAY, HM2_MAX_MEAN_PX_PER_MS, HM2_MAX_MS, HM2_MAX_SUBMOVES, HM2_MED_REF_MS, HM2_MIN_MS, HM2_PRIMARY_TIME_SHARE,
    HM2_CORRECTION_TOLERANCE_PX, HM2_FINAL_CORRECTIONS, HM2_MAX_CORRECTION_PX_PER_MS,
    HM2_RECURVE_PX, HM2_MAX_RECURVE, HM2_SCALE_COLLINEAR_MIN, HM2_MIN_PX_PER_SEGMENT, HM2_TREMOR_MAX_FRACTION, HM2_REPLAN_MIN_DRIFT_PX, HM2_SEGMENT_MS, HM2_SIGMA, HM2_TOLERANCE_PX, HM2_SLOPE_MS_PER_BIT, HM2_TEMPO_SIGMA, HM2_TEMPO_TAU_S, HM2_TREMOR_PX, HM2_UNDERSHOOT_MEAN,
    HM2_UNDERSHOOT_SIGMA,
)


# Session tempo: a slowly-drifting log-duration offset shared by every movement, because a
# human's pace is autocorrelated (fast spells, slow spells) and i.i.d. draws have neither --
# see HM2_TEMPO_SIGMA's comment for the five-recording measurement behind it. One
# Ornstein-Uhlenbeck process, advanced by wall-clock time whenever a duration is drawn.
_tempo_state = {"value": None, "at": None}


# Diagnostics for the 2026-08-28 flush/stall investigation. OFF unless HM2_DIAG=1 is set in the
# environment, and deliberately I/O-free inside the emission loop -- printing there would add
# latency at exactly the moment the firmware buffer is shallowest, i.e. it would manufacture the
# stall we are trying to measure. Counters accumulate; one summary line is emitted per flight.
DIAG = os.environ.get("HM2_DIAG") == "1"


def _diag(msg: str) -> None:
    if not DIAG:
        return
    try:
        import debug_log
        debug_log.log("hm2", msg)
    except Exception:
        print("[hm2] " + msg)


def _reset_tempo(value=None, now=None):
    """value=None re-arms the random session-start mood; a number pins the mood (tests)."""
    _tempo_state["value"] = value
    _tempo_state["at"] = now


def _tempo_now(rng: random.Random = random, now: float = None) -> float:
    if now is None:
        now = time.monotonic()
    st = _tempo_state
    if st["value"] is None:
        # A session starts in a random mood, not at the average pace.
        st["value"] = rng.gauss(0.0, HM2_TEMPO_SIGMA)
        st["at"] = now
        return st["value"]
    dt = max(0.0, now - (st["at"] if st["at"] is not None else now))
    st["at"] = now
    if dt > 0.0:
        alpha = math.exp(-dt / HM2_TEMPO_TAU_S)
        st["value"] = (alpha * st["value"]
                       + rng.gauss(0.0, HM2_TEMPO_SIGMA * math.sqrt(max(0.0, 1.0 - alpha * alpha))))
    return st["value"]


def duration_for(dist: float, rng: random.Random = random) -> float:
    """Movement duration in ms.

    Deliberately only weakly dependent on `dist`. The distance term is scaled so that its
    RELATIVE contribution matches the human's (0.344 per doubling) rather than its absolute
    ms/bit -- keeping the human's absolute 148.7 ms/bit while running at the bot's faster median
    would have made the distance signature stronger than a human's, not weaker.
    """
    d = max(1.0, float(dist))
    median = HM2_MED_REF_MS + HM2_SLOPE_MS_PER_BIT * math.log2(d / HM2_D_REF)
    median = max(HM2_MIN_MS, median)
    # The session's drifting mood rides on top of the per-movement draw -- see _tempo_now.
    ms = median * math.exp(rng.gauss(0.0, HM2_SIGMA) + _tempo_now(rng))
    ms = min(HM2_MAX_MS, max(HM2_MIN_MS, ms))
    # The superhuman-speed floor is applied AFTER the HM2_MAX_MS cap so the cap can never win
    # against it: beyond HM2_MAX_MS * HM2_MAX_MEAN_PX_PER_MS px (3500 at current settings -- a
    # 4K diagonal qualifies) the two constraints conflict, and running longer than the human's
    # longest observed reach is the safer side of that conflict than moving faster than the
    # human's top speed.
    return max(ms, d / HM2_MAX_MEAN_PX_PER_MS)


def plan_submovements(start, target, rng: random.Random = random, tolerance: float = None):
    """Endpoints of each submovement, from `start` toward `target`.

    A human reach is a fast primary that lands NEAR the target, then one or more corrections.
    The primary here covers HM2_UNDERSHOOT_MEAN of the gap with HM2_UNDERSHOOT_SIGMA spread, plus
    a lateral error; every correction repeats that on the remaining gap. The number of
    submovements is therefore emergent -- a bad primary needs more corrections -- which is what
    produces a distribution with a tail instead of a spike at 2.

    The final submovement always lands exactly on `target`, so v2 is never less accurate than v1.
    """
    if tolerance is None:
        tolerance = HM2_TOLERANCE_PX
    points = []
    cx, cy = float(start[0]), float(start[1])
    tx, ty = float(target[0]), float(target[1])

    for idx in range(HM2_MAX_SUBMOVES - 1):
        gap = math.hypot(tx - cx, ty - cy)
        if gap <= tolerance:
            break

        # Successive corrections are both more accurate and less off-axis: a hand closing on a
        # target tightens up rather than repeating the same scatter. Without this decay the two
        # tunables fight -- lateral error is what buys the human's path length (straightness
        # 0.787 vs the old engine's 0.963), but a constant lateral error also stops the gap
        # closing, which drove the submovement count to 95% at 4+ against a human's 42%.
        decay = HM2_LATERAL_DECAY ** idx
        mean = HM2_UNDERSHOOT_MEAN + (1.0 - HM2_UNDERSHOOT_MEAN) * (1.0 - decay)
        frac = rng.gauss(mean, HM2_UNDERSHOOT_SIGMA * decay)
        frac = min(1.12, max(0.35, frac))   # allow a genuine overshoot, but never a wild one

        nx = cx + (tx - cx) * frac
        ny = cy + (ty - cy) * frac

        # Lateral error: a human does not miss purely along the approach axis.
        ux, uy = (tx - cx) / gap, (ty - cy) / gap
        lateral = rng.gauss(0.0, HM2_LATERAL_ERROR * decay) * gap
        nx += -uy * lateral
        ny += ux * lateral

        points.append((nx, ny))
        cx, cy = nx, ny

        if math.hypot(tx - cx, ty - cy) <= tolerance:
            break

    points.append((tx, ty))
    return points


def _ease(t: float) -> float:
    """Same easing v1 uses. Time-to-peak velocity measured 0.269 for the bot against 0.261 for
    the human -- this is the one part of the old engine that was already right."""
    return t * t * (3.0 - 2.0 * t)


# A per-move override of HM2_CURVE_FRACTION, set by move(curve_fraction=...) for its own
# duration. A middle-mouse camera DRAG is the one move that must not curve like a reach:
# MEASURED 2026-09-10 from the human's recording, their 89-360px middle drags wander 2-28px
# vertically (under 8% of their length), while a reach's 35% control offset bulges ~100px on
# a 360px drag -- and every vertical pixel of a middle drag is camera PITCH, which then hits
# the pitch limit mid-drag and comes back 500+ units off. Single-threaded by design.
_CURVE_OVERRIDE = None


def _curve_offsets(seg_len: float, rng: random.Random):
    """Two OPPOSING perpendicular control offsets, giving an S-curve rather than a bow.

    A single-sided bow is what the v1 Bezier produces, and it reads as too clean (straightness
    0.963 against a human's 0.787): a bow of any amplitude is still one smooth arc. Opposing
    offsets make the path change direction mid-flight, which is both longer and less regular.
    """
    fraction = HM2_CURVE_FRACTION if _CURVE_OVERRIDE is None else _CURVE_OVERRIDE
    a = rng.gauss(0.0, fraction) * seg_len
    b = -rng.gauss(0.0, fraction) * seg_len
    return a, b


def _submove_path(p0, p1, rng: random.Random, steps: int):
    """Positions along one submovement, as a cubic with opposing control-point offsets."""
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    seg_len = math.hypot(dx, dy)
    if seg_len < 1e-6:
        return [(x1, y1)] * max(1, steps)

    ux, uy = dx / seg_len, dy / seg_len
    px, py = -uy, ux                      # unit perpendicular
    off_a, off_b = _curve_offsets(seg_len, rng)

    c1 = (x0 + dx / 3.0 + px * off_a, y0 + dy / 3.0 + py * off_a)
    c2 = (x0 + 2.0 * dx / 3.0 + px * off_b, y0 + 2.0 * dy / 3.0 + py * off_b)

    # Tremor scaled to the submovement, NOT absolute. A flat HM2_TREMOR_PX is invisible on the
    # primary reach but is 75% of a 2px final correction, so the cursor churned in place near the
    # target -- reported live as "hovers over it and jitters like crazy". A real hand is steadier
    # on small precise movements, not equally shaky.
    tremor = min(HM2_TREMOR_PX, seg_len * HM2_TREMOR_MAX_FRACTION)

    out = []
    for i in range(1, steps + 1):
        t = _ease(i / steps)
        mt = 1.0 - t
        bx = (mt ** 3) * x0 + 3 * (mt ** 2) * t * c1[0] + 3 * mt * (t ** 2) * c2[0] + (t ** 3) * x1
        by = (mt ** 3) * y0 + 3 * (mt ** 2) * t * c1[1] + 3 * mt * (t ** 2) * c2[1] + (t ** 3) * y1
        if i < steps:
            # Tremor everywhere except the final sample, so the submovement still lands exactly
            # where it was planned to.
            bx += rng.gauss(0.0, tremor)
            by += rng.gauss(0.0, tremor)
        out.append((bx, by))
    return out


def _plan_spans(points, start, duration_ms, primary_share=None):
    """Allocate each submovement's time: a fixed share to the primary, the rest split among the
    corrections BY DISTANCE.

    Returns (points, spans_ms) -- the points pass through untouched; the tuple shape is kept so
    a future filtering step has somewhere to live. Two things this is deliberately NOT:

    NOT an equal split among corrections, which is what it replaced. Their distances span four
    orders of magnitude, so equal time made correction 1 run 2.3-2.5x FASTER than the primary
    while corrections 2..n sat effectively stationary -- one 775px reach spent 406ms, 19% of the
    movement, not moving. MEASURED fastest/slowest submovement within one movement: p50 31.3x,
    p90 153x, worst 631x. USER-REPORTED as "sometimes it'd go super human speed other times very
    slow ... a human would be relatively consistent".

    NOT purely by distance either, which was the first attempt at the fix and overshot: that
    makes every submovement run at exactly the same speed, and a real reach does not. MEASURED
    from the recording, the corrective phase runs median 4.2x (p90 17.9x) slower per pixel than
    the ballistic throw -- the classic two-component reach. HM2_PRIMARY_TIME_SHARE is what
    reproduces that asymmetry; see its comment for the arithmetic tying 0.62 to the measured
    4.2x.

    It also deliberately does NOT drop tiny trailing submovements. Merging anything under ~1.5px
    into its predecessor was tried and reverted the same day: it tripped
    test_v2_settles_while_tracking_a_moving_target from 5.5px of settle churn to 13.1px against a
    12.0px budget (the old engine scores 3.5px), because while chasing a moving target those
    small final approaches are exactly what does the settling. It is also the wrong trade on its
    own terms -- those slow trailing corrections are one of the few places this engine reproduces
    a human's homing phase at all.

    `primary_share` is overridable for the mid-flight replan path. A replanned suffix is the
    HOMING phase of a reach already in progress, not a fresh throw, so handing its first
    submovement a full ballistic share is wrong twice over -- it inflates that leg and starves
    the settle that follows. CONFIRMED by test_v2_settles_while_tracking_a_moving_target, which
    went to 13.1px of settle churn against a 12.0px budget when the replan used the default.
    """
    cur = (float(start[0]), float(start[1]))
    merged, dists = [], []
    for p in points:
        merged.append(p)
        dists.append(math.hypot(p[0] - cur[0], p[1] - cur[1]))
        cur = p

    if len(merged) <= 1:
        return merged, [duration_ms]

    share = HM2_PRIMARY_TIME_SHARE if primary_share is None else primary_share
    if share <= 0.0:
        total = sum(dists)
        if total <= 1e-9:
            return merged, [max(HM2_SEGMENT_MS, duration_ms / len(merged))] * len(merged)
        return merged, [max(HM2_SEGMENT_MS, duration_ms * d / total) for d in dists]

    primary_ms = duration_ms * share
    rest = max(0.0, duration_ms - primary_ms)
    tail = sum(dists[1:])
    if tail <= 1e-9:
        return merged, [duration_ms] + [HM2_SEGMENT_MS] * (len(merged) - 1)
    spans = [primary_ms] + [max(HM2_SEGMENT_MS, rest * d / tail) for d in dists[1:]]
    return merged, spans


def _step_plan(span_ms: float, gap_px: float):
    """(steps, step_ms) for one submovement: segment count bounded by TIME and by DISTANCE.

    A 2px correction spread over five 18ms segments is five chances to wobble for two pixels of
    travel; one segment does the same job and looks like a settle rather than a twitch. Shared by
    build_segments, move()'s main loop, and the residual-correction loop so the bounding rule
    cannot drift between them.
    """
    steps = max(1, int(round(span_ms / HM2_SEGMENT_MS)))
    steps = max(1, min(steps, int(math.ceil(gap_px / HM2_MIN_PX_PER_SEGMENT))))
    return steps, span_ms / steps


def build_segments(start, target, rng: random.Random = random, duration_ms: float = None):
    """Full movement as a list of (dx_hundredths, dy_hundredths, dur_ms) firmware segments.

    Pure: no serial, no cursor reads, no sleeping -- so the statistics this module exists to fix
    can be tested without any hardware. Sub-pixel remainder is carried between segments here as
    well as in the firmware, so rounding never accumulates into a positional error.
    """
    start = (float(start[0]), float(start[1]))
    target = (float(target[0]), float(target[1]))
    total_dist = math.hypot(target[0] - start[0], target[1] - start[1])
    if duration_ms is None:
        duration_ms = duration_for(total_dist, rng)

    points = plan_submovements(start, target, rng)

    points, spans_ms = _plan_spans(points, start, duration_ms)

    segments = []
    cur = start
    carry_x = carry_y = 0.0

    for idx, p in enumerate(points):
        span_ms = max(HM2_SEGMENT_MS, spans_ms[idx])
        gap_px = math.hypot(p[0] - cur[0], p[1] - cur[1])
        steps, step_ms = _step_plan(span_ms, gap_px)

        for pos in _submove_path(cur, p, rng, steps):
            dx = pos[0] - cur[0] + carry_x
            dy = pos[1] - cur[1] + carry_y
            qx = int(round(dx * 100.0))
            qy = int(round(dy * 100.0))
            carry_x = dx - qx / 100.0
            carry_y = dy - qy / 100.0
            if qx or qy:
                segments.append((qx, qy, max(1, int(round(step_ms)))))
            cur = pos
        cur = p

    return segments


# Screen pixels produced per emitted mouse unit. NOT a constant: it is the user's Windows
# pointer-speed setting (this machine measured 0.74 at 5/11; the neutral 6/11 gives 1.0), so it
# is learned at runtime rather than assumed. Starts at 1.0 and converges within a movement or
# two; the final-correction loop covers whatever it has not learned yet.
_px_per_unit = 1.0
_SCALE_ALPHA = 0.25          # EMA weight -- slow enough that one odd movement cannot yank it
_SCALE_MIN, _SCALE_MAX = 0.2, 3.0
_scale_samples = 0       # movements measured so far; see _learn_scale's adaptive weight

# Calibration persistence (2026-08-27). The pointer scale is a property of the MACHINE, not of
# a session, but it used to reset to 1.0 on every process start -- so the first flights after
# each restart under-traveled (~26% on this machine) and the residual-correction loop, the
# least human-looking motion in the engine, cleaned up the difference. Bites hardest in
# routines that fly the mouse rarely (plank_make: ~4 moves/minute). Every I/O path here
# swallows failures -- a state file must never be able to break movement.
_CALIBRATION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".hm2_calibration.json")


def _save_calibration() -> None:
    try:
        tmp = _CALIBRATION_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"px_per_unit": _px_per_unit, "samples": _scale_samples}, f)
        os.replace(tmp, _CALIBRATION_PATH)   # atomic-ish: a crash mid-write can't tear the file
    except Exception:
        pass


def _load_calibration() -> None:
    global _px_per_unit, _scale_samples
    try:
        with open(_CALIBRATION_PATH) as f:
            data = json.load(f)
        px = float(data["px_per_unit"])
        samples = int(data.get("samples", 0))
    except Exception:
        return
    if not (_SCALE_MIN <= px <= _SCALE_MAX) or samples < 1:
        return
    _px_per_unit = px
    # Floor the imported confidence at the point where _learn_scale's adaptive weight has
    # already settled to the flat EMA (1/alpha samples), so a fresh session's first sample is
    # damped rather than treated as the first measurement ever; cap it so an ancient count
    # can't make the estimate feel more settled than the EMA can express.
    _scale_samples = max(int(round(1.0 / _SCALE_ALPHA)), min(samples, 20))


def _learn_scale(units: float, actual_px: float) -> None:
    """Update the px-per-unit estimate from one completed movement."""
    global _px_per_unit, _scale_samples
    if units < 20.0 or actual_px <= 0.0:
        return                # too short to measure reliably
    ratio = actual_px / units
    if not (_SCALE_MIN <= ratio <= _SCALE_MAX):
        return                # nonsense (cursor hit a screen edge, or the move was interrupted)
    # The weight is raised for the first few samples (1/n, floored at _SCALE_ALPHA) so the
    # estimate converges in ONE movement rather than the eight-to-ten a flat 0.25 needs from the
    # 1.0 starting guess. _px_per_unit resets on every process start, and until it converges the
    # planned path under-travels -- on this machine by ~26% -- which is what makes the residual
    # correction above large in the first place. It bites hardest in routines that fly the mouse
    # rarely: routines/plank_make.py moves it about four times a minute.
    alpha = max(_SCALE_ALPHA, 1.0 / (_scale_samples + 1))
    _px_per_unit = (1.0 - alpha) * _px_per_unit + alpha * ratio
    _scale_samples += 1
    _save_calibration()


_load_calibration()   # restore the machine's learned scale from the previous run, if any


def _drain(t0: float, queued_ms: float) -> None:
    """Block until the firmware has actually played everything queued so far, so the cursor can
    be read back as ground truth. Segments are queued AHEAD of real time by design, so reading
    the cursor without this returns a position from the middle of the movement."""
    remaining = queued_ms - (time.perf_counter() - t0) * 1000.0
    if remaining > 0:
        time.sleep(remaining / 1000.0)


def move(ser, x: int, y: int, *, get_cursor_pos, send_line, abort=None,
         duration_scale: float = 1.0, refresh_target=None, flush_queue=None,
         rng: random.Random = random, _recurve_depth: int = 0,
         curve_fraction: float = None) -> None:
    """Drive the cursor to (x, y).

    curve_fraction, if given, replaces HM2_CURVE_FRACTION for this move (and its own
    recurves) -- see _CURVE_OVERRIDE for the one caller that needs it, the camera drag.

    Dependencies are injected rather than imported from controller, so this module can be unit
    tested with no serial port, no Windows cursor API, and no import cycle.

    refresh_target, if given, is re-resolved BETWEEN submovements. Those boundaries are already
    the natural correction points of a human reach, so tracking a moving target costs nothing
    extra here -- unlike v1, where re-aiming forced an artificial leg boundary and each leg paid
    a full duration floor.

    flush_queue, if given, is a zero-arg callable that retracts every QUEUED (unplayed) firmware
    segment -- the active one finishes (see firmware/Firmware.ino's X command). It is invoked
    only when a re-aim replans the flight, and only when enough motion is queued to be worth
    retracting; the emission log below is what lets the bookkeeping rewind to the last segment
    that actually plays. Callers MUST pass None unless the firmware probe confirmed protocol
    v3: against older firmware X is silently ignored, the queued motion plays anyway, and the
    rewound bookkeeping would be wrong. A flushed flight never feeds _learn_scale -- its
    emitted units include motion that never happened.
    """
    global _CURVE_OVERRIDE
    if curve_fraction is not None and _recurve_depth == 0:
        saved = _CURVE_OVERRIDE
        _CURVE_OVERRIDE = curve_fraction
        try:
            return move(ser, x, y, get_cursor_pos=get_cursor_pos, send_line=send_line,
                        abort=abort, duration_scale=duration_scale,
                        refresh_target=refresh_target, flush_queue=flush_queue, rng=rng,
                        _recurve_depth=_recurve_depth)
        finally:
            _CURVE_OVERRIDE = saved
    start = get_cursor_pos()
    target = (float(x), float(y))
    total_dist = math.hypot(target[0] - start[0], target[1] - start[1])
    if total_dist < 2.0:
        return

    duration_ms = duration_for(total_dist, rng) * max(0.05, duration_scale)
    # duration_for enforces the superhuman-speed floor on ITS output, but a duration_scale
    # below 1.0 shrinks the budget after the fact -- re-apply the floor so no caller can plan
    # a mean speed past the human-measured cap.
    duration_ms = max(duration_ms, total_dist / HM2_MAX_MEAN_PX_PER_MS)
    points = plan_submovements(start, target, rng)
    points, spans_ms = _plan_spans(points, start, duration_ms)

    cur = (float(start[0]), float(start[1]))
    carry_x = carry_y = 0.0
    units_x = units_y = 0.0      # emitted mouse units, for the scale calibration below
    queued_ms = 0.0
    t0 = time.perf_counter()
    target_lost = False
    emit_log = []      # (cumulative queued ms after this emission, planned x, planned y)
    # A flush on replan rewinds cur/queued_ms below but deliberately does NOT rewind units_x/
    # units_y or carry_x/carry_y. The units are only consumed by _learn_scale, which `flushed`
    # skips entirely below, so a stale total there is harmless. The sub-pixel carries already
    # diverge from the firmware's own carry by under a pixel on every flight, flushed or not --
    # that's what the residual corrections at the end of move() exist to absorb.
    flushed = False
    diag_starved = 0            # emissions issued after the firmware queue had already run dry
    diag_min_ahead = 1e9        # shallowest the queue ever got, ms
    # TRUE stall measure. `starved`/`min_ahead` above read `ahead = queued_ms - elapsed`, which
    # stopped being able to go negative once queued_ms became idle-aware -- so they now under-report
    # by construction. This measures the thing itself: how long the firmware sat with an EMPTY
    # queue waiting for us, which is exactly the visible cursor stutter.
    diag_idle_ms = 0.0
    diag_idle_events = 0
    diag_max_idle = 0.0
    diag_emissions = 0
    diag_flushes = 0
    diag_rewind_px = 0.0
    diag_flush_log = []
    diag_corrections = 0

    # A while loop, not `for idx, p in enumerate(points)`: re-aiming REPLANS the remaining
    # submovements, and enumerate() would keep walking the original list.
    # Hard iteration cap. Replanning below can APPEND submovements, so a target that moves on
    # every refresh would otherwise grow `points` faster than the loop consumes it and never
    # terminate -- which is exactly what happened the first time this was written.
    max_iters = HM2_MAX_SUBMOVES + 2
    idx = 0
    while idx < len(points) and idx < max_iters:
        if abort is not None and abort.is_set():
            return

        # Re-aim at a submovement boundary.
        #
        # FIXED 2026-08-24 (user report: "still a lot of misses"). This previously assigned the
        # refreshed position to the LOCAL `p`, and only on the final submovement -- so every other
        # refresh paid an HTTP round trip and threw the answer away, and `target` (which the
        # final-correction loop below closes on) was never updated at all. The corrections then
        # actively dragged the cursor BACK to the stale coordinate, so a target that moved during
        # the flight was missed by construction. That matters because slayer.py picks its target
        # from the TickCache-gated /npcs and relies entirely on refresh_target to compensate.
        if idx > 0 and refresh_target is not None and not target_lost:
            updated = refresh_target()
            if updated is None:
                target_lost = True          # died/despawned: finish on the last known good point
            else:
                moved = (float(updated[0]), float(updated[1]))
                if math.hypot(moved[0] - target[0], moved[1] - target[1]) > HM2_REPLAN_MIN_DRIFT_PX:
                    target = moved
                    # Retract what hasn't played. Everything queued past elapsed + the commit
                    # margin (serial latency + the active segment, which always finishes) is
                    # motion toward the OLD target that the firmware will now never perform, so
                    # rewind cur/queued_ms to the last emission that does play and let the
                    # replan start from there. Skipped when there's less than a segment's worth
                    # to reclaim -- a flush that saves nothing is just serial noise.
                    if flush_queue is not None:
                        elapsed_ms = (time.perf_counter() - t0) * 1000.0
                        commit_ms = elapsed_ms + HM2_FLUSH_COMMIT_MS
                        if queued_ms - commit_ms > HM2_SEGMENT_MS:
                            flush_queue()
                            flushed = True
                            diag_flushes += 1
                            _pre_cur, _pre_q = cur, queued_ms
                            for i, (t_ms, ex, ey) in enumerate(emit_log):
                                if t_ms >= commit_ms:
                                    queued_ms = t_ms
                                    cur = (ex, ey)
                                    del emit_log[i + 1:]
                                    break
                            if DIAG:
                                _back = math.hypot(cur[0] - _pre_cur[0], cur[1] - _pre_cur[1])
                                diag_rewind_px += _back
                                diag_flush_log.append(
                                    "flush#%d at idx%d: elapsed=%.0f commit=%.0f queued %.0f->%.0f "
                                    "(-%.0fms) cur rewound %.0fpx"
                                    % (diag_flushes, idx, elapsed_ms, commit_ms, _pre_q,
                                       queued_ms, _pre_q - queued_ms, _back))
                    # Replan the approach from where the cursor actually is toward where the
                    # target actually is. Keeps the submovement structure rather than teleporting
                    # the aim: the remaining corrections just close on the new position.
                    points = points[:idx] + plan_submovements(cur, target, rng)
                    # Re-allocate the REMAINING time across the replanned suffix, by distance.
                    remaining = max(HM2_SEGMENT_MS, duration_ms - queued_ms)
                    tail, suffix = _plan_spans(points[idx:], cur, remaining, primary_share=0.0)
                    points = points[:idx] + tail
                    spans_ms = spans_ms[:idx] + suffix

        p = points[idx]
        span_ms = max(HM2_SEGMENT_MS,
                      spans_ms[idx] if idx < len(spans_ms) else HM2_SEGMENT_MS)
        gap_px = math.hypot(p[0] - cur[0], p[1] - cur[1])
        steps, step_ms = _step_plan(span_ms, gap_px)

        for pos in _submove_path(cur, p, rng, steps):
            if abort is not None and abort.is_set():
                return

            # Convert screen pixels to emitted MOUSE UNITS. One unit is not one pixel -- this
            # machine measured 0.74 px/unit (Windows pointer speed 5/11) -- and the ratio is a
            # property of the user's OS settings, so it is learned (see _px_per_unit) rather than
            # assumed or hardcoded.
            dx = (pos[0] - cur[0]) / _px_per_unit + carry_x
            dy = (pos[1] - cur[1]) / _px_per_unit + carry_y
            qx = int(round(dx * 100.0))
            qy = int(round(dy * 100.0))
            carry_x = dx - qx / 100.0
            carry_y = dy - qy / 100.0
            cur = pos

            if not (qx or qy):
                continue

            units_x += qx / 100.0
            units_y += qy / 100.0
            dur = max(1, int(round(step_ms)))
            send_line(ser, f"S {qx} {qy} {dur}")
            # queued_ms is the firmware's PLAYBACK timeline, not the sum of our durations. If we
            # were late feeding it (a slow refresh_target, a GC pause, an OS stall), the firmware
            # ran dry and idled, so playback resumes NOW rather than back-to-back with the last
            # segment. Summing durations alone silently under-states the finish time for the rest
            # of the flight: _drain then stops waiting, every residual correction reads a cursor
            # that has not moved yet, re-issues the same correction, and the stack plays out after
            # move() returns. MEASURED live 2026-08-28 over 181 flights: the 37 starved ones had a
            # median final error of 7.1px (max 87.1) and 28 of them exhausted all 6 corrections,
            # against 1.0px median and never more than 2 corrections on the 144 that never starved.
            # Identical to the old arithmetic whenever the firmware never idles.
            if DIAG:
                _now_ms = (time.perf_counter() - t0) * 1000.0
                if diag_emissions and _now_ms - queued_ms > 1.0:
                    _gap = _now_ms - queued_ms       # firmware idled this long: a real stall
                    diag_idle_ms += _gap
                    diag_idle_events += 1
                    diag_max_idle = max(diag_max_idle, _gap)
                diag_emissions += 1
            queued_ms = max(queued_ms, (time.perf_counter() - t0) * 1000.0) + dur
            emit_log.append((queued_ms, cur[0], cur[1]))

            # Stay a few segments ahead of the firmware, never more. The queue holds 16 and
            # applies backpressure, but blocking on a full queue would make our own timing
            # depend on serial buffering rather than on the plan.
            ahead = queued_ms - (time.perf_counter() - t0) * 1000.0
            if ahead < diag_min_ahead:
                diag_min_ahead = ahead
            if ahead <= 0.0:
                diag_starved += 1
            slack = ahead - HM2_LOOKAHEAD_SEGMENTS * HM2_SEGMENT_MS
            if slack > 0:
                time.sleep(slack / 1000.0)

        cur = p
        idx += 1

    # Drain ONCE, after the whole planned path -- not at every submovement boundary.
    #
    # Draining per submovement (the first version of this fix) did make v2 land accurately, but
    # MEASURED live 2026-08-24 it wrecked the velocity profile: each drain inserts a stall
    # mid-reach, which pushed time-to-peak from 0.269 to 0.586 against a human's 0.261, and
    # inflated duration by ~44% over the model's own budget. Time-to-peak was the one metric the
    # old engine already had right, so breaking it was strictly a regression.
    #
    # Staying open-loop through the path is only safe because _px_per_unit below removes the
    # systematic part of the error first; what is left is small enough for the corrections to
    # settle without a visible jump.
    _drain(t0, queued_ms)
    cur = tuple(float(v) for v in get_cursor_pos())

    # Learn the pointer scale from what actually happened. Emitting `units` moved the cursor
    # `actual` pixels, so px-per-unit is their ratio; an EMA keeps one noisy movement from
    # yanking the calibration. This is what lets the path above be planned open-loop at all, and
    # it self-tunes on any machine instead of hardcoding this one's 0.74.
    diag_open_err = math.hypot(target[0] - cur[0], target[1] - cur[1])

    # Only a flight whose observed displacement is COLLINEAR with what was commanded may teach
    # the estimator. px-per-unit is one isotropic scalar: it scales motion along the commanded
    # direction and cannot rotate it, so a net displacement pointing elsewhere contains motion we
    # did not cause. MEASURED 2026-08-28 over 226 consecutive live flights: the samples that
    # shifted the estimate by >0.05 had a median open-loop error of 54.1px against 2.2px for
    # every other sample, and single flights moved it by as much as 0.80 -- the persisted value
    # wandered 0.60-1.77 across runs against this machine's true ~0.74, and a wrong persisted
    # scale is what supplies the residual that everything else here has to clean up.
    # Deliberately NOT a test on the SIZE of the error: a genuinely stale scale produces large
    # errors that are perfectly collinear (measured cos 1.00000 at 26% and 40% stale), and those
    # are the samples that correct it fastest. See HM2_SCALE_COLLINEAR_MIN.
    moved = (cur[0] - start[0], cur[1] - start[1])
    moved_px = math.hypot(*moved)
    units_mag = math.hypot(units_x, units_y)
    collinear = True
    if units_mag > 0.0 and moved_px > 0.0:
        cos = (units_x * moved[0] + units_y * moved[1]) / (units_mag * moved_px)
        collinear = cos >= HM2_SCALE_COLLINEAR_MIN
    if not flushed and collinear:
        _learn_scale(units_mag, moved_px)

    # Residual correction. Small once the scale is learned, and genuinely human -- a settling
    # touch at the end of a reach is exactly what the submovement model predicts anyway.
    for _ in range(HM2_FINAL_CORRECTIONS):
        if abort is not None and abort.is_set():
            return
        dx, dy = target[0] - cur[0], target[1] - cur[1]
        if math.hypot(dx, dy) <= HM2_CORRECTION_TOLERANCE_PX:
            break
        diag_corrections += 1
        # Duration SCALES WITH THE GAP, capped at a human-measured top speed. This used to be a
        # flat HM2_SEGMENT_MS however far the correction had to travel, so the cursor snapped
        # faster the further the planned path had fallen short -- and the residual is largest
        # exactly while _px_per_unit is still converging. MEASURED: a 527px reach hit 7.6 px/ms
        # and a 900px one ~13 px/ms, against a human maximum of 5.37 (n=2097 windows,
        # recordings/2026-08-26_225917_mousemove). See HM2_MAX_CORRECTION_PX_PER_MS.
        gap = math.hypot(dx, dy)
        # A gap this size is not a settle: the REAL cursor has diverged from the open-loop plan
        # (a hand on the mouse, or a target that jumped with the camera). Flying it with the
        # straight-line walk below would produce a long constant-velocity slide with no curve,
        # tremor or easing -- the exact signature this engine exists to remove. Fly it as a
        # fresh human reach instead, and let the settle handle whatever that leaves.
        if gap > HM2_RECURVE_PX and _recurve_depth < HM2_MAX_RECURVE:
            move(ser, int(round(target[0])), int(round(target[1])),
                 get_cursor_pos=get_cursor_pos, send_line=send_line, abort=abort,
                 duration_scale=duration_scale, rng=rng,
                 _recurve_depth=_recurve_depth + 1)
            if abort is not None and abort.is_set():
                return
            cur = tuple(float(v) for v in get_cursor_pos())
            continue
        span_ms = max(HM2_SEGMENT_MS, gap / HM2_MAX_CORRECTION_PX_PER_MS)
        steps, step_ms = _step_plan(span_ms, gap)
        dur = max(1, int(round(step_ms)))
        # Straight-line walk to the target -- a settle, not another curved reach.
        prev = cur
        for k in range(1, steps + 1):
            if abort is not None and abort.is_set():
                return
            want = (cur[0] + dx * k / steps, cur[1] + dy * k / steps)
            sx = int(round((want[0] - prev[0]) / _px_per_unit * 100))
            sy = int(round((want[1] - prev[1]) / _px_per_unit * 100))
            prev = want
            if sx or sy:
                send_line(ser, f"S {sx} {sy} {dur}")
                # Same idle-aware timeline as the main loop above -- without it a correction
                # issued after a starved flight is never waited for at all.
                queued_ms = max(queued_ms, (time.perf_counter() - t0) * 1000.0) + dur
        _drain(t0, queued_ms)
        cur = tuple(float(v) for v in get_cursor_pos())

    if DIAG:
        _diag("flight dist=%.0fpx dur=%.0fms open_err=%.1fpx corr=%d final_err=%.1fpx "
              "flushes=%d rewind=%.0fpx starved=%d min_ahead=%.0fms scale=%.3f "
              "idle=%.0fms/%d max_idle=%.0fms"
              % (total_dist, duration_ms, diag_open_err, diag_corrections,
                 math.hypot(target[0] - cur[0], target[1] - cur[1]),
                 diag_flushes, diag_rewind_px, diag_starved, diag_min_ahead, _px_per_unit,
                 diag_idle_ms, diag_idle_events, diag_max_idle))
        for _rec in diag_flush_log:
            _diag("  " + _rec)
