package com.example.providers;

import org.junit.Test;

import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

/**
 * The pure half of SlayerProvider, split out so the two rules the Python side depends on can
 * be pinned without a client: hasTask is "count > 0" (how RuneLite's own SlayerPlugin detects
 * completion), and a name the DB lookup could not resolve is null, never "" -- so "no task"
 * and "could not decode" stay distinguishable (spec §10).
 */
public class SlayerTaskStateTest
{
	@Test
	public void hasTaskFollowsTheKillCount()
	{
		Map<String, Object> on = new SlayerTaskState(27, 30, 4135, "Banshees", 0, null, 2, 19, 415, 0).toMap();
		assertTrue((Boolean) on.get("hasTask"));
		assertEquals(27, on.get("count"));
		assertEquals("Banshees", on.get("taskName"));
		assertEquals(19, on.get("streak"));
		assertEquals(415, on.get("points"));

		Map<String, Object> off = new SlayerTaskState(0, 30, 4135, null, 0, null, 2, 20, 490, 0).toMap();
		assertFalse((Boolean) off.get("hasTask"));
	}

	@Test
	public void anUnresolvedNameIsNullNotEmpty()
	{
		Map<String, Object> m = new SlayerTaskState(5, 5, 9999, null, 7, null, 2, 1, 0, 0).toMap();
		assertTrue(m.containsKey("taskName"));
		assertNull(m.get("taskName"));
		assertTrue(m.containsKey("areaName"));
		assertNull(m.get("areaName"));
	}
}
