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

# CONFIRMED live 2026-08-17 (user-reported, screenshot): click_entity's default aim offset
# (-7, +7) -- already known to systematically miss thin WallObjects like tomb doors, see
# TOMB_DOOR_AIM_OFFSET's own note -- also misses the spear trap at close range (observed
# standing 1 tile away): the click kept resolving to "Walk here" onto the trap's own tile
# instead of Disarm, so the player just walked into it repeatedly with no progress. Aim
# dead-center instead, same fix as the tomb doors.
SPEAR_TRAP_AIM_OFFSET = (0, 0)

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
SARCOPHAGUS_CLOSED_IMPOSTOR_ID = 21255          # version "Closed", option "Open" -- the only
                                                 # state this routine ever clicks (see sarco_step)

# CONFIRMED live 2026-08-17 via the debug log's own per-tick snapshots: across a full session
# these two player animation ids appear 17 times and ONLY ever inside a sarcophagus step -- 4344
# while an Open attempt is being repeated, 4345 on the tick the sarcophagus actually opens (every
# single "room_step: sarco -> door" transition in that log is immediately preceded by an
# anim=4345 snapshot). Used purely as a "the game's own auto-repeat loop is already running,
# don't click again" gate in sarco_step, backed by SARCOPHAGUS_BUSY_MAX_WAIT_S so a wrong id
# degrades to a slightly delayed re-click rather than a deadlock.
SARCOPHAGUS_OPEN_ANIM_IDS = {4344, 4345}
SARCOPHAGUS_BUSY_MAX_WAIT_S = 20.0

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

# REPURPOSED 2026-08-17 (door_step's full rework -- see attempt_door_click's own docstring):
# used to cover two silent-failure cases (a click that misses the object entirely, and
# re-Entering an already-open wrong door with no chat message). The Enter-click case no longer
# exists in the reworked flow -- this now only counts a click that resolved to "missed" (aimed
# at nothing, e.g. a long-range "Walk here"), so a tile this routine can't reliably click still
# gets blacklisted instead of stalling the room forever.
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


def find_objects_by_id(objects: list, object_id: int) -> list:
    """All visible objects whose BASE id matches, nearest first -- same filter as
    find_object_by_id but returns every candidate instead of just the closest. Added 2026-08-17
    for trap_step's swap-after-repeated-failure logic (some rooms genuinely have more than one
    spear trap tile blocking the path to the chest)."""
    candidates = [o for o in objects if o.get("id") == object_id and o.get("visible", True)]
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates


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


def wait_for_mummy(timeout_s: float = 12.0, poll_interval_s: float = 0.5):
    """Polls resolve_mummy() instead of a single fixed-delay-then-check -- user-flagged
    2026-08-17: a correct door was repeatedly misjudged as wrong, and this codebase has already
    hit the underlying cause once before (pest_control.py's own note on NPC-streaming delay --
    a newly-loaded area's NPCs, including the Guardian mummy, can take a few seconds to appear
    in GET /npcs at all after the scene transition). A single check after a fixed 1.5-3.0s
    delay could fire before the mummy had streamed in, reporting a correct door as a failure --
    which then discarded real progress (dropped the door cache / walked into recovery logic)
    for no reason. Returns as soon as the mummy is found, same early-exit shape as
    wait_until_player_idle/wait_for_chat_message elsewhere in this codebase.

    Raised 8.0 -> 12.0 2026-08-17 (user-flagged live, second occurrence of the same class of
    bug): CONFIRMED via debug log -- door index 1 timed out at ~8s on one entry (misjudged as
    wrong, sent through the abandoned-room recovery path) and then reached the mummy in ~6s on
    the very next attempt at the SAME cached index, with no code path in between that could have
    changed which physical door index 1 points at. The only explanation consistent with that is
    the mummy occasionally taking longer than 8s to stream in even on a correct door. Cost of
    the extra margin is cheap (a few more seconds spent per wrong door, of which there are ~3 on
    average per outer-door search); cost of a false negative is much higher (a correct door
    gets treated as wrong, discarding the cached index and forcing a full 4-door re-search)."""
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
    ctx.state.data["trap_fail_tiles"] = {}
    ctx.state.data["trap_disarmed_this_room"] = False


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


POISON_CURE_COOLDOWN_S = 5.0  # user-flagged 2026-08-17: antipoisons/antidotes are expensive --
                               # don't drink again every single tick while `poisoned` is still
                               # true waiting for the dose already taken to register.


