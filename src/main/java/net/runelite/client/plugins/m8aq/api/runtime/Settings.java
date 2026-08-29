package net.runelite.client.plugins.m8aq.api.runtime;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Preferences;
import net.runelite.api.gameval.VarPlayerID;

/** Provides a read-only snapshot of safe game-owned client settings. */
public final class Settings
{
	private Settings()
	{
	}

	/**
	 * Reads current display and audio settings without login identity.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable settings snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Preferences preferences = client.getPreferences();
		return new State(
			client.isResized(),
			client.getVarpValue(VarPlayerID.OPTION_MASTER_VOLUME),
			percentage(client.getMusicVolume(), 255),
			preferences == null ? -1 : percentage(preferences.getSoundEffectVolume(), 127),
			preferences == null ? -1 : percentage(preferences.getAreaSoundEffectVolume(), 127));
	}

	private static double percentage(int value, int maximum)
	{
		return Math.round(value * 1000.0 / maximum) / 10.0;
	}

	/** Immutable safe client-settings state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the game uses resizable rather than fixed display mode */
		private final boolean resized;
		/** @return game master volume from 0 through 100 */
		private final int masterVolumePercent;
		/** @return music volume percentage */
		private final double musicVolumePercent;
		/** @return sound-effect volume percentage, or -1 when unavailable */
		private final double soundEffectVolumePercent;
		/** @return area-sound volume percentage, or -1 when unavailable */
		private final double areaSoundEffectVolumePercent;
	}
}
