package com.example.controller;

import com.google.inject.Provides;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDependency;
import net.runelite.client.plugins.PluginDescriptor;

import javax.inject.Inject;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Same pattern as MiningPlugin -- see its header. Registers routines/firemaking.py's
 * config under /firemaking-config on ControllerPlugin's shared bridge server.
 */
@PluginDescriptor(
	name = "* Controller - Firemaking",
	description = "Firemaking routine settings for the external controller -- see the main HttpBridge/Controller plugin for Profile selection.",
	tags = {"controller", "config", "firemaking", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class FiremakingPlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private FiremakingConfig config;

	@Override
	protected void startUp()
	{
		controllerPlugin.getServer().register("/firemaking-config", () -> export(config));
	}

	@Override
	protected void shutDown()
	{
	}

	@Provides
	FiremakingConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(FiremakingConfig.class);
	}

	private static Map<String, Object> export(FiremakingConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.firemakingEnabled());
		data.put("logName", config.logName());
		data.put("tinderboxName", config.tinderboxName());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("enabled", config.bankEnabled());
		bank.put("teleportFallback", config.bankTeleportFallback());
		bank.put("keepItems", Collections.singletonList(config.tinderboxName()));

		List<Map<String, Object>> withdraw = new ArrayList<>();
		Map<String, Object> entry = new LinkedHashMap<>();
		entry.put("name", config.logName());
		entry.put("quantity", config.bankWithdrawQuantity());
		withdraw.add(entry);
		bank.put("withdraw", withdraw);
		data.put("bank", bank);

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
		safety.put("maxRuntimeHours", config.maxRuntimeHours());
		data.put("safety", safety);

		return data;
	}
}
