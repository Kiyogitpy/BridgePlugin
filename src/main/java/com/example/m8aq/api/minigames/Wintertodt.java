package com.example.m8aq.api.minigames;

import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;

/** Provides a read-only snapshot of the client-observable Wintertodt state. */
public final class Wintertodt
{
	private static final int WINTERTODT_REGION_ID = 6462;
	private static final int REWARD_POINTS_REQUIRED = 500;
	private static final int BROKEN_BRAZIER_SPRITE_ID = 1397;
	private static final int UNLIT_BRAZIER_SPRITE_ID = 1398;
	private static final int LIT_BRAZIER_SPRITE_ID = 1399;
	private static final int INCAPACITATED_PYROMANCER_SPRITE_ID = 1400;

	private Wintertodt()
	{
	}

	/**
	 * Reads the current Wintertodt state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");

		boolean hudVisible = isVisible(client.getWidget(InterfaceID.WintStatus.UNIVERSE));
		boolean energyVisible = hudVisible
			&& isVisible(client.getWidget(InterfaceID.WintStatus.ENERGY));
		boolean warmthVisible = hudVisible
			&& isVisible(client.getWidget(InterfaceID.WintStatus.WARMTH));
		boolean brazierStatusVisible = hudVisible
			&& isVisible(client.getWidget(InterfaceID.WintStatus.BRAZIER_LAYER));
		int energyPercent = energyVisible
			? readFirstInt(client.getWidget(InterfaceID.WintStatus.ENERGY_TITLE)) : -1;
		int points = brazierStatusVisible
			? readFirstInt(client.getWidget(InterfaceID.WintStatus.POINTS)) : -1;
		int warmth = client.getVarbitValue(VarbitID.WINT_WARMTH);
		int respawnDelay = client.getVarbitValue(VarbitID.WINT_TRANSMIT_RESPAWNDELAY);

		EnumMap<Brazier, BrazierState> braziers = new EnumMap<>(Brazier.class);
		for (Brazier brazier : Brazier.values())
		{
			braziers.put(brazier, readBrazier(client, brazier, brazierStatusVisible));
		}

		Player player = client.getLocalPlayer();
		WorldPoint location = player == null ? null : player.getWorldLocation();
		boolean atWintertodt = location != null
			&& location.getRegionID() == WINTERTODT_REGION_ID;

		return new State(
			hudVisible,
			energyVisible,
			warmthVisible,
			brazierStatusVisible,
			energyVisible && energyPercent > 0,
			points,
			energyPercent,
			warmth,
			respawnDelay,
			respawnDelay * 30 / 50,
			client.getVarbitValue(VarbitID.WINT_REWARD_POOL),
			client.getVarbitValue(VarbitID.WINT_NEEDS_WARNING) == 1,
			client.getVarbitValue(VarbitID.WINT_AREA_ATTACK) == 1,
			Collections.unmodifiableMap(braziers),
			atWintertodt);
	}

	private static BrazierState readBrazier(Client client, Brazier brazier, boolean visible)
	{
		Widget brazierWidget = visible ? client.getWidget(brazier.brazierComponent) : null;
		Widget pyromancerWidget = visible ? client.getWidget(brazier.pyromancerComponent) : null;
		int brazierSpriteId = brazierWidget == null ? -1 : brazierWidget.getSpriteId();
		int pyromancerSpriteId = pyromancerWidget == null ? -1 : pyromancerWidget.getSpriteId();
		return new BrazierState(
			brazier,
			brazierSpriteId,
			brazierWidget == null ? BrazierStatus.UNKNOWN
				: BrazierStatus.fromSpriteId(brazierSpriteId),
			pyromancerSpriteId,
			pyromancerWidget == null ? PyromancerStatus.UNKNOWN
				: PyromancerStatus.fromSpriteId(pyromancerSpriteId));
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
		for (int index = 0; index < value.length(); index++)
		{
			char character = value.charAt(index);
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

	/** One HUD quadrant and its server-provided status components. */
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Brazier
	{
		SOUTH_WEST(InterfaceID.WintStatus.BRAZIER1, InterfaceID.WintStatus.PYRO1),
		NORTH_WEST(InterfaceID.WintStatus.BRAZIER2, InterfaceID.WintStatus.PYRO2),
		NORTH_EAST(InterfaceID.WintStatus.BRAZIER3, InterfaceID.WintStatus.PYRO3),
		SOUTH_EAST(InterfaceID.WintStatus.BRAZIER4, InterfaceID.WintStatus.PYRO4);

		private final int brazierComponent;
		private final int pyromancerComponent;
	}

