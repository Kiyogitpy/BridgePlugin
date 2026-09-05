# Gauntlet Instance Plumbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give routines a second, instance-space coordinate system on the bridge plus an offline instance router, an NPC overhead-prayer field, and NPC-spawn / projectile UDP events, so a routine can walk, click, and react inside an instanced area such as the Gauntlet.

**Architecture:** Every provider that reports a position adds `instX`/`instY` (the uncorrected base+scene coordinate, unique per scene tile) beside the existing template-corrected `x`/`y`; the endpoints that take a tile accept `inst=true` and resolve it by arithmetic against the world view's base, never through `LocalPoint.fromWorld`. `/collision?all=true` ships the whole plane as one compact grid, and `ControllerAddon/instance_nav.py` runs BFS over it and drives `travel._hop_to` in instance space. Two new UDP event families ride the existing per-tick spawn budget.

**Tech Stack:** Java 11 RuneLite plugin (runelite-api `latest.release`, currently 1.12.38; JUnit 4 + Mockito 5 tests via `./gradlew.bat test`), Python 3 controller (`pytest` from `ControllerAddon/`).

**Spec:** `docs/superpowers/specs/2026-09-05-gauntlet-instance-plumbing-design.md`

## Global Constraints

- Nothing existing is renamed or changes meaning: `x`/`y`/`plane` keep their template-corrected semantics; `inst*` fields and `inst=true` are additive.
- Instance-space input is resolved by `sceneX = instX - wv.getBaseX()`, `sceneY = instY - wv.getBaseY()`, refused outside `0..103`. `LocalPoint.fromWorld` is never used for it.
- A `@Subscribe` handler MUST be named `on<EventSimpleName>` (RuneLite enforces it; one wrong name kills the whole plugin's registration -- CLAUDE.md).
- New UDP spawn-type events go through `spawnEventBudgetAvailable()` (40 per tick, shared) and null-check `udpSender`.
- Java tests never touch a live client; Python tests stay hermetic (the `no_live_bridge` autouse pattern in `tests/test_travel.py`).
- Any Java change needs `./gradlew.bat run` (client restart) before the bridge reflects it. Python changes need only a routine restart.
- Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Run `python -m pytest tests/test_instance_nav.py tests/test_travel.py -q` (from `ControllerAddon/`) and `./gradlew.bat test` before each commit that touches the respective side. The full Python suite takes ~7 minutes (chinchompa real sleeps); run it once at the end of Task 12.

---

## File map

| File | Responsibility |
|---|---|
| `src/main/java/com/example/util/InstanceCoords.java` (new) | The one place instance-space arithmetic lives: `instancePoint`, `toScene`, `fromInstance`, `sceneIndex`. |
| `src/test/java/com/example/util/InstanceCoordsTest.java` (new) | Pins the arithmetic and bounds. |
| `src/main/java/com/example/providers/PlayerProvider.java` | `/player` gains `inInstance`, `baseX`, `baseY`, `instX`, `instY`. |
| `src/main/java/com/example/providers/NPCProvider.java` | `buildNpcData` gains `instX`, `instY`, `overheadPrayer`, `overheadPrayerId`. |
| `src/main/java/com/example/providers/ObjectProvider.java` | `buildObjectData` gains `instX`/`instY`; `/object/at?inst=true`. |
| `src/main/java/com/example/providers/GroundItemProvider.java` | Items gain `instX`/`instY`; `/ground-item/at?inst=true`. |
| `src/main/java/com/example/providers/CollisionProvider.java` | Tiles gain `instX`/`instY`; `?inst=true` centre; `?all=true` grid via `CollisionGrid`. |
| `src/main/java/com/example/providers/CollisionGrid.java` (new) | Row-major encoder for a plane's flags, pure and testable. |
| `src/test/java/com/example/providers/CollisionGridTest.java` (new) | Pins the encoding. |
| `src/main/java/com/example/providers/CombatProvider.java` | `/ground/walk` and `/minimap/walk` accept `inst=true`. |
| `src/main/java/com/example/controller/ControllerPlugin.java` | `onNpcSpawned`, `onNpcDespawned`, `onProjectileMoved`. |
| `src/main/java/com/example/util/ProjectileSeen.java` (new) | First-sighting dedupe for projectiles. |
| `src/test/java/com/example/util/ProjectileSeenTest.java` (new) | Pins dedupe semantics. |
| `ControllerAddon/controller.py` | `inst=` on the tile-taking wrappers; `get_collision_grid`. |
| `ControllerAddon/travel.py` | `_hop_to(..., inst=False)` pass-through. |
| `ControllerAddon/instance_nav.py` (new) | `InstanceMap`, `find_path`, `walk_to`, `player_inst`. |
| `ControllerAddon/tests/test_instance_nav.py` (new) | BFS and walk tests on synthetic grids. |
| `ControllerAddon/gauntlet_ids.py` (new) | Gauntlet id tables with sources. |
| `ControllerAddon/tests/test_gauntlet_ids.py` (new) | Disjointness and pinned values. |
| `CLAUDE.md` | Instancing gotcha gains its resolution; events list gains the three new types. |

---

### Task 1: `InstanceCoords` helper and `/player` instance fields

**Files:**
- Create: `src/main/java/com/example/util/InstanceCoords.java`
- Test: `src/test/java/com/example/util/InstanceCoordsTest.java`
- Modify: `src/main/java/com/example/providers/PlayerProvider.java` (the block that ends with `data.put("regionId", wp.getRegionID());`, ~line 120)

**Interfaces:**
- Produces: `InstanceCoords.SCENE_SIZE = 104`; `static int toScene(int base, int coord)` returns `coord - base` or `-1` when outside `0..103`; `static WorldPoint instancePoint(Client client, LocalPoint local, int plane)` returns `WorldPoint.fromLocal(client, local.getX(), local.getY(), plane)`; `static LocalPoint fromInstance(WorldView wv, int instX, int instY)` returns `LocalPoint.fromScene(sx, sy, wv)` or `null`; `static int sceneIndex(int sceneX, int sceneY)` returns `sceneY * SCENE_SIZE + sceneX` (row-major by y, the grid encoding Task 5 and Task 9 share).
- Produces on `/player`: `inInstance` (bool), `baseX`, `baseY`, `instX`, `instY` (ints).

- [ ] **Step 1: Write the failing test**

```java
package com.example.util;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class InstanceCoordsTest
{
	@Test
	public void toSceneSubtractsTheBase()
	{
		assertEquals(0, InstanceCoords.toScene(13056, 13056));
		assertEquals(103, InstanceCoords.toScene(13056, 13159));
		assertEquals(37, InstanceCoords.toScene(3200, 3237));
	}

	@Test
	public void toSceneRefusesOutsideTheScene()
	{
		assertEquals(-1, InstanceCoords.toScene(13056, 13055));
		assertEquals(-1, InstanceCoords.toScene(13056, 13160));
	}

	@Test
	public void sceneIndexIsRowMajorByY()
	{
		assertEquals(0, InstanceCoords.sceneIndex(0, 0));
		assertEquals(5, InstanceCoords.sceneIndex(5, 0));
		assertEquals(InstanceCoords.SCENE_SIZE, InstanceCoords.sceneIndex(0, 1));
		assertEquals(InstanceCoords.SCENE_SIZE * InstanceCoords.SCENE_SIZE - 1,
			InstanceCoords.sceneIndex(103, 103));
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat test --tests "com.example.util.InstanceCoordsTest" --console=plain -q`
Expected: compile error, `cannot find symbol: class InstanceCoords`.

- [ ] **Step 3: Confirm the two API signatures the helper depends on**

Run (Git Bash):
```bash
JAR=$(find ~/.gradle/caches -name "runelite-api-1.12.3*.jar" | grep -v sources | tail -1)
javap -cp "$JAR" net.runelite.api.coords.LocalPoint | grep fromScene
javap -cp "$JAR" net.runelite.api.coords.WorldPoint | grep "fromLocal("
javap -cp "$JAR" net.runelite.api.WorldView | grep -i "getBase\|isInstance"
```
Expected: a `fromScene(int, int, net.runelite.api.WorldView)`, a `fromLocal(net.runelite.api.Client, int, int, int)`, and `getBaseX()/getBaseY()/isInstance()`. If `fromScene(int,int,WorldView)` is absent, use `LocalPoint.fromScene(int, int)` (deprecated, top-level view only) and note it in the class comment.

- [ ] **Step 4: Write the helper**

```java
package com.example.util;

import net.runelite.api.Client;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;

/**
 * Instance-space coordinates: the client's own scene base plus the scene offset, exactly what
 * {@link WorldPoint#fromLocal} returns and what {@link LocalPoint#fromWorld} can invert. Unique
 * per scene tile, stable while the scene is loaded, and equal to the ordinary world coordinate
 * outside an instance.
 * <p>
 * Why a second coordinate space (2026-09-05, Gauntlet sub-project 1): every provider reports
 * {@code WorldPoint.fromLocalInstance} -- the instance TEMPLATE coordinate, needed so routines'
 * hardcoded tiles match inside Pest Control etc. -- and that direction cannot be inverted, which
 * is CLAUDE.md's "forward world-coordinate conversion is broken in instances" gotcha. Template
 * coordinates are also not unique (an instance may place the same template chunk twice). So
 * the endpoints that take a tile accept instance space too, resolved here by ARITHMETIC against
 * the world view's base -- never via fromWorld -- and every position-bearing provider reports
 * {@code instX}/{@code instY} beside {@code x}/{@code y}.
 */
public final class InstanceCoords
{
	public static final int SCENE_SIZE = 104;

	private InstanceCoords()
	{
	}

	/** Scene offset of an instance-space coordinate, or -1 when it falls outside the scene. */
	public static int toScene(int base, int coord)
	{
		int scene = coord - base;
		return scene >= 0 && scene < SCENE_SIZE ? scene : -1;
	}

	/** Row-major-by-y index into a {@code SCENE_SIZE * SCENE_SIZE} flat grid. */
	public static int sceneIndex(int sceneX, int sceneY)
	{
		return sceneY * SCENE_SIZE + sceneX;
	}

	/** The uncorrected (base + scene) world point of a local point. */
	public static WorldPoint instancePoint(Client client, LocalPoint local, int plane)
	{
		return WorldPoint.fromLocal(client, local.getX(), local.getY(), plane);
	}

	/** Local point for an instance-space tile in this world view, or null when off-scene. */
	public static LocalPoint fromInstance(WorldView wv, int instX, int instY)
	{
		if (wv == null)
		{
			return null;
		}
		int sx = toScene(wv.getBaseX(), instX);
		int sy = toScene(wv.getBaseY(), instY);
		if (sx < 0 || sy < 0)
		{
			return null;
		}
		return LocalPoint.fromScene(sx, sy, wv);
	}
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./gradlew.bat test --tests "com.example.util.InstanceCoordsTest" --console=plain -q`
Expected: passes (exit 0, no output besides Notes).

- [ ] **Step 6: Add the `/player` fields**

In `PlayerProvider.java`, directly after `data.put("regionId", wp.getRegionID());`:

```java
		// Instance-space position (2026-09-05, see InstanceCoords): the coordinate the walk
		// endpoints accept with inst=true. Equal to x/y outside an instance.
		WorldView topLevel = client.getTopLevelWorldView();
		boolean inInstance = topLevel != null && topLevel.isInstance();
		data.put("inInstance", inInstance);
		if (topLevel != null)
		{
			data.put("baseX", topLevel.getBaseX());
			data.put("baseY", topLevel.getBaseY());
		}
		if (local != null && local.getWorldView() <= WorldView.TOPLEVEL)
		{
			WorldPoint inst = InstanceCoords.instancePoint(client, local, wp.getPlane());
			data.put("instX", inst.getX());
			data.put("instY", inst.getY());
		}
		else
		{
			data.put("instX", wp.getX());
			data.put("instY", wp.getY());
		}
```

Add `import com.example.util.InstanceCoords;` (the file already imports `WorldView`, `WorldPoint`, `LocalPoint`; add any that are missing).

- [ ] **Step 7: Compile and run the Java suite**

Run: `./gradlew.bat test --console=plain -q`
Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/main/java/com/example/util/InstanceCoords.java src/test/java/com/example/util/InstanceCoordsTest.java src/main/java/com/example/providers/PlayerProvider.java
git commit -m "bridge: InstanceCoords helper; /player reports inInstance, base, instX/instY

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `instX`/`instY` on NPCs, objects, ground items, collision tiles

**Files:**
- Modify: `src/main/java/com/example/providers/NPCProvider.java` (`buildNpcData`, ~line 218-262)
- Modify: `src/main/java/com/example/providers/ObjectProvider.java` (`buildObjectData`, the `data.put("x", wp.getX());` block ~line 641)
- Modify: `src/main/java/com/example/providers/GroundItemProvider.java` (the `data.put("x", wp.getX());` block ~line 153)
- Modify: `src/main/java/com/example/providers/CollisionProvider.java` (per-tile output loop, ~line 160-172)

**Interfaces:**
- Consumes: `InstanceCoords.instancePoint(Client, LocalPoint, int)` from Task 1.
- Produces: `instX`/`instY` ints on every element of `/npcs`, `/npc/nearest`, `/npc/by-index`, `/objects`, `/object/at`, `/ground-items`, `/ground-item/at`, and on every tile of `/collision`.

There is no unit test for these providers (they need a live `Client`); the guard is compilation plus the live checklist in Task 12. Keep each edit to the two `put` lines plus the point computation.

- [ ] **Step 1: NPCProvider**

In `buildNpcData`, after `data.put("plane", wp.getPlane());`:

```java
		// Instance-space twin of x/y (2026-09-05, InstanceCoords). Equal outside an instance.
		WorldPoint inst = npcLocal != null
			? InstanceCoords.instancePoint(client, npcLocal, plane)
			: wp;
		data.put("instX", inst.getX());
		data.put("instY", inst.getY());
```

Add `import com.example.util.InstanceCoords;`.

- [ ] **Step 2: ObjectProvider**

In `buildObjectData`, after `data.put("plane", wp.getPlane());` (the block at ~line 641; `obj.getLocalLocation()` is available as the object's local point -- reuse the local variable that block already has, or call it again):

```java
		LocalPoint objLocal = obj.getLocalLocation();
		WorldPoint inst = objLocal != null
			? InstanceCoords.instancePoint(client, objLocal, wp.getPlane())
			: wp;
		data.put("instX", inst.getX());
		data.put("instY", inst.getY());
```

Add the import.

- [ ] **Step 3: GroundItemProvider**

After `data.put("plane", wp.getPlane());` in the item loop (`tileLocal` is already in scope there):

```java
					WorldPoint inst = tileLocal != null
						? InstanceCoords.instancePoint(client, tileLocal, wp.getPlane())
						: wp;
					data.put("instX", inst.getX());
					data.put("instY", inst.getY());
```

Add the import.

- [ ] **Step 4: CollisionProvider per-tile output**

In the output loop, after `tile.put("flags", flags[sx][sy]);`:

```java
				WorldPoint inst = InstanceCoords.instancePoint(client, t.getLocalLocation(), plane);
				tile.put("instX", inst.getX());
				tile.put("instY", inst.getY());
```

Add the import.

- [ ] **Step 5: Compile and run the Java suite**

Run: `./gradlew.bat test --console=plain -q`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/example/providers/NPCProvider.java src/main/java/com/example/providers/ObjectProvider.java src/main/java/com/example/providers/GroundItemProvider.java src/main/java/com/example/providers/CollisionProvider.java
git commit -m "bridge: instX/instY on npcs, objects, ground items and collision tiles

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: NPC overhead prayer

**Files:**
- Modify: `src/main/java/com/example/providers/NPCProvider.java` (`buildNpcData`)

**Interfaces:**
- Produces: `overheadPrayer` (string: a `HeadIcon` name such as `RANGED`, `MAGIC`, `MELEE`, or `null`), `overheadPrayerId` (int, the raw first element of `getOverheadSpriteIds()`, or `-1`).

- [ ] **Step 1: Confirm the API**

Run: `javap -cp "$JAR" net.runelite.api.Actor | grep -i overheadsprite` (with `JAR` from Task 1 step 3).
Expected: `public abstract short[] getOverheadSpriteIds();`.

- [ ] **Step 2: Add the fields**

In `buildNpcData`, after the `instX/instY` block from Task 2:

```java
		// Overhead protection prayer (2026-09-05, Gauntlet sub-project 1). The Hunllef cycles
		// its protection prayer and a hit with the protected style does nothing, so the fight
		// routine needs this. ZombieLite reads the same short[] by reflection into the injected
		// client and indexes HeadIcon.values() with element 0 (NpcsHook.getOldHeadIcon); the
		// public getOverheadSpriteIds() has exposed it since 1.12.x. Whether the element is a
		// HeadIcon ORDINAL or a sprite id is settled live against the Hunllef -- the raw value
		// is reported alongside so a mismatch is diagnosable instead of reading as "no prayer".
		short[] overhead = npc.getOverheadSpriteIds();
		int overheadId = overhead != null && overhead.length > 0 ? overhead[0] : -1;
		data.put("overheadPrayerId", overheadId);
		HeadIcon[] icons = HeadIcon.values();
		data.put("overheadPrayer", overheadId >= 0 && overheadId < icons.length
			? icons[overheadId].name()
			: null);
```

Add `import net.runelite.api.HeadIcon;` (the file imports `net.runelite.api.*` already if you see `import net.runelite.api.*;`; otherwise add the single import).

- [ ] **Step 3: Compile and run the Java suite**

Run: `./gradlew.bat test --console=plain -q`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/providers/NPCProvider.java
git commit -m "bridge: overheadPrayer / overheadPrayerId on npc data

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `inst=true` on the tile-taking endpoints

**Files:**
- Modify: `src/main/java/com/example/providers/CombatProvider.java` (`getMinimapWalkPoint` ~line 192, `getGroundWalkPoint` ~line 234)
- Modify: `src/main/java/com/example/providers/ObjectProvider.java` (`getObjectAt` ~line 62)
- Modify: `src/main/java/com/example/providers/GroundItemProvider.java` (`getGroundItemAt` ~line 51)
- Modify: `src/main/java/com/example/providers/CollisionProvider.java` (`getCollision` centre resolution ~line 84-120)

**Interfaces:**
- Consumes: `InstanceCoords.fromInstance(WorldView, int, int)`, `InstanceCoords.toScene(int, int)` from Task 1.
- Produces: query parameter `inst=true` on `/ground/walk`, `/minimap/walk`, `/object/at`, `/ground-item/at`, `/collision`. When set, `x`/`y` are instance-space.

- [ ] **Step 1: A shared parse helper in `InstanceCoords`**

Add to `InstanceCoords.java`:

```java
	/** True for inst=true / inst=1 (case-insensitive); false when absent or anything else. */
	public static boolean wantsInstance(java.util.Map<String, String> query)
	{
		String v = query.get("inst");
		return v != null && ("true".equalsIgnoreCase(v) || "1".equals(v));
	}
```

And a test in `InstanceCoordsTest`:

```java
	@Test
	public void wantsInstanceReadsTheFlag()
	{
		java.util.Map<String, String> q = new java.util.HashMap<>();
		assertEquals(false, InstanceCoords.wantsInstance(q));
		q.put("inst", "true");
		assertEquals(true, InstanceCoords.wantsInstance(q));
		q.put("inst", "1");
		assertEquals(true, InstanceCoords.wantsInstance(q));
		q.put("inst", "no");
		assertEquals(false, InstanceCoords.wantsInstance(q));
	}
```

Run: `./gradlew.bat test --tests "com.example.util.InstanceCoordsTest" --console=plain -q` -- expected: fails to compile (`wantsInstance` missing) before the helper is added, passes after.

- [ ] **Step 2: Walk points in `CombatProvider`**

In both `getMinimapWalkPoint` and `getGroundWalkPoint`, replace

```java
			WorldPoint wp = new WorldPoint(x, y, plane);
			LocalPoint lp = LocalPoint.fromWorld(client, wp);
```

with

```java
			// inst=true (2026-09-05): x/y are instance-space, resolved by arithmetic against the
			// scene base -- the one direction that works inside an instance (see InstanceCoords).
			LocalPoint lp = InstanceCoords.wantsInstance(query)
				? InstanceCoords.fromInstance(client.getTopLevelWorldView(), x, y)
				: LocalPoint.fromWorld(client, new WorldPoint(x, y, plane));
```

Add `import com.example.util.InstanceCoords;`.

- [ ] **Step 3: `/object/at` and `/ground-item/at`**

In `ObjectProvider.getObjectAt`, the match loop compares `x`/`y`; make the field names depend on the flag:

```java
		String kx = InstanceCoords.wantsInstance(query) ? "instX" : "x";
		String ky = InstanceCoords.wantsInstance(query) ? "instY" : "y";
		Integer radius = parseIntParam(query.get("radius"));
		List<Map<String, Object>> objects = computeObjects(
			radius != null ? radius : OBJECT_AT_DEFAULT_RADIUS,
			InstanceCoords.wantsInstance(query) ? null : new Target(x, y, plane, id));
		for (Map<String, Object> obj : objects)
		{
			if (id.equals(obj.get("id")) && x.equals(obj.get(kx)) && y.equals(obj.get(ky))
				&& plane.equals(obj.get("plane")))
```

(`Target` matches on template coordinates, so it is skipped for the instance case; the id-first rejection in `matchesTarget` is what the pre-filter buys, so also extend `Target` with a `boolean inst` and compare `InstanceCoords.instancePoint(...)` in `matchesTarget` when set -- do this if `/object/at?inst=true` measures slower than 100ms in the live checklist, otherwise leave the simpler null pre-filter.)

Same two-line change in `GroundItemProvider.getGroundItemAt`:

```java
		String kx = InstanceCoords.wantsInstance(query) ? "instX" : "x";
		String ky = InstanceCoords.wantsInstance(query) ? "instY" : "y";
		for (Map<String, Object> item : computeGroundItems())
		{
			if (id.equals(item.get("id")) && x.equals(item.get(kx)) && y.equals(item.get(ky))
				&& plane.equals(item.get("plane")))
```

Add the import to both.

- [ ] **Step 4: `/collision` centre in instance space**

In `CollisionProvider.getCollision`, replace the `if (wantX != null && wantY != null)` scene scan's opening with an arithmetic branch first:

```java
		if (wantX != null && wantY != null && InstanceCoords.wantsInstance(query))
		{
			centreSceneX = InstanceCoords.toScene(view.getBaseX(), wantX);
			centreSceneY = InstanceCoords.toScene(view.getBaseY(), wantY);
		}
		else if (wantX != null && wantY != null)
		{
			... existing scan unchanged ...
		}
		else
		{
			... existing player-centred branch unchanged ...
		}
```

(`toScene` returns -1 off-scene, which the existing `if (centreSceneX < 0)` error path already handles.)

- [ ] **Step 5: Compile and run the Java suite**

Run: `./gradlew.bat test --console=plain -q`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/example/util/InstanceCoords.java src/test/java/com/example/util/InstanceCoordsTest.java src/main/java/com/example/providers/CombatProvider.java src/main/java/com/example/providers/ObjectProvider.java src/main/java/com/example/providers/GroundItemProvider.java src/main/java/com/example/providers/CollisionProvider.java
git commit -m "bridge: inst=true on /ground/walk, /minimap/walk, /object/at, /ground-item/at, /collision

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `/collision?all=true` compact grid

**Files:**
- Create: `src/main/java/com/example/providers/CollisionGrid.java`
- Test: `src/test/java/com/example/providers/CollisionGridTest.java`
- Modify: `src/main/java/com/example/providers/CollisionProvider.java` (`getCollision`, before the per-tile branch)

**Interfaces:**
- Produces: `static List<Integer> CollisionGrid.encode(int[][] flags)` -- flat, row-major by y, `InstanceCoords.sceneIndex(sx, sy)` addressing, missing rows/cells as `0`, length always `SCENE_SIZE * SCENE_SIZE`.
- Produces on the wire: `GET /collision?all=true[&plane=N]` -> `{"baseX", "baseY", "plane", "size": 104, "flags": [...]}`; `{"error": ...}` with an empty `flags` when the client cannot answer.

- [ ] **Step 1: Write the failing test**

```java
package com.example.providers;

import com.example.util.InstanceCoords;
import org.junit.Test;

import java.util.List;

import static org.junit.Assert.assertEquals;

public class CollisionGridTest
{
	@Test
	public void encodesRowMajorByYWithSceneIndex()
	{
		int[][] flags = new int[InstanceCoords.SCENE_SIZE][InstanceCoords.SCENE_SIZE];
		flags[5][7] = 42;      // sceneX 5, sceneY 7
		flags[103][0] = 9;
		List<Integer> out = CollisionGrid.encode(flags);
		assertEquals(InstanceCoords.SCENE_SIZE * InstanceCoords.SCENE_SIZE, out.size());
		assertEquals(42, (int) out.get(InstanceCoords.sceneIndex(5, 7)));
		assertEquals(9, (int) out.get(InstanceCoords.sceneIndex(103, 0)));
		assertEquals(0, (int) out.get(InstanceCoords.sceneIndex(0, 0)));
	}

	@Test
	public void shortOrNullRowsReadAsZero()
	{
		int[][] flags = new int[3][];
		flags[0] = new int[]{1, 2};
		flags[1] = null;
		List<Integer> out = CollisionGrid.encode(flags);
		assertEquals(InstanceCoords.SCENE_SIZE * InstanceCoords.SCENE_SIZE, out.size());
		assertEquals(2, (int) out.get(InstanceCoords.sceneIndex(0, 1)));
		assertEquals(0, (int) out.get(InstanceCoords.sceneIndex(1, 0)));
		assertEquals(0, (int) out.get(InstanceCoords.sceneIndex(50, 50)));
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat test --tests "com.example.providers.CollisionGridTest" --console=plain -q`
Expected: compile error, `cannot find symbol: class CollisionGrid`.

- [ ] **Step 3: Write the encoder**

```java
package com.example.providers;

import com.example.util.InstanceCoords;

import java.util.ArrayList;
import java.util.List;

/**
 * A whole plane's collision flags as one flat list, row-major by y (index =
 * {@link InstanceCoords#sceneIndex}). ~10816 small ints, about 60 KB on the wire, so a routine
 * loads a room-scale map in ONE request instead of 25-tile squares. Missing rows or cells read
 * as 0 (no flags), the same "cannot tell" the per-tile endpoint would skip.
 */
final class CollisionGrid
{
	private CollisionGrid()
	{
	}

	static List<Integer> encode(int[][] flags)
	{
		int n = InstanceCoords.SCENE_SIZE;
		List<Integer> out = new ArrayList<>(n * n);
		for (int sy = 0; sy < n; sy++)
		{
			for (int sx = 0; sx < n; sx++)
			{
				int v = 0;
				if (flags != null && sx < flags.length && flags[sx] != null && sy < flags[sx].length)
				{
					v = flags[sx][sy];
				}
				out.add(v);
			}
		}
		return out;
	}
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat test --tests "com.example.providers.CollisionGridTest" --console=plain -q`
Expected: exit 0.

- [ ] **Step 5: Serve it from `/collision`**

In `CollisionProvider.getCollision`, right after `int[][] flags = maps[plane].getFlags();` and its null-check (before the scene/tiles checks -- the grid needs only flags):

```java
		// all=true (2026-09-05): the whole plane in one compact grid. See CollisionGrid.
		String all = query.get("all");
		if (all != null && ("true".equalsIgnoreCase(all) || "1".equals(all)))
		{
			data.put("baseX", view.getBaseX());
			data.put("baseY", view.getBaseY());
			data.put("plane", plane);
			data.put("size", InstanceCoords.SCENE_SIZE);
			data.put("flags", CollisionGrid.encode(flags));
			return data;
		}
```

Make sure the early-return error paths above it (`view == null`, `maps == null`, `flags == null`) also put an empty `"flags"` list when `all` was asked for -- simplest: at the top of the method compute `boolean wantAll` and in each early-return `data.put(wantAll ? "flags" : "tiles", new ArrayList<>())`.

- [ ] **Step 6: Compile and run the Java suite**

Run: `./gradlew.bat test --console=plain -q`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/providers/CollisionGrid.java src/test/java/com/example/providers/CollisionGridTest.java src/main/java/com/example/providers/CollisionProvider.java
git commit -m "bridge: /collision?all=true serves the whole plane as one row-major grid

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `NPC_SPAWNED` / `NPC_DESPAWNED` events

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (next to `onActorDeath`, ~line 512)

**Interfaces:**
- Consumes: `spawnEventBudgetAvailable()`, `tickClock.get()`, `udpSender.send(String, Object)` already in the class; `InstanceCoords.instancePoint`.
- Produces: UDP `NPC_SPAWNED` / `NPC_DESPAWNED` with `{id, index, name, x, y, instX, instY, plane, tick}`.

- [ ] **Step 1: Add the handlers**

```java
	@Subscribe
	public void onNpcSpawned(NpcSpawned event)
	{
		sendNpcSpawnEvent("NPC_SPAWNED", event.getNpc());
	}

	@Subscribe
	public void onNpcDespawned(NpcDespawned event)
	{
		sendNpcSpawnEvent("NPC_DESPAWNED", event.getNpc());
	}

	/**
	 * Same shape and guards as sendObjectEvent: LOGGED_IN filter, the shared per-tick spawn
	 * budget (a scene load replays EVERY npc as a spawn, the same flood the object events hit),
	 * and the udpSender null-check for the shutDown race. Added 2026-09-05 for Gauntlet
	 * sub-project 1 -- a tornado is an NPC (9025 / 9039) and the fight routine wants to hear it
	 * appear without polling /npcs. Accelerator only: the routine confirms on HTTP before acting.
	 */
	private void sendNpcSpawnEvent(String type, NPC npc)
	{
		if (client.getGameState() != GameState.LOGGED_IN || npc == null)
		{
			return;
		}
		if (!spawnEventBudgetAvailable())
		{
			return;
		}
		if (udpSender == null)
		{
			return;
		}
		LocalPoint lp = npc.getLocalLocation();
		Map<String, Object> payload = new HashMap<>();
		payload.put("id", npc.getId());
		payload.put("index", npc.getIndex());
		payload.put("name", npc.getName());
		if (lp != null)
		{
			WorldPoint wp = WorldPoint.fromLocalInstance(client, lp);
			WorldPoint inst = InstanceCoords.instancePoint(client, lp, client.getPlane());
			payload.put("x", wp.getX());
			payload.put("y", wp.getY());
			payload.put("plane", wp.getPlane());
			payload.put("instX", inst.getX());
			payload.put("instY", inst.getY());
		}
		payload.put("tick", tickClock.get());
		udpSender.send(type, payload);
	}
```

`import net.runelite.api.events.*;` already covers `NpcSpawned`/`NpcDespawned`. Add `import com.example.util.InstanceCoords;` and `import net.runelite.api.coords.LocalPoint;` if not present (check the existing import block; `WorldPoint` is already imported since `sendObjectEvent` uses it).

- [ ] **Step 2: Compile and run the Java suite**

Run: `./gradlew.bat test --console=plain -q`
Expected: exit 0. Also grep that no other `@Subscribe` in the class is named `onNpcSpawned`/`onNpcDespawned` (a duplicate would break registration): `grep -n "onNpcSpawned\|onNpcDespawned" src/main/java/com/example/controller/ControllerPlugin.java` -- expected: exactly one definition each.

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "bridge: NPC_SPAWNED / NPC_DESPAWNED udp events under the spawn budget

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `ProjectileSeen` and `PROJECTILE_SPAWNED`

**Files:**
- Create: `src/main/java/com/example/util/ProjectileSeen.java`
- Test: `src/test/java/com/example/util/ProjectileSeenTest.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (a field + `onProjectileMoved`; reset in the same place `spawnEventBudgetTick` lives is NOT needed -- the set is weak)

**Interfaces:**
- Produces: `ProjectileSeen.firstSighting(Object projectile)` -> `true` exactly once per distinct object (identity, not `equals`), `false` on every later call for the same object; `int size()` for tests.
- Produces: UDP `PROJECTILE_SPAWNED` with `{id, remainingCycles, targetIndex, x, y, instX, instY, plane, tick}`.

- [ ] **Step 1: Write the failing test**

```java
package com.example.util;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ProjectileSeenTest
{
	@Test
	public void firstSightingOnlyOnce()
	{
		ProjectileSeen seen = new ProjectileSeen();
		Object p = new Object();
		assertTrue(seen.firstSighting(p));
		assertFalse(seen.firstSighting(p));
		assertFalse(seen.firstSighting(p));
	}

	@Test
	public void distinctObjectsAreDistinctSightingsEvenWhenEqual()
	{
		// Identity, never equals(): two projectiles with the same id/cycle are two attacks.
		ProjectileSeen seen = new ProjectileSeen();
		Object a = new String("same");
		Object b = new String("same");
		assertTrue(seen.firstSighting(a));
		assertTrue(seen.firstSighting(b));
	}

	@Test
	public void nullIsNeverASighting()
	{
		assertFalse(new ProjectileSeen().firstSighting(null));
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat test --tests "com.example.util.ProjectileSeenTest" --console=plain -q`
Expected: compile error, `cannot find symbol: class ProjectileSeen`.

- [ ] **Step 3: Write the class**

```java
package com.example.util;

import java.util.Collections;
import java.util.Set;
import java.util.WeakHashMap;

/**
 * "Have I seen this projectile before?" for {@code ProjectileMoved}, which RuneLite fires every
 * client tick for the projectile's whole flight. A PROJECTILE_SPAWNED event must fire once per
 * projectile, so the first sighting is what matters. Identity semantics on purpose (two attacks
 * with the same projectile id are two objects), weak so a finished projectile is collected
 * without bookkeeping. Not thread-safe: called from the client thread only.
 */
public final class ProjectileSeen
{
	// WeakHashMap keys on equals()/hashCode(); Projectile does not override them, so this is
	// identity in practice. Object keys keep the class testable without a real Projectile.
	private final Set<Object> seen = Collections.newSetFromMap(new WeakHashMap<>());

	public boolean firstSighting(Object projectile)
	{
		if (projectile == null)
		{
			return false;
		}
		return seen.add(projectile);
	}

	public int size()
	{
		return seen.size();
	}
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat test --tests "com.example.util.ProjectileSeenTest" --console=plain -q`
Expected: exit 0.

- [ ] **Step 5: Confirm the projectile API and add the handler**

Run: `javap -cp "$JAR" net.runelite.api.Projectile | grep -i "getId\|getRemainingCycles\|getInteracting\|getTarget\|getX()\|getY()\|getTarget"` and `javap -cp "$JAR" net.runelite.api.events.ProjectileMoved`.
Expected: `getId()`, `getRemainingCycles()`, an actor accessor (`getInteracting()` or `getTargetActor()` -- use whichever exists), and `ProjectileMoved.getProjectile()` / `getPosition()` (a `LocalPoint`).

In `ControllerPlugin`, add the field `private final ProjectileSeen projectileSeen = new ProjectileSeen();` next to the other trackers, and:

```java
	@Subscribe
	public void onProjectileMoved(ProjectileMoved event)
	{
		Projectile projectile = event.getProjectile();
		if (!projectileSeen.firstSighting(projectile))
		{
			return;
		}
		if (client.getGameState() != GameState.LOGGED_IN || udpSender == null)
		{
			return;
		}
		if (!spawnEventBudgetAvailable())
		{
			return;
		}
		Map<String, Object> payload = new HashMap<>();
		payload.put("id", projectile.getId());
		payload.put("remainingCycles", projectile.getRemainingCycles());
		Actor target = projectile.getInteracting();   // or getTargetActor(), per step 5's javap
		payload.put("targetIndex", target instanceof NPC ? ((NPC) target).getIndex()
			: target == client.getLocalPlayer() ? -2 : -1);
		LocalPoint pos = event.getPosition();
		if (pos != null)
		{
			WorldPoint wp = WorldPoint.fromLocalInstance(client, pos);
			WorldPoint inst = InstanceCoords.instancePoint(client, pos, client.getPlane());
			payload.put("x", wp.getX());
			payload.put("y", wp.getY());
			payload.put("plane", wp.getPlane());
			payload.put("instX", inst.getX());
			payload.put("instY", inst.getY());
		}
		payload.put("tick", tickClock.get());
		udpSender.send("PROJECTILE_SPAWNED", payload);
	}
```

`targetIndex` convention: an NPC's index, `-2` for the local player (the case the Hunllef fight cares about), `-1` for none. Document it in the method comment.

- [ ] **Step 6: Compile and run the Java suite**

Run: `./gradlew.bat test --console=plain -q`
Expected: exit 0. `grep -c "onProjectileMoved" src/main/java/com/example/controller/ControllerPlugin.java` -- expected 1.

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/util/ProjectileSeen.java src/test/java/com/example/util/ProjectileSeenTest.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "bridge: PROJECTILE_SPAWNED udp event, once per projectile (ProjectileSeen)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Python wrappers -- `inst=` and `get_collision_grid`

**Files:**
- Modify: `ControllerAddon/controller.py` (`get_minimap_walk_point` ~line 946, `get_collision` ~950, `get_ground_walk_point` ~960, `get_object_at` ~491, `get_ground_item_at` ~707)
- Test: `ControllerAddon/tests/test_controller.py` (append)

**Interfaces:**
- Produces: `get_minimap_walk_point(x, y, plane, inst=False)`, `get_ground_walk_point(x, y, plane, inst=False)`, `get_object_at(x, y, plane, obj_id, radius=None, inst=False)`, `get_ground_item_at(x, y, plane, item_id, inst=False)`, `get_collision(x, y, plane, radius=6, inst=False)`, and new `get_collision_grid(plane=None) -> dict | None` returning the raw `/collision?all=true` body (`{"baseX","baseY","plane","size","flags"}`) or `None` when the bridge did not answer or `flags` is empty.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_controller.py` (it already imports `controller`; if it uses a different stub style for `api_get`, match it):

```python
def test_inst_flag_is_forwarded_to_the_bridge(monkeypatch):
    calls = []

    def fake_api_get(path, params=None, timeout=None, **kw):
        calls.append((path, dict(params or {})))
        return {"onScreen": True, "onMinimap": True, "found": True, "tiles": [], "flags": [1]}

    monkeypatch.setattr(controller, "api_get", fake_api_get)
    controller.get_ground_walk_point(1, 2, 0, inst=True)
    controller.get_minimap_walk_point(1, 2, 0, inst=True)
    controller.get_object_at(1, 2, 0, 99, inst=True)
    controller.get_ground_item_at(1, 2, 0, 7, inst=True)
    controller.get_collision(1, 2, 0, radius=3, inst=True)
    controller.get_ground_walk_point(1, 2, 0)   # default: no flag on the wire
    assert [p.get("inst") for _, p in calls[:5]] == ["true"] * 5
    assert "inst" not in calls[5][1]


def test_get_collision_grid_returns_body_or_none(monkeypatch):
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, timeout=None, **kw:
                        {"baseX": 13056, "baseY": 5632, "plane": 1, "size": 104, "flags": [0] * 10816})
    grid = controller.get_collision_grid(1)
    assert grid["baseX"] == 13056 and len(grid["flags"]) == 10816
    monkeypatch.setattr(controller, "api_get", lambda *a, **kw: {"flags": []})
    assert controller.get_collision_grid(1) is None
    monkeypatch.setattr(controller, "api_get", lambda *a, **kw: None)
    assert controller.get_collision_grid(1) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `ControllerAddon/`): `python -m pytest tests/test_controller.py -q -k "inst_flag or collision_grid"`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'inst'` and `AttributeError: get_collision_grid`).

- [ ] **Step 3: Implement**

`controller.py`:

```python
def _inst_params(params: dict, inst: bool) -> dict:
    """Add the inst=true flag (instance-space x/y, see InstanceCoords.java) only when asked, so
    a pre-2026-09-05 bridge sees exactly the old query."""
    if inst:
        params["inst"] = "true"
    return params


def get_minimap_walk_point(x: int, y: int, plane: int, inst: bool = False):
    return api_get("/minimap/walk", params=_inst_params({"x": x, "y": y, "plane": plane}, inst)) or {"onMinimap": False}


def get_collision(x: int, y: int, plane: int, radius: int = 6, inst: bool = False):
    """...existing docstring..."""
    data = api_get("/collision", params=_inst_params({"x": x, "y": y, "plane": plane, "radius": radius}, inst))
    return (data or {}).get("tiles", [])


def get_collision_grid(plane: int | None = None):
    """The whole loaded plane's collision flags in one request: {baseX, baseY, plane, size,
    flags} with flags row-major by y (index = sy * size + sx, CollisionGrid.java). None when
    the bridge cannot answer -- an older build, or no scene loaded. This is instance_nav's map
    source; worldmap.py is useless inside an instance."""
    params = {"all": "true"}
    if plane is not None:
        params["plane"] = plane
    data = api_get("/collision", params=params, timeout=2.0)
    if not data or not data.get("flags"):
        return None
    return data


def get_ground_walk_point(x: int, y: int, plane: int, inst: bool = False):
    return api_get("/ground/walk", params=_inst_params({"x": x, "y": y, "plane": plane}, inst)) or {"onScreen": False}
```

`get_object_at`: add `inst: bool = False` to the signature and `_inst_params(params, inst)` before the `api_get`. `get_ground_item_at`: same, building its params dict through `_inst_params`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_controller.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "controller: inst= on the tile-taking wrappers; get_collision_grid

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: `instance_nav.InstanceMap` and `find_path`

**Files:**
- Create: `ControllerAddon/instance_nav.py`
- Test: `ControllerAddon/tests/test_instance_nav.py`

**Interfaces:**
- Consumes: `controller.get_collision_grid(plane)` (Task 8); `travel.BLOCK_MOVEMENT_NORTH/EAST/SOUTH/WEST`, `travel.TILE_BLOCKING_FLAGS`.
- Produces:
  - `class InstanceMap`: `InstanceMap(base_x, base_y, plane, size, flags: list[int])`; `classmethod load(plane=None) -> InstanceMap | None`; `flags_at(x, y) -> int | None` (instance-space; `None` off-grid); `standable(x, y) -> bool`; `edge_open(x, y, dx, dy) -> bool` (cardinal only); `step_ok(x, y, dx, dy) -> bool` (cardinal or diagonal, diagonal needs both corner routes fully open).
  - `find_path(imap, start: tuple, goal: tuple, goal_adjacent=False, avoid: set | None = None) -> list[tuple] | None` -- BFS, 8-directional, path includes `start` and ends at `goal` (or at a cardinal neighbour of it when `goal_adjacent`); `None` when unreachable.
  - `neighbours_of(x, y) -> list[(x, y)]` (the 8 offsets, for tests).

- [ ] **Step 1: Write the failing tests**

```python
"""instance_nav: BFS over a /collision?all=true grid in instance space (Gauntlet sub-project 1,
docs/superpowers/specs/2026-09-05-gauntlet-instance-plumbing-design.md)."""
import pytest

import controller
import instance_nav
from travel import (BLOCK_MOVEMENT_EAST, BLOCK_MOVEMENT_NORTH, BLOCK_MOVEMENT_OBJECT,
                    BLOCK_MOVEMENT_SOUTH, BLOCK_MOVEMENT_WEST)

BASE_X, BASE_Y = 13056, 5632


def make_map(size=8, walls=None, blocked=None):
    """A size x size grid at BASE_X/BASE_Y. `walls` is {(x, y): flags} in GRID coords, `blocked`
    a set of grid tiles carrying BLOCK_MOVEMENT_OBJECT."""
    flags = [0] * (size * size)
    for (gx, gy), f in (walls or {}).items():
        flags[gy * size + gx] |= f
    for gx, gy in (blocked or ()):
        flags[gy * size + gx] |= BLOCK_MOVEMENT_OBJECT
    return instance_nav.InstanceMap(BASE_X, BASE_Y, 1, size, flags)


def P(gx, gy):
    return (BASE_X + gx, BASE_Y + gy)


def test_flags_at_maps_instance_space_onto_the_grid():
    imap = make_map(walls={(2, 3): 7})
    assert imap.flags_at(*P(2, 3)) == 7
    assert imap.flags_at(*P(0, 0)) == 0
    assert imap.flags_at(BASE_X - 1, BASE_Y) is None
    assert imap.flags_at(BASE_X + 8, BASE_Y) is None


def test_straight_corridor():
    imap = make_map()
    path = instance_nav.find_path(imap, P(0, 0), P(5, 0))
    assert path[0] == P(0, 0) and path[-1] == P(5, 0)
    assert len(path) == 6


def test_one_sided_wall_edge_blocks_both_directions():
    # A wall on the EAST edge of (2,0), recorded only on that tile (a one-sided grid).
    imap = make_map(walls={(2, 0): BLOCK_MOVEMENT_EAST})
    assert not imap.edge_open(*P(2, 0), 1, 0)
    assert not imap.edge_open(*P(3, 0), -1, 0)
    assert imap.edge_open(*P(1, 0), 1, 0)


def test_diagonal_needs_both_corner_routes():
    # Moving (0,0)->(1,1): both (1,0) and (0,1) must be standable with all four edges open.
    imap = make_map(blocked={(1, 0)})
    assert not imap.step_ok(*P(0, 0), 1, 1)
    imap = make_map(walls={(0, 0): BLOCK_MOVEMENT_NORTH})
    assert not imap.step_ok(*P(0, 0), 1, 1)
    assert imap.step_ok(*P(0, 0), 1, 0)
    imap = make_map()
    assert imap.step_ok(*P(0, 0), 1, 1)


def test_routes_around_a_wall_line():
    # Vertical wall on the east edge of x=3 for y in 0..5; the gap is at y=6.
    walls = {(3, y): BLOCK_MOVEMENT_EAST for y in range(6)}
    imap = make_map(walls=walls)
    path = instance_nav.find_path(imap, P(0, 0), P(7, 0))
    assert path is not None
    assert any(y == BASE_Y + 6 for _, y in path)
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        assert max(abs(x2 - x1), abs(y2 - y1)) == 1


def test_unreachable_returns_none_and_avoid_excludes_tiles():
    walls = {(3, y): BLOCK_MOVEMENT_EAST for y in range(8)}
    assert instance_nav.find_path(make_map(walls=walls), P(0, 0), P(7, 0)) is None
    imap = make_map()
    direct = instance_nav.find_path(imap, P(0, 0), P(2, 0))
    assert P(1, 0) in direct
    detour = instance_nav.find_path(imap, P(0, 0), P(2, 0), avoid={P(1, 0)})
    assert detour is not None and P(1, 0) not in detour


def test_goal_adjacent_stops_beside_a_blocked_goal():
    imap = make_map(blocked={(4, 4)})
    assert instance_nav.find_path(imap, P(0, 0), P(4, 4)) is None
    path = instance_nav.find_path(imap, P(0, 0), P(4, 4), goal_adjacent=True)
    assert path is not None
    end = path[-1]
    assert abs(end[0] - P(4, 4)[0]) + abs(end[1] - P(4, 4)[1]) == 1


def test_load_reads_the_bridge_grid(monkeypatch):
    monkeypatch.setattr(controller, "get_collision_grid",
                        lambda plane=None: {"baseX": 1, "baseY": 2, "plane": 1, "size": 2, "flags": [0, 5, 0, 0]})
    imap = instance_nav.InstanceMap.load(1)
    assert imap.flags_at(2, 2) == 5
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: None)
    assert instance_nav.InstanceMap.load(1) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_instance_nav.py -q`
Expected: `ModuleNotFoundError: No module named 'instance_nav'`.

- [ ] **Step 3: Implement the map and BFS**

```python
"""Routing inside an INSTANCE, where worldmap.py's static map is meaningless.

Added 2026-09-05 (Gauntlet sub-project 1, docs/superpowers/specs/2026-09-05-gauntlet-instance-
plumbing-design.md). Coordinates here are INSTANCE-SPACE (`instX`/`instY` on the bridge, the
client's own scene base plus scene offset) -- unique per scene tile and stable while the scene is
loaded, which for the Gauntlet (one region, 7768, plane 1) is the whole run. The map is one
`/collision?all=true` read (CollisionGrid.java: flat, row-major by y, index = sy * size + sx),
so a room-scale route costs one HTTP call plus a BFS over at most 104x104 tiles.

Edge semantics are travel.py's (both tiles' opposite movement bits refuse a crossing; a tile
carrying TILE_BLOCKING_FLAGS cannot be stood on). A diagonal step needs BOTH orthogonal corner
routes fully open -- the game's own rule, and the conservative reading of it. No doors, no
transports: the Gauntlet has neither. Empty answers mean "cannot tell", never "blocked":
InstanceMap.load returns None and find_path returns None, and walk_to then declines to click.
"""
from __future__ import annotations

from collections import deque

import controller
import debug_log
from travel import (BLOCK_MOVEMENT_EAST, BLOCK_MOVEMENT_NORTH, BLOCK_MOVEMENT_SOUTH,
                    BLOCK_MOVEMENT_WEST, TILE_BLOCKING_FLAGS)

CARDINALS = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAGONALS = ((1, 1), (1, -1), (-1, 1), (-1, -1))


class InstanceMap:
    def __init__(self, base_x: int, base_y: int, plane: int, size: int, flags: list):
        self.base_x = base_x
        self.base_y = base_y
        self.plane = plane
        self.size = size
        self.flags = flags

    @classmethod
    def load(cls, plane: int | None = None):
        grid = controller.get_collision_grid(plane)
        if not grid:
            return None
        size = int(grid.get("size") or 104)
        flags = grid.get("flags") or []
        if len(flags) < size * size:
            return None
        return cls(int(grid["baseX"]), int(grid["baseY"]), int(grid.get("plane", plane or 0)), size, flags)

    def flags_at(self, x: int, y: int):
        sx, sy = x - self.base_x, y - self.base_y
        if not (0 <= sx < self.size and 0 <= sy < self.size):
            return None
        return self.flags[sy * self.size + sx]

    def standable(self, x: int, y: int) -> bool:
        f = self.flags_at(x, y)
        return f is not None and not (f & TILE_BLOCKING_FLAGS)

    def edge_open(self, x: int, y: int, dx: int, dy: int) -> bool:
        """Cardinal edge from (x, y) toward (dx, dy): refused if EITHER tile records the wall."""
        a = self.flags_at(x, y)
        b = self.flags_at(x + dx, y + dy)
        if a is None or b is None:
            return False
        if dx == 1:
            return not (a & BLOCK_MOVEMENT_EAST or b & BLOCK_MOVEMENT_WEST)
        if dx == -1:
            return not (a & BLOCK_MOVEMENT_WEST or b & BLOCK_MOVEMENT_EAST)
        if dy == 1:
            return not (a & BLOCK_MOVEMENT_NORTH or b & BLOCK_MOVEMENT_SOUTH)
        return not (a & BLOCK_MOVEMENT_SOUTH or b & BLOCK_MOVEMENT_NORTH)

    def step_ok(self, x: int, y: int, dx: int, dy: int) -> bool:
        """Can the player step from (x, y) by (dx, dy)? Destination must be standable; a
        cardinal needs its edge open; a diagonal needs both corner routes standable and open."""
        nx, ny = x + dx, y + dy
        if not self.standable(nx, ny):
            return False
        if dx == 0 or dy == 0:
            return self.edge_open(x, y, dx, dy)
        cx, cy = x + dx, y          # corner via the x step
        kx, ky = x, y + dy          # corner via the y step
        via_x = (self.standable(cx, cy) and self.edge_open(x, y, dx, 0)
                 and self.edge_open(cx, cy, 0, dy))
        via_y = (self.standable(kx, ky) and self.edge_open(x, y, 0, dy)
                 and self.edge_open(kx, ky, dx, 0))
        return via_x and via_y


def neighbours_of(x: int, y: int) -> list:
    return [(x + dx, y + dy) for dx, dy in CARDINALS + DIAGONALS]


def find_path(imap: InstanceMap, start: tuple, goal: tuple, goal_adjacent: bool = False,
              avoid: set | None = None):
    """BFS from start to goal over the instance map. Returns the tile list including start, or
    None. With goal_adjacent the search ends on any cardinal neighbour of the goal (the goal
    itself is usually furniture). `avoid` tiles are never entered."""
    start = (int(start[0]), int(start[1]))
    goal = (int(goal[0]), int(goal[1]))
    avoid = avoid or set()
    if imap.flags_at(*start) is None:
        return None

    def done(t):
        if goal_adjacent:
            return abs(t[0] - goal[0]) + abs(t[1] - goal[1]) == 1
        return t == goal

    if done(start):
        return [start]
    prev = {start: None}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for dx, dy in CARDINALS + DIAGONALS:
            nxt = (x + dx, y + dy)
            if nxt in prev or nxt in avoid:
                continue
            if not imap.step_ok(x, y, dx, dy):
                continue
            prev[nxt] = (x, y)
            if done(nxt):
                path = [nxt]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            queue.append(nxt)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_instance_nav.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/instance_nav.py ControllerAddon/tests/test_instance_nav.py
git commit -m "instance_nav: InstanceMap over /collision?all=true and an 8-way BFS

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `travel._hop_to(inst=)` and `instance_nav.walk_to`

**Files:**
- Modify: `ControllerAddon/travel.py` (`_hop_to`, ~line 1092)
- Modify: `ControllerAddon/instance_nav.py`
- Test: `ControllerAddon/tests/test_travel.py` (append), `ControllerAddon/tests/test_instance_nav.py` (append)

**Interfaces:**
- Consumes: `controller.get_player()` (`instX`/`instY` from Task 1), the `inst=` wrappers (Task 8), `InstanceMap`/`find_path` (Task 9), `travel._hop_to`, `travel.WALK_ARRIVAL_TOLERANCE`, `travel.WALK_HOP_MAX_TILES`, `travel.WALK_MAX_REROUTES`.
- Produces: `travel._hop_to(ctx, x, y, plane, inst: bool = False) -> bool` (unchanged behaviour when `inst` is False); `instance_nav.player_inst() -> tuple[int, int, int] | None` (`instX, instY, plane`); `instance_nav.walk_to(ctx, x, y, tolerance=WALK_ARRIVAL_TOLERANCE) -> bool`; `instance_nav.WALK_MAX_HOPS = 40`.

- [ ] **Step 1: Write the failing travel test**

Append to `tests/test_travel.py` (the module already has `_Ctx` and stubs `controller`):

```python
def test_hop_to_inst_reads_inst_position_and_forwards_the_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1900, "y": 5600, "instX": 13060, "instY": 5640, "plane": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane, inst=False: (calls.append(("mm", x, y, inst)) or
                                                          {"onMinimap": True, "screenX": 5, "screenY": 5}))
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, inst=False: (calls.append(("gd", x, y, inst)) or
                                                          {"onScreen": False}))
    monkeypatch.setattr(travel, "_click_walk_point", lambda ctx, point, shift: calls.append(("click", shift)))
    monkeypatch.setattr(travel.time, "sleep", lambda s: None)
    monkeypatch.setattr(travel, "WALK_HOP_TIMEOUT_S", 0.0)

    assert travel._hop_to(_Ctx(), 13080, 5640, 1, inst=True)
    # The origin for the short-hop test and pull-in is the INSTANCE position (20 tiles away =>
    # minimap), and every lookup carries inst=True.
    assert ("mm", 13080, 5640, True) in calls
    assert all(c[3] is True for c in calls if c[0] in ("mm", "gd"))
    assert ("click", False) in calls
```

Run: `python -m pytest tests/test_travel.py -q -k hop_to_inst` -- expected FAIL (`TypeError: unexpected keyword argument 'inst'`).

- [ ] **Step 2: Add `inst` to `_hop_to`**

In `travel.py`:

```python
def _hop_to(ctx, x: int, y: int, plane: int, inst: bool = False) -> bool:
```

Inside, after `origin = controller.get_player()`, replace the origin read with:

```python
    # inst=True (2026-09-05, instance_nav): x/y and the origin are INSTANCE-SPACE, and every
    # point lookup carries the flag through to the bridge. Nothing else about the hop changes.
    if inst:
        ox, oy = origin.get("instX"), origin.get("instY")
    else:
        ox, oy = origin.get("x"), origin.get("y")
```

Change the three lookups to pass the flag: `controller.get_ground_walk_point(aim_x, aim_y, plane, inst=inst)` (both call sites) and `controller.get_minimap_walk_point(aim_x, aim_y, plane, inst=inst)`. In the arrival wait, read the same space:

```python
        player = controller.get_player()
        if inst:
            px, py = player.get("instX"), player.get("instY")
        else:
            px, py = player.get("x"), player.get("y")
```

Run: `python -m pytest tests/test_travel.py -q` -- expected: all pass (the existing hop tests exercise the `inst=False` path unchanged).

- [ ] **Step 3: Write the failing `walk_to` tests**

Append to `tests/test_instance_nav.py`:

```python
class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = type("S", (), {"data": {}})()


def _fake_world(monkeypatch, imap, start, hop_effect):
    """Stub the bridge: the player stands at `start` (instance space) and every _hop_to call
    moves it per hop_effect(target) -> new position."""
    pos = {"p": start}
    monkeypatch.setattr(controller, "get_collision_grid",
                        lambda plane=None: {"baseX": imap.base_x, "baseY": imap.base_y, "plane": imap.plane,
                                            "size": imap.size, "flags": imap.flags})
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"instX": pos["p"][0], "instY": pos["p"][1], "plane": imap.plane,
                                 "x": -1, "y": -1})
    hops = []

    def fake_hop(ctx, x, y, plane, inst=False):
        assert inst is True
        hops.append((x, y))
        pos["p"] = hop_effect((x, y))
        return True

    monkeypatch.setattr(instance_nav.travel, "_hop_to", fake_hop)
    return hops


