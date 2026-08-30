# Giants' Foundry Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `routines/giants_foundry.py` — runs Giants' Foundry commissions unattended: commission → moulds → crucible → pour → pick up → refine → hand in → repeat.

**Architecture:** One routine module. Pure logic at the top (mould score table + argmax, value-aware fill planner, heat targeting math) — plain functions, no I/O, fully unit-tested. Below it a poll-driven phase dispatcher keyed on `GET /api/minigames/giants-foundry`'s `phase` field, with per-phase handlers that issue at most one action decision per tick (the heat-adjust handler runs one bounded inner poll loop to time the machine click, `mark_busy`-guarded). All counters live in `ctx.state.data` and reset where success is *detected* in `tick()`, never where the attempt is made.

**Tech Stack:** Python (ControllerAddon), pytest with monkeypatch stubs (hermetic — no live bridge). Two one-line Java edits (RoutineChoice enum, PlayerProvider smithingXp) in the final task.

**Spec:** `docs/superpowers/specs/2026-08-31-giants-foundry-design.md` — read it first; it carries the recording-measured facts (`ControllerAddon/recordings/2026-08-31_003238_giants-foundry.*`) every constant here traces to.

## Global Constraints

- Run all Python commands from `ControllerAddon/` (`cd ControllerAddon` first). Tests: `python -m pytest tests/test_giants_foundry.py -v`.
- Never edit anything under `src/main/java/net/runelite/client/plugins/m8aq/` (vendored, sync-overwritten).
- The API JSON keys are the Java **field** names of `GiantsFoundry.State` (Gson serializes fields, not getters): `phase`, `commissionTypes`, `crucibleContents`, `crucibleBarCount`, `crucibleState`, `selectedMoulds`, `usableMoulds`, `preformState`, `temperature`, `completion`, `quality`, `startingQuality`, `qualityLost`, `difficulty`, `sectionCount`, `currentSection`, `toolSequence`, `requiredTool`, `minimumTemperature`, `maximumTemperature`, `temperatureStatus`, `sweetSpotState`, `readyToHandIn`, `activity`, `atGiantsFoundry`. Computed getters like `isMouldSelectionComplete()` do NOT appear in the JSON — derive them in Python (`len(selectedMoulds) == 3`, `crucibleBarCount >= 28`). Enums serialize by name (`"TRIP_HAMMER"`, `"RICASSO"`, `"MITHRIL"`).
- `controller.get_api(...)` returns `None` = "cannot tell" (never "empty"); a dict with an `"error"` key = reader failed. Both must defer, never act.
- Every loop has a ceiling; every ceiling's counter resets at detected success (a varbit/phase change read back), not at the attempt.
- Simulated misclicks exist (`MISTAKE_MISCLICK_CHANCE`): never treat one failed click as a logic error.
- Before any live run: `python -m py_compile` every touched file AND the full `python -m pytest tests/` (pytest alone misses modules no test imports).
- Commit after every task with the message given in that task.

## File Structure

- Create `ControllerAddon/routines/giants_foundry.py` — grown across Tasks 1–9 (pure logic first, then runtime).
- Create `ControllerAddon/tests/test_giants_foundry.py` — grown across the same tasks.
- Modify (Task 10 only): `src/main/java/com/example/controller/RoutineChoice.java`, `ControllerAddon/controller.py` (`ROUTINE_FILES`), `src/main/java/com/example/providers/PlayerProvider.java`.

---

### Task 1: Module scaffold + mould score table + best_moulds

**Files:**
- Create: `ControllerAddon/routines/giants_foundry.py`
- Create: `ControllerAddon/tests/test_giants_foundry.py`

**Interfaces:**
- Produces: `MOULDS: dict[str, tuple[str, str, dict[str, int]]]` (enum name → (display name, part, descriptor scores)), `MOULD_BY_DISPLAY: dict[str, str]` (lowercased display → enum), `mould_score(enum_name, commission: list[str]) -> int`, `best_moulds(commission: list[str], usable: list[str]) -> dict[str, str]` (part `"RICASSO"|"BLADE"|"TIP"` → enum name), `DEFAULT_CONFIG`, `get_config(bridge_config)`.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_giants_foundry.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import time

import pytest

import giants_foundry as gf
import controller
import banker


# ---------------------------------------------------------------------------
# Task 1: mould selection
# ---------------------------------------------------------------------------

ALL_MOULDS = list(gf.MOULDS)

# The account's unlocks during the 2026-08-31 recording: all 18 defaults plus the five
# purchasables its picks prove it owned (SPIKER notably absent).
RECORDING_UNLOCKS = [m for m, v in gf.MOULDS.items() if v[3] is False] + [
    "JUGGERNAUT_FORTE", "CHOPPER_FORTE_PLUS_ONE", "FLAMBERGE_BLADE",
    "SERPENT_BLADE", "NEEDLE_POINT",
]


def test_mould_table_shape():
    # 32 moulds, three parts, every score positive, display names unique
    assert len(gf.MOULDS) == 32
    parts = {"RICASSO": 0, "BLADE": 0, "TIP": 0}
    displays = set()
    for enum, (display, part, scores, purchasable) in gf.MOULDS.items():
        parts[part] += 1
        assert display not in displays
        displays.add(display)
        assert scores and all(v > 0 for v in scores.values())
    assert parts == {"RICASSO": 11, "BLADE": 11, "TIP": 10}


def test_best_moulds_narrow_flat_matches_recording():
    # MEASURED 2026-08-31 (recording, second sword): the human picked exactly these.
    # Needle Point ties The Point! at 22; stable max keeps table order (Needle Point first),
    # which is also what the human chose.
    best = gf.best_moulds(["NARROW", "FLAT"], ALL_MOULDS)
    assert best == {"RICASSO": "CHOPPER_FORTE_PLUS_ONE",
                    "BLADE": "SERPENT_BLADE",
                    "TIP": "NEEDLE_POINT"}


def test_best_moulds_narrow_spiked_matches_recording_unlocks():
    # First sword: SPIKER (the global best forte at 23) was not unlocked; with the
    # recording's unlock set the argmax is what the human picked.
    best = gf.best_moulds(["NARROW", "SPIKED"], RECORDING_UNLOCKS)
    assert best == {"RICASSO": "JUGGERNAUT_FORTE",
                    "BLADE": "FLAMBERGE_BLADE",
                    "TIP": "NEEDLE_POINT"}


