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
