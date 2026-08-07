# Per-NPC Tracker Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Post-implementation note:** this plan (Task 4's `Map<NPC, List<NpcTracker>>` and Task 6's
> steps below) still describes the framework as purely spawn/change-driven. Live verification in
> Task 6 found a real gap in that design -- an NPC that already existed in the scene *before* the
> player targeted it never fires `NpcSpawned`/`NpcChanged` just from being targeted, so
> `CurrentTargetTrackerFactory` never got a chance to attach to it. The fix (added live during
> Task 6, not planned here) was a `revalidateCurrentTarget()` sweep called from `onGameTick`, plus
> switching `attached` to `Map<NPC, Map<NpcTrackerFactory, NpcTracker>>` (see Task 2's own
> mid-implementation fix for the aggregate-boolean bug, further updated by the `GameTick` sweep).
> See `docs/superpowers/specs/2026-08-06-npc-tracker-framework-design.md`'s Architecture section
> for the current, accurate shape -- this plan file is kept as a historical record of the
> as-planned design, not updated task-by-task to match.

**Goal:** Add a generic per-NPC event-tracking layer (`com.example.tracker`) to the RuneLite plugin that lets a factory attach a stateful tracker to any matching NPC and receive `AnimationListener`/`HitsplatListener`/`TrackerTickListener` callbacks, with animation/hitsplat events pushed over the existing UDP bridge -- proven live against whatever NPC is currently being fought, with no specific boss wired up.

**Architecture:** A new `NpcTrackerRegistry` class subscribes to RuneLite's NPC lifecycle and combat events (`NpcSpawned`/`NpcChanged`/`NpcDespawned`/`GameStateChanged`/`HitsplatApplied`/`GameTick`/`ClientTick`), maintains `Map<NPC, List<NpcTracker>>`, and fans events out only to attached trackers that implement the relevant listener interface -- modeled on Salvation's `TrackerPlugin`/`NpcTracker` (see `data/salvation_plugin examples/plugins/tracker/`) but without any overlay/rendering machinery. One proof tracker (`CurrentTargetTracker`, attaches to whatever NPC is currently fighting the local player) is wired in to verify the whole pipeline live.

**Tech Stack:** Java 11, RuneLite plugin API (`net.runelite.api`/`net.runelite.client.eventbus`), Gradle, JUnit 4.12 (already a dependency), Mockito (new dependency, added in Task 1).

## Global Constraints

- No overlays/rendering (`NpcOverlay`, `SceneOverlay`, etc.) -- this bot runs with nobody watching the client. See spec's Non-goals.
- Only `AnimationListener`, `HitsplatListener`, `TrackerTickListener` are built. No `Projectile`/`GameObject`/`GraphicsObject`/`SpotAnimation`/`OverheadText`/`MenuListener`/`DamagePredictor`/`SoundEffect` listeners in this iteration.
- No boss-specific tracker logic. `CurrentTargetTracker` is the only concrete tracker, and it exists purely to prove the pipeline works, not to do anything with a specific boss's mechanics.
- Animation-change detection polls `npc.getAnimation()`/`npc.getAnimationFrame()` on `ClientTick` and diffs internally -- **do not** subscribe to `AnimationChanged` for this. Confirmed against Salvation's real `NpcTracker.java:193-216`: it doesn't rely on that event for NPCs either. See the spec's correction (commit `a413cd6`).
- `ControllerPlugin.java`'s existing player-state event code (`HP_CHANGED`, `ANIMATION_CHANGED`, etc.) is untouched.
- New UDP event types reuse the existing `UdpEventSender` (`com.example.util.UdpEventSender`) -- no new transport.
- Spec: `docs/superpowers/specs/2026-08-06-npc-tracker-framework-design.md`

---

## Verified RuneLite API reference

Confirmed via `javap` against the real jars this project compiles against (`runelite-api-1.12.35.jar`, `client-1.12.35.jar` -- the same version the running client uses), not assumed from memory. An implementer should not need to re-derive these:

```java
// net.runelite.api.NPC extends net.runelite.api.Actor
public interface NPC extends Actor {
    int getId();
    String getName();
    int getIndex();
    // ...
}

// net.runelite.api.Actor (relevant subset)
public interface Actor {
    boolean isInteracting();
    Actor getInteracting();
    int getAnimation();
    int getAnimationFrame();
}

// net.runelite.api.Hitsplat
public interface Hitsplat {
    int getHitsplatType();
    int getAmount();
}

// net.runelite.api.events.HitsplatApplied (class)
public Actor getActor();
public Hitsplat getHitsplat();

// net.runelite.api.events.NpcSpawned (final class)
public NPC getNpc();
public NpcSpawned(NPC npc); // constructor

// net.runelite.api.events.NpcChanged (final class)
public NPC getNpc();
public NPCComposition getOld();

// net.runelite.api.events.NpcDespawned (final class)
public NPC getNpc();

// net.runelite.api.events.GameStateChanged (class)
public GameState getGameState();

// net.runelite.api.events.GameTick (class) -- no fields/getters
// net.runelite.api.events.ClientTick (class) -- no fields/getters

// net.runelite.api.GameState (enum) -- includes GameState.HOPPING

// net.runelite.api.Client
public int getTickCount();
public Player getLocalPlayer();

// net.runelite.client.eventbus.EventBus
public synchronized void register(Object);
public synchronized void unregister(Object);
```