def test_best_moulds_empty_usable_falls_back_to_all():
    # usableMoulds == [] means "cannot tell" from an un-rebuilt client, not "nothing usable".
    best = gf.best_moulds(["LIGHT", "FLAT"], [])
    assert set(best) == {"RICASSO", "BLADE", "TIP"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: FAIL / collection error — `giants_foundry` module does not exist.

- [ ] **Step 3: Write the module scaffold + table**

Create `ControllerAddon/routines/giants_foundry.py`:

```python
# routines/giants_foundry.py
#
# Giants' Foundry: run sword commissions unattended -- commission from Kovac, best moulds
# for the commission, fill the crucible from the Foundry bank chest, pour, pick up, refine
# (hammer/grind/polish at the right temperature, claiming sweet spots), hand in, repeat.
#
# Design: docs/superpowers/specs/2026-08-31-giants-foundry-design.md
# Measured facts: ControllerAddon/recordings/2026-08-31_003238_giants-foundry.*
#
# Run with:
#     python controller.py routines/giants_foundry.py
#
# Ground truth at runtime is GET /api/minigames/giants-foundry (the vendored m8aq reader):
# phase, commission, crucible varbits, mould selections, tool sequence, temperature band,
# sweet-spot state. Nothing load-bearing lives in ctx.state.data -- every phase re-derives
# from live varbits, so restarts mid-sword resume correctly (STORED preforms included).

import math
import time

import banker
import controller
import debug_log

DEFAULT_CONFIG = {
    # Value-aware crucible fill plan (the recording's own loadout): items usable on the
    # crucible, the metal they melt into, bars per item (wiki: equipment = smithing bar
    # cost minus 1), and how many bars of that metal the crucible should end up holding.
    "fill": [
        {"item": "Mithril platebody", "metal": "MITHRIL", "bars_each": 4, "target_bars": 14},
        {"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 14},
    ],
    # Item quantization can overflow the 28-bar cap (MEASURED: 4-bar platebody into 2 bars
    # of space -> confirm dialog, 2 bars lost). Accept the confirm iff the loss is <= this.
    "max_overflow_loss": 2,

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
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # No Controller Settings panel section (gemstone_crab pattern) -- config lives here.
    return DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Pure logic: mould selection
# ---------------------------------------------------------------------------

# Per-mould commission-descriptor scores, ported verbatim from the easy-giantsfoundry
# Plugin Hub plugin's Mould enum (commit 9324c7f, read 2026-08-31 via the osrs-wiki MCP)
# -- the same numbers the game's own mould interface displays. Enum names match OUR
# vendored reader's Mould enum (m8aq GiantsFoundry.java), which is what selectedMoulds/
# usableMoulds serialize; display names are what the Select menu entry carries.
# Tuple: (display name, part, {descriptor: score}, purchasable).
# VALIDATED against the recording: argmax reproduces the human's Narrow Flat picks exactly.
MOULDS = {
    # RICASSO (forte)
    "CHOPPER_FORTE":          ("Chopper Forte", "RICASSO", {"BROAD": 4, "LIGHT": 4, "FLAT": 4}, False),
    "GLADIUS_RICASSO":        ("Gladius Ricasso", "RICASSO", {"BROAD": 4, "HEAVY": 4, "FLAT": 4}, False),
    "DISARMING_FORTE":        ("Disarming Forte", "RICASSO", {"NARROW": 4, "LIGHT": 4, "SPIKED": 4}, False),
    "MEDUSA_RICASSO":         ("Medusa Ricasso", "RICASSO", {"BROAD": 8, "HEAVY": 6, "FLAT": 8}, False),
    "SERPENT_RICASSO":        ("Serpent Ricasso", "RICASSO", {"NARROW": 6, "LIGHT": 8, "FLAT": 8}, False),
    "SERRATED_FORTE":         ("Serrated Forte", "RICASSO", {"NARROW": 8, "HEAVY": 8, "SPIKED": 6}, False),
    "STILETTO_FORTE":         ("Stiletto Forte", "RICASSO", {"NARROW": 8, "LIGHT": 10, "FLAT": 8}, True),
    "DEFENDER_BASE":          ("Defender Base", "RICASSO", {"BROAD": 8, "HEAVY": 10, "FLAT": 8}, True),
    "JUGGERNAUT_FORTE":       ("Juggernaut Forte", "RICASSO", {"BROAD": 4, "HEAVY": 4, "SPIKED": 16}, True),
    "CHOPPER_FORTE_PLUS_ONE": ("Chopper Forte +1", "RICASSO", {"BROAD": 3, "LIGHT": 4, "FLAT": 18}, True),
    "SPIKER":                 ("Spiker!", "RICASSO", {"NARROW": 1, "HEAVY": 2, "SPIKED": 22}, True),
    # BLADE
    "SAW_BLADE":       ("Saw Blade", "BLADE", {"BROAD": 4, "LIGHT": 4, "SPIKED": 4}, False),
    "DEFENDERS_EDGE":  ("Defenders Edge", "BLADE", {"BROAD": 4, "HEAVY": 4, "SPIKED": 4}, False),
    "FISH_BLADE":      ("Fish Blade", "BLADE", {"NARROW": 4, "LIGHT": 4, "FLAT": 4}, False),
    "MEDUSA_BLADE":    ("Medusa Blade", "BLADE", {"BROAD": 8, "HEAVY": 8, "FLAT": 6}, False),
    "STILETTO_BLADE":  ("Stiletto Blade", "BLADE", {"NARROW": 8, "LIGHT": 6, "FLAT": 8}, False),
    "GLADIUS_EDGE":    ("Gladius Edge", "BLADE", {"NARROW": 6, "HEAVY": 8, "FLAT": 8}, False),
    "FLAMBERGE_BLADE": ("Flamberge Blade", "BLADE", {"NARROW": 8, "LIGHT": 8, "SPIKED": 10}, True),
    "SERPENT_BLADE":   ("Serpent Blade", "BLADE", {"NARROW": 10, "LIGHT": 8, "FLAT": 8}, True),
    "CLAYMORE_BLADE":  ("Claymore Blade", "BLADE", {"BROAD": 16, "HEAVY": 4, "FLAT": 4}, True),
    "FLEUR_DE_BLADE":  ("Fleur de Blade", "BLADE", {"BROAD": 4, "HEAVY": 18, "SPIKED": 1}, True),
    "CHOPPA":          ("Choppa!", "BLADE", {"BROAD": 1, "LIGHT": 22, "FLAT": 2}, True),
    # TIP
    "PEOPLE_POKER_POINT": ("People Poker Point", "TIP", {"NARROW": 4, "HEAVY": 4, "FLAT": 4}, False),
    "CHOPPER_TIP":        ("Chopper Tip", "TIP", {"BROAD": 4, "LIGHT": 4, "SPIKED": 4}, False),
    "MEDUSAS_HEAD":       ("Medusa's Head", "TIP", {"BROAD": 4, "HEAVY": 4, "SPIKED": 4}, False),
    "SERPENTS_FANG":      ("Serpent's Fang", "TIP", {"NARROW": 8, "LIGHT": 6, "SPIKED": 8}, False),
    "GLADIUS_POINT":      ("Gladius Point", "TIP", {"NARROW": 8, "HEAVY": 8, "FLAT": 6}, False),
    "SAW_TIP":            ("Saw Tip", "TIP", {"BROAD": 6, "HEAVY": 8, "SPIKED": 8}, False),
    "CORRUPTED_POINT":    ("Corrupted Point", "TIP", {"NARROW": 8, "LIGHT": 10, "SPIKED": 8}, True),
    "DEFENDERS_TIP":      ("Defenders Tip", "TIP", {"BROAD": 10, "HEAVY": 8, "SPIKED": 8}, True),
    "SERRATED_TIP":       ("Serrated Tip", "TIP", {"NARROW": 4, "LIGHT": 16, "SPIKED": 4}, True),
    "NEEDLE_POINT":       ("Needle Point", "TIP", {"NARROW": 18, "LIGHT": 3, "FLAT": 4}, True),
    "THE_POINT":          ("The Point!", "TIP", {"BROAD": 2, "LIGHT": 1, "FLAT": 22}, True),
}

MOULD_BY_DISPLAY = {display.lower(): enum for enum, (display, _, _, _) in MOULDS.items()}

MOULD_PARTS = ("RICASSO", "BLADE", "TIP")


def mould_score(enum_name, commission):
    scores = MOULDS[enum_name][2]
    return sum(scores.get(word, 0) for word in commission)


def best_moulds(commission, usable):
    """Argmax mould per part for this commission (list of descriptor names like
    ["NARROW","FLAT"]). `usable` is the API's usableMoulds (unlocks x Smithing level);
    an EMPTY list means "cannot tell" (un-rebuilt client) and falls back to the full
    catalogue rather than refusing to work. Ties keep table order (stable max via >),
    which matches both the wiki's listing and the recorded human choice."""
    pool = set(usable) if usable else set(MOULDS)
    best = {}
    for enum_name in MOULDS:
        part = MOULDS[enum_name][1]
        if enum_name not in pool:
            continue
        s = mould_score(enum_name, commission)
        if part not in best or s > best[part][0]:
            best[part] = (s, enum_name)
    return {part: pick[1] for part, pick in best.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: scaffold + mould score table + best_moulds (recording-pinned)"
```

---

### Task 2: Fill planner

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (append after mould logic)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Consumes: `DEFAULT_CONFIG["fill"]` entry shape from Task 1.
- Produces: `CRUCIBLE_CAPACITY = 28`, `plan_fill(fill_cfg: list[dict], crucible_contents: dict[str, int], max_overflow_loss: int) -> tuple[list[dict], str | None]` — steps `{"item": str, "bars_each": int, "count": int}` ordered largest `bars_each` first, or `([], error_string)`. Empty steps + no error = crucible already satisfied → pour.

- [ ] **Step 1: Write the failing tests** (append to test file)

```python
# ---------------------------------------------------------------------------
# Task 2: fill planner
# ---------------------------------------------------------------------------

FILL = DEFAULT_FILL = [
    {"item": "Mithril platebody", "metal": "MITHRIL", "bars_each": 4, "target_bars": 14},
    {"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 14},
]


def test_plan_fill_from_empty_matches_recording():
    # MEASURED: 14 mith via 4-bar bodies quantizes to 4 items (16 bars) -> 2 bars of
    # planned overflow at the 28 cap, which the human accepted. Loss 2 <= cap 2 -> ok.
    steps, err = gf.plan_fill(FILL, {}, max_overflow_loss=2)
    assert err is None
    assert steps == [
        {"item": "Mithril platebody", "bars_each": 4, "count": 4},
        {"item": "Adamant platelegs", "bars_each": 2, "count": 7},
    ]


def test_plan_fill_rejects_overflow_beyond_cap():
    steps, err = gf.plan_fill(FILL, {}, max_overflow_loss=1)
    assert steps == []
    assert "overflow" in err


def test_plan_fill_resumes_partial_crucible():
    # Mid-fill restart: 12 mithril + 14 adamant already in -> one more body finishes it.
    steps, err = gf.plan_fill(FILL, {"MITHRIL": 12, "ADAMANT": 14}, max_overflow_loss=2)
    assert err is None
    assert steps == [{"item": "Mithril platebody", "bars_each": 4, "count": 1}]


def test_plan_fill_full_crucible_is_done():
    steps, err = gf.plan_fill(FILL, {"MITHRIL": 14, "ADAMANT": 14}, max_overflow_loss=2)
    assert steps == [] and err is None


def test_plan_fill_short_config_errors():
    short = [{"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 20}]
    steps, err = gf.plan_fill(short, {}, max_overflow_loss=2)
    assert steps == []
    assert "20/28" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k plan_fill -v`
Expected: FAIL — `plan_fill` not defined.

- [ ] **Step 3: Implement** (append to module)

```python
# ---------------------------------------------------------------------------
# Pure logic: crucible fill planning
# ---------------------------------------------------------------------------

CRUCIBLE_CAPACITY = 28


def plan_fill(fill_cfg, crucible_contents, max_overflow_loss):
    """What still needs adding, from the LIVE crucible varbits (crucibleContents) --
    re-derived every tick, no local estimate (the fur-pouch lesson). Returns
    (steps, error): steps ordered largest bars_each first so the 28-bar cap (and any
    quantization loss) lands on the last, cheapest item; ([], None) means the crucible
    is satisfied; ([], "reason") is a config/stock problem worth a safety stop.
    Item counts quantize UP (ceil) -- the game caps the final item at remaining space
    and asks to confirm the loss (MEASURED: 2 bars lost on a 4-bar platebody into 2
    bars of space); the loss is accepted at execution time iff <= max_overflow_loss."""
    contents = crucible_contents or {}
    space = CRUCIBLE_CAPACITY - sum(contents.values())
    if space <= 0:
        return [], None

    steps = []
    planned_bars = 0
    for entry in fill_cfg:
        remaining = entry["target_bars"] - contents.get(entry["metal"], 0)
        if remaining <= 0:
            continue
        count = math.ceil(remaining / entry["bars_each"])
        steps.append({"item": entry["item"], "bars_each": entry["bars_each"], "count": count})
        planned_bars += count * entry["bars_each"]

    if not steps:
        return [], None
    if planned_bars < space:
        total = CRUCIBLE_CAPACITY - space + planned_bars
        return [], (f"fill plan only reaches {total}/{CRUCIBLE_CAPACITY} bars -- "
                    "raise the fill config's target_bars")
    loss = planned_bars - space
    if loss > max_overflow_loss:
        return [], (f"fill plan would overflow {loss} bars "
                    f"(> max_overflow_loss {max_overflow_loss}) -- adjust the fill config")
    steps.sort(key=lambda s: -s["bars_each"])
    return steps, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: value-aware crucible fill planner"
```

---

### Task 3: Heat targeting math

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (append)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Produces: `TOOL_STATS: dict[str, dict]` (keys `"TRIP_HAMMER"|"GRINDSTONE"|"POLISHING_WHEEL"`, fields `cycle_ticks`, `progress`, `heat`, `object`), `actions_left_in_section(completion: int, section_count: int, tool: str) -> int`, `heat_stop_target(tool: str, min_t: int, max_t: int, actions_left: int) -> int`, `HEAT_STOP_MARGIN = 35`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------------------
# Task 3: heat targeting
# ---------------------------------------------------------------------------

def test_tool_stats_are_the_measured_constants():
    # easy-giantsfoundry Stage.java, CONFIRMED live in the recording (hammer +20/-25 per
    # 5 ticks at 916->726; polish +10 per 2 ticks).
    assert gf.TOOL_STATS["TRIP_HAMMER"] == {"cycle_ticks": 5, "progress": 20, "heat": -25, "object": 44619}
    assert gf.TOOL_STATS["GRINDSTONE"] == {"cycle_ticks": 2, "progress": 10, "heat": 15, "object": 44620}
    assert gf.TOOL_STATS["POLISHING_WHEEL"] == {"cycle_ticks": 2, "progress": 10, "heat": -17, "object": 44621}


def test_actions_left_in_section():
    # 5 sections -> 200 per section. At completion 140, 60 to go: 3 hammer actions
    # (20 each) or 6 grind actions (10 each).
    assert gf.actions_left_in_section(140, 5, "TRIP_HAMMER") == 3
    assert gf.actions_left_in_section(140, 5, "GRINDSTONE") == 6
    # Exactly on a boundary: the whole next section remains.
    assert gf.actions_left_in_section(200, 5, "TRIP_HAMMER") == 10
    # No preform data -> 0, never a division by zero.
    assert gf.actions_left_in_section(0, 0, "TRIP_HAMMER") == 0


def test_heat_stop_target_enters_band_at_the_working_end():
    # Hammer cools (-25/action): enter near the TOP of the band so the whole section
    # rides down through it. 8 actions from min 830 -> 830+200=1030, clamped to max.
    assert gf.heat_stop_target("TRIP_HAMMER", 830, 1000, 8) == 1000
    # 3 actions -> 830+75 = 905, inside the band -> unclamped.
    assert gf.heat_stop_target("TRIP_HAMMER", 830, 1000, 3) == 905
    # Grindstone heats (+15): enter near the BOTTOM. 4 actions from max 590 -> 530.
    assert gf.heat_stop_target("GRINDSTONE", 410, 590, 4) == 530
    # Never below min.
    assert gf.heat_stop_target("GRINDSTONE", 410, 590, 20) == 410
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k "tool_stats or actions_left or stop_target" -v`
Expected: FAIL — names not defined.

- [ ] **Step 3: Implement** (append)

```python
# ---------------------------------------------------------------------------
# Pure logic: refinement heat targeting
# ---------------------------------------------------------------------------

# Per-machine constants ported from easy-giantsfoundry's Stage.java and CONFIRMED in the
# recording: the trip hammer acts every 5 game ticks for +20 progress / -25 heat; the
# grindstone and polishing wheel act every 2 ticks for +10 progress and +15 / -17 heat.
# Passive decay is -1 heat per 2 ticks. `object` is the machine's game-object id.
TOOL_STATS = {
    "TRIP_HAMMER":     {"cycle_ticks": 5, "progress": 20, "heat": -25, "object": 44619},
    "GRINDSTONE":      {"cycle_ticks": 2, "progress": 10, "heat": 15, "object": 44620},
    "POLISHING_WHEEL": {"cycle_ticks": 2, "progress": 10, "heat": -17, "object": 44621},
}

# Stop the fast heat/cool this far short of the computed target: the Dunk/Quench ramp
# runs +-27..33 per tick at the point we ride it (easy-giantsfoundry's measured DX_1
# curve from index 10; CONFIRMED in the recording, 724->751->780->813), so one poll of
# latency costs about one fast tick.
HEAT_STOP_MARGIN = 35


def actions_left_in_section(completion, section_count, tool):
    """How many actions of `tool` until the next section boundary (progress is on the
    0-1000 scale, sections are equal slices)."""
    if not section_count:
        return 0
    per = 1000.0 / section_count
    till_next = per - (completion % per)
    if till_next <= 0:
        till_next = per
    return max(1, math.ceil(till_next / TOOL_STATS[tool]["progress"]))


def heat_stop_target(tool, min_t, max_t, actions_left):
    """The band edge to ENTER at so the whole remaining section can run without another
    lava/waterfall trip (easy-giantsfoundry's targeting): cooling tools (hammer, polish)
    enter near the top and ride down; the grindstone (heats) enters near the bottom.
    Clamped into the band -- overshooting the far edge would damage the sword."""
    delta = TOOL_STATS[tool]["heat"]
    if delta < 0:
        target = min_t - delta * actions_left
    else:
        target = max_t - delta * actions_left
    return max(min_t, min(max_t, target))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: heat targeting math (ported Stage constants + band-end targets)"
```

---

### Task 4: State read, tick dispatcher, counter resets

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (append)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append test ctx helpers + tests)

**Interfaces:**
- Consumes: `controller.get_api(group, reader)`, `ctx.state.data` dict, `ctx.state.trigger_safety_stop(reason)`.
- Produces: `read_state() -> dict | None`, `tick(ctx)`, `PHASE_HANDLERS: dict[str, callable]`, `_reset_counters(data, st, phase)`, constants `STATE_UNAVAILABLE_LIMIT = 20`, and the phase handler function *names* the later tasks fill in: `get_commission_phase(ctx, st)`, `prepare_phase(ctx, st)`, `pickup_phase(ctx, st)`, `refine_phase(ctx, st)`, `hand_in_phase(ctx, st)` (stub bodies `pass` for now, replaced in Tasks 5–9).

- [ ] **Step 1: Write the failing tests** (append; the `_Ctx`/`_no_live_bridge` helpers here are used by every later task)

```python
# ---------------------------------------------------------------------------
# Test harness for the runtime half (Tasks 4+)
# ---------------------------------------------------------------------------

class _State:
    def __init__(self, config=None):
        self.config = config or dict(gf.DEFAULT_CONFIG)
        self.data = {}
        self.stopped = None

    def trigger_safety_stop(self, reason):
        if self.stopped is None:
            self.stopped = reason


class _Ctx:
    def __init__(self, config=None):
        self.ser = None
        self.state = _State(config)
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic suite (house rule): stub every controller call that would reach the
    running client or the Arduino. Defaults are the documented 'cannot tell' answers."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [])
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: {})
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "type_text", lambda ser, text: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "mark_busy", lambda s: None)
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "npc_index_refresh_target",
                        lambda idx: (lambda: None))
    monkeypatch.setattr(gf.time, "sleep", lambda s: None)


