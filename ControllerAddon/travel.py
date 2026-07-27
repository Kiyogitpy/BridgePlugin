# travel.py
#
# Location memory + long-distance travel. Lives alongside controller.py
# (not in routines/) since it's shared infrastructure other routines/
# banker.py import, the same reason `import controller` already works from
# any routine file.
#
# What's here:
#   save_location()      -- record the current world tile before leaving
#   teleport_to_ferox()  -- ring of dueling -> Ferox Enclave
#   use_fountain()       -- click Ferox's free HP/prayer/special fountain
#   walk_to(target)      -- get back to a saved location
#
# walk_to routes through the (separately installed) Shortest Path hub
# plugin: it posts a PluginMessage asking it to compute an obstacle-aware
# route, polls for the result, then walks that route by repeatedly clicking
# a waypoint some tiles ahead via the minimap and letting the game's own
# pathfinder handle each short hop -- the look-ahead distance is randomized
# per hop (never below WALK_HOP_MIN_TILES) rather than a fixed number, so
# consecutive walks don't look identical. If Shortest Path isn't installed,
# didn't finish in time, or the route turned out to be unusable, this falls
# back to a straight-line greedy walk toward the target (same hop-and-click
# trick, no obstacle awareness -- fine over the open terrain around Ferox
# Enclave, not a real substitute for the computed route).
#
# ASSUMPTIONS TO VERIFY IN-GAME:
#   - The ring of dueling's Ferox Enclave option is a direct right-click
#     option on the item (no "Rub" submenu first). For a plain left-click
#     to resolve to it, add "Ferox Enclave" to Controller Settings ->
#     Clicking -> Forced Actions. (Confirmed correct.)

import math
import random
import time

import controller

RING_OF_DUELING_NAME = "ring of dueling"
FOUNTAIN_NAME = "Pool of Refreshment"
FOUNTAIN_MAX_DISTANCE = 15

# Royal seed pod: used as the wilderness panic-escape teleport specifically
# because (recalled, not re-verified in-game this session) it still works
# while teleblocked, unlike most other teleports -- the whole point of an
# escape item used only when a PKer is already in attack range.
ROYAL_SEED_POD_NAME = "royal seed pod"

WALK_HOP_MIN_TILES = 10      # never aim a hop shorter than this
WALK_HOP_MAX_TILES = 16      # ...or longer than this, to stay safely within minimap range
WALK_ARRIVAL_TOLERANCE = 3   # tiles; close enough to call a hop, or the whole trip, arrived
WALK_HOP_TIMEOUT_S = 8.0     # give up waiting on a single hop after this long
WALK_MAX_HOPS = 60           # hard cap so a bad target can't loop forever

PATH_REQUEST_TIMEOUT_S = 6.0   # give up waiting for Shortest Path to finish computing
PATH_POLL_INTERVAL_S = 0.3


def save_location():
    """Snapshot the current world tile, or None if position isn't available."""
    player = controller.get_player()
    x, y, plane = player.get("x"), player.get("y"), player.get("plane")
    if x is None:
        return None
    return {"x": x, "y": y, "plane": plane}


def task_location(config: dict, category: str):
    """Wilderness spawn location for a Krystilia slayer task category (see
    SlayerLocations.java on the Java side), as chosen via the Controller
    Settings sidebar's per-target location dropdown when a category has more
    than one known spot -- or None if this category has no location data at
    all. Shape: {"x", "y", "plane", "wildernessLevel", "note", "optionCount"}.
    `category` is the task category name (e.g. "Ice giants"), not a specific
    monster name -- see SlayerMonsters.TASKS on the Java side for that
    mapping if you only have a monster name on hand."""
    return (config.get("taskLocations") or {}).get(category)


def teleport_to_ferox(ctx) -> bool:
    ring = next(
        (i for i in controller.get_inventory() if RING_OF_DUELING_NAME in (i.get("name") or "").lower()),
        None,
    )
    if ring is None:
        print("[travel] no ring of dueling in inventory")
        return False

    print("[travel] teleporting to Ferox Enclave")
    if not controller.click_entity(ctx.ser, ring):
        return False

    time.sleep(random.uniform(3.0, 3.6))  # teleport animation + loading transition
    return True


