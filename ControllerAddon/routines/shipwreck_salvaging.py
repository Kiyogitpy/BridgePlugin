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
    "wreckNames": ["Small shipwreck", "Fisherman's shipwreck", "Large shipwreck"],
    "hookName": "salvaging hook",  # tier-agnostic on purpose: deploy_hook() matches this as a
                                    # substring against the boat's actual hook name, and the boat's
                                    # hook tier (Bronze/Iron/.../Dragon, see
                                    # data/decompiled/sailing-plugin's SalvagingHookTier) changes
                                    # independently of this routine's config -- pinning to one tier
                                    # name broke the moment the boat's hook got upgraded to Steel.
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
HOOK_ENGAGE_TIMEOUT_S = 12.0  # Kept as a backstop even after switching to the geometric
                               # WRECK_SQUARE_SIZE check (CONFIRMED live 2026-08-04). History: this
                               # was originally added because a flat maxDistance guess (15, then 8)
                               # didn't reliably predict real engagement -- observed live standing
                               # at a wreck that still failed to engage despite passing the
                               # distance check. If isIdle never goes False for this long despite
                               # repeated reclicks even though _wreck_in_hook_range said yes,
                               # reposition closer instead of reclicking forever -- covers any edge
                               # case the geometric check still misses (e.g. scene/tile lag).


