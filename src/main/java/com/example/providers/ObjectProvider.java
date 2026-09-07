package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.InstanceCoords;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.Perspective;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

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

		boolean inst = InstanceCoords.wantsInstance(query);
		String kx = inst ? "instX" : "x";
		String ky = inst ? "instY" : "y";
		Integer radius = parseIntParam(query.get("radius"));
		// I2 (2026-09-05, final-fix-brief.md): a Target is built for the inst=true case too, not
		// just the ordinary one -- an unfiltered scan pays the clickbox-hull cost (47-194ms
		// measured in this file's own Target comment) for every object in the radius, exactly the
		// cost that comment exists to avoid. See matchesTarget for the instance-space comparison.
		List<Map<String, Object>> objects = computeObjects(
			radius != null ? radius : OBJECT_AT_DEFAULT_RADIUS,
			new Target(x, y, plane, id, inst));
		for (Map<String, Object> obj : objects)
		{
			if (id.equals(obj.get("id")) && x.equals(obj.get(kx)) && y.equals(obj.get(ky))
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
		// interactable=true (opt-in, ADDED 2026-09-01): emit only objects with at least one
		// menu action. In the GOTR temple ~3,500 of 4,045 scanned objects are pure decor
		// (kelp, lace, guides) that no routine can ever click; skipping them BEFORE the
		// per-object work is what makes the bulk scan cheap. Default (absent/false) keeps
		// the full list -- existing consumers unchanged.
		boolean interactableOnly = "true".equalsIgnoreCase(query.get("interactable"));
		String cacheKey = (radius == null ? "" : radius.toString()) + "|" + interactableOnly;

		return cache.get(tickClock.get(), isFresh(query), cacheKey, () -> (List<Map<String, Object>>) onClient(() -> computeObjects(finalRadius, null, interactableOnly)));
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
		return computeObjects(radius, null);
	}

	/** See buildObjectData's collision block: hoisted out of the per-object path. Only ever read
	 *  and written on the client thread, inside one computeObjects call. */
	private CollisionData[] collisionMapsThisCall;

	private List<Map<String, Object>> computeObjects(Integer radius, Target want)
	{
		return computeObjects(radius, want, false);
	}

	/** See getObjects: only ever read/written on the client thread inside one call. */
	private boolean interactableOnlyThisCall;

	private List<Map<String, Object>> computeObjects(Integer radius, Target want, boolean interactableOnly)
	{
		interactableOnlyThisCall = interactableOnly;
		List<Map<String, Object>> list = new ArrayList<>();

		WorldView collisionView = client.getTopLevelWorldView();
		collisionMapsThisCall = collisionView != null ? collisionView.getCollisionMaps() : null;

		WorldView topLevel = client.getTopLevelWorldView();
		if (topLevel != null)
		{
			walkWorldViewRecursively(topLevel, true, list, 0, radius, want);
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
	private void walkWorldViewRecursively(WorldView view, boolean isTopLevel, List<Map<String, Object>> out, int depth, Integer radius, Target want)
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
				walkScene(scene, client.getPlane(), out, radius, want);
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
					walkScene(scene, nestedPlane, out, null, want);
				}
			}
		}

		for (WorldEntity entity : view.worldEntities())
		{
			WorldView childView = entity.getWorldView();
			if (childView != null)
			{
				walkWorldViewRecursively(childView, false, out, depth + 1, radius, want);
			}
		}
	}

	private void walkScene(Scene scene, int plane, List<Map<String, Object>> out, Integer radius, Target want)
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
						if (matchesTarget(want, obj, plane))
					{
						addIfBuilt(out, obj, "GameObject", plane);
					}
					}
				}

				// Doors, gates, and fences are WallObjects, not GameObjects -- transports.tsv's
				// "Open Door"/"Open Gate" entries need these to be findable too, not just the
				// staircases/ladders that do tend to be GameObjects.
				WallObject wallObj = tile.getWallObject();
				if (wallObj != null)
				{
					if (matchesTarget(want, wallObj, plane))
				{
					addIfBuilt(out, wallObj, "WallObject", plane);
				}
				}

				GroundObject groundObj = tile.getGroundObject();
				if (groundObj != null)
				{
					if (matchesTarget(want, groundObj, plane))
				{
					addIfBuilt(out, groundObj, "GroundObject", plane);
				}
				}

				DecorativeObject decorativeObj = tile.getDecorativeObject();
				if (decorativeObj != null)
				{
					if (matchesTarget(want, decorativeObj, plane))
				{
					addIfBuilt(out, decorativeObj, "DecorativeObject", plane);
				}
				}
			}
		}
	}

	/**
	 * Real top-level, instance-corrected world position of a scene object. Lifted out of
	 * buildObjectData 2026-08-28 so the /object/at pre-filter can compare positions WITHOUT
	 * paying for the rest of that method; one implementation, so the filter and the built
	 * result can never disagree about where an object is.
	 */
	private WorldPoint worldPointOf(TileObject obj)
	{
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
		return wp;
	}

	/**
	 * What /object/at is looking for. Lets the scene walk reject an object BEFORE paying for
	 * its composition/impostor lookup and its clickbox convex hull.
	 *
	 * MEASURED 2026-08-28 live: /object/at cost 70ms median in a mining area and 193.9ms in a
	 * Falador house (9205 objects inside the default radius), against the 54ms of firmware
	 * motion human_move keeps queued -- so every mid-flight re-aim drained the queue and
	 * visibly froze the cursor. Over one live Mahogany Homes run: 28% of flights froze, 72
	 * freeze events, 45% of total flight time, worst single freeze 206ms.
	 * Shrinking the radius is NOT sufficient -- radius 12 still measured 47ms median in that
	 * scene -- because the cost is per-OBJECT (clickbox hull plus grid sampling in
	 * RuneLiteUtil.shapeCoords), not per-tile. Filtering first makes it per-tile.
	 */
	private static final class Target
	{
		private final int x, y, plane, id;
		// I2 (2026-09-05): x/y are instance-space (instX/instY) when true, ordinary top-level
		// world coordinates otherwise -- see matchesTarget.
		private final boolean inst;

		private Target(int x, int y, int plane, int id, boolean inst)
		{
			this.x = x;
			this.y = y;
			this.plane = plane;
			this.id = id;
			this.inst = inst;
		}
	}

	/** id first: a free field read that rejects essentially everything. */
	private boolean matchesTarget(Target want, TileObject obj, int plane)
	{
		if (want == null)
		{
			return true;
		}
		if (obj.getId() != want.id)
		{
			return false;
		}
		// I2 (2026-09-05, final-fix-brief.md): compare in whichever space the caller asked for.
		// instX/instY (base + scene arithmetic, InstanceCoords) for inst=true -- the same space
		// getObjectAt compares its result against below -- else the ordinary top-level world
		// point. Both need the top-level-corrected point for a nested WorldView (a boat's own
		// fixtures), same guard as worldPointOf/buildObjectData use everywhere else.
		LocalPoint local = obj.getLocalLocation();
		WorldPoint wp = want.inst && local.getWorldView() <= WorldView.TOPLEVEL
			? InstanceCoords.instancePoint(client, local, plane)
			: worldPointOf(obj);
		return wp != null && wp.getX() == want.x && wp.getY() == want.y
			&& wp.getPlane() == want.plane;
	}

	/** buildObjectData, tolerating the interactable-only filter's null (skipped object). */
	private void addIfBuilt(List<Map<String, Object>> out, TileObject obj, String objectType, int plane)
	{
		Map<String, Object> built = buildObjectData(obj, objectType, plane);
		if (built != null)
		{
			out.add(built);
		}
	}

	/** id -> "has any menu action", for NON-multiloc ids only (a multiloc's current impostor
	 *  -- and so its actions -- changes with varbits mid-session: cell tiles, doors; those
	 *  resolve fresh every time). Object definitions are otherwise static for the session,
	 *  and the repeated composition+action resolution for ~4k objects per tick was about
	 *  half of the interactable-only scan's remaining cost (MEASURED 2026-09-01: 110ms for
	 *  220 emitted of 4,052 scanned). Client thread only, no synchronization. */
	private final Map<Integer, Boolean> interactableById = new HashMap<>();

	/** Returns null (object skipped) when interactableOnlyThisCall is set and the object has
	 *  no menu actions -- the check runs BEFORE the expensive per-object work (world-point
	 *  instance correction, collision, clickbox hull/projection), which is the whole point. */
	private Map<String, Object> buildObjectData(TileObject obj, String objectType, int plane)
	{
		if (interactableOnlyThisCall)
		{
			Boolean known = interactableById.get(obj.getId());
			if (known != null && !known)
			{
				return null;    // cached non-interactable: skip even the composition lookup
			}
		}

		Map<String, Object> data = new HashMap<>();
		LocalPoint local = obj.getLocalLocation();

		data.put("id", obj.getId());
		data.put("objectType", objectType);
		ObjectComposition comp = client.getObjectDefinition(obj.getId());
		boolean multiloc = comp != null && comp.getImpostorIds() != null;
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

		// ADDED 2026-08-28: the object's menu actions, impostor-resolved (same `comp` the name above
		// comes from), nulls and blanks dropped, order preserved -- index 0 is the left-click default.
		// Needed because a CLOSED door and an OPEN one are both WallObjects named "Door" with
		// per-door ids (Falador's is 24050 closed / 24051 open, Varrock's 11775, Ardougne's 40178),
		// so neither `name` nor `id` can tell them apart without a hardcoded id table. The action
		// can: "Open" vs "Close". travel.py's door recovery keys on exactly this.
		//
		// Cheap enough for the bulk scan: getActions() is a field read on a composition already
		// fetched above, and the list is at most 5 short strings -- nothing like the clickbox convex
		// hull that dominates this method's cost (see the /object/at pre-filter note above).
		if (comp != null && comp.getActions() != null)
		{
			List<String> actions = new ArrayList<>();
			for (String action : comp.getActions())
			{
				if (action != null && !action.isEmpty())
				{
					actions.add(action);
				}
			}
			data.put("actions", actions);
		}

		boolean hasActions = data.containsKey("actions")
			&& !((List<?>) data.get("actions")).isEmpty();
		if (!multiloc)
		{
			interactableById.put(obj.getId(), hasActions);
		}
		if (interactableOnlyThisCall && !hasActions)
		{
			return null;
		}

		WorldPoint wp = worldPointOf(obj);

		// ADDED 2026-08-28 (user's suggestion, after the action check alone kept picking the wrong
		// door): the client's OWN collision entry for this object's tile -- the same data the game
		// walks the player with. One int carrying BOTH families of CollisionDataFlag bits:
		// movement (NORTH 2 / EAST 8 / SOUTH 32 / WEST 128 / OBJECT 256 / FULL 2359552) and line
		// of sight (NORTH 1024 / EAST 4096 / SOUTH 16384 / WEST 65536 / FULL 131072).
		//
		// Why it matters for doors: "Open" in the action list says the door CAN be opened, not
		// that it is currently shut in a way that blocks us -- and neither the name nor the id
		// distinguishes the two states. The collision entry does, directly and without inference:
		// a shut door blocks movement (and sight) across its edge, an open one does not. It also
		// removes the need to GUESS which of several nearby doors is in the way, which is what
		// travel.py's routeless fallback was getting wrong (CONFIRMED live at Barbara's house,
		// Hosidius: two shut doors within 4 tiles, both FURTHER from the goal than the player, so
		// the distance rule rejected both and the walk never recovered).
		//
		// Top-level view only -- a nested WorldView (a boat's deck) has its own collision map and
		// its own scene indices, and mixing them would read a meaningless tile. Absent field means
		// "could not tell", same contract as `actions`.
		if (local != null && local.getWorldView() <= WorldView.TOPLEVEL)
		{
			// Resolved ONCE per request, not once per object. MEASURED 2026-08-29: /objects walks
			// ~9200 objects in a Falador house, and doing getTopLevelWorldView() +
			// getCollisionMaps() inside that loop put two virtual calls per object on the client
			// thread for a value that cannot change mid-request -- which the user felt as lag
			// spikes while the cursor was in flight, since routines poll /objects every tick.
			CollisionData[] maps = collisionMapsThisCall;
			if (maps != null && plane >= 0 && plane < maps.length && maps[plane] != null)
			{
				int[][] flags = maps[plane].getFlags();
				int sx = local.getSceneX();
				int sy = local.getSceneY();
				if (flags != null && sx >= 0 && sx < flags.length
					&& flags[sx] != null && sy >= 0 && sy < flags[sx].length)
				{
					data.put("collisionFlags", flags[sx][sy]);
				}
			}
		}

		data.put("x", wp.getX());
		data.put("y", wp.getY());
		data.put("plane", wp.getPlane());

		WorldPoint inst = local != null && local.getWorldView() <= WorldView.TOPLEVEL
			? InstanceCoords.instancePoint(client, local, wp.getPlane())
			: wp;
		data.put("instX", inst.getX());
		data.put("instY", inst.getY());

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
		// Behavior is unchanged; the implementation moved to RuneLiteUtil.shapeCoords 2026-08-24
		// so NPCProvider/PvpProvider could aim at Actor.getConvexHull() the same way instead of
		// duplicating the grid-sampling logic. See that method for the full rationale.
		// Clickbox hulls DOMINATE this method's cost, and most of a scene never needs one:
		// MEASURED 2026-09-01 in the GOTR temple (4,045 objects, hundreds of them decorative
		// kelp/lace), one bulk scan cost ~450-500ms of CLIENT THREAD time -- ~75% of the whole
		// 600ms tick budget while a routine polls /objects each tick, USER-felt as constant
		// stutter that persisted on an EMPTY world (which ruled out UDP event volume). An
		// object with NO menu actions cannot be clicked, so nothing ever aims at it: skip the
		// hull and let shapeCoords fall back to the cheap ground-tile projection for those.
		// Contract unchanged -- every field still present, `usedClickbox` false for decor.
		boolean interactable = data.containsKey("actions")
			&& !((List<?>) data.get("actions")).isEmpty();
		Map<String, Object> coords = RuneLiteUtil.shapeCoords(
			client, interactable ? obj.getClickbox() : null, local, plane, true);
		data.putAll(coords);
		// Pre-existing field name, kept so anything already reading it doesn't break; shapeCoords
		// reports the same fact as "usedClickbox".
		data.put("debugHasClickbox", coords.get("usedClickbox"));

		// The tile's RENDER HEIGHT, and the only thing that can tell the Motherlode Mine's two
		// floors apart. ADDED 2026-09-07 after a live run mined a DOWNSTAIRS vein from the upper
		// level (USER: "clicking lower level ore from highground"): the upper level is not a
		// plane and it is not a separate part of the map -- it sits DIRECTLY ABOVE the lower one
		// at the same x/y, so world coordinates, the collision map and a reachability flood all
		// answer "same floor" about two tiles a storey apart, and /objects lists both.
		//
		// RuneLite's own MotherlodePlugin has the same problem and solves it exactly this way
		// (isUpstairs: `Perspective.getTileHeight(client, localPoint, 0) < -490`), and its scene
		// overlay hides a vein whose isUpstairs disagrees with the player's. Reported here as the
		// raw height rather than a boolean because the -490 threshold is that mine's fact, not
		// this provider's -- routines/motherlode_mine.py owns the constant, alongside its other
		// measured ids.
		//
		// Cheap: a couple of array lookups and a bilinear interpolation per object, against the
		// clickbox hulls that dominate this method.
		if (local != null)
		{
			data.put("tileHeight", Perspective.getTileHeight(client, local, plane));
		}

		data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
		return data;
	}

}
