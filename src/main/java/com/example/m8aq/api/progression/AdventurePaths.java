package com.example.m8aq.api.progression;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;

/** Provides selected Adventure Path state and path completion flags. */
public final class AdventurePaths
{
	private AdventurePaths()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable Adventure Paths snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Map<String, Boolean> completion = new LinkedHashMap<>();
		completion.put("Combat", client.getVarbitValue(VarbitID.ADVENTUREPATH_COMBAT_PATH_PATH_COMPLETE) != 0);
		completion.put("Gatherer", client.getVarbitValue(VarbitID.ADVENTUREPATH_GATHERER_PATH_PATH_COMPLETE) != 0);
		completion.put("Warrior", client.getVarbitValue(VarbitID.ADVENTUREPATH_WARRIOR_PATH_PATH_COMPLETE) != 0);
		completion.put("Wizard", client.getVarbitValue(VarbitID.ADVENTUREPATH_WIZARD_PATH_PATH_COMPLETE) != 0);
		completion.put("Ranger", client.getVarbitValue(VarbitID.ADVENTUREPATH_RANGER_PATH_PATH_COMPLETE) != 0);
		return new State(pathName(client.getVarpValue(VarPlayerID.ADVENTUREPATH_SELECTED_PATH)),
			client.getVarpValue(VarPlayerID.ADVENTUREPATH_IN_FOCUS) != 0,
			Collections.unmodifiableMap(completion));
	}

	private static String pathName(int path)
	{
		switch (path)
		{
			case 0: return "Combat";
			case 1: return "Gatherer";
			case 2: return "Warrior";
			case 3: return "Wizard";
			case 4: return "Ranger";
			default: return null;
		}
	}

	/** Immutable Adventure Paths state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return selected path name, or null when no known path is selected */ private final String selectedPath;
		/** @return whether an Adventure Path is in focus */ private final boolean inFocus;
		/** @return path names mapped to completion flags */ private final Map<String, Boolean> completedPaths;
	}
}
