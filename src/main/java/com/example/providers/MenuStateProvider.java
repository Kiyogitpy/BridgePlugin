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
	// NOT verified against a live client -- Menu only exposes the popup's overall bounding box
	// (getMenuX/Y/Width/Height), not per-row positions (RuneLite's context menu is a
	// custom-rendered overlay, not built from widgets with their own queryable bounds), so
	// per-entry click coordinates have to be computed from an assumed row height instead of read
	// directly. CONFIRM/CORRECT this against a real right-click menu (see this plan's Task 3)
	// before relying on it to click a specific entry -- also confirm whether entry index 0
	// renders at the TOP of the popup (this code's assumption) or the bottom.
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
			entryData.put("screenY", menuY + (i * MENU_ROW_HEIGHT_PX) + (MENU_ROW_HEIGHT_PX / 2));
			entryList.add(entryData);
		}
		data.put("entries", entryList);

		return data;
	}
}