def maybe_cure_poison(ctx, config) -> None:
    """user-flagged 2026-08-17, after a scarab-swarm poison drained the player to 4/90 hp
    earlier this session (see food.eatAt's own note): sip an antipoison/antidote the moment
    /player reports poisoned, rather than just relying on food to out-heal the ongoing drain.
    ConsumablesProvider only classifies "food"/"prayer" (no antipoison category exists in the
    bridge), so this matches by name directly instead of going through consume_first -- same
    idiom as drop_loot's own name-based inventory scan. open_inventory_tab first for the same
    reason consume_first/drop_loot both do: item screen coords only resolve while that tab is
    actually showing.

    Cooldown-gated (POISON_CURE_COOLDOWN_S): these are real, expensive consumables, not a
    disposable resource like food -- one dose per poisoning event, not one per tick for however
    many ticks `poisoned` stays true while the dose already taken is still registering."""
    if not controller.get_player().get("poisoned"):
        return
    last_drunk = ctx.state.data.get("poison_cure_drunk_at", 0.0)
    if time.time() - last_drunk < POISON_CURE_COOLDOWN_S:
        return
    controller.open_inventory_tab(ctx.ser)
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if "antipoison" in name or "antidote" in name:
            controller.click_entity(ctx.ser, item, check_ui_blockers=False)
            ctx.state.data["poison_cure_drunk_at"] = time.time()
            debug_log.log("pyramid_plunder", f"poisoned -- drank {item.get('name')}")
            return


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
    # polling for the mummy -- wait_for_mummy itself keeps checking for up to 12s rather than a
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


RUN_START_CONFIRM_TIMEOUT_S = 5.0


def lobby_tick(ctx, config, mummy: dict) -> None:
    """Guardian mummy reachable, no run active -- right-click Start-minigame.

    BUG, FIXED 2026-08-17 (user-flagged live: "still clicks"; exact same class as quick_leave's
    own fixed bug below): click_menu_action returning True only means the "Start-minigame" entry
    was found and clicked -- inRun does not flip for another ~1.5-4s while the game teleports the
    player into room 1. This used to log "started a new run" and return straight away, so the
    NEXT tick still read inRun=false, dispatched to the lobby again, and right-clicked the mummy
    a SECOND time. CONFIRMED live via the debug log on every single run start, e.g.:
        [06:58:37.332 tick=216] menu-select Start-minigame Guardian mummy
        [06:58:37.669 tick=216] started a new run
        [06:58:38.910 tick=217] snapshot ... inCombat=True target=Guardian mummy(100%)
        [06:58:39.790 tick=217] click_entity target=Guardian mummy(id=1779) at (1007,718)
        [06:58:40.190 tick=217] [!] No 'Start-minigame' entry in menu for Guardian mummy
                                    (['Cancel', 'Mark Safespot', 'Walk here']) -- closing it
        [06:58:40.477 tick=217] key_tap ESC
    -- i.e. a wasted right-click + menu open + ESC against a mummy the player was already
    mid-interaction with (identical pair at ticks 83/84 earlier in the same log). Poll for the
    confirmation in-call instead, same shape as quick_leave's own inRun poll."""
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if not controller.click_menu_action(ctx.ser, mummy, "Start-minigame", check_ui_blockers=False):
        return
    note_progress(ctx)
    deadline = time.time() + RUN_START_CONFIRM_TIMEOUT_S
    while time.time() < deadline:
        if controller.get_pyramid_plunder().get("inRun"):
            debug_log.log("pyramid_plunder", "started a new run")
            return
        time.sleep(0.15)
    # Unconfirmed: leave it to the next tick's own dispatch rather than claiming success. If the
    # run really did start, that tick reads inRun=true and never comes back here at all; if the
    # click genuinely didn't take, retrying it is the correct thing to do.
    debug_log.log("pyramid_plunder", "Start-minigame click registered but inRun hasn't set yet "
                                     "-- waiting")


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
    maybe_cure_poison(ctx, config)

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
        ctx.state.data["trap_fail_tiles"] = {}
        ctx.state.data["trap_disarmed_this_room"] = False
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


def trap_key(trap: dict):
    return (trap.get("x"), trap.get("y"), trap.get("plane"))


TRAP_SWAP_FAIL_LIMIT = 2  # user directive 2026-08-17: swap to a different trap candidate after
                          # this many CONSECUTIVE fails (missed click or a confirmed failed
                          # disarm roll) on the same physical tile.

# WORKING HYPOTHESIS, NOT YET LIVE-CONFIRMED, 2026-08-17: user-flagged live that the room-
# spanning oscillation bug (see trap_step's own docstring) recurred even after dropping the
# unreliable "moved" success signal -- the room genuinely has 4 spear traps total, 2 on the
# entry side and 2 on the chest side (user-clarified), and TRAP_SWAP_FAIL_LIMIT's swap logic has
# no concept of "which side" a candidate is on, only raw distance. If the entry side's own trap
# racks up 2 consecutive "no confirming signal" results (not necessarily a REAL failed roll --
# just this routine not catching XP/the exact chat phrase in time), the swap could pick the
# next-closest candidate by pure distance, which -- if the far side's traps are within the same
# general radius=15 search used everywhere else in this file -- might be a chest-side trap
# instead of another entry-side one, sending the player straight into the exact loop being
# fixed. Scoped tighter than the general radius (matching MUMMY_MAX_DISTANCE=5's own precedent
# for "genuinely adjacent, not just somewhere in the room") specifically for trap CANDIDATE
# SELECTION, so a far-side trap can never even enter consideration until the player has
# actually walked over there on their own.
TRAP_SEARCH_RADIUS = 6


