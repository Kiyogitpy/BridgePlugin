# Consolidate Routine Configs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the six standalone "\* Controller - X" RuneLite plugins (Slayer, Mining, Gemstone Crab, Tormented Demon, Firemaking, Blast Furnace) with ONE plugin (the existing "\* HttpBridge"/`ControllerPlugin`) whose settings screen shows every routine's settings as its own named, collapsible `@ConfigSection` — "Slayer", "Mining", "Gemstone Crab", "Tormented Demon", "Firemaking", "Blast Furnace" — alongside the existing shared sections (General, Prayer, Food & Potions, Breaks, Safety, Clicking, PvP Safety). Settings genuinely duplicated across multiple routines (break schedule, safety stops, eat-at-HP, restore-prayer-at) move into those shared sections instead of being repeated per routine.

**Architecture:** All seven previously-separate `Config` interfaces merge into one `ControllerConfig`, all owned by `ControllerPlugin`'s own Guice module (the only way RuneLite renders a plugin's settings screen at all — confirmed by this project's own commit history, see Global Constraints). Each routine keeps its own bridge endpoint (`/slayer-config`, `/mining-config`, etc.) and its exported JSON shape is preserved byte-for-byte, so **no Python file changes are needed anywhere** — `controller.py` and every `routines/*.py` file are untouched by this plan. The six standalone plugin classes, their six config interfaces, `SlayerConfig.java`, and the `SlayerConfigProvider`'s two-config-parameter shape are deleted/simplified; `ConfigExporter.java` grows to hold all six routines' export logic in one place, matching its existing role.

**Tech Stack:** Java 11 (RuneLite plugin API). No unit test framework exists in this codebase — verified by `.\gradlew.bat compileJava`/`compileTestJava`; there is no Python-side verification needed since no Python file changes.

## Global Constraints

