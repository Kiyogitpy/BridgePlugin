package com.example.controller;

/**
 * Which controller.py routine should run. The enum name (not the label) is sent over the
 * bridge as-is and must match a key in controller.py's ROUTINE_FILES map, e.g. SLAYER ->
 * routines/slayer.py. Defaults to NONE so a fresh install doesn't silently start acting the
 * moment Controller Enabled is checked -- a routine has to be deliberately picked first.
 */
public enum RoutineChoice
{
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon"),
	SHIPWRECK_SALVAGING("Shipwreck Salvaging"),
	FIREMAKING("Firemaking"),
	BLAST_FURNACE("Blast Furnace"),
	GOLEM_CRAFTING("Golem Crafting"),
	MASTERING_MIXOLOGY("Mastering Mixology"),
	PEST_CONTROL("Pest Control");

	private final String label;

	RoutineChoice(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
