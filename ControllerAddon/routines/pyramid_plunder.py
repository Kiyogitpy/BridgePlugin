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
    # Filled in by Task 3.
    pass
