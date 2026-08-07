package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.MenuEntry;
import net.runelite.api.events.MenuOpened;
import net.runelite.client.game.ItemManager;
import net.runelite.client.util.Text;

import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;

/**
 * Forces a specific menu entry to be the one a plain left-click resolves to, the same trick
 * shift-click uses to force "Walk here": the last entry in the array is always what fires on
 * left-click, so swapping the entry we want into that slot makes it win regardless of what
 * would otherwise be on top (e.g. a tree rendered in front of a cave entrance).
 */
final class MenuPriority
{
	// MenuOpened fires on the client thread every time the client resolves a click/hover, not
	// just on an actual right-click -- i.e. constantly during normal play. Re-parsing all four
	// config lists into fresh Sets on every single firing churned enough small garbage to be a
	// real source of GC-pause stutter, even though no single call was expensive. These caches
	// only re-parse when the underlying config string actually changed since the last call.
	private static final CachedSet targetNpcsCache = new CachedSet();
	private static final CachedSet lootItemsCache = new CachedSet();
	private static final CachedSet targetObjectsCache = new CachedSet();
	private static final CachedSet forcedActionsCache = new CachedSet();

	private MenuPriority()
	{
	}

	static void apply(Client client, ControllerConfig config, MenuOpened event, ItemManager itemManager)
	{
		if (!config.forceClickPriority())
		{
			return;
		}

		MenuEntry[] entries = event.getMenuEntries();
		int lastIndex = entries.length - 1;
		if (lastIndex < 1)
		{
			return;
		}

		Set<String> targetNpcs = targetNpcsCache.get(config.targetNpcNames());
		Set<String> lootItems = lootItemsCache.get(config.lootWhitelist());
		Set<String> targetObjects = targetObjectsCache.get(config.targetObjectNames());
		Set<String> forcedActions = forcedActionsCache.get(config.forcedActions());
		int minLootValue = config.minLootValue();

		for (int i = lastIndex - 1; i >= 0; i--)
		{
			MenuEntry entry = entries[i];
			String option = Text.removeTags(entry.getOption()).toLowerCase(Locale.ENGLISH);
			String target = normalize(entry.getTarget());

			boolean wanted =
				("attack".equals(option) && targetNpcs.contains(target))
					|| ("take".equals(option) && (lootItems.contains(target) || meetsLootValue(itemManager, entry, minLootValue)))
					|| (targetObjects.contains(target) && !"examine".equals(option) && !"cancel".equals(option))
					|| forcedActions.contains(option);

			if (wanted)
			{
				entries[i] = entries[lastIndex];
				entries[lastIndex] = entry;
				client.setMenuEntries(entries);
				return;
			}
		}
	}

	/** Value-based loot priority, alongside the name-whitelist check above -- covers
	 * maybe_loot's (slayer.py) totalValue >= minLootValue path, which has no name to match
	 * against. Uses MenuEntry.getItemId() directly (a real method on the RuneLite API) rather
	 * than parsing `target`, and checks unit GE price only, not price x quantity -- MenuEntry
	 * carries no quantity, and ground-item drops are effectively always qty=1 per entry in
	 * practice, so this is a deliberate simplification, not an oversight. */
	private static boolean meetsLootValue(ItemManager itemManager, MenuEntry entry, int minLootValue)
	{
		if (minLootValue <= 0)
		{
			return false;
		}
		int itemId = entry.getItemId();
		if (itemId <= 0)
		{
			return false;
		}
		return itemManager.getItemPrice(itemId) >= minLootValue;
	}

	private static Set<String> normalizedSet(String commaSeparated)
	{
		List<String> parsed = ConfigExporter.parseList(commaSeparated);
		Set<String> set = new HashSet<>();
		for (String value : parsed)
		{
			set.add(value.toLowerCase(Locale.ENGLISH));
		}
		return set;
	}

	private static String normalize(String rawTarget)
	{
		String stripped = Text.removeTags(rawTarget);
		int parenIndex = stripped.indexOf(" (");
		if (parenIndex >= 0)
		{
			stripped = stripped.substring(0, parenIndex);
		}
		return stripped.toLowerCase(Locale.ENGLISH);
	}

	/** Remembers the last raw config string it parsed, so a call with the same string back-to-back
	 * (the overwhelmingly common case -- config rarely changes between one menu build and the
	 * next) returns the existing Set instead of re-parsing and re-allocating one from scratch. */
	private static final class CachedSet
	{
		private String lastRaw;
		private Set<String> value = new HashSet<>();

		Set<String> get(String raw)
		{
			if (!Objects.equals(raw, lastRaw))
			{
				lastRaw = raw;
				value = normalizedSet(raw);
			}
			return value;
		}
	}
}
