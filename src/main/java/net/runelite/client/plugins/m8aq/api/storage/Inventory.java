package net.runelite.client.plugins.m8aq.api.storage;

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
import net.runelite.api.widgets.Widget;
import net.runelite.client.plugins.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of the local player's backpack inventory. */
public final class Inventory
{
	/** Standard backpack capacity. */
	public static final int CAPACITY = 28;

	private Inventory()
	{
	}

	/**
	 * Reads the current backpack state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable inventory snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		ItemContainer container = accountStateLoaded ? client.getItemContainer(InventoryID.INV) : null;
		boolean containerLoaded = ItemSlot.isLoaded(container);
		List<ItemSlot> slots = containerLoaded ? ItemSlot.copy(client, container) : Collections.emptyList();
		int occupied = containerLoaded ? ItemSlot.occupiedSlotCount(slots) : -1;
		Widget inventory = client.getWidget(InterfaceID.Inventory.ITEMS);
		Widget noops = client.getWidget(InterfaceID.InventoryNoops.ITEMS);
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(inventory);
		List<Interfaces.WidgetEntry> noopsWidgets = Interfaces.snapshot(noops);
		return new State(
			accountStateLoaded,
			containerLoaded,
			slots,
			CAPACITY,
			occupied,
			containerLoaded ? CAPACITY - occupied : -1,
			containerLoaded ? ItemSlot.totalQuantity(slots) : 0,
			Interfaces.isVisible(inventory),
			Interfaces.isVisible(noops),
			widgets,
			noopsWidgets,
			Interfaces.widgetSnapshot(widgets, noopsWidgets));
	}

	/** Immutable backpack state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether account-scoped state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether the server-synchronized backpack container was available */
		private final boolean containerLoaded;
		/** @return immutable materialized positional slots; trailing empty slots may be omitted */
		private final List<ItemSlot> slots;
		/** @return fixed backpack capacity */ private final int capacity;
		/** @return occupied slot count, or -1 when unavailable */ private final int occupiedSlotCount;
		/** @return number of empty slots, or -1 when unavailable */
		private final int emptySlotCount;
		/** @return total quantity across loaded slots */ private final long totalQuantity;
		/** @return whether the normal inventory component is visible */ private final boolean open;
		/** @return whether the no-operations inventory component is visible */ private final boolean noopsOpen;
		/** @return complete normal inventory widget subtree */ private final List<Interfaces.WidgetEntry> widgets;
		/** @return complete no-operations inventory widget subtree */
		private final List<Interfaces.WidgetEntry> noopsWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		/**
		 * Returns a positional slot.
		 *
		 * @param slot zero-based slot index
		 * @return immutable slot, or {@code null} when out of bounds
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

		/**
		 * Checks for an exact raw item ID.
		 *
		 * @param itemId raw item ID
		 * @return whether any slot contains the ID
		 */
		public boolean contains(int itemId)
		{
			for (ItemSlot slot : slots)
			{
				if (slot.getItemId() == itemId)
				{
					return true;
				}
			}
			return false;
		}

	}
}
