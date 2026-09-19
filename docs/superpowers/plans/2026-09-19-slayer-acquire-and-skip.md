# Slayer task loop, sub-project 2: acquire and skip — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `routines/slayer.py` stops being a stub: with no task it casts NPC Contact, picks the right master (Kuradal on every 10th task, Aya otherwise), takes the assignment and confirms it from `GET /slayer`; with an unwanted task it cancels it for 30 points; at an Aya site it fights; anywhere else it stops and says where to go.

**Architecture:** `slayer_tasks.py` (pure) answers "what do we do with this task". `npc_contact.py` owns the spell, the master picker and the dialogue walk, and confirms every outcome from `/slayer`. `routines/slayer.py` derives its phase from live state every tick and keeps only counters in `ctx.state.data`. Travel, banking and loadouts are sub-project 3 and are NOT here.

**Tech Stack:** Python 3 + pytest, run from `ControllerAddon/`. No Java change, no client rebuild.

**Spec:** `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` §7.1–7.4, §9. **Measured facts (these OVERRIDE the spec where they differ):** `docs/slayer-sites/recordings-2026-09-19.md`.

## Global Constraints

- **The physical Arduino is the only input path**: `controller.click_entity`, `controller.click_menu_action`, `controller.key_tap` only.
- **THE invariant (spec F2): a filler master (Aya) is never contacted while a task is active** — asking her to replace a task resets the streak. `slayer_state.assert_may_contact(master, state, tasks_cfg)` is called before every cast and it RAISES.
- **`None` from `/slayer` means unavailable: do nothing.** Never read it as "no task".
- **An options menu with no expected option is ESC + failure, never a guessed number and never Space.** Options are chosen by TEXT (`dialogue.find_option`), never by position — Aya's "are you sure you don't want Vannaka" question shifts what "1" means.
- **Outcomes are confirmed by a bridge read** (`/slayer`), never slept off. Poll loops inside a budget are fine.
- **Anything that spends a resource on repeat has a ceiling, not just a cooldown** (runes per cast, points per cancel).
- Interface clicks pass `check_ui_blockers=False`, `offset=(0, 0)`, `simulate_mistakes=False`.
- `controller.py` is loaded twice: add no module-level mutable state to it. New modules keep per-run state in `ctx.state.data` only.
- Tests are hermetic: every bridge read and every input call is monkeypatched; `time.sleep` is patched out where a loop would otherwise wait.
- `py_compile` every touched file; `python -m pytest tests/<file> -q` from `ControllerAddon/`. Whole-suite baseline: exactly 4 failures in `tests/test_mastering_mixology.py`; three known-flaky tests (`test_travel.py::test_the_open_budget_is_capped...`, `test_human_move.py::test_two_flushes...`, one in `test_falconry.py`).
- Another session commits unrelated `gotr`/`guardians` files on this branch: stage by exact path, never `git add -A`.
- Commit after every task; messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File map

| File | Responsibility | Task |
|---|---|---|
| `ControllerAddon/slayer_tasks.py` (new) + `tests/test_slayer_tasks.py` | FIGHT / SKIP / STOP per task, target names, Aya at-site test | 1 |
| `ControllerAddon/npc_contact.py` (new) + `tests/test_npc_contact.py` | cast, pick the master, walk the dialogue, `get_task` | 2 |
| `ControllerAddon/npc_contact.py` + tests | `cancel_task` through the rewards screen | 3 |
| `ControllerAddon/routines/slayer.py` + `tests/test_slayer_routine.py` (new) | the orchestrator | 4 |
| `CLAUDE.md`, `docs/routine-notes.md`, the spec | docs + full verification | 5 |

---

### Task 1: `slayer_tasks.py` — what to do with a task

**Files:**
- Create: `ControllerAddon/slayer_tasks.py`
- Test: `ControllerAddon/tests/test_slayer_tasks.py`

**Interfaces:**
- Consumes: `aya_tasks.get(name) -> AyaTask | None` (`.disposition` is `aya_tasks.FIGHT` or `aya_tasks.BLOCKED`, `.location`, `.teleports`), `aya_tasks.contains(task, x, y, plane) -> bool`.
- Produces: constants `FIGHT`, `SKIP`, `STOP`; `disposition(task_name, tasks_cfg) -> str`; `user_skips(tasks_cfg) -> set[str]`; `target_names(task_name) -> list[str]`; `at_site(task_name, player) -> bool | None` (`None` = no site known for this task); `where(task_name) -> str` (a human-readable "go here" line, `""` when unknown).

- [ ] **Step 1: Write the failing tests**

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import slayer_tasks as st


def test_the_users_kuradal_decisions():
    """docs/slayer-sites/decisions.md: 22 FIGHT, 1 STOP, 20 SKIP."""
    fights = ["Aberrant spectres", "Abyssal demons", "Ankou", "Basilisks", "Black demons", "Bloodveld",
              "Cave horrors", "Dagannoth", "Dust devils", "Elves", "Fire giants", "Gargoyles", "Gryphons",
              "Hellhounds", "Kalphite", "Kurask", "Mutated zygomites", "Nechryael", "Suqahs", "Trolls",
              "Waterfiends", "Wyrms"]
    skips = ["Boss", "Dark beasts", "Warped creatures", "Vampyres", "Araxytes", "Drakes",
             "Fossil Island wyverns", "Lizardmen", "Red dragons", "Aquanites", "Aviansie", "Black dragons",
             "Blue dragons", "Cave kraken", "Frost dragons", "Metal dragons", "Skeletal wyverns",
             "Smoke devils", "Spiritual creatures", "TzHaar"]
    assert [t for t in fights if st.disposition(t, {}) != st.FIGHT] == []
    assert [t for t in skips if st.disposition(t, {}) != st.SKIP] == []
    assert st.disposition("Greater demons", {}) == st.STOP
    assert len(fights) == 22 and len(skips) == 20


def test_aya_tasks_are_fought_and_a_blocked_one_stops():
    assert st.disposition("Bears", {}) == st.FIGHT
    assert st.disposition("bears", None) == st.FIGHT
    assert st.disposition("Dogs", {}) == st.STOP, "blocked at Aya: if it ever shows up a human should look"


def test_an_unknown_task_stops_it_is_never_cancelled():
    """Cancelling costs 30 points. A name we do not recognise (a new task, a decode slip) must not
    be paid for on a guess."""
    assert st.disposition("Some New Monster", {}) == st.STOP
    assert st.disposition(None, {}) == st.STOP
    assert st.disposition("", {}) == st.STOP


