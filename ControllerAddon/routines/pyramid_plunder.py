# routines/pyramid_plunder.py
#
# Pyramid Plunder loot-only routine: Grand Gold Chest + Sarcophagus only (never the 13 urns
# per room), sarcophagi skipped in rooms 1-3, looted rooms 4-6, run deliberately abandoned via
# Quick-leave after room 6 and restarted from room 1 rather than pushed to rooms 7-8. All loot
# is dropped on the spot except the Pharaoh's sceptre -- no banking loop in this routine at all.
#
# See docs/superpowers/specs/2026-08-16-pyramid-plunder-design.md for the full design and its
# "NEEDS LIVE VERIFICATION" list -- several mechanics below (outer-door wrong-entry recovery,
# tomb-door click sequence, spear trap necessity, sarcophagus opening timing) are first-cut
# assumptions pending live testing, flagged inline same as this codebase's existing
# blast_furnace.py/golem_crafting.py "ASSUMPTION, NOT YET VERIFIED LIVE" convention.
#
# Phase dispatch is re-evaluated fresh every tick from live bridge state (mirrors
# pest_control.py's region-based tick()) -- no phase is ever assumed, so wherever a
# Quick-leave/timeout/sceptre-eject actually drops the player, the very next tick's read of
# /pyramid-plunder + /player + /npcs picks the right phase on its own.
#
# Run with:
#     python controller.py routines/pyramid_plunder.py

import math

import controller
import debug_log

PYRAMID_REGION_ID = 7749

SPEAR_TRAP_ID = 21280
GRAND_GOLD_CHEST_CLOSED_ID = 20946
GRAND_GOLD_CHEST_OPENED_ID = 26616
SARCOPHAGUS_CLOSED_ID = 21255
SARCOPHAGUS_OPENED_ID = 26626
TOMB_DOOR_CLOSED_ID = 20948
TOMB_DOOR_WALL_IDS = {26618, 26619, 26620, 26621}
EXIT_DOOR_ID = 20931

GUARDIAN_MUMMY_NAME = "guardian mummy"
ANONYMOUS_DOOR_NAME = "anonymous looking door"

ROOM_STEP_TRAP = "trap"
ROOM_STEP_CHEST = "chest"
ROOM_STEP_SARCO = "sarco"
ROOM_STEP_DOOR = "door"

DEFAULT_CONFIG = {
    "enabled": True,
    "lastRoomToLoot": 6,          # loot chest+sarco through this room, then quick-leave and restart
    "sarcophagusStartRoom": 4,    # skip sarco in rooms before this
    "artefactNames": [            # fixed drop-list, lowercase-matched -- never a substring match,
        "ivory comb", "pottery scarab", "pottery statuette", "stone seal",   # so food/lockpick/the
        "stone scarab", "stone statuette", "gold seal", "golden scarab",    # sceptre are never at
        "golden statuette",                                                 # risk of being dropped
    ],
    "food": {"enabled": True, "eatAt": 5},   # same shape as slayer.py's food_cfg
    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}


def get_config(bridge_config):
    # No Controller Settings panel for this (same as pest_control.py/mining.py) -- this routine
    # never reads GET /config, just use DEFAULT_CONFIG.
    return DEFAULT_CONFIG


def find_object_by_id(objects: list, object_id: int):
    candidates = [o for o in objects if o.get("id") == object_id and o.get("visible", True)]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def find_objects_by_name(objects: list, name_substring: str) -> list:
    name_substring = name_substring.lower()
    return [o for o in objects
            if name_substring in (o.get("name") or "").lower() and o.get("visible", True)]


def find_npc_by_name(npcs: list, name: str):
    name = name.lower()
    candidates = [n for n in npcs if (n.get("name") or "").lower() == name]
    if not candidates:
        return None
    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


def order_doors_clockwise(doors: list) -> list:
    """Stable ordering of the 4 static anonymous-door objects by angle around their own
    centroid, so the same physical door always resolves to the same index (0-3) across every
    call this session -- lets the OUTSIDE phase cache "correct_door_index" and reuse it
    directly on future entries instead of re-deriving it from scratch every restart. Pure
    function (no bridge calls) -- doesn't need a live client to reason about."""
    cx = sum(d.get("x", 0) for d in doors) / len(doors)
    cy = sum(d.get("y", 0) for d in doors) / len(doors)
    return sorted(doors, key=lambda d: math.atan2(d.get("y", 0) - cy, d.get("x", 0) - cx))


