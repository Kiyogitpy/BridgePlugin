package com.example.providers;

import com.example.util.InstanceCoords;
import org.junit.Test;

import java.util.List;

import static org.junit.Assert.assertEquals;

public class CollisionGridTest
{
	@Test
	public void encodesRowMajorByYWithSceneIndex()
	{
		int[][] flags = new int[InstanceCoords.SCENE_SIZE][InstanceCoords.SCENE_SIZE];
		flags[5][7] = 42;      // sceneX 5, sceneY 7
		flags[103][0] = 9;
		List<Integer> out = CollisionGrid.encode(flags);
		assertEquals(InstanceCoords.SCENE_SIZE * InstanceCoords.SCENE_SIZE, out.size());
		assertEquals(42, (int) out.get(InstanceCoords.sceneIndex(5, 7)));
		assertEquals(9, (int) out.get(InstanceCoords.sceneIndex(103, 0)));
		assertEquals(0, (int) out.get(InstanceCoords.sceneIndex(0, 0)));
	}

	@Test
	public void shortOrNullRowsReadAsZero()
	{
		int[][] flags = new int[3][];
		flags[0] = new int[]{1, 2};
		flags[1] = null;
		List<Integer> out = CollisionGrid.encode(flags);
		assertEquals(InstanceCoords.SCENE_SIZE * InstanceCoords.SCENE_SIZE, out.size());
		assertEquals(2, (int) out.get(InstanceCoords.sceneIndex(0, 1)));
		assertEquals(0, (int) out.get(InstanceCoords.sceneIndex(1, 0)));
		assertEquals(0, (int) out.get(InstanceCoords.sceneIndex(50, 50)));
	}
}
