package com.example.m8aq.api.storage;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.EnumComposition;
import net.runelite.api.EnumID;
import net.runelite.api.GameState;
import net.runelite.api.ItemComposition;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of rune-pouch contents, configuration, and loaded interfaces. */
public final class RunePouch
{
	/** Maximum actual quantity stored in one rune-pouch slot. */
	public static final int MAX_QUANTITY_PER_SLOT = 16_000;
	/** Maximum target quantity accepted by one loadout slot. */
	public static final int MAX_LOADOUT_QUANTITY = 15_999;

	private static final int LOADOUT_NAME_ENUM = 5865;
	private static final int[] TYPE_VARBITS =
	{
		VarbitID.RUNE_POUCH_TYPE_1,
		VarbitID.RUNE_POUCH_TYPE_2,
		VarbitID.RUNE_POUCH_TYPE_3,
		VarbitID.RUNE_POUCH_TYPE_4,
		VarbitID.RUNE_POUCH_TYPE_5,
		VarbitID.RUNE_POUCH_TYPE_6
	};
	private static final int[] QUANTITY_VARBITS =
	{
		VarbitID.RUNE_POUCH_QUANTITY_1,
		VarbitID.RUNE_POUCH_QUANTITY_2,
		VarbitID.RUNE_POUCH_QUANTITY_3,
		VarbitID.RUNE_POUCH_QUANTITY_4,
		VarbitID.RUNE_POUCH_QUANTITY_5,
		VarbitID.RUNE_POUCH_QUANTITY_6
	};
	private static final int[] LOADOUT_NAME_VARBITS =
	{
		VarbitID.RUNE_POUCH_LOADOUT_NAME_A,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_B,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_C,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_D,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_E,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_F,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_G,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_H,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_I,
		VarbitID.RUNE_POUCH_LOADOUT_NAME_J
	};
	private static final int[][] LOADOUT_TYPE_VARBITS =
	{
		{0, 0, VarbitID.RUNE_POUCH_LOADOUT_A_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_A_RUNE4},
		{VarbitID.RUNE_POUCH_LOADOUT_B_RUNE1, VarbitID.RUNE_POUCH_LOADOUT_B_RUNE2,
			VarbitID.RUNE_POUCH_LOADOUT_B_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_B_RUNE4},
		{VarbitID.RUNE_POUCH_LOADOUT_C_RUNE1, VarbitID.RUNE_POUCH_LOADOUT_C_RUNE2,
			VarbitID.RUNE_POUCH_LOADOUT_C_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_C_RUNE4},
		{VarbitID.RUNE_POUCH_LOADOUT_D_RUNE1, VarbitID.RUNE_POUCH_LOADOUT_D_RUNE2,
			VarbitID.RUNE_POUCH_LOADOUT_D_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_D_RUNE4},
		{0, 0, VarbitID.RUNE_POUCH_LOADOUT_E_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_E_RUNE4},
		{VarbitID.RUNE_POUCH_LOADOUT_F_RUNE1, VarbitID.RUNE_POUCH_LOADOUT_F_RUNE2,
			VarbitID.RUNE_POUCH_LOADOUT_F_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_F_RUNE4},
		{VarbitID.RUNE_POUCH_LOADOUT_G_RUNE1, VarbitID.RUNE_POUCH_LOADOUT_G_RUNE2,
			VarbitID.RUNE_POUCH_LOADOUT_G_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_G_RUNE4},
		{VarbitID.RUNE_POUCH_LOADOUT_H_RUNE1, VarbitID.RUNE_POUCH_LOADOUT_H_RUNE2,
			VarbitID.RUNE_POUCH_LOADOUT_H_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_H_RUNE4},
		{0, 0, VarbitID.RUNE_POUCH_LOADOUT_I_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_I_RUNE4},
		{VarbitID.RUNE_POUCH_LOADOUT_J_RUNE1, VarbitID.RUNE_POUCH_LOADOUT_J_RUNE2,
			VarbitID.RUNE_POUCH_LOADOUT_J_RUNE3, VarbitID.RUNE_POUCH_LOADOUT_J_RUNE4}
	};
	private static final int[][] LOADOUT_QUANTITY_VARBITS =
	{
		{VarbitID.RUNE_POUCH_LOADOUT_A_CAP1, VarbitID.RUNE_POUCH_LOADOUT_A_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_A_CAP3, VarbitID.RUNE_POUCH_LOADOUT_A_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_B_CAP1, VarbitID.RUNE_POUCH_LOADOUT_B_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_B_CAP3, VarbitID.RUNE_POUCH_LOADOUT_B_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_C_CAP1, VarbitID.RUNE_POUCH_LOADOUT_C_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_C_CAP3, VarbitID.RUNE_POUCH_LOADOUT_C_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_D_CAP1, VarbitID.RUNE_POUCH_LOADOUT_D_CAP2, 0,
			VarbitID.RUNE_POUCH_LOADOUT_D_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_E_CAP1, VarbitID.RUNE_POUCH_LOADOUT_E_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_E_CAP3, VarbitID.RUNE_POUCH_LOADOUT_E_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_F_CAP1, VarbitID.RUNE_POUCH_LOADOUT_F_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_F_CAP3, VarbitID.RUNE_POUCH_LOADOUT_F_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_G_CAP1, VarbitID.RUNE_POUCH_LOADOUT_G_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_G_CAP3, VarbitID.RUNE_POUCH_LOADOUT_G_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_H_CAP1, VarbitID.RUNE_POUCH_LOADOUT_H_CAP2, 0,
			VarbitID.RUNE_POUCH_LOADOUT_H_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_I_CAP1, VarbitID.RUNE_POUCH_LOADOUT_I_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_I_CAP3, VarbitID.RUNE_POUCH_LOADOUT_I_CAP4},
		{VarbitID.RUNE_POUCH_LOADOUT_J_CAP1, VarbitID.RUNE_POUCH_LOADOUT_J_CAP2,
			VarbitID.RUNE_POUCH_LOADOUT_J_CAP3, VarbitID.RUNE_POUCH_LOADOUT_J_CAP4}
	};

