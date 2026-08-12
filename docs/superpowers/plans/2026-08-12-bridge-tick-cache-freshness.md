# Bridge Tick-Cache Freshness & Client-Thread-Hop Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give bridge callers an on-demand `fresh=true` bypass for the six tick-cached providers, stop cache hits from paying a client-thread round trip, and reuse a persistent HTTP session on the Python side — collapsing six duplicated caching implementations into one shared, unit-tested mechanism along the way.

**Architecture:** Two new zero-RuneLite-API-dependency classes (`TickCache<T>`, `TickClock`) replace the six hand-rolled `cachedTick`/`cached<Thing>` field pairs in `BankProvider`, `NPCProvider`, `TileProvider`, `GroundItemProvider`, `GraphicsObjectProvider`, `ObjectProvider`. Each migrated provider's cache check moves in front of the existing `onClient(...)` client-thread dispatch instead of wrapping it, so a cache hit never touches the client thread. Python's `controller.py` gets a shared `requests.Session()` and `fresh=True` kwargs on the matching `get_*()` wrappers; `blast_furnace.py`'s sleep-a-tick bank workaround is replaced with `fresh=True`.

**Tech Stack:** Java 11 (RuneLite plugin, Gradle, JUnit 4.12 + Mockito 5.11.0), Python 3 (pytest).

## Global Constraints

- New Java classes live in `com.example.providers` and must have **zero RuneLite-API dependency** (no `Client`/`ClientThread` imports in `TickCache`) — this is what makes them unit-testable without mocking, per the spec.
- The freshness query param is exactly `fresh`, checked case-insensitively against the literal value `"true"` (`BridgeProvider.isFresh`).
- Only these six providers are migrated: `BankProvider`, `NPCProvider`, `TileProvider`, `GroundItemProvider`, `GraphicsObjectProvider`, `ObjectProvider`. Do not touch any other provider.
- Do not build a batched multi-endpoint snapshot call — explicit non-goal in the spec (`docs/superpowers/specs/2026-08-12-bridge-tick-cache-freshness-design.md`).
- `blast_furnace.py`'s sleep-a-tick workaround is the only routine migrated off this pattern this pass — no broader audit of other routines.
- Java tests: JUnit 4.12 + Mockito 5.11.0 (already declared in `build.gradle`); follow `src/test/java/com/example/tracker/NpcTrackerRegistryTest.java`'s style (hand-written stubs / mocks, no extra test infra).
- Python tests: pytest, in `ControllerAddon/tests/`; follow `ControllerAddon/tests/test_mcp_debug_server.py`'s conventions (`sys.path.insert` for the module import, `monkeypatch`/`MagicMock`).
- Windows environment: run Gradle via `gradlew.bat`, not `./gradlew`.

---

### Task 1: `TickCache<T>`

**Files:**
- Create: `src/main/java/com/example/providers/TickCache.java`
- Test: `src/test/java/com/example/providers/TickCacheTest.java`

**Interfaces:**
- Produces: `public class TickCache<T> { public T get(int currentTick, boolean forceFresh, Object key, Supplier<T> compute) }` — used by Task 3's six migrated providers.

- [ ] **Step 1: Write the failing test**

