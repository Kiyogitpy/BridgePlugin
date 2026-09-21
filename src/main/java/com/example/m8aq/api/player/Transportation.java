package com.example.m8aq.api.player;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.VarbitID;

/** Provides a read-only snapshot of directly exposed player transportation-network state. */
public final class Transportation
{
	private Transportation()
	{
	}

	/**
	 * Reads directly maintained transportation state from the client.
	 * This does not calculate route reachability or inspect transport menus.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable transportation snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (client.getLocalPlayer() == null)
		{
			return new State(false, null, null);
		}

		int fairyRingPermission = client.getVarbitValue(VarbitID.FAIRYRING_PERMISSION);
		return new State(fairyRingPermission > 0,
			client.getVarbitValue(VarbitID.FAIRYRING_LASTLOC_SET) == 0 ? null
				: fairyRingCode(client.getVarbitValue(VarbitID.FAIRYRING_LASTLOC)),
			spiritTreeDestination(client.getVarbitValue(VarbitID.SPIRIT_TREE_PREVIOUS)));
	}

	private static String fairyRingCode(int value)
	{
		if (value < 0 || value >= 64)
		{
			return null;
		}
		return "ADCB".substring(value / 16, value / 16 + 1)
			+ "ILKJ".substring(value / 4 % 4, value / 4 % 4 + 1)
			+ "PSRQ".substring(value % 4, value % 4 + 1);
	}

	private static String spiritTreeDestination(int value)
	{
		String[] destinations = {null, "Tree Gnome Village", "Gnome Stronghold", "Battlefield of Khazard",
			"Grand Exchange", "Feldip Hills", "Prifddinas", "Port Sarim", "Etceteria", "Brimhaven",
			"Hosidius", "Farming Guild", "Your house", "Poison Waste", "Laguna Aurorae"};
		return value >= 0 && value < destinations.length ? destinations[value] : null;
	}

	/** Immutable directly exposed transportation state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the fairy-ring permission stage allows use */
		private final boolean fairyRingsUnlocked;
		/** @return previous three-letter fairy-ring code, or null when unset */
		private final String lastFairyRingCode;
		/** @return previous spirit-tree destination name, or null when unset or unknown */
		private final String lastSpiritTreeDestination;
	}
}
