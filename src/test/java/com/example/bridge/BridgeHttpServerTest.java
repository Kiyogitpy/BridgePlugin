package com.example.bridge;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class BridgeHttpServerTest
{
	@Test
	public void theDefaultIsLoopbackAndTheToggleIsAllInterfaces()
	{
		// The bridge serves game state, screenshots, the recorder's keystrokes and the bank
		// memory with no auth; before 2026-09-07 it was bound to every interface (spec V9).
		assertEquals("127.0.0.1", BridgeHttpServer.bindAddress(false));
		assertEquals("0.0.0.0", BridgeHttpServer.bindAddress(true));
	}
}
