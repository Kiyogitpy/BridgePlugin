import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import bridge_events
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
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3249, "y": 2351, "plane": 0})
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    # run_cycle ends with a second, in-place click on the rock one tick later --
    # the click that actually starts mining (see run_cycle). It touches the serial
    # port, so it is stubbed here; tests that care assert on it themselves.
    monkeypatch.setattr(tick_cycle, "fast_click", lambda ser: None)
    monkeypatch.setattr(controller, "move_to_human", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_human", lambda *a, **k: None)
    monkeypatch.setattr(controller, "mark_busy", lambda *a, **k: None)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_hover", lambda: {"option": "Use", "target": "Knife"})


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


def test_geometry_ACCEPTS_a_log_surplus_but_says_why_it_matters(monkeypatch):
    """A pile of logs is resolvable, not a reason to refuse: ensure_one_loose_log drops back to
    one on the first tick, and a bank restock deliberately comes home with a pile. MEASURED
    LIVE 2026-09-08 -- refusing here stopped a run one basket-Fill short of working."""
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1680, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1),
           _item("Teak logs", 1740, 800, slot=2)]
    ok, msg = g.check_geometry(_cfg(), inv)
    assert ok is True
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
    # Scale pinned at 1.0: this test is about the message naming the derived limit, not about
    # how fast the flight happens to be configured.
    ok, msg = g.check_geometry(_cfg(flightSpeedScale=1.0), inv)
    assert ok is False
    assert "1340" in msg          # |1740 - 400| -- the measurement must be IN the message
    # ...and the DERIVED limit, not a hand-picked one. MEASURED LIVE 2026-09-08: the config's
    # 220 was three times what the tick budget actually allows, so a geometry that could never
    # hit a tick would have been accepted.
    # Derived exactly as check_geometry derives it, so retuning a constant does not break a
    # test that is really about "the message names the real limit".
    cfg = _cfg(flightSpeedScale=1.0)
    limit = tick_cycle.max_reach_px(
        600.0 - cfg["logClickPhaseMs"]
        - tick_cycle.CLICK_COST_MEDIAN_MS       # log click delays the flight
        - tick_cycle.CLICK_LANDING_MEDIAN_MS,   # rock click only has to land
        cfg["flightSpeedScale"])
    assert f"{limit:.0f}px" in msg


def test_geometry_passes_and_still_reports_the_measurement(monkeypatch):
    # 60px from the log -- inside the ~82px the 600ms tick actually affords.
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1680, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_cfg(), inv)
    assert ok is True
    assert "60" in msg


def test_setup_NEVER_safety_stops_because_it_runs_before_the_login_gate():
    """setup() is called at controller.py:3539; is_fully_logged_in at 3552 and windowFocused at
    3558. At the login screen /inventory answers [] and no TICK packet has arrived, so running
    the assertions here would permanently stop every run before it began."""
    ctx = _Ctx()
    ctx.events = None                       # the worst case: no clock at all
    ctx.state.config = _cfg()
    g.setup(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_startup_pending"] is True


def test_geometry_refuses_an_inventory_holding_anything_it_would_DROP():
    """The keep list alone is not enough: a carried pickaxe would be binned on the first full
    inventory and the stall watch would stop the run 60s later blaming contested rocks."""
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Rune pickaxe", 1700, 830, 2), _item("Ring of dueling(8)", 1740, 830, 3)]
    ok, msg = g.check_inventory_is_clean(_cfg(), inv)
    assert ok is False
    assert "Ring of dueling(8)" in msg            # the offending item is NAMED
    assert "Rune pickaxe" not in msg              # keepItems defaults to ("pickaxe",)


def test_the_clean_inventory_check_accepts_the_knife_log_keepitems_and_granite():
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Rune pickaxe", 1700, 830, 2), _item("Granite (5kg)", 1740, 830, 3)]
    assert g.check_inventory_is_clean(_cfg(), inv)[0] is True


import tick_cycle


class _FakeTicks:
    def __init__(self, phase=0.0, tick=100, stale=False):
        self.phase = phase
        self.tick = tick
        self.stale = stale

    def is_stale(self, now):
        return self.stale


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


def test_run_cycle_FIRES_LATE_rather_than_leaving_the_fletch_to_complete(
        monkeypatch):
    """A click that slips into the next tick is worse than no click: it starts the NEXT cycle's
    mine at the wrong phase and desyncs everything after it.

    The flight is simply NOT STARTED -- there is nothing to abort. request_movement_abort() was
    called here once and was a no-op (no flight is in progress, and resync clears the flag
    immediately after); the firmware's `X` flush it stands for is only legal from inside
    move()'s own flight loop."""
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
    # scale pinned at 1.0: this test is about the CLICK cost being booked, not about how fast
    # the flight happens to be configured.
    assert g.run_cycle(ctx, _cfg(flightSpeedScale=1.0),
                       (1700, 800), (1740, 800), (1640, 800)) == "desync"
    # The rock IS clicked, just late: the log click has already started a fletch, and this is
    # the only thing that displaces it. Abandoning would cost the log and an 8-minute bank trip.
    assert clicked == [(1640, 800)]
    assert aborted == []


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
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})
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
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})
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
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})   # never rises
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
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": next(xps)})
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
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})
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


