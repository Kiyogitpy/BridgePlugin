# 3-tick Granite Mining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A routine that mines granite on a 3-tick cycle by placing two clicks inside one 600ms game tick, plus the tick-phase scheduler that makes that possible.

**Architecture:** A new `tick_cycle.py` schedules input against `bridge_events.TickModel`'s tick-phase clock. A new `routines/granite_3t.py` drives the cycle: during the two dead ticks it selects the knife, re-resolves the next rock's screen point over HTTP, and parks the cursor on the log; on the critical tick it clicks in place (no flight) and then makes exactly one short flight to the rock. `gathering.py` is not touched.

**Tech Stack:** Python 3.13, pytest, `requests` (bridge HTTP), `pyserial` (Arduino). No new dependencies. No Java until Task 8.

**Spec:** `docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md`

## Global Constraints

- **Working directory is `ControllerAddon/`** for every command in this plan.
- **Never run anything that opens the Arduino serial port while `controller.py` is running.** One process per COM port. This applies to Task 1's bench.
- **No HTTP between the log click and the rock click.** HTTP during the two dead ticks is fine and is required (the rocks' projections move as the player does).
- **Every test suite must be hermetic.** An autouse `_no_live_bridge` fixture, as in `tests/test_banker.py` and `tests/test_blast_furnace.py`. A test that reaches the running client is a test whose result depends on where the player is standing.
- **Every wait runs on the module's own `_now`/`_sleep`** so the fake-clock fixture makes budgets expire instantly in tests.
- **`simulate_mistakes=False` on every click in this routine.** A corrective reclick mid-cycle wrecks the cycle; in the inventory it drops the log.
- **Run the full suite before every commit:** `python -m pytest tests/ -q`. Also `python -m py_compile <each touched file>` — some modules are imported by no test, so a syntax error there surfaces only in a direct compile.
- Granite is 50 xp at Mining 45; rocks respawn in 5s; the wiki's target is ~116k xp/hr at Mining 70.

---

### Task 1: Measure the real click window (go/no-go gate)

The whole design rests on arithmetic: an in-place click (~92ms) plus one short flight (~170ms) plus a click (~85ms) fits in 600ms. Measure it before building anything on top. If p90 occupancy exceeds ~500ms, **stop and report** — the answer is the 4-tick celastrus variant, not tuning constants.

**Files:**
- Create: `ControllerAddon/tick_bench.py`

**Interfaces:**
- Consumes: `controller.move_to_human`, `controller.click_human`, `controller.probe_firmware_protocol`, `settings`
- Produces: nothing importable. A standalone script, like `firmware_bench.py`.

- [ ] **Step 1: Write the bench**

This is a measurement script, not library code, so it has no unit test — its output *is* the deliverable. Model it on `firmware_bench.py` (same "open the port, do a thing, print numbers" shape).

```python
# ControllerAddon/tick_bench.py
"""Measure how much of a 600ms game tick one 3-tick-granite critical window actually costs.

The whole 3-tick design (docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md)
rests on an ARITHMETIC claim: an in-place click (~92ms measured in giants_foundry) plus one
short flight (~170ms median at 52px per settings.HM2_MED_REF_MS) plus a click (~85ms median
per CLICK_HOLD_RANGE_S) is ~347ms median and ~560ms at p90, so it fits a 600ms tick. This
script replaces that arithmetic with a measurement on THIS machine.

It never touches the game: it moves the physical cursor between two screen points you give it
and times the sequence. Run it with the client in the foreground but the cursor over empty
desktop, or accept that it will click whatever is under those points.

NEVER run this while controller.py is running -- one process per COM port.

    python tick_bench.py --a 1700 800 --b 1750 700 --cycles 40
"""

import argparse
import statistics
import time

import serial

import controller
import settings


def _percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def one_window(ser, log_point, rock_point):
    """One critical tick: click in place on the parked point, fly to the rock, click.
    Returns (total_ms, in_place_ms, flight_and_click_ms)."""
    t0 = time.time()
    controller.click_human(ser)                       # the log click -- cursor already parked
    t1 = time.time()
    controller.move_to_human(ser, rock_point[0], rock_point[1], aim_radius=3)
    controller.click_human(ser)
    t2 = time.time()
    return (t2 - t0) * 1000.0, (t1 - t0) * 1000.0, (t2 - t1) * 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=settings.SERIAL_PORT)
    ap.add_argument("--baud", type=int, default=settings.SERIAL_BAUD)
    ap.add_argument("--a", nargs=2, type=int, required=True, metavar=("X", "Y"),
                    help="the parked point (stands in for the log's inventory slot)")
    ap.add_argument("--b", nargs=2, type=int, required=True, metavar=("X", "Y"),
                    help="the target point (stands in for a granite rock)")
    ap.add_argument("--cycles", type=int, default=40)
    args = ap.parse_args()

    controller.set_dpi_aware()
    controller.warn_if_pointer_acceleration()

    dist = ((args.a[0] - args.b[0]) ** 2 + (args.a[1] - args.b[1]) ** 2) ** 0.5
    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        time.sleep(2.0)                       # Leonardo reset after the port opens
        controller.probe_firmware_protocol(ser)
        totals, in_place, flights = [], [], []
        for _ in range(args.cycles):
            controller.move_to_human(ser, args.a[0], args.a[1], aim_radius=0)  # park
            time.sleep(0.4)                   # stand in for the dead ticks
            total, ip, fl = one_window(ser, args.a, args.b)
            totals.append(total)
            in_place.append(ip)
            flights.append(fl)

    print(f"\ndistance {dist:.0f}px over {args.cycles} cycles")
    print(f"  in-place click : median {statistics.median(in_place):6.1f}ms  "
          f"p90 {_percentile(in_place, 0.90):6.1f}ms")
    print(f"  flight + click : median {statistics.median(flights):6.1f}ms  "
          f"p90 {_percentile(flights, 0.90):6.1f}ms")
    print(f"  WHOLE WINDOW   : median {statistics.median(totals):6.1f}ms  "
          f"p90 {_percentile(totals, 0.90):6.1f}ms  max {max(totals):6.1f}ms")
    p90 = _percentile(totals, 0.90)
    budget = 600.0 - 60.0                     # tick minus the spec's logClickPhaseMs
    print(f"\n  budget at logClickPhaseMs=60 is {budget:.0f}ms -- "
          f"{'PASS' if p90 <= budget else 'FAIL'} at p90")
    if p90 > budget:
        print("  3-tick is NOT viable at this geometry. Either shorten the distance "
              "(zoom in / smaller client) or fall back to the 4-tick celastrus variant.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Check the settings names the bench uses actually exist**

Run: `python -c "import settings; print(settings.SERIAL_PORT, settings.SERIAL_BAUD)"`
Expected: prints a COM port and a baud rate. If those names differ, read `settings.py` and use the real ones — do not invent them.

- [ ] **Step 3: Compile it**

Run: `python -m py_compile tick_bench.py`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/tick_bench.py
git commit -m "tick_bench: measure the real 3-tick critical window before building on the arithmetic"
```

- [ ] **Step 5: HAND BACK TO THE USER**

This bench needs the Arduino and a client, so it cannot be run by an agent. Report to the user: the bench exists, here is the command, and **the plan's remaining tasks are conditional on its p90 landing under 540ms.** Do not proceed past this point without that number.

---

### Task 2: `tick_cycle.at_phase` — sleeping to a named point inside a tick

**Files:**
- Create: `ControllerAddon/tick_cycle.py`
- Test: `ControllerAddon/tests/test_tick_cycle.py`

**Interfaces:**
- Consumes: `bridge_events.EventStream` (its `.ticks` `TickModel`, `.pump()`), `bridge_events.GAME_TICK_MS`
- Produces:
  - `GAME_TICK_MS: float`
  - `tick_phase_ms(events, now) -> float | None` — None when the clock is unusable
  - `at_phase(events, target_ms, *, now_fn, sleep_fn) -> float | None` — sleeps to the next boundary plus `target_ms`, returns the phase actually woken at, or None when the clock is unusable

- [ ] **Step 1: Write the failing tests**

```python
# ControllerAddon/tests/test_tick_cycle.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_tick_cycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tick_cycle'`.

`EventStream.__init__(self, sock)` takes the socket positionally; `EventStream(sock=None)` is
fine as a keyword and is safe because the fixture above stubs `controller.poll_udp`.

- [ ] **Step 3: Write the implementation**

```python
# ControllerAddon/tick_cycle.py
"""Scheduling input INSIDE a game tick.

Every other routine in this repo is REACTIVE: click, then wait on a pushed signal confirmed by
HTTP, and the tick boundary is where the click LANDS. 3-tick granite is the opposite -- two
clicks must fall inside one named 600ms window, and being right about WHAT to click buys
nothing if the click is 80ms late. This module is the scheduler that makes that expressible.

Everything here takes now_fn/sleep_fn so it is testable with no client, no bridge and no serial
port (the fake-clock fixture pattern from tests/test_blast_furnace.py).

The clock underneath is bridge_events.TickModel, which is anchored on the bridge's own
`atMillis` -- Java's System.currentTimeMillis() at the REAL tick boundary -- rather than on when
Python happened to drain the packet. That anchoring is the only reason sub-tick scheduling is
possible at all; see TickModel.phase_ms's own docstring.

A stale or absent clock is answered with None, never with a guess. Callers must refuse to run:
without a tick phase this whole approach degrades to an ordinary 5-tick miner with extra steps,
and doing that silently would be worse than stopping.
"""

import math
import time

import bridge_events
import controller
import settings

GAME_TICK_MS = bridge_events.GAME_TICK_MS


def tick_phase_ms(events, now):
    """Milliseconds since the last tick boundary, or None when the clock cannot be trusted."""
    if events is None:
        return None
    if events.ticks.is_stale(now):
        return None
    return events.ticks.phase_ms(now)


def at_phase(events, target_ms, *, now_fn=time.time, sleep_fn=time.sleep):
    """Sleep until the NEXT tick boundary plus `target_ms`, returning the phase actually woken
    at (or None when the clock is unusable, in which case nothing was slept).

    Always the NEXT boundary, even when `target_ms` is still ahead in the current tick: a caller
    building a fixed-length cycle needs a predictable period more than it needs the odd tick
    saved."""
    if events is None:
        return None
    now = now_fn()
    events.pump(now=now)
    now = now_fn()
    if events.ticks.is_stale(now):
        return None
    sleep_fn((events.ticks.ms_until_next(now) + target_ms) / 1000.0)
    return tick_phase_ms(events, now_fn())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_tick_cycle.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run the whole suite and commit**

```bash
python -m pytest tests/ -q
python -m py_compile tick_cycle.py
git add ControllerAddon/tick_cycle.py ControllerAddon/tests/test_tick_cycle.py
git commit -m "tick_cycle: at_phase, sleeping to a named point inside a game tick"
```

---

### Task 3: `tick_cycle` — the click primitives and the window budget

**Files:**
- Modify: `ControllerAddon/tick_cycle.py`
- Test: `ControllerAddon/tests/test_tick_cycle.py`

**Interfaces:**
- Consumes: Task 2's module; `controller.move_to_human`, `controller.click_human`, `controller.request_movement_abort`; `settings.HM2_D_REF`, `settings.HM2_MED_REF_MS`, `settings.HM2_SLOPE_MS_PER_BIT`, `settings.HM2_MIN_MS`
- Produces:
  - `estimate_flight_ms(dist_px: float) -> float`
  - `click_in_place(ser, *, now_fn) -> float` (elapsed ms)
  - `timed_click(ser, point, *, aim_radius=0, now_fn) -> float` (elapsed ms)
  - `class Window(opened_at, phase_ms, budget_ms=GAME_TICK_MS)` with `spent_ms(now)`, `remaining_ms(now)`, `room_for(estimate_ms, now)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tick_cycle.py`. **`controller` is already imported by Task 2's header — do not add a second import.**

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_tick_cycle.py -v`
Expected: FAIL — `AttributeError: module 'tick_cycle' has no attribute 'estimate_flight_ms'`.

- [ ] **Step 3: Write the implementation**

Append to `tick_cycle.py`:

```python
def estimate_flight_ms(dist_px):
    """The fitted HM2 reach model's MEDIAN time for a distance, for budget arithmetic only.

    Not a prediction of any individual flight -- HM2_SIGMA is 0.368, so the p90 is ~1.6x this.
    Window.room_for is deliberately fed this median and given a caller-side margin rather than
    a p90, because refusing every cycle whose worst case might overrun would refuse most of
    them."""
    if dist_px <= 1:
        return 0.0
    doublings = math.log2(max(float(dist_px), 1.0) / settings.HM2_D_REF)
    return max(settings.HM2_MIN_MS,
               settings.HM2_MED_REF_MS + doublings * settings.HM2_SLOPE_MS_PER_BIT)


def click_in_place(ser, *, now_fn=time.time):
    """Click wherever the cursor already is. Returns elapsed ms.

    This is the primitive that makes the critical tick affordable: the log click costs no
    flight at all because the cursor was parked on it during the previous cycle's dead ticks."""
    t0 = now_fn()
    controller.click_human(ser)
    return (now_fn() - t0) * 1000.0


def timed_click(ser, point, *, aim_radius=0, now_fn=time.time):
    """Fly to a PRE-RESOLVED (x, y) and click. Returns elapsed ms.

    NO HTTP ANYWHERE IN THIS CALL, which is why it is not controller.click_entity: that one
    reads the bridge at departure, once per refresh leg and again at the settle -- the ~1s per
    click CLAUDE.md already records for deep-sea-trawling. Inside the critical tick there is no
    room for a single round trip, so the caller resolves the point during the dead ticks and
    hands it here as plain numbers."""
    t0 = now_fn()
    controller.move_to_human(ser, int(point[0]), int(point[1]), aim_radius=aim_radius)
    controller.click_human(ser)
    return (now_fn() - t0) * 1000.0


class Window:
    """How much of one 600ms tick a cycle has spent.

    `phase_ms` is where in the tick the window was OPENED -- the cycle starts part way into the
    tick by design (logClickPhaseMs), so that time is already spent before the first click."""

    def __init__(self, opened_at, phase_ms, budget_ms=GAME_TICK_MS):
        self.opened_at = opened_at
        self.phase_ms = phase_ms
        self.budget_ms = budget_ms

    def spent_ms(self, now):
        return self.phase_ms + (now - self.opened_at) * 1000.0

    def remaining_ms(self, now):
        return self.budget_ms - self.spent_ms(now)

    def room_for(self, estimate_ms, now):
        return self.remaining_ms(now) >= estimate_ms
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_tick_cycle.py -v`
Expected: 13 passed.

- [ ] **Step 5: Run the whole suite and commit**

```bash
python -m pytest tests/ -q
python -m py_compile tick_cycle.py
git add ControllerAddon/tick_cycle.py ControllerAddon/tests/test_tick_cycle.py
git commit -m "tick_cycle: HTTP-free click primitives and the per-tick window budget"
```

---

### Task 4: `granite_3t` — config, startup assertions, and the geometry probe

The assertions all refuse to start rather than degrading, reporting the measurement either way. The same resolution code doubles as a standalone probe so USER can capture `rockTiles`/`rockId` and check the geometry without launching the routine.

**Files:**
- Create: `ControllerAddon/routines/granite_3t.py`
- Test: `ControllerAddon/tests/test_granite_3t.py`

**Interfaces:**
- Consumes: `tick_cycle.estimate_flight_ms`; `controller.get_inventory`, `controller.get_object_at`, `controller.get_player`; `debug_log.log`
- Produces:
  - `DEFAULT_CONFIG: dict`
  - `get_config(bridge_config) -> dict`
  - `find_item(inv, name_substring) -> dict | None`
  - `log_count(inv, name_substring) -> int`
  - `rock_points(cfg) -> list[tuple[int, int]] | None` (None when any rock will not resolve)
  - `check_geometry(cfg, inv) -> tuple[bool, str]` — (ok, a message carrying the measurement)
  - `setup(ctx) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# ControllerAddon/tests/test_granite_3t.py
import pytest

import controller
from routines import granite_3t as g


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'routines.granite_3t'`.

If `from routines import granite_3t` does not work, check how the other suites import a routine (`tests/test_gathering.py`) and follow that; do not add `__init__.py` files unless the existing suites have them.

- [ ] **Step 3: Write the implementation**

```python
# ControllerAddon/routines/granite_3t.py
"""3-tick granite mining ("3t4g") at the Cape Conch mine.

Design: docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md -- read it before
changing any timing constant here. NOT YET RUN LIVE; the spec's live-verification checklist is
the first session's worklist.

THE SHAPE, and why it is not gathering.py. Every other routine here is reactive: click, wait on
a pushed signal, confirm over HTTP. This one is a SCHEDULE. Per 3-tick cycle:

    tick T+1 (dead)  select the knife; resolve the NEXT rock's screen point over HTTP;
                     fly to the log and PARK the cursor there
    tick T+2 (dead)  nothing -- the cursor rests on the log
    tick T  (crit)   click IN PLACE on the log (no flight) -> the 3-tick action fires;
                     ONE short flight to the rock; click

Two clicks in one 600ms tick, but only ONE flight, because item selection in OSRS is
client-side: clicking the knife sends no packet, so the selection and the flight to the log are
both paid during the dead ticks. That is the entire reason this is affordable on a physical
mouse -- two aimed flights would be ~510ms median and over budget at p90, against ~347ms for
one. See the spec's "The insight that makes it feasible on our hardware".

The rocks' projections MOVE as the player walks between them, so the next rock's point is
re-resolved every cycle -- during the dead ticks, where 1200ms of slack absorbs an HTTP read.
The knife and log points are resolved once per chain: the inventory fills from low slots
upward, so with those two in the first slots the granite stacks above them and they do not
move.
"""

import math
import random
import time

import controller
import debug_log
import tick_cycle

CATEGORY = "granite3t"

DEFAULT_CONFIG = {
    "enabled": True,

    # Captured once at the mine -- see the probe at the bottom of this file.
    "rockTiles": [],            # [[x, y, plane], x4], cycled in order
    # Granite rocks share one object id per variant; the probe prints it alongside each tile.
    # get_object_at needs it, and matching by id rather than name is what keeps a DEPLETED rock
    # (a different id, offering no actions) from resolving as a live one.
    "rockId": None,

    "knifeName": "knife",
    "logName": "teak logs",

    # Startup refuses above this. The flight is the whole cost of the critical tick, and it
    # scales with distance: settings.HM2_SLOPE_MS_PER_BIT is +120.5ms per DOUBLING.
    "maxReachPx": 220,
    # Where in the critical tick the log click lands. 60 leaves ~190ms of slack under the p90
    # tail (60 + 92 + 270 + 145 = 567). A guess; fit it from the logged per-cycle occupancy.
    "logClickPhaseMs": 60,
    # LIVE-VERIFY (spec checklist item 2): the wiki says the action must start "on the same tick
    # as moving to the next rock", and a click on tick T causes movement on T+1. If the yield
    # rate is poor at 0, try -1.
    "rockClickTickOffset": 0,
    # Deliberate imperfection: a drawn fraction of cycles place the rock click one tick late.
    # A machine that never misses a 3-tick cycle is itself the signature -- EVENT_MOUSE_CLICK_V2
    # carries inter-press timing to the server at 1ms resolution (CLAUDE.md, telemetry).
    "missChance": 0.03,

    "fullInventory": "drop",
    "xpStallStopS": 60,

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 20,
        "everyMaxMinutes": 40,
        "durationMinMinutes": 2,
        "durationMaxMinutes": 8,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        # OFF: this routine drops a full inventory itself. check_safety runs BEFORE tick() and
        # would otherwise win the race -- the same reason gathering.get_config forces it off.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def get_config(bridge_config):
    """DEFAULT_CONFIG only -- this routine has no ControllerConfig section, same as
    gemstone_crab.py and pest_control.py."""
    return DEFAULT_CONFIG


def find_item(inv, name_substring):
    needle = (name_substring or "").lower()
    for item in inv or []:
        if needle in (item.get("name") or "").lower():
            return item
    return None


def log_count(inv, name_substring):
    needle = (name_substring or "").lower()
    return sum(1 for item in (inv or []) if needle in (item.get("name") or "").lower())


def rock_points(cfg):
    """Live screen points for every configured rock tile, or None when any will not resolve.

    None means "cannot aim", never "no rock there" -- the caller refuses rather than guessing."""
    points = []
    for tile in cfg.get("rockTiles") or []:
        x, y, plane = tile[0], tile[1], tile[2]
        obj = controller.get_object_at(x, y, plane, cfg.get("rockId"))
        if not obj or obj.get("screenX") is None:
            return None
        points.append((int(obj["screenX"]), int(obj["screenY"])))
    return points or None


def check_geometry(cfg, inv):
    """(ok, message). The message ALWAYS carries the measurement, pass or fail, so a refusal
    tells USER how much to zoom in rather than merely that something is wrong."""
    knife = find_item(inv, cfg["knifeName"])
    if knife is None:
        return False, f"no {cfg['knifeName']!r} in the inventory"

    logs = log_count(inv, cfg["logName"])
    if logs == 0:
        return False, f"no {cfg['logName']!r} in the inventory"
    if logs > 1:
        return False, (f"{logs} x {cfg['logName']!r} in the inventory -- there must be exactly "
                       f"one, or knife-on-log opens a Make-X interface and eats a tick")

    log = find_item(inv, cfg["logName"])
    if log.get("screenX") is None:
        return False, f"{cfg['logName']!r} has no screen position (inventory tab closed?)"

    if len(cfg.get("rockTiles") or []) < 2:
        return False, "fewer than 2 rockTiles configured -- the rotation needs the movement "\
                      "stall between rocks to buy the second roll"

    points = rock_points(cfg)
    if points is None:
        return False, "at least one rock in rockTiles does not resolve on screen"

    reach = max(math.hypot(px - log["screenX"], py - log["screenY"]) for px, py in points)
    flight = tick_cycle.estimate_flight_ms(reach)
    limit = cfg["maxReachPx"]
    msg = (f"furthest rock is {reach:.0f}px from the log (limit {limit}px), "
           f"~{flight:.0f}ms median flight")
    if reach > limit:
        return False, msg + " -- zoom in, shrink the client, or turn the camera south"
    return True, msg


def setup(ctx):
    cfg = ctx.state.config
    inv = controller.get_inventory() or []

    ok, msg = check_geometry(cfg, inv)
    debug_log.log(CATEGORY, f"geometry: {msg}")
    if not ok:
        ctx.state.trigger_safety_stop(f"3-tick granite cannot start: {msg}")
        return

    # The ONE place this repo's "always tolerate ctx.events is None" rule inverts. There is no
    # fallback: without the tick clock there is no tick phase, and without the phase this is an
    # ordinary 5-tick miner with extra steps. Degrading to that silently would be worse than
    # stopping, so do not "fix" this into a polling fallback.
    if ctx.events is None or ctx.events.ticks.is_stale(_now()):
        ctx.state.trigger_safety_stop(
            "3-tick granite needs the UDP tick clock and it is absent or stale "
            "(is the bridge up? was controller.py started with --no-events?)")
        return

    ctx.state.data["g3t_index"] = 0
    ctx.state.data["g3t_logs"] = log_count(inv, cfg["logName"])
    debug_log.log(CATEGORY, f"ready: {len(cfg['rockTiles'])} rocks, "
                            f"{ctx.state.data['g3t_logs']} log(s)")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: 9 passed.

- [ ] **Step 5: Add the standalone probe**

Append to `routines/granite_3t.py`:

```python
def _probe():
    """Read-only geometry probe, for capturing rockTiles and checking the setup without
    launching the routine. Imports controller only -- never the serial port -- so it is safe
    alongside a running routine, same posture as bank_memory.py's own __main__.

        python routines/granite_3t.py --probe
    """
    inv = controller.get_inventory() or []
    player = controller.get_player() or {}
    print(f"player at ({player.get('x')},{player.get('y')},{player.get('plane')})")
    for item in inv:
        print(f"  slot {item.get('slot'):>2}  {item.get('name'):<20} "
              f"({item.get('screenX')},{item.get('screenY')})")
    granite = [o for o in (controller.get_objects(interactable=True) or [])
               if "granite" in (o.get("name") or "").lower()]
    print(f"\n{len(granite)} granite object(s) in range:")
    for obj in sorted(granite, key=lambda o: o.get("distance", 9999)):
        print(f"  [{obj.get('x')}, {obj.get('y')}, {obj.get('plane')}]  "
              f"id={obj.get('id')} dist={obj.get('distance')} "
              f"screen=({obj.get('screenX')},{obj.get('screenY')})")
    cfg = dict(DEFAULT_CONFIG)
    if cfg.get("rockTiles"):
        print("\n" + check_geometry(cfg, inv)[1])


if __name__ == "__main__":
    _probe()
```

- [ ] **Step 6: Verify the probe compiles and the suite still passes**

```bash
python -m py_compile routines/granite_3t.py
python -m pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/routines/granite_3t.py ControllerAddon/tests/test_granite_3t.py
git commit -m "granite_3t: config, startup assertions that report the measurement, geometry probe"
```

---

### Task 5: `granite_3t` — one cycle

**Files:**
- Modify: `ControllerAddon/routines/granite_3t.py`
- Test: `ControllerAddon/tests/test_granite_3t.py`

**Interfaces:**
- Consumes: Task 4's `DEFAULT_CONFIG`/`find_item`/`rock_points`; `tick_cycle.at_phase`, `tick_cycle.click_in_place`, `tick_cycle.timed_click`, `tick_cycle.Window`, `tick_cycle.estimate_flight_ms`, `tick_cycle.tick_phase_ms`
- Produces:
  - `next_rock_index(index, count) -> int`
  - `prepare(ctx, cfg, knife_point, log_point, rock_tile) -> tuple[int, int] | None` — the dead-tick half; returns the resolved rock point
  - `run_cycle(ctx, cfg, knife_point, log_point, rock_point) -> str` — one of `"ok"`, `"miss"` (a deliberate late click), `"desync"`, `"no_clock"`. Only `"ok"` continues without a resync.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_granite_3t.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'next_rock_index'`.

- [ ] **Step 3: Write the implementation**

Append to `routines/granite_3t.py` (above the `_probe` block):

```python
def next_rock_index(index, count):
    return (index + 1) % count if count else 0


def prepare(ctx, cfg, knife_point, log_point, rock_tile):
    """The DEAD-TICK half of a cycle. Returns the next rock's live screen point, or None.

    Three things happen here and the order is load-bearing:
      1. Click the knife. In OSRS this is a client-side SELECTION and sends no packet, which is
         exactly why it can live outside the critical tick.
      2. Re-resolve the rock's screen point over HTTP. The projection moves every cycle because
         the PLAYER moves between rocks; the 1200ms of dead time is where that read is
         affordable.
      3. Park the cursor on the log, so the critical tick's first click costs no flight.
    """
    tick_cycle.timed_click(ctx.ser, knife_point, aim_radius=0, now_fn=_now)

    x, y, plane = rock_tile[0], rock_tile[1], rock_tile[2]
    obj = controller.get_object_at(x, y, plane, cfg.get("rockId"))
    if not obj or obj.get("screenX") is None:
        return None

    controller.move_to_human(ctx.ser, int(log_point[0]), int(log_point[1]), aim_radius=0)
    return int(obj["screenX"]), int(obj["screenY"])


def run_cycle(ctx, cfg, knife_point, log_point, rock_point):
    """The CRITICAL TICK. "ok" | "desync" | "no_clock".

    No HTTP between the two clicks -- that is the invariant the whole module exists to protect.
    """
    phase = tick_cycle.at_phase(ctx.events, cfg["logClickPhaseMs"],
                                now_fn=_now, sleep_fn=_sleep)
    if phase is None:
        return "no_clock"

    window = tick_cycle.Window(opened_at=_now(), phase_ms=phase)
    tick_cycle.click_in_place(ctx.ser, now_fn=_now)

    # DELIBERATE IMPERFECTION. A human running 3t4g misses cycles and their rate visibly dips
    # and recovers; a machine that never misses is itself the signature, and per CLAUDE.md's
    # telemetry notes EVENT_MOUSE_CLICK_V2 carries inter-press timing to the server at 1ms
    # resolution independently of everything else. This is the honest replacement for the
    # engine's MISTAKE_MISCLICK_CHANCE, which is turned OFF everywhere in this routine: a
    # simulated near-miss plus its corrective reclick would wreck the cycle outright, and in the
    # inventory it would drop the log.
    if random.random() < cfg.get("missChance", 0.0):
        debug_log.log(CATEGORY, "deliberate miss -- placing the rock click a tick late")
        if ctx.events is not None:
            ctx.events.wait_ticks(1, now_fn=_now, sleep_fn=_sleep)
        tick_cycle.timed_click(ctx.ser, rock_point, aim_radius=3, now_fn=_now)
        return "miss"

    need = tick_cycle.estimate_flight_ms(
        math.hypot(rock_point[0] - log_point[0], rock_point[1] - log_point[1]))
    now = _now()
    if not window.room_for(need, now):
        # A click that slips into the NEXT tick is worse than no click at all: it starts the
        # next mine at the wrong phase and desyncs every cycle after it. Abandon this one and
        # let the caller resync on a clean boundary.
        controller.request_movement_abort()
        debug_log.log(CATEGORY,
                      f"desync: {window.spent_ms(now):.0f}ms of the tick spent with "
                      f"~{need:.0f}ms of flight still needed -- abandoning the rock click")
        return "desync"

    tick_cycle.timed_click(ctx.ser, rock_point, aim_radius=3, now_fn=_now)
    return "ok"
```

Note that `request_movement_abort()` sets a **sticky** flag. It is cleared by `resync()` in Task 6, which is the only caller that follows a `"desync"` return — do not clear it here, or the abort would be lifted before the flight it is meant to stop has unwound.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: 16 passed.

- [ ] **Step 5: Run the whole suite and commit**

```bash
python -m pytest tests/ -q
python -m py_compile routines/granite_3t.py
git add ControllerAddon/routines/granite_3t.py ControllerAddon/tests/test_granite_3t.py
git commit -m "granite_3t: one 3-tick cycle -- dead-tick prepare, critical-tick two clicks one flight"
```

---

### Task 6: `granite_3t` — the chain loop, resync, and the XP stall watch

**Files:**
- Modify: `ControllerAddon/routines/granite_3t.py`
- Test: `ControllerAddon/tests/test_granite_3t.py`

**Interfaces:**
- Consumes: Task 5's `prepare`/`run_cycle`/`next_rock_index`
- Produces:
  - `CHAIN_MAX_S: float` (45.0)
  - `resync(ctx) -> None`
  - `tick(ctx) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_granite_3t.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'resync'`.

- [ ] **Step 3: Write the implementation**

Append to `routines/granite_3t.py` (above `_probe`):

```python
CHAIN_MAX_S = 45.0      # hand control back to the engine at least this often


def resync(ctx):
    """Give up on the current cycle and re-enter on a clean tick boundary.

    USER's decision, and the reason there is no desync ceiling: a missed tick is an ordinary
    event in 3-tick mining -- a human's rate dips and recovers -- so this never stops the run.
    The XP stall watch in tick() is the only thing that does.

    Clearing the movement abort is not optional: request_movement_abort is STICKY, and left set
    it silently kills every flight for the rest of the session."""
    controller.clear_movement_abort()
    if ctx.events is not None:
        ctx.events.wait_ticks(1, now_fn=_now, sleep_fn=_sleep)
    else:
        _sleep(tick_cycle.GAME_TICK_MS / 1000.0)


def _stall_check(ctx, cfg, now):
    """True when the run should stop. Keyed on Mining XP, NOT on desyncs.

    This routine's failure mode is exceptionally silent: a perfectly-aimed click landing in the
    wrong tick looks identical in the log to a good one, so without this a contested rock or a
    nudged camera would click a dead pixel for hours. CLAUDE.md's standing lesson -- a silent
    loop is worse than a crash."""
    data = ctx.state.data
    xp = (controller.get_player() or {}).get("miningXp")
    if xp is None:
        return False
    if data.get("g3t_xp") is None or xp > data["g3t_xp"]:
        data["g3t_xp"] = xp
        data["g3t_xp_at"] = now
        return False
    since = now - (data.get("g3t_xp_at") or now)
    if since >= cfg["xpStallStopS"]:
        ctx.state.trigger_safety_stop(
            f"no Mining xp for {since:.0f}s -- the rocks may be contested, the camera moved, "
            f"or the client is stuttering")
        return True
    return False


def tick(ctx):
    cfg = ctx.state.config
    data = ctx.state.data

    if full_inventory_phase(ctx, cfg):
        return

    inv = controller.get_inventory() or []
    knife = find_item(inv, cfg["knifeName"])
    log = find_item(inv, cfg["logName"])
    if knife is None or log is None:
        ctx.state.trigger_safety_stop(
            f"the {cfg['knifeName']!r} or {cfg['logName']!r} is gone from the inventory")
        return

    # Checked between chains, never inside a cycle. USER says teak/mahogany are not consumed by
    # knife-on-log; if that is wrong, a log lasts one cycle and this catches it in seconds
    # instead of letting the run go quietly dry (spec live-checklist item 1).
    logs = log_count(inv, cfg["logName"])
    if logs < (data.get("g3t_logs") or logs):
        ctx.state.trigger_safety_stop(
            f"the {cfg['logName']!r} count fell {data['g3t_logs']} -> {logs}: knife-on-log IS "
            f"consuming the log, so this routine needs a supply loop it does not have")
        return
    data["g3t_logs"] = logs

    knife_point = (int(knife["screenX"]), int(knife["screenY"]))
    log_point = (int(log["screenX"]), int(log["screenY"]))
    tiles = cfg["rockTiles"]

    started = _now()
    while True:
        now = _now()
        if ctx.state.safety_stopped or now - started >= CHAIN_MAX_S:
            return
        if _stall_check(ctx, cfg, now):
            return
        if _inventory_full():
            return          # next tick drops

        # Hold off the idle drift for the whole cycle: it is documented in CLAUDE.md as having
        # fired mid-work before, and here it would drag the cursor off the parked log and break
        # the cycle with nothing in the log to say so.
        controller.mark_busy(3.0)

        index = data.get("g3t_index", 0) % len(tiles)
        rock_point = prepare(ctx, cfg, knife_point, log_point, tiles[index])
        if rock_point is None:
            debug_log.log(CATEGORY, f"rock {tiles[index]} did not resolve -- resyncing")
            resync(ctx)
            data["g3t_index"] = next_rock_index(index, len(tiles))
            continue

        result = run_cycle(ctx, cfg, knife_point, log_point, rock_point)
        data["g3t_index"] = next_rock_index(index, len(tiles))
        if result != "ok":
            resync(ctx)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: FAIL on `full_inventory_phase` / `_inventory_full` not existing — those arrive in Task 7. Add temporary stubs at the top of the module now so this task is independently green:

```python
def _inventory_full():
    return len(controller.get_inventory() or []) >= getattr(controller, "INVENTORY_SIZE", 28)


def full_inventory_phase(ctx, cfg):
    """Replaced in Task 7. Returning False here means 'this tick was not spent on a drop'."""
    return False
```

Re-run. Expected: 22 passed.

- [ ] **Step 5: Run the whole suite and commit**

```bash
python -m pytest tests/ -q
python -m py_compile routines/granite_3t.py
git add ControllerAddon/routines/granite_3t.py ControllerAddon/tests/test_granite_3t.py
git commit -m "granite_3t: chain loop, resync-and-carry-on, and an XP-keyed stall watch"
```

---

### Task 7: `granite_3t` — the full-inventory drop

**Files:**
- Modify: `ControllerAddon/routines/granite_3t.py`
- Test: `ControllerAddon/tests/test_granite_3t.py`

**Interfaces:**
- Consumes: `controller.drop_items` (returns the CONFIRMED inventory delta, not clicks issued)
- Produces: `full_inventory_phase(ctx, cfg) -> bool` (replaces the Task 6 stub), `_inventory_full() -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_granite_3t.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: FAIL — the Task 6 stub returns False unconditionally, so
`test_a_full_inventory_drops_the_granite` fails on `assert ... is True`.

- [ ] **Step 3: Replace the stub**

In `routines/granite_3t.py`, replace the Task 6 `full_inventory_phase` stub with:

```python
def full_inventory_phase(ctx, cfg):
    """True when this tick was spent dropping. Knife + log leaves 26 slots, so this comes due
    roughly every 26 cycles (~60-70s).

    controller.drop_items is the right primitive and its contract matters here: it returns the
    CONFIRMED inventory delta, not the number of clicks issued, and it polls DROP_CONFIRM_S
    before deciding -- a verification window shorter than the game's 0.6s tick reads a good drop
    as a failure. It also holds SHIFT once across the burst rather than tapping it per item,
    which is a wire-visible difference (EVENT_KEYBOARD carries every press with its gap)."""
    if not _inventory_full():
        return False

    keep = (cfg["knifeName"].lower(), cfg["logName"].lower())
    items = [i for i in (controller.get_inventory() or [])
             if not any(k in (i.get("name") or "").lower() for k in keep)]
    debug_log.log(CATEGORY, f"inventory full -- dropping {len(items)} granite")
    if not controller.drop_items(ctx.ser, items):
        ctx.state.trigger_safety_stop("inventory still full after dropping the granite")
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_granite_3t.py -v`
Expected: 26 passed.

- [ ] **Step 5: Run the whole suite and commit**

```bash
python -m pytest tests/ -q
python -m py_compile routines/granite_3t.py
git add ControllerAddon/routines/granite_3t.py ControllerAddon/tests/test_granite_3t.py
git commit -m "granite_3t: drop a full inventory of granite, keeping the knife and the log"
```

---

### Task 8: Panel wiring and documentation

Deferred to last on purpose: everything before this runs from the command line with no client
rebuild, so the first live session needs no Java. This task makes the routine selectable from
the Controller Settings panel, which **does** need a rebuild.

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `ControllerAddon/controller.py` (the `ROUTINE_FILES` dict)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Task 7's finished routine
- Produces: the enum constant `GRANITE_3T` and the `ROUTINE_FILES` key of the same name

- [ ] **Step 1: Read how an existing routine is wired**

Run: `grep -n "WOODCUTTING" ControllerAddon/controller.py src/main/java/com/example/controller/RoutineChoice.java`
Expected: one `ROUTINE_FILES` entry and one enum constant. Copy that shape exactly — CLAUDE.md
documents both as required, and the enum name must match the `ROUTINE_FILES` key.

- [ ] **Step 2: Add the enum constant**

In `RoutineChoice.java`, add a constant alongside the others following the file's existing
pattern (display name and ordering as the neighbouring entries do it):

```java
GRANITE_3T("3-Tick Granite"),
```

- [ ] **Step 3: Add the ROUTINE_FILES key**

In `ControllerAddon/controller.py`, add to the `ROUTINE_FILES` dict:

```python
    "GRANITE_3T": "routines/granite_3t.py",
```

- [ ] **Step 4: Verify both sides agree**

Run: `python -c "import controller; print(controller.ROUTINE_FILES['GRANITE_3T'])"`
Expected: `routines/granite_3t.py`

Run: `grep -c "GRANITE_3T" src/main/java/com/example/controller/RoutineChoice.java`
Expected: `1`

- [ ] **Step 5: Add the CLAUDE.md section**

Add a `## Current state of routines/granite_3t.py` section, placed with the other routine
sections, containing: that it was written 2026-09-08 from the wiki revisions named in the spec
and has **not been run live**; the cycle table (dead ticks select the knife, resolve the rock and
park; the critical tick clicks in place then makes one flight); that item selection being
client-side is what makes it affordable and why `click_entity` cannot be used inside the tick;
that `ctx.events` is a hard requirement here rather than an accelerator, and why that inverts
the repo-wide rule; that a desync never stops the run but a 60s Mining-XP stall does; and a
pointer to the spec's live-verification checklist as the first session's worklist.

- [ ] **Step 6: Commit**

```bash
python -m pytest tests/ -q
git add src/main/java/com/example/controller/RoutineChoice.java ControllerAddon/controller.py CLAUDE.md
git commit -m "granite_3t: wire into the Routine dropdown and document the routine"
```

- [ ] **Step 7: Tell the user a client rebuild is required**

`./gradlew.bat run` from the repo root before the dropdown entry appears. Everything else in
this plan works without it.

---

## After the plan

The routine has **never been run live**. The first session's worklist is the spec's
"Live-verification checklist", in order — and item 2 (`rockClickTickOffset`) is the highest-risk
unknown in the whole design, because the wiki's "on the same tick as moving to the next rock"
may mean the tick the walk *executes* rather than the tick it is *clicked*.
