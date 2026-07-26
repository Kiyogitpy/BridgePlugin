package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.MenuEntry;
import net.runelite.api.events.MenuOpened;
import net.runelite.client.util.Text;

import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Forces a specific menu entry to be the one a plain left-click resolves to, the same trick
 * shift-click uses to force "Walk here": the last entry in the array is always what fires on
 * left-click, so swapping the entry we want into that slot makes it win regardless of what
 * would otherwise be on top (e.g. a tree rendered in front of a cave entrance).
 */
final class MenuPriority
{
	private MenuPriority()
	{
	}

	static void apply(Client client, ControllerConfig config, MenuOpened event)
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

		Set<String> targetNpcs = normalizedSet(config.targetNpcNames());
		Set<String> lootItems = normalizedSet(config.lootWhitelist());
		Set<String> targetObjects = normalizedSet(config.targetObjectNames());

		for (int i = lastIndex - 1; i >= 0; i--)
		{
			MenuEntry entry = entries[i];
			String option = Text.removeTags(entry.getOption()).toLowerCase(Locale.ENGLISH);
			String target = normalize(entry.getTarget());

			boolean wanted =
				("attack".equals(option) && targetNpcs.contains(target))
					|| ("take".equals(option) && lootItems.contains(target))
					|| (targetObjects.contains(target) && !"examine".equals(option) && !"cancel".equals(option));

			if (wanted)
			{
				entries[i] = entries[lastIndex];
				entries[lastIndex] = entry;
				client.setMenuEntries(entries);
				return;
			}
		}
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
}
