package net.runelite.client.plugins.m8aq.api.minigames;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;

/** Provides a read-only snapshot of the client-observable Soul Wars state. */
public final class SoulWars
{
	private static final int MAX_ACTIVITY = 800;
	private static final int MAX_ALIGNMENT = 120;

	private SoulWars()
	{
	}

	/**
	 * Reads the current Soul Wars state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");

		boolean gameHudVisible = isVisible(client.getWidget(InterfaceID.SoulWarsGame.UNIVERSE));
		boolean lobbyHudVisible = isVisible(client.getWidget(InterfaceID.SoulWarsLobby.UNIVERSE));
		boolean resultVisible = isVisible(client.getWidget(InterfaceID.SoulWarsGameEnd.UNIVERSE));
		int activity = client.getVarbitValue(VarbitID.SOUL_WARS_ACTIVITY_VALUE);
		String timeText = gameHudVisible
			? text(client.getWidget(InterfaceID.SoulWarsGame.TIME_LEFT)) : "";

		return new State(
			resultVisible ? Phase.RESULTS : gameHudVisible ? Phase.IN_GAME
				: lobbyHudVisible ? Phase.LOBBY : Phase.INACTIVE,
			Team.fromValue(client.getVarbitValue(VarbitID.SOUL_WARS_TEAM)),
			gameHudVisible,
			timeText,
			gameHudVisible ? readDurationSeconds(timeText) : -1,
			activity,
			percent(activity, MAX_ACTIVITY),
			readTeam(client, Team.BLUE, gameHudVisible),
			readTeam(client, Team.RED, gameHudVisible),
			readObjective(client, Objective.WEST_GRAVEYARD),
			readObjective(client, Objective.SOUL_OBELISK),
			readObjective(client, Objective.EAST_GRAVEYARD),
			readLobby(client, lobbyHudVisible),
			readResult(client, resultVisible),
			new Statistics(
				client.getVarpValue(VarPlayerID.SOUL_WARS_ZEAL),
				client.getVarpValue(VarPlayerID.SOUL_WARS_ZEAL_TOKENS),
				client.getVarpValue(VarPlayerID.SOUL_WARS_TOTAL_KILLS),
				client.getVarpValue(VarPlayerID.SOUL_WARS_TOTAL_DEATHS),
				client.getVarpValue(VarPlayerID.SOUL_WARS_TOTAL_GAMES),
				client.getVarpValue(VarPlayerID.SOUL_WARS_TOTAL_WINS)),
			client.getVarbitValue(VarbitID.SOUL_WARS_BANNED) == 1,
			client.getVarbitValue(VarbitID.SOUL_WARS_RECENT_DEATH) == 1,
			client.getVarbitValue(VarbitID.SOUL_WARS_FINISHED_TUTORIAL) == 1,
			client.getVarbitValue(VarbitID.SOUL_WARS_XP_DISABLE) == 1,
			client.getVarbitValue(VarbitID.COLLECTION_MINIGAMES_SOUL_WARS_COMPLETED) == 1);
	}

	private static TeamState readTeam(Client client, Team team, boolean visible)
	{
		int killsComponent = team == Team.BLUE
			? InterfaceID.SoulWarsGame.BLUE_KILLS : InterfaceID.SoulWarsGame.RED_KILLS;
		int healthComponent = team == Team.BLUE
			? InterfaceID.SoulWarsGame.BLUE_HEALTH : InterfaceID.SoulWarsGame.RED_HEALTH;
		int strengthComponent = team == Team.BLUE
			? InterfaceID.SoulWarsGame.BLUE_STRENGTH : InterfaceID.SoulWarsGame.RED_STRENGTH;
		String killsText = visible ? text(client.getWidget(killsComponent)) : "";
		return new TeamState(
			team,
			visible ? readInt(killsText, 0) : -1,
			visible ? readInt(killsText, 1) : -1,
			visible ? readFirstInt(client.getWidget(healthComponent)) : -1,
			visible ? readFirstInt(client.getWidget(strengthComponent)) : -1);
	}

	private static ObjectiveState readObjective(Client client, Objective objective)
	{
		int alignment = client.getVarbitValue(objective.alignmentVarbit);
		return new ObjectiveState(
			objective,
			Team.fromValue(client.getVarbitValue(objective.holderVarbit)),
			Team.fromValue(client.getVarbitValue(objective.contestedVarbit)),
			alignment,
			percent(MAX_ALIGNMENT - alignment, MAX_ALIGNMENT),
			percent(alignment, MAX_ALIGNMENT));
	}

	private static LobbyState readLobby(Client client, boolean visible)
	{
		String playersText = visible
			? text(client.getWidget(InterfaceID.SoulWarsLobby.PLAYERS_TEXT)) : "";
		String startText = visible
			? text(client.getWidget(InterfaceID.SoulWarsLobby.TIME_TEXT)) : "";
		return new LobbyState(
			visible,
			playersText,
			visible ? readInt(playersText, 0) : -1,
			visible ? readInt(playersText, 1) : -1,
			startText,
			visible ? readDurationSeconds(startText) : -1);
	}

	private static ResultState readResult(Client client, boolean visible)
	{
		String title = visible
			? text(client.getWidget(InterfaceID.SoulWarsGameEnd.TITLE_TEXT)) : "";
		String blueScoreText = visible
			? text(client.getWidget(InterfaceID.SoulWarsGameEnd.BLUE_SCORE_TEXT)) : "";
		String redScoreText = visible
			? text(client.getWidget(InterfaceID.SoulWarsGameEnd.RED_SCORE_TEXT)) : "";
		return new ResultState(
			visible,
			title,
			Outcome.fromTitle(title),
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.PLAYERS_KILLED_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.AREAS_CAPTURED_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.AVATAR_DAMAGE_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.OBELISK_SACRIFICES_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.BONES_BURIED_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.ZEAL_EARNT_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.LIFETIME_ZEAL_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.ZEAL_TOKENS_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.BLUE_PLAYERS_TEXT)) : -1,
			visible ? readFirstInt(client.getWidget(InterfaceID.SoulWarsGameEnd.RED_PLAYERS_TEXT)) : -1,
			visible ? readInt(blueScoreText, 0) : -1,
			visible ? readInt(redScoreText, 0) : -1,
			visible ? readInt(blueScoreText, 1) : -1);
	}

	private static boolean isVisible(Widget widget)
	{
		return widget != null && !widget.isHidden();
	}

	private static String text(Widget widget)
	{
		return widget == null || widget.getText() == null ? "" : widget.getText();
	}

	private static int readFirstInt(Widget widget)
	{
		return readInt(text(widget), 0);
	}

	private static int readInt(String text, int requested)
	{
		int value = 0;
		int found = -1;
		boolean reading = false;
		for (int index = 0; index < text.length(); index++)
		{
			char character = text.charAt(index);
			if (Character.isDigit(character))
			{
				if (!reading)
				{
					found++;
					value = 0;
					reading = true;
				}
				value = value * 10 + character - '0';
			}
			else if (reading && character != ',')
			{
				if (found == requested)
				{
					return value;
				}
				reading = false;
			}
		}
		return reading && found == requested ? value : -1;
	}

	private static int readDurationSeconds(String text)
	{
		String normalized = text.trim().toLowerCase();
		int colon = normalized.indexOf(':');
		if (colon >= 0)
		{
			int minutes = readInt(normalized.substring(0, colon), 0);
			int seconds = readInt(normalized.substring(colon + 1), 0);
			return minutes < 0 || seconds < 0 ? -1 : minutes * 60 + seconds;
		}
		if (normalized.contains("immediate"))
		{
			return 0;
		}
		int value = readInt(normalized, 0);
		if (value < 0)
		{
			return -1;
		}
		return normalized.contains("minute") ? value * 60
			: normalized.contains("second") ? value : -1;
	}

	private static double percent(int value, int maximum)
	{
		return Math.max(0, Math.min(maximum, value)) * 100.0 / maximum;
	}

	/** Current visible Soul Wars phase. */
	public enum Phase
	{
		INACTIVE,
		LOBBY,
		IN_GAME,
		RESULTS
	}

