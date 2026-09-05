package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.InstanceCoords;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.HeadIcon;
import net.runelite.api.NPC;
import net.runelite.api.Player;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;

import java.util.*;
import java.util.stream.Collectors;

public class NPCProvider extends BridgeProvider
{
	// See BankProvider's identical comment for why this is cached, and TickCache/TickClock for
	// how.
	private final TickClock tickClock;
	private final TickCache<List<Map<String, Object>>> cache = new TickCache<>();

	public NPCProvider(Client client, ClientThread clientThread, BridgeHttpServer server, TickClock tickClock)
	{
		super(client, clientThread, server);
		this.tickClock = tickClock;
	}

	@Override
	public void register()
	{
		server.registerQuery("/npcs", query -> getNPCs(isFresh(query)));
		server.registerQuery("/npc/nearest", query -> onClient(() -> getNearestNpc(query)));
		server.registerQuery("/npc/by-index", query -> onClient(() -> getNpcByIndex(query)));
		server.register("/npc-types", () -> onClient(this::getNpcTypes));
	}

	private Object getNPCs(boolean fresh)
	{
		return cache.get(tickClock.get(), fresh, null, () -> (List<Map<String, Object>>) onClient(this::computeNpcs));
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
				putViewerMotion(data, npc, local);
				return data;
			}
		}

		return notFound;
	}

	/**
	 * Every motion source that shifts this NPC's screen projection, for
	 * controller.py's npc_index_refresh_target hold key (added 2026-08-27). Tile-only
	 * deadbanding held a stale aim point whenever the PLAYER was the thing moving -- the NPC's
	 * tile never changes while our own walk slides its projection continuously, which is
	 * exactly "clicks land behind walkers". LocalPoints are the client's per-frame interpolated
	 * positions (128 units/tile), so sub-tile motion is visible here while a fully still scene
	 * reads identically poll after poll and lets the hold suppress hull-animation jitter.
	 * By-index only, deliberately not in buildNpcData -- the bulk /npcs list is TickCache-gated
	 * and per-row viewer state there would just be stale duplication.
	 */
	private void putViewerMotion(Map<String, Object> data, NPC npc, Player local)
	{
		LocalPoint npcLocal = npc.getLocalLocation();
		if (npcLocal != null)
		{
			data.put("npcLocalX", npcLocal.getX());
			data.put("npcLocalY", npcLocal.getY());
		}
		LocalPoint playerLocal = local != null ? local.getLocalLocation() : null;
		if (playerLocal != null)
		{
			data.put("playerLocalX", playerLocal.getX());
			data.put("playerLocalY", playerLocal.getY());
		}
		data.put("cameraYaw", client.getCameraYaw());
		data.put("cameraPitch", client.getCameraPitch());
		data.put("cameraScale", client.getScale());
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

		// npc.getWorldLocation() alone is NOT instance-aware -- inside any instanced area (a
		// minigame, a dungeon, a boss room) it's a raw instance-template coordinate, not the real
		// reachable map location a routine's own hardcoded tiles/distance math is written
		// against. Same bug, same fix as PlayerProvider's regionId addition (see its own
		// comment, CONFIRMED live 2026-08-14 via a Pest Control routine reporting every NPC's x/y
		// in the ~13000s instead of the real ~2600s map coordinates once /player itself was
		// fixed but this wasn't yet).
		LocalPoint npcLocal = npc.getLocalLocation();
		WorldPoint wp = npcLocal != null ? WorldPoint.fromLocalInstance(client, npcLocal) : npc.getWorldLocation();
		int plane = client.getPlane();
		boolean alive = !RuneLiteUtil.isNpcDead(npc);

		data.put("id", npc.getId());
		data.put("index", npc.getIndex());
		data.put("name", npc.getName());
		data.put("x", wp.getX());
		data.put("y", wp.getY());
		data.put("plane", wp.getPlane());
		// Instance-space twin of x/y (2026-09-05, InstanceCoords). Equal outside an instance.
		WorldPoint inst = npcLocal != null && npcLocal.getWorldView() <= WorldView.TOPLEVEL
			? InstanceCoords.instancePoint(client, npcLocal, plane)
			: wp;
		data.put("instX", inst.getX());
		data.put("instY", inst.getY());
		// Overhead protection prayer (2026-09-05, Gauntlet sub-project 1). The Hunllef cycles
		// its protection prayer and a hit with the protected style does nothing, so the fight
		// routine needs this. ZombieLite reads the same short[] by reflection into the injected
		// client and indexes HeadIcon.values() with element 0 (NpcsHook.getOldHeadIcon); the
		// public getOverheadSpriteIds() has exposed it since 1.12.x. Whether the element is a
		// HeadIcon ORDINAL or a sprite id is settled live against the Hunllef -- the raw value
		// is reported alongside so a mismatch is diagnosable instead of reading as "no prayer".
		short[] overhead = npc.getOverheadSpriteIds();
		int overheadId = overhead != null && overhead.length > 0 ? overhead[0] : -1;
		data.put("overheadPrayerId", overheadId);
		HeadIcon[] icons = HeadIcon.values();
		data.put("overheadPrayer", overheadId >= 0 && overheadId < icons.length
			? icons[overheadId].name()
			: null);
		// Aim at a point inside the npc's actual on-screen model (Actor.getConvexHull()), not at
		// the ground-tile projection under its feet -- the same correction ObjectProvider has made
		// via TileObject.getClickbox() since 2026-08-01. Added 2026-08-24 after bench_hover.py
		// showed /npc/by-index reporting a tile-centre point: harmless for a short, squat npc like
		// a Dashing kebbit (feet and body nearly coincide) but increasingly wrong for a tall model
		// or one standing on a raised/sloped tile. Falls back to the previous behavior whenever no
		// hull is available (model not loaded, off-screen), so nothing regresses when it isn't.
		//
		// Deliberately in the SHARED builder, so /npcs, /npc/nearest and /npc/by-index all agree.
		// They must: a routine picks a target from the bulk list and then re-aims through
		// npc_index_refresh_target -> /npc/by-index mid-flight, and if the two disagreed about
		// where the npc is, every tracked click would jump between two different points.
		data.putAll(RuneLiteUtil.shapeCoords(client, npc.getConvexHull(), npc.getLocalLocation(), plane, alive));
		data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
		data.put("hpRatio", npc.getHealthRatio());
		data.put("hpScale", npc.getHealthScale());
		data.put("interactingPlayer", local != null && npc.getInteracting() == local);
		data.put("inCombat", npc.getInteracting() != null);
		data.put("attackable", RuneLiteUtil.isNpcAttackable(npc));
		// Additive, doesn't replace "attackable" (existing routines already filter on that) --
		// see RuneLiteUtil.hasAttackOption's own doc for why this is a more direct signal.
		data.put("hasAttackOption", RuneLiteUtil.hasAttackOption(npc));
		// NOTE: visible/onScreen/clickable come from shapeCoords above, which resolves them from
		// the hull it actually aimed at. The old separate RuneLiteUtil.visibility(...) call that
		// used to live here was removed with that change -- running it here would silently
		// overwrite the hull-derived values with ground-tile ones, so the endpoint would report
		// clickability for a point it wasn't returning.
		return data;
	}
}