def test_walk_to_hops_along_the_route_and_arrives(monkeypatch):
    imap = make_map(size=40)
    hops = _fake_world(monkeypatch, imap, P(0, 0), lambda t: t)
    assert instance_nav.walk_to(_Ctx(), *P(30, 0))
    assert hops and hops[-1] == P(30, 0)
    assert all(max(abs(b[0] - a[0]), abs(b[1] - a[1])) <= instance_nav.travel.WALK_HOP_MAX_TILES
               for a, b in zip([P(0, 0)] + hops, hops))


def test_walk_to_returns_false_without_a_map_or_route(monkeypatch):
    imap = make_map(walls={(3, y): BLOCK_MOVEMENT_EAST for y in range(8)})
    hops = _fake_world(monkeypatch, imap, P(0, 0), lambda t: t)
    assert not instance_nav.walk_to(_Ctx(), *P(7, 0))
    assert hops == []
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: None)
    assert not instance_nav.walk_to(_Ctx(), *P(7, 0))


def test_walk_to_reroutes_around_a_tile_that_refuses(monkeypatch):
    # The map says (1,0) is fine; the "game" never moves us when we aim through it.
    imap = make_map()
    refused = P(1, 0)

    def effect(target):
        return P(0, 0) if target == refused else target

    hops = _fake_world(monkeypatch, imap, P(0, 0), effect)
    monkeypatch.setattr(instance_nav.travel, "WALK_HOP_MAX_TILES", 1)
    assert instance_nav.walk_to(_Ctx(), *P(2, 0))
    assert refused in hops                       # tried it once
    assert hops[-1] == P(2, 0)


