package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetInfo;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class BankProvider extends BridgeProvider
{
	public BankProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/bank", () -> onClient(this::getBank));
	}

	private Object getBank()
	{
		Map<String, Object> data = new HashMap<>();

		Widget container = client.getWidget(WidgetInfo.BANK_CONTAINER);
		if (container == null || container.isHidden())
		{
			data.put("open", false);
			return data;
		}

		data.put("open", true);

		Point canvasScreenLoc = RuneLiteUtil.getCanvasScreenLocation(client);

		List<Map<String, Object>> slots = new ArrayList<>();
		Widget items = client.getWidget(WidgetInfo.BANK_ITEM_CONTAINER);
		Widget[] children = items != null ? items.getDynamicChildren() : null;
		if (children != null)
		{
			for (int i = 0; i < children.length; i++)
			{
				Widget child = children[i];
				if (child == null || child.isHidden() || child.getItemId() <= 0)
				{
					continue;
				}
				slots.add(buildSlotData(i, child, canvasScreenLoc));
			}
		}
		data.put("items", slots);

		data.put("depositInventory", buttonPoint(WidgetInfo.BANK_DEPOSIT_INVENTORY, canvasScreenLoc));
		data.put("depositEquipment", buttonPoint(WidgetInfo.BANK_DEPOSIT_EQUIPMENT, canvasScreenLoc));

		// The withdraw quantity-mode buttons (1/5/10/X/All) aren't in the older WidgetInfo enum,
		// only the newer generated InterfaceID constants -- packed group/child ints, same
		// client.getWidget(int) lookup either way.
		Map<String, Object> quantity = new HashMap<>();
		quantity.put("one", buttonPoint(InterfaceID.Bankmain.QUANTITY1, canvasScreenLoc));
		quantity.put("five", buttonPoint(InterfaceID.Bankmain.QUANTITY5, canvasScreenLoc));
		quantity.put("ten", buttonPoint(InterfaceID.Bankmain.QUANTITY10, canvasScreenLoc));
		quantity.put("x", buttonPoint(InterfaceID.Bankmain.QUANTITYX, canvasScreenLoc));
		quantity.put("all", buttonPoint(InterfaceID.Bankmain.QUANTITYALL, canvasScreenLoc));
		data.put("quantityButtons", quantity);

		return data;
	}

	private Map<String, Object> buildSlotData(int slot, Widget itemWidget, Point canvasScreenLoc)
	{
		Map<String, Object> data = new HashMap<>();
		data.put("slot", slot);
		data.put("id", itemWidget.getItemId());
		data.put("qty", itemWidget.getItemQuantity());

		ItemComposition comp = client.getItemDefinition(itemWidget.getItemId());
		if (comp != null)
		{
			data.put("name", comp.getName());
		}

		Rectangle bounds = itemWidget.getBounds();
		if (bounds != null)
		{
			data.put("screenX", (int) bounds.getCenterX() + canvasScreenLoc.x);
			data.put("screenY", (int) bounds.getCenterY() + canvasScreenLoc.y);
		}
		return data;
	}

	private Map<String, Object> buttonPoint(WidgetInfo widgetInfo, Point canvasScreenLoc)
	{
		Widget widget = client.getWidget(widgetInfo);
		return boundsPoint(widget, canvasScreenLoc);
	}

	private Map<String, Object> buttonPoint(int packedWidgetId, Point canvasScreenLoc)
	{
		Widget widget = client.getWidget(packedWidgetId);
		return boundsPoint(widget, canvasScreenLoc);
	}

	private Map<String, Object> boundsPoint(Widget widget, Point canvasScreenLoc)
	{
		Map<String, Object> point = new HashMap<>();

		if (widget == null || widget.isHidden())
		{
			return point;
		}

		Rectangle bounds = widget.getBounds();
		if (bounds == null)
		{
			return point;
		}

		point.put("screenX", (int) bounds.getCenterX() + canvasScreenLoc.x);
		point.put("screenY", (int) bounds.getCenterY() + canvasScreenLoc.y);
		return point;
	}
}
