package net.runelite.client.plugins.m8aq.api.minigames;

import java.awt.Polygon;
import java.util.ArrayList;
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
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;

/** Provides a read-only snapshot of the client-observable Vale Totems state. */
public final class ValeTotems
{
	private static final int SITE_COUNT = 8;
	private static final int SITE_VARBIT_COUNT = 18;
	private static final int OFFERING_CAP = 15_000;
	private static final int[] TRAIL_BUFF_VARBITS = {
		VarbitID.ENT_TOTEMS_SITE_1_TRAIL_BUFF_ACTIVE,
		VarbitID.ENT_TOTEMS_SITE_2_TRAIL_BUFF_ACTIVE,
		VarbitID.ENT_TOTEMS_SITE_3_TRAIL_BUFF_ACTIVE,
		VarbitID.ENT_TOTEMS_SITE_4_TRAIL_BUFF_ACTIVE,
		VarbitID.ENT_TOTEMS_SITE_5_TRAIL_BUFF_ACTIVE,
		VarbitID.ENT_TOTEMS_SITE_6_TRAIL_BUFF_ACTIVE,
		VarbitID.ENT_TOTEMS_SITE_7_TRAIL_BUFF_ACTIVE,
		VarbitID.ENT_TOTEMS_SITE_8_TRAIL_BUFF_ACTIVE
	};
	private static final Polygon VALE_TOTEMS_AREA = new Polygon(
		new int[]{1344, 1333, 1333, 1325, 1325, 1330, 1330, 1340, 1348, 1348, 1380,
			1400, 1418, 1480, 1504, 1504, 1484, 1468, 1460, 1460, 1448, 1421, 1421,
			1411, 1402, 1402, 1401, 1401, 1400, 1400, 1399, 1399, 1365},
		new int[]{3297, 3312, 3322, 3330, 3333, 3338, 3348, 3348, 3356, 3380, 3400,
			3400, 3394, 3364, 3364, 3312, 3312, 3304, 3296, 3292, 3286, 3286, 3275,
			3270, 3270, 3268, 3267, 3266, 3266, 3263, 3262, 3259, 3259},
		33);

	private ValeTotems()
	{
	}

	/**
	 * Reads the current Vale Totems state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		List<TotemState> sites = new ArrayList<>(SITE_COUNT);
		int totalUnclaimedOfferings = 0;
		for (int index = 0; index < SITE_COUNT; index++)
		{
			TotemState site = readSite(client, index);
			sites.add(site);
			totalUnclaimedOfferings += site.unclaimedOfferings;
		}

		Player player = client.getLocalPlayer();
		WorldPoint location = player == null ? null : player.getWorldLocation();
		boolean atValeTotems = location != null && location.getPlane() == 0
			&& VALE_TOTEMS_AREA.contains(location.getX(), location.getY());

		return new State(
			client.getVarbitValue(VarbitID.ENT_TOTEMS_INTRO),
			client.getVarbitValue(VarbitID.ENT_TOTEMS_BROKEN_CHAT) == 1,
			client.getVarbitValue(VarbitID.ENT_TOTEMS_CARVE_CHAT) == 1,
			client.getVarbitValue(VarbitID.ENT_TOTEMS_DECORATE_CHAT) == 1,
			client.getVarbitValue(VarbitID.ENT_TOTEMS_TRAIL_XP_BUFF_ACTIONS),
			client.getVarbitValue(VarbitID.ENT_TOTEMS_BUFFS_VISIBLE) == 1,
			client.getVarbitValue(VarbitID.BUFF_ENT_TOTEMS_BONUS_XP_DISABLED) == 1,
			client.getVarbitValue(VarbitID.COLLECTION_OTHER_VALE_TOTEMS_COMPLETED) == 1,
			client.getVarpValue(VarPlayerID.ENT_TOTEMS_RESEARCH_POINTS),
			totalUnclaimedOfferings,
			Collections.unmodifiableList(sites),
			atValeTotems);
	}

	private static TotemState readSite(Client client, int index)
	{
		int baseVarbit = VarbitID.ENT_TOTEMS_SITE_1_BASE + index * SITE_VARBIT_COUNT;
		int baseValue = client.getVarbitValue(baseVarbit);
		List<Integer> nearbySpiritValues = List.of(
			client.getVarbitValue(baseVarbit + 7),
			client.getVarbitValue(baseVarbit + 8),
			client.getVarbitValue(baseVarbit + 9));
		List<Spirit> nearbySpirits = new ArrayList<>(3);
		for (int value : nearbySpiritValues)
		{
			nearbySpirits.add(Spirit.fromNearbyValue(value));
		}
		EnumSet<Spirit> multiAnimalSpirits = EnumSet.noneOf(Spirit.class);
		for (Spirit spirit : Spirit.playableValues())
		{
			if (client.getVarbitValue(baseVarbit + 12 + spirit.nearbyValue - 1) == 1)
			{
				multiAnimalSpirits.add(spirit);
			}
		}

		return new TotemState(
			index + 1,
			baseValue,
			Wood.fromValue(baseValue),
			client.getVarbitValue(baseVarbit + 1) == 1,
			client.getVarbitValue(baseVarbit + 2),
			SegmentState.fromValue(client.getVarbitValue(baseVarbit + 3)),
			SegmentState.fromValue(client.getVarbitValue(baseVarbit + 4)),
			SegmentState.fromValue(client.getVarbitValue(baseVarbit + 5)),
			client.getVarbitValue(baseVarbit + 6),
			nearbySpiritValues,
			Collections.unmodifiableList(nearbySpirits),
			client.getVarbitValue(baseVarbit + 10),
			client.getVarbitValue(baseVarbit + 11),
			client.getVarbitValue(TRAIL_BUFF_VARBITS[index]) == 1,
			Collections.unmodifiableSet(multiAnimalSpirits),
			client.getVarbitValue(baseVarbit + 17));
	}

	/** Totem wood and its maximum durability. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Wood
	{
		NONE(0, 0),
		OAK(1, 2),
		WILLOW(2, 3),
		MAPLE(3, 4),
		YEW(4, 5),
		MAGIC(5, 6),
		REDWOOD(6, 7),
		UNKNOWN(-1, 0);

		/** @return raw base value */
		private final int value;
		/** @return maximum ent visits before the totem decays */
		private final int maximumDurability;

