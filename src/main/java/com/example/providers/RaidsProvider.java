package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.ChatMessageType;
import net.runelite.api.Client;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.ChatMessage;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginManager;
import net.runelite.client.plugins.raids.Raid;
import net.runelite.client.plugins.raids.RaidRoom;
import net.runelite.client.plugins.raids.RaidsPlugin;
import net.runelite.client.plugins.raids.RoomType;
import net.runelite.client.plugins.raids.events.RaidReset;
import net.runelite.client.plugins.raids.events.RaidScouted;
import net.runelite.client.plugins.raids.solver.Layout;
import net.runelite.client.plugins.raids.solver.Room;
import net.runelite.client.util.Text;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Mirrors RuneLite's own BUILT-IN Chambers of Xeric raids plugin onto GET /raid, for
 * routines/raid_scouter.py. See docs/superpowers/specs/Raid-scouter.md for the full design and
 * the decompiled-source evidence every field below is read from
 * (data/decompiled/client-1.12.33/net/runelite/client/plugins/raids/).
 *
 * Deliberately consumes RaidsPlugin's own layout solving rather than reimplementing it -- the
 * 231.6 dialogue-widget lesson in CLAUDE.md: read what already works instead of reconstructing
 * it from a nearer source. RaidsPlugin, Raid, RaidRoom, RoomType, Layout and Room are all public
 * classes on our compile classpath (net.runelite:client -- RaidsPlugin ships IN that jar as a
 * core plugin, not a separate dependency), so this needs no reflection and no vendored package,
 * unlike CombatProvider's metronome lookup (that one really is an external Plugin Hub jar).
 *
 * RaidsPlugin.getRaid()/isInRaidChambers()/getRaidPartyID() are all public, so this provider
 * does not re-derive varbit 5432 / varp 1427 itself -- it just reads RaidsPlugin's own answer.
 * The one signal RaidsPlugin does NOT expose a getter for is whether the raid has actually
 * STARTED (it only spins up an internal RaidsTimer), so that alone is latched here off the
 * "The raid has begun!" FRIENDSCHATNOTIFICATION, self-correcting on the party id -- see
 * raidStarted below.
 */
public class RaidsProvider extends BridgeProvider
{
	// Confirmed live in RaidsPlugin.onChatMessage: FRIENDSCHATNOTIFICATION, message prefix
	// exactly "The raid has begun!". NOT yet seen in this project's own recording (8 minutes,
	// solo, raid never started) -- see the design doc's "Not covered by this recording" section.
	private static final String RAID_START_MESSAGE = "The raid has begun!";

	// RaidsPlugin.submitRaid reads this as client.getVarbitValue(5431) for "Total points:".
	// RaidsPlugin keeps no public getter for it, so read directly -- same varbit id, confirmed
	// against the decompiled source, not guessed.
	private static final int VARBIT_TOTAL_POINTS = 5431;

	private final EventBus eventBus;
	private final PluginManager pluginManager;

	// Found once and cached forever after -- RaidsPlugin is a core plugin, instantiated once at
	// client boot regardless of whether the user has it enabled (enabled/disabled only starts or
	// stops it, see PluginManager.isPluginActive). Volatile: read from the HTTP handler thread,
	// written from whichever thread first resolves it (also HTTP handler thread, via onClient).
	private volatile RaidsPlugin raidsPlugin;

	// Incremented once per RaidScouted event. This is the field routines/raid_scouter.py keys
	// its "have I already judged this instance" check on -- see the design doc's scoutId note:
	// a poll landing between a reroll's confirm and the next RaidScouted can otherwise read the
	// PREVIOUS instance's still-cached layout as if it were the new one (the same class of bug
	// travel.py's Shortest Path result-echo check exists to catch).
	private final AtomicInteger scoutId = new AtomicInteger(0);
	private volatile boolean lastFirstScout = false;

