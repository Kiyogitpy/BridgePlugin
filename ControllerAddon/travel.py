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
#   walk_to_exact_tile() -- shift+click force-walk onto ONE specific tile and
#                           wait until standing exactly on it (no tolerance)
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
import debug_log

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

# Below this many tiles (Chebyshev, same plane only) walk_to doesn't bother asking Shortest Path
# at all and goes straight to _walk_greedy: the request plus up to PATH_REQUEST_TIMEOUT_S of
# polling costs more than the walk itself over a short hop. Deliberately NOT a large number --
# _walk_greedy is obstacle-blind, so every tile of gate raises the odds of wedging against a wall
# it can't route around. 25 covers the "already basically there" case (a safespot return, a
# re-approach after a bank trip) without gambling on open terrain.
SHORT_HOP_TILES = 25

# Every ShortestPathConfig option the plugin recognises, so a typo in an override can be caught
# here instead of being silently dropped (the plugin ignores unknown keys, which would leave the
# baseline quietly in force while the caller believed it had opted in). Taken from
# ShortestPathConfig's own method names in the installed jar.
SHORTEST_PATH_CONFIG_KEYS = frozenset({
    "avoidWilderness", "useAgilityShortcuts", "useGrappleShortcuts", "useBoats", "useCanoes",
    "useCharterShips", "useShips", "useFairyRings", "useGnomeGliders", "useHotAirBalloons",
    "useMagicCarpets", "useMagicMushtrees", "useMinecarts", "useQuetzals", "useSpiritTrees",
    "useTeleportationItems", "useTeleportationLevers", "useTeleportationPortals",
    "useTeleportationSpells", "useTeleportationSpellsHome", "useTeleportationMinigames",
    "useWildernessObelisks", "useSeasonalTransports", "includeBankPath", "currencyThreshold",
    "cancelInstead", "recalculateDistance", "reachedDistance", "unreachableTargetDistance",
    "calculationCutoff", "usePoh", "usePohFairyRing", "usePohSpiritTree",
    "useTeleportationPortalsPoh", "pohJewelleryBoxTier", "usePohMountedItems",
    "costAgilityShortcuts", "costBoats", "costCanoes", "costCharterShips", "costFairyRings",
    "costGnomeGliders", "costGrappleShortcuts", "costHotAirBalloons", "costMagicCarpets",
    "costMagicMushtrees", "costMinecarts", "costNonConsumableTeleportationItems",
    "costQuetzalWhistle", "costQuetzals", "costSeasonalTransports", "costShips",
    "costSpiritTrees", "costTeleportationBoxes", "costTeleportationLevers",
    "costTeleportationMinigames", "costTeleportationPortals", "costTeleportationSpells",
    "costTeleportationSpellsHome", "costWildernessObelisks",
})

# The transport policy sent with EVERY path request, in full.
#
# Two reasons it's explicit rather than "let the plugin use whatever the user ticked in its own
# panel". First, a route is only useful if we can actually follow it, and _use_transport can only
# do one thing: click a single menu option on an object or NPC. Every teleport and every
# destination-picker transport produces an edge we'd have to walk across instead, which for a
# teleport means minimap-clicking a tile on the far side of the map -- so inheriting a panel with
# teleports enabled silently breaks routes. Second, the plugin's override map is STICKY: it only
# replaces the map when a non-empty one arrives and never clears it, so a partial override from
# one walk would leak into every later walk in the session. Sending the whole map every time is
# what makes each request independent of the last.
#
# On = what _use_transport can execute today (ordinary doors/gates/ladders/stairs need no key at
# all -- TransportType.TRANSPORT has no toggle and is always on). Off = everything else, until
# there's code that can actually drive it. Routines opt individual keys back on per call via
# walk_to(..., transports={...}).
TRANSPORT_POLICY = {
    # Single-click object/NPC transports -- executable.
    "useAgilityShortcuts": True,
    "useGrappleShortcuts": True,
    "useBoats": True,
    "useShips": True,

    # Clickable, but each opens a destination picker _use_transport can't drive.
    "useCanoes": False,
    "useCharterShips": False,
    "useMinecarts": False,

    # Teleports: no object to click at all, or a code/menu UI on top of one.
    "useTeleportationItems": "None",
    "useTeleportationSpells": False,
    "useTeleportationSpellsHome": False,
    "useTeleportationLevers": False,
    "useTeleportationPortals": False,
    "useTeleportationMinigames": False,
    "useFairyRings": False,
    "useSpiritTrees": False,
    "useGnomeGliders": False,
    "useHotAirBalloons": False,
    "useMagicCarpets": False,
    "useMagicMushtrees": False,
    "useQuetzals": False,
    "useWildernessObelisks": False,

    # Player-owned house routing -- needs a house teleport plus in-house navigation.
    "usePoh": False,
    "usePohFairyRing": False,
    "usePohSpiritTree": False,
    "usePohMountedItems": False,
    "useTeleportationPortalsPoh": False,

    # Leagues/seasonal-only transports, which don't exist on a main.
    "useSeasonalTransports": False,
}


