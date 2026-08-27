# Mahogany Homes Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `routines/mahogany_homes.py`, an unattended Adept-tier Mahogany Homes contract
runner, plus the new `MahoganyHomesProvider.java` bridge endpoint it depends on.

**Architecture:** A new RuneLite-side provider ports the reference plugin's contract data
(`Home`/`HotspotObjects`) and tracks the live contract (via chat-message regex, no widget
polling) behind one endpoint, `GET /mahogany-homes`. The Python routine is a five-phase state
machine (`bank` → `request_contract` → `travel` → `work` → `turn_in`) driven entirely by that
endpoint plus existing generic bridge endpoints (`/objects`, `/inventory`, `/rune-pouch`,
`/widgets/dump`) and existing helpers (`travel.walk_to`, `banker.*`, `controller.click_entity`).

**Tech Stack:** Java 11 (RuneLite plugin, Gradle), Python 3 (`ControllerAddon/`), JUnit 4,
pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-mahogany-homes-design.md`

## Global Constraints

- Adept tier only (`contractTier == 3`) — no other tier's flow is exercised.
- Travel uses teleport tablets/Xeric's talisman, never `travel.walk_to` for inter-city hops —
  only for the final approach inside a city and any needed floor changes.
- No menu-escalation on hotspot clicks — plain `click_entity`, relying on the user's client-side
  menu swap (Remove/Build/Repair as the left-click default).
- `NPC Contact` is widget **group 218, child 113** (`InterfaceID.MagicSpellbook.NPC_CONTACT`,
  confirmed against `runelite-api-1.12.33.jar` and against all 4 recordings) — this exact value
  is load-bearing, not a placeholder to swap out later.
- No plank sack in this version — planks are carried loose, restocked more often, to avoid
  importing golem_crafting's known fur-pouch stale-estimate failure mode into a brand-new,
  not-yet-live-tested routine. See Task 5 for the reasoning in full.
- Every new Python file/function follows the existing `routines/*.py` contract documented in
  `CLAUDE.md` (`DEFAULT_CONFIG`, `get_config`, `setup(ctx)`, `tick(ctx)`).

---

## Task 1: `MahoganyHomesProvider.java` — data + live contract tracking

**Files:**
- Modify: `src/main/java/com/example/providers/PlayerProvider.java:185` (add one line after the
  existing `craftingXp` line)
- Create: `src/main/java/com/example/providers/MahoganyHomesProvider.java`
- Create: `src/test/java/com/example/providers/MahoganyHomesProviderTest.java`

**Interfaces:**
- Produces: `GET /mahogany-homes` (registered in Task 2), returning the JSON shape below.
  `MahoganyHomesProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
  EventBus eventBus)` constructor (same shape as `SailingProvider`/`LastActionProvider`).
  Package-private static `parseAssignment(String)` / `isCompletionMessage(String)`, tested by
  Task 1's own JUnit test — no other task depends on these two directly.
- Consumes: nothing from earlier tasks (this is the first task).

- [ ] **Step 1: Add `constructionXp` to `PlayerProvider`**

In `src/main/java/com/example/providers/PlayerProvider.java`, immediately after line 185
(`data.put("craftingXp", client.getSkillExperience(Skill.CRAFTING));`), add:

```java
		data.put("constructionXp", client.getSkillExperience(Skill.CONSTRUCTION));
```

- [ ] **Step 2: Write `MahoganyHomesProviderTest.java` (fails first — no provider class exists yet)**

Create `src/test/java/com/example/providers/MahoganyHomesProviderTest.java`:

```java
package com.example.providers;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertNotNull;

public class MahoganyHomesProviderTest
{
	@Test
	public void parsesAdeptAssignmentWithTrailingHint()
	{
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseAssignment(
			"Adept Contract: Go see Jeff in the middle of Varrock, west of the museum.");
		assertNotNull(parsed);
		assertEquals(3, parsed.tier);
		assertEquals("Jeff", parsed.homeDisplayName);
	}

	@Test
	public void parsesAssignmentWhereNameIsFollowedByComma()
	{
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseAssignment(
			"Adept Contract: Go see Leela, east of the town market in Hosidius.");
		assertNotNull(parsed);
		assertEquals(3, parsed.tier);
		assertEquals("Leela", parsed.homeDisplayName);
	}

	@Test
	public void parsesEveryTier()
	{
		assertEquals(1, MahoganyHomesProvider.parseAssignment(
			"Beginner Contract: Go see Bob in north-east Varrock, opposite the church.").tier);
		assertEquals(2, MahoganyHomesProvider.parseAssignment(
			"Novice Contract: Go see Bob in north-east Varrock, opposite the church.").tier);
		assertEquals(4, MahoganyHomesProvider.parseAssignment(
			"Expert Contract: Go see Bob in north-east Varrock, opposite the church.").tier);
	}

	@Test
	public void rejectsUnrelatedChatMessage()
	{
		assertNull(MahoganyHomesProvider.parseAssignment("Welcome to Old School RuneScape."));
	}

	@Test
	public void recognizesCompletionMessage()
	{
		assertEquals(true, MahoganyHomesProvider.isCompletionMessage(
			"You have completed 190 contracts with a total of 200 points."));
	}

	@Test
	public void doesNotMatchCompletionOnUnrelatedText()
	{
		assertEquals(false, MahoganyHomesProvider.isCompletionMessage(
			"Adept Contract: Go see Jeff in the middle of Varrock, west of the museum."));
	}
}
```

- [ ] **Step 3: Run the test to verify it fails to compile**

Run: `./gradlew.bat test --tests "com.example.providers.MahoganyHomesProviderTest"`
Expected: compile error — `MahoganyHomesProvider` does not exist yet.

- [ ] **Step 4: Write `MahoganyHomesProvider.java`**

Create `src/main/java/com/example/providers/MahoganyHomesProvider.java`:

```java
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
 * polls this as its click-confirmation signal (remainingCount dropping), so a stale read would
 * reintroduce exactly the lag the varbits are here to remove.
 */
public class MahoganyHomesProvider extends BridgeProvider
{
	private static final int FIRST_HOTSPOT_VARBIT = 10554;

	private static final Pattern CONTRACT_ASSIGNED =
		Pattern.compile("(\\w+) Contract: Go see (\\w+)[, .].*", Pattern.CASE_INSENSITIVE);

	// Ported verbatim from the reference plugin's own CONTRACT_FINISHED pattern.
	private static final Pattern CONTRACT_FINISHED =
		Pattern.compile("You have completed [\\d,]+ contracts with a total of [\\d,]+ points?\\.");

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
		Matcher matcher = CONTRACT_ASSIGNED.matcher(message);
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

