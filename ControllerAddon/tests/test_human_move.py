"""Tests for the v2 movement engine.

The interesting properties here are STATISTICAL -- the whole point of v2 is the distribution of
durations, path lengths and submovement counts, not any single movement. build_segments and
plan_submovements are deliberately pure (no serial, no cursor, no sleeping) so those
distributions can be measured directly, which is also how the design's targets were set.

Targets come from recorder.py captures of a human versus the routine; see
docs/superpowers/specs/2026-08-24-human-mouse-movement-v2-design.md.
"""

import math
import os
import random
import statistics
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import human_move
import settings


@pytest.fixture(autouse=True)
def _isolate_calibration(tmp_path, monkeypatch):
    """Every test writes its pointer-scale calibration to a throwaway file. Without this,
    any test that exercises _learn_scale (directly or through move()) would overwrite the
    REAL machine's persisted calibration with fabricated test values."""
    monkeypatch.setattr(human_move, "_CALIBRATION_PATH", str(tmp_path / "cal.json"))


@pytest.fixture(autouse=True)
def _pin_tempo():
    """Pin the session-tempo drift to exactly 0 so every distribution test measures the fast
    i.i.d. component alone, deterministically -- the tempo tests below drive the drift
    explicitly with injected clocks instead."""
    import time as _time
    human_move._reset_tempo(0.0, now=_time.monotonic())
    yield
    human_move._reset_tempo(None)


# ---- session tempo ----

def test_tempo_is_a_slow_drift_not_iid():
    """MEASURED 2026-08-27 over five human recordings (n=786 reaches): the residual around the
    distance model has lag-1 autocorrelation ~0.15, and the 13-minute session's 2-minute block
    means wander with sigma 0.120 against 0.050 expected under independence. A human has fast
    spells and slow spells; i.i.d. draws have neither."""
    rng = random.Random(11)
    human_move._reset_tempo(None)
    # Steps much shorter than the time constant: consecutive values barely move.
    now = 0.0
    prev = human_move._tempo_now(rng, now=now)
    max_step = 0.0
    for _ in range(50):
        now += 2.0
        cur = human_move._tempo_now(rng, now=now)
        max_step = max(max_step, abs(cur - prev))
        prev = cur
    assert max_step < settings.HM2_TEMPO_SIGMA * 0.5

    # Steps much longer than the time constant: values decorrelate and the stationary spread
    # is the configured sigma.
    vals = []
    for _ in range(3000):
        now += settings.HM2_TEMPO_TAU_S * 10
        vals.append(human_move._tempo_now(rng, now=now))
    sigma = statistics.pstdev(vals)
    assert 0.8 * settings.HM2_TEMPO_SIGMA <= sigma <= 1.2 * settings.HM2_TEMPO_SIGMA
    assert abs(statistics.mean(vals)) < settings.HM2_TEMPO_SIGMA * 0.2


def test_tempo_shifts_every_duration_in_the_session_together():
    """The mood multiplies the whole session's clock: the same seed at tempo +0.2 must produce
    durations ~exp(0.2) longer than at tempo 0 (until the clamps bite)."""
    import time as _time
    rng = random.Random(42)
    human_move._reset_tempo(0.0, now=_time.monotonic())
    base = [human_move.duration_for(300.0, random.Random(i)) for i in range(200)]
    human_move._reset_tempo(0.2, now=_time.monotonic())
    shifted = [human_move.duration_for(300.0, random.Random(i)) for i in range(200)]
    ratio = statistics.median(shifted) / statistics.median(base)
    assert 1.15 <= ratio <= 1.30      # exp(0.2) = 1.221


def test_session_starts_in_a_random_mood():
    """A fresh process should not always begin at the average pace -- the first call draws the
    mood from the stationary distribution."""
    firsts = []
    for i in range(400):
        human_move._reset_tempo(None)
        firsts.append(human_move._tempo_now(random.Random(i), now=0.0))
    sigma = statistics.pstdev(firsts)
    assert 0.8 * settings.HM2_TEMPO_SIGMA <= sigma <= 1.2 * settings.HM2_TEMPO_SIGMA

# MEASURED, recordings/2026-08-26_225917_mousemove: fastest ~10ms window in the whole session.
HUMAN_MAX_INSTANT_PX_PER_MS = 5.37


def _fit_r2(dists, durs):
    xs = [math.log2(d) for d in dists]
    n = len(xs)
    mx, my = sum(xs) / n, sum(durs) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, durs))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in durs)
    if not sxx or not syy:
        return 0.0
    return (sxy / math.sqrt(sxx * syy)) ** 2


def _sample_distances(rng, n):
    """Reach distances drawn from the MEASURED human distribution.

    RE-DERIVED 2026-08-27 from recordings/2026-08-27_001343_human_dataset (13m11s of mixed
    banking/slayer/travel, 136 clean reaches): median 211px, p90 559px, max 973px, which is
    lognormal with mu=ln(211) and sigma~0.76. It previously used median 55px / sigma 0.95, which
    was a guess -- and it mattered, because every threshold below is a statistic OF this
    distribution. Comparing an engine r^2 measured over one distance distribution against a
    human r^2 measured over a different one is not a comparison at all.
    """
    return [max(15.0, math.exp(rng.gauss(math.log(211), 0.76))) for _ in range(n)]


