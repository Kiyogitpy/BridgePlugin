package com.example.gauntletrecolor;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.GameStateChanged;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.inject.Inject;
import java.awt.Color;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.Random;
import java.util.Set;

/**
 * Randomizes the Corrupted Gauntlet's 117HD LIGHTING each time you return to the lobby -- the
 * same trigger the "CG recolor" Plugin Hub addon (author Denaelc,
 * ~/.runelite/plugins/cg-recolor_*.jar) uses for its own "random" mode, decompiled 2026-09-10.
 * That addon can't run alongside 117HD ({@code conflicts = {"117 HD"}}); this one does not
 * touch model face colors at all, so it has no such conflict.
 *
 * <p>LIGHTING ONLY, BY DECISION. Two of 117HD's data files are rewritten, both read from paths
 * given by JVM properties (set in ~/.gradle/gradle.properties's clientJvmArgs) and both watched
 * live by 117HD itself, so a rewrite lands in-game with no client restart:
 * <ul>
 *   <li>{@code environments.json} ({@code rlhd.environments-path},
 *       rs117.hd.scene.EnvironmentManager) -- THE_GAUNTLET_CORRUPTED's ambient/directional
 *       light color.</li>
 *   <li>{@code lights.json} ({@code rlhd.lights-path}, rs117.hd.scene.LightManager) -- the 20
 *       per-object point lights the corrupted variant hardcodes to {@code [255, 0, 0]}. The
 *       Hunllef's alone is radius 800, strength 12.5; without this file every environment tint
 *       still reads red.</li>
 * </ul>
 * Light only MODULATES the geometry underneath, and the Gauntlet's geometry is authored red, so
 * a muted color leaves it looking red -- MEASURED 2026-09-10: pure green tinted the room
 * visibly, a pastel from a "peaceful" palette did not. Hence the SATURATED draw in
 * {@link #randomPalette()}. USER 2026-09-10, after the alternative was tried and reverted:
 * "go back to saturated colors, which visibly tint".
 *
 * <p>WHAT WAS TRIED AND REVERTED THE SAME DAY, so nobody re-derives it: recoloring the model
 * FACE COLORS (what cg-recolor does), with 117HD's {@code model_overrides.json} hue predicates
 * rewritten to match (they select each face's MATERIAL by testing its live color, so a face
 * recolor silently retextures things otherwise -- the likely reason for cg-recolor's conflict
 * declaration). It worked in principle but two things broke live. (1) A uniform hue shift
 * dragged the ~20k bark faces (h=10) along with the red (h=0/62/63) -- fixable, and was. (2)
 * The recolor hooked PreMapLoad, which fires on the MAP LOADER thread, and called
 * {@code Renderable.getModel()} there: "AssertionError: must be called on client thread",
 * which ABORTED the map load partway, leaving the previous scene half-retained under the new
 * one -- USER: "it's somehow bringing along the lobby to the gauntlet", seen as offset floors
 * and black rooms. cg-recolor's own {@code verifyPreMapLoadModel} accepts only
 * {@code instanceof Model} and never calls getModel() for exactly that reason. Not fixed; USER
 * chose the lighting-only path instead.
 */
@PluginDescriptor(
	name = "Gauntlet Recolor (117HD)",
	description = "Randomizes the Corrupted Gauntlet's 117HD lighting color on every lobby visit",
	tags = {"gauntlet", "117hd", "recolor", "corrupted gauntlet"}
)
public class GauntletRecolorPlugin extends Plugin
{
	private static final Logger log = LoggerFactory.getLogger(GauntletRecolorPlugin.class);

	// From cg-recolor's decompiled RecolorCG.java -- REGION_ID_GAUNTLET_LOBBY. The only trigger
	// that addon's "random" mode uses to re-roll: back in the lobby, prepping the next attempt.
	private static final int REGION_ID_GAUNTLET_LOBBY = 12127;

	private static final Set<String> RECOLOR_AREAS = Set.of("THE_GAUNTLET_CORRUPTED");

	// Matches the -Drlhd.environments-path / -Drlhd.lights-path values in clientJvmArgs. Not
	// read from those properties on purpose -- this plugin only needs to agree with 117HD on
	// where the files live, not with how 117HD was told to find them.
	private static final Path RUNELITE_DIR = Paths.get(System.getProperty("user.home"), ".runelite");
	private static final Path ENVIRONMENTS_PATH = RUNELITE_DIR.resolve("117hd-environments-override.json");
	private static final Path LIGHTS_PATH = RUNELITE_DIR.resolve("117hd-lights-override.json");

