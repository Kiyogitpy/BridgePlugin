package com.example.util;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

public final class FoodUtil
{
	private static final Map<String, Integer> HEAL_BY_NAME = new HashMap<>();

	static
	{
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
			|| lower.contains("sanfew serum")
			|| lower.contains("moonlight potion")
			|| lower.contains("restore potion");
	}
}
