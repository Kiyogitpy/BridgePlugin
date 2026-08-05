package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.MenuAction;
import net.runelite.api.Tile;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.MenuEntryAdded;
import net.runelite.client.config.ConfigManager;

/**
 * Injects a "Mark Safespot" option into the ground's right-click menu, alongside the game's own
 * "Walk here" -- the same technique RuneLite's own Ground Markers plugin uses to add
 * tile-targeted options (GroundMarkerPlugin.onMenuEntryAdded, confirmed against the decompiled
 * client source). No arming/mode toggle: the option is always available, exactly like "Walk
 * here" always is, and it never fires on a normal walk-click since it's a distinct entry the
 * player has to deliberately select.
 *
 * CONFIRMED live 2026-08-06 (user report -- marking a safespot inside the Blast Furnace
 * minigame, an instanced area, sent the player on an unstoppable walk toward a nonsense
 * location once the plugin later tried to return there): the original version of this class
 * captured the clicked tile via event.getActionParam0()/getActionParam1() (scene coordinates)
 * fed into WorldPoint.fromScene(client, sceneX, sceneY, plane) -- that conversion is NOT
 * instance-aware, so inside any instanced area (a minigame, a dungeon, a boss room) it produces
 * a raw instance-template coordinate instead of the real, reachable world location. Fixed by
 * switching to the same resolution GroundMarkerPlugin itself uses: resolve the actually-selected
 * tile via WorldView.getSelectedSceneTile() and convert with WorldPoint.fromLocalInstance(),
 * which does account for instance template remapping.
 */
final class SafespotMarker
{
	private SafespotMarker()
	{
	}

	static void apply(Client client, ConfigManager configManager, MenuEntryAdded event)
	{
		MenuAction menuAction = event.getMenuEntry().getType();
		if (menuAction != MenuAction.WALK && menuAction != MenuAction.SET_HEADING)
		{
			return;
		}

		int worldViewId = event.getMenuEntry().getWorldViewId();
		WorldView worldView = client.getWorldView(worldViewId);
		if (worldView == null)
		{
			return;
		}

		Tile selectedSceneTile = worldView.getSelectedSceneTile();
		if (selectedSceneTile == null)
		{
			return;
		}

		LocalPoint local = selectedSceneTile.getLocalLocation();

		client.createMenuEntry(-1)
			.setOption("Mark Safespot")
			.setTarget(event.getTarget())
			.setType(MenuAction.RUNELITE)
			.onClick(entry -> {
				WorldPoint point = WorldPoint.fromLocalInstance(client, local);
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotX", point.getX());
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotY", point.getY());
				configManager.setConfiguration(SlayerConfig.GROUP, "safespotPlane", point.getPlane());
			});
	}
}
