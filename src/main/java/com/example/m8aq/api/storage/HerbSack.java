package com.example.m8aq.api.storage;

import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.gameval.ItemID;

/** Provides a read-only snapshot of the carried herb sack. */
public final class HerbSack
{
	/** Number of supported grimy herb types. */ public static final int HERB_TYPES = 15;
	/** Standard capacity for each herb type. */ public static final int STANDARD_PER_TYPE_CAPACITY = 30;
	/** Silklined capacity for each herb type. */ public static final int SILKLINED_PER_TYPE_CAPACITY = 100;

	private HerbSack()
	{
	}

	/**
	 * Reads variant, capacity, transient Check output, and every relevant item widget.
	 * Contents are unavailable unless matching Check output is currently visible.
	 *
	 * @param client RuneLite client
	 * @return immutable herb-sack snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.CarriedState carried = PortableContainer.readCarried(client,
			new int[]{ItemID.SLAYER_HERB_SACK, ItemID.SLAYER_HERB_SACK_SILK},
			new int[]{ItemID.SLAYER_HERB_SACK_OPEN, ItemID.SLAYER_HERB_SACK_SILK_OPEN},
			"herb sack", "grimy ");
		Type type = type(carried.getItemId());
		int perType = type == Type.STANDARD ? STANDARD_PER_TYPE_CAPACITY
			: type == Type.SILKLINED ? SILKLINED_PER_TYPE_CAPACITY : 0;
		int capacity = perType * HERB_TYPES;
		int quantity = PortableContainer.checkedQuantity(carried.getCheckText());
		return new State(carried, type, perType, capacity, quantity >= 0, quantity,
			quantity >= 0 ? capacity - quantity : -1);
	}

	private static Type type(int itemId)
	{
		return itemId == ItemID.SLAYER_HERB_SACK || itemId == ItemID.SLAYER_HERB_SACK_OPEN
			? Type.STANDARD
			: itemId == ItemID.SLAYER_HERB_SACK_SILK || itemId == ItemID.SLAYER_HERB_SACK_SILK_OPEN
				? Type.SILKLINED : Type.UNKNOWN;
	}

	/** Current carried herb-sack variant. */
	public enum Type
	{
		STANDARD,
		SILKLINED,
		UNKNOWN
	}

	/** Immutable herb-sack state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.CarriedState
	{
		/** @return current carried variant */ private final Type type;
		/** @return capacity for each supported herb type, or zero when absent */ private final int perTypeCapacity;
		/** @return combined capacity across supported herb types, or zero when absent */ private final int capacity;
		/** @return whether transient Check output proves the quantity */ private final boolean contentsLoaded;
		/** @return checked total herb quantity, or -1 when unavailable */ private final int storedQuantity;
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
