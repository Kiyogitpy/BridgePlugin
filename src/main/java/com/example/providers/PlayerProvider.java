package com.example.providers;

import com.example.bridge.BridgeHttpServer;
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

		WorldPoint wp = p.getWorldLocation();
		Map<String, Object> data = new HashMap<>();

		data.put("name", p.getName());
		data.put("combatLevel", p.getCombatLevel());
		data.put("x", wp.getX());
		data.put("y", wp.getY());
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
