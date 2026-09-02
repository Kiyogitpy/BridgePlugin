# Agility Course Runner (Python) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A generic lap engine (`routines/agility.py`) driven by per-course data (`agility_courses.py`) that runs the Wilderness Agility Course unattended: obstacle → obstacle on the XP drop, Tag the dispenser each lap, eat between obstacles, recover from a fall into the dungeon.

**Architecture:** `ControllerAddon/agility_courses.py` is a zero-import data module: `Box`/`Step`/`Course` dataclasses, the WILDERNESS course (every tile from the 2026-09-02 recording and live reads), and pure predicates (`step_for_position`, `classify`, budgets). `ControllerAddon/routines/agility.py` is the engine: resolve the step from the player's tile, click with a refresh target, poll un-gated signals (Agility XP via `/api/skills/skills`, `busy` varbit 12393, `/player` tile) at 100ms until the step classifies, chain straight into the next step. Nothing load-bearing lives in `ctx.state.data`.

**Tech Stack:** Python 3.13, pytest, the existing `controller.py` bridge helpers (`get_api`, `get_varbit`, `get_player`, `get_objects`, `get_object_at`, `object_refresh_target`, `click_entity`, `click_menu_action`, `consume_first`), `travel.walk_to`, `bridge_events.EventStream`.

**Spec:** `docs/superpowers/specs/2026-09-02-agility-runner-design.md`

## Global Constraints

- All tests run from `ControllerAddon/`: `python -m pytest tests/test_agility.py -q`. The suite must stay hermetic: every `controller.*`/`travel.*` call the engine makes is monkeypatched in `tests/test_agility.py` (autouse fixture), same pattern as `tests/test_mahogany_homes.py`.
- `agility_courses.py` imports NOTHING from this repo (only `dataclasses`, `math`). It must be importable and testable without `controller`.
- Signals are un-gated: Agility XP from `controller.get_api("skills", "skills")["skills"]["AGILITY"]["experience"]`, busy from `controller.get_varbit(12393)`, tile from `controller.get_player()`. The bulk `controller.get_objects(interactable=True)` scan is used ONLY to locate an obstacle before the click.
- Every loop has a ceiling that calls `ctx.state.trigger_safety_stop(reason)` with a reason that does NOT end in a period (the engine appends its own suffix).
- `DEFAULT_CONFIG["pvpSafety"]["fleeEnabled"]` is `False` and `["safety"]["stopOnInventoryFull"]` is `False` (user decisions, spec "Decisions").
- The engine must tolerate `ctx.events is None` (fall back to `_sleep(POLL_S)`), and after an `XP_GAINED` event matches once it must stop calling `wait_for_event` for the rest of that step (`wait_for_event` does not consume, so it would return instantly forever — a busy spin).
- Before every relaunch: `python -m py_compile` on every touched file, then the full suite `python -m pytest tests/ -q` (CLAUDE.md live-debugging workflow, step 5).
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File Structure

- Create: `ControllerAddon/agility_courses.py` — dataclasses, WILDERNESS course data, pure predicates. One responsibility: describe courses and answer pure questions about tiles/signals.
- Create: `ControllerAddon/routines/agility.py` — the engine (`DEFAULT_CONFIG`, `get_config`, `setup`, `tick`, `run_step`, `run_tag`, `dungeon_recovery`, `maybe_eat`).
- Create: `ControllerAddon/tests/test_agility.py` — course-data pins, predicate tests, engine tests against a scripted fake controller.
- Modify: `src/main/java/com/example/controller/RoutineChoice.java` (one enum entry), `ControllerAddon/controller.py` (`ROUTINE_FILES`), `CLAUDE.md` (routine section).

---

### Task 1: `agility_courses.py` — data model, wilderness course, pure predicates

**Files:**
- Create: `ControllerAddon/agility_courses.py`
- Test: `ControllerAddon/tests/test_agility.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used verbatim by Task 2/3):
  - `Box(x1, y1, x2, y2, plane=0)` with `.contains(x, y, plane=0) -> bool`
  - `Step(key, name, object_id, action, start: Box, start_tile: tuple[int,int,int], end: Box, xp: float, fail_mode: str, duration_s: float, match_radius: int = 0, confirm: str = "obstacle", xp_certain: bool = True)`
  - `Course(key, label, min_agility, zone: tuple[Box,...], steps: tuple[Step,...], tag: Step | None, pit: Box | None, pit_ladder: dict | None)` with `.all_steps() -> tuple[Step,...]` (steps + tag), `.step_by_key(key) -> Step | None`, `.next_step(step) -> Step`, `.first_pit_step() -> Step | None`
  - `WILDERNESS: Course`, `COURSES: dict[str, Course]`, `course_by_key(key) -> Course | None`
  - `in_zone(course, x, y, plane) -> bool`, `in_pit(course, x, y, plane) -> bool`
  - `step_for_position(course, x, y, plane) -> Step | None`
  - `xp_threshold(step, baseline: float, pending: float) -> float`
  - `step_budget_s(step, tiles: float) -> float`
  - `classify(*, xp_gained, saw_busy, busy_now, in_end, in_pit, timed_out) -> str` in `{"success","fell","failed","no_click","pending"}`
  - `chebyshev(a, b) -> int`
  - Constants: `DUNGEON_Y_MIN = 6400`, `BUSY_VARBIT = 12393`, `BUDGET_WALK_S_PER_TILE = 0.6`, `BUDGET_MULTIPLIER = 2.0`, `BUDGET_CAP_S = 30.0`

- [ ] **Step 1: Write the failing tests (course data + predicates)**

Create `ControllerAddon/tests/test_agility.py`:

```python
import os
import sys
from itertools import combinations

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import agility_courses as ac

W = ac.WILDERNESS


