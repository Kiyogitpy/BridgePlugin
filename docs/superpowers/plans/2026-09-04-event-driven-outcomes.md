# Event-Driven Outcomes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** React to a click's outcome the moment the client learns it (UDP event + one HTTP confirm) instead of polling HTTP through a fixed commit window, first in Guardians of the Rift.

**Architecture:** The Java plugin gains client-event-driven UDP events (`VARBIT_CHANGED` behind a subscription filter, `WIDGET_LOADED/CLOSED`, `MENU_CLICKED`, `GRAPHIC_CHANGED`, `HITSPLAT`) plus a per-frame `PLAYER_MOVING/STOPPED` signal from `ClientTick`. Python gains one `await_outcome` primitive on `bridge_events.EventStream` (wait for a signal, confirm with one HTTP read, fall back to the HTTP read at the budget, pad with a human reaction draw), an event-wakeable engine loop, and varbit-subscription plumbing. `routines/guardians_of_the_rift.py` replaces every `_commit` window with an outcome wait and chains the next rung inside the same tick.

**Tech Stack:** Java 11 / RuneLite API 1.12.38 / JUnit 4 (Gradle), Python 3 / pytest. Client rebuild via `./gradlew.bat run` (user runs it; it needs the RuneLite window).

**Spec:** `docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md`

## Global Constraints

- `ctx.events` is an accelerator, never a source of truth: every wait falls back to the HTTP read the routine uses today (CLAUDE.md).
- A `@Subscribe` method MUST be named `on<EventSimpleName>` (RuneLite `EventBus.register` enforces it; one wrong name kills the whole plugin's registration).
- Never edit files under `src/main/java/net/runelite/client/plugins/m8aq/api/`.
- `controller.py` is loaded twice in a running process (`__main__` and `controller`); shared mutable state belongs in its own module. `bridge_events` is a single module; the engine owns the only `EventStream`.
- Windows paths, PowerShell shell. Python commands run from `ControllerAddon/`; Gradle from the repo root.
- Every UDP payload added here carries `tick` and `atMillis`.
- `REACTION_DELAY_S = (0.15, 0.35)` initial value; `FALLBACK_POLL_S = 0.25`; `EVENT_POLL_INTERVAL_S = 0.02` (existing); `STOPPED_AFTER_FRAMES = 2`; `TELEPORT_JUMP_UNITS = 256`; `CHAIN_MAX_ACTIONS = 4`; `CHAIN_MAX_S = 15.0`; `LOOP_SLOW_LOG_S = 1.0`; `LOOP_STATS_EVERY_S = 60.0`.
- GOTR HUD interface group = 746 (`InterfaceID.GotrHud.UNIVERSE` = 0x02ea_0000). GOTR varbits: `GOTR_IS_PLAYING` 13691, `GOTR_PLAYER_HAS_FINISHED_GAME` 13688, `GOTR_ELEMENTAL_EARNED_THIS_GAME` 13686, `GOTR_CATALYTIC_EARNED_THIS_GAME` 13685, `GOTR_PORTAL_BOTTOM_VISIBLE` 13687, `GOTR_MINING_COUNT` 5667 (read from `runelite-api-1.12.38.jar`, `VarbitID`). Cell barriers are `GroundObject`s, NOT varbits; their outcome signal is the inventory (cell consumed / fragments spent).
- Commit only the files each task names. The working tree carries the user's unrelated uncommitted changes; never `git add -A`.

---

## File Structure

**Java (create):**
- `src/main/java/com/example/util/EventSubscriptions.java` -- lock-free varbit/varp subscription set + CSV parse. Pure.
- `src/main/java/com/example/util/PlayerMotionTracker.java` -- per-frame local-position diff -> MOVING/STOPPED transitions. Pure.
- `src/main/java/com/example/providers/EventsProvider.java` -- `GET /events/subscribe`, `GET /events/subscriptions`.
- `src/test/java/com/example/util/EventSubscriptionsTest.java`, `src/test/java/com/example/util/PlayerMotionTrackerTest.java`.

**Java (modify):**
- `src/main/java/com/example/controller/ControllerPlugin.java` -- new handlers; animation/target emission moved off the GameTick poll; wiring.
- `src/main/java/com/example/providers/PlayerProvider.java` -- `moving` field.

**Python (modify):**
- `ControllerAddon/settings.py` -- `REACTION_DELAY_S`, `FALLBACK_POLL_S`.
- `ControllerAddon/bridge_events.py` -- sequence numbers, `mark()`, `Outcome`, `await_outcome`, `wait_any`.
- `ControllerAddon/controller.py` -- `subscribe_varbits`, subscription posting, `--no-events`, `LoopStats`, loop wake.
- `ControllerAddon/routines/guardians_of_the_rift.py` -- `_await`, chaining, rung conversions.
- `ControllerAddon/tests/test_bridge_events.py`, `tests/test_controller.py`, `tests/test_guardians_of_the_rift.py`.
- `CLAUDE.md`, the spec (one correction).

**Python (create):**
- `ControllerAddon/log_gaps.py` -- gap histogram over a routine log (the measurement tool).
- `ControllerAddon/fit_reaction.py` -- human reaction-time fit from a recording `.jsonl`.
- `ControllerAddon/tests/test_log_tools.py`.

---

### Task 1: `EventSubscriptions` (Java, pure)

**Files:**
- Create: `src/main/java/com/example/util/EventSubscriptions.java`
- Test: `src/test/java/com/example/util/EventSubscriptionsTest.java`

**Interfaces:**
- Produces: `public final class EventSubscriptions { void replace(Collection<Integer> varbitIds, Collection<Integer> varpIds); boolean matches(int varbitId, int varpId); Set<Integer> varbits(); Set<Integer> varps(); static List<Integer> parseIds(String csv); }`

- [ ] **Step 1: Write the failing test**

```java
package com.example.util;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

import static org.junit.Assert.*;

public class EventSubscriptionsTest
{
	@Test
	public void emptyByDefaultMatchesNothing()
	{
		EventSubscriptions s = new EventSubscriptions();
		assertFalse(s.matches(13691, 4310));
		assertFalse(s.matches(-1, 4310));
	}

	@Test
	public void matchesSubscribedVarbitOrVarp()
	{
		EventSubscriptions s = new EventSubscriptions();
		s.replace(Arrays.asList(13691, 5667), Collections.singletonList(1000));
		assertTrue(s.matches(13691, 4310));
		assertTrue(s.matches(-1, 1000));
		assertFalse(s.matches(13688, 4311));
	}

	@Test
	public void replaceIsReplaceNotMerge()
	{
		EventSubscriptions s = new EventSubscriptions();
		s.replace(Collections.singletonList(13691), Collections.emptyList());
		s.replace(Collections.singletonList(5667), Collections.emptyList());
		assertFalse(s.matches(13691, 0));
		assertTrue(s.matches(5667, 0));
		s.replace(Collections.emptyList(), Collections.emptyList());
		assertFalse(s.matches(5667, 0));
	}

	@Test
	public void parseIdsSkipsJunkAndBlanks()
	{
		assertEquals(Arrays.asList(1, 2, 3), EventSubscriptions.parseIds("1,2, 3"));
		assertEquals(Collections.emptyList(), EventSubscriptions.parseIds(null));
		assertEquals(Collections.emptyList(), EventSubscriptions.parseIds(""));
		assertEquals(Collections.singletonList(7), EventSubscriptions.parseIds("x,7,,"));
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run (repo root): `./gradlew.bat test --tests com.example.util.EventSubscriptionsTest`
Expected: compilation failure, `EventSubscriptions` does not exist.

- [ ] **Step 3: Write the implementation**

```java
package com.example.util;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Which varbits/varps the bridge emits VARBIT_CHANGED for. VarbitChanged fires for hundreds
 * of varps on login and every scene load; unfiltered, that burst evicts the useful events
 * from UdpEventSender's 512-slot queue. So the set is opt-in, REPLACE semantics (a routine
 * declares what it needs; the engine posts it on load and after every login), and empty means
 * emit none. AtomicReference swaps make matches() a lock-free read on the client thread.
 */
public final class EventSubscriptions
{
	private final AtomicReference<Set<Integer>> varbits = new AtomicReference<>(Collections.emptySet());
	private final AtomicReference<Set<Integer>> varps = new AtomicReference<>(Collections.emptySet());

	public void replace(Collection<Integer> varbitIds, Collection<Integer> varpIds)
	{
		varbits.set(Collections.unmodifiableSet(new HashSet<>(varbitIds)));
		varps.set(Collections.unmodifiableSet(new HashSet<>(varpIds)));
	}

	/** varbitId is -1 when the client changed a raw varp with no varbit mapping. */
	public boolean matches(int varbitId, int varpId)
	{
		return (varbitId >= 0 && varbits.get().contains(varbitId)) || varps.get().contains(varpId);
	}

	public Set<Integer> varbits()
	{
		return varbits.get();
	}

	public Set<Integer> varps()
	{
		return varps.get();
	}

