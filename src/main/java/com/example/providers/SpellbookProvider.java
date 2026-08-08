package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.Map;

/**
 * Telekinetic Grab's widget position, for golem_crafting.py's Goat Pit hunt phase (see
 * docs/superpowers/specs/2026-08-08-goat-pit-telekinetic-grab-design.md). Scoped to this one
 * spell rather than a general spellbook lookup -- nothing else in this codebase casts spells,
 * and InterfaceID.MagicSpellbook.TELEGRAB already gives a direct, stable widget id, so there's
 * no name-matching/iteration step that would need to generalize across all spells anyway.
 */
public class SpellbookProvider extends BridgeProvider
{
	public SpellbookProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/spellbook", () -> onClient(this::getTelekineticGrab));
	}

	private Object getTelekineticGrab()
	{
		Map<String, Object> data = new HashMap<>();

		// ASSUMPTION, NOT YET VERIFIED LIVE -- InterfaceID.MagicSpellbook.TELEGRAB is confirmed
		// to exist in the RuneLite API jar (javap inspection), but not yet confirmed to resolve
		// to a real widget once the Magic tab is actually open in this game. Verify via this
		// endpoint before trusting hunt_phase's Grab-casting unattended.
		Widget grab = client.getWidget(InterfaceID.MagicSpellbook.TELEGRAB);
		if (grab == null || grab.isHidden())
		{
			data.put("visible", false);
			return data;
		}

		Rectangle bounds = grab.getBounds();
		if (bounds == null || bounds.width <= 0 || bounds.height <= 0)
		{
			data.put("visible", false);
			return data;
		}

		Point screen = RuneLiteUtil.canvasToScreen(client, bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
		data.put("visible", true);
		data.put("screenX", screen.x);
		data.put("screenY", screen.y);
		return data;
	}
}
