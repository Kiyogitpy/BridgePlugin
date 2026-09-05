package com.example.util;

import net.runelite.api.Client;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;

/**
 * Instance-space coordinates: the client's own scene base plus the scene offset, exactly what
 * {@link WorldPoint#fromLocal} returns and what {@link LocalPoint#fromWorld} can invert. Unique
 * per scene tile, stable while the scene is loaded, and equal to the ordinary world coordinate
 * outside an instance.
 * <p>
 * Why a second coordinate space (2026-09-05, Gauntlet sub-project 1): every provider reports
 * {@code WorldPoint.fromLocalInstance} -- the instance TEMPLATE coordinate, needed so routines'
 * hardcoded tiles match inside Pest Control etc. -- and that direction cannot be inverted, which
 * is CLAUDE.md's "forward world-coordinate conversion is broken in instances" gotcha. Template
 * coordinates are also not unique (an instance may place the same template chunk twice). So
 * the endpoints that take a tile accept instance space too, resolved here by ARITHMETIC against
 * the world view's base -- never via fromWorld -- and every position-bearing provider reports
 * {@code instX}/{@code instY} beside {@code x}/{@code y}.
 */
public final class InstanceCoords
{
	public static final int SCENE_SIZE = 104;

	private InstanceCoords()
	{
	}

	/** Scene offset of an instance-space coordinate, or -1 when it falls outside the scene. */
	public static int toScene(int base, int coord)
	{
		int scene = coord - base;
		return scene >= 0 && scene < SCENE_SIZE ? scene : -1;
	}

	/** Row-major-by-y index into a {@code SCENE_SIZE * SCENE_SIZE} flat grid. */
	public static int sceneIndex(int sceneX, int sceneY)
	{
		return sceneY * SCENE_SIZE + sceneX;
	}

	/** The uncorrected (base + scene) world point of a local point. */
	public static WorldPoint instancePoint(Client client, LocalPoint local, int plane)
	{
		return WorldPoint.fromLocal(client, local.getX(), local.getY(), plane);
	}

	/** Local point for an instance-space tile in this world view, or null when off-scene. */
	public static LocalPoint fromInstance(WorldView wv, int instX, int instY)
	{
		if (wv == null)
		{
			return null;
		}
		int sx = toScene(wv.getBaseX(), instX);
		int sy = toScene(wv.getBaseY(), instY);
		if (sx < 0 || sy < 0)
		{
			return null;
		}
		return LocalPoint.fromScene(sx, sy, wv);
	}

	/** True for inst=true / inst=1 (case-insensitive); false when absent or anything else. */
	public static boolean wantsInstance(java.util.Map<String, String> query)
	{
		String v = query.get("inst");
		return v != null && ("true".equalsIgnoreCase(v) || "1".equals(v));
	}
}
