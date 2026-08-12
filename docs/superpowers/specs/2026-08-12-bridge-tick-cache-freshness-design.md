# Bridge tick-cache freshness & client-thread-hop reduction — design

Status: approved, not yet implemented.

## Purpose

Six bridge providers (`BankProvider`, `NPCProvider`, `TileProvider`, `GroundItemProvider`,
`GraphicsObjectProvider`, `ObjectProvider`) each independently hand-roll the same "recompute at
most once per game tick" caching pattern (a `cachedTick`/`cached<Thing>` field pair, first added
to `ObjectProvider` to fix measured client stutter from over-polling — see that file's own
header comment). Two real problems fall out of this today:

1. **No way to force a fresh read on demand.** Once a provider is tick-cached, a caller that just
   took an action (e.g. withdrew from the bank) has no way to say "skip the cache, give me the
   real current state" — it has to wait out a tick instead. `blast_furnace.py` documents this
   exact workaround: it sleeps a full game tick after a withdrawal specifically so the bridge's
   per-tick-cached `/bank` read reflects it, rather than resolving against a stale, pre-
   withdrawal snapshot.
2. **Cache hits still pay a client-thread round trip.** Every provider's `onClient(...)` call
   unconditionally schedules onto RuneLite's client thread (`clientThread.invokeLater` + block on
   a `CountDownLatch`), even when the cached value already answers the request. This cost is
   real here specifically: `controller.py`'s main loop polls on a fixed `STATE_POLL_INTERVAL`,
   not gated by game ticks, so `routine.tick()` — and the several `get_*()` calls it makes —
   fires many times within a single 600ms game tick. Post-cache-hit, none of those repeat calls
   need the client thread at all, but today they all still queue for it.

This iteration fixes both: an opt-in `fresh=true` query param that bypasses the cache for a
single call, and a cache-hit path that never touches the client thread. It also collapses the
six duplicated caching implementations into one shared, unit-tested mechanism, and fixes a small
latent bug that falls out of that refactor for free (see Architecture, `TickCache`).

## Non-goals

- Migrating the other ~15 always-live providers (`Player`, `Inventory`, `Combat`, `Equipment`,
  `Skill`, ...) onto `TickCache`. They recompute fresh on every call today with no reported
  staleness problem — nothing here suggests they need caching.
- A batched multi-endpoint "snapshot" call (e.g. one request returning npcs+objects+inventory
  together to cut round-trip *count*, not per-call cost). Real idea, but a materially bigger,
  separate initiative — new aggregation endpoint, call-site rewrites across every routine. Left
  as a future initiative.
- Auditing every routine for other "sleep to let the cache roll over" workarounds.
  `blast_furnace.py`'s is the known, motivating one and gets fixed here as end-to-end validation
  of the new mechanism; a fuller audit across all routines is a separate pass.
- Any change to `Executors.newCachedThreadPool()` sizing/strategy in `BridgeHttpServer`. Cache
  hits no longer occupying the client-thread queue is itself a secondary win for the pool's other
  work; the pool's own configuration isn't touched.

## Architecture

Two new classes, both in `src/main/java/com/example/providers/`, both with zero RuneLite-API
dependency (deliberately — see Testing):

- **`TickCache<T>`** — replaces the six duplicated `cachedTick`/`cached<Thing>` field pairs.

  ```java
  public class TickCache<T> {
      private int cachedTick = -1;
      private Object cachedKey;
      private T cached;
      private boolean hasValue;

      public T get(int currentTick, boolean forceFresh, Object key, Supplier<T> compute) {
          if (forceFresh || !hasValue || currentTick != cachedTick || !Objects.equals(key, cachedKey)) {
              cached = compute.get();
              cachedTick = currentTick;
              cachedKey = key;
              hasValue = true;
          }
          return cached;
      }
  }
  ```

  `key` generalizes `ObjectProvider`'s existing extra `cachedRadius` check (the only provider
  with a compound cache key today) — every other provider passes `null`. Tracking `hasValue`
  instead of the current code's `cached == null` check is a small correctness fix: today, a
  provider whose compute genuinely returns `null`/empty would look "never cached" forever and
  recompute on every single call — `hasValue` makes an empty result cache correctly like any
  other value.

- **`TickClock`** — one shared, per-tick counter, decoupled from `client.getTickCount()` so
  reading it never needs the client thread:

  ```java
  public class TickClock {
      private volatile int tick;
      public void register(EventBus eventBus) { eventBus.register(this); }
      @Subscribe
      public void onGameTick(GameTick event) { tick++; }
      public int get() { return tick; }
  }
  ```

  `onGameTick` always runs on the client thread (RuneLite dispatches `@Subscribe` there), so this
  is a standard single-writer/volatile-read pattern — safe to read from any HTTP handler thread
  with no locking. It doesn't need to match `client.getTickCount()`'s actual value; it only needs
  to increment exactly once per real game tick, which is all `TickCache` needs to detect
  staleness. One instance is constructed once (in `ControllerPlugin`, alongside where the
  existing `EventBus` is already threaded through to `SailingProvider`) and injected into each of
  the six migrated providers' constructors.

