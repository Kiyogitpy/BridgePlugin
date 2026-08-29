package net.runelite.client.plugins.m8aq.api.storage;

import java.util.Collections;
import java.util.List;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.plugins.m8aq.api.ui.Interfaces;

/** Shared immutable state for carried item containers with separately loaded contents. */
public final class PortableContainer
{
	private static final Pattern NUMBER = Pattern.compile("[0-9][0-9,]*");

	private PortableContainer()
	{
	}

	static State read(Client client, int closedItemId, int openItemId, int contentsId, int capacity)
	{
		Objects.requireNonNull(client, "client");
		ItemContainer inventory = client.getItemContainer(InventoryID.INV);
		boolean present = inventory != null
			&& (inventory.contains(closedItemId) || inventory.contains(openItemId));
		boolean open = inventory != null && inventory.contains(openItemId);
		ItemContainer contents = present ? client.getItemContainer(contentsId) : null;
		if (contents == null)
		{
			return new State(inventory != null, false, present, open, capacity,
				Collections.emptyList(), -1, -1, 0L, Interfaces.widgetSnapshot());
		}

		List<ItemSlot> slots = ItemSlot.copy(client, contentsId);
		int occupied = 0;
		long total = 0;
		for (ItemSlot slot : slots)
		{
			if (slot.getItemId() >= 0)
			{
				occupied++;
				total += slot.getQuantity();
			}
		}
		return new State(true, true, true, open, capacity, slots, occupied, capacity - occupied, total,
			Interfaces.widgetSnapshot());
	}

	static CarriedState readCarried(Client client, int[] closedItemIds, int[] openItemIds,
		String... checkMarkers)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		ItemContainer inventory = accountStateLoaded ? client.getItemContainer(InventoryID.INV) : null;
		ItemContainer equipment = accountStateLoaded ? client.getItemContainer(InventoryID.WORN) : null;
		boolean inventoryLoaded = ItemSlot.isLoaded(inventory);
		boolean equipmentLoaded = ItemSlot.isLoaded(equipment);
		int inventoryItemId = find(inventoryLoaded ? inventory : null, closedItemIds, openItemIds);
		int equipmentItemId = find(equipmentLoaded ? equipment : null, closedItemIds, openItemIds);
		int itemId = inventoryItemId >= 0 ? inventoryItemId : equipmentItemId;

		Widget objectBox = client.getWidget(InterfaceID.Objectbox.UNIVERSE);
		Widget objectBoxText = client.getWidget(InterfaceID.Objectbox.TEXT);
		String checkText = itemId >= 0 && Interfaces.isVisible(objectBoxText)
			? normalize(objectBoxText.getText()) : null;
		if (!containsAny(checkText, checkMarkers))
		{
			checkText = null;
			objectBox = null;
		}
		List<Interfaces.WidgetEntry> inventoryWidgets =
			Interfaces.snapshot(client.getWidget(InterfaceID.Inventory.ITEMS));
		List<Interfaces.WidgetEntry> noopsWidgets =
			Interfaces.snapshot(client.getWidget(InterfaceID.InventoryNoops.ITEMS));
		List<Interfaces.WidgetEntry> bankWidgets =
			Interfaces.snapshot(client.getWidget(InterfaceID.Bankside.ITEMS));
		List<Interfaces.WidgetEntry> depositBoxWidgets =
			Interfaces.snapshot(client.getWidget(InterfaceID.BankDepositbox.INVENTORY));
		List<Interfaces.WidgetEntry> equipmentWidgets =
			Interfaces.snapshot(client.getWidget(InterfaceID.Wornitems.UNIVERSE));
		List<Interfaces.WidgetEntry> checkWidgets = Interfaces.snapshot(objectBox);

