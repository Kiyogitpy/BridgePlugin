# Golem Crafting: varbit-backed state overhaul

Supersedes the `craft_phase` sections of
`docs/superpowers/specs/2026-08-07-golem-crafting-design.md`. That document's research was done
against the live wiki before the routine had ever run; this one is grounded in decompiled source
for the game's own state variables.

## Problem

`routines/golem_crafting.py` runs unattended but is inconsistent and slow. The root cause is
that **`craft_phase` has no ground truth about the golem it is carving.** It tracks which of the
four sides are done in `ctx.state.data["gc_craft_sides_done"]` — a list of `(dx, dy)` offsets it
appends to when a chat message confirms a click landed.

Three consequences follow from that single design choice:

1. **State desyncs and cannot self-correct.** `ctx.state.data` resets to `{}` on every process
   restart and on every edit to the routine file, but the plinth's real server-side progress
   persists. A resumed run's four tracked offsets do not correspond to the four real remaining
   sides, so one real side gets re-attempted while another is never tried. The existing code
   documents this happening live and works around it with a 16-attempt retry budget, camera
   nudges from attempt 8 onward, and finally abandoning the golem entirely
   ([golem_crafting.py:1162-1214](../../../ControllerAddon/routines/golem_crafting.py#L1162-L1214)).
   Every one of those attempts is a walk plus a click plus a confirmation wait.

2. **Confirmation is slow.** Each click waits for a chat message with
   `GOLEM_ACTION_TIMEOUT_S = 15.0`. A dropped or unmatched message stalls the routine for a full
   15 seconds before it retries.

3. **Walking is unplanned.** Sides are carved in fixed `N, E, S, W` order, then the routine walks
   *again* to the insert side. That extra walk happens on every single golem.

The user additionally reports problems in the mining, banking/fur, and hunting phases. Those are
scoped out of this document — see Decomposition.

## Source of the fix

RuneLite's own `VarbitID` class — already on this project's classpath — exposes the complete
golem-crafting state as named constants. Verified by `javap` against
`runelite-api-1.12.36.jar`:

```
GOLEM_CRAFTING_STATION_1        = 15728   GOLEM_CRAFTING_STATION_2        = 15733
GOLEM_CRAFTING_STATION_1_NORTH  = 15729   GOLEM_CRAFTING_STATION_2_NORTH  = 15734
GOLEM_CRAFTING_STATION_1_EAST   = 15730   GOLEM_CRAFTING_STATION_2_EAST   = 15735
GOLEM_CRAFTING_STATION_1_SOUTH  = 15731   GOLEM_CRAFTING_STATION_2_SOUTH  = 15736
GOLEM_CRAFTING_STATION_1_WEST   = 15732   GOLEM_CRAFTING_STATION_2_WEST   = 15737
GOLEM_1_NORTH_PROGRESS          = 15739   GOLEM_2_NORTH_PROGRESS          = 15743
GOLEM_1_EAST_PROGRESS           = 15740   GOLEM_2_EAST_PROGRESS           = 15744
GOLEM_1_SOUTH_PROGRESS          = 15741   GOLEM_2_SOUTH_PROGRESS          = 15745
GOLEM_1_WEST_PROGRESS           = 15742   GOLEM_2_WEST_PROGRESS           = 15746
GOLEM_CRAFTING_COUNT            = 15738   BUSY                            = 12393
```

Semantics were cross-referenced against the decompiled `Golem Crafting` plugin-hub plugin
(`org.zhbot.golem_crafting`, decompiled from
`~/.runelite/plugins/golem-crafting_3J61T7eKfjJwQ7H5_2GXC02_EnRVeVmKgOLPJoMzboM.jar` with CFR
0.152). That plugin's `Golem`, `NorthGolem` and `SouthGolem` classes establish:

- A side's `*_STATE` varbit is non-zero exactly when that side is carved (`isNorthDone()` etc.).
- A side's `*_PROGRESS` varbit runs 0–10 and is rendered as a fraction (`value / 10.0`).
- `GOLEM_CRAFTING_STATION_N` is the golem's overall progress; `> 0` means a golem is in progress
  at that station, which is how the plugin picks the active one (`getActiveGolem()`).
- Insert-core accepts **exactly one** side per station, fixed: station 1 (north, object `62351`,
  tile `2596, 2257`) takes it from the **south**; station 2 (south, object `62352`, tile
  `2595, 2253`) takes it from the **north**. This confirms the existing
  `GOLEM_INSERT_OFFSET_BY_ID` table, which until now was a live-observed guess with a blind
  four-side fallback.
- The crafting animation is `14458`; the area centre is `2590, 2250` with a radius of 15.

Note that these are **plain varbit reads through RuneLite's public API**. Nothing here depends on
the third-party plugin being installed or enabled at runtime; it was used as documentation only.
This mirrors how `MixologyProvider` was built (see that file's class comment).

## Approach

Add a `GolemProvider` exposing `GET /golem`, then rewrite `craft_phase` against it. Rejected
alternatives:

- **Relying on the installed plugin's menu-entry removal.** The user's profile already has
  `plinthRemoveShapeGolem=true` and `plinthRemoveInsertCore=true`, and our dev client calls
  `RuneLite.main()` so the plugin does load. That makes wrong clicks safer, but leaves us blind:
  we still cannot plan a rotation, still cannot confirm faster than chat, and we would acquire a
  silent runtime dependency on a plugin-hub plugin staying installed. Kept as an incidental
  safety net, not as the mechanism.
- **Rewriting `golem_crafting.py` from scratch.** The file is 1938 lines, much of it
  `CORRECTED 2026-08-08` commentary. That commentary encodes live-verified knowledge (misclick
  handling, the ~2s plinth reset delay, the loot-pickup race, bank keep-list edge cases) that
  would be silently regressed by a clean-room rewrite.

## Design

### 1. `GolemProvider` → `GET /golem`

New file `src/main/java/com/example/providers/GolemProvider.java`, following
`MixologyProvider`'s structure exactly: extends `BridgeProvider`, registers one path, reads
everything on the client thread via `onClient`.

Registered in `ControllerPlugin.java` alongside the existing providers:

```java
new GolemProvider(client, clientThread, server).register();
```

**Not** gated by `TickCache`. Per CLAUDE.md, only the six bulk list endpoints are cached; a
single-entity state read like this must stay fresh so it can serve as a sub-tick confirmation
signal.

Response shape:

```json
{
  "inArea": true,
  "totalCrafted": 1247,
  "busy": 0,
  "stations": [
    {
      "name": "north",
      "objectId": 62351,
      "x": 2596, "y": 2257, "plane": 0,
      "progress": 2,
      "insertSide": "south",
      "sides": {
        "north": { "done": true,  "progress": 10, "x": 2596, "y": 2258 },
        "east":  { "done": false, "progress": 4,  "x": 2597, "y": 2257 },
        "south": { "done": false, "progress": 0,  "x": 2596, "y": 2256 },
        "west":  { "done": false, "progress": 0,  "x": 2595, "y": 2257 }
      }
    },
    { "name": "south", "objectId": 62352, "x": 2595, "y": 2253, "insertSide": "north", "...": "..." }
  ]
}
```

Field notes:

- `sides.*.x/y` are computed in Java as `golemTile ± 1` on the relevant axis. Wyrmscraig is not
  instanced, so the forward-conversion gap described in CLAUDE.md does not apply here; these are
  plain arithmetic on a constant, not a `LocalPoint.fromWorld()` lookup.
- `sides.*.progress` is the raw 0–10 varbit value, not the plugin's `/10.0` fraction. Raw values
  keep the endpoint a state mirror rather than a presentation layer.
- `insertSide` is a static per-station constant, not a varbit read.
- `busy` is the raw `VarbitID.BUSY` value. Its exact meaning is **not yet verified** for this
  activity — the reference plugin only timestamps when it changes. Exposed as raw data for
  diagnosis; no logic in this spec depends on it.
- `inArea` is `distanceTo(2590, 2250) <= 15`, matching the reference plugin's own area test.
- The provider deliberately exposes **no policy**: no "which side should I do next", no material
  sufficiency verdict. Rotation choice belongs in Python, consistent with how every other
  provider in this codebase is a state mirror.

Material counts are *not* included — `/inventory` and `/equipment` already serve them, and
duplicating them here would create two sources of truth for the same fact.

### 2. `PlayerProvider`: add `craftingXp`

One line, following the pattern CLAUDE.md documents for sub-tick XP confirmation:

```java
data.put("craftingXp", client.getSkillExperience(Skill.CRAFTING));
```

`animation` is already exposed, so the `14458` crafting-animation check needs no Java change.

Both Java changes require `./gradlew.bat run` (rebuild + client restart) before the Python side
can see them.

### 3. `craft_phase` rewritten against `/golem`

The phase becomes a deterministic function of live state. New helper `controller.get_golem()`
alongside the existing bridge getters.

**Station selection.** Pick the station whose `progress > 0` (a golem is in progress there). If
neither is in progress, pick the nearer one and start a golem on it. This replaces
`find_golem_object`'s object-name scan and the `gc_craft_golem_id` commitment tracking — the
varbit *is* the identity, and it cannot desync across a restart.

**Side ordering.** Compute the carve order so the last side carved is `insertSide`, leaving the
player already standing on the correct tile for Insert-core:

- Order the four sides rotationally (`N, E, S, W`) starting from a start side. This is a module
  constant `GOLEM_START_SIDE = "east"` (matching the reference plugin's
  `southGolemStartDirection=EAST`), **not** a bridge-config field: it is not a player-facing
  choice, and adding it to `ControllerConfig.java` would mean a Java rebuild to tune a constant.
  Same reasoning as `FUR_PRIORITY` already being a module constant rather than exported config
  ([golem_crafting.py:63-70](../../../ControllerAddon/routines/golem_crafting.py#L63-L70)).
- Choose rotation direction (clockwise or counter-clockwise) such that `insertSide` is reached
  last, using the reference plugin's `isClockwise` rule: clockwise if
  `(finalIdx - startIdx) mod 4 >= (startIdx - finalIdx) mod 4`.
- Skip any side whose `done` varbit is already set. This is what makes a resumed mid-golem run
  correct for free: the routine simply carves whatever is genuinely left.

**Per-side loop.** For the next not-`done` side: `walk_to_exact_tile` to `sides[side].x/y`, then
click the station. Confirm by polling `/golem` until that side's `done` flips true.

**Confirmation.** Replaces the 15-second chat wait. Poll `/golem` and treat the side's `done`
varbit flipping to true as success — this is immediate on the tick the server processes the
action. Retain a much shorter timeout (`GOLEM_SIDE_TIMEOUT_S = 4.0`) after which the click is
retried; a varbit that has not moved in four seconds means the click genuinely did not land
(most often a deliberate `MISTAKE_MISCLICK_CHANCE` near-miss, which produces no game action at
all — see CLAUDE.md). Retrying a missed click is cheap and correct now, because the varbit tells
us unambiguously whether the previous one landed.

**Insert-core.** Go directly to `insertSide` — already the current tile if the rotation worked.
Click, then confirm on `station.progress` returning to 0 (golem complete) or `totalCrafted`
incrementing. The blind four-side trial-and-error and its
`"Insert-core was rejected from all 4 sides"` safety stop are both deleted.

**What is deliberately kept** from the current implementation, unchanged:

- The post-completion loot pickup with its three-attempt retry and fresh position re-lookup
  ([golem_crafting.py:1010-1044](../../../ControllerAddon/routines/golem_crafting.py#L1010-L1044)),
  including the 2.0s plinth-reset settle.
- `walk_to_exact_tile`'s shift+click force-walk and `refresh_target` re-aim.
- The `_hop_toward_point` off-screen fallback.
- `handle_missing_target`'s walk-back-and-retry ladder.

**What is deleted:** `gc_craft_sides_done`, `gc_craft_insert_tried`, `gc_craft_awaiting`,
`gc_craft_extra_side_attempts`, `GOLEM_EXTRA_SIDE_ATTEMPTS_CAP`,
`GOLEM_EXTRA_SIDE_MISSES_BEFORE_ROTATE`, the camera-nudge escalation, and the golem-side branches
of `on_event`'s chat capture. `on_event` keeps its hunt-phase chat handling.

### 4. Open question to resolve live, first thing

The reference plugin's `hasGolemMaterials()` requires `sunstoneCount >= 4` to start a golem. This
routine's header comment claims `Start-golem` consumes exactly **1** raw sunstone, with the
material cost front-loaded and the remaining three sides free — explicitly flagged there as
`confirmed once, n=1`
([golem_crafting.py:31-35](../../../ControllerAddon/routines/golem_crafting.py#L31-L35)).

If the real cost is 1 per side, the current per-trip budget (`SUNSTONE_PER_TRIP = 20`,
`CORES_PER_TRIP = 4` → 16 raw for 4 golems) is consumed exactly, with zero slack, which would
explain runs bailing to `mine` unexpectedly.

**Resolution:** before implementing the side loop, snapshot the raw-sunstone count immediately
before and after a single `Start-golem`, and again after one `Shape-golem`. This is a
two-minute check against the running client and it determines whether `SUNSTONE_PER_TRIP` needs
to change. Do not implement budget changes on the assumption; measure first.

## Error handling and safety

- No station found in `/golem` (e.g. wrong location, bridge on an old build): fall back to
  `handle_missing_target`, then `trigger_safety_stop`, unchanged from today.
- `/golem` returning 404 (Java side not yet rebuilt): `get_golem()` returns `None` and
  `craft_phase` safety-stops with an explicit "rebuild the client" message rather than silently
  degrading to the old blind path. Two code paths for the same phase is exactly the kind of
  divergence that produced the current bug.
- A side's `done` varbit never flipping after several retries at the same side: safety-stop with
  the full station state in the message. This replaces a 16-attempt budget with a genuine
  assertion, because with ground truth available a stuck side is a real anomaly, not an expected
  desync to be absorbed.

## Testing

- **Unit** (`ControllerAddon/tests/test_golem_crafting.py`, new): the side-ordering function is
  pure and table-testable. Cover, for both stations and both start directions: full four-side
  order ends on `insertSide`; already-`done` sides are skipped; a single remaining side that *is*
  the insert side; all four done → no sides to carve. No bridge or serial access.
- **Java**: no unit tests — this codebase's providers have none, and the value is entirely in
  the live read.
- **Live verification**, per CLAUDE.md's live-debugging workflow: `py_compile` every touched
  file, run `python -m pytest tests/`, then `./gradlew.bat run` for the Java changes. Confirm
  `GET /golem` reports plausible values against a plinth in a known state before running the
  routine. Then watch a full golem carve end-to-end — specifically confirming the player does
  **not** walk after the final side before Insert-core.
- **The resumed-run case is the one that matters most** and must be tested explicitly: kill the
  routine mid-golem, restart it, and confirm it carves only the genuinely remaining sides. That
  is the exact scenario the current implementation cannot handle.

## Decomposition

This document covers sub-projects 1 and 2 only. The endpoint and the `craft_phase` rewrite are
specced together because the endpoint's shape is determined by what `craft_phase` needs from it.

Follow-on cycles, each getting its own spec:

3. **`mine_phase`.** Revisit monolith-only mining against the rock "momentum" mechanic the
   reference plugin overlays: rocks `62393`/`62394`, monolith `62216`, momentum being a 5-tick
   window after `"You manage to mine some sunstone."` on a **rock** (the monolith explicitly
   clears it). The current implementation abandoned rocks entirely after a bad live session;
   momentum is a concrete reason to revisit that, and `miningXp` gives per-swing confirmation.
4. **`bank_phase` / fur / `hunt_phase`.** The blind `gc_fur_stock` counter can be replaced with
   an authoritative reading: the reference plugin parses
   `"Your fur pouch is currently holding (\d+) fur."` from a Check on the pouch. Fur pouch count
   is **not** varbit-backed — the reference plugin persists it in its own config and increments
   it on catch messages — so a periodic Check is the only ground truth available. Also covers
   the user-reported hunting-phase problems, which are not yet characterised and will need their
   own investigation before that spec can be written.