	/** Soul Wars team value used by team, holder, and contested varbits. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Team
	{
		NONE(0),
		BLUE(1),
		RED(2),
		UNKNOWN(-1);

		/** @return raw server value */ private final int value;

		private static Team fromValue(int value)
		{
			for (Team team : values())
			{
				if (team.value == value)
				{
					return team;
				}
			}
			return UNKNOWN;
		}
	}

	/** Capture area represented by the official game HUD. */
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public enum Objective
	{
		WEST_GRAVEYARD(
			VarbitID.SOUL_WARS_WEST_GY_HOLDER,
			VarbitID.SOUL_WARS_WEST_GY_CONTESTED,
			VarbitID.SOUL_WARS_WEST_GY_ALIGNMENT),
		SOUL_OBELISK(
			VarbitID.SOUL_WARS_OBELISK_HOLDER,
			VarbitID.SOUL_WARS_OBELISK_CONTESTED,
			VarbitID.SOUL_WARS_OBELISK_ALIGNMENT),
		EAST_GRAVEYARD(
			VarbitID.SOUL_WARS_EAST_GY_HOLDER,
			VarbitID.SOUL_WARS_EAST_GY_CONTESTED,
			VarbitID.SOUL_WARS_EAST_GY_ALIGNMENT);

		private final int holderVarbit;
		private final int contestedVarbit;
		private final int alignmentVarbit;
	}

	/** Outcome shown by the game-summary interface. */
	public enum Outcome
	{
		BLUE_WIN,
		RED_WIN,
		DRAW,
		UNKNOWN;

		private static Outcome fromTitle(String title)
		{
			String normalized = title.toLowerCase();
			if (normalized.contains("blue team"))
			{
				return BLUE_WIN;
			}
			if (normalized.contains("red team"))
			{
				return RED_WIN;
			}
			return normalized.contains("draw") ? DRAW : UNKNOWN;
		}
	}

	/** Immutable score, health, and strength for one team. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class TeamState
	{
		/** @return team */ private final Team team;
		/** @return Avatar kills, or -1 when unavailable */ private final int avatarKills;
		/** @return Avatar kill target, or -1 when unavailable */ private final int avatarKillTarget;
		/** @return Avatar health percentage, or -1 when unavailable */ private final int avatarHealthPercent;
		/** @return Avatar damage-reduction percentage, or -1 when unavailable */
		private final int avatarStrengthPercent;
	}

	/** Immutable state for one capture objective. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class ObjectiveState
	{
		/** @return objective */ private final Objective objective;
		/** @return team currently holding the objective */ private final Team holder;
		/** @return team currently contesting the objective */ private final Team contestingTeam;
		/** @return raw alignment on the 0 blue to 120 red scale */ private final int alignment;
		/** @return blue share of the official alignment bar */ private final double blueAlignmentPercent;
		/** @return red share of the official alignment bar */ private final double redAlignmentPercent;

		/** @return whether either team is contesting this objective */
		public boolean isContested()
		{
			return contestingTeam == Team.BLUE || contestingTeam == Team.RED;
		}
	}

	/** Immutable lobby HUD state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class LobbyState
	{
		/** @return whether the lobby HUD is visible */ private final boolean visible;
		/** @return raw players-waiting text */ private final String playersText;
		/** @return current waiting players, or -1 */ private final int playersWaiting;
		/** @return required waiting players when shown as a fraction, or -1 */
		private final int playersRequired;
		/** @return raw next-game text */ private final String nextGameText;
		/** @return approximate seconds represented by the next-game text, or -1 */
		private final int secondsUntilNextGame;
	}

	/** Immutable game-summary interface state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class ResultState
	{
		/** @return whether the result interface is visible */ private final boolean visible;
		/** @return raw result title */ private final String title;
		/** @return decoded game outcome */ private final Outcome outcome;
		/** @return opposing players killed, or -1 */ private final int playersKilled;
		/** @return capture events credited, or -1 */ private final int areasCaptured;
		/** @return damage dealt to the opposing Avatar, or -1 */ private final int avatarDamage;
		/** @return soul fragments sacrificed, or -1 */ private final int obeliskSacrifices;
		/** @return bones buried, or -1 */ private final int bonesBuried;
		/** @return Zeal earned in the game, or -1 */ private final int zealEarned;
		/** @return lifetime Zeal shown after the game, or -1 */ private final int lifetimeZeal;
		/** @return current Zeal Tokens shown after the game, or -1 */ private final int zealTokens;
		/** @return blue team players, or -1 */ private final int bluePlayers;
		/** @return red team players, or -1 */ private final int redPlayers;
		/** @return blue Avatar kills, or -1 */ private final int blueScore;
		/** @return red Avatar kills, or -1 */ private final int redScore;
		/** @return game kill target, or -1 */ private final int scoreTarget;
	}

	/** Immutable persistent Soul Wars totals. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Statistics
	{
		/** @return lifetime Zeal earned */ private final int zeal;
		/** @return spendable Zeal Tokens */ private final int zealTokens;
		/** @return lifetime player kills */ private final int playerKills;
		/** @return lifetime deaths */ private final int deaths;
		/** @return lifetime games played */ private final int gamesPlayed;
		/** @return lifetime games won */ private final int gamesWon;
	}

	/** Immutable Soul Wars state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return current visible phase */ private final Phase phase;
		/** @return local player's current team */ private final Team team;
		/** @return whether the game HUD is visible */ private final boolean gameHudVisible;
		/** @return raw remaining-game-time text */ private final String gameTimeText;
		/** @return remaining game seconds parsed from the HUD, or -1 */ private final int gameSecondsRemaining;
		/** @return raw activity on the 0-800 scale */ private final int activity;
		/** @return activity percentage derived by the official clientscript */ private final double activityPercent;
		/** @return blue team HUD state */ private final TeamState blueTeam;
		/** @return red team HUD state */ private final TeamState redTeam;
		/** @return west graveyard state */ private final ObjectiveState westGraveyard;
		/** @return Soul Obelisk state */ private final ObjectiveState soulObelisk;
		/** @return east graveyard state */ private final ObjectiveState eastGraveyard;
		/** @return lobby HUD state */ private final LobbyState lobby;
		/** @return game-summary state */ private final ResultState result;
		/** @return persistent player totals */ private final Statistics statistics;
		/** @return whether the account is temporarily banned from Soul Wars */ private final boolean banned;
		/** @return whether the server's recent-death flag is active */ private final boolean recentDeath;
		/** @return whether the Soul Wars tutorial is complete */ private final boolean tutorialCompleted;
		/** @return whether combat experience is disabled in Soul Wars */ private final boolean xpDisabled;
		/** @return whether the Soul Wars collection-log section is complete */
		private final boolean collectionLogCompleted;
	}
}
