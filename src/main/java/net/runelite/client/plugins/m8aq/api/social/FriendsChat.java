package net.runelite.client.plugins.m8aq.api.social;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.FriendsChatManager;
import net.runelite.api.FriendsChatMember;
import net.runelite.api.FriendsChatRank;

/** Provides copied state for the currently joined Friends Chat. */
public final class FriendsChat
{
	private FriendsChat()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable Friends Chat snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		FriendsChatManager manager = client.getFriendsChatManager();
		if (manager == null)
		{
			return new State(null, null, null, null, Collections.emptyList());
		}
		List<Member> members = new ArrayList<>();
		if (manager.getMembers() != null)
		{
			for (FriendsChatMember member : manager.getMembers())
			{
				if (member != null)
				{
					members.add(new Member(member.getName(), member.getPrevName(), member.getWorld(), member.getRank()));
				}
			}
		}
		return new State(manager.getName(), manager.getOwner(), manager.getMyRank(), manager.getKickRank(),
			Collections.unmodifiableList(members));
	}

	/** Immutable Friends Chat member. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Member
	{
		/** @return current display name */ private final String name;
		/** @return previous display name, or null */ private final String previousName;
		/** @return current world */ private final int world;
		/** @return Friends Chat rank */ private final FriendsChatRank rank;
	}

	/** Immutable Friends Chat state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return channel name, or null */ private final String name;
		/** @return channel owner, or null */ private final String owner;
		/** @return local member rank, or null */ private final FriendsChatRank localRank;
		/** @return minimum kick rank, or null */ private final FriendsChatRank kickRank;
		/** @return immutable channel members */ private final List<Member> members;
	}
}
