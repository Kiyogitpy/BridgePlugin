package com.example.util;

import net.runelite.api.EquipmentInventorySlot;
import net.runelite.client.game.ItemEquipmentStats;
import net.runelite.client.game.ItemStats;
import org.junit.Test;

import java.util.HashMap;
import java.util.Map;
import java.util.function.IntFunction;
import java.util.function.IntUnaryOperator;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

/**
 * Pins the equipSlot/twoHanded fields /bank and /inventory report per item. slayer_bib.py keys on
 * them to keep ONE Slayer Best in Bank pick per slot (it recommended three ammo-slot blessings on
 * one trip, 2026-09-21) -- so a placeholder has to report its REAL item's slot, or a worn item's
 * placeholder could not claim its slot and the alternative behind it would be withdrawn.
 */
public class EquipSlotUtilTest
{
	private static final int REAL_BLESSING = 100;
	private static final int PLACEHOLDER_BLESSING = 101;   // canonicalizes to 100
	private static final int BLOWPIPE = 200;
	private static final int POTION = 300;

	private static final IntUnaryOperator CANON = id -> id == PLACEHOLDER_BLESSING ? REAL_BLESSING : id;

	private static final IntFunction<ItemStats> STATS = id ->
	{
		switch (id)
		{
			case REAL_BLESSING:
				return new ItemStats(true, 0, 0, ItemEquipmentStats.builder()
					.slot(EquipmentInventorySlot.AMMO.getSlotIdx()).build());
			case BLOWPIPE:
				return new ItemStats(true, 0, 0, ItemEquipmentStats.builder()
					.slot(EquipmentInventorySlot.WEAPON.getSlotIdx()).isTwoHanded(true).build());
			case POTION:
				return new ItemStats(false, 0, 0, null);
			default:
				return null;
		}
	};

	private static Map<String, Object> describe(int id)
	{
		Map<String, Object> out = new HashMap<>();
		EquipSlotUtil.describe(id, CANON, STATS, out);
		return out;
	}

	@Test
	public void wearableReportsLowercaseSlot()
	{
		Map<String, Object> out = describe(REAL_BLESSING);
		assertEquals("ammo", out.get("equipSlot"));
		assertFalse(out.containsKey("twoHanded"));
	}

	@Test
	public void placeholderReportsItsRealItemsSlot()
	{
		assertEquals("ammo", describe(PLACEHOLDER_BLESSING).get("equipSlot"));
	}

	@Test
	public void twoHandedWeaponIsFlagged()
	{
		Map<String, Object> out = describe(BLOWPIPE);
		assertEquals("weapon", out.get("equipSlot"));
		assertEquals(Boolean.TRUE, out.get("twoHanded"));
	}

	@Test
	public void nonWearableAndUnknownReportNothing()
	{
		assertTrue(describe(POTION).isEmpty());
		assertTrue(describe(999).isEmpty());
	}

	@Test
	public void aThrowingLookupIsSwallowed()
	{
		Map<String, Object> out = new HashMap<>();
		EquipSlotUtil.describe(REAL_BLESSING, CANON, id -> { throw new IllegalStateException(); }, out);
		assertTrue(out.isEmpty());
	}
}
