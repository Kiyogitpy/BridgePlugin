package com.example.m8aq.api.storage;

import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.gameval.ItemID;

/** Provides a read-only snapshot of the carried log or forestry basket. */
public final class LogBasket
{
	/** Shared log-basket capacity. */ public static final int CAPACITY = 28;

	private LogBasket()
	{
	}

	/**
	 * Reads variant, location, transient Check output, and every relevant item widget.
	 * Contents are unavailable unless matching Check output is currently visible.
	 *
	 * @param client RuneLite client
	 * @return immutable log-basket snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.CarriedState carried = PortableContainer.readCarried(client,
			new int[]{ItemID.LOG_BASKET_CLOSED, ItemID.FORESTRY_BASKET_CLOSED},
			new int[]{ItemID.LOG_BASKET_OPEN, ItemID.FORESTRY_BASKET_OPEN}, "basket");
		Type type = type(carried.getItemId());
		int quantity = PortableContainer.checkedQuantity(carried.getCheckText());
		int capacity = type == Type.UNKNOWN ? 0 : CAPACITY;
		return new State(carried, type, capacity, quantity >= 0, quantity,
			quantity >= 0 ? capacity - quantity : -1);
	}

	private static Type type(int itemId)
	{
		return itemId == ItemID.LOG_BASKET_CLOSED || itemId == ItemID.LOG_BASKET_OPEN
			? Type.LOG_BASKET
			: itemId == ItemID.FORESTRY_BASKET_CLOSED || itemId == ItemID.FORESTRY_BASKET_OPEN
				? Type.FORESTRY_BASKET : Type.UNKNOWN;
	}

	/** Current carried log-container variant. */
	public enum Type
	{
		LOG_BASKET,
		FORESTRY_BASKET,
		UNKNOWN
	}

	/** Immutable log-basket state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.CarriedState
	{
		/** @return current carried variant */ private final Type type;
		/** @return current capacity, or zero when absent */ private final int capacity;
		/** @return whether transient Check output proves the quantity */ private final boolean contentsLoaded;
		/** @return checked total log quantity, or -1 when unavailable */ private final int storedQuantity;
		/** @return remaining capacity, or -1 when unavailable */ private final int remainingCapacity;

		private State(PortableContainer.CarriedState carried, Type type, int capacity,
			boolean contentsLoaded, int storedQuantity, int remainingCapacity)
		{
			super(carried);
			this.type = type;
			this.capacity = capacity;
			this.contentsLoaded = contentsLoaded;
			this.storedQuantity = storedQuantity;
			this.remainingCapacity = remainingCapacity;
		}
	}
}