	/** "1,2, 3" -> [1, 2, 3]; null/blank/junk entries are skipped, never an error. */
	public static List<Integer> parseIds(String csv)
	{
		List<Integer> out = new ArrayList<>();
		if (csv == null || csv.isEmpty())
		{
			return out;
		}
		for (String part : csv.split(","))
		{
			String s = part.trim();
			if (s.isEmpty())
			{
				continue;
			}
			try
			{
				out.add(Integer.parseInt(s));
			}
			catch (NumberFormatException ignored)
			{
				// junk in the query string -- skip it
			}
		}
		return out;
	}
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat test --tests com.example.util.EventSubscriptionsTest`
Expected: BUILD SUCCESSFUL, 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/util/EventSubscriptions.java src/test/java/com/example/util/EventSubscriptionsTest.java
git commit -m "events: EventSubscriptions -- opt-in varbit/varp set for VARBIT_CHANGED emission"
```

---

### Task 2: `PlayerMotionTracker` (Java, pure)

**Files:**
- Create: `src/main/java/com/example/util/PlayerMotionTracker.java`
- Test: `src/test/java/com/example/util/PlayerMotionTrackerTest.java`

**Interfaces:**
- Produces: `public final class PlayerMotionTracker { enum Transition { NONE, MOVING, STOPPED }; Transition observe(Integer localX, Integer localY, int plane); boolean isMoving(); static final int STOPPED_AFTER_FRAMES = 2; static final int TELEPORT_JUMP_UNITS = 256; }`

- [ ] **Step 1: Write the failing test**

```java
package com.example.util;

import org.junit.Test;

import static com.example.util.PlayerMotionTracker.Transition.*;
import static org.junit.Assert.*;

public class PlayerMotionTrackerTest
{
	@Test
	public void firstSampleIsNotMotion()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		assertEquals(NONE, t.observe(1000, 1000, 0));
		assertFalse(t.isMoving());
	}

	@Test
	public void movingOnFirstDeltaThenStoppedAfterDebounce()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		assertEquals(MOVING, t.observe(1004, 1000, 0));   // walking: ~4 units per client tick
		assertTrue(t.isMoving());
		assertEquals(NONE, t.observe(1008, 1000, 0));
		assertEquals(NONE, t.observe(1008, 1000, 0));     // 1 still frame: not yet
		assertEquals(STOPPED, t.observe(1008, 1000, 0));  // 2 still frames: stopped
		assertFalse(t.isMoving());
		assertEquals(NONE, t.observe(1008, 1000, 0));     // stays stopped, no repeat
	}

	@Test
	public void oneStillFrameMidRunDoesNotStop()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(0, 0, 0);
		t.observe(8, 0, 0);
		assertEquals(NONE, t.observe(8, 0, 0));
		assertEquals(NONE, t.observe(16, 0, 0));
		assertTrue(t.isMoving());
	}

	@Test
	public void teleportJumpResetsWithoutMoving()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		assertEquals(NONE, t.observe(1000 + 3 * 128, 1000, 0));   // 3 tiles in one frame
		assertFalse(t.isMoving());
	}

	@Test
	public void teleportWhileMovingEmitsStopped()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		t.observe(1004, 1000, 0);
		assertEquals(STOPPED, t.observe(5000, 5000, 0));
		assertFalse(t.isMoving());
	}

	@Test
	public void nullOrPlaneChangeResets()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		t.observe(1004, 1000, 0);
		assertEquals(STOPPED, t.observe(null, null, 0));
		assertEquals(NONE, t.observe(1004, 1000, 0));
		assertEquals(NONE, t.observe(1004, 1000, 1));   // plane change: re-seed, no motion
		assertEquals(MOVING, t.observe(1008, 1000, 1));
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat test --tests com.example.util.PlayerMotionTrackerTest`
Expected: compilation failure.

- [ ] **Step 3: Write the implementation**

```java
package com.example.util;

/**
 * Sub-tick player motion from the client's per-frame interpolated LocalPoint, fed once per
 * ClientTick (50Hz, independent of FPS). A moving actor's local position slides every client
 * tick (~4 units walking, ~8 running, out of 128 per tile); it stops sliding the moment the
 * interpolation reaches the destination tile -- BEFORE the next server tick updates the tile.
 * That is the arrival signal a routine wants, and it needs no "two stable HTTP reads".
 *
 * Pure logic so it is unit-testable; ControllerPlugin.onClientTick feeds it and emits
 * PLAYER_MOVING / PLAYER_STOPPED on the transitions, PlayerProvider reports isMoving().
 */
public final class PlayerMotionTracker
{
	public enum Transition
	{
		NONE, MOVING, STOPPED
	}

	/** Consecutive unchanged frames before a stop is reported (~40ms). */
	static final int STOPPED_AFTER_FRAMES = 2;
	/** A jump this large in one frame is a teleport / scene reload, not a walk (2 tiles). */
	static final int TELEPORT_JUMP_UNITS = 256;

	private Integer lastX;
	private Integer lastY;
	private int lastPlane;
	private int stillFrames;
	private boolean moving;

	public Transition observe(Integer localX, Integer localY, int plane)
	{
		if (localX == null || localY == null || lastX == null || plane != lastPlane)
		{
			boolean wasMoving = moving;
			reset(localX, localY, plane);
			return wasMoving ? Transition.STOPPED : Transition.NONE;
		}
		int delta = Math.max(Math.abs(localX - lastX), Math.abs(localY - lastY));
		if (delta > TELEPORT_JUMP_UNITS)
		{
			boolean wasMoving = moving;
			reset(localX, localY, plane);
			return wasMoving ? Transition.STOPPED : Transition.NONE;
		}
		lastX = localX;
		lastY = localY;
		if (delta > 0)
		{
			stillFrames = 0;
			if (!moving)
			{
				moving = true;
				return Transition.MOVING;
			}
			return Transition.NONE;
		}
		if (moving && ++stillFrames >= STOPPED_AFTER_FRAMES)
		{
			moving = false;
			stillFrames = 0;
			return Transition.STOPPED;
		}
		return Transition.NONE;
	}

	public boolean isMoving()
	{
		return moving;
	}

	private void reset(Integer localX, Integer localY, int plane)
	{
		lastX = localX;
		lastY = localY;
		lastPlane = plane;
		stillFrames = 0;
		moving = false;
	}
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat test --tests com.example.util.PlayerMotionTrackerTest`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/util/PlayerMotionTracker.java src/test/java/com/example/util/PlayerMotionTrackerTest.java
git commit -m "events: PlayerMotionTracker -- per-frame MOVING/STOPPED from the interpolated local position"
```

---

### Task 3: Java wiring -- new UDP events, `/events/*`, `/player.moving`

**Files:**
- Create: `src/main/java/com/example/providers/EventsProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (imports at lines 3-33; fields ~95-107; `startUp` provider list at 126-159; `onGameTick` at 271-335; helpers near `targetKey` at ~580)
- Modify: `src/main/java/com/example/providers/PlayerProvider.java` (constructor at line 36, `data.put("poseAnimation", ...)` at line 230)

**Interfaces:**
- Consumes: `EventSubscriptions` (Task 1), `PlayerMotionTracker` (Task 2), `UdpEventSender.send(String, Object)`, `TickClock.get()`.
- Produces: UDP events `VARBIT_CHANGED{varbitId,varpId,value}`, `WIDGET_LOADED{groupId}`, `WIDGET_CLOSED{groupId}`, `MENU_CLICKED{option,target,id,menuAction,param0,param1,itemId}`, `GRAPHIC_CHANGED{graphic}`, `HITSPLAT{actor,amount,hitsplatType,npcIndex?,npcId?,npcName?}`, `PLAYER_MOVING{x,y,plane,localX,localY}`, `PLAYER_STOPPED{same}`; all with `tick`, `atMillis`. Endpoints `GET /events/subscribe?varbits=&varps=` and `GET /events/subscriptions` -> `{"varbits":[...],"varps":[...]}`. `/player.moving` boolean.

- [ ] **Step 1: Create `EventsProvider`**

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.EventSubscriptions;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * GET /events/subscribe?varbits=1,2&varps=3 -- REPLACE the set of varbits/varps that
 * ControllerPlugin.onVarbitChanged emits VARBIT_CHANGED for (no params = emit none).
 * GET /events/subscriptions -- read the current sets back.
 *
 * Pure bookkeeping, no client-thread hop: EventSubscriptions swaps immutable sets atomically.
 * See the 2026-09-04 event-driven-outcomes spec for why the filter exists (a login/scene-load
 * varp burst would otherwise evict useful events from the UDP queue).
 */
public class EventsProvider extends BridgeProvider
{
	private final EventSubscriptions subscriptions;

	public EventsProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
		EventSubscriptions subscriptions)
	{
		super(client, clientThread, server);
		this.subscriptions = subscriptions;
	}

	@Override
	public void register()
	{
		server.registerQuery("/events/subscribe", this::subscribe);
		server.register("/events/subscriptions", this::current);
	}

	private Object subscribe(Map<String, String> query)
	{
		subscriptions.replace(EventSubscriptions.parseIds(query.get("varbits")),
			EventSubscriptions.parseIds(query.get("varps")));
		return current();
	}

	private Object current()
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("varbits", sorted(subscriptions.varbits()));
		data.put("varps", sorted(subscriptions.varps()));
		return data;
	}

	private static List<Integer> sorted(java.util.Set<Integer> ids)
	{
		List<Integer> out = new ArrayList<>(ids);
		Collections.sort(out);
		return out;
	}
}
```

- [ ] **Step 2: `PlayerProvider` -- `moving`**

Change the constructor and add the field. Every `new PlayerProvider(` call is in `ControllerPlugin` (verified by grep 2026-09-04; re-run `grep -rn "new PlayerProvider(" src/` before editing).

```java
// field + constructor
private final com.example.util.PlayerMotionTracker motionTracker;

public PlayerProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
	com.example.util.PlayerMotionTracker motionTracker)
{
	super(client, clientThread, server);
	this.motionTracker = motionTracker;
}
```

Directly after `data.put("poseAnimation", p.getPoseAnimation());`:

```java
		// Sub-tick: true while the interpolated local position is sliding this frame
		// (PlayerMotionTracker, fed from ClientTick). The HTTP fallback for PLAYER_MOVING/STOPPED.
		data.put("moving", motionTracker.isMoving());
```

- [ ] **Step 3: `ControllerPlugin` -- fields, wiring, helper**

Add imports:

```java
import com.example.util.EventSubscriptions;
import com.example.util.PlayerMotionTracker;
import net.runelite.api.coords.LocalPoint;
import net.runelite.client.util.Text;
```

Add fields next to `lastTargetKey`:

```java
	private final EventSubscriptions eventSubscriptions = new EventSubscriptions();
	private final PlayerMotionTracker motionTracker = new PlayerMotionTracker();
```

In `startUp`, change the PlayerProvider line and add the EventsProvider:

```java
		new PlayerProvider(client, clientThread, server, motionTracker).register();
		new EventsProvider(client, clientThread, server, eventSubscriptions).register();
```

Add a helper next to `targetKey`:

```java
	/** Every event added by the 2026-09-04 event-driven-outcomes work carries these two. */
	private Map<String, Object> stampedPayload()
	{
		Map<String, Object> payload = new HashMap<>();
		payload.put("tick", tickClock.get());
		payload.put("atMillis", System.currentTimeMillis());
		return payload;
	}
```

- [ ] **Step 4: `ControllerPlugin` -- move animation/target off the GameTick poll**

In `onGameTick`, DELETE the block from `Player local = client.getLocalPlayer();` through the closing brace of `if (local != null) { ... }` (the animation and interacting polls, lines ~303-334). Keep the TICK, HP and PRAYER emission. Then add these handlers after `onGameTick`:

```java
	// ANIMATION_CHANGED / TARGET_CHANGED used to be polled inside onGameTick. They now ride the
	// client's own events, which fire as the packet is processed rather than at the tick handler
	// (2026-09-04 event-driven-outcomes). Same event names and payloads -- no Python change.
	@Subscribe
	public void onAnimationChanged(AnimationChanged event)
	{
		if (udpSender == null || event.getActor() != client.getLocalPlayer())
		{
			return;
		}
		int anim = event.getActor().getAnimation();
		if (anim == lastAnimation)
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("animation", anim);
		// "from" is the animation being left -- tells a mid-sequence pause (5212 -> -1, re-lay
		// still to come) apart from a finished action (5208 -> -1).
		payload.put("from", lastAnimation);
		udpSender.send("ANIMATION_CHANGED", payload);
		lastAnimation = anim;
	}

	@Subscribe
	public void onInteractingChanged(InteractingChanged event)
	{
		if (udpSender == null || event.getSource() != client.getLocalPlayer())
		{
			return;
		}
		Actor target = event.getTarget();
		String key = target == null ? "" : targetKey(target);
		if (key.equals(lastTargetKey))
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("target", key.isEmpty() ? null : key);
		if (target instanceof NPC)
		{
			payload.put("targetId", ((NPC) target).getId());
			payload.put("targetName", target.getName());
		}
		udpSender.send("TARGET_CHANGED", payload);
		lastTargetKey = key;
	}

	@Subscribe
	public void onVarbitChanged(VarbitChanged event)
	{
		if (udpSender == null || !eventSubscriptions.matches(event.getVarbitId(), event.getVarpId()))
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("varbitId", event.getVarbitId());
		payload.put("varpId", event.getVarpId());
		payload.put("value", event.getValue());
		udpSender.send("VARBIT_CHANGED", payload);
	}

	@Subscribe
	public void onWidgetLoaded(WidgetLoaded event)
	{
		if (udpSender == null)
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("groupId", event.getGroupId());
		udpSender.send("WIDGET_LOADED", payload);
	}

	@Subscribe
	public void onWidgetClosed(WidgetClosed event)
	{
		if (udpSender == null)
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("groupId", event.getGroupId());
		udpSender.send("WIDGET_CLOSED", payload);
	}

	// The game's own verdict on what a click became, pushed instead of polled off /last-action
	// (LastActionProvider keeps that endpoint; this is the same data as an event).
	@Subscribe
	public void onMenuOptionClicked(MenuOptionClicked event)
	{
		if (udpSender == null)
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("option", Text.removeTags(event.getMenuOption()));
		payload.put("target", Text.removeTags(event.getMenuTarget()));
		payload.put("id", event.getId());
		payload.put("menuAction", event.getMenuAction() != null ? event.getMenuAction().name() : null);
		payload.put("param0", event.getParam0());
		payload.put("param1", event.getParam1());
		payload.put("itemId", event.getItemId());
		udpSender.send("MENU_CLICKED", payload);
	}

	@Subscribe
	public void onGraphicChanged(GraphicChanged event)
	{
		if (udpSender == null || event.getActor() != client.getLocalPlayer())
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("graphic", event.getActor().getGraphic());
		udpSender.send("GRAPHIC_CHANGED", payload);
	}

	@Subscribe
	public void onHitsplatApplied(HitsplatApplied event)
	{
		Player local = client.getLocalPlayer();
		if (udpSender == null || local == null)
		{
			return;
		}
		Actor actor = event.getActor();
		boolean onPlayer = actor == local;
		boolean onTarget = actor != null && actor == local.getInteracting();
		if (!onPlayer && !onTarget)
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		payload.put("actor", onPlayer ? "player" : "target");
		payload.put("amount", event.getHitsplat().getAmount());
		payload.put("hitsplatType", event.getHitsplat().getHitsplatType());
		if (actor instanceof NPC)
		{
			payload.put("npcIndex", ((NPC) actor).getIndex());
			payload.put("npcId", ((NPC) actor).getId());
			payload.put("npcName", actor.getName());
		}
		udpSender.send("HITSPLAT", payload);
	}

	// 50Hz, independent of FPS. The tracker is a few integer compares; the WorldPoint conversion
	// only runs on a transition (twice per walk).
	@Subscribe
	public void onClientTick(ClientTick event)
	{
		if (udpSender == null)
		{
			return;
		}
		Player local = client.getLocalPlayer();
		LocalPoint lp = (local != null && client.getGameState() == GameState.LOGGED_IN)
			? local.getLocalLocation() : null;
		PlayerMotionTracker.Transition t = lp == null
			? motionTracker.observe(null, null, 0)
			: motionTracker.observe(lp.getX(), lp.getY(), client.getTopLevelWorldView().getPlane());
		if (t == PlayerMotionTracker.Transition.NONE)
		{
			return;
		}
		Map<String, Object> payload = stampedPayload();
		if (lp != null)
		{
			// fromLocalInstance: instanced areas (Pest Control, ...) break the bare conversion --
			// see CLAUDE.md "Instanced areas break forward world-coordinate conversion".
			WorldPoint wp = WorldPoint.fromLocalInstance(client, lp);
			payload.put("x", wp.getX());
			payload.put("y", wp.getY());
			payload.put("plane", wp.getPlane());
			payload.put("localX", lp.getX());
			payload.put("localY", lp.getY());
		}
		udpSender.send(t == PlayerMotionTracker.Transition.MOVING ? "PLAYER_MOVING" : "PLAYER_STOPPED", payload);
	}
