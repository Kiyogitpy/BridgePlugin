# Golem Crafting routine implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new `routines/golem_crafting.py` automation (mine sunstone -> chisel cores -> craft golems -> bank, with an automatic Goat Pit hunting fallback for fur), wired into the Controller plugin's settings screen and routine dropdown exactly like every other routine in this codebase.

**Architecture:** A `ControllerAddon/routines/golem_crafting.py` phase-state-machine routine (`ctx.state.data["gc_phase"]`, one phase function per tick-dispatch: `mine -> chisel -> craft -> bank -> (mine | hunt)`), two new shared banking helpers in `ControllerAddon/banker.py` for the fur pouch (mirroring the existing coal-bag helpers exactly), and the usual four-file RuneLite wiring (`RoutineChoice.java`, `ControllerConfig.java`, `ConfigExporter.java`, `ControllerPlugin.java`) plus `controller.py`'s `ROUTINE_FILES`/`get_golem_crafting_config`.

**Tech Stack:** Python 3 (`ControllerAddon/`, no unit-test framework for routines -- see Global Constraints), Java 8 / RuneLite plugin API (Gradle), no new dependencies.

## Global Constraints

- **Source spec:** `docs/superpowers/specs/2026-08-07-golem-crafting-design.md` -- every task below implements one numbered section of it. Re-read it if a task's rationale is unclear; this plan does not repeat its "why" material.
- **No unit-test framework exists for routines in this codebase.** Verification is live, against the running bridge (`AGENTS.md`). Every Python task's "test" step below is therefore a syntax/import sanity check (`py_compile` or a full module-exec smoke import), NOT a behavioral test -- real behavioral verification is the dedicated live-verification tasks at the end of this plan (mirroring the spec's own staged Testing section). Do not invent a pytest suite for `banker.py` or `routines/golem_crafting.py` -- no other routine or the existing coal-bag helpers have one, and inventing one here would be an unrequested, unprecedented pattern for this corner of the codebase.
- **Every object/menu-option name and click-sequence assumption below is unverified against the live game** (this activity is 9 days old at spec time, per the spec's Research method section). Each one gets an inline `# ASSUMPTION, NOT YET VERIFIED LIVE` comment, exactly like `blast_furnace.py`'s and `banker.py`'s existing coal-bag comments -- do not remove or soften these comments; they are load-bearing documentation, not TODOs.
- Deliberately **out of scope** (do not implement): the XP-drop tick-timing speed-up trick (always use the safe, un-optimized 4-side carve cadence); acquiring the fur pouch itself (assume the player already owns one); any fur source other than the bank or the Goat Pit; quest completion, Crafting leveling, or other one-time account setup.
- Follow this codebase's established local-helper convention: every routine keeps its own `find_item`/`find_object` (or equivalent) rather than sharing one generic implementation across routines -- see `blast_furnace.py`'s `find_object` docstring for why.
- Match existing code style exactly: tabs in `.java` files (RuneLite/Checkstyle convention already in every file you'll touch), 4-space indents in `.py` files, Allman braces in Java, docstrings/comments in the same voice as the files you're extending (explain *why*, cite the spec section, flag assumptions).

---

## File Structure

- **Modify `ControllerAddon/banker.py`** -- add `FUR_POUCH_NAME`, `has_fur_pouch`, `fill_fur_pouch`, `empty_fur_pouch`, placed directly after the existing `empty_coal_bag` function (same file, shared banking infra, mirrors the coal-bag block exactly).
- **Create `ControllerAddon/routines/golem_crafting.py`** -- the new routine. One file, following every other routine's shape (module docstring with mechanics/assumptions, `DEFAULT_CONFIG`, `get_config`, `check_level_requirement`, one function per phase, local `find_item`/`find_object`, `tick`).
- **Modify `ControllerAddon/controller.py`** -- add `get_golem_crafting_config()` next to `get_blast_furnace_config()`, and a `"GOLEM_CRAFTING": "routines/golem_crafting.py"` entry in `ROUTINE_FILES`.
- **Modify `src/main/java/com/example/controller/RoutineChoice.java`** -- add the `GOLEM_CRAFTING("Golem Crafting")` enum constant.
- **Modify `src/main/java/com/example/controller/ControllerConfig.java`** -- add a new `golemCraftingSection` (`@ConfigSection`, position 13, after Blast Furnace) with `golemCraftingEnabled` and `golemCraftingBankTeleportFallback` fields. Reuses the existing shared Breaks/Safety sections -- no new fields needed there.
- **Modify `src/main/java/com/example/controller/ConfigExporter.java`** -- add `exportGolemCrafting(ControllerConfig config)`, reusing the existing `sharedBreaks`/`sharedSafety` private helpers.
- **Modify `src/main/java/com/example/controller/ControllerPlugin.java`** -- register `server.register("/golem-crafting-config", () -> ConfigExporter.exportGolemCrafting(config));` next to the other routine-config registrations.

---

### Task 1: `banker.py` fur pouch helpers

**Files:**
- Modify: `ControllerAddon/banker.py` (insert after `empty_coal_bag`, currently ending at line 216)

**Interfaces:**
- Produces: `banker.FUR_POUCH_NAME: str`, `banker.has_fur_pouch() -> bool`, `banker.fill_fur_pouch(ctx) -> bool`, `banker.empty_fur_pouch(ctx) -> bool` -- all consumed by `routines/golem_crafting.py`'s `bank_phase`/`craft_phase`/`hunt_phase` in later tasks, with call signatures identical in shape to `has_coal_bag`/`fill_coal_bag`/`empty_coal_bag` immediately above them in the file.

- [ ] **Step 1: Add the fur pouch helpers**

Insert this block immediately after `empty_coal_bag` (after the line `return True` that ends it, before `def _click_bank_button`):

```python
FUR_POUCH_NAME = "fur pouch"  # substring match -- covers small/medium/large uniformly, same
                               # idiom as COAL_BAG_NAME above (e.g. "Large fur pouch" still
                               # matches). See docs/superpowers/specs/2026-08-07-golem-crafting-
                               # design.md section 1.


def has_fur_pouch() -> bool:
    return any(FUR_POUCH_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def fill_fur_pouch(ctx) -> bool:
    """Click the fur pouch at an open bank to fill it with hunter fur. ASSUMPTION, NOT YET
    VERIFIED LIVE -- which action a plain left-click resolves to here, same uncertainty
    fill_coal_bag documents above: the OSRS Wiki notes a bank-side click on a non-empty
    container can resolve to "Empty" instead of "Fill". Verify live which action a plain click
    actually performs in each state; add the wrong one to Controller Settings -> Clicking ->
    Forced Actions if needed. check_ui_blockers=False since this is an inventory-item click,
    same reasoning as every other inventory click in this codebase."""
    pouch = next(
        (i for i in controller.get_inventory() if FUR_POUCH_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pouch is None:
        return False

    print("[bank] filling fur pouch")
    if not controller.click_entity(ctx.ser, pouch, check_ui_blockers=False):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True


def empty_fur_pouch(ctx) -> bool:
    """Click Empty on the fur pouch to dump its fur into an open bank (pre-craft: into
    inventory instead, if no bank is open -- see routines/golem_crafting.py's craft_phase,
    which calls this before carving a plinth so any fur caught while hunting is available to
    insert into a golem). Same situation as empty_coal_bag above: "Empty" is not always the
    item's default option, so add it to Controller Settings -> Clicking -> Forced Actions if a
    plain click doesn't already resolve to it. check_ui_blockers=False since this is an
    inventory-item click, used both at a bank and out in the open (at the plinth), where the
    inventory panel itself is a live UI-blocker rect in resizable layouts."""
    pouch = next(
        (i for i in controller.get_inventory() if FUR_POUCH_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pouch is None:
        return False

    print("[bank] emptying fur pouch")
    if not controller.click_entity(ctx.ser, pouch, check_ui_blockers=False):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True
```

- [ ] **Step 2: Syntax/import sanity check**

Run (from the repo root):

```
cd ControllerAddon && python -c "import banker; print('ok', banker.FUR_POUCH_NAME, banker.has_fur_pouch, banker.fill_fur_pouch, banker.empty_fur_pouch)"
```

Expected: prints `ok fur pouch <function ...> <function ...> <function ...>` with no traceback. This only proves the module imports and the three names resolve -- see Global Constraints for why there's no behavioral test here.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/banker.py
git commit -m "Add fur pouch banking helpers, mirroring the coal bag helpers"
```

---

### Task 2: `routines/golem_crafting.py` scaffolding

**Files:**
- Create: `ControllerAddon/routines/golem_crafting.py`

**Interfaces:**
- Consumes: `banker.has_fur_pouch/fill_fur_pouch/empty_fur_pouch` (Task 1), `controller.get_skills()`, `controller.get_inventory()`, `controller.get_objects()`, `controller.INVENTORY_SIZE` (confirmed present and used the same way by `routines/shipwreck_salvaging.py`).
- Produces: module constants `FUR_PRIORITY: list[str]`, `MAX_OBJECT_DISTANCE: int`, `CRAFTING_LEVEL_REQUIRED: int`; `DEFAULT_CONFIG: dict`; `get_config(bridge_config) -> dict`; `check_level_requirement(ctx, config) -> None`; `find_item(name: str) -> dict | None`; `find_object(name: str) -> dict | None`. Every later task (3-8) in this file calls these.

- [ ] **Step 1: Write the module header, constants, config, level check, and local lookup helpers**

Create `ControllerAddon/routines/golem_crafting.py`:

```python
# routines/golem_crafting.py
#
# Golem Crafting routine: mine sunstone -> chisel it into cores -> carve an empty plinth and
# insert a core + hunter fur to finish a golem (Crafting XP + an uncut gem) -> bank, with an
# automatic fallback to hunting goats at the Goat Pit when the bank has no usable fur left.
#
# Full design and research notes: docs/superpowers/specs/2026-08-07-golem-crafting-design.md.
# Golem Crafting is a member-only Crafting method on Wyrmscraig, released 2026-07-29 -- 9 days
# before that design doc. The bundled OSRS Wiki mirror (osrs-wiki-data-master/) still tags
# Wyrmscraig.txt and Fallen From Grace.txt {{Future Content}} with no mechanical detail, so
# EVERY object name, menu-option string, and click sequence below comes from a live wiki fetch,
# not the local mirror, and has NOT been cross-checked against the running game the way this
# codebase's other routines verify object/menu names before relying on them. Each such
# assumption below carries its own "ASSUMPTION, NOT YET VERIFIED LIVE" comment, same convention
# as blast_furnace.py's -- correct these during the staged live verification this design's
# Testing section lays out before trusting this routine unattended.
#
# Requirements assumed already met (out of scope for this routine, see the design's Scope):
# Crafting 60, "Fallen From Grace" quest complete, already own a fur pouch (any size).
#
# Deliberately NOT implemented: the XP-drop tick-timing speed-up trick for plinth carving
# (always uses the safe, un-optimized ~15-ticks-per-side cadence); acquiring a fur pouch;
# sourcing fur from anywhere but the bank or the Goat Pit.
#
# Run with:
#     python controller.py routines/golem_crafting.py

import random
import time

import banker
import controller
import debug_log

# Highest fur-quality first (routines/golem_crafting.py-design.md's "Fur table"). A pure module
# constant, not read from bridge config -- ConfigExporter.exportGolemCrafting deliberately
# doesn't export this list (the player never chooses a fur, same reasoning
# BlastFurnaceBarType's BAR_TYPES table isn't Java-exposed either, see the design's section 3).
# Keeping this a constant that every phase reads directly (rather than config.get("furPriority"))
# means behavior is identical whether get_config() below returns the live bridge config (which
# has no "furPriority" key at all) or DEFAULT_CONFIG.
FUR_PRIORITY = [
    "Moonlight antelope fur", "Sunlight antelope fur", "Dashing kebbit fur",
    "Kyatt fur", "Dark kebbit fur", "Fox fur", "Spotted kebbit fur",
    "Graahk fur", "Larupia fur", "Feldip weasel fur", "Common kebbit fur",
    "Desert devil fur", "Polar kebbit fur", "Wyrmscraig goat fur",
]

# Same reasoning as FUR_PRIORITY above -- ConfigExporter.exportGolemCrafting doesn't export
# maxObjectDistance either, so every phase's find_object() call uses this constant directly
# rather than config.get("maxObjectDistance", ...), which would silently fall back to this same
# value anyway whenever the live bridge config is in effect.
MAX_OBJECT_DISTANCE = 15

CRAFTING_LEVEL_REQUIRED = 60

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact object names for the 22 rock spots + 1 monolith
# (design section "Key facts", "Core loop").
ROCK_NAMES = ("sunstone rocks", "sunstone monolith")

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact inventory item names for the raw ore, the finished
# core, and the tool used to turn one into the other.
RAW_SUNSTONE_NAME = "Sunstone"
SUNSTONE_CORE_NAME = "Sunstone core"
CHISEL_NAME = "Chisel"

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact object name for the crafting station. Assumed to
# stay findable by this same substring both before and after carving starts (see craft_phase's
# docstring in a later task for how this assumption is used).
PLINTH_NAME = "plinth"

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact item/object names for the Goat Pit hunting loop.
WOODEN_SPIKES_NAME = "Wooden spikes"
GOAT_PIT_NAME = "Goat pit"
GOAT_NAME = "goat"

# Used only if a Controller plugin build predating this routine's wiring is running (so
# GET /golem-crafting-config 404s and get_golem_crafting_config() returns None) -- keeps this
# routine runnable standalone, same as every other routine's DEFAULT_CONFIG fallback.
DEFAULT_CONFIG = {
    "enabled": True,
    # Kept here for documentation/standalone completeness -- see the FUR_PRIORITY/
    # MAX_OBJECT_DISTANCE constants above for why the actual phase code doesn't read these two
    # keys out of config.
    "furPriority": FUR_PRIORITY,
    "maxObjectDistance": MAX_OBJECT_DISTANCE,

    "bank": {"teleportFallback": False},

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
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_golem_crafting_config()
    return config if config is not None else DEFAULT_CONFIG


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check, run from inside tick() (never setup()
    -- controller.py calls routine.setup(ctx) at routine-load time, BEFORE the
    login.is_fully_logged_in() gate, so /skills could be queried pre-login and false-trigger a
    permanent safety stop on an account that's actually fine once logged in). Gated by a
    ctx.state.data flag so it runs exactly once per routine-activation -- state.data is reset to
    {} by controller.py whenever the active routine file changes. Same shape as
    blast_furnace.py's/firemaking.py's own check_level_requirement."""
    crafting = controller.get_skills().get("Crafting") or {}
    level = crafting.get("level")
    if level is not None and level < CRAFTING_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Crafting level {level} is below the {CRAFTING_LEVEL_REQUIRED} required for Golem Crafting"
        )


def find_item(name: str):
    """First inventory item whose name matches `name` exactly (case-insensitive) -- exact
    rather than substring match, same reasoning as blast_furnace.py's find_item: a substring
    match here would wrongly match "Sunstone" against "Sunstone core" (and vice versa isn't a
    risk since RAW_SUNSTONE_NAME is a prefix of SUNSTONE_CORE_NAME, but an exact match makes
    both lookups unambiguous either way)."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
        None,
    )


def find_object(name: str):
    """Closest visible object whose name contains `name`, within MAX_OBJECT_DISTANCE -- same
    filter shape as blast_furnace.py's find_object/mining.py's pick_rock/banker.py's
    find_nearby_bank, kept local per this codebase's established convention."""
    name_lower = name.lower()
    candidates = [
        o for o in controller.get_objects()
        if name_lower in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
```

- [ ] **Step 2: Syntax/import sanity check**

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('ok', m.DEFAULT_CONFIG['bank'], m.CRAFTING_LEVEL_REQUIRED, m.find_item, m.find_object)
"
```

Expected: prints `ok {'teleportFallback': False} 60 <function ...> <function ...>` with no traceback. This is the same load mechanism `controller.load_routine` uses (`importlib.util.spec_from_file_location` + `exec_module`), minus its `hasattr(module, "tick")` assertion -- `tick` doesn't exist yet (added in Task 8), which is expected and fine at this stage. `get_config`/`check_level_requirement` are not called here (they need a live bridge/`ctx`), only imported -- this step only proves the module's top-level code (imports, constant/dict literals, `def`s) executes without error.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Scaffold golem_crafting routine: config, level check, local lookup helpers"
```

---

### Task 3: `mine` phase

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (append after `find_object`)

**Interfaces:**
- Consumes: `controller.get_combat()`, `controller.get_inventory()`, `controller.INVENTORY_SIZE`, `controller.get_objects()`, `controller.click_entity`, `ctx.state.data`, `ctx.state.trigger_safety_stop`, `ROCK_NAMES`/`MAX_OBJECT_DISTANCE` (Task 2).
- Produces: `mine_phase(ctx, config) -> None`, consumed by `tick()`'s phase dispatch table in Task 8. On success, sets `ctx.state.data["gc_phase"] = "chisel"` once inventory is full; on failure, calls `ctx.state.trigger_safety_stop`.

- [ ] **Step 1: Add `find_rocks` and `mine_phase`**

Append to `ControllerAddon/routines/golem_crafting.py`:

```python
def find_rocks(objects: list) -> list:
    """Every visible sunstone rock/monolith within MAX_OBJECT_DISTANCE, closest first --
    plural (unlike find_object above) because mine_phase's momentum re-targeting (see below)
    needs the whole candidate list, not just the nearest one."""
    candidates = [
        o for o in objects
        if (o.get("name") or "").lower() in ROCK_NAMES
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates


def mine_phase(ctx, config: dict) -> None:
    """Momentum-click sunstone rocks/the monolith until the inventory is full, then hand off
    to chisel_phase. One swing (or one re-target) per call, gated on isIdle -- same shape as
    mining.py's tick(), not a single blocking loop, so the engine's between-tick safety/break/
    PvP checks still run at their normal cadence throughout a long mining stretch.

    Momentum mechanic (design section "Key facts", "Core loop"): switching rocks between
    swings raises the success chance and gives a 20% chance of bonus sunstone, so this
    deliberately targets a DIFFERENT rock each successful click instead of hammering the same
    one like mining.py does -- tracked via ctx.state.data["gc_last_rock_pos"] (an (x, y) world
    tile, not an object id: every "Sunstone rocks" instance shares the same object id, so id
    alone can't distinguish which specific rock was clicked last)."""
    if not controller.get_combat().get("isIdle", True):
        return  # still swinging

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        debug_log.log("golem crafting", "inventory full -- moving to chisel phase")
        ctx.state.data["gc_phase"] = "chisel"
        return

    rocks = find_rocks(controller.get_objects())
    if not rocks:
        ctx.state.trigger_safety_stop("no sunstone rocks or monolith found nearby -- wrong location?")
        return

    last_pos = ctx.state.data.get("gc_last_rock_pos")
    target = next((r for r in rocks if (r.get("x"), r.get("y")) != last_pos), rocks[0])

    debug_log.log("golem crafting", f"mining {target.get('name')} (dist={target.get('distance')})")
    time.sleep(random.uniform(0.15, 0.5))
    if controller.click_entity(ctx.ser, target):
        ctx.state.data["gc_last_rock_pos"] = (target.get("x"), target.get("y"))
```

- [ ] **Step 2: Syntax/import sanity check**

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('ok', m.find_rocks, m.mine_phase)
"
```

Expected: `ok <function ...> <function ...>`, no traceback.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Add golem_crafting mine phase with momentum re-targeting"
```

---

### Task 4: `chisel` phase

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (append after `mine_phase`)

**Interfaces:**
- Consumes: `controller.open_inventory_tab`, `controller.get_combat`, `controller.click_entity`, `find_item` (Task 2), `RAW_SUNSTONE_NAME`/`CHISEL_NAME` (Task 2).
- Produces: `chisel_phase(ctx, config) -> None`, consumed by `tick()` in Task 8. Sets `ctx.state.data["gc_phase"] = "craft"` once no raw sunstone remains.

- [ ] **Step 1: Add `chisel_phase`**

Append:

```python
def chisel_phase(ctx, config: dict) -> None:
    """For each raw sunstone in inventory, use the chisel on it to make a sunstone core --
    item-then-item two-click idiom, same as firemaking.py's tinderbox+log. One chisel action
    per call, gated on isIdle, so a low-Crafting-level failed attempt (if any -- design section
    "Key facts" doesn't document a failure chance for this step, unlike Firemaking's, but the
    isIdle gate costs nothing extra either way) just retries next call the same way
    firemaking.py's light loop does. -> craft once no raw sunstone is left."""
    if not controller.get_combat().get("isIdle", True):
        return

    controller.open_inventory_tab(ctx.ser)

    raw = find_item(RAW_SUNSTONE_NAME)
    if raw is None:
        debug_log.log("golem crafting", "no raw sunstone left -- moving to craft phase")
        ctx.state.data["gc_phase"] = "craft"
        return

    chisel = find_item(CHISEL_NAME)
    if chisel is None:
        ctx.state.trigger_safety_stop("no chisel in inventory")
        return

    debug_log.log("golem crafting", "chiselling sunstone into a core")
    time.sleep(random.uniform(0.15, 0.35))
    controller.click_entity(ctx.ser, chisel, check_ui_blockers=False)
    time.sleep(random.uniform(0.1, 0.25))
    controller.click_entity(ctx.ser, raw, check_ui_blockers=False)
    # Let the action register server-side before the next isIdle poll -- neither inventory
    # click makes isMoving true, so without this the very next poll can still read isIdle ==
    # True and fire a duplicate click pair (same reasoning as firemaking.py's own light loop).
    time.sleep(random.uniform(0.6, 1.0))
```

- [ ] **Step 2: Syntax/import sanity check**

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('ok', m.chisel_phase)
"
```

Expected: `ok <function ...>`, no traceback.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Add golem_crafting chisel phase"
```

---

### Task 5: `craft` phase

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (append after `chisel_phase`)

**Interfaces:**
- Consumes: `banker.has_fur_pouch`/`banker.empty_fur_pouch` (Task 1), `find_item`/`find_object` (Task 2), `FUR_PRIORITY`/`PLINTH_NAME`/`SUNSTONE_CORE_NAME` (Task 2), `controller.click_entity`.
- Produces: `pick_best_fur_in_inventory() -> str | None`, `craft_phase(ctx, config) -> None`, consumed by `tick()` in Task 8. Sets `ctx.state.data["gc_phase"] = "bank"` once out of cores or fur.

- [ ] **Step 1: Add `pick_best_fur_in_inventory` and `craft_phase`**

Append:

```python
def pick_best_fur_in_inventory() -> str:
    """Highest-priority (per FUR_PRIORITY) fur type actually present in inventory right now,
    or None. Exact-name match against each inventory item, same reasoning as find_item."""
    inv_names = {(i.get("name") or "").lower() for i in controller.get_inventory()}
    return next((f for f in FUR_PRIORITY if f.lower() in inv_names), None)


# ASSUMPTION, NOT YET VERIFIED LIVE -- ~15 game ticks (0.6s each) per plinth side (design
# section "Key facts": "~15 ticks/side, ~60 ticks total"). Randomized within a band around
# that, same jitter style every other routine's animation-wait sleeps use.
PLINTH_SIDE_WAIT_SECONDS = (8.5, 9.5)


def craft_phase(ctx, config: dict) -> None:
    """Craft one golem per call (empty the fur pouch first if this is the first call since
    entering this phase, carve all 4 sides of an empty plinth, then insert one sunstone core +
    the best available fur) -- sized like blast_furnace.py's run_trip (one bounded unit of work
    per call, not a single call looping over every golem this batch), so the engine's between-
    tick safety/break/PvP checks still run between golems on a long crafting stretch. tick()
    keeps re-entering this phase (see Task 8) until it sets gc_phase to "bank" itself, once out
    of cores or fur -- that repetition IS the "repeat until out of cores or fur" the design
    describes, just spread across multiple tick() calls rather than one internal loop.

    ASSUMPTION, NOT YET VERIFIED LIVE -- PLINTH_NAME ("plinth") is assumed to still substring-
    match find_object's lookup both before carving starts and after the golem is finished
    (i.e. the object's display name doesn't change to something without "plinth" in it, e.g.
    switching to "Golem"/"Finished golem" entirely). If live testing shows otherwise, add the
    post-carve name as a second candidate substring here.
    """
    if not ctx.state.data.get("gc_pouch_emptied_this_craft"):
        if banker.has_fur_pouch():
            banker.empty_fur_pouch(ctx)
        ctx.state.data["gc_pouch_emptied_this_craft"] = True

    core = find_item(SUNSTONE_CORE_NAME)
    if core is None:
        debug_log.log("golem crafting", "out of sunstone cores -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        ctx.state.data["gc_pouch_emptied_this_craft"] = False
        return

    fur_name = pick_best_fur_in_inventory()
    if fur_name is None:
        debug_log.log("golem crafting", "out of usable fur -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        ctx.state.data["gc_pouch_emptied_this_craft"] = False
        return

    plinth = find_object(PLINTH_NAME)
    if plinth is None:
        ctx.state.trigger_safety_stop("no empty plinth found nearby")
        return

    debug_log.log("golem crafting", f"carving plinth (dist={plinth.get('distance')})")
    # A real while-loop with its own success counter, NOT `for side in range(4)` with a
    # `continue` on a failed click -- CONFIRMED during task review (2026-08-07): `continue`
    # inside a `for` loop advances to the next value from range(4) regardless of whether the
    # click actually landed, so a single missed/blocked click would silently consume one of the
    # 4 iterations without a real carve, and the loop would still exit claiming 4 sides done.
    # Bounded (not an unconditional retry-forever) the same way blast_furnace.py's deposit_items
    # counts real successes against a requested count rather than assuming success.
    sides_carved = 0
    attempts = 0
    max_attempts = 8  # up to 4 retries total across all sides, on top of the 4 real carves
    while sides_carved < 4 and attempts < max_attempts:
        attempts += 1
        controller.open_inventory_tab(ctx.ser)  # no-op if already open; harmless before a world-object click
        if not controller.click_entity(ctx.ser, plinth):
            time.sleep(random.uniform(0.3, 0.6))
            continue  # missed/blocked click -- retry the same side (sides_carved unchanged)
        sides_carved += 1
        debug_log.log("golem crafting", f"carved side {sides_carved}/4")
        time.sleep(random.uniform(*PLINTH_SIDE_WAIT_SECONDS))

    if sides_carved < 4:
        debug_log.log("golem crafting", f"only carved {sides_carved}/4 sides -- plinth may have gone out of range; retrying next tick")
        return

    controller.open_inventory_tab(ctx.ser)
    core = find_item(SUNSTONE_CORE_NAME)
    fur = find_item(fur_name)
    if core is None or fur is None:
        # Carving consumed more time than expected and the fur pouch/inventory state moved out
        # from under us (e.g. a catch routed into the pouch mid-carve) -- bail this golem
        # rather than clicking with a stale/missing item; next call re-evaluates from scratch.
        debug_log.log("golem crafting", "core or fur went missing mid-carve -- retrying next tick")
        return

    debug_log.log("golem crafting", f"inserting core + {fur_name}")
    controller.click_entity(ctx.ser, core, check_ui_blockers=False)
    time.sleep(random.uniform(0.15, 0.3))
    controller.click_entity(ctx.ser, plinth)
    time.sleep(random.uniform(0.4, 0.7))

    fur = find_item(fur_name)
    if fur is None:
        debug_log.log("golem crafting", "fur went missing after inserting the core -- retrying next tick")
        return
    controller.click_entity(ctx.ser, fur, check_ui_blockers=False)
    time.sleep(random.uniform(0.15, 0.3))
    controller.click_entity(ctx.ser, plinth)
    time.sleep(random.uniform(0.6, 1.0))
```

- [ ] **Step 2: Syntax/import sanity check**

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('ok', m.pick_best_fur_in_inventory, m.craft_phase)
"
```

Expected: `ok <function ...> <function ...>`, no traceback.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Add golem_crafting craft phase"
```

---

### Task 6: `bank` phase

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (append after `craft_phase`)

**Interfaces:**
- Consumes: `banker.find_nearby_bank`, `banker.open_bank`, `banker.close_bank`, `banker.deposit_inventory`, `banker.withdraw_loadout`, `banker.has_fur_pouch`, `banker.fill_fur_pouch`, `banker.bank_up` (all Task 1 / existing `banker.py`); `controller.get_bank`; `FUR_PRIORITY`, `CHISEL_NAME`, `WOODEN_SPIKES_NAME`, `SUNSTONE_CORE_NAME`, `banker.FUR_POUCH_NAME` (Task 2 / Task 1).
- Produces: `bank_phase(ctx, config) -> None`, consumed by `tick()` in Task 8. Sets `ctx.state.data["gc_phase"]` to `"mine"` or `"hunt"`.

- [ ] **Step 1: Add `bank_phase`**

Append:

```python
# Never deposited during a golem-crafting bank trip -- the pouch/tools are needed for the next
# mine/chisel/craft cycle, and a leftover sunstone core (if fur ran out before cores did) needs
# to survive the trip so the next craft_phase call can still use it.
BANK_KEEP_ITEMS = [banker.FUR_POUCH_NAME, CHISEL_NAME.lower(), WOODEN_SPIKES_NAME.lower(), SUNSTONE_CORE_NAME.lower()]

# Withdraw budget when no fur pouch is carried -- "a fixed inventory-slot budget (e.g. 14, half
# an inventory)" per the design's bank-phase bullet. With a pouch, the budget is effectively the
# pouch's own capacity (small/medium/large all <= 28), so 28 (a full inventory) as the withdraw
# cap is safe either way -- withdraw_loadout only ever takes min(cap, what's actually in the
# bank) since it withdraws unit-by-unit off the bank's real stack.
FUR_WITHDRAW_BUDGET_WITH_POUCH = controller.INVENTORY_SIZE
FUR_WITHDRAW_BUDGET_NO_POUCH = 14


def find_available_fur_in_bank(bank: dict) -> str:
    """Highest-priority (per FUR_PRIORITY) fur type with qty > 0 in the open bank, or None."""
    bank_items = {(i.get("name") or "").lower(): i.get("qty", 0) for i in bank.get("items", [])}
    return next((f for f in FUR_PRIORITY if bank_items.get(f.lower(), 0) > 0), None)


def _run_bank_trip(ctx, config: dict) -> None:
    """The actual deposit/withdraw logic, run once a bank is confirmed open (by either branch
    of bank_phase below). Deposits everything except BANK_KEEP_ITEMS (uncut gems and any
    leftover raw sunstone per the design's bank-phase bullet -- anything not explicitly kept),
    then withdraws the best available fur (filling the pouch if one is carried) or falls
    through to the hunt phase if the bank has none of FUR_PRIORITY at all."""
    banker.deposit_inventory(ctx, {"keepItems": BANK_KEEP_ITEMS})

    bank = controller.get_bank()
    fur_name = find_available_fur_in_bank(bank)
    if fur_name is None:
        debug_log.log("golem crafting", "bank has none of the usable fur types -- moving to hunt phase")
        ctx.state.data["gc_phase"] = "hunt"
        return

    has_pouch = banker.has_fur_pouch()
    budget = FUR_WITHDRAW_BUDGET_WITH_POUCH if has_pouch else FUR_WITHDRAW_BUDGET_NO_POUCH
    slot = next((s for s in bank.get("items", []) if (s.get("name") or "").lower() == fur_name.lower()), None)
    qty = min(budget, slot.get("qty", 0)) if slot else 0

    if qty > 0:
        debug_log.log("golem crafting", f"withdrawing {qty}x {fur_name}")
        banker.withdraw_loadout(ctx, {"withdraw": [{"name": fur_name, "quantity": qty}]})

    if has_pouch:
        time.sleep(random.uniform(0.3, 0.5))  # let the just-withdrawn fur register in
                                               # get_inventory() before fill_fur_pouch looks for it
        banker.fill_fur_pouch(ctx)

    debug_log.log("golem crafting", "banking done -- moving to mine phase")
    ctx.state.data["gc_phase"] = "mine"


def bank_phase(ctx, config: dict) -> None:
    """Bank near Wyrmscraig if a bank is in walking distance; otherwise teleport to Ferox
    Enclave via the ring of dueling if Teleport If No Bank Nearby is enabled. Sized like
    blast_furnace.py's run_trip / banker.bank_up (one full bounded bank visit per call).

    Fur selection needs to inspect the ALREADY-OPEN bank's actual contents (find_available_
    fur_in_bank), which banker.bank_up's single deposit+withdraw+close call can't do (it needs
    the withdraw list decided up front). So the local-bank branch below opens the bank itself
    and calls _run_bank_trip directly, same manual pattern blast_furnace.py's run_trip uses
    instead of bank_up. The Ferox teleport-fallback branch, though, has to go through
    banker.bank_up (its private Ferox path isn't meant to be called directly) -- so it can only
    ever deposit everything and withdraw nothing dynamic, same acknowledged limitation
    blast_furnace.py's own module header documents for the coal bag ("the Ferox teleport
    fallback cannot fill the coal bag"). That's not a dead end here, though: Ferox has its own
    bank chest in walking distance, so the very next bank_phase call (gc_phase is still "bank")
    finds THAT bank via find_nearby_bank and takes the local-bank branch, which does the real
    fur scan/withdraw against the same (account-wide) bank contents.
    """
    bank_obj = banker.find_nearby_bank({})
    if bank_obj is not None:
        if not banker.open_bank(ctx, bank_obj):
            ctx.state.trigger_safety_stop("failed to open the bank near Wyrmscraig")
            return
        _run_bank_trip(ctx, config)
        banker.close_bank(ctx)
        time.sleep(random.uniform(0.3, 0.6))
        return

    if config.get("bank", {}).get("teleportFallback"):
        if not banker.bank_up(ctx, {"keepItems": BANK_KEEP_ITEMS, "teleportFallback": True}):
            ctx.state.trigger_safety_stop("couldn't reach a bank near Wyrmscraig, and the Ferox teleport fallback failed")
        # gc_phase deliberately stays "bank" either way -- see docstring above: a successful
        # Ferox teleport lands next to Ferox's own bank, and the next call's find_nearby_bank
        # above will find it and do the real fur withdrawal.
        return

    ctx.state.trigger_safety_stop("no bank found near Wyrmscraig, and Teleport If No Bank Nearby is off")
```

- [ ] **Step 2: Syntax/import sanity check**

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('ok', m.find_available_fur_in_bank, m.bank_phase, m.BANK_KEEP_ITEMS)
"
```

Expected: `ok <function ...> <function ...> ['fur pouch', 'chisel', 'wooden spikes', 'sunstone core']`, no traceback.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Add golem_crafting bank phase"
```

---

### Task 7: `hunt` phase

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (append after `bank_phase`)

**Interfaces:**
- Consumes: `banker.find_nearby_bank`, `banker.open_bank`, `banker.close_bank`, `banker.withdraw_loadout`, `banker.has_fur_pouch` (Task 1); `find_item` (Task 2); `WOODEN_SPIKES_NAME`, `GOAT_PIT_NAME`, `GOAT_NAME` (Task 2); `controller.get_npcs`, `controller.click_entity`.
- Produces: `hunt_phase(ctx, config) -> None`, consumed by `tick()` in Task 8. Only ever entered from `bank_phase` when the bank has no usable fur; sets `ctx.state.data["gc_phase"] = "bank"` once the pit is cleared.

- [ ] **Step 1: Add `hunt_phase`**

Append:

```python
def _ensure_wooden_spikes(ctx, config: dict) -> bool:
    """Withdraw wooden spikes from the bank if not already carried. Returns False (with a
    safety stop already triggered) if neither inventory nor bank has any -- a genuine dead end,
    same "hard stop, don't spin forever" convention banker.py's out_of_critical_supplies path
    uses."""
    if find_item(WOODEN_SPIKES_NAME) is not None:
        return True

    bank_obj = banker.find_nearby_bank({})
    if bank_obj is None:
        ctx.state.trigger_safety_stop("out of wooden spikes and no bank in range to restock")
        return False

    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank while restocking wooden spikes")
        return False

    banker.withdraw_loadout(ctx, {"withdraw": [{"name": WOODEN_SPIKES_NAME, "quantity": 1}]})
    banker.close_bank(ctx)
    time.sleep(random.uniform(0.3, 0.6))

    if find_item(WOODEN_SPIKES_NAME) is None:
        ctx.state.trigger_safety_stop("no wooden spikes in inventory or bank -- can't line the Goat Pit")
        return False
    return True


def hunt_phase(ctx, config: dict) -> None:
    """Entered only when bank_phase finds no usable fur at all. Line the Goat Pit with wooden
    spikes, open the fur pouch (if owned) so catches route straight in, then lure/harvest
    goats until none are left nearby (the pit needing to be "fully cleared of goats before it
    can be re-lured" per the design's Key facts -- approximated here as "no goat NPCs visible
    near the pit", since the bridge has no dedicated pit-state field to check against). One
    action (line the pit, lure, or harvest) per call, same isIdle-adjacent single-small-action
    shape as mine_phase/chisel_phase.

    ASSUMPTION, NOT YET VERIFIED LIVE -- this is the single most uncertain part of this whole
    routine (see the design doc's Research method: the Goat Pit's exact interaction states
    weren't deeply covered by the live wiki fetch this design was based on). In particular:
      - Whether a plain left-click on a wild goat resolves to "Lure" by default, and whether
        the SAME plain click on an already-trapped goat resolves to "Harvest" -- this function
        can't currently tell "needs luring" and "ready to harvest" apart from the bridge's NPC
        data alone, so it clicks whatever goat NPC is nearest and lets the game's own menu
        resolution sort out which action that click actually performs. Add "Lure"/"Harvest" to
        Controller Settings -> Clicking -> Forced Actions if a plain click doesn't resolve
        correctly in one or both states.
      - Whether "Open" is really the fur pouch's default action while not at a bank (as opposed
        to Fill/Empty, which is what a bank-side plain click is assumed to resolve to
        elsewhere in this file) -- same kind of state-dependent-default-action uncertainty
        banker.py's fill_coal_bag docstring already flags for the coal bag.
    Correct this function during the Goat Pit stage of this plan's live verification task
    before trusting the hunt phase unattended.
    """
    if not ctx.state.data.get("gc_pit_lined"):
        if not _ensure_wooden_spikes(ctx, config):
            return

        pit = find_object(GOAT_PIT_NAME)
        if pit is None:
            ctx.state.trigger_safety_stop("Goat Pit not found nearby")
            return

        spikes = find_item(WOODEN_SPIKES_NAME)
        debug_log.log("golem crafting", "lining the Goat Pit with wooden spikes")
        controller.click_entity(ctx.ser, spikes, check_ui_blockers=False)
        time.sleep(random.uniform(0.15, 0.3))
        controller.click_entity(ctx.ser, pit)
        time.sleep(random.uniform(0.6, 1.0))
        ctx.state.data["gc_pit_lined"] = True

        if banker.has_fur_pouch() and not ctx.state.data.get("gc_pouch_opened_this_hunt"):
            pouch = find_item(banker.FUR_POUCH_NAME.title()) or next(
                (i for i in controller.get_inventory() if banker.FUR_POUCH_NAME in (i.get("name") or "").lower()),
                None,
            )
            if pouch is not None:
                debug_log.log("golem crafting", "opening fur pouch so catches route straight in")
                controller.click_entity(ctx.ser, pouch, check_ui_blockers=False)
                time.sleep(random.uniform(0.3, 0.5))
            ctx.state.data["gc_pouch_opened_this_hunt"] = True
        return

    goats = [
        n for n in controller.get_npcs()
        if GOAT_NAME in (n.get("name") or "").lower()
        and n.get("visible", True)
        and 0 <= n.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not goats:
        debug_log.log("golem crafting", "Goat Pit cleared -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        ctx.state.data["gc_pit_lined"] = False
        ctx.state.data["gc_pouch_opened_this_hunt"] = False
        return

    goats.sort(key=lambda n: n.get("distance", 9999))
    target = goats[0]
    debug_log.log("golem crafting", f"interacting with {target.get('name')} (dist={target.get('distance')})")
    time.sleep(random.uniform(0.2, 0.5))
    controller.click_entity(ctx.ser, target)
    time.sleep(random.uniform(0.6, 1.0))
```

- [ ] **Step 2: Syntax/import sanity check**

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('ok', m._ensure_wooden_spikes, m.hunt_phase)
"
```

Expected: `ok <function ...> <function ...>`, no traceback.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Add golem_crafting hunt phase (Goat Pit fallback)"
```

---

### Task 8: phase dispatcher (`tick`)

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (append after `hunt_phase` -- this is the final piece of the file)

**Interfaces:**
- Consumes: `check_level_requirement`, `mine_phase`, `chisel_phase`, `craft_phase`, `bank_phase`, `hunt_phase` (Tasks 2-7).
- Produces: `tick(ctx) -> None` -- the required entry point `controller.load_routine` checks for via `hasattr(module, "tick")`. This is what makes the file a runnable routine for the first time.

- [ ] **Step 1: Add the phase dispatch table and `tick`**

Append:

```python
PHASES = {
    "mine": mine_phase,
    "chisel": chisel_phase,
    "craft": craft_phase,
    "bank": bank_phase,
    "hunt": hunt_phase,
}


def tick(ctx):
    config = ctx.state.config

    if not ctx.state.data.get("gc_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["gc_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    phase = ctx.state.data.get("gc_phase", "mine")
    handler = PHASES.get(phase)
    if handler is None:
        ctx.state.trigger_safety_stop(f"unknown golem crafting phase '{phase}'")
        return

    handler(ctx, config)
```

- [ ] **Step 2: Full-module smoke check**

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert hasattr(m, 'tick'), 'tick missing'
assert set(m.PHASES) == {'mine', 'chisel', 'craft', 'bank', 'hunt'}
print('ok', sorted(m.PHASES))
"
```

Expected: `ok ['bank', 'chisel', 'craft', 'hunt', 'mine']`, no traceback. This is the first point in the plan where the file satisfies `controller.load_routine`'s full contract (`hasattr(module, "tick")`) -- from here on, the routine could in principle be pinned and run (`python controller.py routines/golem_crafting.py`) against a real bridge, though object/menu names are still unverified until Task 10.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Wire golem_crafting phase dispatcher (tick)"
```

---

### Task 9: Controller/RuneLite wiring

**Files:**
- Modify: `ControllerAddon/controller.py:220` area (next to `get_blast_furnace_config`) and `:1240` area (`ROUTINE_FILES`)
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `src/main/java/com/example/controller/ControllerConfig.java`
- Modify: `src/main/java/com/example/controller/ConfigExporter.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:124` area

**Interfaces:**
- Consumes: `routines/golem_crafting.py`'s `get_config` (Task 2), which calls `controller.get_golem_crafting_config()` -- this task is what makes that call resolve instead of `AttributeError`.
- Produces: an end-to-end path from the RuneLite "Routine" dropdown through to the Python routine, matching every other routine's wiring exactly.

- [ ] **Step 1: `controller.py` -- add `get_golem_crafting_config` and the `ROUTINE_FILES` entry**

In `ControllerAddon/controller.py`, add this function immediately after `get_blast_furnace_config` (currently ending at line 221, right before `get_minimap_walk_point`):

```python
def get_golem_crafting_config():
    return api_get("/golem-crafting-config")
```

Then in `ROUTINE_FILES` (currently lines 1240-1248), add the new entry after `"BLAST_FURNACE"`:

```python
ROUTINE_FILES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
    "GEMSTONE_CRAB": "routines/gemstone_crab.py",
    "TORMENTED_DEMON": "routines/tormented_demon.py",
    "SHIPWRECK_SALVAGING": "routines/shipwreck_salvaging.py",
    "FIREMAKING": "routines/firemaking.py",
    "BLAST_FURNACE": "routines/blast_furnace.py",
    "GOLEM_CRAFTING": "routines/golem_crafting.py",
}
```

- [ ] **Step 2: `RoutineChoice.java` -- add the enum constant**

In `src/main/java/com/example/controller/RoutineChoice.java`, change:

```java
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon"),
	SHIPWRECK_SALVAGING("Shipwreck Salvaging"),
	FIREMAKING("Firemaking"),
	BLAST_FURNACE("Blast Furnace");
```

to:

```java
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon"),
	SHIPWRECK_SALVAGING("Shipwreck Salvaging"),
	FIREMAKING("Firemaking"),
	BLAST_FURNACE("Blast Furnace"),
	GOLEM_CRAFTING("Golem Crafting");
```

- [ ] **Step 3: `ControllerConfig.java` -- add the section and its two fields**

Add a new `@ConfigSection` after the existing Blast Furnace one (currently lines 73-74):

```java
	@ConfigSection(name = "Blast Furnace", description = "Blast Furnace routine settings", position = 12)
	String blastFurnaceSection = "blastFurnace";

	@ConfigSection(name = "Golem Crafting", description = "Golem Crafting routine settings", position = 13)
	String golemCraftingSection = "golemCrafting";
```

Then append these two `@ConfigItem`s at the very end of the interface, after the existing `blastFurnaceBankTeleportFallback` (currently the last member, ending the file at line 1085):

```java
	// ============================================================
	// Golem Crafting
	// ============================================================

	@ConfigItem(
		keyName = "golemCraftingEnabled",
		name = "Golem Crafting Enabled",
		description = "Secondary switch on top of launching controller.py with routines/golem_crafting.py.",
		section = golemCraftingSection,
		position = 0
	)
	default boolean golemCraftingEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "golemCraftingBankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the ring of dueling to Ferox Enclave when no bank is within walking distance of Wyrmscraig.",
		section = golemCraftingSection,
		position = 1
	)
	default boolean golemCraftingBankTeleportFallback()
	{
		return false;
	}
```

- [ ] **Step 4: `ConfigExporter.java` -- add `exportGolemCrafting`**

Add this method right after `exportBlastFurnace` (currently ending at line 210, right before the `sharedBreaks` helper):

```java
	public static Map<String, Object> exportGolemCrafting(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.golemCraftingEnabled());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("teleportFallback", config.golemCraftingBankTeleportFallback());
		data.put("bank", bank);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}
```

- [ ] **Step 5: `ControllerPlugin.java` -- register the endpoint**

In `src/main/java/com/example/controller/ControllerPlugin.java`, change:

```java
		server.register("/blast-furnace-config", () -> ConfigExporter.exportBlastFurnace(config));
```

to:

```java
		server.register("/blast-furnace-config", () -> ConfigExporter.exportBlastFurnace(config));
		server.register("/golem-crafting-config", () -> ConfigExporter.exportGolemCrafting(config));
```

- [ ] **Step 6: Compile checks**

Python (from repo root):

```
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/golem_crafting.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('ok')
"
python -c "import ast; ast.parse(open('ControllerAddon/controller.py').read())" 2>/dev/null || python -c "import ast; ast.parse(open('controller.py').read())"
```

(the second line just re-confirms `controller.py` itself still parses cleanly after the edit -- run it from whichever directory makes the relative path resolve, e.g. from the repo root use `ControllerAddon/controller.py`).

Java (from repo root):

```
./gradlew.bat compileJava
```

Expected: `BUILD SUCCESSFUL`. This is the real correctness check for this task -- it catches typos in the new `@ConfigItem`/enum/method wiring that a human read-through could miss (mismatched method names between `ControllerConfig.java` and `ConfigExporter.java` are exactly the kind of thing that fails loudly here and silently nowhere else).

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/controller.py src/main/java/com/example/controller/RoutineChoice.java src/main/java/com/example/controller/ControllerConfig.java src/main/java/com/example/controller/ConfigExporter.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Wire Golem Crafting into the Controller plugin's routine dropdown and settings screen"
```

---

### Task 10: Live verification

**Files:** none (no code changes -- this task executes the spec's own staged Testing plan against the running game/bridge, per `AGENTS.md`'s live-verification conventions). Fix forward into the files from Tasks 1-9 as issues are found; don't defer fixes to a later task.

This task requires the RuneLite client running locally with this plugin loaded (`./gradlew.bat run`), logged into an account that meets the routine's assumed prerequisites (Crafting 60, "Fallen From Grace" complete, owns a fur pouch), standing on Wyrmscraig. Use the bridge/MCP tools `AGENTS.md` documents (`bridge_get`, `right_click`/`GET /menu`, or the MCP debug server's equivalents) to confirm real object/item/menu names before/instead of guessing from this plan's `ASSUMPTION, NOT YET VERIFIED LIVE` placeholders.

- [ ] **Step 1: Confirm real names before running anything unattended**

Query `GET /objects` near the mining spots, the plinth, and the Goat Pit, and `GET /inventory` after picking up sunstone/chiselling/insertingfur, to confirm or correct every constant flagged `ASSUMPTION, NOT YET VERIFIED LIVE` in Tasks 2, 5, and 7: `ROCK_NAMES`, `RAW_SUNSTONE_NAME`, `SUNSTONE_CORE_NAME`, `CHISEL_NAME`, `PLINTH_NAME`, `WOODEN_SPIKES_NAME`, `GOAT_PIT_NAME`, `GOAT_NAME`. Right-click a sunstone rock, an empty plinth, and a wild goat (via `right_click`/`GET /menu`) to confirm what a plain left-click's default action actually is in each case, and whether the fur pouch's plain-click default differs at a bank vs. out in the open (`fill_fur_pouch`/`empty_fur_pouch`/the `hunt_phase` "Open" assumption in Task 7). Fix any mismatches directly in `routines/golem_crafting.py` before continuing.

- [ ] **Step 2: One full manual cycle, by hand**

Manually mine one sunstone, chisel it, carve a plinth's 4 sides, insert the core + a fur, and bank -- confirming exact menu-option names and that the plinth click behavior matches Task 5's assumption (same findable name before/after carving). This is the spec's Testing step 1.

- [ ] **Step 3: One full automated cycle**

Set the RuneLite plugin's Routine dropdown to Golem Crafting (or run `python controller.py routines/golem_crafting.py` directly), and watch one full `mine -> chisel -> craft -> bank` cycle via `debug_log`'s output, confirming phase transitions happen where expected and no safety stop fires unexpectedly. This is the spec's Testing step 2.

- [ ] **Step 4: Goat Pit fallback**

Deliberately deposit/remove every `FUR_PRIORITY` fur type from the bank, run the routine through a `bank -> hunt` transition, and confirm one full lure-harvest-clear cycle at the Goat Pit completes and returns to `bank`. Pay special attention to the `hunt_phase` assumptions flagged in Task 7 (lure vs. harvest default-click resolution, pit-cleared detection) -- this is the phase with the least wiki grounding in the spec, per its own Research method section. This is the spec's Testing step 3.

- [ ] **Step 5: Soak run**

Only once Steps 1-4 are clean, run the routine unattended for an extended stretch (this is the spec's Testing step 4) to surface anything that only shows up over many cycles (fur pouch capacity edge cases, momentum-mining rock exhaustion near a boundary, etc.).

- [ ] **Step 6: Final commit for any live-verification fixes**

If Steps 1-5 required code changes, commit them separately from the implementation tasks above, with a message describing what live testing corrected, e.g.:

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Correct golem_crafting object/menu-option names after live verification"
```

---

## Self-Review

**Spec coverage:**
- Section 1 (`banker.py` fur pouch helpers) -> Task 1.
- Section 2 (`routines/golem_crafting.py`, all 5 phases + config + level check) -> Tasks 2-8.
- Section 3 (wiring: `ROUTINE_FILES`, `RoutineChoice.java`, `ControllerConfig.java`, `ConfigExporter.java`) -> Task 9 (also added the `ControllerPlugin.java` registration the spec's section 3 doesn't spell out but its own Design section 2's `get_config` clearly needs, and which every other routine's wiring includes).
- Section 4 (error handling & safety: hard stops for missing rocks/plinth/bank/Goat Pit/spikes, ASSUMPTION comments) -> covered inline across Tasks 3, 5, 6, 7.
- Testing section (4 staged live-verification steps) -> Task 10, steps 1-5 in the same order.
- Scope's explicit exclusions (XP-drop timing trick, acquiring a pouch, non-bank/non-Goat-Pit fur sources, quest/leveling/setup) -> none implemented anywhere above; called out in Global Constraints and the module docstring so a future reader doesn't mistake an omission for a bug.

**Placeholder scan:** no TODO/TBD/"add appropriate handling" text anywhere above; every step has real code or a concrete, runnable command.

**Type/name consistency check:** `ctx.state.data["gc_phase"]` is the single phase key, read/written identically in Tasks 3, 4, 5, 6, 7, 8 (`"mine"`, `"chisel"`, `"craft"`, `"bank"`, `"hunt"` -- matches `PHASES`' keys in Task 8 exactly). `find_item`/`find_object` (Task 2) signatures match every call site in Tasks 3-7. `banker.FUR_POUCH_NAME`/`has_fur_pouch`/`fill_fur_pouch`/`empty_fur_pouch` (Task 1) match every call site in Tasks 5-7. `golemCraftingEnabled`/`golemCraftingBankTeleportFallback` (Task 9 Java) match the method names `exportGolemCrafting` calls in the same task.

---

Plan complete and saved to `docs/superpowers/plans/2026-08-07-golem-crafting-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
