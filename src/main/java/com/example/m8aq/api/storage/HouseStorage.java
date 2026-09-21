package com.example.m8aq.api.storage;

import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.ui.Interfaces;

/** Provides a complete read-only snapshot of player-owned-house costume storage. */
public final class HouseStorage
{
	/** Raw other-player/container namespace flag used by client scripts. */
	public static final int OTHER_CONTAINER_FLAG = 0x8000;
	/** Raw server container used by {@code invother} costume-room scripts. */
	public static final int STORED_ITEMS_CONTAINER = InventoryID.POH_COSTUMES | OTHER_CONTAINER_FLAG;
	private static final int SEARCH_MESSAGE_LAYER_MODE = 17;

	private HouseStorage()
	{
	}

	/**
	 * Reads costume-room contents, configuration, search, and loaded widgets.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable house-storage snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		ItemContainer container = accountStateLoaded ? client.getItemContainer(STORED_ITEMS_CONTAINER) : null;
		int sourceContainerId = STORED_ITEMS_CONTAINER;
		if (!ItemSlot.isLoaded(container) && accountStateLoaded)
		{
			container = client.getItemContainer(InventoryID.POH_COSTUMES);
			sourceContainerId = InventoryID.POH_COSTUMES;
		}
		boolean containerLoaded = ItemSlot.isLoaded(container);
		List<ItemSlot> slots = containerLoaded ? ItemSlot.copy(client, container) : Collections.emptyList();
		if (!containerLoaded)
		{
			sourceContainerId = -1;
		}

		Widget infinite = client.getWidget(InterfaceID.PohCostumes.INFINITE);
		Widget universe = client.getWidget(InterfaceID.PohCostumes.UNIVERSE);
		Widget side = client.getWidget(InterfaceID.PohCostumesSide.ITEMS);
		boolean open = Interfaces.isVisible(universe);
		String searchQuery = accountStateLoaded && open
			? Interfaces.getSearchQuery(client, SEARCH_MESSAGE_LAYER_MODE) : null;
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(infinite == null ? universe : infinite);
		List<Interfaces.WidgetEntry> sideWidgets = Interfaces.snapshot(side);
		return new State(
			open,
			Interfaces.isVisible(side),
			accountStateLoaded,
			containerLoaded,
			sourceContainerId,
			slots,
			containerLoaded ? ItemSlot.occupiedSlotCount(slots) : -1,
			containerLoaded ? ItemSlot.totalQuantity(slots) : 0,
			accountStateLoaded
				? StorageMode.fromRaw(client.getVarbitValue(VarbitID.POH_COSTUMES_MODE))
				: StorageMode.UNKNOWN,
			searchQuery != null,
			searchQuery,
			widgets,
			sideWidgets,
			Interfaces.widgetSnapshot(widgets, sideWidgets));
	}

	/** Current costume-room item operation mode. */
	public enum StorageMode
	{
		WITHDRAW,
		DEPOSIT,
		UNKNOWN;

		private static StorageMode fromRaw(int value)
		{
			return value == 0 ? WITHDRAW : value == 1 ? DEPOSIT : UNKNOWN;
		}
	}

	/** Immutable player-owned-house costume storage state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether the main costume-room interface is visible */ private final boolean open;
		/** @return whether its inventory-side component is visible */ private final boolean sideOpen;
		/** @return whether account-persistent state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether a server-synchronized costume container was available */
		private final boolean containerLoaded;
		/** @return exact raw container ID used, or -1 when unavailable */ private final int sourceContainerId;
		/** @return immutable positional costume-storage slots */ private final List<ItemSlot> slots;
		/** @return occupied source slots, or -1 when unavailable */ private final int occupiedSlotCount;
		/** @return total quantity across loaded source slots */ private final long totalQuantity;
		/** @return selected withdraw-or-deposit mode */ private final StorageMode mode;
		/** @return whether the costume search message layer is active */ private final boolean searchActive;
		/** @return active raw search query, otherwise null */ private final String searchQuery;
		/** @return complete fixed and dynamic main-interface widget tree */
		private final List<Interfaces.WidgetEntry> widgets;
		/** @return complete inventory-side widget tree */ private final List<Interfaces.WidgetEntry> sideWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		/**
		 * Returns one positional stored item.
		 *
		 * @param slot zero-based source slot
		 * @return immutable slot, or null when out of bounds
		 */
		public ItemSlot getItem(int slot)
		{
			return slot >= 0 && slot < slots.size() ? slots.get(slot) : null;
		}

		/**
		 * Counts an exact raw item ID.
		 *
		 * @param itemId raw item ID
		 * @return total quantity
		 */
		public int count(int itemId)
		{
			int total = 0;
			for (ItemSlot slot : slots)
			{
				if (slot.getItemId() == itemId)
				{
					total += slot.getQuantity();
				}
			}
			return total;
		}

	}
}
