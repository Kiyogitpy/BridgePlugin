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
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how. radius is passed as TickCache's extra key (see ObjectProvider's own historical note
	// on why a radius-bounded call and an unbounded one must not share a cache entry) -- unlike
	// the old cachedRadius field, TickCache's Objects.equals-based key comparison handles a null
	// radius as its own distinct key with no sentinel value needed.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public ObjectProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	// Default search radius (tiles, around the player) for /object/at -- generous since a caller
	// looking this up already has an approximate world position for something it's about to
	// click, which is necessarily within normal interaction range already.
	private static final int OBJECT_AT_DEFAULT_RADIUS = 30;

	@Override
	public void register()
	{
		server.registerQuery("/objects", this::getObjects);
		server.registerQuery("/object/at", query -> onClient(() -> getObjectAt(query)));
		server.registerQuery("/debug-objects-all-planes", query -> onClient(() -> getObjectsAllPlanes(query)));
	}

	/**
	 * Exact lookup by tile position + object id (uncached, unlike the bulk /objects) -- the
	 * object equivalent of NPCProvider's getNpcByIndex, for re-checking/re-aiming at a specific
	 * object already committed to mid-approach (e.g. a moving player about to click one). Static
	 * objects have no NPC-like per-instance index; position + id (stable across a multiloc
	 * object's own appearance changes -- see buildObjectData's own comment) is the closest
	 * equivalent identity.
	 *
	 * Deliberately reuses computeObjects' own scene walk (same instance-corrected world
	 * coordinates, same clickbox resolution) and filters the result, rather than converting the
	 * caller's (x, y, plane) back into a LocalPoint via LocalPoint.fromWorld and indexing the
	 * tile array directly -- CONFIRMED live 2026-08-14 (see PlayerProvider/NPCProvider/this
	 * class's own regionId/coordinate fixes) that the forward real-world-to-local conversion is
	 * NOT reliably instance-aware in this codebase, the same class of bug already found for
	 * CombatProvider's /minimap/walk and /ground/walk. Filtering the already-correctly-resolved
	 * bulk list sidesteps that entirely instead of introducing a second, separately-fragile
	 * conversion path.
	 */
	private Object getObjectAt(Map<String, String> query)
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

		Integer radius = parseIntParam(query.get("radius"));
		List<Map<String, Object>> objects = computeObjects(radius != null ? radius : OBJECT_AT_DEFAULT_RADIUS);
		for (Map<String, Object> obj : objects)
		{
			if (id.equals(obj.get("id")) && x.equals(obj.get("x")) && y.equals(obj.get("y"))
				&& plane.equals(obj.get("plane")))
			{
				Map<String, Object> found = new HashMap<>(obj);
				found.put("found", true);
				return found;
			}
		}
		return notFound;
	}

	private Object getObjects(Map<String, String> query)
	{
		Integer radius = null;
		String radiusStr = query.get("radius");
		if (radiusStr != null && !radiusStr.isEmpty())
		{
			try
			{
				radius = Integer.parseInt(radiusStr);
			}
			catch (NumberFormatException ignored)
			{
			}
		}
		Integer finalRadius = radius;

		return cache.get(tickClock.get(), isFresh(query), radius, () -> (List<Map<String, Object>>) onClient(() -> computeObjects(finalRadius)));
	}

	// ADDED 2026-08-08 (user investigation): debug-only, uncached, deliberately not the normal
	// get_objects() path -- walkScene (used by computeObjects above) only ever walks
	// client.getPlane(), the player's own current plane, same as every other caller in this
	// codebase assumes. A golem-crafting "carved side" ground effect that isn't any TileObject
	// type, GroundItem, NPC, GraphicsObject, or Projectile (all already checked live) could still
	// be a real TileObject if it happens to sit on a DIFFERENT plane at the same (x, y) -- a known
	// trick for certain overlay-style ground effects. This scans every plane of the top-level
	// scene at an explicit (x, y), not just the player's current one, to test that specifically.
	// Skips buildObjectData's screen-projection/clickbox work entirely (meaningless for a plane
	// the client isn't currently rendering) and reports the plane it was actually found on
	// directly from the loop variable, not from buildObjectData's own "plane" field -- that field
	// comes from WorldPoint.fromLocal(client, local), which itself assumes client.getPlane(), so
	// it would silently mislabel anything found on another plane as being on the current one.
	private Object getObjectsAllPlanes(Map<String, String> query)
	{
		Integer x = parseIntParam(query.get("x"));
		Integer y = parseIntParam(query.get("y"));
		Integer radius = parseIntParam(query.get("radius"));
		int r = radius != null ? radius : 2;

		List<Map<String, Object>> list = new ArrayList<>();
		if (x == null || y == null)
		{
			return list;
		}

		Scene scene = client.getScene();
		Tile[][][] tiles = scene != null ? scene.getTiles() : null;
		if (tiles == null)
		{
			return list;
		}

		int px = x - scene.getBaseX();
		int py = y - scene.getBaseY();

		for (int plane = 0; plane < tiles.length; plane++)
		{
			if (tiles[plane] == null)
			{
				continue;
			}
			int width = tiles[plane].length;
			int height = width > 0 ? tiles[plane][0].length : 0;
			int xStart = Math.max(0, px - r);
			int xEnd = Math.min(width, px + r + 1);
			int yStart = Math.max(0, py - r);
			int yEnd = Math.min(height, py + r + 1);

			for (int sx = xStart; sx < xEnd; sx++)
			{
				for (int sy = yStart; sy < yEnd; sy++)
				{
					Tile tile = tiles[plane][sx][sy];
					if (tile == null)
					{
						continue;
					}

					for (GameObject obj : tile.getGameObjects())
					{
						if (obj != null)
						{
							list.add(buildDebugObjectData(obj, "GameObject", plane));
						}
					}
					WallObject wallObj = tile.getWallObject();
					if (wallObj != null)
					{
						list.add(buildDebugObjectData(wallObj, "WallObject", plane));
					}
					GroundObject groundObj = tile.getGroundObject();
					if (groundObj != null)
					{
						list.add(buildDebugObjectData(groundObj, "GroundObject", plane));
					}
					DecorativeObject decorativeObj = tile.getDecorativeObject();
					if (decorativeObj != null)
					{
						list.add(buildDebugObjectData(decorativeObj, "DecorativeObject", plane));
					}
				}
			}
		}
		return list;
	}

	private Map<String, Object> buildDebugObjectData(TileObject obj, String objectType, int plane)
	{
		Map<String, Object> data = new HashMap<>();
		data.put("id", obj.getId());
		data.put("objectType", objectType);
		data.put("plane", plane);
		ObjectComposition comp = client.getObjectDefinition(obj.getId());
		if (comp != null && comp.getImpostorIds() != null)
		{
			comp = comp.getImpostor();
		}
		data.put("name", comp != null ? comp.getName() : "Unknown");
		LocalPoint local = obj.getLocalLocation();
		data.put("sceneX", local.getSceneX());
		data.put("sceneY", local.getSceneY());
		return data;
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

	// Recursion guard for walkWorldViewRecursively -- WorldEntity/WorldView nesting is expected to
	// be a shallow tree (confirmed live 2026-08-02: the boat's helm alone needed 2 levels below
	// the top level -- the player's own view, plus one further WorldEntity nested inside that),
	// this is just insurance against an unexpected cycle turning into a runaway walk.
	private static final int MAX_WORLD_VIEW_DEPTH = 6;

	private List<Map<String, Object>> computeObjects(Integer radius)
	{
		List<Map<String, Object>> list = new ArrayList<>();

		WorldView topLevel = client.getTopLevelWorldView();
		if (topLevel != null)
		{
			walkWorldViewRecursively(topLevel, true, list, 0, radius);
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
	private void walkWorldViewRecursively(WorldView view, boolean isTopLevel, List<Map<String, Object>> out, int depth, Integer radius)
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
				// radius (tiles, scene-local) bounds the walk to a box around the player instead
				// of the whole loaded scene (up to 104x104 tiles) -- confirmed live 2026-08-02:
				// building clickbox/screen-projection data for every single object in the scene,
				// every game tick, on the client thread was a real source of client stutter for a
				// routine that only ever cares about objects within its own maxDistance/
				// maxSailDistance anyway. null preserves the old unbounded behavior for callers
				// that genuinely need the whole scene.
				walkScene(scene, client.getPlane(), out, radius);
			}
			else
			{
				// Unlike the top-level world (mutually-exclusive floors you switch between), a
				// nested WorldView's whole small structure is rendered together, all planes at
				// once -- walking every plane a scene can have is cheap for one this tiny, unlike
				// doing the same for the top-level world. Never radius-bounded -- a boat's own
				// deck is already far smaller than any reasonable radius.
				for (int nestedPlane = 0; nestedPlane < Constants.MAX_Z; nestedPlane++)
				{
					walkScene(scene, nestedPlane, out, null);
				}
			}
		}

		for (WorldEntity entity : view.worldEntities())
		{
			WorldView childView = entity.getWorldView();
			if (childView != null)
			{
				walkWorldViewRecursively(childView, false, out, depth + 1, radius);
			}
		}
	}

	private void walkScene(Scene scene, int plane, List<Map<String, Object>> out, Integer radius)
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

		int width = tiles[plane].length;
		int height = width > 0 ? tiles[plane][0].length : 0;
		int xStart = 0, xEnd = width;
		int yStart = 0, yEnd = height;

		if (radius != null)
		{
			// Scene-local bounds derived from the player's TOP-LEVEL world position (via
			// RuneLiteUtil.getTopLevelWorldLocation, same helper used elsewhere for this exact
			// nested-WorldView problem) and the scene's own base offset, not LocalPoint.getSceneX/Y
			// directly -- this branch only ever runs for the top-level scene (see the isTopLevel
			// check above), but a player standing on their own boat has a LocalLocation tagged to
			// the boat's nested WorldView, not the top level, so its raw scene coords would be
			// relative to the wrong (tiny, boat-local) scene entirely.
			WorldPoint playerWp = RuneLiteUtil.getTopLevelWorldLocation(client, client.getLocalPlayer());
			int px = playerWp.getX() - scene.getBaseX();
			int py = playerWp.getY() - scene.getBaseY();
			xStart = Math.max(0, px - radius);
			xEnd = Math.min(width, px + radius + 1);
			yStart = Math.max(0, py - radius);
			yEnd = Math.min(height, py + radius + 1);
		}

		for (int x = xStart; x < xEnd; x++)
		{
			for (int y = yStart; y < yEnd; y++)
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

		// Instance-template correction -- CONFIRMED live 2026-08-14 for PlayerProvider/
		// NPCProvider (same bug class, see their own comments), found here by inspection while
		// adding /object/at rather than by a live symptom: toTopLevelWorldPoint above only
		// handles the nested-WorldView case (a boat's own fixtures), not classic instance-
		// template chunk remapping, so every object's x/y/plane -- and hence "distance" -- would
		// be a raw, meaningless instance-template coordinate inside any instanced area (a
		// minigame, a dungeon, a boss room). WorldPoint.fromLocalInstance() does that correction;
		// only applied for the common non-nested-WorldView case, same guard as PlayerProvider's.
		if (local.getWorldView() <= WorldView.TOPLEVEL)
		{
			wp = WorldPoint.fromLocalInstance(client, local);
		}

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
		// ADDED 2026-08-12 (Mastering Mixology live verification): "id" above is deliberately left
		// as the object's raw base/impostor-table id -- unchanged, since other routines (e.g.
		// golem_crafting.py's plinth tracking) rely on it staying stable as an identity key across
		// a multiloc object's own state changes. But some routines need the OPPOSITE: which content
		// variant is currently showing (e.g. Mastering Mixology's mixing vessel/retort/agitator/
		// alembic, whose "id" always reports 55395/55389/55390/55391 -- the multiloc's own base
		// definition id -- regardless of what potion, if any, is actually in it; CONFIRMED live
		// 2026-08-12: the vessel showed id=55395 name="Mixing vessel (Empty)", never the
		// content-variant ids 54918-54928 the plan's VESSEL_ID_TO_BASE table expects). This field
		// is the impostor-resolved id (same as "id" for a non-multiloc object) for exactly that use.
		data.put("impostorId", comp != null ? comp.getId() : obj.getId());
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