			Map<String, Object> slot = new LinkedHashMap<>();
			slot.put("varb", varb);
			slot.put("objectId", home.slotObjectIds[i]);
			slot.put("value", value);
			slot.put("needsAttention", needsAttention);
			hotspots.add(slot);

			if (needsAttention)
			{
				remaining++;
				HotspotType type = home.slotTypes[i];
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
```

- [ ] **Step 5: Run the JUnit test to verify it passes**

Run: `./gradlew.bat test --tests "com.example.providers.MahoganyHomesProviderTest"`
Expected: PASS (6 tests).

- [ ] **Step 6: Compile the whole project to catch any unrelated break**

Run: `./gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/providers/MahoganyHomesProvider.java \
        src/main/java/com/example/providers/PlayerProvider.java \
        src/test/java/com/example/providers/MahoganyHomesProviderTest.java
git commit -m "Add MahoganyHomesProvider: ports contract data, tracks active contract via chat"
```

---

## Task 2: Wire the provider, routine choice, and config toggle

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:153` (register call) and
  the `-config` endpoint block around line 168
- Modify: `src/main/java/com/example/controller/RoutineChoice.java:25`
- Modify: `src/main/java/com/example/controller/ControllerConfig.java:80` (new section
  declaration) and end of file (new `@ConfigItem`)
- Modify: `src/main/java/com/example/controller/ConfigExporter.java` (new export method, end of
  file before the closing brace of the class's export-method group — insert after
  `exportMasteringMixology`, whose own body is visible at the `sed` output already gathered)

**Interfaces:**
- Consumes: `MahoganyHomesProvider` (Task 1).
- Produces: `GET /mahogany-homes-config` returning `{"enabled": bool, "breaks": {...},
  "safety": {...}}` — consumed by Task 3's `get_mahogany_homes_config()`.

- [ ] **Step 1: Add the RoutineChoice entry**

In `src/main/java/com/example/controller/RoutineChoice.java`, change:

```java
	FALCONRY("Falconry (Dashing kebbit)"),
	PLANK_MAKE("Plank Make");
```

to:

```java
	FALCONRY("Falconry (Dashing kebbit)"),
	PLANK_MAKE("Plank Make"),
	MAHOGANY_HOMES("Mahogany Homes");
```

- [ ] **Step 2: Add the config section + item to `ControllerConfig.java`**

After line 80 (`String masteringMixologySection = "masteringMixology";`), add a blank line then:

```java
	@ConfigSection(name = "Mahogany Homes", description = "Mahogany Homes routine settings", position = 15)
	String mahoganyHomesSection = "mahoganyHomes";
```

At the very end of the file, immediately before the interface's closing `}`, add:

```java

	// ============================================================
	// Mahogany Homes
	// ============================================================

	@ConfigItem(
		keyName = "mahoganyHomesEnabled",
		name = "Mahogany Homes Enabled",
		description = "Secondary switch on top of launching controller.py with routines/mahogany_homes.py.",
		section = mahoganyHomesSection,
		position = 0
	)
	default boolean mahoganyHomesEnabled()
	{
		return true;
	}
```

- [ ] **Step 3: Add the export method to `ConfigExporter.java`**

Immediately after `exportGolemCrafting`'s closing brace (the method ending at line 224 per the
existing file), add:

```java

	public static Map<String, Object> exportMahoganyHomes(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.mahoganyHomesEnabled());
		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}
```

- [ ] **Step 4: Register the provider and config endpoint in `ControllerPlugin.java`**

After line 153 (`new GolemProvider(client, clientThread, server).register();`), add:

```java
		new MahoganyHomesProvider(client, clientThread, server, eventBus).register();
```

In the `-config` registration block (after line 168,
`server.register("/golem-crafting-config", () -> ConfigExporter.exportGolemCrafting(config));`),
add:

```java
		server.register("/mahogany-homes-config", () -> ConfigExporter.exportMahoganyHomes(config));
```

- [ ] **Step 5: Compile**

Run: `./gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/example/controller/ControllerPlugin.java \
        src/main/java/com/example/controller/RoutineChoice.java \
        src/main/java/com/example/controller/ControllerConfig.java \
        src/main/java/com/example/controller/ConfigExporter.java
git commit -m "Wire Mahogany Homes into RoutineChoice, ControllerConfig, and the bridge"
```

---

## Task 3: Python bridge accessors + `ROUTINE_FILES` entry

**Files:**
- Modify: `ControllerAddon/controller.py` (four new one-line accessor functions, added next to
  `get_mastering_mixology_config` around line 730; one `ROUTINE_FILES` entry around line 2796)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Produces: `controller.get_mahogany_homes_state() -> dict`,
  `controller.get_mahogany_homes_config() -> dict | None`,
  `controller.get_widgets_dump(scan: bool = False) -> list`,
  `controller.get_npc_nearest(npc_id: int) -> dict | None` — all consumed by Task 4 onward.

- [ ] **Step 1: Write the failing tests**

Add to `ControllerAddon/tests/test_controller.py` (append near other `api_get`-backed accessor
tests; check the file's existing imports at the top — it already imports `controller` and uses
`monkeypatch.setattr(controller, "api_get", ...)` per the established convention):

```python
def test_get_mahogany_homes_state_returns_dict(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: {"hasContract": False})
    assert controller.get_mahogany_homes_state() == {"hasContract": False}


def test_get_mahogany_homes_state_defaults_to_no_contract_on_404(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_mahogany_homes_state() == {"hasContract": False}


def test_get_mahogany_homes_config_passes_through(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: {"enabled": True})
    assert controller.get_mahogany_homes_config() == {"enabled": True}


def test_get_widgets_dump_returns_widget_list(monkeypatch):
    monkeypatch.setattr(controller, "api_get",
                         lambda path, **kw: {"widgets": [{"group": 1, "child": 2}]})
    assert controller.get_widgets_dump() == [{"group": 1, "child": 2}]


def test_get_widgets_dump_fails_safe_to_empty_list(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_widgets_dump() == []


def test_get_npc_nearest_returns_full_entity(monkeypatch):
    monkeypatch.setattr(controller, "api_get",
                         lambda path, **kw: {"found": True, "screenX": 5, "screenY": 6, "index": 9})
    result = controller.get_npc_nearest(10415)
    assert result == {"found": True, "screenX": 5, "screenY": 6, "index": 9}


def test_get_npc_nearest_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: {"found": False})
    assert controller.get_npc_nearest(10415) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_controller.py -k mahogany_homes_state or widgets_dump or npc_nearest -v`
(from `ControllerAddon/`)
Expected: FAIL with `AttributeError: module 'controller' has no attribute 'get_mahogany_homes_state'`
(and similarly for the other three).

- [ ] **Step 3: Add the four accessor functions**

In `ControllerAddon/controller.py`, immediately after `get_mastering_mixology_config` (the
function ending at line 731 per the existing file: `return api_get("/mastering-mixology-config")`),
add:

```python
def get_mahogany_homes_config():
    return api_get("/mahogany-homes-config")


def get_mahogany_homes_state():
    return api_get("/mahogany-homes") or {"hasContract": False}


def get_widgets_dump(scan: bool = False):
    data = api_get("/widgets/dump", params={"scan": "true"} if scan else None)
    if not data:
        return []
    return data.get("widgets", [])


def get_npc_nearest(npc_id: int):
    """Full entity dict for the nearest NPC of this *type* (unlike get_npc_screen_pos, which
    only returns a bare (x, y) tuple) -- needed wherever the caller also wants the NPC's index
    (for get_npc_by_index-based tracking) or wants to pass the result straight into
    click_menu_action, which reads more than just screenX/screenY off its `entity` argument."""
    data = api_get("/npc/nearest", params={"id": npc_id})
    if not data or not data.get("found"):
        return None
    return data
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_controller.py -k mahogany_homes_state or widgets_dump or npc_nearest -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Add the `ROUTINE_FILES` entry**

In `ControllerAddon/controller.py`'s `ROUTINE_FILES` dict, after
`"PLANK_MAKE": "routines/plank_make.py",`, add:

```python
    "MAHOGANY_HOMES": "routines/mahogany_homes.py",
```

- [ ] **Step 6: `py_compile` + run the full test suite**

Run (from `ControllerAddon/`): `python -m py_compile controller.py && python -m pytest tests/ -v`
Expected: no compile error; all tests pass (the new ones plus every pre-existing test).

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "Add Mahogany Homes bridge accessors and ROUTINE_FILES entry"
```

---

## Task 4: `routines/mahogany_homes.py` skeleton — config + pure decision functions

**Files:**
- Create: `ControllerAddon/routines/mahogany_homes.py`
- Create: `ControllerAddon/tests/test_mahogany_homes.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG` (dict), `get_config(bridge_config) -> dict`,
  `tier_number(name: str) -> int`, `pick_next_hotspot(hotspots: list) -> dict | None`,
  `find_object_by_id(objects: list, object_id: int) -> dict | None`,
  `deficit(current: int, target: int) -> int`,
  `count_item(inventory: list, name: str) -> int`,
  `rune_pouch_counts(rune_pouch: dict) -> dict`,
  `needs_bank_trip(ctx, config: dict, state: dict) -> bool`. `setup(ctx)` is also added here
  (harness-required per the routine contract) even though `tick` doesn't exist until Task 7 —
  a routine module without `tick` simply isn't loadable by `controller.py` yet, which is fine
  since this task's own tests import the module directly, not through the loader.
- Consumes: `controller.get_inventory`, `controller.get_rune_pouch` (both pre-existing),
  `controller.get_mahogany_homes_config` (Task 3).

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_mahogany_homes.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import mahogany_homes as mh
import controller


def _hotspot(varb, object_id, value, needs_attention):
    return {"varb": varb, "objectId": object_id, "value": value, "needsAttention": needs_attention}


def _object(obj_id, distance=1, x=0, y=0, plane=0):
    return {"id": obj_id, "distance": distance, "x": x, "y": y, "plane": plane,
            "screenX": 100, "screenY": 100}


def _item(name, quantity=1):
    return {"name": name, "quantity": quantity}


# --- tier_number ---------------------------------------------------------------------

def test_tier_number_recognizes_all_four_tiers():
    assert mh.tier_number("beginner") == 1
    assert mh.tier_number("Novice") == 2
    assert mh.tier_number("ADEPT") == 3
    assert mh.tier_number("expert") == 4


def test_tier_number_returns_zero_for_unknown():
    assert mh.tier_number("bogus") == 0


# --- pick_next_hotspot ----------------------------------------------------------------

def test_pick_next_hotspot_returns_first_needing_attention():
    hotspots = [
        _hotspot(10554, 39989, 0, False),
        _hotspot(10555, 39990, 3, True),
        _hotspot(10556, 39991, 3, True),
    ]
    assert mh.pick_next_hotspot(hotspots)["objectId"] == 39990


def test_pick_next_hotspot_returns_none_when_all_done():
    hotspots = [_hotspot(10554, 39989, 0, False), _hotspot(10555, 39990, 0, False)]
    assert mh.pick_next_hotspot(hotspots) is None


def test_pick_next_hotspot_returns_none_for_empty_list():
    assert mh.pick_next_hotspot([]) is None


# --- find_object_by_id ------------------------------------------------------------------

def test_find_object_by_id_matches_exact_id():
    objects = [_object(1111, distance=5), _object(39990, distance=3)]
    found = mh.find_object_by_id(objects, 39990)
    assert found is not None and found["id"] == 39990


def test_find_object_by_id_picks_nearest_when_duplicated():
    objects = [_object(39990, distance=9), _object(39990, distance=2)]
    assert mh.find_object_by_id(objects, 39990)["distance"] == 2


def test_find_object_by_id_returns_none_when_absent():
    assert mh.find_object_by_id([_object(1111)], 39990) is None


# --- deficit --------------------------------------------------------------------------

def test_deficit_positive_when_short():
    assert mh.deficit(5, 10) == 5


def test_deficit_zero_when_sufficient():
    assert mh.deficit(10, 5) == 0


def test_deficit_zero_when_exact():
    assert mh.deficit(5, 5) == 0


# --- count_item -------------------------------------------------------------------------

def test_count_item_sums_matching_quantities_case_insensitively():
    inventory = [_item("Teak plank", 1), _item("teak plank", 1), _item("Steel bar", 5)]
    assert mh.count_item(inventory, "Teak plank") == 2


def test_count_item_uses_quantity_field_when_present():
    inventory = [_item("Varrock teleport", 8)]
    assert mh.count_item(inventory, "Varrock teleport") == 8


def test_count_item_returns_zero_when_absent():
    assert mh.count_item([_item("Hammer")], "Teak plank") == 0


# --- rune_pouch_counts ------------------------------------------------------------------

def test_rune_pouch_counts_builds_lowercase_name_map():
    pouch = {"runes": [{"slot": 0, "type": 1, "quantity": 500, "name": "Astral rune"},
                        {"slot": 1, "type": 2, "quantity": 300, "name": "Cosmic rune"}]}
    counts = mh.rune_pouch_counts(pouch)
    assert counts == {"astral rune": 500, "cosmic rune": 300}


def test_rune_pouch_counts_skips_unresolved_slots():
    pouch = {"runes": [{"slot": 0, "type": 0, "quantity": 0}]}
    assert mh.rune_pouch_counts(pouch) == {}


def test_rune_pouch_counts_handles_empty_pouch():
    assert mh.rune_pouch_counts({"runes": []}) == {}


# --- needs_bank_trip --------------------------------------------------------------------

class _State:
    def __init__(self):
        self.data = {}
        self.config = mh.DEFAULT_CONFIG


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = _State()


def test_needs_bank_trip_true_when_planks_low(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 2)])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is True


