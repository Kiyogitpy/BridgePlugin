import dataclasses
import math
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller

# Captured immediately, before pytest's collection phase can import test_mcp_debug_server.py --
# that module replaces controller.make_udp_listener with a MagicMock at its own import time (to
# avoid a real socket bind for its whole test session) and never restores it, since it isn't
# wrapped in monkeypatch. Python import order in this directory is alphabetical, so this line
# runs first and this reference stays the real implementation regardless of collection order.
_REAL_MAKE_UDP_LISTENER = controller.make_udp_listener


@pytest.fixture(autouse=True)
def _reset_human_related_global_state():
    rng_state = controller.random.getstate()
    profile_before = controller._human_profile
    yield
    controller.random.setstate(rng_state)
    controller._human_profile = profile_before


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


# ---- panic listener (F1), 2026-08-27 ----
# Rebuilt to match the corrected pause listener's shape (user direction: "make sure they are
# built the same as pause but just exits instead of pauses") -- a single dedicated daemon
# thread blocking on keyboard.wait, replacing the old add_hotkey+wait-loop double registration
# (harmless there only because os._exit is idempotent-ish; still an inconsistent shape once
# pause could no longer use it -- see _pause_watchdog's own note).

class _StopPanicWatchdogLoop(Exception):
    """Breaks _panic_watchdog's otherwise-infinite loop deterministically inside a test."""


def test_panic_exit_beeps_before_exiting(monkeypatch):
    order = []
    monkeypatch.setattr(controller.winsound, "Beep",
                        lambda freq, dur: order.append(("beep", freq, dur)))
    monkeypatch.setattr(controller.os, "_exit", lambda code: order.append(("exit", code)))

    controller.panic_exit()

    assert order == [("beep", controller.PANIC_BEEP_HZ, controller.PANIC_BEEP_DURATION_MS),
                      ("exit", 0)]


def test_panic_beep_is_deeper_than_either_pause_tone():
    """A distinct 'something serious happened' tone, not just a third shade of pause/resume."""
    assert controller.PANIC_BEEP_HZ < controller.PAUSE_BEEP_HZ
    assert controller.PANIC_BEEP_HZ < controller.RESUME_BEEP_HZ


def test_panic_watchdog_exits_each_time_the_hotkey_fires(monkeypatch):
    wait_calls = {"n": 0}
    exits = []

    def fake_wait(key):
        wait_calls["n"] += 1
        if wait_calls["n"] > 2:
            raise _StopPanicWatchdogLoop()

    monkeypatch.setattr(controller.keyboard, "wait", fake_wait)
    monkeypatch.setattr(controller, "panic_exit", lambda: exits.append(1))

    with pytest.raises(_StopPanicWatchdogLoop):
        controller._panic_watchdog()

    assert wait_calls["n"] == 3
    assert exits == [1, 1]


def test_panic_watchdog_waits_on_the_f1_key(monkeypatch):
    seen = []

    def fake_wait(key):
        seen.append(key)
        raise _StopPanicWatchdogLoop()

    monkeypatch.setattr(controller.keyboard, "wait", fake_wait)

    with pytest.raises(_StopPanicWatchdogLoop):
        controller._panic_watchdog()

    assert seen == ["f1"]


def test_start_panic_listener_runs_the_watchdog_on_its_own_daemon_thread(monkeypatch):
    started = {}

    class _FakeThread:
        def __init__(self, target, daemon):
            started["target"] = target
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(controller.threading, "Thread", _FakeThread)

    controller.start_panic_listener()

    assert started["daemon"] is True
    assert started["started"] is True
    assert callable(started["target"])


def test_start_panic_listener_does_not_also_register_add_hotkey(monkeypatch):
    """The old shape double-registered (add_hotkey AND a wait-loop thread, both wired to
    panic_exit) -- harmless for an idempotent hard exit, but not the single-mechanism shape
    pause needs, and no longer what panic should do either now that they're meant to match."""
    monkeypatch.setattr(controller.threading, "Thread", lambda target, daemon: type(
        "T", (), {"start": lambda self: None})())
    calls = []
    monkeypatch.setattr(controller.keyboard, "add_hotkey",
                        lambda key, fn: calls.append(key))

    controller.start_panic_listener()

    assert calls == []


def test_toggle_pause_beeps_when_pausing(monkeypatch):
    beeps = []
    monkeypatch.setattr(controller.winsound, "Beep", lambda freq, dur: beeps.append((freq, dur)))
    state = controller.ControllerState()

    controller.toggle_pause(state)  # False -> True (paused)

    assert len(beeps) == 1


def test_toggle_pause_beeps_when_resuming(monkeypatch):
    beeps = []
    monkeypatch.setattr(controller.winsound, "Beep", lambda freq, dur: beeps.append((freq, dur)))
    state = controller.ControllerState()
    state.paused = True

    controller.toggle_pause(state)  # True -> False (resumed)

    assert len(beeps) == 1


def test_toggle_pause_uses_a_different_tone_for_pause_vs_resume(monkeypatch):
    """Audible feedback for a hotkey the user can't visually confirm without alt-tabbing --
    distinct tones so pause vs resume is tellable by ear alone, not just that SOMETHING fired."""
    beeps = []
    monkeypatch.setattr(controller.winsound, "Beep", lambda freq, dur: beeps.append(freq))
    state = controller.ControllerState()

    controller.toggle_pause(state)  # pause
    controller.toggle_pause(state)  # resume

    assert beeps[0] != beeps[1]


# ---- pause listener (F2), 2026-08-27 ----
# CONFIRMED report: a bare keyboard.add_hotkey('f2', ...) callback (the old implementation)
# stopped firing once RuneLite, not the terminal, had focus. start_panic_listener's (F1) own
# dedicated thread running a keyboard.wait loop was the one confirmed to keep working
# focus-independently -- start_pause_listener now mirrors that instead of add_hotkey.

class _StopWatchdogLoop(Exception):
    """Breaks _pause_watchdog's otherwise-infinite loop deterministically inside a test."""


def test_pause_watchdog_toggles_once_per_hotkey_press(monkeypatch):
    wait_calls = {"n": 0}
    toggled = []

    def fake_wait(key):
        wait_calls["n"] += 1
        if wait_calls["n"] > 3:
            raise _StopWatchdogLoop()

    monkeypatch.setattr(controller.keyboard, "wait", fake_wait)
    monkeypatch.setattr(controller, "toggle_pause", lambda state: toggled.append(state))
    state = controller.ControllerState()

    with pytest.raises(_StopWatchdogLoop):
        controller._pause_watchdog(state)

    assert wait_calls["n"] == 4
    assert toggled == [state, state, state]  # one toggle per of the first 3 presses


def test_pause_watchdog_waits_on_the_f2_key(monkeypatch):
    seen = []

    def fake_wait(key):
        seen.append(key)
        raise _StopWatchdogLoop()

    monkeypatch.setattr(controller.keyboard, "wait", fake_wait)
    state = controller.ControllerState()

    with pytest.raises(_StopWatchdogLoop):
        controller._pause_watchdog(state)

    assert seen == ["f2"]


def test_start_pause_listener_runs_the_watchdog_on_its_own_daemon_thread(monkeypatch):
    started = {}

    class _FakeThread:
        def __init__(self, target, daemon):
            started["target"] = target
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(controller.threading, "Thread", _FakeThread)
    state = controller.ControllerState()

    controller.start_pause_listener(state)

    assert started["daemon"] is True
    assert started["started"] is True
    assert callable(started["target"])


# ---- move_to_human's refresh_legs (multi-leg tracking) additions, 2026-08-14 ----
# Pure-logic coverage only -- live mouse movement genuinely can't be unit tested, but
# _move_to_human_tracking's leg/refresh bookkeeping is cheap to verify with the real cursor and
# serial calls monkeypatched out. aim_radius=0 is used throughout to get deterministic zero
# jitter (pick_aim_point's own "radius <= 0 -> return x, y unchanged" shortcut), instead of
# needing to monkeypatch pick_aim_point itself.

def test_move_to_human_without_refresh_legs_never_uses_tracking(monkeypatch):
    """Regression guard for the "purely additive" claim: refresh_legs=None (the default for
    every pre-existing caller) must never reach _move_to_human_tracking at all."""
    # Pins the v1 engine explicitly: HUMAN_MOVE_V2 defaults ON since 2026-08-26, and this
    # test is about the v1 dispatch path specifically.
    monkeypatch.setattr(controller, "HUMAN_MOVE_V2", False)
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

    def fake_exact(ser, wx, wy, *, fps, duration_scale, **kw):
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
                         lambda ser, wx, wy, *, fps, duration_scale, **kw: exact_calls.append((wx, wy)))

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
        lambda ser, wx, wy, *, fps, duration_scale, **kw: positions.append((wx, wy)),
    )
    refresh_calls = []

    def fake_refresh():
        refresh_calls.append(1)
        return None  # target vanished from the very first re-aim attempt

    controller._move_to_human_tracking(None, (0, 0), 100, 100, fps=190, aim_radius=0,
                                        duration_scale=1.0, refresh_target=fake_refresh, legs=4)

    assert len(refresh_calls) == 1  # tried once (leg 1), not again for legs 2/3


