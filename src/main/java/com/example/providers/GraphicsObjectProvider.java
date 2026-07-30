package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.GraphicsObject;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Active ground-anchored spotanims (GraphicsObjects) -- e.g. a boss's telegraphed AoE attack
 * landing on a tile before it detonates. Generic/id-agnostic on purpose: a routine that cares
 * about one specific effect (see routines/tormented_demon.py's fireball dodge) filters this
 * list by id itself rather than this provider knowing about any particular boss.
 */
public class GraphicsObjectProvider extends BridgeProvider
{
	// Same reasoning as ObjectProvider/NPCProvider/GroundItemProvider/TileProvider: a caller
	// polling faster than the game state actually changes turns repeated per-object WorldPoint/
	// distance work into a real source of client stutter, worst exactly when it matters most --
	// a busy fight with many active effects onscreen. Caching by tick count adds no staleness
	// since these only move/spawn/finish on game tick boundaries anyway.
	private int cachedTick = -1;
	private List<Map<String, Object>> cachedGraphicsObjects;

	public GraphicsObjectProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/graphics-objects", () -> onClient(this::getGraphicsObjects));
	}

	private Object getGraphicsObjects()
	{
		int tick = client.getTickCount();
		if (cachedGraphicsObjects == null || tick != cachedTick)
		{
			cachedGraphicsObjects = computeGraphicsObjects();
			cachedTick = tick;
		}
		return cachedGraphicsObjects;
	}

	private List<Map<String, Object>> computeGraphicsObjects()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		for (GraphicsObject obj : client.getGraphicsObjects())
		{
			if (obj == null)
			{
				continue;
			}

			WorldPoint wp = WorldPoint.fromLocal(client, obj.getLocation());
			if (wp == null)
			{
				continue;
			}

			Map<String, Object> data = new HashMap<>();
			data.put("id", obj.getId());
			data.put("x", wp.getX());
			data.put("y", wp.getY());
			data.put("plane", obj.getLevel());
			data.put("finished", obj.finished());
			data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
			list.add(data);
		}
		return list;
	}
}
