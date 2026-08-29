package net.runelite.client.plugins.m8aq.api.storage;

import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.gameval.ItemID;

/** Provides a read-only snapshot of the carried fish barrel. */
public final class FishBarrel
{
	/** Shared fish-barrel capacity. */ public static final int CAPACITY = 28;

	private FishBarrel()
	{
	}

	/**
	 * Reads variant, location, transient Check output, and every relevant item widget.
	 * Contents are unavailable unless matching Check output is currently visible.
	 *
	 * @param client RuneLite client
	 * @return immutable fish-barrel snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.CarriedState carried = PortableContainer.readCarried(client,
			new int[]{ItemID.FISH_BARREL_CLOSED, ItemID.FISH_SACK_BARREL_CLOSED},
			new int[]{ItemID.FISH_BARREL_OPEN, ItemID.FISH_SACK_BARREL_OPEN}, "barrel");
		Type type = type(carried.getItemId());
		int quantity = PortableContainer.checkedQuantity(carried.getCheckText());
		int capacity = type == Type.UNKNOWN ? 0 : CAPACITY;
		return new State(carried, type, capacity, quantity >= 0, quantity,
			quantity >= 0 ? capacity - quantity : -1);
	}

	private static Type type(int itemId)
	{
		return itemId == ItemID.FISH_BARREL_CLOSED || itemId == ItemID.FISH_BARREL_OPEN
			? Type.BARREL
			: itemId == ItemID.FISH_SACK_BARREL_CLOSED || itemId == ItemID.FISH_SACK_BARREL_OPEN
				? Type.SACK_BARREL : Type.UNKNOWN;
	}

	/** Current carried fish-container variant. */
	public enum Type
	{
		BARREL,
		SACK_BARREL,
		UNKNOWN
	}

	/** Immutable fish-barrel state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.CarriedState
	{
		/** @return current carried variant */ private final Type type;
		/** @return current capacity, or zero when absent */ private final int capacity;
		/** @return whether transient Check output proves the quantity */ private final boolean contentsLoaded;
		/** @return checked total fish quantity, or -1 when unavailable */ private final int storedQuantity;
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
