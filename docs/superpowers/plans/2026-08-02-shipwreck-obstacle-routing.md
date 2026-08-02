# Obstacle-Aware Boat Route Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the boat from running aground on islands during repositioning (currently a hard dead stop needing manual intervention) by planning a route around obstacles instead of always steering in a straight line, with a stuck-detection fallback that re-plans if it still runs aground.

**Architecture:** A new `ocean_path.py` runs A* over the tile grid `TileProvider`'s existing `/tiles` endpoint already exposes (`walkable`/`blocked` flags from RuneLite's own collision map — live-verified 2026-08-02 that open water reads walkable and a nearby landmass reads blocked in a tight cluster), dilated by the boat's own footprint, and simplifies the raw path down to a handful of corner waypoints. `sailing.sail_to` calls it before steering: if a route comes back, it steers through each waypoint in sequence (only braking on the final leg); if not, it falls back to a single direct `steer_toward` call, unchanged from today. `steer_toward` itself gets a self-contained stuck check (no meaningful position change across several consecutive hops) so a leg that runs aground reports failure quickly instead of grinding through all 40 hops; `sail_to` re-plans from the boat's current position when that happens, up to a few attempts.

**Tech Stack:** Python 3 (`ControllerAddon/`), no external test framework — this codebase verifies live against the bridge + Arduino, not with pytest. No Java/provider changes — `/tiles` already exposes everything this needs.

## Global Constraints

- Full design rationale lives in `docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md`'s "Design update: obstacle-aware route planning (2026-08-02)" section — read it if anything below seems under-explained.
- No unit test framework exists. "Tests" in this plan are `python -m py_compile` for each modified file. The final task is a manual, live, human-executed verification checklist — it cannot be automated.
- This is a Python-only change (`ControllerAddon/controller.py`, new `ControllerAddon/ocean_path.py`, `ControllerAddon/sailing.py`) — only a `controller.py` restart is needed, not a client rebuild.
- `BOAT_CLEARANCE_TILES=4` is an initial estimate from the boat's known ~8-tile footprint, not live-tuned — flagged for adjustment once observed, same convention as every other numeric constant in this codebase.
- `find_ocean_route` only covers the currently loaded scene (~104x104 tiles around the player). This is intentional, not a bug to fix here — repositioning targets are bounded by `maxSailDistance` (60 tiles), well within that range (see the spec section for the live-confirmed reasoning).

---

### Task 1: `controller.py` — expose `/tiles`

**Files:**
- Modify: `ControllerAddon/controller.py:133-134` (right after the existing `get_objects`)

