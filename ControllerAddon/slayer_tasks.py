# slayer_tasks.py -- what the task loop does with a task: FIGHT it, SKIP it (30 points), or STOP.
# PURE. Added 2026-09-19 (slayer spec §6, §7.1, §7.3). Sub-project 2's slice of the catalog:
# dispositions and target names only. Sites, routes, gear and supplies per Kuradal task are
# sub-project 3; Aya's sites already exist in aya_tasks.py.
#
# The table below is the USER's, task by task (docs/slayer-sites/decisions.md). An UNKNOWN task
# name is STOP, never SKIP: a cancel costs 30 points, and a name we do not recognise (new
# content, a DB decode slip) must not be paid for on a guess.

import aya_tasks

FIGHT, SKIP, STOP = "FIGHT", "SKIP", "STOP"

_KURADAL_FIGHT = (
    "aberrant spectres", "abyssal demons", "ankou", "basilisks", "black demons", "bloodveld",
    "cave horrors", "dagannoth", "dust devils", "elves", "fire giants", "gargoyles", "gryphons",
    "hellhounds", "kalphite", "kurask", "mutated zygomites", "nechryael", "suqahs", "trolls",
    "waterfiends", "wyrms",
)
_KURADAL_SKIP = (
    "boss", "dark beasts", "warped creatures", "vampyres", "araxytes", "drakes",
    "fossil island wyverns", "lizardmen", "red dragons", "aquanites", "aviansie", "black dragons",
    "blue dragons", "cave kraken", "frost dragons", "metal dragons", "skeletal wyverns",
    "smoke devils", "spiritual creatures", "tzhaar",
)
# Greater demons: the user does this task by hand on tormented demons, which count for it.
_KURADAL_STOP = ("greater demons",)

KURADAL = {**{t: FIGHT for t in _KURADAL_FIGHT}, **{t: SKIP for t in _KURADAL_SKIP},
           **{t: STOP for t in _KURADAL_STOP}}

# Task names that are NOT a substring of the monsters' names. combat_core matches each entry as
# a lowercased substring of the NPC name (and adds its own singular), so most tasks need nothing.
_TARGETS = {
    "birds": ["chicken", "rooster", "seagull", "duck", "penguin", "terrorbird"],
    "dwarves": ["dwarf"],
    "wolves": ["wolf"],
    "elves": ["elf"],
    "aberrant spectres": ["aberrant spectre"],
    "mutated zygomites": ["zygomite"],
}


def _key(task_name) -> str:
    return (task_name or "").strip().lower()


def user_skips(tasks_cfg) -> set:
    raw = (tasks_cfg or {}).get("skipTasks") or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def disposition(task_name, tasks_cfg) -> str:
    key = _key(task_name)
    if not key:
        return STOP
    known = key in KURADAL or aya_tasks.get(key) is not None
    if known and key in user_skips(tasks_cfg):
        return SKIP
    if key in KURADAL:
        return KURADAL[key]
    aya = aya_tasks.get(key)
    if aya is not None:
        return FIGHT if aya.disposition == aya_tasks.FIGHT else STOP
    return STOP


def target_names(task_name) -> list:
    key = _key(task_name)
    if not key:
        return []
    return list(_TARGETS.get(key, [key]))


def at_site(task_name, player):
    """True/False for a task whose site we know (Aya's, today); None when we know no site."""
    aya = aya_tasks.get(_key(task_name))
    if aya is None or not aya.areas:
        return None
    x, y = (player or {}).get("x"), (player or {}).get("y")
    if x is None or y is None:
        return False
    return aya_tasks.contains(aya, x, y, player.get("plane", 0))


def where(task_name) -> str:
    aya = aya_tasks.get(_key(task_name))
    if aya is None or not aya.location:
        return ""
    via = f" (via {aya.teleports[0]})" if aya.teleports else ""
    return f"{aya.location}{via}, around {aya.target}"
