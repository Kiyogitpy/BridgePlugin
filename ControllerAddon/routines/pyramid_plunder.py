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
import time

import controller
import debug_log

PYRAMID_REGION_ID = 7749

SPEAR_TRAP_ID = 21280

# BASE ids vs IMPOSTOR ids -- this distinction is load-bearing for every stateful object below,
# and getting it backwards silently breaks the whole routine (it did, in this file's first
# draft). ObjectProvider.java puts GameObject.getId() -- the multiloc BASE id, which never
# changes as the object is opened/searched/picked -- in each object's "id" field, and the
# varbit-resolved state variant in a SEPARATE "impostorId" field. So: match on "id" to find the
# object at all, then branch on "impostorId" to know what state it's in.
#
# Base ids 26616 (chest) / 26626 (sarcophagus) / 26618-26621 (the 4 tomb-door wall objects) and
# closed impostors 20946 / 21255 / 20948 are lifted straight from the official RuneLite plugin
# (data/decompiled/client-1.12.33/net/runelite/client/plugins/pyramidplunder/), whose overlay
# does exactly this two-step (`26616 == object.getId()` then
# `20946 != getObjectDefinition(object.getId()).getImpostor().getId()`). The remaining
# impostor ids come from the wiki infoboxes' id1/id2/id3 version lists in data/raw/.
GRAND_GOLD_CHEST_ID = 26616                     # base object id
GRAND_GOLD_CHEST_CLOSED_IMPOSTOR_ID = 20946     # version "Closed", option "Search"
GRAND_GOLD_CHEST_SEARCHED_IMPOSTOR_ID = 20947   # version "Opened", no options -- already looted

SARCOPHAGUS_ID = 26626                          # base object id
SARCOPHAGUS_CLOSED_IMPOSTOR_ID = 21255          # version "Closed", option "Open"
SARCOPHAGUS_OPENING_IMPOSTOR_ID = 21257         # version "Opening", NO options -- mid-animation
SARCOPHAGUS_OPENED_IMPOSTOR_ID = 21256          # version "Opened", option "Search"

TOMB_DOOR_WALL_IDS = {26618, 26619, 26620, 26621}   # base ids of the 4 physical wall objects
TOMB_DOOR_CLOSED_ID = 20948                     # impostor, option "Pick-lock"
TOMB_DOOR_OPENED_ID = 20949                     # impostor, option "Enter"

# NEEDS LIVE VERIFICATION: unlike everything above, this id has no decompiled-plugin
# cross-check -- it's the wiki infobox's id1 for "Tomb Door (exit)" only, and that infobox lists
# a single "In game" version carrying BOTH "Leave Tomb" and "Quick-leave" (id2=20932 is the
# lobby-side door), so it may be a plain single-state object where base id == impostor id, or it
# may be an impostor id like every other id1 on this page. quick_leave() therefore matches it
# against BOTH "id" and "impostorId" and falls back to a name lookup if neither hits.
EXIT_DOOR_ID = 20931
EXIT_DOOR_NAME = "tomb door"

# CONFIRMED live 2026-08-17: a wrong outer anonymous door places the player in an "abandoned
# room" that shares Pyramid Plunder's own region (7749) but reports roomNumber=0/inRun=false via
# /pyramid-plunder -- i.e. it looks like a real room to a naive region check, but isn't one. No
# Guardian mummy is present. The room contains a "Tomb Door" object at id=20932 (the wiki
# infobox's "Lobby" variant, option "Leave Tomb" only, no "Quick-leave") right next to the
# player's landing tile -- clicking it is the actual escape. NOT matched by EXIT_DOOR_NAME's
# generic "tomb door" substring search alone in step_back_outside, because that name collides
# with the room-progression Tomb Doors (20948/20949) once back in a real room; matched by id
# here specifically to stay unambiguous.
ABANDONED_ROOM_EXIT_ID = 20932

GUARDIAN_MUMMY_NAME = "guardian mummy"
ANONYMOUS_DOOR_NAME = "anonymous looking door"

# GET /npcs returns every NPC in the loaded scene with no radius or line-of-sight filter, and
# the mummy's lobby sits only ~2-14 tiles from the outer anonymous doors -- close enough that
# it's in the list (through a wall) while the player is still standing outside. Without this
# bound the LOBBY branch wins from the very first tick and the routine can never actually enter
# the pyramid. The design spec's wording is "mummy reachable", not "mummy present".
#
# Kept deliberately tight; tick() backs it up with an unbounded match for the case where the 4
# outer anonymous doors are NOT visible (i.e. we can't possibly be standing outside), so an
# over-tight bound degrades to "takes an extra tick to notice the lobby" rather than
# deadlocking outside_tick against a lobby it can't act on.
MUMMY_MAX_DISTANCE = 5

# An outer anonymous-door entry attempt can fail its Thieving roll (stun + 1-4 dmg) on the
# CORRECT door too, so one failure is not evidence the cached door is wrong. Only invalidate the
# cache after this many CONSECUTIVE failures on the same cached index.
OUTSIDE_DOOR_FAIL_TOLERANCE = 2

# CONFIRMED live 2026-08-17: entering an already-opened (impostorId TOMB_DOOR_OPENED_ID) tomb
# door that turns out to be a dead end produces NO chat message at all -- unlike the initial
# Pick-lock attempt on a still-closed door, which does say "leads to a dead end" -- and of
# course no room advance either. Silent on both signals door_step otherwise relies on, so an
# already-open wrong door would be re-Entered forever without this fallback. Small on purpose:
# entering an unlocked door has no skill-roll retry mechanic (unlike Pick-lock), so a handful of
# identical silent attempts is already conclusive, not a normal gameplay variance to wait out.
DOOR_ENTER_ATTEMPT_LIMIT = 3

# CONFIRMED live 2026-08-17: click_entity's default aim offset (-7, +7) -- tuned for wide
# floor-standing GameObjects like the chest/sarcophagus/spear trap, which all clicked fine with
# it -- systematically missed a distant (15-tile) Tomb Door WallObject every single time,
# resolving to a plain "Walk here" instead of the door's own action (confirmed via
# GET /last-action: {"menuAction": "WALK", "option": "Walk here"}), even though the object's own
# reported screenX/screenY were otherwise accurate. WallObjects render as a thin strip, not a
# wide clickable area, and don't tolerate a fixed diagonal pixel bias the way a chest does --
# aim dead-center instead for every object in the Tomb Door family (room-progression doors and
# both exit-door variants).
TOMB_DOOR_AIM_OFFSET = (0, 0)