- Java target: source/target compatibility 11 (`build.gradle`: `options.release.set(11)`).
- **RuneLite only renders a plugin's wrench-icon settings screen for `@ConfigGroup` interfaces that plugin's own Guice module `@Provides`** — confirmed empirically and documented in `SlayerPlugin.java`'s own header comment (the reason the six-plugin split existed in the first place). This is exactly why everything must move onto `ControllerPlugin`'s own module, not stay spread across separate `@Provides` bindings.
- **RuneLite's `@ConfigSection` has no nesting** — one flat list of `@ConfigItem`s per section, confirmed by disassembling the actual vendored `client-1.12.34.jar`'s `ConfigSection`/`ConfigItem` annotations (no "hidden-unless" mechanism of any kind exists, static or dynamic). Each routine's settings become ONE flat section (per the user's explicit choice), not nested sub-groups.
- **`@ConfigItem.keyName` — not the Java method name — is RuneLite's actual persistence key**, stored as `(GROUP, keyName)`. Since every routine's settings are moving into the SAME `@ConfigGroup` string (`ControllerConfig.GROUP = "controller"`, which `SlayerConfig` already reuses today with zero conflicts), every `keyName` across all seven interfaces must be checked for uniqueness within that one namespace — not just Java method names. Three `keyName` collisions exist between distinct, purpose-different settings (not true duplicates) and must be renamed (see the Collision Table below); every other setting keeps its exact original `keyName`, so **no user's existing saved config values reset** except for the three explicitly renamed items (documented below, and unavoidable — a genuinely different persistence key is required to keep them independently toggleable).
- Every `@ConfigItem` default must be a concrete value RuneLite's config UI can render — never `null` for a primitive-backed item (unchanged rule, verify no accidental regression while moving items).
- `ExamplePluginTest.java`'s dev harness only loads whatever's explicitly listed in its `loadBuiltin(...)` call — after this plan, that list shrinks to just `ControllerPlugin.class`.
- The newly-merged `dev` branch (post-sync) added `RoutineChoice.SHIPWRECK_SALVAGING` and a generic (non-per-routine-settings) `SailingProvider` — neither is touched by this plan. Shipwreck Salvaging has no settings plugin yet (`shipwreck_salvaging.py`'s `get_config()` just returns a local `DEFAULT_CONFIG`), so there is nothing of its to consolidate.

### Collision table (from a full field-by-field audit of all 7 config interfaces)

**True duplicates — hoisted to the existing shared root sections, routine-specific copies deleted** (per the user's explicit instruction: "the one that multiple has can be in http root config"). All of these already exist on `ControllerConfig`'s Breaks/Safety/Food & Potions/Prayer sections today (Slayer already reads them this way) — this plan just points the other five routines' exports at the same shared fields instead of their own duplicate copies:

| Field | Routines that had their own copy | Root default | Note |
|---|---|---|---|
| `takeBreaks`, `breakEveryMinMinutes`, `breakEveryMaxMinutes`, `breakDurationMinMinutes`, `breakDurationMaxMinutes`, `microBreaks` | Mining, Gemstone Crab, Tormented Demon, Firemaking, Blast Furnace | 45/75/3/12/true | ⚠️ Tormented Demon's own schedule was 30/50/3/10 (more frequent) — this distinct cadence is lost, unified to the shared 45/75/3/12 schedule. |
| `stopOnDeath`, `maxRuntimeHours` | Mining, Gemstone Crab, Tormented Demon, Firemaking, Blast Furnace | true / 0 | Identical everywhere already — no behavior change. |
| `stopOnInventoryFull` | Mining (default **true**), Gemstone Crab (false), Tormented Demon (false) | false | ⚠️ Mining's own default was `true` (deliberately, per its comment: no auto-bank logic, relies on this to stop when full). After this merge it defaults to the shared `false`. Mining will no longer auto-stop on a full inventory by default — the user can still turn the shared toggle on, but it then applies to whichever routine is active. |
| `eatAtHp` | Tormented Demon (default 50) | 42 | ⚠️ TD's distinct 50 threshold is lost, becomes the shared 42. |
| `restorePrayerAt` | Tormented Demon (default 30) | 18 | ⚠️ TD's distinct 30 threshold is lost, becomes the shared 18. |

**Same `keyName`, genuinely different purpose — renamed (Java method AND `keyName` string) to disambiguate**, since they can't share one persisted value without corrupting each other once merged into one `@ConfigGroup`:

| Original `keyName` | Kept as-is on | Renamed on | New `keyName` | Why not hoisted |
|---|---|---|---|---|
| `maxTargetDistance` | Slayer (general target-finding across any monster, default 15) | Tormented Demon (boss-specific, default 20) | `tormentedDemonMaxTargetDistance` | Different scope and appropriate value; not the same tunable concept. |
| `bankEnabled` | Slayer (combat banking on/off, default false) | Firemaking (log-restocking on/off, default false) | `firemakingBankEnabled` | Independently useful — a user may want one routine to auto-bank and not the other. |
| `bankTeleportFallback` | Slayer (default **true**) | Firemaking (default false) → `firemakingBankTeleportFallback`; Blast Furnace (default false) → `blastFurnaceBankTeleportFallback` | see previous column | Same reasoning as `bankEnabled`; three-way collision (Slayer/Firemaking/Blast Furnace all had this name). |

**Note on the renames:** anyone who already customized Tormented Demon's max target distance, or Firemaking's/Blast Furnace's bank-enabled/teleport-fallback toggles, will see those three specific settings reset to their defaults after this change (a genuinely new persistence key, unavoidable). Every other setting — including everything hoisted to the shared sections above — keeps its exact original `keyName` and does not reset.

**Everything else** (routine-unique settings like `rockNames`, `crabName`, `combatStyle`, `logName`, `barType`, `cofferTopUpAmount`, etc.) has no collision and moves across unchanged.

---

### Task 1: Rewrite `ControllerConfig.java` with all six routines' sections merged in

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerConfig.java` (full rewrite)

**Interfaces:**
- Produces: one `ControllerConfig` interface (`@ConfigGroup("controller")`) containing every `@ConfigItem` this project's settings screen exposes. This is purely additive to what Java code compiles against right now (the six old `Config` interfaces still exist untouched until Task 3 deletes them) — this task alone must still leave the project compiling, since nothing else references these new methods yet.

- [ ] **Step 1: Replace the full contents of `src/main/java/com/example/controller/ControllerConfig.java`**

```java
package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

/**
 * The single settings screen for every Controller routine, owned entirely by ControllerPlugin
 * -- RuneLite only renders a plugin's wrench-icon config screen for @ConfigGroup interfaces
 * that plugin's own Guice module @Provides (see ControllerPlugin's header), so everything has
 * to live in one interface here rather than split across per-routine plugins as before.
 *
 * Shared sections (General, Prayer, Food & Potions, Breaks, Safety, Clicking, PvP Safety) hold
 * settings genuinely reused by more than one routine -- every routine's export function reads
 * these directly instead of keeping its own duplicate copy. Everything below the shared
 * sections is one flat, routine-named section per routine (Slayer, Mining, Gemstone Crab,
 * Tormented Demon, Firemaking, Blast Furnace) holding only that routine's own settings.
 *
 * Three settings keep a routine-specific value despite sharing a concept with another routine
 * (maxTargetDistance, bankEnabled, bankTeleportFallback) -- these could not be hoisted to a
 * single shared field without losing independent tuning per routine, so their Java method
 * names (and keyName strings, to avoid two different settings silently sharing one persisted
 * value) are routine-prefixed instead: tormentedDemonMaxTargetDistance, firemakingBankEnabled,
 * firemakingBankTeleportFallback, blastFurnaceBankTeleportFallback.
 */
@ConfigGroup(ControllerConfig.GROUP)
public interface ControllerConfig extends Config
{
	String GROUP = "controller";

	// --- Shared sections ---

	@ConfigSection(name = "General", description = "Master controller controls", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Prayer", description = "Prayer usage", position = 1)
	String prayerSection = "prayer";

	@ConfigSection(name = "Food & Potions", description = "Healing and supplies", position = 2)
	String suppliesSection = "supplies";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule, shared by every routine", position = 3)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits, shared by every routine", position = 4)
	String safetySection = "safety";

	@ConfigSection(name = "Clicking", description = "Force which action a click resolves to", position = 5)
	String clickingSection = "clicking";

	@ConfigSection(name = "PvP Safety", description = "Wilderness flee-on-threat behavior", position = 6)
	String pvpSafetySection = "pvpSafety";

	// --- Per-routine sections (one flat section each) ---

	@ConfigSection(name = "Slayer", description = "Slayer/combat routine settings", position = 7)
	String slayerSection = "slayer";

	@ConfigSection(name = "Mining", description = "Mining routine settings", position = 8)
	String miningSection = "mining";

	@ConfigSection(name = "Gemstone Crab", description = "Gemstone Crab routine settings", position = 9)
	String gemstoneCrabSection = "gemstoneCrab";

	@ConfigSection(name = "Tormented Demon", description = "Tormented Demon routine settings", position = 10)
	String tormentedDemonSection = "tormentedDemon";

	@ConfigSection(name = "Firemaking", description = "Firemaking routine settings", position = 11)
	String firemakingSection = "firemaking";

	@ConfigSection(name = "Blast Furnace", description = "Blast Furnace routine settings", position = 12)
	String blastFurnaceSection = "blastFurnace";

	// ============================================================
	// General
	// ============================================================

	@ConfigItem(
		keyName = "controllerEnabled",
		name = "Controller Enabled",
		description = "Master switch. Python should only act when this is on.",
		section = generalSection,
		position = 0
	)
	default boolean controllerEnabled()
	{
		return false;
	}

	@ConfigItem(
		keyName = "routine",
		name = "Routine",
		description = "Which controller.py routine to run. Add new routines to controller.py's "
			+ "ROUTINE_FILES map to add options here. Defaults to None so nothing runs until you "
			+ "deliberately pick one.",
		section = generalSection,
		position = 1
	)
	default RoutineChoice routine()
	{
		return RoutineChoice.NONE;
	}

	// ============================================================
	// Prayer
	// ============================================================

	@ConfigItem(
		keyName = "usePrayer",
		name = "Use Prayer",
		description = "Enable prayer switching and potion drinking for prayer.",
		section = prayerSection,
		position = 0
	)
	default boolean usePrayer()
	{
		return false;
	}

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
		description = "Stat-boosting prayer to flick on right before each of your own attacks and off once it "
			+ "lands. Requires the separately-installed \"Attack Timer Metronome\" plugin to be enabled -- "
			+ "without it, this setting silently does nothing.",
		section = prayerSection,
		position = 2
	)
	default OffensivePrayerChoice offensivePrayer()
	{
		return OffensivePrayerChoice.NONE;
	}

	@ConfigItem(
		keyName = "useQuickPrayers",
		name = "Use Quick Prayers",
		description = "Toggle quick-prayer orb instead of individual prayers (uses your RuneLite quick-prayer setup).",
		section = prayerSection,
		position = 3
	)
	default boolean useQuickPrayers()
	{
		return false;
	}

	@ConfigItem(
		keyName = "restorePrayerAt",
		name = "Restore Prayer At",
		description = "Drink a prayer potion when prayer points drop to or below this value. Shared by every "
			+ "routine that manages prayer.",
		section = prayerSection,
		position = 4
	)
	@Range(min = 1, max = 99)
	default int restorePrayerAt()
	{
		return 18;
	}

	// ============================================================
	// Food & Potions
	// ============================================================

	@ConfigItem(
		keyName = "useFood",
		name = "Use Food",
		description = "Eat food when HP is low.",
		section = suppliesSection,
		position = 0
	)
	default boolean useFood()
	{
		return true;
	}

	@ConfigItem(
		keyName = "food",
		name = "Food",
		description = "Preferred food -- eaten first if more than one type is in your inventory.",
		section = suppliesSection,
		position = 1
	)
	default FoodChoice food()
	{
		return FoodChoice.SHARK;
	}

	@ConfigItem(
		keyName = "eatAtHp",
		name = "Eat At HP",
		description = "Eat when hitpoints drop to or below this value. Shared by every routine that manages food.",
		section = suppliesSection,
		position = 2
	)
	@Range(min = 1, max = 99)
	default int eatAtHp()
	{
		return 42;
	}

	@ConfigItem(
		keyName = "usePrayerPotions",
		name = "Use Prayer Potions",
		description = "Drink prayer restore potions when prayer is low.",
		section = suppliesSection,
		position = 3
	)
	default boolean usePrayerPotions()
	{
		return true;
	}

	@ConfigItem(
		keyName = "prayerPotion",
		name = "Prayer Potion",
		description = "Preferred prayer restore -- drunk first if more than one type is in your inventory.",
		section = suppliesSection,
		position = 4
	)
	default PrayerPotionChoice prayerPotion()
	{
		return PrayerPotionChoice.SUPER_RESTORE;
	}

	@ConfigItem(
		keyName = "useCombatPotions",
		name = "Use Combat Potions",
		description = "Drink combat potions when they expire (Python checks buff timers).",
		section = suppliesSection,
		position = 5
	)
	default boolean useCombatPotions()
	{
		return false;
	}

	@ConfigItem(
		keyName = "combatPotion",
		name = "Combat Potion",
		description = "Combat potion name (e.g. Super combat, Ranging potion).",
		section = suppliesSection,
		position = 6
	)
	default String combatPotion()
	{
		return "Super combat potion";
	}

	@ConfigItem(
		keyName = "useWildernessSupplies",
		name = "Use Wilderness Supplies",
		description = "Eat/drink and withdraw-when-banking the wilderness food/prayer potion below instead of the " +
			"normal ones (e.g. blighted variants that heal less but don't need banking runs out of the wilderness).",
		section = suppliesSection,
		position = 7
	)
	default boolean useWildernessSupplies()
	{
		return false;
	}

	@ConfigItem(
		keyName = "wildernessFood",
		name = "Wilderness Food",
		description = "Food used instead of Food above when Use Wilderness Supplies is on.",
		section = suppliesSection,
		position = 8
	)
	default FoodChoice wildernessFood()
	{
		return FoodChoice.BLIGHTED_MANTA_RAY;
	}

	@ConfigItem(
		keyName = "wildernessPrayerPotion",
		name = "Wilderness Prayer Potion",
		description = "Prayer restore used instead of Prayer Potion above when Use Wilderness Supplies is on.",
		section = suppliesSection,
		position = 9
	)
	default PrayerPotionChoice wildernessPrayerPotion()
	{
		return PrayerPotionChoice.BLIGHTED_SUPER_RESTORE;
	}

	// ============================================================
	// Breaks (shared by every routine)
	// ============================================================

	@ConfigItem(
		keyName = "takeBreaks",
		name = "Take Breaks",
		description = "Periodically pause the controller for human-like breaks.",
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
		description = "Take short 5–30 second pauses between actions for realism.",
		section = breaksSection,
		position = 5
	)
	default boolean microBreaks()
	{
		return true;
	}

	// ============================================================
	// Safety (shared by every routine)
	// ============================================================

	@ConfigItem(
		keyName = "stopOnDeath",
		name = "Stop On Death",
		description = "Stop the controller after dying.",
		section = safetySection,
		position = 1
	)
	default boolean stopOnDeath()
	{
		return true;
	}

	@ConfigItem(
		keyName = "stopOnInventoryFull",
		name = "Stop When Inventory Full",
		description = "Stop when inventory has no free slots.",
		section = safetySection,
		position = 2
	)
	default boolean stopOnInventoryFull()
	{
		return false;
	}

	@ConfigItem(
		keyName = "maxRuntimeHours",
		name = "Max Runtime (hours)",
		description = "Auto-stop after this many hours (0 = unlimited).",
		section = safetySection,
		position = 3
	)
	@Range(min = 0, max = 24)
	default int maxRuntimeHours()
	{
		return 0;
	}

	// ============================================================
	// Clicking
	// ============================================================

	@ConfigItem(
		keyName = "forceClickPriority",
		name = "Force Click Priority",
		description = "Same trick as shift-click forcing \"Walk here\": reorder the menu so Attack (target NPCs), " +
			"Take (loot whitelist items) and priority objects are what a plain click resolves to, even if " +
			"something else (like a tree in front of a cave entrance) would otherwise win.",
		section = clickingSection,
		position = 0
	)
	default boolean forceClickPriority()
	{
		return true;
	}

	@ConfigItem(
		keyName = "targetObjectNames",
		name = "Priority Objects",
		description = "Comma-separated object names (e.g. \"Cave\") that should always win a click over anything " +
			"else in the way, such as a tree blocking a cave mouth.",
		section = clickingSection,
		position = 1
	)
	default String targetObjectNames()
	{
		return "Cave";
	}

	@ConfigItem(
		keyName = "forcedActions",
		name = "Forced Actions",
		description = "Comma-separated menu option text (e.g. \"Ferox Enclave\") that should always win a click " +
			"regardless of target, for items/objects with multiple actions like a multi-teleport ring where the " +
			"default action isn't the one you want.",
		section = clickingSection,
		position = 2
	)
	default String forcedActions()
	{
		return "";
	}

	// ============================================================
	// PvP Safety
	// ============================================================

	@ConfigItem(
		keyName = "pvpFleeEnabled",
		name = "Flee From Attackers",
		description = "In the wilderness, teleport away via royal seed pod if another player is within the current wilderness level's attack range.",
		section = pvpSafetySection,
		position = 0
	)
	default boolean pvpFleeEnabled()
	{
		return false;
	}

	// ============================================================
	// Slayer (flattened from its former Targets/Loot/Banking/Travel sub-sections)
	// ============================================================

	@ConfigItem(
		keyName = "targetNpcNames",
		name = "Target NPCs",
		description = "Comma-separated NPC names. Use the Controller Settings sidebar panel to pick from nearby monsters.",
		section = slayerSection,
		position = 0
	)
	default String targetNpcNames()
	{
		return "";
	}

	@ConfigItem(
		keyName = "targetNpcIds",
		name = "Target NPC IDs",
		description = "Optional extra NPC IDs (comma-separated), added to names above.",
		section = slayerSection,
		position = 1
	)
	default String targetNpcIds()
	{
		return "";
	}

	@ConfigItem(
		keyName = "maxTargetDistance",
		name = "Max Target Distance",
		description = "Only attack NPCs within this many tiles.",
		section = slayerSection,
		position = 2
	)
	@Range(min = 1, max = 30)
	default int maxTargetDistance()
	{
		return 15;
	}

	@ConfigItem(
		keyName = "skipNpcInCombat",
		name = "Skip NPCs In Combat",
		description = "Do not attack NPCs already fighting another player.",
		section = slayerSection,
		position = 3
	)
	default boolean skipNpcInCombat()
	{
		return true;
	}

	@ConfigItem(
		keyName = "onlyAttackWhenIdle",
		name = "Only Attack When Idle",
		description = "Wait until you are not moving or in combat before finding a new target.",
		section = slayerSection,
		position = 4
	)
	default boolean onlyAttackWhenIdle()
	{
		return true;
	}

	@ConfigItem(
		keyName = "lootEnabled",
		name = "Pick Up Loot",
		description = "Enable looting ground items on the whitelist.",
		section = slayerSection,
		position = 5
	)
	default boolean lootEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "lootWhitelist",
		name = "Loot Whitelist",
		description = "Comma-separated item names to pick up.",
		section = slayerSection,
		position = 6
	)
	default String lootWhitelist()
	{
		return "";
	}

	@ConfigItem(
		keyName = "minLootValue",
		name = "Min Loot Value (GP)",
		description = "Also pick up any ground item worth at least this much GE value, regardless of the " +
			"whitelist above (e.g. a Rune platebody drop at 14k GE value gets picked up if this is set to 9000). " +
			"0 = off, whitelist only.",
		section = slayerSection,
		position = 7
	)
	default int minLootValue()
	{
		return 5000;
	}

	@ConfigItem(
		keyName = "buryBones",
		name = "Bury Bones",
		description = "Automatically bury bones from inventory.",
		section = slayerSection,
		position = 8
	)
	default boolean buryBones()
	{
		return false;
	}

	@ConfigItem(
		keyName = "bankEnabled",
		name = "Enable Banking",
		description = "Bank for more supplies when inventory is full instead of just stopping.",
		section = slayerSection,
		position = 9
	)
	default boolean bankEnabled()
	{
		return false;
	}

	@ConfigItem(
		keyName = "bankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the ring of dueling to Ferox Enclave when no bank is within walking distance.",
		section = slayerSection,
		position = 10
	)
	default boolean bankTeleportFallback()
	{
		return true;
	}

	@ConfigItem(
		keyName = "bankFoodQuantity",
		name = "Food To Withdraw",
		description = "How many of the Food item (Food & Potions section) to withdraw when banking. 0 = don't withdraw food.",
		section = slayerSection,
		position = 11
	)
	@Range(min = 0, max = 28)
	default int bankFoodQuantity()
	{
		return 10;
	}

	@ConfigItem(
		keyName = "bankPrayerPotionQuantity",
		name = "Prayer Potions To Withdraw",
		description = "How many of the Prayer Potion item to withdraw when banking. 0 = don't withdraw.",
		section = slayerSection,
		position = 12
	)
	@Range(min = 0, max = 28)
	default int bankPrayerPotionQuantity()
	{
		return 4;
	}

	@ConfigItem(
		keyName = "bankCombatPotionQuantity",
		name = "Combat Potions To Withdraw",
		description = "How many of the Combat Potion item to withdraw when banking. 0 = don't withdraw.",
		section = slayerSection,
		position = 13
	)
	@Range(min = 0, max = 28)
	default int bankCombatPotionQuantity()
	{
		return 0;
	}

	@ConfigItem(
		keyName = "bankKeepItems",
		name = "Never Deposit",
		description = "Comma-separated item names never deposited while banking (e.g. clue caskets, teleport items).",
		section = slayerSection,
		position = 14
	)
	default String bankKeepItems()
	{
		return "";
	}

	@ConfigItem(
		keyName = "taskLocationChoices",
		name = "Task Location Choices",
		description = "Managed by the Controller Settings sidebar panel -- which location was picked for each slayer task category that has more than one known spot (e.g. Ice giants: cave vs. Frozen Waste Plateau). Format: \"Category=index;Category2=index\".",
		section = slayerSection,
		position = 15
	)
	default String taskLocationChoices()
	{
		return "";
	}

	// ============================================================
	// Mining
	// ============================================================

	@ConfigItem(
		keyName = "miningEnabled",
		name = "Mining Enabled",
		description = "Secondary switch on top of launching controller.py with routines/mining.py.",
		section = miningSection,
		position = 0
	)
	default boolean miningEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "rockNames",
		name = "Rock Names",
		description = "Comma-separated rock names to mine, closest first (e.g. Tin rocks, Copper rocks).",
		section = miningSection,
		position = 1
	)
	default String rockNames()
	{
		return "Tin rocks, Copper rocks";
	}

	@ConfigItem(
		keyName = "maxRockDistance",
		name = "Max Rock Distance",
		description = "Only mine rocks within this many tiles.",
		section = miningSection,
		position = 2
	)
	@Range(min = 1, max = 30)
	default int maxRockDistance()
	{
		return 10;
	}

	// ============================================================
	// Gemstone Crab
	// ============================================================

	@ConfigItem(
		keyName = "gemstoneCrabEnabled",
		name = "Gemstone Crab Enabled",
		description = "Secondary switch on top of launching controller.py with routines/gemstone_crab.py.",
		section = gemstoneCrabSection,
		position = 0
	)
	default boolean gemstoneCrabEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "crabName",
		name = "Crab Name",
		description = "NPC name to attack.",
		section = gemstoneCrabSection,
		position = 1
	)
	default String crabName()
	{
		return "Gemstone Crab";
	}

	@ConfigItem(
		keyName = "tunnelName",
		name = "Tunnel Name",
		description = "Object name to follow the crab through when it flees underground. Also add this to " +
			"Controller Settings -> Clicking -> Priority Objects so a click near it can't resolve to a nearby " +
			"tree/rock instead.",
		section = gemstoneCrabSection,
		position = 2
	)
	default String tunnelName()
	{
		return "cave";
	}

	@ConfigItem(
		keyName = "maxDistance",
		name = "Max Distance",
		description = "Only engage the crab or its tunnel within this many tiles.",
		section = gemstoneCrabSection,
		position = 3
	)
	@Range(min = 1, max = 30)
	default int maxDistance()
	{
		return 25;
	}

	// ============================================================
	// Tormented Demon
	// ============================================================

	@ConfigItem(
		keyName = "tormentedDemonEnabled",
		name = "Tormented Demon Enabled",
		description = "Secondary switch on top of launching controller.py with routines/tormented_demon.py.",
		section = tormentedDemonSection,
		position = 0
	)
	default boolean tormentedDemonEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "tormentedDemonMaxTargetDistance",
		name = "Max Target Distance",
		description = "Only engage a Tormented Demon within this many tiles.",
		section = tormentedDemonSection,
		position = 1
	)
	@Range(min = 1, max = 30)
	default int tormentedDemonMaxTargetDistance()
	{
		return 20;
	}

	@ConfigItem(
		keyName = "combatStyle",
		name = "Combat Style",
		description = "Fixed fighting style used whenever Auto Swap Gear is off, or a given tick's weakness " +
			"reading is unavailable.",
		section = tormentedDemonSection,
		position = 2
	)
	default TormentedDemonCombatStyle combatStyle()
	{
		return TormentedDemonCombatStyle.MELEE;
	}

	@ConfigItem(
		keyName = "autoSwapGear",
		name = "Auto Swap Gear",
		description = "Automatically re-equip the matching weapon below as soon as the demon's overhead icon " +
			"(weakness) changes. Off by default -- relies on a reflection-based read of RuneLite internals " +
			"that has no official API guarantee and can silently stop working after a game update. See " +
			"routines/tormented_demon.py's module header before turning this on.",
		section = tormentedDemonSection,
		position = 3
	)
	default boolean autoSwapGear()
	{
		return false;
	}

	@ConfigItem(
		keyName = "meleeWeapon",
		name = "Melee Weapon",
		description = "Exact inventory item name, e.g. \"Ghrazi rapier\".",
		section = tormentedDemonSection,
		position = 4
	)
	default String meleeWeapon()
	{
		return "";
	}

	@ConfigItem(
		keyName = "rangedWeapon",
		name = "Ranged Weapon",
		description = "Exact inventory item name, e.g. \"Toxic blowpipe\".",
		section = tormentedDemonSection,
		position = 5
	)
	default String rangedWeapon()
	{
		return "";
	}

	@ConfigItem(
		keyName = "mageWeapon",
		name = "Mage Weapon",
		description = "Exact inventory item name, e.g. \"Sanguinesti staff\". Assumes Autocast is already " +
			"configured in-game for this staff -- this routine has no right-click/menu support to set that up " +
			"itself, only to equip the weapon.",
		section = tormentedDemonSection,
		position = 6
	)
	default String mageWeapon()
	{
		return "";
	}

	// ============================================================
	// Firemaking
	// ============================================================

	@ConfigItem(
		keyName = "firemakingEnabled",
		name = "Firemaking Enabled",
		description = "Secondary switch on top of launching controller.py with routines/firemaking.py.",
		section = firemakingSection,
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
		section = firemakingSection,
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
		section = firemakingSection,
		position = 2
	)
	default String tinderboxName()
	{
		return "Tinderbox";
	}

	@ConfigItem(
		keyName = "firemakingBankEnabled",
		name = "Enable Banking",
		description = "Bank for more logs when out instead of just stopping.",
		section = firemakingSection,
		position = 3
	)
	default boolean firemakingBankEnabled()
	{
		return false;
	}

	@ConfigItem(
		keyName = "firemakingBankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the ring of dueling to Ferox Enclave when no bank is within walking distance.",
		section = firemakingSection,
		position = 4
	)
	default boolean firemakingBankTeleportFallback()
	{
		return false;
	}

	@ConfigItem(
		keyName = "bankWithdrawQuantity",
		name = "Logs Per Trip",
		description = "How many logs to withdraw per bank trip (27 leaves exactly one slot free for the tinderbox in a 28-slot inventory).",
		section = firemakingSection,
		position = 5
	)
	@Range(min = 1, max = 27)
	default int bankWithdrawQuantity()
	{
		return 27;
	}

	// ============================================================
	// Blast Furnace
	// ============================================================

	@ConfigItem(
		keyName = "blastFurnaceEnabled",
		name = "Blast Furnace Enabled",
		description = "Secondary switch on top of launching controller.py with routines/blast_furnace.py.",
		section = blastFurnaceSection,
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
		section = blastFurnaceSection,
		position = 1
	)
	default BlastFurnaceBarType barType()
	{
		return BlastFurnaceBarType.IRON;
	}

	@ConfigItem(
		keyName = "cofferTopUpEnabled",
		name = "Auto Top Up Coffer",
		description = "Periodically deposit coins into the coffer so the dwarves keep operating the furnace. Relies on an unverified assumption that using the coffer opens a chatbox \"Enter amount\" prompt -- see routines/blast_furnace.py's module header.",
		section = blastFurnaceSection,
		position = 2
	)
	default boolean cofferTopUpEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "cofferTopUpAmount",
		name = "Top Up Amount (coins)",
		description = "How many coins to deposit into the coffer each top-up.",
		section = blastFurnaceSection,
		position = 3
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
		section = blastFurnaceSection,
		position = 4
	)
	@Range(min = 1, max = 120)
	default int cofferTopUpIntervalMinutes()
	{
		return 30;
	}

	@ConfigItem(
		keyName = "blastFurnaceBankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the ring of dueling to Ferox Enclave when no bank is within walking distance. Shouldn't normally trigger right at the Blast Furnace's own bank chest.",
		section = blastFurnaceSection,
		position = 5
	)
	default boolean blastFurnaceBankTeleportFallback()
	{
		return false;
	}
}
```

- [ ] **Step 2: Compile check**

Run: `.\gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL` (nothing else references these new methods yet, so this only needs to compile standalone -- it will not yet cause errors in the still-untouched old `SlayerConfig`/`MiningConfig`/etc. files, which remain separate valid interfaces for now).

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/example/controller/ControllerConfig.java
git commit -m "Merge all six routines' settings into ControllerConfig as flat per-routine sections"
```

