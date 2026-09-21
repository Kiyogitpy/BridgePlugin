# Slayer task loop, sub-project 3: travel, banking, loadouts, restock, loot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between "has a task" and "is killing it": the routine travels to the task's site, carries what the task needs, restocks mid-task and comes back, loots by the user's rules, and does all of it for Aya's 24 tasks and the user's 22 Kuradal FIGHT tasks — end to end, task after task.

**Architecture:** Five phases, each ending in software that runs live and is useful by itself. `slayer_routes.py` (pure) turns a task into an ordered list of `legs`; the orchestrator's new TRAVEL phase runs them one bounded leg per tick. The house is the hub (tablet → POH → fairy ring / jewellery box / pool). `slayer_loadout.py` (pure) turns a task into a bank withdraw list; BANK and RESTOCK are orchestrator phases that reuse `banker`. Loot tiers extend `combat_core` through a hook, not a fork. Kuradal's 22 sites become data last, on top of machinery Aya's tasks have already proven.

**Tech Stack:** Python 3 + pytest from `ControllerAddon/`. Java only in Phase D (two config items) — everything else needs no client rebuild.

**Spec:** `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` §6, §7.5–7.10, §8. **Measured facts OUTRANK the spec:** `docs/slayer-sites/recordings-2026-09-19.md`, `docs/slayer-sites/decisions.md`, `docs/slayer-sites/routes.md`, and the live-run notes in `docs/routine-notes.md` (slayer section).

## How this plan is written, and why (a ruling, recorded up front)

Phase A is specified to the line: tests, code, commands. **Phases B–E are specified by interface, test list, measured facts and live-run gate — their code blocks are written when the phase before them has run live.** On 2026-09-19 every live run changed the facts the code stood on (the spell re-contacts the last NPC; interface 75 is not in the root walk; `/equipment` is a dict; `skipTasks` is a list). Writing 2,000 lines of Phase C–E code against facts the Phase A/B live runs will overturn is the failure this project's own notes warn about ("plan-authored tests were wrong more often than the code"). Each later phase therefore begins with a **"detail the phase" step** that expands it to Phase-A granularity in this same file, using what was measured. That step is part of the plan, not a gap in it.

## Global Constraints

- **The physical Arduino is the only input path.** `controller.click_entity`, `click_menu_action`, `key_tap` only.
- **Outcomes are confirmed by a bridge read, never slept off.** A poll inside a budget is fine.
- **THE invariant: Aya is never contacted while a task is active** (`slayer_state.assert_may_contact`). Nothing in this sub-project contacts a master at all; do not add a path that does.
- **`None` from the bridge means unavailable: do nothing.** Unknown is never zero (bank never seen ≠ bank empty).
- **Anything that spends a resource on repeat has a ceiling** (teleport charges, tablets, restock trips), and every ceiling ends in a safety stop that NAMES what ran out or which leg failed. Being stranded with a task active must stop loudly.
- **One bounded action per tick.** A phase never loops a multi-leg journey inside one `tick`; `legs.run_next` is called once per tick with a cursor in `ctx.state.data`.
- **The phase is DERIVED from live state every tick**; `ctx.state.data` holds only cursors and counters and resets on restart. A restart mid-journey must resume correctly.
- **Test fixtures are built from a REAL GET, not from memory** (the `/equipment`-is-a-dict lesson). Every new test file carries the `_no_live_bridge` autouse fixture from `tests/test_npc_contact.py` (it makes `controller.api_get` raise).
- Do not change `travel.walk_to`, `travel.route_to`, `worldmap.py`. Additive keyword parameters on other shared functions are allowed when every existing caller's behaviour is unchanged and a test pins that.
- Never hand-edit `ControllerAddon/vendor/`. Never edit `src/main/java/net/runelite/client/plugins/m8aq/api/`.
- `controller.py` is loaded twice: no new module-level mutable state in it.
- `py_compile` every touched file; `python -m pytest tests/<file> -q` from `ControllerAddon/`. Whole-suite baseline since 2026-09-21: **GREEN, 0 failures** (those 4 mixology tests were stale, not a bug -- see the ledger); the three tests once called flaky (`test_travel.py::test_the_open_budget_is_capped...`, `test_human_move.py::test_two_flushes...`, one in `test_falconry.py`) did NOT flake in 8 and 3 consecutive runs on 2026-09-21; `test_crossfloor::test_an_impossible_trip_is_none_and_fast` now takes 1.4 s against its 8 s bound (it was ~5 s before dominance pruning), so its margin is 5.7x.
- Another session commits unrelated files on this branch: stage by exact path, never `git add -A`.
- A safety-stopped `controller.py` stays alive holding UDP 50555: kill it before a relaunch.
- Commit after every task; messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Phases and their live-run gates

| Phase | Delivers | Gate before the next phase |
|---|---|---|
| **A. Travel from where we stand** | TRAVEL phase; Aya tasks reached by an OVERWORLD fairy ring or a carried item; per-task required-item check; fight hardening | Cave slimes (or any fairy-ring Aya task) reached and killed unattended; task → next task hand-off seen once |
| **B. The house hub** | tablet → POH; POH fairy ring; ornate jewellery box; pool | One Aya task reached through the house from a bank |
| **C. Bank and loadout** | BANK phase; supply profiles → withdraw list; charges; gear on/off; base-gear persistence; low-stock stop; between-task bank decision | Three Aya tasks in a row with no human touch, including one that needed the bank |
| **D. Restock and loot** | RESTOCK phase; loot tiers 50k / 150k; Java config items | One Kuradal-style fight run out of food on purpose: leaves, restocks, returns, continues |
| **E. Kuradal's 22 sites** | the catalog as data, hub by hub; hand-written entrances; slot and Wilderness tests | Each hub proven by one task before the next hub's entries are trusted |

## File map

| File | Responsibility | Phase |
|---|---|---|
| `ControllerAddon/slayer_tasks.py` | + `required_items`, `missing_items` | A |
| `ControllerAddon/slayer_routes.py` (new) | pure: task + position + carried items → legs, or a reason | A, B, E |
| `ControllerAddon/legs.py`, `travel.py` | fairy leg carries its ring tile/id; (B) POH legs | A, B |
| `ControllerAddon/routines/slayer.py` | TRAVEL, then BANK, RESTOCK phases; fight watch | A, C, D |
| `ControllerAddon/poh.py` (new) | the house: landing, ring, box, pool — measured ids | B |
| `ControllerAddon/slayer_loadout.py` (new) | pure: task → withdraw list, keep list, slot count | C |
| `ControllerAddon/gear.py`, `supplies.py` | base-gear capture/restore; quantity rules | C |
| `ControllerAddon/combat_core.py` | loot hook (additive) | D |
| `src/.../ControllerConfig.java`, `ConfigExporter.java` | `slayerMakeRoomValue`, `slayerMustLootValue`, `slayerMinHouseTeleports` | C, D |
| `ControllerAddon/slayer_catalog.py` (new) | Kuradal `TaskSpec` data | E |

---

# PHASE A — travel from where we stand

Scope, deliberately narrow: the player is ALREADY out in the world (the state after a task completes, or after the user parks the account). Aya's teleport list is tried in the plugin's own order; a teleport is usable in this phase when it is (a) `Fairy ring: XYZ` and an OVERWORLD ring entrance is walkable within `RING_WALK_MAX` tiles, or (b) a carried item this phase knows how to use. Everything else yields a reason string and the existing "go to …" safety stop. The house, the bank and Kuradal are later phases.

