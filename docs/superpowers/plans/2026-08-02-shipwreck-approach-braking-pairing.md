# Shipwreck Approach Braking + Pair-Midpoint Targeting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the boat from ramming shipwrecks during approach, and position between a nearby pair of wrecks (when one exists) instead of always targeting a single wreck, so a re-click after the first sinks doesn't need a second sail trip.

**Architecture:** `sailing.steer_toward` currently only stops hopping once within `SAIL_ARRIVAL_TOLERANCE` (5 tiles) of the target, but the boat keeps coasting under full sail the whole approach and `stop_boat`'s own measured post-toggle drift (2.2-5.0 tiles) then carries it into the wreck. Add a wider `SAIL_BRAKE_TRIGGER_TILES` (10) that `steer_toward` uses for its own arrival decision instead, so it stops hopping — and `sail_to` calls `stop_boat()` — while still several tiles out, letting the known drift close the rest of the gap. Separately, `routines/shipwreck_salvaging.py` gets a new `find_wreck_pair_target` helper that, given a wreck the routine is about to sail to, looks for a second matching active wreck within `PAIR_MAX_TILES` (16) and targets their midpoint instead, falling back to the single wreck if no partner is found.

**Tech Stack:** Python 3 (`ControllerAddon/`), no external test framework — this codebase verifies live against the bridge + Arduino, not with pytest.

## Global Constraints

