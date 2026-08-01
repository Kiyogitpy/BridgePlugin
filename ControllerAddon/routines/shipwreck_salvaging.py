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
#     retrieve/stop it. tick() below re-clicks whenever idle, self-healing from any stop reason
#     (the wreck sank, a stray misclick retrieved it, ...) instead of needing to track why it
#     stopped. UNLIKE mining.py's click-the-rock-whenever-idle loop, the isIdle check only gates
#     the reclick itself, not the whole near-wreck branch -- salvaging turned out to be a
#     continuous multi-tick channel (isIdle can stay False for a wreck's entire lifespan), so
#     gating the branch on it meant the routine never noticed a sunk wreck and never looked
#     farther out, confirmed live 2026-08-02.
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
HOOK_CLICK_COOLDOWN_S = 3.0  # don't reclick the hook again within this long of the last click --
                              # CONFIRMED live 2026-08-02: isIdle can read True for a tick or two
                              # right after clicking, before the salvaging animation actually
                              # starts, causing back-to-back redundant clicks without this. Still
                              # short enough to redeploy quickly for a genuine stop (wreck sank,
                              # misclick retrieved it, ...), just not on literally every tick.
HOOK_ENGAGE_TIMEOUT_S = 12.0  # CONFIRMED live 2026-08-02: maxDistance (the configured
                               # hook-operating range) is only a guess at the hook's real
                               # functional range -- observed live standing exactly at a wreck
                               # 15 tiles away (the configured default), isIdle stayed True and
                               # animation stayed -1 no matter how many times the hook got
                               # reclicked, because the wreck was actually still too far for the
                               # hook to engage even though it passed the maxDistance check. If
                               # isIdle never goes False for this long despite repeated reclicks,
                               # the wreck is probably out of real range regardless of what
                               # maxDistance said -- reposition closer instead of reclicking
                               # forever.


def tick(ctx):
    config = ctx.state.config

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        drop_salvage(ctx)
        return

    near_wreck = find_nearby_wreck(config, config.get("maxDistance", 15))
    if near_wreck is not None:
        # isIdle only gates the reclick itself (avoid spam-clicking mid-channel) -- it must NOT
        # gate this whole branch, or the far-wreck check below never runs. CONFIRMED live
        # 2026-08-02: salvaging is a continuous multi-tick channel, not brief per-swing bursts
        # like mining.py -- isIdle can stay False for a wreck's entire lifespan, so gating the
        # branch on it meant the routine never noticed the near wreck was gone and never looked
        # for a farther one to sail to.
        # No "not navigating" gate here (there used to be one) -- CONFIRMED live 2026-08-02:
        # get_sailing()["navigating"] reflects whether the helm/steering interface is still
        # engaged, not whether the boat is physically moving, and it can stay True even after the
        # boat has fully stopped (sails down) until the helm is explicitly left somehow. Gating on
        # it here got the routine permanently stuck ("stuck at the helm... not clicking the
        # hook") once navigating got stuck True. stop_boat() already waits for the boat's actual
        # *position* to stabilize before returning, which is the real "safe to deploy" signal.
        idle = controller.get_combat().get("isIdle", True)
        now = time.time()
        if not idle:
            # Channel confirmed actually running -- clear the stuck tracker below.
            ctx.state.data.pop("hook_engage_attempt_at", None)
        cooldown_elapsed = now - ctx.state.data.get("last_deploy_click_at", 0) > HOOK_CLICK_COOLDOWN_S
        if idle and cooldown_elapsed:
            attempt_start = ctx.state.data.setdefault("hook_engage_attempt_at", now)
            if now - attempt_start > HOOK_ENGAGE_TIMEOUT_S:
                print("[salvage] hook isn't engaging after repeated clicks -- repositioning closer")
                sailing.sail_to(ctx, near_wreck)
                ctx.state.data.pop("hook_engage_attempt_at", None)
            sailing.deploy_hook(ctx, config.get("hookName", ""))
            ctx.state.data["last_deploy_click_at"] = now
        return
    ctx.state.data.pop("hook_engage_attempt_at", None)

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60))
    if far_wreck is not None:
        # No "not navigating" gate here either -- see the near-wreck branch's comment above.
        if sailing.sail_to(ctx, far_wreck):
            sailing.deploy_hook(ctx, config.get("hookName", ""))
            ctx.state.data["last_deploy_click_at"] = time.time()
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
            time.sleep(random.uniform(0.06, 0.12))  # 40% faster still, on top of the earlier 2x (original 0.2-0.4s)


# Sinking swaps a wreck's object out entirely for a "stump" variant that keeps the exact same
# display name as the active wreck (confirmed live via cache2 -- see
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md) -- id is the only way to tell
# them apart. One stump id per tier (Small..Merchant), confirmed against the live game cache.
SUNK_SHIPWRECK_IDS = frozenset({60465, 60467, 60469, 60471, 60473, 60475, 60477, 60479})


def find_nearby_wreck(config: dict, max_dist: float):
    """Nearest ACTIVE object matching config["wreckNames"] within max_dist tiles of the BOAT (not
    the player), or None (sunk wrecks -- see SUNK_SHIPWRECK_IDS -- are filtered out; matching them
    would let the routine treat a dead wreck as "in range" and never look for a real one).

    Measures from /player's boatX/boatY (falling back to the player's own x/y if not aboard a
    WorldEntity) rather than trusting ObjectProvider's own player-relative "distance" field.
    CONFIRMED live 2026-08-02: a Sailing boat's own WorldView is only ~8 tiles per side, and
    standing at one end can be several tiles from the opposite end -- maxDistance is a range check
    against something the *boat*'s hook needs to reach, not specifically wherever the player
    happens to be standing on deck, so the player-relative distance could read "out of range" (or
    "in range") wrongly by that same margin.

    Pure range test otherwise -- the wreck itself is never clicked (only the boat's own salvaging
    hook is), so on-screen-projectability doesn't matter here, only world position. ObjectProvider
    always sets x/y from the object's WorldPoint regardless of whether it's currently on-screen,
    which is exactly what both the hook-operating-range check and the farther sail-range check
    need. Chebyshev distance (max(|dx|, |dy|)), matching WorldPoint.distanceTo2D's own convention
    on the Java side."""
    names = {n.lower() for n in config.get("wreckNames", [])}

    player = controller.get_player()
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    if origin_x is None or origin_y is None:
        return None

    best = None
    best_dist = None
    for o in controller.get_objects():
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        wx, wy = o.get("x"), o.get("y")
        if wx is None or wy is None:
            continue

        dist = max(abs(wx - origin_x), abs(wy - origin_y))
        if dist > max_dist:
            continue
        if best_dist is None or dist < best_dist:
            best, best_dist = o, dist

    return best