def test_a_full_inventory_with_nothing_droppable_safety_stops(monkeypatch):
    """The one path that bypasses the XP stall watch: tick() returns at the
    full_inventory_phase gate, so nothing downstream could ever catch this spin."""
    dropped = []
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, i) for i in range(28)])
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.append(items), True)[1])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is True
    assert ctx.state.safety_stopped is True
    assert dropped == []          # never even attempted


# --------------------------------------------------------------------------------------------
# C1 / I8 -- the cycle is PINNED to CYCLE_TICKS, and the rock click's tick offset is real config
# --------------------------------------------------------------------------------------------

def _ok_cycle_patches(monkeypatch, order=None):
    """The click primitives, stubbed. `order` collects the sequence when given."""
    def _in_place(ser, **kw):
        if order is not None:
            order.append("log-in-place")
        return 92.0

    def _timed(ser, pt, **kw):
        if order is not None:
            order.append(("rock", pt))
        return 200.0

    monkeypatch.setattr(tick_cycle, "click_in_place", _in_place)
    monkeypatch.setattr(tick_cycle, "timed_click", _timed)
    monkeypatch.setattr(g.random, "random", lambda: 1.0)      # never a deliberate miss
    monkeypatch.setattr(g, "_now", lambda: 1000.0)


def test_run_cycle_pins_the_critical_click_to_the_named_tick(monkeypatch):
    """THE C1 FIX. Once a cycle has fired, the next one is scheduled by TICK NUMBER through
    at_tick -- at_phase lands on whatever boundary comes next, which made the period whatever
    prepare()'s knife flight, log flight and HTTP read happened to take."""
    called = []
    monkeypatch.setattr(tick_cycle, "at_tick",
                        lambda ev, target, ms, **kw: (called.append((target, ms)), 60.0)[1])
    monkeypatch.setattr(tick_cycle, "at_phase",
                        lambda *a, **k: pytest.fail("at_phase must not be used once a tick "
                                                    "has been pinned"))
    _ok_cycle_patches(monkeypatch)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800), 205) == "ok"
    assert called == [(205, _cfg()["logClickPhaseMs"])]


def test_a_missed_target_tick_is_a_desync_not_a_late_click(monkeypatch):
    monkeypatch.setattr(tick_cycle, "at_tick", lambda *a, **k: "missed")
    _ok_cycle_patches(monkeypatch)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800), 205) == "desync"


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _ticking_events(clock, monkeypatch, start_tick=100, phase_ms=0.0):
    """A REAL EventStream whose TICK packets are synthesised from the fake clock, so the tick
    NUMBER advances exactly as the bridge's would as the clock is slept forward.

    A fixture whose tick number never moves (the _FakeEvents above) cannot see an off-by-one in
    the tick that gets RECORDED, which is what this file used to pin."""
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


