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

	private final DefaultListModel<String> selectedListModel = new DefaultListModel<>();
	private final JList<String> selectedList = new JList<>(selectedListModel);

	@Inject
	SettingsPanel(Client client, ClientThread clientThread, ConfigManager configManager, ControllerConfig config)
	{
		this.client = client;
		this.clientThread = clientThread;
		this.configManager = configManager;
		this.config = config;

		selectedTargets.addAll(ConfigExporter.parseList(config.targetNpcNames()));

		setLayout(new BorderLayout());
		setBackground(ColorScheme.DARK_GRAY_COLOR);
		setBorder(new EmptyBorder(10, 10, 10, 10));

		JPanel header = new JPanel();
		header.setLayout(new BoxLayout(header, BoxLayout.Y_AXIS));
		header.setBackground(ColorScheme.DARK_GRAY_COLOR);

		JLabel title = new JLabel("Controller Settings");
		title.setFont(title.getFont().deriveFont(Font.BOLD, 16f));
		title.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(title);

		JLabel hint = new JLabel("<html>Search any slayer task monster, pick from nearby monsters, or both.<br>Settings are exposed at <b>/config</b>.</html>");
		hint.setBorder(new EmptyBorder(6, 0, 8, 0));
		hint.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(hint);

		statusLabel.setBorder(new EmptyBorder(4, 0, 4, 0));
		statusLabel.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(statusLabel);

		JPanel enabledRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 0, 0));
		enabledRow.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledRow.setAlignmentX(Component.LEFT_ALIGNMENT);

		JCheckBox enabledBox = new JCheckBox("Controller Enabled");
		enabledBox.setSelected(config.controllerEnabled());
		enabledBox.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledBox.setForeground(Color.WHITE);
		enabledBox.addActionListener(e -> configManager.setConfiguration(
			ControllerConfig.GROUP,
			"controllerEnabled",
			enabledBox.isSelected()
		));
		enabledRow.add(enabledBox);
		header.add(enabledRow);

		JPanel profileRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 4, 0));
		profileRow.setBackground(ColorScheme.DARK_GRAY_COLOR);
		profileRow.setAlignmentX(Component.LEFT_ALIGNMENT);
		profileRow.setBorder(new EmptyBorder(4, 0, 0, 0));

		JLabel profileLabel = new JLabel("Profile:");
		profileLabel.setForeground(Color.WHITE);
		profileRow.add(profileLabel);

		JComboBox<Profile> profileCombo = new JComboBox<>(Profile.values());
		profileCombo.setSelectedItem(config.profile());
		profileCombo.addActionListener(e -> configManager.setConfiguration(
			ControllerConfig.GROUP,
			"profile",
			profileCombo.getSelectedItem()
		));
		profileRow.add(profileCombo);
		header.add(profileRow);

		add(header, BorderLayout.NORTH);

		JPanel body = new JPanel();
		body.setLayout(new BoxLayout(body, BoxLayout.Y_AXIS));
		body.setBackground(ColorScheme.DARK_GRAY_COLOR);

		body.add(buildSearchSection());
		body.add(buildSelectedSection());
		body.add(buildNearbySection());

		JScrollPane bodyScroll = new JScrollPane(body);
		bodyScroll.setBorder(BorderFactory.createEmptyBorder());
		bodyScroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		bodyScroll.getVerticalScrollBar().setUnitIncrement(16);
		add(bodyScroll, BorderLayout.CENTER);

		refreshSearchResults();
		refreshSelectedListModel();
		refreshNpcList();
	}

	private JPanel buildSearchSection()
	{
		JPanel section = new JPanel(new BorderLayout(0, 4));
		section.setBackground(ColorScheme.DARK_GRAY_COLOR);
		section.setBorder(new EmptyBorder(0, 0, 8, 0));
		section.setAlignmentX(Component.LEFT_ALIGNMENT);

		JLabel label = sectionLabel("Add Slayer Task Monster");
		section.add(label, BorderLayout.NORTH);

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

		JPanel labelRow = new JPanel();
		labelRow.setLayout(new BoxLayout(labelRow, BoxLayout.Y_AXIS));
		labelRow.setBackground(ColorScheme.DARK_GRAY_COLOR);

		JLabel selectedLabel = sectionLabel("Selected Targets");
		selectedLabel.setAlignmentX(Component.LEFT_ALIGNMENT);
		labelRow.add(selectedLabel);

		JPanel clearRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 4, 4));
		clearRow.setBackground(ColorScheme.DARK_GRAY_COLOR);
		clearRow.setAlignmentX(Component.LEFT_ALIGNMENT);

		JButton clearBtn = new JButton("Clear All");
		clearBtn.addActionListener(e -> {
			selectedTargets.clear();
			persistSelections();
		});
		clearRow.add(clearBtn);
		labelRow.add(clearRow);
		section.add(labelRow, BorderLayout.NORTH);

		selectedList.setBackground(ColorScheme.DARKER_GRAY_COLOR);
		selectedList.setForeground(Color.WHITE);
		selectedList.setSelectionBackground(ColorScheme.MEDIUM_GRAY_COLOR);
		selectedList.addMouseListener(new MouseAdapter()
		{
			@Override
			public void mouseClicked(MouseEvent e)
			{
				if (e.getClickCount() == 2)
				{
					removeSelected(selectedList.getSelectedValue());
				}
			}
		});

		JScrollPane selectedScroll = new JScrollPane(selectedList);
		selectedScroll.setPreferredSize(new Dimension(0, 120));
		selectedScroll.setBorder(BorderFactory.createLineBorder(ColorScheme.MEDIUM_GRAY_COLOR));
		selectedScroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		section.add(selectedScroll, BorderLayout.CENTER);

		JLabel hint = new JLabel("Double-click to remove");
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

		JPanel labelRow = new JPanel();
		labelRow.setLayout(new BoxLayout(labelRow, BoxLayout.Y_AXIS));
		labelRow.setBackground(ColorScheme.DARK_GRAY_COLOR);

		JLabel nearbyLabel = sectionLabel("Monsters In Current Area");
		nearbyLabel.setAlignmentX(Component.LEFT_ALIGNMENT);
		labelRow.add(nearbyLabel);

		// A single BorderLayout row (label WEST, buttons EAST) doesn't fit this sidebar's width
		// -- WEST/EAST don't wrap, so the buttons (including Refresh) were being clipped off
		// entirely. Stacking the buttons on their own line below the label instead guarantees
		// they always have room.
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

		labelRow.add(buttons);
		section.add(labelRow, BorderLayout.NORTH);

		npcListPanel.setLayout(new BoxLayout(npcListPanel, BoxLayout.Y_AXIS));
		npcListPanel.setBackground(ColorScheme.DARKER_GRAY_COLOR);

		JScrollPane scroll = new JScrollPane(npcListPanel);
		scroll.setPreferredSize(new Dimension(0, 200));
		scroll.setBorder(BorderFactory.createLineBorder(ColorScheme.MEDIUM_GRAY_COLOR));
		scroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		scroll.getVerticalScrollBar().setPreferredSize(new Dimension(8, 0));
		section.add(scroll, BorderLayout.CENTER);

		errorPanel.setContent("Controller Settings", "Walk near monsters and click Refresh to populate this list.");
		section.add(errorPanel, BorderLayout.SOUTH);

		return section;
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

		refreshSelectedListModel();
		updateStatusLabel(npcCheckboxes.size());
	}

	private void refreshSelectedListModel()
	{
		selectedListModel.clear();
		List<String> sorted = new ArrayList<>(selectedTargets);
		sorted.sort(String.CASE_INSENSITIVE_ORDER);
		for (String name : sorted)
		{
			selectedListModel.addElement(name);
		}
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
