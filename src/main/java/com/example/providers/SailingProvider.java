package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Constants;
import net.runelite.api.Perspective;
import net.runelite.api.Player;
import net.runelite.api.WorldEntity;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.events.GameTick;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.Map;

public class SailingProvider extends BridgeProvider
{
	private final EventBus eventBus;

	// Boat speed, sampled once per GameTick rather than on every /sailing GET -- GETs can arrive
	// several times within a single game tick (polling loop), and the boat's WorldEntity position
	// only actually changes on tick boundaries, so re-measuring on every GET would either read a
	// stale delta of 0 between ticks or double-count a single tick's movement across polls.
	// Modeled directly on the sideloaded Sailing plugin's own speed readout
	// (data/decompiled/sailing-plugin's NavigationOverlay.onGameTick): measures the boat's
	// WorldEntity.getTargetLocation() (the boat's own logical position for this tick), not
	// getLocalLocation() (visually tweened/interpolated between ticks, and so noisy on a per-tick
	// sample), matching that plugin's own choice for the same reason.
	//
	// lastBoat tracked alongside lastBoatTarget (CONFIRMED live 2026-08-04, sailing.py-side bug
	// report: a boatSpeed reading of ~31 tiles/tick showed up in a live log, immediately after
	// "engaging the helm" with no real movement in between -- physically impossible next to every
	// other sample in the same run, 0-2.1). findTopLevelWorldEntityContaining can hand back a
	// different WorldEntity instance across the helm-engage boundary (the boat's steering target
	// location resets the instant Navigate engages), and the old null-only reset below only
	// guarded against the boat disappearing entirely, not against silently tracking a *different*
	// entity than the one lastBoatTarget was captured from -- diffing across that swap produced
	// one bogus huge one-tick delta instead of 0. Only diff when it's confirmed to be the same
	// entity as last tick.
	private WorldEntity lastBoat;
	private LocalPoint lastBoatTarget;
	private double lastBoatSpeedTilesPerTick;

	public SailingProvider(Client client, ClientThread clientThread, BridgeHttpServer server, EventBus eventBus)
	{
		super(client, clientThread, server);
		this.eventBus = eventBus;
	}

	@Override
	public void register()
	{
		server.register("/sailing", () -> onClient(this::getSailing));
		eventBus.register(this);
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		Player local = client.getLocalPlayer();
		WorldEntity boat = local != null
			? RuneLiteUtil.findTopLevelWorldEntityContaining(client, local.getWorldView())
			: null;
		if (boat == null)
		{
			lastBoat = null;
			lastBoatTarget = null;
			lastBoatSpeedTilesPerTick = 0;
			return;
		}

		LocalPoint target = boat.getTargetLocation();
		if (boat == lastBoat && lastBoatTarget != null && target != null)
		{
			double localDist = Math.hypot(target.getX() - lastBoatTarget.getX(), target.getY() - lastBoatTarget.getY());
			lastBoatSpeedTilesPerTick = localDist / Perspective.LOCAL_TILE_SIZE;
		}
		else
		{
			lastBoatSpeedTilesPerTick = 0;
		}
		lastBoat = boat;
		lastBoatTarget = target;
	}

	private Object getSailing()
	{
		Map<String, Object> data = new HashMap<>();

		// UNVERIFIED live -- the reference plugin only ever consumes this as a rounded UI display
		// figure (NavigationOverlay.roundToQuarterTile), never as raw precision, so this exact
		// tiles-per-tick value hasn't itself been cross-checked against a known live speed. The
		// underlying getTargetLocation()-delta approach is CONFIRMED as the right data source
		// (matches that plugin's own choice, see the field comment above); what's unverified is
		// only whether the raw value here needs further smoothing/rounding for sailing.py's
		// braking-distance use.
		data.put("boatSpeed", lastBoatSpeedTilesPerTick);

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

		// CONFIRMED live 2026-08-02: the sail control isn't two separately-labeled buttons
		// ("Take down sails" / "Set sail", the original guess) -- it's a single toggle icon whose
		// menu text just flips between "Set sails" and "Un-set sails" depending on current state.
		// Confirmed the icon itself carries no text at all (every one of 73 dumped dynamic
		// children under FACILITIES_ROWS had empty getText() -- "Steering"/"Repairs: No kits"
		// section labels were the only non-empty text found), so text-matching could never work
		// here to begin with. The real click target is a single big invisible click-layer
		// (FACILITIES_CONTENT_CLICKLAYER) sitting over the whole icon grid -- confirmed via
		// GameStateProvider's /debug-menu while hovering the icon, whose menu entry resolved
		// through that layer's widget id, not a per-icon one. Each icon instead renders as an
		// 11-entry tile cluster (8 corner/edge pieces + its own center point repeated 3x) within
		// FACILITIES_ROWS's dynamic children, in a fixed layout order: the Steering section's
		// three controls (confirmed live: sails / speed / reverse, matching the wiki's "set or
		// un-set the sails, change the speed of the boat, or reverse the boat") always come
		// first, and cross-checking the confirmed-by-hover sail-icon's on-screen position against
		// this array showed it's always the FIRST cluster (children[0]'s own bounds). So: use
		// that position directly, rather than trying to match by text or a fixed widget id.
		Map<String, Object> sailToggle = new HashMap<>();
		if (facilitiesTabOpen)
		{
			Widget rows = client.getWidget(InterfaceID.SailingSidepanel.FACILITIES_ROWS);
			Widget[] children = rows != null ? rows.getDynamicChildren() : null;
			if (children != null && children.length > 0 && children[0] != null && !children[0].isHidden())
			{
				sailToggle.putAll(boundsPoint(children[0]));
			}
		}
		data.put("sailToggle", sailToggle);

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
