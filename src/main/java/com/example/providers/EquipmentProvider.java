package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.*;
import net.runelite.client.callback.ClientThread;

import java.util.HashMap;
import java.util.Map;

public class EquipmentProvider extends BridgeProvider
{
	private static final int SLOT_HELMET = 0;
	private static final int SLOT_CAPE = 1;
	private static final int SLOT_AMULET = 2;
	private static final int SLOT_BODY = 4;
	private static final int SLOT_LEGS = 7;
	private static final int SLOT_WEAPON = 3;
	private static final int SLOT_SHIELD = 5;
	private static final int SLOT_GLOVES = 9;
	private static final int SLOT_BOOTS = 10;
	private static final int SLOT_RING = 12;
	private static final int SLOT_AMMO = 13;

	public EquipmentProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/equipment", () -> onClient(this::getEquipment));
	}

	private Object getEquipment()
	{
		Map<String, Object> data = new HashMap<>();
		ItemContainer equipment = client.getItemContainer(InventoryID.EQUIPMENT);
		if (equipment == null)
		{
			return data;
		}

		Item[] items = equipment.getItems();
		data.put("helmet", slotItem(items, SLOT_HELMET));
		data.put("cape", slotItem(items, SLOT_CAPE));
		data.put("amulet", slotItem(items, SLOT_AMULET));
		data.put("body", slotItem(items, SLOT_BODY));
		data.put("legs", slotItem(items, SLOT_LEGS));
		data.put("weapon", slotItem(items, SLOT_WEAPON));
		data.put("shield", slotItem(items, SLOT_SHIELD));
		data.put("gloves", slotItem(items, SLOT_GLOVES));
		data.put("boots", slotItem(items, SLOT_BOOTS));
		data.put("ring", slotItem(items, SLOT_RING));
		data.put("ammo", slotItem(items, SLOT_AMMO));
		return data;
	}

	private Map<String, Object> slotItem(Item[] items, int slot)
	{
		Map<String, Object> slotData = new HashMap<>();
		if (slot >= items.length)
		{
			return slotData;
		}

		Item item = items[slot];
		if (item == null || item.getId() <= 0)
		{
			return slotData;
		}

		slotData.put("id", item.getId());
		slotData.put("qty", item.getQuantity());

		ItemComposition comp = client.getItemDefinition(item.getId());
		if (comp != null)
		{
			slotData.put("name", comp.getName());
		}
		return slotData;
	}
}
