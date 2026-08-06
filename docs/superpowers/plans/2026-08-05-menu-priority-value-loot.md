# MenuPriority Value-Matched Loot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `MenuPriority.java` force-prioritize the "Take" menu option for ground items that meet the configured `minLootValue` threshold, not just items on the explicit name whitelist -- fixing a confirmed live bug where value-matched loot pickup clicks could resolve to the wrong menu action and loop forever.

**Architecture:** `MenuPriority.apply()` already swaps a matching `MenuEntry` into the array's last slot (the slot a plain left-click always resolves to) for attack targets, whitelisted loot, target objects, and forced actions. This plan widens the loot half of that check to also match by GE price via `MenuEntry.getItemId()` + the already-injected `ItemManager`, reusing the exact same swap mechanism -- no new interaction paradigm, no Python changes.

**Tech Stack:** Java (RuneLite plugin API), Gradle. No JUnit tests exist for this logic anywhere in the codebase (the one `src/test` file is a manual dev launcher, not a unit test) -- verification here follows the codebase's actual established pattern: `./gradlew compileJava` for compile-correctness, plus a manual live check via that same dev launcher.

## Global Constraints

- No changes to `slayer.py` / any Python file -- the fix is entirely server-side in `MenuPriority.java`. The plain left-click `maybe_loot` already issues will resolve correctly once this lands.
- Value matching uses unit GE price (`itemManager.getItemPrice(itemId)`), not `price x quantity` -- `MenuEntry` has no quantity accessor. This is a deliberate, documented simplification (see spec), not a gap to fix in this plan.
- `MenuEntry.getItemId()` is a real method on `net.runelite.api.MenuEntry` (confirmed via `javap` against `runelite-api-1.12.33.jar`) -- use it directly, no text/name parsing for this check (unlike the existing `target`-based checks).

---

### Task 1: Thread ItemManager into MenuPriority and widen the loot-priority check

**Files:**
- Modify: `src/main/java/com/example/controller/MenuPriority.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:144`

**Interfaces:**
- Consumes: `net.runelite.client.game.ItemManager` (already injected as `itemManager` in `ControllerPlugin`, `src/main/java/com/example/controller/ControllerPlugin.java:44-45`), `net.runelite.api.MenuEntry.getItemId()` (RuneLite API, confirmed present).
- Produces: `MenuPriority.apply(Client, ControllerConfig, SlayerConfig, MenuOpened, ItemManager)` -- new 5-arg signature (was 4-arg). No other file calls `MenuPriority.apply`, confirmed by grep.

- [ ] **Step 1: Update the call site in ControllerPlugin.java**

In `src/main/java/com/example/controller/ControllerPlugin.java`, find line 144:

```java
		MenuPriority.apply(client, config, configManager.getConfig(SlayerConfig.class), event);
```

Replace with:

```java
		MenuPriority.apply(client, config, configManager.getConfig(SlayerConfig.class), event, itemManager);
```

- [ ] **Step 2: Add the ItemManager import to MenuPriority.java**

In `src/main/java/com/example/controller/MenuPriority.java`, find the import block:

```java
import net.runelite.api.Client;
import net.runelite.api.MenuEntry;
import net.runelite.api.events.MenuOpened;
import net.runelite.client.util.Text;
```

Replace with:

```java
import net.runelite.api.Client;
import net.runelite.api.MenuEntry;
import net.runelite.api.events.MenuOpened;
import net.runelite.client.game.ItemManager;
import net.runelite.client.util.Text;
```

- [ ] **Step 3: Widen the `apply` method signature and add the value-loot read**

In the same file, find:

```java
	static void apply(Client client, ControllerConfig config, SlayerConfig slayerConfig, MenuOpened event)
	{
		if (!config.forceClickPriority())
		{
			return;
		}

		MenuEntry[] entries = event.getMenuEntries();
		int lastIndex = entries.length - 1;
		if (lastIndex < 1)
		{
			return;
		}

		Set<String> targetNpcs = targetNpcsCache.get(slayerConfig.targetNpcNames());
		Set<String> lootItems = lootItemsCache.get(slayerConfig.lootWhitelist());
		Set<String> targetObjects = targetObjectsCache.get(config.targetObjectNames());
		Set<String> forcedActions = forcedActionsCache.get(config.forcedActions());
```

Replace with:

```java
	static void apply(Client client, ControllerConfig config, SlayerConfig slayerConfig, MenuOpened event, ItemManager itemManager)
	{
		if (!config.forceClickPriority())
		{
			return;
		}

		MenuEntry[] entries = event.getMenuEntries();
		int lastIndex = entries.length - 1;
		if (lastIndex < 1)
		{
			return;
		}

		Set<String> targetNpcs = targetNpcsCache.get(slayerConfig.targetNpcNames());
		Set<String> lootItems = lootItemsCache.get(slayerConfig.lootWhitelist());
		Set<String> targetObjects = targetObjectsCache.get(config.targetObjectNames());
		Set<String> forcedActions = forcedActionsCache.get(config.forcedActions());
		int minLootValue = slayerConfig.minLootValue();
```