def trap_step(ctx, config, pp: dict) -> None:
    """Disarm the spear trap if the bridge reports it still armed. A failed attempt is
    confirmed via the chat message "You fail to deactivate the trap." (user-confirmed, see
    Global Constraints) -- logged for visibility, but the actual retry is driven by re-polling
    pp["trapActive"] next tick either way (stays on "trap" until the bridge confirms it's
    actually disarmed), so a missed/late chat message can't cause a stuck loop.

    NEEDS LIVE VERIFICATION: whether this is actually mandatory to reach the chest/
    sarcophagus/doors at all, or just an optional bonus-XP obstacle -- see design doc. Either
    way, disarming it first is a safe default (10 xp, minor 1-4 dmg risk on a failed attempt).

    BUG, FIXED 2026-08-17 (user-flagged live, multiple rounds -- final root cause): "the way we
    can see if we crossed the traps sucessfully is either the thieving 10 experience we get for
    disabling it OR the message that says 'You deactivate the trap!'" User clarified the actual
    room layout: 4 spear traps total, 2 on the entry side and 2 on the chest side (the chest
    itself is ALWAYS interactable/visible regardless of which side the player is on, so an
    earlier attempt at this fix that checked chest visibility as a "have we crossed" signal was
    wrong and has been reverted -- it would have skipped straight past every trap in every room).
    The real, previously-unfixed root cause: "moved" (the player's queued movement resuming) was
    never a reliable success signal at all, on either side of the room -- a failed attempt can
    ALSO produce movement (a knockback, or continuing to walk despite taking damage), so
    "moved"-as-success caused the routine to wrongly believe a trap was disarmed and immediately
    try the (still-armed) NEXT one, over and over, taking repeated damage. Dropped "moved"
    entirely; XP and the exact success chat message are the only two things this function ever
    trusts as confirmation.

    Earlier fix, still in place: some rooms genuinely have more than one spear trap tile
    blocking a single side's path (CONFIRMED live: the debug log shows exactly two real
    resolutions per room in the overwhelming common case). Always re-picking the single CLOSEST
    candidate every attempt, with no memory of which tile keeps failing, could get stuck
    alternating between two candidates that both keep failing (or one whose approach path
    requires stepping onto the OTHER trap first, taking incidental damage) with no way to break
    the cycle. TRAP_SWAP_FAIL_LIMIT tracks consecutive fails per tile
    (ctx.state.data["trap_fail_tiles"]) and temporarily deprioritizes a tile once it's failed
    twice in a row, falling back to trying every candidate again if all of them are currently
    deprioritized (never gets permanently stuck).

    BUG, FIXED 2026-08-17 (user directive, the actual final fix for the oscillation bug): "i
    think we just once we see the message you deactivate the trap we never again touch a trap
    until next room." Live investigation (chat log check + user confirming the character stayed
    in the same spot, not a cross-room walk) ruled out the TRAP_SEARCH_RADIUS cross-side theory
    above -- the real issue was that pp["trapActive"] apparently doesn't reliably reflect "has
    THIS player already disarmed what they need to," so trusting it at the top of every call
    could send the routine back into trap-clicking after a confirmed success. Once this
    function's own XP/chat confirmation fires even once, ctx.state.data["trap_disarmed_this_room"]
    is set and checked FIRST, before ever consulting trapActive again -- reset alongside the
    other per-room bookkeeping (dead_end_doors, trap_fail_tiles, ...) whenever the room actually
    changes."""
    if ctx.state.data.get("trap_disarmed_this_room"):
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        return

    if not pp.get("trapActive"):
        # BUG, FIXED 2026-08-17 (user-flagged live, room 4: "we went and disarmed the trap on
        # the other side and got into a loop"). This fallback fires whenever our own faster
        # XP/chat confirmation never caught the disarm at all (CONFIRMED live: room 4 transitioned
        # straight from "trap" to "chest" with NO "trap disarmed" log line anywhere -- the bridge
        # eventually reported trapActive=false on its own, tick-capped, without this function's
        # own detection ever firing). The success branch below sets trap_disarmed_this_room, but
        # this one didn't -- leaving the latch never actually engaged, so a later tick that saw
        # trapActive flip true again (the far side's own separate traps) walked straight back
        # into trap-clicking. trapActive=false is just as much a genuine "done with traps this
        # room" signal as our own confirmation, so it latches the same way.
        ctx.state.data["trap_disarmed_this_room"] = True
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        return

    candidates = find_objects_by_id(controller.get_objects(radius=TRAP_SEARCH_RADIUS), SPEAR_TRAP_ID)
    if not candidates:
        debug_log.log("pyramid_plunder", "trapActive but no spear trap object visible -- skipping")
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        return
    note_progress(ctx)

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    # user-flagged 2026-08-17 (screenshot): standing near the trap and clicking a copy resolved
    # before this wait kept walking the player INTO the trap tile instead of disarming it --
    # the candidates picked at the top of this function can be stale by the time we actually
    # click (wait_until_player_idle can block up to 5s), so re-resolve fresh immediately before
    # picking -- not a one-time pick at function entry.
    candidates = find_objects_by_id(controller.get_objects(radius=TRAP_SEARCH_RADIUS), SPEAR_TRAP_ID) or candidates
    trap_fail_tiles = dict(ctx.state.data.get("trap_fail_tiles", {}))
    usable = [t for t in candidates if trap_fail_tiles.get(trap_key(t), 0) < TRAP_SWAP_FAIL_LIMIT]
    trap = (usable or candidates)[0]  # closest usable candidate; if all are deprioritized, try everything again
    since_tick = controller.get_last_chat_message().get("tick")
    action_before = controller.get_last_action().get("tick")
    before_pos = (controller.get_player().get("localX"), controller.get_player().get("localY"))
    xp_before = controller.get_player().get("thievingXp")
    controller.click_entity(ctx.ser, trap, check_ui_blockers=False)

    # BUG, FIXED 2026-08-17 (user-flagged live: "still doesnt seem like we click the closest
    # speartrap?"). Diagnostic logging on the "moved" branch below (added earlier this session
    # to find out why xp_gained never fires) proved the real issue: xp_before/xp_now came back
    # IDENTICAL across 8+ consecutive "successful" (moved) resolutions in the same room --
    # thievingXp was never actually changing. That means "moved" was a false-positive success
    # signal the whole time: CONFIRMED live via the same class of bug already documented for
    # tomb doors (see attempt_door_click) and the chest -- a click on `trap` can silently
    # resolve to a plain "Walk here" instead of the trap's own Disarm/whatever action, and a
    # walk-here miss ALSO produces player movement, which the old code treated as "disarm
    # succeeded, queued movement resumed." Checking /last-action's menuAction rules this out
    # before ever trusting "moved" as evidence of anything.
    action = controller.get_last_action()
    click_missed = (action.get("tick") == action_before
                     or (action.get("menuAction") or "").upper() == "WALK")
    if click_missed:
        key = trap_key(trap)
        trap_fail_tiles[key] = trap_fail_tiles.get(key, 0) + 1
        ctx.state.data["trap_fail_tiles"] = trap_fail_tiles
        debug_log.log("pyramid_plunder", f"spear trap click missed (resolved to Walk here) "
                                         f"{trap_fail_tiles[key]}x -- retrying")
        controller.wait_until_player_idle(timeout_s=2.0)
        return

    # user directive 2026-08-17 (final root-cause fix): only two signals are ever trusted as
    # confirmation -- Thieving XP (lands the moment the client's own StatChanged event fires,
    # same sub-tick idiom as the tomb-door Pick-lock confirmation) and the exact chat message
    # "You deactivate the trap!" (user-specified). "moved" is no longer checked at all: a failed
    # attempt can ALSO produce player movement (a knockback, or continuing to walk despite
    # taking damage), which is exactly what caused the oscillation bug across multiple earlier
    # fix attempts this session. The confirmed-fail message and "Ouch!" (the generic OSRS
    # damage-taken message) are still checked, purely for fail-count bookkeeping (the swap
    # mechanic) and logging -- neither one is required for correctness, since simply not seeing
    # a success signal is already enough to keep retrying.
    deadline = time.time() + 2.5
    xp_gained = disarmed = failed = False
    while time.time() < deadline:
        xp = controller.get_player().get("thievingXp")
        if xp is not None and xp_before is not None and xp > xp_before:
            xp_gained = True
            break
        msg = controller.get_last_chat_message()
        msg_tick = msg.get("tick")
        if msg_tick is not None and since_tick is not None and msg_tick > since_tick:
            # BUG, FIXED 2026-08-17 (user-flagged live, then user-diagnosed: "most likely our
            # string matching isnt strict"). The old success check ("deactivate the trap" in
            # text) is a loose fragment that's ALSO contained inside the failure message's own
            # text -- if the real failure wording didn't precisely match the old strict
            # "fail to deactivate the trap" phrase (e.g. any variation this file's own comments
            # haven't seen confirmed live), it fell through to the looser success check and got
            # misread as a disarm, which is exactly what let the routine walk straight back into
            # a still-armed trap believing it was already past it. Now: ANY message containing
            # the single, unambiguous keyword "fail" is treated as a failure -- robust against
            # exact wording -- and success requires the full specific phrase "you deactivate the
            # trap" (mutually exclusive with "fail" in the overwhelming likely case), not just a
            # fragment shared with the failure text.
            text = (msg.get("message") or "").lower()
            if "fail" in text or "ouch" in text:
                failed = True
                break
            if "you deactivate the trap" in text:
                disarmed = True
                break
        time.sleep(0.15)

    key = trap_key(trap)

    if xp_gained or disarmed:
        # user directive 2026-08-17: "the moment we get xp we can click gold chest" -- chain
        # straight into chest_step's own click right here instead of just setting room_step and
        # waiting for the next external tick's own pp["trapActive"] re-check to notice the
        # disarm (bridge-state-gated, tick-capped). chest_step does its own fresh object lookup,
        # state check, click, and room_step advance -- calling it directly here is exactly
        # equivalent to how the next tick would have handled it, just without the wait.
        trap_fail_tiles.pop(key, None)
        ctx.state.data["trap_fail_tiles"] = trap_fail_tiles
        ctx.state.data["trap_disarmed_this_room"] = True
        reason = "XP" if xp_gained else "chat message"
        debug_log.log("pyramid_plunder", f"trap disarmed ({reason} confirmed) -- clicking the chest immediately")
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        chest_step(ctx, config, pp)
        return

    trap_fail_tiles[key] = trap_fail_tiles.get(key, 0) + 1
    ctx.state.data["trap_fail_tiles"] = trap_fail_tiles
    reason = "failed the roll" if failed else "no confirming signal within the window"
    debug_log.log("pyramid_plunder", f"spear trap attempt {reason} ({trap_fail_tiles[key]}x) -- retrying")
    # Re-polled next tick via pp["trapActive"] either way -- stays on "trap" until the bridge
    # confirms it's actually disarmed, so a failed/ambiguous attempt naturally retries instead
    # of advancing early. A tile that hits TRAP_SWAP_FAIL_LIMIT gets deprioritized (not excluded
    # outright) by the candidate selection above, so a genuinely single-trap room still
    # eventually retries it once every other candidate is exhausted.