def transport_config(overrides: dict | None = None) -> dict:
    """TRANSPORT_POLICY with `overrides` applied, as a fresh dict. Raises ValueError on a key
    Shortest Path wouldn't recognise -- it ignores unknown keys silently, so a typo would leave
    the baseline in force while the caller believed it had opted into something."""
    unknown = set(overrides or {}) - SHORTEST_PATH_CONFIG_KEYS
    if unknown:
        raise ValueError(f"not a Shortest Path config key: {', '.join(sorted(unknown))}")
    config = dict(TRANSPORT_POLICY)
    config.update(overrides or {})
    return config


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
        debug_log.log("travel", "no ring of dueling in inventory")
        return False

    debug_log.log("travel", "teleporting to Ferox Enclave")
    # check_ui_blockers=False: inventory-item click, same reasoning as every other inventory
    # click in this codebase (see banker.py's coal bag notes) -- CONFIRMED live 2026-08-06 (user
    # session: bot stuck at 8hp repeatedly failing this exact click): without it, the ring's own
    # screen position (always inside the inventory panel) gets flagged as "behind a UI panel",
    # and rotating the camera to work around it does nothing for a flat 2D panel -- it just
    # loops forever, rotating and re-blocking, never actually clicking.
    if not controller.click_entity(ctx.ser, ring, check_ui_blockers=False):
        return False

    controller.human_delay(3.0, 3.6)  # teleport animation + loading transition
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
        debug_log.log("travel", "no royal seed pod in inventory -- cannot flee!")
        return False

    debug_log.log("travel", "PvP threat detected -- using royal seed pod to escape")
    # check_ui_blockers=False -- same inventory-panel UI-blocker loop as teleport_to_ferox's ring
    # click above; this one's the emergency PvP flee path, so it matters just as much here.
    if not controller.click_entity(ctx.ser, pod, check_ui_blockers=False):
        return False

    controller.human_delay(3.0, 3.6)  # teleport animation + loading transition
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
        debug_log.log("travel", f"{FOUNTAIN_NAME} not found nearby")
        return False

    debug_log.log("travel", f"using {fountain.get('name')}")
    if not controller.click_entity(ctx.ser, fountain):
        return False

    controller.human_delay(1.0, 1.5)
    return True


def walk_to(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE,
            transports: dict | None = None, min_path_tiles: float | None = None) -> bool:
    """Get back to a location from save_location(). Tries a computed route
    from Shortest Path first, falls back to a direct greedy walk if that's
    unavailable or fails partway. True once within `tolerance` tiles of the
    target (defaults to WALK_ARRIVAL_TOLERANCE; callers needing a tighter
    final arrival -- e.g. safespot anchoring, where 3 tiles of slack could
    still be inside a melee monster's reach -- can pass a smaller value).

    `transports` overrides individual TRANSPORT_POLICY keys for this one walk
    (e.g. {"avoidWilderness": True}); `min_path_tiles` overrides SHORT_HOP_TILES,
    the distance below which the pathfinder isn't worth asking at all -- pass 0
    to force a computed route even for a short hop."""
    if _is_short_hop(target, min_path_tiles):
        debug_log.log("travel", "target is close enough to walk straight to -- skipping Shortest Path")
        return _walk_greedy(ctx, target, tolerance)

    steps, reached, reason = _request_computed_path(target, transport_config(transports))
    if steps:
        if not reached:
            # The route is the best Shortest Path could do, not one that arrives -- follow it
            # anyway (getting closer beats standing still, and _walk_greedy picks up the
            # remainder) but say so, because the alternative is a walk that silently stops short
            # and a caller that can't tell why.
            debug_log.log("travel", f"Shortest Path couldn't reach the target ({reason}) -- "
                                    f"following its best partial route, then walking the rest")
        debug_log.log("travel", f"following a {len(steps)}-tile route from Shortest Path")
        if _walk_along_path(ctx, steps, target, tolerance):
            return True
        debug_log.log("travel", "following the computed route failed partway, falling back to a direct walk")
    else:
        debug_log.log("travel", "Shortest Path unavailable -- walking straight toward the target instead")

    return _walk_greedy(ctx, target, tolerance)