# ---- the core fix: timing must not be a function of distance ----

def test_duration_is_only_weakly_predicted_by_distance():
    """THE regression guard for v2's entire reason to exist.

    The old bot measured 0.512. The human measured 0.280 on the golem capture and 0.375 on the
    larger 2026-08-27 mixed dataset -- so "human" is a range, not a point, and the thing to guard
    is not drifting back toward the bot's 0.512.

    RE-BASED 2026-08-27: this asserted < 0.32, a threshold taken from the golem figure while the
    r^2 was computed over a made-up distance distribution (see _sample_distances). Measured over
    the human's real distance distribution the engine sits at 0.402 and the human at 0.375.
    """
    rng = random.Random(1234)
    dists = _sample_distances(rng, 4000)
    durs = [human_move.duration_for(d, rng) for d in dists]
    assert _fit_r2(dists, durs) < 0.50


def test_duration_spread_matches_the_human_not_the_old_engine():
    """v1's own log-duration sigma is ~0.154 (its +/-15% jitter).

    RE-BASED 2026-08-27. The old 0.675 target was a DOUBLE COUNT -- measured across all
    distances, so it also contained the variation HM2_SLOPE_MS_PER_BIT already applies. Measured
    the same way as this assertion (across the human's real distance distribution) the human is
    0.401 and the engine 0.519; the residual spread around the distance model, which is what
    HM2_SIGMA actually sets, is 0.368.
    """
    rng = random.Random(99)
    durs = [human_move.duration_for(d, rng) for d in _sample_distances(rng, 4000)]
    sigma = statistics.pstdev([math.log(d) for d in durs])
    assert 0.35 <= sigma <= 0.70


def test_duration_median_matches_the_measured_human():
    """The human's measured median is 432ms (see human_move.py's header table).

    REVERSED 2026-08-26. This used to assert 150-260ms, encoding the original "keep current
    speed" decision: v2 fixed the SHAPE of the timing distribution but deliberately kept the old
    engine's clock, leaving it at 200/432 = 46% of human duration. MEASURED live that day on
    routines/plank_make.py, an 842px reach took v2 264ms against v1's 748ms and a human model's
    ~1029ms -- below the ~360ms Fitts's law floor for that reach, i.e. faster than a human can
    physically point. See settings.py's HM2_* block for the full derivation.

    RE-BASED 2026-08-27 against the larger mixed dataset: the human's median reach is 415ms
    (n=136) and the engine now sits at 405ms over the same distance distribution. The earlier
    380-480 window came from a 432ms figure that was the median ACROSS all distances being used
    as the value AT the 52px reference distance -- which made every short reach far too slow.
    """
    rng = random.Random(7)
    durs = [human_move.duration_for(d, rng) for d in _sample_distances(rng, 4000)]
    assert 350.0 <= statistics.median(durs) <= 480.0


def test_duration_respects_clamps():
    """HM2_MAX_MS caps the duration EXCEPT where that would force a superhuman mean speed:
    beyond HM2_MAX_MS * HM2_MAX_MEAN_PX_PER_MS px (3500 at current settings, within reach of a
    4K diagonal) the speed cap wins and the duration is allowed past the cap instead -- moving
    faster than the measured human top speed is the worse detection signature of the two."""
    rng = random.Random(5)
    for d in (1, 5, 50, 500, 5000, 50000):
        for _ in range(200):
            ms = human_move.duration_for(d, rng)
            assert ms >= settings.HM2_MIN_MS
            assert ms <= max(settings.HM2_MAX_MS, d / settings.HM2_MAX_MEAN_PX_PER_MS)
            assert d / ms <= settings.HM2_MAX_MEAN_PX_PER_MS


# ---- submovement structure ----

def test_submovement_counts_have_a_tail_instead_of_collapsing_onto_two():
    """The measured bot needed 4+ submovements on 2% of reaches; the human on 42%. v1 cannot
    produce a tail at all, because maybe_overshoot_point is a single coin flip."""
    rng = random.Random(2024)
    counts = [len(human_move.plan_submovements((0, 0), (d, 0), rng))
              for d in _sample_distances(rng, 3000)]
    share_4plus = sum(1 for c in counts if c >= 4) / len(counts)
    assert share_4plus > 0.15, f"tail too thin: {share_4plus:.1%} at 4+"
    assert len(set(counts)) >= 3, "counts collapsed onto too few values"


def test_final_submovement_lands_exactly_on_target():
    """v2 must never be less ACCURATE than v1 -- only differently shaped."""
    rng = random.Random(3)
    for _ in range(200):
        target = (rng.uniform(-900, 900), rng.uniform(-900, 900))
        pts = human_move.plan_submovements((0, 0), target, rng)
        assert pts[-1] == (float(target[0]), float(target[1]))


def test_submovement_count_is_bounded():
    rng = random.Random(11)
    for _ in range(500):
        pts = human_move.plan_submovements((0, 0), (rng.uniform(1, 2000), 0), rng)
        assert 1 <= len(pts) <= settings.HM2_MAX_SUBMOVES