```java
package com.example.providers;

import org.junit.Test;

import java.util.function.Supplier;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

public class TickCacheTest
{
	private static Supplier<String> countingSupplier(int[] callCount, String value)
	{
		return () -> {
			callCount[0]++;
			return value;
		};
	}

	@Test
	public void cacheHitReturnsWithoutRecomputing()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		String first = cache.get(1, false, null, countingSupplier(calls, "a"));
		String second = cache.get(1, false, null, countingSupplier(calls, "b"));

		assertEquals("a", first);
		assertEquals("a", second);
		assertEquals(1, calls[0]);
	}

	@Test
	public void tickChangeRecomputes()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		cache.get(1, false, null, countingSupplier(calls, "a"));
		String second = cache.get(2, false, null, countingSupplier(calls, "b"));

		assertEquals("b", second);
		assertEquals(2, calls[0]);
	}

	@Test
	public void keyChangeRecomputes()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		cache.get(1, false, "radius=5", countingSupplier(calls, "a"));
		String second = cache.get(1, false, "radius=10", countingSupplier(calls, "b"));

		assertEquals("b", second);
		assertEquals(2, calls[0]);
	}

	@Test
	public void forceFreshAlwaysRecomputes()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		cache.get(1, false, null, countingSupplier(calls, "a"));
		String second = cache.get(1, true, null, countingSupplier(calls, "b"));

		assertEquals("b", second);
		assertEquals(2, calls[0]);
	}

	@Test
	public void nullResultStillCachesInsteadOfRecomputingEveryCall()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		String first = cache.get(1, false, null, countingSupplier(calls, null));
		String second = cache.get(1, false, null, countingSupplier(calls, "should not run"));

		assertNull(first);
		assertNull(second);
		assertEquals(1, calls[0]);
	}
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `gradlew.bat test --tests "com.example.providers.TickCacheTest"`
Expected: FAIL to compile — `TickCache` does not exist yet.

- [ ] **Step 3: Implement `TickCache`**

```java
package com.example.providers;

import java.util.Objects;
import java.util.function.Supplier;

/**
 * Caches a computed value until the game tick it was computed on changes, an optional caller-
 * supplied key changes, or the caller explicitly asks for a fresh value. Replaces the identical
 * cachedTick/cached&lt;Thing&gt; field pairs six providers used to hand-roll independently
 * (Bank, NPC, Tile, GroundItem, GraphicsObject, Object).
 */
public class TickCache<T>
{
	private int cachedTick = -1;
	private Object cachedKey;
	private T cached;
	private boolean hasValue;

