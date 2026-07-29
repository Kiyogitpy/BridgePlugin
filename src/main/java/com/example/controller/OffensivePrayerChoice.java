package com.example.controller;

import net.runelite.api.Prayer;

/**
 * Selectable offensive (stat-boosting) prayers for the Offensive Prayer config
 * dropdown, plus three "Auto" pseudo-entries (prayer == null, resolved
 * client-side in slayer.py's PRAYER_LEVELS/AUTO_PRAYER_LADDERS, which must stay
 * in sync with the levels here) that pick the best of these the account has
 * actually unlocked for a given combat style. Unlike DefensivePrayerChoice, the
 * selection here gets flicked on right before the player's own attack and off
 * once the hit registers (see routines/slayer.py's maintain_offensive_prayer),
 * not held on for the whole fight.
 *
 * DEADEYE is deliberately NOT in the AUTO_RANGED ladder even though it's the
 * best Ranged offensive prayer -- unlocking it requires looting a scroll from
 * Royal Titans in addition to meeting the level requirement, which a level-only
 * ladder can't verify. Pick it manually if your account has it unlocked.
 */
public enum OffensivePrayerChoice
{
	NONE(null, "None", 1),
	AUTO_MELEE(null, "Auto (Melee)", 1),
	AUTO_RANGED(null, "Auto (Ranged)", 1),
	AUTO_MAGIC(null, "Auto (Magic)", 1),
	STEEL_SKIN(Prayer.STEEL_SKIN, "Steel Skin", 28),
	ULTIMATE_STRENGTH(Prayer.ULTIMATE_STRENGTH, "Ultimate Strength", 31),
	INCREDIBLE_REFLEXES(Prayer.INCREDIBLE_REFLEXES, "Incredible Reflexes", 34),
	CHIVALRY(Prayer.CHIVALRY, "Chivalry", 60),
	PIETY(Prayer.PIETY, "Piety", 70),
	EAGLE_EYE(Prayer.EAGLE_EYE, "Eagle Eye", 44),
	DEADEYE(Prayer.DEADEYE, "Deadeye", 62),
	RIGOUR(Prayer.RIGOUR, "Rigour", 74),
	MYSTIC_LORE(Prayer.MYSTIC_LORE, "Mystic Lore", 27),
	MYSTIC_MIGHT(Prayer.MYSTIC_MIGHT, "Mystic Might", 45),
	AUGURY(Prayer.AUGURY, "Augury", 77);

	private final Prayer prayer;
	private final String label;
	private final int level;

	OffensivePrayerChoice(Prayer prayer, String label, int level)
	{
		this.prayer = prayer;
		this.label = label;
		this.level = level;
	}

	public Prayer getPrayer()
	{
		return prayer;
	}

	public String getLabel()
	{
		return label;
	}

	public int getLevel()
	{
		return level;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
