package com.example.util;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

public class LoginFingerprintTest
{
	@Test
	public void truncateKeepsTheClientsPrefixLengths()
	{
		// The decompiled client keeps the first 10 chars of an exe name and 22 of an agent name.
		assertEquals("RuneLite.e", LoginFingerprint.truncate("RuneLite.exe", 10));
		assertEquals("java.exe", LoginFingerprint.truncate("java.exe", 10));
		assertNull(LoginFingerprint.truncate(null, 10));
	}

	@Test
	public void fileNameStripsEitherSeparator()
	{
		assertEquals("java.exe", LoginFingerprint.fileName("C:\\Users\\Kiyo\\.jdks\\jdk-11\\bin\\java.exe"));
		assertEquals("java", LoginFingerprint.fileName("/usr/lib/jvm/bin/java"));
		assertEquals("RuneLite.exe", LoginFingerprint.fileName("RuneLite.exe"));
		assertNull(LoginFingerprint.fileName(null));
	}

	@Test
	public void javaAgentNameFollowsTheClientsParse()
	{
		// classVS.method12108: the first argument whose 10-char prefix hashes to -1094877034
		// ("-javaagent"); take from after the last '/' or '\' (else ':') up to '=' (else the end),
		// at most 22 chars.
		assertEquals("agent.jar", LoginFingerprint.javaAgentName(Arrays.asList(
			"-Xmx512m", "-javaagent:C:\\tools\\agent.jar=opt1,opt2", "-ea")));
		assertEquals("agent.jar", LoginFingerprint.javaAgentName(Collections.singletonList(
			"-javaagent:/opt/agent.jar")));
		assertEquals("a-very-long-agent-name", LoginFingerprint.javaAgentName(Collections.singletonList(
			"-javaagent:a-very-long-agent-name-that-exceeds.jar")));
		assertNull(LoginFingerprint.javaAgentName(Arrays.asList("-Xmx512m", "-ea")));
		assertNull(LoginFingerprint.javaAgentName(Collections.emptyList()));
	}

	@Test
	public void uuidFromWmicTakesTheSecondLineAndFallsBackToThePlaceholder()
	{
		// wmic prints a header line, the value, then blank lines.
		assertEquals("1EBB33E4-2B90-8093-433F-50EBF62C698A",
			LoginFingerprint.uuidFromWmic("UUID\r\n1EBB33E4-2B90-8093-433F-50EBF62C698A\r\n\r\n"));
		assertEquals(LoginFingerprint.PLACEHOLDER_UUID, LoginFingerprint.uuidFromWmic(""));
		assertEquals(LoginFingerprint.PLACEHOLDER_UUID, LoginFingerprint.uuidFromWmic("UUID\n\n"));
		assertEquals(LoginFingerprint.PLACEHOLDER_UUID, LoginFingerprint.uuidFromWmic(null));
	}
}
