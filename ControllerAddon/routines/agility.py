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
OBJECT_SCAN_RADIUS = 16      # bounds the bulk scene scan: every candidate is within a few
                             # tiles by construction (we click from the step's own start box)
XP_EVENT_SKILL = "Agility"   # ControllerPlugin.onStatChanged emits Skill.getName()
POST_STEP_DELAY = (0.1, 0.3) # the human's measured 0.2s gap between XP drop and next click

TAG_MAX_ATTEMPTS = 3         # a lost ticket is cheap; a stalled lap is not
DUNGEON_MAX_ATTEMPTS = 4
LADDER_CLIMB_WAIT_S = 6.0
LADDER_NEAR_TILES = 2
TICKET_NAME = "wilderness agility ticket"


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
    """Nearest scene object with the step's id (bulk radius-bounded scan, TickCache-gated --
    the one gated read in this routine, used once per step), else an un-cached /object/at
    lookup around the recorded click tile."""
    objs = controller.get_objects(radius=OBJECT_SCAN_RADIUS) or []
    cands = [o for o in objs if o.get("id") == step.object_id]
    if cands:
        cands.sort(key=lambda o: o.get("distance", 9999))
        return cands[0]
    sx, sy, sp = step.start_tile
    return controller.get_object_at(sx, sy, sp, step.object_id, radius=OBJECT_SEARCH_RADIUS)


def _click_object(ctx, obj, object_id, action, radius=None, label=""):
    """Aimed left-click with a refresh target and hover verification; a withheld click (a
    player camped on the pixel -- common on this course) escalates straight to the right-click
    menu, which cannot be hijacked by whatever is drawn on top. Shared by every object click
    site in this routine, not just the obstacle steps -- see the dungeon ladder."""
    refresh = controller.object_refresh_target(
        obj["x"], obj["y"], obj.get("plane", 0), object_id, radius=radius)
    result = controller.click_entity(
        ctx.ser, obj, offset=(0, 0), refresh_target=refresh, refresh_legs=3,
        expected_action=action)
    if result:
        return True
    if getattr(result, "withheld", False):
        debug_log.log("agility", f"{label}: pixel contested -- using the menu")
        return bool(controller.click_menu_action(
            ctx.ser, obj, action, offset=(0, 0), refresh_target=refresh))
    return False


def _click_step(ctx, step, obj):
    return _click_object(ctx, obj, step.object_id, step.action,
                         radius=step.match_radius or None, label=step.key)


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
    # 12393 is the game's GENERAL busy flag, not this obstacle's: it can already read 1 at
    # click time (the previous obstacle finishing, an eat). Latching saw_busy on that would
    # make the very next 0 -- with the player still short of the end box -- classify as
    # `failed` and re-click mid-obstacle. So saw_busy arms only on an observed 0->1 edge
    # AFTER the click; a busy already at 1 must first be seen to drop. XP stays primary.
    busy_at_click = busy_flag()
    if not _click_step(ctx, step, obj):
        return "no_click"
    debug_log.log("agility", f"{step.key}: clicked {step.action} (xp={xp0}, pending={pending})")

    deadline = _now() + ac.step_budget_s(step, tiles)
    saw_busy = False
    armed = busy_at_click != 1
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
            if _now() >= deadline:
                return "failed" if saw_busy else "no_click"
            continue
        xp_gained = threshold is not None and xp is not None and xp >= threshold
        if busy == 0:
            armed = True
        elif busy == 1 and armed:
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
        if step.confirm == "ticket":
            data["tag_attempts"] = 0
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
        if step.key == "tag" and not ctx.state.config.get("tagDispenser", True):
            step = course.steps[0]
            if not step.start.contains(x, y, plane):
                # Walking past the dispenser to the pipe makes no step progress, so it must
                # NOT reset the no-step counter: an un-walkable goal here would otherwise be
                # an unbounded silent loop (walk, return None, walk, ...) with no ceiling.
                n = data.get("no_step_ticks", 0) + 1
                data["no_step_ticks"] = n
                if n >= NO_STEP_MAX_TICKS:
                    ctx.state.trigger_safety_stop(
                        f"tag disabled but could not reach the {step.key} start from ({x},{y}) for {n} ticks")
                    return None
                sx, sy, sp = step.start_tile
                if not travel.walk_to(ctx, {"x": sx, "y": sy, "plane": sp},
                                      tolerance=1.0, goal_adjacent=False):
                    debug_log.log("agility",
                                  f"tag disabled: no route from ({x},{y}) to the {step.key} start ({sx},{sy})")
                return None
        data["no_step_ticks"] = 0
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