---

### Task 2: Extend `ConfigExporter.java` with every routine's export logic

**Files:**
- Modify: `src/main/java/com/example/controller/ConfigExporter.java` (rename `export` to `exportSlayer`, add five new export methods)

**Interfaces:**
- Consumes: `ControllerConfig` (Task 1) — every export method now takes a single `ControllerConfig` parameter (plus `Client` for `exportSlayer`, which still needs to resolve NPC names to live IDs).
- Produces: `ConfigExporter.exportSlayer(Client, ControllerConfig)`, `exportMining(ControllerConfig)`, `exportGemstoneCrab(ControllerConfig)`, `exportTormentedDemon(ControllerConfig)`, `exportFiremaking(ControllerConfig)`, `exportBlastFurnace(ControllerConfig)` — each returns the **exact same JSON shape** its old per-plugin `export()` function produced, so `controller.py`'s `get_*_config()` functions and every `routines/*.py` file need zero changes.
- This task is still additive/non-breaking on its own — the six old `*Plugin.java` files still call their own private `export()` methods unchanged until Task 3 deletes them, so this must still compile standalone.

- [ ] **Step 1: Replace the full contents of `src/main/java/com/example/controller/ConfigExporter.java`**

```java
package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.NPC;

import java.util.*;
import java.util.stream.Collectors;

public final class ConfigExporter
{
	private ConfigExporter()
	{
	}

	// General combat mechanics (Prayer, Food & Potions, Breaks, Safety, PvP Safety) come from
	// the shared sections of ControllerConfig -- not slayer-task-specific, so shared rather
	// than duplicated per routine. Everything about which monster to fight, its loot, and
	// banking comes from the Slayer section of the same ControllerConfig.
	public static Map<String, Object> exportSlayer(Client client, ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();

		data.put("enabled", config.controllerEnabled());
		data.put("targets", resolveTargetIds(client, config));
		data.put("targetNames", parseList(config.targetNpcNames()));
		data.put("maxTargetDistance", config.maxTargetDistance());
		data.put("skipNpcInCombat", config.skipNpcInCombat());
		data.put("onlyAttackWhenIdle", config.onlyAttackWhenIdle());

		Map<String, Object> prayer = new LinkedHashMap<>();
		prayer.put("enabled", config.usePrayer());
		prayer.put("defensivePrayer", config.defensivePrayer().name());
		prayer.put("offensivePrayer", config.offensivePrayer().name());
		prayer.put("useQuickPrayers", config.useQuickPrayers());
		prayer.put("restoreAt", config.restorePrayerAt());
		data.put("prayer", prayer);

		String foodName = (config.useWildernessSupplies() ? config.wildernessFood() : config.food()).getItemName();
		String prayerPotionName = (config.useWildernessSupplies() ? config.wildernessPrayerPotion() : config.prayerPotion()).getItemName();

		Map<String, Object> food = new LinkedHashMap<>();
		food.put("enabled", config.useFood());
		food.put("name", foodName);
		food.put("eatAt", config.eatAtHp());
		data.put("food", food);

		Map<String, Object> potions = new LinkedHashMap<>();
		potions.put("prayerEnabled", config.usePrayerPotions());
		potions.put("prayerPotion", prayerPotionName);
		potions.put("combatEnabled", config.useCombatPotions());
		potions.put("combatPotion", config.combatPotion());
		data.put("potions", potions);

		Map<String, Object> loot = new LinkedHashMap<>();
		loot.put("enabled", config.lootEnabled());
		loot.put("items", parseList(config.lootWhitelist()));
		loot.put("minValue", config.minLootValue());
		loot.put("buryBones", config.buryBones());
		data.put("loot", loot);

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
		safety.put("stopOnInventoryFull", config.stopOnInventoryFull());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		data.put("safety", safety);

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("enabled", config.bankEnabled());
		bank.put("teleportFallback", config.bankTeleportFallback());
		bank.put("keepItems", parseList(config.bankKeepItems()));

		List<Map<String, Object>> withdraw = new ArrayList<>();
		addWithdrawEntry(withdraw, foodName, config.bankFoodQuantity());
		addWithdrawEntry(withdraw, prayerPotionName, config.bankPrayerPotionQuantity());
		addWithdrawEntry(withdraw, config.combatPotion(), config.bankCombatPotionQuantity());
		bank.put("withdraw", withdraw);

		data.put("bank", bank);

		Map<String, Object> pvpSafety = new LinkedHashMap<>();
		pvpSafety.put("fleeEnabled", config.pvpFleeEnabled());
		data.put("pvpSafety", pvpSafety);

		data.put("taskLocations", resolveTaskLocations(config));

		data.put("eatAt", config.eatAtHp());
		data.put("restorePrayerAt", config.restorePrayerAt());
		data.put("prayerPotion", prayerPotionName);

		return data;
	}

	public static Map<String, Object> exportMining(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.miningEnabled());
		data.put("rockNames", parseList(config.rockNames()));
		data.put("maxRockDistance", config.maxRockDistance());
		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafetyWithInventoryFull(config));
		return data;
	}

	public static Map<String, Object> exportGemstoneCrab(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.gemstoneCrabEnabled());
		data.put("crabName", config.crabName());
		data.put("tunnelName", config.tunnelName());
		data.put("maxDistance", config.maxDistance());
		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafetyWithInventoryFull(config));
		return data;
	}

	public static Map<String, Object> exportTormentedDemon(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.tormentedDemonEnabled());
		data.put("maxTargetDistance", config.tormentedDemonMaxTargetDistance());

		// Lowercased to match routines/tormented_demon.py's OVERHEAD_ICON_TO_STYLE / loadouts
		// dict keys ("melee"/"ranged"/"magic") exactly.
		data.put("combatStyle", config.combatStyle().name().toLowerCase(Locale.ROOT));
		data.put("autoSwapGear", config.autoSwapGear());

		Map<String, Object> loadouts = new LinkedHashMap<>();
		loadouts.put("melee", nullIfBlank(config.meleeWeapon()));
		loadouts.put("ranged", nullIfBlank(config.rangedWeapon()));
		loadouts.put("magic", nullIfBlank(config.mageWeapon()));
		data.put("loadouts", loadouts);

		Map<String, Object> food = new LinkedHashMap<>();
		food.put("enabled", true);
		food.put("eatAt", config.eatAtHp());
		data.put("food", food);

		Map<String, Object> prayer = new LinkedHashMap<>();
		prayer.put("restoreAt", config.restorePrayerAt());
		data.put("prayer", prayer);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}

	public static Map<String, Object> exportFiremaking(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.firemakingEnabled());
		data.put("logName", config.logName());
		data.put("tinderboxName", config.tinderboxName());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("enabled", config.firemakingBankEnabled());
		bank.put("teleportFallback", config.firemakingBankTeleportFallback());
		bank.put("keepItems", Collections.singletonList(config.tinderboxName()));

		List<Map<String, Object>> withdraw = new ArrayList<>();
		Map<String, Object> entry = new LinkedHashMap<>();
		entry.put("name", config.logName());
		entry.put("quantity", config.bankWithdrawQuantity());
		withdraw.add(entry);
		bank.put("withdraw", withdraw);
		data.put("bank", bank);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}

	public static Map<String, Object> exportBlastFurnace(ControllerConfig config)
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
		bank.put("teleportFallback", config.blastFurnaceBankTeleportFallback());
		data.put("bank", bank);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}

	/** Breaks shape shared by every routine's export -- read from ControllerConfig's shared
	 * Breaks section rather than a routine-specific duplicate (see this plan's Collision Table). */
	private static Map<String, Object> sharedBreaks(ControllerConfig config)
	{
		Map<String, Object> breaks = new LinkedHashMap<>();
		breaks.put("enabled", config.takeBreaks());
		breaks.put("everyMinMinutes", config.breakEveryMinMinutes());
		breaks.put("everyMaxMinutes", config.breakEveryMaxMinutes());
		breaks.put("durationMinMinutes", config.breakDurationMinMinutes());
		breaks.put("durationMaxMinutes", config.breakDurationMaxMinutes());
		breaks.put("microBreaks", config.microBreaks());
		return breaks;
	}

	/** Safety shape (stopOnDeath + maxRuntimeHours only) for routines whose export never
	 * included stopOnInventoryFull (Tormented Demon, Firemaking, Blast Furnace) -- unchanged
	 * from before, just reading the shared field instead of a routine-specific one. */
	private static Map<String, Object> sharedSafety(ControllerConfig config)
	{
		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", config.stopOnDeath());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		return safety;
	}

	/** Safety shape including stopOnInventoryFull, for routines whose export already included
	 * it (Mining, Gemstone Crab) -- unchanged shape, now reading the shared field. */
	private static Map<String, Object> sharedSafetyWithInventoryFull(ControllerConfig config)
	{
		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", config.stopOnDeath());
		safety.put("stopOnInventoryFull", config.stopOnInventoryFull());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		return safety;
	}

	private static String nullIfBlank(String s)
	{
		return s == null || s.trim().isEmpty() ? null : s.trim();
	}

	private static void addWithdrawEntry(List<Map<String, Object>> withdraw, String name, int quantity)
	{
		if (name == null || name.trim().isEmpty() || quantity <= 0)
		{
			return;
		}

		Map<String, Object> entry = new LinkedHashMap<>();
		entry.put("name", name.trim());
		entry.put("quantity", quantity);
		withdraw.add(entry);
	}

	/**
	 * Every slayer task category that has known wilderness location data (see
	 * SlayerLocations.java), each resolved to a single chosen location -- the one picked via the
	 * Controller Settings sidebar's per-target dropdown for categories with more than one known
	 * spot, or index 0 (whatever's first/safest) if nothing's been chosen yet.
	 */
	public static Map<String, Object> resolveTaskLocations(ControllerConfig config)
	{
		Map<String, Integer> choices = parseLocationChoices(config.taskLocationChoices());
		Map<String, Object> result = new LinkedHashMap<>();

		for (Map.Entry<String, List<SlayerLocations.Location>> entry : SlayerLocations.LOCATIONS.entrySet())
		{
			String category = entry.getKey();
			List<SlayerLocations.Location> locations = entry.getValue();

			int index = choices.getOrDefault(category, 0);
			if (index < 0 || index >= locations.size())
			{
				index = 0;
			}
			SlayerLocations.Location loc = locations.get(index);

			Map<String, Object> locData = new LinkedHashMap<>();
			locData.put("x", loc.x);
			locData.put("y", loc.y);
			locData.put("plane", loc.plane);
			locData.put("wildernessLevel", loc.wildernessLevel);
			locData.put("note", loc.note);
			locData.put("optionCount", locations.size());
			result.put(category, locData);
		}

		return result;
	}

	public static Map<String, Integer> parseLocationChoices(String raw)
	{
		Map<String, Integer> choices = new LinkedHashMap<>();
		for (String part : parseList(raw == null ? null : raw.replace(";", ",")))
		{
			int eq = part.indexOf('=');
			if (eq <= 0)
			{
				continue;
			}
			try
			{
				choices.put(part.substring(0, eq).trim(), Integer.parseInt(part.substring(eq + 1).trim()));
			}
			catch (NumberFormatException ignored)
			{
			}
		}
		return choices;
	}

	public static List<Integer> resolveTargetIds(Client client, ControllerConfig config)
	{
		Set<Integer> ids = new LinkedHashSet<>();

		for (String part : parseList(config.targetNpcIds()))
		{
			try
			{
				ids.add(Integer.parseInt(part.trim()));
			}
			catch (NumberFormatException ignored)
			{
			}
		}

		Set<String> names = parseList(config.targetNpcNames()).stream()
			.map(s -> s.trim().toLowerCase(Locale.ENGLISH))
			.collect(Collectors.toCollection(LinkedHashSet::new));

		if (client != null)
		{
			for (NPC npc : client.getNpcs())
			{
				if (npc == null || npc.getName() == null)
				{
					continue;
				}

				if (names.contains(npc.getName().toLowerCase(Locale.ENGLISH)))
				{
					ids.add(npc.getId());
				}
			}
		}

		return new ArrayList<>(ids);
	}

	public static List<String> parseList(String raw)
	{
		if (raw == null || raw.trim().isEmpty())
		{
			return Collections.emptyList();
		}

		return Arrays.stream(raw.split(","))
			.map(String::trim)
			.filter(s -> !s.isEmpty())
			.collect(Collectors.toList());
	}
}
```

