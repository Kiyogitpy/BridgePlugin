package com.example.controller;

/**
 * What a gathering routine does when the inventory fills up.
 *
 * <p>Exported lowercase as the shared {@code fullInventory} config key and read by
 * ControllerAddon/gathering.py's {@code full_inventory_phase}, which drives both
 * routines/mining.py and routines/woodcutting.py. Mining defaults to BANK (ore is worth
 * keeping); woodcutting defaults to DROP, because power-chopping is the normal loop there.
 *
 * <p>BANK looks for a bank within 40 tiles, deposits everything but the profile's kept tool,
 * and walks back to the tile it was gathering from. With no bank in range it raises the same
 * safety stop STOP does, so a misconfigured BANK never turns into an idle bot.
 */
public enum FullInventoryChoice
{
	BANK,
	DROP,
	STOP
}
