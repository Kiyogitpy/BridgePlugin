package com.example.providers;

import net.runelite.api.Item;
import net.runelite.api.gameval.ItemID;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * One remembered bank: the account it belongs to, when it was captured, and every real item in
 * it. Deliberately a plain value with no client references, so the filtering rules can be
 * unit-tested without a running client -- the same extraction RecordRing got out of
 * RecorderProvider, and for the same reason.
 *
 * <p>The item rules are copied from the Bank Memory plugin
 * (Lazyfaith/runelite-bank-memory-plugin, {@code BankSave.fromCurrentBank}) rather than
 * reconstructed, because the placeholder rule in particular is not obvious and getting it wrong
 * fails silently: a bank PLACEHOLDER sits in the container with quantity 0 under an id that
 * canonicalizes to a DIFFERENT id, so without the check an item you have run out of still
 * answers "yes, you have one".
 */
public final class BankSnapshot
{
	/**
	 * Whatever can answer "is this id a placeholder" and "what is this id called". Narrowed to
	 * an interface so tests need no ItemManager; the provider passes one backed by it.
	 */
	public interface ItemLookup
	{
		int canonicalize(int id);

		String nameOf(int id);
	}

	private final String accountHash;
	private final boolean seen;
	private final long capturedAt;
	private final int capturedTick;
	private final String source;
	private final List<Map<String, Object>> items;

	private BankSnapshot(String accountHash, boolean seen, long capturedAt, int capturedTick,
						 String source, List<Map<String, Object>> items)
	{
		this.accountHash = accountHash;
		this.seen = seen;
		this.capturedAt = capturedAt;
		this.capturedTick = capturedTick;
		this.source = source;
		this.items = items;
	}

	/**
	 * "We have never had this account's bank open." Distinct from a captured-but-empty bank, and
	 * the distinction is load-bearing: collapsing the two lets a routine read "the bank is empty"
	 * off a bank it has simply never seen. Same rule as the vendored m8aq readers (CLAUDE.md).
	 */
	public static BankSnapshot neverSeen(String accountHash)
	{
		return new BankSnapshot(accountHash, false, 0L, 0, "none", null);
	}

	public static BankSnapshot captured(String accountHash, long capturedAt, int capturedTick,
										List<Map<String, Object>> items, String source)
	{
		return new BankSnapshot(accountHash, true, capturedAt, capturedTick, source,
			Collections.unmodifiableList(new ArrayList<>(items)));
	}

	/**
	 * Every real item in a bank container, as {id, name, quantity}.
	 *
	 * <p>Rejected, in order: null entries; placeholders (canonicalize disagrees with the id);
	 * {@code -1} and BANK_FILLER; and anything with quantity 0, because "I have zero of this"
	 * must never read as "I have this".
	 */
	public static List<Map<String, Object>> itemsFrom(Item[] contents, ItemLookup lookup)
	{
		List<Map<String, Object>> out = new ArrayList<>();
		if (contents == null)
		{
			return out;
		}

		for (Item item : contents)
		{
			if (item == null)
			{
				continue;
			}

			int id = item.getId();
			if (id == -1 || id == ItemID.BANK_FILLER)
			{
				continue;
			}
			if (lookup.canonicalize(id) != id)
			{
				// A placeholder for something the bank no longer holds.
				continue;
			}
			if (item.getQuantity() <= 0)
			{
				continue;
			}

			Map<String, Object> entry = new HashMap<>();
			entry.put("id", id);
			entry.put("name", lookup.nameOf(id));
			entry.put("quantity", item.getQuantity());
			out.add(entry);
		}
		return out;
	}

	public boolean isSeen()
	{
		return seen;
	}

	public String getAccountHash()
	{
		return accountHash;
	}

	public List<Map<String, Object>> getItems()
	{
		return items;
	}

	public long getCapturedAt()
	{
		return capturedAt;
	}

	public int getCapturedTick()
	{
		return capturedTick;
	}

	public Map<String, Object> toMap()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("accountHash", accountHash);
		data.put("seen", seen);
		data.put("capturedAt", capturedAt);
		data.put("capturedTick", capturedTick);
		data.put("source", source);
		data.put("itemCount", items == null ? 0 : items.size());
		data.put("items", items);
		return data;
	}

	/**
	 * The same reply, with `items` narrowed to those whose name contains one of `names`
	 * (case-insensitive substring, matching banker.py's own keepItems convention).
	 *
	 * <p>`itemCount` deliberately stays the size of the WHOLE bank, so a filtered reply can
	 * never be mistaken for a bank that holds only the things asked about.
	 */
	public Map<String, Object> filtered(List<String> names)
	{
		Map<String, Object> data = toMap();
		if (items == null || names == null || names.isEmpty())
		{
			return data;
		}

		List<String> needles = new ArrayList<>();
		for (String name : names)
		{
			if (name != null && !name.trim().isEmpty())
			{
				needles.add(name.trim().toLowerCase(Locale.ROOT));
			}
		}
		if (needles.isEmpty())
		{
			return data;
		}

		List<Map<String, Object>> hits = new ArrayList<>();
		for (Map<String, Object> entry : items)
		{
			String name = String.valueOf(entry.get("name")).toLowerCase(Locale.ROOT);
			for (String needle : needles)
			{
				if (name.contains(needle))
				{
					hits.add(entry);
					break;
				}
			}
		}
		data.put("items", hits);
		return data;
	}
}
