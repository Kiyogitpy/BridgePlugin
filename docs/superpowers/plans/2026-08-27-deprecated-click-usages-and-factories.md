# Deprecated click-site cleanup + refresh_target factory adoption — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring every `click_menu_action` call site and hand-rolled `refresh_target` lambda across `ControllerAddon/routines/` and `travel.py` up to date with the 2026-08-27 `click_menu_action(refresh_target=, refresh_legs=)` addition and the existing `*_refresh_target` factories, and correct the doc comments that assert capabilities that no longer match the code.

**Architecture:** Purely additive, mechanical changes at named call sites — pass an existing factory (`npc_index_refresh_target` / `object_refresh_target` / `ground_item_refresh_target` / `ground_tile_refresh_target`, all already defined in `controller.py`) into calls that currently either omit `refresh_target` entirely or hand-roll an inline lambda duplicating a factory. No new factories, no provider/Java changes, no removal of any existing `wait_until_player_idle()` blocking call — see "Explicitly out of scope" below.

**Tech Stack:** Python 3, pytest, `unittest.mock`/`monkeypatch` (existing test conventions in `ControllerAddon/tests/`).

**Spec:** No separate spec document — this plan was scoped directly from a codebase survey (see "Background / findings" below) run against `CLAUDE.md`'s existing "Sub-tick data and clicking while moving" / "Moving-target aiming hardening" sections, which already document the target end-state pattern in detail.

## Background / findings

A survey of every file in `ControllerAddon/routines/` plus `banker.py`/`travel.py` for the two things the user asked about produced:

1. **`click_menu_action` call sites not passing `refresh_target`.** Only one call site in the whole tree already passes it (`slayer.py`'s loot take, added 2026-08-27 same day the parameter landed). Of the rest, the ones worth fixing are calls against something whose screen position can shift with the camera — an NPC, a world object, or a ground item. Calls against static UI (bank slots, inventory items, worn-equipment slots — `banker.py:258`, `banker.py:358`, `falconry.py:255`, `falconry.py:566`, `mastering_mixology.py:367`) don't move on screen regardless of the player's position, so a factory buys nothing there — **left alone, not part of this plan.**
2. **Hand-rolled `refresh_target=lambda: ...` duplicating an existing factory** instead of calling it: `tormented_demon.py:205` and `travel.py:624`. Both produce lambdas byte-for-byte equivalent to what the factory already returns, except `tormented_demon.py`'s hand-rolled version calls the un-hardened `get_npc_screen_pos_by_index` directly and so misses the 2026-08-27 deadband hardening (`_npc_view_key`) that `npc_index_refresh_target` wraps around it.
3. **Stale comments** asserting `click_menu_action` "has no refresh_target parameter" — factually true before 2026-08-27, false now: `golem_crafting.py:1454-1456`, `cannonball_crafting.py:321-323`, `slayer.py:865-866` (this last one is doc-only; the code beneath it was already fixed the same day).
4. **Tick-based `ctx.state.data` bookkeeping that could instead be derived from `ctx.events.ticks`**: nothing found. `chinchompa_hunting.py:660` already uses `events.deadline_after_ticks(...)` — the reference-correct pattern, not a problem. The other `ctx.state.data` counters found (`slayer.py`'s `engage_stuck_ticks`, `pyramid_plunder.py`'s `no_progress_ticks`) count consecutive **poll-loop iterations**, not elapsed game ticks, so `ctx.events.ticks` doesn't model what they're counting — no migration applies. **No task in this plan covers this category; it's already satisfied.**
5. **References to explicitly-removed features** (MCP debug server action tools, `TickClock` self-registration): none found anywhere in the Python routine layer. Those removals were Java/MCP-side only.

## Explicitly out of scope (do not do this in these tasks)

`golem_crafting.py`'s `_click_station(..., use_menu=True)` (around line 1443-1476) and `cannonball_crafting.py`'s `click_smelt_via_menu` (around line 316-336) both currently call `controller.wait_until_player_idle()` immediately before their `click_menu_action` call, and their doc comments say this exists *because* `click_menu_action` couldn't re-aim mid-flight. That premise is now false, which makes removing the blocking wait tempting — **do not do this here.** Both of these are deliberate escalation/fallback tiers on live, unattended, real-account automation (`golem_crafting.py`'s own module notes: "six fixes were made to what looked like one problem, and each relocated the failure instead of removing it" — the standing lesson in this codebase is that changes to this exact call shape need live re-verification, not just correct-looking code). This plan only fixes the now-inaccurate comment and adds `refresh_target` to the click itself (harmless — the wait already guarantees the position won't have moved further); it leaves `wait_until_player_idle()` in place. Removing it is a separate, deliberately live-verified change for later.

## Global Constraints

- Every routine file must still `py_compile` cleanly and the full `pytest` suite must still pass before any task is considered done (per this repo's own live-debugging workflow in `CLAUDE.md`).
- No task in this plan touches Java/provider code or requires a client rebuild — every factory and the `click_menu_action` parameter already exist in `controller.py`.
- No task removes an existing `wait_until_player_idle()`/`walk_to_exact_tile()` blocking call — see "Explicitly out of scope" above.
- Match each file's existing docstring/comment style (this codebase writes dated, reasoned comments, not generic ones) when editing a comment.

---

## Task 1: `golem_crafting.py` — plinth menu-escalation gets `refresh_target`, stale comment fixed

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py:1443-1476` (inside `_click_station`)
- Test: `ControllerAddon/tests/test_golem_crafting.py`

**Interfaces:**
- Consumes: `controller.object_refresh_target(x, y, plane, obj_id, radius=None)` (existing, `controller.py:819`), `GOLEM_CLICK_REFRESH_LEGS` (existing module constant, `golem_crafting.py:1226`, value `3`).
- Produces: nothing new — `_click_station`'s signature and return type are unchanged.

- [ ] **Step 1: Write the failing test**

Add to `ControllerAddon/tests/test_golem_crafting.py` (near the other `_click_station`/menu-escalation tests):

```python
def test_menu_escalation_plinth_click_passes_refresh_target(monkeypatch):
    """The use_menu=True escalation right-clicks the plinth via click_menu_action -- now that
    click_menu_action can re-aim mid-flight (2026-08-27), the escalation should hand it a
    refresh_target factory instead of a bare screen position, same as every other click site."""
    station = _station("north", 2596, 2257)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(
        controller, "get_object_at",
        lambda x, y, plane, obj_id, radius=None: {
            "id": obj_id, "x": x, "y": y, "plane": plane, "name": "Shape-golem",
        },
    )
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    objects = [{"id": station["objectId"], "x": station["x"], "y": station["y"], "plane": 0}]
    assert gc._click_station(_ctx(), station, objects, use_menu=True) is True
    assert seen.get("refresh_target") is not None, \
        "menu escalation must re-resolve the plinth's live screen position"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ControllerAddon/tests/test_golem_crafting.py::test_menu_escalation_plinth_click_passes_refresh_target -v`
Expected: FAIL — `seen.get("refresh_target")` is `None` (or `KeyError`/`AssertionError`), because the current call passes no `refresh_target`.

- [ ] **Step 3: Fix the code and the stale comment**

In `ControllerAddon/routines/golem_crafting.py`, replace the block at lines 1443-1476:

```python
    if use_menu:
        # Stop moving before trusting any screen position. CONFIRMED live 2026-08-23 21:24: an
        # escalation right-clicked bare tile even though it had just re-resolved the plinth,
        # because the player was still walking -- the position was read at 21:24:47.571 and the
        # cursor landed at 21:24:47.771, 200ms and roughly a third of a tile later, 6px off the
        # object. The bare-click path tolerates that via refresh_legs re-aiming mid-flight;
        # click_menu_action aims exactly once, so its aim is only trustworthy from a standstill.
        # This is already the slow path, so the wait costs the fast one nothing. See
        # wait_until_player_idle's own docstring -- it exists for precisely this call shape.
        controller.wait_until_player_idle()
        # Re-resolve the plinth's CURRENT screen position first. `objects` was fetched at the top
        # of craft_phase, before the walk, so its screenX/screenY is stale by however far the
        # player travelled -- the bare-click path hides that with refresh_target/refresh_legs
        # re-aiming mid-flight, but click_menu_action has no such parameter and would right-click
        # the stale pixel. CONFIRMED live 2026-08-23 21:12: the first menu escalations opened a
        # menu of pure ground options (['Cancel', 'Mark Safespot', 'Examine', 'Walk here']) --
        # a right-click on bare tile, not on the plinth at all. offset=(0,0) for the same reason:
        # click_menu_action's default (-7, +7) nudge is enough to slide off a small plinth.
        fresh = controller.get_object_at(
            obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
            radius=MAX_OBJECT_DISTANCE,
        )
        if fresh is not None:
            obj = fresh
```

with:

```python
    if use_menu:
        # Stop moving before trusting any screen position. CONFIRMED live 2026-08-23 21:24: an
        # escalation right-clicked bare tile even though it had just re-resolved the plinth,
        # because the player was still walking -- the position was read at 21:24:47.571 and the
        # cursor landed at 21:24:47.771, 200ms and roughly a third of a tile later, 6px off the
        # object. This is already the slow path, so the wait costs the fast one nothing. See
        # wait_until_player_idle's own docstring -- it exists for precisely this call shape.
        # click_menu_action GAINED refresh_target/refresh_legs 2026-08-27 (see its own docstring)
        # and now re-aims mid-flight like a bare click, but the idle-wait stays: it was ALSO
        # covering "click_menu_action aims exactly once" (now false too), yet removing a wait
        # this codebase's own notes call load-bearing on a live, unattended routine is a separate,
        # live-verified change -- not bundled into this factory-adoption pass.
        controller.wait_until_player_idle()
        # Re-resolve the plinth's CURRENT screen position first. `objects` was fetched at the top
        # of craft_phase, before the walk, so its screenX/screenY is stale by however far the
        # player travelled. CONFIRMED live 2026-08-23 21:12: the first menu escalations opened a
        # menu of pure ground options (['Cancel', 'Mark Safespot', 'Examine', 'Walk here']) --
        # a right-click on bare tile, not on the plinth at all. offset=(0,0) for the same reason:
        # click_menu_action's default (-7, +7) nudge is enough to slide off a small plinth.
        fresh = controller.get_object_at(
            obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
            radius=MAX_OBJECT_DISTANCE,
        )
        if fresh is not None:
            obj = fresh
        refresh = controller.object_refresh_target(
            obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
            radius=MAX_OBJECT_DISTANCE,
        )
```

Then update the `click_menu_action` call a few lines below (still inside the same `if use_menu:` block) from:

```python
        if controller.click_menu_action(ctx.ser, obj, GOLEM_PLINTH_OPTIONS, offset=(0, 0)):
```

to:

```python
        if controller.click_menu_action(ctx.ser, obj, GOLEM_PLINTH_OPTIONS, offset=(0, 0),
                                         refresh_target=refresh,
                                         refresh_legs=GOLEM_CLICK_REFRESH_LEGS):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest ControllerAddon/tests/test_golem_crafting.py -v`
Expected: PASS, including the new test and every pre-existing one in the file (the change is additive to an existing call).

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/golem_crafting.py`
Expected: no output (clean compile).

```bash
git add ControllerAddon/routines/golem_crafting.py ControllerAddon/tests/test_golem_crafting.py
git commit -m "golem_crafting: pass refresh_target to the plinth menu escalation, fix stale comment"
```

---

## Task 2: `cannonball_crafting.py` — smelt menu-escalation gets `refresh_target`, stale comment fixed

**Files:**
- Modify: `ControllerAddon/routines/cannonball_crafting.py:316-336` (`click_smelt_via_menu`)
- Test: Create `ControllerAddon/tests/test_cannonball_crafting.py` (no test file exists for this routine yet)

**Interfaces:**
- Consumes: `controller.object_refresh_target(x, y, plane, obj_id, radius=None)`, `REFRESH_LEGS` (existing module constant, `cannonball_crafting.py:121`, value `3`).
- Produces: nothing new — `click_smelt_via_menu`'s signature and return type are unchanged.

- [ ] **Step 1: Write the failing test**

Create `ControllerAddon/tests/test_cannonball_crafting.py`:

```python
"""Tests for cannonball_crafting.py's menu-escalation smelt click."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import cannonball_crafting as cc
import controller


class _Ctx:
    ser = None


def test_click_smelt_via_menu_passes_refresh_target(monkeypatch):
    """click_smelt_via_menu re-resolves the furnace via /object/at then right-clicks it -- now
    that click_menu_action can re-aim mid-flight (2026-08-27), the call should hand it a
    refresh_target factory the same way click_smelt's bare-click path already does."""
    furnace = {"x": 100, "y": 200, "plane": 0, "id": 999, "name": "Furnace"}
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(
        controller, "get_object_at",
        lambda x, y, plane, obj_id, radius=None: {
            "id": obj_id, "x": x, "y": y, "plane": plane, "name": "Furnace",
        },
    )
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    assert cc.click_smelt_via_menu(_Ctx(), furnace) is True
    assert seen.get("refresh_target") is not None, \
        "menu escalation must re-resolve the furnace's live screen position"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ControllerAddon/tests/test_cannonball_crafting.py -v`
Expected: FAIL — `seen.get("refresh_target")` is `None`.

- [ ] **Step 3: Fix the code and the stale comment**

In `ControllerAddon/routines/cannonball_crafting.py`, replace:

```python
def click_smelt_via_menu(ctx, furnace) -> bool:
    """Escalation tier: right-click, read /menu, pick "Smelt" by name -- immune to whatever is
    drawn on top of the furnace's aim pixel, which is the failure a bare click cannot retry its
    way out of (golem_crafting.py's plinth hijacks, same shape).

    Re-resolves the object through /object/at immediately before clicking: click_menu_action has
    no refresh_target and right-clicks exactly the pixel it is handed, so a position from the
    /objects scan at the top of the tick is not safe to give it once the player has moved."""
    x, y, plane = furnace.get("x"), furnace.get("y"), furnace.get("plane", 0)
    obj_id = furnace.get("id")
    if None in (x, y, obj_id):
        return False

    controller.wait_until_player_idle(timeout_s=ARRIVAL_TIMEOUT_S)
    fresh = controller.get_object_at(x, y, plane, obj_id)
    if fresh is None:
        return False

    debug_log.log("cannonball", "escalating to the right-click menu for Smelt")
    # offset=(0, 0): aim at the reported centre, not the default (-7, +7) nudge.
    return controller.click_menu_action(ctx.ser, fresh, "Smelt", offset=(0, 0))
```

with:

```python
def click_smelt_via_menu(ctx, furnace) -> bool:
    """Escalation tier: right-click, read /menu, pick "Smelt" by name -- immune to whatever is
    drawn on top of the furnace's aim pixel, which is the failure a bare click cannot retry its
    way out of (golem_crafting.py's plinth hijacks, same shape).

    Re-resolves the object through /object/at immediately before clicking, then passes
    refresh_target too: click_menu_action gained refresh_target/refresh_legs 2026-08-27, so the
    right-click now re-aims mid-flight the same as click_smelt's bare-click path. The idle-wait
    stays regardless -- see golem_crafting.py's own equivalent escalation for why removing it is
    a separate, live-verified change and not part of adopting the new parameter."""
    x, y, plane = furnace.get("x"), furnace.get("y"), furnace.get("plane", 0)
    obj_id = furnace.get("id")
    if None in (x, y, obj_id):
        return False

    controller.wait_until_player_idle(timeout_s=ARRIVAL_TIMEOUT_S)
    fresh = controller.get_object_at(x, y, plane, obj_id)
    if fresh is None:
        return False

    debug_log.log("cannonball", "escalating to the right-click menu for Smelt")
    refresh = controller.object_refresh_target(x, y, plane, obj_id)
    # offset=(0, 0): aim at the reported centre, not the default (-7, +7) nudge.
    return controller.click_menu_action(ctx.ser, fresh, "Smelt", offset=(0, 0),
                                         refresh_target=refresh, refresh_legs=REFRESH_LEGS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest ControllerAddon/tests/test_cannonball_crafting.py -v`
Expected: PASS.

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/cannonball_crafting.py`
Expected: no output.

```bash
git add ControllerAddon/routines/cannonball_crafting.py ControllerAddon/tests/test_cannonball_crafting.py
git commit -m "cannonball_crafting: pass refresh_target to the smelt menu escalation, fix stale comment"
```

---

## Task 3: `golem_crafting.py` — gem pickup and Spikes-supply `take-5` get `refresh_target`

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py:1747` (inside `_collect_golem_gift`) and `:2438` (inside `_ensure_wooden_spikes`)
- Test: `ControllerAddon/tests/test_golem_crafting.py`

**Interfaces:**
- Consumes: `controller.ground_item_refresh_target(x, y, plane, item_id)` (`controller.py:830`), `controller.object_refresh_target(x, y, plane, obj_id, radius=None)`, `GOLEM_CLICK_REFRESH_LEGS`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Add to `ControllerAddon/tests/test_golem_crafting.py`:

```python
def test_gem_pickup_menu_click_passes_refresh_target(monkeypatch):
    """_collect_golem_gift's click_menu_action Take should re-resolve the gift's live screen
    position, same as every other ground-item menu click in this codebase (see slayer.py's
    maybe_loot for the reference pattern)."""
    station = _station("north", 2596, 2257)
    item = {"x": station["x"], "y": station["y"], "plane": 0, "id": 5, "name": "Uncut sapphire"}
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: [item])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    gc._collect_golem_gift(_ctx(), station, "Uncut sapphire")
    assert seen.get("refresh_target") is not None


def test_spikes_supply_menu_click_passes_refresh_target(monkeypatch):
    """_ensure_wooden_spikes's Take-5 click should re-resolve the supply's live screen
    position."""
    supply = {"x": 100, "y": 200, "plane": 0, "id": 42, "name": "Spikes supply"}
    monkeypatch.setattr(gc, "find_item", lambda name: None if name == gc.WOODEN_SPIKES_NAME
                         else {"name": name})
    monkeypatch.setattr(gc, "find_object", lambda name: supply)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    gc._ensure_wooden_spikes(_ctx(), gc.DEFAULT_CONFIG)
    assert seen.get("refresh_target") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest ControllerAddon/tests/test_golem_crafting.py -k "menu_click_passes_refresh_target" -v`
Expected: both FAIL — neither call currently passes `refresh_target`.

- [ ] **Step 3: Fix the code**

In `ControllerAddon/routines/golem_crafting.py`, inside `_collect_golem_gift` (around line 1742-1747), replace:

```python
        target = candidates[0]
        debug_log.log(
            "golem crafting",
            f"gem-bag-full pickup attempt {attempt}/{GOLEM_GEM_PICKUP_ATTEMPTS}: {target.get('name')}",
        )
        controller.click_menu_action(ctx.ser, target, "take", target_hint=target.get("name"), offset=(0, 0))
        controller.human_delay(0.6, 1.0)
```

with:

```python
        target = candidates[0]
        debug_log.log(
            "golem crafting",
            f"gem-bag-full pickup attempt {attempt}/{GOLEM_GEM_PICKUP_ATTEMPTS}: {target.get('name')}",
        )
        refresh = controller.ground_item_refresh_target(
            target.get("x"), target.get("y"), target.get("plane", 0), target.get("id"),
        )
        controller.click_menu_action(ctx.ser, target, "take", target_hint=target.get("name"),
                                      offset=(0, 0), refresh_target=refresh,
                                      refresh_legs=GOLEM_CLICK_REFRESH_LEGS)
        controller.human_delay(0.6, 1.0)
```

Inside `_ensure_wooden_spikes` (around line 2433-2438), replace:

```python
    debug_log.log("golem crafting", "taking 5 wooden spikes from the Spikes supply")
    controller.click_menu_action(ctx.ser, supply, "take-5")
    controller.human_delay(0.4, 0.7)
```

with:

```python
    debug_log.log("golem crafting", "taking 5 wooden spikes from the Spikes supply")
    refresh = controller.object_refresh_target(
        supply.get("x"), supply.get("y"), supply.get("plane", 0), supply.get("id"),
    )
    controller.click_menu_action(ctx.ser, supply, "take-5", refresh_target=refresh,
                                  refresh_legs=GOLEM_CLICK_REFRESH_LEGS)
    controller.human_delay(0.4, 0.7)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ControllerAddon/tests/test_golem_crafting.py -v`
Expected: PASS, full file.

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/golem_crafting.py`
Expected: no output.

```bash
git add ControllerAddon/routines/golem_crafting.py ControllerAddon/tests/test_golem_crafting.py
git commit -m "golem_crafting: pass refresh_target to the gem pickup and Spikes-supply menu clicks"
```

---

## Task 4: `chinchompa_hunting.py` — dropped-trap recovery gets `refresh_target`

**Files:**
- Modify: `ControllerAddon/routines/chinchompa_hunting.py:919` (inside `recover_dropped_trap`)
- Test: `ControllerAddon/tests/test_chinchompa_hunting.py`

**Interfaces:**
- Consumes: `controller.ground_item_refresh_target(x, y, plane, item_id)`, `TRAP_CLICK_REFRESH_LEGS` (existing module constant, `chinchompa_hunting.py:150`, value `None` — deliberately no extra legs, see that constant's own comment).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Add to `ControllerAddon/tests/test_chinchompa_hunting.py` (match that file's existing stubbing style for `recover_dropped_trap` if a fixture already sets one up; otherwise add standalone):

```python
def test_recover_dropped_trap_passes_refresh_target(monkeypatch):
    """recover_dropped_trap's Take click should re-resolve the trap item's live screen position,
    same as every other ground-item menu click in this codebase."""
    tile = (2513, 9301, 0)
    item = {"x": tile[0], "y": tile[1], "plane": tile[2], "id": ch.BOX_TRAP_ITEM_ID}
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_ground_item_at", lambda x, y, plane, item_id: item)
    monkeypatch.setattr(ch, "box_trap_count", lambda: 0)
    monkeypatch.setattr(ch, "_note_acted_on", lambda ctx, tile: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    ch.recover_dropped_trap(_Ctx(), tile, item)
    assert seen.get("refresh_target") is not None
```

Uses the file's existing `_Ctx` class (`test_chinchompa_hunting.py:32-36`, already imports `chinchompa_hunting as ch` and `controller`) — no new fixtures needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ControllerAddon/tests/test_chinchompa_hunting.py -k refresh_target -v`
Expected: FAIL.

- [ ] **Step 3: Fix the code**

In `ControllerAddon/routines/chinchompa_hunting.py`, inside `recover_dropped_trap`, replace:

```python
    if not controller.click_menu_action(ctx.ser, target, DROPPED_TRAP_ACTIONS,
                                        target_hint=BOX_TRAP_ITEM_NAME):
        return False
```

with:

```python
    refresh = controller.ground_item_refresh_target(tile[0], tile[1], tile[2], BOX_TRAP_ITEM_ID)
    if not controller.click_menu_action(ctx.ser, target, DROPPED_TRAP_ACTIONS,
                                        target_hint=BOX_TRAP_ITEM_NAME,
                                        refresh_target=refresh,
                                        refresh_legs=TRAP_CLICK_REFRESH_LEGS):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ControllerAddon/tests/test_chinchompa_hunting.py -v`
Expected: PASS, full file.

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/chinchompa_hunting.py`
Expected: no output.

```bash
git add ControllerAddon/routines/chinchompa_hunting.py ControllerAddon/tests/test_chinchompa_hunting.py
git commit -m "chinchompa_hunting: pass refresh_target to the dropped-trap recovery click"
```

---

## Task 5: `pyramid_plunder.py` — lobby start and quick-leave get `refresh_target`

**Files:**
- Modify: `ControllerAddon/routines/pyramid_plunder.py:613` (`lobby_tick`) and `:1192` (`quick_leave`)
- Test: Create `ControllerAddon/tests/test_pyramid_plunder.py` (no test file exists for this routine yet)

**Interfaces:**
- Consumes: `controller.npc_index_refresh_target(npc_index)`, `controller.object_refresh_target(x, y, plane, obj_id, radius=None)`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_pyramid_plunder.py`:

```python
"""Tests for pyramid_plunder.py's two click_menu_action call sites gaining refresh_target."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pyramid_plunder as pp
import controller


class _Ctx:
    ser = None
    state = None


def test_lobby_tick_passes_refresh_target(monkeypatch):
    mummy = {"index": 7, "x": 100, "y": 200, "plane": 0, "name": "Guardian mummy"}
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(pp, "note_progress", lambda ctx: None)
    monkeypatch.setattr(controller, "get_pyramid_plunder", lambda: {"inRun": False})
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return False  # bail out before the confirmation poll loop; only the call matters here
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    pp.lobby_tick(_Ctx(), pp.DEFAULT_CONFIG, mummy)
    assert seen.get("refresh_target") is not None


def test_quick_leave_passes_refresh_target(monkeypatch):
    exit_door = {"id": 123, "x": 100, "y": 200, "plane": 0, "name": "Tomb Door"}
    monkeypatch.setattr(pp, "find_exit_doors", lambda objs: [exit_door])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [exit_door])
    monkeypatch.setattr(pp, "note_progress", lambda ctx: None)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return False
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    pp.quick_leave(_Ctx(), pp.DEFAULT_CONFIG)
    assert seen.get("refresh_target") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest ControllerAddon/tests/test_pyramid_plunder.py -v`
Expected: both FAIL — neither call currently passes `refresh_target`.

- [ ] **Step 3: Fix the code**

In `ControllerAddon/routines/pyramid_plunder.py`, inside `lobby_tick`, replace:

```python
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if not controller.click_menu_action(ctx.ser, mummy, "Start-minigame", check_ui_blockers=False):
        return
```

with:

```python
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    refresh = controller.npc_index_refresh_target(mummy["index"])
    if not controller.click_menu_action(ctx.ser, mummy, "Start-minigame", check_ui_blockers=False,
                                         refresh_target=refresh):
        return
```

Inside `quick_leave`, replace:

```python
    for exit_door in exit_doors[:3]:
        if controller.click_menu_action(ctx.ser, exit_door, "Quick-leave",
                                         offset=TOMB_DOOR_AIM_OFFSET, check_ui_blockers=False):
```

with:

```python
    for exit_door in exit_doors[:3]:
        refresh = controller.object_refresh_target(
            exit_door.get("x"), exit_door.get("y"), exit_door.get("plane", 0), exit_door.get("id"),
        )
        if controller.click_menu_action(ctx.ser, exit_door, "Quick-leave",
                                         offset=TOMB_DOOR_AIM_OFFSET, check_ui_blockers=False,
                                         refresh_target=refresh):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ControllerAddon/tests/test_pyramid_plunder.py -v`
Expected: PASS.

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/pyramid_plunder.py`
Expected: no output.

```bash
git add ControllerAddon/routines/pyramid_plunder.py ControllerAddon/tests/test_pyramid_plunder.py
git commit -m "pyramid_plunder: pass refresh_target to the lobby-start and quick-leave menu clicks"
```

---

## Task 6: `falconry.py` — hiring Matthias gets `refresh_target`

**Files:**
- Modify: `ControllerAddon/routines/falconry.py:434` (inside `_hire_falcon`)
- Test: Create `ControllerAddon/tests/test_falconry.py` (no test file exists for this routine yet)

**Interfaces:**
- Consumes: `controller.npc_index_refresh_target(npc_index)`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `ControllerAddon/tests/test_falconry.py`:

```python
"""Tests for falconry.py's Quick-falcon menu click gaining refresh_target."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import falconry
import controller


class _Ctx:
    ser = None


def test_hire_falcon_passes_refresh_target(monkeypatch):
    matthias = {"index": 3, "x": 100, "y": 200, "plane": 0, "name": "Matthias", "distance": 2}
    monkeypatch.setattr(falconry, "_first_inventory_item",
                         lambda sub: {"qty": falconry.DEFAULT_CONFIG["minCoins"]})
    monkeypatch.setattr(falconry, "_nearest_npc", lambda name, max_distance: matthias)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    assert falconry._hire_falcon(_Ctx()) is True
    assert seen.get("refresh_target") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ControllerAddon/tests/test_falconry.py -v`
Expected: FAIL.

- [ ] **Step 3: Fix the code**

In `ControllerAddon/routines/falconry.py`, inside `_hire_falcon`, replace:

```python
    debug_log.log("falconry", "hiring a falcon from Matthias")
    if not controller.click_menu_action(ctx.ser, matthias, OPT_QUICK_FALCON):
        return False
```

with:

```python
    debug_log.log("falconry", "hiring a falcon from Matthias")
    refresh = controller.npc_index_refresh_target(matthias["index"])
    if not controller.click_menu_action(ctx.ser, matthias, OPT_QUICK_FALCON, refresh_target=refresh):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ControllerAddon/tests/test_falconry.py -v`
Expected: PASS.

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/falconry.py`
Expected: no output.

```bash
git add ControllerAddon/routines/falconry.py ControllerAddon/tests/test_falconry.py
git commit -m "falconry: pass refresh_target to the Quick-falcon menu click"
```

---

## Task 7: `tormented_demon.py` — swap hand-rolled lambda for `npc_index_refresh_target`

**Files:**
- Modify: `ControllerAddon/routines/tormented_demon.py:199-206`
- Test: Create `ControllerAddon/tests/test_tormented_demon.py` (no test file exists for this routine yet)

**Interfaces:**
- Consumes: `controller.npc_index_refresh_target(npc_index)`.
- Produces: nothing new.

This is the one case CLAUDE.md names explicitly: `tormented_demon.py` still hand-rolls its own `refresh_target` lambda calling the un-hardened `get_npc_screen_pos_by_index` directly, so it misses the 2026-08-27 deadband hardening (`_npc_view_key`: holds the aim point through hull-animation jitter, only re-resolving on real tile/camera motion) that every other NPC click site already gets through the factory. The click site lives directly inside `tick(ctx)` (`tormented_demon.py:169-206`) — there is no separate `engage`-style helper wrapping it, so the test drives `tick` itself.

- [ ] **Step 1: Write the failing test**

Create `ControllerAddon/tests/test_tormented_demon.py`:

```python
"""Tests for tormented_demon.py's tick() combat click using the refresh_target factory."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import tormented_demon as td
import controller


class _State:
    def __init__(self, config):
        self.config = config


class _Ctx:
    def __init__(self, config):
        self.ser = None
        self.state = _State(config)


def test_tick_uses_the_npc_index_refresh_target_factory(monkeypatch):
    """Regression: a hand-rolled lambda calling get_npc_screen_pos_by_index directly bypasses
    the 2026-08-27 deadband hardening that npc_index_refresh_target wraps around it. Asserting
    the factory itself was called (not just any callable) is what catches a regression back to
    the hand-rolled version."""
    npc = {"index": 9, "x": 100, "y": 200, "plane": 0, "name": "Tormented Demon"}
    config = {"food": {"enabled": False}}

    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 99})
    monkeypatch.setattr(td, "find_target", lambda cfg: npc)
    monkeypatch.setattr(td, "maybe_dodge_fireball", lambda ctx, player: False)
    monkeypatch.setattr(td, "react_to_attack_animation", lambda ctx, npc: None)
    monkeypatch.setattr(td, "maintain_prayer_potions", lambda ctx, cfg: None)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": True})

    factory_calls = []
    real_factory = controller.npc_index_refresh_target
    def _spy_factory(npc_index):
        factory_calls.append(npc_index)
        return real_factory(npc_index)
    monkeypatch.setattr(controller, "npc_index_refresh_target", _spy_factory)

    click_kwargs = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, e, **kw: click_kwargs.append(kw) or True)

    td.tick(_Ctx(config))
    assert factory_calls == [9], "must go through npc_index_refresh_target, not a hand-rolled lambda"
    assert click_kwargs and click_kwargs[0].get("refresh_target") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ControllerAddon/tests/test_tormented_demon.py -v`
Expected: FAIL — `factory_calls` is empty, since the current code never calls `npc_index_refresh_target` at all.

- [ ] **Step 3: Fix the code**

In `ControllerAddon/routines/tormented_demon.py`, replace:

```python
    combat = controller.get_combat()
    if combat.get("isIdle", True):
        npc_index = npc["index"]
        controller.click_entity(
            ctx.ser,
            npc,
            refresh_target=lambda: controller.get_npc_screen_pos_by_index(npc_index),
        )