		private static Wood fromValue(int value)
		{
			for (Wood wood : values())
			{
				if (wood.value == value)
				{
					return wood;
				}
			}
			return UNKNOWN;
		}
	}

	/** Spirit used by nearby ent groups and carved totem segments. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Spirit
	{
		NONE(0, 0),
		BUFFALO(1, 10),
		JAGUAR(2, 11),
		EAGLE(3, 12),
		SNAKE(4, 13),
		SCORPION(5, 14),
		UNKNOWN(-1, -1);

		/** @return raw nearby-spirit value */
		private final int nearbyValue;
		/** @return raw carved-segment value */
		private final int carvedValue;

		private static Spirit fromNearbyValue(int value)
		{
			for (Spirit spirit : values())
			{
				if (spirit.nearbyValue == value)
				{
					return spirit;
				}
			}
			return UNKNOWN;
		}

		private static Spirit fromCarvedValue(int value)
		{
			if (value >= 0 && value <= 6)
			{
				return NONE;
			}
			for (Spirit spirit : values())
			{
				if (spirit.carvedValue == value)
				{
					return spirit;
				}
			}
			return UNKNOWN;
		}

		private static Spirit[] playableValues()
		{
			return new Spirit[]{BUFFALO, JAGUAR, EAGLE, SNAKE, SCORPION};
		}
	}

	/** One bottom, middle, or top totem segment. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class SegmentState
	{
		/** @return raw segment value */
		private final int value;
		/** @return carved spirit, none while merely built, or unknown */
		private final Spirit spirit;

		private static SegmentState fromValue(int value)
		{
			return new SegmentState(value, Spirit.fromCarvedValue(value));
		}

		/** @return whether this segment has been built but not carved */
		public boolean isBuilt()
		{
			return value >= 1 && value <= 6;
		}

		/** @return whether this segment contains a recognized spirit carving */
		public boolean isCarved()
		{
			return spirit != Spirit.NONE && spirit != Spirit.UNKNOWN;
		}
	}

	/** Immutable state for one of the eight Vale Totems sites. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class TotemState
	{
		/** @return one-based site number */ private final int siteNumber;
		/** @return raw base varbit value */ private final int baseValue;
		/** @return decoded base wood */ private final Wood wood;
		/** @return whether the base has been carved */ private final boolean baseCarved;
		/** @return raw base multiloc value */ private final int baseMultilocValue;
		/** @return bottom segment state */ private final SegmentState bottom;
		/** @return middle segment state */ private final SegmentState middle;
		/** @return top segment state */ private final SegmentState top;
		/** @return number of attached decorations */ private final int decorations;
		/** @return immutable raw nearby-spirit slots */ private final List<Integer> nearbySpiritValues;
		/** @return immutable decoded nearby-spirit slots */ private final List<Spirit> nearbySpirits;
		/** @return remaining ent visits before decay */ private final int durabilityRemaining;
		/** @return unclaimed offerings stored at this site */ private final int unclaimedOfferings;
		/** @return whether this site's next ent visit receives the trail offering buff */
		private final boolean trailBuffActive;
		/** @return immutable spirits represented by the five multianimal flags */
		private final Set<Spirit> multiAnimalSpirits;
		/** @return raw combined five-bit multianimal mask */ private final int multiAnimalMask;

		/** @return whether construction has started at this site */
		public boolean isStarted()
		{
			return baseValue != 0;
		}

		/** @return whether all three segment slots are nonempty */
		public boolean isFullyBuilt()
		{
			return bottom.value != 0 && middle.value != 0 && top.value != 0;
		}

		/** @return whether the server marks all carving as complete */
		public boolean isFullyCarved()
		{
			return baseCarved;
		}

		/** @return whether all four decorations are attached */
		public boolean isFullyDecorated()
		{
			return decorations >= 4;
		}

		/** @return whether offerings reached the documented soft cap */
		public boolean isOfferingCapped()
		{
			return unclaimedOfferings >= OFFERING_CAP;
		}

		/** @return recognized spirits carved into the three segments */
		public Set<Spirit> getCarvedSpirits()
		{
			EnumSet<Spirit> spirits = EnumSet.noneOf(Spirit.class);
			addRecognized(spirits, bottom.spirit);
			addRecognized(spirits, middle.spirit);
			addRecognized(spirits, top.spirit);
			return Collections.unmodifiableSet(spirits);
		}

		/** @return recognized nearby spirits not yet carved into the totem */
		public Set<Spirit> getMissingNearbySpirits()
		{
			EnumSet<Spirit> missing = recognizedNearbySpirits();
			missing.removeAll(getCarvedSpirits());
			return Collections.unmodifiableSet(missing);
		}

		/** @return recognized carvings which are not in the current nearby group */
		public Set<Spirit> getIncorrectCarvings()
		{
			EnumSet<Spirit> incorrect = EnumSet.noneOf(Spirit.class);
			incorrect.addAll(getCarvedSpirits());
			incorrect.removeAll(recognizedNearbySpirits());
			return Collections.unmodifiableSet(incorrect);
		}

		private EnumSet<Spirit> recognizedNearbySpirits()
		{
			EnumSet<Spirit> spirits = EnumSet.noneOf(Spirit.class);
			for (Spirit spirit : nearbySpirits)
			{
				addRecognized(spirits, spirit);
			}
			return spirits;
		}

		private static void addRecognized(Set<Spirit> spirits, Spirit spirit)
		{
			if (spirit != Spirit.NONE && spirit != Spirit.UNKNOWN)
			{
				spirits.add(spirit);
			}
		}
	}

	/** Immutable Vale Totems state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return raw Vale Totems miniquest progress */ private final int miniquestProgress;
		/** @return whether the broken-totem tutorial chat has been seen */
		private final boolean brokenChatSeen;
		/** @return whether the carving tutorial chat has been seen */ private final boolean carveChatSeen;
		/** @return whether the decoration tutorial chat has been seen */
		private final boolean decorateChatSeen;
		/** @return remaining build or carve actions receiving trail bonus XP */
		private final int trailXpBuffActionsRemaining;
		/** @return raw official buff-display visibility flag */ private final boolean buffsVisible;
		/** @return whether the official bonus-XP buff display is disabled */
		private final boolean bonusXpBuffDisplayDisabled;
		/** @return whether the Vale Totems collection-log section is complete */
		private final boolean collectionLogCompleted;
		/** @return persistent Vale Totems research points */ private final int researchPoints;
		/** @return unclaimed offerings summed across all sites */
		private final int totalUnclaimedOfferings;
		/** @return immutable site snapshots in site-number order */ private final List<TotemState> sites;
		/** @return whether the local player is in the Vale Totems area */ private final boolean atValeTotems;

		/** @return whether the observed miniquest progress is complete */
		public boolean isMiniquestCompleted()
		{
			return miniquestProgress >= 100;
		}

		/**
		 * Returns one site by its one-based number.
		 *
		 * @param siteNumber site number from 1 through 8
		 * @return immutable site snapshot
		 */
		public TotemState getSite(int siteNumber)
		{
			if (siteNumber < 1 || siteNumber > sites.size())
			{
				throw new IllegalArgumentException("siteNumber must be from 1 through 8");
			}
			return sites.get(siteNumber - 1);
		}
	}
}
