package com.example.util;

import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Set;

/**
 * "Have I seen this projectile before?" for {@code ProjectileMoved}, which RuneLite fires every
 * client tick for the projectile's whole flight. A PROJECTILE_SPAWNED event must fire once per
 * projectile, so the first sighting is what matters. Identity semantics on purpose (two attacks
 * with the same projectile id are two objects). Not thread-safe: called from the client thread only.
 */
public final class ProjectileSeen
{
	// IdentityHashMap uses identity (==) rather than equals() for key comparison, so two
	// distinct projectile objects are always distinct even if they have the same id or other
	// properties. Object keys keep the class testable without a real Projectile.
	private final Set<Object> seen = Collections.newSetFromMap(new IdentityHashMap<>());

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
