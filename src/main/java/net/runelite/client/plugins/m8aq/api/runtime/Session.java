package net.runelite.client.plugins.m8aq.api.runtime;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;

/** Provides a read-only snapshot of the client's current lifecycle and counters. */
public final class Session
{
	private Session()
	{
	}

	/**
	 * Reads the current client session state.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable session snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		GameState gameState = client.getGameState();
		return new State(
			gameState,
			gameState == GameState.LOGGED_IN,
			client.getTickCount(),
			client.getGameCycle());
	}

	/** Immutable client session state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return current RuneLite game state */
		private final GameState gameState;
		/** @return whether the client is fully logged in */
		private final boolean loggedIn;
		/** @return current server tick count */
		private final int tickCount;
		/** @return current 20-millisecond client cycle */
		private final int gameCycle;
	}
}
