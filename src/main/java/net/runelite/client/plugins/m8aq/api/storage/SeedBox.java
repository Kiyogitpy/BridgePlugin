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

/** Provides a read-only snapshot of a carried seed box and its currently loaded contents. */
public final class SeedBox
{
	/** Number of distinct seed types the seed box can hold. */
	public static final int CAPACITY = 6;

	private SeedBox()
	{
	}

	/**
	 * Reads current seed-box state from the client.
	 * Box contents are available only while the game has loaded inventory 573.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable seed-box snapshot
	 */
	public static State getState(Client client)
	{
		PortableContainer.State container = PortableContainer.read(
			client, ItemID.SEED_BOX, ItemID.SEED_BOX_OPEN, InventoryID.SEED_BOX, CAPACITY);
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		Widget box = client.getWidget(InterfaceID.HosidiusSeedbox.UNIVERSE);
		List<Interfaces.WidgetEntry> boxWidgets = Interfaces.snapshot(box);
		return new State(
			container,
			accountStateLoaded,
			accountStateLoaded && client.getVarbitValue(VarbitID.EMPTYONDEATH_SEEDBOX) == 1,
			Interfaces.isVisible(box),
			boxWidgets,
			Interfaces.widgetSnapshot(boxWidgets));
	}

	/** Immutable seed-box state captured from one client read. */
	@Getter
	public static final class State extends PortableContainer.State
	{
		/** @return whether account-persistent settings were safe to read */
		private final boolean accountStateLoaded;
		/** @return whether the account setting empties the seed box on death */
		private final boolean emptyOnDeathEnabled;
		/** @return whether the seed-box interface is visibly open */
		private final boolean boxInterfaceOpen;
		/** @return every loaded fixed and dynamic widget under the seed-box interface */
		private final List<Interfaces.WidgetEntry> boxWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		private State(
			PortableContainer.State container,
			boolean accountStateLoaded,
			boolean emptyOnDeathEnabled,
			boolean boxInterfaceOpen,
			List<Interfaces.WidgetEntry> boxWidgets,
			Interfaces.WidgetSnapshot widgetSnapshot)
		{
			super(container);
			this.accountStateLoaded = accountStateLoaded;
			this.emptyOnDeathEnabled = emptyOnDeathEnabled;
			this.boxInterfaceOpen = boxInterfaceOpen;
			this.boxWidgets = boxWidgets;
			this.widgetSnapshot = widgetSnapshot;
		}
	}
}
