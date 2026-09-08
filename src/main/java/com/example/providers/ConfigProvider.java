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

	// Just the master switch and which routine to run -- routine-specific settings live in
	// ControllerConfig's own per-routine sections, exported separately by SlayerConfigProvider's
	// /slayer-config endpoint and ControllerPlugin's other routine endpoints.
	//
	// valeTotemsWood is the one exception: routines/vale_totems.py has no dedicated config
	// endpoint and reads this key straight off whatever resolve_config() hands its get_config()
	// as bridge_config, which is this endpoint's response -- so it has to be here, not only in
	// ConfigExporter.exportValeTotems (kept for the enum-NAME convention, see its own comment).
	private Object getConfig()
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.controllerEnabled());
		data.put("routine", config.routine().name());
		data.put("valeTotemsWood", config.valeTotemsWood().name());
		return data;
	}
}
