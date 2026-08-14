# routines/pest_control.py
#
# Pest Control (Veteran boat) routine: board via the gangplank, wait for the game to start,
# close the gap to the Void Knight's fixed tile, then attack whatever attackable monster is
# closest to it within a 10x10 tile box -- locking onto that target (confirmed via
# LastActionProvider) until it's actually dead, rather than reconsidering every tick.
# Deliberately does NOT target Portals; this is pure guard-duty kill farming next to the Void
# Knight, not an attempt to actually win games faster. No banking, no food/HP management --
# relies on the generic stopOnDeath safety stop like every other routine.
#
# Run with:
#     python controller.py routines/pest_control.py
#
# Phase detection is pure player position, per two known regions (see PlayerProvider.java's
# "regionId" field, added for this routine):
#   - regionId == dockRegionId (10537): dock + boat staging area share this region.
#       - inside stagingBox -> WAITING (boarded, game hasn't started yet): do nothing.
#       - outside stagingBox -> LOBBY (not boarded yet): click the gangplank.
#   - regionId == arenaRegionId (10536): IN_GAME. Run the combat loop every tick.
# Region flips back to dockRegionId when the game ends -> back to LOBBY.
#
# No world-coordinate walking inside the arena -- CONFIRMED live 2026-08-14: the whole arena
# is aboard a nested WorldEntity (see /player's boatX/boatY, still populated even after
# stepping off the physical boat model), and CombatProvider's /minimap/walk and /ground/walk
# have no boat-aware reverse transform for it (both fail to resolve even the player's own
# current tile). Entity screen positions resolve fine regardless -- they come from the
# entity's own LocalPoint, never round-tripping through a WorldPoint -- so click_entity on an
# actual NPC always works. The only gap is the first few seconds after landing, before
# anything attackable has streamed into RuneLite's ~15-tile NPC range at all: see
# step_forward_blind for how that's closed (the user's own live-tested fix). Once something
# is targetable, OSRS's own auto-pathing on an attack click handles the rest -- no explicit
# walk-to-Void-Knight step needed.

import math
import random
import time

import controller
import debug_log
import travel

# User directive 2026-08-14: controller.py's engine-level check_stray_dialog only presses Space
# once per tick, so a multi-stage dialogue (several consecutive "click to continue" pages) only
# advances one stage per call instead of clearing in one shot -- not wrong, just not useful here,
# since this routine's own region-based phase detection doesn't depend on a dialogue being fully
# dismissed to keep working (it already tolerates "unrecognized region" ticks while one lingers).
SKIP_STRAY_DIALOG_CHECK = True

