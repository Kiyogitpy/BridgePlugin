package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.EventSubscriptions;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * GET /events/subscribe?varbits=1,2&varps=3 -- REPLACE the set of varbits/varps that
 * ControllerPlugin.onVarbitChanged emits VARBIT_CHANGED for (no params = emit none).
 * GET /events/subscriptions -- read the current sets back.
 *
 * Pure bookkeeping, no client-thread hop: EventSubscriptions swaps immutable sets atomically.
 * See the 2026-09-04 event-driven-outcomes spec for why the filter exists (a login/scene-load
 * varp burst would otherwise evict useful events from the UDP queue).
 */
public class EventsProvider extends BridgeProvider
{
	private final EventSubscriptions subscriptions;

	public EventsProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
		EventSubscriptions subscriptions)
	{
		super(client, clientThread, server);
		this.subscriptions = subscriptions;
	}

	@Override
	public void register()
	{
		server.registerQuery("/events/subscribe", this::subscribe);
		server.register("/events/subscriptions", this::current);
	}

	private Object subscribe(Map<String, String> query)
	{
		subscriptions.replace(EventSubscriptions.parseIds(query.get("varbits")),
			EventSubscriptions.parseIds(query.get("varps")));
		return current();
	}

	private Object current()
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("varbits", sorted(subscriptions.varbits()));
		data.put("varps", sorted(subscriptions.varps()));
		return data;
	}

	private static List<Integer> sorted(Set<Integer> ids)
	{
		List<Integer> out = new ArrayList<>(ids);
		Collections.sort(out);
		return out;
	}
}