```

- [ ] **Step 5: Compile and run the whole Java test suite**

Run: `./gradlew.bat compileJava test`
Expected: BUILD SUCCESSFUL. If `WorldPoint.fromLocalInstance` or `getTopLevelWorldView()` does not resolve, check the imports (`net.runelite.api.*` and `net.runelite.api.coords.WorldPoint` are already imported at lines 9-10).

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/example/providers/EventsProvider.java src/main/java/com/example/providers/PlayerProvider.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "events: client-event-driven UDP (varbit/widget/menu/graphic/hitsplat), per-frame PLAYER_MOVING/STOPPED, /events/subscribe, /player.moving"
```

Note for the user: the client must be rebuilt and restarted (`./gradlew.bat run`) before any of this exists on the bridge. Python tasks below do not need it until Task 12.

---

### Task 4: `bridge_events` -- sequence numbers, `mark()`, `Outcome`, `await_outcome`, `wait_any`

**Files:**
- Modify: `ControllerAddon/settings.py` (add two constants near `DELAY_SKEW_RANGE`, ~line 405)
- Modify: `ControllerAddon/bridge_events.py` (`EventStream.__init__`, `pump`, `wait_for_event`, new methods, module-level function)
- Test: `ControllerAddon/tests/test_bridge_events.py` (update the two `for t, _ in s._recent` lines at ~159 and ~180 to `for t, *_ in s._recent`)

**Interfaces:**
- Produces: `EventStream.mark() -> int`; `EventStream.await_outcome(signals, budget_s, confirm=None, since=None, reaction=True, shorten=None, now_fn=time.time, sleep_fn=time.sleep) -> Outcome`; `EventStream.wait_any(timeout_s, ignore=("TICK",), now_fn, sleep_fn) -> bool`; module `await_outcome(events, signals, budget_s, confirm=None, since=None, reaction=True, shorten=None, now_fn, sleep_fn) -> Outcome`; `class Outcome(hit, signal, payload, confirmed, latency_s)` with `__bool__`.
- `signals`: a `str`, a sequence of event-type strings, or a `dict {event_type: predicate_or_None}`.
- `shorten`: `dict {event_type: seconds}` -- when such an event lands after `since`, the deadline becomes `min(deadline, now + seconds)` (used for "PLAYER_STOPPED, then the action's own short budget").

- [ ] **Step 1: Settings**

Add to `settings.py` after `DELAY_SKEW_RANGE`:

```python
# Event-driven outcomes (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md).
# REACTION_DELAY_S: after a confirmed outcome event, bridge_events.await_outcome sleeps a draw from
# this range (minus the time the HTTP confirm already took) before returning, so the next click does
# not land tens of milliseconds after the event every time. Basis: the agility human recording's
# next click lands ~0.2s after the XP drop. Re-fit from the GOTR benchmark with fit_reaction.py.
REACTION_DELAY_S = (0.15, 0.35)
# Polling cadence of the HTTP confirm when there is no event stream at all (ctx.events is None).
FALLBACK_POLL_S = 0.25
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_bridge_events.py`:

```python
# --- await_outcome / mark / wait_any (2026-09-04 event-driven outcomes) ------------------

class _Clock:
    """Injected wall clock: sleep_fn advances it, so a test never really sleeps."""
    def __init__(self, t=1000.0):
        self.t = t
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _no_pad(monkeypatch):
    monkeypatch.setattr(bridge_events.controller, "sample_human_delay", lambda a, b: 0.0)


def test_mark_and_since_exclude_earlier_identical_events(monkeypatch):
    _no_pad(monkeypatch)
    clk = _Clock()
    s = _stream(monkeypatch, [[("INVENTORY_CHANGED", None)], [], [("INVENTORY_CHANGED", None)]])
    s.pump()                                   # the stale one from a previous action
    mark = s.mark()
    res = s.await_outcome({"INVENTORY_CHANGED": None}, budget_s=1.0, confirm=lambda: True,
                          since=mark, now_fn=clk.now, sleep_fn=clk.sleep)
    assert res and res.signal == "INVENTORY_CHANGED"
    # It needed the SECOND event (pumped after one sleep), not the stale first one.
    assert len(clk.sleeps) >= 1


def test_await_outcome_confirms_over_http_and_rejects_a_false_confirm(monkeypatch):
    _no_pad(monkeypatch)
    clk = _Clock()
    s = _stream(monkeypatch, [[("XP_GAINED", {"skill": "Runecraft"})], [("XP_GAINED", {"skill": "Runecraft"})]])
    answers = iter([False, True, True])
    res = s.await_outcome({"XP_GAINED": lambda p: p.get("skill") == "Runecraft"}, budget_s=2.0,
                          confirm=lambda: next(answers), now_fn=clk.now, sleep_fn=clk.sleep)
    assert res.hit and res.signal == "XP_GAINED" and res.confirmed is True
    assert res.payload == {"skill": "Runecraft"}


def test_await_outcome_falls_back_to_the_final_confirm_at_budget(monkeypatch):
    _no_pad(monkeypatch)
    clk = _Clock()
    s = _stream(monkeypatch, [])
    calls = []
    res = s.await_outcome("INVENTORY_CHANGED", budget_s=0.5,
                          confirm=lambda: calls.append(1) or "delta", now_fn=clk.now, sleep_fn=clk.sleep)
    assert res.hit and res.signal is None and res.confirmed == "delta"
    assert calls == [1]                        # confirm runs ONCE, at the budget
    assert clk.t == pytest.approx(1000.5, abs=0.03)


def test_await_outcome_is_falsy_when_nothing_happened(monkeypatch):
    _no_pad(monkeypatch)
    clk = _Clock()
    s = _stream(monkeypatch, [])
    res = s.await_outcome(["INVENTORY_CHANGED"], budget_s=0.3, confirm=lambda: None,
                          now_fn=clk.now, sleep_fn=clk.sleep)
    assert not res and res.signal is None


def test_await_outcome_without_confirm_trusts_the_signal(monkeypatch):
    _no_pad(monkeypatch)
    clk = _Clock()
    s = _stream(monkeypatch, [[("MENU_CLICKED", {"option": "Enter"})]])
    res = s.await_outcome("MENU_CLICKED", budget_s=1.0, now_fn=clk.now, sleep_fn=clk.sleep)
    assert res and res.payload["option"] == "Enter" and res.confirmed is True


def test_reaction_padding_is_reduced_by_confirm_time_and_skippable(monkeypatch):
    monkeypatch.setattr(bridge_events.controller, "sample_human_delay", lambda a, b: 0.30)
    clk = _Clock()
    s = _stream(monkeypatch, [[("INVENTORY_CHANGED", None)]])

    def slow_confirm():
        clk.t += 0.10       # the HTTP read took 100ms
        return True
    res = s.await_outcome("INVENTORY_CHANGED", budget_s=1.0, confirm=slow_confirm,
                          now_fn=clk.now, sleep_fn=clk.sleep)
    assert res and clk.sleeps[-1] == pytest.approx(0.20)

    s2 = _stream(monkeypatch, [[("INVENTORY_CHANGED", None)]])
    clk2 = _Clock()
    s2.await_outcome("INVENTORY_CHANGED", budget_s=1.0, confirm=lambda: True, reaction=False,
                     now_fn=clk2.now, sleep_fn=clk2.sleep)
    assert clk2.sleeps == []


def test_shorten_pulls_the_deadline_in_after_a_stop(monkeypatch):
    _no_pad(monkeypatch)
    clk = _Clock()
    s = _stream(monkeypatch, [[("PLAYER_STOPPED", {"x": 1})]])
    res = s.await_outcome("INVENTORY_CHANGED", budget_s=10.0, confirm=lambda: None,
                          shorten={"PLAYER_STOPPED": 0.5}, now_fn=clk.now, sleep_fn=clk.sleep)
    assert not res
    assert clk.t == pytest.approx(1000.5, abs=0.03)


def test_module_await_outcome_polls_confirm_without_a_stream(monkeypatch):
    _no_pad(monkeypatch)
    clk = _Clock()
    answers = iter([None, None, 3])
    res = bridge_events.await_outcome(None, "INVENTORY_CHANGED", budget_s=5.0,
                                      confirm=lambda: next(answers), now_fn=clk.now, sleep_fn=clk.sleep)
    assert res and res.confirmed == 3 and res.signal is None
    assert clk.sleeps == [bridge_events.FALLBACK_POLL_S, bridge_events.FALLBACK_POLL_S]


def test_wait_any_wakes_on_a_real_event_but_not_on_tick(monkeypatch):
    clk = _Clock()
    s = _stream(monkeypatch, [[("TICK", {"tick": 1, "atMillis": 1.0})], [], [("XP_GAINED", {"skill": "Mining"})]])
    assert s.wait_any(1.0, now_fn=clk.now, sleep_fn=clk.sleep) is True
    assert len(clk.sleeps) == 2
    assert [t for t, _ in s.drain()] == ["TICK", "XP_GAINED"]   # nothing lost for on_event


def test_wait_any_times_out_quietly(monkeypatch):
    clk = _Clock()
    s = _stream(monkeypatch, [])
    assert s.wait_any(0.1, now_fn=clk.now, sleep_fn=clk.sleep) is False
    assert clk.t == pytest.approx(1000.1, abs=0.03)
```

Also change the two existing lines `types = [t for t, _ in s._recent]` to `types = [t for _seq, t, _ in s._recent]`.

- [ ] **Step 3: Run tests to verify they fail**

Run (from `ControllerAddon/`): `python -m pytest tests/test_bridge_events.py -q`
Expected: the new tests fail (`AttributeError: 'EventStream' object has no attribute 'mark'`), the two edited old tests fail on tuple unpacking.

- [ ] **Step 4: Implement**

In `bridge_events.py`:

1. Add near the top: `import settings` and

```python
FALLBACK_POLL_S = settings.FALLBACK_POLL_S
WAKE_IGNORED_TYPES = ("TICK",)   # wait_any never wakes the engine loop on a tick boundary --
                                 # a loop aligned to the server tick aligns every click to it too


class Outcome:
    """Result of await_outcome. Falsy when nothing happened. `signal` is the event type that
    ended the wait (None when the final HTTP confirm decided), `confirmed` the confirm's value."""
    __slots__ = ("hit", "signal", "payload", "confirmed", "latency_s")

    def __init__(self, hit, signal, payload, confirmed, latency_s):
        self.hit = bool(hit)
        self.signal = signal
        self.payload = payload
        self.confirmed = confirmed
        self.latency_s = latency_s

    def __bool__(self):
        return self.hit

    def __repr__(self):
        return (f"Outcome(hit={self.hit}, signal={self.signal!r}, "
                f"latency_s={self.latency_s:.2f})")


def _normalize_signals(signals):
    if isinstance(signals, str):
        return {signals: None}
    if isinstance(signals, dict):
        return signals
    return {t: None for t in signals}


def _reaction_pad(spent_s, sleep_fn):
    draw = controller.sample_human_delay(*settings.REACTION_DELAY_S)
    pad = max(0.0, draw - spent_s)
    if pad > 0:
        sleep_fn(pad)
```

2. `EventStream.__init__`: add `self._seq = 0`.

3. `pump`: every appended entry becomes a 3-tuple. Replace `self._recent.append((event_type, payload))` with:

```python
            self._seq += 1
            self._recent.append((self._seq, event_type, payload))
```

and the prune's `_still_fresh` unpacks `_seq, _event_type, payload = entry`. `_pending` keeps 2-tuples (`self._pending.extend(events)` unchanged) -- that is the `drain()` contract.

4. `wait_for_event`: the scan loop becomes `for _seq, event_type, payload in self._recent:`.

5. New methods on `EventStream`:

