package com.example.providers;

import com.example.controller.ConfigExporter;
import com.example.controller.ControllerConfig;
import com.example.controller.SlayerConfig;
import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

public class SlayerConfigProvider extends BridgeProvider
{
	private final ControllerConfig controllerConfig;
	private final SlayerConfig config;

	public SlayerConfigProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
		ControllerConfig controllerConfig, SlayerConfig config)
	{
		super(client, clientThread, server);
		this.controllerConfig = controllerConfig;
		this.config = config;
	}

	@Override
	public void register()
	{
		server.register("/slayer-config", () -> onClient(this::getConfig));
	}

	private Object getConfig()
	{
		return ConfigExporter.export(client, controllerConfig, config);
	}
}