def maybe_eat_food(ctx, config) -> None:
    """hp <= eatAt -> eat, same inline pattern as slayer.py's food handling. Independent of
    phase -- called first thing in tick(), every tick, regardless of OUTSIDE/LOBBY/IN_ROOM."""
    food_cfg = config.get("food", {})
    if not food_cfg.get("enabled"):
        return
    hp = controller.get_player().get("hp")
    if hp is not None and hp <= food_cfg.get("eatAt", 0):
        if controller.consume_first(ctx.ser, "food"):
            debug_log.log("pyramid_plunder", f"ate food (hp={hp})")


def outside_tick(ctx, config) -> None:
    """Not near the Guardian mummy yet -- find and enter the correct one of the 4 static
    anonymous-door objects. Caches the winning index in ctx.state.data["correct_door_index"]
    (see order_doors_clockwise) so almost every entry after the first is a single direct
    click, not a fresh 4-door search -- expected to keep working for the life of the ~25-minute
    door rotation.

    NEEDS LIVE VERIFICATION: the exact signal that a door was wrong (led to an "abandoned
    room" rather than the Guardian mummy's lobby) and how to leave that room back outside.
    Best-effort assumption implemented below: re-click the same door we just walked through
    (anonymous doors are described as ordinary bidirectional entry points in the wiki) to step
    back outside, then try the next door index next tick. Correct if wrong, per this
    codebase's "ASSUMPTION, NOT YET VERIFIED LIVE" convention (see blast_furnace.py)."""
    doors = find_objects_by_name(controller.get_objects(radius=30), ANONYMOUS_DOOR_NAME)
    if len(doors) < 4:
        debug_log.log("pyramid_plunder", f"only found {len(doors)} anonymous doors -- waiting")
        return
    ordered = order_doors_clockwise(doors)

    cached_index = ctx.state.data.get("correct_door_index")
    attempt_index = ctx.state.data.get("door_attempt_index", 0) % len(ordered)
    index_to_try = cached_index if cached_index is not None else attempt_index
    door = ordered[index_to_try]

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if not controller.click_entity(ctx.ser, door, check_ui_blockers=False):
        return
    # Let the search-the-door attempt (and possible 1-4 dmg stun on failure) resolve before
    # checking whether it worked.
    controller.human_delay(1.5, 3.0)

    mummy = find_npc_by_name(controller.get_npcs(), GUARDIAN_MUMMY_NAME)
    if mummy is not None:
        debug_log.log("pyramid_plunder", f"reached the Guardian mummy via door index {index_to_try}")
        ctx.state.data["correct_door_index"] = index_to_try
        return

    debug_log.log("pyramid_plunder", f"door index {index_to_try} didn't reach the mummy -- "
                                      f"stepping back through it and trying the next one")
    controller.wait_until_player_idle(timeout_s=5.0)
    controller.click_entity(ctx.ser, door, check_ui_blockers=False)
    controller.human_delay(1.0, 2.0)

    if cached_index is not None:
        ctx.state.data["correct_door_index"] = None
    else:
        ctx.state.data["door_attempt_index"] = attempt_index + 1


def lobby_tick(ctx, config, mummy: dict) -> None:
    """Guardian mummy reachable, no run active -- right-click Start-minigame."""
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if controller.click_menu_action(ctx.ser, mummy, "Start-minigame", check_ui_blockers=False):
        debug_log.log("pyramid_plunder", "started a new run")
        controller.human_delay(0.8, 1.5)


def setup(ctx):
    ctx.state.data.setdefault("room_step", ROOM_STEP_TRAP)


def tick(ctx):
    config = ctx.state.config
    maybe_eat_food(ctx, config)

    pp = controller.get_pyramid_plunder()
    if pp.get("inRun"):
        in_room_tick(ctx, config, pp)
        return

    mummy = find_npc_by_name(controller.get_npcs(), GUARDIAN_MUMMY_NAME)
    if mummy is not None:
        lobby_tick(ctx, config, mummy)
    else:
        outside_tick(ctx, config)


def in_room_tick(ctx, config, pp: dict) -> None:
    step = ctx.state.data.get("room_step", ROOM_STEP_TRAP)

    if step == ROOM_STEP_TRAP:
        trap_step(ctx, config, pp)
    elif step == ROOM_STEP_CHEST:
        chest_step(ctx, config, pp)
    elif step == ROOM_STEP_SARCO:
        sarco_step(ctx, config, pp)
    elif step == ROOM_STEP_DOOR:
        door_step(ctx, config, pp)

    drop_loot(ctx, config)


