package net.runelite.client.plugins.m8aq.api.trade;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GrandExchangeOffer;
import net.runelite.api.GrandExchangeOfferState;
import net.runelite.api.ItemComposition;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;

/** Provides copied state for the player's Grand Exchange offers. */
public final class GrandExchange
{
	private GrandExchange()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable Grand Exchange snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Widget root = client.getWidget(InterfaceID.GeOffers.UNIVERSE);
		List<Offer> entries = new ArrayList<>();
		GrandExchangeOffer[] offers = client.getGrandExchangeOffers();
		if (offers != null)
		{
			for (int slot = 0; slot < offers.length; slot++)
			{
				GrandExchangeOffer offer = offers[slot];
				if (offer != null)
				{
					ItemComposition item = offer.getItemId() < 0 ? null : client.getItemDefinition(offer.getItemId());
					entries.add(new Offer(slot, offer.getItemId(), item == null ? null : item.getName(), offer.getState(),
						offer.getPrice(), offer.getTotalQuantity(), offer.getQuantitySold(), offer.getSpent()));
				}
			}
		}
		return new State(root != null && !root.isHidden(), Collections.unmodifiableList(entries));
	}

	/** Immutable Grand Exchange offer. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Offer
	{
		/** @return zero-based offer slot */ private final int slot;
		/** @return raw item ID */ private final int itemId;
		/** @return canonical item name, or null */ private final String itemName;
		/** @return offer state */ private final GrandExchangeOfferState state;
		/** @return price per item */ private final int price;
		/** @return requested quantity */ private final int totalQuantity;
		/** @return completed quantity */ private final int quantitySold;
		/** @return coins spent or received so far */ private final int spent;
	}

	/** Immutable Grand Exchange state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the offers interface is visibly open */ private final boolean open;
		/** @return immutable offer slots */ private final List<Offer> offers;
	}
}
