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


def _ticking_events(clock, monkeypatch, start_tick=100, phase_ms=0.0):
    """A REAL EventStream whose TICK packets are synthesised from the fake clock, so the tick
    NUMBER advances exactly as the bridge's would as the clock is slept forward. This is what
    at_tick counts, so a fixture whose tick never moves would prove nothing."""
    events = bridge_events.EventStream(sock=None)
    boundary_s = clock.now() - phase_ms / 1000.0

    def _poll(sock):
        elapsed_ms = (clock.now() - boundary_s) * 1000.0
        n = int(elapsed_ms // bridge_events.GAME_TICK_MS)
        return [("TICK", {"tick": start_tick + n,
                          "atMillis": boundary_s * 1000.0 + n * bridge_events.GAME_TICK_MS})]

    monkeypatch.setattr(controller, "poll_udp", _poll)
    events.pump(now=clock.now())
    return events


def test_at_phase_leaves_the_TICK_NUMBER_current_after_the_sleep(monkeypatch):
    """at_phase sleeps ACROSS a tick boundary by construction, so a caller that reads
    current_tick() right afterwards must be answered with the tick it woke IN, not the one it
    left. Pumping only before the sleep answered 100 here; the cycle built on that pinned its
    next critical click one tick early, turning a 3-tick cycle into a 2-tick one."""
    clock = _Clock()
    events = _ticking_events(clock, monkeypatch, start_tick=100, phase_ms=200.0)
    assert tick_cycle.current_tick(events, now=clock.now()) == 100

    woke = tick_cycle.at_phase(events, 60.0, now_fn=clock.now, sleep_fn=clock.sleep)
    assert woke == pytest.approx(60.0, abs=1.0)
    assert clock.t == pytest.approx(1000.460, abs=1e-6)     # 400ms to the boundary, then 60ms
    assert tick_cycle.current_tick(events, now=clock.now()) == 101


def test_current_tick_reports_the_bridges_tick_number(monkeypatch):
    clock = _Clock()
    events = _ticking_events(clock, monkeypatch, start_tick=100)
    assert tick_cycle.current_tick(events, now=clock.now()) == 100


def test_current_tick_is_none_without_a_stream_or_with_a_stale_clock():
    clock = _Clock()
    assert tick_cycle.current_tick(None) is None
    events = _events_at(clock, 250.0)
    clock.t += 5.0
    assert tick_cycle.current_tick(events, now=clock.now()) is None


def test_at_tick_waits_for_the_named_tick_and_lands_at_its_phase(monkeypatch):
    """THE C1 FIX. at_phase lands on whatever boundary comes next, so the cycle period was
    however long the caller's own work took; at_tick pins it to a counted tick."""
    clock = _Clock()
    events = _ticking_events(clock, monkeypatch, start_tick=100, phase_ms=200.0)
    woke = tick_cycle.at_tick(events, 103, 60.0, now_fn=clock.now, sleep_fn=clock.sleep)
    assert tick_cycle.current_tick(events, now=clock.now()) == 103
    assert woke == pytest.approx(60.0, abs=6.0)
    # 400ms left of tick 100, then two whole ticks, then 60ms in.
    assert clock.t == pytest.approx(1000.0 + 1.66, abs=0.02)


def test_at_tick_reports_missed_rather_than_firing_late(monkeypatch):
    """A click placed in the wrong tick desyncs every cycle after it, so a target that has
    already passed must be refused, not fired late."""
    clock = _Clock()
    events = _ticking_events(clock, monkeypatch, start_tick=100)
    assert tick_cycle.at_tick(events, 99, 60.0,
                              now_fn=clock.now, sleep_fn=clock.sleep) == "missed"


def test_at_tick_returns_immediately_when_the_target_tick_is_already_current(monkeypatch):
    clock = _Clock()
    events = _ticking_events(clock, monkeypatch, start_tick=100, phase_ms=10.0)
    woke = tick_cycle.at_tick(events, 100, 60.0, now_fn=clock.now, sleep_fn=clock.sleep)
    assert woke == pytest.approx(60.0, abs=6.0)
    assert clock.t == pytest.approx(1000.05, abs=0.01)


def test_at_tick_refuses_rather_than_guessing_when_the_clock_is_stale():
    clock = _Clock()
    events = _events_at(clock, 250.0)
    clock.t += 5.0
    assert tick_cycle.at_tick(events, 101, 60.0,
                              now_fn=clock.now, sleep_fn=clock.sleep) is None


def test_at_tick_gives_up_on_a_clock_that_answers_but_never_advances(monkeypatch):
    """A wedged listener keeps the model fresh (so is_stale is false) while the tick number
    stands still. max_wait_ticks is what stops that hanging the routine forever."""
    clock = _Clock()
    events = bridge_events.EventStream(sock=None)
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("TICK", {"tick": 100,
                                                "atMillis": clock.now() * 1000.0})])
    events.pump(now=clock.now())
    assert tick_cycle.at_tick(events, 103, 60.0, now_fn=clock.now, sleep_fn=clock.sleep,
                              max_wait_ticks=3) is None
    assert clock.t < 1000.0 + 4 * 0.6 + 0.05


def test_at_tick_is_none_without_a_stream():
    assert tick_cycle.at_tick(None, 100, 60.0) is None


def test_click_cost_ms_books_the_UPPER_end_of_the_hold_plus_its_trailing_delay():
    """It feeds Window.room_for, which is a guard: conservative costs one abandoned cycle,
    optimistic costs a desynced chain."""
    import settings
    assert tick_cycle.CLICK_COST_MS == pytest.approx(
        (settings.CLICK_HOLD_RANGE_S[1] + 0.09) * 1000.0)
    assert tick_cycle.CLICK_COST_MS > 85.0 + 60.0        # comfortably past the medians


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
