package com.example.m8aq.api.scene;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.Tile;
import net.runelite.api.TileItem;
import net.runelite.api.WorldView;
import net.runelite.api.coords.WorldPoint;

/** Provides copied ground-item state from every loaded top-level scene tile. */
public final class GroundItems
{
	private static final List<String> TAKE_ACTION = Collections.singletonList("Take");

	private GroundItems()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable loaded-ground-item snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		WorldView worldView = client.getTopLevelWorldView();
		if (worldView == null || worldView.getScene() == null || worldView.getScene().getTiles() == null)
		{
			return new State(Collections.emptyList());
		}
		List<GroundItemEntry> entries = new ArrayList<>();
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
					if (tile == null || tile.getGroundItems() == null)
					{
						continue;
					}
					WorldPoint point = tile.getWorldLocation();
					for (TileItem item : tile.getGroundItems())
					{
						int ownership = item.getOwnership();
						if (item.isPrivate() && ownership != TileItem.OWNERSHIP_SELF
							&& ownership != TileItem.OWNERSHIP_GROUP)
						{
							continue;
						}
						ItemComposition definition = client.getItemDefinition(item.getId());
						entries.add(new GroundItemEntry(item.getId(), definition == null ? null : definition.getName(),
							item.getQuantity(), point, TAKE_ACTION, ownership(ownership), item.isPrivate(),
							remainingTicks(item.getDespawnTime(), client.getTickCount())));
					}
				}
			}
		}
		return new State(Collections.unmodifiableList(entries));
	}

	private static int remainingTicks(int eventTick, int currentTick)
	{
		return eventTick <= 0 ? -1 : Math.max(0, eventTick - currentTick);
	}

	private static Ownership ownership(int value)
	{
		switch (value)
		{
			case TileItem.OWNERSHIP_SELF: return Ownership.SELF;
			case TileItem.OWNERSHIP_OTHER: return Ownership.OTHER;
			case TileItem.OWNERSHIP_GROUP: return Ownership.GROUP;
			case TileItem.OWNERSHIP_NONE: return Ownership.NONE;
			default: return Ownership.UNKNOWN;
		}
	}

	/** Named ground-item owner category. */
	public enum Ownership
	{
		NONE,
		SELF,
		OTHER,
		GROUP,
		UNKNOWN
	}

	/** Immutable loaded-ground-item entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class GroundItemEntry
	{
		/** @return raw item ID */ private final int itemId;
		/** @return canonical item name, or null */ private final String itemName;
		/** @return quantity */ private final int quantity;
		/** @return tile coordinate */ private final WorldPoint worldPoint;
		/** @return immutable actions currently offered for this item */ private final List<String> actions;
		/** @return named ownership category */ private final Ownership ownership;
		/** @return whether private to an owner */ private final boolean privateItem;
		/** @return server ticks until despawn, or -1 when no despawn tick is known */ private final int despawnsInTicks;
	}

	/** Immutable collection of loaded ground items. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return immutable loaded ground-item entries */ private final List<GroundItemEntry> items;
	}
}
