package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

@ConfigGroup(MiningConfig.GROUP)
public interface MiningConfig extends Config
{
	String GROUP = "controllerMining";

	@ConfigSection(name = "General", description = "Which rocks to mine", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule", position = 1)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits", position = 2)
	String safetySection = "safety";

	// --- General ---

	@ConfigItem(
		keyName = "miningEnabled",
		name = "Mining Enabled",
		description = "Secondary switch on top of launching controller.py with routines/mining.py.",
		section = generalSection,
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
		section = generalSection,
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
		section = generalSection,
		position = 2
	)
	@Range(min = 1, max = 30)
	default int maxRockDistance()
	{
		return 10;
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
		keyName = "stopOnInventoryFull",
		name = "Stop When Inventory Full",
		description = "Stop when inventory has no free slots.",
		section = safetySection,
		position = 1
	)
	default boolean stopOnInventoryFull()
	{
		return true;
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
