package com.example.util;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

public final class FoodUtil
{
	// LinkedHashMap, not HashMap -- getHealAmount matches in insertion order, so the
	// blighted variants below must come before their regular-food counterparts. Both
	// "Blighted manta ray" and "Blighted anglerfish" also contain "manta ray"/"anglerfish",
	// so if the regular entry were checked first (a plain HashMap gives no ordering
	// guarantee either way), a blighted item would silently report the regular item's
	// (higher) heal value instead of its own lower one.
	private static final Map<String, Integer> HEAL_BY_NAME = new LinkedHashMap<>();

	static
	{
		// TODO verify these exact heal amounts in-game/wiki -- recalled, not confirmed.
		HEAL_BY_NAME.put("blighted manta ray", 8);
		HEAL_BY_NAME.put("blighted anglerfish", 8);
		HEAL_BY_NAME.put("blighted karambwan", 8);

		HEAL_BY_NAME.put("shark", 20);
		HEAL_BY_NAME.put("manta ray", 22);
		HEAL_BY_NAME.put("dark crab", 22);
		HEAL_BY_NAME.put("anglerfish", 22);
		HEAL_BY_NAME.put("karambwan", 18);
		HEAL_BY_NAME.put("monkfish", 16);
		HEAL_BY_NAME.put("lobster", 12);
		HEAL_BY_NAME.put("swordfish", 14);
		HEAL_BY_NAME.put("tuna", 10);
		HEAL_BY_NAME.put("salmon", 9);
		HEAL_BY_NAME.put("trout", 7);
		HEAL_BY_NAME.put("bread", 5);
		HEAL_BY_NAME.put("cake", 12);
		HEAL_BY_NAME.put("pizza", 14);
		HEAL_BY_NAME.put("cooked karambwan", 18);
	}

	private FoodUtil()
	{
	}

	public static int getHealAmount(String itemName)
	{
		if (itemName == null)
		{
			return 0;
		}

		String lower = itemName.toLowerCase(Locale.ENGLISH);
		for (Map.Entry<String, Integer> entry : HEAL_BY_NAME.entrySet())
		{
			if (lower.contains(entry.getKey()))
			{
				return entry.getValue();
			}
		}
		return 0;
	}

	public static boolean isFood(String itemName)
	{
		return getHealAmount(itemName) > 0;
	}

	public static boolean isPrayerPotion(String itemName)
	{
		if (itemName == null)
		{
			return false;
		}

		String lower = itemName.toLowerCase(Locale.ENGLISH);
		return lower.contains("prayer potion")
			|| lower.contains("super restore")
			|| lower.contains("blighted super restore")
			|| lower.contains("sanfew serum")
			|| lower.contains("moonlight potion")
			|| lower.contains("restore potion");
	}
}
