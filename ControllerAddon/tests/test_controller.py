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