def test_walk_to_gives_up_after_the_hop_ceiling(monkeypatch):
    imap = make_map(size=40)
    hops = _fake_world(monkeypatch, imap, P(0, 0), lambda t: P(0, 0))   # never moves
    monkeypatch.setattr(instance_nav, "WALK_MAX_HOPS", 3)
    assert not instance_nav.walk_to(_Ctx(), *P(30, 0))
    assert len(hops) <= 3 + instance_nav.travel.WALK_MAX_REROUTES
```

Run: `python -m pytest tests/test_instance_nav.py -q -k walk_to` -- expected FAIL (`AttributeError: walk_to`).

- [ ] **Step 4: Implement `player_inst` and `walk_to`**

Append to `instance_nav.py` (and add `import travel` at the top, after `import debug_log`):

```python
WALK_MAX_HOPS = 40           # a Gauntlet room-to-room walk is 2-4 hops; 40 is a wedge, not a walk


def player_inst():
    """(instX, instY, plane) from /player, or None when the bridge cannot say."""
    p = controller.get_player() or {}
    x, y = p.get("instX"), p.get("instY")
    if x is None or y is None:
        return None
    return int(x), int(y), int(p.get("plane") or 0)


def walk_to(ctx, x: int, y: int, tolerance: float = travel.WALK_ARRIVAL_TOLERANCE) -> bool:
    """Walk to an instance-space tile: reload the map, route from the player's instX/instY,
    hop along the route through travel._hop_to(inst=True) (minimap beyond
    HOP_VIEWPORT_MAX_TILES, shift-clicked viewport inside it, the lead-click release, the
    blocker opt-outs -- all inherited). A hop that leaves the player on the same tile marks the
    route's next tile as refused and re-routes (`avoid`, bounded by travel.WALK_MAX_REROUTES).
    False when there is no map, no route, or WALK_MAX_HOPS is spent."""
    goal = (int(x), int(y))
    avoid: set = set()
    reroutes = 0
    hops = 0
    while hops < WALK_MAX_HOPS:
        here = player_inst()
        if here is None:
            debug_log.log("inav", "no instance position from /player -- cannot walk")
            return False
        px, py, plane = here
        if max(abs(px - goal[0]), abs(py - goal[1])) <= tolerance:
            return True
        imap = InstanceMap.load(plane)
        if imap is None:
            debug_log.log("inav", "no collision grid from the bridge -- cannot route")
            return False
        path = find_path(imap, (px, py), goal, avoid=avoid)
        if path is None:
            debug_log.log("inav", f"no route from {(px, py)} to {goal} (avoiding {len(avoid)} tiles)")
            return False
        # Farthest route point within one hop, so a long room crossing is 1-2 minimap clicks.
        idx = min(len(path) - 1, travel.WALK_HOP_MAX_TILES)
        while idx > 1 and max(abs(path[idx][0] - px), abs(path[idx][1] - py)) > travel.WALK_HOP_MAX_TILES:
            idx -= 1
        target = path[idx]
        hops += 1
        if not travel._hop_to(ctx, target[0], target[1], plane, inst=True):
            return False
        after = player_inst()
        if after is not None and (after[0], after[1]) == (px, py) and len(path) > 1:
            # Did not move at all: the first step is refused by something the map cannot see
            # (another player, a live blocker). Same rule as travel.walk_to's reroute, and a
            # shut door cannot be the cause here -- instances handled here have none.
            reroutes += 1
            if reroutes > travel.WALK_MAX_REROUTES:
                debug_log.log("inav", f"still on {(px, py)} after {reroutes} reroutes -- giving up")
                return False
            avoid.add(path[1])
            debug_log.log("inav", f"hop to {target} did not move us; avoiding {path[1]} and re-routing")
    debug_log.log("inav", f"spent {WALK_MAX_HOPS} hops without reaching {goal}")
    return False
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_instance_nav.py tests/test_travel.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/travel.py ControllerAddon/instance_nav.py ControllerAddon/tests/test_travel.py ControllerAddon/tests/test_instance_nav.py
git commit -m "instance_nav.walk_to over travel._hop_to(inst=True), with reroute and hop ceiling

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: `gauntlet_ids.py`

