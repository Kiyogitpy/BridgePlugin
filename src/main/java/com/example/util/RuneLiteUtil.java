package com.example.util;

import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.NPC;
import net.runelite.api.Perspective;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;

import java.awt.Canvas;
import java.awt.Point;import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

public final class RuneLiteUtil
{
	private RuneLiteUtil()
	{
	}

	public static Object runOnClientThread(Client client, net.runelite.client.callback.ClientThread clientThread, Supplier<Object> supplier)
	{
		AtomicReference<Object> result = new AtomicReference<>();
		CountDownLatch latch = new CountDownLatch(1);

		clientThread.invokeLater(() -> {
			try
			{
				result.set(supplier.get());
			}
			finally
			{
				latch.countDown();
			}
		});

		try
		{
			latch.await();
		}
		catch (InterruptedException e)
		{
			Thread.currentThread().interrupt();
		}

		return result.get();
	}

	public static Point getCanvasScreenLocation(Client client)
	{
		Canvas canvas = client.getCanvas();
		return canvas != null ? canvas.getLocationOnScreen() : new Point(0, 0);
	}

	public static net.runelite.api.Point localToScreen(Client client, LocalPoint local, int plane)
	{
		if (local == null)
		{
			return null;
		}
		return Perspective.localToCanvas(client, local, plane);
	}

	public static Map<String, Object> screenCoords(Client client, LocalPoint local, int plane)
	{
		Map<String, Object> coords = new HashMap<>();
		net.runelite.api.Point point = localToScreen(client, local, plane);
		if (point != null)
		{
			Point canvasLoc = getCanvasScreenLocation(client);
			coords.put("screenX", point.getX() + canvasLoc.x);
			coords.put("screenY", point.getY() + canvasLoc.y);
		}
		return coords;
	}

	public static boolean isOnScreen(Client client, net.runelite.api.Point canvasPoint)
	{
		if (canvasPoint == null)
		{
			return false;
		}

		int vw = client.getViewportWidth();
		int vh = client.getViewportHeight();
		if (vw <= 0 || vh <= 0)
		{
			return false;
		}

		int x = canvasPoint.getX();
		int y = canvasPoint.getY();
		return x >= 0 && y >= 0 && x <= vw && y <= vh;
	}

	public static Map<String, Object> visibility(Client client, LocalPoint local, int plane, boolean alive)
	{
		Map<String, Object> vis = new HashMap<>();
		net.runelite.api.Point canvasPoint = localToScreen(client, local, plane);

		boolean onScreen = isOnScreen(client, canvasPoint);
		vis.put("visible", canvasPoint != null && alive);
		vis.put("onScreen", onScreen);
		vis.put("clickable", onScreen && alive && canvasPoint != null);
		return vis;
	}

	public static Map<String, Object> itemSlot(Client client, int itemId, int quantity)
	{
		Map<String, Object> slot = new HashMap<>();
		if (itemId <= 0)
		{
			return slot;
		}

		slot.put("id", itemId);
		slot.put("qty", quantity);

		ItemComposition comp = client.getItemDefinition(itemId);
		if (comp != null)
		{
			slot.put("name", comp.getName());
			slot.put("stackable", comp.isStackable());
		}
		return slot;
	}

	public static boolean isNpcAttackable(NPC npc)
	{
		if (npc == null || npc.getName() == null)
		{
			return false;
		}

		if (npc.getHealthRatio() == 0)
		{
			return false;
		}

		return npc.getCombatLevel() > 0;
	}

	public static boolean isNpcDead(NPC npc)
	{
		return npc != null && npc.getHealthRatio() == 0;
	}

	public static int distanceToPlayer(Client client, WorldPoint wp)
	{
		return GeometryUtil.distanceToPlayer(client, wp);
	}
}
