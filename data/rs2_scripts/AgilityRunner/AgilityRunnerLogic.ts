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