def chest_step(ctx, config, pp: dict) -> None:
    """Grand Gold Chest always opens instantly on a single search (per the wiki -- no
    fail-and-retry loop needed here, unlike the trap/sarcophagus/doors).

    Three distinct outcomes, not two -- "not in this poll's object list" is a camera/distance
    problem, NOT evidence the chest is already looted, so it retries next tick instead of
    silently advancing past an unlooted chest.

    BUG, FIXED 2026-08-17 (user-flagged live: "we missclick the golden chest now because of the
    movement, or rather the chest position once we drag our mouse over is using stale data").
    Same class of bug already fixed for trap_step's own trap pick (see that function's own
    comment): the chest object fetched here for the state check can be resolved WHILE the
    player is still walking -- especially now that trap_step can chain straight into this
    function the instant a trap disarm is confirmed, well before the player has finished
    crossing the room. Its screenX/screenY reflect wherever the camera/player were at that
    fetch, not wherever they end up once wait_until_player_idle below actually returns. Re-
    fetching fresh immediately before the click (not a one-time pick at function entry) is the
    fix, same idiom as trap_step/attempt_door_click/sarco_step."""
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
    chest = find_object_by_id(controller.get_objects(radius=15), GRAND_GOLD_CHEST_ID) or chest
    controller.click_entity(ctx.ser, chest, check_ui_blockers=False)
    controller.human_delay(0.6, 1.5)
    ctx.state.data["room_step"] = next_step


