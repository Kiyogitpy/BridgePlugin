# sailing.py
#
# Boat-specific navigation, parallel to travel.py's foot-travel helpers. On open ocean there's
# no click-to-walk pathing -- moving the boat needs the helm engaged first. Steering itself
# reuses the same minimap-click mechanism travel.py already uses for foot travel (confirmed live
# by the user: minimap clicks drive the boat once the helm is engaged), just with coarser
# heading -- it snaps to a handful of compass directions instead of plotting a precise line to
# the clicked point, hence the shorter hop distance and wider arrival tolerance below compared
# to travel.py's own WALK_HOP_MIN/MAX_TILES and WALK_ARRIVAL_TOLERANCE.
#
# ASSUMPTIONS TO VERIFY IN-GAME (same convention as travel.py's own header; numbers refer to
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md's "Still unverified" section):
#   - SAIL_HOP_MIN/MAX_TILES and SAIL_ARRIVAL_TOLERANCE below are a starting guess at how coarse
#     the boat's heading snapping actually is -- tune once observed live (#1).
#   - SAILING_OPTIONS_TAB_KEY: CONFIRMED live 2026-08-01, "q" is correct (#6, resolved).
#   - HELM_NAME: CONFIRMED live 2026-08-01 -- the boat's steering control is an object literally
#     named "Helm" (id 59578, not the wiki's "Ship's wheel", and not any of the ObjectProvider ids
#     guessed earlier from the stale source checkout). Confirmed via the live cache (not just the
#     stale checkout): its ops are {0: "Navigate", 3: "Escape"} -- index 0 is this codebase's
#     established left-click-default convention (same as the salvaging hook's 0: "Deploy"), so a
#     plain click_entity should resolve straight to Navigate with no Forced Actions tweak needed.
#     Resolves #5. It was initially misidentified as the "Sails" object (the only object
#     ObjectProvider reported at the player's exact tile at the time) -- that attribution was
#     wrong: "Helm" wasn't showing up in /objects at all yet because of the plane-walking gap
#     described next, not because it doesn't exist, so the right-click result seen then was very
#     likely actually on "Helm" all along, just misattributed to whatever WAS visible nearby.
#   - CONFIRMED live 2026-08-02 (#2): the sail control is a single untextted toggle icon, not two
#     separately-labeled buttons -- see stop_boat()'s docstring and SailingProvider.java for the
#     full story (a text-match approach could never have worked here at all).

import math
import random
import time

import controller
import debug_log
import ocean_path

HELM_NAME = "helm"  # CONFIRMED live 2026-08-01 -- see module header
SAILING_OPTIONS_TAB_KEY = "q"  # TODO verify -- see module header

SAIL_HOP_MIN_TILES = 6       # shorter than travel.WALK_HOP_MIN_TILES -- coarse heading means
SAIL_HOP_MAX_TILES = 10      # overshooting a long hop is more likely to need correcting
SAIL_ARRIVAL_TOLERANCE = 5   # tiles; wider than travel.WALK_ARRIVAL_TOLERANCE for the same reason
SAIL_HOP_TIMEOUT_S = 10.0
SAIL_MAX_HOPS = 40
NAVIGATE_TIMEOUT_S = 8.0
STOP_TIMEOUT_S = 15.0

# CONFIRMED live 2026-08-02: steer_toward previously only stopped hopping once within
# SAIL_ARRIVAL_TOLERANCE (5 tiles) of the target -- but the sails stay up and the boat keeps
# coasting under full sail the entire approach, so by the time sail_to finally called stop_boat,
# the boat was already point-blank, and stop_boat's own measured post-toggle drift (2.2-5.0 tiles,
# two live samples) carried it into the wreck (a real collision, not cosmetic). Now steer_toward
# stops hopping -- and sail_to calls stop_boat -- once within THIS wider distance instead, letting
# the already-measured drift close the remaining gap and land the boat short of the target instead
# of through it. Initial estimate (not yet live-tuned): bigger than the max observed drift (5.0)
# with margin, small enough that the resulting stop still lands within hook range (see
# shipwreck_salvaging.py's WRECK_SQUARE_SIZE for the callers that care about this specifically).
#
# Kept only as the fallback when a live speed reading isn't available (see _dynamic_brake_tiles
# below) -- SailingProvider.java now measures the boat's actual per-tick speed
# (WorldEntity.getTargetLocation() deltas, same approach the sideloaded Sailing plugin's own speed
# readout uses), so the brake trigger no longer has to be one flat guess covering the boat's full-
# sail top speed regardless of how fast it's actually moving right now.
SAIL_BRAKE_TRIGGER_TILES = 10