	private RunePouch()
	{
	}

	/**
	 * Reads current rune-pouch state from the client.
	 * Contents are intentionally hidden when no pouch variant is carried because the varbits persist.
	 * Account-persistent configuration is hidden while logged out for the same reason.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable rune-pouch snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		ItemContainer inventory = client.getItemContainer(InventoryID.INV);
		boolean inventoryLoaded = inventory != null;
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		Widget pouchInterface = client.getWidget(InterfaceID.RunePouch.UNIVERSE);
		Widget bankConfiguration = client.getWidget(InterfaceID.Bankside.RUNEPOUCH_CONTAINER);
		boolean pouchInterfaceOpen = Interfaces.isVisible(pouchInterface);
		boolean bankConfigurationOpen = Interfaces.isVisible(bankConfiguration);

		PouchType type = inventory == null ? PouchType.NONE : pouchType(inventory);
		boolean protectedVariant = inventory != null && (inventory.contains(ItemID.BH_RUNE_POUCH_TROUVER)
			|| inventory.contains(ItemID.DIVINE_RUNE_POUCH_TROUVER));
		EnumComposition runeTypes = client.getEnum(EnumID.RUNEPOUCH_RUNE);
		List<ItemSlot> slots = Collections.emptyList();
		int activeSlots = 0;
		long total = 0;
		boolean extraSlotPopulated = false;
		if (type != PouchType.NONE)
		{
			List<ItemSlot> mutableSlots = new ArrayList<>(TYPE_VARBITS.length);
			for (int slot = 0; slot < TYPE_VARBITS.length; slot++)
			{
				int typeKey = client.getVarbitValue(TYPE_VARBITS[slot]);
				int quantity = client.getVarbitValue(QUANTITY_VARBITS[slot]);
				mutableSlots.add(itemSlot(client, runeTypes, slot, typeKey, quantity));
				if (typeKey != 0)
				{
					activeSlots++;
					total += quantity;
					extraSlotPopulated |= slot >= 4;
				}
			}
			slots = Collections.unmodifiableList(mutableSlots);
		}

		int baseSlotCapacity = inventoryLoaded ? type == PouchType.RUNE ? 3 : type == PouchType.DIVINE ? 4 : 0 : -1;
		ExtraSlotAccess extraSlotAccess = inventoryLoaded
			? extraSlotAccess(type, pouchInterfaceOpen,
				client.getWidget(InterfaceID.RunePouch.POUCH), extraSlotPopulated)
			: ExtraSlotAccess.UNKNOWN;
		int usableSlotCount = !inventoryLoaded ? -1
			: type == PouchType.NONE ? 0
				: extraSlotAccess == ExtraSlotAccess.ENABLED ? baseSlotCapacity + 2
					: extraSlotAccess == ExtraSlotAccess.DISABLED ? baseSlotCapacity : -1;

		List<Loadout> loadouts = accountStateLoaded
			? readLoadouts(client, runeTypes, client.getEnum(LOADOUT_NAME_ENUM)) : Collections.emptyList();
		List<Interfaces.WidgetEntry> pouchWidgets = Interfaces.snapshot(pouchInterface);
		List<Interfaces.WidgetEntry> bankConfigurationWidgets = Interfaces.snapshot(bankConfiguration);
		return new State(
			inventoryLoaded,
			accountStateLoaded,
			type,
			protectedVariant,
			slots,
			baseSlotCapacity,
			extraSlotAccess,
			usableSlotCount,
			activeSlots,
			total,
			accountStateLoaded
				? WithdrawalMode.fromRaw(client.getVarbitValue(VarbitID.RUNE_POUCH_SELECTEDQUANTITY))
				: WithdrawalMode.UNKNOWN,
			accountStateLoaded ? client.getVarbitValue(VarbitID.RUNE_POUCH_CUSTOMQUANTITY) : 0,
			accountStateLoaded && client.getVarbitValue(VarbitID.TAKE_RUNES_TOGGLE) == 1,
			accountStateLoaded && client.getVarbitValue(VarbitID.EMPTYONDEATH_RUNEPOUCH) == 1,
			accountStateLoaded && client.getVarbitValue(VarbitID.SETTINGS_RUNEPOUCH_LOADOUT_NAMES_DISABLED) == 0,
			loadouts,
			pouchInterfaceOpen,
			bankConfigurationOpen,
			pouchWidgets,
			bankConfigurationWidgets,
			Interfaces.widgetSnapshot(pouchWidgets, bankConfigurationWidgets));
	}

	private static PouchType pouchType(ItemContainer inventory)
	{
		if (inventory.contains(ItemID.DIVINE_RUNE_POUCH)
			|| inventory.contains(ItemID.DIVINE_RUNE_POUCH_TROUVER))
		{
			return PouchType.DIVINE;
		}
		return inventory.contains(ItemID.BH_RUNE_POUCH)
			|| inventory.contains(ItemID.BH_RUNE_POUCH_TROUVER) ? PouchType.RUNE : PouchType.NONE;
	}

	private static ItemSlot itemSlot(
		Client client, EnumComposition runeTypes, int slot, int typeKey, int quantity)
	{
		int itemId = typeKey == 0 || runeTypes == null ? -1 : runeTypes.getIntValue(typeKey);
		ItemComposition definition = itemId < 0 ? null : client.getItemDefinition(itemId);
		return new ItemSlot(slot, itemId, definition == null ? null : definition.getName(), quantity);
	}

	private static ExtraSlotAccess extraSlotAccess(
		PouchType type, boolean pouchInterfaceOpen, Widget pouch, boolean extraSlotPopulated)
	{
		if (type == PouchType.NONE)
		{
			return ExtraSlotAccess.DISABLED;
		}
		if (extraSlotPopulated)
		{
			return ExtraSlotAccess.ENABLED;
		}
		if (!pouchInterfaceOpen || pouch == null)
		{
			return ExtraSlotAccess.UNKNOWN;
		}
		Widget[] children = pouch.getDynamicChildren();
		if (children == null || children.length < 6 || children[4] == null || children[5] == null)
		{
			return ExtraSlotAccess.UNKNOWN;
		}
		boolean fifthVisible = !children[4].isHidden();
		boolean sixthVisible = !children[5].isHidden();
		return fifthVisible && sixthVisible ? ExtraSlotAccess.ENABLED
			: !fifthVisible && !sixthVisible ? ExtraSlotAccess.DISABLED : ExtraSlotAccess.UNKNOWN;
	}

	private static List<Loadout> readLoadouts(
		Client client, EnumComposition runeTypes, EnumComposition loadoutNames)
	{
		List<Loadout> loadouts = new ArrayList<>(LOADOUT_NAME_VARBITS.length);
		for (int loadout = 0; loadout < LOADOUT_NAME_VARBITS.length; loadout++)
		{
			List<ItemSlot> slots = new ArrayList<>(4);
			for (int slot = 0; slot < 4; slot++)
			{
				int typeKey = readLoadoutType(client, loadout, slot);
				int quantity = readLoadoutQuantity(client, loadout, slot);
				if (quantity < 0 || quantity > MAX_LOADOUT_QUANTITY)
				{
					quantity = 0;
				}
				ItemSlot value = itemSlot(client, runeTypes, slot, typeKey, quantity);
				slots.add(value.getItemId() < 0 ? new ItemSlot(slot, -1, null, 0) : value);
			}
			int nameId = client.getVarbitValue(LOADOUT_NAME_VARBITS[loadout]);
			loadouts.add(new Loadout(
				loadout,
				String.valueOf((char) ('A' + loadout)),
				nameId,
				loadoutNames == null ? null : loadoutNames.getStringValue(nameId),
				Collections.unmodifiableList(slots)));
		}
		return Collections.unmodifiableList(loadouts);
	}

	private static int readLoadoutType(Client client, int loadout, int slot)
	{
		if ((loadout == 0 || loadout == 4 || loadout == 8) && slot < 2)
		{
			int low;
			int high;
			if (loadout == 0)
			{
				low = slot == 0 ? VarbitID.RUNE_POUCH_LOADOUT_A_RUNE1_BITSA
					: VarbitID.RUNE_POUCH_LOADOUT_A_RUNE2_BITSA;
				high = slot == 0 ? VarbitID.RUNE_POUCH_LOADOUT_A_RUNE1_BITSB
					: VarbitID.RUNE_POUCH_LOADOUT_A_RUNE2_BITSB;
			}
			else if (loadout == 4)
			{
				low = slot == 0 ? VarbitID.RUNE_POUCH_LOADOUT_E_RUNE1_BITSA
					: VarbitID.RUNE_POUCH_LOADOUT_E_RUNE2_BITSA;
				high = slot == 0 ? VarbitID.RUNE_POUCH_LOADOUT_E_RUNE1_BITSB
					: VarbitID.RUNE_POUCH_LOADOUT_E_RUNE2_BITSB;
			}
			else
			{
				low = slot == 0 ? VarbitID.RUNE_POUCH_LOADOUT_I_RUNE1_BITSA
					: VarbitID.RUNE_POUCH_LOADOUT_I_RUNE2_BITSA;
				high = slot == 0 ? VarbitID.RUNE_POUCH_LOADOUT_I_RUNE1_BITSB
					: VarbitID.RUNE_POUCH_LOADOUT_I_RUNE2_BITSB;
			}
			return 16 * client.getVarbitValue(high) + client.getVarbitValue(low);
		}
		return client.getVarbitValue(LOADOUT_TYPE_VARBITS[loadout][slot]);
	}

	private static int readLoadoutQuantity(Client client, int loadout, int slot)
	{
		if ((loadout == 3 || loadout == 7) && slot == 2)
		{
			int low = loadout == 3 ? VarbitID.RUNE_POUCH_LOADOUT_D_CAP3_BITSA
				: VarbitID.RUNE_POUCH_LOADOUT_H_CAP3_BITSA;
			int middle = loadout == 3 ? VarbitID.RUNE_POUCH_LOADOUT_D_CAP3_BITSB
				: VarbitID.RUNE_POUCH_LOADOUT_H_CAP3_BITSB;
			int high = loadout == 3 ? VarbitID.RUNE_POUCH_LOADOUT_D_CAP3_BITSC
				: VarbitID.RUNE_POUCH_LOADOUT_H_CAP3_BITSC;
			return 1024 * client.getVarbitValue(high)
				+ 64 * client.getVarbitValue(middle) + client.getVarbitValue(low);
		}
		return client.getVarbitValue(LOADOUT_QUANTITY_VARBITS[loadout][slot]);
	}

	/** Carried rune-pouch item family. */
	public enum PouchType
	{
		NONE,
		RUNE,
		DIVINE
	}