# ---- per-leg duration floor, 2026-08-24 ----
# MEASURED (bench_hover.py, Dashing kebbit anchor): estimate_duration_ms floors every hop at
# MOVE_MIN_DURATION_MS, and _move_to_human_tracking's waypoints are *fractions* of the remaining
# distance, so every leg is short enough to pay that floor in full. refresh_legs=6 measured a
# 1380ms median flight -- 6 * 200ms of floor plus delays -- to cover ~250px that a single hop
# covers in ~242ms. A mid-flight tracking leg is part of one continuous gesture, not a discrete
# click movement, so it gets the lower TRACKING_LEG_MIN_DURATION_MS floor instead.

def test_estimate_duration_ms_honors_a_custom_floor():
    """The floor must be a parameter, not a constant baked into the clamp.

    Note what the low floor actually buys: the duration model's own intercept (90 + 0.55*dist,
    then **0.92 * 1.9) never produces less than ~105ms however short the hop, so a 60ms floor is
    inert as a floor. Its real effect is removing the 200ms *clamp*, letting a short leg fall to
    its natural duration (~150ms for the ~42px legs a 6-leg flight actually uses)."""
    assert controller.estimate_duration_ms(5) == controller.MOVE_MIN_DURATION_MS
    # Raising the floor is the deterministic direction, and proves the parameter is wired.
    assert controller.estimate_duration_ms(5, min_ms=1000) == 1000
    # Lowering it lets the natural value through instead of clamping up to 200.
    assert controller.TRACKING_LEG_MIN_DURATION_MS <= controller.estimate_duration_ms(
        5, min_ms=controller.TRACKING_LEG_MIN_DURATION_MS) < controller.MOVE_MIN_DURATION_MS
    # The ceiling and the distance model itself must be untouched by the new parameter.
    assert controller.estimate_duration_ms(5000, min_ms=60) == 1800


def test_move_to_human_tracking_uses_reduced_floor_for_non_final_legs(monkeypatch):
    """Non-final legs are mid-gesture corrections and take the reduced floor; the final leg is
    the visible settling approach onto the target and keeps the full one."""
    positions = [(0, 0)]
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: positions[-1])
    floors = []

    def fake_exact(ser, wx, wy, *, fps, duration_scale, min_duration_ms):
        floors.append(min_duration_ms)
        positions.append((wx, wy))

    monkeypatch.setattr(controller, "_move_to_human_exact", fake_exact)

    controller._move_to_human_tracking(None, (0, 0), 400, 400, fps=190, aim_radius=0,
                                        duration_scale=1.0, refresh_target=lambda: (400, 400),
                                        legs=4)

    assert floors[:-1] == [controller.TRACKING_LEG_MIN_DURATION_MS] * 3
    assert floors[-1] == controller.MOVE_MIN_DURATION_MS


def test_move_to_human_non_tracking_paths_keep_the_full_floor(monkeypatch):
    """Regression guard: the single-checkpoint and plain paths are unchanged, so every existing
    caller's click still moves at exactly the speed it did before."""
    # Pins the v1 engine explicitly: HUMAN_MOVE_V2 defaults ON since 2026-08-26, and this
    # test is about the v1 dispatch path specifically.
    monkeypatch.setattr(controller, "HUMAN_MOVE_V2", False)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    monkeypatch.setattr(controller, "maybe_overshoot_point", lambda *a, **k: None)
    floors = []
    # Defaulted, not required: these paths deliberately don't pass the kwarg at all, so what's
    # asserted is the EFFECTIVE floor they run at.
    monkeypatch.setattr(
        controller, "_move_to_human_exact",
        lambda ser, wx, wy, *, fps, duration_scale,
        min_duration_ms=controller.MOVE_MIN_DURATION_MS: floors.append(min_duration_ms),
    )

    controller.move_to_human(None, 500, 500)  # plain path, no refresh at all
    controller.move_to_human(None, 500, 500, refresh_target=lambda: (500, 500))  # midflight path

    assert floors == [controller.MOVE_MIN_DURATION_MS] * 3


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


def test_sample_human_delay_never_goes_below_min_s():
    # The regression this test guards against: several real call sites' min_s is a documented
    # fix for a real bug (e.g. banker.py's search_bank), not an aesthetic minimum -- violating
    # it is a correctness bug, not just a humanization-quality nitpick.
    controller.random.seed(1)
    for min_s, max_s in [(0.7, 0.9), (0.5, 0.7), (0.6, 1.0), (3.0, 3.6), (0.2, 10.0), (0.0005, 0.0015)]:
        samples = [controller.sample_human_delay(min_s, max_s) for _ in range(2000)]
        assert min(samples) >= min_s, f"violated floor for ({min_s}, {max_s}): got {min(samples)}"


def test_sample_human_delay_has_no_point_mass_at_the_floor():
    # Guards against reverting to a clamp (max(draw, floor)), which produces a spike of
    # bit-for-bit identical values at the floor -- a hard-edge artifact.
    controller.random.seed(2)
    samples = [controller.sample_human_delay(0.7, 0.9) for _ in range(5000)]
    from collections import Counter
    most_common_count = Counter(samples).most_common(1)[0][1]
    assert most_common_count <= 2  # no meaningful repeated-value spike


def test_sample_human_delay_has_no_hard_ceiling_unlike_uniform():
    controller.random.seed(3)
    samples = [controller.sample_human_delay(1.0, 2.0) for _ in range(10000)]
    over_ceiling = sum(1 for s in samples if s > 2.0)
    assert over_ceiling > 50  # meaningfully non-zero; uniform(1,2) would give exactly 0


def test_sample_human_delay_median_tracks_the_range():
    controller.random.seed(4)
    samples = sorted(controller.sample_human_delay(1.0, 2.0) for _ in range(10000))
    median = samples[len(samples) // 2]
    assert 1.0 < median < 2.0


def test_sample_human_delay_scales_spread_with_range_width():
    # The core fix this branch's review required: sigma must scale with the range's own
    # log-width, not be a fixed relative spread -- otherwise a narrow tick-critical range gets
    # the same spread as a wide, deliberately-erratic one.
    controller.random.seed(5)
    narrow = [controller.sample_human_delay(0.7, 0.9) for _ in range(5000)]
    controller.random.seed(5)
    wide = [controller.sample_human_delay(0.2, 10.0) for _ in range(5000)]

    def spread(samples):
        s = sorted(samples)
        return s[int(len(s) * 0.9)] - s[int(len(s) * 0.1)]  # p90 - p10

    assert spread(wide) > spread(narrow) * 5


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


def test_build_bezier_path_curve_magnitude_follows_profile_range(monkeypatch):
    def make_profile(low, high):
        return controller.HumanProfile(
            aim_sigma_factor=1.5, curve_mag_low=low, curve_mag_high=high,
            delay_skew=0.5, rerolled_at=0.0, reroll_after_s=1e9,
        )

    def mean_c1_offset(samples):
        return sum(samples) / len(samples)

    start, end = (0, 0), (400, 0)

    controller.random.seed(2)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(0.08, 0.08))
    low_offsets = []
    for _ in range(2000):
        bez = controller.build_bezier_path(start, end)
        # c1 sits at c1_t along the straight line, plus a perpendicular offset --
        # isolate that offset by comparing against the line's own y at that x.
        low_offsets.append(abs(bez.p1[1]))

    controller.random.seed(2)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(0.20, 0.20))
    high_offsets = []
    for _ in range(2000):
        bez = controller.build_bezier_path(start, end)
        high_offsets.append(abs(bez.p1[1]))

    # Same seed sequence, only the profile's curve_mag range differs -- pinning
    # it to 0.20 instead of 0.08 should produce a visibly larger average
    # perpendicular offset (confirmed empirically: ~2.5x on a 400px move).
    assert mean_c1_offset(high_offsets) > mean_c1_offset(low_offsets) * 1.5


# ---- ControllerState.break_due / the before_break hook, 2026-08-20 ----

BREAKS_ON = {"enabled": True, "everyMinMinutes": 45, "everyMaxMinutes": 75}


