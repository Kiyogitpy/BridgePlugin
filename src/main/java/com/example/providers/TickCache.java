package com.example.providers;

import java.util.Objects;
import java.util.function.Supplier;

/**
 * Caches a computed value until the game tick it was computed on changes, an optional caller-
 * supplied key changes, or the caller explicitly asks for a fresh value. Replaces the identical
 * cachedTick/cached&lt;Thing&gt; field pairs six providers used to hand-roll independently
 * (Bank, NPC, Tile, GroundItem, GraphicsObject, Object).
 */
public class TickCache<T>
{
	private int cachedTick = -1;
	private Object cachedKey;
	private T cached;
	private boolean hasValue;

	/**
	 * @param currentTick the caller's current tick number (see TickClock)
	 * @param forceFresh  when true, always recomputes regardless of tick/key
	 * @param key         extra cache-key component beyond the tick (e.g. ObjectProvider's
	 *                    radius); pass null for providers with no extra key
	 * @param compute     produces a fresh value; only invoked on an actual cache miss
	 */
	// synchronized -- callers now run on arbitrary HTTP worker threads (see BridgeHttpServer's
	// thread pool), not RuneLite's client thread, so concurrent requests to the same cache
	// instance need this to avoid racing on cachedTick/cachedKey/cached/hasValue.
	public synchronized T get(int currentTick, boolean forceFresh, Object key, Supplier<T> compute)
	{
		if (forceFresh || !hasValue || currentTick != cachedTick || !Objects.equals(key, cachedKey))
		{
			cached = compute.get();
			cachedTick = currentTick;
			cachedKey = key;
			hasValue = true;
		}
		return cached;
	}
}
