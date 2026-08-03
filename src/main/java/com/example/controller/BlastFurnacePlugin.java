package com.example.controller;

import com.google.inject.Provides;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDependency;
import net.runelite.client.plugins.PluginDescriptor;

import javax.inject.Inject;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Same pattern as MiningPlugin/FiremakingPlugin -- see their headers. Registers
 * routines/blast_furnace.py's config under /blast-furnace-config on ControllerPlugin's
 * shared bridge server.
 */
@PluginDescriptor(
	name = "* Controller - Blast Furnace",
	description = "Blast Furnace routine settings for the external controller -- see the main HttpBridge/Controller plugin for Profile selection.",
	tags = {"controller", "config", "blast", "furnace", "smithing", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class BlastFurnacePlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private BlastFurnaceConfig config;

	@Override
	protected void startUp()
	{
		controllerPlugin.getServer().register("/blast-furnace-config", () -> export(config));
	}

	@Override
	protected void shutDown()
	{
	}

	@Provides
	BlastFurnaceConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(BlastFurnaceConfig.class);
	}

	private static Map<String, Object> export(BlastFurnaceConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.blastFurnaceEnabled());
		// Lowercased to match routines/blast_furnace.py's BAR_TYPES dict keys exactly.
		data.put("barType", config.barType().name().toLowerCase(Locale.ROOT));

		Map<String, Object> coffer = new LinkedHashMap<>();
		coffer.put("topUpEnabled", config.cofferTopUpEnabled());
		coffer.put("topUpAmount", config.cofferTopUpAmount());
		coffer.put("topUpIntervalMinutes", config.cofferTopUpIntervalMinutes());
		data.put("coffer", coffer);

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("teleportFallback", config.bankTeleportFallback());
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
