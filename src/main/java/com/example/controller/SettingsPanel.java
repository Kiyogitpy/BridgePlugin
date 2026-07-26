package com.example.controller;

import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.ui.ColorScheme;
import net.runelite.client.ui.PluginPanel;
import net.runelite.client.ui.components.PluginErrorPanel;

import javax.inject.Inject;
import javax.swing.*;
import javax.swing.border.EmptyBorder;
import java.awt.*;
import java.util.*;
import java.util.List;

public class SettingsPanel extends PluginPanel
{
	private final Client client;
	private final ConfigManager configManager;
	private final ControllerConfig config;

	private final JPanel npcListPanel = new JPanel();
	private final PluginErrorPanel errorPanel = new PluginErrorPanel();
	private final JLabel statusLabel = new JLabel();

	private final Map<String, JCheckBox> npcCheckboxes = new LinkedHashMap<>();

	@Inject
	SettingsPanel(Client client, ConfigManager configManager, ControllerConfig config)
	{
		this.client = client;
		this.configManager = configManager;
		this.config = config;

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

		JLabel hint = new JLabel("<html>Select monsters visible in your current area.<br>Settings are exposed at <b>/config</b>.</html>");
		hint.setBorder(new EmptyBorder(6, 0, 8, 0));
		hint.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(hint);

		JPanel buttons = new JPanel(new FlowLayout(FlowLayout.LEFT, 4, 0));
		buttons.setBackground(ColorScheme.DARK_GRAY_COLOR);
		buttons.setAlignmentX(Component.LEFT_ALIGNMENT);

		JButton refreshBtn = new JButton("Refresh NPCs");
		refreshBtn.addActionListener(e -> refreshNpcList());
		buttons.add(refreshBtn);

		JButton selectAllBtn = new JButton("Select All");
		selectAllBtn.addActionListener(e -> setAllSelected(true));
		buttons.add(selectAllBtn);

		JButton clearBtn = new JButton("Clear");
		clearBtn.addActionListener(e -> setAllSelected(false));
		buttons.add(clearBtn);

		header.add(buttons);

		statusLabel.setBorder(new EmptyBorder(8, 0, 4, 0));
		statusLabel.setAlignmentX(Component.LEFT_ALIGNMENT);
		header.add(statusLabel);

		JPanel enabledRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 0, 0));
		enabledRow.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledRow.setAlignmentX(Component.LEFT_ALIGNMENT);

		JCheckBox enabledBox = new JCheckBox("Controller Enabled");
		enabledBox.setSelected(config.botEnabled());
		enabledBox.setBackground(ColorScheme.DARK_GRAY_COLOR);
		enabledBox.setForeground(Color.WHITE);
		enabledBox.addActionListener(e -> {
			configManager.setConfiguration(
				ControllerConfig.GROUP,
				"botEnabled",
				enabledBox.isSelected()
			);
		});
		enabledRow.add(enabledBox);
		header.add(enabledRow);

		add(header, BorderLayout.NORTH);

		npcListPanel.setLayout(new BoxLayout(npcListPanel, BoxLayout.Y_AXIS));
		npcListPanel.setBackground(ColorScheme.DARKER_GRAY_COLOR);

		JScrollPane scroll = new JScrollPane(npcListPanel);
		scroll.setBorder(BorderFactory.createLineBorder(ColorScheme.MEDIUM_GRAY_COLOR));
		scroll.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
		scroll.getVerticalScrollBar().setPreferredSize(new Dimension(8, 0));
		add(scroll, BorderLayout.CENTER);

		errorPanel.setContent("Controller Settings", "Walk near monsters and click Refresh NPCs to populate the target list.");
		add(errorPanel, BorderLayout.SOUTH);

		refreshNpcList();
	}

	void refreshNpcList()
	{
		npcListPanel.removeAll();
		npcCheckboxes.clear();

		Set<String> selected = new LinkedHashSet<>(ConfigExporter.parseList(config.targetNpcNames()));
		Map<Integer, String> types = new TreeMap<>(Comparator.comparingInt(id -> id));

		for (NPC npc : client.getNpcs())
		{
			if (npc == null || npc.getName() == null || npc.getName().isEmpty())
			{
				continue;
			}
			types.putIfAbsent(npc.getId(), npc.getName());
		}

		if (types.isEmpty())
		{
			errorPanel.setContent("No NPCs Found", "No NPCs are loaded. Make sure you are logged in and near monsters, then click Refresh.");
			statusLabel.setText("0 monsters in area");
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
			box.setSelected(selected.contains(name));
			box.setBackground(ColorScheme.DARKER_GRAY_COLOR);
			box.setForeground(Color.WHITE);
			box.setAlignmentX(Component.LEFT_ALIGNMENT);
			box.addActionListener(e -> saveSelections());

			npcCheckboxes.put(name, box);
			npcListPanel.add(box);
		}

		int selectedCount = (int) npcCheckboxes.values().stream().filter(AbstractButton::isSelected).count();
		statusLabel.setText(types.size() + " monsters in area · " + selectedCount + " selected");
		npcListPanel.revalidate();
		npcListPanel.repaint();
	}

	private void setAllSelected(boolean selected)
	{
		for (JCheckBox box : npcCheckboxes.values())
		{
			box.setSelected(selected);
		}
		saveSelections();
	}

	private void saveSelections()
	{
		List<String> names = new ArrayList<>();
		for (Map.Entry<String, JCheckBox> entry : npcCheckboxes.entrySet())
		{
			if (entry.getValue().isSelected())
			{
				names.add(entry.getKey());
			}
		}

		configManager.setConfiguration(
			ControllerConfig.GROUP,
			"targetNpcNames",
			String.join(", ", names)
		);

		statusLabel.setText(npcCheckboxes.size() + " monsters in area · " + names.size() + " selected");
	}
}
