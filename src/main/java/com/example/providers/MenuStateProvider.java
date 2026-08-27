package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Menu;
import net.runelite.api.MenuEntry;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.util.Text;

import java.awt.Point;

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
		server.register("/hover", () -> onClient(this::getHoverState));
	}

	/**
	 * What a LEFT-CLICK would do right now, without opening any menu (added 2026-08-27 for
	 * verify-then-click on moving targets -- see controller.py's hover_matches). The client
	 * rebuilds its menu entries every render frame for whatever is under the cursor, so the
	 * highest-index entry (see getMenuState's ordering comment: highest index = top-priority =
	 * the default action) is exactly what the game's own top-left hover text shows. Un-cached
	 * and read-only, same live style as /menu itself.
	 *
	 * menuOpen matters to the consumer: with a real right-click menu displayed, a left-click
	 * selects a menu row rather than acting on the world, so verification must not apply.
	 */
	private Object getHoverState()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("menuOpen", client.isMenuOpen());

		MenuEntry[] entries = client.getMenu().getMenuEntries();
		data.put("count", entries.length);
		if (entries.length > 0)
		{
			MenuEntry top = entries[entries.length - 1];
			data.put("option", Text.removeTags(top.getOption()));
			data.put("target", Text.removeTags(top.getTarget()));
		}

		List<Map<String, Object>> entryList = new ArrayList<>();
		for (MenuEntry entry : entries)
		{
			Map<String, Object> entryData = new HashMap<>();
			entryData.put("option", Text.removeTags(entry.getOption()));
			entryData.put("target", Text.removeTags(entry.getTarget()));
			entryList.add(entryData);
		}
		data.put("entries", entryList);
		return data;
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

		// Menu.getMenuX()/getMenuY() are canvas-space coordinates (like every other provider's
		// Perspective/Widget-derived positions), NOT absolute screen coordinates -- CONFIRMED
		// live 2026-08-06 (user report: computed click landed "miles" off in both x and y) after
		// the row-height/header fix alone still didn't work. Every other provider in this
		// codebase converts through RuneLiteUtil.canvasToScreen(...) before returning a
		// "screenX"/"screenY" field (see GroundItemProvider); this one skipped that step
		// entirely. canvasToScreen also applies stretched-mode display scaling, not just a fixed
		// offset, so the miss grows with distance from the canvas origin rather than being a
		// constant error -- matching what was actually observed.
		int canvasEntryX = menuX + menu.getMenuWidth() / 2;
		List<Map<String, Object>> entryList = new ArrayList<>();
		for (int i = 0; i < entries.length; i++)
		{
			MenuEntry entry = entries[i];
			// getMenuEntries() is bottom-to-top: index 0 is the menu's LAST/lowest-priority
			// entry (visually at the BOTTOM -- typically "Cancel"), and the highest index is
			// the default/top-priority action (visually at the TOP, immediately below the
			// header). CONFIRMED live 2026-08-06 via a screenshot of a 5-entry "Take Adamant
			// full helm" menu: visual top-to-bottom order was
			// [Take, Walk here, Examine, Mark Safespot, Cancel], but getMenuEntries() (and
			// therefore this endpoint, before this fix) reported index 0 = Cancel .. index 4 =
			// Take -- the reverse of what row-index-0-is-the-top-row assumed. Every ground-tile
			// menu logged up to this point is consistent with the same pattern (Cancel always
			// comes back at index 0). The original live verification of MENU_HEADER_HEIGHT_PX/
			// MENU_ROW_HEIGHT_PX only ever clicked "Mark Safespot", the *middle* entry of a
			// 3-entry menu -- the middle row sits at the same offset whether you count top-down
			// or bottom-up, so that test proved the header/row constants but never actually
			// exercised, let alone confirmed, direction.
			int displayRow = entries.length - 1 - i;
			int canvasEntryY = menuY + MENU_HEADER_HEIGHT_PX + (displayRow * MENU_ROW_HEIGHT_PX) + (MENU_ROW_HEIGHT_PX / 2);
			Point screen = RuneLiteUtil.canvasToScreen(client, canvasEntryX, canvasEntryY);

			Map<String, Object> entryData = new HashMap<>();
			entryData.put("index", i);
			entryData.put("option", Text.removeTags(entry.getOption()));
			entryData.put("target", Text.removeTags(entry.getTarget()));
			entryData.put("screenX", screen.x);
			entryData.put("screenY", screen.y);
			entryList.add(entryData);
		}
		data.put("entries", entryList);

		return data;
	}
}
