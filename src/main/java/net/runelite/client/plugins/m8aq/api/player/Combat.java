package net.runelite.client.plugins.m8aq.api.player;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Actor;
import net.runelite.api.Client;
import net.runelite.api.EnumComposition;
import net.runelite.api.EnumID;
import net.runelite.api.NPC;
import net.runelite.api.ParamID;
import net.runelite.api.Player;
import net.runelite.api.StructComposition;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;

/** Provides a read-only snapshot of combat controls and the local player's interaction target. */
public final class Combat
{
	private Combat()
	{
	}

	/**
	 * Reads current combat state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable combat snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Player player = client.getLocalPlayer();
		if (player == null)
		{
			return new State(null, false, -1.0, false, null);
		}

		int attackStyleIndex = client.getVarpValue(VarPlayerID.COM_MODE);
		int weaponCategory = client.getVarbitValue(VarbitID.COMBAT_WEAPON_CATEGORY);
		int specialEnergy = client.getVarpValue(VarPlayerID.SA_ENERGY);
		return new State(
			attackStyle(client, weaponCategory, attackStyleIndex),
			client.getVarpValue(VarPlayerID.OPTION_NODEF) == 0,
			specialEnergy / 10.0,
			client.getVarpValue(VarPlayerID.SA_ATTACK) == 1,
			Target.from(player.getInteracting()));
	}

	private static String attackStyle(Client client, int weaponCategory, int attackStyleIndex)
	{
		String[] fallback = weaponCategory == 22
			? new String[]{"Accurate", "Aggressive", "Other", "Defensive", "Casting", "Defensive Casting"}
			: weaponCategory == 30
				? new String[]{"Accurate", "Aggressive", "Aggressive", "Defensive"} : null;
		int index = attackStyleIndex == 4
			? attackStyleIndex + client.getVarbitValue(VarbitID.AUTOCAST_DEFMODE) : attackStyleIndex;
		if (fallback != null)
		{
			return index >= 0 && index < fallback.length ? fallback[index] : null;
		}
		try
		{
			EnumComposition weaponStyles = client.getEnum(EnumID.WEAPON_STYLES);
			int stylesEnumId = weaponStyles == null ? -1 : weaponStyles.getIntValue(weaponCategory);
			EnumComposition styles = stylesEnumId < 0 ? null : client.getEnum(stylesEnumId);
			int[] styleIds = styles == null ? null : styles.getIntVals();
			if (styleIds == null || index < 0 || index >= styleIds.length)
			{
				return null;
			}
			StructComposition style = client.getStructComposition(styleIds[index]);
			String name = style == null ? null : style.getStringValue(ParamID.ATTACK_STYLE_NAME);
			if (name == null || name.isBlank())
			{
				return null;
			}
			if (index == 5 && "defensive".equalsIgnoreCase(name))
			{
				return "Defensive Casting";
			}
			return Character.toUpperCase(name.charAt(0)) + name.substring(1).toLowerCase();
		}
		catch (RuntimeException ignored)
		{
			return null;
		}
	}

	/** Direct actor category for an interaction target. */
	public enum TargetType
	{
		PLAYER,
		NPC,
		OTHER
	}

	/** Immutable copied interaction-target state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Target
	{
		/** @return direct RuneLite actor category */
		private final TargetType type;
		/** @return target name, or null when unavailable */
		private final String name;
		/** @return target combat level */
		private final int combatLevel;
		/** @return target world coordinate, or null when unavailable */
		private final WorldPoint worldPoint;
		/** @return player index, or -1 for non-player targets */
		private final int playerId;
		/** @return NPC composition ID, or -1 for non-NPC targets */
		private final int npcId;
		/** @return NPC actor index, or -1 for non-NPC targets */
		private final int npcIndex;
		/** @return target health ratio, or -1 when the client does not expose it */
		private final int healthRatio;
		/** @return target health-ratio scale, or -1 when the client does not expose it */
		private final int healthScale;

		private static Target from(Actor actor)
		{
			if (actor == null)
			{
				return null;
			}
			TargetType type = actor instanceof Player ? TargetType.PLAYER
				: actor instanceof NPC ? TargetType.NPC : TargetType.OTHER;
			return new Target(
				type,
				actor.getName(),
				actor.getCombatLevel(),
				actor.getWorldLocation(),
				actor instanceof Player ? ((Player) actor).getId() : -1,
				actor instanceof NPC ? ((NPC) actor).getId() : -1,
				actor instanceof NPC ? ((NPC) actor).getIndex() : -1,
				actor.getHealthRatio(),
				actor.getHealthScale());
		}
	}

	/** Immutable combat state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return selected attack-style name, or null when unavailable */
		private final String attackStyle;
		/** @return whether auto-retaliate is enabled */
		private final boolean autoRetaliateEnabled;
		/** @return special-attack energy as a percentage, or -1 when unavailable */
		private final double specialAttackEnergyPercent;
		/** @return whether the special-attack toggle is armed */
		private final boolean specialAttackArmed;
		/** @return direct interaction target, or null when no actor is selected */
		private final Target target;
	}
}
