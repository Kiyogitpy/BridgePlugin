package com.example.controller;

import net.runelite.api.Prayer;

public enum CombatPrayer
{
	NONE(null, "None"),
	PROTECT_FROM_MELEE(Prayer.PROTECT_FROM_MELEE, "Protect from Melee"),
	PROTECT_FROM_MISSILES(Prayer.PROTECT_FROM_MISSILES, "Protect from Missiles"),
	PROTECT_FROM_MAGIC(Prayer.PROTECT_FROM_MAGIC, "Protect from Magic"),
	PIETY(Prayer.PIETY, "Piety"),
	RIGOUR(Prayer.RIGOUR, "Rigour"),
	AUGURY(Prayer.AUGURY, "Augury"),
	EAGLE_EYE(Prayer.EAGLE_EYE, "Eagle Eye"),
	MYSTIC_MIGHT(Prayer.MYSTIC_MIGHT, "Mystic Might"),
	STEEL_SKIN(Prayer.STEEL_SKIN, "Steel Skin"),
	ULTIMATE_STRENGTH(Prayer.ULTIMATE_STRENGTH, "Ultimate Strength"),
	INCREDIBLE_REFLEXES(Prayer.INCREDIBLE_REFLEXES, "Incredible Reflexes");

	private final Prayer prayer;
	private final String label;

	CombatPrayer(Prayer prayer, String label)
	{
		this.prayer = prayer;
		this.label = label;
	}

	public Prayer getPrayer()
	{
		return prayer;
	}

	public String getLabel()
	{
		return label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
