import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import granite_3t as g


def _item(name, x=1700, y=800, slot=0):
    return {"name": name, "qty": 1, "slot": slot, "screenX": x, "screenY": y}


class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(g.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.state = _State()
        self.ser = object()
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic: nothing here may reach the running client or the serial port."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def _cfg(**over):
    cfg = dict(g.DEFAULT_CONFIG)
    cfg["rockTiles"] = [[10, 20, 0], [11, 20, 0], [12, 20, 0], [13, 20, 0]]
    cfg["rockId"] = 1
    cfg.update(over)
    return cfg


def test_find_item_matches_a_substring_case_insensitively():
    inv = [_item("Teak logs"), _item("Knife")]
    assert g.find_item(inv, "knife")["name"] == "Knife"


def test_log_count_counts_every_matching_slot():
    inv = [_item("Teak logs", slot=1), _item("Teak logs", slot=2), _item("Knife", slot=0)]
    assert g.log_count(inv, "teak logs") == 2


def test_geometry_refuses_when_the_knife_is_missing():
    ok, msg = g.check_geometry(_cfg(), [_item("Teak logs")])
    assert ok is False
    assert "knife" in msg.lower()


def test_geometry_refuses_on_more_than_one_log_and_says_why(monkeypatch):
    inv = [_item("Knife", slot=0), _item("Teak logs", slot=1), _item("Teak logs", slot=2)]
    ok, msg = g.check_geometry(_cfg(), inv)
    assert ok is False
    assert "make-x" in msg.lower()


def test_geometry_refuses_when_a_rock_will_not_resolve(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    ok, msg = g.check_geometry(_cfg(), [_item("Knife", slot=0), _item("Teak logs", slot=1)])
    assert ok is False
    assert "rock" in msg.lower()


def test_geometry_refuses_when_the_rocks_are_too_far_from_the_log_AND_reports_the_pixels(
        monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 400, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_cfg(maxReachPx=220), inv)
    assert ok is False
    assert "1340" in msg          # |1740 - 400| -- the measurement must be IN the message
    assert "220" in msg


def test_geometry_passes_and_still_reports_the_measurement(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1640, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_cfg(maxReachPx=220), inv)
    assert ok is True
    assert "100" in msg


def test_setup_safety_stops_without_a_tick_clock(monkeypatch):
    """The one place this repo's 'always tolerate ctx.events is None' rule inverts: there is
    no fallback, and degrading silently to a 5-tick miner would be worse than stopping."""
    ctx = _Ctx()
    ctx.events = None
    ctx.state.config = _cfg()
    monkeypatch.setattr(g, "check_geometry", lambda cfg, inv: (True, "fine"))
    g.setup(ctx)
    assert ctx.state.safety_stopped is True
    assert "tick" in ctx.state.safety_reason.lower()


import tick_cycle


class _FakeTicks:
    def __init__(self, phase=0.0):
        self.phase = phase

    def is_stale(self, now):
        return False


class _FakeEvents:
    """Enough EventStream for the cycle: a tick clock that never goes stale."""
    def __init__(self):
        self.ticks = _FakeTicks()
        self._seq = 0

    def pump(self, now=None):
        return []

    def mark(self):
        return self._seq

    def since(self, seq):
        return []

    def wait_ticks(self, n, now_fn=None, sleep_fn=None):
        return None


def test_next_rock_index_wraps_around_the_rotation():
    assert g.next_rock_index(0, 4) == 1
    assert g.next_rock_index(3, 4) == 0


def test_run_cycle_clicks_the_log_IN_PLACE_then_flies_to_the_rock(monkeypatch):
    """The whole design: two clicks, ONE flight, and the log click must not move the cursor."""
    order = []
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 60.0)
    monkeypatch.setattr(tick_cycle, "click_in_place",
                        lambda ser, **kw: (order.append("log-in-place"), 92.0)[1])
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (order.append(("rock", pt)), 255.0)[1])
    # missChance defaults to 0.03, so an unpatched draw makes this test flake ~3% of runs.
    monkeypatch.setattr(g.random, "random", lambda: 1.0)
    monkeypatch.setattr(g, "_now", lambda: 1000.0)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    result = g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800))
    assert result == "ok"
    assert order == ["log-in-place", ("rock", (1640, 800))]


def test_run_cycle_reports_desync_when_the_clock_will_not_answer(monkeypatch):
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: None)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800)) == "no_clock"