# ---- path shape ----

def test_path_is_less_straight_than_the_old_engine():
    """Measured: human 0.787, current engine 0.963. A path that is still ~0.96 has not changed."""
    rng = random.Random(808)
    ratios = []
    for _ in range(300):
        dist = rng.uniform(60, 600)
        segs = human_move.build_segments((0, 0), (dist, 0), rng)
        path = sum(math.hypot(sx, sy) for sx, sy, _ in segs) / 100.0
        if path > 0:
            ratios.append(dist / path)
    assert statistics.median(ratios) < 0.93


# ---- segment emission ----

def test_segments_sum_to_the_requested_displacement():
    """Sub-pixel carry must not accumulate into a positional error."""
    rng = random.Random(41)
    for _ in range(120):
        tx, ty = rng.uniform(-800, 800), rng.uniform(-800, 800)
        segs = human_move.build_segments((0, 0), (tx, ty), rng)
        sx = sum(s[0] for s in segs) / 100.0
        sy = sum(s[1] for s in segs) / 100.0
        assert math.hypot(sx - tx, sy - ty) < 1.0


def test_segments_respect_firmware_limits():
    """Firmware.c clamps to +/-2000 px and 1000 ms per segment; exceeding either silently
    truncates the motion."""
    rng = random.Random(17)
    for _ in range(200):
        segs = human_move.build_segments((0, 0), (rng.uniform(-1500, 1500),
                                                  rng.uniform(-1500, 1500)), rng)
        for qx, qy, dur in segs:
            assert abs(qx) <= 200000 and abs(qy) <= 200000
            assert 1 <= dur <= 1000


# ---- rollout safety ----

def test_v1_is_untouched_while_the_flag_is_off(monkeypatch):
    """The rollout guarantee: 157 call sites keep v1 behaviour until the flag is flipped."""
    monkeypatch.setattr(controller, "HUMAN_MOVE_V2", False)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    monkeypatch.setattr(controller, "maybe_overshoot_point", lambda *a, **k: None)
    v2_calls = []
    monkeypatch.setattr(human_move, "move", lambda *a, **k: v2_calls.append(1))
    exact = []
    monkeypatch.setattr(controller, "_move_to_human_exact",
                        lambda ser, wx, wy, **kw: exact.append((wx, wy)))

    controller.move_to_human(None, 400, 400)

    assert v2_calls == []
    assert exact, "v1 path did not run"


def test_flag_on_routes_everything_through_v2(monkeypatch):
    monkeypatch.setattr(controller, "HUMAN_MOVE_V2", True)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    v2_calls = []
    monkeypatch.setattr(human_move, "move", lambda *a, **k: v2_calls.append(k))
    exact = []
    monkeypatch.setattr(controller, "_move_to_human_exact",
                        lambda ser, wx, wy, **kw: exact.append(1))

    controller.move_to_human(None, 400, 400, refresh_target=lambda: (400, 400), refresh_legs=6)

    assert len(v2_calls) == 1
    assert exact == [], "v1 path ran despite the flag being on"
    # refresh_target must survive the hop; refresh_legs is deliberately dropped (v2 has no legs).
    assert v2_calls[0]["refresh_target"] is not None


def test_v2_emits_S_segments_and_never_legacy_M(monkeypatch):
    """v2 depends on Firmware.c v2. Emitting a bare 'M' would mean falling back to whole-pixel
    moves and losing the sub-pixel motion the firmware work exists to provide."""
    lines = []
    monkeypatch.setattr(human_move.time, "sleep", lambda *_: None)
    human_move.move(None, 300, 120,
                    get_cursor_pos=lambda: (0, 0),
                    send_line=lambda ser, s: lines.append(s),
                    rng=random.Random(6))
    assert lines, "no segments emitted"
    assert all(line.startswith("S ") for line in lines)


def test_v2_lands_on_target_even_when_the_os_scales_pointer_motion(monkeypatch):
    """REGRESSION, found on hardware 2026-08-24: v2 missed by a median of 122px over a median
    distance of 495px -- a 24.6% undershoot exactly matching the machine's measured 0.74 px per
    mouse unit (Windows pointer speed at 5/11 rather than the neutral 6/11).

    v1 never had this problem because _move_to_human_exact is closed-loop: it re-reads the cursor
    every step and emits `target - current`, so any scaling silently cancels out. v2 planned the
    whole path up front and streamed it open-loop, so the error went straight to the screen.

    The engine must not assume one emitted unit equals one screen pixel.
    """
    SCALE = 0.74
    pos = [0.0, 0.0]

    def fake_send(ser, line):
        _, qx, qy, _dur = line.split()
        pos[0] += int(qx) / 100.0 * SCALE
        pos[1] += int(qy) / 100.0 * SCALE

    monkeypatch.setattr(human_move.time, "sleep", lambda *_: None)
    human_move.move(None, 600, 300,
                    get_cursor_pos=lambda: (round(pos[0]), round(pos[1])),
                    send_line=fake_send, rng=random.Random(21))

    assert math.hypot(pos[0] - 600, pos[1] - 300) < 5.0, (
        f"landed at ({pos[0]:.0f}, {pos[1]:.0f}) instead of (600, 300) -- v2 is open-loop and "
        f"does not correct for pointer scaling")


