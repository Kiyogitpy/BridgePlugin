# Slayer Safespot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a player right-click the ground and choose "Mark Safespot" to save a tile into Slayer config, then have the slayer routine automatically walk back to that exact tile whenever it drifts away (looting, a monster's pull attack, etc.), so ranged/magic tasks can be fought safely from a fixed spot.

**Architecture:** A new Java-side `MenuEntryAdded` subscriber injects a "Mark Safespot" option into the ground's right-click menu (the same technique RuneLite's own Ground Markers plugin uses) and saves the clicked tile straight into `SlayerConfig` via `ConfigManager` when clicked -- no separate click-handling subscriber needed, since `MenuEntry.onClick(Consumer<MenuEntry>)` attaches the handler directly to the synthetic entry. `ConfigExporter` exports the saved tile to the Python bridge config; a new `maintain_safespot(ctx)` in `slayer.py`, run every tick, walks back to it whenever the player isn't standing on it.

**Tech Stack:** Java (RuneLite plugin API: `MenuEntryAdded`, `Client.createMenuEntry`, `WorldPoint.fromScene`, `ConfigManager.setConfiguration`), Python (existing `travel.walk_to`/`save_location` infrastructure).

## Global Constraints

- No new right-click "arming" step -- "Mark Safespot" is always available on any ground right-click menu, exactly like "Walk here" always is (per the approved design).
- Single tile storage, not per-task-category -- `SlayerConfig.safespotX/Y/plane`, overwritten on each new mark. No per-category map (explicitly out of scope in the design doc).
- `travel.walk_to`'s existing 3-tile `WALK_ARRIVAL_TOLERANCE` default must stay unchanged for its current callers (`banker.py`) -- the new tolerance parameter is additive, not a behavior change to existing call sites.
- Safespotting only constrains where the player stands, never which monster gets targeted -- no changes to `pick_target`/`engage_target`'s target-selection logic.

---

### Task 1: Java-side safespot marking, config storage, and export

**Files:**
- Create: `src/main/java/com/example/controller/SafespotMarker.java`
- Modify: `src/main/java/com/example/controller/SlayerConfig.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`
- Modify: `src/main/java/com/example/controller/ConfigExporter.java`

**Interfaces:**
- Consumes: `net.runelite.api.events.MenuEntryAdded` (RuneLite API -- fires once per menu entry as the game builds a right-click menu; `getOption()`, `getTarget()`, `getActionParam0()`/`getActionParam1()` (scene X/Y for a `WALK` entry) are all real methods, confirmed via `javap` against `runelite-api-1.12.33.jar`). `net.runelite.client.config.ConfigManager.setConfiguration(String group, String key, T value)` (confirmed generic 3-arg overload exists).
- Produces: `SlayerConfig.safespotEnabled()` (boolean), `SlayerConfig.safespotX()`/`safespotY()`/`safespotPlane()` (ints, `0` sentinel = "never marked"). `ConfigExporter`'s exported bridge config gets a new top-level `"safespot"` key: `{"enabled": bool, "marked": bool, "x": int, "y": int, "plane": int}` -- Task 2's Python code consumes exactly this shape.

- [ ] **Step 1: Add the Safespot section and four config fields to SlayerConfig**

In `src/main/java/com/example/controller/SlayerConfig.java`, find:

```java
	@ConfigSection(name = "Travel", description = "Chosen location for slayer task categories with more than one known spot", position = 10)
	String travelSection = "travel";
```

Replace with:

```java
	@ConfigSection(name = "Travel", description = "Chosen location for slayer task categories with more than one known spot", position = 10)
	String travelSection = "travel";

	@ConfigSection(name = "Safespot", description = "Fight from a fixed tile a melee monster can't reach", position = 11)
	String safespotSection = "safespot";
```

Then find the closing brace at the end of the file:

```java
	default String taskLocationChoices()
	{
		return "";
	}
}
```

Replace with:

