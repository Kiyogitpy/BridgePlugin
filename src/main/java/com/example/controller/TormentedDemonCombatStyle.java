package com.example.controller;

/**
 * Wire values (name(), lowercased by ConfigExporter's exportTormentedDemon) match
 * routines/tormented_demon.py's OVERHEAD_ICON_TO_STYLE / loadouts dict keys exactly --
 * keep both in sync if either changes.
 */
public enum TormentedDemonCombatStyle
{
	MELEE("Melee"),
	RANGED("Ranged"),
	MAGIC("Magic");

	private final String label;

	TormentedDemonCombatStyle(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