	/**
	 * MEASURED 2026-09-10 the region read flips back and forth while the player walks around
	 * the lobby ("entered the lobby" fired four times in 40s), and the previousRegionId edge
	 * guard alone does not hold it to one fire. Without a floor the lighting would change every
	 * ~13s while standing in the lobby.
	 */
	private static final long REROLL_MIN_INTERVAL_MS = 30_000;

	// USER 2026-09-10: "we dont want them all the exact same color ... pick three similar colors
	// and mix match". PALETTE_SIZE colors from one base hue, PALETTE_HUE_SPREAD apart -- an
	// analogous palette, the same shape the stock ambient #bb9eae / directional #c58c9e pair is.
	private static final int PALETTE_SIZE = 3;
	private static final float PALETTE_HUE_SPREAD = 0.07f; // ~25 degrees per step

	// HSB draw bounds. Saturation is deliberately HIGH -- see the class doc: a pastel tint
	// cannot mask red geometry, a saturated one can. Brightness stays floored so no roll can
	// land as dark as the #0595d2 (luma ~113 vs the stock ~173) that a plain RGB draw produced.
	private static final float SATURATION_MIN = 0.80f;
	private static final float SATURATION_MAX = 1.00f;
	private static final float BRIGHTNESS_MIN = 0.85f;

	/**
	 * Perceptual floor, applied AFTER the HSB draw. HSB "brightness" is max(R,G,B) and says
	 * nothing about how bright a color LOOKS: pure blue at brightness 1.0 has luma ~29, pure
	 * yellow ~226. USER 2026-09-10, on a blue/purple roll (#1ba2e0 / #1e52e1 / #3715ef, lumas
	 * 116 / 74 / 44): "this color combination makes it too dark to see" -- every blue and purple
	 * hue sails through the HSB floor and lands near-black. {@link #liftToLuma} blends such a
	 * color toward white until it meets this, keeping the hue. Reference points: the stock
	 * pastel #bb9eae is ~173, the green test that looked right (#00ff00) ~150, the #0595d2 that
	 * was already "slightly too dark" ~113.
	 */
	private static final float LUMA_MIN = 150f;

	// 117HD's own stock values for THE_GAUNTLET_CORRUPTED and for every one of the 20 corrupted
	// point lights, read out of the vendored environments.json / lights.json 2026-09-10. Written
	// back on shutDown() so switching the plugin off in the RuneLite plugin list returns the
	// Gauntlet to the default look -- without this, the override files would keep the last
	// rolled palette and 117HD would keep rendering it with the plugin off. Hardcoded rather
	// than kept in a "pristine" side file on purpose: a side file is one more thing that can go
	// stale, and these are three constants.
	private static final String STOCK_AMBIENT = "#bb9eae";
	private static final String STOCK_DIRECTIONAL = "#c58c9e";
	private static final Color STOCK_LIGHT = Color.RED;

	private final Random random = new Random();
	private final Gson gson = new GsonBuilder().setPrettyPrinting().create();

	@Inject
	private Client client;

	private Integer previousRegionId;
	private long lastRerollMs;

	@Override
	protected void startUp()
	{
		previousRegionId = null;
		reroll("startup");
	}

	/**
	 * Toggling the plugin off in the RuneLite plugin list is THE off switch: this puts 117HD's
	 * stock colors back into both override files, and 117HD's own file watcher picks them up
	 * live, so the Gauntlet is back to default with no restart.
	 */
	@Override
	protected void shutDown()
	{
		boolean env = writeEnvironment(STOCK_AMBIENT, STOCK_DIRECTIONAL);
		boolean lights = writeLights(new Color[]{STOCK_LIGHT});
		log.debug("gauntlet-recolor: restored 117HD defaults (env={}, lights={})", env, lights);
	}

	@Subscribe
	public void onGameStateChanged(GameStateChanged event)
	{
		if (event.getGameState() != GameState.LOGGED_IN)
		{
			return;
		}

		Integer regionId = currentRegionId();
		boolean enteredLobby = regionId != null && regionId == REGION_ID_GAUNTLET_LOBBY
			&& (previousRegionId == null || previousRegionId != REGION_ID_GAUNTLET_LOBBY);
		if (regionId != null)
		{
			previousRegionId = regionId;
		}

		if (!enteredLobby)
		{
			return;
		}
		if (System.currentTimeMillis() - lastRerollMs < REROLL_MIN_INTERVAL_MS)
		{
			log.debug("gauntlet-recolor: lobby entry within {}ms of the last reroll -- skipping",
				REROLL_MIN_INTERVAL_MS);
			return;
		}
		reroll("entered the lobby");
	}