```java
	default String taskLocationChoices()
	{
		return "";
	}

	// --- Safespot ---

	@ConfigItem(
		keyName = "safespotEnabled",
		name = "Use Safespot",
		description = "Walk back to the marked safespot tile whenever you drift off it (looting, a monster's " +
			"pull attack, etc). Only useful for Ranged/Magic tasks -- melee requires standing adjacent to the " +
			"target, which defeats the point.",
		section = safespotSection,
		position = 0
	)
	default boolean safespotEnabled()
	{
		return false;
	}

	@ConfigItem(
		keyName = "safespotX",
		name = "Safespot X",
		description = "Managed by right-clicking the ground and choosing \"Mark Safespot\" -- 0 means no tile " +
			"has been marked yet.",
		section = safespotSection,
		position = 1
	)
	default int safespotX()
	{
		return 0;
	}

	@ConfigItem(
		keyName = "safespotY",
		name = "Safespot Y",
		description = "Managed by right-clicking the ground and choosing \"Mark Safespot\".",
		section = safespotSection,
		position = 2
	)
	default int safespotY()
	{
		return 0;
	}

	@ConfigItem(
		keyName = "safespotPlane",
		name = "Safespot Plane",
		description = "Managed by right-clicking the ground and choosing \"Mark Safespot\".",
		section = safespotSection,
		position = 3
	)
	default int safespotPlane()
	{
		return 0;
	}
}
```

- [ ] **Step 2: Create SafespotMarker.java**

Create `src/main/java/com/example/controller/SafespotMarker.java`:

```java
package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.MenuAction;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.MenuEntryAdded;
import net.runelite.client.config.ConfigManager;

/**
 * Injects a "Mark Safespot" option into the ground's right-click menu, alongside the game's own
 * "Walk here" -- the same technique RuneLite's own Ground Markers plugin uses to add
 * tile-targeted options. No arming/mode toggle: the option is always available, exactly like
 * "Walk here" always is, and it never fires on a normal walk-click since it's a distinct entry
 * the player has to deliberately select.
 *
 * Scene coordinates are captured from the "Walk here" entry that triggered this addition (not
 * re-read from the synthetic entry at click time) and closed over in the onClick lambda -- this
 * sidesteps any question of whether a client.createMenuEntry(...) entry carries the same params
 * back out the way the original one did.
 */
final class SafespotMarker
{
	private SafespotMarker()
	{
	}

	static void apply(Client client, ConfigManager configManager, MenuEntryAdded event)
	{
		if (!"Walk here".equals(event.getOption()))
		{
			return;
		}

		int sceneX = event.getActionParam0();
		int sceneY = event.getActionParam1();
		int plane = client.getPlane();

		client.createMenuEntry(-1)
			.setOption("Mark Safespot")
			.setTarget(event.getTarget())
			.setType(MenuAction.RUNELITE)
			.onClick(entry -> {
				WorldPoint point = WorldPoint.fromScene(client, sceneX, sceneY, plane);
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotX", point.getX());
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotY", point.getY());
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotPlane", point.getPlane());
			});
	}
}
```

**Note for the next task's live-verification step:** `client.createMenuEntry(-1)`'s exact insertion position (top vs. relative to "Walk here") isn't verifiable from static analysis alone -- Task 3 confirms this live and this step can be revisited if the entry lands somewhere confusing (e.g. buried under a long menu instead of near the top).

- [ ] **Step 3: Wire the MenuEntryAdded subscriber into ControllerPlugin**

In `src/main/java/com/example/controller/ControllerPlugin.java`, find:

```java
	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		// SlayerConfig isn't @Inject-ed here -- its @Provides binding lives on SlayerPlugin
		// (needed there for RuneLite to associate it with that plugin's own config screen), and
		// a dependent plugin's binding isn't visible back to the plugin it depends on. Fetching
		// it straight from ConfigManager sidesteps needing a Guice binding here at all.
		MenuPriority.apply(client, config, configManager.getConfig(SlayerConfig.class), event, itemManager);
	}
```

Replace with:

```java
	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		// SlayerConfig isn't @Inject-ed here -- its @Provides binding lives on SlayerPlugin
		// (needed there for RuneLite to associate it with that plugin's own config screen), and
		// a dependent plugin's binding isn't visible back to the plugin it depends on. Fetching
		// it straight from ConfigManager sidesteps needing a Guice binding here at all.
		MenuPriority.apply(client, config, configManager.getConfig(SlayerConfig.class), event, itemManager);
	}

	@Subscribe
	public void onMenuEntryAdded(MenuEntryAdded event)
	{
		SafespotMarker.apply(client, configManager, event);
	}
```

Then find the import block at the top of the same file:

```java
import net.runelite.api.*;
import net.runelite.api.events.*;
```

