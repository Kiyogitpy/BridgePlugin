package net.runelite.client.plugins.m8aq.api.player;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;

/** Provides a read-only snapshot of the local player's coordinates. */
public final class Location
{
	private Location()
	{
	}

	/**
	 * Reads the current local-player location from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable location snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Player player = client.getLocalPlayer();
		if (player == null)
		{
			return unavailable();
		}

		WorldPoint worldPoint = player.getWorldLocation();
		LocalPoint localPoint = player.getLocalLocation();
		WorldView worldView = player.getWorldView();
		if (worldPoint == null || localPoint == null || worldView == null)
		{
			return unavailable();
		}

		return new State(
			worldPoint,
			localPoint,
			worldPoint.getRegionID(),
			worldView.isInstance(),
			worldView.getId());
	}

	private static State unavailable()
	{
		return new State(null, null, -1, false, -1);
	}

	/** Immutable local-player location captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return server/world coordinate, or {@code null} when unavailable */
		private final WorldPoint worldPoint;
		/** @return rendered local coordinate, or {@code null} when unavailable */
		private final LocalPoint localPoint;
		/** @return map-region ID, or {@code -1} when unavailable */
		private final int regionId;
		/** @return whether the actor's world view is instanced */
		private final boolean instanced;
		/** @return actor world-view ID, or {@code -1} when unavailable */
		private final int worldViewId;
	}
}
