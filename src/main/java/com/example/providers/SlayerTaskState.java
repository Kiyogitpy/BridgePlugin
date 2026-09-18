package com.example.providers;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * One read of the slayer task varps/varbits, already decoded -- the value SlayerProvider
 * serves on GET /slayer. Pure so the rules are testable without a client (SlayerTaskStateTest),
 * the same split RecordRing and BankSnapshot got.
 *
 * Field sources (RuneLite 1.12.39, plugins/slayer/SlayerPlugin.updateTask): count = varp 394
 * SLAYER_COUNT, originalCount = varp 4258 SLAYER_COUNT_ORIGINAL, taskId = varp 395
 * SLAYER_TARGET, areaId = varp 2096 SLAYER_AREA (Konar only), masterId = varbit 4067
 * SLAYER_MASTER, streak = varbit 4069 SLAYER_TASKS_COMPLETED, points = varbit 4068
 * SLAYER_POINTS, bossId = varbit 4723 SLAYER_TARGET_BOSSID. taskName/areaName come from DB
 * tables 113/115 and are null when the lookup fails -- never "" -- so the Python side can tell
 * "no task" (count 0) from "could not decode" (count > 0, name null).
 */
public final class SlayerTaskState
{
	public final int count;
	public final int originalCount;
	public final int taskId;
	public final String taskName;
	public final int areaId;
	public final String areaName;
	public final int masterId;
	public final int streak;
	public final int points;
	public final int bossId;

	public SlayerTaskState(int count, int originalCount, int taskId, String taskName,
		int areaId, String areaName, int masterId, int streak, int points, int bossId)
	{
		this.count = count;
		this.originalCount = originalCount;
		this.taskId = taskId;
		this.taskName = taskName;
		this.areaId = areaId;
		this.areaName = areaName;
		this.masterId = masterId;
		this.streak = streak;
		this.points = points;
		this.bossId = bossId;
	}

	/** Completion is "count went to 0" -- exactly how SlayerPlugin detects it (:448-452). */
	public boolean hasTask()
	{
		return count > 0;
	}

	public Map<String, Object> toMap()
	{
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("count", count);
		m.put("originalCount", originalCount);
		m.put("taskId", taskId);
		m.put("taskName", taskName);
		m.put("areaId", areaId);
		m.put("areaName", areaName);
		m.put("masterId", masterId);
		m.put("streak", streak);
		m.put("points", points);
		m.put("bossId", bossId);
		m.put("hasTask", hasTask());
		return m;
	}
}
