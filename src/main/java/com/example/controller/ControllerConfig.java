package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

/**
 * The shared HTTP/UDP bridge plugin's own config -- the master enable switch, plus general
 * combat mechanics that aren't slayer-task-specific (Prayer, Food & Potions, Breaks, Safety,
 * Clicking, PvP Safety). Slayer-specific settings (which monster to attack, loot, banking,
 * travel) live on SlayerConfig instead, exposed through its own standalone SlayerPlugin,
 * consistent with how Mining/Gemstone Crab/Tormented Demon each own their settings rather than
 * sharing this one.
 */
@ConfigGroup(ControllerConfig.GROUP)
public interface ControllerConfig extends Config
{
	String GROUP = "controller";

	@ConfigSection(name = "General", description = "Master controller controls", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Prayer", description = "Prayer usage", position = 2)
	String prayerSection = "prayer";

	@ConfigSection(name = "Food & Potions", description = "Healing and supplies", position = 3)
	String suppliesSection = "supplies";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule", position = 5)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits", position = 6)
	String safetySection = "safety";

	@ConfigSection(name = "Clicking", description = "Force which action a click resolves to", position = 7)
	String clickingSection = "clicking";

	@ConfigSection(name = "PvP Safety", description = "Wilderness flee-on-threat behavior", position = 9)
	String pvpSafetySection = "pvpSafety";

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

	// --- Prayer ---

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
		description = "Drink a prayer potion when prayer points drop to or below this value.",
		section = prayerSection,
		position = 4
	)
	@Range(min = 1, max = 99)
	default int restorePrayerAt()
	{
		return 18;
	}

	// --- Food & Potions ---

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
		description = "Eat when hitpoints drop to or below this value.",
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

	// --- Breaks ---

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

	// --- Safety ---

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

	// --- Clicking ---

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

	// --- PvP Safety ---

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
}
