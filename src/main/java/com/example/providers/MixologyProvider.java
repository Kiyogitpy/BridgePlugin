package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Reads Mastering Mixology's live state directly from RuneLite's own generated VarbitID
 * constants -- confirmed present in this project's runelite-api jar via direct javap inspection,
 * and cross-referenced against the hex-agon/mastering-mixology reference plugin's usage of the
 * same constants (see docs/superpowers/specs/2026-08-12-mastering-mixology-design.md's Research
 * method section). No widget reading, no dependency on that plugin at runtime -- this file
 * reimplements the handful of lookup tables it needs directly.
 */
public class MixologyProvider extends BridgeProvider
{
	private static final int LABS_REGION_ID = 5521;
	private static final int LABS_REGION_PLANE = 0;

	// Declared order matches PotionType's order in the reference plugin, which is also the
	// 0-indexed value the MM_LAB_ORDER_*_TYPE / MM_LAB_*_POTION varbits encode (real varbit
	// values are 1-indexed; index = varbit value - 1).
	private static final String[] POTION_NAMES = {
		"Mammoth-might mix", "Mystic mana amalgam", "Marley's moonlight", "Alco-augmentator",
		"Azure aura mix", "Aqualux amalgam", "Liplack liquor", "Megalite liquid",
		"Anti-leech lotion", "Mixalot",
	};

	// {mox, aga, lye} per POTION_NAMES entry, from the wiki's recipe table.
	private static final int[][] POTION_RECIPES = {
		{3, 0, 0}, {2, 1, 0}, {2, 0, 1}, {0, 3, 0}, {1, 2, 0},
		{0, 2, 1}, {0, 0, 3}, {1, 0, 2}, {0, 1, 2}, {1, 1, 1},
	};

	// Declared order matches PotionModifier's order in the reference plugin: 0=Homogenised
	// (Agitator), 1=Concentrated (Retort), 2=Crystalised (Alembic). Same 1-indexed varbit
	// convention as the potion-type varbits above.
	private static final String[] MODIFIER_STATIONS = {"Agitator", "Retort", "Alembic"};

	public MixologyProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/mixology-state", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("inLab", isInLab());
		data.put("orders", computeOrders());
		data.put("hopper", computeHopper());
		data.put("stations", computeStations());
		data.put("digweed", computeDigweed());
		return data;
	}

	private boolean isInLab()
	{
		Player local = client.getLocalPlayer();
		if (local == null)
		{
			return false;
		}
		// Use getTopLevelWorldLocation() to handle nested WorldViews (e.g. Sailing) correctly.
		// Raw getWorldLocation() returns WorldView-local coordinates which are meaningless outside that view.
		return RuneLiteUtil.getTopLevelWorldLocation(client, local).getRegionID() == LABS_REGION_ID
			&& RuneLiteUtil.getTopLevelWorldLocation(client, local).getPlane() == LABS_REGION_PLANE;
	}

	private List<Map<String, Object>> computeOrders()
	{
		List<Map<String, Object>> orders = new ArrayList<>();
		addOrderIfPresent(orders, VarbitID.MM_LAB_ORDER_1_TYPE, VarbitID.MM_LAB_ORDER_1_MODIFIER);
		addOrderIfPresent(orders, VarbitID.MM_LAB_ORDER_2_TYPE, VarbitID.MM_LAB_ORDER_2_MODIFIER);
		addOrderIfPresent(orders, VarbitID.MM_LAB_ORDER_3_TYPE, VarbitID.MM_LAB_ORDER_3_MODIFIER);
		return orders;
	}

	private void addOrderIfPresent(List<Map<String, Object>> orders, int typeVarbit, int modifierVarbit)
	{
		int typeIdx = client.getVarbitValue(typeVarbit) - 1;
		int modifierIdx = client.getVarbitValue(modifierVarbit) - 1;
		if (typeIdx < 0 || typeIdx >= POTION_NAMES.length || modifierIdx < 0 || modifierIdx >= MODIFIER_STATIONS.length)
		{
			return;
		}

		Map<String, Object> order = new HashMap<>();
		order.put("name", POTION_NAMES[typeIdx]);
		order.put("mox", POTION_RECIPES[typeIdx][0]);
		order.put("aga", POTION_RECIPES[typeIdx][1]);
		order.put("lye", POTION_RECIPES[typeIdx][2]);
		order.put("station", MODIFIER_STATIONS[modifierIdx]);
		orders.add(order);
	}

	private Map<String, Object> computeHopper()
	{
		Map<String, Object> hopper = new HashMap<>();
		// MM_AVAILABLE_{MOX,AGA,LYE} are lower-confidence varbits per the design doc's Global Constraints.
		// Live verification needed during implementation.
		hopper.put("mox", client.getVarbitValue(VarbitID.MM_AVAILABLE_MOX));
		hopper.put("aga", client.getVarbitValue(VarbitID.MM_AVAILABLE_AGA));
		hopper.put("lye", client.getVarbitValue(VarbitID.MM_AVAILABLE_LYE));
		return hopper;
	}

	private Map<String, Object> computeStations()
	{
		Map<String, Object> stations = new HashMap<>();
		stations.put("retort", buildStation(VarbitID.MM_LAB_RETORT_POTION, VarbitID.MM_RETORT_PROGRESS));
		stations.put("agitator", buildStation(VarbitID.MM_LAB_AGITATOR_POTION, VarbitID.MM_AGITATOR_PROGRESS));
		stations.put("alembic", buildStation(VarbitID.MM_LAB_ALEMBIC_POTION, VarbitID.MM_ALEMBIC_PROGRESS));
		return stations;
	}

	private Map<String, Object> buildStation(int potionVarbit, int progressVarbit)
	{
		Map<String, Object> station = new HashMap<>();
		int typeIdx = client.getVarbitValue(potionVarbit) - 1;
		station.put("potionName", (typeIdx >= 0 && typeIdx < POTION_NAMES.length) ? POTION_NAMES[typeIdx] : null);
		station.put("progress", client.getVarbitValue(progressVarbit));
		return station;
	}

	private Map<String, Object> computeDigweed()
	{
		Map<String, Object> digweed = new HashMap<>();
		digweed.put("northEast", client.getVarbitValue(VarbitID.MM_HERB_READY_1) != 0);
		digweed.put("southEast", client.getVarbitValue(VarbitID.MM_HERB_READY_2) != 0);
		digweed.put("southWest", client.getVarbitValue(VarbitID.MM_HERB_READY_3) != 0);
		digweed.put("northWest", client.getVarbitValue(VarbitID.MM_HERB_READY_4) != 0);
		return digweed;
	}
}
