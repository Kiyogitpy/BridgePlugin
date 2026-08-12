package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.NPC;

import java.util.*;
import java.util.stream.Collectors;

public final class ConfigExporter
{
	private ConfigExporter()
	{
	}

	// General combat mechanics (Prayer, Food & Potions, Breaks, Safety, PvP Safety) come from
	// the shared sections of ControllerConfig -- not slayer-task-specific, so shared rather
	// than duplicated per routine. Everything about which monster to fight, its loot, and
	// banking comes from the Slayer section of the same ControllerConfig.
	public static Map<String, Object> exportSlayer(Client client, ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();

		data.put("enabled", config.controllerEnabled());
		data.put("targets", resolveTargetIds(client, config));
		data.put("targetNames", parseList(config.targetNpcNames()));
		data.put("maxTargetDistance", config.maxTargetDistance());
		data.put("skipNpcInCombat", config.skipNpcInCombat());
		data.put("onlyAttackWhenIdle", config.onlyAttackWhenIdle());

		Map<String, Object> prayer = new LinkedHashMap<>();
		prayer.put("enabled", config.usePrayer());
		prayer.put("defensivePrayer", config.defensivePrayer().name());
		prayer.put("offensivePrayer", config.offensivePrayer().name());
		prayer.put("useQuickPrayers", config.useQuickPrayers());
		prayer.put("restoreAt", config.restorePrayerAt());
		data.put("prayer", prayer);

		String foodName = (config.useWildernessSupplies() ? config.wildernessFood() : config.food()).getItemName();
		String prayerPotionName = (config.useWildernessSupplies() ? config.wildernessPrayerPotion() : config.prayerPotion()).getItemName();

		Map<String, Object> food = new LinkedHashMap<>();
		food.put("enabled", config.useFood());
		food.put("name", foodName);
		food.put("eatAt", config.eatAtHp());
		data.put("food", food);

		Map<String, Object> potions = new LinkedHashMap<>();
		potions.put("prayerEnabled", config.usePrayerPotions());
		potions.put("prayerPotion", prayerPotionName);
		potions.put("combatEnabled", config.useCombatPotions());
		potions.put("combatPotion", config.combatPotion());
		data.put("potions", potions);

		Map<String, Object> loot = new LinkedHashMap<>();
		loot.put("enabled", config.lootEnabled());
		loot.put("items", parseList(config.lootWhitelist()));
		loot.put("minValue", config.minLootValue());
		loot.put("buryBones", config.buryBones());
		data.put("loot", loot);

		Map<String, Object> breaks = new LinkedHashMap<>();
		breaks.put("enabled", config.takeBreaks());
		breaks.put("everyMinMinutes", config.breakEveryMinMinutes());
		breaks.put("everyMaxMinutes", config.breakEveryMaxMinutes());
		breaks.put("durationMinMinutes", config.breakDurationMinMinutes());
		breaks.put("durationMaxMinutes", config.breakDurationMaxMinutes());
		breaks.put("microBreaks", config.microBreaks());
		data.put("breaks", breaks);

		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", config.stopOnDeath());
		safety.put("stopOnInventoryFull", config.stopOnInventoryFull());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		data.put("safety", safety);

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("enabled", config.bankEnabled());
		bank.put("teleportFallback", config.bankTeleportFallback());
		bank.put("keepItems", parseList(config.bankKeepItems()));

		List<Map<String, Object>> withdraw = new ArrayList<>();
		addWithdrawEntry(withdraw, foodName, config.bankFoodQuantity());
		addWithdrawEntry(withdraw, prayerPotionName, config.bankPrayerPotionQuantity());
		addWithdrawEntry(withdraw, config.combatPotion(), config.bankCombatPotionQuantity());
		bank.put("withdraw", withdraw);

		data.put("bank", bank);

		Map<String, Object> pvpSafety = new LinkedHashMap<>();
		pvpSafety.put("fleeEnabled", config.pvpFleeEnabled());
		data.put("pvpSafety", pvpSafety);

		Map<String, Object> safespot = new LinkedHashMap<>();
		boolean safespotMarked = config.safespotEnabled() && (config.safespotX() != 0 || config.safespotY() != 0);
		safespot.put("enabled", config.safespotEnabled());
		safespot.put("marked", safespotMarked);
		safespot.put("x", config.safespotX());
		safespot.put("y", config.safespotY());
		safespot.put("plane", config.safespotPlane());
		data.put("safespot", safespot);

		data.put("taskLocations", resolveTaskLocations(config));

		data.put("eatAt", config.eatAtHp());
		data.put("restorePrayerAt", config.restorePrayerAt());
		data.put("prayerPotion", prayerPotionName);

		return data;
	}

