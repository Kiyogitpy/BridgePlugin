package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.InstanceCoords;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginManager;

import java.awt.Point;
import java.util.HashMap;
import java.util.Map;

public class CombatProvider extends BridgeProvider
{
	private static final long METRONOME_HOOK_RETRY_INTERVAL_MS = 10_000;

	private final PluginManager pluginManager;
	private Plugin metronomePlugin;
	private java.lang.reflect.Method ticksUntilNextAttackMethod;
	private long lastMetronomeHookAttemptMs = -METRONOME_HOOK_RETRY_INTERVAL_MS;

	public CombatProvider(Client client, ClientThread clientThread, BridgeHttpServer server, PluginManager pluginManager)
	{
		super(client, clientThread, server);
		this.pluginManager = pluginManager;
	}

	@Override
	public void register()
	{
		server.register("/combat", () -> onClient(this::getCombat));
		server.register("/path", () -> onClient(this::getPath));
		server.registerQuery("/minimap/walk", query -> onClient(() -> getMinimapWalkPoint(query)));
		server.registerQuery("/ground/walk", query -> onClient(() -> getGroundWalkPoint(query)));
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

		Integer ticksUntilNextAttack = getTicksUntilNextAttack();
		if (ticksUntilNextAttack != null)
		{
			data.put("ticksUntilNextAttack", ticksUntilNextAttack);
		}

		data.put("animation", local.getAnimation());
		data.put("poseAnimation", local.getPoseAnimation());

		// CONFIRMED live 2026-08-05 (user report -- shipwreck_salvaging.py's hook-management logic
		// silently stalled indefinitely): while aboard a Sailing boat, local.getWorldLocation() and
		// WorldPoint.fromLocal(client, dest) each resolve against whichever WorldView their own
		// LocalPoint names (see RuneLiteUtil.toTopLevelWorldPoint's own doc -- the exact same class
		// of bug already fixed once for ObjectProvider's distance field reading Integer.MAX_VALUE).
		// A minimap steering click sets a real getLocalDestinationLocation(), but it and the
		// player's own boat-relative getWorldLocation() aren't necessarily in the same coordinate
		// space -- comparing them directly could report "still moving" forever after boat steering,
		// even with the boat fully stopped (confirmed live: boatSpeed 0.0, navigating false, yet
		// /combat kept reporting isMoving=true, isIdle=false indefinitely). Resolving both through
		// the same top-level-world helper used everywhere else this exact mismatch matters fixes it
		// for boats without changing behavior on foot (RuneLiteUtil's WorldView.TOPLEVEL fast path).
		WorldPoint current = RuneLiteUtil.getTopLevelWorldLocation(client, local);
		WorldPoint destination = null;

		net.runelite.api.coords.LocalPoint dest = client.getLocalDestinationLocation();
		if (dest != null)
		{
			destination = RuneLiteUtil.toTopLevelWorldPoint(client, dest);
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

	// Attack Timer Metronome has no official RuneLite API surface -- this reflects into it by
	// name, same technique as UnsafeNpcOverheadIcon and the same technique the community
	// ZombieLite fork uses for the same plugin (see its ZombiePrayerPlugin
	// .hookAttackTimerMetronome()). Retried on a cooldown rather than a one-shot latch --
	// the user may enable the plugin (or RuneLite may start it) after this provider's first
	// /combat poll, and a permanent latch would otherwise mean restarting RuneLite to pick it
	// up. If the plugin genuinely isn't installed, or this method no longer exists on it,
	// ticksUntilNextAttack is simply absent from every /combat response -- no error, no
	// fallback guess.
	private void hookMetronomeIfNeeded()
	{
		if (metronomePlugin != null)
		{
			return;
		}
		long now = System.currentTimeMillis();
		if (now - lastMetronomeHookAttemptMs < METRONOME_HOOK_RETRY_INTERVAL_MS)
		{
			return;
		}
		lastMetronomeHookAttemptMs = now;

		Plugin plugin = pluginManager.getPlugins().stream()
			.filter(p -> p.getClass().getName().equals("com.attacktimer.AttackTimerMetronomePlugin"))
			.findFirst().orElse(null);
		if (plugin == null)
		{
			return;
		}

		try
		{
			java.lang.reflect.Method method = plugin.getClass().getDeclaredMethod("getTicksUntilNextAttack");
			if (method.getReturnType() != int.class)
			{
				return;
			}
			method.setAccessible(true);
			metronomePlugin = plugin;
			ticksUntilNextAttackMethod = method;
		}
		catch (NoSuchMethodException | SecurityException ignored)
		{
		}
	}

	private Integer getTicksUntilNextAttack()
	{
		hookMetronomeIfNeeded();
		if (metronomePlugin == null || ticksUntilNextAttackMethod == null)
		{
			return null;
		}
		try
		{
			return (int) ticksUntilNextAttackMethod.invoke(metronomePlugin);
		}
		catch (Exception ignored)
		{
			return null;
		}
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

			// inst=true (2026-09-05): x/y are instance-space, resolved by arithmetic against the
			// scene base -- the one direction that works inside an instance (see InstanceCoords).
			LocalPoint lp = InstanceCoords.wantsInstance(query)
				? InstanceCoords.fromInstance(client.getTopLevelWorldView(), x, y)
				: LocalPoint.fromWorld(client, new WorldPoint(x, y, plane));
			if (lp == null)
			{
				return data;
			}

			net.runelite.api.Point point = Perspective.localToMinimap(client, lp);
			if (point == null)
			{
				return data;
			}

			Point screen = RuneLiteUtil.canvasToScreen(client, point.getX(), point.getY());
			data.put("onMinimap", true);
			data.put("screenX", screen.x);
			data.put("screenY", screen.y);
		}
		catch (Exception ignored)
		{
			// missing/invalid x, y -> onMinimap stays false
		}

		return data;
	}

	/**
	 * Projects a world tile onto the 3D viewport (not the minimap) so a caller can click a
	 * nearby ground tile the same way a player closing a short distance to something on-screen
	 * would, instead of routing every approach through a minimap click.
	 */
	private Object getGroundWalkPoint(Map<String, String> query)
	{
		Map<String, Object> data = new HashMap<>();
		data.put("onScreen", false);

		try
		{
			int x = Integer.parseInt(query.get("x"));
			int y = Integer.parseInt(query.get("y"));
			int plane = query.containsKey("plane") ? Integer.parseInt(query.get("plane")) : client.getPlane();

			// inst=true (2026-09-05): x/y are instance-space, resolved by arithmetic against the
			// scene base -- the one direction that works inside an instance (see InstanceCoords).
			LocalPoint lp = InstanceCoords.wantsInstance(query)
				? InstanceCoords.fromInstance(client.getTopLevelWorldView(), x, y)
				: LocalPoint.fromWorld(client, new WorldPoint(x, y, plane));
			if (lp == null)
			{
				return data;
			}

			net.runelite.api.Point point = RuneLiteUtil.localToScreen(client, lp, plane);
			if (!RuneLiteUtil.isOnScreen(client, point))
			{
				return data;
			}

			Point screen = RuneLiteUtil.canvasToScreen(client, point.getX(), point.getY());
			data.put("onScreen", true);
			data.put("screenX", screen.x);
			data.put("screenY", screen.y);
		}
		catch (Exception ignored)
		{
			// missing/invalid x, y -> onScreen stays false
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
