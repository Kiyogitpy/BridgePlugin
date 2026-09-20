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

	@ConfigSection(name = "Woodcutting", description = "Woodcutting routine settings", position = 17)
	String woodcuttingSection = "woodcutting";

	@ConfigSection(name = "Gemstone Crab", description = "Gemstone Crab routine settings", position = 9)
	String gemstoneCrabSection = "gemstoneCrab";

	@ConfigSection(name = "Tormented Demon", description = "Tormented Demon routine settings", position = 10)
	String tormentedDemonSection = "tormentedDemon";

	@ConfigSection(name = "Firemaking", description = "Firemaking routine settings", position = 11)
	String firemakingSection = "firemaking";

	@ConfigSection(name = "Blast Furnace", description = "Blast Furnace routine settings", position = 12)
	String blastFurnaceSection = "blastFurnace";

	@ConfigSection(name = "Golem Crafting", description = "Golem Crafting routine settings", position = 13)
	String golemCraftingSection = "golemCrafting";

	@ConfigSection(name = "Mastering Mixology", description = "Mastering Mixology routine settings", position = 14)
	String masteringMixologySection = "masteringMixology";

	@ConfigSection(name = "Player-Owned House", description = "What is built in your POH. The client cannot see inside a house, so travel planning has to be told.", position = 16, closedByDefault = true)
	String pohSection = "poh";

	@ConfigSection(name = "Mahogany Homes", description = "Mahogany Homes routine settings", position = 15)
	String mahoganyHomesSection = "mahoganyHomes";

	@ConfigSection(name = "Vale Totems", description = "Forestry totem circuit", position = 18)
	String valeTotemsSection = "valeTotems";

	@ConfigSection(name = "Slayer tasks", description = "The Slayer (tasks) routine: which masters, when the milestone task is, what to skip", position = 19)
	String slayerTasksSection = "slayerTasks";

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
		keyName = "bridgeBindAll",
		name = "Bridge on all interfaces",
		description = "Bind the HTTP bridge to 0.0.0.0 instead of 127.0.0.1. It has NO auth and serves "
			+ "game state, screenshots, recorder keystrokes and the bank memory -- turn on only for a "
			+ "monitoring PC. The OSRS VM guest uses the BRIDGE_BIND_ALL=1 environment variable instead "
			+ "(a VirtualBox port-forward reaches the guest's NAT address, not its loopback).",
		section = generalSection,
		position = 1
	)
	default boolean bridgeBindAll()
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

	@ConfigItem(
		keyName = "safespotEnabled",
		name = "Use Safespot",
		description = "Walk back to the marked safespot tile whenever you drift off it (looting, a monster's " +
			"pull attack, etc). Only useful for Ranged/Magic tasks -- melee requires standing adjacent to the " +
			"target, which defeats the point.",
		section = slayerSection,
		position = 16
	)
	default boolean safespotEnabled()
	{
		return false;
	}

	@ConfigItem(
		keyName = "safespotX",
		name = "Safespot X",
		description = "Managed by right-clicking the ground and choosing \"Mark Safespot\" -- 0 means no tile " +
			"has been marked yet. Hidden: not meant to be hand-typed, only ever set by the marker.",
		section = slayerSection,
		position = 17,
		hidden = true
	)
	default int safespotX()
	{
		return 0;
	}

	@ConfigItem(
		keyName = "safespotY",
		name = "Safespot Y",
		description = "Managed by right-clicking the ground and choosing \"Mark Safespot\". Hidden: not meant " +
			"to be hand-typed, only ever set by the marker.",
		section = slayerSection,
		position = 18,
		hidden = true
	)
	default int safespotY()
	{
		return 0;
	}

	@ConfigItem(
		keyName = "safespotPlane",
		name = "Safespot Plane",
		description = "Managed by right-clicking the ground and choosing \"Mark Safespot\". Hidden: not meant " +
			"to be hand-typed, only ever set by the marker.",
		section = slayerSection,
		position = 19,
		hidden = true
	)
	default int safespotPlane()
	{
		return 0;
	}

	@ConfigItem(
		keyName = "slayerBraceletReequip",
		name = "Re-wear Slayer Bracelets",
		description = "When a bracelet of slaughter / expeditious bracelet crumbles, wear a spare from the inventory. " +
			"Only ever acts on an EMPTY gloves slot.",
		section = slayerSection,
		position = 20
	)
	default boolean slayerBraceletReequip()
	{
		return true;
	}

	// ============================================================
	// Slayer tasks (routines/slayer.py -- spec docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md)
	// ============================================================

	@ConfigItem(
		keyName = "slayerFillerMaster",
		name = "Filler Master",
		description = "Asked for every task that is NOT a milestone. Aya's tasks give 0 points but count toward the streak.",
		section = slayerTasksSection,
		position = 0
	)
	default SlayerMasterChoice slayerFillerMaster()
	{
		return SlayerMasterChoice.AYA;
	}

	@ConfigItem(
		keyName = "slayerMilestoneMaster",
		name = "Milestone Master",
		description = "Asked for the task whose completion lands on a milestone (every 10th/50th/100th). Kuradal: 75/225/375 points.",
		section = slayerTasksSection,
		position = 1
	)
	default SlayerMasterChoice slayerMilestoneMaster()
	{
		return SlayerMasterChoice.KURADAL;
	}

	@ConfigItem(
		keyName = "slayerMilestoneEvery",
		name = "Milestone Every N Tasks",
		description = "The milestone master is used when the NEXT task number is a multiple of this. 10 = every 10th task; 50 = only every 50th.",
		section = slayerTasksSection,
		position = 2
	)
	@Range(min = 10, max = 50)
	default int slayerMilestoneEvery()
	{
		return 10;
	}

	@ConfigItem(
		keyName = "slayerSkipTasks",
		name = "Always Skip Tasks",
		description = "Comma-separated task names (as the game names them, e.g. 'Abyssal demons') to cancel for 30 points whenever assigned.",
		section = slayerTasksSection,
		position = 3
	)
	default String slayerSkipTasks()
	{
		return "";
	}

	@ConfigItem(
		keyName = "slayerMaxSkips",
		name = "Max Skips Per Slot",
		description = "Consecutive 30-point cancels allowed for one task slot before the routine stops. " +
			"With the chosen skip list about 40% of Kuradal rolls are skips, so 3 in a row is routine; 8 is not.",
		section = slayerTasksSection,
		position = 4
	)
	@Range(min = 0, max = 20)
	default int slayerMaxSkips()
	{
		return 8;
	}

	@ConfigItem(
		keyName = "slayerPointsReserve",
		name = "Points Reserve",
		description = "Never skip a task if it would leave fewer than this many slayer points.",
		section = slayerTasksSection,
		position = 5
	)
	@Range(min = 0, max = 5000)
	default int slayerPointsReserve()
	{
		return 60;
	}

	@ConfigItem(
		keyName = "slayerMinHouseTeleports",
		name = "Min House Teleports",
		description = "Stop (with the list to buy) when fewer than this many Teleport to house tablets are left in the " +
			"inventory and bank together. The house is the travel hub, so running out strands the routine. It never buys anything.",
		section = slayerTasksSection,
		position = 6
	)
	@Range(min = 0, max = 1000)
	default int slayerMinHouseTeleports()
	{
		return 5;
	}

	@ConfigItem(
		keyName = "slayerMakeRoomValue",
		name = "Make Room Value",
		description = "With a full inventory, a drop worth at least this many coins is worth EATING one food for -- " +
			"only when HP is actually missing. Below it, a full inventory just leaves the drop.",
		section = slayerTasksSection,
		position = 7
	)
	@Range(min = 0, max = 100000000)
	default int slayerMakeRoomValue()
	{
		return 50000;
	}

	@ConfigItem(
		keyName = "slayerMustLootValue",
		name = "Must Loot Value",
		description = "With a full inventory, a drop worth at least this many coins is always taken: eat a food, else finish a " +
			"1-dose prayer potion, else DROP the cheapest consumable. Never gear, a teleport item or an item the task requires.",
		section = slayerTasksSection,
		position = 8
	)
	@Range(min = 0, max = 100000000)
	default int slayerMustLootValue()
	{
		return 150000;
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
		description = "Comma-separated rock names to mine, closest first (e.g. Tin rocks, Copper rocks). Also managed by the Controller Settings sidebar panel's Mining section, which lists whatever is mineable nearby.",
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

	@ConfigItem(
		keyName = "miningFullInventory",
		name = "When Full",
		description = "What routines/mining.py does with a full inventory: bank it at a bank within 40 tiles, " +
			"drop everything but the pickaxe, or stop the run.",
		section = miningSection,
		position = 3
	)
	default FullInventoryChoice miningFullInventory()
	{
		return FullInventoryChoice.BANK;
	}

	// ============================================================
	// Woodcutting
	// ============================================================

	@ConfigItem(
		keyName = "woodcuttingEnabled",
		name = "Woodcutting Enabled",
		description = "Secondary switch on top of launching controller.py with routines/woodcutting.py.",
		section = woodcuttingSection,
		position = 0
	)
	default boolean woodcuttingEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "treeNames",
		name = "Tree Names",
		description = "Comma-separated tree names to chop, closest first (e.g. Yew tree, Magic tree). Also managed by " +
			"the Controller Settings sidebar panel's Gathering section with its action set to Chop down, which lists " +
			"whatever is choppable nearby. Note the game's own name is \"Yew tree\", not \"Yew\".",
		section = woodcuttingSection,
		position = 1
	)
	default String treeNames()
	{
		return "Tree";
	}

	@ConfigItem(
		keyName = "maxTreeDistance",
		name = "Max Tree Distance",
		description = "Only chop trees within this many tiles.",
		section = woodcuttingSection,
		position = 2
	)
	@Range(min = 1, max = 30)
	default int maxTreeDistance()
	{
		return 10;
	}

	@ConfigItem(
		keyName = "woodcuttingFullInventory",
		name = "When Full",
		description = "What routines/woodcutting.py does with a full inventory. Power-chopping (drop) is the normal " +
			"woodcutting loop; bank uses the same banking path mining does.",
		section = woodcuttingSection,
		position = 3
	)
	default FullInventoryChoice woodcuttingFullInventory()
	{
		return FullInventoryChoice.DROP;
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

	// ============================================================
	// Golem Crafting
	// ============================================================

	@ConfigItem(
		keyName = "golemCraftingEnabled",
		name = "Golem Crafting Enabled",
		description = "Secondary switch on top of launching controller.py with routines/golem_crafting.py.",
		section = golemCraftingSection,
		position = 0
	)
	default boolean golemCraftingEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "golemCraftingBankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the ring of dueling to Ferox Enclave when no bank is within walking distance of Wyrmscraig.",
		section = golemCraftingSection,
		position = 1
	)
	default boolean golemCraftingBankTeleportFallback()
	{
		return false;
	}

	// ============================================================
	// Mastering Mixology
	// ============================================================

	@ConfigItem(
		keyName = "masteringMixologyEnabled",
		name = "Mastering Mixology Enabled",
		description = "Secondary switch on top of launching controller.py with routines/mastering_mixology.py.",
		section = masteringMixologySection,
		position = 0
	)
	default boolean masteringMixologyEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "masteringMixologyBankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the configured teleport fallback if no bank is within walking distance of the lab.",
		section = masteringMixologySection,
		position = 1
	)
	default boolean masteringMixologyBankTeleportFallback()
	{
		return false;
	}

	@ConfigItem(
		keyName = "masteringMixologyDigweedEnabled",
		name = "Collect Digweed",
		description = "Collect and use digweed spawns when they appear.",
		section = masteringMixologySection,
		position = 2
	)
	default boolean masteringMixologyDigweedEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "masteringMixologySpeedupsEnabled",
		name = "Processing Speedups",
		description = "Perform the timing-based speedup clicks at each processing station.",
		section = masteringMixologySection,
		position = 3
	)
	default boolean masteringMixologySpeedupsEnabled()
	{
		return true;
	}

	// ============================================================
	// Mahogany Homes
	// ============================================================

	@ConfigItem(
		keyName = "mahoganyHomesEnabled",
		name = "Mahogany Homes Enabled",
		description = "Secondary switch on top of launching controller.py with routines/mahogany_homes.py.",
		section = mahoganyHomesSection,
		position = 0
	)
	default boolean mahoganyHomesEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "usePoh",
		name = "Use POH teleports",
		description = "Master switch for every Player-Owned House transport. Off means travel " +
			"planning never routes through your house, whatever the settings below say.",
		section = pohSection,
		position = 0
	)
	default boolean usePoh()
	{
		return false;
	}

	@ConfigItem(
		keyName = "pohJewelleryBoxTier",
		name = "Jewellery box tier",
		description = "The jewellery box built in your house. Tiers are cumulative -- an Ornate " +
			"box offers everything Basic does. None disables it.",
		section = pohSection,
		position = 1
	)
	default JewelleryBoxTier pohJewelleryBoxTier()
	{
		return JewelleryBoxTier.NONE;
	}

	@ConfigItem(
		keyName = "usePohPortals",
		name = "Portal nexus / portals",
		description = "Whether to route through the teleport portals built in your house.",
		section = pohSection,
		position = 2
	)
	default boolean usePohPortals()
	{
		return false;
	}

	@ConfigItem(
		keyName = "usePohMountedItems",
		name = "Mounted items",
		description = "Mounted glory, Xeric's talisman, digsite pendant, mythical cape. Note an " +
			"Ornate jewellery box supersedes a mounted glory, so the glory is skipped when you " +
			"have one.",
		section = pohSection,
		position = 3
	)
	default boolean usePohMountedItems()
	{
		return false;
	}

	@ConfigItem(
		keyName = "usePohFairyRing",
		name = "Fairy ring",
		description = "Whether to route through a fairy ring built in your house.",
		section = pohSection,
		position = 4
	)
	default boolean usePohFairyRing()
	{
		return false;
	}

	// ============================================================
	// Vale Totems
	// ============================================================

	@ConfigItem(
		keyName = "valeTotemsWood",
		name = "Totem wood",
		description = "Which log to build and decorate Vale Totems with.",
		section = valeTotemsSection
	)
	default ValeTotemWood valeTotemsWood()
	{
		return ValeTotemWood.YEW;
	}
}
