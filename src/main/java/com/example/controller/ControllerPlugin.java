package com.example.controller;

import com.example.bridge.BridgeHttpServer;
import com.example.providers.*;
import com.example.tracker.CurrentTargetTrackerFactory;
import com.example.tracker.NpcTrackerRegistry;
import com.example.util.UdpEventSender;
import com.google.inject.Provides;
import net.runelite.api.*;
import net.runelite.api.coords.WorldPoint;
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
import java.util.EnumMap;
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

	// Shared per-tick budget for OBJECT_*/GROUND_ITEM_* events (see sendObjectEvent's comment for
	// why this exists and why 40 is deliberately a cap, not a warning threshold).
	private static final int SPAWN_EVENT_CAP_PER_TICK = 40;

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
	// Tracks raw XP per skill (not level) so XP_GAINED can report every gain, including ones
	// too small to bump a level -- CLAUDE.md's "sub-tick data" note identifies StatChanged's XP
	// as the definitive, non-tick-capped proof a skilling action's server-side check succeeded.
	private final Map<Skill, Integer> lastXp = new EnumMap<>(Skill.class);
	private int spawnEventBudgetTick = -1;
	private int spawnEventBudgetCount = 0;

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
				// "from" is the animation being left, not the new one -- the whole point is
				// telling a mid-sequence pause (e.g. 5212 -> -1, trap check ending, re-lay still
				// to come) apart from a finished action (5208 -> -1), which "animation" alone
				// can't do. Read lastAnimation here, before it's overwritten below.
				payload.put("from", lastAnimation);
				payload.put("tick", tickClock.get());
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
	public void onGameObjectSpawned(GameObjectSpawned event)
	{
		sendObjectEvent("OBJECT_SPAWNED", event.getGameObject());
	}

	@Subscribe
	public void onGameObjectDespawned(GameObjectDespawned event)
	{
		sendObjectEvent("OBJECT_DESPAWNED", event.getGameObject());
	}

	@Subscribe
	public void onItemSpawned(ItemSpawned event)
	{
		sendGroundItemEvent("GROUND_ITEM_SPAWNED", event.getTile(), event.getItem());
	}

	@Subscribe
	public void onItemDespawned(ItemDespawned event)
	{
		sendGroundItemEvent("GROUND_ITEM_DESPAWNED", event.getTile(), event.getItem());
	}

	private void sendObjectEvent(String type, GameObject obj)
	{
		// Cheap first filter -- excludes the logged-out case -- but NOT sufficient alone to guard
		// against a scene-load flood: LoginProvider.java:28-31 records, live-confirmed, that
		// GameState is already LOGGED_IN at the "CLICK HERE TO PLAY" screen, before the world has
		// loaded, and an ordinary walk-triggered scene swap never transitions GameState at all --
		// it stays LOGGED_IN for the whole session while routines here travel constantly. See the
		// shared per-tick budget below for what actually bounds a scene-load burst.
		if (client.getGameState() != GameState.LOGGED_IN)
		{
			return;
		}
		if (obj == null)
		{
			return;
		}
		if (!spawnEventBudgetAvailable())
		{
			return;
		}
		WorldPoint wp = WorldPoint.fromLocalInstance(client, obj.getLocalLocation());
		if (wp == null)
		{
			return;
		}
		// shutDown() nulls udpSender and RuneLite only unregisters this plugin's subscribers
		// AFTER shutDown() returns (M5, whole-branch review, 2026-08-21) -- onConfigChanged
		// already guards the same race; these two spawn helpers didn't, and an
		// ObjectSpawned/ItemSpawned event landing in that gap NPE'd.
		if (udpSender == null)
		{
			return;
		}
		Map<String, Object> payload = new HashMap<>();
		payload.put("id", obj.getId());
		payload.put("x", wp.getX());
		payload.put("y", wp.getY());
		payload.put("plane", wp.getPlane());
		payload.put("tick", tickClock.get());
		udpSender.send(type, payload);
	}

	private void sendGroundItemEvent(String type, Tile tile, TileItem item)
	{
		// Same cheap-but-insufficient GameState filter as sendObjectEvent -- see that method's
		// comment for why a scene-load burst needs the per-tick budget below, not just this check.
		if (client.getGameState() != GameState.LOGGED_IN)
		{
			return;
		}
		if (tile == null || item == null)
		{
			return;
		}
		if (!spawnEventBudgetAvailable())
		{
			return;
		}
		WorldPoint wp = WorldPoint.fromLocalInstance(client, tile.getLocalLocation());
		if (wp == null)
		{
			return;
		}
		// Same shutDown()-vs-unregister race as sendObjectEvent above -- see that method's
		// comment (M5, whole-branch review, 2026-08-21).
		if (udpSender == null)
		{
			return;
		}
		Map<String, Object> payload = new HashMap<>();
		payload.put("id", item.getId());
		payload.put("qty", item.getQuantity());
		payload.put("x", wp.getX());
		payload.put("y", wp.getY());
		payload.put("plane", wp.getPlane());
		payload.put("tick", tickClock.get());
		udpSender.send(type, payload);
	}

	/**
	 * Shared per-tick emission budget for OBJECT_-prefixed and GROUND_ITEM_-prefixed events,
	 * checked before either helper above does its own UdpEventSender.send(). UdpEventSender.send()
	 * makes two synchronous, blocking DatagramSocket.send() calls (UdpEventSender.java:47-56) fired
	 * straight from this @Subscribe handler on the client thread -- an ordinary scene swap from
	 * walking (GameObjectSpawned/Despawned fire in bulk with no GameState transition to filter on,
	 * see the comment above) would otherwise turn into hundreds of blocking socket writes on
	 * RuneLite's render thread. One shared counter for both event categories, because the harm is
	 * total blocking writes per tick, not per category. Dropping events past the cap is intended
	 * and correct, not a bug to "fix" later: the Python-side mirror this feeds is an accelerator
	 * with an HTTP (/objects, /ground-items) fallback, so a dropped spawn costs latency, never
	 * correctness.
	 */
	private boolean spawnEventBudgetAvailable()
	{
		int tick = tickClock.get();
		if (tick != spawnEventBudgetTick)
		{
			spawnEventBudgetTick = tick;
			spawnEventBudgetCount = 0;
		}
		if (spawnEventBudgetCount >= SPAWN_EVENT_CAP_PER_TICK)
		{
			return false;
		}
		spawnEventBudgetCount++;
		return true;
	}

	// Handles both LEVEL_UP and XP_GAINED off the single StatChanged event -- NOT split into two
	// @Subscribe methods. RuneLite's EventBus.register() hard-requires a subscriber's method name
	// to equal "on" + the parameter type's simple name (verified against client-1.12.36.jar, the
	// version `latest.release` resolves to); for a StatChanged parameter that name is literally
	// "onStatChanged". A differently-named second subscriber (e.g. onXpGained) throws
	// IllegalArgumentException inside register()'s method loop, before subscribers = builder.build()
	// runs -- which kills registration for every subscriber in this class, not just the new one, so
	// the whole plugin comes up dead. Two methods both named onStatChanged are impossible in Java
	// anyway, so both jobs have to live in the one handler.
	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		Skill skill = event.getSkill();

		int realLevel = client.getRealSkillLevel(skill);
		Integer previousLevel = lastRealLevels.put(skill, realLevel);
		if (previousLevel != null && realLevel > previousLevel)
		{
			Map<String, Object> payload = new HashMap<>();
			payload.put("skill", skill.getName());
			payload.put("level", realLevel);
			payload.put("previousLevel", previousLevel);
			payload.put("xp", client.getSkillExperience(skill));
			udpSender.send("LEVEL_UP", payload);
		}

		int xp = event.getXp();
		Integer previousXp = lastXp.put(skill, xp);
		if (previousXp == null || xp <= previousXp)
		{
			// previousXp == null is the first StatChanged for this skill since login/plugin
			// start, which carries the account's entire XP total, not a gain -- reporting it
			// as a "delta" would mean e.g. an account with ~1.5M Hunter XP emitting a single
			// 1,500,000 XP_GAINED the moment tracking begins.
			return;
		}
		Map<String, Object> payload = new HashMap<>();
		payload.put("skill", skill.getName());
		payload.put("xp", xp);
		payload.put("delta", xp - previousXp);
		payload.put("tick", tickClock.get());
		udpSender.send("XP_GAINED", payload);
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
