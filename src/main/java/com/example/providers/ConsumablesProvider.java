package com.example.providers;

import com.example.controller.ControllerConfig;
import com.example.bridge.BridgeHttpServer;
import com.example.util.FoodUtil;
import net.runelite.api.*;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.*;

public class ConsumablesProvider extends BridgeProvider
{
	private final ControllerConfig config;

	public ConsumablesProvider(Client client, ClientThread clientThread, BridgeHttpServer server, ControllerConfig config)
	{
		super(client, clientThread, server);
		this.config = config;
	}

	@Override
	public void register()
	{
		server.register("/consumables", () -> onClient(this::getConsumables));
	}

	private Object getConsumables()
	{
		Map<String, Object> data = new HashMap<>();
		List<Map<String, Object>> food = new ArrayList<>();
		List<Map<String, Object>> prayer = new ArrayList<>();

		ItemContainer inv = client.getItemContainer(InventoryID.INVENTORY);
		if (inv == null)
		{
			data.put("food", food);
			data.put("prayer", prayer);
			return data;
		}

		Widget invWidget = client.getWidget(WidgetInfo.INVENTORY);
		Point canvasScreenLoc = com.example.util.RuneLiteUtil.getCanvasScreenLocation(client);
		Item[] items = inv.getItems();

		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];
			if (item == null || item.getId() <= 0)
			{
				continue;
			}

			ItemComposition comp = client.getItemDefinition(item.getId());
			if (comp == null)
			{
				continue;
			}

			String name = comp.getName();
			if (FoodUtil.isFood(name))
			{
				Map<String, Object> entry = new HashMap<>();
				entry.put("slot", i);
				entry.put("id", item.getId());
				entry.put("name", name);
				entry.put("heal", FoodUtil.getHealAmount(name));
				entry.put("qty", item.getQuantity());
				addScreenCoords(entry, invWidget, i, canvasScreenLoc);
				food.add(entry);
			}
			else if (FoodUtil.isPrayerPotion(name))
			{
				Map<String, Object> entry = new HashMap<>();
				entry.put("slot", i);
				entry.put("id", item.getId());
				entry.put("name", name);
				entry.put("qty", item.getQuantity());
				addScreenCoords(entry, invWidget, i, canvasScreenLoc);
				prayer.add(entry);
			}
		}

		String configuredFood = config.food();
		if (configuredFood != null && !configuredFood.isEmpty())
		{
			food.sort(Comparator.comparing(entry -> !entry.get("name").toString()
				.toLowerCase().contains(configuredFood.toLowerCase())));
		}

		String configuredPrayer = config.prayerPotion();
		if (configuredPrayer != null && !configuredPrayer.isEmpty())
		{
			prayer.sort(Comparator.comparing(entry -> !entry.get("name").toString()
				.toLowerCase().contains(configuredPrayer.toLowerCase())));
		}

		data.put("food", food);
		data.put("prayer", prayer);
		return data;
	}

	private void addScreenCoords(Map<String, Object> entry, Widget invWidget, int slot, Point canvasScreenLoc)
	{
		if (invWidget == null)
		{
			return;
		}

		Widget slotWidget = invWidget.getChild(slot);
		if (slotWidget != null)
		{
			Rectangle bounds = slotWidget.getBounds();
			entry.put("screenX", (int) bounds.getCenterX() + canvasScreenLoc.x);
			entry.put("screenY", (int) bounds.getCenterY() + canvasScreenLoc.y);
		}
	}
}
