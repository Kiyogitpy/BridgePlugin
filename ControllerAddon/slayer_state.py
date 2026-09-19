# slayer_state.py -- the slayer task loop's decisions that need no client: which master next,
# may we contact them, may we skip. PURE. Added 2026-09-19 (spec §7.1-7.4).
#
# The invariant everything here protects (spec F2, Aya.txt:29): asking Aya to REPLACE another
# master's task resets the task streak to ZERO. Taking an Aya task with no task active resets
# nothing. So a filler master is never contacted while a task is active -- assert_may_contact
# raises rather than returning False, because a caller that ignored a False would cost the user
# the streak this whole routine exists to build.

from collections import namedtuple

CANCEL_COST = 30            # Slayer Rewards "Cancel task": 30 points, streak kept (spec F5)
DEFAULT_MAX_SKIPS = 8       # ~40% of Kuradal rolls are skips with the user's list; 3 in a row is
                            # a 1-in-16 event, 8 in a row is not (docs/slayer-sites/decisions.md)
DEFAULT_POINTS_RESERVE = 60
FILLER_DEFAULT, MILESTONE_DEFAULT, EVERY_DEFAULT = "AYA", "KURADAL", 10

TaskState = namedtuple("TaskState", ("count", "task", "task_id", "streak", "points", "master_id", "has_task"))


class StreakGuardError(Exception):
    """Raised instead of contacting a filler master while a task is active."""


def from_bridge(payload):
    """GET /slayer -> TaskState. None stays None: the bridge or endpoint is unavailable and the
    caller must do nothing. `task` is None when the key is ABSENT -- Gson drops null map values,
    so an undecoded name is missing on the wire rather than null."""
    if payload is None:
        return None
    count = int(payload.get("count") or 0)
    name = payload.get("taskName")
    return TaskState(
        count=count,
        task=name.strip().lower() if isinstance(name, str) and name.strip() else None,
        task_id=int(payload.get("taskId") or 0),
        streak=int(payload.get("streak") or 0),
        points=int(payload.get("points") or 0),
        master_id=payload.get("masterId"),
        has_task=bool(payload.get("hasTask", count > 0)),
    )


def next_master(streak: int, tasks_cfg) -> str:
    """The master to ask for task number streak + 1."""
    cfg = tasks_cfg or {}
    every = int(cfg.get("milestoneEvery") or EVERY_DEFAULT)
    if (int(streak) + 1) % every == 0:
        return cfg.get("milestoneMaster") or MILESTONE_DEFAULT
    return cfg.get("fillerMaster") or FILLER_DEFAULT


def assert_may_contact(master: str, state, tasks_cfg=None) -> None:
    filler = (tasks_cfg or {}).get("fillerMaster") or FILLER_DEFAULT
    if state is not None and state.has_task and master == filler:
        raise StreakGuardError(
            f"refusing to contact {master} with a task active ({state.task or state.task_id}, "
            f"{state.count} left): a task change there resets the streak of {state.streak} to zero")


def can_skip(points: int, skips_so_far: int, tasks_cfg):
    cfg = tasks_cfg or {}
    max_skips = cfg.get("maxSkips")
    max_skips = DEFAULT_MAX_SKIPS if max_skips is None else int(max_skips)
    reserve = cfg.get("pointsReserve")
    reserve = DEFAULT_POINTS_RESERVE if reserve is None else int(reserve)
    if skips_so_far >= max_skips:
        return False, f"already skipped {skips_so_far} times for this slot (limit {max_skips})"
    if points - CANCEL_COST < reserve:
        return False, f"{points} points would drop below the reserve of {reserve} after a {CANCEL_COST}-point cancel"
    return True, ""
