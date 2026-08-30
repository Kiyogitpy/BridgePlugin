# Generic Agility Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One data-driven agility script (`AgilityRunner`) that runs any standard lap course from a per-course `CourseDef`, with the Wilderness course behavior-identical to today's `WildyAgility`.

**Architecture:** Three new files under `data/rs2_scripts/AgilityRunner/`: a dependency-free pure-logic module (types, classification, zone predicates), a course-data registry (the wilderness entry *imports* `WildyAgilityLogic`'s constants rather than copying them — parity by construction), and a TaskBot engine generalized from `WildyAgility.ts`. Registered in `index.ts` alongside the existing scripts; `AgilityBot` and `WildyAgility` stay untouched until live parity is confirmed.

**Tech Stack:** TypeScript (the rs2_scripts TaskBot framework: `Locs`, `Traversal`, `GameMessages`, `GroundItems`, `Reach`, `DeathRecovery`, `ScriptRegistry`).

**Spec:** `docs/superpowers/specs/2026-08-30-generic-agility-runner-design.md`

## Global Constraints

- **Wilderness must not regress** (user directive 2026-08-30). The wilderness CourseDef references `WildyAgilityLogic` exports directly (`COURSE_OBSTACLES`, `OBSTACLE_START` via `getStartTile`, `RIDGE_*`, `GATE_TILE`, `PIT_Z_GAP`, `WRONG_SIDE`, `PIT_FALL`, `EDGEVILLE_BANK`) — never literal copies. `WildyAgility.ts`/`WildyAgilityLogic.ts` are NOT modified by this plan.
- **No Node.js exists on this machine** (verified: `node`/`npx` absent from both Git Bash and PowerShell). TypeScript can be written and reviewed here but not compiled or executed. Every "verify" step below is either a mechanical text check that runs here, or an explicitly-labeled **[framework repo]** / **[live]** step for the user to run where `../../api` resolves. Do not invent local `tsc`/test invocations — they will fail.
- **Step names are lowercase** in all CourseDef data; matching is `loc.name?.toLowerCase() === step.name`.
- **Every loop gets a ceiling** (house doctrine): entry timeouts, no-obstacle-visible, full-lap-of-skips all `ScriptRunner.stop` with a reason.
- Follow the vendored scripts' house style: `// Why:` comments for non-obvious decisions, `*Logic.ts` split for pure functions, settings schema exported beside the default-export bot class.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Spec deviation (approved rationale):** `ObstacleStep.locIds` from the spec is dropped for v1 — no v1 course needs it, and `Loc`'s id-exposure surface is unverified. Reintroduce it the first time a course's repeated names actually misfire.

## File Structure

- Create: `data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.ts` — pure types + functions, **zero imports** (so it is runnable standalone by `tsx` in the framework repo).
- Create: `data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.selftest.ts` — plain `node:assert` self-test script (no test-runner dependency; the framework repo's runner is unknown).
- Create: `data/rs2_scripts/AgilityRunner/AgilityCourses.ts` — the CourseDef registry (wilderness + gnome + draynor + canifis).
- Create: `data/rs2_scripts/AgilityRunner/AgilityRunner.ts` — the TaskBot engine.
- Modify: `data/rs2_scripts/index.ts` — one new `ScriptRegistry.register` block + imports.

---

### Task 1: AgilityRunnerLogic.ts — pure logic module + self-test

**Files:**
- Create: `data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.ts`
- Create: `data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.selftest.ts`

**Interfaces:**
- Consumes: nothing (zero imports, by design).
- Produces (used verbatim by Tasks 2–3):
  - Types: `TileXZ {x,z,level}`, `ZoneBox {x1,z1,x2,z2,level}`, `FailMode`, `ObstacleStep`, `WildernessRidgeEntry`, `CourseDef`, `ObstacleSignals`, `ObstacleOutcome`, `RidgeSignals`, `RidgeOutcome`.
  - Functions: `inZone(here, zone)`, `nearTileL(here, dest, radius)`, `southOfDoor(here, door)`, `inPitScene(here, pit)`, `entryCorridor(here, def)`, `awayFromCourse(here, def)`, `classifyObstacle(s)`, `classifyRidge(s)`, `compileCustomCourse(namesCsv, searchRadius)`, `reactionMs(rng?)`.

- [ ] **Step 1: Write `AgilityRunnerLogic.ts`**

```ts
/**
 * Pure logic for the generic agility runner. ZERO imports on purpose: this file
 * must stay runnable standalone (see AgilityRunnerLogic.selftest.ts) and safe to
 * reason about without the client adapter in scope.
 *
 * Wilderness parity: the wilderness CourseDef in AgilityCourses.ts imports its
 * geometry/regexes from WildyAgilityLogic.ts — the types here only DESCRIBE
 * courses, they never restate wilderness constants.
 */

export interface TileXZ {
    x: number;
    z: number;
    level: number;
}

export interface ZoneBox {
    x1: number;
    z1: number;
    x2: number;
    z2: number;
    level: number;
}

/**
 * How an obstacle fails (wiki-classified, per the 2026-08-30 spec):
 * - none: cannot fail.
 * - knockback: same-scene damage, player stays put (wilderness stepping stone).
 * - pit: teleports to a pit/dungeon scene, detected via CourseDef.pit z-gap.
 * - planeDrop: rooftop fall to a lower plane, detected vs the click-time plane.
 */
export type FailMode = 'none' | 'knockback' | 'pit' | 'planeDrop';

export interface ObstacleStep {
    /** Loc name, lowercase. Matching is `loc.name?.toLowerCase() === name`. */
    name: string;
    /** Menu op; when absent the loc's first action is used (house pattern). */
    op?: string;
    /** Stand tile for approach / wrong-side / fail recovery. Absent = retry in place. */
    startTile?: TileXZ;
    /** Wiki per-obstacle XP. Diagnostic only — never used for control flow. */
    xp?: number;
    failMode: FailMode;
}

/**
 * Wilderness ridge entry: the Door crossing with its own success/fail chat and
 * the same-scene wolf pit. The only entry hook kind in v1; a future weird
 * course adds a kind here plus a handler in AgilityRunner.ts — never a fork of
 * the engine loop.
 */
export interface WildernessRidgeEntry {
    kind: 'wilderness_ridge';
    locName: string;
    op: string;
    door: TileXZ;
    gate: TileXZ;
    approach: TileXZ;
    successRe: RegExp;
    failRe: RegExp;
}

export interface CourseDef {
    id: string;
    label: string;
    /** Boostable; checked once at onStart like WildyAgility does today. */
    minAgility: number;
    /** Empty = "wherever you stand is on-course" (custom courses). */
    zone: readonly ZoneBox[];
    /** Where TravelToCourse walks. Absent = travel task disabled (custom courses). */
    approach?: TileXZ;
    steps: readonly ObstacleStep[];
    entry?: WildernessRidgeEntry;
    /** Obstacle-pit scene detection (wilderness high world-z pits). */
    pit?: { centre: TileXZ; zGap: number };
    /** Pick up Mark of grace mid-lap (rooftop courses only). */
    marks?: boolean;
    /** Course-specific immediate-fail chat line (wilderness PIT_FALL). */
    failRe?: RegExp;
    /** Course-specific wrong-approach-side chat line (wilderness WRONG_SIDE). */
    wrongSideRe?: RegExp;
    danger?: {
        bank: TileXZ;
        recoveryAnchor: TileXZ;
        retaliateOff: boolean;
    };
    searchRadius?: number;
}

/** Chebyshev distance on the same plane (port of WildyAgilityLogic.nearTile). */
export function nearTileL(here: TileXZ, dest: TileXZ, radius: number): boolean {
    return here.level === dest.level && Math.max(Math.abs(here.x - dest.x), Math.abs(here.z - dest.z)) <= radius;
}

/** Course membership. Empty zone means always-on-course (custom courses). */
export function inZone(here: TileXZ, zone: readonly ZoneBox[]): boolean {
    if (zone.length === 0) {
        return true;
    }
    return zone.some(
        b => here.level === b.level && here.x >= b.x1 && here.x <= b.x2 && here.z >= b.z1 && here.z <= b.z2
    );
}

/** True when standing on the outside (south) side of an entry door. */
export function southOfDoor(here: TileXZ, door: TileXZ): boolean {
    return here.level === door.level && here.z <= door.z;
}

/**
 * Obstacle pit (wilderness ropeswing / log): the server teleports the player to
 * a high world-z scene. Port of WildyAgilityLogic.inPit.
 */
export function inPitScene(here: TileXZ, pit: { centre: TileXZ; zGap: number }): boolean {
    return here.level === pit.centre.level && here.z - pit.centre.z > pit.zGap;
}

/**
 * Entry corridor: the zone TravelToCourse must not yank the player out of.
 * Wilderness: port of WildyAgilityLogic.nearCourseEntry (gate corridor, south of
 * the ridge Door, lateral band of 10). Everything else: near the approach tile.
 */
export function entryCorridor(here: TileXZ, def: CourseDef): boolean {
    const e = def.entry;
    if (e?.kind === 'wilderness_ridge') {
        if (here.level !== e.gate.level) {
            return false;
        }
        if (here.z > e.gate.z) {
            return false;
        }
        if (here.z < e.approach.z - 2) {
            return false;
        }
        return Math.abs(here.x - e.gate.x) <= 10;
    }
    return def.approach ? nearTileL(here, def.approach, 6) : false;
}

/** Far from both the lap zone and the entry corridor (bank, death spawn, ...). */
export function awayFromCourse(here: TileXZ, def: CourseDef): boolean {
    return !inZone(here, def.zone) && !entryCorridor(here, def);
}

export type ObstacleOutcome =
    | 'xp'
    | 'pit'
    | 'plane_drop'
    | 'interrupted'
    | 'wrong_side'
    | 'fail_msg'
    | 'cant_reach'
    | 'low_hp'
    | 'timeout';

export interface ObstacleSignals {
    xpGained: boolean;
    inPit: boolean;
    /** Player plane dropped below the click-time plane (planeDrop steps only). */
    fellPlane: boolean;
    cantReach: boolean;
    wrongSide: boolean;
    /** Course failRe seen (wilderness PIT_FALL: fail message without scene change). */
    failMessage: boolean;
    interrupted: boolean;
    lowHp: boolean;
}

/**
 * Classify a finished obstacle wait. Priority is WildyAgilityLogic's
 * classifyObstacle order with plane_drop slotted directly after pit — XP always
 * wins, live fall outcomes beat chat, chat beats timeout.
 */
export function classifyObstacle(s: ObstacleSignals): ObstacleOutcome {
    if (s.xpGained) {
        return 'xp';
    }
    if (s.inPit) {
        return 'pit';
    }
    if (s.fellPlane) {
        return 'plane_drop';
    }
    if (s.interrupted) {
        return 'interrupted';
    }
    if (s.wrongSide) {
        return 'wrong_side';
    }
    if (s.failMessage) {
        return 'fail_msg';
    }
    if (s.cantReach) {
        return 'cant_reach';
    }
    if (s.lowHp) {
        return 'low_hp';
    }
    return 'timeout';
}

export type RidgeOutcome = 'success' | 'fail' | 'interrupted' | 'timeout';

export interface RidgeSignals {
    xpGained: boolean;
    successMessage: boolean;
    failMessage: boolean;
    interrupted: boolean;
}

/**
 * Port of WildyAgilityLogic.classifyRidge. Fail before success: a residual
 * success line must not mask a fresh fail; XP only arrives on a true balance.
 */
export function classifyRidge(s: RidgeSignals): RidgeOutcome {
    if (s.interrupted) {
        return 'interrupted';
    }
    if (s.failMessage) {
        return 'fail';
    }
    if (s.xpGained || s.successMessage) {
        return 'success';
    }
    return 'timeout';
}

/**
 * Compile the AgilityBot-style comma-separated obstacle list into an ad-hoc
 * CourseDef: no zone (always on-course), no approach (no travel), every step
 * unfailable and retried in place. This replaces AgilityBot's capability.
 */
export function compileCustomCourse(namesCsv: string, searchRadius: number): CourseDef | null {
    const names = namesCsv
        .split(',')
        .map(s => s.trim().toLowerCase())
        .filter(Boolean);
    if (names.length === 0) {
        return null;
    }
    return {
        id: 'custom',
        label: 'Custom (obstacle list)',
        minAgility: 1,
        zone: [],
        steps: names.map(name => ({ name, failMode: 'none' as const })),
        searchRadius
    };
}

/**
 * Human-like pause after clearing an obstacle (port of WildyAgilityLogic
 * .reactionMs). One game tick is 600ms; the character needs to settle before
 * the next click or the first attempt is wasted.
 */
export function reactionMs(rng: () => number = Math.random): number {
    return rng() < 0.1 ? 1200 + rng() * 1800 : 600 + rng() * 900;
}
```

- [ ] **Step 2: Write `AgilityRunnerLogic.selftest.ts`**

Runnable in the framework repo with `npx tsx AgilityRunner/AgilityRunnerLogic.selftest.ts` — plain `node:assert`, no runner dependency. The wilderness geometry pins use the REAL measured values (gate 2998,3931 / approach 2998,3916 / door 2998,3917) so a corridor regression fails loudly.

```ts
import assert from 'node:assert/strict';
import {
    awayFromCourse,
    classifyObstacle,
    classifyRidge,
    compileCustomCourse,
    entryCorridor,
    inPitScene,
    inZone,
    nearTileL,
    reactionMs,
    southOfDoor,
    type CourseDef,
    type ObstacleSignals
} from './AgilityRunnerLogic.js';

const noSignals: ObstacleSignals = {
    xpGained: false,
    inPit: false,
    fellPlane: false,
    cantReach: false,
    wrongSide: false,
    failMessage: false,
    interrupted: false,
    lowHp: false
};

// classifyObstacle priority: xp beats everything, pit beats plane_drop,
// plane_drop beats interrupted, nothing at all is a timeout.
assert.equal(classifyObstacle({ ...noSignals, xpGained: true, inPit: true, interrupted: true }), 'xp');
assert.equal(classifyObstacle({ ...noSignals, inPit: true, fellPlane: true }), 'pit');
assert.equal(classifyObstacle({ ...noSignals, fellPlane: true, interrupted: true }), 'plane_drop');
assert.equal(classifyObstacle({ ...noSignals, wrongSide: true, failMessage: true }), 'wrong_side');
assert.equal(classifyObstacle({ ...noSignals, failMessage: true, cantReach: true }), 'fail_msg');
assert.equal(classifyObstacle({ ...noSignals, cantReach: true, lowHp: true }), 'cant_reach');
assert.equal(classifyObstacle({ ...noSignals, lowHp: true }), 'low_hp');
assert.equal(classifyObstacle(noSignals), 'timeout');

// classifyRidge: fail masks a stale success line; xp alone is success.
assert.equal(classifyRidge({ xpGained: true, successMessage: true, failMessage: true, interrupted: false }), 'fail');
assert.equal(classifyRidge({ xpGained: true, successMessage: false, failMessage: false, interrupted: false }), 'success');
assert.equal(classifyRidge({ xpGained: false, successMessage: false, failMessage: false, interrupted: true }), 'interrupted');
assert.equal(classifyRidge({ xpGained: false, successMessage: false, failMessage: false, interrupted: false }), 'timeout');

// inZone: empty zone is always on-course; boxes respect bounds and level.
assert.equal(inZone({ x: 1, z: 1, level: 0 }, []), true);
const box = [{ x1: 2974, z1: 3932, x2: 3022, z2: 3979, level: 0 }];
assert.equal(inZone({ x: 2998, z: 3945, level: 0 }, box), true);
assert.equal(inZone({ x: 2998, z: 3931, level: 0 }, box), false); // on the gate, not north of it
assert.equal(inZone({ x: 2998, z: 3945, level: 1 }, box), false); // wrong plane

// nearTileL / southOfDoor / inPitScene basics.
assert.equal(nearTileL({ x: 5, z: 5, level: 0 }, { x: 7, z: 3, level: 0 }, 2), true);
assert.equal(nearTileL({ x: 5, z: 5, level: 1 }, { x: 5, z: 5, level: 0 }, 2), false);
assert.equal(southOfDoor({ x: 2998, z: 3916, level: 0 }, { x: 2998, z: 3917, level: 0 }), true);
assert.equal(southOfDoor({ x: 2998, z: 3918, level: 0 }, { x: 2998, z: 3917, level: 0 }), false);
const pit = { centre: { x: 2998, z: 3945, level: 0 }, zGap: 2000 };
assert.equal(inPitScene({ x: 2998, z: 5950, level: 0 }, pit), true);
assert.equal(inPitScene({ x: 2998, z: 3950, level: 0 }, pit), false);

// entryCorridor with the REAL wilderness geometry (gate 2998,3931 / door
// 2998,3917 / approach 2998,3916) — a corridor regression must fail here.
const wildyLike: CourseDef = {
    id: 'w',
    label: 'w',
    minAgility: 52,
    zone: box,
    approach: { x: 2998, z: 3916, level: 0 },
    steps: [{ name: 'rocks', failMode: 'none' }],
    entry: {
        kind: 'wilderness_ridge',
        locName: 'Door',
        op: 'Open',
        door: { x: 2998, z: 3917, level: 0 },
        gate: { x: 2998, z: 3931, level: 0 },
        approach: { x: 2998, z: 3916, level: 0 },
        successRe: /balance across the ridge/i,
        failRe: /fall into the wolf pit/i
    }
};
assert.equal(entryCorridor({ x: 2998, z: 3916, level: 0 }, wildyLike), true); // the approach stand
assert.equal(entryCorridor({ x: 2998, z: 3925, level: 0 }, wildyLike), true); // gate corridor
assert.equal(entryCorridor({ x: 2998, z: 3932, level: 0 }, wildyLike), false); // north of the gate
assert.equal(entryCorridor({ x: 2998, z: 3910, level: 0 }, wildyLike), false); // too far south
assert.equal(entryCorridor({ x: 3012, z: 3925, level: 0 }, wildyLike), false); // off the lateral band
assert.equal(awayFromCourse({ x: 3094, z: 3493, level: 0 }, wildyLike), true); // Edgeville bank
assert.equal(awayFromCourse({ x: 2998, z: 3945, level: 0 }, wildyLike), false); // on course

// Non-entry courses: corridor is just "near the approach"; no approach = never.
const plain: CourseDef = {
    id: 'p',
    label: 'p',
    minAgility: 1,
    zone: [{ x1: 0, z1: 0, x2: 10, z2: 10, level: 0 }],
    approach: { x: 5, z: 20, level: 0 },
    steps: [{ name: 'x', failMode: 'none' }]
};
assert.equal(entryCorridor({ x: 5, z: 22, level: 0 }, plain), true);
assert.equal(entryCorridor({ x: 5, z: 40, level: 0 }, plain), false);

// compileCustomCourse: parse + trim + lowercase; empty input is null.
const custom = compileCustomCourse(' Log balance , Obstacle NET ,, ', 24);
assert.ok(custom);
assert.deepEqual(
    custom.steps.map(s => s.name),
    ['log balance', 'obstacle net']
);
assert.equal(custom.zone.length, 0);
assert.equal(custom.approach, undefined);
assert.equal(custom.searchRadius, 24);
assert.equal(compileCustomCourse('  ,  ', 20), null);

// reactionMs stays within the human bounds for both branches.
assert.equal(reactionMs(() => 0.0), 1200); // 10% long-pause branch, floor
assert.equal(reactionMs(() => 0.5), 600 + 0.5 * 900); // common branch

console.log('AgilityRunnerLogic selftest: all assertions passed');
```

- [ ] **Step 3: Mechanical verification (runs here)**

Run: `grep -n "^import\|require(" data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.ts`
Expected: **no output** — the logic module imports nothing. (The selftest file DOES import — check the logic file only.)

- [ ] **Step 4: Commit**

```bash
git add data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.ts data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.selftest.ts
git commit -m "agility runner: pure logic module + standalone selftest

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: AgilityCourses.ts — the CourseDef registry

**Files:**
- Create: `data/rs2_scripts/AgilityRunner/AgilityCourses.ts`

**Interfaces:**
- Consumes: every type from Task 1; `WildyAgilityLogic` exports (`COURSE_OBSTACLES`, `COURSE_CENTRE`, `RIDGE_DOOR`, `RIDGE_APPROACH`, `GATE_TILE`, `COURSE_X_RADIUS`, `SEARCH_RADIUS`, `PIT_Z_GAP`, `RIDGE_MIN_AGILITY`, `EDGEVILLE_BANK`, `RIDGE_NAME`, `RIDGE_OP`, `RIDGE_SUCCESS`, `RIDGE_FAIL`, `WRONG_SIDE`, `PIT_FALL`, `getStartTile`).
- Produces (used by Task 3): `COURSES: readonly CourseDef[]`, `COURSE_LABELS: string[]` (registry labels + the custom label, in dropdown order), `CUSTOM_LABEL: string`, `courseByLabel(label: string): CourseDef | null`.

- [ ] **Step 1: Write `AgilityCourses.ts`**

Course data provenance — every number below is sourced, not guessed:
- Wilderness: imported from `WildyAgilityLogic` (live-measured constants); per-step XP + fail modes from wiki rev 15283980.
- Gnome: obstacle order from `AgilityBot`'s proven default list; XP from wiki rev 15290118 (log 10, net 10, branch 6.5, rope 10, branch 6.5, net 10, pipe 7.5, +50 completion bonus); footprint from `AgilityBot`'s `GNOME_COURSE_START` (2474,3436) r20.
- Draynor: obstacle order + XP from wiki rev 15239943 (wall 5, tightrope 8, tightrope 7, narrow wall 7, wall 10, gap 4, crate 79); all unfailable since the 8 May 2024 update; map anchor 3103,3279.
- Canifis: obstacle order + XP from wiki rev 15239946 (tall tree 10, gap 8, gap 8, gap 10 FAILABLE, gap 8, pole-vault 10, gap 11, gap 175); the one failable obstacle is Gap #3 (tanner→general store), fail = plane drop; map anchor 3507,3488.

```ts
/**
 * CourseDef registry for AgilityRunner. Data only — no client access.
 *
 * WILDERNESS PARITY IS BY CONSTRUCTION: every wilderness tile, radius and chat
 * regex is IMPORTED from WildyAgilityLogic (the live-measured source of truth),
 * never restated. When WildyAgility is eventually retired, its Logic file's
 * constants move here — until then there is exactly one copy.
 *
 * Rooftop/gnome zone boxes are deliberately generous bounding boxes (course
 * footprint + margin) — they answer "should TravelToCourse walk?" and "is this
 * mark of grace ours to take?", not precise geometry.
 */
import type { CourseDef, FailMode, ZoneBox } from './AgilityRunnerLogic.js';
import {
    COURSE_CENTRE,
    COURSE_OBSTACLES,
    COURSE_X_RADIUS,
    EDGEVILLE_BANK,
    GATE_TILE,
    PIT_FALL,
    PIT_Z_GAP,
    RIDGE_APPROACH,
    RIDGE_DOOR,
    RIDGE_FAIL,
    RIDGE_MIN_AGILITY,
    RIDGE_NAME,
    RIDGE_OP,
    RIDGE_SUCCESS,
    SEARCH_RADIUS as WILDY_SEARCH_RADIUS,
    WRONG_SIDE,
    getStartTile
} from '../WildyAgility/WildyAgilityLogic.js';

/** One footprint repeated across the planes a course spans. */
function stack(x1: number, z1: number, x2: number, z2: number, levels: readonly number[]): ZoneBox[] {
    return levels.map(level => ({ x1, z1, x2, z2, level }));
}

// Per-step XP and fail modes: wiki rev 15283980 (pipe/rocks unfailable;
// ropeswing/log fall to the dungeon; stepping stone is same-scene knockback;
// rocks' 498.9 is the lap bonus, paid only when every obstacle was completed).
const WILDY_STEP_META: Record<string, { xp: number; failMode: FailMode }> = {
    'obstacle pipe': { xp: 12.5, failMode: 'none' },
    ropeswing: { xp: 20, failMode: 'pit' },
    'stepping stone': { xp: 20, failMode: 'knockback' },
    'log balance': { xp: 20, failMode: 'pit' },
    rocks: { xp: 498.9, failMode: 'none' }
};

const WILDERNESS: CourseDef = {
    id: 'wilderness',
    label: 'Wilderness (52)',
    minAgility: RIDGE_MIN_AGILITY,
    // Why: the old onCourse had no north bound (strictly-north-of-gate band);
    // z2 = gate + 48 clears the course fence with margin, so the only behavior
    // change is at tiles no lap ever reaches.
    zone: [
        {
            x1: GATE_TILE.x - COURSE_X_RADIUS,
            z1: GATE_TILE.z + 1,
            x2: GATE_TILE.x + COURSE_X_RADIUS,
            z2: GATE_TILE.z + 48,
            level: GATE_TILE.level
        }
    ],
    approach: RIDGE_APPROACH,
    steps: COURSE_OBSTACLES.map(name => ({
        name,
        startTile: getStartTile(name) ?? undefined,
        ...WILDY_STEP_META[name]!
    })),
    entry: {
        kind: 'wilderness_ridge',
        locName: RIDGE_NAME,
        op: RIDGE_OP,
        door: RIDGE_DOOR,
        gate: GATE_TILE,
        approach: RIDGE_APPROACH,
        successRe: RIDGE_SUCCESS,
        failRe: RIDGE_FAIL
    },
    pit: { centre: COURSE_CENTRE, zGap: PIT_Z_GAP },
    failRe: PIT_FALL,
    wrongSideRe: WRONG_SIDE,
    danger: { bank: EDGEVILLE_BANK, recoveryAnchor: RIDGE_APPROACH, retaliateOff: true },
    searchRadius: WILDY_SEARCH_RADIUS
};

// Order: AgilityBot's proven default list. XP: wiki rev 15290118; the pipe also
// pays the 50 XP completion bonus. Nothing on this course can be failed.
const GNOME: CourseDef = {
    id: 'gnome',
    label: 'Gnome Stronghold (1)',
    minAgility: 1,
    zone: stack(2454, 3416, 2494, 3456, [0, 1, 2]),
    approach: { x: 2474, z: 3436, level: 0 },
    steps: [
        { name: 'log balance', xp: 10, failMode: 'none' },
        { name: 'obstacle net', xp: 10, failMode: 'none' },
        { name: 'tree branch', xp: 6.5, failMode: 'none' },
        { name: 'balancing rope', xp: 10, failMode: 'none' },
        { name: 'tree branch', xp: 6.5, failMode: 'none' },
        { name: 'obstacle net', xp: 10, failMode: 'none' },
        { name: 'obstacle pipe', xp: 7.5, failMode: 'none' } // + 50 completion bonus
    ]
};

// Wiki rev 15239943. All steps unfailable since 8 May 2024 (wall-cross and
// tightrope fails removed; the rest never failed). Starts at the Rough wall
// north of the bank; crate ends the lap back on the ground.
const DRAYNOR: CourseDef = {
    id: 'draynor',
    label: 'Draynor rooftop (1)',
    minAgility: 1,
    zone: stack(3086, 3261, 3121, 3297, [0, 1, 2, 3]),
    approach: { x: 3103, z: 3279, level: 0 },
    steps: [
        { name: 'rough wall', xp: 5, failMode: 'none' },
        { name: 'tightrope', xp: 8, failMode: 'none' },
        { name: 'tightrope', xp: 7, failMode: 'none' },
        { name: 'narrow wall', xp: 7, failMode: 'none' },
        { name: 'wall', xp: 10, failMode: 'none' },
        { name: 'gap', xp: 4, failMode: 'none' },
        { name: 'crate', xp: 79, failMode: 'none' }
    ],
    marks: true
};

// Wiki rev 15239946. One failable obstacle: Gap #3 (tanner -> general store),
// 88.28% success at 40 scaling to unfailable at 64 — fail is a plane drop.
// Double mark-of-grace rate vs other rooftops, and the high-level rate nerf
// doesn't apply here, which is why this course is in v1.
const CANIFIS: CourseDef = {
    id: 'canifis',
    label: 'Canifis rooftop (40)',
    minAgility: 40,
    zone: stack(3486, 3468, 3528, 3510, [0, 1, 2, 3]),
    approach: { x: 3507, z: 3488, level: 0 },
    steps: [
        { name: 'tall tree', xp: 10, failMode: 'none' },
        { name: 'gap', xp: 8, failMode: 'none' },
        { name: 'gap', xp: 8, failMode: 'none' },
        { name: 'gap', xp: 10, failMode: 'planeDrop' },
        { name: 'gap', xp: 8, failMode: 'none' },
        { name: 'pole-vault', xp: 10, failMode: 'none' },
        { name: 'gap', xp: 11, failMode: 'none' },
        { name: 'gap', xp: 175, failMode: 'none' }
    ],
    marks: true
};

export const COURSES: readonly CourseDef[] = [WILDERNESS, GNOME, DRAYNOR, CANIFIS];

export const CUSTOM_LABEL = 'Custom (obstacle list)';

export const COURSE_LABELS: string[] = [...COURSES.map(c => c.label), CUSTOM_LABEL];

export function courseByLabel(label: string): CourseDef | null {
    return COURSES.find(c => c.label === label) ?? null;
}
```

- [ ] **Step 2: Mechanical verification (runs here)**

Run: `grep -nE "29(9[0-9]|[0-9]{2})[0-9]|39[0-4][0-9]" data/rs2_scripts/AgilityRunner/AgilityCourses.ts | grep -v "3416\|3456\|2454\|2494\|2474\|3436\|3086\|3261\|3121\|3297\|3103\|3279\|3486\|3468\|3528\|3510\|3507\|3488"`
Expected: **no output** — no wilderness-range coordinate literal (x≈2990-3020, z≈3910-3949) appears in the file; every wilderness tile arrives via import. (The allowlist is the gnome/draynor/canifis anchors defined in this task.)

- [ ] **Step 3: Commit**

```bash
git add data/rs2_scripts/AgilityRunner/AgilityCourses.ts
git commit -m "agility runner: course registry — wilderness by import, gnome/draynor/canifis from wiki

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: AgilityRunner.ts — the engine

One task on purpose: the engine is one reviewable unit and nothing can compile-check partial files here. The file is written in four appends (steps 1–4) and committed once.

**Files:**
- Create: `data/rs2_scripts/AgilityRunner/AgilityRunner.ts`

**Interfaces:**
- Consumes: Task 1 functions/types, Task 2 registry, and the framework API exactly as `WildyAgility.ts` uses it (plus `GroundItems` from `../../api/grounditems/GroundItems.js` and `Reach` from `../../api/walking/Reach.js`, both verified against `ChickenKiller.ts` usage).
- Produces (used by Task 4): `export const AGILITY_RUNNER_SETTINGS: SettingsSchema` and `export default class AgilityRunner extends TaskBot`.

- [ ] **Step 1: Write the file header — imports, settings, module state, helpers**

```ts
/**
 * AgilityRunner — generic lap-course engine, generalized from WildyAgility.
 * Course-specific behavior lives in AgilityCourses.ts data; the only code-level
 * hook is the wilderness ridge entry (attemptRidgeCrossing below).
 *
 * WILDERNESS PARITY: task set, signal waits, retry ladder and recovery walks
 * are ports of WildyAgility's, gated by CourseDef fields. WildyAgility itself
 * stays registered until a live parity session confirms this runner (see the
 * plan's final task), so nothing is lost while this is proven.
 */
import { FOOD_OPTIONS, foodHealAmount, isFoodItem, shouldEatToUseFood } from '../../api/combat/food.js';
import { actions, reader } from '../../adapter/ClientAdapter.js';
import { TaskBot, type Task } from '../../api/bot/Bot.js';
import { Execution } from '../../api/execution/Execution.js';
import { EventSignal } from '../../api/execution/EventSignal.js';
import { Game } from '../../api/game/Game.js';
import Tile from '../../geometry/Tile.js';
import { Traversal } from '../../api/walking/Traversal.js';
import { Reach } from '../../api/walking/Reach.js';
import { ContinueDialog } from '../../api/tasks/ContinueDialog.js';
import { DeathRecovery } from '../../api/tasks/DeathRecovery.js';
import { Bank } from '../../api/bank/Bank.js';
import { ChatDialog } from '../../api/ui/dialogue/ChatDialog.js';
import { Inventory } from '../../api/inventory/Inventory.js';
import { GroundItems } from '../../api/grounditems/GroundItems.js';
import { Paint } from '../../paint/Paint.js';
import { ScriptRunner } from '../../runtime/ScriptRunner.js';
import { Skills } from '../../api/skills/Skills.js';
import { Locs, type Loc } from '../../api/locs/Locs.js';
import { CANT_REACH, GameMessages } from '../../api/chatbox/gameMessages.js';
import type { SettingsSchema } from '../../runtime/Settings.js';
import { fmtDuration } from '../../paint/paintLogic.js';
import { scriptFood } from '../../api/loadout/loadoutPlan.js';
import { LOADOUT_SETTING } from '../../api/loadout/loadoutSetting.js';
import {
    awayFromCourse,
    classifyObstacle,
    classifyRidge,
    compileCustomCourse,
    entryCorridor,
    inPitScene,
    inZone,
    nearTileL,
    reactionMs,
    southOfDoor,
    type CourseDef,
    type ObstacleStep,
    type RidgeOutcome
} from './AgilityRunnerLogic.js';
import { COURSE_LABELS, CUSTOM_LABEL, courseByLabel } from './AgilityCourses.js';

// Why: the default wait is 24 ticks and obstacle clears can take ~20 (WildyAgility).
const LAP_RETRY_LIMIT = 2;
const RIDGE_TIMEOUT_MS = 10_000;
// Why: ceilings, not cooldowns — a cooldown only sets the rate of an unbounded loop.
const ENTRY_TIMEOUT_STOP = 8; // consecutive ridge TIMEOUTS (fails are normal and reset it)
const NO_OBSTACLE_STOP = 50; // consecutive "nothing visible" passes (~60s+) before stopping
const MARK_SKIP_MS = 30_000;

export const AGILITY_RUNNER_SETTINGS: SettingsSchema = {
    course: {
        type: 'string',
        default: 'Wilderness (52)',
        options: COURSE_LABELS,
        label: 'Course',
        help: 'which agility course to run; Custom uses the obstacle list below'
    },
    obstacles: {
        type: 'string',
        default: '',
        label: 'Custom obstacles (lap order)',
        help: "only used when Course is 'Custom (obstacle list)': comma-separated loc names in lap order, repeats allowed; each step clicks the nearest loc with that name and advances on agility xp"
    },
    customSearchRadius: {
        type: 'number',
        default: 20,
        min: 4,
        max: 64,
        label: 'Custom search radius (tiles)',
        help: "only used when Course is 'Custom (obstacle list)'"
    },
    loadout: LOADOUT_SETTING,
    food: {
        type: 'string',
        default: '',
        options: FOOD_OPTIONS,
        label: 'Food (overrides loadout)',
        help: "exact food to eat and withdraw; blank uses the loadout's food, or 'Lobster' if the loadout names none. Only consequential on dangerous courses (Wilderness)"
    },
    foodWithdraw: {
        type: 'number',
        default: 20,
        min: 1,
        max: 28,
        label: 'Food to withdraw',
        help: 'how many to withdraw at startup restock and after death (dangerous courses only)'
    },
    minFood: {
        type: 'number',
        default: 1,
        min: 0,
        max: 28,
        label: 'Bank below food count',
        help: 'at script start (and after death), bank if carrying fewer than this many; 0 = skip the startup food check (dangerous courses only)'
    },
    acquireFoodAtStart: {
        type: 'boolean',
        default: true,
        label: 'Acquire food at start',
        help: 'withdraw food before heading out when starting empty (dangerous courses only)'
    },
    obstacleTimeoutTicks: {
        type: 'number',
        default: 24,
        min: 5,
        max: 60,
        label: 'Obstacle timeout (ticks)',
        help: 'max idle ticks to wait for XP or a known-delaying event before treating as timeout'
    }
};

let FOOD = 'lobster';
let ACQUIRE_FOOD_AT_START = true;
let FOOD_WITHDRAW = 20;
let MIN_FOOD = 1;
let OBSTACLE_TIMEOUT_TICKS = 24;

/** Ensure Auto Retaliate is off so course monsters don't pull us into combat. */
async function ensureRetaliateOff(log: (m: string) => void): Promise<void> {
    const controls = reader.retaliateControls();
    if (!controls) {
        log('retaliateControls not available — cannot toggle auto retaliate');
        return;
    }
    const result = actions.ifButton(controls.offComId);
    log(`Auto Retaliate turned off (comId: ${controls.offComId}, result: ${result})`);
}

function foodCount(): number {
    return Inventory.items().filter(i => isFoodItem(i.name, FOOD)).length;
}

function needEat(): boolean {
    const n = foodCount();
    if (n <= 0) {
        return false;
    }
    return shouldEatToUseFood({
        hp: Skills.effective('hitpoints'),
        maxHp: Skills.level('hitpoints'),
        heal: foodHealAmount(FOOD),
        foodCount: n
    });
}
```

- [ ] **Step 2: Append the bot class core (state, onStart, paint, helpers, banking)**

```ts
export default class AgilityRunner extends TaskBot {
    override loopDelay = 600;

    def!: CourseDef;
    private step = 0;
    private laps = 0;
    private cleared = 0;
    private eats = 0;
    private deaths = 0;
    private marksTaken = 0;
    private status = 'starting';
    private startedAt = Date.now();
    private xpAtStart = 0;
    died = false;
    private entered = false;
    justEscapedPit = false;
    lastClearedTick = 0;
    obstacleTimes: number[] = [];
    /** Consecutive skip-advances; a full lap of skips is a structural stop. */
    consecutiveSkips = 0;
    entryTimeouts = 0;

    override async onStart(): Promise<void> {
        await Execution.delayUntil(() => Game.ingame() && Game.tile() !== null, 0);

        const label = this.settings.str('course', COURSE_LABELS[0]!);
        const def =
            label === CUSTOM_LABEL
                ? compileCustomCourse(this.settings.str('obstacles', ''), this.settings.num('customSearchRadius', 20))
                : courseByLabel(label);
        if (!def) {
            throw new Error(`AgilityRunner: unknown or empty course '${label}'`);
        }
        this.def = def;

        FOOD = (this.settings.str('food', '').trim() || scriptFood(this.settings, 'Lobster')).toLowerCase();
        FOOD_WITHDRAW = this.settings.num('foodWithdraw', 20);
        MIN_FOOD = this.settings.num('minFood', 1);
        ACQUIRE_FOOD_AT_START = this.settings.bool('acquireFoodAtStart', true);
        OBSTACLE_TIMEOUT_TICKS = this.settings.num('obstacleTimeoutTicks', 24);

        const agility = Skills.level('agility');
        if (agility < def.minAgility) {
            throw new Error(`AgilityRunner: '${def.label}' needs Agility ${def.minAgility} (have ${agility})`);
        }

        this.startedAt = Date.now();
        this.xpAtStart = Skills.xp('agility');
        this.lastClearedTick = Game.tick();

        const here = Game.tile()!;
        this.entered = inZone(here, def.zone);

        this.on('chat.message', e => {
            if (/oh dear.*you are dead/i.test(e.text)) {
                this.died = true;
            }
        });

        if (def.danger?.retaliateOff) {
            await ensureRetaliateOff(m => this.log(m));
        }

        // Why: startup below minFood banks before walking out — dangerous courses
        // only; mid-session restock stays death-only (no safe exit mid-lap).
        if (def.danger) {
            const startingFood = foodCount();
            const needStartupFood =
                (MIN_FOOD > 0 && startingFood < MIN_FOOD) || (ACQUIRE_FOOD_AT_START && startingFood === 0);
            if (needStartupFood) {
                if (this.entered || (def.pit && inPitScene(here, def.pit))) {
                    this.log(`only ${startingFood} '${FOOD}' (min ${MIN_FOOD}) but already on course, continuing until death`);
                } else {
                    const required = MIN_FOOD > 0 ? MIN_FOOD : 1;
                    this.log(`only ${startingFood} '${FOOD}' (min ${MIN_FOOD}), banking before heading to the course`);
                    let opened = false;
                    for (let attempt = 0; attempt < 6 && !opened; attempt++) {
                        opened = await this.bankForFood('startup');
                        if (opened) {
                            break;
                        }
                        this.log(`startup bank open failed (attempt ${attempt + 1}/6), retrying`);
                        await Execution.delayTicks(2);
                    }
                    if (!opened || foodCount() < required) {
                        this.setStatus(`out of '${FOOD}' in bank, stopped`);
                        ScriptRunner.stop(`only ${foodCount()} '${FOOD}' after bank (need ${required})`);
                        return;
                    }
                }
            }
        }

        this.log(
            `AgilityRunner starting — '${def.label}', lap [${def.steps.map(s => s.name).join(' -> ')}]` +
                (def.danger ? `, food '${FOOD}' x${foodCount()} (min ${MIN_FOOD}, withdraw ${FOOD_WITHDRAW})` : '') +
                `, timeout ${OBSTACLE_TIMEOUT_TICKS} ticks`
        );

        const tasks: Task[] = [new ContinueDialog()];
        if (def.danger) {
            const danger = def.danger;
            tasks.push(
                new DeathRecovery(this, {
                    anchor: danger.recoveryAnchor,
                    radius: 6,
                    onDeath: () => {
                        this.deaths++;
                        this.entered = false;
                        this.justEscapedPit = false;
                        this.lastClearedTick = Game.tick();
                        this.setStatus('died — recovering');
                        this.log('died — banking (food-only) and returning');
                        if (danger.retaliateOff) {
                            void ensureRetaliateOff(m => this.log(m));
                        }
                    },
                    onRecovered: () => {
                        this.died = false;
                        this.setStatus('recovered — re-entering the course');
                    },
                    walkBack: () => this.recoverAndReturn()
                })
            );
            tasks.push(new EatFood(this));
        }
        if (def.pit) {
            tasks.push(new PitEscape(this));
        }
        if (def.marks) {
            tasks.push(new PickupMark(this));
        }
        if (def.approach) {
            tasks.push(new TravelToCourse(this));
        }
        if (def.entry) {
            tasks.push(new EnterCourse(this));
        }
        tasks.push(new RunLap(this));
        this.add(...tasks);
    }

    override recoveryAnchor(): Tile {
        const a = this.def.danger?.recoveryAnchor ?? this.def.approach ?? Game.tile()!;
        return Tile.from(a);
    }

    override onPaint(ctx: CanvasRenderingContext2D): void {
        const p = Paint.begin(ctx, { dock: 'chatbox', accent: '#e0a15b' });
        p.title(`AgilityRunner — ${this.status}`);

        const mins = (Date.now() - this.startedAt) / 60_000;
        const tabs = this.def?.danger ? ['Overview', 'Survival'] : ['Overview'];
        const tab = p.tabs('ar', tabs);
        if (tab === 'Overview') {
            const xph =
                mins > 0.5
                    ? `${((((Skills.xp('agility') - this.xpAtStart) / mins) * 60) / 1000).toFixed(1)}k`
                    : '—';
            p.row(`Runtime: ${fmtDuration(mins)}`, `Laps: ${this.laps}`, `XP/hr: ${xph}`);
            p.row(`Obstacles: ${this.cleared}`, `Step: ${this.currentName() ?? '—'}`);
            if (this.def?.marks) {
                p.row(`Marks: ${this.marksTaken}`);
            }
            if (this.obstacleTimes.length > 0) {
                const avg = this.obstacleTimes.reduce((a, b) => a + b, 0) / this.obstacleTimes.length;
                p.row(`Avg obstacle: ${avg.toFixed(1)} ticks`);
            }
        } else {
            p.row(`Food: ${foodCount()}`, `Ate: ${this.eats}`, `Deaths: ${this.deaths}`);
            p.bar('HP', Skills.hpFraction());
        }

        p.gap();
        ScriptRunner.paintControls(p);
        p.end();
    }

    private async recoverAndReturn(): Promise<boolean> {
        if (!(await this.bankForFood('death'))) {
            return false;
        }
        const anchor = this.def.danger!.recoveryAnchor;
        this.setStatus('recovering: returning to the course approach');
        return Traversal.walkResilient(anchor, {
            radius: 2,
            attempts: 6,
            timeoutMs: 120_000,
            log: m => this.log(`  ${m}`)
        });
    }

    /** Walks to the course bank, deposits, withdraws food. Dangerous courses only. */
    private async bankForFood(reason: 'startup' | 'death'): Promise<boolean> {
        const bank = this.def.danger!.bank;
        this.setStatus(`${reason}: walking to the bank`);
        await Traversal.walkResilient(bank, {
            radius: 4,
            attempts: 4,
            timeoutMs: 120_000,
            log: m => this.log(`  ${m}`)
        });

        if (!(await Bank.openNearest('Bank booth', 'Use-quickly', m => this.log(`  ${m}`)))) {
            this.log('could not open the bank — will retry next loop');
            return false;
        }

        await Bank.depositInventory();
        await Execution.delayTicks(1);
        await this.withdrawFood();
        this.log(`${reason}: restocked ${foodCount()} '${FOOD}'`);
        return true;
    }

    private async withdrawFood(): Promise<void> {
        for (let i = 0; i < FOOD_WITHDRAW * 2 && foodCount() < FOOD_WITHDRAW; i++) {
            const banked = Bank.items().find(it => isFoodItem(it.name, FOOD));
            if (!banked?.name) {
                this.log(`no '${FOOD}' left in the bank`);
                return;
            }
            const before = foodCount();
            if (!(await Bank.withdraw(banked.name, 'Withdraw-1'))) {
                return;
            }
            if (!(await Execution.delayUntil(() => foodCount() > before, 2000))) {
                return;
            }
        }
    }

    courseDef(): CourseDef {
        return this.def;
    }
    setStatus(s: string): void {
        this.status = s;
    }
    isEntered(): boolean {
        return this.entered;
    }
    markEntered(): void {
        this.entered = true;
    }
    markLeft(): void {
        this.entered = false;
    }
    markEscapedPit(): void {
        this.justEscapedPit = true;
        // Escape/climb time is not "idle between obstacles".
        this.lastClearedTick = Game.tick();
    }
    clearEscapedPit(): void {
        this.justEscapedPit = false;
    }
    countEat(): void {
        this.eats++;
    }
    countCleared(): void {
        this.cleared++;
    }
    countMark(): void {
        this.marksTaken++;
    }
    searchRadius(): number {
        return this.def.searchRadius ?? 20;
    }
    currentStep(): ObstacleStep {
        return this.def.steps[this.step]!;
    }
    currentName(): string {
        return this.def.steps[this.step]?.name ?? '—';
    }
    courseNames(): string[] {
        return this.def.steps.map(s => s.name);
    }
    advance(): void {
        this.step++;
        if (this.step >= this.def.steps.length) {
            this.step = 0;
            this.laps++;
            this.log(`lap ${this.laps} complete`);
        }
    }
    resyncTo(name: string): boolean {
        const idx = this.def.steps.findIndex(s => s.name === name);
        if (idx === -1) {
            return false;
        }
        this.log(`lap re-sync: step ${this.step} (${this.currentName()}) -> ${idx} (${name})`);
        this.step = idx;
        return true;
    }
}
```

- [ ] **Step 3: Append the entry crossing + travel/enter/eat/pit tasks**

```ts
function findEntryLoc(bot: AgilityRunner): Loc | null {
    const e = bot.courseDef().entry!;
    return Locs.query()
        .name(e.locName)
        .action(e.op)
        .where(l => l.distance() <= bot.searchRadius())
        .nearest();
}

/** Walks to the stand tile south of the entry Door without pathfinding through it. */
async function walkToEntryApproach(bot: AgilityRunner, attempts = 4, timeoutMs = 60_000): Promise<void> {
    const e = bot.courseDef().entry!;
    const here = Game.tile();
    if (here && nearTileL(here, e.approach, 2) && southOfDoor(here, e.door)) {
        return;
    }
    bot.setStatus(`walking to entry approach (${e.approach.x},${e.approach.z})`);
    // Why: targeting a tile north of the Door makes the pathfinder Open it as a
    // multi-tile transport, stealing the ridge attempt from the script.
    await Traversal.walkResilient(e.approach, {
        radius: 1,
        attempts,
        timeoutMs,
        log: m => bot.log(`  ${m}`)
    });
}

/**
 * Crosses the wilderness ridge into the course. Outcome from GameMessages + XP;
 * the wolf pit is same-scene, so chat is the authoritative fail signal (position
 * alone is ambiguous after a fail or a cancelled click).
 */
async function attemptRidgeCrossing(bot: AgilityRunner): Promise<RidgeOutcome> {
    const e = bot.courseDef().entry!;
    await walkToEntryApproach(bot);

    const ridge = findEntryLoc(bot);
    if (!ridge) {
        bot.setStatus(`waiting: no '${e.locName}' (entry) within ${bot.searchRadius()} tiles`);
        await Execution.delayTicks(2);
        return 'timeout';
    }

    // Mark BEFORE the click so only messages produced by this attempt count.
    const mark = GameMessages.mark();
    const beforeXp = Skills.xp('agility');
    bot.setStatus(`crossing the entry (${e.op} ${ridge.name} @ ${ridge.tile().x},${ridge.tile().z})`);

    if (!(await ridge.interact(e.op))) {
        await Execution.delayTicks(2);
        return 'timeout';
    }

    await Execution.delayUntil(() => {
        if (EventSignal.pending()) {
            return true;
        }
        if (Skills.xp('agility') > beforeXp) {
            return true;
        }
        if (GameMessages.sawSince(mark, e.successRe)) {
            return true;
        }
        if (GameMessages.sawSince(mark, e.failRe)) {
            return true;
        }
        return false;
    }, RIDGE_TIMEOUT_MS);

    const outcome = classifyRidge({
        xpGained: Skills.xp('agility') > beforeXp,
        successMessage: GameMessages.sawSince(mark, e.successRe),
        failMessage: GameMessages.sawSince(mark, e.failRe),
        interrupted: EventSignal.pending()
    });

    if (outcome === 'success') {
        bot.markEntered();
        // Entry XP can arrive slightly after the success line; stamp so RunLap
        // does not attribute it to the first obstacle.
        bot.lastClearedTick = Game.tick();
        bot.entryTimeouts = 0;
        bot.log('entry crossed');
        return outcome;
    }

    if (outcome === 'interrupted') {
        bot.setStatus('random event — handling');
        return outcome;
    }

    if (outcome === 'fail') {
        bot.entryTimeouts = 0;
        bot.log('fell at the entry (wolf pit) — walking back to the approach');
    } else {
        bot.entryTimeouts++;
        bot.log(`entry crossing timed out (${bot.entryTimeouts}/${ENTRY_TIMEOUT_STOP})`);
        if (bot.entryTimeouts >= ENTRY_TIMEOUT_STOP) {
            ScriptRunner.stop(`${ENTRY_TIMEOUT_STOP} consecutive entry timeouts — entry unusable from here`);
            return outcome;
        }
    }
    await walkToEntryApproach(bot, 3, 30_000);
    return outcome;
}

class EatFood implements Task {
    constructor(private bot: AgilityRunner) {}

    validate(): boolean {
        return needEat();
    }

    async execute(): Promise<void> {
        for (let bite = 0; bite < 28; bite++) {
            if (this.bot.died || ChatDialog.canContinue() || EventSignal.pending()) {
                return;
            }
            if (!needEat()) {
                return;
            }
            const food = Inventory.items().find(i => isFoodItem(i.name, FOOD));
            if (!food) {
                return;
            }
            this.bot.setStatus(`eating ${food.name} (${Math.round(Skills.hpFraction() * 100)}% hp)`);
            const before = Skills.effective('hitpoints');
            if (!(await food.interact('Eat'))) {
                return;
            }
            await Execution.delayUntil(() => Skills.effective('hitpoints') > before || foodCount() === 0, 3000);
            if (Skills.effective('hitpoints') > before) {
                this.bot.countEat();
            }
        }
    }
}

/** Escape high-z obstacle pits. Only registered for courses with a pit def. */
class PitEscape implements Task {
    constructor(private bot: AgilityRunner) {}

    private findLadder(): Loc | null {
        // Prefer Climb-up so we don't keep climbing down into the pit.
        let ladder = Locs.query()
            .where(l => l.actions().some(a => /climb[- ]up/i.test(a)))
            .nearest();
        if (!ladder) {
            ladder = Locs.query()
                .where(l => l.actions().some(a => /climb|ladder/i.test(a)))
                .nearest();
        }
        return ladder;
    }

    validate(): boolean {
        // Death recovery owns repositioning after death.
        if (this.bot.died) {
            return false;
        }
        const pit = this.bot.courseDef().pit;
        const here = Game.tile();
        return pit !== undefined && here !== null && inPitScene(here, pit);
    }

    async execute(): Promise<void> {
        if (this.bot.died) {
            return;
        }
        const pit = this.bot.courseDef().pit!;
        const ladder = this.findLadder();
        if (!ladder) {
            const near = Locs.query()
                .where(l => l.actions().length > 0)
                .nearest();
            this.bot.setStatus('in the pit — no ladder found');
            this.bot.log(
                `fell into the pit but found no climb/ladder loc — nearest interactable: ${
                    near ? `${near.name} [${near.actions().join(', ')}]` : 'none'
                }`
            );
            await Execution.delayTicks(3);
            return;
        }

        const op =
            ladder.actions().find(a => /climb[- ]up/i.test(a)) ??
            ladder.actions().find(a => /climb|ladder/i.test(a)) ??
            'Climb-up';

        const here = Game.tile();
        const lt = ladder.tile();
        if (here && !nearTileL(here, lt, 2)) {
            this.bot.setStatus('in the pit — heading to the ladder');
            await Traversal.walkTo(lt, { radius: 1 });
            if (this.bot.died) {
                return;
            }
        }

        this.bot.setStatus(`climbing out of the pit (${op} ${ladder.name})`);
        this.bot.log(`fell into the pit — ${op} ${ladder.name} back up to the course`);
        if (!(await ladder.interact(op))) {
            await Execution.delayTicks(2);
            return;
        }
        await Execution.delayUntil(() => {
            const t = Game.tile();
            return t !== null && !inPitScene(t, pit);
        }, 10_000);
        this.bot.markEscapedPit();
    }
}

/** Pick up a Mark of grace on our plane inside the course zone (rooftops). */
class PickupMark implements Task {
    private skipUntil = new Map<string, number>();

    constructor(private bot: AgilityRunner) {}

    private findMark() {
        const def = this.bot.courseDef();
        const here = Game.tile();
        if (!here) {
            return null;
        }
        const now = performance.now();
        return GroundItems.query()
            .where(g => (g.name?.toLowerCase() ?? '') === 'mark of grace')
            .where(g => g.tile().level === here.level && inZone(g.tile(), def.zone))
            .where(g => (this.skipUntil.get(`${g.tile().x},${g.tile().z}`) ?? 0) < now)
            .within(12)
            .nearest();
    }

    validate(): boolean {
        if (this.bot.died || EventSignal.pending()) {
            return false;
        }
        return this.findMark() !== null;
    }

    async execute(): Promise<void> {
        const mark = this.findMark();
        if (!mark) {
            return;
        }
        const tile = mark.tile();
        const key = `${tile.x},${tile.z}`;
        this.bot.setStatus(`picking up mark of grace at ${tile.x},${tile.z}`);
        const before = Inventory.count('Mark of grace');
        const status = await Reach.entityOp({
            find: () =>
                GroundItems.query()
                    .where(g => g.id === mark.id && g.tile().equals(tile))
                    .nearest(),
            op: 'Take',
            expect: () => Inventory.count('Mark of grace') > before,
            expectMs: 6000,
            what: 'Mark of grace',
            log: m => this.bot.log(m)
        });
        if (status === 'done' && Inventory.count('Mark of grace') > before) {
            this.bot.countMark();
            this.bot.log('picked up a mark of grace');
        } else {
            // One attempt per sighting: a mark we can't reach from this roof
            // section must not stall the lap.
            this.skipUntil.set(key, performance.now() + MARK_SKIP_MS);
            this.bot.log(`mark pickup did not complete — ignoring ${key} for ${MARK_SKIP_MS / 1000}s`);
        }
    }
}

class TravelToCourse implements Task {
    constructor(private bot: AgilityRunner) {}

    validate(): boolean {
        const here = Game.tile();
        return here !== null && awayFromCourse(here, this.bot.courseDef());
    }

    async execute(): Promise<void> {
        const approach = this.bot.courseDef().approach!;
        this.bot.markLeft();
        this.bot.setStatus(`walking to the course (${approach.x},${approach.z})`);
        await Traversal.walkResilient(approach, {
            radius: 2,
            attempts: 6,
            timeoutMs: 120_000,
            log: m => this.bot.log(`  ${m}`)
        });
    }
}

/** Entry-hook courses only (wilderness ridge). */
class EnterCourse implements Task {
    constructor(private bot: AgilityRunner) {}

    validate(): boolean {
        const def = this.bot.courseDef();
        const here = Game.tile();
        if (here === null) {
            return false;
        }
        // Once inside, never validate — the gate exits back out and clicking
        // the ridge from inside derails the lap.
        if (inZone(here, def.zone)) {
            return false;
        }
        const e = def.entry!;
        if (!entryCorridor(here, def) && !nearTileL(here, e.approach, 4)) {
            return false;
        }
        return southOfDoor(here, e.door);
    }

    async execute(): Promise<void> {
        await attemptRidgeCrossing(this.bot);
    }
}
```

- [ ] **Step 4: Append RunLap**

The port of WildyAgility's RunLap with the CourseDef deltas: per-step fail modes, optional start tiles, plane-drop detection against the click-time plane, course-gated chat regexes, and the two new ceilings (no-obstacle, full-lap-of-skips).

```ts
class RunLap implements Task {
    private stuck = 0;
    private loggedOutOfFood = false;
    private noObstacleTicks = 0;

    constructor(private bot: AgilityRunner) {}

    private find(name: string): Loc | null {
        const within = this.bot.searchRadius();
        return Locs.query()
            .where(l => l.name?.toLowerCase() === name && l.distance() <= within && l.actions().length > 0)
            .nearest();
    }

    validate(): boolean {
        const here = Game.tile();
        if (here === null || this.bot.courseDef().steps.length === 0) {
            return false;
        }
        // Stays active after a pit climb so the walk to the next start tile
        // happens even when the ladder exit briefly reads outside the zone.
        if (this.bot.justEscapedPit) {
            return true;
        }
        return this.bot.isEntered() || inZone(here, this.bot.courseDef().zone);
    }

    async execute(): Promise<void> {
        const def = this.bot.courseDef();
        const step = this.bot.currentStep();
        const name = step.name;

        // Food runs out mid-session on danger courses; restock is death-only.
        if (def.danger) {
            if (foodCount() === 0) {
                if (!this.loggedOutOfFood) {
                    this.loggedOutOfFood = true;
                    this.bot.log(`out of '${FOOD}' — continuing course until death (death recovery restocks)`);
                }
            } else {
                this.loggedOutOfFood = false;
            }
        }

        // Approach before finding/clicking/timing — pit-ladder exits and lap
        // wraps are far from the next start tile, and counting that walk against
        // OBSTACLE_TIMEOUT_TICKS causes false "no progress" retries.
        const escapedPit = this.bot.justEscapedPit;
        if (escapedPit) {
            this.bot.clearEscapedPit();
            this.bot.log(`just escaped pit — walking to '${name}' starting side before clicking`);
        }
        if (escapedPit || !this.nearStart(step, 2)) {
            const alreadyHere = !escapedPit && this.find(name);
            if (!alreadyHere) {
                this.bot.log(`walking to '${name}' starting side`);
                const walked = await this.walkToStartTile(step);
                if (this.bot.died || EventSignal.pending() || ChatDialog.canContinue()) {
                    return;
                }
                const here = Game.tile();
                if (here !== null && def.pit && inPitScene(here, def.pit)) {
                    return; // still in the pit (failed climb / re-fell) — PitEscape owns it
                }
                if (!walked && !this.nearStart(step, 2) && !this.find(name)) {
                    if (++this.stuck >= LAP_RETRY_LIMIT) {
                        this.bot.log(`'${name}' start unreachable after ${this.stuck} tries — moving on`);
                        this.stuck = 0;
                        this.skipAdvance();
                    } else {
                        this.bot.log(`could not reach '${name}' start — will retry (${this.stuck}/${LAP_RETRY_LIMIT})`);
                        await Execution.delayTicks(2);
                    }
                    return;
                }
            }
        }

        let obstacle = this.find(name);
        if (!obstacle) {
            // Resync earliest-visible obstacle in course order (not nearest) so
            // recovery near the entrance doesn't jump mid-lap.
            for (const courseName of this.bot.courseNames()) {
                if (this.find(courseName) && this.bot.resyncTo(courseName)) {
                    obstacle = this.find(courseName);
                    this.stuck = 0;
                    break;
                }
            }
        }
        if (!obstacle) {
            this.noObstacleTicks++;
            this.bot.setStatus(`waiting: no ${this.bot.currentName()} within ${this.bot.searchRadius()} tiles`);
            this.bot.log(
                `waiting: no '${this.bot.currentName()}' within ${this.bot.searchRadius()} tiles (${this.noObstacleTicks}/${NO_OBSTACLE_STOP})`
            );
            if (this.noObstacleTicks >= NO_OBSTACLE_STOP) {
                ScriptRunner.stop(
                    `no course obstacle visible for ${NO_OBSTACLE_STOP} passes — wrong area or wrong course selected`
                );
                return;
            }
            await Execution.delayTicks(2);
            return;
        }
        this.noObstacleTicks = 0;

        const currentStep = this.bot.currentStep();
        const op = currentStep.op ?? obstacle.actions()[0];
        if (!op) {
            this.bot.log(`'${obstacle.name}' has no actions — retrying`);
            await Execution.delayTicks(2);
            return;
        }

        const gapTicks = Game.tick() - this.bot.lastClearedTick;
        if (!escapedPit && gapTicks > OBSTACLE_TIMEOUT_TICKS * 2) {
            this.bot.log(`gap ${gapTicks} ticks since last obstacle`);
        }

        const mark = GameMessages.mark();
        const before = Skills.xp('agility');
        const clickLevel = Game.tile()?.level ?? 0;
        const ot = obstacle.tile();
        this.bot.setStatus(`${op} ${obstacle.name} at ${ot.x},${ot.z}`);
        this.bot.log(`${op} '${this.bot.currentName()}' @ ${ot.x},${ot.z}`);

        if (!(await obstacle.interact(op))) {
            this.bot.log(`interact('${op}') on '${this.bot.currentName()}' failed — retrying`);
            await Execution.delayTicks(2);
            return;
        }

        // Timeout starts at the click; a hard wall-clock bound covers perpetual
        // animation/movement from combat or pathing jitter.
        const clickTick = Game.tick();
        const waitDeadline = performance.now() + OBSTACLE_TIMEOUT_TICKS * 600 + 3_000;
        let idleTicks = 0;
        let waitedTicks = 0;
        let lowHp = false;
        let lastTile = Game.tile();
        while (idleTicks < OBSTACLE_TIMEOUT_TICKS) {
            if (performance.now() >= waitDeadline) {
                break;
            }
            const t = Game.tile();
            if (Skills.xp('agility') > before) {
                break;
            }
            if (t !== null && def.pit && inPitScene(t, def.pit)) {
                break;
            }
            // Plane-drop steps: falling below the click plane settles the wait.
            // Gated by failMode because legitimate descents (gnome branch-down,
            // draynor crate) change plane WITH xp — xp may land a tick later,
            // and an ungated check would misread a working obstacle as a fall.
            if (currentStep.failMode === 'planeDrop' && t !== null && t.level < clickLevel) {
                break;
            }
            if (GameMessages.sawSince(mark, CANT_REACH)) {
                break;
            }
            if (def.wrongSideRe && GameMessages.sawSince(mark, def.wrongSideRe)) {
                break;
            }
            if (def.failRe && GameMessages.sawSince(mark, def.failRe)) {
                break;
            }
            if (EventSignal.pending() || ChatDialog.canContinue()) {
                break;
            }
            // Danger courses: yield to EatFood while monsters chip us — after a
            // few ticks so residual damage doesn't abort the click.
            if (def.danger && waitedTicks >= 3 && needEat()) {
                lowHp = true;
                break;
            }

            await Execution.delayTicks(1);
            waitedTicks++;

            const now = Game.tile();
            const moved =
                !!now && !!lastTile && (now.x !== lastTile.x || now.z !== lastTile.z || now.level !== lastTile.level);
            if (!Game.animating() && !moved) {
                idleTicks++;
            }
            lastTile = now ?? lastTile;
        }

        const hereAfter = Game.tile();
        const reason = classifyObstacle({
            xpGained: Skills.xp('agility') > before,
            inPit: hereAfter !== null && def.pit !== undefined && inPitScene(hereAfter, def.pit),
            fellPlane:
                currentStep.failMode === 'planeDrop' && hereAfter !== null && hereAfter.level < clickLevel,
            cantReach: GameMessages.sawSince(mark, CANT_REACH),
            wrongSide: def.wrongSideRe ? GameMessages.sawSince(mark, def.wrongSideRe) : false,
            failMessage: def.failRe ? GameMessages.sawSince(mark, def.failRe) : false,
            interrupted: EventSignal.pending() || ChatDialog.canContinue(),
            lowHp
        });

        if (reason === 'interrupted') {
            this.bot.setStatus('random event — handling');
            return;
        }

        if (reason === 'pit') {
            this.bot.log(`fell into the pit during '${this.bot.currentName()}' — escaping`);
            this.bot.setStatus('in the pit — escaping');
            this.stuck = 0; // a fall is a live outcome, not a stuck click
            return;
        }

        if (reason === 'plane_drop') {
            this.stuck = 0;
            this.bot.log(`fell off '${this.bot.currentName()}' (plane drop) — recovering`);
            // Rooftop reality: getting back up means redoing the course from its
            // ground entry, so without a per-step start tile we walk to the
            // course approach and let the resync land on the entry obstacle.
            if (currentStep.startTile) {
                await this.walkToStartTile(currentStep);
            } else if (def.approach) {
                this.bot.setStatus('walking back to the course start');
                await Traversal.walkTo(def.approach, { radius: 2, timeoutMs: 45_000 });
            }
            return; // no advance: the failed obstacle must be re-attempted
        }

        if (reason === 'timeout') {
            this.bot.setStatus(`timeout waiting for ${obstacle.name} (${idleTicks} idle / ${waitedTicks} total ticks)`);
            this.bot.log(`'${this.bot.currentName()}' timed out after ${waitedTicks} ticks (${idleTicks} idle) — no xp/chat`);
        }

        if (reason === 'xp') {
            this.stuck = 0;
            this.bot.consecutiveSkips = 0;
            this.bot.countCleared();
            const elapsed = Game.tick() - clickTick;
            this.bot.log(`cleared '${this.bot.currentName()}' in ${elapsed} ticks`);
            this.bot.obstacleTimes.push(elapsed);
            if (this.bot.obstacleTimes.length > 20) {
                this.bot.obstacleTimes.shift();
            }
            this.bot.lastClearedTick = Game.tick();
            this.bot.advance();
            await Execution.delay(reactionMs());
            return;
        }

        if (reason === 'low_hp') {
            this.bot.log(
                `yielding '${this.bot.currentName()}' for food (${Math.round(Skills.hpFraction() * 100)}% hp, ${foodCount()} left)`
            );
            return;
        }

        if (reason === 'wrong_side') {
            this.bot.log(`'${this.bot.currentName()}' wrong side error — walking to starting side`);
            await this.walkToStartTile(currentStep);
            return;
        }

        if (reason === 'fail_msg') {
            this.bot.log(`'${this.bot.currentName()}' fell (no scene change) — retrying`);
            this.stuck = 0;
            await Execution.delayTicks(1);
            return;
        }

        if (reason === 'cant_reach') {
            this.bot.setStatus(`out of range for ${obstacle.name} — retrying`);
            this.bot.log(`'${this.bot.currentName()}' can't reach — retrying`);
        }

        // stuck=1: in-place retry; stuck=2: walk back to start; stuck>limit: skip.
        if (++this.stuck > LAP_RETRY_LIMIT) {
            const skipped = this.bot.currentName();
            this.bot.log(`'${skipped}' isn't completing from here after ${this.stuck} tries — moving on`);
            this.stuck = 0;
            this.skipAdvance();
            const nextName = this.bot.currentName();
            const firstName = this.bot.courseNames()[0]!;
            if (nextName === skipped || nextName === firstName) {
                const firstStep = this.bot.courseDef().steps[0]!;
                if (firstStep.startTile) {
                    this.bot.log(`obstacle skip wrapped lap — walking to '${firstName}' starting side`);
                    await this.walkToStartTile(firstStep);
                }
            }
        } else if (this.stuck >= 2) {
            this.bot.log(`'${this.bot.currentName()}' no progress — walking back to starting side (${this.stuck}/${LAP_RETRY_LIMIT})`);
            const walked = await this.walkToStartTile(currentStep);
            if (!walked) {
                this.bot.log(`'${this.bot.currentName()}' starting side unreachable — advancing to next obstacle`);
                this.stuck = 0;
                this.skipAdvance();
            }
        } else {
            this.bot.setStatus(`retrying ${obstacle.name}`);
            this.bot.log(`'${this.bot.currentName()}' no progress — retrying (${this.stuck}/${LAP_RETRY_LIMIT})`);
            await Execution.delayTicks(2);
        }
    }

    /** Advance past a step WITHOUT completing it, with the structural ceiling. */
    private skipAdvance(): void {
        this.bot.consecutiveSkips++;
        if (this.bot.consecutiveSkips >= this.bot.courseDef().steps.length) {
            ScriptRunner.stop(
                `a full lap advanced only by skips (${this.bot.consecutiveSkips}) — course is structurally stuck`
            );
            return;
        }
        this.bot.advance();
    }

    private nearStart(step: ObstacleStep, radius: number): boolean {
        const here = Game.tile();
        return !!step.startTile && !!here && nearTileL(here, step.startTile, radius);
    }

    private async walkToStartTile(step: ObstacleStep): Promise<boolean> {
        const start = step.startTile;
        if (!start) {
            // No recovery tile for this step (gnome/rooftop data) — retry in
            // place; the resync path handles being genuinely elsewhere.
            this.bot.log(`no starting side for '${step.name}' — retrying in place`);
            this.bot.setStatus(`retrying ${step.name}`);
            await Execution.delayTicks(2);
            return false;
        }
        this.bot.setStatus(`walking to ${step.name} starting side (${start.x},${start.z})`);
        const here = Game.tile();
        if (here && nearTileL(here, start, 1)) {
            this.bot.log(`already near starting side for '${step.name}'`);
            await Execution.delayTicks(1);
            return true;
        }
        // Bound the walk so a bad path cannot hang the lap with no further logs.
        const result = await Traversal.walkTo(start, { radius: 1, timeoutMs: 45_000 });
        if (!result) {
            this.bot.log(`could not reach starting side for '${step.name}' — path unreachable`);
            return false;
        }
        return true;
    }
}
```

- [ ] **Step 5: Mechanical verification (runs here)**

Run: `grep -c "class \(EatFood\|PitEscape\|PickupMark\|TravelToCourse\|EnterCourse\|RunLap\)" data/rs2_scripts/AgilityRunner/AgilityRunner.ts`
Expected: `6` — every task class the onStart registration references exists exactly once.

Run: `grep -n "entryTimeouts\|consecutiveSkips\|noObstacleTicks" data/rs2_scripts/AgilityRunner/AgilityRunner.ts | grep -c "++"`
Expected: `3` — all three ceilings actually count.

- [ ] **Step 6: Commit**

```bash
git add data/rs2_scripts/AgilityRunner/AgilityRunner.ts
git commit -m "agility runner: generic TaskBot engine generalized from WildyAgility

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Register in index.ts

