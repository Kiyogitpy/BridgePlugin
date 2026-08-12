package com.example.providers;

import net.runelite.api.events.GameTick;
import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class TickClockTest
{
	@Test
	public void startsAtZero()
	{
		TickClock clock = new TickClock();
		assertEquals(0, clock.get());
	}

	@Test
	public void incrementsByOnePerGameTick()
	{
		TickClock clock = new TickClock();

		clock.onGameTick(new GameTick());
		assertEquals(1, clock.get());

		clock.onGameTick(new GameTick());
		clock.onGameTick(new GameTick());
		assertEquals(3, clock.get());
	}
}