Measured inputs: `travel.hop_fairy_ring` is proven live (karambwan); fairy ring AJQ's Confirm text reads **"Cave south of Dorgesh-Kaan"** (travel.py:1461); `transports.load_fairy_rings()` gives `entrances` (a list of `Step`, `origin` = the ring's tile, `object_id` = 29495 for most, 29560 Zanaris) and `exit_for(code)`; `legs.walk` goes through `legs.walk_across`, which relocates an unwalkable goal (a ring's own tile is solid on our map) to the nearest reachable tile.

### Task A1: what a task needs carried

**Files:**
- Modify: `ControllerAddon/slayer_tasks.py` (append)
- Test: `ControllerAddon/tests/test_slayer_tasks.py` (append)

**Interfaces:**
- Consumes: `aya_tasks.get(name).info` — the plugin's own gear note: `"Bring a light source"` (cave bugs, cave slimes), `"Bring Ice coolers"` (lizards), `"Bring a rope"` (skeletons), `"South of the Sourhog Cave"` (goblins: a location hint, not an item).
- Produces: `slayer_tasks.Need(label: str, any_of: tuple[str, ...], quantity: int)`; `required_items(task_name, count) -> list[Need]`; `missing_items(task_name, count, carried: dict[str, int]) -> list[str]` (human-readable, empty = ready). `carried` maps lowercased item name → quantity across inventory and equipment.

- [ ] **Step 1: Append the failing tests**

```python
def test_required_items_come_from_the_plugins_own_gear_notes():
    slimes = st.required_items("Cave slimes", 17)
    assert [n.label for n in slimes] == ["a light source"]
    assert "bullseye lantern" in slimes[0].any_of and slimes[0].quantity == 1
    lizards = st.required_items("Lizards", 20)
    assert lizards[0].label == "ice coolers" and lizards[0].quantity == 25, "one per kill plus five spare"
    assert st.required_items("Skeletons", 15)[0].any_of == ("rope",)
    assert st.required_items("Goblins", 15) == [], "'South of the Sourhog Cave' is a place, not an item"
    assert st.required_items("Bears", 15) == [] and st.required_items("Nechryael", 90) == []


def test_missing_items_names_what_to_fetch_and_accepts_any_alternative():
    assert st.missing_items("Cave slimes", 17, {}) == ["a light source (e.g. bullseye lantern)"]
    assert st.missing_items("Cave slimes", 17, {"bullseye lantern": 1}) == []
    assert st.missing_items("Cave slimes", 17, {"kandarin headgear 3": 1}) == []
    assert st.missing_items("Cave slimes", 17, {"unlit torch": 1}) != [], "an UNLIT source is not a light"
    assert st.missing_items("Lizards", 20, {"ice cooler": 10}) == ["ice coolers x25 (have 10)"]
    assert st.missing_items("Lizards", 20, {"ice cooler": 30}) == []
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest tests/test_slayer_tasks.py -q` → `AttributeError: module 'slayer_tasks' has no attribute 'required_items'`.

- [ ] **Step 3: Append to `ControllerAddon/slayer_tasks.py`**

```python
# ---------------------------------------------------------------- what a task needs carried
#
# From the Turael Skipping registry's own gear note per task (aya_tasks.AyaTask.info). A dark
# cave without a light is not a slow task, it is constant swarm damage -- so a missing item is a
# STOP that names it, checked BEFORE any teleport is spent (phase A); the bank phase (C) will
# fetch it instead once it exists.

from collections import namedtuple

Need = namedtuple("Need", ("label", "any_of", "quantity"))

# LIT sources only, matched as substrings of the lowercased item name. "unlit"/"empty" variants
# share these words, so they are excluded explicitly.
LIGHT_SOURCES = ("bullseye lantern", "oil lantern", "candle lantern", "lit torch", "lit candle",
                 "lit black candle", "mining helmet", "kandarin headgear", "firemaking cape",
                 "max cape", "bruma torch", "abyssal lantern", "emerald lantern", "sapphire lantern")
_NOT_LIT = ("unlit", "empty", "(u)")


def required_items(task_name, count) -> list:
    aya = aya_tasks.get(_key(task_name))
    note = ((aya.info if aya else None) or "").lower()
    if "light source" in note:
        return [Need("a light source", LIGHT_SOURCES, 1)]
    if "ice cooler" in note:
        return [Need("ice coolers", ("ice cooler",), int(count or 0) + 5)]
    if "rope" in note:
        return [Need("a rope", ("rope",), 1)]
    return []


def _have(need, carried) -> int:
    total = 0
    for name, quantity in (carried or {}).items():
        lowered = (name or "").lower()
        if any(bad in lowered for bad in _NOT_LIT):
            continue
        if any(option in lowered for option in need.any_of):
            total += int(quantity or 0)
    return total


def missing_items(task_name, count, carried) -> list:
    out = []
    for need in required_items(task_name, count):
        have = _have(need, carried)
        if have >= need.quantity:
            continue
        out.append(f"{need.label} (e.g. {need.any_of[0]})" if need.quantity == 1
                   else f"{need.label} x{need.quantity} (have {have})")
    return out
```

- [ ] **Step 4: Run to verify they pass** — `python -m py_compile slayer_tasks.py; python -m pytest tests/test_slayer_tasks.py -q` → all pass.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/slayer_tasks.py ControllerAddon/tests/test_slayer_tasks.py
git commit -m "slayer_tasks: what a task needs carried, from the plugin's own gear notes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task A2: the fairy leg knows which ring it means

**Files:**
- Modify: `ControllerAddon/travel.py` (`hop_fairy_ring` gains `ring_ids`), `ControllerAddon/legs.py` (`fairy` builder + the `fairy` branch of `run_next`)
- Test: `ControllerAddon/tests/test_legs.py` (append)

**Interfaces:**
- Consumes: `travel.hop_fairy_ring(ctx, code, expect, ring_tile=None, expect_region=None, log="travel")` — today it looks for object id `FAIRY_RING_ID` (29495) within radius 20.
- Produces: `travel.hop_fairy_ring(..., ring_ids=None)` — `None` keeps today's `(FAIRY_RING_ID,)`; `legs.fairy(code, expect, dest=None, ring_tile=None, ring_id=None)` storing `ring_tile` (list or None) and `ring_id`; `run_next` passes them through.

- [ ] **Step 1: Append the failing tests**

```python
def test_a_fairy_leg_carries_its_ring_tile_and_id_through_to_the_hop(monkeypatch):
    seen = {}

    def hop(ctx, code, expect, **kw):
        seen.update(kw, code=code, expect=expect)
        return True
    monkeypatch.setattr(travel, "hop_fairy_ring", hop)
    leg = legs.fairy("AJQ", "Dorgesh", ring_tile=(2740, 3351, 0), ring_id=29495)
    assert leg["ring_tile"] == [2740, 3351, 0] and leg["ring_id"] == 29495
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert seen["ring_tile"] == {"x": 2740, "y": 3351, "plane": 0} and seen["ring_ids"] == (29495,)


def test_a_plain_fairy_leg_still_calls_the_hop_exactly_as_before(monkeypatch):
    """banker and karambwan build fairy legs with no ring: nothing new may be passed."""
    seen = {}
    monkeypatch.setattr(travel, "hop_fairy_ring", lambda ctx, code, expect, **kw: seen.update(kw) or True)
    assert legs.run_next(_Ctx(), [legs.fairy("cks", "Slayer Tower")], 0) == (legs.DONE, 1)
    assert set(seen) == {"log"}
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest tests/test_legs.py -q` → `TypeError: fairy() got an unexpected keyword argument 'ring_tile'`.

- [ ] **Step 3: Implement**

In `travel.py`, change the signature and the ring lookup of `hop_fairy_ring` (nothing else):
```python
def hop_fairy_ring(ctx, code, expect, ring_tile=None, expect_region=None, log="travel", ring_ids=None):
```
and replace the first statement with:
```python
    wanted = tuple(ring_ids) if ring_ids else (FAIRY_RING_ID,)      # Zanaris is 29560, a POH ring 29228
    ring = next((o for o in controller.get_objects(radius=20)
                 if o.get("id") in wanted and o.get("visible", True)), None)
```
Add to the docstring: `` `ring_ids` object ids that count as the ring; None = the ordinary overworld ring.``

In `legs.py` replace the `fairy` builder and the `fairy` branch:
```python
def fairy(code: str, expect: str, dest=None, ring_tile=None, ring_id=None) -> dict:
    """A fairy-ring hop, delegated to travel.hop_fairy_ring (dialled, never Last-destination).
    `ring_tile`/`ring_id` name WHICH ring when it is not an ordinary one already in view."""
    leg = _base("fairy", f"fairy ring {code}", dest)
    leg["code"] = code
    leg["expect"] = expect
    leg["ring_tile"] = list(ring_tile) if ring_tile else None
    leg["ring_id"] = ring_id
    return leg
```
```python
    if kind == "fairy":
        extra = {}
        tile = leg.get("ring_tile")
        if tile:
            extra["ring_tile"] = {"x": tile[0], "y": tile[1], "plane": tile[2]}
        if leg.get("ring_id"):
            extra["ring_ids"] = (leg["ring_id"],)
        ok = travel.hop_fairy_ring(ctx, leg.get("code"), leg.get("expect"), log=log, **extra)
```
(the rest of the branch unchanged).

- [ ] **Step 4: Run to verify** — `python -m py_compile travel.py legs.py; python -m pytest tests/test_legs.py tests/test_banker.py tests/test_travel.py tests/test_karambwan_fishing.py -q` → all pass (the known-flaky `test_travel` open-budget test aside).

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/travel.py ControllerAddon/legs.py ControllerAddon/tests/test_legs.py
git commit -m "A fairy leg can name which ring it means; plain legs call the hop exactly as before

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task A3: `slayer_routes.py` — a task becomes legs

**Files:**
- Create: `ControllerAddon/slayer_routes.py`
- Test: `ControllerAddon/tests/test_slayer_routes.py`

**Interfaces:**
- Consumes: `aya_tasks.get(name)` (`.teleports` in the plugin's preference order, `.target`), `transports.load_fairy_rings()` (`.entrances: list[Step]`, `.exit_for(code)`), `crossfloor.find_route(start, goal, skill_levels=, max_links=)` → `Route.cost` / `None`, `legs.walk/fairy/item`.
- Produces: `slayer_routes.parse(teleport_text) -> tuple[str, str]` (`("fairy","AJQ")`, `("item","ectophial")`, `("unsupported", text)`); `nearest_ring(player_tile, skill_levels=None) -> Step | None`; `plan(task_name, player, carried, skill_levels=None) -> tuple[list | None, str]` (legs, or `None` + the reason every listed teleport was unusable); constants `RING_WALK_MAX = 150`, `FAIRY_EXPECT`, `ITEMS`.

- [ ] **Step 1: Write the failing tests**

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import slayer_routes as sr


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


def test_the_plugins_teleport_strings_are_parsed_not_guessed():
    assert sr.parse("Fairy ring: AJQ") == ("fairy", "AJQ")
    assert sr.parse("Fairy ring: blr") == ("fairy", "BLR")
    assert sr.parse("Ectophial: Empty") == ("item", "ectophial")
    assert sr.parse("Ornate Jewellery Box: Cooking Guild")[0] == "unsupported", "the house is phase B"
    assert sr.parse("Slayer Ring: Morytania Slayer Tower (Option 2)")[0] == "unsupported", "paged dialogue: phase B"
    assert sr.parse("")[0] == "unsupported"


def test_the_nearest_ring_is_measured_by_walking_not_by_the_crow():
    """From the bears site south-west of the Legends' Guild the BLR ring is a ~45 tile walk."""
    ring = sr.nearest_ring((2700, 3321, 0))
    assert ring is not None and ring.origin == (2740, 3351, 0)
    assert sr.nearest_ring((3221, 9600, 0)) is None, "no overworld ring is walkable from a random cave tile"


def test_cave_slimes_from_the_bears_site_is_walk_ring_walk():
    route, why = sr.plan("Cave slimes", {"x": 2700, "y": 3321, "plane": 0}, carried={})
    assert why == "" and [leg["kind"] for leg in route] == ["walk", "fairy", "walk"]
    walk_in, hop, walk_out = route
    assert walk_in["dest"] == [2740, 3351, 0]
    assert hop["code"] == "AJQ" and hop["expect"] == "Dorgesh" and hop["dest"] == [2735, 5221, 0]
    assert hop["ring_tile"] == [2740, 3351, 0] and hop["ring_id"] == 29495
    assert walk_out["dest"] == [2730, 5234, 0]


def test_a_carried_item_teleport_is_used_when_the_task_lists_one():
    route, why = sr.plan("Zombies", {"x": 3200, "y": 3200, "plane": 0}, carried={"ectophial": 1})
    assert why == "" and [leg["kind"] for leg in route] == ["item", "walk"]
    assert route[0]["item"] == "ectophial" and route[0]["option"] == "Empty"
    no_item, why = sr.plan("Zombies", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert no_item is None and "Ectophial" in why


def test_every_unusable_teleport_is_named_in_the_reason():
    route, why = sr.plan("Minotaurs", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None
    assert "Ornate Jewellery Box" in why and "Skull sceptre" in why


def test_a_task_with_no_site_has_no_plan():
    route, why = sr.plan("Nechryael", {"x": 3200, "y": 3200, "plane": 0}, carried={})
    assert route is None and "catalog" in why
    assert sr.plan("Cave slimes", {}, carried={}) == (None, "the player's position is unknown")


def test_an_unverified_ring_code_still_plans_because_the_dial_refuses_a_wrong_name():
    """hop_fairy_ring safety-stops when the Confirm text does not contain `expect`, BEFORE it
    teleports -- so a wrong guess in FAIRY_EXPECT costs a stop, never a trip to the wrong place."""
    for code in ("AJQ", "AJR", "BLR", "DLQ"):
        assert sr.FAIRY_EXPECT[code]
```

- [ ] **Step 2: Run to verify it fails** — `ModuleNotFoundError: No module named 'slayer_routes'`.

- [ ] **Step 3: Write `ControllerAddon/slayer_routes.py`**

```python
# slayer_routes.py -- a slayer task becomes an ordered list of legs.py legs. PURE: it reads the
# vendored data and the offline collision map, never the bridge; the caller hands in the
# player's tile and what is carried. Added 2026-09-19 (spec §6, §7.6), PHASE A of
# docs/superpowers/plans/2026-09-19-slayer-travel-bank-loadout.md.
#
# Phase A plans from WHERE THE PLAYER STANDS: Aya's teleports are tried in the Turael Skipping
# plugin's own order, and one is usable when it is a fairy ring code and an OVERWORLD ring is a
# walk away, or an item we carry and know how to use. The house (tablet -> POH ring / jewellery
# box) is phase B and Kuradal's sites are phase E; until then plan() answers None plus a reason
# naming every teleport it could not use, which the routine prints in its safety stop.
#
# FAIRY_EXPECT is the substring travel.hop_fairy_ring must find on the Confirm button before it
# teleports. Only AJQ is MEASURED ("Cave south of Dorgesh-Kaan", travel.py). The rest are the
# travel log's place names and are safe to get wrong: a mismatch is a safety stop BEFORE the
# hop, never a trip to the wrong continent.

import aya_tasks
import crossfloor
import legs
import transports

RING_WALK_MAX = 150          # tiles, straight-line pre-filter before the real walk is costed

FAIRY_EXPECT = {
    "AJQ": "Dorgesh",        # MEASURED: "Cave south of Dorgesh-Kaan"
    "AJR": "Rellekka",       # Slayer cave south-east of Rellekka        (unverified)
    "BLR": "Legends",        # Legends' Guild                            (unverified)
    "DLQ": "Nardah",         # Kharidian Desert, north of Nardah         (unverified)
    "BIQ": "Kalphite",       # Near the Kalphite Hive                    (unverified)
    "CKS": "Canifis",        # Morytania: Canifis                        (unverified)
}

# Carried items phase A knows how to use: registry text prefix -> (item name, menu option, landing).
ITEMS = {
    "ectophial": ("ectophial", "Empty", (3660, 3522, 0)),
}


def parse(teleport_text):
    text = (teleport_text or "").strip()
    head, _, tail = text.partition(":")
    key = head.strip().lower()
    if key == "fairy ring" and len(tail.strip()) == 3:
        return "fairy", tail.strip().upper()
    if key in ITEMS:
        return "item", key
    return "unsupported", text


def nearest_ring(player_tile, skill_levels=None):
    """The overworld fairy ring with the shortest WALK from `player_tile`, or None."""
    best = None
    for ring in transports.load_fairy_rings().entrances:
        ox, oy, plane = ring.origin
        if plane != player_tile[2]:
            continue
        if max(abs(ox - player_tile[0]), abs(oy - player_tile[1])) > RING_WALK_MAX:
            continue
        route = crossfloor.find_route(tuple(player_tile), ring.origin, skill_levels=skill_levels, max_links=0)
        if route is not None and (best is None or route.cost < best[0]):
            best = (route.cost, ring)
    return best[1] if best else None


def plan(task_name, player, carried, skill_levels=None):
    """(legs, "") for the first usable teleport, else (None, why)."""
    x, y = (player or {}).get("x"), (player or {}).get("y")
    if x is None or y is None:
        return None, "the player's position is unknown"
    here = (x, y, player.get("plane", 0))
    task = aya_tasks.get(task_name)
    if task is None or not task.target:
        return None, f"{task_name} has no site in the catalog yet"
    site = legs.walk(*task.target)
    reasons = []
    for text in task.teleports:
        kind, value = parse(text)
        if kind == "fairy":
            if value not in FAIRY_EXPECT:
                reasons.append(f"{text}: no Confirm-text check is known for {value}")
                continue
            exit_tile = transports.load_fairy_rings().exit_for(value)
            ring = nearest_ring(here, skill_levels)
            if exit_tile is None or ring is None:
                reasons.append(f"{text}: no fairy ring within a {RING_WALK_MAX}-tile walk of {here}")
                continue
            return [legs.walk(*ring.origin),
                    legs.fairy(value, FAIRY_EXPECT[value], dest=exit_tile,
                               ring_tile=ring.origin, ring_id=ring.object_id),
                    site], ""
        if kind == "item":
            name, option, landing = ITEMS[value]
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            return [legs.item(name, option, landing), site], ""
        reasons.append(f"{text}: not supported until the house/bank phases")
    return None, "; ".join(reasons) or f"{task_name} lists no teleport"
```

- [ ] **Step 4: Run to verify it passes** — `python -m py_compile slayer_routes.py; python -m pytest tests/test_slayer_routes.py -q` → 7 passed, under 20 s (real map).

If `test_the_nearest_ring...` finds a different ring than BLR, print the candidates and their costs and REPORT — do not change the expected tile to fit.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/slayer_routes.py ControllerAddon/tests/test_slayer_routes.py
git commit -m "slayer_routes.py: an Aya task becomes legs from where the player stands

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task A4: the TRAVEL phase

**Files:**
- Modify: `ControllerAddon/routines/slayer.py`
- Test: `ControllerAddon/tests/test_slayer_routine.py` (append; two existing tests change, named below)

**Interfaces:**
- Consumes: `slayer_routes.plan`, `slayer_tasks.missing_items`, `legs.run_next(ctx, legs, cursor, log) -> (status, cursor)` with `legs.DONE/PROGRESS/FAILED/FINISHED`, `legs.skill_levels()`, `gear.carried_names() -> list[str]`, `gear.worn_names() -> list[str]`, `controller.get_inventory()` (items carry `name`, `qty`).
- Produces: phase `TRAVEL`; `derive_phase` returns `TRAVEL` wherever it returned `GO_THERE`; constants `TRAVEL_MAX_FAILS = 3`, `TRAVEL_MAX_PROGRESS = 40`; `ctx.state.data` keys `slayer_route`, `slayer_route_for`, `slayer_leg`, `slayer_leg_fails`, `slayer_leg_progress`.

Behaviour, one bounded action per tick:
1. `missing = slayer_tasks.missing_items(task, count, carried)` → safety stop naming it (before any teleport is spent).
2. No stored route for `(task, streak)` → `plan(...)`. `None` → the existing "go to …" stop, now carrying `why`.
3. `legs.run_next(...)` once. `DONE` → store cursor, zero both counters. `PROGRESS` → count; past `TRAVEL_MAX_PROGRESS` → stop naming the leg. `FAILED` → count; at `TRAVEL_MAX_FAILS` → stop naming the leg. `FINISHED` with the player still off-site → drop the stored route so the next tick re-plans from the new position; a second `FINISHED` in a row → stop ("route finished but the site is not reached").
4. A new task or streak clears the stored route (`_reset_slot_counters`).

- [ ] **Step 1: Append the failing tests** (and update the two named tests)

```python
import legs
import slayer_routes


def _carry(monkeypatch, names=()):
    import gear
    monkeypatch.setattr(gear, "carried_names", lambda: list(names))
    monkeypatch.setattr(gear, "worn_names", lambda: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": n, "qty": 1} for n in names])
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 70})


def test_travel_runs_one_leg_per_tick_and_keeps_its_cursor(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    route = [legs.walk(2740, 3351, 0), legs.fairy("BLR", "Legends"), legs.walk(2700, 3330, 0)]
    planned = []
    monkeypatch.setattr(slayer_routes, "plan", lambda task, player, carried, skill_levels=None: planned.append(task) or (route, ""))
    ran = []
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": ran.append(cursor) or (legs.DONE, cursor + 1))
    ctx = _Ctx()
    routine.tick(ctx)
    routine.tick(ctx)
    assert ran == [0, 1] and planned == ["bears"], "planned once, then the cursor advances a leg per tick"
    assert ctx.state.stops == []


def test_a_missing_required_item_stops_before_any_teleport(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Cave slimes", count=17))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2700, "y": 3321, "plane": 0})
    _carry(monkeypatch, names=["Teleport to house"])
    ran = []
    monkeypatch.setattr(legs, "run_next", lambda *a, **k: ran.append(1) or (legs.DONE, 1))
    ctx = _Ctx()
    routine.tick(ctx)
    assert ran == [] and "light source" in ctx.state.stops[0]


def test_a_leg_that_keeps_failing_is_a_stop_that_names_the_leg(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: ([legs.fairy("BLR", "Legends")], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.FAILED, cursor))
    ctx = _Ctx()
    for _ in range(routine.TRAVEL_MAX_FAILS + 1):
        routine.tick(ctx)
    assert ctx.state.stops and "fairy ring BLR" in ctx.state.stops[0]


def test_endless_progress_is_bounded(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: ([legs.fairy("BLR", "Legends")], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.PROGRESS, cursor))
    ctx = _Ctx()
    for _ in range(routine.TRAVEL_MAX_PROGRESS + 2):
        routine.tick(ctx)
    assert ctx.state.stops and "fairy ring BLR" in ctx.state.stops[0]


def test_a_finished_route_replans_once_then_stops(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Bears", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    plans = []
    monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: plans.append(1) or ([legs.walk(1, 1, 0)], ""))
    monkeypatch.setattr(legs, "run_next", lambda ctx, legs_, cursor, log="travel": (legs.FINISHED, cursor))
    ctx = _Ctx()
    for _ in range(4):
        routine.tick(ctx)
    assert len(plans) == 2 and ctx.state.stops and "not reached" in ctx.state.stops[0]


def test_no_usable_teleport_stops_with_the_reason(monkeypatch):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Minotaurs", count=15))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    _carry(monkeypatch)
    ctx = _Ctx()
    routine.tick(ctx)
    assert "Ornate Jewellery Box" in ctx.state.stops[0] and "minotaurs" in ctx.state.stops[0].lower()
```

Update two existing tests in the same file:
- `test_phase_is_derived_from_live_state_alone`: replace both `routine.GO_THERE` expectations with `routine.TRAVEL`.
- `test_away_from_the_site_it_stops_and_says_where_to_go`: it used Bears from (3200,3200), which now PLANS nothing usable only if no ring is near. Make it deterministic: add `_carry(monkeypatch)` and `monkeypatch.setattr(slayer_routes, "plan", lambda *a, **k: (None, "Fairy ring: BLR: no fairy ring within a 150-tile walk"))`, and keep its two assertions (`"bears"` lowercased, `"Legends"`).

- [ ] **Step 2: Run to verify they fail** — `AttributeError: module 'slayer' has no attribute 'TRAVEL'`.

- [ ] **Step 3: Implement in `routines/slayer.py`**

Add imports `import gear`, `import legs`, `import slayer_routes`. Replace the constant line with
```python
ACQUIRE, SKIP, STOP, FIGHT, TRAVEL = "ACQUIRE", "SKIP", "STOP", "FIGHT", "TRAVEL"
GO_THERE = TRAVEL              # the pre-phase-A name; kept so old notes and logs still read

TRAVEL_MAX_FAILS = 3           # a leg that fails three times strands us: stop and NAME it
TRAVEL_MAX_PROGRESS = 40       # legs.run_next's PROGRESS is unbounded by design; the caller bounds it
```
In `derive_phase` the last line becomes `return FIGHT if slayer_tasks.at_site(state.task, player) else TRAVEL`.
In `_reset_slot_counters`, add to the update dict: `"slayer_route": None, "slayer_route_for": None, "slayer_leg": 0, "slayer_leg_fails": 0, "slayer_leg_progress": 0, "slayer_route_finished": 0`.
Add:
```python
def _carried() -> dict:
    """Lowercased item name -> quantity, inventory plus worn. Built from gear.py's own readers so
    the /equipment-is-a-dict shape is handled in exactly one place."""
    counts = {}
    for item in controller.get_inventory() or ():
        name = (item.get("name") or "").lower()
        if name:
            counts[name] = counts.get(name, 0) + int(item.get("qty") or item.get("quantity") or 1)
    for name in gear.worn_names() or ():
        counts[(name or "").lower()] = counts.get((name or "").lower(), 0) + 1
    return counts


def _stranded(ctx, state, why) -> None:
    where = slayer_tasks.where(state.task)
    ctx.state.trigger_safety_stop(
        f"Slayer: task is {state.task} x{state.count} and I cannot get there -- {why}."
        + (f" The site is {where}." if where else ""))


def _travel(ctx, state, player) -> None:
    data = ctx.state.data
    carried = _carried()
    missing = slayer_tasks.missing_items(state.task, state.count, carried)
    if missing:
        ctx.state.trigger_safety_stop(f"Slayer: {state.task} needs {', '.join(missing)} -- not carried")
        return
    key = (state.task, state.streak)
    if data.get("slayer_route_for") != key or not data.get("slayer_route"):
        route, why = slayer_routes.plan(state.task, player, carried, skill_levels=legs.skill_levels())
        if route is None:
            _stranded(ctx, state, why)
            return
        data.update({"slayer_route": route, "slayer_route_for": key, "slayer_leg": 0,
                     "slayer_leg_fails": 0, "slayer_leg_progress": 0})
        debug_log.log(LOG, f"route to {state.task}: " + " -> ".join(leg["display"] for leg in route))
    route, cursor = data["slayer_route"], data.get("slayer_leg", 0)
    status, cursor = legs.run_next(ctx, route, cursor, log=LOG)
    name = route[min(cursor, len(route) - 1)]["display"]
    if status == legs.DONE:
        data.update({"slayer_leg": cursor, "slayer_leg_fails": 0, "slayer_leg_progress": 0,
                     "slayer_route_finished": 0})
    elif status == legs.PROGRESS:
        data["slayer_leg_progress"] = data.get("slayer_leg_progress", 0) + 1
        if data["slayer_leg_progress"] > TRAVEL_MAX_PROGRESS:
            _stranded(ctx, state, f"leg '{name}' made no landing in {TRAVEL_MAX_PROGRESS} ticks")
    elif status == legs.FAILED:
        data["slayer_leg_fails"] = data.get("slayer_leg_fails", 0) + 1
        if data["slayer_leg_fails"] >= TRAVEL_MAX_FAILS:
            _stranded(ctx, state, f"leg '{name}' failed {TRAVEL_MAX_FAILS} times")
    else:                                   # FINISHED, and derive_phase still says we are not there
        data["slayer_route_finished"] = data.get("slayer_route_finished", 0) + 1
        data["slayer_route"] = None
        if data["slayer_route_finished"] >= 2:
            _stranded(ctx, state, "the route finished twice but the site is not reached")
```
In `tick`, replace the `GO_THERE` branch with `elif phase == TRAVEL: _travel(ctx, state, player)` (read `player = controller.get_player()` once, before `derive_phase`, and pass it to both). Delete the old GO_THERE message block. Update the file's header comment: travel now exists for fairy-ring and carried-item Aya routes; house, bank and Kuradal are still to come.

- [ ] **Step 4: Run to verify** — `python -m py_compile routines/slayer.py; python -m pytest tests/test_slayer_routine.py -q` → all pass.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/routines/slayer.py ControllerAddon/tests/test_slayer_routine.py
git commit -m "routines/slayer.py: the TRAVEL phase, one bounded leg per tick, every failure a named stop

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task A5: the fight holds its ground

**Files:**
- Modify: `ControllerAddon/routines/slayer.py` (`_fight`)
- Test: `ControllerAddon/tests/test_slayer_routine.py` (append)

**Interfaces:**
- Consumes: `ctx.state.data["current_target_index"]` — `combat_core`'s own "I have a target" marker (`None` = no target); `aya_tasks.get(task).target`; `legs.walk_across(ctx, goal, log)`.
- Produces: constants `NO_TARGET_TICKS = 30`, `NO_TARGET_WALKS = 3`; a TRIVIAL-task loot rule (`cfg["loot"]["minLootValue"]` raised out of reach so only the panel whitelist is looted).

- [ ] **Step 1: Append the failing tests**

```python
def _fighting(monkeypatch, target_index):
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: ctx.state.data.__setitem__("current_target_index", target_index))


def test_nothing_to_fight_for_long_walks_back_to_the_site_then_stops(monkeypatch):
    """Targets wander and respawn at the site tile; 'stranded with nothing to fight' must not look
    like idling (docs/routine-notes.md)."""
    _fighting(monkeypatch, None)
    walks = []
    monkeypatch.setattr(legs, "walk_across", lambda ctx, goal, log="travel": walks.append(tuple(goal)) or True)
    ctx = _Ctx()
    for _ in range(routine.NO_TARGET_TICKS * routine.NO_TARGET_WALKS + 5):
        routine.tick(ctx)
        if ctx.state.stops:
            break
    assert walks == [(3442, 3542, 0)] * routine.NO_TARGET_WALKS
    assert "banshees" in ctx.state.stops[0].lower() and "nothing to fight" in ctx.state.stops[0]


def test_having_a_target_resets_the_watch(monkeypatch):
    _fighting(monkeypatch, 4412)
    ctx = _Ctx()
    ctx.state.data["slayer_no_target"] = 29
    routine.tick(ctx)
    assert ctx.state.data["slayer_no_target"] == 0


def test_an_aya_task_loots_only_the_whitelist(monkeypatch):
    seen = []
    monkeypatch.setattr(controller, "get_slayer_task", lambda: _task("Banshees"))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3440, "y": 3545, "plane": 0})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(combat_core, "fight_tick", lambda ctx: seen.append(ctx.state.config["loot"]["minLootValue"]))
    ctx = _Ctx()
    ctx.state.config["loot"] = {"minLootValue": 5000, "names": ["Mystic gloves (dark)"]}
    routine.tick(ctx)
    assert seen[0] >= 10 ** 9 and ctx.state.config["loot"]["minLootValue"] == 5000
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** — add the two constants; in `_fight`, after the `bank` override:
```python
    # Aya's monsters drop nothing worth a click and travel dominates a 20-kill task (spec §7.10):
    # only the panel's named whitelist is looted, never "anything over minLootValue".
    cfg["loot"] = {**(cfg.get("loot") or {}), "minLootValue": 10 ** 9}
```
and after the `try/finally`:
```python
    data = ctx.state.data
    if data.get("current_target_index") is not None:
        data["slayer_no_target"] = 0
        return
    data["slayer_no_target"] = data.get("slayer_no_target", 0) + 1
    if data["slayer_no_target"] < NO_TARGET_TICKS:
        return
    data["slayer_no_target"] = 0
    walks = data.get("slayer_no_target_walks", 0) + 1
    data["slayer_no_target_walks"] = walks
    site = aya_tasks.get(state.task)
    if walks > NO_TARGET_WALKS or site is None or not site.target:
        ctx.state.trigger_safety_stop(f"Slayer: nothing to fight for {state.task} after "
                                      f"{NO_TARGET_WALKS} walks back to the site")
        return
    debug_log.log(LOG, f"no target for {NO_TARGET_TICKS} ticks -- walking back to the site tile {site.target}")
    legs.walk_across(ctx, site.target, log=LOG)
```
(`import aya_tasks`; reset `slayer_no_target_walks` in `_reset_slot_counters`.) The test expects exactly `NO_TARGET_WALKS` walks and then a stop: check the `>` against the test before committing.

- [ ] **Step 4: Run** — `python -m pytest tests/test_slayer_routine.py tests/test_combat.py -q` → all pass.

- [ ] **Step 5: Commit** — `git add` the two files; message `routines/slayer.py: a fight with nothing to fight walks back to the site, then stops; Aya tasks loot the whitelist only`.

### Task A6: docs, full suite, and the phase-A live run

- [ ] **Step 1:** `docs/routine-notes.md` slayer section: append a dated paragraph — what TRAVEL does, the `FAIRY_EXPECT` table's unverified rows, the required-items stop, and the live checklist below. `CLAUDE.md` status row: replace "otherwise STOPS and says where to go" with "travels to fairy-ring and carried-item Aya sites from where it stands (phase A); house, bank and Kuradal sites are phases B–E".
- [ ] **Step 2:** full suite → only the baseline failures. Commit docs.
- [ ] **Step 3: LIVE RUN (gate for Phase B).** Pre-flight per CLAUDE.md's live-debugging workflow (focus, no stray controller, `/slayer`). With a fairy-ring Aya task active and its required item carried, launch `python -u controller.py routines/slayer.py *> logs/slayer_travel1.log` and watch, in order: the route line; the walk to the ring; `dialling the fairy ring to AJQ (Dorgesh)`; `fairy ring reads '…' -- confirming`; landing; the walk to the site; the first attack; the count dropping on `/slayer`; at zero, the next ACQUIRE. Record every measured Confirm text into `FAIRY_EXPECT` (flip "unverified"), and every surprise into `docs/routine-notes.md`. One symptom, one fix, relaunch.

---

# PHASE B — the house hub

**Measured (recordings-2026-09-19.md):** `Break` a `Teleport to house` tablet (item 8013): anim 4069→4071, spotanim 678, two LOADING flickers, settled ~4.1 s later **inside at (1922,7051) plane 1**, varbit 6719 = 2 while loading. **Fairy Ring id 29228 at (1963,7044) p1**, **Ornate Jewellery Box id 29156 at (1934,7105) p1** — identical on two visits, both clicked from the landing tile (41 and 54 tiles away) with the game walking there itself. Ring: `Last-destination` observed NOTHING; `Configure` → interface 398 is the same dial as any ring. Box: `Teleport Menu` → interface **590**, destination = dynamic child of `590.5` whose TEXT is `"H: Cooking Guild"` (slot 8) — the letter is its keyboard shortcut. Pool: NOT yet recorded (object name/id/action unknown).

**Settled from open source instead of a recording (USER 2026-09-19: "you should have all this data
if you look into microbot").** Microbot-Hub's BSD slayer script (`SlayerScript.java`, surveyed in
`docs/slayer-sites/microbot-slayer-survey.md`; facts ported, no code and never its input layer):
- **The pool**: object ids `29241` ornate, `29240` fancy, `29239` rejuvenation, `29238`
  revitalisation, `29237` restoration; action **`Drink`**. The user's pool restores prayer and run
  but not HP, i.e. rejuvenation or fancy -- so match ANY of the five by id, and confirm by
  `/player` prayer rising. No recording needed.
- **The cancel**: inside interface 426, `Tasks` -> a control reading `Cancel` -> a control reading
  **`Confirm`** on an overlay in the SAME interface (not a chat menu). `npc_contact.cancel_task`
  now clicks it. The dialogue-driven variant (`Assignment` -> `Cancel current task` -> `Yes`) is
  what a master reached by dialogue offers; keep both.
- Source alone CANNOT settle what is specific to this account or to OUR bridge: the house LAYOUT
  (already measured: ring (1963,7044), box (1934,7105), landing (1922,7051), all plane 1), whether
  interfaces 590 and 426 appear in our `/widgets/dump` root walk (75 did not), and whether
  `/objects` inside the POH serves stable coordinates. Those are one read-only probe each.

**MEASURED LIVE 2026-09-20, player standing in the house with the box menu open** (read-only GETs):
- **TEMPLATE x/y lie about distance inside the house; INSTANCE coordinates are the geometry.**
  Player template (1933,7105) p1 / instance (11669,1449), `instPlane` 0, `inInstance` true, region
  7791. Box template (1934,7105) inst (11670,1449) -- 1 tile away. Pool template (1967,7040) inst
  (11671,1447) -- **2 tiles away**, not 65. Fairy ring template (1964,7043) inst (11668,1444) -- 5
  tiles. A POH's rooms are template chunks laid out anywhere, so the recording's "dist 41 / 54"
  was template arithmetic, not a walk. Every in-house distance uses `instX`/`instY`.
- **`distance` is 2147483647 on EVERY object in the house** (all 3,674). Nothing may sort or filter
  by it there (`banker.find_nearby_bank`-style code would pick at random). `?radius=` still
  filters (radius 10 -> 245 objects).
- Template x/y + id still ADDRESS an object: `/object/at?x=1967&y=7040&plane=1&id=29239` answers
  `found`, `clickable`, `onScreen`. Multi-tile objects appear once per tile in the bulk list.
- Objects and actions: **Ornate Jewellery Box 29156** (impostor 37538) `Teleport Menu / Farming
  Guild / Remove` -- the middle action is the LAST DESTINATION, so a plain left-click may teleport
  there: always take `Teleport Menu` from the menu (the NPC Contact lesson again). **Fairy Ring
  29228** `Zanaris / Configure / Last-destination / Favourites / Remove`. **Pool of Rejuvenation
  29239** `Drink / Upgrade / Remove`. **Portal 4525** (exit) at template (1923,7052). Portal Nexus
  33408 `Teleport / Teleport Menu / ...` also exists. House-location varbit 2187 = 3.
- **Interface 590 IS in `/widgets/dump`'s root walk** (104 entries; unlike 75). Each destination is
  a dynamic child of `590.2`..`590.7` (one container per jewellery type) whose **ACTION is the
  destination's plain name** and whose text carries the keyboard shortcut: `1` Emir's Arena, `2`
  Castle Wars, `3` Ferox Enclave, `4` Fortis Colosseum (**struck through `<str>` = locked**), `5`
  Burthorpe, `6` Barbarian Outpost, `7` Corporeal Beast, `8` Tears of Guthix, `9` Wintertodt Camp,
  `A` Warriors' Guild, `B` Champions' Guild, `C` Monastery, `D` Ranging Guild, `E` Fishing Guild,
  `F` Mining Guild, `G` Crafting Guild, `H` Cooking Guild, `I` Woodcutting Guild, `J` Farming
  Guild, `K` Miscellania, `L` Grand Exchange, `M` Falador Park, `N` Dondakan's Rock, `O`
  Edgeville, `P` Karamja, `Q` Draynor Village, `R` Al Kharid. So a box leg = menu `Teleport Menu`
  -> find the entry whose action equals the destination and whose text has no `<str>` -> press
  its KEY (or click it) -> `await_arrival`. No slot numbers.

**Open facts to measure at the start of the phase (the "detail the phase" step) -- (1), (2) and
(3) are now ANSWERED above; (4) is answered too: the ring is 5 instance tiles from the box, so
"click from afar" is simply an ordinary nearby click:** (1) are `/objects` coordinates inside the POH stable template coordinates or instance coordinates — read `/player` + `/objects?fresh=true` inside the house and compare with the recording; (2) is interface 590 in `/widgets/dump`'s root walk or, like 75, only addressable by id — probe both with the box open; (3) the pool's object (one `recorder.py house-pool` recording); (4) `hop_fairy_ring` sees a ring only within `radius=20` and the POH ring is 41 tiles from the landing — either walk first (is the POH on the collision map? almost certainly NOT: it is an instance) or click from afar the way the human did, which needs the ring in `/objects` at radius ≥ 45 and on screen.

**Tasks (expanded to Phase-A granularity in this file once the four facts above are measured):**
- **B0 Detail the phase.** Measure (1)–(4) read-only against the live client with the user standing in the house; rewrite B1–B5 with code.
- **B1 `poh.py`**: constants above; `in_house(player) -> bool` (region / plane-1 box around the landing); `enter(ctx) -> bool` = tablet leg + `in_house` confirm (budget 8 s; tablets are counted: `supplies.charges` does not apply — count the stack).
- **B2 POH fairy leg**: `legs.fairy(..., ring_tile=poh.RING, ring_id=29228)` runs through `hop_fairy_ring(ring_ids=(29228,))`; the approach is what fact (4) decides. Tests pin that an overworld fairy leg is unchanged.
- **B3 Box leg**: `legs.box(destination)`; click `Teleport Menu` (the second option — `click_menu_action`, since left-click may be a last-destination shortcut exactly as the spell turned out to be); find the entry by TEXT containing the destination within `590.5`'s children (addressed by id per fact (2)); confirm landing with `await_arrival`. Destination → landing table for the entries the catalog uses (Cooking Guild, Burthorpe, Barbarian Outpost, Draynor, Karamja, Ferox/Castle Wars via ring of dueling entries), each flipped to measured as it is first used.
- **B4 Pool**: one click, confirmed by `/player` prayer rising or already full; skipped when prayer and run are full.
- **B5 `slayer_routes.plan` grows the house**: a teleport string the house can serve (`Ornate Jewellery Box: X`, `Fairy ring: XYZ` with no overworld ring near, jewellery the box replaces) becomes `[tablet, pool?, box|poh-ring, walk]` when a tablet is carried. The route choice order stays the plugin's.
- **B6 Live gate**: one Aya task reached through the house.

---

# PHASE C — bank and loadout

**Inputs that exist:** `banker.bank_up(ctx, config)` with `{"enabled", "keepItems", "withdraw": [{"name","quantity"}], "returnToSpot": False, "teleportFallback"}`; nearby bank else ring of dueling → Ferox + the pool there (HP too); `banker.search_and_withdraw`; `bank_memory.load(names=)` (`seen`, `quantity` — None when never seen); `gear.ensure_worn/equip_from_inventory/worn_names`; `supplies.PROFILES/charges/teleports_left/lowest_charge/prayer_doses_needed/prayer_potions_for/fits/doses_carried`.

**Tasks:**
- **C0 Detail the phase** from Phase A/B's live notes (what the inventory really looks like after a task; whether `bank_memory` is populated on this client — its six-item live checklist in CLAUDE.md has never been run and C depends on items 1–4).
- **C1 `slayer_loadout.py` (pure)** (**DONE 2026-09-20**, ahead of C0 at the user's say-so; signature differs slightly from the sketch below -- see the module header and the ledger): `build(task, count, profile, panel_cfg, prayer_level, prayer_bonus, carried, banked) -> Loadout(withdraw, keep, slots, problems)`. Food/potion NAMES come from the panel (`food.name`, `potions.prayerPotion`); COUNTS from the profile (`TRIVIAL` for every Aya task) with `supplies.prayer_potions_for` over the drain formula; `+ required_items` (A1) `+ the route's first-leg item (lowest-charge copy first) + a tablet floor + an escape ring of dueling`. `problems` lists what neither inventory nor bank holds. Tests: the user's two anchor loadouts (nechryael 15 food / 6 potions; an Aya task 3 food / 0), 28-slot refusal, unknown bank ≠ empty bank.
- **C2 BANK phase**: derive `BANK` when `loadout.problems == []` but the inventory does not yet satisfy it, or free slots after packing < `LOOT_RESERVE_SLOTS` (4). Runs `banker.bank_up` with the built config and `returnToSpot=False`; re-derive; a loadout still unmet after one full bank visit → stop naming the item (never loop). A SKIP-able task short of an item → SKIP; a staple short (`slayerMinHouseTeleports`, default 5) → stop with the list to buy. **It never buys anything.**
- **C3 Gear on/off**: base loadout captured at setup and after completion ONLY when no gear task is active; persisted to `~/.runelite/bridge-slayer/<accountHash>-base-gear.json`; task gear is a per-slot override; displaced base items are KEPT in the inventory; restored on `count → 0` before anything else, confirmed on `/equipment`. Tests include the restart-mid-gear-task case.
- **C4 Java**: `slayerMinHouseTeleports` (5) in the `tasks` block; exporter; rebuild note.
- **C5 Live gate**: three Aya tasks in a row, one of which needs the bank.

---

# PHASE D — restock and loot

**Tasks:**
- **D0 Detail the phase** from Phase C's live notes (real escape timings under attack; what `needs_banking` reports at the moment food runs out).
- **D1 RESTOCK phase**: before each FIGHT tick, `combat_core.out_of_critical_supplies(ctx)` → `RESTOCK`: clear the target, leave by the escape ring of dueling, bank to the SAME profile, then §7.1 derives TRAVEL and the task's own route runs again. `RESTOCK_MAX_PER_TASK = 4` → stop naming the task and what ran out. "Merely full of loot" is NOT a restock: looting pauses and the fight goes on.
- **D2 Loot tiers** as an additive hook in `combat_core.maybe_loot` (`config["loot"]["makeRoom"]` callable or thresholds; absent = today's behaviour, pinned by a test): ≥ `slayerMakeRoomValue` (50,000) eat one food if HP is missing, else leave it; ≥ `slayerMustLootValue` (150,000) eat → finish a 1-dose prayer potion → DROP the cheapest consumable; never gear, a teleport item or a task-required item; drop and pickup each confirmed on `/inventory`. Stackables already held are always taken.
- **D3 Java**: the two thresholds in the `tasks` block; exporter.
- **D4 Live gate**: a fight deliberately started short of food leaves, restocks, returns and continues.

---

# PHASE E — Kuradal's 22 sites

**Inputs:** `docs/slayer-sites/decisions.md` (sites, access, needs, supply profile per task), `kuradal-1a/1b/2a/2b.md` (coordinates), `routes.md` (what `crossfloor` already routes and what it cannot: Karuulm and the Great Conch cave have NO transport row; the Ancient Cavern whirlpool needs varbit 3759; Taverley needs 80 Agility or the dusty key; the Trolls route is unverified).

**Found 2026-09-19 in Microbot's core client** (`docs/slayer-sites/microbot-core-survey.md`, BSD-2,
facts and data only -- never its input layer): its fork of the Shortest Path tables is NEWER and
LARGER than the copy we vendored from the installed jar (transports.tsv 6,077 lines against our
5,634) and **has the Karuulm Slayer Dungeon** -- `Activate;Elevator;34359` (1311,3806/3808,0) ->
(1311/1312,10189,0), the `Climb;Rocks;34544` hops and the `Climb;Steps;34530/34531` floor changes
-- which our copy lacks entirely. It also carries `blocked_edges.tsv`, `dangerous_tiles.tsv`,
`restrictions.tsv`, `quetzals.tsv` and a dusty-key gate row (item 1590) for Taverley. It does NOT
have a gryphon-cave entrance row (only the CJQ ring landing), so that leg is still hand-written.
**Its format differs**: columns `Item IDs / Currency / isMembers / Varplayers`, and the object cell
is `Option;Target;id` with semicolons where ours is `Option Target id`. MEASURED: our loader reads
the file without error but mis-splits every object cell -- so it needs an ADAPTER, not a copy.

**Tasks:**
- **E-pre Vendor Microbot's transport tables through an adapter.** **DONE 2026-09-20** (see the
  ledger's E-pre entry; `tests/test_transports.py` "the Microbot table" block). `vendor/microbot/` holds the
  byte-identical TSVs plus a `VENDORED.md` naming the commit (`8bcc652` on 2026-09-19);
  `transports.load_links` gains a second source read by header NAME with the semicolon object
  cell, merged with ours by `(origin, dest, object id)` so nothing is double-counted. Tests pin
  the Karuulm elevator, that every row our copy has still resolves identically, and re-run
  `tools/slayer_route_report.py` -- Wyrms must route. Also port `Rs2DoorClassifier`'s name and
  action lists (pure strings, broader than ours: barrier, stile, portcullis, archway, fence).
- **E0 Detail the phase.** Decide with the user the per-task gear the research left open (one-handed weapon beside a mirror shield; ranged kit for waterfiends; heat boots for wyrms).
- **E1 `slayer_catalog.py`** (**DONE 2026-09-20**, ahead of the live gates at the user's say-so -- pure data; see the ledger): `TaskSpec(name, site, areas, routes, gear, carry, supplies, targets)` as DATA; `slayer_tasks.at_site/where/target_names` and `slayer_routes.plan` read it for non-Aya tasks. Catalog tests: every FIGHT task has an entry; every route's first leg is a teleport; profile + carry + displaced gear + teleports fit 28 slots; no site with y ≥ 3520 on plane 0 unless tagged `wilderness=True`; every `walk` is `crossfloor`-routable from the previous landing (the routes report, as assertions, for entries marked `verified`).
- **E2–E5 one hub per task, each gated by one live task at that hub before the next hub's entries are trusted:** Stronghold Slayer Cave (5 tasks) → Slayer Tower basement (3) → Catacombs (2) → Fremennik (2); then the singles. Hand-written `legs.from_transport` entrances for Karuulm and the Great Conch cave; `walk_across` gains `varbit_values` for the whirlpool (reads the handful of varbit ids the candidate links name, once per plan).
- **E6 Picker path + CANCEL** get their first supervised live runs here by construction: the first Kuradal task (streak 19) and the first SKIP with ≥ 90 points.

## Self-review against the spec

- §7.6 TRAVEL → A4 (cursor, per-leg ceilings, named stops). §8 legs → A2, B2, B3. §6/§6.1 catalog → A3 (Aya, from the vendored registry) and E1 (Kuradal). §6.2 POH hub → B. §7.5 BANK/RESTOCK → C2, D1. §7.9 supplies/charges/gear restore/low-stock stop → C1–C4. §7.10 loot → A5 (TRIVIAL whitelist), D2–D3. §7.7 no-target watch → A5. §7.8 Wilderness test → E1.
- NOT in this sub-project: the Burthorpe walk-to-Aya fallback (needs B's box leg first — add it to B5 when detailing B); tormented demons / Greater demons (STOP by decision); Grand Exchange buying (never).
- Type consistency (Phase A): `slayer_routes.plan(task_name, player, carried, skill_levels=None)` is called with those names in A4; `legs.fairy(code, expect, dest=None, ring_tile=None, ring_id=None)` is built in A3 exactly as A2 defines it; `slayer_tasks.missing_items(task_name, count, carried)` takes the dict `_carried()` builds.
