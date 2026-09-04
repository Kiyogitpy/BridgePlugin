package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.util.Text;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * {@code GET /world}: the current world number plus everything a routine needs to hop worlds
 * through the in-game World Switcher with PHYSICAL clicks (the user's decision 2026-09-03,
 * keeping the Arduino-only input rule rather than calling the client's own hop): the logout
 * tab button, the "World Switcher" button on the logout tab, whether the switcher list is open,
 * the list's screen rectangle (to scroll over), and every row with a numeric world label.
 *
 * Added 2026-09-03 after seven straight failed rounds on a non-themed world (the guardian
 * drained to 0% within a minute of every start) -- USER: "gotta be on a guardians of the rift
 * world, otherwise i dont think we finish a game". Widget bounds are canvas space and go
 * through {@link RuneLiteUtil#canvasToScreen}, same as WidgetProvider.
 */
public class WorldProvider extends BridgeProvider
{
	private static final int WORLD_SWITCHER_GROUP = 69;
	private static final int[] LIST_LAYER_CHILDREN = {17, 18, 19};   // scrollarea, buttons, info
	private static final int MAX_ROWS = 800;

	public WorldProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/world", () -> onClient(this::getWorld));
	}

	private Object getWorld()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("world", client.getWorld());

		Map<String, Object> logoutTab = firstVisible(
			WidgetInfo.RESIZABLE_VIEWPORT_BOTTOM_LINE_LOGOUT_BUTTON,
			WidgetInfo.RESIZABLE_MINIMAP_LOGOUT_BUTTON,
			WidgetInfo.RESIZABLE_VIEWPORT_LOGOUT_ICON,
			WidgetInfo.FIXED_VIEWPORT_LOGOUT_ICON);
		data.put("logoutTab", logoutTab);
		data.put("worldSwitcherButton", centre(client.getWidget(WidgetInfo.WORLD_SWITCHER_BUTTON)));

		Widget list = client.getWidget(WidgetInfo.WORLD_SWITCHER_LIST);
		boolean open = list != null && !list.isHidden();
		data.put("switcherOpen", open);
		if (open)
		{
			// The VISIBLE clip of the list, not the list's own bounds: WORLD_SWITCHER_LIST is the
			// scrollable content layer (MEASURED 2026-09-03: 10455px tall, starting at y -1651),
			// so a row can be "inside" it while thousands of pixels off screen. 69:17 is the
			// scrollarea layer whose bounds are the on-screen window; 69:10 (list) is the fallback.
			Widget clip = client.getWidget(WORLD_SWITCHER_GROUP, 17);
			if (clip == null || clip.isHidden() || clip.getBounds() == null || clip.getBounds().height > 2000)
			{
				clip = client.getWidget(WORLD_SWITCHER_GROUP, 10);
			}
			data.put("listBounds", rect((clip != null ? clip : list).getBounds()));
			data.put("contentBounds", rect(list.getBounds()));
			List<Map<String, Object>> rows = new ArrayList<>();
			for (int childId : LIST_LAYER_CHILDREN)
			{
				Widget layer = client.getWidget(WORLD_SWITCHER_GROUP, childId);
				if (layer == null)
				{
					continue;
				}
				collectRows(layer.getDynamicChildren(), rows);
				collectRows(layer.getStaticChildren(), rows);
				collectRows(layer.getNestedChildren(), rows);
			}
			collectRows(list.getDynamicChildren(), rows);
			data.put("rows", rows);
		}
		return data;
	}

	private void collectRows(Widget[] children, List<Map<String, Object>> rows)
	{
		if (children == null)
		{
			return;
		}
		for (Widget w : children)
		{
			if (w == null || rows.size() >= MAX_ROWS)
			{
				continue;
			}
			String text = w.getText() == null ? "" : Text.removeTags(w.getText()).trim();
			if (text.isEmpty() || !text.chars().allMatch(Character::isDigit))
			{
				continue;
			}
			Rectangle b = w.getBounds();
			if (b == null || b.width <= 0)
			{
				continue;
			}
			Map<String, Object> row = new HashMap<>();
			row.put("world", Integer.parseInt(text));
			row.put("hidden", w.isHidden());
			Point screen = RuneLiteUtil.canvasToScreen(client, (int) b.getCenterX(), (int) b.getCenterY());
			row.put("screenX", screen.x);
			row.put("screenY", screen.y);
			row.put("bounds", rect(b));
			rows.add(row);
		}
	}

	private Map<String, Object> firstVisible(WidgetInfo... infos)
	{
		for (WidgetInfo info : infos)
		{
			Widget w = client.getWidget(info);
			if (w != null && !w.isHidden())
			{
				Map<String, Object> c = centre(w);
				if (c != null)
				{
					return c;
				}
			}
		}
		return null;
	}

	private Map<String, Object> centre(Widget w)
	{
		if (w == null || w.isHidden())
		{
			return null;
		}
		Rectangle b = w.getBounds();
		if (b == null || b.width <= 0)
		{
			return null;
		}
		Point screen = RuneLiteUtil.canvasToScreen(client, (int) b.getCenterX(), (int) b.getCenterY());
		Map<String, Object> m = new HashMap<>();
		m.put("screenX", screen.x);
		m.put("screenY", screen.y);
		return m;
	}

	private Map<String, Object> rect(Rectangle b)
	{
		if (b == null)
		{
			return null;
		}
		double[] scale = RuneLiteUtil.getStretchScale(client);
		Point screen = RuneLiteUtil.canvasToScreen(client, b.x, b.y);
		Map<String, Object> m = new HashMap<>();
		m.put("x", screen.x);
		m.put("y", screen.y);
		m.put("width", (int) Math.round(b.width * scale[0]));
		m.put("height", (int) Math.round(b.height * scale[1]));
		return m;
	}
}