	public static Map<String, Object> exportMining(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.miningEnabled());
		data.put("rockNames", parseList(config.rockNames()));
		data.put("maxRockDistance", config.maxRockDistance());
		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafetyWithInventoryFull(config));
		return data;
	}

	public static Map<String, Object> exportGemstoneCrab(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.gemstoneCrabEnabled());
		data.put("crabName", config.crabName());
		data.put("tunnelName", config.tunnelName());
		data.put("maxDistance", config.maxDistance());
		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafetyWithInventoryFull(config));
		return data;
	}

	public static Map<String, Object> exportTormentedDemon(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.tormentedDemonEnabled());
		data.put("maxTargetDistance", config.tormentedDemonMaxTargetDistance());

		// Lowercased to match routines/tormented_demon.py's OVERHEAD_ICON_TO_STYLE / loadouts
		// dict keys ("melee"/"ranged"/"magic") exactly.
		data.put("combatStyle", config.combatStyle().name().toLowerCase(Locale.ROOT));
		data.put("autoSwapGear", config.autoSwapGear());

		Map<String, Object> loadouts = new LinkedHashMap<>();
		loadouts.put("melee", nullIfBlank(config.meleeWeapon()));
		loadouts.put("ranged", nullIfBlank(config.rangedWeapon()));
		loadouts.put("magic", nullIfBlank(config.mageWeapon()));
		data.put("loadouts", loadouts);

		Map<String, Object> food = new LinkedHashMap<>();
		food.put("enabled", true);
		food.put("eatAt", config.eatAtHp());
		data.put("food", food);

		Map<String, Object> prayer = new LinkedHashMap<>();
		prayer.put("restoreAt", config.restorePrayerAt());
		data.put("prayer", prayer);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafetyWithInventoryFull(config));
		return data;
	}

	public static Map<String, Object> exportFiremaking(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.firemakingEnabled());
		data.put("logName", config.logName());
		data.put("tinderboxName", config.tinderboxName());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("enabled", config.firemakingBankEnabled());
		bank.put("teleportFallback", config.firemakingBankTeleportFallback());
		bank.put("keepItems", Collections.singletonList(config.tinderboxName()));

		List<Map<String, Object>> withdraw = new ArrayList<>();
		Map<String, Object> entry = new LinkedHashMap<>();
		entry.put("name", config.logName());
		entry.put("quantity", config.bankWithdrawQuantity());
		withdraw.add(entry);
		bank.put("withdraw", withdraw);
		data.put("bank", bank);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}

	public static Map<String, Object> exportBlastFurnace(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.blastFurnaceEnabled());
		// Lowercased to match routines/blast_furnace.py's BAR_TYPES dict keys exactly.
		data.put("barType", config.barType().name().toLowerCase(Locale.ROOT));

		Map<String, Object> coffer = new LinkedHashMap<>();
		coffer.put("topUpEnabled", config.cofferTopUpEnabled());
		coffer.put("topUpAmount", config.cofferTopUpAmount());
		coffer.put("topUpIntervalMinutes", config.cofferTopUpIntervalMinutes());
		data.put("coffer", coffer);

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("teleportFallback", config.blastFurnaceBankTeleportFallback());
		data.put("bank", bank);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}

	public static Map<String, Object> exportGolemCrafting(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.golemCraftingEnabled());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("teleportFallback", config.golemCraftingBankTeleportFallback());
		data.put("bank", bank);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}

	public static Map<String, Object> exportMasteringMixology(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.masteringMixologyEnabled());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("teleportFallback", config.masteringMixologyBankTeleportFallback());
		data.put("bank", bank);

		Map<String, Object> digweed = new LinkedHashMap<>();
		digweed.put("enabled", config.masteringMixologyDigweedEnabled());
		data.put("digweed", digweed);

		Map<String, Object> speedups = new LinkedHashMap<>();
		boolean speedupsEnabled = config.masteringMixologySpeedupsEnabled();
		speedups.put("retort", speedupsEnabled);
		speedups.put("agitator", speedupsEnabled);
		speedups.put("alembic", speedupsEnabled);
		data.put("speedups", speedups);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}

	/** Breaks shape shared by every routine's export -- read from ControllerConfig's shared
	 * Breaks section rather than a routine-specific duplicate (see this plan's Collision Table). */
	private static Map<String, Object> sharedBreaks(ControllerConfig config)
	{
		Map<String, Object> breaks = new LinkedHashMap<>();
		breaks.put("enabled", config.takeBreaks());
		breaks.put("everyMinMinutes", config.breakEveryMinMinutes());
		breaks.put("everyMaxMinutes", config.breakEveryMaxMinutes());
		breaks.put("durationMinMinutes", config.breakDurationMinMinutes());
		breaks.put("durationMaxMinutes", config.breakDurationMaxMinutes());
		breaks.put("microBreaks", config.microBreaks());
		return breaks;
	}

	/** Safety shape (stopOnDeath + maxRuntimeHours only) for routines whose export never
	 * included stopOnInventoryFull (Firemaking, Blast Furnace) -- unchanged from before, just
	 * reading the shared field instead of a routine-specific one. */
	private static Map<String, Object> sharedSafety(ControllerConfig config)
	{
		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", config.stopOnDeath());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		return safety;
	}

	/** Safety shape including stopOnInventoryFull, for routines whose export already included
	 * it (Mining, Gemstone Crab) -- unchanged shape, now reading the shared field. */
	private static Map<String, Object> sharedSafetyWithInventoryFull(ControllerConfig config)
	{
		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", config.stopOnDeath());
		safety.put("stopOnInventoryFull", config.stopOnInventoryFull());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		return safety;
	}

	private static String nullIfBlank(String s)
	{
		return s == null || s.trim().isEmpty() ? null : s.trim();
	}

	private static void addWithdrawEntry(List<Map<String, Object>> withdraw, String name, int quantity)
	{
		if (name == null || name.trim().isEmpty() || quantity <= 0)
		{
			return;
		}

		Map<String, Object> entry = new LinkedHashMap<>();
		entry.put("name", name.trim());
		entry.put("quantity", quantity);
		withdraw.add(entry);
	}

	/**
	 * Every slayer task category that has known wilderness location data (see
	 * SlayerLocations.java), each resolved to a single chosen location -- the one picked via the
	 * Controller Settings sidebar's per-target dropdown for categories with more than one known
	 * spot, or index 0 (whatever's first/safest) if nothing's been chosen yet.
	 */
	public static Map<String, Object> resolveTaskLocations(ControllerConfig config)
	{
		Map<String, Integer> choices = parseLocationChoices(config.taskLocationChoices());
		Map<String, Object> result = new LinkedHashMap<>();

		for (Map.Entry<String, List<SlayerLocations.Location>> entry : SlayerLocations.LOCATIONS.entrySet())
		{
			String category = entry.getKey();
			List<SlayerLocations.Location> locations = entry.getValue();

			int index = choices.getOrDefault(category, 0);
			if (index < 0 || index >= locations.size())
			{
				index = 0;
			}
			SlayerLocations.Location loc = locations.get(index);

			Map<String, Object> locData = new LinkedHashMap<>();
			locData.put("x", loc.x);
			locData.put("y", loc.y);
			locData.put("plane", loc.plane);
			locData.put("wildernessLevel", loc.wildernessLevel);
			locData.put("note", loc.note);
			locData.put("optionCount", locations.size());
			result.put(category, locData);
		}

		return result;
	}

	public static Map<String, Integer> parseLocationChoices(String raw)
	{
		Map<String, Integer> choices = new LinkedHashMap<>();
		for (String part : parseList(raw == null ? null : raw.replace(";", ",")))
		{
			int eq = part.indexOf('=');
			if (eq <= 0)
			{
				continue;
			}
			try
			{
				choices.put(part.substring(0, eq).trim(), Integer.parseInt(part.substring(eq + 1).trim()));
			}
			catch (NumberFormatException ignored)
			{
			}
		}
		return choices;
	}

	public static List<Integer> resolveTargetIds(Client client, ControllerConfig config)
	{
		Set<Integer> ids = new LinkedHashSet<>();

		for (String part : parseList(config.targetNpcIds()))
		{
			try
			{
				ids.add(Integer.parseInt(part.trim()));
			}
			catch (NumberFormatException ignored)
			{
			}
		}

		Set<String> names = parseList(config.targetNpcNames()).stream()
			.map(s -> s.trim().toLowerCase(Locale.ENGLISH))
			.collect(Collectors.toCollection(LinkedHashSet::new));

		if (client != null)
		{
			for (NPC npc : client.getNpcs())
			{
				if (npc == null || npc.getName() == null)
				{
					continue;
				}

				if (names.contains(npc.getName().toLowerCase(Locale.ENGLISH)))
				{
					ids.add(npc.getId());
				}
			}
		}

		return new ArrayList<>(ids);
	}

	public static List<String> parseList(String raw)
	{
		if (raw == null || raw.trim().isEmpty())
		{
			return Collections.emptyList();
		}

		return Arrays.stream(raw.split(","))
			.map(String::trim)
			.filter(s -> !s.isEmpty())
			.collect(Collectors.toList());
	}
}
