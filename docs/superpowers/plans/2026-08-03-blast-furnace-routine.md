# Blast Furnace Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `routines/blast_furnace.py` bot routine that smelts a configured bar type at the Blast Furnace on an official ("mass") Blast Furnace world — banking materials, loading coal (via a coal bag) and ore onto the conveyor belt following the wiki's per-bar-type trip patterns, collecting bars, and periodically topping up the coffer — wired into the same live-picker/settings-panel infrastructure every other routine in this project uses.

**Architecture:** A trip-pattern state machine: each bar type maps to an ordered list of "trips" (what to load onto the conveyor, and whether to collect bars at the end of that trip), taken directly from the OSRS Wiki's coal-bag-optimized patterns. `tick()` executes one trip per call — bank for that trip's materials, deposit them on the conveyor belt (reusing firemaking's item-then-target two-click idiom, just targeting a world object instead of a second inventory item), collect bars if the trip calls for it, advance to the next trip index (wrapping after the last one, which is what makes single-trip bar types like Iron or Steel repeat correctly with no special-casing). Two small pieces of genuinely new shared infrastructure are needed first: a chatbox digit-typing helper (`controller.py`) for the coffer's "Enter amount" top-up, and a coal-bag Fill/Empty pair (`banker.py`) mirroring the existing looting-bag helpers.

**Tech Stack:** Java 11 (RuneLite plugin API, `build.gradle`), Python 3 (bridge client). No unit test framework exists for either side of this codebase — Java changes are verified by `.\gradlew.bat compileJava`/`compileTestJava`, Python changes by `python -m py_compile`, and routine behavior is verified live against the running bridge + game, same as every other routine in this project.

## Global Constraints

- Java target: source/target compatibility 11 (`build.gradle`: `options.release.set(11)`).
- Every `@ConfigItem` default must be a concrete value RuneLite's config UI can render — never `null` for a primitive-backed item.
- Every new `@PluginDescriptor`-annotated class needs an explicit entry in `src/test/java/com/example/ExamplePluginTest.java`'s `loadBuiltin(...)` call, or the dev harness silently never starts it — this bit every prior Controller-family plugin (see the file's own comment).
- Follow the resolved config-fallback pattern from `routines/tormented_demon.py`/`routines/firemaking.py` (`get_config` calls the bridge endpoint, falls back to `DEFAULT_CONFIG` only if the plugin isn't loaded/running).
- Any one-time-per-activation account-state check (e.g. a level requirement) must run from inside `tick()`, gated by a flag in `ctx.state.data`, **never** from a `setup()` hook — `controller.py`'s main loop calls `routine.setup(ctx)` at routine-load time, *before* the `login.is_fully_logged_in()` gate, so a `setup()`-based check can read pre-login/stale data and false-trigger a permanent safety stop. (`routines/firemaking.py`'s final review caught exactly this bug; `check_level_requirement` here must not repeat it.)
- Small per-file object/item lookup helpers (`find_item`-by-substring, `find_object`-by-name-and-distance) are duplicated locally in every routine file that needs them (`mining.py`, `gemstone_crab.py`, `banker.py`, `travel.py`, `firemaking.py` each have their own) rather than shared from one place — follow that established convention here too; do not hoist a shared helper into `controller.py` as part of this plan.
- Substring item-name matching (`name.lower() in item_name.lower()`) is the established convention throughout this codebase — keep using it, with the same known sharp edge already documented in `firemaking.py` (a short configured name can accidentally match a longer item name containing it as a substring).

### Blast Furnace mechanics reference (from the local OSRS wiki mirror)

Sourced from `osrs-wiki-data-master/raw/B/Blast Furnace.txt`, `raw/B/Blast Furnace_Strategies.txt`, `raw/C/Coffer (Blast Furnace).txt`, `raw/C/Conveyor belt (Blast Furnace).txt`, `raw/B/Bar dispenser.txt`, `raw/C/Coal bag.txt`, and `raw/M/Money making guide_Smelting runite bars at Blast Furnace.txt`.

