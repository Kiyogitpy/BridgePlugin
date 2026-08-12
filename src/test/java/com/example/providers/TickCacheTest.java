package com.example.providers;

import org.junit.Test;

import java.util.function.Supplier;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

public class TickCacheTest
{
	private static Supplier<String> countingSupplier(int[] callCount, String value)
	{
		return () -> {
			callCount[0]++;
			return value;
		};
	}

	@Test
	public void cacheHitReturnsWithoutRecomputing()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		String first = cache.get(1, false, null, countingSupplier(calls, "a"));
		String second = cache.get(1, false, null, countingSupplier(calls, "b"));

		assertEquals("a", first);
		assertEquals("a", second);
		assertEquals(1, calls[0]);
	}

	@Test
	public void tickChangeRecomputes()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		cache.get(1, false, null, countingSupplier(calls, "a"));
		String second = cache.get(2, false, null, countingSupplier(calls, "b"));

		assertEquals("b", second);
		assertEquals(2, calls[0]);
	}

	@Test
	public void keyChangeRecomputes()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		cache.get(1, false, "radius=5", countingSupplier(calls, "a"));
		String second = cache.get(1, false, "radius=10", countingSupplier(calls, "b"));

		assertEquals("b", second);
		assertEquals(2, calls[0]);
	}

	@Test
	public void forceFreshAlwaysRecomputes()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		cache.get(1, false, null, countingSupplier(calls, "a"));
		String second = cache.get(1, true, null, countingSupplier(calls, "b"));

		assertEquals("b", second);
		assertEquals(2, calls[0]);
	}

	@Test
	public void nullResultIsNotCachedSoItRetriesNextCall()
	{
		TickCache<String> cache = new TickCache<>();
		int[] calls = {0};

		String first = cache.get(1, false, null, countingSupplier(calls, null));
		String second = cache.get(1, false, null, countingSupplier(calls, "a"));

		assertNull(first);
		assertEquals("a", second);
		assertEquals(2, calls[0]);
	}
}
