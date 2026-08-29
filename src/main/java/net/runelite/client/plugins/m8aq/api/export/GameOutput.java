package net.runelite.client.plugins.m8aq.api.export;

import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;

/** Provides one immutable aggregate of every implemented game API reader. */
public final class GameOutput
{
	private GameOutput()
	{
	}

	/**
	 * Captures every implemented reader during the current client-thread task.
	 *
	 * @param client RuneLite client
	 * @return immutable grouped game state
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		return new State(client.getTickCount(), ReaderCatalog.captureAll(client));
	}

	/** Immutable whole-game snapshot grouped by API directory. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return game tick at the end of this capture */
		private final int capturedTick;
		/** @return immutable directory and reader-name map */
		private final Map<String, Map<String, Object>> groups;
	}
}
