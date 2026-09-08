import pytest

import bridge_events
import controller
import tick_cycle


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """EventStream.pump calls controller.poll_udp(self._sock), which would touch a real socket.
    Hand it nothing so the suite is hermetic and the tick model is driven only by the anchors
    the tests set explicitly."""
    monkeypatch.setattr(controller, "poll_udp", lambda sock: [])


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _events_at(clock, phase_ms):
    """A real EventStream whose tick model is anchored so that `clock.now()` sits `phase_ms`
    into the current tick. Uses the real TickModel so its own arithmetic is what we exercise."""
    events = bridge_events.EventStream(sock=None)
    at_millis = clock.now() * 1000.0 - phase_ms
    events.ticks.update(tick=100, at_millis=at_millis, now=clock.now())
    return events


def test_tick_phase_ms_reports_where_we_are_in_the_tick():
    clock = _Clock()
    events = _events_at(clock, 250.0)
    assert tick_cycle.tick_phase_ms(events, clock.now()) == pytest.approx(250.0, abs=1.0)


def test_tick_phase_ms_is_none_without_a_stream():
    assert tick_cycle.tick_phase_ms(None, 1000.0) is None


def test_tick_phase_ms_is_none_when_the_clock_is_stale():
    clock = _Clock()
    events = _events_at(clock, 250.0)
    clock.t += 5.0          # no TICK packet for 5s -- well past STALE_AFTER_MS
    assert tick_cycle.tick_phase_ms(events, clock.now()) is None


def test_at_phase_lands_at_the_requested_phase_of_the_NEXT_tick():
    clock = _Clock()
    events = _events_at(clock, 500.0)       # 100ms left in this tick
    woke = tick_cycle.at_phase(events, 60.0, now_fn=clock.now, sleep_fn=clock.sleep)
    # 100ms to the boundary, then 60ms in: 160ms of sleep.
    assert clock.t == pytest.approx(1000.160, abs=1e-6)
    assert woke == pytest.approx(60.0, abs=1.0)


def test_at_phase_goes_to_the_next_tick_even_when_the_phase_has_not_passed_yet():
    """Deliberate: always the NEXT boundary, so the caller's cycle length is predictable."""
    clock = _Clock()
    events = _events_at(clock, 10.0)        # target phase 60 is still ahead in THIS tick
    tick_cycle.at_phase(events, 60.0, now_fn=clock.now, sleep_fn=clock.sleep)
    assert clock.t == pytest.approx(1000.650, abs=1e-6)   # 590ms to boundary + 60ms


def test_at_phase_refuses_rather_than_guessing_when_the_clock_is_stale():
    clock = _Clock()
    events = _events_at(clock, 250.0)
    clock.t += 5.0
    assert tick_cycle.at_phase(events, 60.0, now_fn=clock.now, sleep_fn=clock.sleep) is None


def test_estimate_flight_ms_matches_the_fitted_model_at_its_reference_distance():
    import settings
    assert tick_cycle.estimate_flight_ms(settings.HM2_D_REF) == pytest.approx(
        settings.HM2_MED_REF_MS, abs=0.5)


def test_estimate_flight_ms_adds_one_slope_per_doubling():
    import settings
    near = tick_cycle.estimate_flight_ms(settings.HM2_D_REF)
    far = tick_cycle.estimate_flight_ms(settings.HM2_D_REF * 2)
    assert far - near == pytest.approx(settings.HM2_SLOPE_MS_PER_BIT, abs=0.5)


def test_estimate_flight_ms_is_zero_for_a_click_in_place():
    assert tick_cycle.estimate_flight_ms(0) == 0.0


def test_timed_click_moves_then_clicks_and_reports_elapsed(monkeypatch):
    clock = _Clock()
    calls = []
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: (calls.append(("move", x, y)), clock.sleep(0.17)))
    monkeypatch.setattr(controller, "click_human",
                        lambda ser: (calls.append(("click",)), clock.sleep(0.085)))
    elapsed = tick_cycle.timed_click(object(), (400, 300), now_fn=clock.now)
    assert calls == [("move", 400, 300), ("click",)]
    assert elapsed == pytest.approx(255.0, abs=1.0)


def test_click_in_place_never_moves(monkeypatch):
    clock = _Clock()
    moved = []
    monkeypatch.setattr(controller, "move_to_human", lambda *a, **k: moved.append(a))
    monkeypatch.setattr(controller, "click_human", lambda ser: clock.sleep(0.092))
    elapsed = tick_cycle.click_in_place(object(), now_fn=clock.now)
    assert moved == []
    assert elapsed == pytest.approx(92.0, abs=1.0)


def test_window_counts_the_phase_it_opened_at_not_just_elapsed_time():
    """The window opens PART WAY into the tick -- at logClickPhaseMs -- so the phase is
    already spent before a single click has been issued."""
    w = tick_cycle.Window(opened_at=1000.0, phase_ms=60.0)
    assert w.spent_ms(1000.0) == pytest.approx(60.0)
    assert w.spent_ms(1000.2) == pytest.approx(260.0)
    assert w.remaining_ms(1000.2) == pytest.approx(340.0)


def test_window_room_for_refuses_a_click_that_would_cross_the_boundary():
    w = tick_cycle.Window(opened_at=1000.0, phase_ms=60.0)
    assert w.room_for(300.0, 1000.2) is True       # 340ms left
    assert w.room_for(400.0, 1000.2) is False
