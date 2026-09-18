package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.gameval.DBTableID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.List;

/**
 * GET /slayer -- the current slayer task, streak and points, decoded the way RuneLite's own
 * SlayerPlugin.updateTask() does it (client 1.12.39, plugins/slayer/SlayerPlugin.java:362-453):
 * varps 394/395/4258/2096 + varbits 4067/4068/4069/4723, with the task NAME looked up in DB
 * table 113 (SlayerTask, COL_NAME_UPPERCASE) -- or, for the "Bosses" pseudo-task (id 98), via
 * table 116 (SlayerTaskSublist) keyed on varbit 4723.
 *
 * Added 2026-09-18 for the slayer task loop (spec §10). Nothing in the repo read task state
 * before this. No TickCache on purpose: these are varp reads, cheap and read fresh on the client
 * thread like /player. An UNREBUILT client answers 404 here (unlike /bank/memory, which fell
 * through to /bank by longest prefix) because /slayer-config is not a prefix of /slayer.
 *
 * masterId is the RAW varbit 4067. Only Krystilia (7) and Mortimer (10) are known upstream;
 * Aya's and Kuradal's values are read live in the spec's §12 checklist, not guessed here.
 */
public class SlayerProvider extends BridgeProvider
{
	/** SlayerPlugin.java:370 -- "Bosses, from [proc,helper_slayer_current_assignment]". */
	static final int BOSS_TASK_ID = 98;

	public SlayerProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/slayer", () -> onClient(this::read));
	}

	private Object read()
	{
		int count = client.getVarpValue(VarPlayerID.SLAYER_COUNT);
		int taskId = client.getVarpValue(VarPlayerID.SLAYER_TARGET);
		int original = client.getVarpValue(VarPlayerID.SLAYER_COUNT_ORIGINAL);
		int areaId = client.getVarpValue(VarPlayerID.SLAYER_AREA);
		int masterId = client.getVarbitValue(VarbitID.SLAYER_MASTER);
		int streak = client.getVarbitValue(VarbitID.SLAYER_TASKS_COMPLETED);
		int points = client.getVarbitValue(VarbitID.SLAYER_POINTS);
		int bossId = client.getVarbitValue(VarbitID.SLAYER_TARGET_BOSSID);

		String taskName = count > 0 ? taskName(taskId, bossId) : null;
		String areaName = areaId > 0 ? areaName(areaId) : null;

		return new SlayerTaskState(count, original, taskId, taskName, areaId, areaName,
			masterId, streak, points, bossId).toMap();
	}

	private String taskName(int taskId, int bossId)
	{
		try
		{
			int row;
			if (taskId == BOSS_TASK_ID)
			{
				List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerTaskSublist.ID,
					DBTableID.SlayerTaskSublist.COL_TASK_SUBTABLE_ID, 0, bossId);
				if (rows == null || rows.isEmpty())
				{
					return null;
				}
				row = (Integer) client.getDBTableField(rows.get(0), DBTableID.SlayerTaskSublist.COL_TASK, 0)[0];
			}
			else
			{
				List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerTask.ID,
					DBTableID.SlayerTask.COL_ID, 0, taskId);
				if (rows == null || rows.isEmpty())
				{
					return null;
				}
				row = rows.get(0);
			}
			Object[] name = client.getDBTableField(row, DBTableID.SlayerTask.COL_NAME_UPPERCASE, 0);
			return name != null && name.length > 0 && name[0] != null ? String.valueOf(name[0]) : null;
		}
		catch (RuntimeException e)
		{
			// A DB table that is not loaded yet (login screen, mid-load) throws rather than
			// answering empty. Null is the documented "could not decode" answer.
			return null;
		}
	}

	private String areaName(int areaId)
	{
		try
		{
			List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerArea.ID,
				DBTableID.SlayerArea.COL_AREA_ID, 0, areaId);
			if (rows == null || rows.isEmpty())
			{
				return null;
			}
			Object[] name = client.getDBTableField(rows.get(0), DBTableID.SlayerArea.COL_AREA_NAME_IN_HELPER, 0);
			return name != null && name.length > 0 && name[0] != null ? String.valueOf(name[0]) : null;
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}
}
