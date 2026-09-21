package com.example.m8aq.api.progression;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.DBTableID;
import net.runelite.api.gameval.VarPlayerID;

/** Provides named current, previous, and overriding music tracks. */
public final class Music
{
	private Music()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable music snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		return new State(track(client, client.getVarpValue(VarPlayerID.MUSIC_CURRENT_TRACK)),
			track(client, client.getVarpValue(VarPlayerID.MUSIC_LAST_TRACK)),
			track(client, client.getVarpValue(VarPlayerID.MUSIC_OVERRIDE_TRACK)),
			client.getVarpValue(VarPlayerID.MUSICLOOP) != 0);
	}

	private static Track track(Client client, int rowId)
	{
		if (rowId <= 0)
		{
			return null;
		}
		try
		{
			Object[] values = client.getDBTableField(rowId, DBTableID.Music.COL_DISPLAYNAME, 0);
			String name = values != null && values.length > 0 && values[0] instanceof String
				? (String) values[0] : "Unknown track";
			return new Track(rowId, name);
		}
		catch (RuntimeException ignored)
		{
			return new Track(rowId, "Unknown track");
		}
	}

	/** Immutable named music track. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Track
	{
		/** @return game database row ID */ private final int id;
		/** @return display name, or {@code Unknown track} when the cache row cannot be read */ private final String name;
	}

	/** Immutable music-player state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return current named track, or null */ private final Track currentTrack;
		/** @return previous named track, or null */ private final Track previousTrack;
		/** @return temporary overriding named track, or null */ private final Track overrideTrack;
		/** @return whether looping is enabled */ private final boolean looping;
	}
}
