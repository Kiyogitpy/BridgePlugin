package com.example.providers;

import net.runelite.api.Item;
import net.runelite.api.gameval.ItemID;
import org.junit.Test;

import java.util.List;
import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

/**
 * The pure half of BankMemoryProvider, extracted so the filtering rules can be pinned without a
 * client -- same reason RecordRing came out of RecorderProvider.
 *
 * The placeholder rule is the load-bearing one and it is copied from the Bank Memory plugin
 * (Lazyfaith/runelite-bank-memory-plugin, BankSave.fromCurrentBank): a bank PLACEHOLDER sits in
 * the container with quantity 0 under an id that canonicalizes to a DIFFERENT id. Without the
 * check, a placeholder left behind by an item you have run out of answers "yes, you have one" --
 * and a routine would withdraw nothing and then try to teleport home on an empty slot.
 */
public class BankSnapshotTest
{
	/** Stand-in for ItemManager: even ids are real, odd ids are placeholders for id-1. */
	private static final BankSnapshot.ItemLookup LOOKUP = new BankSnapshot.ItemLookup()
	{
		@Override
		public int canonicalize(int id)
		{
			return id % 2 == 0 ? id : id - 1;
		}

		@Override
		public String nameOf(int id)
		{
			return "Item " + id;
		}
	};

	@Test
	public void keepsRealItemsWithTheirNameAndQuantity()
	{
		List<Map<String, Object>> items = BankSnapshot.itemsFrom(
			new Item[]{new Item(8006, 112), new Item(2552, 3)}, LOOKUP);

		assertEquals(2, items.size());
		assertEquals(8006, items.get(0).get("id"));
		assertEquals("Item 8006", items.get(0).get("name"));
		assertEquals(112, items.get(0).get("quantity"));
		assertEquals(3, items.get(1).get("quantity"));
	}

	@Test
	public void dropsPlaceholders()
	{
		// 8007 canonicalizes to 8006 -- a placeholder for an item the bank no longer holds.
		List<Map<String, Object>> items = BankSnapshot.itemsFrom(
			new Item[]{new Item(8007, 0), new Item(2552, 3)}, LOOKUP);

		assertEquals(1, items.size());
		assertEquals(2552, items.get(0).get("id"));
	}

	@Test
	public void dropsFillersAndNullItems()
	{
		List<Map<String, Object>> items = BankSnapshot.itemsFrom(
			new Item[]{new Item(-1, 0), new Item(ItemID.BANK_FILLER, 1), new Item(2552, 3)}, LOOKUP);

		assertEquals(1, items.size());
		assertEquals(2552, items.get(0).get("id"));
	}

	@Test
	public void dropsZeroQuantityEntriesEvenWhenTheyAreNotPlaceholders()
	{
		// "I have zero of this" must never read as "I have this" -- the whole point of the
		// snapshot is answering that question for a routine about to commit to a trip.
		List<Map<String, Object>> items = BankSnapshot.itemsFrom(
			new Item[]{new Item(2552, 0), new Item(8006, 1)}, LOOKUP);

		assertEquals(1, items.size());
		assertEquals(8006, items.get(0).get("id"));
	}

	@Test
	public void toleratesANullContainerAndNullEntries()
	{
		assertTrue(BankSnapshot.itemsFrom(null, LOOKUP).isEmpty());
		assertEquals(1, BankSnapshot.itemsFrom(new Item[]{null, new Item(8006, 1)}, LOOKUP).size());
	}

	@Test
	public void neverSeenIsNotTheSameAsEmpty()
	{
		// The m8aq lesson already in CLAUDE.md: "no data" and "empty container" are different
		// answers and must not collapse, or a routine reads "bank is empty" off a bank it has
		// simply never opened.
		BankSnapshot never = BankSnapshot.neverSeen("acct");
		assertEquals(Boolean.FALSE, never.toMap().get("seen"));
		assertEquals(null, never.toMap().get("items"));

		BankSnapshot empty = BankSnapshot.captured("acct", 1L, 2, List.of(), "live");
		assertEquals(Boolean.TRUE, empty.toMap().get("seen"));
		assertEquals(0, ((List<?>) empty.toMap().get("items")).size());
	}

	@Test
	public void filtersByNameCaseInsensitiveSubstring()
	{
		BankSnapshot snap = BankSnapshot.captured("acct", 1L, 2, BankSnapshot.itemsFrom(
			new Item[]{new Item(8006, 1), new Item(2552, 1)}, LOOKUP), "live");

		List<?> hits = (List<?>) snap.filtered(List.of("ITEM 8006")).get("items");
		assertEquals(1, hits.size());

		// itemCount stays the count of the WHOLE bank, so a filtered reply can't be mistaken
		// for a bank that only holds the things asked about.
		assertEquals(2, snap.filtered(List.of("item 8006")).get("itemCount"));
	}
}