	/** Client-observable access to League/event rune slots five and six. */
	public enum ExtraSlotAccess
	{
		ENABLED,
		DISABLED,
		UNKNOWN
	}

	/** Selected quantity used by the rune-pouch interface. */
	public enum WithdrawalMode
	{
		ONE,
		FIVE,
		ALL,
		CUSTOM,
		UNKNOWN;

		private static WithdrawalMode fromRaw(int value)
		{
			switch (value)
			{
				case 0:
					return ONE;
				case 1:
					return FIVE;
				case 2:
					return ALL;
				case 3:
					return CUSTOM;
				default:
					return UNKNOWN;
			}
		}
	}

	/** Immutable server-synchronized rune-pouch loadout. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Loadout
	{
		/** @return zero-based loadout index */
		private final int index;
		/** @return stable A-J loadout label */
		private final String label;
		/** @return raw server-synchronized name enum key */
		private final int nameId;
		/** @return cache-resolved loadout name, or {@code null} when unavailable */
		private final String name;
		/** @return immutable four-entry target rune and quantity list */
		private final List<ItemSlot> slots;
	}

	/** Immutable rune-pouch state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether the inventory container was available to classify pouch presence */
		private final boolean inventoryLoaded;
		/** @return whether account-persistent settings and loadouts were safe to read */
		private final boolean accountStateLoaded;
		/** @return carried pouch family, or NONE */
		private final PouchType pouchType;
		/** @return whether the carried pouch is the trouver-protected item variant */
		private final boolean protectedVariant;
		/** @return immutable six-entry current rune-slot list, or empty without a carried pouch */
		private final List<ItemSlot> slots;
		/** @return pouch-family base capacity, zero without a pouch, or -1 when inventory is unavailable */
		private final int baseSlotCapacity;
		/** @return client-observable access to slots five and six */
		private final ExtraSlotAccess extraSlotAccess;
		/** @return exact usable slot count, or -1 when extra-slot entitlement is unknown */
		private final int usableSlotCount;
		/** @return number of non-zero current rune type slots */
		private final int activeSlotCount;
		/** @return total server-synchronized stored rune quantity */
		private final long totalQuantity;
		/** @return selected interface withdrawal mode, or UNKNOWN while account state is unavailable */
		private final WithdrawalMode withdrawalMode;
		/** @return raw custom withdrawal quantity, or zero while account state is unavailable */
		private final int customQuantity;
		/** @return whether matching picked-up or crafted runes are automatically stored */
		private final boolean autoStoreRunesEnabled;
		/** @return whether the account setting empties the rune pouch on death */
		private final boolean emptyOnDeathEnabled;
		/** @return whether loadout names are enabled */
		private final boolean loadoutNamesEnabled;
		/** @return immutable ten-entry loadout list, or empty while account state is unavailable */
		private final List<Loadout> loadouts;
		/** @return whether the standalone rune-pouch interface is visibly open */
		private final boolean pouchInterfaceOpen;
		/** @return whether the bank rune-pouch configuration panel is visibly open */
		private final boolean bankConfigurationOpen;
		/** @return every currently loaded fixed and dynamic widget under the standalone pouch interface */
		private final List<Interfaces.WidgetEntry> pouchWidgets;
		/** @return every currently loaded fixed and dynamic widget under the bank configuration panel */
		private final List<Interfaces.WidgetEntry> bankConfigurationWidgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

		/** @return whether any rune-pouch item variant is carried */
		public boolean isPresent()
		{
			return pouchType != PouchType.NONE;
		}

	}
}
