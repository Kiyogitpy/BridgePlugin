package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.*;

public class InventoryProvider extends BridgeProvider
{
	/** Game enum mapping a rune-pouch slot's type value to the rune's item id. */
	private static final int RUNEPOUCH_RUNE_ENUM = 982;

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
		server.register("/rune-pouch", () -> onClient(this::getRunePouch));
	}

	/**
	 * Rune pouch contents -- what each slot holds and how many.
	 *
	 * Added 2026-08-27 (user request). A rune pouch is a CONTAINER, so {@code /inventory} reports
	 * only the pouch item itself and says nothing about the runes inside it. That left a routine
	 * with no way to know it was about to run out: routines/plank_make.py could only find out by
	 * casting and watching the cast fail.
	 *
	 * The contents live in varbits, not an ItemContainer. The slot ids come from
	 * {@link net.runelite.api.gameval.VarbitID} rather than being hardcoded -- there are SIX
	 * slots in the current API (a divine pouch carries four; the last two are headroom), and
	 * reading them by name means a future pouch size costs nothing here.
	 *
	 * The type value is an index into the game's own rune enum, not an item id, so it is mapped
	 * through {@code client.getEnum}. That enum's id is not exposed as a named constant in this
	 * API version, so it is a literal here and the mapping is treated as best-effort: if it does
	 * not resolve, the slot is still reported with its raw {@code type} and {@code quantity} and
	 * simply carries no id/name. A caller counting runes keeps working either way.
	 */
	private Object getRunePouch()
	{
		final int[] typeVarbits = {
			VarbitID.RUNE_POUCH_TYPE_1, VarbitID.RUNE_POUCH_TYPE_2, VarbitID.RUNE_POUCH_TYPE_3,
			VarbitID.RUNE_POUCH_TYPE_4, VarbitID.RUNE_POUCH_TYPE_5, VarbitID.RUNE_POUCH_TYPE_6,
		};
		final int[] qtyVarbits = {
			VarbitID.RUNE_POUCH_QUANTITY_1, VarbitID.RUNE_POUCH_QUANTITY_2,
			VarbitID.RUNE_POUCH_QUANTITY_3, VarbitID.RUNE_POUCH_QUANTITY_4,
			VarbitID.RUNE_POUCH_QUANTITY_5, VarbitID.RUNE_POUCH_QUANTITY_6,
		};

		Map<String, Object> result = new HashMap<>();
		List<Map<String, Object>> runes = new ArrayList<>();
		result.put("runes", runes);

		// Whether a pouch is actually carried is worth reporting separately: every varbit below
		// reads 0 both when the pouch is empty and when there is no pouch at all, and those are
		// very different problems for a caller deciding whether to restock or to stop.
		boolean carried = false;
		ItemContainer inv = client.getItemContainer(InventoryID.INVENTORY);
		if (inv != null)
		{
			for (Item item : inv.getItems())
			{
				if (item == null || item.getId() <= 0)
				{
					continue;
				}
				ItemComposition comp = client.getItemDefinition(item.getId());
				String name = comp != null ? comp.getName() : null;
				if (name != null && name.toLowerCase().contains("rune pouch"))
				{
					carried = true;
					break;
				}
			}
		}
		result.put("carried", carried);

		EnumComposition runeEnum = null;
		try
		{
			runeEnum = client.getEnum(RUNEPOUCH_RUNE_ENUM);
		}
		catch (Exception ignored)
		{
			// Best-effort mapping only -- see the doc comment.
		}

		int total = 0;
		for (int i = 0; i < typeVarbits.length; i++)
		{
			int type = client.getVarbitValue(typeVarbits[i]);
			int qty = client.getVarbitValue(qtyVarbits[i]);
			if (type <= 0 || qty <= 0)
			{
				continue;
			}

			Map<String, Object> slot = new HashMap<>();
			slot.put("slot", i + 1);
			slot.put("type", type);
			slot.put("quantity", qty);

			int itemId = runeEnum != null ? runeEnum.getIntValue(type) : -1;
			if (itemId > 0)
			{
				slot.put("id", itemId);
				ItemComposition comp = client.getItemDefinition(itemId);
				if (comp != null)
				{
					slot.put("name", comp.getName());
				}
			}
			runes.add(slot);
			total += qty;
		}
		result.put("total", total);
		return result;
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
