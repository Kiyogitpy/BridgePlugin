package com.example.m8aq.api.storage;

import java.util.Collections;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.ToIntFunction;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.ItemContainer;
import net.runelite.api.Skill;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of essence-pouch contents, degradation, and repair state. */
public final class EssencePouches
{
	private static final int CORDELIA_REPAIR_PEARL_COST = 1;

	private EssencePouches()
	{
	}

	/**
	 * Reads the current essence-pouch state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		ItemContainer inventory = accountStateLoaded ? client.getItemContainer(InventoryID.INV) : null;
		ItemContainer equipment = accountStateLoaded ? client.getItemContainer(InventoryID.WORN) : null;
		boolean inventoryLoaded = ItemSlot.isLoaded(inventory);
		boolean equipmentLoaded = ItemSlot.isLoaded(equipment);
		EnumMap<Pouch, PouchState> pouches = new EnumMap<>(Pouch.class);
		for (Pouch pouch : Pouch.values())
		{
			pouches.put(pouch, accountStateLoaded ? pouch.read(client, inventoryLoaded ? inventory : null)
				: pouch.unavailable());
		}
		int totalStored = 0;
		int totalCapacity = 0;
		int totalMaximumCapacity = 0;
		for (PouchState pouch : pouches.values())
		{
			totalStored += pouch.getStoredEssence();
			totalCapacity += pouch.getCapacity();
			totalMaximumCapacity += pouch.getMaximumCapacity();
		}
		Widget inventoryWidget = client.getWidget(InterfaceID.Inventory.ITEMS);
		Widget noopsWidget = client.getWidget(InterfaceID.InventoryNoops.ITEMS);
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(inventoryWidget);
		List<Interfaces.WidgetEntry> noopsWidgets = Interfaces.snapshot(noopsWidget);

		return new State(
			pouches,
			accountStateLoaded,
			inventoryLoaded,
			equipmentLoaded,
			accountStateLoaded && client.getVarbitValue(VarbitID.GOTR_CORDELIA_REPAIR_POUCH) == 1,
			inventoryLoaded ? inventory.count(ItemID.ABYSSAL_PEARL) : 0,
			accountStateLoaded && client.getVarbitValue(VarbitID.GOTR_IS_PLAYING) == 1
				&& equipmentLoaded && equipment.contains(ItemID.ABYSSAL_LANTERN_REDWOOD),
			accountStateLoaded && client.getVarbitValue(VarbitID.EMPTYONDEATH_ESSENCEPOUCH) == 1,
			totalStored,
			totalCapacity,
			totalMaximumCapacity,
			widgets,
			noopsWidgets,
			Interfaces.widgetSnapshot(widgets, noopsWidgets));
	}

	/** Essence-pouch size and client values. */
	public enum Pouch
	{
		SMALL(
			VarbitID.SMALL_ESSENCE_POUCH,
			client -> 0,
			ItemID.RCU_POUCH_SMALL,
			-1,
			3),
		MEDIUM(
			VarbitID.MEDIUM_ESSENCE_POUCH,
			client -> client.getVarpValue(VarPlayerID.RCU_POUCH_DEGRADATION_MED),
			ItemID.RCU_POUCH_MEDIUM,
			ItemID.RCU_POUCH_MEDIUM_DEGRADE,
			6,
			800, 0,
			400, 3),
		LARGE(
			VarbitID.LARGE_ESSENCE_POUCH,
			client -> client.getVarpValue(VarPlayerID.RCU_POUCH_DEGRADATION_LARGE),
			ItemID.RCU_POUCH_LARGE,
			ItemID.RCU_POUCH_LARGE_DEGRADE,
			9,
			1000, 0,
			800, 3,
			600, 5,
			400, 7),
		GIANT(
			VarbitID.GIANT_ESSENCE_POUCH,
			client -> client.getVarpValue(VarPlayerID.RCU_POUCH_DEGRADATION_GIANT),
			ItemID.RCU_POUCH_GIANT,
			ItemID.RCU_POUCH_GIANT_DEGRADE,
			12,
			1200, 0,
			1000, 3,
			800, 5,
			600, 6,
			400, 7,
			300, 8,
			200, 9),
		COLOSSAL(
			VarbitID.COLOSSAL_ESSENCE_POUCH,
			client -> client.getVarbitValue(VarbitID.RCU_POUCH_DEGRADATION_COLOSSAL),
			ItemID.RCU_POUCH_COLOSSAL,
			ItemID.RCU_POUCH_COLOSSAL_DEGRADE,
			40,
			1020, 0,
			1015, 5,
			995, 10,
			950, 15,
			870, 20,
			745, 25,
			565, 30,
			320, 35)
			{
				@Override
				int scaleCapacity(Client client, int capacity)
				{
					if (capacity == 0)
					{
						return 0;
					}
					int level = client.getRealSkillLevel(Skill.RUNECRAFT);
					int maximum = level >= 85 ? 40 : level >= 75 ? 27 : level >= 50 ? 16 : 8;
					return Math.max(1, capacity * maximum / 40);
				}
			};

		private final int amountVarbit;
		private final ToIntFunction<Client> degradationReader;
		private final int itemId;
		private final int degradedItemId;
		private final int maximumCapacity;
		private final int[] degradationLevels;

		Pouch(
			int amountVarbit,
			ToIntFunction<Client> degradationReader,
			int itemId,
			int degradedItemId,
			int maximumCapacity,
			int... degradationLevels)
		{
			this.amountVarbit = amountVarbit;
			this.degradationReader = degradationReader;
			this.itemId = itemId;
			this.degradedItemId = degradedItemId;
			this.maximumCapacity = maximumCapacity;
			this.degradationLevels = degradationLevels;
		}

