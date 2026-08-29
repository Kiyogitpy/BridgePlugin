package net.runelite.client.plugins.m8aq.api.storage;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.Item;
import net.runelite.api.ItemComposition;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.plugins.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of loaded bank contents, configuration, and interfaces. */
public final class Bank
{
	private static final int POTION_STORAGE_TAB = 15;
	private static final int[] TAB_COUNTS =
	{
		VarbitID.BANK_TAB_1,
		VarbitID.BANK_TAB_2,
		VarbitID.BANK_TAB_3,
		VarbitID.BANK_TAB_4,
		VarbitID.BANK_TAB_5,
		VarbitID.BANK_TAB_6,
		VarbitID.BANK_TAB_7,
		VarbitID.BANK_TAB_8,
		VarbitID.BANK_TAB_9
	};

	private Bank()
	{
	}

	/**
	 * Reads the current bank state from the client.
	 * Account-persistent values and cached contents are suppressed while logged out.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable bank snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		InteractionState interaction = readInteractionState(client, accountStateLoaded);
		Widget root = client.getWidget(InterfaceID.Bankmain.UNIVERSE);
		Widget bankSide = client.getWidget(InterfaceID.Bankside.UNIVERSE);
		boolean open = Interfaces.isVisible(root);
		ItemContainer container = accountStateLoaded ? client.getItemContainer(InventoryID.BANK) : null;
		Item[] items = container == null ? null : container.getItems();
		boolean containerLoaded = items != null;
		int[] tabCounts = containerLoaded ? readTabCounts(client) : new int[TAB_COUNTS.length];
		int numberedItemCount = 0;
		for (int count : tabCounts)
		{
			numberedItemCount += count;
		}
		boolean validTabs = containerLoaded && numberedItemCount <= items.length;

		List<BankSlot> mutableSlots = new ArrayList<>();
		int occupied = containerLoaded ? 0 : -1;
		int placeholders = containerLoaded ? 0 : -1;
		long totalQuantity = 0;
		if (containerLoaded)
		{
			for (int slot = 0; slot < items.length; slot++)
			{
				Item item = items[slot];
				int itemId = item == null ? -1 : item.getId();
				int quantity = item == null ? 0 : item.getQuantity();
				ItemComposition definition = itemId < 0 ? null : client.getItemDefinition(itemId);
				boolean placeholder = definition != null && definition.getPlaceholderTemplateId() >= 0;
				if (itemId >= 0)
				{
					occupied++;
					placeholders += placeholder ? 1 : 0;
					totalQuantity += quantity;
				}

				int tabNumber = validTabs ? 0 : -1;
				int positionInTab = validTabs ? slot - numberedItemCount : -1;
				int tabStart = 0;
				for (int tab = 0; validTabs && tab < tabCounts.length; tab++)
				{
					int tabEnd = tabStart + tabCounts[tab];
					if (slot >= tabStart && slot < tabEnd)
					{
						tabNumber = tab + 1;
						positionInTab = slot - tabStart;
						break;
					}
					tabStart = tabEnd;
				}
				mutableSlots.add(new BankSlot(itemId, definition == null ? null : definition.getName(),
					quantity, slot, tabNumber, positionInTab, placeholder));
			}
		}

		List<BankSlot> slots = Collections.unmodifiableList(mutableSlots);
		int selectedTab = open && accountStateLoaded ? client.getVarbitValue(VarbitID.BANK_CURRENTTAB) : -1;
		String searchQuery = accountStateLoaded && open ? Interfaces.getSearchQuery(client) : null;
		boolean searchActive = searchQuery != null;
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(root);
		List<Interfaces.WidgetEntry> bankSideWidgets = Interfaces.snapshot(bankSide);

		return new State(
			open,
			Interfaces.isVisible(bankSide),
			accountStateLoaded,
			containerLoaded,
			slots,
			open ? Interfaces.getLastInteger(client.getWidget(InterfaceID.Bankmain.CAPACITY)) : -1,
			occupied,
			placeholders,
			totalQuantity,
			selectedTab,
			View.fromRaw(selectedTab),
			validTabs ? readTabs(tabCounts) : Collections.emptyList(),
			interaction.getWithdrawalMode(),
			interaction.getCustomQuantity(),
			interaction.getWithdrawForm(),
			interaction.getRearrangeMode(),
			interaction.isPlaceholdersEnabled(),
			interaction.isAlwaysDepositPotionsEnabled(),
			searchActive,
			searchQuery,
			widgets,
			bankSideWidgets,
			Interfaces.widgetSnapshot(widgets, bankSideWidgets));
	}

	static InteractionState readInteractionState(Client client, boolean accountStateLoaded)
	{
		return new InteractionState(
			accountStateLoaded
				? WithdrawalMode.fromRaw(client.getVarbitValue(VarbitID.BANK_QUANTITY_TYPE))
				: WithdrawalMode.UNKNOWN,
			accountStateLoaded ? client.getVarbitValue(VarbitID.BANK_REQUESTEDQUANTITY) : 0,
			accountStateLoaded
				? WithdrawForm.fromRaw(client.getVarbitValue(VarbitID.BANK_WITHDRAWNOTES))
				: WithdrawForm.UNKNOWN,
			accountStateLoaded
				? RearrangeMode.fromRaw(client.getVarbitValue(VarbitID.BANK_INSERTMODE))
				: RearrangeMode.UNKNOWN,
			accountStateLoaded && client.getVarbitValue(VarbitID.BANK_LEAVEPLACEHOLDERS) == 1,
			accountStateLoaded && client.getVarbitValue(VarbitID.BANK_DEPOSITPOTION) == 1);
	}

	private static int[] readTabCounts(Client client)
	{
		int[] counts = new int[TAB_COUNTS.length];
		for (int index = 0; index < TAB_COUNTS.length; index++)
		{
			counts[index] = Math.max(0, client.getVarbitValue(TAB_COUNTS[index]));
		}
		return counts;
	}

	private static List<BankTab> readTabs(int[] counts)
	{
		int startSlot = 0;
		List<BankTab> tabs = new ArrayList<>();
		for (int index = 0; index < counts.length; index++)
		{
			if (counts[index] > 0)
			{
				tabs.add(new BankTab(index + 1, startSlot, counts[index]));
				startSlot += counts[index];
			}
		}
		return Collections.unmodifiableList(tabs);
	}

	/** Selected default bank withdrawal quantity. */
	public enum WithdrawalMode
	{
		ONE,
		FIVE,
		TEN,
		CUSTOM,
		ALL,
		UNKNOWN;

