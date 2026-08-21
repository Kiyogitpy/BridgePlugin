package com.example.controller;

import com.example.bridge.BridgeHttpServer;
import com.example.providers.*;
import com.example.tracker.CurrentTargetTrackerFactory;
import com.example.tracker.NpcTrackerRegistry;
import com.example.util.UdpEventSender;
import com.google.inject.Provides;
import net.runelite.api.*;
import net.runelite.api.events.*;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.events.ConfigChanged;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.game.ItemManager;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.plugins.PluginManager;
import net.runelite.client.ui.ClientToolbar;
import net.runelite.client.ui.DrawManager;
import net.runelite.client.ui.NavigationButton;
import net.runelite.client.ui.overlay.OverlayManager;
import net.runelite.client.util.ImageUtil;

import javax.inject.Inject;
import java.awt.image.BufferedImage;
import java.util.HashMap;
import java.util.Map;

@PluginDescriptor(
	name = "* HttpBridge",
	description = "Master enable switch, shared HTTP/UDP bridge, and every routine's settings (Slayer, Mining, Gemstone Crab, Tormented Demon, Firemaking, Blast Furnace) all in one place.",
	tags = {"controller", "config", "bridge", "http"}
)
public class ControllerPlugin extends Plugin
{
	private static final int HTTP_PORT = 8080;

	@Inject
	private Client client;

	@Inject
	private ClientThread clientThread;

	@Inject
	private ControllerConfig config;

	@Inject
	private ConfigManager configManager;

	@Inject
	private ItemManager itemManager;

	@Inject
	private PluginManager pluginManager;

	@Inject
	private EventBus eventBus;

	@Inject
	private SettingsPanel panel;

	@Inject
	private ClientToolbar clientToolbar;

	@Inject
	private OverlayManager overlayManager;

	@Inject
	private SafespotOverlay safespotOverlay;

	@Inject
	private DrawManager drawManager;

	private NavigationButton navButton;

	private BridgeHttpServer server;
	private UdpEventSender udpSender;
	private NpcTrackerRegistry npcTrackerRegistry;
	private TickClock tickClock;

	private int lastHp = -1;
	private int lastPrayer = -1;
	private int lastInventoryHash = 0;
	private int lastAnimation = -1;
	private String lastTargetKey = "";
	private final Map<Skill, Integer> lastRealLevels = new HashMap<>();

