# routines/gemstone_crab.py
#
# Gemstone Crab routine: attack the crab; when it's no longer found (it's
# fled underground), follow it by clicking the tunnel ("Cave"), then repeat.
# There's no RuneLite settings panel for this yet (same as mining.py), so it
# just keeps its own DEFAULT_CONFIG instead of reading GET /config.
#
# Run with:
#     python controller.py routines/gemstone_crab.py
#
# IMPORTANT: the cave tends to have other objects (trees, rocks) rendered
# near it, and a plain click can resolve to whichever one happens to share
# that pixel -- add "Cave" to Controller Settings -> Clicking -> Priority
# Objects so the click always resolves to it regardless of what's in the
# way (same trick as shift-click forcing "Walk here", already built for
# exactly this kind of case). setup() below prints a one-time reminder.
# This can't be set from the routine itself: Priority Objects is a
# RuneLite-panel setting read by MenuPriority.java from ControllerConfig,
# and this routine never talks to the bridge's real GET /config.

import random
import time

import controller

DEFAULT_CONFIG = {
    "enabled": True,
    "crabName": "gemstone crab",
    "tunnelName": "cave",
    "maxDistance": 15,

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # This routine doesn't need anything from the Controller Settings panel.
    return DEFAULT_CONFIG


def setup(ctx):
    tunnel_name = DEFAULT_CONFIG["tunnelName"]
    print(
        f"[crab] reminder: add \"{tunnel_name}\" to Controller Settings -> Clicking -> "
        f"Priority Objects, so a click near it can't resolve to a nearby tree/rock instead."
    )


def tick(ctx):
    config = ctx.state.config

    if not controller.get_combat().get("isIdle", True):
        return  # already mid-swing or walking -- don't reclick

    crab = find_nearby(controller.get_npcs(), config, "crabName")
    if crab:
        print(f"[crab] attacking (dist={crab.get('distance')})")
        time.sleep(random.uniform(0.15, 0.4))
        controller.click_entity(ctx.ser, crab)
        return

    tunnel = find_nearby(controller.get_objects(), config, "tunnelName")
    if tunnel:
        print("[crab] not found -- following it into the tunnel")
        time.sleep(random.uniform(0.2, 0.5))
        controller.click_entity(ctx.ser, tunnel)
        time.sleep(random.uniform(1.0, 1.5))  # entering transition
        return

    print("[crab] neither the crab nor the tunnel is visible nearby")


def find_nearby(entities: list, config: dict, name_key: str):
    name = config.get(name_key, "").lower()
    max_dist = config.get("maxDistance", 15)

    candidates = [
        e for e in entities
        if name in (e.get("name") or "").lower()
        and e.get("visible", True)
        and 0 <= e.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda e: e.get("distance", 9999))
    return candidates[0]
