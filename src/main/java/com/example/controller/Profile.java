package com.example.controller;

/**
 * Which controller.py routine should run. The enum name (not the label) is
 * sent over the bridge as-is and must match a key in controller.py's
 * PROFILE_ROUTINES map, e.g. SLAYER -> routines/slayer.py.
 */
public enum Profile
{
	SLAYER("Slayer / Combat"),
	MINING("Mining");

	private final String label;

	Profile(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