	private Integer currentRegionId()
	{
		if (client.getLocalPlayer() == null)
		{
			return null;
		}
		LocalPoint local = client.getLocalPlayer().getLocalLocation();
		if (local == null)
		{
			return null;
		}
		// Instance-template correction -- same call PlayerProvider uses for /player's regionId
		// (see CLAUDE.md's instanced-area gotcha). The Gauntlet lobby/instance both need it.
		return WorldPoint.fromLocalInstance(client, local).getRegionID();
	}

	/**
	 * PALETTE_SIZE colors sharing one base hue (drawn from the full 0-360 range), each offset by
	 * a multiple of PALETTE_HUE_SPREAD with its own saturation/brightness draw inside the bounds.
	 * Drawing in HSB rather than per-channel RGB is what gives every roll real hue variety --
	 * MEASURED: clamping each RGB channel to a floor capped saturation and made every roll a
	 * near-white pastel with a faint orange lean.
	 */
	private Color[] randomPalette()
	{
		float baseHue = random.nextFloat();
		Color[] palette = new Color[PALETTE_SIZE];
		for (int i = 0; i < PALETTE_SIZE; i++)
		{
			float hue = baseHue + (i - (PALETTE_SIZE - 1) / 2.0f) * PALETTE_HUE_SPREAD;
			hue = ((hue % 1.0f) + 1.0f) % 1.0f;
			float saturation = SATURATION_MIN + random.nextFloat() * (SATURATION_MAX - SATURATION_MIN);
			float brightness = BRIGHTNESS_MIN + random.nextFloat() * (1.0f - BRIGHTNESS_MIN);
			palette[i] = liftToLuma(Color.getHSBColor(hue, saturation, brightness), LUMA_MIN);
		}
		return palette;
	}

	private static float luma(Color c)
	{
		return 0.299f * c.getRed() + 0.587f * c.getGreen() + 0.114f * c.getBlue();
	}

	/**
	 * Blends {@code c} toward white by exactly the fraction needed to reach {@code minLuma}.
	 * Luma is linear in RGB, so {@code luma' = luma + (255 - luma) * t} and the fraction solves
	 * in closed form. A color already at or above the floor is returned as-is.
	 */
	private static Color liftToLuma(Color c, float minLuma)
	{
		float current = luma(c);
		if (current >= minLuma || current >= 255f)
		{
			return c;
		}
		float t = (minLuma - current) / (255f - current);
		return new Color(
			Math.round(c.getRed() + (255 - c.getRed()) * t),
			Math.round(c.getGreen() + (255 - c.getGreen()) * t),
			Math.round(c.getBlue() + (255 - c.getBlue()) * t));
	}

	private static String toHex(Color c)
	{
		return String.format("#%02x%02x%02x", c.getRed(), c.getGreen(), c.getBlue());
	}

	private void reroll(String reason)
	{
		lastRerollMs = System.currentTimeMillis();
		Color[] palette = randomPalette();
		// ambientColor gets palette[0], directionalColor palette[1] -- two related-but-distinct
		// tones, matching the shape the stock #bb9eae / #c58c9e pair has, not one flat color.
		boolean env = writeEnvironment(toHex(palette[0]), toHex(palette[Math.min(1, palette.length - 1)]));
		boolean lights = writeLights(palette);
		if (env || lights)
		{
			StringBuilder colors = new StringBuilder();
			for (Color c : palette)
			{
				colors.append(toHex(c)).append(' ');
			}
			log.debug("gauntlet-recolor: {} -> palette {}", reason, colors.toString().trim());
		}
	}

	/** Sets THE_GAUNTLET_CORRUPTED's two light colors; used by both the reroll and the restore. */
	private boolean writeEnvironment(String ambientHex, String directionalHex)
	{
		JsonArray root = readJsonArray(ENVIRONMENTS_PATH);
		if (root == null)
		{
			return false;
		}

		int changed = 0;
		for (JsonElement element : root)
		{
			JsonObject obj = element.getAsJsonObject();
			if (obj.has("area") && RECOLOR_AREAS.contains(obj.get("area").getAsString()))
			{
				obj.addProperty("ambientColor", ambientHex);
				obj.addProperty("directionalColor", directionalHex);
				changed++;
			}
		}

		if (changed == 0)
		{
			log.warn("gauntlet-recolor: no matching area in {} -- nothing changed", ENVIRONMENTS_PATH);
			return false;
		}
		return writeJsonArray(ENVIRONMENTS_PATH, root);
	}

