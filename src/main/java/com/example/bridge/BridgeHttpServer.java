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

	public void start(int port)
	{
		try
		{
			server = HttpServer.create(new InetSocketAddress(port), 0);
			server.setExecutor(Executors.newCachedThreadPool());
			server.start();
			System.out.println("Bridge running on http://localhost:" + port);
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
				query.put(pair.substring(0, idx), pair.substring(idx + 1));
			}
			else
			{
				query.put(pair, "");
			}
		}
		return query;
	}
}
