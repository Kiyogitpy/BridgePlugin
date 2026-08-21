# Tick-Accurate Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let routines act on game-tick boundaries and react to pushed events, instead of sleeping wall-clock margins and polling HTTP.

**Architecture:** The Java plugin stamps its UDP events with the game tick and adds the four signals routines actually block on. A new Python module owns the UDP socket in a background thread, keeps a live mirror plus a tick clock, and offers tick-aligned waiting primitives. The mirror is an accelerator only — HTTP stays the source of truth for anything acted on, so a dropped packet costs latency, never a wrong click.

**Tech Stack:** Java 11 (RuneLite plugin, Gradle), Python 3.13 (pytest), UDP on 127.0.0.1:50555, HTTP bridge on localhost:8080.

**Spec:** `docs/superpowers/specs/2026-08-21-tick-accurate-timing-design.md`

## Global Constraints

- **The mirror is never authoritative.** Timing/waiting may read it; any destructive decision (which entity to click, whether an action succeeded) re-confirms via HTTP. From the spec: "a dropped packet must cost latency, never a wrong click."
- **Purely additive.** Absent the new calls, every existing routine must behave bit-for-bit as it does today. No existing routine is modified except `chinchompa_hunting.py` in Task 7.
- **Only one process may bind UDP 50555.** `controller.main()` already binds it via `make_udp_listener`. The new module takes that ownership over; it must not add a second bind. (A double bind already crashed a run this session with `WinError 10048`.)
- **The `on_event` routine contract is unchanged.** Routines that define `on_event(ctx, event_type, payload)` keep receiving every event, in order.
- **Java changes require `./gradlew.bat run`** (client rebuild + restart) before the Python side can see new fields.
- **Baseline to beat is 6.16s**, the current `chinchompa_hunting` retrieve dwell median — not the 10.18s original.
- **Run tests from `ControllerAddon/`:** `python -m pytest tests/ -q`. Also `python -m py_compile` any touched module — some are not imported by any test.

---

### Task 1: Expose the tick clock

**Files:**
- Modify: `src/main/java/com/example/providers/TickClock.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (the `onGameTick` handler, currently `udpSender.send("TICK")`)

**Interfaces:**
- Consumes: nothing.
- Produces: `TickClock.get() -> int` (exists), `TickClock.lastTickMillis() -> long` (new). UDP event `TICK` gains payload `{"tick": int, "atMillis": long}`.

- [ ] **Step 1: Add the timestamp to TickClock**

`TickClock` currently only increments a counter. Add the wall-clock stamp of the most recent tick:

```java
	private volatile int tick;
	private volatile long lastTickMillis;

	@Subscribe
	public void onGameTick(GameTick event)
	{
		tick++;
		lastTickMillis = System.currentTimeMillis();
	}

	public int get()
	{
		return tick;
	}

	/** Wall-clock time of the most recent GameTick, for clients that need tick PHASE and not
	 *  just the count -- everything in OSRS resolves on tick boundaries, so a caller that knows
	 *  only the number still cannot tell how far through the current tick it is. */
	public long lastTickMillis()
	{
		return lastTickMillis;
	}