- **"Mass world" = an official Blast Furnace world** (`Blast Furnace.txt:43`): on these ~22 themed worlds, five dwarves operate the pedals/pump/stove/cogs automatically — the player only loads the conveyor belt and collects bars from the dispenser. This plan targets **only** this mode; manual operation (pedals, pump, stove, temperature gauge, coke/hammer repairs) is explicitly out of scope.
- **Prerequisites assumed already met, not verified by this routine** (matching this codebase's existing convention of documenting rather than enforcing account-state prerequisites, e.g. `tormented_demon.py` assuming you've already walked to the demon): standing in the Blast Furnace basement on an official/mass world; `The Giant Dwarf` quest done (`Blast Furnace.txt:26`); Ice gloves or Smiths gloves (i) equipped so bars never need bucket-of-water cooling (`Blast Furnace.txt:31`, `Bar dispenser.txt:31`); already own a coal bag (`Coal bag.txt` — purchased from Prospector Percy, this routine never buys one).
- **Coffer fee** (`Blast Furnace.txt:29`, `Coffer (Blast Furnace).txt:18`): 72,000 coins/hour = 1,200 coins/minute = 12 coins/tick, only drains while logged in and inside the Blast Furnace basement on a themed world. Deposits only work on themed worlds; withdrawals work anywhere. There is **no bridge endpoint exposing the coffer's live balance** — this plan's top-up is a blind, time-based deposit (deposit a configured amount every configured interval), not balance-aware. Documented limitation, not solved here.
- **Coffer interaction** (`Coffer (Blast Furnace).txt` infobox: `options = Use`): clicking the coffer is assumed to open a chatbox "Enter amount" numeric prompt (the same UI pattern used elsewhere in OSRS for depositing into a container), **not yet verified live** — flagged in code, same epistemic honesty as `tormented_demon.py`'s reflection-based caveats and `travel.py`'s Forced-Actions notes.
- **Conveyor belt** (`Conveyor belt (Blast Furnace).txt`: `options = Put-ore-on`): loading ore/coal is a **two-click "use" action** — click the ore/coal item in inventory first, then click the conveyor belt object — the same item-then-target idiom `routines/firemaking.py` already established for tinderbox+log, just targeting a world object as the second click instead of a second inventory item.
- **Coal-before-ore ordering matters** (`Blast Furnace.txt:184`, money-making guide for steel bars): if iron ore lands in the furnace before coal, the furnace yields iron bars instead of steel. Every trip pattern below deposits coal first, ore second, to avoid this.
- **Coal bag deposit sequence** (`Money making guide_Smelting runite bars at Blast Furnace.txt:46`: *"emptying your coal bag of coal and depositing that right after"*): there is **no direct bag-to-conveyor shortcut**. The sequence is: empty the bag into inventory (a normal `Empty` click on the bag, same mechanic as this codebase's existing `banker.empty_looting_bag`), then deposit each resulting coal item onto the conveyor individually, same as loose coal.
- **Coal bag capacity** (`Coal bag.txt:34`): 27 coal (36 with a Smithing cape — this plan uses 27 uniformly and doesn't special-case the cape).
- **Bar dispenser** (`Bar dispenser.txt:16-23,31,35`): `Take` collects bars regardless of hot/cooled state given ice gloves; up to 28 bars held per type before the furnace refuses more primary ore ("You should collect your bars before making any more" — `Blast Furnace_Strategies.txt:23`). The trip patterns below (taken directly from the wiki) never exceed this.
- **Per-bar-type coal-bag trip patterns** (`Blast Furnace_Strategies.txt:101-176`, "Bar patterns" table, which explicitly assumes a coal bag + ice gloves): reproduced verbatim below (Level / Bar / ordered trips, each trip either loading materials or loading-and-collecting):

  | Level | Bar | Trips |
  |---|---|---|
  | 1 | Bronze | 14 Copper, 14 Tin, **Collect** |
  | 15 | Iron | 28 Iron, **Collect** |
  | 20 | Silver | 28 Silver, **Collect** |
  | 30 | Steel | 27 Coal, 27 Iron, **Collect** |
  | 40 | Gold | 27 Gold, **Collect** |
  | 50 | Mithril | 54 Coal → 27 Coal, 27 Mithril, **Collect** → 27 Coal, 27 Mithril, **Collect** |
  | 70 | Adamantite | 54 Coal → 27 Coal, 27 Adamantite, **Collect** |
  | 85 | Runite | 54 Coal → 54 Coal → 27 Coal, 27 Runite → 54 Coal, **Collect** → 27 Coal, 27 Runite, **Collect** |

  A "54 Coal" trip means the coal bag (27) plus 27 more loose coal withdrawn directly into inventory slots — this plan's bank-withdrawal planner computes that split automatically (see Task 4).
- **Deliberately out of scope** (documented trade-offs, matching this codebase's precedent of stating what a routine doesn't do rather than silently omitting it — e.g. `firemaking.py`'s "power firemaking" note): the hybrid gold-mixing method (`Blast Furnace_Strategies.txt:179-353`), world-hopping strategies, manual/pedal operation, the Smithing cape's 36-coal bag capacity, and buying ore from Ordan's shop. All ore/coal/bars move through the bank only.

---

### Task 1: Shared infrastructure — coffer digit-typing helper + coal bag Fill/Empty

**Files:**
- Modify: `ControllerAddon/controller.py` (new function, placed after `key_up` at line 362)
- Modify: `ControllerAddon/banker.py` (new functions, placed after `empty_looting_bag` at line 125)

**Interfaces:**
- Produces: `controller.type_digits_and_enter(ser: serial.Serial, text: str) -> None` — types each character of `text` via `key_tap`, then presses Enter. Used by Task 4's coffer top-up.
- Produces: `banker.has_coal_bag() -> bool`, `banker.fill_coal_bag(ctx) -> bool`, `banker.empty_coal_bag(ctx) -> bool` — mirror `has_looting_bag`/`empty_looting_bag`'s exact shape. Used by Task 4's bank/deposit phases.

- [ ] **Step 1: Add `type_digits_and_enter` to `controller.py`**

In `ControllerAddon/controller.py`, immediately after `key_up` (currently lines 357-362):

```python
def key_up(ser: serial.Serial, key: str) -> None:
    send_line(ser, f"KU {key}")


def type_digits_and_enter(ser: serial.Serial, text: str) -> None:
    """Type each character of `text` (expected to be a plain digit string, e.g. a coin
    amount) via key_tap, then press Enter -- for chatbox "Enter amount" numeric prompts
    (e.g. the Blast Furnace coffer's Use action, see routines/blast_furnace.py).

    ASSUMPTION, NOT YET VERIFIED LIVE: that the Arduino firmware accepts single-digit-
    character key names ("0".."9") the same way it already accepts named keys like "ESC"/
    "SPACE" elsewhere in this file, and that "ENTER" is a valid key name for it. Verify with
    a small manual test (e.g. typing an amount into any existing "Enter amount" prompt)
    before relying on this for an unattended coffer top-up -- same epistemic honesty as this
    project's other live-verification-needed assumptions (see travel.py's Forced Actions
    notes, tormented_demon.py's reflection caveats)."""
    for ch in text:
        key_tap(ser, ch)
        time.sleep(random.uniform(0.05, 0.12))
    key_tap(ser, "ENTER")
    time.sleep(random.uniform(0.2, 0.4))
```

- [ ] **Step 2: Add coal bag Fill/Empty helpers to `banker.py`**

In `ControllerAddon/banker.py`, immediately after `empty_looting_bag` (currently lines 108-125):

```python
COAL_BAG_NAME = "coal bag"


def has_coal_bag() -> bool:
    return any(COAL_BAG_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def fill_coal_bag(ctx) -> bool:
    """Click Fill on the coal bag to top it up (to 27 coal, 36 with a Smithing cape -- this
    project doesn't special-case the cape) from an open bank. "Fill" is listed first among
    the item's own options (Fill, Open, Check, Empty, Destroy), so it's likely this project's
    plain-left-click default the way most single-primary-action tools already are elsewhere
    in this codebase -- unlike Empty below, this one is NOT expected to need Forced Actions,
    but verify live."""
    bag = next(
        (i for i in controller.get_inventory() if COAL_BAG_NAME in (i.get("name") or "").lower()),
        None,
    )
    if bag is None:
        return False

    print("[bank] filling coal bag")
    if not controller.click_entity(ctx.ser, bag):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True


def empty_coal_bag(ctx) -> bool:
    """Click Empty on the coal bag to dump its coal into inventory -- e.g. before depositing
    it onto the Blast Furnace conveyor belt one piece at a time, since there's no direct
    bag-to-object deposit shortcut (see routines/blast_furnace.py). Same situation as
    empty_looting_bag above: "Empty" is not the item's first-listed (default) option, so add
    it to Controller Settings -> Clicking -> Forced Actions if a plain click doesn't already
    resolve to it."""
    bag = next(
        (i for i in controller.get_inventory() if COAL_BAG_NAME in (i.get("name") or "").lower()),
        None,
    )
    if bag is None:
        return False

    print("[bank] emptying coal bag")
    if not controller.click_entity(ctx.ser, bag):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True
```

- [ ] **Step 3: Syntax check**

Run: `python -m py_compile ControllerAddon/controller.py ControllerAddon/banker.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/banker.py
git commit -m "Add coffer digit-typing helper and coal bag Fill/Empty (shared infra for Blast Furnace)"
```

---

### Task 2: `BlastFurnaceBarType`/`BlastFurnaceConfig`/`BlastFurnacePlugin` — settings panel + bridge endpoint

**Files:**
- Create: `src/main/java/com/example/controller/BlastFurnaceBarType.java`
- Create: `src/main/java/com/example/controller/BlastFurnaceConfig.java`
- Create: `src/main/java/com/example/controller/BlastFurnacePlugin.java`

**Interfaces:**
- Produces: `BlastFurnaceBarType` enum (`BRONZE`, `IRON`, `SILVER`, `STEEL`, `GOLD`, `MITHRIL`, `ADAMANTITE`, `RUNITE`), each with a Smithing level requirement.
- Produces: `GET /blast-furnace-config` returning `{"enabled": bool, "barType": str (lowercase enum name, e.g. "iron"), "coffer": {"topUpEnabled": bool, "topUpAmount": int, "topUpIntervalMinutes": int}, "bank": {"teleportFallback": bool}, "breaks": {...same shape as every other routine's breaks export...}, "safety": {"stopOnDeath": bool, "maxRuntimeHours": int}}` — this exact shape is what Task 4's `routines/blast_furnace.py` reads.

- [ ] **Step 1: Create `BlastFurnaceBarType.java`**

```java
package com.example.controller;

/**
 * Which bar the Blast Furnace routine smelts. .name().toLowerCase() (done by
 * BlastFurnacePlugin's export) must match a key in routines/blast_furnace.py's BAR_TYPES
 * dict exactly -- keep both in sync if either changes. Levels are the Smithing requirement
 * to smelt that bar, per Blast Furnace.txt's "Experience rates" table.
 */
public enum BlastFurnaceBarType
{
	BRONZE("Bronze", 1),
	IRON("Iron", 15),
	SILVER("Silver", 20),
	STEEL("Steel", 30),
	GOLD("Gold", 40),
	MITHRIL("Mithril", 50),
	ADAMANTITE("Adamantite", 70),
	RUNITE("Runite", 85);

	private final String label;
	private final int level;

	BlastFurnaceBarType(String label, int level)
	{
		this.label = label;
		this.level = level;
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

- [ ] **Step 2: Create `BlastFurnaceConfig.java`**

```java
package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

@ConfigGroup(BlastFurnaceConfig.GROUP)
public interface BlastFurnaceConfig extends Config
{
	String GROUP = "controllerBlastFurnace";

	@ConfigSection(name = "General", description = "Which bar to smelt", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Coffer", description = "Automatic coffer top-up to keep the dwarves working", position = 1)
	String cofferSection = "coffer";

	@ConfigSection(name = "Banking", description = "Bank trip behavior", position = 2)
	String bankSection = "bank";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule", position = 3)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits", position = 4)
	String safetySection = "safety";

	// --- General ---

	@ConfigItem(
		keyName = "blastFurnaceEnabled",
		name = "Blast Furnace Enabled",
		description = "Secondary switch on top of launching controller.py with routines/blast_furnace.py.",
		section = generalSection,
		position = 0
	)
	default boolean blastFurnaceEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "barType",
		name = "Bar Type",
		description = "Which bar to smelt -- determines the ore/coal trip pattern and the Smithing level check.",
		section = generalSection,
		position = 1
	)
	default BlastFurnaceBarType barType()
	{
		return BlastFurnaceBarType.IRON;
	}

	// --- Coffer ---

	@ConfigItem(
		keyName = "cofferTopUpEnabled",
		name = "Auto Top Up Coffer",
		description = "Periodically deposit coins into the coffer so the dwarves keep operating the furnace. Relies on an unverified assumption that using the coffer opens a chatbox \"Enter amount\" prompt -- see routines/blast_furnace.py's module header.",
		section = cofferSection,
		position = 0
	)
	default boolean cofferTopUpEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "cofferTopUpAmount",
		name = "Top Up Amount (coins)",
		description = "How many coins to deposit into the coffer each top-up.",
		section = cofferSection,
		position = 1
	)
	@Range(min = 1, max = 1000000)
	default int cofferTopUpAmount()
	{
		return 40000;
	}

	@ConfigItem(
		keyName = "cofferTopUpIntervalMinutes",
		name = "Top Up Every (minutes)",
		description = "How often to deposit the amount above. The coffer drains 1,200 coins/minute while active, so the amount above should comfortably cover this interval.",
		section = cofferSection,
		position = 2
	)
	@Range(min = 1, max = 120)
	default int cofferTopUpIntervalMinutes()
	{
		return 30;
	}

	// --- Banking ---

	@ConfigItem(
		keyName = "bankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the ring of dueling to Ferox Enclave when no bank is within walking distance. Shouldn't normally trigger right at the Blast Furnace's own bank chest.",
		section = bankSection,
		position = 0
	)
	default boolean bankTeleportFallback()
	{
		return false;
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

- [ ] **Step 3: Create `BlastFurnacePlugin.java`**

```java
package com.example.controller;

import com.google.inject.Provides;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDependency;
import net.runelite.client.plugins.PluginDescriptor;

import javax.inject.Inject;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Same pattern as MiningPlugin/FiremakingPlugin -- see their headers. Registers
 * routines/blast_furnace.py's config under /blast-furnace-config on ControllerPlugin's
 * shared bridge server.
 */
@PluginDescriptor(
	name = "* Controller - Blast Furnace",
	description = "Blast Furnace routine settings for the external controller -- see the main HttpBridge/Controller plugin for Profile selection.",
	tags = {"controller", "config", "blast", "furnace", "smithing", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class BlastFurnacePlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private BlastFurnaceConfig config;

	@Override
	protected void startUp()
	{
		controllerPlugin.getServer().register("/blast-furnace-config", () -> export(config));
	}

	@Override
	protected void shutDown()
	{
	}

	@Provides
	BlastFurnaceConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(BlastFurnaceConfig.class);
	}

	private static Map<String, Object> export(BlastFurnaceConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.blastFurnaceEnabled());
		// Lowercased to match routines/blast_furnace.py's BAR_TYPES dict keys exactly.
		data.put("barType", config.barType().name().toLowerCase(Locale.ROOT));

		Map<String, Object> coffer = new LinkedHashMap<>();
		coffer.put("topUpEnabled", config.cofferTopUpEnabled());
		coffer.put("topUpAmount", config.cofferTopUpAmount());
		coffer.put("topUpIntervalMinutes", config.cofferTopUpIntervalMinutes());
		data.put("coffer", coffer);

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("teleportFallback", config.bankTeleportFallback());
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

- [ ] **Step 4: Compile check**

Run: `.\gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`, no errors referencing `BlastFurnaceBarType`, `BlastFurnaceConfig`, or `BlastFurnacePlugin`.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/controller/BlastFurnaceBarType.java src/main/java/com/example/controller/BlastFurnaceConfig.java src/main/java/com/example/controller/BlastFurnacePlugin.java
git commit -m "Add Blast Furnace settings panel + /blast-furnace-config bridge endpoint"
```

---

### Task 3: Wire the new plugin into the routine picker and dev harness

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `src/test/java/com/example/ExamplePluginTest.java`
- Modify: `ControllerAddon/controller.py` (new `get_blast_furnace_config()` helper + `ROUTINE_FILES` entry)

**Interfaces:**
- Consumes: `BlastFurnacePlugin.class` from Task 2.
- Produces: `RoutineChoice.BLAST_FURNACE` enum constant, whose `.name()` (`"BLAST_FURNACE"`) is the exact string `ROUTINE_FILES`'s new key must match. `controller.get_blast_furnace_config() -> dict | None`, consumed by Task 4.

- [ ] **Step 1: Add `BLAST_FURNACE` to `RoutineChoice.java`**

In `src/main/java/com/example/controller/RoutineChoice.java`, change:

```java
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon"),
	FIREMAKING("Firemaking");
```

to:

```java
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon"),
	FIREMAKING("Firemaking"),
	BLAST_FURNACE("Blast Furnace");
```

- [ ] **Step 2: Register `BlastFurnacePlugin` in the dev harness**

In `src/test/java/com/example/ExamplePluginTest.java`, replace the full file with:

```java
package com.example;

import com.example.controller.BlastFurnacePlugin;
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
			FiremakingPlugin.class,
			BlastFurnacePlugin.class
		);
		RuneLite.main(args);
	}
}
```

- [ ] **Step 3: Add `get_blast_furnace_config()` and register the routine file in `controller.py`**

In `ControllerAddon/controller.py`, immediately after `get_skills` (currently lines 169-170):

```python
def get_skills():
    return api_get("/skills") or {}


def get_blast_furnace_config():
    return api_get("/blast-furnace-config")
```

And change the `ROUTINE_FILES` map (was at lines 1075-1081 before Task 1's edit above shifted everything below it down by ~15 lines -- match on the dict's content below, not the line number):

```python
ROUTINE_FILES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
    "GEMSTONE_CRAB": "routines/gemstone_crab.py",
    "TORMENTED_DEMON": "routines/tormented_demon.py",
    "FIREMAKING": "routines/firemaking.py",
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
    "BLAST_FURNACE": "routines/blast_furnace.py",
}
```

- [ ] **Step 4: Compile/syntax check**

Run: `.\gradlew.bat compileJava .\gradlew.bat compileTestJava`
Run: `python -m py_compile ControllerAddon/controller.py`
Expected: `BUILD SUCCESSFUL`; no py_compile output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/controller/RoutineChoice.java src/test/java/com/example/ExamplePluginTest.java ControllerAddon/controller.py
git commit -m "Register Blast Furnace in RoutineChoice, dev harness, and ROUTINE_FILES"
```

---

### Task 4: `routines/blast_furnace.py` — the routine itself

**Files:**
- Create: `ControllerAddon/routines/blast_furnace.py`

**Interfaces:**
- Consumes: `controller.get_blast_furnace_config()`, `controller.get_skills()`, `controller.get_combat()` (unused directly but `get_inventory()`/`get_objects()` are), `controller.get_inventory()`, `controller.get_objects()`, `controller.click_entity(ser, entity, ...)`, `controller.open_inventory_tab(ser)`, `controller.type_digits_and_enter(ser, text)` (Task 1), `banker.find_nearby_bank(cfg)`, `banker.open_bank(ctx, obj)`, `banker.deposit_inventory(ctx, cfg)`, `banker.withdraw_loadout(ctx, cfg)`, `banker.close_bank(ctx)`, `banker.bank_up(ctx, cfg)`, `banker.fill_coal_bag(ctx)`, `banker.empty_coal_bag(ctx)` (Task 1).
- Produces: `DEFAULT_CONFIG` (dict), `get_config(bridge_config)`, `tick(ctx)` — the routine contract every `routines/*.py` module implements (`controller.py:17-59`).

- [ ] **Step 1: Create `ControllerAddon/routines/blast_furnace.py`**

```python
# routines/blast_furnace.py
#
# Smelt a configured bar type at the Blast Furnace on an official ("mass") Blast Furnace
# world: bank materials, load coal (via a coal bag) then ore onto the conveyor belt
# following the OSRS Wiki's per-bar-type trip patterns, collect bars, repeat -- plus a
# periodic blind coffer top-up so the dwarves keep operating the machinery unattended.
#
# Mechanics (from the local OSRS wiki mirror, osrs-wiki-data-master/raw/B/Blast Furnace.txt,
# raw/B/Blast Furnace_Strategies.txt, raw/C/Coffer (Blast Furnace).txt, raw/C/Conveyor belt
# (Blast Furnace).txt, raw/B/Bar dispenser.txt, raw/C/Coal bag.txt):
#   - "Mass world" means an official Blast Furnace world, where dwarves handle the
#     pedals/pump/stove/cogs automatically -- this routine only loads the conveyor belt and
#     collects bars. Manual operation is out of scope entirely.
#   - PREREQUISITES ASSUMED, NOT VERIFIED: standing in the Blast Furnace basement on a mass
#     world; Ice gloves or Smiths gloves (i) equipped (so bars never need bucket-of-water
#     cooling); already own a coal bag.
#   - Loading the conveyor belt ("Put-ore-on") is a two-click "use" action -- click the
#     ore/coal item, then click the conveyor belt -- same item-then-target idiom
#     routines/firemaking.py established for tinderbox+log, just with a world object as the
#     second click.
#   - Coal must land before the primary ore or the furnace yields the wrong (lower-tier) bar
#     -- every trip pattern below deposits coal first.
#   - The coal bag has no direct bag-to-conveyor shortcut: its coal is emptied into inventory
#     first (banker.empty_coal_bag), then deposited item-by-item like loose coal.
#   - The coffer drains 1,200 coins/minute while active and has no bridge-exposed live
#     balance -- top-up here is a blind, time-based deposit, not balance-aware.
#   - ASSUMPTION, NOT YET VERIFIED LIVE: clicking the coffer (its only option is "Use") opens
#     a chatbox "Enter amount" numeric prompt that controller.type_digits_and_enter can fill
#     in. Verify this live before relying on unattended coffer top-up.
#   - Deliberately NOT implemented: the hybrid gold-mixing method, world-hopping strategies,
#     manual/pedal operation, the Smithing cape's 36-coal bag capacity, buying ore from
#     Ordan's shop.
#
# Run with:
#     python controller.py routines/blast_furnace.py

import random
import time

import banker
import controller

COAL_BAG_CAPACITY = 27

# Per-bar-type trip patterns, reproduced verbatim from Blast Furnace_Strategies.txt's "Bar
# patterns" table (which assumes a coal bag + ice gloves). Each trip is a "deposit" list of
# (item name, quantity) pairs -- coal always listed first, per the coal-before-ore ordering
# rule above -- and a "collect" flag for whether to gather bars at the end of that trip.
# tick() advances through a bar type's trip list in order and wraps back to the start after
# the last one, so single-trip bar types (bronze/iron/silver/steel/gold) just repeat trip 0
# forever with no special-casing needed.
BAR_TYPES = {
    "bronze": {
        "level": 1,
        "trips": [
            {"deposit": [("Copper ore", 14), ("Tin ore", 14)], "collect": True},
        ],
    },
    "iron": {
        "level": 15,
        "trips": [
            {"deposit": [("Iron ore", 28)], "collect": True},
        ],
    },
    "silver": {
        "level": 20,
        "trips": [
            {"deposit": [("Silver ore", 28)], "collect": True},
        ],
    },
    "steel": {
        "level": 30,
        "trips": [
            {"deposit": [("Coal", 27), ("Iron ore", 27)], "collect": True},
        ],
    },
    "gold": {
        "level": 40,
        "trips": [
            {"deposit": [("Gold ore", 27)], "collect": True},
        ],
    },
    "mithril": {
        "level": 50,
        "trips": [
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 27), ("Mithril ore", 27)], "collect": True},
            {"deposit": [("Coal", 27), ("Mithril ore", 27)], "collect": True},
        ],
    },
    "adamantite": {
        "level": 70,
        "trips": [
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 27), ("Adamantite ore", 27)], "collect": True},
        ],
    },
    "runite": {
        "level": 85,
        "trips": [
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 27), ("Runite ore", 27)], "collect": False},
            {"deposit": [("Coal", 54)], "collect": True},
            {"deposit": [("Coal", 27), ("Runite ore", 27)], "collect": True},
        ],
    },
}

# Used only if BlastFurnacePlugin isn't loaded/enabled in RuneLite (so /blast-furnace-config
# comes back None) -- keeps this routine runnable standalone, same as tormented_demon.py.
DEFAULT_CONFIG = {
    "enabled": True,
    "barType": "iron",

    "coffer": {
        "topUpEnabled": True,
        "topUpAmount": 40000,
        "topUpIntervalMinutes": 30,
    },
    "bank": {
        "teleportFallback": False,
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
    config = controller.get_blast_furnace_config()
    return config if config is not None else DEFAULT_CONFIG


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check, run from inside tick() (never
    setup() -- see this plan's Global Constraints for why setup() would false-trigger a
    permanent safety stop pre-login). Gated by a ctx.state.data flag so it runs exactly once
    per routine-activation -- state.data is reset to {} by controller.py whenever the active
    routine file changes."""
    bar_type = (config.get("barType") or "iron").lower()
    pattern = BAR_TYPES.get(bar_type)
    if pattern is None:
        return  # unknown/custom bar type -- can't validate, don't block

    smithing = controller.get_skills().get("Smithing") or {}
    level = smithing.get("level")
    if level is not None and level < pattern["level"]:
        ctx.state.trigger_safety_stop(
            f"Smithing level {level} is below the {pattern['level']} required for {bar_type} bars"
        )


def maybe_top_up_coffer(ctx, config: dict) -> None:
    """Blind, time-based coffer top-up -- see module header for why this can't be
    balance-aware. Checked once per tick alongside the main trip logic, same as how other
    routines interleave periodic side-actions (e.g. slayer.py's supply/prayer checks) with
    their main loop."""
    coffer_cfg = config.get("coffer", {})
    if not coffer_cfg.get("topUpEnabled"):
        return

    now = time.time()
    next_at = ctx.state.data.get("bf_coffer_next_topup_at")
    if next_at is not None and now < next_at:
        return

    interval_s = coffer_cfg.get("topUpIntervalMinutes", 30) * 60
    coffer = find_object(config, "Coffer")
    if coffer is None:
        print("[blast furnace] coffer not found nearby -- will retry shortly")
        ctx.state.data["bf_coffer_next_topup_at"] = now + 60
        return

    amount = coffer_cfg.get("topUpAmount", 40000)
    print(f"[blast furnace] topping up coffer with {amount} coins")
    controller.click_entity(ctx.ser, coffer)
    time.sleep(random.uniform(0.8, 1.2))  # wait for the amount prompt to appear
    controller.type_digits_and_enter(ctx.ser, str(amount))
    time.sleep(random.uniform(0.4, 0.8))
    ctx.state.data["bf_coffer_next_topup_at"] = now + interval_s


def plan_bank_withdrawals(trip: dict) -> tuple:
    """Given one trip's deposit list, compute the bank withdraw list and whether the coal
    bag needs filling. A "Coal" entry over COAL_BAG_CAPACITY splits into a full bag (27) plus
    loose coal withdrawn directly into inventory for the remainder (e.g. 54 Coal -> fill the
    bag, withdraw 27 loose)."""
    withdraw = []
    need_bag_fill = False
    for name, qty in trip["deposit"]:
        if name.lower() == "coal":
            need_bag_fill = True
            loose = max(0, qty - COAL_BAG_CAPACITY)
            if loose > 0:
                withdraw.append({"name": "Coal", "quantity": loose})
        else:
            withdraw.append({"name": name, "quantity": qty})
    return withdraw, need_bag_fill


def run_trip(ctx, config: dict, trip: dict) -> bool:
    """Execute one full trip: bank for this trip's materials (depositing any bars/leftovers
    from the previous trip along the way, via the normal keepItems-aware deposit), walk to
    the conveyor belt, deposit coal (if any) before the primary ore, then collect bars if
    this trip ends with a collect. Returns False if something is missing and a safety-stop or
    retry was already handled; True once the trip's deposits (and any collect) completed."""
    withdraw, need_bag_fill = plan_bank_withdrawals(trip)
    bank_cfg = {"keepItems": ["coal bag"], "withdraw": withdraw}

    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is not None:
        if not banker.open_bank(ctx, bank_obj):
            ctx.state.trigger_safety_stop("failed to open the Blast Furnace bank chest")
            return False
        banker.deposit_inventory(ctx, bank_cfg)
        if need_bag_fill:
            banker.fill_coal_bag(ctx)
        banker.withdraw_loadout(ctx, bank_cfg)
        banker.close_bank(ctx)
    elif config.get("bank", {}).get("teleportFallback"):
        if not banker.bank_up(ctx, dict(bank_cfg, teleportFallback=True)):
            ctx.state.trigger_safety_stop("couldn't reach a bank to restock materials")
            return False
    else:
        ctx.state.trigger_safety_stop("no bank found near the Blast Furnace")
        return False

    conveyor = find_object(config, "Conveyor belt")
    if conveyor is None:
        ctx.state.trigger_safety_stop("conveyor belt not found nearby -- are you at the Blast Furnace?")
        return False

    if need_bag_fill:
        banker.empty_coal_bag(ctx)
        time.sleep(random.uniform(0.3, 0.6))

    controller.open_inventory_tab(ctx.ser)
    for name, qty in trip["deposit"]:
        deposited = deposit_items(ctx, name, qty, conveyor)
        if deposited < qty:
            ctx.state.trigger_safety_stop(
                f"only deposited {deposited}/{qty} {name} onto the conveyor belt -- ran out?"
            )
            return False

    if trip.get("collect"):
        dispenser = find_object(config, "Bar dispenser")
        if dispenser is None:
            ctx.state.trigger_safety_stop("bar dispenser not found nearby")
            return False
        print("[blast furnace] collecting bars")
        time.sleep(random.uniform(0.2, 0.4))
        controller.click_entity(ctx.ser, dispenser)
        time.sleep(random.uniform(0.6, 1.0))

    return True


def deposit_items(ctx, name: str, quantity: int, target: dict) -> int:
    """Deposit up to `quantity` inventory items named `name` onto `target` (the conveyor
    belt) via the item-then-target two-click idiom -- click the item, then click the
    conveyor. Ore/coal aren't stackable in inventory, so this is a real loop, one click pair
    per item, not a single bulk action. Each iteration re-looks-up the item fresh via
    find_item rather than reusing one slot reference repeatedly (unlike bank-slot
    withdrawals, which target a persistent bank-side stack): depositing an item removes it
    from its inventory slot and shifts the rest, so a stale reference from an earlier
    iteration could click whatever shifted into that slot instead. Returns how many were
    actually deposited, so the caller can tell a real shortfall (ran out of that item) from
    success."""
    deposited = 0
    for _ in range(quantity):
        item = find_item(name)
        if item is None:
            break
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        time.sleep(random.uniform(0.1, 0.2))
        controller.click_entity(ctx.ser, target)
        time.sleep(random.uniform(0.2, 0.4))
        deposited += 1
    return deposited


def tick(ctx):
    config = ctx.state.config

    if not ctx.state.data.get("bf_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["bf_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    # Soft-retry only -- maybe_top_up_coffer never calls trigger_safety_stop itself (a missed
    # coffer top-up shouldn't halt an otherwise-working session); if the player genuinely
    # isn't at the Blast Furnace at all, run_trip's own bank/conveyor checks below will catch
    # that and safety-stop instead.
    maybe_top_up_coffer(ctx, config)

    bar_type = (config.get("barType") or "iron").lower()
    pattern = BAR_TYPES.get(bar_type)
    if pattern is None:
        ctx.state.trigger_safety_stop(f"unknown bar type '{bar_type}'")
        return

    trips = pattern["trips"]
    index = ctx.state.data.get("bf_trip_index", 0) % len(trips)
    trip = trips[index]

    if not run_trip(ctx, config, trip):
        return

    ctx.state.data["bf_trip_index"] = (index + 1) % len(trips)


def find_item(name: str):
    """First inventory item whose name contains `name` (case-insensitive substring, same
    idiom as firemaking.py/travel.py/banker.py) -- kept local rather than shared, matching
    this codebase's established convention of duplicating small per-file lookup helpers."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if name_lower in (i.get("name") or "").lower()),
        None,
    )


def find_object(config: dict, name: str):
    """Closest visible object whose name contains `name`, within maxObjectDistance -- same
    filter shape as mining.py's pick_rock/banker.py's find_nearby_bank, kept local per this
    codebase's established convention."""
    name_lower = name.lower()
    max_dist = config.get("maxObjectDistance", 15)

    candidates = [
        o for o in controller.get_objects()
        if name_lower in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
```

- [ ] **Step 2: Syntax check**

Run: `python -m py_compile ControllerAddon/routines/blast_furnace.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Live verification (manual — no automated test framework covers routine behavior in this codebase)**

With the Blast Furnace-enabled build running in RuneLite (`BlastFurnacePlugin` loaded, settings panel configured with a real `barType` you're leveled for) and `python controller.py routines/blast_furnace.py` running against it, standing in the Blast Furnace basement on an official/mass world with a coal bag, Ice gloves (or Smiths gloves (i)) equipped, and coins in your bank:

1. **Coffer top-up first, in isolation**: manually click the coffer once and confirm it actually opens a chatbox "Enter amount" prompt (the whole design assumes this) — if it doesn't, `type_digits_and_enter`'s keystrokes will go nowhere useful and this needs a different approach before anything else is trusted.
2. Confirm the digit-typing itself lands correctly (right amount deposited, no stray characters) before leaving auto-top-up running unattended.
3. With `barType: iron` (no coal bag needed), confirm one full trip: bank withdraws 28 iron ore, walks to the conveyor, deposits all 28 (watch for the item-then-conveyor two-click pattern actually registering each one), collects bars from the dispenser, and loops back to bank again.
4. Switch to `barType: steel` and confirm coal is deposited **before** iron ore each trip (watch the actual deposit order), and that steel bars (not iron) come out.
5. Switch to `barType: mithril` (multi-trip) and confirm the 3-trip cycle runs in order (54 Coal only, no ore; then two 27 Coal + 27 Mithril + collect trips) and correctly wraps back to trip 1 afterward — this is the part most likely to reveal a state-machine bug.
6. Confirm the coal bag Fill (at the bank) and Empty (at the conveyor) clicks both resolve correctly — per the code's own caveat, Empty in particular may need "Empty" added to Controller Settings → Clicking → Forced Actions.
7. Deliberately let a trip run out of an ore/coal type mid-deposit (e.g. bank has less than the trip needs) and confirm it safety-stops with the "only deposited X/Y" message instead of silently mis-loading the furnace.
8. Confirm the configured break schedule and `Stop On Death`/`Max Runtime` still work (these come free from `controller.py`'s generic engine as long as the config dict has `breaks`/`safety` keys shaped correctly).

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/blast_furnace.py
git commit -m "Add routines/blast_furnace.py"
```

---

## Self-Review

**Spec coverage:**
- Bank → load conveyor (coal before ore) → collect bars → repeat, on a mass world → Task 4 `run_trip`/`deposit_items`/`tick`.
- Coal bag support (Fill at bank, Empty at conveyor) and the wiki's per-bar-type trip patterns → Task 1 (`fill_coal_bag`/`empty_coal_bag`) + Task 4 (`BAR_TYPES`, `plan_bank_withdrawals`).
- Automated coffer top-up → Task 1 (`type_digits_and_enter`) + Task 4 (`maybe_top_up_coffer`), both explicitly flagged as unverified new ground per the user's explicit choice to automate this.
- Settings panel (per this project's established per-routine-plugin convention) → Task 2.
- Live routine picker wiring → Task 3 (`RoutineChoice`, `ExamplePluginTest`, `ROUTINE_FILES`, `get_blast_furnace_config`).
- Under-level footgun, using the lesson from `firemaking.py`'s final review (check must live in `tick()`, not `setup()`) → Task 4 `check_level_requirement`, documented explicitly in Global Constraints so a reviewer or implementer can catch a regression to the old bug pattern immediately.
- Mechanics research from local wiki → embedded in Global Constraints reference section and in `routines/blast_furnace.py`'s own header comment.

**Placeholder scan:** No TBD/TODO markers; every step has literal file content, not a description of content.

**Type consistency:** `get_blast_furnace_config()` (Task 3) returns the exact dict shape `BlastFurnacePlugin.export()` (Task 2) produces, and `routines/blast_furnace.py`'s `DEFAULT_CONFIG` (Task 4) mirrors that shape key-for-key. `RoutineChoice.BLAST_FURNACE.name()` (Task 3) is `"BLAST_FURNACE"`, matching `ROUTINE_FILES["BLAST_FURNACE"]` (Task 3) exactly. `BAR_TYPES`' keys (`"bronze"`...`"runite"`, Task 4) match `BlastFurnaceBarType`'s `.name().toLowerCase()` values (Task 2) exactly.