def test_v2_lands_on_target_with_no_scaling():
    """The same guarantee on a neutral machine, so the fix cannot be a hardcoded 0.74."""
    pos = [0.0, 0.0]

    def fake_send(ser, line):
        _, qx, qy, _dur = line.split()
        pos[0] += int(qx) / 100.0
        pos[1] += int(qy) / 100.0

    import unittest.mock as _m
    with _m.patch.object(human_move.time, "sleep", lambda *_: None):
        human_move.move(None, -420, 260,
                        get_cursor_pos=lambda: (round(pos[0]), round(pos[1])),
                        send_line=fake_send, rng=random.Random(4))

    assert math.hypot(pos[0] + 420, pos[1] - 260) < 5.0


def test_v2_follows_a_moving_target_to_its_LATEST_position(monkeypatch):
    """REGRESSION, reported live 2026-08-24 ("still a lot of misses").

    slayer.py picks its target from the TickCache-gated /npcs and compensates by passing
    refresh_target (an uncached /npc/by-index lookup). v1 honoured that. v2 did not:

      - refresh_target()'s result was discarded unless it was the final submovement, so the
        round trip was paid and thrown away, and
      - the final-correction loop closed on `target`, the ORIGINAL coordinate, which nothing
        ever updated -- so it actively dragged the cursor BACK to the stale position.

    A gargoyle that moves during the flight was therefore missed by construction.
    """
    pos = [0.0, 0.0]

    def fake_send(ser, line):
        _, qx, qy, _dur = line.split()
        pos[0] += int(qx) / 100.0
        pos[1] += int(qy) / 100.0

    # The target walks away while we fly at it; the last value it reports is where it really is.
    live = [500, 300]

    def refresh():
        live[0] += 12
        live[1] += 6
        return (live[0], live[1])

    monkeypatch.setattr(human_move.time, "sleep", lambda *_: None)
    human_move.move(None, 500, 300,
                    get_cursor_pos=lambda: (round(pos[0]), round(pos[1])),
                    send_line=fake_send, refresh_target=refresh, rng=random.Random(8))

    err_latest = math.hypot(pos[0] - live[0], pos[1] - live[1])
    err_original = math.hypot(pos[0] - 500, pos[1] - 300)
    assert err_latest < err_original, (
        f"ended {err_latest:.0f}px from the target's CURRENT position but {err_original:.0f}px "
        f"from its original one -- refresh_target is being ignored")
    assert err_latest < 8.0, f"did not converge on the moving target ({err_latest:.1f}px off)"


def test_v2_tolerates_a_target_that_disappears_mid_flight(monkeypatch):
    """refresh_target returning None (npc died/despawned) must not crash or strand the cursor."""
    pos = [0.0, 0.0]

    def fake_send(ser, line):
        _, qx, qy, _dur = line.split()
        pos[0] += int(qx) / 100.0
        pos[1] += int(qy) / 100.0

    monkeypatch.setattr(human_move.time, "sleep", lambda *_: None)
    human_move.move(None, 400, 200,
                    get_cursor_pos=lambda: (round(pos[0]), round(pos[1])),
                    send_line=fake_send, refresh_target=lambda: None, rng=random.Random(9))
    # Falls back to the last known good target -- the original one here.
    assert math.hypot(pos[0] - 400, pos[1] - 200) < 8.0


def _run_capturing_segments(target, seed, scale=0.757, refresh=None):
    """Drive move() against an INTEGER-quantised cursor, like the real GetCursorPos."""
    pos = [0.0, 0.0]
    emitted = []

    def send(ser, line):
        _, qx, qy, _d = line.split()
        pos[0] += int(qx) / 100.0 * scale
        pos[1] += int(qy) / 100.0 * scale
        emitted.append((int(qx), int(qy)))

    import unittest.mock as _m
    with _m.patch.object(human_move.time, "sleep", lambda *_: None):
        human_move._px_per_unit = scale
        human_move.move(None, target[0], target[1],
                        get_cursor_pos=lambda: (round(pos[0]), round(pos[1])),
                        send_line=send, refresh_target=refresh, rng=random.Random(seed))
    return pos, emitted


def _run_capturing_track(target, seed, scale=0.757, refresh=None):
    """Same as _run_capturing_segments but returns the cursor's path, for _dawdle_px."""
    pos = [0.0, 0.0]
    track = []

    def send(ser, line):
        _, qx, qy, _d = line.split()
        pos[0] += int(qx) / 100.0 * scale
        pos[1] += int(qy) / 100.0 * scale
        track.append((pos[0], pos[1]))

    import unittest.mock as _m
    with _m.patch.object(human_move.time, "sleep", lambda *_: None):
        human_move._px_per_unit = scale
        human_move.move(None, target[0], target[1],
                        get_cursor_pos=lambda: (round(pos[0]), round(pos[1])),
                        send_line=send, refresh_target=refresh, rng=random.Random(seed))
    return track