	/**
	 * @param currentTick the caller's current tick number (see TickClock)
	 * @param forceFresh  when true, always recomputes regardless of tick/key
	 * @param key         extra cache-key component beyond the tick (e.g. ObjectProvider's
	 *                    radius); pass null for providers with no extra key
	 * @param compute     produces a fresh value; only invoked on an actual cache miss
	 */
	public T get(int currentTick, boolean forceFresh, Object key, Supplier<T> compute)
	{
		if (forceFresh || !hasValue || currentTick != cachedTick || !Objects.equals(key, cachedKey))
		{
			cached = compute.get();
			cachedTick = currentTick;
			cachedKey = key;
			hasValue = true;
		}
		return cached;
	}
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `gradlew.bat test --tests "com.example.providers.TickCacheTest"`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/providers/TickCache.java src/test/java/com/example/providers/TickCacheTest.java
git commit -m "Add TickCache: shared per-tick caching with an explicit force-fresh bypass"
```

---

### Task 2: `TickClock`

**Files:**
- Create: `src/main/java/com/example/providers/TickClock.java`
- Test: `src/test/java/com/example/providers/TickClockTest.java`

**Interfaces:**
- Consumes: `net.runelite.api.events.GameTick` (public no-arg constructor, confirmed via `javap` against `runelite-api-1.12.35.jar`), `net.runelite.client.eventbus.EventBus`/`Subscribe` (already used the same way in `SailingProvider`).
- Produces: `public class TickClock { public void register(EventBus eventBus); public int get(); @Subscribe public void onGameTick(GameTick event); }` — used by Task 3's six migrated providers and by `ControllerPlugin.startUp()`.

- [ ] **Step 1: Write the failing test**

```java
package com.example.providers;

import net.runelite.api.events.GameTick;
import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class TickClockTest
{
	@Test
	public void startsAtZero()
	{
		TickClock clock = new TickClock();
		assertEquals(0, clock.get());
	}

	@Test
	public void incrementsByOnePerGameTick()
	{
		TickClock clock = new TickClock();

		clock.onGameTick(new GameTick());
		assertEquals(1, clock.get());

		clock.onGameTick(new GameTick());
		clock.onGameTick(new GameTick());
		assertEquals(3, clock.get());
	}
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `gradlew.bat test --tests "com.example.providers.TickClockTest"`
Expected: FAIL to compile — `TickClock` does not exist yet.

- [ ] **Step 3: Implement `TickClock`**

```java
package com.example.providers;

import net.runelite.api.events.GameTick;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;

/**
 * A plain per-tick counter, readable from any thread without touching RuneLite's client thread.
 * onGameTick always runs on the client thread (RuneLite dispatches @Subscribe there), so this is
 * a standard single-writer/volatile-read pattern -- safe for a bridge HTTP handler thread to
 * read with no locking. The counter doesn't need to match client.getTickCount()'s actual value,
 * only to increment exactly once per real game tick, which is all TickCache needs to detect
 * staleness.
 */
public class TickClock
{
	private volatile int tick;

	public void register(EventBus eventBus)
	{
		eventBus.register(this);
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		tick++;
	}

	public int get()
	{
		return tick;
	}
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `gradlew.bat test --tests "com.example.providers.TickClockTest"`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/providers/TickClock.java src/test/java/com/example/providers/TickClockTest.java
git commit -m "Add TickClock: per-tick counter readable without a client-thread round trip"
```

---

### Task 3: Migrate the six tick-cached providers and wire `TickClock`

This task is a mechanical refactor of already-working code onto the pieces built in Tasks 1-2,
not new behavior — there's no meaningful unit test to TDD here (provider classes take a live
RuneLite `Client`, and this codebase's own convention, see `NpcTrackerRegistryTest`'s "Testing"
section in `docs/superpowers/specs/2026-08-06-npc-tracker-framework-design.md`, is that
`@Subscribe`-wired RuneLite integration is verified live, not through unit tests). Correctness
here is verified by a clean compile after each file and the live check in Task 6.

**Files:**
- Modify: `src/main/java/com/example/providers/BridgeProvider.java`
- Modify: `src/main/java/com/example/providers/BankProvider.java`
- Modify: `src/main/java/com/example/providers/NPCProvider.java`
- Modify: `src/main/java/com/example/providers/TileProvider.java`
- Modify: `src/main/java/com/example/providers/GroundItemProvider.java`
- Modify: `src/main/java/com/example/providers/GraphicsObjectProvider.java`
- Modify: `src/main/java/com/example/providers/ObjectProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`

**Interfaces:**
- Consumes: `TickCache<T>` (Task 1), `TickClock` (Task 2).
- Produces: six provider constructors each gain a trailing `TickClock tickClock` parameter (`GroundItemProvider`'s existing `ItemManager itemManager` parameter stays before it, same order as today). Nothing outside `ControllerPlugin` constructs these providers, so this is not a public API other tasks depend on.

- [ ] **Step 1: Add the shared `isFresh` helper to `BridgeProvider`**

Modify `src/main/java/com/example/providers/BridgeProvider.java`:

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import java.util.Map;

public abstract class BridgeProvider
{
	protected final Client client;
	protected final ClientThread clientThread;
	protected final BridgeHttpServer server;

	protected BridgeProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		this.client = client;
		this.clientThread = clientThread;
		this.server = server;
	}

	public abstract void register();

	protected Object onClient(SupplierLike supplier)
	{
		return com.example.util.RuneLiteUtil.runOnClientThread(client, clientThread, supplier::get);
	}

	/**
	 * True when the request explicitly asked to bypass a provider's TickCache (e.g. right after
	 * an action that just changed the state that endpoint reports).
	 */
	protected boolean isFresh(Map<String, String> query)
	{
		return "true".equalsIgnoreCase(query.get("fresh"));
	}

	@FunctionalInterface
	protected interface SupplierLike
	{
		Object get();
	}
}
```

- [ ] **Step 2: Migrate `BankProvider`**

Replace the field declarations, constructor, `register()`, and `getBank()` in
`src/main/java/com/example/providers/BankProvider.java` (leave `computeBank`, `buildSlotData`,
`buttonPoint`, `boundsPoint` untouched):

```java
	// Cached per game tick via TickCache -- banker.py polls this repeatedly for the whole
	// duration of a banking trip, and a bank can hold hundreds of slots each needing a
	// widget-bounds/composition lookup, so capping that work to once per tick (unless fresh=true
	// is passed, see BridgeProvider.isFresh) matters the same way it did before this moved into
	// a shared cache.
	private final TickClock tickClock;
	private final TickCache<Map<String, Object>> cache = new TickCache<>();

