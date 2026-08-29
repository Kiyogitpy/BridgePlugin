package net.runelite.client.plugins.m8aq.api.ui;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.gameval.VarClientID;
import net.runelite.api.widgets.Widget;

/** Provides copied root metadata and relevant currently loaded widgets. */
public final class Interfaces
{
	private static final Pattern NUMBER = Pattern.compile("[0-9][0-9,]*");

	private Interfaces()
	{
	}

	/**
	 * @param widget widget
	 * @return whether the widget exists and is not hidden
	 */
	public static boolean isVisible(Widget widget)
	{
		return widget != null && !widget.isHidden();
	}

	/**
	 * Reads the shared message-layer query used by bank-style searches.
	 *
	 * @param client RuneLite client
	 * @return raw query, an empty string for an active empty search, or null when inactive
	 */
	public static String getSearchQuery(Client client)
	{
		int mode = client.getVarcIntValue(VarClientID.MESLAYERMODE);
		String input = client.getVarcStrValue(VarClientID.MESLAYERINPUT);
		return mode == 11 || mode <= 0 && input != null && !input.isEmpty()
			? input == null ? "" : input : null;
	}

	/**
	 * Reads a query only while the requested message-layer mode is active.
	 *
	 * @param client RuneLite client
	 * @param mode expected message-layer mode
	 * @return raw query, an empty string for an active empty search, or null when inactive
	 */
	public static String getSearchQuery(Client client, int mode)
	{
		if (client.getVarcIntValue(VarClientID.MESLAYERMODE) != mode)
		{
			return null;
		}
		String input = client.getVarcStrValue(VarClientID.MESLAYERINPUT);
		return input == null ? "" : input;
	}

	/**
	 * Reads the last non-negative integer displayed by a visible widget.
	 *
	 * @param widget text widget
	 * @return parsed integer, or -1 when unavailable or invalid
	 */
	public static int getLastInteger(Widget widget)
	{
		if (!isVisible(widget) || widget.getText() == null)
		{
			return -1;
		}

		Matcher matcher = NUMBER.matcher(widget.getText());
		String value = null;
		while (matcher.find())
		{
			value = matcher.group();
		}
		if (value == null)
		{
			return -1;
		}
		try
		{
			return Integer.parseInt(value.replace(",", ""));
		}
		catch (NumberFormatException ex)
		{
			return -1;
		}
	}

	/**
	 * @param client RuneLite client
	 * @return immutable interface snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		List<WidgetEntry> roots = new ArrayList<>();
		List<WidgetEntry> allWidgets = new ArrayList<>();
		Widget[] rootWidgets = client.getWidgetRoots();
		if (rootWidgets != null)
		{
			for (Widget widget : rootWidgets)
			{
				if (widget != null)
				{
					WidgetEntry root = copy(widget);
					roots.add(root);
					addIfRelevant(root, allWidgets);
					copyDescendants(widget, allWidgets, true);
				}
			}
		}
		Widget selected = client.getSelectedWidget();
		Widget focused = client.getFocusedInputFieldWidget();
		List<WidgetEntry> rootSnapshot = Collections.unmodifiableList(roots);
		List<WidgetEntry> widgetSnapshot = Collections.unmodifiableList(allWidgets);
		return new State(client.getTopLevelInterfaceId(), rootSnapshot,
			widgetSnapshot, widgetSnapshot(widgetSnapshot),
			selected == null ? -1 : selected.getId(), focused == null ? -1 : focused.getId());
	}

	/**
	 * Copies every currently loaded widget beneath one fixed component, including structural widgets.
	 *
	 * @param root fixed or dynamic subtree root
	 * @return immutable flattened subtree, or an empty list when the root is unavailable
	 */
	public static List<WidgetEntry> snapshot(Widget root)
	{
		if (root == null)
		{
			return Collections.emptyList();
		}
		List<WidgetEntry> widgets = new ArrayList<>();
		widgets.add(copy(root));
		copyDescendants(root, widgets, false);
		return Collections.unmodifiableList(widgets);
	}

	/**
	 * Creates exact lookup state for one or more immutable widget groups.
	 *
	 * @param groups widget groups in lookup order
	 * @return immutable widget lookup snapshot
	 */
	@SafeVarargs
	public static WidgetSnapshot widgetSnapshot(List<WidgetEntry>... groups)
	{
		List<List<WidgetEntry>> copy = new ArrayList<>(groups.length);
		for (List<WidgetEntry> group : groups)
		{
			copy.add(List.copyOf(Objects.requireNonNull(group, "group")));
		}
		return new WidgetSnapshot(Collections.unmodifiableList(copy));
	}

	private static void copyDescendants(Widget widget, List<WidgetEntry> entries, boolean relevantOnly)
	{
		copyChildren(widget.getDynamicChildren(), entries, relevantOnly);
		copyChildren(widget.getStaticChildren(), entries, relevantOnly);
		copyChildren(widget.getNestedChildren(), entries, relevantOnly);
	}

