# routines/slayer.py -- the Slayer TASK routine: get a task, deal with it, get the next one.
#
# Sub-project 2 of docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md (2026-09-19):
# ACQUIRE through NPC Contact (Kuradal on every `milestoneEvery`-th task, Aya otherwise), SKIP an
# unwanted task for 30 points, STOP on a task the user does by hand, and FIGHT when already
# standing at a known site. Sub-project 3 phase A (2026-09-19) adds TRAVEL: a fairy-ring or
# carried-item route from where the player stands, for Aya tasks whose teleport list this phase
# knows how to use. The house, the bank and Kuradal's sites are still to come -- until they exist,
# a route TRAVEL cannot plan is a safety stop naming why.
#
# The phase is DERIVED from GET /slayer every tick (spec §7.1); ctx.state.data holds only
# counters, so a restart mid-task resumes correctly. None from /slayer = do nothing.
#
# The old "fight the monsters picked in the panel" behaviour is routines/combat.py.
# UNVERIFIED LIVE as of 2026-09-19.

import time

import aya_tasks
import combat_core
import controller
import debug_log
import gear
import legs
import npc_contact
import slayer_routes
import slayer_state
import slayer_tasks

LOG = "slayer"

# The routine owns every dialogue it opens: the engine's once-per-tick Space must never land in a
# slayer master's dialogue. During FIGHT it calls check_stray_dialog itself (level-up boxes).
SKIP_STRAY_DIALOG_CHECK = True
VARP_SUBSCRIPTIONS = (394, 395)
VARBIT_SUBSCRIPTIONS = (4067, 4068, 4069)

ACQUIRE, SKIP, STOP, FIGHT, TRAVEL = "ACQUIRE", "SKIP", "STOP", "FIGHT", "TRAVEL"
GO_THERE = TRAVEL              # the pre-phase-A name; kept so old notes and logs still read

CONTACT_MAX_CASTS = 3          # each cast spends runes: a ceiling, not just a cooldown
CONTACT_RETRY_COOLDOWN_S = 6.0

TRAVEL_MAX_FAILS = 3           # a leg that fails three times strands us: stop and NAME it
TRAVEL_MAX_PROGRESS = 40       # legs.run_next's PROGRESS is unbounded by design; the caller bounds it

NO_TARGET_TICKS = 30           # ~18s with nothing to fight before walking back to the site tile
NO_TARGET_WALKS = 3            # that many walks back with still nothing found: stop, don't idle forever


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
    return FIGHT if slayer_tasks.at_site(state.task, player) else TRAVEL


def _reset_slot_counters(ctx, state) -> None:
    """Casts and skips are counted per task SLOT. The streak moving, or a task appearing, is a
    new slot."""
    seen = ctx.state.data.get("slayer_streak_seen")
    if seen != state.streak:
        ctx.state.data.update({"slayer_streak_seen": state.streak, "slayer_casts": 0, "slayer_skips": 0,
                               "slayer_cancel_attempts": 0, "slayer_route": None, "slayer_route_for": None,
                               "slayer_leg": 0, "slayer_leg_fails": 0, "slayer_leg_progress": 0,
                               "slayer_route_finished": 0, "slayer_no_target_walks": 0})


def _acquire(ctx, state, tasks_cfg) -> None:
    master = slayer_state.next_master(state.streak, tasks_cfg)
    # Cooldown FIRST: missing_requirements is five bridge reads, not something to repeat every tick.
    if time.time() - ctx.state.data.get("slayer_contact_at", 0.0) < CONTACT_RETRY_COOLDOWN_S:
        return
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
    if time.time() - ctx.state.data.get("slayer_contact_at", 0.0) < CONTACT_RETRY_COOLDOWN_S:
        return
    problem = npc_contact.missing_requirements(master)
    if problem:
        ctx.state.trigger_safety_stop(f"Slayer: cannot cancel {state.task} -- {problem}")
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


def _carried() -> dict:
    """Lowercased item name -> quantity, inventory plus worn. Built from gear.py's own readers so
    the /equipment-is-a-dict shape is handled in exactly one place."""
    counts = {}
    for item in controller.get_inventory() or ():
        name = (item.get("name") or "").lower()
        if name:
            counts[name] = counts.get(name, 0) + int(item.get("qty") or item.get("quantity") or 1)
    for name in gear.worn_names() or ():
        counts[(name or "").lower()] = counts.get((name or "").lower(), 0) + 1
    return counts


def _stranded(ctx, state, why) -> None:
    where = slayer_tasks.where(state.task)
    ctx.state.trigger_safety_stop(
        f"Slayer: task is {state.task} x{state.count} and I cannot get there -- {why}."
        + (f" The site is {where}." if where else ""))