**Files:**
- Create: `ControllerAddon/gauntlet_ids.py`
- Test: `ControllerAddon/tests/test_gauntlet_ids.py`

**Interfaces:**
- Produces: module constants `HUNLLEF_CRYSTALLINE = {9021, 9022, 9023, 9024}`, `HUNLLEF_CORRUPTED = {9035, 9036, 9037, 9038}`, `HUNLLEF = HUNLLEF_CRYSTALLINE | HUNLLEF_CORRUPTED`, `TORNADO = {9025, 9039}`, `ANIM_ATTACK = 8419`, `ANIM_SWITCH_STYLE = 8754`, `ANIM_SPECIAL_TORNADO = 8755`, `ANIM_SPECIAL_PRAYER = 8418`, `ANIM_STOMP = 8420`, `ANIM_SPAWN = 8753`, `ANIM_ATTACK_LIKE = {ANIM_ATTACK, ANIM_SPECIAL_PRAYER}`, `PROJ_BOW = 249`, `PROJ_STAFF = {1720, 1723}`, `PROJ_PRAYER_ATTACK = {1713, 1714}`, `HAZARD_TILES = {36047, 36048}`, `VARBIT_FIGHTING_HUNLLEF = 9177`, `REGION_CORRUPTED = 7768`, `REGION_NORMAL = 7512`, `OBJ_BARRIER = 37337`, `OBJ_ENTRANCE = 37340`, `OBJ_REWARD_CHEST = 37341`, `HUNLLEF_ATTACK_PERIOD_TICKS = 5`, `HUNLLEF_ATTACKS_PER_STYLE = 4`, `TORNADO_LIFESPAN_TICKS = 20`, `WEAK_MONSTERS = {9040, 9041, 9042, 9043, 9044, 9045}`, `DEMI_BOSSES = {9046: "Corrupted Bear", 9047: "Corrupted Dragon"}` (plus the Dark Beast id once read off a recording that met one -- leave a comment, do not guess).