# --- eating ----------------------------------------------------------------------

def maybe_eat(ctx, config) -> bool:
    """Eat between steps only (never inside a step's wait -- an eat mid-obstacle cannot help
    and the click would be dropped). False only when the routine safety-stopped."""
    food = config.get("food") or {}
    if not food.get("enabled"):
        return True
    hp = (controller.get_player() or {}).get("hp")
    if hp is None or hp > food.get("eatAt", 45):
        return True
    if controller.consume_first(ctx.ser, "food"):
        debug_log.log("agility", f"ate food (hp={hp})")
        controller.human_delay(0.3, 0.6)
        return True
    # consume_first returns click_entity's result, so a MISSED or withheld click is falsy
    # too -- it is not proof the food is gone. "Out of food" is decided from the same list
    # consume_first itself reads (controller.py:645-646 and :2676), never from the click.
    if controller.get_consumables().get("food", []):
        debug_log.log("agility", f"food click did not land -- retrying next step (hp={hp})")
        return True
    if hp <= food.get("stopAt", 30):
        ctx.state.trigger_safety_stop(f"out of food at {hp} hp")
        return False
    debug_log.log("agility", f"no food left (hp={hp}) -- continuing until stopAt")
    return True


# --- dispenser Tag ----------------------------------------------------------------

def ticket_count():
    """Tickets held, or None when /inventory could not be read. None is NOT zero: a failed
    read that answered 0 would make the next successful read look like a fresh ticket."""
    inv = controller.get_inventory()
    if not inv:
        return None
    total = 0
    for item in inv:
        if TICKET_NAME in (item.get("name") or "").lower():
            total += int(item.get("qty") or 0)
    return total


def run_tag(ctx, step, obj) -> str:
    """Tag the dispenser; success = the ticket stack grew (inventory is un-gated). The chat
    line is only an accelerator. An unreadable baseline means we cannot confirm anything:
    still click (the tag is worth attempting), but never declare success on a guess."""
    before = ticket_count()
    if ctx.events is not None:
        ctx.events.clear()
    x, y, _, _ = player_tile()
    tiles = ac.chebyshev((x, y), (obj["x"], obj["y"])) if x is not None else 0
    if not _click_step(ctx, step, obj):
        return "no_click"
    deadline = _now() + ac.step_budget_s(step, tiles)
    use_events = ctx.events is not None
    while _now() < deadline:
        if use_events:
            hit = ctx.events.wait_for_event(
                "CHAT_MESSAGE",
                match=lambda p: "agility ticket" in (p.get("message") or "").lower(),
                timeout_s=POLL_S)
            if hit is not None:
                use_events = False
        else:
            _sleep(POLL_S)
        now_count = ticket_count()
        if before is not None and now_count is not None and now_count > before:
            debug_log.log("agility", f"tagged the dispenser: tickets {before} -> {now_count}")
            return "success"
    return "failed"


def _handle_tag_failure(ctx, course, step) -> bool:
    # Its OWN counter, not `attempts`: a tag failure is cheap and must not spend the
    # obstacle budget (nor be spent BY it -- an `attempts` left at the ceiling by a `fell`
    # on the previous lap would otherwise skip the tag on its very first try).
    data = ctx.state.data
    n = data.get("tag_attempts", 0) + 1
    data["tag_attempts"] = n
    if n < TAG_MAX_ATTEMPTS:
        debug_log.log("agility", f"tag did not confirm -- retrying ({n}/{TAG_MAX_ATTEMPTS})")
        return True
    # Give the ticket up rather than the lap: walk onto the pipe's start box so
    # step_for_position resolves to the pipe instead of the dispenser again.
    first = course.steps[0]
    sx, sy, sp = first.start_tile
    debug_log.log("agility", f"tag failed {n} times -- skipping it this lap")
    if travel.walk_to(ctx, {"x": sx, "y": sy, "plane": sp}, tolerance=1.0, goal_adjacent=False):
        data["tag_attempts"] = 0
    else:
        # No route: leave the counter standing so the ceiling keeps being reached (and this
        # keeps being logged) rather than resetting into a silent loop.
        debug_log.log("agility", "no route from the dispenser to the pipe start")
    return True


# --- dungeon recovery -------------------------------------------------------------

