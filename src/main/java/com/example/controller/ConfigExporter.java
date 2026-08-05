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
	// ControllerConfig/HttpBridge -- not slayer-task-specific, so shared rather than duplicated
	// per routine. Everything about which monster to fight, its loot, and banking comes from
	// SlayerConfig.
	public static Map<String, Object> export(Client client, ControllerConfig controllerConfig, SlayerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();

		data.put("enabled", controllerConfig.controllerEnabled());
		data.put("targets", resolveTargetIds(client, config));
		data.put("targetNames", parseList(config.targetNpcNames()));
		data.put("maxTargetDistance", config.maxTargetDistance());
		data.put("skipNpcInCombat", config.skipNpcInCombat());
		data.put("onlyAttackWhenIdle", config.onlyAttackWhenIdle());

		Map<String, Object> prayer = new LinkedHashMap<>();
		prayer.put("enabled", controllerConfig.usePrayer());
		prayer.put("defensivePrayer", controllerConfig.defensivePrayer().name());
		prayer.put("offensivePrayer", controllerConfig.offensivePrayer().name());
		prayer.put("useQuickPrayers", controllerConfig.useQuickPrayers());
		prayer.put("restoreAt", controllerConfig.restorePrayerAt());
		data.put("prayer", prayer);

		String foodName = (controllerConfig.useWildernessSupplies() ? controllerConfig.wildernessFood() : controllerConfig.food()).getItemName();
		String prayerPotionName = (controllerConfig.useWildernessSupplies() ? controllerConfig.wildernessPrayerPotion() : controllerConfig.prayerPotion()).getItemName();

		Map<String, Object> food = new LinkedHashMap<>();
		food.put("enabled", controllerConfig.useFood());
		food.put("name", foodName);
		food.put("eatAt", controllerConfig.eatAtHp());
		data.put("food", food);

		Map<String, Object> potions = new LinkedHashMap<>();
		potions.put("prayerEnabled", controllerConfig.usePrayerPotions());
		potions.put("prayerPotion", prayerPotionName);
		potions.put("combatEnabled", controllerConfig.useCombatPotions());
		potions.put("combatPotion", controllerConfig.combatPotion());
		data.put("potions", potions);

		Map<String, Object> loot = new LinkedHashMap<>();
		loot.put("enabled", config.lootEnabled());
		loot.put("items", parseList(config.lootWhitelist()));
		loot.put("minValue", config.minLootValue());
		loot.put("buryBones", config.buryBones());
		data.put("loot", loot);

		Map<String, Object> breaks = new LinkedHashMap<>();
		breaks.put("enabled", controllerConfig.takeBreaks());
		breaks.put("everyMinMinutes", controllerConfig.breakEveryMinMinutes());
		breaks.put("everyMaxMinutes", controllerConfig.breakEveryMaxMinutes());
		breaks.put("durationMinMinutes", controllerConfig.breakDurationMinMinutes());
		breaks.put("durationMaxMinutes", controllerConfig.breakDurationMaxMinutes());
		breaks.put("microBreaks", controllerConfig.microBreaks());
		data.put("breaks", breaks);

		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", controllerConfig.stopOnDeath());
		safety.put("stopOnInventoryFull", controllerConfig.stopOnInventoryFull());
		safety.put("maxRuntimeHours", controllerConfig.maxRuntimeHours());
		data.put("safety", safety);

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("enabled", config.bankEnabled());
		bank.put("teleportFallback", config.bankTeleportFallback());
		bank.put("keepItems", parseList(config.bankKeepItems()));

		List<Map<String, Object>> withdraw = new ArrayList<>();
		addWithdrawEntry(withdraw, foodName, config.bankFoodQuantity());
		addWithdrawEntry(withdraw, prayerPotionName, config.bankPrayerPotionQuantity());
		addWithdrawEntry(withdraw, controllerConfig.combatPotion(), config.bankCombatPotionQuantity());
		bank.put("withdraw", withdraw);

		data.put("bank", bank);

		Map<String, Object> pvpSafety = new LinkedHashMap<>();
		pvpSafety.put("fleeEnabled", controllerConfig.pvpFleeEnabled());
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

		data.put("eatAt", controllerConfig.eatAtHp());
		data.put("restorePrayerAt", controllerConfig.restorePrayerAt());
		data.put("prayerPotion", prayerPotionName);

		return data;
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
	public static Map<String, Object> resolveTaskLocations(SlayerConfig config)
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

	public static List<Integer> resolveTargetIds(Client client, SlayerConfig config)
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
