package net.runelite.client.plugins.m8aq.api.storage;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.gameval.VarbitID;

/** Provides a complete read-only snapshot of the carried plank sack. */
public final class PlankSack
{
	/** Shared total plank capacity. */ public static final int CAPACITY = 28;
	private static final int[] ITEM_IDS =
	{
		ItemID.WOODPLANK,
		ItemID.PLANK_OAK,
		ItemID.PLANK_TEAK,
		ItemID.PLANK_MAHOGANY,
		ItemID.PLANK_CAMPHOR,
		ItemID.PLANK_IRONWOOD,
		ItemID.PLANK_ROSEWOOD
	};
	private static final int[] VARBITS =
	{
		VarbitID.PLANK_SACK_PLAIN,
		VarbitID.PLANK_SACK_OAK,
		VarbitID.PLANK_SACK_TEAK,
		VarbitID.PLANK_SACK_MAHOGANY,
		VarbitID.PLANK_SACK_CAMPHOR,
		VarbitID.PLANK_SACK_IRONWOOD,
		VarbitID.PLANK_SACK_ROSEWOOD
	};

	private PlankSack()
	{
	}

	/**
	 * Reads all seven server-synchronized plank quantities and every relevant item widget.
	 * Persistent values are hidden unless the sack is currently carried.
	 *
	 * @param client RuneLite client
	 * @return immutable plank-sack snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.CarriedState carried = PortableContainer.readCarried(client,
			new int[]{ItemID.PLANK_SACK}, new int[0]);
		if (!carried.isPresent())
		{
			return new State(carried, false, Collections.emptyList(), -1, -1);
		}

		List<ItemSlot> contents = new ArrayList<>(VARBITS.length);
		int total = 0;
		for (int index = 0; index < VARBITS.length; index++)
		{
			int quantity = Math.max(0, client.getVarbitValue(VARBITS[index]));
			ItemComposition definition = client.getItemDefinition(ITEM_IDS[index]);
			contents.add(new ItemSlot(index, ITEM_IDS[index],
				definition == null ? null : definition.getName(), quantity));
			total += quantity;
		}
		return new State(carried, true, Collections.unmodifiableList(contents), total,
			Math.max(0, CAPACITY - total));
	}

	/** Immutable plank-sack state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.CarriedState
	{
		/** @return whether maintained plank quantities were safe to read */ private final boolean contentsLoaded;
		/** @return immutable fixed-order plain, oak, teak, mahogany, camphor, ironwood, rosewood quantities */
		private final List<ItemSlot> contents;
		/** @return total stored planks, or -1 when unavailable */ private final int storedQuantity;
		/** @return remaining shared capacity, or -1 when unavailable */ private final int remainingCapacity;

		private State(PortableContainer.CarriedState carried, boolean contentsLoaded,
			List<ItemSlot> contents, int storedQuantity, int remainingCapacity)
		{
			super(carried);
			this.contentsLoaded = contentsLoaded;
			this.contents = contents;
			this.storedQuantity = storedQuantity;
			this.remainingCapacity = remainingCapacity;
		}

		/** @return shared capacity, or zero when the sack is absent */
		public int getCapacity()
		{
			return isPresent() ? CAPACITY : 0;
		}

		/**
		 * Returns the synchronized quantity for one exact plank item ID.
		 *
		 * @param itemId raw plank item ID
		 * @return quantity, or -1 when contents are unavailable or the ID is unsupported
		 */
		public int getQuantity(int itemId)
		{
			if (!contentsLoaded)
			{
				return -1;
			}
			for (ItemSlot item : contents)
			{
				if (item.getItemId() == itemId)
				{
					return item.getQuantity();
				}
			}
			return -1;
		}
	}
}
