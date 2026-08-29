package net.runelite.client.plugins.m8aq.api.social;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Friend;
import net.runelite.api.FriendContainer;

/** Provides copied names and online worlds from the local friend list. */
public final class Friends
{
	private Friends()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable friend-list snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		FriendContainer container = client.getFriendContainer();
		List<FriendEntry> entries = new ArrayList<>();
		if (container != null && container.getMembers() != null)
		{
			for (Friend friend : container.getMembers())
			{
				if (friend != null)
				{
					entries.add(new FriendEntry(friend.getName(), friend.getPrevName(), friend.getWorld()));
				}
			}
		}
		return new State(Collections.unmodifiableList(entries));
	}

	/** Immutable friend-list entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class FriendEntry
	{
		/** @return current display name */ private final String name;
		/** @return previous display name, or null */ private final String previousName;
		/** @return online world, or zero when offline */ private final int world;
	}

	/** Immutable friend-list state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return immutable friend entries */ private final List<FriendEntry> friends;
	}
}
