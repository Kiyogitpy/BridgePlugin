package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

@ConfigGroup(ControllerConfig.GROUP)
public interface ControllerConfig extends Config
{
	String GROUP = "controller";

	@ConfigSection(name = "General", description = "Master controller controls", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Targets", description = "Which monsters to attack", position = 1)
	String targetsSection = "targets";

	@ConfigSection(name = "Prayer", description = "Prayer usage", position = 2)
	String prayerSection = "prayer";

	@ConfigSection(name = "Food & Potions", description = "Healing and supplies", position = 3)
	String suppliesSection = "supplies";

	@ConfigSection(name = "Loot", description = "Ground item pickup", position = 4)
	String lootSection = "loot";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule", position = 5)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits", position = 6)
	String safetySection = "safety";

	@ConfigSection(name = "Clicking", description = "Force which action a click resolves to", position = 7)
	String clickingSection = "clicking";

	// --- General ---

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
		keyName = "profile",
		name = "Profile",
		description = "Which controller.py routine to run. Add new routines to controller.py's PROFILE_ROUTINES map to add options here.",
		section = generalSection,
		position = 1
	)
	default Profile profile()
	{
		return Profile.SLAYER;
	}

	// --- Targets ---

	@ConfigItem(
		keyName = "targetNpcNames",
		name = "Target NPCs",
		description = "Comma-separated NPC names. Use the Controller Settings sidebar panel to pick from nearby monsters.",
		section = targetsSection,
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
		section = targetsSection,
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
		section = targetsSection,
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
		section = targetsSection,
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
		section = targetsSection,
		position = 4
	)
	default boolean onlyAttackWhenIdle()
	{
		return true;
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

	@ConfigItem(
		keyName = "useQuickPrayers",
		name = "Use Quick Prayers",
		description = "Toggle quick-prayer orb instead of individual prayers (uses your RuneLite quick-prayer setup).",
		section = prayerSection,
		position = 2
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
		position = 3
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
		description = "Preferred food name (partial match, e.g. Shark).",
		section = suppliesSection,
		position = 1
	)
	default String food()
	{
		return "Shark";
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
		description = "Preferred prayer restore item (partial match, e.g. Super restore).",
		section = suppliesSection,
		position = 4
	)
	default String prayerPotion()
	{
		return "Super restore";
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

	// --- Loot ---

	@ConfigItem(
		keyName = "lootEnabled",
		name = "Pick Up Loot",
		description = "Enable looting ground items on the whitelist.",
		section = lootSection,
		position = 0
	)
	default boolean lootEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "lootWhitelist",
		name = "Loot Whitelist",
		description = "Comma-separated item names to pick up.",
		section = lootSection,
		position = 1
	)
	default String lootWhitelist()
	{
		return "";
	}

	@ConfigItem(
		keyName = "buryBones",
		name = "Bury Bones",
		description = "Automatically bury bones from inventory.",
		section = lootSection,
		position = 2
	)
	default boolean buryBones()
	{
		return false;
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
		description = "Comma-separated object names (e.g. \"Cave entrance\") that should always win a click over " +
			"anything else in the way, such as a tree blocking a cave mouth.",
		section = clickingSection,
		position = 1
	)
	default String targetObjectNames()
	{
		return "";
	}
}
