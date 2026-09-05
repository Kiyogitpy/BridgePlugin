package com.example.util;

import com.google.common.collect.MapMaker;
import java.util.Collections;
import java.util.Set;

/**
 * "Have I seen this projectile before?" for {@code ProjectileMoved}, which RuneLite fires every
 * client tick for the projectile's whole flight. A PROJECTILE_SPAWNED event must fire once per
 * projectile, so the first sighting is what matters. Identity semantics on purpose (two attacks
 * with the same projectile id are two objects), weak so a finished projectile is collected
 * without bookkeeping. Not thread-safe: called from the client thread only.
 */
public final class ProjectileSeen
{
	// Guava MapMaker with weakKeys() provides both identity comparison (==, not equals()) and
	// weak references. JDK WeakHashMap keys on equals()/hashCode(), which would collapse two
	// equal-but-distinct projectiles; JDK has no WeakIdentityHashMap. Object keys keep the
	// class testable without a real Projectile.
	private final Set<Object> seen = Collections.newSetFromMap(new MapMaker().weakKeys().<Object, Boolean>makeMap());

	public boolean firstSighting(Object projectile)
	{
		if (projectile == null)
		{
			return false;
		}
		return seen.add(projectile);
	}

	public int size()
	{
		return seen.size();
	}
}
