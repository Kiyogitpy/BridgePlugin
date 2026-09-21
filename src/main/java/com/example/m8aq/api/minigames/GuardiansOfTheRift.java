package com.example.m8aq.api.minigames;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Queue;
import java.util.Set;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.CollisionData;
import net.runelite.api.CollisionDataFlag;
import net.runelite.api.GameObject;
import net.runelite.api.GroundObject;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.NPC;
import net.runelite.api.Player;
import net.runelite.api.Point;
import net.runelite.api.Quest;
import net.runelite.api.Tile;
import net.runelite.api.WorldView;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.gameval.NpcID;
import net.runelite.api.gameval.ObjectID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.widgets.Widget;

/** Provides a read-only snapshot of the player-visible Guardians of the Rift state. */
public final class GuardiansOfTheRift
{
	private static final int MAIN_TEMPLE_REGION_ID = 14484;
	private static final int ENTRY_BARRIER_Y = 9483;
	private static final int REWARD_ENERGY_REQUIREMENT = 300;
	private static final int[][] DIRECTIONS = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
	private static final int[] BLOCKED_FROM = {
		CollisionDataFlag.BLOCK_MOVEMENT_EAST,
		CollisionDataFlag.BLOCK_MOVEMENT_WEST,
		CollisionDataFlag.BLOCK_MOVEMENT_NORTH,
		CollisionDataFlag.BLOCK_MOVEMENT_SOUTH
	};

	private GuardiansOfTheRift()
	{
	}

