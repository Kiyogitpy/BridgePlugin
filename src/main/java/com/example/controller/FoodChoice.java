package com.example.controller;

/** Selectable food items for the Food/Wilderness Food config dropdowns -- kept in sync with the
 * names FoodUtil.HEAL_BY_NAME recognizes, since a choice here only does anything useful if the
 * bridge's ConsumablesProvider actually detects it as food and preference-sorts it accordingly.
 * ANY has an empty item name deliberately -- ConsumablesProvider skips preference-sorting and
 * ConfigExporter skips the bank withdraw entry when the name is blank, which is exactly "no
 * preferred food, just eat whatever recognized food is already in the inventory". */
public enum FoodChoice
{
	ANY("", "Any food"),
	SHARK("Shark"),
	MANTA_RAY("Manta ray"),
	DARK_CRAB("Dark crab"),
	ANGLERFISH("Anglerfish"),
	KARAMBWAN("Karambwan"),
	MONKFISH("Monkfish"),
	LOBSTER("Lobster"),
	SWORDFISH("Swordfish"),
	TUNA("Tuna"),
	SALMON("Salmon"),
	TROUT("Trout"),
	BREAD("Bread"),
	CAKE("Cake"),
	PIZZA("Pizza"),
	BLIGHTED_MANTA_RAY("Blighted manta ray"),
	BLIGHTED_ANGLERFISH("Blighted anglerfish"),
	BLIGHTED_KARAMBWAN("Blighted karambwan"),
	COOKED_DASHING_KEBBIT("Cooked dashing kebbit");

	private final String itemName;
	private final String displayName;

	FoodChoice(String itemName)
	{
		this(itemName, itemName);
	}

	FoodChoice(String itemName, String displayName)
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
