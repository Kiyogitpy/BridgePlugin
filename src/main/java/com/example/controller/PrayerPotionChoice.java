package com.example.controller;

/** Selectable prayer-restore items for the Prayer Potion/Wilderness Prayer Potion config
 * dropdowns -- kept in sync with the names FoodUtil.isPrayerPotion recognizes, since a choice
 * here only does anything useful if the bridge's ConsumablesProvider actually detects it as a
 * prayer restore and preference-sorts it accordingly. */
public enum PrayerPotionChoice
{
	PRAYER_POTION("Prayer potion"),
	SUPER_RESTORE("Super restore"),
	SANFEW_SERUM("Sanfew serum"),
	MOONLIGHT_POTION("Moonlight potion"),
	BLIGHTED_SUPER_RESTORE("Blighted super restore");

	private final String itemName;

	PrayerPotionChoice(String itemName)
	{
		this.itemName = itemName;
	}

	public String getItemName()
	{
		return itemName;
	}

	@Override
	public String toString()
	{
		return itemName;
	}
}
