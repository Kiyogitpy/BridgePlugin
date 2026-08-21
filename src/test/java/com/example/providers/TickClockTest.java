package com.example.providers;

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

		clock.bump();
		assertEquals(1, clock.get());

		clock.bump();
		clock.bump();
		assertEquals(3, clock.get());
	}
}
