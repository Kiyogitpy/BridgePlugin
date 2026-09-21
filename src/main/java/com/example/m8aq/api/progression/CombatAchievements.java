package com.example.m8aq.api.progression;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;

/** Provides readable combat-achievement points, task count, and reward tier. */
public final class CombatAchievements
{
	private static final int[] COMPLETION_VARPS = {VarPlayerID.CA_TASK_COMPLETED_0, VarPlayerID.CA_TASK_COMPLETED_1,
		VarPlayerID.CA_TASK_COMPLETED_2, VarPlayerID.CA_TASK_COMPLETED_3, VarPlayerID.CA_TASK_COMPLETED_4,
		VarPlayerID.CA_TASK_COMPLETED_5, VarPlayerID.CA_TASK_COMPLETED_6, VarPlayerID.CA_TASK_COMPLETED_7,
		VarPlayerID.CA_TASK_COMPLETED_8, VarPlayerID.CA_TASK_COMPLETED_9, VarPlayerID.CA_TASK_COMPLETED_10,
		VarPlayerID.CA_TASK_COMPLETED_11, VarPlayerID.CA_TASK_COMPLETED_12, VarPlayerID.CA_TASK_COMPLETED_13,
		VarPlayerID.CA_TASK_COMPLETED_14, VarPlayerID.CA_TASK_COMPLETED_15, VarPlayerID.CA_TASK_COMPLETED_16,
		VarPlayerID.CA_TASK_COMPLETED_17, VarPlayerID.CA_TASK_COMPLETED_18, VarPlayerID.CA_TASK_COMPLETED_19,
		VarPlayerID.CA_TASK_COMPLETED_20};

	private CombatAchievements()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable combat-achievement snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		int points = client.getVarbitValue(VarbitID.CA_POINTS);
		Map<String, Integer> thresholds = new LinkedHashMap<>();
		thresholds.put("Easy", client.getVarbitValue(VarbitID.CA_THRESHOLD_EASY));
		thresholds.put("Medium", client.getVarbitValue(VarbitID.CA_THRESHOLD_MEDIUM));
		thresholds.put("Hard", client.getVarbitValue(VarbitID.CA_THRESHOLD_HARD));
		thresholds.put("Elite", client.getVarbitValue(VarbitID.CA_THRESHOLD_ELITE));
		thresholds.put("Master", client.getVarbitValue(VarbitID.CA_THRESHOLD_MASTER));
		thresholds.put("Grandmaster", client.getVarbitValue(VarbitID.CA_THRESHOLD_GRANDMASTER));
		int completedTasks = 0;
		for (int varp : COMPLETION_VARPS)
		{
			completedTasks += Integer.bitCount(client.getVarpValue(varp));
		}
		String rewardTier = "None";
		for (Map.Entry<String, Integer> threshold : thresholds.entrySet())
		{
			if (threshold.getValue() > 0 && points >= threshold.getValue())
			{
				rewardTier = threshold.getKey();
			}
		}
		return new State(points, Collections.unmodifiableMap(thresholds), completedTasks, rewardTier);
	}

	/** Immutable combat-achievement state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return current combat-achievement points */ private final int points;
		/** @return reward-tier names mapped to their current point thresholds */ private final Map<String, Integer> tierThresholds;
		/** @return number of completed combat-achievement tasks */ private final int completedTaskCount;
		/** @return highest reward tier currently earned, or {@code None} */ private final String achievedRewardTier;
	}
}
