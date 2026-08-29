package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.plugins.m8aq.api.export.GameOutput;
import net.runelite.client.plugins.m8aq.api.export.ReaderCatalog;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/**
 * Exposes the vendored RLitePlus/m8aq-plugins game-state readers as bridge endpoints.
 *
 * <p>The readers themselves live under {@code net.runelite.client.plugins.m8aq.api} and are a
 * byte-identical copy of upstream in their ORIGINAL package -- see that tree's VENDORED.md.
 * Nothing there may be edited; this class is the only adapter between them and our bridge, so
 * a re-sync (tools/sync-m8aq.sh) stays a clean overwrite.
 *
 * <p><b>Discovery is by reflection, on purpose.</b> {@link ReaderCatalog} finds every public
 * class under that package with a {@code public static State getState(Client)} method, so a
 * reader added upstream becomes an endpoint on the next client rebuild with no code change here
 * -- which is the whole point of vendoring verbatim. The cost is that a discovery failure
 * (a classloader that Guava's ClassPath scan cannot walk) would silently serve NOTHING rather
 * than failing loudly, so registration logs its count and {@code M8aqProviderTest} asserts a
 * floor on it.
 *
 * <p>Endpoints:
 * <ul>
 *   <li>{@code GET /api/<group>/<reader>} -- one per reader, kebab-cased
 *       (e.g. /api/storage/rune-pouch, /api/player/movement)</li>
 *   <li>{@code GET /api/all} -- {@link GameOutput}, every group in one snapshot</li>
 *   <li>{@code GET /api/index} -- the live path list, so the Python side can discover the
 *       surface without reading Java</li>
 * </ul>
 *
 * <p>Namespaced under {@code /api/} because eight reader names (Bank, Inventory, Equipment,
 * Prayer, Npcs, Skills, GroundItems, TileObjects) collide with endpoints we already serve in a
 * different JSON shape. The two coexist and mean different things.
 *
 * <p>No {@link TickCache}: every reader is a varbit/item-container read, not one of the bulk
 * scene scans that cache exists to make cheap. Each request reads fresh on the client thread,
 * the same as /player and /object/at.
 */
public class M8aqProvider extends BridgeProvider
{
	private static final String PREFIX = "/api/";

	/**
	 * Floor for the discovered reader count. Upstream had 67 at the 2026-08-25 sync; this only
	 * has to be low enough to tolerate upstream deleting a reader and high enough that a
	 * classloader regression (which yields 0) is caught. See the class comment.
	 */
	static final int MINIMUM_EXPECTED_READERS = 60;

	/** Reader class -> bridge path, in registration order. */
	private final Map<Class<?>, String> paths;

	public M8aqProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
		this.paths = buildPaths(ReaderCatalog.getClasses());
	}

	@Override
	public void register()
	{
		paths.forEach((type, path) -> server.register(path, () -> onClient(() -> capture(type))));
		server.register(PREFIX + "all", () -> onClient(() -> GameOutput.getState(client)));
		server.register(PREFIX + "index", this::index);

		System.out.println("Bridge: registered " + paths.size() + " m8aq reader endpoints under " + PREFIX);
		if (paths.size() < MINIMUM_EXPECTED_READERS)
		{
			// Loud rather than silent: a ClassPath scan that walks nothing looks exactly like a
			// working bridge with no data on it.
			System.err.println("Bridge: WARNING -- only " + paths.size() + " m8aq readers discovered, expected at"
				+ " least " + MINIMUM_EXPECTED_READERS + ". ReaderCatalog's classpath scan may have failed.");
		}
	}

	/**
	 * Maps every discovered reader to its endpoint path.
	 *
	 * <p>Package-private and static so the test can exercise the naming without a Client.
	 *
	 * @param types discovered reader classes
	 * @return reader class -> path, minus {@link GameOutput} (served as /api/all)
	 */
	static Map<Class<?>, String> buildPaths(List<Class<?>> types)
	{
		Map<Class<?>, String> byType = new LinkedHashMap<>();
		Map<String, Class<?>> byPath = new LinkedHashMap<>();

		for (Class<?> type : types)
		{
			if (type == GameOutput.class)
			{
				continue;
			}

			String path = PREFIX + kebab(ReaderCatalog.getGroupName(type)) + "/" + kebab(type.getSimpleName());
			Class<?> clash = byPath.put(path, type);
			if (clash != null)
			{
				// Two readers can only collide if upstream adds a same-named class in a nested
				// directory of the same group (skills/smithing already nests). Registering the
				// second would silently shadow the first, so keep the first and say so.
				System.err.println("Bridge: WARNING -- m8aq path " + path + " claimed by both "
					+ clash.getName() + " and " + type.getName() + "; keeping the first.");
				continue;
			}
			byType.put(type, path);
		}
		return Collections.unmodifiableMap(byType);
	}

	/**
	 * Converts a Java identifier to a URL segment: {@code RunePouch} -> {@code rune-pouch},
	 * {@code GuardiansOfTheRift} -> {@code guardians-of-the-rift}.
	 *
	 * @param name class or group name
	 * @return kebab-cased segment
	 */
	static String kebab(String name)
	{
		StringBuilder out = new StringBuilder(name.length() + 8);
		for (int i = 0; i < name.length(); i++)
		{
			char c = name.charAt(i);
			if (Character.isUpperCase(c) && i > 0 && !Character.isUpperCase(name.charAt(i - 1)))
			{
				out.append('-');
			}
			out.append(Character.toLowerCase(c));
		}
		return out.toString().toLowerCase(Locale.ROOT);
	}

	/**
	 * Invokes one reader on the client thread.
	 *
	 * @param type reader class
	 * @return the reader's state, or an {@code error} map when it threw
	 */
	private Object capture(Class<?> type)
	{
		try
		{
			return ReaderCatalog.capture(type, client);
		}
		catch (ReflectiveOperationException | RuntimeException e)
		{
			// One reader throwing must not 500 the endpoint in a way that reads as "bridge is
			// down". Several readers are interface-dependent and legitimately unhappy when the
			// interface is closed or the player is logged out.
			Map<String, Object> error = new LinkedHashMap<>();
			error.put("reader", type.getSimpleName());
			error.put("error", e.getClass().getSimpleName() + ": " + e.getMessage());
			return error;
		}
	}

	/** @return the registered path list, grouped, for discovery from Python */
	private Object index()
	{
		Map<String, List<String>> groups = new TreeMap<>();
		paths.forEach((type, path) ->
			groups.computeIfAbsent(ReaderCatalog.getGroupName(type), ignored -> new ArrayList<>()).add(path));
		groups.values().forEach(Collections::sort);

		Map<String, Object> data = new LinkedHashMap<>();
		data.put("count", paths.size());
		data.put("aggregate", PREFIX + "all");
		data.put("groups", groups);
		return data;
	}
}
