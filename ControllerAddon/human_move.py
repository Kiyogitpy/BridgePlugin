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

import math
import random
import time

from settings import (
    HM2_CURVE_FRACTION, HM2_D_REF, HM2_LATERAL_ERROR, HM2_LOOKAHEAD_SEGMENTS,
    HM2_LATERAL_DECAY, HM2_MAX_MS, HM2_MAX_SUBMOVES, HM2_MED_REF_MS, HM2_MIN_MS, HM2_PRIMARY_TIME_SHARE,
    HM2_CORRECTION_TOLERANCE_PX, HM2_FINAL_CORRECTIONS, HM2_MIN_PX_PER_SEGMENT, HM2_TREMOR_MAX_FRACTION, HM2_REPLAN_MIN_DRIFT_PX, HM2_SEGMENT_MS, HM2_SIGMA, HM2_TOLERANCE_PX, HM2_SLOPE_MS_PER_BIT, HM2_TREMOR_PX, HM2_UNDERSHOOT_MEAN,
    HM2_UNDERSHOOT_SIGMA,
)


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
    ms = median * math.exp(rng.gauss(0.0, HM2_SIGMA))
    return min(HM2_MAX_MS, max(HM2_MIN_MS, ms))


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


def _curve_offsets(seg_len: float, rng: random.Random):
    """Two OPPOSING perpendicular control offsets, giving an S-curve rather than a bow.

    A single-sided bow is what the v1 Bezier produces, and it reads as too clean (straightness
    0.963 against a human's 0.787): a bow of any amplitude is still one smooth arc. Opposing
    offsets make the path change direction mid-flight, which is both longer and less regular.
    """
    a = rng.gauss(0.0, HM2_CURVE_FRACTION) * seg_len
    b = -rng.gauss(0.0, HM2_CURVE_FRACTION) * seg_len
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
    n_corrections = max(0, len(points) - 1)

    # The primary gets the lion's share of the time; corrections split the rest and are quicker,
    # matching the shape of a real reach (one fast throw, then small fast fixes).
    primary_ms = duration_ms * HM2_PRIMARY_TIME_SHARE if n_corrections else duration_ms
    correction_ms = (duration_ms - primary_ms) / n_corrections if n_corrections else 0.0

    segments = []
    cur = start
    carry_x = carry_y = 0.0

    for idx, p in enumerate(points):
        span_ms = primary_ms if idx == 0 else correction_ms
        span_ms = max(HM2_SEGMENT_MS, span_ms)
        # Bound segment count by DISTANCE as well as time. A 2px correction spread over five
        # 18ms segments is five chances to wobble for two pixels of travel; one segment does the
        # same job and looks like a settle rather than a twitch.
        gap_px = math.hypot(p[0] - cur[0], p[1] - cur[1])
        steps = max(1, int(round(span_ms / HM2_SEGMENT_MS)))
        steps = max(1, min(steps, int(math.ceil(gap_px / HM2_MIN_PX_PER_SEGMENT))))
        step_ms = span_ms / steps

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


def _learn_scale(units: float, actual_px: float) -> None:
    """Update the px-per-unit estimate from one completed movement."""
    global _px_per_unit
    if units < 20.0 or actual_px <= 0.0:
        return                # too short to measure reliably
    ratio = actual_px / units
    if not (_SCALE_MIN <= ratio <= _SCALE_MAX):
        return                # nonsense (cursor hit a screen edge, or the move was interrupted)
    _px_per_unit = (1.0 - _SCALE_ALPHA) * _px_per_unit + _SCALE_ALPHA * ratio


def _drain(t0: float, queued_ms: float) -> None:
    """Block until the firmware has actually played everything queued so far, so the cursor can
    be read back as ground truth. Segments are queued AHEAD of real time by design, so reading
    the cursor without this returns a position from the middle of the movement."""
    remaining = queued_ms - (time.perf_counter() - t0) * 1000.0
    if remaining > 0:
        time.sleep(remaining / 1000.0)


