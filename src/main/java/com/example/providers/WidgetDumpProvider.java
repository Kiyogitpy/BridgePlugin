package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Debug-only widget-tree dump: reports every visible widget with its group/child id, text, and
 * both canvas and screen bounds.
 *
 * <p>Re-added 2026-08-21 while generalizing login.py. An earlier version of this provider
 * established that the old saved-account login screen had no widget backing at all -- that
 * finding is what LoginProvider's hardcoded ACCOUNT_TILE_FRACTION is built on. The current
 * client shows a different screen there ("Play Now", loginIndex 10), and the welcome screen's
 * CLICK HERE TO PLAY button on the very next step *is* a real widget, so the old finding cannot
 * simply be assumed to still hold. This exists to re-test it rather than inherit it.
 *
 * <p>Two traversal modes, because the answer differs by screen:
 * <ul>
 *   <li>the default walks {@link Client#getWidgetRoots()}, which is the normal way to enumerate
 *       an interface, but returned nothing at all on the old login screen;</li>
 *   <li>{@code ?scan=true} brute-forces {@link Client#getWidget(int, int)} across a group/child
 *       range instead. A root that is not attached to the root list is invisible to the walk but
 *       still resolves by id, so a screen can look widget-free to one method and not the other.
 *       Bounded by {@code maxGroup}/{@code maxChild} because the scan is O(groups x children)
 *       lookups on the client thread, and an unbounded sweep would stall rendering.</li>
 * </ul>
 */
public class WidgetDumpProvider extends BridgeProvider
{
	private static final int DEFAULT_MAX_GROUP = 1000;
	private static final int DEFAULT_MAX_CHILD = 60;

	public WidgetDumpProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.registerQuery("/widgets/dump", query -> onClient(() -> dump(query)));
	}

	private Object dump(Map<String, String> query)
	{
		List<Map<String, Object>> found = new ArrayList<>();

		if ("true".equalsIgnoreCase(query.get("scan")))
		{
			int maxGroup = intParam(query, "maxGroup", DEFAULT_MAX_GROUP);
			int maxChild = intParam(query, "maxChild", DEFAULT_MAX_CHILD);
			for (int group = 0; group <= maxGroup; group++)
			{
				for (int child = 0; child <= maxChild; child++)
				{
					collect(client.getWidget(group, child), found);
				}
			}
		}
		else
		{
			Widget[] roots = client.getWidgetRoots();
			if (roots != null)
			{
				for (Widget root : roots)
				{
					walk(root, found);
				}
			}
		}

		Map<String, Object> data = new HashMap<>();
		data.put("gameState", client.getGameState().name());
		data.put("loginIndex", client.getLoginIndex());
		data.put("rootCount", client.getWidgetRoots() == null ? -1 : client.getWidgetRoots().length);
		data.put("count", found.size());
		data.put("widgets", found);
		return data;
	}

	private void walk(Widget widget, List<Map<String, Object>> out)
	{
		if (widget == null)
		{
			return;
		}
		collect(widget, out);
		// A widget's children live across three separate arrays; missing any of them would make a
		// screen look emptier than it is, which is exactly the wrong error for this tool to make.
		for (Widget[] group : new Widget[][]{
			widget.getStaticChildren(), widget.getDynamicChildren(), widget.getNestedChildren()})
		{
			if (group != null)
			{
				for (Widget child : group)
				{
					walk(child, out);
				}
			}
		}
	}

	private void collect(Widget widget, List<Map<String, Object>> out)
	{
		if (widget == null || widget.isHidden())
		{
			return;
		}

		Rectangle bounds = widget.getBounds();
		if (bounds == null || bounds.width <= 0 || bounds.height <= 0)
		{
			return;
		}

		Map<String, Object> entry = new HashMap<>();
		int id = widget.getId();
		entry.put("group", id >>> 16);
		entry.put("child", id & 0xFFFF);
		entry.put("index", widget.getIndex());
		entry.put("canvasX", bounds.x);
		entry.put("canvasY", bounds.y);
		entry.put("width", bounds.width);
		entry.put("height", bounds.height);

		Point screen = RuneLiteUtil.canvasToScreen(client, (int) bounds.getCenterX(), (int) bounds.getCenterY());
		entry.put("screenX", screen.x);
		entry.put("screenY", screen.y);

		String text = widget.getText();
		if (text != null && !text.isEmpty())
		{
			entry.put("text", text);
		}
		String name = widget.getName();
		if (name != null && !name.isEmpty())
		{
			entry.put("name", name);
		}
		String[] actions = widget.getActions();
		if (actions != null)
		{
			List<String> usable = new ArrayList<>();
			for (String action : actions)
			{
				if (action != null && !action.isEmpty())
				{
					usable.add(action);
				}
			}
			if (!usable.isEmpty())
			{
				entry.put("actions", usable);
			}
		}

		out.add(entry);
	}

	private static int intParam(Map<String, String> query, String key, int fallback)
	{
		String raw = query.get(key);
		if (raw == null)
		{
			return fallback;
		}
		try
		{
			return Integer.parseInt(raw);
		}
		catch (NumberFormatException e)
		{
			return fallback;
		}
	}
}