```python
    def mark(self):
        """Sequence number of the last pumped event. Take one BEFORE the action whose outcome
        you will wait for and pass it as await_outcome(since=...): only later events can match,
        so an identical event from a previous action can never satisfy the wait -- without
        discarding anything on_event still needs (the reason this replaced clear())."""
        return self._seq

    def await_outcome(self, signals, budget_s, confirm=None, since=None, reaction=True,
                      shorten=None, now_fn=time.time, sleep_fn=time.sleep):
        """Block until a listed signal lands (after `since`) AND `confirm()` -- one HTTP read --
        agrees, or `budget_s` passes, in which case confirm() decides alone (today's polling
        behaviour, so a dropped packet costs latency, never correctness).

        signals: str | sequence | {type: predicate-or-None}. confirm: zero-arg callable whose
        truthy value is the outcome; None means the signal IS the truth (MENU_CLICKED).
        shorten: {type: seconds} -- that event pulls the deadline in to now + seconds (the
        "arrived, now the action itself has this long" shape). reaction: pad the return with
        a human reaction draw (settings.REACTION_DELAY_S) minus the confirm's own duration."""
        signals = _normalize_signals(signals)
        shorten = shorten or {}
        start = now_fn()
        deadline = start + budget_s
        if since is None:
            since = self._seq           # before the first pump: nothing that arrives during
                                        # this call is excluded
        rejected = set()                # seqs whose confirm() came back falsy
        shortened = set()
        self.pump(now=now_fn())
        while True:
            now = now_fn()
            for seq, event_type, payload in self._recent:
                if seq <= since:
                    continue
                if event_type in shorten and seq not in shortened:
                    shortened.add(seq)
                    deadline = min(deadline, now + shorten[event_type])
                if event_type not in signals or seq in rejected:
                    continue
                predicate = signals[event_type]
                if predicate is not None and not predicate(payload or {}):
                    continue
                t0 = now_fn()
                value = True if confirm is None else confirm()
                if value:
                    if reaction:
                        _reaction_pad(now_fn() - t0, sleep_fn)
                    return Outcome(True, event_type, payload or {}, value, now_fn() - start)
                rejected.add(seq)
            now = now_fn()
            if now >= deadline:
                break
            sleep_fn(min(EVENT_POLL_INTERVAL_S, deadline - now))
            self.pump(now=now_fn())
        value = None if confirm is None else confirm()
        return Outcome(bool(value), None, None, value, now_fn() - start)

    def wait_any(self, timeout_s, ignore=WAKE_IGNORED_TYPES, now_fn=time.time, sleep_fn=time.sleep):
        """Sleep at most `timeout_s`, returning True as soon as any event not in `ignore`
        arrives. The engine loop's replacement for a flat sleep between iterations. Events
        pumped here stay in _pending for the next drain()."""
        deadline = now_fn() + timeout_s
        while True:
            events = self.pump(now=now_fn())
            if any(t not in ignore for t, _ in events):
                return True
            now = now_fn()
            if now >= deadline:
                return False
            sleep_fn(min(EVENT_POLL_INTERVAL_S, deadline - now))
```

6. Module-level function (after the class):

```python
def await_outcome(events, signals, budget_s, confirm=None, since=None, reaction=True,
                  shorten=None, now_fn=time.time, sleep_fn=time.sleep):
    """await_outcome for a routine holding ctx.events, which may be None (engine started no
    listener, or --no-events): then confirm() is polled every FALLBACK_POLL_S until truthy or
    the budget -- the same HTTP polling the routines did before this existed."""
    if events is not None:
        return events.await_outcome(signals, budget_s, confirm=confirm, since=since,
                                    reaction=reaction, shorten=shorten,
                                    now_fn=now_fn, sleep_fn=sleep_fn)
    start = now_fn()
    deadline = start + budget_s
    while True:
        t0 = now_fn()
        value = None if confirm is None else confirm()
        if value:
            if reaction:
                _reaction_pad(now_fn() - t0, sleep_fn)
            return Outcome(True, None, None, value, now_fn() - start)
        now = now_fn()
        if now >= deadline:
            return Outcome(bool(value), None, None, value, now - start)
        sleep_fn(min(FALLBACK_POLL_S, deadline - now))
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_bridge_events.py -q`
Expected: all pass (old and new). Then `python -m pytest tests/ -q` -- everything else still green (chinchompa/mining tests use `wait_for_event`).

- [ ] **Step 6: Commit**

```bash
git add settings.py bridge_events.py tests/test_bridge_events.py
git commit -m "bridge_events: mark()/since, Outcome, await_outcome (signal + HTTP confirm + reaction pad), wait_any"
```

---

### Task 5: Engine -- `subscribe_varbits`, subscription posting, `--no-events`, loop wake, loop timing

**Files:**
- Modify: `ControllerAddon/controller.py` (header comment block near line 43 for the routine contract; a new `subscribe_varbits` after `get_player` ~line 295; `LoopStats` before `class Context` ~line 2884; `main()` argparse ~3056, listener creation ~3074, routine load ~3138, loop bottom ~3225)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: `EventStream.wait_any` (Task 4), `GET /events/subscribe` (Task 3).
- Produces: `controller.subscribe_varbits(varbits=(), varps=()) -> dict | None`; `controller.LoopStats(now_fn).record(iteration_s, tick_s) -> list[str]`; routine module attributes `VARBIT_SUBSCRIPTIONS`, `VARP_SUBSCRIPTIONS`; CLI flag `--no-events`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_controller.py` (it already imports `controller`; check its header):

```python
# --- event-driven outcomes engine plumbing (2026-09-04) ------------------------------

def test_subscribe_varbits_posts_csv_and_returns_the_bridge_answer(monkeypatch):
    seen = {}

    def fake_get(path, params=None, timeout=0.3):
        seen["path"], seen["params"] = path, params
        return {"varbits": [5667, 13691], "varps": []}
    monkeypatch.setattr(controller, "api_get", fake_get)
    controller._subscribe_warned = False
    assert controller.subscribe_varbits((13691, 5667)) == {"varbits": [5667, 13691], "varps": []}
    assert seen["path"] == "/events/subscribe"
    assert seen["params"] == {"varbits": "13691,5667", "varps": ""}


def test_subscribe_varbits_warns_once_when_the_bridge_lacks_the_endpoint(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda *a, **k: None)
    logged = []
    monkeypatch.setattr(controller.debug_log, "log", lambda cat, msg: logged.append(msg))
    controller._subscribe_warned = False
    assert controller.subscribe_varbits((1,)) is None
    assert controller.subscribe_varbits((1,)) is None
    assert len([m for m in logged if "events/subscribe" in m]) == 1


def test_loop_stats_logs_slow_iterations_and_a_periodic_summary():
    t = [0.0]
    stats = controller.LoopStats(now_fn=lambda: t[0])
    assert stats.record(0.4, 0.2) == []
    lines = stats.record(1.6, 1.1)
    assert lines and "slow loop iteration 1.60s" in lines[0] and "tick 1.10s" in lines[0]
    t[0] = controller.LOOP_STATS_EVERY_S + 1
    lines = stats.record(0.5, 0.3)
    summary = [l for l in lines if "iterations" in l]
    assert summary and "3 iterations" in summary[0] and "mean 0.83s" in summary[0]
    assert stats.record(0.5, 0.3) == []       # window reset after the summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_controller.py -q -k "subscribe_varbits or loop_stats"`
Expected: FAIL, attributes missing.

- [ ] **Step 3: Implement in `controller.py`**

After `get_player()`:

```python
_subscribe_warned = False


def subscribe_varbits(varbits=(), varps=()):
    """REPLACE the set of varbits/varps the bridge pushes as VARBIT_CHANGED (GET
    /events/subscribe, EventsProvider.java). Empty clears. None when the bridge cannot answer
    (older client without the endpoint: logged once, varbit events simply stay off)."""
    global _subscribe_warned
    params = {"varbits": ",".join(str(v) for v in varbits),
              "varps": ",".join(str(v) for v in varps)}
    result = api_get("/events/subscribe", params=params)
    if result is None and not _subscribe_warned:
        _subscribe_warned = True
        debug_log.log("!", "bridge has no /events/subscribe -- VARBIT_CHANGED events are off "
                           "(rebuild + restart the client)")
    return result


def _post_routine_subscriptions(routine):
    """The routine contract's VARBIT_SUBSCRIPTIONS / VARP_SUBSCRIPTIONS, posted on routine load
    and again after every login (a client restart drops the set)."""
    varbits = tuple(getattr(routine, "VARBIT_SUBSCRIPTIONS", ()) or ())
    varps = tuple(getattr(routine, "VARP_SUBSCRIPTIONS", ()) or ())
    subscribe_varbits(varbits, varps)
```

Before `class Context`:

```python
LOOP_SLOW_LOG_S = 1.0        # one engine-loop iteration slower than this is logged on its own
LOOP_STATS_EVERY_S = 60.0    # and a mean/p90 summary this often


class LoopStats:
    """Engine-loop iteration timing. The loop iteration is the reaction FLOOR whenever a routine
    hands back to the engine, and the ~1.1s figure CLAUDE.md remembers was never measured --
    this is the measurement. record() returns the log lines to emit (pure, testable)."""

    def __init__(self, now_fn=time.monotonic):
        self._now = now_fn
        self._window_start = now_fn()
        self._iters = []
        self._ticks = []

    def record(self, iteration_s, tick_s):
        lines = []
        self._iters.append(iteration_s)
        self._ticks.append(tick_s)
        if iteration_s > LOOP_SLOW_LOG_S:
            lines.append(f"slow loop iteration {iteration_s:.2f}s (tick {tick_s:.2f}s)")
        now = self._now()
        if now - self._window_start >= LOOP_STATS_EVERY_S:
            n = len(self._iters)
            mean = sum(self._iters) / n
            p90 = sorted(self._iters)[min(n - 1, int(0.9 * n))]
            tick_mean = sum(self._ticks) / n
            lines.append(f"loop: {n} iterations, mean {mean:.2f}s, p90 {p90:.2f}s, "
                         f"tick mean {tick_mean:.2f}s")
            self._window_start = now
            self._iters, self._ticks = [], []
        return lines
```

In `main()`:

- argparse, after `--debug-log`:

```python
    parser.add_argument(
        "--no-events",
        action="store_true",
        help=(
            "Start no UDP event listener: ctx.events is None, every await_outcome falls back to "
            "HTTP polling and the engine loop sleeps a flat STATE_POLL_INTERVAL. The A/B control "
            "for the event-driven path. NOTE: UDP-only safety signals (PLAYER_DIED via "
            "handle_generic_events) are off too -- do not run unattended with this."
        ),
    )
```

- listener creation:

```python
    if args.no_events:
        udp = None
        event_stream = None
        debug_log.log("!", "--no-events: no UDP listener, HTTP polling only")
    else:
        udp = make_udp_listener(UDP_PORT)
        event_stream = bridge_events.EventStream(udp)
```

- loop head (`while True:` ... `events = event_stream.drain()`): declare `loop_stats = LoopStats()`, `iter_start = None`, `last_tick_s = 0.0`, `subs_posted_for = None` before the loop; at the top of each iteration:

```python
            now_mono = time.monotonic()
            if iter_start is not None:
                for line in loop_stats.record(now_mono - iter_start, last_tick_s):
                    debug_log.log("engine", line)
            iter_start = now_mono
            last_tick_s = 0.0
            events = event_stream.drain() if event_stream is not None else []
```

- after the `if desired_path != active_path:` block (routine loaded) and after the login/focus gates, before `check_safety`:

```python
            if subs_posted_for != active_path:
                _post_routine_subscriptions(routine)
                subs_posted_for = active_path
```

and in the `if not login.is_fully_logged_in():` branch add `subs_posted_for = None` (so a relogin re-posts).

- around `routine.tick(ctx)`:

```python
            tick_start = time.monotonic()
            try:
                routine.tick(ctx)
            except Exception as exc:
                debug_log.log_exception("tick", exc)
                raise
            last_tick_s = time.monotonic() - tick_start
```

- loop bottom, replacing `time.sleep(STATE_POLL_INTERVAL)`:

```python
            if event_stream is not None:
                event_stream.wait_any(STATE_POLL_INTERVAL)   # wakes on any non-TICK event
            else:
                time.sleep(STATE_POLL_INTERVAL)
```

- header comment (the routine-contract block near line 43): add

```
#   VARBIT_SUBSCRIPTIONS = (13691, ...)   # optional; VARP_SUBSCRIPTIONS likewise. Varbit/varp ids
#       the bridge should push as VARBIT_CHANGED UDP events for this routine (opt-in, replace
#       semantics, re-posted after every login). See bridge_events.await_outcome.
```

- [ ] **Step 4: Compile and test**

Run: `python -m py_compile controller.py && python -m pytest tests/test_controller.py tests/test_bridge_events.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add controller.py tests/test_controller.py
git commit -m "engine: subscribe_varbits + VARBIT_SUBSCRIPTIONS posting, --no-events, wake-on-event loop, LoopStats timing"
```

---

### Task 6: Measurement tools -- `log_gaps.py`, `fit_reaction.py`

**Files:**
- Create: `ControllerAddon/log_gaps.py`, `ControllerAddon/fit_reaction.py`
- Test: `ControllerAddon/tests/test_log_tools.py`