def test_the_first_cycle_of_a_chain_enters_through_at_phase_and_records_the_TICK_IT_FIRED_IN(
        monkeypatch):
    """The first cycle has no previous critical tick to count from, so it enters through
    at_phase -- but the tick number it RECORDS has to be the tick the log click actually landed
    in, and that is the half this test used to get wrong.

    at_phase sleeps ACROSS a tick boundary. Reading the stream's tick number afterwards without
    pumping again answers the tick we LEFT: the recorded tick was one low, so tick() pinned the
    next cycle at that + CYCLE_TICKS = only TWO ticks after the real critical click, and a
    2-tick cycle re-fires knife-on-log before the skilling timer has expired and yields nothing.
    That is the exact defect the whole tick-pinning fix exists to remove, and it recurs after
    every chain start, every desync and every deliberate miss, all of which drop the pin and
    re-enter here.

    So: real at_phase, real TickModel, a clock that really advances -- and the assertion is that
    the recorded tick equals the tick the click landed in, confirmed against a fresh pump."""
    clock = _Clock()
    # 200ms into tick 100, so at_phase's 400ms-to-the-boundary + 60ms sleep lands in tick 101.
    events = _ticking_events(clock, monkeypatch, start_tick=100, phase_ms=200.0)
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: 92.0)
    monkeypatch.setattr(tick_cycle, "timed_click", lambda ser, pt, **kw: 200.0)
    monkeypatch.setattr(g.random, "random", lambda: 1.0)      # never a deliberate miss
    monkeypatch.setattr(g, "_now", clock.now)
    monkeypatch.setattr(g, "_sleep", clock.sleep)

    ctx = _Ctx()
    ctx.events = events
    assert g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800)) == "ok"

    events.pump(now=clock.now())
    assert events.ticks.tick == 101         # where the log click really landed
    assert ctx.state.data["g3t_fired_tick"] == 101


def test_tick_targets_CYCLE_TICKS_after_the_last_critical_click(monkeypatch):
    targets = []

    def _fake_run_cycle(ctx, cfg, kp, lp, rp, target_tick=None):
        targets.append(target_tick)
        ctx.state.data["g3t_fired_tick"] = 200 + len(targets)
        return "ok"

    monkeypatch.setattr(g, "prepare", lambda *a, **k: (1640, 800))
    monkeypatch.setattr(g, "run_cycle", _fake_run_cycle)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})
    times = iter([1000.0 + i * 5.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    g.tick(ctx)
    assert g.CYCLE_TICKS == 4   # MEASURED from USER's recording; see the constant
    assert targets[0] is None                       # first cycle of the chain
    assert targets[1] == 201 + g.CYCLE_TICKS
    assert targets[2] == 202 + g.CYCLE_TICKS


def test_a_desync_drops_the_pin_so_the_next_cycle_re_enters_on_a_clean_boundary(monkeypatch):
    targets = []

    def _fake_run_cycle(ctx, cfg, kp, lp, rp, target_tick=None):
        targets.append(target_tick)
        if len(targets) == 1:
            ctx.state.data["g3t_fired_tick"] = 300
            return "ok"
        return "desync"

    monkeypatch.setattr(g, "prepare", lambda *a, **k: (1640, 800))
    monkeypatch.setattr(g, "run_cycle", _fake_run_cycle)
    monkeypatch.setattr(g, "resync", lambda ctx: None)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})
    times = iter([1000.0 + i * 5.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    g.tick(ctx)
    assert targets[1] == 300 + g.CYCLE_TICKS
    assert targets[2] is None       # the desync dropped the pin


# --------------------------------------------------------------------------------------------
# I5 -- the desync guard books the click, not just the flight
# --------------------------------------------------------------------------------------------

def test_the_desync_guard_books_the_click_hold_as_well_as_the_flight():
    log_point, rock_point = (1740, 800), (1640, 800)
    flight_only = tick_cycle.estimate_flight_ms(100.0)
    assert g._flight_budget(log_point, rock_point) == pytest.approx(
        flight_only + tick_cycle.CLICK_LANDING_COST_MS)


def test_a_cycle_with_room_for_the_flight_but_not_the_click_is_a_desync(monkeypatch):
    """The exact hole I5 describes: room_for passed on the median flight alone, the click then
    spent a further 50-145ms hold plus a 30-90ms delay, and it landed in the NEXT tick while
    being recorded as "ok"."""
    clicked = []
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 60.0)
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: 0.0)
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (clicked.append(pt), 200.0)[1])
    monkeypatch.setattr(g.random, "random", lambda: 1.0)
    # 260ms into the tick (60 phase + 200 elapsed), so 340ms of budget is left. Chosen so the
    # flight ALONE fits and the flight-plus-landing does not -- which is the whole point.
    times = iter([1000.0, 1000.20, 1000.20, 1000.20])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert tick_cycle.estimate_flight_ms(100.0) < 340.0     # the flight ALONE would have fitted
    assert (tick_cycle.estimate_flight_ms(100.0)
            + tick_cycle.CLICK_LANDING_COST_MS) > 340.0     # ...with the landing, it does not
    # scale pinned at 1.0: this test is about the CLICK cost being booked, not about how fast
    # the flight happens to be configured.
    assert g.run_cycle(ctx, _cfg(flightSpeedScale=1.0),
                       (1700, 800), (1740, 800), (1640, 800)) == "desync"
    # The rock IS clicked, just late: the log click has already started a fletch, and this is
    # the only thing that displaces it. Abandoning would cost the log and an 8-minute bank trip.
    assert clicked == [(1640, 800)]


