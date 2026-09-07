package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.InstanceCoords;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class PlayerProvider extends BridgeProvider
{
	/**
	 * Bit 0x4 of a tile's render-rule byte: the tile is under a roof, i.e. indoors.
	 *
	 * VERIFIED live 2026-08-28 by walking East Ardougne and sampling every tile, so the proof is a
	 * TRANSITION rather than one reading that could have been coincidence: 30-odd consecutive
	 * outdoor tiles all read tileSettings 0 / underRoof false, (2661,3295) and (2660,3295) under a
	 * roofed structure both read 4 / true, and (2659,3296) went straight back to 0 / false. The
	 * flag flips with the bit in both directions.
	 *
	 * `tileSettings` is still published raw beside `underRoof` on purpose: these bit meanings are
	 * game-data convention that no javadoc states (0x2 = bridge is the only other one proven here,
	 * via data/decompiled/target-true-tile/PerspectiveUtil.java), so anything reading a NEW bit
	 * later can measure it the same way instead of trusting a constant.
	 */
	private static final int ROOF_TILE_SETTING = 0x4;

	// Fed from ControllerPlugin.onClientTick; the HTTP fallback for PLAYER_MOVING/STOPPED.
	private final com.example.util.PlayerMotionTracker motionTracker;

	public PlayerProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
		com.example.util.PlayerMotionTracker motionTracker)
	{
		super(client, clientThread, server);
		this.motionTracker = motionTracker;
	}

	@Override
	public void register()
	{
		server.register("/player", () -> onClient(this::getPlayer));
		server.registerQuery("/varbit", query -> onClient(() -> getVarbit(query)));
	}

	// ADDED 2026-08-22 (user directive, Blast Furnace live session): generic passthrough to
	// client.getVarbitValue(id) -- not tied to any one routine's data model, same "expose the
	// raw client primitive, let the Python side interpret it" idiom /player's other fields
	// already follow. First use: routines/blast_furnace.py reading the bar-dispenser-ready-count
	// varbit the official RuneLite Blast Furnace plugin itself reads (BarsOres.java in
	// net.runelite.client.plugins.blastfurnace, e.g. varbit 943 for Steel bar) to confirm a
	// dispenser collection actually happened instead of trusting a blind click+Space.
	private Object getVarbit(Map<String, String> query)
	{
		Map<String, Object> result = new HashMap<>();
		String idStr = query.get("id");
		if (idStr == null || idStr.isEmpty())
		{
			result.put("value", null);
			return result;
		}
		try
		{
			result.put("value", client.getVarbitValue(Integer.parseInt(idStr)));
		}
		catch (NumberFormatException e)
		{
			result.put("value", null);
		}
		return result;
	}

	private Object getPlayer()
	{
		Player p = client.getLocalPlayer();
		if (p == null)
		{
			return Collections.emptyMap();
		}

		// Not p.getWorldLocation() directly -- that's local to whatever WorldView the player is
		// currently registered in, which is meaningless outside it (e.g. standing on a Sailing
		// boat's own nested WorldView). This resolves to the true top-level-world position either
		// way, matching what ObjectProvider/NPCProvider's coordinates already are.
		WorldPoint wp = RuneLiteUtil.getTopLevelWorldLocation(client, p);

		LocalPoint local = p.getLocalLocation();

		// Instance-template correction -- CONFIRMED live 2026-08-14 (Pest Control routine
		// live-debugging): inside any instanced area (a minigame, a dungeon, a boss room), wp
		// above is still a raw instance-template coordinate (observed x~13600, regionId~54000
		// aboard a Pest Control boat), not the real reachable map location a routine's own
		// hardcoded tiles/region ids are written against -- the exact same bug class already
		// fixed once for SafespotMarker.java (Blast Furnace, see its own comment) but left
		// unaddressed here (flagged, not fixed, in slayer.py's SAFESPOT_MAX_RETURN_DISTANCE
		// comment). WorldPoint.fromLocalInstance() does the same chunk-remapping SafespotMarker
		// now uses, degrading to a plain WorldPoint.fromLocal() when not actually inside an
		// instance -- safe to apply unconditionally. Only applied for the common
		// non-nested-WorldView case; a player aboard a Sailing boat's own WorldView isn't
		// expected to also be inside an instance, so getTopLevelWorldLocation's boat handling
		// above takes priority there instead.
		if (local != null && local.getWorldView() <= WorldView.TOPLEVEL)
		{
			wp = WorldPoint.fromLocalInstance(client, local);
		}

		Map<String, Object> data = new HashMap<>();

		data.put("name", p.getName());
		data.put("combatLevel", p.getCombatLevel());
		data.put("x", wp.getX());
		data.put("y", wp.getY());
		data.put("regionId", wp.getRegionID());

		// Instance-space position (2026-09-05, see InstanceCoords): the coordinate the walk
		// endpoints accept with inst=true. Equal to x/y outside an instance.
		WorldView topLevel = client.getTopLevelWorldView();
		boolean inInstance = topLevel != null && topLevel.isInstance();
		data.put("inInstance", inInstance);
		if (topLevel != null)
		{
			data.put("baseX", topLevel.getBaseX());
			data.put("baseY", topLevel.getBaseY());
		}
		if (local != null && local.getWorldView() <= WorldView.TOPLEVEL)
		{
			WorldPoint inst = InstanceCoords.instancePoint(client, local, wp.getPlane());
			data.put("instX", inst.getX());
			data.put("instY", inst.getY());
		}
		else
		{
			data.put("instX", wp.getX());
			data.put("instY", wp.getY());
		}

		// C1 (2026-09-05, docs/superpowers/sdd/2026-09-05-gauntlet-instance-plumbing/
		// final-fix-brief.md): the LIVE plane, as opposed to `plane` below (wp.getPlane(),
		// WorldPoint.fromLocalInstance's TEMPLATE plane inside an instance -- see CLAUDE.md's
		// instancing gotcha). instance_nav.walk_to must route/hop on the collision grid's own
		// plane, never on /player's -- this is the direct read for anything that needs the true
		// plane without going through the grid at all. 0 when the view is null, same convention
		// as `inInstance` above.
		data.put("instPlane", topLevel != null ? topLevel.getPlane() : 0);

		if (local != null)
		{
			data.put("localX", local.getX());
			data.put("localY", local.getY());
			// The tile's RENDER HEIGHT -- see ObjectProvider's own tileHeight comment. The pair
			// is what tells the Motherlode Mine's two floors apart: the mine's upper level sits
			// directly above the lower one at the same x/y, so this is the only field on the
			// bridge that separates them, and a routine compares an object's against ours.
			data.put("tileHeight", Perspective.getTileHeight(client, local, local.getWorldView() >= 0
				? client.getTopLevelWorldView().getPlane() : 0));
		}

		// ADDED 2026-08-28 (user request): indoors/outdoors, which `plane` alone cannot answer --
		// plane is the STOREY, and plane 0 covers both the open street and a house's ground floor.
		// A roof over the tile is the game's own notion of "inside", and it is exactly what
		// changes when you cross a door, so this is the signal for "did we actually get in".
		//
		// Read from the EXTENDED scene: the reference plugin data/decompiled/target-true-tile/
		// PerspectiveUtil.java indexes getExtendedTileSettings() as [plane][sceneX + 40][sceneY +
		// 40] over a 184-wide array (104 + 2*40). The offset is derived from the array's own width
		// rather than hardcoded to 40, so a client that changes the extended scene size can't turn
		// this into a silent off-by-N read of the wrong tile.
		//
		// `tileSettings` is the raw byte alongside it, deliberately kept: the bit meanings are
		// game-data conventions no javadoc states (0x2 = bridge/render-one-plane-up is the one the
		// reference plugin proves), so anything reading a NEW bit later can verify it against real
		// values instead of trusting a constant, the same way ROOF_TILE_SETTING was verified here.
		WorldView roofView = p.getWorldView();
		if (roofView != null && local != null && local.getWorldView() <= WorldView.TOPLEVEL)
		{
			Scene scene = roofView.getScene();
			byte[][][] settings = scene != null ? scene.getExtendedTileSettings() : null;
			int plane = wp.getPlane();
			if (settings != null && plane >= 0 && plane < settings.length && settings[plane] != null)
			{
				int offset = (settings[plane].length - Constants.SCENE_SIZE) / 2;
				int sx = local.getSceneX() + offset;
				int sy = local.getSceneY() + offset;
				if (sx >= 0 && sx < settings[plane].length
					&& sy >= 0 && settings[plane][sx] != null && sy < settings[plane][sx].length)
				{
					byte tileSetting = settings[plane][sx][sy];
					data.put("tileSettings", tileSetting);
					data.put("underRoof", (tileSetting & ROOF_TILE_SETTING) != 0);
				}
			}
		}

		// The boat's own reference position, separate from the player's own foot position above --
		// confirmed live 2026-08-02: standing at one end of a Sailing boat (its own WorldView is
		// only ~8 tiles per side) can be several tiles from the opposite end, which matters for a
		// routine's maxDistance-style range check against something the *boat* needs to be near,
		// not specifically wherever the player happens to be standing on deck. x/y above stays the
		// player's own foot position -- e.g. sailing.py's hop-based steering math wants that, not
		// this. null (both fields omitted) when not aboard a WorldEntity at all.
		WorldPoint boatWp = RuneLiteUtil.getBoatOrActorWorldLocation(client, p);

		// I4 (shipwreck salvaging final-review fix wave, 2026-09-07): resolved and emitted
		// UNCONDITIONALLY, independent of the boatWp/wp tile comparison below. boatViewId used to
		// sit inside that `if`, so it vanished on exactly the tick the player's own tile happened
		// to equal the boat's reference tile -- which the player crosses constantly just walking
		// the deck. sailing.find_facilities keys its own-boat-vs-neighbour filter on boatViewId,
		// so that tick silently fell back to nearest-by-name, indistinguishable on a crowded mass
		// world from a neighbouring boat's identical fixture. Everything else in this block
		// (boatX/Y/Plane, hull width/height) stays gated on the tile comparison -- those describe
		// a boat position DISTINCT from the player's own foot position, which is meaningless to
		// report when the two tiles coincide, unlike boatViewId which identifies the boat itself.
		WorldEntity boat = RuneLiteUtil.findTopLevelWorldEntityContaining(client, p.getWorldView());
		if (boat != null && boat.getWorldView() != null)
		{
			data.put("boatViewId", boat.getWorldView().getId());
		}

		if (!boatWp.equals(wp))
		{
			data.put("boatX", boatWp.getX());
			data.put("boatY", boatWp.getY());
			data.put("boatPlane", boatWp.getPlane());

			// True hull footprint, in tiles -- RuneLite's own WorldEntityConfig bounds on the
			// boat's WorldEntity, not a guessed size. Modeled on the reference usage in the
			// sideloaded Sailing plugin's TrueTileIndicator.renderBoatArea (see
			// data/decompiled/sailing-plugin/). 128 local units per tile is RuneLite's standard
			// LOCAL_TILE_SIZE conversion factor. Omitted (like the boat fields above) whenever
			// the boat's WorldEntity or its config isn't resolvable.
			// UNVERIFIED live as of 2026-08-02 -- values not yet cross-checked against an actual
			// boat's known size class in-game.
			WorldEntityConfig boatConfig = boat != null ? boat.getConfig() : null;
			if (boatConfig != null)
			{
				data.put("boatWidth", boatConfig.getBoundsWidth() / 128.0);
				data.put("boatHeight", boatConfig.getBoundsHeight() / 128.0);
			}
		}

		// General-purpose debug fields: direct visibility into which WorldView the player is
		// currently registered in and how big its scene is. Not consumed by any routine -- kept
		// for the same reason as GameStateProvider's /debug-menu (see its comment): this is what
		// confirmed the player's own WorldView (3078) differed from the Helm's actual clickable
		// WorldView (3083) on 2026-08-02, which ObjectProvider's own output couldn't reveal.
		WorldView pv = p.getWorldView();
		if (pv != null)
		{
			data.put("debugWorldViewId", pv.getId());
			data.put("debugWorldViewIsTopLevel", pv.isTopLevel());
			data.put("debugWorldViewSizeX", pv.getSizeX());
			data.put("debugWorldViewSizeY", pv.getSizeY());
			data.put("debugWorldViewPlane", pv.getPlane());
			Scene pvScene = pv.getScene();
			data.put("debugWorldViewSceneNull", pvScene == null);
		}
		data.put("plane", wp.getPlane());
		data.put("inWilderness", client.getVarbitValue(Varbits.IN_WILDERNESS) != 0);
		// Varbit 3956 -- matches the official RuneLite Nightmare Zone plugin's own
		// checkAbsorption() (client.getVarbitValue(3956)); no named Varbits constant exists for
		// it even in that plugin, so match it exactly with the same raw id. Read unconditionally
		// (cheap, harmless outside NMZ) rather than gating on region, same as inWilderness above.
		data.put("nmzAbsorptionPoints", client.getVarbitValue(3956));

		Widget wildernessLevelWidget = client.getWidget(WidgetInfo.PVP_WILDERNESS_LEVEL);
		if (wildernessLevelWidget != null && !wildernessLevelWidget.isHidden())
		{
			String text = wildernessLevelWidget.getText();
			data.put("wildernessLevelText", text);

			if (text != null)
			{
				Matcher matcher = Pattern.compile("\\d+").matcher(text);
				if (matcher.find())
				{
					data.put("wildernessLevel", Integer.parseInt(matcher.group()));
				}
			}
		}
		data.put("animation", p.getAnimation());
		data.put("poseAnimation", p.getPoseAnimation());
		// Sub-tick: true while the interpolated local position is sliding this frame
		// (PlayerMotionTracker, fed from ClientTick). The HTTP fallback for PLAYER_MOVING/STOPPED.
		data.put("moving", motionTracker.isMoving());
		data.put("hp", client.getBoostedSkillLevel(Skill.HITPOINTS));
		data.put("hpReal", client.getRealSkillLevel(Skill.HITPOINTS));
		data.put("prayer", client.getBoostedSkillLevel(Skill.PRAYER));
		data.put("prayerReal", client.getRealSkillLevel(Skill.PRAYER));
		data.put("hitpointsXp", client.getSkillExperience(Skill.HITPOINTS));
		data.put("thievingXp", client.getSkillExperience(Skill.THIEVING));
		data.put("craftingXp", client.getSkillExperience(Skill.CRAFTING));
		data.put("constructionXp", client.getSkillExperience(Skill.CONSTRUCTION));
		data.put("smithingXp", client.getSkillExperience(Skill.SMITHING));
		data.put("runecraftXp", client.getSkillExperience(Skill.RUNECRAFT));
		data.put("miningXp", client.getSkillExperience(Skill.MINING));
		data.put("woodcuttingXp", client.getSkillExperience(Skill.WOODCUTTING));

		data.put("runEnergy", client.getEnergy() / 100);
		data.put("specialAttackPercent", client.getVarpValue(VarPlayer.SPECIAL_ATTACK_PERCENT) / 10);
		data.put("specialAttackEnabled", client.getVarpValue(VarPlayer.SPECIAL_ATTACK_ENABLED) == 1);

		int poison = client.getVarpValue(VarPlayer.POISON);
		data.put("poisoned", poison > 0);
		data.put("poisonValue", poison);

		int disease = client.getVarpValue(VarPlayer.DISEASE_VALUE);
		data.put("diseased", disease > 0);

		ItemContainer equipment = client.getItemContainer(InventoryID.EQUIPMENT);
		if (equipment != null)
		{
			Item[] eq = equipment.getItems();

			if (eq.length > 3)
			{
				Item weapon = eq[3];
				if (weapon != null && weapon.getId() != -1)
				{
					ItemComposition comp = client.getItemDefinition(weapon.getId());
					data.put("weaponId", weapon.getId());
					data.put("weaponQty", weapon.getQuantity());
					if (comp != null)
					{
						data.put("weaponName", comp.getName());
					}
				}
			}

			if (eq.length > 13)
			{
				Item ammo = eq[13];
				if (ammo != null && ammo.getId() != -1)
				{
					ItemComposition comp = client.getItemDefinition(ammo.getId());
					data.put("ammoId", ammo.getId());
					data.put("ammoQty", ammo.getQuantity());
					if (comp != null)
					{
						data.put("ammoName", comp.getName());
					}
				}
			}
		}

		LocalPoint dest = client.getLocalDestinationLocation();
		if (dest != null)
		{
			WorldPoint destination = WorldPoint.fromLocal(client, dest);
			data.put("destinationX", destination.getX());
			data.put("destinationY", destination.getY());
		}

		return data;
	}
}
