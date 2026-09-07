package com.example.providers;

import org.junit.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

public class ClientVersionProviderTest
{
	@Test
	public void sha256MatchesTheReferenceVectorAndPythonsHashlib() throws IOException
	{
		// deob_guard.py compares this string with hashlib.sha256(jar).hexdigest(), so the two
		// implementations must agree byte for byte: lower-case hex, no separators, 64 chars.
		Path f = Files.createTempFile("cvp", ".bin");
		try
		{
			Files.write(f, "abc".getBytes(StandardCharsets.US_ASCII));
			assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
				ClientVersionProvider.sha256(f));
			Files.write(f, new byte[0]);
			assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
				ClientVersionProvider.sha256(f));
		}
		finally
		{
			Files.deleteIfExists(f);
		}
	}

	@Test
	public void jarOfResolvesAClassLoadedFromTheFilesystem()
	{
		// This test class itself comes from build/classes on disk: a "file:" code source, so the
		// resolver must answer with an existing path rather than null. The injected client's
		// `client` class is loaded the same way (a jar in the Gradle cache).
		Path p = ClientVersionProvider.jarOf(ClientVersionProviderTest.class);
		assertNotNull(p);
		assertTrue(p.toString(), Files.exists(p));
	}

	@Test
	public void jarOfIsNullForABootstrapClass()
	{
		// java.lang.String has no code source under the platform loader: the provider must report
		// "unknown" rather than throw, because /client-version is polled before every run.
		assertEquals(null, ClientVersionProvider.jarOf(String.class));
	}
}