# CONFIRMED live 2026-08-17 (user directive): local units, RuneLite's own LOCAL_TILE_SIZE=128
# per tile -- roughly 3 tiles' worth of instant displacement. A room-transfer teleport should
# clear this in a single ~0.1s poll; ordinary walking, even at run speed, moves at most a
# fraction of that per render frame (see attempt_door_click's own note on why this beats waiting
# for the tick-capped roomNumber varbit). NEEDS LIVE VERIFICATION: picked to comfortably clear
# normal per-frame walk deltas, not measured against an actual transfer's real jump distance.
POSITION_JUMP_THRESHOLD = 380

# NEEDS LIVE VERIFICATION: even with TOMB_DOOR_AIM_OFFSET, a Tomb Door candidate at long range
# (observed failing at 14-15 tiles, across the room) still resolved every click to a plain
# "Walk here" (GET /last-action: menuAction=WALK) instead of the door's own action. A shift-click
# force-walk toward the door's own (x, y) was tried as a fix and reverted (2026-08-17): the
# player never actually moved across ~20 consecutive attempts, most likely because a wall
# object's own tile isn't a valid walk destination the way an open floor tile is -- OSRS's
# "walk near an unwalkable tile" fallback behavior needs to be understood before a real fix can
# be written here, not guessed at again blind. Left unhandled for now: the existing
# NO_PROGRESS_MAX_TICKS safety-stop is the backstop for this case (the routine restarts and the
# door layout may differ next time), same as any other genuinely open live-verification gap in
# this file.

# Hard safety-stop backstop (design spec section 4: "Hard safety-stop ... if no anonymous door /
# Guardian mummy / chest / tomb door can be found at all within a reasonable number of
# attempts"). One shared counter, incremented on any tick that resolved nothing and changed
# nothing, reset by note_progress() the moment anything real happens. Deliberately far larger
# than slayer.py's per-target ENGAGE_STUCK_MAX_TICKS=5 style counters -- this is a
# whole-routine last resort, not a per-action retry budget.
NO_PROGRESS_MAX_TICKS = 80

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
    # CONFIRMED live 2026-08-17: eatAt=5 (the original value) let a scarab-swarm-inflicted
    # poison drain the player from 81 hp down to 4 hp before the safety net ever triggered --
    # poison ticks on the game's own damage schedule, not gated behind this routine's tick loop,
    # so multiple ticks can land between two consecutive checks. 40 leaves real margin against
    # that without materially increasing food consumption (this routine still only eats when
    # actually needed, just meaningfully earlier).
    "food": {"enabled": True, "eatAt": 40},   # same shape as slayer.py's food_cfg
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
    """Nearest visible object whose BASE id (GET /objects' "id") matches. Callers that care
    about a multiloc object's current state must branch on the returned object's "impostorId"
    themselves -- see the base-id/impostor-id note at the top of this file."""
    candidates = [o for o in objects if o.get("id") == object_id and o.get("visible", True)]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def find_objects_by_name(objects: list, name_substring: str) -> list:
    """Matches are deduplicated by (x, y) tile -- CONFIRMED live 2026-08-17: the 4 anonymous
    doors each come back as 4 exactly-identical entries (same id/impostorId/screen position)
    within radius 30, presumably multiple wall-object layers on the same tile all resolving to
    the same display name. Without dedup, outside_tick's own `!= 4` sanity check ("found 16
    anonymous doors, expected 4") correctly refused to proceed rather than misordering
    order_doors_clockwise on the raw list -- but it also meant the routine could never actually
    enter the pyramid. One physical tile is one clickable target regardless of how many
    same-named object layers RuneLite reports there, so dedup is the correct fix, not a
    workaround."""
    name_substring = name_substring.lower()
    matches = [o for o in objects
               if name_substring in (o.get("name") or "").lower() and o.get("visible", True)]
    deduped = {}
    for o in matches:
        deduped.setdefault((o.get("x"), o.get("y")), o)
    return list(deduped.values())


def find_npc_by_name(npcs: list, name: str, max_distance: float = None):
    """Nearest NPC with this exact (case-insensitive) name. max_distance bounds the match to
    NPCs within that many tiles -- GET /npcs is scene-wide with no visibility/reachability
    filter, so an unbounded lookup happily "finds" an NPC through a wall (see
    MUMMY_MAX_DISTANCE). Default None keeps the original unbounded behavior."""
    name = name.lower()
    candidates = [n for n in npcs if (n.get("name") or "").lower() == name]
    if max_distance is not None:
        candidates = [n for n in candidates if n.get("distance", 9999) <= max_distance]
    if not candidates:
        return None
    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


def find_outer_doors():
    return find_objects_by_name(controller.get_objects(radius=30), ANONYMOUS_DOOR_NAME)


def resolve_mummy(npcs: list = None, doors: list = None):
    """The routine's single "are we in the mummy's lobby" test. Primary rule: the mummy within
    MUMMY_MAX_DISTANCE tiles (see that constant for why an unbounded /npcs match is wrong).
    Relaxation: if the bounded match misses AND the 4 outer anonymous doors are not visible, we
    demonstrably aren't standing outside, so an unbounded match is accepted -- that keeps an
    over-tight bound from stranding the routine in a lobby outside_tick can't act on. Returns
    the mummy dict, or None for "we're outside"."""
    if npcs is None:
        npcs = controller.get_npcs()
    mummy = find_npc_by_name(npcs, GUARDIAN_MUMMY_NAME, max_distance=MUMMY_MAX_DISTANCE)
    if mummy is not None:
        return mummy
    if doors is None:
        doors = find_outer_doors()
    if len(doors) != 4:
        return find_npc_by_name(npcs, GUARDIAN_MUMMY_NAME)
    return None