**Files:**
- Modify: `data/rs2_scripts/index.ts` (imports near the top with the other script imports; registration block next to the existing `GnomeCourse`/`WildyAgility` blocks, ~line 615)

**Interfaces:**
- Consumes: `AgilityRunner` default export + `AGILITY_RUNNER_SETTINGS` from Task 3.
- Produces: the `AgilityRunner` entry in `ScriptRegistry`.

- [ ] **Step 1: Add the import** (alphabetically near the other script imports)

```ts
import AgilityRunner, { AGILITY_RUNNER_SETTINGS } from './AgilityRunner/AgilityRunner.js';
```

- [ ] **Step 2: Add the registration block** (immediately after the `WildyAgility` block, so the Agility category reads together)

```ts
ScriptRegistry.register({
    name: 'AgilityRunner',
    description:
        'Generic agility course runner — Wilderness (food + death recovery, behavior-parity with WildyAgility), Gnome Stronghold, Draynor and Canifis rooftops (marks of grace), or a custom obstacle list. Per-course fail handling: pit escapes, rooftop plane drops, knockbacks',
    category: 'Agility',
    tags: ['course', 'generic', 'rooftop', 'wilderness', 'marks-of-grace'],
    settingsSchema: AGILITY_RUNNER_SETTINGS,
    create: () => new AgilityRunner()
});
```

