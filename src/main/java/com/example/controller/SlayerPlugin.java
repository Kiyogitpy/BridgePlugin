package com.example.controller;

import com.example.providers.SlayerConfigProvider;
import com.google.inject.Provides;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.events.ConfigChanged;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDependency;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.ui.ClientToolbar;
import net.runelite.client.ui.NavigationButton;
import net.runelite.client.util.ImageUtil;

import javax.inject.Inject;
import java.awt.image.BufferedImage;

/**
 * Same pattern as MiningPlugin/GemstoneCrabPlugin/TormentedDemonPlugin -- see their headers.
 * Registers routines/slayer.py's config under /slayer-config on ControllerPlugin's shared
 * bridge server. Unlike those three, its export (SlayerConfigProvider) needs the client thread,
 * since resolving Target NPC names to live NPC IDs touches the Client API.
 *
 * Also owns the "Controller Settings" sidebar panel (SettingsPanel) -- its content (target
 * search, selected monsters, task location picks) is entirely slayer-specific, so it belongs
 * here rather than on the generic bridge-hosting ControllerPlugin.
 *
 * SlayerConfig's @Provides binding lives here (not on ControllerPlugin) because RuneLite ties a
 * plugin's own wrench-icon config screen to bindings that plugin's own module declares, not
 * merely what it injects -- confirmed live: with the binding only on ControllerPlugin, this
 * plugin's config screen showed nothing but the on/off toggle. ControllerPlugin still needs a
 * SlayerConfig instance too (for click-priority forcing), but fetches it directly via
 * ConfigManager instead of injecting it, which would otherwise conflict with this binding in
 * this plugin's own composed injector (@PluginDependency merges ControllerPlugin's module in
 * here -- two @Provides for the same type in one injector is a Guice CreationException).
 */
@PluginDescriptor(
	name = "* Controller - Slayer",
	description = "Slayer/combat routine settings for the external controller -- see the main HttpBridge plugin for the master enable switch.",
	tags = {"controller", "config", "combat", "slayer", "bridge"}
)
@PluginDependency(ControllerPlugin.class)
public class SlayerPlugin extends Plugin
{
	@Inject
	private ControllerPlugin controllerPlugin;

	@Inject
	private Client client;

	@Inject
	private ClientThread clientThread;

	@Inject
	private ControllerConfig controllerConfig;

	@Inject
	private SlayerConfig config;

	@Inject
	private SettingsPanel panel;

	@Inject
	private ClientToolbar clientToolbar;

	private NavigationButton navButton;

	@Override
	protected void startUp()
	{
		BufferedImage icon;
		try
		{
			icon = ImageUtil.loadImageResource(getClass(), "/controller_icon.png");
		}
		catch (IllegalArgumentException e)
		{
			icon = createFallbackIcon();
		}

		navButton = NavigationButton.builder()
			.tooltip("Controller Settings")
			.icon(icon)
			.priority(5)
			.panel(panel)
			.build();

		clientToolbar.addNavigation(navButton);

		new SlayerConfigProvider(client, clientThread, controllerPlugin.getServer(), controllerConfig, config).register();
	}

	@Override
	protected void shutDown()
	{
		clientToolbar.removeNavigation(navButton);
	}

	@Provides
	SlayerConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(SlayerConfig.class);
	}

	@Subscribe
	public void onConfigChanged(ConfigChanged event)
	{
		if (!ControllerConfig.GROUP.equals(event.getGroup()))
		{
			return;
		}

		// Keeps the sidebar panel's Controller Enabled/target selections live with whatever
		// just changed -- including edits made from either wrench-icon config screen, which
		// this panel doesn't otherwise hear about.
		panel.syncFromConfig();
	}

	private static BufferedImage createFallbackIcon()
	{
		BufferedImage img = new BufferedImage(16, 16, BufferedImage.TYPE_INT_ARGB);
		java.awt.Graphics2D g = img.createGraphics();
		g.setColor(new java.awt.Color(0x4CAF50));
		g.fillOval(1, 1, 14, 14);
		g.setColor(java.awt.Color.WHITE);
		g.fillRect(5, 4, 2, 8);
		g.fillRect(9, 6, 2, 6);
		g.dispose();
		return img;
	}
}