def _settle_px(track, target, near=6.0):
    """Path length travelled after the LAST time the cursor was further than `near` from target
    -- i.e. the final settling phase only.

    This is the metric that captures the reported symptom ("the mouse kinda just hovers over it
    and jitters like crazy"). Two metrics were tried and rejected first, and both are worth not
    repeating: counting direction REVERSALS flags the engine's intentional tremor as a bug, and
    measuring from the FIRST arrival within `near` counts an entire legitimate movement whenever
    the trajectory happens to sweep past the target early (that read 48px on a reach whose real
    settle was 3px).
    """
    last = -1
    for i, (x, y) in enumerate(track):
        if math.hypot(x - target[0], y - target[1]) > near:
            last = i
    seg = track[last + 1:]
    if len(seg) < 2:
        return 0.0
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(seg, seg[1:]))


def test_v2_settles_instead_of_dawdling_on_a_static_target():
    """REGRESSION, reported live 2026-08-24. Tremor was an ABSOLUTE 1.5px regardless of how far
    a submovement travelled. On the primary reach that is invisible; on a 2px final correction it
    is 75% of the movement, and every correction still got a full complement of segments -- so
    the cursor churned in place near the target."""
    worst = 0.0
    for seed in range(12):
        track = _run_capturing_track((300, 200), seed)
        worst = max(worst, _settle_px(track, (300, 200)))
    assert worst < 15.0, f"{worst:.0f}px of churn in the settle phase -- visible as jitter"


def test_v2_settles_while_tracking_a_moving_target():
    """The same symptom's worse half: a walking NPC also triggered a replan on every refresh."""
    worst = 0.0
    for seed in range(10):
        live = [400.0, 250.0]

        def refresh():
            live[0] += 2.0
            live[1] += 1.0
            return (live[0], live[1])

        track = _run_capturing_track((400, 250), seed, refresh=refresh)
        worst = max(worst, _settle_px(track, (live[0], live[1])))
    assert worst < 12.0, f"{worst:.0f}px of churn in the settle phase while tracking"


def test_v2_stops_promptly_on_abort(monkeypatch):
    class Abort:
        def is_set(self):
            return True

    lines = []
    monkeypatch.setattr(human_move.time, "sleep", lambda *_: None)
    human_move.move(None, 500, 500, get_cursor_pos=lambda: (0, 0),
                    send_line=lambda ser, s: lines.append(s), abort=Abort())
    assert lines == []


# ---- the end-of-path snap ----

def test_no_segment_ever_exceeds_human_top_speed_even_when_the_scale_is_wrong():
    """USER-REPORTED 2026-08-26: "sometimes half of a path is super slow then goes blazing fast".

    The residual-correction loop used to emit the WHOLE remaining gap as one segment with a flat
    HM2_SEGMENT_MS duration, so the further the planned path fell short the FASTER the cursor
    closed it. The gap is largest exactly while _px_per_unit is still converging from its 1.0
    starting guess toward this machine's ~0.74, which under-travels the whole path by ~26%.

    Human speeds MEASURED from recordings/2026-08-26_225917_mousemove (n=2097 ~10ms windows):
    p50 0.40, p99 3.99, max 5.37 px/ms. The old code hit 7.6 px/ms on a 527px reach and would
    have hit ~13 px/ms on a 900px one -- 2.4x the fastest instant in the whole human session.
    """
    scale = 0.74                      # what the OS actually does
    pos = [0.0, 0.0]
    speeds = []

    def send(ser, line):
        _, qx, qy, dur = line.split()
        dx, dy = int(qx) / 100.0 * scale, int(qy) / 100.0 * scale
        pos[0] += dx
        pos[1] += dy
        speeds.append(math.hypot(dx, dy) / max(1, int(dur)))

    import unittest.mock as _m
    with _m.patch.object(human_move.time, "sleep", lambda *_: None):
        human_move._px_per_unit = 1.0          # the un-learned starting guess
        human_move._scale_samples = 0
        human_move.move(None, 900, 0,
                        get_cursor_pos=lambda: (round(pos[0]), round(pos[1])),
                        send_line=send, rng=random.Random(3))

    # Bounded by the human's fastest OBSERVED instant, not by the correction cap: the planned
    # path legitimately runs at ballistic speed (~1-2 px/ms), and only the residual corrections
    # are held to HM2_MAX_CORRECTION_PX_PER_MS. Asserting the correction cap over every segment
    # conflated the two populations -- see that constant's comment for the measurement showing
    # the homing phase is an order of magnitude slower than the throw.
    assert max(speeds) <= HUMAN_MAX_INSTANT_PX_PER_MS
    assert math.hypot(900 - pos[0], 0 - pos[1]) <= 3.0     # still lands


def test_pointer_scale_converges_on_the_first_measurement():
    """A flat 0.25 EMA needed eight to ten movements to get from 1.0 to ~0.74. plank_make.py
    flies the mouse about four times a MINUTE, so that left it mis-calibrated -- and therefore
    snapping at the end of every reach -- for minutes after each restart."""
    human_move._px_per_unit = 1.0
    human_move._scale_samples = 0
    human_move._learn_scale(units=1000.0, actual_px=740.0)
    assert abs(human_move._px_per_unit - 0.74) < 0.01

    # ...and later samples are still damped, so one odd movement cannot yank the calibration.
    for _ in range(6):
        human_move._learn_scale(units=1000.0, actual_px=740.0)
    human_move._learn_scale(units=1000.0, actual_px=2000.0)
    assert human_move._px_per_unit < 1.1


