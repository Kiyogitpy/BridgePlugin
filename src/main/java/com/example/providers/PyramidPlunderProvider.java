package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.HashMap;
import java.util.Map;

/**
 * Reads Pyramid Plunder's live state (room number, run timer, spear-trap-armed flag) directly
 * from the same varbits the official RuneLite Pyramid Plunder plugin uses --
 * data/decompiled/client-1.12.33/net/runelite/client/plugins/pyramidplunder/PyramidPlunderPlugin.java
 * (isInPyramidPlunder(), onGameTick()) -- cross-referenced against named constants in
 * IdeaProjects/runelite's runelite-api VarbitID.java (NTK_TRAP_ACTIVE=2365,
 * NTK_PLAYER_TIMER_COUNT=2375, NTK_ROOM_NUMBER=2377). See
 * docs/superpowers/specs/2026-08-16-pyramid-plunder-design.md's Research method section.
 */
public class PyramidPlunderProvider extends BridgeProvider
{
	private static final int PYRAMID_PLUNDER_REGION = 7749;

	public PyramidPlunderProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/pyramid-plunder", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();

		Player local = client.getLocalPlayer();
		// Deliberately plain getWorldLocation(), NOT the instance-safe
		// WorldPoint.fromLocalInstance()/getTopLevelWorldLocation() this codebase standardizes on
		// elsewhere (see CLAUDE.md's "Instanced areas break forward world-coordinate conversion"):
		// this reproduces the official RuneLite PyramidPlunderPlugin.isInPyramidPlunder() exactly,
		// and the Jalsavrah Pyramid's rooms are a real, shared, NON-instanced region (7749) -- the
		// instance-template chunk remapping that gotcha is about simply doesn't happen here, so
		// there is no wrong region id to correct for.
		boolean inRegion = local != null && local.getWorldLocation().getRegionID() == PYRAMID_PLUNDER_REGION;
		int timerTicks = client.getVarbitValue(VarbitID.NTK_PLAYER_TIMER_COUNT);

		// Matches PyramidPlunderPlugin.isInPyramidPlunder() exactly: region + timer running.
		data.put("inRun", inRegion && timerTicks > 0);
		data.put("roomNumber", client.getVarbitValue(VarbitID.NTK_ROOM_NUMBER));
		data.put("timerTicks", timerTicks);
		data.put("trapActive", client.getVarbitValue(VarbitID.NTK_TRAP_ACTIVE) == 1);

		return data;
	}
}