def test_run_cycle_ABORTS_the_rock_click_rather_than_letting_it_cross_the_boundary(monkeypatch):
    """A click that slips into the next tick is worse than no click: it starts the NEXT cycle's
    mine at the wrong phase and desyncs everything after it."""
    clicked = []
    aborted = []
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 60.0)
    # The log click alone eats almost the whole tick -- a stutter, a GC pause, an OS hiccup.
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: 520.0)
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (clicked.append(pt), 255.0)[1])
    monkeypatch.setattr(controller, "request_movement_abort", lambda: aborted.append(True))
    monkeypatch.setattr(g.random, "random", lambda: 1.0)      # never a deliberate miss here
    times = iter([1000.0, 1000.52, 1000.52, 1000.52])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800)) == "desync"
    assert clicked == []


def test_a_deliberate_miss_still_clicks_the_rock_but_a_tick_late(monkeypatch):
    """The imperfection has to be a LATE click, not a skipped one -- skipping would forfeit the
    granite as well as the cycle, which is not what a human miss looks like."""
    waited = []
    clicked = []
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 60.0)
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: 92.0)
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (clicked.append(pt), 255.0)[1])
    monkeypatch.setattr(g.random, "random", lambda: 0.0)      # always miss
    monkeypatch.setattr(g, "_now", lambda: 1000.0)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.events.wait_ticks = lambda n, **kw: waited.append(n)
    assert g.run_cycle(ctx, _cfg(missChance=1.0), (1700, 800), (1740, 800), (1640, 800)) == "miss"
    assert waited == [1]
    assert clicked == [(1640, 800)]


def test_no_deliberate_miss_when_the_draw_is_above_the_chance(monkeypatch):
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 60.0)
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: 92.0)
    monkeypatch.setattr(tick_cycle, "timed_click", lambda ser, pt, **kw: 255.0)
    monkeypatch.setattr(g.random, "random", lambda: 0.99)
    monkeypatch.setattr(g, "_now", lambda: 1000.0)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(missChance=0.03), (1700, 800), (1740, 800), (1640, 800)) == "ok"


def test_prepare_selects_the_knife_then_parks_on_the_log(monkeypatch):
    """Order matters: the knife click is the client-side SELECTION, and the cursor must end on
    the log so the critical tick's click needs no flight."""
    order = []
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (order.append(("click", pt)), 200.0)[1])
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: order.append(("park", (x, y))))
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1640, "screenY": 800, "id": 1})
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 0.0)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    point = g.prepare(ctx, _cfg(), (1700, 800), (1740, 800), [10, 20, 0])
    assert point == (1640, 800)
    assert order == [("click", (1700, 800)), ("park", (1740, 800))]


def test_prepare_parks_on_the_log_even_when_the_rock_does_not_resolve(monkeypatch):
    """The park must be unconditional: run_cycle's click_in_place assumes the cursor is on
    the log, and its abandon arithmetic measures from log_point."""
    order = []
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (order.append(("click", pt)), 200.0)[1])
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: order.append(("park", (x, y))))
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.prepare(ctx, _cfg(), (1700, 800), (1740, 800), [10, 20, 0]) is None
    assert order == [("click", (1700, 800)), ("park", (1740, 800))]


def test_run_cycle_makes_NO_bridge_call_between_the_two_clicks(monkeypatch):
    """The invariant the module exists for: one round trip is 15-120ms out of a 600ms
    budget that is already mostly spent."""
    calls = []
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, timeout=0.3: calls.append(path))
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: calls.append("get_object_at"))
    monkeypatch.setattr(controller, "get_player", lambda: calls.append("get_player"))
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 60.0)
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: 92.0)
    monkeypatch.setattr(tick_cycle, "timed_click", lambda ser, pt, **kw: 255.0)
    monkeypatch.setattr(g.random, "random", lambda: 1.0)
    monkeypatch.setattr(g, "_now", lambda: 1000.0)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800)) == "ok"
    assert calls == []


