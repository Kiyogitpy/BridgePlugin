import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bridge_events


def test_phase_is_zero_immediately_after_a_tick():
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=5_000_000, now=1000.0)
    assert m.tick == 100
    assert m.phase_ms(now=1000.0) == 0


def test_phase_advances_with_wall_clock():
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=5_000_000, now=1000.0)
    assert m.phase_ms(now=1000.25) == 250


def test_ms_until_next_is_the_remainder_of_the_tick():
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=5_000_000, now=1000.0)
    assert m.ms_until_next(now=1000.25) == 350


def test_a_late_tick_does_not_report_negative_time_remaining():
    """The client can stutter and a tick can run long; callers must never sleep a negative."""
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=5_000_000, now=1000.0)
    assert m.ms_until_next(now=1002.0) == 0


def test_an_unsynced_model_reports_no_tick():
    m = bridge_events.TickModel()
    assert m.tick is None
    assert m.ms_until_next(now=1000.0) == 0