- [ ] **Step 1: Write the failing test**

```python
import gauntlet_ids as g


def test_npc_id_families_are_disjoint():
    families = [g.HUNLLEF, g.TORNADO, g.WEAK_MONSTERS, set(g.DEMI_BOSSES)]
    for i, a in enumerate(families):
        for b in families[i + 1:]:
            assert not (a & b)


def test_pinned_values_from_zombielite_and_the_recording():
    assert g.HUNLLEF_CORRUPTED == {9035, 9036, 9037, 9038}
    assert 9039 in g.TORNADO and 9025 in g.TORNADO
    assert g.ANIM_ATTACK == 8419 and g.ANIM_SWITCH_STYLE == 8754
    assert g.PROJ_PRAYER_ATTACK == {1713, 1714}
    assert g.HAZARD_TILES == {36047, 36048}
    assert g.VARBIT_FIGHTING_HUNLLEF == 9177
    assert g.REGION_CORRUPTED == 7768
    assert g.OBJ_REWARD_CHEST == 37341


def test_attack_counting_constants():
    assert g.HUNLLEF_ATTACK_PERIOD_TICKS == 5
    assert g.HUNLLEF_ATTACKS_PER_STYLE == 4
    assert g.ANIM_ATTACK_LIKE == {g.ANIM_ATTACK, g.ANIM_SPECIAL_PRAYER}
```

