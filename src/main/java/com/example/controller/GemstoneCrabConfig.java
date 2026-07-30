package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

@ConfigGroup(GemstoneCrabConfig.GROUP)
public interface GemstoneCrabConfig extends Config
{
	String GROUP = "controllerGemstoneCrab";

	@ConfigSection(name = "General", description = "Crab and tunnel identification", position = 0)
	String generalSection = "general";

	@ConfigSection(name = "Breaks", description = "Human-like break schedule", position = 1)
	String breaksSection = "breaks";

	@ConfigSection(name = "Safety", description = "Emergency stops and limits", position = 2)
	String safetySection = "safety";

	// --- General ---

	@ConfigItem(
		keyName = "gemstoneCrabEnabled",
		name = "Gemstone Crab Enabled",
		description = "Secondary switch on top of launching controller.py with routines/gemstone_crab.py.",
		section = generalSection,
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
		section = generalSection,
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
		section = generalSection,
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
		section = generalSection,
		position = 3
	)
	@Range(min = 1, max = 30)
	default int maxDistance()
	{
		return 25;
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
