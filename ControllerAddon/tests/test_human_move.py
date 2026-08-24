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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import human_move
import settings


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
    return [max(15.0, math.exp(rng.gauss(math.log(55), 0.95))) for _ in range(n)]


# ---- the core fix: timing must not be a function of distance ----

def test_duration_is_only_weakly_predicted_by_distance():
    """THE regression guard for v2's entire reason to exist.

    The measured bot was 0.512; the human 0.280. v1's formula simulates at 0.322. If this climbs
    back toward 0.5 the distance signature has returned.
    """
    rng = random.Random(1234)
    dists = _sample_distances(rng, 4000)
    durs = [human_move.duration_for(d, rng) for d in dists]
    assert _fit_r2(dists, durs) < 0.32


def test_duration_spread_matches_the_human_not_the_old_engine():
    """v1's own log-duration sigma is ~0.154 (its +/-15% jitter). The human's is 0.675."""
    rng = random.Random(99)
    durs = [human_move.duration_for(d, rng) for d in _sample_distances(rng, 4000)]
    sigma = statistics.pstdev([math.log(d) for d in durs])
    assert 0.55 <= sigma <= 0.80


def test_duration_median_preserves_current_speed():
    """'Keep current speed' was an explicit decision -- v2 fixes structure, not throughput."""
    rng = random.Random(7)
    durs = [human_move.duration_for(d, rng) for d in _sample_distances(rng, 4000)]
    assert 150.0 <= statistics.median(durs) <= 260.0


def test_duration_respects_clamps():
    rng = random.Random(5)
    for d in (1, 5, 50, 500, 5000, 50000):
        for _ in range(200):
            ms = human_move.duration_for(d, rng)
            assert settings.HM2_MIN_MS <= ms <= settings.HM2_MAX_MS


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