Run: `python -m pytest tests/test_gauntlet_ids.py -q` -- expected `ModuleNotFoundError`.

- [ ] **Step 2: Write the module**

```python
"""Gauntlet ids, each with its source. Pure data -- sub-project 2 (the Hunllef fight) and 3 (the
maze) import from here and never re-derive an id.

Sources:
  ZL  = ZombieLite, C:/Users/Kiyo/Documents/ZombieLite/src/main/java/com/salvation/data/
        (NpcId, AnimationId, ProjectileId, RegionId) and plugins/gauntlet/GauntletPlugin.java
        (hazard tiles, tornado lifespan, varbit 9177) and npcs/Hunllef.java (attack cadence).
  REC = recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.summary (object/npc ids seen).
"""

# --- NPCs (ZL NpcId; REC confirms 9035-9038 as "Corrupted Hunllef", 9039 as the tornado) ---
HUNLLEF_CRYSTALLINE = {9021, 9022, 9023, 9024}
HUNLLEF_CORRUPTED = {9035, 9036, 9037, 9038}
HUNLLEF = HUNLLEF_CRYSTALLINE | HUNLLEF_CORRUPTED
TORNADO = {9025, 9039}                       # crystalline / corrupted
WEAK_MONSTERS = {9040, 9041, 9042, 9043, 9044, 9045}   # REC: rat, spider, bat, unicorn, scorpion, wolf
DEMI_BOSSES = {9046: "Corrupted Bear", 9047: "Corrupted Dragon"}   # REC; Dark Beast id: read off a
                                                                     # recording that meets one

# --- Hunllef animations (ZL AnimationId) ---
ANIM_ATTACK = 8419
ANIM_SPECIAL_PRAYER = 8418        # the prayer-disabling attack; counts as a standard attack
ANIM_SPAWN = 8753
ANIM_SWITCH_STYLE = 8754
ANIM_SPECIAL_TORNADO = 8755
ANIM_STOMP = 8420
ANIM_ATTACK_LIKE = {ANIM_ATTACK, ANIM_SPECIAL_PRAYER}

# --- Attack cadence (ZL Hunllef.onAnimationChanged: next attack at tick+5; style flips every
#     4 standard attacks, (count // 4) % 2 == 0 -> RANGED else MAGIC) ---
HUNLLEF_ATTACK_PERIOD_TICKS = 5
HUNLLEF_ATTACKS_PER_STYLE = 4
TORNADO_LIFESPAN_TICKS = 20       # ZL GauntletPlugin.TORNADO_LIFESPAN_TICKS

# --- Projectiles (ZL ProjectileId) ---
PROJ_BOW = 249
PROJ_STAFF = {1720, 1723}         # crystalline / corrupted
PROJ_PRAYER_ATTACK = {1713, 1714}

# --- Floor hazards (ZL GauntletPlugin.HAZARD_TILE_OBJECT_IDS; GroundObjects) ---
HAZARD_TILES = {36047, 36048}

# --- Varbits / regions ---
VARBIT_FIGHTING_HUNLLEF = 9177    # ZL: 1 while in the boss room
REGION_CORRUPTED = 7768           # ZL RegionId; REC: the whole run is this one region
REGION_NORMAL = 7512

# --- Lobby objects (REC summary) ---
OBJ_BARRIER = 37337
OBJ_ENTRANCE = 37340              # "The Gauntlet"
OBJ_REWARD_CHEST = 37341
```