# --- course data pinned to recordings/2026-09-02_032621_wilderness-agility ---------

def test_wilderness_step_order_ids_and_actions():
    assert [s.key for s in W.steps] == ["pipe", "ropeswing", "stones", "log", "rocks"]
    assert [s.object_id for s in W.steps] == [23137, 23132, 23556, 23542, 23640]
    assert [s.action for s in W.steps] == [
        "Squeeze-through", "Swing-on", "Cross", "Walk-across", "Climb"]
    assert W.tag is not None and W.tag.object_id == 53224 and W.tag.action == "Tag"
    assert W.tag.confirm == "ticket"
    assert [s.fail_mode for s in W.steps] == ["none", "pit", "knockback", "pit", "none"]
    assert W.steps[4].xp_certain is False          # rocks: 498.9 only on a clean lap


def test_wilderness_pit_ladder_pinned_live_2026_09_02():
    assert W.pit_ladder == {"id": 17385, "x": 3005, "y": 10363, "plane": 0,
                            "action": "Climb-up"}
    assert W.first_pit_step().key == "ropeswing"


def test_start_boxes_are_pairwise_disjoint():
    steps = W.all_steps()
    for a, b in combinations(steps, 2):
        overlap = (a.start.x1 <= b.start.x2 and b.start.x1 <= a.start.x2
                   and a.start.y1 <= b.start.y2 and b.start.y1 <= a.start.y2)
        assert not overlap, f"{a.key} start box overlaps {b.key}"


@pytest.mark.parametrize("tile,key", [
    ((3004, 3936), "pipe"),        # recorded Squeeze-through click tile
    ((3004, 3950), "ropeswing"),   # recorded Swing-on click tile
    ((3004, 3949), "ropeswing"),   # pipe exit tile: the swing is next
    ((3005, 3958), "stones"),      # recorded Cross click tile
    ((2996, 3960), "log"),         # recorded Walk-across click tile
    ((2996, 3945), "rocks"),       # recorded Climb click tile
    ((2994, 3945), "rocks"),       # second recorded Climb click tile
    ((2995, 3933), "tag"),         # recorded Tag click tile
    ((2996, 3933), "tag"),
])
def test_step_for_position_matches_recorded_click_tiles(tile, key):
    step = ac.step_for_position(W, tile[0], tile[1], 0)
    assert step is not None and step.key == key


def test_every_end_box_lies_inside_the_next_start_box():
    steps = W.all_steps()
    for i, s in enumerate(steps):
        nxt = steps[(i + 1) % len(steps)]
        assert W.next_step(s) is nxt
        for x in range(s.end.x1, s.end.x2 + 1):
            for y in range(s.end.y1, s.end.y2 + 1):
                assert nxt.start.contains(x, y, 0), f"{s.key} end ({x},{y}) not in {nxt.key} start"


def test_zone_and_pit():
    assert ac.in_zone(W, 2996, 3945, 0)
    assert ac.in_zone(W, 3004, 3936, 0)
    assert not ac.in_zone(W, 2998, 3916, 0)        # south of the entrance Door
    assert not ac.in_zone(W, 3005, 10362, 0)       # dungeon
    assert ac.in_pit(W, 3005, 10362, 0)            # live tile beside ladder 17385
    assert not ac.in_pit(W, 3005, 3962, 0)
    assert not ac.in_zone(W, 2996, 3945, 1)        # wrong plane


def test_step_for_position_none_off_course():
    assert ac.step_for_position(W, 2998, 3916, 0) is None
    assert ac.step_for_position(W, 3005, 10362, 0) is None


# --- pure helpers ----------------------------------------------------------------

def test_xp_threshold_accounts_for_fractional_and_pending_xp():
    pipe, swing = W.steps[0], W.steps[1]
    assert ac.xp_threshold(pipe, 1000.0, 0.0) == pytest.approx(1011.5)
    # pipe XP still in flight when the swing is clicked: 12.5 pending + 20 - 1
    assert ac.xp_threshold(swing, 1000.0, 12.5) == pytest.approx(1031.5)


def test_step_budget_scales_with_walk_and_caps():
    log = W.steps[3]                              # 10.0s measured
    assert ac.step_budget_s(log, 0) == pytest.approx(20.0)
    assert ac.step_budget_s(log, 5) == pytest.approx(23.0)
    assert ac.step_budget_s(log, 100) == pytest.approx(ac.BUDGET_CAP_S)


@pytest.mark.parametrize("kw,expected", [
    (dict(xp_gained=True, saw_busy=False, busy_now=0, in_end=False, in_pit=False, timed_out=False), "success"),
    (dict(xp_gained=False, saw_busy=True, busy_now=0, in_end=True, in_pit=False, timed_out=False), "success"),
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=False, in_pit=True, timed_out=False), "fell"),
    (dict(xp_gained=False, saw_busy=False, busy_now=0, in_end=False, in_pit=True, timed_out=False), "fell"),
    (dict(xp_gained=False, saw_busy=True, busy_now=0, in_end=False, in_pit=False, timed_out=False), "failed"),
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=False, in_pit=False, timed_out=False), "pending"),
    (dict(xp_gained=False, saw_busy=False, busy_now=0, in_end=False, in_pit=False, timed_out=False), "pending"),
    (dict(xp_gained=False, saw_busy=False, busy_now=0, in_end=False, in_pit=False, timed_out=True), "no_click"),
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=False, in_pit=False, timed_out=True), "failed"),
    # busy still 1 but the player is already in the end box with no XP yet: keep waiting
    (dict(xp_gained=False, saw_busy=True, busy_now=1, in_end=True, in_pit=False, timed_out=False), "pending"),
])
def test_classify_truth_table(kw, expected):
    assert ac.classify(**kw) == expected


