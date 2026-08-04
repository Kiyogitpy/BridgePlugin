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
