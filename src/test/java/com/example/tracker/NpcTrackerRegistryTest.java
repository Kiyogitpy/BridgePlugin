package com.example.tracker;

import com.example.util.UdpEventSender;
import net.runelite.api.Client;
import net.runelite.api.NPC;
import net.runelite.api.events.NpcSpawned;
import net.runelite.client.eventbus.EventBus;
import org.junit.Before;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

public class NpcTrackerRegistryTest
{
	private Client client;
	private EventBus eventBus;
	private UdpEventSender udpEventSender;
	private NpcTrackerRegistry registry;

	@Before
	public void setUp()
	{
		client = mock(Client.class);
		eventBus = mock(EventBus.class);
		udpEventSender = mock(UdpEventSender.class);
		registry = new NpcTrackerRegistry(client, eventBus, udpEventSender);
	}

	private static NPC npc(int index)
	{
		NPC npc = mock(NPC.class);
		when(npc.getIndex()).thenReturn(index);
		return npc;
	}

	@Test
	public void attachesTrackerWhenFactoryMatches()
	{
		NPC target = npc(1);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);

		registry.onNpcSpawned(new NpcSpawned(target));

		assertEquals(1, registry.attached.get(target).size());
		assertTrue(registry.attached.get(target).containsValue(tracker));
	}

	@Test
	public void doesNotAttachWhenFactoryDoesNotMatch()
	{
		NPC other = npc(2);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(other)).thenReturn(false);
		registry.addFactory(factory);

		registry.onNpcSpawned(new NpcSpawned(other));

		assertEquals(null, registry.attached.get(other));
	}

	@Test
	public void multipleMatchingFactoriesEachAttachTheirOwnTracker()
	{
		NPC target = npc(3);
		NpcTracker trackerA = mock(NpcTracker.class);
		NpcTracker trackerB = mock(NpcTracker.class);
		NpcTrackerFactory factoryA = mock(NpcTrackerFactory.class);
		NpcTrackerFactory factoryB = mock(NpcTrackerFactory.class);
		when(factoryA.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryA.newInstance(target)).thenReturn(trackerA);
		when(factoryB.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryB.newInstance(target)).thenReturn(trackerB);
		registry.addFactory(factoryA);
		registry.addFactory(factoryB);

		registry.onNpcSpawned(new NpcSpawned(target));

		assertEquals(2, registry.attached.get(target).size());
	}

	@Test
	public void detachesAndCallsOnDetachWhenNpcDespawns()
	{
		NPC target = npc(4);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onNpcDespawned(new net.runelite.api.events.NpcDespawned(target));

		assertEquals(null, registry.attached.get(target));
		org.mockito.Mockito.verify(tracker).onDetach();
	}

	@Test
	public void npcChangedDetachesWhenNoLongerValid()
	{
		NPC target = npc(5);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.newInstance(target)).thenReturn(tracker);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		when(factory.isValidTrackingTarget(target)).thenReturn(false);
		registry.onNpcChanged(new net.runelite.api.events.NpcChanged(target, null));

		assertEquals(null, registry.attached.get(target));
	}

	@Test
	public void npcChangedAttachesWhenNewlyValid()
	{
		NPC target = npc(6);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(false);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target)); // not attached yet

		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.onNpcChanged(new net.runelite.api.events.NpcChanged(target, null));

		assertEquals(1, registry.attached.get(target).size());
	}

	@Test
	public void gameStateHoppingClearsEverything()
	{
		NPC target = npc(7);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		net.runelite.api.events.GameStateChanged event = new net.runelite.api.events.GameStateChanged();
		event.setGameState(net.runelite.api.GameState.HOPPING);
		registry.onGameStateChanged(event);

		assertEquals(null, registry.attached.get(target));
		org.mockito.Mockito.verify(tracker).onDetach();
	}

	@Test
	public void npcChangedHandlesSimultaneousFactoryTransitionIndependently()
	{
		NPC target = npc(8);
		NpcTracker trackerA = mock(NpcTracker.class);
		NpcTracker trackerB = mock(NpcTracker.class);
		NpcTrackerFactory factoryA = mock(NpcTrackerFactory.class);
		NpcTrackerFactory factoryB = mock(NpcTrackerFactory.class);

		// Factory A initially matches, Factory B doesn't
		when(factoryA.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryA.newInstance(target)).thenReturn(trackerA);
		when(factoryB.isValidTrackingTarget(target)).thenReturn(false);
		registry.addFactory(factoryA);
		registry.addFactory(factoryB);
		registry.onNpcSpawned(new NpcSpawned(target));

		// Verify A is attached, B is not
		assertEquals(1, registry.attached.get(target).size());
		assertTrue(registry.attached.get(target).containsValue(trackerA));

		// Now flip: Factory A no longer matches, Factory B starts matching
		when(factoryA.isValidTrackingTarget(target)).thenReturn(false);
		when(factoryB.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryB.newInstance(target)).thenReturn(trackerB);
		registry.onNpcChanged(new net.runelite.api.events.NpcChanged(target, null));

		// Verify A is detached (onDetach called) and B is now attached
		org.mockito.Mockito.verify(trackerA).onDetach();
		assertEquals(1, registry.attached.get(target).size());
		assertTrue(registry.attached.get(target).containsValue(trackerB));
	}
}
