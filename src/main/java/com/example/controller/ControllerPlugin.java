package com.example.controller;

import com.example.bridge.BridgeHttpServer;
import com.example.providers.*;
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

import javax.inject.Inject;
import java.util.HashMap;
import java.util.Map;

@PluginDescriptor(
	name = "* HttpBridge",
	description = "Master enable switch, plus the shared HTTP/UDP bridge every other Controller plugin (Slayer, Mining, Gemstone Crab, Tormented Demon) talks to.",
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

	private BridgeHttpServer server;
	private UdpEventSender udpSender;

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

		server = new BridgeHttpServer();
		server.start(HTTP_PORT);

		new GameStateProvider(client, clientThread, server).register();
		new PlayerProvider(client, clientThread, server).register();
		new PvpProvider(client, clientThread, server).register();
		new CombatProvider(client, clientThread, server, pluginManager).register();
		new PrayerProvider(client, clientThread, server).register();
		new SkillProvider(client, clientThread, server).register();
		new CameraProvider(client, clientThread, server).register();
		new EquipmentProvider(client, clientThread, server).register();
		new InventoryProvider(client, clientThread, server).register();
		new BankProvider(client, clientThread, server).register();
		new NPCProvider(client, clientThread, server).register();
		new ObjectProvider(client, clientThread, server).register();
		new GroundItemProvider(client, clientThread, server, itemManager).register();
		new GraphicsObjectProvider(client, clientThread, server).register();
		new TileProvider(client, clientThread, server).register();
		new ConfigProvider(client, clientThread, server, config).register();
		new ConsumablesProvider(client, clientThread, server, config).register();
		new LoginProvider(client, clientThread, server).register();

		try
		{
			new ShortestPathProvider(client, clientThread, server, pluginManager, eventBus).register();
		}
		catch (Throwable t)
		{
			System.out.println("Shortest Path integration unavailable: " + t);
		}
	}

	@Override
	protected void shutDown()
	{
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

	/**
	 * Lets sibling plugins (declared via @PluginDependency(ControllerPlugin.class), e.g.
	 * MiningPlugin/GemstoneCrabPlugin/TormentedDemonPlugin) register their own config
	 * endpoints on this same bridge instead of each standing up a separate HTTP server.
	 * @PluginDependency guarantees this plugin's startUp() (which creates the server) runs
	 * before any dependent plugin's startUp() runs, so this is never null by the time a
	 * dependent needs it.
	 */
	public BridgeHttpServer getServer()
	{
		return server;
	}

	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		// SlayerConfig isn't @Inject-ed here -- its @Provides binding lives on SlayerPlugin
		// (needed there for RuneLite to associate it with that plugin's own config screen), and
		// a dependent plugin's binding isn't visible back to the plugin it depends on. Fetching
		// it straight from ConfigManager sidesteps needing a Guice binding here at all.
		MenuPriority.apply(client, config, configManager.getConfig(SlayerConfig.class), event);
	}

	@Subscribe
	public void onConfigChanged(ConfigChanged event)
	{
		if (!ControllerConfig.GROUP.equals(event.getGroup()))
		{
			return;
		}

		if (udpSender != null)
		{
			udpSender.send("CONFIG_CHANGED");
		}
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		udpSender.send("TICK");

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
