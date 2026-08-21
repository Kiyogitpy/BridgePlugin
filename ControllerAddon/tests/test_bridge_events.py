import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import bridge_events


def test_phase_is_zero_immediately_after_a_tick():
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=1_000_000.0, now=1000.0)
    assert m.tick == 100
    assert m.phase_ms(now=1000.0) == 0


def test_phase_advances_with_wall_clock():
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=1_000_000.0, now=1000.0)
    assert m.phase_ms(now=1000.25) == 250


def test_phase_ms_is_derived_from_at_millis_not_python_drain_time():
    """I3: at_millis is Java's System.currentTimeMillis() at the real tick boundary -- the same
    wall clock time.time() reads. phase_ms must be measured from THAT instant, not from whenever
    Python happened to drain the UDP packet (_synced_at), otherwise phase is under-reported by
    the drain latency -- exactly the tick-phase blindness this module exists to remove."""
    m = bridge_events.TickModel()
    # The packet is drained 80ms late (_synced_at lags at_millis) -- phase_ms must still reflect
    # true elapsed time since the boundary (250ms), not time since the late drain (170ms).
    m.update(tick=100, at_millis=1_000_000.0 - 80.0, now=1000.0)
    assert m.phase_ms(now=1000.25) == pytest.approx(250 + 80)


def test_phase_ms_wraps_via_modulo_instead_of_growing_unbounded():
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=1_000_000.0, now=1000.0)
    # 1.4s later = 2 tick boundaries (1200ms) plus 200ms into the next.
    assert m.phase_ms(now=1001.4) == pytest.approx(200)


def test_ms_until_next_is_the_remainder_of_the_tick():
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=1_000_000.0, now=1000.0)
    assert m.ms_until_next(now=1000.25) == 350


def test_ms_until_next_never_goes_negative_even_several_ticks_later():
    """Post-I3, phase_ms wraps via modulo, so it can never exceed one tick period on its own --
    but the max(0, ...) clamp in ms_until_next stays as a defensive floor regardless."""
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=1_000_000.0, now=1000.0)
    assert 0 <= m.ms_until_next(now=1003.7) <= bridge_events.GAME_TICK_MS


def test_an_unsynced_model_reports_no_tick():
    m = bridge_events.TickModel()
    assert m.tick is None
    assert m.ms_until_next(now=1000.0) == 0


def test_is_stale_when_never_synced():
    m = bridge_events.TickModel()
    assert m.is_stale(now=1000.0) is True


def test_is_stale_judges_time_since_the_last_packet_not_phase():
    """I2/I3: staleness must be judged by _synced_at (when a TICK packet was last actually
    processed), never by phase_ms -- phase_ms is derived purely from the atMillis anchor plus the
    wall clock (I3) and would keep reporting a plausible in-range phase forever even if the UDP
    listener died minutes ago and nothing has arrived since."""
    m = bridge_events.TickModel()
    m.update(tick=100, at_millis=1_000_000.0, now=1000.0)
    assert m.is_stale(now=1000.1) is False
    # STALE_AFTER_MS = GAME_TICK_MS * 2 = 1200ms since the last packet was actually processed.
    assert m.is_stale(now=1000.0 + bridge_events.STALE_AFTER_MS / 1000.0 + 0.001) is True


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
    s = _stream(monkeypatch, [[("TICK", {"tick": 42, "atMillis": 1000.0 * 1000.0})]])
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


def test_the_age_prune_survives_a_tick_counter_reset(monkeypatch):
    """M3: after a client restart tickClock returns to 0 while the buffer still holds entries
    tagged with a tick from before the restart (e.g. 5000). The old `current_tick - tick <=
    EVENT_BUFFER_MAX_TICKS` keep-condition computes 0 - 5000 = -5000, which passes (<=10) and
    wrongly keeps a now-meaningless stale entry "fresh" forever. The condition must also reject
    tick > current_tick."""
    import controller
    old_batch = [("GROUND_ITEM_SPAWNED", {"id": 1, "x": 1, "y": 1, "tick": 5000})]
    monkeypatch.setattr(controller, "poll_udp", lambda sock: old_batch if old_batch else [])
    s = bridge_events.EventStream(_FakeSock())
    s.pump(now=1000.0)
    old_batch = []
    # Client restarted -- tick counter is back at (near) 0.
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("TICK", {"tick": 3, "atMillis": 3000.0})])
    s.pump(now=1001.0)
    types = [t for t, _ in s._recent]
    assert "GROUND_ITEM_SPAWNED" not in types


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


def test_drain_returns_events_pumped_during_a_wait_for_event_call(monkeypatch):
    """C1: wait_for_event's internal pump() calls must not swallow events from main()'s
    dispatch path. A PLAYER_DIED that arrives mid-wait (e.g. during retrieve_trap's 3s HTTP
    confirm loop) has to still reach handle_generic_events/on_event via drain() -- pump() alone
    hands it to nobody, since wait_for_event discards pump's return value."""
    import controller
    batches = [[], [("PLAYER_DIED", None)], []]
    monkeypatch.setattr(controller, "poll_udp", lambda sock: batches.pop(0) if batches else [])
    s = bridge_events.EventStream(_FakeSock())
    clock = {"t": 1000.0}

    # wait_for_event pumps internally while waiting; nothing it sees should be lost.
    got = s.wait_for_event("SOMETHING_ELSE", timeout_s=0.05,
                           now_fn=lambda: clock["t"],
                           sleep_fn=lambda d: clock.__setitem__("t", clock["t"] + d))
    assert got is None

    dispatched = s.drain(now=clock["t"])
    assert ("PLAYER_DIED", None) in dispatched


