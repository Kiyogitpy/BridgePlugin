package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.awt.Canvas;
import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.Map;

/**
 * Exposes what's needed to detect being logged out and click back in. The login flow has two
 * very different screens, found by hovering/inspecting them live via a temporary widget/Swing
 * dump tool (see WidgetDumpProvider/SwingDumpProvider):
 *
 *  1. The saved-account tile chooser, shown at GameState.LOGIN_SCREEN. This is drawn as raw
 *     pixels by the game engine itself -- confirmed empirically, both client.getWidgetRoots()
 *     (rootCount: 0, not null) and a full Swing/AWT component-tree walk found nothing for it.
 *     There is no API surface to read its layout, so its click point is a fraction of the
 *     canvas's actual on-screen size (canvas.getWidth()/getHeight(), i.e. post-stretch), captured
 *     once from a confirmed on-screen point. NOT a fraction of GET /state's raw mouseCanvasX/Y --
 *     that's in RuneLite's internal pre-stretch render space, and dividing it directly by the
 *     stretched canvas width mixes units, silently off by the stretch-mode scale factor. This is
 *     inherently approximate -- it assumes the tile stays in the same relative position -- and
 *     will need recapturing if that ever stops being true (different number of saved accounts, a
 *     client update that reflows the screen, etc.).
 *
 *  2. The "Welcome to Old School RuneScape" / CLICK HERE TO PLAY screen shown right after
 *     (GameState is already LOGGED_IN by this point, even though you're not really in the
 *     world yet). This genuinely is a widget -- group 378, child 77, confirmed via
 *     /widgets/dump while looking directly at it (text: "CLICK HERE TO PLAY") -- so unlike the
 *     tile, its exact bounds are read fresh every call, not a hardcoded pixel guess.
 */
public class LoginProvider extends BridgeProvider
{
	private static final int CLICK_TO_PLAY_GROUP = 378;
	private static final int CLICK_TO_PLAY_CHILD = 77;

	// Fraction of the canvas size (canvas.getWidth()/getHeight() below, i.e. the actual
	// on-screen/stretched size), NOT of client.getMouseCanvasPosition() -- that's in
	// RuneLite's internal pre-stretch render space, and naively dividing it by the
	// stretched canvas width mixes units, silently off by the stretch-mode scale factor
	// (~2x was observed). Derived here from a confirmed on-screen point (1141, 570) while
	// canvasScreenLoc was (0, 23) and the canvas was 2287x1377.
	private static final double ACCOUNT_TILE_FRACTION_X = 1141.0 / 2287.0;
	private static final double ACCOUNT_TILE_FRACTION_Y = 547.0 / 1377.0;

	public LoginProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/login/status", () -> onClient(this::getStatus));
	}

	private Object getStatus()
	{
		Map<String, Object> data = new HashMap<>();
		GameState state = client.getGameState();
		data.put("gameState", state.name());
		data.put("loggedIn", state == GameState.LOGGED_IN);

		Point canvasScreenLoc = RuneLiteUtil.getCanvasScreenLocation(client);

		if (state == GameState.LOGIN_SCREEN)
		{
			Canvas canvas = client.getCanvas();
			int canvasWidth = canvas != null ? canvas.getWidth() : 0;
			int canvasHeight = canvas != null ? canvas.getHeight() : 0;

			Map<String, Object> tile = new HashMap<>();
			tile.put("screenX", canvasScreenLoc.x + (int) Math.round(canvasWidth * ACCOUNT_TILE_FRACTION_X));
			tile.put("screenY", canvasScreenLoc.y + (int) Math.round(canvasHeight * ACCOUNT_TILE_FRACTION_Y));
			data.put("accountTile", tile);
		}

		Widget clickToPlay = client.getWidget(CLICK_TO_PLAY_GROUP, CLICK_TO_PLAY_CHILD);
		if (clickToPlay != null && !clickToPlay.isHidden())
		{
			Rectangle bounds = clickToPlay.getBounds();
			if (bounds != null)
			{
				Point screen = RuneLiteUtil.canvasToScreen(client, (int) bounds.getCenterX(), (int) bounds.getCenterY());
				Map<String, Object> button = new HashMap<>();
				button.put("screenX", screen.x);
				button.put("screenY", screen.y);
				data.put("clickToPlay", button);
			}
		}

		return data;
	}
}
