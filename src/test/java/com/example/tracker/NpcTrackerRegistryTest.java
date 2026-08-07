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

	private static net.runelite.api.Hitsplat hitsplat(int type, int amount)
	{
		net.runelite.api.Hitsplat h = mock(net.runelite.api.Hitsplat.class);
		when(h.getHitsplatType()).thenReturn(type);
		when(h.getAmount()).thenReturn(amount);
		return h;
	}

	private interface FullTracker extends AnimationListener, HitsplatListener, TrackerTickListener
	{
	}

	@Test
	public void hitsplatAppliedCallsAttachedHitsplatListenerAndPushesUdpEvent()
	{
		NPC target = npc(8);
		when(target.getId()).thenReturn(100);
		when(target.getName()).thenReturn("Cockatrice");
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		net.runelite.api.Hitsplat splat = hitsplat(1, 7);
		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(target);
		event.setHitsplat(splat);
		registry.onHitsplatApplied(event);

		org.mockito.Mockito.verify(tracker).onHitsplat(splat);
		org.mockito.ArgumentCaptor<java.util.Map> payloadCaptor = org.mockito.ArgumentCaptor.forClass(java.util.Map.class);
		org.mockito.Mockito.verify(udpEventSender).send(org.mockito.Mockito.eq("NPC_TRACKED_HITSPLAT"), payloadCaptor.capture());
		java.util.Map<String, Object> payload = payloadCaptor.getValue();
		assertEquals(8, payload.get("index"));
		assertEquals(100, payload.get("id"));
		assertEquals("Cockatrice", payload.get("name"));
		assertEquals(1, payload.get("hitsplatType"));
		assertEquals(7, payload.get("amount"));
	}

	@Test
	public void hitsplatAppliedIgnoresNonNpcActors()
	{
		net.runelite.api.Player player = mock(net.runelite.api.Player.class);
		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(player);
		event.setHitsplat(hitsplat(1, 5));

		registry.onHitsplatApplied(event); // must not throw

		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void hitsplatAppliedIgnoresUntrackedNpcs()
	{
		NPC untracked = npc(9);
		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(untracked);
		event.setHitsplat(hitsplat(1, 5));

		registry.onHitsplatApplied(event);

		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void hitsplatListenerExceptionDoesNotStopOtherListeners()
	{
		NPC target = npc(10);
		FullTracker throwing = mock(FullTracker.class);
		org.mockito.Mockito.doThrow(new RuntimeException("boom")).when(throwing).onHitsplat(org.mockito.Mockito.any());
		FullTracker fine = mock(FullTracker.class);
		NpcTrackerFactory factoryA = mock(NpcTrackerFactory.class);
		when(factoryA.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryA.newInstance(target)).thenReturn(throwing);
		NpcTrackerFactory factoryB = mock(NpcTrackerFactory.class);
		when(factoryB.isValidTrackingTarget(target)).thenReturn(true);
		when(factoryB.newInstance(target)).thenReturn(fine);
		registry.addFactory(factoryA);
		registry.addFactory(factoryB);
		registry.onNpcSpawned(new NpcSpawned(target));

		net.runelite.api.events.HitsplatApplied event = new net.runelite.api.events.HitsplatApplied();
		event.setActor(target);
		event.setHitsplat(hitsplat(1, 5));
		registry.onHitsplatApplied(event); // must not throw

		org.mockito.Mockito.verify(fine).onHitsplat(org.mockito.Mockito.any());
	}

	@Test
	public void clientTickCallsOnAnimationChangedWhenAnimationDiffers()
	{
		NPC target = npc(11);
		when(target.getId()).thenReturn(200);
		when(target.getName()).thenReturn("Turoth");
		when(target.getAnimation()).thenReturn(1156);
		when(target.getAnimationFrame()).thenReturn(0);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onClientTick(new net.runelite.api.events.ClientTick());

		org.mockito.Mockito.verify(tracker).onAnimationChanged(1156);
		org.mockito.ArgumentCaptor<java.util.Map> payloadCaptor = org.mockito.ArgumentCaptor.forClass(java.util.Map.class);
		org.mockito.Mockito.verify(udpEventSender).send(org.mockito.Mockito.eq("NPC_TRACKED_ANIMATION_CHANGED"), payloadCaptor.capture());
		java.util.Map<String, Object> payload = payloadCaptor.getValue();
		assertEquals(11, payload.get("index"));
		assertEquals(200, payload.get("id"));
		assertEquals("Turoth", payload.get("name"));
		assertEquals(1156, payload.get("animationId"));
	}

	@Test
	public void clientTickDoesNotFireWhenAnimationUnchanged()
	{
		NPC target = npc(12);
		when(target.getAnimation()).thenReturn(-1);
		when(target.getAnimationFrame()).thenReturn(0);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onClientTick(new net.runelite.api.events.ClientTick()); // first poll: -1 -> -1, no prior state, treated as initial baseline
		org.mockito.Mockito.clearInvocations(tracker, udpEventSender);
		registry.onClientTick(new net.runelite.api.events.ClientTick()); // second poll: still -1, genuinely unchanged

		org.mockito.Mockito.verify(tracker, org.mockito.Mockito.never()).onAnimationChanged(org.mockito.Mockito.anyInt());
		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void clientTickDetectsSameAnimationIdRestartingViaFrameRegression()
	{
		NPC target = npc(13);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		when(target.getAnimation()).thenReturn(390);
		when(target.getAnimationFrame()).thenReturn(5);
		registry.onClientTick(new net.runelite.api.events.ClientTick());
		org.mockito.Mockito.clearInvocations(tracker);

		// Same animation id, but the frame counter went backwards -- a fresh restart of the
		// same attack animation, not a continuation of the previous one.
		when(target.getAnimationFrame()).thenReturn(1);
		registry.onClientTick(new net.runelite.api.events.ClientTick());

		org.mockito.Mockito.verify(tracker).onAnimationChanged(390);
	}

	@Test
	public void clientTickSkipsNpcsWithNoAnimationListenerAttached()
	{
		NPC target = npc(14);
		when(target.getAnimation()).thenReturn(999);
		NpcTracker plainTracker = mock(NpcTracker.class); // no listener interfaces
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(plainTracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onClientTick(new net.runelite.api.events.ClientTick()); // must not throw

		org.mockito.Mockito.verifyNoInteractions(udpEventSender);
	}

	@Test
	public void gameTickCallsOnTrackerTickWithCurrentTickCount()
	{
		when(client.getTickCount()).thenReturn(4242);
		NPC target = npc(15);
		FullTracker tracker = mock(FullTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		registry.onGameTick(new net.runelite.api.events.GameTick());

		org.mockito.Mockito.verify(tracker).onTrackerTick(4242);
	}

	@Test
	public void gameTickAttachesFactoryForNpcAlreadyInteractingWithLocalPlayer()
	{
		NPC target = npc(20); // already "existing" -- never spawned through the registry
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);

		net.runelite.api.Player player = mock(net.runelite.api.Player.class);
		when(player.getInteracting()).thenReturn(target);
		when(client.getLocalPlayer()).thenReturn(player);

		registry.onGameTick(new net.runelite.api.events.GameTick());

		assertEquals(1, registry.attached.get(target).size());
		assertTrue(registry.attached.get(target).containsValue(tracker));
	}

	@Test
	public void gameTickDetachesTrackerWhenAttachedNpcNoLongerMatchesAnyFactory()
	{
		NPC target = npc(21);
		NpcTracker tracker = mock(NpcTracker.class);
		NpcTrackerFactory factory = mock(NpcTrackerFactory.class);
		when(factory.isValidTrackingTarget(target)).thenReturn(true);
		when(factory.newInstance(target)).thenReturn(tracker);
		registry.addFactory(factory);
		registry.onNpcSpawned(new NpcSpawned(target));

		when(factory.isValidTrackingTarget(target)).thenReturn(false);
		when(client.getLocalPlayer()).thenReturn(null); // nobody interacting with anything

		registry.onGameTick(new net.runelite.api.events.GameTick());

		assertEquals(null, registry.attached.get(target));
		org.mockito.Mockito.verify(tracker).onDetach();
	}
}
