package com.example.providers;

import net.runelite.api.HeadIcon;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

/**
 * The pure half of {@link OverheadPrayer}, split out the way {@link SlayerTaskState} was: the
 * reflection cascade needs a real obfuscated client and cannot be unit-tested, but the decision it
 * feeds -- turning a raw sprite ordinal into a {@link HeadIcon}, or refusing to -- can be, and that
 * is the half where a wrong answer sends a routine into the demon with the style it is praying
 * against.
 *
 * <p>The rule under test is "unknown is never a prayer". Every path that cannot produce a real
 * overhead must answer null, because a caller treating a bad decode as MELEE would swap gear on a
 * guess.
 */
public class OverheadPrayerTest
{
	@Test
	public void aValidOrdinalDecodesToItsHeadIcon()
	{
		HeadIcon[] icons = HeadIcon.values();
		for (int ordinal = 0; ordinal < icons.length; ordinal++)
		{
			assertEquals(icons[ordinal], OverheadPrayer.fromSpriteId(ordinal));
		}
	}

	@Test
	public void theNoOverheadSentinelIsNullRatherThanAPrayer()
	{
		// -1 is what both the client and ZombieLite's own readers use for "no overhead at all".
		assertNull(OverheadPrayer.fromSpriteId(-1));
	}

	@Test
	public void anOrdinalOutsideTheEnumIsRefusedInsteadOfThrowing()
	{
		// A sprite drawn from an archive that is NOT the prayer archive lands here. The old code
		// would have indexed straight into HeadIcon.values() and thrown, taking the whole /npcs
		// read down with it; this is the case that made the reflection worth having.
		assertNull(OverheadPrayer.fromSpriteId(HeadIcon.values().length));
		assertNull(OverheadPrayer.fromSpriteId(Integer.MAX_VALUE));
		assertNull(OverheadPrayer.fromSpriteId(-99));
	}

	@Test
	public void aMissingNpcIsNullAndNeverAnException()
	{
		// Called once per npc in a bulk scan, so it must never be the thing that throws.
		assertNull(OverheadPrayer.read(null));
	}

	@Test
	public void theThreeCombatOverheadsAreTheOnesCallersActuallyBranchOn()
	{
		// Pinned because the Python side maps exactly these three names and treats everything else
		// as "not a style" -- see tormented_demons.STYLE_FROM_HEAD_ICON.
		assertEquals("MELEE", HeadIcon.MELEE.name());
		assertEquals("RANGED", HeadIcon.RANGED.name());
		assertEquals("MAGIC", HeadIcon.MAGIC.name());
	}
}
