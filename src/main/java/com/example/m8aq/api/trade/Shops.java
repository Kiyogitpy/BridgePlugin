package com.example.m8aq.api.trade;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.storage.ItemSlot;

/** Provides copied item stock from the currently loaded shop interface. */
public final class Shops
{
	private Shops()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable shop snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Widget root = client.getWidget(InterfaceID.Shopmain.UNIVERSE);
		Widget items = client.getWidget(InterfaceID.Shopmain.ITEMS);
		return new State(root != null && !root.isHidden(), copyItems(client, items));
	}

	private static List<ItemSlot> copyItems(Client client, Widget container)
	{
		if (container == null || container.getChildren() == null)
		{
			return Collections.emptyList();
		}
		List<ItemSlot> items = new ArrayList<>();
		for (Widget child : container.getChildren())
		{
			if (child != null && child.getItemId() >= 0)
			{
				ItemComposition item = client.getItemDefinition(child.getItemId());
				items.add(new ItemSlot(child.getIndex(), child.getItemId(), item == null ? null : item.getName(),
					child.getItemQuantity()));
			}
		}
		return Collections.unmodifiableList(items);
	}

	/** Immutable current-shop state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether a shop is visibly open */ private final boolean open;
		/** @return immutable currently displayed shop stock */ private final List<ItemSlot> items;
	}
}