	public BankProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/bank", query -> getBank(isFresh(query)));
	}

	private Object getBank(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> onClient(this::computeBank));
	}
```

- [ ] **Step 3: Migrate `NPCProvider`**

Replace the field declarations, constructor, `register()`, and `getNPCs()` in
`src/main/java/com/example/providers/NPCProvider.java` (leave `computeNpcs`, `getNearestNpc`,
`getNpcByIndex`, `getNpcTypes`, `buildNpcData` untouched — only `/npcs` is tick-cached; the other
three endpoints on this provider stay always-live, unchanged):

```java
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public NPCProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/npcs", query -> getNPCs(isFresh(query)));
		server.registerQuery("/npc/nearest", query -> onClient(() -> getNearestNpc(query)));
		server.registerQuery("/npc/by-index", query -> onClient(() -> getNpcByIndex(query)));
		server.register("/npc-types", () -> onClient(this::getNpcTypes));
	}

	private Object getNPCs(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> onClient(this::computeNpcs));
	}
```

- [ ] **Step 4: Migrate `TileProvider`**

Replace the field declarations, constructor, `register()`, and `getTiles()` in
`src/main/java/com/example/providers/TileProvider.java` (leave `computeTiles` untouched):

```java
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public TileProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/tiles", query -> getTiles(isFresh(query)));
	}

	private Object getTiles(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> onClient(this::computeTiles));
	}
```

- [ ] **Step 5: Migrate `GroundItemProvider`**

Replace the field declarations, constructor, `register()`, and `getGroundItems()` in
`src/main/java/com/example/providers/GroundItemProvider.java` (leave `computeGroundItems`
untouched; `itemManager` stays a field):

```java
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public GroundItemProvider(Client client, ClientThread clientThread, BridgeHttpServer server, ItemManager itemManager, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.itemManager = itemManager;
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/ground-items", query -> getGroundItems(isFresh(query)));
	}

	private Object getGroundItems(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> onClient(this::computeGroundItems));
	}
```

- [ ] **Step 6: Migrate `GraphicsObjectProvider`**

Replace the field declarations, constructor, `register()`, and `getGraphicsObjects()` in
`src/main/java/com/example/providers/GraphicsObjectProvider.java` (leave `computeGraphicsObjects`
untouched):

```java
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public GraphicsObjectProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/graphics-objects", query -> getGraphicsObjects(isFresh(query)));
	}

	private Object getGraphicsObjects(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> onClient(this::computeGraphicsObjects));
	}
```

- [ ] **Step 7: Migrate `ObjectProvider`**

Replace the field declarations, constructor, `register()`, and `getObjects()` in
`src/main/java/com/example/providers/ObjectProvider.java` (leave `getObjectsAllPlanes`,
`computeObjects`, `walkWorldViewRecursively`, `walkScene`, `buildObjectData`,
`pointInsideShape`, `buildDebugObjectData`, `parseIntParam` untouched). `radiusKey` no longer
needs the old `-1`-means-"no filter" sentinel — `TickCache`'s `key` uses `Objects.equals`, so a
`null` radius is already a valid, distinct key on its own:

```java
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how. radius is passed as TickCache's extra key (see ObjectProvider's own historical note
	// on why a radius-bounded call and an unbounded one must not share a cache entry) -- unlike
	// the old cachedRadius field, TickCache's Objects.equals-based key comparison handles a null
	// radius as its own distinct key with no sentinel value needed.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public ObjectProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/objects", this::getObjects);
		server.registerQuery("/debug-objects-all-planes", query -> onClient(() -> getObjectsAllPlanes(query)));
	}

	private Object getObjects(Map<String, String> query)
	{
		Integer radius = null;
		String radiusStr = query.get("radius");
		if (radiusStr != null && !radiusStr.isEmpty())
		{
			try
			{
				radius = Integer.parseInt(radiusStr);
			}
			catch (NumberFormatException ignored)
			{
			}
		}
		Integer finalRadius = radius;

		return cache.get(tickClock.get(), isFresh(query), radius, () -> onClient(() -> computeObjects(finalRadius)));
	}
