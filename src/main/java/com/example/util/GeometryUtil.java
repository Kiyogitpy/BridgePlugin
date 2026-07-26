package com.example.util;

import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.coords.WorldPoint;

public final class GeometryUtil
{
	private GeometryUtil()
	{
	}

	public static int distanceToPlayer(Client client, WorldPoint wp)
	{
		Player p = client.getLocalPlayer();
		if (p == null)
		{
			return -1;
		}
		return p.getWorldLocation().distanceTo(wp);
	}
}
