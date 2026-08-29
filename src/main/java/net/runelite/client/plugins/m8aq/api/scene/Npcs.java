package net.runelite.client.plugins.m8aq.api.scene;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Actor;
import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.api.NPCComposition;
import net.runelite.api.WorldView;
import net.runelite.api.coords.WorldPoint;

/** Provides copied state for NPCs loaded in the top-level world view. */
public final class Npcs
{
	private Npcs()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable loaded-NPC snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		List<NpcEntry> entries = new ArrayList<>();
		WorldView worldView = client.getTopLevelWorldView();
		if (worldView != null && worldView.npcs() != null)
		{
			for (NPC npc : worldView.npcs())
			{
				if (npc != null && !npc.isDead())
				{
					NPCComposition composition = npc.getTransformedComposition();
					List<String> actions = SceneActions.copy(composition == null ? null : composition.getActions());
					if (actions.isEmpty())
					{
						continue;
					}
					Actor interacting = npc.getInteracting();
					entries.add(new NpcEntry(npc.getId(), npc.getIndex(), npc.getName(), npc.getCombatLevel(),
						composition.getSize(), actions, npc.getWorldLocation(), npc.getAnimation(),
						npc.getPoseAnimation(), npc.getOrientation(), npc.getHealthRatio(), npc.getHealthScale(),
						interacting == null ? null : interacting.getName()));
				}
			}
		}
		return new State(Collections.unmodifiableList(entries));
	}

	/** Immutable loaded-NPC entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class NpcEntry
	{
		/** @return transformed NPC ID */ private final int id;
		/** @return NPC index */ private final int index;
		/** @return transformed NPC name */ private final String name;
		/** @return combat level */ private final int combatLevel;
		/** @return NPC tile size */ private final int size;
		/** @return immutable actions currently offered for this NPC */ private final List<String> actions;
		/** @return world coordinate */ private final WorldPoint worldPoint;
		/** @return animation ID */ private final int animation;
		/** @return pose animation ID */ private final int poseAnimation;
		/** @return orientation */ private final int orientation;
		/** @return overhead health-bar ratio, or -1 */ private final int healthRatio;
		/** @return overhead health-bar scale, or -1 */ private final int healthScale;
		/** @return interacting actor name, or null */ private final String interactingName;
	}

	/** Immutable collection of loaded NPCs. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return immutable loaded NPC entries */ private final List<NpcEntry> npcs;
	}
}
