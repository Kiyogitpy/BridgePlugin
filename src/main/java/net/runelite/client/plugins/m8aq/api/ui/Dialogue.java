package net.runelite.client.plugins.m8aq.api.ui;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;

/** Provides copied text and choices from the active dialogue interfaces. */
public final class Dialogue
{
	private Dialogue()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable dialogue snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		Widget optionsWidget = visible(client.getWidget(InterfaceID.Chatmenu.OPTIONS));
		List<String> options = new ArrayList<>();
		if (optionsWidget != null && optionsWidget.getChildren() != null)
		{
			for (Widget option : optionsWidget.getChildren())
			{
				if (option != null && !option.isHidden())
				{
					options.add(option.getText());
				}
			}
		}
		Widget item = visible(client.getWidget(InterfaceID.Objectbox.ITEM));
		Widget item2 = visible(client.getWidget(InterfaceID.ObjectboxDouble.MODEL2));
		int itemId = item == null ? -1 : item.getItemId();
		int secondItemId = item2 == null ? -1 : item2.getItemId();
		return new State(text(client, InterfaceID.ChatLeft.NAME), text(client, InterfaceID.ChatLeft.TEXT),
			text(client, InterfaceID.ChatRight.TEXT), Collections.unmodifiableList(options),
			firstText(client, InterfaceID.Objectbox.TEXT, InterfaceID.ObjectboxDouble.TEXT),
			itemId, itemName(client, itemId), secondItemId, itemName(client, secondItemId));
	}

	private static String itemName(Client client, int itemId)
	{
		ItemComposition item = itemId < 0 ? null : client.getItemDefinition(itemId);
		return item == null ? null : item.getName();
	}

	private static String text(Client client, int id)
	{
		Widget widget = visible(client.getWidget(id));
		return widget == null ? null : widget.getText();
	}

	private static String firstText(Client client, int first, int second)
	{
		String value = text(client, first);
		return value == null ? text(client, second) : value;
	}

	private static Widget visible(Widget widget)
	{
		return widget == null || widget.isHidden() ? null : widget;
	}

	/** Immutable current-dialogue state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return speaking NPC name, or null */ private final String npcName;
		/** @return NPC dialogue text, or null */ private final String npcText;
		/** @return player dialogue text, or null */ private final String playerText;
		/** @return immutable visible response choices */ private final List<String> options;
		/** @return object-box dialogue text, or null */ private final String itemText;
		/** @return first displayed item ID, or -1 */ private final int itemId;
		/** @return first displayed item name, or null */ private final String itemName;
		/** @return second displayed item ID, or -1 */ private final int secondItemId;
		/** @return second displayed item name, or null */ private final String secondItemName;
	}
}
