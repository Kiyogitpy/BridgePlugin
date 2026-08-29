package net.runelite.client.plugins.m8aq.api.minigames;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.DecorativeObject;
import net.runelite.api.Player;
import net.runelite.api.Scene;
import net.runelite.api.Tile;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.ObjectID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;

/** Provides a read-only snapshot of the client-observable Tears of Guthix state. */
public final class TearsOfGuthix
{
	private static final int TEARS_OF_GUTHIX_REGION = 12948;
	private static final double SECONDS_PER_TICK = 0.6;
	private static final int[][] ADJACENT_WALL_OFFSETS = {{1, 0}, {0, 1}, {0, -1}};

	private TearsOfGuthix()
	{
	}

	/**
	 * Reads the current Tears of Guthix state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Player player = client.getLocalPlayer();
		WorldPoint location = player == null ? null : player.getWorldLocation();
		boolean atTearsOfGuthix = location != null
			&& location.getRegionID() == TEARS_OF_GUTHIX_REGION;
		boolean hudVisible = isVisible(client.getWidget(InterfaceID.TogSidepanel.UNIVERSE));
		boolean collecting = client.getVarbitValue(VarbitID.TOG_MINIGAME_COLLECTING) == 1;
		int ticksRemaining = client.getVarbitValue(VarbitID.TOG_COUNTDOWN);
		int questPoints = client.getVarpValue(VarPlayerID.QP);
		StreamReading stream = readCollectingStream(client, player, collecting);

		return new State(
			hudVisible,
			atTearsOfGuthix,
			hudVisible && ticksRemaining > 0,
			collecting,
			stream.state,
			stream.objectId,
			client.getVarbitValue(VarbitID.TOG_TEARS_COLLECTED),
			client.getVarbitValue(VarbitID.TOG_MAX_TEARS_COLLECTED),
			ticksRemaining,
			ticksRemaining * SECONDS_PER_TICK,
			questPoints,
			questPoints > 0 ? percent(ticksRemaining, questPoints) : -1);
	}

	private static StreamReading readCollectingStream(
		Client client,
		Player player,
		boolean collecting)
	{
		if (!collecting)
		{
			return new StreamReading(CollectingState.NOT_COLLECTING, -1);
		}
		WorldView worldView = client.getTopLevelWorldView();
		LocalPoint location = player == null ? null : player.getLocalLocation();
		Scene scene = worldView == null ? null : worldView.getScene();
		Tile[][][] tiles = scene == null ? null : scene.getTiles();
		if (location == null || tiles == null)
		{
			return new StreamReading(CollectingState.UNKNOWN, -1);
		}

		int plane = worldView.getPlane();
		for (int[] offset : ADJACENT_WALL_OFFSETS)
		{
			int sceneX = location.getSceneX() + offset[0];
			int sceneY = location.getSceneY() + offset[1];
			if (plane < 0 || plane >= tiles.length
				|| sceneX < 0 || sceneX >= tiles[plane].length
				|| sceneY < 0 || sceneY >= tiles[plane][sceneX].length)
			{
				continue;
			}
			Tile tile = tiles[plane][sceneX][sceneY];
			DecorativeObject object = tile == null ? null : tile.getDecorativeObject();
			if (object != null)
			{
				CollectingState state = CollectingState.fromObjectId(object.getId());
				if (state != CollectingState.UNKNOWN)
				{
					return new StreamReading(state, object.getId());
				}
			}
		}
		return new StreamReading(CollectingState.UNKNOWN, -1);
	}

	private static boolean isVisible(Widget widget)
	{
		return widget != null && !widget.isHidden();
	}

	private static double percent(int value, int maximum)
	{
		return Math.max(0, Math.min(maximum, value)) * 100.0 / maximum;
	}

	/** Type of stream beside the player while the collection flag is active. */
	public enum CollectingState
	{
		NOT_COLLECTING,
		BLUE,
		GREEN,
		EMPTY,
		UNKNOWN;

		private static CollectingState fromObjectId(int objectId)
		{
			if (objectId == ObjectID.TOG_WEEPING_WALL_GOOD_R
				|| objectId == ObjectID.TOG_WEEPING_WALL_GOOD_L)
			{
				return BLUE;
			}
			if (objectId == ObjectID.TOG_WEEPING_WALL_BAD_R
				|| objectId == ObjectID.TOG_WEEPING_WALL_BAD_L)
			{
				return GREEN;
			}
			if (objectId == ObjectID.TOG_WEEPING_WALL_OFF_R
				|| objectId == ObjectID.TOG_WEEPING_WALL_OFF_L)
			{
				return EMPTY;
			}
			return UNKNOWN;
		}
	}

	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	private static final class StreamReading
	{
		private final CollectingState state;
		private final int objectId;
	}

	/** Immutable Tears of Guthix state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the Tears sidepanel is visible */ private final boolean hudVisible;
		/** @return whether the player is in the Tears of Guthix cave region */
		private final boolean atTearsOfGuthix;
		/** @return whether the sidepanel has a positive server countdown */
		private final boolean sessionActive;
		/** @return raw server collecting flag */ private final boolean collecting;
		/** @return decoded adjacent stream while collecting */ private final CollectingState collectingState;
		/** @return raw adjacent stream object ID, or -1 when unavailable */
		private final int collectingObjectId;
		/** @return tears collected in the current session */ private final int tearsCollected;
		/** @return personal-best tear count */ private final int personalBest;
		/** @return server game ticks remaining in the session */ private final int ticksRemaining;
		/** @return seconds represented by the server countdown */ private final double secondsRemaining;
		/** @return current quest points, which determine the initial countdown */
		private final int questPoints;
		/** @return countdown remaining as a percentage of quest points, or -1 */
		private final double timeRemainingPercent;
	}
}
