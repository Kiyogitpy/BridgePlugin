package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class ObjectProvider extends BridgeProvider
{
	public ObjectProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/objects", () -> onClient(this::getObjects));
	}

	private Object getObjects()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		Scene scene = client.getScene();
		if (scene == null)
		{
			return list;
		}

		Tile[][][] tiles = scene.getTiles();
		int plane = client.getPlane();

		for (int x = 0; x < tiles[plane].length; x++)
		{
			for (int y = 0; y < tiles[plane][x].length; y++)
			{
				Tile tile = tiles[plane][x][y];
				if (tile == null)
				{
					continue;
				}

				for (GameObject obj : tile.getGameObjects())
				{
					if (obj != null)
					{
						list.add(buildObjectData(obj, "GameObject", plane));
					}
				}

				// Doors, gates, and fences are WallObjects, not GameObjects -- transports.tsv's
				// "Open Door"/"Open Gate" entries need these to be findable too, not just the
				// staircases/ladders that do tend to be GameObjects.
				WallObject wallObj = tile.getWallObject();
				if (wallObj != null)
				{
					list.add(buildObjectData(wallObj, "WallObject", plane));
				}

				GroundObject groundObj = tile.getGroundObject();
				if (groundObj != null)
				{
					list.add(buildObjectData(groundObj, "GroundObject", plane));
				}

				DecorativeObject decorativeObj = tile.getDecorativeObject();
				if (decorativeObj != null)
				{
					list.add(buildObjectData(decorativeObj, "DecorativeObject", plane));
				}
			}
		}
		return list;
	}

	private Map<String, Object> buildObjectData(TileObject obj, String objectType, int plane)
	{
		Map<String, Object> data = new HashMap<>();
		WorldPoint wp = WorldPoint.fromLocal(client, obj.getLocalLocation());

		data.put("id", obj.getId());
		data.put("objectType", objectType);
		ObjectComposition comp = client.getObjectDefinition(obj.getId());
		data.put("name", comp != null ? comp.getName() : "Unknown");
		data.put("x", wp.getX());
		data.put("y", wp.getY());
		data.put("plane", wp.getPlane());

		net.runelite.api.Point point = RuneLiteUtil.localToScreen(client, obj.getLocalLocation(), plane);
		if (point != null)
		{
			Point screen = RuneLiteUtil.canvasToScreen(client, point.getX(), point.getY());
			data.put("screenX", screen.x);
			data.put("screenY", screen.y);
		}

		data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
		data.putAll(RuneLiteUtil.visibility(client, obj.getLocalLocation(), plane, true));
		return data;
	}
}
