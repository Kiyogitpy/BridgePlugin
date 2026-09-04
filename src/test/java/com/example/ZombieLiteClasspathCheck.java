package com.example;

import java.util.List;

/**
 * `./gradlew.bat checkZombieLite`: proves the ../ZombieLite jar on the `run` classpath actually
 * links -- every class in its CustomPlugins.CLASSLIST resolves against the RuneLite version the
 * bridge builds with -- without launching a client. Run it after pulling ZombieLite or after a
 * RuneLite `latest.release` bump; a class that fails here would otherwise fail inside
 * ExamplePluginTest.main, where the only symptom is a client that comes up without those plugins.
 * Classes are loaded WITHOUT static initialisation (plugin statics reach into RuneLite's injector,
 * which only exists in a real client), so this catches missing dependencies and API drift in the
 * class shapes themselves, not runtime behaviour.
 */
public class ZombieLiteClasspathCheck
{
	public static void main(String[] args) throws Exception
	{
		Class<?> custom = Class.forName("com.salvation.plugins.CustomPlugins", false,
			ZombieLiteClasspathCheck.class.getClassLoader());
		// getField initialises CustomPlugins itself, which only references the plugin Class objects.
		Object[] list = (Object[]) custom.getField("CLASSLIST").get(null);
		int failures = 0;
		for (Object entry : list)
		{
			Class<?> plugin = (Class<?>) entry;
			try
			{
				// Force linkage (superclass, interfaces, field/method descriptors) without <clinit>.
				plugin.getDeclaredMethods();
				plugin.getDeclaredFields();
			}
			catch (Throwable t)
			{
				failures++;
				System.err.println("FAIL " + plugin.getName() + ": " + t);
			}
		}
		System.err.println("[zombielite] " + (list.length - failures) + "/" + list.length + " plugin classes link"
			+ (failures == 0 ? "" : " -- " + failures + " FAILED"));
		if (failures > 0)
		{
			System.exit(1);
		}
	}
}
