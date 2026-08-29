package net.runelite.client.plugins.m8aq.api.minigames;

import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;
import java.util.Objects;
import lombok.Getter;
import net.runelite.api.Client;
import net.runelite.api.GameObject;
import net.runelite.api.ItemContainer;
import net.runelite.api.Player;
import net.runelite.api.Tile;
import net.runelite.api.WorldView;
import net.runelite.api.gameval.AnimationID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.gameval.ObjectID;
import net.runelite.api.gameval.VarbitID;

/**
 * Provides a read-only snapshot of the player-visible Blast Furnace state in
 * both automatic and manually operated worlds. Because the values can persist
 * outside the minigame, the snapshot also records whether the local player is
 * in the Blast Furnace region.
 */
public final class BlastFurnace
{
	private static final int BLAST_FURNACE_REGION_ID = 7757;

	private BlastFurnace()
	{
	}

	/**
	 * Reads the current Blast Furnace state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");

		EnumMap<Material, Integer> materials = new EnumMap<>(Material.class);
		for (Material material : Material.values())
		{
			putNonZero(materials, material, client.getVarbitValue(material.varbit));
		}

		EnumMap<Bar, Integer> bars = new EnumMap<>(Bar.class);
		for (Bar bar : Bar.values())
		{
			putNonZero(bars, bar, client.getVarbitValue(bar.varbit));
		}

		ItemContainer equipment = client.getItemContainer(InventoryID.WORN);
		boolean coolingGlovesEquipped = equipment != null
			&& (equipment.contains(ItemID.ICE_GLOVES)
				|| equipment.contains(ItemID.SMITHING_UNIFORM_GLOVES_ICE));
		Player localPlayer = client.getLocalPlayer();
		boolean atBlastFurnace = localPlayer != null
			&& localPlayer.getWorldLocation().getRegionID() == BLAST_FURNACE_REGION_ID;
		MachineryState machinery = readMachinery(client.getTopLevelWorldView());
		int dispenserValue = client.getVarbitValue(VarbitID.BLAST_FURNACE_BARS_HOT);

		return new State(
			dispenserValue,
			DispenserState.fromValue(dispenserValue),
			client.getVarbitValue(VarbitID.BLAST_FURNACE_TEMPERATURE_READING),
			client.getVarbitValue(VarbitID.BLAST_FURNACE_BROKEN_PIPE_READING) == 1,
			client.getVarbitValue(VarbitID.BLAST_FURNACE_FUEL_LOW_READING) == 1,
			client.getVarbitValue(VarbitID.BLAST_FURNACE_COAL_REQUIRED),
			client.getVarbitValue(VarbitID.BLAST_FURNACE_COINSINCOFFER) == 1,
			client.getVarbitValue(VarbitID.BLAST_FURNACE_COFFER),
			client.getVarbitValue(VarbitID.BLAST_FURNACE_VISITED) == 1,
			client.getVarbitValue(VarbitID.BLAST_FURNACE_HELPER_DISABLED) == 1,
			client.getVarbitValue(VarbitID.BLAST_FURNACE_HIGHLIGHTS_DISABLED) == 1,
			materials,
			bars,
			coolingGlovesEquipped,
			atBlastFurnace,
			machinery,
			PlayerOperation.fromAnimation(localPlayer == null ? -1 : localPlayer.getAnimation()));
	}

	private static MachineryState readMachinery(WorldView worldView)
	{
		if (worldView == null || worldView.getScene() == null)
		{
			return MachineryState.UNKNOWN;
		}
		int plane = worldView.getPlane();
		Tile[][][] tiles = worldView.getScene().getTiles();
		if (tiles == null || plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			return MachineryState.UNKNOWN;
		}

		StoveState stove = StoveState.UNKNOWN;
		ComponentState driveBelt = ComponentState.UNKNOWN;
		ComponentState cogs = ComponentState.UNKNOWN;
		ComponentState westPipe = ComponentState.UNKNOWN;
		ComponentState eastPipe = ComponentState.UNKNOWN;
		for (Tile[] column : tiles[plane])
		{
			if (column == null)
			{
				continue;
			}
			for (Tile tile : column)
			{
				if (tile == null || tile.getGameObjects() == null)
				{
					continue;
				}
				for (GameObject object : tile.getGameObjects())
				{
					if (object == null)
					{
						continue;
					}
					switch (object.getId())
					{
						case ObjectID.BLAST_FURNACE_STOVE_LOW:
							stove = StoveState.LOW;
							break;
						case ObjectID.BLAST_FURNACE_STOVE_MEDIUM:
							stove = StoveState.MEDIUM;
							break;
						case ObjectID.BLAST_FURNACE_STOVE_FULL:
							stove = StoveState.FULL;
							break;
						case ObjectID.BLAST_FURNACE_CONVEYER_COGS:
							driveBelt = ComponentState.OPERATIONAL;
							break;
						case ObjectID.BLAST_FURNACE_CONVEYER_COGS_BROKEN:
							driveBelt = ComponentState.BROKEN;
							break;
						case ObjectID.BLAST_FURNACE_CONVEYER_COGS2:
							cogs = ComponentState.OPERATIONAL;
							break;
						case ObjectID.BLAST_FURNACE_CONVEYER_COGS2_BROKEN:
							cogs = ComponentState.BROKEN;
							break;
						case ObjectID.BLAST_FURNACE_PIPES2:
							westPipe = ComponentState.OPERATIONAL;
							break;
						case ObjectID.BLAST_FURNACE_PIPES2_BROKEN:
							westPipe = ComponentState.BROKEN;
							break;
						case ObjectID.BLAST_FURNACE_PIPES6:
							eastPipe = ComponentState.OPERATIONAL;
							break;
						case ObjectID.BLAST_FURNACE_PIPES6_BROKEN:
							eastPipe = ComponentState.BROKEN;
							break;
						default:
							break;
					}
				}
			}
		}
		return new MachineryState(stove, driveBelt, cogs, westPipe, eastPipe);
	}

	private static <K extends Enum<K>> void putNonZero(Map<K, Integer> counts, K key, int count)
	{
		if (count != 0)
		{
			counts.put(key, count);
		}
	}

	/** Bar-dispenser state. */
	public enum DispenserState
	{
		UNKNOWN(-1),
		EMPTY(0),
		PROCESSING(1),
		HOT(2),
		COOLED(3);

