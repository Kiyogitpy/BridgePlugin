# Pyramid Plunder Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a loot-only Pyramid Plunder automation routine (Grand Gold Chest + Sarcophagus, rooms 1-6, restart after room 6) plus the one new bridge endpoint it needs.

**Architecture:** A new `PyramidPlunderProvider.java` exposes room number / run timer / spear-trap state via three varbits RuneLite's own official Pyramid Plunder plugin already reads. A new `routines/pyramid_plunder.py` re-derives its phase (outside the pyramid / in the lobby / in a timed room) fresh from that endpoint plus `/player` and `/npcs` every tick — same self-correcting dispatch style as `pest_control.py` — so wherever a Quick-leave or a sceptre-triggered eject actually drops the player, the very next tick picks the right phase on its own. Final task wires the routine into `ROUTINE_FILES`/`RoutineChoice.java` per this codebase's standard routine-contract steps.

**Tech Stack:** Java 11 / RuneLite plugin API (provider), Python 3 (routine), existing HTTP bridge (`localhost:8080`) and Arduino-serial input layer — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-16-pyramid-plunder-design.md`

## Global Constraints

These are copied verbatim from the spec and apply to every task below:

- Region: the whole pyramid interior is a single region id **`7749`**.
- Varbits (`net.runelite.api.gameval.VarbitID`): `NTK_ROOM_NUMBER` (2377, room 1-8), `NTK_PLAYER_TIMER_COUNT` (2375, ticks elapsed this run; `0` = not in a run), `NTK_TRAP_ACTIVE` (2365, `1` = current room's spear trap still armed).
- Object ids (decompiled-client-confirmed, take priority over the wiki infobox where they differ): spear trap `21280`; Grand Gold Chest closed `20946` / searched `26616`; Sarcophagus closed `21255` / opened-searchable `26626`; Tomb Door closed appearance `20948`, the 4 physical wall-object instances `26618`-`26621`; Tomb Door (exit) `20931` with a **"Quick-leave"** menu option.
- Wrong-tomb-door chat text (user-confirmed, case-insensitive substring match): `"this door leads to a dead end."`
- Loot policy: only Grand Gold Chest + Sarcophagus are looted (never urns); sarcophagi are skipped in rooms 1-3 and looted rooms 4-6; the run is deliberately abandoned via Quick-leave after room 6 and restarted, never pushed to rooms 7-8; every artefact from a fixed name list is dropped, **except** "Pharaoh's sceptre" which is never dropped.
- No banking, no prayer management (rejected explicitly in the spec) — only a generic `hp <= eatAt` food-eat safety net, same as `slayer.py`.
- No `ControllerConfig.java` panel/section — this routine never reads `GET /config`.
- Several mechanics are explicitly **unverified** pending live testing (see each task's "NEEDS LIVE VERIFICATION" comments): outer-door wrong-entry detection/recovery, whether Pick-lock+Enter is one click or two, whether the spear trap is mandatory to path through, and the sarcophagus opening-animation timing. Every task implements a concrete best-effort behavior for these (never a bare TODO), flagged inline exactly like this codebase's existing `blast_furnace.py`/`golem_crafting.py` "ASSUMPTION, NOT YET VERIFIED LIVE" convention.
- This codebase has no unit-test framework for routines or bridge providers (confirmed: no test file exists for any of `pest_control.py`, `golem_crafting.py`, `MixologyProvider.java`, `PlayerProvider.java`, etc. — only `controller.py`'s own directly-imported functions get `tests/test_controller.py` coverage). This plan follows that established boundary rather than introducing a new test file: verification is `py_compile`/`compileJava` (syntax/build correctness) plus staged live testing against the running bridge, matching this codebase's own `AGENTS.md`/CLAUDE.md live-debugging workflow.

---

### Task 1: `PyramidPlunderProvider.java` + Python-side accessor

**Files:**
- Create: `src/main/java/com/example/providers/PyramidPlunderProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (register the new provider)
- Modify: `ControllerAddon/controller.py` (add `get_pyramid_plunder()` wrapper)

