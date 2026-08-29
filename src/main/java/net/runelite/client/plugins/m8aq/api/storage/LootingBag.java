package net.runelite.client.plugins.m8aq.api.storage;

import java.util.List;
import lombok.AccessLevel;
import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.plugins.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of a carried looting bag and its currently loaded contents. */
public final class LootingBag
{
	/** Number of item stacks the looting bag can hold. */
	public static final int CAPACITY = 28;

	private LootingBag()
	{
	}

	/**
	 * Reads current looting-bag state from the client.
	 * Bag contents are available only while the game has loaded inventory 516.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable looting-bag snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.State container = PortableContainer.read(
			client, ItemID.LOOTING_BAG, ItemID.LOOTING_BAG_OPEN, InventoryID.LOOTING_BAG, CAPACITY);
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		Widget bag = client.getWidget(InterfaceID.WildernessLootingbag.UNIVERSE);
		Widget bank = client.getWidget(InterfaceID.Bankside.LOOTINGBAG_CONTAINER);
		List<Interfaces.WidgetEntry> bagWidgets = Interfaces.snapshot(bag);
		List<Interfaces.WidgetEntry> bankWidgets = Interfaces.snapshot(bank);
		return new State(
			container,
			accountStateLoaded,
			accountStateLoaded && client.getVarbitValue(VarbitID.LOOTINGBAG_USEALLITEMS) == 1,
			accountStateLoaded && client.getVarbitValue(VarbitID.LOOTINGBAG_IGNORE_FOOD) == 1,
			Interfaces.isVisible(bag),
			Interfaces.isVisible(bank),
			bagWidgets,
			bankWidgets,
			Interfaces.widgetSnapshot(bagWidgets, bankWidgets));
	}

	/** Immutable looting-bag state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.State
	{
		/** @return whether account-persistent settings were safe to read */
		private final boolean accountStateLoaded;
		/** @return whether using an item stores as many as possible */
		private final boolean storeAllEnabled;
		/** @return whether automatic storage excludes food and potions */
		private final boolean ignoreSuppliesEnabled;
		/** @return whether the standalone looting-bag interface is visibly open */
		private final boolean bagInterfaceOpen;
		/** @return whether the bank-side looting-bag panel is visibly open */
		private final boolean bankInterfaceOpen;
		/** @return every loaded fixed and dynamic widget under the standalone interface */
		private final List<Interfaces.WidgetEntry> bagWidgets;
		/** @return every loaded fixed and dynamic widget under the bank-side panel */
		private final List<Interfaces.WidgetEntry> bankWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		private State(
			PortableContainer.State container,
			boolean accountStateLoaded,
			boolean storeAllEnabled,
			boolean ignoreSuppliesEnabled,
			boolean bagInterfaceOpen,
			boolean bankInterfaceOpen,
			List<Interfaces.WidgetEntry> bagWidgets,
			List<Interfaces.WidgetEntry> bankWidgets,
			Interfaces.WidgetSnapshot widgetSnapshot)
		{
			super(container);
			this.accountStateLoaded = accountStateLoaded;
			this.storeAllEnabled = storeAllEnabled;
			this.ignoreSuppliesEnabled = ignoreSuppliesEnabled;
			this.bagInterfaceOpen = bagInterfaceOpen;
			this.bankInterfaceOpen = bankInterfaceOpen;
			this.bagWidgets = bagWidgets;
			this.bankWidgets = bankWidgets;
			this.widgetSnapshot = widgetSnapshot;
		}
	}
}
