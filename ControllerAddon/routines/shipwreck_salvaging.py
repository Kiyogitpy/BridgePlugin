# routines/shipwreck_salvaging.py
#
# Shipwreck Salvaging routine: deploy the boat's own salvaging hook near a matching shipwreck
# and keep it running, sailing to a new wreck when nothing is left in range. Unlike every other
# routine here, the wreck itself is never clicked -- it only has an Inspect option. The real
# interaction is the salvaging hook facility built on the player's own boat -- see
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md for the full research behind
# this and the list of assumptions still needing in-game verification.
#
# Run with:
#     python controller.py routines/shipwreck_salvaging.py
#
# ASSUMPTIONS TO VERIFY IN-GAME (see the spec's "Still unverified" section for the full list):
#   - Re-clicking an already-deployed hook might not be safe (its default click option may swap
#     once deployed, #3) -- this routine deploys once per arrival and never re-clicks later.
#   - Whether the hook keeps working on its own once the current wreck sinks and a new one rises
#     in the same spot, or needs re-deploying (#4) -- assumed yes (do nothing) until proven
#     otherwise.

import random
import time

import controller
import sailing

DEFAULT_CONFIG = {
    "enabled": True,
    "wreckNames": ["Small shipwreck"],
    "hookName": "Bronze salvaging hook",
    "maxDistance": 15,       # hook-operating range -- gathering happens here, no boat movement
    "maxSailDistance": 60,   # a farther wreck worth repositioning the boat for

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
        "stopOnInventoryFull": False,   # routine drops salvage itself, see drop_salvage
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # This routine doesn't need anything from the Controller Settings panel.
    return DEFAULT_CONFIG


WAITING_MESSAGE_INTERVAL_S = 30.0


def tick(ctx):
    config = ctx.state.config

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        drop_salvage(ctx)
        return

    near_wreck = find_nearby_wreck(config, config.get("maxDistance", 15))
    if near_wreck is not None:
        if not ctx.state.data.get("hook_deployed"):
            if sailing.deploy_hook(ctx, config.get("hookName", "")):
                ctx.state.data["hook_deployed"] = True
        return

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60), require_visible=False)
    if far_wreck is not None:
        ctx.state.data["hook_deployed"] = False
        if sailing.sail_to(ctx, far_wreck):
            if sailing.deploy_hook(ctx, config.get("hookName", "")):
                ctx.state.data["hook_deployed"] = True
        return

    now = time.time()
    if now - ctx.state.data.get("last_waiting_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
        print("[salvage] no shipwreck in range -- waiting")
        ctx.state.data["last_waiting_message_at"] = now


def drop_salvage(ctx):
    """Drop every inventory item whose name ends in "salvage" (covers all 8 tiers -- Small,
    Fishy, Barracuda, Large, Plundered, Martial, Fremennik, Opulent -- without needing a config
    list). Left-click-to-drop needs "Drop" in Controller Settings -> Clicking -> Forced Actions,
    same convention as the looting bag's "Empty" elsewhere in this codebase. Does NOT touch
    ctx.state.data["hook_deployed"] -- dropping items doesn't retrieve the hook (only clicking
    away from it, e.g. the helm, does that -- confirmed by the user), so re-deploying here would
    be an unnecessary, possibly-unsafe re-click (see module header)."""
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if name.endswith("salvage"):
            controller.click_entity(ctx.ser, item)
            time.sleep(random.uniform(0.2, 0.4))


def find_nearby_wreck(config: dict, max_dist: float, require_visible: bool = True):
    """Nearest object matching config["wreckNames"] within max_dist tiles, or None.
    require_visible=True (the default, used for the hook-operating-range check) also requires
    the object to be on-screen-projectable right now. require_visible=False (used for the
    farther sail-range check) only needs its world x/y/plane -- ObjectProvider always sets those
    from the object's WorldPoint regardless of whether it's currently on-screen, which is exactly
    what sailing.sail_to needs to steer toward it."""
    names = {n.lower() for n in config.get("wreckNames", [])}

    candidates = [
        o for o in controller.get_objects()
        if (o.get("name") or "").lower() in names
        and (not require_visible or o.get("visible", True))
        and o.get("x") is not None
        and 0 <= o.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
