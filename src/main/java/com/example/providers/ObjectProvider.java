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

	// Recursion guard for walkWorldViewRecursively -- WorldEntity/WorldView nesting is expected to
	// be a shallow tree (confirmed live 2026-08-02: the boat's helm alone needed 2 levels below
	// the top level -- the player's own view, plus one further WorldEntity nested inside that),
	// this is just insurance against an unexpected cycle turning into a runaway walk.
	private static final int MAX_WORLD_VIEW_DEPTH = 6;

	private List<Map<String, Object>> computeObjects()
	{
		List<Map<String, Object>> list = new ArrayList<>();

		WorldView topLevel = client.getTopLevelWorldView();
		if (topLevel != null)
		{
			walkWorldViewRecursively(topLevel, true, list, 0);
		}

		return list;
	}

	// Sailing's boats are WorldEntities with their own separate WorldView (own scene, own plane
	// numbering) -- fixtures built on the boat itself (the helm, a salvaging hook, ...) live
	// there, not in the top-level scene, so standing on your own boat would otherwise never see
	// them at all. Confirmed live 2026-08-01/02, in stages:
	//   1. Walking only the top-level scene missed every boat fixture entirely.
	//   2. Walking the player's own current WorldView (client.getLocalPlayer().getWorldView())
	//      found some fixtures (a salvaging hook) but not others (the helm) -- the helm turned
	//      out to sit on a different plane than the player within that same nested scene, fixed
	//      by walking every plane (Constants.MAX_Z) of a nested view instead of just the
	//      player's current one.
	//   3. Still missing: a /debug-menu dump of the actual menu entries under the mouse while
	//      hovering the helm showed its real clickable id resolves in a DIFFERENT worldViewId
	//      than the player's own (client.getLocalPlayer().getWorldView().getId()) -- i.e. the
	//      helm lives in a WorldEntity nested *inside* the boat's main WorldView, not the player's
	//      own view directly. WorldView.worldEntities() supports exactly this (a view can contain
	//      further WorldEntities, each with their own child WorldView), so rather than walking
	//      only the player's specific view, this recurses through every WorldEntity's view
	//      starting from the top level, at any depth, catching sub-views nothing else pointed at.
	private void walkWorldViewRecursively(WorldView view, boolean isTopLevel, List<Map<String, Object>> out, int depth)
	{
		if (depth > MAX_WORLD_VIEW_DEPTH)
		{
			return;
		}

		Scene scene = view.getScene();
		if (scene != null)
		{
			if (isTopLevel)
			{
				walkScene(scene, client.getPlane(), out);
			}
			else
			{
				// Unlike the top-level world (mutually-exclusive floors you switch between), a
				// nested WorldView's whole small structure is rendered together, all planes at
				// once -- walking every plane a scene can have is cheap for one this tiny, unlike
				// doing the same for the top-level world.
				for (int nestedPlane = 0; nestedPlane < Constants.MAX_Z; nestedPlane++)
				{
					walkScene(scene, nestedPlane, out);
				}
			}
		}

		for (WorldEntity entity : view.worldEntities())
		{
			WorldView childView = entity.getWorldView();
			if (childView != null)
			{
				walkWorldViewRecursively(childView, false, out, depth + 1);
			}
		}
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
		if (comp != null && comp.getImpostorIds() != null)
		{
			// Multiloc (varbit-conditional appearance) object -- the base composition's own name
			// is often a placeholder ("null") with the real displayed name only resolvable
			// through its current impostor variant. Confirmed live 2026-08-02: this is why the
			// boat's "Helm" (and several other boat fixtures seen throughout this session) kept
			// reporting name "null" here even once found -- the game's own menu-text resolution
			// (what a right-click label shows) already does this same impostor lookup.
			comp = comp.getImpostor();
		}
		data.put("name", comp != null ? comp.getName() : "Unknown");
		data.put("x", wp.getX());
		data.put("y", wp.getY());
		data.put("plane", wp.getPlane());

		// Prefer the object's actual on-screen clickable area (its model's real convex hull) over
		// a plain ground-tile projection -- TileObject.getClickbox()'s own javadoc says as much
		// ("you don't want [localToCanvas], use getClickbox() instead"). Confirmed live
		// 2026-08-01: clicking the ground-tile-projected point for the salvaging hook landed on
		// the deck tile beside it, not the hook itself, and did nothing -- the hook's visible
		// model sits off the tile's ground height enough for the two to diverge. Falls back to
		// the previous ground-tile projection when no clickbox is available right now (e.g. the
		// object isn't currently rendered) -- same behavior as before for that case. Uses the
		// object's own raw (possibly boat-local) LocalPoint/clickbox, not the top-level-transformed
		// WorldPoint above -- both Perspective.localToCanvas and getClickbox already handle a
		// LocalPoint tagged with a nested WorldView correctly on their own.
		java.awt.Shape clickbox = obj.getClickbox();
		java.awt.Rectangle clickBounds = clickbox != null ? clickbox.getBounds() : null;
		// Debug field (2026-08-02): which path buildObjectData actually took -- not consumed by
		// any routine.
		data.put("debugHasClickbox", clickBounds != null && clickBounds.width > 0 && clickBounds.height > 0);

		if (clickBounds != null && clickBounds.width > 0 && clickBounds.height > 0)
		{
			Point insideShape = pointInsideShape(clickbox, clickBounds);
			int canvasX = insideShape.x;
			int canvasY = insideShape.y;

			Point screen = RuneLiteUtil.canvasToScreen(client, canvasX, canvasY);
			data.put("screenX", screen.x);
			data.put("screenY", screen.y);

			boolean onScreen = RuneLiteUtil.isOnScreen(client, new net.runelite.api.Point(canvasX, canvasY));
			data.put("visible", true);
			data.put("onScreen", onScreen);
			data.put("clickable", onScreen);
		}
		else
		{
			net.runelite.api.Point point = RuneLiteUtil.localToScreen(client, local, plane);
			if (point != null)
			{
				Point screen = RuneLiteUtil.canvasToScreen(client, point.getX(), point.getY());
				data.put("screenX", screen.x);
				data.put("screenY", screen.y);
			}
			data.putAll(RuneLiteUtil.visibility(client, local, plane, true));
		}

		data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
		return data;
	}

	// Grid offsets (as fractions of the bounding box's width/height) tried in order after the
	// dead-center point fails Shape.contains() -- ordered roughly outward from center so the
	// first hit is usually still fairly central.
	private static final double[][] SHAPE_SAMPLE_FRACTIONS = {
		{0.5, 0.5}, {0.5, 0.3}, {0.5, 0.7}, {0.3, 0.5}, {0.7, 0.5},
		{0.3, 0.3}, {0.7, 0.3}, {0.3, 0.7}, {0.7, 0.7},
	};

	/**
	 * A point actually inside {@code shape}, not just inside its bounding box. Confirmed live
	 * 2026-08-02: for a non-convex or ring/spoke model (the boat's Helm -- a steering wheel),
	 * the bounding box's dead-center point can fall in the model's own "hole", which
	 * getClickbox()'s bounds alone can't tell you -- clicking that point missed the model
	 * entirely and landed on the tile behind it. Samples a small grid of candidate points across
	 * the bounding box and returns the first one Shape.contains() confirms is real, falling back
	 * to a vertex from the shape's own outline (guaranteed to be on it) if none of those land
	 * inside either.
	 */
	private Point pointInsideShape(java.awt.Shape shape, java.awt.Rectangle bounds)
	{
		for (double[] fraction : SHAPE_SAMPLE_FRACTIONS)
		{
			int x = bounds.x + (int) (bounds.width * fraction[0]);
			int y = bounds.y + (int) (bounds.height * fraction[1]);
			if (shape.contains(x, y))
			{
				return new Point(x, y);
			}
		}

		java.awt.geom.PathIterator it = shape.getPathIterator(null);
		double[] coords = new double[6];
		while (!it.isDone())
		{
			int segType = it.currentSegment(coords);
			if (segType == java.awt.geom.PathIterator.SEG_MOVETO || segType == java.awt.geom.PathIterator.SEG_LINETO)
			{
				return new Point((int) coords[0], (int) coords[1]);
			}
			it.next();
		}

		return new Point((int) bounds.getCenterX(), (int) bounds.getCenterY());
	}
}
