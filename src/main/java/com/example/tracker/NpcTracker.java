package com.example.tracker;

/**
 * Marker interface for a stateful tracker attached to one specific NPC instance by an
 * {@link NpcTrackerFactory}. Implement one or more of {@link AnimationListener},
 * {@link HitsplatListener}, {@link TrackerTickListener} to actually receive callbacks --
 * a bare NpcTracker with none of those receives nothing.
 */
public interface NpcTracker
{
	/**
	 * Called once when this tracker is detached (the NPC despawned, stopped matching its
	 * factory's isValidTrackingTarget, or the world was hopped). Optional cleanup hook.
	 */
	default void onDetach()
	{
	}
}
