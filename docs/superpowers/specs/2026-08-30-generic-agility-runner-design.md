# Generic Agility Runner — design spec

Date: 2026-08-30
Target: `data/rs2_scripts/` (the TypeScript TaskBot framework — `Locs`/`Traversal`/`GameMessages`/`DeathRecovery` API surface, as used by `WildyAgility`)
Status: DRAFT — awaiting user review

## Goal

One data-driven agility script, `AgilityRunner`, that runs **any standard lap course** from a
per-course definition — replacing the naive `AgilityBot` (name-list + XP-advance, no fail
handling) and generalizing the hardened `WildyAgility` loop into an engine + data split.

**Hard requirement (user, 2026-08-30): Wilderness Agility must keep working.** The wilderness
course is the parity target — every measured behavior in today's `WildyAgility`
(ridge entry, wolf-pit vs obstacle-pit distinction, food/death recovery, resync, retry/skip
ladder) must survive the generalization unchanged. The engine is derived FROM WildyAgility, not
from AgilityBot.

## Research summary (osrs-wiki MCP, wiki snapshot 2026-08-17 / cache 2026-08-26)

What varies across courses — the dimensions the CourseDef must express:

1. **Obstacle sequence.** Ordered loc names + menu ops. Names REPEAT within a course (both
   Draynor tightropes are just `Tightrope`; Gnome has two `Tree branch` / `Obstacle net`), so
   nearest-by-name only works because the player advances spatially; cache loc ids
   (`config/loc`, e.g. `prif_agility_tightrope1` 36234, `op1=Cross`) are available to pin a
   step exactly when nearest-match ever misfires. Cache also carries `forceapproach` — the
   authoritative "which side must you be on" fact behind wrong-side failures.
2. **Fail modes are per-obstacle, and there are exactly four shapes:**
   - `none` — cannot fail (wilderness pipe/rocks, most low-level rooftop steps post-2024).
   - `knockback` — same-scene damage, player stays put (wilderness stepping stone: fail only
     on the third stone, ⌊hp×0.2⌋+1 damage).
   - `pit` — teleport to a dungeon/pit scene (wilderness ropeswing/log: ⌊hp×0.15⌋+1; on this
     server the pit reads as a high world-z scene, `PIT_Z_GAP` — already measured live).
   - `planeDrop` — rooftop fails drop the player to a lower plane with damage.
3. **Lap bonus discipline.** Wilderness rocks award 498.9 XP **only if every obstacle was
   completed**; a failed obstacle must be re-attempted before advancing (rooftops behave the
   same for their final-obstacle bonus). So: **a fail never advances the step**, and skipping
   an obstacle is a last resort that sacrifices the lap bonus.
4. **Course zone geometry.** Wilderness is a flat gate-based band; rooftops span planes 0–3,
   so on-course membership is a set of boxes each with its own plane.
5. **Entry mechanics.** Rooftops: none — the first obstacle IS the entry. Wilderness: the
   ridge Door crossing with its own success/fail chat lines and the same-scene wolf pit
   (distinct from obstacle pits). Ape Atoll / Werewolf / Penguin have gear- or NPC-gated
   entries — out of scope v1.
6. **Marks of grace.** Rooftops only (explicitly NOT wilderness). Random ground-item spawns
   anywhere in the course; picked up mid-lap. `GroundItems.query()` exists in the framework.
7. **Danger profile.** Wilderness: PKers + level 18/25 skeletons → food, auto-retaliate off,
   death recovery, Edgeville bank restock. Rooftops: safe; obstacles restore 1–2% run energy,
   so no food or run management needed at all.
8. **Wilderness dispenser/ticket economy** (150k fee, looting bag, lap-streak loot, tickets →
   XP): real and profitable (~2M/hr per wiki) but a whole separate subsystem. Today's
   WildyAgility doesn't touch it. **Out of scope v1; the CourseDef leaves room for it.**

Sources that did NOT pan out: the spawns DB has no agility-obstacle placements (`Rough wall`,
`Obstacle pipe` → 0 results), so obstacle discovery stays live `Locs.query` at runtime, and
recovery start tiles stay hand-measured (or lifted from the rev-274 content maps, the route
`BrimhavenAgilityLogic` already used for its pillar graph).

## Scope

**v1 courses:** Wilderness (parity target), Gnome Stronghold, Draynor rooftop, Canifis rooftop.
The registry is designed so a further standard course (other rooftops, Barbarian Outpost,
Prifddinas, Colossal Wyrm) is a data-only addition.

**Out of scope:** Brimhaven Agility Arena (graph minigame — `BrimhavenAgility` already owns
it), Agility Pyramid, Dorgesh-Kaan, Werewolf, Penguin, Ape Atoll, Hallowed Sepulchre (all
non-standard mechanics: item fetch, timed NPCs, gear gates); the wilderness dispenser/ticket
loop (future work, see above).

