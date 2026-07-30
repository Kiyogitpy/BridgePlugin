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
 * Same pattern as MiningPlugin -- see its header. Registers routines/tormented_demon.py's
 * config under /tormented-demon-config on ControllerPlugin's shared bridge server.
 */
@PluginDescriptor(
	name = "* Controller - Tormented Demon",
	description = "Tormented Demon routine settings for the external controller -- see the main HttpBridge/Controller plugin for Profile selection.",
	tags = {"controller", "config", "tormented", "demon", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class TormentedDemonPlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private TormentedDemonConfig config;

	@Override
	protected void startUp()
	{
		controllerPlugin.getServer().register("/tormented-demon-config", () -> export(config));
	}

	@Override
	protected void shutDown()
	{
	}

	@Provides
	TormentedDemonConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(TormentedDemonConfig.class);
	}

	private static Map<String, Object> export(TormentedDemonConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.tormentedDemonEnabled());
		data.put("maxTargetDistance", config.maxTargetDistance());

		// Lowercased to match routines/tormented_demon.py's OVERHEAD_ICON_TO_STYLE / loadouts
		// dict keys ("melee"/"ranged"/"magic") exactly.
		data.put("combatStyle", config.combatStyle().name().toLowerCase(Locale.ROOT));
		data.put("autoSwapGear", config.autoSwapGear());

		Map<String, Object> loadouts = new LinkedHashMap<>();
		loadouts.put("melee", nullIfBlank(config.meleeWeapon()));
		loadouts.put("ranged", nullIfBlank(config.rangedWeapon()));
		loadouts.put("magic", nullIfBlank(config.mageWeapon()));
		data.put("loadouts", loadouts);

		Map<String, Object> food = new LinkedHashMap<>();
		food.put("enabled", true);
		food.put("eatAt", config.eatAtHp());
		data.put("food", food);

		Map<String, Object> prayer = new LinkedHashMap<>();
		prayer.put("restoreAt", config.restorePrayerAt());
		data.put("prayer", prayer);

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

	private static String nullIfBlank(String s)
	{
		return s == null || s.trim().isEmpty() ? null : s.trim();
	}
}
