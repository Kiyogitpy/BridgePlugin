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
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.ui.Interfaces;

/** Provides complete copied state for Items Kept on Death and Death's Office storage. */
public final class DeathRecovery
{
	private DeathRecovery()
	{
	}

	/**
	 * Reads loaded death-storage containers and complete interface widget trees.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable death-recovery snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		ItemContainer temporary = accountStateLoaded
			? client.getItemContainer(InventoryID.DEATHKEEP_ITEMS) : null;
		ItemContainer permanent = accountStateLoaded
			? client.getItemContainer(InventoryID.DEATH_PERMANENT) : null;
		boolean temporaryItemsLoaded = ItemSlot.isLoaded(temporary);
		boolean permanentItemsLoaded = ItemSlot.isLoaded(permanent);
		List<ItemSlot> temporaryItems = temporaryItemsLoaded
			? ItemSlot.copy(client, temporary) : Collections.emptyList();
		List<ItemSlot> permanentItems = permanentItemsLoaded
			? ItemSlot.copy(client, permanent) : Collections.emptyList();
		Widget deathkeep = client.getWidget(InterfaceID.Deathkeep.UNIVERSE);
		Widget deathOffice = client.getWidget(InterfaceID.DeathOffice.UNIVERSE);
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(deathkeep);
		List<Interfaces.WidgetEntry> deathOfficeWidgets = Interfaces.snapshot(deathOffice);

		return new State(
			Interfaces.isVisible(deathkeep),
			Interfaces.isVisible(deathOffice),
			accountStateLoaded,
			temporaryItemsLoaded,
			permanentItemsLoaded,
			temporaryItems,
			permanentItems,
			temporaryItemsLoaded ? temporaryItems.size() : -1,
			permanentItemsLoaded ? permanentItems.size() : -1,
			temporaryItemsLoaded ? ItemSlot.occupiedSlotCount(temporaryItems) : -1,
			permanentItemsLoaded ? ItemSlot.occupiedSlotCount(permanentItems) : -1,
			temporaryItemsLoaded ? ItemSlot.totalQuantity(temporaryItems) : 0,
			permanentItemsLoaded ? ItemSlot.totalQuantity(permanentItems) : 0,
			widgets,
			deathOfficeWidgets,
			Interfaces.widgetSnapshot(widgets, deathOfficeWidgets));
	}

	/** Immutable death-recovery state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether the Items Kept on Death interface is visible */ private final boolean open;
		/** @return whether Death's Office item retrieval is visible */ private final boolean deathOfficeOpen;
		/** @return whether account-scoped state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether the temporary death-item container was available */
		private final boolean temporaryItemsLoaded;
		/** @return whether the permanent Death's Office container was available */
		private final boolean permanentItemsLoaded;
		/** @return immutable temporary death-item slots */ private final List<ItemSlot> temporaryItems;
		/** @return immutable permanent death-storage slots */ private final List<ItemSlot> permanentItems;
		/** @return temporary source capacity, or -1 when unavailable */ private final int temporaryCapacity;
		/** @return permanent source capacity, or -1 when unavailable */ private final int permanentCapacity;
		/** @return occupied temporary slots, or -1 when unavailable */
		private final int temporaryOccupiedSlotCount;
		/** @return occupied permanent slots, or -1 when unavailable */
		private final int permanentOccupiedSlotCount;
		/** @return total quantity in the temporary container */ private final long temporaryTotalQuantity;
		/** @return total quantity in permanent Death's Office storage */
		private final long permanentTotalQuantity;
		/** @return complete Items Kept on Death widget tree */ private final List<Interfaces.WidgetEntry> widgets;
		/** @return complete Death's Office item-retrieval widget tree */
		private final List<Interfaces.WidgetEntry> deathOfficeWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		/** @return whether either supported death-recovery interface is visible */
		public boolean isAnyOpen()
		{
			return open || deathOfficeOpen;
		}

	}
}