```

(`finalRadius` exists only because a lambda needs an effectively-final variable — `radius` itself
is reassigned in the `try` block above, so it can't be captured directly.)

- [ ] **Step 8: Wire `TickClock` into `ControllerPlugin`**

In `src/main/java/com/example/controller/ControllerPlugin.java`, add the import:

```java
import com.example.providers.TickClock;
```

In `startUp()`, immediately before the provider registration block (before the
`new GameStateProvider(...)` line), construct and register one shared clock:

```java
		TickClock tickClock = new TickClock();
		tickClock.register(eventBus);

```

Then replace these seven lines (same order, just adding `tickClock` to the six migrated
providers' constructor calls):

```java
		new BankProvider(client, clientThread, server, tickClock).register();
		new SailingProvider(client, clientThread, server, eventBus).register();
		new NPCProvider(client, clientThread, server, tickClock).register();
		new ObjectProvider(client, clientThread, server, tickClock).register();
		new GroundItemProvider(client, clientThread, server, itemManager, tickClock).register();
		new GraphicsObjectProvider(client, clientThread, server, tickClock).register();
		new TileProvider(client, clientThread, server, tickClock).register();
```

- [ ] **Step 9: Compile clean**

Run: `gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL, no errors.

- [ ] **Step 10: Run the full Java test suite**

Run: `gradlew.bat test`
Expected: BUILD SUCCESSFUL — all existing tests plus `TickCacheTest`/`TickClockTest` pass.

- [ ] **Step 11: Commit**

```bash
git add src/main/java/com/example/providers/BridgeProvider.java \
        src/main/java/com/example/providers/BankProvider.java \
        src/main/java/com/example/providers/NPCProvider.java \
        src/main/java/com/example/providers/TileProvider.java \
        src/main/java/com/example/providers/GroundItemProvider.java \
        src/main/java/com/example/providers/GraphicsObjectProvider.java \
        src/main/java/com/example/providers/ObjectProvider.java \
        src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Migrate the six tick-cached providers onto TickCache/TickClock with a fresh=true bypass"
```

---

### Task 4: `controller.py` — shared HTTP session + `fresh` kwargs

**Files:**
- Modify: `ControllerAddon/controller.py`
- Create: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: the `fresh=true` query param now honored by the bridge (Task 3).
- Produces: `controller._session` (module-level `requests.Session()`); `get_bank(fresh=False)`,
  `get_npcs(fresh=False)`, `get_objects(radius=None, fresh=False)`, `get_tiles(fresh=False)`,
  `get_ground_items(fresh=False)`, `get_graphics_objects(fresh=False)` — used by Task 5.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_controller.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller


class _FakeResponse:
    def __init__(self, json_body):
        self._json_body = json_body

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_body


def test_api_get_uses_shared_session_not_a_fresh_connection_per_call(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_dpi_scale", lambda: 1.0)
    monkeypatch.setattr(
        controller._session, "get",
        lambda url, params=None, timeout=0.3: calls.append((url, params)) or _FakeResponse({}),
    )

    controller.api_get("/state")

    assert len(calls) == 1
    assert calls[0][0] == "http://localhost:8080/state"


def test_get_bank_without_fresh_sends_no_fresh_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=0.3: captured.update(params=params) or {"open": False},
    )

    controller.get_bank()

    assert captured["params"] is None


def test_get_bank_with_fresh_true_sends_fresh_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=0.3: captured.update(params=params) or {"open": False},
    )

    controller.get_bank(fresh=True)

    assert captured["params"] == {"fresh": "true"}


