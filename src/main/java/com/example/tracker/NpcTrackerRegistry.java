package com.example.tracker;

import com.example.util.UdpEventSender;
import net.runelite.api.Client;
import net.runelite.api.NPC;
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
	final Map<NPC, List<NpcTracker>> attached = new HashMap<>();

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
				attached.computeIfAbsent(npc, n -> new ArrayList<>()).add(factory.newInstance(npc));
			}
		}
	}

	@Subscribe
	public void onNpcChanged(NpcChanged event)
	{
		NPC npc = event.getNpc();
		List<NpcTracker> current = attached.get(npc);
		boolean wasTracked = current != null && !current.isEmpty();
		boolean stillValid = factories.stream().anyMatch(f -> f.isValidTrackingTarget(npc));

		if (wasTracked && !stillValid)
		{
			detachAll(npc);
		}
		else if (!wasTracked && stillValid)
		{
			attachMatchingFactories(npc);
		}
	}

	@Subscribe
	public void onNpcDespawned(NpcDespawned event)
	{
		detachAll(event.getNpc());
	}

	private void detachAll(NPC npc)
	{
		List<NpcTracker> trackers = attached.remove(npc);
		if (trackers == null)
		{
			return;
		}
		for (NpcTracker tracker : trackers)
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
		// Filled in by Task 3.
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		// Filled in by Task 4.
	}

	@Subscribe
	public void onClientTick(ClientTick event)
	{
		// Filled in by Task 4.
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