		private final int value;

		DispenserState(int value)
		{
			this.value = value;
		}

		private static DispenserState fromValue(int value)
		{
			for (DispenserState state : values())
			{
				if (state.value == value)
				{
					return state;
				}
			}
			return UNKNOWN;
		}
	}

	/** Observable fuel level of the manually operated stove. */
	public enum StoveState
	{
		UNKNOWN,
		LOW,
		MEDIUM,
		FULL
	}

	/** Observable condition of a repairable furnace component. */
	public enum ComponentState
	{
		UNKNOWN,
		OPERATIONAL,
		BROKEN
	}

	/** Current local-player operation of manual furnace machinery. */
	public enum PlayerOperation
	{
		NONE,
		PUMPING,
		PEDALING;

		private static PlayerOperation fromAnimation(int animation)
		{
			if (animation == AnimationID.BLAST_FURNACE_HUMAN_AIR_PUMP)
			{
				return PUMPING;
			}
			if (animation == AnimationID.BLAST_FURNACE_HUMAN_CONVEYER_CYCLE)
			{
				return PEDALING;
			}
			return NONE;
		}
	}

	/** Observable state of the manually operated furnace machinery. */
	@Getter
	public static final class MachineryState
	{
		private static final MachineryState UNKNOWN = new MachineryState(
			StoveState.UNKNOWN,
			ComponentState.UNKNOWN,
			ComponentState.UNKNOWN,
			ComponentState.UNKNOWN,
			ComponentState.UNKNOWN);

