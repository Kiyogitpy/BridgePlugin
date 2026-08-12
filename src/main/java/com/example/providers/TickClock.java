package com.example.providers;

import net.runelite.api.events.GameTick;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;

/**
 * A plain per-tick counter, readable from any thread without touching RuneLite's client thread.
 * onGameTick always runs on the client thread (RuneLite dispatches @Subscribe there), so this is
 * a standard single-writer/volatile-read pattern -- safe for a bridge HTTP handler thread to
 * read with no locking. The counter doesn't need to match client.getTickCount()'s actual value,
 * only to increment exactly once per real game tick, which is all TickCache needs to detect
 * staleness.
 */
public class TickClock
{
	private volatile int tick;

	public void register(EventBus eventBus)
	{
		eventBus.register(this);
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		tick++;
	}

	public int get()
	{
		return tick;
	}
}