	private static void copyChildren(Widget[] children, List<WidgetEntry> entries, boolean relevantOnly)
	{
		if (children == null)
		{
			return;
		}
		for (Widget child : children)
		{
			if (child != null)
			{
				WidgetEntry copy = copy(child);
				if (!relevantOnly)
				{
					entries.add(copy);
				}
				else
				{
					addIfRelevant(copy, entries);
				}
				copyDescendants(child, entries, relevantOnly);
			}
		}
	}

	private static void addIfRelevant(WidgetEntry widget, List<WidgetEntry> entries)
	{
		if (!isBlank(widget.text) || !isBlank(widget.name) || !widget.actions.isEmpty()
			|| widget.itemId >= 0 && widget.itemId != ItemID.BLANKOBJECT)
		{
			entries.add(widget);
		}
	}

	private static boolean isBlank(String value)
	{
		return value == null || value.isBlank();
	}

	private static WidgetEntry copy(Widget widget)
	{
		return new WidgetEntry(widget.getId(), widget.getParentId(), widget.getIndex(), widget.getType(), widget.getContentType(),
			widget.isHidden(), widget.getText(), widget.getName(), widget.getItemId(), widget.getItemQuantity(),
			copyActions(widget), widget.getRelativeX(), widget.getRelativeY(), widget.getWidth(), widget.getHeight());
	}

	private static Map<Integer, String> copyActions(Widget widget)
	{
		String[] actions = widget.getActions();
		if (actions == null)
		{
			return Collections.emptyMap();
		}
		Map<Integer, String> copy = new LinkedHashMap<>();
		for (int index = 0; index < actions.length; index++)
		{
			if (!isBlank(actions[index]))
			{
				copy.put(index + 1, actions[index]);
			}
		}
		return Collections.unmodifiableMap(copy);
	}

	/** Immutable widget entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class WidgetEntry
	{
		/** @return packed widget ID */ private final int id;
		/** @return packed parent ID */ private final int parentId;
		/** @return dynamic-child index, or -1 for a fixed component */ private final int index;
		/** @return widget type */ private final int type;
		/** @return widget content type */ private final int contentType;
		/** @return whether hidden */ private final boolean hidden;
		/** @return raw widget text */ private final String text;
		/** @return raw widget name */ private final String name;
		/** @return item ID */ private final int itemId;
		/** @return item quantity */ private final int itemQuantity;
		/** @return immutable non-empty actions keyed by one-based operation number */
		private final Map<Integer, String> actions;
		/** @return relative X */ private final int x;
		/** @return relative Y */ private final int y;
		/** @return width */ private final int width;
		/** @return height */ private final int height;
	}

	/** Immutable exact lookup over copied fixed and dynamic widget groups. */
	public static final class WidgetSnapshot
	{
		private final List<List<WidgetEntry>> groups;

		private WidgetSnapshot(List<List<WidgetEntry>> groups)
		{
			this.groups = groups;
		}

		/**
		 * Finds a fixed widget by packed component ID.
		 *
		 * @param packedId packed component ID
		 * @return immutable widget entry, or {@code null} when it is not loaded
		 */
		public WidgetEntry getWidget(int packedId)
		{
			return getWidget(packedId, -1);
		}

		/**
		 * Finds a widget by packed component ID and child index.
		 *
		 * @param packedId packed component ID
		 * @param index dynamic-child index, or {@code -1} for a fixed component
		 * @return immutable widget entry, or {@code null} when it is not loaded
		 */
		public WidgetEntry getWidget(int packedId, int index)
		{
			for (List<WidgetEntry> widgets : groups)
			{
				for (WidgetEntry widget : widgets)
				{
					if (widget.id == packedId && widget.index == index)
					{
						return widget;
					}
				}
			}
			return null;
		}
	}

	/** Shared exact lookup interface for states that contain a widget snapshot. */
	public abstract static class WidgetLookup
	{
		/** @return immutable widget lookup snapshot */
		protected abstract WidgetSnapshot getWidgetSnapshot();

		/**
		 * Finds a fixed widget by packed component ID.
		 *
		 * @param packedId packed component ID
		 * @return immutable widget entry, or {@code null} when it is not loaded
		 */
		public final WidgetEntry getWidget(int packedId)
		{
			return getWidgetSnapshot().getWidget(packedId);
		}

		/**
		 * Finds a widget by packed component ID and child index.
		 *
		 * @param packedId packed component ID
		 * @param index dynamic-child index, or {@code -1} for a fixed component
		 * @return immutable widget entry, or {@code null} when it is not loaded
		 */
		public final WidgetEntry getWidget(int packedId, int index)
		{
			return getWidgetSnapshot().getWidget(packedId, index);
		}
	}

	/** Immutable interface state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends WidgetLookup
	{
		/** @return active top-level interface group ID */ private final int topLevelInterfaceId;
		/** @return immutable root-widget metadata */ private final List<WidgetEntry> roots;
		/** @return immutable flattened relevant widgets from every loaded widget tree */
		private final List<WidgetEntry> widgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient WidgetSnapshot widgetSnapshot;
		/** @return selected packed widget ID, or -1 */ private final int selectedWidgetId;
		/** @return focused input packed widget ID, or -1 */ private final int focusedInputWidgetId;

	}
}