def test_break_due_is_false_before_the_first_break_is_even_scheduled():
    """maybe_toggle_break's first call only SCHEDULES -- asking before that must not report a
    break as due, or a routine would tear down its work on the very first tick."""
    state = controller.ControllerState()
    assert state.break_due(BREAKS_ON) is False


def test_break_due_is_false_while_still_counting_down():
    state = controller.ControllerState()
    state.maybe_toggle_break(BREAKS_ON)   # schedules next_break_at some 45-75 minutes out
    assert state.next_break_at is not None
    assert state.break_due(BREAKS_ON) is False


def test_break_due_goes_true_exactly_when_the_break_would_start():
    state = controller.ControllerState()
    state.maybe_toggle_break(BREAKS_ON)
    state.next_break_at = controller.time.time() - 1
    assert state.break_due(BREAKS_ON) is True


def test_break_due_never_reports_due_while_already_on_a_break():
    """before_break must fire once per break, not repeatedly for its whole duration."""
    state = controller.ControllerState()
    state.maybe_toggle_break(BREAKS_ON)
    state.next_break_at = controller.time.time() - 1
    state.maybe_toggle_break(BREAKS_ON)   # actually starts it
    assert state.on_break() is True
    assert state.break_due(BREAKS_ON) is False


def test_break_due_is_false_when_breaks_are_disabled():
    state = controller.ControllerState()
    state.next_break_at = controller.time.time() - 1
    assert state.break_due({"enabled": False}) is False


def test_break_due_has_no_side_effects():
    """It is a predicate main() asks BEFORE maybe_toggle_break -- if it scheduled, started or
    cleared anything, asking the question would change the answer."""
    state = controller.ControllerState()
    state.maybe_toggle_break(BREAKS_ON)
    before = (state.next_break_at, state.break_until)
    for _ in range(5):
        state.break_due(BREAKS_ON)
    assert (state.next_break_at, state.break_until) == before


# ---- click_menu_action's option-list form, 2026-08-20 ----

def _menu(*options):
    return {"open": True, "entries": [
        {"option": option, "target": "Box trap", "screenX": 10, "screenY": 20 + i}
        for i, option in enumerate(options)
    ]}


def _stub_menu(monkeypatch, menu, picked):
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_menu", lambda: menu)
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **k: picked.append((x, y)))
    monkeypatch.setattr(controller, "click_human", lambda ser: None)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))


def test_click_menu_action_still_takes_a_plain_string(monkeypatch):
    picked = []
    _stub_menu(monkeypatch, _menu("Examine", "Check"), picked)
    assert controller.click_menu_action(None, {"screenX": 1, "screenY": 1}, "Check") is True
    assert picked == [(10, 21)]


def test_click_menu_action_takes_options_in_priority_order(monkeypatch):
    """First option wins even though the menu lists the second one first -- the caller's stated
    preference, not menu order, decides (a full box trap must be Checked, never Dismantled)."""
    picked = []
    _stub_menu(monkeypatch, _menu("Dismantle", "Check"), picked)
    assert controller.click_menu_action(None, {"screenX": 1, "screenY": 1},
                                         ("Check", "Dismantle")) is True
    assert picked == [(10, 21)], "should have picked Check, the second entry"


def test_click_menu_action_falls_through_to_a_later_option(monkeypatch):
    picked = []
    _stub_menu(monkeypatch, _menu("Examine", "Dismantle"), picked)
    assert controller.click_menu_action(None, {"screenX": 1, "screenY": 1},
                                         ("Check", "Dismantle")) is True
    assert picked == [(10, 21)]


def test_click_menu_action_returns_false_when_no_option_matches(monkeypatch):
    picked = []
    _stub_menu(monkeypatch, _menu("Examine", "Walk here"), picked)
    assert controller.click_menu_action(None, {"screenX": 1, "screenY": 1},
                                         ("Check", "Dismantle")) is False


def test_an_over_narrow_target_hint_degrades_instead_of_failing(monkeypatch):
    """A wrong target_hint should cost precision, not the whole interaction -- the box trap
    object's displayed name is one of the things this codebase could not confirm offline."""
    picked = []
    _stub_menu(monkeypatch, _menu("Check"), picked)
    assert controller.click_menu_action(None, {"screenX": 1, "screenY": 1}, "Check",
                                         target_hint="Cage trap") is True
    assert picked == [(10, 20)]


def test_a_matching_target_hint_still_narrows_the_choice(monkeypatch):
    picked = []
    menu = {"open": True, "entries": [
        {"option": "Take", "target": "Bones", "screenX": 10, "screenY": 20},
        {"option": "Take", "target": "Box trap", "screenX": 10, "screenY": 30},
    ]}
    _stub_menu(monkeypatch, menu, picked)
    assert controller.click_menu_action(None, {"screenX": 1, "screenY": 1}, "Take",
                                         target_hint="box trap") is True
    assert picked == [(10, 30)]


def test_an_exact_option_still_beats_a_substring_one(monkeypatch):
    """The documented "Examine" contains "mine" trap -- pinned again now that matching runs
    through the multi-option scanner."""
    picked = []
    _stub_menu(monkeypatch, _menu("Examine", "Mine"), picked)
    assert controller.click_menu_action(None, {"screenX": 1, "screenY": 1}, "Mine") is True
    assert picked == [(10, 21)], "must land on Mine, not on Examine"


def test_make_udp_listener_bumps_the_receive_buffer_past_the_windows_default():
    """I5: this branch's spawn-event budget (ControllerPlugin.SPAWN_EVENT_CAP_PER_TICK) can put
    up to 40 OBJECT_/GROUND_ITEM_ packets on the socket per tick, and routines that never opted
    into ctx.events -- or that block for seconds between pump() calls -- only drain it once per
    main-loop iteration. Windows' 64KB default can overflow and the kernel drops silently, and a
    dropped packet is not necessarily a harmless spawn: PLAYER_DIED shares the same socket."""
    s = _REAL_MAKE_UDP_LISTENER(port=0)   # port 0 -- OS picks a free one, no live conflict
    try:
        actual = s.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        assert actual >= (1 << 20)
    finally:
        s.close()


# ---- npc_index_refresh_target deadband ----

def test_npc_refresh_ignores_animation_but_follows_real_movement(monkeypatch):
    """NPCProvider aims at Actor.getConvexHull(), recomputed every frame, so an animating
    monster's reported point moves while the monster does not. MEASURED live 2026-08-27 on a
    Gargoyle that never changed tile: p50 7.2px, p90 35.2px, max 55.4px of jitter -- over
    HM2_REPLAN_MIN_DRIFT_PX on most polls, which made move() replan as it arrived.

    Re-aiming at a hull is right; re-aiming at ANIMATION is not.
    """
    frames = [
        {"found": True, "x": 10, "y": 20, "plane": 0, "screenX": 100, "screenY": 200},
        {"found": True, "x": 10, "y": 20, "plane": 0, "screenX": 148, "screenY": 236},  # animating
        {"found": True, "x": 10, "y": 20, "plane": 0, "screenX": 91,  "screenY": 173},  # animating
        {"found": True, "x": 11, "y": 20, "plane": 0, "screenX": 160, "screenY": 205},  # WALKED
        {"found": True, "x": 11, "y": 20, "plane": 0, "screenX": 205, "screenY": 250},  # animating
    ]
    monkeypatch.setattr(controller, "get_npc_by_index", lambda idx: frames.pop(0))
    refresh = controller.npc_index_refresh_target(7)

    assert refresh() == (100, 200)
    assert refresh() == (100, 200)      # held: same tile, only the model moved
    assert refresh() == (100, 200)
    assert refresh() == (160, 205)      # tile changed -- a human would look again
    assert refresh() == (160, 205)


def test_npc_refresh_reports_a_lost_target_as_none(monkeypatch):
    """A target that dies or despawns must still read as gone, not as its last held point --
    move()'s target_lost path depends on it."""
    monkeypatch.setattr(controller, "get_npc_by_index", lambda idx: None)
    assert controller.npc_index_refresh_target(7)() is None


def test_npc_refresh_state_is_per_factory(monkeypatch):
    """The held point lives in the closure, so one factory is one click's flight. Callers build
    them inline at the call site; sharing one would hold a stale point across clicks."""
    monkeypatch.setattr(controller, "get_npc_by_index",
                        lambda idx: {"found": True, "x": 1, "y": 2, "plane": 0,
                                     "screenX": 50, "screenY": 60})
    a = controller.npc_index_refresh_target(1)
    assert a() == (50, 60)
    monkeypatch.setattr(controller, "get_npc_by_index",
                        lambda idx: {"found": True, "x": 1, "y": 2, "plane": 0,
                                     "screenX": 999, "screenY": 999})
    assert a() == (50, 60)                                    # same factory holds
    assert controller.npc_index_refresh_target(1)() == (999, 999)   # a fresh one re-resolves


