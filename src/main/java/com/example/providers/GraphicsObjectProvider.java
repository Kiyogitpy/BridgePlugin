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
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public GraphicsObjectProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/graphics-objects", query -> getGraphicsObjects(isFresh(query)));
	}

	private Object getGraphicsObjects(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> (List<Map<String, Object>>) onClient(this::computeGraphicsObjects));
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
