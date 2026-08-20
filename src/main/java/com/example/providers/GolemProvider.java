package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Mirrors Wyrmscraig's golem-crafting state onto GET /golem, read straight from RuneLite's own
 * generated VarbitID constants -- confirmed present in this project's runelite-api jar via direct
 * javap inspection, the same method MixologyProvider documents. Semantics were cross-referenced
 * against the decompiled "Golem Crafting" plugin-hub plugin (org.zhbot.golem_crafting); that
 * plugin is NOT a runtime dependency, it was documentation only.
 *
 * Deliberately NOT TickCache-gated (unlike /objects, /npcs and the other bulk list endpoints):
 * routines/golem_crafting.py polls this as its click-confirmation signal, so a stale read would
 * reintroduce exactly the lag the varbits are here to remove.
 *
 * Deliberately exposes no policy -- no "which side next", no material verdict. Which side to
 * carve depends on where the player is standing, which is the Python side's business. This is a
 * state mirror, same as every other provider here.
 */
public class GolemProvider extends BridgeProvider
{
	// Matches the reference plugin's own area test.
	private static final WorldPoint AREA_CENTRE = new WorldPoint(2590, 2250, 0);
	private static final int AREA_RADIUS = 15;

	// No named ObjectID constants exist for these in runelite-api 1.12.36 (checked by javap --
	// the GOLEM_* ObjectID entries are all Desert Treasure's Golem quest). Raw ids, from the
	// reference plugin's NorthGolem/SouthGolem.
	private static final int NORTH_STATION_OBJECT_ID = 62351;
	private static final int SOUTH_STATION_OBJECT_ID = 62352;
	private static final WorldPoint NORTH_GOLEM_TILE = new WorldPoint(2596, 2257, 0);
	private static final WorldPoint SOUTH_GOLEM_TILE = new WorldPoint(2595, 2253, 0);

	// Insert-core is accepted from exactly one side per station, fixed per plinth.
	private static final String NORTH_STATION_INSERT_SIDE = "south";
	private static final String SOUTH_STATION_INSERT_SIDE = "north";

	public GolemProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/golem", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("inArea", inArea());
		data.put("totalCrafted", client.getVarbitValue(VarbitID.GOLEM_CRAFTING_COUNT));
		// Raw generic "player is busy" varbit. Its exact meaning for this activity is NOT
		// verified -- exposed for diagnosis only; nothing on the Python side depends on it.
		data.put("busy", client.getVarbitValue(VarbitID.BUSY));

		List<Map<String, Object>> stations = new ArrayList<>();
		stations.add(buildStation(
			"north", NORTH_STATION_OBJECT_ID, NORTH_GOLEM_TILE, NORTH_STATION_INSERT_SIDE,
			VarbitID.GOLEM_CRAFTING_STATION_1,
			VarbitID.GOLEM_CRAFTING_STATION_1_NORTH, VarbitID.GOLEM_CRAFTING_STATION_1_EAST,
			VarbitID.GOLEM_CRAFTING_STATION_1_SOUTH, VarbitID.GOLEM_CRAFTING_STATION_1_WEST,
			VarbitID.GOLEM_1_NORTH_PROGRESS, VarbitID.GOLEM_1_EAST_PROGRESS,
			VarbitID.GOLEM_1_SOUTH_PROGRESS, VarbitID.GOLEM_1_WEST_PROGRESS));
		stations.add(buildStation(
			"south", SOUTH_STATION_OBJECT_ID, SOUTH_GOLEM_TILE, SOUTH_STATION_INSERT_SIDE,
			VarbitID.GOLEM_CRAFTING_STATION_2,
			VarbitID.GOLEM_CRAFTING_STATION_2_NORTH, VarbitID.GOLEM_CRAFTING_STATION_2_EAST,
			VarbitID.GOLEM_CRAFTING_STATION_2_SOUTH, VarbitID.GOLEM_CRAFTING_STATION_2_WEST,
			VarbitID.GOLEM_2_NORTH_PROGRESS, VarbitID.GOLEM_2_EAST_PROGRESS,
			VarbitID.GOLEM_2_SOUTH_PROGRESS, VarbitID.GOLEM_2_WEST_PROGRESS));
		data.put("stations", stations);
		return data;
	}

	private boolean inArea()
	{
		Player local = client.getLocalPlayer();
		if (local == null)
		{
			return false;
		}
		// Wyrmscraig is not instanced, so the instance-template remapping trap documented in
		// CLAUDE.md does not apply here -- this is a plain proximity check.
		return RuneLiteUtil.getTopLevelWorldLocation(client, local).distanceTo(AREA_CENTRE) <= AREA_RADIUS;
	}

	private Map<String, Object> buildStation(
		String name, int objectId, WorldPoint tile, String insertSide, int progressVarbit,
		int northState, int eastState, int southState, int westState,
		int northProgress, int eastProgress, int southProgress, int westProgress)
	{
		Map<String, Object> station = new LinkedHashMap<>();
		station.put("name", name);
		station.put("objectId", objectId);
		station.put("x", tile.getX());
		station.put("y", tile.getY());
		station.put("plane", tile.getPlane());
		station.put("progress", client.getVarbitValue(progressVarbit));
		station.put("insertSide", insertSide);

		// Plain arithmetic on a constant tile -- NOT a LocalPoint.fromWorld() forward lookup, so
		// none of CLAUDE.md's forward-conversion caveats apply. OSRS world y increases northward.
		Map<String, Object> sides = new LinkedHashMap<>();
		sides.put("north", buildSide(northState, northProgress, tile.getX(), tile.getY() + 1, tile.getPlane()));
		sides.put("east", buildSide(eastState, eastProgress, tile.getX() + 1, tile.getY(), tile.getPlane()));
		sides.put("south", buildSide(southState, southProgress, tile.getX(), tile.getY() - 1, tile.getPlane()));
		sides.put("west", buildSide(westState, westProgress, tile.getX() - 1, tile.getY(), tile.getPlane()));
		station.put("sides", sides);
		return station;
	}

	private Map<String, Object> buildSide(int stateVarbit, int progressVarbit, int x, int y, int plane)
	{
		Map<String, Object> side = new LinkedHashMap<>();
		side.put("done", client.getVarbitValue(stateVarbit) != 0);
		// Raw 0-10 varbit value, not the reference plugin's /10.0 fraction -- this endpoint is a
		// state mirror, not a presentation layer.
		side.put("progress", client.getVarbitValue(progressVarbit));
		side.put("x", x);
		side.put("y", y);
		side.put("plane", plane);
		return side;
	}
}