def test_the_panels_skip_list_overrides_a_fight():
    cfg = {"skipTasks": "Abyssal demons, kurask ,  "}
    assert st.user_skips(cfg) == {"abyssal demons", "kurask"}
    assert st.disposition("Kurask", cfg) == st.SKIP
    assert st.disposition("Abyssal Demons", cfg) == st.SKIP
    assert st.disposition("Bloodveld", cfg) == st.FIGHT
    assert st.user_skips(None) == set() and st.user_skips({"skipTasks": None}) == set()


def test_the_skip_list_cannot_turn_a_stop_into_a_paid_cancel_by_accident():
    assert st.disposition("Greater demons", {"skipTasks": "greater demons"}) == st.SKIP, \
        "an explicit user entry IS a decision to pay"
    assert st.disposition("Some New Monster", {"skipTasks": "bloodveld"}) == st.STOP


def test_target_names_are_what_combat_core_matches_by_substring():
    """combat_core matches a lowercased name as a SUBSTRING of the NPC's name and adds the
    singular itself, so the task's own name is enough for most tasks."""
    assert st.target_names("Bears") == ["bears"]
    assert st.target_names("Nechryael") == ["nechryael"]
    assert "chicken" in st.target_names("Birds") and "seagull" in st.target_names("Birds")
    assert st.target_names("Dwarves") == ["dwarf"]
    assert st.target_names("Wolves") == ["wolf"]
    assert st.target_names("Kalphite") == ["kalphite"]
    assert st.target_names(None) == []


def test_at_site_is_known_only_for_aya_tasks():
    assert st.at_site("Banshees", {"x": 3440, "y": 3545, "plane": 0}) is True
    assert st.at_site("Banshees", {"x": 3200, "y": 3200, "plane": 0}) is False
    assert st.at_site("Nechryael", {"x": 3440, "y": 3570, "plane": 2}) is None, "Kuradal sites are sub-project 3"
    assert st.at_site("Banshees", {}) is False


