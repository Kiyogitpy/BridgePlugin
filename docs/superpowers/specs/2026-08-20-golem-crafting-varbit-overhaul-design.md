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

Two further inefficiencies are independent of the desync but sit in the same code path, and are
fixed here because the rewrite touches exactly those lines:

4. **The routine waits out each plinth's reset.** After a golem completes it sleeps 2.0s for the
   plinth to become available again, when the *other* plinth is free immediately (see 3b).
5. **It scans the ground for loot that is never there.** The gift goes straight into the gem bag;
   the three-attempt pickup loop guards a case that does not normally occur (see 3a).

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

**Station selection.** Prefer a station with `progress > 0` (a golem already in progress). If
both are in progress, take the one with the higher `progress` — finishing the more advanced golem
first minimises the window in which a crash could strand its already-spent 4 sunstone. If neither
is in progress, take the station that is **not** the one just completed (see Alternating plinths
below), falling back to the nearer one. This replaces `find_golem_object`'s object-name scan and
the `gc_craft_golem_id` commitment tracking — the varbit *is* the identity, and it cannot desync
across a restart.

**Side ordering.** Compute the carve order so the last side carved is `insertSide`, leaving the
player already standing on the correct tile for Insert-core:

- Order the four sides rotationally (`N, E, S, W`) from a start side, stepping clockwise or
  counter-clockwise such that `insertSide` is reached last. Direction follows the reference
  plugin's `isClockwise` rule: clockwise if
  `(finalIdx - startIdx) mod 4 >= (startIdx - finalIdx) mod 4`.
- **The start side must be adjacent to `insertSide`.** Rotating four steps from `start` ends on
  `start + 3` (clockwise) or `start + 1` (counter-clockwise), so a full four-side carve can only
  finish on `insertSide` if `start` is one of its two neighbours. Starting on `insertSide` itself,
  or on the side opposite it, makes ending there impossible. Both plinths insert from a
  north/south side, so **east and west are the only valid start sides for either** — which is
  exactly why the reference plugin's `StartDirection` enum contains only `EAST` and `WEST`, a
  constraint that reads as an arbitrary limitation until this is derived. Verified exhaustively
  over all four starts × both insert sides.
- Given only two valid starts, pick **whichever of east/west is nearer to the player's current
  position**, rather than fixing one as a constant. Both produce a correct four-side order, so
  the choice is free, and making it adaptive is what keeps plinth alternation cheap: arriving
  from the other plinth, the routine begins on the near side instead of walking around first.
- Skip any side whose `done` varbit is already set. This is what makes a resumed mid-golem run
  correct for free: the routine simply carves whatever is genuinely left.

  Note the interaction: on a *resumed* golem the remaining sides may not include a valid
  east/west start, and the surviving order may not end on `insertSide`. That is fine and must not
  be treated as an error — the routine carves what remains in rotational order and walks to
  `insertSide` afterwards if it is not already there. The end-on-insert optimisation is a
  best-effort saving on fresh golems, never a correctness requirement.

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

### 3a. Loot goes to the gem bag — no ground pickup

**CONFIRMED by the user 2026-08-20:** the gem the finished golem awards goes **straight into the
gem bag**; there is nothing to pick up off the ground. The reference plugin's loot message regex
corroborates this by distinguishing all three destinations:

```
As you complete the golem it leaves a gift (on the ground|in your gem sack|in your gem bag)
for you: (\d+) x (.*)\.
```

