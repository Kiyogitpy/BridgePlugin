package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.ChatMessageType;
import net.runelite.api.Client;
import net.runelite.api.events.ChatMessage;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.util.Text;

import java.util.HashMap;
import java.util.Map;

/**
 * Exposes the most recent chat message the client actually processed -- the game's own ground
 * truth for "did that action finish", fired for every GAMEMESSAGE/SPAM/DIALOG line (same filter
 * as UdpEventSender's CHAT_MESSAGE event, see ControllerPlugin#onChatMessage).
 *
 * Added 2026-08-13 (user request, Mastering Mixology live session): CONFIRMED live that OSRS
 * prints a start/finish message pair for a station's processing action (e.g. "You start
 * concentrating the Liplack liquor..." / "You finish concentrating the Liplack liquor.") and a
 * single message for a mixing-vessel take ("You collect some Liplack liquor from the mixing
 * vessel."). Every completion check in mastering_mixology.py up to this point works by polling
 * /inventory for an id to appear -- up to 20s at 0.3s granularity for a station finishing, up to
 * 3s for a vessel take -- when the game already announces the exact tick it happened. Same
 * tick-baseline idiom as LastActionProvider: a caller records client.getTickCount() (or this
 * endpoint's own last-seen tick) right before acting, then polls until this endpoint reports a
 * newer tick whose message matches what it's waiting for.
 */
public class LastChatMessageProvider extends BridgeProvider
{
	private final EventBus eventBus;

	private volatile Map<String, Object> lastChatMessage = new HashMap<>();

	public LastChatMessageProvider(Client client, ClientThread clientThread, BridgeHttpServer server, EventBus eventBus)
	{
		super(client, clientThread, server);
		this.eventBus = eventBus;
	}

	@Override
	public void register()
	{
		server.register("/last-chat-message", () -> lastChatMessage);
		eventBus.register(this);
	}

	@Subscribe
	public void onChatMessage(ChatMessage event)
	{
		if (event.getType() != ChatMessageType.GAMEMESSAGE
			&& event.getType() != ChatMessageType.SPAM
			&& event.getType() != ChatMessageType.DIALOG)
		{
			return;
		}

		// tick captured here (event handler, always on the client thread), not at read time --
		// same reasoning as LastActionProvider: a caller needs to diff against its own pre-action
		// baseline tick, not whatever tick happens to be current whenever the bridge GET arrives.
		Map<String, Object> data = new HashMap<>();
		data.put("tick", client.getTickCount());
		data.put("type", event.getType().name());
		data.put("message", Text.removeTags(event.getMessage()));
		data.put("sender", event.getName());

		lastChatMessage = data;
	}
}
