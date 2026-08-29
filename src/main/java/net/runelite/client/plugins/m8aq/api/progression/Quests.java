package net.runelite.client.plugins.m8aq.api.progression;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.Quest;
import net.runelite.api.QuestState;

/** Provides quest and miniquest progression from RuneLite's maintained quest catalogue. */
public final class Quests
{
	private Quests()
	{
	}

	/**
	 * Reads all quest states. This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable quest snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (client.getGameState() != GameState.LOGGED_IN)
		{
			return new State(Collections.emptyList(), 0, 0, 0);
		}

		List<QuestEntry> entries = new ArrayList<>();
		int completed = 0;
		int inProgress = 0;
		int notStarted = 0;
		for (Quest quest : Quest.values())
		{
			QuestState questState = quest.getState(client);
			entries.add(new QuestEntry(quest.getId(), quest.getName(), questState));
			if (questState == QuestState.FINISHED)
			{
				completed++;
			}
			else if (questState == QuestState.IN_PROGRESS)
			{
				inProgress++;
			}
			else
			{
				notStarted++;
			}
		}
		return new State(Collections.unmodifiableList(entries), completed, inProgress, notStarted);
	}

	/** Immutable quest entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class QuestEntry
	{
		/** @return RuneLite quest ID */ private final int id;
		/** @return canonical quest name */ private final String name;
		/** @return current quest state */ private final QuestState state;
	}

	/** Immutable quest-state collection. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return immutable quests in RuneLite catalogue order */ private final List<QuestEntry> quests;
		/** @return number of finished quests */ private final int completedCount;
		/** @return number of quests in progress */ private final int inProgressCount;
		/** @return number of quests not started */ private final int notStartedCount;
	}
}
