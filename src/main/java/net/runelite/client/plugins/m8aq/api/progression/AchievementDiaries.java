package net.runelite.client.plugins.m8aq.api.progression;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.VarbitID;

/** Provides readable completed-task counts for every achievement diary tier. */
public final class AchievementDiaries
{
	private AchievementDiaries()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable readable diary snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Map<String, Region> regions = new LinkedHashMap<>();
		put(regions, "Karamja", client, VarbitID.KARAMJA_EASY_COUNT, VarbitID.KARAMJA_MED_COUNT,
			VarbitID.KARAMJA_HARD_COUNT, VarbitID.KARAMJA_ELITE_COUNT);
		put(regions, "Varrock", client, VarbitID.VARROCK_EASY_COUNT, VarbitID.VARROCK_MED_COUNT,
			VarbitID.VARROCK_HARD_COUNT, VarbitID.VARROCK_ELITE_COUNT);
		put(regions, "Kandarin", client, VarbitID.KANDARIN_EASY_COUNT, VarbitID.KANDARIN_MED_COUNT,
			VarbitID.KANDARIN_HARD_COUNT, VarbitID.KANDARIN_ELITE_COUNT);
		put(regions, "Morytania", client, VarbitID.MORYTANIA_EASY_COUNT, VarbitID.MORYTANIA_MED_COUNT,
			VarbitID.MORYTANIA_HARD_COUNT, VarbitID.MORYTANIA_ELITE_COUNT);
		put(regions, "Western Provinces", client, VarbitID.WESTERN_EASY_COUNT, VarbitID.WESTERN_MED_COUNT,
			VarbitID.WESTERN_HARD_COUNT, VarbitID.WESTERN_ELITE_COUNT);
		put(regions, "Fremennik", client, VarbitID.FREMENNIK_EASY_COUNT, VarbitID.FREMENNIK_MED_COUNT,
			VarbitID.FREMENNIK_HARD_COUNT, VarbitID.FREMENNIK_ELITE_COUNT);
		put(regions, "Falador", client, VarbitID.FALADOR_EASY_COUNT, VarbitID.FALADOR_MED_COUNT,
			VarbitID.FALADOR_HARD_COUNT, VarbitID.FALADOR_ELITE_COUNT);
		put(regions, "Wilderness", client, VarbitID.WILDERNESS_EASY_COUNT, VarbitID.WILDERNESS_MED_COUNT,
			VarbitID.WILDERNESS_HARD_COUNT, VarbitID.WILDERNESS_ELITE_COUNT);
		put(regions, "Lumbridge & Draynor", client, VarbitID.LUMBRIDGE_EASY_COUNT, VarbitID.LUMBRIDGE_MED_COUNT,
			VarbitID.LUMBRIDGE_HARD_COUNT, VarbitID.LUMBRIDGE_ELITE_COUNT);
		put(regions, "Ardougne", client, VarbitID.ARDOUGNE_EASY_COUNT, VarbitID.ARDOUGNE_MED_COUNT,
			VarbitID.ARDOUGNE_HARD_COUNT, VarbitID.ARDOUGNE_ELITE_COUNT);
		put(regions, "Desert", client, VarbitID.DESERT_EASY_COUNT, VarbitID.DESERT_MED_COUNT,
			VarbitID.DESERT_HARD_COUNT, VarbitID.DESERT_ELITE_COUNT);
		put(regions, "Kourend & Kebos", client, VarbitID.KOUREND_EASY_COUNT, VarbitID.KOUREND_MED_COUNT,
			VarbitID.KOUREND_HARD_COUNT, VarbitID.KOUREND_ELITE_COUNT);
		return new State(Collections.unmodifiableMap(regions), regions.values().stream()
			.mapToInt(Region::getCompletedTasks).sum());
	}

	private static void put(Map<String, Region> regions, String name, Client client,
		int easyVarbit, int mediumVarbit, int hardVarbit, int eliteVarbit)
	{
		int easy = client.getVarbitValue(easyVarbit);
		int medium = client.getVarbitValue(mediumVarbit);
		int hard = client.getVarbitValue(hardVarbit);
		int elite = client.getVarbitValue(eliteVarbit);
		regions.put(name, new Region(easy, medium, hard, elite, easy + medium + hard + elite));
	}

	/** Immutable completed-task counts for one diary region. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Region
	{
		/** @return completed easy tasks */ private final int easyCompleted;
		/** @return completed medium tasks */ private final int mediumCompleted;
		/** @return completed hard tasks */ private final int hardCompleted;
		/** @return completed elite tasks */ private final int eliteCompleted;
		/** @return completed tasks across all four tiers */ private final int completedTasks;
	}

	/** Immutable readable achievement-diary state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return region names mapped to readable completed-task counts */ private final Map<String, Region> regions;
		/** @return completed tasks across every diary */ private final int completedTasks;
	}
}