def trap_step(ctx, config, pp: dict) -> None:
    """Disarm the spear trap if the bridge reports it still armed. A failed attempt is
    confirmed via the chat message "You fail to deactivate the trap." (user-confirmed, see
    Global Constraints) -- logged for visibility, but the actual retry is driven by re-polling
    pp["trapActive"] next tick either way (stays on "trap" until the bridge confirms it's
    actually disarmed), so a missed/late chat message can't cause a stuck loop.

    NEEDS LIVE VERIFICATION: whether this is actually mandatory to reach the chest/
    sarcophagus/doors at all, or just an optional bonus-XP obstacle -- see design doc. Either
    way, disarming it first is a safe default (10 xp, minor 1-4 dmg risk on a failed attempt)."""
    if not pp.get("trapActive"):
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        return

    trap = find_object_by_id(controller.get_objects(radius=15), SPEAR_TRAP_ID)
    if trap is None:
        debug_log.log("pyramid_plunder", "trapActive but no spear trap object visible -- skipping")
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        return

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    since_tick = controller.get_last_chat_message().get("tick")
    controller.click_entity(ctx.ser, trap, check_ui_blockers=False)
    controller.human_delay(0.6, 1.2)

    failed = controller.wait_for_chat_message(
        lambda m: "fail to deactivate the trap" in (m.get("message") or "").lower(),
        since_tick=since_tick, timeout_s=2.0,
    )
    if failed is not None:
        debug_log.log("pyramid_plunder", "failed to disarm the spear trap -- retrying")
    # Re-polled next tick via pp["trapActive"] either way -- stays on "trap" until the bridge
    # confirms it's actually disarmed, so a failed attempt (logged above or not) naturally
    # retries instead of advancing early.


def chest_step(ctx, config, pp: dict) -> None:
    """Grand Gold Chest always opens instantly on a single search (per the wiki -- no
    fail-and-retry loop needed here, unlike the trap/sarcophagus/doors)."""
    next_step = ROOM_STEP_SARCO if pp.get("roomNumber", 0) >= config["sarcophagusStartRoom"] else ROOM_STEP_DOOR

    chest = find_object_by_id(controller.get_objects(radius=15), GRAND_GOLD_CHEST_CLOSED_ID)
    if chest is None:
        # Already searched (now shows the opened id) or not resolved this poll -- either way
        # there's nothing left to click here.
        ctx.state.data["room_step"] = next_step
        return

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    controller.click_entity(ctx.ser, chest, check_ui_blockers=False)
    controller.human_delay(0.6, 1.5)
    ctx.state.data["room_step"] = next_step


def sarco_step(ctx, config, pp: dict) -> None:
    """Two-stage: click "Open" on the closed sarcophagus, wait for it to become searchable
    (id flips from SARCOPHAGUS_CLOSED_ID to SARCOPHAGUS_OPENED_ID), then click it again to
    Search. No mummy-specific handling beyond the routine's normal food-eat safety net --
    per the design doc, prayer management was explicitly rejected in favor of that.

    NEEDS LIVE VERIFICATION: how long the opening animation actually takes before the id flip
    happens -- the 2.0-4.0s wait below is a first-cut guess, not a measured value."""
    objs = controller.get_objects(radius=15)
    opened = find_object_by_id(objs, SARCOPHAGUS_OPENED_ID)
    if opened is not None:
        if not controller.wait_until_player_idle(timeout_s=5.0):
            return
        controller.click_entity(ctx.ser, opened, check_ui_blockers=False)
        controller.human_delay(1.0, 2.0)
        ctx.state.data["room_step"] = ROOM_STEP_DOOR
        return

    closed = find_object_by_id(objs, SARCOPHAGUS_CLOSED_ID)
    if closed is not None:
        if not controller.wait_until_player_idle(timeout_s=5.0):
            return
        controller.click_entity(ctx.ser, closed, check_ui_blockers=False)
        controller.human_delay(2.0, 4.0)
        return  # re-poll next tick; stays on "sarco" until `opened` is found above

    # Neither id present -- already looted (or genuinely missing this poll). Move on either way
    # rather than getting stuck waiting on something that isn't there.
    ctx.state.data["room_step"] = ROOM_STEP_DOOR


def quick_leave(ctx, config) -> None:
    """Room 6 (or configured lastRoomToLoot) is fully looted -- end the run via the in-room
    exit door's Quick-leave option rather than walking back out through 6 tomb doors. The next
    tick's phase dispatch (OUTSIDE vs LOBBY) picks up wherever the game actually drops the
    player, so no explicit "where did we land" handling is needed here."""
    exit_door = find_object_by_id(controller.get_objects(radius=15), EXIT_DOOR_ID)
    if exit_door is None:
        debug_log.log("pyramid_plunder", "no exit door found in the final room -- waiting")
        return
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if controller.click_menu_action(ctx.ser, exit_door, "Quick-leave", check_ui_blockers=False):
        debug_log.log("pyramid_plunder", f"quick-left room {config['lastRoomToLoot']} -- run complete, restarting")
        ctx.state.data["room_step"] = ROOM_STEP_TRAP
        ctx.state.data["door_attempt"] = 0