def test_get_objects_combines_radius_and_fresh_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=1.0: captured.update(params=params) or [],
    )

    controller.get_objects(radius=10, fresh=True)

    assert captured["params"] == {"radius": 10, "fresh": "true"}


def test_get_objects_with_no_radius_and_no_fresh_sends_no_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=1.0: captured.update(params=params) or [],
    )

    controller.get_objects()

    assert captured["params"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_controller.py -v` (from `ControllerAddon/`)
Expected: FAIL — `controller._session` doesn't exist yet, and `get_bank`/`get_objects` don't
accept `fresh`.

- [ ] **Step 3: Add the shared session in `controller.py`**

Modify the "bridge HTTP helpers" section (around line 100):

```python
# ---------------- bridge HTTP helpers ----------------
_bridge_failing = False
_bridge_fail_count = 0
# Reused for the process lifetime instead of requests.get()'s implicit one-off connection --
# CONFIRMED this endpoint is polled many times a second (see ObjectProvider.java's own comment
# on /objects/ /tiles/ /ground-items), so a shared Session's TCP connection reuse meaningfully
# cuts per-call overhead to the local bridge.
_session = requests.Session()


def api_get(path, params=None, timeout=0.3):
    global _bridge_failing, _bridge_fail_count
    try:
        resp = _session.get(f"{API_BASE}{path}", params=params, timeout=timeout)
```

(Everything else in `api_get` is unchanged — only the `requests.get(...)` call becomes
`_session.get(...)`.)

- [ ] **Step 4: Add `fresh` kwargs to the six wrappers**

Replace `get_npcs`, `get_objects`, `get_tiles`, `get_ground_items`, `get_bank`,
`get_graphics_objects` in `controller.py`:

```python
def get_npcs(fresh=False):
    params = {"fresh": "true"} if fresh else None
    return api_get("/npcs", params=params) or []
```

```python
def get_objects(radius=None, fresh=False):
    # radius (tiles, optional): bounds the scene walk to a box around the player instead of the
    # whole loaded scene. CONFIRMED live 2026-08-02 -- building screen-projection data for every
    # object in the entire scene, every game tick, regardless of whether any caller actually cares
    # about it, was a real source of client stutter. Every caller here already filters results by
    # its own distance check afterward, so passing that same bound through costs nothing.
    #
    # timeout=1.0, not api_get's default 0.3 -- CONFIRMED live 2026-08-08: even a radius-bounded
    # /objects call still builds clickbox/screen-projection data for every object in range, and an
    # object-dense area (Wyrmscraig: ~2900 objects within a 40-tile radius) measured 390-420ms for
    # this specific endpoint while every other endpoint (e.g. /state) measured under 20ms. Sharing
    # the same 300ms budget as those cheap endpoints meant /objects timed out on every single tick
    # here, and a timed-out call fails-safe to an empty list indistinguishable from "genuinely
    # nothing nearby" -- golem_crafting's bank_phase read that as no bank at all and safety-stopped
    # even standing right next to one. 1.0s comfortably covers the measured worst case with
    # headroom, without the caller having to wait meaningfully longer than that if the bridge is
    # genuinely down.
    #
    # fresh=True bypasses the bridge's per-tick cache for this one call -- see get_bank's fresh
    # kwarg for the motivating case.
    params = {}
    if radius is not None:
        params["radius"] = radius
    if fresh:
        params["fresh"] = "true"
    return api_get("/objects", params=params or None, timeout=1.0) or []
```

```python
def get_tiles(fresh=False):
    params = {"fresh": "true"} if fresh else None
    return api_get("/tiles", params=params) or []
```

```python
def get_ground_items(fresh=False):
    params = {"fresh": "true"} if fresh else None
    return api_get("/ground-items", params=params) or []
```

```python
def get_bank(fresh=False):
    # fresh=True bypasses the bridge's per-tick cache for this one call -- pass it right after an
    # action that just changed bank state (a withdrawal/deposit) instead of waiting out a full
    # game tick for BankProvider's cache to naturally reflect it. See banker.withdraw_loadout's
    # own fresh parameter for the motivating call site.
    params = {"fresh": "true"} if fresh else None
    return api_get("/bank", params=params) or {"open": False}
```

```python
def get_graphics_objects(fresh=False):
    params = {"fresh": "true"} if fresh else None
    return api_get("/graphics-objects", params=params) or []
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_controller.py -v` (from `ControllerAddon/`)
Expected: PASS, 5 tests.

- [ ] **Step 6: Run the full Python test suite**

Run: `pytest tests/` (from `ControllerAddon/`)
Expected: all tests pass, including the pre-existing `test_mcp_debug_server.py` suite (confirms
the shared session change doesn't break anything importing `controller`).

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "Add a shared requests.Session and fresh= kwargs to the six tick-cached bridge wrappers"
```

---

### Task 5: Retire `blast_furnace.py`'s sleep-a-tick bank workaround

**Files:**
- Modify: `ControllerAddon/banker.py:379`
- Modify: `ControllerAddon/routines/blast_furnace.py:305-369`

**Interfaces:**
- Consumes: `controller.get_bank(fresh=False)` (Task 4).
- Produces: `banker.withdraw_loadout(ctx, config: dict, fresh: bool = False) -> None` — the two
  existing callers in `banker.py:586` and `banker.py:659` are unaffected (positional
  `(ctx, config)` calls; `fresh` defaults to `False`, unchanged behavior).

- [ ] **Step 1: Add the `fresh` parameter to `withdraw_loadout`**

In `ControllerAddon/banker.py`, replace:

```python
def withdraw_loadout(ctx, config: dict) -> None:
    entries = [e for e in config.get("withdraw", []) if e.get("name") and e.get("quantity", 1) > 0]

    if entries:
        set_quantity_mode(ctx, "one")

    for entry in entries:
        name = entry["name"].lower()
        quantity = entry["quantity"]

        bank = controller.get_bank()
```

with:

```python
def withdraw_loadout(ctx, config: dict, fresh: bool = False) -> None:
    """fresh: forces this call's /bank reads to bypass the bridge's per-tick cache -- pass True
    when the bank state may have just changed from something outside this call (e.g. a separate
    withdraw_loadout call for a different item immediately before this one) and waiting out a
    full game tick for the cache to reflect it isn't acceptable. See controller.get_bank's own
    fresh kwarg."""
    entries = [e for e in config.get("withdraw", []) if e.get("name") and e.get("quantity", 1) > 0]

    if entries:
        set_quantity_mode(ctx, "one")

    for entry in entries:
        name = entry["name"].lower()
        quantity = entry["quantity"]

        bank = controller.get_bank(fresh=fresh)
```

- [ ] **Step 2: Replace the sleep-a-tick workaround in `blast_furnace.py`**

In `ControllerAddon/routines/blast_furnace.py`, update `run_trip`'s docstring: replace the
sentence "A full-tick settle sleep after the bag's own withdrawal call is what actually
guarantees the next /bank read reflects the bag being gone." with "Passing `fresh=True` to the
materials withdrawal's own `withdraw_loadout` call (only when the bag was actually just
withdrawn) is what actually guarantees its `/bank` read reflects the bag being gone, without
waiting out a full tick for it."