---

### Task 1: Tracker interfaces + Mockito test dependency

**Files:**
- Create: `src/main/java/com/example/tracker/NpcTracker.java`
- Create: `src/main/java/com/example/tracker/NpcTrackerFactory.java`
- Create: `src/main/java/com/example/tracker/AnimationListener.java`
- Create: `src/main/java/com/example/tracker/HitsplatListener.java`
- Create: `src/main/java/com/example/tracker/TrackerTickListener.java`
- Modify: `build.gradle`

**Interfaces:**
- Produces: `NpcTracker` (marker, `default void onDetach() {}`), `NpcTrackerFactory` (`isValidTrackingTarget(NPC)`, `newInstance(NPC)`), `AnimationListener extends NpcTracker` (`onAnimationChanged(int)`), `HitsplatListener extends NpcTracker` (`onHitsplat(Hitsplat)`), `TrackerTickListener extends NpcTracker` (`onTrackerTick(int)`). Every later task consumes these exact names/signatures.

This project has zero existing Java unit tests -- `build.gradle` declares `junit:junit:4.12` but nothing uses it yet. Task 2 onward needs to fake `NPC`/`Hitsplat`/`Client` objects (both are RuneLite interfaces with many unrelated abstract methods -- `Actor` alone has 15+), so this task adds Mockito rather than hand-writing full stub classes.

- [ ] **Step 1: Add Mockito to `build.gradle`**

In `build.gradle`, in the `dependencies { ... }` block, add this line directly below the existing `testImplementation 'junit:junit:4.12'` line:

```groovy
	testImplementation 'org.mockito:mockito-core:5.11.0'
```

- [ ] **Step 2: Create the marker and factory interfaces**

`src/main/java/com/example/tracker/NpcTracker.java`:

```java
package com.example.tracker;

/**
 * Marker interface for a stateful tracker attached to one specific NPC instance by an
 * {@link NpcTrackerFactory}. Implement one or more of {@link AnimationListener},
 * {@link HitsplatListener}, {@link TrackerTickListener} to actually receive callbacks --
 * a bare NpcTracker with none of those receives nothing.
 */
public interface NpcTracker
{
	/**
	 * Called once when this tracker is detached (the NPC despawned, stopped matching its
	 * factory's isValidTrackingTarget, or the world was hopped). Optional cleanup hook.
	 */
	default void onDetach()
	{
	}
}
```

`src/main/java/com/example/tracker/NpcTrackerFactory.java`:

```java
package com.example.tracker;

import net.runelite.api.NPC;

/**
 * Decides which NPCs get a tracker attached, and creates that tracker. Register instances via
 * {@link NpcTrackerRegistry#addFactory(NpcTrackerFactory)}. Multiple factories can be
 * registered independently; one NPC can end up with more than one tracker attached if more
 * than one factory matches it.
 */
public interface NpcTrackerFactory
{
	boolean isValidTrackingTarget(NPC npc);

	NpcTracker newInstance(NPC npc);
}
```

- [ ] **Step 3: Create the listener interfaces**

`src/main/java/com/example/tracker/AnimationListener.java`:

```java
package com.example.tracker;

/**
 * Receives a callback whenever the tracked NPC's animation actually changes. Detected by
 * polling npc.getAnimation()/getAnimationFrame() every ClientTick and diffing -- NOT driven by
 * RuneLite's AnimationChanged event, which is not reliable for NPCs (confirmed against
 * Salvation's own NpcTracker, which does the same polling instead of subscribing to it).
 */
public interface AnimationListener extends NpcTracker
{
	void onAnimationChanged(int animationId);
}
```

`src/main/java/com/example/tracker/HitsplatListener.java`:

```java
package com.example.tracker;

import net.runelite.api.Hitsplat;

/** Receives a callback whenever a HitsplatApplied event's actor is this tracked NPC. */
public interface HitsplatListener extends NpcTracker
{
	void onHitsplat(Hitsplat hitsplat);
}
```

`src/main/java/com/example/tracker/TrackerTickListener.java`:

```java
package com.example.tracker;

/**
 * Receives a callback once per GameTick while attached -- for a tracker's own bookkeeping
 * (e.g. ticks since last seen attacking), not detection. Separate cadence from
 * AnimationListener's ClientTick-driven polling.
 */
public interface TrackerTickListener extends NpcTracker
{
	void onTrackerTick(int tick);
}
```

- [ ] **Step 4: Compile**

Run: `./gradlew compileJava --console=plain`
Expected: `BUILD SUCCESSFUL`. These are pure interfaces with no logic, so a compile check is the only verification this step needs -- Task 2 is where behavior gets tested.

- [ ] **Step 5: Commit**

```bash
git add build.gradle src/main/java/com/example/tracker/NpcTracker.java src/main/java/com/example/tracker/NpcTrackerFactory.java src/main/java/com/example/tracker/AnimationListener.java src/main/java/com/example/tracker/HitsplatListener.java src/main/java/com/example/tracker/TrackerTickListener.java
git commit -m "Add NPC tracker interfaces and Mockito test dependency"
```

---

### Task 2: NpcTrackerRegistry -- attach/detach lifecycle