def test_where_names_the_place_and_the_first_teleport():
    text = st.where("Bears")
    assert "Legends" in text and "BLR" in text
    assert st.where("Nechryael") == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_slayer_tasks.py -q` — Expected: `ModuleNotFoundError: No module named 'slayer_tasks'`.

- [ ] **Step 3: Write `ControllerAddon/slayer_tasks.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m py_compile slayer_tasks.py; python -m pytest tests/test_slayer_tasks.py -q` — Expected: 8 passed.

Note for the implementer: `test_the_skip_list_cannot_turn_a_stop_into...` asserts that a user-listed KNOWN task (Greater demons) becomes SKIP while an unknown one stays STOP. If `aya_tasks.get("Kalphite")` and `KURADAL["kalphite"]` both exist that is fine — both say FIGHT.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/slayer_tasks.py ControllerAddon/tests/test_slayer_tasks.py
git commit -m "slayer_tasks.py: fight, skip or stop per task; an unknown task is never paid to cancel

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `npc_contact.py` — cast, pick the master, take the assignment

**Files:**
- Create: `ControllerAddon/npc_contact.py`
- Test: `ControllerAddon/tests/test_npc_contact.py`

**Measured facts this encodes** (`docs/slayer-sites/recordings-2026-09-19.md`): spell widget `218.113`; the picker is interface group `75`, its rows are dynamic children of `75.4`, each row's TEXT and NAME are EMPTY and the only thing that names it is its action `Contact Aya` / `Contact Kuradal`; the picker appears ~4.4 s after the cast; dialogue = greeting → options (`I need another assignment.`) → for a strong account Aya asks about Vannaka → options → `Your new task is to kill 15 Bears.`; **the task is assigned on the tick that line appears**, before the dialogue ends.

**Interfaces:**
- Consumes: `controller.get_widget(group, child) -> dict` (`found`, `clickable`, `screenX`, `screenY`), `controller.get_widgets_dump() -> list[dict]` (each: `group`, `child`, `index`, `screenX`, `screenY` (centre), `canvasY`, `height`, optional `actions: list[str]`), `controller.click_entity(ser, entity, **kw)`, `controller.key_tap(ser, key)`, `controller.get_active_tab()`, `controller.get_slayer_task() -> dict | None`, `controller.get_api(group, reader)`, `controller.get_inventory()`, `controller.get_rune_pouch()`, `controller.get_equipment()`, `controller.get_skills()`, `controller.human_delay(a, b)`; `dialogue.read()`, `dialogue.options(d)`, `dialogue.find_option(opts, needle)`, `dialogue.is_open(d)`, `dialogue.close(ctx)`; `slayer_state.from_bridge`, `slayer_state.assert_may_contact`, `slayer_state.canonical_master`.
- Produces: `npc_contact.MASTERS` (`{"AYA": {...}, "KURADAL": {...}}`); `find_master_row(dump, master) -> dict | None`; `missing_requirements(master) -> str | None`; `open_picker(ctx) -> bool`; `contact(ctx, master, tasks_cfg) -> bool` (dialogue with that master is open); `walk_to_assignment(ctx) -> TaskState | None`; `get_task(ctx, master, tasks_cfg) -> TaskState | None`; constants `SPELL = (218, 113)`, `PICKER_GROUP = 75`, `MAGIC_TAB_KEY = "F6"`, `PICKER_BUDGET_S = 9.0`, `DIALOGUE_BUDGET_S = 25.0`.

- [ ] **Step 1: Write the failing tests**

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import dialogue
import npc_contact
import slayer_state

CFG = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10}
NO_TASK = {"count": 0, "hasTask": False, "streak": 12, "points": 24, "taskId": 52}
BEARS = {"count": 15, "hasTask": True, "taskName": "Bears", "taskId": 13, "streak": 12, "points": 24, "masterId": 1}


class _Ctx:
    def __init__(self):
        self.ser = None

        class _S:
            data = {}
            stops = []

            def trigger_safety_stop(self, reason):
                self.stops.append(reason)
        self.state = _S()
        self.state.data, self.state.stops = {}, []
        self.events = None


def _row(index, action, y=300):
    return {"group": 75, "child": 4, "index": index, "screenX": 690, "screenY": y, "canvasY": y - 20,
            "height": 40, "actions": [action]}


def _quiet(monkeypatch):
    monkeypatch.setattr(npc_contact.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def test_the_master_row_is_found_by_its_action_never_by_slot():
    """MEASURED: a picker row's text and name are EMPTY; only its action names the master. The
    slots seen (Aya 18, Kuradal 33) are not used -- they move when a master is unlocked."""
    dump = [_row(33, "Contact Kuradal", 419), _row(18, "Contact Aya", 306), _row(2, "Contact Honest Jimmy", 120),
            {"group": 218, "child": 113, "index": -1, "screenX": 1, "screenY": 1, "actions": ["Cast"]}]
    assert npc_contact.find_master_row(dump, "AYA")["index"] == 18
    assert npc_contact.find_master_row(dump, "KURADAL")["index"] == 33
    assert npc_contact.find_master_row(dump, "KONAR") is None


def test_the_other_name_of_the_same_master_matches_too():
    """Aya is Turael's Varlamore twin and Kuradal is Duradel's; which name the picker shows
    depends on the account, so both are accepted."""
    assert npc_contact.find_master_row([_row(9, "Contact Turael")], "AYA")["index"] == 9
    assert npc_contact.find_master_row([_row(9, "Contact Duradel")], "KURADAL")["index"] == 9
    assert npc_contact.find_master_row([_row(9, "Contact Ayatollah")], "AYA") is None, "whole name, not a prefix"


def test_get_task_refuses_aya_while_a_task_is_active_before_touching_anything(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS))
    touched = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: touched.append("click"))
    with pytest.raises(slayer_state.StreakGuardError):
        npc_contact.get_task(_Ctx(), "AYA", CFG)
    assert touched == []


def test_get_task_does_nothing_when_the_slayer_state_is_unavailable(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: None)
    touched = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    with pytest.raises(slayer_state.StreakGuardError):
        npc_contact.get_task(_Ctx(), "KURADAL", CFG)
    assert touched == []


def _dialogue_script(monkeypatch, stages, slayer_after):
    """`stages` is a list of dialogue dicts served one per read; the last repeats. The slayer
    state flips to `slayer_after` once the stage whose npcText contains 'new task' has been read."""
    seen = {"i": 0, "assigned": False}

    def read():
        stage = stages[min(seen["i"], len(stages) - 1)]
        if "new task" in (stage.get("npcText") or ""):
            seen["assigned"] = True
        return stage
    monkeypatch.setattr(dialogue, "read", read)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(slayer_after if seen["assigned"] else NO_TASK))
    keys = []

    def key_tap(ser, key):
        keys.append(key)
        seen["i"] += 1
    monkeypatch.setattr(controller, "key_tap", key_tap)
    return keys


def test_the_recorded_aya_dialogue_is_walked_by_text(monkeypatch):
    _quiet(monkeypatch)
    stages = [
        {"npcName": "Aya", "npcText": "'Ello, and what are you after then?", "options": []},
        {"options": ["Select an option", "I need another assignment.", "Have you any rewards for me?", "Er... nothing..."]},
        {"playerText": "I need another assignment.", "options": []},
        {"npcName": "Aya", "npcText": "You're actually very strong, are you sure you don't want Vannaka under Edgeville to assign you a task?", "options": []},
        {"options": ["Select an option", "No that's okay, I'll take a task from you.", "Oh okay then, I'll go talk to Vannaka."]},
        {"npcName": "Aya", "npcText": "Excellent, you're doing great. Your new task is to kill 15 Bears.", "options": []},
    ]
    keys = _dialogue_script(monkeypatch, stages, BEARS)
    state = npc_contact.walk_to_assignment(_Ctx())
    assert state is not None and state.task == "bears" and state.count == 15
    assert keys[:5] == ["SPACE", "1", "SPACE", "SPACE", "1"]
    assert keys[-1] == "ESC", "once /slayer shows the task the rest of the dialogue is just closed"


def test_the_assignment_option_is_chosen_by_text_when_it_is_not_first(monkeypatch):
    _quiet(monkeypatch)
    stages = [
        {"options": ["Select an option", "Have you any rewards for me?", "I need another assignment."]},
        {"npcName": "Kuradal", "npcText": "Your new task is to kill 130 Bloodveld.", "options": []},
    ]
    keys = _dialogue_script(monkeypatch, stages, dict(BEARS, taskName="Bloodveld", count=130))
    assert npc_contact.walk_to_assignment(_Ctx()).task == "bloodveld"
    assert keys[0] == "2"


def test_an_unexpected_menu_is_escaped_never_guessed(monkeypatch):
    """The hard rule: a menu with no expected option is ESC + failure. Never '1', never Space."""
    _quiet(monkeypatch)
    stages = [{"options": ["Select an option", "Yes, replace my task.", "No, I'll keep it."]}]
    keys = _dialogue_script(monkeypatch, stages, BEARS)
    ctx = _Ctx()
    assert npc_contact.walk_to_assignment(ctx) is None
    assert keys == ["ESC"]
    assert "replace my task" in ctx.state.data["npc_contact_unknown_menu"]


def test_a_master_who_says_you_still_have_a_task_is_a_failure_not_a_loop(monkeypatch):
    _quiet(monkeypatch)
    stages = [
        {"options": ["Select an option", "I need another assignment."]},
        {"npcName": "Kuradal", "npcText": "You're still hunting nechryael; you have 92 to go.", "options": []},
        {},
    ]
    keys = _dialogue_script(monkeypatch, stages, NO_TASK)
    assert npc_contact.walk_to_assignment(_Ctx()) is None
    assert keys.count("1") == 1


def test_missing_requirements_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"spellbook": "STANDARD"})
    assert "Lunar" in npc_contact.missing_requirements("AYA")
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"spellbook": "LUNAR"})
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"level": 89}})
    monkeypatch.setattr(controller, "get_equipment", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"carried": True, "runes": [
        {"name": "Astral rune", "quantity": 500}, {"name": "Cosmic rune", "quantity": 500}]})
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Air rune", "quantity": 1}])
    assert "air" in npc_contact.missing_requirements("AYA").lower(), "2 air runes per cast, 1 held"
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Air rune", "quantity": 40}])
    assert npc_contact.missing_requirements("AYA") is None
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"level": 60}})
    assert "67" in npc_contact.missing_requirements("AYA")


def test_an_unreadable_spellbook_does_not_block_the_cast(monkeypatch):
    """None = the reader is unavailable (un-rebuilt client). The cast itself then proves it:
    a non-Lunar book has no clickable 218.113."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(controller, "get_equipment", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"name": "Astral rune", "quantity": 9}, {"name": "Cosmic rune", "quantity": 9}, {"name": "Air rune", "quantity": 9}])
    assert npc_contact.missing_requirements("AYA") is None


def test_contact_clicks_the_spell_then_the_row_with_the_hover_gate(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None:
                        {"found": True, "clickable": True, "screenX": 1270, "screenY": 676})
    dumps = iter([[], [], [_row(18, "Contact Aya", 306), _row(33, "Contact Kuradal", 419)]])
    last = {}

    def get_widgets_dump(scan=False):
        last["d"] = next(dumps, last.get("d", []))
        return last["d"]
    monkeypatch.setattr(controller, "get_widgets_dump", get_widgets_dump)
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append((entity.get("index"), kw.get("expected_action"), kw.get("check_ui_blockers"), kw.get("offset")))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(dialogue, "read", lambda: {"npcName": "Aya", "npcText": "'Ello, and what are you after then?"})
    assert npc_contact.contact(_Ctx(), "AYA", CFG) is True
    assert clicks[0] == (None, None, False, (0, 0)), "the spell: an interface click, no hover gate"
    assert clicks[1] == (18, "Contact Aya", False, (0, 0))


def test_contact_fails_cleanly_when_the_spell_is_not_clickable(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: {"found": True, "clickable": False})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert npc_contact.contact(_Ctx(), "AYA", CFG) is False
    assert clicked == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_npc_contact.py -q` — Expected: `ModuleNotFoundError: No module named 'npc_contact'`.