```

- [ ] **Step 2: Put tick + timestamp on the TICK event**

In `ControllerPlugin.onGameTick`, replace the bare send. `ControllerPlugin` already constructs `TickClock tickClock` (around line 102) and shares it with the cached providers — reuse that instance, do not make a second one:

```java
	@Subscribe
	public void onGameTick(GameTick event)
	{
		Map<String, Object> tickPayload = new HashMap<>();
		tickPayload.put("tick", tickClock.get());
		tickPayload.put("atMillis", tickClock.lastTickMillis());
		udpSender.send("TICK", tickPayload);
		// ... rest of the existing handler (HP_CHANGED etc.) unchanged
```

Note `tickClock` must be a field on `ControllerPlugin` for this handler to reach it; it is currently a local in `startUp`. Promote it to a field, keeping the same single instance passed to the providers.

**Ordering caveat:** RuneLite subscriber order is not guaranteed, so `TickClock.onGameTick` may run after this one. Make `ControllerPlugin.onGameTick` compute the values itself rather than reading a possibly-stale clock:

```java
		tickClock.bump();                       // explicit, ordering-independent
		tickPayload.put("tick", tickClock.get());
		tickPayload.put("atMillis", tickClock.lastTickMillis());
```

and change `TickClock.onGameTick` into a plain `public void bump()` with no `@Subscribe`, removing its `register(EventBus)` call from `ControllerPlugin.startUp`. One writer, one ordering, no race.

- [ ] **Step 3: Build**

Run: `./gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Verify live**

Run `./gradlew.bat run`, wait for login, then from `ControllerAddon/`:

```bash
python -c "
import controller, time
s = controller.make_udp_listener()
time.sleep(3)
for t, p in controller.poll_udp(s):
    if t == 'TICK':
        print(t, p); break
"
```
Expected: `TICK {'tick': <int>, 'atMillis': <ms>}`. Two consecutive readings should differ by ~600ms.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/providers/TickClock.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Stamp the TICK event with the game tick number and wall-clock time"
```

---

### Task 2: Push object and ground-item spawns

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`

**Interfaces:**
- Consumes: `TickClock` field from Task 1.
- Produces: UDP events `OBJECT_SPAWNED`, `OBJECT_DESPAWNED` with `{id, x, y, plane, tick}`; `GROUND_ITEM_SPAWNED`, `GROUND_ITEM_DESPAWNED` with `{id, x, y, plane, qty, tick}`.

- [ ] **Step 1: Add the object spawn handlers**

RuneLite fires `GameObjectSpawned`/`GameObjectDespawned`. Use `WorldPoint.fromLocalInstance` for the position — per CLAUDE.md this is the *only* correct way to read a position out of the client, and a bare `getWorldLocation()` reports instance-template coordinates in the ~13000s inside instanced areas:

```java
	@Subscribe
	public void onGameObjectSpawned(GameObjectSpawned event)
	{
		sendObjectEvent("OBJECT_SPAWNED", event.getGameObject());
	}

	@Subscribe
	public void onGameObjectDespawned(GameObjectDespawned event)
	{
		sendObjectEvent("OBJECT_DESPAWNED", event.getGameObject());
	}

	private void sendObjectEvent(String type, GameObject obj)
	{
		if (obj == null)
		{
			return;
		}
		WorldPoint wp = WorldPoint.fromLocalInstance(client, obj.getLocalLocation());
		if (wp == null)
		{
			return;
		}
		Map<String, Object> payload = new HashMap<>();
		payload.put("id", obj.getId());
		payload.put("x", wp.getX());
		payload.put("y", wp.getY());
		payload.put("plane", wp.getPlane());
		payload.put("tick", tickClock.get());
		udpSender.send(type, payload);
	}
```

- [ ] **Step 2: Add the ground-item handlers**

```java
	@Subscribe
	public void onItemSpawned(ItemSpawned event)
	{
		sendGroundItemEvent("GROUND_ITEM_SPAWNED", event.getTile(), event.getItem());
	}

	@Subscribe
	public void onItemDespawned(ItemDespawned event)
	{
		sendGroundItemEvent("GROUND_ITEM_DESPAWNED", event.getTile(), event.getItem());
	}

	private void sendGroundItemEvent(String type, Tile tile, TileItem item)
	{
		if (tile == null || item == null)
		{
			return;
		}
		WorldPoint wp = WorldPoint.fromLocalInstance(client, tile.getLocalLocation());
		if (wp == null)
		{
			return;
		}
		Map<String, Object> payload = new HashMap<>();
		payload.put("id", item.getId());
		payload.put("qty", item.getQuantity());
		payload.put("x", wp.getX());
		payload.put("y", wp.getY());
		payload.put("plane", wp.getPlane());
		payload.put("tick", tickClock.get());
		udpSender.send(type, payload);
	}
```

Imports needed: `net.runelite.api.GameObject`, `net.runelite.api.Tile`, `net.runelite.api.TileItem`, `net.runelite.api.coords.WorldPoint`, and the four event classes from `net.runelite.api.events`.

- [ ] **Step 2b: Guard the volume**

A region load spawns thousands of objects at once and would flood the socket. Skip events while the scene is loading:

```java
		if (client.getGameState() != GameState.LOGGED_IN)
		{
			return;
		}
```
as the first check in both `sendObjectEvent` and `sendGroundItemEvent`.

- [ ] **Step 3: Build**

Run: `./gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Verify live**

Rebuild and restart the client, stand in the chinchompa cave, then:

```bash
python -c "
import controller, time
s = controller.make_udp_listener()
time.sleep(20)
seen = {}
for t, p in controller.poll_udp(s):
    if 'OBJECT' in t or 'GROUND_ITEM' in t:
        seen.setdefault(t, p)
for t, p in seen.items(): print(t, p)
"
```
Expected: at least one `OBJECT_SPAWNED`/`OBJECT_DESPAWNED` with plausible cave coordinates (x ~2510-2520, y ~9290-9310) and a `tick` field. Lay a trap by hand to force a `GROUND_ITEM_SPAWNED`.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Push object and ground-item spawn/despawn over the UDP event stream"
```

---

### Task 3: Push XP gains and stamp animation changes

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`

**Interfaces:**
- Consumes: `TickClock` field from Task 1.
- Produces: UDP event `XP_GAINED` with `{skill, xp, delta, tick}`; existing `ANIMATION_CHANGED` gains `tick` and `from`.

- [ ] **Step 1: Write the XP handler**

CLAUDE.md identifies XP as the definitive sub-tick success signal for any skilling action — it fires with the client's own `StatChanged`, is not tick-capped, and proves the skill check succeeded. Nothing currently consumes it.

```java
	private final Map<Skill, Integer> lastXp = new EnumMap<>(Skill.class);

	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		Skill skill = event.getSkill();
		int xp = event.getXp();
		Integer previous = lastXp.put(skill, xp);
		if (previous == null || xp <= previous)
		{
			return;         // first sighting, or no gain -- nothing to report
		}
		Map<String, Object> payload = new HashMap<>();
		payload.put("skill", skill.getName());
		payload.put("xp", xp);
		payload.put("delta", xp - previous);
		payload.put("tick", tickClock.get());
		udpSender.send("XP_GAINED", payload);
	}
