package com.example.m8aq.api.storage;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.Item;
import net.runelite.api.ItemComposition;
import net.runelite.api.ItemContainer;

/** Immutable item data copied from one positional game slot. */
@Getter
public final class ItemSlot
{
	static List<ItemSlot> copy(Client client, int inventoryId)
	{
		return copy(client, client.getItemContainer(inventoryId));
	}

	static boolean isLoaded(ItemContainer container)
	{
		return container != null && container.getItems() != null;
	}

	static List<ItemSlot> copy(Client client, ItemContainer container)
	{
		if (!isLoaded(container))
		{
			return Collections.emptyList();
		}
		List<ItemSlot> slots = new ArrayList<>();
		Item[] items = container.getItems();
		for (int slot = 0; slot < items.length; slot++)
		{
			Item item = items[slot];
			int itemId = item == null ? -1 : item.getId();
			ItemComposition definition = itemId < 0 ? null : client.getItemDefinition(itemId);
			slots.add(new ItemSlot(slot, itemId, definition == null ? null : definition.getName(),
				item == null ? 0 : item.getQuantity(),
				definition != null && definition.getPlaceholderTemplateId() >= 0));
		}
		return Collections.unmodifiableList(slots);
	}

	static int occupiedSlotCount(List<ItemSlot> slots)
	{
		int count = 0;
		for (ItemSlot slot : slots)
		{
			count += slot.itemId >= 0 ? 1 : 0;
		}
		return count;
	}

	static int placeholderSlotCount(List<ItemSlot> slots)
	{
		int count = 0;
		for (ItemSlot slot : slots)
		{
			count += slot.placeholder ? 1 : 0;
		}
		return count;
	}

	static long totalQuantity(List<ItemSlot> slots)
	{
		long total = 0;
		for (ItemSlot slot : slots)
		{
			if (slot.itemId >= 0)
			{
				total += slot.quantity;
			}
		}
		return total;
	}

	/** @return zero-based source slot */
	private final int slot;
	/** @return raw item ID, or {@code -1} for an empty slot */
	private final int itemId;
	/** @return canonical item name, or {@code null} for an empty/unresolved ID */
	private final String itemName;
	/** @return raw item quantity */
	private final int quantity;
	/** @return whether the raw item is a placeholder variant */
	private final boolean placeholder;

	/**
	 * Creates an immutable slot value.
	 *
	 * @param slot zero-based source slot
	 * @param itemId raw item ID
	 * @param itemName canonical item name, or {@code null}
	 * @param quantity raw item quantity
	 */
	public ItemSlot(int slot, int itemId, String itemName, int quantity)
	{
		this(slot, itemId, itemName, quantity, false);
	}

	/**
	 * Creates an immutable slot value with placeholder state.
	 *
	 * @param slot zero-based source slot
	 * @param itemId raw item ID
	 * @param itemName canonical item name, or {@code null}
	 * @param quantity raw item quantity
	 * @param placeholder whether the raw item is a placeholder variant
	 */
	public ItemSlot(int slot, int itemId, String itemName, int quantity, boolean placeholder)
	{
		this.slot = slot;
		this.itemId = itemId;
		this.itemName = itemName;
		this.quantity = quantity;
		this.placeholder = placeholder;
	}
}