DEFAULT_CONFIG = {
    "enabled": True,

    "gangplankObjectId": 25632,
    "dockRegionId": 10537,
    "arenaRegionId": 10536,

    # Boat staging box, sampled right after boarding (top-left / bottom-right corners --
    # x decreases left-to-right here, so this is normalized to min/max at lookup time,
    # not assumed to already be in min/max order).
    "stagingBoxCorner1": {"x": 2635, "y": 2649},
    "stagingBoxCorner2": {"x": 2632, "y": 2654},

    "voidKnightName": "Void Knight",
    "voidKnightTile": {"x": 2656, "y": 2592},
    "voidKnightBoxHalfWidth": 5,   # tiles each direction -- a 10x10 box centered on the Void Knight

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
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

GANGPLANK_CLICK_COOLDOWN_S = 5.0
STEP_FORWARD_STOP_DISTANCE = 10.0   # tiles from the Void Knight tile -- stop blind-stepping once this close

# Absolute screen pixels of a landmark on the minimap ("the brown object"), captured via ShareX
# right after stepping off the boat -- see step_forward_blind's own docstring for why a minimap
# point (not a viewport one) is used here. Only valid for the client window's current position;
# needs re-capturing if the window is ever moved.
ABS_MINIMAP_CLICK_X = 2365
ABS_MINIMAP_CLICK_Y = 80   # nudged down a few px from the raw ShareX capture (74) -- user report live 2026-08-14


def get_config(bridge_config):
    # No Controller Settings panel for this yet (same as gemstone_crab.py/mining.py) --
    # just use DEFAULT_CONFIG.
    return DEFAULT_CONFIG


def setup(ctx):
    debug_log.log("pest_control", "reminder: add \"Gangplank\" to Controller Settings -> Clicking -> "
                                   "Priority Objects, so a click near it can't resolve to something else "
                                   "on the dock instead.")


def on_event(ctx, event_type, payload):
    if event_type == "NPC_DIED" and payload:
        # Matched by instance index -- a different monster dying nearby must not clear our
        # current target (mirrors slayer.py's own on_event).
        if payload.get("index") == ctx.state.data.get("current_target_index"):
            ctx.state.data["current_target_index"] = None


def tick(ctx):
    config = ctx.state.config
    player = controller.get_player()
    region = player.get("regionId")

    if region == config["arenaRegionId"]:
        in_game_tick(ctx, config)
    elif region == config["dockRegionId"]:
        dock_tick(ctx, config, player)
    else:
        debug_log.log("pest_control", f"unrecognized region {region} -- waiting")


def dock_tick(ctx, config, player):
    if in_staging_box(player, config):
        debug_log.log("pest_control", "on the boat, waiting for the game to start")
        return

    # LOBBY: fresh state for the next game.
    ctx.state.data["current_target_index"] = None
    ctx.state.data["made_contact"] = False

    last_click = ctx.state.data.get("gangplank_clicked_at", 0.0)
    if time.time() - last_click < GANGPLANK_CLICK_COOLDOWN_S:
        return

    # Wait for the player to be genuinely idle, THEN resolve the gangplank fresh, THEN click --
    # same ordering as mastering_mixology.py's _click_world_object (user directive to gate
    # every action behind player stillness): resolving before the wait risks clicking a
    # position/entity that was only valid before the player finished settling. Skip this tick
    # entirely on timeout (still moving) rather than clicking anyway -- CONFIRMED live
    # 2026-08-14 elsewhere in this file (step_forward_blind) that an unchecked timeout
    # reintroduces the exact "click while moving" bug this gate exists to prevent.
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    gangplank = find_object_by_id(controller.get_objects(), config["gangplankObjectId"])
    if not gangplank:
        debug_log.log("pest_control", "gangplank not visible")
        return

    debug_log.log("pest_control", "boarding the boat")
    time.sleep(random.uniform(0.2, 10.0))
    click_without_rotating(ctx, gangplank)
    ctx.state.data["gangplank_clicked_at"] = time.time()


def in_game_tick(ctx, config):
    combat = controller.get_combat()
    npcs = controller.get_npcs()
    # Fixed anchor, not a live NPC lookup -- user-provided exact tile the Void Knight always
    # stands on. Avoids the RuneLite ~15-tile NPC-streaming delay entirely (no "not visible
    # yet" gap to wait out for the anchor itself), and never needs a fallback.
    anchor = config["voidKnightTile"]

    # Locked onto a target from a previous tick -- user directive: commit to it (verified via
    # click_entity's own confirmed attack) until it's actually dead, rather than re-evaluating
    # "closest to the Void Knight" every tick and abandoning a partly-damaged monster for a
    # closer one. on_event's NPC_DIED handler already clears this the instant the game reports
    # the death (faster than this poll), but hpRatio==0 catches it too in case that event is
    # ever missed, and "gone from /npcs entirely" catches despawn without a health bar ever
    # having been shown.
    locked_index = ctx.state.data.get("current_target_index")
    if locked_index is not None:
        locked = find_npc_by_index(npcs, locked_index)
        if locked is None:
            debug_log.log("pest_control", "locked target is gone -- releasing")
            ctx.state.data["current_target_index"] = None
        elif locked.get("hpRatio", -1) == 0:
            debug_log.log("pest_control", f"{locked.get('name')} is dead -- releasing")
            ctx.state.data["current_target_index"] = None
        else:
            return  # still alive -- stay committed, no re-evaluation

    target = find_closest_to(npcs, anchor, config)

    if not target:
        if ctx.state.data.get("made_contact"):
            # Already reached the Void Knight's cluster once this game -- a temporary lull
            # (everything nearby just died) is not the same as "still crossing the landing
            # gap". User directive: stay put near the Void Knight rather than continuing to
            # blind-step further and further away hunting for the next monster.
            debug_log.log("pest_control", "no monsters nearby -- waiting")
            return

        # CONFIRMED live 2026-08-14: a single step_forward_blind click can cover a large
        # distance (the aim point near the top of the viewport projects to a tile well ahead,
        # given the camera's pitch) -- without this check, "made_contact" alone let the player
        # blind-step 7+ times in a row, well past the designated tile, before any monster
        # happened to be both in the 10x10 box AND within NPC-streaming range of wherever that
        # run ended up. Stop advancing once genuinely close to the tile, whether or not
        # anything's been spotted yet -- a temporary empty box just means wait, not "keep
        # walking south".
        player = controller.get_player()
        if distance(player, config["voidKnightTile"]) <= STEP_FORWARD_STOP_DISTANCE:
            debug_log.log("pest_control", "close to the Void Knight tile -- waiting instead of stepping further")
            return

        debug_log.log("pest_control", "nothing targetable yet -- stepping forward")
        step_forward_blind(ctx, config)
        return

    ctx.state.data["made_contact"] = True

    # Wait for the player to be genuinely idle, THEN re-resolve the target fresh, THEN click --
    # same ordering as mastering_mixology.py's _click_world_object (user directive to gate
    # every action behind player stillness). `target` above was only used to decide THAT
    # something is attackable; re-fetching after the wait avoids clicking a screen position
    # that was only valid before the player finished settling from a prior action. Skip this
    # tick entirely on timeout rather than clicking anyway -- same reasoning as
    # step_forward_blind and dock_tick's gangplank click.
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    npcs = controller.get_npcs()
    target = find_closest_to(npcs, anchor, config)
    if not target:
        return  # died or moved out of range during the wait -- picked up fresh next tick

    if travel.maybe_approach_before_click(ctx, target, already_engaged=combat.get("inCombat", False),
                                           via_minimap=False):
        return

    debug_log.log("pest_control", f"attacking {target.get('name')} (dist to Void Knight="
                                   f"{distance(target, anchor):.1f})")
    time.sleep(random.uniform(0.15, 0.4))
    since_tick = controller.get_last_action().get("tick")
    if not click_without_rotating(ctx, target):
        return  # nulled (UI-blocked) or otherwise failed -- next tick re-resolves and retries
    if confirm_attack_click(target, since_tick):
        ctx.state.data["current_target_index"] = target.get("index")
    else:
        debug_log.log("pest_control", f"click on {target.get('name')} didn't land -- will retry")


def confirm_attack_click(target: dict, since_tick, timeout_s: float = 2.0, poll_interval_s: float = 0.1) -> bool:
    """Polls /last-action (LastActionProvider.java) for the game's own confirmation that the
    click just issued actually resolved to an Attack on `target`, rather than assuming it
    landed. Matches against both "id" and "index" -- CONFIRMED live 2026-08-14 that RuneLite's
    MenuOptionClicked "id" for an NPC target is the NPC's index, not its species id, but
    matching either costs nothing and needs no assumption about which a given client build
    uses.

    Keeps polling past the first newer-tick action seen instead of judging on it alone --
    CONFIRMED live 2026-08-14 this was a real bug: click_entity's own misclick-simulation/
    reclick-correction (see controller.py) can produce an unrelated intermediate menu action
    (e.g. a "Walk here" from the deliberate near-miss) with a newer tick than the real Attack
    that follows it, which an early return would misreport as "didn't land" even though combat
    was genuinely underway (observed live: target's own /npcs entry showed hpRatio dropping,
    inCombat=true, right after this function had already reported failure)."""
    deadline = time.time() + timeout_s
    last_seen_tick = since_tick
    while time.time() < deadline:
        action = controller.get_last_action()
        tick = action.get("tick")
        if tick is not None and (last_seen_tick is None or tick > last_seen_tick):
            last_seen_tick = tick
            option = (action.get("option") or "").lower()
            if "attack" in option and action.get("id") in (target.get("id"), target.get("index")):
                return True
        time.sleep(poll_interval_s)
    return False


def find_npc_by_index(npcs: list, index):
    for n in npcs:
        if n.get("index") == index:
            return n
    return None


def click_without_rotating(ctx, entity: dict, **kwargs) -> bool:
    """click_entity, but never lets a UI-blocked click trigger controller.py's default
    rotate_camera_away_from_ui response -- user directive 2026-08-14: this routine's
    step_forward_blind depends on a hardcoded minimap screen position (ABS_MINIMAP_CLICK_X/Y)
    that only stays valid as long as the camera's yaw never changes. Any click in this routine
    landing behind a UI panel would otherwise silently rotate the camera and invalidate that
    coordinate for the rest of the game. Nulls the click instead (skip it, return False) --
    the caller just retries next tick, same as any other "click didn't land" case already
    handled elsewhere in this file (e.g. confirm_attack_click's own retry)."""
    screen_x, screen_y = entity.get("screenX"), entity.get("screenY")
    if screen_x is not None and screen_y is not None:
        blocker = controller.is_point_blocked_by_ui(int(screen_x), int(screen_y))
        if blocker is not None:
            debug_log.log("pest_control", f"{entity.get('name')} is behind a UI panel -- skipping click, not rotating")
            return False
    # check_ui_blockers=False -- our own pre-check above is the only gate; disabling
    # click_entity's internal one guarantees rotate_camera_away_from_ui can never fire from
    # this routine even in the narrow window between the pre-check and this call (e.g. a UI
    # panel opening in that instant).
    return controller.click_entity(ctx.ser, entity, check_ui_blockers=False, **kwargs)


def step_forward_blind(ctx, config):
    """Click a fixed, hardcoded point on the minimap -- user directive 2026-08-14: the previous
    version clicked a point computed from the 3D viewport instead, which walks a wildly
    inconsistent distance per click depending on camera pitch (CONFIRMED live: single clicks
    ranged from ~4 to ~12+ tiles), causing repeated overshoot past the Void Knight tile. A
    minimap click doesn't have that problem -- the player is always drawn at the minimap's own
    center, so a fixed screen offset from it represents the same real-world direction and
    distance on every single click, session to session, as long as the client window doesn't
    move. ABS_MINIMAP_CLICK_X/Y is the user's own ShareX-captured screen position of a landmark
    on the minimap ("the brown object", large and hard to miss) visible right after stepping off
    the boat -- sanity-checked live against /state's windowX/Y/Width/Height (2026-08-14) to
    confirm it actually falls on the minimap widget in this window's current position.

    Waits for the player to be genuinely idle first -- same ordering as mastering_mixology.py's
    _click_world_object (user directive to gate every action behind player stillness): without
    this, a still-settling player from the PREVIOUS blind step could get a fresh click stacked
    on top of it every tick instead of one clean step at a time. timeout_s=10.0 (not
    wait_until_player_idle's own 5.0 default) and checked before clicking -- CONFIRMED live
    2026-08-14: a single minimap click can cover ~16 tiles, which took longer than 5s to fully
    settle and previously timed out, and the old code clicked again regardless of the timeout,
    stacking a fresh click on a still-moving player -- exactly the bug this whole gate exists to
    prevent. On a still-moving player (timeout), skip this tick's click entirely and let the
    next tick's fresh wait pick up the wait where this one left off.

    Re-checks the distance-to-tile stop condition AFTER the wait, right before clicking --
    CONFIRMED live 2026-08-14 this was a real bug, not just a timeout edge case: the routine's
    own loop can re-enter in_game_tick, and decide a second step is needed, BEFORE the FIRST
    click's walk has even started moving -- that decision was correct in the instant it was
    made, but then this function's wait_until_player_idle blocks through the entire first
    walk, and used to fire the already-decided second click unconditionally once it settled,
    even though the first click may have already closed the gap. Since every click represents
    the same fixed direction+distance, an unnecessary second click compounds straight past the
    tile instead of correcting toward it. Skip the click entirely if we're already close enough
    by the time the wait actually finishes.

    Random 1-5s pause right before the click itself (user directive 2026-08-14) -- purely
    humanization, on top of (not instead of) the idle-wait above."""
    if not controller.wait_until_player_idle(timeout_s=10.0):
        return

    player = controller.get_player()
    if distance(player, config["voidKnightTile"]) <= STEP_FORWARD_STOP_DISTANCE:
        debug_log.log("pest_control", "already close enough after settling -- skipping this step")
        return

    time.sleep(random.uniform(1.0, 5.0))
    point = {"screenX": ABS_MINIMAP_CLICK_X, "screenY": ABS_MINIMAP_CLICK_Y, "name": "minimap landmark"}
    click_without_rotating(ctx, point, offset=(0, 0))


def in_staging_box(player, config):
    px, py = player.get("x"), player.get("y")
    if px is None or py is None:
        return False
    c1, c2 = config["stagingBoxCorner1"], config["stagingBoxCorner2"]
    min_x, max_x = sorted((c1["x"], c2["x"]))
    min_y, max_y = sorted((c1["y"], c2["y"]))
    return min_x <= px <= max_x and min_y <= py <= max_y


def find_object_by_id(objects: list, object_id: int):
    candidates = [o for o in objects if o.get("id") == object_id and o.get("visible", True)]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def distance(a: dict, b: dict) -> float:
    return math.hypot(a.get("x", 0) - b.get("x", 0), a.get("y", 0) - b.get("y", 0))


def find_closest_to(npcs: list, anchor: dict, config: dict):
    """Closest attackable, non-Void-Knight NPC to `anchor` (the Void Knight's fixed tile) within
    a 10x10 tile box centered on it -- user directive: stay near the Void Knight rather than
    chasing something far across the arena. This is what makes target switching ignore whether
    the player has "finished" the current monster: Pest Control is a multi-combat area, so
    simply always going for whatever's nearest within the box is both simplest and keeps the
    Void Knight itself safest."""
    void_knight_name = config["voidKnightName"].lower()
    half_width = config["voidKnightBoxHalfWidth"]
    ax, ay = anchor.get("x", 0), anchor.get("y", 0)
    candidates = [
        n for n in npcs
        if n.get("visible", True)
        and n.get("attackable", True)
        and (n.get("name") or "").lower() != void_knight_name
        and abs(n.get("x", 0) - ax) <= half_width
        and abs(n.get("y", 0) - ay) <= half_width
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda n: distance(n, anchor))
    return candidates[0]