```

Note the `previous == null` guard: the first `StatChanged` per skill after login carries the account's whole total, which would otherwise be reported as one enormous gain.

- [ ] **Step 2: Stamp the animation event**

Find the existing `ANIMATION_CHANGED` send in `ControllerPlugin` and add the tick plus the animation being left. Knowing which animation *ended* is what distinguishes a mid-sequence pause from a finished action — a `5212 -> -1` (trap check ending, re-lay still to come) reads identically to a final `5208 -> -1` without it:

```java
		payload.put("from", lastAnimation);      // the animation being left
		payload.put("tick", tickClock.get());
```

where `lastAnimation` is the field already tracked for change detection. Set it *after* building the payload.

- [ ] **Step 3: Build**

Run: `./gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Verify live**

Rebuild, restart, catch one chinchompa by hand:

```bash
python -c "
import controller, time
s = controller.make_udp_listener()
time.sleep(25)
for t, p in controller.poll_udp(s):
    if t in ('XP_GAINED', 'ANIMATION_CHANGED'): print(t, p)
"
```
Expected: an `XP_GAINED` with `skill: 'Hunter'` and a `delta` of 265 (a carnivorous chinchompa catch), and `ANIMATION_CHANGED` entries carrying both `from` and `tick`. No absurd first-gain delta.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Push XP gains and stamp animation changes with tick and previous animation"
```

---

### Task 4: TickModel (pure Python, no I/O)

**Files:**
- Create: `ControllerAddon/bridge_events.py`
- Test: `ControllerAddon/tests/test_bridge_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TickModel` with `update(tick: int, at_millis: int, now: float) -> None`, `tick -> int`, `phase_ms(now) -> float`, `ms_until_next(now) -> float`, `GAME_TICK_MS = 600`.

Keeping this pure — `now` injected, never `time.time()` internally — is what makes it testable without a client, the same way the existing 261 tests run.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_bridge_events.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bridge_events'`.