**Files:**
- Create: `src/main/java/com/example/tracker/NpcTrackerRegistry.java`
- Test: `src/test/java/com/example/tracker/NpcTrackerRegistryTest.java`

**Interfaces:**
- Consumes: `NpcTracker`, `NpcTrackerFactory`, `AnimationListener`, `HitsplatListener`, `TrackerTickListener` (Task 1).
- Produces: `NpcTrackerRegistry(Client client, EventBus eventBus, UdpEventSender udpEventSender)` constructor; `void register()` (wires this instance onto the event bus -- call once, e.g. from `ControllerPlugin.startUp()`); `void addFactory(NpcTrackerFactory factory)`; `@Subscribe`-annotated `onNpcSpawned(NpcSpawned)`, `onNpcChanged(NpcChanged)`, `onNpcDespawned(NpcDespawned)`, `onGameStateChanged(GameStateChanged)`, `onHitsplatApplied(HitsplatApplied)`, `onGameTick(GameTick)`, `onClientTick(ClientTick)`. Package-private `Map<NPC, List<NpcTracker>> attached` field, used directly by this task's tests (same package) to assert state -- Tasks 3/4 add behavior to `onHitsplatApplied`/`onGameTick`/`onClientTick`, which are stubbed as empty method bodies in this task.

This task builds attach-on-spawn, detach-on-despawn, re-validation-on-change, and hop cleanup. `onHitsplatApplied`, `onGameTick`, `onClientTick` are declared with `@Subscribe` but empty bodies here -- Tasks 3 and 4 fill them in. This keeps each task's diff focused and independently testable, per this plan's task-sizing rule.

- [ ] **Step 1: Write the failing tests for spawn-attach and multi-factory attach**

Create `src/test/java/com/example/tracker/NpcTrackerRegistryTest.java`:

```java
package com.example.tracker;

import com.example.util.UdpEventSender;
import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.api.events.NpcSpawned;
import net.runelite.client.eventbus.EventBus;
import org.junit.Before;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

public class NpcTrackerRegistryTest
{
	private Client client;
	private EventBus eventBus;
	private UdpEventSender udpEventSender;
	private NpcTrackerRegistry registry;

	@Before
	public void setUp()
	{
		client = mock(Client.class);
		eventBus = mock(EventBus.class);
		udpEventSender = mock(UdpEventSender.class);
		registry = new NpcTrackerRegistry(client, eventBus, udpEventSender);
	}

	private static NPC npc(int index)
	{
		NPC npc = mock(NPC.class);
		when(npc.getIndex()).thenReturn(index);
		return npc;
	}

	@Test
	public void attachesTrackerWhenFactoryMatches()
	{
		NPC target = npc(1);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);

		registry.onNpcSpawned(new NpcSpawned(target));

		assertEquals(1, registry.attached.get(target).size());
		assertTrue(registry.attached.get(target).contains(tracker));
	}

	@Test
	public void doesNotAttachWhenFactoryDoesNotMatch()
	{
		NPC other = npc(2);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(other)).thenReturn(false);
		registry.addFactory(factory);

		registry.onNpcSpawned(new NpcSpawned(other));

		assertEquals(null, registry.attached.get(other));
	}

	@Test
	public void multipleMatchingFactoriesEachAttachTheirOwnTracker()
	{
		NPC target = npc(3);
		NpcTracker trackerA = mock(NpcTracker.class);
		NpcTracker trackerB = mock(NpcTracker.class);
		NpcTrackerFactory factoryA = mock(NpcTrackerFactory.class);
		NpcTrackerFactory factoryB = mock(NpcTrackerFactory.class);
		when(factoryA.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryA.newInstance(target)).thenReturn(trackerA);
		when(factoryB.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryB.newInstance(target)).thenReturn(trackerB);
		registry.addFactory(factoryA);
		registry.addFactory(factoryB);

		registry.onNpcSpawned(new NpcSpawned(target));

		assertEquals(2, registry.attached.get(target).size());
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew test --tests "com.example.tracker.NpcTrackerRegistryTest" --console=plain`
Expected: compile failure (`NpcTrackerRegistry` doesn't exist yet, `registry.attached` doesn't exist). This is the expected RED state -- confirms the test file itself is wired up correctly and is exercising code that doesn't exist yet, not a typo.

- [ ] **Step 3: Implement NpcTrackerRegistry (spawn/attach only, other subscribers stubbed)**

Create `src/main/java/com/example/tracker/NpcTrackerRegistry.java`:

```java
package com.example.tracker;

import com.example.util.UdpEventSender;
import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.api.events.ClientTick;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GameTick;
import net.runelite.api.events.HitsplatApplied;
import net.runelite.api.events.NpcChanged;
import net.runelite.api.events.NpcDespawned;
import net.runelite.api.events.NpcSpawned;
import net.runelite.api.GameState;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class NpcTrackerRegistry
{
	private final Client client;
	private final EventBus eventBus;
	private final UdpEventSender udpEventSender;
	private final List<NpcTrackerFactory> factories = new ArrayList<>();

	// Package-private (not private) so this task's same-package tests can assert on attachment
	// state directly, instead of needing a getter whose only caller would be a test.
	final Map<NPC, List<NpcTracker>> attached = new HashMap<>();

	public NpcTrackerRegistry(Client client, EventBus eventBus, UdpEventSender udpEventSender)
	{
		this.client = client;
		this.eventBus = eventBus;
		this.udpEventSender = udpEventSender;
	}

	/** Wires this instance onto the event bus. Call once, e.g. from ControllerPlugin.startUp(). */
	public void register()
	{
		eventBus.register(this);
	}

	/** Registers a factory. An NPC can end up with more than one tracker if more than one
	 * factory matches it -- each is independent. */
	public void addFactory(NpcTrackerFactory factory)
	{
		factories.add(factory);
	}

	@Subscribe
	public void onNpcSpawned(NpcSpawned event)
	{
		attachMatchingFactories(event.getNpc());
	}

	private void attachMatchingFactories(NPC npc)
	{
		for (NpcTrackerFactory factory : factories)
		{
			if (factory.isValidTrackingTarget(npc))
			{
				attached.computeIfAbsent(npc, n -> new ArrayList<>()).add(factory.newInstance(npc));
			}
		}
	}

	@Subscribe
	public void onNpcChanged(NpcChanged event)
	{
		NPC npc = event.getNpc();
		List<NpcTracker> current = attached.get(npc);
		boolean wasTracked = current != null && !current.isEmpty();
		boolean stillValid = factories.stream().anyMatch(f -> f.isValidTrackingTarget(npc));

		if (wasTracked && !stillValid)
		{
			detachAll(npc);
		}
		else if (!wasTracked && stillValid)
		{
			attachMatchingFactories(npc);
		}
	}

	@Subscribe
	public void onNpcDespawned(NpcDespawned event)
	{
		detachAll(event.getNpc());
	}

	private void detachAll(NPC npc)
	{
		List<NpcTracker> trackers = attached.remove(npc);
		if (trackers == null)
		{
			return;
		}
		for (NpcTracker tracker : trackers)
		{
			safely(tracker::onDetach);
		}
	}

	@Subscribe
	public void onGameStateChanged(GameStateChanged event)
	{
		if (event.getGameState() != GameState.HOPPING)
		{
			return;
		}
		for (NPC npc : new ArrayList<>(attached.keySet()))
		{
			detachAll(npc);
		}
	}

	@Subscribe
	public void onHitsplatApplied(HitsplatApplied event)
	{
		// Filled in by Task 3.
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		// Filled in by Task 4.
	}

	@Subscribe
	public void onClientTick(ClientTick event)
	{
		// Filled in by Task 4.
	}

	private void safely(Runnable action)
	{
		try
		{
			action.run();
		}
		catch (Exception e)
		{
			System.err.println("NpcTrackerRegistry listener threw: " + e.getMessage());
		}
	}
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew test --tests "com.example.tracker.NpcTrackerRegistryTest" --console=plain`
Expected: `BUILD SUCCESSFUL`, 3 tests passed.

- [ ] **Step 5: Write failing tests for despawn, re-validation, and world-hop cleanup**

Append to `NpcTrackerRegistryTest.java` (inside the class, before the closing brace):

```java
	@Test
	public void detachesAndCallsOnDetachWhenNpcDespawns()
	{
		NPC target = npc(4);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onNpcDespawned(new net.runelite.api.events.NpcDespawned(target));

		assertEquals(null, registry.attached.get(target));
		org.mockito.Mockito.verify(tracker).onDetach();
	}

	@Test
	public void npcChangedDetachesWhenNoLongerValid()
	{
		NPC target = npc(5);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.newInstance(target)).thenReturn(tracker);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		when(factory.isValidTrackingTarget(target)).thenReturn(false);
		registry.onNpcChanged(new net.runelite.api.events.NpcChanged(target, null));

		assertEquals(null, registry.attached.get(target));
	}

	@Test
	public void npcChangedAttachesWhenNewlyValid()
	{
		NPC target = npc(6);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(false);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target)); // not attached yet

		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.onNpcChanged(new net.runelite.api.events.NpcChanged(target, null));

		assertEquals(1, registry.attached.get(target).size());
	}

	@Test
	public void gameStateHoppingClearsEverything()
	{
		NPC target = npc(7);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		net.runelite.api.events.GameStateChanged event = new net.runelite.api.events.GameStateChanged();
		event.setGameState(net.runelite.api.GameState.HOPPING);
		registry.onGameStateChanged(event);

		assertEquals(null, registry.attached.get(target));
		org.mockito.Mockito.verify(tracker).onDetach();
	}
```

- [ ] **Step 6: Run tests to verify they fail correctly, then pass**

Run: `./gradlew test --tests "com.example.tracker.NpcTrackerRegistryTest" --console=plain`