	/**
	 * Every matched light gets one color from {@code cycle}, round-robin by encounter order --
	 * so the Hunllef, the corrupted monsters, the chest and the resource nodes don't all glow the
	 * identical shade, with no per-object bookkeeping. A one-element cycle sets them all the
	 * same, which is how the restore puts every light back to stock red.
	 */
	private boolean writeLights(Color[] cycle)
	{
		JsonArray root = readJsonArray(LIGHTS_PATH);
		if (root == null)
		{
			return false;
		}

		int changed = 0;
		for (JsonElement element : root)
		{
			JsonObject obj = element.getAsJsonObject();
			if (isCorruptedGauntletLight(obj))
			{
				Color c = cycle[changed % cycle.length];
				JsonArray colorArr = new JsonArray();
				colorArr.add(c.getRed());
				colorArr.add(c.getGreen());
				colorArr.add(c.getBlue());
				obj.add("color", colorArr);
				changed++;
			}
		}

		if (changed == 0)
		{
			log.warn("gauntlet-recolor: no matching light in {} -- nothing changed", LIGHTS_PATH);
			return false;
		}
		return writeJsonArray(LIGHTS_PATH, root);
	}

	/**
	 * VERIFIED against the vendored lights.json 2026-09-10: matches exactly the 20 Gauntlet
	 * corrupted-variant lights (Hunllef, every corrupted monster, the reward chest, tool storage,
	 * range, singing bowl, lit nodes, resource nodes) with zero false positives.
	 *
	 * <p>IDENTITY-ONLY ON PURPOSE. The first version also required the baked {@code color} to be
	 * [255,0,0], as the discriminator against the two lookalikes carrying Gauntlet-HM ids
	 * (PRIF_TELEPORT_PLATFORM, GAUNTLET_ENTRANCE -- both cyan, shared with Zalcano and the
	 * Prifddinas library). That worked for exactly ONE reroll: once a reroll moves a matched
	 * entry off [255,0,0] it never matches again, and the client log showed "no matching light
	 * -- nothing changed" on every lobby entry after the first. A target set cannot be defined
	 * by a property the code is about to overwrite. GAUNTLET_FISHING_SPOT is the one ambiguous
	 * description (reused by the normal and corrupted variants), so it is the one case still
	 * needing an id check, on the "_HM" suffix.
	 */
	private static boolean isCorruptedGauntletLight(JsonObject obj)
	{
		String description = obj.has("description") ? obj.get("description").getAsString() : "";
		if (description.startsWith("CORRUPTED_"))
		{
			return true;
		}
		if (description.equals("GAUNTLET_FISHING_SPOT"))
		{
			for (String key : new String[]{"objectIds", "npcIds"})
			{
				if (!obj.has(key))
				{
					continue;
				}
				for (JsonElement idEl : obj.getAsJsonArray(key))
				{
					if (idEl.getAsString().endsWith("_HM"))
					{
						return true;
					}
				}
			}
		}
		return false;
	}

	private JsonArray readJsonArray(Path path)
	{
		if (!Files.isRegularFile(path))
		{
			log.debug("gauntlet-recolor: {} does not exist, skipping", path);
			return null;
		}
		try
		{
			String content = new String(Files.readAllBytes(path), StandardCharsets.UTF_8);
			return new JsonParser().parse(content).getAsJsonArray();
		}
		catch (IOException | RuntimeException e)
		{
			log.warn("gauntlet-recolor: failed to read {}", path, e);
			return null;
		}
	}

	private boolean writeJsonArray(Path path, JsonArray array)
	{
		try
		{
			Path tmp = path.resolveSibling(path.getFileName() + ".tmp");
			Files.write(tmp, gson.toJson(array).getBytes(StandardCharsets.UTF_8));
			// Atomic replace, so 117HD's FileWatcher never reads a half-written file mid-write.
			Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING);
			return true;
		}
		catch (IOException | RuntimeException e)
		{
			// Swallowed on purpose (same discipline as BankMemoryProvider's flush()): a failed
			// reroll costs one visually-unchanged run, never the client.
			log.warn("gauntlet-recolor: failed to write {}", path, e);
			return false;
		}
	}
}