- [ ] **Step 3: Write `ControllerAddon/npc_contact.py`**

```python
# npc_contact.py -- reach a slayer master through the Lunar spell NPC Contact, and take a task.
# Added 2026-09-19 (slayer spec §9). Every id and line below was MEASURED from the user's own
# recordings that day (docs/slayer-sites/recordings-2026-09-19.md), not reasoned:
#
#   spell            widget 218.113, option "Cast"; anim 4413 / spotanim 728 follow in 0.3s
#   picker           interface group 75, ~4.4s after the cast. A row's TEXT and NAME are EMPTY --
#                    the only thing naming a row is its ACTION, "Contact Aya". So rows come from
#                    /widgets/dump (which carries actions), never /widget/children (which does not),
#                    and never from a slot number (Aya 18, Kuradal 33 on the day; they move).
#   dialogue         greeting -> options ("I need another assignment.") -> a strong account gets
#                    Aya's "are you sure you don't want Vannaka" -> options -> "Your new task is
#                    to kill 15 Bears."  The task is assigned ON THE TICK that line appears
#                    (varbit 4067, varp 394, varp 395 together), before the dialogue ends.
#
# Two rules everything here obeys. (1) slayer_state.assert_may_contact runs before ANY input:
# asking Aya to replace an active task resets the streak. (2) An options menu with none of the
# expected options is ESC + failure -- never a guessed number, never Space. The one dialogue in
# the game this module must never accept is a replace-my-task confirmation, so an unknown menu is
# a bug to stop on, and its text is kept in ctx.state.data for the log.
#
# UNVERIFIED LIVE as of 2026-09-19: the option text that declines Vannaka was not captured (the
# recorder logs keys, not menus); DECLINE_NEEDLES is the wiki's wording and the first live run
# will confirm or correct it -- safely, by rule (2).

import time

import controller
import debug_log
import dialogue
import slayer_state

LOG = "npc_contact"
SPELL = (218, 113)
PICKER_GROUP = 75
MAGIC_TAB_KEY = "F6"
MAGIC_TAB_INDEX = 6

PICKER_BUDGET_S = 9.0        # cast -> picker readable. MEASURED 4.4s; the cast animation is fixed.
CONTACT_BUDGET_S = 5.0       # row click -> the master's greeting. MEASURED 0.5-0.7s.
DIALOGUE_BUDGET_S = 25.0     # greeting -> task in /slayer. MEASURED 18s at human pace with Vannaka.
POLL_S = 0.2
MAX_STEPS = 14               # keypresses in one dialogue walk; the recorded one takes 5

MASTERS = {
    "AYA": {"names": ("aya", "turael"), "magic": 67},
    "KURADAL": {"names": ("kuradal", "duradel"), "magic": 67},
    "KONAR": {"names": ("konar",), "magic": 67},
}

ASSIGN_NEEDLES = ("another assignment", "need a task", "new assignment")
DECLINE_NEEDLES = ("take a task from you", "no that's okay", "from you")   # Aya's Vannaka question
ASSIGNED_TEXT = "new task is to kill"
STILL_HUNTING_TEXT = "still hunting"

RUNES_PER_CAST = {"astral rune": 1, "cosmic rune": 1, "air rune": 2}
AIR_STAVES = ("staff of air", "air battlestaff", "mystic air staff", "smoke battlestaff", "mist battlestaff",
              "dust battlestaff", "mystic smoke staff", "mystic mist staff", "mystic dust staff")


def _master(master) -> dict:
    return MASTERS.get(slayer_state.canonical_master(master)) or {"names": ((master or "").lower(),), "magic": 67}


def find_master_row(dump, master):
    """The picker row whose action is 'Contact <one of the master's names>', or None."""
    wanted = {f"contact {name}" for name in _master(master)["names"]}
    for entry in dump or ():
        if entry.get("group") != PICKER_GROUP or entry.get("screenX") is None:
            continue
        if any((action or "").strip().lower() in wanted for action in entry.get("actions") or ()):
            return entry
    return None


def _rune_counts() -> dict:
    counts = {}
    for item in controller.get_inventory() or ():
        name = (item.get("name") or "").lower()
        counts[name] = counts.get(name, 0) + int(item.get("quantity") or 1)
    pouch = controller.get_rune_pouch() or {}
    for rune in pouch.get("runes") or ():
        name = (rune.get("name") or "").lower()
        if name:
            counts[name] = counts.get(name, 0) + int(rune.get("quantity") or 0)
    return counts


def missing_requirements(master):
    """A sentence naming the first thing that stops the cast, or None. An UNREADABLE value never
    blocks (None = cannot verify); the spell widget not being clickable is the backstop."""
    magic = controller.get_api("player", "magic") or {}
    book = magic.get("spellbook")
    if book is not None and str(book).upper() != "LUNAR":
        return f"NPC Contact needs the Lunar spellbook (the account is on {book})"
    level = ((controller.get_skills() or {}).get("Magic") or {}).get("level")
    need = _master(master)["magic"]
    if level is not None and level < need:
        return f"NPC Contact needs {need} Magic (have {level})"
    counts = _rune_counts()
    worn = [(item.get("name") or "").lower() for item in controller.get_equipment() or ()]
    has_air_staff = any(staff in name for name in worn for staff in AIR_STAVES)
    for rune, per_cast in RUNES_PER_CAST.items():
        if rune == "air rune" and has_air_staff:
            continue
        if counts.get(rune, 0) < per_cast:
            return f"NPC Contact needs {per_cast} {rune}(s) per cast (have {counts.get(rune, 0)})"
    return None


def open_picker(ctx) -> bool:
    """Magic tab -> cast -> the picker is readable. False (and no cast) when the spell is not
    clickable: wrong spellbook, filtered spell, or an interface covering the tab."""
    if controller.get_active_tab() != MAGIC_TAB_INDEX:
        controller.key_tap(ctx.ser, MAGIC_TAB_KEY)
        controller.human_delay(0.3, 0.5)
    spell = controller.get_widget(*SPELL)
    if not spell or not spell.get("clickable"):
        debug_log.log(LOG, f"spell widget {SPELL[0]}.{SPELL[1]} is not clickable -- Lunar spellbook? spell filtered?")
        return False
    debug_log.log(LOG, "casting NPC Contact")
    if not controller.click_entity(ctx.ser, spell, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False):
        return False
    ctx.state.data["npc_contact_casts"] = ctx.state.data.get("npc_contact_casts", 0) + 1
    deadline = time.time() + PICKER_BUDGET_S
    while time.time() < deadline:
        if any(entry.get("group") == PICKER_GROUP for entry in controller.get_widgets_dump() or ()):
            return True
        time.sleep(POLL_S)
    debug_log.log(LOG, f"the contact picker (interface {PICKER_GROUP}) never appeared within {PICKER_BUDGET_S}s")
    return False


def contact(ctx, master, tasks_cfg) -> bool:
    """Cast and pick `master`. True once THEIR dialogue is open."""
    slayer_state.assert_may_contact(master, slayer_state.from_bridge(controller.get_slayer_task()), tasks_cfg)
    if not open_picker(ctx):
        return False
    row = find_master_row(controller.get_widgets_dump(), master)
    if row is None:
        debug_log.log(LOG, f"no 'Contact {master}' row in the picker -- not unlocked, or scrolled out of view")
        dialogue.close(ctx)
        return False
    action = (row.get("actions") or [""])[0]
    clicked = controller.click_entity(ctx.ser, row, offset=(0, 0), check_ui_blockers=False,
                                      simulate_mistakes=False, expected_action=action)
    if not clicked:
        debug_log.log(LOG, f"the click on '{action}' was withheld or failed")
        dialogue.close(ctx)
        return False
    names = _master(master)["names"]
    deadline = time.time() + CONTACT_BUDGET_S
    while time.time() < deadline:
        speaker = (dialogue.read().get("npcName") or "").strip().lower()
        if speaker in names:
            return True
        time.sleep(POLL_S)
    debug_log.log(LOG, f"{master}'s dialogue did not open within {CONTACT_BUDGET_S}s")
    return False


def _assigned():
    state = slayer_state.from_bridge(controller.get_slayer_task())
    return state if state is not None and state.has_task else None


def walk_to_assignment(ctx):
    """Drive the open master dialogue until /slayer shows a task. Returns the TaskState, or None.

    Space through text; at a menu choose by TEXT (assignment first, then Aya's Vannaka decline);
    a menu with neither is ESC + None with its options kept in ctx.state.data. Success is read
    from /slayer, so the tail of the dialogue (tips / 'Okay, great!') is simply closed."""
    deadline = time.time() + DIALOGUE_BUDGET_S
    steps = 0
    while time.time() < deadline and steps < MAX_STEPS:
        state = _assigned()
        if state is not None:
            dialogue.close(ctx)
            return state
        d = dialogue.read()
        opts = dialogue.options(d)
        if opts:
            hit = None
            for needle in ASSIGN_NEEDLES + DECLINE_NEEDLES:
                hit = dialogue.find_option(opts, needle)
                if hit is not None:
                    break
            if hit is None:
                ctx.state.data["npc_contact_unknown_menu"] = " | ".join(opts)
                debug_log.log(LOG, f"UNEXPECTED menu, escaping without choosing: {opts}")
                dialogue.close(ctx)
                return None
            controller.key_tap(ctx.ser, str(hit + 1))
        elif STILL_HUNTING_TEXT in (d.get("npcText") or "").lower():
            debug_log.log(LOG, f"the master says a task is still active: {d.get('npcText')!r}")
            dialogue.close(ctx)
            return None
        elif dialogue.is_open(d):
            controller.key_tap(ctx.ser, "SPACE")
        else:
            time.sleep(POLL_S)
            continue
        steps += 1
        controller.human_delay(0.5, 0.9)
    state = _assigned()
    dialogue.close(ctx)
    if state is None:
        debug_log.log(LOG, f"no task registered after {steps} dialogue steps")
    return state


def get_task(ctx, master, tasks_cfg):
    """One full attempt: guard, cast, pick, take the assignment. TaskState on success, else None.
    Raises slayer_state.StreakGuardError instead of contacting a filler master with a task active."""
    slayer_state.assert_may_contact(master, slayer_state.from_bridge(controller.get_slayer_task()), tasks_cfg)
    if not contact(ctx, master, tasks_cfg):
        return None
    return walk_to_assignment(ctx)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m py_compile npc_contact.py; python -m pytest tests/test_npc_contact.py -q` — Expected: 12 passed.