def tick(ctx):
    config = ctx.state.config

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        drop_salvage(ctx)
        return

    near_wreck = find_wreck_in_hook_range(config)
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
                # Sail straight at near_wreck's own point -- NOT sail_to_pair. find_wreck_in_hook_range
                # already confirmed the boat's hull overlaps THIS wreck's square, so what's needed is
                # fine positioning on it, not sail_to_pair's pair-midpoint logic (meant for the initial
                # far-away approach): that can retarget toward a different, farther partner wreck up to
                # PAIR_MAX_TILES away and send the boat somewhere it was never stuck getting to before --
                # observed live 2026-08-04 running the boat aground trying to reach that new point right
                # after sitting fine at near_wreck.
                print("[salvage] hook isn't engaging after repeated clicks -- repositioning closer")
                sailing.sail_to(ctx, {"x": near_wreck["x"], "y": near_wreck["y"], "plane": near_wreck["plane"]})
                ctx.state.data.pop("hook_engage_attempt_at", None)
            sailing.deploy_hook(ctx, config.get("hookName", ""))
            ctx.state.data["last_deploy_click_at"] = now
        return
    ctx.state.data.pop("hook_engage_attempt_at", None)

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60))
    if far_wreck is not None:
        # No "not navigating" gate here either -- see the near-wreck branch's comment above.
        if sail_to_pair(ctx, config, far_wreck):
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
    click, so skip it here same as the rest of this file's world-object clicks keep it on.

    Forced "w" press when facilitiesTabOpen: CONFIRMED live 2026-08-02 -- the boat's Sailing
    Options (Facilities) panel, left open after any sail_to reposition, visually covers the
    inventory panel without clearing GameStateProvider's inventoryTabOpen flag (the inventory
    widget is still technically visible underneath), so open_inventory_tab's idempotent check
    silently skipped the "w" press and every item click landed on the sail menu instead of a
    salvage item, dropping nothing. Bypass the check and force the tab switch whenever the
    Facilities panel is open.

    Space fallback if "w" still didn't switch tabs: CONFIRMED live 2026-08-02 -- when the
    inventory fills up completely while on the Facilities tab, a standard-looking blue "click
    here to continue" dialogue appears and eats the "w" press entirely, leaving the Facilities
    tab open. It's not caught by controller.check_stray_dialog (GET /state's dialogOpen stayed
    False while the user confirmed the dialogue was still on screen) -- like every other Sailing
    UI element found this session, it apparently doesn't wire through the widgets it visually
    resembles. Dismissed with Space specifically (CONFIRMED live 2026-08-02 -- Enter was tried
    first and the user corrected this: it isn't right, Space is), matching
    controller.check_stray_dialog's own existing convention for this exact dialogue style. Only
    retried (not pressed proactively every call) to avoid the same risk that ruled out Enter --
    a key that isn't actually needed doing something unwanted when nothing was actually blocking
    -- this checks facilitiesTabOpen again first and only falls back once the plain "w" attempt is
    confirmed to have failed."""
    if controller.get_sailing().get("facilitiesTabOpen"):
        controller.key_tap(ctx.ser, controller.INVENTORY_TAB_KEY)
        time.sleep(random.uniform(0.2, 0.35))
        if controller.get_sailing().get("facilitiesTabOpen"):
            print("[salvage] still on the sail menu after 'w' -- dismissing a likely dialogue with Space")
            controller.key_tap(ctx.ser, "SPACE")
            time.sleep(random.uniform(0.3, 0.5))
            controller.key_tap(ctx.ser, controller.INVENTORY_TAB_KEY)
            time.sleep(random.uniform(0.2, 0.35))
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

# Required boosted Sailing level to actually engage each wreck tier's hook (active-wreck object id
# -> level) -- from data/decompiled/sailing-plugin's SalvagingHighlight.java SALVAGE_LEVEL_REQ,
# which the plugin itself checks (`sailingLevel >= SALVAGE_LEVEL_REQ.get(wreck.getId())`) before
# treating a wreck as "active" rather than "too high level" in its own highlight overlay. A wreck
# below this threshold isn't really salvageable yet even though it's a live, non-stump object
# matching wreckNames -- CONFIRMED live 2026-08-04 (user report): find_wreck_pair_target picked a
# higher-tier wreck as a pairing partner that the player's actual Sailing level couldn't engage,
# only one of the pair was genuinely salvageable, and the resulting midpoint wasn't a sensible
# target for either. req is looked up with a None-is-fine default (fail open) so an id missing from
# this map -- a tier not yet seen live -- doesn't silently exclude an otherwise-valid wreck.
SALVAGE_LEVEL_REQ = {
    60464: 15,
    60466: 26,
    60468: 35,
    60470: 53,
    60472: 64,
    60474: 73,
    60476: 80,
    60478: 87,
}


def _boosted_sailing_level() -> int:
    return controller.get_skills().get("Sailing", {}).get("boosted", 0)


def _wreck_is_salvageable(wreck: dict, sailing_level: int) -> bool:
    req = SALVAGE_LEVEL_REQ.get(wreck.get("id"))
    return req is None or sailing_level >= req


# Padding added on top of max_dist when bounding /objects' radius-limited scene walk (see
# find_nearby_wreck below). CONFIRMED live 2026-08-02: ObjectProvider's radius is centered on the
# PLAYER, but find_nearby_wreck measures from the BOAT's reference position, which can differ from
# the player's exact tile by up to the boat's own WorldView size (~8 tiles per side, confirmed
# live) -- without this padding, a wreck could be within max_dist of the boat but just outside the
# player-centered box and get silently missed.
WRECK_SEARCH_RADIUS_PADDING = 10

# The sideloaded Sailing plugin's SalvagingHighlight.java SIZE_SALVAGEABLE_AREA -- CONFIRMED live
# 2026-08-04 (user report) to be the hook's real engage boundary, replacing the earlier maxDistance
# guess (15, then 8) entirely. runelite-api's own Perspective.getCanvasTileAreaPoly centers this on
# the wreck's own tile ("size the size of the area (ie. 3x3 AoE evaluates to size 3)", and the
# implementation offsets by size * LOCAL_TILE_SIZE / 2 to each side) -- so it's a size x size
# square split evenly around the wreck, not a one-directional radius on its own.
WRECK_SQUARE_SIZE = 15
WRECK_SQUARE_HALF = WRECK_SQUARE_SIZE / 2.0

# Max distance (tiles, between the two wrecks themselves) for find_wreck_pair_target to treat two
# active wrecks as a pair worth positioning between. Shipwrecks spawn in close pairs at each
# hotspot (wiki-confirmed: "a different pair of shipwrecks", "double Salvage spots" -- see
# data/raw/S/Shipwreck salvaging.txt), and the hook does NOT auto-continue onto a different active
# wreck once its own sinks (CONFIRMED live 2026-08-01) -- so pre-positioning at the midpoint of a
# pair means a re-click after the first sinks can pick up the second immediately, no second sail
# trip needed. Derived from WRECK_SQUARE_SIZE: a boat sitting at the midpoint of two wrecks is
# WRECK_SQUARE_SIZE/2 from each wreck once their centers are WRECK_SQUARE_SIZE tiles apart --
# exactly the shared edge of both squares -- so WRECK_SQUARE_SIZE itself is the right geometric
# bound. (History, see the design spec's 2026-08-03/04 updates: two earlier values -- 30 from
# doubling WRECK_SQUARE_SIZE, and 16 from doubling the old maxDistance=8 -- were both wrong for the
# same reason, mixing up a diameter with a radius.)
PAIR_MAX_TILES = WRECK_SQUARE_SIZE


def find_nearby_wreck(config: dict, max_dist: float):
    """Nearest ACTIVE, actually-salvageable-at-this-level object matching config["wreckNames"]
    within max_dist tiles of the BOAT (not the player), or None (sunk wrecks -- see
    SUNK_SHIPWRECK_IDS -- and wrecks above the player's boosted Sailing level -- see
    SALVAGE_LEVEL_REQ -- are both filtered out; matching either would let the routine treat a wreck
    it can't actually work as "in range" and never look for a real one).

    Only used for the farther "is this wreck worth sailing to" check (maxSailDistance) now -- the
    near/hook-range case uses find_wreck_in_hook_range's WRECK_SQUARE_SIZE geometry instead (see
    its own docstring for why a flat max_dist guess didn't hold up for that purpose).

    Measures from /player's boatX/boatY (falling back to the player's own x/y if not aboard a
    WorldEntity) rather than trusting ObjectProvider's own player-relative "distance" field.
    CONFIRMED live 2026-08-02: a Sailing boat's own WorldView is only ~8 tiles per side, and
    standing at one end can be several tiles from the opposite end -- this is a range check against
    something the *boat* needs to reach, not specifically wherever the player happens to be
    standing on deck, so the player-relative distance could read "out of range" (or "in range")
    wrongly by that same margin.

    Pure range test otherwise -- the wreck itself is never clicked (only the boat's own salvaging
    hook is), so on-screen-projectability doesn't matter here, only world position. ObjectProvider
    always sets x/y from the object's WorldPoint regardless of whether it's currently on-screen.
    Chebyshev distance (max(|dx|, |dy|)), matching WorldPoint.distanceTo2D's own convention on the
    Java side.

    Passes max_dist (plus padding, see WRECK_SEARCH_RADIUS_PADDING) as /objects' radius --
    CONFIRMED live 2026-08-02: an unbounded full-scene walk building screen-projection data for
    every object in the loaded scene, every game tick, was a real source of client stutter. This
    routine only ever cares about objects within max_dist anyway, so bounding the scan costs
    nothing here."""
    names = {n.lower() for n in config.get("wreckNames", [])}
    sailing_level = _boosted_sailing_level()

    player = controller.get_player()
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    if origin_x is None or origin_y is None:
        return None

    best = None
    best_dist = None
    for o in controller.get_objects(radius=int(max_dist) + WRECK_SEARCH_RADIUS_PADDING):
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        if not _wreck_is_salvageable(o, sailing_level):
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


def find_wreck_pair_target(config: dict, wreck: dict):
    """Given a wreck the routine is about to sail to, look for a second matching active wreck
    within PAIR_MAX_TILES of it (same tier names as wreck, same plane, not sunk, and actually
    within the player's current Sailing level -- see SALVAGE_LEVEL_REQ) and target their midpoint
    instead of the lone wreck -- see PAIR_MAX_TILES's comment for why. Falls back to the given
    wreck's own position if no partner is found nearby (confirmed with the user: a lone wreck is
    still worth sailing to directly, pairing is an optimization, not a requirement).

    The level check matters specifically here, not just as a general filter -- CONFIRMED live
    2026-08-04 (user report): aiming for the midpoint is only reasonable when BOTH wrecks are
    actually salvageable; a nearby wreck that merely matches wreckNames by name but is above the
    player's level isn't really "active" for them (same distinction the sideloaded Sailing plugin's
    own highlight overlay draws), and pairing toward it skews the target for no benefit -- the
    player can't engage that wreck's hook once there anyway.

    Returns (target, partner): target is a plain {"x", "y", "plane"} dict, the same shape
    sailing.sail_to already expects (not a raw ObjectProvider entry, since a midpoint isn't a real
    game object). partner is the raw partner object if one was found, else None -- callers need it
    (not just target) to verify the boat actually landed within range of BOTH wrecks after
    sailing, since target alone can't distinguish "real wreck, brake to avoid ramming it" from "a
    midpoint over open water, no collision risk"."""
    names = {n.lower() for n in config.get("wreckNames", [])}
    sailing_level = _boosted_sailing_level()
    wx, wy, wplane = wreck.get("x"), wreck.get("y"), wreck.get("plane")

    # get_objects' radius is centered on the PLAYER, not on wreck (see controller.get_objects) --
    # BUG confirmed live 2026-08-03 (user report: "it only aims for one wreck instead of the
    # center of the two"): passing PAIR_MAX_TILES alone left the search box too small whenever
    # wreck itself was still far from the player (the common case -- this runs against far_wreck,
    # up to maxSailDistance away, on the routine's initial approach), so a real partner near wreck
    # sat outside the box and was silently never found. Widen the box by however far the player
    # already is from wreck, so it reaches PAIR_MAX_TILES past wreck regardless of where the
    # player currently stands.
    player = controller.get_player()
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    dist_to_wreck = 0
    if origin_x is not None and origin_y is not None:
        dist_to_wreck = max(abs(wx - origin_x), abs(wy - origin_y))

    best_partner = None
    best_dist = None
    for o in controller.get_objects(radius=int(dist_to_wreck + PAIR_MAX_TILES) + WRECK_SEARCH_RADIUS_PADDING):
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        if not _wreck_is_salvageable(o, sailing_level):
            continue
        if o.get("plane") != wplane:
            continue
        ox, oy = o.get("x"), o.get("y")
        if ox is None or oy is None:
            continue
        if ox == wx and oy == wy:
            # This is the wreck itself, not a partner.
            continue

        dist = max(abs(ox - wx), abs(oy - wy))
        if dist > PAIR_MAX_TILES:
            continue
        if best_dist is None or dist < best_dist:
            best_partner, best_dist = o, dist

    if best_partner is None:
        return {"x": wx, "y": wy, "plane": wplane}, None

    print(f"[salvage] found a wreck pair {best_dist} tiles apart -- sailing to the midpoint")
    target = {
        "x": (wx + best_partner["x"]) // 2,
        "y": (wy + best_partner["y"]) // 2,
        "plane": wplane,
    }
    return target, best_partner


def _boat_hull_rect(player: dict):
    """Boat's true-tile hull footprint as (min_x, max_x, min_y, max_y) -- CONFIRMED live
    2026-08-04 (user report) against the sideloaded Sailing plugin's own TrueTileIndicator overlay
    (data/decompiled/sailing-plugin's TrueTileIndicator.renderBoatArea), which PlayerProvider's
    boatWidth/boatHeight fields were already modeled on (RuneLite's own WorldEntityConfig bounds,
    not a guessed size). None if the boat's position/bounds aren't available (not aboard a
    WorldEntity)."""
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    if origin_x is None or origin_y is None:
        return None
    half_w = player.get("boatWidth", 0) / 2.0
    half_h = player.get("boatHeight", 0) / 2.0
    return origin_x - half_w, origin_x + half_w, origin_y - half_h, origin_y + half_h


def _wreck_in_hook_range(player: dict, wreck: dict) -> bool:
    """Whether the boat's true-tile hull footprint (_boat_hull_rect) overlaps wreck's own
    WRECK_SQUARE_SIZE square -- i.e. whether the hook could actually engage it from here. Replaces
    the old single-point maxDistance guess entirely (CONFIRMED live 2026-08-04, see
    WRECK_SQUARE_SIZE's own comment) with a real rectangle/square overlap test: two axis-aligned
    boxes overlap exactly when they're within reach of each other on BOTH axes at once."""
    hull = _boat_hull_rect(player)
    wx, wy = wreck.get("x"), wreck.get("y")
    if hull is None or wx is None or wy is None:
        return False
    boat_min_x, boat_max_x, boat_min_y, boat_max_y = hull
    return (
        boat_min_x <= wx + WRECK_SQUARE_HALF and boat_max_x >= wx - WRECK_SQUARE_HALF
        and boat_min_y <= wy + WRECK_SQUARE_HALF and boat_max_y >= wy - WRECK_SQUARE_HALF
    )


def find_wreck_in_hook_range(config: dict):
    """Nearest ACTIVE, actually-salvageable-at-this-level object matching config["wreckNames"]
    whose WRECK_SQUARE_SIZE square overlaps the boat's own true-tile hull footprint
    (_wreck_in_hook_range) -- i.e. whether the hook could engage it without moving. This is the
    near-wreck counterpart to find_nearby_wreck's farther maxSailDistance search; see
    find_nearby_wreck's docstring (including for the SALVAGE_LEVEL_REQ filter)."""
    names = {n.lower() for n in config.get("wreckNames", [])}
    sailing_level = _boosted_sailing_level()

    player = controller.get_player()
    hull = _boat_hull_rect(player)
    if hull is None:
        return None
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))

    # Bound /objects' radius-limited scene walk the same way find_nearby_wreck does (see its own
    # comment) -- half the wreck square plus half the boat's own footprint on each axis covers the
    # farthest a wreck could be from the boat's reference point and still overlap, plus the usual
    # padding for the player-vs-boat offset.
    search_radius = int(WRECK_SQUARE_HALF + player.get("boatWidth", 0) + player.get("boatHeight", 0)) + WRECK_SEARCH_RADIUS_PADDING

    best = None
    best_dist = None
    for o in controller.get_objects(radius=search_radius):
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        if not _wreck_is_salvageable(o, sailing_level):
            continue
        if not _wreck_in_hook_range(player, o):
            continue
        wx, wy = o.get("x"), o.get("y")
        dist = max(abs(wx - origin_x), abs(wy - origin_y))
        if best_dist is None or dist < best_dist:
            best, best_dist = o, dist

    return best


