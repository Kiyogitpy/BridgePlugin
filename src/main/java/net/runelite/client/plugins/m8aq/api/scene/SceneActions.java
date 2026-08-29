package net.runelite.client.plugins.m8aq.api.scene;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.stream.Collectors;

/** Copies non-empty player actions from client-owned arrays. */
final class SceneActions
{
	private SceneActions()
	{
	}

	static List<String> copy(String[] actions)
	{
		return actions == null ? Collections.emptyList() : Collections.unmodifiableList(Arrays.stream(actions)
			.filter(action -> action != null && !action.isBlank())
			.collect(Collectors.toList()));
	}
}