# ---- calibration persistence ----

def test_pointer_scale_persists_across_restart():
    """_px_per_unit is a property of the MACHINE (the Windows pointer-speed setting), not of a
    session -- but it reset to 1.0 on every process start, so the first flights after each
    restart under-traveled (~26% on this machine) and the residual-correction loop cleaned up
    the difference. Learning it once should survive a restart."""
    human_move._px_per_unit = 1.0
    human_move._scale_samples = 0
    human_move._learn_scale(units=1000.0, actual_px=740.0)
    learned = human_move._px_per_unit

    # Simulate a process restart: state back to defaults, then load what was persisted.
    human_move._px_per_unit = 1.0
    human_move._scale_samples = 0
    human_move._load_calibration()
    assert abs(human_move._px_per_unit - learned) < 1e-9
    # The restored estimate must count as already-settled: a fresh session's first sample
    # should be damped by the EMA, not treated as the first measurement ever (which gets
    # alpha 1.0 and would throw the loaded value away).
    assert human_move._scale_samples >= 4


def test_calibration_load_tolerates_missing_and_corrupt_files():
    human_move._px_per_unit = 1.0
    human_move._scale_samples = 0
    human_move._load_calibration()                      # no file yet
    assert human_move._px_per_unit == 1.0
    assert human_move._scale_samples == 0

    with open(human_move._CALIBRATION_PATH, "w") as f:
        f.write("{not json")
    human_move._load_calibration()                      # corrupt file
    assert human_move._px_per_unit == 1.0

    with open(human_move._CALIBRATION_PATH, "w") as f:
        f.write('{"px_per_unit": 9.0, "samples": 3}')
    human_move._load_calibration()                      # out-of-range value
    assert human_move._px_per_unit == 1.0


# ---- v3 flush (retractable firmware queue) ----

class FakeClock:
    """move() paces itself with time.perf_counter/time.sleep; this replaces both so a test
    controls elapsed time exactly and runs instantly."""
    def __init__(self):
        self.t = 0.0

    def perf_counter(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, seconds)


class FakeFirmware:
    """Minimal model of Firmware.ino's queue: S segments play back-to-back on the fake clock,
    and X drops every segment not yet STARTED at that instant -- the active one finishes,
    mirroring flushQueue() keeping queue[q_head]. NOTE: segment dx/dy are NOT guaranteed to be
    pixels, despite the name -- `_isolate_calibration` only redirects _CALIBRATION_PATH, it does
    not reset _px_per_unit. That's a module global set once, at import time, by
    _load_calibration() reading the REAL machine's calibration file (human_move.py:396) --
    before any fixture runs, and measured at 0.737 during the 2026-08-27 review that caught this
    comment. The landing tests below still converge on target regardless of that scale: the
    open-loop `cur` move() tracks internally is exact plan-space bookkeeping independent of
    _px_per_unit, and the closed-loop final corrections re-read this class's own pos() every
    iteration and self-correct for whatever scale is actually in effect."""
    def __init__(self, clk):
        self.clk = clk
        self.segs = []        # [start_ms, end_ms, dx_px, dy_px]
        self.t_tail = 0.0     # timeline position of the last queued segment's end
        self.flushes = 0

    def _now_ms(self):
        return self.clk.t * 1000.0

    def send(self, ser, line):
        if line == "X":
            self.flushes += 1
            self.segs = [s for s in self.segs if s[0] <= self._now_ms()]
            self.t_tail = self.segs[-1][1] if self.segs else self._now_ms()
            return
        if not line.startswith("S "):
            return
        _, dxq, dyq, dur = line.split()
        start = max(self.t_tail, self._now_ms())
        end = start + max(1.0, float(dur))
        self.segs.append([start, end, int(dxq) / 100.0, int(dyq) / 100.0])
        self.t_tail = end

    def pos(self):
        now, x, y = self._now_ms(), 0.0, 0.0
        for start, end, dx, dy in self.segs:
            if now >= end:
                x += dx
                y += dy
            elif now > start:
                frac = (now - start) / (end - start)
                x += dx * frac
                y += dy * frac
        return (round(x), round(y))


def _flush_rig(monkeypatch):
    clk = FakeClock()
    monkeypatch.setattr(human_move.time, "perf_counter", clk.perf_counter)
    monkeypatch.setattr(human_move.time, "sleep", clk.sleep)
    return clk, FakeFirmware(clk)