	/**
	 * Reads the current Guardians of the Rift state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");

		Widget hud = client.getWidget(InterfaceID.GotrHud.UNIVERSE);
		boolean hudVisible = isVisible(hud);
		Widget guardianPowerWidget = hudVisible
			? client.getWidget(InterfaceID.GotrHud.ENERGY_TITLE) : null;
		Widget altarRotationWidget = hudVisible
			? client.getWidget(InterfaceID.GotrHud.PORTAL_TIME) : null;
		Widget elementalPortalWidget = hudVisible
			? client.getWidget(InterfaceID.GotrHud.ELEMENTAL_PORTAL) : null;
		Widget catalyticPortalWidget = hudVisible
			? client.getWidget(InterfaceID.GotrHud.CATALYTIC_PORTAL) : null;
		Widget guardianLimitWidget = hudVisible
			? client.getWidget(InterfaceID.GotrHud.GUARDIAN_LIMIT) : null;
		int[] guardians = hudVisible
			? readPair(guardianLimitWidget)
			: new int[]{-1, -1};
		boolean guardianEssencePortalOpen = hudVisible
			&& isVisible(client.getWidget(InterfaceID.GotrHud.PORTAL_SHORTCUT_LAYER));
		String guardianEssencePortalStatus = guardianEssencePortalOpen
			? text(client.getWidget(InterfaceID.GotrHud.PORTAL_POSITION))
			: "";
		Player player = client.getLocalPlayer();
		WorldPoint location = player == null ? null : player.getWorldLocation();
		boolean inMainTemple = location != null
			&& location.getRegionID() == MAIN_TEMPLE_REGION_ID;
		InventoryState inventory = InventoryState.fromContainer(
			client.getItemContainer(InventoryID.INV));
		WorldView worldView = client.getTopLevelWorldView();
		Set<Barrier> barriers = readBarriers(worldView);
		SceneState scene = SceneState.fromWorldView(worldView);
		String guardianPowerText = hudVisible ? text(guardianPowerWidget) : "";
		String altarRotationText = hudVisible ? text(altarRotationWidget) : "";

		return new State(
			hudVisible,
			client.getVarbitValue(VarbitID.GOTR_IS_PLAYING) == 1,
			client.getVarbitValue(VarbitID.GOTR_PLAYER_HAS_FINISHED_GAME) == 1,
			client.getVarbitValue(VarbitID.GOTR_BANNED) == 1,
			hudVisible ? readFirstInt(guardianPowerWidget) : -1,
			guardianPowerText,
			hudVisible ? readDurationSeconds(altarRotationWidget) : -1,
			altarRotationText,
			client.getVarbitValue(VarbitID.GOTR_ELEMENTAL_EARNED_THIS_GAME),
			client.getVarbitValue(VarbitID.GOTR_CATALYTIC_EARNED_THIS_GAME),
			hudVisible ? text(client.getWidget(InterfaceID.GotrHud.ELEMENTAL_ENERGY_TEXT)) : "",
			hudVisible ? text(client.getWidget(InterfaceID.GotrHud.CATALYTIC_ENERGY_TEXT)) : "",
			hudVisible ? Altar.fromWidget(elementalPortalWidget, false) : Altar.UNKNOWN,
			hudVisible ? Altar.fromWidget(catalyticPortalWidget, true) : Altar.UNKNOWN,
			elementalPortalWidget == null ? -1 : elementalPortalWidget.getSpriteId(),
			catalyticPortalWidget == null ? -1 : catalyticPortalWidget.getSpriteId(),
			guardians[0],
			guardians[1],
			hudVisible ? text(guardianLimitWidget) : "",
			guardianEssencePortalOpen,
			client.getVarbitValue(VarbitID.GOTR_PORTAL_BOTTOM_VISIBLE) == 1,
			guardianEssencePortalStatus,
			readPortalPosition(guardianEssencePortalStatus),
			readPortalSeconds(guardianEssencePortalStatus),
			inMainTemple,
			inMainTemple && location.getY() > ENTRY_BARRIER_Y,
			hudVisible && location != null
				? Altar.fromRegion(location.getRegionID())
				: Altar.UNKNOWN,
			inMainTemple ? MiningArea.fromClient(client, location) : MiningArea.NONE,
			client.getVarbitValue(VarbitID.GOTR_MINING_COUNT),
			client.getVarpValue(VarPlayerID.GOTR_TEMP_0),
			PersistentState.fromClient(client),
			SettingsState.fromClient(client),
			inventory,
			barriers,
			scene);
	}

	private static Set<Barrier> readBarriers(WorldView worldView)
	{
		if (worldView == null || worldView.getScene() == null)
		{
			return Collections.emptySet();
		}
		int plane = worldView.getPlane();
		Tile[][][] sceneTiles = worldView.getScene().getTiles();
		if (plane < 0 || plane >= sceneTiles.length || sceneTiles[plane] == null)
		{
			return Collections.emptySet();
		}

		Set<Barrier> barriers = new LinkedHashSet<>();
		for (Tile[] column : sceneTiles[plane])
		{
			if (column == null)
			{
				continue;
			}
			for (Tile tile : column)
			{
				GroundObject object = tile == null ? null : tile.getGroundObject();
				BarrierState state = object == null
					? null
					: BarrierState.fromObjectId(object.getId());
				if (state != null)
				{
					barriers.add(new Barrier(object.getWorldLocation(), state));
				}
			}
		}
		return Collections.unmodifiableSet(barriers);
	}

	private static Set<WorldPoint> readMiningAreaTiles(Client client, MiningArea area)
	{
		Objects.requireNonNull(client, "client");
		Objects.requireNonNull(area, "area");
		if (area == MiningArea.NONE)
		{
			return Collections.emptySet();
		}

		WorldView worldView = client.getTopLevelWorldView();
		if (worldView == null || worldView.getScene() == null)
		{
			return Collections.emptySet();
		}
		int plane = worldView.getPlane();
		CollisionData[] collisionMaps = worldView.getCollisionMaps();
		Tile[][][] sceneTiles = worldView.getScene().getTiles();
		if (collisionMaps == null || plane < 0 || plane >= collisionMaps.length
			|| collisionMaps[plane] == null || plane >= sceneTiles.length)
		{
			return Collections.emptySet();
		}

		int[][] flags = collisionMaps[plane].getFlags();
		Set<Integer> seeds = findPerimeterSeeds(sceneTiles[plane], flags, area.objectId);
		Set<WorldPoint> result = new HashSet<>();
		for (int tile : flood(flags, seeds))
		{
			result.add(WorldPoint.fromScene(worldView, tile >>> 16, tile & 0xffff, plane));
		}
		return Collections.unmodifiableSet(result);
	}

	private static Set<Integer> findPerimeterSeeds(Tile[][] tiles, int[][] flags, int objectId)
	{
		Set<Integer> seeds = new HashSet<>();
		for (Tile[] column : tiles)
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
					if (object != null && object.getId() == objectId)
					{
						addPerimeterSeeds(seeds, flags,
							object.getSceneMinLocation(), object.getSceneMaxLocation());
					}
				}
			}
		}
		return seeds;
	}

	private static void addPerimeterSeeds(Set<Integer> seeds, int[][] flags,
		Point minimum, Point maximum)
	{
		for (int x = minimum.getX() - 1; x <= maximum.getX() + 1; x++)
		{
			for (int y = minimum.getY() - 1; y <= maximum.getY() + 1; y++)
			{
				if ((x == minimum.getX() - 1 || x == maximum.getX() + 1
					|| y == minimum.getY() - 1 || y == maximum.getY() + 1)
					&& isOpen(flags, x, y))
				{
					seeds.add(pack(x, y));
				}
			}
		}
	}

	private static Set<Integer> flood(int[][] flags, Set<Integer> seeds)
	{
		Set<Integer> visited = new HashSet<>();
		Queue<Integer> pending = new ArrayDeque<>();
		for (int seed : seeds)
		{
			int x = seed >>> 16;
			int y = seed & 0xffff;
			if (isOpen(flags, x, y) && visited.add(seed))
			{
				pending.add(seed);
			}
		}

		while (!pending.isEmpty())
		{
			int tile = pending.remove();
			int x = tile >>> 16;
			int y = tile & 0xffff;
			for (int i = 0; i < DIRECTIONS.length; i++)
			{
				int nextX = x + DIRECTIONS[i][0];
				int nextY = y + DIRECTIONS[i][1];
				int next = pack(nextX, nextY);
				if (isOpen(flags, nextX, nextY)
					&& (flags[nextX][nextY] & BLOCKED_FROM[i]) == 0
					&& visited.add(next))
				{
					pending.add(next);
				}
			}
		}
		return visited;
	}

	private static int pack(int x, int y)
	{
		return x << 16 | y & 0xffff;
	}

	private static boolean isOpen(int[][] flags, int x, int y)
	{
		return x >= 0 && x < flags.length && y >= 0 && y < flags[x].length
			&& (flags[x][y] & CollisionDataFlag.BLOCK_MOVEMENT_FULL) == 0;
	}

	private static boolean isVisible(Widget widget)
	{
		return widget != null && !widget.isHidden();
	}

	private static String text(Widget widget)
	{
		return widget == null || widget.getText() == null ? "" : widget.getText();
	}

	private static int readFirstInt(Widget widget)
	{
		String value = text(widget);
		int result = 0;
		boolean found = false;
		for (int i = 0; i < value.length(); i++)
		{
			char character = value.charAt(i);
			if (Character.isDigit(character))
			{
				found = true;
				result = result * 10 + character - '0';
			}
			else if (found && character != ',')
			{
				break;
			}
		}
		return found ? result : -1;
	}

	private static int[] readPair(Widget widget)
	{
		String[] values = text(widget).split("/", -1);
		if (values.length != 2)
		{
			return new int[]{-1, -1};
		}

		try
		{
			return new int[]{Integer.parseInt(values[0].trim()), Integer.parseInt(values[1].trim())};
		}
		catch (NumberFormatException ex)
		{
			return new int[]{-1, -1};
		}
	}

	private static int readDurationSeconds(Widget widget)
	{
		return isVisible(widget) ? readDurationSeconds(text(widget)) : -1;
	}

	private static int readDurationSeconds(String text)
	{
		String value = text.trim();
		int separator = value.indexOf(':');
		if (separator <= 0 || separator != value.lastIndexOf(':'))
		{
			return -1;
		}

		try
		{
			int minutes = Integer.parseInt(value.substring(0, separator));
			int seconds = Integer.parseInt(value.substring(separator + 1));
			return minutes >= 0 && seconds >= 0 && seconds < 60
				&& minutes <= (Integer.MAX_VALUE - seconds) / 60
				? minutes * 60 + seconds
				: -1;
		}
		catch (NumberFormatException ex)
		{
			return -1;
		}
	}

	private static String readPortalPosition(String status)
	{
		int separator = status.lastIndexOf(" - ");
		return (separator < 0 ? status : status.substring(0, separator)).trim();
	}

	private static int readPortalSeconds(String status)
	{
		int separator = status.lastIndexOf(" - ");
		return separator < 0 ? -1 : readDurationSeconds(status.substring(separator + 3));
	}

	/** Separated mining pocket occupied by the player. */
	public enum MiningArea
	{
		NONE(-1),
		EAST_LARGE_REMAINS(ObjectID.GOTR_ESSENCE_TIER_2),
		WEST_HUGE_REMAINS(ObjectID.GOTR_ESSENCE_TIER_3);