**Interfaces:**
- Produces: `GET /pyramid-plunder` → `{"inRun": bool, "roomNumber": int, "timerTicks": int, "trapActive": bool}`.
- Produces (Python): `controller.get_pyramid_plunder() -> dict` with the same shape (never `None` — falls back to a safe all-false/zero default, same idiom as `get_sailing()`/`get_combat()`).
- Consumes: `BridgeProvider` base class (`src/main/java/com/example/providers/BridgeProvider.java`) — constructor `(Client, ClientThread, BridgeHttpServer)`, `register()` abstract method, `onClient(SupplierLike)` helper.

- [ ] **Step 1: Write `PyramidPlunderProvider.java`**

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.HashMap;
import java.util.Map;

/**
 * Reads Pyramid Plunder's live state (room number, run timer, spear-trap-armed flag) directly
 * from the same varbits the official RuneLite Pyramid Plunder plugin uses --
 * data/decompiled/client-1.12.33/net/runelite/client/plugins/pyramidplunder/PyramidPlunderPlugin.java
 * (isInPyramidPlunder(), onGameTick()) -- cross-referenced against named constants in
 * IdeaProjects/runelite's runelite-api VarbitID.java (NTK_TRAP_ACTIVE=2365,
 * NTK_PLAYER_TIMER_COUNT=2375, NTK_ROOM_NUMBER=2377). See
 * docs/superpowers/specs/2026-08-16-pyramid-plunder-design.md's Research method section.
 */
public class PyramidPlunderProvider extends BridgeProvider
{
	private static final int PYRAMID_PLUNDER_REGION = 7749;

	public PyramidPlunderProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/pyramid-plunder", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();

		Player local = client.getLocalPlayer();
		boolean inRegion = local != null && local.getWorldLocation().getRegionID() == PYRAMID_PLUNDER_REGION;
		int timerTicks = client.getVarbitValue(VarbitID.NTK_PLAYER_TIMER_COUNT);

		// Matches PyramidPlunderPlugin.isInPyramidPlunder() exactly: region + timer running.
		data.put("inRun", inRegion && timerTicks > 0);
		data.put("roomNumber", client.getVarbitValue(VarbitID.NTK_ROOM_NUMBER));
		data.put("timerTicks", timerTicks);
		data.put("trapActive", client.getVarbitValue(VarbitID.NTK_TRAP_ACTIVE) == 1);

		return data;
	}
}
```

- [ ] **Step 2: Register it in `ControllerPlugin.java`**

Add alongside the other providers (same file, same block as `MixologyProvider`):

```java
		new MixologyProvider(client, clientThread, server).register();
		new PyramidPlunderProvider(client, clientThread, server).register();
```

- [ ] **Step 3: Compile check**

Run: `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`, no errors referencing `PyramidPlunderProvider` or `VarbitID.NTK_*`.

- [ ] **Step 4: Add the Python-side accessor**

In `ControllerAddon/controller.py`, add next to `get_sailing()`/`get_combat()` (same file, same style — a thin `api_get` wrapper with a safe default):

```python
def get_pyramid_plunder():
    return api_get("/pyramid-plunder") or {"inRun": False, "roomNumber": 0, "timerTicks": 0, "trapActive": False}
```

- [ ] **Step 5: Syntax check**

Run (from `ControllerAddon/`): `python -m py_compile controller.py`
Expected: no output (success).

- [ ] **Step 6: Regression check**

Run (from `ControllerAddon/`): `python -m pytest tests/ -v`
Expected: all existing tests still pass (this change only adds a new function, doesn't modify existing behavior).

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/providers/PyramidPlunderProvider.java src/main/java/com/example/controller/ControllerPlugin.java ControllerAddon/controller.py
git commit -m "Add PyramidPlunderProvider bridge endpoint for room/timer/trap state"
```

---

### Task 2: `routines/pyramid_plunder.py` — config, helpers, and the outside/lobby entry loop

**Files:**
- Create: `ControllerAddon/routines/pyramid_plunder.py`

