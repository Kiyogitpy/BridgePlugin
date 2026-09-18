package com.example.controller;

/**
 * A slayer master the task routine may ask for an assignment (spec §10). The enum NAME is what
 * /slayer-config carries; the Python side maps it to the master's displayed name in the NPC
 * Contact picker and its dialogue. KONAR is here because the wiki names her as the best
 * milestone master; her location-locked tasks need a catalog the routine does not have yet, so
 * choosing her is a config change that becomes useful in a later sub-project, not a promise.
 */
public enum SlayerMasterChoice
{
	AYA("Aya"),
	KURADAL("Kuradal"),
	KONAR("Konar quo Maten");

	private final String label;

	SlayerMasterChoice(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
