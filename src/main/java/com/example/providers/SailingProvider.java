package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Constants;
import net.runelite.api.WorldView;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.Map;

public class SailingProvider extends BridgeProvider
{
	// "Take down sails" / "Set sail" aren't fixed WidgetInfo/InterfaceID constants -- they're
	// dynamically built rows inside the Sailing Options panel's Facilities tab (same situation
	// BankProvider.java already handles for individual bank item slots: dynamic children,
	// matched by their own text at runtime, not a fixed component id). TODO verify in-game (see
	// docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md, "Still unverified" #2)
	// that this text match actually finds the right child -- the row's exact text/position
	// hasn't been confirmed live yet.
	private static final String TAKE_DOWN_SAILS_TEXT = "Take down sails";
	private static final String SET_SAIL_TEXT = "Set sail";

	public SailingProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/sailing", () -> onClient(this::getSailing));
	}

	private Object getSailing()
	{
		Map<String, Object> data = new HashMap<>();

		// CONFIRMED WRONG live 2026-08-02 (was spec "Still unverified" #7, the single
		// highest-consequence guess in this whole feature): FossilquestSailing (interface group
		// 604)'s visibility never reflects engaging the helm -- clicking it (confirmed working,
		// resolves to Navigate) never made this widget appear. That guess assumed the wiki's
		// bearing/throttle steering HUD opens on Navigate, but minimap-click steering (the actual
		// mechanism this project uses, see travel-style steer_toward in sailing.py) apparently
		// never needs that HUD at all, so its visibility was never a meaningful signal here.
		//
		// WorldView.getYellowClickAction() is the real, direct signal instead: it reports whether
		// a tile click currently resolves to CLICK_ACTION_WALK (normal foot movement) or
		// CLICK_ACTION_SET_HEADING (boat steering engaged) for that view -- exactly what
		// "navigating" needs to mean, from an API built for precisely this purpose rather than an
		// inferred side effect of some widget's visibility.
		WorldView topLevelView = client.getTopLevelWorldView();
		int yellowClickAction = topLevelView != null ? topLevelView.getYellowClickAction() : Constants.CLICK_ACTION_WALK;
		data.put("navigating", yellowClickAction == Constants.CLICK_ACTION_SET_HEADING);

		Widget facilitiesContent = client.getWidget(InterfaceID.SailingSidepanel.FACILITIES_CONTENT_CONTAINER);
		boolean facilitiesTabOpen = facilitiesContent != null && !facilitiesContent.isHidden();
		data.put("facilitiesTabOpen", facilitiesTabOpen);

		Map<String, Object> takeDownSails = new HashMap<>();
		Map<String, Object> setSail = new HashMap<>();
		if (facilitiesTabOpen)
		{
			Widget rows = client.getWidget(InterfaceID.SailingSidepanel.FACILITIES_ROWS);
			Widget[] children = rows != null ? rows.getDynamicChildren() : null;
			if (children != null)
			{
				for (Widget child : children)
				{
					if (child == null || child.isHidden())
					{
						continue;
					}

					String text = child.getText();
					if (text == null)
					{
						continue;
					}

					if (text.equalsIgnoreCase(TAKE_DOWN_SAILS_TEXT))
					{
						takeDownSails.putAll(boundsPoint(child));
					}
					else if (text.equalsIgnoreCase(SET_SAIL_TEXT))
					{
						setSail.putAll(boundsPoint(child));
					}
				}
			}
		}
		data.put("takeDownSails", takeDownSails);
		data.put("setSail", setSail);

		return data;
	}

	private Map<String, Object> boundsPoint(Widget widget)
	{
		Map<String, Object> point = new HashMap<>();

		Rectangle bounds = widget.getBounds();
		if (bounds == null)
		{
			return point;
		}

		Point screen = RuneLiteUtil.canvasToScreen(client, (int) bounds.getCenterX(), (int) bounds.getCenterY());
		point.put("screenX", screen.x);
		point.put("screenY", screen.y);
		return point;
	}
}
