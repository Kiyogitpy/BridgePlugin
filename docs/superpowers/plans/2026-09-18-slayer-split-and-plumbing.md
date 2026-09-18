# Slayer sub-project 1: combat/slayer split + task-state plumbing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn today's `routines/slayer.py` into a shared combat engine plus a `Combat` routine, and build every piece of plumbing the future Slayer task orchestrator needs that does not depend on the live recording session: a `/slayer` task-state endpoint, `/varp`, shared dialogue/leg/gear helpers, and the new panel config.

**Architecture:** `routines/slayer.py` moves (git mv, history kept) to `combat_core.py`; `routines/combat.py` is a ~10-line wrapper over it. The Java side gains `SlayerProvider` (`GET /slayer`, decoding varps 394/395/4258/2096 and varbits 4067/4068/4069/4723 through the DB tables exactly as RuneLite's own `SlayerPlugin.updateTask()` does), `GET /varp?id=`, a `COMBAT` routine choice, and a "Slayer tasks" config section. Three shared Python modules are promoted out of routines that own private copies: `dialogue.py` (from golem_crafting/giants_foundry), `legs.py` (from banker's return-teleport runner), `gear.py` (from the bracelet re-wear pattern). `routines/slayer.py` becomes a stub that safety-stops with a pointer to the spec until sub-project 2 replaces it.

**Tech Stack:** Python 3.13 + pytest (from `ControllerAddon/`), Java 11 RuneLite plugin built with `./gradlew.bat` (JUnit 4.12, Mockito 5). `net.runelite.api.gameval.{VarPlayerID, VarbitID, DBTableID}` (present in runelite-api ≥ 1.12.36; the build uses `latest.release`).

**Spec:** `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` — this plan implements §5 (split), §8 (legs, promotion + builders only), §9's shared dialogue helpers, §10 (bridge + config), §11's `test_combat.py`/`test_dialogue.py`/`test_legs.py`, and the "gear.py" line of §4. It does NOT implement the orchestrator (§7), NPC Contact (§9 proper), the catalog (§6) or object legs — those are sub-projects 2 and 3 and need the §12 recording first.

## Global Constraints

- **The physical Arduino is the only input path** (CLAUDE.md "What this is"); nothing here adds a new way to click. Every click goes through `controller.click_entity` / `click_menu_action` / `key_tap`.
- **No fixed sleeps for outcomes.** Any wait for a game result uses `outcome_waits.await_pre` / `bridge_events.await_outcome` with a `confirm` read; a bare `time.sleep` is only acceptable as a poll interval inside such a loop.
- **`ctx.events` may be `None`**; every consumer must tolerate it (the `outcome_waits` helpers already do).
- **`controller.py` is loaded twice** (as `__main__` and as `controller`); no new module-level mutable state goes in it. New shared state goes in its own module.
- **A `@Subscribe` method must be named `on<EventSimpleName>`** — not relevant here (no new subscribers), stated so nobody adds one.
- **Never edit a file under `src/main/java/net/runelite/client/plugins/m8aq/api/`.**
- **Java: any endpoint that reads client state runs through `onClient(...)`** (`BridgeProvider.onClient`), never off-thread.
- **Tests must stay hermetic**: `tests/conftest.py`'s `no_live_bridge` fixture stubs `get_collision`/`is_point_within_window`; every other bridge read a test exercises must be monkeypatched in that test.
- **Every touched Python file is `py_compile`d before the suite runs** (CLAUDE.md live-debugging rule 5 — `banker.py` is not imported by every test).
- Run Python tests from `ControllerAddon/`: `python -m pytest tests/ -q`. Run Java tests from the repo root: `./gradlew.bat test --offline` (drop `--offline` if the Gradle cache is cold). A client rebuild (`./gradlew.bat run`) is only needed to see the Java changes live; the plan never requires the live client.
- Commit after every task. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `ControllerAddon/combat_core.py` (moved from `routines/slayer.py`) | the fight loop: banking trigger, prayer/food/potions/bracelets, loot, safespot, engage; `fight_tick(ctx)`; `combat_config()` | 1, 2 |
| `ControllerAddon/routines/combat.py` (new) | panel-config wrapper over `combat_core` | 1 |
| `ControllerAddon/routines/slayer.py` (rewritten as stub) | safety-stops with a pointer to the spec until sub-project 2 | 3 |
| `ControllerAddon/tests/test_combat.py` (moved from `test_slayer.py`) | the 19 pinned combat behaviours + `BANK_NO_PROGRESS_MAX` + `combat_config` | 1, 2 |
| `src/main/java/com/example/controller/RoutineChoice.java` | `COMBAT` entry, `SLAYER` relabel | 3 |
| `ControllerAddon/controller.py` | `ROUTINE_FILES["COMBAT"]`, `get_varp()`, `get_slayer_task()` | 3, 5, 7 |
| `src/main/java/com/example/controller/ControllerConfig.java` | `slayerBraceletReequip`; "Slayer tasks" section | 4, 11 |
| `src/main/java/com/example/controller/ConfigExporter.java` | `bracelets` and `tasks` blocks on `/slayer-config` | 4, 11 |
| `src/main/java/com/example/controller/SlayerMasterChoice.java` (new) | enum for the config dropdowns | 11 |
| `src/main/java/com/example/providers/PlayerProvider.java` | `/varp?id=` | 5 |
| `src/main/java/com/example/providers/SlayerTaskState.java` (new) | pure value object → JSON map | 6 |
| `src/test/java/com/example/providers/SlayerTaskStateTest.java` (new) | pins the null-name / hasTask rules | 6 |
| `src/main/java/com/example/providers/SlayerProvider.java` (new) | `GET /slayer` | 7 |
| `src/main/java/com/example/controller/ControllerPlugin.java` | registers `SlayerProvider` | 7 |
| `ControllerAddon/dialogue.py` (new) | read / is_open / options / find_option / choose (paged) / close | 8 |
| `ControllerAddon/tests/test_dialogue.py` (new) | the paged-dialogue tests, moved from golem | 8 |
| `ControllerAddon/routines/golem_crafting.py`, `routines/giants_foundry.py` | delegate their private copies | 8 |
| `ControllerAddon/legs.py` (new) | leg dict builders, `run_leg`, `run_next` cursor; promoted from banker | 9 |
| `ControllerAddon/tests/test_legs.py` (new) | `run_next` semantics, walk/fairy dispatch | 9 |
| `ControllerAddon/banker.py` | imports `legs`, keeps `_run_leg`/… as aliases | 9 |
| `ControllerAddon/gear.py` (new) | worn/carried names, `equip_from_inventory`, `ensure_worn` | 10 |
| `ControllerAddon/tests/test_gear.py` (new) | equip is confirmed by `/equipment`, never assumed | 10 |
| `CLAUDE.md`, `docs/routine-notes.md` | routine index rows, section rename | 12 |

---

### Task 1: Move the fight loop to `combat_core.py` and add `routines/combat.py`

**Files:**
- Move: `ControllerAddon/routines/slayer.py` → `ControllerAddon/combat_core.py`
- Move: `ControllerAddon/tests/test_slayer.py` → `ControllerAddon/tests/test_combat.py`
- Create: `ControllerAddon/routines/combat.py`

