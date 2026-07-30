package com.example.controller;

import com.google.inject.Provides;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDependency;
import net.runelite.client.plugins.PluginDescriptor;

import javax.inject.Inject;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Owns Mining's own Controller Settings panel entry (separate from the main "HttpBridge"
 * plugin's Slayer/Combat-focused config) and registers routines/mining.py's config under
 * /mining-config on the same bridge HTTP server ControllerPlugin already runs -- see
 * ControllerPlugin.getServer().
 */
@PluginDescriptor(
	name = "* Controller - Mining",
	description = "Mining routine settings for the external controller -- see the main HttpBridge/Controller plugin for Profile selection.",
	tags = {"controller", "config", "mining", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class MiningPlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private MiningConfig config;

	@Override
	protected void startUp()
	{
		controllerPlugin.getServer().register("/mining-config", () -> export(config));
	}

	@Override
	protected void shutDown()
	{
		// No explicit unregister -- BridgeHttpServer has no removeContext, and this endpoint
		// simply stops being asked for by mining.py once the routine isn't selected anymore.
		// Matches every other provider's lifecycle in this bridge (registered once at
		// startUp, never individually torn down).
	}

	@Provides
	MiningConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(MiningConfig.class);
	}

	private static Map<String, Object> export(MiningConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.miningEnabled());
		data.put("rockNames", ConfigExporter.parseList(config.rockNames()));
		data.put("maxRockDistance", config.maxRockDistance());

		Map<String, Object> breaks = new LinkedHashMap<>();
		breaks.put("enabled", config.takeBreaks());
		breaks.put("everyMinMinutes", config.breakEveryMinMinutes());
		breaks.put("everyMaxMinutes", config.breakEveryMaxMinutes());
		breaks.put("durationMinMinutes", config.breakDurationMinMinutes());
		breaks.put("durationMaxMinutes", config.breakDurationMaxMinutes());
		breaks.put("microBreaks", config.microBreaks());
		data.put("breaks", breaks);

		Map<String, Object> safety = new LinkedHashMap<>();
		safety.put("stopOnDeath", config.stopOnDeath());
		safety.put("stopOnInventoryFull", config.stopOnInventoryFull());
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		data.put("safety", safety);

		return data;
	}
}
