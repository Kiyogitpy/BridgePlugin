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
