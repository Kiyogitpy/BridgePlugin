package com.example.providers;

import com.example.controller.ConfigExporter;
import com.example.controller.ControllerConfig;
import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import java.util.Map;

public class ConfigProvider extends BridgeProvider
{
	private final ControllerConfig config;

	public ConfigProvider(Client client, ClientThread clientThread, BridgeHttpServer server, ControllerConfig config)
	{
		super(client, clientThread, server);
		this.config = config;
	}

	@Override
	public void register()
	{
		server.register("/config", () -> onClient(this::getConfig));
	}

	private Object getConfig()
	{
		return ConfigExporter.export(client, config);
	}
}