def test_npc_refresh_follows_projection_while_the_player_moves(monkeypatch):
    """The tile-only deadband held a stale point whenever *we* were the thing moving: the NPC's
    tile never changes while the player walks, but its screen projection slides continuously --
    which is exactly 'clicks land behind'. With the viewer-motion fields present (playerLocal*,
    camera*), a change in OUR position must invalidate the hold."""
    base = {"found": True, "x": 10, "y": 20, "plane": 0,
            "npcLocalX": 6464, "npcLocalY": 6464,
            "cameraYaw": 512, "cameraPitch": 300, "cameraScale": 550}
    frames = [
        dict(base, playerLocalX=6000, playerLocalY=6000, screenX=100, screenY=200),
        dict(base, playerLocalX=6050, playerLocalY=6000, screenX=130, screenY=210),  # we walked
        dict(base, playerLocalX=6100, playerLocalY=6000, screenX=160, screenY=220),  # still walking
    ]
    monkeypatch.setattr(controller, "get_npc_by_index", lambda idx: frames.pop(0))
    refresh = controller.npc_index_refresh_target(7)
    assert refresh() == (100, 200)
    assert refresh() == (130, 210)
    assert refresh() == (160, 220)


def test_npc_refresh_still_holds_through_animation_when_nothing_moves(monkeypatch):
    """When every motion source is still (npc tile+local, player local, camera), hull-animation
    jitter in the reported point must still be held -- the original 2026-08-27 fix."""
    base = {"found": True, "x": 10, "y": 20, "plane": 0,
            "npcLocalX": 6464, "npcLocalY": 6464,
            "playerLocalX": 6000, "playerLocalY": 6000,
            "cameraYaw": 512, "cameraPitch": 300, "cameraScale": 550}
    frames = [
        dict(base, screenX=100, screenY=200),
        dict(base, screenX=148, screenY=236),   # hull animating
        dict(base, screenX=91, screenY=173),    # hull animating
    ]
    monkeypatch.setattr(controller, "get_npc_by_index", lambda idx: frames.pop(0))
    refresh = controller.npc_index_refresh_target(7)
    assert refresh() == (100, 200)
    assert refresh() == (100, 200)
    assert refresh() == (100, 200)


def test_npc_refresh_follows_camera_rotation(monkeypatch):
    """Camera motion shifts every projection with neither tile changing -- must re-resolve."""
    base = {"found": True, "x": 10, "y": 20, "plane": 0,
            "npcLocalX": 6464, "npcLocalY": 6464,
            "playerLocalX": 6000, "playerLocalY": 6000,
            "cameraPitch": 300, "cameraScale": 550}
    frames = [
        dict(base, cameraYaw=512, screenX=100, screenY=200),
        dict(base, cameraYaw=700, screenX=300, screenY=200),
    ]
    monkeypatch.setattr(controller, "get_npc_by_index", lambda idx: frames.pop(0))
    refresh = controller.npc_index_refresh_target(7)
    assert refresh() == (100, 200)
    assert refresh() == (300, 200)


# ---- pre-click settle + hover verification ----

def _click_entity_harness(monkeypatch, cursor=(100, 100)):
    """Common mocks for exercising click_entity without serial/bridge. Returns (moves, clicks)."""
    moves, clicks = [], []
    monkeypatch.setattr(controller, "move_to_human", lambda ser, x, y, **k: moves.append((x, y)))
    monkeypatch.setattr(controller, "click_human", lambda ser: clicks.append(1))
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: cursor)
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: True)
    controller._movement_abort.clear()
    return moves, clicks


def test_click_entity_settles_before_click_when_target_drifted(monkeypatch):
    """The terminal blind window: after move_to_human returns, nothing used to re-check the
    target before the mouse-down. While the player walks, that last ~100-300ms is easily tens
    of px of drift -- so click_entity now takes one more look and settles if it moved."""
    moves, clicks = _click_entity_harness(monkeypatch)
    entity = {"name": "Goblin", "id": 1, "screenX": 100, "screenY": 100}
    ok = controller.click_entity(None, entity, offset=(0, 0), simulate_mistakes=False,
                                 check_ui_blockers=False,
                                 refresh_target=lambda: (150, 100))
    assert ok
    assert len(moves) == 2                 # main flight + one settle
    assert moves[-1] == (150, 100)         # settle re-aimed at the refreshed position
    assert clicks == [1]


def test_click_entity_skips_settle_when_cursor_already_on_target(monkeypatch):
    moves, clicks = _click_entity_harness(monkeypatch)
    entity = {"name": "Goblin", "id": 1, "screenX": 100, "screenY": 100}
    ok = controller.click_entity(None, entity, offset=(0, 0), simulate_mistakes=False,
                                 check_ui_blockers=False,
                                 refresh_target=lambda: (104, 103))
    assert ok
    assert len(moves) == 1                 # within tolerance: no extra motion
    assert clicks == [1]


def test_click_entity_settle_tolerates_lost_target(monkeypatch):
    """refresh_target returning None mid-settle = target died/despawned; click the last-known
    point anyway, same as move()'s own target_lost path."""
    moves, clicks = _click_entity_harness(monkeypatch)
    entity = {"name": "Goblin", "id": 1, "screenX": 100, "screenY": 100}
    ok = controller.click_entity(None, entity, offset=(0, 0), simulate_mistakes=False,
                                 check_ui_blockers=False,
                                 refresh_target=lambda: None)
    assert ok
    assert len(moves) == 1
    assert clicks == [1]


def test_hover_matches_accepts_case_insensitive_substring(monkeypatch):
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"menuOpen": False, "option": "Attack",
                                 "target": "Gargoyle (level-111)"})
    assert controller.hover_matches(expected_action="attack", expected_target="gargoyle")


def test_hover_matches_blocks_wrong_default_action(monkeypatch):
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"menuOpen": False, "option": "Walk here", "target": ""})
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    assert not controller.hover_matches(expected_action="Attack", expected_target="Gargoyle")


def test_hover_matches_never_blocks_without_the_endpoint(monkeypatch):
    """An un-rebuilt client has no /hover (api_get returns None) -- verification must degrade
    to the old click-and-check-last-action behavior, not withhold every click."""
    monkeypatch.setattr(controller, "get_hover", lambda: None)
    assert controller.hover_matches(expected_action="Attack")


def test_hover_matches_ignores_an_open_menu(monkeypatch):
    """With a real right-click menu open, left-click semantics are the menu's, not the world's."""
    monkeypatch.setattr(controller, "get_hover", lambda: {"menuOpen": True})
    assert controller.hover_matches(expected_action="Attack")


def test_click_entity_withholds_click_on_persistent_hover_mismatch(monkeypatch):
    """Verify-then-click: when the hover text persistently says the left-click would do the
    wrong thing (someone camped on the pixel, target slipped away), do NOT click -- return False
    so the caller retries, instead of burning the click and discovering via /last-action."""
    moves, clicks = _click_entity_harness(monkeypatch)
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"menuOpen": False, "option": "Walk here", "target": ""})
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    entity = {"name": "Gargoyle", "id": 1, "screenX": 100, "screenY": 100}
    ok = controller.click_entity(None, entity, offset=(0, 0), simulate_mistakes=False,
                                 check_ui_blockers=False,
                                 refresh_target=lambda: (100, 100),
                                 expected_action="Attack")
    assert not ok
    assert clicks == []                    # the wrong click was never sent
    # The withheld case returns the CLICK_WITHHELD sentinel, not plain False, so a caller with
    # an escalation path (golem_crafting's tiers) can tell "pixel contested" from "aim failed"
    # -- while every plain truthiness check keeps working.
    assert getattr(ok, "withheld", False)


def test_get_active_tab_returns_int_from_widget_endpoint(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, params=None, timeout=0.3: {"tab": 5})
    assert controller.get_active_tab() == 5


def test_get_active_tab_returns_none_when_endpoint_missing(monkeypatch):
    """An un-rebuilt client has no /widget/active-tab (api_get returns None) -- callers must
    treat that as 'can't verify', not 'wrong tab'."""
    monkeypatch.setattr(controller, "api_get", lambda path, params=None, timeout=0.3: None)
    assert controller.get_active_tab() is None


def test_ensure_ui_state_returns_true_without_acting_when_already_in_state(monkeypatch):
    acted = []
    ok = controller.ensure_ui_state(lambda: acted.append(1), lambda: True)
    assert ok
    assert acted == []


