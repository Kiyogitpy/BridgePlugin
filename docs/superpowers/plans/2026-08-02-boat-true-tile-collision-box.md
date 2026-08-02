# Boat True-Tile Collision Box Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ocean_path.py`'s hand-tuned, guessed `BOAT_CLEARANCE_TILES` dilation radius with one derived from the boat's real hull footprint, sourced from RuneLite's own `WorldEntityConfig` bounds (the same "true tile" data the sideloaded Sailing plugin's `TrueTileIndicator` overlay uses).

**Architecture:** Two small, independent changes. (1) `PlayerProvider.java` gains `boatWidth`/`boatHeight` fields (in tiles) on the existing `/player` endpoint, resolved via `RuneLiteUtil.findTopLevelWorldEntityContaining` (already used for `boatX/boatY`) plus `WorldEntity.getConfig().getBoundsWidth()/getBoundsHeight()`. (2) `ocean_path.py`'s obstacle dilation reads those fields via a new pure function and computes a real clearance radius instead of using a constant, falling back to the old guessed value when the fields aren't available.

**Tech Stack:** Java (RuneLite plugin, existing `BridgeProvider` pattern), Python (existing `ControllerAddon` bridge client, no test framework in use anywhere in this repo).

## Global Constraints

- Keep the existing *isotropic* dilation algorithm in `ocean_path.py` exactly as-is (a plain radius around each blocked tile) — do not introduce oriented-rectangle collision math. This was an explicit scope decision: the boat's orientation is not modeled, only its size.
- `WorldEntityConfig`'s bounds are in RuneLite's local-coordinate units; convert to tiles by dividing by `128` (`LOCAL_TILE_SIZE`, RuneLite's standard local-units-per-tile constant — confirmed via `data/decompiled/client-1.12.33/net/runelite/client/plugins/devtools/SceneOverlay.java:54`).
- New bridge fields must be omitted (not `null`, not `0`) whenever the boat's `WorldEntity`/config isn't resolvable, matching the existing `boatX`/`boatY`/`boatPlane` fields' omission behavior in the same method.
- `ocean_path.py` must fall back to the pre-existing guessed constant (`2`) when the new fields are absent, so behavior is unchanged against an older bridge build or while not aboard a boat.
- This repo has no automated test suite anywhere (`ControllerAddon/` has zero test files, no pytest config). Follow the codebase's existing verification convention instead: manual live checks via `curl`, documented with a `CONFIRMED live YYYY-MM-DD` code comment once verified (see the many existing examples in `PlayerProvider.java`/`SailingProvider.java`). Do not introduce a new test framework as part of this plan.

---

### Task 1: Expose the boat's true-tile bounds on `/player`

**Files:**
- Modify: `src/main/java/com/example/providers/PlayerProvider.java:58-64`

**Interfaces:**
- Consumes: `RuneLiteUtil.findTopLevelWorldEntityContaining(Client client, WorldView view) -> WorldEntity` (existing, already imported via `com.example.util.RuneLiteUtil`, already used one line above in this same method).
- Produces: `/player` JSON response gains optional `boatWidth` (double, tiles) and `boatHeight` (double, tiles) fields, present only when `boatX`/`boatY`/`boatPlane` are also present (i.e. aboard a boat). Consumed by Task 2's `_boat_clearance_tiles(player: dict)`.

- [ ] **Step 1: Add the boat bounds fields**

Open `src/main/java/com/example/providers/PlayerProvider.java`. Find this existing block (currently lines 58-64):

```java
		WorldPoint boatWp = RuneLiteUtil.getBoatOrActorWorldLocation(client, p);
		if (!boatWp.equals(wp))
		{
			data.put("boatX", boatWp.getX());
			data.put("boatY", boatWp.getY());
			data.put("boatPlane", boatWp.getPlane());
		}
```

Replace it with:

```java
		WorldPoint boatWp = RuneLiteUtil.getBoatOrActorWorldLocation(client, p);
		if (!boatWp.equals(wp))
		{
			data.put("boatX", boatWp.getX());
			data.put("boatY", boatWp.getY());
			data.put("boatPlane", boatWp.getPlane());

			// True hull footprint, in tiles -- RuneLite's own WorldEntityConfig bounds on the
			// boat's WorldEntity, not a guessed size. Modeled on the reference usage in the
			// sideloaded Sailing plugin's TrueTileIndicator.renderBoatArea (see
			// data/decompiled/sailing-plugin/). 128 local units per tile is RuneLite's standard
			// LOCAL_TILE_SIZE conversion factor. Omitted (like the boat fields above) whenever
			// the boat's WorldEntity or its config isn't resolvable.
			// UNVERIFIED live as of 2026-08-02 -- values not yet cross-checked against an actual
			// boat's known size class in-game.
			WorldEntity boat = RuneLiteUtil.findTopLevelWorldEntityContaining(client, p.getWorldView());
			WorldEntityConfig boatConfig = boat != null ? boat.getConfig() : null;
			if (boatConfig != null)
			{
				data.put("boatWidth", boatConfig.getBoundsWidth() / 128.0);
				data.put("boatHeight", boatConfig.getBoundsHeight() / 128.0);
			}
		}
```

No new imports are needed — `WorldEntity` and `WorldEntityConfig` are both in `net.runelite.api`, already covered by this file's existing `import net.runelite.api.*;` (line 5).

- [ ] **Step 2: Compile-check**

Run: `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`. This confirms the code compiles against the RuneLite API on the classpath; it does not confirm runtime correctness (see Step 3).

- [ ] **Step 3: Verify live and update the comment**

This step needs a running RuneLite client with the plugin loaded and a character aboard a Sailing boat — hand this step to the user rather than attempting it autonomously if you don't have that access.

1. Start the client per this repo's normal dev flow (`./gradlew.bat run`, per `AGENTS.md`).
2. While aboard a boat: `curl http://localhost:8080/player` and confirm `boatWidth`/`boatHeight` are present with plausible tile values (small integers/decimals, not e.g. thousands — that would indicate a missing `/128.0` conversion).
3. While on foot, not aboard a boat: `curl http://localhost:8080/player` and confirm `boatWidth`/`boatHeight` (and the existing `boatX`/`boatY`/`boatPlane`) are absent from the response.
4. Once confirmed, update the `UNVERIFIED live as of 2026-08-02` line from Step 1 to `CONFIRMED live YYYY-MM-DD: <actual observed values>`, matching this file's existing comment convention.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/providers/PlayerProvider.java
git commit -m "$(cat <<'EOF'
Expose boat's true-tile bounds on /player

Adds boatWidth/boatHeight from WorldEntityConfig so callers can size
hull clearance from real dimensions instead of a guess.
EOF
)"
```

---

### Task 2: Derive `ocean_path.py`'s clearance radius from the boat's real bounds

**Files:**
- Modify: `ControllerAddon/ocean_path.py:1-92`

**Interfaces:**
- Consumes: `controller.get_player() -> dict` (existing, already imported via `import controller` at the top of this file), specifically the optional `boatWidth`/`boatHeight` keys added in Task 1.
- Produces: `_boat_clearance_tiles(player: dict) -> int` (new, pure function — no I/O, testable with plain dicts). `_build_passable_set(tiles, plane, blocked, clearance_tiles: int)` gains a fourth parameter (previously read the module-level `BOAT_CLEARANCE_TILES` constant directly).

- [ ] **Step 1: Replace the constant and dilation call site**

Open `ControllerAddon/ocean_path.py`. Add `import math` to the top imports (currently just `import heapq` and `import controller`):

```python
import heapq
import math

