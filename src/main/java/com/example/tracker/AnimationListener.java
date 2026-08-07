package com.example.tracker;

/**
 * Receives a callback whenever the tracked NPC's animation actually changes. Detected by
 * polling npc.getAnimation()/getAnimationFrame() every ClientTick and diffing -- NOT driven by
 * RuneLite's AnimationChanged event, which is not reliable for NPCs (confirmed against
 * Salvation's own NpcTracker, which does the same polling instead of subscribing to it).
 *
 * <p>Two callback timings are easy to misread as bugs but are intentional: (1) the very first
 * poll after a tracker attaches always fires, with whatever getAnimation() currently returns
 * (often -1 for an idle NPC) -- this establishes a real baseline instead of silently treating
 * "no prior state" as "unchanged". (2) when an animation ends, this fires again with
 * animationId == -1. Neither of these means the NPC "started attacking" -- check animationId
 * against the specific value(s) you care about.
 */
public interface AnimationListener extends NpcTracker
{
	void onAnimationChanged(int animationId);
}