def test_needs_bank_trip_true_when_a_required_rune_low(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], mh.DEFAULT_CONFIG["restock"]["targetPlanks"]),
        _item("Steel bar", mh.DEFAULT_CONFIG["restock"]["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8),
        _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 5},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is True


def test_needs_bank_trip_false_when_everything_sufficient(monkeypatch):
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8),
        _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mahogany_homes.py -v` (from `ControllerAddon/`)
Expected: FAIL with `ModuleNotFoundError: No module named 'mahogany_homes'`.

- [ ] **Step 3: Write `routines/mahogany_homes.py` (config + pure functions only)**

Create `ControllerAddon/routines/mahogany_homes.py`:

```python
# routines/mahogany_homes.py
#
# Runs Adept-tier Mahogany Homes contracts unattended: request a contract via the NPC Contact
# spell, travel to the assigned homeowner's house via teleport tablets, remove/build/repair
# every hotspot, turn the contract in, repeat. See
# docs/superpowers/specs/2026-08-27-mahogany-homes-design.md for the full design and the
# recording/decompile evidence behind every constant below.
#
# Every WorldArea/object-id/tier fact this routine depends on lives server-side in
# MahoganyHomesProvider.java (ported from the decompiled reference plugin) and is read fresh
# every tick via GET /mahogany-homes -- this file holds no duplicate copy of that data.
#
# NOT YET LIVE-TESTED as of this file's creation. HOTSPOT_CLICK_COOLDOWN_S/TURN_IN_CLICK_
# COOLDOWN_S below are first-draft guesses (no recording measured Mahogany Homes' own action
# duration the way golem_crafting.py's GOLEM_SIDE_TIMEOUT_S was measured at 11.8s/side) --
# expect to retune them after the first live run, the same way that constant was corrected
# from an initial guess once real timing was observed.

