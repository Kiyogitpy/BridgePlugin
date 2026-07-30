package com.example.controller;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;
import net.runelite.client.config.Range;

/**
 * Slayer-task-specific settings -- split out of ControllerConfig into its own standalone
 * plugin (SlayerPlugin) for consistency with the other Controller-family plugins (Mining,
 * Gemstone Crab, Tormented Demon), each of which owns its own config rather than sharing one
 * combined screen. General combat mechanics that aren't slayer-specific (Prayer, Food &
 * Potions, Breaks, Safety, Clicking, PvP Safety) live on ControllerConfig/HttpBridge instead --
 * only "which monster to fight and what to do with its drops/where to fight it" lives here.
 * Deliberately reuses ControllerConfig.GROUP ("controller") rather than a new group name --
 * these keyNames are unchanged from when they lived on ControllerConfig, so reusing the same
 * group string means every value already configured/saved carries over instead of resetting to
 * defaults on this split.
 */
@ConfigGroup(SlayerConfig.GROUP)
public interface SlayerConfig extends Config
{
	String GROUP = ControllerConfig.GROUP;

	@ConfigSection(name = "Targets", description = "Which monsters to attack", position = 1)
	String targetsSection = "targets";

	@ConfigSection(name = "Loot", description = "Ground item pickup", position = 4)
	String lootSection = "loot";

	@ConfigSection(name = "Banking", description = "Restock and deposit loot when banking is needed", position = 8)
	String bankSection = "bank";

	@ConfigSection(name = "Travel", description = "Chosen location for slayer task categories with more than one known spot", position = 10)
	String travelSection = "travel";

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
		keyName = "minLootValue",
		name = "Min Loot Value (GP)",
		description = "Also pick up any ground item worth at least this much GE value, regardless of the " +
			"whitelist above (e.g. a Rune platebody drop at 14k GE value gets picked up if this is set to 9000). " +
			"0 = off, whitelist only.",
		section = lootSection,
		position = 2
	)
	default int minLootValue()
	{
		return 5000;
	}

	@ConfigItem(
		keyName = "buryBones",
		name = "Bury Bones",
		description = "Automatically bury bones from inventory.",
		section = lootSection,
		position = 3
	)
	default boolean buryBones()
	{
		return false;
	}

	// --- Banking ---

	@ConfigItem(
		keyName = "bankEnabled",
		name = "Enable Banking",
		description = "Bank for more supplies when inventory is full instead of just stopping.",
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
		return true;
	}

	@ConfigItem(
		keyName = "bankFoodQuantity",
		name = "Food To Withdraw",
		description = "How many of the Food item (Food & Potions section) to withdraw when banking. 0 = don't withdraw food.",
		section = bankSection,
		position = 2
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
		section = bankSection,
		position = 3
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
		section = bankSection,
		position = 4
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
		section = bankSection,
		position = 5
	)
	default String bankKeepItems()
	{
		return "";
	}

	// --- Travel ---

	@ConfigItem(
		keyName = "taskLocationChoices",
		name = "Task Location Choices",
		description = "Managed by the Controller Settings sidebar panel -- which location was picked for each slayer task category that has more than one known spot (e.g. Ice giants: cave vs. Frozen Waste Plateau). Format: \"Category=index;Category2=index\".",
		section = travelSection,
		position = 0
	)
	default String taskLocationChoices()
	{
		return "";
	}
}