**Interfaces:**
- Produces: `log_gaps.gaps(lines) -> list[(gap_s, index, line)]` sorted descending, `log_gaps.summary(gaps, thresholds=(0.5,1,2,4)) -> dict`; `fit_reaction.reaction_deltas(records, max_ms=3000) -> list[int]` (ms from each `inv`/`xp` record to the next `action` record).

- [ ] **Step 1: Write the failing tests**

```python
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import log_gaps
import fit_reaction


def test_gaps_are_measured_between_timestamped_lines_and_sorted():
    lines = ["[07:00:00.000 tick=1] a", "junk", "[07:00:01.500 tick=2] b", "[07:00:06.000 tick=3] c"]
    g = log_gaps.gaps(lines)
    assert [round(x[0], 1) for x in g] == [4.5, 1.5]
    assert g[0][2].endswith(" b")          # the line BEFORE the biggest gap
    s = log_gaps.summary(g)
    assert s["total_s"] == 6.0 and s["over"][4.0] == 4.5 and s["over"][1.0] == 6.0


def test_reaction_deltas_pair_each_outcome_with_the_next_action():
    recs = [{"kind": "inv", "t": 1000}, {"kind": "mouse", "t": 1100},
            {"kind": "action", "t": 1250}, {"kind": "xp", "t": 5000},
            {"kind": "action", "t": 9500}, {"kind": "xp", "t": 9600}]
    assert fit_reaction.reaction_deltas(recs) == [250]      # 4500 is over max_ms; last has no action
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_log_tools.py -q` -- ModuleNotFoundError.

- [ ] **Step 3: Implement**

`log_gaps.py`:

```python
"""Where does a routine's time go? Gaps between consecutive timestamped log lines.

    python log_gaps.py logs/gotr_live76.log [--top 25]

Built for the 2026-09-04 event-driven-outcomes work (run 76 baseline: 48% of the round in
gaps over 4s). Every '[HH:MM:SS.mmm tick=N]' line is a sample; the line BEFORE a gap is what
the routine was doing while it waited."""
import re
import sys

_TS = re.compile(r"\[(\d\d):(\d\d):(\d\d)\.(\d\d\d)")


def gaps(lines):
    out, prev = [], None
    for i, line in enumerate(lines):
        m = _TS.match(line)
        if not m:
            continue
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000.0
        if prev is not None:
            out.append((t - prev[0], prev[1], lines[prev[1]]))
        prev = (t, i)
    out.sort(key=lambda g: -g[0])
    return out


def summary(gs, thresholds=(0.5, 1.0, 2.0, 4.0)):
    total = sum(g for g, _, _ in gs)
    return {"total_s": total, "count": len(gs),
            "over": {thr: sum(g for g, _, _ in gs if g > thr) for thr in thresholds}}


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    top = int(argv[argv.index("--top") + 1]) if "--top" in argv else 25
    lines = open(argv[0], encoding="utf-8", errors="replace").read().splitlines()
    gs = gaps(lines)
    s = summary(gs)
    print(f"total span {s['total_s']:.1f}s over {s['count']} gaps")
    for thr, secs in s["over"].items():
        pct = 100 * secs / s["total_s"] if s["total_s"] else 0
        print(f"gaps>{thr}s: {secs:.0f}s ({pct:.0f}%)")
    print(f"--- top {top} gaps: seconds, line before")
    for g, i, line in gs[:top]:
        print(f"{g:5.1f}  L{i + 1}: {line[:140]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

`fit_reaction.py`:

```python
"""Human reaction time from a recorder .jsonl: ms from each outcome record (inventory delta,
XP drop) to the human's NEXT click. Feeds settings.REACTION_DELAY_S.

    python fit_reaction.py recordings/2026-09-03_061331_guaridianoftherift-benchmark.jsonl
"""
import json
import sys

OUTCOME_KINDS = ("inv", "xp")


def reaction_deltas(records, max_ms=3000):
    actions = sorted(r["t"] for r in records if r.get("kind") == "action")
    out = []
    for r in records:
        if r.get("kind") not in OUTCOME_KINDS:
            continue
        nxt = next((a for a in actions if a > r["t"]), None)
        if nxt is not None and nxt - r["t"] <= max_ms:
            out.append(nxt - r["t"])
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    records = []
    for line in open(argv[0], encoding="utf-8"):
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    d = sorted(reaction_deltas(records))
    if not d:
        print("no outcome->action pairs found")
        return 1
    q = lambda p: d[min(len(d) - 1, int(p * len(d)))]
    print(f"n={len(d)} p10={q(0.10)}ms p25={q(0.25)}ms median={q(0.5)}ms p75={q(0.75)}ms p90={q(0.90)}ms")
    print("suggested REACTION_DELAY_S = (%.2f, %.2f)  # p25..p75" % (q(0.25) / 1000, q(0.75) / 1000))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Test, then run both on real data**

Run: `python -m pytest tests/test_log_tools.py -q` -- pass.
Run: `python log_gaps.py logs/gotr_live76.log` -- expect "gaps>4.0s: 292s (48%)" (the baseline).
Run: `python fit_reaction.py recordings/2026-09-03_061331_guaridianoftherift-benchmark.jsonl` and record the printed numbers in the commit message. If p25..p75 differs materially from (0.15, 0.35), set `REACTION_DELAY_S` to the printed suggestion in `settings.py` and say so in the commit.

- [ ] **Step 5: Commit**

```bash
git add log_gaps.py fit_reaction.py tests/test_log_tools.py settings.py
git commit -m "tools: log_gaps.py (routine idle-gap histogram), fit_reaction.py (human reaction fit); REACTION_DELAY_S from the GOTR benchmark"
```

---

### Task 7: GOTR scaffolding -- `_await`, click marks, chaining, subscriptions

**Files:**
- Modify: `ControllerAddon/routines/guardians_of_the_rift.py` (constants block ~415-455; `_click_escalating` ~625; `tick` ~2173)
- Test: `ControllerAddon/tests/test_guardians_of_the_rift.py`

**Interfaces:**
- Consumes: `bridge_events.await_outcome`, `EventStream.mark()` (Task 4).
- Produces: `gotr._await(ctx, key, budget_s, signals, confirm, reaction=True, shorten=None) -> Outcome`; `gotr._inv_now() -> dict`; `gotr._energy_varbit(payload) -> bool`; `gotr._after_stop(seconds) -> dict`; `data["gotr_click_mark"]` set by `_click_escalating`; `data["gotr_outcome_hit"]` set by `_await`; `tick` = chain wrapper over `_tick_once`; module constants `VARBIT_SUBSCRIPTIONS`, `CHAIN_MAX_ACTIONS`, `CHAIN_MAX_S`, `ACTION_AFTER_STOP_S`, `MINE_AFTER_STOP_S`, `CRAFT_AFTER_STOP_S`, `WALK_START_BUDGET_S`, `PREROUND_MINE_BUDGET_S`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_guardians_of_the_rift.py` (it has `_Ctx` at ~line 403 -- reuse it; it exposes `.state.data`, `.state.config`, `.ser`; add `events=None` handling: check `_Ctx.__init__` and add `self.events = None` if absent):

```python
# --- event-driven outcomes (2026-09-04) ------------------------------------------------

import bridge_events


class _FakeEvents:
    """Stand-in EventStream: mark() counts, await_outcome returns a canned Outcome."""
    def __init__(self, outcome):
        self.outcome = outcome
        self.marks = 0
        self.calls = []

    def mark(self):
        self.marks += 1
        return self.marks

    def await_outcome(self, signals, budget_s, confirm=None, since=None, reaction=True,
                      shorten=None, now_fn=None, sleep_fn=None):
        self.calls.append({"signals": signals, "budget": budget_s, "since": since,
                           "shorten": shorten, "confirm": confirm})
        return self.outcome