- [ ] **Step 4: Widen the `wanted` condition to also match by value**

In the same file, find:

```java
			boolean wanted =
				("attack".equals(option) && targetNpcs.contains(target))
					|| ("take".equals(option) && lootItems.contains(target))
					|| (targetObjects.contains(target) && !"examine".equals(option) && !"cancel".equals(option))
					|| forcedActions.contains(option);
```

Replace with:

```java
			boolean wanted =
				("attack".equals(option) && targetNpcs.contains(target))
					|| ("take".equals(option) && (lootItems.contains(target) || meetsLootValue(itemManager, entry, minLootValue)))
					|| (targetObjects.contains(target) && !"examine".equals(option) && !"cancel".equals(option))
					|| forcedActions.contains(option);
```

- [ ] **Step 5: Add the `meetsLootValue` helper**

In the same file, find the `normalizedSet` method:

```java
	private static Set<String> normalizedSet(String commaSeparated)
	{
```

Insert this new method directly above it:

```java
	/** Value-based loot priority, alongside the name-whitelist check above -- covers
	 * maybe_loot's (slayer.py) totalValue >= minLootValue path, which has no name to match
	 * against. Uses MenuEntry.getItemId() directly (a real method on the RuneLite API) rather
	 * than parsing `target`, and checks unit GE price only, not price x quantity -- MenuEntry
	 * carries no quantity, and ground-item drops are effectively always qty=1 per entry in
	 * practice, so this is a deliberate simplification, not an oversight. */
	private static boolean meetsLootValue(ItemManager itemManager, MenuEntry entry, int minLootValue)
	{
		if (minLootValue <= 0)
		{
			return false;
		}
		int itemId = entry.getItemId();
		if (itemId <= 0)
		{
			return false;
		}
		return itemManager.getItemPrice(itemId) >= minLootValue;
	}

```

- [ ] **Step 6: Compile and verify**

Run: `./gradlew compileJava`
Expected: `BUILD SUCCESSFUL`. If it fails, the error will point at a typo in one of the snippets above (most likely a mismatched brace from Step 3/4's find-and-replace) -- fix and re-run before continuing.

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/controller/MenuPriority.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "$(cat <<'EOF'
Force-prioritize Take on value-matched loot, not just whitelisted items

MenuPriority only covered maybe_loot's name-whitelist path; value-matched
loot (minLootValue) got no menu-priority forcing at all, so a plain
left-click gambled on whatever the game put on top of the tile's menu --
confirmed root cause of a live stuck-loot-pickup bug.
EOF
)"
```

---

### Task 2: Manually verify the fix against a live client

**Files:** none (verification only, no changes)

**Interfaces:**
- Consumes: `MenuPriority.apply` from Task 1 (already wired into `ControllerPlugin`'s `MenuOpened` handler -- no new wiring needed here).
- Produces: nothing consumed by later tasks (this is the last task).

- [ ] **Step 1: Launch the dev RuneLite client with the plugins loaded**

This project's plugins run from source via the existing manual test launcher (`src/test/java/com/example/ExamplePluginTest.java`), not a packaged jar. From an IDE, run its `main` method (or equivalent Gradle run task if one exists in this project) to start a RuneLite client with `ControllerPlugin`, `SlayerPlugin`, and the rest already loaded.

- [ ] **Step 2: Enable Force Click Priority and set a loot value threshold**

In the RuneLite client, open Controller Settings and confirm/set: `forceClickPriority` (Force Click Priority) is on, and the Slayer panel's Min Loot Value is set to something a common drop on your current task will exceed (e.g. 500).

- [ ] **Step 3: Drop-test without running the bot**

Manually kill (or find) a monster whose drop would cross that value threshold but is NOT on your loot name whitelist. Right-click the resulting ground item yourself and confirm "Take" now appears as the top (bolded/first) option in the context menu -- this is the same reordering the bot's plain left-click will now land on.

- [ ] **Step 4: Run the slayer routine and confirm pickup succeeds**

From `ControllerAddon/`, run:

```bash
python controller.py --debug-log routines/slayer.py
```

Let it fight until a value-matched (non-whitelisted) item drops. Confirm in the console/log output that the `[loot] picking up X (worth Ngp)` line is followed by the item actually leaving the ground within a tick or two -- i.e. the same `[loot] picking up X` line does NOT repeat on consecutive ticks for the same item the way the original bug did. Let it run for a few minutes across at least 2-3 such drops before considering this verified.

- [ ] **Step 5: Stop the controller**

Press the configured panic key (or Ctrl+C in the terminal) to stop the bot once verification is complete.
