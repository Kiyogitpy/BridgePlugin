package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.ChatMessageType;
import net.runelite.api.Client;
import net.runelite.api.coords.WorldArea;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.ChatMessage;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.util.Text;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Mirrors the currently-assigned Mahogany Homes contract onto GET /mahogany-homes. Ports the
 * static contract data (which furniture hotspot lives at which fixed object id, per homeowner)
 * from the decompiled reference plugin (thestonedturtle.mahoganyhomes, sideloaded Plugin Hub jar
 * -- NOT a runtime dependency, documentation only, see data/decompiled/mahogany-homes-plugin/).
 *
 * Deliberately does NOT port that plugin's dialogue-widget polling for detecting the assigned
 * home -- the single assignment chat message ("Adept Contract: Go see Jeff in the middle of
 * Varrock, west of the museum.") already carries both the tier and the home's name in one
 * line, confirmed against four hand-recorded play sessions
 * (ControllerAddon/recordings/2026-08-27_*mahogny-homes*.log). Simpler and needs no widget id.
 *
 * Deliberately NOT TickCache-gated (same reasoning as GolemProvider): routines/mahogany_homes.py
 * re-reads the whole `hotspots` list on every poll and picks the first slot still flagged
 * needsAttention, so a stale read would reintroduce exactly the lag the varbits are here to
 * remove (it would keep re-clicking a hotspot it had already finished).
 */
public class MahoganyHomesProvider extends BridgeProvider
{
	private static final int FIRST_HOTSPOT_VARBIT = 10554;

	private static final Pattern CONTRACT_ASSIGNED =
		Pattern.compile("(\\w+) Contract: Go see (\\w+)[, .].*", Pattern.CASE_INSENSITIVE);

	// Ported verbatim from the reference plugin's own CONTRACT_FINISHED pattern.
	private static final Pattern CONTRACT_FINISHED =
		Pattern.compile("You have completed [\\d,]+ contracts with a total of [\\d,]+ points?\\.");

	// Ported from the reference plugin's REMINDER_PATTERN. The game re-states the active contract
	// on request ("Talk-to" a Mahogany Homes NPC / the Estate agent), which is the ONLY way to
	// recover currentHome/contractTier after a client restart mid-contract -- the assignment
	// message is long gone by then and nothing else on the wire carries the home's name.
	private static final Pattern CONTRACT_REMINDER =
		Pattern.compile("You're currently on an? (\\w+) Contract\\. Go see (\\w+)[ ,][\\w\\s,-]*\\. You can get another job once you have furnished \\w+ home\\.", Pattern.CASE_INSENSITIVE);

	private final EventBus eventBus;

	private volatile Home currentHome = null;
	private volatile int contractTier = 0;

	public MahoganyHomesProvider(Client client, ClientThread clientThread, BridgeHttpServer server, EventBus eventBus)
	{
		super(client, clientThread, server);
		this.eventBus = eventBus;
	}

	@Override
	public void register()
	{
		server.register("/mahogany-homes", () -> onClient(this::getState));
		eventBus.register(this);
	}

	@Subscribe
	public void onChatMessage(ChatMessage event)
	{
		if (event.getType() != ChatMessageType.GAMEMESSAGE)
		{
			return;
		}
		String message = Text.removeTags(event.getMessage());

		if (isCompletionMessage(message))
		{
			currentHome = null;
			contractTier = 0;
			return;
		}

		ParsedAssignment parsed = parseAssignment(message);
		if (parsed == null)
		{
			parsed = parseReminder(message);
		}
		if (parsed != null)
		{
			Home home = Home.byDisplayName(parsed.homeDisplayName);
			if (home != null)
			{
				currentHome = home;
				contractTier = parsed.tier;
			}
		}
	}

	/** Package-private (not private) so MahoganyHomesProviderTest can call it directly. */
	static ParsedAssignment parseAssignment(String message)
	{
		return parseWith(CONTRACT_ASSIGNED, message);
	}

	/**
	 * The "you're currently on a ... Contract" reminder line. Same two capture groups (tier,
	 * home display name) as an assignment, so it feeds currentHome/contractTier identically --
	 * this is the mid-contract restart recovery path. Package-private for the test.
	 */
	static ParsedAssignment parseReminder(String message)
	{
		return parseWith(CONTRACT_REMINDER, message);
	}

	private static ParsedAssignment parseWith(Pattern pattern, String message)
	{
		Matcher matcher = pattern.matcher(message);
		if (!matcher.matches())
		{
			return null;
		}
		int tier = tierFromString(matcher.group(1));
		if (tier == 0)
		{
			return null;
		}
		return new ParsedAssignment(tier, matcher.group(2));
	}

	static boolean isCompletionMessage(String message)
	{
		return CONTRACT_FINISHED.matcher(message).matches();
	}

	private static int tierFromString(String tier)
	{
		switch (tier.toLowerCase())
		{
			case "beginner":
				return 1;
			case "novice":
				return 2;
			case "adept":
				return 3;
			case "expert":
				return 4;
			default:
				return 0;
		}
	}

	private Object getState()
	{
		Map<String, Object> data = new LinkedHashMap<>();
		Home home = currentHome;
		if (home == null)
		{
			data.put("hasContract", false);
			data.put("homeName", null);
			data.put("npcName", null);
			data.put("contractTier", 0);
			return data;
		}

		data.put("hasContract", true);
		data.put("homeName", home.name());
		data.put("npcName", home.displayName);
		data.put("contractTier", contractTier);
		data.put("npcId", home.npcId);
		data.put("standingLocation", pointMap(home.standingLocation));
		data.put("area", areaMap(home.area));
		data.put("ladderObjectIds", home.ladders);

		List<Map<String, Object>> hotspots = new ArrayList<>();
		int remaining = 0;
		int planks = 0;
		int steelBars = 0;
		for (int i = 0; i < home.slotObjectIds.length; i++)
		{
			int varb = FIRST_HOTSPOT_VARBIT + i;
			int value = client.getVarbitValue(varb);
			// Ported verbatim from the reference plugin's doesHotspotRequireAttention.
			boolean needsAttention = value == 1 || value == 3 || value == 4;
			HotspotType type = home.slotTypes[i];

			Map<String, Object> slot = new LinkedHashMap<>();
			slot.put("varb", varb);
			slot.put("objectId", home.slotObjectIds[i]);
			slot.put("value", value);
			slot.put("needsAttention", needsAttention);
			// Per-slot cost, so the routine can ask "can I afford THIS hotspot" instead of only
			// "can I afford the whole rest of the contract" (requiredMaterials below) -- being 2
			// planks short of the total must not stop the 90% of the contract already affordable.
			slot.put("material", type.material.name());
			slot.put("quantity", type.count);
			hotspots.add(slot);

			if (needsAttention)
			{
				remaining++;
				if (type.material == MaterialType.PLANK)
				{
					planks += type.count;
				}
				else
				{
					steelBars += type.count;
				}
			}
		}
		data.put("hotspots", hotspots);
		data.put("remainingCount", remaining);
		data.put("totalSlots", home.slotObjectIds.length);

		Map<String, Object> materials = new LinkedHashMap<>();
		materials.put("planks", planks);
		materials.put("steelBars", steelBars);
		data.put("requiredMaterials", materials);

		return data;
	}

	private static Map<String, Object> pointMap(WorldPoint p)
	{
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("x", p.getX());
		m.put("y", p.getY());
		m.put("plane", p.getPlane());
		return m;
	}

	private static Map<String, Object> areaMap(WorldArea a)
	{
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("x", a.getX());
		m.put("y", a.getY());
		m.put("width", a.getWidth());
		m.put("height", a.getHeight());
		m.put("plane", a.getPlane());
		return m;
	}

	static final class ParsedAssignment
	{
		final int tier;
		final String homeDisplayName;

		ParsedAssignment(int tier, String homeDisplayName)
		{
			this.tier = tier;
			this.homeDisplayName = homeDisplayName;
		}
	}

	private enum MaterialType
	{
		PLANK, STEEL_BAR
	}

	/** Ported verbatim from the reference plugin's HotspotObjects.HotspotType. */
	private enum HotspotType
	{
		B1(MaterialType.PLANK, 1),
		B2(MaterialType.PLANK, 2),
		B3(MaterialType.PLANK, 3),
		B4(MaterialType.PLANK, 4),
		RP(MaterialType.PLANK, 1),
		SB(MaterialType.STEEL_BAR, 1);

		final MaterialType material;
		final int count;

		HotspotType(MaterialType material, int count)
		{
			this.material = material;
			this.count = count;
		}
	}

	/**
	 * Ported from the reference plugin's Home enum (area/hint/npcId/location/ladders) and
	 * HotspotObjects enum (one fixed object id + material type per slot, confirmed stable
	 * across a slot's whole Remove-then-Build lifecycle -- see this class's own header comment
	 * and the design doc's "Researched facts" section for the recording evidence). Every
	 * WorldArea/WorldPoint/object id below is copied from that decompile, not guessed.
	 */
	private enum Home
	{
		JESS(new WorldArea(2611, 3290, 14, 7, 0), "Jess", 10421, new WorldPoint(2621, 3292, 0),
			new int[]{17026, 16685},
			new int[]{40171, 40172, 40173, 40174, 40175, 40176, 40177, 40299},
			new HotspotType[]{HotspotType.B2, HotspotType.B2, HotspotType.B2, HotspotType.B2,
				HotspotType.B3, HotspotType.B3, HotspotType.RP, HotspotType.SB}),
		NOELLA(new WorldArea(2652, 3317, 15, 8, 0), "Noella", 10419, new WorldPoint(2659, 3322, 0),
			new int[]{17026, 16685, 15645, 15648},
			new int[]{40156, 40157, 40158, 40159, 40160, 40161, 40162, 40163},
			new HotspotType[]{HotspotType.B2, HotspotType.B2, HotspotType.RP, HotspotType.RP,
				HotspotType.B2, HotspotType.B3, HotspotType.B3, HotspotType.RP}),
		ROSS(new WorldArea(2609, 3313, 11, 9, 0), "Ross", 10420, new WorldPoint(2613, 3316, 0),
			new int[]{16683, 16679},
			new int[]{40164, 40165, 40166, 40167, 40168, 40169, 40170},
			new HotspotType[]{HotspotType.SB, HotspotType.B2, HotspotType.B2, HotspotType.B3,
				HotspotType.RP, HotspotType.B2, HotspotType.RP}),
		LARRY(new WorldArea(3033, 3360, 10, 9, 0), "Larry", 10418, new WorldPoint(3038, 3364, 0),
			new int[]{24075, 24076},
			new int[]{40297, 40095, 40096, 40097, 40298, 40098, 40099},
			new HotspotType[]{HotspotType.SB, HotspotType.B2, HotspotType.B2, HotspotType.B3,
				HotspotType.RP, HotspotType.B3, HotspotType.RP}),
		NORMAN(new WorldArea(3034, 3341, 8, 8, 0), "Norman", 3266, new WorldPoint(3038, 3344, 0),
			new int[]{24082, 24085},
			new int[]{40296, 40089, 40090, 40091, 40092, 40093, 40094},
			new HotspotType[]{HotspotType.SB, HotspotType.RP, HotspotType.B3, HotspotType.B3,
				HotspotType.B2, HotspotType.B2, HotspotType.B2}),
		TAU(new WorldArea(3043, 3340, 10, 11, 0), "Tau", 10417, new WorldPoint(3047, 3345, 0),
			new int[]{},
			new int[]{40083, 40084, 40085, 40086, 40087, 40088, 40295},
			new HotspotType[]{HotspotType.SB, HotspotType.B3, HotspotType.B3, HotspotType.B2,
				HotspotType.B2, HotspotType.B2, HotspotType.RP}),
		BARBARA(new WorldArea(1746, 3531, 10, 11, 0), "Barbara", 10424, new WorldPoint(1750, 3534, 0),
			new int[]{},
			new int[]{40011, 40293, 40012, 40294, 40013, 40014, 40015},
			new HotspotType[]{HotspotType.RP, HotspotType.SB, HotspotType.B3, HotspotType.B2,
				HotspotType.B2, HotspotType.B1, HotspotType.B1}),
		LEELA(new WorldArea(1781, 3589, 9, 8, 0), "Leela", 10423, new WorldPoint(1785, 3592, 0),
			new int[]{11794, 11802},
			new int[]{40007, 40008, 40290, 40291, 40009, 40010, 40292},
			new HotspotType[]{HotspotType.B2, HotspotType.B2, HotspotType.SB, HotspotType.B3,
				HotspotType.B3, HotspotType.RP, HotspotType.B2}),
		MARIAH(new WorldArea(1762, 3618, 10, 7, 0), "Mariah", 10422, new WorldPoint(1766, 3621, 0),
			new int[]{11794, 11802},
			new int[]{40002, 40287, 40003, 40288, 40004, 40005, 40006, 40289},
			new HotspotType[]{HotspotType.B3, HotspotType.SB, HotspotType.B2, HotspotType.B2,
				HotspotType.B2, HotspotType.B2, HotspotType.B2, HotspotType.RP}),
		BOB(new WorldArea(3234, 3482, 10, 10, 0), "Bob", 10414, new WorldPoint(3238, 3486, 0),
			new int[]{11797, 11799},
			new int[]{39981, 39982, 39983, 39984, 39985, 39986, 39987, 39988},
			new HotspotType[]{HotspotType.B4, HotspotType.RP, HotspotType.B2, HotspotType.B2,
				HotspotType.B2, HotspotType.B2, HotspotType.B2, HotspotType.B2}),
		JEFF(new WorldArea(3235, 3445, 10, 12, 0), "Jeff", 10415, new WorldPoint(3239, 3450, 0),
			new int[]{11789, 11793},
			new int[]{39989, 39990, 39991, 39992, 39993, 39994, 39995, 39996},
			new HotspotType[]{HotspotType.B3, HotspotType.B2, HotspotType.B2, HotspotType.B3,
				HotspotType.B2, HotspotType.B2, HotspotType.RP, HotspotType.B1}),
		SARAH(new WorldArea(3232, 3381, 8, 7, 0), "Sarah", 10416, new WorldPoint(3235, 3384, 0),
			new int[]{},
			new int[]{39997, 39998, 39999, 40000, 40286, 40001},
			new HotspotType[]{HotspotType.B3, HotspotType.B2, HotspotType.B2, HotspotType.B2,
				HotspotType.SB, HotspotType.B2});

		final WorldArea area;
		final String displayName;
		final int npcId;
		final WorldPoint standingLocation;
		final int[] ladders;
		final int[] slotObjectIds;
		final HotspotType[] slotTypes;

		Home(WorldArea area, String displayName, int npcId, WorldPoint standingLocation,
			int[] ladders, int[] slotObjectIds, HotspotType[] slotTypes)
		{
			this.area = area;
			this.displayName = displayName;
			this.npcId = npcId;
			this.standingLocation = standingLocation;
			this.ladders = ladders;
			this.slotObjectIds = slotObjectIds;
			this.slotTypes = slotTypes;
		}

		static Home byDisplayName(String name)
		{
			for (Home h : values())
			{
				if (h.displayName.equalsIgnoreCase(name))
				{
					return h;
				}
			}
			return null;
		}
	}
}
