package com.example.util;

import org.junit.Test;

import static com.example.util.PlayerMotionTracker.Transition.*;
import static org.junit.Assert.*;

public class PlayerMotionTrackerTest
{
	@Test
	public void firstSampleIsNotMotion()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		assertEquals(NONE, t.observe(1000, 1000, 0));
		assertFalse(t.isMoving());
	}

	@Test
	public void movingOnFirstDeltaThenStoppedAfterDebounce()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		assertEquals(MOVING, t.observe(1004, 1000, 0));   // walking: ~4 units per client tick
		assertTrue(t.isMoving());
		assertEquals(NONE, t.observe(1008, 1000, 0));
		assertEquals(NONE, t.observe(1008, 1000, 0));     // 1 still frame: not yet
		assertEquals(STOPPED, t.observe(1008, 1000, 0));  // 2 still frames: stopped
		assertFalse(t.isMoving());
		assertEquals(NONE, t.observe(1008, 1000, 0));     // stays stopped, no repeat
	}

	@Test
	public void oneStillFrameMidRunDoesNotStop()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(0, 0, 0);
		t.observe(8, 0, 0);
		assertEquals(NONE, t.observe(8, 0, 0));
		assertEquals(NONE, t.observe(16, 0, 0));
		assertTrue(t.isMoving());
	}

	@Test
	public void teleportJumpResetsWithoutMoving()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		assertEquals(NONE, t.observe(1000 + 3 * 128, 1000, 0));   // 3 tiles in one frame
		assertFalse(t.isMoving());
	}

	@Test
	public void teleportWhileMovingEmitsStopped()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		t.observe(1004, 1000, 0);
		assertEquals(STOPPED, t.observe(5000, 5000, 0));
		assertFalse(t.isMoving());
	}

	@Test
	public void nullOrPlaneChangeResets()
	{
		PlayerMotionTracker t = new PlayerMotionTracker();
		t.observe(1000, 1000, 0);
		t.observe(1004, 1000, 0);
		assertEquals(STOPPED, t.observe(null, null, 0));
		assertEquals(NONE, t.observe(1004, 1000, 0));
		assertEquals(NONE, t.observe(1004, 1000, 1));   // plane change: re-seed, no motion
		assertEquals(MOVING, t.observe(1008, 1000, 1));
	}
}
