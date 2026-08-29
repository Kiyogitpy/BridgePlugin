package net.runelite.client.plugins.m8aq.api.runtime;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.gameval.VarClientID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;

/** Provides a safe read-only snapshot of account mode and membership state. */
public final class Account
{
	private Account()
	{
	}

	/**
	 * Reads the current account state without login identity or credentials.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable account snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (client.getGameState() != GameState.LOGGED_IN)
		{
			return new State(Mode.UNKNOWN, false, false, false, -1);
		}

		int rawAccountType = client.getVarbitValue(VarbitID.IRONMAN);
		Mode mode = Mode.fromRaw(rawAccountType);
		boolean ironman = rawAccountType >= 1 && rawAccountType <= 6;
		boolean groupIronman = rawAccountType >= 4 && rawAccountType <= 6;
		return new State(
			mode,
			ironman,
			groupIronman,
			client.getVarcIntValue(VarClientID.PLAYERMEMBER) == 1,
			client.getVarpValue(VarPlayerID.ACCOUNT_CREDIT));
	}

	/** Named values of the raw account-mode variable. */
	public enum Mode
	{
		NORMAL(0),
		IRONMAN(1),
		ULTIMATE_IRONMAN(2),
		HARDCORE_IRONMAN(3),
		GROUP_IRONMAN(4),
		HARDCORE_GROUP_IRONMAN(5),
		UNRANKED_GROUP_IRONMAN(6),
		UNKNOWN(-1);

		private final int rawValue;

		Mode(int rawValue)
		{
			this.rawValue = rawValue;
		}

		private static Mode fromRaw(int rawValue)
		{
			for (Mode mode : values())
			{
				if (mode.rawValue == rawValue)
				{
					return mode;
				}
			}
			return UNKNOWN;
		}
	}

	/** Immutable safe account state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return named account mode or {@link Mode#UNKNOWN} */
		private final Mode mode;
		/** @return whether the raw value is a known Ironman mode */
		private final boolean ironman;
		/** @return whether the raw value is a known Group Ironman mode */
		private final boolean groupIronman;
		/** @return whether the account currently has active membership */
		private final boolean member;
		/** @return membership days remaining, zero when active without credit, or -1 when unavailable */
		private final int membershipDays;
	}
}