def test_await_marks_hit_and_clears_the_commit(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents(bridge_events.Outcome(True, "INVENTORY_CHANGED", {}, True, 0.4))
    ctx.state.data["gotr_click_mark"] = 7
    res = gotr._await(ctx, "gotr_x_commit", 5.0, {"INVENTORY_CHANGED": None}, lambda: True)
    assert res and ctx.state.data["gotr_outcome_hit"] is True
    assert ctx.state.data["gotr_x_commit"] == 0.0
    assert ctx.events.calls[0]["since"] == 7 and "gotr_click_mark" not in ctx.state.data


def test_await_without_a_hit_leaves_no_chain_flag(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents(bridge_events.Outcome(False, None, None, None, 5.0))
    assert not gotr._await(ctx, "gotr_x_commit", 5.0, "INVENTORY_CHANGED", lambda: None)
    assert not ctx.state.data.get("gotr_outcome_hit")


def test_click_escalating_takes_a_mark_before_the_click(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents(None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    assert gotr._click_escalating(ctx, {"x": 1, "y": 2, "id": 5}, ("Mine",)) is True
    assert ctx.state.data["gotr_click_mark"] == 1


def test_tick_chains_while_outcomes_land_and_stops_at_the_cap(monkeypatch):
    ctx = _Ctx()
    runs = []

    def fake_once(c):
        runs.append(1)
        c.state.data["gotr_outcome_hit"] = True
    monkeypatch.setattr(gotr, "_tick_once", fake_once)
    gotr.tick(ctx)
    assert len(runs) == gotr.CHAIN_MAX_ACTIONS


def test_tick_does_not_chain_without_an_outcome(monkeypatch):
    ctx = _Ctx()
    runs = []
    monkeypatch.setattr(gotr, "_tick_once", lambda c: runs.append(1))
    gotr.tick(ctx)
    assert runs == [1]


def test_tick_chain_respects_the_time_cap(monkeypatch):
    ctx = _Ctx()
    clock = [0.0]
    monkeypatch.setattr(gotr.time, "time", lambda: clock[0])

    def fake_once(c):
        c.state.data["gotr_outcome_hit"] = True
        clock[0] += gotr.CHAIN_MAX_S
    runs = []
    monkeypatch.setattr(gotr, "_tick_once", lambda c: (runs.append(1), fake_once(c)))
    gotr.tick(ctx)
    assert len(runs) == 1


def test_varbit_subscriptions_cover_the_round_and_energy_signals():
    assert {13691, 13688, 13685, 13686, 13687, 5667} <= set(gotr.VARBIT_SUBSCRIPTIONS)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_guardians_of_the_rift.py -q -k "await or chain or mark or subscriptions"`
Expected: FAIL (`_await`, `_tick_once`, `VARBIT_SUBSCRIPTIONS` missing).

- [ ] **Step 3: Implement**

Top of the routine (near the other imports): `import bridge_events`.

Constants block (after `ACTIVE_SETTLE_S`):

```python
# --- event-driven outcomes (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md)
# Varbits the bridge pushes as VARBIT_CHANGED for this routine (VarbitID, runelite-api 1.12.38):
VB_IS_PLAYING = 13691
VB_FINISHED_GAME = 13688
VB_CATALYTIC_EARNED = 13685
VB_ELEMENTAL_EARNED = 13686
VB_PORTAL_VISIBLE = 13687
VB_MINING_COUNT = 5667
VARBIT_SUBSCRIPTIONS = (VB_IS_PLAYING, VB_FINISHED_GAME, VB_CATALYTIC_EARNED,
                        VB_ELEMENTAL_EARNED, VB_PORTAL_VISIBLE, VB_MINING_COUNT)
CHAIN_MAX_ACTIONS = 4          # a tick chains straight into the next rung after an outcome lands,
CHAIN_MAX_S = 15.0             # bounded so the engine's safety/focus/break gates stay responsive
ACTION_AFTER_STOP_S = 2.5      # once PLAYER_STOPPED lands at the target, the action itself gets this
MINE_AFTER_STOP_S = 4.0        # long (mining wind-up is 2-3 ticks before the first fragment)
CRAFT_AFTER_STOP_S = 3.0
WALK_START_BUDGET_S = 0.8      # after a walk click: wait for PLAYER_MOVING (or /player.moving) this long
PREROUND_MINE_BUDGET_S = 2.5   # a pre-round Mine attempt is REFUSED by the game -- short wait, re-try
```

Helpers, after `_commit`:

```python
def _inv_now():
    """Fresh inventory counts from the GOTR reader (one HTTP read)."""
    return (read_state() or {}).get("inventory") or {}


def _energy_varbit(payload):
    return payload.get("varbitId") in (VB_ELEMENTAL_EARNED, VB_CATALYTIC_EARNED)


def _after_stop(seconds):
    """shorten= for _await: the walk is over when PLAYER_STOPPED lands; the action gets `seconds`."""
    return {"PLAYER_STOPPED": seconds}


def _await(ctx, key, budget_s, signals, confirm, reaction=True, shorten=None):
    """The replacement for _commit's fixed window: block until the click's OUTCOME -- a listed
    UDP signal confirmed by one HTTP read -- or `budget_s`, whichever first (at the budget the
    HTTP read decides alone, exactly the old polling). A hit sets gotr_outcome_hit so tick()
    chains into the next rung instead of handing back to the engine loop. `key` keeps the
    _committed() contract for any re-entrant check while the wait is in progress."""
    data = ctx.state.data
    data[key] = time.time() + budget_s
    res = bridge_events.await_outcome(ctx.events, signals, budget_s, confirm=confirm,
                                      since=data.pop("gotr_click_mark", None),
                                      reaction=reaction, shorten=shorten)
    data[key] = 0.0
    if res:
        data["gotr_outcome_hit"] = True
        debug_log.log("gotr", f"{key[5:]}: outcome via {res.signal or 'http at budget'} "
                              f"after {res.latency_s:.2f}s")
    else:
        debug_log.log("gotr", f"{key[5:]}: no outcome within {budget_s:.1f}s")
    return res
```

`_click_escalating`: first statement of the function body:

```python
    if ctx.events is not None:
        ctx.state.data["gotr_click_mark"] = ctx.events.mark()
```

`tick`: rename the existing function to `_tick_once` and add:

```python
def tick(ctx):
    """One engine tick = one rung, PLUS every rung whose predecessor's outcome landed, up to
    CHAIN_MAX_ACTIONS / CHAIN_MAX_S (the mining/agility chaining shape). Every ceiling and the
    stuck watch count a chained action exactly as a ticked one."""
    data = ctx.state.data
    deadline = time.time() + CHAIN_MAX_S
    for _ in range(CHAIN_MAX_ACTIONS):
        data["gotr_outcome_hit"] = False
        _tick_once(ctx)
        if (not data.get("gotr_outcome_hit") or ctx.state.safety_stopped
                or time.time() >= deadline):
            return
```

- [ ] **Step 4: Test**

Run: `python -m py_compile routines/guardians_of_the_rift.py && python -m pytest tests/test_guardians_of_the_rift.py -q`
Expected: pass (all 59 existing + the new ones). If an existing test calls `gotr.tick` expecting a single pass, it still holds (no outcome flag -> one pass).

- [ ] **Step 5: Commit**

```bash
git add routines/guardians_of_the_rift.py tests/test_guardians_of_the_rift.py
git commit -m "gotr: _await outcome helper, click marks, in-tick chaining, VARBIT_SUBSCRIPTIONS"
```

---

### Task 8: GOTR conversions A -- approach/click sites, restock, deposit, Power-up, rubble

**Files:**
- Modify: `ControllerAddon/routines/guardians_of_the_rift.py` (`_approach_or_click` ~897; `repair_barrier_phase` ~1290; `create_guardian_phase` ~1366; `place_cell_phase` ~1392; `_restock_cells` ~1244; `_deposit_runes` ~1263; `spend_stones_phase` ~1311; `_climb_rubble` ~735; `restock_cells_phase` ~2346; `between_rounds_phase` call of `_restock_cells` ~1180)
- Test: `ControllerAddon/tests/test_guardians_of_the_rift.py`

**Interfaces:**
- Consumes: `_await`, `_inv_now`, `_energy_varbit`, `_after_stop` (Task 7).
- Produces: `_approach_or_click(ctx, entity, options, counter_key, max_clicks, stop_reason, signals=None, confirm=None, budget_s=None, after_stop_s=ACTION_AFTER_STOP_S)`; `_restock_cells(ctx, st)`.

- [ ] **Step 1: Write the failing tests**

```python
def _outcome_events(hit=True):
    return _FakeEvents(bridge_events.Outcome(hit, "INVENTORY_CHANGED" if hit else None, {}, hit, 0.3))


def test_approach_or_click_awaits_the_given_outcome(monkeypatch):
    ctx = _Ctx()
    ctx.events = _outcome_events()
    monkeypatch.setattr(gotr.travel, "can_reach", lambda e: True)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 1.0)
    confirm = lambda: True
    assert gotr._approach_or_click(ctx, {"x": 1, "y": 1}, ("Take",), "gotr_restock_clicks", 6,
                                   "never", signals="INVENTORY_CHANGED", confirm=confirm) is True
    call = ctx.events.calls[0]
    assert call["signals"] == "INVENTORY_CHANGED" and call["confirm"] is confirm
    assert call["budget"] == gotr.ACTION_COMMIT_S + 1.0
    assert call["shorten"] == {"PLAYER_STOPPED": gotr.ACTION_AFTER_STOP_S}
    assert ctx.state.data["gotr_outcome_hit"] is True


def test_approach_or_click_without_an_outcome_spec_keeps_the_legacy_commit(monkeypatch):
    ctx = _Ctx()
    ctx.events = _outcome_events()
    monkeypatch.setattr(gotr.travel, "can_reach", lambda e: True)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 0.0)
    assert gotr._approach_or_click(ctx, {"x": 1, "y": 1}, ("Take",), "gotr_k", 6, "never") is True
    assert ctx.events.calls == [] and ctx.state.data["gotr_k_commit"] > 0


def test_restock_confirm_is_more_uncharged_cells(monkeypatch):
    ctx = _Ctx()
    ctx.events = _outcome_events()
    st = make_state(inventory=make_inv(unchargedCells=3))
    monkeypatch.setattr(gotr, "_find_object_by_name", lambda *a, **k: {"x": 1, "y": 1, "name": "Uncharged cells"})
    monkeypatch.setattr(gotr.travel, "can_reach", lambda e: True)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 0.0)
    monkeypatch.setattr(gotr, "_inv_now", lambda: make_inv(unchargedCells=8))
    gotr._restock_cells(ctx, st)
    assert ctx.events.calls[0]["confirm"]() is True
    monkeypatch.setattr(gotr, "_inv_now", lambda: make_inv(unchargedCells=3))
    assert not ctx.events.calls[0]["confirm"]()


def test_powerup_confirm_is_fewer_stones(monkeypatch):
    ctx = _Ctx()
    ctx.events = _outcome_events()
    st = make_state(inventory=make_inv(elementalGuardianStones=20))
    monkeypatch.setattr(gotr, "_find_npc_by_name", lambda s: {"index": 3, "name": "The Great Guardian"})
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "mark_busy", lambda s: None)
    monkeypatch.setattr(gotr, "_inv_now", lambda: make_inv(elementalGuardianStones=0))
    gotr.spend_stones_phase(ctx, st)
    call = ctx.events.calls[0]
    assert "VARBIT_CHANGED" in call["signals"] and call["confirm"]() is True
    assert ctx.state.data["gotr_click_mark"] is None or "gotr_click_mark" not in ctx.state.data
```

(`_Ctx` must expose `.ser`; `controller.human_delay` should already be stubbed in that test module's fixtures -- if not, `monkeypatch.setattr(controller, "human_delay", lambda a, b: None)` in each test.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_guardians_of_the_rift.py -q -k "approach_or_click or restock_confirm or powerup_confirm"` -- FAIL (TypeError on the new kwargs / `_restock_cells` arity).

- [ ] **Step 3: Implement**

`_approach_or_click`:

```python
def _approach_or_click(ctx, entity, options, counter_key, max_clicks, stop_reason,
                       signals=None, confirm=None, budget_s=None,
                       after_stop_s=ACTION_AFTER_STOP_S):
    """The standard gated click: ceiling -> can_reach -> walk when unreachable ->
    escalating click -> WAIT FOR THE OUTCOME (signals + confirm; see _await). Without an
    outcome spec the old fixed commit window is kept. A routeless walk ALSO spends the
    counter (every loop needs a ceiling). True only when a click was issued."""
    data = ctx.state.data
    if _committed(data, counter_key + "_commit"):
        return False
    if data.get(counter_key, 0) >= max_clicks:
        ctx.state.trigger_safety_stop(f"gotr: {stop_reason}")
        return False
    reach = travel.can_reach(entity)
    if reach is False and not _arena_direct_click_ok(entity):
        if not _walk_toward(ctx, entity):
            data[counter_key] = data.get(counter_key, 0) + 1
        return False
    data[counter_key] = data.get(counter_key, 0) + 1
    budget = (budget_s if budget_s is not None else ACTION_COMMIT_S) + _walk_budget(entity)
    if _click_escalating(ctx, entity, options):
        if signals is None and confirm is None:
            _commit(data, counter_key + "_commit", budget)
        else:
            _await(ctx, counter_key + "_commit", budget, signals or "INVENTORY_CHANGED",
                   confirm, shorten=_after_stop(after_stop_s))
    return True
```

Confirm helpers (after `_after_stop`):

```python
def _confirm_cell_gone():
    return (_inv_now().get("chargedCellTier") or "NONE") == "NONE"


def _confirm_repaired(frags_before):
    st2 = read_state() or {}
    inv2 = st2.get("inventory") or {}
    return (inv2.get("guardianFragments", 0) < frags_before
            or (st2.get("barriers") and find_repairable(st2["barriers"]) is None))


def _confirm_fewer_stones(stones_before):
    return total_stones(_inv_now()) < stones_before


def _confirm_more_cells(cells_before):
    return _inv_now().get("unchargedCells", 0) > cells_before


CELL_SIGNALS = {"INVENTORY_CHANGED": None, "VARBIT_CHANGED": _energy_varbit}
```

Call sites:

- `repair_barrier_phase`: `frags = (st.get("inventory") or {}).get("guardianFragments", 0)` before the call; pass `signals="INVENTORY_CHANGED", confirm=lambda: _confirm_repaired(frags)`. After the `if _approach_or_click(...)`: keep `_hold_action` and `mark_busy`; change `controller.human_delay(0.5, 0.9)` to `if not data.get("gotr_outcome_hit"): controller.human_delay(0.5, 0.9)`.
- `create_guardian_phase` and `place_cell_phase`: pass `signals=CELL_SIGNALS, confirm=_confirm_cell_gone`; same human_delay guard.
- `_restock_cells(ctx, st)`: signature gains `st`; `cells = (st.get("inventory") or {}).get("unchargedCells", 0)`; replace the `_commit(...)` line with `_await(ctx, "gotr_restock_commit", ACTION_COMMIT_S + _walk_budget(table), "INVENTORY_CHANGED", lambda: _confirm_more_cells(cells), shorten=_after_stop(ACTION_AFTER_STOP_S))` -- compute the budget BEFORE the click into a local. Guard the trailing `human_delay(0.5, 0.9)` with `if not data.get("gotr_outcome_hit")`. Update both callers: `between_rounds_phase` -> `_restock_cells(ctx, st)`, `restock_cells_phase(ctx, st)` -> `_restock_cells(ctx, st)`.
- `_deposit_runes`: `_await(ctx, "gotr_deposit_commit", budget, "INVENTORY_CHANGED", lambda: not _runes_in_inventory(), shorten=_after_stop(ACTION_AFTER_STOP_S))`; guard the `human_delay(1.0, 1.6)`.
- `spend_stones_phase`: before `controller.click_menu_action(...)`: `stones_before = total_stones(st.get("inventory") or {})` and `if ctx.events is not None: data["gotr_click_mark"] = ctx.events.mark()` (this site bypasses `_click_escalating`). Replace `_commit(data, "gotr_powerup_commit", POWERUP_COMMIT_S)` with `_await(ctx, "gotr_powerup_commit", POWERUP_COMMIT_S, CELL_SIGNALS, lambda: _confirm_fewer_stones(stones_before), shorten=_after_stop(ACTION_AFTER_STOP_S))`. Guard the human_delay.
- `_climb_rubble`: `side_before = (p.get("x") or 0) >= POCKET_MIN_X` computed where `p` is read; replace `_commit(data, "gotr_climb_commit", ...)` with

```python
        _await(ctx, "gotr_climb_commit", CLIMB_COMMIT_S + tiles * PORTAL_RUN_S_PER_TILE,
               {"PLAYER_STOPPED": None, "ANIMATION_CHANGED": None},
               lambda: (((controller.get_player() or {}).get("x") or 0) >= POCKET_MIN_X) != side_before)
```

  and guard its trailing `human_delay(0.7, 1.2)`.

- [ ] **Step 4: Test**

Run: `python -m py_compile routines/guardians_of_the_rift.py && python -m pytest tests/test_guardians_of_the_rift.py -q` -- pass.

- [ ] **Step 5: Commit**

```bash
git add routines/guardians_of_the_rift.py tests/test_guardians_of_the_rift.py
git commit -m "gotr: outcome waits for cell/repair/guardian, restock, deposit, Power-up and the rubble climb"
```

---

### Task 9: GOTR conversions B -- craft and mine

**Files:**
- Modify: `ControllerAddon/routines/guardians_of_the_rift.py` (`craft_phase` ~1675; `mine_phase` ~1718; the pocket mine in `portal_trip_phase` ~1622-1631; the pre-round pocket mine in `between_rounds_phase` ~1146-1154)
- Test: `ControllerAddon/tests/test_guardians_of_the_rift.py`

