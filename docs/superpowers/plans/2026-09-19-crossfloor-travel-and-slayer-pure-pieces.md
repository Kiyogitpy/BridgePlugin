# Cross-floor travel + the slayer loop's pure pieces — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let walking cross floors and enter dungeons by searching over the imported Shortest Path transports, and build every offline-testable piece of the slayer task loop that does not need the live recording session.

**Architecture:** `crossfloor.py` (pure) runs A* over nodes that are tiles, where each expansion is ONE bounded `worldmap.reachable_from` flood and the edges are (a) a walk to the goal and (b) a walk to a usable transport's origin plus the transport. `legs.run_transport` is the single generic executor (walk to origin, click the object by id with the expected option, confirm the landing) and `legs.walk_across` re-plans after every transport from the live position. `supplies.py`, `slayer_state.py` and `aya_tasks.py` are pure data/logic modules for the future orchestrator.

**Tech Stack:** Python 3.13 + pytest, run from `ControllerAddon/`. One Java default change, built with `./gradlew.bat`.

**Spec:** `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` — §8.1 (transports), §7.9 (supplies, charges), §7.2 (`next_master`, the never-contact-Aya-with-a-task guard), §6.1 (Aya's tasks from the Turael Skipping registry), §6.2 (`slayerMaxSkips` default). Decisions: `docs/slayer-sites/decisions.md`.

## Global Constraints

- **The physical Arduino is the only input path.** Clicks go through `controller.click_entity` / `controller.click_menu_action` only.
- **Outcomes are confirmed by a bridge read, never slept off.** A poll interval inside a bounded confirm loop is fine; a bare sleep standing in for an outcome is not.
- **Pure modules stay pure.** `crossfloor.py`, `supplies.py`, `slayer_state.py`, `aya_tasks.py` import no `controller`, make no HTTP call and touch no client state. Only `legs.py` executes.
- **Requirements fail CLOSED** (`transports.usable`): unknown skill levels, quests, varbits or items mean a transport is not usable.
- **Never use the Wilderness ditch or a plain door as a route edge** (`Step.enters_wilderness_ditch`, `Step.kind == transports.DOOR`). Doors belong to `travel.open_door_toward`.
- **Do not change `travel.walk_to`, `travel.route_to` or `worldmap.py`.** They are shared by every routine and live-proven. New behaviour goes in new functions.
- **`controller.py` is loaded twice**; add no module-level mutable state to it.
- **Never hand-edit a file under `ControllerAddon/vendor/`.**
- Tests stay hermetic: every bridge read a test exercises is monkeypatched. Tests that use the real collision map and the vendored tables are allowed (they are local files) and must each finish in under 10 seconds.
- Python tests: `cd ControllerAddon; python -m pytest tests/<file> -q`. Known baseline when running the whole suite: exactly 4 pre-existing failures, all in `tests/test_mastering_mixology.py`; `test_travel.py::test_the_open_budget_is_capped_so_a_stuck_door_still_fails_fast`, `test_human_move.py::test_two_flushes_in_one_flight_keep_emit_log_monotonic` and one test in `test_falconry.py` fail intermittently on unchanged code. Anything else failing is yours.
- `py_compile` every touched Python file before running the suite.
- Commit after every task; messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File map

| File | Responsibility | Task |
|---|---|---|
| `ControllerAddon/crossfloor.py` (new) | pure A* over floods + transports; `find_route` | 1 |
| `ControllerAddon/tests/test_crossfloor.py` (new) | hub routes on the real map, gating, budgets | 1 |
| `ControllerAddon/legs.py` | `await_arrival(radius=)`, `run_transport`, `walk_across`; `walk` legs cross floors | 2, 3 |
| `ControllerAddon/tests/test_legs.py` | executor + walker tests with fakes | 2, 3 |
| `tools/slayer_route_report.py` (new), `docs/slayer-sites/routes.md` (generated) | the routes for the chosen sites, for a human to sanity-check | 4 |
| `ControllerAddon/supplies.py` (new), `tests/test_supplies.py` (new) | charges, profiles, prayer-dose formula, packing | 5 |
| `ControllerAddon/slayer_state.py` (new), `tests/test_slayer_state.py` (new) | `/slayer` wrapper, `next_master`, the Aya guard | 6 |
| `src/main/java/com/example/controller/ControllerConfig.java` | `slayerMaxSkips` default 3 → 8 | 6 |
| `tools/turael_registry_to_json.py` (new), `ControllerAddon/vendor/turael_skipping_tasks.json` (generated), `ControllerAddon/aya_tasks.py` (new), `tests/test_aya_tasks.py` (new) | Aya's task data + the user's overrides | 7 |
| `CLAUDE.md`, the spec, `ControllerAddon/vendor/VENDORED.md` | docs | 8 |

---

### Task 1: `crossfloor.py` — the route search

**Files:**
- Create: `ControllerAddon/crossfloor.py`
- Test: `ControllerAddon/tests/test_crossfloor.py`

**Interfaces:**
- Consumes: `transports.load_links(include_doors=False)`, `transports.load_agility_shortcuts()`, `transports.usable(step, skill_levels=, completed_quests=, varbit_values=, varplayer_values=, owned_item_names=)`, `Step.origin/dest/object_info/enters_wilderness_ditch/kind`; `worldmap.reachable_from(x, y, plane, *, radius, max_nodes) -> {(x, y): steps}`.
- Produces:
  - `crossfloor.Walk(dest: tuple[int,int,int], tiles: int)` — `.dest` is the REACHABLE tile chosen (may differ from the requested goal by up to `GOAL_TOLERANCE`).
  - `crossfloor.Link(step: transports.Step, approach: tuple[int,int,int])` — `.approach` is the reachable tile at or beside `step.origin`.
  - `crossfloor.Route(steps: list, cost: int)` with `.links` (list of `Link`).
  - `crossfloor.find_route(start, goal, *, skill_levels=None, completed_quests=None, varbit_values=None, varplayer_values=None, owned_item_names=None, use_agility=True, max_links=MAX_LINKS) -> Route | None`. `start`/`goal` are `(x, y, plane)`. `None` = no route within budget.
  - Constants: `FLOOD_RADIUS = 160`, `FLOOD_NODES = 40_000`, `MAX_LINKS = 4`, `MAX_EXPANSIONS = 40`, `LINK_COST = 6`, `GOAL_TOLERANCE = 12`, `ORIGIN_TOLERANCE = 2`, `PLANE_PENALTY = 40`, `UNDERGROUND_DY = 6400`.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_crossfloor.py`:
```python
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import crossfloor
import transports

# These run against the REAL collision map and the vendored Shortest Path tables: both are local
# files, and the routes below were MEASURED with a spike on 2026-09-19 before this was written.


def _infos(route):
    return [s.step.object_info for s in route.links]


def test_the_stronghold_slayer_cave_is_one_link_from_the_slayer_ring_landing():
    """The table's origin tile (2430,3424) is the cave mouth itself, which our map marks solid.
    Without ORIGIN_TOLERANCE the flood never touches it and the search finds nothing -- the first
    spike failed exactly that way with the entrance two tiles from the start."""
    route = crossfloor.find_route((2431, 3422, 0), (2420, 9785, 0))
    assert route is not None
    assert _infos(route) == ["Enter Cave 26709"]
    assert isinstance(route.steps[0], crossfloor.Walk) and route.steps[0].tiles <= 5
    assert route.steps[-1].dest[2] == 0 and route.steps[-1].dest[1] > 9000


def test_the_slayer_tower_basement_is_the_ladder_and_lands_on_plane_three():
    route = crossfloor.find_route((3421, 3537, 0), (3434, 9938, 3))
    assert route is not None and _infos(route) == ["Climb-down Ladder 30191"]
    assert route.steps[-1].dest[2] == 3


def test_the_catacombs_are_entered_by_the_statue():
    route = crossfloor.find_route((1644, 3673, 0), (1686, 10062, 0))
    assert route is not None and _infos(route) == ["Investigate Statue 27785"]


def test_the_slayer_tower_top_floor_takes_two_staircases():
    route = crossfloor.find_route((3421, 3537, 0), (3432, 3540, 2))
    assert route is not None
    assert [l.step.dest[2] for l in route.links] == [1, 2]
    assert all(l.step.option == "Climb-up" for l in route.links)


def test_a_same_plane_trip_uses_no_links():
    route = crossfloor.find_route((2899, 3553, 0), (2864, 3590, 0))      # Burthorpe -> Death Plateau
    assert route is not None and route.links == []
    assert len(route.steps) == 1 and isinstance(route.steps[0], crossfloor.Walk)


def test_a_cluster_centre_goal_resolves_to_a_reachable_tile_nearby():
    """Site coordinates from research are cluster centres and are often not walkable tiles."""
    route = crossfloor.find_route((2431, 3422, 0), (2420, 9785, 0))
    dest = route.steps[-1].dest
    assert max(abs(dest[0] - 2420), abs(dest[1] - 9785)) <= crossfloor.GOAL_TOLERANCE


def test_an_impossible_trip_is_none_and_fast():
    t0 = time.perf_counter()
    assert crossfloor.find_route((3221, 3218, 0), (3178, 2447, 0)) is None    # Lumbridge -> Great Conch
    assert time.perf_counter() - t0 < 8.0


def test_the_usable_link_set_never_contains_doors_or_the_wilderness_ditch():
    links = crossfloor.usable_links()
    assert links
    assert all(s.kind != transports.DOOR for s in links)
    assert not any(s.enters_wilderness_ditch for s in links)


def test_agility_shortcuts_only_join_the_link_set_when_the_level_is_proven():
    none = crossfloor.usable_links()
    assert not any(s.skills for s in none), "with no levels given, nothing skill-gated is usable"
    high = crossfloor.usable_links(skill_levels={"Agility": 99})
    assert any(s.skills.get("Agility") for s in high)
    off = crossfloor.usable_links(skill_levels={"Agility": 99}, use_agility=False)
    assert len(off) < len(high)


def test_max_links_zero_means_walking_only():
    assert crossfloor.find_route((2431, 3422, 0), (2420, 9785, 0), max_links=0) is None
    assert crossfloor.find_route((2899, 3553, 0), (2864, 3590, 0), max_links=0) is not None


def test_heuristic_folds_the_underground_offset_and_penalises_the_wrong_plane():
    surface = (2430, 3424, 0)
    cave_goal = (2420, 9785, 0)
    assert crossfloor.heuristic(surface, cave_goal) < 100, "y+6400 is the same place underground"
    assert crossfloor.heuristic((3421, 3537, 1), (3421, 3537, 0)) == crossfloor.PLANE_PENALTY
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ControllerAddon; python -m pytest tests/test_crossfloor.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'crossfloor'`.

- [ ] **Step 3: Write `ControllerAddon/crossfloor.py`**

```python
# crossfloor.py -- walking routes that change floor: worldmap's same-plane floods joined by the
# Shortest Path transports (ladders, stairs, cave mouths, trapdoors, agility shortcuts).
# PURE: no HTTP, no client state, no clicking. legs.walk_across executes what this returns.
#
# Added 2026-09-19 (slayer spec §8.1). Every walking primitive we had stays on one plane, and the
# slayer sites are mostly underground. The algorithm below was SPIKED against the real map before
# it was written, and two of its constants exist because the first spike failed without them:
#
# - ORIGIN_TOLERANCE. A transport's Origin is the tile Shortest Path stands on, read off ITS
#   collision map. On OURS the Stronghold Slayer Cave's origin (2430,3424) is the cave mouth and
#   reads solid, so a flood never touches it: the first spike found NO ROUTE to a cave two tiles
#   from the start. An origin counts as reached from any flooded tile within 2 of it.
# - GOAL_TOLERANCE. Site coordinates from research are cluster centres, often not walkable.
#
# A* rather than Dijkstra because uninformed search burns its expansions on the nearest stairs
# (Lumbridge castle, the Gnome Stronghold's trees) before it reaches the one link that matters.
# The heuristic folds OSRS's dungeon convention -- a dungeon is drawn at (x, y + 6400) on plane 0
# -- so the dark hole's landing reads as "next to the swamp-cave goal", and penalises a plane
# mismatch so ladders that climb AWAY from the goal sort last.
#
# Each expansion is ONE bounded worldmap.reachable_from flood, which answers "how far is every
# link origin and the goal from here" at once (the same trick banker's return planner uses).
# MEASURED: hub routes 0.06-0.5s, a hopeless one ~2s.
#
# Known limits, on purpose: no-data reads PASSABLE on the static map, so a flood can leak through
# a coverage hole -- the radius and node caps bound that, and tools/slayer_route_report.py prints
# every chosen site's route for a human to sanity-check once. Quest-gated links stay unusable
# until something supplies completed quests (requirements fail closed).

import heapq
import itertools

import transports
import worldmap

FLOOD_RADIUS = 160
FLOOD_NODES = 40_000
MAX_LINKS = 4
MAX_EXPANSIONS = 40
LINK_COST = 6            # tiles-equivalent for the click, the animation and the load
GOAL_TOLERANCE = 12
ORIGIN_TOLERANCE = 2
PLANE_PENALTY = 40
UNDERGROUND_DY = 6400
_CELL = 64


class Walk:
    __slots__ = ("dest", "tiles")

    def __init__(self, dest, tiles):
        self.dest = dest
        self.tiles = tiles

    def __repr__(self):
        return f"Walk({self.tiles} tiles -> {self.dest})"


class Link:
    __slots__ = ("step", "approach")

    def __init__(self, step, approach):
        self.step = step
        self.approach = approach

    def __repr__(self):
        return f"Link({self.step.object_info!r} {self.step.origin} -> {self.step.dest})"


class Route:
    __slots__ = ("steps", "cost")

    def __init__(self, steps, cost):
        self.steps = list(steps)
        self.cost = cost

    @property
    def links(self):
        return [s for s in self.steps if isinstance(s, Link)]

    def __repr__(self):
        return f"Route(cost={self.cost}, {self.steps})"


def heuristic(tile, goal) -> int:
    dx = abs(tile[0] - goal[0])
    dy = min(abs(tile[1] - goal[1]),
             abs(tile[1] + UNDERGROUND_DY - goal[1]),
             abs(tile[1] - UNDERGROUND_DY - goal[1]))
    return max(dx, dy) + PLANE_PENALTY * abs(tile[2] - goal[2])


def usable_links(skill_levels=None, completed_quests=None, varbit_values=None,
                 varplayer_values=None, owned_item_names=None, use_agility=True) -> list:
    """Every transport this account may take: no doors, no Wilderness ditch, requirements proven."""
    steps = list(transports.load_links(include_doors=False))
    if use_agility:
        steps += transports.load_agility_shortcuts()
    return [s for s in steps
            if not s.enters_wilderness_ditch and s.kind != transports.DOOR
            and transports.usable(s, skill_levels=skill_levels, completed_quests=completed_quests,
                                  varbit_values=varbit_values, varplayer_values=varplayer_values,
                                  owned_item_names=owned_item_names)]


def _index(links):
    index = {}
    for s in links:
        index.setdefault((s.origin[2], s.origin[0] // _CELL, s.origin[1] // _CELL), []).append(s)
    return index


def _candidates(index, tile):
    x, y, plane = tile
    out = []
    for cx in range((x - FLOOD_RADIUS) // _CELL, (x + FLOOD_RADIUS) // _CELL + 1):
        for cy in range((y - FLOOD_RADIUS) // _CELL, (y + FLOOD_RADIUS) // _CELL + 1):
            out.extend(index.get((plane, cx, cy), ()))
    return out


def _nearest(flood, xy, tolerance):
    """(walk distance, tile) for `xy` itself or the closest flooded tile within `tolerance`."""
    d = flood.get(xy)
    if d is not None:
        return d, xy
    best = None
    for dx in range(-tolerance, tolerance + 1):
        for dy in range(-tolerance, tolerance + 1):
            tile = (xy[0] + dx, xy[1] + dy)
            near = flood.get(tile)
            if near is None:
                continue
            key = (max(abs(dx), abs(dy)), near)
            if best is None or key < best[0]:
                best = (key, near, tile)
    return (best[1] + 1, best[2]) if best else (None, None)


def find_route(start, goal, *, skill_levels=None, completed_quests=None, varbit_values=None,
               varplayer_values=None, owned_item_names=None, use_agility=True,
               max_links=MAX_LINKS):
    """Cheapest walk-and-transport route from `start` to within GOAL_TOLERANCE of `goal`, or None."""
    index = _index(usable_links(skill_levels, completed_quests, varbit_values, varplayer_values,
                                owned_item_names, use_agility)) if max_links > 0 else {}
    counter = itertools.count()
    goal_node = ("GOAL",)
    queue = [(heuristic(start, goal), next(counter), 0, tuple(start), ())]
    seen = set()
    expansions = 0

    while queue:
        _, _, cost, tile, steps = heapq.heappop(queue)
        if tile == goal_node:
            return Route(steps, cost)
        if tile in seen:
            continue
        seen.add(tile)
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            return None

        flood = worldmap.reachable_from(tile[0], tile[1], tile[2],
                                        radius=FLOOD_RADIUS, max_nodes=FLOOD_NODES)
        if tile[2] == goal[2]:
            d, at = _nearest(flood, (goal[0], goal[1]), GOAL_TOLERANCE)
            if d is not None:
                total = cost + d
                heapq.heappush(queue, (total, next(counter), total, goal_node,
                                       steps + (Walk((at[0], at[1], goal[2]), d),)))

        if sum(1 for s in steps if isinstance(s, Link)) >= max_links:
            continue

        best = {}
        for step in _candidates(index, tile):
            d, at = _nearest(flood, (step.origin[0], step.origin[1]), ORIGIN_TOLERANCE)
            if d is None:
                continue
            key = (step.object_info, step.dest)
            if key not in best or d < best[key][0]:
                best[key] = (d, at, step)
        for d, at, step in best.values():
            if step.dest in seen:
                continue
            g = cost + d + LINK_COST
            approach = (at[0], at[1], tile[2])
            heapq.heappush(queue, (g + heuristic(step.dest, goal), next(counter), g, step.dest,
                                   steps + (Walk(approach, d), Link(step, approach))))
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m py_compile crossfloor.py; python -m pytest tests/test_crossfloor.py -q`
Expected: 11 passed, in well under 30 seconds.

If `test_the_slayer_tower_top_floor_takes_two_staircases` fails because the search picked a different but valid pair of staircases, that is acceptable ONLY if both links are `Climb-up` and the planes go 1 then 2 — that is all the test asserts. Do not loosen any other test; a failing hub test means the algorithm differs from the spike, so re-check constants.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/crossfloor.py ControllerAddon/tests/test_crossfloor.py
git commit -m "crossfloor.py: A* route search over same-plane floods and Shortest Path transports

Pure. Proven on the real map for the slayer hubs (Stronghold Slayer Cave,
Slayer Tower basement and top floor, Catacombs). Origins and goals are
tolerant because our collision map marks a cave mouth solid and research
coordinates are cluster centres.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `legs.run_transport` — the one generic executor

**Files:**
- Modify: `ControllerAddon/legs.py` (`await_arrival` signature; new constants and two functions)
- Test: `ControllerAddon/tests/test_legs.py`

**Interfaces:**
- Consumes: `transports.Step` (`origin`, `dest`, `option`, `target`, `object_id`, `object_info`); `travel.walk_to(ctx, target, tolerance=..., goal_adjacent=...) -> bool`; `controller.get_player()`, `controller.get_objects(radius=, fresh=)` (list of dicts with `id`, `name`, `x`, `y`, `plane`, `screenX`, `screenY`), `controller.object_refresh_target(x, y, plane, obj_id, radius=)`, `controller.click_entity(ser, entity, **kw)` (returns a falsy value with `.withheld` when the hover check refused), `controller.click_menu_action(ser, entity, option, **kw)`.
- Produces: `legs.await_arrival(dest, budget_s=ITEM_BUDGET_S, radius=ARRIVAL_TILES) -> bool` (new optional `radius`); `legs.find_transport_object(step) -> dict | None`; `legs.run_transport(ctx, step, log="travel") -> bool`; constants `LINK_ARRIVAL_TILES = 3`, `LINK_BUDGET_S = 10.0`, `LINK_OBJECT_RADIUS = 6`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_legs.py`:
```python
import transports


def _step(info="Enter Cave 26709", origin=(2430, 3424, 0), dest=(2429, 9824, 0)):
    return transports.Step(origin, dest, info, {}, "", (), (), (), 0, "")


def _cave_object(**over):
    obj = {"id": 26709, "name": "Cave", "x": 2429, "y": 3425, "plane": 0, "screenX": 500, "screenY": 300}
    obj.update(over)
    return obj


def test_run_transport_walks_clicks_the_object_by_id_and_confirms_the_landing(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2440, "y": 3420, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        calls.append(("walk", target, goal_adjacent)) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [_cave_object(id=1, name="Tree"), _cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id, radius=None, inst=False: "REFRESH")

    def click_entity(ser, entity, **kw):
        calls.append(("click", entity["id"], kw.get("expected_action"), kw.get("refresh_target"), kw.get("simulate_mistakes")))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None:
                        calls.append(("await", tuple(dest), budget_s, radius)) or True)

    assert legs.run_transport(_Ctx(), _step()) is True
    assert calls[0] == ("walk", {"x": 2430, "y": 3424, "plane": 0}, False)
    assert calls[1] == ("click", 26709, "Enter", "REFRESH", False)
    assert calls[2] == ("await", (2429, 9824, 0), legs.LINK_BUDGET_S, legs.LINK_ARRIVAL_TILES)


def test_run_transport_falls_back_to_goal_adjacent_when_the_origin_tile_is_solid(monkeypatch):
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2440, "y": 3420, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        walks.append(goal_adjacent) or goal_adjacent)     # only the adjacent walk succeeds
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None: True)
    assert legs.run_transport(_Ctx(), _step()) is True
    assert walks == [False, True]


def test_run_transport_skips_the_walk_when_already_beside_the_origin(monkeypatch):
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3423, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda *a, **k: walks.append(1) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None: True)
    assert legs.run_transport(_Ctx(), _step()) is True
    assert walks == []


def test_run_transport_uses_the_menu_when_the_left_click_is_withheld(monkeypatch):
    class _Withheld:
        withheld = True

        def __bool__(self):
            return False
    menu = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3423, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: _Withheld())
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: menu.append(option) or True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None: True)
    assert legs.run_transport(_Ctx(), _step()) is True
    assert menu == ["Enter"]


def test_run_transport_fails_cleanly_when_the_object_is_not_there(monkeypatch):
    clicked = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3423, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert legs.run_transport(_Ctx(), _step()) is False
    assert not clicked


def test_an_id_less_row_finds_its_object_by_name_nearest_the_origin(monkeypatch):
    far = {"id": 5, "name": "Staircase", "x": 2810, "y": 2793, "plane": 0, "screenX": 1, "screenY": 1}
    near = {"id": 6, "name": "Staircase", "x": 2795, "y": 2794, "plane": 0, "screenX": 2, "screenY": 2}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [far, near])
    step = _step("Climb-up Staircase", origin=(2795, 2793, 0), dest=(2795, 2797, 1))
    assert step.object_id is None
    assert legs.find_transport_object(step)["id"] == 6


def test_await_arrival_honours_a_tighter_radius(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2435, "y": 9824, "plane": 0})
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((2429, 9824, 0), budget_s=0.6) is True          # default radius 12
    assert legs.await_arrival((2429, 9824, 0), budget_s=0.6, radius=3) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_legs.py -q`
Expected: the 7 new tests fail (`AttributeError: module 'legs' has no attribute 'run_transport'` / unexpected keyword `radius`); the existing legs tests still pass.

- [ ] **Step 3: Implement in `ControllerAddon/legs.py`**

Add below `PICKER_BUDGET_S`:
```python
LINK_ARRIVAL_TILES = 3   # a ladder lands you on (or beside) one tile -- far tighter than a teleport
LINK_BUDGET_S = 10.0     # click -> landed, INCLUDING the last steps onto the object and a region load
LINK_OBJECT_RADIUS = 6   # how far from the origin tile the transport's own object may sit
```

Change `await_arrival`'s signature and its two comparisons (body otherwise unchanged):
```python
def await_arrival(dest, budget_s: float = ITEM_BUDGET_S, radius: int = ARRIVAL_TILES) -> bool:
```
replacing both `<= ARRIVAL_TILES` with `<= radius`.

Append at the end of the file:
```python
# ---------------------------------------------------------------- transports (ladders, caves, stairs)

def find_transport_object(step):
    """The scene object a transport row means: by ID when the row has one, else by NAME, and
    always the match nearest the row's origin tile (a building has many identical staircases)."""
    ox, oy, plane = step.origin
    matches = []
    for obj in controller.get_objects(radius=LINK_OBJECT_RADIUS * 3, fresh=True) or []:
        if obj.get("plane", plane) != plane or obj.get("screenX") is None:
            continue
        if step.object_id is not None:
            if obj.get("id") != step.object_id:
                continue
        elif (obj.get("name") or "").lower() != (step.target or "").lower():
            continue
        distance = max(abs(obj.get("x", 0) - ox), abs(obj.get("y", 0) - oy))
        if distance <= LINK_OBJECT_RADIUS:
            matches.append((distance, obj))
    return min(matches, key=lambda m: m[0])[1] if matches else None


def run_transport(ctx, step, log: str = "travel") -> bool:
    """Take ONE Shortest Path transport: get beside its origin, click its object with the row's
    own menu option, and confirm we landed at its destination. The generic replacement for a
    hand-written ladder/cave/stairs step per site (spec §8.1). UNVERIFIED LIVE as of 2026-09-19.

    The origin may be a tile our map calls solid (a cave mouth), so an exact walk that finds no
    route falls back to a goal-adjacent one. The hover gate is safe here: the walk has finished,
    so the cursor reads the object's action from a standing player (CLAUDE.md warns only against
    gating clicks made WHILE walking up)."""
    ox, oy, plane = step.origin
    player = controller.get_player() or {}
    px, py = player.get("x"), player.get("y")
    beside = (px is not None and player.get("plane", plane) == plane
              and max(abs(px - ox), abs(py - oy)) <= 2)
    if not beside:
        target = {"x": ox, "y": oy, "plane": plane}
        if not (travel.walk_to(ctx, target, goal_adjacent=False)
                or travel.walk_to(ctx, target, goal_adjacent=True)):
            debug_log.log(log, f"couldn't walk to {step.object_info!r} at {step.origin}")
            return False

    obj = find_transport_object(step)
    if obj is None:
        debug_log.log(log, f"{step.object_info!r} not found near {step.origin}")
        return False

    debug_log.log(log, f"transport: {step.object_info} {step.origin} -> {step.dest}")
    refresh = None
    if obj.get("id") is not None:
        refresh = controller.object_refresh_target(obj["x"], obj["y"], plane, obj["id"],
                                                   radius=LINK_OBJECT_RADIUS * 3)
    ok = controller.click_entity(ctx.ser, obj, simulate_mistakes=False, refresh_target=refresh,
                                 expected_action=step.option)
    if not ok and getattr(ok, "withheld", False):
        debug_log.log(log, f"left-click on {step.target} isn't '{step.option}' -- using the menu")
        ok = controller.click_menu_action(ctx.ser, obj, step.option, refresh_target=refresh)
    if not ok:
        return False
    return await_arrival(step.dest, LINK_BUDGET_S, LINK_ARRIVAL_TILES)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m py_compile legs.py; python -m pytest tests/test_legs.py tests/test_banker.py -q`
Expected: all pass (banker's tests still green: `await_arrival`'s new parameter is optional).

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/legs.py ControllerAddon/tests/test_legs.py
git commit -m "legs.run_transport: one generic executor for any Shortest Path transport

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `legs.walk_across` — walking that crosses floors

**Files:**
- Modify: `ControllerAddon/legs.py` (append `walk_across`; change `run_next`'s `walk` branch)
- Test: `ControllerAddon/tests/test_legs.py`

**Interfaces:**
- Consumes: `crossfloor.find_route(start, goal, skill_levels=...) -> Route | None`, `crossfloor.Walk`, `crossfloor.Link`; `legs.run_transport` (Task 2); `travel.walk_to`; `controller.get_player()`, `controller.get_skills()` (`{"Agility": {"level": 70, ...}, ...}`).
- Produces: `legs.skill_levels() -> dict | None` (`{"Agility": 70, ...}`, `None` when the bridge gave nothing); `legs.walk_across(ctx, goal, log="travel") -> bool` where `goal` is `(x, y, plane)`; constant `WALK_ACROSS_MAX_LINKS = 6`. `run_next`'s `walk` kind now calls `walk_across`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_legs.py`:
```python
import crossfloor


def _route(*steps):
    return crossfloor.Route(steps, 0)


def test_walk_across_replans_from_the_live_position_after_every_transport(monkeypatch):
    """The plan is a hint, the player's real tile is the truth: after each transport the route is
    asked for again from wherever we actually landed."""
    cave = _step()
    positions = iter([{"x": 2431, "y": 3422, "plane": 0},      # at the ring landing
                      {"x": 2429, "y": 9824, "plane": 0},      # after the cave
                      {"x": 2420, "y": 9786, "plane": 0}])     # arrived
    current = {}

    def get_player():
        current.update(next(positions, current))
        return dict(current)
    monkeypatch.setattr(controller, "get_player", get_player)
    monkeypatch.setattr(controller, "get_skills", lambda: {"Agility": {"level": 60}})
    asked = []

    def find_route(start, goal, **kw):
        asked.append((start, kw.get("skill_levels")))
        if start[1] < 9000:
            return _route(crossfloor.Walk((2430, 3423, 0), 2), crossfloor.Link(cave, (2430, 3423, 0)),
                          crossfloor.Walk((2420, 9786, 0), 38))
        return _route(crossfloor.Walk((2420, 9786, 0), 38))
    monkeypatch.setattr(crossfloor, "find_route", find_route)
    ran = []
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel": ran.append(step.object_info) or True)
    walks = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        walks.append((target, goal_adjacent)) or True)

    assert legs.walk_across(_Ctx(), (2420, 9785, 0)) is True
    assert ran == ["Enter Cave 26709"]
    assert [a[0] for a in asked] == [(2431, 3422, 0), (2429, 9824, 0)]
    assert asked[0][1] == {"Agility": 60}
    assert walks == [({"x": 2420, "y": 9786, "plane": 0}, False)], "the pre-link walk is run_transport's job"


def test_walk_across_is_false_when_there_is_no_route(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3221, "y": 3218, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw: None)
    assert legs.walk_across(_Ctx(), (3178, 2447, 0)) is False


def test_walk_across_stops_when_a_transport_fails(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3422, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw:
                        _route(crossfloor.Link(_step(), (2430, 3423, 0)), crossfloor.Walk((2420, 9786, 0), 38)))
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel": False)
    assert legs.walk_across(_Ctx(), (2420, 9785, 0)) is False


def test_walk_across_cannot_loop_forever(monkeypatch):
    """A transport that 'succeeds' but never moves us must not spin."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3422, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw:
                        _route(crossfloor.Link(_step(), (2430, 3423, 0)), crossfloor.Walk((2420, 9786, 0), 38)))
    ran = []
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel": ran.append(1) or True)
    assert legs.walk_across(_Ctx(), (2420, 9785, 0)) is False
    assert len(ran) == legs.WALK_ACROSS_MAX_LINKS


def test_skill_levels_is_none_when_the_bridge_gave_nothing(monkeypatch):
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    assert legs.skill_levels() is None
    monkeypatch.setattr(controller, "get_skills", lambda: {"Agility": {"level": 71, "boosted": 74}, "Mining": {"level": 60}})
    assert legs.skill_levels() == {"Agility": 71, "Mining": 60}


def test_a_walk_leg_now_goes_through_walk_across(monkeypatch):
    seen = []
    monkeypatch.setattr(legs, "walk_across", lambda ctx, goal, log="travel": seen.append(tuple(goal)) or True)
    assert legs.run_next(_Ctx(), [legs.walk(3434, 9938, 3)], 0) == (legs.DONE, 1)
    assert seen == [(3434, 9938, 3)]
```

The existing test `test_a_walk_leg_walks_to_the_exact_tile` (it asserts `travel.walk_to` is called directly by a walk leg) is superseded by the last test above: **delete it**.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_legs.py -q`
Expected: the 6 new tests fail with `AttributeError: module 'legs' has no attribute 'walk_across'` / `skill_levels`.

- [ ] **Step 3: Implement**

In `ControllerAddon/legs.py` add `import crossfloor` with the other imports, add the constant `WALK_ACROSS_MAX_LINKS = 6` beside `LINK_OBJECT_RADIUS`, replace `run_next`'s `walk` branch with:
```python
    if kind == "walk":
        dest = leg.get("dest") or []
        ok = len(dest) == 3 and walk_across(ctx, tuple(dest), log=log)
        return (DONE, cursor + 1) if ok else (FAILED, cursor)
```
and append at the end of the file:
```python
def skill_levels():
    """{"Agility": 71, ...} from GET /skills (REAL levels -- a boost that wears off mid-route must
    not unlock a shortcut), or None when the bridge gave nothing, so requirements fail closed."""
    raw = controller.get_skills() or {}
    levels = {name: data.get("level") for name, data in raw.items()
              if isinstance(data, dict) and data.get("level") is not None}
    return levels or None


def walk_across(ctx, goal, log: str = "travel") -> bool:
    """Walk to `goal` (x, y, plane), taking ladders, stairs and cave mouths as needed.

    The route is re-planned from the LIVE position after every transport: a plan is a hint, the
    tile we actually landed on is the truth. A same-plane trip is exactly one travel.walk_to.
    Bounded by WALK_ACROSS_MAX_LINKS so a transport that reports success without moving us
    cannot spin. UNVERIFIED LIVE as of 2026-09-19."""
    levels = skill_levels()
    for _ in range(WALK_ACROSS_MAX_LINKS):
        player = controller.get_player() or {}
        if player.get("x") is None:
            return False
        here = (player["x"], player["y"], player.get("plane", 0))
        route = crossfloor.find_route(here, tuple(goal), skill_levels=levels)
        if route is None:
            debug_log.log(log, f"no route from {here} to {tuple(goal)}")
            return False
        link = next((s for s in route.steps if isinstance(s, crossfloor.Link)), None)
        if link is None:
            dest = route.steps[-1].dest
            return bool(travel.walk_to(ctx, {"x": dest[0], "y": dest[1], "plane": dest[2]},
                                       goal_adjacent=False))
        if not run_transport(ctx, link.step, log=log):
            return False
    debug_log.log(log, f"gave up after {WALK_ACROSS_MAX_LINKS} transports without reaching {tuple(goal)}")
    return False
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m py_compile legs.py; python -m pytest tests/test_legs.py tests/test_banker.py tests/test_crossfloor.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/legs.py ControllerAddon/tests/test_legs.py
git commit -m "legs.walk_across: walking that crosses floors, re-planned after every transport

A walk leg now goes through it, so a catalog route is just 'teleport, then
walk to the site'.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The route report for the chosen sites

**Files:**
- Create: `tools/slayer_route_report.py`
- Create (generated by running it): `docs/slayer-sites/routes.md`

**Interfaces:**
- Consumes: `crossfloor.find_route(start, goal, skill_levels=...)`, `Route.steps`, `crossfloor.Walk`, `crossfloor.Link`.
- Produces: a markdown report. No test: it is a human-facing report whose whole point is to be READ; `NO ROUTE` lines are findings, not failures.

- [ ] **Step 1: Write the tool**

```python
"""Print the walking route from each chosen slayer site's first-leg landing to the site.

    python tools/slayer_route_report.py            # prints, and writes docs/slayer-sites/routes.md

Spec §8.1: the static collision map reads missing data as PASSABLE, so a route can leak through
a hole. This report exists so a human can look at every chosen site's transports ONCE. A
"NO ROUTE" line is a finding (a requirement we could not prove, a gate needing an item, a
coordinate that is wrong), not a bug in this script.

Landings and sites come from docs/slayer-sites/decisions.md and its research files. They are
APPROXIMATE (cluster centres); crossfloor resolves them to the nearest reachable tile.
"""
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "ControllerAddon"))

import crossfloor  # noqa: E402

RING_STRONGHOLD = (2431, 3422, 0)
RING_TOWER = (3421, 3537, 0)
RING_FREMENNIK = (2802, 9999, 0)
XERICS_HEART = (1644, 3673, 0)

# (task, landing name, landing tile, site tile)
SITES = [
    ("Aberrant spectres", "slayer ring: Stronghold Slayer Cave", RING_STRONGHOLD, (2440, 9790, 0)),
    ("Ankou", "slayer ring: Stronghold Slayer Cave", RING_STRONGHOLD, (2470, 9805, 0)),
    ("Bloodveld", "slayer ring: Stronghold Slayer Cave", RING_STRONGHOLD, (2450, 9820, 0)),
    ("Fire giants", "slayer ring: Stronghold Slayer Cave", RING_STRONGHOLD, (2400, 9780, 0)),
    ("Hellhounds", "slayer ring: Stronghold Slayer Cave", RING_STRONGHOLD, (2420, 9785, 0)),
    ("Abyssal demons (basement)", "slayer ring: Slayer Tower", RING_TOWER, (3420, 9965, 3)),
    ("Gargoyles (basement)", "slayer ring: Slayer Tower", RING_TOWER, (3434, 9938, 3)),
    ("Nechryael (basement)", "slayer ring: Slayer Tower", RING_TOWER, (3412, 9962, 3)),
    ("Banshees (Aya)", "slayer ring: Slayer Tower", RING_TOWER, (3442, 3542, 0)),
    ("Crawling hands (Aya)", "slayer ring: Slayer Tower", RING_TOWER, (3419, 3571, 0)),
    ("Basilisks", "slayer ring: Fremennik Slayer Dungeon", RING_FREMENNIK, (2738, 10008, 0)),
    ("Kurask", "slayer ring: Fremennik Slayer Dungeon", RING_FREMENNIK, (2705, 9980, 0)),
    ("Cave crawlers (Aya)", "slayer ring: Fremennik Slayer Dungeon", RING_FREMENNIK, (2795, 9997, 0)),
    ("Dagannoth", "Xeric's talisman: Xeric's Heart", XERICS_HEART, (1665, 9995, 0)),
    ("Dust devils", "Xeric's talisman: Xeric's Heart", XERICS_HEART, (1710, 10020, 0)),
    ("Ghosts (Aya)", "Xeric's talisman: Xeric's Heart", XERICS_HEART, (1693, 10063, 0)),
    ("Black demons", "Taverley teleport", (2893, 3465, 0), (2860, 9835, 0)),
    ("Trolls", "games necklace: Burthorpe", (2899, 3553, 0), (2864, 3590, 0)),
    ("Waterfiends", "games necklace: Barbarian Outpost", (2520, 3571, 0), (1750, 5350, 0)),
    ("Kalphite", "desert amulet 4: Kalphite cave", (3322, 3122, 0), (3309, 9526, 0)),
    ("Wyrms", "Rada's blessing: Mount Karuulm", (1311, 3806, 0), (1265, 10170, 0)),
    ("Suqahs (south-west)", "Moonclan teleport", (2113, 3915, 0), (2085, 3868, 0)),
    ("Mutated zygomites", "fairy ring: Zanaris", (2412, 4434, 0), (2416, 4373, 0)),
    ("Gryphons (eastern cave)", "fairy ring: CJQ", (3178, 2447, 0), (3225, 8860, 0)),
    ("Zombies / Birds / Cows (Aya)", "Ectophial", (3660, 3522, 0), (3630, 3530, 0)),
    ("Minotaurs (Aya)", "skills necklace: Cooking Guild", (3143, 3442, 0), (1875, 5218, 0)),
    ("Scorpions (Aya)", "skills necklace: Mining Guild", (3049, 9763, 0), (3049, 9771, 0)),
]

# Proven levels are unknown offline, so the report is run twice: nothing proven, and everything.
PASSES = [("no requirements proven", None), ("all skills 99", {"Agility": 99, "Mining": 99, "Strength": 99,
                                                                 "Thieving": 99, "Ranged": 99, "Prayer": 99,
                                                                 "Magic": 99, "Firemaking": 99, "Woodcutting": 99})]


def describe(route):
    lines = []
    for step in route.steps:
        if isinstance(step, crossfloor.Walk):
            lines.append(f"walk {step.tiles} tiles to {step.dest}")
        else:
            s = step.step
            gate = f" [needs {s.skills}]" if s.skills else ""
            lines.append(f"**{s.object_info}** {s.origin} -> {s.dest}{gate}")
    return lines


def main():
    out = ["# Slayer site routes", "",
           "Generated by `python tools/slayer_route_report.py`. Landing -> site, from the imported",
           "Shortest Path transports and the offline collision map. **Read each one once**: the map",
           "treats missing data as passable, so an absurd route means a leak, and `NO ROUTE` means a",
           "requirement we could not prove, a gate that needs an item, or a wrong coordinate.", ""]
    for task, landing_name, landing, site in SITES:
        out.append(f"## {task}")
        out.append(f"{landing_name} {landing} -> site {site}")
        previous = None
        for label, levels in PASSES:
            t0 = time.perf_counter()
            route = crossfloor.find_route(landing, site, skill_levels=levels)
            dt = time.perf_counter() - t0
            text = describe(route) if route else ["NO ROUTE"]
            if text == previous:
                out.append(f"- *{label}*: same ({dt:.2f}s)")
                continue
            previous = text
            out.append(f"- *{label}* ({dt:.2f}s, cost {route.cost if route else '-'}):")
            out.extend(f"  - {line}" for line in text)
        out.append("")
    report = "\n".join(out)
    print(report)
    path = os.path.join(REPO, "docs", "slayer-sites", "routes.md")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(report + "\n")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run from the repo root: `python tools/slayer_route_report.py`
Expected: a report prints and `docs/slayer-sites/routes.md` is written; total runtime under 3 minutes. Do NOT edit coordinates to make a `NO ROUTE` line disappear — list every `NO ROUTE` and every route with more than 3 transports or a walk over 400 tiles in your report as findings.

- [ ] **Step 3: Commit**

```powershell
git add tools/slayer_route_report.py docs/slayer-sites/routes.md
git commit -m "Slayer site route report: landing to site for every chosen task, for a human to check once

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `supplies.py` — charges, profiles, the prayer-dose formula, packing

**Files:**
- Create: `ControllerAddon/supplies.py`
- Test: `ControllerAddon/tests/test_supplies.py`

**Interfaces:**
- Produces:
  - `supplies.charges(name: str) -> int | None` — trailing `(n)`; `None` for no suffix or a non-numeric one (`Slayer ring (eternal)`).
  - `supplies.base_name(name: str) -> str` — lowercased, suffix stripped.
  - `supplies.teleports_left(base: str, carried: list[str], banked: list[tuple[str, int]] | None) -> int | None` — sum of charges × quantity; `banked=None` means the bank was never seen: the answer is `None` unless carried charges alone are > 0; an unlimited (suffix-less) copy anywhere returns `supplies.UNLIMITED`.
  - `supplies.lowest_charge(names: list[str], base: str) -> str | None`.
  - `supplies.Supplies(food: int, prayer_potions: int, extras: tuple = ())`; `supplies.PROFILES` with keys `PRAYER`, `PRAYER_PLUS_DAMAGE`, `SAFESPOT`, `TRIVIAL`.
  - `supplies.prayer_doses_needed(kills, prayer_level, prayer_bonus=0, protection=True, seconds_per_kill=18.0, downtime_s=0.0, super_restore=False) -> int` (clamped 1..32).
  - `supplies.prayer_potions_for(profile: Supplies, doses_needed: int, cap: int = 8) -> int` — `max(profile.prayer_potions, ceil(doses/4))` capped, and 0 stays 0.
  - `supplies.slots_needed(profile, prayer_potions, carry_count, displaced_gear, teleport_items) -> int`; `supplies.fits(...) -> bool` (≤ 28).
  - `supplies.doses_carried(names: list[str], potion: str) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_supplies.py`:
```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import supplies


def test_charges_reads_the_trailing_count_in_both_spacings():
    assert supplies.charges("Slayer ring (8)") == 8
    assert supplies.charges("Ring of dueling(3)") == 3
    assert supplies.charges("Games necklace(1)") == 1
    assert supplies.charges("Slayer ring (eternal)") is None, "eternal has no charges to count"
    assert supplies.charges("Ectophial") is None
    assert supplies.charges("Prayer potion(4)") == 4
    assert supplies.charges(None) is None


def test_base_name_strips_the_suffix_and_case():
    assert supplies.base_name("Slayer ring (8)") == "slayer ring"
    assert supplies.base_name("Ring of dueling(3)") == "ring of dueling"
    assert supplies.base_name("Slayer ring (eternal)") == "slayer ring (eternal)"


def test_teleports_left_sums_inventory_and_bank():
    banked = [("Slayer ring (8)", 2), ("Slayer ring (3)", 1), ("Shark", 100)]
    assert supplies.teleports_left("slayer ring", ["Slayer ring (2)"], banked) == 2 + 16 + 3


def test_an_unseen_bank_is_unknown_not_zero():
    assert supplies.teleports_left("slayer ring", [], None) is None
    assert supplies.teleports_left("slayer ring", ["Slayer ring (2)"], None) == 2, "what we carry is still known"
    assert supplies.teleports_left("slayer ring", [], []) == 0, "a SEEN bank with none is a real zero"


def test_an_unlimited_copy_answers_unlimited():
    assert supplies.teleports_left("slayer ring", ["Slayer ring (eternal)"], []) == supplies.UNLIMITED
    assert supplies.teleports_left("ectophial", ["Ectophial"], []) == supplies.UNLIMITED


def test_lowest_charge_is_used_first_so_partly_spent_rings_get_finished():
    names = ["Slayer ring (8)", "Shark", "Slayer ring (2)", "Slayer ring (5)"]
    assert supplies.lowest_charge(names, "slayer ring") == "Slayer ring (2)"
    assert supplies.lowest_charge(["Shark"], "slayer ring") is None


def test_the_users_two_anchor_figures_are_the_profiles():
    """USER 2026-09-19: prayer-tanked tasks want 5-6 full prayer potions; nechryael need at
    least 15 food because death spawns hit through prayer."""
    assert supplies.PROFILES["PRAYER"].prayer_potions == 6
    assert supplies.PROFILES["PRAYER_PLUS_DAMAGE"].food == 15
    assert supplies.PROFILES["PRAYER_PLUS_DAMAGE"].prayer_potions == 6
    assert supplies.PROFILES["TRIVIAL"] == supplies.Supplies(food=3, prayer_potions=0)
    assert supplies.PROFILES["SAFESPOT"] == supplies.Supplies(food=8, prayer_potions=2)


def test_prayer_doses_follow_the_drain_rate_model():
    """Slayer Best in Bank's PotionDoseEstimator (BSD-2): drain = duration * pointsPerMinute /
    resistance, resistance = 60 + 2*prayerBonus, a full bar is credited first, a dose restores
    floor(level/4) + 7 (8 for super restore). Hand-computed:
      20 kills * 18s = 360s; 360 * 58 / 60 = 348 drained; minus level 70 = 278; / (17 + 7 = 24) = 11.58 -> 12."""
    assert supplies.prayer_doses_needed(20, prayer_level=70) == 12
    # +30 prayer bonus doubles resistance: 360*58/120 = 174; -70 = 104; /24 = 4.33 -> 5
    assert supplies.prayer_doses_needed(20, prayer_level=70, prayer_bonus=30) == 5
    # no protection prayer: 38 points/min -> 360*38/60 = 228; -70 = 158; /24 = 6.58 -> 7
    assert supplies.prayer_doses_needed(20, prayer_level=70, protection=False) == 7
    # super restore restores one more per dose: 278 / 25 = 11.12 -> 12
    assert supplies.prayer_doses_needed(20, prayer_level=70, super_restore=True) == 12
    assert supplies.prayer_doses_needed(1, prayer_level=99) == 1, "clamped to at least one dose"
    assert supplies.prayer_doses_needed(200, prayer_level=70) == 32, "clamped to one trip's worth"


def test_potion_count_never_drops_below_the_users_floor_and_is_capped():
    prayer = supplies.PROFILES["PRAYER"]
    assert supplies.prayer_potions_for(prayer, doses_needed=5) == 6, "the user's floor wins over a low estimate"
    assert supplies.prayer_potions_for(prayer, doses_needed=32) == 8, "32 doses = 8 potions, the cap"
    assert supplies.prayer_potions_for(prayer, doses_needed=26) == 7
    assert supplies.prayer_potions_for(supplies.PROFILES["TRIVIAL"], doses_needed=20) == 0, "a no-prayer task stays at zero"


def test_doses_carried_counts_doses_not_potions():
    names = ["Prayer potion(4)", "Prayer potion(4)", "Prayer potion(2)", "Super restore(3)", "Shark"]
    assert supplies.doses_carried(names, "prayer potion") == 10
    assert supplies.doses_carried(names, "super restore") == 3


def test_packing_is_checked_against_28_slots():
    heavy = supplies.PROFILES["PRAYER_PLUS_DAMAGE"]
    assert supplies.slots_needed(heavy, 6, carry_count=0, displaced_gear=0, teleport_items=3) == 15 + 6 + 3
    assert supplies.fits(heavy, 6, carry_count=0, displaced_gear=0, teleport_items=3)
    assert not supplies.fits(heavy, 8, carry_count=3, displaced_gear=2, teleport_items=3), "15+8+3+2+3 = 31"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_supplies.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'supplies'`.

- [ ] **Step 3: Write `ControllerAddon/supplies.py`**

```python
# supplies.py -- what a slayer task needs carried: teleport charges, food, prayer potions, and
# whether it all fits. PURE: callers hand in item names; nothing here reads the bridge.
# Added 2026-09-19, slayer spec §7.9.
#
# Supplies are PER TASK (USER 2026-09-19): "a lot more for tasks that mostly rely on prayer to
# negate damage, 5-6 full prayer pots ... if it's nechryaels there is unavoidable damage, we
# should bring at least 15 food". Those two figures anchor the profiles; the rest are proposals
# recorded per task in docs/slayer-sites/decisions.md.
#
# The prayer-dose estimate is Slayer Best in Bank's drain-rate model (FreeArcanes, BSD-2,
# PotionDoseEstimator.prayerDoses -- ported as a formula, the plugin is not vendored; survey in
# docs/slayer-sites/slayer-best-in-bank-survey.md). It is grounded in game mechanics: a dose
# restores floor(level/4) + 7 (8 for a super restore) and worn Prayer bonus raises "resistance".
# Its FOOD estimate is deliberately NOT ported: it scales with trip length only and knows nothing
# about which tasks hurt, which is exactly what the user's per-task numbers do know.

import math
import re
from collections import namedtuple

INVENTORY_SLOTS = 28
UNLIMITED = 10 ** 9            # an uncharged or eternal copy: never the limiting factor
_SUFFIX = re.compile(r"\s*\((\d+)\)\s*$")

Supplies = namedtuple("Supplies", ("food", "prayer_potions", "extras"), defaults=((),))

PROFILES = {
    "PRAYER": Supplies(food=5, prayer_potions=6),
    "PRAYER_PLUS_DAMAGE": Supplies(food=15, prayer_potions=6),
    "SAFESPOT": Supplies(food=8, prayer_potions=2),
    "TRIVIAL": Supplies(food=3, prayer_potions=0),
}

PROTECTION_POINTS_PER_MIN = 58.0   # a protection prayer plus an offensive one
ORDINARY_POINTS_PER_MIN = 38.0
MIN_DOSES, MAX_DOSES = 1, 32       # one trip's worth; a longer task restocks (spec §7.5)
DOSES_PER_POTION = 4


def charges(name):
    """`Slayer ring (8)` -> 8, `Ring of dueling(3)` -> 3. None when there is no numeric suffix
    (an Ectophial, a `Slayer ring (eternal)`)."""
    m = _SUFFIX.search(name or "")
    return int(m.group(1)) if m else None


def base_name(name) -> str:
    return _SUFFIX.sub("", name or "").strip().lower()


def _is(name, base) -> bool:
    return base_name(name).startswith(base.lower())


def teleports_left(base, carried, banked):
    """Charges of `base` across what we carry/wear (`carried`, names) and the bank (`banked`,
    (name, quantity) pairs, or None when the bank has never been seen). Unknown is None, never 0."""
    total = 0
    for name in carried or ():
        if _is(name, base):
            c = charges(name)
            if c is None:
                return UNLIMITED
            total += c
    if banked is None:
        return total if total > 0 else None
    for name, quantity in banked:
        if _is(name, base):
            c = charges(name)
            if c is None:
                return UNLIMITED
            total += c * max(1, quantity)
    return total


def lowest_charge(names, base):
    """The copy to take/use first, so partly spent rings are finished off rather than stranded."""
    matches = [(charges(n), n) for n in names or () if _is(n, base) and charges(n) is not None]
    return min(matches)[1] if matches else None


def prayer_doses_needed(kills, prayer_level, prayer_bonus=0, protection=True,
                        seconds_per_kill=18.0, downtime_s=0.0, super_restore=False) -> int:
    duration = seconds_per_kill * kills + downtime_s * max(0, kills - 1)
    per_minute = PROTECTION_POINTS_PER_MIN if protection else ORDINARY_POINTS_PER_MIN
    resistance = 60 + 2 * prayer_bonus
    drained = duration * per_minute / resistance
    restore = prayer_level // 4 + (8 if super_restore else 7)
    doses = math.ceil(max(0.0, drained - prayer_level) / restore)
    return max(MIN_DOSES, min(MAX_DOSES, doses))


def prayer_potions_for(profile, doses_needed, cap=8) -> int:
    if profile.prayer_potions == 0:
        return 0
    return min(cap, max(profile.prayer_potions, math.ceil(doses_needed / DOSES_PER_POTION)))


def doses_carried(names, potion) -> int:
    return sum(charges(n) or 0 for n in names or () if _is(n, potion))


def slots_needed(profile, prayer_potions, carry_count, displaced_gear, teleport_items) -> int:
    return profile.food + prayer_potions + len(profile.extras) + carry_count + displaced_gear + teleport_items


def fits(profile, prayer_potions, carry_count, displaced_gear, teleport_items) -> bool:
    return slots_needed(profile, prayer_potions, carry_count, displaced_gear, teleport_items) <= INVENTORY_SLOTS
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m py_compile supplies.py; python -m pytest tests/test_supplies.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/supplies.py ControllerAddon/tests/test_supplies.py
git commit -m "supplies.py: charge counting, per-task profiles, the prayer drain formula, packing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `slayer_state.py` and the `slayerMaxSkips` default

**Files:**
- Create: `ControllerAddon/slayer_state.py`
- Test: `ControllerAddon/tests/test_slayer_state.py`
- Modify: `src/main/java/com/example/controller/ControllerConfig.java` (the `slayerMaxSkips` item)

**Interfaces:**
- Produces:
  - `slayer_state.TaskState` (namedtuple: `count, task, task_id, streak, points, master_id, has_task`) and `slayer_state.from_bridge(payload: dict | None) -> TaskState | None` — `None` in, `None` out; `task` is the lowercased `taskName` or `None` when the key is ABSENT (Gson drops null map values).
  - `slayer_state.next_master(streak: int, tasks_cfg: dict) -> str` — returns the enum NAME from config (`"KURADAL"`/`"AYA"` by default).
  - `slayer_state.StreakGuardError(Exception)`; `slayer_state.assert_may_contact(master: str, state: TaskState)` raises it for a filler master while a task is active.
  - `slayer_state.can_skip(points: int, skips_so_far: int, tasks_cfg: dict) -> tuple[bool, str]`.
  - Constants `CANCEL_COST = 30`, `DEFAULT_MAX_SKIPS = 8`.
- Java: `slayerMaxSkips()` default `8`, `@Range(min = 0, max = 20)`.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_slayer_state.py`:
```python
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import slayer_state

CFG = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10,
       "maxSkips": 8, "pointsReserve": 60}


@pytest.mark.parametrize("streak,master", [
    (0, "AYA"), (8, "AYA"), (9, "KURADAL"), (10, "AYA"), (19, "KURADAL"),
    (48, "AYA"), (49, "KURADAL"), (99, "KURADAL"), (100, "AYA"),
])
def test_the_milestone_master_gets_every_task_whose_completion_is_a_multiple(streak, master):
    """USER: 'do 9 tasks with Aya then swap to Kuradal ... like the 20th task would be Kuradal'.
    `streak` is tasks COMPLETED, so the task about to be taken is number streak + 1."""
    assert slayer_state.next_master(streak, CFG) == master


def test_a_fifty_interval_is_a_settings_change_not_a_rewrite():
    cfg = dict(CFG, milestoneEvery=50, milestoneMaster="KONAR")
    assert slayer_state.next_master(9, cfg) == "AYA"
    assert slayer_state.next_master(49, cfg) == "KONAR"


def test_defaults_apply_when_the_panel_block_is_missing():
    assert slayer_state.next_master(9, {}) == "KURADAL"
    assert slayer_state.next_master(3, None) == "AYA"


def test_from_bridge_keeps_unavailable_distinct_from_no_task():
    assert slayer_state.from_bridge(None) is None, "bridge/endpoint unavailable: the caller does NOTHING"
    idle = slayer_state.from_bridge({"count": 0, "hasTask": False, "streak": 9, "points": 415, "masterId": 2, "taskId": 0})
    assert idle.has_task is False and idle.count == 0 and idle.streak == 9 and idle.task is None


def test_an_undecoded_name_is_absent_on_the_wire_and_reads_as_none():
    """Gson drops null map values, so taskName is simply missing when the DB lookup failed."""
    state = slayer_state.from_bridge({"count": 27, "hasTask": True, "taskId": 4135, "streak": 19, "points": 0, "masterId": 2})
    assert state.has_task and state.task is None and state.task_id == 4135
    named = slayer_state.from_bridge({"count": 27, "hasTask": True, "taskName": "Banshees", "taskId": 4135,
                                      "streak": 19, "points": 0, "masterId": 2})
    assert named.task == "banshees"


def test_aya_is_never_contacted_while_a_task_is_active():
    """The one invariant (spec F2): asking Aya to REPLACE a task resets the streak to zero."""
    active = slayer_state.from_bridge({"count": 12, "hasTask": True, "taskName": "Gargoyles", "streak": 19, "points": 0})
    with pytest.raises(slayer_state.StreakGuardError):
        slayer_state.assert_may_contact("AYA", active)
    idle = slayer_state.from_bridge({"count": 0, "hasTask": False, "streak": 19, "points": 0})
    slayer_state.assert_may_contact("AYA", idle)
    slayer_state.assert_may_contact("KURADAL", active)      # rewards / cancel through the milestone master is fine


def test_skipping_respects_the_points_reserve_and_the_per_slot_ceiling():
    assert slayer_state.can_skip(200, 0, CFG) == (True, "")
    ok, why = slayer_state.can_skip(80, 0, CFG)
    assert not ok and "points" in why, "80 - 30 would leave 50, under the 60 reserve"
    assert slayer_state.can_skip(90, 0, CFG)[0], "90 - 30 = 60, exactly the reserve"
    ok, why = slayer_state.can_skip(500, 8, CFG)
    assert not ok and "8" in why
    assert slayer_state.can_skip(500, 2, {})[0], "defaults: 8 skips, 60 reserve"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_slayer_state.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'slayer_state'`.

- [ ] **Step 3: Write `ControllerAddon/slayer_state.py`**

```python
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
```

- [ ] **Step 4: Change the Java default**

In `src/main/java/com/example/controller/ControllerConfig.java`, in the `slayerMaxSkips` config item: change `@Range(min = 0, max = 10)` to `@Range(min = 0, max = 20)`, change `return 3;` to `return 8;`, and replace its `description` with:
```java
		description = "Consecutive 30-point cancels allowed for one task slot before the routine stops. " +
			"With the chosen skip list about 40% of Kuradal rolls are skips, so 3 in a row is routine; 8 is not.",
```

- [ ] **Step 5: Run everything for this task**

Run from `ControllerAddon/`: `python -m py_compile slayer_state.py; python -m pytest tests/test_slayer_state.py -q` — expected: all pass (16 with the parametrised cases).
Run from the repo root: `./gradlew.bat compileJava --offline -q` — expected: BUILD SUCCESSFUL.

- [ ] **Step 6: Commit**

```powershell
git add ControllerAddon/slayer_state.py ControllerAddon/tests/test_slayer_state.py src/main/java/com/example/controller/ControllerConfig.java
git commit -m "slayer_state.py: next master, the never-contact-Aya-with-a-task guard, skip limits; maxSkips default 8

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Aya's tasks from the Turael Skipping registry

**Files:**
- Create: `tools/turael_registry_to_json.py`
- Create (generated): `ControllerAddon/vendor/turael_skipping_tasks.json`
- Create: `ControllerAddon/aya_tasks.py`
- Test: `ControllerAddon/tests/test_aya_tasks.py`

**Interfaces:**
- Consumes: `docs/slayer-sites/aya-turael-skipping-registry.txt` — blocks of the form
  ```
  ## key=banshees | task=Banshees
  npcIds: [414]
  info: null
  shortestPath: 3442,3542,0
  worldMap: 3442,3542,0 
    loc: Morytania Slayer Tower | teleports=[Slayer Ring: Morytania Slayer Tower (Option 2)] | areas=[x=3431,y=3530,w=22,h=38,p=0] 
  ```
- Produces: `aya_tasks.AyaTask` (namedtuple `key, name, npc_ids, target, location, teleports, areas, info, disposition, note`), `aya_tasks.load() -> dict[str, AyaTask]`, `aya_tasks.get(task_name) -> AyaTask | None` (case-insensitive, accepts the singular/plural keys the registry has), `aya_tasks.contains(task, x, y, plane) -> bool`, constants `FIGHT = "FIGHT"`, `BLOCKED = "BLOCKED"`.

- [ ] **Step 1: Write the converter**

`tools/turael_registry_to_json.py`:
```python
"""Convert the Turael Skipping registry dump into the JSON the controller loads.

    python tools/turael_registry_to_json.py

Input : docs/slayer-sites/aya-turael-skipping-registry.txt -- a reflective dump of the plugin's
        own SlayerTaskRegistry.TURAEL_TASKS (see DumpTuraelSkippingRegistry.java beside it).
Output: ControllerAddon/vendor/turael_skipping_tasks.json
The plugin's data, not a reconstruction. Never hand-edit the JSON: re-dump, then re-run this.
"""
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "docs", "slayer-sites", "aya-turael-skipping-registry.txt")
OUT = os.path.join(REPO, "ControllerAddon", "vendor", "turael_skipping_tasks.json")

_AREA = re.compile(r"\[x=(-?\d+),y=(-?\d+),w=(\d+),h=(\d+),p=(\d+)\]")


def _point(text):
    text = text.strip()
    if not text or text == "null":
        return None
    return [int(v) for v in text.split(",")]


def parse(text):
    tasks = {}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## key="):
            key, _, name = line[len("## key="):].partition(" | task=")
            current = {"key": key.strip(), "name": name.strip(), "npcIds": [], "info": None,
                       "target": None, "worldMap": [], "locations": []}
            tasks[current["key"]] = current
        elif current is None:
            continue
        elif line.startswith("npcIds:"):
            current["npcIds"] = [int(v) for v in re.findall(r"-?\d+", line)]
        elif line.startswith("info:"):
            value = line[len("info:"):].strip()
            current["info"] = None if value == "null" else value
        elif line.startswith("shortestPath:"):
            current["target"] = _point(line[len("shortestPath:"):])
        elif line.startswith("worldMap:"):
            current["worldMap"] = [_point(p) for p in line[len("worldMap:"):].split() if p.strip()]
        elif line.strip().startswith("loc:"):
            name, _, rest = line.strip()[len("loc:"):].partition(" | teleports=")
            teleports_text, _, areas_text = rest.partition(" | areas=")
            teleports = [t.strip() for t in teleports_text.strip().strip("[]").split(", ") if t.strip()]
            areas = [[int(v) for v in m.groups()] for m in _AREA.finditer(areas_text)]
            current["locations"].append({"name": name.strip(), "teleports": teleports, "areas": areas})
    return tasks


def main():
    with open(SRC, encoding="utf-8-sig") as handle:
        tasks = parse(handle.read())
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"source": "turael-skipping Plugin Hub jar, SlayerTaskRegistry.TURAEL_TASKS",
                   "tasks": tasks}, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"{len(tasks)} tasks -> {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate the JSON**

Run from the repo root: `python tools/turael_registry_to_json.py`
Expected: `26 tasks -> ...turael_skipping_tasks.json` (24 Aya tasks; the registry carries both `goblin` and `goblins`, and `rats`).

- [ ] **Step 3: Write the failing tests**

Create `ControllerAddon/tests/test_aya_tasks.py`:
```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aya_tasks

# The 24 tasks Aya/Turael assigns (osrs-wiki Turael/Slayer assignments), as the game names them.
AYA_TASKS = ["Banshees", "Bats", "Bears", "Birds", "Cave bugs", "Cave crawlers", "Cave slimes", "Cows",
             "Crawling hands", "Dogs", "Dwarves", "Ghosts", "Goblins", "Icefiends", "Kalphite",
             "Lizards", "Minotaurs", "Monkeys", "Rats", "Scorpions", "Skeletons", "Spiders",
             "Wolves", "Zombies"]


def test_every_aya_task_name_resolves():
    missing = [t for t in AYA_TASKS if aya_tasks.get(t) is None]
    assert missing == []


def test_lookup_is_case_insensitive_and_tolerates_the_singular_form():
    assert aya_tasks.get("BANSHEES").npc_ids == (414,)
    assert aya_tasks.get("cave slime") is aya_tasks.get("Cave slimes")
    assert aya_tasks.get("crawling hand") is aya_tasks.get("Crawling Hands")
    assert aya_tasks.get("not a task") is None


def test_the_plugins_data_comes_through_verbatim():
    banshees = aya_tasks.get("Banshees")
    assert banshees.target == (3442, 3542, 0)
    assert banshees.location == "Morytania Slayer Tower"
    assert banshees.teleports == ("Slayer Ring: Morytania Slayer Tower (Option 2)",)
    assert banshees.areas == ((3431, 3530, 22, 38, 0),)
    assert aya_tasks.get("Cave bugs").info == "Bring a light source"
    assert aya_tasks.get("Lizards").info == "Bring Ice coolers"


def test_alices_farm_serves_three_tasks_from_one_ectophial():
    farm = [aya_tasks.get(t) for t in ("Zombies", "Birds", "Cows")]
    assert {t.location for t in farm} == {"Alice's Farm, west of the Ectofuntus"}
    assert all(t.teleports == ("Ectophial: Empty",) for t in farm)
    assert 2992 in aya_tasks.get("Cows").npc_ids and 2992 in aya_tasks.get("Zombies").npc_ids


def test_the_users_three_overrides_are_applied():
    """docs/slayer-sites/decisions.md: dogs are blocked at Aya; monkeys are killed on Karamja,
    not in Kruk's Dungeon; minotaurs keep the plugin's site but travel by the ornate jewellery
    box to the Cooking Guild."""
    assert aya_tasks.get("Dogs").disposition == aya_tasks.BLOCKED
    monkeys = aya_tasks.get("Monkeys")
    assert monkeys.disposition == aya_tasks.FIGHT
    assert monkeys.location == "Karamja, near the volcano" and monkeys.target == (2873, 3148, 0)
    assert "unverified" in monkeys.note.lower(), "the Karamja tile is a wiki pin, to confirm on the first visit"
    minotaurs = aya_tasks.get("Minotaurs")
    assert minotaurs.target == (1875, 5218, 0), "site unchanged"
    assert minotaurs.teleports[0] == "Ornate Jewellery Box: Cooking Guild"


def test_everything_else_is_fought():
    others = [t for t in AYA_TASKS if t not in ("Dogs",)]
    assert all(aya_tasks.get(t).disposition == aya_tasks.FIGHT for t in others)


def test_contains_answers_am_i_at_the_site():
    banshees = aya_tasks.get("Banshees")
    assert aya_tasks.contains(banshees, 3440, 3545, 0)
    assert not aya_tasks.contains(banshees, 3440, 3545, 1)
    assert not aya_tasks.contains(banshees, 3400, 3545, 0)
    assert aya_tasks.contains(aya_tasks.get("Lizards"), 3430, 3060, 0), "a site may be several rectangles"
```

- [ ] **Step 4: Run to verify it fails**

Run: `python -m pytest tests/test_aya_tasks.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'aya_tasks'`.

- [ ] **Step 5: Write `ControllerAddon/aya_tasks.py`**

```python
# aya_tasks.py -- where and what to kill for each task Aya (= Turael) assigns. PURE data.
# Added 2026-09-19 (slayer spec §6.1).
#
# The source of truth is the Turael Skipping Plugin Hub plugin the user runs, vendored as
# vendor/turael_skipping_tasks.json: a reflective dump of its own SlayerTaskRegistry (its data,
# not a reconstruction -- see docs/slayer-sites/). Per task: the NPC IDS that count, a target
# tile, the kill area as WorldArea rectangles (x, y, width, height, plane), a gear note, and the
# plugin's teleports in its own preference order. The orchestrator tries those teleports in
# order and takes the first the account owns.
#
# The user's overrides (docs/slayer-sites/decisions.md) are applied here and nowhere else:
#   dogs      -- BLOCKED at Aya, so never assigned; kept only so the name resolves.
#   monkeys   -- killed on Karamja ("closer"), not in Kruk's Dungeon.
#   minotaurs -- same site, reached by the ornate jewellery box to the Cooking Guild.

import json
import os
from collections import namedtuple

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "turael_skipping_tasks.json")

FIGHT = "FIGHT"
BLOCKED = "BLOCKED"

AyaTask = namedtuple("AyaTask", ("key", "name", "npc_ids", "target", "location", "teleports",
                                 "areas", "info", "disposition", "note"))

_OVERRIDES = {
    "dogs": {"disposition": BLOCKED, "note": "blocked at Aya by the user; never assigned"},
    "monkeys": {
        "location": "Karamja, near the volcano",
        "target": (2873, 3148, 0),
        "areas": ((2863, 3138, 21, 21, 0),),
        "teleports": ("Ornate Jewellery Box: Karamja", "Glory amulet: Karamja"),
        "info": None,
        "npc_ids": (),
        "note": "user override of Kruk's Dungeon. UNVERIFIED: the tile is a wiki pin (4 spawns) and "
                "the plugin's npc id (7118) is the Kruk's Dungeon monkey, so targets fall back to the "
                "name 'Monkey' until the first visit confirms the id.",
    },
    "minotaurs": {
        "teleports": ("Ornate Jewellery Box: Cooking Guild", "Skull sceptre: Invoke"),
        "note": "user override: the ornate jewellery box's Cooking Guild teleport, then walk to the "
                "Stronghold of Security entrance in Barbarian Village.",
    },
}

_cache = {}


def _build(raw):
    location = (raw.get("locations") or [{}])[0]
    fields = {
        "key": raw["key"],
        "name": raw["name"],
        "npc_ids": tuple(raw.get("npcIds") or ()),
        "target": tuple(raw["target"]) if raw.get("target") else None,
        "location": location.get("name"),
        "teleports": tuple(location.get("teleports") or ()),
        "areas": tuple(tuple(a) for a in location.get("areas") or ()),
        "info": raw.get("info"),
        "disposition": FIGHT,
        "note": "",
    }
    fields.update(_OVERRIDES.get(raw["key"], {}))
    return AyaTask(**fields)


def load(path=None) -> dict:
    path = path or DATA_PATH
    if path not in _cache:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)["tasks"]
        _cache[path] = {key: _build(task) for key, task in raw.items()}
    return _cache[path]


def get(task_name, path=None):
    """Case-insensitive; accepts the game's name, the registry's key, and a singular/plural slip
    ("cave slime" / "cave slimes", "crawling hand" / "crawling hands")."""
    tasks = load(path)
    key = (task_name or "").strip().lower()
    for candidate in (key, key + "s", key[:-1] if key.endswith("s") else key):
        if candidate in tasks:
            return tasks[candidate]
    return None


def contains(task, x, y, plane) -> bool:
    """Is (x, y, plane) inside the task's kill area? The 'am I at the site' test."""
    return any(ax <= x < ax + w and ay <= y < ay + h and plane == p for ax, ay, w, h, p in task.areas)
```

- [ ] **Step 6: Run to verify it passes**

Run: `python -m py_compile aya_tasks.py; python -m pytest tests/test_aya_tasks.py -q`
Expected: 7 passed. If `test_every_aya_task_name_resolves` reports a missing name, the registry keys differ from the game's names in a way `get` does not bridge: report the missing names rather than adding entries by hand.

- [ ] **Step 7: Commit**

```powershell
git add tools/turael_registry_to_json.py ControllerAddon/vendor/turael_skipping_tasks.json ControllerAddon/aya_tasks.py ControllerAddon/tests/test_aya_tasks.py
git commit -m "aya_tasks.py: Aya's 24 tasks from the Turael Skipping registry, with the user's three overrides

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md` (the **Pathfinding** bullet in "Running things")
- Modify: `ControllerAddon/vendor/VENDORED.md` (append a section)
- Modify: `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` (§8.1's "What is NOT built yet" sentence)

- [ ] **Step 1: CLAUDE.md**

In the **Pathfinding** bullet, after the sentence ending `Shortest Path was removed 2026-08-29.`, insert:
```
  Since 2026-09-19 floors are no longer a dead end: `crossfloor.find_route` joins those same-plane
  routes with Shortest Path's own `transports.tsv` (ladders, stairs, cave mouths, agility
  shortcuts -- vendored in `ControllerAddon/vendor/`, loaded by `transports.py`), and
  `legs.walk_across` executes the result, re-planning after every transport. `travel.walk_to`
  itself is unchanged and still same-plane. Requirements fail closed, doors and the Wilderness
  ditch are never route edges, and quest-gated transports stay unusable until something supplies
  completed quests. `tools/slayer_route_report.py` prints the routes for the slayer sites.
  UNVERIFIED LIVE.
```

- [ ] **Step 2: VENDORED.md**

Append:
```markdown
## `turael_skipping_tasks.json`

Added 2026-09-19. Generated by `python tools/turael_registry_to_json.py` from
`docs/slayer-sites/aya-turael-skipping-registry.txt`, which is a reflective dump of the Turael
Skipping Plugin Hub plugin's own `SlayerTaskRegistry.TURAEL_TASKS`
(`~/.runelite/plugins/turael-skipping_*.jar`, dumped by
`docs/slayer-sites/DumpTuraelSkippingRegistry.java`). The plugin's data, not a reconstruction --
the user runs the plugin and asked for its spots. **Never hand-edit it**: re-dump, re-run the
converter, diff. The user's three overrides live in `ControllerAddon/aya_tasks.py`, not in the
data. Upstream: brastasauce's turael-skipping (Plugin Hub; BSD-2-Clause like every hub plugin).
```

- [ ] **Step 3: The spec**

In §8.1, replace the sentence `**What is NOT built yet is everything below: the route search and the executor.**` with:
```
**Built 2026-09-19, UNVERIFIED LIVE:** the route search (`crossfloor.py`), the executor
(`legs.run_transport`) and the floor-crossing walker (`legs.walk_across`), per
`docs/superpowers/plans/2026-09-19-crossfloor-travel-and-slayer-pure-pieces.md`. The design
notes below describe what was built; the routes it finds for the chosen sites are in
`docs/slayer-sites/routes.md`.
```

- [ ] **Step 4: Full verification**

Run from `ControllerAddon/`:
```powershell
python -m py_compile crossfloor.py legs.py supplies.py slayer_state.py aya_tasks.py transports.py
python -m pytest tests/ -q
```
Expected: only the baseline failures listed in Global Constraints. Record the exact failed-test names.
Run from the repo root: `./gradlew.bat test --offline -q` — expected BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```powershell
git add CLAUDE.md ControllerAddon/vendor/VENDORED.md docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md
git commit -m "docs: cross-floor routing, the Turael Skipping data, spec 8.1 status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review against the spec

- **§8.1 transports**: route search (Task 1), generic executor (Task 2), re-planning walker and `walk` legs crossing floors (Task 3), the human sanity report replacing the "reachability test" (Task 4 — a report, because coordinates are approximate and a hard assertion on 27 sites would encode guesses). POH interior handling is NOT here: it needs the live recording (spec §12).
- **§7.9 supplies**: charges, lowest-charge-first, profiles with the user's anchors, the ported prayer formula, dose counting, the 28-slot check (Task 5). Gear restore and the low-stock stop need `/equipment`, bank memory and the orchestrator: sub-project 3.
- **§7.2 / F2**: `next_master` and the raising guard (Task 6). **§6.2**: `slayerMaxSkips` default 8 (Task 6).
- **§6.1**: Aya data vendored as JSON with the three overrides and an at-site test (Task 7).
- **Type consistency**: `crossfloor.find_route(start, goal, *, skill_levels=...)` is called the same way in Tasks 3 and 4; `legs.await_arrival(dest, budget_s, radius)` positional order matches its use in `run_transport`; `transports.Step(origin, dest, object_info, skills, items, quests, varbits, varplayers, duration, display)` matches the test helper `_step`.