import time

import banker
import controller
import debug_log
import travel

# InterfaceID.MagicSpellbook.NPC_CONTACT (packed 14286961 == 218<<16 | 113) -- confirmed both
# against runelite-api-1.12.33.jar (javap -v) and against all four recordings, where every
# "Last-tier contract" click lands on this exact widget. See the design doc's "Contract
# request" section for the full derivation.
NPC_CONTACT_GROUP = 218
NPC_CONTACT_CHILD = 113

# F6 opens the Magic tab -- MEASURED in the recordings (0.6s before every NPC Contact click,
# e.g. recordings/2026-08-27_174045_mahogny-homes.log:2181-2186).
MAGIC_TAB_KEY = "F6"

CONTACT_RETRY_COOLDOWN_S = 6.0     # spell cast -> assignment chat measured at ~4.4s in the
                                    # recordings; this just stops re-casting while waiting.
HOTSPOT_CLICK_COOLDOWN_S = 2.5      # first-draft guess, see module docstring
TURN_IN_CLICK_COOLDOWN_S = 2.0      # first-draft guess, see module docstring
TRAVEL_TELEPORT_DISTANCE = 40       # tiles -- skip teleporting if already this close

# Which teleport item gets a player near each home's city. Xeric's talisman's "Xeric's Glade"
# destination is the closest teleport to Barbara/Leela/Mariah's homes (see Mahogany Homes.txt's
# own "Recommended setups" section) -- same city grouping as the reference plugin's Home enum.
CITY_TELEPORTS = {
    "JESS": "ardougne teleport", "NOELLA": "ardougne teleport", "ROSS": "ardougne teleport",
    "LARRY": "falador teleport", "NORMAN": "falador teleport", "TAU": "falador teleport",
    "BARBARA": "xeric's talisman", "LEELA": "xeric's talisman", "MARIAH": "xeric's talisman",
    "BOB": "varrock teleport", "JEFF": "varrock teleport", "SARAH": "varrock teleport",
}