**Interfaces:**
- Consumes: `controller.get_pyramid_plunder()` (Task 1), `controller.get_objects(radius=None, fresh=False)`, `controller.get_npcs(fresh=False)`, `controller.get_player()`, `controller.click_entity(ser, entity, offset=(-7,7), check_ui_blockers=True, ...)`, `controller.click_menu_action(ser, entity, option, target_hint=None, offset=(-7,7), check_ui_blockers=True) -> bool`, `controller.wait_until_player_idle(timeout_s=5.0) -> bool`, `controller.human_delay(min_s, max_s)`, `controller.consume_first(ser, category) -> bool`, `debug_log.log(tag, message)`.
- Produces: module-level `DEFAULT_CONFIG`, `get_config(bridge_config)`, `setup(ctx)`, `tick(ctx)` (routine contract entry points controller.py's main loop calls); local helpers `find_object_by_id`, `find_objects_by_name`, `find_npc_by_name`, `order_doors_clockwise`, `maybe_eat_food` reused by Task 3.
- Room-loop phase functions (`in_room_tick` and its sub-steps) are added in Task 3, in the same file — this task only needs to leave `tick()` dispatching to a stub `in_room_tick(ctx, config, pp)` that Task 3 fills in.

- [ ] **Step 1: Write the file header, constants, and config**

```python
# routines/pyramid_plunder.py
#
# Pyramid Plunder loot-only routine: Grand Gold Chest + Sarcophagus only (never the 13 urns
# per room), sarcophagi skipped in rooms 1-3, looted rooms 4-6, run deliberately abandoned via
# Quick-leave after room 6 and restarted from room 1 rather than pushed to rooms 7-8. All loot
# is dropped on the spot except the Pharaoh's sceptre -- no banking loop in this routine at all.
#
# See docs/superpowers/specs/2026-08-16-pyramid-plunder-design.md for the full design and its
# "NEEDS LIVE VERIFICATION" list -- several mechanics below (outer-door wrong-entry recovery,
# tomb-door click sequence, spear trap necessity, sarcophagus opening timing) are first-cut
# assumptions pending live testing, flagged inline same as this codebase's existing
# blast_furnace.py/golem_crafting.py "ASSUMPTION, NOT YET VERIFIED LIVE" convention.
#
# Phase dispatch is re-evaluated fresh every tick from live bridge state (mirrors
# pest_control.py's region-based tick()) -- no phase is ever assumed, so wherever a
# Quick-leave/timeout/sceptre-eject actually drops the player, the very next tick's read of
# /pyramid-plunder + /player + /npcs picks the right phase on its own.
#
# Run with:
#     python controller.py routines/pyramid_plunder.py

import math

import controller
import debug_log

PYRAMID_REGION_ID = 7749

SPEAR_TRAP_ID = 21280
GRAND_GOLD_CHEST_CLOSED_ID = 20946
GRAND_GOLD_CHEST_OPENED_ID = 26616
SARCOPHAGUS_CLOSED_ID = 21255
SARCOPHAGUS_OPENED_ID = 26626
TOMB_DOOR_CLOSED_ID = 20948
TOMB_DOOR_WALL_IDS = {26618, 26619, 26620, 26621}
EXIT_DOOR_ID = 20931

GUARDIAN_MUMMY_NAME = "guardian mummy"
ANONYMOUS_DOOR_NAME = "anonymous looking door"

ROOM_STEP_TRAP = "trap"
ROOM_STEP_CHEST = "chest"
ROOM_STEP_SARCO = "sarco"
ROOM_STEP_DOOR = "door"

DEFAULT_CONFIG = {
    "enabled": True,
    "lastRoomToLoot": 6,          # loot chest+sarco through this room, then quick-leave and restart
    "sarcophagusStartRoom": 4,    # skip sarco in rooms before this
    "artefactNames": [            # fixed drop-list, lowercase-matched -- never a substring match,
        "ivory comb", "pottery scarab", "pottery statuette", "stone seal",   # so food/lockpick/the
        "stone scarab", "stone statuette", "gold seal", "golden scarab",    # sceptre are never at
        "golden statuette",                                                 # risk of being dropped
    ],
    "food": {"enabled": True, "eatAt": 5},   # same shape as slayer.py's food_cfg
    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}


def get_config(bridge_config):
    # No Controller Settings panel for this (same as pest_control.py/mining.py) -- this routine
    # never reads GET /config, just use DEFAULT_CONFIG.
    return DEFAULT_CONFIG
```

- [ ] **Step 2: Write the shared lookup helpers**

Append to the same file:

```python
def find_object_by_id(objects: list, object_id: int):
    candidates = [o for o in objects if o.get("id") == object_id and o.get("visible", True)]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def find_objects_by_name(objects: list, name_substring: str) -> list:
    name_substring = name_substring.lower()
    return [o for o in objects
            if name_substring in (o.get("name") or "").lower() and o.get("visible", True)]


def find_npc_by_name(npcs: list, name: str):
    name = name.lower()
    candidates = [n for n in npcs if (n.get("name") or "").lower() == name]
    if not candidates:
        return None
    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


def order_doors_clockwise(doors: list) -> list:
    """Stable ordering of the 4 static anonymous-door objects by angle around their own
    centroid, so the same physical door always resolves to the same index (0-3) across every
    call this session -- lets the OUTSIDE phase cache "correct_door_index" and reuse it
    directly on future entries instead of re-deriving it from scratch every restart. Pure
    function (no bridge calls) -- doesn't need a live client to reason about."""
    cx = sum(d.get("x", 0) for d in doors) / len(doors)
    cy = sum(d.get("y", 0) for d in doors) / len(doors)
    return sorted(doors, key=lambda d: math.atan2(d.get("y", 0) - cy, d.get("x", 0) - cx))


def maybe_eat_food(ctx, config) -> None:
    """hp <= eatAt -> eat, same inline pattern as slayer.py's food handling. Independent of
    phase -- called first thing in tick(), every tick, regardless of OUTSIDE/LOBBY/IN_ROOM."""
    food_cfg = config.get("food", {})
    if not food_cfg.get("enabled"):
        return
    hp = controller.get_player().get("hp")
    if hp is not None and hp <= food_cfg.get("eatAt", 0):
        if controller.consume_first(ctx.ser, "food"):
            debug_log.log("pyramid_plunder", f"ate food (hp={hp})")
```

- [ ] **Step 3: Write the OUTSIDE phase**

```python
def outside_tick(ctx, config) -> None:
    """Not near the Guardian mummy yet -- find and enter the correct one of the 4 static
    anonymous-door objects. Caches the winning index in ctx.state.data["correct_door_index"]
    (see order_doors_clockwise) so almost every entry after the first is a single direct
    click, not a fresh 4-door search -- expected to keep working for the life of the ~25-minute
    door rotation.

    NEEDS LIVE VERIFICATION: the exact signal that a door was wrong (led to an "abandoned
    room" rather than the Guardian mummy's lobby) and how to leave that room back outside.
    Best-effort assumption implemented below: re-click the same door we just walked through
    (anonymous doors are described as ordinary bidirectional entry points in the wiki) to step
    back outside, then try the next door index next tick. Correct if wrong, per this
    codebase's "ASSUMPTION, NOT YET VERIFIED LIVE" convention (see blast_furnace.py)."""
    doors = find_objects_by_name(controller.get_objects(radius=30), ANONYMOUS_DOOR_NAME)
    if len(doors) < 4:
        debug_log.log("pyramid_plunder", f"only found {len(doors)} anonymous doors -- waiting")
        return
    ordered = order_doors_clockwise(doors)

    cached_index = ctx.state.data.get("correct_door_index")
    attempt_index = ctx.state.data.get("door_attempt_index", 0) % len(ordered)
    index_to_try = cached_index if cached_index is not None else attempt_index
    door = ordered[index_to_try]

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if not controller.click_entity(ctx.ser, door, check_ui_blockers=False):
        return
    # Let the search-the-door attempt (and possible 1-4 dmg stun on failure) resolve before
    # checking whether it worked.
    controller.human_delay(1.5, 3.0)

    mummy = find_npc_by_name(controller.get_npcs(), GUARDIAN_MUMMY_NAME)
    if mummy is not None:
        debug_log.log("pyramid_plunder", f"reached the Guardian mummy via door index {index_to_try}")
        ctx.state.data["correct_door_index"] = index_to_try
        return

    debug_log.log("pyramid_plunder", f"door index {index_to_try} didn't reach the mummy -- "
                                      f"stepping back through it and trying the next one")
    controller.wait_until_player_idle(timeout_s=5.0)
    controller.click_entity(ctx.ser, door, check_ui_blockers=False)
    controller.human_delay(1.0, 2.0)

    if cached_index is not None:
        ctx.state.data["correct_door_index"] = None
    else:
        ctx.state.data["door_attempt_index"] = attempt_index + 1


def lobby_tick(ctx, config, mummy: dict) -> None:
    """Guardian mummy reachable, no run active -- right-click Start-minigame."""
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if controller.click_menu_action(ctx.ser, mummy, "Start-minigame", check_ui_blockers=False):
        debug_log.log("pyramid_plunder", "started a new run")
        controller.human_delay(0.8, 1.5)
```

- [ ] **Step 4: Write `setup()` and `tick()` with a stub `in_room_tick`**

```python
def setup(ctx):
    ctx.state.data.setdefault("room_step", ROOM_STEP_TRAP)


def tick(ctx):
    config = ctx.state.config
    maybe_eat_food(ctx, config)

    pp = controller.get_pyramid_plunder()
    if pp.get("inRun"):
        in_room_tick(ctx, config, pp)
        return

    mummy = find_npc_by_name(controller.get_npcs(), GUARDIAN_MUMMY_NAME)
    if mummy is not None:
        lobby_tick(ctx, config, mummy)
    else:
        outside_tick(ctx, config)


def in_room_tick(ctx, config, pp: dict) -> None:
    # Filled in by Task 3.
    pass
```

- [ ] **Step 5: Syntax check**

Run (from `ControllerAddon/`): `python -m py_compile routines/pyramid_plunder.py`
Expected: no output (success).

- [ ] **Step 6: Regression check**

Run (from `ControllerAddon/`): `python -m pytest tests/ -v`
Expected: all existing tests still pass (new file, no shared code touched).

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/routines/pyramid_plunder.py
git commit -m "Add Pyramid Plunder routine: config, helpers, outside/lobby entry loop"
```

---

### Task 3: `routines/pyramid_plunder.py` — the in-room loot loop

**Files:**
- Modify: `ControllerAddon/routines/pyramid_plunder.py` (replace the Task 2 stub `in_room_tick`, add its sub-steps and loot-drop)

**Interfaces:**
- Consumes: everything from Task 2 (constants, `find_object_by_id`, `ROOM_STEP_*`), plus `controller.get_inventory()`, `controller.get_last_chat_message() -> {"tick": int, "message": str}`, `controller.wait_for_chat_message(predicate, since_tick=None, timeout_s=5.0) -> dict|None`.
- Produces: `in_room_tick(ctx, config, pp)` (replaces the Task 2 stub), `trap_step`, `chest_step`, `sarco_step`, `door_step`, `quick_leave`, `drop_loot` — none consumed outside this file.

- [ ] **Step 1: Replace the stub `in_room_tick` and add the sub-step dispatch**

Replace the Task 2 stub:

```python
def in_room_tick(ctx, config, pp: dict) -> None:
    # Filled in by Task 3.
    pass
```

with:

```python
def in_room_tick(ctx, config, pp: dict) -> None:
    step = ctx.state.data.get("room_step", ROOM_STEP_TRAP)

    if step == ROOM_STEP_TRAP:
        trap_step(ctx, config, pp)
    elif step == ROOM_STEP_CHEST:
        chest_step(ctx, config, pp)
    elif step == ROOM_STEP_SARCO:
        sarco_step(ctx, config, pp)
    elif step == ROOM_STEP_DOOR:
        door_step(ctx, config, pp)

    drop_loot(ctx, config)
```

- [ ] **Step 2: Write `trap_step` and `chest_step`**

```python
def trap_step(ctx, config, pp: dict) -> None:
    """Disarm the spear trap if the bridge reports it still armed.

    NEEDS LIVE VERIFICATION: whether this is actually mandatory to reach the chest/
    sarcophagus/doors at all, or just an optional bonus-XP obstacle -- see design doc. Either
    way, disarming it first is a safe default (10 xp, minor 1-4 dmg risk on a failed attempt)."""
    if not pp.get("trapActive"):
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        return

    trap = find_object_by_id(controller.get_objects(radius=15), SPEAR_TRAP_ID)
    if trap is None:
        debug_log.log("pyramid_plunder", "trapActive but no spear trap object visible -- skipping")
        ctx.state.data["room_step"] = ROOM_STEP_CHEST
        return

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    controller.click_entity(ctx.ser, trap, check_ui_blockers=False)
    controller.human_delay(0.6, 1.2)
    # Re-polled next tick via pp["trapActive"] -- stays on "trap" until the bridge confirms it's
    # actually disarmed, so a failed attempt naturally retries instead of advancing early.


def chest_step(ctx, config, pp: dict) -> None:
    """Grand Gold Chest always opens instantly on a single search (per the wiki -- no
    fail-and-retry loop needed here, unlike the trap/sarcophagus/doors)."""
    next_step = ROOM_STEP_SARCO if pp.get("roomNumber", 0) >= config["sarcophagusStartRoom"] else ROOM_STEP_DOOR

    chest = find_object_by_id(controller.get_objects(radius=15), GRAND_GOLD_CHEST_CLOSED_ID)
    if chest is None:
        # Already searched (now shows the opened id) or not resolved this poll -- either way
        # there's nothing left to click here.
        ctx.state.data["room_step"] = next_step
        return

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    controller.click_entity(ctx.ser, chest, check_ui_blockers=False)
    controller.human_delay(0.6, 1.5)
    ctx.state.data["room_step"] = next_step
```

- [ ] **Step 3: Write `sarco_step`**

```python
def sarco_step(ctx, config, pp: dict) -> None:
    """Two-stage: click "Open" on the closed sarcophagus, wait for it to become searchable
    (id flips from SARCOPHAGUS_CLOSED_ID to SARCOPHAGUS_OPENED_ID), then click it again to
    Search. No mummy-specific handling beyond the routine's normal food-eat safety net --
    per the design doc, prayer management was explicitly rejected in favor of that.

    NEEDS LIVE VERIFICATION: how long the opening animation actually takes before the id flip
    happens -- the 2.0-4.0s wait below is a first-cut guess, not a measured value."""
    objs = controller.get_objects(radius=15)
    opened = find_object_by_id(objs, SARCOPHAGUS_OPENED_ID)
    if opened is not None:
        if not controller.wait_until_player_idle(timeout_s=5.0):
            return
        controller.click_entity(ctx.ser, opened, check_ui_blockers=False)
        controller.human_delay(1.0, 2.0)
        ctx.state.data["room_step"] = ROOM_STEP_DOOR
        return

    closed = find_object_by_id(objs, SARCOPHAGUS_CLOSED_ID)
    if closed is not None:
        if not controller.wait_until_player_idle(timeout_s=5.0):
            return
        controller.click_entity(ctx.ser, closed, check_ui_blockers=False)
        controller.human_delay(2.0, 4.0)
        return  # re-poll next tick; stays on "sarco" until `opened` is found above

    # Neither id present -- already looted (or genuinely missing this poll). Move on either way
    # rather than getting stuck waiting on something that isn't there.
    ctx.state.data["room_step"] = ROOM_STEP_DOOR
```

- [ ] **Step 4: Write `door_step` and `quick_leave`**

```python
def quick_leave(ctx, config) -> None:
    """Room 6 (or configured lastRoomToLoot) is fully looted -- end the run via the in-room
    exit door's Quick-leave option rather than walking back out through 6 tomb doors. The next
    tick's phase dispatch (OUTSIDE vs LOBBY) picks up wherever the game actually drops the
    player, so no explicit "where did we land" handling is needed here."""
    exit_door = find_object_by_id(controller.get_objects(radius=15), EXIT_DOOR_ID)
    if exit_door is None:
        debug_log.log("pyramid_plunder", "no exit door found in the final room -- waiting")
        return
    if not controller.wait_until_player_idle(timeout_s=5.0):
        return
    if controller.click_menu_action(ctx.ser, exit_door, "Quick-leave", check_ui_blockers=False):
        debug_log.log("pyramid_plunder", f"quick-left room {config['lastRoomToLoot']} -- run complete, restarting")
        ctx.state.data["room_step"] = ROOM_STEP_TRAP
        ctx.state.data["door_attempt"] = 0


def door_step(ctx, config, pp: dict) -> None:
    """Rooms before lastRoomToLoot: find the correct one of the 4 Tomb Doors. Rooms at or past
    lastRoomToLoot: Quick-leave instead (this room's looting is already done by the time
    in_room_tick reaches "door").

    A lockpick in inventory is assumed to passively boost the pick-lock success chance (per the
    wiki: "Having a lockpick will increase the chance") rather than needing a separate
    use-item-on-door click -- NEEDS LIVE VERIFICATION: if that assumption is wrong and an
    explicit "use lockpick on door" step is actually required, this is the function to add it
    to; not dropping the lockpick (it's absent from artefactNames) means it stays available
    either way.

    Outcome detection: wait_for_chat_message for the confirmed dead-end text (Global
    Constraints) means a wrong door; roomNumber incrementing with no dead-end message means the
    right one. Capped implicitly at 4 attempts per room by cycling through the 4 physical wall
    objects (26618-26621) -- the game's own "3 wrong -> 4th always works" rule bounds the
    worst case.

    NEEDS LIVE VERIFICATION: whether Pick-lock and Enter are actually one click or two -- this
    implementation assumes one click resolves both (closed tomb doors have a single default
    action per the wiki infobox)."""
    room = pp.get("roomNumber", 0)
    if room >= config["lastRoomToLoot"]:
        quick_leave(ctx, config)
        return

    objs = controller.get_objects(radius=15)
    doors = [o for o in objs
             if o.get("id") in TOMB_DOOR_WALL_IDS and o.get("impostorId") == TOMB_DOOR_CLOSED_ID
             and o.get("visible", True)]
    if not doors:
        debug_log.log("pyramid_plunder", "no closed tomb doors found -- waiting")
        return
    doors.sort(key=lambda o: o.get("distance", 9999))

    attempt = ctx.state.data.get("door_attempt", 0)
    door = doors[attempt % len(doors)]

    if not controller.wait_until_player_idle(timeout_s=5.0):
        return

    since_tick = controller.get_last_chat_message().get("tick")
    if not controller.click_entity(ctx.ser, door, check_ui_blockers=False):
        return
    controller.human_delay(0.8, 1.6)

    dead_end = controller.wait_for_chat_message(
        lambda m: "leads to a dead end" in (m.get("message") or "").lower(),
        since_tick=since_tick, timeout_s=3.0,
    )
    if dead_end is not None:
        debug_log.log("pyramid_plunder", f"door attempt {attempt} is a dead end -- trying the next one")
        ctx.state.data["door_attempt"] = attempt + 1
        return

    fresh_pp = controller.get_pyramid_plunder()
    if fresh_pp.get("roomNumber", room) > room:
        debug_log.log("pyramid_plunder", f"advanced to room {fresh_pp.get('roomNumber')}")
        ctx.state.data["room_step"] = ROOM_STEP_TRAP
        ctx.state.data["door_attempt"] = 0
        return

    # Neither signal fired yet (still picking the lock) -- stay on this door and retry next tick.
```

- [ ] **Step 5: Write `drop_loot`**

```python
def drop_loot(ctx, config) -> None:
    """Drop every inventory item whose name is in the fixed artefactNames list -- never a
    substring match, so food/lockpick are never at risk. One drop per tick (not a burst) --
    next tick re-scans and picks up whatever's left, same pacing idiom as this codebase's
    other per-tick inventory actions. Left-click-to-drop resolves via controller.py's
    click_menu_action underneath click_entity's own UI-blocker-free inventory path, same as
    shipwreck_salvaging.py's drop_salvage.

    Finding the Pharaoh's sceptre is logged, never dropped (it's deliberately absent from
    artefactNames) -- the game's own auto-eject on finding it is what actually ends the run;
    this routine does nothing special beyond logging it."""
    names = {n.lower() for n in config.get("artefactNames", [])}
    for item in controller.get_inventory():
        item_name = (item.get("name") or "").lower()
        if item_name in names:
            controller.click_entity(ctx.ser, item, check_ui_blockers=False)
            controller.human_delay(0.2, 0.5)
            return
        if item_name == "pharaoh's sceptre" and not ctx.state.data.get("sceptre_logged"):
            debug_log.log("pyramid_plunder", "PHARAOH'S SCEPTRE FOUND -- letting the game's own auto-eject take over")
            ctx.state.data["sceptre_logged"] = True
```

- [ ] **Step 6: Syntax check**

Run (from `ControllerAddon/`): `python -m py_compile routines/pyramid_plunder.py`
Expected: no output (success).

- [ ] **Step 7: Regression check**

Run (from `ControllerAddon/`): `python -m pytest tests/ -v`
Expected: all existing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add ControllerAddon/routines/pyramid_plunder.py
git commit -m "Add Pyramid Plunder in-room loot loop: trap, chest, sarcophagus, doors, quick-leave"
```

---

### Task 4: Wiring + staged live verification

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `ControllerAddon/controller.py` (`ROUTINE_FILES`)

**Interfaces:**
- Consumes: `routines/pyramid_plunder.py` (Tasks 2-3), `PyramidPlunderProvider` (Task 1).
- Produces: nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Add the `RoutineChoice` enum entry**

In `src/main/java/com/example/controller/RoutineChoice.java`, add to the enum body (alongside `PEST_CONTROL`/`NMZ`):

```java
	PYRAMID_PLUNDER("Pyramid Plunder"),
```

- [ ] **Step 2: Add the `ROUTINE_FILES` mapping**

In `ControllerAddon/controller.py`'s `ROUTINE_FILES` dict, add:

```python
    "PYRAMID_PLUNDER": "routines/pyramid_plunder.py",
```

- [ ] **Step 3: Compile check**

Run: `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 4: Syntax + regression check**

Run (from `ControllerAddon/`):
```bash
python -m py_compile controller.py routines/pyramid_plunder.py
python -m pytest tests/ -v
```
Expected: no py_compile output, all pytest tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/controller/RoutineChoice.java ControllerAddon/controller.py
git commit -m "Wire Pyramid Plunder into RoutineChoice and ROUTINE_FILES"
```

- [ ] **Step 6: Launch the client and confirm the routine is selectable**

Run: `./gradlew.bat run` (launches via `ExamplePluginTest.main`, loads the Controller plugin).
In the RuneLite Controller Settings panel, confirm "Pyramid Plunder" now appears in the Routine dropdown. (Per CLAUDE.md, this requires the client restart that just happened — a stale running client won't show it.)

- [ ] **Step 7: Manual pre-checks before the first live run (per the design doc's Testing section)**

Standing in front of the pyramid's 4 anonymous doors, and separately inside a room at a known-wrong tomb door:
1. Manually search a wrong anonymous door once and observe what actually happens (message text, whether an "abandoned room" is a real distinct area, how you get back outside) — compare against `outside_tick`'s assumption in Task 2 Step 3 and adjust if it doesn't match.
2. Manually pick a known-wrong tomb door once and confirm the exact chat text/casing matches the Global Constraints' `"this door leads to a dead end."` substring, and note whether Pick-lock and Enter are one click or two.

Fix any mismatches found in `routines/pyramid_plunder.py` directly (small, targeted edits — not a new task), re-run Step 4's checks, and commit each fix separately.

- [ ] **Step 8: First live run — one full entry**

Launch: `python -u controller.py routines/pyramid_plunder.py *> pp_test.log 2>&1` (background, per CLAUDE.md's live-debugging workflow), standing anywhere within sight of the pyramid's 4 anonymous doors.
Watch (via `Read` on the log, 20-90s polling gaps) for: door search attempts, a "reached the Guardian mummy" line, "started a new run" line, and the phase actually landing on `IN_ROOM` (room 1).

- [ ] **Step 9: Second live run — one full room 1-6 cycle**

Let it continue (or relaunch) through a full room 1-6 cycle. Watch for: trap disarm attempts, chest/sarcophagus search log lines, door attempts (including at least one confirmed dead-end log line if one occurs naturally), the room-6 "quick-left ... run complete, restarting" line, and confirm the very next tick's phase dispatch lands back on `OUTSIDE` or `LOBBY` correctly (not stuck).

- [ ] **Step 10: Confirm the door cache survives a restart**

Across 2-3 consecutive restarts, confirm `correct_door_index` (visible via `debug_log`'s state-diff output if `--debug-log` is passed) is reused directly rather than re-searched from scratch each time, before trusting it for an unattended soak run.

## Self-Review Notes

- **Spec coverage**: every Design section item (provider, config, OUTSIDE/LOBBY/IN_ROOM phases, all 4 room sub-steps, quick-leave restart, loot-drop/sceptre handling, food safety, wiring) maps to a task above. All five "NEEDS LIVE VERIFICATION" items from the spec are carried through as inline comments in the task that first touches them, plus re-surfaced as concrete manual checks in Task 4 Steps 7-9.
- **Type consistency checked**: `pp` dict shape (`inRun`/`roomNumber`/`timerTicks`/`trapActive`) matches between Task 1's Java `data.put(...)` calls, Task 1's Python default dict, and every Task 2/3 call site that reads it. `ctx.state.data` keys (`correct_door_index`, `door_attempt_index`, `room_step`, `door_attempt`, `sceptre_logged`) are each written and read under the same name everywhere they appear.
- **No placeholders**: every step contains complete, runnable code; the four "NEEDS LIVE VERIFICATION" spots each carry a real best-effort implementation (not a bare TODO), consistent with this codebase's established convention for unverified new-content assumptions.
