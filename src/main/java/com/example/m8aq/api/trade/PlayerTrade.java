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

/** Provides copied offers from the active player-to-player trade interface. */
public final class PlayerTrade
{
	private PlayerTrade()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable player-trade snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Widget main = visible(client.getWidget(InterfaceID.Trademain.UNIVERSE));
		Widget confirmation = visible(client.getWidget(InterfaceID.Tradeconfirm.UNIVERSE));
		boolean confirming = confirmation != null;
		return new State(main != null || confirming, confirming,
			text(client, confirming ? InterfaceID.Tradeconfirm.TRADEOPPONENT : InterfaceID.Trademain.TITLE),
			text(client, confirming ? InterfaceID.Tradeconfirm.REMOVAL_WARNING : InterfaceID.Trademain.STATUS),
			copyItems(client, client.getWidget(confirming ? InterfaceID.Tradeconfirm.YOUR_OFFER : InterfaceID.Trademain.YOUR_OFFER)),
			copyItems(client, client.getWidget(confirming ? InterfaceID.Tradeconfirm.OTHER_OFFER : InterfaceID.Trademain.OTHER_OFFER)));
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

	private static String text(Client client, int id)
	{
		Widget widget = client.getWidget(id);
		return widget == null ? null : widget.getText();
	}

	private static Widget visible(Widget widget)
	{
		return widget == null || widget.isHidden() ? null : widget;
	}

	/** Immutable player-trade state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether either trade stage is visibly open */ private final boolean open;
		/** @return whether the confirmation stage is open */ private final boolean confirming;
		/** @return raw trade title or opponent text */ private final String opponent;
		/** @return raw trade status or warning text */ private final String status;
		/** @return immutable local offer */ private final List<ItemSlot> ownItems;
		/** @return immutable other-player offer */ private final List<ItemSlot> otherItems;
	}
}
