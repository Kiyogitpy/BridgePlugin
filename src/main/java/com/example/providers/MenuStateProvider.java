package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Menu;
import net.runelite.api.MenuEntry;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.util.Text;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Exposes the currently-open right-click context menu, if any, so the bot can read back what a
 * menu actually contains after right-clicking (see controller.py's right_click_human/
 * click_entity(button="right")) instead of needing a human or screen vision to confirm it.
 *
 * Reads live on every poll (client.isMenuOpen() + client.getMenu()), the same on-demand style
 * NPCProvider/GroundItemProvider already use for their own state -- deliberately NOT a cached
 * MenuOpened subscription, since MenuOpened fires on every hover during normal play, not just an
 * actual right-click (see MenuPriority's own docs for this same fact); caching off it would
 * expose stale/irrelevant menu contents most of the time. isMenuOpen() answers "is a menu
 * genuinely displayed on screen right now" directly.
 */
public class MenuStateProvider extends BridgeProvider
{
	// CONFIRMED live 2026-08-06 via two independent measurements: a 3-entry ground menu
	// (Cancel/Mark Safespot/Walk here) measured height=67px, a 4-entry inventory-item menu
	// (Cancel/Examine/Drop/Use) measured height=82px. Solving header + N*row = height for both
	// (header + 3*row = 67, header + 4*row = 82) gives row = 15 (the original guess was right)
	// and header = 22 -- entries don't start at the popup's very top; there's a ~22px header
	// band above them first (confirmed: a click computed without this offset landed one entry
	// off, selecting "Cancel" at index 0's old position instead of the intended index-1 entry).
	// Entry index 0 renders at the TOP of the popup, immediately below the header.
	private static final int MENU_HEADER_HEIGHT_PX = 22;
	private static final int MENU_ROW_HEIGHT_PX = 15;

	public MenuStateProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/menu", () -> onClient(this::getMenuState));
	}

	private Object getMenuState()
	{
		Map<String, Object> data = new HashMap<>();

		if (!client.isMenuOpen())
		{
			data.put("open", false);
			return data;
		}

		Menu menu = client.getMenu();
		MenuEntry[] entries = menu.getMenuEntries();
		int menuX = menu.getMenuX();
		int menuY = menu.getMenuY();

		data.put("open", true);
		data.put("x", menuX);
		data.put("y", menuY);
		data.put("width", menu.getMenuWidth());
		data.put("height", menu.getMenuHeight());

		List<Map<String, Object>> entryList = new ArrayList<>();
		for (int i = 0; i < entries.length; i++)
		{
			MenuEntry entry = entries[i];
			Map<String, Object> entryData = new HashMap<>();
			entryData.put("index", i);
			entryData.put("option", Text.removeTags(entry.getOption()));
			entryData.put("target", Text.removeTags(entry.getTarget()));
			entryData.put("screenX", menuX + menu.getMenuWidth() / 2);
			entryData.put("screenY", menuY + MENU_HEADER_HEIGHT_PX + (i * MENU_ROW_HEIGHT_PX) + (MENU_ROW_HEIGHT_PX / 2));
			entryList.add(entryData);
		}
		data.put("entries", entryList);

		return data;
	}
}