def dungeon_recovery(ctx, course) -> None:
    """Fell off the ropeswing/log: climb the pinned dungeon ladder, then walk back to the
    failed step's start tile (default: the first pit obstacle, for a restart that lost
    fell_step). One attempt per call; DUNGEON_MAX_ATTEMPTS then safety-stop."""
    data = ctx.state.data
    n = data.get("dungeon_attempts", 0) + 1
    data["dungeon_attempts"] = n
    if n > DUNGEON_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(f"could not leave the dungeon after {n - 1} attempts")
        return
    lad = course.pit_ladder
    if lad is None:
        ctx.state.trigger_safety_stop("in a pit with no ladder defined for this course")
        return
    x, y, plane, _ = player_tile()
    if x is None:
        return
    if ac.chebyshev((x, y), (lad["x"], lad["y"])) > LADDER_NEAR_TILES:
        debug_log.log("agility", f"in the dungeon at ({x},{y}) -- walking to the ladder")
        if not travel.walk_to(ctx, {"x": lad["x"], "y": lad["y"], "plane": lad["plane"]},
                              tolerance=1.5, goal_adjacent=True):
            debug_log.log("agility", "no route to the dungeon ladder")
            return
    obj = controller.get_object_at(lad["x"], lad["y"], lad["plane"], lad["id"], radius=2)
    if obj is None:
        debug_log.log("agility", f"ladder {lad['id']} not visible at ({lad['x']},{lad['y']})")
        return
    if not _click_object(ctx, obj, lad["id"], lad["action"], radius=2, label="ladder"):
        return
    deadline = _now() + LADDER_CLIMB_WAIT_S
    while _now() < deadline:
        _sleep(POLL_S)
        px, py, pplane, _ = player_tile()
        if px is None:
            continue
        if not ac.in_pit(course, px, py, pplane):
            data["dungeon_attempts"] = 0
            back = course.step_by_key(data.get("fell_step")) or course.first_pit_step() or course.steps[0]
            sx, sy, sp = back.start_tile
            debug_log.log("agility", f"climbed out at ({px},{py}) -- walking back to {back.key} start ({sx},{sy})")
            travel.walk_to(ctx, {"x": sx, "y": sy, "plane": sp}, tolerance=1.0, goal_adjacent=False)
            return
    debug_log.log("agility", f"still in the dungeon {LADDER_CLIMB_WAIT_S}s after the climb click ({n}/{DUNGEON_MAX_ATTEMPTS})")


# --- routine contract -------------------------------------------------------------

def setup(ctx):
    # CONFIG IS NOT AVAILABLE HERE: the engine calls setup() before resolve_config()
    # populates ctx.state.config (see tormented_demon.py/vale_totems.py's own setup()
    # comments for the same gap) -- resolve the same way get_config() will instead.
    config = get_config(None)
    course = ac.course_by_key(config.get("course"))
    if course is None:
        ctx.state.trigger_safety_stop(f"unknown course {config.get('course')!r}")
        return
    debug_log.log("agility", f"course: {course.label}; steps: "
                             + " > ".join(s.key for s in course.all_steps()))


def tick(ctx):
    config = ctx.state.config
    course = ac.course_by_key(config.get("course"))
    if course is None:
        ctx.state.trigger_safety_stop(f"unknown course {config.get('course')!r}")
        return
    for _ in range(LAP_STEPS_PER_TICK):
        if ctx.state.safety_stopped:
            return
        # Re-checked at the TOP of every chained iteration, not once per tick: a step that
        # ends in the dungeon (or off the course entirely) must be caught before the next
        # step is resolved and clicked, not a whole lap later.
        x, y, plane, _ = player_tile()
        if x is None:
            return
        if ac.in_pit(course, x, y, plane):
            dungeon_recovery(ctx, course)
            return
        if not ac.in_zone(course, x, y, plane):
            ctx.state.trigger_safety_stop(f"outside the course at ({x},{y},{plane})")
            return
        if not maybe_eat(ctx, config):
            return
        step = resolve_step(ctx, course)
        if step is None:
            return
        outcome = run_step(ctx, course, step)
        keep_going = handle_outcome(ctx, course, step, outcome)
        # Chain ONLY on a clean pass. Any other outcome has had its bookkeeping/walk-back
        # done above; hand back so the engine loop (safety checks, PLAYER_DIED, breaks)
        # gets its turn before we click again.
        if not keep_going or outcome != "success":
            return