		/** @return stove fuel level, or unknown when its scene object is unavailable */
		private final StoveState stove;
		/** @return drive-belt condition */
		private final ComponentState driveBelt;
		/** @return cog condition */
		private final ComponentState cogs;
		/** @return western pipe condition */
		private final ComponentState westPipe;
		/** @return eastern pipe condition */
		private final ComponentState eastPipe;

		private MachineryState(StoveState stove, ComponentState driveBelt,
			ComponentState cogs, ComponentState westPipe, ComponentState eastPipe)
		{
			this.stove = stove;
			this.driveBelt = driveBelt;
			this.cogs = cogs;
			this.westPipe = westPipe;
			this.eastPipe = eastPipe;
		}

		/** @return whether any loaded repairable component is broken */
		public boolean hasBrokenComponent()
		{
			return driveBelt == ComponentState.BROKEN
				|| cogs == ComponentState.BROKEN
				|| westPipe == ComponentState.BROKEN
				|| eastPipe == ComponentState.BROKEN;
		}
	}

	/** Materials currently held by the furnace. */
	public enum Material
	{
		COAL(VarbitID.BLAST_FURNACE_COAL),
		COPPER_ORE(VarbitID.BLAST_FURNACE_COPPER_ORE),
		TIN_ORE(VarbitID.BLAST_FURNACE_TIN_ORE),
		IRON_ORE(VarbitID.BLAST_FURNACE_IRON_ORE),
		SILVER_ORE(VarbitID.BLAST_FURNACE_SILVER_ORE),
		GOLD_ORE(VarbitID.BLAST_FURNACE_GOLD_ORE),
		PERFECT_GOLD_ORE(VarbitID.BLAST_FURNACE_PERFECT_GOLD_ORE),
		MITHRIL_ORE(VarbitID.BLAST_FURNACE_MITHRIL_ORE),
		ADAMANTITE_ORE(VarbitID.BLAST_FURNACE_ADAMANTITE_ORE),
		RUNITE_ORE(VarbitID.BLAST_FURNACE_RUNITE_ORE),
		LEAD_ORE(VarbitID.BLAST_FURNACE_LEAD_ORE),
		NICKEL_ORE(VarbitID.BLAST_FURNACE_NICKEL_ORE),
		SMITHING_CATALYST(VarbitID.BLAST_FURNACE_CATALYST);

		private final int varbit;

		Material(int varbit)
		{
			this.varbit = varbit;
		}
	}

	/** Bars currently held by the dispenser. */
	public enum Bar
	{
		BRONZE(VarbitID.BLAST_FURNACE_BRONZE_BARS),
		IRON(VarbitID.BLAST_FURNACE_IRON_BARS),
		STEEL(VarbitID.BLAST_FURNACE_STEEL_BARS),
		SILVER(VarbitID.BLAST_FURNACE_SILVER_BARS),
		GOLD(VarbitID.BLAST_FURNACE_GOLD_BARS),
		PERFECT_GOLD(VarbitID.BLAST_FURNACE_PERFECT_GOLD_BARS),
		MITHRIL(VarbitID.BLAST_FURNACE_MITHRIL_BARS),
		ADAMANTITE(VarbitID.BLAST_FURNACE_ADAMANTITE_BARS),
		RUNITE(VarbitID.BLAST_FURNACE_RUNITE_BARS),
		LEAD(VarbitID.BLAST_FURNACE_LEAD_BARS),
		CUPRONICKEL(VarbitID.BLAST_FURNACE_CUPRONICKEL_BARS);

		private final int varbit;

		Bar(int varbit)
		{
			this.varbit = varbit;
		}
	}