def wait_for_mummy(timeout_s: float = 8.0, poll_interval_s: float = 0.5):
    """Polls resolve_mummy() instead of a single fixed-delay-then-check -- user-flagged
    2026-08-17: a correct door was repeatedly misjudged as wrong, and this codebase has already
    hit the underlying cause once before (pest_control.py's own note on NPC-streaming delay --
    a newly-loaded area's NPCs, including the Guardian mummy, can take a few seconds to appear
    in GET /npcs at all after the scene transition). A single check after a fixed 1.5-3.0s
    delay could fire before the mummy had streamed in, reporting a correct door as a failure --
    which then discarded real progress (dropped the door cache / walked into recovery logic)
    for no reason. Returns as soon as the mummy is found, same early-exit shape as
    wait_until_player_idle/wait_for_chat_message elsewhere in this codebase."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        mummy = resolve_mummy()
        if mummy is not None:
            return mummy
        time.sleep(poll_interval_s)
    return None


def order_doors_clockwise(doors: list) -> list:
    """Stable ordering of the 4 static anonymous-door objects by angle around their own
    centroid, so the same physical door always resolves to the same index (0-3) across every
    call this session -- lets the OUTSIDE phase cache "correct_door_index" and reuse it
    directly on future entries instead of re-deriving it from scratch every restart. Pure
    function (no bridge calls) -- doesn't need a live client to reason about."""
    cx = sum(d.get("x", 0) for d in doors) / len(doors)
    cy = sum(d.get("y", 0) for d in doors) / len(doors)
    return sorted(doors, key=lambda d: math.atan2(d.get("y", 0) - cy, d.get("x", 0) - cx))


def note_progress(ctx) -> None:
    """Something real happened this tick -- a phase change, a step change, a room-number
    change, or simply resolving the object/NPC this step is waiting on. Resets the shared
    no-progress safety counter (see NO_PROGRESS_MAX_TICKS / check_no_progress)."""
    ctx.state.data["progress_this_tick"] = True
    ctx.state.data["no_progress_ticks"] = 0


def check_no_progress(ctx) -> None:
    """Called once at the end of every tick. Nothing resolved and nothing changed for
    NO_PROGRESS_MAX_TICKS consecutive ticks means the routine is genuinely lost (stuck in an
    abandoned room whose door won't resolve, standing somewhere with no pyramid in sight, ...),
    not mid-multi-tick-action -- hard-stop rather than clicking at nothing forever."""
    if ctx.state.data.pop("progress_this_tick", False):
        ctx.state.data["no_progress_ticks"] = 0
        return
    stalled = ctx.state.data.get("no_progress_ticks", 0) + 1
    ctx.state.data["no_progress_ticks"] = stalled
    if stalled >= NO_PROGRESS_MAX_TICKS:
        ctx.state.data["no_progress_ticks"] = 0
        ctx.state.trigger_safety_stop(
            f"Pyramid Plunder: no progress for {stalled} ticks -- couldn't resolve an anonymous "
            f"door, the Guardian mummy, the chest, the sarcophagus or a tomb door")


def note_phase(ctx, phase: str) -> None:
    if ctx.state.data.get("phase") != phase:
        ctx.state.data["phase"] = phase
        note_progress(ctx)


def reset_run_state(ctx) -> None:
    """No run is active -- clear every piece of mid-run bookkeeping so the next run starts from
    room 1 step "trap". Without this, a run that ended abnormally (501-tick timer expiry, a
    sceptre auto-eject, death) leaves room_step/door_attempt at whatever they were, and the next
    run skips room 1's trap/chest/sarcophagus entirely. Idempotent -- cheap to run every
    OUTSIDE/LOBBY tick rather than trying to catch each abnormal ending individually."""
    ctx.state.data["room_step"] = ROOM_STEP_TRAP
    ctx.state.data["door_attempt"] = 0
    ctx.state.data["dead_end_doors"] = []
    ctx.state.data["door_enter_attempts"] = {}


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


