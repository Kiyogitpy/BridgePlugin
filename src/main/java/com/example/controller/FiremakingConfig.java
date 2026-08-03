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