Then replace:

```python
        banker.deposit_inventory(ctx, bank_cfg)
        if need_bag_withdraw:
            banker.withdraw_loadout(ctx, {"withdraw": [{"name": "coal bag", "quantity": 1}]})
            # Let a full game tick pass so the bridge's per-tick-cached /bank read reflects
            # the bag actually being gone before the materials withdrawal below searches it
            # for "Coal" -- see the docstring above.
            time.sleep(random.uniform(0.9, 1.3))
        banker.withdraw_loadout(ctx, bank_cfg)
```

with:

```python
        banker.deposit_inventory(ctx, bank_cfg)
        if need_bag_withdraw:
            banker.withdraw_loadout(ctx, {"withdraw": [{"name": "coal bag", "quantity": 1}]})
        # fresh=True (only actually needed when the bag was just withdrawn above) forces this
        # call's /bank reads to bypass the bridge's per-tick cache instead of waiting out a full
        # game tick for it to naturally reflect the bag being gone -- see the docstring above and
        # withdraw_loadout's own fresh parameter.
        banker.withdraw_loadout(ctx, bank_cfg, fresh=need_bag_withdraw)
```

- [ ] **Step 3: Byte-compile every touched Python file**

Run (from `ControllerAddon/`): `python -m py_compile banker.py routines/blast_furnace.py controller.py`
Expected: no output, exit code 0. Per `CLAUDE.md`'s live-debugging workflow, this catches syntax
errors in files (like `banker.py`) that aren't imported by any existing test.

