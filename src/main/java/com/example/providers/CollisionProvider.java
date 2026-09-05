package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.InstanceCoords;
import net.runelite.api.*;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * GET /collision?x=&y=&plane=&radius= -- the client's own CollisionData for a square of tiles.
 *
 * WHY A WHOLE GRID AND NOT JUST PER-OBJECT FLAGS: ObjectProvider already reports `collisionFlags`
 * for the tile an object stands on, and that turned out to answer the wrong question. MEASURED
 * live 2026-08-28: a SHUT door and an OPEN one both reported 1026 on their own tile, because a
 * door that opens SWINGS -- the doorway edge becomes passable while the leaf comes to rest
 * blocking a different edge, often on a different tile. Per-object flags therefore cannot say
 * "shut", and neither can any distance heuristic (two of those were wrong at two different houses).
 *
 * The question actually worth asking is per-EDGE and not per-object: "is movement from this tile
 * to that one blocked right now". That is exactly what a walker needs, it is what changes when a
 * door opens, and it needs collision for arbitrary tiles -- including tiles with no object on them
 * at all, which is every tile of a doorway you are trying to walk through.
 *
 * Read-only and uncached: CollisionData.getFlags() is a live int[][] the client already maintains,
 * so this is an array copy, not a scene walk.
 */
public class CollisionProvider extends BridgeProvider
{
	private static final int DEFAULT_RADIUS = 6;
	private static final int MAX_RADIUS = 25;

	public CollisionProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.registerQuery("/collision", query -> onClient(() -> getCollision(query)));
	}

	// Same shape as every other provider's own copy (TileProvider, ObjectProvider, ...).
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

	private Object getCollision(Map<String, String> query)
	{
		Map<String, Object> data = new LinkedHashMap<>();

		WorldView view = client.getTopLevelWorldView();
		Player local = client.getLocalPlayer();
		if (view == null || local == null)
		{
			data.put("tiles", new ArrayList<>());
			return data;
		}

		Integer radius = parseIntParam(query.get("radius"));
		int r = radius != null ? Math.max(0, Math.min(MAX_RADIUS, radius)) : DEFAULT_RADIUS;

		// Centre defaults to the player. An explicit x/y is resolved by SCANNING the scene for the
		// tile whose real world position matches, never by converting world -> local: that forward
		// conversion is broken inside instances (see CLAUDE.md's instancing gotcha) and this needs
		// to keep working in one.
		Integer wantX = parseIntParam(query.get("x"));
		Integer wantY = parseIntParam(query.get("y"));
		Integer wantPlane = parseIntParam(query.get("plane"));
		int plane = wantPlane != null ? wantPlane : local.getWorldLocation().getPlane();

		CollisionData[] maps = view.getCollisionMaps();
		if (maps == null || plane < 0 || plane >= maps.length || maps[plane] == null)
		{
			data.put("tiles", new ArrayList<>());
			return data;
		}
		int[][] flags = maps[plane].getFlags();
		Scene scene = view.getScene();
		Tile[][][] tiles = scene != null ? scene.getTiles() : null;
		if (flags == null || tiles == null || plane >= tiles.length || tiles[plane] == null)
		{
			data.put("tiles", new ArrayList<>());
			return data;
		}

		int centreSceneX = -1, centreSceneY = -1;
		if (wantX != null && wantY != null && InstanceCoords.wantsInstance(query))
		{
			centreSceneX = InstanceCoords.toScene(view.getBaseX(), wantX);
			centreSceneY = InstanceCoords.toScene(view.getBaseY(), wantY);
		}
		else if (wantX != null && wantY != null)
		{
			for (int sx = 0; sx < tiles[plane].length && centreSceneX < 0; sx++)
			{
				for (int sy = 0; sy < tiles[plane][sx].length; sy++)
				{
					Tile t = tiles[plane][sx][sy];
					if (t == null)
					{
						continue;
					}
					WorldPoint wp = WorldPoint.fromLocalInstance(client, t.getLocalLocation(), plane);
					if (wp != null && wp.getX() == wantX && wp.getY() == wantY)
					{
						centreSceneX = sx;
						centreSceneY = sy;
						break;
					}
				}
			}
		}
		else
		{
			LocalPoint lp = local.getLocalLocation();
			if (lp != null)
			{
				centreSceneX = lp.getSceneX();
				centreSceneY = lp.getSceneY();
			}
		}
		if (centreSceneX < 0)
		{
			data.put("tiles", new ArrayList<>());
			data.put("error", "centre tile not in the loaded scene");
			return data;
		}

		List<Map<String, Object>> out = new ArrayList<>();
		for (int sx = centreSceneX - r; sx <= centreSceneX + r; sx++)
		{
			if (sx < 0 || sx >= flags.length || flags[sx] == null || sx >= tiles[plane].length)
			{
				continue;
			}
			for (int sy = centreSceneY - r; sy <= centreSceneY + r; sy++)
			{
				if (sy < 0 || sy >= flags[sx].length || sy >= tiles[plane][sx].length)
				{
					continue;
				}
				Tile t = tiles[plane][sx][sy];
				if (t == null)
				{
					continue;
				}
				WorldPoint wp = WorldPoint.fromLocalInstance(client, t.getLocalLocation(), plane);
				if (wp == null)
				{
					continue;
				}
				Map<String, Object> tile = new LinkedHashMap<>();
				tile.put("x", wp.getX());
				tile.put("y", wp.getY());
				tile.put("plane", plane);
				tile.put("flags", flags[sx][sy]);
				WorldPoint inst = InstanceCoords.instancePoint(client, t.getLocalLocation(), plane);
				tile.put("instX", inst.getX());
				tile.put("instY", inst.getY());
				out.add(tile);
			}
		}
		data.put("plane", plane);
		data.put("tiles", out);
		return data;
	}
}
