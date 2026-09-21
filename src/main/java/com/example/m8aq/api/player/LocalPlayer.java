package com.example.m8aq.api.player;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.HeadIcon;
import net.runelite.api.NPCComposition;
import net.runelite.api.Player;
import net.runelite.api.PlayerComposition;
import net.runelite.api.SkullIcon;

/** Provides a read-only snapshot of the local player's identity, actor state, and appearance. */
public final class LocalPlayer
{
	private LocalPlayer()
	{
	}

	/**
	 * Reads the current local-player state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable local-player snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Player player = client.getLocalPlayer();
		if (player == null)
		{
			return new State(-1, null, -1, -1, null, SkullType.NONE,
				-1, -1, -1, -1, -1, false, null, Appearance.unavailable());
		}

		return new State(
			player.getId(),
			player.getName(),
			player.getCombatLevel(),
			player.getTeam(),
			player.getOverheadIcon(),
			skullType(player.getSkullIcon()),
			player.getAnimation(),
			player.getPoseAnimation(),
			player.getIdlePoseAnimation(),
			player.getOrientation(),
			player.getCurrentOrientation(),
			player.isDead(),
			player.getOverheadText(),
			Appearance.from(client, player.getPlayerComposition()));
	}

	private static SkullType skullType(int value)
	{
		switch (value)
		{
			case SkullIcon.NONE: return SkullType.NONE;
			case SkullIcon.SKULL: return SkullType.SKULL;
			case SkullIcon.SKULL_FIGHT_PIT: return SkullType.FIGHT_PIT;
			case SkullIcon.SKULL_HIGH_RISK: return SkullType.HIGH_RISK;
			case SkullIcon.FORINTHRY_SURGE: return SkullType.FORINTHRY_SURGE;
			case SkullIcon.SKULL_DEADMAN: return SkullType.DEADMAN;
			case SkullIcon.LOOT_KEYS_ONE: return SkullType.LOOT_KEYS_ONE;
			case SkullIcon.LOOT_KEYS_TWO: return SkullType.LOOT_KEYS_TWO;
			case SkullIcon.LOOT_KEYS_THREE: return SkullType.LOOT_KEYS_THREE;
			case SkullIcon.LOOT_KEYS_FOUR: return SkullType.LOOT_KEYS_FOUR;
			case SkullIcon.LOOT_KEYS_FIVE: return SkullType.LOOT_KEYS_FIVE;
			case SkullIcon.FORINTHRY_SURGE_DEADMAN: return SkullType.FORINTHRY_SURGE_DEADMAN;
			case SkullIcon.FORINTHRY_SURGE_KEYS_ONE: return SkullType.FORINTHRY_SURGE_KEYS_ONE;
			case SkullIcon.FORINTHRY_SURGE_KEYS_TWO: return SkullType.FORINTHRY_SURGE_KEYS_TWO;
			case SkullIcon.FORINTHRY_SURGE_KEYS_THREE: return SkullType.FORINTHRY_SURGE_KEYS_THREE;
			case SkullIcon.FORINTHRY_SURGE_KEYS_FOUR: return SkullType.FORINTHRY_SURGE_KEYS_FOUR;
			case SkullIcon.FORINTHRY_SURGE_KEYS_FIVE: return SkullType.FORINTHRY_SURGE_KEYS_FIVE;
			default: return SkullType.UNKNOWN;
		}
	}

	/** Named player gender. */
	public enum Gender
	{
		MALE,
		FEMALE,
		UNKNOWN
	}

	/** Named overhead skull type. */
	public enum SkullType
	{
		NONE, SKULL, FIGHT_PIT, HIGH_RISK, FORINTHRY_SURGE, DEADMAN,
		LOOT_KEYS_ONE, LOOT_KEYS_TWO, LOOT_KEYS_THREE, LOOT_KEYS_FOUR, LOOT_KEYS_FIVE,
		FORINTHRY_SURGE_DEADMAN, FORINTHRY_SURGE_KEYS_ONE, FORINTHRY_SURGE_KEYS_TWO,
		FORINTHRY_SURGE_KEYS_THREE, FORINTHRY_SURGE_KEYS_FOUR, FORINTHRY_SURGE_KEYS_FIVE,
		UNKNOWN
	}

	/** Immutable copied appearance state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Appearance
	{
		/** @return named gender */
		private final Gender gender;
		/** @return transformed NPC ID, or -1 when not transformed or unavailable */
		private final int transformedNpcId;
		/** @return transformed NPC name, or null when not transformed or unresolved */
		private final String transformedNpcName;

		private static Appearance from(Client client, PlayerComposition composition)
		{
			if (composition == null)
			{
				return unavailable();
			}
			int transformedNpcId = composition.getTransformedNpcId();
			NPCComposition npc = transformedNpcId < 0 ? null : client.getNpcDefinition(transformedNpcId);
			return new Appearance(composition.getGender() == 0 ? Gender.MALE
				: composition.getGender() == 1 ? Gender.FEMALE : Gender.UNKNOWN,
				transformedNpcId, npc == null ? null : npc.getName());
		}

		private static Appearance unavailable()
		{
			return new Appearance(Gender.UNKNOWN, -1, null);
		}
	}

	/** Immutable local-player state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return local player index, or -1 when unavailable */
		private final int playerId;
		/** @return player name, or null when unavailable */
		private final String name;
		/** @return combat level, or -1 when unavailable */
		private final int combatLevel;
		/** @return current team/cape identifier, or -1 when unavailable */
		private final int team;
		/** @return overhead prayer icon, or null when absent/unavailable */
		private final HeadIcon overheadIcon;
		/** @return named overhead skull type */
		private final SkullType skull;
		/** @return current animation ID, or -1 when unavailable */
		private final int animation;
		/** @return current pose animation ID, or -1 when unavailable */
		private final int poseAnimation;
		/** @return idle pose animation ID, or -1 when unavailable */
		private final int idlePoseAnimation;
		/** @return target orientation, or -1 when unavailable */
		private final int orientation;
		/** @return current rendered orientation, or -1 when unavailable */
		private final int currentOrientation;
		/** @return whether the local player is marked dead */
		private final boolean dead;
		/** @return overhead text, or null when absent/unavailable */
		private final String overheadText;
		/** @return copied appearance state */
		private final Appearance appearance;
	}
}