- [ ] **Step 4: Run the full Python test suite**

Run: `pytest tests/` (from `ControllerAddon/`)
Expected: all tests still pass — `banker.py`/`blast_furnace.py` aren't imported by any existing
test, so this mainly re-confirms nothing else broke.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/banker.py ControllerAddon/routines/blast_furnace.py
git commit -m "Replace blast_furnace's post-withdrawal sleep-a-tick with fresh=True"
```

---

### Task 6: Live verification

Unit tests can't prove the RuneLite HTTP server actually serves `fresh=true` correctly, that
cache hits truly skip the client thread, or that `blast_furnace.py` still withdraws coal
correctly without its sleep — this needs the real client, per `CLAUDE.md`'s live-debugging
workflow and its "Distinguish infra/hardware failures from code bugs" section.

**Files:** none (verification only).

- [ ] **Step 1: Check for a serial-port conflict**

Run: `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"` (PowerShell)
Confirm no stray `controller.py`/`mcp_debug_server.py` process still holds the Arduino before
launching anything that needs it.

- [ ] **Step 2: Relaunch the RuneLite client**

Run: `gradlew.bat run` (from the repo root), and confirm in its output that the bridge starts:
`Bridge running on http://localhost:8080`.

- [ ] **Step 3: Confirm `fresh=true` bypasses the cache**

With the client logged in, run (PowerShell):
```powershell
Invoke-RestMethod "http://localhost:8080/bank"
Invoke-RestMethod "http://localhost:8080/bank?fresh=true"
```
Both should return valid JSON (bank open/closed state) with no error — confirms the new
`registerQuery`-based `/bank` route still works for both the cached and forced-fresh path.

- [ ] **Step 4: Confirm the other five migrated endpoints still respond correctly**

Run (PowerShell), while standing somewhere with NPCs/objects/ground items nearby:
```powershell
Invoke-RestMethod "http://localhost:8080/npcs" | Select-Object -First 3
Invoke-RestMethod "http://localhost:8080/objects?radius=10"
Invoke-RestMethod "http://localhost:8080/tiles" | Select-Object -First 3
Invoke-RestMethod "http://localhost:8080/ground-items"
Invoke-RestMethod "http://localhost:8080/graphics-objects"
```
Confirm each returns data shaped like before this change (same fields as documented in each
provider's `build*Data`/`compute*` methods) — this is the regression check that moving the cache
check in front of `onClient(...)` didn't change any provider's actual output.

- [ ] **Step 5: Launch `blast_furnace.py` and watch a coal-bag trip**

Launch per `CLAUDE.md`'s live-debugging workflow:
```powershell
python -u controller.py routines/blast_furnace.py *> blast_furnace_live.log 2>&1
```
(from `ControllerAddon/`, run in the background). Poll the log with `Read`, not a tight loop.
Confirm a trip that needs the coal bag withdrawn (i.e. `need_bag_withdraw` true — the bag wasn't
already held) completes its materials withdrawal correctly immediately after the bag withdrawal,
with no `time.sleep(random.uniform(0.9, 1.3))` pause between them and no "Coal not found in bank"
log line that would indicate a stale pre-withdrawal read.

- [ ] **Step 6: Stop the routine**

Press F1 (panic key) or let the trip complete, then confirm the process exited cleanly in the
log.
