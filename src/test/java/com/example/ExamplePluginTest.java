package com.example;

import com.example.controller.ControllerPlugin;
import com.example.controller.FiremakingPlugin;
import com.example.controller.GemstoneCrabPlugin;
import com.example.controller.MiningPlugin;
import com.example.controller.SlayerPlugin;
import com.example.controller.TormentedDemonPlugin;
import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;

public class ExamplePluginTest
{
	public static void main(String[] args) throws Exception
	{
		// This dev harness only loads whatever's explicitly listed here -- it does not scan for
		// other @PluginDescriptor classes, so every Controller-family plugin needs its own entry
		// or it silently never starts (no error, just absent from the plugin list).
		ExternalPluginManager.loadBuiltin(
			ControllerPlugin.class,
			SlayerPlugin.class,
			MiningPlugin.class,
			GemstoneCrabPlugin.class,
			TormentedDemonPlugin.class,
			FiremakingPlugin.class
		);
		RuneLite.main(args);
	}
}