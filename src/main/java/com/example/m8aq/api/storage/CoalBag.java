package com.example.m8aq.api.storage;

import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.ItemID;

/** Provides a read-only snapshot of the carried coal bag. */
public final class CoalBag
{
	/** Normal coal-bag capacity. */ public static final int CAPACITY = 27;
	/** Capacity while a Smithing or max cape is equipped. */ public static final int CAPE_CAPACITY = 36;

	private CoalBag()
	{
	}

	/**
	 * Reads item, capacity, transient Check output, and every relevant item widget.
	 * Contents are unavailable unless matching Check output is currently visible.
	 *
	 * @param client RuneLite client
	 * @return immutable coal-bag snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.CarriedState carried = PortableContainer.readCarried(client,
			new int[]{ItemID.COAL_BAG}, new int[]{ItemID.COAL_BAG_OPEN}, "coal bag");
		int capacity = carried.isPresent() ? (hasCapacityCape(client) ? CAPE_CAPACITY : CAPACITY) : 0;
		int quantity = PortableContainer.checkedQuantity(carried.getCheckText());
		return new State(carried, capacity, quantity >= 0, quantity,
			quantity >= 0 ? capacity - quantity : -1);
	}

	private static boolean hasCapacityCape(Client client)
	{
		ItemContainer equipment = client.getItemContainer(InventoryID.WORN);
		return equipment != null && (equipment.contains(ItemID.SKILLCAPE_SMITHING)
			|| equipment.contains(ItemID.SKILLCAPE_SMITHING_TRIMMED)
			|| equipment.contains(ItemID.SKILLCAPE_MAX)
			|| equipment.contains(ItemID.SKILLCAPE_MAX_WORN));
	}

	/** Immutable coal-bag state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.CarriedState
	{
		/** @return current capacity, or zero when absent */ private final int capacity;
		/** @return whether transient Check output proves the quantity */ private final boolean contentsLoaded;
		/** @return checked coal quantity, or -1 when unavailable */ private final int storedQuantity;
		/** @return remaining capacity, or -1 when unavailable */ private final int remainingCapacity;

		private State(PortableContainer.CarriedState carried, int capacity, boolean contentsLoaded,
			int storedQuantity, int remainingCapacity)
		{
			super(carried);
			this.capacity = capacity;
			this.contentsLoaded = contentsLoaded;
			this.storedQuantity = storedQuantity;
			this.remainingCapacity = remainingCapacity;
		}
	}
}