- [ ] **Step 2: Compile check**

Run: `.\gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`. Note `SlayerConfigProvider.java` still calls the OLD two-argument `ConfigExporter.export(client, controllerConfig, slayerConfig)` at this point — that old three-argument overload no longer exists after this rewrite, so this step will actually **fail to compile** until `SlayerConfigProvider.java` is updated. Since that update is tightly coupled to deleting the old plugin files (Task 3), do this compile check only to confirm `ConfigExporter.java` itself has no syntax errors in isolation; a real `BUILD SUCCESSFUL` for the whole project isn't expected until Task 3 completes. Note this in your commit message.

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/example/controller/ConfigExporter.java
git commit -m "Add exportMining/exportGemstoneCrab/exportTormentedDemon/exportFiremaking/exportBlastFurnace, rename export to exportSlayer

Whole-project compile is expected to fail until the next commit updates SlayerConfigProvider and deletes the old per-routine plugin files -- this commit only adds new methods and renames the old export() overload, which SlayerConfigProvider.java (updated next) still calls under its old name."
```

---

### Task 3: The coupled rewire — update every consumer, delete the six old plugin pairs, update the dev harness

This is one atomic task: `ControllerPlugin`, `MenuPriority`, `SettingsPanel`, and `SlayerConfigProvider` all need to stop referencing the standalone `SlayerConfig` type and the deleted plugins' registration methods at the same moment those files are deleted, or the project won't compile in between. Do every step below before checking the build.

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`
- Modify: `src/main/java/com/example/controller/MenuPriority.java`
- Modify: `src/main/java/com/example/controller/SettingsPanel.java`
- Modify: `src/main/java/com/example/providers/SlayerConfigProvider.java`
- Modify: `src/test/java/com/example/ExamplePluginTest.java`
- Delete: `src/main/java/com/example/controller/SlayerConfig.java`
- Delete: `src/main/java/com/example/controller/SlayerPlugin.java`
- Delete: `src/main/java/com/example/controller/MiningConfig.java`
- Delete: `src/main/java/com/example/controller/MiningPlugin.java`
- Delete: `src/main/java/com/example/controller/GemstoneCrabConfig.java`
- Delete: `src/main/java/com/example/controller/GemstoneCrabPlugin.java`
- Delete: `src/main/java/com/example/controller/TormentedDemonConfig.java`
- Delete: `src/main/java/com/example/controller/TormentedDemonPlugin.java`
- Delete: `src/main/java/com/example/controller/FiremakingConfig.java`
- Delete: `src/main/java/com/example/controller/FiremakingPlugin.java`
- Delete: `src/main/java/com/example/controller/BlastFurnaceConfig.java`
- Delete: `src/main/java/com/example/controller/BlastFurnacePlugin.java`

