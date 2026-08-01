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
#   - CONFIRMED live 2026-08-01 (#4): the hook does NOT keep working on its own once its wreck
#     sinks, even if a different active wreck of the same name is still in range -- salvaging
#     visibly stopped while the routine kept believing hook_deployed=True. Fixed by tracking which
#     specific wreck (by tile) the hook was deployed at and redeploying whenever that changes, see
#     tick() below -- no longer just "is *a* wreck nearby".

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
HOOK_STALL_TIMEOUT_S = 90.0  # no inventory growth this long after "deploying" means it didn't
                              # take -- shorter than a Small shipwreck's own ~1 minute lifespan
                              # (see the wiki's "Average duration" column) so this can't sit
                              # through a whole extra wreck cycle before reacting; the
                              # deployed_wreck_key check below is the primary defense against a
                              # sunk-and-not-redeployed hook, this is just a slower fallback for
                              # everything else (e.g. a misclick retrieving the hook).


def tick(ctx):
    config = ctx.state.config

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        drop_salvage(ctx)
        return

    near_wreck = find_nearby_wreck(config, config.get("maxDistance", 15))
    if near_wreck is not None:
        # CONFIRMED live: the hook does not keep collecting on its own once the specific wreck it
        # was deployed at sinks, even if a different active wreck of the same name is still within
        # maxDistance -- so "a wreck is nearby" alone isn't enough to trust hook_deployed. Track
        # which wreck (by tile) it was actually deployed at and force a redeploy the moment that
        # changes, rather than waiting on the stall-timeout fallback below.
        wreck_key = (near_wreck.get("x"), near_wreck.get("y"))
        if ctx.state.data.get("deployed_wreck_key") != wreck_key:
            ctx.state.data["hook_deployed"] = False
            ctx.state.data["deployed_wreck_key"] = wreck_key

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
        ctx.state.data["deployed_wreck_key"] = None
        if sailing.sail_to(ctx, far_wreck) and not controller.get_sailing().get("navigating"):
            if sailing.deploy_hook(ctx, config.get("hookName", "")):
                ctx.state.data["hook_deployed"] = True
                ctx.state.data["hook_deployed_at"] = time.time()
                ctx.state.data["hook_deployed_inventory_count"] = len(controller.get_inventory())
                ctx.state.data["deployed_wreck_key"] = (far_wreck.get("x"), far_wreck.get("y"))
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


# Sinking swaps a wreck's object out entirely for a "stump" variant that keeps the exact same
# display name as the active wreck (confirmed live via cache2 -- see
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md) -- id is the only way to tell
# them apart. One stump id per tier (Small..Merchant), confirmed against the live game cache.
SUNK_SHIPWRECK_IDS = frozenset({60465, 60467, 60469, 60471, 60473, 60475, 60477, 60479})


def find_nearby_wreck(config: dict, max_dist: float):
    """Nearest ACTIVE object matching config["wreckNames"] within max_dist tiles, or None (sunk
    wrecks -- see SUNK_SHIPWRECK_IDS -- are filtered out; matching them would let the routine
    treat a dead wreck as "in range" and never look for a real one). Pure range test otherwise --
    the wreck itself is never clicked (only the boat's own salvaging hook is), so
    on-screen-projectability doesn't matter here, only world position. ObjectProvider always
    sets x/y/distance from the object's WorldPoint regardless of whether it's currently
    on-screen, which is exactly what both the hook-operating-range check and the farther
    sail-range check need."""
    names = {n.lower() for n in config.get("wreckNames", [])}

    candidates = [
        o for o in controller.get_objects()
        if (o.get("name") or "").lower() in names
        and o.get("id") not in SUNK_SHIPWRECK_IDS
        and o.get("x") is not None
        and 0 <= o.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
