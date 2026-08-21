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


class _FakeSock:
    """Stands in for the UDP socket; controller.poll_udp is monkeypatched, so this is only ever
    an identity token."""


def _stream(monkeypatch, batches):
    """EventStream whose poll_udp yields each batch in turn, then nothing."""
    import controller
    queue = list(batches)
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: queue.pop(0) if queue else [])
    return bridge_events.EventStream(_FakeSock())


def test_pump_returns_events_for_on_event_dispatch(monkeypatch):
    """The existing routine contract must keep working -- pump is the only drain, so it has to
    hand every event back to the caller."""
    s = _stream(monkeypatch, [[("CHAT_MESSAGE", {"message": "hi"})]])
    assert s.pump(now=1000.0) == [("CHAT_MESSAGE", {"message": "hi"})]


def test_pump_feeds_the_tick_model(monkeypatch):
    s = _stream(monkeypatch, [[("TICK", {"tick": 42, "atMillis": 5_000_000})]])
    s.pump(now=1000.0)
    assert s.ticks.tick == 42
    assert s.ticks.ms_until_next(now=1000.0) == 600


def test_wait_for_event_returns_one_that_already_arrived_this_tick(monkeypatch):
    """The common race: the thing we are waiting for landed while we were still deciding to
    wait. Without the buffer this blocks for the full timeout on an event we already have."""
    s = _stream(monkeypatch, [[("GROUND_ITEM_SPAWNED", {"id": 10008, "x": 1, "y": 2, "tick": 7})]])
    s.pump(now=1000.0)
    got = s.wait_for_event("GROUND_ITEM_SPAWNED", timeout_s=0, now_fn=lambda: 1000.0)
    assert got["id"] == 10008


def test_wait_for_event_matches_on_a_predicate(monkeypatch):
    s = _stream(monkeypatch, [[("OBJECT_SPAWNED", {"id": 1, "x": 5, "y": 5}),
                               ("OBJECT_SPAWNED", {"id": 9380, "x": 9, "y": 9})]])
    s.pump(now=1000.0)
    got = s.wait_for_event("OBJECT_SPAWNED", match=lambda p: p.get("id") == 9380,
                           timeout_s=0, now_fn=lambda: 1000.0)
    assert got["x"] == 9


def test_wait_for_event_returns_none_on_timeout(monkeypatch):
    """A dropped packet must degrade to the caller's slow path, not hang."""
    s = _stream(monkeypatch, [])
    clock = {"t": 1000.0}
    got = s.wait_for_event("XP_GAINED", timeout_s=0.5,
                           now_fn=lambda: clock["t"],
                           sleep_fn=lambda d: clock.__setitem__("t", clock["t"] + d))
    assert got is None


def test_the_buffer_does_not_grow_without_bound(monkeypatch):
    """These run unattended for hours; the ring exists to close a race, not to be a log."""
    import controller
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("TICK", {"tick": 1, "atMillis": 0})] * 50)
    s = bridge_events.EventStream(_FakeSock())
    for i in range(100):
        s.pump(now=1000.0 + i)
    assert len(s._recent) <= bridge_events.EVENT_BUFFER_MAX


def test_the_buffer_is_pruned_by_tick_age_not_just_count(monkeypatch):
    """The spec requires pruning to the last 10 ticks, not just the last 256 events -- an event
    from 30+ seconds ago sitting under the count cap could otherwise still satisfy a
    wait_for_event, and a routine would wrongly conclude a stale action just completed."""
    import controller
    old_batch = [("GROUND_ITEM_SPAWNED", {"id": 1, "x": 1, "y": 1, "tick": 5})]
    monkeypatch.setattr(controller, "poll_udp", lambda sock: old_batch if old_batch else [])
    s = bridge_events.EventStream(_FakeSock())
    s.pump(now=1000.0)
    old_batch = []
    # Advance the tick model well past the buffered event's tick.
    s.ticks.update(tick=5 + bridge_events.EVENT_BUFFER_MAX_TICKS + 5, at_millis=0, now=1001.0)
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("OBJECT_SPAWNED", {"id": 2, "x": 2, "y": 2,
                                                            "tick": s.ticks.tick})])
    s.pump(now=1002.0)
    types = [t for t, _ in s._recent]
    assert "GROUND_ITEM_SPAWNED" not in types
    assert "OBJECT_SPAWNED" in types


def test_the_age_prune_does_not_drop_payload_less_or_untagged_events(monkeypatch):
    """CHAT_MESSAGE and other older event types carry no tick field at all -- they must survive
    the age prune (governed by the count cap only) and must never crash on payload is None."""
    import controller
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("PLAYER_DIED", None),
                                      ("TICK", {"tick": 500, "atMillis": 0})])
    s = bridge_events.EventStream(_FakeSock())
    s.pump(now=1000.0)
    types = [t for t, _ in s._recent]
    assert "PLAYER_DIED" in types
