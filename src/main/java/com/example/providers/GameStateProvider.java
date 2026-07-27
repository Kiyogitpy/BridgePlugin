package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.*;
import java.util.HashMap;
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
		server.register("/state", this::getState);
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

		return data;
	}
}
