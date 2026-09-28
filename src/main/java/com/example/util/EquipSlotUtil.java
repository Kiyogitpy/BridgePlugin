package com.example.util;

import net.runelite.api.EquipmentInventorySlot;
import net.runelite.client.game.ItemEquipmentStats;
import net.runelite.client.game.ItemStats;

import java.util.Map;
import java.util.function.IntFunction;
import java.util.function.IntUnaryOperator;

/**
 * Adds an item's equipment slot to a bridge item dict: {@code equipSlot} (lowercase
 * {@link EquipmentInventorySlot} name, e.g. "ammo") and {@code twoHanded} (only when true).
 * Non-wearable and unknown items get neither key -- absent means "not wearable, or unknown".
 *
 * Added 2026-09-28 for slayer_bib.py, which must keep ONE Slayer Best in Bank pick per slot. The id
 * is CANONICALIZED first because a bank placeholder carries its own id with no stats: without it,
 * the placeholder a worn item leaves behind could not claim its slot.
 *
 * The lookups are functions (ItemManager::canonicalize, ItemManager::getItemStats in the
 * providers) so this is testable without a client, like BankSnapshot.
 */
public final class EquipSlotUtil
{
	private EquipSlotUtil()
	{
	}

	public static void describe(int itemId, IntUnaryOperator canonicalize, IntFunction<ItemStats> stats,
								Map<String, Object> out)
	{
		try
		{
			ItemStats s = stats.apply(canonicalize.applyAsInt(itemId));
			if (s == null || !s.isEquipable() || s.getEquipment() == null)
			{
				return;
			}
			ItemEquipmentStats eq = s.getEquipment();
			for (EquipmentInventorySlot slot : EquipmentInventorySlot.values())
			{
				if (slot.getSlotIdx() == eq.getSlot())
				{
					out.put("equipSlot", slot.name().toLowerCase());
					if (eq.isTwoHanded())
					{
						out.put("twoHanded", true);
					}
					return;
				}
			}
		}
		catch (RuntimeException ignored)
		{
			// Best-effort metadata: an item-stats lookup failing must never cost the caller the
			// whole /bank or /inventory response.
		}
	}
}
