package com.example.controller;

/**
 * Which jewellery box is built in the player's house.
 *
 * <p>The client CANNOT see inside a POH, so this is configuration rather than something we
 * detect -- exactly as it is in the Shortest Path plugin (ShortestPathConfig.pohJewelleryBoxTier).
 * The tiers are CUMULATIVE: an ornate box offers everything a basic one does. See
 * ControllerAddon/teleports.py's poh_allows, which is a port of that plugin's
 * PathfinderConfig.checkJewelleryBoxTier.
 */
public enum JewelleryBoxTier
{
	NONE,
	BASIC,
	FANCY,
	ORNATE
}