Do NOT remove or alter the `GnomeCourse` (`AgilityBot`) or `WildyAgility` registrations — both stay until the live parity gate below.

- [ ] **Step 3: Mechanical verification (runs here)**

Run: `grep -c "ScriptRegistry.register" data/rs2_scripts/index.ts`
Expected: exactly one more than before the edit (record the before count first).

- [ ] **Step 4: Commit**

```bash
git add data/rs2_scripts/index.ts
git commit -m "agility runner: register AgilityRunner (AgilityBot/WildyAgility kept until parity)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Framework-repo verification + live parity gate (user-run)

No code. This task is the checklist the plan hands to the user, because nothing in this repo can compile or run TypeScript (see Global Constraints).

- [ ] **Step 1 [framework repo]: Sync + compile.** Copy/sync `AgilityRunner/` and the `index.ts` change into the repo where `../../api` resolves; run its build/typecheck. Fix any type errors here first — likely suspects are the `Reach.entityOp` option shape and `Traversal` option fields, both transcribed from `ChickenKiller.ts`/`WildyAgility.ts` usage rather than their definitions.
- [ ] **Step 2 [framework repo]: Run the selftest.** `npx tsx data/rs2_scripts/AgilityRunner/AgilityRunnerLogic.selftest.ts` (path as synced). Expected: `AgilityRunnerLogic selftest: all assertions passed`.
- [ ] **Step 3 [live] Wilderness parity session** — the gate the user set ("make sure wilderness agility works"). Observe ALL of, in one session or several:
  - Full laps completing with the lap-bonus XP on rocks (571.4/lap total).
  - An obstacle-pit fall (ropeswing or log) recovered: PitEscape climbs out, lap resumes at the SAME obstacle, no skip.
  - A ridge fail (wolf pit) recovered back to the approach and retried.
  - A death recovered: bank restock, walk back, re-entry.
  - The stepping-stone knockback retried in place.
- [ ] **Step 4 [live] Rooftop smoke:** one Draynor session (laps + a mark of grace pickup) and one Canifis session (ideally at an agility level that still fails Gap #3, to see the plane-drop recovery walk back to the tall tree and resync).
- [ ] **Step 5 [live] Custom-mode smoke:** run `Custom (obstacle list)` with AgilityBot's gnome list to confirm the AgilityBot replacement path.
- [ ] **Step 6 [decision, user's call per spec]:** once parity holds, retire `WildyAgility` (thin alias or delete) and `AgilityBot` — a separate task, not part of this plan.

---

## Self-Review (completed)

- **Spec coverage:** CourseDef schema → Task 1; wilderness-by-import parity → Task 2; engine task set, RunLap generalization, PickupMark, entry hook, ceilings → Task 3; registration → Task 4; parity plan + custom mode → Tasks 3/5. Deviations from spec, both flagged in Global Constraints: `locIds` dropped (YAGNI, unverified `Loc.id` surface); `plane` per step replaced by runtime click-plane detection (strictly better — no static plane data to get wrong).
- **Placeholder scan:** no TBDs; every code step carries full code.
- **Type consistency:** `TileXZ`/`ZoneBox`/`ObstacleStep`/`CourseDef`/`WildernessRidgeEntry` names match across Tasks 1–3; `courseByLabel`/`COURSE_LABELS`/`CUSTOM_LABEL` match Tasks 2–3; `AGILITY_RUNNER_SETTINGS`/default export match Tasks 3–4; `ObstacleSignals.failMessage`/`fellPlane` match the classify call in RunLap.
