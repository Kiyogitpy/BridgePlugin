package com.example.controller;

import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.ui.ColorScheme;
import net.runelite.client.ui.PluginPanel;
import net.runelite.client.ui.components.PluginErrorPanel;

import javax.inject.Inject;
import javax.swing.*;
import javax.swing.border.EmptyBorder;
import javax.swing.event.DocumentEvent;
import javax.swing.event.DocumentListener;
import java.awt.*;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.util.*;
import java.util.List;

public class SettingsPanel extends PluginPanel
{
	private final Client client;
	private final ClientThread clientThread;
	private final ConfigManager configManager;
	private final ControllerConfig config;

	private final JPanel npcListPanel = new JPanel();
	private final PluginErrorPanel errorPanel = new PluginErrorPanel();
	private final JLabel statusLabel = new JLabel();

	private final Map<String, JCheckBox> npcCheckboxes = new LinkedHashMap<>();

	// Canonical set of chosen slayer targets -- both the nearby-monster checkboxes and the
	// search-based picker below read/write this same set and persist it to the one
	// "targetNpcNames" config key, so a name selected either way shows up consistently in both.
	private final Set<String> selectedTargets = new LinkedHashSet<>();

	private final JTextField searchField = new JTextField();
	private final DefaultListModel<ResultRow> searchResultsModel = new DefaultListModel<>();
	private final JList<ResultRow> searchResultsList = new JList<>(searchResultsModel);

	private final JPanel selectedTargetsPanel = new JPanel();

	// Chosen location index per task category, for categories with more than one known spot
	// (see SlayerLocations.java) -- persisted to the "taskLocationChoices" config key.
	private final Map<String, Integer> categoryLocationChoice = new LinkedHashMap<>();

	// Fields (not locals) so syncFromConfig() below can re-sync them whenever this config
	// changes from somewhere other than this panel, e.g. the wrench-icon config screen --
	// controllerEnabled/targetNpcNames/taskLocationChoices are all editable from both.
	private final JCheckBox enabledBox = new JCheckBox("Controller Enabled");