**Interfaces:**
- Consumes: `ConfigExporter.exportSlayer/exportMining/exportGemstoneCrab/exportTormentedDemon/exportFiremaking/exportBlastFurnace` (Task 2), `ControllerConfig` (Task 1).
- Produces: `ControllerPlugin` registers all six bridge endpoints itself and owns the sidebar `SettingsPanel`/`NavigationButton` lifecycle (moved from the deleted `SlayerPlugin`). `MenuPriority.apply(...)` takes one `ControllerConfig` instead of two config types. `SettingsPanel`'s constructor takes one `ControllerConfig` instead of `(ControllerConfig, SlayerConfig)`.

- [ ] **Step 1: Update `SlayerConfigProvider.java`**

Replace its full contents:

```java
package com.example.providers;

import com.example.controller.ConfigExporter;
import com.example.controller.ControllerConfig;
import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

public class SlayerConfigProvider extends BridgeProvider
{
	private final ControllerConfig config;

	public SlayerConfigProvider(Client client, ClientThread clientThread, BridgeHttpServer server, ControllerConfig config)
	{
		super(client, clientThread, server);
		this.config = config;
	}

	@Override
	public void register()
	{
		server.register("/slayer-config", () -> onClient(this::getConfig));
	}

	private Object getConfig()
	{
		return ConfigExporter.exportSlayer(client, config);
	}
}
```