		private final int objectId;

		MiningArea(int objectId)
		{
			this.objectId = objectId;
		}

		private static MiningArea fromClient(Client client, WorldPoint location)
		{
			Set<WorldPoint> east = readMiningAreaTiles(client, EAST_LARGE_REMAINS);
			Set<WorldPoint> west = readMiningAreaTiles(client, WEST_HUGE_REMAINS);
			return fromTiles(location, east, west);
		}

		private static MiningArea fromTiles(WorldPoint location, Set<WorldPoint> east, Set<WorldPoint> west)
		{
			if (east.contains(location))
			{
				return EAST_LARGE_REMAINS;
			}
			return west.contains(location) ? WEST_HUGE_REMAINS : NONE;
		}
	}

	/** Charged-cell strength produced by a rune altar. */
	public enum CellTier
	{
		NONE(-1),
		WEAK(ItemID.GOTR_CELL_TIER1),
		MEDIUM(ItemID.GOTR_CELL_TIER2),
		STRONG(ItemID.GOTR_CELL_TIER3),
		OVERCHARGED(ItemID.GOTR_CELL_TIER4);

		/** @return charged-cell item ID, or {@code -1} for {@link #NONE} */
		@Getter
		private final int itemId;

		CellTier(int itemId)
		{
			this.itemId = itemId;
		}

		private static CellTier fromInventory(ItemContainer inventory)
		{
			if (inventory != null)
			{
				for (CellTier tier : values())
				{
					if (tier != NONE && inventory.contains(tier.itemId))
					{
						return tier;
					}
				}
			}
			return NONE;
		}
	}

	/** Client-visible condition and available interaction of a barrier cell tile. */
	public enum BarrierState
	{
		BROKEN_REPAIRABLE(ObjectID.GOTR_CELL_TILE_BROKEN_REPAIRABLE, true, true, false),
		BROKEN(ObjectID.GOTR_CELL_TILE_BROKEN, true, false, false),
		INACTIVE_NO_OP(ObjectID.GOTR_CELL_TILE_INACTIVE_NOOP, false, false, false),
		INACTIVE(ObjectID.GOTR_CELL_TILE_INACTIVE, false, false, true),
		WEAK(ObjectID.GOTR_CELL_TILE_TIER1, false, false, true),
		MEDIUM(ObjectID.GOTR_CELL_TILE_TIER2, false, false, true),
		STRONG(ObjectID.GOTR_CELL_TILE_TIER3, false, false, true),
		OVERCHARGED(ObjectID.GOTR_CELL_TILE_TIER4, false, false, true);

		/** @return cell-tile ground-object ID */
		@Getter
		private final int objectId;
		/** @return whether the barrier is broken */
		@Getter
		private final boolean broken;
		/** @return whether the cell tile currently offers the Repair interaction */
		@Getter
		private final boolean repairable;
		private final boolean acceptsCell;

		BarrierState(int objectId, boolean broken, boolean repairable, boolean acceptsCell)
		{
			this.objectId = objectId;
			this.broken = broken;
			this.repairable = repairable;
			this.acceptsCell = acceptsCell;
		}

		/** @return whether the cell tile currently offers the Place-cell interaction */
		public boolean acceptsCell()
		{
			return acceptsCell;
		}

		private static BarrierState fromObjectId(int objectId)
		{
			for (BarrierState state : values())
			{
				if (state.objectId == objectId)
				{
					return state;
				}
			}
			return null;
		}
	}

