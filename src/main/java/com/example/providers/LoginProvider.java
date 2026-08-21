package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.awt.Dimension;
import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.Map;

/**
 * Exposes what's needed to detect being logged out and click back in. The login flow has two
 * very different screens:
 *
 *  1. The "Play Now" account screen, shown at GameState.LOGIN_SCREEN with loginIndex 10. This is
 *     drawn as raw pixels by the game engine -- re-confirmed 2026-08-21 against the current
 *     client via /widgets/dump: client.getWidgetRoots() returns rootCount 0, AND a brute-force
 *     client.getWidget(group, child) sweep over groups 0-1000 x children 0-60 found nothing
 *     either. (Both checks matter: a root that is not attached to the root list is invisible to
 *     the walk but still resolves by id, so the walk alone was never proof.) There is no API
 *     surface to read its layout, so its position is computed from the layout rule below.
 *
 *  2. The "Welcome to Old School RuneScape" / CLICK HERE TO PLAY screen shown right after
 *     (GameState is already LOGGED_IN by this point, even though you're not really in the
 *     world yet). This genuinely is a widget -- group 378, child 77 -- so its exact bounds are
 *     read fresh every call and it needs no layout rule at all.
 *
 * <p><b>The login UI's layout rule.</b> An earlier version of this class placed the account
 * button at a fixed fraction of the canvas's stretched on-screen size. That is structurally
 * wrong, and was confirmed broken live: resizing the window moved the reported point ~80px off
 * target. Two things defeat a canvas fraction -- the login art is letterboxed at some window
 * sizes (142px black bars each side at 2529x1377, none at 1202x682), and the UI does not scale
 * with the canvas at all.
 *
 * <p>What it actually does: the login UI is drawn at a <b>fixed pixel size in the game's real
 * (pre-stretch) render space, horizontally centred and top-anchored</b>. Measured from captured
 * frames (see ScreenshotProvider) across three independent geometries:
 *
 * <pre>
 *   real dims    aspect  stretch   "Play Now" text top   text width   centre x vs realW/2
 *   1227x668     1.84    2.06x     241.1                 77.6         -0.7
 *    886x503     1.76    1.36x     241.2                 77.4         -1.1
 *   2470x503     4.91    1.00x     241.0                 77.0         -1.5
 * </pre>
 *
 * <p>Aspect ratio nearly tripled and the stretch factor went from 2.06x to none, yet the text
 * top stayed at real y 241 -- so the offset is anchored to the top, not vertically centred (a
 * centred layout would have shifted by (668-503)/2 = 82px between rows 1 and 2). PLAY_NOW_Y
 * below is the text's vertical centre, 241 + half the ~22px glyph height.
 *
 * <p>Because that position is expressed in the game's internal render space, it goes through
 * {@link RuneLiteUtil#canvasToScreen} -- the exact same conversion the CLICK_TO_PLAY widget
 * bounds use, which is what makes both paths resolution-, DPI-, and stretch-independent.
 * Do NOT feed it a coordinate measured off a DrawManager frame instead: those frames are in
 * post-stretch canvas space, so canvasToScreen would scale them a second time.
 */
public class LoginProvider extends BridgeProvider
{
	private static final int CLICK_TO_PLAY_GROUP = 378;
	private static final int CLICK_TO_PLAY_CHILD = 77;

	/**
	 * loginIndex of the "Play Now" screen. Gating on this rather than on GameState.LOGIN_SCREEN
	 * alone matters: LOGIN_SCREEN also covers screens this class has no layout rule for (the
	 * classic username/password fields, the authenticator prompt). Reporting no button at all on
	 * an unrecognised screen is correct -- it lets the caller fail loudly instead of clicking a
	 * constant derived from a different screen.
	 */
	private static final int LOGIN_INDEX_PLAY_NOW = 10;

	/**
	 * loginIndex of the message-dialog screen -- confirmed live by forcing a network drop, which
	 * produced "You were disconnected from the server." above a single "Ok" button. The index is
	 * named for the dialog rather than for disconnection specifically: the same screen very
	 * plausibly carries other login messages (world full, bad credentials), and dismissing it
	 * with Ok is the right move for any of them, since that returns to the Play Now screen where
	 * the normal login path takes over.
	 */
	private static final int LOGIN_INDEX_DIALOG = 24;

	/** Vertical centre of the "Play Now" text, in real (pre-stretch) render pixels. */
	private static final int PLAY_NOW_Y = 252;

	/**
	 * Vertical centre of the dialog's "Ok" button, in real (pre-stretch) render pixels. Measured
	 * the same way as PLAY_NOW_Y: the "Ok" glyphs centre on real y 300.8, and the button box's
	 * own borders sit at real y 281 and 318, putting the box centre at 299.5. The box is ~150x37
	 * real pixels, so this is not a tight target.
	 */
	private static final int OK_BUTTON_Y = 300;

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
		int loginIndex = client.getLoginIndex();
		data.put("gameState", state.name());
		data.put("loggedIn", state == GameState.LOGGED_IN);
		data.put("loginIndex", loginIndex);

		if (state == GameState.LOGIN_SCREEN)
		{
			if (loginIndex == LOGIN_INDEX_PLAY_NOW)
			{
				putCentredButton(data, "playNow", PLAY_NOW_Y);
			}
			else if (loginIndex == LOGIN_INDEX_DIALOG)
			{
				putCentredButton(data, "okButton", OK_BUTTON_Y);
			}
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

	/**
	 * Reports a login-UI button that the layout rule places on the horizontal centre line at a
	 * fixed height, converting it to an absolute screen point. Emits nothing at all if the real
	 * dimensions are unreadable -- a caller that gets no point can say so, whereas one handed a
	 * bogus point would click into empty space and never explain why.
	 *
	 * @param realY vertical position in the game's real (pre-stretch) render space
	 */
	private void putCentredButton(Map<String, Object> data, String key, int realY)
	{
		Dimension real = client.getRealDimensions();
		if (real == null || real.width <= 0)
		{
			return;
		}

		Point screen = RuneLiteUtil.canvasToScreen(client, real.width / 2, realY);
		Map<String, Object> button = new HashMap<>();
		button.put("screenX", screen.x);
		button.put("screenY", screen.y);
		data.put(key, button);
	}
}