BRAKE_LOOKAHEAD_TICKS = 3  # UNVERIFIED -- initial guess for how many ticks' worth of the boat's
                            # CURRENT measured speed it keeps coasting for after sails come down.
                            # stop_boat() now logs the measured speed at the moment of toggling
                            # right next to its own drift-tiles measurement specifically so this
                            # can be fitted against real paired data once a few live samples exist
                            # (drift_tiles ~= speed_tiles_per_tick * actual_coast_ticks), same
                            # convention as every other unverified constant in this file.

STUCK_HOP_LIMIT = 3  # consecutive hops with no meaningful position change before steer_toward
                       # gives up on the current leg and reports failure (False), letting sail_to
                       # replan a fresh route instead of grinding through all SAIL_MAX_HOPS against
                       # a wall. CONFIRMED live 2026-08-02: running aground is a hard dead stop,
                       # not something that resolves itself by retrying the same heading.
STUCK_MOVEMENT_THRESHOLD_TILES = 1.5  # position change below this, across one hop, doesn't count
                                        # as real progress -- generous enough to allow for the
                                        # boat's own post-hop settle jitter.
MAX_ROUTE_ATTEMPTS = 3  # how many times sail_to will re-plan a fresh route after a leg reports
                          # getting stuck, before giving up and reporting failure -- same
                          # bounded-retry philosophy as SAIL_MAX_HOPS.

# Radius passed to /objects when looking up boat fixtures (helm, salvaging hook) -- CONFIRMED
# live 2026-08-02: an unbounded full-scene walk building screen-projection data for every object
# in the loaded scene, every game tick, was a real source of client stutter. These fixtures live
# in the boat's own nested WorldView, which ObjectProvider always walks in full regardless of
# radius (it's tiny -- see ObjectProvider.java), so this only trims the unrelated top-level ocean
# scan; a boat's WorldView is ~8 tiles per side (confirmed live), so 20 is comfortable margin.
BOAT_OBJECT_SEARCH_RADIUS = 20


def open_sailing_options_tab(ctx) -> None:
    """Tap the Sailing Options tab hotkey unless it's already open -- same idempotent-tap idea
    as controller.py's open_inventory_tab/open_prayer_tab."""
    if controller.get_sailing().get("facilitiesTabOpen"):
        return
    controller.key_tap(ctx.ser, SAILING_OPTIONS_TAB_KEY)
    controller.human_delay(0.3, 0.6)


def use_helm(ctx) -> bool:
    """Click the boat's own steering control (an object literally named "Helm" -- confirmed live,
    not the wiki's "Ship's wheel"; see module header). Confirmed by the user: this both retrieves
    any currently-deployed salvaging hook and engages steering in one action -- no separate
    stop/retrieve step needed first."""
    helm = next(
        (o for o in controller.get_objects(radius=BOAT_OBJECT_SEARCH_RADIUS)
         if HELM_NAME in (o.get("name") or "").lower() and o.get("visible", True)),
        None,
    )
    if helm is None:
        debug_log.log("sailing", "helm not found nearby")
        return False

    debug_log.log("sailing", "engaging the helm")
    if not controller.click_entity(ctx.ser, helm):
        return False

    waited = 0.0
    while waited < NAVIGATE_TIMEOUT_S:
        time.sleep(0.5)
        waited += 0.5
        if controller.get_sailing().get("navigating"):
            return True

    debug_log.log("sailing", "clicked the helm but never saw navigate mode engage")
    return False