def test_chebyshev():
    assert ac.chebyshev((3004, 3936), (3004, 3949)) == 13
    assert ac.chebyshev((2996, 3945), (3005, 3936)) == 9
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `ControllerAddon/`): `python -m pytest tests/test_agility.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'agility_courses'`.

- [ ] **Step 3: Write `agility_courses.py`**

```python
# agility_courses.py
#
# Course data + pure predicates for routines/agility.py. ZERO repo imports on purpose:
# this module describes courses and answers pure questions (which step am I on, did the
# step succeed) so it can be tested without controller.py.
#
# WILDERNESS is pinned to recordings/2026-09-02_032621_wilderness-agility (one human lap)
# and to live bridge reads the same day (object ids/tiles, dungeon ladder). See
# docs/superpowers/specs/2026-09-02-agility-runner-design.md for the derivation of every box.

from dataclasses import dataclass

DUNGEON_Y_MIN = 6400            # OSRS underground areas live at map-y + 6400
BUSY_VARBIT = 12393             # cache config/varbit 12393 "busy": 1 while an obstacle animates
BUDGET_MULTIPLIER = 2.0         # budget = 2 x measured duration ...
BUDGET_WALK_S_PER_TILE = 0.6    # ... + one game tick per tile the click has to walk first
BUDGET_CAP_S = 30.0


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int
    plane: int = 0

    def contains(self, x, y, plane=0) -> bool:
        return (plane == self.plane and self.x1 <= x <= self.x2
                and self.y1 <= y <= self.y2)


@dataclass(frozen=True)
class Step:
    key: str
    name: str                 # object name, lowercase (diagnostic cross-check only)
    object_id: int
    action: str               # menu option; also click_entity's expected_action
    start: Box                # where this step is clicked FROM -- resync + recovery goal
    start_tile: tuple         # (x, y, plane): the recorded click tile, the walk goal
    end: Box                  # where a successful pass leaves the player
    xp: float
    fail_mode: str            # "none" | "knockback" | "pit"
    duration_s: float         # measured click -> done
    match_radius: int = 0     # /object/at radius for multi-tile objects
    confirm: str = "obstacle" # "obstacle" (xp / busy / end box) | "ticket" (inventory count)
    xp_certain: bool = True   # False: XP may legitimately be 0 (rocks on a forfeited lap)


@dataclass(frozen=True)
class Course:
    key: str
    label: str
    min_agility: int
    zone: tuple
    steps: tuple
    tag: object = None        # Step | None -- dispenser Tag, runs after the last step
    pit: object = None        # Box | None -- dungeon zone
    pit_ladder: object = None # dict(id, x, y, plane, action) | None

    def all_steps(self) -> tuple:
        return self.steps + ((self.tag,) if self.tag is not None else ())

    def step_by_key(self, key):
        for s in self.all_steps():
            if s.key == key:
                return s
        return None

    def next_step(self, step):
        steps = self.all_steps()
        for i, s in enumerate(steps):
            if s is step or s.key == step.key:
                return steps[(i + 1) % len(steps)]
        return steps[0]

    def first_pit_step(self):
        for s in self.steps:
            if s.fail_mode == "pit":
                return s
        return None


# --- Wilderness Agility Course ----------------------------------------------------
# Boxes: START = the tiles a step is clicked from (pairwise disjoint, tested);
# END = where a successful pass lands (always inside the NEXT step's start box, tested).

WILDERNESS = Course(
    key="wilderness",
    label="Wilderness Agility Course",
    min_agility=52,
    zone=(Box(2988, 3932, 3010, 3966),),
    steps=(
        Step("pipe", "obstacle pipe", 23137, "Squeeze-through",
             start=Box(3001, 3933, 3007, 3938), start_tile=(3004, 3936, 0),
             end=Box(3002, 3947, 3006, 3949), xp=12.5, fail_mode="none",
             duration_s=6.4),
        Step("ropeswing", "ropeswing", 23132, "Swing-on",
             start=Box(3002, 3947, 3006, 3953), start_tile=(3004, 3950, 0),
             end=Box(3003, 3956, 3007, 3959), xp=20.0, fail_mode="pit",
             duration_s=3.7, match_radius=3),
        Step("stones", "stepping stone", 23556, "Cross",
             start=Box(3003, 3956, 3007, 3959), start_tile=(3005, 3958, 0),
             end=Box(2994, 3959, 2999, 3962), xp=20.0, fail_mode="knockback",
             duration_s=9.0, match_radius=2),
        Step("log", "log balance", 23542, "Walk-across",
             start=Box(2994, 3959, 2999, 3962), start_tile=(2996, 3960, 0),
             end=Box(2993, 3941, 2998, 3947), xp=20.0, fail_mode="pit",
             duration_s=10.0, match_radius=6),
        Step("rocks", "rocks", 23640, "Climb",
             start=Box(2993, 3941, 2998, 3947), start_tile=(2996, 3945, 0),
             end=Box(2990, 3930, 3000, 3936), xp=498.9, fail_mode="none",
             duration_s=5.6, match_radius=2, xp_certain=False),
    ),
    tag=Step("tag", "agility dispenser", 53224, "Tag",
             start=Box(2990, 3930, 3000, 3936), start_tile=(2995, 3933, 0),
             end=Box(3001, 3933, 3007, 3938), xp=0.0, fail_mode="none",
             duration_s=2.8, confirm="ticket"),
    pit=Box(2990, 10344, 3010, 10370),
    pit_ladder={"id": 17385, "x": 3005, "y": 10363, "plane": 0, "action": "Climb-up"},
)

COURSES = {WILDERNESS.key: WILDERNESS}


def course_by_key(key):
    return COURSES.get((key or "").lower())


def chebyshev(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def in_zone(course, x, y, plane) -> bool:
    return any(b.contains(x, y, plane) for b in course.zone)


def in_pit(course, x, y, plane) -> bool:
    if course.pit is None:
        return False
    return y >= DUNGEON_Y_MIN and course.pit.contains(x, y, plane)


def step_for_position(course, x, y, plane):
    """The step to click next from this tile (START boxes only, disjoint by construction)."""
    for s in course.all_steps():
        if s.start.contains(x, y, plane):
            return s
    return None


def xp_threshold(step, baseline: float, pending: float) -> float:
    """XP total that proves THIS step landed. -1 absorbs fractional XP (12.5 shows as +12);
    `pending` is the previous step's XP when that step was confirmed by position before its
    XP arrived, so a late drop cannot satisfy this step's check."""
    return baseline + pending + step.xp - 1.0


def step_budget_s(step, tiles: float) -> float:
    return min(BUDGET_CAP_S, BUDGET_MULTIPLIER * step.duration_s + BUDGET_WALK_S_PER_TILE * tiles)


def classify(*, xp_gained, saw_busy, busy_now, in_end, in_pit, timed_out) -> str:
    """Outcome of one obstacle poll. XP wins outright; a fall is a fall even mid-busy; a busy
    1->0 edge decides success (end box) vs failure; a timeout with busy never seen means the
    click never registered."""
    if xp_gained:
        return "success"
    if in_pit:
        return "fell"
    if saw_busy and busy_now == 0:
        return "success" if in_end else "failed"
    if timed_out:
        return "failed" if saw_busy else "no_click"
    return "pending"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_agility.py -q`