def sarco_step(ctx, config, pp: dict) -> None:
    """REWRITTEN 2026-08-17 (user directive, live-observed on the real account, overriding the
    wiki-infobox-derived two-stage Open->Search design this used to have): "it clicked search
    and it should never search. as long as the click resolves it will loot the item." A single
    Open click resolving is the complete interaction -- no separate Search click, no waiting on
    the object's impostorId to reach OPENED first. Trusting the user's direct live observation
    of their own account over the wiki-derived Closed/Opening/Opened state model.

    Click once, confirm it registered via /last-action (same idiom as every other confirmed
    click in this file), short delay, move straight to the doors -- nothing waits on the
    object's state to reach a specific value.

    BUG, FIXED 2026-08-17 (user-flagged: "we skipped 2 sarcos"): the version of this that only
    checked /last-action registration -- and not whether the Open attempt actually SUCCEEDED --
    advanced to the doors even on a normal failed Strength roll (silent, no message, object
    stays Closed, per the wiki: "Failing has no downside, other than needing to wait a few
    ticks... you will automatically keep attempting to open it until successful"). CONFIRMED
    live via the debug log: a room's sarcophagus click registered, room_step advanced to DOOR,
    door_step's own >=lastRoomToLoot branch quick-left, and only the quick-leave's own eager
    reset_run_state (see quick_leave's docstring) sent the routine back through trap/chest/sarco
    a second time -- during which the retry click landed on a still-CLOSED object, proving the
    first attempt's roll had failed and the loot was never actually collected. "The click
    resolves" (registers) and "the roll succeeds" are different things; only the latter means
    done."""
    sarco = find_object_by_id(controller.get_objects(radius=15), SARCOPHAGUS_ID)
    if sarco is None:
        debug_log.log("pyramid_plunder", "no sarcophagus resolved this poll -- retrying")
        return
    note_progress(ctx)

    if sarco.get("impostorId") != SARCOPHAGUS_CLOSED_IMPOSTOR_ID:
        # Already opened/mid-animation (e.g. resumed mid-sarco after a routine restart) --
        # per the user directive above, never issue a Search click. Nothing left to do here.
        #
        # user-flagged 2026-08-17: this is the tick right after the busy-wait animation gate
        # above first sees impostorId flip away from Closed -- the discrete object state updates
        # slightly before the sarcophagus's own opening animation visually finishes on screen.
        # We don't need to wait it out (clicking through it wouldn't cancel it), but moving
        # straight to a tomb door click here can visibly overlap the tail of that animation.
        # Small fixed settle delay, not the usual human_delay range -- this isn't approximating
        # human reaction time, just giving the animation a moment to finish. Raised 0.3-0.4 ->
        # 0.5-0.7 -> 0.7-0.9 -> 1.2-1.4 2026-08-17 (user-flagged live three times: still not
        # enough).
        controller.human_delay(1.2, 1.4)
        ctx.state.data["room_step"] = ROOM_STEP_DOOR
        return

    # BUG, FIXED 2026-08-17 (user-flagged live: "still clicks"). Per the wiki
    # (data/raw/S/"Sarcophagus (Pyramid Plunder).txt": "Failing has no downside, other than
    # needing to wait a few ticks until you can attempt again - you will automatically keep
    # attempting to open it until successful"), ONE Open click starts an auto-repeating attempt
    # loop the player drives entirely on their own. There is nothing to re-click while that loop
    # is running -- but nothing here noticed it was running: wait_until_player_idle is
    # deliberately position-only and returns True instantly mid-animation (see its own
    # docstring), so every tick sailed straight through it and fired another identical click at
    # the same pixel. CONFIRMED live via the debug log, room 4: three consecutive ticks each
    # clicking Sarcophagus at exactly (1285,801) -- 06:56:09.980 / 06:56:11.720 / 06:56:13.681 --
    # with each of those ticks' own snapshots already reporting anim=4344 BEFORE the click. Same
    # pattern in every other room's sarco step in that log (5 clicks where 1-2 were real).
    #
    # This is the same root cause as quick_leave's and lobby_tick's own fixed bugs: re-issuing an
    # action whose previous attempt has registered but whose effect hasn't landed yet.
    if controller.get_player().get("animation") in SARCOPHAGUS_OPEN_ANIM_IDS:
        waiting_since = ctx.state.data.get("sarco_busy_since")
        if waiting_since is None:
            waiting_since = time.time()
            ctx.state.data["sarco_busy_since"] = waiting_since
        if time.time() - waiting_since < SARCOPHAGUS_BUSY_MAX_WAIT_S:
            debug_log.log("pyramid_plunder", "already attempting to open the sarcophagus -- "
                                             "letting the game's own auto-retry run")
            return
        # Held the animation implausibly long: either the auto-repeat loop was interrupted
        # without the animation clearing, or these ids don't mean what the live log implied.
        # Fall through and re-click rather than waiting on it forever.
        debug_log.log("pyramid_plunder", "sarcophagus open animation stuck -- re-clicking anyway")
    ctx.state.data["sarco_busy_since"] = None

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    action_before = controller.get_last_action().get("tick")
    controller.click_entity(ctx.ser, sarco, check_ui_blockers=False)   # "Open"

    deadline = time.time() + 2.0
    registered = False
    while time.time() < deadline:
        action = controller.get_last_action()
        if action.get("tick") != action_before:
            registered = True
            break
        time.sleep(0.15)

    if not registered:
        debug_log.log("pyramid_plunder", "sarcophagus Open click didn't register -- retrying")
        return
    controller.human_delay(0.4, 0.8)

    refreshed = controller.get_object_at(sarco.get("x"), sarco.get("y"), sarco.get("plane"), sarco.get("id"))
    if refreshed is not None and refreshed.get("impostorId") == SARCOPHAGUS_CLOSED_IMPOSTOR_ID:
        debug_log.log("pyramid_plunder", "sarcophagus Open roll failed -- the game keeps "
                                         "attempting on its own")
        return
    controller.human_delay(1.2, 1.4)  # same settle-time note as the branch above
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


