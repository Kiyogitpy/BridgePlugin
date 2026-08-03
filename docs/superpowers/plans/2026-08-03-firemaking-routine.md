# Firemaking Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `routines/firemaking.py` bot routine that burns a configured log type with a tinderbox (click tinderbox, click log, repeat), restocking from a bank when configured, wired into the same live-picker/settings-panel infrastructure every other routine in this project uses.

**Architecture:** Follows the `routines/mining.py` shape exactly (poll `get_combat().isIdle`, act, repeat) but is the project's first routine to click two inventory items in sequence (tinderbox, then log) rather than one world object/NPC/menu button. A new `FiremakingConfig`/`FiremakingPlugin` pair (same pattern as `MiningConfig`/`MiningPlugin`) exposes settings over the bridge at `/firemaking-config`, fully wired the way `TormentedDemonConfig`/`tormented_demon.py` are (not the stale `mining.py`/`gemstone_crab.py` shortcut that never reads its own endpoint). Banking reuses `banker.py` wholesale, the same way `slayer.py` does.

**Tech Stack:** Java 11 (RuneLite plugin API, `build.gradle`), Python 3 (bridge client). No unit test framework exists for either side of this codebase — Java changes are verified by `./gradlew.bat compileJava`, Python changes by `python -m py_compile`, and routine behavior is verified live against the running bridge + game, same as every other routine in this project (see `travel.py`'s "ASSUMPTIONS TO VERIFY IN-GAME" comments for the established convention).

## Global Constraints

- Java target: source/target compatibility 11 (`build.gradle`: `options.release.set(11)`).
- Every `@ConfigItem` default must be a concrete value RuneLite's config UI can render — never `null` for a primitive-backed item.
- Every new `@PluginDescriptor`-annotated class needs an explicit entry in `src/test/java/com/example/ExamplePluginTest.java`'s `loadBuiltin(...)` call, or the dev harness silently never starts it (no error, just absent from the plugin list) — this bit every prior Controller-family plugin (`MiningPlugin`, `GemstoneCrabPlugin`, `TormentedDemonPlugin`) and is easy to forget.
- Follow the resolved config-fallback pattern from `routines/tormented_demon.py:126-135` (`get_config` calls the bridge endpoint, falls back to `DEFAULT_CONFIG` only if the plugin isn't loaded/running) — not the stale `mining.py`/`gemstone_crab.py` pattern that ignores its own bridge endpoint entirely.
- Substring item-name matching (`name.lower() in item_name.lower()`) is the established convention throughout this codebase (`banker.py`, `travel.py`) for matching configured item names against inventory contents — keep using it for consistency, but note its known sharp edge below.

### Firemaking mechanics reference (from the local OSRS wiki mirror)

Sourced from `osrs-wiki-data-master/raw/F/Firemaking.txt` and `raw/F/Fire.txt` (this repo's local wiki dump, indexed at `osrs-wiki-data-master/index.tsv` — not to be confused with the source-comment-only "wiki" references in `SlayerLocations.java`/`SlayerMonsters.java`, which just cite the live wiki as a data source, not a local mirror):

- **How lighting works** (`Firemaking.txt:36-40`): use the tinderbox on the log, or the log on the tinderbox — either resolves to "drop the log and light it." No right-click submenu is needed for either item; tinderbox's plain left-click default action is "Use" (it has no more-prioritized default action like Wield/Eat/Drink to compete with), same reasoning as this project's other tool items. **Verify live**: if a plain left-click on the tinderbox doesn't resolve to Use, add it to Controller Settings → Clicking → Forced Actions, same escape hatch `travel.py` documents for the ring of dueling and royal seed pod.
- **Success chance below level 43** (`Firemaking.txt:384-389`): crowdsourced at 65/256 (~25%) at level 1, rising linearly to 256/256 (100%) at level 43+, regardless of log tier. A failed attempt shows no lighting animation and does not consume the log — the player can simply try again immediately. This is why the routine below doesn't need explicit retry logic: if the two clicks didn't start an animation, `get_combat().isIdle` is still `True` on the very next `tick()`, so the same two clicks just fire again.
- **Burn duration** (`Fire.txt:56`): every fire lasts 60–119 real seconds regardless of log type (a fire's duration is not affected by Firemaking level or log tier) — irrelevant to this routine, which never interacts with the fire itself after lighting it, only with the player's own light-then-step-away animation via `isIdle`.
- **Post-light movement** (`Firemaking.txt:42`, `Fire.txt:60`): after lighting, the player auto-walks one step west (or east/south/north if blocked). This is why the routine waits for `isIdle` (not just "animation finished") before its next click — the step-away is a second, brief `isMoving` window that `get_combat().isIdle` already accounts for (`isIdle = !isMoving && animation == -1 && target == null`, see `CombatProvider.java:106-107`).
- **"Power firemaking"** (`Fire.txt:62`): a real player can queue the next light attempt before finishing the walk-away step, for higher xp/hour. **Deliberately not implemented here** — waiting for full `isIdle` between logs is simpler and safer, matching the "pretty simple" scope of this feature. Worth a future enhancement, not part of this plan.
- **Multiplayer collision** (`Fire.txt:64`): if another player's fire is already lit on the target tile, the routine's log is just dropped, unlit. Not specifically handled — it looks identical to a normal failed-lighting roll (no animation, log gone from inventory next check... actually the log **would** be consumed in this specific case, unlike a failed roll) and is rare enough in most training spots to not warrant special-casing in a "pretty simple" routine.
- **Level requirements and XP** (`Firemaking.txt:57-143`, full table): Logs/Achey tree logs = level 1 (40xp), Oak = 15 (60xp), Willow = 30 (90xp), Teak = 35 (105xp), Jatoba = 40 (120xp), Arctic pine = 42 (125xp), Maple = 45 (135xp), Mahogany = 50 (157.5xp), Yew = 60 (202.5xp), Blisterwood = 62 (96xp), Camphor = 66 (180xp), Magic = 75 (303.8xp), Ironwood = 80 (220.5xp), Redwood = 90 (350xp), Rosewood = 92 (268xp). Used below only for a one-time under-level sanity check, not enforced during the burn loop itself.
- **Tinderbox item ID** 590, **normal Logs item ID** 1511 (`Tinderbox.txt:19`, `Logs.txt:20`) — not needed by the routine itself (everything goes by name over the bridge, same as the rest of this codebase), noted here only for reference.

**Known sharp edge to flag, not fix** (out of scope for "pretty simple"): substring matching means a `logName` of `"Logs"` will also match inventory items like `"Achey tree logs"` if both are present, since `"logs" in "achey tree logs"` is `True`. This is the same limitation every existing substring-matching call in this codebase already has (`banker.py`'s `keepItems`, `withdraw_loadout`); not a new problem introduced here.

---

### Task 1: `FiremakingConfig`/`FiremakingPlugin` — settings panel + bridge endpoint

**Files:**
- Create: `src/main/java/com/example/controller/FiremakingConfig.java`
- Create: `src/main/java/com/example/controller/FiremakingPlugin.java`

**Interfaces:**
- Consumes: `ControllerPlugin.getServer()` (existing, see `MiningPlugin.java:36` for the identical call), `ConfigExporter` is NOT used here (no comma-list fields needed, unlike Mining's `rockNames`).
- Produces: `GET /firemaking-config` returning `{"enabled": bool, "logName": str, "tinderboxName": str, "bank": {"enabled": bool, "teleportFallback": bool, "keepItems": [str], "withdraw": [{"name": str, "quantity": int}]}, "breaks": {...same shape as MiningPlugin's breaks export...}, "safety": {"stopOnDeath": bool, "maxRuntimeHours": int}}` — this exact shape is what Task 4's `routines/firemaking.py` reads.

- [ ] **Step 1: Create `FiremakingConfig.java`**

```java
package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

@ConfigGroup(FiremakingConfig.GROUP)
public interface FiremakingConfig extends Config
{
	String GROUP = "controllerFiremaking";

	@ConfigSection(name = "General", description = "Which log to burn", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Banking", description = "Restock logs from a bank when out", position = 1)
	String bankSection = "bank";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule", position = 2)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits", position = 3)
	String safetySection = "safety";

	// --- General ---

	@ConfigItem(
		keyName = "firemakingEnabled",
		name = "Firemaking Enabled",
		description = "Secondary switch on top of launching controller.py with routines/firemaking.py.",
		section = generalSection,
		position = 0
	)
	default boolean firemakingEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "logName",
		name = "Log Name",
		description = "Exact inventory item name of the log to burn (e.g. \"Willow logs\").",
		section = generalSection,
		position = 1
	)
	default String logName()
	{
		return "Willow logs";
	}

	@ConfigItem(
		keyName = "tinderboxName",
		name = "Tinderbox Name",
		description = "Exact inventory item name of the tinderbox.",
		section = generalSection,
		position = 2
	)
	default String tinderboxName()
	{
		return "Tinderbox";
	}

	// --- Banking ---

	@ConfigItem(
		keyName = "bankEnabled",
		name = "Enable Banking",
		description = "Bank for more logs when out instead of just stopping.",
		section = bankSection,
		position = 0
	)
	default boolean bankEnabled()
	{
		return false;
	}

	@ConfigItem(
		keyName = "bankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the ring of dueling to Ferox Enclave when no bank is within walking distance.",
		section = bankSection,
		position = 1
	)
	default boolean bankTeleportFallback()
	{
		return false;
	}

	@ConfigItem(
		keyName = "bankWithdrawQuantity",
		name = "Logs Per Trip",
		description = "How many logs to withdraw per bank trip (27 leaves exactly one slot free for the tinderbox in a 28-slot inventory).",
		section = bankSection,
		position = 2
	)
	@Range(min = 1, max = 27)
	default int bankWithdrawQuantity()
	{
		return 27;
	}

	// --- Breaks ---

	@ConfigItem(
		keyName = "takeBreaks",
		name = "Take Breaks",
		description = "Periodically pause for human-like breaks.",
		section = breaksSection,
		position = 0
	)
	default boolean takeBreaks()
	{
		return true;
	}

	@ConfigItem(
		keyName = "breakEveryMinMinutes",
		name = "Break Every (min minutes)",
		description = "Minimum play time before a break.",
		section = breaksSection,
		position = 1
	)
	@Range(min = 5, max = 300)
	default int breakEveryMinMinutes()
	{
		return 45;
	}

	@ConfigItem(
		keyName = "breakEveryMaxMinutes",
		name = "Break Every (max minutes)",
		description = "Maximum play time before a break.",
		section = breaksSection,
		position = 2
	)
	@Range(min = 5, max = 300)
	default int breakEveryMaxMinutes()
	{
		return 75;
	}

	@ConfigItem(
		keyName = "breakDurationMinMinutes",
		name = "Break Duration (min minutes)",
		description = "Minimum break length.",
		section = breaksSection,
		position = 3
	)
	@Range(min = 1, max = 60)
	default int breakDurationMinMinutes()
	{
		return 3;
	}

	@ConfigItem(
		keyName = "breakDurationMaxMinutes",
		name = "Break Duration (max minutes)",
		description = "Maximum break length.",
		section = breaksSection,
		position = 4
	)
	@Range(min = 1, max = 60)
	default int breakDurationMaxMinutes()
	{
		return 12;
	}

	@ConfigItem(
		keyName = "microBreaks",
		name = "Micro Breaks",
		description = "Take short 5-30 second pauses between actions for realism.",
		section = breaksSection,
		position = 5
	)
	default boolean microBreaks()
	{
		return true;
	}

	// --- Safety ---

	@ConfigItem(
		keyName = "stopOnDeath",
		name = "Stop On Death",
		description = "Stop the controller after dying.",
		section = safetySection,
		position = 0
	)
	default boolean stopOnDeath()
	{
		return true;
	}

	@ConfigItem(
		keyName = "maxRuntimeHours",
		name = "Max Runtime (hours)",
		description = "Auto-stop after this many hours (0 = unlimited).",
		section = safetySection,
		position = 1
	)
	@Range(min = 0, max = 24)
	default int maxRuntimeHours()
	{
		return 0;
	}
}
```

- [ ] **Step 2: Create `FiremakingPlugin.java`**

```java
package com.example.controller;

import com.google.inject.Provides;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDependency;
import net.runelite.client.plugins.PluginDescriptor;

import javax.inject.Inject;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Same pattern as MiningPlugin -- see its header. Registers routines/firemaking.py's
 * config under /firemaking-config on ControllerPlugin's shared bridge server.
 */
@PluginDescriptor(
	name = "* Controller - Firemaking",
	description = "Firemaking routine settings for the external controller -- see the main HttpBridge/Controller plugin for Profile selection.",
	tags = {"controller", "config", "firemaking", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class FiremakingPlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private FiremakingConfig config;

	@Override
	protected void startUp()
	{
		controllerPlugin.getServer().register("/firemaking-config", () -> export(config));
	}

	@Override
	protected void shutDown()
	{
	}

	@Provides
	FiremakingConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(FiremakingConfig.class);
	}

	private static Map<String, Object> export(FiremakingConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.firemakingEnabled());
		data.put("logName", config.logName());
		data.put("tinderboxName", config.tinderboxName());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("enabled", config.bankEnabled());
		bank.put("teleportFallback", config.bankTeleportFallback());
		bank.put("keepItems", Collections.singletonList(config.tinderboxName()));

		List<Map<String, Object>> withdraw = new ArrayList<>();
		Map<String, Object> entry = new LinkedHashMap<>();
		entry.put("name", config.logName());
		entry.put("quantity", config.bankWithdrawQuantity());
		withdraw.add(entry);
		bank.put("withdraw", withdraw);
		data.put("bank", bank);

		Map<String, Object> breaks = new LinkedHashMap<>();
		breaks.put("enabled", config.takeBreaks());
		breaks.put("everyMinMinutes", config.breakEveryMinMinutes());
		breaks.put("everyMaxMinutes", config.breakEveryMaxMinutes());
		breaks.put("durationMinMinutes", config.breakDurationMinMinutes());
		breaks.put("durationMaxMinutes", config.breakDurationMaxMinutes());
		breaks.put("microBreaks", config.microBreaks());
		data.put("breaks", breaks);

		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", config.stopOnDeath());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		data.put("safety", safety);

		return data;
	}
}
```

- [ ] **Step 3: Compile check**

Run: `.\gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`, no errors referencing `FiremakingConfig` or `FiremakingPlugin`.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/controller/FiremakingConfig.java src/main/java/com/example/controller/FiremakingPlugin.java
git commit -m "Add Firemaking settings panel + /firemaking-config bridge endpoint"
```

---

### Task 2: Wire the new plugin into the routine picker and dev harness

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `src/test/java/com/example/ExamplePluginTest.java`

**Interfaces:**
- Consumes: `FiremakingPlugin.class` from Task 1.
- Produces: `RoutineChoice.FIREMAKING` enum constant, whose `.name()` (`"FIREMAKING"`) is the exact string Task 3's `ROUTINE_FILES` map key must match.

- [ ] **Step 1: Add `FIREMAKING` to `RoutineChoice.java`**

In `src/main/java/com/example/controller/RoutineChoice.java`, change:

```java
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon");
```

to:

```java
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon"),
	FIREMAKING("Firemaking");
```

- [ ] **Step 2: Register `FiremakingPlugin` in the dev harness**

In `src/test/java/com/example/ExamplePluginTest.java`, add the import and the `loadBuiltin(...)` entry:

```java
import com.example.controller.ControllerPlugin;
import com.example.controller.FiremakingPlugin;
import com.example.controller.GemstoneCrabPlugin;
import com.example.controller.MiningPlugin;
import com.example.controller.SlayerPlugin;
import com.example.controller.TormentedDemonPlugin;
import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;

public class ExamplePluginTest
{
	public static void main(String[] args) throws Exception
	{
		// This dev harness only loads whatever's explicitly listed here -- it does not scan for
		// other @PluginDescriptor classes, so every Controller-family plugin needs its own entry
		// or it silently never starts (no error, just absent from the plugin list).
		ExternalPluginManager.loadBuiltin(
			ControllerPlugin.class,
			SlayerPlugin.class,
			MiningPlugin.class,
			GemstoneCrabPlugin.class,
			TormentedDemonPlugin.class,
			FiremakingPlugin.class
		);
		RuneLite.main(args);
	}
}
```

- [ ] **Step 3: Compile check**

Run: `.\gradlew.bat compileJava .\gradlew.bat compileTestJava`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/controller/RoutineChoice.java src/test/java/com/example/ExamplePluginTest.java
git commit -m "Register Firemaking in RoutineChoice and the dev harness"
```

---

### Task 3: `controller.py` bridge helpers + routine registration

**Files:**
- Modify: `ControllerAddon/controller.py:161-163` (next to `get_tormented_demon_config`)
- Modify: `ControllerAddon/controller.py:1067-1072` (`ROUTINE_FILES`)

**Interfaces:**
- Produces: `controller.get_firemaking_config() -> dict | None` (mirrors `get_tormented_demon_config`), `controller.get_skills() -> dict` (new — no routine has needed per-skill levels before this).
- Consumes: `api_get(path, params=None, timeout=0.3)` (existing, `controller.py:100`).

- [ ] **Step 1: Add `get_firemaking_config()` and `get_skills()`**

In `ControllerAddon/controller.py`, immediately after `get_tormented_demon_config` (currently lines 161-162):

```python
def get_tormented_demon_config():
    return api_get("/tormented-demon-config")


def get_firemaking_config():
    return api_get("/firemaking-config")


def get_skills():
    return api_get("/skills") or {}
```

- [ ] **Step 2: Register the routine file**

In `ControllerAddon/controller.py`, change the `ROUTINE_FILES` map (currently lines 1067-1072):

```python
ROUTINE_FILES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
    "GEMSTONE_CRAB": "routines/gemstone_crab.py",
    "TORMENTED_DEMON": "routines/tormented_demon.py",
}
```

to:

```python
ROUTINE_FILES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
    "GEMSTONE_CRAB": "routines/gemstone_crab.py",
    "TORMENTED_DEMON": "routines/tormented_demon.py",
    "FIREMAKING": "routines/firemaking.py",
}
```

- [ ] **Step 3: Syntax check**

Run: `python -m py_compile ControllerAddon/controller.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/controller.py
git commit -m "Add get_firemaking_config/get_skills bridge helpers, register FIREMAKING routine"
```

---

### Task 4: `routines/firemaking.py` — the routine itself

**Files:**
- Create: `ControllerAddon/routines/firemaking.py`

**Interfaces:**
- Consumes: `controller.get_firemaking_config()`, `controller.get_skills()` (Task 3), `controller.get_combat()`, `controller.get_inventory()`, `controller.click_entity(ser, entity)` (all pre-existing), `banker.bank_up(ctx, bank_cfg)` (pre-existing, `banker.py:231`).
- Produces: `DEFAULT_CONFIG` (dict), `get_config(bridge_config)`, `setup(ctx)`, `tick(ctx)` — the routine contract every `routines/*.py` module implements (`controller.py:17-59`).

- [ ] **Step 1: Create `ControllerAddon/routines/firemaking.py`**

```python
# routines/firemaking.py
#
# Burn a configured log type with a tinderbox, one log per tick, restocking from a bank
# when configured. Modeled on routines/mining.py's shape (poll isIdle, act, repeat), but is
# this project's first routine to click two inventory items in sequence (tinderbox, then
# log) rather than one world object/NPC/menu button.
#
# Mechanics (from the local OSRS wiki mirror, osrs-wiki-data-master/raw/F/Firemaking.txt and
# raw/F/Fire.txt):
#   - Lighting is "use tinderbox on log" (or log on tinderbox) -- a plain left-click on the
#     tinderbox already resolves to "Use" (it has no more-prioritized default action to
#     compete with), same reasoning as this project's other tool items. If that's not true
#     for your account/settings, add it to Controller Settings -> Clicking -> Forced Actions,
#     same escape hatch travel.py documents for the ring of dueling.
#   - Below Firemaking level 43, lighting can fail (crowdsourced success chance runs from
#     ~65/256 at level 1 to 256/256 at level 43+, regardless of log tier) with no animation
#     and no log lost -- retrying is just clicking again. tick() gets this for free: if the
#     two clicks didn't start an animation, get_combat().isIdle is still True on the very next
#     tick, so the same two clicks just fire again.
#   - After a successful light, the player auto-walks one step away (west, or east/south/north
#     if blocked) before the next log can be lit -- get_combat().isIdle already accounts for
#     this (isIdle = not moving AND no animation AND no interact target), so waiting for
#     isIdle covers both the light animation and the step-away.
#   - "Power firemaking" (queuing the next light before finishing the walk-away step) is
#     deliberately NOT implemented here -- waiting for full isIdle between logs is simpler and
#     safer, matching this routine's "pretty simple" scope.
#
# Run with:
#     python controller.py routines/firemaking.py

import random
import time

import banker
import controller

# Firemaking level required to burn each log type -- Firemaking.txt's "List of combustible
# logs" table. Used only for a one-time sanity check in setup() (stop early with a clear
# message instead of silently retrying forever against a log the account isn't leveled for
# yet) -- not needed by the burn loop itself, which already handles the success-chance rolls
# above by retrying every tick until isIdle flips.
LOG_LEVEL_REQUIREMENTS = {
    "logs": 1,
    "achey tree logs": 1,
    "oak logs": 15,
    "willow logs": 30,
    "teak logs": 35,
    "jatoba logs": 40,
    "arctic pine logs": 42,
    "maple logs": 45,
    "mahogany logs": 50,
    "yew logs": 60,
    "blisterwood logs": 62,
    "camphor logs": 66,
    "magic logs": 75,
    "ironwood logs": 80,
    "redwood logs": 90,
    "rosewood logs": 92,
}

# Used only if FiremakingPlugin isn't loaded/enabled in RuneLite (so /firemaking-config comes
# back None) -- keeps this routine runnable standalone, same as tormented_demon.py.
DEFAULT_CONFIG = {
    "enabled": True,
    "logName": "Willow logs",
    "tinderboxName": "Tinderbox",

    "bank": {
        "enabled": False,
        "teleportFallback": False,
        "keepItems": ["tinderbox"],
        "withdraw": [{"name": "Willow logs", "quantity": 27}],
    },

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_firemaking_config()
    return config if config is not None else DEFAULT_CONFIG


def setup(ctx):
    """One-time under-level sanity check -- see LOG_LEVEL_REQUIREMENTS above. Resolves config
    the same way get_config() will, since ctx.state.config isn't populated yet at this point
    in the routine-switch lifecycle (same reasoning as tormented_demon.py's setup())."""
    config = get_config(None)
    log_name = (config.get("logName") or "").strip().lower()
    required = LOG_LEVEL_REQUIREMENTS.get(log_name)
    if required is None:
        return  # unknown/custom log name -- can't validate, don't block

    firemaking = controller.get_skills().get("Firemaking") or {}
    level = firemaking.get("level")
    if level is not None and level < required:
        ctx.state.trigger_safety_stop(
            f"Firemaking level {level} is below the {required} required for {config.get('logName')}"
        )


def tick(ctx):
    config = ctx.state.config

    if not controller.get_combat().get("isIdle", True):
        return  # still lighting the last log, or stepping away from the new fire

    tinderbox = find_item(config.get("tinderboxName", "tinderbox"))
    log = find_item(config.get("logName", "logs"))

    if tinderbox is None or log is None:
        bank_cfg = config.get("bank", {})
        if bank_cfg.get("enabled"):
            print("[firemaking] out of logs or no tinderbox -- banking")
            banker.bank_up(ctx, bank_cfg)
        else:
            ctx.state.trigger_safety_stop("out of logs or no tinderbox in inventory")
        return

    print(f"[firemaking] lighting {log.get('name')}")
    time.sleep(random.uniform(0.15, 0.35))
    controller.click_entity(ctx.ser, tinderbox)
    time.sleep(random.uniform(0.1, 0.25))
    controller.click_entity(ctx.ser, log)


def find_item(name: str):
    """First inventory item whose name contains `name` (case-insensitive substring, same
    idiom as travel.py/banker.py) -- e.g. a logName of "Logs" will also match "Achey tree
    logs" if both are present. Pick specific enough logName/tinderboxName values to avoid
    that ambiguity; not solved here, same limitation every other substring match in this
    codebase already has."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if name_lower in (i.get("name") or "").lower()),
        None,
    )
```

- [ ] **Step 2: Syntax check**

Run: `python -m py_compile ControllerAddon/routines/firemaking.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Live verification (manual — no automated test framework covers routine behavior in this codebase)**

With the Firemaking-enabled build running in RuneLite (`FiremakingPlugin` loaded, settings panel configured with a real `logName`/`tinderboxName` you're carrying) and `python controller.py routines/firemaking.py` running against it:

1. Stand somewhere open (or your usual bank-adjacent firemaking spot) with a tinderbox and at least a few of the configured log type in inventory. Confirm the routine clicks the tinderbox, then the log, and a fire lights.
2. Confirm it waits through the light animation *and* the auto-walk-away step before attempting the next log (watch for a double-click too early, which would mean the `isIdle` check isn't holding).
3. Set your Firemaking level (or `logName`) so `LOG_LEVEL_REQUIREMENTS` reports you as under-leveled; confirm `setup()` triggers a safety stop with a clear message instead of the routine spinning silently.
4. Burn down to your last log with `bank.enabled: false`; confirm it safety-stops with "out of logs or no tinderbox in inventory" instead of clicking on nothing.
5. Set `bank.enabled: true` with a real bank and withdraw quantity, burn out, and confirm `banker.bank_up` walks to the bank, deposits nothing unexpected (tinderbox must stay via `keepItems`), withdraws the configured log count, and the routine resumes burning afterward.
6. Leave it running a few minutes and confirm the configured break schedule (`breaks`) actually pauses it, and that `Stop On Death`/`Max Runtime` still work (these come free from `controller.py`'s generic engine as long as the config dict has `breaks`/`safety` keys shaped correctly — no routine-specific code should be needed for this to already work).

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/firemaking.py
git commit -m "Add routines/firemaking.py"
```

---

## Self-Review

**Spec coverage:**
- Tinderbox + log burning loop → Task 4 `tick()`.
- Bank restock when out of logs → Task 4 `tick()` + `banker.bank_up` (Task 1's Java export, reused Python helper).
- Settings panel (per this project's established per-routine-plugin convention) → Task 1.
- Live routine picker wiring → Task 2 (`RoutineChoice`) + Task 3 (`ROUTINE_FILES`).
- Dev harness registration (easy-to-miss step per `ExamplePluginTest.java`'s own comment) → Task 2.
- Under-level footgun (spinning forever against a log the account can't light) → Task 4 `setup()` + new `get_skills()` helper (Task 3).
- Mechanics research from local wiki → embedded in Global Constraints reference section and in `routines/firemaking.py`'s own header comment for future readers.

**Placeholder scan:** No TBD/TODO markers; every step has literal file content, not a description of content.

**Type consistency:** `get_firemaking_config()` (Task 3) returns the exact dict shape `FiremakingPlugin.export()` (Task 1) produces, and `routines/firemaking.py`'s `DEFAULT_CONFIG` (Task 4) mirrors that shape key-for-key so both code paths in `get_config()` behave identically to every existing routine's config resolution. `RoutineChoice.FIREMAKING.name()` (Task 2) is `"FIREMAKING"`, matching `ROUTINE_FILES["FIREMAKING"]` (Task 3) exactly.
