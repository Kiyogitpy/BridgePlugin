package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.GeometryUtil;
import net.runelite.api.*;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class TileProvider extends BridgeProvider
{
	public TileProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/tiles", () -> onClient(this::getTiles));
	}

	private Object getTiles()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		Scene scene = client.getScene();
		if (scene == null)
		{
			return list;
		}

		CollisionData[] collisionMaps = client.getCollisionMaps();
		int plane = client.getPlane();
		Tile[][][] tiles = scene.getTiles();
		Player local = client.getLocalPlayer();
		if (tiles == null || plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			// The scene object exists but this plane's tile grid isn't populated yet -- e.g. not
			// logged in, or mid floor-transition/teleport. Bailing out here (instead of throwing)
			// matters beyond correctness: an uncaught NPE here runs on the client thread and gets
			// logged with a full stack trace on every single poll, which for a caller hitting this
			// endpoint every tick is a real, continuous source of stutter, not just a cosmetic error.
			return list;
		}

		for (int sx = 0; sx < tiles[plane].length; sx++)
		{
			for (int sy = 0; sy < tiles[plane][sx].length; sy++)
			{
				Tile tile = tiles[plane][sx][sy];
				if (tile == null)
				{
					continue;
				}

				WorldPoint wp = tile.getWorldLocation();
				Map<String, Object> data = new HashMap<>();
				data.put("x", wp.getX());
				data.put("y", wp.getY());
				data.put("plane", wp.getPlane());

				int flags = 0;
				if (collisionMaps != null && plane < collisionMaps.length && collisionMaps[plane] != null)
				{
					flags = collisionMaps[plane].getFlags()[sx][sy];
				}

				boolean blocked = (flags & CollisionDataFlag.BLOCK_MOVEMENT_FULL) != 0;
				boolean walkable = !blocked;
				data.put("walkable", walkable);
				data.put("blocked", blocked);
				data.put("reachable", walkable && local != null
					&& GeometryUtil.distanceToPlayer(client, wp) <= 20);

				list.add(data);
			}
		}
		return list;
	}
}
