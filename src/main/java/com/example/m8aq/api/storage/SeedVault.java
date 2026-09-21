package com.example.m8aq.api.storage;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of Farming Guild seed-vault contents, configuration, and interfaces. */
public final class SeedVault
{
	/** Cache-defined number of positional seed-vault slots. */
	public static final int CAPACITY = 104;

	private static final int EMPTY_FAVOURITE = 255;
	private static final int[] FAVOURITE_VARBITS =
	{
		VarbitID.SEED_VAULT_FAVE1,
		VarbitID.SEED_VAULT_FAVE2,
		VarbitID.SEED_VAULT_FAVE3,
		VarbitID.SEED_VAULT_FAVE4,
		VarbitID.SEED_VAULT_FAVE5,
		VarbitID.SEED_VAULT_FAVE6,
		VarbitID.SEED_VAULT_FAVE7,
		VarbitID.SEED_VAULT_FAVE8
	};

	private SeedVault()
	{
	}

	/**
	 * Reads the current seed-vault state from the client.
	 * Account-persistent values are suppressed while logged out.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable seed-vault snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		ItemContainer container = accountStateLoaded ? client.getItemContainer(InventoryID.SEED_VAULT) : null;
		boolean contentsLoaded = container != null && container.getItems() != null;
		List<ItemSlot> slots = contentsLoaded
			? ItemSlot.copy(client, InventoryID.SEED_VAULT) : Collections.emptyList();

		int occupied = contentsLoaded ? 0 : -1;
		int placeholders = contentsLoaded ? 0 : -1;
		long totalQuantity = 0;
		for (ItemSlot slot : slots)
		{
			if (slot.getItemId() >= 0)
			{
				occupied++;
				totalQuantity += slot.getQuantity();
				if (slot.isPlaceholder())
				{
					placeholders++;
				}
			}
		}

		Widget root = client.getWidget(InterfaceID.SeedVault.UNIVERSE);
		Widget deposit = client.getWidget(InterfaceID.SeedVaultDeposit.UNIVERSE);
		boolean open = Interfaces.isVisible(root);
		String searchQuery = accountStateLoaded && open ? Interfaces.getSearchQuery(client) : null;
		boolean searchActive = searchQuery != null;

		int selectedQuantity = accountStateLoaded
			? client.getVarpValue(VarPlayerID.SEED_VAULT_SELECTED_QTY) : 0;
		int rawCategory = accountStateLoaded ? client.getVarbitValue(VarbitID.SEED_VAULT_CATEGORY) : -1;
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(root);
		List<Interfaces.WidgetEntry> depositWidgets = Interfaces.snapshot(deposit);
		return new State(
			open,
			Interfaces.isVisible(deposit),
			accountStateLoaded,
			contentsLoaded,
			CAPACITY,
			slots,
			occupied,
			placeholders,
			totalQuantity,
			accountStateLoaded ? WithdrawalMode.fromRaw(selectedQuantity) : WithdrawalMode.UNKNOWN,
			selectedQuantity,
			accountStateLoaded ? client.getVarpValue(VarPlayerID.SEED_VAULT_REQUESTED_QTY) : 0,
			rawCategory,
			accountStateLoaded ? Category.fromRaw(rawCategory) : Category.UNKNOWN,
			accountStateLoaded ? readFavourites(client, slots) : Collections.emptyList(),
			searchActive,
			searchQuery,
			widgets,
			depositWidgets,
			Interfaces.widgetSnapshot(widgets, depositWidgets));
	}

	private static List<Favourite> readFavourites(Client client, List<ItemSlot> slots)
	{
		List<Favourite> favourites = new ArrayList<>(FAVOURITE_VARBITS.length);
		for (int index = 0; index < FAVOURITE_VARBITS.length; index++)
		{
			int vaultSlot = client.getVarbitValue(FAVOURITE_VARBITS[index]);
			ItemSlot seed = vaultSlot == EMPTY_FAVOURITE || vaultSlot < 0 || vaultSlot >= slots.size()
				? null : slots.get(vaultSlot);
			favourites.add(new Favourite(index, vaultSlot,
				seed == null ? -1 : seed.getItemId(), seed == null ? null : seed.getItemName()));
		}
		return Collections.unmodifiableList(favourites);
	}

	/** Selected seed-vault withdrawal amount. */
	public enum WithdrawalMode
	{
		ONE,
		FIVE,
		TEN,
		CUSTOM,
		ALL,
		UNKNOWN;

		private static WithdrawalMode fromRaw(int value)
		{
			switch (value)
			{
				case 1:
					return ONE;
				case 5:
					return FIVE;
				case 10:
					return TEN;
				case Integer.MAX_VALUE:
					return ALL;
				default:
					return value >= 0 ? CUSTOM : UNKNOWN;
			}
		}
	}

	/** Selected seed-vault category. */
	@Getter
	@RequiredArgsConstructor
	public enum Category
	{
		FAVOURITE_SLOTS(0),
		ALLOTMENT(1),
		FLOWERS(2),
		HERBS(3),
		HOPS(4),
		BUSHES(5),
		TREES(6),
		FRUIT_TREES(7),
		EXOTIC(8),
		ANIMA(9),
		SAPLINGS(10),
		CORAL(11),
		FARMING_CONTRACT(12),
		UNKNOWN(-1);

		/** @return raw category value */ private final int value;

		private static Category fromRaw(int value)
		{
			for (Category category : values())
			{
				if (category.value == value)
				{
					return category;
				}
			}
			return UNKNOWN;
		}
	}

	/** Immutable configured favourite seed slot. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Favourite
	{
		/** @return zero-based favourite position */ private final int index;
		/** @return raw seed-vault slot, with 255 meaning empty */ private final int vaultSlot;
		/** @return resolved raw seed item ID, or -1 */ private final int itemId;
		/** @return resolved canonical seed name, or null */ private final String itemName;
	}

	/** Immutable seed-vault state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether the main seed-vault interface is visibly open */ private final boolean open;
		/** @return whether the seed-vault inventory side panel is visibly open */ private final boolean depositPanelOpen;
		/** @return whether account-persistent state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether inventory 626 was available */ private final boolean contentsLoaded;
		/** @return cache-defined positional capacity */ private final int capacity;
		/** @return immutable positional seed-vault slots */ private final List<ItemSlot> slots;
		/** @return occupied slots, or -1 while contents are unavailable */ private final int occupiedSlotCount;
		/** @return placeholder slots, or -1 while contents are unavailable */ private final int placeholderSlotCount;
		/** @return total raw quantity across loaded slots */ private final long totalQuantity;
		/** @return selected withdrawal mode */ private final WithdrawalMode withdrawalMode;
		/** @return raw selected withdrawal quantity */ private final int selectedQuantity;
		/** @return configured X quantity */ private final int customQuantity;
		/** @return raw category value, or -1 while account state is unavailable */ private final int rawCategory;
		/** @return selected semantic category */ private final Category category;
		/** @return immutable eight-entry favourites, or empty while account state is unavailable */
		private final List<Favourite> favourites;
		/** @return whether the visible vault is currently searching */ private final boolean searchActive;
		/** @return raw shared search query while searching, otherwise null */ private final String searchQuery;
		/** @return every loaded fixed and dynamic widget under interface 631 */
		private final List<Interfaces.WidgetEntry> widgets;
		/** @return every loaded fixed and dynamic widget under side interface 630 */
		private final List<Interfaces.WidgetEntry> depositWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

	}
}