import controller
```

Replace the existing `BOAT_CLEARANCE_TILES` constant and its long comment block (currently lines 18-27):

```python
BOAT_CLEARANCE_TILES = 2  # dilation radius around blocked tiles -- so a route's center-line
                           # staying clear of a blocked tile doesn't mean the boat's actual hull
                           # does too. CONFIRMED live 2026-08-02: the original guess (4, half the
                           # boat's ~8-tile WorldView footprint) sealed off routing entirely in a
                           # real dense island cluster (2708 of 10816 loaded tiles blocked --
                           # dilating by 4 shrank passable water to ~2700 tiles, disconnected, so
                           # find_ocean_route returned None even for the boat's own current
                           # position). 2 keeps a real margin while still leaving usable channels;
                           # the stuck-detection safety net in sailing.py covers cases this margin
                           # turns out too tight for.
```

with:

```python
_FALLBACK_CLEARANCE_TILES = 2  # dilation radius used when the boat's true bounds aren't
                                # available (not aboard a boat, or an older bridge build without
                                # PlayerProvider's boatWidth/boatHeight fields). CONFIRMED live
                                # 2026-08-02 as a reasonable default in its own right: the original
                                # guess (4, half the boat's ~8-tile WorldView footprint) sealed off
                                # routing entirely in a real dense island cluster (2708 of 10816
                                # loaded tiles blocked -- dilating by 4 shrank passable water to
                                # ~2700 tiles, disconnected, so find_ocean_route returned None even
                                # for the boat's own current position). 2 keeps a real margin while
                                # still leaving usable channels; the stuck-detection safety net in
                                # sailing.py covers cases this margin turns out too tight for.
```

- [ ] **Step 2: Add `_boat_clearance_tiles` and wire it into `find_ocean_route`**

In `find_ocean_route`, find this line (currently line 62):

```python
    passable = _build_passable_set(tiles, plane, blocked)
```

Replace it with:

```python
    clearance_tiles = _boat_clearance_tiles(controller.get_player())
    passable = _build_passable_set(tiles, plane, blocked, clearance_tiles)