		static WithdrawalMode fromRaw(int value)
		{
			switch (value)
			{
				case 0:
					return ONE;
				case 1:
					return FIVE;
				case 2:
					return TEN;
				case 3:
					return CUSTOM;
				case 4:
					return ALL;
				default:
					return UNKNOWN;
			}
		}
	}

	/** Bank withdrawal item form. */
	public enum WithdrawForm
	{
		ITEMS,
		NOTES,
		UNKNOWN;

		static WithdrawForm fromRaw(int value)
		{
			return value == 0 ? ITEMS : value == 1 ? NOTES : UNKNOWN;
		}
	}

	/** Bank item rearrangement mode. */
	public enum RearrangeMode
	{
		SWAP,
		INSERT,
		UNKNOWN;

		static RearrangeMode fromRaw(int value)
		{
			return value == 0 ? SWAP : value == 1 ? INSERT : UNKNOWN;
		}
	}

	/** Semantic interpretation of the raw selected bank tab. */
	public enum View
	{
		ALL,
		TAB,
		POTION_STORAGE,
		SPECIAL,
		UNAVAILABLE;

		private static View fromRaw(int value)
		{
			return value < 0 ? UNAVAILABLE
				: value == 0 ? ALL
					: value >= 1 && value <= 9 ? TAB
						: value == POTION_STORAGE_TAB ? POTION_STORAGE : SPECIAL;
		}
	}

	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	static final class InteractionState
	{
		private final WithdrawalMode withdrawalMode;
		private final int customQuantity;
		private final WithdrawForm withdrawForm;
		private final RearrangeMode rearrangeMode;
		private final boolean placeholdersEnabled;
		private final boolean alwaysDepositPotionsEnabled;
	}

