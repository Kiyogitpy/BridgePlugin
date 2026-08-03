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