**Interfaces:**
- Produces: `controller.get_tiles() -> list[dict]`, each entry shaped `{"x": int, "y": int, "plane": int, "walkable": bool, "blocked": bool, "reachable": bool}` (matching `TileProvider.java`'s existing `/tiles` response exactly — no Java changes needed). Task 2's `ocean_path.py` is the only consumer.

- [ ] **Step 1: Add the wrapper next to `get_objects`**

Current code (`ControllerAddon/controller.py:133-144`):

```python
def get_objects(radius=None):
    # radius (tiles, optional): bounds the scene walk to a box around the player instead of the
    # whole loaded scene. CONFIRMED live 2026-08-02 -- building screen-projection data for every
    # object in the entire scene, every game tick, regardless of whether any caller actually cares
    # about it, was a real source of client stutter. Every caller here already filters results by
    # its own distance check afterward, so passing that same bound through costs nothing.
    params = {"radius": radius} if radius is not None else None
    return api_get("/objects", params=params) or []


def get_consumables():
    return api_get("/consumables") or {"food": [], "prayer": []}
```

Insert the new function between them:

```python
def get_objects(radius=None):
    # radius (tiles, optional): bounds the scene walk to a box around the player instead of the
    # whole loaded scene. CONFIRMED live 2026-08-02 -- building screen-projection data for every
    # object in the entire scene, every game tick, regardless of whether any caller actually cares
    # about it, was a real source of client stutter. Every caller here already filters results by
    # its own distance check afterward, so passing that same bound through costs nothing.
    params = {"radius": radius} if radius is not None else None
    return api_get("/objects", params=params) or []


def get_tiles():
    return api_get("/tiles") or []


def get_consumables():
    return api_get("/consumables") or {"food": [], "prayer": []}
```

**Files touched:** `ControllerAddon/controller.py`

- [ ] **Step 2: Verify the file compiles**

Run: `cd ControllerAddon && python -m py_compile controller.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Verify it returns real data against the running bridge**

Run: `cd ControllerAddon && python -c "import controller; tiles = controller.get_tiles(); print(len(tiles), tiles[0] if tiles else None)"`
Expected: a tile count (thousands) and one sample dict with `x`/`y`/`plane`/`walkable`/`blocked`/`reachable` keys — confirms the wrapper actually talks to the live bridge correctly, not just that it compiles.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/controller.py
git commit -m "$(cat <<'EOF'
Expose GET /tiles as controller.get_tiles()

TileProvider.java already exposes per-tile walkable/blocked collision
flags -- no controller.py wrapper existed yet. Needed by the upcoming
ocean_path.py obstacle-aware route planner.
EOF
)"
```

---

### Task 2: `ocean_path.py` — A* route planning over the tile grid

**Files:**
- Create: `ControllerAddon/ocean_path.py`

**Interfaces:**
- Consumes: `controller.get_tiles() -> list[dict]` (Task 1).
- Produces: `find_ocean_route(start: dict, target: dict) -> list | None`, where `start`/`target` are `{"x", "y", "plane"}` dicts and the return value (when not `None`) is a list of `{"x", "y", "plane"}` waypoint dicts ending with `target`'s own position, in travel order, NOT including `start` itself. Task 3's `sailing.sail_to` is the only consumer.

- [ ] **Step 1: Write `ocean_path.py`**

```python
# ocean_path.py
#
# Obstacle-aware routing for sailing.py's boat repositioning, built on top of TileProvider's
# /tiles collision data (walkable/blocked, from RuneLite's own CollisionDataFlag.BLOCK_MOVEMENT_FULL
# map) -- CONFIRMED live 2026-08-02: open water reads walkable, a nearby landmass reads blocked in
# a tight cluster, so this data source correctly distinguishes sailable water from land. See
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md's "Design update: obstacle-aware
# route planning" section for the full design.
#
# Only covers the currently loaded scene (~104x104 tiles around the player, one plane) -- fine
# here since repositioning targets (from routines/shipwreck_salvaging.py's find_nearby_wreck) are
# bounded by maxSailDistance (60 tiles), well within that range.

import heapq

import controller

BOAT_CLEARANCE_TILES = 4  # dilation radius around blocked tiles -- half the boat's own ~8-tile
                           # WorldView footprint (confirmed live, PlayerProvider's
                           # debugWorldViewSizeX/Y), so a route's center-line staying clear of a
                           # blocked tile doesn't mean the boat's actual hull does too. Initial
                           # estimate, not yet live-tuned.

# 8-directional grid movement -- fine-grained enough for the compass-snapped heading steer_toward
# already handles; the grid is only used to find a route around obstacles, not to model the boat's
# exact heading options.
_NEIGHBOR_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_DIAGONAL_COST = 1.4142135
_STRAIGHT_COST = 1.0


def find_ocean_route(start: dict, target: dict) -> list | None:
    """A* route from start to target ({"x", "y", "plane"} each) over /tiles' collision data,
    simplified to a handful of corner waypoints. Returns None if either point falls outside the
    currently loaded scene, if nothing in the scene is even blocked (no detour needed), or if no
    route exists at all -- callers should fall back to steering directly at the target in every
    None case, same as sailing.py's behavior before this module existed."""
    if start["plane"] != target["plane"]:
        return None

    tiles = controller.get_tiles()
    blocked = {(t["x"], t["y"]) for t in tiles if t.get("blocked") and t.get("plane") == start["plane"]}
    if not blocked:
        return None

    passable = _build_passable_set(tiles, start["plane"], blocked)

    start_xy = (start["x"], start["y"])
    target_xy = (target["x"], target["y"])
    if start_xy not in passable or target_xy not in passable:
        return None

    raw_path = _astar(start_xy, target_xy, passable)
    if raw_path is None:
        return None

    waypoints = _simplify_path(raw_path)
    # Drop the starting point (the boat is already there); keep the rest, including the final
    # target -- sail_to steers through each of these in turn.
    return [{"x": x, "y": y, "plane": start["plane"]} for x, y in waypoints[1:]]


def _build_passable_set(tiles, plane, blocked):
    """Every loaded tile that ISN'T within BOAT_CLEARANCE_TILES (Chebyshev) of a blocked tile --
    dilating outward from each blocked tile is cheaper than testing every candidate tile against
    every blocked tile."""
    passable = {(t["x"], t["y"]) for t in tiles if t.get("plane") == plane}
    clearance_zone = set()
    for (bx, by) in blocked:
        for dx in range(-BOAT_CLEARANCE_TILES, BOAT_CLEARANCE_TILES + 1):
            for dy in range(-BOAT_CLEARANCE_TILES, BOAT_CLEARANCE_TILES + 1):
                clearance_zone.add((bx + dx, by + dy))
    return passable - clearance_zone


def _astar(start_xy, target_xy, passable):
    open_heap = [(0.0, start_xy)]
    came_from = {}
    g_score = {start_xy: 0.0}

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current == target_xy:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        cx, cy = current
        for dx, dy in _NEIGHBOR_OFFSETS:
            neighbor = (cx + dx, cy + dy)
            if neighbor not in passable:
                continue
            step_cost = _DIAGONAL_COST if dx != 0 and dy != 0 else _STRAIGHT_COST
            tentative_g = g_score[current] + step_cost
            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + _octile_heuristic(neighbor, target_xy)
                heapq.heappush(open_heap, (f_score, neighbor))

    return None


def _octile_heuristic(a, b):
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return max(dx, dy) + 0.4142135 * min(dx, dy)


def _simplify_path(path):
    """Keep only the path's corner points (where the direction of travel changes) plus the first
    and last point -- a handful of waypoints instead of one per tile."""
    if len(path) <= 2:
        return path

    simplified = [path[0]]
    for i in range(1, len(path) - 1):
        prev_dx = path[i][0] - simplified[-1][0]
        prev_dy = path[i][1] - simplified[-1][1]
        next_dx = path[i + 1][0] - path[i][0]
        next_dy = path[i + 1][1] - path[i][1]
        if _sign(prev_dx) != _sign(next_dx) or _sign(prev_dy) != _sign(next_dy):
            simplified.append(path[i])
    simplified.append(path[-1])
    return simplified


def _sign(v):
    return (v > 0) - (v < 0)
```

**Files touched:** `ControllerAddon/ocean_path.py` (new)

- [ ] **Step 2: Verify the file compiles**

Run: `cd ControllerAddon && python -m py_compile ocean_path.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Sanity-check the algorithm against a plain Python grid (no live bridge needed)**

Run this inline check (a simple hand-built obstacle, not the live game) to confirm the module's internal logic is sound before ever touching the real bridge:

```bash
cd ControllerAddon && python -c "
import ocean_path
import controller

# Monkeypatch controller.get_tiles for this standalone check -- a 30x30 open patch with a solid
# wall of blocked tiles (x=15, y=0..15), forcing a route from the left side to the right side to
# detour around it. The gap left past the wall's end (y=16..29) is wider than 2*BOAT_CLEARANCE_TILES
# so it survives the clearance dilation.
def fake_get_tiles_with_wall():
    tiles = []
    for x in range(0, 30):
        for y in range(0, 30):
            blocked = (x == 15 and y <= 15)
            tiles.append({'x': x, 'y': y, 'plane': 0, 'walkable': not blocked, 'blocked': blocked})
    return tiles

controller.get_tiles = fake_get_tiles_with_wall
route = ocean_path.find_ocean_route({'x': 2, 'y': 2, 'plane': 0}, {'x': 28, 'y': 2, 'plane': 0})
assert route is not None, 'expected a route around the wall'
assert route[-1] == {'x': 28, 'y': 2, 'plane': 0}, f'route should end at the target, got {route[-1]}'
print(f'route has {len(route)} waypoints:', route)

# A straight line that happens not to cross the wall still comes back as a (near-direct) route,
# not None -- None is reserved for 'nothing in the scene is blocked at all', per the docstring.
clear_route = ocean_path.find_ocean_route({'x': 2, 'y': 2, 'plane': 0}, {'x': 2, 'y': 28, 'plane': 0})
assert clear_route is not None
assert clear_route[-1] == {'x': 2, 'y': 28, 'plane': 0}
print(f'unobstructed line still returns a route: {len(clear_route)} waypoints (expected, matches straight line)')

# Only a scene with NOTHING blocked at all short-circuits to None.
def fake_get_tiles_empty():
    tiles = []
    for x in range(0, 20):
        for y in range(0, 20):
            tiles.append({'x': x, 'y': y, 'plane': 0, 'walkable': True, 'blocked': False})
    return tiles

controller.get_tiles = fake_get_tiles_empty
none_route = ocean_path.find_ocean_route({'x': 2, 'y': 2, 'plane': 0}, {'x': 2, 'y': 18, 'plane': 0})
assert none_route is None, 'expected None when nothing in the scene is blocked at all'
print('nothing-blocked case correctly returned None')
"
```

Expected: all three `print` lines execute with no `AssertionError` — a multi-waypoint route around the fake wall, a route (not None) for the unobstructed line since something elsewhere in the scene is still blocked, and `None` only for the fully-open scene.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/ocean_path.py
git commit -m "$(cat <<'EOF'
Add ocean_path.py obstacle-aware route planner

A* over TileProvider's /tiles collision data (live-verified: open
water reads walkable, a nearby landmass reads blocked in a tight
cluster), dilated by the boat's own footprint and simplified to a
handful of corner waypoints. Not yet wired into sailing.py.
EOF
)"
```

---

### Task 3: `sailing.py` — steer through the planned route, with stuck-detection replanning

**Files:**
- Modify: `ControllerAddon/sailing.py:35` (import), `:106-140` (`steer_toward`), `:237-244` (`sail_to`)

**Interfaces:**
- Consumes: `ocean_path.find_ocean_route(start, target) -> list | None` (Task 2).
- Produces: `steer_toward(ctx, target: dict, arrival_tolerance: float = SAIL_BRAKE_TRIGGER_TILES) -> bool` (new optional parameter; existing single-arg calls keep today's behavior unchanged). `sail_to(ctx, target: dict) -> bool` — signature and contract unchanged, existing callers in `routines/shipwreck_salvaging.py` need no changes.

- [ ] **Step 1: Import `ocean_path`**

Current code (`ControllerAddon/sailing.py:35`):

```python
import controller
```

Replace with:

```python
import controller
import ocean_path
```

**Files touched:** `ControllerAddon/sailing.py`

- [ ] **Step 2: Add stuck-detection constants next to the other sailing constants**

Right after the existing `SAIL_BRAKE_TRIGGER_TILES = 10` line, add:

```python
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
```

**Files touched:** `ControllerAddon/sailing.py`

- [ ] **Step 3: Add the `arrival_tolerance` parameter and stuck detection to `steer_toward`**

Current code (`ControllerAddon/sailing.py:106-140`):

```python
def steer_toward(ctx, target: dict) -> bool:
    """Drive the boat to within SAIL_BRAKE_TRIGGER_TILES tiles of target ({"x", "y", "plane"}),
    same hop-and-click-minimap loop shape as travel._walk_greedy -- confirmed live that minimap
    clicks steer the boat once the helm is engaged, just with coarser (compass-snapped) heading
    than foot travel.

    Stops at SAIL_BRAKE_TRIGGER_TILES, not the tighter SAIL_ARRIVAL_TOLERANCE -- CONFIRMED live
    2026-08-02: the boat keeps coasting under full sail for as long as this function keeps
    running, so returning only once already within the tight tolerance left no room to brake
    before ramming the target. sail_to calls stop_boat() immediately after this returns, and the
    already-measured post-toggle drift closes the remaining distance."""
    target_x, target_y, target_plane = target["x"], target["y"], target["plane"]

    for _ in range(SAIL_MAX_HOPS):
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if plane != target_plane:
            print("[sailing] target is on a different plane -- can't sail there")
            return False

        if _within_tolerance(px, py, target_x, target_y, SAIL_BRAKE_TRIGGER_TILES):
            return True

        dist = math.hypot(target_x - px, target_y - py)
        hop_dist = min(dist, random.randint(SAIL_HOP_MIN_TILES, SAIL_HOP_MAX_TILES))
        hop_x = int(round(px + (target_x - px) / dist * hop_dist))
        hop_y = int(round(py + (target_y - py) / dist * hop_dist))

        if not _hop_to(ctx, hop_x, hop_y, plane):
            return False

    return False
```

Replace it entirely with:

```python
def steer_toward(ctx, target: dict, arrival_tolerance: float = SAIL_BRAKE_TRIGGER_TILES) -> bool:
    """Drive the boat to within arrival_tolerance tiles of target ({"x", "y", "plane"}), same
    hop-and-click-minimap loop shape as travel._walk_greedy -- confirmed live that minimap clicks
    steer the boat once the helm is engaged, just with coarser (compass-snapped) heading than foot
    travel.

    Defaults to SAIL_BRAKE_TRIGGER_TILES, not the tighter SAIL_ARRIVAL_TOLERANCE -- CONFIRMED live
    2026-08-02: the boat keeps coasting under full sail for as long as this function keeps
    running, so returning only once already within the tight tolerance left no room to brake
    before ramming the target. sail_to calls stop_boat() immediately after the FINAL leg of a
    route returns, and the already-measured post-toggle drift closes the remaining distance.
    Intermediate route waypoints (see sail_to) pass the tighter SAIL_ARRIVAL_TOLERANCE instead --
    they exist specifically to route around an obstacle, so cutting the corner early would defeat
    the point.

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
            print("[sailing] target is on a different plane -- can't sail there")
            return False

        if _within_tolerance(px, py, target_x, target_y, arrival_tolerance):
            return True

        if last_pos is not None and _within_tolerance(px, py, last_pos[0], last_pos[1], STUCK_MOVEMENT_THRESHOLD_TILES):
            stuck_hops += 1
            if stuck_hops >= STUCK_HOP_LIMIT:
                print("[sailing] stuck -- position hasn't changed across repeated hops, likely aground")
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
```

**Files touched:** `ControllerAddon/sailing.py`

- [ ] **Step 4: Wire route planning and retry into `sail_to`**

Current code (`ControllerAddon/sailing.py:237-244`):

```python
def sail_to(ctx, target: dict) -> bool:
    """Full reposition: engage the helm, steer to target, take down sails. Does not redeploy the
    hook afterward -- the caller decides when that's appropriate."""
    if not use_helm(ctx):
        return False
    if not steer_toward(ctx, target):
        return False
    return stop_boat(ctx)
```

Replace with:

```python
def sail_to(ctx, target: dict) -> bool:
    """Full reposition: engage the helm, plan an obstacle-aware route (falling back to a direct
    line if no route is needed or none can be found), steer through it leg by leg, take down
    sails at the end. Does not redeploy the hook afterward -- the caller decides when that's
    appropriate.

    Re-plans from the current position (up to MAX_ROUTE_ATTEMPTS times) if a leg reports getting
    stuck -- CONFIRMED live 2026-08-02: running aground is a hard dead stop needing a real course
    change to recover from, not something retrying the exact same heading resolves on its own."""
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
            tolerance = SAIL_BRAKE_TRIGGER_TILES if is_final_leg else SAIL_ARRIVAL_TOLERANCE
            if not steer_toward(ctx, leg, arrival_tolerance=tolerance):
                print(f"[sailing] got stuck heading to a waypoint (attempt {attempt + 1}/{MAX_ROUTE_ATTEMPTS}) -- replanning")
                stuck = True
                break

        if not stuck:
            return stop_boat(ctx)

    print("[sailing] couldn't find a working route after repeated attempts -- giving up")
    return False
```

**Files touched:** `ControllerAddon/sailing.py`

- [ ] **Step 5: Verify the file compiles and imports cleanly**

Run: `cd ControllerAddon && python -m py_compile sailing.py ocean_path.py`
Expected: no output, exit code 0.

Run: `cd ControllerAddon && python -c "import sailing"`
Expected: no output, exit code 0 (catches any name typo the compile step alone might miss, same as this codebase's other plans).

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/sailing.py
git commit -m "$(cat <<'EOF'
Steer through a planned route instead of straight at every target

sail_to now asks ocean_path for a route before steering: if one
comes back, it steers through each waypoint in sequence (only
braking on the final leg), falling back to a single direct
steer_toward call when no route is needed or none can be found --
unchanged from today's behavior in that case.

steer_toward also gets its own stuck detection (no meaningful
position change across a few consecutive hops means likely aground,
confirmed live as a hard dead stop) so a blocked leg reports failure
quickly instead of grinding through all SAIL_MAX_HOPS. sail_to
re-plans from the current position when that happens, up to
MAX_ROUTE_ATTEMPTS times.
EOF
)"
```

---

### Task 4: Manual live verification

This cannot be automated — no client/game-state simulation exists for the actual sailing physics. Run the routine live and watch for these specific things.

- [ ] **Step 1: Restart the Python side**

Python-only change — restart `controller.py` (no client rebuild needed):

```bash
cd ControllerAddon && python controller.py routines/shipwreck_salvaging.py
```

- [ ] **Step 2: Trigger a reposition near a known island/obstruction**

Position yourself (or wait for the routine to naturally reposition) somewhere the straight line to the next wreck target would previously have run aground. Watch for:
- Any `[sailing] got stuck heading to a waypoint...` line — if this appears, note whether the *next* attempt (replanned route) actually gets past the same obstruction, or whether it repeats `MAX_ROUTE_ATTEMPTS` times and gives up.
- Whether the boat visibly steers around the island rather than into it — a route was found and followed.
- If nothing obstructs the direct line, confirm behavior is unchanged from before this plan (single direct `steer_toward` call, no waypoint detour) — this confirms the "no route needed" fallback path still works normally for the common case.

- [ ] **Step 3: Confirm no false positives from the stuck check**

During a *normal* unobstructed reposition, confirm the `[sailing] stuck -- position hasn't changed...` line does NOT fire spuriously (e.g. from a slow hop settle rather than genuinely running aground). If it does fire on clear water, `STUCK_MOVEMENT_THRESHOLD_TILES` (1.5) or `STUCK_HOP_LIMIT` (3) need loosening.

- [ ] **Step 4: Report back**

Report whether steps 2-3 behaved as expected, including any `[sailing]` log lines from a real grounding-and-recovery sequence if one occurs -- these feed back into tuning `BOAT_CLEARANCE_TILES`, `STUCK_HOP_LIMIT`, and `STUCK_MOVEMENT_THRESHOLD_TILES`, the same way every other constant in this codebase has been tuned from live observation.