**Interfaces:**
- Produces: `combat_core.fight_tick(ctx) -> None` (today's `tick`), `combat_core.on_event(ctx, event_type, payload)`, `combat_core.BANK_NO_PROGRESS_MAX: int = 3`, and every existing public helper unchanged (`needs_banking`, `out_of_critical_supplies`, `protected_supply_names`, `resolve_targets`, `pick_target`, `maintain_*`, `maybe_loot`, `engage_target`, constants).
- Produces: `routines/combat.py` with `get_config(bridge_config) -> dict`, `on_event`, `tick`.

- [ ] **Step 1: Move the files with git so history follows**

Run from the repo root:
```powershell
git mv ControllerAddon/routines/slayer.py ControllerAddon/combat_core.py
git mv ControllerAddon/tests/test_slayer.py ControllerAddon/tests/test_combat.py
```

- [ ] **Step 2: Re-point the moved test file at the new module**

In `ControllerAddon/tests/test_combat.py`, replace line 10 `from routines import slayer` with:
```python
import combat_core
slayer = combat_core   # the fixture and the 19 moved tests keep the short name
```
and replace the two calls `slayer.tick(ctx)` (in the two `tick` tests) with `slayer.fight_tick(ctx)`. Do this by hand or with a CASE-SENSITIVE replace (PowerShell `-replace` is case-insensitive; use `-creplace`) limited to the exact string `slayer.tick(` — do not touch other `slayer` occurrences, the alias covers them:
```powershell
(Get-Content tests/test_combat.py -Raw) -creplace 'from routines import slayer', "import combat_core`nslayer = combat_core   # the fixture and the 19 moved tests keep the short name" -creplace 'slayer\.tick\(', 'slayer.fight_tick(' | Set-Content tests/test_combat.py -NoNewline
```
Then open the file and confirm by eye that the `quiet_tick` fixture still reads `monkeypatch.setattr(slayer, "maintain_defensive_prayer", ...)` (now aliased to `combat_core`) and the two `tick()` tests call `slayer.fight_tick(ctx)`.

- [ ] **Step 3: Add the failing test for the undefined `BANK_NO_PROGRESS_MAX`**

Append to `ControllerAddon/tests/test_combat.py`:
```python
def test_repeated_no_progress_bank_trips_end_in_a_safety_stop(monkeypatch):
    """BANK_NO_PROGRESS_MAX was used at the bank branch of the tick but never defined
    anywhere (found by the 2026-09-18 survey): two consecutive bank trips that did not
    restock raised NameError instead of stopping. Three no-progress trips must stop."""
    ctx = _Ctx({"bank": {"enabled": True}, "food": {"enabled": True, "eatAt": 40}})
    monkeypatch.setattr(controller, "get_ammo_count", lambda: None)
    monkeypatch.setattr(combat_core, "needs_banking", lambda ctx: True)
    monkeypatch.setattr(combat_core, "out_of_critical_supplies", lambda ctx: True)
    monkeypatch.setattr(banker, "bank_up", lambda ctx, cfg: True)

    for _ in range(combat_core.BANK_NO_PROGRESS_MAX):
        combat_core.fight_tick(ctx)

    assert ctx.state.stops, "no safety stop after repeated no-progress bank trips"
    assert "bank withdraw list" in ctx.state.stops[0]


def test_combat_routine_is_a_thin_wrapper_over_combat_core():
    from routines import combat
    assert combat.tick is combat_core.fight_tick
    assert combat.on_event is combat_core.on_event
```

- [ ] **Step 4: Run the moved suite to see the two new tests fail**

Run from `ControllerAddon/`: `python -m pytest tests/test_combat.py -q`
Expected: the 19 existing tests error on import (`combat_core` has no `fight_tick` yet / `routines.combat` missing) — that is fine; the point is the file imports the new names. If the whole module fails to import, that is the expected failure at this step.

- [ ] **Step 5: Edit `combat_core.py`: header, drop `get_config`, rename `tick`, define the constant**

Replace lines 1-10 of `ControllerAddon/combat_core.py` (the `# routines/slayer.py` header through `#     python controller.py routines/slayer.py`) with:
```python
# combat_core.py
#
# The shared fight loop: pick a target from a config's monster list, fight it, keep
# prayer/food/potions topped up, loot, bury bones, bank when needed. Two routines drive it:
#   routines/combat.py  -- config straight from the Controller Settings panel (GET /slayer-config)
#   routines/slayer.py  -- the task orchestrator, which substitutes the current task's monsters
#                          into that config (combat_config) and delegates each FIGHT tick here.
# Moved here from routines/slayer.py 2026-09-18 (git mv; the history is that file's).
# See com.example.controller.ControllerConfig / ConfigExporter for the config shape.
```

Delete the `get_config` function (the three lines `def get_config(bridge_config): return controller.get_slayer_config()` and the comment block above it that begins `# GET /slayer-config already includes the master switch`).

Rename `def tick(ctx):` to `def fight_tick(ctx):`.

Directly above `def needs_banking(ctx) -> bool:` insert:
```python
# Consecutive bank trips that completed but left a critical-supply emergency unresolved before
# the routine stops and asks a human to fix the withdraw list. Referenced by fight_tick's bank
# branch since 2026-08-06; DEFINED only on 2026-09-18 (it raised NameError before that).
BANK_NO_PROGRESS_MAX = 3
```

- [ ] **Step 6: Create `routines/combat.py`**

```python
# routines/combat.py
#
# "Combat" in the Controller Settings routine dropdown: fight whatever monsters are picked in
# the panel's Slayer section, using the shared fight loop in combat_core.py. This is exactly
# what routines/slayer.py did until 2026-09-18; slayer.py is now the task orchestrator.
#
# Run with:
#     python controller.py routines/combat.py

import combat_core
import controller


# GET /slayer-config already includes the master switch (SlayerConfigProvider reads it off
# ControllerConfig too) alongside every combat setting -- bridge_config (GET /config) isn't
# needed here.
def get_config(bridge_config):
    return controller.get_slayer_config()


on_event = combat_core.on_event
tick = combat_core.fight_tick
```

- [ ] **Step 7: Compile and run the combat tests**

Run from `ControllerAddon/`:
```powershell
python -m py_compile combat_core.py routines/combat.py tests/test_combat.py
python -m pytest tests/test_combat.py -q
```
Expected: 21 passed.

- [ ] **Step 8: Run the whole suite (nothing else imported `routines.slayer`, confirm)**

Run: `python -m pytest tests/ -q`
Expected: all green. If anything fails with `ModuleNotFoundError: routines.slayer`, that caller must import `combat_core` instead (the 2026-09-18 survey found only `test_slayer.py` imported it).

- [ ] **Step 9: Commit**

```powershell
git add -A ControllerAddon/combat_core.py ControllerAddon/routines/combat.py ControllerAddon/tests/test_combat.py
git commit -m "Split: move the fight loop to combat_core.py, add routines/combat.py

routines/slayer.py becomes combat_core.py (git mv, history kept); the
Combat routine is a thin wrapper. Defines BANK_NO_PROGRESS_MAX, which the
bank branch referenced since 2026-08-06 but nothing ever defined.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `combat_core.combat_config()` — the orchestrator's hook into the fight loop

**Files:**
- Modify: `ControllerAddon/combat_core.py` (append after `resolve_targets`)
- Test: `ControllerAddon/tests/test_combat.py`

**Interfaces:**
- Produces: `combat_core.combat_config(base_config: dict, target_names) -> dict` — a COPY of `base_config` with `targetNames` replaced by `list(target_names)`, `targets` set to `[]`, and `safespot` forced `{"enabled": False, "marked": False}` (other safespot keys preserved). Never mutates `base_config`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_combat.py`:
```python
def test_combat_config_substitutes_the_task_targets_without_touching_the_panel_config():
    base = {"targetNames": ["Cow"], "targets": [2805],
            "safespot": {"enabled": True, "marked": True, "x": 1, "y": 2, "plane": 0},
            "food": {"enabled": True}}
    cfg = combat_core.combat_config(base, ("Banshee", "Screaming banshee"))

    assert cfg["targetNames"] == ["Banshee", "Screaming banshee"]
    assert cfg["targets"] == [], "ids are only ever resolved for NPCs in the scene; names are the durable signal"
    assert cfg["safespot"]["enabled"] is False and cfg["safespot"]["marked"] is False
    assert cfg["safespot"]["x"] == 1, "other safespot fields survive"
    assert cfg["food"] == {"enabled": True}
    # The panel config the caller holds is untouched.
    assert base["targetNames"] == ["Cow"] and base["targets"] == [2805]
    assert base["safespot"]["enabled"] is True


def test_combat_config_targets_resolve_through_the_normal_singularizing_matcher():
    cfg = combat_core.combat_config({}, ("Banshees",))
    ids, names = combat_core.resolve_targets(cfg)
    assert ids == set()
    assert "banshee" in names and "banshees" in names
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_combat.py -k combat_config -q`
Expected: FAIL with `AttributeError: module 'combat_core' has no attribute 'combat_config'`.

- [ ] **Step 3: Implement**

Append to `ControllerAddon/combat_core.py`, directly after `resolve_targets`:
```python
def combat_config(base_config: dict, target_names) -> dict:
    """The panel config with a task's monsters substituted in -- what the Slayer task
    orchestrator hands fight_tick for each FIGHT tick (spec §5).

    `targets` (ids) is emptied on purpose: ConfigExporter.resolveTargetIds only ever fills it
    with ids of NPCs currently in the scene, so names are the durable signal and a stale id list
    from the panel would fight the wrong monster. A marked safespot belongs to one place, so it
    is forced off for a task fight. The panel config is copied, never mutated."""
    cfg = dict(base_config or {})
    cfg["targetNames"] = list(target_names or ())
    cfg["targets"] = []
    safespot = dict(cfg.get("safespot") or {})
    safespot["enabled"] = False
    safespot["marked"] = False
    cfg["safespot"] = safespot
    return cfg
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_combat.py -q`
Expected: 23 passed.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/combat_core.py ControllerAddon/tests/test_combat.py
git commit -m "combat_core.combat_config: substitute a task's targets into the panel config

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `COMBAT` routine choice, `ROUTINE_FILES`, and the `routines/slayer.py` stub

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java:12`
- Modify: `ControllerAddon/controller.py:3664-3666` (`ROUTINE_FILES`)
- Create: `ControllerAddon/routines/slayer.py`
- Test: `ControllerAddon/tests/test_combat.py`

**Interfaces:**
- Produces: `RoutineChoice.COMBAT("Combat")`, `RoutineChoice.SLAYER("Slayer (tasks)")`; `ROUTINE_FILES["COMBAT"] == "routines/combat.py"`, `ROUTINE_FILES["SLAYER"] == "routines/slayer.py"`.
- Produces: `routines/slayer.py` exposing `SKIP_STRAY_DIALOG_CHECK = True`, `get_config`, `tick` (stub that safety-stops). Sub-project 2 replaces this file wholesale.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_combat.py`:
```python
import os
import re


def test_combat_and_slayer_are_both_wired_into_routine_files_and_routine_choice():
    """Wiring a routine into ROUTINE_FILES AND RoutineChoice is a required step, not polish
    (CLAUDE.md). The split adds COMBAT and re-points SLAYER at the task orchestrator."""
    assert controller.ROUTINE_FILES["COMBAT"] == "routines/combat.py"
    assert controller.ROUTINE_FILES["SLAYER"] == "routines/slayer.py"
    java = open(os.path.join(os.path.dirname(__file__), "..", "..", "src", "main", "java", "com",
                             "example", "controller", "RoutineChoice.java"), encoding="utf-8").read()
    names = set(re.findall(r"^\s*([A-Z_0-9]+)\(\"", java, flags=re.M))
    assert "COMBAT" in names and "SLAYER" in names
    assert set(controller.ROUTINE_FILES) <= names, "every ROUTINE_FILES key must be a RoutineChoice"
    for rel in controller.ROUTINE_FILES.values():
        assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", rel)), rel


def test_the_slayer_stub_stops_and_points_at_the_spec():
    from routines import slayer
    ctx = _Ctx({})
    slayer.tick(ctx)
    assert ctx.state.stops and "Combat" in ctx.state.stops[0]
    assert slayer.SKIP_STRAY_DIALOG_CHECK is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_combat.py -k "wired or stub" -q`
Expected: FAIL — `KeyError: 'COMBAT'`, and `ModuleNotFoundError: routines.slayer`.

- [ ] **Step 3: Edit `RoutineChoice.java`**

Replace line 12 `SLAYER("Slayer / Combat"),` with:
```java
	COMBAT("Combat"),
	SLAYER("Slayer (tasks)"),
```
and update the javadoc line 5 example from `SLAYER -> routines/slayer.py` to `COMBAT -> routines/combat.py`.

- [ ] **Step 4: Edit `ROUTINE_FILES` in `controller.py`**

Replace line 3665 `    "SLAYER": "routines/slayer.py",` with:
```python
    "COMBAT": "routines/combat.py",
    "SLAYER": "routines/slayer.py",
```

- [ ] **Step 5: Create the stub `routines/slayer.py`**

```python
# routines/slayer.py
#
# PLACEHOLDER (2026-09-18). The Slayer task orchestrator -- NPC Contact task acquisition,
# Aya/Kuradal milestone selection, per-task banking and travel, the fight delegated to
# combat_core -- is designed in docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md
# and is built in that spec's sub-projects 2 and 3. Until then this file exists so the
# "Slayer (tasks)" panel choice loads something that says so instead of failing to load.
#
# The old "Slayer / Combat" behaviour (fight the monsters picked in the panel) is
# routines/combat.py, "Combat" in the dropdown.

import controller

# The orchestrator owns every dialogue it opens (spec §7); set here already so the engine's
# once-per-tick Space never lands in a slayer master's dialogue.
SKIP_STRAY_DIALOG_CHECK = True


def get_config(bridge_config):
    return controller.get_slayer_config()


def tick(ctx):
    ctx.state.trigger_safety_stop(
        "the Slayer task routine is not built yet (docs/superpowers/specs/"
        "2026-09-18-slayer-task-loop-design.md) -- pick 'Combat' in Controller Settings "
        "for the old fight-the-selected-monsters behaviour"
    )
```

- [ ] **Step 6: Compile Java and run the tests**

Run from the repo root: `./gradlew.bat compileJava --offline -q`
Expected: BUILD SUCCESSFUL.
Run from `ControllerAddon/`: `python -m py_compile routines/slayer.py controller.py; python -m pytest tests/ -q`
Expected: all green (other tests that parse `RoutineChoice.java` — `test_vale_totems_routine.py:732`, `test_wintertodt.py:1019`, `test_fletching.py:758`, `test_mining_guild_iron.py:359` — still pass because they only check their own key is present).

- [ ] **Step 7: Commit**

```powershell
git add src/main/java/com/example/controller/RoutineChoice.java ControllerAddon/controller.py ControllerAddon/routines/slayer.py ControllerAddon/tests/test_combat.py
git commit -m "Routine choices: COMBAT (the old Slayer / Combat), SLAYER now the task routine stub

Anyone with 'Slayer / Combat' selected gets the stub's safety stop after
the client rebuild and is told to pick Combat -- the user's stated intent
(spec D6), called out so it is not a surprise.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Export `bracelets.reequip` (the toggle the code already reads)

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerConfig.java` (Slayer section, after `safespotPlane`)
- Modify: `src/main/java/com/example/controller/ConfigExporter.java:100-103`

**Interfaces:**
- Produces: `ControllerConfig.slayerBraceletReequip() -> boolean` (default true); `/slayer-config` JSON gains `"bracelets": {"reequip": true}`. `combat_core.maintain_bracelet` already reads `config["bracelets"]["reequip"]` (default True) — no Python change.

- [ ] **Step 1: Add the config item**

In `ControllerConfig.java`, find the last `@ConfigItem` in `slayerSection` (the hidden `safespotPlane` item) and add after it:
```java
	@ConfigItem(
		keyName = "slayerBraceletReequip",
		name = "Re-wear Slayer Bracelets",
		description = "When a bracelet of slaughter / expeditious bracelet crumbles, wear a spare from the inventory. " +
			"Only ever acts on an EMPTY gloves slot.",
		section = slayerSection,
		position = 20
	)
	default boolean slayerBraceletReequip()
	{
		return true;
	}
```

- [ ] **Step 2: Export it**

In `ConfigExporter.exportSlayer`, after `data.put("safespot", safespot);` (line 100) add:
```java
		// Read by combat_core.maintain_bracelet since 2026-09-18 (it defaulted to true because
		// nothing exported the key -- found by the split survey).
		Map<String, Object> bracelets = new LinkedHashMap<>();
		bracelets.put("reequip", config.slayerBraceletReequip());
		data.put("bracelets", bracelets);
```

- [ ] **Step 3: Compile**

Run from the repo root: `./gradlew.bat compileJava --offline -q`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```powershell
git add src/main/java/com/example/controller/ControllerConfig.java src/main/java/com/example/controller/ConfigExporter.java
git commit -m "Export bracelets.reequip on /slayer-config; the fight loop already honoured it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `GET /varp?id=` and `controller.get_varp()`

**Files:**
- Modify: `src/main/java/com/example/providers/PlayerProvider.java:47-79`
- Modify: `ControllerAddon/controller.py` (after `get_varbit`, line ~293)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Produces: `GET /varp?id=N` → `{"value": <int>}` or `{"value": null}` on a bad/missing id. `controller.get_varp(varp_id: int) -> int | None` (None = "could not read", never zero).

- [ ] **Step 1: Write the failing Python test**

Append to `ControllerAddon/tests/test_controller.py`:
```python
def test_get_varp_reads_the_varp_endpoint_and_returns_none_when_unreadable(monkeypatch):
    """/varp is the varp twin of /varbit (PlayerProvider). None means 'cannot verify', which
    callers must not collapse into zero -- varp 394 == 0 is 'no slayer task'."""
    calls = []

    def api_get(path, params=None, timeout=0.3):
        calls.append((path, params))
        return {"value": 27}
    monkeypatch.setattr(controller, "api_get", api_get)
    assert controller.get_varp(394) == 27
    assert calls == [("/varp", {"id": 394})]

    monkeypatch.setattr(controller, "api_get", lambda *a, **k: None)
    assert controller.get_varp(394) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_controller.py -k get_varp -q`
Expected: FAIL with `AttributeError: module 'controller' has no attribute 'get_varp'`.

- [ ] **Step 3: Add the Java endpoint**

In `PlayerProvider.register()` add after the `/varbit` line:
```java
		server.registerQuery("/varp", query -> onClient(() -> getVarp(query)));
```
Replace the whole `getVarbit(Map<String, String> query)` method with:
```java
	private Object getVarbit(Map<String, String> query)
	{
		return readVar(query, client::getVarbitValue);
	}

	// ADDED 2026-09-18 (slayer task loop, spec §10): the varp twin of /varbit. Slayer task state
	// is varps (394 kills remaining, 395 task id, 4258 original amount, 2096 Konar area) and
	// nothing exposed a varp generically before this. Same shape and same "null = unreadable"
	// rule as /varbit.
	private Object getVarp(Map<String, String> query)
	{
		return readVar(query, client::getVarpValue);
	}

	private static Object readVar(Map<String, String> query, java.util.function.IntFunction<Integer> read)
	{
		Map<String, Object> result = new HashMap<>();
		String idStr = query.get("id");
		if (idStr == null || idStr.isEmpty())
		{
			result.put("value", null);
			return result;
		}
		try
		{
			result.put("value", read.apply(Integer.parseInt(idStr)));
		}
		catch (NumberFormatException e)
		{
			result.put("value", null);
		}
		return result;
	}
```

- [ ] **Step 4: Add the Python wrapper**

In `ControllerAddon/controller.py`, directly after `get_varbit` (after its `return resp.get("value")` line) add:
```python
def get_varp(varp_id: int):
    """Raw client.getVarpValue(varp_id) passthrough (PlayerProvider's /varp) -- the varp twin
    of get_varbit, added 2026-09-18 for slayer task state (varp 394 kills remaining, 395 task
    id). Returns None if the value couldn't be read; callers treat that as "can't verify",
    never as zero (0 is a real answer here: it means no task)."""
    resp = api_get("/varp", params={"id": varp_id}) or {}
    return resp.get("value")
```

- [ ] **Step 5: Compile and test**

Run from the repo root: `./gradlew.bat compileJava --offline -q` — expected BUILD SUCCESSFUL.
Run from `ControllerAddon/`: `python -m pytest tests/test_controller.py -k get_varp -q` — expected 1 passed.

- [ ] **Step 6: Commit**

```powershell
git add src/main/java/com/example/providers/PlayerProvider.java ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "GET /varp?id= and controller.get_varp: the varp twin of /varbit

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `SlayerTaskState` — the pure half of the task endpoint

**Files:**
- Create: `src/main/java/com/example/providers/SlayerTaskState.java`
- Test: `src/test/java/com/example/providers/SlayerTaskStateTest.java`

**Interfaces:**
- Produces: `new SlayerTaskState(int count, int originalCount, int taskId, String taskName, int areaId, String areaName, int masterId, int streak, int points, int bossId)` and `Map<String, Object> toMap()` with keys `count, originalCount, taskId, taskName, areaId, areaName, masterId, streak, points, bossId, hasTask`. `hasTask == count > 0`. `taskName`/`areaName` are emitted as JSON `null` when passed null (never `""`).

- [ ] **Step 1: Write the failing test**

Create `src/test/java/com/example/providers/SlayerTaskStateTest.java`:
```java
package com.example.providers;

import org.junit.Test;

import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

/**
 * The pure half of SlayerProvider, split out so the two rules the Python side depends on can
 * be pinned without a client: hasTask is "count > 0" (how RuneLite's own SlayerPlugin detects
 * completion), and a name the DB lookup could not resolve is null, never "" -- so "no task"
 * and "could not decode" stay distinguishable (spec §10).
 */
public class SlayerTaskStateTest
{
	@Test
	public void hasTaskFollowsTheKillCount()
	{
		Map<String, Object> on = new SlayerTaskState(27, 30, 4135, "Banshees", 0, null, 2, 19, 415, 0).toMap();
		assertTrue((Boolean) on.get("hasTask"));
		assertEquals(27, on.get("count"));
		assertEquals("Banshees", on.get("taskName"));
		assertEquals(19, on.get("streak"));
		assertEquals(415, on.get("points"));

		Map<String, Object> off = new SlayerTaskState(0, 30, 4135, null, 0, null, 2, 20, 490, 0).toMap();
		assertFalse((Boolean) off.get("hasTask"));
	}

	@Test
	public void anUnresolvedNameIsNullNotEmpty()
	{
		Map<String, Object> m = new SlayerTaskState(5, 5, 9999, null, 7, null, 2, 1, 0, 0).toMap();
		assertTrue(m.containsKey("taskName"));
		assertNull(m.get("taskName"));
		assertTrue(m.containsKey("areaName"));
		assertNull(m.get("areaName"));
	}
}
```

- [ ] **Step 2: Run to verify it fails to compile**

Run from the repo root: `./gradlew.bat test --offline --tests com.example.providers.SlayerTaskStateTest`
Expected: compilation failure, `cannot find symbol SlayerTaskState`.

- [ ] **Step 3: Implement**

Create `src/main/java/com/example/providers/SlayerTaskState.java`:
```java
package com.example.providers;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * One read of the slayer task varps/varbits, already decoded -- the value SlayerProvider
 * serves on GET /slayer. Pure so the rules are testable without a client (SlayerTaskStateTest),
 * the same split RecordRing and BankSnapshot got.
 *
 * Field sources (RuneLite 1.12.39, plugins/slayer/SlayerPlugin.updateTask): count = varp 394
 * SLAYER_COUNT, originalCount = varp 4258 SLAYER_COUNT_ORIGINAL, taskId = varp 395
 * SLAYER_TARGET, areaId = varp 2096 SLAYER_AREA (Konar only), masterId = varbit 4067
 * SLAYER_MASTER, streak = varbit 4069 SLAYER_TASKS_COMPLETED, points = varbit 4068
 * SLAYER_POINTS, bossId = varbit 4723 SLAYER_TARGET_BOSSID. taskName/areaName come from DB
 * tables 113/115 and are null when the lookup fails -- never "" -- so the Python side can tell
 * "no task" (count 0) from "could not decode" (count > 0, name null).
 */
public final class SlayerTaskState
{
	public final int count;
	public final int originalCount;
	public final int taskId;
	public final String taskName;
	public final int areaId;
	public final String areaName;
	public final int masterId;
	public final int streak;
	public final int points;
	public final int bossId;

	public SlayerTaskState(int count, int originalCount, int taskId, String taskName,
		int areaId, String areaName, int masterId, int streak, int points, int bossId)
	{
		this.count = count;
		this.originalCount = originalCount;
		this.taskId = taskId;
		this.taskName = taskName;
		this.areaId = areaId;
		this.areaName = areaName;
		this.masterId = masterId;
		this.streak = streak;
		this.points = points;
		this.bossId = bossId;
	}

	/** Completion is "count went to 0" -- exactly how SlayerPlugin detects it (:448-452). */
	public boolean hasTask()
	{
		return count > 0;
	}

	public Map<String, Object> toMap()
	{
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("count", count);
		m.put("originalCount", originalCount);
		m.put("taskId", taskId);
		m.put("taskName", taskName);
		m.put("areaId", areaId);
		m.put("areaName", areaName);
		m.put("masterId", masterId);
		m.put("streak", streak);
		m.put("points", points);
		m.put("bossId", bossId);
		m.put("hasTask", hasTask());
		return m;
	}
}
```

- [ ] **Step 4: Run the test**

Run: `./gradlew.bat test --offline --tests com.example.providers.SlayerTaskStateTest`
Expected: BUILD SUCCESSFUL, 2 tests passed.

- [ ] **Step 5: Commit**

```powershell
git add src/main/java/com/example/providers/SlayerTaskState.java src/test/java/com/example/providers/SlayerTaskStateTest.java
git commit -m "SlayerTaskState: the pure, testable half of the /slayer endpoint

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `SlayerProvider` — `GET /slayer` — and `controller.get_slayer_task()`

**Files:**
- Create: `src/main/java/com/example/providers/SlayerProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:176` (register after `SlayerConfigProvider`)
- Modify: `ControllerAddon/controller.py` (after `get_slayer_config`, line ~1093)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: `SlayerTaskState` (Task 6).
- Produces: `GET /slayer` → the `SlayerTaskState.toMap()` JSON. `controller.get_slayer_task() -> dict | None` — `None` when the bridge/endpoint is unavailable (an UNREBUILT client answers 404 here, because `/slayer-config` is not a path prefix of `/slayer`); a dict with `hasTask` otherwise.

- [ ] **Step 1: Write the failing Python test**

Append to `ControllerAddon/tests/test_controller.py`:
```python
def test_get_slayer_task_returns_the_endpoint_dict_or_none(monkeypatch):
    """None means 'bridge/endpoint unavailable' (an un-rebuilt client 404s /slayer) and the
    orchestrator must do nothing on it -- never guess a task from a missing read."""
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, timeout=0.3: {"count": 0, "hasTask": False, "streak": 9})
    assert controller.get_slayer_task() == {"count": 0, "hasTask": False, "streak": 9}
    monkeypatch.setattr(controller, "api_get", lambda *a, **k: None)
    assert controller.get_slayer_task() is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_controller.py -k get_slayer_task -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Create `SlayerProvider.java`**

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.gameval.DBTableID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.List;

