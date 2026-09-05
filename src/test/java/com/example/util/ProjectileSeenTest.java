package com.example.util;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ProjectileSeenTest
{
	@Test
	public void firstSightingOnlyOnce()
	{
		ProjectileSeen seen = new ProjectileSeen();
		Object p = new Object();
		assertTrue(seen.firstSighting(p));
		assertFalse(seen.firstSighting(p));
		assertFalse(seen.firstSighting(p));
	}

	@Test
	public void distinctObjectsAreDistinctSightingsEvenWhenEqual()
	{
		// Identity, never equals(): two projectiles with the same id/cycle are two attacks.
		ProjectileSeen seen = new ProjectileSeen();
		Object a = new String("same");
		Object b = new String("same");
		assertTrue(seen.firstSighting(a));
		assertTrue(seen.firstSighting(b));
	}

	@Test
	public void nullIsNeverASighting()
	{
		assertFalse(new ProjectileSeen().firstSighting(null));
	}
}
