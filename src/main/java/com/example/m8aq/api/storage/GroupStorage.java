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

/** Provides a complete read-only snapshot of Group Ironman shared storage. */
public final class GroupStorage
{
	private GroupStorage()
	{
	}

	/**
	 * Reads the current shared-storage working copy, bank settings, and loaded interfaces.
	 * The loaded container may contain unsaved edits; {@link State#isUnsavedChanges()} identifies them.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable group-storage snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		Bank.InteractionState interaction = Bank.readInteractionState(client, accountStateLoaded);
		ItemContainer container = accountStateLoaded ? client.getItemContainer(InventoryID.INV_GROUP_TEMP) : null;
		boolean containerLoaded = ItemSlot.isLoaded(container);
		List<ItemSlot> slots = containerLoaded ? ItemSlot.copy(client, container) : Collections.emptyList();

		Widget infinite = client.getWidget(InterfaceID.SharedBank.INFINITE);
		Widget universe = client.getWidget(InterfaceID.SharedBank.UNIVERSE);
		Widget side = client.getWidget(InterfaceID.SharedBankSide.ITEMS);
		Widget unlocks = client.getWidget(InterfaceID.GimSharedBankUnlocks.UNIVERSE);
		boolean open = Interfaces.isVisible(universe);
		int capacity = open ? Interfaces.getLastInteger(client.getWidget(InterfaceID.SharedBank.CAPACITY)) : -1;
		if (capacity < 0 && containerLoaded)
		{
			capacity = slots.size();
		}
		int occupied = containerLoaded ? ItemSlot.occupiedSlotCount(slots) : -1;
		String searchQuery = accountStateLoaded && open ? Interfaces.getSearchQuery(client) : null;
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(infinite == null ? universe : infinite);
		List<Interfaces.WidgetEntry> sideWidgets = Interfaces.snapshot(side);
		List<Interfaces.WidgetEntry> unlockWidgets = Interfaces.snapshot(unlocks);

		return new State(
			open,
			Interfaces.isVisible(side),
			Interfaces.isVisible(unlocks),
			accountStateLoaded,
			containerLoaded,
			slots,
			capacity,
			occupied,
			containerLoaded && capacity >= occupied ? capacity - occupied : -1,
			containerLoaded ? ItemSlot.totalQuantity(slots) : 0,
			accountStateLoaded && client.getVarbitValue(VarbitID.GIM_SHARED_BANK_HASEDITED) == 1,
			interaction.getWithdrawalMode(),
			interaction.getCustomQuantity(),
			interaction.getWithdrawForm(),
			interaction.getRearrangeMode(),
			searchQuery != null,
			searchQuery,
			widgets,
			sideWidgets,
			unlockWidgets,
			Interfaces.widgetSnapshot(widgets, sideWidgets, unlockWidgets));
	}

	/** Immutable Group Ironman shared-storage state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether the main shared-storage interface is visible */ private final boolean open;
		/** @return whether its inventory-side component is visible */ private final boolean sideOpen;
		/** @return whether the storage-unlocks interface is visible */ private final boolean unlocksOpen;
		/** @return whether account-persistent state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether the server-synchronized working container was available */
		private final boolean containerLoaded;
		/** @return immutable positional working-copy slots */ private final List<ItemSlot> slots;
		/** @return available shared-storage slots, or -1 when unavailable */ private final int capacity;
		/** @return occupied working-copy slots, or -1 when unavailable */ private final int occupiedSlotCount;
		/** @return empty working-copy slots, or -1 when unavailable */ private final int emptySlotCount;
		/** @return total quantity across the loaded working copy */ private final long totalQuantity;
		/** @return whether the current working copy has unsaved edits */ private final boolean unsavedChanges;
		/** @return selected default deposit/withdraw quantity */ private final Bank.WithdrawalMode withdrawalMode;
		/** @return configured X quantity */ private final int customQuantity;
		/** @return selected item-or-note withdrawal form */ private final Bank.WithdrawForm withdrawForm;
		/** @return selected swap-or-insert rearrangement mode */ private final Bank.RearrangeMode rearrangeMode;
		/** @return whether shared-storage search is active */ private final boolean searchActive;
		/** @return active raw search query, otherwise null */ private final String searchQuery;
		/** @return complete fixed and dynamic shared-storage widget tree */
		private final List<Interfaces.WidgetEntry> widgets;
		/** @return complete inventory-side widget tree */ private final List<Interfaces.WidgetEntry> sideWidgets;
		/** @return complete fixed and dynamic storage-unlocks widget tree */
		private final List<Interfaces.WidgetEntry> unlockWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		/**
		 * Returns one positional working-copy item.
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
