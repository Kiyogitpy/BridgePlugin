package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.api.Player;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.util.*;
import java.util.stream.Collectors;

public class NPCProvider extends BridgeProvider
{
	// Same reasoning as ObjectProvider/GroundItemProvider/TileProvider: building screen-coord/
	// distance/health data for every NPC on screen is real work on the client thread, and a
	// caller polling this faster than the game state actually changes (confirmed: /npcs gets
	// polled multiple times a second) turns into repeated full-list rebuilds -- a real, measurable
	// source of client stutter. NPCs only change once per game tick regardless of poll rate, so
	// caching by tick count adds no staleness while capping the work to once per tick.
	private int cachedTick = -1;
	private List<Map<String, Object>> cachedNpcs;

	public NPCProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/npcs", () -> onClient(this::getNPCs));
		server.registerQuery("/npc/nearest", query -> onClient(() -> getNearestNpc(query)));
		server.registerQuery("/npc/by-index", query -> onClient(() -> getNpcByIndex(query)));
		server.register("/npc-types", () -> onClient(this::getNpcTypes));
	}

	private Object getNPCs()
	{
		int tick = client.getTickCount();
		if (cachedNpcs == null || tick != cachedTick)
		{
			cachedNpcs = computeNpcs();
			cachedTick = tick;
		}
		return cachedNpcs;
	}

	private List<Map<String, Object>> computeNpcs()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		Player local = client.getLocalPlayer();

		for (NPC npc : client.getNpcs())
		{
			if (npc == null)
			{
				continue;
			}
			list.add(buildNpcData(npc, local));
		}
		return list;
	}

	private Object getNearestNpc(Map<String, String> query)
	{
		String name = query.get("name");
		String idStr = query.get("id");

		if ((name == null || name.isEmpty()) && (idStr == null || idStr.isEmpty()))
		{
			return Collections.emptyMap();
		}

		Integer targetId = null;
		if (idStr != null && !idStr.isEmpty())
		{
			try
			{
				targetId = Integer.parseInt(idStr);
			}
			catch (NumberFormatException ignored)
			{
			}
		}

		Player local = client.getLocalPlayer();
		NPC nearest = null;
		int bestDistance = Integer.MAX_VALUE;

		for (NPC npc : client.getNpcs())
		{
			if (npc == null || npc.getName() == null)
			{
				continue;
			}

			if (targetId != null && npc.getId() != targetId)
			{
				continue;
			}

			if (name != null && !name.isEmpty()
				&& !npc.getName().toLowerCase().contains(name.toLowerCase()))
			{
				continue;
			}

			int dist = RuneLiteUtil.distanceToPlayer(client, npc.getWorldLocation());
			if (dist >= 0 && dist < bestDistance)
			{
				bestDistance = dist;
				nearest = npc;
			}
		}

		if (nearest == null)
		{
			Map<String, Object> notFound = new HashMap<>();
			notFound.put("found", false);
			return notFound;
		}

		Map<String, Object> data = buildNpcData(nearest, local);
		data.put("found", true);
		return data;
	}

	/**
	 * Exact lookup by NPC instance (client.getIndex(), stable for that specific monster's
	 * lifetime), not by type -- unlike /npc/nearest?id=, which picks whichever NPC of a given
	 * type happens to be closest. Used to re-check/re-aim at a target already committed to,
	 * where a same-species neighbor must never be substituted.
	 */
	private Object getNpcByIndex(Map<String, String> query)
	{
		Map<String, Object> notFound = new HashMap<>();
		notFound.put("found", false);

		String indexStr = query.get("index");
		if (indexStr == null || indexStr.isEmpty())
		{
			return notFound;
		}

		int index;
		try
		{
			index = Integer.parseInt(indexStr);
		}
		catch (NumberFormatException e)
		{
			return notFound;
		}

		Player local = client.getLocalPlayer();
		for (NPC npc : client.getNpcs())
		{
			if (npc != null && npc.getIndex() == index)
			{
				Map<String, Object> data = buildNpcData(npc, local);
				data.put("found", true);
				return data;
			}
		}

		return notFound;
	}

	private Object getNpcTypes()
	{
		Map<Integer, String> types = new LinkedHashMap<>();

		for (NPC npc : client.getNpcs())
		{
			if (npc == null || npc.getName() == null)
			{
				continue;
			}
			types.putIfAbsent(npc.getId(), npc.getName());
		}

		return types.entrySet().stream()
			.sorted(Map.Entry.comparingByValue(String.CASE_INSENSITIVE_ORDER))
			.map(e -> {
				Map<String, Object> entry = new HashMap<>();
				entry.put("id", e.getKey());
				entry.put("name", e.getValue());
				return entry;
			})
			.collect(Collectors.toList());
	}

	private Map<String, Object> buildNpcData(NPC npc, Player local)
	{
		Map<String, Object> data = new HashMap<>();
		WorldPoint wp = npc.getWorldLocation();
		int plane = client.getPlane();
		boolean alive = !RuneLiteUtil.isNpcDead(npc);

		data.put("id", npc.getId());
		data.put("index", npc.getIndex());
		data.put("name", npc.getName());
		data.put("x", wp.getX());
		data.put("y", wp.getY());
		data.put("plane", wp.getPlane());
		data.putAll(RuneLiteUtil.screenCoords(client, npc.getLocalLocation(), plane));
		data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
		data.put("hpRatio", npc.getHealthRatio());
		data.put("hpScale", npc.getHealthScale());
		data.put("interactingPlayer", local != null && npc.getInteracting() == local);
		data.put("inCombat", npc.getInteracting() != null);
		data.put("attackable", RuneLiteUtil.isNpcAttackable(npc));
		data.putAll(RuneLiteUtil.visibility(client, npc.getLocalLocation(), plane, alive));
		return data;
	}
}
