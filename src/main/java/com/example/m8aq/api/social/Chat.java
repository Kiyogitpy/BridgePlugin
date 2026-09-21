package com.example.m8aq.api.social;

import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.ChatLineBuffer;
import net.runelite.api.ChatMessageType;
import net.runelite.api.Client;
import net.runelite.api.MessageNode;
import net.runelite.client.util.Text;

/** Provides the 100 newest non-private chat messages currently retained by the client. */
public final class Chat
{
	private static final int LIMIT = 100;
	private static final Set<ChatMessageType> PRIVATE_TYPES = EnumSet.of(ChatMessageType.PRIVATECHAT,
		ChatMessageType.PRIVATECHATOUT, ChatMessageType.MODPRIVATECHAT);

	private Chat()
	{
	}

	/**
	 * Reads retained chat while deliberately excluding incoming and outgoing private messages.
	 *
	 * @param client RuneLite client
	 * @return immutable privacy-filtered chat snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Map<Integer, ChatEntry> unique = new LinkedHashMap<>();
		Map<Integer, ChatLineBuffer> buffers = client.getChatLineMap();
		if (buffers != null)
		{
			for (ChatLineBuffer buffer : buffers.values())
			{
				if (buffer == null || buffer.getLines() == null)
				{
					continue;
				}
				for (MessageNode message : buffer.getLines())
				{
					if (message != null && !PRIVATE_TYPES.contains(message.getType()))
					{
						unique.putIfAbsent(message.getId(), new ChatEntry(message.getId(), message.getType(),
							plain(message.getName()), plain(message.getSender()), plain(message.getValue()),
							message.getTimestamp()));
					}
				}
			}
		}
		List<ChatEntry> messages = new ArrayList<>(unique.values());
		messages.sort((left, right) -> Integer.compare(right.timestamp, left.timestamp));
		if (messages.size() > LIMIT)
		{
			messages = new ArrayList<>(messages.subList(0, LIMIT));
		}
		return new State(Collections.unmodifiableList(messages));
	}

	private static String plain(String value)
	{
		return value == null ? null : Text.removeTags(value);
	}

	/** Immutable non-private chat message. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class ChatEntry
	{
		/** @return client message ID */ private final int id;
		/** @return message channel/type */ private final ChatMessageType type;
		/** @return display name field, or null */ private final String name;
		/** @return sender field, or null */ private final String sender;
		/** @return message text without game formatting tags */ private final String value;
		/** @return client timestamp */ private final int timestamp;
	}

	/** Immutable privacy-filtered chat state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return up to 100 newest non-private retained messages */ private final List<ChatEntry> messages;
	}
}
