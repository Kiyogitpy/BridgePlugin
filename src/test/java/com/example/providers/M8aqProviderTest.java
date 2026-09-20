package com.example.providers;

import net.runelite.client.plugins.m8aq.api.export.GameOutput;
import net.runelite.client.plugins.m8aq.api.export.ReaderCatalog;
import org.junit.Test;

import java.util.Arrays;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

/**
 * Guards the two things that can silently break the vendored m8aq endpoints.
 *
 * <p>The first is discovery: {@link ReaderCatalog} finds readers with a Guava classpath scan, and
 * a scan that walks nothing produces zero endpoints and no error at all -- a bridge that is up
 * and serves none of this data. {@link #discoveryFindsTheVendoredReaders()} turns that into a
 * build failure.
 *
 * <p>The second is path stability: {@code /api/...} paths are derived from class names, so an
 * upstream rename moves an endpoint the Python side is calling. Nothing can prevent that, but
 * pinning a few known paths means a sync that moves them fails here rather than live.
 */
public class M8aqProviderTest
{
	@Test
	public void discoveryFindsTheVendoredReaders()
	{
		List<Class<?>> discovered = ReaderCatalog.getClasses();
		assertTrue("ReaderCatalog discovered only " + discovered.size() + " readers; a classpath scan"
				+ " that walks nothing looks exactly like a working bridge with no data on it",
			discovered.size() >= M8aqProvider.MINIMUM_EXPECTED_READERS);
	}

	@Test
	public void everyDiscoveredReaderGetsAPathExceptTheAggregate()
	{
		List<Class<?>> discovered = ReaderCatalog.getClasses();
		Map<Class<?>, String> paths = M8aqProvider.buildPaths(discovered);

		assertEquals(discovered.size() - 1, paths.size());
		assertFalse("GameOutput is served as /api/all, not as a per-reader endpoint",
			paths.containsKey(GameOutput.class));
	}

	@Test
	public void pathsAreUnique()
	{
		Collection<String> paths = M8aqProvider.buildPaths(ReaderCatalog.getClasses()).values();
		Set<String> unique = new HashSet<>(paths);
		assertEquals("a duplicate path silently shadows one reader", paths.size(), unique.size());
	}

	@Test
	public void knownPathsAreStable()
	{
		Collection<String> paths = M8aqProvider.buildPaths(ReaderCatalog.getClasses()).values();
		for (String expected : Arrays.asList(
			"/api/storage/rune-pouch",
			"/api/storage/plank-sack",
			"/api/player/movement",
			"/api/player/status-effects",
			"/api/ui/dialogue",
			"/api/runtime/world",
			"/api/trade/grand-exchange",
			"/api/minigames/guardians-of-the-rift"))
		{
			assertTrue("expected endpoint " + expected + " is missing -- an upstream rename moved it",
				paths.contains(expected));
		}
	}

	@Test
	public void aReaderThatDiesWithAnErrorReportsItInsteadOfAnEmptyObject()
	{
		// FOUND LIVE 2026-09-20: GET /api/progression/quests answered {} in 13 ms. The client log
		// had "NoClassDefFoundError: ...Quests$QuestEntry" -- an ERROR, not an exception, so nothing
		// caught it; runOnClientThread released its latch in a finally, the result stayed null, and
		// the server wrote {}. Python reads {} as "this reader has nothing", which is a lie.
		Object result = M8aqProvider.guarded("Quests", () -> {
			throw new NoClassDefFoundError("net/runelite/client/plugins/m8aq/api/progression/Quests$QuestEntry");
		});
		assertTrue(result instanceof java.util.Map);
		java.util.Map<?, ?> error = (java.util.Map<?, ?>) result;
		assertEquals("Quests", error.get("reader"));
		assertTrue(String.valueOf(error.get("error")).contains("NoClassDefFoundError"));

		Object assertion = M8aqProvider.guarded("X", () -> {
			throw new AssertionError("must be called on client thread");
		});
		assertTrue(String.valueOf(((java.util.Map<?, ?>) assertion).get("error")).contains("AssertionError"));

		assertEquals("fine", M8aqProvider.guarded("Y", () -> "fine"));
	}

	@Test
	public void kebabSplitsOnWordBoundariesOnly()
	{
		assertEquals("rune-pouch", M8aqProvider.kebab("RunePouch"));
		assertEquals("guardians-of-the-rift", M8aqProvider.kebab("GuardiansOfTheRift"));
		assertEquals("world", M8aqProvider.kebab("World"));
		// Consecutive capitals are one word, so an acronym does not explode into letters.
		assertEquals("npcs", M8aqProvider.kebab("Npcs"));
	}
}
