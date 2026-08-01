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
#   - SAILING_OPTIONS_TAB_KEY is a guess at the keybind that reveals the Sailing Options panel,
#     same idempotent-tap idea as controller.py's INVENTORY_TAB_KEY/PRAYER_TAB_KEY (#6).
#   - HELM_NAME assumes a plain left-click on the helm resolves to "Navigate" -- if not, add
#     "Navigate" to Controller Settings -> Clicking -> Forced Actions (#5).
#   - SailingProvider.java's "Take down sails" widget lookup is a best-effort text match, not yet
#     confirmed against the live client (#2).

import math
import random
import time

import controller

HELM_NAME = "ship's wheel"
SAILING_OPTIONS_TAB_KEY = "q"  # TODO verify -- see module header

SAIL_HOP_MIN_TILES = 6       # shorter than travel.WALK_HOP_MIN_TILES -- coarse heading means
SAIL_HOP_MAX_TILES = 10      # overshooting a long hop is more likely to need correcting
SAIL_ARRIVAL_TOLERANCE = 5   # tiles; wider than travel.WALK_ARRIVAL_TOLERANCE for the same reason
SAIL_HOP_TIMEOUT_S = 10.0
SAIL_MAX_HOPS = 40
NAVIGATE_TIMEOUT_S = 8.0
STOP_TIMEOUT_S = 15.0


def open_sailing_options_tab(ctx) -> None:
    """Tap the Sailing Options tab hotkey unless it's already open -- same idempotent-tap idea
    as controller.py's open_inventory_tab/open_prayer_tab."""
    if controller.get_sailing().get("facilitiesTabOpen"):
        return
    controller.key_tap(ctx.ser, SAILING_OPTIONS_TAB_KEY)
    time.sleep(random.uniform(0.3, 0.6))


def use_helm(ctx) -> bool:
    """Click the boat's own helm ("Ship's wheel"). Confirmed by the user: this both retrieves
    any currently-deployed salvaging hook and engages steering in one action -- no separate
    stop/retrieve step needed first."""
    helm = next(
        (o for o in controller.get_objects()
         if HELM_NAME in (o.get("name") or "").lower() and o.get("visible", True)),
        None,
    )
    if helm is None:
        print("[sailing] helm not found nearby")
        return False

    print("[sailing] engaging the helm")
    if not controller.click_entity(ctx.ser, helm):
        return False

    waited = 0.0
    while waited < NAVIGATE_TIMEOUT_S:
        time.sleep(0.5)
        waited += 0.5
        if controller.get_sailing().get("navigating"):
            return True

    print("[sailing] clicked the helm but never saw navigate mode engage")
    return False


def steer_toward(ctx, target: dict) -> bool:
    """Drive the boat to within SAIL_ARRIVAL_TOLERANCE tiles of target ({"x", "y", "plane"}),
    same hop-and-click-minimap loop shape as travel._walk_greedy -- confirmed live that minimap
    clicks steer the boat once the helm is engaged, just with coarser (compass-snapped) heading
    than foot travel."""
    target_x, target_y, target_plane = target["x"], target["y"], target["plane"]

    for _ in range(SAIL_MAX_HOPS):
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if plane != target_plane:
            print("[sailing] target is on a different plane -- can't sail there")
            return False

        if _within_tolerance(px, py, target_x, target_y):
            return True

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
        print("[sailing] hop point isn't on the minimap, giving up")
        return False

    controller.click_entity(ctx.ser, point, offset=(0, 0))

    waited = 0.0
    while waited < SAIL_HOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        px, py = player.get("x"), player.get("y")
        if px is not None and _within_tolerance(px, py, x, y):
            break

    return True


def _within_tolerance(x1: float, y1: float, x2: float, y2: float) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= SAIL_ARRIVAL_TOLERANCE


def stop_boat(ctx) -> bool:
    """Open the Sailing Options panel, click Take down sails, and wait for the boat to actually
    stop moving."""
    open_sailing_options_tab(ctx)

    sailing_state = controller.get_sailing()
    button = sailing_state.get("takeDownSails") or {}
    if button.get("screenX") is None:
        print("[sailing] Take down sails button not found")
        return False

    print("[sailing] taking down sails")
    controller.click_entity(ctx.ser, button, offset=(0, 0))

    waited = 0.0
    last_pos = None
    while waited < STOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        pos = (player.get("x"), player.get("y"))
        if pos[0] is not None and pos == last_pos:
            return True
        last_pos = pos

    print("[sailing] never saw the boat settle -- continuing anyway")
    return True


def deploy_hook(ctx, hook_name: str) -> bool:
    """Click the boat's own salvaging hook facility object by name (e.g. "Bronze salvaging
    hook") -- this is the actual salvaging interaction; the shipwreck object itself only has an
    Inspect option."""
    hook = next(
        (o for o in controller.get_objects()
         if hook_name.lower() in (o.get("name") or "").lower() and o.get("visible", True)),
        None,
    )
    if hook is None:
        print(f"[sailing] {hook_name} not found nearby")
        return False

    print(f"[sailing] deploying {hook.get('name')}")
    return controller.click_entity(ctx.ser, hook)


def sail_to(ctx, target: dict) -> bool:
    """Full reposition: engage the helm, steer to target, take down sails. Does not redeploy the
    hook afterward -- the caller decides when that's appropriate."""
    if not use_helm(ctx):
        return False
    if not steer_toward(ctx, target):
        return False
    return stop_boat(ctx)