If `test_the_recorded_aya_dialogue_is_walked_by_text` fails on the exact key list, print `keys` and check it against the stage list by hand before touching anything: the intended sequence is SPACE (greeting), `1` (assignment), SPACE (player line), SPACE (Vannaka question), `1` (decline), then the assignment stage flips `/slayer` and the walk ends with ESC. Report rather than loosen.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/npc_contact.py ControllerAddon/tests/test_npc_contact.py
git commit -m "npc_contact.py: cast, find the master by its action, take the assignment by text

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `npc_contact.cancel_task` — 30 points through the rewards screen

**Files:**
- Modify: `ControllerAddon/npc_contact.py` (append)
- Test: `ControllerAddon/tests/test_npc_contact.py` (append)

**Measured:** rewards interface = group `426`; the `Tasks` tab is `426.14` dynamic child **6**; the USER confirmed the master's NPC Contact dialogue reaches rewards. **NOT measured:** the dialogue option's wording, the cancel button's widget, the confirmation. So the button is found by its TEXT or ACTION containing "cancel" inside group 426, and the whole function is a no-op-with-failure when it cannot find exactly one such thing.

**Interfaces:**
- Consumes: everything Task 2 produced, plus `slayer_state.can_skip(points, skips_so_far, tasks_cfg) -> (bool, str)`, `slayer_state.CANCEL_COST`.
- Produces: `open_rewards(ctx, master, tasks_cfg) -> bool`; `find_cancel_control(dump) -> dict | None`; `cancel_task(ctx, master, tasks_cfg) -> bool`; constants `REWARDS_GROUP = 426`, `TASKS_TAB = (426, 14, 6)`, `REWARD_NEEDLES`.

- [ ] **Step 1: Append the failing tests**

