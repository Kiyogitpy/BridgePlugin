package com.example.providers;

import com.example.util.InstanceCoords;

import java.util.ArrayList;
import java.util.List;

/**
 * A whole plane's collision flags as one flat list, row-major by y (index =
 * {@link InstanceCoords#sceneIndex}). ~10816 small ints, about 60 KB on the wire, so a routine
 * loads a room-scale map in ONE request instead of 25-tile squares. Missing rows or cells read
 * as 0 (no flags), the same "cannot tell" the per-tile endpoint would skip.
 */
final class CollisionGrid
{
	private CollisionGrid()
	{
	}

	static List<Integer> encode(int[][] flags)
	{
		int n = InstanceCoords.SCENE_SIZE;
		List<Integer> out = new ArrayList<>(n * n);
		for (int sy = 0; sy < n; sy++)
		{
			for (int sx = 0; sx < n; sx++)
			{
				int v = 0;
				if (flags != null && sx < flags.length && flags[sx] != null && sy < flags[sx].length)
				{
					v = flags[sx][sy];
				}
				out.add(v);
			}
		}
		return out;
	}
}