def test_flush_fires_on_replan_and_flight_still_lands(monkeypatch):
    """A big mid-flight target jump must (a) send X to retract queued motion and (b) still
    finish on the NEW target. This is a coarse regression guard, NOT proof the rewind itself is
    correct -- HM2_FINAL_CORRECTIONS' six closed-loop corrections (each re-reading the TRUE
    cursor via get_cursor_pos()) would absorb a missing, inverted, or wrong-entry rewind just as
    well; a null-rewind was measured to pass this exact assertion on 6/6 seeds. See
    test_flush_rewind_keeps_queue_pacing_tight for a test that checks the rewind itself."""
    clk, fw = _flush_rig(monkeypatch)
    state = {"target": (800, 0), "calls": 0}

    def refresh():
        state["calls"] += 1
        if state["calls"] == 1:
            state["target"] = (800, 260)
        return state["target"]

    human_move.move(None, 800, 0,
                    get_cursor_pos=fw.pos, send_line=fw.send,
                    refresh_target=refresh,
                    flush_queue=lambda: fw.send(None, "X"),
                    rng=random.Random(6))
    clk.t += 5.0   # let every queued segment play out
    assert fw.flushes >= 1
    fx, fy = fw.pos()
    miss = math.hypot(fx - 800, fy - 260)
    assert miss <= settings.HM2_CORRECTION_TOLERANCE_PX + 4, (
        f"landed {miss:.1f}px off the moved target -- post-flush rewind is inconsistent "
        f"with played motion")


def test_flush_rewind_keeps_queue_pacing_tight(monkeypatch):
    """Partial check on the rewind, via one observable SIDE EFFECT -- see
    test_flush_rewinds_queued_ms_to_the_committed_emission (below) for a direct assertion
    on the rewound value itself, which is the one that actually distinguishes every wrong-entry
    case; this test only catches an UNDER-correction (a stale/unrewound queued_ms), and says so
    plainly rather than overclaiming.

    The signal: move()'s backpressure throttle keeps `queued_ms` within HM2_LOOKAHEAD_SEGMENTS *
    HM2_SEGMENT_MS of elapsed real time (`ahead = queued_ms - elapsed; sleep iff ahead - 54 > 0`,
    right after every send). If cur/queued_ms are NOT rewound on flush at all, queued_ms is left
    at its full pre-flush value -- a whole lookahead window (~54ms / 3 segments) ahead of where
    the firmware actually is after the flush discarded most of that queue -- so the very next
    backpressure check believes it is already over budget and sleeps a full extra segment before
    the SECOND post-flush segment goes out.

    BUT the direction only cuts one way: `ahead` is `queued_ms - elapsed`, so a SMALLER queued_ms
    only ever reduces `ahead`/`slack`, never grows it -- it can never trigger a sleep. That means
    this gap is silent (0ms, same as a correct rewind) for an OVER-correction (rewinding to an
    earlier entry than the algorithm specifies), for an INVERTED comparison (rewinding nearly to
    the start of the flight, also smaller-than-correct), and in general for any mis-pick that
    lands on or before the correct entry. MEASURED directly across 8 seeds: with the rewind
    stubbed out entirely (a no-op/deletion -- the one case this DOES catch), the gap between the
    first two post-flush sends is consistently ~16-21ms (one segment); with the rewind intact,
    consistently 0.0ms.
    """
    clk, fw = _flush_rig(monkeypatch)
    sent = []   # (line, t_ms_at_send) for every line actually sent, in order

    def spy_send(ser, line):
        sent.append((line, clk.t * 1000.0))
        fw.send(ser, line)

    state = {"target": (800, 0), "calls": 0}

    def refresh():
        state["calls"] += 1
        if state["calls"] == 1:
            state["target"] = (800, 260)
        return state["target"]

    human_move.move(None, 800, 0,
                    get_cursor_pos=fw.pos, send_line=spy_send,
                    refresh_target=refresh,
                    flush_queue=lambda: spy_send(None, "X"),
                    rng=random.Random(6))

    assert fw.flushes == 1
    x_idx = next(i for i, (line, _) in enumerate(sent) if line == "X")
    post_flush_sends = [t for line, t in sent[x_idx + 1:] if line.startswith("S ")]
    assert len(post_flush_sends) >= 2, "flushed flight didn't emit enough afterward to check pacing"
    gap = post_flush_sends[1] - post_flush_sends[0]
    assert gap < settings.HM2_SEGMENT_MS / 2, (
        f"{gap:.1f}ms gap between the first two post-flush segments -- queued_ms was not "
        f"rewound close to elapsed, so the backpressure throttle still thinks it's queued a "
        f"stale lookahead window ahead of the firmware")