# No plank sack in this version -- planks are carried loose. targetPlanks=18 covers an Adept
# contract's worst case (Bob/Jeff need up to ~16-17 per the wiki's own per-homeowner table)
# with a small buffer, while leaving inventory room for tools/tablets/rune pouch/talisman
# (hammer+saw+rune pouch+3 tablet types+talisman = 7 slots; 18+3 = 21 fits in the remaining 21).
# See the plan's Task 5 for why the sack was left out rather than built the way golem_crafting's
# fur pouch was.
DEFAULT_CONFIG = {
    "enabled": True,
    "tier": "adept",
    "plankName": "Teak plank",
    "restock": {
        "minPlanks": 6,
        "targetPlanks": 18,
        "minSteelBars": 1,
        "targetSteelBars": 3,
        "minTabletCount": 2,
        "tabletTargets": {
            "varrock teleport": 8,
            "ardougne teleport": 8,
            "falador teleport": 8,
        },
        "minRuneCount": 30,
        "runeTargets": {
            "astral rune": 300,
            "cosmic rune": 300,
        },
    },
    "homeTeleport": "varrock teleport",
    "bank": {
        "bankNames": ["Bank booth", "Bank chest", "Bank counter", "Grand Exchange booth"],
        "maxBankDistance": 40,
    },
    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_mahogany_homes_config()
    if config is None:
        return DEFAULT_CONFIG

    # Only "enabled"/"breaks"/"safety" come from the bridge (ConfigExporter.exportMahoganyHomes)
    # -- everything else (restock thresholds, plank name, teleport mapping) is routine-specific
    # tuning that isn't worth a RuneLite GUI control, same reasoning golem_crafting.py's
    # "locations" re-injection comment gives for its own config.
    merged = dict(DEFAULT_CONFIG)
    merged["enabled"] = config.get("enabled", DEFAULT_CONFIG["enabled"])
    merged["breaks"] = config.get("breaks", DEFAULT_CONFIG["breaks"])
    merged["safety"] = config.get("safety", DEFAULT_CONFIG["safety"])
    return merged


def setup(ctx):
    debug_log.log("mahogany_homes", "reminder: RuneLite's menu-entry swapper must have Remove/"
                                     "Build/Repair set as the left-click default on Mahogany "
                                     "Homes hotspot objects, and NPC Contact must already be "
                                     "configured to request a last-tier (Adept) contract in one "
                                     "click -- this routine does not set up either.")


def tier_number(name: str) -> int:
    return {"beginner": 1, "novice": 2, "adept": 3, "expert": 4}.get((name or "").lower(), 0)


def pick_next_hotspot(hotspots: list):
    for hotspot in hotspots:
        if hotspot.get("needsAttention"):
            return hotspot
    return None


def find_object_by_id(objects: list, object_id: int):
    matches = [o for o in objects if o.get("id") == object_id]
    if not matches:
        return None
    return min(matches, key=lambda o: o.get("distance", float("inf")))


def deficit(current: int, target: int) -> int:
    return max(0, target - current)


def count_item(inventory: list, name: str) -> int:
    name_lower = name.lower()
    return sum(
        item.get("quantity", 1)
        for item in inventory
        if (item.get("name") or "").lower() == name_lower
    )


def rune_pouch_counts(rune_pouch: dict) -> dict:
    counts = {}
    for slot in rune_pouch.get("runes", []):
        name = (slot.get("name") or "").lower()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + slot.get("quantity", 0)
    return counts


def needs_bank_trip(ctx, config: dict, state: dict) -> bool:
    restock = config.get("restock", {})
    inventory = controller.get_inventory()
    plank_name = config.get("plankName", "Teak plank")

    if deficit(count_item(inventory, plank_name), restock.get("minPlanks", 0)) > 0:
        return True
    if deficit(count_item(inventory, "Steel bar"), restock.get("minSteelBars", 0)) > 0:
        return True

    min_tablets = restock.get("minTabletCount", 0)
    for tablet_name in restock.get("tabletTargets", {}):
        if deficit(count_item(inventory, tablet_name), min_tablets) > 0:
            return True

    min_runes = restock.get("minRuneCount", 0)
    counts = rune_pouch_counts(controller.get_rune_pouch())
    for rune_name in restock.get("runeTargets", {}):
        if deficit(counts.get(rune_name.lower(), 0), min_runes) > 0:
            return True

    return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mahogany_homes.py -v`
Expected: PASS (all tests from Step 1).

- [ ] **Step 5: `py_compile` the new file**

Run: `python -m py_compile routines/mahogany_homes.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/mahogany_homes.py ControllerAddon/tests/test_mahogany_homes.py
git commit -m "Add mahogany_homes.py skeleton: config + pure decision functions"
```

---

## Task 5: `bank_phase`

**Files:**
- Modify: `ControllerAddon/routines/mahogany_homes.py` (append `bank_phase`)
- Modify: `ControllerAddon/tests/test_mahogany_homes.py` (append tests)

**Interfaces:**
- Consumes: `banker.find_nearby_bank`, `banker.open_bank`, `banker.close_bank`,
  `banker.withdraw_loadout` (all pre-existing), `count_item`/`deficit` (Task 4).
- Produces: `bank_phase(ctx, config, state) -> None`; sets `ctx.state.data["mh_phase"]` to
  `"request_contract"` or `"travel"` on completion. Consumed by Task 7's `PHASE_HANDLERS`.

**Design note (why no plank sack):** golem_crafting.py's fur pouch (`gc_fur_stock`) is a
locally-tracked *estimate* of a container's contents, because the bridge has no way to see
inside it -- and `CLAUDE.md` records that this estimate can read positive while the pouch is
genuinely empty, a real bug that cost that routine several live-debugging sessions to work
around (fur-priority reclick gates, chat-message corrections, etc.). A plank sack has the exact
same blind-spot: `GET /inventory` can't see inside it either. Importing that same failure mode
into a brand-new, not-yet-live-tested routine is a bad trade for the inventory-space savings it
buys, so this version keeps planks loose and re-banks more often instead. If bank-trip frequency
turns out to matter after a live run, an in-sack estimate can be added later following
golem_crafting's own (eventually-corrected) pattern.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_mahogany_homes.py`:

```python
def test_bank_phase_breaks_home_teleport_when_no_bank_and_no_teleport_leaves_safety_stopped(monkeypatch):
    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})
    # No bank in range and no home teleport tablet in inventory -- genuine dead end.
    assert ctx.state.safety_stopped is True


def test_bank_phase_withdraws_up_to_target_and_advances_phase(monkeypatch):
    withdrawn = []

    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: {"id": 1, "name": "Bank booth"})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, bank_obj: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: None)

    def fake_withdraw_loadout(ctx, config, fresh=False):
        withdrawn.append(config["withdraw"][0])

    monkeypatch.setattr(banker, "withdraw_loadout", fake_withdraw_loadout)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    names = {w["name"] for w in withdrawn}
    assert mh.DEFAULT_CONFIG["plankName"] in names
    assert "Steel bar" in names
    assert ctx.state.data["mh_phase"] == "travel"
```

Add `safety_stopped`/`trigger_safety_stop` to the shared `_State` test helper (used by every test
in this file from here on) -- update its definition earlier in the file:

```python
class _State:
    def __init__(self):
        self.data = {}
        self.config = mh.DEFAULT_CONFIG
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason
```

And add `import banker` alongside the existing `import mahogany_homes as mh` / `import controller`
at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mahogany_homes.py -k bank_phase -v`
Expected: FAIL with `AttributeError: module 'mahogany_homes' has no attribute 'bank_phase'`.

- [ ] **Step 3: Add `bank_phase` to `routines/mahogany_homes.py`**

Add `import banker` to the top of the file (alongside the existing `import controller`,
`import debug_log`, `import travel`), then append:

```python
def bank_phase(ctx, config: dict, state: dict) -> None:
    bank_config = config.get("bank", {})
    bank_obj = banker.find_nearby_bank(bank_config)
    if bank_obj is None:
        home_teleport = config.get("homeTeleport", "varrock teleport")
        item = next(
            (i for i in controller.get_inventory() if home_teleport in (i.get("name") or "").lower()),
            None,
        )
        if item is None:
            ctx.state.trigger_safety_stop(
                f"out of {home_teleport} and no bank in range for Mahogany Homes restocking"
            )
            return
        debug_log.log("mahogany_homes", f"breaking {home_teleport} to reach a bank")
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        controller.human_delay(2.5, 3.2)
        return

    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank for Mahogany Homes restocking")
        return

    restock = config.get("restock", {})
    plank_name = config.get("plankName", "Teak plank")

    _withdraw_up_to(ctx, plank_name, restock.get("targetPlanks", 0))
    _withdraw_up_to(ctx, "Steel bar", restock.get("targetSteelBars", 0))
    for tablet_name, target in restock.get("tabletTargets", {}).items():
        _withdraw_up_to(ctx, tablet_name, target)

    banker.close_bank(ctx)
    controller.human_delay(0.3, 0.6)
    ctx.state.data["mh_phase"] = "request_contract" if not state.get("hasContract") else "travel"


def _withdraw_up_to(ctx, name: str, target: int) -> None:
    have = count_item(controller.get_inventory(), name)
    need = deficit(have, target)
    if need <= 0:
        return
    banker.withdraw_loadout(ctx, {"withdraw": [{"name": name, "quantity": need}]}, fresh=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mahogany_homes.py -v`
Expected: PASS (every test in the file, old and new).

- [ ] **Step 5: `py_compile`**

Run: `python -m py_compile routines/mahogany_homes.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/mahogany_homes.py ControllerAddon/tests/test_mahogany_homes.py
git commit -m "Add mahogany_homes.py bank_phase (loose planks, no plank sack -- see docstring)"
```

---

## Task 6: `request_contract_phase` + `travel_phase`

**Files:**
- Modify: `ControllerAddon/routines/mahogany_homes.py` (append both phase functions)
- Modify: `ControllerAddon/tests/test_mahogany_homes.py` (append tests)

**Interfaces:**
- Consumes: `controller.get_widget`, `controller.click_entity`, `controller.key_tap`,
  `controller.get_player`, `controller.get_inventory`, `travel.walk_to` (all pre-existing).
- Produces: `request_contract_phase(ctx, config, state) -> None` (sets `mh_phase` to `"travel"`
  once `state["hasContract"]` is true — read on the *next* tick's fresh state, not this call's
  stale `state` argument), `travel_phase(ctx, config, state) -> None` (sets `mh_phase` to
  `"work"` on arrival, or `"bank"` if the needed teleport item is missing). Both consumed by
  Task 7's `PHASE_HANDLERS`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_mahogany_homes.py`:

```python
def test_request_contract_phase_skips_when_already_has_contract():
    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})
    assert "mh_contact_cast_at" not in ctx.state.data


def test_request_contract_phase_casts_npc_contact_when_no_contract(monkeypatch):
    clicked = {}

    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                         lambda group, child, index=None: {"clickable": True, "screenX": 1, "screenY": 2})

    def fake_click(ser, entity, **kw):
        clicked["entity"] = entity
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click)

    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert clicked["entity"] == {"clickable": True, "screenX": 1, "screenY": 2}
    assert "mh_contact_cast_at" in ctx.state.data


def test_request_contract_phase_respects_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: calls.append(key))

    ctx = _Ctx()
    ctx.state.data["mh_contact_cast_at"] = time.time()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert calls == []  # still on cooldown -- must not re-cast


def test_travel_phase_teleports_then_walks(monkeypatch):
    clicked = {}

    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Varrock teleport", 8)])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    def fake_click(ser, entity, **kw):
        clicked["entity"] = entity

    monkeypatch.setattr(controller, "click_entity", fake_click)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert clicked["entity"]["name"] == "Varrock teleport"
    assert ctx.state.data["mh_teleported_this_contract"] is True


def test_travel_phase_walks_to_standing_tile_after_teleport_and_advances_to_work(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3448, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] == "work"


def test_travel_phase_banks_when_teleport_item_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])

    ctx = _Ctx()
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] == "bank"


def test_travel_phase_resets_teleport_flag_on_new_home(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Falador teleport", 8)])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: None)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "TAU", "standingLocation": {"x": 3047, "y": 3345, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    # New home -> the flag must have been reset before being set again by this same call.
    assert ctx.state.data["mh_last_home"] == "TAU"
    assert ctx.state.data["mh_teleported_this_contract"] is True
```

Add `import time` and `import travel` to the test file's imports if not already present (`travel`
is already imported transitively by `mahogany_homes`, but the test file needs its own reference
to monkeypatch `travel.walk_to` directly).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mahogany_homes.py -k "request_contract_phase or travel_phase" -v`
Expected: FAIL — both functions don't exist yet.

- [ ] **Step 3: Add both phase functions**

Append to `ControllerAddon/routines/mahogany_homes.py`:

```python
def request_contract_phase(ctx, config: dict, state: dict) -> None:
    if state.get("hasContract"):
        return

    last_cast = ctx.state.data.get("mh_contact_cast_at", 0.0)
    if time.time() - last_cast < CONTACT_RETRY_COOLDOWN_S:
        return

    controller.key_tap(ctx.ser, MAGIC_TAB_KEY)
    controller.human_delay(0.3, 0.5)

    widget = controller.get_widget(NPC_CONTACT_GROUP, NPC_CONTACT_CHILD)
    if not widget or not widget.get("clickable"):
        debug_log.log("mahogany_homes", "NPC Contact not clickable -- Lunar spellbook active?")
        return

    debug_log.log("mahogany_homes", "casting NPC Contact for a new contract")
    controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False)
    ctx.state.data["mh_contact_cast_at"] = time.time()