These wildcard imports already cover `MenuEntryAdded` (`net.runelite.api.events.*`) -- no new import line needed. Skip if already present (it is, per the existing file).

- [ ] **Step 4: Export the safespot block in ConfigExporter**

In `src/main/java/com/example/controller/ConfigExporter.java`, find:

```java
		data.put("taskLocations", resolveTaskLocations(config));
```

Replace with:

```java
		Map<String, Object> safespot = new LinkedHashMap<>();
		boolean safespotMarked = config.safespotEnabled() && (config.safespotX() != 0 || config.safespotY() != 0);
		safespot.put("enabled", config.safespotEnabled());
		safespot.put("marked", safespotMarked);
		safespot.put("x", config.safespotX());
		safespot.put("y", config.safespotY());
		safespot.put("plane", config.safespotPlane());
		data.put("safespot", safespot);

		data.put("taskLocations", resolveTaskLocations(config));
```

**Note:** `safespotEnabled()`/`safespotX()`/etc. live on `SlayerConfig` (the `config` parameter of `ConfigExporter.export`, per this file's existing signature `export(Client client, ControllerConfig controllerConfig, SlayerConfig config)`) -- use `config.safespotEnabled()`, never `controllerConfig.safespotEnabled()` (that field doesn't exist on `ControllerConfig` at all; see the existing `foodName`/`prayerPotionName` lines earlier in this same method for the established convention of which parameter holds which config).

- [ ] **Step 5: Compile and verify**

Run: `./gradlew compileJava`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/example/controller/SafespotMarker.java src/main/java/com/example/controller/SlayerConfig.java src/main/java/com/example/controller/ControllerPlugin.java src/main/java/com/example/controller/ConfigExporter.java
git commit -m "$(cat <<'EOF'
Add right-click Mark Safespot menu option, persist and export the tile

