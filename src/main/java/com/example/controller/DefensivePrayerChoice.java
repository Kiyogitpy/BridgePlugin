package com.example.controller;

import net.runelite.api.Prayer;

/**
 * Selectable defensive (Protect from X) prayers for the Defensive Prayer config
 * dropdown -- always-on-while-in-combat behavior (see routines/slayer.py's
 * maintain_defensive_prayer). Enum constant names double as the wire format sent
 * to Python (see ConfigExporter) and, for entries with a real Prayer, must match
 * that Prayer's own name() -- that's how slayer.py cross-references
 * PrayerProvider's active/icons maps.
 */
public enum DefensivePrayerChoice
{
	NONE(null, "None", 1),
	PROTECT_FROM_MAGIC(Prayer.PROTECT_FROM_MAGIC, "Protect from Magic", 37),
	PROTECT_FROM_MISSILES(Prayer.PROTECT_FROM_MISSILES, "Protect from Missiles", 40),
	PROTECT_FROM_MELEE(Prayer.PROTECT_FROM_MELEE, "Protect from Melee", 43);

	private final Prayer prayer;
	private final String label;
	private final int level;

	DefensivePrayerChoice(Prayer prayer, String label, int level)
	{
		this.prayer = prayer;
		this.label = label;
		this.level = level;
	}

	public Prayer getPrayer()
	{
		return prayer;
	}

	public String getLabel()
	{
		return label;
	}

	public int getLevel()
	{
		return level;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
