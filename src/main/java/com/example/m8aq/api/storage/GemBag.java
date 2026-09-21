package com.example.m8aq.api.storage;

import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.gameval.ItemID;

/** Provides a read-only snapshot of the carried gem bag or gem sack. */
public final class GemBag
{
	/** Gem-bag capacity for each supported gem. */ public static final int BAG_PER_TYPE_CAPACITY = 60;
	/** Total gem-bag capacity. */ public static final int BAG_CAPACITY = 300;
	/** Gem-sack capacity for each supported gem. */ public static final int SACK_PER_TYPE_CAPACITY = 60;
	/** Total gem-sack capacity. */ public static final int SACK_CAPACITY = 480;

	private GemBag()
	{
	}

	/**
	 * Reads variant, capacity, transient Check output, and every relevant item widget.
	 * Contents are unavailable unless matching Check output is currently visible.
	 *
	 * @param client RuneLite client
	 * @return immutable gem-container snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.CarriedState carried = PortableContainer.readCarried(client,
			new int[]{ItemID.GEM_BAG, ItemID.GEM_SACK},
			new int[]{ItemID.GEM_BAG_OPEN, ItemID.GEM_SACK_OPEN}, "sapphires:", "opal:", "gem bag", "gem sack");
		Type type = type(carried.getItemId());
		int quantity = PortableContainer.checkedQuantity(carried.getCheckText());
		int capacity = type == Type.BAG ? BAG_CAPACITY : type == Type.SACK ? SACK_CAPACITY : 0;
		return new State(carried, type, type == Type.UNKNOWN ? 0 : BAG_PER_TYPE_CAPACITY,
			capacity, quantity >= 0, quantity, quantity >= 0 ? capacity - quantity : -1);
	}

	private static Type type(int itemId)
	{
		return itemId == ItemID.GEM_BAG || itemId == ItemID.GEM_BAG_OPEN ? Type.BAG
			: itemId == ItemID.GEM_SACK || itemId == ItemID.GEM_SACK_OPEN ? Type.SACK : Type.UNKNOWN;
	}

	/** Current carried gem-container variant. */
	public enum Type
	{
		BAG,
		SACK,
		UNKNOWN
	}

	/** Immutable gem-container state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.CarriedState
	{
		/** @return current carried variant */ private final Type type;
		/** @return capacity of each supported gem type, or zero when absent */ private final int perTypeCapacity;
		/** @return combined capacity across supported gem types, or zero when absent */ private final int capacity;
		/** @return whether transient Check output proves the quantity */ private final boolean contentsLoaded;
		/** @return checked total gem quantity, or -1 when unavailable */ private final int storedQuantity;
		/** @return remaining combined capacity, or -1 when unavailable */ private final int remainingCapacity;

		private State(PortableContainer.CarriedState carried, Type type, int perTypeCapacity,
			int capacity, boolean contentsLoaded, int storedQuantity, int remainingCapacity)
		{
			super(carried);
			this.type = type;
			this.perTypeCapacity = perTypeCapacity;
			this.capacity = capacity;
			this.contentsLoaded = contentsLoaded;
			this.storedQuantity = storedQuantity;
			this.remainingCapacity = remainingCapacity;
		}
	}
}
