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
import net.runelite.api.Player;
import net.runelite.api.WorldView;
import net.runelite.api.coords.WorldPoint;

/** Provides copied state for loaded players other than the local player. */
public final class OtherPlayers
{
	private OtherPlayers()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable loaded-player snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Player local = client.getLocalPlayer();
		List<PlayerEntry> entries = new ArrayList<>();
		List<String> actions = SceneActions.copy(client.getPlayerOptions());
		WorldView worldView = client.getTopLevelWorldView();
		if (!actions.isEmpty() && worldView != null && worldView.players() != null)
		{
			for (Player player : worldView.players())
			{
				if (player != null && player != local && !player.isDead())
				{
					Actor interacting = player.getInteracting();
					entries.add(new PlayerEntry(player.getId(), player.getName(), player.getCombatLevel(), player.getTeam(), actions,
						player.getWorldLocation(), player.getAnimation(), player.getPoseAnimation(), player.getOrientation(),
						player.isFriend(), player.isFriendsChatMember(), player.isClanMember(),
						interacting == null ? null : interacting.getName()));
				}
			}
		}
		return new State(Collections.unmodifiableList(entries));
	}

	/** Immutable loaded-player entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class PlayerEntry
	{
		/** @return player index */ private final int id;
		/** @return player name */ private final String name;
		/** @return combat level */ private final int combatLevel;
		/** @return team/cape identifier */ private final int team;
		/** @return immutable actions currently offered for this player */ private final List<String> actions;
		/** @return world coordinate */ private final WorldPoint worldPoint;
		/** @return animation ID */ private final int animation;
		/** @return pose animation ID */ private final int poseAnimation;
		/** @return orientation */ private final int orientation;
		/** @return whether on the friend list */ private final boolean friend;
		/** @return whether in the friends chat */ private final boolean friendsChatMember;
		/** @return whether in the clan */ private final boolean clanMember;
		/** @return interacting actor name, or null */ private final String interactingName;
	}

	/** Immutable collection of loaded non-local players. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return immutable loaded player entries */ private final List<PlayerEntry> players;
	}
}
