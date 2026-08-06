package com.example.tracker;

import net.runelite.api.NPC;

/**
 * Decides which NPCs get a tracker attached, and creates that tracker. Register instances via
 * {@link NpcTrackerRegistry#addFactory(NpcTrackerFactory)}. Multiple factories can be
 * registered independently; one NPC can end up with more than one tracker attached if more
 * than one factory matches it.
 */
public interface NpcTrackerFactory
{
	boolean isValidTrackingTarget(NPC npc);

	NpcTracker newInstance(NPC npc);
}