def test_ensure_ui_state_retries_action_until_check_passes(monkeypatch):
    """The regression case this exists for: a single unconfirmed tap/click used to be trusted as
    success outright. Here the check only starts passing after the second action, so a
    single-attempt caller would have wrongly reported success."""
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    calls = []

    def action():
        calls.append(1)

    def check():
        return len(calls) >= 2

    ok = controller.ensure_ui_state(action, check, tries=3)
    assert ok
    assert len(calls) == 2


def test_ensure_ui_state_gives_up_after_tries_exhausted(monkeypatch):
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    calls = []
    ok = controller.ensure_ui_state(lambda: calls.append(1), lambda: False, tries=3)
    assert not ok
    assert len(calls) == 3


def test_ensure_ui_state_degrades_when_check_cannot_verify(monkeypatch):
    """check_fn returning None means the signal doesn't exist (un-rebuilt client) -- must not
    block forever on a feature that isn't there, same degrade-gracefully stance as
    hover_matches. Fires the action once and trusts it, same as the old unverified behavior."""
    calls = []
    ok = controller.ensure_ui_state(lambda: calls.append(1), lambda: None, tries=3)
    assert ok
    assert len(calls) == 1


def test_open_inventory_tab_skips_tap_when_already_open(monkeypatch):
    monkeypatch.setattr(controller, "get_state", lambda: {"inventoryTabOpen": True})
    taps = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: taps.append(key))
    controller.open_inventory_tab(None)
    assert taps == []


def test_open_inventory_tab_retries_until_the_tab_actually_opens(monkeypatch):
    """CONFIRMED live bug (golem_crafting.py before this fix): a blind key_tap('w') with no
    verification at all -- a dropped keypress left the routine clicking inventory slots on
    whatever tab was actually showing. open_inventory_tab must now keep tapping until
    inventoryTabOpen actually flips, not just once."""
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    state = {"open": False, "taps": 0}

    def fake_key_tap(ser, key):
        state["taps"] += 1
        if state["taps"] >= 2:
            state["open"] = True

    monkeypatch.setattr(controller, "key_tap", fake_key_tap)
    monkeypatch.setattr(controller, "get_state", lambda: {"inventoryTabOpen": state["open"]})

    ok = controller.open_inventory_tab(None)
    assert ok
    assert state["taps"] == 2


def test_open_prayer_tab_retries_until_the_tab_actually_opens(monkeypatch):
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    state = {"open": False, "taps": 0}

    def fake_key_tap(ser, key):
        state["taps"] += 1
        if state["taps"] >= 2:
            state["open"] = True

    monkeypatch.setattr(controller, "key_tap", fake_key_tap)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": state["open"]})

    ok = controller.open_prayer_tab(None)
    assert ok
    assert state["taps"] == 2


def test_click_widget_returns_false_when_not_clickable(monkeypatch):
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"found": True, "clickable": False})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1))
    assert not controller.click_widget(None, 149, 0)
    assert clicked == []


def test_click_widget_without_verify_fn_clicks_once_and_returns_true(monkeypatch):
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None:
                        {"found": True, "clickable": True, "screenX": 10, "screenY": 10})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1))
    assert controller.click_widget(None, 149, 0)
    assert clicked == [1]


def test_click_widget_verifies_and_retries_via_verify_fn(monkeypatch):
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None:
                        {"found": True, "clickable": True, "screenX": 10, "screenY": 10})
    state = {"clicks": 0, "done": False}

    def fake_click_entity(ser, entity, **kw):
        state["clicks"] += 1
        if state["clicks"] >= 2:
            state["done"] = True

    monkeypatch.setattr(controller, "click_entity", fake_click_entity)

    ok = controller.click_widget(None, 149, 0, verify_fn=lambda: state["done"])
    assert ok
    assert state["clicks"] == 2


def test_human_delay_without_serial_is_a_plain_sleep(monkeypatch):
    """Nothing that imports controller without owning the Arduino (tests, analysis tools) may
    grow fidget behavior -- unregistered means exactly the old single sleep."""
    slept = []
    monkeypatch.setattr(controller.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 5.0)
    monkeypatch.setattr(controller, "_fidget_ser", None)
    controller.human_delay(1.0, 6.0)
    assert slept == [5.0]


def test_long_waits_are_split_into_human_length_still_segments(monkeypatch):
    """MEASURED: the human never sits still >4.5s during active play; a 9s bot wait used to be
    one 9s freeze. It must become several human-length still segments with fidgets between."""
    clock = {"t": 0.0}
    monkeypatch.setattr(controller.time, "perf_counter", lambda: clock["t"])

    slept = []

    def fake_sleep(s):
        slept.append(s)
        clock["t"] += s

    monkeypatch.setattr(controller.time, "sleep", fake_sleep)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 9.0)
    monkeypatch.setattr(controller, "_fidget_ser", object())
    fidgets = []
    monkeypatch.setattr(controller, "_maybe_fidget_during_wait", lambda: fidgets.append(1))
    controller.human_delay(2.0, 12.0)
    # never shorter than drawn -- epsilon because the split's segment draws accumulate float
    # error (observed failing at 8.999999999999998 with random draws, 2026-08-31); a real
    # shortening bug would miss by whole segments, not 2e-15s
    assert sum(slept) >= 9.0 - 1e-9
    assert max(slept) <= controller.IDLE_FIDGET_MAX_STILL_S        # no single long freeze
    assert len(slept) >= 2
    assert len(fidgets) >= 1


def _fidget_env(monkeypatch, cursor):
    """Everything _maybe_fidget_during_wait touches, with the cursor tracking its own moves."""
    import input_state

    moves = []

    def move(ser, x, y, **k):
        # The real move_to_human lands on pick_aim_point(x, y, aim_radius), not on (x, y) --
        # which is the whole point of the return leg's jitter, so the fake must do it too.
        moves.append((x, y, k.get("aim_radius")))
        cursor[0], cursor[1] = controller.pick_aim_point(x, y, k.get("aim_radius") or 0)
    monkeypatch.setattr(controller, "move_to_human", move)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (cursor[0], cursor[1]))
    monkeypatch.setattr(controller, "get_state", lambda: {"windowFocused": True})
    monkeypatch.setattr(controller, "get_menu", lambda: {"open": False})
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(controller.random, "random", lambda: 0.0)     # always fidget
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "_fidget_ser", object())
    monkeypatch.setattr(input_state, "fidget_anchor", None)
    controller._movement_abort.clear()
    return moves


def test_fidget_returns_near_its_anchor_but_not_onto_the_identical_pixel(monkeypatch):
    """MEASURED live 2026-09-06 (karambwan, recordings/2026-09-06_090950_cg-bot1): with the
    return leg at aim_radius=0 the cursor snapped to a mathematically identical pixel every
    time, so it tethered to one point -- chains of 19/17/14 consecutive out-and-backs about one
    anchor and 11 rests on the exact same pixel, against the human recording of the SAME
    activity at 10 and 3. A resting hand does not land twice on one pixel. The return leg keeps
    the cursor parked (that is the safety contract) but jitters within a few px."""
    import settings

    cursor = [500, 400]
    moves = _fidget_env(monkeypatch, cursor)
    rests = []
    for _ in range(12):
        controller._maybe_fidget_during_wait()
        rests.append(tuple(cursor))          # where the cursor actually came to rest
    assert len(moves) == 24
    for out, back in zip(moves[0::2], moves[1::2]):
        assert (out[0], out[1]) != (500, 400)                    # actually went somewhere
        assert back[2] == settings.IDLE_FIDGET_RETURN_JITTER_PX  # jittered, not pixel-exact
        assert math.dist((back[0], back[1]), (500, 400)) <= settings.IDLE_FIDGET_ANCHOR_RESET_PX
    landings = {tuple(p) for p in rests}
    assert len(landings) > 1, "every return landed on the identical pixel -- the live signature"


def test_fidget_holds_one_anchor_instead_of_random_walking_off_the_target(monkeypatch):
    """The jitter must not compound: each return aims at the REMEMBERED anchor, so a hundred
    fidgets through a long wait leave the cursor where the wait started (mixology and
    golem_crafting park it on the next click target and then click IN PLACE). The anchor is
    re-taken only when something else has moved the cursor away from it."""
    import input_state
    import settings

    cursor = [500, 400]
    _fidget_env(monkeypatch, cursor)
    for _ in range(50):
        controller._maybe_fidget_during_wait()
    assert input_state.fidget_anchor == (500, 400)
    assert math.dist(cursor, (500, 400)) <= settings.IDLE_FIDGET_ANCHOR_RESET_PX

    cursor[0], cursor[1] = 900, 700          # a real click moved the cursor
    controller._maybe_fidget_during_wait()
    assert input_state.fidget_anchor == (900, 700)