- [ ] **Step 3: Implement**

```python
"""Live view of the bridge's UDP event stream, plus a game-tick clock.

Shared infrastructure alongside controller.py, same as travel.py and banker.py.

This is an ACCELERATOR, never a source of truth. It answers "has X happened yet" so a routine
can stop sleeping conservative margins. Anything a routine ACTS on -- which entity to click,
whether an action succeeded -- is still confirmed against HTTP, because UDP is lossy and these
routines run unattended for hours on a real account. A dropped packet must cost latency, not a
wrong click.
"""

GAME_TICK_MS = 600      # OSRS server tick


class TickModel:
    """Where we are in the game-tick cycle, driven by the bridge's TICK event.

    `now` is injected rather than read internally so this is testable without a client.
    """

    def __init__(self):
        self.tick = None
        self._at_millis = None
        self._synced_at = None

    def update(self, tick, at_millis, now):
        self.tick = tick
        self._at_millis = at_millis
        self._synced_at = now

    def phase_ms(self, now):
        """Milliseconds since the last tick boundary, 0 if never synced."""
        if self._synced_at is None:
            return 0
        return (now - self._synced_at) * 1000.0

    def ms_until_next(self, now):
        """Milliseconds until the next boundary. Never negative: the client can stutter and a
        tick can overrun, and callers feed this straight into sleep()."""
        if self._synced_at is None:
            return 0
        return max(0.0, GAME_TICK_MS - self.phase_ms(now))
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_bridge_events.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/bridge_events.py ControllerAddon/tests/test_bridge_events.py
git commit -m "Add TickModel: game-tick phase tracking for routine timing"
```

---

### Task 5: EventStream — socket owner, mirror, and waiting primitives

**Files:**
- Modify: `ControllerAddon/bridge_events.py`
- Test: `ControllerAddon/tests/test_bridge_events.py`

**Interfaces:**
- Consumes: `TickModel` from Task 4; `controller.poll_udp(sock)` and `controller.make_udp_listener(port)`.
- Produces: `EventStream(sock)` with `pump(now) -> list[tuple[str, dict|None]]`, `wait_for_event(types, match=None, timeout_s=..., now_fn=...) -> dict|None`, `wait_ticks(n, now_fn=..., sleep_fn=...) -> None`, and attribute `ticks: TickModel`.

**Why a `pump` rather than a thread:** `controller.main()` already owns the socket and drains it each loop, and only one binder is allowed. `pump()` is called from that same loop — it feeds the mirror *and* returns the events for `on_event` dispatch, so there is exactly one drain and one owner. The blocking helpers call `pump()` themselves while they wait, which is what stops events piling up undrained in the OS buffer during a long `tick()`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_bridge_events.py -q -k "pump or wait_for or buffer"`
Expected: FAIL — `module 'bridge_events' has no attribute 'EventStream'`.

- [ ] **Step 3: Implement**

```python
import time

import controller

EVENT_BUFFER_MAX = 256      # closes the already-arrived race; deliberately not a log
EVENT_POLL_INTERVAL_S = 0.02