Injects a "Mark Safespot" entry alongside the ground's "Walk here" via
MenuEntryAdded, saves the clicked tile into SlayerConfig, and exports it
to the Python bridge config as a new top-level "safespot" block.
EOF
)"
```

---

### Task 2: Python-side safespot anchoring

**Files:**
- Modify: `ControllerAddon/travel.py`
- Modify: `ControllerAddon/routines/slayer.py`

**Interfaces:**
- Consumes: bridge config's `"safespot"` key from Task 1 -- `{"enabled": bool, "marked": bool, "x": int, "y": int, "plane": int}`. `controller.get_player()` (existing, returns dict with `"x"`/`"y"`/`"plane"`).
- Produces: `travel.walk_to(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool` (widened signature -- existing 2-arg call sites in `banker.py` are unaffected since the new parameter has a default equal to the previous hardcoded behavior). `slayer.maintain_safespot(ctx) -> None`, called from `tick()`.

- [ ] **Step 1: Add an optional tolerance parameter to travel.py's arrival-check chain**

In `ControllerAddon/travel.py`, find:

```python
def _within_tolerance(x1: float, y1: float, x2: float, y2: float) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= WALK_ARRIVAL_TOLERANCE
```

Replace with:

```python
def _within_tolerance(x1: float, y1: float, x2: float, y2: float, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= tolerance
```

Then find:

```python
def walk_to(ctx, target: dict) -> bool:
    """Get back to a location from save_location(). Tries a computed route
    from Shortest Path first, falls back to a direct greedy walk if that's
    unavailable or fails partway. True once within WALK_ARRIVAL_TOLERANCE
    tiles of the target."""
    steps = _request_computed_path(target)
    if steps:
        debug_log.log("travel", f"following a {len(steps)}-tile route from Shortest Path")
        if _walk_along_path(ctx, steps, target):
            return True
        debug_log.log("travel", "following the computed route failed partway, falling back to a direct walk")
    else:
        debug_log.log("travel", "Shortest Path unavailable -- walking straight toward the target instead")

    return _walk_greedy(ctx, target)
```

Replace with:

```python
def walk_to(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    """Get back to a location from save_location(). Tries a computed route
    from Shortest Path first, falls back to a direct greedy walk if that's
    unavailable or fails partway. True once within `tolerance` tiles of the
    target (defaults to WALK_ARRIVAL_TOLERANCE; callers needing a tighter
    final arrival -- e.g. safespot anchoring, where 3 tiles of slack could
    still be inside a melee monster's reach -- can pass a smaller value)."""
    steps = _request_computed_path(target)
    if steps:
        debug_log.log("travel", f"following a {len(steps)}-tile route from Shortest Path")
        if _walk_along_path(ctx, steps, target, tolerance):
            return True
        debug_log.log("travel", "following the computed route failed partway, falling back to a direct walk")
    else:
        debug_log.log("travel", "Shortest Path unavailable -- walking straight toward the target instead")

    return _walk_greedy(ctx, target, tolerance)
```

Then find:

```python
def _walk_along_path(ctx, steps: list, target: dict) -> bool:
```

Replace with:

```python
def _walk_along_path(ctx, steps: list, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
```

A few lines below in the same function, find:

```python
        if _within_tolerance(px, py, target["x"], target["y"]):
            return True
```

Replace with:

```python
        if _within_tolerance(px, py, target["x"], target["y"], tolerance):
            return True
```

Then find:

```python
def _walk_greedy(ctx, target: dict) -> bool:
    """Straight-line walk toward target with no obstacle awareness -- the
    fallback when Shortest Path isn't available. True once within
    WALK_ARRIVAL_TOLERANCE tiles of the target, False if it gives up (different
    plane, hop point off the minimap, or WALK_MAX_HOPS exhausted)."""
```

Replace with:

```python
def _walk_greedy(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    """Straight-line walk toward target with no obstacle awareness -- the
    fallback when Shortest Path isn't available. True once within
    `tolerance` tiles of the target, False if it gives up (different
    plane, hop point off the minimap, or WALK_MAX_HOPS exhausted)."""
```

A few lines below in the same function, find:

```python
        if _within_tolerance(px, py, target_x, target_y):
            return True
```

Replace with:

```python
        if _within_tolerance(px, py, target_x, target_y, tolerance):
            return True
```

**Note:** `_hop_to`'s own intermediate-hop arrival check (inside its wait loop) and `_use_transport`'s post-transport wait loop both call `_within_tolerance` with only 2 coordinate pairs (no tolerance argument) -- leave those two call sites exactly as they are. They govern "did this one intermediate click resolve," not final arrival, so they should keep using the default `WALK_ARRIVAL_TOLERANCE` regardless of what tolerance the overall `walk_to` call was given.

- [ ] **Step 2: Verify travel.py still compiles**

Run: `python -m py_compile ControllerAddon/travel.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Add maintain_safespot to slayer.py**

In `ControllerAddon/routines/slayer.py`, find:

```python
ENGAGE_STUCK_MAX_TICKS = 5
```

(this is immediately followed by the `HERB_SACK_BYPASS_NAMES` block already in the file -- insert the new constant and function *after* `SEED_BOX_BYPASS_NAMES`'s closing `}`, immediately before `def resolve_combat_prayer`). Find:

```python
SEED_BOX_BYPASS_NAMES = {
    "guam seed", "marrentill seed", "tarromin seed", "harralander seed", "ranarr seed",
    "toadflax seed", "irit seed", "avantoe seed", "kwuarm seed", "huasca seed",
    "snapdragon seed", "cadantine seed", "lantadyme seed", "dwarf weed seed", "torstol seed",
    "acorn", "willow seed", "maple seed", "yew seed", "magic seed",
}


def resolve_combat_prayer(combat_prayer: str, prayer_level):
```

Replace with:

```python
SEED_BOX_BYPASS_NAMES = {
    "guam seed", "marrentill seed", "tarromin seed", "harralander seed", "ranarr seed",
    "toadflax seed", "irit seed", "avantoe seed", "kwuarm seed", "huasca seed",
    "snapdragon seed", "cadantine seed", "lantadyme seed", "dwarf weed seed", "torstol seed",
    "acorn", "willow seed", "maple seed", "yew seed", "magic seed",
}

# maintain_safespot walks back to the marked safespot tile once the player is more than this
# many tiles off of it -- 0 means "must be standing on the exact tile", matching the point of a
# safespot (travel.walk_to's own default 3-tile WALK_ARRIVAL_TOLERANCE is tuned for bank-trip-scale
# travel and could still leave the player inside a melee monster's reach).
SAFESPOT_RETURN_TOLERANCE = 0


def maintain_safespot(ctx):
    """Walks back to the marked safespot tile whenever the player has drifted off of it -- e.g.
    after stepping out to loot something, or getting dragged by a monster's pull attack. Runs
    every tick regardless of combat state (not just when picking a fresh target), since that's
    what actually catches a mid-fight drag rather than just anchoring at the start of each
    fight."""
    config = ctx.state.config
    safespot = config.get("safespot", {})
    if not safespot.get("enabled") or not safespot.get("marked"):
        return

    player = controller.get_player()
    if player.get("x") is None:
        return

    tile = {"x": safespot["x"], "y": safespot["y"], "plane": safespot["plane"]}
    if player.get("x") == tile["x"] and player.get("y") == tile["y"] and player.get("plane") == tile["plane"]:
        return

    debug_log.log("safespot", f"drifted from safespot (at ({player.get('x')},{player.get('y')}), "
                               f"safespot is ({tile['x']},{tile['y']})) -- returning")
    travel.walk_to(ctx, tile, tolerance=SAFESPOT_RETURN_TOLERANCE)


def resolve_combat_prayer(combat_prayer: str, prayer_level):
```

**Note:** `slayer.py` already has `import travel` at the top of the file (line 19, alongside `import banker`/`import controller`/`import debug_log`/`import settings`) -- no new import needed to call `travel.walk_to`.

- [ ] **Step 4: Wire maintain_safespot into tick()**

In `ControllerAddon/routines/slayer.py`, find:

```python
    if config.get("loot", {}).get("enabled"):
        maybe_loot(ctx)

    engage_target(ctx, combat)
```

Replace with:

```python
    if config.get("loot", {}).get("enabled"):
        maybe_loot(ctx)

    maintain_safespot(ctx)
    engage_target(ctx, combat)
```

- [ ] **Step 5: Verify slayer.py still compiles**

Run: `python -m py_compile ControllerAddon/routines/slayer.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/travel.py ControllerAddon/routines/slayer.py
git commit -m "$(cat <<'EOF'
Add maintain_safespot: walk back to the marked tile whenever drifted

Runs every tick (not just before picking a new target) so a mid-fight
drag off the safespot -- from looting or a monster's pull attack -- gets
corrected too, not just the initial approach.
EOF
)"
```

---

### Task 3: Manually verify the full feature against a live client

**Files:** none (verification only, no changes)

**Interfaces:**
- Consumes: `SafespotMarker`/`ConfigExporter` from Task 1, `maintain_safespot`/`walk_to` from Task 2 -- already wired together, nothing new to connect here.
- Produces: nothing (last task).

- [ ] **Step 1: Launch the dev RuneLite client with the plugins loaded**

Run the project's manual test launcher (`src/test/java/com/example/ExamplePluginTest.java`'s `main` method, or the Gradle `run` task if this project has one configured) to start a RuneLite client with `ControllerPlugin`/`SlayerPlugin` loaded from the freshly compiled classes.

- [ ] **Step 2: Confirm the menu entry appears and lands somewhere sensible**

Right-click any ground tile near your character. Confirm "Mark Safespot" appears in the menu alongside "Walk here". If it's buried at the bottom of a long menu instead of appearing near the top, revisit `SafespotMarker.java`'s `client.createMenuEntry(-1)` call (per Task 1 Step 2's note) -- try a different index/insertion approach and re-verify.

- [ ] **Step 3: Mark a safespot and confirm it persists**

Stand at a real safespot for a Ranged/Magic-appropriate task (a tile where a melee monster can't reach you but you have line of sight to attack it), right-click the ground under your own feet (or the safespot tile itself), and click "Mark Safespot". Open Controller Settings -> Slayer -> Safespot section and confirm Safespot X/Y/Plane show non-zero values matching your position. Enable "Use Safespot".

- [ ] **Step 4: Run the slayer routine and confirm anchoring**

From `ControllerAddon/`, run:

```bash
python controller.py --debug-log routines/slayer.py
```

With a target attackable from the marked tile, confirm the bot fights without moving off the tile under normal conditions. Manually walk the character a few tiles away (simulating a drag/drift) while the bot is running -- confirm a `[safespot] drifted from safespot ... -- returning` log line appears and the character walks back to the exact marked tile.

- [ ] **Step 5: Confirm looting still returns to the safespot**

With loot enabled and a lootable item within walking distance of the safespot, let the bot step out to grab it. Confirm it returns to the exact marked tile afterward (same log line as Step 4) rather than staying wherever the loot pickup left it.

- [ ] **Step 6: Stop the controller**

Press the configured panic key (or Ctrl+C in the terminal) to stop the bot once verification is complete.
