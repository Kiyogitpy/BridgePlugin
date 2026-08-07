# routines/gemstone_crab.py
#
# Gemstone Crab routine: attack the crab; when it's no longer found (it's
# fled underground), follow it by clicking the tunnel ("Cave"), then repeat.
# There's no RuneLite settings panel for this yet (same as mining.py), so it
# just keeps its own DEFAULT_CONFIG instead of reading GET /config.
#image.pngst

# Run with:
#     python controller.py routines/gemstone_crab.py
#
# IMPORTANT: the cave tends to have other objects (trees, rocks) rendered
# near it, and a plain click can resolve to whichever one happens to share
# that pixel -- add "Cave" to Coroller Settings -> Clicking -> Priority
# Objects so the click always resolves to it regardless of what's in the
# way (same trick as shift-click forcing "Walk here", already built for
# exactly this kind of case). setup() below prints a one-time reminder.
# This can't be set from the routine itself: Priority Objects is a
# RuneLite-panel setting read by MenuPriority.java from ControllerConfig,
# and this routine never talks to the bridge's real GET /config.

import random
import time

import controller
import debug_log
import travel

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
    debug_log.log("crab", f"reminder: add \"{tunnel_name}\" to Controller Settings -> Clicking -> "
                           f"Priority Objects, so a click near it can't resolve to a nearby tree/rock instead.")


# After clicking the tunnel to follow the crab across, it takes a while to resurface on the
# other side. This is a generous fallback only -- tick() must not just re-click the tunnel
# every time it's still visible and the crab hasn't shown up yet, or it ping-pongs back and
# forth across the tunnel instead of waiting. It only fires if the crab genuinely never shows
# up in this long (wrong side, died to something else, etc.), as a recovery, not a retry timer.
TUNNEL_RETRY_TIMEOUT_SECONDS = 45.0

# Rolled once per tick while mid-fight (isIdle False) until it fires -- gives
# a natural-feeling delay before the mouse drifts toward the tunnel, rather
# than snapping over there the instant combat starts.
HOVER_NEXT_STEP_CHANCE = 0.08


def tick(ctx):
    config = ctx.state.config
    combat = controller.get_combat()

    if not combat.get("isIdle", True):
        maybe_hover_next_step(ctx, config)
        return  # already mid-swing or walking -- don't reclick

    crab = find_nearby(controller.get_npcs(), config, "crabName", require_attackable=True)
    if crab:
        # inCombat stays True for the whole fight even on an idle tick between attack
        # animations -- without checking it, a re-evaluation mid-fight could wander off
        # to approach a tile instead of just continuing the attack.
        if travel.maybe_approach_before_click(ctx, crab, already_engaged=combat.get("inCombat", False),
                                               via_minimap=False):
            ctx.state.data["tunnel_retry_at"] = None
            return
        debug_log.log("crab", f"attacking (dist={crab.get('distance')})")
        time.sleep(random.uniform(0.15, 0.4))
        controller.click_entity(ctx.ser, crab)
        ctx.state.data["tunnel_retry_at"] = None
        ctx.state.data["hovered_next_step"] = False  # fresh fight -- allow one more anticipatory hover
        return

    retry_at = ctx.state.data.get("tunnel_retry_at")
    if retry_at is not None:
        if time.time() < retry_at:
            return  # already followed the crab across -- give it time to resurface, don't touch the tunnel again
        debug_log.log("crab", "crab still hasn't shown up since crossing -- trying the tunnel again")
        ctx.state.data["tunnel_retry_at"] = None

    tunnel = find_nearby(controller.get_objects(), config, "tunnelName")
    if tunnel:
        if travel.maybe_approach_before_click(ctx, tunnel, already_engaged=combat.get("inCombat", False)):
            return
        debug_log.log("crab", "not found -- following it into the tunnel")
        time.sleep(random.uniform(0.2, 0.5))
        controller.click_entity(ctx.ser, tunnel)
        time.sleep(random.uniform(1.0, 1.5))  # entering transition
        ctx.state.data["tunnel_retry_at"] = time.time() + TUNNEL_RETRY_TIMEOUT_SECONDS
        return

    debug_log.log("crab", "neither the crab nor the tunnel is visible nearby")


def maybe_hover_next_step(ctx, config: dict) -> None:
    """While mid-fight and otherwise doing nothing, occasionally drift the
    mouse toward the tunnel -- the next thing this routine will need once
    the crab dies -- instead of just sitting still until then. Fires at
    most once per fight (tracked via ctx.state.data["hovered_next_step"],
    reset whenever a new crab attack starts)."""
    if ctx.state.data.get("hovered_next_step"):
        return
    if random.random() > HOVER_NEXT_STEP_CHANCE:
        return

    tunnel = find_nearby(controller.get_objects(), config, "tunnelName")
    if not tunnel:
        return

    if controller.hover_entity(ctx.ser, tunnel):
        ctx.state.data["hovered_next_step"] = True


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