class EventStream:
    """Owns the drain of the bridge's UDP socket and keeps a short live view of it.

    controller.main() already binds the socket and drains it once per loop, and only one thing
    may bind port 50555 (a second bind crashed a run with WinError 10048). So this does not
    start a thread or open its own socket: main() calls pump() in place of poll_udp(), pump()
    feeds the mirror AND returns the events for on_event dispatch, and the blocking helpers
    below call pump() themselves so events cannot pile up undrained during a long tick().
    """

    def __init__(self, sock):
        self._sock = sock
        self.ticks = TickModel()
        self._recent = []

    def pump(self, now=None):
        """Drain the socket, update the mirror, return the events for the caller to dispatch."""
        now = time.time() if now is None else now
        events = controller.poll_udp(self._sock)
        for event_type, payload in events:
            if event_type == "TICK" and payload:
                self.ticks.update(payload.get("tick"), payload.get("atMillis"), now)
            self._recent.append((event_type, payload))
        if len(self._recent) > EVENT_BUFFER_MAX:
            del self._recent[:-EVENT_BUFFER_MAX]
        return events

    def wait_for_event(self, types, match=None, timeout_s=1.0,
                       now_fn=time.time, sleep_fn=time.sleep):
        """Payload of the first matching event, or None if `timeout_s` passes without one.

        Checks the buffer BEFORE waiting: the event is very often already in hand by the time a
        caller decides to wait for it, and blocking the full timeout on something we already
        received is the exact stall this module exists to remove.

        None is a normal outcome, not an error -- UDP is lossy, and the caller falls back to
        whatever HTTP check it would have used anyway.
        """
        if isinstance(types, str):
            types = (types,)
        deadline = now_fn() + timeout_s

        while True:
            for event_type, payload in self._recent:
                if event_type in types and (match is None or match(payload or {})):
                    return payload or {}
            if now_fn() >= deadline:
                return None
            sleep_fn(EVENT_POLL_INTERVAL_S)
            self.pump(now=now_fn())

    def wait_ticks(self, n, now_fn=time.time, sleep_fn=time.sleep):
        """Sleep until `n` tick boundaries have passed, ALIGNED to the boundary rather than
        counted in wall-clock milliseconds.

        This is the difference between "wait 1.2s" and "act on the tick": an unaligned sleep
        starts at whatever arbitrary phase the caller happened to notice something at, so it
        lands anywhere inside a 600ms window. Falls back to a plain sleep when the clock has
        never synced, so a caller is never worse off than it is today.
        """
        if self.ticks.tick is None:
            sleep_fn(n * GAME_TICK_MS / 1000.0)
            return
        remaining = self.ticks.ms_until_next(now_fn()) + (n - 1) * GAME_TICK_MS
        sleep_fn(remaining / 1000.0)

    def clear(self):
        """Drop buffered events -- for a caller that must not match something from before it
        started waiting."""
        self._recent.clear()
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_bridge_events.py -q`
Expected: all pass (5 from Task 4 + 6 here).

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/bridge_events.py ControllerAddon/tests/test_bridge_events.py
git commit -m "Add EventStream: UDP mirror with tick-aligned waiting primitives"
```

---

### Task 6: Wire EventStream into the engine

**Files:**
- Modify: `ControllerAddon/controller.py` (`main()`, around lines 2138 and 2152)
- Test: `ControllerAddon/tests/test_bridge_events.py`

**Interfaces:**
- Consumes: `bridge_events.EventStream` from Task 5.
- Produces: `ctx.events -> EventStream` available to every routine. `on_event` dispatch unchanged.

- [ ] **Step 1: Write the failing test**

`Context` is currently `__init__(self, ser: serial.Serial, state: ControllerState)` at
`controller.py:1963`. Two tests: the new attribute exists, and the old two-argument construction
still works (every existing caller and test uses it).

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_bridge_events.py -q -k ctx_exposes`
Expected: FAIL — unexpected keyword argument `events`.

- [ ] **Step 3: Implement**

Add the third parameter with a default:

```python
    def __init__(self, ser: serial.Serial, state: ControllerState, events=None):
        self.ser = ser
        self.state = state
        # Live view of the bridge's UDP stream (bridge_events.EventStream), or None when the
        # engine did not start one. Routines MUST tolerate None -- it is an accelerator, and
        # every path that uses it needs a working fallback without it.
        self.events = events
```

In `main()`:

```python
    udp = make_udp_listener(UDP_PORT)
    event_stream = bridge_events.EventStream(udp)
    ctx.events = event_stream