		return new CarriedState(
			accountStateLoaded,
			inventoryLoaded,
			equipmentLoaded,
			itemId >= 0,
			equipmentItemId >= 0,
			contains(openItemIds, itemId),
			itemId,
			checkText != null,
			checkText,
			inventoryWidgets,
			noopsWidgets,
			bankWidgets,
			depositBoxWidgets,
			equipmentWidgets,
			checkWidgets,
			Interfaces.widgetSnapshot(inventoryWidgets, noopsWidgets, bankWidgets,
				depositBoxWidgets, equipmentWidgets, checkWidgets));
	}

	static int checkedQuantity(String text)
	{
		if (text == null)
		{
			return -1;
		}
		String lower = text.toLowerCase();
		if (lower.contains("empty"))
		{
			return 0;
		}
		if (lower.contains("one piece"))
		{
			return 1;
		}

		long total = 0;
		boolean found = false;
		Matcher matcher = NUMBER.matcher(text);
		while (matcher.find())
		{
			found = true;
			try
			{
				total += Integer.parseInt(matcher.group().replace(",", ""));
			}
			catch (NumberFormatException ex)
			{
				return -1;
			}
		}
		return found && total <= Integer.MAX_VALUE ? (int) total : -1;
	}

	private static int find(ItemContainer container, int[]... itemIdGroups)
	{
		if (container == null)
		{
			return -1;
		}
		for (Item item : container.getItems())
		{
			if (item != null)
			{
				for (int[] itemIds : itemIdGroups)
				{
					if (contains(itemIds, item.getId()))
					{
						return item.getId();
					}
				}
			}
		}
		return -1;
	}

	private static boolean contains(int[] values, int value)
	{
		for (int candidate : values)
		{
			if (candidate == value)
			{
				return true;
			}
		}
		return false;
	}

	private static boolean containsAny(String value, String[] markers)
	{
		if (value == null)
		{
			return false;
		}
		String lower = value.toLowerCase();
		for (String marker : markers)
		{
			if (lower.contains(marker.toLowerCase()))
			{
				return true;
			}
		}
		return false;
	}

	private static String normalize(String text)
	{
		return text == null ? null : text.replace("<br>", " ")
			.replaceAll("<[^>]+>", "").replace('\u00a0', ' ').trim();
	}

	/** Immutable state shared by carried containers with separately loaded contents. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static class State extends Interfaces.WidgetLookup
	{
		/** @return whether the backpack inventory was available */ private final boolean inventoryLoaded;
		/** @return whether the server-synchronized contents container was available */ private final boolean contentsLoaded;
		/** @return whether the container is currently in the backpack */ private final boolean present;
		/** @return whether the carried item is the open variant */ private final boolean open;
		/** @return cache-defined slot capacity */ private final int capacity;
		/** @return immutable materialized positional contents */ private final List<ItemSlot> slots;
		/** @return occupied slots, or -1 when contents are unavailable */ private final int occupiedSlotCount;
		/** @return empty slots, or -1 when contents are unavailable */ private final int emptySlotCount;
		/** @return total raw item quantity, or zero when contents are unavailable */ private final long totalQuantity;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		protected State(State state)
		{
			this(state.inventoryLoaded, state.contentsLoaded, state.present, state.open, state.capacity,
				state.slots, state.occupiedSlotCount, state.emptySlotCount, state.totalQuantity,
				state.widgetSnapshot);
		}
	}

	/** Shared immutable carried-item and widget state for scalar portable stores. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static class CarriedState extends Interfaces.WidgetLookup
	{
		/** @return whether account-scoped state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether the backpack container was available */ private final boolean inventoryLoaded;
		/** @return whether the equipment container was available */ private final boolean equipmentLoaded;
		/** @return whether a supported variant is carried or equipped */ private final boolean present;
		/** @return whether a supported wearable variant is equipped */ private final boolean equipped;
		/** @return whether the current item is an open variant */ private final boolean open;
		/** @return exact current raw item ID, or -1 when absent */ private final int itemId;
		/** @return whether matching transient Check output is visible */ private final boolean checkDialogOpen;
		/** @return normalized matching Check output, otherwise null */ private final String checkText;
		/** @return normal inventory item widgets */ private final List<Interfaces.WidgetEntry> inventoryWidgets;
		/** @return no-operations inventory item widgets */ private final List<Interfaces.WidgetEntry> noopsWidgets;
		/** @return bank-side inventory item widgets */ private final List<Interfaces.WidgetEntry> bankWidgets;
		/** @return deposit-box inventory item widgets */ private final List<Interfaces.WidgetEntry> depositBoxWidgets;
		/** @return worn-item widgets */ private final List<Interfaces.WidgetEntry> equipmentWidgets;
		/** @return matching transient Check object-box widgets */ private final List<Interfaces.WidgetEntry> checkWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		protected CarriedState(CarriedState state)
		{
			this(state.accountStateLoaded, state.inventoryLoaded, state.equipmentLoaded, state.present,
				state.equipped, state.open, state.itemId, state.checkDialogOpen, state.checkText,
				state.inventoryWidgets, state.noopsWidgets, state.bankWidgets, state.depositBoxWidgets,
				state.equipmentWidgets, state.checkWidgets, state.widgetSnapshot);
		}
	}
}
