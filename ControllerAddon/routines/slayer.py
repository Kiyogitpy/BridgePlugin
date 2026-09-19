# routines/slayer.py -- the Slayer TASK routine: get a task, deal with it, get the next one.
#
# Sub-project 2 of docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md (2026-09-19):
# ACQUIRE through NPC Contact (Kuradal on every `milestoneEvery`-th task, Aya otherwise), SKIP an
# unwanted task for 30 points, STOP on a task the user does by hand, and FIGHT when already
# standing at a known site. What is NOT here yet is sub-project 3: travel, banking and loadouts.
# So away from the site this routine STOPS and says where to go -- walk there, press F2/relaunch,
# and it fights, finishes, and asks the next master.
#
# The phase is DERIVED from GET /slayer every tick (spec §7.1); ctx.state.data holds only
# counters, so a restart mid-task resumes correctly. None from /slayer = do nothing.
#
# The old "fight the monsters picked in the panel" behaviour is routines/combat.py.
# UNVERIFIED LIVE as of 2026-09-19.

import time

import combat_core
import controller
import debug_log
import npc_contact
import slayer_state
import slayer_tasks

LOG = "slayer"

# The routine owns every dialogue it opens: the engine's once-per-tick Space must never land in a
# slayer master's dialogue. During FIGHT it calls check_stray_dialog itself (level-up boxes).
SKIP_STRAY_DIALOG_CHECK = True
VARP_SUBSCRIPTIONS = (394, 395)
VARBIT_SUBSCRIPTIONS = (4067, 4068, 4069)

ACQUIRE, SKIP, STOP, FIGHT, GO_THERE = "ACQUIRE", "SKIP", "STOP", "FIGHT", "GO_THERE"

CONTACT_MAX_CASTS = 3          # each cast spends runes: a ceiling, not just a cooldown
CONTACT_RETRY_COOLDOWN_S = 6.0


def get_config(bridge_config):
    return controller.get_slayer_config()


def on_event(ctx, event_type, payload):
    combat_core.on_event(ctx, event_type, payload)


def derive_phase(state, tasks_cfg, player) -> str:
    if not state.has_task:
        return ACQUIRE
    if state.task is None:
        return STOP                      # the name did not decode: never cancel or fight a guess
    verdict = slayer_tasks.disposition(state.task, tasks_cfg)
    if verdict == slayer_tasks.SKIP:
        return SKIP
    if verdict == slayer_tasks.STOP:
        return STOP
    return FIGHT if slayer_tasks.at_site(state.task, player) else GO_THERE


def _reset_slot_counters(ctx, state) -> None:
    """Casts and skips are counted per task SLOT. The streak moving, or a task appearing, is a
    new slot."""
    seen = ctx.state.data.get("slayer_streak_seen")
    if seen != state.streak:
        ctx.state.data.update({"slayer_streak_seen": state.streak, "slayer_casts": 0, "slayer_skips": 0})


def _acquire(ctx, state, tasks_cfg) -> None:
    master = slayer_state.next_master(state.streak, tasks_cfg)
    problem = npc_contact.missing_requirements(master)
    if problem:
        ctx.state.trigger_safety_stop(f"Slayer: cannot contact {master} -- {problem}")
        return
    casts = ctx.state.data.get("slayer_casts", 0)
    if casts >= CONTACT_MAX_CASTS:
        ctx.state.trigger_safety_stop(
            f"Slayer: cast NPC Contact {casts} times for task {state.streak + 1} and no task ever "
            f"registered with {master} -- see the [npc_contact] lines above")
        return
    if time.time() - ctx.state.data.get("slayer_contact_at", 0.0) < CONTACT_RETRY_COOLDOWN_S:
        return
    ctx.state.data["slayer_contact_at"] = time.time()
    ctx.state.data["slayer_casts"] = casts + 1
    ctx.state.data.pop("npc_contact_unknown_menu", None)
    debug_log.log(LOG, f"no task: asking {master} for task {state.streak + 1} (streak {state.streak}, "
                       f"{state.points} points)")
    try:
        got = npc_contact.get_task(ctx, master, tasks_cfg)
    except slayer_state.StreakGuardError as exc:
        ctx.state.trigger_safety_stop(f"Slayer: {exc}")
        return
    unknown = ctx.state.data.get("npc_contact_unknown_menu")
    if unknown:
        ctx.state.trigger_safety_stop(
            f"Slayer: {master}'s dialogue showed a menu this routine does not know, and it was "
            f"escaped without choosing: [{unknown}]. Tell the developer these options")
        return
    if got is not None:
        ctx.state.data["slayer_casts"] = 0
        debug_log.log(LOG, f"task: {got.task or got.task_id} x{got.count} from {master} "
                           f"(streak {got.streak}, {got.points} points)")