def steer_toward(ctx, target: dict, arrival_tolerance: float = None) -> bool:
    """Drive the boat to within arrival_tolerance tiles of target ({"x", "y", "plane"}), same
    hop-and-click-minimap loop shape as travel._hop_to -- confirmed live that minimap clicks
    steer the boat once the helm is engaged, just with coarser (compass-snapped) heading than foot
    travel.

    arrival_tolerance=None (the default) recomputes a braking distance from the boat's CURRENT
    measured speed on every hop instead of using one fixed number -- see _dynamic_brake_tiles.
    Recomputed per-hop rather than once up front because speed genuinely changes hop to hop
    (accelerating away from a stop, cruising at full sail, slowed by an obstacle-avoidance leg,
    ...), so a single snapshot taken before the loop started could be stale by the time it matters.

    A wide (not the tighter SAIL_ARRIVAL_TOLERANCE) default in general -- CONFIRMED live
    2026-08-02: the boat keeps coasting under full sail for as long as this function keeps
    running, so returning only once already within the tight tolerance left no room to brake
    before ramming the target. sail_to calls stop_boat() immediately after the FINAL leg of a
    route returns, and the already-measured post-toggle drift closes the remaining distance.
    Intermediate route waypoints (see sail_to) pass the tighter SAIL_ARRIVAL_TOLERANCE explicitly
    instead -- they exist specifically to route around an obstacle, so cutting the corner early
    would defeat the point.

    Returns False (instead of exhausting all SAIL_MAX_HOPS) once STUCK_HOP_LIMIT consecutive hops
    show no meaningful position change -- CONFIRMED live 2026-08-02: running aground on an island
    is a hard dead stop, not something that resolves itself by continuing to retry the same
    heading. sail_to treats this as a signal to re-plan a fresh route from the current position."""
    target_x, target_y, target_plane = target["x"], target["y"], target["plane"]
    last_pos = None
    stuck_hops = 0

    for _ in range(SAIL_MAX_HOPS):
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if plane != target_plane:
            debug_log.log("sailing", "target is on a different plane -- can't sail there")
            return False

        current_tolerance = arrival_tolerance if arrival_tolerance is not None else _dynamic_brake_tiles()
        if _within_tolerance(px, py, target_x, target_y, current_tolerance):
            return True

        if last_pos is not None and _within_tolerance(px, py, last_pos[0], last_pos[1], STUCK_MOVEMENT_THRESHOLD_TILES):
            stuck_hops += 1
            if stuck_hops >= STUCK_HOP_LIMIT:
                debug_log.log("sailing", "stuck -- position hasn't changed across repeated hops, likely aground")
                return False
        else:
            stuck_hops = 0
        last_pos = (px, py)

        dist = math.hypot(target_x - px, target_y - py)
        hop_dist = min(dist, random.randint(SAIL_HOP_MIN_TILES, SAIL_HOP_MAX_TILES))
        hop_x = int(round(px + (target_x - px) / dist * hop_dist))
        hop_y = int(round(py + (target_y - py) / dist * hop_dist))

        if not _hop_to(ctx, hop_x, hop_y, plane):
            return False

    return False


def _hop_to(ctx, x: int, y: int, plane: int) -> bool:
    point = controller.get_minimap_walk_point(x, y, plane)
    if not point.get("onMinimap"):
        debug_log.log("sailing", "hop point isn't on the minimap, giving up")
        return False

    if not controller.click_entity(ctx.ser, point, offset=(0, 0)):
        # click_entity already printed why (blocked by UI, off-window, ...) -- flag it here too so a
        # later "stuck -- likely aground" isn't the only signal: the boat may never have gotten a
        # steering click at all, rather than genuinely running aground.
        debug_log.log("sailing", "minimap click didn't land -- boat won't have moved this hop")

    waited = 0.0
    while waited < SAIL_HOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        px, py = player.get("x"), player.get("y")
        if px is not None and _within_tolerance(px, py, x, y):
            break

    return True


def _within_tolerance(x1: float, y1: float, x2: float, y2: float, tolerance: float = SAIL_ARRIVAL_TOLERANCE) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= tolerance


def _dynamic_brake_tiles() -> float:
    """Distance (tiles) steer_toward should stop hopping at, scaled to the boat's CURRENT measured
    speed (SailingProvider.java's boatSpeed, tiles/tick from WorldEntity.getTargetLocation() deltas
    -- CONFIRMED live to be the right data source, see its own comment) instead of one flat
    worst-case guess. Never below SAIL_ARRIVAL_TOLERANCE -- a stopped or barely-moving boat still
    needs to actually reach the target, not stop short because the speed-scaled distance rounded
    to near zero. Falls back to the old flat SAIL_BRAKE_TRIGGER_TILES whenever a live speed
    reading isn't available (older bridge build, or boatSpeed missing for any other reason) --
    same safety net that constant always was."""
    speed = controller.get_sailing().get("boatSpeed")
    if speed is None:
        return SAIL_BRAKE_TRIGGER_TILES
    return max(SAIL_ARRIVAL_TOLERANCE, speed * BRAKE_LOOKAHEAD_TICKS)