def test_fidget_skips_when_a_menu_is_open(monkeypatch):
    """Moving the cursor off an open right-click menu closes it -- a fidget mid-menu would
    break click_menu_action's read-then-select flow."""
    moves = []
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **k: moves.append((x, y)))
    monkeypatch.setattr(controller, "get_state", lambda: {"windowFocused": True})
    monkeypatch.setattr(controller, "get_menu", lambda: {"open": True})
    monkeypatch.setattr(controller.random, "random", lambda: 0.0)
    monkeypatch.setattr(controller, "_fidget_ser", object())
    controller._movement_abort.clear()
    controller._maybe_fidget_during_wait()
    assert moves == []


def test_pointer_acceleration_warning_fires_only_when_detected(monkeypatch):
    """Acceleration makes px-per-unit speed-dependent, which _px_per_unit (a single scalar)
    cannot express -- so it gets a loud startup warning. Unreadable (None) must NOT warn:
    a headless environment has no pointer to be wrong about."""
    for detected, expected in ((True, True), (False, False), (None, False)):
        monkeypatch.setattr(controller, "_windows_pointer_acceleration_enabled",
                            lambda d=detected: d)
        assert controller.warn_if_pointer_acceleration() is expected


def test_hover_matches_accepts_any_of_a_sequence_of_actions(monkeypatch):
    """State-dependent actions the caller doesn't choose (GOLEM_PLINTH_OPTIONS): any match in
    the sequence passes; a hover outside the whole set still blocks."""
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"menuOpen": False, "option": "Shape-golem", "target": "Plinth"})
    options = ("Start-golem", "Shape-golem", "Insert-core")
    assert controller.hover_matches(expected_action=options)
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"menuOpen": False, "option": "Walk here", "target": ""})
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    assert not controller.hover_matches(expected_action=options)


def test_click_entity_clicks_when_hover_confirms(monkeypatch):
    moves, clicks = _click_entity_harness(monkeypatch)
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"menuOpen": False, "option": "Attack",
                                 "target": "Gargoyle (level-111)"})
    entity = {"name": "Gargoyle", "id": 1, "screenX": 100, "screenY": 100}
    ok = controller.click_entity(None, entity, offset=(0, 0), simulate_mistakes=False,
                                 check_ui_blockers=False,
                                 refresh_target=lambda: (100, 100),
                                 expected_action="Attack", expected_target="Gargoyle")
    assert ok
    assert clicks == [1]


def test_click_menu_action_passes_refresh_target_through_to_the_right_click(monkeypatch):
    """The right-click used to be the ONE interaction with no mid-flight correction available.

    CONFIRMED live 2026-08-27: slayer's loot right-click lands ~1.0s after the item is resolved,
    and over that window a moving player plus the camera's trailing animation drift a ground
    item's small icon out from under the aim point -- three misses in one session, each reading
    back ['Cancel', 'Mark Safespot', 'Walk here'] and costing a ~7s retry.
    """
    seen = {}

    def fake_click_entity(ser, entity, **kw):
        seen.update(kw)
        return False          # stop before the menu read; only the plumbing is under test

    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    sentinel = lambda: (1, 2)
    controller.click_menu_action(None, {"screenX": 5, "screenY": 6}, "take",
                                 refresh_target=sentinel, refresh_legs=3)
    assert seen["refresh_target"] is sentinel
    assert seen["refresh_legs"] == 3
    assert seen["button"] == "right"


def test_click_menu_action_without_refresh_is_unchanged(monkeypatch):
    """Purely additive: omitting both must reproduce the previous behaviour exactly."""
    seen = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: seen.update(kw) or False)
    controller.click_menu_action(None, {"screenX": 5, "screenY": 6}, "take")
    assert seen["refresh_target"] is None
    assert seen["refresh_legs"] is None


# ---- firmware v3 protocol probe ----

class _FakeSer:
    def __init__(self, response=b""):
        self._response = response
        self.written = []

    def write(self, data):
        self.written.append(bytes(data))

    def reset_input_buffer(self):
        pass

    def readline(self):
        return self._response


def test_probe_firmware_protocol_v3():
    ser = _FakeSer(b"HB 3\n")
    assert controller.probe_firmware_protocol(ser) == 3
    assert b"V\n" in ser.written


def test_probe_firmware_protocol_legacy_times_out():
    # Pre-v3 firmware ignores unknown lines entirely; readline returns b"" on timeout.
    assert controller.probe_firmware_protocol(_FakeSer(b"")) == 2


def test_probe_firmware_protocol_garbage_is_legacy():
    assert controller.probe_firmware_protocol(_FakeSer(b"\x00\xffnoise\n")) == 2


def test_probe_firmware_protocol_never_raises():
    class Broken:
        def reset_input_buffer(self):
            raise OSError("port died")
    assert controller.probe_firmware_protocol(Broken()) == 2


def test_get_mahogany_homes_state_returns_dict(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: {"hasContract": False})
    assert controller.get_mahogany_homes_state() == {"hasContract": False}


def test_get_mahogany_homes_state_defaults_to_no_contract_on_404(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_mahogany_homes_state() == {"hasContract": False}


def test_get_mahogany_homes_config_passes_through(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: {"enabled": True})
    assert controller.get_mahogany_homes_config() == {"enabled": True}


def test_get_widgets_dump_returns_widget_list(monkeypatch):
    monkeypatch.setattr(controller, "api_get",
                         lambda path, **kw: {"widgets": [{"group": 1, "child": 2}]})
    assert controller.get_widgets_dump() == [{"group": 1, "child": 2}]


def test_get_widgets_dump_fails_safe_to_empty_list(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_widgets_dump() == []


def test_get_npc_nearest_returns_full_entity(monkeypatch):
    monkeypatch.setattr(controller, "api_get",
                         lambda path, **kw: {"found": True, "screenX": 5, "screenY": 6, "index": 9})
    result = controller.get_npc_nearest(10415)
    assert result == {"found": True, "screenX": 5, "screenY": 6, "index": 9}


def test_get_npc_nearest_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: {"found": False})
    assert controller.get_npc_nearest(10415) is None


# --- m8aq reader endpoints (/api/...) -----------------------------------------


def test_get_api_builds_the_group_reader_path(monkeypatch):
    captured = {}
    monkeypatch.setattr(controller, "api_get",
                        lambda path, **kw: captured.update(path=path) or {"charges": 3})

    assert controller.get_api("storage", "rune-pouch") == {"charges": 3}
    assert captured["path"] == "/api/storage/rune-pouch"


def test_get_api_returns_none_when_the_endpoint_is_unavailable(monkeypatch):
    """An un-rebuilt client must read as "can't verify", never as an empty container."""
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_api("player", "movement") is None


def test_get_api_passes_a_server_side_reader_error_through(monkeypatch):
    """M8aqProvider answers a throwing reader with an error dict rather than a 500, so callers
    can tell "this reader failed" from "the bridge is down" (None)."""
    monkeypatch.setattr(controller, "api_get",
                        lambda path, **kw: {"reader": "Dialogue", "error": "NullPointerException: null"})

    result = controller.get_api("ui", "dialogue")

    assert result["error"].startswith("NullPointerException")


def test_get_api_all_hits_the_aggregate_endpoint(monkeypatch):
    captured = {}
    monkeypatch.setattr(controller, "api_get",
                        lambda path, **kw: captured.update(path=path) or {"capturedTick": 7, "groups": {}})

    assert controller.get_api_all()["capturedTick"] == 7
    assert captured["path"] == "/api/all"


def test_get_api_index_fails_safe_to_empty_dict(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_api_index() == {}


def test_get_api_index_returns_the_group_listing(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: {
        "count": 66, "aggregate": "/api/all", "groups": {"Storage": ["/api/storage/rune-pouch"]},
    })

    index = controller.get_api_index()

    assert index["count"] == 66
    assert index["groups"]["Storage"] == ["/api/storage/rune-pouch"]


# --- a click is mouse activity ---------------------------------------------------------------

def test_click_human_counts_as_mouse_activity(monkeypatch):
    """USER 2026-08-30, watching a hotspot burst live: "when we flick i think the idle function
    drags back the mouse".

    `_last_mouse_activity` gates maybe_idle_mouse_move's drift and was bumped ONLY by
    move_to_human. That was harmless while every click was preceded by a move -- but
    mahogany_homes' hotspot burst now repeats a click IN PLACE (which is what the human's 0.20s
    median is), and those clicks moved nothing. So mid-work the engine believed the mouse had
    been still for seconds and inserted a slow idle drift, dragging the cursor off the target
    between bursts. A click is deliberate input whether or not it moved the cursor."""
    calls = []
    monkeypatch.setattr(controller, "mouse_left_down", lambda ser: calls.append("down"))
    monkeypatch.setattr(controller, "mouse_left_up", lambda ser: calls.append("up"))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (100, 100))

    import input_state
    input_state.reset()
    input_state._last_mouse_activity -= 60.0
    stale = input_state._last_mouse_activity

    controller.click_human(object())

    assert input_state._last_mouse_activity > stale,         "an in-place click must reset the idle-drift timer, or the engine drifts mid-work"


# --- idle drift must not interrupt work in flight ---------------------------------------------

class _IdleState:
    def __init__(self):
        self.next_idle_move_at = 0.0


def test_idle_drift_is_suppressed_while_an_action_is_in_flight(monkeypatch):
    """MEASURED live 2026-08-30 over one run: 14 idle drifts against 48 clicks, and 5 of the 14
    fired after under 2.5s of stillness -- BETWEEN bursts, while the routine was actively working
    on a hotspot. On screen that is the cursor wandering off target and being flown back for the
    next click. USER: "when we flick i think the idle function drags back the mouse".

    Bumping _last_mouse_activity on click_human fixed the MID-burst case, but it could not fix
    this one: waiting ~2s for a queued action to complete genuinely is stillness, and the drawn
    still-segment budget is ~1.5s. The routine knows it is mid-action though, so it says so.
    Genuine idle is untouched -- this only silences the drift while work is actually pending."""
    moved = []
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (500, 500))
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: moved.append((x, y)))

    import input_state
    input_state.reset()
    input_state._last_mouse_activity -= 60.0

    class _C:
        ser = object()

    state = _IdleState()
    controller.mark_busy(5.0)
    controller.maybe_idle_mouse_move(_C(), state)
    assert not moved, "an idle drift must not fire while an action is still in flight"

    input_state._busy_until = 0.0
    state.next_idle_move_at = 0.0
    controller.maybe_idle_mouse_move(_C(), state)
    assert moved, "genuine idle must still drift -- this is measured anti-freeze behaviour"