# --------------------------------------------------------------------------------------------
# I7 -- per-cycle occupancy is measured and logged
# --------------------------------------------------------------------------------------------

def test_every_cycle_logs_its_measured_occupancy(monkeypatch):
    """Spec live-checklist item 3 is "fit logClickPhaseMs from the measured ms per cycle" --
    both click primitives already returned elapsed ms and every call site threw them away."""
    lines = []
    monkeypatch.setattr(g.debug_log, "log", lambda cat, msg: lines.append(msg))
    monkeypatch.setattr(tick_cycle, "at_phase", lambda ev, ms, **kw: 60.0)
    _ok_cycle_patches(monkeypatch)

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1700, 800), (1740, 800), (1640, 800)) == "ok"
    assert any("occupancy" in line and "in-place 92ms" in line for line in lines)
    assert ctx.state.data["g3t_occupancy"] == [pytest.approx(60.0)]
    assert ctx.state.data["g3t_cycles"] == 1


def test_a_periodic_summary_makes_a_long_run_readable(monkeypatch):
    lines = []
    monkeypatch.setattr(g.debug_log, "log", lambda cat, msg: lines.append(msg))
    ctx = _Ctx()
    for i in range(g.OCCUPANCY_SUMMARY_EVERY):
        g._log_occupancy(ctx, "ok", 60.0, 92.0, 200.0, 350.0 + i)
    assert any(line.startswith("occupancy over the last") for line in lines)


# --------------------------------------------------------------------------------------------
# I9 -- neither the knife nor the log is clicked at an exact pixel
# --------------------------------------------------------------------------------------------

def test_the_knife_and_log_are_not_aimed_at_one_exact_pixel(monkeypatch):
    """~2,000 identical landings an hour on each of two points, with the whole cursor
    trajectory on the wire at 20Hz -- CLAUDE.md's fidget-anchor lesson exactly."""
    radii = []
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (radii.append(kw.get("aim_radius")), 200.0)[1])
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: radii.append(kw.get("aim_radius")))
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1640, "screenY": 800, "id": 1})

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    g.prepare(ctx, _cfg(), (1700, 800), (1740, 800), [10, 20, 0])
    assert g.AIM_RADIUS_PX > 0
    assert radii == [g.AIM_RADIUS_PX, g.AIM_RADIUS_PX]


# --------------------------------------------------------------------------------------------
# C4 -- the startup assertions are DEFERRED into tick(), because setup() runs before the gates
# --------------------------------------------------------------------------------------------

def _startup_ctx(monkeypatch, inv, stale=False, events=True):
    ctx = _Ctx()
    ctx.events = _FakeEvents() if events else None
    if events:
        ctx.events.ticks.stale = stale
    ctx.state.config = _cfg()
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    return ctx


def test_the_startup_check_DEFERS_on_an_empty_inventory(monkeypatch):
    """At the login screen /inventory answers [] -- that is "cannot tell yet", not "failed"."""
    ctx = _startup_ctx(monkeypatch, [])
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is False


def test_the_startup_check_DEFERS_on_a_stale_tick_clock(monkeypatch):
    ctx = _startup_ctx(monkeypatch, [_item("Knife", 1700, 800, 0),
                                     _item("Teak logs", 1740, 800, 1)], stale=True)
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is False


def test_setup_then_a_tick_at_the_login_screen_does_not_stop_the_run(monkeypatch):
    ctx = _startup_ctx(monkeypatch, [])
    g.setup(ctx)
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_startup_pending"] is True


def test_the_startup_check_STOPS_once_it_is_genuinely_answerable_and_wrong(monkeypatch):
    """get_object_at answers None from the autouse fixture, so no rock resolves."""
    ctx = _startup_ctx(monkeypatch, [_item("Knife", 1700, 800, 0),
                                     _item("Teak logs", 1740, 800, 1)])
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is True
    assert "rock" in ctx.state.safety_reason.lower()