def stop_boat(ctx, target: dict = None) -> bool:
    """Open the Sailing Options panel and click the sail toggle to un-set the sails, then wait
    for the boat to actually stop moving. CONFIRMED live 2026-08-02: this isn't two separately
    labeled buttons ("Take down sails"/"Set sail", the original guess) -- it's a single toggle
    icon (no text of its own) whose menu action just flips between "Set sails" and "Un-set sails"
    depending on current state, so clicking it here always resolves to whichever one is actually
    valid right now (matching the confirmed-safe re-click precedent for the salvaging hook).

    target: the point steer_toward was actually trying to land on (sail_to passes its own target
    through), logged alongside the drift measurement below as the real overshoot-from-target --
    the more direct number for fitting BRAKE_LOOKAHEAD_TICKS against than drift-from-toggle-point
    alone, since that only measures how far the boat coasted, not whether it landed short of or
    past where it was actually aiming. None (e.g. no caller-supplied target) just skips that part
    of the log line."""
    open_sailing_options_tab(ctx)

    sailing_state = controller.get_sailing()
    button = sailing_state.get("sailToggle") or {}
    if button.get("screenX") is None:
        debug_log.log("sailing", "sail toggle not found")
        return False

    start_player = controller.get_player()
    start_pos = (start_player.get("x"), start_player.get("y"))
    speed_at_toggle = sailing_state.get("boatSpeed")

    debug_log.log("sailing", "toggling sails")
    controller.click_entity(ctx.ser, button, offset=(0, 0))

    waited = 0.0
    last_pos = None
    while waited < STOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        pos = (player.get("x"), player.get("y"))
        if pos[0] is not None and pos == last_pos:
            # Drift measurement (2026-08-02): the boat keeps coasting for a bit after un-setting
            # sails rather than stopping instantly (confirmed live), which can carry it well past
            # where steer_toward's SAIL_ARRIVAL_TOLERANCE said "close enough, stop now". Logging
            # the actual distance to see how much to compensate for -- e.g. triggering the stop
            # earlier, or padding the tolerance -- once this has been observed a few times live.
            #
            # speed_at_toggle logged alongside it since 2026-08-04 (boatSpeed added to
            # SailingProvider) -- pairs each drift sample with the actual measured speed that
            # produced it, giving real data to fit BRAKE_LOOKAHEAD_TICKS against
            # (drift_tiles ~= speed_at_toggle * actual_coast_ticks) instead of guessing.
            #
            # overshoot (added 2026-08-04, boat-upgrade speed increase): distance from where the
            # boat actually settled to the target it was aiming for. drift alone can't tell
            # whether BRAKE_LOOKAHEAD_TICKS's linear speed*ticks model still holds now that the
            # boat is faster -- overshoot is the direct "how far past/short of center did we land"
            # number needed to refit it.
            if start_pos[0] is not None and pos[0] is not None:
                drift = math.hypot(pos[0] - start_pos[0], pos[1] - start_pos[1])
                msg = f"stopped -- drifted {drift:.1f} tiles after toggling sails (speed at toggle: {speed_at_toggle})"
                if target is not None and target.get("x") is not None:
                    overshoot = math.hypot(pos[0] - target["x"], pos[1] - target["y"])
                    msg += f" -- {overshoot:.1f} tiles from target"
                debug_log.log("sailing", msg)
            return True
        last_pos = pos

    debug_log.log("sailing", "never saw the boat settle -- continuing anyway")
    return True