def test_tick_chains_many_cycles_rather_than_one_per_engine_iteration(monkeypatch):
    """The engine loop costs ~1s, which is longer than a whole 1.8s cycle -- one cycle per
    engine iteration would halve the rate."""
    ran = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: (1640, 800))
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (ran.append(1), "ok")[1])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": 1000})
    times = iter([1000.0 + i * 2.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_index"] = 0
    g.tick(ctx)
    assert len(ran) > 1


def test_a_desync_resyncs_and_the_run_CONTINUES(monkeypatch):
    """USER's decision: resync and carry on. A desync is never a stop."""
    results = iter(["desync", "ok", "ok"])
    monkeypatch.setattr(g, "prepare", lambda *a, **k: (1640, 800))
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: next(results, "ok"))
    resyncs = []
    monkeypatch.setattr(g, "resync", lambda ctx: resyncs.append(1))
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": 1000})
    times = iter([1000.0 + i * 5.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_index"] = 0
    g.tick(ctx)
    assert resyncs
    assert ctx.state.safety_stopped is False


def test_resync_clears_the_movement_abort_flag(monkeypatch):
    """request_movement_abort is sticky -- left set, it silently kills every later flight."""
    cleared = []
    monkeypatch.setattr(controller, "clear_movement_abort", lambda: cleared.append(1))
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    g.resync(ctx)
    assert cleared == [1]


def test_no_mining_xp_for_the_stall_window_safety_stops(monkeypatch):
    """NOT a desync ceiling -- an ordinary desync never trips this. It fires when the cursor is
    clicking a dead pixel, which in this routine looks identical in the log to working."""
    monkeypatch.setattr(g, "prepare", lambda *a, **k: (1640, 800))
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": 1000})   # never rises
    times = iter([1000.0 + i * 10.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    # 20s, NOT the 60s default: CHAIN_MAX_S is 45, so with a 10s-per-iteration fake clock the
    # chain cap would return before a 60s stall could ever fire and the test would pass
    # vacuously. The constant under test is the comparison, not the shipped number.
    ctx.state.config = _cfg(xpStallStopS=20)
    ctx.state.data["g3t_index"] = 0
    ctx.state.data["g3t_xp"] = 1000
    ctx.state.data["g3t_xp_at"] = 1000.0
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "xp" in ctx.state.safety_reason.lower()


def test_a_rising_xp_reading_resets_the_stall_clock(monkeypatch):
    # STRICTLY increasing. A repeating sequence wraps back below its own high-water mark, the
    # stall clock stops resetting, and the test then passes only because CHAIN_MAX_S returns
    # first -- i.e. for the wrong reason, proving nothing about the reset.
    xps = iter(range(1000, 100000, 50))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: (1640, 800))
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": next(xps)})
    times = iter([1000.0 + i * 10.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg(xpStallStopS=20)
    ctx.state.data["g3t_index"] = 0
    g.tick(ctx)
    assert ctx.state.safety_stopped is False


def test_the_rotation_advances_one_rock_per_cycle(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "prepare",
                        lambda ctx, cfg, kp, lp, tile: (seen.append(tuple(tile)), (1, 2))[1])
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": 1000})
    times = iter([1000.0 + i * 8.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_index"] = 0
    g.tick(ctx)
    assert seen[:4] == [(10, 20, 0), (11, 20, 0), (12, 20, 0), (13, 20, 0)]


def test_tick_safety_stops_on_an_empty_rock_rotation(monkeypatch):
    """setup() refuses this today, but tick() must not crash if it is ever reached --
    a ZeroDivisionError is the wrong failure mode for an unattended routine."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg(rockTiles=[])
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "rocktiles" in ctx.state.safety_reason.lower()


def _full_inventory():
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)]
    inv += [_item("Granite (5kg)", 1700 + i, 830, 2 + i) for i in range(26)]
    return inv


def test_a_full_inventory_drops_the_granite(monkeypatch):
    dropped = {}
    monkeypatch.setattr(controller, "get_inventory", _full_inventory)
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.update(names=[i["name"] for i in items]),
                                            True)[1])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is True
    assert set(dropped["names"]) == {"Granite (5kg)"}


def test_the_drop_NEVER_touches_the_knife_or_the_log(monkeypatch):
    """A dropped log ends the run; a dropped knife ends it too. Both are on the keep list."""
    dropped = {}
    monkeypatch.setattr(controller, "get_inventory", _full_inventory)
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.update(names=[i["name"] for i in items]),
                                            True)[1])
    ctx = _Ctx()
    g.full_inventory_phase(ctx, _cfg())
    assert "Knife" not in dropped["names"]
    assert "Teak logs" not in dropped["names"]


def test_a_drop_that_frees_nothing_safety_stops(monkeypatch):
    """drop_items returns the CONFIRMED delta. False means nothing left the inventory, and a
    routine that keeps mining with no room is the silent loop this repo keeps paying for."""
    monkeypatch.setattr(controller, "get_inventory", _full_inventory)
    monkeypatch.setattr(controller, "drop_items", lambda ser, items: False)
    ctx = _Ctx()
    g.full_inventory_phase(ctx, _cfg())
    assert ctx.state.safety_stopped is True


def test_a_non_full_inventory_is_not_a_drop_tick(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is False