def test_an_unanswerable_startup_stops_at_the_deferral_ceiling(monkeypatch):
    """So a misconfigured run refuses loudly instead of idling forever."""
    ctx = _startup_ctx(monkeypatch, [], events=False)
    ctx.state.data["g3t_startup_since"] = 1000.0
    monkeypatch.setattr(g, "_now", lambda: 1000.0 + g.STARTUP_DEFER_MAX_S + 1.0)
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is True
    assert "tick clock" in ctx.state.safety_reason.lower()
    assert "inventory" in ctx.state.safety_reason.lower()


def test_the_startup_happy_path_seeds_the_rotation_and_the_log_count(monkeypatch):
    """M15 -- only the refusals were pinned, and tick() depends on both of these."""
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1680, "screenY": 800, "id": 1})
    ctx = _startup_ctx(monkeypatch, [_item("Knife", 1700, 800, 0),
                                     _item("Teak logs", 1740, 800, 1)])
    assert g._startup_check(ctx, ctx.state.config) is True
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_index"] == 0
    assert ctx.state.data["g3t_logs"] == 1
    assert ctx.state.data["g3t_startup_pending"] is False


# --------------------------------------------------------------------------------------------
# C2 -- the XP stall clock only counts time during which tick() was actually running
# --------------------------------------------------------------------------------------------

def test_a_long_gap_between_ticks_stands_the_stall_clock_down(monkeypatch):
    """Breaks are ON by default (20-40 min apart, 2-8 min long) and tick() is not called during
    one, so no xp can arrive. The first check afterwards computed a several-hundred second
    stall and stopped the run with a message that was simply false."""
    monkeypatch.setattr(g, "_run_chain", lambda ctx: None)
    monkeypatch.setattr(g, "_now", lambda: 1400.0)
    ctx = _Ctx()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_xp"] = 1000
    ctx.state.data["g3t_xp_at"] = 1000.0
    ctx.state.data["g3t_last_tick_at"] = 1000.0     # a 400s break
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_xp_at"] == 1400.0    # re-seeded, not accumulated


def test_a_short_gap_between_ticks_leaves_the_stall_clock_alone(monkeypatch):
    """The engine loop's own ~1s cadence must not stand the watch down every tick, or the one
    belt this routine has never fires."""
    monkeypatch.setattr(g, "_run_chain", lambda ctx: None)
    monkeypatch.setattr(g, "_now", lambda: 1001.0)
    ctx = _Ctx()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_xp_at"] = 900.0
    ctx.state.data["g3t_last_tick_at"] = 1000.0
    g.tick(ctx)
    assert ctx.state.data["g3t_xp_at"] == 900.0


# --------------------------------------------------------------------------------------------
# I6 -- an empty /inventory read means "cannot tell", never "the items are gone"
# --------------------------------------------------------------------------------------------

