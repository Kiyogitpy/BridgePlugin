package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.awt.Rectangle;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * TEMPORARY diagnostic endpoint: dumps the visible widget tree (recursing through every
 * root's static/dynamic/nested children) so login-screen widget IDs -- the saved-session
 * tiles, the confirm button -- can be identified empirically. Neither the older WidgetInfo
 * enum nor the newer generated InterfaceID constants expose named IDs for the login screen in
 * this client version (confirmed via javap against both); it's rendered by the game engine
 * with no documented RuneLite API surface for it. Meant to be used once (while actually on the
 * login screen) to find the real IDs, then removed/replaced by a proper LoginProvider that
 * hardcodes them.
 */
public class WidgetDumpProvider extends BridgeProvider
{
	public WidgetDumpProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/widgets/dump", () -> onClient(this::dumpWidgets));
	}

	private Object dumpWidgets()
	{
		Map<String, Object> response = new HashMap<>();
		List<Map<String, Object>> results = new ArrayList<>();
		Widget[] roots = client.getWidgetRoots();

		response.put("rootsIsNull", roots == null);
		response.put("rootCount", roots == null ? 0 : roots.length);

		if (roots != null)
		{
			for (Widget root : roots)
			{
				walk(root, results, 0);
			}
		}
		response.put("widgets", results);
		return response;
	}

	private void walk(Widget widget, List<Map<String, Object>> out, int depth)
	{
		if (widget == null || depth > 8)
		{
			return;
		}

		if (!widget.isHidden())
		{
			boolean hasText = widget.getText() != null && !widget.getText().isEmpty();
			boolean hasSprite = widget.getSpriteId() > 0;

			if (hasText || hasSprite)
			{
				Map<String, Object> data = new HashMap<>();
				int packed = widget.getId();
				data.put("groupId", packed >>> 16);
				data.put("childId", packed & 0xFFFF);
				data.put("index", widget.getIndex());
				data.put("text", widget.getText());
				data.put("textColor", String.format("#%06X", widget.getTextColor() & 0xFFFFFF));
				data.put("spriteId", widget.getSpriteId());

				Rectangle bounds = widget.getBounds();
				if (bounds != null && bounds.width > 0 && bounds.height > 0)
				{
					data.put("x", bounds.x);
					data.put("y", bounds.y);
					data.put("width", bounds.width);
					data.put("height", bounds.height);
				}
				data.put("depth", depth);
				out.add(data);
			}
		}

		walkChildren(widget.getStaticChildren(), out, depth);
		walkChildren(widget.getDynamicChildren(), out, depth);
		walkChildren(widget.getNestedChildren(), out, depth);
	}

	private void walkChildren(Widget[] children, List<Map<String, Object>> out, int depth)
	{
		if (children == null)
		{
			return;
		}
		for (Widget child : children)
		{
			walk(child, out, depth + 1);
		}
	}
}