These four should already pass against the Step 3 implementation (it was written to cover this behavior) -- run this as a real check, not a formality. If any fail, fix `NpcTrackerRegistry` (not the test) until `BUILD SUCCESSFUL`, 7 tests passed.

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/tracker/NpcTrackerRegistry.java src/test/java/com/example/tracker/NpcTrackerRegistryTest.java
git commit -m "Add NpcTrackerRegistry attach/detach/re-validation/hop-cleanup lifecycle"
```

---

### Task 3: Hitsplat fan-out + UDP push

**Files:**
- Modify: `src/main/java/com/example/tracker/NpcTrackerRegistry.java` (fills in `onHitsplatApplied`)
- Test: `src/test/java/com/example/tracker/NpcTrackerRegistryTest.java`

**Interfaces:**
- Consumes: `HitsplatListener.onHitsplat(Hitsplat)` (Task 1), `attached` map (Task 2).
- Produces: `onHitsplatApplied` now calls every attached `HitsplatListener` for the event's NPC and, if at least one existed, calls `udpEventSender.send("NPC_TRACKED_HITSPLAT", Map<String,Object>)` with keys `"index"` (int), `"id"` (int), `"name"` (String), `"hitsplatType"` (int), `"amount"` (int). Later tasks/consumers should expect exactly these key names and types.

- [ ] **Step 1: Write the failing tests**

Append to `NpcTrackerRegistryTest.java`:

```java
	private static net.runelite.api.Hitsplat hitsplat(int type, int amount)
	{
		net.runelite.api.Hitsplat h = mock(net.runelite.api.Hitsplat.class);
		when(h.getHitsplatType()).thenReturn(type);
		when(h.getAmount()).thenReturn(amount);
		return h;
	}

	private interface FullTracker extends AnimationListener, HitsplatListener, TrackerTickListener
	{
	}

	@Test
	public void hitsplatAppliedCallsAttachedHitsplatListenerAndPushesUdpEvent()
	{
		NPC target = npc(8);
		when(target.getId()).thenReturn(100);
		when(target.getName()).thenReturn("Cockatrice");
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		net.runelite.api.Hitsplat splat = hitsplat(1, 7);
		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(target);
		event.setHitsplat(splat);
		registry.onHitsplatApplied(event);

		org.mockito.Mockito.verify(tracker).onHitsplat(splat);
		org.mockito.ArgumentCaptor<java.util.Map> payloadCaptor = org.mockito.ArgumentCaptor.forClass(java.util.Map.class);
		org.mockito.Mockito.verify(udpEventSender).send(org.mockito.Mockito.eq("NPC_TRACKED_HITSPLAT"), payloadCaptor.capture());
		java.util.Map<String, Object> payload = payloadCaptor.getValue();
		assertEquals(8, payload.get("index"));
		assertEquals(100, payload.get("id"));
		assertEquals("Cockatrice", payload.get("name"));
		assertEquals(1, payload.get("hitsplatType"));
		assertEquals(7, payload.get("amount"));
	}

	@Test
	public void hitsplatAppliedIgnoresNonNpcActors()
	{
		net.runelite.api.Player player = mock(net.runelite.api.Player.class);
		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(player);
		event.setHitsplat(hitsplat(1, 5));

		registry.onHitsplatApplied(event); // must not throw

		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void hitsplatAppliedIgnoresUntrackedNpcs()
	{
		NPC untracked = npc(9);
		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(untracked);
		event.setHitsplat(hitsplat(1, 5));

		registry.onHitsplatApplied(event);

		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void hitsplatListenerExceptionDoesNotStopOtherListeners()
	{
		NPC target = npc(10);
		FullTracker throwing = mock(FullTracker.class);
		org.mockito.Mockito.doThrow(new RuntimeException("boom")).when(throwing).onHitsplat(org.mockito.Mockito.any());
		FullTracker fine = mock(FullTracker.class);
		NpcTrackerFactory factoryA = mock(NpcTrackerFactory.class);
		when(factoryA.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryA.newInstance(target)).thenReturn(throwing);
		NpcTrackerFactory factoryB = mock(NpcTrackerFactory.class);
		when(factoryB.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryB.newInstance(target)).thenReturn(fine);
		registry.addFactory(factoryA);
		registry.addFactory(factoryB);
		registry.onNpcSpawned(new NpcSpawned(target));

		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(target);
		event.setHitsplat(hitsplat(1, 5));
		registry.onHitsplatApplied(event); // must not throw

		org.mockito.Mockito.verify(fine).onHitsplat(org.mockito.Mockito.any());
	}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew test --tests "com.example.tracker.NpcTrackerRegistryTest" --console=plain`
Expected: the 4 new tests FAIL (`onHitsplatApplied` is still an empty stub from Task 2) -- e.g. `hitsplatAppliedCallsAttachedHitsplatListenerAndPushesUdpEvent` fails on the `verify(tracker).onHitsplat(splat)` line. The other 7 from Task 2 still pass.

- [ ] **Step 3: Implement onHitsplatApplied**

In `src/main/java/com/example/tracker/NpcTrackerRegistry.java`, replace:

```java
	@Subscribe
	public void onHitsplatApplied(HitsplatApplied event)
	{
		// Filled in by Task 3.
	}
```

with:

```java
	@Subscribe
	public void onHitsplatApplied(HitsplatApplied event)
	{
		if (!(event.getActor() instanceof NPC))
		{
			return;
		}
		NPC npc = (NPC) event.getActor();
		List<NpcTracker> trackers = attached.get(npc);
		if (trackers == null)
		{
			return;
		}

		net.runelite.api.Hitsplat hitsplat = event.getHitsplat();
		boolean anyListener = false;
		for (NpcTracker tracker : trackers)
		{
			if (tracker instanceof HitsplatListener)
			{
				anyListener = true;
				HitsplatListener listener = (HitsplatListener) tracker;
				safely(() -> listener.onHitsplat(hitsplat));
			}
		}

		if (!anyListener)
		{
			return;
		}

		Map<String, Object> payload = new HashMap<>();
		payload.put("index", npc.getIndex());
		payload.put("id", npc.getId());
		payload.put("name", npc.getName());
		payload.put("hitsplatType", hitsplat.getHitsplatType());
		payload.put("amount", hitsplat.getAmount());
		udpEventSender.send("NPC_TRACKED_HITSPLAT", payload);
	}
```

Add the needed import: `import net.runelite.api.Hitsplat;` at the top of the file (near the other `net.runelite.api.*` imports), and simplify the fully-qualified `net.runelite.api.Hitsplat` reference in the method body to just `Hitsplat` once that import is added.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew test --tests "com.example.tracker.NpcTrackerRegistryTest" --console=plain`
Expected: `BUILD SUCCESSFUL`, 11 tests passed.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/tracker/NpcTrackerRegistry.java src/test/java/com/example/tracker/NpcTrackerRegistryTest.java
git commit -m "Fan out HitsplatApplied to attached HitsplatListeners, push NPC_TRACKED_HITSPLAT"
```

---

### Task 4: Animation-change polling (ClientTick) + tick callback (GameTick)

**Files:**
- Modify: `src/main/java/com/example/tracker/NpcTrackerRegistry.java` (fills in `onClientTick`, `onGameTick`)
- Test: `src/test/java/com/example/tracker/NpcTrackerRegistryTest.java`

**Interfaces:**
- Consumes: `AnimationListener.onAnimationChanged(int)`, `TrackerTickListener.onTrackerTick(int)` (Task 1), `attached` map (Task 2).
- Produces: `onClientTick` polls `npc.getAnimation()`/`getAnimationFrame()` for every tracked NPC with at least one attached `AnimationListener`, calls `onAnimationChanged(animationId)` on each such listener only when the animation actually changed (id differs, or id is the same but `animationFrame` regressed -- a restart), and on that same condition pushes `udpEventSender.send("NPC_TRACKED_ANIMATION_CHANGED", payload)` with keys `"index"`, `"id"`, `"name"`, `"animationId"`. `onGameTick` calls `onTrackerTick(client.getTickCount())` on every attached `TrackerTickListener`.

- [ ] **Step 1: Write the failing tests**

Append to `NpcTrackerRegistryTest.java`:

```java
	@Test
	public void clientTickCallsOnAnimationChangedWhenAnimationDiffers()
	{
		NPC target = npc(11);
		when(target.getId()).thenReturn(200);
		when(target.getName()).thenReturn("Turoth");
		when(target.getAnimation()).thenReturn(1156);
		when(target.getAnimationFrame()).thenReturn(0);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onClientTick(new net.runelite.api.events.ClientTick());

		org.mockito.Mockito.verify(tracker).onAnimationChanged(1156);
		org.mockito.ArgumentCaptor<java.util.Map> payloadCaptor = org.mockito.ArgumentCaptor.forClass(java.util.Map.class);
		org.mockito.Mockito.verify(udpEventSender).send(org.mockito.Mockito.eq("NPC_TRACKED_ANIMATION_CHANGED"), payloadCaptor.capture());
		java.util.Map<String, Object> payload = payloadCaptor.getValue();
		assertEquals(11, payload.get("index"));
		assertEquals(200, payload.get("id"));
		assertEquals("Turoth", payload.get("name"));
		assertEquals(1156, payload.get("animationId"));
	}

	@Test
	public void clientTickDoesNotFireWhenAnimationUnchanged()
	{
		NPC target = npc(12);
		when(target.getAnimation()).thenReturn(-1);
		when(target.getAnimationFrame()).thenReturn(0);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onClientTick(new net.runelite.api.events.ClientTick()); // first poll: -1 -> -1, no prior state, treated as initial baseline
		org.mockito.Mockito.clearInvocations(tracker, udpEventSender);
		registry.onClientTick(new net.runelite.api.events.ClientTick()); // second poll: still -1, genuinely unchanged

		org.mockito.Mockito.verify(tracker, org.mockito.Mockito.never()).onAnimationChanged(org.mockito.Mockito.anyInt());
		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void clientTickDetectsSameAnimationIdRestartingViaFrameRegression()
	{
		NPC target = npc(13);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		when(target.getAnimation()).thenReturn(390);
		when(target.getAnimationFrame()).thenReturn(5);
		registry.onClientTick(new net.runelite.api.events.ClientTick());
		org.mockito.Mockito.clearInvocations(tracker);

		// Same animation id, but the frame counter went backwards -- a fresh restart of the
		// same attack animation, not a continuation of the previous one.
		when(target.getAnimationFrame()).thenReturn(1);
		registry.onClientTick(new net.runelite.api.events.ClientTick());

		org.mockito.Mockito.verify(tracker).onAnimationChanged(390);
	}

	@Test
	public void clientTickSkipsNpcsWithNoAnimationListenerAttached()
	{
		NPC target = npc(14);
		when(target.getAnimation()).thenReturn(999);
		NpcTracker plainTracker = mock(NpcTracker.class); // no listener interfaces
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(plainTracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onClientTick(new net.runelite.api.events.ClientTick()); // must not throw

		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void gameTickCallsOnTrackerTickWithCurrentTickCount()
	{
		when(client.getTickCount()).thenReturn(4242);
		NPC target = npc(15);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onGameTick(new net.runelite.api.events.GameTick());

		org.mockito.Mockito.verify(tracker).onTrackerTick(4242);
	}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew test --tests "com.example.tracker.NpcTrackerRegistryTest" --console=plain`
Expected: the 5 new tests FAIL (`onClientTick`/`onGameTick` are still empty stubs). The 11 from Tasks 2-3 still pass.

- [ ] **Step 3: Implement onClientTick and onGameTick**

In `src/main/java/com/example/tracker/NpcTrackerRegistry.java`, add two new fields alongside `attached`:

```java
	private final Map<NPC, Integer> lastAnimationId = new HashMap<>();
	private final Map<NPC, Integer> lastAnimationFrame = new HashMap<>();
```

Replace:

```java
	@Subscribe
	public void onGameTick(GameTick event)
	{
		// Filled in by Task 4.
	}

	@Subscribe
	public void onClientTick(ClientTick event)
	{
		// Filled in by Task 4.
	}
```

with:

```java
	@Subscribe
	public void onGameTick(GameTick event)
	{
		int tick = client.getTickCount();
		for (List<NpcTracker> trackers : attached.values())
		{
			for (NpcTracker tracker : trackers)
			{
				if (tracker instanceof TrackerTickListener)
				{
					TrackerTickListener listener = (TrackerTickListener) tracker;
					safely(() -> listener.onTrackerTick(tick));
				}
			}
		}
	}

	@Subscribe
	public void onClientTick(ClientTick event)
	{
		for (Map.Entry<NPC, List<NpcTracker>> entry : attached.entrySet())
		{
			NPC npc = entry.getKey();
			List<NpcTracker> trackers = entry.getValue();
			boolean anyAnimationListener = trackers.stream().anyMatch(t -> t instanceof AnimationListener);
			if (!anyAnimationListener)
			{
				continue;
			}

			int animation = npc.getAnimation();
			int animationFrame = npc.getAnimationFrame();
			Integer previousId = lastAnimationId.put(npc, animation);
			Integer previousFrame = lastAnimationFrame.put(npc, animationFrame);

			boolean changed = previousId == null
				|| animation != previousId
				|| (previousFrame != null && animationFrame < previousFrame);
			if (!changed)
			{
				continue;
			}

			for (NpcTracker tracker : trackers)
			{
				if (tracker instanceof AnimationListener)
				{
					AnimationListener listener = (AnimationListener) tracker;
					safely(() -> listener.onAnimationChanged(animation));
				}
			}

			Map<String, Object> payload = new HashMap<>();
			payload.put("index", npc.getIndex());
			payload.put("id", npc.getId());
			payload.put("name", npc.getName());
			payload.put("animationId", animation);
			udpEventSender.send("NPC_TRACKED_ANIMATION_CHANGED", payload);
		}
	}
```

Also update `detachAll` to clean up the two new maps, so a despawned/detached NPC doesn't leak entries:

```java
	private void detachAll(NPC npc)
	{
		List<NpcTracker> trackers = attached.remove(npc);
		lastAnimationId.remove(npc);
		lastAnimationFrame.remove(npc);
		if (trackers == null)
		{
			return;
		}
		for (NpcTracker tracker : trackers)
		{
			safely(tracker::onDetach);
		}
	}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew test --tests "com.example.tracker.NpcTrackerRegistryTest" --console=plain`
Expected: `BUILD SUCCESSFUL`, 16 tests passed.

- [ ] **Step 5: Run the full test suite to confirm nothing else broke**

Run: `./gradlew test --console=plain`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/example/tracker/NpcTrackerRegistry.java src/test/java/com/example/tracker/NpcTrackerRegistryTest.java
git commit -m "Add ClientTick animation-change polling and GameTick tick callback"
```

---

### Task 5: CurrentTargetTracker proof tracker + wire into ControllerPlugin

**Files:**
- Create: `src/main/java/com/example/tracker/CurrentTargetTracker.java`
- Create: `src/main/java/com/example/tracker/CurrentTargetTrackerFactory.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`

**Interfaces:**
- Consumes: `AnimationListener`, `HitsplatListener`, `TrackerTickListener`, `NpcTrackerFactory`, `NpcTrackerRegistry` (Tasks 1-4). `ControllerPlugin`'s existing `@Inject private Client client;`, `@Inject private EventBus eventBus;`, and its `private UdpEventSender udpSender;` field (already created and started in `startUp()`, before providers are registered).
- Produces: nothing new consumed by later code -- this is the final task, proven live in Task 6.

No unit tests for this task -- `CurrentTargetTrackerFactory.isValidTrackingTarget` needs `client.getLocalPlayer()`, which is exactly the kind of thin RuneLite-wiring glue this codebase has never unit tested (see the spec's Testing section). Verified by compiling clean, then live in Task 6.

- [ ] **Step 1: Create CurrentTargetTracker**

`src/main/java/com/example/tracker/CurrentTargetTracker.java`:

```java
package com.example.tracker;

import net.runelite.api.Hitsplat;

/**
 * The proof tracker for this framework -- attaches to whatever NPC is currently fighting the
 * local player (see CurrentTargetTrackerFactory) and does nothing itself. NpcTrackerRegistry is
 * what pushes NPC_TRACKED_ANIMATION_CHANGED/NPC_TRACKED_HITSPLAT over UDP for any NPC with an
 * attached listener of the matching type -- this class exists only to be that listener, so its
 * callback bodies are intentionally empty. Verify the pipeline via poll_events, not by reading
 * Java console output.
 */
public class CurrentTargetTracker implements AnimationListener, HitsplatListener, TrackerTickListener
{
	@Override
	public void onAnimationChanged(int animationId)
	{
	}

	@Override
	public void onHitsplat(Hitsplat hitsplat)
	{
	}

	@Override
	public void onTrackerTick(int tick)
	{
	}
}
```

- [ ] **Step 2: Create CurrentTargetTrackerFactory**

`src/main/java/com/example/tracker/CurrentTargetTrackerFactory.java`:

```java
package com.example.tracker;

import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.api.Player;

public class CurrentTargetTrackerFactory implements NpcTrackerFactory
{
	private final Client client;

	public CurrentTargetTrackerFactory(Client client)
	{
		this.client = client;
	}

	@Override
	public boolean isValidTrackingTarget(NPC npc)
	{
		Player localPlayer = client.getLocalPlayer();
		return localPlayer != null && npc.equals(localPlayer.getInteracting());
	}

	@Override
	public NpcTracker newInstance(NPC npc)
	{
		return new CurrentTargetTracker();
	}
}
```

- [ ] **Step 3: Wire NpcTrackerRegistry into ControllerPlugin**

In `src/main/java/com/example/controller/ControllerPlugin.java`, add the import (alongside the existing `com.example.providers.*`/`com.example.util.UdpEventSender` imports near the top):

```java
import com.example.tracker.CurrentTargetTrackerFactory;
import com.example.tracker.NpcTrackerRegistry;
```

Add a new field alongside the existing `private UdpEventSender udpSender;` (around line 61):

```java
	private NpcTrackerRegistry npcTrackerRegistry;
```

In `startUp()`, after `udpSender.start();` and before the `server = new BridgeHttpServer();` line (order doesn't matter relative to the HTTP server, but `udpSender` must already be started since the registry needs it):

```java
		npcTrackerRegistry = new NpcTrackerRegistry(client, eventBus, udpSender);
		npcTrackerRegistry.addFactory(new CurrentTargetTrackerFactory(client));
		npcTrackerRegistry.register();
```

Note: `ControllerPlugin.shutDown()` does not currently call `eventBus.unregister(...)` for `SailingProvider` either (the one other class in this codebase that registers itself on the event bus) -- this task follows that same existing precedent and does not add teardown for `NpcTrackerRegistry`, to stay consistent rather than introduce a new pattern as a side effect of this change.

- [ ] **Step 4: Compile**

Run: `./gradlew compileJava --console=plain`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 5: Run the full test suite**

Run: `./gradlew test --console=plain`
Expected: `BUILD SUCCESSFUL`, all 16 tests from Tasks 2-4 still passing.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/example/tracker/CurrentTargetTracker.java src/main/java/com/example/tracker/CurrentTargetTrackerFactory.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Wire CurrentTargetTracker into ControllerPlugin as the tracker framework's proof case"
```

---

### Task 6: Live verification

**Files:** none (no code changes -- this task confirms Tasks 1-5 actually work against the real, running client, per this codebase's established "verify live, not just from reading the code" convention).

- [ ] **Step 1: Relaunch the client**

Run: `./gradlew run` (or `./gradlew.bat run` depending on shell -- see this repo's existing convention). Wait for it to reach the login screen, then log in, same as any other session.

- [ ] **Step 2: Confirm the bridge is up**

Run: `curl -s http://localhost:8080/state`
Expected: a JSON body with `"loggedIn": true` (matches this repo's existing bridge-verification convention).

- [ ] **Step 3: Start a fight against any NPC**

Manually engage any attackable NPC (a Slayer target, or literally anything attackable) so `client.getLocalPlayer().getInteracting()` becomes non-null and matches `CurrentTargetTrackerFactory`.

- [ ] **Step 4: Confirm tracked events arrive via poll_events**

Using the MCP server's `poll_events` tool (`mcp__osrs-debug__poll_events`, confirmed working earlier this session) or `GET`-equivalent polling if the MCP server isn't connected, call it a few times over the course of the fight.

Expected: at least one event with `"type": "NPC_TRACKED_ANIMATION_CHANGED"` and at least one with `"type": "NPC_TRACKED_HITSPLAT"`, each with a `payload` containing `"index"`, `"id"`, `"name"` matching the NPC being fought.

- [ ] **Step 5: Confirm cleanup on despawn**

Kill the NPC (or let the fight end) and confirm no exceptions appear in the RuneLite client's own stdout/log (the `./gradlew run` terminal) -- `detachAll` firing on `NpcDespawned` should be silent and uneventful.

- [ ] **Step 6: Note the outcome**

This is the spec's "Done when" condition (see `docs/superpowers/specs/2026-08-06-npc-tracker-framework-design.md`'s Testing section) -- no commit for this task, it's a verification gate, not a code change. If anything in Steps 4-5 doesn't match, that's a real bug in Tasks 1-5's implementation to fix (with a new failing unit test reproducing it first, per this codebase's TDD convention), not something to patch around here.
