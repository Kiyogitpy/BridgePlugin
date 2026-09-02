# routines/agility.py
#
# Generic agility-course lap runner. Course geometry, ids and per-step timings live in
# agility_courses.py (pure data); this file is the engine: resolve which step to click from
# the player's tile, click it with a refresh target, poll UN-GATED signals (Agility XP from
# /api/skills/skills, the game's own `busy` varbit 12393, the player tile) at 100ms until the
# step classifies, then carry straight on into the next step inside the same tick.
#
# Design: docs/superpowers/specs/2026-09-02-agility-runner-design.md
# Built from recordings/2026-09-02_032621_wilderness-agility (one human lap) plus live reads
# the same day. The human clicks the next obstacle ~0.2s after the previous XP drop -- that
# cadence, not the engine's ~1.1s loop, is what LAP_STEPS_PER_TICK chaining reproduces.
#
# Run with:
#     python controller.py routines/agility.py
#
# Nothing load-bearing lives in ctx.state.data: the step is re-derived from the player's tile
# every time (agility_courses.step_for_position), so restarts and fall recoveries resume
# correctly. data holds only counters (attempts, ceilings) and pending_xp.

import time

import controller
import debug_log
import travel

import agility_courses as ac

DEFAULT_CONFIG = {
    "course": "wilderness",
    "tagDispenser": True,
    "food": {"enabled": True, "eatAt": 45, "stopAt": 30},

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "stopOnInventoryFull": False,   # loadout is 28/28 by design; tickets stack
        "maxRuntimeHours": 0,
    },
    # User decision 2026-09-02: the course is crowded; the engine's flee-on-any-player check
    # would stop the run on every passer-by.
    "pvpSafety": {"fleeEnabled": False},
}

POLL_S = 0.1                 # every signal polled here is un-gated (spec "Signals")
STEP_MAX_ATTEMPTS = 8        # ~92% per-attempt success on the pit obstacles at 75 Agility
NO_OBJECT_MAX_TICKS = 10
NO_STEP_MAX_TICKS = 10
LAP_STEPS_PER_TICK = 6       # one full lap (5 obstacles + Tag) chained inside one tick
OBJECT_SEARCH_RADIUS = 8     # /object/at fallback radius when the bulk scan misses the id
XP_EVENT_SKILL = "Agility"   # ControllerPlugin.onStatChanged emits Skill.getName()
POST_STEP_DELAY = (0.1, 0.3) # the human's measured 0.2s gap between XP drop and next click


def get_config(bridge_config):
    return DEFAULT_CONFIG


# wrapped so tests can drive a fake clock without patching the global time module
def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


# --- signals (all un-gated) -------------------------------------------------------

def agility_xp():
    snap = controller.get_api("skills", "skills")
    if not snap or "error" in snap:
        return None
    return ((snap.get("skills") or {}).get("AGILITY") or {}).get("experience")


def busy_flag():
    return controller.get_varbit(ac.BUSY_VARBIT)


def player_tile():
    p = controller.get_player() or {}
    return p.get("x"), p.get("y"), p.get("plane"), p


def _route_len(a, b):
    """Offline route length between two (x, y, plane) tiles, or None when unroutable."""
    import worldmap
    path = worldmap.find_path(a[0], a[1], a[2], b[0], b[1])
    return None if path is None else len(path)


# --- locating and clicking --------------------------------------------------------

def locate_object(step):
    """Nearest scene object with the step's id (bulk interactable scan, TickCache-gated --
    the one gated read in this routine, used once per step), else an un-cached /object/at
    lookup around the recorded click tile."""
    objs = controller.get_objects(interactable=True) or []
    cands = [o for o in objs if o.get("id") == step.object_id]
    if cands:
        cands.sort(key=lambda o: o.get("distance", 9999))
        return cands[0]
    sx, sy, sp = step.start_tile
    return controller.get_object_at(sx, sy, sp, step.object_id, radius=OBJECT_SEARCH_RADIUS)


