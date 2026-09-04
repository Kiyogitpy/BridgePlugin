package com.example.util;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.atomic.AtomicLong;

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

	// ASYNC AS OF 2026-09-01: socket.send() used to run synchronously on the CALLER's thread
	// -- for game events that is RuneLite's client/render thread, two blocking writes per
	// event. On an event-dense scene (a mass-world GOTR arena: other players' ground-item
	// drops, cell barriers transforming, up to SPAWN_EVENT_CAP_PER_TICK spawn events a tick)
	// that is up to ~80 blocking writes per 0.6s game tick, USER-REPORTED as constant client
	// "lag spikes". send() now only enqueues; a daemon worker owns all socket I/O. The queue
	// DROPS when full rather than ever blocking the client thread -- the Python mirror is an
	// accelerator with an HTTP fallback by contract (see CLAUDE.md), so a dropped event costs
	// latency, never correctness. NOTE FOR FUTURE EDITS: JSON serialization deliberately stays
	// on the caller's thread (send(String, Object) below) -- payload objects can reference
	// live client state, which must not be read from the worker thread.
	private static final int QUEUE_CAPACITY = 512;

	private DatagramSocket socket;
	private InetAddress host;
	private Thread worker;
	private BlockingQueue<String> queue;
	private final AtomicLong dropped = new AtomicLong();
	private long lastDropReport;

	public void start()
	{
		try
		{
			socket = new DatagramSocket();
			host = InetAddress.getByName(HOST);
		}
		catch (Exception e)
		{
			System.err.println("UDP event sender failed to start: " + e.getMessage());
			socket = null;
			return;
		}

		queue = new ArrayBlockingQueue<>(QUEUE_CAPACITY);
		worker = new Thread(this::drainLoop, "udp-event-sender");
		worker.setDaemon(true);
		worker.start();
	}

	public void stop()
	{
		if (worker != null)
		{
			worker.interrupt();
			worker = null;
		}
		if (socket != null)
		{
			socket.close();
			socket = null;
		}
		queue = null;
	}

	public void send(String event)
	{
		BlockingQueue<String> q = queue;
		if (q == null)
		{
			return;
		}
		if (!q.offer(event))
		{
			long total = dropped.incrementAndGet();
			long now = System.currentTimeMillis();
			// Loud but rate-limited: a full queue means the worker cannot keep up,
			// which is worth knowing about -- once a minute, not once per event.
			if (now - lastDropReport > 60_000L)
			{
				lastDropReport = now;
				System.err.println("UDP event queue full -- dropped " + total
					+ " events so far this session");
			}
		}
	}

	private void drainLoop()
	{
		BlockingQueue<String> q = queue;
		DatagramSocket s = socket;
		if (q == null || s == null)
		{
			return;
		}
		try
		{
			while (!Thread.currentThread().isInterrupted())
			{
				String event = q.take();
				sendTo(s, event, PORT);
				sendTo(s, event, DEBUG_PORT);
			}
		}
		catch (InterruptedException e)
		{
			// stop() -- fall through and exit.
		}
	}

	private void sendTo(DatagramSocket s, String event, int port)
	{
		try
		{
			byte[] data = event.getBytes(StandardCharsets.UTF_8);
			DatagramPacket packet = new DatagramPacket(data, data.length, host, port);
			s.send(packet);
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
		// toJson on the CALLER's thread, always -- see the async note above.
		send(type + "|" + JsonUtil.toJson(payload));
	}
}
