# routines/gemstone_crab.py
#
# Gemstone Crab routine: attack the crab. Once it's killed it leaves a
# carcass (same name, but no longer attackable) instead of just despawning,
# and the next crab only comes out once the cave ("Cave") has been climbed
# through -- but exactly once per carcass. Reclicking the cave while the same
# carcass is still lying there does nothing but spam it, and the routine has
# no reliable way to tell "new crab is still coming out" from "climb didn't
# register" other than waiting for either a fresh attackable crab or a new
# carcass to show up. There's no RuneLite settings panel for this yet (same
# as mining.py), so it just keeps its own DEFAULT_CONFIG instead of reading
# GET /config.
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
    "crabName": "Gemstone Crab",
    "tunnelName": "cave",
    "maxDistance": 25,

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


# Applied before every click below (attacking, climbing the cave) so the routine
# doesn't react to a newly-found target instantly every single time -- one
# shared range instead of a different one-off sleep per click.
REACTION_DELAY_SECONDS = (0.2, 0.5)

# Settle time after climbing through the cave, before the routine starts
# looking for the next crab.
POST_CLIMB_DELAY_SECONDS = (1.0, 1.5)

# Guessed generously since the real respawn timing isn't known -- tune down if
# a successful climb reliably produces a new crab much sooner than this. If the
# tunnel click missed (blocked by UI, overshot onto a neighboring tree/rock --
# see the Priority Objects note above) the latch would otherwise never clear,
# since no new crab is ever coming to trigger it. This timeout supersedes the
# latch so a failed click gets retried instead of stranding the routine.
CLIMB_RETRY_TIMEOUT_SECONDS = (18.0, 25.0)


def tick(ctx):
    config = ctx.state.config
    data = ctx.state.data

    if not controller.get_combat().get("isIdle", True):
        return  # already mid-swing or walking -- don't reclick

    # Not gated on clickable here: an attackable crab that isn't clickable
    # (off-screen) is still very much alive, and must not fall through to the
    # carcass check below -- that search doesn't filter on attackable at all,
    # so an off-screen live crab would otherwise get mistaken for a carcass
    # and trigger a needless tunnel climb.
    crab = find_nearby(controller.get_npcs(), config, "crabName", require_attackable=True)
    if crab:
        if not crab.get("clickable", True):
            print("[crab] found but off-screen -- rotating camera to bring it into view")
            controller.rotate_camera_to_reveal(ctx.ser, crab.get("screenX", 0))
            return

        print(f"[crab] attacking (dist={crab.get('distance')})")
        time.sleep(random.uniform(*REACTION_DELAY_SECONDS))
        controller.click_entity(ctx.ser, crab)
        data["climbed_for_carcass"] = False  # a fresh crab -- next carcass gets its own one-time climb
        data["crab_confirmed_dead"] = False  # ditto for the kill-confirmation latch below
        return

    # Latched rather than re-checked live: a carcass can despawn before we get
    # around to clicking the cave (e.g. it took a few ticks to notice, or the
    # first climb attempt missed), and without remembering "a kill happened"
    # independently of the corpse still being visible, the routine would just
    # stall forever once it's gone -- never found not attackable via the
    # first search, so it'd never be revisited despite knowing better once.
    carcass = find_nearby(controller.get_npcs(), config, "crabName", require_attackable=False)
    if carcass:
        data["crab_confirmed_dead"] = True

    if not data.get("crab_confirmed_dead"):
        print("[crab] neither the crab nor a carcass is visible nearby")
        return

    if data.get("climbed_for_carcass"):
        if time.time() < data.get("climbed_for_carcass_deadline", 0):
            return  # still within the normal respawn window -- don't spam the cave
        print("[crab] no new crab since climbing -- click may have missed, retrying the cave")
        data["climbed_for_carcass"] = False

    tunnel = find_nearby(controller.get_objects(), config, "tunnelName")
    if tunnel:
        if not tunnel.get("clickable", True):
            print("[crab] cave found but off-screen -- rotating camera to bring it into view")
            controller.rotate_camera_to_reveal(ctx.ser, tunnel.get("screenX", 0))
            return

        print("[crab] confirmed a kill -- climbing through the cave (once) to bring out the next crab")
        time.sleep(random.uniform(*REACTION_DELAY_SECONDS))
        clicked = controller.click_entity(ctx.ser, tunnel)
        time.sleep(random.uniform(*POST_CLIMB_DELAY_SECONDS))  # entering transition
        if clicked:
            data["climbed_for_carcass"] = True
            data["climbed_for_carcass_deadline"] = time.time() + random.uniform(*CLIMB_RETRY_TIMEOUT_SECONDS)
        return

    print("[crab] confirmed a kill but no cave nearby to climb through")


def find_nearby(entities: list, config: dict, name_key: str, require_attackable: bool = False):
    name = config.get(name_key, "").lower()
    max_dist = config.get("maxDistance", 15)

    candidates = [
        e for e in entities
        if name in (e.get("name") or "").lower()
        and e.get("visible", True)
        and (not require_attackable or e.get("attackable", True))
        and 0 <= e.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda e: e.get("distance", 9999))
    return candidates[0]
