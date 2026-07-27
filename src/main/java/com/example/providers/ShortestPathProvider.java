package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.events.PluginMessage;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginManager;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Bridges to the separately-installed "Shortest Path" hub plugin's own pathfinder instead of
 * reimplementing one -- entirely through reflection. RuneLite loads hub plugins in a
 * classloader our own plugin's classloader can't resolve shortestpath.* types from directly
 * (confirmed live: a plain typed reference to ShortestPathPlugin threw NoClassDefFoundError at
 * runtime even with a compileOnly-vendored jar), so every call into that plugin's own classes
 * happens via java.lang.reflect instead. Core net.runelite.api.* types (WorldPoint,
 * PluginMessage) ARE shared across that boundary -- only the hub plugin's own classes
 * (ShortestPathPlugin, Pathfinder, PathStep, WorldPointUtil) are isolated -- which is exactly
 * why those stay untyped Objects here while WorldPoint is used normally.
 *
 * Protocol: posts a PluginMessage ("shortestpath", "path", {"target": WorldPoint}) -- the same
 * one Quest Helper and others use -- then reads the result back off the plugin's public
 * getPathfinder() / Pathfinder.getPath()/isDone() methods.
 */
public class ShortestPathProvider extends BridgeProvider
{
	private static final String PLUGIN_CLASS_NAME = "shortestpath.ShortestPathPlugin";
	private static final String WORLD_POINT_UTIL_CLASS_NAME = "shortestpath.WorldPointUtil";

	private final PluginManager pluginManager;
	private final EventBus eventBus;

	public ShortestPathProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
		PluginManager pluginManager, EventBus eventBus)
	{
		super(client, clientThread, server);
		this.pluginManager = pluginManager;
		this.eventBus = eventBus;
	}

	@Override
	public void register()
	{
		server.registerQuery("/shortest-path/request", query -> onClient(() -> requestPath(query)));
		server.register("/shortest-path/result", () -> onClient(this::getResult));
	}

	private Object requestPath(Map<String, String> query)
	{
		Map<String, Object> ack = new HashMap<>();
		try
		{
			Object plugin = findShortestPathPlugin();
			if (plugin == null)
			{
				ack.put("requested", false);
				ack.put("reason", "Shortest Path plugin not installed/running");
				return ack;
			}

			int x = Integer.parseInt(query.get("x"));
			int y = Integer.parseInt(query.get("y"));
			int plane = query.containsKey("plane") ? Integer.parseInt(query.get("plane")) : client.getPlane();

			Map<String, Object> data = new HashMap<>();
			data.put("target", new WorldPoint(x, y, plane));
			eventBus.post(new PluginMessage("shortestpath", "path", data));

			ack.put("requested", true);
		}
		catch (Throwable t)
		{
			ack.put("requested", false);
			ack.put("reason", t.toString());
		}
		return ack;
	}

	private Object getResult()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("active", false);
		data.put("done", false);
		data.put("steps", new ArrayList<>());

		try
		{
			Object plugin = findShortestPathPlugin();
			if (plugin == null)
			{
				return data;
			}

			Object pathfinder = invoke(plugin, "getPathfinder");
			if (pathfinder == null)
			{
				return data;
			}

			data.put("active", true);
			data.put("done", invoke(pathfinder, "isDone"));

			List<?> path = (List<?>) invoke(pathfinder, "getPath");
			if (path != null && !path.isEmpty())
			{
				ClassLoader loader = plugin.getClass().getClassLoader();
				Class<?> worldPointUtil = Class.forName(WORLD_POINT_UTIL_CLASS_NAME, true, loader);
				Method unpack = worldPointUtil.getMethod("unpackWorldPoint", int.class);

				List<Map<String, Object>> steps = new ArrayList<>();
				for (Object step : path)
				{
					int packed = (int) invoke(step, "getPackedPosition");
					WorldPoint wp = (WorldPoint) unpack.invoke(null, packed);
					Map<String, Object> tile = new HashMap<>();
					tile.put("x", wp.getX());
					tile.put("y", wp.getY());
					tile.put("plane", wp.getPlane());
					steps.add(tile);
				}
				data.put("steps", steps);
			}
		}
		catch (Throwable ignored)
		{
			// Shortest Path missing, mid-update, or an incompatible version -- report
			// unavailable rather than letting this endpoint blow up.
		}

		return data;
	}

	private Object findShortestPathPlugin()
	{
		for (Plugin plugin : pluginManager.getPlugins())
		{
			if (plugin.getClass().getName().equals(PLUGIN_CLASS_NAME))
			{
				return plugin;
			}
		}
		return null;
	}

	private Object invoke(Object target, String methodName) throws Exception
	{
		Method method = target.getClass().getMethod(methodName);
		return method.invoke(target);
	}
}
