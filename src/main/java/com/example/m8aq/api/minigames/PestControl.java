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
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;

/** Provides a read-only snapshot of the client-observable Pest Control state. */
public final class PestControl
{
	private static final int BATTLEFIELD_REGION_ID = 10536;
	private static final int OUTPOST_REGION_ID = 10537;

	private PestControl()
	{
	}

	/**
	 * Reads the current Pest Control state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");

		boolean gameHudVisible = isVisible(client.getWidget(InterfaceID.PestStatusOverlay.UNIVERSE));
		boolean landerHudVisible = isVisible(client.getWidget(InterfaceID.PestLanderOverlay.UNIVERSE));
		EnumMap<Portal, PortalState> portals = new EnumMap<>(Portal.class);
		for (Portal portal : Portal.values())
		{
			Widget health = client.getWidget(portal.healthComponent);
			Widget shield = client.getWidget(portal.shieldComponent);
			ShieldState shieldState = !gameHudVisible || shield == null
				? ShieldState.UNKNOWN
				: shield.isHidden() ? ShieldState.DROPPED : ShieldState.SHIELDED;
			portals.put(portal, new PortalState(
				portal,
				gameHudVisible ? readFirstInt(health) : -1,
				shieldState));
		}

		int activity = client.getVarbitValue(VarbitID.PEST_ACTIVITY_COUNTER);
		Player player = client.getLocalPlayer();
		WorldPoint location = playerLocation(client, player);
		int regionId = location == null ? -1 : location.getRegionID();

		Widget roundTime = client.getWidget(InterfaceID.PestStatusOverlay.PEST_STATUS_OVER_TIME);
		Widget departure = client.getWidget(InterfaceID.PestLanderOverlay.PEST_LANDER_OVER_TIME);
		return new State(
			gameHudVisible ? Phase.IN_GAME : landerHudVisible ? Phase.ON_LANDER : Phase.INACTIVE,
			gameHudVisible,
			gameHudVisible ? text(roundTime) : "",
			gameHudVisible
				? readFirstInt(client.getWidget(InterfaceID.PestStatusOverlay.PEST_STATUS_OVER_HEALTH)) : -1,
			gameHudVisible
				? readFirstInt(client.getWidget(InterfaceID.PestStatusOverlay.PEST_STATUS_OVER_DAM)) : -1,
			activity,
			activity * 2,
			Collections.unmodifiableMap(portals),
			landerHudVisible,
			landerHudVisible ? text(departure) : "",
			landerHudVisible ? readFirstInt(departure) : -1,
			landerHudVisible
				? readFirstInt(client.getWidget(InterfaceID.PestLanderOverlay.PEST_LANDER_OVER_PL)) : -1,
			landerHudVisible
				? readFirstInt(client.getWidget(InterfaceID.PestLanderOverlay.PEST_LANDER_OVER_POINTS)) : -1,
			landerHudVisible
				? Difficulty.fromText(text(client.getWidget(
					InterfaceID.PestLanderOverlay.PEST_LANDER_OVER_LEVEL)))
				: Difficulty.UNKNOWN,
			client.getVarbitValue(VarbitID.COLLECTION_MINIGAMES_PEST_COMPLETED) == 1,
			regionId == OUTPOST_REGION_ID,
			regionId == BATTLEFIELD_REGION_ID);
	}

	private static WorldPoint playerLocation(Client client, Player player)
	{
		if (player == null)
		{
			return null;
		}
		WorldPoint location = player.getWorldLocation();
		LocalPoint localLocation = player.getLocalLocation();
		WorldView worldView = client.getTopLevelWorldView();
		if (worldView != null && worldView.isInstance() && worldView.getScene() != null
			&& localLocation != null)
		{
			WorldPoint templateLocation = WorldPoint.fromLocalInstance(
				worldView.getScene(), localLocation, worldView.getPlane());
			return templateLocation == null ? location : templateLocation;
		}
		return location;
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

	/** Current lifecycle phase visible in the official overlays. */
	public enum Phase
	{
		INACTIVE,
		ON_LANDER,
		IN_GAME
	}

