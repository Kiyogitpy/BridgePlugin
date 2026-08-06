package com.example.tracker;

import net.runelite.api.Hitsplat;

/** Receives a callback whenever a HitsplatApplied event's actor is this tracked NPC. */
public interface HitsplatListener extends NpcTracker
{
	void onHitsplat(Hitsplat hitsplat);
}