The entire post-completion pickup block is therefore **deleted**, not kept
([golem_crafting.py:1010-1044](../../../ControllerAddon/routines/golem_crafting.py#L1010-L1044)):
the ground-item scan, the three-attempt `click_menu_action` retry with fresh position re-lookup,
its 0.6–1.0s per-attempt settle, and the trailing 2.0s plinth-reset sleep. On the common path
that removes roughly 2 seconds and up to three clicks from **every golem**, and it is what makes
plinth alternation worth doing — with no pickup to perform, there is no reason to remain at a
plinth that has just entered its reset.

**The one case that still needs handling** is a full gem bag, where the gift falls to the ground
instead. Rather than pay for a ground scan every golem to catch a rare case, detect it from the
message itself: `on_event` matches the loot line and flags `gc_gem_bag_full` when capture group 1
is `"on the ground"`. When that flag is set, `craft_phase` picks the item up once and routes to
`bank` to empty the bag. Costs nothing in the common case and cannot silently drop loot — which
was the original concern behind the retry loop being written in the first place ("it missed
looting its gem, we want always the loot").

This requires `on_event` to keep a golem-crafting chat branch after all, contrary to what the
deletion list below would otherwise say — but a much narrower one: the loot line only, not the
side/insert confirmation messages that the varbits now supersede.

### 3b. Alternating plinths

**User-directed 2026-08-20.** Rather than waiting out a completed plinth's reset, immediately
begin the next golem on the *other* plinth. Wyrmscraig has two, and their geometry makes this
nearly free — the two insert tiles are **2 tiles apart**:

| Station | Object | Golem tile | Insert side | Insert tile |
|---|---|---|---|---|
| north | 62351 | 2596, 2257 | south | 2596, 2256 |
| south | 62352 | 2595, 2253 | north | 2595, 2254 |

So the player finishes a golem standing on one insert tile and is already within two tiles of the
other plinth's. Combined with the adaptive east/west start side above, the routine arrives and
begins carving on the near side without walking around first.

The cycle becomes: complete on A → walk to B → carve and complete on B → walk back to A, which
reset long ago. Only ever **one golem in progress at a time** — this is about never waiting on a
reset, not about running two golems concurrently. Two concurrent golems would double the
sunstone at risk (8 rather than 4 committed and unrecoverable on a crash) for no throughput gain,
since the player can only act on one plinth at a time.

Implementation is small, given station selection above already prefers a station with
`progress > 0`: record the just-completed station in `ctx.state.data["gc_last_station"]`, and
when neither plinth is in progress prefer the other one. If that key is missing — a fresh start
or a restart — either plinth is correct, so no reconciliation is needed. The existing
`GOLEM_STATE_NAMES`-based waiting for a plinth to read `Empty plinth` again disappears entirely;
the `progress` varbit answers it directly and the routine is elsewhere while it resets.

**What is deliberately kept** from the current implementation, unchanged:

- `walk_to_exact_tile`'s shift+click force-walk and `refresh_target` re-aim.
- The `_hop_toward_point` off-screen fallback.
- `handle_missing_target`'s walk-back-and-retry ladder.

**What is deleted:** `gc_craft_sides_done`, `gc_craft_insert_tried`, `gc_craft_awaiting`,
`gc_craft_extra_side_attempts`, `GOLEM_EXTRA_SIDE_ATTEMPTS_CAP`,
`GOLEM_EXTRA_SIDE_MISSES_BEFORE_ROTATE`, the camera-nudge escalation, the post-completion loot
pickup and its 2.0s settle (see 3a), and `on_event`'s side/insert confirmation branches.
`on_event` keeps its hunt-phase chat handling and gains the narrow loot-line branch from 3a.

### 4. Material cost: 4 sunstone per golem (resolved)

**CONFIRMED by the user 2026-08-20:** `Start-golem` consumes **4 raw sunstone**, all deducted on
that first click. This matches the reference plugin's `hasGolemMaterials()` gate
(`sunstoneCount < 4` → cannot start) and **contradicts this routine's header comment**, which
claims 1 raw sunstone with the remaining three sides free — a claim explicitly self-flagged there
as `confirmed once, n=1`
([golem_crafting.py:31-35](../../../ControllerAddon/routines/golem_crafting.py#L31-L35)). That
comment is wrong and must be corrected as part of this work.

**The per-trip budget is already correct, by coincidence.** Each golem costs 4 raw sunstone plus
1 core, and a core is itself 1 raw sunstone chiselled 1-for-1 — so 5 sunstone-equivalents per
golem. The existing constants happen to balance exactly:

```
SUNSTONE_PER_TRIP = 20  mined
  - CORES_PER_TRIP = 4  chiselled into cores   ->  16 raw + 4 cores
  4 golems x (4 raw + 1 core)                  =   16 raw + 4 cores   (exact, zero slack)
```

`SUNSTONE_PER_TRIP` and `CORES_PER_TRIP` therefore stay as they are. `mine_phase`'s budget check
(`raw_count + cores_have >= SUNSTONE_PER_TRIP`) is also still correct, since chiselling preserves
that sum.

**What is wrong is the gating.** Two changes follow:

- `craft_phase`'s pre-`Start-golem` check tests only that raw sunstone and a core *exist*
  ([golem_crafting.py:1087](../../../ControllerAddon/routines/golem_crafting.py#L1087)). With a
  cost of 4, holding 1–3 raw sunstone passes that check, fires a doomed `Start-golem`, and burns
  a full click plus confirmation wait before the rejection routes it to `mine`. Replace with a
  count check: `raw >= 4 and cores >= 1`.
- Because the cost is charged entirely on the first click, **abandoning a half-carved golem
  destroys 4 sunstone** — a full golem's worth of mining. The current implementation's
  `GOLEM_EXTRA_SIDE_ATTEMPTS_CAP` path does exactly that whenever side-tracking desyncs
  ([golem_crafting.py:1186-1197](../../../ControllerAddon/routines/golem_crafting.py#L1186-L1197)).
  With zero slack in the budget, one abandonment costs the trip its fourth golem outright. This
  makes the varbit rewrite a throughput fix, not only a reliability one — reading `done` from
  varbits removes the desync that triggers abandonment in the first place.

Zero slack is acceptable *given* that the abandonment path is gone; it is not worth padding
`SUNSTONE_PER_TRIP` to absorb a failure mode this work eliminates. If live runs still show the
fourth golem stranded, revisit then with the real cause in hand rather than pre-emptively.

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
- **Unit**: that only east/west starts can end on `insertSide`, asserted directly rather than
  left implicit — all four starts × both insert sides, exactly the exhaustive check that derived
  the constraint. This is what stops a later "why is the start side restricted?" simplification
  from silently reintroducing the extra walk.
- **Unit**: the `Start-golem` material gate, at the 4-sunstone boundary specifically — 3 raw
  must not attempt a start, 4 raw with 1 core must. This is the check whose off-by-existence
  version is being replaced (see section 4), so the boundary is the whole point of the test.
- **Unit**: station selection — prefers a station with `progress > 0`; prefers the higher
  `progress` when both are in progress; alternates away from `gc_last_station` when neither is;
  and tolerates `gc_last_station` being absent (fresh start or restart).
- **Java**: no unit tests — this codebase's providers have none, and the value is entirely in
  the live read.
- **Live verification**, per CLAUDE.md's live-debugging workflow: `py_compile` every touched
  file, run `python -m pytest tests/`, then `./gradlew.bat run` for the Java changes. Confirm
  `GET /golem` reports plausible values against a plinth in a known state before running the
  routine. Then watch a full golem carve end-to-end, confirming three things specifically:
  the player does **not** walk after the final side before Insert-core; the player does **not**
  linger at the completed plinth (no ground scan, no 2s settle) and instead heads for the other
  one; and the gem arrives in the bag with nothing left on the ground.
- **Two golems back to back** is the alternation test: watch A complete, B start immediately, and
  A be reused on the third — confirming the reset never actually gets waited on.
- **The resumed-run case is the one that matters most** and must be tested explicitly: kill the
  routine mid-golem, restart it, and confirm it carves only the genuinely remaining sides. That
  is the exact scenario the current implementation cannot handle. Include a resume where the
  remaining sides do *not* admit an east/west start, confirming the routine still finishes and
  simply walks to `insertSide` rather than treating it as an error.
- **Gem bag full** should be provoked at least once if practical (or the flag forced by hand),
  confirming the `"on the ground"` path picks the gift up and routes to `bank`. This is the only
  route by which loot can now be permanently lost, so it should not ship purely reasoned-about.

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