```python
def _w(group, child, index=-1, text=None, actions=None, x=500, y=300):
    entry = {"group": group, "child": child, "index": index, "screenX": x, "screenY": y}
    if text:
        entry["text"] = text
    if actions:
        entry["actions"] = actions
    return entry


def test_the_cancel_control_is_found_by_word_inside_the_rewards_interface_only():
    dump = [_w(426, 14, 6, actions=["Tasks"]), _w(426, 26, 3, text="Cancel task", actions=["Cancel"]),
            _w(162, 5, text="Cancel"), _w(426, 30, 1, text="Block task", actions=["Block"])]
    assert npc_contact.find_cancel_control(dump)["child"] == 26
    assert npc_contact.find_cancel_control([_w(162, 5, text="Cancel")]) is None


def test_two_cancel_candidates_is_a_refusal_not_a_coin_toss():
    dump = [_w(426, 26, 3, text="Cancel task"), _w(426, 40, 0, text="Cancel block")]
    assert npc_contact.find_cancel_control(dump) is None


def test_cancel_task_is_refused_without_the_points(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS, points=24))
    touched = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: touched.append("click") or True)
    assert npc_contact.cancel_task(_Ctx(), "KURADAL", CFG) is False
    assert touched == []


def test_cancel_task_never_goes_through_the_filler_master(monkeypatch):
    """Rewards from Aya is harmless, but the DIALOGUE to get there passes her assignment option.
    With a task active (always true for a cancel) she is simply not contacted."""
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS, points=300))
    with pytest.raises(slayer_state.StreakGuardError):
        npc_contact.cancel_task(_Ctx(), "AYA", CFG)


def test_cancel_task_confirms_from_the_slayer_state_not_from_the_click(monkeypatch):
    _quiet(monkeypatch)
    state = {"s": dict(BEARS, points=300)}
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(state["s"]))
    monkeypatch.setattr(npc_contact, "contact", lambda ctx, master, cfg: True)
    monkeypatch.setattr(dialogue, "read", lambda: {"options": ["Select an option", "I need another assignment.",
                                                               "Have you any rewards for me?"]})
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None:
                        {"found": True, "clickable": True, "screenX": 614, "screenY": 214})
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False:
                        [_w(426, 14, 6, actions=["Tasks"]), _w(426, 26, 3, text="Cancel task", actions=["Cancel"])])
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append(entity.get("child", "tab"))
        if entity.get("child") == 26:
            state["s"] = dict(NO_TASK, points=270)
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    assert npc_contact.cancel_task(_Ctx(), "KURADAL", CFG) is True
    assert 26 in clicks


def test_cancel_task_is_false_when_the_task_survives_the_click(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(npc_contact, "CANCEL_BUDGET_S", 0.05)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS, points=300))
    monkeypatch.setattr(npc_contact, "contact", lambda ctx, master, cfg: True)
    monkeypatch.setattr(dialogue, "read", lambda: {"options": ["Select an option", "Have you any rewards for me?"]})
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None:
                        {"found": True, "clickable": True, "screenX": 614, "screenY": 214})
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [_w(426, 26, 3, text="Cancel task")])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    assert npc_contact.cancel_task(_Ctx(), "KURADAL", CFG) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_npc_contact.py -q` — Expected: the 6 new tests fail with `AttributeError` (`find_cancel_control`, `cancel_task`).

- [ ] **Step 3: Append to `ControllerAddon/npc_contact.py`**

```python
# ------------------------------------------------------------------ cancelling a task (30 points)
#
# MEASURED 2026-09-19: rewards = interface 426, the Tasks tab = 426.14 dynamic child 6. USER the
# same day: "You can cancel from the spell too". NOT measured: the dialogue option's wording, the
# cancel button's widget, the confirmation step (the account had 9 points). So the button is
# found by the WORD "cancel" inside group 426 and nothing is clicked unless exactly ONE control
# matches. Success is /slayer reading no task -- never the click. UNVERIFIED LIVE.

REWARDS_GROUP = 426
TASKS_TAB = (426, 14, 6)
REWARD_NEEDLES = ("reward",)
CONFIRM_NEEDLES = ("yes", "confirm")     # NOT "cancel": "No, don't cancel" would match it
REWARDS_BUDGET_S = 6.0
CANCEL_BUDGET_S = 6.0


def find_cancel_control(dump):
    """The ONE control in the rewards interface whose text or action says 'cancel', else None."""
    hits = []
    for entry in dump or ():
        if entry.get("group") != REWARDS_GROUP or entry.get("screenX") is None:
            continue
        words = [entry.get("text") or ""] + list(entry.get("actions") or ())
        if any("cancel" in (w or "").lower() for w in words):
            hits.append(entry)
    return hits[0] if len(hits) == 1 else None


def _rewards_open() -> bool:
    return any(e.get("group") == REWARDS_GROUP for e in controller.get_widgets_dump() or ())


def open_rewards(ctx, master, tasks_cfg) -> bool:
    if not contact(ctx, master, tasks_cfg):
        return False
    deadline = time.time() + REWARDS_BUDGET_S
    while time.time() < deadline:
        if _rewards_open():
            return True
        d = dialogue.read()
        opts = dialogue.options(d)
        if opts:
            hit = next((h for h in (dialogue.find_option(opts, n) for n in REWARD_NEEDLES) if h is not None), None)
            if hit is None:
                ctx.state.data["npc_contact_unknown_menu"] = " | ".join(opts)
                debug_log.log(LOG, f"no rewards option in {opts} -- escaping")
                dialogue.close(ctx)
                return False
            controller.key_tap(ctx.ser, str(hit + 1))
        elif dialogue.is_open(d):
            controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.5, 0.9)
    return _rewards_open()


def cancel_task(ctx, master, tasks_cfg) -> bool:
    """Cancel the active task for 30 points through `master`'s rewards screen. True only when
    /slayer then reads no task. Never through the filler master; never without the points."""
    state = slayer_state.from_bridge(controller.get_slayer_task())
    slayer_state.assert_may_contact(master, state, tasks_cfg)
    ok, why = slayer_state.can_skip(state.points, ctx.state.data.get("slayer_skips", 0), tasks_cfg)
    if not ok:
        debug_log.log(LOG, f"not cancelling: {why}")
        return False
    if not open_rewards(ctx, master, tasks_cfg):
        return False
    tab = controller.get_widget(*TASKS_TAB)
    if not tab or not tab.get("clickable"):
        debug_log.log(LOG, f"the Tasks tab {TASKS_TAB} is not clickable")
        dialogue.close(ctx)
        return False
    controller.click_entity(ctx.ser, tab, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    controller.human_delay(0.5, 0.9)
    button = find_cancel_control(controller.get_widgets_dump())
    if button is None:
        debug_log.log(LOG, "no single 'cancel' control in the rewards interface -- closing it untouched")
        dialogue.close(ctx)
        return False
    debug_log.log(LOG, f"cancelling {state.task or state.task_id} for {slayer_state.CANCEL_COST} points "
                       f"(widget {button.get('group')}.{button.get('child')}[{button.get('index')}])")
    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    deadline = time.time() + CANCEL_BUDGET_S
    confirmed_once = False
    while time.time() < deadline:
        after = slayer_state.from_bridge(controller.get_slayer_task())
        if after is not None and not after.has_task:
            ctx.state.data["slayer_skips"] = ctx.state.data.get("slayer_skips", 0) + 1
            dialogue.close(ctx)
            return True
        opts = dialogue.options()
        if opts and not confirmed_once:
            hit = next((h for h in (dialogue.find_option(opts, n) for n in CONFIRM_NEEDLES) if h is not None), None)
            if hit is not None:
                controller.key_tap(ctx.ser, str(hit + 1))
                confirmed_once = True
        time.sleep(POLL_S)
    debug_log.log(LOG, "the task is still active after the cancel click")
    dialogue.close(ctx)
    return False
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m py_compile npc_contact.py; python -m pytest tests/test_npc_contact.py -q` — Expected: 18 passed.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/npc_contact.py ControllerAddon/tests/test_npc_contact.py
git commit -m "npc_contact.cancel_task: 30 points through the rewards screen, confirmed from /slayer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `routines/slayer.py` — the orchestrator

