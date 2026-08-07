package com.example.controller;

/** Selectable prayer-restore items for the Prayer Potion/Wilderness Prayer Potion config
 * dropdowns -- kept in sync with the names FoodUtil.isPrayerPotion recognizes, since a choice
 * here only does anything useful if the bridge's ConsumablesProvider actually detects it as a
 * prayer restore and preference-sorts it accordingly. ANY has an empty item name deliberately --
 * ConsumablesProvider skips preference-sorting and ConfigExporter skips the bank withdraw entry
 * when the name is blank, which is exactly "no preferred prayer restore, just drink whatever
 * recognized one is already in the inventory". */
public enum PrayerPotionChoice
{
	ANY("", "Any prayer restore"),
	PRAYER_POTION("Prayer potion"),
	SUPER_RESTORE("Super restore"),
	SANFEW_SERUM("Sanfew serum"),
	MOONLIGHT_POTION("Moonlight potion"),
	MOONLIGHT_MOTH_MIX("Moonlight moth mix"),
	BLIGHTED_SUPER_RESTORE("Blighted super restore");

	private final String itemName;
	private final String displayName;

	PrayerPotionChoice(String itemName)
	{
		this(itemName, itemName);
	}

	PrayerPotionChoice(String itemName, String displayName)
	{
		this.itemName = itemName;
		this.displayName = displayName;
	}

	public String getItemName()
	{
		return itemName;
	}

	@Override
	public String toString()
	{
		return displayName;
	}
}