def test_a_single_empty_inventory_read_does_NOT_stop_the_run(monkeypatch):
    """controller.get_inventory returns [] on ANY bridge error, so a 200ms hiccup would
    otherwise end an unattended run hours before anyone looked."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_empty_reads"] == 1


def test_a_run_of_empty_inventory_reads_is_eventually_a_stop(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_empty_reads"] = g.EMPTY_INVENTORY_STOP - 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is True


# --------------------------------------------------------------------------------------------
# I11 -- the inventory tab is open, and the knife's left click really is a selection
# --------------------------------------------------------------------------------------------

def _chain_ctx(monkeypatch, started):
    monkeypatch.setattr(g, "prepare", lambda *a, **k: (started.append("cycle"), (1640, 800))[1])
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})
    times = iter([1000.0 + i * 10.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    return ctx


def test_no_chain_starts_when_the_inventory_tab_will_not_open(monkeypatch):
    """gf_live27: after a pause/resume the stats tab was up and every inventory click landed on
    the wrong interface."""
    started = []
    ctx = _chain_ctx(monkeypatch, started)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: False)
    g.tick(ctx)
    assert started == []
    assert ctx.state.safety_stopped is False        # transient, not terminal


def test_no_chain_starts_when_the_knife_left_click_is_not_a_Use(monkeypatch):
    """A Menu Entry Swapper entry making the knife's left click "Drop" would bin the knife on
    cycle 1, and every cycle after it would look perfect in the log."""
    started = []
    ctx = _chain_ctx(monkeypatch, started)
    monkeypatch.setattr(controller, "get_hover", lambda: {"option": "Drop", "target": "Knife"})
    g.tick(ctx)
    assert started == []


def test_the_knife_hover_check_fails_CLOSED_when_it_cannot_read_the_hover(monkeypatch):
    """Unlike controller.hover_matches, which never blocks when it cannot verify: there is no
    post-hoc check here that would ever notice a dropped knife."""
    started = []
    ctx = _chain_ctx(monkeypatch, started)
    monkeypatch.setattr(controller, "get_hover", lambda: None)
    g.tick(ctx)
    assert started == []


def test_a_normal_chain_runs_when_the_tab_and_the_hover_both_check_out(monkeypatch):
    started = []
    ctx = _chain_ctx(monkeypatch, started)
    g.tick(ctx)
    assert started


# --------------------------------------------------------------------------------------------
# Both I11 gates sit BEFORE _stall_check and return from tick() without running a cycle, so a
# gate that never passes is a silent forever-loop with no terminal condition at all.
# --------------------------------------------------------------------------------------------

def test_a_tab_that_never_opens_is_eventually_a_stop_that_names_the_gate(monkeypatch):
    """open_inventory_tab reads /state's inventoryTabOpen, which is None -> False whenever the
    bridge read fails -- so this is a bridge outage, not an exotic case."""
    ctx = _chain_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: False)
    ctx.state.data["g3t_tab_fails"] = g.INVENTORY_TAB_STOP - 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "inventory tab" in ctx.state.safety_reason.lower()


def test_a_tab_failure_followed_by_a_success_does_NOT_stop_the_run(monkeypatch):
    answers = iter([False, True])
    ctx = _chain_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: next(answers))
    g.tick(ctx)
    assert ctx.state.data["g3t_tab_fails"] == 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_tab_fails"] == 0


def test_an_unreadable_hover_is_eventually_a_stop_that_names_the_likely_cause(monkeypatch):
    """controller.get_hover() returns None on a bridge error AND on any client built without
    MenuStateProvider -- so against a stale client this gate failing is the DEFAULT outcome, and
    this routine needs a rebuild anyway for its Routine-dropdown entry."""
    ctx = _chain_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "get_hover", lambda: None)
    ctx.state.data["g3t_hover_fails"] = g.KNIFE_HOVER_STOP - 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "menustateprovider" in ctx.state.safety_reason.lower()


def test_a_hover_failure_followed_by_a_success_does_NOT_stop_the_run(monkeypatch):
    answers = iter([None, {"option": "Use", "target": "Knife"}])
    ctx = _chain_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "get_hover", lambda: next(answers))
    g.tick(ctx)
    assert ctx.state.data["g3t_hover_fails"] == 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_hover_fails"] == 0


# --------------------------------------------------------------------------------------------
# M13 -- a failed rock resolve must not leave the knife selected
# --------------------------------------------------------------------------------------------

def test_a_failed_rock_resolve_CONSUMES_the_knife_selection(monkeypatch):
    """Otherwise the next cycle's knife click DESELECTS it and that cycle is silently wasted
    too -- and there is no test of the rock_point-is-None branch at all otherwise."""
    consumed = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(tick_cycle, "click_in_place",
                        lambda ser, **kw: (consumed.append(1), 92.0)[1])
    monkeypatch.setattr(g, "resync", lambda ctx: None)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3249, "y": 2351, "plane": 0, "miningXp": 1000})
    times = iter([1000.0 + i * 10.0 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    g.tick(ctx)
    assert consumed
    assert ctx.state.safety_stopped is False


# --------------------------------------------------------------------------------------------
# C3 -- the drop keeps everything on keepItems
# --------------------------------------------------------------------------------------------

def test_the_drop_keeps_a_carried_pickaxe(monkeypatch):
    """Nothing checks whether the pickaxe is worn or carried; a carried one dropped on the floor
    of a busy mine ends the run about a minute later with a message naming the wrong cause."""
    dropped = {}
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Rune pickaxe", 1700, 830, 2)]
    inv += [_item("Granite (5kg)", 1700 + i, 860, 3 + i) for i in range(25)]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.update(names=[i["name"] for i in items]),
                                            True)[1])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is True
    assert set(dropped["names"]) == {"Granite (5kg)"}