	@Override
	protected void startUp()
	{
		udpSender = new UdpEventSender();
		udpSender.start();

		npcTrackerRegistry = new NpcTrackerRegistry(client, eventBus, udpSender);
		npcTrackerRegistry.addFactory(new CurrentTargetTrackerFactory(client));
		npcTrackerRegistry.register();

		server = new BridgeHttpServer();
		server.start(HTTP_PORT);

		tickClock = new TickClock();

		new GameStateProvider(client, clientThread, server).register();
		new PlayerProvider(client, clientThread, server).register();
		new PvpProvider(client, clientThread, server).register();
		new CombatProvider(client, clientThread, server, pluginManager).register();
		new PrayerProvider(client, clientThread, server).register();
		new SkillProvider(client, clientThread, server).register();
		new CameraProvider(client, clientThread, server).register();
		new EquipmentProvider(client, clientThread, server).register();
		new InventoryProvider(client, clientThread, server).register();
		new BankProvider(client, clientThread, server, tickClock).register();
		new SailingProvider(client, clientThread, server, eventBus).register();
		new NPCProvider(client, clientThread, server, tickClock).register();
		new ObjectProvider(client, clientThread, server, tickClock).register();
		new GroundItemProvider(client, clientThread, server, itemManager, tickClock).register();
		new GraphicsObjectProvider(client, clientThread, server, tickClock).register();
		new TileProvider(client, clientThread, server, tickClock).register();
		new ConfigProvider(client, clientThread, server, config).register();
		new ConsumablesProvider(client, clientThread, server, config).register();
		new LoginProvider(client, clientThread, server).register();
		new ScreenshotProvider(client, clientThread, server, drawManager).register();
		new WidgetDumpProvider(client, clientThread, server).register();
		new SlayerConfigProvider(client, clientThread, server, config).register();
		new MenuStateProvider(client, clientThread, server).register();
		new SpellbookProvider(client, clientThread, server).register();
		new MixologyProvider(client, clientThread, server).register();
		new PyramidPlunderProvider(client, clientThread, server).register();
		new GolemProvider(client, clientThread, server).register();
		new LastActionProvider(client, clientThread, server, eventBus).register();
		new LastChatMessageProvider(client, clientThread, server, eventBus).register();

		server.register("/mining-config", () -> ConfigExporter.exportMining(config));
		server.register("/gemstone-crab-config", () -> ConfigExporter.exportGemstoneCrab(config));
		server.register("/tormented-demon-config", () -> ConfigExporter.exportTormentedDemon(config));
		server.register("/firemaking-config", () -> ConfigExporter.exportFiremaking(config));
		server.register("/blast-furnace-config", () -> ConfigExporter.exportBlastFurnace(config));
		server.register("/golem-crafting-config", () -> ConfigExporter.exportGolemCrafting(config));
		server.register("/mastering-mixology-config", () -> ConfigExporter.exportMasteringMixology(config));

		try
		{
			new ShortestPathProvider(client, clientThread, server, pluginManager, eventBus).register();
		}
		catch (Throwable t)
		{
			System.out.println("Shortest Path integration unavailable: " + t);
		}

		BufferedImage icon;
		try
		{
			icon = ImageUtil.loadImageResource(getClass(), "/controller_icon.png");
		}
		catch (IllegalArgumentException e)
		{
			icon = createFallbackIcon();
		}

		navButton = NavigationButton.builder()
			.tooltip("Controller Settings")
			.icon(icon)
			.priority(5)
			.panel(panel)
			.build();

		clientToolbar.addNavigation(navButton);
		overlayManager.add(safespotOverlay);
	}

	@Override
	protected void shutDown()
	{
		clientToolbar.removeNavigation(navButton);
		overlayManager.remove(safespotOverlay);

		if (server != null)
		{
			server.stop();
			server = null;
		}

		if (udpSender != null)
		{
			udpSender.stop();
			udpSender = null;
		}
	}

