package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.InstanceCoords;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.game.ItemManager;

import java.awt.Point;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class GroundItemProvider extends BridgeProvider
{
	private final ItemManager itemManager;

	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public GroundItemProvider(Client client, ClientThread clientThread, BridgeHttpServer server, ItemManager itemManager, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.itemManager = itemManager;
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/ground-items", query -> getGroundItems(isFresh(query)));
		server.registerQuery("/ground-item/at", query -> onClient(() -> getGroundItemAt(query)));
	}

	/**
	 * Exact lookup by tile position + item id (uncached, unlike the bulk /ground-items) -- the
	 * ground-item equivalent of ObjectProvider's /object/at and NPCProvider's /npc/by-index, for
	 * re-checking/re-aiming at a specific item already committed to looting mid-approach. Ground
	 * items have no per-instance index either; position + id is the same composite identity
	 * /object/at already uses, with the same caveat: two identical item drops on the exact same
	 * tile are indistinguishable by this key, same as two identical objects would be -- not
	 * expected to matter for tracking a single loot target. computeGroundItems already scans the
	 * whole current-plane tile grid unconditionally (no radius param to reuse, unlike
	 * computeObjects), so no extra bounding is needed here.
	 */
	private Object getGroundItemAt(Map<String, String> query)
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

		String kx = InstanceCoords.wantsInstance(query) ? "instX" : "x";
		String ky = InstanceCoords.wantsInstance(query) ? "instY" : "y";
		for (Map<String, Object> item : computeGroundItems())
		{
			if (id.equals(item.get("id")) && x.equals(item.get(kx)) && y.equals(item.get(ky))
				&& plane.equals(item.get("plane")))
			{
				Map<String, Object> found = new HashMap<>(item);
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

	private Object getGroundItems(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> (List<Map<String, Object>>) onClient(this::computeGroundItems));
	}

	private List<Map<String, Object>> computeGroundItems()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		Scene scene = client.getScene();
		if (scene == null)
		{
			return list;
		}

		Tile[][][] tiles = scene.getTiles();
		int plane = client.getPlane();
		if (tiles == null || plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			// The scene object exists but this plane's tile grid isn't populated yet -- e.g. not
			// logged in, or mid floor-transition/teleport. Bailing out here (instead of throwing)
			// matters beyond correctness: an uncaught NPE here runs on the client thread and gets
			// logged with a full stack trace on every single poll, which for a caller hitting this
			// endpoint every tick is a real, continuous source of stutter, not just a cosmetic error.
			return list;
		}

		for (int x = 0; x < tiles[plane].length; x++)
		{
			for (int y = 0; y < tiles[plane][x].length; y++)
			{
				Tile tile = tiles[plane][x][y];
				if (tile == null)
				{
					continue;
				}

				ItemLayer layer = tile.getItemLayer();
				if (layer == null)
				{
					continue;
				}

				Node node = layer.getTop();
				while (node instanceof TileItem)
				{
					TileItem item = (TileItem) node;
					Map<String, Object> data = new HashMap<>();
					// Instance-template correction -- CONFIRMED live 2026-08-14 for
					// PlayerProvider/NPCProvider/ObjectProvider (same bug class, see their own
					// comments): tile.getWorldLocation() alone is a raw instance-template
					// coordinate inside any instanced area. Only applied for the common
					// non-nested-WorldView case, same guard as the other three providers.
					LocalPoint tileLocal = tile.getLocalLocation();
					WorldPoint wp = tileLocal != null && tileLocal.getWorldView() <= WorldView.TOPLEVEL
						? WorldPoint.fromLocalInstance(client, tileLocal)
						: tile.getWorldLocation();

					data.put("id", item.getId());
					data.put("qty", item.getQuantity());
					data.put("x", wp.getX());
					data.put("y", wp.getY());
					data.put("plane", wp.getPlane());
					WorldPoint inst = tileLocal != null && tileLocal.getWorldView() <= WorldView.TOPLEVEL
						? InstanceCoords.instancePoint(client, tileLocal, wp.getPlane())
						: wp;
					data.put("instX", inst.getX());
					data.put("instY", inst.getY());
					// 2 == belongs to another player and isn't takeable by us yet (their private
					// kill-loot timer hasn't expired) -- same field RuneLite's own bundled Ground
					// Items plugin reads for its "Takeable" filter (GroundItemsPlugin
					// .shouldDisplayItem: `ownership != 2`). CONFIRMED live 2026-08-06 (user
					// report): the bot was attempting to loot other nearby players' drops in this
					// shared (non-instanced) Slayer cave, which are visible in the client's own
					// scene data same as ours but aren't actually ours to take.
					data.put("ownership", item.getOwnership());

					ItemComposition comp = client.getItemDefinition(item.getId());
					if (comp != null)
					{
						data.put("name", comp.getName());
						data.put("stackable", comp.isStackable());
					}

					int gePrice = itemManager.getItemPrice(item.getId());
					data.put("value", gePrice);
					data.put("totalValue", gePrice * (long) item.getQuantity());

					net.runelite.api.Point point = RuneLiteUtil.localToScreen(client, tile.getLocalLocation(), plane);
					if (point != null)
					{
						Point screen = RuneLiteUtil.canvasToScreen(client, point.getX(), point.getY());
						data.put("screenX", screen.x);
						data.put("screenY", screen.y);
					}

					data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
					data.putAll(RuneLiteUtil.visibility(client, tile.getLocalLocation(), plane, true));
					list.add(data);

					node = node.getNext();
				}
			}
		}
		return list;
	}
}