	/** Immutable Blast Furnace state captured from one client read. */
	@Getter
	public static final class State
	{
		/** @return raw dispenser varbit value */
		private final int dispenserValue;
		/** @return current dispenser state */
		private final DispenserState dispenserState;
		/** @return current temperature-gauge reading */
		private final int temperature;
		/** @return whether the readings report a broken pipe */
		private final boolean brokenPipeWarning;
		/** @return whether the readings report low stove fuel */
		private final boolean fuelLowWarning;
		/** @return coal still required for the current furnace contents */
		private final int coalRequired;
		/** @return whether the coffer's scene morph reports that it contains coins */
		private final boolean cofferHasCoins;
		/** @return coffer balance in coins */
		private final int cofferCoins;
		/** @return whether the account has visited the Blast Furnace */
		private final boolean visited;
		/** @return whether the official Blast Furnace helper is disabled */
		private final boolean helperDisabled;
		/** @return whether official Blast Furnace highlights are disabled */
		private final boolean highlightsDisabled;
		/** @return immutable map containing nonzero stored-material counts */
		private final Map<Material, Integer> materials;
		/** @return immutable map containing nonzero stored-bar counts */
		private final Map<Bar, Integer> bars;
		/** @return whether ice-capable gloves are currently equipped */
		private final boolean coolingGlovesEquipped;
		/** @return whether the local player is in the Blast Furnace region */
		private final boolean atBlastFurnace;
		/** @return observable manual-machinery state */
		private final MachineryState machinery;
		/** @return current local-player manual operation */
		private final PlayerOperation playerOperation;

		private State(
			int dispenserValue,
			DispenserState dispenserState,
			int temperature,
			boolean brokenPipeWarning,
			boolean fuelLowWarning,
			int coalRequired,
			boolean cofferHasCoins,
			int cofferCoins,
			boolean visited,
			boolean helperDisabled,
			boolean highlightsDisabled,
			EnumMap<Material, Integer> materials,
			EnumMap<Bar, Integer> bars,
			boolean coolingGlovesEquipped,
			boolean atBlastFurnace,
			MachineryState machinery,
			PlayerOperation playerOperation)
		{
			this.dispenserValue = dispenserValue;
			this.dispenserState = dispenserState;
			this.temperature = temperature;
			this.brokenPipeWarning = brokenPipeWarning;
			this.fuelLowWarning = fuelLowWarning;
			this.coalRequired = coalRequired;
			this.cofferHasCoins = cofferHasCoins;
			this.cofferCoins = cofferCoins;
			this.visited = visited;
			this.helperDisabled = helperDisabled;
			this.highlightsDisabled = highlightsDisabled;
			this.materials = Collections.unmodifiableMap(materials);
			this.bars = Collections.unmodifiableMap(bars);
			this.coolingGlovesEquipped = coolingGlovesEquipped;
			this.atBlastFurnace = atBlastFurnace;
			this.machinery = machinery;
			this.playerOperation = playerOperation;
		}

		/**
		 * Returns a stored-material count.
		 *
		 * @param material material to query
		 * @return stored quantity, or zero
		 */
		public int getMaterialCount(Material material)
		{
			return materials.getOrDefault(material, 0);
		}

		/**
		 * Returns a stored-bar count.
		 *
		 * @param bar bar to query
		 * @return stored quantity, or zero
		 */
		public int getBarCount(Bar bar)
		{
			return bars.getOrDefault(bar, 0);
		}

		/** @return total number of stored materials */
		public int getTotalMaterialCount()
		{
			return materials.values().stream().mapToInt(Integer::intValue).sum();
		}

		/** @return total number of stored bars */
		public int getTotalBarCount()
		{
			return bars.values().stream().mapToInt(Integer::intValue).sum();
		}

		/** @return whether the conveyor/dispenser is processing a batch */
		public boolean isProcessing()
		{
			return dispenserState == DispenserState.PROCESSING;
		}

		/** @return whether hot bars cannot currently be collected */
		public boolean needsCooling()
		{
			return dispenserState == DispenserState.HOT && !coolingGlovesEquipped;
		}

		/** @return whether bars are currently ready to collect */
		public boolean isReadyToCollect()
		{
			return dispenserState == DispenserState.COOLED
				|| dispenserState == DispenserState.HOT && coolingGlovesEquipped;
		}

	}
}