	/** Current status of one brazier. */
	public enum BrazierStatus
	{
		UNKNOWN,
		BROKEN,
		UNLIT,
		LIT;

		private static BrazierStatus fromSpriteId(int spriteId)
		{
			switch (spriteId)
			{
				case BROKEN_BRAZIER_SPRITE_ID:
					return BROKEN;
				case UNLIT_BRAZIER_SPRITE_ID:
					return UNLIT;
				case LIT_BRAZIER_SPRITE_ID:
					return LIT;
				default:
					return UNKNOWN;
			}
		}
	}

	/** Current status of the pyromancer beside one brazier. */
	public enum PyromancerStatus
	{
		UNKNOWN,
		ALIVE,
		INCAPACITATED;

		private static PyromancerStatus fromSpriteId(int spriteId)
		{
			if (spriteId == -1)
			{
				return ALIVE;
			}
			return spriteId == INCAPACITATED_PYROMANCER_SPRITE_ID
				? INCAPACITATED : UNKNOWN;
		}
	}

	/** Immutable state for one brazier and its pyromancer. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class BrazierState
	{
		/** @return HUD quadrant */ private final Brazier brazier;
		/** @return raw brazier sprite ID, or -1 when unavailable */ private final int brazierSpriteId;
		/** @return decoded brazier status */ private final BrazierStatus status;
		/** @return raw pyromancer sprite ID, or -1 when unavailable */
		private final int pyromancerSpriteId;
		/** @return decoded pyromancer status */ private final PyromancerStatus pyromancerStatus;

		/** @return whether this brazier is lit and its pyromancer is active */
		public boolean isOperational()
		{
			return status == BrazierStatus.LIT
				&& pyromancerStatus == PyromancerStatus.ALIVE;
		}
	}

	/** Immutable Wintertodt state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the official Wintertodt HUD is visible */ private final boolean hudVisible;
		/** @return whether the energy bar is visible */ private final boolean energyVisible;
		/** @return whether the warmth bar is visible */ private final boolean warmthVisible;
		/** @return whether brazier and pyromancer status is visible */
		private final boolean brazierStatusVisible;
		/** @return whether a round has positive visible Wintertodt energy */ private final boolean roundActive;
		/** @return player points, or -1 when unavailable */ private final int points;
		/** @return visible Wintertodt energy percentage, or -1 */ private final int energyPercent;
		/** @return raw warmth on the 0-1000 scale */ private final int warmth;
		/** @return raw server respawn-delay value */ private final int respawnDelay;
		/** @return seconds until the next round derived by RuneLite */
		private final int secondsUntilRoundStart;
		/** @return raw accumulated reward-pool value */ private final int rewardPool;
		/** @return whether the server says the player needs the warmth warning */
		private final boolean warningNeeded;
		/** @return whether the server's area-attack flag is active */ private final boolean areaAttackActive;
		/** @return immutable state for all four braziers */ private final Map<Brazier, BrazierState> braziers;
		/** @return whether the local player is in the Wintertodt region */ private final boolean atWintertodt;

		/** @return warmth as a percentage */
		public double getWarmthPercent()
		{
			return warmth / 10.0;
		}

		/** @return whether visible points meet the standard 500-point reward threshold */
		public boolean isRewardEligible()
		{
			return points >= REWARD_POINTS_REQUIRED;
		}

		/**
		 * Returns one quadrant snapshot.
		 *
		 * @param brazier quadrant
		 * @return immutable brazier state
		 */
		public BrazierState getBrazier(Brazier brazier)
		{
			return braziers.get(Objects.requireNonNull(brazier, "brazier"));
		}
	}
}