def _skip(ctx, state, tasks_cfg) -> None:
    ok, why = slayer_state.can_skip(state.points, ctx.state.data.get("slayer_skips", 0), tasks_cfg)
    if not ok:
        ctx.state.trigger_safety_stop(f"Slayer: {state.task} is on the skip list but {why}")
        return
    master = (tasks_cfg or {}).get("milestoneMaster") or slayer_state.MILESTONE_DEFAULT
    problem = npc_contact.missing_requirements(master)
    if problem:
        ctx.state.trigger_safety_stop(f"Slayer: cannot cancel {state.task} -- {problem}")
        return
    if time.time() - ctx.state.data.get("slayer_contact_at", 0.0) < CONTACT_RETRY_COOLDOWN_S:
        return
    ctx.state.data["slayer_contact_at"] = time.time()
    attempts = ctx.state.data.get("slayer_cancel_attempts", 0) + 1
    ctx.state.data["slayer_cancel_attempts"] = attempts
    if attempts > CONTACT_MAX_CASTS:
        ctx.state.trigger_safety_stop(f"Slayer: {attempts - 1} attempts to cancel {state.task} through "
                                      f"{master}'s rewards did not clear the task")
        return
    debug_log.log(LOG, f"skipping {state.task} through {master}'s rewards ({state.points} points)")
    try:
        if npc_contact.cancel_task(ctx, master, tasks_cfg):
            ctx.state.data["slayer_cancel_attempts"] = 0
    except slayer_state.StreakGuardError as exc:
        ctx.state.trigger_safety_stop(f"Slayer: {exc}")


def _fight(ctx, state) -> None:
    controller.check_stray_dialog(ctx)
    panel = ctx.state.config
    ctx.state.config = combat_core.combat_config(panel, slayer_tasks.target_names(state.task))
    try:
        combat_core.fight_tick(ctx)
    finally:
        ctx.state.config = panel


def tick(ctx):
    state = slayer_state.from_bridge(controller.get_slayer_task())
    if state is None:
        debug_log.log(LOG, "GET /slayer unavailable -- doing nothing")
        return
    tasks_cfg = (ctx.state.config or {}).get("tasks") or {}
    _reset_slot_counters(ctx, state)
    phase = derive_phase(state, tasks_cfg, controller.get_player())
    if phase == ACQUIRE:
        _acquire(ctx, state, tasks_cfg)
    elif phase == SKIP:
        _skip(ctx, state, tasks_cfg)
    elif phase == STOP:
        label = state.task or f"task id {state.task_id} (name did not decode)"
        ctx.state.trigger_safety_stop(f"Slayer: {label} x{state.count} is a task this routine leaves to you")
    elif phase == GO_THERE:
        where = slayer_tasks.where(state.task)
        ctx.state.trigger_safety_stop(
            f"Slayer: task is {state.task} x{state.count}. Travel is not built yet -- "
            + (f"go to {where}, then start the routine again" if where
               else "this task's site is not in the catalog yet (sub-project 3); use 'Combat' for it"))
    else:
        _fight(ctx, state)