		private PouchState read(Client client, ItemContainer inventory)
		{
			int degradation = degradationReader.applyAsInt(client);
			int capacity = maximumCapacity;
			for (int index = 0; index < degradationLevels.length; index += 2)
			{
				if (degradation >= degradationLevels[index])
				{
					capacity = degradationLevels[index + 1];
					break;
				}
			}
			return new PouchState(
				this,
				client.getVarbitValue(amountVarbit),
				scaleCapacity(client, capacity),
				scaleCapacity(client, maximumCapacity),
				degradation > 0,
				inventory != null && (inventory.contains(itemId)
					|| degradedItemId >= 0 && inventory.contains(degradedItemId)),
				inventory != null && degradedItemId >= 0 && inventory.contains(degradedItemId));
		}

		private PouchState unavailable()
		{
			return new PouchState(this, 0, 0, 0, false, false, false);
		}

		int scaleCapacity(Client client, int capacity)
		{
			return capacity;
		}
	}

	/** Immutable state for one pouch size. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class PouchState
	{
		/** @return pouch size */
		private final Pouch pouch;
		/** @return essence currently stored in the pouch */
		private final int storedEssence;
		/** @return capacity at the current degradation and Runecraft level */
		private final int capacity;
		/** @return capacity with no degradation at the current Runecraft level */
		private final int maximumCapacity;
		/** @return whether any accumulated degradation can be repaired */
		@Getter(AccessLevel.NONE)
		private final boolean repairNeeded;
		/** @return whether this pouch size is currently visible in inventory */
		private final boolean inInventory;
		/** @return whether the inventory item is the visibly degraded variant */
		private final boolean visiblyDegradedInInventory;

		/** @return whether any accumulated degradation can be repaired */
		public boolean needsRepair()
		{
			return repairNeeded;
		}
	}

	/** Immutable aggregate essence-pouch state captured from one client read. */
	@Getter
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return immutable state for every pouch size */
		private final Map<Pouch, PouchState> pouches;
		/** @return whether account-persistent pouch state was safe to read */
		private final boolean accountStateLoaded;
		/** @return whether the backpack container was available */ private final boolean inventoryLoaded;
		/** @return whether the equipment container was available */ private final boolean equipmentLoaded;
		/** @return whether Cordelia's 25-pearl repair unlock has been purchased */
		private final boolean cordeliaRepairUnlocked;
		/** @return abyssal pearls currently in inventory */
		private final int abyssalPearls;
		/**
		 * @return whether a redwood-lit lantern is currently preventing degradation in GOTR
		 */
		private final boolean gotrDecayProtectionActive;
		/** @return whether essence is configured to leave pouches on death */
		private final boolean emptyOnDeathEnabled;
		/** @return total essence stored across every pouch size */ private final int totalStoredEssence;
		/** @return total current capacity across every pouch size */ private final int totalCapacity;
		/** @return total undegraded capacity across every pouch size */
		private final int totalMaximumCapacity;
		/** @return complete normal inventory widget subtree containing pouch actions */
		private final List<Interfaces.WidgetEntry> widgets;
		/** @return complete no-operations inventory widget subtree */
		private final List<Interfaces.WidgetEntry> noopsWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		private State(
			EnumMap<Pouch, PouchState> pouches,
			boolean accountStateLoaded,
			boolean inventoryLoaded,
			boolean equipmentLoaded,
			boolean cordeliaRepairUnlocked,
			int abyssalPearls,
			boolean gotrDecayProtectionActive,
			boolean emptyOnDeathEnabled,
			int totalStoredEssence,
			int totalCapacity,
			int totalMaximumCapacity,
			List<Interfaces.WidgetEntry> widgets,
			List<Interfaces.WidgetEntry> noopsWidgets,
			Interfaces.WidgetSnapshot widgetSnapshot)
		{
			this.pouches = Collections.unmodifiableMap(pouches);
			this.accountStateLoaded = accountStateLoaded;
			this.inventoryLoaded = inventoryLoaded;
			this.equipmentLoaded = equipmentLoaded;
			this.cordeliaRepairUnlocked = cordeliaRepairUnlocked;
			this.abyssalPearls = abyssalPearls;
			this.gotrDecayProtectionActive = gotrDecayProtectionActive;
			this.emptyOnDeathEnabled = emptyOnDeathEnabled;
			this.totalStoredEssence = totalStoredEssence;
			this.totalCapacity = totalCapacity;
			this.totalMaximumCapacity = totalMaximumCapacity;
			this.widgets = widgets;
			this.noopsWidgets = noopsWidgets;
			this.widgetSnapshot = widgetSnapshot;
		}

		/**
		 * Returns one pouch-size state.
		 *
		 * @param pouch pouch size
		 * @return pouch state
		 */
		public PouchState getPouch(Pouch pouch)
		{
			return pouches.get(Objects.requireNonNull(pouch, "pouch"));
		}

		/** @return whether any pouch has accumulated repairable degradation */
		public boolean needsRepair()
		{
			for (PouchState pouch : pouches.values())
			{
				if (pouch.needsRepair())
				{
					return true;
				}
			}
			return false;
		}

		/** @return whether the player has unlocked and can pay for Cordelia's next repair */
		public boolean canPayForCordeliaRepair()
		{
			return cordeliaRepairUnlocked
				&& abyssalPearls >= CORDELIA_REPAIR_PEARL_COST;
		}

	}
}
