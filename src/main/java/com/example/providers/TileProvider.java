package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.GeometryUtil;
import net.runelite.api.*;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class TileProvider extends BridgeProvider
{
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public TileProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/tiles", query -> getTiles(isFresh(query)));
		server.registerQuery("/tile/at", query -> onClient(() -> getTileAt(query)));
	}

	private Object getTiles(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> (List<Map<String, Object>>) onClient(this::computeTiles));
	}

	/**
	 * Exact lookup by world tile position (uncached, unlike the bulk /tiles) -- the tile
	 * equivalent of ObjectProvider's getObjectAt/GroundItemProvider's getGroundItemAt, for
	 * re-checking one specific tile's walkability/state without a caller re-scanning the whole
	 * plane every poll. Filters computeTiles()'s own already-computed list rather than converting
	 * the caller's (x, y, plane) back into a LocalPoint and indexing the scene array directly --
	 * same reasoning as ObjectProvider's getObjectAt: that reverse conversion is the exact class
	 * of instance-template bug already found for PlayerProvider/NPCProvider/ObjectProvider/
	 * GroundItemProvider (see CLAUDE.md's "Instanced areas break forward world-coordinate
	 * conversion"). Inherits /tiles' own existing limitation of only covering client.getPlane() --
	 * a lookup for a different plane always misses, same as the bulk endpoint already would.
	 */
	private Object getTileAt(Map<String, String> query)
	{
		Map<String, Object> notFound = new HashMap<>();
		notFound.put("found", false);

		Integer x = parseIntParam(query.get("x"));
		Integer y = parseIntParam(query.get("y"));
		Integer plane = parseIntParam(query.get("plane"));
		if (x == null || y == null || plane == null)
		{
			return notFound;
		}

		for (Map<String, Object> tile : computeTiles())
		{
			if (x.equals(tile.get("x")) && y.equals(tile.get("y")) && plane.equals(tile.get("plane")))
			{
				Map<String, Object> found = new HashMap<>(tile);
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

	private List<Map<String, Object>> computeTiles()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		Scene scene = client.getScene();
		if (scene == null)
		{
			return list;
		}

		CollisionData[] collisionMaps = client.getCollisionMaps();
		int plane = client.getPlane();
		Tile[][][] tiles = scene.getTiles();
		Player local = client.getLocalPlayer();
		if (tiles == null || plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			// The scene object exists but this plane's tile grid isn't populated yet -- e.g. not
			// logged in, or mid floor-transition/teleport. Bailing out here (instead of throwing)
			// matters beyond correctness: an uncaught NPE here runs on the client thread and gets
			// logged with a full stack trace on every single poll, which for a caller hitting this
			// endpoint every tick is a real, continuous source of stutter, not just a cosmetic error.
			return list;
		}

		for (int sx = 0; sx < tiles[plane].length; sx++)
		{
			for (int sy = 0; sy < tiles[plane][sx].length; sy++)
			{
				Tile tile = tiles[plane][sx][sy];
				if (tile == null)
				{
					continue;
				}

				WorldPoint wp = tile.getWorldLocation();
				Map<String, Object> data = new HashMap<>();
				data.put("x", wp.getX());
				data.put("y", wp.getY());
				data.put("plane", wp.getPlane());

				int flags = 0;
				if (collisionMaps != null && plane < collisionMaps.length && collisionMaps[plane] != null)
				{
					flags = collisionMaps[plane].getFlags()[sx][sy];
				}

				boolean blocked = (flags & CollisionDataFlag.BLOCK_MOVEMENT_FULL) != 0;
				boolean walkable = !blocked;
				data.put("walkable", walkable);
				data.put("blocked", blocked);
				data.put("reachable", walkable && local != null
					&& GeometryUtil.distanceToPlayer(client, wp) <= 20);

				// ADDED 2026-08-08 (user investigation): a ground effect that isn't any TileObject
				// (GameObject/WallObject/GroundObject/DecorativeObject -- see ObjectProvider),
				// GroundItem, NPC, GraphicsObject, or Projectile has to be something else -- the
				// remaining possibility is the tile's own paint/model recolor (SceneTilePaint for a
				// flat tile, SceneTileModel for a sloped/shaped one), which isn't an entity at all
				// and so wouldn't show up under any of those categories. Exposed here, not on
				// ObjectProvider, since this is a property of the Tile itself, not a TileObject on
				// it. Only the flat-paint case's 4 corner colors + texture are surfaced (the common
				// case for a plain ground tile); a shaped/sloped tile's SceneTileModel data isn't
				// pulled in yet since nothing has needed it -- add getModelOverlay()/
				// getTriangleColorA/B/C() etc. here the same way if a future comparison needs it.
				SceneTilePaint paint = tile.getSceneTilePaint();
				if (paint != null)
				{
					data.put("paintTexture", paint.getTexture());
					data.put("paintSwColor", paint.getSwColor());
					data.put("paintSeColor", paint.getSeColor());
					data.put("paintNwColor", paint.getNwColor());
					data.put("paintNeColor", paint.getNeColor());
				}

				// CONFIRMED live 2026-08-08 (user session): the golem-crafting "completed side"
				// tile has no SceneTilePaint at all (a normal flat ground tile does) -- meaning it's
				// a SceneTileModel instead, a genuinely different (sloped/shaped) tile mesh, not
				// just a recolor. modelOverlay/modelUnderlay identify which ground textures the
				// shape blends between; shape itself is the actual geometry variant id -- both are
				// enough to tell a completed side's tile apart from a plain flat one without needing
				// the full vertex/triangle arrays.
				SceneTileModel model = tile.getSceneTileModel();
				if (model != null)
				{
					data.put("modelShape", model.getShape());
					data.put("modelOverlay", model.getModelOverlay());
					data.put("modelUnderlay", model.getModelUnderlay());
				}

				list.add(data);
			}
		}
		return list;
	}
}
