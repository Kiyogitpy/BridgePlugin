package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.*;
import net.runelite.api.coords.WorldPoint;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.game.ItemManager;

import java.awt.Point;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class GroundItemProvider extends BridgeProvider
{
	private final ItemManager itemManager;

	public GroundItemProvider(Client client, ClientThread clientThread, BridgeHttpServer server, ItemManager itemManager)
	{
		super(client, clientThread, server);
		this.itemManager = itemManager;
	}

	@Override
	public void register()
	{
		server.register("/ground-items", () -> onClient(this::getGroundItems));
	}

	private Object getGroundItems()
	{
		List<Map<String, Object>> list = new ArrayList<>();
		Scene scene = client.getScene();
		if (scene == null)
		{
			return list;
		}

		Tile[][][] tiles = scene.getTiles();
		int plane = client.getPlane();
		Point canvasScreenLoc = RuneLiteUtil.getCanvasScreenLocation(client);

		for (int x = 0; x < tiles[plane].length; x++)
		{
			for (int y = 0; y < tiles[plane][x].length; y++)
			{
				Tile tile = tiles[plane][x][y];
				if (tile == null)
				{
					continue;
				}

				ItemLayer layer = tile.getItemLayer();
				if (layer == null)
				{
					continue;
				}

				Node node = layer.getTop();
				while (node instanceof TileItem)
				{
					TileItem item = (TileItem) node;
					Map<String, Object> data = new HashMap<>();
					WorldPoint wp = tile.getWorldLocation();

					data.put("id", item.getId());
					data.put("qty", item.getQuantity());
					data.put("x", wp.getX());
					data.put("y", wp.getY());
					data.put("plane", wp.getPlane());

					ItemComposition comp = client.getItemDefinition(item.getId());
					if (comp != null)
					{
						data.put("name", comp.getName());
						data.put("stackable", comp.isStackable());
					}

					int gePrice = itemManager.getItemPrice(item.getId());
					data.put("value", gePrice);
					data.put("totalValue", gePrice * (long) item.getQuantity());

					net.runelite.api.Point point = RuneLiteUtil.localToScreen(client, tile.getLocalLocation(), plane);
					if (point != null)
					{
						data.put("screenX", point.getX() + canvasScreenLoc.x);
						data.put("screenY", point.getY() + canvasScreenLoc.y);
					}

					data.put("distance", RuneLiteUtil.distanceToPlayer(client, wp));
					data.putAll(RuneLiteUtil.visibility(client, tile.getLocalLocation(), plane, true));
					list.add(data);

					node = node.getNext();
				}
			}
		}
		return list;
	}
}
