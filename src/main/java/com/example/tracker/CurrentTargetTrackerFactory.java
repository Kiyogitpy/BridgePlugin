package com.example.tracker;

import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.api.Player;

public class CurrentTargetTrackerFactory implements NpcTrackerFactory
{
	private final Client client;

	public CurrentTargetTrackerFactory(Client client)
	{
		this.client = client;
	}

	@Override
	public boolean isValidTrackingTarget(NPC npc)
	{
		Player localPlayer = client.getLocalPlayer();
		return localPlayer != null && npc.equals(localPlayer.getInteracting());
	}

	@Override
	public NpcTracker newInstance(NPC npc)
	{
		return new CurrentTargetTracker();
	}
}
