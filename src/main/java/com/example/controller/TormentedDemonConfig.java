package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

@ConfigGroup(TormentedDemonConfig.GROUP)
public interface TormentedDemonConfig extends Config
{
	String GROUP = "controllerTormentedDemon";

	@ConfigSection(name = "General", description = "Targeting", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Combat Style", description = "Fixed style, or auto-swap to match the demon's weakness", position = 1)
	String combatStyleSection = "combatStyle";

	@ConfigSection(name = "Supplies", description = "Food and prayer restore thresholds", position = 2)
	String suppliesSection = "supplies";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule", position = 3)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits", position = 4)
	String safetySection = "safety";

	// --- General ---

	@ConfigItem(
		keyName = "tormentedDemonEnabled",
		name = "Tormented Demon Enabled",
		description = "Secondary switch on top of launching controller.py with routines/tormented_demon.py.",
		section = generalSection,
		position = 0
	)
	default boolean tormentedDemonEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "maxTargetDistance",
		name = "Max Target Distance",
		description = "Only engage a Tormented Demon within this many tiles.",
		section = generalSection,
		position = 1
	)
	@Range(min = 1, max = 30)
	default int maxTargetDistance()
	{
		return 20;
	}

	// --- Combat Style ---

	@ConfigItem(
		keyName = "combatStyle",
		name = "Combat Style",
		description = "Fixed fighting style used whenever Auto Swap Gear is off, or a given tick's weakness " +
			"reading is unavailable.",
		section = combatStyleSection,
		position = 0
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
		section = combatStyleSection,
		position = 1
	)
	default boolean autoSwapGear()
	{
		return false;
	}

	@ConfigItem(
		keyName = "meleeWeapon",
		name = "Melee Weapon",
		description = "Exact inventory item name, e.g. \"Ghrazi rapier\".",
		section = combatStyleSection,
		position = 2
	)
	default String meleeWeapon()
	{
		return "";
	}

	@ConfigItem(
		keyName = "rangedWeapon",
		name = "Ranged Weapon",
		description = "Exact inventory item name, e.g. \"Toxic blowpipe\".",
		section = combatStyleSection,
		position = 3
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
		section = combatStyleSection,
		position = 4
	)
	default String mageWeapon()
	{
		return "";
	}

	// --- Supplies ---

	@ConfigItem(
		keyName = "eatAtHp",
		name = "Eat At HP",
		description = "Eat when hitpoints drop to or below this value.",
		section = suppliesSection,
		position = 0
	)
	@Range(min = 1, max = 99)
	default int eatAtHp()
	{
		return 50;
	}

	@ConfigItem(
		keyName = "restorePrayerAt",
		name = "Restore Prayer At",
		description = "Drink a prayer potion when prayer points drop to or below this value.",
		section = suppliesSection,
		position = 1
	)
	@Range(min = 1, max = 99)
	default int restorePrayerAt()
	{
		return 30;
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
		return 30;
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
		return 50;
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
		return 10;
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
		keyName = "stopOnInventoryFull",
		name = "Stop When Inventory Full",
		description = "Stop when inventory has no free slots.",
		section = safetySection,
		position = 1
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
		position = 2
	)
	@Range(min = 0, max = 24)
	default int maxRuntimeHours()
	{
		return 0;
	}
}