def travel_phase(ctx, config: dict, state: dict) -> None:
    home_name = state.get("homeName")
    if ctx.state.data.get("mh_last_home") != home_name:
        ctx.state.data["mh_last_home"] = home_name
        ctx.state.data["mh_teleported_this_contract"] = False

    target = state.get("standingLocation") or {}
    player = controller.get_player()
    if _chebyshev(player, target) > TRAVEL_TELEPORT_DISTANCE and not ctx.state.data.get("mh_teleported_this_contract"):
        teleport_name = CITY_TELEPORTS.get(home_name)
        if teleport_name is None:
            ctx.state.trigger_safety_stop(f"unrecognized Mahogany Homes home {home_name!r}")
            return

        item = next(
            (i for i in controller.get_inventory() if teleport_name in (i.get("name") or "").lower()),
            None,
        )
        if item is None:
            debug_log.log("mahogany_homes", f"no {teleport_name} in inventory -- banking")
            ctx.state.data["mh_phase"] = "bank"
            return

        debug_log.log("mahogany_homes", f"breaking {teleport_name} toward {home_name}")
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        controller.human_delay(2.5, 3.2)
        ctx.state.data["mh_teleported_this_contract"] = True
        return

    if not travel.walk_to(ctx, target):
        debug_log.log("mahogany_homes", f"still walking toward {home_name}'s standing tile")
        return

    ctx.state.data["mh_phase"] = "work"


def _chebyshev(a: dict, b: dict) -> int:
    return max(abs(a.get("x", 0) - b.get("x", 0)), abs(a.get("y", 0) - b.get("y", 0)))
```

Add `import time` to the top of `routines/mahogany_homes.py` if not already present (it is
already listed among that file's imports from Task 4).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mahogany_homes.py -v`
Expected: PASS (every test in the file).

- [ ] **Step 5: `py_compile`**

Run: `python -m py_compile routines/mahogany_homes.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/mahogany_homes.py ControllerAddon/tests/test_mahogany_homes.py
git commit -m "Add mahogany_homes.py request_contract_phase and travel_phase"
```

---

## Task 7: `work_phase` + `turn_in_phase` + `tick()` — completes the routine

**Files:**
- Modify: `ControllerAddon/routines/mahogany_homes.py` (append both phase functions, `tick()`,
  `PHASE_HANDLERS`)
- Modify: `ControllerAddon/tests/test_mahogany_homes.py` (append tests)