**Files:**
- Modify (replace wholesale): `ControllerAddon/routines/slayer.py`
- Test: `ControllerAddon/tests/test_slayer_routine.py` (new)

**Interfaces:**
- Consumes: `controller.get_slayer_config() -> dict` (its `tasks` block: `fillerMaster`, `milestoneMaster`, `milestoneEvery`, `skipTasks`, `maxSkips`, `pointsReserve`), `controller.get_slayer_task()`, `controller.get_player()`, `controller.check_stray_dialog(ctx)`; `slayer_state.from_bridge`, `next_master`, `can_skip`, `StreakGuardError`; `slayer_tasks.disposition/target_names/at_site/where` and `FIGHT/SKIP/STOP`; `npc_contact.get_task`, `cancel_task`, `missing_requirements`; `combat_core.combat_config(base, names)`, `combat_core.fight_tick(ctx)`, `combat_core.on_event`.
- Produces: module attributes `SKIP_STRAY_DIALOG_CHECK = True`, `VARP_SUBSCRIPTIONS = (394, 395)`, `VARBIT_SUBSCRIPTIONS = (4067, 4068, 4069)`, `get_config`, `on_event`, `tick`, `derive_phase(state, tasks_cfg, player) -> str`, constants `ACQUIRE/SKIP/STOP/FIGHT/GO_THERE`, `CONTACT_MAX_CASTS = 3`.

`combat_core.fight_tick` reads `ctx.state.config`. The orchestrator therefore swaps `ctx.state.config` for the task's combat config around the call and restores it in a `finally`.

- [ ] **Step 1: Write the failing tests**

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import combat_core
import controller
import npc_contact
import slayer_state
import slayer as routine

TASKS = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10, "skipTasks": "",
         "maxSkips": 8, "pointsReserve": 60}
CONFIG = {"tasks": TASKS, "targetNames": ["Cow"], "safespot": {"enabled": True}}


class _State:
    def __init__(self):
        self.data, self.stops, self.config = {}, [], dict(CONFIG)

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self):
        self.ser, self.events, self.state = None, None, _State()


def _task(name, count=20, streak=12, points=200):
    return {"count": count, "hasTask": count > 0, "taskName": name, "taskId": 1, "streak": streak, "points": points}


def test_phase_is_derived_from_live_state_alone():
    s = slayer_state.from_bridge
    assert routine.derive_phase(s(_task(None, count=0)), TASKS, {}) == routine.ACQUIRE
    assert routine.derive_phase(s(_task("Drakes")), TASKS, {}) == routine.SKIP
    assert routine.derive_phase(s(_task("Greater demons")), TASKS, {}) == routine.STOP
    assert routine.derive_phase(s(_task("Banshees")), TASKS, {"x": 3440, "y": 3545, "plane": 0}) == routine.FIGHT
    assert routine.derive_phase(s(_task("Banshees")), TASKS, {"x": 3200, "y": 3200, "plane": 0}) == routine.GO_THERE
    assert routine.derive_phase(s(_task("Nechryael")), TASKS, {"x": 1, "y": 1, "plane": 0}) == routine.GO_THERE
    assert routine.derive_phase(s({"count": 9, "hasTask": True, "taskId": 77}), TASKS, {}) == routine.STOP, \
        "a task whose NAME did not decode is never cancelled and never fought"


