package net.runelite.client.plugins.m8aq.api.minigames;

import java.util.Collections;
import java.util.EnumSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;

/** Provides a read-only snapshot of the client-observable Mastering Mixology state. */
public final class MasteringMixology
{
	private static final int LAB_REGION_ID = 5521;

	private MasteringMixology()
	{
	}

	/**
	 * Reads the current Mastering Mixology state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");

		List<Order> orders = List.of(
			readOrder(client, 1, VarbitID.MM_LAB_ORDER_1_TYPE, VarbitID.MM_LAB_ORDER_1_MODIFIER),
			readOrder(client, 2, VarbitID.MM_LAB_ORDER_2_TYPE, VarbitID.MM_LAB_ORDER_2_MODIFIER),
			readOrder(client, 3, VarbitID.MM_LAB_ORDER_3_TYPE, VarbitID.MM_LAB_ORDER_3_MODIFIER));
		List<Reagent> mixerSlots = List.of(
			Reagent.fromValue(client.getVarbitValue(VarbitID.MM_LAB_MIXER_SLOT_0)),
			Reagent.fromValue(client.getVarbitValue(VarbitID.MM_LAB_MIXER_SLOT_1)),
			Reagent.fromValue(client.getVarbitValue(VarbitID.MM_LAB_MIXER_SLOT_2)));
		int vesselPotionValue = client.getVarbitValue(VarbitID.MM_LAB_VESSEL_READY);
		EnumSet<DigweedPatch> readyDigweedPatches = EnumSet.noneOf(DigweedPatch.class);
		for (DigweedPatch patch : DigweedPatch.values())
		{
			if (client.getVarbitValue(patch.varbit) == 1)
			{
				readyDigweedPatches.add(patch);
			}
		}

		Player player = client.getLocalPlayer();
		WorldPoint location = player == null ? null : player.getWorldLocation();
		boolean atMasteringMixology = location != null
			&& location.getRegionID() == LAB_REGION_ID
			&& location.getPlane() == 0;
		Widget overlay = client.getWidget(InterfaceID.MmOverlay.UNIVERSE);

		return new State(
			overlay != null && !overlay.isHidden(),
			atMasteringMixology,
			orders,
			mixerSlots,
			PotionType.fromRecipe(mixerSlots),
			vesselPotionValue,
			PotionType.fromValue(vesselPotionValue),
			readStation(client, Station.AGITATOR, VarbitID.MM_LAB_AGITATOR_POTION,
				VarbitID.MM_AGITATOR_PROGRESS, VarbitID.MM_LAB_HIT_SKILLSHOT_AGITATOR),
			readStation(client, Station.RETORT, VarbitID.MM_LAB_RETORT_POTION,
				VarbitID.MM_RETORT_PROGRESS, 0),
			readStation(client, Station.ALEMBIC, VarbitID.MM_LAB_ALEMBIC_POTION,
				VarbitID.MM_ALEMBIC_PROGRESS, VarbitID.MM_LAB_HIT_SKILLSHOT_ALEMBIC),
			Collections.unmodifiableSet(readyDigweedPatches),
			client.getVarbitValue(VarbitID.MM_ORDERS_SINCE_HERB),
			client.getVarbitValue(VarbitID.MM_INTRODUCTION_COMPLETE) == 1,
			client.getVarbitValue(VarbitID.MM_MET_SUPERVISOR) == 1,
			client.getVarbitValue(VarbitID.MM_POTION_STORAGE_UNLOCKED) == 1,
			client.getVarbitValue(VarbitID.MM_AVAILABLE_MOX),
			client.getVarbitValue(VarbitID.MM_AVAILABLE_AGA),
			client.getVarbitValue(VarbitID.MM_AVAILABLE_LYE),
			client.getVarpValue(VarPlayerID.MIXOLOGY_MOX_POINTS),
			client.getVarpValue(VarPlayerID.MIXOLOGY_AGA_POINTS),
			client.getVarpValue(VarPlayerID.MIXOLOGY_LYE_POINTS),
			client.getVarpValue(VarPlayerID.TOTAL_MIXOLOGY_ORDERS),
			client.getVarbitValue(VarbitID.COLLECTION_MINIGAMES_MASTERING_MIXOLOGY_COMPLETED) == 1);
	}

	private static Order readOrder(Client client, int slot, int typeVarbit, int modifierVarbit)
	{
		int potionValue = client.getVarbitValue(typeVarbit);
		int modifierValue = client.getVarbitValue(modifierVarbit);
		return new Order(slot, potionValue, PotionType.fromValue(potionValue),
			modifierValue, Modifier.fromValue(modifierValue));
	}

	private static StationState readStation(
		Client client,
		Station station,
		int potionVarbit,
		int progressVarbit,
		int quickActionVarbit)
	{
		int potionValue = client.getVarbitValue(potionVarbit);
		return new StationState(
			station,
			potionValue,
			PotionType.fromValue(potionValue),
			client.getVarbitValue(progressVarbit),
			quickActionVarbit != 0 && client.getVarbitValue(quickActionVarbit) == 1);
	}

	/** Paste loaded into one mixer slot. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Reagent
	{
		NONE(0),
		MOX(1),
		AGA(2),
		LYE(3),
		UNKNOWN(-1);

		/** @return raw mixer-slot value */ private final int value;