def test_flush_rewinds_queued_ms_to_the_committed_emission(monkeypatch):
    """Direct check on the REWOUND VALUE ITSELF, not a side effect. The pacing test above
    (test_flush_rewind_keeps_queue_pacing_tight) only trips for an under-correction; this
    test catches an over-correction, an inverted comparison, or a mis-pick in either direction
    too, because it asserts the rewound `queued_ms` equals the value the algorithm SPECIFIES,
    not just something a downstream side effect happens to tolerate.

    How the rewound queued_ms is recovered from outside move(), with no change to human_move.py:
      1. _drain(t0, queued_ms) sleeps the fake clock until elapsed == queued_ms (measured from
         t0), and the very next statement in move() is `cur = tuple(get_cursor_pos())` -- the
         first get_cursor_pos() call after the single one at the very top of the function
         (`start = get_cursor_pos()`). On this rig t0 == 0 (the fake clock starts at 0.0 and
         nothing sleeps before t0 is captured), so the clock reading at that SECOND
         get_cursor_pos() call equals the FINAL queued_ms, in milliseconds, exactly.
      2. queued_ms only ever grows after that point via the normal `queued_ms += dur` per sent
         segment. Subtracting the duration of every segment sent between the flush and that
         second get_cursor_pos() call removes everything accumulated AFTER the rewind, leaving
         the rewound value itself.

    The expected value is computed independently from data this test already captures (every
    send, in order, plus the elapsed time at the moment flush_queue() was invoked): replicate
    move()'s own specified algorithm -- commit_ms = flush_elapsed_ms + HM2_FLUSH_COMMIT_MS,
    expected = the first PRE-flush cumulative duration sum >= commit_ms -- the exact emission the
    implementation's scan is specified to pick.
    """
    clk, fw = _flush_rig(monkeypatch)
    events = []   # chronological across BOTH hooks: ("S", dur_ms) | ("X",) | ("POS", clk.t)

    def spy_send(ser, line):
        if line == "X":
            events.append(("X",))
        elif line.startswith("S "):
            events.append(("S", float(line.split()[3])))
        fw.send(ser, line)

    def spy_pos():
        events.append(("POS", clk.t))
        return fw.pos()

    flush_info = {}

    def flush_queue():
        flush_info["elapsed_ms"] = clk.t * 1000.0
        spy_send(None, "X")

    state = {"target": (800, 0), "calls": 0}

    def refresh():
        state["calls"] += 1
        if state["calls"] == 1:
            state["target"] = (800, 260)
        return state["target"]

    human_move.move(None, 800, 0,
                    get_cursor_pos=spy_pos, send_line=spy_send,
                    refresh_target=refresh,
                    flush_queue=flush_queue,
                    rng=random.Random(6))

    assert fw.flushes == 1
    assert "elapsed_ms" in flush_info

    x_idx = next(i for i, ev in enumerate(events) if ev[0] == "X")
    pos_indices = [i for i, ev in enumerate(events) if ev[0] == "POS"]
    # pos_indices[0] is the `start = get_cursor_pos()` call at the very top of move(), before any
    # S/X events at all; pos_indices[1] is the next one -- the only other get_cursor_pos() call
    # in the open-loop portion of the function, immediately after _drain(t0, final_queued_ms).
    assert len(pos_indices) >= 2, "expected a post-emission-loop get_cursor_pos() call"
    drain_pos_idx = pos_indices[1]
    assert drain_pos_idx > x_idx, "the flush must precede the end of the open-loop emission"

    pre_flush_durs = [ev[1] for ev in events[:x_idx] if ev[0] == "S"]
    post_flush_durs = [ev[1] for ev in events[x_idx + 1:drain_pos_idx] if ev[0] == "S"]

    commit_ms = flush_info["elapsed_ms"] + settings.HM2_FLUSH_COMMIT_MS
    cum = 0.0
    expected_queued_ms = None
    for d in pre_flush_durs:
        cum += d
        if cum >= commit_ms:
            expected_queued_ms = cum
            break
    assert expected_queued_ms is not None, "test scenario didn't queue enough to reach commit_ms"

    final_queued_ms = events[drain_pos_idx][1] * 1000.0
    actual_queued_ms = final_queued_ms - sum(post_flush_durs)

    assert abs(actual_queued_ms - expected_queued_ms) <= settings.HM2_SEGMENT_MS / 4, (
        f"rewound queued_ms={actual_queued_ms:.1f}ms, expected {expected_queued_ms:.1f}ms "
        f"(commit_ms={commit_ms:.1f}) -- the rewind picked the wrong emission")


def test_no_flush_without_replan(monkeypatch):
    """A stationary target must never pay a flush."""
    clk, fw = _flush_rig(monkeypatch)
    human_move.move(None, 600, 300,
                    get_cursor_pos=fw.pos, send_line=fw.send,
                    refresh_target=lambda: (600, 300),
                    flush_queue=lambda: fw.send(None, "X"),
                    rng=random.Random(3))
    assert fw.flushes == 0


def test_flushed_flight_skips_scale_learning(monkeypatch):
    """Emitted units on a flushed flight include motion that never played, so feeding them to
    _learn_scale would poison the persisted calibration. A clean flight must still learn."""
    clk, fw = _flush_rig(monkeypatch)
    state = {"target": (800, 0), "calls": 0}

    def refresh():
        state["calls"] += 1
        if state["calls"] == 1:
            state["target"] = (800, 260)
        return state["target"]

    before = human_move._scale_samples
    human_move.move(None, 800, 0,
                    get_cursor_pos=fw.pos, send_line=fw.send,
                    refresh_target=refresh,
                    flush_queue=lambda: fw.send(None, "X"),
                    rng=random.Random(6))
    assert human_move._scale_samples == before, "flushed flight fed the calibration"

    clk2, fw2 = _flush_rig(monkeypatch)
    human_move.move(None, 500, 200,
                    get_cursor_pos=fw2.pos, send_line=fw2.send,
                    rng=random.Random(7))
    assert human_move._scale_samples == before + 1, "clean flight failed to learn"