def test_input_state_is_shared_across_both_copies_of_controller():
    """THE POINT OF input_state.py. controller.py is loaded TWICE in a running process -- as
    `__main__` (it is the script) and as `controller` (every routine imports it) -- so a
    module-level variable there exists twice, and the engine's copy never sees a routine's
    writes. PROVEN 2026-08-30 by loading the file both ways and comparing identity.

    input_state is only ever imported by NAME, so there is exactly one of it however many times
    controller.py is loaded. This test fails the moment shared input state migrates back into
    controller.py."""
    import importlib.util, os
    import input_state

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "controller.py")
    spec = importlib.util.spec_from_file_location("controller_second_copy", path)
    second = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(second)

    assert second is not controller, "the premise: two copies of controller really do exist"
    assert second.input_state is input_state,         "both copies of controller must share ONE input_state, or the idle drift goes blind again"


def test_idle_drift_will_not_fire_inside_the_gap_between_our_own_actions(monkeypatch):
    """The fix that finally worked, after two that did not. MEASURED live 2026-08-30: drifts kept
    firing after 1.3-2.5s of stillness while the routine was actively clicking -- first because
    click_human did not count as activity, then because the state was in the WRONG COPY of
    controller, then because marking work_phase busy left _climb (and every other clicking path)
    unmarked.

    A floor in one place cannot be forgotten by a call site. 4.0s comes from the same measurement
    the feature rests on: the human is NEVER still longer than 4.5s, so a drift at 1.3s is not
    imitating them, it is out-moving them."""
    moved = []
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (500, 500))
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: moved.append((x, y)))

    import input_state
    input_state.reset()

    class _C:
        ser = object()

    state = _IdleState()
    # A gap of the size that sits between two of our own actions, with a drawn budget below it.
    state.next_idle_move_at = 1.0
    input_state._last_mouse_activity -= 2.0
    controller.maybe_idle_mouse_move(_C(), state)
    assert not moved, "2s is an ordinary gap between our own clicks, not a freeze"

    # A genuine freeze still gets filled -- that is the whole point of the feature.
    input_state._last_mouse_activity -= 10.0
    controller.maybe_idle_mouse_move(_C(), state)
    assert moved, "a real freeze must still drift"


def test_click_entity_departs_from_the_refreshed_position_not_the_stale_one(monkeypatch):
    """A refresh_target means "this target MOVES, keep up with it". Honouring it only mid-flight
    while departing toward entity["screenX"/"screenY"] -- whatever stale snapshot the caller
    happened to be holding -- is half a contract.

    MEASURED live 2026-08-30 in mahogany_homes' hotspot burst: the obj dict came from ONE
    /objects read at the top of the tick, and while the player ran, every re-aimed click set off
    toward where the hotspot had been and was only hauled forward mid-flight. USER: "as we move
    the mouse have to flick more but we flick back for some reason after we click". The flick
    back IS the departure toward the stale pixel.

    Fixed HERE rather than in the routine so no other call site can inherit it -- slayer,
    golem_crafting and travel's door click all pass refresh_target too."""
    flights = []
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: flights.append((x, y)))
    monkeypatch.setattr(controller, "click_human", lambda ser: None)
    monkeypatch.setattr(controller, "_settle_on_target_before_click", lambda ser, rt: None)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (10, 10))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    stale = {"name": "Table", "screenX": 900, "screenY": 400}

    controller.click_entity(object(), stale, offset=(0, 0),
                            refresh_target=lambda: (1180, 660),
                            simulate_mistakes=False, check_ui_blockers=False)

    assert flights, "expected a flight"
    assert flights[0] == (1180, 660), (
        f"the flight must DEPART from the live position, not the stale snapshot -- got {flights[0]}"
    )


# --- panic/pause must not depend on a single hook ---------------------------------------------

def test_async_key_watchdog_fires_the_action_once_per_press(monkeypatch):
    """USER-REPORTED 2026-08-30, mid-run: "my f1 and f2 doesnt work now hmm" -- the panic key had
    silently stopped responding while the routine was still driving the mouse.

    The `keyboard` library's wait() rides a Windows low-level hook, and Windows SILENTLY REMOVES
    such a hook when its callback overruns LowLevelHooksTimeout (default 300ms, confirmed not
    overridden on that machine). Once dropped, F1 is dead for the life of the process with no
    error anywhere. The hotspot burst had just become much busier -- a tight click loop making two
    extra HTTP calls per click -- which is exactly the GIL pressure that starves such a callback.

    GetAsyncKeyState asks the OS for the key's state directly. No hook, so nothing to lose, and
    it keeps working when the foreground window is elevated. This is the second, independent path
    panic used to have before it was unified to one on 2026-08-27 for symmetry with pause -- that
    unification removed the redundancy that would have covered this.

    Edge-triggered: the action fires on the DOWN transition only. Panic is idempotent (os._exit)
    but pause is a toggle, so a level-triggered watchdog would flip it many times per press."""
    states = iter([0, 0, 0x8000, 0x8000, 0x8000, 0, 0x8000, 0])
    fired = []
    monkeypatch.setattr(controller, "_async_key_down",
                        lambda vk: bool(next(states, 0) & 0x8000))

    stop = {"n": 0}
    def should_stop():
        stop["n"] += 1
        return stop["n"] > 8

    controller._async_key_watchdog(0x70, lambda: fired.append(1),
                                   poll_s=0, _stop=should_stop)

    assert len(fired) == 2, f"one fire per press, not per poll -- got {len(fired)}"


# --- event-driven outcomes engine plumbing (2026-09-04) ------------------------------

def test_subscribe_varbits_posts_csv_and_returns_the_bridge_answer(monkeypatch):
    seen = {}

    def fake_get(path, params=None, timeout=0.3):
        seen["path"], seen["params"] = path, params
        return {"varbits": [5667, 13691], "varps": []}
    monkeypatch.setattr(controller, "api_get", fake_get)
    controller._subscribe_warned = False
    assert controller.subscribe_varbits((13691, 5667)) == {"varbits": [5667, 13691], "varps": []}
    assert seen["path"] == "/events/subscribe"
    assert seen["params"] == {"varbits": "13691,5667", "varps": ""}


def test_subscribe_varbits_warns_once_when_the_bridge_lacks_the_endpoint(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda *a, **k: None)
    logged = []
    monkeypatch.setattr(controller.debug_log, "log", lambda cat, msg: logged.append(msg))
    controller._subscribe_warned = False
    assert controller.subscribe_varbits((1,)) is None
    assert controller.subscribe_varbits((1,)) is None
    assert len([m for m in logged if "events/subscribe" in m]) == 1