Expected: all pass (approximately 30 tests including parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/agility_courses.py ControllerAddon/tests/test_agility.py
git commit -m "agility: course data model + wilderness course pinned to the 2026-09-02 recording

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `routines/agility.py` — engine core (resolve, click, poll, chain)

**Files:**
- Create: `ControllerAddon/routines/agility.py`
- Test: `ControllerAddon/tests/test_agility.py` (append)

**Interfaces:**
- Consumes: everything in Task 1's "Produces"; `controller.get_api`, `controller.get_varbit`, `controller.get_player`, `controller.get_objects(interactable=True)`, `controller.get_object_at(x, y, plane, obj_id, radius=None)`, `controller.object_refresh_target(x, y, plane, obj_id, radius=None)`, `controller.click_entity(ser, entity, offset=, refresh_target=, refresh_legs=, expected_action=)` (falsy `CLICK_WITHHELD` carries `.withheld`), `controller.click_menu_action(ser, entity, option, offset=, refresh_target=)`, `controller.human_delay(a, b)`, `travel.walk_to(ctx, target: dict, tolerance=, goal_adjacent=)`, `ctx.events.clear()/wait_for_event(types, match=, timeout_s=)`.
- Produces (used by Task 3): `DEFAULT_CONFIG`, `get_config`, `tick(ctx)`, `run_step(ctx, course, step) -> str`, `handle_outcome(ctx, course, step, outcome) -> bool`, `resolve_step(ctx, course) -> Step | None`, `locate_object(step) -> dict | None`, `agility_xp() -> float | None`, `busy_flag() -> int | None`, `player_tile() -> (x, y, plane, player)`, `_now()`, `_sleep(s)`, constants `POLL_S`, `STEP_MAX_ATTEMPTS`, `NO_OBJECT_MAX_TICKS`, `NO_STEP_MAX_TICKS`, `LAP_STEPS_PER_TICK`, `XP_EVENT_SKILL`. Task 3 adds `maybe_eat`, `run_tag`, `dungeon_recovery` — Task 2 ships them as stubs that return `True` / `"failed"` / `None` so `tick` is complete from the start.

- [ ] **Step 1: Write the failing engine tests (append to `tests/test_agility.py`)**

```python
# =============================== engine =========================================

import types

import controller
import travel
import agility


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Scene:
    """Scripted bridge. Each call to advance() moves to the next reading; the last reading
    repeats forever. A reading is dict(xp=, busy=, pos=(x,y,plane), hp=)."""

    def __init__(self, readings):
        self.readings = list(readings)
        self.i = 0
        self.clicks = []          # (object_id, action, kind)
        self.walks = []           # target dicts
        self.eats = 0
        self.objects = []
        self.inventory = []

    def cur(self):
        return self.readings[min(self.i, len(self.readings) - 1)]

    def advance(self):
        self.i += 1

    # bridge stand-ins
    def get_api(self, group, reader):
        assert (group, reader) == ("skills", "skills")
        return {"skills": {"AGILITY": {"experience": self.cur()["xp"]}}}

    def get_varbit(self, vid):
        assert vid == ac.BUSY_VARBIT
        return self.cur()["busy"]

    def get_player(self):
        x, y, p = self.cur()["pos"]
        return {"x": x, "y": y, "plane": p, "hp": self.cur().get("hp", 90)}


def _obj(step, x=None, y=None):
    return {"id": step.object_id, "name": step.name.title(), "x": x or step.start_tile[0],
            "y": y or step.start_tile[1] + 3, "plane": 0, "distance": 3,
            "screenX": 500, "screenY": 500, "actions": [step.action]}


@pytest.fixture
def scene(monkeypatch):
    sc = Scene([dict(xp=1000, busy=0, pos=(3004, 3936, 0))])
    clock = FakeClock()
    monkeypatch.setattr(agility, "_now", clock.now)
    # every sleep also advances the scripted scene by one reading
    def _sleep(s):
        clock.sleep(s)
        sc.advance()
    monkeypatch.setattr(agility, "_sleep", _sleep)
    monkeypatch.setattr(controller, "get_api", sc.get_api)
    monkeypatch.setattr(controller, "get_varbit", sc.get_varbit)
    monkeypatch.setattr(controller, "get_player", sc.get_player)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: list(sc.objects))
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, plane, obj_id, radius=None:
                        next((o for o in sc.objects if o["id"] == obj_id), None))
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda x, y, plane, obj_id, radius=None: (lambda: (500, 500)))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: sc.clicks.append(
                            (ent["id"], kw.get("expected_action"), "left")) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **kw: sc.clicks.append(
                            (ent["id"], option, "menu")) or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "consume_first",
                        lambda ser, cat: (sc.__setattr__("eats", sc.eats + 1) or True)
                        if sc.inventory else False)
    # a reading may carry its own "inv" list so a test can script an inventory change mid-wait
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: list(sc.cur().get("inv", sc.inventory)))
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, **kw: sc.walks.append(dict(target)) or True)
    sc.clock = clock
    return sc


def make_ctx():
    state = controller.ControllerState()
    state.config = agility.get_config(None)
    return types.SimpleNamespace(ser=None, state=state, events=None)


def test_default_config_flags():
    cfg = agility.get_config({"anything": 1})
    assert cfg["course"] == "wilderness"
    assert cfg["pvpSafety"]["fleeEnabled"] is False
    assert cfg["safety"]["stopOnInventoryFull"] is False
    assert cfg["safety"]["stopOnDeath"] is True


def test_run_step_success_on_xp(scene):
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert scene.clicks == [(23137, "Squeeze-through", "left")]
    assert ctx.state.data["pending_xp"] == 0.0


def test_run_step_success_on_end_box_sets_pending_xp(scene):
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    # XP never arrives inside the window, but busy drops with the player at the exit
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3942, 0)),
                      dict(xp=1000, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert ctx.state.data["pending_xp"] == pytest.approx(12.5)


def test_pending_xp_blocks_late_drop_from_completing_next_step(scene):
    swing = W.steps[1]
    scene.objects = [_obj(swing)]
    # the pipe's late +12 lands while we wait on the swing; busy never rises; then timeout
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3950, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3950, 0))]
    ctx = make_ctx()
    ctx.state.data["pending_xp"] = 12.5
    assert agility.run_step(ctx, W, swing) == "no_click"


def test_run_step_fell_into_dungeon(scene):
    swing = W.steps[1]
    scene.objects = [_obj(swing)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3950, 0)),
                      dict(xp=1000, busy=1, pos=(3005, 3953, 0)),
                      dict(xp=1000, busy=0, pos=(2998, 10352, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, swing) == "fell"


def test_run_step_knockback_failed(scene):
    stones = W.steps[2]
    scene.objects = [_obj(stones)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3005, 3958, 0)),
                      dict(xp=1000, busy=1, pos=(3003, 3960, 0)),
                      dict(xp=1000, busy=0, pos=(3005, 3958, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, stones) == "failed"


def test_run_step_rocks_forfeited_lap_completes_on_end_box_without_pending(scene):
    rocks = W.steps[4]
    scene.objects = [_obj(rocks)]
    scene.readings = [dict(xp=1000, busy=0, pos=(2996, 3945, 0)),
                      dict(xp=1000, busy=1, pos=(2995, 3940, 0)),
                      dict(xp=1000, busy=0, pos=(2995, 3933, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, rocks) == "success"
    assert ctx.state.data["pending_xp"] == 0.0        # xp_certain False: never pend 498.9


def test_run_step_no_object(scene):
    ctx = make_ctx()
    scene.objects = []
    assert agility.run_step(ctx, W, W.steps[0]) == "no_object"
    assert scene.clicks == []


def test_withheld_click_escalates_to_menu(scene, monkeypatch):
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: scene.clicks.append((ent["id"], "withheld", "left"))
                        or controller.CLICK_WITHHELD)
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert scene.clicks[-1] == (23137, "Squeeze-through", "menu")


def test_tick_chains_steps_within_one_tick(scene):
    """pipe done -> swing clicked in the SAME tick, no engine-loop latency in between."""
    pipe, swing = W.steps[0], W.steps[1]
    scene.objects = [_obj(pipe), _obj(swing)]
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),   # resolve: pipe
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0)),   # pipe success
                      dict(xp=1012, busy=1, pos=(3005, 3953, 0)),   # swing in progress
                      dict(xp=1032, busy=0, pos=(3005, 3958, 0))]   # swing success
    ctx = make_ctx()
    agility.tick(ctx)
    ids = [c[0] for c in scene.clicks]
    assert ids[:2] == [23137, 23132]


def test_tick_outside_course_safety_stops(scene):
    scene.readings = [dict(xp=1000, busy=0, pos=(2998, 3916, 0))]
    ctx = make_ctx()
    agility.tick(ctx)
    assert ctx.state.safety_stopped and "outside the course" in ctx.state.safety_reason


def test_failed_step_retries_and_ceiling_stops(scene):
    stones = W.steps[2]
    ctx = make_ctx()
    ctx.state.data["attempts"] = agility.STEP_MAX_ATTEMPTS - 1
    assert agility.handle_outcome(ctx, W, stones, "failed") is False
    assert ctx.state.safety_stopped and "stones" in ctx.state.safety_reason


def test_failed_step_walks_back_to_start_when_displaced(scene):
    stones = W.steps[2]
    scene.readings = [dict(xp=1000, busy=0, pos=(3000, 3962, 0))]   # not in stones' start box
    ctx = make_ctx()
    assert agility.handle_outcome(ctx, W, stones, "failed") is True
    assert scene.walks and (scene.walks[-1]["x"], scene.walks[-1]["y"]) == (3005, 3958)
    assert ctx.state.data["attempts"] == 1


def test_no_object_ceiling_stops(scene):
    ctx = make_ctx()
    ctx.state.data["no_object_ticks"] = agility.NO_OBJECT_MAX_TICKS - 1
    assert agility.handle_outcome(ctx, W, W.steps[0], "no_object") is False
    assert ctx.state.safety_stopped


def test_resolve_step_off_box_walks_to_nearest_start(scene, monkeypatch):
    # a tile inside the zone but in no start box (mid-course grass)
    scene.readings = [dict(xp=1000, busy=0, pos=(3000, 3950, 0))]
    monkeypatch.setattr(agility, "_route_len",
                        lambda a, b: ac.chebyshev(a, b))
    ctx = make_ctx()
    assert agility.resolve_step(ctx, W) is None
    assert scene.walks and (scene.walks[-1]["x"], scene.walks[-1]["y"]) == (3004, 3950)
    assert ctx.state.data["no_step_ticks"] == 1


def test_events_used_only_until_first_match(scene):
    """wait_for_event does not consume: after one match the engine must fall back to _sleep."""
    pipe = W.steps[0]
    scene.objects = [_obj(pipe)]
    calls = []

    class Ev:
        def clear(self):
            calls.append("clear")

        def wait_for_event(self, types, match=None, timeout_s=0.1):
            calls.append("wait")
            agility._sleep(timeout_s)
            return {"skill": "Agility", "delta": 12}

    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3940, 0)),
                      dict(xp=1000, busy=1, pos=(3004, 3944, 0)),
                      dict(xp=1012, busy=0, pos=(3004, 3949, 0))]
    ctx = make_ctx()
    ctx.events = Ev()
    assert agility.run_step(ctx, W, pipe) == "success"
    assert calls.count("wait") == 1 and calls[0] == "clear"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_agility.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'agility'`.

- [ ] **Step 3: Write `routines/agility.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_agility.py -q`
Expected: all pass. If `test_tick_chains_steps_within_one_tick` fails on the click order, check that `resolve_step` is called AFTER the previous step's success advanced the scripted position (the scene advances one reading per `_sleep`; the pipe's success reading (3004,3949) sits in the swing's start box).

- [ ] **Step 5: py_compile and commit**

```bash
python -m py_compile routines/agility.py agility_courses.py
git add ControllerAddon/routines/agility.py ControllerAddon/tests/test_agility.py
git commit -m "agility: lap engine -- resolve from tile, click with refresh, poll un-gated signals, chain steps

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: eating, dispenser Tag, dungeon recovery

**Files:**
- Modify: `ControllerAddon/routines/agility.py` (replace the four Task 2 stubs)
- Test: `ControllerAddon/tests/test_agility.py` (append)

**Interfaces:**
- Consumes: Task 2's `player_tile`, `_click_step`, `_now`, `_sleep`, `POLL_S`; `controller.consume_first(ser, "food") -> bool`, `controller.get_inventory() -> list[{"name","qty",...}]`, `controller.get_object_at`, `travel.walk_to`.
- Produces: `maybe_eat(ctx, config) -> bool` (False only when the routine safety-stopped), `run_tag(ctx, step, obj) -> "success" | "failed" | "no_click"`, `_handle_tag_failure(ctx, course, step) -> bool`, `dungeon_recovery(ctx, course) -> None`, `ticket_count() -> int`, constants `TAG_MAX_ATTEMPTS = 3`, `DUNGEON_MAX_ATTEMPTS = 4`, `LADDER_CLIMB_WAIT_S = 6.0`, `TICKET_NAME = "wilderness agility ticket"`.

- [ ] **Step 1: Write the failing tests (append)**

```python
# ============================ eating / tag / dungeon ==============================

def test_maybe_eat_only_below_threshold_and_stops_when_out_of_food(scene):
    ctx = make_ctx()
    cfg = ctx.state.config
    scene.inventory = [{"name": "Blighted karambwan", "qty": 3}]
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0), hp=80)]
    assert agility.maybe_eat(ctx, cfg) is True and scene.eats == 0
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0), hp=40)]
    assert agility.maybe_eat(ctx, cfg) is True and scene.eats == 1
    scene.inventory = []
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0), hp=40)]
    assert agility.maybe_eat(ctx, cfg) is True          # above stopAt: keep going, logged
    scene.readings = [dict(xp=1000, busy=0, pos=(3004, 3936, 0), hp=25)]
    assert agility.maybe_eat(ctx, cfg) is False
    assert ctx.state.safety_stopped and "out of food" in ctx.state.safety_reason