	/** Immutable state for one currently loaded barrier cell tile. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Barrier
	{
		/** @return barrier cell-tile location */
		private final WorldPoint location;
		/** @return client-visible barrier state */
		private final BarrierState state;
	}

	/** Rift guardian assembled from one charged cell. */
	@Getter
	@RequiredArgsConstructor
	public enum RiftGuardianType
	{
		WEAK_CATALYTIC(NpcID.GOTR_GUARDIAN_MIND, true, CellTier.WEAK),
		MEDIUM_CATALYTIC(NpcID.GOTR_GUARDIAN_CHAOS, true, CellTier.MEDIUM),
		STRONG_CATALYTIC(NpcID.GOTR_GUARDIAN_DEATH, true, CellTier.STRONG),
		OVERCHARGED_CATALYTIC(NpcID.GOTR_GUARDIAN_BLOOD, true, CellTier.OVERCHARGED),
		WEAK_ELEMENTAL(NpcID.GOTR_GUARDIAN_AIR, false, CellTier.WEAK),
		MEDIUM_ELEMENTAL(NpcID.GOTR_GUARDIAN_WATER, false, CellTier.MEDIUM),
		STRONG_ELEMENTAL(NpcID.GOTR_GUARDIAN_EARTH, false, CellTier.STRONG),
		OVERCHARGED_ELEMENTAL(NpcID.GOTR_GUARDIAN_FIRE, false, CellTier.OVERCHARGED);

		/** @return NPC ID */
		private final int npcId;
		/** @return whether the guardian is catalytic rather than elemental */
		private final boolean catalytic;
		/** @return charged-cell tier used to assemble the guardian */
		private final CellTier cellTier;

		private static RiftGuardianType fromNpcId(int npcId)
		{
			for (RiftGuardianType type : values())
			{
				if (type.npcId == npcId)
				{
					return type;
				}
			}
			return null;
		}
	}

	/** Immutable state for one currently loaded rift guardian. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class RiftGuardian
	{
		/** @return RuneLite NPC index */
		private final int npcIndex;
		/** @return guardian strength and elemental/catalytic type */
		private final RiftGuardianType type;
		/** @return current world location */
		private final WorldPoint location;
	}

	/** Abyssal creature attacking the Great Guardian. */
	@Getter
	@RequiredArgsConstructor
	public enum AbyssalCreatureType
	{
		PYRAMID(NpcID.GOTR_ABYSSAL_PYRAMID),
		WALKER(NpcID.GOTR_ABYSSAL_WALKER),
		LEECH(NpcID.GOTR_ABYSSAL_LEECH);

		/** @return NPC ID */
		private final int npcId;

		private static AbyssalCreatureType fromNpcId(int npcId)
		{
			for (AbyssalCreatureType type : values())
			{
				if (type.npcId == npcId)
				{
					return type;
				}
			}
			return null;
		}
	}

	/** Immutable state for one currently loaded Abyssal creature. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class AbyssalCreature
	{
		/** @return RuneLite NPC index */
		private final int npcIndex;
		/** @return creature type */
		private final AbyssalCreatureType type;
		/** @return current world location */
		private final WorldPoint location;
		/** @return current client health-bar ratio, or {@code -1} when no bar is visible */
		private final int healthRatio;
		/** @return current client health-bar scale, or {@code -1} when no bar is visible */
		private final int healthScale;
	}

	/** Strength variant of a live guardian barrier NPC. */
	@Getter
	@RequiredArgsConstructor
	public enum BarrierEntityType
	{
		WEAK(NpcID.GOTR_BARRIER_NPC_TIER1, NpcID.GOTR_BARRIER_NPC_TIER1_LARGE,
			CellTier.WEAK, 50),
		MEDIUM(NpcID.GOTR_BARRIER_NPC_TIER2, NpcID.GOTR_BARRIER_NPC_TIER2_LARGE,
			CellTier.MEDIUM, 100),
		STRONG(NpcID.GOTR_BARRIER_NPC_TIER3, NpcID.GOTR_BARRIER_NPC_TIER3_LARGE,
			CellTier.STRONG, 200),
		OVERCHARGED(NpcID.GOTR_BARRIER_NPC_TIER4, NpcID.GOTR_BARRIER_NPC_TIER4_LARGE,
			CellTier.OVERCHARGED, 400);

		/** @return regular barrier NPC ID */
		private final int npcId;
		/** @return large barrier NPC ID */
		private final int largeNpcId;
		/** @return barrier cell tier */
		private final CellTier cellTier;
		/** @return cache-defined maximum hitpoints */
		private final int maximumHitpoints;

		private static BarrierEntityType fromNpcId(int npcId)
		{
			for (BarrierEntityType type : values())
			{
				if (type.npcId == npcId || type.largeNpcId == npcId)
				{
					return type;
				}
			}
			return null;
		}
	}

	/** Immutable state for one currently loaded guardian barrier NPC. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class BarrierEntity
	{
		/** @return RuneLite NPC index */
		private final int npcIndex;
		/** @return barrier strength and maximum hitpoints */
		private final BarrierEntityType type;
		/** @return whether this is the large three-tile barrier variant */
		private final boolean large;
		/** @return current world location */
		private final WorldPoint location;
		/** @return current client health-bar ratio, or {@code -1} when no bar is visible */
		private final int healthRatio;
		/** @return current client health-bar scale, or {@code -1} when no bar is visible */
		private final int healthScale;
	}

	/** Immutable classified NPC state from the currently loaded GOTR scene. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class SceneState
	{
		private static final SceneState EMPTY = new SceneState(false,
			Collections.emptyList(), Collections.emptyList(), Collections.emptyList());

		/** @return whether a top-level scene was available */
		private final boolean loaded;
		/** @return immutable list of loaded rift guardians */
		private final List<RiftGuardian> riftGuardians;
		/** @return immutable list of loaded barrier NPCs */
		private final List<BarrierEntity> barrierEntities;
		/** @return immutable list of loaded Abyssal creatures */
		private final List<AbyssalCreature> abyssalCreatures;

		private static SceneState fromWorldView(WorldView worldView)
		{
			if (worldView == null || worldView.getScene() == null)
			{
				return EMPTY;
			}

			List<RiftGuardian> guardians = new ArrayList<>();
			List<BarrierEntity> barrierEntities = new ArrayList<>();
			List<AbyssalCreature> creatures = new ArrayList<>();
			Iterable<? extends NPC> npcs = worldView.npcs();
			if (npcs != null)
			{
				for (NPC npc : npcs)
				{
					if (npc == null)
					{
						continue;
					}

					RiftGuardianType guardianType = RiftGuardianType.fromNpcId(npc.getId());
					if (guardianType != null)
					{
						guardians.add(new RiftGuardian(npc.getIndex(), guardianType,
							npc.getWorldLocation()));
						continue;
					}

					BarrierEntityType barrierType = BarrierEntityType.fromNpcId(npc.getId());
					if (barrierType != null)
					{
						barrierEntities.add(new BarrierEntity(npc.getIndex(), barrierType,
							npc.getId() == barrierType.largeNpcId, npc.getWorldLocation(),
							npc.getHealthRatio(), npc.getHealthScale()));
						continue;
					}

					AbyssalCreatureType creatureType = AbyssalCreatureType.fromNpcId(npc.getId());
					if (creatureType != null)
					{
						creatures.add(new AbyssalCreature(npc.getIndex(), creatureType,
							npc.getWorldLocation(), npc.getHealthRatio(), npc.getHealthScale()));
					}
				}
			}

			return new SceneState(true,
				Collections.unmodifiableList(guardians),
				Collections.unmodifiableList(barrierEntities),
				Collections.unmodifiableList(creatures));
		}
	}

	/** Immutable permanent and cross-round GOTR state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class PersistentState
	{
		/** @return total successfully closed rifts */
		private final int completedRifts;
		/** @return opaque server game identifier assigned to the player, or zero outside a game */
		private final int gameId;
		/** @return raw packed GOTR permanent-reward varp */
		private final int rawRewardState;
		/** @return raw otherwise-undocumented permanent GOTR varp */
		private final int rawPermanentState;
		/** @return whether the Abyssal needle has been unlocked */
		private final boolean needleUnlocked;
		/** @return whether the Guardians of the Rift book has been obtained */
		private final boolean bookObtained;
		/** @return whether the tarnished locket has been obtained */
		private final boolean locketObtained;
		/** @return whether the tarnished locket has been opened */
		private final boolean locketOpened;
		/** @return whether the Amulet of the Eye has been obtained */
		private final boolean amuletOfTheEyeObtained;
		/** @return whether the lost bag has been obtained */
		private final boolean lostBagObtained;
		/** @return raw lost-bag colour value */
		private final int lostBagColour;
		/** @return raw Amulet of the Eye colour value */
		private final int amuletOfTheEyeColour;
		/** @return whether the GOTR collection-log completion flag is set */
		private final boolean collectionLogCompleted;
		/** @return raw Lumbridge Guide GOTR dialogue state */
		private final int lumbridgeGuideChatState;
		/** @return whether Apprentice Tamara exposes the quick-lock option */
		private final boolean tamaraQuickLockEnabled;
		/** @return whether Apprentice Cordelia exposes the pouch-repair option */
		private final boolean cordeliaPouchRepairEnabled;
		/** @return whether the GOTR variant of the Runecraft pet is unlocked */
		private final boolean runecraftPetGotrUnlocked;

		private static PersistentState fromClient(Client client)
		{
			return new PersistentState(
				client.getVarpValue(VarPlayerID.TOTAL_GOTR_KILLS),
				client.getVarpValue(VarPlayerID.GOTR_GAME_ID_ON_PLAYER),
				client.getVarpValue(VarPlayerID.GOTR_REWARDS_PERM),
				client.getVarpValue(VarPlayerID.GOTR_PERM_0),
				client.getVarbitValue(VarbitID.GOTR_UNLOCKED_NEEDLE) == 1,
				client.getVarbitValue(VarbitID.GOTR_BOOK_OBTAINED) == 1,
				client.getVarbitValue(VarbitID.GOTR_LOCKET_OBTAINED) == 1,
				client.getVarbitValue(VarbitID.GOTR_LOCKET_OPENED) == 1,
				client.getVarbitValue(VarbitID.GOTR_AMULET_OF_THE_EYE_OBTAINED) == 1,
				client.getVarbitValue(VarbitID.GOTR_BAG_OBTAINED) == 1,
				client.getVarbitValue(VarbitID.GOTR_BAG_COLOUR),
				client.getVarbitValue(VarbitID.GOTR_AMULET_COLOUR),
				client.getVarbitValue(VarbitID.COLLECTION_MINIGAMES_GOTR_COMPLETED) == 1,
				client.getVarbitValue(VarbitID.GOTR_LUMBRIDGE_GUIDE_CHAT),
				client.getVarbitValue(VarbitID.GOTR_TAMARA_LOCK_OP) == 1,
				client.getVarbitValue(VarbitID.GOTR_CORDELIA_REPAIR_POUCH) == 1,
				client.getVarbitValue(VarbitID.SKILLPET_RUNECRAFTING_GOTR_UNLOCKED) == 1);
		}
	}

	/** Immutable player-configured GOTR warning and talisman-drop settings. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class SettingsState
	{
		/** @return raw essence-warning varbit value */
		private final int essenceWarningValue;
		/** @return whether the guardian-fragment warning is disabled */
		private final boolean fragmentWarningDisabled;
		/** @return immutable per-altar map; true means that portal-talisman drop is disabled */
		private final Map<Altar, Boolean> portalTalismanDropsDisabled;
		/** @return whether the colourful GOTR fade effect is disabled */
		private final boolean colourfulFadeDisabled;
		/** @return whether the colourful-fade prompt is disabled */
		private final boolean colourfulFadePromptDisabled;

		private static SettingsState fromClient(Client client)
		{
			EnumMap<Altar, Boolean> talismanSettings = new EnumMap<>(Altar.class);
			for (Altar altar : Altar.values())
			{
				if (altar.talismanToggleVarbitId != -1)
				{
					talismanSettings.put(altar,
						client.getVarbitValue(altar.talismanToggleVarbitId) == 1);
				}
			}

			return new SettingsState(
				client.getVarbitValue(VarbitID.GOTR_ESSENCE_WARNING),
				client.getVarbitValue(VarbitID.GOTR_FRAGMENT_WARNING_OFF) == 1,
				Collections.unmodifiableMap(talismanSettings),
				client.getVarbitValue(VarbitID.GOTR_COLOURFUL_FADE_DISABLED) == 1,
				client.getVarbitValue(VarbitID.GOTR_COLOURFUL_FADE_PROMPT_DISABLED) == 1);
		}

		/**
		 * Returns whether portal-talisman drops are disabled for one altar.
		 *
		 * @param altar rune altar
		 * @return true when that talisman drop is disabled
		 */
		public boolean isPortalTalismanDropDisabled(Altar altar)
		{
			return portalTalismanDropsDisabled.getOrDefault(
				Objects.requireNonNull(altar, "altar"), false);
		}
	}

	/** Immutable GOTR-relevant inventory state. */
	@Getter
	public static final class InventoryState
	{
		/** @return whether the inventory container is present */
		private final boolean inventoryPresent;
		/** @return guardian fragments in inventory */
		private final int guardianFragments;
		/** @return guardian essence in inventory, excluding essence pouches */
		private final int guardianEssence;
		/** @return uncharged cells in inventory */
		private final int unchargedCells;
		/** @return charged-cell tier, or {@link CellTier#NONE} */
		private final CellTier chargedCellTier;
		/** @return immutable charged-cell counts keyed by tier */
		private final Map<CellTier, Integer> chargedCells;
		/** @return catalytic guardian stones in inventory */
		private final int catalyticGuardianStones;
		/** @return elemental guardian stones in inventory */
		private final int elementalGuardianStones;
		/** @return polyelemental guardian stones in inventory */
		private final int polyelementalGuardianStones;
		/** @return polycatalytic guardian stones in inventory */
		private final int polycatalyticGuardianStones;
		/** @return immutable portal-talisman counts keyed by altar */
		private final Map<Altar, Integer> portalTalismans;
		/** @return whether a chisel is in inventory */
		private final boolean chiselPresent;
		/** @return currently empty inventory slots, or zero when inventory is unavailable */
		private final int emptySlots;

		private InventoryState(
			boolean inventoryPresent,
			int guardianFragments,
			int guardianEssence,
			int unchargedCells,
			CellTier chargedCellTier,
			EnumMap<CellTier, Integer> chargedCells,
			int catalyticGuardianStones,
			int elementalGuardianStones,
			int polyelementalGuardianStones,
			int polycatalyticGuardianStones,
			EnumMap<Altar, Integer> portalTalismans,
			boolean chiselPresent,
			int emptySlots)
		{
			this.inventoryPresent = inventoryPresent;
			this.guardianFragments = guardianFragments;
			this.guardianEssence = guardianEssence;
			this.unchargedCells = unchargedCells;
			this.chargedCellTier = chargedCellTier;
			this.chargedCells = Collections.unmodifiableMap(chargedCells);
			this.catalyticGuardianStones = catalyticGuardianStones;
			this.elementalGuardianStones = elementalGuardianStones;
			this.polyelementalGuardianStones = polyelementalGuardianStones;
			this.polycatalyticGuardianStones = polycatalyticGuardianStones;
			this.portalTalismans = Collections.unmodifiableMap(portalTalismans);
			this.chiselPresent = chiselPresent;
			this.emptySlots = emptySlots;
		}

		private static InventoryState fromContainer(ItemContainer inventory)
		{
			EnumMap<Altar, Integer> talismans = new EnumMap<>(Altar.class);
			for (Altar altar : Altar.values())
			{
				if (altar != Altar.UNKNOWN)
				{
					talismans.put(altar, count(inventory, altar.portalTalismanItemId));
				}
			}
			EnumMap<CellTier, Integer> chargedCells = new EnumMap<>(CellTier.class);
			for (CellTier tier : CellTier.values())
			{
				if (tier != CellTier.NONE)
				{
					chargedCells.put(tier, count(inventory, tier.itemId));
				}
			}

			return new InventoryState(
				inventory != null,
				count(inventory, ItemID.GOTR_GUARDIAN_FRAGMENT),
				count(inventory, ItemID.GOTR_GUARDIAN_ESSENCE),
				count(inventory, ItemID.GOTR_CELL_UNCHARGED),
				CellTier.fromInventory(inventory),
				chargedCells,
				count(inventory, ItemID.GOTR_GUARDIAN_STONE_CATALYTIC),
				count(inventory, ItemID.GOTR_GUARDIAN_STONE_ELEMENTAL),
				count(inventory, ItemID.GOTR_GUARDIAN_STONE_POLYELEMENTAL),
				count(inventory, ItemID.GOTR_GUARDIAN_STONE_POLYCATALYTIC),
				talismans,
				inventory != null && inventory.contains(ItemID.CHISEL),
				countEmptySlots(inventory));
		}

		private static int countEmptySlots(ItemContainer inventory)
		{
			int emptySlots = 0;
			if (inventory != null)
			{
				for (Item item : inventory.getItems())
				{
					emptySlots += item.getId() == -1 ? 1 : 0;
				}
			}
			return emptySlots;
		}

		private static int count(ItemContainer inventory, int itemId)
		{
			return inventory == null ? 0 : inventory.count(itemId);
		}

		/**
		 * Returns the number of portal talismans held for one altar.
		 *
		 * @param altar rune altar
		 * @return portal-talisman count
		 */
		public int getPortalTalismanCount(Altar altar)
		{
			return portalTalismans.getOrDefault(Objects.requireNonNull(altar, "altar"), 0);
		}

		/**
		 * Returns the charged-cell count for one tier.
		 *
		 * @param tier charged-cell tier
		 * @return inventory count
		 */
		public int getChargedCellCount(CellTier tier)
		{
			return chargedCells.getOrDefault(Objects.requireNonNull(tier, "tier"), 0);
		}

		/** @return total charged cells across all tiers */
		public int getTotalChargedCells()
		{
			return chargedCells.values().stream().mapToInt(Integer::intValue).sum();
		}

		/** @return total guardian stones across all four variants */
		public int getTotalGuardianStones()
		{
			return catalyticGuardianStones + elementalGuardianStones
				+ polyelementalGuardianStones + polycatalyticGuardianStones;
		}

		/** @return total portal talismans across all altars */
		public int getTotalPortalTalismans()
		{
			return portalTalismans.values().stream().mapToInt(Integer::intValue).sum();
		}
	}

	/** Rune altar available in Guardians of the Rift. */
	public enum Altar
	{
		UNKNOWN(-1, false, -1, -1, CellTier.NONE, -1, null, -1, -1),
		AIR(4353, false, 11339, 1, CellTier.WEAK, ItemID.GOTR_PORTAL_TALISMAN_AIR, null,
			ObjectID.GOTR_PORTAL_AIR, VarbitID.GOTR_TALISMAN_TOGGLE_AIR),
		MIND(4354, true, 11083, 2, CellTier.WEAK, ItemID.GOTR_PORTAL_TALISMAN_MIND, null,
			ObjectID.GOTR_PORTAL_MIND, VarbitID.GOTR_TALISMAN_TOGGLE_MIND),
		WATER(4355, false, 10827, 5, CellTier.MEDIUM, ItemID.GOTR_PORTAL_TALISMAN_WATER, null,
			ObjectID.GOTR_PORTAL_WATER, VarbitID.GOTR_TALISMAN_TOGGLE_WATER),
		EARTH(4356, false, 10571, 9, CellTier.STRONG, ItemID.GOTR_PORTAL_TALISMAN_EARTH, null,
			ObjectID.GOTR_PORTAL_EARTH, VarbitID.GOTR_TALISMAN_TOGGLE_EARTH),
		FIRE(4357, false, 10315, 14, CellTier.OVERCHARGED, ItemID.GOTR_PORTAL_TALISMAN_FIRE, null,
			ObjectID.GOTR_PORTAL_FIRE, VarbitID.GOTR_TALISMAN_TOGGLE_FIRE),
		BODY(4358, true, 10059, 20, CellTier.WEAK, ItemID.GOTR_PORTAL_TALISMAN_BODY, null,
			ObjectID.GOTR_PORTAL_BODY, VarbitID.GOTR_TALISMAN_TOGGLE_BODY),
		COSMIC(4359, true, 8523, 27, CellTier.MEDIUM, ItemID.GOTR_PORTAL_TALISMAN_COSMIC,
			Quest.LOST_CITY, ObjectID.GOTR_PORTAL_COSMIC, VarbitID.GOTR_TALISMAN_TOGGLE_COSMIC),
		CHAOS(4360, true, 9035, 35, CellTier.MEDIUM, ItemID.GOTR_PORTAL_TALISMAN_CHAOS, null,
			ObjectID.GOTR_PORTAL_CHAOS, VarbitID.GOTR_TALISMAN_TOGGLE_CHAOS),
		NATURE(4361, true, 9547, 44, CellTier.STRONG, ItemID.GOTR_PORTAL_TALISMAN_NATURE, null,
			ObjectID.GOTR_PORTAL_NATURE, VarbitID.GOTR_TALISMAN_TOGGLE_NATURE),
		LAW(4362, true, 9803, 54, CellTier.STRONG, ItemID.GOTR_PORTAL_TALISMAN_LAW,
			Quest.TROLL_STRONGHOLD, ObjectID.GOTR_PORTAL_LAW, VarbitID.GOTR_TALISMAN_TOGGLE_LAW),
		DEATH(4363, true, 8779, 65, CellTier.OVERCHARGED, ItemID.GOTR_PORTAL_TALISMAN_DEATH,
			Quest.MOURNINGS_END_PART_II, ObjectID.GOTR_PORTAL_DEATH, VarbitID.GOTR_TALISMAN_TOGGLE_DEATH),
		BLOOD(4364, true, 12875, 77, CellTier.OVERCHARGED, ItemID.GOTR_PORTAL_TALISMAN_BLOOD,
			Quest.SINS_OF_THE_FATHER, ObjectID.GOTR_PORTAL_BLOOD, VarbitID.GOTR_TALISMAN_TOGGLE_BLOOD);

		/** @return active-altar HUD sprite ID, or {@code -1} for unknown */
		@Getter
		private final int spriteId;
		/** @return whether this is a catalytic altar */
		@Getter
		private final boolean catalytic;
		/** @return rune-altar region ID, or {@code -1} for unknown */
		@Getter
		private final int regionId;
		/** @return Runecraft level required to imbue essence, or {@code -1} for unknown */
		@Getter
		private final int requiredRunecraftLevel;
		/** @return charged-cell tier produced by this altar */
		@Getter
		private final CellTier cellTier;
		/** @return corresponding portal-talisman item ID, or {@code -1} for unknown */
		@Getter
		private final int portalTalismanItemId;
		/** @return quest required to enter this portal, or {@code null} when none */
		@Getter
		private final Quest requiredQuest;
		/** @return portal-guardian object ID, or {@code -1} for unknown */
		@Getter
		private final int portalObjectId;
		/** @return per-altar portal-talisman toggle varbit ID, or {@code -1} for unknown */
		@Getter
		private final int talismanToggleVarbitId;

		Altar(
			int spriteId,
			boolean catalytic,
			int regionId,
			int requiredRunecraftLevel,
			CellTier cellTier,
			int portalTalismanItemId,
			Quest requiredQuest,
			int portalObjectId,
			int talismanToggleVarbitId)
		{
			this.spriteId = spriteId;
			this.catalytic = catalytic;
			this.regionId = regionId;
			this.requiredRunecraftLevel = requiredRunecraftLevel;
			this.cellTier = cellTier;
			this.portalTalismanItemId = portalTalismanItemId;
			this.requiredQuest = requiredQuest;
			this.portalObjectId = portalObjectId;
			this.talismanToggleVarbitId = talismanToggleVarbitId;
		}

		private static Altar fromWidget(Widget widget, boolean catalytic)
		{
			if (widget != null)
			{
				for (Altar altar : values())
				{
					if (altar != UNKNOWN && altar.catalytic == catalytic
						&& altar.spriteId == widget.getSpriteId())
					{
						return altar;
					}
				}
			}
			return UNKNOWN;
		}

		private static Altar fromRegion(int regionId)
		{
			for (Altar altar : values())
			{
				if (altar.regionId == regionId)
				{
					return altar;
				}
			}
			return UNKNOWN;
		}
	}

	/** Immutable Guardians of the Rift state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the Guardians of the Rift HUD is visible, including inside rune altars */
		private final boolean hudVisible;
		/** @return whether the server currently treats the player as a minigame participant */
		private final boolean participating;
		@Getter(AccessLevel.NONE)
		private final boolean finishedGame;
		/** @return whether the player's GOTR ban flag is set */
		private final boolean banned;
		/** @return displayed Great Guardian power percentage, or {@code -1} when unavailable */
		private final int guardianPower;
		/** @return raw displayed Great Guardian power text, or an empty string when unavailable */
		private final String guardianPowerText;
		/** @return seconds until the active altar pair rotates, or {@code -1} when unavailable */
		private final int altarRotationSecondsRemaining;
		/** @return raw displayed altar-rotation timer text, or an empty string when unavailable */
		private final String altarRotationText;
		/** @return elemental energy earned in the current game */
		private final int elementalEnergy;
		/** @return catalytic energy earned in the current game */
		private final int catalyticEnergy;
		/** @return raw displayed elemental-energy text, or an empty string when unavailable */
		private final String elementalEnergyText;
		/** @return raw displayed catalytic-energy text, or an empty string when unavailable */
		private final String catalyticEnergyText;
		/** @return active elemental altar, or {@link Altar#UNKNOWN} */
		private final Altar elementalAltar;
		/** @return active catalytic altar, or {@link Altar#UNKNOWN} */
		private final Altar catalyticAltar;
		/** @return raw elemental-altar sprite ID, or {@code -1} when unavailable */
		private final int elementalAltarSpriteId;
		/** @return raw catalytic-altar sprite ID, or {@code -1} when unavailable */
		private final int catalyticAltarSpriteId;
		/** @return active rift guardians, or {@code -1} when unavailable */
		private final int activeGuardians;
		/** @return maximum active rift guardians, or {@code -1} when unavailable */
		private final int guardianLimit;
		/** @return raw displayed guardian count/limit text, or an empty string when unavailable */
		private final String guardianCountText;
		/** @return whether the portal to the huge guardian remains is displayed as open */
		private final boolean guardianEssencePortalOpen;
		/** @return server varbit controlling visibility of the portal's lower endpoint */
		private final boolean guardianEssencePortalBottomVisible;
		/** @return raw displayed huge-remains portal status, or an empty string while closed */
		private final String guardianEssencePortalStatus;
		/** @return displayed portal compass position, or an empty string while closed */
		private final String guardianEssencePortalPosition;
		/** @return seconds until the huge-remains portal closes, or {@code -1} when unavailable */
		private final int guardianEssencePortalSecondsRemaining;
		/** @return whether the player is in the main Temple of the Eye minigame region */
		private final boolean inMainTemple;
		/** @return whether the player is north of the entry barrier in the playable arena */
		private final boolean inArena;
		/** @return rune altar the player currently occupies, or {@link Altar#UNKNOWN} */
		private final Altar currentAltar;
		/** @return separated east or west mining pocket occupied by the player */
		private final MiningArea miningArea;
		/** @return guardian fragments mined in the current game, capped by the server */
		private final int minedFragmentsThisGame;
		/** @return raw otherwise-undocumented temporary GOTR varp */
		private final int rawTemporaryState;
		/** @return immutable permanent and cross-round GOTR state */
		private final PersistentState persistent;
		/** @return immutable GOTR warning and talisman-drop settings */
		private final SettingsState settings;
		/** @return immutable GOTR-relevant inventory state */
		private final InventoryState inventory;
		/** @return immutable set of currently loaded barrier cell tiles */
		private final Set<Barrier> barriers;
		/** @return immutable classified NPC state from the loaded GOTR scene */
		private final SceneState scene;

		/** @return whether a round is active, excluding the lobby period between rounds */
		public boolean isRoundActive()
		{
			return participating && !finishedGame && guardianPower > 0 && guardianPower < 100;
		}

		/** @return whether the player is inside waiting for an active round to begin */
		public boolean isWaitingForRound()
		{
			return participating && !finishedGame && guardianPower == 0;
		}

		/** @return whether the current round has reached its successful completion state */
		public boolean isRoundComplete()
		{
			return hudVisible && (finishedGame || guardianPower >= 100);
		}

		/** @return the game's raw flag indicating that the player finished the current game */
		public boolean hasFinishedGame()
		{
			return finishedGame;
		}

		/** @return total elemental and catalytic energy earned in the current game */
		public int getTotalEnergy()
		{
			return elementalEnergy + catalyticEnergy;
		}

		/** @return whether the player has earned the 300 combined energy required for rewards */
		public boolean isRewardEligible()
		{
			return getTotalEnergy() >= REWARD_ENERGY_REQUIREMENT;
		}

		/** @return whether the player is south of the entry barrier in the temple lobby */
		public boolean isInLobby()
		{
			return inMainTemple && !inArena;
		}

	}
}