/**
 * GET /slayer -- the current slayer task, streak and points, decoded the way RuneLite's own
 * SlayerPlugin.updateTask() does it (client 1.12.39, plugins/slayer/SlayerPlugin.java:362-453):
 * varps 394/395/4258/2096 + varbits 4067/4068/4069/4723, with the task NAME looked up in DB
 * table 113 (SlayerTask, COL_NAME_UPPERCASE) -- or, for the "Bosses" pseudo-task (id 98), via
 * table 116 (SlayerTaskSublist) keyed on varbit 4723.
 *
 * Added 2026-09-18 for the slayer task loop (spec §10). Nothing in the repo read task state
 * before this. No TickCache on purpose: these are varp reads, cheap and read fresh on the client
 * thread like /player. An UNREBUILT client answers 404 here (unlike /bank/memory, which fell
 * through to /bank by longest prefix) because /slayer-config is not a prefix of /slayer.
 *
 * masterId is the RAW varbit 4067. Only Krystilia (7) and Mortimer (10) are known upstream;
 * Aya's and Kuradal's values are read live in the spec's §12 checklist, not guessed here.
 */
public class SlayerProvider extends BridgeProvider
{
	/** SlayerPlugin.java:370 -- "Bosses, from [proc,helper_slayer_current_assignment]". */
	static final int BOSS_TASK_ID = 98;