- [ ] **Step 3: Run the test**

Run: `python -m pytest tests/test_gauntlet_ids.py -q` -- expected 3 passed.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/gauntlet_ids.py ControllerAddon/tests/test_gauntlet_ids.py
git commit -m "gauntlet_ids: the Hunllef/tornado/projectile/hazard/region id table with sources

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Docs, full suites, rebuild, live checklist

**Files:**
- Modify: `CLAUDE.md` (the "Instanced areas break forward world-coordinate conversion" bullet under "Known environment gotchas"; the UDP event list in the Architecture diagram)

- [ ] **Step 1: CLAUDE.md**

Append to the instancing gotcha bullet:

```
  **RESOLVED for the Gauntlet 2026-09-05 (sub-project 1): the bridge now carries a second,
  INSTANCE-SPACE coordinate pair.** `instX`/`instY` (base + scene, `InstanceCoords.java`) on
  `/player` (+ `inInstance`, `baseX`, `baseY`), `/npcs`, `/npc/*`, `/objects`, `/object/at`,
  `/ground-items`, `/ground-item/at` and `/collision` tiles; `inst=true` on `/ground/walk`,
  `/minimap/walk`, `/object/at`, `/ground-item/at`, `/collision` resolves x/y by ARITHMETIC
  against the world view's base (never `LocalPoint.fromWorld`); `/collision?all=true` is the
  whole plane as one grid. `ControllerAddon/instance_nav.py` (`InstanceMap`, `find_path`,
  `walk_to`) is the instance routing engine; `worldmap.py` stays the overworld one. Template
  `x`/`y` keep their meaning and are still what a hardcoded tile in a routine matches. Live
  status: see the spec's checklist -- fill in what was verified here.
```

In the Architecture diagram's UDP event list add `NPC_SPAWNED/DESPAWNED, PROJECTILE_SPAWNED (2026-09-05)`. In "Current state of routines" add no new section yet -- the routine does not exist.

- [ ] **Step 2: Full Python suite**

Run (from `ControllerAddon/`): `python -m pytest tests/ -q` (about 7 minutes).
Expected: all pass. `py_compile` every touched Python file too: `python -m py_compile controller.py travel.py instance_nav.py gauntlet_ids.py`.

- [ ] **Step 3: Full Java suite**

Run: `./gradlew.bat test --console=plain -q` -- expected exit 0.

- [ ] **Step 4: Commit the docs**

```bash
git add CLAUDE.md
git commit -m "docs: instancing gotcha resolved via instance-space coordinates; new udp events

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 5: Rebuild the client and run the live checklist**

Ask the user to restart the client (`./gradlew.bat run`), then, with them in a player-owned house or the Gauntlet, run from `ControllerAddon/`:

```bash
python -c "import controller as c; p=c.get_player(); print({k:p.get(k) for k in ('x','y','instX','instY','inInstance','baseX','baseY','plane')})"
python -c "import controller as c; p=c.get_player(); print(c.get_ground_walk_point(p['instX'], p['instY'], p['plane'], inst=True))"
python -c "import controller as c; g=c.get_collision_grid(); print(g['baseX'], g['baseY'], g['size'], len(g['flags']), sum(1 for f in g['flags'] if f))"
```

Expected, in an instance: `inInstance` True and `instX != x`; the ground point `onScreen` True; a grid with a non-zero count. Then `instance_nav.walk_to` across one room (through a throwaway script, not a routine). At the Hunllef: `/npcs` entries with `overheadPrayer` in `RANGED/MAGIC/MELEE` (else record `overheadPrayerId` values for the three states), and `poll_events` via the MCP debug server shows one `PROJECTILE_SPAWNED` per attack and an `NPC_SPAWNED` for id 9039. Record each result in the spec's checklist section and in CLAUDE.md's bullet.

---

## Self-review

- **Spec coverage:** §1 instX/instY -> Tasks 1-2; §2 inst=true + all=true -> Tasks 4-5; §3 overhead prayer -> Task 3; §4 events -> Tasks 6-7; §5 instance_nav -> Tasks 8-10; §6 gauntlet_ids -> Task 11; error handling (empty means cannot tell, null prayer) -> Tasks 5, 9, 10 as written; testing -> every task; live checklist -> Task 12.
- **Placeholders:** none; every step has code. The one deliberately open datum (Dark Beast id) is stated as unknown with the way to get it.
- **Type consistency:** `InstanceCoords.instancePoint(Client, LocalPoint, int)` used identically in Tasks 1, 2, 6, 7; `sceneIndex` row-major-by-y in Tasks 1, 5, and `InstanceMap.flags_at` (`sy * size + sx`) in Task 9; `_hop_to(..., inst=)` signature matches between Task 10's travel change and `walk_to`; wrapper signatures in Task 8 match their call sites in Task 10's tests.