def test_run_tag_confirms_on_ticket_count(scene):
    tag = W.tag
    scene.objects = [_obj(tag, x=3005, y=3936)]
    seven = [{"name": "Wilderness agility ticket", "qty": 7}]
    eight = [{"name": "Wilderness agility ticket", "qty": 8}]
    # the ticket lands on the second poll after the click
    scene.readings = [dict(xp=1000, busy=0, pos=(2995, 3933, 0), inv=seven),
                      dict(xp=1000, busy=0, pos=(3000, 3934, 0), inv=seven),
                      dict(xp=1000, busy=0, pos=(3004, 3935, 0), inv=eight)]
    ctx = make_ctx()
    assert agility.run_tag(ctx, tag, scene.objects[0]) == "success"
    assert scene.clicks == [(53224, "Tag", "left")]


def test_run_tag_times_out_as_failed(scene):
    tag = W.tag
    scene.objects = [_obj(tag, x=3005, y=3936)]
    scene.inventory = [{"name": "Wilderness agility ticket", "qty": 7}]
    scene.readings = [dict(xp=1000, busy=0, pos=(2995, 3933, 0))]
    ctx = make_ctx()
    assert agility.run_tag(ctx, tag, scene.objects[0]) == "failed"


def test_tag_failure_ceiling_walks_on_to_the_pipe_instead_of_stopping(scene):
    ctx = make_ctx()
    ctx.state.data["attempts"] = agility.TAG_MAX_ATTEMPTS - 1
    assert agility.handle_outcome(ctx, W, W.tag, "failed") is True
    assert not ctx.state.safety_stopped
    assert (scene.walks[-1]["x"], scene.walks[-1]["y"]) == (3004, 3936)
    assert ctx.state.data["attempts"] == 0