**Interfaces:**
- Produces: `_confirm_crafted(ess_before, frags_before)`, `_confirm_mined(frags_before, mined_before)`, `MINE_SIGNALS`, `CRAFT_SIGNALS`, `_await_mine(ctx, node, st, budget_s=None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_confirm_mined_and_crafted_read_fresh_state(monkeypatch):
    monkeypatch.setattr(gotr, "read_state", lambda: make_state(
        inventory=make_inv(guardianFragments=40, guardianEssence=3), minedFragmentsThisGame=40))
    assert gotr._confirm_mined(30, 30) is True
    assert gotr._confirm_mined(40, 40) is False
    assert gotr._confirm_crafted(0, 45) is True        # essence up / fragments down
    assert gotr._confirm_crafted(3, 40) is False


def test_mine_signals_filter_the_mining_skill_and_count_varbit():
    xp = gotr.MINE_SIGNALS["XP_GAINED"]
    assert xp({"skill": "Mining"}) and not xp({"skill": "Runecraft"})
    vb = gotr.MINE_SIGNALS["VARBIT_CHANGED"]
    assert vb({"varbitId": gotr.VB_MINING_COUNT}) and not vb({"varbitId": 1})


def test_await_mine_stamps_the_delta_clock_on_a_hit(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents(bridge_events.Outcome(True, "XP_GAINED", {"skill": "Mining"}, True, 1.0))
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 2.0)
    st = make_state(inventory=make_inv(guardianFragments=10))
    gotr._await_mine(ctx, {"x": 1, "y": 1}, st)
    assert ctx.state.data["gotr_mine_delta_at"] > 0
    assert ctx.events.calls[0]["budget"] == gotr.MINE_COMMIT_S + 2.0
    assert ctx.events.calls[0]["shorten"] == {"PLAYER_STOPPED": gotr.MINE_AFTER_STOP_S}
```

- [ ] **Step 2: Run to verify failure** -- `python -m pytest tests/test_guardians_of_the_rift.py -q -k "confirm_mined or mine_signals or await_mine"` fails.

- [ ] **Step 3: Implement**

Helpers (after `_confirm_more_cells`):

```python
def _confirm_mined(frags_before, mined_before):
    st2 = read_state() or {}
    inv2 = st2.get("inventory") or {}
    return (inv2.get("guardianFragments", 0) > frags_before
            or st2.get("minedFragmentsThisGame", 0) > mined_before)


def _confirm_crafted(ess_before, frags_before):
    inv2 = _inv_now()
    return (inv2.get("guardianEssence", 0) > ess_before
            or inv2.get("guardianFragments", 0) < frags_before)


MINE_SIGNALS = {"INVENTORY_CHANGED": None,
                "XP_GAINED": lambda p: p.get("skill") == "Mining",
                "VARBIT_CHANGED": lambda p: p.get("varbitId") == VB_MINING_COUNT}
CRAFT_SIGNALS = {"INVENTORY_CHANGED": None, "XP_GAINED": None}


def _await_mine(ctx, node, st, budget_s=None):
    """After a verified Mine click: the first fragment (inventory/XP/mined-count) is the
    outcome; a hit stamps gotr_mine_delta_at so MINE_RECLICK_S leaves the swing alone."""
    inv = st.get("inventory") or {}
    frags, mined = inv.get("guardianFragments", 0), st.get("minedFragmentsThisGame", 0)
    budget = (MINE_COMMIT_S if budget_s is None else budget_s) + _walk_budget(node)
    res = _await(ctx, "gotr_mine_commit", budget, MINE_SIGNALS,
                 lambda: _confirm_mined(frags, mined), shorten=_after_stop(MINE_AFTER_STOP_S))
    if res:
        ctx.state.data["gotr_mine_delta_at"] = time.time()
        ctx.state.data["gotr_mine_stall"] = 0
    return res
```

Call sites (compute budgets/baselines BEFORE the click):

- `mine_phase`: replace `_commit(data, "gotr_mine_commit", MINE_COMMIT_S + _walk_budget(node))` with `_await_mine(ctx, node, st)`; guard the trailing `human_delay(0.6, 1.0)` with `if not data.get("gotr_outcome_hit")`.
- `portal_trip_phase` pocket mine (the `_commit(data, "gotr_mine_commit", ...)` at ~1629): same replacement, same guard on `human_delay(0.6, 1.0)`.
- `between_rounds_phase` pre-round pocket mine (~1153): `_await_mine(ctx, node, st, budget_s=PREROUND_MINE_BUDGET_S)`; the following `human_delay(1.0, 2.0)` becomes `if not data.get("gotr_outcome_hit"): controller.human_delay(1.0, 2.0)`.
- `craft_phase`: before the click `ess, frags = inv.get("guardianEssence", 0), inv.get("guardianFragments", 0)`; `budget = CRAFT_COMMIT_S + _walk_budget(bench)`; replace `_commit(data, "gotr_craft_commit", CRAFT_COMMIT_S + _walk_budget(bench))` with

```python
        if _await(ctx, "gotr_craft_commit", budget, CRAFT_SIGNALS,
                  lambda: _confirm_crafted(ess, frags), shorten=_after_stop(CRAFT_AFTER_STOP_S)):
            data["gotr_craft_delta_at"] = time.time()
            data["gotr_craft_stall"] = 0
```

  and guard `human_delay(1.0, 1.6)`. The `_fill_pouches(..., resume=...)` + `_commit(data, "gotr_craft_commit", CRAFT_COMMIT_S)` path is left as is (the fill has its own confirmation flow).

- [ ] **Step 4: Test** -- `python -m py_compile routines/guardians_of_the_rift.py && python -m pytest tests/test_guardians_of_the_rift.py -q` pass.

- [ ] **Step 5: Commit**

```bash
git add routines/guardians_of_the_rift.py tests/test_guardians_of_the_rift.py
git commit -m "gotr: outcome waits for mining (fragment/XP/count) and the workbench"
```

---

### Task 10: GOTR conversions C -- Enter/exit teleports and the altar imbue

**Files:**
- Modify: `ControllerAddon/routines/guardians_of_the_rift.py` (`_enter_resolved` ~666; `altar_trip_phase` ~1508-1518; `portal_trip_phase` outside branch ~1664-1672 and pocket exit ~1607-1618; `_work_altar` imbue ~1551-1556 and exit ~1562-1586; `between_rounds_phase` pocket exit ~1125-1131; `_leave_side_area` ~2109-2124)
- Test: `ControllerAddon/tests/test_guardians_of_the_rift.py`

**Interfaces:**
- Produces: `_enter(ctx, entity, options, key, label, changed) -> bool` (True when a verified click was issued); `_hud_widget(payload) -> bool`; `_confirm_in_altar()`, `_confirm_in_pocket()`, `_confirm_back_in_arena()`; `TELEPORT_SIGNALS`; `HUD_GROUP = 746`.

- [ ] **Step 1: Write the failing tests**

```python
def test_enter_uses_the_menu_verdict_then_awaits_the_teleport(monkeypatch):
    ctx = _Ctx()
    verdict = bridge_events.Outcome(True, "MENU_CLICKED", {"option": "Enter", "target": "Portal"}, True, 0.1)
    tele = bridge_events.Outcome(True, "WIDGET_LOADED", {"groupId": 746}, True, 2.0)
    outcomes = iter([verdict, tele])

    class _Ev(_FakeEvents):
        def await_outcome(self, *a, **k):
            self.calls.append(k)
            return next(outcomes)
    ctx.events = _Ev(None)
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 1.0)
    assert gotr._enter(ctx, {"x": 1, "y": 1}, ("Enter",), "gotr_enter_commit", "test", lambda: True) is True
    assert ctx.events.calls[0]["reaction"] is False              # the verdict is not an action
    assert ctx.events.calls[1]["budget_s"] == gotr.ENTER_COMMIT_S + 1.0
    assert ctx.state.data["gotr_outcome_hit"] is True


def test_enter_gives_up_when_the_click_became_something_else(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents(bridge_events.Outcome(True, "MENU_CLICKED", {"option": "Walk here", "target": ""}, True, 0.1))
    monkeypatch.setattr(gotr, "_click_escalating", lambda c, e, o: True)
    monkeypatch.setattr(gotr, "_walk_budget", lambda e: 0.0)
    assert gotr._enter(ctx, {"x": 1, "y": 1}, ("Enter",), "gotr_enter_commit", "test", lambda: True) is False
    assert len(ctx.events.calls) == 1


def test_hud_widget_predicate_and_teleport_confirms(monkeypatch):
    assert gotr._hud_widget({"groupId": 746}) and not gotr._hud_widget({"groupId": 162})
    monkeypatch.setattr(gotr, "read_state", lambda: make_state(currentAltar="AIR", inArena=False))
    assert gotr._confirm_in_altar() and not gotr._confirm_back_in_arena()
    monkeypatch.setattr(gotr, "read_state", lambda: make_state(miningArea="WEST_HUGE_REMAINS"))
    assert gotr._confirm_in_pocket()
    monkeypatch.setattr(gotr, "read_state", lambda: make_state())
    assert gotr._confirm_back_in_arena() and not gotr._confirm_in_pocket()
```

- [ ] **Step 2: Run to verify failure** -- `-k "test_enter or hud_widget"` fails.

- [ ] **Step 3: Implement**

Constants and helpers (after `CRAFT_SIGNALS`):

```python
HUD_GROUP = 746                # InterfaceID.GotrHud (0x02ea): the arena HUD interface group


def _hud_widget(payload):
    return payload.get("groupId") == HUD_GROUP


# A teleport shows as: the HUD interface closing/reloading, a spotanim on the player, and the
# interpolation tracker seeing a >2-tile jump (PLAYER_STOPPED). Any of them triggers the HTTP
# confirm; which one actually fires first is on the live-verification list.
TELEPORT_SIGNALS = {"WIDGET_LOADED": _hud_widget, "WIDGET_CLOSED": _hud_widget,
                    "GRAPHIC_CHANGED": None, "PLAYER_STOPPED": None}


def _confirm_in_altar():
    return ((read_state() or {}).get("currentAltar") or "UNKNOWN") != "UNKNOWN"


def _confirm_in_pocket():
    st2 = read_state() or {}
    return st2.get("miningArea") == "WEST_HUGE_REMAINS" or side_area(st2) == "pocket"


def _confirm_back_in_arena():
    st2 = read_state() or {}
    return (bool(st2.get("inArena")) and (st2.get("currentAltar") or "UNKNOWN") == "UNKNOWN"
            and st2.get("miningArea") != "WEST_HUGE_REMAINS")


def _enter(ctx, entity, options, key, label, changed):
    """A verified Enter: click, take the game's own verdict on what the click became
    (MENU_CLICKED -- replaces _enter_resolved's /last-action poll, kept as the fallback when
    the event does not arrive), then wait for the teleport (TELEPORT_SIGNALS, HTTP-confirmed by
    `changed`). True when a verified click was issued (a miss is logged and returns False)."""
    data = ctx.state.data
    before = controller.get_last_action()
    budget = ENTER_COMMIT_S + _walk_budget(entity)
    if not _click_escalating(ctx, entity, options):
        return False
    verdict = bridge_events.await_outcome(ctx.events, "MENU_CLICKED", ENTER_ECHO_TIMEOUT_S,
                                          confirm=None, since=data.get("gotr_click_mark"),
                                          reaction=False)
    if verdict and verdict.payload is not None:
        option = (verdict.payload.get("option") or "").lower()
        if not any(o.lower() in option for o in options):
            debug_log.log("!", f"gotr: {label} click resolved to "
                               f"{verdict.payload.get('option')!r} on {verdict.payload.get('target')!r}")
            data.pop("gotr_click_mark", None)
            return False
    elif _enter_resolved(before, options[0], label) is False:
        data.pop("gotr_click_mark", None)
        return False
    _await(ctx, key, budget, TELEPORT_SIGNALS, changed)
    return True
```

Note: `bridge_events.await_outcome(None, ...)` with `confirm=None` sleeps the whole `ENTER_ECHO_TIMEOUT_S` and returns falsy -- in `--no-events` mode `_enter` therefore falls through to `_enter_resolved`, the old poll. Acceptable for the control arm.

Call sites:

- `altar_trip_phase` (the block from `before = controller.get_last_action()` to `_hold_action(data, "altar_trip", budget)`):

```python
    debug_log.log("gotr", f"entering the {altar} portal")
    if _enter(ctx, portal, (PORTAL_GUARDIAN_ACTION,), "gotr_enter_commit", f"{altar} Enter",
              _confirm_in_altar) and not data.get("gotr_outcome_hit"):
        _hold_action(data, "altar_trip", ACTION_COMMIT_S)
    controller.mark_busy(3.0)
    if not data.get("gotr_outcome_hit"):
        controller.human_delay(0.7, 1.2)
```

