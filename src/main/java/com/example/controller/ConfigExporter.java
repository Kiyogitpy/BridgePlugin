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

	public static Map<String, Object> export(Client client, ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();

		data.put("enabled", config.controllerEnabled());
		data.put("profile", config.profile().name());
		data.put("targets", resolveTargetIds(client, config));
		data.put("targetNames", parseList(config.targetNpcNames()));
		data.put("maxTargetDistance", config.maxTargetDistance());
		data.put("skipNpcInCombat", config.skipNpcInCombat());
		data.put("onlyAttackWhenIdle", config.onlyAttackWhenIdle());

		Map<String, Object> prayer = new LinkedHashMap<>();
		prayer.put("enabled", config.usePrayer());
		prayer.put("combatPrayer", config.combatPrayer().name());
		prayer.put("useQuickPrayers", config.useQuickPrayers());
		prayer.put("restoreAt", config.restorePrayerAt());
		data.put("prayer", prayer);

		String foodName = config.useWildernessSupplies() ? config.wildernessFood() : config.food();
		String prayerPotionName = config.useWildernessSupplies() ? config.wildernessPrayerPotion() : config.prayerPotion();

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

		data.put("eatAt", config.eatAtHp());
		data.put("restorePrayerAt", config.restorePrayerAt());
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
