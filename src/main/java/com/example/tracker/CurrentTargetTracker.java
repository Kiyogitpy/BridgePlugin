package com.example.tracker;

import net.runelite.api.Hitsplat;

/**
 * The proof tracker for this framework -- attaches to whatever NPC is currently fighting the
 * local player (see CurrentTargetTrackerFactory) and does nothing itself. NpcTrackerRegistry is
 * what pushes NPC_TRACKED_ANIMATION_CHANGED/NPC_TRACKED_HITSPLAT over UDP for any NPC with an
 * attached listener of the matching type -- this class exists only to be that listener, so its
 * callback bodies are intentionally empty. Verify the pipeline via poll_events, not by reading
 * Java console output.
 */
public class CurrentTargetTracker implements AnimationListener, HitsplatListener, TrackerTickListener
{
	@Override
	public void onAnimationChanged(int animationId)
	{
	}

	@Override
	public void onHitsplat(Hitsplat hitsplat)
	{
	}

	@Override
	public void onTrackerTick(int tick)
	{
	}
}