	public SlayerProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/slayer", () -> onClient(this::read));
	}

	private Object read()
	{
		int count = client.getVarpValue(VarPlayerID.SLAYER_COUNT);
		int taskId = client.getVarpValue(VarPlayerID.SLAYER_TARGET);
		int original = client.getVarpValue(VarPlayerID.SLAYER_COUNT_ORIGINAL);
		int areaId = client.getVarpValue(VarPlayerID.SLAYER_AREA);
		int masterId = client.getVarbitValue(VarbitID.SLAYER_MASTER);
		int streak = client.getVarbitValue(VarbitID.SLAYER_TASKS_COMPLETED);
		int points = client.getVarbitValue(VarbitID.SLAYER_POINTS);
		int bossId = client.getVarbitValue(VarbitID.SLAYER_TARGET_BOSSID);

		String taskName = count > 0 ? taskName(taskId, bossId) : null;
		String areaName = areaId > 0 ? areaName(areaId) : null;

		return new SlayerTaskState(count, original, taskId, taskName, areaId, areaName,
			masterId, streak, points, bossId).toMap();
	}

	private String taskName(int taskId, int bossId)
	{
		try
		{
			int row;
			if (taskId == BOSS_TASK_ID)
			{
				List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerTaskSublist.ID,
					DBTableID.SlayerTaskSublist.COL_TASK_SUBTABLE_ID, 0, bossId);
				if (rows == null || rows.isEmpty())
				{
					return null;
				}
				row = (Integer) client.getDBTableField(rows.get(0), DBTableID.SlayerTaskSublist.COL_TASK, 0)[0];
			}
			else
			{
				List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerTask.ID,
					DBTableID.SlayerTask.COL_ID, 0, taskId);
				if (rows == null || rows.isEmpty())
				{
					return null;
				}
				row = rows.get(0);
			}
			Object[] name = client.getDBTableField(row, DBTableID.SlayerTask.COL_NAME_UPPERCASE, 0);
			return name != null && name.length > 0 && name[0] != null ? String.valueOf(name[0]) : null;
		}
		catch (RuntimeException e)
		{
			// A DB table that is not loaded yet (login screen, mid-load) throws rather than
			// answering empty. Null is the documented "could not decode" answer.
			return null;
		}
	}

	private String areaName(int areaId)
	{
		try
		{
			List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerArea.ID,
				DBTableID.SlayerArea.COL_AREA_ID, 0, areaId);
			if (rows == null || rows.isEmpty())
			{
				return null;
			}
			Object[] name = client.getDBTableField(rows.get(0), DBTableID.SlayerArea.COL_AREA_NAME_IN_HELPER, 0);
			return name != null && name.length > 0 && name[0] != null ? String.valueOf(name[0]) : null;
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}
}
```

- [ ] **Step 4: Register it**

In `ControllerPlugin.java`, after line 176 `new SlayerConfigProvider(client, clientThread, server, config).register();` add:
```java
		new SlayerProvider(client, clientThread, server).register();
```

- [ ] **Step 5: Add the Python wrapper**

In `ControllerAddon/controller.py`, directly after `get_slayer_config` add:
```python
def get_slayer_task():
    """GET /slayer (SlayerProvider, added 2026-09-18): the current slayer task decoded from the
    varps/varbits RuneLite's own Slayer plugin reads -- {count, originalCount, taskId, taskName,
    areaId, areaName, masterId, streak, points, bossId, hasTask}. None when the bridge or the
    endpoint is unavailable (an un-rebuilt client 404s it); a routine must do NOTHING on None
    rather than infer a task. taskName is None (not "") when the DB lookup failed."""
    return api_get("/slayer")
```

- [ ] **Step 6: Compile, run Java tests, run the Python test**

Run from the repo root: `./gradlew.bat test --offline -q` — expected BUILD SUCCESSFUL (all existing Java tests still green).
Run from `ControllerAddon/`: `python -m pytest tests/test_controller.py -k slayer_task -q` — expected 1 passed.

- [ ] **Step 7: Commit**

```powershell
git add src/main/java/com/example/providers/SlayerProvider.java src/main/java/com/example/controller/ControllerPlugin.java ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "GET /slayer: task, streak and points decoded like RuneLite's SlayerPlugin

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `dialogue.py` — one shared chat-dialogue helper

**Files:**
- Create: `ControllerAddon/dialogue.py`
- Create: `ControllerAddon/tests/test_dialogue.py`
- Modify: `ControllerAddon/routines/golem_crafting.py:3892-3896` (constants) and `:4129-4189` (the three helpers)
- Modify: `ControllerAddon/routines/giants_foundry.py:1732-1768` (`_dialogue`, `_dialogue_open`, `_dialogue_options`)
- Modify: `ControllerAddon/tests/test_golem_crafting.py:1881-1943` (move the four paged-dialogue tests out)

**Interfaces:**
- Produces:
  - `dialogue.read() -> dict` — `/api/ui/dialogue` or `{}` on failure/error.
  - `dialogue.is_open(d: dict) -> bool` — options OR npcText OR playerText OR itemText (the giants_foundry rule: the reader has no open flag).
  - `dialogue.options(d: dict | None = None) -> list[str]` — real options in number-key order (title at raw index 0 dropped, blanks dropped); reads `read()` when `d` is None.
  - `dialogue.find_option(options, needle: str) -> int | None` — case-insensitive substring.
  - `dialogue.choose(ctx, needle: str, budget_s: float = CHOOSE_BUDGET_S, log: str = "dialogue") -> bool` — paged, bounded, never re-presses a page-forward that changed nothing.
  - `dialogue.close(ctx) -> None` — ESC.
  - Constants `MORE_OPTIONS = "more option"`, `MAX_PAGES = 3`, `PAGE_S = 0.6`, `CHOOSE_BUDGET_S = 6.0`.
- Golem keeps `_dialogue_options`, `find_dialogue_option`, `_choose_dialogue_option`, `DIALOGUE_S`, `DIALOGUE_MORE_OPTIONS`, `DIALOGUE_MAX_PAGES`, `DIALOGUE_PAGE_S` as thin aliases/wrappers so its other call sites are untouched. Giants keeps `_dialogue`, `_dialogue_open`, `_dialogue_options` as aliases.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_dialogue.py`:
```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import dialogue


# --- The slayer ring's destination dialogue is PAGED (2026-09-14, live; moved from golem) -----
#
# USER: "On slayer ring there is dialog options to tp or a second menu that expand when hovering
# tele". MEASURED live off /api/ui/dialogue -- Wyrmscraig is not on page one:
#   page 1: 1 Stronghold Slayer Cave | 2 Morytania Slayer Tower | 3 Fremennik Slayer Dungeon
#           4 More options...
#   page 2: 1 Tarn's Lair | 2 Dark Beasts | 3 the Wyrmscraig Cavern | 4 Previous options...

PAGE_1 = ["Teleport to the Stronghold Slayer Cave", "Teleport to the Morytania Slayer Tower",
          "Teleport to the Fremennik Slayer Dungeon", "More options..."]
PAGE_2 = ["Teleport to Tarn's Lair", "Teleport to Dark Beasts",
          "Teleport to the Wyrmscraig Cavern", "Previous options..."]


class _State:
    def __init__(self):
        self.data = {}


class _Ctx:
    def __init__(self):
        self.ser = None
        self.state = _State()
        self.events = None


def _fake_clock(monkeypatch):
    state = {"t": 5000.0}
    monkeypatch.setattr(dialogue.time, "time", lambda: state["t"])
    monkeypatch.setattr(dialogue.time, "sleep", lambda s: state.__setitem__("t", state["t"] + s))
    return state


def _paged_dialogue(monkeypatch, pages):
    """A dialogue whose 'More options...' key advances to the next page."""
    state = {"page": 0}
    keys = []

    def options(d=None):
        return pages[state["page"]]

    def key_tap(ser, key):
        keys.append(key)
        chosen = pages[state["page"]][int(key) - 1]
        if "more option" in chosen.lower():
            state["page"] = min(state["page"] + 1, len(pages) - 1)
    monkeypatch.setattr(dialogue, "options", options)
    monkeypatch.setattr(controller, "key_tap", key_tap)
    # Deliberately NOT stubbing sleep: _fake_clock's sleep is what ADVANCES the fake clock, so a
    # no-op here freezes time and the bounded wait can never time out. That hung the suite once.
    return keys


def test_the_wyrmscraig_entry_is_found_on_the_second_page(monkeypatch):
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_1, PAGE_2])
    assert dialogue.choose(_Ctx(), "wyrmscraig") is True
    assert keys == ["4", "3"], "must take 'More options...' (4) then Wyrmscraig (3)"


def test_a_first_page_hit_does_not_page_at_all(monkeypatch):
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_2, PAGE_1])
    assert dialogue.choose(_Ctx(), "wyrmscraig") is True
    assert keys == ["3"]


def test_a_dialogue_that_never_advances_cannot_spin(monkeypatch):
    """A page-forward that answers with the same options must not be pressed forever."""
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_1])          # 'More options...' goes nowhere
    assert dialogue.choose(_Ctx(), "wyrmscraig") is False
    assert keys.count("4") <= dialogue.MAX_PAGES, f"paged forever: {keys}"


def test_find_option_is_a_case_insensitive_substring():
    assert dialogue.find_option(PAGE_2, "wyrmscraig") == 2
    assert dialogue.find_option(PAGE_1, "wyrmscraig") is None


def test_options_drop_the_title_and_blank_trailing_children():
    """MEASURED live 2026-09-03 (Giants' Foundry): the m8aq reader returns the TITLE as child 0
    then blank trailing children. Counting keys over the raw list pressed the wrong option."""
    d = {"options": ["How many would you like to add?", "1", "3", "X", "All", "", ""]}
    assert dialogue.options(d) == ["1", "3", "X", "All"]
    assert dialogue.options({"options": [{"text": "Select an Option"}, {"text": "Yes"}]}) == ["Yes"]


