package com.example.providers;

import com.example.controller.ControllerConfig;
import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import java.util.LinkedHashMap;
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

	// Just the master switch and which routine to run -- routine-specific settings moved to
	// SlayerConfig, exported separately by SlayerPlugin's own /slayer-config endpoint (see
	// SlayerConfigProvider).
	private Object getConfig()
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.controllerEnabled());
		data.put("routine", config.routine().name());
		return data;
	}
}
