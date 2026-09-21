package com.example.m8aq.api.ui;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Menu;
import net.runelite.api.MenuEntry;

/** Provides copied state for the current interaction menu. */
public final class SelectionMenu
{
	private SelectionMenu()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable interaction-menu snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (!client.isMenuOpen())
		{
			return new State(false, false, 0, 0, 0, 0, 0, Collections.emptyList());
		}
		List<Entry> entries = new ArrayList<>();
		Menu menu = client.getMenu();
		MenuEntry[] menuEntries = menu == null ? null : menu.getMenuEntries();
		if (menuEntries != null)
		{
			for (MenuEntry entry : menuEntries)
			{
				if (entry != null)
				{
					entries.add(new Entry(entry.getOption(), entry.getTarget(), entry.getIdentifier(), entry.getType(),
						entry.getParam0(), entry.getParam1(), entry.getItemId(), entry.getWorldViewId(),
						entry.isForceLeftClick(), entry.isDeprioritized()));
				}
			}
		}
		return new State(true, client.isMenuScrollable(), client.getMenuScroll(),
			menu == null ? 0 : menu.getMenuX(), menu == null ? 0 : menu.getMenuY(),
			menu == null ? 0 : menu.getMenuWidth(), menu == null ? 0 : menu.getMenuHeight(),
			Collections.unmodifiableList(entries));
	}

	/** Immutable interaction-menu entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Entry
	{
		/** @return action label */ private final String option;
		/** @return formatted target */ private final String target;
		/** @return action identifier */ private final int identifier;
		/** @return menu-action type */ private final net.runelite.api.MenuAction type;
		/** @return first raw parameter */ private final int param0;
		/** @return second raw parameter */ private final int param1;
		/** @return associated item ID */ private final int itemId;
		/** @return world-view ID */ private final int worldViewId;
		/** @return force-left-click flag */ private final boolean forceLeftClick;
		/** @return deprioritized flag */ private final boolean deprioritized;
	}

	/** Immutable interaction-menu state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return whether the menu is open */ private final boolean open;
		/** @return whether the menu scrolls */ private final boolean scrollable;
		/** @return scroll offset */ private final int scroll;
		/** @return menu X */ private final int x;
		/** @return menu Y */ private final int y;
		/** @return menu width */ private final int width;
		/** @return menu height */ private final int height;
		/** @return immutable entries in client order */ private final List<Entry> entries;
	}
}