```

and replace the drain in the loop:

```python
            events = event_stream.pump()
```

leaving the `on_event` dispatch beneath it exactly as it is. `poll_udp` stays public and unchanged — other tools use it.

Import `bridge_events` at the top of `controller.py`. **Watch for a circular import:** `bridge_events` imports `controller`. Import it *inside* `main()`, or at the bottom of the module, whichever the existing file style favours; a plain top-of-file import will fail.

- [ ] **Step 4: Run to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/ -q && python -m py_compile controller.py bridge_events.py`
Expected: all tests pass (261 existing + the new ones), compile clean.

- [ ] **Step 5: Verify live**

Start any routine and confirm it still runs and still receives chat events:

```bash
cd ControllerAddon && python -u controller.py routines/chinchompa_hunting.py > /tmp/wire.log 2>&1
```
Expected: `caught one (N this session)` lines appear as before — those come via `on_event`, so they prove the dispatch survived the rewire.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/tests/test_bridge_events.py
git commit -m "Route the UDP drain through EventStream and expose it as ctx.events"
```

---

### Task 7: Adopt in chinchompa_hunting and A/B it

**Files:**
- Modify: `ControllerAddon/routines/chinchompa_hunting.py` (`_await_placement_settled`, `_wait_until_free`)
- Test: `ControllerAddon/tests/test_chinchompa_hunting.py`

**Interfaces:**
- Consumes: `ctx.events` from Task 6.
- Produces: no new public API.

`_await_placement_settled` currently polls `/ground-items` at 100ms to spot the placement transient, then records a wall-clock deadline two ticks out. Both halves get better: the event tells us the instant it lands, and `wait_ticks` aligns the wait to the boundary instead of starting mid-tick.

- [ ] **Step 1: Write the failing test**

```python
def test_placement_uses_the_event_stream_when_available(monkeypatch):
    """The GROUND_ITEM_SPAWNED event is the instant the trap hits the floor -- the same moment
    HunterPlugin logs "Trap placed". Polling for it costs up to a poll interval on top."""
    class _Events:
        def __init__(self):
            self.asked = []
        def wait_for_event(self, types, match=None, timeout_s=1.0):
            self.asked.append(types)
            return {"id": 10008, "x": 2512, "y": 9300, "plane": 0}
        def wait_ticks(self, n):
            self.ticks = n

    ctx = _Ctx()
    ctx.events = _Events()
    monkeypatch.setattr(ch.controller, "get_ground_items",
                        lambda **kw: (_ for _ in ()).throw(
                            AssertionError("must not poll when the event stream is available")))

    assert ch._await_placement_settled(ctx, (2512, 9300, 0)) is True
    assert ctx.events.ticks == ch.PLACEMENT_SETTLE_TICKS


def test_placement_falls_back_to_polling_without_an_event_stream(monkeypatch):
    """ctx.events is None under the old engine or if the listener died -- the routine must keep
    working exactly as it does today."""
    ctx = _Ctx()
    ctx.events = None
    calls = {"n": 0}

    def _items(**_kw):
        calls["n"] += 1
        return [_item(2512, 9300)]

    monkeypatch.setattr(ch.controller, "get_ground_items", _items)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    assert ch._await_placement_settled(ctx, (2512, 9300, 0)) is True
    assert calls["n"] >= 1
```

`_Ctx` in the existing test file needs an `events` attribute defaulting to `None`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_chinchompa_hunting.py -q -k placement`
Expected: FAIL — the first test's `get_ground_items` guard fires, because the routine still polls.

- [ ] **Step 3: Implement**