	@Provides
	ControllerConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(ControllerConfig.class);
	}

	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		MenuPriority.apply(client, config, event, itemManager);
	}

	@Subscribe
	public void onMenuEntryAdded(MenuEntryAdded event)
	{
		SafespotMarker.apply(client, configManager, event);
	}

	@Subscribe
	public void onConfigChanged(ConfigChanged event)
	{
		if (!ControllerConfig.GROUP.equals(event.getGroup()))
		{
			return;
		}

		// Keeps the sidebar panel's Controller Enabled/target selections live with whatever
		// just changed -- including edits made from the wrench-icon config screen, which this
		// panel doesn't otherwise hear about.
		panel.syncFromConfig();

		if (udpSender != null)
		{
			udpSender.send("CONFIG_CHANGED");
		}
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		// TickClock isn't a self-registered subscriber (RuneLite doesn't guarantee subscriber
		// ordering, so a second GameTick handler here could race this one) -- bump it explicitly
		// so the TICK payload always reflects the tick this handler is currently on.
		tickClock.bump();
		Map<String, Object> tickPayload = new HashMap<>();
		tickPayload.put("tick", tickClock.get());
		tickPayload.put("atMillis", tickClock.lastTickMillis());
		udpSender.send("TICK", tickPayload);

		int hp = client.getBoostedSkillLevel(Skill.HITPOINTS);
		if (lastHp >= 0 && hp != lastHp)
		{
			Map<String, Object> payload = new HashMap<>();
			payload.put("hp", hp);
			payload.put("maxHp", client.getRealSkillLevel(Skill.HITPOINTS));
			udpSender.send("HP_CHANGED", payload);
		}
		lastHp = hp;

		int prayer = client.getBoostedSkillLevel(Skill.PRAYER);
		if (lastPrayer >= 0 && prayer != lastPrayer)
		{
			Map<String, Object> payload = new HashMap<>();
			payload.put("prayer", prayer);
			payload.put("maxPrayer", client.getRealSkillLevel(Skill.PRAYER));
			udpSender.send("PRAYER_CHANGED", payload);
		}
		lastPrayer = prayer;

		Player local = client.getLocalPlayer();
		if (local != null)
		{
			int anim = local.getAnimation();
			if (lastAnimation >= 0 && anim != lastAnimation)
			{
				Map<String, Object> payload = new HashMap<>();
				payload.put("animation", anim);
				udpSender.send("ANIMATION_CHANGED", payload);
			}
			lastAnimation = anim;

			Actor target = local.getInteracting();
			String targetKey = target == null ? "" : targetKey(target);
			if (!targetKey.equals(lastTargetKey))
			{
				Map<String, Object> payload = new HashMap<>();
				payload.put("target", targetKey.isEmpty() ? null : targetKey);
				if (target instanceof NPC)
				{
					payload.put("targetId", ((NPC) target).getId());
					payload.put("targetName", target.getName());
				}
				udpSender.send("TARGET_CHANGED", payload);
				lastTargetKey = targetKey;
			}
		}
	}

	@Subscribe
	public void onActorDeath(ActorDeath event)
	{
		Actor actor = event.getActor();
		if (actor instanceof NPC)
		{
			NPC npc = (NPC) actor;
			Map<String, Object> payload = new HashMap<>();
			payload.put("id", npc.getId());
			payload.put("index", npc.getIndex());
			payload.put("name", npc.getName());
			udpSender.send("NPC_DIED", payload);
		}
		else if (actor instanceof Player && actor == client.getLocalPlayer())
		{
			udpSender.send("PLAYER_DIED");
		}
	}

	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		Skill skill = event.getSkill();
		int realLevel = client.getRealSkillLevel(skill);
		Integer previous = lastRealLevels.put(skill, realLevel);

		if (previous == null || realLevel <= previous)
		{
			return;
		}

		Map<String, Object> payload = new HashMap<>();
		payload.put("skill", skill.getName());
		payload.put("level", realLevel);
		payload.put("previousLevel", previous);
		payload.put("xp", client.getSkillExperience(skill));
		udpSender.send("LEVEL_UP", payload);
	}

	@Subscribe
	public void onItemContainerChanged(ItemContainerChanged event)
	{
		if (event.getContainerId() != InventoryID.INVENTORY.getId())
		{
			return;
		}

		ItemContainer container = event.getItemContainer();
		int hash = inventoryHash(container);
		if (hash != lastInventoryHash)
		{
			lastInventoryHash = hash;
			udpSender.send("INVENTORY_CHANGED");
		}
	}

	@Subscribe
	public void onChatMessage(ChatMessage event)
	{
		if (event.getType() != ChatMessageType.GAMEMESSAGE
			&& event.getType() != ChatMessageType.SPAM
			&& event.getType() != ChatMessageType.DIALOG)
		{
			return;
		}

		Map<String, Object> payload = new HashMap<>();
		payload.put("type", event.getType().name());
		payload.put("message", event.getMessage());
		payload.put("sender", event.getName());
		udpSender.send("CHAT_MESSAGE", payload);
	}

	private static BufferedImage createFallbackIcon()
	{
		BufferedImage img = new BufferedImage(16, 16, BufferedImage.TYPE_INT_ARGB);
		java.awt.Graphics2D g = img.createGraphics();
		g.setColor(new java.awt.Color(0x4CAF50));
		g.fillOval(1, 1, 14, 14);
		g.setColor(java.awt.Color.WHITE);
		g.fillRect(5, 4, 2, 8);
		g.fillRect(9, 6, 2, 6);
		g.dispose();
		return img;
	}

	private String targetKey(Actor actor)
	{
		if (actor instanceof NPC)
		{
			return "npc:" + ((NPC) actor).getId();
		}
		return "actor:" + actor.getName();
	}

	private int inventoryHash(ItemContainer container)
	{
		if (container == null)
		{
			return 0;
		}

		int hash = 1;
		Item[] items = container.getItems();
		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];
			if (item != null)
			{
				hash = 31 * hash + i;
				hash = 31 * hash + item.getId();
				hash = 31 * hash + item.getQuantity();
			}
		}
		return hash;
	}
}