def move(ser, x: int, y: int, *, get_cursor_pos, send_line, abort=None,
         duration_scale: float = 1.0, refresh_target=None, rng: random.Random = random) -> None:
    """Drive the cursor to (x, y).

    Dependencies are injected rather than imported from controller, so this module can be unit
    tested with no serial port, no Windows cursor API, and no import cycle.

    refresh_target, if given, is re-resolved BETWEEN submovements. Those boundaries are already
    the natural correction points of a human reach, so tracking a moving target costs nothing
    extra here -- unlike v1, where re-aiming forced an artificial leg boundary and each leg paid
    a full duration floor.
    """
    start = get_cursor_pos()
    target = (float(x), float(y))
    total_dist = math.hypot(target[0] - start[0], target[1] - start[1])
    if total_dist < 2.0:
        return

    duration_ms = duration_for(total_dist, rng) * max(0.05, duration_scale)
    points = plan_submovements(start, target, rng)
    n_corrections = max(0, len(points) - 1)
    primary_ms = duration_ms * HM2_PRIMARY_TIME_SHARE if n_corrections else duration_ms
    correction_ms = (duration_ms - primary_ms) / n_corrections if n_corrections else 0.0

    cur = (float(start[0]), float(start[1]))
    carry_x = carry_y = 0.0
    units_x = units_y = 0.0      # emitted mouse units, for the scale calibration below
    queued_ms = 0.0
    t0 = time.perf_counter()
    target_lost = False

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
                    # Replan the approach from where the cursor actually is toward where the
                    # target actually is. Keeps the submovement structure rather than teleporting
                    # the aim: the remaining corrections just close on the new position.
                    points = points[:idx] + plan_submovements(cur, target, rng)
                    n_corrections = max(0, len(points) - idx - 1)
                    correction_ms = ((duration_ms - primary_ms) / n_corrections
                                     if n_corrections else correction_ms)

        p = points[idx]
        span_ms = primary_ms if idx == 0 else correction_ms
        span_ms = max(HM2_SEGMENT_MS, span_ms)
        # Bound segment count by DISTANCE as well as time. A 2px correction spread over five
        # 18ms segments is five chances to wobble for two pixels of travel; one segment does the
        # same job and looks like a settle rather than a twitch.
        gap_px = math.hypot(p[0] - cur[0], p[1] - cur[1])
        steps = max(1, int(round(span_ms / HM2_SEGMENT_MS)))
        steps = max(1, min(steps, int(math.ceil(gap_px / HM2_MIN_PX_PER_SEGMENT))))
        step_ms = span_ms / steps

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
            queued_ms += dur

            # Stay a few segments ahead of the firmware, never more. The queue holds 16 and
            # applies backpressure, but blocking on a full queue would make our own timing
            # depend on serial buffering rather than on the plan.
            ahead = queued_ms - (time.perf_counter() - t0) * 1000.0
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
    _learn_scale(math.hypot(units_x, units_y),
                 math.hypot(cur[0] - start[0], cur[1] - start[1]))

    # Residual correction. Small once the scale is learned, and genuinely human -- a settling
    # touch at the end of a reach is exactly what the submovement model predicts anyway.
    for _ in range(HM2_FINAL_CORRECTIONS):
        if abort is not None and abort.is_set():
            return
        dx, dy = target[0] - cur[0], target[1] - cur[1]
        if math.hypot(dx, dy) <= HM2_CORRECTION_TOLERANCE_PX:
            break
        dur = max(1, int(round(HM2_SEGMENT_MS)))
        send_line(ser, f"S {int(round(dx / _px_per_unit * 100))} "
                       f"{int(round(dy / _px_per_unit * 100))} {dur}")
        queued_ms += dur
        _drain(t0, queued_ms)
        cur = tuple(float(v) for v in get_cursor_pos())