def flee_royal_seed_pod(ctx) -> bool:
    """Emergency wilderness escape: use the royal seed pod. A plain
    left-click resolves through whatever menu option the pod actually uses
    (recalled as "Rub", not re-verified) -- add that option to Controller
    Settings -> Clicking -> Forced Actions if a plain click doesn't already
    resolve to it, same as Ferox Enclave above."""
    pod = next(
        (i for i in controller.get_inventory() if ROYAL_SEED_POD_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pod is None:
        print("[travel] no royal seed pod in inventory -- cannot flee!")
        return False

    print("[travel] PvP threat detected -- using royal seed pod to escape")
    if not controller.click_entity(ctx.ser, pod):
        return False

    time.sleep(random.uniform(3.0, 3.6))  # teleport animation + loading transition
    return True


def use_fountain(ctx) -> bool:
    fountain = next(
        (
            o for o in controller.get_objects()
            if FOUNTAIN_NAME.lower() in (o.get("name") or "").lower()
            and o.get("visible", True)
            and 0 <= o.get("distance", 9999) <= FOUNTAIN_MAX_DISTANCE
        ),
        None,
    )
    if fountain is None:
        print(f"[travel] {FOUNTAIN_NAME} not found nearby")
        return False

    print(f"[travel] using {fountain.get('name')}")
    if not controller.click_entity(ctx.ser, fountain):
        return False

    time.sleep(random.uniform(1.0, 1.5))
    return True


def walk_to(ctx, target: dict) -> bool:
    """Get back to a location from save_location(). Tries a computed route
    from Shortest Path first, falls back to a direct greedy walk if that's
    unavailable or fails partway. True once within WALK_ARRIVAL_TOLERANCE
    tiles of the target."""
    steps = _request_computed_path(target)
    if steps:
        print(f"[travel] following a {len(steps)}-tile route from Shortest Path")
        if _walk_along_path(ctx, steps, target):
            return True
        print("[travel] following the computed route failed partway, falling back to a direct walk")
    else:
        print("[travel] Shortest Path unavailable -- walking straight toward the target instead")

    return _walk_greedy(ctx, target)


def _request_computed_path(target: dict):
    """Ask Shortest Path for a route to target and poll for the result.
    Returns a list of {"x", "y", "plane"} waypoints in travel order, or None
    if the plugin isn't available or didn't finish in time."""
    ack = controller.request_shortest_path(target["x"], target["y"], target["plane"])
    if not ack.get("requested"):
        return None

    waited = 0.0
    while waited < PATH_REQUEST_TIMEOUT_S:
        time.sleep(PATH_POLL_INTERVAL_S)
        waited += PATH_POLL_INTERVAL_S

        result = controller.get_shortest_path_result()
        if not result.get("active"):
            return None
        if result.get("done"):
            steps = result.get("steps") or []
            return steps if steps else None

    return None


def _walk_along_path(ctx, steps: list, target: dict) -> bool:
    """Walk a computed route by repeatedly aiming a random number of tiles
    ahead along it (not straight-line toward the final target) and clicking
    via the minimap, same hop-and-click trick as _walk_greedy but following
    an actual obstacle-aware route instead of a straight line.

    Some steps carry a "transport" (see ShortestPathProvider.describeTransport
    -- a door, gate, staircase, ladder, cave entrance, or an NPC that ferries
    you, e.g. a boat captain): minimap-clicking past those doesn't work, since
    the tile on the far side often isn't reachable by ordinary walking at all
    (a different floor, a boat's deck, a cave's own coordinate space). When
    the next transport is within the upcoming hop, this walks right up to it
    instead of hopping past it, then hands off to _use_transport."""
    index = 0

    for _ in range(WALK_MAX_HOPS):
        player = controller.get_player()
        px, py = player.get("x"), player.get("y")
        if px is None:
            return False

        if _within_tolerance(px, py, target["x"], target["y"]):
            return True

        hop_index = min(index + random.randint(WALK_HOP_MIN_TILES, WALK_HOP_MAX_TILES), len(steps) - 1)
        transport_index = _find_next_transport_index(steps, index)

        if transport_index is not None and transport_index <= hop_index:
            origin_index = transport_index - 1
            if origin_index > index:
                origin = steps[origin_index]
                if not _hop_to(ctx, origin["x"], origin["y"], origin["plane"]):
                    return False

            waypoint = steps[transport_index]
            if not _use_transport(ctx, waypoint["transport"], waypoint):
                print("[travel] couldn't use the transport automatically -- trying to walk through it anyway")
                if not _hop_to(ctx, waypoint["x"], waypoint["y"], waypoint["plane"]):
                    return False

            index = transport_index
            continue

        waypoint = steps[hop_index]
        if not _hop_to(ctx, waypoint["x"], waypoint["y"], waypoint["plane"]):
            return False
        index = hop_index

    return False


def _find_next_transport_index(steps: list, from_index: int):
    """Index of the next step carrying transport info after from_index, or
    None if the rest of the route is ordinary walking."""
    for i in range(from_index + 1, len(steps)):
        if steps[i].get("transport"):
            return i
    return None


TRANSPORT_INTERACT_TIMEOUT_S = 8.0


def _use_transport(ctx, transport: dict, waypoint: dict) -> bool:
    """Interact with a transport edge instead of trying to walk through it.

    Only handles the "menu option on a specific object or NPC" case
    (transport["objectId"]/["objectName"] present, from ShortestPathProvider
    parsing e.g. "Open Door 9398" or "Travel Holgart 7789") -- transports
    that instead rely on an inventory item, a spell, or a UI (fairy ring
    codes, spirit trees, minecarts, teleport tablets) aren't covered here and
    this returns False for those so the caller falls back to a plain walk
    attempt. The same object/NPC name+id can belong to either a game object
    or an NPC (boat "captains" use this exact same tsv column), so both are
    tried."""
    object_id = transport.get("objectId")
    name = transport.get("objectName")
    option = transport.get("objectOption")
    if object_id is None or not name:
        print(f"[travel] transport '{transport.get('displayInfo') or transport.get('type')}' "
              f"has no object/NPC info -- can't auto-use it yet (item/spell/UI-based transports "
              f"aren't handled)")
        return False

    entity = _find_transport_object(object_id) or _find_transport_npc(name)
    if entity is None:
        print(f"[travel] couldn't find '{option} {name}' ({transport.get('type')}) nearby")
        return False

    print(f"[travel] using transport: {option} {name} ({transport.get('type')})")
    if not controller.click_entity(ctx.ser, entity):
        return False

    # transport["duration"] is in game ticks (0.6s each) per transports.tsv; add slack for
    # loading transitions (floor changes, boat departures) that a fixed tick count won't cover.
    time.sleep(max(transport.get("duration", 1), 1) * 0.6 + random.uniform(0.8, 1.4))

    waited = 0.0
    while waited < TRANSPORT_INTERACT_TIMEOUT_S:
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is not None and plane == waypoint["plane"] and _within_tolerance(px, py, waypoint["x"], waypoint["y"]):
            return True
        time.sleep(0.6)
        waited += 0.6

    print("[travel] used the transport but didn't arrive where expected -- continuing from wherever we ended up")
    return True


def _find_transport_object(object_id: int):
    candidates = [
        o for o in controller.get_objects()
        if o.get("id") == object_id and o.get("clickable")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _find_transport_npc(name: str):
    name_lower = name.lower()
    candidates = [
        n for n in controller.get_npcs()
        if name_lower in (n.get("name") or "").lower() and n.get("screenX") is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


def _walk_greedy(ctx, target: dict) -> bool:
    """Straight-line walk toward target with no obstacle awareness -- the
    fallback when Shortest Path isn't available. True once within
    WALK_ARRIVAL_TOLERANCE tiles, False if it gives up (different plane, hop
    point off the minimap, or WALK_MAX_HOPS exhausted)."""
    target_x, target_y, target_plane = target["x"], target["y"], target["plane"]

    for _ in range(WALK_MAX_HOPS):
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if plane != target_plane:
            print("[travel] target is on a different plane -- can't walk there yet")
            return False

        if _within_tolerance(px, py, target_x, target_y):
            return True

        dist = math.hypot(target_x - px, target_y - py)
        hop_dist = min(dist, random.randint(WALK_HOP_MIN_TILES, WALK_HOP_MAX_TILES))
        hop_x = int(round(px + (target_x - px) / dist * hop_dist))
        hop_y = int(round(py + (target_y - py) / dist * hop_dist))

        if not _hop_to(ctx, hop_x, hop_y, plane):
            return False

    return False


def _hop_to(ctx, x: int, y: int, plane: int) -> bool:
    """Click a minimap point and wait for arrival (or timeout) before
    returning. False only if the point isn't projectable onto the minimap
    at all; a timed-out wait still returns True so the caller just
    recomputes the next hop from wherever the player actually ended up."""
    point = controller.get_minimap_walk_point(x, y, plane)
    if not point.get("onMinimap"):
        print("[travel] hop point isn't on the minimap, giving up")
        return False

    controller.click_entity(ctx.ser, point, offset=(0, 0))

    waited = 0.0
    while waited < WALK_HOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        px, py = player.get("x"), player.get("y")
        if px is not None and _within_tolerance(px, py, x, y):
            break

    return True


def _within_tolerance(x1: float, y1: float, x2: float, y2: float) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= WALK_ARRIVAL_TOLERANCE
