package com.example.util;

/**
 * Sub-tick player motion from the client's per-frame interpolated LocalPoint, fed once per
 * ClientTick (50Hz, independent of FPS). A moving actor's local position slides every client
 * tick (~4 units walking, ~8 running, out of 128 per tile); it stops sliding the moment the
 * interpolation reaches the destination tile -- BEFORE the next server tick updates the tile.
 * That is the arrival signal a routine wants, and it needs no "two stable HTTP reads".
 *
 * Pure logic so it is unit-testable; ControllerPlugin.onClientTick feeds it and emits
 * PLAYER_MOVING / PLAYER_STOPPED on the transitions, PlayerProvider reports isMoving().
 *
 * See docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md.
 */
public final class PlayerMotionTracker
{
	public enum Transition
	{
		NONE, MOVING, STOPPED
	}

	/** Consecutive unchanged frames before a stop is reported (~40ms). */
	static final int STOPPED_AFTER_FRAMES = 2;
	/** A jump this large in one frame is a teleport / scene reload, not a walk (2 tiles). */
	static final int TELEPORT_JUMP_UNITS = 256;

	private Integer lastX;
	private Integer lastY;
	private int lastPlane;
	private int stillFrames;
	private boolean moving;

	public Transition observe(Integer localX, Integer localY, int plane)
	{
		if (localX == null || localY == null || lastX == null || plane != lastPlane)
		{
			boolean wasMoving = moving;
			reset(localX, localY, plane);
			return wasMoving ? Transition.STOPPED : Transition.NONE;
		}
		int delta = Math.max(Math.abs(localX - lastX), Math.abs(localY - lastY));
		if (delta > TELEPORT_JUMP_UNITS)
		{
			boolean wasMoving = moving;
			reset(localX, localY, plane);
			return wasMoving ? Transition.STOPPED : Transition.NONE;
		}
		lastX = localX;
		lastY = localY;
		if (delta > 0)
		{
			stillFrames = 0;
			if (!moving)
			{
				moving = true;
				return Transition.MOVING;
			}
			return Transition.NONE;
		}
		if (moving && ++stillFrames >= STOPPED_AFTER_FRAMES)
		{
			moving = false;
			stillFrames = 0;
			return Transition.STOPPED;
		}
		return Transition.NONE;
	}

	public boolean isMoving()
	{
		return moving;
	}

	private void reset(Integer localX, Integer localY, int plane)
	{
		lastX = localX;
		lastY = localY;
		lastPlane = plane;
		stillFrames = 0;
		moving = false;
	}
}