	@Inject
	SettingsPanel(Client client, ClientThread clientThread, ConfigManager configManager, ControllerConfig config)
	{
		this.client = client;
		this.clientThread = clientThread;
		this.configManager = configManager;
		this.config = config;

		selectedTargets.addAll(ConfigExporter.parseList(config.targetNpcNames()));
		categoryLocationChoice.putAll(ConfigExporter.parseLocationChoices(config.taskLocationChoices()));

		setLayout(new BorderLayout());
		setBackground(ColorScheme.DARK_GRAY_COLOR);
		setBorder(new EmptyBorder(10, 10, 10, 10));

		JPanel header = new JPanel();
		header.setLayout(new BoxLayout(header, BoxLayout.Y_AXIS));
		header.setBackground(ColorScheme.DARK_GRAY_COLOR);

		JLabel title = new JLabel("Controller Settings");
		title.setForeground(Color.WHITE);
		title.setFont(title.getFont().deriveFont(Font.BOLD, 16f));
		title.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(title);

		JLabel hint = new JLabel("<html>Search any slayer task monster, pick from nearby monsters, or both.<br>Settings are exposed at <b>/config</b>.</html>");
		hint.setForeground(Color.WHITE);
		hint.setBorder(new EmptyBorder(6, 0, 8, 0));
		hint.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(hint);

		statusLabel.setForeground(Color.WHITE);
		statusLabel.setBorder(new EmptyBorder(4, 0, 4, 0));
		statusLabel.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(statusLabel);

		JPanel enabledRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 0, 0));
		enabledRow.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledRow.setAlignmentX(Component.LEFT_ALIGNMENT);

		enabledBox.setSelected(config.controllerEnabled());
		enabledBox.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledBox.setForeground(Color.WHITE);
		enabledBox.addActionListener(e -> {
			// Guard against redundant writes -- setSelected() (used by syncFromConfig() to mirror
			// an external change) never fires this listener on its own, but this still avoids
			// persisting a no-op if that ever changes.
			boolean selected = enabledBox.isSelected();
			if (selected != config.controllerEnabled())
			{
				configManager.setConfiguration(ControllerConfig.GROUP, "controllerEnabled", selected);
			}
		});
		enabledRow.add(enabledBox);
		header.add(enabledRow);

		add(header, BorderLayout.NORTH);

		JPanel body = new JPanel();
		body.setLayout(new BoxLayout(body, BoxLayout.Y_AXIS));
		body.setBackground(ColorScheme.DARK_GRAY_COLOR);

		body.add(collapsibleSection("Add Slayer Task Monster", buildSearchSection(), true));
		body.add(collapsibleSection("Selected Targets", buildSelectedSection(), true));
		body.add(collapsibleSection("Monsters In Current Area", buildNearbySection(), true));

		JScrollPane bodyScroll = new JScrollPane(body);
		bodyScroll.setBorder(BorderFactory.createEmptyBorder());
		bodyScroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		// Scrollbar hidden entirely (mouse wheel still scrolls fine) -- a visible one was both an
		// eyesore and ate into the sidebar's already-narrow width, clipping text near the right edge.
		bodyScroll.setVerticalScrollBarPolicy(ScrollPaneConstants.VERTICAL_SCROLLBAR_NEVER);
		bodyScroll.getVerticalScrollBar().setUnitIncrement(16);
		add(bodyScroll, BorderLayout.CENTER);

		refreshSearchResults();
		refreshSelectedTargetsPanel();
		refreshNpcList();
	}

	private JPanel buildSearchSection()
	{
		JPanel section = new JPanel(new BorderLayout(0, 4));
		section.setBackground(ColorScheme.DARK_GRAY_COLOR);
		section.setBorder(new EmptyBorder(0, 0, 8, 0));
		section.setAlignmentX(Component.LEFT_ALIGNMENT);

		JPanel fieldWrap = new JPanel(new BorderLayout());
		fieldWrap.setBackground(ColorScheme.DARK_GRAY_COLOR);
		searchField.putClientProperty("JTextField.placeholderText", "Type to search, e.g. \"cra\"");
		searchField.getDocument().addDocumentListener((SimpleDocumentListener) e -> refreshSearchResults());
		fieldWrap.add(searchField, BorderLayout.CENTER);
		section.add(fieldWrap, BorderLayout.CENTER);

		searchResultsList.setBackground(ColorScheme.DARKER_GRAY_COLOR);
		searchResultsList.setForeground(Color.WHITE);
		searchResultsList.setSelectionBackground(ColorScheme.MEDIUM_GRAY_COLOR);
		searchResultsList.setCellRenderer(new ResultRowRenderer());
		searchResultsList.addMouseListener(new MouseAdapter()
		{
			@Override
			public void mouseClicked(MouseEvent e)
			{
				if (e.getClickCount() == 2)
				{
					ResultRow row = searchResultsList.getSelectedValue();
					if (row == null)
					{
						return;
					}
					if (row.isHeader())
					{
						// A task category header with no distinct monster names underneath just
						// falls back to the category label itself (e.g. "Ankou" -> "Ankou").
						List<String> monsters = SlayerMonsters.TASKS.getOrDefault(row.category, java.util.List.of(row.category));
						selectedTargets.addAll(monsters);
						persistSelections();
					}
					else
					{
						addSelected(row.monster);
					}
				}
			}
		});

		JScrollPane resultsScroll = new JScrollPane(searchResultsList);
		resultsScroll.setPreferredSize(new Dimension(0, 220));
		resultsScroll.setBorder(BorderFactory.createLineBorder(ColorScheme.MEDIUM_GRAY_COLOR));
		resultsScroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		resultsScroll.setVerticalScrollBarPolicy(ScrollPaneConstants.VERTICAL_SCROLLBAR_NEVER);
		section.add(resultsScroll, BorderLayout.SOUTH);

		JLabel resultsHint = new JLabel("<html>Double-click a task to add all its monsters,<br>or a monster to add just that one</html>");
		resultsHint.setForeground(ColorScheme.LIGHT_GRAY_COLOR);
		resultsHint.setFont(resultsHint.getFont().deriveFont(11f));

		JPanel wrap = new JPanel(new BorderLayout());
		wrap.setBackground(ColorScheme.DARK_GRAY_COLOR);
		wrap.add(section, BorderLayout.CENTER);
		wrap.add(resultsHint, BorderLayout.SOUTH);
		wrap.setAlignmentX(Component.LEFT_ALIGNMENT);
		wrap.setBorder(new EmptyBorder(0, 0, 8, 0));
		return wrap;
	}

	private JPanel buildSelectedSection()
	{
		JPanel section = new JPanel(new BorderLayout(0, 4));
		section.setBackground(ColorScheme.DARK_GRAY_COLOR);
		section.setBorder(new EmptyBorder(0, 0, 8, 0));
		section.setAlignmentX(Component.LEFT_ALIGNMENT);

		JPanel clearRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 4, 4));
		clearRow.setBackground(ColorScheme.DARK_GRAY_COLOR);
		clearRow.setAlignmentX(Component.LEFT_ALIGNMENT);

		JButton clearBtn = new JButton("Clear All");
		clearBtn.addActionListener(e -> {
			selectedTargets.clear();
			persistSelections();
		});
		clearRow.add(clearBtn);
		section.add(clearRow, BorderLayout.NORTH);

		selectedTargetsPanel.setLayout(new BoxLayout(selectedTargetsPanel, BoxLayout.Y_AXIS));
		selectedTargetsPanel.setBackground(ColorScheme.DARKER_GRAY_COLOR);

		JScrollPane selectedScroll = new JScrollPane(selectedTargetsPanel);
		selectedScroll.setPreferredSize(new Dimension(0, 160));
		selectedScroll.setBorder(BorderFactory.createLineBorder(ColorScheme.MEDIUM_GRAY_COLOR));
		selectedScroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		selectedScroll.setVerticalScrollBarPolicy(ScrollPaneConstants.VERTICAL_SCROLLBAR_NEVER);
		section.add(selectedScroll, BorderLayout.CENTER);

		JLabel hint = new JLabel("<html>Click × to remove. A location dropdown appears<br>for tasks with more than one known spot.</html>");
		hint.setForeground(ColorScheme.LIGHT_GRAY_COLOR);
		hint.setFont(hint.getFont().deriveFont(11f));

		JPanel wrap = new JPanel(new BorderLayout());
		wrap.setBackground(ColorScheme.DARK_GRAY_COLOR);
		wrap.add(section, BorderLayout.CENTER);
		wrap.add(hint, BorderLayout.SOUTH);
		wrap.setAlignmentX(Component.LEFT_ALIGNMENT);
		wrap.setBorder(new EmptyBorder(0, 0, 8, 0));
		return wrap;
	}

	private JPanel buildNearbySection()
	{
		JPanel section = new JPanel(new BorderLayout(0, 4));
		section.setBackground(ColorScheme.DARK_GRAY_COLOR);
		section.setAlignmentX(Component.LEFT_ALIGNMENT);

		JPanel buttons = new JPanel(new FlowLayout(FlowLayout.LEFT, 4, 4));
		buttons.setBackground(ColorScheme.DARK_GRAY_COLOR);
		buttons.setAlignmentX(Component.LEFT_ALIGNMENT);

		JButton refreshBtn = new JButton("Refresh");
		refreshBtn.addActionListener(e -> refreshNpcList());
		buttons.add(refreshBtn);

		JButton selectAllBtn = new JButton("All");
		selectAllBtn.addActionListener(e -> setAllNearbySelected(true));
		buttons.add(selectAllBtn);

		JButton clearBtn = new JButton("None");
		clearBtn.addActionListener(e -> setAllNearbySelected(false));
		buttons.add(clearBtn);

		section.add(buttons, BorderLayout.NORTH);

		npcListPanel.setLayout(new BoxLayout(npcListPanel, BoxLayout.Y_AXIS));
		npcListPanel.setBackground(ColorScheme.DARKER_GRAY_COLOR);

		JScrollPane scroll = new JScrollPane(npcListPanel);
		scroll.setPreferredSize(new Dimension(0, 200));
		scroll.setBorder(BorderFactory.createLineBorder(ColorScheme.MEDIUM_GRAY_COLOR));
		scroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		scroll.setVerticalScrollBarPolicy(ScrollPaneConstants.VERTICAL_SCROLLBAR_NEVER);
		section.add(scroll, BorderLayout.CENTER);

		errorPanel.setContent("Controller Settings", "Walk near monsters and click Refresh to populate this list.");
		section.add(errorPanel, BorderLayout.SOUTH);

		return section;
	}

	/** Wraps `content` behind a clickable header showing `title` and a collapse arrow -- click
	 * the header to show/hide the section, so a narrow sidebar with several stacked sections
	 * doesn't force constant scrolling once you only care about one or two of them. */
	private JPanel collapsibleSection(String title, JComponent content, boolean initiallyExpanded)
	{
		JPanel wrapper = new JPanel(new BorderLayout());
		wrapper.setBackground(ColorScheme.DARK_GRAY_COLOR);
		wrapper.setAlignmentX(Component.LEFT_ALIGNMENT);
		wrapper.setBorder(new EmptyBorder(0, 0, 8, 0));

		JLabel arrow = new JLabel(initiallyExpanded ? "▼" : "▶");
		arrow.setForeground(Color.WHITE);
		arrow.setBorder(new EmptyBorder(0, 0, 0, 4));

		JLabel titleLabel = sectionLabel(title);

		JPanel header = new JPanel(new FlowLayout(FlowLayout.LEFT, 0, 2));
		header.setBackground(ColorScheme.DARK_GRAY_COLOR);
		header.setCursor(Cursor.getPredefinedCursor(Cursor.HAND_CURSOR));
		header.add(arrow);
		header.add(titleLabel);

		content.setVisible(initiallyExpanded);

		header.addMouseListener(new MouseAdapter()
		{
			@Override
			public void mouseClicked(MouseEvent e)
			{
				boolean nowExpanded = !content.isVisible();
				content.setVisible(nowExpanded);
				arrow.setText(nowExpanded ? "▼" : "▶");
				wrapper.revalidate();
				wrapper.repaint();
			}
		});

		wrapper.add(header, BorderLayout.NORTH);
		wrapper.add(content, BorderLayout.CENTER);
		return wrapper;
	}

	private JLabel sectionLabel(String text)
	{
		JLabel label = new JLabel(text);
		label.setForeground(Color.WHITE);
		label.setFont(label.getFont().deriveFont(Font.BOLD, 12f));
		return label;
	}

	private void refreshSearchResults()
	{
		searchResultsModel.clear();
		for (SlayerMonsters.TaskMatch match : SlayerMonsters.search(searchField.getText()))
		{
			searchResultsModel.addElement(new ResultRow(match.category, null));
			for (String monster : match.monsters)
			{
				searchResultsModel.addElement(new ResultRow(match.category, monster));
			}
		}
	}

	/** One row in the search results list -- either a task category header (monster == null,
	 * rendered bold/unindented) or a specific monster belonging to that category (rendered
	 * indented underneath its header). */
	private static final class ResultRow
	{
		final String category;
		final String monster;

		ResultRow(String category, String monster)
		{
			this.category = category;
			this.monster = monster;
		}

		boolean isHeader()
		{
			return monster == null;
		}
	}

	private static final class ResultRowRenderer implements ListCellRenderer<ResultRow>
	{
		@Override
		public Component getListCellRendererComponent(JList<? extends ResultRow> list, ResultRow row, int index,
			boolean isSelected, boolean cellHasFocus)
		{
			JLabel label = new JLabel();
			label.setOpaque(true);
			label.setForeground(Color.WHITE);
			label.setBackground(isSelected ? ColorScheme.MEDIUM_GRAY_COLOR : ColorScheme.DARKER_GRAY_COLOR);

			if (row.isHeader())
			{
				label.setText(row.category);
				label.setFont(label.getFont().deriveFont(Font.BOLD));
				label.setBorder(new EmptyBorder(4, 4, 2, 4));
			}
			else
			{
				label.setText("    " + row.monster);
				label.setFont(label.getFont().deriveFont(Font.PLAIN));
				label.setBorder(new EmptyBorder(0, 4, 2, 4));
			}

			return label;
		}
	}

	private void addSelected(String name)
	{
		if (name == null)
		{
			return;
		}
		selectedTargets.add(name);
		persistSelections();
	}

	private void removeSelected(String name)
	{
		if (name == null)
		{
			return;
		}
		selectedTargets.remove(name);
		persistSelections();
	}

	void refreshNpcList()
	{
		npcListPanel.removeAll();
		npcCheckboxes.clear();

		@SuppressWarnings("unchecked")
		Map<Integer, String> types = (Map<Integer, String>) RuneLiteUtil.runOnClientThread(client, clientThread, () -> {
			Map<Integer, String> found = new TreeMap<>(Comparator.comparingInt(id -> id));
			for (NPC npc : client.getNpcs())
			{
				if (npc == null || npc.getName() == null || npc.getName().isEmpty())
				{
					continue;
				}
				found.putIfAbsent(npc.getId(), npc.getName());
			}
			return found;
		});

		if (types.isEmpty())
		{
			errorPanel.setContent("No NPCs Found", "No NPCs are loaded. Make sure you are logged in and near monsters, then click Refresh.");
			npcListPanel.revalidate();
			npcListPanel.repaint();
			return;
		}

		errorPanel.setVisible(false);

		for (Map.Entry<Integer, String> entry : types.entrySet())
		{
			String name = entry.getValue();
			int id = entry.getKey();

			JCheckBox box = new JCheckBox(name + "  (id " + id + ")");
			box.setSelected(selectedTargets.contains(name));
			box.setBackground(ColorScheme.DARKER_GRAY_COLOR);
			box.setForeground(Color.WHITE);
			box.setAlignmentX(Component.LEFT_ALIGNMENT);
			box.addActionListener(e -> {
				if (box.isSelected())
				{
					selectedTargets.add(name);
				}
				else
				{
					selectedTargets.remove(name);
				}
				persistSelections();
			});

			npcCheckboxes.put(name, box);
			npcListPanel.add(box);
		}

		npcListPanel.revalidate();
		npcListPanel.repaint();
		updateStatusLabel(types.size());
	}

	private void setAllNearbySelected(boolean selected)
	{
		for (Map.Entry<String, JCheckBox> entry : npcCheckboxes.entrySet())
		{
			entry.getValue().setSelected(selected);
			if (selected)
			{
				selectedTargets.add(entry.getKey());
			}
			else
			{
				selectedTargets.remove(entry.getKey());
			}
		}
		persistSelections();
	}

	private void persistSelections()
	{
		configManager.setConfiguration(
			ControllerConfig.GROUP,
			"targetNpcNames",
			String.join(", ", selectedTargets)
		);

		for (Map.Entry<String, JCheckBox> entry : npcCheckboxes.entrySet())
		{
			entry.getValue().setSelected(selectedTargets.contains(entry.getKey()));
		}

		refreshSelectedTargetsPanel();
		updateStatusLabel(npcCheckboxes.size());
	}

	private void refreshSelectedTargetsPanel()
	{
		selectedTargetsPanel.removeAll();

		List<String> sorted = new ArrayList<>(selectedTargets);
		sorted.sort(String.CASE_INSENSITIVE_ORDER);

		for (String name : sorted)
		{
			selectedTargetsPanel.add(buildSelectedTargetRow(name));
		}

		selectedTargetsPanel.revalidate();
		selectedTargetsPanel.repaint();
	}

	private JPanel buildSelectedTargetRow(String name)
	{
		JPanel row = new JPanel(new BorderLayout(4, 0));
		row.setBackground(ColorScheme.DARKER_GRAY_COLOR);
		row.setBorder(new EmptyBorder(2, 4, 2, 4));
		row.setAlignmentX(Component.LEFT_ALIGNMENT);
		row.setMaximumSize(new Dimension(Integer.MAX_VALUE, row.getPreferredSize().height + 20));

		JLabel nameLabel = new JLabel(name);
		nameLabel.setForeground(Color.WHITE);
		row.add(nameLabel, BorderLayout.CENTER);

		JPanel right = new JPanel(new FlowLayout(FlowLayout.RIGHT, 4, 0));
		right.setBackground(ColorScheme.DARKER_GRAY_COLOR);

		String category = categoryForMonster(name);
		List<SlayerLocations.Location> locations = category == null ? null : SlayerLocations.LOCATIONS.get(category);
		if (locations != null && locations.size() > 1)
		{
			JComboBox<String> locationCombo = new JComboBox<>();
			for (SlayerLocations.Location loc : locations)
			{
				locationCombo.addItem(loc.note);
			}
			int chosen = categoryLocationChoice.getOrDefault(category, 0);
			if (chosen < 0 || chosen >= locations.size())
			{
				chosen = 0;
			}
			locationCombo.setSelectedIndex(chosen);

			String finalCategory = category;
			locationCombo.addActionListener(e -> {
				categoryLocationChoice.put(finalCategory, locationCombo.getSelectedIndex());
				persistLocationChoices();
			});
			right.add(locationCombo);
		}

		JButton removeBtn = new JButton("×");
		removeBtn.setMargin(new Insets(0, 4, 0, 4));
		removeBtn.addActionListener(e -> removeSelected(name));
		right.add(removeBtn);

		row.add(right, BorderLayout.EAST);
		return row;
	}

	/** Which SlayerMonsters.TASKS category (if any) a selected monster name belongs to -- e.g.
	 * "Lizardman shaman" -> "Lizardmen". Returns null for names not found in that table (e.g.
	 * something picked from the nearby-monster checkboxes that isn't a known slayer task
	 * monster), which is fine -- those just don't get a location dropdown. */
	private String categoryForMonster(String monsterName)
	{
		String lower = monsterName.toLowerCase(Locale.ENGLISH);
		for (Map.Entry<String, List<String>> entry : SlayerMonsters.TASKS.entrySet())
		{
			for (String candidate : entry.getValue())
			{
				if (candidate.toLowerCase(Locale.ENGLISH).equals(lower))
				{
					return entry.getKey();
				}
			}
		}
		return null;
	}

	private void persistLocationChoices()
	{
		StringBuilder sb = new StringBuilder();
		for (Map.Entry<String, Integer> entry : categoryLocationChoice.entrySet())
		{
			if (sb.length() > 0)
			{
				sb.append(";");
			}
			sb.append(entry.getKey()).append("=").append(entry.getValue());
		}
		configManager.setConfiguration(ControllerConfig.GROUP, "taskLocationChoices", sb.toString());
	}

	/** Re-syncs every control here from the current config -- called by ControllerPlugin whenever
	 * ControllerConfig changes from anywhere, so this panel never shows a stale
	 * value for a setting it shares with the wrench-icon config screens (Controller Enabled,
	 * target NPCs, and task location choices are all editable from both places). Safe to call
	 * after a change originating from this panel too: JCheckBox#setSelected doesn't fire its
	 * ActionListener at all. */
	void syncFromConfig()
	{
		enabledBox.setSelected(config.controllerEnabled());

		selectedTargets.clear();
		selectedTargets.addAll(ConfigExporter.parseList(config.targetNpcNames()));

		categoryLocationChoice.clear();
		categoryLocationChoice.putAll(ConfigExporter.parseLocationChoices(config.taskLocationChoices()));

		for (Map.Entry<String, JCheckBox> entry : npcCheckboxes.entrySet())
		{
			entry.getValue().setSelected(selectedTargets.contains(entry.getKey()));
		}

		refreshSelectedTargetsPanel();
		updateStatusLabel(npcCheckboxes.size());
	}

	private void updateStatusLabel(int nearbyCount)
	{
		statusLabel.setText(nearbyCount + " monsters in area · " + selectedTargets.size() + " selected total");
	}

	@FunctionalInterface
	private interface SimpleDocumentListener extends DocumentListener
	{
		void onChange(DocumentEvent e);

		@Override
		default void insertUpdate(DocumentEvent e)
		{
			onChange(e);
		}

		@Override
		default void removeUpdate(DocumentEvent e)
		{
			onChange(e);
		}

		@Override
		default void changedUpdate(DocumentEvent e)
		{
			onChange(e);
		}
	}
}
