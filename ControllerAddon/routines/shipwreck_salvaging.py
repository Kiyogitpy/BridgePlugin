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
#   - CONFIRMED live 2026-08-02 (#3): re-clicking an already-deployed hook IS safe -- it does not
#     retrieve/stop it. tick() below re-clicks whenever idle, same pattern as mining.py's
#     click-the-rock-whenever-idle loop, which self-heals from any stop reason (the wreck sank, a
#     stray misclick retrieved it, ...) instead of needing to track why it stopped.
#   - CONFIRMED live 2026-08-01 (#4): the hook does NOT keep working on its own once its wreck
#     sinks, even if a different active wreck of the same name is still in range -- salvaging
#     visibly stopped. No longer tracked explicitly (see #3 above) -- the idle-triggered re-click
#     covers this the same as every other stop reason.

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

    if not controller.get_combat().get("isIdle", True):
        return  # already mid-roll -- don't reclick

    near_wreck = find_nearby_wreck(config, config.get("maxDistance", 15))
    if near_wreck is not None:
        if not controller.get_sailing().get("navigating"):
            sailing.deploy_hook(ctx, config.get("hookName", ""))
        return

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60))
    if far_wreck is not None:
        if sailing.sail_to(ctx, far_wreck) and not controller.get_sailing().get("navigating"):
            sailing.deploy_hook(ctx, config.get("hookName", ""))
        return

    now = time.time()
    if now - ctx.state.data.get("last_waiting_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
        print("[salvage] no shipwreck in range -- waiting")
        ctx.state.data["last_waiting_message_at"] = now


def drop_salvage(ctx):
    """Drop every inventory item whose name ends in "salvage" (covers all 8 tiers -- Small,
    Fishy, Barracuda, Large, Plundered, Martial, Fremennik, Opulent -- without needing a config
    list). Left-click-to-drop needs "Drop" in Controller Settings -> Clicking -> Forced Actions,
    same convention as the looting bag's "Empty" elsewhere in this codebase.

    check_ui_blockers=False: CONFIRMED live 2026-08-01 -- click_entity's default UI-blocker check
    exists to catch a *world* click accidentally landing on the floating inventory panel (in
    Resizable-Classic layout), but an inventory item's own screen position is naturally *inside*
    that same panel -- the check flagged every salvage-item click as "blocked", endlessly rotated
    the camera trying to un-block something that was never a world click, and never actually
    clicked anything, so salvage silently never dropped. Not appropriate for any inventory-item
    click, so skip it here same as the rest of this file's world-object clicks keep it on."""
    controller.open_inventory_tab(ctx.ser)
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if name.endswith("salvage"):
            controller.click_entity(ctx.ser, item, check_ui_blockers=False)
            time.sleep(random.uniform(0.1, 0.2))  # ~2x faster than the original 0.2-0.4s, per request


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
