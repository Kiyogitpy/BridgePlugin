package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.VarClientInt;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.util.Text;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * A clickable screen position for any interface element, addressed by its group/child id.
 *
 * Added 2026-08-23. Every other provider exposes a *world* thing (an NPC, an object, a tile); the
 * only interface elements reachable before this were the handful BankProvider hard-codes
 * (depositInventory, depositContainers, ...). That left a real gap: {@code /equipment} reports
 * what is worn but carries NO screen position, so a routine physically could not click a worn item
 * -- CONFIRMED while writing routines/falconry.py, which has to un-wield a Dramen staff after a
 * fairy-ring hop because the falconry glove needs the same weapon slot. There was no way to do it.
 *
 * This is deliberately generic rather than another hard-coded set of named buttons, because
 * RecorderProvider now reports the exact group/child of whatever a human clicked (see
 * recorder.py's fmt_widget). That makes the workflow: record yourself doing the thing, read the
 * decoded id out of the log, address it here. Hard-coding a name per button would need a Java
 * change for every new interface a routine ever touches.
 *
 * <h2>Coordinates</h2>
 * Widget bounds are CANVAS space, like Perspective's projections and Menu.getMenuX/Y -- not screen
 * space. They go through {@link RuneLiteUtil#canvasToScreen} for the same reason MenuStateProvider
 * does (see its comment): that conversion also applies stretched-mode scaling, so skipping it
 * produces an error that grows with distance from the canvas origin rather than a constant offset.
 * The returned screenX/screenY is the element's CENTRE, which is what a caller wants to click.
 *
 * <h2>Endpoints</h2>
 * <ul>
 *   <li>{@code GET /widget?group=387&child=18} -- one element. Add {@code &index=N} to address a
 *       dynamic child within it (an inventory or bank slot inside its container widget).</li>
 *   <li>{@code GET /widget/children?group=387&child=0} -- every child with a resolvable position,
 *       for discovering which index holds what without guessing.</li>
 *   <li>{@code GET /widget/active-tab} -- {@code {"tab": <int>}}, the sidebar tab index currently
 *       on screen. Added 2026-08-27 (user report: routines had no way to sanity-check "am I
 *       actually looking at the inventory" before clicking into it, and a blind tab-hotkey tap
 *       with no verification -- see golem_crafting.py's pre-fix key_tap(ctx.ser, "w") calls --
 *       could silently leave inventory clicks landing on whatever tab was actually open). Backed
 *       by {@code VarClientInt.INVENTORY_TAB}, the same authoritative per-tick signal
 *       PrayerProvider already relies on for its own tab (see that class's comment on why a
 *       widget group's own non-null/hidden state is NOT reliable evidence of which tab is
 *       actually showing -- it can stay loaded from earlier in the session regardless), exposed
 *       generically here instead of adding another one-off per-tab boolean.</li>
 * </ul>
 * Always 200 with {@code found:false} rather than an error when the widget is absent -- an
 * interface that is simply closed is a normal, expected state for a polling caller, not a failure.
 */
public class WidgetProvider extends BridgeProvider
{
	/** Guards /widget/children against dumping a pathologically large container. */
	private static final int MAX_CHILDREN = 500;

	public WidgetProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.registerQuery("/widget", query -> onClient(() -> getWidget(query)));
		server.registerQuery("/widget/children", query -> onClient(() -> getChildren(query)));
		server.register("/widget/active-tab", () -> onClient(this::getActiveTab));
	}

	private Object getActiveTab()
	{
		Map<String, Object> result = new HashMap<>();
		result.put("tab", client.getVarcIntValue(VarClientInt.INVENTORY_TAB));
		return result;
	}

	private Object getWidget(Map<String, String> query)
	{
		Integer group = parseInt(query.get("group"));
		Integer child = parseInt(query.get("child"));
		Integer index = parseInt(query.get("index"));

		Map<String, Object> result = new HashMap<>();
		if (group == null || child == null)
		{
			result.put("found", false);
			result.put("error", "group and child are required");
			return result;
		}

		Widget widget = client.getWidget(group, child);
		if (widget == null)
		{
			result.put("found", false);
			return result;
		}

		if (index != null)
		{
			Widget[] children = widget.getDynamicChildren();
			if (children == null || index < 0 || index >= children.length)
			{
				result.put("found", false);
				result.put("error", "index out of range");
				return result;
			}
			widget = children[index];
			if (widget == null)
			{
				result.put("found", false);
				return result;
			}
		}

		return describe(widget, index);
	}

	private Object getChildren(Map<String, String> query)
	{
		Integer group = parseInt(query.get("group"));
		Integer child = parseInt(query.get("child"));

		Map<String, Object> result = new HashMap<>();
		List<Map<String, Object>> out = new ArrayList<>();
		result.put("children", out);

		if (group == null || child == null)
		{
			result.put("found", false);
			result.put("error", "group and child are required");
			return result;
		}

		Widget parent = client.getWidget(group, child);
		if (parent == null)
		{
			result.put("found", false);
			return result;
		}

		result.put("found", true);
		Widget[] children = parent.getDynamicChildren();
		if (children == null)
		{
			return result;
		}

		for (int i = 0; i < children.length && out.size() < MAX_CHILDREN; i++)
		{
			Widget entry = children[i];
			// Empty inventory/bank slots exist as real widgets carrying itemId -1; skipping them
			// keeps a listing usable instead of 28 rows of nothing.
			if (entry == null || (entry.getItemId() <= 0 && isBlank(entry.getText())))
			{
				continue;
			}
			out.add(describe(entry, i));
		}
		return result;
	}

	private Map<String, Object> describe(Widget widget, Integer index)
	{
		Map<String, Object> data = new HashMap<>();
		data.put("found", true);
		data.put("hidden", widget.isHidden());
		if (index != null)
		{
			data.put("index", index);
		}

		String text = widget.getText();
		if (!isBlank(text))
		{
			data.put("text", Text.removeTags(text));
		}
		String name = widget.getName();
		if (!isBlank(name))
		{
			data.put("name", Text.removeTags(name));
		}

		int itemId = widget.getItemId();
		if (itemId > 0)
		{
			data.put("itemId", itemId);
			data.put("itemQty", widget.getItemQuantity());
			ItemComposition comp = client.getItemDefinition(itemId);
			if (comp != null)
			{
				data.put("itemName", comp.getName());
			}
		}

		Rectangle bounds = widget.getBounds();
		if (bounds == null || bounds.width <= 0 || bounds.height <= 0)
		{
			// A widget can exist while having no drawn area -- typically because its interface is
			// closed. Reported as found-but-unclickable (no screenX) rather than not-found, so a
			// caller can tell "wrong id" from "right id, interface not open".
			data.put("clickable", false);
			return data;
		}

		Point centre = RuneLiteUtil.canvasToScreen(client,
			bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
		data.put("screenX", centre.x);
		data.put("screenY", centre.y);
		data.put("width", bounds.width);
		data.put("height", bounds.height);
		data.put("clickable", !widget.isHidden());
		return data;
	}

	private static boolean isBlank(String value)
	{
		return value == null || value.trim().isEmpty();
	}

	private static Integer parseInt(String value)
	{
		if (value == null || value.isEmpty())
		{
			return null;
		}
		try
		{
			return Integer.parseInt(value);
		}
		catch (NumberFormatException e)
		{
			return null;
		}
	}
}