def outside_tick(ctx, config, doors: list = None) -> None:
    """Not near the Guardian mummy yet -- find and enter the correct one of the 4 static
    anonymous-door objects. Caches the winning index in ctx.state.data["correct_door_index"]
    (see order_doors_clockwise) so almost every entry after the first is a single direct
    click, not a fresh 4-door search -- expected to keep working for the life of the ~25-minute
    door rotation.

    A single failed attempt is NOT treated as "wrong door": per the wiki, entering the correct
    door is a Thieving roll that can fail (stun + 1-4 dmg) just like a wrong one. The cached
    index is only invalidated after OUTSIDE_DOOR_FAIL_TOLERANCE consecutive failures on that
    same index, which tolerates a bad roll without re-searching all 4 doors, while still
    recovering when the ~25-minute rotation genuinely moves the correct door.

    NEEDS LIVE VERIFICATION: the exact signal that a door was wrong (led to an "abandoned
    room" rather than the Guardian mummy's lobby) and how to leave that room back outside.
    Best-effort assumption implemented below: re-click the anonymous door we can see from
    wherever we ended up (anonymous doors are described as ordinary bidirectional entry points
    in the wiki) to step back outside, then try the next door index next tick. Correct if
    wrong, per this codebase's "ASSUMPTION, NOT YET VERIFIED LIVE" convention (see
    blast_furnace.py)."""
    if doors is None:
        doors = find_outer_doors()
    if len(doors) != 4:
        # != rather than < : a larger match set means the name substring caught something this
        # code's clockwise indexing was never designed for, and proceeding would cache an index
        # that points at a different physical door every call.
        #
        # CONFIRMED live 2026-08-17 (bug, now fixed): this used to just log-and-wait here, which
        # meant step_back_outside (below) was only ever reachable as a same-tick follow-up right
        # after THIS function's own door click -- a fresh tick that starts already inside an
        # abandoned room (e.g. right after a routine restart) hit this early return every single
        # tick forever, since doors-not-visible-from-inside never becomes 4 on its own. Routing
        # through step_back_outside here too means every tick gets a real chance at recovery
        # (all 4 visible / abandoned-room exit found / genuinely nothing -> defers to the
        # no-progress safety net), not just the one that happened to just click a door.
        debug_log.log("pyramid_plunder", f"found {len(doors)} anonymous doors (expected 4) -- checking for recovery")
        step_back_outside(ctx)
        return
    ordered = order_doors_clockwise(doors)
    note_progress(ctx)

    cached_index = ctx.state.data.get("correct_door_index")
    attempt_index = ctx.state.data.get("door_attempt_index", 0) % len(ordered)
    index_to_try = cached_index if cached_index is not None else attempt_index
    door = ordered[index_to_try]

    since_tick = controller.get_last_chat_message().get("tick")
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if not controller.click_entity(ctx.ser, door, check_ui_blockers=False):
        return
    # Let the search-the-door attempt (and possible 1-4 dmg stun on failure) resolve before
    # polling for the mummy -- wait_for_mummy itself keeps checking for up to 8s rather than a
    # single check, since a correct door's mummy can take a few seconds to stream into GET
    # /npcs after the scene transition (see wait_for_mummy's own docstring).
    controller.human_delay(1.0, 1.8)

    if wait_for_mummy() is not None:
        debug_log.log("pyramid_plunder", f"reached the Guardian mummy via door index {index_to_try}")
        ctx.state.data["correct_door_index"] = index_to_try
        ctx.state.data["cached_door_fails"] = 0
        ctx.state.data["cached_door_fail_index"] = None
        return

    # CONFIRMED live 2026-08-17 (user directive, matches the wiki): the "shoved back" punch --
    # "A mysterious bandaged hand punches you away from the door." -- only happens when
    # attempting the CORRECT door and failing its Thieving roll; a wrong door leads silently
    # into the abandoned room with no punch at all. So this message alone definitively proves
    # which door is correct even on a failed attempt -- cache it immediately and don't count
    # this toward OUTSIDE_DOOR_FAIL_TOLERANCE, unlike an ordinary "didn't reach the mummy" that
    # could just as easily mean a wrong door.
    punched = controller.wait_for_chat_message(
        lambda m: "bandaged hand punches you away" in (m.get("message") or "").lower(),
        since_tick=since_tick, timeout_s=0.5,
    )
    if punched is not None:
        debug_log.log("pyramid_plunder", f"door index {index_to_try} punched us back -- "
                                         f"confirmed correct, just an unlucky roll")
        ctx.state.data["correct_door_index"] = index_to_try
        ctx.state.data["cached_door_fails"] = 0
        ctx.state.data["cached_door_fail_index"] = None
        step_back_outside(ctx)
        return

    debug_log.log("pyramid_plunder", f"door index {index_to_try} didn't reach the mummy -- "
                                     f"stepping back out and trying again")
    step_back_outside(ctx)

    if cached_index is None:
        ctx.state.data["door_attempt_index"] = attempt_index + 1
        return

    # Consecutive-failure accounting for the cached index only (see the docstring above).
    if ctx.state.data.get("cached_door_fail_index") != index_to_try:
        ctx.state.data["cached_door_fail_index"] = index_to_try
        fails = 0
    else:
        fails = ctx.state.data.get("cached_door_fails", 0)
    fails += 1
    ctx.state.data["cached_door_fails"] = fails
    if fails >= OUTSIDE_DOOR_FAIL_TOLERANCE:
        debug_log.log("pyramid_plunder", f"cached door index {index_to_try} failed {fails} times "
                                         f"in a row -- dropping the cache and re-searching")
        ctx.state.data["correct_door_index"] = None
        ctx.state.data["cached_door_fails"] = 0
        ctx.state.data["cached_door_fail_index"] = None
        ctx.state.data["door_attempt_index"] = (index_to_try + 1) % len(ordered)


def step_back_outside(ctx) -> None:
    """Wrong-door recovery. Three possible fresh states, checked in order:

    1. All 4 outer doors still visible -- the entry attempt just failed its Thieving roll and
       we never left (nothing to step back out of -- clicking a door here would be an unrelated
       extra entry attempt on one we didn't choose).
    2. CONFIRMED live 2026-08-17: a wrong door lands in an "abandoned room" (still region 7749,
       but roomNumber=0/inRun=false and no Guardian mummy) containing its own exit object,
       ABANDONED_ROOM_EXIT_ID -- click that to leave. This is the actual escape; a plain
       re-search for anonymous doors from inside this room always finds zero, since anonymous
       doors don't exist in there at all.
    3. Nothing resolvable (neither the outer doors nor the room's own exit) -- exactly the case
       the shared no-progress counter (check_no_progress) exists to catch, so no further
       hand-rolled escape here.

    Deliberately re-fetches /objects rather than reusing outside_tick's pre-click snapshot --
    that snapshot's screenX/screenY were resolved on the other side of an area transition and
    can easily point at nothing useful now."""
    controller.wait_until_player_idle(timeout_s=5.0)
    fresh = find_outer_doors()
    if len(fresh) >= 4:
        debug_log.log("pyramid_plunder", "still outside (all 4 doors visible) -- the entry "
                                         "attempt just failed its roll, nothing to step back out of")
        return

    exit_obj = find_object_by_id(controller.get_objects(radius=15), ABANDONED_ROOM_EXIT_ID)
    if exit_obj is not None:
        debug_log.log("pyramid_plunder", "in an abandoned room -- leaving via its exit door")
        note_progress(ctx)
        controller.click_entity(ctx.ser, exit_obj, offset=TOMB_DOOR_AIM_OFFSET, check_ui_blockers=False)
        controller.human_delay(1.0, 2.0)
        return

    if not fresh:
        debug_log.log("pyramid_plunder", "no anonymous door or abandoned-room exit resolvable "
                                         "-- waiting for a fresh poll")
        return
    fresh.sort(key=lambda o: o.get("distance", 9999))
    note_progress(ctx)
    controller.click_entity(ctx.ser, fresh[0], check_ui_blockers=False)
    controller.human_delay(1.0, 2.0)


def lobby_tick(ctx, config, mummy: dict) -> None:
    """Guardian mummy reachable, no run active -- right-click Start-minigame."""
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if controller.click_menu_action(ctx.ser, mummy, "Start-minigame", check_ui_blockers=False):
        debug_log.log("pyramid_plunder", "started a new run")
        note_progress(ctx)
        controller.human_delay(0.8, 1.5)


def setup(ctx):
    ctx.state.data.setdefault("room_step", ROOM_STEP_TRAP)
    ctx.state.data.setdefault("door_attempt", 0)
    ctx.state.data.setdefault("dead_end_doors", [])
    ctx.state.data.setdefault("door_enter_attempts", {})
    ctx.state.data.setdefault("no_progress_ticks", 0)