def _travel(ctx, state, player) -> None:
    data = ctx.state.data
    carried = _carried()
    missing = slayer_tasks.missing_items(state.task, state.count, carried)
    if missing:
        ctx.state.trigger_safety_stop(f"Slayer: {state.task} needs {', '.join(missing)} -- not carried")
        return
    key = (state.task, state.streak)
    if data.get("slayer_route_for") != key or not data.get("slayer_route"):
        route, why = slayer_routes.plan(state.task, player, carried, skill_levels=legs.skill_levels())
        if route is None:
            _stranded(ctx, state, why)
            return
        data.update({"slayer_route": route, "slayer_route_for": key, "slayer_leg": 0,
                     "slayer_leg_fails": 0, "slayer_leg_progress": 0})
        debug_log.log(LOG, f"route to {state.task}: " + " -> ".join(leg["display"] for leg in route))
    route, cursor = data["slayer_route"], data.get("slayer_leg", 0)
    status, cursor = legs.run_next(ctx, route, cursor, log=LOG)
    name = route[min(cursor, len(route) - 1)]["display"]
    if status == legs.DONE:
        data.update({"slayer_leg": cursor, "slayer_leg_fails": 0, "slayer_leg_progress": 0,
                     "slayer_route_finished": 0})
    elif status == legs.PROGRESS:
        data["slayer_leg_progress"] = data.get("slayer_leg_progress", 0) + 1
        if data["slayer_leg_progress"] > TRAVEL_MAX_PROGRESS:
            _stranded(ctx, state, f"leg '{name}' made no landing in {TRAVEL_MAX_PROGRESS} ticks")
    elif status == legs.FAILED:
        data["slayer_leg_fails"] = data.get("slayer_leg_fails", 0) + 1
        if data["slayer_leg_fails"] >= TRAVEL_MAX_FAILS:
            _stranded(ctx, state, f"leg '{name}' failed {TRAVEL_MAX_FAILS} times")
    else:                                   # FINISHED, and derive_phase still says we are not there
        first_finish = data.get("slayer_route_finished", 0) == 0
        data["slayer_route_finished"] = data.get("slayer_route_finished", 0) + 1
        if first_finish:
            data["slayer_route"] = None    # drop it so the NEXT tick re-plans from here
        if data["slayer_route_finished"] >= 2:
            # already gave up: leave the exhausted route in place so a tick that still runs after
            # the (terminal) safety stop cannot spend another plan() on it.
            _stranded(ctx, state, "the route finished twice but the site is not reached")


def _fight(ctx, state) -> None:
    controller.check_stray_dialog(ctx)
    panel = ctx.state.config
    cfg = combat_core.combat_config(panel, slayer_tasks.target_names(state.task))
    # The orchestrator owns restocks (spec 7.5). Left on, fight_tick teleports to a bank by itself,
    # and the next tick sees the player off-site and safety-stops there (final review).
    cfg["bank"] = {**(cfg.get("bank") or {}), "enabled": False}
    # Aya's monsters drop nothing worth a click and travel dominates a 20-kill task (spec §7.10):
    # only the panel's named whitelist is looted, never "anything over minLootValue".
    cfg["loot"] = {**(cfg.get("loot") or {}), "minLootValue": 10 ** 9}
    ctx.state.config = cfg
    try:
        combat_core.fight_tick(ctx)
    finally:
        ctx.state.config = panel
    data = ctx.state.data
    if data.get("current_target_index") is not None:
        data["slayer_no_target"] = 0
        return
    data["slayer_no_target"] = data.get("slayer_no_target", 0) + 1
    if data["slayer_no_target"] < NO_TARGET_TICKS:
        return
    data["slayer_no_target"] = 0
    walks = data.get("slayer_no_target_walks", 0) + 1
    data["slayer_no_target_walks"] = walks
    site = aya_tasks.get(state.task)
    if site is None or not site.target:
        ctx.state.trigger_safety_stop(f"Slayer: nothing to fight for {state.task} and its site is "
                                      f"not in the catalog -- cannot walk back to it")
        return
    debug_log.log(LOG, f"no target for {NO_TARGET_TICKS} ticks -- walking back to the site tile {site.target}")
    legs.walk_across(ctx, site.target, log=LOG)
    if walks >= NO_TARGET_WALKS:
        ctx.state.trigger_safety_stop(f"Slayer: nothing to fight for {state.task} after "
                                      f"{NO_TARGET_WALKS} walks back to the site")


def tick(ctx):
    state = slayer_state.from_bridge(controller.get_slayer_task())
    if state is None:
        debug_log.log(LOG, "GET /slayer unavailable -- doing nothing")
        return
    tasks_cfg = (ctx.state.config or {}).get("tasks") or {}
    _reset_slot_counters(ctx, state)
    player = controller.get_player()
    phase = derive_phase(state, tasks_cfg, player)
    if phase == ACQUIRE:
        _acquire(ctx, state, tasks_cfg)
    elif phase == SKIP:
        _skip(ctx, state, tasks_cfg)
    elif phase == STOP:
        label = state.task or f"task id {state.task_id} (name did not decode)"
        ctx.state.trigger_safety_stop(f"Slayer: {label} x{state.count} is a task this routine leaves to you")
    elif phase == TRAVEL:
        _travel(ctx, state, player)
    else:
        _fight(ctx, state)
