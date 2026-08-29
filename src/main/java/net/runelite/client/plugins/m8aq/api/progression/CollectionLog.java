package net.runelite.client.plugins.m8aq.api.progression;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.VarPlayerID;

/** Provides persisted collection-log totals by category. */
public final class CollectionLog
{
	private CollectionLog()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable collection-log snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Map<String, Category> categories = new LinkedHashMap<>();
		categories.put("Bosses", category(client, VarPlayerID.COLLECTION_COUNT_BOSSES, VarPlayerID.COLLECTION_COUNT_BOSSES_MAX));
		categories.put("Raids", category(client, VarPlayerID.COLLECTION_COUNT_RAIDS, VarPlayerID.COLLECTION_COUNT_RAIDS_MAX));
		categories.put("Clues", category(client, VarPlayerID.COLLECTION_COUNT_CLUES, VarPlayerID.COLLECTION_COUNT_CLUES_MAX));
		categories.put("Minigames", category(client, VarPlayerID.COLLECTION_COUNT_MINIGAMES, VarPlayerID.COLLECTION_COUNT_MINIGAMES_MAX));
		categories.put("Other", category(client, VarPlayerID.COLLECTION_COUNT_OTHER, VarPlayerID.COLLECTION_COUNT_OTHER_MAX));
		return new State(client.getVarpValue(VarPlayerID.COLLECTION_COUNT),
			client.getVarpValue(VarPlayerID.COLLECTION_COUNT_MAX),
			Collections.unmodifiableMap(categories));
	}

	private static Category category(Client client, int countVarp, int maximumVarp)
	{
		return new Category(client.getVarpValue(countVarp), client.getVarpValue(maximumVarp));
	}

	/** Immutable collection category total. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Category
	{
		/** @return collected entries */ private final int count;
		/** @return total entries */ private final int maximum;
	}

	/** Immutable collection-log state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return collected entries across the log */ private final int count;
		/** @return total entries across the log */ private final int maximum;
		/** @return immutable category totals */ private final Map<String, Category> categories;
	}
}
