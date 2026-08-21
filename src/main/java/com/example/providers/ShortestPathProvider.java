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

			Map<String, Object> config = parseConfigOverrides(query);

			Map<String, Object> data = new HashMap<>();
			data.put("target", new WorldPoint(x, y, plane));
			if (!config.isEmpty())
			{
				data.put("config", config);
			}
			eventBus.post(new PluginMessage("shortestpath", "path", data));

			ack.put("requested", true);
			ack.put("config", config);
		}
		catch (Throwable t)
		{
			ack.put("requested", false);
			ack.put("reason", t.toString());
		}
		return ack;
	}

	/**
	 * Pulls the "cfg.<option>" query params into the per-request config override map the Shortest
	 * Path plugin's own "path" PluginMessage accepts alongside "target" (it copies the map into a
	 * static override consulted by every one of its config getters). Keys are that plugin's
	 * ShortestPathConfig option names -- useFairyRings, useTeleportationItems, avoidWilderness,
	 * calculationCutoff, ... -- and are passed through unvalidated: the plugin silently ignores
	 * ones it doesn't know, and the Python side (travel.SHORTEST_PATH_CONFIG_KEYS) is where a
	 * typo actually gets caught.
	 *
	 * Its override() readers only accept Boolean, Integer and String values, so the text of each
	 * param is coerced to whichever of those it looks like. That coercion is the whole reason the
	 * values can cross the classloader boundary at all: unlike shortestpath's own types, those
	 * three are shared, so nothing here needs reflection.
	 *
	 * NOTE the override map is sticky on the plugin's side -- it's only replaced when a non-empty
	 * map arrives and is never cleared -- so a caller that wants a request independent of the last
	 * one has to send its whole policy every time, not just the keys it wants changed. That's why
	 * travel.py sends all of TRANSPORT_POLICY on every request.
	 */
	private Map<String, Object> parseConfigOverrides(Map<String, String> query)
	{
		Map<String, Object> config = new HashMap<>();
		for (Map.Entry<String, String> entry : query.entrySet())
		{
			if (!entry.getKey().startsWith("cfg.") || entry.getKey().length() <= 4)
			{
				continue;
			}
			config.put(entry.getKey().substring(4), coerceConfigValue(entry.getValue()));
		}
		return config;
	}

	private Object coerceConfigValue(String value)
	{
		if ("true".equalsIgnoreCase(value) || "false".equalsIgnoreCase(value))
		{
			return Boolean.valueOf(value);
		}
		try
		{
			return Integer.valueOf(value);
		}
		catch (NumberFormatException e)
		{
			// Left as a String -- which is also what the plugin's TeleportationItem and
			// JewelleryBoxTier overrides expect, both read via their own fromType(String).
			return value;
		}
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
			boolean done = (boolean) invoke(pathfinder, "isDone");
			data.put("done", done);

			ClassLoader loader = plugin.getClass().getClassLoader();
			Class<?> worldPointUtil = Class.forName(WORLD_POINT_UTIL_CLASS_NAME, true, loader);
			Method unpack = worldPointUtil.getMethod("unpackWorldPoint", int.class);

			addPathfinderIdentity(data, pathfinder, unpack);
			if (done)
			{
				addPathfinderOutcome(data, pathfinder);
			}

			List<?> path = (List<?>) invoke(pathfinder, "getPath");
			if (path != null && !path.isEmpty())
			{
				Class<?> pathStepClass = path.get(0).getClass();
				Method transportsForEdge = plugin.getClass().getMethod("transportsForEdge", pathStepClass, pathStepClass);

				List<Map<String, Object>> steps = new ArrayList<>();
				Object previousStep = null;
				for (Object step : path)
				{
					int packed = (int) invoke(step, "getPackedPosition");
					WorldPoint wp = (WorldPoint) unpack.invoke(null, packed);
					Map<String, Object> tile = new HashMap<>();
					tile.put("x", wp.getX());
					tile.put("y", wp.getY());
					tile.put("plane", wp.getPlane());

					// A transport (door, staircase, ladder, boat, fairy ring, teleport item, etc.)
					// isn't recorded on the step itself -- it's an edge between two steps, so it
					// has to be looked up pairwise via the plugin's own transportsForEdge, which
					// mirrors exactly what its debug overlay uses to draw transport icons on the map.
					if (previousStep != null)
					{
						Map<String, Object> transport = describeTransport(plugin, transportsForEdge, previousStep, step);
						if (transport != null)
						{
							tile.put("transport", transport);
						}
					}

					steps.add(tile);
					previousStep = step;
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

	/**
	 * Adds "start" and "targets" -- the tiles the CURRENTLY RUNNING pathfinder was created for,
	 * which is the only way a caller can tell whether a finished path is the one it asked for.
	 * The plugin swaps its Pathfinder in asynchronously after the "path" PluginMessage, so a poll
	 * arriving in between reads the previous request's pathfinder, already done, with a complete
	 * path to somewhere else entirely. Without this, that path was indistinguishable from ours.
	 *
	 * Best-effort and separately caught: an older Shortest Path without getStart()/getTargets()
	 * should cost the caller the target check, not the path itself.
	 */
	private void addPathfinderIdentity(Map<String, Object> data, Object pathfinder, Method unpack)
	{
		try
		{
			int start = (int) invoke(pathfinder, "getStart");
			Map<String, Object> startTile = unpackTile(unpack, start);
			if (startTile != null)
			{
				data.put("start", startTile);
			}

			java.util.Set<?> targets = (java.util.Set<?>) invoke(pathfinder, "getTargets");
			if (targets != null)
			{
				List<Map<String, Object>> tiles = new ArrayList<>();
				for (Object packed : targets)
				{
					Map<String, Object> tile = unpackTile(unpack, (Integer) packed);
					if (tile != null)
					{
						tiles.add(tile);
					}
				}
				data.put("targets", tiles);
			}
		}
		catch (Throwable ignored)
		{
			// Leaving "targets" absent is the documented signal for "this build can't tell you";
			// travel.py falls back to treating any finished path as the answer, as it used to.
		}
	}

	/**
	 * Adds "reached" and "terminationReason" (TARGET_REACHED / SEARCH_EXHAUSTED / CUTOFF_REACHED /
	 * CANCELLED) from the plugin's own PathfinderResult. Shortest Path returns its best partial
	 * route when it can't get all the way there, which on the wire is indistinguishable from a
	 * complete one -- so without these a walk that stops 40 tiles short looks exactly like a walk
	 * that arrived. Only called once the pathfinder is done, since that's the only point the
	 * outcome means anything.
	 */
	private void addPathfinderOutcome(Map<String, Object> data, Object pathfinder)
	{
		try
		{
			Object result = invoke(pathfinder, "getResult");
			if (result == null)
			{
				return;
			}
			data.put("reached", invoke(result, "isReached"));
			Object reason = invoke(result, "getTerminationReason");
			data.put("terminationReason", reason == null ? null : ((Enum<?>) reason).name());
		}
		catch (Throwable ignored)
		{
			// Same reasoning as addPathfinderIdentity: absent means "unknown", and travel.py
			// treats an absent "reached" as true rather than refusing to walk.
		}
	}

	private Map<String, Object> unpackTile(Method unpack, int packed) throws Exception
	{
		if (packed == -1)
		{
			return null;
		}
		WorldPoint wp = (WorldPoint) unpack.invoke(null, packed);
		if (wp == null)
		{
			return null;
		}
		Map<String, Object> tile = new HashMap<>();
		tile.put("x", wp.getX());
		tile.put("y", wp.getY());
		tile.put("plane", wp.getPlane());
		return tile;
	}

	/**
	 * Looks up the Transport (if any) used to get from `previousStep` to `step`, and returns a
	 * plain description of it -- or null for an ordinary walked tile. When more than one
	 * Transport connects the same two tiles (e.g. alternate unlock conditions), the first one is
	 * used; distinguishing which one the pathfinder actually picked isn't exposed by this API.
	 */
	private Map<String, Object> describeTransport(Object plugin, Method transportsForEdge, Object previousStep, Object step) throws Exception
	{
		java.util.Set<?> transports = (java.util.Set<?>) transportsForEdge.invoke(plugin, previousStep, step);
		if (transports == null || transports.isEmpty())
		{
			return null;
		}

		Object transport = transports.iterator().next();
		Map<String, Object> data = new HashMap<>();

		Object type = invoke(transport, "getType");
		data.put("type", type == null ? null : ((Enum<?>) type).name());
		if (type != null)
		{
			try
			{
				// Resolved on the enum's own declaring class, not type.getClass(): several
				// TransportType constants have class bodies, so getClass() is a non-public
				// anonymous subclass whose methods reflection won't let us invoke.
				Method isTeleport = ((Enum<?>) type).getDeclaringClass().getMethod("isTeleport");
				data.put("isTeleport", isTeleport.invoke(type));
			}
			catch (Throwable ignored)
			{
				// Purely informational -- travel.py decides executability from whether there's an
				// object to click, not from this. Absent is fine.
			}
		}
		data.put("displayInfo", invoke(transport, "getDisplayInfo"));
		data.put("duration", invoke(transport, "getDuration"));
		data.put("consumable", invoke(transport, "isConsumable"));

		// "Open Door 9398" / "Climb-down Ladder 9726" -- option, object name, object id, in that
		// order, space-separated (per transports.tsv's "menuOption menuTarget objectID" column).
		// Not every transport has one (teleport spells/items act on your inventory, not an
		// object), so this is left out entirely when objectInfo is blank.
		String objectInfo = (String) invoke(transport, "getObjectInfo");
		if (objectInfo != null && !objectInfo.trim().isEmpty())
		{
			data.put("objectInfo", objectInfo);
			String[] parts = objectInfo.trim().split("\\s+");
			if (parts.length >= 2)
			{
				try
				{
					int objectId = Integer.parseInt(parts[parts.length - 1]);
					data.put("objectOption", parts[0]);
					data.put("objectName", String.join(" ", java.util.Arrays.copyOfRange(parts, 1, parts.length - 1)));
					data.put("objectId", objectId);
				}
				catch (NumberFormatException ignored)
				{
					// Doesn't match the expected "option name... id" shape -- objectInfo above is
					// still there for a caller that wants to parse it differently.
				}
			}
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
