package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;

import java.util.HashMap;
import java.util.Map;

public class CameraProvider extends BridgeProvider
{
	public CameraProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/camera", () -> onClient(this::getCamera));
	}

	private Object getCamera()
	{
		Map<String, Object> cam = new HashMap<>();
		cam.put("pitch", client.getCameraPitch());
		cam.put("yaw", client.getCameraYaw());
		cam.put("x", client.getCameraX());
		cam.put("y", client.getCameraY());
		cam.put("z", client.getCameraZ());
		return cam;
	}
}
