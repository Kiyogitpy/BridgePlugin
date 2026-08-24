package com.example.util;

import net.runelite.api.Actor;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.NPC;
import net.runelite.api.Perspective;
import net.runelite.api.WorldEntity;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;

import java.awt.Canvas;
import java.awt.Dimension;
import java.awt.Point;import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

public final class RuneLiteUtil
{
	private RuneLiteUtil()
	{
	}

	public static Object runOnClientThread(Client client, net.runelite.client.callback.ClientThread clientThread, Supplier<Object> supplier)
	{
		AtomicReference<Object> result = new AtomicReference<>();
		CountDownLatch latch = new CountDownLatch(1);

		clientThread.invokeLater(() -> {
			try
			{
				result.set(supplier.get());
			}
			finally
			{
				latch.countDown();
			}
		});

		try
		{
			latch.await();
		}
		catch (InterruptedException e)
		{
			Thread.currentThread().interrupt();
		}

		return result.get();
	}

	public static Point getCanvasScreenLocation(Client client)
	{
		Canvas canvas = client.getCanvas();
		if (canvas == null || !canvas.isShowing())
		{
			return new Point(0, 0);
		}
		return canvas.getLocationOnScreen();
	}

	public static net.runelite.api.Point localToScreen(Client client, LocalPoint local, int plane)
	{
		if (local == null)
		{
			return null;
		}
		return Perspective.localToCanvas(client, local, plane);
	}

	/**
	 * Ratio between the canvas's actual on-screen pixel size and the game's internal render
	 * resolution. Widget bounds and Perspective's screen projections are always computed in
	 * that internal resolution, so in stretched mode (canvas resized without the game engine
	 * re-rendering at that size) a raw canvas-space coordinate lands in the wrong place unless
	 * scaled back up by this ratio first. 1:1 whenever stretched mode is off.
	 */
	public static double[] getStretchScale(Client client)
	{
		if (!client.isStretchedEnabled())
		{
			return new double[]{1.0, 1.0};
		}

		Dimension real = client.getRealDimensions();
		Dimension stretched = client.getStretchedDimensions();
		if (real == null || stretched == null || real.width <= 0 || real.height <= 0)
		{
			return new double[]{1.0, 1.0};
		}

		return new double[]{(double) stretched.width / real.width, (double) stretched.height / real.height};
	}

	/**
	 * Converts a canvas-space coordinate (as returned by Perspective/Widget.getBounds()) into
	 * an absolute on-screen point, accounting for stretched-mode scaling.
	 */
	public static Point canvasToScreen(Client client, int canvasX, int canvasY)
	{
		double[] scale = getStretchScale(client);
		Point canvasLoc = getCanvasScreenLocation(client);
		int x = canvasLoc.x + (int) Math.round(canvasX * scale[0]);
		int y = canvasLoc.y + (int) Math.round(canvasY * scale[1]);
		return new Point(x, y);
	}

	public static Map<String, Object> screenCoords(Client client, LocalPoint local, int plane)
	{
		Map<String, Object> coords = new HashMap<>();
		net.runelite.api.Point point = localToScreen(client, local, plane);
		if (point != null)
		{
			Point screen = canvasToScreen(client, point.getX(), point.getY());
			coords.put("screenX", screen.x);
			coords.put("screenY", screen.y);
		}
		return coords;
	}

