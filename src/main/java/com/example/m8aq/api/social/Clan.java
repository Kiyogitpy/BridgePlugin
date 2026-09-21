package com.example.m8aq.api.social;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.clan.ClanChannel;
import net.runelite.api.clan.ClanChannelMember;
import net.runelite.api.clan.ClanID;
import net.runelite.api.clan.ClanRank;
import net.runelite.api.clan.ClanSettings;
import net.runelite.api.clan.ClanTitle;

/** Provides copied state for the player's clan and currently visited guest clan. */
public final class Clan
{
	private Clan()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable clan snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		return new State(read(client.getClanChannel(), client.getClanSettings()),
			read(client.getGuestClanChannel(), client.getGuestClanSettings()),
			read(client.getClanChannel(ClanID.GROUP_IRONMAN), client.getClanSettings(ClanID.GROUP_IRONMAN)));
	}

	static Channel read(ClanChannel channel, ClanSettings settings)
	{
		if (channel == null && settings == null)
		{
			return null;
		}
		List<Member> members = new ArrayList<>();
		if (channel != null && channel.getMembers() != null)
		{
			for (ClanChannelMember member : channel.getMembers())
			{
				if (member != null)
				{
					ClanRank rank = member.getRank();
					ClanTitle title = settings == null || rank == null ? null : settings.titleForRank(rank);
					members.add(new Member(member.getName(), member.getWorld(), rank == null ? -1 : rank.getRank(),
						title == null ? null : title.getName()));
				}
			}
		}
		return new Channel(channel == null ? null : channel.getName(), settings == null ? null : settings.getName(),
			Collections.unmodifiableList(members));
	}

	/** Immutable clan channel. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Channel
	{
		/** @return live channel name, or null */ private final String channelName;
		/** @return persistent settings name, or null */ private final String settingsName;
		/** @return immutable online channel members */ private final List<Member> members;
	}

	/** Immutable online clan member. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Member
	{
		/** @return display name */ private final String name;
		/** @return current world */ private final int world;
		/** @return raw clan rank, or -1 */ private final int rank;
		/** @return configured rank title, or null */ private final String title;
	}

	/** Immutable own, guest, and Group Ironman clan state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return own clan, or null */ private final Channel ownClan;
		/** @return guest clan, or null */ private final Channel guestClan;
		/** @return Group Ironman account group, or null */ private final Channel accountGroup;
	}
}