def _st(**over):
    """A minimal healthy API snapshot; override per test."""
    base = {
        "phase": "REFINING", "commissionTypes": ["NARROW", "FLAT"],
        "crucibleContents": {}, "crucibleBarCount": 0, "crucibleState": "EMPTY",
        "selectedMoulds": {}, "usableMoulds": [],
        "preformState": "EQUIPPED", "temperature": 900, "completion": 100,
        "quality": 147, "startingQuality": 147, "qualityLost": 0,
        "difficulty": 95, "sectionCount": 5, "currentSection": 1,
        "toolSequence": ["TRIP_HAMMER", "GRINDSTONE", "POLISHING_WHEEL",
                          "GRINDSTONE", "TRIP_HAMMER"],
        "requiredTool": "TRIP_HAMMER",
        "minimumTemperature": 830, "maximumTemperature": 1000,
        "temperatureStatus": "READY", "sweetSpotState": "NONE",
        "readyToHandIn": False, "activity": "NONE", "atGiantsFoundry": True,
    }
    base.update(over)
    return base


def _serve_state(monkeypatch, st):
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: st if (g, r) == ("minigames", "giants-foundry") else None)


# ---------------------------------------------------------------------------
# Task 4: dispatcher
# ---------------------------------------------------------------------------

def test_read_state_error_and_none_mean_unavailable(monkeypatch):
    assert gf.read_state() is None                      # fixture default: get_api -> None
    _serve_state(monkeypatch, {"error": "boom", "reader": "GiantsFoundry"})
    assert gf.read_state() is None


def test_tick_unavailable_state_defers_then_stops(monkeypatch):
    ctx = _Ctx()
    for _ in range(gf.STATE_UNAVAILABLE_LIMIT - 1):
        gf.tick(ctx)
    assert ctx.state.stopped is None
    gf.tick(ctx)
    assert "unavailable" in ctx.state.stopped


def test_tick_inactive_safety_stops(monkeypatch):
    ctx = _Ctx()
    _serve_state(monkeypatch, _st(phase="INACTIVE"))
    gf.tick(ctx)
    assert "not at the foundry" in ctx.state.stopped.lower()


def test_tick_routes_every_known_phase(monkeypatch):
    seen = []
    for phase in ("AWAITING_COMMISSION", "PREPARING", "SELECTING_MOULDS", "MOULD_SET",
                  "PREFORM_READY", "COLLECTING_PREFORM", "REFINING", "STORED",
                  "READY_TO_HAND_IN", "RUINED"):
        assert phase in gf.PHASE_HANDLERS, phase


def test_tick_resets_counters_where_success_is_detected(monkeypatch):
    ctx = _Ctx()
    d = ctx.state.data
    d["gf_commission_attempts"] = 3
    d["gf_handin_attempts"] = 4
    d["gf_pickup_attempts"] = 2
    d["gf_fill_attempts"] = 2
    d["gf_last_bars"] = 4
    # A REFINING read with a commission present and more bars than last time clears all
    # the counters whose success it proves.
    _serve_state(monkeypatch, _st(crucibleBarCount=8))
    monkeypatch.setitem(gf.PHASE_HANDLERS, "REFINING", lambda ctx, st: None)
    gf.tick(ctx)
    assert d["gf_commission_attempts"] == 0
    assert d["gf_handin_attempts"] == 0      # phase is not READY_TO_HAND_IN/RUINED
    assert d["gf_pickup_attempts"] == 0      # phase is not PREFORM_READY/COLLECTING
    assert d["gf_fill_attempts"] == 0        # bar count grew
    assert d["gf_last_bars"] == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k "tick or read_state" -v`
Expected: FAIL — `read_state`/`tick` not defined.

- [ ] **Step 3: Implement** (append)

```python
# ---------------------------------------------------------------------------
# Runtime: state access + dispatch
# ---------------------------------------------------------------------------

KOVAC_NPC_ID = 11472
BANK_CHEST_ID = 44630
LAVA_POOL_ID = 44631
WATERFALL_ID = 44632
STORAGE_EMPTY_ID = 44633    # "Preform storage", op Store-preform (cache config/loc)
STORAGE_FULL_ID = 44634     # "Preform storage", op Take-preform
CRUCIBLE_ID = 44776
MOULD_JIG_ID = 44777

# A read that fails this many consecutive ticks means the endpoint genuinely is not
# there (un-rebuilt client / bridge down), not a blip.
STATE_UNAVAILABLE_LIMIT = 20


def read_state():
    """One fresh Giants' Foundry snapshot, or None for 'cannot tell' (endpoint missing,
    bridge down, or the reader itself erroring). None must always defer, never act."""
    st = controller.get_api("minigames", "giants-foundry")
    if not st or st.get("error"):
        return None
    return st