## Approaches considered

- **A. Extend AgilityBot's settings** (more knobs on the name-list runner). Rejected: no fail
  model, no pit/plane recovery, no entry handling — cannot host wilderness, which is the one
  course that must not regress.
- **B. CourseDef data + one generic engine (chosen).** WildyAgility's proven loop becomes the
  engine; everything course-specific becomes data plus one narrow entry hook. Adding a course
  costs data, not code; wilderness parity is checkable field-by-field against today's
  constants.
- **C. Subclass per course** (shared helpers, one TaskBot each). Rejected: that is the status
  quo (WildyAgility / BrimhavenAgility / EdgevilleMonkeyBars already drifted apart), and each
  new course re-pays the debugging the wilderness loop already banked. Its one virtue —
  handling genuinely weird mechanics — survives as the CourseDef `entry` hook.

## Architecture

Three files under `rs2_scripts/AgilityRunner/`:

- **`AgilityCourses.ts`** — data only: the `CourseDef` registry, one entry per course.
- **`AgilityRunnerLogic.ts`** — pure functions, no client access: outcome classification,
  zone predicates, step selection/resync, fail-mode dispatch. Ports of
  `classifyObstacle`/`classifyRidge`/`onCourse`/`inPit`/`reactionMs`, parameterized by
  CourseDef instead of module constants.
- **`AgilityRunner.ts`** — the TaskBot. Task set mirrors WildyAgility's, each task gated by
  the CourseDef fields that make it meaningful:
  - `ContinueDialog` — always.
  - `DeathRecovery` + `EatFood` — only when `danger` is present.
  - `PitEscape` — only when `pit` is present (z-gap detection, climb-up preference).
  - `PickupMark` — only when `marks: true`.
  - `TravelToCourse` / `EnterCourse` — walk to the approach tile; `EnterCourse` runs the
    `entry` hook when one exists (wilderness ridge), otherwise the first step is the entry.
  - `RunLap` — the generalized obstacle loop.

### CourseDef schema

```ts
interface ObstacleStep {
    name: string;              // loc name, lowercase (matching is case-insensitive)
    op?: string;               // menu op; default: the loc's first action (op1)
    locIds?: number[];         // optional exact pin when nearest-by-name is ambiguous
    startTile?: WorldTile;     // stand tile for approach / wrong-side / fail recovery;
                               // absent (custom courses) = no recovery walk, retry in place
    plane?: number;            // plane the player stands on to CLICK this step;
                               // absent = planeDrop detection disabled for the step
    xp?: number;               // wiki per-obstacle XP; diagnostic + optional resync hint
    failMode: 'none' | 'knockback' | 'pit' | 'planeDrop';
}

interface CourseDef {
    id: string;                // settings key, e.g. 'wilderness'
    label: string;
    minAgility: number;        // boostable; checked at start like WildyAgility does today
    zone: Array<{ x1: number; z1: number; x2: number; z2: number; plane: number }>;
    approach: WorldTile;       // where TravelToCourse walks to
    steps: ObstacleStep[];
    entry?: EntryHook;         // course-specific entry procedure (see below)
    pit?: { centre: WorldTile; zGap: number };   // obstacle-pit scene detection
    marks?: boolean;           // pick up Mark of grace mid-lap
    danger?: {                 // wilderness profile; absent = safe course, no food tasks
        bank: WorldTile;
        recoveryAnchor: WorldTile;
        retaliateOff: boolean;
    };
    searchRadius?: number;     // default 20
}
```

`EntryHook` is a named, registered handler plus its data — not arbitrary code in the data
file: `{ kind: 'wilderness_ridge', door: Tile, approach: Tile, successRe, failRe, minLevel }`.
v1 registers exactly one kind. This is the escape hatch that keeps approach C's flexibility
without its drift: a future weird course adds a hook implementation, not a fork of the engine.

### RunLap: the generalized loop

Unchanged from WildyAgility in shape — the parts below are the parameterization, not a
redesign:

1. **Approach**: if not near `step.startTile` and the obstacle isn't already interactable,
   walk there (bounded `walkTo`, same stuck/skip ladder: retry in place → walk back to start
   → after `LAP_RETRY_LIMIT`, advance with a log; advancing past a failed obstacle knowingly
   sacrifices the lap bonus and is last-resort only).
2. **Find**: nearest loc matching `step.name` (and `locIds` when present) within
   `searchRadius`, with actions.
3. **Click + settle**: `GameMessages.mark()` + agility-XP snapshot before the click; settle on
   XP gain / pit entry (z-gap) / plane change (for `planeDrop` steps: current plane below
   `step.plane`) / CANT_REACH / wrong-side / fail-message / interrupt / low-HP yield, bounded
   by idle-tick count AND a wall clock, exactly as today.
