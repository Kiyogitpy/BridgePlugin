package com.example.util;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/**
 * What the game client puts in its login {@code PlatformInfo}, computed the SAME way the client
 * computes it -- so a launch can be checked for fingerprint parity BEFORE the account logs in.
 *
 * Every rule here was read from the rl-suite deobfuscation (CLAUDE.md "What the game client
 * sends home", spec 2026-09-07-telemetry-vector-mitigation section 7): the Java client sends the
 * OS/Java property strings (os.* lower-cased), max heap in MB + 1, the CPU count, the SMBIOS
 * UUID via {@code wmic csproduct get UUID} (second line; a fixed placeholder on failure), and --
 * found only by diffing our own 1.12.38 run -- its own exe file name (first 10 chars), its PARENT
 * process's exe file name (first 10 chars) and the file name of the first {@code -javaagent}
 * argument (up to 22 chars). Nothing here is sent anywhere by us; it is served read-only on
 * {@code GET /client-version} for the parity check.
 */
public final class LoginFingerprint
{
	public static final String PLACEHOLDER_UUID = "12345678-0000-0000-0000-123456789012";
	static final int NAME_LIMIT = 10;
	static final int AGENT_LIMIT = 22;

	private LoginFingerprint()
	{
	}

	static String truncate(String s, int max)
	{
		if (s == null)
		{
			return null;
		}
		return s.length() > max ? s.substring(0, max) : s;
	}

	static String fileName(String path)
	{
		if (path == null)
		{
			return null;
		}
		int i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
		return i >= 0 ? path.substring(i + 1) : path;
	}

	/**
	 * The client matches the argument by {@code arg.substring(0, 10).hashCode() == -1094877034},
	 * which is exactly {@code "-javaagent".hashCode()}; startsWith is the readable equivalent.
	 */
	static String javaAgentName(List<String> jvmArgs)
	{
		for (String a : jvmArgs)
		{
			if (a.length() > 10 && a.startsWith("-javaagent"))
			{
				int eq = a.indexOf('=');
				if (eq == -1)
				{
					eq = a.length();
				}
				int sep = a.lastIndexOf('/', eq);
				if (sep == -1)
				{
					sep = a.lastIndexOf('\\', eq);
				}
				if (sep == -1)
				{
					sep = a.lastIndexOf(':');
				}
				return truncate(a.substring(sep + 1, eq), AGENT_LIMIT);
			}
		}
		return null;
	}

	/** The client joins every output line with "\n" and takes everything after the FIRST "\n", trimmed. */
	static String uuidFromWmic(String output)
	{
		if (output == null)
		{
			return PLACEHOLDER_UUID;
		}
		int nl = output.indexOf('\n');
		if (nl < 0)
		{
			return PLACEHOLDER_UUID;
		}
		String value = output.substring(nl).trim();
		return value.isEmpty() ? PLACEHOLDER_UUID : value;
	}

	private static String lower(String s)
	{
		return s == null ? null : s.toLowerCase(Locale.ROOT);
	}

	private static String readSmbiosUuid()
	{
		String os = System.getProperty("os.name", "");
		if (!os.toLowerCase(Locale.ROOT).contains("windows"))
		{
			return null;   // the mac/linux readers exist in the client too, but this project is Windows-only
		}
		try
		{
			Process p = Runtime.getRuntime().exec("wmic csproduct get UUID");
			StringBuilder sb = new StringBuilder();
			try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8)))
			{
				String line;
				while ((line = r.readLine()) != null)
				{
					sb.append(line).append('\n');
				}
			}
			return uuidFromWmic(sb.toString());
		}
		catch (Exception e)
		{
			return PLACEHOLDER_UUID;   // what the client sends when wmic is absent (Windows 11 24H2+)
		}
	}

	/** Assemble the block. Spawns wmic once; callers cache the result. Never throws. */
	public static Map<String, Object> collect()
	{
		Map<String, Object> f = new LinkedHashMap<>();
		try
		{
			ProcessHandle self = ProcessHandle.current();
			f.put("processCommand", truncate(fileName(self.info().command().orElse(null)), NAME_LIMIT));
			Optional<String> parentCmd = self.parent().flatMap(p -> p.info().command());
			f.put("parentProcessCommand", truncate(fileName(parentCmd.orElse(null)), NAME_LIMIT));
		}
		catch (Exception e)
		{
			f.put("processCommand", null);
			f.put("parentProcessCommand", null);
		}
		try
		{
			f.put("javaAgent", javaAgentName(ManagementFactory.getRuntimeMXBean().getInputArguments()));
			f.put("osName", lower(System.getProperty("os.name")));
			f.put("osVersion", lower(System.getProperty("os.version")));
			f.put("osArch", lower(System.getProperty("os.arch")));
			f.put("javaVendor", System.getProperty("java.vendor"));
			f.put("javaVersion", System.getProperty("java.version"));
			f.put("maxMemoryMb", (int) (Runtime.getRuntime().maxMemory() / 1048576L) + 1);
			f.put("availableProcessors", Runtime.getRuntime().availableProcessors());
			f.put("smbiosUuid", readSmbiosUuid());
		}
		catch (Exception e)
		{
			f.put("javaAgent", null);
			f.put("osName", null);
			f.put("osVersion", null);
			f.put("osArch", null);
			f.put("javaVendor", null);
			f.put("javaVersion", null);
			f.put("maxMemoryMb", null);
			f.put("availableProcessors", null);
			f.put("smbiosUuid", PLACEHOLDER_UUID);
		}
		return f;
	}
}
