import dataclasses
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller


class _FakeResponse:
    def __init__(self, json_body):
        self._json_body = json_body

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_body


def test_api_get_uses_shared_session_not_a_fresh_connection_per_call(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_dpi_scale", lambda: 1.0)
    monkeypatch.setattr(
        controller._session, "get",
        lambda url, params=None, timeout=0.3: calls.append((url, params)) or _FakeResponse({}),
    )

    controller.api_get("/state")

    assert len(calls) == 1
    assert calls[0][0] == "http://localhost:8080/state"


def test_get_bank_without_fresh_sends_no_fresh_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=0.3: captured.update(params=params) or {"open": False},
    )

    controller.get_bank()

    assert captured["params"] is None


def test_get_bank_with_fresh_true_sends_fresh_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=0.3: captured.update(params=params) or {"open": False},
    )

    controller.get_bank(fresh=True)

    assert captured["params"] == {"fresh": "true"}


def test_get_objects_combines_radius_and_fresh_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=1.0: captured.update(params=params) or [],
    )

    controller.get_objects(radius=10, fresh=True)

    assert captured["params"] == {"radius": 10, "fresh": "true"}


def test_get_objects_with_no_radius_and_no_fresh_sends_no_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=1.0: captured.update(params=params) or [],
    )

    controller.get_objects()

    assert captured["params"] is None


# ---- move_to_human's refresh_legs (multi-leg tracking) additions, 2026-08-14 ----
# Pure-logic coverage only -- live mouse movement genuinely can't be unit tested, but
# _move_to_human_tracking's leg/refresh bookkeeping is cheap to verify with the real cursor and
# serial calls monkeypatched out. aim_radius=0 is used throughout to get deterministic zero
# jitter (pick_aim_point's own "radius <= 0 -> return x, y unchanged" shortcut), instead of
# needing to monkeypatch pick_aim_point itself.

def test_move_to_human_without_refresh_legs_never_uses_tracking(monkeypatch):
    """Regression guard for the "purely additive" claim: refresh_legs=None (the default for
    every pre-existing caller) must never reach _move_to_human_tracking at all."""
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    monkeypatch.setattr(controller, "maybe_overshoot_point", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_move_to_human_exact", lambda *a, **k: None)
    tracking_calls = []
    monkeypatch.setattr(controller, "_move_to_human_tracking", lambda *a, **k: tracking_calls.append(1))

    controller.move_to_human(None, 500, 500, refresh_target=lambda: (500, 500))

    assert tracking_calls == []


def test_move_to_human_tracking_reaims_once_per_leg_after_the_first(monkeypatch):
    positions = [(0, 0)]
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: positions[-1])

    exact_calls = []

    def fake_exact(ser, wx, wy, *, fps, duration_scale):
        exact_calls.append((wx, wy))
        positions.append((wx, wy))

    monkeypatch.setattr(controller, "_move_to_human_exact", fake_exact)

    refresh_calls = []

    def fake_refresh():
        refresh_calls.append(1)
        return (100, 100)

    controller._move_to_human_tracking(None, (0, 0), 100, 100, fps=190, aim_radius=0,
                                        duration_scale=1.0, refresh_target=fake_refresh, legs=4)

    assert len(exact_calls) == 4
    assert len(refresh_calls) == 3  # legs - 1: no re-aim needed before the very first leg
    assert exact_calls[-1] == (100, 100)  # final leg always lands exactly on the (jittered) aim


def test_move_to_human_tracking_first_leg_does_not_fly_the_full_distance(monkeypatch):
    """The bug caught in review before this ever ran live: leg 0 must travel only a fraction of
    the distance toward an interpolated waypoint, not the entire distance to the stale target
    (which would make re-aiming happen only after arrival, i.e. too late to matter)."""
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    exact_calls = []
    monkeypatch.setattr(controller, "_move_to_human_exact",
                         lambda ser, wx, wy, *, fps, duration_scale: exact_calls.append((wx, wy)))

    controller._move_to_human_tracking(None, (0, 0), 100, 0, fps=190, aim_radius=0,
                                        duration_scale=1.0, refresh_target=lambda: (100, 0), legs=4)

    assert exact_calls[0] == (25, 0)  # 1/4 of the way, not (100, 0)