def test_tag_failure_below_ceiling_retries(scene):
    ctx = make_ctx()
    assert agility.handle_outcome(ctx, W, W.tag, "failed") is True
    assert ctx.state.data["attempts"] == 1 and not scene.walks


def test_dungeon_recovery_climbs_and_walks_back_to_failed_step(scene):
    ladder = {"id": 17385, "name": "Ladder", "x": 3005, "y": 10363, "plane": 0,
              "distance": 1, "screenX": 400, "screenY": 400, "actions": ["Climb-up"]}
    scene.objects = [ladder]
    scene.readings = [dict(xp=1000, busy=0, pos=(3005, 10362, 0)),
                      dict(xp=1000, busy=0, pos=(3005, 10362, 0)),
                      dict(xp=1000, busy=0, pos=(3005, 3962, 0))]     # back on the course
    ctx = make_ctx()
    ctx.state.data["fell_step"] = "log"
    agility.dungeon_recovery(ctx, W)
    assert scene.clicks == [(17385, "Climb-up", "left")]
    assert (scene.walks[-1]["x"], scene.walks[-1]["y"]) == (2996, 3960)
    assert ctx.state.data["dungeon_attempts"] == 0


def test_dungeon_recovery_walks_to_ladder_when_far(scene):
    ladder = {"id": 17385, "name": "Ladder", "x": 3005, "y": 10363, "plane": 0,
              "distance": 12, "screenX": 400, "screenY": 400, "actions": ["Climb-up"]}
    scene.objects = [ladder]
    scene.readings = [dict(xp=1000, busy=0, pos=(2998, 10352, 0)),
                      dict(xp=1000, busy=0, pos=(3005, 10362, 0)),
                      dict(xp=1000, busy=0, pos=(3005, 3962, 0))]
    ctx = make_ctx()
    agility.dungeon_recovery(ctx, W)
    assert (scene.walks[0]["x"], scene.walks[0]["y"]) == (3005, 10363)
    # unknown fell_step (restart): default to the first pit obstacle, the ropeswing
    assert (scene.walks[-1]["x"], scene.walks[-1]["y"]) == (3004, 3950)


