package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.Perspective;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.ui.overlay.Overlay;
import net.runelite.client.ui.overlay.OverlayLayer;
import net.runelite.client.ui.overlay.OverlayPosition;
import net.runelite.client.ui.overlay.OverlayUtil;

import javax.inject.Inject;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Graphics2D;
import java.awt.Polygon;

/**
 * Draws a colored tile border over the marked safespot, whenever Use Safespot is on and a tile
 * has actually been marked -- so a bad/stale mark (e.g. captured inside an instance, see
 * SafespotMarker's own history) is visually obvious immediately instead of only surfacing once
 * the bot tries to walk there and fails. Same rendering technique RuneLite's own Ground Markers
 * plugin uses (GroundMarkerOverlay, confirmed against the decompiled client source):
 * Perspective.getCanvasTilePoly projects the tile onto the viewport, OverlayUtil.renderPolygon
 * draws it. Fixed color, not configurable -- the point is just visibility, not customization.
 */
class SafespotOverlay extends Overlay
{
	private static final Color BORDER_COLOR = Color.CYAN;
	private static final Color FILL_COLOR = new Color(0, 255, 255, 50);
	private static final BasicStroke BORDER_STROKE = new BasicStroke(2);

	private final Client client;
	private final ControllerConfig config;

	@Inject
	SafespotOverlay(Client client, ControllerConfig config)
	{
		this.client = client;
		this.config = config;
		setPosition(OverlayPosition.DYNAMIC);
		setLayer(OverlayLayer.ABOVE_SCENE);
	}

	@Override
	public Dimension render(Graphics2D graphics)
	{
		if (!config.safespotEnabled() || (config.safespotX() == 0 && config.safespotY() == 0))
		{
			return null;
		}

		WorldPoint point = new WorldPoint(config.safespotX(), config.safespotY(), config.safespotPlane());
		if (point.getPlane() != client.getPlane())
		{
			return null;
		}

		LocalPoint local = LocalPoint.fromWorld(client, point);
		if (local == null)
		{
			return null;
		}

		Polygon poly = Perspective.getCanvasTilePoly(client, local);
		if (poly != null)
		{
			OverlayUtil.renderPolygon(graphics, poly, BORDER_COLOR, FILL_COLOR, BORDER_STROKE);
		}

		return null;
	}
}
