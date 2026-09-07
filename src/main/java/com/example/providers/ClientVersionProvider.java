package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.RuneLiteProperties;
import net.runelite.client.callback.ClientThread;

import java.io.IOException;
import java.io.InputStream;
import java.net.URISyntaxException;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.CodeSource;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashMap;
import java.util.Map;

/**
 * {@code GET /client-version}: which game client this process is actually running, so the
 * Python side can refuse to drive it before anyone has READ it.
 *
 * Added 2026-09-07 with {@code tools/deobfuscate.py} and {@code ControllerAddon/deob_guard.py}.
 * The readable copy of the client under {@code data/decompiled/} (rl-suite's deobfuscation) is
 * how we know what the client sends home -- see CLAUDE.md "What the game client sends home".
 * Every RuneLite build re-obfuscates the injected client, so that readable copy goes stale
 * per BUILD, not per game revision; the identity that matters is therefore the jar itself,
 * and the SHA-256 of the jar the {@code client} class was loaded from is what
 * {@code deob-state.json} is keyed on. The game revision ({@link Client#getRevision()}) and
 * RuneLite's own version string ride along for the log line and for choosing rl-suite's
 * packet-name profile.
 *
 * The jar path comes from the {@code client} implementation class's code source, not from
 * {@code RuneLiteProperties}: under {@code ./gradlew.bat run} the injected client is an
 * ordinary classpath jar in the Gradle cache, and the code source is the one thing that
 * names exactly that file. Hashing a ~40 MB jar is done ONCE, lazily, on the HTTP thread
 * (never on the client thread) and cached for the life of the plugin.
 */
public class ClientVersionProvider extends BridgeProvider
{
	private volatile String cachedSha256;
	private volatile String cachedJarPath;
	private volatile Map<String, Object> cachedFingerprint;

	public ClientVersionProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/client-version", this::getVersion);

		// The fingerprint spawns `wmic` (LoginFingerprint.collect()), which used to run inline on
		// the FIRST request to this endpoint -- an HTTP worker thread -- with no timeout on the
		// process. A wmic that never exits (its stderr pipe fills with nobody draining it; see
		// LoginFingerprint.readSmbiosUuid()'s own fix) parked that pooled thread forever. Computed
		// here instead, once, off the request path entirely: getVersion() just reads the field,
		// which may be null for the first few ms of the plugin's life -- fine, and strictly better
		// than blocking a request on it.
		Thread t = new Thread(() -> cachedFingerprint = com.example.util.LoginFingerprint.collect(),
			"client-version-fingerprint");
		t.setDaemon(true);
		t.start();
	}

	private Object getVersion()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("runeliteVersion", RuneLiteProperties.getVersion());
		data.put("runeliteCommit", RuneLiteProperties.getCommit());
		Object revision = onClient(() -> client.getRevision());
		data.put("revision", revision);

		Path jar = jarOf(client.getClass());
		data.put("injectedClientJar", jar == null ? null : jar.toString());
		data.put("injectedClientSha256", sha256Cached(jar));

		// What the login packet will carry, computed the client's way (LoginFingerprint). Computed
		// once by a background thread started from register() -- see there for why.
		data.put("fingerprint", cachedFingerprint);
		return data;
	}

	private String sha256Cached(Path jar)
	{
		if (jar == null)
		{
			return null;
		}
		String path = jar.toString();
		if (cachedSha256 != null && path.equals(cachedJarPath))
		{
			return cachedSha256;
		}
		try
		{
			String sha = sha256(jar);
			cachedJarPath = path;
			cachedSha256 = sha;
			return sha;
		}
		catch (IOException e)
		{
			return null;
		}
	}

	/**
	 * The jar (or directory) a class was loaded from, or null when the class loader does not
	 * say (a synthetic/generated class, or a code source without a file location).
	 */
	static Path jarOf(Class<?> clazz)
	{
		try
		{
			CodeSource source = clazz.getProtectionDomain().getCodeSource();
			if (source == null)
			{
				return null;
			}
			URL location = source.getLocation();
			if (location == null || !"file".equals(location.getProtocol()))
			{
				return null;
			}
			return Paths.get(location.toURI());
		}
		catch (URISyntaxException | SecurityException | IllegalArgumentException e)
		{
			return null;
		}
	}

	/** Lower-case hex SHA-256 of a file, streamed -- the injected client is tens of MB. */
	static String sha256(Path file) throws IOException
	{
		MessageDigest digest;
		try
		{
			digest = MessageDigest.getInstance("SHA-256");
		}
		catch (NoSuchAlgorithmException e)
		{
			throw new IOException(e);
		}
		try (InputStream in = Files.newInputStream(file))
		{
			byte[] buf = new byte[1 << 16];
			int n;
			while ((n = in.read(buf)) > 0)
			{
				digest.update(buf, 0, n);
			}
		}
		StringBuilder hex = new StringBuilder(64);
		for (byte b : digest.digest())
		{
			hex.append(Character.forDigit((b >> 4) & 0xF, 16)).append(Character.forDigit(b & 0xF, 16));
		}
		return hex.toString();
	}
}