def _click_step(ctx, step, obj):
    """Aimed left-click with a refresh target and hover verification; a withheld click (a
    player camped on the pixel -- common on this course) escalates straight to the right-click
    menu, which cannot be hijacked by whatever is drawn on top."""
    refresh = controller.object_refresh_target(
        obj["x"], obj["y"], obj.get("plane", 0), step.object_id,
        radius=step.match_radius or None)
    result = controller.click_entity(
        ctx.ser, obj, offset=(0, 0), refresh_target=refresh, refresh_legs=3,
        expected_action=step.action)
    if result:
        return True
    if getattr(result, "withheld", False):
        debug_log.log("agility", f"{step.key}: pixel contested -- using the menu")
        return bool(controller.click_menu_action(
            ctx.ser, obj, step.action, offset=(0, 0), refresh_target=refresh))
    return False


# --- the step loop ----------------------------------------------------------------

def run_step(ctx, course, step):
    """Click one step and poll until it classifies. Returns one of
    success | fell | failed | no_click | no_object."""
    obj = locate_object(step)
    if obj is None:
        return "no_object"
    if step.confirm == "ticket":
        return run_tag(ctx, step, obj)

    data = ctx.state.data
    xp0 = agility_xp()
    pending = float(data.get("pending_xp", 0.0) or 0.0)
    threshold = ac.xp_threshold(step, xp0, pending) if xp0 is not None else None

    if ctx.events is not None:
        ctx.events.clear()
    x, y, _, _ = player_tile()
    tiles = ac.chebyshev((x, y), step.start_tile) if x is not None else 0
    if not _click_step(ctx, step, obj):
        return "no_click"
    debug_log.log("agility", f"{step.key}: clicked {step.action} (xp={xp0}, pending={pending})")

    deadline = _now() + ac.step_budget_s(step, tiles)
    saw_busy = False
    use_events = ctx.events is not None
    while True:
        if use_events:
            hit = ctx.events.wait_for_event(
                "XP_GAINED", match=lambda p: p.get("skill") == XP_EVENT_SKILL,
                timeout_s=POLL_S)
            if hit is not None:
                use_events = False     # wait_for_event does not consume: never re-arm
        else:
            _sleep(POLL_S)
        xp = agility_xp()
        busy = busy_flag()
        px, py, pplane, _ = player_tile()
        if px is None:
            continue
        xp_gained = threshold is not None and xp is not None and xp >= threshold
        if busy == 1:
            saw_busy = True
        outcome = ac.classify(
            xp_gained=xp_gained, saw_busy=saw_busy, busy_now=busy,
            in_end=step.end.contains(px, py, pplane),
            in_pit=ac.in_pit(course, px, py, pplane),
            timed_out=_now() >= deadline)
        if outcome == "pending":
            continue
        if outcome == "success":
            # confirmed by position before the XP landed: carry it so the late drop cannot
            # satisfy the NEXT step (swing and stones are both 20). Rocks' XP is conditional
            # (0 on a forfeited lap), so it is never pended.
            data["pending_xp"] = 0.0 if (xp_gained or not step.xp_certain) else step.xp
        debug_log.log("agility", f"{step.key}: {outcome} (xp {xp0}->{xp}, busy_seen={saw_busy})")
        return outcome


def handle_outcome(ctx, course, step, outcome) -> bool:
    """Bookkeeping + recovery for one run_step result. True = keep chaining this tick."""
    data = ctx.state.data
    if outcome == "success":
        data["attempts"] = 0
        data["no_object_ticks"] = 0
        controller.human_delay(*POST_STEP_DELAY)
        return True
    if outcome == "fell":
        data["fell_step"] = step.key
        data["attempts"] = data.get("attempts", 0) + 1
        debug_log.log("agility", f"{step.key}: fell into the dungeon -- recovering next tick")
        return False
    if outcome == "no_object":
        n = data.get("no_object_ticks", 0) + 1
        data["no_object_ticks"] = n
        if n >= NO_OBJECT_MAX_TICKS:
            ctx.state.trigger_safety_stop(f"{step.key}: object {step.object_id} not visible for {n} ticks")
        return False
    # failed / no_click
    if step.confirm == "ticket":
        return _handle_tag_failure(ctx, course, step)
    n = data.get("attempts", 0) + 1
    data["attempts"] = n
    if n >= STEP_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(f"{step.key} failed {n} times in a row")
        return False
    x, y, plane, _ = player_tile()
    if x is not None and not step.start.contains(x, y, plane):
        sx, sy, sp = step.start_tile
        debug_log.log("agility", f"{step.key}: {outcome} -- walking back to ({sx},{sy})")
        travel.walk_to(ctx, {"x": sx, "y": sy, "plane": sp}, tolerance=1.0, goal_adjacent=False)
    else:
        debug_log.log("agility", f"{step.key}: {outcome} -- retrying ({n}/{STEP_MAX_ATTEMPTS})")
    return True