def tick(ctx):
    config = ctx.state.config
    ctx.state.data["progress_this_tick"] = False
    maybe_eat_food(ctx, config)

    pp = controller.get_pyramid_plunder()

    # CONFIRMED live 2026-08-17 (user directive): this is the routine's one reliable,
    # timing-independent signal that a new room has actually started -- door_step's own
    # post-click "did roomNumber just increase" check can race the varbit update and miss a
    # genuine advance. When that happens, room_step used to stay stuck on "door" into the new
    # room: the next door_step call would run against a stale door_attempt/dead_end_doors state
    # from the OLD room, with the physical geometry now belonging to a DIFFERENT room -- live-
    # confirmed this landed a click on the new room's own exit door by accident, ending the run
    # early. Resetting here, unconditionally, whenever the room number itself changes (by
    # whatever means) is the fix: it can never miss a transition door_step's own narrower check
    # might.
    room = pp.get("roomNumber", 0)
    if room != ctx.state.data.get("last_room_number"):
        ctx.state.data["last_room_number"] = room
        ctx.state.data["room_step"] = ROOM_STEP_TRAP
        ctx.state.data["door_attempt"] = 0
        ctx.state.data["dead_end_doors"] = []
        ctx.state.data["door_enter_attempts"] = {}
        note_progress(ctx)

    if pp.get("inRun"):
        note_phase(ctx, "in_room")
        in_room_tick(ctx, config, pp)
        check_no_progress(ctx)
        return

    # Not in a run: whatever ended the last one (Quick-leave, the 501-tick timer, a sceptre
    # auto-eject, death) leaves mid-run bookkeeping behind -- clear it before the next run.
    reset_run_state(ctx)
    # Leftover artefacts survive the run's end too, so keep draining them out here as well and
    # not only from in_room_tick.
    drop_loot(ctx, config)

    npcs = controller.get_npcs()
    doors = find_outer_doors()
    mummy = resolve_mummy(npcs, doors)

    if mummy is not None:
        note_phase(ctx, "lobby")
        lobby_tick(ctx, config, mummy)
    else:
        note_phase(ctx, "outside")
        outside_tick(ctx, config, doors)

    check_no_progress(ctx)


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

    if ctx.state.data.get("room_step", ROOM_STEP_TRAP) != step:
        note_progress(ctx)

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
    note_progress(ctx)

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    since_tick = controller.get_last_chat_message().get("tick")
    before_pos = (controller.get_player().get("localX"), controller.get_player().get("localY"))
    controller.click_entity(ctx.ser, trap, check_ui_blockers=False)

    # CONFIRMED live 2026-08-17 (user directive): a successful disarm lets the player's own
    # queued movement resume immediately (they start walking past the trap); a failed attempt
    # leaves them standing still. Poll for whichever resolves first -- movement starting (a
    # fast, positive success signal) or the confirmed fail message -- instead of always
    # blocking out the full fail-message timeout even on the success path, where no such
    # message is ever coming.
    deadline = time.time() + 2.5
    moved = failed = False
    while time.time() < deadline:
        pos = (controller.get_player().get("localX"), controller.get_player().get("localY"))
        if None not in pos and pos != before_pos:
            moved = True
            break
        msg = controller.get_last_chat_message()
        msg_tick = msg.get("tick")
        if (msg_tick is not None and since_tick is not None and msg_tick > since_tick
                and "fail to deactivate the trap" in (msg.get("message") or "").lower()):
            failed = True
            break
        time.sleep(0.15)

    if moved:
        controller.wait_until_player_idle(timeout_s=3.0)
    elif failed:
        debug_log.log("pyramid_plunder", "failed to disarm the spear trap -- retrying")
    # Re-polled next tick via pp["trapActive"] either way -- stays on "trap" until the bridge
    # confirms it's actually disarmed, so an ambiguous/failed attempt (logged above or not)
    # naturally retries instead of advancing early.


def chest_step(ctx, config, pp: dict) -> None:
    """Grand Gold Chest always opens instantly on a single search (per the wiki -- no
    fail-and-retry loop needed here, unlike the trap/sarcophagus/doors).

    Three distinct outcomes, not two -- "not in this poll's object list" is a camera/distance
    problem, NOT evidence the chest is already looted, so it retries next tick instead of
    silently advancing past an unlooted chest."""
    next_step = ROOM_STEP_SARCO if pp.get("roomNumber", 0) >= config["sarcophagusStartRoom"] else ROOM_STEP_DOOR

    chest = find_object_by_id(controller.get_objects(radius=15), GRAND_GOLD_CHEST_ID)
    if chest is None:
        debug_log.log("pyramid_plunder", "no Grand Gold Chest resolved this poll -- retrying")
        return
    note_progress(ctx)

    impostor = chest.get("impostorId")
    if impostor == GRAND_GOLD_CHEST_SEARCHED_IMPOSTOR_ID:
        ctx.state.data["room_step"] = next_step
        return
    if impostor != GRAND_GOLD_CHEST_CLOSED_IMPOSTOR_ID:
        debug_log.log("pyramid_plunder", f"Grand Gold Chest in unexpected state (impostorId="
                                         f"{impostor}) -- treating it as done")
        ctx.state.data["room_step"] = next_step
        return

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    controller.click_entity(ctx.ser, chest, check_ui_blockers=False)
    controller.human_delay(0.6, 1.5)
    ctx.state.data["room_step"] = next_step


