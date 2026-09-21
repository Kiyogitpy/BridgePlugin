package com.example.m8aq.api.runtime;

import java.util.Collections;
import java.util.EnumSet;
import java.util.Objects;
import java.util.Set;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.WorldType;

/** Provides a read-only snapshot of the client's selected game world. */
public final class World
{
	private World()
	{
	}

	/**
	 * Reads the current world selection and connection state.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable world snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		EnumSet<WorldType> rawTypes = client.getWorldType();
		Set<WorldType> worldTypes = rawTypes == null
			? Collections.emptySet()
			: Collections.unmodifiableSet(EnumSet.copyOf(rawTypes));
		return new State(
			client.getWorld(),
			worldTypes,
			worldTypes.contains(WorldType.MEMBERS),
			WorldType.isPvpWorld(worldTypes));
	}

	/** Immutable selected-world state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return selected world ID; during a hop this may be the pending target */
		private final int worldId;
		/** @return immutable selected-world type flags */
		private final Set<WorldType> worldTypes;
		/** @return whether the selected world has the members flag */
		private final boolean membersWorld;
		/** @return whether RuneLite classifies the selected world as PvP */
		private final boolean pvpWorld;
	}
}