def test_an_unavailable_slayer_state_does_nothing(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: None)
    called = []
    monkeypatch.setattr(npc_contact, "get_task", lambda *a: called.append(1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert called == [] and ctx.state.stops == []


def test_acquire_asks_the_master_the_streak_calls_for(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    asked = []

    def get_task(ctx, master, cfg):
        asked.append(master)
        return slayer_state.from_bridge(_task("Bears", count=15))
    monkeypatch.setattr(npc_contact, "get_task", get_task)
    for streak, master in ((12, "AYA"), (19, "KURADAL"), (9, "KURADAL"), (20, "AYA")):
        monkeypatch.setattr(controller, "get_slayer_task", lambda s=streak: _task(None, count=0, streak=s))
        routine.tick(_Ctx())
        assert asked[-1] == master


def test_a_missing_requirement_is_a_safety_stop_naming_it(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: "NPC Contact needs 1 astral rune(s) per cast (have 0)")
    cast = []
    monkeypatch.setattr(npc_contact, "get_task", lambda *a: cast.append(1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert cast == [] and "astral" in ctx.state.stops[0]


def test_failed_casts_have_a_ceiling_because_each_one_spends_runes(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    monkeypatch.setattr(routine.time, "time", lambda: 1e9)
    attempts = []
    monkeypatch.setattr(npc_contact, "get_task", lambda ctx, master, cfg: attempts.append(1))
    ctx = _Ctx()
    for _ in range(routine.CONTACT_MAX_CASTS + 2):
        ctx.state.data["slayer_contact_at"] = 0.0
        routine.tick(ctx)
    assert len(attempts) == routine.CONTACT_MAX_CASTS
    assert ctx.state.stops and "NPC Contact" in ctx.state.stops[0]


def test_an_unknown_dialogue_menu_is_reported_in_the_stop_message(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task(None, count=0))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)

    def get_task(ctx, master, cfg):
        ctx.state.data["npc_contact_unknown_menu"] = "Yes, replace my task. | No."
        return None
    monkeypatch.setattr(npc_contact, "get_task", get_task)
    ctx = _Ctx()
    routine.tick(ctx)
    assert ctx.state.stops and "replace my task" in ctx.state.stops[0], "an unknown menu stops at once, no retry"


def test_skip_cancels_through_the_milestone_master_and_counts_per_slot(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Drakes", points=300))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    used = []
    monkeypatch.setattr(npc_contact, "cancel_task", lambda ctx, master, cfg: used.append(master) or True)
    routine.tick(_Ctx())
    assert used == ["KURADAL"], "rewards are never opened through the filler master"


def test_skip_without_the_points_is_a_safety_stop_not_a_fight(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Drakes", points=70))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    cancelled = []
    monkeypatch.setattr(npc_contact, "cancel_task", lambda *a: cancelled.append(1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert cancelled == [] and "drakes" in ctx.state.stops[0].lower() and "points" in ctx.state.stops[0]


def test_stop_names_the_task(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Greater demons"))
    monkeypatch.setattr(controller, "get_player", lambda: {})
    ctx = _Ctx()
    routine.tick(ctx)
    assert "greater demons" in ctx.state.stops[0].lower()


def test_away_from_the_site_it_stops_and_says_where_to_go(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    ctx = _Ctx()
    routine.tick(ctx)
    assert "bears" in ctx.state.stops[0].lower() and "Legends" in ctx.state.stops[0]


def test_at_the_site_it_fights_with_the_tasks_monsters_and_restores_the_config(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    seen = []
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(
        (list(ctx.state.config["targetNames"]), ctx.state.config["safespot"]["enabled"])))
    ctx = _Ctx()
    routine.tick(ctx)
    assert seen == [(["banshees"], False)]
    assert ctx.state.config["targetNames"] == ["Cow"], "the panel config comes back even if the fight raises"
    assert ctx.state.stops == []


def test_a_new_task_resets_the_per_slot_counters(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees", streak=13))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: None)
    ctx = _Ctx()
    ctx.state.data.update({"slayer_casts": 2, "slayer_skips": 3, "slayer_streak_seen": 12})
    routine.tick(ctx)
    assert ctx.state.data["slayer_casts"] == 0 and ctx.state.data["slayer_skips"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_slayer_routine.py -q` — Expected: failures (`AttributeError: module 'slayer' has no attribute 'derive_phase'`).

- [ ] **Step 3: Replace `ControllerAddon/routines/slayer.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m py_compile routines/slayer.py; python -m pytest tests/test_slayer_routine.py tests/test_combat.py -q` — Expected: all pass (12 new).

Two things to check rather than work around: `test_a_new_task_resets_the_per_slot_counters` expects BOTH counters at 0 after one tick in which the streak changed from 12 to 13. And `test_failed_casts_have_a_ceiling...` patches `routine.time.time` to a constant and zeroes `slayer_contact_at` before each tick so the cooldown never blocks; if it fails, report the attempt count.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/routines/slayer.py ControllerAddon/tests/test_slayer_routine.py
git commit -m "routines/slayer.py: acquire, skip, stop, and fight at a known site

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md` (the `slayer.py` row of the routine status table)
- Modify: `docs/routine-notes.md` (the slayer section: append a dated paragraph)
- Modify: `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` (§13 item 2)

- [ ] **Step 1: CLAUDE.md** — replace the `slayer.py` table row with:

```
| `slayer.py` | Task loop, sub-project 2 (2026-09-19): gets tasks by NPC Contact (Kuradal every 10th, Aya otherwise), cancels skip-list tasks, fights at a known Aya site, otherwise STOPS and says where to go -- travel/banking are sub-project 3. NOT run live |
```

- [ ] **Step 2: docs/routine-notes.md** — under the slayer heading append:

```markdown
**2026-09-19, sub-project 2 (acquire + skip), NOT run live.** `npc_contact.py` finds the master
in the picker by its ACTION (`Contact Aya`) from `/widgets/dump` -- a row's text and name are
empty, MEASURED -- and walks the dialogue by option TEXT. First-live-run checklist, in order:
(1) the Vannaka decline wording (`DECLINE_NEEDLES` is the wiki's; an unknown menu is escaped and
its options are printed in the safety stop, so a wrong guess costs one cast, never a wrong
answer); (2) the rewards option wording and the cancel control in interface 426 (found by the
word "cancel", refused unless exactly one matches); (3) whether the picker ever needs
scrolling; (4) `F6` being this account's Magic tab key. Measured facts:
`docs/slayer-sites/recordings-2026-09-19.md`.
```

- [ ] **Step 3: the spec** — in §13 item 2, append: ` **Built 2026-09-19** (`docs/superpowers/plans/2026-09-19-slayer-acquire-and-skip.md`), NOT run live; FIGHT is limited to Aya sites the player is already standing in.`

- [ ] **Step 4: Full verification**

```powershell
python -m py_compile slayer_tasks.py npc_contact.py routines/slayer.py
python -m pytest tests/ -q
```
Expected: only the baseline failures from Global Constraints. Record the failed-test names.

- [ ] **Step 5: Commit**

```powershell
git add CLAUDE.md docs/routine-notes.md docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md
git commit -m "docs: the slayer routine gets and cancels tasks; first-live-run checklist

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review against the spec

- §7.1 phase derivation: Task 4 (`derive_phase`), with BANK/TRAVEL/RESTOCK collapsed into `GO_THERE` = stop-and-say-where, by design (sub-project 3).
- §7.2 ACQUIRE: `next_master`, preconditions as safety stops, cast ceiling 3, F2 guard raising before any input (Tasks 2, 4). Confirmation is a `/slayer` poll rather than `await_outcome`: the dialogue loop already reads state every step, and the assignment lands mid-dialogue.
- §7.4 SKIP: points reserve and per-slot ceiling from `slayer_state.can_skip`, through the MILESTONE master only, confirmed from `/slayer` (Tasks 3, 4). The in-person Burthorpe fallback is NOT built (needs travel).
- §9: picker rows by action, dialogue by text, unknown menu = ESC + stop with the options printed.
- Type consistency: `npc_contact.get_task(ctx, master, tasks_cfg)` and `cancel_task(ctx, master, tasks_cfg)` are called with exactly those arguments in Task 4; `slayer_state.from_bridge` returns the namedtuple whose `.task` is lowercased, which `slayer_tasks` lowercases again harmlessly.
