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
		server.registerQuery("/graphics-object/at", query -> onClient(() -> getGraphicsObjectAt(query)));
	}

	private Object getGraphicsObjects(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> (List<Map<String, Object>>) onClient(this::computeGraphicsObjects));
	}

	/**
	 * Exact lookup by tile position + graphics-object id (uncached, unlike the bulk
	 * /graphics-objects) -- the graphics-object equivalent of ObjectProvider's getObjectAt, for a
	 * routine re-checking a specific timing cue (e.g. Mastering Mixology's Agitator/Alembic flash
	 * -- routines/mastering_mixology.py's AGITATOR_CUE_GRAPHICS_ID/ALEMBIC_CUE_GRAPHICS_ID)
	 * without scanning the whole bulk list every poll.
	 *
	 * NOTE: unlike ObjectProvider/GroundItemProvider/NPCProvider/PlayerProvider, this provider
	 * does NOT apply WorldPoint.fromLocalInstance() (see computeGraphicsObjects' plain
	 * WorldPoint.fromLocal call) -- it inherits whatever coordinate correctness the bulk
	 * /graphics-objects endpoint already has, which is UNVERIFIED inside an instance (see
	 * CLAUDE.md's "Instanced areas break forward world-coordinate conversion": assume the same
	 * gap until checked, same as every other provider not in the four already fixed). Filters
	 * computeGraphicsObjects()' own list rather than a fresh reverse lookup, same reasoning as
	 * ObjectProvider's getObjectAt.
	 */
	private Object getGraphicsObjectAt(Map<String, String> query)
	{
		Map<String, Object> notFound = new HashMap<>();
		notFound.put("found", false);

		Integer x = parseIntParam(query.get("x"));
		Integer y = parseIntParam(query.get("y"));
		Integer plane = parseIntParam(query.get("plane"));
		Integer id = parseIntParam(query.get("id"));
		if (x == null || y == null || plane == null || id == null)
		{
			return notFound;
		}

		for (Map<String, Object> obj : computeGraphicsObjects())
		{
			if (id.equals(obj.get("id")) && x.equals(obj.get("x")) && y.equals(obj.get("y"))
				&& plane.equals(obj.get("plane")))
			{
				Map<String, Object> found = new HashMap<>(obj);
				found.put("found", true);
				return found;
			}
		}
		return notFound;
	}

	private Integer parseIntParam(String s)
	{
		if (s == null || s.isEmpty())
		{
			return null;
		}
		try
		{
			return Integer.parseInt(s);
		}
		catch (NumberFormatException e)
		{
			return null;
		}
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
