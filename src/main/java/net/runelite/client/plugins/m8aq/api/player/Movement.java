package net.runelite.client.plugins.m8aq.api.player;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.gameval.VarPlayerID;

/** Provides a read-only snapshot of the local player's movement controls and resources. */
public final class Movement
{
	private Movement()
	{
	}

	/**
	 * Reads current movement state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable movement snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (client.getLocalPlayer() == null)
		{
			return new State(-1.0, -1, false, null);
		}

		int energy = client.getEnergy();
		return new State(
			energy / 100.0,
			client.getWeight(),
			client.getVarpValue(VarPlayerID.OPTION_RUN) == 1,
			client.getLocalDestinationLocation());
	}

	/** Immutable movement state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return run energy as a percentage, or -1 when unavailable */
		private final double runEnergyPercent;
		/** @return current carried weight, or -1 when unavailable */
		private final int weight;
		/** @return whether run mode is enabled */
		private final boolean runEnabled;
		/** @return selected local destination, or null when none/unavailable */
		private final LocalPoint destination;
	}
}