def _reset_counters(data, st, phase):
    # Counters reset where success is DETECTED (read back from varbits), never where the
    # attempt was made -- the mahogany_homes contact-cast lesson: a reset that lives in a
    # phase handler dies the moment the dispatcher stops routing there.
    if st.get("commissionTypes"):
        data["gf_commission_attempts"] = 0
    if phase not in ("READY_TO_HAND_IN", "RUINED"):
        data["gf_handin_attempts"] = 0
    if phase not in ("PREFORM_READY", "COLLECTING_PREFORM"):
        data["gf_pickup_attempts"] = 0
    bars = st.get("crucibleBarCount", 0)
    if bars > data.get("gf_last_bars", -1):
        data["gf_fill_attempts"] = 0
    data["gf_last_bars"] = bars
    if st.get("mouldSetupOpen"):
        data["gf_mould_opens"] = 0


def tick(ctx):
    data = ctx.state.data
    st = read_state()
    if st is None:
        data["gf_no_state"] = data.get("gf_no_state", 0) + 1
        if data["gf_no_state"] >= STATE_UNAVAILABLE_LIMIT:
            ctx.state.trigger_safety_stop(
                "giants foundry: /api/minigames/giants-foundry unavailable -- "
                "rebuild + restart the client?")
        return
    data["gf_no_state"] = 0

    phase = st.get("phase")
    if phase == "INACTIVE":
        ctx.state.trigger_safety_stop(
            "giants foundry: not at the Foundry -- start inside the minigame area")
        return

    _reset_counters(data, st, phase)

    handler = PHASE_HANDLERS.get(phase)
    if handler is None:
        debug_log.log("gf", f"unhandled phase {phase} -- waiting")
        return
    handler(ctx, st)


# Stubs -- replaced by Tasks 5-9. Defined so PHASE_HANDLERS can bind now.
def get_commission_phase(ctx, st):
    pass


def prepare_phase(ctx, st):
    pass


def pickup_phase(ctx, st):
    pass


def refine_phase(ctx, st):
    pass


def hand_in_phase(ctx, st):
    pass


PHASE_HANDLERS = {
    "AWAITING_COMMISSION": get_commission_phase,
    "PREPARING": prepare_phase,
    "SELECTING_MOULDS": prepare_phase,
    "MOULD_SET": prepare_phase,
    "PREFORM_READY": pickup_phase,
    "COLLECTING_PREFORM": pickup_phase,
    "REFINING": refine_phase,
    "STORED": refine_phase,
    "READY_TO_HAND_IN": hand_in_phase,
    "RUINED": hand_in_phase,
}
```

**Note for Tasks 5–9:** because `PHASE_HANDLERS` binds the functions at import time, each later task must REPLACE the stub body in place (edit the existing `def`), not define a second function of the same name below it — a second def would leave `PHASE_HANDLERS` pointing at the stub.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: phase dispatcher + detected-success counter resets"
```

---

### Task 5: Object/NPC/dialogue helpers + commission + hand-in phases

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (helpers appended; `get_commission_phase`/`hand_in_phase` stub bodies replaced)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Consumes: `controller.get_objects/get_npcs/click_entity/click_menu_action/key_tap/npc_index_refresh_target/object_refresh_target`, Task 4's dispatcher.
- Produces: `_find_object(obj_id, radius=32) -> dict | None`, `_refresh(obj) -> callable`, `_find_kovac() -> dict | None`, `_dialogue() -> dict`, `_dialogue_open(d) -> bool`, `_dialogue_options(d) -> list[str]`, `_choose_option(ctx, d, substrings, fallback_key=None) -> bool`, constants `COMMISSION_MAX_ATTEMPTS = 4`, `HANDIN_MAX_ATTEMPTS = 6`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------------------
# Task 5: commission + hand-in
# ---------------------------------------------------------------------------

def _kovac(monkeypatch):
    npc = {"id": gf.KOVAC_NPC_ID, "index": 28704, "name": "Kovac",
           "screenX": 500, "screenY": 300}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [npc])
    return npc


def test_commission_right_clicks_kovac(monkeypatch):
    ctx = _Ctx()
    _kovac(monkeypatch)
    calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: calls.append(option) or True)
    gf.get_commission_phase(ctx, _st(phase="AWAITING_COMMISSION", commissionTypes=[]))
    assert calls == ["Commission"]
    assert ctx.state.data["gf_commission_attempts"] == 1


def test_commission_ceiling_stops(monkeypatch):
    ctx = _Ctx()
    _kovac(monkeypatch)
    st = _st(phase="AWAITING_COMMISSION", commissionTypes=[])
    for _ in range(gf.COMMISSION_MAX_ATTEMPTS + 1):
        gf.get_commission_phase(ctx, st)
    assert "commission" in ctx.state.stopped


def test_hand_in_dialogue_open_advances_not_clicks(monkeypatch):
    # With the reward dialogue open, hand_in presses keys, never re-clicks Kovac.
    ctx = _Ctx()
    _kovac(monkeypatch)
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True, "options": []} if (g, r) == ("ui", "dialogue") else None)
    keys, clicks = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: clicks.append(a) or True)
    gf.hand_in_phase(ctx, _st(phase="READY_TO_HAND_IN", readyToHandIn=True))
    assert keys == ["SPACE"] and clicks == []


def test_hand_in_dialogue_options_picks_commission(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True,
                                      "options": ["Can I have another commission?", "No thanks."]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    gf.hand_in_phase(ctx, _st(phase="READY_TO_HAND_IN", readyToHandIn=True))
    assert keys == ["1"]


def test_hand_in_no_dialogue_clicks_kovac_with_ceiling(monkeypatch):
    ctx = _Ctx()
    _kovac(monkeypatch)
    calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: calls.append(option) or True)
    st = _st(phase="READY_TO_HAND_IN", readyToHandIn=True)
    for _ in range(gf.HANDIN_MAX_ATTEMPTS + 1):
        gf.hand_in_phase(ctx, st)
    assert calls == ["Hand-in"] * gf.HANDIN_MAX_ATTEMPTS
    assert "hand-in" in ctx.state.stopped.lower()


def test_dialogue_options_tolerates_both_shapes():
    assert gf._dialogue_options({"options": ["a", "b"]}) == ["a", "b"]
    assert gf._dialogue_options({"options": [{"text": "a"}, {"text": "b"}]}) == ["a", "b"]
    assert gf._dialogue_options({}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k "commission or hand_in or dialogue_options" -v`
Expected: FAIL (stub bodies do nothing; helpers missing).

- [ ] **Step 3: Implement** (append helpers; REPLACE the two stub bodies in place)

```python
# ---------------------------------------------------------------------------
# Runtime: entity + dialogue helpers
# ---------------------------------------------------------------------------

def _find_object(obj_id, radius=32):
    for obj in controller.get_objects(radius=radius):
        if obj.get("id") == obj_id:
            return obj
    return None


def _refresh(obj):
    return controller.object_refresh_target(
        obj.get("x"), obj.get("y"), obj.get("plane", 0), obj.get("id"))


def _find_kovac():
    for npc in controller.get_npcs():
        if npc.get("id") == KOVAC_NPC_ID or (npc.get("name") or "").lower() == "kovac":
            return npc
    return None


def _dialogue():
    d = controller.get_api("ui", "dialogue")
    if not d or d.get("error"):
        return {}
    return d


def _dialogue_open(d):
    # Tolerant read of the m8aq Dialogue reader: exact field names get pinned on the
    # first live session; any of these shapes means "a dialogue is up".
    if not d:
        return False
    for key in ("open", "isOpen", "visible"):
        if isinstance(d.get(key), bool):
            return d[key]
    dtype = d.get("type")
    return bool(d.get("options")) or bool(dtype and dtype != "NONE")


def _dialogue_options(d):
    out = []
    for o in d.get("options") or []:
        out.append(o if isinstance(o, str) else (o.get("text") or ""))
    return out


def _choose_option(ctx, d, substrings, fallback_key=None):
    """Press the number key of the first dialogue option matching any substring
    (case-insensitive). Falls back to fallback_key when nothing matches."""
    for i, text in enumerate(_dialogue_options(d)):
        low = text.lower()
        if any(s in low for s in substrings):
            controller.key_tap(ctx.ser, str(i + 1))
            return True
    if fallback_key is not None:
        controller.key_tap(ctx.ser, fallback_key)
        return True
    return False
```

Replace `get_commission_phase`'s stub body:

```python
def get_commission_phase(ctx, st):
    # Right-click Commission on Kovac -- MEASURED: the commission varbits land ~0.1s
    # after the click, before the confirmation dialogue is even dismissed. The counter
    # resets in _reset_counters the moment commissionTypes reads non-empty.
    data = ctx.state.data
    d = _dialogue()
    if _dialogue_open(d):
        controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.4, 0.8)
        return
    if data.get("gf_commission_attempts", 0) >= COMMISSION_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "giants foundry: commission never registered after repeated asks")
        return
    kovac = _find_kovac()
    if kovac is None:
        debug_log.log("gf", "Kovac not visible -- waiting")
        return
    data["gf_commission_attempts"] = data.get("gf_commission_attempts", 0) + 1
    controller.click_menu_action(
        ctx.ser, kovac, "Commission",
        refresh_target=controller.npc_index_refresh_target(kovac.get("index")))
    controller.human_delay(0.8, 1.2)
```

Replace `hand_in_phase`'s stub body:

```python
def hand_in_phase(ctx, st):
    # Right-click Hand-in on Kovac, then ride the reward dialogue through to the offer
    # of a NEW commission (MEASURED: Space x2, option key 1, Space). A RUINED sword is
    # handed in identically -- that is the game's own recovery. Success is detected by
    # the dispatcher: the foundry varbits all zero and a fresh commission registers.
    data = ctx.state.data
    d = _dialogue()
    if _dialogue_open(d):
        if _dialogue_options(d):
            _choose_option(ctx, d, ("commission",), fallback_key="1")
        else:
            controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.4, 0.8)
        return
    if data.get("gf_handin_attempts", 0) >= HANDIN_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop("giants foundry: hand-in never completed")
        return
    kovac = _find_kovac()
    if kovac is None:
        return
    data["gf_handin_attempts"] = data.get("gf_handin_attempts", 0) + 1
    controller.click_menu_action(
        ctx.ser, kovac, "Hand-in",
        refresh_target=controller.npc_index_refresh_target(kovac.get("index")))
    controller.mark_busy(3.0)
    controller.human_delay(0.8, 1.2)
