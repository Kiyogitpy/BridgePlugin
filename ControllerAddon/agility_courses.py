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
