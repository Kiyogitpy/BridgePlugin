package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.util.HashMap;
import java.util.Map;

public class CombatProvider extends BridgeProvider
{
	public CombatProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/combat", () -> onClient(this::getCombat));
		server.register("/path", () -> onClient(this::getPath));
		server.registerQuery("/minimap/walk", query -> onClient(() -> getMinimapWalkPoint(query)));
	}

	private Object getCombat()
	{
		Map<String, Object> data = new HashMap<>();
		Player local = client.getLocalPlayer();

		if (local == null)
		{
			return data;
		}

		data.put("tick", client.getTickCount());
		data.put("animation", local.getAnimation());
		data.put("poseAnimation", local.getPoseAnimation());

		WorldPoint current = local.getWorldLocation();
		WorldPoint destination = null;

		net.runelite.api.coords.LocalPoint dest = client.getLocalDestinationLocation();
		if (dest != null)
		{
			destination = WorldPoint.fromLocal(client, dest);
		}

		boolean moving = destination != null && !destination.equals(current);
		data.put("isMoving", moving);

		Actor target = local.getInteracting();
		data.put("inCombat", target != null);

		if (target != null)
		{
			data.put("targetType", target instanceof NPC ? "NPC" : "PLAYER");
			data.put("targetName", target.getName());

			WorldPoint wp = target.getWorldLocation();
			data.put("targetX", wp.getX());
			data.put("targetY", wp.getY());
			data.put("targetPlane", wp.getPlane());

			if (target instanceof NPC)
			{
				NPC npc = (NPC) target;
				data.put("targetId", npc.getId());
				data.put("targetHpRatio", npc.getHealthRatio());
				data.put("targetHpScale", npc.getHealthScale());
				data.put("targetInteractingPlayer", npc.getInteracting() == local);
			}
		}

		boolean underAttack = false;
		for (NPC npc : client.getNpcs())
		{
			if (npc != null && npc.getInteracting() == local)
			{
				underAttack = true;
				break;
			}
		}
		data.put("underAttack", underAttack);

		boolean idle = !moving && local.getAnimation() == -1 && target == null;
		data.put("isIdle", idle);

		return data;
	}

	/**
	 * Projects a world tile onto the minimap so a caller can click-to-walk
	 * toward destinations that aren't rendered in the main viewport.
	 */
	private Object getMinimapWalkPoint(Map<String, String> query)
	{
		Map<String, Object> data = new HashMap<>();
		data.put("onMinimap", false);

		try
		{
			int x = Integer.parseInt(query.get("x"));
			int y = Integer.parseInt(query.get("y"));
			int plane = query.containsKey("plane") ? Integer.parseInt(query.get("plane")) : client.getPlane();

			WorldPoint wp = new WorldPoint(x, y, plane);
			LocalPoint lp = LocalPoint.fromWorld(client, wp);
			if (lp == null)
			{
				return data;
			}

			net.runelite.api.Point point = Perspective.localToMinimap(client, lp);
			if (point == null)
			{
				return data;
			}

			Point canvasLoc = RuneLiteUtil.getCanvasScreenLocation(client);
			data.put("onMinimap", true);
			data.put("screenX", point.getX() + canvasLoc.x);
			data.put("screenY", point.getY() + canvasLoc.y);
		}
		catch (Exception ignored)
		{
			// missing/invalid x, y -> onMinimap stays false
		}

		return data;
	}

	private Object getPath()
	{
		java.util.List<Map<String, Object>> path = new java.util.ArrayList<>();
		net.runelite.api.coords.LocalPoint dest = client.getLocalDestinationLocation();
		if (dest == null)
		{
			return path;
		}

		WorldPoint wp = WorldPoint.fromLocal(client, dest);
		Map<String, Object> tile = new HashMap<>();
		tile.put("x", wp.getX());
		tile.put("y", wp.getY());
		path.add(tile);
		return path;
	}
}
