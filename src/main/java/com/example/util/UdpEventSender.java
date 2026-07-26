package com.example.util;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;

public class UdpEventSender
{
	private static final String HOST = "127.0.0.1";
	private static final int PORT = 50555;

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

		try
		{
			byte[] data = event.getBytes(StandardCharsets.UTF_8);
			DatagramPacket packet = new DatagramPacket(
				data,
				data.length,
				InetAddress.getByName(HOST),
				PORT
			);
			socket.send(packet);
		}
		catch (Exception e)
		{
			System.err.println("UDP send failed: " + e.getMessage());
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
