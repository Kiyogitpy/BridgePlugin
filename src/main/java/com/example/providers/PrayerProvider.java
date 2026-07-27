package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Prayer;
import net.runelite.api.Skill;
import net.runelite.api.Varbits;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

public class PrayerProvider extends BridgeProvider
{
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

		return data;
	}
}