- [ ] **Step 2: Update `MenuPriority.java`**

Change the `apply` method's signature to drop the separate `SlayerConfig` parameter (both `targetNpcNames`/`lootWhitelist` now live directly on the single `ControllerConfig`). This is the complete method, end to end — replace it in full so there's no ambiguity about what changes (only the signature and the two `slayerConfig.X()` → `config.X()` calls; the loop body below is otherwise byte-for-byte identical to the original, since it only references the already-fetched local variables, never `slayerConfig` directly):

```java
	static void apply(Client client, ControllerConfig config, MenuOpened event)
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

		Set<String> targetNpcs = targetNpcsCache.get(config.targetNpcNames());
		Set<String> lootItems = lootItemsCache.get(config.lootWhitelist());
		Set<String> targetObjects = targetObjectsCache.get(config.targetObjectNames());
		Set<String> forcedActions = forcedActionsCache.get(config.forcedActions());

		for (int i = lastIndex - 1; i >= 0; i--)
		{
			MenuEntry entry = entries[i];
			String option = Text.removeTags(entry.getOption()).toLowerCase(Locale.ENGLISH);
			String target = normalize(entry.getTarget());

			boolean wanted =
				("attack".equals(option) && targetNpcs.contains(target))
					|| ("take".equals(option) && lootItems.contains(target))
					|| (targetObjects.contains(target) && !"examine".equals(option) && !"cancel".equals(option))
					|| forcedActions.contains(option);

			if (wanted)
			{
				entries[i] = entries[lastIndex];
				entries[lastIndex] = entry;
				client.setMenuEntries(entries);
				return;
			}
		}
	}
```

Leave everything else in `MenuPriority.java` — the class-level `CachedSet` fields, `normalize`, `normalizedSet`, the `CachedSet` inner class — completely unchanged; only the `apply` method above is replaced.

- [ ] **Step 3: Update `SettingsPanel.java`**

Three edits:

1. Constructor field/parameter — remove the separate `SlayerConfig` field and constructor parameter:

```java
	private final Client client;
	private final ClientThread clientThread;
	private final ConfigManager configManager;
	private final ControllerConfig config;
```