def test_drain_only_hands_back_each_event_once(monkeypatch):
    """A second drain() right after the first must come back empty -- otherwise main() would
    dispatch the same PLAYER_DIED/on_event call twice."""
    import controller
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("PLAYER_DIED", None)])
    s = bridge_events.EventStream(_FakeSock())
    first = s.drain(now=1000.0)
    monkeypatch.setattr(controller, "poll_udp", lambda sock: [])
    second = s.drain(now=1000.1)
    assert first == [("PLAYER_DIED", None)]
    assert second == []


def test_wait_for_event_pumps_before_its_first_buffer_scan(monkeypatch):
    """M1: retrieve_trap's flow calls clear() then, some time later, wait_for_event -- nothing
    pumps in between (that's the whole point of overlapping HTTP setup with the wait), so the
    event is very likely already sitting unread in the socket the instant wait_for_event is
    called. Scanning the (still-empty) buffer before ever pumping wastes a full poll interval on
    exactly the path this module exists to accelerate."""
    import controller
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("GROUND_ITEM_SPAWNED", {"id": 10008, "x": 1, "y": 2})])
    s = bridge_events.EventStream(_FakeSock())
    clock = {"t": 1000.0}
    slept = []

    got = s.wait_for_event("GROUND_ITEM_SPAWNED", timeout_s=1.0,
                           now_fn=lambda: clock["t"],
                           sleep_fn=lambda d: (slept.append(d),
                                                clock.__setitem__("t", clock["t"] + d)))
    assert got is not None
    assert slept == [], "must be satisfied by the pre-wait pump, no sleep_fn call needed"


def test_wait_ticks_pumps_first_so_a_boundary_that_just_landed_is_seen(monkeypatch):
    import controller
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("TICK", {"tick": 1, "atMillis": 1000000.0})])
    s = bridge_events.EventStream(_FakeSock())
    slept = []
    s.wait_ticks(1, now_fn=lambda: 1000.0, sleep_fn=lambda d: slept.append(d))
    assert s.ticks.tick == 1
    assert slept == [pytest.approx(0.6)]


def test_wait_ticks_falls_back_to_a_full_wall_clock_sleep_on_a_stale_model(monkeypatch):
    """I2: ms_until_next clamps with max(0.0, ...), so once the model is stale (no fresh TICK
    heard in a while) it would silently report 0ms remaining and wait_ticks(1) would become a
    no-op -- turning a settle wait into an early click instead of merely a slow one. A stale
    model must fall back to a plain n * GAME_TICK_MS wall-clock sleep, matching the "no worse
    than a plain wait" guarantee the whole event-mirror design promises for a dead listener."""
    import controller
    monkeypatch.setattr(controller, "poll_udp", lambda sock: [])
    s = bridge_events.EventStream(_FakeSock())
    # Synced long ago -- well past STALE_AFTER_MS.
    s.ticks.update(tick=1, at_millis=1000000.0, now=1000.0)
    clock = {"t": 1000.0 + bridge_events.STALE_AFTER_MS / 1000.0 + 1.0}
    slept = []
    s.wait_ticks(2, now_fn=lambda: clock["t"], sleep_fn=lambda d: slept.append(d))
    assert slept == [pytest.approx(2 * bridge_events.GAME_TICK_MS / 1000.0)]


def test_deadline_after_ticks_returns_a_wall_clock_time_without_sleeping(monkeypatch):
    """I4: a caller (e.g. _await_placement_settled) wants to overlap its own setup work with the
    wait, the same way the polling path's chin_free_at already does -- so this must hand back a
    deadline, never block."""
    import controller
    monkeypatch.setattr(controller, "poll_udp",
                        lambda sock: [("TICK", {"tick": 1, "atMillis": 1000000.0})])
    s = bridge_events.EventStream(_FakeSock())
    deadline = s.deadline_after_ticks(2, now_fn=lambda: 1000.0)
    # Synced exactly on the boundary: 2 ticks away = 1.2s, no sleep occurred getting the answer.
    assert deadline == pytest.approx(1000.0 + 1.2)


def test_deadline_after_ticks_falls_back_to_wall_clock_on_a_stale_model(monkeypatch):
    import controller
    monkeypatch.setattr(controller, "poll_udp", lambda sock: [])
    s = bridge_events.EventStream(_FakeSock())
    # Never synced at all.
    deadline = s.deadline_after_ticks(2, now_fn=lambda: 1000.0)
    assert deadline == pytest.approx(1000.0 + 2 * bridge_events.GAME_TICK_MS / 1000.0)


def test_ctx_exposes_the_event_stream():
    """Routines reach the mirror through ctx, the same way they reach ctx.state and ctx.ser."""
    import controller
    ctx = controller.Context(ser=None, state=None, events="sentinel")
    assert ctx.events == "sentinel"


def test_ctx_still_constructs_without_an_event_stream():
    """Purely additive: every existing caller passes two positional args and must keep working,
    and a routine running without a stream must see None rather than an AttributeError."""
    import controller
    ctx = controller.Context(None, None)
    assert ctx.events is None
