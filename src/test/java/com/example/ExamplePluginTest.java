package com.example;

import com.example.controller.ControllerPlugin;
import com.example.bridge.BridgePlugin;
import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;

public class ExamplePluginTest
{
	public static void main(String[] args) throws Exception
	{
		ExternalPluginManager.loadBuiltin(BridgePlugin.class);
		ExternalPluginManager.loadBuiltin(ControllerPlugin.class);
		RuneLite.main(args);
	}
}