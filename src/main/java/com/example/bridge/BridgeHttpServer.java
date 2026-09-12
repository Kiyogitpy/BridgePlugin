package com.example.bridge;

import com.example.util.JsonUtil;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.function.Function;
import java.util.function.Supplier;

public class BridgeHttpServer
{
	private HttpServer server;

	/** Loopback unless told otherwise -- the bridge has no auth (spec V9, 2026-09-07). */
	static String bindAddress(boolean bindAll)
	{
		return bindAll ? "0.0.0.0" : "127.0.0.1";
	}

	public void start(int port)
	{
		start(port, false);
	}

	public void start(int port, boolean bindAll)
	{
		try
		{
			String host = bindAddress(bindAll);
			server = HttpServer.create(new InetSocketAddress(host, port), 0);
			server.setExecutor(Executors.newCachedThreadPool());
			server.start();
			System.out.println("Bridge running on http://" + host + ":" + port
				+ (bindAll ? "  (ALL interfaces -- reachable from the LAN)" : ""));
		}
		catch (Exception e)
		{
			throw new RuntimeException("Failed to start HTTP bridge", e);
		}
	}

	public void stop()
	{
		if (server != null)
		{
			server.stop(0);
			server = null;
		}
	}

	public void register(String path, Supplier<Object> provider)
	{
		server.createContext(path, exchange -> handle(exchange, provider));
	}

	public void registerQuery(String path, Function<Map<String, String>, Object> provider)
	{
		server.createContext(path, exchange -> handle(exchange, () -> provider.apply(parseQuery(exchange.getRequestURI()))));
	}

	public void registerPrefix(String prefix, Function<String, Object> provider)
	{
		server.createContext(prefix, exchange -> {
			String fullPath = exchange.getRequestURI().getPath();
			String remainder = fullPath.length() > prefix.length() ? fullPath.substring(prefix.length()) : "";
			handle(exchange, () -> provider.apply(remainder));
		});
	}

	private void handle(HttpExchange exchange, Supplier<Object> provider)
	{
		try
		{
			write(exchange, provider.get());
		}
		catch (Exception e)
		{
			e.printStackTrace();
			try
			{
				exchange.sendResponseHeaders(500, 0);
				exchange.getResponseBody().close();
			}
			catch (Exception ignored)
			{
			}
		}
	}

	private void write(HttpExchange exchange, Object obj)
	{
		try
		{
			if (obj == null)
			{
				obj = Collections.emptyMap();
			}

			String json = JsonUtil.toJson(obj);
			byte[] bytes = json.getBytes(StandardCharsets.UTF_8);

			exchange.getResponseHeaders().add("Content-Type", "application/json");
			exchange.getResponseHeaders().add("Access-Control-Allow-Origin", "*");
			exchange.sendResponseHeaders(200, bytes.length);

			try (OutputStream os = exchange.getResponseBody())
			{
				os.write(bytes);
			}
		}
		catch (Exception e)
		{
			e.printStackTrace();
			try
			{
				exchange.sendResponseHeaders(500, 0);
				exchange.getResponseBody().close();
			}
			catch (Exception ignored)
			{
			}
		}
	}

	private Map<String, String> parseQuery(URI uri)
	{
		Map<String, String> query = new HashMap<>();
		String raw = uri.getRawQuery();
		if (raw == null || raw.isEmpty())
		{
			return query;
		}

		for (String pair : raw.split("&"))
		{
			int idx = pair.indexOf('=');
			if (idx > 0)
			{
				query.put(decode(pair.substring(0, idx)), decode(pair.substring(idx + 1)));
			}
			else
			{
				query.put(decode(pair), "");
			}
		}
		return query;
	}

	/**
	 * Percent-decode one query name/value. This read getRawQuery() and never decoded, which is
	 * fine until a value legitimately contains an encoded character -- CONFIRMED live 2026-09-13:
	 * Python's requests encodes a comma as %2C, so /events/subscribe?varbits=a%2Cb%2Cc reached
	 * EventSubscriptions.parseIds as ONE unparseable token and every routine subscribing to two
	 * or more varbits silently got an EMPTY subscription set (HTTP 200, well-formed reply, no
	 * warning anywhere) and ran with varbit events off. A single id has no comma, which is why
	 * this hid for so long. Applies to every registerQuery endpoint, not just that one -- any
	 * value with a space, &, = or + was equally broken.
	 *
	 * Never throws: a malformed escape yields the raw text rather than a 500 on a query that
	 * used to work.
	 */
	private static String decode(String s)
	{
		try
		{
			return java.net.URLDecoder.decode(s, StandardCharsets.UTF_8.name());
		}
		catch (Exception e)
		{
			return s;
		}
	}
}