	// raidStarted is latched true by the chat line and self-corrects: it only reads as true for
	// the SAME party id it was set under. A reroll always changes the party id (-1, then a new
	// value), so a stale "started" flag from a previous instance goes false again with no extra
	// event wiring needed -- no onRaidReset handling required for this specifically.
	private volatile boolean raidStarted = false;
	private volatile int startedForPartyId = Integer.MIN_VALUE;

	public RaidsProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
		EventBus eventBus, PluginManager pluginManager)
	{
		super(client, clientThread, server);
		this.eventBus = eventBus;
		this.pluginManager = pluginManager;
	}

	@Override
	public void register()
	{
		server.register("/raid", () -> onClient(this::getState));
		eventBus.register(this);
	}

	@Subscribe
	public void onRaidScouted(RaidScouted event)
	{
		lastFirstScout = event.isFirstScout();
		scoutId.incrementAndGet();
	}

	@Subscribe
	public void onRaidReset(RaidReset event)
	{
		// No action needed: RaidsPlugin.getRaid() itself goes null on reset (Raid.reset()), which
		// getState() already reports as scouted=false. scoutId does not need to move here -- the
		// next RaidScouted after re-entry increments it same as always, and that increment is what
		// the routine actually keys on.
	}

	@Subscribe
	public void onChatMessage(ChatMessage event)
	{
		if (event.getType() != ChatMessageType.FRIENDSCHATNOTIFICATION)
		{
			return;
		}
		String message = Text.removeTags(event.getMessage());
		if (message.startsWith(RAID_START_MESSAGE))
		{
			RaidsPlugin plugin = findRaidsPlugin();
			raidStarted = true;
			startedForPartyId = plugin != null ? plugin.getRaidPartyID() : Integer.MIN_VALUE;
		}
	}

	private RaidsPlugin findRaidsPlugin()
	{
		RaidsPlugin cached = raidsPlugin;
		if (cached != null)
		{
			return cached;
		}
		for (Plugin plugin : pluginManager.getPlugins())
		{
			if (plugin instanceof RaidsPlugin)
			{
				raidsPlugin = (RaidsPlugin) plugin;
				return raidsPlugin;
			}
		}
		return null;
	}

	private Object getState()
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("world", client.getWorld());

		RaidsPlugin plugin = findRaidsPlugin();
		if (plugin == null)
		{
			// The built-in plugin has not even been instantiated by RuneLite's own plugin loader
			// yet (very early poll during client boot) -- distinct from "found but disabled",
			// but the routine treats both the same way: nothing to scout, not our failure to fix.
			putEmpty(data, false);
			return data;
		}

		boolean active = pluginManager.isPluginActive(plugin);
		data.put("raidsPluginEnabled", active);

		boolean inRaid = plugin.isInRaidChambers();
		int partyId = plugin.getRaidPartyID();
		data.put("inRaid", inRaid);
		data.put("raidPartyId", partyId);
		data.put("raidStarted", raidStarted && startedForPartyId == partyId);
		data.put("totalPoints", client.getVarbitValue(VARBIT_TOTAL_POINTS));
		data.put("scoutId", scoutId.get());
		data.put("firstScout", lastFirstScout);

		Raid raid = plugin.getRaid();
		if (raid == null || raid.getLayout() == null)
		{
			data.put("scouted", false);
			data.put("gridBase", null);
			data.put("layoutCode", null);
			data.put("rooms", Collections.emptyList());
			data.put("allRooms", Collections.emptyList());
			data.put("unknownRooms", 0);
			return data;
		}

		List<RaidRoom> ordered = orderedRooms(raid);
		data.put("scouted", true);
		data.put("gridBase", pointMap(raid.getGridBase()));
		data.put("layoutCode", raid.getLayout().toCodeString());
		data.put("rooms", combatAndPuzzleNames(ordered));
		data.put("allRooms", allNames(ordered));
		data.put("unknownRooms", countUnknown(ordered));

		return data;
	}

	private static void putEmpty(Map<String, Object> data, boolean raidsPluginEnabled)
	{
		data.put("raidsPluginEnabled", raidsPluginEnabled);
		data.put("inRaid", false);
		data.put("raidPartyId", -1);
		data.put("raidStarted", false);
		data.put("totalPoints", 0);
		data.put("scoutId", 0);
		data.put("firstScout", false);
		data.put("scouted", false);
		data.put("gridBase", null);
		data.put("layoutCode", null);
		data.put("rooms", Collections.emptyList());
		data.put("allRooms", Collections.emptyList());
		data.put("unknownRooms", 0);
	}

	/**
	 * The raid's 16 room slots, in PATH order (Raid.getOrderedRooms() is package-private in
	 * net.runelite.client.plugins.raids, so this reproduces it via the public API: walk the
	 * layout's Room list -- already in traversal order, confirmed against Raid.getOrderedRooms's
	 * own implementation in the decompiled source -- and look each position up on the raid).
	 *
	 * Requires raid.getLayout() != null (checked by the caller); once a layout is assigned,
	 * Raid.updateLayout fills every position with either a solved room or an UNKNOWN_COMBAT/
	 * UNKNOWN_PUZZLE placeholder, so every entry here is non-null.
	 *
	 * Untestable from a plain unit test without reflection: Raid has no public layout setter
	 * (updateLayout is package-private), so a test cannot construct a Raid with a real Layout
	 * from this package. combatAndPuzzleNames/countUnknown below are the pure, testable half of
	 * this logic -- they take the resulting List<RaidRoom> directly.
	 */
	static List<RaidRoom> orderedRooms(Raid raid)
	{
		List<RaidRoom> ordered = new ArrayList<>();
		for (Room r : raid.getLayout().getRooms())
		{
			RaidRoom room = raid.getRoom(r.getPosition());
			if (room != null)
			{
				ordered.add(room);
			}
		}
		return ordered;
	}

	/** All room names in path order, UNKNOWN placeholders included -- the "allRooms" field. */
	static List<String> allNames(List<RaidRoom> ordered)
	{
		List<String> names = new ArrayList<>(ordered.size());
		for (RaidRoom room : ordered)
		{
			names.add(room.getName());
		}
		return names;
	}

	/**
	 * Path-ordered names filtered to COMBAT | PUZZLE -- the "rooms" field the match rule in
	 * docs/superpowers/specs/Raid-scouter.md is stated over. Scavengers/Farming/Start/End/Empty
	 * are dropped. Includes UNKNOWN_COMBAT/UNKNOWN_PUZZLE by name (their RaidRoom.getName() is
	 * "Unknown (combat)"/"Unknown (puzzle)") -- the caller uses unknownRooms, not this list, to
	 * decide whether the layout is fully resolved.
	 */
	static List<String> combatAndPuzzleNames(List<RaidRoom> ordered)
	{
		List<String> names = new ArrayList<>();
		for (RaidRoom room : ordered)
		{
			RoomType type = room.getType();
			if (type == RoomType.COMBAT || type == RoomType.PUZZLE)
			{
				names.add(room.getName());
			}
		}
		return names;
	}

	/**
	 * Count of rooms the rotation solver could not resolve (outside the loaded scene, and not
	 * inferable from the four known combat rotations). Load-bearing: routines/raid_scouter.py
	 * treats unknownRooms > 0 as "not scouted yet" and abstains rather than rejecting -- judging
	 * a partial layout would throw away a good instance.
	 */
	static int countUnknown(List<RaidRoom> ordered)
	{
		int count = 0;
		for (RaidRoom room : ordered)
		{
			if (room == RaidRoom.UNKNOWN_COMBAT || room == RaidRoom.UNKNOWN_PUZZLE)
			{
				count++;
			}
		}
		return count;
	}

	private static Map<String, Object> pointMap(WorldPoint p)
	{
		if (p == null)
		{
			return null;
		}
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("x", p.getX());
		m.put("y", p.getY());
		m.put("plane", p.getPlane());
		return m;
	}
}
