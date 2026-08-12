package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import java.util.Map;

public abstract class BridgeProvider
{
	protected final Client client;
	protected final ClientThread clientThread;
	protected final BridgeHttpServer server;

	protected BridgeProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		this.client = client;
		this.clientThread = clientThread;
		this.server = server;
	}

	public abstract void register();

	protected Object onClient(SupplierLike supplier)
	{
		return com.example.util.RuneLiteUtil.runOnClientThread(client, clientThread, supplier::get);
	}

	/**
	 * True when the request explicitly asked to bypass a provider's TickCache (e.g. right after
	 * an action that just changed the state that endpoint reports).
	 */
	protected boolean isFresh(Map<String, String> query)
	{
		return "true".equalsIgnoreCase(query.get("fresh"));
	}

	@FunctionalInterface
	protected interface SupplierLike
	{
		Object get();
	}
}