# RAISED 2.0 -> 5.0 2026-08-17. CONFIRMED live via the debug log: the 2.0s window never once
# confirmed -- all three quick-leaves in that session (ticks 12, 80, 214) hit the timeout and
# logged "inRun hasn't cleared yet" even though the leave had in fact gone through, landing the
# player outside within the next tick or two (e.g. click at 06:53:35.255, timeout at
# 06:53:37.491, already outside the pyramid by 06:53:38.991). So the success branch below --
# including its reset_run_state() -- was structurally dead code, the log line was actively
# misleading, and one of the three (tick 12 -> 13) did re-click Quick-leave a second time before
# the first had registered. inRun is observed flipping ~2.2-4.2s after the click; 5.0s covers
# that with real margin while still returning immediately on confirmation.
QUICK_LEAVE_CONFIRM_TIMEOUT_S = 5.0


def quick_leave(ctx, config) -> None:
    """Room 6 (or configured lastRoomToLoot) is fully looted -- end the run via the in-room
    exit door's Quick-leave option rather than walking back out through 6 tomb doors. The next
    tick's phase dispatch (OUTSIDE vs LOBBY) picks up wherever the game actually drops the
    player, so no explicit "where did we land" handling is needed here.

    BUG, FIXED 2026-08-17: click_menu_action returning True only means the "Quick-leave" menu
    entry was found and clicked -- not that the leave has actually taken effect yet. CONFIRMED
    live via the debug log: reset_run_state() used to fire the instant that click registered,
    which -- while the leave was still processing -- sent the routine straight back through
    trap/chest/sarco a second time inside the SAME still-current room (roomNumber/inRun hadn't
    changed yet), redundantly re-clicking an already-searched chest and already-open sarcophagus.
    Harmless once the sarco success-check above is fixed (both no-op correctly on a second pass),
    but still wasted real time every single final room.

    REGRESSION, FIXED 2026-08-17: the first attempt at "poll briefly for inRun to actually flip
    false" added the poll but never gated on its result -- reset_run_state() ran unconditionally
    after the loop regardless of whether it broke early (confirmed) or simply timed out
    (unconfirmed), so a slow-to-process leave still reset immediately, reproducing the exact bug
    the poll was meant to fix. CONFIRMED live via the debug log: two consecutive
    "quick-left ... restarting" messages 4 ticks apart, room_step visibly cycling
    door->trap->chest->sarco->door twice in the same still-current room. Now only resets (and
    logs success) on a CONFIRMED inRun==false; a timeout logs and returns instead, leaving
    room_step untouched so the next tick's door_step naturally retries quick_leave from
    scratch -- no redundant reset, no re-walking the room's own steps."""
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
            deadline = time.time() + QUICK_LEAVE_CONFIRM_TIMEOUT_S
            left = False
            while time.time() < deadline:
                if not controller.get_pyramid_plunder().get("inRun", True):
                    left = True
                    break
                time.sleep(0.15)
            if not left:
                debug_log.log("pyramid_plunder", "quick-leave click registered but inRun hasn't "
                                                  "cleared yet -- retrying")
                return
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


