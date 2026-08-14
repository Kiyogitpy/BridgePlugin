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
