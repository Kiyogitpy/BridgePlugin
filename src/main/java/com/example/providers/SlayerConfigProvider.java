package com.example.providers;

import com.example.controller.ConfigExporter;
import com.example.controller.ControllerConfig;
import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

public class SlayerConfigProvider extends BridgeProvider
{
	private final ControllerConfig config;

	public SlayerConfigProvider(Client client, ClientThread clientThread, BridgeHttpServer server, ControllerConfig config)
	{
		super(client, clientThread, server);
		this.config = config;
	}

	@Override
	public void register()
	{
		server.register("/slayer-config", () -> onClient(this::getConfig));
	}

	private Object getConfig()
	{
		return ConfigExporter.exportSlayer(client, config);
	}
}