		private static Reagent fromValue(int value)
		{
			for (Reagent reagent : values())
			{
				if (reagent.value == value)
				{
					return reagent;
				}
			}
			return UNKNOWN;
		}
	}

	/** Potion recipe used by orders, the vessel, and processing stations. */
	@Getter
	public enum PotionType
	{
		NONE(0),
		MAMMOTH_MIGHT_MIX(1, Reagent.MOX, Reagent.MOX, Reagent.MOX),
		MYSTIC_MANA_AMALGAM(2, Reagent.MOX, Reagent.MOX, Reagent.AGA),
		MARLEYS_MOONLIGHT(3, Reagent.MOX, Reagent.MOX, Reagent.LYE),
		ALCO_AUGMENTATOR(4, Reagent.AGA, Reagent.AGA, Reagent.AGA),
		AZURE_AURA_MIX(5, Reagent.AGA, Reagent.AGA, Reagent.MOX),
		AQUALUX_AMALGAM(6, Reagent.AGA, Reagent.AGA, Reagent.LYE),
		LIPLACK_LIQUOR(7, Reagent.LYE, Reagent.LYE, Reagent.LYE),
		MEGALITE_LIQUID(8, Reagent.LYE, Reagent.LYE, Reagent.MOX),
		ANTI_LEECH_LOTION(9, Reagent.LYE, Reagent.LYE, Reagent.AGA),
		MIXALOT(10, Reagent.MOX, Reagent.AGA, Reagent.LYE),
		UNKNOWN(-1);

		/** @return raw one-based potion value */ private final int value;
		/** @return immutable three-paste recipe */ private final List<Reagent> recipe;

		PotionType(int value, Reagent... recipe)
		{
			this.value = value;
			this.recipe = List.of(recipe);
		}

		private static PotionType fromValue(int value)
		{
			for (PotionType type : values())
			{
				if (type.value == value)
				{
					return type;
				}
			}
			return UNKNOWN;
		}

		private static PotionType fromRecipe(List<Reagent> recipe)
		{
			if (recipe.contains(Reagent.NONE))
			{
				return NONE;
			}
			if (recipe.contains(Reagent.UNKNOWN))
			{
				return UNKNOWN;
			}
			for (PotionType type : values())
			{
				if (type.value > 0 && sameCounts(type.recipe, recipe))
				{
					return type;
				}
			}
			return UNKNOWN;
		}

		private static boolean sameCounts(List<Reagent> first, List<Reagent> second)
		{
			return Collections.frequency(first, Reagent.MOX)
				== Collections.frequency(second, Reagent.MOX)
				&& Collections.frequency(first, Reagent.AGA)
				== Collections.frequency(second, Reagent.AGA)
				&& Collections.frequency(first, Reagent.LYE)
				== Collections.frequency(second, Reagent.LYE);
		}
	}

	/** Potion processing station. */
	public enum Station
	{
		AGITATOR,
		RETORT,
		ALEMBIC
	}

	/** Modifier requested by one potion order. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Modifier
	{
		NONE(0, null),
		HOMOGENOUS(1, Station.AGITATOR),
		CONCENTRATED(2, Station.RETORT),
		CRYSTALLISED(3, Station.ALEMBIC),
		UNKNOWN(-1, null);

		/** @return raw one-based modifier value */ private final int value;
		/** @return processing station, or null for none/unknown */ private final Station station;

		private static Modifier fromValue(int value)
		{
			for (Modifier modifier : values())
			{
				if (modifier.value == value)
				{
					return modifier;
				}
			}
			return UNKNOWN;
		}
	}

	/** One of the four possible digweed spawn patches. */
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum DigweedPatch
	{
		NORTH_EAST(VarbitID.MM_HERB_READY_1),
		SOUTH_EAST(VarbitID.MM_HERB_READY_2),
		SOUTH_WEST(VarbitID.MM_HERB_READY_3),
		NORTH_WEST(VarbitID.MM_HERB_READY_4);

		private final int varbit;
	}

	/** Immutable state for one requested potion. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Order
	{
		/** @return one-based order slot */ private final int slot;
		/** @return raw potion-type value */ private final int potionValue;
		/** @return decoded potion type */ private final PotionType potion;
		/** @return raw modifier value */ private final int modifierValue;
		/** @return decoded processing modifier */ private final Modifier modifier;

		/** @return whether this slot contains a recognized active order */
		public boolean isActive()
		{
			return potion != PotionType.NONE && potion != PotionType.UNKNOWN
				&& modifier != Modifier.NONE && modifier != Modifier.UNKNOWN;
		}
	}

	/** Immutable state for one processing station. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class StationState
	{
		/** @return processing station */ private final Station station;
		/** @return raw potion value, with zero meaning empty */ private final int potionValue;
		/** @return decoded potion in the station */ private final PotionType potion;
		/** @return raw station progress */ private final int progress;
		/** @return whether the station's quick-action window is active */
		private final boolean quickActionActive;

		/** @return whether this station currently contains a recognized potion */
		public boolean isOccupied()
		{
			return potion != PotionType.NONE && potion != PotionType.UNKNOWN;
		}
	}

	/** Immutable Mastering Mixology state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the official order overlay is visible */ private final boolean overlayVisible;
		/** @return whether the local player is inside the Mastering Mixology lab */
		private final boolean atMasteringMixology;
		/** @return immutable three-slot order list */ private final List<Order> orders;
		/** @return immutable mixer slots in oldest-to-newest order */ private final List<Reagent> mixerSlots;
		/** @return potion represented by the full mixer, NONE while incomplete */
		private final PotionType mixedPotion;
		/** @return raw potion value waiting in the mixing vessel */ private final int vesselPotionValue;
		/** @return decoded potion waiting in the mixing vessel */ private final PotionType vesselPotion;
		/** @return current Agitator state */ private final StationState agitator;
		/** @return current Retort state */ private final StationState retort;
		/** @return current Alembic state */ private final StationState alembic;
		/** @return immutable set of ready digweed patches */ private final Set<DigweedPatch> readyDigweedPatches;
		/** @return completed orders since the last digweed spawn */ private final int ordersSinceDigweed;
		/** @return whether the introduction is complete */ private final boolean introductionComplete;
		/** @return whether the player has met the lab supervisor */ private final boolean metSupervisor;
		/** @return whether potion storage is unlocked */ private final boolean potionStorageUnlocked;
		/** @return available Mox paste */ private final int moxPaste;
		/** @return available Aga paste */ private final int agaPaste;
		/** @return available Lye paste */ private final int lyePaste;
		/** @return persistent Mox resin */ private final int moxResin;
		/** @return persistent Aga resin */ private final int agaResin;
		/** @return persistent Lye resin */ private final int lyeResin;
		/** @return lifetime completed Mixology orders */ private final int totalOrders;
		/** @return whether the Mastering Mixology collection-log section is complete */
		private final boolean collectionLogCompleted;

		/**
		 * Returns one order by its one-based slot.
		 *
		 * @param slot slot from 1 through 3
		 * @return immutable order state
		 */
		public Order getOrder(int slot)
		{
			if (slot < 1 || slot > orders.size())
			{
				throw new IllegalArgumentException("slot must be from 1 through 3");
			}
			return orders.get(slot - 1);
		}

		/** @return whether any processing station currently contains a potion */
		public boolean hasActiveStation()
		{
			return agitator.isOccupied() || retort.isOccupied() || alembic.isOccupied();
		}
	}
}