```

Add the two constants next to `STATE_UNAVAILABLE_LIMIT`:

```python
COMMISSION_MAX_ATTEMPTS = 4
HANDIN_MAX_ATTEMPTS = 6
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: commission + hand-in phases with dialogue riding"
```

---

### Task 6: Mould selection execution (prepare_phase part 1)

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (`prepare_phase` stub body replaced; mould-UI helpers appended)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Consumes: Task 1's `best_moulds`/`MOULDS`/`MOULD_BY_DISPLAY`, Task 5's `_find_object`/`_refresh`.
- Produces: `_select_moulds_in_ui(ctx, st, plan) -> None`, `_open_mould_setup(ctx) -> None`, constants `MOULD_GROUP = 718`, `MOULD_LIST_CHILD = 9`, `MOULD_TAB_CHILD = 12`, `MOULD_SET_CHILD = 6`, `MOULD_TAB_SLOTS = {"RICASSO": 0, "BLADE": 9, "TIP": 18}`, `MOULD_MAX_OPENS = 3`. `prepare_phase` now dispatches moulds → fill → pour; `_fill_crucible`/`_pour` are stubs (`pass`) until Task 7.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------------------
# Task 6: mould selection execution
# ---------------------------------------------------------------------------

def _mould_rows(monkeypatch, names):
    rows = [{"text": n, "screenX": 400, "screenY": 200 + 20 * i}
            for i, n in enumerate(names)]
    monkeypatch.setattr(controller, "get_widget_children",
                        lambda g, c: rows if (g, c) == (gf.MOULD_GROUP, gf.MOULD_LIST_CHILD) else [])
    return rows


def test_prepare_opens_jig_when_moulds_unset(monkeypatch):
    ctx = _Ctx()
    jig = {"id": gf.MOULD_JIG_ID, "x": 3370, "y": 11488, "plane": 0,
           "screenX": 700, "screenY": 300, "name": "Mould jig"}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [jig])
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicked.append(ent.get("id")) or True)
    gf.prepare_phase(ctx, _st(phase="PREPARING", selectedMoulds={}, mouldSetupOpen=False))
    assert clicked == [gf.MOULD_JIG_ID]
    assert ctx.state.data["gf_mould_opens"] == 1


def test_prepare_open_jig_ceiling(monkeypatch):
    ctx = _Ctx()
    st = _st(phase="PREPARING", selectedMoulds={}, mouldSetupOpen=False)
    for _ in range(gf.MOULD_MAX_OPENS + 1):
        gf.prepare_phase(ctx, st)     # no jig visible -> attempts still count
    assert "mould" in ctx.state.stopped.lower()


def test_select_moulds_clicks_best_visible_row_one_part_per_call(monkeypatch):
    # Narrow Flat, RICASSO tab: with these rows visible the argmax is Chopper Forte +1.
    ctx = _Ctx()
    rows = _mould_rows(monkeypatch, ["Chopper Forte", "Serpent Ricasso", "Chopper Forte +1"])
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicked.append(ent) or True)
    st = _st(phase="SELECTING_MOULDS", mouldSetupOpen=True, selectedMoulds={},
             usableMoulds=list(gf.MOULDS))
    gf.prepare_phase(ctx, st)
    assert len(clicked) == 1
    assert clicked[0]["text"] == "Chopper Forte +1"


def test_select_moulds_all_set_clicks_set_button(monkeypatch):
    ctx = _Ctx()
    plan = gf.best_moulds(["NARROW", "FLAT"], [])
    set_button = {"screenX": 660, "screenY": 152, "clickable": True}
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, index=None: set_button
                        if (g, c) == (gf.MOULD_GROUP, gf.MOULD_SET_CHILD) else None)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicked.append(ent) or True)
    st = _st(phase="SELECTING_MOULDS", mouldSetupOpen=True,
             selectedMoulds={"RICASSO": plan["RICASSO"], "BLADE": plan["BLADE"],
                              "TIP": plan["TIP"]})
    gf.prepare_phase(ctx, st)
    assert clicked == [set_button]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k mould -v`
Expected: the new tests FAIL (prepare_phase is still a stub).

- [ ] **Step 3: Implement**

Append constants + helpers:

```python
# ---------------------------------------------------------------------------
# Runtime: mould setup interface (group 718 -- MEASURED in the recording)
# ---------------------------------------------------------------------------

MOULD_GROUP = 718
MOULD_SET_CHILD = 6      # "Set Mould" button (recorder: widget 718.6)
MOULD_LIST_CHILD = 9     # scroll list of mould rows (recorder: "Select <name>" on 718.9)
MOULD_TAB_CHILD = 12     # part tabs (recorder: "View Fortes/Blades/Tips" on 718.12)
MOULD_TAB_SLOTS = {"RICASSO": 0, "BLADE": 9, "TIP": 18}
MOULD_MAX_OPENS = 3


def _strip_tags(text):
    out, depth = [], 0
    for ch in text or "":
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip()


def _open_mould_setup(ctx):
    data = ctx.state.data
    if data.get("gf_mould_opens", 0) >= MOULD_MAX_OPENS:
        ctx.state.trigger_safety_stop(
            "giants foundry: mould setup never completed (interface not opening, or a "
            "planned mould row never selectable)")
        return
    data["gf_mould_opens"] = data.get("gf_mould_opens", 0) + 1   # reset when mouldSetupOpen reads True
    jig = _find_object(MOULD_JIG_ID)
    if jig is None:
        debug_log.log("gf", "mould jig not visible -- waiting")
        return
    controller.click_entity(ctx.ser, jig, refresh_target=_refresh(jig))
    controller.mark_busy(4.0)
    controller.human_delay(1.0, 1.6)


def _click_widget(ctx, group, child, index=None):
    w = controller.get_widget(group, child, index=index)
    if not w or not w.get("screenX"):
        return False
    return bool(controller.click_entity(
        ctx.ser, w, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False))


def _select_moulds_in_ui(ctx, st, plan):
    """One part per call (the varbit confirms between calls): find the first part whose
    live selection differs from the plan, switch to its tab, and click the best USABLE
    row that is actually on screen -- if the planned row isn't rendered (scrolled off),
    the best visible one is taken instead of scrolling, logged. When every part matches,
    click Set Mould."""
    selected = st.get("selectedMoulds") or {}
    commission = st.get("commissionTypes") or []
    usable = set(st.get("usableMoulds") or [])
    for part in MOULD_PARTS:
        want = plan.get(part)
        if want is None or selected.get(part) == want:
            continue
        _click_widget(ctx, MOULD_GROUP, MOULD_TAB_CHILD, index=MOULD_TAB_SLOTS[part])
        controller.human_delay(0.4, 0.7)
        rows = controller.get_widget_children(MOULD_GROUP, MOULD_LIST_CHILD) or []
        best = None
        for row in rows:
            enum = MOULD_BY_DISPLAY.get(_strip_tags(row.get("text")).lower())
            if enum is None or MOULDS[enum][1] != part:
                continue
            if usable and enum not in usable:
                continue
            s = mould_score(enum, commission)
            if best is None or s > best[0]:
                best = (s, enum, row)
        if best is None:
            debug_log.log("gf", f"no selectable mould row for {part} -- re-opening")
            return
        if best[1] != want:
            debug_log.log("gf", f"planned {want} not on screen for {part} -- "
                                f"taking best visible {best[1]}")
        controller.click_entity(ctx.ser, best[2], offset=(0, 0),
                                check_ui_blockers=False, simulate_mistakes=False)
        controller.human_delay(0.5, 0.9)
        return
    # every part satisfied -> confirm
    if _click_widget(ctx, MOULD_GROUP, MOULD_SET_CHILD):
        controller.human_delay(0.6, 1.0)
```

Replace `prepare_phase`'s stub body (note `_fill_crucible`/`_pour` stubs added below it for Task 7):

```python
def prepare_phase(ctx, st):
    plan = best_moulds(st.get("commissionTypes") or [], st.get("usableMoulds") or [])
    selected = st.get("selectedMoulds") or {}
    if st.get("mouldSetupOpen"):
        _select_moulds_in_ui(ctx, st, plan)
        return
    # A part-mismatch against the plan only forces a re-open while the mould is not yet
    # SET (phase PREPARING/SELECTING_MOULDS); once workflowState says MOULD_SET we take
    # the selection as final -- re-opening a set mould mid-fill would loop.
    if st.get("phase") != "MOULD_SET" and (
            len(selected) < 3 or any(selected.get(p) != plan.get(p) for p in plan)):
        _open_mould_setup(ctx)
        return
    if st.get("crucibleBarCount", 0) < CRUCIBLE_CAPACITY:
        _fill_crucible(ctx, st)
        return
    _pour(ctx, st)


def _fill_crucible(ctx, st):
    pass    # Task 7


def _pour(ctx, st):
    pass    # Task 7
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: mould selection through the group-718 interface"
```

---

### Task 7: Crucible fill execution + pour (prepare_phase part 2)

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (`_fill_crucible`/`_pour` stub bodies replaced; bank + dialog helpers appended)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Consumes: Task 2's `plan_fill`, Task 5's dialogue helpers, `banker.find_nearby_bank/open_bank/deposit_all_inventory/withdraw_loadout/close_bank`, `controller.get_inventory` (items carry `name` + screen coords; slot count = item count, never distinct names).
- Produces: `_fill_crucible(ctx, st)`, `_restock_from_bank(ctx, steps)`, `_answer_fill_dialogs(ctx, count, accept_overflow)`, `_pour(ctx, st)`, constants `FILL_MAX_ATTEMPTS = 3`, `FILL_DIALOG_TIMEOUT_S = 4.0`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------------------
# Task 7: crucible fill + pour
# ---------------------------------------------------------------------------

def _crucible(monkeypatch, extra_objects=()):
    cru = {"id": gf.CRUCIBLE_ID, "x": 3373, "y": 11488, "plane": 0,
           "screenX": 670, "screenY": 320, "name": "Crucible"}
    objs = [cru] + list(extra_objects)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: objs)
    return cru


def _inv(monkeypatch, names):
    items = [{"name": n, "screenX": 900, "screenY": 400 + 10 * i, "qty": 1}
             for i, n in enumerate(names)]
    monkeypatch.setattr(controller, "get_inventory", lambda: {"items": items})
    return items


def test_fill_uses_item_on_crucible(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    _inv(monkeypatch, ["Mithril platebody"] * 4 + ["Adamant platelegs"] * 7)
    menu, clicks = [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent.get("name"), option)) or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("id")) or True)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    # Selects Use on the first planned item (bodies first -- biggest bars_each), then
    # clicks the crucible.
    assert menu == [("Mithril platebody", "Use")]
    assert clicks == [gf.CRUCIBLE_ID]
    assert ctx.state.data["gf_fill_attempts"] == 1


