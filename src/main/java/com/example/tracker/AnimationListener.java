package com.example.tracker;

/**
 * Receives a callback whenever the tracked NPC's animation actually changes. Detected by
 * polling npc.getAnimation()/getAnimationFrame() every ClientTick and diffing -- NOT driven by
 * RuneLite's AnimationChanged event, which is not reliable for NPCs (confirmed against
 * Salvation's own NpcTracker, which does the same polling instead of subscribing to it).
 */
public interface AnimationListener extends NpcTracker
{
	void onAnimationChanged(int animationId);
}