def sarco_step(ctx, config, pp: dict) -> None:
    """Two-stage, driven entirely off the single base-id object's impostorId (the three wiki
    versions of the same object, in order): 21255 "Closed"/Open -> 21257 "Opening"/no options
    at all while the animation plays -> 21256 "Opened"/Search. No mummy-specific handling
    beyond the routine's normal food-eat safety net -- per the design doc, prayer management
    was explicitly rejected in favor of that.

    The 21257 state is exactly what an "wait for the opening animation" delay was ever really
    waiting out, so it's polled rather than guessed: stay on this step, click nothing (there is
    no clickable option in that state), re-poll next tick."""
    sarco = find_object_by_id(controller.get_objects(radius=15), SARCOPHAGUS_ID)
    if sarco is None:
        debug_log.log("pyramid_plunder", "no sarcophagus resolved this poll -- retrying")
        return
    note_progress(ctx)

    impostor = sarco.get("impostorId")

    if impostor == SARCOPHAGUS_OPENING_IMPOSTOR_ID:
        # Mid-animation, no menu option exists -- just let it finish.
        controller.human_delay(0.6, 1.2)
        return

    if impostor == SARCOPHAGUS_OPENED_IMPOSTOR_ID:
        if not controller.wait_until_player_idle(timeout_s=5.0):
            return
        # CONFIRMED live 2026-08-17 (user directive): Search has a long completion animation,
        # and advancing unconditionally right after the click (the old behavior) let a
        # still-animating room get re-polled and re-clicked before it actually finished,
        # observed live producing a real "You have already looted this Sarcophagus." message
        # from a redundant second Search. Confirm the click actually registered via
        # /last-action, then wait for the loot to actually land (an inventory count change)
        # before considering this step done -- same "confirm via the game's own signal, not a
        # fixed guess" idiom as trap_step's movement/fail-message poll above.
        before_count = len(controller.get_inventory())
        action_before = controller.get_last_action().get("tick")
        controller.click_entity(ctx.ser, sarco, check_ui_blockers=False)   # "Search"

        deadline = time.time() + 6.0
        registered = looted = False
        while time.time() < deadline:
            if not registered:
                action = controller.get_last_action()
                if action.get("tick") != action_before:
                    registered = True
            if len(controller.get_inventory()) != before_count:
                looted = True
                break
            time.sleep(0.2)

        if not registered:
            debug_log.log("pyramid_plunder", "sarcophagus Search click didn't register -- retrying")
            return
        if not looted:
            debug_log.log("pyramid_plunder", "sarcophagus Search registered but no loot landed "
                                             "within the timeout -- moving on anyway")
        ctx.state.data["room_step"] = ROOM_STEP_DOOR
        return

    if impostor == SARCOPHAGUS_CLOSED_IMPOSTOR_ID:
        if not controller.wait_until_player_idle(timeout_s=5.0):
            return
        controller.click_entity(ctx.ser, sarco, check_ui_blockers=False)   # "Open"
        controller.human_delay(1.0, 2.0)
        return  # re-poll next tick; opening (21257) then opened (21256) are handled above

    debug_log.log("pyramid_plunder", f"sarcophagus in unexpected state (impostorId={impostor}) "
                                     f"-- moving on to the doors")
    ctx.state.data["room_step"] = ROOM_STEP_DOOR


def find_exit_doors(objs: list) -> list:
    """Candidate in-room exit doors, nearest first: by id first, by name second. EXIT_DOOR_ID is
    the one id in this file with no decompiled-plugin cross-check (see its own comment), so it's
    matched against BOTH "id" (in case it's a plain single-state object) and "impostorId" (in
    case it's really an impostor id, like every other wiki id1 on these pages), and a name lookup
    backs both up -- a wrong id degrades to a less targeted lookup instead of silently never
    finding the door at all. The name fallback excludes the 4 pick-lock progression doors, which
    share the "Tomb Door" name but never offer Quick-leave."""
    candidates = [o for o in objs
                  if (o.get("id") == EXIT_DOOR_ID or o.get("impostorId") == EXIT_DOOR_ID)
                  and o.get("visible", True)]
    if not candidates:
        candidates = [o for o in find_objects_by_name(objs, EXIT_DOOR_NAME)
                      if o.get("id") not in TOMB_DOOR_WALL_IDS]
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates


def quick_leave(ctx, config) -> None:
    """Room 6 (or configured lastRoomToLoot) is fully looted -- end the run via the in-room
    exit door's Quick-leave option rather than walking back out through 6 tomb doors. The next
    tick's phase dispatch (OUTSIDE vs LOBBY) picks up wherever the game actually drops the
    player, so no explicit "where did we land" handling is needed here."""
    exit_doors = find_exit_doors(controller.get_objects(radius=15))
    if not exit_doors:
        debug_log.log("pyramid_plunder", "no exit door found in the final room -- waiting")
        return
    note_progress(ctx)
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    # click_menu_action, not click_entity: the door's default left-click action is "Leave Tomb",
    # and only "Quick-leave" ends the run instantly from inside the room. It returns False when
    # the option isn't on that object's menu at all, which is also how a wrong name-fallback
    # candidate gets rejected -- so walk the candidates until one actually offers Quick-leave.
    for exit_door in exit_doors[:3]:
        if controller.click_menu_action(ctx.ser, exit_door, "Quick-leave",
                                         offset=TOMB_DOOR_AIM_OFFSET, check_ui_blockers=False):
            debug_log.log("pyramid_plunder", f"quick-left room {config['lastRoomToLoot']} -- run complete, restarting")
            reset_run_state(ctx)
            return


def door_key(door: dict):
    return (door.get("x"), door.get("y"), door.get("plane"))


ROOM_DOOR_SEARCH_RADIUS = 10  # tiles from the room's own chest, not the player -- see docstring


def find_room_doors(dead_ends: set) -> list:
    """Tomb Door candidates for the CURRENT room only, anchored on the Grand Gold Chest's
    position rather than the player's -- user-flagged 2026-08-17: a player-anchored radius still
    let the previous room's doors bleed in whenever the player happened to be standing near a
    room's edge (their distance-to-player varies with where they stand; the chest, the fixed
    center of every room per the wiki, doesn't). Falls back to a player-anchored radius if the
    chest itself isn't resolvable this particular poll (e.g. mid-transition) -- degraded, not
    broken."""
    objs = controller.get_objects(radius=15)
    chest = find_object_by_id(objs, GRAND_GOLD_CHEST_ID)

    if chest is not None:
        cx, cy = chest.get("x", 0), chest.get("y", 0)
        distance_fn = lambda o: math.hypot(o.get("x", 0) - cx, o.get("y", 0) - cy)
    else:
        distance_fn = lambda o: o.get("distance", 9999)

    return [o for o in objs
            if o.get("id") in TOMB_DOOR_WALL_IDS
            and o.get("impostorId") in (TOMB_DOOR_CLOSED_ID, TOMB_DOOR_OPENED_ID)
            and o.get("visible", True)
            and door_key(o) not in dead_ends
            and distance_fn(o) <= ROOM_DOOR_SEARCH_RADIUS]


