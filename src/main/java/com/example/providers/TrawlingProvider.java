package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.game.ItemManager;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Deep sea trawling state on GET /trawling: the two ITEM CONTAINERS nothing else on this bridge
 * can reach, plus the trawling varbits.
 *
 * Why this exists at all: every other fact routines/deep_sea_trawling.py needs is already
 * served -- the shoal and the net are both ordinary objects on /objects (ObjectProvider walks
 * the boat's nested WorldView and the top-level ocean scene and reports both in top-level world
 * coordinates), the Sailing/Fishing XP heartbeat arrives as pushed XP_GAINED events, and
 * /sailing already reports boat speed and whether steering is engaged. What is NOT reachable is
 * how many fish are in the nets: that lives in item container 968 (inv sailing_trawling_net,
 * size 250, decoded cache dump 5bc2ff1e), and the boat's cargo hold in 963..967
 * (sailing_boat_N_cargohold, size 240). No m8aq reader covers sailing at all (/api/index has no
 * sailing group).
 *
 * Deliberately NOT TickCache-gated, same reasoning as GolemProvider: these are container and
 * varbit reads like /player, not the bulk scene scans TickCache exists to make cheap, and the
 * routine polls netFish as a click-confirmation signal where a stale read would reintroduce the
 * lag the endpoint is here to remove.
 *
 * A missing container serves null, never 0 -- "not aboard a boat with a net" and "the net is
 * empty" are different answers and the Python side branches on the difference.
 */
public class TrawlingProvider extends BridgeProvider
{
	private static final int NET_CAPACITY = 250;       // inv 968 sailing_trawling_net size
	// The DECLARED max of the cargo-hold item containers (inv 963..967, decoded cache size 240).
	// It is NOT necessarily a given boat's real built hold: a smaller hull can hold fewer, and
	// nothing here measures that. So `cargo.capacity` is a ceiling, not a promise, and the Python
	// side must treat "a Collect-from that moved nothing" as the AUTHORITATIVE full signal --
	// see routines/deep_sea_trawling.py's collect_phase / _cargo_is_full. `cargo.slots` below is
	// served alongside it as the container's own raw length, so a live session can tell the two
	// numbers apart without a rebuild.
	private static final int CARGO_CAPACITY = 240;     // inv 963..967 declared size

	private static final int[] CARGO_CONTAINERS = {
		InventoryID.SAILING_BOAT_1_CARGOHOLD,
		InventoryID.SAILING_BOAT_2_CARGOHOLD,
		InventoryID.SAILING_BOAT_3_CARGOHOLD,
		InventoryID.SAILING_BOAT_4_CARGOHOLD,
		InventoryID.SAILING_BOAT_5_CARGOHOLD,
	};

	// Two trawling-net facility slots, each with a depth and the hotspot it was built on.
	private static final int[][] NET_VARBITS = {
		{VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_0_DEPTH,
		 VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_0_HOTSPOT_ID},
		{VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_1_DEPTH,
		 VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_1_HOTSPOT_ID},
	};

	private final ItemManager itemManager;

	public TrawlingProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
	                        ItemManager itemManager)
	{
		super(client, clientThread, server);
		this.itemManager = itemManager;
	}

	@Override
	public void register()
	{
		server.register("/trawling", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();

		ItemContainer net = client.getItemContainer(InventoryID.SAILING_TRAWLING_NET);
		data.put("netCapacity", NET_CAPACITY);
		data.put("netFish", net != null ? countItems(net) : null);
		data.put("netItems", net != null ? describe(net) : null);

		Map<String, Object> cargo = null;
		for (int id : CARGO_CONTAINERS)
		{
			ItemContainer hold = client.getItemContainer(id);
			if (hold == null)
			{
				continue;
			}
			cargo = new HashMap<>();
			cargo.put("containerId", id);
			cargo.put("used", usedSlots(hold));
			cargo.put("capacity", CARGO_CAPACITY);
			// The container's own raw length, next to the declared max above -- the two can
			// disagree, and only this one comes from the client.
			cargo.put("slots", hold.getItems().length);
			cargo.put("items", describe(hold));
			break;
		}
		data.put("cargo", cargo);

		data.put("carryingCargo", client.getVarbitValue(VarbitID.SAILING_CARRYING_CARGO));

		List<Map<String, Object>> nets = new ArrayList<>();
		for (int slot = 0; slot < NET_VARBITS.length; slot++)
		{
			Map<String, Object> entry = new LinkedHashMap<>();
			entry.put("slot", slot);
			entry.put("depth", client.getVarbitValue(NET_VARBITS[slot][0]));
			entry.put("hotspotId", client.getVarbitValue(NET_VARBITS[slot][1]));
			nets.add(entry);
		}
		data.put("nets", nets);

		return data;
	}

	private int countItems(ItemContainer container)
	{
		int total = 0;
		for (Item item : container.getItems())
		{
			if (item != null && item.getId() > 0)
			{
				total += Math.max(item.getQuantity(), 0);
			}
		}
		return total;
	}

	private int usedSlots(ItemContainer container)
	{
		int used = 0;
		for (Item item : container.getItems())
		{
			if (item != null && item.getId() > 0)
			{
				used++;
			}
		}
		return used;
	}

	private List<Map<String, Object>> describe(ItemContainer container)
	{
		Map<Integer, Integer> totals = new LinkedHashMap<>();
		for (Item item : container.getItems())
		{
			if (item != null && item.getId() > 0)
			{
				totals.merge(item.getId(), Math.max(item.getQuantity(), 0), Integer::sum);
			}
		}

		List<Map<String, Object>> out = new ArrayList<>();
		for (Map.Entry<Integer, Integer> entry : totals.entrySet())
		{
			Map<String, Object> row = new LinkedHashMap<>();
			row.put("id", entry.getKey());
			row.put("name", itemManager.getItemComposition(entry.getKey()).getName());
			row.put("quantity", entry.getValue());
			out.add(row);
		}
		return out;
	}
}
