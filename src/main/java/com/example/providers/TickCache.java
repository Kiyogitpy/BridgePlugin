package com.example.providers;

import java.util.Objects;
import java.util.function.Supplier;

/**
 * Caches a computed value until the game tick it was computed on changes, an optional caller-
 * supplied key changes, or the caller explicitly asks for a fresh value. Replaces the identical
 * cachedTick/cached&lt;Thing&gt; field pairs six providers used to hand-roll independently
 * (Bank, NPC, Tile, GroundItem, GraphicsObject, Object).
 *
 * A {@code null} compute result is returned for that call but never cached: for every real
 * caller in this codebase a {@code null} only happens when the underlying compute (e.g. a
 * client-thread dispatch) failed or was interrupted, never as a legitimate "empty" answer (those
 * are always an empty List/Map), so caching it would pin a transient failure as the confident
 * answer for the rest of the tick instead of retrying on the next call.
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
			T result = compute.get();
			if (result != null)
			{
				cached = result;
				cachedTick = currentTick;
				cachedKey = key;
				hasValue = true;
			}
			else
			{
				// A null result is never a legitimate "empty" answer from any of this class's real
				// callers (they all return an empty List/Map, never null, for the empty case) -- it
				// only happens when the underlying compute (e.g. a client-thread dispatch) failed or
				// was interrupted. Don't cache it: the next call should retry immediately instead of
				// serving a full tick's worth of confidently-wrong empty answers. CONFIRMED (final
				// branch review): RuneLiteUtil.runOnClientThread returns null on a swallowed
				// exception/interrupt, and before this class existed, each provider's own
				// cachedX == null check had this same self-healing retry-on-null behavior.
				cached = null;
				hasValue = false;
			}
		}
		return cached;
	}
}