	public static boolean isOnScreen(Client client, net.runelite.api.Point canvasPoint)
	{
		if (canvasPoint == null)
		{
			return false;
		}

		int vw = client.getViewportWidth();
		int vh = client.getViewportHeight();
		if (vw <= 0 || vh <= 0)
		{
			return false;
		}

		int x = canvasPoint.getX();
		int y = canvasPoint.getY();
		return x >= 0 && y >= 0 && x <= vw && y <= vh;
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
	 * the bounding box's dead-center point can fall in the model's own "hole", which the bounds
	 * alone can't tell you -- clicking that point missed the model entirely and landed on the
	 * tile behind it. Samples a small grid of candidate points across the bounding box and
	 * returns the first one Shape.contains() confirms is real, falling back to a vertex from the
	 * shape's own outline (guaranteed to be on it) if none of those land inside either.
	 *
	 * Moved here from ObjectProvider 2026-08-24 when NPCProvider/PvpProvider needed the same
	 * logic for Actor.getConvexHull(); it was never object-specific, only object-first.
	 */
	public static Point pointInsideShape(java.awt.Shape shape, java.awt.Rectangle bounds)
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

	/**
	 * screenX/screenY/visible/onScreen/clickable for an entity that has a real on-screen model,
	 * aiming at a point genuinely inside its clickable shape rather than at the ground-tile
	 * projection under its feet. Falls back to exactly the old localToScreen + visibility()
	 * behavior whenever no usable shape is available right now (model not loaded, off-screen,
	 * or an entity type that has no hull at all).
	 *
	 * Generalizes what ObjectProvider has done since 2026-08-01 via TileObject.getClickbox() --
	 * where the ground-tile point was CONFIRMED live to miss (a boat's salvaging hook, whose
	 * model sits far enough off the tile's ground height that clicking the tile did nothing).
	 * Actors (NPCs, players) expose the equivalent shape as Actor.getConvexHull().
	 *
	 * The returned point is CANVAS space converted through canvasToScreen -- both getClickbox()
	 * and getConvexHull() project into the game's real (pre-stretch) render resolution, so they
	 * need exactly the same stretch handling as Perspective's own output. Do not convert twice.
	 */
	public static Map<String, Object> shapeCoords(Client client, java.awt.Shape shape,
												   LocalPoint fallbackLocal, int plane, boolean alive)
	{
		java.awt.Rectangle bounds = shape != null ? shape.getBounds() : null;
		if (bounds != null && bounds.width > 0 && bounds.height > 0)
		{
			Point inside = pointInsideShape(shape, bounds);
			Point screen = canvasToScreen(client, inside.x, inside.y);
			boolean onScreen = isOnScreen(client, new net.runelite.api.Point(inside.x, inside.y));

			Map<String, Object> data = new HashMap<>();
			data.put("screenX", screen.x);
			data.put("screenY", screen.y);
			data.put("visible", alive);
			data.put("onScreen", onScreen);
			data.put("clickable", onScreen && alive);
			// Debug field: which path this took. Not consumed by any routine, but it's the only
			// way to tell a real hull-aimed point from a fallback tile-centre one on the wire.
			data.put("usedClickbox", true);
			return data;
		}

		Map<String, Object> data = new HashMap<>(screenCoords(client, fallbackLocal, plane));
		data.putAll(visibility(client, fallbackLocal, plane, alive));
		data.put("usedClickbox", false);
		return data;
	}

	public static Map<String, Object> visibility(Client client, LocalPoint local, int plane, boolean alive)
	{
		Map<String, Object> vis = new HashMap<>();
		net.runelite.api.Point canvasPoint = localToScreen(client, local, plane);

		boolean onScreen = isOnScreen(client, canvasPoint);
		vis.put("visible", canvasPoint != null && alive);
		vis.put("onScreen", onScreen);
		vis.put("clickable", onScreen && alive && canvasPoint != null);
		return vis;
	}

	public static Map<String, Object> itemSlot(Client client, int itemId, int quantity)
	{
		Map<String, Object> slot = new HashMap<>();
		if (itemId <= 0)
		{
			return slot;
		}

		slot.put("id", itemId);
		slot.put("qty", quantity);

		ItemComposition comp = client.getItemDefinition(itemId);
		if (comp != null)
		{
			slot.put("name", comp.getName());
			slot.put("stackable", comp.isStackable());
		}
		return slot;
	}

	public static boolean isNpcAttackable(NPC npc)
	{
		if (npc == null || npc.getName() == null)
		{
			return false;
		}

		if (npc.getHealthRatio() == 0)
		{
			return false;
		}

		return npc.getCombatLevel() > 0;
	}

	public static boolean isNpcDead(NPC npc)
	{
		return npc != null && npc.getHealthRatio() == 0;
	}

	/**
	 * Whether the NPC's own composition actually defines an "Attack" menu option -- a more
	 * semantically direct attackability signal than {@link #isNpcAttackable}'s combatLevel/health
	 * heuristic, which can't distinguish "attackable" from "just has a nonzero combat level for
	 * flavor" (or miss an attackable NPC with combatLevel 0). Ported from the sideloaded "Target
	 * True Tile" plugin's own {@code isNpcAttackable} (decompiled to
	 * data/decompiled/target-true-tile/, 2026-08-14) -- that plugin checks only
	 * {@code actions[1] != null} (a fixed-slot convention); this generalizes to a case-insensitive
	 * scan of every action slot instead of assuming "Attack" is always at index 1, since that
	 * position isn't guaranteed for every NPC composition.
	 */
	public static boolean hasAttackOption(NPC npc)
	{
		if (npc == null || npc.getComposition() == null)
		{
			return false;
		}

		String[] actions = npc.getComposition().getActions();
		if (actions == null)
		{
			return false;
		}

		for (String action : actions)
		{
			if (action != null && action.equalsIgnoreCase("Attack"))
			{
				return true;
			}
		}
		return false;
	}

	public static int distanceToPlayer(Client client, WorldPoint wp)
	{
		return GeometryUtil.distanceToPlayer(client, wp);
	}

	/**
	 * A local point's position in the top-level world, even when the point belongs to a nested
	 * WorldView -- e.g. anything on one of Sailing's boats, which are WorldEntities with their
	 * own separate WorldView (own scene, own plane numbering, own coordinate base).
	 * WorldPoint.fromLocal(client, local) alone resolves against whichever WorldView the point's
	 * own LocalPoint.getWorldView() names, which for a boat-local point means coordinates local
	 * to the boat's own view, not real Gielinor coordinates -- silently meaningless when compared
	 * against a WorldPoint from the top-level world (e.g. anything ObjectProvider/NPCProvider
	 * return by default, since those walk the deprecated Client.getScene()/getPlane(), which only
	 * ever see the top-level WorldView). Confirmed live 2026-08-01: every object's computed
	 * "distance" read back as Integer.MAX_VALUE while on a boat, because WorldPoint.distanceTo()
	 * returns that sentinel whenever the two points are on different planes -- which they always
	 * are here, since the boat's own WorldView numbers its plane independently of the top-level
	 * world's.
	 * <p>
	 * Mirrors the lookup Perspective.localToCanvas itself uses internally for the same
	 * point.getWorldView() &gt; -1 case (WorldView.TOPLEVEL) -- the owning WorldEntity is
	 * addressable directly via the top-level view's own worldEntities().byIndex(id), not a
	 * linear search.
	 */
	public static WorldPoint toTopLevelWorldPoint(Client client, LocalPoint local)
	{
		if (local.getWorldView() <= WorldView.TOPLEVEL)
		{
			return WorldPoint.fromLocal(client, local);
		}

		WorldView topLevel = client.getTopLevelWorldView();
		if (topLevel == null)
		{
			return WorldPoint.fromLocal(client, local);
		}

		WorldEntity entity = topLevel.worldEntities().byIndex(local.getWorldView());
		if (entity == null)
		{
			return WorldPoint.fromLocal(client, local);
		}

		LocalPoint mainWorldLocal = entity.transformToMainWorld(local);
		return mainWorldLocal != null
			? WorldPoint.fromLocal(client, mainWorldLocal)
			: WorldPoint.fromLocal(client, local);
	}

	/**
	 * Convenience wrapper of {@link #toTopLevelWorldPoint(Client, LocalPoint)} for an Actor --
	 * see that method for why this is needed instead of Actor.getWorldLocation() directly.
	 */
	public static WorldPoint getTopLevelWorldLocation(Client client, Actor actor)
	{
		LocalPoint local = actor.getLocalLocation();
		return local != null ? toTopLevelWorldPoint(client, local) : actor.getWorldLocation();
	}

	// Recursion guard for findTopLevelWorldEntityContaining -- see the matching one in
	// ObjectProvider.walkWorldViewRecursively for why this is just insurance, not expected to
	// ever actually trigger for a well-formed WorldView tree.
	private static final int MAX_WORLD_VIEW_SEARCH_DEPTH = 6;

	/**
	 * The top-level WorldEntity (e.g. a Sailing boat) whose own WorldView is, or contains
	 * (possibly several levels deep -- see the boat's Helm needing two levels, confirmed live
	 * 2026-08-02), the given WorldView. Returns null if {@code view} is already top-level, or no
	 * containing entity is found.
	 * <p>
	 * Added to get the boat's own reference position instead of the player's for distance
	 * purposes -- confirmed live 2026-08-02 that a player standing at one end of an ~8-tile boat
	 * (its own WorldView is that small, see PlayerProvider's debugWorldViewSizeX/Y) can be
	 * several tiles from the opposite end, which matters for maxDistance-style range checks
	 * against something the *boat* needs to be near, not specifically wherever the player happens
	 * to be standing on deck.
	 */
	public static WorldEntity findTopLevelWorldEntityContaining(Client client, WorldView view)
	{
		if (view == null || view.isTopLevel())
		{
			return null;
		}

		WorldView topLevel = client.getTopLevelWorldView();
		if (topLevel == null)
		{
			return null;
		}

		for (WorldEntity entity : topLevel.worldEntities())
		{
			WorldView entityView = entity.getWorldView();
			if (entityView == null)
			{
				continue;
			}
			if (entityView.getId() == view.getId() || viewContainsId(entityView, view.getId(), 0))
			{
				return entity;
			}
		}

		return null;
	}

	private static boolean viewContainsId(WorldView view, int targetId, int depth)
	{
		if (depth > MAX_WORLD_VIEW_SEARCH_DEPTH)
		{
			return false;
		}

		for (WorldEntity child : view.worldEntities())
		{
			WorldView childView = child.getWorldView();
			if (childView == null)
			{
				continue;
			}
			if (childView.getId() == targetId || viewContainsId(childView, targetId, depth + 1))
			{
				return true;
			}
		}

		return false;
	}

	/**
	 * The boat's own position (its containing WorldEntity's own top-level location) if the given
	 * actor is aboard one, or the actor's own top-level position otherwise (e.g. on foot, or the
	 * WorldEntity's position genuinely isn't resolvable right now). See
	 * {@link #findTopLevelWorldEntityContaining} for why this differs from the actor's own
	 * position while aboard a boat.
	 */
	public static WorldPoint getBoatOrActorWorldLocation(Client client, Actor actor)
	{
		WorldView actorView = actor.getWorldView();
		WorldEntity boat = actorView != null ? findTopLevelWorldEntityContaining(client, actorView) : null;
		if (boat != null)
		{
			LocalPoint boatLocal = boat.getLocalLocation();
			if (boatLocal != null)
			{
				return WorldPoint.fromLocal(client, boatLocal);
			}
		}

		return getTopLevelWorldLocation(client, actor);
	}
}