- Full design rationale lives in `docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md`'s "Design update: approach braking + pair-midpoint targeting (2026-08-02)" section — read it if anything below seems under-explained.
- No unit test framework exists. "Tests" in this plan are `python -m py_compile` for each modified file. The final task is a manual, live, human-executed verification checklist — it cannot be automated.
- Both new constants (`SAIL_BRAKE_TRIGGER_TILES=10`, `PAIR_MAX_TILES=16`) are initial estimates, not live-measured — flagged for tuning after live testing, same convention as every other numeric constant in this codebase (see `sailing.py`'s own header).
- This is a Python-only change (`ControllerAddon/sailing.py`, `ControllerAddon/routines/shipwreck_salvaging.py`) — no Java/provider changes, so only a `controller.py` restart is needed to pick it up, not a client rebuild.

---

### Task 1: `sailing.py` — brake early instead of hopping all the way to the target

**Files:**
- Modify: `ControllerAddon/sailing.py:40-54` (constants), `:95-123` (`steer_toward`), `:146-147` (`_within_tolerance`)

**Interfaces:**
- Produces: `sailing.SAIL_BRAKE_TRIGGER_TILES` (int constant) and a generalized `sailing._within_tolerance(x1, y1, x2, y2, tolerance=SAIL_ARRIVAL_TOLERANCE)` (optional 5th param, backward compatible with `_hop_to`'s existing 4-arg calls). `steer_toward`'s public signature/behavior contract (`steer_toward(ctx, target: dict) -> bool`) is unchanged — only when it returns `True` changes (now once within the brake trigger, not the tight arrival tolerance).

- [ ] **Step 1: Add the new constant next to the existing sailing constants**

In `ControllerAddon/sailing.py`, right after the existing `STOP_TIMEOUT_S = 15.0` line (currently line 46), add:

```python
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
# with margin, small enough that the resulting stop still lands within hook range (maxDistance).
SAIL_BRAKE_TRIGGER_TILES = 10
```

**Files touched:** `ControllerAddon/sailing.py`

- [ ] **Step 2: Generalize `_within_tolerance` to accept an optional tolerance override**

Current code (`ControllerAddon/sailing.py:146-147`):

```python
def _within_tolerance(x1: float, y1: float, x2: float, y2: float) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= SAIL_ARRIVAL_TOLERANCE
```

Replace with:

```python
def _within_tolerance(x1: float, y1: float, x2: float, y2: float, tolerance: float = SAIL_ARRIVAL_TOLERANCE) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= tolerance
```

`_hop_to`'s existing call at line 140 (`_within_tolerance(px, py, x, y)`) is unaffected — it keeps using the default `SAIL_ARRIVAL_TOLERANCE` for its own intermediate-hop tracking during the cruise phase. Only `steer_toward`'s own arrival check (next step) passes the new wider tolerance explicitly.

**Files touched:** `ControllerAddon/sailing.py`

- [ ] **Step 3: Use the brake trigger for `steer_toward`'s own arrival decision**

Current code (`ControllerAddon/sailing.py:95-123`):

```python
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
```

Replace the docstring and the single arrival-check line (everything else in the function is unchanged):

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

**Files touched:** `ControllerAddon/sailing.py`

- [ ] **Step 4: Verify the file compiles**

Run: `cd ControllerAddon && python -m py_compile sailing.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/sailing.py
git commit -m "$(cat <<'EOF'
Brake before reaching the target instead of ramming through it

steer_toward only stopped hopping once within the tight
SAIL_ARRIVAL_TOLERANCE (5 tiles), but the boat keeps coasting under
full sail the entire approach -- by the time sail_to called
stop_boat, the boat was already point-blank, and its own measured
post-toggle drift (2.2-5.0 tiles) carried it into the wreck (a real
collision, confirmed live). Now it stops hopping at the wider
SAIL_BRAKE_TRIGGER_TILES (10) instead, so the known drift closes the
remaining gap and lands the boat short of the target.
EOF
)"
```

---

### Task 2: `routines/shipwreck_salvaging.py` — pair-midpoint target helper

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py:170-178` (constants), add new function after `find_nearby_wreck` (currently ending at line 231)

**Interfaces:**
- Consumes: `controller.get_objects(radius=...)` (existing, `ControllerAddon/controller.py`), `SUNK_SHIPWRECK_IDS`, `WRECK_SEARCH_RADIUS_PADDING` (existing module-level constants in this same file).
- Produces: `PAIR_MAX_TILES` (int constant) and `find_wreck_pair_target(config: dict, wreck: dict) -> dict` returning `{"x": int, "y": int, "plane": int}` — always the given wreck's own position if no partner is found, or the two wrecks' midpoint if one is. Task 3 calls this wherever a wreck is currently passed straight to `sailing.sail_to`.

- [ ] **Step 1: Add the `PAIR_MAX_TILES` constant next to `WRECK_SEARCH_RADIUS_PADDING`**

In `ControllerAddon/routines/shipwreck_salvaging.py`, right after the existing `WRECK_SEARCH_RADIUS_PADDING = 10` line (currently line 178), add:

```python
WRECK_SEARCH_RADIUS_PADDING = 10

# Max distance (tiles, between the two wrecks themselves) for find_wreck_pair_target to treat two
# active wrecks as a pair worth positioning between. Shipwrecks spawn in close pairs at each
# hotspot (wiki-confirmed: "a different pair of shipwrecks", "double Salvage spots" -- see
# data/raw/S/Shipwreck salvaging.txt), and the hook does NOT auto-continue onto a different active
# wreck once its own sinks (CONFIRMED live 2026-08-01) -- so pre-positioning at the midpoint of a
# pair means a re-click after the first sinks can pick up the second immediately, no second sail
# trip needed. Initial estimate (not yet live-tuned): twice the hook's maxDistance (8), so a
# midpoint puts both wrecks within hook range.
PAIR_MAX_TILES = 16
```

**Files touched:** `ControllerAddon/routines/shipwreck_salvaging.py`

- [ ] **Step 2: Add `find_wreck_pair_target` after `find_nearby_wreck`**

Add this new function at the end of the file, immediately after `find_nearby_wreck`'s closing `return best` (currently line 231):

```python
def find_wreck_pair_target(config: dict, wreck: dict) -> dict:
    """Given a wreck the routine is about to sail to, look for a second matching active wreck
    within PAIR_MAX_TILES of it (same tier names as wreck, same plane, not sunk) and target their
    midpoint instead of the lone wreck -- see PAIR_MAX_TILES's comment for why. Falls back to the
    given wreck's own position if no partner is found nearby (confirmed with the user: a lone
    wreck is still worth sailing to directly, pairing is an optimization, not a requirement).

    Returns a plain {"x", "y", "plane"} dict, the same shape sailing.sail_to already expects as
    its target -- not a raw ObjectProvider entry, since a midpoint isn't a real game object."""
    names = {n.lower() for n in config.get("wreckNames", [])}
    wx, wy, wplane = wreck.get("x"), wreck.get("y"), wreck.get("plane")

    best_partner = None
    best_dist = None
    for o in controller.get_objects(radius=int(PAIR_MAX_TILES) + WRECK_SEARCH_RADIUS_PADDING):
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        if o.get("plane") != wplane:
            continue
        ox, oy = o.get("x"), o.get("y")
        if ox is None or oy is None:
            continue
        if ox == wx and oy == wy:
            # This is the wreck itself, not a partner.
            continue

        dist = max(abs(ox - wx), abs(oy - wy))
        if dist > PAIR_MAX_TILES:
            continue
        if best_dist is None or dist < best_dist:
            best_partner, best_dist = o, dist

    if best_partner is None:
        return {"x": wx, "y": wy, "plane": wplane}

    print(f"[salvage] found a wreck pair {best_dist} tiles apart -- sailing to the midpoint")
    return {
        "x": (wx + best_partner["x"]) // 2,
        "y": (wy + best_partner["y"]) // 2,
        "plane": wplane,
    }
```

**Files touched:** `ControllerAddon/routines/shipwreck_salvaging.py`

- [ ] **Step 3: Verify the file compiles**

Run: `cd ControllerAddon && python -m py_compile routines/shipwreck_salvaging.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py
git commit -m "$(cat <<'EOF'
Add find_wreck_pair_target helper for pair-midpoint navigation

Shipwrecks spawn in close pairs at each hotspot, and the hook does
not auto-continue onto a different active wreck once its own sinks
(confirmed live 2026-08-01) -- so pre-positioning between a pair
means a re-click after the first sinks can pick up the second
immediately, no second sail trip needed. Not yet wired into tick().
EOF
)"
```

---

### Task 3: `routines/shipwreck_salvaging.py` — wire the pair target into both `sail_to` call sites

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py:88-126` (`tick`)

**Interfaces:**
- Consumes: `find_wreck_pair_target(config, wreck) -> dict` (Task 2), `sailing.sail_to(ctx, target: dict) -> bool` (existing, `ControllerAddon/sailing.py`).

- [ ] **Step 1: Replace both raw-wreck `sail_to` calls with pair-target calls**

Current code (`ControllerAddon/routines/shipwreck_salvaging.py:88-126`):

```python
    near_wreck = find_nearby_wreck(config, config.get("maxDistance", 15))
    if near_wreck is not None:
        # isIdle only gates the reclick itself (avoid spam-clicking mid-channel) -- it must NOT
        # gate this whole branch, or the far-wreck check below never runs. CONFIRMED live
        # 2026-08-02: salvaging is a continuous multi-tick channel, not brief per-swing bursts
        # like mining.py -- isIdle can stay False for a wreck's entire lifespan, so gating the
        # branch on it meant the routine never noticed the near wreck was gone and never looked
        # for a farther one to sail to.
        # No "not navigating" gate here (there used to be one) -- CONFIRMED live 2026-08-02:
        # get_sailing()["navigating"] reflects whether the helm/steering interface is still
        # engaged, not whether the boat is physically moving, and it can stay True even after the
        # boat has fully stopped (sails down) until the helm is explicitly left somehow. Gating on
        # it here got the routine permanently stuck ("stuck at the helm... not clicking the
        # hook") once navigating got stuck True. stop_boat() already waits for the boat's actual
        # *position* to stabilize before returning, which is the real "safe to deploy" signal.
        idle = controller.get_combat().get("isIdle", True)
        now = time.time()
        if not idle:
            # Channel confirmed actually running -- clear the stuck tracker below.
            ctx.state.data.pop("hook_engage_attempt_at", None)
        cooldown_elapsed = now - ctx.state.data.get("last_deploy_click_at", 0) > HOOK_CLICK_COOLDOWN_S
        if idle and cooldown_elapsed:
            attempt_start = ctx.state.data.setdefault("hook_engage_attempt_at", now)
            if now - attempt_start > HOOK_ENGAGE_TIMEOUT_S:
                print("[salvage] hook isn't engaging after repeated clicks -- repositioning closer")
                sailing.sail_to(ctx, near_wreck)
                ctx.state.data.pop("hook_engage_attempt_at", None)
            sailing.deploy_hook(ctx, config.get("hookName", ""))
            ctx.state.data["last_deploy_click_at"] = now
        return
    ctx.state.data.pop("hook_engage_attempt_at", None)

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60))
    if far_wreck is not None:
        # No "not navigating" gate here either -- see the near-wreck branch's comment above.
        if sailing.sail_to(ctx, far_wreck):
            sailing.deploy_hook(ctx, config.get("hookName", ""))
            ctx.state.data["last_deploy_click_at"] = time.time()
        return
```

Replace the two `sail_to` call lines only (everything else, including all existing comments, is unchanged):

```python
            if now - attempt_start > HOOK_ENGAGE_TIMEOUT_S:
                print("[salvage] hook isn't engaging after repeated clicks -- repositioning closer")
                sailing.sail_to(ctx, find_wreck_pair_target(config, near_wreck))
                ctx.state.data.pop("hook_engage_attempt_at", None)
```

and:

```python
    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60))
    if far_wreck is not None:
        # No "not navigating" gate here either -- see the near-wreck branch's comment above.
        if sailing.sail_to(ctx, find_wreck_pair_target(config, far_wreck)):
            sailing.deploy_hook(ctx, config.get("hookName", ""))
            ctx.state.data["last_deploy_click_at"] = time.time()
        return
```

**Files touched:** `ControllerAddon/routines/shipwreck_salvaging.py`

- [ ] **Step 2: Verify the file compiles**

Run: `cd ControllerAddon && python -m py_compile routines/shipwreck_salvaging.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Verify the full routine still imports cleanly (catches any name typo the compile step alone might miss)**

Run: `cd ControllerAddon && python -c "import routines.shipwreck_salvaging"`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py
git commit -m "$(cat <<'EOF'
Sail to the wreck-pair midpoint instead of a single wreck

Wires find_wreck_pair_target into both places tick() calls
sailing.sail_to for a wreck: the initial far-wreck approach and the
near-wreck stuck-reposition self-heal. Both now target the midpoint
of a nearby wreck pair when one exists, so a re-click after the
first sinks doesn't need a second sail trip.
EOF
)"
```

---

### Task 4: Manual live verification

This cannot be automated — no client/game-state simulation exists in this codebase. Run the routine live and watch for these specific things, matching the two problems reported.

- [ ] **Step 1: Restart the Python side**

This is a Python-only change — restart `controller.py` (no client rebuild needed):

```bash
cd ControllerAddon && python controller.py routines/shipwreck_salvaging.py
```

- [ ] **Step 2: Watch one full far-wreck approach for the braking fix**

Trigger a reposition (either let the routine naturally sail to a farther wreck, or start with nothing in `maxDistance` range but something within `maxSailDistance`). Watch for:
- The `[sailing] toggling sails` log line firing noticeably before the boat visually reaches the wreck, not right next to/on top of it.
- No collision with the wreck's model.
- The `[sailing] stopped -- drifted N.N tiles after toggling sails` line's `N.N` value, and whether the boat's final resting position looks like it's within hook range of the target (close enough that `deploy_hook` successfully engages, not stuck retrying).

If the boat still collides: `SAIL_BRAKE_TRIGGER_TILES` (10) needs to go up. If it stops noticeably farther from the wreck than hook range: it needs to go down. Report the observed drift and resting distance either way.

- [ ] **Step 3: Watch for the pair-midpoint log line**

If a `[salvage] found a wreck pair N tiles apart -- sailing to the midpoint` line appears, confirm both wrecks visually end up within hook range of where the boat stops. If it never appears despite two wrecks clearly being close together in the hotspot, `PAIR_MAX_TILES` (16) may need to go up, or there may be a plane mismatch between the two wreck objects worth checking via `/objects`.

- [ ] **Step 4: Confirm the re-click-after-sink case actually skips a second sail trip**

Let the currently-targeted wreck sink (its despawn timer runs out) while a paired second wreck is still active nearby. Confirm the routine's next `isIdle`-triggered reclick (see `tick()`'s near-wreck branch) picks up the second wreck directly, with no new `[sailing] engaging the helm` / reposition sequence in between.

- [ ] **Step 5: Report back**

Report whether each of steps 2-4 behaved as expected, and any observed drift/resting-distance/pair-spacing numbers -- these feed back into tuning `SAIL_BRAKE_TRIGGER_TILES` and `PAIR_MAX_TILES` the same way the existing drift samples in the design spec were recorded.