```

with:

```python
    combat = controller.get_combat()
    if combat.get("isIdle", True):
        npc_index = npc["index"]
        controller.click_entity(
            ctx.ser,
            npc,
            refresh_target=controller.npc_index_refresh_target(npc_index),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ControllerAddon/tests/test_tormented_demon.py -v`
Expected: PASS.

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/tormented_demon.py`
Expected: no output.

```bash
git add ControllerAddon/routines/tormented_demon.py ControllerAddon/tests/test_tormented_demon.py
git commit -m "tormented_demon: use the npc_index_refresh_target factory instead of a hand-rolled lambda"
```

---

## Task 8: `travel.py` — dedupe `walk_to_exact_tile`'s hand-rolled lambda into `ground_tile_refresh_target`

**Files:**
- Modify: `ControllerAddon/travel.py:624`
- Test: `ControllerAddon/tests/test_travel.py`

**Interfaces:**
- Consumes: `controller.ground_tile_refresh_target(x, y, plane)` (existing, `controller.py:812` — its body is `lambda: get_ground_walk_screen_pos(x, y, plane)`, byte-for-byte identical to the lambda this task removes).
- Produces: nothing new — `walk_to_exact_tile`'s signature and behavior are unchanged; this is pure deduplication, used by both `chinchompa_hunting.py` and `golem_crafting.py`'s `walk_to_exact_tile` wrapper.

- [ ] **Step 1: Write the failing test**

Add to `ControllerAddon/tests/test_travel.py` (near the existing `walk_to_exact_tile` non-blocking-mode tests around line 337+):

```python
def test_walk_to_exact_tile_uses_the_ground_tile_refresh_target_factory(monkeypatch):
    """Regression: walk_to_exact_tile used to hand-roll its own lambda duplicating
    ground_tile_refresh_target's body instead of calling the factory. Assert the factory itself
    gets called so a future edit can't silently reintroduce the duplicate."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                         lambda x, y, plane: {"onScreen": True})
    monkeypatch.setattr(controller, "key_down", lambda ser, key: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, key: None)

    factory_calls = []
    real_factory = controller.ground_tile_refresh_target
    def _spy_factory(x, y, plane):
        factory_calls.append((x, y, plane))
        return real_factory(x, y, plane)
    monkeypatch.setattr(controller, "ground_tile_refresh_target", _spy_factory)

    click_kwargs = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, e, **kw: click_kwargs.append(kw) or True)

    travel.walk_to_exact_tile(_Ctx(), 12, 12, 0, wait_for_arrival=False)
    assert factory_calls == [(12, 12, 0)]
    assert click_kwargs and click_kwargs[0].get("refresh_target") is not None
```

Reuse the `_Ctx` class already defined in `test_travel.py` around line 373 (`"""Minimal stand-in for controller.Context -- walk_to_exact_tile only ever reads ctx.ser."""`) rather than redefining it.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ControllerAddon/tests/test_travel.py -k ground_tile_refresh_target -v`
Expected: FAIL — `factory_calls` is empty, since `walk_to_exact_tile` currently builds its own inline lambda instead of calling `controller.ground_tile_refresh_target`.

- [ ] **Step 3: Fix the code**

In `ControllerAddon/travel.py`, replace:

```python
    refresh = lambda: controller.get_ground_walk_screen_pos(x, y, plane)
```

with:

```python
    refresh = controller.ground_tile_refresh_target(x, y, plane)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ControllerAddon/tests/test_travel.py -v`
Expected: PASS, full file (this is a behavior-preserving swap — same function body, different call path).

- [ ] **Step 5: Compile check + commit**

Run: `python -m py_compile ControllerAddon/travel.py`
Expected: no output.

```bash
git add ControllerAddon/travel.py ControllerAddon/tests/test_travel.py
git commit -m "travel: walk_to_exact_tile uses the ground_tile_refresh_target factory instead of a duplicate lambda"
```

---

## Task 9: `slayer.py` — doc-only fix for the one stale comment

**Files:**
- Modify: `ControllerAddon/routines/slayer.py:865-866`
- Test: none — this is a comment-only change; the code beneath it already passes `refresh_target` (fixed 2026-08-27, same day the parameter landed) and is already covered by `slayer.py`'s existing tests.

- [ ] **Step 1: Fix the stale comment**

In `ControllerAddon/routines/slayer.py`, replace:

```python
                # This matters more here than for an NPC click: click_menu_action has NO
                # refresh_target parameter (see CLAUDE.md), so it right-clicks precisely the pixel
                # it is handed. Re-resolving immediately beforehand is the only correction
                # available. Symptom when it is stale, seen live: "No 'take' entry in menu ...
                # ['Cancel', 'Mark Safespot', 'Walk here']" -- a right-click on bare ground.
```

with:

```python
                # This mattered even more here than for an NPC click, back when click_menu_action
                # had no refresh_target parameter and right-clicked precisely the pixel it was
                # handed -- re-resolving immediately beforehand was the only correction available.
                # Fixed 2026-08-27 (see the refresh_target= passed below); re-resolving here still
                # matters as the position fed into that factory, it just isn't the only defense
                # anymore. Symptom when it was stale, seen live: "No 'take' entry in menu ...
                # ['Cancel', 'Mark Safespot', 'Walk here']" -- a right-click on bare ground.
```

- [ ] **Step 2: Run the full slayer test file to confirm nothing broke**

Run: `python -m pytest ControllerAddon/tests/ -k slayer -v`
Expected: PASS, unchanged (comment-only edit).

- [ ] **Step 3: Compile check + commit**

Run: `python -m py_compile ControllerAddon/routines/slayer.py`
Expected: no output.

```bash
git add ControllerAddon/routines/slayer.py
git commit -m "slayer: fix stale comment about click_menu_action lacking refresh_target"
```

---

## Final check (run once, after all 9 tasks)

- [ ] Run the full suite: `python -m pytest ControllerAddon/tests/ -v` — expect all green.
- [ ] Run `python -m py_compile` over every modified/created file in one pass:
  `python -m py_compile ControllerAddon/routines/golem_crafting.py ControllerAddon/routines/cannonball_crafting.py ControllerAddon/routines/chinchompa_hunting.py ControllerAddon/routines/pyramid_plunder.py ControllerAddon/routines/falconry.py ControllerAddon/routines/tormented_demon.py ControllerAddon/routines/slayer.py ControllerAddon/travel.py`
- [ ] Update this repo's `CLAUDE.md` if any of these routines' own "Current state of routines/X.py" sections mention the now-fixed stale-comment claims verbatim (golem_crafting.py's and cannonball_crafting.py's sections both quote the "click_menu_action has no such parameter" line) — keep the doc's own account in sync with the code, same standard the rest of `CLAUDE.md` holds itself to.
- [ ] None of these changes have been run against a live client. Per this codebase's own standing practice (see `CLAUDE.md`'s live-debugging workflow), the next live session for each touched routine should confirm no regression before treating the change as proven, not just tested.