def test_fill_restocks_from_bank_when_items_missing(monkeypatch):
    ctx = _Ctx()
    chest = {"id": gf.BANK_CHEST_ID, "name": "Bank chest", "distance": 2,
             "screenX": 400, "screenY": 300, "x": 3375, "y": 11493, "plane": 0}
    _crucible(monkeypatch, extra_objects=[chest])
    _inv(monkeypatch, [])
    opened, withdrew = [], []
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: chest)
    monkeypatch.setattr(banker, "open_bank", lambda ctx_, obj, **k: opened.append(obj) or True)
    monkeypatch.setattr(banker, "deposit_all_inventory", lambda ctx_: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx_: None)

    def fake_withdraw(ctx_, cfg, fresh=False):
        withdrew.extend(cfg["withdraw"])
        _inv(monkeypatch, ["Mithril platebody"] * 4 + ["Adamant platelegs"] * 7)
    monkeypatch.setattr(banker, "withdraw_loadout", fake_withdraw)

    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    assert opened == [chest]
    assert {w["name"]: w["quantity"] for w in withdrew} == \
        {"Mithril platebody": 4, "Adamant platelegs": 7}
    assert ctx.state.stopped is None


def test_fill_bank_dry_safety_stops(monkeypatch):
    ctx = _Ctx()
    chest = {"id": gf.BANK_CHEST_ID, "name": "Bank chest", "distance": 2,
             "screenX": 400, "screenY": 300, "x": 3375, "y": 11493, "plane": 0}
    _crucible(monkeypatch, extra_objects=[chest])
    _inv(monkeypatch, [])
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: chest)
    monkeypatch.setattr(banker, "open_bank", lambda ctx_, obj, **k: True)
    monkeypatch.setattr(banker, "deposit_all_inventory", lambda ctx_: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx_: None)
    monkeypatch.setattr(banker, "withdraw_loadout", lambda ctx_, cfg, fresh=False: None)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    assert "out of" in ctx.state.stopped


def test_fill_attempt_ceiling(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    _inv(monkeypatch, ["Mithril platebody"] * 4)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    for _ in range(gf.FILL_MAX_ATTEMPTS + 1):
        gf._fill_crucible(ctx, st)   # bar count never grows -> counter never resets
    assert "fill" in ctx.state.stopped.lower()


def test_answer_fill_dialogs_exact_option(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True, "options": ["1", "5", "X", "All"]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    gf._answer_fill_dialogs(ctx, count=5, accept_overflow=True)
    assert keys[0] == "2"


def test_answer_fill_dialogs_custom_amount(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True, "options": ["1", "5", "X", "All"]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys, typed = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "type_text", lambda ser, text: typed.append(text))
    gf._answer_fill_dialogs(ctx, count=3, accept_overflow=True)
    assert keys[0] == "3" and typed == ["3"]


def test_pour_clicks_crucible_with_expected_action(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    kwargs = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: kwargs.update(k) or True)
    gf._pour(ctx, _st(phase="MOULD_SET", crucibleBarCount=28, crucibleState="FULL"))
    assert kwargs.get("expected_action") == "Pour"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k "fill or pour" -v`
Expected: new tests FAIL (stub bodies).

- [ ] **Step 3: Implement**

Add constants near the others:

```python
FILL_MAX_ATTEMPTS = 3
FILL_DIALOG_TIMEOUT_S = 4.0
```

Replace `_fill_crucible`/`_pour` stub bodies and append the helpers:

```python
def _fill_crucible(ctx, st):
    cfg = ctx.state.config
    data = ctx.state.data
    steps, err = plan_fill(cfg["fill"], st.get("crucibleContents") or {},
                           cfg["max_overflow_loss"])
    if err:
        ctx.state.trigger_safety_stop(f"giants foundry: {err}")
        return
    if not steps:
        return    # satisfied -- prepare_phase pours on the next tick's read

    step = steps[0]
    inv = controller.get_inventory() or {}
    held = [i for i in (inv.get("items") or [])
            if (i.get("name") or "").lower() == step["item"].lower()]
    if not held:
        _restock_from_bank(ctx, steps)
        return

    if data.get("gf_fill_attempts", 0) >= FILL_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "giants foundry: crucible fill not progressing (crucible bar count never grew)")
        return
    crucible = _find_object(CRUCIBLE_ID)
    if crucible is None:
        return
    data["gf_fill_attempts"] = data.get("gf_fill_attempts", 0) + 1   # reset when bars grow
    # Explicit Use via the menu -- a wearable's LEFT-click default is Wear/Wield, so a
    # bare click on platelegs would equip them instead of selecting them.
    if not controller.click_menu_action(ctx.ser, held[0], "Use",
                                        offset=(0, 0), check_ui_blockers=False):
        return
    controller.human_delay(0.2, 0.4)
    if not controller.click_entity(ctx.ser, crucible, refresh_target=_refresh(crucible)):
        return
    controller.mark_busy(FILL_DIALOG_TIMEOUT_S + 4)
    _answer_fill_dialogs(ctx, min(step["count"], len(held)), accept_overflow=True)


def _restock_from_bank(ctx, steps):
    """Withdraw everything the remaining plan needs in one trip. plan_fill already
    vetoed any overflow beyond config, so whatever we withdraw here is intended."""
    bank_obj = banker.find_nearby_bank({"maxBankDistance": 25})
    if bank_obj is None:
        debug_log.log("gf", "bank chest not visible -- waiting")
        return
    if not banker.open_bank(ctx, bank_obj):
        return
    banker.deposit_all_inventory(ctx)
    need = {}
    for s in steps:
        need[s["item"]] = need.get(s["item"], 0) + s["count"]
    banker.withdraw_loadout(
        ctx, {"withdraw": [{"name": item, "quantity": n} for item, n in need.items()]},
        fresh=True)
    inv = controller.get_inventory() or {}
    names = [(i.get("name") or "").lower() for i in (inv.get("items") or [])]
    for item in need:
        if item.lower() not in names:
            banker.close_bank(ctx)
            ctx.state.trigger_safety_stop(f"giants foundry: bank is out of {item}")
            return
    banker.close_bank(ctx)


def _answer_fill_dialogs(ctx, count, accept_overflow):
    """Ride the add-to-crucible dialogs: a quantity prompt (option keys; 'X' takes a
    typed amount -- MEASURED), then possibly the overflow confirm ('Are you sure?').
    Exact widget/option layout gets pinned on the first live session; matching is by
    option text so a layout shuffle degrades to the '1' fallback, not a wrong click.
    Iteration-bounded (not wall-clock) so the hermetic tests' no-op sleep exits
    instantly: 16 polls x HEAT_POLL_S-sized sleeps ~ FILL_DIALOG_TIMEOUT_S live."""
    answered_quantity = False
    for _ in range(int(FILL_DIALOG_TIMEOUT_S / 0.25)):
        d = _dialogue()
        opts = _dialogue_options(d)
        if opts and not answered_quantity and not _is_confirm(opts):
            picked = False
            for i, text in enumerate(opts):
                if text.strip() == str(count):
                    controller.key_tap(ctx.ser, str(i + 1))
                    picked = True
                    break
            if not picked:
                for i, text in enumerate(opts):
                    if text.strip().lower() in ("x", "other"):
                        controller.key_tap(ctx.ser, str(i + 1))
                        controller.human_delay(0.6, 0.9)
                        controller.type_text(ctx.ser, str(count))
                        picked = True
                        break
            if not picked:
                controller.key_tap(ctx.ser, "1")
            answered_quantity = True
            controller.human_delay(0.4, 0.7)
            continue
        if opts and _is_confirm(opts):
            want = ("yes",) if accept_overflow else ("no",)
            _choose_option(ctx, d, want, fallback_key=None)
            return
        time.sleep(0.25)


def _is_confirm(opts):
    low = " ".join(o.lower() for o in opts)
    return "yes" in low or "no" in low


def _pour(ctx, st):
    crucible = _find_object(CRUCIBLE_ID)
    if crucible is None:
        return
    controller.click_entity(ctx.ser, crucible, refresh_target=_refresh(crucible),
                            expected_action="Pour")
    controller.mark_busy(4.0)
    controller.human_delay(1.0, 1.6)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: crucible fill execution, bank restock, pour"
```

---

### Task 8: Pickup phase + free-hands recovery + on_event

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (`pickup_phase` stub body replaced; `_free_hands`, `on_event` appended)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Consumes: `controller.get_widget/key_tap/click_menu_action`, Task 5 helpers.
- Produces: `pickup_phase(ctx, st)`, `_free_hands(ctx)`, `on_event(ctx, event_type, payload)`, constants `PICKUP_MAX_ATTEMPTS = 3`, `EQUIP_GROUP = 387`, `WEAPON_CHILD = 18`, `SHIELD_CHILD = 20` (recorder-decoded worn-equipment slots).

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------------------
# Task 8: pickup + free hands
# ---------------------------------------------------------------------------

def _jig(monkeypatch):
    jig = {"id": gf.MOULD_JIG_ID, "x": 3370, "y": 11488, "plane": 0,
           "screenX": 700, "screenY": 300, "name": "Mould jig"}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [jig])
    return jig


def test_pickup_clicks_jig(monkeypatch):
    ctx = _Ctx()
    _jig(monkeypatch)
    kwargs = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: kwargs.update(k) or True)
    gf.pickup_phase(ctx, _st(phase="PREFORM_READY"))
    assert kwargs.get("expected_action") == "Pick-up"
    assert ctx.state.data["gf_pickup_attempts"] == 1


def test_pickup_free_hands_chat_triggers_unequip_once(monkeypatch):
    ctx = _Ctx()
    _jig(monkeypatch)
    gf.on_event(ctx, "CHAT_MESSAGE",
                {"message": "You must free your hands so you can hold the preform."})
    assert ctx.state.data["gf_need_free_hands"] is True
    keys, menu = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, index=None: {"screenX": 1028, "screenY": 470, "clickable": True})
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append(option) or True)
    gf.pickup_phase(ctx, _st(phase="PREFORM_READY"))
    # The recovery presses E, removes both hand slots, escapes -- and does NOT count as
    # a pickup attempt.
    assert keys[0] == "E" and keys[-1] == "ESCAPE"
    assert menu == ["Remove", "Remove"]
    assert ctx.state.data["gf_need_free_hands"] is False
    assert ctx.state.data.get("gf_pickup_attempts", 0) == 0


def test_pickup_ceiling(monkeypatch):
    ctx = _Ctx()
    _jig(monkeypatch)
    st = _st(phase="PREFORM_READY")
    for _ in range(gf.PICKUP_MAX_ATTEMPTS + 1):
        gf.pickup_phase(ctx, st)
    assert "pick up" in ctx.state.stopped.lower()


def test_on_event_tolerates_none_payload():
    ctx = _Ctx()
    gf.on_event(ctx, "CHAT_MESSAGE", None)
    gf.on_event(ctx, "TICK", {"tick": 1})
    assert ctx.state.stopped is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k "pickup or on_event" -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

Constants near the others:

```python
PICKUP_MAX_ATTEMPTS = 3
EQUIP_GROUP = 387     # worn-equipment interface (recorder-decoded)
WEAPON_CHILD = 18
SHIELD_CHILD = 20
```

Replace `pickup_phase`'s stub body and append:

```python
def pickup_phase(ctx, st):
    # Pick the preform off the jig. Needs ice/smithing gloves (setup checks) AND both
    # hand slots empty -- MEASURED: an off-hand Imcando hammer blocks it with the
    # "free your hands" chat, which on_event flags for the recovery below.
    data = ctx.state.data
    if data.get("gf_need_free_hands"):
        _free_hands(ctx)
        data["gf_need_free_hands"] = False
        return
    if data.get("gf_pickup_attempts", 0) >= PICKUP_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "giants foundry: could not pick up the preform (hands still full?)")
        return
    jig = _find_object(MOULD_JIG_ID)
    if jig is None:
        return
    data["gf_pickup_attempts"] = data.get("gf_pickup_attempts", 0) + 1
    controller.click_entity(ctx.ser, jig, refresh_target=_refresh(jig),
                            expected_action="Pick-up")
    controller.mark_busy(4.0)
    controller.human_delay(1.2, 1.8)


