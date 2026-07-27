package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.*;

public class InventoryProvider extends BridgeProvider
{
	public InventoryProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/inventory", () -> onClient(this::getInventory));
		server.registerQuery("/inventory/search", query -> onClient(() -> searchInventory(query)));
		server.registerPrefix("/inventory/id/", suffix -> onClient(() -> searchById(suffix)));
	}

	private Object getInventory()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		ItemContainer inv = client.getItemContainer(InventoryID.INVENTORY);
		if (inv == null)
		{
			return list;
		}

		Widget invWidget = client.getWidget(WidgetInfo.INVENTORY);
		if (invWidget == null)
		{
			return list;
		}

		int equippedAmmoId = getEquippedAmmoId();

		Item[] items = inv.getItems();
		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];
			if (item == null || item.getId() <= 0)
			{
				continue;
			}

			Map<String, Object> data = buildSlotData(i, item, invWidget);
			if (item.getId() == equippedAmmoId)
			{
				data.put("equippedAmmo", true);
			}
			list.add(data);
		}
		return list;
	}

	private Object searchInventory(Map<String, String> query)
	{
		String name = query.get("name");
		if (name == null || name.isEmpty())
		{
			Map<String, Object> notFound = new HashMap<>();
			notFound.put("exists", false);
			return notFound;
		}

		return findItem(item -> {
			ItemComposition comp = client.getItemDefinition(item.getId());
			return comp != null && comp.getName().toLowerCase().contains(name.toLowerCase());
		});
	}

	private Object searchById(String suffix)
	{
		try
		{
			int id = Integer.parseInt(suffix.replace("/", ""));
			return findItem(item -> item.getId() == id);
		}
		catch (NumberFormatException e)
		{
			Map<String, Object> notFound = new HashMap<>();
			notFound.put("exists", false);
			return notFound;
		}
	}

	private Object findItem(java.util.function.Predicate<Item> matcher)
	{
		Map<String, Object> notFound = new HashMap<>();
		notFound.put("exists", false);

		ItemContainer inv = client.getItemContainer(InventoryID.INVENTORY);
		if (inv == null)
		{
			return notFound;
		}

		Widget invWidget = client.getWidget(WidgetInfo.INVENTORY);
		if (invWidget == null)
		{
			return notFound;
		}

		Item[] items = inv.getItems();

		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];
			if (item == null || item.getId() <= 0 || !matcher.test(item))
			{
				continue;
			}

			Map<String, Object> result = buildSlotData(i, item, invWidget);
			result.put("exists", true);
			return result;
		}

		return notFound;
	}

	private Map<String, Object> buildSlotData(int slot, Item item, Widget invWidget)
	{
		Map<String, Object> data = new HashMap<>();
		data.put("slot", slot);
		data.put("id", item.getId());
		data.put("qty", item.getQuantity());

		ItemComposition comp = client.getItemDefinition(item.getId());
		if (comp != null)
		{
			data.put("name", comp.getName());
		}

		Widget slotWidget = invWidget.getChild(slot);
		if (slotWidget != null)
		{
			Rectangle bounds = slotWidget.getBounds();
			Point screen = RuneLiteUtil.canvasToScreen(client, (int) bounds.getCenterX(), (int) bounds.getCenterY());
			data.put("screenX", screen.x);
			data.put("screenY", screen.y);
		}
		return data;
	}

	private int getEquippedAmmoId()
	{
		ItemContainer equipment = client.getItemContainer(InventoryID.EQUIPMENT);
		if (equipment == null)
		{
			return -1;
		}

		Item[] eq = equipment.getItems();
		if (eq.length > 13)
		{
			Item ammo = eq[13];
			if (ammo != null && ammo.getId() > 0)
			{
				return ammo.getId();
			}
		}

		if (eq.length > 3)
		{
			Item weapon = eq[3];
			if (weapon != null && weapon.getId() > 0)
			{
				ItemComposition comp = client.getItemDefinition(weapon.getId());
				if (comp != null && comp.isStackable())
				{
					return weapon.getId();
				}
			}
		}
		return -1;
	}
}
