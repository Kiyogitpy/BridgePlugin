package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.MenuAction;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.MenuEntryAdded;
import net.runelite.client.config.ConfigManager;

/**
 * Injects a "Mark Safespot" option into the ground's right-click menu, alongside the game's own
 * "Walk here" -- the same technique RuneLite's own Ground Markers plugin uses to add
 * tile-targeted options. No arming/mode toggle: the option is always available, exactly like
 * "Walk here" always is, and it never fires on a normal walk-click since it's a distinct entry
 * the player has to deliberately select.
 *
 * Scene coordinates are captured from the "Walk here" entry that triggered this addition (not
 * re-read from the synthetic entry at click time) and closed over in the onClick lambda -- this
 * sidesteps any question of whether a client.createMenuEntry(...) entry carries the same params
 * back out the way the original one did.
 */
final class SafespotMarker
{
	private SafespotMarker()
	{
	}

	static void apply(Client client, ConfigManager configManager, MenuEntryAdded event)
	{
		if (!"Walk here".equals(event.getOption()))
		{
			return;
		}

		int sceneX = event.getActionParam0();
		int sceneY = event.getActionParam1();
		int plane = client.getPlane();

		client.createMenuEntry(-1)
			.setOption("Mark Safespot")
			.setTarget(event.getTarget())
			.setType(MenuAction.RUNELITE)
			.onClick(entry -> {
				WorldPoint point = WorldPoint.fromScene(client, sceneX, sceneY, plane);
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotX", point.getX());
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotY", point.getY());
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotPlane", point.getPlane());
			});
	}
}