def _is_short_hop(target: dict, min_path_tiles: float | None = None) -> bool:
    """True when the target is near enough (and on this plane) that a computed route isn't worth
    the round trip. Plane is a hard gate, not a distance term: a staircase's two ends are a
    couple of tiles apart horizontally but only the route knows about the staircase."""
    threshold = SHORT_HOP_TILES if min_path_tiles is None else min_path_tiles
    player = controller.get_player()
    px, py = player.get("x"), player.get("y")
    if px is None or player.get("plane") != target["plane"]:
        return False
    return max(abs(target["x"] - px), abs(target["y"] - py)) <= threshold


def _request_computed_path(target: dict, config: dict):
    """Ask Shortest Path for a route to target and poll for the result.

    Returns (steps, reached, termination_reason): a list of {"x", "y", "plane"} waypoints in
    travel order, whether the plugin's own pathfinder actually got to the target, and its
    PathTerminationReason (TARGET_REACHED / SEARCH_EXHAUSTED / CUTOFF_REACHED / CANCELLED).
    steps is None if the plugin isn't available or didn't finish in time.

    The result is matched against the target we asked for before it's accepted. Shortest Path
    replaces its Pathfinder asynchronously, so an early poll can still be reading the PREVIOUS
    walk's finished path -- and before this check, that path was followed as if it were ours,
    sending the player off toward wherever the last routine was going. A build too old to report
    its targets degrades to the old done-is-enough behaviour rather than refusing to path."""
    ack = controller.request_shortest_path(target["x"], target["y"], target["plane"], config=config)
    if not ack.get("requested"):
        return None, False, None

    waited = 0.0
    while waited < PATH_REQUEST_TIMEOUT_S:
        time.sleep(PATH_POLL_INTERVAL_S)
        waited += PATH_POLL_INTERVAL_S

        result = controller.get_shortest_path_result()
        if not result.get("active"):
            return None, False, None
        if not result.get("done") or not _result_is_for(result, target):
            continue

        steps = result.get("steps") or []
        return steps or None, bool(result.get("reached", True)), result.get("terminationReason")

    return None, False, None


def _result_is_for(result: dict, target: dict) -> bool:
    """Whether a finished path is the one we asked for. True when the result carries no target
    info at all -- see _request_computed_path on the older-build fallback."""
    targets = result.get("targets")
    if targets is None:
        return True
    return any(
        t.get("x") == target["x"] and t.get("y") == target["y"] and t.get("plane") == target["plane"]
        for t in targets
    )


