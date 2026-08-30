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
