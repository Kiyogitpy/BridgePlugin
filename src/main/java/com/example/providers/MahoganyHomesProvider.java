package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.ChatMessageType;
import net.runelite.api.Client;
import net.runelite.api.Constants;
import net.runelite.api.GameObject;
import net.runelite.api.Scene;
import net.runelite.api.Tile;
import net.runelite.api.TileObject;
import net.runelite.api.WallObject;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldArea;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.ChatMessage;
import net.runelite.api.events.GameTick;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.util.Text;

import java.util.ArrayList;
import java.util.HashMap;
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

	// The NPC dialogue text box, group 231 child 6 -- the reference plugin's own 15138822.
	private static final int NPC_DIALOG_GROUP = 231;
	private static final int NPC_DIALOG_CHILD = 6;

	// Ported verbatim from the reference plugin's CONTRACT_PATTERN. This is the ASSIGNMENT as the
	// homeowner-finder SPEAKS it, which is a different sentence from the "<Tier> Contract: Go see
	// X" game message, and the only place the home's name appears when a contract is re-stated.
	private static final Pattern DIALOG_CONTRACT =
		Pattern.compile("(Please could you g|G)o see (\\w+)[ ,][\\w\\s,-]*[?.] You can get another job once you have furnished \\w+ home\\.", Pattern.CASE_INSENSITIVE);

	private static final char NBSP = ' ';

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

	// Diagnostic only, surfaced on GET /mahogany-homes as "lastChat". Added 2026-08-28 after
	// three wrong hypotheses about why an in-progress contract would not register: it makes the
	// boundary between "the chat line never reached this provider" and "it reached it and the
	// pattern rejected it" directly observable, instead of inferrable. One volatile String.
	private volatile String lastChat = null;

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
		// DIALOG as well as GAMEMESSAGE. CONFIRMED live 2026-08-28 (run 8) against recorder.py's
		// raw records: the ASSIGNMENT line arrives as a GAMEMESSAGE ("Adept Contract: Go see
		// Larry, ...") but the REMINDER line -- the one that recovers an in-progress contract
		// after a client restart -- only ever arrives as
		//   {"type": "DIALOG", "message": "Amy|You're currently on an Adept Contract. Go see ..."}
		// so the old GAMEMESSAGE-only filter meant CONTRACT_REMINDER could never match anything.
		// Symptom: restart mid-contract -> hasContract false -> the routine re-requested the
		// contract it already had, one NPC Contact cast per tick, forever.
		//
		// The reference plugin reads this off the NPC dialogue WIDGET (231.6) on a tick loop
		// instead; the chat event carries the same text and needs no polling, but it does carry
		// the speaker as a "<name>|" prefix, and multi-line dialogue as <br> -- hence
		// sanitizeMultilineText (which turns <br> into a space) rather than a bare removeTags,
		// which would run "furnished his" and "home." together and fail the pattern.
		ChatMessageType type = event.getType();
		if (type != ChatMessageType.GAMEMESSAGE && type != ChatMessageType.DIALOG)
		{
			return;
		}
		String message = normalizeWhitespace(type == ChatMessageType.DIALOG
			? stripDialogSpeaker(Text.sanitizeMultilineText(event.getMessage()))
			: Text.removeTags(event.getMessage()));

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
		lastChat = type.name() + " parsed=" + (parsed != null)
			+ (parsed != null ? " home=" + parsed.homeDisplayName + " tier=" + parsed.tier : "")
			+ " text=" + message;
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

	/**
	 * Reads the active contract straight off the NPC dialogue box, exactly as the reference
	 * plugin does (its checkForAssignmentDialog, called from its own onGameTick).
	 *
	 * WHY THIS AND NOT THE CHAT EVENT: the assignment arrives as a GAMEMESSAGE and parses fine,
	 * but the line that re-states an ALREADY-ACTIVE contract -- the one that recovers state after
	 * a client restart mid-contract -- never did. Chased through three wrong theories on
	 * 2026-08-28 (message type, the "<speaker>|" prefix RuneLite puts on a DIALOG message,
	 * non-breaking spaces) with the routine looping "casting NPC Contact" over a contract it
	 * already held. The reference plugin never touches chat for this, and it works: the dialogue
	 * WIDGET carries the full, untruncated, already-assembled text with no speaker prefix and no
	 * message-type question to get wrong. Read what works rather than reconstructing it.
	 *
	 * Both patterns are tried because the box shows either sentence depending on whether the
	 * contract was just handed out or is being re-stated; only the reminder carries a tier.
	 */
	@Subscribe
	public void onGameTick(GameTick event)
	{
		Widget dialog = client.getWidget(NPC_DIALOG_GROUP, NPC_DIALOG_CHILD);
		if (dialog == null)
		{
			return;
		}
		String text = normalizeWhitespace(Text.sanitizeMultilineText(dialog.getText()));
		if (text == null || text.isEmpty())
		{
			return;
		}

		String name = null;
		int tier = -1;
		Matcher assigned = DIALOG_CONTRACT.matcher(text);
		Matcher reminder = CONTRACT_REMINDER.matcher(text);
		if (assigned.matches())
		{
			name = assigned.group(2);
		}
		else if (reminder.matches())
		{
			tier = tierFromString(reminder.group(1));
			name = reminder.group(2);
		}
		if (name == null)
		{
			return;
		}

		lastChat = "DIALOG_WIDGET home=" + name + " tier=" + tier + " text=" + text;
		Home home = Home.byDisplayName(name);
		if (home != null)
		{
			currentHome = home;
			if (tier != -1)
			{
				contractTier = tier;
			}
		}
	}

	/**
	 * Collapses every run of whitespace to a single plain space.
	 *
	 * Both patterns here spell out the exact spacing of a sentence, which makes them brittle in
	 * two ways this game hits. OSRS uses NON-BREAKING SPACE (U+00A0) in its own text -- visible
	 * in recorder.py's raw capture as the player name "07 Kiyo" -- and Java's \s does NOT
	 * match U+00A0, so a single one anywhere in the line fails the whole match. And NPC dialogue
	 * is line-wrapped with <br>, which Text.sanitizeMultilineText turns into a space, doubling up
	 * wherever the source already had one. Neither changes what the sentence SAYS, so normalise
	 * rather than trying to enumerate them in the patterns.
	 */
	static String normalizeWhitespace(String message)
	{
		if (message == null)
		{
			return null;
		}
		StringBuilder out = new StringBuilder(message.length());
		boolean pendingSpace = false;
		for (int i = 0; i < message.length(); i++)
		{
			char c = message.charAt(i);
			// isWhitespace misses U+00A0 by design (it is non-breaking), which is exactly the one
			// this has to catch -- so test it explicitly alongside.
			if (Character.isWhitespace(c) || c == NBSP)
			{
				pendingSpace = out.length() > 0;
				continue;
			}
			if (pendingSpace)
			{
				out.append(' ');
				pendingSpace = false;
			}
			out.append(c);
		}
		return out.toString();
	}

	/**
	 * Drops the "<speaker>|" prefix RuneLite puts on a DIALOG chat message, leaving just the
	 * spoken text. Only the FIRST bar is treated as the separator -- a speaker name has no bar
	 * in it, and anything later belongs to the line itself.
	 */
	static String stripDialogSpeaker(String message)
	{
		if (message == null)
		{
			return null;
		}
		int bar = message.indexOf('|');
		return bar >= 0 ? message.substring(bar + 1) : message;
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
			data.put("lastChat", lastChat);
			return data;
		}

		data.put("hasContract", true);
		data.put("homeName", home.name());
		data.put("npcName", home.displayName);
		data.put("contractTier", contractTier);
		data.put("lastChat", lastChat);
		data.put("npcId", home.npcId);
		data.put("standingLocation", pointMap(home.standingLocation));
		data.put("area", areaMap(home.area));
		data.put("ladderObjectIds", home.ladders);
		// PORTED 2026-08-28 from the reference plugin's Home.isNpcUpstairs (it was the one Home
		// field the original port missed). True for JESS and NORMAN only -- every other home
		// takes the constructor overload that defaults it to false. Tells turn_in_phase which
		// FLOOR the homeowner is on, so "the NPC isn't visible" can mean "climb" rather than
		// "walk further".
		data.put("npcUpstairs", home.npcUpstairs);

		Map<Integer, Map<String, Object>> located = locateHotspotObjects(home.slotObjectIds);

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
			// Where this hotspot actually IS, scanned across EVERY plane rather than just the
			// player's own. /objects only ever reports the current plane, so before this the
			// routine could not tell "the hotspot is upstairs" from "I'm not close enough yet"
			// and had to guess by climbing whichever direction a ladder offered. Null when the
			// object isn't in the loaded scene (out of render distance) -- the routine keeps its
			// existing walk-then-climb fallback for that case.
			Map<String, Object> at = located.get(home.slotObjectIds[i]);
			slot.put("location", at);
			slot.put("plane", at != null ? at.get("plane") : null);
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

	/**
	 * Every hotspot object id in `wanted`, located anywhere in the currently loaded scene, on ANY
	 * plane -- as id -> {x, y, plane}.
	 *
	 * Scanning all four planes rather than just the player's is the whole point: a house's upper
	 * floor is present in the scene while you stand downstairs, so this answers "which FLOOR is
	 * this hotspot on" outright. /objects cannot (it walks client.getPlane() only), which is why
	 * routines/mahogany_homes.py previously had to infer the floor by climbing whichever
	 * direction a ladder happened to offer and seeing whether the hotspot turned up.
	 *
	 * A Mahogany Homes house is never instanced, but WorldPoint.fromLocalInstance is used anyway
	 * -- it is correct in both cases and is the codebase rule for reading a position OUT of the
	 * client (see CLAUDE.md's instancing gotcha). Hotspots are GameObjects; WallObject is checked
	 * too because several hotspots are wall-mounted (the reference plugin's own overlay walks
	 * both).
	 */
	private Map<Integer, Map<String, Object>> locateHotspotObjects(int[] wanted)
	{
		Map<Integer, Map<String, Object>> found = new HashMap<>();

		WorldView view = client.getTopLevelWorldView();
		Scene scene = view != null ? view.getScene() : null;
		Tile[][][] tiles = scene != null ? scene.getTiles() : null;
		if (tiles == null)
		{
			return found;
		}

		for (int plane = 0; plane < tiles.length && plane < Constants.MAX_Z; plane++)
		{
			if (tiles[plane] == null)
			{
				continue;
			}
			for (Tile[] column : tiles[plane])
			{
				if (column == null)
				{
					continue;
				}
				for (Tile tile : column)
				{
					if (tile == null)
					{
						continue;
					}
					GameObject[] gameObjects = tile.getGameObjects();
					if (gameObjects != null)
					{
						for (GameObject obj : gameObjects)
						{
							recordIfWanted(obj, plane, wanted, found);
						}
					}
					WallObject wall = tile.getWallObject();
					if (wall != null)
					{
						recordIfWanted(wall, plane, wanted, found);
					}
				}
			}
		}

		return found;
	}

	/**
	 * Adds obj's real world position to `found` if its id is one of `wanted` and it's not there yet.
	 *
	 * `plane` is the plane of the tile array this object was walked out of, and it MUST be passed
	 * in rather than derived from the client. CONFIRMED live 2026-08-28 (Larry's house, Falador):
	 * the two-argument WorldPoint.fromLocalInstance(client, local) stamps the WORLD VIEW's own
	 * current plane onto the result, so every hotspot this scan found upstairs was reported as
	 * plane 0 while the player stood downstairs. That is the exact question this scan exists to
	 * answer, and it answered it wrong in the one direction that matters: work_phase read
	 * "same floor as me", never tried a staircase, and clicked at a hotspot through a ceiling
	 * until the unreachable guard safety-stopped the run. The reference plugin gets this right --
	 * it marks the stairs as an objective when the remaining hotspots are upstairs.
	 */
	private void recordIfWanted(TileObject obj, int plane, int[] wanted,
		Map<Integer, Map<String, Object>> found)
	{
		if (obj == null || found.containsKey(obj.getId()))
		{
			return;
		}
		boolean isWanted = false;
		for (int id : wanted)
		{
			if (id == obj.getId())
			{
				isWanted = true;
				break;
			}
		}
		if (!isWanted)
		{
			return;
		}

		LocalPoint local = obj.getLocalLocation();
		if (local == null)
		{
			return;
		}
		WorldPoint wp = WorldPoint.fromLocalInstance(client, local, plane);
		if (wp == null)
		{
			return;
		}

		Map<String, Object> at = new LinkedHashMap<>();
		at.put("x", wp.getX());
		at.put("y", wp.getY());
		at.put("plane", wp.getPlane());
		found.put(obj.getId(), at);
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
				HotspotType.B3, HotspotType.B3, HotspotType.RP, HotspotType.SB}, true),
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
				HotspotType.B2, HotspotType.B2, HotspotType.B2}, true),
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
		/** Reference plugin's Home.npcUpstairs -- true for JESS and NORMAN only. */
		final boolean npcUpstairs;

		Home(WorldArea area, String displayName, int npcId, WorldPoint standingLocation,
			int[] ladders, int[] slotObjectIds, HotspotType[] slotTypes)
		{
			this(area, displayName, npcId, standingLocation, ladders, slotObjectIds, slotTypes, false);
		}

		Home(WorldArea area, String displayName, int npcId, WorldPoint standingLocation,
			int[] ladders, int[] slotObjectIds, HotspotType[] slotTypes, boolean npcUpstairs)
		{
			this.npcUpstairs = npcUpstairs;
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
