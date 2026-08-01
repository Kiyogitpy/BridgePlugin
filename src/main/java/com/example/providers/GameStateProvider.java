package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.*;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import javax.swing.SwingUtilities;

public class GameStateProvider extends BridgeProvider
{
	public GameStateProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/state", () -> onClient(this::getState));
		server.register("/debug-menu", () -> onClient(this::getDebugMenu));
	}

	// General-purpose debug endpoint: dumps whatever menu entries the client currently has built
	// (hover a target, then query this) -- the same ground truth the client's own Examine debug
	// tool reads from. Earned its keep finding a real bug 2026-08-02: it's what showed a boat's
	// "Helm" object resolves to a DIFFERENT clickable id (its EXAMINE_OBJECT entry's identifier
	// differed from its GAME_OBJECT_FIRST_OPTION entries' identifier) in a DIFFERENT worldViewId
	// than the player's own -- neither fact discoverable from ObjectProvider's own output alone.
	// Worth keeping for the next time an object/menu mismatch like that needs chasing down.
	private Object getDebugMenu()
	{
		List<Map<String, Object>> entries = new ArrayList<>();
		for (net.runelite.api.MenuEntry entry : client.getMenuEntries())
		{
			Map<String, Object> e = new HashMap<>();
			e.put("option", entry.getOption());
			e.put("target", entry.getTarget());
			e.put("identifier", entry.getIdentifier());
			e.put("type", entry.getType() != null ? entry.getType().name() : null);
			e.put("param0", entry.getParam0());
			e.put("param1", entry.getParam1());
			e.put("worldViewId", entry.getWorldViewId());
			entries.add(e);
		}
		return entries;
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();

		data.put("loggedIn", client.getGameState() == GameState.LOGGED_IN);
		data.put("gameState", client.getGameState().name());
		data.put("tick", client.getTickCount());

		Canvas canvas = client.getCanvas();
		data.put("canvasWidth", canvas.getWidth());
		data.put("canvasHeight", canvas.getHeight());

		Point canvasScreenLoc = canvas.getLocationOnScreen();
		data.put("canvasScreenX", canvasScreenLoc.x);
		data.put("canvasScreenY", canvasScreenLoc.y);

		java.awt.Window window = SwingUtilities.getWindowAncestor(canvas);
		if (window != null)
		{
			Rectangle bounds = window.getBounds();
			data.put("windowX", bounds.x);
			data.put("windowY", bounds.y);
			data.put("windowWidth", bounds.width);
			data.put("windowHeight", bounds.height);
			data.put("windowFocused", window.isActive());
		}

		Widget minimapDrawArea = client.getWidget(WidgetInfo.RESIZABLE_MINIMAP_DRAW_AREA);
		if (minimapDrawArea == null || minimapDrawArea.isHidden())
		{
			minimapDrawArea = client.getWidget(WidgetInfo.FIXED_VIEWPORT_MINIMAP_DRAW_AREA);
		}
		if (minimapDrawArea != null && !minimapDrawArea.isHidden())
		{
			Rectangle bounds = minimapDrawArea.getBounds();
			double[] scale = com.example.util.RuneLiteUtil.getStretchScale(client);
			Point minimapScreen = com.example.util.RuneLiteUtil.canvasToScreen(client, bounds.x, bounds.y);
			data.put("minimapX", minimapScreen.x);
			data.put("minimapY", minimapScreen.y);
			data.put("minimapWidth", (int) Math.round(bounds.width * scale[0]));
			data.put("minimapHeight", (int) Math.round(bounds.height * scale[1]));
		}

		data.put("viewportWidth", client.getViewportWidth());
		data.put("viewportHeight", client.getViewportHeight());
		data.put("viewportXOffset", client.getViewportXOffset());
		data.put("viewportYOffset", client.getViewportYOffset());
		data.put("stretchedEnabled", client.isStretchedEnabled());

		net.runelite.api.Point mouse = client.getMouseCanvasPosition();
		if (mouse != null)
		{
			data.put("mouseCanvasX", mouse.getX());
			data.put("mouseCanvasY", mouse.getY());
		}

		// screen-space rectangles a click can land on top of without actually reaching the game
		// world -- e.g. the resizable layouts' inventory panel floats over the 3D viewport, so a
		// target's on-screen coords (which only account for viewport bounds) can still resolve
		// underneath it. Routines/controller.py should treat a target inside one of these as
		// unclickable rather than blind-clicking into the panel.
		List<Map<String, Object>> uiBlockers = new ArrayList<>();
		addBlockerRect(uiBlockers, WidgetInfo.RESIZABLE_VIEWPORT_INVENTORY_CONTAINER);
		addBlockerRect(uiBlockers, WidgetInfo.RESIZABLE_VIEWPORT_BOTTOM_LINE_INVENTORY_CONTAINER);
		data.put("uiBlockers", uiBlockers);

		// True while a "click here to continue"-style dialogue (NPC/player chat, an
		// examine/sprite box, or a two-item box) is open -- e.g. from a misclick on scenery
		// sharing a pixel with an intended target. Deliberately excludes DIALOG_OPTION (a
		// multiple-choice box), since dismissing that would mean guessing a choice rather than
		// just continuing.
		data.put("dialogOpen", isWidgetVisible(WidgetInfo.DIALOG_NPC_TEXT)
			|| isWidgetVisible(WidgetInfo.DIALOG_PLAYER_TEXT)
			|| isWidgetVisible(WidgetInfo.DIALOG_SPRITE_TEXT)
			|| isWidgetVisible(WidgetInfo.DIALOG_DOUBLE_SPRITE_TEXT));

		// True only while the Inventory sidebar tab is the one currently selected -- the
		// INVENTORY widget group (what ConsumablesProvider/InventoryProvider resolve item
		// screen coords from) only holds live bounds while its tab is on screen. Routines
		// must switch to this tab (see controller.py's open_inventory_tab) before trusting
		// any inventory item's screenX/Y, or before treating a click on one as reachable.
		data.put("inventoryTabOpen", isWidgetVisible(WidgetInfo.INVENTORY));

		return data;
	}

	private void addBlockerRect(List<Map<String, Object>> out, WidgetInfo info)
	{
		Widget widget = client.getWidget(info);
		if (widget == null || widget.isHidden())
		{
			return;
		}

		Rectangle bounds = widget.getBounds();
		if (bounds == null || bounds.width <= 0 || bounds.height <= 0)
		{
			return;
		}

		double[] scale = com.example.util.RuneLiteUtil.getStretchScale(client);
		Point screen = com.example.util.RuneLiteUtil.canvasToScreen(client, bounds.x, bounds.y);

		Map<String, Object> rect = new HashMap<>();
		rect.put("x", screen.x);
		rect.put("y", screen.y);
		rect.put("width", (int) Math.round(bounds.width * scale[0]));
		rect.put("height", (int) Math.round(bounds.height * scale[1]));
		out.add(rect);
	}

	private boolean isWidgetVisible(WidgetInfo info)
	{
		Widget widget = client.getWidget(info);
		return widget != null && !widget.isHidden();
	}
}
