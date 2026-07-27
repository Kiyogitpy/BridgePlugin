package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.kit.KitType;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Nearby *other* players and PvP-relevant state about them: what weapon they're wielding,
 * whether they're currently targeting you, their active overhead prayer icon, and skull
 * status. Meant for wilderness/PvP-world awareness -- combine with /player's inWilderness
 * flag to decide when this actually matters. Weapon classification (melee/range/mage/spec)
 * is name-based, ported from a prior WeaponClassifier of the user's -- see classifyWeapon
 * below. Add keywords to the lists there as new weapons get misclassified.
 */
public class PvpProvider extends BridgeProvider
{
	// Checked first and wins over the melee/range/mage classification below -- these are
	// weapons whose special attack matters more than their normal combat style day-to-day
	// (e.g. dragon claws are "melee" the rest of the time, but seeing one drawn is a
	// burst-damage warning, not a "pray melee" instruction). "godsword" alone matches ANY
	// godsword (Bandos/Saradomin/Zamorak/Armadyl), not just AGS specifically.
	private static final String[] SPEC_WEAPONS = {
		"godsword",
		"dragon dagger",
		"dragon claws",
		"granite maul",
		"armadyl godsword",
		"voidwaker",
		"dark bow",
		"ballista",
		"volatile nightmare staff",
		"eldritch nightmare staff",
	};

	private static final String[] RANGE_KEYWORDS = {
		"bow", "crossbow", "blowpipe", "knife", "dart", "javelin",
		"thrownaxe", "chinchompa", "atlatl",
	};

	private static final String[] MAGE_KEYWORDS = {
		"staff", "wand", "trident", "sceptre", "scepter", "kodai",
	};

	private static final String[] MELEE_KEYWORDS = {
		"sword", "scimitar", "dagger", "mace", "warhammer", "hammer", "axe",
		"halberd", "hasta", "spear", "claws", "whip", "maul", "rapier", "bludgeon",
	};

	public PvpProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/players", () -> onClient(this::getPlayers));
	}

	/**
	 * One of "melee", "range", "mage", "spec", or null if weaponName is null/empty (unarmed)
	 * or doesn't match any category. The spec check runs first and overrides the
	 * melee/range/mage classification -- see SPEC_WEAPONS above.
	 */
	static String classifyWeapon(String weaponName)
	{
		if (weaponName == null || weaponName.isEmpty())
		{
			return null;
		}

		String name = weaponName.toLowerCase();

		if (containsAny(name, SPEC_WEAPONS))
		{
			return "spec";
		}
		if (containsAny(name, RANGE_KEYWORDS))
		{
			return "range";
		}
		if (containsAny(name, MAGE_KEYWORDS))
		{
			return "mage";
		}
		if (containsAny(name, MELEE_KEYWORDS))
		{
			return "melee";
		}
		return null;
	}

	private static boolean containsAny(String name, String[] keywords)
	{
		for (String keyword : keywords)
		{
			if (name.contains(keyword))
			{
				return true;
			}
		}
		return false;
	}

	private Object getPlayers()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		Player local = client.getLocalPlayer();
		if (local == null)
		{
			return list;
		}

		Point canvasScreenLoc = RuneLiteUtil.getCanvasScreenLocation(client);

		for (Player player : client.getPlayers())
		{
			if (player == null || player == local || player.getName() == null)
			{
				continue;
			}

			Map<String, Object> data = new HashMap<>();
			WorldPoint wp = player.getWorldLocation();

			data.put("name", player.getName());
			data.put("combatLevel", player.getCombatLevel());
			data.put("x", wp.getX());
			data.put("y", wp.getY());
			data.put("plane", wp.getPlane());
			data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
			data.put("attackingYou", player.getInteracting() == local);
			data.put("animation", player.getAnimation());
			data.put("skulled", player.getSkullIcon() != -1);
			data.put("friend", player.isFriend());
			data.put("clanMember", player.isClanMember());
			data.put("friendsChatMember", player.isFriendsChatMember());

			HeadIcon overhead = player.getOverheadIcon();
			if (overhead != null)
			{
				data.put("overheadIcon", overhead.name());
			}

			PlayerComposition comp = player.getPlayerComposition();
			if (comp != null)
			{
				int weaponEquipmentId = comp.getEquipmentId(KitType.WEAPON);
				if (weaponEquipmentId >= PlayerComposition.ITEM_OFFSET)
				{
					int itemId = weaponEquipmentId - PlayerComposition.ITEM_OFFSET;
					data.put("weaponId", itemId);
					ItemComposition itemComp = client.getItemDefinition(itemId);
					if (itemComp != null)
					{
						data.put("weaponName", itemComp.getName());
						data.put("weaponType", classifyWeapon(itemComp.getName()));
					}
				}
			}

			net.runelite.api.Point point = RuneLiteUtil.localToScreen(client, player.getLocalLocation(), wp.getPlane());
			if (point != null)
			{
				data.put("screenX", point.getX() + canvasScreenLoc.x);
				data.put("screenY", point.getY() + canvasScreenLoc.y);
			}

			list.add(data);
		}

		return list;
	}
}
