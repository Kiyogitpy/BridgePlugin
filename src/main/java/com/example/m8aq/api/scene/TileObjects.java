package com.example.m8aq.api.scene;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.DecorativeObject;
import net.runelite.api.EntityOps;
import net.runelite.api.GameObject;
import net.runelite.api.GroundObject;
import net.runelite.api.ObjectComposition;
import net.runelite.api.Tile;
import net.runelite.api.TileObject;
import net.runelite.api.WallObject;
import net.runelite.api.WorldView;
import net.runelite.api.coords.WorldPoint;

/** Provides copied wall, ground, decorative, and game-object state from the loaded scene. */
public final class TileObjects
{
	private TileObjects()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable loaded-tile-object snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		WorldView worldView = client.getTopLevelWorldView();
		if (worldView == null || worldView.getScene() == null || worldView.getScene().getTiles() == null)
		{
			return new State(Collections.emptyList());
		}
		Map<String, ObjectEntry> entries = new LinkedHashMap<>();
		for (Tile[][] plane : worldView.getScene().getTiles())
		{
			if (plane == null)
			{
				continue;
			}
			for (Tile[] column : plane)
			{
				if (column == null)
				{
					continue;
				}
				for (Tile tile : column)
				{
					if (tile != null)
					{
						add(entries, client, tile.getWallObject(), "Wall", orientation(tile.getWallObject()));
						add(entries, client, tile.getGroundObject(), "Ground", -1);
						add(entries, client, tile.getDecorativeObject(), "Decorative", -1);
						GameObject[] gameObjects = tile.getGameObjects();
						if (gameObjects != null)
						{
							for (GameObject object : gameObjects)
							{
								add(entries, client, object, "Game", object == null ? -1 : object.getOrientation());
							}
						}
					}
				}
			}
		}
		return new State(Collections.unmodifiableList(new ArrayList<>(entries.values())));
	}

	private static int orientation(WallObject object)
	{
		return object == null ? -1 : object.getOrientationA();
	}

	private static void add(Map<String, ObjectEntry> entries, Client client, TileObject object,
		String type, int orientation)
	{
		if (object == null)
		{
			return;
		}
		ObjectComposition definition = client.getObjectDefinition(object.getId());
		if (definition != null && definition.getImpostorIds() != null)
		{
			try
			{
				ObjectComposition transformed = definition.getImpostor();
				definition = transformed == null ? definition : transformed;
			}
			catch (RuntimeException ignored)
			{
				// Keep the base definition when live transformation data is temporarily invalid.
			}
		}
		List<String> actions = actions(object, definition);
		if (actions.isEmpty())
		{
			return;
		}
		entries.putIfAbsent(type + ':' + object.getHash(), new ObjectEntry(object.getId(),
			definition == null ? null : definition.getName(), type, actions, object.getWorldLocation(), orientation));
	}

	private static List<String> actions(TileObject object, ObjectComposition definition)
	{
		String[] base = definition == null ? null : definition.getActions();
		String[] shown = new String[EntityOps.MAX_OPS];
		for (int i = 0; i < shown.length; i++)
		{
			if (object.isOpShown(i))
			{
				String override = object.getOpOverride(i);
				shown[i] = override != null ? override : base != null && i < base.length ? base[i] : null;
			}
		}
		return SceneActions.copy(shown);
	}

	/** Immutable loaded tile-object entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class ObjectEntry
	{
		/** @return raw object ID */ private final int id;
		/** @return transformed object name, or null */ private final String name;
		/** @return Wall, Ground, Decorative, or Game */ private final String type;
		/** @return immutable actions currently offered for this object */ private final List<String> actions;
		/** @return world coordinate */ private final WorldPoint worldPoint;
		/** @return primary orientation, or -1 */ private final int orientation;
	}

	/** Immutable collection of loaded tile objects. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return immutable deduplicated tile-object entries */ private final List<ObjectEntry> objects;
	}
}