4. **Classify + dispatch** (pure, in AgilityRunnerLogic):
   - `xp` → advance; count; humanized `reactionMs` pause.
   - `pit` → yield to PitEscape; on return, walk back to the SAME step's startTile (no
     advance).
   - `planeDrop` → walk back to the same step's startTile via ground route (no advance).
     Detection: player plane < step plane while `settled` without XP.
   - `knockback` / `pit_fall_msg` → retry same step.
   - `wrong_side` / `cant_reach` / `timeout` → existing stuck ladder.
   - `low_hp` → yield to EatFood without burning a retry.
5. **Resync** when the current step's loc isn't visible: earliest course-order obstacle that
   IS visible (never nearest — same rationale as today: death recovery near the entrance must
   not jump mid-lap).

### PickupMark (rooftops only)

Between obstacles — after an `xp` advance, before the next approach — query
`GroundItems.query()` for `Mark of grace` **inside the course zone on the player's current
plane**. If one is reachable, take it (bounded: one attempt per sighting, never mid-settle,
never when it would require leaving the current plane); otherwise carry on. A mark on another
plane is left for the lap to reach naturally. Marks are why Canifis is in v1: it is the
canonical mark farm and exercises this path hard.

### Wilderness parity plan

- The wilderness CourseDef is extracted **mechanically** from today's `WildyAgilityLogic`
  constants (course order, `OBSTACLE_START` tiles, gate/ridge/approach tiles, `PIT_Z_GAP`,
  chat regexes, `RIDGE_MIN_AGILITY` 52, Edgeville bank) — no re-derivation, no wiki
  overrides. Wiki data fills only the new per-step fields (`xp`, `failMode`).
- `WildyAgility` stays untouched and registered until `AgilityRunner` (wilderness) passes a
  live parity session: full laps completing, an obstacle-pit escape, a ridge fail recovery,
  and a death recovery observed working. Only then does `WildyAgility` become a thin alias
  (its settings forwarding to AgilityRunner with `course=wilderness`) or get removed — user's
  call at that point.
- The wolf-pit / obstacle-pit distinction (same-scene vs z-gap) lives in the
  `wilderness_ridge` entry hook and the `pit` def respectively, exactly as it is split today.

### Settings

- `course` — dropdown over the registry (plus `custom`).
- `custom` course mode preserves AgilityBot's one capability the registry lacks: a
  comma-separated obstacle-name list + search radius, compiled into an ad-hoc CourseDef with
  every step `failMode: 'none'`, no zone (Chebyshev radius around start), no recovery tiles.
  This is the migration path for any unlisted track and replaces AgilityBot outright.
- Existing food/timeout settings (`food`, `foodWithdraw`, `minFood`, `acquireFoodAtStart`,
  `obstacleTimeoutTicks`) — shown for all courses, only consequential on danger courses.

## Error handling & ceilings

Per the standing doctrine (every loop needs a ceiling, a cooldown only sets the rate of an
unbounded loop):

- Entry attempts (ridge crossings) — bounded per session segment; repeated timeout →
  ScriptRunner.stop with reason.
- Per-step stuck ladder — `LAP_RETRY_LIMIT` as today, and a lap-level ceiling: N consecutive
  step-skips (i.e. a full lap advancing only by skips) → stop, something is structurally
  wrong.
- No obstacle visible AND no resync candidate for M ticks → stop (covers wrong-region
  starts and dead worlds) rather than waiting silently.
- Out-of-food on a danger course: unchanged policy — continue until death, death recovery
  restocks (logged once, as today).

## Testing

- All of `AgilityRunnerLogic` is pure: classification, zone predicates, planeDrop detection,
  resync ordering, custom-course compilation get direct unit tests.
- Wilderness fixtures are pinned from today's `WildyAgilityLogic` values (tiles, regexes,
  z-gap), so any drift in the extracted CourseDef fails a test rather than a live run —
  the same pinning discipline as `test_worldmap.py` / `test_travel.py`.
- Rooftop fixtures: Draynor step list with per-step XP from the wiki page (rev 15239943).
- Note: the rs2_scripts framework's test harness lives outside this repo; tests land wherever
  that project keeps `*Logic` tests today (open question below).

## Open questions for review

1. **v1 course list** — Wilderness + Gnome + Draynor + Canifis is a guess at what you run.
   Swap freely; each additional standard course is data-only either way.
2. **Wilderness dispenser/tickets** — confirmed out of scope for v1? (150k fee, looting bag,
   lap-streak loot, ticket redemption — a real money-maker but a separate subsystem.)
3. **AgilityBot's fate** — spec proposes replacing it with AgilityRunner's `custom` mode and
   removing it after parity. Keep it instead?
4. **Where do the framework's unit tests live** (the repo the `../../api` imports resolve
   in)? The Logic tests should follow that convention.