	/** Immutable item entry at one absolute bank-container slot. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class BankSlot
	{
		/** @return exact raw item ID */ private final int itemId;
		/** @return canonical item name, or null for an empty/unresolved ID */ private final String itemName;
		/** @return raw item quantity */ private final int quantity;
		/** @return zero-based absolute bank-container slot */ private final int slot;
		/** @return normal bank tab number: 0 for main, 1-9 numbered, or -1 when unknown */
		private final int tabNumber;
		/** @return zero-based position within the normal bank tab, or -1 when unknown */
		private final int positionInTab;
		/** @return whether the raw item is a placeholder variant */ private final boolean placeholder;
	}

	/** Immutable visible bank-tab boundary. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class BankTab
	{
		/** @return one-based bank-tab number */ private final int number;
		/** @return zero-based starting slot in bank-container order */ private final int startSlot;
		/** @return number of entries in the tab */ private final int itemCount;
	}

	/** Immutable bank state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether the main bank interface was visibly open */ private final boolean open;
		/** @return whether the bank-side inventory interface was visibly open */ private final boolean bankSideOpen;
		/** @return whether account-persistent state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether the server-synchronized bank container was available */ private final boolean containerLoaded;
		/** @return immutable positional bank entries */ private final List<BankSlot> slots;
		/** @return displayed bank capacity, or -1 when unavailable */ private final int capacity;
		/** @return occupied raw slots, or -1 while the container is unavailable */ private final int occupiedSlotCount;
		/** @return placeholder slots, or -1 while the container is unavailable */ private final int placeholderSlotCount;
		/** @return total raw quantity across loaded slots */ private final long totalQuantity;
		/** @return selected bank view, or -1 when the bank is closed/unavailable */ private final int selectedTab;
		/** @return semantic selected bank view */ private final View view;
		/** @return immutable visible bank-tab boundaries in order */ private final List<BankTab> tabs;
		/** @return selected default withdrawal quantity */ private final WithdrawalMode withdrawalMode;
		/** @return configured X quantity */ private final int customQuantity;
		/** @return selected item-or-note withdrawal form */ private final WithdrawForm withdrawForm;
		/** @return selected swap-or-insert rearrangement mode */ private final RearrangeMode rearrangeMode;
		/** @return whether ordinary withdrawals leave placeholders */ private final boolean placeholdersEnabled;
		/** @return whether applicable deposits route to potion storage */ private final boolean alwaysDepositPotionsEnabled;
		/** @return whether the visible bank is currently searching */ private final boolean searchActive;
		/** @return raw shared search query while searching, otherwise null */ private final String searchQuery;
		/** @return every loaded fixed and dynamic widget under the main bank interface */
		private final List<Interfaces.WidgetEntry> widgets;
		/** @return every loaded fixed and dynamic widget under the bank-side interface */
		private final List<Interfaces.WidgetEntry> bankSideWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		/**
		 * Returns a positional bank entry.
		 *
		 * @param slot zero-based slot index
		 * @return immutable slot, or null when out of bounds
		 */
		public BankSlot getItem(int slot)
		{
			return slot >= 0 && slot < slots.size() ? slots.get(slot) : null;
		}

		/**
		 * Counts an exact raw item ID in loaded bank state.
		 *
		 * @param itemId raw item ID
		 * @return total quantity
		 */
		public int count(int itemId)
		{
			int total = 0;
			for (BankSlot slot : slots)
			{
				if (slot.getItemId() == itemId)
				{
					total += slot.getQuantity();
				}
			}
			return total;
		}

	}
}
