package com.example.tracker;

/**
 * Receives a callback once per GameTick while attached -- for a tracker's own bookkeeping
 * (e.g. ticks since last seen attacking), not detection. Separate cadence from
 * AnimationListener's ClientTick-driven polling.
 */
public interface TrackerTickListener extends NpcTracker
{
	void onTrackerTick(int tick);
}
