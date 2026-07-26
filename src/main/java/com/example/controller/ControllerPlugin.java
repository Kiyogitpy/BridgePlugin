package com.example.controller;

import com.google.inject.Provides;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.ui.ClientToolbar;
import net.runelite.client.ui.NavigationButton;
import net.runelite.client.util.ImageUtil;

import javax.inject.Inject;
import java.awt.image.BufferedImage;

@PluginDescriptor(
	name = "Controller",
	description = "Configure combat targets, prayers, food, potions, breaks, and safety limits.",
	tags = {"controller", "config", "combat", "slayer"}
)
public class ControllerPlugin extends Plugin
{
	@Inject
	private SettingsPanel panel;

	@Inject
	private ClientToolbar clientToolbar;

	private NavigationButton navButton;

	@Override
	protected void startUp()
	{
		BufferedImage icon = ImageUtil.loadImageResource(getClass(), "/bot_icon.png");
		if (icon == null)
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
	}

	@Override
	protected void shutDown()
	{
		clientToolbar.removeNavigation(navButton);
	}

	@Provides
	ControllerConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(ControllerConfig.class);
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
