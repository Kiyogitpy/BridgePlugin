package com.example.controller;

/**
 * Which bar the Blast Furnace routine smelts. .name().toLowerCase() (done by
 * ConfigExporter's exportBlastFurnace) must match a key in routines/blast_furnace.py's BAR_TYPES
 * dict exactly -- keep both in sync if either changes. Levels are the Smithing requirement
 * to smelt that bar, per Blast Furnace.txt's "Experience rates" table.
 */
public enum BlastFurnaceBarType
{
	BRONZE("Bronze", 1),
	IRON("Iron", 15),
	SILVER("Silver", 20),
	STEEL("Steel", 30),
	GOLD("Gold", 40),
	MITHRIL("Mithril", 50),
	ADAMANTITE("Adamantite", 70),
	RUNITE("Runite", 85);

	private final String label;
	private final int level;

	BlastFurnaceBarType(String label, int level)
	{
		this.label = label;
		this.level = level;
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