def test_dungeon_recovery_ceiling_stops(scene):
    scene.objects = []
    scene.readings = [dict(xp=1000, busy=0, pos=(3005, 10362, 0))]
    ctx = make_ctx()
    ctx.state.data["dungeon_attempts"] = agility.DUNGEON_MAX_ATTEMPTS
    agility.dungeon_recovery(ctx, W)
    assert ctx.state.safety_stopped and "dungeon" in ctx.state.safety_reason


def test_tick_routes_to_dungeon_recovery(scene, monkeypatch):
    called = []
    monkeypatch.setattr(agility, "dungeon_recovery", lambda ctx, course: called.append(course.key))
    scene.readings = [dict(xp=1000, busy=0, pos=(3005, 10362, 0))]
    agility.tick(make_ctx())
    assert called == ["wilderness"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_agility.py -q`
Expected: the new tests fail (`maybe_eat` always True, `run_tag` always "failed", `dungeon_recovery` does nothing, `_handle_tag_failure` returns False).

- [ ] **Step 3: Replace the four stubs in `routines/agility.py`**

Add these constants beside the others:

```python
TAG_MAX_ATTEMPTS = 3         # a lost ticket is cheap; a stalled lap is not
DUNGEON_MAX_ATTEMPTS = 4
LADDER_CLIMB_WAIT_S = 6.0
LADDER_NEAR_TILES = 2
TICKET_NAME = "wilderness agility ticket"
```

Replace the stub block with:

```python
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
    if hp <= food.get("stopAt", 30):
        ctx.state.trigger_safety_stop(f"out of food at {hp} hp")
        return False
    debug_log.log("agility", f"no food left (hp={hp}) -- continuing until stopAt")
    return True


# --- dispenser Tag ----------------------------------------------------------------

def ticket_count() -> int:
    total = 0
    for item in controller.get_inventory() or []:
        if TICKET_NAME in (item.get("name") or "").lower():
            total += int(item.get("qty") or 0)
    return total


def run_tag(ctx, step, obj) -> str:
    """Tag the dispenser; success = the ticket stack grew (inventory is un-gated). The chat
    line is only an accelerator."""
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
        if ticket_count() > before:
            debug_log.log("agility", f"tagged the dispenser: tickets {before} -> {before + 1}")
            return "success"
    return "failed"


def _handle_tag_failure(ctx, course, step) -> bool:
    data = ctx.state.data
    n = data.get("attempts", 0) + 1
    data["attempts"] = n
    if n < TAG_MAX_ATTEMPTS:
        debug_log.log("agility", f"tag did not confirm -- retrying ({n}/{TAG_MAX_ATTEMPTS})")
        return True
    # Give the ticket up rather than the lap: walk onto the pipe's start box so
    # step_for_position resolves to the pipe instead of the dispenser again.
    first = course.steps[0]
    sx, sy, sp = first.start_tile
    debug_log.log("agility", f"tag failed {n} times -- skipping it this lap")
    data["attempts"] = 0
    travel.walk_to(ctx, {"x": sx, "y": sy, "plane": sp}, tolerance=1.0, goal_adjacent=False)
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
    refresh = controller.object_refresh_target(lad["x"], lad["y"], lad["plane"], lad["id"], radius=2)
    if not controller.click_entity(ctx.ser, obj, offset=(0, 0), refresh_target=refresh,
                                   refresh_legs=3, expected_action=lad["action"]):
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
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest tests/test_agility.py -q` then `python -m pytest tests/ -q`
Expected: all pass, nothing else regressed.

- [ ] **Step 5: py_compile and commit**

```bash
python -m py_compile routines/agility.py
git add ControllerAddon/routines/agility.py ControllerAddon/tests/test_agility.py
git commit -m "agility: eating between steps, dispenser Tag confirmed on ticket count, dungeon ladder recovery

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: wiring + docs

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java` (enum body)
- Modify: `ControllerAddon/controller.py:2973-2993` (`ROUTINE_FILES`)
- Modify: `CLAUDE.md` (new "Current state of routines/agility.py" section, placed directly after the GOTR section)

**Interfaces:**
- Consumes: `routines/agility.py` from Tasks 2-3.
- Produces: GUI-selectable `AGILITY` routine (after a client rebuild) and the CLAUDE.md contract for the next session.

- [ ] **Step 1: Add the enum entry**

In `RoutineChoice.java`, after `GUARDIANS_OF_THE_RIFT("Guardians of the Rift")` change the trailing `;` so the list reads:

```java
	GUARDIANS_OF_THE_RIFT("Guardians of the Rift"),
	AGILITY("Agility (course runner)");
```

- [ ] **Step 2: Add the `ROUTINE_FILES` key**

In `controller.py`'s `ROUTINE_FILES`, after the `"GUARDIANS_OF_THE_RIFT"` line add:

```python
    "AGILITY": "routines/agility.py",
```

- [ ] **Step 3: Verify**

Run from `ControllerAddon/`: `python -c "import controller; assert controller.ROUTINE_FILES['AGILITY'] == 'routines/agility.py'; print('ok')"`
Expected: `ok`. Run `python -m pytest tests/ -q` — all pass. (The Java change is verified by the next `./gradlew.bat run`; it is a one-line enum addition.)

- [ ] **Step 4: CLAUDE.md section**

Insert after the "Current state of routines/guardians_of_the_rift.py" section:

```markdown
## Current state of routines/agility.py

Written 2026-09-02 from a human recording (`recordings/2026-09-02_032621_wilderness-agility.*`,
one lap) plus live bridge reads the same day. A generic lap engine over per-course data in
`ControllerAddon/agility_courses.py` (only WILDERNESS shipped; a rooftop is a data entry).
Design: `docs/superpowers/specs/2026-09-02-agility-runner-design.md`. **Not yet run live**;
the spec's "Live-verification checklist" is the first session's worklist — it needs one
deliberate ropeswing fail and one log fail to confirm the dungeon recovery walk-back.

Signals are un-gated on purpose (user directive): Agility XP from `/api/skills/skills`,
the game's own `busy` varbit 12393 (1 while an obstacle animates, 0 when done -- the signal
that confirms Rocks on a forfeited lap, where the 498.9 bonus is 0), the player tile from
`/player`. The step is re-derived from the player's tile every time
(`agility_courses.step_for_position`, disjoint START boxes pinned by test), so nothing
load-bearing lives in `ctx.state.data`. Steps are chained inside one `tick` (the human's
next click lands 0.2s after the previous XP drop; the engine loop's ~1.1s would triple
that). `pvpSafety.fleeEnabled` is False for this routine (crowded course, user decision).
Dungeon ladder pinned live: id 17385 at (3005,10363), `Climb-up`.
```

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/controller/RoutineChoice.java ControllerAddon/controller.py CLAUDE.md
git commit -m "agility: register AGILITY routine in the GUI enum + ROUTINE_FILES; CLAUDE.md section

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5 [live]: first session (user-run, with Claude reading the log)

- [ ] **Step 1:** `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"` — no stray `controller.py`.
- [ ] **Step 2:** From `ControllerAddon/`, with the character standing anywhere inside the course:
  `python -u controller.py routines/agility.py *> logs/agility_live1.log 2>&1`
