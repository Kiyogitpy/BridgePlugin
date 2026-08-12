package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.events.MenuOptionClicked;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.util.Text;

import java.util.HashMap;
import java.util.Map;

/**
 * Exposes the most recent MenuOptionClicked the client actually processed -- the game's own
 * ground truth for "what did we just click", fired whenever ANY menu action is invoked
 * (a left-click default action or a right-click submenu selection), regardless of whether the
 * click came from a real mouse or the Arduino-simulated one this bot drives.
 *
 * Added 2026-08-12 (user request, Mastering Mixology live session): every click-confirmation
 * idiom in this codebase up to this point works by inference after the fact -- re-polling
 * inventory/bank/mixology-state for a change the click was expected to cause, or (worse)
 * pre-emptively waiting for the camera/player to "settle" before risking a click at all. The
 * latter in particular made Mastering Mixology's routine extremely slow once it worked
 * correctly (every single world-object click blocked on a multi-hundred-ms to multi-second
 * idle-wait first). This endpoint lets a caller click immediately and then directly confirm --
 * from the game's own event, not a proxy -- whether that specific click actually resolved to
 * the intended target, so a wrong/missed click can be detected and retried without ever
 * needing to guess reactively.
 *
 * Deliberately NOT cross-referenced against ObjectProvider/NPCProvider here to resolve a
 * human-readable name -- getMenuTarget() already carries the display name (with colour tags,
 * stripped below) the same way the game's own chatbox/menu does, and getId() is the same
 * identifier those providers already expose per-object, so a caller matching this endpoint's
 * `id` against a previously-resolved object's own `id` field needs no extra lookup here.
 */
public class LastActionProvider extends BridgeProvider
{
	private final EventBus eventBus;

	private volatile Map<String, Object> lastAction = new HashMap<>();

	public LastActionProvider(Client client, ClientThread clientThread, BridgeHttpServer server, EventBus eventBus)
	{
		super(client, clientThread, server);
		this.eventBus = eventBus;
	}

	@Override
	public void register()
	{
		server.register("/last-action", () -> lastAction);
		eventBus.register(this);
	}

	@Subscribe
	public void onMenuOptionClicked(MenuOptionClicked event)
	{
		// tick captured here (event handler, always on the client thread) rather than inside
		// onClient(...) at read time -- the tick a click was PROCESSED on is what a caller needs
		// to diff against its own pre-click baseline tick, not whatever tick happens to be
		// current whenever the bridge GET later arrives.
		int tick = client.getTickCount();

		Map<String, Object> data = new HashMap<>();
		data.put("tick", tick);
		data.put("option", Text.removeTags(event.getMenuOption()));
		data.put("target", Text.removeTags(event.getMenuTarget()));
		data.put("id", event.getId());
		data.put("menuAction", event.getMenuAction() != null ? event.getMenuAction().name() : null);
		data.put("param0", event.getParam0());
		data.put("param1", event.getParam1());
		data.put("itemId", event.getItemId());

		lastAction = data;
	}
}