def test_move_to_human_tracking_stops_on_abort(monkeypatch):
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    exact_calls = []
    monkeypatch.setattr(controller, "_move_to_human_exact", lambda *a, **k: exact_calls.append(1))
    controller._movement_abort.set()
    try:
        controller._move_to_human_tracking(None, (0, 0), 100, 100, fps=190, aim_radius=0,
                                            duration_scale=1.0, refresh_target=lambda: (100, 100), legs=4)
    finally:
        controller._movement_abort.clear()

    assert exact_calls == []


def test_move_to_human_tracking_stops_repolling_after_target_lost(monkeypatch):
    positions = [(0, 0)]
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: positions[-1])
    monkeypatch.setattr(
        controller, "_move_to_human_exact",
        lambda ser, wx, wy, *, fps, duration_scale: positions.append((wx, wy)),
    )
    refresh_calls = []

    def fake_refresh():
        refresh_calls.append(1)
        return None  # target vanished from the very first re-aim attempt

    controller._move_to_human_tracking(None, (0, 0), 100, 100, fps=190, aim_radius=0,
                                        duration_scale=1.0, refresh_target=fake_refresh, legs=4)

    assert len(refresh_calls) == 1  # tried once (leg 1), not again for legs 2/3


def test_get_human_profile_returns_same_profile_until_interval_elapses(monkeypatch):
    monkeypatch.setattr(controller, "_human_profile", None)

    first = controller.get_human_profile()
    again = controller.get_human_profile()

    assert again is first


def test_get_human_profile_rerolls_after_interval_elapses(monkeypatch):
    monkeypatch.setattr(controller, "_human_profile", None)
    first = controller.get_human_profile()

    stale = dataclasses.replace(
        first, rerolled_at=first.rerolled_at - (first.reroll_after_s + 1.0)
    )
    monkeypatch.setattr(controller, "_human_profile", stale)

    second = controller.get_human_profile()

    assert second is not stale
    assert second.rerolled_at > stale.rerolled_at


def test_sample_human_delay_median_tracks_midpoint_with_soft_floor():
    controller.random.seed(1234)
    samples = sorted(controller.sample_human_delay(1.0, 2.0) for _ in range(10000))

    median = samples[len(samples) // 2]
    assert 1.3 < median < 1.7  # tracks (1.0+2.0)/2 == 1.5

    assert min(samples) >= 1.0 * 0.6 - 1e-9  # soft floor, not min_s itself


def test_sample_human_delay_has_no_hard_ceiling_unlike_uniform():
    controller.random.seed(5678)
    samples = [controller.sample_human_delay(1.0, 2.0) for _ in range(10000)]

    over_ceiling = sum(1 for s in samples if s > 2.0)
    assert over_ceiling > 1000  # meaningfully non-zero; uniform(1,2) would give exactly 0


def test_sample_human_delay_is_not_uniformly_distributed():
    controller.random.seed(9012)
    samples = [controller.sample_human_delay(1.0, 2.0) for _ in range(10000)]

    mean = sum(samples) / len(samples)
    variance = sum((s - mean) ** 2 for s in samples) / len(samples)
    # uniform(1, 2)'s variance is (2-1)**2/12 ~= 0.083; the fat-tailed shape
    # should sit well above that regardless of the current delay_skew draw.
    assert variance > 0.15


def test_human_delay_sleeps_for_the_sampled_duration(monkeypatch):
    slept = []
    monkeypatch.setattr(controller.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 1.23)

    controller.human_delay(1.0, 2.0)

    assert slept == [1.23]


def test_pick_aim_point_jitter_spread_follows_profile_aim_sigma_factor(monkeypatch):
    def make_profile(factor):
        return controller.HumanProfile(
            aim_sigma_factor=factor, curve_mag_low=0.08, curve_mag_high=0.20,
            delay_skew=0.5, rerolled_at=0.0, reroll_after_s=1e9,
        )

    def variance(samples):
        mean = sum(samples) / len(samples)
        return sum((s - mean) ** 2 for s in samples) / len(samples)

    controller.random.seed(1)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(1.8))
    tight = [
        math.hypot(*controller.pick_aim_point(0, 0, 10)) for _ in range(5000)
    ]

    controller.random.seed(1)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(1.3))
    wide = [
        math.hypot(*controller.pick_aim_point(0, 0, 10)) for _ in range(5000)
    ]

    # Same seed sequence, only the profile's sigma factor differs -- a smaller
    # factor means a larger sigma (radius / factor) and should measurably widen
    # the spread of magnitudes (confirmed empirically: ~1.9x variance ratio
    # between factor=1.3 and factor=1.8 at radius=10).
    assert variance(wide) > variance(tight) * 1.3