- [ ] **Step 3:** Watch one clean lap in the log: each `[agility] <step>: success` followed within ~0.3s by the next `clicked` line; Tag confirms on the ticket count.
- [ ] **Step 4:** Spec checklist items 4 and 5: fail the ropeswing and the log on purpose (or wait for it), confirm the ladder climb and the walk back reach the failed step's start box and the lap resumes.
- [ ] **Step 5:** Record every corrected constant (durations, boxes, `match_radius`) back into `agility_courses.py` with a MEASURED note, and pin it in `tests/test_agility.py`.

## Self-review notes

- Spec coverage: signals (Task 2 `run_step`), late-XP guard (Task 1 `xp_threshold` + Task 2 `pending_xp`), step boxes/resync (Task 1), chaining (Task 2 `tick`), Tag + eating + dungeon (Task 3), ceilings (Tasks 2-3), config flags (Task 2), wiring (Task 4), live checklist (Task 5). `LAP_STEPS_PER_TICK`, `STEP_MAX_ATTEMPTS`, `TAG_MAX_ATTEMPTS`, `DUNGEON_MAX_ATTEMPTS`, `NO_OBJECT`/`NO_STEP` ceilings all match the spec's constants table.
- Deviation from spec, deliberate: `classify` treats "busy 1 while already in the end box" as pending (wait for the edge or the XP) rather than success, so a mid-animation tile that briefly overlaps an end box cannot complete a step early.
- Type consistency: `Step.start_tile` is a 3-tuple everywhere; `travel.walk_to` always receives `{"x","y","plane"}`; `handle_outcome` returns bool; `run_step`/`run_tag` return the same string set.