def deploy_hook(ctx, hook_name: str) -> bool:
    """Click the boat's own salvaging hook facility object by name (e.g. "Bronze salvaging
    hook") -- this is the actual salvaging interaction; the shipwreck object itself only has an
    Inspect option.

    offset=(0, 0): CONFIRMED live 2026-08-02 -- click_entity's default offset=(-7, 7) is a blind
    pixel nudge tuned for other entity types, and on the boat's cramped deck it was carrying the
    aim point off the hook's clickbox and onto the physical 3D sail model rendered right next to
    it, toggling the sails exactly as if the UI sail-toggle button had been clicked and sending
    the routine into a cascading fail loop. ObjectProvider's screenX/Y already comes from
    pointInsideShape (a point verified to be inside the actual clickbox, not a bounding-box
    corner), so no extra offset is needed here -- same reasoning as stop_boat's own sail-toggle
    click, which already used offset=(0, 0) for the same reason."""
    hook = next(
        (o for o in controller.get_objects(radius=BOAT_OBJECT_SEARCH_RADIUS)
         if hook_name.lower() in (o.get("name") or "").lower() and o.get("visible", True)),
        None,
    )
    if hook is None:
        debug_log.log("sailing", f"{hook_name} not found nearby")
        return False

    debug_log.log("sailing", f"deploying {hook.get('name')}")
    return controller.click_entity(ctx.ser, hook, offset=(0, 0))


def sail_to(ctx, target: dict) -> bool:
    """Full reposition: engage the helm, plan an obstacle-aware route (falling back to a direct
    line if no route is needed or none can be found), steer through it leg by leg, take down
    sails at the end. Does not redeploy the hook afterward -- the caller decides when that's
    appropriate.

    The final leg always uses the speed-scaled braking distance (steer_toward's
    arrival_tolerance=None / _dynamic_brake_tiles), regardless of whether target is a solid
    object or open water (e.g. a computed midpoint between two wrecks) -- stop_boat's post-toggle
    coast happens either way and scales with current speed either way, so a flat, unscaled
    tolerance for the "no ram risk" case just meant the boat kept overshooting the target by
    however much the flat number under-covered the real drift. There used to be a `brake` flag
    that skipped the speed-scaled distance for open-water targets in favor of a flat
    SAIL_ARRIVAL_TOLERANCE, reasoning that precision mattered more than collision margin there --
    CONFIRMED live 2026-08-04 (user report, after a boat speed upgrade): that flat tolerance
    doesn't account for the boat now coasting farther post-toggle, so it was landing well past a
    computed pair-midpoint instead of near it. Removed -- there's no case where the flat,
    speed-blind distance is more correct than the measured one. (Intermediate route waypoints
    still pass the tighter SAIL_ARRIVAL_TOLERANCE explicitly -- they exist specifically to route
    around an obstacle, not to land precisely, so cutting the corner early would defeat the
    point.)

    Re-plans from the current position (up to MAX_ROUTE_ATTEMPTS times) if a leg reports getting
    stuck -- CONFIRMED live 2026-08-02: running aground is a hard dead stop needing a real course
    change to recover from, not something retrying the exact same heading resolves on its own.

    Does nothing at all -- not even engaging the helm -- if the boat is already within arrival
    distance of target. CONFIRMED live 2026-08-05 (user report, "overshot again"): a caller can
    pass a target the boat is already sitting on or very near (e.g. shipwreck_salvaging.py asking
    to reposition slightly when it's already about as close as it should safely get), and
    steer_toward would then see itself as already arrived and issue zero hop clicks -- but
    use_helm() had already unconditionally re-engaged steering before that check ever ran, with no
    fresh minimap click ever telling the boat where to go, letting it resume moving on whatever
    stale heading the game still had queued and drift tens of tiles in the wrong direction. Only a
    target requiring an actual new hop should ever touch the helm at all."""
    player = controller.get_player()
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is not None and plane == target.get("plane") and _within_tolerance(px, py, target["x"], target["y"], _dynamic_brake_tiles()):
        return True

    if not use_helm(ctx):
        return False

    for attempt in range(MAX_ROUTE_ATTEMPTS):
        player = controller.get_player()
        start = {"x": player.get("x"), "y": player.get("y"), "plane": player.get("plane")}
        if start["x"] is None:
            return False

        route = ocean_path.find_ocean_route(start, target)
        legs = route if route else [target]

        stuck = False
        for i, leg in enumerate(legs):
            is_final_leg = (i == len(legs) - 1)
            tolerance = None if is_final_leg else SAIL_ARRIVAL_TOLERANCE
            if not steer_toward(ctx, leg, arrival_tolerance=tolerance):
                debug_log.log("sailing", f"got stuck heading to a waypoint (attempt {attempt + 1}/{MAX_ROUTE_ATTEMPTS}) -- replanning")
                stuck = True
                break

        if not stuck:
            return stop_boat(ctx, target)

    debug_log.log("sailing", "couldn't find a working route after repeated attempts -- giving up")
    return False