	/** Lander difficulty. */
	public enum Difficulty
	{
		NOVICE,
		INTERMEDIATE,
		VETERAN,
		UNKNOWN;

		private static Difficulty fromText(String text)
		{
			for (Difficulty difficulty : values())
			{
				if (difficulty != UNKNOWN && difficulty.name().equalsIgnoreCase(text.trim()))
				{
					return difficulty;
				}
			}
			return UNKNOWN;
		}
	}

	/** Server-controlled portal shield visibility. */
	public enum ShieldState
	{
		UNKNOWN,
		SHIELDED,
		DROPPED
	}

	/** One colour-coded Pest Control portal. */
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Portal
	{
		PURPLE("W", InterfaceID.PestStatusOverlay.PEST_STATUS_PORTTXT1,
			InterfaceID.PestStatusOverlay.PURPLE_SHIELD),
		BLUE("E", InterfaceID.PestStatusOverlay.PEST_STATUS_PORTTXT2,
			InterfaceID.PestStatusOverlay.BLUE_SHIELD),
		YELLOW("SE", InterfaceID.PestStatusOverlay.PEST_STATUS_PORTTXT3,
			InterfaceID.PestStatusOverlay.YELLOW_SHIELD),
		RED("SW", InterfaceID.PestStatusOverlay.PEST_STATUS_PORTTXT4,
			InterfaceID.PestStatusOverlay.RED_SHIELD);

		/** @return compass direction shown by the official HUD */
		@Getter
		private final String direction;
		private final int healthComponent;
		private final int shieldComponent;
	}

	/** Immutable state for one portal. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class PortalState
	{
		/** @return portal colour */ private final Portal portal;
		/** @return current hitpoints, or -1 when unavailable */ private final int hitpoints;
		/** @return current server-controlled shield visibility */ private final ShieldState shieldState;

		/** @return whether the portal has zero hitpoints */
		public boolean isDestroyed()
		{
			return hitpoints == 0;
		}

		/** @return whether the portal's shield is visibly active */
		public boolean isShielded()
		{
			return shieldState == ShieldState.SHIELDED;
		}
	}

	/** Immutable Pest Control state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return current official-overlay phase */ private final Phase phase;
		/** @return whether the in-game status HUD is visible */ private final boolean gameHudVisible;
		/** @return raw round-time text, or an empty string */ private final String roundTimeText;
		/** @return Void Knight hitpoints, or -1 outside a game */ private final int voidKnightHitpoints;
		/** @return current contribution, or -1 outside a game */ private final int contribution;
		/** @return raw activity value on the 0-50 scale */ private final int activity;
		/** @return activity percentage derived by the official clientscript */ private final int activityPercent;
		/** @return immutable state for all four portals */ private final Map<Portal, PortalState> portals;
		/** @return whether the lander status HUD is visible */ private final boolean landerHudVisible;
		/** @return raw next-departure text, or an empty string */ private final String departureText;
		/** @return approximate departure seconds shown by the HUD, or -1 */ private final int departureSeconds;
		/** @return players currently ready on the lander, or -1 */ private final int playersReady;
		/** @return Pest Points shown on the lander, or -1 */ private final int pestPoints;
		/** @return lander difficulty, or UNKNOWN */ private final Difficulty difficulty;
		/** @return whether the Pest Control collection-log section is complete */
		private final boolean collectionLogCompleted;
		/** @return whether the local player is at the Void Knights' Outpost */ private final boolean atOutpost;
		/** @return whether the local player is on the Pest Control battlefield */
		private final boolean onBattlefield;

		/**
		 * Returns one portal snapshot.
		 *
		 * @param portal portal colour
		 * @return immutable portal state
		 */
		public PortalState getPortal(Portal portal)
		{
			return portals.get(Objects.requireNonNull(portal, "portal"));
		}

		/** @return whether the player is at either Pest Control location */
		public boolean isAtPestControl()
		{
			return atOutpost || onBattlefield;
		}
	}
}
