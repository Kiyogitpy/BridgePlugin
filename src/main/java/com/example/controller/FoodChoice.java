package com.example.controller;

/** Selectable food items for the Food/Wilderness Food config dropdowns -- kept in sync with the
 * names FoodUtil.HEAL_BY_NAME recognizes, since a choice here only does anything useful if the
 * bridge's ConsumablesProvider actually detects it as food and preference-sorts it accordingly. */
public enum FoodChoice
{
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
	BLIGHTED_KARAMBWAN("Blighted karambwan");

	private final String itemName;

	FoodChoice(String itemName)
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