def resolve_step(ctx, course):
    """The step to click next, from the player's tile. Off every start box (mid-course grass
    after a resync): walk to the nearest routable start tile and let the next call resolve."""
    data = ctx.state.data
    x, y, plane, _ = player_tile()
    if x is None:
        return None
    step = ac.step_for_position(course, x, y, plane)
    if step is not None:
        data["no_step_ticks"] = 0
        if step.key == "tag" and not ctx.state.config.get("tagDispenser", True):
            step = course.steps[0]
            if not step.start.contains(x, y, plane):
                sx, sy, sp = step.start_tile
                travel.walk_to(ctx, {"x": sx, "y": sy, "plane": sp}, tolerance=1.0, goal_adjacent=False)
                return None
        return step
    n = data.get("no_step_ticks", 0) + 1
    data["no_step_ticks"] = n
    if n >= NO_STEP_MAX_TICKS:
        ctx.state.trigger_safety_stop(f"no obstacle start box reached from ({x},{y}) for {n} ticks")
        return None
    best, best_len = None, None
    for s in course.all_steps():
        length = _route_len((x, y, plane), s.start_tile)
        if length is not None and (best_len is None or length < best_len):
            best, best_len = s, length
    if best is None:
        debug_log.log("agility", f"no route from ({x},{y}) to any step start")
        return None
    sx, sy, sp = best.start_tile
    debug_log.log("agility", f"off every start box at ({x},{y}) -- walking to {best.key} start ({sx},{sy})")
    travel.walk_to(ctx, {"x": sx, "y": sy, "plane": sp}, tolerance=1.0, goal_adjacent=False)
    return None


# --- Task 3 fills these in (stubs keep tick() complete) ---------------------------

def maybe_eat(ctx, config) -> bool:
    return True


def run_tag(ctx, step, obj) -> str:
    return "failed"


def _handle_tag_failure(ctx, course, step) -> bool:
    return False


def dungeon_recovery(ctx, course) -> None:
    return None


# --- routine contract -------------------------------------------------------------

def setup(ctx):
    course = ac.course_by_key(ctx.state.config.get("course"))
    if course is None:
        ctx.state.trigger_safety_stop(f"unknown course {ctx.state.config.get('course')!r}")
        return
    debug_log.log("agility", f"course: {course.label}; steps: "
                             + " > ".join(s.key for s in course.all_steps()))


def tick(ctx):
    config = ctx.state.config
    course = ac.course_by_key(config.get("course"))
    if course is None:
        ctx.state.trigger_safety_stop(f"unknown course {config.get('course')!r}")
        return
    x, y, plane, _ = player_tile()
    if x is None:
        return
    if ac.in_pit(course, x, y, plane):
        dungeon_recovery(ctx, course)
        return
    if not ac.in_zone(course, x, y, plane):
        ctx.state.trigger_safety_stop(f"outside the course at ({x},{y},{plane})")
        return

    for _ in range(LAP_STEPS_PER_TICK):
        if ctx.state.safety_stopped:
            return
        if not maybe_eat(ctx, config):
            return
        step = resolve_step(ctx, course)
        if step is None:
            return
        outcome = run_step(ctx, course, step)
        if not handle_outcome(ctx, course, step, outcome):
            return
