package net.runelite.client.plugins.m8aq.api.player;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;

/** Provides a read-only snapshot of directly exposed local-player status effects. */
public final class StatusEffects
{
	private StatusEffects()
	{
	}

	/**
	 * Reads current status effects from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable status-effect snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (client.getLocalPlayer() == null)
		{
			return new State(ToxinStatus.NONE, 0, 0, false, false, false);
		}

		int poison = client.getVarpValue(VarPlayerID.POISON);
		int disease = client.getVarpValue(VarPlayerID.DISEASE);
		int teleblockCycles = client.getVarbitValue(VarbitID.TELEBLOCK_CYCLES);
		return new State(
			ToxinStatus.from(poison),
			nextToxinDamage(poison),
			immunityTicks(poison),
			disease > 0,
			client.getVarbitValue(VarbitID.STAMINA_ACTIVE) == 1,
			teleblockCycles > 0);
	}

	private static int nextToxinDamage(int poison)
	{
		if (poison >= 1_000_000)
		{
			return Math.min(20, (poison - 999_997) * 2);
		}
		return poison > 0 ? (poison + 4) / 5 : 0;
	}

	private static int immunityTicks(int poison)
	{
		if (poison >= 0)
		{
			return 0;
		}
		long magnitude = Math.abs((long) poison);
		long ticks = (magnitude - (poison < -38 ? 38 : 0)) * 30;
		return ticks > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int) ticks;
	}

	/** Poison, venom, or toxin-immunity state decoded from the poison varp. */
	public enum ToxinStatus
	{
		NONE,
		POISONED,
		VENOMED,
		POISON_IMMUNE,
		VENOM_IMMUNE;

		private static ToxinStatus from(int poison)
		{
			if (poison >= 1_000_000)
			{
				return VENOMED;
			}
			if (poison > 0)
			{
				return POISONED;
			}
			if (poison < -38)
			{
				return VENOM_IMMUNE;
			}
			return poison < 0 ? POISON_IMMUNE : NONE;
		}
	}

	/** Immutable directly exposed status-effect state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return toxin state decoded from the poison varp */
		private final ToxinStatus toxinStatus;
		/** @return next poison/venom damage, or zero when not poisoned */
		private final int nextToxinDamage;
		/** @return remaining poison/venom immunity in game ticks, or zero */
		private final int toxinImmunityTicks;
		/** @return whether disease severity is positive */
		private final boolean diseased;
		/** @return whether the stamina effect is marked active */
		private final boolean staminaActive;
		/** @return whether teleblock cycles remain */
		private final boolean teleblocked;
	}
}
