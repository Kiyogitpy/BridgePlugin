package com.example.m8aq.api.scene;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import java.util.stream.Collectors;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Tile;
import net.runelite.api.WorldView;
import net.runelite.api.coords.WorldPoint;

/** Provides top-level loaded-scene metadata. */
public final class Scene
{
	private Scene()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable scene snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		WorldView worldView = client.getTopLevelWorldView();
		if (worldView == null || worldView.getScene() == null)
		{
			return new State(-1, -1, -1, -1, -1, false, Collections.emptyList(), null);
		}
		int[] regions = worldView.getMapRegions();
		Tile selected = worldView.getSelectedSceneTile();
		List<Integer> regionIds = regions == null ? Collections.emptyList()
			: Collections.unmodifiableList(Arrays.stream(regions).boxed().collect(Collectors.toList()));
		return new State(worldView.getId(), worldView.getBaseX(), worldView.getBaseY(), worldView.getPlane(),
			worldView.getScene().getDrawDistance(), worldView.isInstance(), regionIds,
			selected == null ? null : selected.getWorldLocation());
	}

	/** Immutable loaded-scene state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return top-level world-view ID, or -1 */ private final int worldViewId;
		/** @return scene base X, or -1 */ private final int baseX;
		/** @return scene base Y, or -1 */ private final int baseY;
		/** @return active plane, or -1 */ private final int plane;
		/** @return scene draw distance, or -1 */ private final int drawDistance;
		/** @return whether the scene is instanced */ private final boolean instanced;
		/** @return immutable loaded map-region IDs */ private final List<Integer> mapRegionIds;
		/** @return selected scene tile, or null */ private final WorldPoint selectedTile;
	}
}
