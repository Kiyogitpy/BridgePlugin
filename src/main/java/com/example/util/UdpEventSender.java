package com.example.util;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;

public class UdpEventSender
{
	private static final String HOST = "127.0.0.1";

	// Two destination ports, not one -- controller.py's own event stream (PORT, 50555) and
	// mcp_debug_server.py's poll_events (DEBUG_PORT, 50556, see ControllerAddon/settings.py's
	// DEBUG_UDP_PORT) used to share a single port, which meant whichever of those two Python
	// processes happened to bind() it first won and the other's bind attempt just failed --
	// CONFIRMED live 2026-08-13: an mcp_debug_server.py poll_events() call during a debugging
	// session bound the shared port, and controller.py's own launch right after crashed with
	// OSError [WinError 10048] trying to bind the same one. Sending every event to both fixed
	// ports instead means each process's bind is now for a port only it ever binds -- there is
	// no longer a race to lose.
	private static final int PORT = 50555;
	private static final int DEBUG_PORT = 50556;

	private DatagramSocket socket;

	public void start()
	{
		try
		{
			socket = new DatagramSocket();
		}
		catch (Exception e)
		{
			System.err.println("UDP event sender failed to start: " + e.getMessage());
		}
	}

	public void stop()
	{
		if (socket != null)
		{
			socket.close();
			socket = null;
		}
	}

	public void send(String event)
	{
		if (socket == null)
		{
			return;
		}

		sendTo(event, PORT);
		sendTo(event, DEBUG_PORT);
	}

	private void sendTo(String event, int port)
	{
		try
		{
			byte[] data = event.getBytes(StandardCharsets.UTF_8);
			DatagramPacket packet = new DatagramPacket(
				data,
				data.length,
				InetAddress.getByName(HOST),
				port
			);
			socket.send(packet);
		}
		catch (Exception e)
		{
			System.err.println("UDP send failed (port " + port + "): " + e.getMessage());
		}
	}

	public void send(String type, Object payload)
	{
		if (payload == null)
		{
			send(type);
			return;
		}
		send(type + "|" + JsonUtil.toJson(payload));
	}
}
