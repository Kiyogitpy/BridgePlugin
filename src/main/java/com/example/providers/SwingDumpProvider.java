package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import javax.swing.AbstractButton;
import javax.swing.JLabel;
import javax.swing.JList;
import javax.swing.SwingUtilities;
import java.awt.Component;
import java.awt.Container;
import java.awt.Point;
import java.awt.Rectangle;
import java.awt.Window;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * TEMPORARY diagnostic: dumps the Swing/AWT component tree of every open application window.
 * For UI that isn't part of the game engine's widget system at all -- e.g. the saved-Jagex
 * -account chooser shown before the game canvas/world exists, which /widgets/dump
 * (client.getWidgetRoots()) found genuinely empty at the login screen (rootCount: 0, not
 * null), meaning it isn't rendered through the game's interface system.
 *
 * Swing must only be touched on the AWT event dispatch thread, not the game client thread --
 * this does NOT go through BridgeProvider.onClient (that's for client-thread state); it uses
 * SwingUtilities.invokeAndWait instead.
 */
public class SwingDumpProvider extends BridgeProvider
{
	public SwingDumpProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/swing/dump", this::dumpSwingSafely);
	}

	private Object dumpSwingSafely()
	{
		List<Map<String, Object>> results = new ArrayList<>();
		try
		{
			SwingUtilities.invokeAndWait(() -> {
				for (Window window : Window.getWindows())
				{
					walk(window, results, 0);
				}
			});
		}
		catch (Exception e)
		{
			Map<String, Object> error = new HashMap<>();
			error.put("error", e.toString());
			return error;
		}
		return results;
	}

	private void walk(Component component, List<Map<String, Object>> out, int depth)
	{
		if (component == null || !component.isVisible() || depth > 20)
		{
			return;
		}

		String text = null;
		if (component instanceof AbstractButton)
		{
			text = ((AbstractButton) component).getText();
		}
		else if (component instanceof JLabel)
		{
			text = ((JLabel) component).getText();
		}

		boolean interesting = (text != null && !text.trim().isEmpty())
			|| component instanceof AbstractButton
			|| component instanceof JList;

		if (interesting)
		{
			Map<String, Object> data = new HashMap<>();
			data.put("class", component.getClass().getName());
			data.put("text", text);
			data.put("depth", depth);

			try
			{
				Point loc = component.getLocationOnScreen();
				Rectangle bounds = component.getBounds();
				data.put("screenX", loc.x);
				data.put("screenY", loc.y);
				data.put("width", bounds.width);
				data.put("height", bounds.height);
				data.put("centerX", loc.x + bounds.width / 2);
				data.put("centerY", loc.y + bounds.height / 2);
			}
			catch (Exception ignored)
			{
				// Not showing on screen -- skip coordinates, keep the rest.
			}

			out.add(data);
		}

		if (component instanceof Container)
		{
			for (Component child : ((Container) component).getComponents())
			{
				walk(child, out, depth + 1);
			}
		}
	}
}