**Interfaces:**
- Consumes: `controller.get_objects`, `controller.object_refresh_target`,
  `controller.click_menu_action`, `controller.get_widgets_dump` (Task 3), `controller.get_npc_nearest`
  (Task 3), `bank_phase`/`request_contract_phase`/`travel_phase` (Tasks 5-6),
  `pick_next_hotspot`/`find_object_by_id`/`count_item`/`deficit`/`needs_bank_trip` (Task 4).
- Produces: `work_phase(ctx, config, state)`, `turn_in_phase(ctx, config, state)`,
  `tick(ctx)`, `PHASE_HANDLERS` (dict) — this is the routine's public entry point per the
  routine contract in `CLAUDE.md`; nothing later depends on this file's internals.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_mahogany_homes.py`:

```python
def test_work_phase_advances_to_turn_in_when_nothing_left():
    ctx = _Ctx()
    state = {"hotspots": [_hotspot(10554, 39989, 0, False)], "requiredMaterials": {"planks": 0, "steelBars": 0}}
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert ctx.state.data["mh_phase"] == "turn_in"


def test_work_phase_banks_when_materials_insufficient(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 1)])
    ctx = _Ctx()
    state = {
        "hotspots": [_hotspot(10554, 39989, 3, True)],
        "requiredMaterials": {"planks": 5, "steelBars": 0},
    }
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert ctx.state.data["mh_phase"] == "bank"


def test_work_phase_walks_closer_when_object_not_yet_visible(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [])
    walked = {}
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.setdefault("target", target))

    ctx = _Ctx()
    state = {
        "hotspots": [_hotspot(10554, 39989, 3, True)],
        "requiredMaterials": {"planks": 3, "steelBars": 0},
        "standingLocation": {"x": 3239, "y": 3450, "plane": 0},
        "homeName": "JEFF",
    }
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert walked["target"] == {"x": 3239, "y": 3450, "plane": 0}


def test_work_phase_clicks_the_hotspot_object(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(39989, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))

    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    state = {
        "hotspots": [_hotspot(10554, 39989, 3, True)],
        "requiredMaterials": {"planks": 3, "steelBars": 0},
        "standingLocation": {"x": 3239, "y": 3450, "plane": 0},
        "homeName": "JEFF",
    }
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert clicked["entity"]["id"] == 39989
    assert "mh_last_hotspot_click" in ctx.state.data


def test_work_phase_respects_click_cooldown(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(39989, distance=2)])

    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: clicked.append(entity))

    ctx = _Ctx()
    ctx.state.data["mh_last_hotspot_click"] = time.time()
    state = {
        "hotspots": [_hotspot(10554, 39989, 3, True)],
        "requiredMaterials": {"planks": 3, "steelBars": 0},
        "standingLocation": {"x": 3239, "y": 3450, "plane": 0},
        "homeName": "JEFF",
    }
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert clicked == []


def test_turn_in_phase_talks_to_homeowner(monkeypatch):
    monkeypatch.setattr(controller, "get_npc_nearest",
                         lambda npc_id: {"index": 5, "screenX": 10, "screenY": 20})
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])

    talked = {}
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda ser, entity, option, **kw: talked.setdefault("option", option))

    ctx = _Ctx()
    state = {"npcId": 10415, "npcName": "Jeff"}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert talked["option"] == "Talk-to"


def test_turn_in_phase_accepts_offered_tea(monkeypatch):
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: {"index": 5})
    monkeypatch.setattr(controller, "get_widgets_dump",
                         lambda: [{"text": "Yes, I'd love a cuppa.", "screenX": 40, "screenY": 50}])

    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    ctx.state.data["mh_last_turnin_click"] = time.time()  # skip the Talk-to click this tick
    state = {"npcId": 10415, "npcName": "Jeff"}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert clicked["entity"]["screenX"] == 40


def test_tick_starts_in_bank_phase_when_stock_is_low(monkeypatch):
    # PHASE_HANDLERS holds direct function references captured at module-load time, so
    # patching mh.bank_phase itself would NOT change what tick() actually calls through the
    # dict -- monkeypatch.setitem on the dict is the correct way to intercept it.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "bank"))

    ctx = _Ctx()
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "bank"
    assert ctx.state.data["called"] == "bank"


def test_tick_moves_to_request_contract_once_stocked(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8), _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "request_contract",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "request"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "request_contract"
    assert ctx.state.data["called"] == "request"


def test_tick_resets_to_request_contract_when_contract_disappears_mid_flow(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "request_contract",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "request"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "turn_in"
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "request_contract"
    assert ctx.state.data["called"] == "request"


def test_tick_does_nothing_when_disabled():
    ctx = _Ctx()
    ctx.state.config = dict(mh.DEFAULT_CONFIG)
    ctx.state.config["enabled"] = False
    mh.tick(ctx)
    assert "mh_phase" not in ctx.state.data
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mahogany_homes.py -k "work_phase or turn_in_phase or tick" -v`
Expected: FAIL — none of `work_phase`/`turn_in_phase`/`tick` exist yet.

- [ ] **Step 3: Add both phase functions, `tick()`, and `PHASE_HANDLERS`**

Append to `ControllerAddon/routines/mahogany_homes.py`:

```python
def work_phase(ctx, config: dict, state: dict) -> None:
    hotspots = state.get("hotspots") or []
    target = pick_next_hotspot(hotspots)
    if target is None:
        ctx.state.data["mh_phase"] = "turn_in"
        return

    required = state.get("requiredMaterials") or {}
    inventory = controller.get_inventory()
    plank_name = config.get("plankName", "Teak plank")
    have_planks = count_item(inventory, plank_name)
    have_bars = count_item(inventory, "Steel bar")
    if (deficit(have_planks, required.get("planks", 0)) > 0
            or deficit(have_bars, required.get("steelBars", 0)) > 0):
        debug_log.log(
            "mahogany_homes",
            f"short on materials for the rest of this contract (have {have_planks} planks/"
            f"{have_bars} bars, need {required.get('planks', 0)}/{required.get('steelBars', 0)}) -- banking",
        )
        ctx.state.data["mh_phase"] = "bank"
        return

    objects = controller.get_objects()
    obj = find_object_by_id(objects, target["objectId"])
    if obj is None:
        debug_log.log(
            "mahogany_homes",
            f"hotspot object {target['objectId']} not visible yet -- walking toward "
            f"{state.get('homeName')}'s house",
        )
        travel.walk_to(ctx, state.get("standingLocation") or {})
        return

    last_click = ctx.state.data.get("mh_last_hotspot_click", 0.0)
    if time.time() - last_click < HOTSPOT_CLICK_COOLDOWN_S:
        return

    debug_log.log("mahogany_homes", f"working hotspot {target['objectId']} (varb {target['varb']})")
    controller.click_entity(
        ctx.ser, obj,
        refresh_target=controller.object_refresh_target(obj["x"], obj["y"], obj["plane"], obj["id"]),
        refresh_legs=2,
    )
    ctx.state.data["mh_last_hotspot_click"] = time.time()


def turn_in_phase(ctx, config: dict, state: dict) -> None:
    npc_id = state.get("npcId")
    npc = controller.get_npc_nearest(npc_id)
    if npc is None:
        debug_log.log("mahogany_homes", f"homeowner npc {npc_id} not visible -- walking to standing tile")
        travel.walk_to(ctx, state.get("standingLocation") or {})
        return

    _accept_tea_if_offered(ctx)

    last_click = ctx.state.data.get("mh_last_turnin_click", 0.0)
    if time.time() - last_click < TURN_IN_CLICK_COOLDOWN_S:
        return

    debug_log.log("mahogany_homes", f"turning in the contract with {state.get('npcName')}")
    controller.click_menu_action(ctx.ser, npc, "Talk-to")
    ctx.state.data["mh_last_turnin_click"] = time.time()


def _accept_tea_if_offered(ctx) -> None:
    option = next(
        (w for w in controller.get_widgets_dump() if "cuppa" in (w.get("text") or "").lower()),
        None,
    )
    if option is not None:
        debug_log.log("mahogany_homes", "accepting the offered cup of tea")
        controller.click_entity(ctx.ser, option, offset=(0, 0), check_ui_blockers=False)


PHASE_HANDLERS = {
    "bank": bank_phase,
    "request_contract": request_contract_phase,
    "travel": travel_phase,
    "work": work_phase,
    "turn_in": turn_in_phase,
}


def tick(ctx) -> None:
    config = ctx.state.config
    if not config.get("enabled", True):
        return

    state = controller.get_mahogany_homes_state()
    phase = ctx.state.data.get("mh_phase") or "bank"

    if not state.get("hasContract"):
        if needs_bank_trip(ctx, config, state):
            phase = "bank"
        elif phase != "request_contract":
            phase = "request_contract"
    elif phase in ("bank", "request_contract"):
        phase = "travel"
    elif phase == "work" and needs_bank_trip(ctx, config, state):
        debug_log.log("mahogany_homes", "running low mid-contract -- banking")
        phase = "bank"

    ctx.state.data["mh_phase"] = phase
    PHASE_HANDLERS[phase](ctx, config, state)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mahogany_homes.py -v`
Expected: PASS (every test in the file).

- [ ] **Step 5: `py_compile`**

Run: `python -m py_compile routines/mahogany_homes.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/mahogany_homes.py ControllerAddon/tests/test_mahogany_homes.py
git commit -m "Complete mahogany_homes.py: work_phase, turn_in_phase, tick() dispatcher"
```

---

## Task 8: Full verification + CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md` (new "Current state of routines/mahogany_homes.py" section)

**Interfaces:**
- Consumes: everything from Tasks 1-7. Nothing later depends on this task — it's the final
  polish/verification pass.

- [ ] **Step 1: `py_compile` every touched Python file**

Run (from `ControllerAddon/`):

```bash
python -m py_compile controller.py banker.py travel.py routines/mahogany_homes.py
```

Expected: no output, exit code 0 for each.

- [ ] **Step 2: Run the full Python test suite**

Run (from `ControllerAddon/`): `python -m pytest tests/ -v`
Expected: PASS — every test in the suite, not just this feature's new ones (per the project's
own live-debugging workflow: "Pytest alone is not sufficient... but always run the whole suite
before relaunching").

- [ ] **Step 3: Compile and test the Java side**

Run (from the repo root): `./gradlew.bat compileJava test`
Expected: BUILD SUCCESSFUL, including the `MahoganyHomesProviderTest` cases from Task 1.

- [ ] **Step 4: Add the CLAUDE.md section**

Add a new `## Current state of routines/mahogany_homes.py` section to `CLAUDE.md` (placed after
the existing `## Current state of routines/chinchompa_hunting.py` section, before `## Clicking
interface elements`), with this content:

```markdown
## Current state of routines/mahogany_homes.py

Written 2026-08-27 from four human recordings (`recordings/2026-08-27_{174045,175143,180346,
181548}_mahogny-homes{,2,3,4}.*`) plus the decompiled reference plugin
(`data/decompiled/mahogany-homes-plugin/`, sideloaded Plugin Hub jar, documentation only).
Runs Adept-tier contracts: NPC Contact spell (widget 218.113, confirmed against both the
recordings and `runelite-api-1.12.33.jar`) requests a contract with no dialogue to navigate,
`MahoganyHomesProvider.java` tracks which home/tier is active purely from the assignment chat
message (no varbit exists for this), teleport tablets/Xeric's talisman get the player to the
right city, and a plain left-click on each hotspot's fixed object id (one id per home per slot,
stable across its whole Remove-then-Build lifecycle) does the work, relying on the user's own
client-side menu swap rather than any hijack-escalation machinery. **Not yet run live** — every
widget/interface id this depends on was verified against the recordings and the real
`runelite-api` jar rather than guessed, but `HOTSPOT_CLICK_COOLDOWN_S`/`TURN_IN_CLICK_COOLDOWN_S`
(both first-draft guesses, see the routine's own module docstring) and the overall phase timing
need a live session to confirm, the same way golem_crafting's own click cadence was corrected
after its first live run. No plank sack in this version — planks are carried loose and restocked
more often, a deliberate simplification to avoid golem_crafting's known fur-pouch stale-estimate
failure mode in a routine with no live mileage yet. See
`docs/superpowers/specs/2026-08-27-mahogany-homes-design.md` for the full design and
`docs/superpowers/plans/2026-08-27-mahogany-homes.md` for the implementation history.
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Document routines/mahogany_homes.py in CLAUDE.md"
```

---

## After this plan

Not covered here, left for the first live session (per the project's live-debugging workflow —
launch via `python -u controller.py routines/mahogany_homes.py *> <logfile> 2>&1`, poll the log,
watch one full contract end-to-end before leaving it to loop):

- Confirming `HOTSPOT_CLICK_COOLDOWN_S`/`TURN_IN_CLICK_COOLDOWN_S` against real observed timing.
- Confirming the assumption that a Mahogany Homes hotspot's Remove/Build/Repair option is never
  silently withdrawn for lack of materials the way golem_crafting's Start-golem was (see the
  design doc's "Researched facts" section) — if it turns out otherwise, `work_phase`'s
  materials check needs a second signal beyond the pre-click inventory comparison it has today.
- Whether `CITY_TELEPORTS`' Xeric's talisman charge ever runs out mid-session — no recharging
  logic exists; running dry should surface as a `bank_phase` safety-stop (no teleport item, no
  bank in range) rather than a silent stall, but this hasn't been observed live.
