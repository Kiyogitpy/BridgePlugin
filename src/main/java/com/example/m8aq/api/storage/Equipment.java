package com.example.m8aq.api.storage;

import java.util.Collections;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.EquipmentInventorySlot;
import net.runelite.api.GameState;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of the local player's equipped items. */
public final class Equipment
{
	private Equipment()
	{
	}

	/**
	 * Reads the current equipment state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable equipment snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		ItemContainer container = accountStateLoaded ? client.getItemContainer(InventoryID.WORN) : null;
		boolean containerLoaded = ItemSlot.isLoaded(container);
		List<ItemSlot> items = containerLoaded ? ItemSlot.copy(client, container) : Collections.emptyList();
		Map<EquipmentInventorySlot, ItemSlot> slots = new EnumMap<>(EquipmentInventorySlot.class);
		if (containerLoaded)
		{
			for (EquipmentInventorySlot slot : EquipmentInventorySlot.values())
			{
				int index = slot.getSlotIdx();
				slots.put(slot, index < items.size()
					? items.get(index)
					: new ItemSlot(index, -1, null, 0));
			}
		}

		Widget worn = client.getWidget(InterfaceID.Wornitems.UNIVERSE);
		Widget stats = client.getWidget(InterfaceID.Equipment.UNIVERSE);
		Widget side = client.getWidget(InterfaceID.EquipmentSide.ITEMS);
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(worn);
		List<Interfaces.WidgetEntry> statsWidgets = Interfaces.snapshot(stats);
		List<Interfaces.WidgetEntry> sideWidgets = Interfaces.snapshot(side);
		return new State(
			accountStateLoaded,
			containerLoaded,
			Collections.unmodifiableMap(slots),
			containerLoaded ? ItemSlot.occupiedSlotCount(items) : -1,
			containerLoaded ? ItemSlot.totalQuantity(items) : 0,
			Interfaces.isVisible(worn),
			Interfaces.isVisible(stats),
			Interfaces.isVisible(side),
			widgets,
			statsWidgets,
			sideWidgets,
			Interfaces.widgetSnapshot(widgets, statsWidgets, sideWidgets));
	}

	/** Immutable equipment state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether account-scoped state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether the server-synchronized equipment container was available */
		private final boolean containerLoaded;
		/** @return immutable item value for every semantic equipment slot */
		private final Map<EquipmentInventorySlot, ItemSlot> slots;
		/** @return occupied equipment slots, or -1 when unavailable */ private final int occupiedSlotCount;
		/** @return total quantity across loaded equipment slots */ private final long totalQuantity;
		/** @return whether the normal worn-items panel is visible */ private final boolean open;
		/** @return whether the equipment-statistics interface is visible */ private final boolean statsOpen;
		/** @return whether the equipment side inventory is visible */ private final boolean sideOpen;
		/** @return complete normal worn-items widget tree */ private final List<Interfaces.WidgetEntry> widgets;
		/** @return complete equipment-statistics widget tree */
		private final List<Interfaces.WidgetEntry> statsWidgets;
		/** @return complete equipment side-inventory widget tree */
		private final List<Interfaces.WidgetEntry> sideWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		/**
		 * Returns an item by semantic equipment slot.
		 *
		 * @param slot equipment slot
		 * @return immutable slot, or {@code null} when unavailable or given null
		 */
		public ItemSlot getItem(EquipmentInventorySlot slot)
		{
			return slot == null ? null : slots.get(slot);
		}

		/**
		 * Checks whether an exact raw item ID is equipped.
		 *
		 * @param itemId raw item ID
		 * @return whether any equipment slot contains the ID
		 */
		public boolean isEquipped(int itemId)
		{
			for (ItemSlot slot : slots.values())
			{
				if (slot.getItemId() == itemId)
				{
					return true;
				}
			}
			return false;
		}

		/** @return weapon slot, or {@code null} when unavailable */
		public ItemSlot getWeapon()
		{
			return getItem(EquipmentInventorySlot.WEAPON);
		}

		/** @return shield/off-hand slot, or {@code null} when unavailable */
		public ItemSlot getShield()
		{
			return getItem(EquipmentInventorySlot.SHIELD);
		}

	}
}