def test_is_open_sees_a_continue_only_stage():
    """The reader has NO open/visible field; a 'click here to continue' stage has text but no
    options and used to read as CLOSED (giants_foundry re-clicked Kovac mid-dialogue)."""
    assert dialogue.is_open({"npcText": "Well done.", "options": []}) is True
    assert dialogue.is_open({"playerText": "Hi"}) is True
    assert dialogue.is_open({"itemText": "You feel refreshed."}) is True
    assert dialogue.is_open({"options": ["Select an Option", "Yes"]}) is True
    assert dialogue.is_open({}) is False
    assert dialogue.is_open({"options": [], "npcText": "", "playerText": None}) is False


def test_read_returns_an_empty_dict_on_a_failed_or_erroring_reader(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    assert dialogue.read() == {}
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"reader": "Dialogue", "error": "boom"})
    assert dialogue.read() == {}
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"npcName": "Aya", "options": []})
    assert dialogue.read() == {"npcName": "Aya", "options": []}


def test_close_presses_escape(monkeypatch):
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    dialogue.close(_Ctx())
    assert keys == ["ESC"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_dialogue.py -q`
Expected: FAIL at import, `ModuleNotFoundError: No module named 'dialogue'`.

- [ ] **Step 3: Create `dialogue.py`**

```python
# dialogue.py -- the chat dialogue, read off the vendored m8aq reader (GET /api/ui/dialogue).
#
# Promoted 2026-09-18 out of four private copies (golem_crafting, giants_foundry, crew,
# guardians_of_the_rift) for the slayer task loop, which walks slayer-master dialogues. Two
# measured facts shape it:
#
# - The reader has NO open/visible field (net.runelite.client.plugins.m8aq.api.ui.Dialogue has
#   only npcName/npcText/playerText/options/itemText/...). "Open" is therefore "any of those is
#   set". Collapsing it to bool(options) reads a "click here to continue" stage as CLOSED --
#   giants_foundry re-clicked Kovac mid-dialogue because of exactly that (2026-09-03).
# - `options` carries the menu TITLE as child 0, then the real options, then blank trailing
#   children (MEASURED live 2026-09-03: ['How many would you like to add?', '1', '3', 'X',
#   'All', '', '']). Number keys map to the real options, so the title must be dropped first.
#
# choose() pages through "More options..." (the slayer ring's teleport menu has two pages,
# CONFIRMED live 2026-09-14) and is bounded twice: MAX_PAGES, and "never re-press a
# page-forward that did not change the options", so a dialogue that loops cannot spin.

import time

import controller
import debug_log

MORE_OPTIONS = "more option"    # "More options..." -- the page-forward entry
MAX_PAGES = 3                   # the slayer ring has two; the cap is for a dialogue that loops
PAGE_S = 0.6                    # a page turn -> the new options being readable
CHOOSE_BUDGET_S = 6.0           # a click -> the menu, INCLUDING one page turn
POLL_S = 0.1


def read() -> dict:
    """The dialogue reader's state, or {} when the bridge/reader is unavailable or errored
    (an `error` key means the reader threw -- usually its interface is closed)."""
    d = controller.get_api("ui", "dialogue")
    if not d or d.get("error"):
        return {}
    return d


def is_open(d) -> bool:
    """True when ANY dialogue stage is up, including a continue-only one."""
    if not d:
        return False
    for key in ("open", "isOpen", "visible"):     # forward-compat if the reader grows one
        if isinstance(d.get(key), bool):
            return d[key]
    return bool(d.get("options") or d.get("npcText") or d.get("playerText") or d.get("itemText"))


def options(d=None) -> list:
    """The REAL options of the open chat menu, in number-key order (key = position + 1)."""
    if d is None:
        d = read()
    raw = [o if isinstance(o, str) else (o.get("text") or "") for o in (d.get("options") or [])]
    raw = raw[1:] if raw else raw            # child 0 is the title
    return [o for o in raw if o.strip()]


def find_option(opts, needle: str):
    """Index of the first option containing `needle` (case-insensitive), else None. Pure."""
    for i, text in enumerate(opts or []):
        if needle.lower() in (text or "").lower():
            return i
    return None


def choose(ctx, needle: str, budget_s: float = CHOOSE_BUDGET_S, log: str = "dialogue") -> bool:
    """Wait for a chat menu whose options contain `needle` and press its number key, paging
    through "More options..." when it is not on the first page. False when the budget runs out
    -- a normal outcome for the caller to handle, never a keypress guessed at."""
    deadline = time.time() + budget_s
    pages = 0
    seen = []
    while time.time() < deadline:
        opts = options()
        hit = find_option(opts, needle)
        if hit is not None:
            controller.key_tap(ctx.ser, str(hit + 1))
            return True
        more = find_option(opts, MORE_OPTIONS)
        if more is not None and opts != seen and pages < MAX_PAGES:
            debug_log.log(log, f"'{needle}' is not on this dialogue page -- taking '{opts[more]}'")
            seen = opts
            pages += 1
            controller.key_tap(ctx.ser, str(more + 1))
            time.sleep(PAGE_S)
            continue
        time.sleep(POLL_S)
    debug_log.log(log, f"no dialogue option containing '{needle}' appeared within {budget_s}s")
    return False


def close(ctx) -> None:
    """Dismiss whatever dialogue is up. ESC closes a chat menu without choosing anything."""
    controller.key_tap(ctx.ser, "ESC")
```

- [ ] **Step 4: Run the new tests**

Run: `python -m pytest tests/test_dialogue.py -q`
Expected: 8 passed.

- [ ] **Step 5: Make golem_crafting delegate**

In `ControllerAddon/routines/golem_crafting.py` add `import dialogue` to the imports at the top of the file (alphabetically among the local imports). Replace the four constants at lines 3892-3896 with:
```python
DIALOGUE_S = dialogue.CHOOSE_BUDGET_S           # kept as names: other call sites in this file
DIALOGUE_MORE_OPTIONS = dialogue.MORE_OPTIONS
DIALOGUE_MAX_PAGES = dialogue.MAX_PAGES
DIALOGUE_PAGE_S = dialogue.PAGE_S
```
Replace the three functions `_dialogue_options`, `find_dialogue_option`, `_choose_dialogue_option` (lines 4129-4189, the whole block from `def _dialogue_options() -> list:` through the `return False` of `_choose_dialogue_option`) with:
```python
# The chat-menu helpers moved to dialogue.py 2026-09-18 (the paged slayer-ring lesson lives in
# its header now); these names stay so the rest of this file reads as before.
_dialogue_options = dialogue.options
find_dialogue_option = dialogue.find_option


def _choose_dialogue_option(ctx, needle: str, budget_s: float = DIALOGUE_S) -> bool:
    return dialogue.choose(ctx, needle, budget_s, log="golem crafting")
```

- [ ] **Step 6: Move the four golem tests out**

In `ControllerAddon/tests/test_golem_crafting.py` delete lines 1881-1943: the comment block starting `# --- The slayer ring's destination dialogue is PAGED`, `PAGE_1`, `PAGE_2`, `_paged_dialogue`, and the four tests `test_the_wyrmscraig_entry_is_found_on_the_second_page`, `test_a_first_page_hit_does_not_page_at_all`, `test_a_dialogue_that_never_advances_cannot_spin`, `test_find_dialogue_option_is_a_case_insensitive_substring` (they now live in `test_dialogue.py` against the module that owns the logic). Then grep the file for `PAGE_1`/`PAGE_2`/`_paged_dialogue`; if any later test still uses them, keep those two constants and the helper (only the four tests move).

- [ ] **Step 7: Make giants_foundry delegate**

In `ControllerAddon/routines/giants_foundry.py` add `import dialogue` to the imports. Replace `_dialogue`, `_dialogue_open`, `_dialogue_options` (lines 1732-1768, through the `return [o for o in raw if o.strip()]` of `_dialogue_options`) with:
```python
# Moved to dialogue.py 2026-09-18; the "no open field" and "title at index 0" findings that
# were documented here are in its header. Names kept for the call sites and tests below.
_dialogue = dialogue.read
_dialogue_open = dialogue.is_open
_dialogue_options = dialogue.options
```
Leave `_choose_option` (substring list + fallback key) as is — it has different semantics.

- [ ] **Step 8: Compile and run the suite**

Run from `ControllerAddon/`:
```powershell
python -m py_compile dialogue.py routines/golem_crafting.py routines/giants_foundry.py
python -m pytest tests/test_dialogue.py tests/test_golem_crafting.py tests/test_giants_foundry.py -q
```
Expected: all green. If a giants_foundry test monkeypatches `gf._dialogue_options` and then calls `gf._choose_option`, it still works: `_choose_option` looks the name up on the module at call time.

- [ ] **Step 9: Commit**

```powershell
git add ControllerAddon/dialogue.py ControllerAddon/tests/test_dialogue.py ControllerAddon/routines/golem_crafting.py ControllerAddon/routines/giants_foundry.py ControllerAddon/tests/test_golem_crafting.py
git commit -m "dialogue.py: one shared chat-dialogue helper (paged choose, no-open-field rule)

Promoted from golem_crafting and giants_foundry, which now delegate.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: `legs.py` — promote the transport-leg runner out of banker, add builders and a cursor

**Files:**
- Create: `ControllerAddon/legs.py`
- Create: `ControllerAddon/tests/test_legs.py`
- Modify: `ControllerAddon/banker.py:1170-1180` (the four constants; `RETURN_TELEPORT_KEY` at 1168 stays) and `:1303-1331`, `:1422-1537` (the moved functions become aliases)
- Modify: `ControllerAddon/tests/test_banker.py:680` (`banker.time.sleep` → `legs.time.sleep`)

**Interfaces:**
- Produces (moved, same behaviour): `legs.from_transport(t) -> dict` (was `_leg_dict`), `legs.trailing_int(text)`, `legs.await_arrival(dest, budget_s=ITEM_BUDGET_S) -> bool`, `legs.run_leg(ctx, leg, log="travel") -> bool`, `legs.run_item_leg(ctx, leg, log)`, `legs.run_object_leg(ctx, leg, log)`, `legs.pick_destination(ctx, option, log)`; constants `ITEM_BUDGET_S = 6.0`, `POLL_S = 0.3`, `ARRIVAL_TILES = 12`, `PICKER_BUDGET_S = 4.0`.
- Produces (new): builders `legs.item(name, option, dest) -> dict`, `legs.fairy(code, expect, dest=None) -> dict`, `legs.walk(x, y, plane) -> dict`; `legs.run_next(ctx, leg_list, cursor: int, log="travel") -> tuple[str, int]` returning `(DONE|PROGRESS|FAILED, new_cursor)` with `DONE = "done"`, `PROGRESS = "progress"`, `FAILED = "failed"`, `FINISHED = "finished"` (cursor past the end).
- banker keeps: `_leg_dict`, `_trailing_int`, `_await_arrival`, `_run_leg`, `_run_item_leg`, `_run_object_leg`, `_pick_destination`, `RETURN_TELEPORT_BUDGET_S`, `RETURN_TELEPORT_POLL_S`, `RETURN_TELEPORT_ARRIVAL_TILES`, `PICKER_BUDGET_S` as aliases so `_use_return_teleport` and the existing tests (which monkeypatch `banker._run_leg`) keep working.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_legs.py`:
```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import legs
import travel


class _State:
    def __init__(self):
        self.data = {}
        self.stops = []

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self):
        self.ser = None
        self.state = _State()
        self.events = None


def test_builders_produce_the_flat_dict_shape_banker_already_stores():
    assert legs.item("slayer ring", "Slayer Tower", (3421, 3537, 0)) == {
        "kind": "item", "item": "slayer ring", "option": "Slayer Tower",
        "display": "slayer ring: Slayer Tower", "dest": [3421, 3537, 0],
        "origin": None, "object_id": None, "object_info": ""}
    assert legs.walk(3429, 3564, 0)["kind"] == "walk"
    assert legs.walk(3429, 3564, 0)["dest"] == [3429, 3564, 0]
    f = legs.fairy("cks", "Slayer Tower")
    assert f["kind"] == "fairy" and f["code"] == "cks" and f["expect"] == "Slayer Tower"


def test_run_next_advances_only_on_done_and_reports_finished_past_the_end(monkeypatch):
    calls = []
    monkeypatch.setattr(legs, "run_leg", lambda ctx, leg, log="travel": calls.append(leg["display"]) or True)
    chain = [legs.item("ring of dueling", "Ferox Enclave", (3151, 3635, 0)),
             legs.item("games necklace", "Burthorpe", (2898, 3553, 0))]
    ctx = _Ctx()

    assert legs.run_next(ctx, chain, 0) == (legs.DONE, 1)
    assert legs.run_next(ctx, chain, 1) == (legs.DONE, 2)
    assert legs.run_next(ctx, chain, 2) == (legs.FINISHED, 2)
    assert calls == ["ring of dueling: Ferox Enclave", "games necklace: Burthorpe"]


def test_a_failed_item_leg_keeps_the_cursor_so_the_caller_can_retry_or_stop(monkeypatch):
    monkeypatch.setattr(legs, "run_leg", lambda ctx, leg, log="travel": False)
    chain = [legs.item("slayer ring", "Slayer Tower", (3421, 3537, 0))]
    assert legs.run_next(_Ctx(), chain, 0) == (legs.FAILED, 0)


def test_a_fairy_leg_reports_progress_not_failure_while_the_hop_is_still_underway(monkeypatch):
    """travel.hop_fairy_ring is ONE bounded action per call: False means 'walked toward the
    ring / dialled, come back next tick', and a hard failure is its own safety stop."""
    answers = iter([False, False, True])
    seen = []
    monkeypatch.setattr(travel, "hop_fairy_ring",
                        lambda ctx, code, expect, **kw: seen.append((code, expect)) or next(answers))
    chain = [legs.fairy("cks", "Slayer Tower")]
    ctx = _Ctx()
    assert legs.run_next(ctx, chain, 0) == (legs.PROGRESS, 0)
    assert legs.run_next(ctx, chain, 0) == (legs.PROGRESS, 0)
    assert legs.run_next(ctx, chain, 0) == (legs.DONE, 1)
    assert seen == [("cks", "Slayer Tower")] * 3


def test_a_walk_leg_walks_to_the_exact_tile(monkeypatch):
    walks = []
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, tolerance=3, goal_adjacent=True: walks.append((target, goal_adjacent)) or True)
    assert legs.run_next(_Ctx(), [legs.walk(3429, 3564, 0)], 0) == (legs.DONE, 1)
    assert walks == [({"x": 3429, "y": 3564, "plane": 0}, False)]


def test_await_arrival_polls_the_player_until_within_the_landing_radius(monkeypatch):
    reads = iter([{}, {"x": 3000, "y": 3000, "plane": 0}, {"x": 3421, "y": 3540, "plane": 0}])
    monkeypatch.setattr(controller, "get_player", lambda: next(reads))
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((3421, 3537, 0), budget_s=2.0) is True


def test_await_arrival_gives_up_at_the_budget(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1, "plane": 0})
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((3421, 3537, 0), budget_s=1.0) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_legs.py -q`
Expected: FAIL at import, `ModuleNotFoundError: No module named 'legs'`.

- [ ] **Step 3: Create `legs.py`**

```python
# legs.py -- execute ONE transport leg (a teleport item, a fairy ring, a POH box/portal, a
# walk) and confirm it landed. Promoted 2026-09-18 out of banker.py's return-teleport runner
# (_run_leg / _run_item_leg / _run_object_leg / _pick_destination / _await_arrival, added
# 2026-09-07) so bank returns and slayer task travel share one runner. banker keeps those names
# as aliases; nothing about the bank path changed.
#
# A leg is a flat dict that survives in ctx.state.data across ticks:
#   {"kind": "item"|"fairy"|"walk"|"box"|"poh_portal", "display": str, "dest": [x, y, plane]|None,
#    "item": str|None, "option": str|None, "origin": [x, y, plane]|None,
#    "object_id": int|None, "object_info": str, "code": str|None, "expect": str|None}
# run_next() is the cursor over a chain: one bounded leg per call, so a routine's tick stays
# short and a restart resumes at the leg it was on. Object legs by NAME/ACTION (ladders, caves,
# stairs -- spec §8) are sub-project 3; "box"/"poh_portal" here are banker's POH legs, still
# UNVERIFIED live.

import time

import controller
import debug_log
import travel

ITEM_BUDGET_S = 6.0      # a teleport is ~4 ticks of animation plus a region load; falconry measured 2.4-3.2s
POLL_S = 0.3
ARRIVAL_TILES = 12       # several teleports drop you on a small spread of tiles; the walk covers the rest
PICKER_BUDGET_S = 4.0    # a transport's destination picker -> appeared and answered

DONE = "done"            # the leg landed; cursor advanced
PROGRESS = "progress"    # the leg did a bounded step and wants another call (fairy rings)
FAILED = "failed"        # the leg did not land; cursor unchanged, caller decides (retry / stop)
FINISHED = "finished"    # cursor is past the last leg


# ---------------------------------------------------------------- builders

def _base(kind, display, dest):
    return {"kind": kind, "display": display, "item": None, "option": None,
            "dest": list(dest) if dest else None, "origin": None, "object_id": None,
            "object_info": ""}


def item(name: str, option: str, dest) -> dict:
    """An inventory (or worn) teleport item: left-click verified against `option`, else the menu."""
    leg = _base("item", f"{name}: {option}", dest)
    leg["item"] = name
    leg["option"] = option
    return leg


def fairy(code: str, expect: str, dest=None) -> dict:
    """A fairy-ring hop, delegated to travel.hop_fairy_ring (dialled, never Last-destination)."""
    leg = _base("fairy", f"fairy ring {code}", dest)
    leg["code"] = code
    leg["expect"] = expect
    return leg


def walk(x: int, y: int, plane: int) -> dict:
    """A same-plane walk to an exact tile (travel.walk_to, goal_adjacent=False)."""
    return _base("walk", f"walk to {x},{y},{plane}", (x, y, plane))


def from_transport(t) -> dict:
    """A teleports.Transport, flattened -- banker's _leg_dict."""
    return {
        "kind": t.kind,
        "display": t.display,
        "item": t.item_name,
        "option": t.destination_name,
        "dest": list(t.dest),
        "origin": list(t.origin) if t.origin else None,
        "object_id": trailing_int(t.object_info),
        "object_info": t.object_info,
    }


def trailing_int(text):
    """`Teleport Menu Ornate Jewellery Box 37520` -> 37520.

    The column is `menuOption menuTarget objectID` with both names multi-word, so it cannot be
    split reliably -- but the object id is always the trailing integer, and an id is all we need
    to find the thing in /objects.
    """
    if not text:
        return None
    tail = text.strip().split()[-1]
    try:
        return int(tail)
    except ValueError:
        return None


# ---------------------------------------------------------------- running

def run_next(ctx, leg_list, cursor: int, log: str = "travel"):
    """Run the leg at `cursor`. Returns (status, new_cursor)."""
    if cursor >= len(leg_list or []):
        return FINISHED, cursor
    leg = leg_list[cursor]
    kind = leg.get("kind")
    if kind == "fairy":
        ok = travel.hop_fairy_ring(ctx, leg.get("code"), leg.get("expect"), log=log)
        return (DONE, cursor + 1) if ok else (PROGRESS, cursor)
    if kind == "walk":
        dest = leg.get("dest") or []
        ok = len(dest) == 3 and travel.walk_to(
            ctx, {"x": dest[0], "y": dest[1], "plane": dest[2]}, goal_adjacent=False)
        return (DONE, cursor + 1) if ok else (FAILED, cursor)
    ok = run_leg(ctx, leg, log=log)
    return (DONE, cursor + 1) if ok else (FAILED, cursor)


def await_arrival(dest, budget_s: float = ITEM_BUDGET_S) -> bool:
    """True once we are standing near `dest`. Polled, never slept off -- karambwan_fishing
    MEASURED that a teleport's own signals fire on the tick the LOADING screen starts, when the
    bridge answers nothing at all."""
    waited = 0.0
    while waited < budget_s:
        time.sleep(POLL_S)
        waited += POLL_S
        player = controller.get_player() or {}
        px, py = player.get("x"), player.get("y")
        if px is None:
            continue
        if (player.get("plane", 0) == dest[2]
                and abs(px - dest[0]) <= ARRIVAL_TILES
                and abs(py - dest[1]) <= ARRIVAL_TILES):
            return True
    return False


def run_leg(ctx, leg, log: str = "travel") -> bool:
    """Execute one item/box/portal hop. False means the chain stops and the caller decides."""
    dest = leg.get("dest") or []
    if len(dest) != 3:
        return False
    return (run_item_leg(ctx, leg, log) if leg.get("kind") == "item"
            else run_object_leg(ctx, leg, log)) and await_arrival(dest)


def run_item_leg(ctx, leg, log: str = "travel") -> bool:
    """An inventory teleport: LEFT-click verified through /hover, menu only as the fallback.

    The pattern falconry/karambwan already prove live (USER 2026-09-06: "for inventory teleports
    ive changed them so we just click the item and we teleport to the right spot").
    """
    name = (leg.get("item") or "").lower()
    it = next((i for i in (controller.get_inventory() or [])
               if name and name in (i.get("name") or "").lower()), None)
    if it is None:
        debug_log.log(log, f"no {leg.get('item')} in inventory -- leg '{leg.get('display')}' cannot run")
        return False

    option = leg.get("option")
    debug_log.log(log, f"leg: {leg.get('display')}")
    controller.open_inventory_tab(ctx.ser)
    ok = controller.click_entity(ctx.ser, it, check_ui_blockers=False,
                                 simulate_mistakes=False,
                                 expected_action=option if option else None)
    if not ok:
        if getattr(ok, "withheld", False):
            debug_log.log(log, f"{leg.get('item')}: left-click isn't the teleport here -- using the menu")
        ok = controller.click_menu_action(ctx.ser, it, option or "Teleport",
                                          check_ui_blockers=False)
    return bool(ok)


def run_object_leg(ctx, leg, log: str = "travel") -> bool:
    """A world-object hop: a POH jewellery box or portal.

    NOT YET VERIFIED LIVE -- it has never run inside a house. Every step confirms, and any
    failure returns False so the caller falls back rather than clicking blindly.
    """
    obj_id = leg.get("object_id")
    origin = leg.get("origin")
    if obj_id is None or not origin:
        return False

    # Walk to the object first: the game accepts a click on anything visible and then silently
    # does nothing when it cannot path there (CLAUDE.md's standing invariant).
    if not travel.walk_to(ctx, {"x": origin[0], "y": origin[1], "plane": origin[2]},
                          goal_adjacent=True):
        debug_log.log(log, f"couldn't reach {leg.get('display')}'s object")
        return False

    obj = controller.get_object_at(origin[0], origin[1], origin[2], obj_id, radius=16)
    if obj is None:
        obj = next((o for o in (controller.get_objects(radius=16) or [])
                    if o.get("id") == obj_id), None)
    if obj is None:
        debug_log.log(log, f"object {obj_id} not found for {leg.get('display')}")
        return False

    debug_log.log(log, f"leg: {leg.get('display')} (object {obj_id})")
    if not controller.click_entity(
            ctx.ser, obj, simulate_mistakes=False,
            refresh_target=controller.object_refresh_target(origin[0], origin[1], origin[2],
                                                            obj_id, radius=16)):
        return False

    option = leg.get("option")
    if not option:
        return True
    return pick_destination(ctx, option, log)


def pick_destination(ctx, option, log: str = "travel") -> bool:
    """Answer a transport's destination picker by the TSV's own destination name.

    The box's `Display info` is "3: Ferox Enclave" -- the digit is the interface's own shortcut
    and the text is the entry. The widget carrying that text is clicked. UNVERIFIED live; this
    is the assumption the whole POH chain rests on.
    """
    deadline = time.time() + PICKER_BUDGET_S
    while time.time() < deadline:
        dump = controller.get_widgets_dump() or {}
        widgets = dump.get("widgets") if isinstance(dump, dict) else dump
        for w in (widgets or []):
            text = (w.get("text") or "").strip()
            if option.lower() in text.lower() and w.get("screenX") is not None:
                debug_log.log(log, f"picking '{text}' from the destination menu")
                return bool(controller.click_entity(ctx.ser, w, check_ui_blockers=False,
                                                    simulate_mistakes=False))
        time.sleep(POLL_S)

    debug_log.log(log, f"destination menu never offered '{option}'")
    return False
```

- [ ] **Step 4: Run the new tests**

Run: `python -m pytest tests/test_legs.py -q`
Expected: 7 passed.

- [ ] **Step 5: Make banker delegate**

In `ControllerAddon/banker.py`:
1. Add `import legs` to the imports at the top (alphabetically among the local imports).
2. Replace the constants at lines 1170-1180 (`RETURN_TELEPORT_BUDGET_S`, `RETURN_TELEPORT_POLL_S`, `RETURN_TELEPORT_ARRIVAL_TILES`, `PICKER_BUDGET_S` with their comments) with:
```python
# The leg runner moved to legs.py 2026-09-18; these names stay for this file's readers.
RETURN_TELEPORT_BUDGET_S = legs.ITEM_BUDGET_S
RETURN_TELEPORT_POLL_S = legs.POLL_S
RETURN_TELEPORT_ARRIVAL_TILES = legs.ARRIVAL_TILES
PICKER_BUDGET_S = legs.PICKER_BUDGET_S
```
3. Replace `_leg_dict` and `_trailing_int` (lines 1303-1330) with:
```python
_leg_dict = legs.from_transport
_trailing_int = legs.trailing_int
```
4. Replace `_await_arrival`, `_run_leg`, `_run_item_leg`, `_run_object_leg`, `_pick_destination` (lines 1422-1537, from `def _await_arrival(` through `_pick_destination`'s final `return False`) with:
```python
# Moved to legs.py (2026-09-18). Aliases so _use_return_teleport and the tests that monkeypatch
# banker._run_leg keep working; the "bank" log tag becomes legs' default "travel".
_await_arrival = legs.await_arrival
_run_leg = legs.run_leg
_run_item_leg = legs.run_item_leg
_run_object_leg = legs.run_object_leg
_pick_destination = legs.pick_destination
```
5. Leave `_use_return_teleport` untouched — it calls `_run_leg(ctx, leg)`, which now resolves to the alias (and a test's `monkeypatch.setattr(banker, "_run_leg", ...)` still replaces what it calls).

- [ ] **Step 6: Fix the one banker test that stubbed the sleep on the old module**

In `ControllerAddon/tests/test_banker.py` line 680, replace `monkeypatch.setattr(banker.time, "sleep", lambda s: None)` with:
```python
    import legs
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)   # _await_arrival lives in legs now
```

- [ ] **Step 7: Compile and run the suite**

Run from `ControllerAddon/`:
```powershell
python -m py_compile legs.py banker.py
python -m pytest tests/test_legs.py tests/test_banker.py -q
```
Expected: all green. Then `python -m pytest tests/ -q` — all green.

- [ ] **Step 8: Commit**

```powershell
git add ControllerAddon/legs.py ControllerAddon/tests/test_legs.py ControllerAddon/banker.py ControllerAddon/tests/test_banker.py
git commit -m "legs.py: shared transport-leg runner with builders and a cursor

Promoted from banker's return-teleport runner (aliases kept); adds
item/fairy/walk builders and run_next for the slayer task travel.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `gear.py` — worn/carried lookups and a confirmed equip

**Files:**
- Create: `ControllerAddon/gear.py`
- Create: `ControllerAddon/tests/test_gear.py`

**Interfaces:**
- Produces:
  - `gear.worn_names() -> set[str]` — lowercased names from `/equipment` (dict keyed by slot).
  - `gear.carried_names() -> set[str]` — lowercased names from `/inventory`.
  - `gear.is_worn(name: str, alternatives=()) -> bool` — substring match of `name` or any alternative against worn names.
  - `gear.has(name: str) -> bool` — worn or carried.
  - `gear.equip_from_inventory(ctx, name: str) -> bool` — click the first inventory item containing `name` with `expected_action=("Wear", "Wield", "Equip")`, then CONFIRM via `/equipment` within `EQUIP_BUDGET_S = 2.0`. False when the item is absent, the click is withheld, or the confirm never comes.
  - `gear.ensure_worn(ctx, required) -> list[str]` — `required` is an iterable of `(name, alternatives)` tuples; equips each missing one; returns the names still missing afterwards.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_gear.py`:
```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import gear


class _State:
    def __init__(self):
        self.data = {}


class _Ctx:
    def __init__(self):
        self.ser = None
        self.state = _State()
        self.events = None


def _equipment(**slots):
    """/equipment is a DICT KEYED BY SLOT whose values are item dicts (banker._item_rows)."""
    return {slot: {"id": 1, "name": name} for slot, name in slots.items()}


def test_worn_and_carried_names_are_lowercased_substring_friendly(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment", lambda: _equipment(head="Slayer helmet", ring="Slayer ring (7)"))
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Earmuffs", "slot": 3}, {"name": "Shark", "slot": 4}])
    assert gear.worn_names() == {"slayer helmet", "slayer ring (7)"}
    assert gear.carried_names() == {"earmuffs", "shark"}
    assert gear.is_worn("slayer ring")
    assert gear.is_worn("earmuffs", alternatives=("slayer helmet",)), "a slayer helmet satisfies earmuffs"
    assert not gear.is_worn("earmuffs")
    assert gear.has("earmuffs") and gear.has("slayer ring") and not gear.has("nose peg")


def test_equip_from_inventory_clicks_wear_and_confirms_on_equipment(monkeypatch):
    worn = {"gloves": None}
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append((entity["name"], kw.get("expected_action"), kw.get("simulate_mistakes")))
        worn["gloves"] = {"id": 2, "name": entity["name"]}
        return True
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Expeditious bracelet", "slot": 5, "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {k: v for k, v in worn.items() if v})
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(gear.time, "sleep", lambda s: None)

    assert gear.equip_from_inventory(_Ctx(), "expeditious bracelet") is True
    assert clicks == [("Expeditious bracelet", gear.EQUIP_ACTIONS, False)]


def test_equip_from_inventory_is_false_when_the_click_never_shows_on_equipment(monkeypatch):
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Earmuffs", "slot": 5, "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(gear.time, "sleep", lambda s: None)
    monkeypatch.setattr(gear.time, "time", iter(range(0, 100)).__next__)   # each read is +1s
    assert gear.equip_from_inventory(_Ctx(), "earmuffs") is False


def test_equip_from_inventory_is_false_when_the_item_is_not_carried(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert gear.equip_from_inventory(_Ctx(), "earmuffs") is False
    assert not clicked


def test_ensure_worn_equips_what_is_missing_and_reports_what_still_is(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment", lambda: _equipment(head="Slayer helmet"))
    attempted = []
    monkeypatch.setattr(gear, "equip_from_inventory", lambda ctx, name: attempted.append(name) or name == "rock hammer")
    missing = gear.ensure_worn(_Ctx(), [("earmuffs", ("slayer helmet",)), ("rock hammer", ()), ("mirror shield", ())])
    assert attempted == ["rock hammer", "mirror shield"], "earmuffs are satisfied by the helmet"
    assert missing == ["mirror shield"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gear.py -q`
Expected: FAIL at import, `ModuleNotFoundError: No module named 'gear'`.

- [ ] **Step 3: Create `gear.py`**

```python
# gear.py -- what is worn, what is carried, and putting a carried item on. Added 2026-09-18 for
# the slayer task loop (spec §7.5): a task's required gear (earmuffs, nose peg, rock hammer...)
# is withdrawn at the bank and must be WORN before leaving. Nothing in banker.py could equip.
#
# The click is the pattern combat_core.maintain_bracelet proves live: left-click the inventory
# item with the equip action VERIFIED through /hover (expected_action), no simulated misclick
# (a miss here eats a Shark), and the result CONFIRMED on /equipment -- never assumed from the
# click. /equipment is a dict keyed by slot name; /inventory is a list (banker._item_rows).

import time

import controller
import debug_log

EQUIP_ACTIONS = ("Wear", "Wield", "Equip")
EQUIP_BUDGET_S = 2.0     # click -> the item showing on /equipment (one tick plus the read)
POLL_S = 0.15


def worn_names() -> set:
    eq = controller.get_equipment() or {}
    return {(v.get("name") or "").lower() for v in eq.values() if isinstance(v, dict) and v.get("name")}


def carried_names() -> set:
    return {(i.get("name") or "").lower() for i in (controller.get_inventory() or []) if i.get("name")}


def is_worn(name: str, alternatives=()) -> bool:
    """Substring match, like every item check in this codebase (charge suffixes vary)."""
    worn = worn_names()
    wanted = (name.lower(), *(a.lower() for a in alternatives))
    return any(w in n for n in worn for w in wanted)


def has(name: str) -> bool:
    n = name.lower()
    return any(n in w for w in worn_names()) or any(n in c for c in carried_names())


def equip_from_inventory(ctx, name: str) -> bool:
    """Wear/wield the first carried item containing `name`; True only once /equipment shows it."""
    n = name.lower()
    it = next((i for i in (controller.get_inventory() or []) if n in (i.get("name") or "").lower()), None)
    if it is None:
        debug_log.log("gear", f"no '{name}' in the inventory to equip")
        return False
    controller.open_inventory_tab(ctx.ser)
    ok = controller.click_entity(ctx.ser, it, check_ui_blockers=False, simulate_mistakes=False,
                                 expected_action=EQUIP_ACTIONS)
    if not ok:
        debug_log.log("gear", f"equip click on '{it.get('name')}' did not go "
                              f"({'withheld: no equip action under the cursor' if getattr(ok, 'withheld', False) else 'aim failed'})")
        return False
    deadline = time.time() + EQUIP_BUDGET_S
    while time.time() < deadline:
        if is_worn(n):
            return True
        time.sleep(POLL_S)
    debug_log.log("gear", f"'{it.get('name')}' never showed on /equipment after the click")
    return False


def ensure_worn(ctx, required) -> list:
    """Equip every (name, alternatives) not already worn. Returns the names still missing."""
    missing = []
    for name, alternatives in required:
        if is_worn(name, alternatives):
            continue
        if not equip_from_inventory(ctx, name):
            missing.append(name)
    return missing
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_gear.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/gear.py ControllerAddon/tests/test_gear.py
git commit -m "gear.py: worn/carried lookups and a /equipment-confirmed equip

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: The "Slayer tasks" config section and its export

**Files:**
- Create: `src/main/java/com/example/controller/SlayerMasterChoice.java`
- Modify: `src/main/java/com/example/controller/ControllerConfig.java` (new section after `valeTotemsSection`, items after the Slayer section's items)
- Modify: `src/main/java/com/example/controller/ConfigExporter.java` (`exportSlayer`, after the `bracelets` block from Task 4)

**Interfaces:**
- Produces: `SlayerMasterChoice { AYA("Aya"), KURADAL("Kuradal"), KONAR("Konar quo Maten") }`; config methods `slayerFillerMaster() -> SlayerMasterChoice` (AYA), `slayerMilestoneMaster()` (KURADAL), `slayerMilestoneEvery() -> int` (10), `slayerSkipTasks() -> String` (""), `slayerMaxSkips() -> int` (3), `slayerPointsReserve() -> int` (60). `/slayer-config` gains:
```json
"tasks": {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10,
          "skipTasks": [], "maxSkips": 3, "pointsReserve": 60}
```

- [ ] **Step 1: Create the enum**

`src/main/java/com/example/controller/SlayerMasterChoice.java`:
```java
package com.example.controller;

/**
 * A slayer master the task routine may ask for an assignment (spec §10). The enum NAME is what
 * /slayer-config carries; the Python side maps it to the master's displayed name in the NPC
 * Contact picker and its dialogue. KONAR is here because the wiki names her as the best
 * milestone master; her location-locked tasks need a catalog the routine does not have yet, so
 * choosing her is a config change that becomes useful in a later sub-project, not a promise.
 */
public enum SlayerMasterChoice
{
	AYA("Aya"),
	KURADAL("Kuradal"),
	KONAR("Konar quo Maten");

	private final String label;

	SlayerMasterChoice(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
```

- [ ] **Step 2: Add the section and items to `ControllerConfig.java`**

After line 92 (`String valeTotemsSection = "valeTotems";`) add:
```java
	@ConfigSection(name = "Slayer tasks", description = "The Slayer (tasks) routine: which masters, when the milestone task is, what to skip", position = 19)
	String slayerTasksSection = "slayerTasks";
```
After the `slayerBraceletReequip` item (Task 4) add:
```java
	// ============================================================
	// Slayer tasks (routines/slayer.py -- spec docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md)
	// ============================================================

	@ConfigItem(
		keyName = "slayerFillerMaster",
		name = "Filler Master",
		description = "Asked for every task that is NOT a milestone. Aya's tasks give 0 points but count toward the streak.",
		section = slayerTasksSection,
		position = 0
	)
	default SlayerMasterChoice slayerFillerMaster()
	{
		return SlayerMasterChoice.AYA;
	}

	@ConfigItem(
		keyName = "slayerMilestoneMaster",
		name = "Milestone Master",
		description = "Asked for the task whose completion lands on a milestone (every 10th/50th/100th). Kuradal: 75/225/375 points.",
		section = slayerTasksSection,
		position = 1
	)
	default SlayerMasterChoice slayerMilestoneMaster()
	{
		return SlayerMasterChoice.KURADAL;
	}

	@ConfigItem(
		keyName = "slayerMilestoneEvery",
		name = "Milestone Every N Tasks",
		description = "The milestone master is used when the NEXT task number is a multiple of this. 10 = every 10th task; 50 = only every 50th.",
		section = slayerTasksSection,
		position = 2
	)
	@Range(min = 10, max = 50)
	default int slayerMilestoneEvery()
	{
		return 10;
	}

	@ConfigItem(
		keyName = "slayerSkipTasks",
		name = "Always Skip Tasks",
		description = "Comma-separated task names (as the game names them, e.g. 'Abyssal demons') to cancel for 30 points whenever assigned.",
		section = slayerTasksSection,
		position = 3
	)
	default String slayerSkipTasks()
	{
		return "";
	}

	@ConfigItem(
		keyName = "slayerMaxSkips",
		name = "Max Skips Per Slot",
		description = "Consecutive 30-point cancels allowed for one task slot before the routine stops and asks a human.",
		section = slayerTasksSection,
		position = 4
	)
	@Range(min = 0, max = 10)
	default int slayerMaxSkips()
	{
		return 3;
	}

	@ConfigItem(
		keyName = "slayerPointsReserve",
		name = "Points Reserve",
		description = "Never skip a task if it would leave fewer than this many slayer points.",
		section = slayerTasksSection,
		position = 5
	)
	@Range(min = 0, max = 5000)
	default int slayerPointsReserve()
	{
		return 60;
	}
```

- [ ] **Step 3: Export it**

In `ConfigExporter.exportSlayer`, after the `bracelets` block (Task 4) add:
```java
		// The Slayer (tasks) routine's own settings (spec §10). Enum NAMES on the wire, like
		// prayer.defensivePrayer above.
		Map<String, Object> tasks = new LinkedHashMap<>();
		tasks.put("fillerMaster", config.slayerFillerMaster().name());
		tasks.put("milestoneMaster", config.slayerMilestoneMaster().name());
		tasks.put("milestoneEvery", config.slayerMilestoneEvery());
		tasks.put("skipTasks", parseList(config.slayerSkipTasks()));
		tasks.put("maxSkips", config.slayerMaxSkips());
		tasks.put("pointsReserve", config.slayerPointsReserve());
		data.put("tasks", tasks);
```

- [ ] **Step 4: Compile and run the Java tests**

Run from the repo root: `./gradlew.bat test --offline -q`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```powershell
git add src/main/java/com/example/controller/SlayerMasterChoice.java src/main/java/com/example/controller/ControllerConfig.java src/main/java/com/example/controller/ConfigExporter.java
git commit -m "Controller Settings: 'Slayer tasks' section, exported as /slayer-config.tasks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Documentation, full verification, and the sub-project hand-off note

**Files:**
- Modify: `CLAUDE.md` (routine status index table, ~line 639; the `routines/*.py` line in the architecture diagram, line 24)
- Modify: `docs/routine-notes.md:2712` (section heading)
- Modify: `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` (Status line)

- [ ] **Step 1: Update CLAUDE.md**

In the routine status index table replace the row
`| `slayer.py` | Bank-trip return rebuilt 2026-09-07, pinned in tests, NOT run live |`
with:
```
| `combat.py` (`combat_core.py`) | The old "Slayer / Combat": bank-trip return rebuilt 2026-09-07, pinned in tests, NOT run live; split out 2026-09-18 |
| `slayer.py` | STUB (safety-stops, points at the 2026-09-18 spec) until the task orchestrator ships; `GET /slayer`, `/varp`, `dialogue.py`, `legs.py`, `gear.py` are built |
```
In the architecture diagram line 24, change `(slayer, mining, golem_crafting, ...)` to `(combat, slayer, mining, golem_crafting, ...)`.

Add to the "Known environment gotchas" list, after the `/bank/memory` bullet under Bank memory or at the end of the gotchas list:
```
- **`GET /slayer` (2026-09-18) is the ONLY reader of slayer task state.** varps 394 (kills
  remaining) / 395 (task id) / 4258 / 2096 and varbits 4067 (master) / 4068 (points) / 4069
  (streak) / 4723, decoded through DB tables 113/115/116 exactly as RuneLite's own
  `SlayerPlugin.updateTask()` does. Completion is `count` going to 0 -- the plugin no longer
  parses chat for it, and neither do we. `taskName` is `null` (not `""`) when the DB lookup
  failed, so "no task" and "could not decode" stay distinguishable. An un-rebuilt client 404s
  it (`/slayer-config` is not a path prefix of `/slayer`), and `controller.get_slayer_task()`
  returns `None` -- a routine must do NOTHING on `None`. `GET /varp?id=` is the varp twin of
  `/varbit`, same null-means-unreadable rule.
```

- [ ] **Step 2: Update routine-notes.md**

Change the heading at line 2712 from `## Current state of routines/slayer.py` to `## Current state of combat_core.py / routines/combat.py (was routines/slayer.py until 2026-09-18)` and add one paragraph under it:
```
**Split 2026-09-18** (docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md): the file
is now `combat_core.py` (git mv), `tick` is `fight_tick`, and `routines/combat.py` is the
panel-config wrapper the "Combat" dropdown entry runs. Everything below still applies to it
unchanged. Two latent bugs fixed in the move: `BANK_NO_PROGRESS_MAX` was referenced but never
defined (NameError after two no-restock bank trips), and `bracelets.reequip` was read but
never exported. `routines/slayer.py` is a stub until sub-project 2.
```

- [ ] **Step 3: Update the spec's status line**

In the spec, change `**Status:** DRAFT for user review. Nothing implemented.` to `**Status:** approved 2026-09-18; sub-project 1 (split + plumbing) implemented per docs/superpowers/plans/2026-09-18-slayer-split-and-plumbing.md; sub-projects 2-3 pending the §12 recording.`

- [ ] **Step 4: Full verification**

Run from `ControllerAddon/`:
```powershell
python -m py_compile combat_core.py routines/combat.py routines/slayer.py controller.py banker.py dialogue.py legs.py gear.py routines/golem_crafting.py routines/giants_foundry.py
python -m pytest tests/ -q
```
Expected: all green, no warnings about unknown markers.
Run from the repo root: `./gradlew.bat test --offline` — expected BUILD SUCCESSFUL.
Run `git status` — expected: only the three doc files modified (everything else committed).

- [ ] **Step 5: Commit**

```powershell
git add CLAUDE.md docs/routine-notes.md docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md
git commit -m "docs: record the combat/slayer split and the /slayer endpoint

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: Hand-off**

The next step is NOT more code: it is the spec's §12.1-12.3 recording session against the live client (rebuild with `./gradlew.bat run` so `/slayer`, `/varp`, the `Combat`/`Slayer (tasks)` choices and the "Slayer tasks" section exist). Record `recorder.py contact-aya`, `contact-kuradal`, `rewards-cancel`, and probe `GET /slayer` during a task. Those measurements are the inputs to the sub-project 2 plan (NPC Contact + acquire/skip orchestrator).

---

## Self-review against the spec

- **§5 split**: Tasks 1-4 (move, `fight_tick`, `BANK_NO_PROGRESS_MAX`, `bracelets.reequip`, `combat_config`, `COMBAT`/`SLAYER` choices, tick-order test preserved by moving the file).
- **§10 bridge**: Task 5 (`/varp` + `get_varp`), 6-7 (`SlayerTaskState`, `SlayerProvider`, `get_slayer_task`), 11 (config section + `tasks` export). `M8aqProviderTest.knownPathsAreStable` covers `/api/*` paths only, so `/slayer` is pinned by `SlayerTaskStateTest` + the Python wrapper test instead — noted deviation from §11's wording.
- **§8 legs**: Task 9 — promotion + `item`/`fairy`/`walk` builders + `run_next`. The `object(name, action, at, lands)` leg and the worn-item path of `item` are explicitly sub-project 3 (§13).
- **§9 dialogue helpers**: Task 8. NPC Contact itself is sub-project 2.
- **§4 gear.py**: Task 10.
- **§11 tests**: `test_combat.py` (Tasks 1-3), `test_dialogue.py` (8), `test_legs.py` (9), `test_gear.py` (10), `SlayerTaskStateTest` (6), `get_varp`/`get_slayer_task` in `test_controller.py` (5, 7).
- **Type consistency**: `legs.run_next(ctx, leg_list, cursor, log)` returns `(status, cursor)` everywhere; `dialogue.choose(ctx, needle, budget_s, log)`; `gear.ensure_worn(ctx, required)` takes `(name, alternatives)` tuples, matching the spec's `Gear.worn(name, alternatives=...)` shape that sub-project 3 will build on.