def _walk_along_path(ctx, steps: list, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
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

        if _within_tolerance(px, py, target["x"], target["y"], tolerance):
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
            transport = waypoint["transport"]

            if not _transport_is_executable(transport):
                # Walking through an unusable transport is only sane when there's something
                # physically there to walk into. For a teleport (or any edge with no object at
                # all) the far side is somewhere else entirely, so the "walk through it anyway"
                # fallback below would minimap-click a tile across the map. Give up on the route
                # instead and let walk_to fall back to a direct walk.
                debug_log.log("travel", f"route needs a transport we can't drive "
                                        f"({transport.get('type')}: {transport.get('displayInfo')}) "
                                        f"-- abandoning the computed route")
                return False

            if not _use_transport(ctx, transport, waypoint):
                debug_log.log("travel", "couldn't use the transport automatically -- trying to walk through it anyway")
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


def _transport_is_executable(transport: dict) -> bool:
    """Whether _use_transport can actually drive this edge: it clicks one menu option on one
    object or NPC, so it needs both an id and a name to find. Deliberately keyed on that rather
    than on the transport's type or its isTeleport flag -- a teleport lever IS a clickable
    object, and a boat that somehow arrives without object info is NOT, whatever its type says."""
    return transport.get("objectId") is not None and bool(transport.get("objectName"))


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
        debug_log.log("travel", f"transport '{transport.get('displayInfo') or transport.get('type')}' "
                                 f"has no object/NPC info -- can't auto-use it yet (item/spell/UI-based transports "
                                 f"aren't handled)")
        return False

    entity = _find_transport_object(object_id) or _find_transport_npc(name)
    if entity is None:
        debug_log.log("travel", f"couldn't find '{option} {name}' ({transport.get('type')}) nearby")
        return False

    debug_log.log("travel", f"using transport: {option} {name} ({transport.get('type')})")
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

    debug_log.log("travel", "used the transport but didn't arrive where expected -- continuing from wherever we ended up")
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


def _walk_greedy(ctx, target: dict, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    """Straight-line walk toward target with no obstacle awareness -- the
    fallback when Shortest Path isn't available. True once within
    `tolerance` tiles of the target, False if it gives up (different
    plane, hop point off the minimap, or WALK_MAX_HOPS exhausted)."""
    target_x, target_y, target_plane = target["x"], target["y"], target["plane"]

    for _ in range(WALK_MAX_HOPS):
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if plane != target_plane:
            debug_log.log("travel", "target is on a different plane -- can't walk there yet")
            return False

        if _within_tolerance(px, py, target_x, target_y, tolerance):
            return True

        dist = math.hypot(target_x - px, target_y - py)
        hop_dist = min(dist, random.randint(WALK_HOP_MIN_TILES, WALK_HOP_MAX_TILES))
        hop_x = int(round(px + (target_x - px) / dist * hop_dist))
        hop_y = int(round(py + (target_y - py) / dist * hop_dist))

        if not _hop_to(ctx, hop_x, hop_y, plane):
            return False

    return False


def _hop_to(ctx, x: int, y: int, plane: int) -> bool:
    """Click the hop point and wait for arrival (or timeout) before
    returning. False only if the point isn't projectable onto the minimap
    at all; a timed-out wait still returns True so the caller just
    recomputes the next hop from wherever the player actually ended up.

    Prefers a viewport click (get_ground_walk_point) over a minimap click
    whenever the tile is actually rendered on screen -- CONFIRMED live
    2026-08-06: the minimap is too small/coarse to reliably land on a
    specific single tile (e.g. maintain_safespot's exact-tile return), while
    a viewport click aims at the real projected tile position. Minimap
    clicking is still needed as a fallback for hops beyond render distance,
    which is the common case for most of a route -- only the tile actually
    visible right now can use the more precise path."""
    ground_point = controller.get_ground_walk_point(x, y, plane)
    if ground_point.get("onScreen"):
        point = ground_point
    else:
        point = controller.get_minimap_walk_point(x, y, plane)
        if not point.get("onMinimap"):
            debug_log.log("travel", "hop point isn't on screen or the minimap, giving up")
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


def _within_tolerance(x1: float, y1: float, x2: float, y2: float, tolerance: float = WALK_ARRIVAL_TOLERANCE) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= tolerance


EXACT_TILE_POLL_INTERVAL_S = 0.6   # one game tick -- the soonest the player's own tile can change
EXACT_TILE_TIMEOUT_S = 8.0         # generous for the short, in-render-range hops this is for


def walk_to_exact_tile(ctx, x: int, y: int, plane: int, wait_for_arrival: bool = True) -> bool:
    """Walk to the EXACT world tile (x, y, plane) and wait until standing exactly on it.

    Deliberately not walk_to/_hop_to above: those treat landing within WALK_ARRIVAL_TOLERANCE
    (3 tiles) as arrival, which is useless whenever a tile's identity is the whole point --
    golem_crafting.py's golem sides are 1-2 tiles apart (a 3-tile-tolerant arrival can report
    success while standing on the WRONG side), and chinchompa_hunting.py has to be standing on
    the precise tile it means to lay a box trap on, since "Lay" always targets the player's own
    tile and nothing else.

    Returns False only if the tile can't be projected onto the screen at all (out of render
    range); a timed-out wait still returns True, since callers re-check position fresh afterward
    either way.

    EXTRACTED 2026-08-20 from routines/golem_crafting.py, unchanged, once a second routine needed
    the identical behaviour -- see that file's own walk_to_exact_tile (now a delegating wrapper)
    for the full provenance of each piece below. In short:
      - Early-out with no click at all when already standing there (2026-08-20 review finding E):
        the common case for both callers is a walk that turns out to be a no-op, and paying for a
        full human-paced flight plus a poll to discover that is pure waste.
      - Shift held around the click (2026-08-09 user report, CONFIRMED live): a plain click at a
        ground point near an interactive object can resolve to that OBJECT's action instead of
        walking -- it cleared the Goat Pit's lining mid walk-back. Shift+click is OSRS's own
        force-walk shortcut, looks like any other click, so it's used unconditionally. This is
        the codebase rule for every walk-to-a-tile click (see CLAUDE.md), not a per-caller opt-in.
      - refresh_target (2026-08-10 user report): the tile doesn't move but its screen projection
        does (camera auto-follow/momentum) during move_to_human's flight time, so the final
        approach re-aims at the tile's live position instead of a pre-click snapshot.

    `wait_for_arrival=False` (added 2026-08-23, user directive) fires the walk and returns as soon
    as the click is out, so the caller can issue its NEXT click while the player is still moving.
    That is what a human does: MEASURED across a full hand-played golem carve
    (recordings/2026-08-23_194302_golem-carving), the human clicked the target object 0.6-1.0s
    after each walk click, from a tile they had not reached yet, 4 times out of 4 -- and the side
    that got carved was the tile they were walking TO, not the one they clicked from (confirmed by
    resolving GOLEM_2_{NORTH,EAST,SOUTH,WEST}_PROGRESS against runelite-api-1.12.36.jar). The game
    queues the interaction and performs it on arrival, so blocking here buys no correctness -- it
    is pure added latency, and worse, the block is what turns an over-tight confirmation timeout
    into an action-destroying re-walk rather than a harmless extra wait. Pair it with a
    `*_refresh_target` factory plus `refresh_legs` on the following click so the aim is corrected
    mid-flight against un-cached position reads. See CLAUDE.md's "Sub-tick data and clicking while
    moving" for the standing preference this implements.

    Default is True, which reproduces every existing caller's behaviour exactly.
    """
    player = controller.get_player()
    if player.get("x") == x and player.get("y") == y and player.get("plane", 0) == plane:
        return True
    point = controller.get_ground_walk_point(x, y, plane)
    if not point.get("onScreen"):
        return False
    refresh = lambda: controller.get_ground_walk_screen_pos(x, y, plane)
    controller.key_down(ctx.ser, "SHIFT")
    controller.click_entity(ctx.ser, point, offset=(0, 0), refresh_target=refresh)
    controller.key_up(ctx.ser, "SHIFT")
    if not wait_for_arrival:
        return True
    waited = 0.0
    while waited < EXACT_TILE_TIMEOUT_S:
        time.sleep(EXACT_TILE_POLL_INTERVAL_S)
        waited += EXACT_TILE_POLL_INTERVAL_S
        player = controller.get_player()
        if player.get("x") == x and player.get("y") == y:
            return True
    return True


def maybe_approach_before_click(ctx, entity: dict, already_engaged: bool = False,
                                 chance: float = controller.APPROACH_TILE_CHANCE,
                                 via_minimap: bool = True) -> bool:
    """With `chance` probability, click a ground tile partway between the
    player and `entity` instead of clicking the entity itself. A real player
    often closes some of the distance to something a few tiles off manually
    rather than always relying on the game's own click-to-walk-then-interact
    -- doing the latter 100% of the time is a tell.

    via_minimap picks how that intermediate tile gets clicked: True (default)
    uses the same minimap-hop trick as _hop_to, appropriate for a target far
    enough that the approach tile may not even be rendered in the viewport.
    Pass False for something close and on-screen (e.g. closing the last few
    tiles to a monster mid-approach) -- a real player closing that kind of
    short distance clicks the ground in front of them, not the minimap.
    False here silently declines (same as an off-minimap point would) if the
    tile isn't actually on-screen, so the caller falls back to clicking the
    entity directly.

    This is only for *approaching* something, never appropriate once you're
    already committed to it -- pass already_engaged=True (e.g. from
    get_combat().inCombat) whenever the caller might already be mid-fight
    with this exact entity: isIdle can read True for a single tick between
    attack animations while inCombat stays True for the whole fight, and
    without this a routine re-evaluating on that idle tick could wander off
    to some tile mid-fight instead of just continuing the attack.

    Returns True if it clicked an approach tile instead (the caller should
    skip its own click this tick -- get_combat().isIdle will read False
    while walking, so the routine's usual "already busy" check naturally
    holds off the real click until a later tick, once the player has closed
    the distance). Returns False if already_engaged, if it rolled against
    approaching, or if entity/player position data wasn't available --
    caller should click the entity as normal.
    """
    if already_engaged:
        return False

    x, y, plane = entity.get("x"), entity.get("y"), entity.get("plane")
    dist = entity.get("distance")
    if x is None or y is None or plane is None or dist is None:
        return False
    if dist < controller.APPROACH_TILE_MIN_DISTANCE:
        return False
    if random.random() > chance:
        return False

    player = controller.get_player()
    px, py = player.get("x"), player.get("y")
    if px is None:
        return False

    frac = random.uniform(controller.APPROACH_TILE_FRACTION_MIN, controller.APPROACH_TILE_FRACTION_MAX)
    hop_x = int(round(px + (x - px) * frac))
    hop_y = int(round(py + (y - py) * frac))

    if via_minimap:
        point = controller.get_minimap_walk_point(hop_x, hop_y, plane)
        if not point.get("onMinimap"):
            return False
    else:
        point = controller.get_ground_walk_point(hop_x, hop_y, plane)
        if not point.get("onScreen"):
            return False

    debug_log.log("travel", f"closing some distance to {entity.get('name')} before the final click")
    controller.click_entity(ctx.ser, point, offset=(0, 0))
    return True
