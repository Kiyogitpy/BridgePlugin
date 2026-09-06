import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bridge_events
import controller
import outcome_waits as ow


class _Ctx:
    def __init__(self, events=None):
        self.events = events


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _quiet(monkeypatch, clock):
    monkeypatch.setattr(controller, "poll_udp", lambda sock: [])
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.3)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    return _Ctx(bridge_events.EventStream(sock=None))


def test_await_pre_returns_at_once_with_a_reaction_pad_when_confirm_already_holds(monkeypatch):
    clock = _Clock()
    ctx = _quiet(monkeypatch, clock)
    out = ow.await_pre(ctx, "INVENTORY_CHANGED", 10.0, confirm=lambda: True,
                       now_fn=clock.now, sleep_fn=clock.sleep)
    assert out
    assert out.signal is None
    assert abs(clock.t - 1000.3) < 1e-9          # the reaction draw, nothing else


def test_await_pre_falls_to_the_budget_when_nothing_happens(monkeypatch):
    clock = _Clock()
    ctx = _quiet(monkeypatch, clock)
    out = ow.await_pre(ctx, "INVENTORY_CHANGED", 2.0, confirm=lambda: False,
                       now_fn=clock.now, sleep_fn=clock.sleep)
    assert not out
    assert clock.t >= 1002.0


def test_await_long_fidgets_between_chunks_and_stops_on_stop(monkeypatch):
    clock = _Clock()
    ctx = _quiet(monkeypatch, clock)
    fidgets = []
    monkeypatch.setattr(controller, "fidget_once", lambda: fidgets.append(1))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] >= 3

    out = ow.await_long(ctx, "INVENTORY_CHANGED", 60.0, confirm=lambda: False, stop=stop,
                        now_fn=clock.now, sleep_fn=clock.sleep)
    assert not out
    assert clock.t - 1000.0 < 60.0
    assert len(fidgets) >= 2


def test_await_long_can_run_without_fidgets(monkeypatch):
    clock = _Clock()
    ctx = _quiet(monkeypatch, clock)
    fidgets = []
    monkeypatch.setattr(controller, "fidget_once", lambda: fidgets.append(1))
    ow.await_long(ctx, "INVENTORY_CHANGED", 8.0, confirm=lambda: False, fidget=False,
                  now_fn=clock.now, sleep_fn=clock.sleep)
    assert fidgets == []


def test_tick_pause_without_events_sleeps_whole_ticks(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    ow.tick_pause(_Ctx(None), 2, now_fn=clock.now, sleep_fn=clock.sleep)
    assert abs(clock.t - 1001.2) < 1e-9


def test_predicates():
    assert ow.varbit_in((1, 2))({"varbitId": 2})
    assert not ow.varbit_in((1, 2))({"varbitId": 3})
    assert ow.chat_has("you collect", "liplack")({"message": "You collect some Liplack liquor."})
    assert not ow.chat_has("you finish")({"message": "You collect some Liplack liquor."})
    assert ow.walk_budget({"distance": 5}) == 3.0
    assert ow.walk_budget(None) == 0.0


def test_mark_is_none_without_events():
    assert ow.mark(_Ctx(None)) is None
    assert ow.events_of(_Ctx(None)) is None
