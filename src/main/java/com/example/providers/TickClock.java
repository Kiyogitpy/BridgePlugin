package com.example.providers;

/**
 * A plain per-tick counter, readable from any thread without touching RuneLite's client thread.
 * bump() is called from ControllerPlugin's own GameTick subscriber, always on the client thread,
 * so this is a standard single-writer/volatile-read pattern -- safe for a bridge HTTP handler
 * thread to read with no locking. The counter doesn't need to match client.getTickCount()'s
 * actual value, only to increment exactly once per real game tick, which is all TickCache needs
 * to detect staleness.
 *
 * Deliberately NOT a self-registering EventBus subscriber (it was, until 2026-08-21): RuneLite
 * doesn't guarantee subscriber ordering, so a second GameTick subscriber here could run before or
 * after ControllerPlugin's own on any given tick. ControllerPlugin's onGameTick needs get() and
 * lastTickMillis() to reflect the *current* tick when it stamps the TICK UDP event, not whichever
 * one happened to fire last -- so it calls bump() itself, explicitly, before reading either, and
 * this class has exactly one writer.
 */
public class TickClock
{
	private volatile int tick;
	private volatile long lastTickMillis;

	/** Advances the clock by one tick. Call exactly once per GameTick, from ControllerPlugin's
	 *  own subscriber -- see the class doc comment for why this isn't self-subscribed. */
	public void bump()
	{
		tick++;
		lastTickMillis = System.currentTimeMillis();
	}

	public int get()
	{
		return tick;
	}

	/** Wall-clock time of the most recent GameTick, for clients that need tick PHASE and not
	 *  just the count -- everything in OSRS resolves on tick boundaries, so a caller that knows
	 *  only the number still cannot tell how far through the current tick it is. */
	public long lastTickMillis()
	{
		return lastTickMillis;
	}
}