This is the complete constructor, end to end (only the parameter list, the removed `slayerConfig` field assignment, and the two `slayerConfig.X()` → `config.X()` calls near the top differ from the original — everything from `setLayout(...)` onward is byte-for-byte unchanged, included here in full so there's no ambiguity about where the constructor ends):

```java
	@Inject
	SettingsPanel(Client client, ClientThread clientThread, ConfigManager configManager, ControllerConfig config)
	{
		this.client = client;
		this.clientThread = clientThread;
		this.configManager = configManager;
		this.config = config;

		selectedTargets.addAll(ConfigExporter.parseList(config.targetNpcNames()));
		categoryLocationChoice.putAll(ConfigExporter.parseLocationChoices(config.taskLocationChoices()));

		setLayout(new BorderLayout());
		setBackground(ColorScheme.DARK_GRAY_COLOR);
		setBorder(new EmptyBorder(10, 10, 10, 10));

		JPanel header = new JPanel();
		header.setLayout(new BoxLayout(header, BoxLayout.Y_AXIS));
		header.setBackground(ColorScheme.DARK_GRAY_COLOR);

		JLabel title = new JLabel("Controller Settings");
		title.setForeground(Color.WHITE);
		title.setFont(title.getFont().deriveFont(Font.BOLD, 16f));
		title.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(title);

		JLabel hint = new JLabel("<html>Search any slayer task monster, pick from nearby monsters, or both.<br>Settings are exposed at <b>/config</b>.</html>");
		hint.setForeground(Color.WHITE);
		hint.setBorder(new EmptyBorder(6, 0, 8, 0));
		hint.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(hint);

		statusLabel.setForeground(Color.WHITE);
		statusLabel.setBorder(new EmptyBorder(4, 0, 4, 0));
		statusLabel.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(statusLabel);

		JPanel enabledRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 0, 0));
		enabledRow.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledRow.setAlignmentX(Component.LEFT_ALIGNMENT);

		enabledBox.setSelected(config.controllerEnabled());
		enabledBox.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledBox.setForeground(Color.WHITE);
		enabledBox.addActionListener(e -> {
			// Guard against redundant writes -- setSelected() (used by syncFromConfig() to mirror
			// an external change) never fires this listener on its own, but this still avoids
			// persisting a no-op if that ever changes.
			boolean selected = enabledBox.isSelected();
			if (selected != config.controllerEnabled())
			{
				configManager.setConfiguration(ControllerConfig.GROUP, "controllerEnabled", selected);
			}
		});
		enabledRow.add(enabledBox);
		header.add(enabledRow);

		add(header, BorderLayout.NORTH);

		JPanel body = new JPanel();
		body.setLayout(new BoxLayout(body, BoxLayout.Y_AXIS));
		body.setBackground(ColorScheme.DARK_GRAY_COLOR);

		body.add(collapsibleSection("Add Slayer Task Monster", buildSearchSection(), true));
		body.add(collapsibleSection("Selected Targets", buildSelectedSection(), true));
		body.add(collapsibleSection("Monsters In Current Area", buildNearbySection(), true));

		JScrollPane bodyScroll = new JScrollPane(body);
		bodyScroll.setBorder(BorderFactory.createEmptyBorder());
		bodyScroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		// Scrollbar hidden entirely (mouse wheel still scrolls fine) -- a visible one was both an
		// eyesore and ate into the sidebar's already-narrow width, clipping text near the right edge.
		bodyScroll.setVerticalScrollBarPolicy(ScrollPaneConstants.VERTICAL_SCROLLBAR_NEVER);
		bodyScroll.getVerticalScrollBar().setUnitIncrement(16);
		add(bodyScroll, BorderLayout.CENTER);

		refreshSearchResults();
		refreshSelectedTargetsPanel();
		refreshNpcList();
	}
```

2. `persistSelections()` — change `SlayerConfig.GROUP` to `ControllerConfig.GROUP`:

```java
		configManager.setConfiguration(
			ControllerConfig.GROUP,
			"targetNpcNames",
			String.join(", ", selectedTargets)
		);
```

3. `persistLocationChoices()` — same change:

```java
		configManager.setConfiguration(ControllerConfig.GROUP, "taskLocationChoices", sb.toString());
```

4. `syncFromConfig()` — change both `slayerConfig.X()` calls to `config.X()`. This is the complete method, end to end — replace it in full:

```java
	void syncFromConfig()
	{
		enabledBox.setSelected(config.controllerEnabled());

		selectedTargets.clear();
		selectedTargets.addAll(ConfigExporter.parseList(config.targetNpcNames()));

		categoryLocationChoice.clear();
		categoryLocationChoice.putAll(ConfigExporter.parseLocationChoices(config.taskLocationChoices()));

		for (Map.Entry<String, JCheckBox> entry : npcCheckboxes.entrySet())
		{
			entry.getValue().setSelected(selectedTargets.contains(entry.getKey()));
		}

		refreshSelectedTargetsPanel();
		updateStatusLabel(npcCheckboxes.size());
	}
```

Nothing else in this 663-line file changes — every other method (`buildSearchSection`, `buildSelectedSection`, `buildNearbySection`, `collapsibleSection`, `refreshSearchResults`, `refreshNpcList`, etc.) references only `client`, `clientThread`, `configManager`, `selectedTargets`, `categoryLocationChoice`, or Swing components, none of which change.

- [ ] **Step 4: Update `ControllerPlugin.java`**

Replace its full contents:

```java
package com.example.controller;

import com.example.bridge.BridgeHttpServer;
import com.example.providers.*;
import com.example.util.UdpEventSender;
import com.google.inject.Provides;
import net.runelite.api.*;
import net.runelite.api.events.*;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.events.ConfigChanged;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.game.ItemManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.plugins.PluginManager;
import net.runelite.client.ui.ClientToolbar;
import net.runelite.client.ui.NavigationButton;
import net.runelite.client.util.ImageUtil;

import javax.inject.Inject;
import java.awt.image.BufferedImage;
import java.util.HashMap;
import java.util.Map;

@PluginDescriptor(
	name = "* HttpBridge",
	description = "Master enable switch, shared HTTP/UDP bridge, and every routine's settings (Slayer, Mining, Gemstone Crab, Tormented Demon, Firemaking, Blast Furnace) all in one place.",
	tags = {"controller", "config", "bridge", "http"}
)
public class ControllerPlugin extends Plugin
{
	private static final int HTTP_PORT = 8080;

	@Inject
	private Client client;

	@Inject
	private ClientThread clientThread;

	@Inject
	private ControllerConfig config;

	@Inject
	private ConfigManager configManager;

	@Inject
	private ItemManager itemManager;

	@Inject
	private PluginManager pluginManager;

	@Inject
	private EventBus eventBus;

	@Inject
	private SettingsPanel panel;

	@Inject
	private ClientToolbar clientToolbar;

	private NavigationButton navButton;

	private BridgeHttpServer server;
	private UdpEventSender udpSender;

	private int lastHp = -1;
	private int lastPrayer = -1;
	private int lastInventoryHash = 0;
	private int lastAnimation = -1;
	private String lastTargetKey = "";
	private final Map<Skill, Integer> lastRealLevels = new HashMap<>();

	@Override
	protected void startUp()
	{
		udpSender = new UdpEventSender();
		udpSender.start();

		server = new BridgeHttpServer();
		server.start(HTTP_PORT);

		new GameStateProvider(client, clientThread, server).register();
		new PlayerProvider(client, clientThread, server).register();
		new PvpProvider(client, clientThread, server).register();
		new CombatProvider(client, clientThread, server, pluginManager).register();
		new PrayerProvider(client, clientThread, server).register();
		new SkillProvider(client, clientThread, server).register();
		new CameraProvider(client, clientThread, server).register();
		new EquipmentProvider(client, clientThread, server).register();
		new InventoryProvider(client, clientThread, server).register();
		new BankProvider(client, clientThread, server).register();
		new SailingProvider(client, clientThread, server, eventBus).register();
		new NPCProvider(client, clientThread, server).register();
		new ObjectProvider(client, clientThread, server).register();
		new GroundItemProvider(client, clientThread, server, itemManager).register();
		new GraphicsObjectProvider(client, clientThread, server).register();
		new TileProvider(client, clientThread, server).register();
		new ConfigProvider(client, clientThread, server, config).register();
		new ConsumablesProvider(client, clientThread, server, config).register();
		new LoginProvider(client, clientThread, server).register();
		new SlayerConfigProvider(client, clientThread, server, config).register();

		server.register("/mining-config", () -> ConfigExporter.exportMining(config));
		server.register("/gemstone-crab-config", () -> ConfigExporter.exportGemstoneCrab(config));
		server.register("/tormented-demon-config", () -> ConfigExporter.exportTormentedDemon(config));
		server.register("/firemaking-config", () -> ConfigExporter.exportFiremaking(config));
		server.register("/blast-furnace-config", () -> ConfigExporter.exportBlastFurnace(config));

		try
		{
			new ShortestPathProvider(client, clientThread, server, pluginManager, eventBus).register();
		}
		catch (Throwable t)
		{
			System.out.println("Shortest Path integration unavailable: " + t);
		}

		BufferedImage icon;
		try
		{
			icon = ImageUtil.loadImageResource(getClass(), "/controller_icon.png");
		}
		catch (IllegalArgumentException e)
		{
			icon = createFallbackIcon();
		}

		navButton = NavigationButton.builder()
			.tooltip("Controller Settings")
			.icon(icon)
			.priority(5)
			.panel(panel)
			.build();

		clientToolbar.addNavigation(navButton);
	}

	@Override
	protected void shutDown()
	{
		clientToolbar.removeNavigation(navButton);

		if (server != null)
		{
			server.stop();
			server = null;
		}

		if (udpSender != null)
		{
			udpSender.stop();
			udpSender = null;
		}
	}

	@Provides
	ControllerConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(ControllerConfig.class);
	}

	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		MenuPriority.apply(client, config, event);
	}

	@Subscribe
	public void onConfigChanged(ConfigChanged event)
	{
		if (!ControllerConfig.GROUP.equals(event.getGroup()))
		{
			return;
		}

		// Keeps the sidebar panel's Controller Enabled/target selections live with whatever
		// just changed -- including edits made from the wrench-icon config screen, which this
		// panel doesn't otherwise hear about.
		panel.syncFromConfig();

		if (udpSender != null)
		{
			udpSender.send("CONFIG_CHANGED");
		}
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		udpSender.send("TICK");

		int hp = client.getBoostedSkillLevel(Skill.HITPOINTS);
		if (lastHp >= 0 && hp != lastHp)
		{
			Map<String, Object> payload = new HashMap<>();
			payload.put("hp", hp);
			payload.put("maxHp", client.getRealSkillLevel(Skill.HITPOINTS));
			udpSender.send("HP_CHANGED", payload);
		}
		lastHp = hp;

		int prayer = client.getBoostedSkillLevel(Skill.PRAYER);
		if (lastPrayer >= 0 && prayer != lastPrayer)
		{
			Map<String, Object> payload = new HashMap<>();
			payload.put("prayer", prayer);
			payload.put("maxPrayer", client.getRealSkillLevel(Skill.PRAYER));
			udpSender.send("PRAYER_CHANGED", payload);
		}
		lastPrayer = prayer;

		Player local = client.getLocalPlayer();
		if (local != null)
		{
			int anim = local.getAnimation();
			if (lastAnimation >= 0 && anim != lastAnimation)
			{
				Map<String, Object> payload = new HashMap<>();
				payload.put("animation", anim);
				udpSender.send("ANIMATION_CHANGED", payload);
			}
			lastAnimation = anim;

			Actor target = local.getInteracting();
			String targetKey = target == null ? "" : targetKey(target);
			if (!targetKey.equals(lastTargetKey))
			{
				Map<String, Object> payload = new HashMap<>();
				payload.put("target", targetKey.isEmpty() ? null : targetKey);
				if (target instanceof NPC)
				{
					payload.put("targetId", ((NPC) target).getId());
					payload.put("targetName", target.getName());
				}
				udpSender.send("TARGET_CHANGED", payload);
				lastTargetKey = targetKey;
			}
		}
	}

	@Subscribe
	public void onActorDeath(ActorDeath event)
	{
		Actor actor = event.getActor();
		if (actor instanceof NPC)
		{
			NPC npc = (NPC) actor;
			Map<String, Object> payload = new HashMap<>();
			payload.put("id", npc.getId());
			payload.put("index", npc.getIndex());
			payload.put("name", npc.getName());
			udpSender.send("NPC_DIED", payload);
		}
		else if (actor instanceof Player && actor == client.getLocalPlayer())
		{
			udpSender.send("PLAYER_DIED");
		}
	}

	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		Skill skill = event.getSkill();
		int realLevel = client.getRealSkillLevel(skill);
		Integer previous = lastRealLevels.put(skill, realLevel);

		if (previous == null || realLevel <= previous)
		{
			return;
		}

		Map<String, Object> payload = new HashMap<>();
		payload.put("skill", skill.getName());
		payload.put("level", realLevel);
		payload.put("previousLevel", previous);
		payload.put("xp", client.getSkillExperience(skill));
		udpSender.send("LEVEL_UP", payload);
	}

	@Subscribe
	public void onItemContainerChanged(ItemContainerChanged event)
	{
		if (event.getContainerId() != InventoryID.INVENTORY.getId())
		{
			return;
		}

		ItemContainer container = event.getItemContainer();
		int hash = inventoryHash(container);
		if (hash != lastInventoryHash)
		{
			lastInventoryHash = hash;
			udpSender.send("INVENTORY_CHANGED");
		}
	}

	@Subscribe
	public void onChatMessage(ChatMessage event)
	{
		if (event.getType() != ChatMessageType.GAMEMESSAGE
			&& event.getType() != ChatMessageType.SPAM
			&& event.getType() != ChatMessageType.DIALOG)
		{
			return;
		}

		Map<String, Object> payload = new HashMap<>();
		payload.put("type", event.getType().name());
		payload.put("message", event.getMessage());
		payload.put("sender", event.getName());
		udpSender.send("CHAT_MESSAGE", payload);
	}

	private static BufferedImage createFallbackIcon()
	{
		BufferedImage img = new BufferedImage(16, 16, BufferedImage.TYPE_INT_ARGB);
		java.awt.Graphics2D g = img.createGraphics();
		g.setColor(new java.awt.Color(0x4CAF50));
		g.fillOval(1, 1, 14, 14);
		g.setColor(java.awt.Color.WHITE);
		g.fillRect(5, 4, 2, 8);
		g.fillRect(9, 6, 2, 6);
		g.dispose();
		return img;
	}

	private String targetKey(Actor actor)
	{
		if (actor instanceof NPC)
		{
			return "npc:" + ((NPC) actor).getId();
		}
		return "actor:" + actor.getName();
	}

	private int inventoryHash(ItemContainer container)
	{
		if (container == null)
		{
			return 0;
		}

		int hash = 1;
		Item[] items = container.getItems();
		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];
			if (item != null)
			{
				hash = 31 * hash + i;
				hash = 31 * hash + item.getId();
				hash = 31 * hash + item.getQuantity();
			}
		}
		return hash;
	}
}
```

Notes on this rewrite versus the original:
- `getServer()` is removed — nothing needs it anymore, since no other plugin depends on `ControllerPlugin` to reach the shared `BridgeHttpServer` (there are no other plugins left).
- `SlayerConfigProvider` is now constructed and registered directly in `startUp()` (previously done by the deleted `SlayerPlugin.startUp()`), passing the single merged `config`.
- The five other routines are registered as plain lambdas (`() -> ConfigExporter.exportX(config)`) since none of their export functions ever touched `Client` (confirmed in Task 2 — only Slayer's did, hence it alone goes through `SlayerConfigProvider`'s `onClient(...)` wrapper for thread safety).
- `SettingsPanel`/`NavigationButton` startup/shutdown ordering (moved from `SlayerPlugin`) is placed after all bridge/provider registration in `startUp()`, and its teardown is placed first in `shutDown()` — matching `SlayerPlugin`'s own original ordering (nav button added last on the way up, removed first on the way down).

- [ ] **Step 5: Update `ExamplePluginTest.java`**

Replace its full contents:

```java
package com.example;

import com.example.controller.ControllerPlugin;
import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;

public class ExamplePluginTest
{
	public static void main(String[] args) throws Exception
	{
		ExternalPluginManager.loadBuiltin(
			ControllerPlugin.class
		);
		RuneLite.main(args);
	}
}
```

- [ ] **Step 6: Delete the twelve obsolete files**

```bash
git rm src/main/java/com/example/controller/SlayerConfig.java
git rm src/main/java/com/example/controller/SlayerPlugin.java
git rm src/main/java/com/example/controller/MiningConfig.java
git rm src/main/java/com/example/controller/MiningPlugin.java
git rm src/main/java/com/example/controller/GemstoneCrabConfig.java
git rm src/main/java/com/example/controller/GemstoneCrabPlugin.java
git rm src/main/java/com/example/controller/TormentedDemonConfig.java
git rm src/main/java/com/example/controller/TormentedDemonPlugin.java
git rm src/main/java/com/example/controller/FiremakingConfig.java
git rm src/main/java/com/example/controller/FiremakingPlugin.java
git rm src/main/java/com/example/controller/BlastFurnaceConfig.java
git rm src/main/java/com/example/controller/BlastFurnacePlugin.java
```

- [ ] **Step 7: Compile check**

Run: `.\gradlew.bat compileJava .\gradlew.bat compileTestJava`
Expected: `BUILD SUCCESSFUL`. This is the first point in the whole plan where a clean build is expected again (Tasks 1-2 were deliberately left non-final-compiling per their own steps).

If it doesn't compile, the most likely causes, in order of likelihood:
- A leftover `slayerConfig`/`SlayerConfig` reference in `SettingsPanel.java` outside the four edits in Step 3 (re-grep the file for `SlayerConfig` and `slayerConfig` case-insensitively — there should be zero matches left).
- A stale import in `ControllerPlugin.java`, `MenuPriority.java`, or `SlayerConfigProvider.java` referencing a deleted type.
- A `@ConfigItem` method name in `ControllerConfig.java` (Task 1) that doesn't match what `ConfigExporter.java` (Task 2) or `SettingsPanel.java` calls — cross-check exact method names against Task 1's literal code, not against memory of the old per-routine files.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Consolidate all routine settings onto ControllerPlugin, delete the six standalone plugins

BridgeHttpServer.getServer() is no longer needed anywhere -- ControllerPlugin now owns every
provider and every routine's config endpoint directly, so there's nothing left for a dependent
plugin to reach it through. The Controller Settings sidebar panel (SettingsPanel/NavigationButton)
moves from the deleted SlayerPlugin onto ControllerPlugin, since it's the only plugin left.

No Python file changes: every /X-config endpoint keeps its exact path and JSON shape, so
controller.py and every routines/*.py file are untouched."
```

---

## Self-Review

**Spec coverage:**
- "Add all our configs in an expand arrow thing, in HTTP master" → Task 1 merges every routine into `ControllerConfig`, owned solely by `ControllerPlugin` ("HTTP master"/HttpBridge).
- "Remove the \* Slayer etc." → Task 3 deletes all six standalone plugin classes and their config interfaces.
- "The header of the expander called Slayer, Mining etc" → Task 1's per-routine sections are named exactly that, one flat section per routine (per the user's explicit "flatten" choice).
- "No duplicate settings in the individual routines, the one that multiple has can be in http root config" → the Collision Table's "true duplicates" (Breaks, stopOnDeath/stopOnInventoryFull/maxRuntimeHours, eatAtHp, restorePrayerAt) are hoisted to the existing shared root sections and deleted from every routine's own section; only genuinely different settings that happen to share a name (maxTargetDistance, bankEnabled, bankTeleportFallback) are kept distinct, via a rename (not a hoist), because hoisting those specific ones would silently force one shared value onto routines that need independently different values.
- "Slayerconfig is just one of several routines, don't bunch them all under Slayer" → confirmed: Slayer's settings live only in the "Slayer" section; every other routine gets its own equally-weighted section; `SlayerConfig` was never a container for other routines' settings, just Slayer's own historical class name (which happened to already share the `"controller"` persistence group with the root config — clarified in Task 1's header comment so this isn't confusing to a future reader either).
- Zero Python-side changes → confirmed throughout: every endpoint path and every exported JSON key/shape is preserved exactly; only the Java-side data source changes.

**Placeholder scan:** No TBD/TODO markers; every step has complete, literal file content specified. `ConfigExporter.java`'s "expected compile failure" in Task 2 Step 2 is explicitly called out as an interim, non-final state (not a placeholder) — the plan is honest that this specific refactor cannot leave every single task in a fully-compiling state the way the previous routine-adding plans could, since Java requires the whole call graph to resolve together once the old `SlayerConfig` type is dropped.

**Type consistency:** `ConfigExporter`'s six export method names/signatures (Task 2) match exactly what `ControllerPlugin.java` (Task 3) calls. Every `@ConfigItem` method name referenced by `ConfigExporter`/`SettingsPanel`/`MenuPriority` in Tasks 2-3 matches a method actually defined in `ControllerConfig.java` (Task 1) — cross-checked field-by-field against the Collision Table, including all four renamed methods (`tormentedDemonMaxTargetDistance`, `firemakingBankEnabled`, `firemakingBankTeleportFallback`, `blastFurnaceBankTeleportFallback`).
