package com.example.tracker;

import com.example.util.UdpEventSender;
import net.runelite.api.Client;
import net.runelite.api.Hitsplat;
import net.runelite.api.NPC;
import net.runelite.api.Player;
import net.runelite.api.events.ClientTick;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GameTick;
import net.runelite.api.events.HitsplatApplied;
import net.runelite.api.events.NpcChanged;
import net.runelite.api.events.NpcDespawned;
import net.runelite.api.events.NpcSpawned;
import net.runelite.api.GameState;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class NpcTrackerRegistry
{
	private final Client client;
	private final EventBus eventBus;
	private final UdpEventSender udpEventSender;
	private final List<NpcTrackerFactory> factories = new ArrayList<>();

	// Package-private (not private) so this task's same-package tests can assert on attachment
	// state directly, instead of needing a getter whose only caller would be a test.
	// Keyed by NPC, then by the factory that produced each tracker, enabling per-factory
	// re-validation instead of aggregate booleans.
	final Map<NPC, Map<NpcTrackerFactory, NpcTracker>> attached = new HashMap<>();
	private final Map<NPC, Integer> lastAnimationId = new HashMap<>();
	private final Map<NPC, Integer> lastAnimationFrame = new HashMap<>();

	public NpcTrackerRegistry(Client client, EventBus eventBus, UdpEventSender udpEventSender)
	{
		this.client = client;
		this.eventBus = eventBus;
		this.udpEventSender = udpEventSender;
	}

	/** Wires this instance onto the event bus. Call once, e.g. from ControllerPlugin.startUp(). */
	public void register()
	{
		eventBus.register(this);
	}

	/** Registers a factory. An NPC can end up with more than one tracker if more than one
	 * factory matches it -- each is independent. */
	public void addFactory(NpcTrackerFactory factory)
	{
		factories.add(factory);
	}

	@Subscribe
	public void onNpcSpawned(NpcSpawned event)
	{
		attachMatchingFactories(event.getNpc());
	}

	private void attachMatchingFactories(NPC npc)
	{
		for (NpcTrackerFactory factory : factories)
		{
			if (factory.isValidTrackingTarget(npc))
			{
				Map<NpcTrackerFactory, NpcTracker> trackersByFactory = attached.computeIfAbsent(npc, n -> new HashMap<>());
				// Only create tracker if this factory hasn't already produced one for this NPC
				if (!trackersByFactory.containsKey(factory))
				{
					trackersByFactory.put(factory, factory.newInstance(npc));
				}
			}
		}
	}

	@Subscribe
	public void onNpcChanged(NpcChanged event)
	{
		revalidateFactoriesFor(event.getNpc());
	}

	private void revalidateFactoriesFor(NPC npc)
	{
		Map<NpcTrackerFactory, NpcTracker> trackersByFactory = attached.get(npc);

		// Re-validate each factory independently
		for (NpcTrackerFactory factory : factories)
		{
			boolean hasTracker = trackersByFactory != null && trackersByFactory.containsKey(factory);
			boolean nowValid = factory.isValidTrackingTarget(npc);

			if (hasTracker && !nowValid)
			{
				// Detach this one tracker
				NpcTracker tracker = trackersByFactory.remove(factory);
				safely(tracker::onDetach);
				// Clean up if inner map is now empty
				if (trackersByFactory.isEmpty())
				{
					attached.remove(npc);
					lastAnimationId.remove(npc);
					lastAnimationFrame.remove(npc);
				}
			}
			else if (!hasTracker && nowValid)
			{
				// Attach this factory's tracker
				NpcTracker tracker = factory.newInstance(npc);
				attached.computeIfAbsent(npc, n -> new HashMap<>()).put(factory, tracker);
			}
		}
	}

	@Subscribe
	public void onNpcDespawned(NpcDespawned event)
	{
		detachAll(event.getNpc());
	}

	private void detachAll(NPC npc)
	{
		Map<NpcTrackerFactory, NpcTracker> trackersByFactory = attached.remove(npc);
		lastAnimationId.remove(npc);
		lastAnimationFrame.remove(npc);
		if (trackersByFactory == null)
		{
			return;
		}
		for (NpcTracker tracker : trackersByFactory.values())
		{
			safely(tracker::onDetach);
		}
	}

	@Subscribe
	public void onGameStateChanged(GameStateChanged event)
	{
		if (event.getGameState() != GameState.HOPPING)
		{
			return;
		}
		for (NPC npc : new ArrayList<>(attached.keySet()))
		{
			detachAll(npc);
		}
	}

	@Subscribe
	public void onHitsplatApplied(HitsplatApplied event)
	{
		if (!(event.getActor() instanceof NPC))
		{
			return;
		}
		NPC npc = (NPC) event.getActor();
		Map<NpcTrackerFactory, NpcTracker> trackers = attached.get(npc);
		if (trackers == null)
		{
			return;
		}

		Hitsplat hitsplat = event.getHitsplat();
		boolean anyListener = false;
		for (NpcTracker tracker : trackers.values())
		{
			if (tracker instanceof HitsplatListener)
			{
				anyListener = true;
				HitsplatListener listener = (HitsplatListener) tracker;
				safely(() -> listener.onHitsplat(hitsplat));
			}
		}

		if (!anyListener)
		{
			return;
		}

		Map<String, Object> payload = new HashMap<>();
		payload.put("index", npc.getIndex());
		payload.put("id", npc.getId());
		payload.put("name", npc.getName());
		payload.put("hitsplatType", hitsplat.getHitsplatType());
		payload.put("amount", hitsplat.getAmount());
		udpEventSender.send("NPC_TRACKED_HITSPLAT", payload);
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		revalidateCurrentTarget();

		int tick = client.getTickCount();
		for (Map<NpcTrackerFactory, NpcTracker> trackers : attached.values())
		{
			for (NpcTracker tracker : trackers.values())
			{
				if (tracker instanceof TrackerTickListener)
				{
					TrackerTickListener listener = (TrackerTickListener) tracker;
					safely(() -> listener.onTrackerTick(tick));
				}
			}
		}
	}

	private void revalidateCurrentTarget()
	{
		// Already-tracked NPCs might have stopped matching (e.g. the player switched targets) --
		// bounded by however many NPCs are currently tracked, not a scene-wide scan.
		for (NPC npc : new ArrayList<>(attached.keySet()))
		{
			revalidateFactoriesFor(npc);
		}

		// The player's current target might have no attached trackers at all yet -- e.g. it already
		// existed in the scene before the player started interacting with it, so it never got a
		// chance to match via onNpcSpawned/onNpcChanged. Check it explicitly.
		Player localPlayer = client.getLocalPlayer();
		if (localPlayer != null && localPlayer.getInteracting() instanceof NPC)
		{
			revalidateFactoriesFor((NPC) localPlayer.getInteracting());
		}
	}

	@Subscribe
	public void onClientTick(ClientTick event)
	{
		for (Map.Entry<NPC, Map<NpcTrackerFactory, NpcTracker>> entry : attached.entrySet())
		{
			NPC npc = entry.getKey();
			Map<NpcTrackerFactory, NpcTracker> trackers = entry.getValue();
			boolean anyAnimationListener = trackers.values().stream().anyMatch(t -> t instanceof AnimationListener);
			if (!anyAnimationListener)
			{
				continue;
			}

			int animation = npc.getAnimation();
			int animationFrame = npc.getAnimationFrame();
			Integer previousId = lastAnimationId.put(npc, animation);
			Integer previousFrame = lastAnimationFrame.put(npc, animationFrame);

			boolean changed = previousId == null
				|| animation != previousId
				|| (previousFrame != null && animationFrame < previousFrame);
			if (!changed)
			{
				continue;
			}

			for (NpcTracker tracker : trackers.values())
			{
				if (tracker instanceof AnimationListener)
				{
					AnimationListener listener = (AnimationListener) tracker;
					safely(() -> listener.onAnimationChanged(animation));
				}
			}

			Map<String, Object> payload = new HashMap<>();
			payload.put("index", npc.getIndex());
			payload.put("id", npc.getId());
			payload.put("name", npc.getName());
			payload.put("animationId", animation);
			udpEventSender.send("NPC_TRACKED_ANIMATION_CHANGED", payload);
		}
	}

	private void safely(Runnable action)
	{
		try
		{
			action.run();
		}
		catch (Exception e)
		{
			System.err.println("NpcTrackerRegistry listener threw: " + e.getMessage());
		}
	}
}
