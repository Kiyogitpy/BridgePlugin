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
 * Same pattern as MiningPlugin -- see its header. Registers routines/gemstone_crab.py's
 * config under /gemstone-crab-config on ControllerPlugin's shared bridge server.
 */
@PluginDescriptor(
	name = "* Controller - Gemstone Crab",
	description = "Gemstone Crab routine settings for the external controller -- see the main HttpBridge/Controller plugin for Profile selection.",
	tags = {"controller", "config", "gemstone", "crab", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class GemstoneCrabPlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private GemstoneCrabConfig config;

	@Override
	protected void startUp()
	{
		controllerPlugin.getServer().register("/gemstone-crab-config", () -> export(config));
	}

	@Override
	protected void shutDown()
	{
	}

	@Provides
	GemstoneCrabConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(GemstoneCrabConfig.class);
	}

	private static Map<String, Object> export(GemstoneCrabConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.gemstoneCrabEnabled());
		data.put("crabName", config.crabName());
		data.put("tunnelName", config.tunnelName());
		data.put("maxDistance", config.maxDistance());

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