def attempt_door_click(ctx, door: dict) -> str:
    """REWRITTEN 2026-08-17 (user directive, full rework): "lets not worry about transfer to
    another place i fixed it in another way. so now the idea with tomb doors is exactly: Click
    tomb door - wait for xp - xp drop - delay 0.3-0.5 sec - blacklist this door - click next
    door. if we dont get xp we poll chat for that we failed the lockpick." Drops the entire
    Enter-click chain, the position-jump/roomNumber advance detection, and the "leads to a dead
    end" chat check that used to live here -- whether a picked door happens to be the correct
    one and transfers the player is no longer this function's concern at all; tick()'s own
    top-level room-number-change reset is the sole thing that notices and resets the room's
    bookkeeping when that happens. This function now only ever answers one question: did this
    Pick-lock attempt succeed (grant Thieving XP)?

    Returns "picked" (XP confirmed, OR the game's chat confirms this door was already open --
    see the safety-net note below; caller should blacklist this door and move on), "failed" (no
    XP and no "already open" message; a normal, expected, silent roll failure per the wiki --
    caller should just retry the same door), or "missed" (click resolved to a plain "Walk
    here", not a real Pick-lock attempt -- see TOMB_DOOR_AIM_OFFSET's own note)."""
    since_tick = controller.get_last_chat_message().get("tick")
    action_before = controller.get_last_action().get("tick")
    xp_before = controller.get_player().get("thievingXp")
    if not controller.click_entity(ctx.ser, door, offset=TOMB_DOOR_AIM_OFFSET, check_ui_blockers=False):
        return "missed"
    controller.human_delay(0.6, 1.0)

    # CONFIRMED live 2026-08-17: a long-range Tomb Door click can silently resolve to a plain
    # "Walk here" instead of Pick-lock (GET /last-action: menuAction=WALK) -- a genuine aim
    # miss, not a skill-roll failure.
    action = controller.get_last_action()
    if action.get("tick") == action_before or (action.get("menuAction") or "").upper() == "WALK":
        return "missed"

    # Fast path: Thieving XP lands the moment the client's own StatChanged event fires (see
    # CLAUDE.md's "XP gain is another sub-tick, general-purpose signal" note) -- sub-tick and
    # not gated by the 0.6s server tick the way roomNumber/impostorId state is.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        xp = controller.get_player().get("thievingXp")
        if xp is not None and xp_before is not None and xp > xp_before:
            controller.human_delay(0.3, 0.5)
            return "picked"
        time.sleep(0.1)

    # No XP within the window -- per the wiki this is a normal, silent, expected failure roll,
    # but poll chat briefly anyway to confirm it (one combined poll covers two possibilities:
    # a normal failed pick-lock, or the safety-net case below).
    #
    # user-flagged 2026-08-17: safety net for find_room_doors' own dead_ends filter somehow
    # missing this door (e.g. a coordinate-key mismatch) and letting an already-blacklisted,
    # already-picked door get reclicked -- if the game itself confirms via chat that this door
    # is already open, treat that exactly like a fresh "picked" outcome (blacklist and move on)
    # rather than retrying it forever as if it were a normal failed roll.
    def _outcome_message(m):
        text = (m.get("message") or "").lower()
        return ("already" in text and "open" in text) or ("lock" in text and "fail" in text)

    msg = controller.wait_for_chat_message(_outcome_message, since_tick=since_tick, timeout_s=1.0)
    if msg is None:
        debug_log.log("pyramid_plunder", "no XP and no confirming chat message -- treating as a failed lockpick anyway")
        return "failed"

    text = (msg.get("message") or "").lower()
    if "already" in text and "open" in text:
        debug_log.log("pyramid_plunder", f"door was already open (blacklist safety net): {msg.get('message')}")
        return "picked"

    debug_log.log("pyramid_plunder", f"failed the lockpick: {msg.get('message')}")
    return "failed"


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

    REWRITTEN 2026-08-17 (user directive, full rework -- see attempt_door_click's own docstring
    for the "why"): every successfully-picked door gets blacklisted (dead_end_doors) immediately,
    whether or not it turns out to have been the correct one -- if it was correct, the room has
    already changed out from under this bookkeeping by the time anything here would read it
    again, and tick()'s own top-level room-number-change reset clears dead_end_doors for the new
    room anyway. This makes the old "prefer an already-opened door" selection, the Enter-click
    chain, and the door_enter_attempts silent-dead-end tracking all unnecessary -- doors here are
    never anything but Closed candidates, so selection is a plain distance sort. Capped
    implicitly at 4 attempts per room by cycling through the 4 physical wall objects
    (26618-26621) -- the game's own "3 wrong -> 4th always works" rule bounds the worst case.

    door_enter_attempts (the state key, name kept for continuity) now only counts a click that
    resolved to "missed" (aimed at nothing, e.g. a long-range "Walk here") -- a tile this routine
    simply can't reliably click gets blacklisted after DOOR_ENTER_ATTEMPT_LIMIT misses rather
    than stalling the room forever."""
    room = pp.get("roomNumber", 0)
    if room >= config["lastRoomToLoot"]:
        quick_leave(ctx, config)
        return

    dead_ends = {tuple(k) for k in ctx.state.data.get("dead_end_doors", [])}
    doors = find_room_doors(dead_ends)
    if not doors:
        # user directive 2026-08-17: safety net for the room getting stuck with every candidate
        # blacklisted (CONFIRMED live: "no untried tomb doors found -- waiting" repeating for
        # many ticks straight) -- rather than staying stuck forever waiting on a bookkeeping
        # state that can't self-correct, just reset the blacklist and let the room re-resolve
        # from scratch next tick. Cheap: at worst this repeats a genuinely-confirmed dead end
        # once more before re-excluding it.
        debug_log.log("pyramid_plunder", "no untried tomb doors found -- resetting the blacklist and retrying")
        ctx.state.data["dead_end_doors"] = []
        ctx.state.data["door_attempt"] = 0
        ctx.state.data["door_enter_attempts"] = {}
        return

    doors.sort(key=lambda o: o.get("distance", 9999))
    attempt = ctx.state.data.get("door_attempt", 0)
    door = doors[attempt % len(doors)]

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return

    outcome = attempt_door_click(ctx, door)

    if outcome == "picked":
        debug_log.log("pyramid_plunder", f"door attempt {attempt} picked -- blacklisting and trying the next one")
        remembered = list(ctx.state.data.get("dead_end_doors", []))
        remembered.append(list(door_key(door)))
        ctx.state.data["dead_end_doors"] = remembered
        ctx.state.data["door_attempt"] = attempt + 1
        note_progress(ctx)
        return

    if outcome == "failed":
        note_progress(ctx)  # a real attempt happened -- normal, expected, just retry this door
        return

    # outcome == "missed": count per tile, blacklist once DOOR_ENTER_ATTEMPT_LIMIT is hit so an
    # unreachable door doesn't stall the room forever.
    key = door_key(door)
    stalled_attempts = dict(ctx.state.data.get("door_enter_attempts", {}))
    count = stalled_attempts.get(key, 0) + 1
    if count >= DOOR_ENTER_ATTEMPT_LIMIT:
        debug_log.log("pyramid_plunder", f"door attempt {attempt}: the click keeps missing {count}x -- "
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
