package com.example;

import com.example.controller.ControllerPlugin;
import com.example.gauntletrecolor.GauntletRecolorPlugin;
import com.sun.jna.Native;
import com.sun.jna.Pointer;
import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;
import net.runelite.client.plugins.Plugin;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class ExamplePluginTest
{
	// Per-monitor DPI awareness is a Windows-process-level property (declared via an app
	// manifest, or a SetProcessDpiAwarenessContext/SetProcessDPIAware call made before any
	// window is created) -- NOT something a JVM system property like sun.java2d.dpiaware can
	// grant on JDK 9+ (CONFIRMED live 2026-08-08: setting it in build.gradle's run task jvmArgs
	// had no effect at all). A bare `java.exe` launch (this Gradle `run` task, unlike an
	// installed RuneLite's own launcher which carries a DPI-aware manifest) has no such
	// manifest, so Windows silently virtualizes every AWT window's screen coordinates to 96 DPI
	// -- e.g. on a 125%-scaled display, RuneLiteUtil.canvasToScreen's reported screenX/screenY
	// come out exactly 1/1.25 of the real physical pixel a DPI-aware clicker (see
	// ControllerAddon/controller.py's set_dpi_aware(), called from mcp_debug_server.py at
	// import time) needs to land on -- every click_entity/click_menu_entry from that side missed
	// or hit the wrong nearby object all session. Same fix shape as controller.py's own
	// set_dpi_aware(): call the Win32 API directly, before RuneLite.main(args) creates any
	// window, via JNA (already on this classpath as a transitive RuneLite client dependency --
	// no new build.gradle dependency needed).
	private static void makeProcessDpiAware()
	{
		if (!System.getProperty("os.name", "").toLowerCase().contains("win"))
		{
			return;
		}
		try
		{
			boolean ok = User32Dpi.INSTANCE.SetProcessDpiAwarenessContext(new Pointer(-4)); // PER_MONITOR_AWARE_V2
			System.err.println("[dpi-debug] SetProcessDpiAwarenessContext returned " + ok);
			if (!ok)
			{
				boolean fallbackOk = User32Dpi.INSTANCE.SetProcessDPIAware();
				System.err.println("[dpi-debug] SetProcessDPIAware fallback returned " + fallbackOk);
			}
		}
		catch (Throwable t)
		{
			System.err.println("[dpi-debug] SetProcessDpiAwarenessContext threw " + t);
			try
			{
				boolean fallbackOk = User32Dpi.INSTANCE.SetProcessDPIAware();
				System.err.println("[dpi-debug] SetProcessDPIAware fallback returned " + fallbackOk);
			}
			catch (Throwable t2)
			{
				System.err.println("[dpi-debug] SetProcessDPIAware fallback threw " + t2);
			}
		}
		java.awt.Toolkit tk = java.awt.Toolkit.getDefaultToolkit();
		System.err.println("[dpi-debug] Toolkit screen resolution (DPI) = " + tk.getScreenResolution()
			+ ", screen size = " + tk.getScreenSize());
	}

	private interface User32Dpi extends com.sun.jna.win32.StdCallLibrary
	{
		User32Dpi INSTANCE = Native.load("user32", User32Dpi.class);

		boolean SetProcessDpiAwarenessContext(Pointer value);

		boolean SetProcessDPIAware();
	}

	/**
	 * ZombieLite's plugin list, when its jar is on the classpath (build.gradle's `run` puts it
	 * there whenever ../ZombieLite exists). Resolved by reflection on purpose: this launcher must
	 * compile and run with no ZombieLite checkout at all, so nothing here may name its classes.
	 */
	@SuppressWarnings("unchecked")
	private static List<Class<? extends Plugin>> zombieLitePlugins()
	{
		try
		{
			Class<?> custom = Class.forName("com.salvation.plugins.CustomPlugins");
			Class<? extends Plugin>[] list = (Class<? extends Plugin>[]) custom.getField("CLASSLIST").get(null);
			System.err.println("[zombielite] loading " + list.length + " ZombieLite plugins alongside the bridge");
			return new ArrayList<>(Arrays.asList(list));
		}
		catch (ClassNotFoundException e)
		{
			System.err.println("[zombielite] not on the classpath -- bridge only");
			return new ArrayList<>();
		}
		catch (ReflectiveOperationException | RuntimeException e)
		{
			System.err.println("[zombielite] present but unloadable, continuing without it: " + e);
			return new ArrayList<>();
		}
	}

	@SuppressWarnings("unchecked")
	public static void main(String[] args) throws Exception
	{
		makeProcessDpiAware();
		List<Class<? extends Plugin>> plugins = zombieLitePlugins();
		plugins.add(ControllerPlugin.class);
		plugins.add(GauntletRecolorPlugin.class);
		ExternalPluginManager.loadBuiltin(plugins.toArray(new Class[0]));
		RuneLite.main(args);
	}
}
