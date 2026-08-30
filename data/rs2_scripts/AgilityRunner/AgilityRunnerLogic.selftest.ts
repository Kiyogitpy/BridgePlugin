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