def sail_to_pair(ctx, config: dict, wreck: dict) -> bool:
    """Sail to wreck (or, if a nearby partner exists, the pair midpoint -- see
    find_wreck_pair_target) and verify the boat actually ends up within the hook's real
    WRECK_SQUARE_SIZE range of BOTH wrecks, instead of just trusting sail_to's own arrival
    tolerance blind. CONFIRMED live 2026-08-03 (user report): without this check, sail_to's
    brake-then-drift landing (see sail_to's own brake= docstring) could miss a computed pair
    midpoint by a wide enough margin to land outside hook range of one or both wrecks, silently
    defeating the whole point of pairing.

    Checks against _wreck_in_hook_range specifically, not some looser notion of "nearby" --
    CONFIRMED live 2026-08-04 (user report): an earlier version of this check used a flat
    maxDistance figure that didn't match the real engage boundary, so it could report "landed
    fine" even when the hook genuinely couldn't reach either wreck from there.

    brake=False (no ram risk) only when a real partner was found -- a midpoint is open water, but
    the single-wreck fallback is a real solid object and still needs the wide brake tolerance."""
    target, partner = find_wreck_pair_target(config, wreck)
    ok = sailing.sail_to(ctx, target, brake=(partner is None))

    if ok and partner is not None:
        player = controller.get_player()
        if not (_wreck_in_hook_range(player, wreck) and _wreck_in_hook_range(player, partner)):
            print("[salvage] landed outside hook range of one of the paired wrecks -- correcting")
            ok = sailing.sail_to(ctx, target, brake=False)

    return ok