```python
def _await_placement_settled(ctx, tile, timeout_s=PLACEMENT_WAIT_TIMEOUT_S):
    events = getattr(ctx, "events", None)
    if events is not None:
        landed = events.wait_for_event(
            "GROUND_ITEM_SPAWNED",
            match=lambda p: p.get("id") == BOX_TRAP_ITEM_ID
            and (p.get("x"), p.get("y"), p.get("plane")) == tuple(tile),
            timeout_s=timeout_s,
        )
        if landed is None:
            return False
        events.wait_ticks(PLACEMENT_SETTLE_TICKS)
        return True

    # No event stream (older engine, or the listener died): the polling path, unchanged.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        items = controller.get_ground_items(fresh=True) or []
        if any(i.get("id") == BOX_TRAP_ITEM_ID
               and (i.get("x"), i.get("y"), i.get("plane")) == tuple(tile) for i in items):
            ctx.state.data["chin_free_at"] = time.time() + GAME_TICK_S * PLACEMENT_SETTLE_TICKS
            return True
        time.sleep(CONFIRM_POLL_INTERVAL_S)
    return False
```

Note the event path waits the ticks itself and never sets `chin_free_at`, so `_wait_until_free` becomes a no-op for it — correct, because the wait has already happened, aligned. The polling path keeps the deadline exactly as today.

- [ ] **Step 4: Run to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Measure against the baseline**

Rebuild the client if not already on the Task 1-3 build, then run for at least 40 retrieves:

```bash
cd ControllerAddon && python -u controller.py routines/chinchompa_hunting.py > /tmp/chins_events.log 2>&1
```

Compute retrieve dwell the same way as before:

```bash
python - /tmp/chins_events.log <<'EOF'
import re, sys
lines = list(open(sys.argv[1], encoding='utf-8', errors='replace'))
def t(l):
    m = re.match(r'\[(\d\d):(\d\d):(\d\d\.\d+)', l)
    return int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3)) if m else None
s = [(t(l), l) for l in lines if '[chins]' in l and ('trap at' in l or 'laying a trap' in l or 'picking it back up' in l)]
d = [b-a for (a, l0), (b, _) in zip(s, s[1:]) if a and b and b-a < 60 and 'trap at' in l0 and 'laying' not in l0]
print('n=%d median %.2fs (baseline 6.16s)' % (len(d), sorted(d)[len(d)//2]))
print('clickmiss', sum('is still ' in l for l in lines), 'noobj', sum('no trap object' in l for l in lines))
EOF
```

**Ship criterion:** median dwell below 6.16s AND `clickmiss`/`noobj` no worse than the current run. If it is not faster, revert Task 7 and keep Tasks 1-6 — the infrastructure is still worth having for other routines, and three timing changes were already reverted on measurement this session.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/chinchompa_hunting.py ControllerAddon/tests/test_chinchompa_hunting.py
git commit -m "Use pushed ground-item events and tick-aligned waits for trap placement"
```

- [ ] **Step 7: Record the result in CLAUDE.md**

Add the measured before/after to the `routines/chinchompa_hunting.py` section, alongside the existing timing notes. If it did not beat 6.16s, record *that* — the reverted experiments in that file are as valuable as the kept ones.

```bash
git add CLAUDE.md && git commit -m "Record tick-aligned timing result for chinchompa_hunting"
```

---

## Deliberately deferred

The spec lists a third primitive, `wait_until_actionable()`. **No task builds it**, on purpose:
nothing in Task 7 needs it, and it would ship untested against a real client. `refresh_legs` was
built that way — unused and unexercised — and when this session's routine finally adopted it, it
correlated with lay clicks failing outright and had to be backed out. Build it when a routine
actually needs it, with that routine's measurements to check it against.

## Notes for the executor

- **Tasks 1-3 need a client rebuild and restart** (`./gradlew.bat run`) before their live verification, and Python cannot see any new field until that happens.
- **Check for a stray `controller.py` before every live run** — `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"`. A leftover process holds UDP 50555 and the next run dies with `WinError 10048`. This bit twice this session.
- **The mirror never decides anything.** If a task tempts you to skip an HTTP confirmation because the event already said so, don't — that is the one constraint the whole design rests on.
- **Measure, don't reason, about timing.** Three timing changes were reverted on measurement this session after seeming obviously correct. `clickmiss` and `noobj` in the routine log are the counters that caught them.