def door_step(ctx, config, pp: dict) -> None:
    """Rooms before lastRoomToLoot: find the correct one of the 4 Tomb Doors. Rooms at or past
    lastRoomToLoot: Quick-leave instead (this room's looting is already done by the time
    in_room_tick reaches "door").

    A lockpick in inventory is assumed to passively boost the pick-lock success chance (per the
    wiki: "Having a lockpick will increase the chance") rather than needing a separate
    use-item-on-door click -- NEEDS LIVE VERIFICATION: if that assumption is wrong and an
    explicit "use lockpick on door" step is actually required, this is the function to add it
    to; not dropping the lockpick (it's absent from artefactNames) means it stays available
    either way.

    Outcome detection: wait_for_chat_message for the confirmed dead-end text (Global
    Constraints) means a wrong door; roomNumber incrementing with no dead-end message means the
    right one. Capped implicitly at 4 attempts per room by cycling through the 4 physical wall
    objects (26618-26621) -- the game's own "3 wrong -> 4th always works" rule bounds the
    worst case.

    NEEDS LIVE VERIFICATION: whether Pick-lock and Enter are actually one click or two -- this
    implementation assumes one click resolves both (closed tomb doors have a single default
    action per the wiki infobox)."""
    room = pp.get("roomNumber", 0)
    if room >= config["lastRoomToLoot"]:
        quick_leave(ctx, config)
        return

    objs = controller.get_objects(radius=15)
    doors = [o for o in objs
             if o.get("id") in TOMB_DOOR_WALL_IDS and o.get("impostorId") == TOMB_DOOR_CLOSED_ID
             and o.get("visible", True)]
    if not doors:
        debug_log.log("pyramid_plunder", "no closed tomb doors found -- waiting")
        return
    doors.sort(key=lambda o: o.get("distance", 9999))

    attempt = ctx.state.data.get("door_attempt", 0)
    door = doors[attempt % len(doors)]

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return

    since_tick = controller.get_last_chat_message().get("tick")
    if not controller.click_entity(ctx.ser, door, check_ui_blockers=False):
        return
    controller.human_delay(0.8, 1.6)

    dead_end = controller.wait_for_chat_message(
        lambda m: "leads to a dead end" in (m.get("message") or "").lower(),
        since_tick=since_tick, timeout_s=3.0,
    )
    if dead_end is not None:
        debug_log.log("pyramid_plunder", f"door attempt {attempt} is a dead end -- trying the next one")
        ctx.state.data["door_attempt"] = attempt + 1
        return

    fresh_pp = controller.get_pyramid_plunder()
    if fresh_pp.get("roomNumber", room) > room:
        debug_log.log("pyramid_plunder", f"advanced to room {fresh_pp.get('roomNumber')}")
        ctx.state.data["room_step"] = ROOM_STEP_TRAP
        ctx.state.data["door_attempt"] = 0
        return

    # Neither signal fired yet (still picking the lock) -- stay on this door and retry next tick.


def drop_loot(ctx, config) -> None:
    """Drop every inventory item whose name is in the fixed artefactNames list -- never a
    substring match, so food/lockpick are never at risk. One drop per tick (not a burst) --
    next tick re-scans and picks up whatever's left, same pacing idiom as this codebase's
    other per-tick inventory actions. Left-click-to-drop resolves via controller.py's
    click_menu_action underneath click_entity's own UI-blocker-free inventory path, same as
    shipwreck_salvaging.py's drop_salvage.

    Finding the Pharaoh's sceptre is logged, never dropped (it's deliberately absent from
    artefactNames) -- the game's own auto-eject on finding it is what actually ends the run;
    this routine does nothing special beyond logging it."""
    names = {n.lower() for n in config.get("artefactNames", [])}
    for item in controller.get_inventory():
        item_name = (item.get("name") or "").lower()
        if item_name in names:
            controller.click_entity(ctx.ser, item, check_ui_blockers=False)
            controller.human_delay(0.2, 0.5)
            return
        if item_name == "pharaoh's sceptre" and not ctx.state.data.get("sceptre_logged"):
            debug_log.log("pyramid_plunder", "PHARAOH'S SCEPTRE FOUND -- letting the game's own auto-eject take over")
            ctx.state.data["sceptre_logged"] = True
