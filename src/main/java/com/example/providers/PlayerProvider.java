package com.example.providers;

import com.example.bridge.BridgeHttpServer;
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
	public PlayerProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/player", () -> onClient(this::getPlayer));
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
		Map<String, Object> data = new HashMap<>();

		data.put("name", p.getName());
		data.put("combatLevel", p.getCombatLevel());
		data.put("x", wp.getX());
		data.put("y", wp.getY());

		// The boat's own reference position, separate from the player's own foot position above --
		// confirmed live 2026-08-02: standing at one end of a Sailing boat (its own WorldView is
		// only ~8 tiles per side) can be several tiles from the opposite end, which matters for a
		// routine's maxDistance-style range check against something the *boat* needs to be near,
		// not specifically wherever the player happens to be standing on deck. x/y above stays the
		// player's own foot position -- e.g. sailing.py's hop-based steering math wants that, not
		// this. null (both fields omitted) when not aboard a WorldEntity at all.
		WorldPoint boatWp = RuneLiteUtil.getBoatOrActorWorldLocation(client, p);
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
			WorldEntity boat = RuneLiteUtil.findTopLevelWorldEntityContaining(client, p.getWorldView());
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
		data.put("hp", client.getBoostedSkillLevel(Skill.HITPOINTS));
		data.put("hpReal", client.getRealSkillLevel(Skill.HITPOINTS));
		data.put("prayer", client.getBoostedSkillLevel(Skill.PRAYER));
		data.put("prayerReal", client.getRealSkillLevel(Skill.PRAYER));
		data.put("hitpointsXp", client.getSkillExperience(Skill.HITPOINTS));

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
