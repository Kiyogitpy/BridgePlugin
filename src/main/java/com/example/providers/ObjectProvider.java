package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class ObjectProvider extends BridgeProvider
{
	// Walking the whole scene's tile grid (and building screen-projection/composition data for
	// every object in it) is real, non-trivial work on the client thread -- cheap for one call,
	// but a caller hitting this endpoint faster than the game state actually changes (confirmed:
	// something was polling /objects, /tiles, and /ground-items multiple times a second) turns
	// into repeated full scene walks and was a real, measurable source of client stutter. The
	// scene only changes once per game tick regardless of how often this is polled, so caching by
	// tick count adds no real staleness while capping the expensive work to once per tick no
	// matter how many times something calls in.
	private int cachedTick = -1;
	private List<Map<String, Object>> cachedObjects;

	public ObjectProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/objects", () -> onClient(this::getObjects));
	}

	private Object getObjects()
	{
		int tick = client.getTickCount();
		if (cachedObjects == null || tick != cachedTick)
		{
			cachedObjects = computeObjects();
			cachedTick = tick;
		}
		return cachedObjects;
	}

	private List<Map<String, Object>> computeObjects()
	{
		List<Map<String, Object>> list = new ArrayList<>();

		Scene topLevelScene = client.getScene();
		if (topLevelScene != null)
		{
			walkScene(topLevelScene, client.getPlane(), list);
		}

		// Sailing's boats are WorldEntities with their own separate WorldView (own scene, own
		// plane numbering) -- fixtures built on the boat itself (the helm, a salvaging hook, ...)
		// live there, not in the top-level scene walked above, so standing on your own boat would
		// otherwise never see them at all. Confirmed live 2026-08-01: "Ship's wheel" and a
		// salvaging hook were completely absent from this endpoint while aboard a boat before this
		// was added, even though both were right in front of the player.
		Player localPlayer = client.getLocalPlayer();
		WorldView playerView = localPlayer != null ? localPlayer.getWorldView() : null;
		if (playerView != null && !playerView.isTopLevel())
		{
			Scene playerScene = playerView.getScene();
			if (playerScene != null)
			{
				walkScene(playerScene, playerView.getPlane(), list);
			}
		}

		return list;
	}

	private void walkScene(Scene scene, int plane, List<Map<String, Object>> out)
	{
		Tile[][][] tiles = scene.getTiles();
		if (tiles == null || plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			// The scene object exists but this plane's tile grid isn't populated yet -- e.g. not
			// logged in, or mid floor-transition/teleport. Bailing out here (instead of throwing)
			// matters beyond correctness: an uncaught NPE here runs on the client thread and gets
			// logged with a full stack trace on every single poll, which for a caller hitting this
			// endpoint every tick is a real, continuous source of stutter, not just a cosmetic error.
			return;
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

				for (GameObject obj : tile.getGameObjects())
				{
					if (obj != null)
					{
						out.add(buildObjectData(obj, "GameObject", plane));
					}
				}

				// Doors, gates, and fences are WallObjects, not GameObjects -- transports.tsv's
				// "Open Door"/"Open Gate" entries need these to be findable too, not just the
				// staircases/ladders that do tend to be GameObjects.
				WallObject wallObj = tile.getWallObject();
				if (wallObj != null)
				{
					out.add(buildObjectData(wallObj, "WallObject", plane));
				}

				GroundObject groundObj = tile.getGroundObject();
				if (groundObj != null)
				{
					out.add(buildObjectData(groundObj, "GroundObject", plane));
				}

				DecorativeObject decorativeObj = tile.getDecorativeObject();
				if (decorativeObj != null)
				{
					out.add(buildObjectData(decorativeObj, "DecorativeObject", plane));
				}
			}
		}
	}

	private Map<String, Object> buildObjectData(TileObject obj, String objectType, int plane)
	{
		Map<String, Object> data = new HashMap<>();
		LocalPoint local = obj.getLocalLocation();
		// x/y/plane/distance always resolved to real top-level-world coordinates (even for an
		// object that lives in a boat's own nested WorldView), so every object this endpoint
		// returns is comparable on equal terms -- see RuneLiteUtil.toTopLevelWorldPoint.
		WorldPoint wp = RuneLiteUtil.toTopLevelWorldPoint(client, local);

		data.put("id", obj.getId());
		data.put("objectType", objectType);
		ObjectComposition comp = client.getObjectDefinition(obj.getId());
		data.put("name", comp != null ? comp.getName() : "Unknown");
		data.put("x", wp.getX());
		data.put("y", wp.getY());
		data.put("plane", wp.getPlane());

		// Screen projection uses the object's own raw (possibly boat-local) LocalPoint, not the
		// top-level-transformed WorldPoint above -- Perspective.localToCanvas already handles a
		// LocalPoint tagged with a nested WorldView correctly on its own (it special-cases
		// LocalPoint.getWorldView() > -1 internally), and re-deriving a fresh LocalPoint from the
		// transformed WorldPoint would just be a lossier round-trip of the same information.
		net.runelite.api.Point point = RuneLiteUtil.localToScreen(client, local, plane);
		if (point != null)
		{
			Point screen = RuneLiteUtil.canvasToScreen(client, point.getX(), point.getY());
			data.put("screenX", screen.x);
			data.put("screenY", screen.y);
		}

		data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
		data.putAll(RuneLiteUtil.visibility(client, local, plane, true));
		return data;
	}
}