```

Then add the new function right after `find_ocean_route` (before `_build_passable_set`):

```python
def _boat_clearance_tiles(player: dict) -> int:
    """Isotropic dilation radius (in tiles) around blocked tiles, sized to the boat's own true
    hull footprint (PlayerProvider's boatWidth/boatHeight -- RuneLite's own WorldEntityConfig
    bounds, see data/decompiled/sailing-plugin's TrueTileIndicator for the reference usage this is
    modeled on) instead of a guessed constant. Uses the larger of width/height, halved and rounded
    up, so the radius covers the hull regardless of current heading -- this dilation still has no
    orientation awareness, same as before (see the 2026-08-02 design note on scope: full oriented-
    rectangle collision was explicitly out of scope). Falls back to the original hand-tuned guess
    when the boat's true bounds aren't available (not aboard a boat, or an older bridge build
    without these fields)."""
    width = player.get("boatWidth")
    height = player.get("boatHeight")
    if width is None or height is None:
        return _FALLBACK_CLEARANCE_TILES
    return max(1, math.ceil(max(width, height) / 2))
```

- [ ] **Step 3: Update `_build_passable_set` to take the radius as a parameter**

Find the existing function (currently lines 82-92):

```python
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
```

Replace it with:

```python
def _build_passable_set(tiles, plane, blocked, clearance_tiles):
    """Every loaded tile that ISN'T within clearance_tiles (Chebyshev) of a blocked tile --
    dilating outward from each blocked tile is cheaper than testing every candidate tile against
    every blocked tile."""
    passable = {(t["x"], t["y"]) for t in tiles if t.get("plane") == plane}
    clearance_zone = set()
    for (bx, by) in blocked:
        for dx in range(-clearance_tiles, clearance_tiles + 1):
            for dy in range(-clearance_tiles, clearance_tiles + 1):
                clearance_zone.add((bx + dx, by + dy))
    return passable - clearance_zone
```

- [ ] **Step 4: Verify `_boat_clearance_tiles` with plain assertions**

This repo has no pytest/test-runner infrastructure anywhere, so match its existing manual-verification convention instead of introducing one. Run this directly:

```bash
cd ControllerAddon
python -c "
from ocean_path import _boat_clearance_tiles, _FALLBACK_CLEARANCE_TILES
import math

assert _boat_clearance_tiles({'boatWidth': 6.0, 'boatHeight': 10.0}) == 5, 'expected ceil(10/2)=5'
assert _boat_clearance_tiles({'boatWidth': 3.0, 'boatHeight': 3.0}) == 2, 'expected ceil(3/2)=2'
assert _boat_clearance_tiles({'boatWidth': 1.0, 'boatHeight': 1.0}) == 1, 'expected min clamp of 1'
assert _boat_clearance_tiles({}) == _FALLBACK_CLEARANCE_TILES, 'expected fallback when fields missing'
assert _boat_clearance_tiles({'boatWidth': 6.0}) == _FALLBACK_CLEARANCE_TILES, 'expected fallback when only one field present'
print('all _boat_clearance_tiles assertions passed')
"
```

Expected output: `all _boat_clearance_tiles assertions passed`. If any `AssertionError` fires, fix `_boat_clearance_tiles` before continuing.

- [ ] **Step 5: Verify live, once Task 1 is confirmed**

With Task 1's bridge change confirmed live and the character aboard a boat near a blocked tile cluster, run a `find_ocean_route` call (e.g. via the existing `python controller.py routines/shipwreck_salvaging.py` live flow, or a quick `python -c` call against a real target) and confirm routing still succeeds and the boat doesn't visibly clip land. This mirrors how every other live-data change in this file was verified (see the `CONFIRMED live 2026-08-02` comments already in this file).

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/ocean_path.py
git commit -m "$(cat <<'EOF'
Derive ocean_path.py's boat clearance from real hull bounds

Replaces the guessed BOAT_CLEARANCE_TILES constant with a radius
computed from PlayerProvider's boatWidth/boatHeight, falling back to
the old guess when that data isn't available.
EOF
)"
```

---

## Self-Review

**Spec coverage:** Task 1 covers the bridge-side true-tile bounds exposure; Task 2 covers `ocean_path.py`'s consumption, fallback, and the scope constraint (isotropic dilation only, no oriented-rectangle collision) discussed during design. No gaps against the agreed design.

**Placeholder scan:** No TBD/TODO markers; every step has literal, complete code.

**Type consistency:** `_boat_clearance_tiles(player: dict) -> int` in Task 2 Step 2 matches its call site in Step 1 (`clearance_tiles = _boat_clearance_tiles(controller.get_player())`) and its consumption in `_build_passable_set(tiles, plane, blocked, clearance_tiles)` in Step 3. `boatWidth`/`boatHeight` field names match exactly between Task 1's Java `data.put(...)` calls and Task 2's Python `player.get(...)` calls.
