package net.runelite.client.plugins.m8aq.api.progression;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Skill;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;

/** Provides every named emote and whether the player has unlocked it. */
public final class Emotes
{
	private static final String[] DEFAULT_EMOTES = {"Yes", "No", "Bow", "Angry", "Think", "Wave", "Shrug",
		"Cheer", "Beckon", "Laugh", "Jump for Joy", "Yawn", "Dance", "Jig", "Spin", "Headbang", "Cry",
		"Blow Kiss", "Panic", "Raspberry", "Clap", "Salute"};

	private Emotes()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable emote snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Map<String, Boolean> unlocks = new LinkedHashMap<>();
		for (String emote : DEFAULT_EMOTES)
		{
			unlocks.put(emote, true);
		}
		boolean goblin = client.getVarpValue(VarPlayerID.LOST_TRIBE) >= 7;
		unlocks.put("Goblin Bow", goblin);
		unlocks.put("Goblin Salute", goblin);
		put(unlocks, "Glass Box", client, VarbitID.EMOTE_GLASSBOX);
		put(unlocks, "Climb Rope", client, VarbitID.EMOTE_CLIMBROPE);
		put(unlocks, "Lean", client, VarbitID.EMOTE_LEAN);
		put(unlocks, "Glass Wall", client, VarbitID.EMOTE_GLASSWALL);
		put(unlocks, "Idea", client, VarbitID.SOS_EMOTE_IDEA);
		put(unlocks, "Stamp", client, VarbitID.SOS_EMOTE_STAMP);
		put(unlocks, "Flap", client, VarbitID.SOS_EMOTE_FLAP);
		put(unlocks, "Slap Head", client, VarbitID.SOS_EMOTE_DOH);
		put(unlocks, "Zombie Walk", client, VarbitID.EMOTE_ZOMBIE_WALK);
		put(unlocks, "Zombie Dance", client, VarbitID.EMOTE_ZOMBIE_DANCE);
		put(unlocks, "Scared", client, VarbitID.EMOTE_TERRIFIED);
		put(unlocks, "Rabbit Hop", client, VarbitID.EMOTE_BUNNY_HOP);
		put(unlocks, "Sit up", client, VarbitID.EMOTE_DRILLDEMON);
		put(unlocks, "Push up", client, VarbitID.EMOTE_DRILLDEMON);
		put(unlocks, "Star jump", client, VarbitID.EMOTE_DRILLDEMON);
		put(unlocks, "Jog", client, VarbitID.EMOTE_DRILLDEMON);
		put(unlocks, "Flex", client, VarbitID.EMOTE_FLEX);
		put(unlocks, "Zombie Hand", client, VarbitID.EMOTE_ZOMBIE_HAND);
		put(unlocks, "Hypermobile Drinker", client, VarbitID.EMOTE_ASH);
		unlocks.put("Skill Cape", hasSkillCapeEmote(client));
		put(unlocks, "Air Guitar", client, VarbitID.EMOTE_MUSICCAPE);
		put(unlocks, "Uri transform", client, VarbitID.EMOTE_URI_TRANSFORM);
		put(unlocks, "Smooth dance", client, VarbitID.EMOTE_HOTLINE_BLING);
		put(unlocks, "Crazy dance", client, VarbitID.EMOTE_GANGNAM);
		put(unlocks, "Premier Shield", client, VarbitID.EMOTE_PREMIER_CLUB_2018);
		put(unlocks, "Explore", client, VarbitID.EMOTE_EXPLORE);
		int relicType = client.getVarbitValue(VarbitID.POH_LEAGUEHALL_OUTFITSTAND_RELICHUNTER_TYPE);
		unlocks.put("Relic unlock", relicType >= 3 && relicType <= 18 && relicType % 3 == 0);
		put(unlocks, "Party", client, VarbitID.EMOTE_PARTY);
		put(unlocks, "Trick", client, VarbitID.EMOTE_TRICK);
		unlocks.put("Fortis Salute", client.getVarpValue(VarPlayerID.COLOSSEUM_GLORY) >= 20_000);
		unlocks.put("Sit down", true);
		put(unlocks, "Crab dance", client, VarbitID.EMOTE_CRAB);
		return new State(Collections.unmodifiableMap(unlocks),
			(int) unlocks.values().stream().filter(Boolean::booleanValue).count());
	}

	private static void put(Map<String, Boolean> unlocks, String name, Client client, int varbit)
	{
		unlocks.put(name, client.getVarbitValue(varbit) != 0);
	}

	private static boolean hasSkillCapeEmote(Client client)
	{
		int maximumQuestPoints = client.getVarbitValue(VarbitID.QP_MAX);
		if (maximumQuestPoints > 0 && client.getVarpValue(VarPlayerID.QP) >= maximumQuestPoints
			|| client.getVarbitValue(VarbitID.EMOTE_MAXCAPE) != 0
			|| client.getVarbitValue(VarbitID.EMOTE_EVENT_SKILLCAPES) != 0)
		{
			return true;
		}
		for (Skill skill : Skill.values())
		{
			if (!"Overall".equals(skill.getName()) && client.getRealSkillLevel(skill) >= 99)
			{
				return true;
			}
		}
		return false;
	}

	/** Immutable readable emote state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return emote names mapped to their current unlocked state */ private final Map<String, Boolean> unlocks;
		/** @return number of currently unlocked emotes */ private final int unlockedCount;
	}
}
