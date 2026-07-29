package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.controller.DefensivePrayerChoice;
import com.example.controller.OffensivePrayerChoice;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Prayer;
import net.runelite.api.Skill;
import net.runelite.api.VarClientInt;
import net.runelite.api.Varbits;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetID;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

public class PrayerProvider extends BridgeProvider
{
	// Once a prayer's widget component ID is resolved by name (see resolveByName), it's
	// stable for the rest of the client session -- RuneLite's prayer-reorder plugin only
	// repositions a widget (setPos), it never reassigns which component ID represents which
	// prayer. Caching it turns every request after the first (per prayer) into a single
	// direct client.getWidget(id) instead of re-walking the enum/item-composition lookup.
	private final Map<String, Integer> resolvedComponentIds = new HashMap<>();

	public PrayerProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/prayers", () -> onClient(this::getPrayers));
	}

	private Object getPrayers()
	{
		Map<String, Object> data = new HashMap<>();
		Map<String, Boolean> active = new LinkedHashMap<>();

		for (Prayer prayer : Prayer.values())
		{
			active.put(prayer.name(), client.isPrayerActive(prayer));
		}

		data.put("active", active);
		data.put("currentPrayer", client.getBoostedSkillLevel(Skill.PRAYER));
		data.put("maxPrayer", client.getRealSkillLevel(Skill.PRAYER));
		data.put("quickPrayerEnabled", client.getVarbitValue(Varbits.QUICK_PRAYER) == 1);

		Widget orb = client.getWidget(WidgetInfo.MINIMAP_QUICK_PRAYER_ORB);
		if (orb != null && !orb.isHidden())
		{
			Rectangle bounds = orb.getBounds();
			Point screen = RuneLiteUtil.canvasToScreen(client, (int) bounds.getCenterX(), (int) bounds.getCenterY());
			data.put("quickOrbScreenX", screen.x);
			data.put("quickOrbScreenY", screen.y);
		}

		// The Prayer interface's widget group stays loaded (non-null) once it's ever been
		// opened this session, even while a different sidebar tab is the one actually visible
		// -- client.getWidget(WidgetID.PRAYER_GROUP_ID, 0) != null is therefore a false
		// positive that never goes back to false after the first switch. VarClientInt.
		// INVENTORY_TAB (despite the name) tracks which sidebar tab is currently the visible
		// one -- 5 is Prayer -- and is the real signal routines must check before trusting
		// "icons" to be populated (see controller.py's open_prayer_tab).
		boolean tabOpen = client.getVarcIntValue(VarClientInt.INVENTORY_TAB) == PRAYER_TAB_INDEX;
		data.put("tabOpen", tabOpen);
		data.put("icons", tabOpen ? getPrayerIconScreenCoords() : new HashMap<>());

		return data;
	}

	private static final int PRAYER_TAB_INDEX = 5;

	// Individual prayer icons don't have static WidgetInfo constants (their positions can be
	// drag-reordered, and Deadeye/Mystic Vigour swap in for Eagle Eye/Mystic Might in the same
	// slot) -- so each one is resolved dynamically by walking the Prayer group's components and
	// matching on the widget's own name text (a tooltip string like
	// "<col=ff9040>Protect from Melee</col>"), rather than via the item/enum composition chain
	// (EnumID.PRAYERS_NORMAL etc.) an earlier version of this used -- that chain turned out to
	// point at stale/invalid item IDs for this account regardless of which enum variant was
	// picked, confirmed by dumping the widget tree directly and comparing. The resolved
	// component ID then gets cached (see resolvedComponentIds) so this walk only actually runs
	// once per prayer per client session.
	private Map<String, Object> getPrayerIconScreenCoords()
	{
		Map<String, Object> icons = new HashMap<>();

		for (DefensivePrayerChoice choice : DefensivePrayerChoice.values())
		{
			resolveInto(icons, choice.getPrayer(), choice.getLabel());
		}
		for (OffensivePrayerChoice choice : OffensivePrayerChoice.values())
		{
			resolveInto(icons, choice.getPrayer(), choice.getLabel());
		}

		return icons;
	}

	private void resolveInto(Map<String, Object> icons, Prayer prayer, String label)
	{
		if (prayer == null || icons.containsKey(prayer.name()))
		{
			return;
		}

		Map<String, Object> entry = resolveFromCache(prayer);
		if (entry == null)
		{
			entry = resolveByName(prayer, label);
		}
		if (entry != null)
		{
			icons.put(prayer.name(), entry);
		}
	}

	private Map<String, Object> resolveFromCache(Prayer prayer)
	{
		Integer componentId = resolvedComponentIds.get(prayer.name());
		if (componentId == null)
		{
			return null;
		}

		Widget widget = client.getWidget(WidgetID.PRAYER_GROUP_ID, componentId);
		if (widget == null || widget.isHidden())
		{
			return null;
		}

		return screenEntry(widget);
	}

	private static final int MAX_PRAYER_GROUP_COMPONENTS = 150;

	private Map<String, Object> resolveByName(Prayer prayer, String label)
	{
		for (int componentId = 0; componentId < MAX_PRAYER_GROUP_COMPONENTS; componentId++)
		{
			Widget widget = client.getWidget(WidgetID.PRAYER_GROUP_ID, componentId);
			if (widget == null)
			{
				continue;
			}

			String name = widget.getName();
			if (name == null)
			{
				continue;
			}

			// Strip the "<col=ff9040>...</col>" tooltip markup before matching on the plain
			// prayer name.
			String plain = name.replaceAll("<[^>]*>", "");
			if (!plain.equalsIgnoreCase(label))
			{
				continue;
			}

			if (widget.isHidden())
			{
				return null;
			}

			resolvedComponentIds.put(prayer.name(), componentId);
			return screenEntry(widget);
		}

		return null;
	}

	private Map<String, Object> screenEntry(Widget widget)
	{
		Rectangle bounds = widget.getBounds();
		Point screen = RuneLiteUtil.canvasToScreen(client, (int) bounds.getCenterX(), (int) bounds.getCenterY());

		Map<String, Object> entry = new HashMap<>();
		entry.put("screenX", screen.x);
		entry.put("screenY", screen.y);
		return entry;
	}
}