- `portal_trip_phase` outside branch: same shape with `_confirm_in_pocket`, label `"huge-portal Enter"`, hold `"portal_trip"`.
- `portal_trip_phase` pocket exit (`if _click_escalating(ctx, exit_portal, EXIT_PORTAL_ACTIONS): _commit(...)`): `_enter(ctx, exit_portal, EXIT_PORTAL_ACTIONS, "gotr_exit_commit", "pocket exit", _confirm_back_in_arena)`; guard the `human_delay(0.7, 1.1)`.
- `_work_altar` exit: `_enter(ctx, exit_portal, EXIT_PORTAL_ACTIONS, "gotr_exit_commit", "altar exit", _confirm_back_in_arena)`; guard `human_delay(0.7, 1.1)`.
- `between_rounds_phase` pocket exit and `_leave_side_area`: the same `_enter(... "gotr_exit_commit" ... _confirm_back_in_arena)` in place of `_click_escalating` + `_commit`; guard the trailing delays.
- `_work_altar` imbue: replace `_await_xp("runecraftXp", xp_before, IMBUE_CONFIRM_S)` with

```python
        if xp_before is None:
            _await_xp("runecraftXp", xp_before, IMBUE_CONFIRM_S)   # un-rebuilt client: old path
        else:
            _await(ctx, "gotr_imbue_commit", IMBUE_CONFIRM_S,
                   {"XP_GAINED": lambda p: p.get("skill") == "Runecraft"},
                   lambda: ((controller.get_player() or {}).get("runecraftXp") or 0) > xp_before)
```

`_enter_resolved` stays (fallback). Delete nothing else.

- [ ] **Step 4: Test** -- `python -m py_compile routines/guardians_of_the_rift.py && python -m pytest tests/test_guardians_of_the_rift.py -q` pass.

- [ ] **Step 5: Commit**

```bash
git add routines/guardians_of_the_rift.py tests/test_guardians_of_the_rift.py
git commit -m "gotr: Enter/exit teleports confirmed on MENU_CLICKED + HUD/spotanim/jump, imbue on the XP event"
```

---

### Task 11: GOTR conversions D -- walks confirm on motion

**Files:**
- Modify: `ControllerAddon/routines/guardians_of_the_rift.py` (`_direct_walk` ~793 and the `human_delay` that follows each `_direct_walk(` call: `_climb_rubble` ~755, `between_rounds_phase` ~1132, `altar_trip_phase` ~1502, `_work_altar` ~1549 and ~1580, `_leave_side_area` ~2120)
- Test: `ControllerAddon/tests/test_guardians_of_the_rift.py`

**Interfaces:**
- Produces: `_await_moving(ctx) -> bool`; `_direct_walk` calls it after a successful click.

- [ ] **Step 1: Write the failing tests**

```python
def test_await_moving_returns_at_once_when_already_moving(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents(None)
    monkeypatch.setattr(controller, "get_player", lambda: {"moving": True})
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    assert gotr._await_moving(ctx) is True
    assert ctx.events.calls == []


def test_await_moving_waits_for_player_moving_otherwise(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents(bridge_events.Outcome(True, "PLAYER_MOVING", {}, True, 0.2))
    monkeypatch.setattr(controller, "get_player", lambda: {"moving": False})
    assert gotr._await_moving(ctx) is True
    assert ctx.events.calls[0]["signals"] == "PLAYER_MOVING"
    assert ctx.events.calls[0]["budget"] == gotr.WALK_START_BUDGET_S


def test_direct_walk_awaits_motion_after_its_click(monkeypatch):
    ctx = _Ctx()
    gotr._walk_memo.update({"target": None, "player": None, "stuck": 0})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3600, "y": 9500})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 500, "screenY": 400})
    monkeypatch.setattr(controller, "is_point_blocked_by_ui", lambda x, y: None)
    monkeypatch.setattr(travel, "walk_to_exact_tile", lambda *a, **k: True)
    waited = []
    monkeypatch.setattr(gotr, "_await_moving", lambda c: waited.append(1) or True)
    assert gotr._direct_walk(ctx, 3610, 9500) is True
    assert waited == [1]
```

(`travel` is already imported in that test module for the existing `_direct_walk` tests.)

- [ ] **Step 2: Run to verify failure** -- `-k "await_moving or awaits_motion"` fails.

- [ ] **Step 3: Implement**

After `_after_stop`:

```python
def _await_moving(ctx):
    """After a walk click: hand control back the moment the player is provably moving
    (PLAYER_MOVING, or /player.moving already true), else after WALK_START_BUDGET_S. Replaces
    the flat 0.5-0.9s delay every walk site paid; the reaction pad keeps it human."""
    if (controller.get_player() or {}).get("moving"):
        controller.human_delay(*settings.REACTION_DELAY_S)
        return True
    return bool(bridge_events.await_outcome(
        ctx.events, "PLAYER_MOVING", WALK_START_BUDGET_S,
        confirm=lambda: bool((controller.get_player() or {}).get("moving"))))
```

(`import settings` at the top of the routine if it is not already there.)

`_direct_walk`: rename the existing function to `_direct_walk_click` and add

```python
def _direct_walk(ctx, x, y):
    """_direct_walk_click, then wait for the player to actually be moving."""
    if not _direct_walk_click(ctx, x, y):
        return False
    _await_moving(ctx)
    return True
```

Then remove the `controller.human_delay(...)` line that IMMEDIATELY follows each `_direct_walk(` call listed in Files (six sites; `grep -n "_direct_walk(" routines/guardians_of_the_rift.py` and inspect the next line). Leave the delays that follow `_walk_toward(` calls alone (`_walk_toward` may take the `travel.walk_to` branch, which already waits).

- [ ] **Step 4: Test** -- `python -m py_compile routines/guardians_of_the_rift.py && python -m pytest tests/ -q` -- the whole suite.

- [ ] **Step 5: Commit**

```bash
git add routines/guardians_of_the_rift.py tests/test_guardians_of_the_rift.py
git commit -m "gotr: walks return on PLAYER_MOVING instead of a flat delay"
```

---

### Task 12: Docs -- CLAUDE.md, spec correction

**Files:**
- Modify: `CLAUDE.md` (Architecture UDP list; the routine contract's `ctx.events` bullet; "Current state of routines/guardians_of_the_rift.py"), `docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md` (Section 3 table).

- [ ] **Step 1: CLAUDE.md**

Architecture block, UDP line: add `VARBIT_CHANGED (subscribed ids only), WIDGET_LOADED/CLOSED, MENU_CLICKED, GRAPHIC_CHANGED, HITSPLAT, PLAYER_MOVING/STOPPED (per-frame)`.

Routine contract, after the `ctx.events` bullet's "Useful methods" sentence, add:

```
  **Added 2026-09-04 (event-driven outcomes,
  `docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md`):** `mark()` + `since=`
  replace the clear-before-the-action rule; `await_outcome(signals, budget_s, confirm=,
  since=, shorten=)` is THE way to wait for a click's outcome -- a listed UDP signal, confirmed
  by one HTTP read, with the HTTP read deciding alone at the budget, padded by a human
  reaction draw (`settings.REACTION_DELAY_S`). The module-level
  `bridge_events.await_outcome(ctx.events, ...)` handles `None` by polling. `controller.py
  --no-events` is the A/B control. Routines may declare `VARBIT_SUBSCRIPTIONS`/
  `VARP_SUBSCRIPTIONS`; the engine posts them on load and after every login. The engine loop
  wakes on any non-TICK event (`EventStream.wait_any`) and logs its own iteration time
  (`LoopStats`, `[engine] loop: ...` every 60s) -- read that line before assuming the loop's
  cost. `log_gaps.py <log>` shows where a run's time went.
```

GOTR section: prepend a bullet:

```
- **Outcome-driven since 2026-09-04 (not yet run live in this form).** Every `_commit` window
  became `_await` (signals + HTTP confirm, chained inside one tick up to `CHAIN_MAX_ACTIONS`/
  `CHAIN_MAX_S`); Enter clicks take the game's verdict from `MENU_CLICKED`; walks return on
  `PLAYER_MOVING`. Baseline to beat: run 76, 48% of the round in gaps over 4s, 5732 earned RC
  XP/round (run 75). First live session's worklist is the spec's live-verification list.
```

- [ ] **Step 2: Spec correction**

In Section 3's table, change the place_cell/create_guardian row to `INVENTORY_CHANGED, energy varbits 13685/13686 | cell tier is NONE` and the repair_barrier row to `INVENTORY_CHANGED (fragments spent) | no repairable tile or fragments decreased`, with a sentence after the table: "Cell barriers are GroundObjects, not varbits (m8aq `GuardiansOfTheRift.readBarriers`), so their signal is the cell/fragments leaving the inventory."

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md
git commit -m "docs: event-driven outcomes -- routine contract, engine loop, GOTR state; spec barrier correction"
```

---

### Task 13: Live verification and A/B measurement (manual, with the user)

**Files:** none new; findings go into `CLAUDE.md`'s GOTR section and the spec's live-verification list, and `settings.py`/GOTR constants if a value changes.

- [ ] **Step 1: Rebuild and restart the client** (user): `./gradlew.bat run`. Confirm `GET /events/subscriptions` answers `{"varbits":[],"varps":[]}` and `GET /player` has `moving`.

- [ ] **Step 2: Watch the new events arrive.** With the client logged in and the MCP `osrs-debug` server connected, walk a few tiles by hand and call `poll_events`; expect one `PLAYER_MOVING` and one `PLAYER_STOPPED` per walk, none mid-run. Click an object; expect `MENU_CLICKED` with the right option. Open the bank; expect `WIDGET_LOADED`.

- [ ] **Step 3: The HUD question.** Run GOTR for one altar trip and grep the debug log / `poll_events` output for `WIDGET_CLOSED`/`WIDGET_LOADED` with `groupId 746` around the Enter. If the pair fires, replace `ACTIVE_SETTLE_S`'s timer condition in `_tick_once`'s between-round branch with "no HUD `WIDGET_LOADED` since the last `WIDGET_CLOSED`" (keep the timer as a fallback) in a follow-up commit; if not, record that in CLAUDE.md and leave the timer.

- [ ] **Step 4: A/B, same world (409), one full round each:**

```
python -u controller.py routines/guardians_of_the_rift.py --no-events *> logs/gotr_ab_control.log
python -u controller.py routines/guardians_of_the_rift.py *> logs/gotr_ab_events.log
python log_gaps.py logs/gotr_ab_control.log
python log_gaps.py logs/gotr_ab_events.log
```

Run `recorder.py gotr-ab-control` / `gotr-ab-events` alongside for the XP-per-round parse. Also read the `[engine] loop:` summary lines from both logs and the `UDP event queue full` count on the client's stderr.

- [ ] **Step 5: Record.** Add the measured numbers (over-4s share, cycle times, XP/round, loop mean/p90, reaction fit, queue drops) to CLAUDE.md's GOTR section, and tick off the spec's live-verification list with what was found. Commit as `gotr: first live session on the event-driven path -- measurements`.

---

## Self-review

**Spec coverage.** Section 1: events (T3), subscription filter + endpoints (T1, T3), motion tracker + `/player.moving` (T2, T3), Java tests (T1, T2). Section 2: `await_outcome`/`mark`/`Outcome`/padding/None fallback (T4), engine wake + loop instrumentation (T5), subscription plumbing (T5), `--no-events` (T5), tests (T4, T5). Section 3: per-rung outcomes (T8-T10), walks (T11), chaining (T7), settling decision (T13 step 3, deliberately after the live check as the spec says), measurement tools and A/B (T6, T13), live-verification list (T13), GOTR tests (T7-T11). Risks: padding fit (T6 step 4), drops (T13), two-copies rule (`bridge_events` only), re-post after login (T5).

**Placeholder scan.** None left; the one deliberately deferred change (HUD-based settling) is stated as a decision taken in T13 with both branches spelled out.

**Type consistency.** `await_outcome(signals, budget_s, confirm=None, since=None, reaction=True, shorten=None, now_fn, sleep_fn)` is used with those names in T4, T7 (`_FakeEvents`), T10 (`budget_s`, `reaction` as kwargs in the test's captured `k`). `_await(ctx, key, budget_s, signals, confirm, reaction=True, shorten=None)` matches T7-T10. `_approach_or_click`'s new kwargs match T8's callers. `_restock_cells(ctx, st)` updated at both callers. `Outcome(hit, signal, payload, confirmed, latency_s)` positional order matches every test construction. Java: `EventSubscriptions.replace/matches/varbits/varps/parseIds`, `PlayerMotionTracker.observe/isMoving/Transition` used identically in T2 and T3.