def _free_hands(ctx):
    # E opens worn equipment; Remove on both hand slots (387.18 weapon, 387.20 shield --
    # recorder-decoded). An empty slot's menu has no Remove entry, so click_menu_action
    # returns False harmlessly there.
    controller.key_tap(ctx.ser, "E")
    controller.human_delay(0.5, 0.9)
    for child in (WEAPON_CHILD, SHIELD_CHILD):
        w = controller.get_widget(EQUIP_GROUP, child)
        if w and w.get("screenX"):
            controller.click_menu_action(ctx.ser, w, "Remove",
                                         offset=(0, 0), check_ui_blockers=False)
            controller.human_delay(0.4, 0.7)
    controller.key_tap(ctx.ser, "ESCAPE")


def on_event(ctx, event_type, payload):
    if event_type != "CHAT_MESSAGE":
        return
    msg = ((payload or {}).get("message") or "")
    if "free your hands" in msg:
        ctx.state.data["gf_need_free_hands"] = True
    elif "overwork the preform" in msg:
        # Informational: the overwork guard should make this rare; count it so a log
        # shows how often it still happens.
        ctx.state.data["gf_overworked"] = ctx.state.data.get("gf_overworked", 0) + 1
        debug_log.log("gf", f"overworked the preform "
                            f"(x{ctx.state.data['gf_overworked']} this session)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: preform pickup with free-hands recovery"
```

---

### Task 9: Refinement controller

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (`refine_phase` stub body replaced; helpers appended)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Consumes: Task 3's `TOOL_STATS`/`actions_left_in_section`/`heat_stop_target`/`HEAT_STOP_MARGIN`, Task 5's `_find_object`/`_refresh`, `read_state`.
- Produces: `refine_phase(ctx, st)`, `_click_machine(ctx, st, tool) -> bool`, `_watch_progress(ctx, st)`, `_adjust_heat_and_engage(ctx, st, tool)`, constants `WORK_STALL_S = 12.0`, `WORK_FIRST_ACTION_S = 6.0`, `DEAD_CLICK_LIMIT = 5`, `HEAT_ADJUST_TIMEOUT_S = 20.0`, `HEAT_POLL_S = 0.25`, `WORKING_ACTIVITIES = ("HAMMERING", "GRINDING", "POLISHING")`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------------------
# Task 9: refinement controller
# ---------------------------------------------------------------------------

def _machines(monkeypatch):
    objs = [
        {"id": 44619, "name": "Trip hammer", "x": 3363, "y": 11496, "plane": 0,
         "screenX": 800, "screenY": 300},
        {"id": 44620, "name": "Grindstone", "x": 3369, "y": 11493, "plane": 0,
         "screenX": 600, "screenY": 350},
        {"id": 44621, "name": "Polishing wheel", "x": 3362, "y": 11484, "plane": 0,
         "screenX": 500, "screenY": 420},
        {"id": gf.LAVA_POOL_ID, "name": "Lava pool", "x": 3372, "y": 11497, "plane": 0,
         "screenX": 650, "screenY": 450},
        {"id": gf.WATERFALL_ID, "name": "Waterfall", "x": 3357, "y": 11488, "plane": 0,
         "screenX": 250, "screenY": 150},
        {"id": gf.STORAGE_FULL_ID, "name": "Preform storage", "x": 3366, "y": 11485,
         "plane": 0, "screenX": 520, "screenY": 380},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: objs)
    return {o["id"]: o for o in objs}


def test_refine_ready_idle_clicks_machine(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="NONE"))
    assert clicks == [44619]


def test_refine_working_out_of_band_interrupts_to_adjust(monkeypatch):
    # THE QUALITY GUARD: working while TOO_COLD damages the sword every action -- the
    # controller must interrupt with the heat adjust instead of watching it burn.
    ctx = _Ctx()
    _machines(monkeypatch)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    _serve_state(monkeypatch, _st(temperature=990, temperatureStatus="READY"))
    gf.refine_phase(ctx, _st(temperature=700, temperatureStatus="TOO_COLD",
                             activity="HAMMERING"))
    assert menu == [(gf.LAVA_POOL_ID, "Dunk-preform")]


def test_refine_working_never_reclicks(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="HAMMERING"))
    assert clicks == []    # overwork guard


def test_refine_sweet_spot_claims_once(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    st = _st(temperatureStatus="READY", activity="HAMMERING", sweetSpotState="AVAILABLE")
    gf.refine_phase(ctx, st)
    gf.refine_phase(ctx, st)     # same AVAILABLE window -> no second click
    assert clicks == [44619]
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="HAMMERING",
                             sweetSpotState="CLAIMED"))
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="HAMMERING",
                             sweetSpotState="AVAILABLE"))
    assert clicks == [44619, 44619]   # new window -> one more claim


def test_refine_too_cold_dunks_then_engages(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu, clicks = [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    # Serve a state whose temperature is already past target so the inner poll exits
    # on its first read.
    hot = _st(temperature=990, temperatureStatus="READY")
    _serve_state(monkeypatch, hot)
    cold = _st(temperature=700, temperatureStatus="TOO_COLD", activity="NONE")
    gf.refine_phase(ctx, cold)
    assert menu == [(gf.LAVA_POOL_ID, "Dunk-preform")]
    assert clicks == [44619]     # engaged the hammer right after the ramp


def test_refine_too_hot_quenches(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    _serve_state(monkeypatch, _st(temperature=400, temperatureStatus="READY",
                                  requiredTool="GRINDSTONE",
                                  minimumTemperature=410, maximumTemperature=590))
    gf.refine_phase(ctx, _st(temperature=700, temperatureStatus="TOO_HOT",
                             requiredTool="GRINDSTONE", activity="NONE",
                             minimumTemperature=410, maximumTemperature=590))
    assert menu == [(gf.WATERFALL_ID, "Quench-preform")]


def test_refine_stored_takes_from_storage(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    kwargs = {}
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: (clicks.append(ent["id"]), kwargs.update(k)) or True)
    gf.refine_phase(ctx, _st(phase="STORED", preformState="STORED"))
    assert clicks == [gf.STORAGE_FULL_ID]
    assert kwargs.get("expected_action") == "Take-preform"


def test_watch_progress_stall_reengages_and_dead_clicks_stop(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    st = _st(temperatureStatus="READY", activity="HAMMERING", completion=200)
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    gf.refine_phase(ctx, st)               # working, progress mark set
    assert clicks == []
    now[0] += gf.WORK_STALL_S + 1
    gf.refine_phase(ctx, st)               # stalled -> re-engage (dead click 1)
    assert clicks == [44619]
    for _ in range(gf.DEAD_CLICK_LIMIT):
        now[0] += gf.WORK_STALL_S + 1
        gf.refine_phase(ctx, st)
    assert "no progress" in ctx.state.stopped.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k refine -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

Constants:

```python
WORKING_ACTIVITIES = ("HAMMERING", "GRINDING", "POLISHING")
WORK_STALL_S = 12.0        # > two hammer cycles (3s each) + the nulled first cycle
WORK_FIRST_ACTION_S = 6.0
DEAD_CLICK_LIMIT = 5
HEAT_ADJUST_TIMEOUT_S = 20.0
HEAT_POLL_S = 0.25
```

Replace `refine_phase`'s stub body and append helpers:

```python
def refine_phase(ctx, st):
    """One decision per tick: sweet spot > (working? watch, don't touch) > in band?
    engage machine > out of band? fast-adjust and engage. The overwork rule (MEASURED:
    clicking a machine mid-action forfeits the next action) is why 'working' returns
    without clicking anything except a sweet-spot claim."""
    data = ctx.state.data

    if st.get("preformState") == "STORED":
        storage = _find_object(STORAGE_FULL_ID)
        if storage is not None:
            controller.click_entity(ctx.ser, storage, refresh_target=_refresh(storage),
                                    expected_action="Take-preform")
            controller.mark_busy(3.0)
            controller.human_delay(1.0, 1.5)
        return

    tool = st.get("requiredTool")
    if tool not in TOOL_STATS:
        return    # boundary tick / completed read -- dispatcher resolves next tick

    # Sweet spot: exactly one claim click per AVAILABLE window, and only while the
    # temperature is right -- claiming out of band damages the sword instead.
    if st.get("sweetSpotState") == "AVAILABLE":
        if st.get("temperatureStatus") == "READY" and not data.get("gf_sweet_claimed"):
            machine = _find_object(TOOL_STATS[tool]["object"])
            if machine is not None:
                controller.click_entity(ctx.ser, machine, refresh_target=_refresh(machine),
                                        simulate_mistakes=False)
                data["gf_sweet_claimed"] = True
                return
    else:
        data["gf_sweet_claimed"] = False

    working = st.get("activity") in WORKING_ACTIVITIES
    status = st.get("temperatureStatus")
    if working:
        if status in ("TOO_COLD", "TOO_HOT"):
            # THE QUALITY GUARD: a running machine keeps acting while the temperature
            # is wrong, and every such action costs 10 quality. Interrupt it by going
            # straight to the heat adjust -- clicking the lava/waterfall interrupts the
            # work exactly like any other click, so this is one action, not two.
            _adjust_heat_and_engage(ctx, st, tool)
            return
        _watch_progress(ctx, st)
        return

    if status == "READY":
        _click_machine(ctx, st, tool)
    elif status in ("TOO_COLD", "TOO_HOT"):
        _adjust_heat_and_engage(ctx, st, tool)


def _watch_progress(ctx, st):
    """While the machine runs, only watch. Progress advancing clears the dead-click
    counter; WORK_STALL_S with no progress while 'working' means the activity read is
    stale or the action never started -- re-engage once per stall window."""
    data = ctx.state.data
    comp = st.get("completion", 0)
    mark = data.get("gf_progress_mark")
    now = time.time()
    if mark is None or comp > mark[0]:
        data["gf_progress_mark"] = (comp, now)
        data["gf_dead_clicks"] = 0
        return
    if now - mark[1] > WORK_STALL_S:
        debug_log.log("gf", f"no progress for {WORK_STALL_S:.0f}s while working -- re-engaging")
        data["gf_progress_mark"] = (comp, now)
        _click_machine(ctx, st, st.get("requiredTool"))


def _click_machine(ctx, st, tool):
    data = ctx.state.data
    if tool not in TOOL_STATS:
        return False
    if data.get("gf_dead_clicks", 0) >= DEAD_CLICK_LIMIT:
        ctx.state.trigger_safety_stop(
            "giants foundry: machine clicks produce no progress -- aim or state problem")
        return False
    machine = _find_object(TOOL_STATS[tool]["object"])
    if machine is None:
        return False
    data["gf_dead_clicks"] = data.get("gf_dead_clicks", 0) + 1   # cleared on progress
    ok = controller.click_entity(ctx.ser, machine, refresh_target=_refresh(machine))
    if ok:
        data["gf_progress_mark"] = (st.get("completion", 0), time.time())
        controller.mark_busy(WORK_FIRST_ACTION_S)
    return bool(ok)


def _adjust_heat_and_engage(ctx, st, tool):
    """Fast heat/cool (the human uses ONLY Dunk/Quench -- MEASURED), polled every
    HEAT_POLL_S against the live temperature varbit, stopped HEAT_STOP_MARGIN short of
    the band-end target -- and the stop IS the machine click (MEASURED: clicking the
    next machine interrupts the dunk; there is no separate walk-away click)."""
    min_t = st.get("minimumTemperature", -1)
    max_t = st.get("maximumTemperature", -1)
    if min_t < 0 or max_t < 0:
        return
    heating = st.get("temperatureStatus") == "TOO_COLD"
    actions = actions_left_in_section(st.get("completion", 0), st.get("sectionCount", 0), tool)
    target = heat_stop_target(tool, min_t, max_t, actions)
    pool = _find_object(LAVA_POOL_ID if heating else WATERFALL_ID)
    if pool is None:
        return
    option = "Dunk-preform" if heating else "Quench-preform"
    if not controller.click_menu_action(ctx.ser, pool, option,
                                        refresh_target=_refresh(pool)):
        return
    controller.mark_busy(HEAT_ADJUST_TIMEOUT_S + 2)
    deadline = time.time() + HEAT_ADJUST_TIMEOUT_S
    cur = None
    while time.time() < deadline:
        cur = read_state()
        if cur is not None:
            t = cur.get("temperature", -1)
            if t >= 0:
                if heating and t >= target - HEAT_STOP_MARGIN:
                    break
                if not heating and t <= target + HEAT_STOP_MARGIN:
                    break
            if cur.get("requiredTool") != tool:
                break    # section moved under us -- re-decide from the top next tick
        time.sleep(HEAT_POLL_S)
    _click_machine(ctx, cur or st, tool)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_giants_foundry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py
git commit -m "giants foundry: refinement controller (heat targeting, sweet spots, guards)"
```

---

### Task 10: setup(), before_break, engine wiring, full verification

**Files:**
- Modify: `ControllerAddon/routines/giants_foundry.py` (append `setup`, `before_break`)
- Modify: `ControllerAddon/controller.py` (add one `ROUTINE_FILES` entry at line ~2982)
- Modify: `src/main/java/com/example/controller/RoutineChoice.java` (one enum entry)
- Modify: `src/main/java/com/example/providers/PlayerProvider.java` (one field line, next to the existing `constructionXp` line ~238)
- Modify: `ControllerAddon/tests/test_giants_foundry.py` (append)

**Interfaces:**
- Consumes: everything above; `controller.get_equipment()`, `ctx.state.break_due` machinery (engine-side, no change).
- Produces: routine selectable from the RuneLite panel as "Giants' Foundry"; `smithingXp` on `GET /player`.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------------------
# Task 10: setup + before_break + wiring
# ---------------------------------------------------------------------------

def test_setup_requires_gloves(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"items": [{"name": "Rune platebody"}]})
    gf.setup(ctx)
    assert "gloves" in ctx.state.stopped.lower()


def test_setup_accepts_ice_gloves(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"items": [{"name": "Ice gloves"}]})
    gf.setup(ctx)
    assert ctx.state.stopped is None


def test_setup_cannot_read_equipment_does_not_stop(monkeypatch):
    # {} is get_equipment's bridge-down value -- "cannot tell" must not stop the run.
    ctx = _Ctx()
    gf.setup(ctx)
    assert ctx.state.stopped is None


def test_before_break_stores_equipped_preform(monkeypatch):
    ctx = _Ctx()
    storage = {"id": gf.STORAGE_EMPTY_ID, "x": 3366, "y": 11485, "plane": 0,
               "screenX": 520, "screenY": 380, "name": "Preform storage"}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [storage])
    _serve_state(monkeypatch, _st(preformState="EQUIPPED"))
    kwargs = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: kwargs.update(k) or True)
    gf.before_break(ctx)
    assert kwargs.get("expected_action") == "Store-preform"


def test_before_break_no_preform_no_click(monkeypatch):
    ctx = _Ctx()
    _serve_state(monkeypatch, _st(preformState="NONE", phase="AWAITING_COMMISSION"))
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(1) or True)
    gf.before_break(ctx)
    assert clicks == []


def test_routine_registered_in_controller():
    assert controller.ROUTINE_FILES.get("GIANTS_FOUNDRY") == "routines/giants_foundry.py"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_giants_foundry.py -k "setup or before_break or registered" -v`
Expected: FAIL.

- [ ] **Step 3: Implement Python side**

Append to the routine:

```python
# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def setup(ctx):
    eq = controller.get_equipment() or {}
    items = eq.get("items")
    if items is None:
        # {} is the bridge-down answer -- cannot tell is never a reason to refuse to start.
        debug_log.log("gf", "could not read equipment at setup -- glove check skipped")
        return
    names = " ".join((i.get("name") or "").lower() for i in items)
    if "ice gloves" not in names and "smithing gloves (i)" not in names:
        ctx.state.trigger_safety_stop(
            "giants foundry: ice gloves / smithing gloves (i) must be equipped to pick "
            "up preforms")
        return
    debug_log.log("gf", "setup ok -- gloves equipped")


def before_break(ctx):
    # Park a held preform in the Preform Storage before a 3-12 minute break -- the
    # game's own pause mechanism; heat and progress hold indefinitely. On resume the
    # STORED phase picks it back up.
    st = read_state()
    if not st or st.get("preformState") != "EQUIPPED":
        return
    storage = _find_object(STORAGE_EMPTY_ID)
    if storage is None:
        debug_log.log("gf", "break due but preform storage not visible -- breaking anyway")
        return
    debug_log.log("gf", "break due -- storing the preform first")
    controller.click_entity(ctx.ser, storage, refresh_target=_refresh(storage),
                            expected_action="Store-preform")
    controller.human_delay(2.0, 3.0)
```

Add to `ROUTINE_FILES` in `ControllerAddon/controller.py` (before the closing brace, after `"RAID_SCOUTER"`):

```python
    "GIANTS_FOUNDRY": "routines/giants_foundry.py",
```

- [ ] **Step 4: Implement Java side**

In `src/main/java/com/example/controller/RoutineChoice.java`, after `RAID_SCOUTER(...)` (change its trailing `;` to `,`):

```java
	RAID_SCOUTER("Raid Scouter (CoX)"),
	GIANTS_FOUNDRY("Giants' Foundry");
```

In `src/main/java/com/example/providers/PlayerProvider.java`, next to the existing XP lines (~line 238):

```java
		data.put("smithingXp", client.getSkillExperience(Skill.SMITHING));
```

- [ ] **Step 5: Run the full verification**

```bash
python -m py_compile routines/giants_foundry.py controller.py
python -m pytest tests/ -v
```
Expected: compiles clean; the WHOLE suite passes (not just this file's).

From the repo root, compile the Java side:

```bash
./gradlew.bat compileJava
```
Expected: BUILD SUCCESSFUL.

- [ ] **Step 6: Commit**

```bash
git add routines/giants_foundry.py tests/test_giants_foundry.py controller.py ../src/main/java/com/example/controller/RoutineChoice.java ../src/main/java/com/example/providers/PlayerProvider.java
git commit -m "giants foundry: lifecycle hooks + GUI wiring + smithingXp on /player"
```

---

## First live session checklist (not a plan task — run with the user present)

The spec's deferred items that only a live client can pin. Follow the CLAUDE.md
live-debugging workflow (background launch, log to file, recorder running alongside):

1. `GET /api/index` — confirm the exact giants-foundry path.
2. Rebuild + restart the client (`./gradlew.bat run`) for RoutineChoice + smithingXp.
3. Quantity-dialog options for the crucible fill (`_answer_fill_dialogs`'s option-text
   matching) — dump `/widgets/dump` while one is open, adjust matchers.
4. The m8aq `ui/dialogue` field names (`_dialogue_open` tolerant read).
5. Mould row/tab widget clicks (row geometry from `get_widget_children(718, 9)`).
6. Heat-adjust behaviour: watch `_adjust_heat_and_engage` overshoot/undershoot; tune
   `HEAT_STOP_MARGIN` from the log before trusting long runs.
7. Sweet-spot claims: confirm the green "quick reactions" chat follows claims.
8. Verify `smithingXp` appears on `GET /player` after rebuild.