def attempt_door_click(ctx, door: dict, room: int):
    """Click one Tomb Door candidate and interpret what happened. Returns (outcome, door):

    - "dead_end": the confirmed chat message arrived.
    - "advanced": roomNumber actually increased -- resets the room bookkeeping itself (needed
      for the immediate same-call chain below; tick()'s own top-level room-number check is the
      general backstop for any race this misses).
    - "flipped_open": a still-closed door's impostorId flipped to OPENED within this same call.
      CONFIRMED live 2026-08-17 (user directive): the Pick-lock roll's outcome is knowable
      instantly via a direct re-lookup of this exact door (get_object_at), not just by waiting
      for the next external tick's own fresh /objects scan to notice it. The returned `door` is
      the refreshed object, ready to feed straight back into this function for the Enter click.
    - "missed": the click resolved to a plain "Walk here" instead of a real door interaction
      (see TOMB_DOOR_AIM_OFFSET's own note).
    - "registered": a genuine Pick-lock/Enter attempt happened but produced no other signal --
      a normal, expected silent failure roll on a still-closed door, per the wiki."""
    since_tick = controller.get_last_chat_message().get("tick")
    action_before = controller.get_last_action().get("tick")
    before_impostor = door.get("impostorId")
    before_pos = (controller.get_player().get("localX"), controller.get_player().get("localY"))
    if not controller.click_entity(ctx.ser, door, offset=TOMB_DOOR_AIM_OFFSET, check_ui_blockers=False):
        return "missed", door
    controller.human_delay(0.3, 0.6)

    # CONFIRMED live 2026-08-17: a long-range Tomb Door click can silently resolve to a plain
    # "Walk here" instead of Pick-lock/Enter (GET /last-action: menuAction=WALK) -- a genuine
    # aim miss, not a skill-roll failure.
    action = controller.get_last_action()
    click_missed = (action.get("tick") == action_before
                     or (action.get("menuAction") or "").upper() == "WALK")

    # CONFIRMED live 2026-08-17 (user directive): roomNumber only updates once per 0.6s game
    # tick, but a real room transfer teleports the player instantly. localX/localY are
    # interpolated every render frame regardless (see CLAUDE.md's "Sub-tick data" section), so a
    # sudden large jump is a far faster and more reliable "we just transferred rooms" signal
    # than waiting for the tick-capped bridge state to catch up. Without this, a click on the
    # CORRECT door could get treated as still-pending and re-clicked (the object hadn't visually
    # despawned from the OLD room's scene yet) before roomNumber ever confirmed the advance --
    # live-confirmed this produces a confusing "You've already opened this door and it leads to
    # a dead end." on a door that was actually the right one, not a real dead end at all.
    # NEEDS LIVE VERIFICATION: POSITION_JUMP_THRESHOLD's exact value -- picked as comfortably
    # larger than one render-frame's worth of normal walking (see the "player MOVED" step sizes
    # this codebase's own debug log already records), not measured against an actual room
    # transfer's real jump distance yet.
    deadline = time.time() + (0.5 if click_missed else 2.5)
    jumped = dead_end = False
    while time.time() < deadline:
        pos = (controller.get_player().get("localX"), controller.get_player().get("localY"))
        if None not in pos and None not in before_pos \
                and math.hypot(pos[0] - before_pos[0], pos[1] - before_pos[1]) > POSITION_JUMP_THRESHOLD:
            jumped = True
            break
        msg = controller.get_last_chat_message()
        msg_tick = msg.get("tick")
        if (msg_tick is not None and since_tick is not None and msg_tick > since_tick
                and "leads to a dead end" in (msg.get("message") or "").lower()):
            dead_end = True
            break
        time.sleep(0.1)

    if dead_end:
        return "dead_end", door

    if jumped:
        debug_log.log("pyramid_plunder", "position jumped right after the click -- treating as an instant room transfer")
        ctx.state.data["room_step"] = ROOM_STEP_TRAP
        ctx.state.data["door_attempt"] = 0
        ctx.state.data["dead_end_doors"] = []
        ctx.state.data["door_enter_attempts"] = {}
        return "advanced", door

    fresh_pp = controller.get_pyramid_plunder()
    # Fallback for a transfer this function's own fast position-jump check somehow missed.
    # roomNumber alone is not fully reliable either -- it can still read a stale leftover value
    # for a moment even after a run has actually ended (see the inRun guard below) -- but by
    # this point in the poll it's had the full window to settle, unlike a single early check.
    if fresh_pp.get("inRun") and fresh_pp.get("roomNumber", room) > room:
        debug_log.log("pyramid_plunder", f"advanced to room {fresh_pp.get('roomNumber')}")
        ctx.state.data["room_step"] = ROOM_STEP_TRAP
        ctx.state.data["door_attempt"] = 0
        ctx.state.data["dead_end_doors"] = []
        ctx.state.data["door_enter_attempts"] = {}
        return "advanced", door

    if click_missed:
        return "missed", door

    if before_impostor == TOMB_DOOR_CLOSED_ID:
        refreshed = controller.get_object_at(door.get("x"), door.get("y"), door.get("plane"), door.get("id"))
        if refreshed is not None and refreshed.get("impostorId") == TOMB_DOOR_OPENED_ID:
            return "flipped_open", refreshed

    return "registered", door


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

    NEEDS LIVE VERIFICATION: whether Pick-lock and Enter are actually one click or two. The
    wiki infobox lists them as the two SEPARATE version states of the same object (impostor
    20948 "Closed"/Pick-lock -> 20949 "Opened"/Enter), so this treats them as two clicks and
    accepts either state as a candidate: a door that flipped to 20949 was successfully picked
    and still needs entering, and is preferred over starting a fresh pick attempt elsewhere. If
    it turns out one click does both, the 20949 branch simply never has anything to click and
    this still behaves exactly like a one-click implementation.

    Doors confirmed dead ends this room are remembered by tile (dead_end_doors) and excluded --
    without that, an already-picked dead end sitting at 20949 would win the "prefer opened"
    preference forever. A tile also gets excluded (door_enter_attempts, DOOR_ENTER_ATTEMPT_LIMIT)
    without an explicit dead-end message if its clicks keep producing no signal at all -- covers
    both a click that misses the object entirely (CONFIRMED live: resolves to a plain "Walk
    here" instead, see click_missed below -- open at long range, see TOMB_DOOR_AIM_OFFSET's own
    note) and a silent dead end on an already-opened door (Entering it produces no chat message,
    unlike the initial Pick-lock attempt on a closed one)."""
    room = pp.get("roomNumber", 0)
    if room >= config["lastRoomToLoot"]:
        quick_leave(ctx, config)
        return

    dead_ends = {tuple(k) for k in ctx.state.data.get("dead_end_doors", [])}
    doors = find_room_doors(dead_ends)
    if not doors:
        debug_log.log("pyramid_plunder", "no untried tomb doors found -- waiting")
        return

    # Prefer a door that's already been picked open (just needs "Enter") over starting a new
    # pick-lock attempt -- entering an unlocked door is strictly more progress.
    opened = sorted((o for o in doors if o.get("impostorId") == TOMB_DOOR_OPENED_ID),
                    key=lambda o: o.get("distance", 9999))
    attempt = ctx.state.data.get("door_attempt", 0)
    if opened:
        door = opened[0]
    else:
        doors.sort(key=lambda o: o.get("distance", 9999))
        door = doors[attempt % len(doors)]

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return

    outcome, door = attempt_door_click(ctx, door, room)

    if outcome == "dead_end":
        debug_log.log("pyramid_plunder", f"door attempt {attempt} is a dead end -- trying the next one")
        remembered = list(ctx.state.data.get("dead_end_doors", []))
        remembered.append(list(door_key(door)))
        ctx.state.data["dead_end_doors"] = remembered
        ctx.state.data["door_attempt"] = attempt + 1
        return

    if outcome == "advanced":
        return  # room-number-change reset (tick()'s own check) already reset every bit of this

    # user-flagged 2026-08-17: once a Pick-lock succeeds (impostorId flips CLOSED -> OPENED),
    # we know it INSTANTLY via a direct re-lookup of that exact door -- no need to wait a full
    # external tick before acting on it. Chain straight into the Enter click right here instead
    # of returning and letting the next tick's "prefer opened" branch pick it up later.
    if outcome == "flipped_open":
        debug_log.log("pyramid_plunder", f"door attempt {attempt} picked open -- entering immediately")
        outcome, door = attempt_door_click(ctx, door, room)
        if outcome == "dead_end":
            debug_log.log("pyramid_plunder", f"door attempt {attempt} is a dead end -- trying the next one")
            remembered = list(ctx.state.data.get("dead_end_doors", []))
            remembered.append(list(door_key(door)))
            ctx.state.data["dead_end_doors"] = remembered
            ctx.state.data["door_attempt"] = attempt + 1
            return
        if outcome == "advanced":
            return

    click_missed = outcome == "missed"

    # A genuinely-registered click on a still-closed door is a normal, expected silent
    # Pick-lock failure roll (per the wiki) and just needs retrying -- that's real progress
    # (the attempt actually happened), so note_progress() fires. A click that missed
    # (click_missed) or one that registered on an already-OPENED door (silently entered a dead
    # end -- Entering an unlocked door has no skill-roll retry mechanic to wait out) is NOT
    # progress -- count these per tile and give up on it once DOOR_ENTER_ATTEMPT_LIMIT is hit,
    # so a tile this routine simply can't reliably click doesn't stall it forever.
    was_opened = door.get("impostorId") == TOMB_DOOR_OPENED_ID
    if not click_missed and not was_opened:
        note_progress(ctx)
        return

    key = door_key(door)
    stalled_attempts = dict(ctx.state.data.get("door_enter_attempts", {}))
    count = stalled_attempts.get(key, 0) + 1
    if count >= DOOR_ENTER_ATTEMPT_LIMIT:
        reason = "the click keeps missing" if click_missed else "entered with no signal either way"
        debug_log.log("pyramid_plunder", f"door attempt {attempt}: {reason} {count}x -- "
                                         f"treating as unreachable, trying the next one")
        remembered = list(ctx.state.data.get("dead_end_doors", []))
        remembered.append(list(key))
        ctx.state.data["dead_end_doors"] = remembered
        ctx.state.data["door_attempt"] = attempt + 1
        stalled_attempts.pop(key, None)
        ctx.state.data["door_enter_attempts"] = stalled_attempts
        return
    stalled_attempts[key] = count
    ctx.state.data["door_enter_attempts"] = stalled_attempts


INVENTORY_FULL_THRESHOLD = 28  # OSRS's own inventory size


def drop_loot(ctx, config) -> None:
    """Drop every inventory item whose name is in the fixed artefactNames list -- never a
    substring match, so food/lockpick are never at risk. Only fires once the inventory is
    actually full, then bursts through every matching item in one go rather than one drop per
    tick -- user directive 2026-08-17: dropping on every single tick that happens to be holding
    an artefact interrupts far too often and wastes real time across a whole run; there's no
    need to keep inventory empty moment-to-moment when nothing else is competing for the space.
    Left-click-to-drop resolves via controller.py's click_menu_action underneath click_entity's
    own UI-blocker-free inventory path, same as shipwreck_salvaging.py's drop_salvage.

    Finding the Pharaoh's sceptre is logged, never dropped (it's deliberately absent from
    artefactNames) -- the game's own auto-eject on finding it is what actually ends the run;
    this routine does nothing special beyond logging it."""
    names = {n.lower() for n in config.get("artefactNames", [])}
    # InventoryProvider resolves item screen coords from the inventory Widget, which only exists
    # while the Inventory tab is actually showing (see consume_first/open_inventory_tab's own
    # notes) -- clicking off a stale/hidden slot position otherwise. Idempotent and cheap: it
    # no-ops when the tab is already open.
    controller.open_inventory_tab(ctx.ser)
    inventory = controller.get_inventory()

    # startswith, not ==: the minigame drops the uncharged variant, named "Pharaoh's sceptre
    # (uncharged)". Drop-safety is unaffected either way (no sceptre name is in artefactNames),
    # this only decides whether the find gets logged. The flag clears itself once the sceptre is
    # gone from the inventory, so a second sceptre later in the same process still logs.
    has_sceptre = any((i.get("name") or "").lower().startswith("pharaoh's sceptre")
                      for i in inventory)
    if not has_sceptre:
        ctx.state.data["sceptre_logged"] = False
    elif not ctx.state.data.get("sceptre_logged"):
        debug_log.log("pyramid_plunder", "PHARAOH'S SCEPTRE FOUND -- letting the game's own auto-eject take over")
        ctx.state.data["sceptre_logged"] = True

    if len(inventory) < INVENTORY_FULL_THRESHOLD:
        return

    debug_log.log("pyramid_plunder", f"inventory full ({len(inventory)}/{INVENTORY_FULL_THRESHOLD}) "
                                     f"-- dropping all loot")
    for item in inventory:
        if (item.get("name") or "").lower() in names:
            controller.click_entity(ctx.ser, item, check_ui_blockers=False)
            controller.human_delay(0.2, 0.5)
