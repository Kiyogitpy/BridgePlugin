package net.runelite.client.plugins.m8aq.api.player;

import java.util.Collections;
import java.util.EnumSet;
import java.util.Objects;
import java.util.Set;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.VarbitID;

/** Provides a read-only snapshot of the local player's prayer points and selections. */
public final class Prayer
{
	private Prayer()
	{
	}

	/**
	 * Reads current prayer state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable prayer snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (client.getLocalPlayer() == null)
		{
			return new State(PrayerBook.UNKNOWN, Collections.emptySet(), false);
		}

		EnumSet<net.runelite.api.Prayer> active = EnumSet.noneOf(net.runelite.api.Prayer.class);
		for (net.runelite.api.Prayer prayer : net.runelite.api.Prayer.values())
		{
			if (client.getVarbitValue(prayer.getVarbit()) == 1)
			{
				active.add(prayer);
			}
		}

		return new State(
			prayerBook(client.getVarbitValue(VarbitID.PRAYERBOOK)),
			Collections.unmodifiableSet(active),
			client.getVarbitValue(VarbitID.QUICKPRAYER_ACTIVE) == 1);
	}

	private static PrayerBook prayerBook(int value)
	{
		return value == 0 ? PrayerBook.STANDARD : value == 1 ? PrayerBook.RUINOUS_POWERS : PrayerBook.UNKNOWN;
	}

	/** Named prayer-book category. */
	public enum PrayerBook
	{
		STANDARD,
		RUINOUS_POWERS,
		UNKNOWN
	}

	/** Immutable prayer state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return named current prayer book */
		private final PrayerBook prayerBook;
		/** @return immutable set of prayers currently active */
		private final Set<net.runelite.api.Prayer> activePrayers;
		/** @return whether quick prayers are currently active */
		private final boolean quickPrayersActive;
	}
}
