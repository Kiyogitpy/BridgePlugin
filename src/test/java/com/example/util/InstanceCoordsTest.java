package com.example.util;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

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
}
