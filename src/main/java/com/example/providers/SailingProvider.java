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

		// FossilquestSailing (interface group 604) is the boat-piloting HUD that the wiki
		// describes opening when the helm's Navigate option is used -- its visibility is the
		// most direct signal for "is the helm currently engaged". TODO verify in-game (see spec
		// "Still unverified" #7) -- this interface group's real purpose was never confirmed live,
		// only inferred from its component names in a stale source checkout.
		Widget steeringHud = client.getWidget(InterfaceID.FossilquestSailing.UNIVERSAL);
		data.put("navigating", steeringHud != null && !steeringHud.isHidden());

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
