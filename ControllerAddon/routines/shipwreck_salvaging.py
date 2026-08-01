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
HOOK_STALL_TIMEOUT_S = 300.0  # no inventory growth this long after "deploying" means it didn't take


def tick(ctx):
    config = ctx.state.config

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        drop_salvage(ctx)
        return

    near_wreck = find_nearby_wreck(config, config.get("maxDistance", 15))
    if near_wreck is not None:
        if ctx.state.data.get("hook_deployed"):
            stalled = (
                time.time() - ctx.state.data.get("hook_deployed_at", 0) > HOOK_STALL_TIMEOUT_S
                and len(controller.get_inventory()) <= ctx.state.data.get("hook_deployed_inventory_count", 0)
            )
            if stalled:
                ctx.state.data["hook_deployed"] = False

        if not ctx.state.data.get("hook_deployed") and not controller.get_sailing().get("navigating"):
            if sailing.deploy_hook(ctx, config.get("hookName", "")):
                ctx.state.data["hook_deployed"] = True
                ctx.state.data["hook_deployed_at"] = time.time()
                ctx.state.data["hook_deployed_inventory_count"] = len(controller.get_inventory())
        return

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60))
    if far_wreck is not None:
        ctx.state.data["hook_deployed"] = False
        if sailing.sail_to(ctx, far_wreck) and not controller.get_sailing().get("navigating"):
            if sailing.deploy_hook(ctx, config.get("hookName", "")):
                ctx.state.data["hook_deployed"] = True
                ctx.state.data["hook_deployed_at"] = time.time()
                ctx.state.data["hook_deployed_inventory_count"] = len(controller.get_inventory())
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
    controller.open_inventory_tab(ctx.ser)
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if name.endswith("salvage"):
            controller.click_entity(ctx.ser, item)
            time.sleep(random.uniform(0.2, 0.4))


def find_nearby_wreck(config: dict, max_dist: float):
    """Nearest object matching config["wreckNames"] within max_dist tiles, or None. Pure range
    test -- the wreck itself is never clicked (only the boat's own salvaging hook is), so
    on-screen-projectability doesn't matter here, only world position. ObjectProvider always
    sets x/y/distance from the object's WorldPoint regardless of whether it's currently
    on-screen, which is exactly what both the hook-operating-range check and the farther
    sail-range check need."""
    names = {n.lower() for n in config.get("wreckNames", [])}

    candidates = [
        o for o in controller.get_objects()
        if (o.get("name") or "").lower() in names
        and o.get("x") is not None
        and 0 <= o.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
