package com.example.m8aq.api.ui;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Point;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.api.worldmap.WorldMap;

/** Provides current world-map and minimap view state. */
public final class Maps
{
	private Maps()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable map-view snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		WorldMap worldMap = client.getWorldMap();
		Widget root = client.getWidget(InterfaceID.Worldmap.UNIVERSE);
		return new State(root != null && !root.isHidden(), worldMap == null ? null : worldMap.getWorldMapPosition(),
			worldMap == null ? -1.0f : worldMap.getWorldMapZoom(), client.getMinimapZoom(), client.isMinimapZoom());
	}

	/** Immutable map-view state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the world-map interface is visibly open */ private final boolean worldMapOpen;
		/** @return world-map display position, or null */ private final Point worldMapPosition;
		/** @return world-map zoom, or -1 */ private final float worldMapZoom;
		/** @return current minimap zoom multiplier */ private final double minimapZoom;
		/** @return whether minimap zoom mode is enabled */ private final boolean minimapZoomEnabled;
	}
}
