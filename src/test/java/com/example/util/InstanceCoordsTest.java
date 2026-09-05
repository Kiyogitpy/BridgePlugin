package com.example.util;

import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

public class InstanceCoordsTest
{
	@Test
	public void toSceneSubtractsTheBase()
	{
		assertEquals(0, InstanceCoords.toScene(13056, 13056));
		assertEquals(103, InstanceCoords.toScene(13056, 13159));
		assertEquals(37, InstanceCoords.toScene(3200, 3237));
	}

	@Test
	public void toSceneRefusesOutsideTheScene()
	{
		assertEquals(-1, InstanceCoords.toScene(13056, 13055));
		assertEquals(-1, InstanceCoords.toScene(13056, 13160));
	}

	@Test
	public void sceneIndexIsRowMajorByY()
	{
		assertEquals(0, InstanceCoords.sceneIndex(0, 0));
		assertEquals(5, InstanceCoords.sceneIndex(5, 0));
		assertEquals(InstanceCoords.SCENE_SIZE, InstanceCoords.sceneIndex(0, 1));
		assertEquals(InstanceCoords.SCENE_SIZE * InstanceCoords.SCENE_SIZE - 1,
			InstanceCoords.sceneIndex(103, 103));
	}

	@Test
	public void wantsInstanceReadsTheFlag()
	{
		java.util.Map<String, String> q = new java.util.HashMap<>();
		assertEquals(false, InstanceCoords.wantsInstance(q));
		q.put("inst", "true");
		assertEquals(true, InstanceCoords.wantsInstance(q));
		q.put("inst", "1");
		assertEquals(true, InstanceCoords.wantsInstance(q));
		q.put("inst", "no");
		assertEquals(false, InstanceCoords.wantsInstance(q));
	}

	// M (2026-09-05, final-fix-brief.md): fromInstance's own null branches, mocked rather than
	// exercised through a real client -- this class has no live-client harness like the
	// providers do.
	@Test
	public void fromInstanceIsNullWithoutAWorldView()
	{
		assertNull(InstanceCoords.fromInstance(null, 13056, 5632));
	}

	@Test
	public void fromInstanceIsNullOffSceneOnX()
	{
		WorldView wv = mock(WorldView.class);
		when(wv.getBaseX()).thenReturn(13056);
		when(wv.getBaseY()).thenReturn(5632);
		// x resolves off-scene (toScene returns -1); y would be in-scene on its own, so this
		// pins that EITHER coordinate failing is enough to refuse the whole point, not just both.
		assertNull(InstanceCoords.fromInstance(wv, 13055, 5632));
	}

	@Test
	public void fromInstanceIsNullOffSceneOnY()
	{
		WorldView wv = mock(WorldView.class);
		when(wv.getBaseX()).thenReturn(13056);
		when(wv.getBaseY()).thenReturn(5632);
		assertNull(InstanceCoords.fromInstance(wv, 13056, 5631));
	}

	// In-scene case: LocalPoint.fromScene(sx, sy, WorldView) may call other WorldView methods
	// (e.g. getPlane()) that a bare Mockito mock answers with harmless defaults (0/false/null),
	// so this is expected to succeed against the mock -- if LocalPoint.fromScene instead throws
	// against it (e.g. NPEs on something a mock can't stand in for), this test is the one to
	// delete, and the three null-branch tests above are the ones this task is actually asking
	// for; see the final-fix-report for which happened.
	@Test
	public void fromInstanceResolvesAnInSceneTile()
	{
		WorldView wv = mock(WorldView.class);
		when(wv.getBaseX()).thenReturn(13056);
		when(wv.getBaseY()).thenReturn(5632);
		LocalPoint lp = InstanceCoords.fromInstance(wv, 13056 + 10, 5632 + 20);
		assertNotNull(lp);
	}
}
