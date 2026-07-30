# Split Defensive/Offensive Prayer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single "Combat Prayer" config slot with independent Defensive (always-on-in-combat) and Offensive (flick-on-before-attack, flick-off-after-hit-lands) prayer slots, so both can be active simultaneously.

**Architecture:** Two new Java enums (`DefensivePrayerChoice`, `OffensivePrayerChoice`) replace `CombatPrayer`, each with its own `ControllerConfig` dropdown, exported as two separate fields by `ConfigExporter`. The bridge gains two new live-state fields (`ticksUntilNextAttack` via reflection into the already-installed "Attack Timer Metronome" plugin, `hitpointsXp`). `slayer.py`'s single `maintain_prayer` becomes two functions called independently from `tick()`.

**Tech Stack:** Java 11 (RuneLite plugin API), Python 3 (bridge client, `routines/slayer.py`). No unit test framework exists for either side of this codebase — Java changes are verified by `./gradlew.bat compileJava`, behavioral changes are verified live against the running bridge + game (same pattern used throughout this project's existing manual-verification workflow).

## Global Constraints

- Java target: source/target compatibility 11 (`build.gradle`: `options.release.set(11)`).
- `net.runelite:client` dependency resolves to whatever `latest.release` is at build time — some API classes (e.g. `net.runelite.api.gameval.*`) may not exist in older cached copies; prefer the legacy (non-`gameval`) API classes already used elsewhere in this codebase (`net.runelite.api.EnumID`, `net.runelite.api.Varbits`, `net.runelite.api.widgets.WidgetID`) since those are confirmed to exist in the actually-resolved dependency.
- Every `@ConfigItem` enum default must be a real, always-present entry (never `null`) — RuneLite's config UI requires a concrete default.
- Python side has no test framework; "tests" for Python tasks are `python -m py_compile` (syntax only) plus documented live-verification steps.

---

### Task 1: Split `CombatPrayer` into `DefensivePrayerChoice` and `OffensivePrayerChoice`

**Files:**
- Create: `src/main/java/com/example/controller/DefensivePrayerChoice.java`
- Create: `src/main/java/com/example/controller/OffensivePrayerChoice.java`
- Delete: `src/main/java/com/example/controller/CombatPrayer.java`

**Interfaces:**
- Produces: `DefensivePrayerChoice` enum with `.getPrayer()` (nullable `net.runelite.api.Prayer`), `.getLabel()` (`String`), `.getLevel()` (`int`), `.name()` values `NONE`/`PROTECT_FROM_MAGIC`/`PROTECT_FROM_MISSILES`/`PROTECT_FROM_MELEE`.
- Produces: `OffensivePrayerChoice` enum with the same three accessors, `.name()` values `NONE`/`AUTO_MELEE`/`AUTO_RANGED`/`AUTO_MAGIC`/`STEEL_SKIN`/`ULTIMATE_STRENGTH`/`INCREDIBLE_REFLEXES`/`CHIVALRY`/`PIETY`/`EAGLE_EYE`/`DEADEYE`/`RIGOUR`/`MYSTIC_LORE`/`MYSTIC_MIGHT`/`AUGURY`.

- [ ] **Step 1: Create `DefensivePrayerChoice.java`**

```java
package com.example.controller;

import net.runelite.api.Prayer;

/**
 * Selectable defensive (Protect from X) prayers for the Defensive Prayer config
 * dropdown -- always-on-while-in-combat behavior (see routines/slayer.py's
 * maintain_defensive_prayer). Enum constant names double as the wire format sent
 * to Python (see ConfigExporter) and, for entries with a real Prayer, must match
 * that Prayer's own name() -- that's how slayer.py cross-references
 * PrayerProvider's active/icons maps.
 */
public enum DefensivePrayerChoice
{
	NONE(null, "None", 1),
	PROTECT_FROM_MAGIC(Prayer.PROTECT_FROM_MAGIC, "Protect from Magic", 37),
	PROTECT_FROM_MISSILES(Prayer.PROTECT_FROM_MISSILES, "Protect from Missiles", 40),
	PROTECT_FROM_MELEE(Prayer.PROTECT_FROM_MELEE, "Protect from Melee", 43);

	private final Prayer prayer;
	private final String label;
	private final int level;

	DefensivePrayerChoice(Prayer prayer, String label, int level)
	{
		this.prayer = prayer;
		this.label = label;
		this.level = level;
	}

	public Prayer getPrayer()
	{
		return prayer;
	}

	public String getLabel()
	{
		return label;
	}

	public int getLevel()
	{
		return level;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
```

- [ ] **Step 2: Create `OffensivePrayerChoice.java`**

```java
package com.example.controller;

import net.runelite.api.Prayer;

/**
 * Selectable offensive (stat-boosting) prayers for the Offensive Prayer config
 * dropdown, plus three "Auto" pseudo-entries (prayer == null, resolved
 * client-side in slayer.py's PRAYER_LEVELS/AUTO_PRAYER_LADDERS, which must stay
 * in sync with the levels here) that pick the best of these the account has
 * actually unlocked for a given combat style. Unlike DefensivePrayerChoice, the
 * selection here gets flicked on right before the player's own attack and off
 * once the hit registers (see routines/slayer.py's maintain_offensive_prayer),
 * not held on for the whole fight.
 *
 * DEADEYE is deliberately NOT in the AUTO_RANGED ladder even though it's the
 * best Ranged offensive prayer -- unlocking it requires looting a scroll from
 * Royal Titans in addition to meeting the level requirement, which a level-only
 * ladder can't verify. Pick it manually if your account has it unlocked.
 */
public enum OffensivePrayerChoice
{
	NONE(null, "None", 1),
	AUTO_MELEE(null, "Auto (Melee)", 1),
	AUTO_RANGED(null, "Auto (Ranged)", 1),
	AUTO_MAGIC(null, "Auto (Magic)", 1),
	STEEL_SKIN(Prayer.STEEL_SKIN, "Steel Skin", 28),
	ULTIMATE_STRENGTH(Prayer.ULTIMATE_STRENGTH, "Ultimate Strength", 31),
	INCREDIBLE_REFLEXES(Prayer.INCREDIBLE_REFLEXES, "Incredible Reflexes", 34),
	CHIVALRY(Prayer.CHIVALRY, "Chivalry", 60),
	PIETY(Prayer.PIETY, "Piety", 70),
	EAGLE_EYE(Prayer.EAGLE_EYE, "Eagle Eye", 44),
	DEADEYE(Prayer.DEADEYE, "Deadeye", 62),
	RIGOUR(Prayer.RIGOUR, "Rigour", 74),
	MYSTIC_LORE(Prayer.MYSTIC_LORE, "Mystic Lore", 27),
	MYSTIC_MIGHT(Prayer.MYSTIC_MIGHT, "Mystic Might", 45),
	AUGURY(Prayer.AUGURY, "Augury", 77);

	private final Prayer prayer;
	private final String label;
	private final int level;

	OffensivePrayerChoice(Prayer prayer, String label, int level)
	{
		this.prayer = prayer;
		this.label = label;
		this.level = level;
	}

	public Prayer getPrayer()
	{
		return prayer;
	}

	public String getLabel()
	{
		return label;
	}

	public int getLevel()
	{
		return level;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
```

- [ ] **Step 3: Delete `CombatPrayer.java`**

Delete `src/main/java/com/example/controller/CombatPrayer.java`. Leave its
usages in `PrayerProvider.java` and `ControllerConfig.java` broken for now --
Tasks 2 and 4 fix them. This step will NOT compile on its own; that's expected
and fixed by the end of Task 4.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/controller/DefensivePrayerChoice.java src/main/java/com/example/controller/OffensivePrayerChoice.java
git rm src/main/java/com/example/controller/CombatPrayer.java
git commit -m "Split CombatPrayer into DefensivePrayerChoice and OffensivePrayerChoice"
```

---

### Task 2: Update `ControllerConfig.java` — two dropdowns instead of one

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerConfig.java:150-160`

**Interfaces:**
- Consumes: `DefensivePrayerChoice`, `OffensivePrayerChoice` (Task 1).
- Produces: `ControllerConfig.defensivePrayer()` returning `DefensivePrayerChoice` (default `NONE`), `ControllerConfig.offensivePrayer()` returning `OffensivePrayerChoice` (default `NONE`). `combatPrayer()` no longer exists.

- [ ] **Step 1: Replace the `combatPrayer` config item**

In `ControllerConfig.java`, replace lines 150-160 (the `combatPrayer` `@ConfigItem` and its default method):

```java
	@ConfigItem(
		keyName = "combatPrayer",
		name = "Combat Prayer",
		description = "Prayer to activate while in combat.",
		section = prayerSection,
		position = 1
	)
	default CombatPrayer combatPrayer()
	{
		return CombatPrayer.NONE;
	}
```

with:

```java
	@ConfigItem(
		keyName = "defensivePrayer",
		name = "Defensive Prayer",
		description = "Protect-from prayer to keep on for the whole fight.",
		section = prayerSection,
		position = 1
	)
	default DefensivePrayerChoice defensivePrayer()
	{
		return DefensivePrayerChoice.NONE;
	}

	@ConfigItem(
		keyName = "offensivePrayer",
		name = "Offensive Prayer",
		description = "Stat-boosting prayer to flick on right before each of your own attacks and off once it lands.",
		section = prayerSection,
		position = 2
	)
	default OffensivePrayerChoice offensivePrayer()
	{
		return OffensivePrayerChoice.NONE;
	}
```

Note the two `@ConfigItem`s after this one (`useQuickPrayers` at `position = 2`,
`restorePrayerAt` at `position = 3`) now collide on position with the new
`offensivePrayer` entry -- renumber `useQuickPrayers` to `position = 3` and
`restorePrayerAt` to `position = 4` so all four stay in a sensible order.

- [ ] **Step 2: Compile-check**

Run: `./gradlew.bat compileJava --console=plain` from the repo root.
Expected: still fails (PrayerProvider.java still references the deleted
`CombatPrayer` — fixed in Task 4). Confirm the *only* remaining errors
mention `CombatPrayer`/`combatPrayer` in `PrayerProvider.java`, not
`ControllerConfig.java`.

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/example/controller/ControllerConfig.java
git commit -m "Replace ControllerConfig.combatPrayer with defensivePrayer/offensivePrayer"
```

---

### Task 3: Update `ConfigExporter.java` — export both prayer fields

**Files:**
- Modify: `src/main/java/com/example/controller/ConfigExporter.java:27-32`

**Interfaces:**
- Consumes: `ControllerConfig.defensivePrayer()`, `ControllerConfig.offensivePrayer()` (Task 2).
- Produces: bridge `/config` response's `prayer` object gains `defensivePrayer` and `offensivePrayer` string fields (each `.name()` of the chosen enum constant); `combatPrayer` field no longer present.

- [ ] **Step 1: Replace the `combatPrayer` export line**

In `ConfigExporter.java`, replace line 29:

```java
		prayer.put("combatPrayer", config.combatPrayer().name());
```

with:

```java
		prayer.put("defensivePrayer", config.defensivePrayer().name());
		prayer.put("offensivePrayer", config.offensivePrayer().name());
```

- [ ] **Step 2: Compile-check**

Run: `./gradlew.bat compileJava --console=plain`.
Expected: still fails, same `PrayerProvider.java` errors as Task 2 Step 2 —
confirm no *new* errors were introduced in `ConfigExporter.java`.

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/example/controller/ConfigExporter.java
git commit -m "Export defensivePrayer/offensivePrayer instead of combatPrayer"
```

---

### Task 4: Update `PrayerProvider.java` to resolve icons for both enums

**Files:**
- Modify: `src/main/java/com/example/providers/PrayerProvider.java:4` (import)
- Modify: `src/main/java/com/example/providers/PrayerProvider.java` (`getPrayerIconScreenCoords`, currently iterating `CombatPrayer.values()`)

**Interfaces:**
- Consumes: `DefensivePrayerChoice`, `OffensivePrayerChoice` (Task 1).
- Produces: `/prayers` response's `icons` map now covers every `Prayer` referenced by either `DefensivePrayerChoice` or `OffensivePrayerChoice` (previously covered by `CombatPrayer`) — no change to the map's shape (`{prayerName: {screenX, screenY}}`), only to which prayers can appear in it.

- [ ] **Step 1: Update the import**

Replace `import com.example.controller.CombatPrayer;` with:

```java
import com.example.controller.DefensivePrayerChoice;
import com.example.controller.OffensivePrayerChoice;
```

- [ ] **Step 2: Update `getPrayerIconScreenCoords`**

Find the current method (introduced this session, iterates `CombatPrayer.values()`):

```java
	private Map<String, Object> getPrayerIconScreenCoords()
	{
		Map<String, Object> icons = new HashMap<>();

		for (CombatPrayer combatPrayer : CombatPrayer.values())
		{
			Prayer prayer = combatPrayer.getPrayer();
			if (prayer == null || icons.containsKey(prayer.name()))
			{
				continue;
			}

			Map<String, Object> entry = resolveFromCache(prayer);
			if (entry == null)
			{
				entry = resolveByName(prayer, combatPrayer.getLabel());
			}
			if (entry != null)
			{
				icons.put(prayer.name(), entry);
			}
		}

		return icons;
	}
```

Replace it with a version that walks both enums' `(Prayer, label)` pairs
through the same resolution logic:

```java
	private Map<String, Object> getPrayerIconScreenCoords()
	{
		Map<String, Object> icons = new HashMap<>();

		for (DefensivePrayerChoice choice : DefensivePrayerChoice.values())
		{
			resolveInto(icons, choice.getPrayer(), choice.getLabel());
		}
		for (OffensivePrayerChoice choice : OffensivePrayerChoice.values())
		{
			resolveInto(icons, choice.getPrayer(), choice.getLabel());
		}

		return icons;
	}

	private void resolveInto(Map<String, Object> icons, Prayer prayer, String label)
	{
		if (prayer == null || icons.containsKey(prayer.name()))
		{
			return;
		}

		Map<String, Object> entry = resolveFromCache(prayer);
		if (entry == null)
		{
			entry = resolveByName(prayer, label);
		}
		if (entry != null)
		{
			icons.put(prayer.name(), entry);
		}
	}
```

- [ ] **Step 3: Compile-check**

Run: `./gradlew.bat clean compileJava --console=plain`.
Expected: `BUILD SUCCESSFUL`. This is the first point in the plan where the
whole Java side compiles clean again.

- [ ] **Step 4: Live verification**

Relaunch RuneLite (however you normally sideload this plugin, e.g.
`./gradlew.bat run`), log in, set both a Defensive and an Offensive prayer in
Controller Settings, open the Prayer tab, then check:

```bash
curl -s http://localhost:8080/prayers
```

Expected: the `icons` map contains entries for both the chosen defensive
prayer (e.g. `PROTECT_FROM_MELEE`) and the chosen offensive prayer (e.g.
`PIETY`), each with real `screenX`/`screenY` values.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/providers/PrayerProvider.java
git commit -m "Resolve prayer icons for DefensivePrayerChoice + OffensivePrayerChoice"
```

---

### Task 5: Diagnose the Attack Timer Metronome plugin's real method

**Files:**
- Modify: `src/main/java/com/example/providers/CombatProvider.java` (temporary diagnostic only — Task 6 replaces this with the real implementation)

**Interfaces:**
- Produces: nothing permanent — this task's only output is console log lines telling us the real method name/signature to use in Task 6. Do not commit this task's diagnostic code as-is; Task 6 replaces it.

**Why this task exists:** ZombieLite's own integration with this same plugin
(`ZombiePrayerPlugin.hookAttackTimerMetronome()`, in the local
`Documents/ZombieLite-master` checkout) finds the plugin by name, then calls
*every* zero-argument `int`-returning method on it once and guesses that
whichever one currently returns exactly `1` is "ticks until next attack." That
heuristic is fragile and we have no guarantee it picks the right method on
this specific installed version of the plugin. Dumping every candidate
method's actual name and current value first lets us pick the real one by
name in Task 6 instead of copying a guess.

- [ ] **Step 1: Add the diagnostic to `CombatProvider.java`**

Add this import at the top of `CombatProvider.java`:

```java
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginManager;
```

Add a `PluginManager` field and constructor parameter (temporarily — Task 6
makes this permanent, so this edit survives into the next task):

```java
public class CombatProvider extends BridgeProvider
{
	private final PluginManager pluginManager;
	private boolean dumpedMetronomeMethods = false;

	public CombatProvider(Client client, ClientThread clientThread, BridgeHttpServer server, PluginManager pluginManager)
	{
		super(client, clientThread, server);
		this.pluginManager = pluginManager;
	}
```

(This replaces the existing constructor
`public CombatProvider(Client client, ClientThread clientThread, BridgeHttpServer server)` — Task 6 keeps this same signature, so no further constructor changes are needed later.)

Add this diagnostic block inside `getCombat()`, right after
`data.put("tick", client.getTickCount());`:

```java
		// TEMP DIAGNOSTIC -- remove in Task 6 once the real method name is known.
		if (!dumpedMetronomeMethods)
		{
			Plugin metronome = pluginManager.getPlugins().stream()
				.filter(p -> p.getName() != null && p.getName().toLowerCase().contains("metronome")
					|| p.getClass().getSimpleName().toLowerCase().contains("metronome")
					|| p.getClass().getSimpleName().toLowerCase().contains("attacktimer"))
				.findFirst().orElse(null);

			if (metronome == null)
			{
				System.out.println("[metronome-debug] no metronome/attacktimer plugin found");
			}
			else
			{
				System.out.println("[metronome-debug] found plugin: " + metronome.getName()
					+ " (" + metronome.getClass().getName() + ")");
				for (java.lang.reflect.Method m : metronome.getClass().getDeclaredMethods())
				{
					if (m.getReturnType() != int.class || m.getParameterCount() > 0)
					{
						continue;
					}
					try
					{
						m.setAccessible(true);
						int value = -999;
						try
						{
							value = (int) m.invoke(metronome);
						}
						catch (Exception ignored)
						{
						}
						m.setAccessible(false);
						System.out.println("[metronome-debug]   method=" + m.getName() + " value=" + value);
					}
					catch (SecurityException ignored)
					{
					}
				}
			}
			dumpedMetronomeMethods = true;
		}
```

- [ ] **Step 2: Update the call site**

In `ControllerPlugin.java`, find:

```java
		new CombatProvider(client, clientThread, server).register();
```

and change it to:

```java
		new CombatProvider(client, clientThread, server, pluginManager).register();
```

(`pluginManager` is already an injected field on `ControllerPlugin` — see its
use a few lines below for `ShortestPathProvider`.)

- [ ] **Step 3: Compile-check**

Run: `./gradlew.bat clean compileJava --console=plain`.
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 4: Live diagnostic run**

Relaunch RuneLite (`./gradlew.bat run`), log in with the Attack Timer
Metronome plugin enabled, and either query the bridge once...

```bash
curl -s http://localhost:8080/combat > /dev/null
```

...or just let the routine poll it naturally (any hit to `/combat` triggers
the one-time dump). Read whatever printed to the console/terminal running
`gradlew run` for lines starting `[metronome-debug]`.

Expected output shape: a `found plugin: ...` line, followed by one
`method=... value=...` line per zero-arg int method the plugin exposes. Note
down the exact method name whose current value looks like a plausible
"ticks until next attack" reading (small non-negative integer, changing
value across repeated calls while actually in combat vs. a fixed/irrelevant
constant while idle).

**Do not commit this task.** Its only purpose is the console output — Task 6
starts from a clean re-read of `CombatProvider.java` and replaces the
diagnostic block with the real implementation using the method name found
here.

---

### Task 6: Implement `ticksUntilNextAttack` in `CombatProvider.java`

**Files:**
- Modify: `src/main/java/com/example/providers/CombatProvider.java` (replace Task 5's diagnostic block)

**Interfaces:**
- Consumes: the exact method name identified in Task 5 (this task's steps use a placeholder name, `getTicksUntilNextAttack`, adjusted to the finding). `PluginManager` (already wired into the constructor in Task 5 — no further constructor changes here).
- Produces: `/combat` response gains `ticksUntilNextAttack` (int), present only when the metronome plugin and its method were both found; absent (not `null` — the key itself missing) otherwise.

- [ ] **Step 1: Replace the diagnostic block with the real hookup**

Remove the `dumpedMetronomeMethods` field and the diagnostic block added in
Task 5. Add these fields to `CombatProvider`:

```java
	private Plugin metronomePlugin;
	private java.lang.reflect.Method ticksUntilNextAttackMethod;
	private boolean metronomeHooked = false;
```

Add this method (adjust `"getTicksUntilNextAttack"` and the plugin
name-matching substring below to whatever Task 5 actually found):

```java
	// Attack Timer Metronome has no official RuneLite API surface -- this reflects into it by
	// name, same technique as UnsafeNpcOverheadIcon and the same technique the community
	// ZombieLite fork uses for the same plugin (see its ZombiePrayerPlugin
	// .hookAttackTimerMetronome()). Hooked once (first call); if the plugin isn't loaded or
	// this method no longer exists on it, ticksUntilNextAttack is simply absent from every
	// /combat response afterward -- no error, no fallback guess.
	private void hookMetronomeIfNeeded()
	{
		if (metronomeHooked)
		{
			return;
		}
		metronomeHooked = true;

		Plugin plugin = pluginManager.getPlugins().stream()
			.filter(p -> p.getClass().getSimpleName().equals("AttackTimerMetronomePlugin"))
			.findFirst().orElse(null);
		if (plugin == null)
		{
			return;
		}

		try
		{
			java.lang.reflect.Method method = plugin.getClass().getDeclaredMethod("getTicksUntilNextAttack");
			if (method.getReturnType() != int.class)
			{
				return;
			}
			method.setAccessible(true);
			metronomePlugin = plugin;
			ticksUntilNextAttackMethod = method;
		}
		catch (NoSuchMethodException | SecurityException ignored)
		{
		}
	}

	private Integer getTicksUntilNextAttack()
	{
		hookMetronomeIfNeeded();
		if (metronomePlugin == null || ticksUntilNextAttackMethod == null)
		{
			return null;
		}
		try
		{
			return (int) ticksUntilNextAttackMethod.invoke(metronomePlugin);
		}
		catch (Exception ignored)
		{
			return null;
		}
	}
```

In `getCombat()`, add (right after the existing `data.put("tick", ...)`
line):

```java
		Integer ticksUntilNextAttack = getTicksUntilNextAttack();
		if (ticksUntilNextAttack != null)
		{
			data.put("ticksUntilNextAttack", ticksUntilNextAttack);
		}
```

- [ ] **Step 2: Compile-check**

Run: `./gradlew.bat clean compileJava --console=plain`.
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 3: Live verification**

Relaunch RuneLite, log in, get into combat, then:

```bash
curl -s http://localhost:8080/combat
```

Expected: a `ticksUntilNextAttack` field present with a small integer that
changes across repeated calls while attacking (counting down toward your
next hit), or absent entirely if the metronome plugin isn't loaded/enabled.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/providers/CombatProvider.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Add ticksUntilNextAttack to /combat via Attack Timer Metronome reflection"
```

---

### Task 7: Add `hitpointsXp` to `PlayerProvider.java`

**Files:**
- Modify: `src/main/java/com/example/providers/PlayerProvider.java:68`

**Interfaces:**
- Produces: `/player` response gains `hitpointsXp` (int).

- [ ] **Step 1: Add the field**

In `PlayerProvider.java`, right after the existing line
`data.put("prayerReal", client.getRealSkillLevel(Skill.PRAYER));`, add:

```java
		data.put("hitpointsXp", client.getSkillExperience(Skill.HITPOINTS));
```

- [ ] **Step 2: Compile-check**

Run: `./gradlew.bat clean compileJava --console=plain`.
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 3: Live verification**

Relaunch RuneLite, log in, then:

```bash
curl -s http://localhost:8080/player
```

Expected: a `hitpointsXp` field with a plausible XP number (thousands to
millions depending on the account's Hitpoints level). Land a hit on
something and query again — the value should have increased.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/providers/PlayerProvider.java
git commit -m "Add hitpointsXp to /player, used as the offensive-prayer hit-landed signal"
```

---

### Task 8: Update `PRAYER_LEVELS`/`AUTO_PRAYER_LADDERS` in `slayer.py`

**Files:**
- Modify: `ControllerAddon/routines/slayer.py:178-206`

**Interfaces:**
- Produces: `PRAYER_LEVELS` gains `"DEADEYE": 62` and `"MYSTIC_LORE": 27`. `AUTO_PRAYER_LADDERS` unchanged (still `AUTO_RANGED: ["RIGOUR", "EAGLE_EYE"]` — Deadeye is deliberately excluded, see Task 1's `OffensivePrayerChoice` docstring).

- [ ] **Step 1: Update the comment and dict**

Replace the comment above `PRAYER_LEVELS` (currently says "mirroring
com.example.controller.CombatPrayer") and add the two missing entries:

```python
# Prayer level requirements, mirroring com.example.controller.DefensivePrayerChoice /
# OffensivePrayerChoice on the Java side (keep both in sync if either changes) --
# standard OSRS prayer levels, not re-verified against a live client but stable/
# well-known. Used to skip a manually-picked prayer the account hasn't unlocked yet
# (rather than looping forever re-clicking a locked icon) and to resolve the three
# Auto entries below.
PRAYER_LEVELS = {
    "PROTECT_FROM_MAGIC": 37,
    "PROTECT_FROM_MISSILES": 40,
    "PROTECT_FROM_MELEE": 43,
    "STEEL_SKIN": 28,
    "ULTIMATE_STRENGTH": 31,
    "INCREDIBLE_REFLEXES": 34,
    "CHIVALRY": 60,
    "PIETY": 70,
    "EAGLE_EYE": 44,
    "DEADEYE": 62,
    "RIGOUR": 74,
    "MYSTIC_LORE": 27,
    "MYSTIC_MIGHT": 45,
    "AUGURY": 77,
}

# Best-to-worst per combat style -- AUTO_* resolves to the first entry the account's
# prayer level actually covers. DEADEYE is deliberately not in AUTO_RANGED's ladder:
# unlocking it needs a Royal Titans scroll drop on top of the level requirement,
# which this level-only check can't verify -- accounts that have it unlocked can
# pick it directly instead of via Auto.
AUTO_PRAYER_LADDERS = {
    "AUTO_MELEE": ["PIETY", "CHIVALRY", "STEEL_SKIN"],
    "AUTO_RANGED": ["RIGOUR", "EAGLE_EYE"],
    "AUTO_MAGIC": ["AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"],
}
```

(`AUTO_MAGIC`'s ladder gains `MYSTIC_LORE` as its lowest tier, mirroring how
`AUTO_MELEE` already has a three-tier ladder ending in `STEEL_SKIN` and
`AUTO_RANGED` a two-tier one ending in `EAGLE_EYE` — magic's was previously
missing its equivalent lowest rung.)

- [ ] **Step 2: Syntax-check**

Run: `python -m py_compile ControllerAddon/routines/slayer.py`
Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/slayer.py
git commit -m "Add DEADEYE/MYSTIC_LORE to slayer.py's prayer level table"
```

---

### Task 9: Split `maintain_prayer` into defensive + offensive functions

**Files:**
- Modify: `ControllerAddon/routines/slayer.py:34-57` (`tick()` call site)
- Modify: `ControllerAddon/routines/slayer.py:235-` (replace `maintain_prayer`)

**Interfaces:**
- Consumes: `combat.get("ticksUntilNextAttack")` (Task 6), `player.get("hitpointsXp")` (Task 7), `prayer_cfg.get("defensivePrayer")`/`prayer_cfg.get("offensivePrayer")` (Task 3's bridge export), `resolve_combat_prayer`/`PRAYER_LEVELS`/`AUTO_PRAYER_LADDERS` (Task 8, unchanged function signature).
- Produces: `maintain_defensive_prayer(ctx, combat)`, `maintain_offensive_prayer(ctx, combat)` — both called from `tick()`. `maintain_prayer` no longer exists.

- [ ] **Step 1: Update the `tick()` call site**

In `tick()`, replace:

```python
    combat = controller.get_combat()
    maintain_prayer(ctx, combat)
    maintain_supplies(ctx)
```

with:

```python
    combat = controller.get_combat()
    maintain_defensive_prayer(ctx, combat)
    maintain_offensive_prayer(ctx, combat)
    maintain_supplies(ctx)
```

- [ ] **Step 2: Replace `maintain_prayer` with `maintain_defensive_prayer`**

Replace the entire current `maintain_prayer` function -- from `def
maintain_prayer(ctx, combat: dict):` through its last line (the
potion-drinking block at the end, which stays as part of this function,
unchanged in behavior) -- with:

```python
def maintain_defensive_prayer(ctx, combat: dict):
    """Keeps a single defensive (Protect from X) prayer on for the whole fight --
    LAZY behavior (see docs/superpowers/specs/2026-07-29-split-defensive-offensive-prayer-design.md),
    unlike maintain_offensive_prayer's flick-on/flick-off. Also drives prayer-potion
    upkeep, since that's independent of which prayer(s) are actually selected."""
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    if not prayer_cfg.get("enabled"):
        return

    prayers = controller.get_prayers()
    player = controller.get_player()

    raw_defensive_prayer = prayer_cfg.get("defensivePrayer", "NONE")
    defensive_prayer = resolve_combat_prayer(raw_defensive_prayer, player.get("prayerReal"))

    if prayer_cfg.get("useQuickPrayers"):
        want_on = bool(combat.get("inCombat"))
        is_on = prayers.get("quickPrayerEnabled", False)
        if want_on != is_on:
            ox, oy = prayers.get("quickOrbScreenX"), prayers.get("quickOrbScreenY")
            if ox is not None and oy is not None:
                controller.click_entity(ctx.ser, {"screenX": ox, "screenY": oy}, offset=(0, 0))
    elif defensive_prayer:
        want_on = bool(combat.get("inCombat"))
        is_on = prayers.get("active", {}).get(defensive_prayer, False)
        if want_on != is_on:
            # Prayer icons only get live screen coords while the Prayer tab is open --
            # switch to it, then re-fetch (the "prayers" snapshot above was taken before
            # we knew we'd need it, so its "icons" may still be from whatever tab was
            # open a moment ago).
            controller.open_prayer_tab(ctx.ser)
            icon = controller.get_prayers().get("icons", {}).get(defensive_prayer)
            if icon:
                print(f"[prayer] {'turning on' if want_on else 'turning off'} {defensive_prayer}")
                controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False)
    elif raw_defensive_prayer in (None, "", "NONE"):
        if not ctx.state.data.get("warned_no_defensive_prayer"):
            print("[prayer] usePrayer is on with useQuickPrayers off, but Defensive Prayer is "
                  "set to None in Controller Settings -- nothing to toggle. Pick a prayer there "
                  "(e.g. Protect from Melee) or enable Use Quick Prayers instead.")
            ctx.state.data["warned_no_defensive_prayer"] = True
    elif not ctx.state.data.get("warned_defensive_prayer_level_too_low"):
        # Either a manually-picked prayer this account hasn't unlocked, or an Auto ladder
        # with nothing on it affordable yet -- either way, nothing usable right now. Surfaced
        # once rather than resolve_combat_prayer's None silently looking identical to "nothing
        # configured", which would otherwise be confusing to debug from the logs alone.
        print(f"[prayer] Defensive Prayer is set to {raw_defensive_prayer} in Controller "
              f"Settings, but nothing in that selection is usable yet at prayer level "
              f"{player.get('prayerReal')} -- nothing to toggle until it's leveled up.")
        ctx.state.data["warned_defensive_prayer_level_too_low"] = True

    potions_cfg = config.get("potions", {})
    if potions_cfg.get("prayerEnabled"):
        restore_at = prayer_cfg.get("restoreAt", 18)
        current_prayer, prayer_max = player.get("prayer"), player.get("prayerReal")
        if current_prayer is None:
            return

        # Drinking at the exact same prayer point every time is a tell -- roll a fresh
        # threshold somewhere in [restore_at, restore_at + spread] instead, so some fights
        # get topped up right at the floor and others earlier, same as a real player
        # glancing at the orb rather than watching it like a hawk. Sticks until a drink
        # actually happens, then re-rolls for next time.
        threshold = ctx.state.data.get("prayer_drink_threshold")
        if threshold is None:
            threshold = random.uniform(restore_at, restore_at + settings.PRAYER_RESTORE_SPREAD)
            ctx.state.data["prayer_drink_threshold"] = threshold

        should_drink = current_prayer <= threshold
        mistake = False
        if not should_drink and prayer_max and current_prayer >= prayer_max * settings.MISTAKE_OVERDRINK_MIN_PRAYER_FRACTION \
                and random.random() < settings.MISTAKE_OVERDRINK_CHANCE:
            should_drink, mistake = True, True  # wastes a dose we didn't actually need yet

        if should_drink and controller.consume_first(ctx.ser, "prayer"):
            print(f"[prayer] {'(mistake) ' if mistake else ''}drank prayer restore "
                  f"(prayer={current_prayer}, threshold={threshold:.0f})")
            ctx.state.data["prayer_drink_threshold"] = None
```

- [ ] **Step 3: Add `maintain_offensive_prayer`**

Add this new function directly after `maintain_defensive_prayer`:

```python
def maintain_offensive_prayer(ctx, combat: dict):
    """Flicks a single offensive (stat-boosting) prayer on right before the player's
    own attack lands and off once it registers -- LAZY_FLICK-style behavior (see
    docs/superpowers/specs/2026-07-29-split-defensive-offensive-prayer-design.md),
    scoped down from the full damage-prediction system a reference community fork
    (ZombieLite) uses for the same idea, since this project has no equivalent and
    can't reliably hit tick-perfect timing through external input anyway. Requires
    the Attack Timer Metronome plugin to be loaded -- if combat.ticksUntilNextAttack
    is ever absent, this is a permanent no-op (never guesses a fallback timing)."""
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    if not prayer_cfg.get("enabled"):
        return

    raw_offensive_prayer = prayer_cfg.get("offensivePrayer", "NONE")
    player = controller.get_player()
    offensive_prayer = resolve_combat_prayer(raw_offensive_prayer, player.get("prayerReal"))
    if not offensive_prayer:
        return

    ticks_until_attack = combat.get("ticksUntilNextAttack")
    if ticks_until_attack is None:
        return

    prayers = controller.get_prayers()
    is_on = prayers.get("active", {}).get(offensive_prayer, False)
    hp_xp = player.get("hitpointsXp")
    flicked_on_tick = ctx.state.data.get("offensive_prayer_on_since_tick")

    if not is_on:
        if ticks_until_attack == 1 and combat.get("inCombat"):
            controller.open_prayer_tab(ctx.ser)
            icon = controller.get_prayers().get("icons", {}).get(offensive_prayer)
            if icon:
                print(f"[prayer] flicking on {offensive_prayer}")
                controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False)
                ctx.state.data["offensive_prayer_on_since_tick"] = combat.get("tick")
                ctx.state.data["offensive_prayer_hp_xp_at_flick"] = hp_xp
        return

    hp_xp_at_flick = ctx.state.data.get("offensive_prayer_hp_xp_at_flick")
    hit_landed = hp_xp is not None and hp_xp_at_flick is not None and hp_xp > hp_xp_at_flick
    tick_passed = flicked_on_tick is not None and combat.get("tick") is not None and combat.get("tick") > flicked_on_tick

    if hit_landed or tick_passed:
        controller.open_prayer_tab(ctx.ser)
        icon = controller.get_prayers().get("icons", {}).get(offensive_prayer)
        if icon:
            print(f"[prayer] flicking off {offensive_prayer} ({'hit landed' if hit_landed else 'attack tick passed'})")
            controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False)
        ctx.state.data["offensive_prayer_on_since_tick"] = None
        ctx.state.data["offensive_prayer_hp_xp_at_flick"] = None
```

- [ ] **Step 4: Syntax-check**

Run: `python -m py_compile ControllerAddon/routines/slayer.py`
Expected: no output (success).

- [ ] **Step 5: Live verification**

Restart the python controller (`python controller.py routines/slayer.py`
from `ControllerAddon/`) against a live target with both a Defensive and an
Offensive prayer configured, and the Attack Timer Metronome plugin loaded.
Watch the console for `[prayer] flicking on <OFFENSIVE_PRAYER>` lines landing
right around each of your own attacks, followed shortly by a matching
`flicking off ... (hit landed)` or `(attack tick passed)` line. Separately
confirm the defensive prayer's `[prayer] turning on/off` lines still appear
exactly as before (once at combat start/end, not flickering).

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/slayer.py
git commit -m "Split maintain_prayer into maintain_defensive_prayer + maintain_offensive_prayer"
```

---

## Self-Review Notes

- **Spec coverage:** Config split (Tasks 1-3) ✓. Icon resolution for both enums (Task 4) ✓. `ticksUntilNextAttack` via metronome reflection (Tasks 5-6, split into diagnose-then-implement per the spec's documented fragility) ✓. `hitpointsXp` (Task 7) ✓. `PRAYER_LEVELS`/`AUTO_PRAYER_LADDERS` updates including the DEADEYE-excluded-from-ladder decision (Task 8) ✓. `maintain_defensive_prayer`/`maintain_offensive_prayer` split with the exact flick-on/flick-off/miss-handling rules from the spec (Task 9) ✓.
- **Placeholder scan:** no TBD/TODO; Task 5's method name is explicitly a to-be-filled-in-from-live-data placeholder by design (the whole point of the task), and Task 6 says exactly how to fill it in.
- **Type consistency:** `defensivePrayer`/`offensivePrayer` string keys match across `ConfigExporter` (Task 3), `slayer.py`'s `prayer_cfg.get(...)` calls (Task 9). `ticksUntilNextAttack`/`hitpointsXp` field names match across Java `data.put(...)` calls (Tasks 6-7) and Python `combat.get(...)`/`player.get(...)` calls (Task 9).