**Migrating the six providers.** Each gets a `TickCache<T>` field, the injected `TickClock`, and
a `BridgeProvider.isFresh(query)` helper (new, small: `"true".equalsIgnoreCase(query.get("fresh"))`).
The five currently using `server.register(...)` (no query access) switch to
`server.registerQuery(...)` — `ObjectProvider` already uses `registerQuery` for its `radius`
param and just adds the `fresh` check alongside it. The key shape change, which is what makes the
client-thread-hop reduction work: the cache check moves in *front of* `onClient(...)` instead of
wrapping it.

```java
// before
server.register("/bank", () -> onClient(this::getBank));

// after
server.registerQuery("/bank", query ->
    cache.get(tickClock.get(), isFresh(query), null, () -> onClient(this::computeBank)));
```

`onClient(this::computeBank)` is passed as `TickCache`'s lazy `Supplier` — it only actually runs
(and only then touches the client thread) on a genuine cache miss or `fresh=true`. A cache hit
returns straight from `TickCache` on whatever thread the HTTP handler is already running on.

**Python side (`ControllerAddon/controller.py`):**

- `api_get`'s transport moves from the module-level `requests.get(...)` to one shared
  `requests.Session()` constructed once at import time, so repeated polls to the local bridge
  reuse a TCP connection instead of paying a fresh handshake every call. `api_get`'s signature
  and error handling are unchanged.
- `get_bank()`, `get_npcs()`, `get_objects(radius=None)`, `get_tiles()`, `get_ground_items()`,
  `get_graphics_objects()` each gain a `fresh: bool = False` kwarg, forwarded as
  `params={"fresh": "true"}` when set.
- `blast_furnace.py`'s post-withdrawal sleep-a-tick workaround is replaced with a
  `get_bank(fresh=True)` call, as the concrete proof this solves the motivating problem.

**Data flow:** real game tick fires → `TickClock.onGameTick` bumps its `volatile int` (client
thread, cheap) → an HTTP GET lands on a bridge worker thread → the provider reads `TickClock.get()`
(no client-thread contact) and checks `isFresh(query)` → `TickCache` either returns the cached
value immediately (hit) or runs `onClient(this::compute)` (miss / `fresh=true`), which is where
the existing client-thread round trip still happens, now conditionally.

## Error handling

- Missing or malformed `fresh` query param → `isFresh` returns `false`; existing callers that
  never pass it see no behavior change.
- Two requests racing the same tick before either has cached a value can both independently
  trigger a recompute — this race exists in the current code today too (no locking there
  either), so this is unchanged, not introduced. The client thread serializes the actual
  `compute` work regardless, so a duplicate trigger costs one harmless extra round trip, not a
  correctness problem.
- `TickClock` resets to 0 on plugin reload — harmless; it only needs to detect "has a tick passed
  since I last computed," never an absolute value.
- `requests.Session()` reconnecting after the bridge process restarts (RuneLite closed/reopened)
  is handled by `requests` itself (a `Session` pools and reconnects, it doesn't pin one dead
  connection) — no new handling needed in `api_get`'s existing try/except/fail-counting logic.

## Testing

- **`TickCacheTest`** (new, pure logic, no RuneLite API involved — no client/Mockito needed):
  cache hit returns without recomputing; miss recomputes; tick change recomputes; key change
  recomputes; `forceFresh` always recomputes; a `null`-returning compute still caches (regression
  test for the `hasValue` fix). Follows `NpcTrackerRegistryTest.java`'s existing style, the
  closest precedent in this codebase for a hand-written-stub, no-mocking-library Java unit test.
- **`TickClockTest`** (new, pure logic): starts at a known baseline; increments by exactly 1 per
  `GameTick` event.
- **Python** (`ControllerAddon/tests/`): extend existing tests to confirm `api_get` issues
  requests through the shared `Session` (not the module-level function), and that `fresh=True`
  on each modified `get_*()` wrapper forwards the right query param.
- **Live verification** (per `CLAUDE.md`'s live-debugging workflow — compile clean, relaunch,
  watch it work through the specific thing that was broken, don't stop at "compiles and reads
  right"): confirm `get_bank(fresh=True)` immediately reflects a withdrawal instead of needing a
  full tick's wait, and that `blast_furnace.py` runs correctly with its sleep-a-tick workaround
  removed.

Done when: both Java test classes and the extended Python tests pass, and the live check above
confirms `blast_furnace.py` withdraws correctly with the sleep-a-tick workaround removed.
