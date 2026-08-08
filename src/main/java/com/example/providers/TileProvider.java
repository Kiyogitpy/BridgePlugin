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
	// See ObjectProvider's identical cache for why -- walking the whole tile grid is real work,
	// and a caller polling faster than the game state changes (confirmed: something was hitting
	// /tiles, /objects, and /ground-items multiple times a second) turns that into repeated full
	// walks and was a real, measurable source of client stutter. Tiles only change once per game
	// tick regardless of poll rate, so caching by tick count adds no real staleness.
	private int cachedTick = -1;
	private List<Map<String, Object>> cachedTiles;

	public TileProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/tiles", () -> onClient(this::getTiles));
	}

	private Object getTiles()
	{
		int tick = client.getTickCount();
		if (cachedTiles == null || tick != cachedTick)
		{
			cachedTiles = computeTiles();
			cachedTick = tick;
		}
		return cachedTiles;
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