def test_loop_stats_logs_slow_iterations_and_a_periodic_summary():
    t = [0.0]
    stats = controller.LoopStats(now_fn=lambda: t[0])
    assert stats.record(0.4, 0.2) == []
    lines = stats.record(1.6, 1.1)
    assert lines and "slow loop iteration 1.60s" in lines[0] and "tick 1.10s" in lines[0]
    t[0] = controller.LOOP_STATS_EVERY_S + 1
    lines = stats.record(0.5, 0.3)
    summary = [l for l in lines if "iterations" in l]
    assert summary and "3 iterations" in summary[0] and "mean 0.83s" in summary[0]
    assert stats.record(0.5, 0.3) == []       # window reset after the summary


def test_inst_flag_is_forwarded_to_the_bridge(monkeypatch):
    calls = []

    def fake_api_get(path, params=None, timeout=None, **kw):
        calls.append((path, dict(params or {})))
        return {"onScreen": True, "onMinimap": True, "found": True, "tiles": [], "flags": [1]}

    monkeypatch.setattr(controller, "api_get", fake_api_get)
    controller.get_ground_walk_point(1, 2, 0, inst=True)
    controller.get_minimap_walk_point(1, 2, 0, inst=True)
    controller.get_object_at(1, 2, 0, 99, inst=True)
    controller.get_ground_item_at(1, 2, 0, 7, inst=True)
    controller.get_collision(1, 2, 0, radius=3, inst=True)
    controller.get_ground_walk_point(1, 2, 0)   # default: no flag on the wire
    assert [p.get("inst") for _, p in calls[:5]] == ["true"] * 5
    assert "inst" not in calls[5][1]


def test_get_collision_grid_returns_body_or_none(monkeypatch):
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, timeout=None, **kw:
                        {"baseX": 13056, "baseY": 5632, "plane": 1, "size": 104, "flags": [0] * 10816})
    grid = controller.get_collision_grid(1)
    assert grid["baseX"] == 13056 and len(grid["flags"]) == 10816
    monkeypatch.setattr(controller, "api_get", lambda *a, **kw: {"flags": []})
    assert controller.get_collision_grid(1) is None
    monkeypatch.setattr(controller, "api_get", lambda *a, **kw: None)
    assert controller.get_collision_grid(1) is None

    # C1: no `plane` key on the wire when none is passed (CollisionProvider then defaults to the
    # LIVE plane), and `plane` forwarded verbatim when one is given.
    seen = {}

    def capture(path, params=None, timeout=None, **kw):
        seen["params"] = params
        return {"baseX": 0, "baseY": 0, "plane": 0, "size": 2, "flags": [0, 0, 0, 0]}

    monkeypatch.setattr(controller, "api_get", capture)
    controller.get_collision_grid()
    assert "plane" not in seen["params"]
    controller.get_collision_grid(3)
    assert seen["params"]["plane"] == 3


def test_fidget_serial_is_shared_across_module_copies(monkeypatch):
    """controller.py is loaded twice (see input_state's docstring); main() registers the fidget
    serial on __main__'s copy, so a routine-side human_delay must find it through input_state,
    not this module's own _fidget_ser -- otherwise routine waits never fidget (2026-09-06)."""
    import input_state
    token = object()
    monkeypatch.setattr(controller, "_fidget_ser", None)
    monkeypatch.setattr(input_state, "fidget_ser", None)
    assert controller._fidget_serial() is None
    controller.register_fidget_serial(token)
    monkeypatch.setattr(controller, "_fidget_ser", None)     # the OTHER copy never saw main()
    assert controller._fidget_serial() is token
    assert input_state.fidget_ser is token


def test_get_trawling_passes_through_and_tolerates_an_unreachable_bridge(monkeypatch):
    payload = {"netFish": 137, "netCapacity": 250, "cargo": None, "nets": []}
    monkeypatch.setattr(controller, "api_get", lambda path, params=None: payload
                        if path == "/trawling" else None)
    assert controller.get_trawling() is payload

    monkeypatch.setattr(controller, "api_get", lambda path, params=None: None)
    assert controller.get_trawling() == {}


# --------------------------------------------------------------------------- Alt+Tab / focus
#
# Lifted out of routines/nmz.py 2026-09-06 so routines/karambwan_fishing.py can tab out for its
# 90-110s fishing waits without a second copy. See nmz's own comment for why a routine that
# unfocuses itself MUST verify the tab back in: the engine loop won't call tick() again while
# unfocused, so a tab-back-in that lands on some other window stalls forever with nothing in the
# log.

def test_alt_tab_holds_alt_down_across_the_tab(monkeypatch):
    lines = []
    monkeypatch.setattr(controller, "send_line", lambda ser, s: lines.append(s))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    controller.alt_tab(object())
    assert lines == ["KD ALT", "KT TAB", "KU ALT"]


def test_wait_for_focus_returns_as_soon_as_the_window_reports_focused(monkeypatch):
    reads = iter([{"windowFocused": False}, {"windowFocused": False}, {"windowFocused": True}])
    monkeypatch.setattr(controller, "get_state", lambda: next(reads))
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    assert controller.wait_for_focus(timeout_s=5.0, poll_interval_s=0.0) is True


def test_wait_for_focus_gives_up_and_says_so(monkeypatch):
    monkeypatch.setattr(controller, "get_state", lambda: {"windowFocused": False})
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    assert controller.wait_for_focus(timeout_s=0.0, poll_interval_s=0.0) is False


def test_wait_for_focus_can_wait_for_the_window_to_GO_AWAY(monkeypatch):
    """The tab-OUT half: a machine with only RuneLite open swallows Alt+Tab entirely, and a
    routine that thinks it tabbed out would then fish with no input for two minutes."""
    reads = iter([{"windowFocused": True}, {"windowFocused": False}])
    monkeypatch.setattr(controller, "get_state", lambda: next(reads))
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    assert controller.wait_for_focus(timeout_s=5.0, poll_interval_s=0.0, focused=False) is True


# --------------------------------------------------------------------------- drop_items
#
# Shift-dropping an inventory (added 2026-09-07 for gathering.py's power-chopping path). Two
# things make this more than a click loop: shift-drop is a GAME SETTING the account may not
# have on, so the first drop is verified and the rest fall back to the right-click menu; and
# these are inventory clicks, where a simulated misclick plus its corrective reclick drops the
# NEIGHBOURING item -- the axe (banker.py's rule).

def _drop_env(monkeypatch, inventory_after):
    """Records what drop_items does. `inventory_after` is what get_inventory reports from the
    first drop onward -- shorter than the starting inventory means shift-drop worked."""
    calls = []
    start = [{"name": "Yew logs", "screenX": 10, "screenY": 20},
             {"name": "Yew logs", "screenX": 30, "screenY": 20}]
    reads = {"n": 0}

    def _inv():
        reads["n"] += 1
        return list(start) if reads["n"] == 1 else list(inventory_after)
    monkeypatch.setattr(controller, "get_inventory", _inv)
    monkeypatch.setattr(controller, "open_inventory_tab",
                        lambda ser: calls.append(("tab",)) or True)
    monkeypatch.setattr(controller, "send_line", lambda ser, s: calls.append(("line", s)))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: calls.append(("click", ent["screenX"], kw)) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, opt, **kw: calls.append(("menu", ent["screenX"], opt)) or True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    return calls, start


def test_drop_items_opens_the_inventory_tab_and_shift_drops_each_item(monkeypatch):
    calls, items = _drop_env(monkeypatch, inventory_after=[])
    assert controller.drop_items(object(), items) is True
    assert calls[0] == ("tab",)
    kinds = [c[0] for c in calls]
    assert kinds.count("click") == 2 and "menu" not in kinds
    # SHIFT is held around every click and released again, so a crash mid-loop can't leave it down.
    assert [c[1] for c in calls if c[0] == "line"] == ["KD SHIFT", "KU SHIFT"] * 2


def test_drop_items_never_simulates_a_misclick_on_an_inventory_slot(monkeypatch):
    calls, items = _drop_env(monkeypatch, inventory_after=[])
    controller.drop_items(object(), items)
    for kind, _, kw in [c for c in calls if c[0] == "click"]:
        assert kw["simulate_mistakes"] is False
        assert kw["check_ui_blockers"] is False


def test_drop_items_falls_back_to_the_menu_when_shift_drop_is_off(monkeypatch):
    # The inventory never shrinks: the account has shift-click drop disabled, so the first
    # click did nothing and every drop from here on is a named menu action.
    calls, items = _drop_env(monkeypatch, inventory_after=[{"name": "Yew logs"}] * 2)
    assert controller.drop_items(object(), items) is False
    menus = [c for c in calls if c[0] == "menu"]
    assert [m[1] for m in menus] == [10, 30]      # the first item is retried, then the second
    assert all(m[2] == "Drop" for m in menus)
    assert len([c for c in calls if c[0] == "click"]) == 1   # only the one probing shift-click
