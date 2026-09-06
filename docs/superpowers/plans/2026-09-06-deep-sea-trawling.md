# Deep Sea Trawling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `deep_sea_trawling` routine that parks the boat at a Giant krill shoal and operates the trawling net **once per game tick** (a 50% catch-rate gain over the game's idle 3-tick cadence), collecting into the cargo hold and following the shoal between its stops.

**Architecture:** One small read-only Java provider (`GET /trawling`) exposes the two item containers nothing else can reach (the 250-fish net, the cargo hold). One pure Python data module (`trawling_routes.py`) holds the Wiki's shoal stop tables. The routine derives its phase from live state every tick, and its core is a burst that rides the tick clock and clicks in place, taking the game's own Sailing XP drop as the "the net is operating in range" heartbeat.

**Tech Stack:** Java 11 / RuneLite plugin API 1.12.38, Python 3 + pytest, the existing `controller.py` / `bridge_events.py` / `outcome_waits.py` / `sailing.py` helpers.

**Spec:** `docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md` — read it before Task 1. Every game fact in this plan comes from there.

## Global Constraints

- **Never edit anything under `src/main/java/net/runelite/client/plugins/m8aq/`.** Not touched by this plan.
- **Physical input only.** No packet sending, no client-API input injection. Every click goes through `controller.py`'s Arduino serial helpers.
- **Working directory for every Python command is `ControllerAddon/`.** Use absolute paths in `Bash`; do not `cd` as a prefix of a compound command.
- **Tests must be hermetic** — no bridge HTTP, no serial port, no live client. Stub `controller.api_get` / the specific `controller.get_*` helpers you use.
- **`ctx.events` may be `None` everywhere.** Every wait must keep a polling fallback.
- **`ctx.state.break_due(breaks_cfg)` takes the breaks config.** It is *not* a no-arg call (this crashed `corrupted_gauntlet` live on 2026-09-06).
- **`ControllerState.trigger_safety_stop(reason)` appends its own ". Automation paused -- fix the issue and restart."** Do not repeat that sentence in a reason string.
- **A `@Subscribe` method must be named `on<EventSimpleName>`.** No new `@Subscribe` methods are needed in this plan; do not add one.
- Commit after every task. Run `python -m pytest tests/ -q` from `ControllerAddon/` before each commit.
- Python line length in this repo is long (~100 chars); match surrounding style. Java uses tabs.

---

### Task 1: `GET /trawling` — the net and cargo-hold containers

**Files:**
- Create: `src/main/java/com/example/providers/TrawlingProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (add one registration line beside the other `new XProvider(...).register();` lines, around line 148 where `SailingProvider` is registered)
- Modify: `ControllerAddon/controller.py` (add `get_trawling()`)
- Test: `ControllerAddon/tests/test_controller.py` (one test for the new helper)

**Interfaces:**
- Consumes: nothing.
- Produces: `controller.get_trawling() -> dict` returning the JSON below (or `{}` when the bridge is unreachable).

```json
{
  "netFish": 137, "netCapacity": 250,
  "netItems": [{"id": 30135, "name": "Raw giant krill", "quantity": 137}],
  "cargo": {"containerId": 963, "used": 12, "capacity": 240,
            "items": [{"id": 30135, "name": "Raw giant krill", "quantity": 12}]},
  "carryingCargo": 1,
  "nets": [{"slot": 0, "depth": 1, "hotspotId": 3}, {"slot": 1, "depth": 0, "hotspotId": 0}]
}
```

`netFish` is `null` (JSON) / `None` (Python) when the container does not exist — meaning "not
aboard a boat with a net", which is **not** the same as 0. Same for `cargo`.

- [ ] **Step 1: Read the reference material**

Read `src/main/java/com/example/providers/GolemProvider.java` in full — it is the closest
template (small, read-only, `BridgeProvider` subclass, `server.register("/x", () -> onClient(this::getState))`).
Read the "3.1 The one Java addition" section of the spec.

- [ ] **Step 2: Write `TrawlingProvider.java`**

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.game.ItemManager;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Deep sea trawling state on GET /trawling: the two ITEM CONTAINERS nothing else on this bridge
 * can reach, plus the trawling varbits.
 *
 * Why this exists at all: every other fact routines/deep_sea_trawling.py needs is already
 * served -- the shoal and the net are both ordinary objects on /objects (ObjectProvider walks
 * the boat's nested WorldView and the top-level ocean scene and reports both in top-level world
 * coordinates), the Sailing/Fishing XP heartbeat arrives as pushed XP_GAINED events, and
 * /sailing already reports boat speed and whether steering is engaged. What is NOT reachable is
 * how many fish are in the nets: that lives in item container 968 (inv sailing_trawling_net,
 * size 250, decoded cache dump 5bc2ff1e), and the boat's cargo hold in 963..967
 * (sailing_boat_N_cargohold, size 240). No m8aq reader covers sailing at all (/api/index has no
 * sailing group).
 *
 * Deliberately NOT TickCache-gated, same reasoning as GolemProvider: these are container and
 * varbit reads like /player, not the bulk scene scans TickCache exists to make cheap, and the
 * routine polls netFish as a click-confirmation signal where a stale read would reintroduce the
 * lag the endpoint is here to remove.
 *
 * A missing container serves null, never 0 -- "not aboard a boat with a net" and "the net is
 * empty" are different answers and the Python side branches on the difference.
 */
public class TrawlingProvider extends BridgeProvider
{
	private static final int NET_CAPACITY = 250;       // inv 968 sailing_trawling_net size
	private static final int CARGO_CAPACITY = 240;     // inv 963..967 size

	private static final int[] CARGO_CONTAINERS = {
		InventoryID.SAILING_BOAT_1_CARGOHOLD,
		InventoryID.SAILING_BOAT_2_CARGOHOLD,
		InventoryID.SAILING_BOAT_3_CARGOHOLD,
		InventoryID.SAILING_BOAT_4_CARGOHOLD,
		InventoryID.SAILING_BOAT_5_CARGOHOLD,
	};

	// Two trawling-net facility slots, each with a depth and the hotspot it was built on.
	private static final int[][] NET_VARBITS = {
		{VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_0_DEPTH,
		 VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_0_HOTSPOT_ID},
		{VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_1_DEPTH,
		 VarbitID.SAILING_SIDEPANEL_BOAT_TRAWLING_NET_1_HOTSPOT_ID},
	};

	private final ItemManager itemManager;

	public TrawlingProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
	                        ItemManager itemManager)
	{
		super(client, clientThread, server);
		this.itemManager = itemManager;
	}

	@Override
	public void register()
	{
		server.register("/trawling", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();

		ItemContainer net = client.getItemContainer(InventoryID.SAILING_TRAWLING_NET);
		data.put("netCapacity", NET_CAPACITY);
		data.put("netFish", net != null ? countItems(net) : null);
		data.put("netItems", net != null ? describe(net) : null);

		Map<String, Object> cargo = null;
		for (int id : CARGO_CONTAINERS)
		{
			ItemContainer hold = client.getItemContainer(id);
			if (hold == null)
			{
				continue;
			}
			cargo = new HashMap<>();
			cargo.put("containerId", id);
			cargo.put("used", usedSlots(hold));
			cargo.put("capacity", CARGO_CAPACITY);
			cargo.put("items", describe(hold));
			break;
		}
		data.put("cargo", cargo);

		data.put("carryingCargo", client.getVarbitValue(VarbitID.SAILING_CARRYING_CARGO));

		List<Map<String, Object>> nets = new ArrayList<>();
		for (int slot = 0; slot < NET_VARBITS.length; slot++)
		{
			Map<String, Object> entry = new LinkedHashMap<>();
			entry.put("slot", slot);
			entry.put("depth", client.getVarbitValue(NET_VARBITS[slot][0]));
			entry.put("hotspotId", client.getVarbitValue(NET_VARBITS[slot][1]));
			nets.add(entry);
		}
		data.put("nets", nets);

		return data;
	}

	private int countItems(ItemContainer container)
	{
		int total = 0;
		for (Item item : container.getItems())
		{
			if (item != null && item.getId() > 0)
			{
				total += Math.max(item.getQuantity(), 0);
			}
		}
		return total;
	}

	private int usedSlots(ItemContainer container)
	{
		int used = 0;
		for (Item item : container.getItems())
		{
			if (item != null && item.getId() > 0)
			{
				used++;
			}
		}
		return used;
	}

	private List<Map<String, Object>> describe(ItemContainer container)
	{
		Map<Integer, Integer> totals = new LinkedHashMap<>();
		for (Item item : container.getItems())
		{
			if (item != null && item.getId() > 0)
			{
				totals.merge(item.getId(), Math.max(item.getQuantity(), 0), Integer::sum);
			}
		}

		List<Map<String, Object>> out = new ArrayList<>();
		for (Map.Entry<Integer, Integer> entry : totals.entrySet())
		{
			Map<String, Object> row = new LinkedHashMap<>();
			row.put("id", entry.getKey());
			row.put("name", itemManager.getItemComposition(entry.getKey()).getName());
			row.put("quantity", entry.getValue());
			out.add(row);
		}
		return out;
	}
}
```

- [ ] **Step 3: Register it**

In `src/main/java/com/example/controller/ControllerPlugin.java`, immediately after the
`new SailingProvider(client, clientThread, server, eventBus).register();` line, add:

```java
		new TrawlingProvider(client, clientThread, server, itemManager).register();
```

`itemManager` is already an injected field on the plugin (it is passed to `GroundItemProvider`
on the line beginning `new GroundItemProvider(client, clientThread, server, itemManager, ...`).
Confirm that with `grep -n "itemManager" src/main/java/com/example/controller/ControllerPlugin.java`
before editing; if it is a local rather than a field, mirror however `GroundItemProvider` obtains it.

- [ ] **Step 4: Compile**

Run from the repo root: `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`. If `InventoryID.SAILING_TRAWLING_NET` or a `VarbitID` constant does
not resolve, verify the exact name with:
`javap -constants -cp <the runelite-api jar in ~/.gradle/caches> net.runelite.api.gameval.InventoryID | grep -i sailing`
(the jar path is `~/.gradle/caches/modules-2/files-2.1/net.runelite/runelite-api/1.12.38/*/runelite-api-1.12.38.jar`).
Do **not** invent a constant name — use the one javap prints.

- [ ] **Step 5: Add the Python helper**

In `ControllerAddon/controller.py`, immediately after `get_varbit`, add:

```python
def get_trawling():
    """GET /trawling (TrawlingProvider.java, added 2026-09-06): the deep-sea-trawling item
    containers and varbits. netFish is None (not 0) when the container does not exist, i.e. the
    player is not aboard a boat with a trawling net -- routines/deep_sea_trawling.py branches on
    that difference, so do not collapse it with `or 0`."""
    return api_get("/trawling") or {}
```

- [ ] **Step 6: Write the failing test for the helper**

Append to `ControllerAddon/tests/test_controller.py`:

```python
def test_get_trawling_passes_through_and_tolerates_an_unreachable_bridge(monkeypatch):
    payload = {"netFish": 137, "netCapacity": 250, "cargo": None, "nets": []}
    monkeypatch.setattr(controller, "api_get", lambda path, params=None: payload
                        if path == "/trawling" else None)
    assert controller.get_trawling() is payload

    monkeypatch.setattr(controller, "api_get", lambda path, params=None: None)
    assert controller.get_trawling() == {}
```

If `test_controller.py` has an autouse `no_live_bridge` fixture that stubs `api_get`, mark this
test `@pytest.mark.real_bridge_helper` the way the existing bridge-helper tests in that file do —
check how a neighbouring `get_*` test there is marked and copy it exactly.

- [ ] **Step 7: Run the test**

Run from `ControllerAddon/`: `python -m pytest tests/test_controller.py -q -k trawling`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/main/java/com/example/providers/TrawlingProvider.java \
        src/main/java/com/example/controller/ControllerPlugin.java \
        ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "bridge: GET /trawling -- the 250-fish net and cargo-hold containers"
```

---

### Task 2: `trawling_routes.py` — shoal specs and the Wiki stop tables

**Files:**
- Create: `ControllerAddon/trawling_routes.py`
- Test: `ControllerAddon/tests/test_trawling_routes.py`

**Interfaces:**
- Consumes: nothing (pure module, no imports from the project).
- Produces, used by Task 3 onward:
  - `SHOALS: dict[str, ShoalSpec]`, `ShoalSpec` is a `NamedTuple` with fields
    `key, name, object_ids (tuple[int]), mixed_ids (tuple[int]), fishing_level (int),
    stop_ticks (int), depths (tuple[str])`
  - `ROUTES: dict[tuple[str, str], Route]`, `Route` is a `NamedTuple` with fields
    `lap_ticks (int), markers (tuple[tuple[int, int, int]])`
  - `world_of(region_id: int, rx: int, ry: int) -> tuple[int, int]`
  - `stops_world(shoal: str, route: str) -> list[tuple[int, int]]`
  - `nearest_stop(shoal, route, x, y) -> tuple[int, tuple[int, int], float]` — `(index, (x, y), distance)`
  - `next_stop(shoal, route, index) -> tuple[int, tuple[int, int]]` — wraps
  - `route_bounds(shoal, route) -> tuple[int, int, int, int]` — `(minx, miny, maxx, maxy)`
  - `in_route_area(shoal, route, x, y, margin=24) -> bool`
  - `leg_ticks(shoal, route) -> float`
  - `all_shoal_ids(shoal) -> tuple[int, ...]` — `object_ids + mixed_ids`

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_trawling_routes.py`:

```python
"""Pure tests for trawling_routes.py. Every coordinate here is hand-computed from the OSRS Wiki's
own tile-marker tables (Giant krill shoal, rev 15237362) with
worldX = (regionId >> 8) * 64 + regionX, worldY = (regionId & 0xFF) * 64 + regionY."""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import trawling_routes as tr


def test_world_of_matches_the_region_packing():
    # region 6452 = (25, 52) chunks -> base (1600, 3328)
    assert tr.world_of(6452, 16, 51) == (1616, 3379)
    # region 6196 = (24, 52) chunks -> base (1536, 3328)
    assert tr.world_of(6196, 38, 10) == (1574, 3338)


def test_great_sound_stops_are_the_wiki_order():
    stops = tr.stops_world("giant_krill", "great_sound")
    assert len(stops) == 7
    assert stops[0] == (1616, 3379)     # Wiki label 1
    assert stops[1] == (1580, 3387)     # label 2
    assert stops[3] == (1574, 3338)     # label 4
    assert stops[6] == (1580, 3365)     # label 7


def test_nearest_stop_returns_index_point_and_distance():
    idx, point, dist = tr.nearest_stop("giant_krill", "great_sound", 1576, 3340)
    assert idx == 3
    assert point == (1574, 3338)
    assert dist == math.hypot(2, 2)


def test_next_stop_wraps_to_the_start_of_the_lap():
    assert tr.next_stop("giant_krill", "great_sound", 6) == (0, (1616, 3379))
    assert tr.next_stop("giant_krill", "great_sound", 0) == (1, (1580, 3387))


def test_route_bounds_and_membership():
    assert tr.route_bounds("giant_krill", "great_sound") == (1560, 3338, 1616, 3387)
    assert tr.in_route_area("giant_krill", "great_sound", 1580, 3360)
    assert tr.in_route_area("giant_krill", "great_sound", 1550, 3330, margin=24)
    assert not tr.in_route_area("giant_krill", "great_sound", 3200, 3200, margin=24)


def test_leg_ticks_is_the_lap_minus_the_stops():
    # 1234 tick lap, 7 stops of 150 ticks -> 184 moving ticks over 7 legs
    assert tr.leg_ticks("giant_krill", "great_sound") == 184 / 7


def test_every_stored_route_is_traversable_at_a_plausible_boat_speed():
    """The evidence that the Wiki's label 1..7 really is the VISITING order: with the stops in
    that order, every leg's implied speed lands in a tight, plausible band (Great Sound 1.18
    tiles/tick average and 1.57 worst leg, Simian Sea 1.47/1.77, Turtle Belt 1.26/1.56). A wrong
    ordering would scatter these well past any real boat speed."""
    for (shoal, route) in tr.ROUTES:
        stops = tr.stops_world(shoal, route)
        budget = tr.leg_ticks(shoal, route) * tr.MAX_BOAT_TILES_PER_TICK
        for i, here in enumerate(stops):
            nxt = stops[(i + 1) % len(stops)]
            assert math.dist(here, nxt) <= budget, f"{shoal}/{route} leg {i}"


def test_all_shoal_ids_includes_the_mixed_shoal_that_replaces_it():
    assert tr.all_shoal_ids("giant_krill") == (59734, 59740)


def test_every_route_key_names_a_known_shoal():
    for (shoal, _route) in tr.ROUTES:
        assert shoal in tr.SHOALS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `ControllerAddon/`: `python -m pytest tests/test_trawling_routes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trawling_routes'`.

- [ ] **Step 3: Write the module**

Create `ControllerAddon/trawling_routes.py`:

```python
"""Deep sea trawling: shoal definitions and the shoals' fixed patrol routes.

Pure data and pure lookups -- no bridge, no client, no imports from this project. Shoal paths
are PREDETERMINED and cyclical (OSRS Wiki, Deep sea trawling rev 15297127: "The movements of
each shoal are predetermined, making their path, speed, stopping points, and time spent still
cyclical in nature"), so the stop points are a table rather than something to discover live.

Stops are stored as the Wiki's own raw tile markers -- (regionId, regionX, regionY) -- and
converted here, so no coordinate is transcribed by hand. Source: the "Routes" section of each
shoal's Wiki page (Giant krill shoal rev 15237362, Haddock shoal rev 15293151); the marker
"label" is the visiting order.

THE TABLE IS AN ACCELERATOR, NEVER TRUTH. The live shoal object in /objects decides where the
shoal actually is; these stops only aim the boat ahead of it while it is in transit, and give
routines/deep_sea_trawling.py somewhere to sail when no shoal is in the loaded scene. If the
shoal turns up somewhere else, the ordinary follow corrects on the next tick.

NOT MEASURED LIVE. See the spec's live-verification checklist item 8: log the shoal's tile at
each stop for one lap and diff it against stops_world().
"""

from typing import Dict, List, NamedTuple, Tuple

# A boat's top speed is around 2-2.5 tiles/tick; the implied speeds of every route stored here
# sit between 1.18 and 1.77, so this bound is a real check on the stop ORDER, not a formality.
MAX_BOAT_TILES_PER_TICK = 2.5


class ShoalSpec(NamedTuple):
    key: str
    name: str
    object_ids: Tuple[int, ...]     # the shoal's own clickbox loc ids
    mixed_ids: Tuple[int, ...]      # mixed shoals that replace it 10% of the time
    fishing_level: int
    stop_ticks: int                 # ticks the shoal sits still at each stop
    depths: Tuple[str, ...]         # ("shallow",) means no depth management is needed


class Route(NamedTuple):
    lap_ticks: int
    markers: Tuple[Tuple[int, int, int], ...]   # (regionId, regionX, regionY), visiting order


# Cache config/loc ids (dump 5bc2ff1e): sailing_shoal_clickbox_* 59734..59742.
SHOALS: Dict[str, ShoalSpec] = {
    "giant_krill": ShoalSpec(
        key="giant_krill", name="Giant krill shoal",
        object_ids=(59734,), mixed_ids=(59740,),          # 59740 = shimmering shoal
        fishing_level=69, stop_ticks=150, depths=("shallow",)),
    "haddock": ShoalSpec(
        key="haddock", name="Haddock shoal",
        object_ids=(59735,), mixed_ids=(59740,),
        fishing_level=73, stop_ticks=120, depths=("shallow",)),
}


ROUTES: Dict[Tuple[str, str], Route] = {
    # --- Giant krill (90s stops) ---------------------------------------------------------
    # Great Sound is the v1 default: 7 stops inside ~56x49 tiles, so every stop is inside the
    # 104x104 loaded scene from anywhere on the route and the shoal is essentially always in
    # /objects.
    ("giant_krill", "great_sound"): Route(1234, (
        (6452, 16, 51), (6196, 44, 59), (6196, 25, 36), (6196, 38, 10),
        (6196, 44, 51), (6196, 24, 42), (6196, 44, 37))),
    ("giant_krill", "simian_sea"): Route(1196, (
        (11048, 12, 48), (11049, 30, 5), (11048, 54, 48), (11304, 23, 31),
        (11304, 13, 12), (11047, 43, 63), (11048, 35, 28))),
    ("giant_krill", "sunset_bay"): Route(1250, (
        (5934, 31, 5), (6189, 33, 56), (6189, 40, 24), (6188, 54, 56),
        (5933, 50, 16), (5933, 22, 8), (5933, 25, 41))),
    ("giant_krill", "turtle_belt"): Route(1410, (
        (11815, 19, 29), (11815, 0, 49), (11816, 27, 6), (11815, 49, 35),
        (12071, 9, 16), (11815, 41, 6), (11814, 24, 45), (11559, 61, 10))),

    # --- Haddock (72s stops) -------------------------------------------------------------
    # These routes span five or more regions, so the shoal LEAVES the loaded scene mid-leg and
    # the follow has to fall back on the table. Not the v1 default for that reason.
    ("haddock", "anglerfishs_light"): Route(1303, (
        (11301, 1, 9), (11045, 20, 43), (10789, 53, 10), (11044, 23, 36),
        (11044, 18, 10), (10788, 14, 20), (10789, 25, 51), (11045, 31, 63))),
    ("haddock", "sea_of_shells"): Route(1674, (
        (12068, 13, 2), (11812, 7, 9), (11812, 30, 61), (12068, 33, 47),
        (12324, 25, 0), (12323, 14, 16), (12322, 24, 38), (12066, 51, 22),
        (11810, 60, 28), (11810, 31, 45))),
}


def world_of(region_id: int, rx: int, ry: int) -> Tuple[int, int]:
    """A Wiki tile marker -> absolute world coordinates. A region id packs the 64x64 chunk as
    (chunkX << 8) | chunkY."""
    return ((region_id >> 8) * 64 + rx, (region_id & 0xFF) * 64 + ry)


def _route(shoal: str, route: str) -> Route:
    try:
        return ROUTES[(shoal, route)]
    except KeyError:
        raise KeyError(f"no route {route!r} for shoal {shoal!r}; known: "
                       f"{sorted(k for k in ROUTES if k[0] == shoal)}")


def stops_world(shoal: str, route: str) -> List[Tuple[int, int]]:
    return [world_of(*m) for m in _route(shoal, route).markers]


def nearest_stop(shoal: str, route: str, x: float, y: float):
    stops = stops_world(shoal, route)
    best = min(range(len(stops)),
               key=lambda i: (stops[i][0] - x) ** 2 + (stops[i][1] - y) ** 2)
    bx, by = stops[best]
    return best, (bx, by), ((bx - x) ** 2 + (by - y) ** 2) ** 0.5


def next_stop(shoal: str, route: str, index: int):
    stops = stops_world(shoal, route)
    nxt = (index + 1) % len(stops)
    return nxt, stops[nxt]


def route_bounds(shoal: str, route: str) -> Tuple[int, int, int, int]:
    stops = stops_world(shoal, route)
    xs = [s[0] for s in stops]
    ys = [s[1] for s in stops]
    return (min(xs), min(ys), max(xs), max(ys))


def in_route_area(shoal: str, route: str, x: float, y: float, margin: int = 24) -> bool:
    minx, miny, maxx, maxy = route_bounds(shoal, route)
    return (minx - margin) <= x <= (maxx + margin) and (miny - margin) <= y <= (maxy + margin)


def leg_ticks(shoal: str, route: str) -> float:
    """Expected ticks the shoal spends travelling between two consecutive stops."""
    r = _route(shoal, route)
    n = len(r.markers)
    return (r.lap_ticks - n * SHOALS[shoal].stop_ticks) / n


def all_shoal_ids(shoal: str) -> Tuple[int, ...]:
    """Every loc id that counts as "our shoal", including the mixed shoal that replaces it 10%
    of the time (Wiki: mixed shoals keep the original's movements and stop timers)."""
    spec = SHOALS[shoal]
    return spec.object_ids + spec.mixed_ids
```

- [ ] **Step 4: Run the tests**

Run from `ControllerAddon/`: `python -m pytest tests/test_trawling_routes.py -q`
Expected: all PASS. If `test_every_stored_route_is_traversable_at_a_plausible_boat_speed` fails
for a Haddock route, **do not raise `MAX_BOAT_TILES_PER_TICK` to make it pass** — the marker
order is transcribed from the Wiki's `label` field and a failure means a transcription error.
Re-derive that route's order from the spec's §4 listing and fix the table. If it still fails,
delete that route entry and note it in the commit message; the v1 default (`great_sound`) is the
only one that has to be right.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/trawling_routes.py ControllerAddon/tests/test_trawling_routes.py
git commit -m "trawling_routes: shoal specs and the Wiki's fixed shoal stop tables"
```

---

### Task 3: The routine's skeleton — snapshot, config, and pure `derive_phase`

**Files:**
- Create: `ControllerAddon/routines/deep_sea_trawling.py`
- Test: `ControllerAddon/tests/test_deep_sea_trawling.py`

**Interfaces:**
- Consumes: `trawling_routes` (Task 2), `controller.get_trawling` (Task 1).
- Produces, used by Tasks 4-6:
  - `DEFAULT_CONFIG: dict`, `get_config(bridge_config) -> dict`
  - `VARBIT_SUBSCRIPTIONS: list[int]`
  - `TrawlSnapshot` — a `NamedTuple` with fields
    `px, py, plane, boat_speed, navigating, net (dict|None), net_depth (int|None),
    shoal (dict|None), shoal_distance (float|None), net_fish (int|None),
    cargo_used (int|None), cargo_capacity (int|None), ticks_since_sailing_xp (float),
    in_route_area (bool)`
  - `derive_phase(snap: TrawlSnapshot, cfg: dict) -> str` — one of
    `"lost" | "travel" | "approach" | "lower" | "trawl" | "collect" | "done"`
  - `_now()` / `_sleep(s)` module-level indirections (so tests install a fake clock)

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_deep_sea_trawling.py`:

```python
"""Tests for routines/deep_sea_trawling.py. Hermetic: no bridge, no serial, no client."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import deep_sea_trawling as dst


def _snap(**kw):
    base = dict(px=1580, py=3365, plane=0, boat_speed=0.0, navigating=True,
                net={"id": 59755, "x": 1580, "y": 3365, "plane": 0, "distance": 2},
                net_depth=1,
                shoal={"id": 59734, "x": 1580, "y": 3365, "plane": 0}, shoal_distance=3.0,
                net_fish=10, cargo_used=5, cargo_capacity=240,
                ticks_since_sailing_xp=0.0, in_route_area=True)
    base.update(kw)
    return dst.TrawlSnapshot(**base)


CFG = dict(dst.DEFAULT_CONFIG)


def test_derive_phase_truth_table():
    d = dst.derive_phase
    assert d(_snap(), CFG) == "trawl"
    # No net facility on the boat at all, or the container does not exist: we are not aboard.
    assert d(_snap(net=None), CFG) == "lost"
    assert d(_snap(net_fish=None), CFG) == "lost"
    # No shoal in the scene -> sail to the route.
    assert d(_snap(shoal=None, shoal_distance=None), CFG) == "travel"
    # Out of parking range, or still moving: reposition.
    assert d(_snap(shoal_distance=30.0), CFG) == "approach"
    assert d(_snap(boat_speed=1.4), CFG) == "approach"
    # In range and stopped, but the net is up.
    assert d(_snap(net_depth=0), CFG) == "lower"
    # Net at the collect threshold beats everything except lost/done.
    assert d(_snap(net_fish=CFG["netFullAt"]), CFG) == "collect"
    assert d(_snap(net_fish=CFG["netFullAt"], shoal_distance=30.0), CFG) == "collect"
    # Cargo hold full and nothing left in the nets -> done.
    assert d(_snap(cargo_used=240, net_fish=0), CFG) == "done"
    # Cargo hold full but fish still in the nets -> collect them first; the collect itself
    # discovers the hold is full and the next tick reads done.
    assert d(_snap(cargo_used=240, net_fish=10), CFG) == "collect"


def test_derive_phase_travel_outside_the_route_area_is_lost():
    assert dst.derive_phase(_snap(shoal=None, shoal_distance=None, in_route_area=False),
                            CFG) == "lost"


def test_get_config_ignores_the_bridge_config_and_forces_the_inventory_safety_off():
    cfg = dst.get_config({"anything": 1})
    assert cfg["shoal"] == "giant_krill"
    assert cfg["route"] == "great_sound"
    assert cfg["safety"]["stopOnInventoryFull"] is False


def test_shoal_ids_include_the_mixed_shoal():
    assert dst.shoal_ids(CFG) == (59734, 59740)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `ControllerAddon/`: `python -m pytest tests/test_deep_sea_trawling.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'deep_sea_trawling'`.

- [ ] **Step 3: Write the module skeleton**

Create `ControllerAddon/routines/deep_sea_trawling.py`:

```python
# routines/deep_sea_trawling.py
#
# Deep sea trawling: park the boat at a shoal and OPERATE the trawling net once per game tick.
#
# Why one click per tick: the game rolls for a catch every 3 ticks while a net is operating in
# range, but clicking the net every tick makes it roll every 2 ticks -- a flat +50% catch rate
# (OSRS Wiki, Deep sea trawling rev 15297127, "1-tick clicking"). That burst is the whole point
# of this routine, and it is why the net is clicked IN PLACE (controller.click_human, ~92ms,
# measured in giants_foundry) rather than re-flown per click: a fresh click_entity flight costs
# ~1s and cannot hold a 0.6s cadence at all.
#
# Written 2026-09-06 from the Wiki, the decoded cache (dump 5bc2ff1e) and runelite-api 1.12.38.
# There is NO human recording and NOTHING here has been run live -- see the design spec's
# "Live-verification checklist", which is the first session's worklist:
#   docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md
#
# Game facts this rests on:
#   loc 59755 "Rope trawling net"  op1=Operate op2=Raise op3=Lower op4=Collect-from op5=Modify
#       (59756/59757/59758 linen/hemp/cotton; 59759..59766 the 3x8 port/starboard variants)
#   loc 59734 "Giant krill shoal"  a 6x6 object out at sea; 59740 the shimmering shoal that
#       replaces it 10% of the time with identical movement and stop timers
#   inv 968   the shared 250-fish net container, per player  -> GET /trawling
#   Every roll grants a small SAILING xp drop; a SUCCESSFUL roll also grants FISHING xp. The
#       Sailing drop is therefore a pushed, sub-tick HEARTBEAT proving "the net is operating in
#       range" -- it is the range test, not distance to a moving 6x6 object.
#   Fish in the nets are DISCARDED on disembark, teleport, world-hop or a forced logout. This
#       routine therefore never hops worlds, never teleports, and collects before every break.
#
# Preconditions (checked by _startup_checks, each with its own one-line stop reason):
#   - standing on your boat, which has a trawling net built
#   - a CREWMATE on the helm. Helmsmanship lets a crewmate hold the helm so the player can stand
#     at another facility while the player still clicks the heading (Wiki, Crew Management).
#     Without one the player must stand at the helm to steer and cannot trawl at the same time.
#   - Fishing level >= the configured shoal's requirement
#   - the boat is on the configured route
#
# Run with:
#     python controller.py routines/deep_sea_trawling.py

import time
from typing import NamedTuple, Optional

import bridge_events
import controller
import debug_log
import outcome_waits
import sailing
import trawling_routes

# --------------------------------------------------------------------------- config

DEFAULT_CONFIG = {
    "shoal": "giant_krill",
    "route": "great_sound",
    # UNVERIFIED (spec checklist #3): the largest boat->shoal-centre distance that still keeps
    # the Sailing XP heartbeat alive. Park inside this; the heartbeat, not this number, decides
    # whether we are actually catching.
    "trawlRange": 8,
    "netFullAt": 240,            # collect here, not at the 250 cap
    "collectTo": "cargo",        # "cargo" | "inventory"
    "stopWhenCargoFull": True,
    "safety": {},
}

# The engine's safety check runs BEFORE tick(), so an inventory-full stop would win the race
# against our own collect. Same reason mining.py forces it off.
_FORCED_SAFETY = {"stopOnInventoryFull": False}

# Net depth (19206/19208) and "carrying cargo" (19134). The engine posts these on load and after
# every login; on_event and the burst both key on them.
VARBIT_SUBSCRIPTIONS = [19206, 19208, 19134]

NET_NAME_FRAGMENT = "trawling net"
NET_OBJECT_IDS = tuple(range(59755, 59767))     # rope/linen/hemp/cotton x 2x5 + 3x8 port/starboard

# --------------------------------------------------------------------------- clock indirection

def _now():
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


# --------------------------------------------------------------------------- state


class TrawlSnapshot(NamedTuple):
    px: Optional[int]
    py: Optional[int]
    plane: Optional[int]
    boat_speed: float
    navigating: bool
    net: Optional[dict]
    net_depth: Optional[int]
    shoal: Optional[dict]
    shoal_distance: Optional[float]
    net_fish: Optional[int]
    cargo_used: Optional[int]
    cargo_capacity: Optional[int]
    ticks_since_sailing_xp: float
    in_route_area: bool


def get_config(bridge_config):
    """Plain DEFAULT_CONFIG -- this routine reads nothing from GET /config, so wiring it into the
    plugin needs only the RoutineChoice enum entry and controller.ROUTINE_FILES, no
    ControllerConfig section (CLAUDE.md, "The routine contract")."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["safety"] = dict(cfg.get("safety") or {})
    cfg["safety"].update(_FORCED_SAFETY)
    return cfg


def shoal_ids(cfg):
    return trawling_routes.all_shoal_ids(cfg["shoal"])


BOAT_MOVING_SPEED = 0.05     # tiles/tick below which the boat counts as stopped


def derive_phase(snap: TrawlSnapshot, cfg: dict) -> str:
    """Pure. Every phase comes from LIVE state, never from a stored step -- ctx.state.data is
    wiped on restart while the boat's position, its nets and its cargo are not."""
    if snap.net is None or snap.net_fish is None:
        return "lost"

    if snap.cargo_used is not None and snap.cargo_capacity is not None \
            and snap.cargo_used >= snap.cargo_capacity and cfg["stopWhenCargoFull"]:
        return "collect" if snap.net_fish > 0 else "done"

    if snap.net_fish >= cfg["netFullAt"]:
        return "collect"

    if snap.shoal is None:
        return "travel" if snap.in_route_area else "lost"

    if snap.boat_speed > BOAT_MOVING_SPEED:
        return "approach"
    if snap.shoal_distance is None or snap.shoal_distance > cfg["trawlRange"]:
        return "approach"

    if not snap.net_depth:
        return "lower"

    return "trawl"
```

- [ ] **Step 4: Run the tests**

Run from `ControllerAddon/`: `python -m pytest tests/test_deep_sea_trawling.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/deep_sea_trawling.py \
        ControllerAddon/tests/test_deep_sea_trawling.py
git commit -m "deep_sea_trawling: config, live snapshot and pure phase derivation"
```

---

### Task 4: Reading the world — the net object, the shoal, and the snapshot

**Files:**
- Modify: `ControllerAddon/routines/deep_sea_trawling.py`
- Test: `ControllerAddon/tests/test_deep_sea_trawling.py`

**Interfaces:**
- Consumes: `TrawlSnapshot`, `shoal_ids`, `derive_phase` (Task 3); `controller.get_trawling` (Task 1); `trawling_routes.in_route_area` (Task 2).
- Produces, used by Tasks 5-6:
  - `find_net(objects) -> dict | None`
  - `find_shoal(objects, cfg, px, py) -> tuple[dict | None, float | None]`
  - `read_snapshot(ctx, cfg, ticks_since_sailing_xp=0.0) -> TrawlSnapshot`
  - `net_refresh_target(net)` — a zero-arg callable for `click_entity` / `hover_entity`

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_deep_sea_trawling.py`:

```python
import controller
import trawling_routes


NET_OBJ = {"id": 59755, "name": "Rope trawling net", "x": 1580, "y": 3365, "plane": 0,
           "distance": 2, "screenX": 700, "screenY": 400}
SHOAL_OBJ = {"id": 59734, "name": "<col=FFFF00>Giant krill shoal</col>",
             "x": 1583, "y": 3367, "plane": 0, "screenX": 900, "screenY": 300}


def test_find_net_matches_by_id_not_by_name():
    """The name carries a colour tag on the shoal and could carry one anywhere, so ids come
    first; the name fragment is only the fallback for a net variant we have not tabulated."""
    assert dst.find_net([{"id": 1}, NET_OBJ]) is NET_OBJ
    odd = {"id": 99999, "name": "Driftwood trawling net", "x": 1, "y": 2, "plane": 0}
    assert dst.find_net([odd]) is odd
    assert dst.find_net([{"id": 1, "name": "Helm"}]) is None


def test_find_shoal_picks_the_nearest_matching_id_and_reports_its_distance():
    far = dict(SHOAL_OBJ, x=1620, y=3390)
    shoal, dist = dst.find_shoal([far, SHOAL_OBJ], CFG, 1580, 3365)
    assert shoal is SHOAL_OBJ
    assert round(dist, 3) == round(((3 ** 2) + (2 ** 2)) ** 0.5, 3)


def test_find_shoal_accepts_the_mixed_shoal_that_replaces_ours():
    mixed = dict(SHOAL_OBJ, id=59740, name="<col=FFFF00>Shimmering shoal</col>")
    shoal, _dist = dst.find_shoal([mixed], CFG, 1580, 3365)
    assert shoal is mixed


def test_find_shoal_ignores_another_shoal_type():
    other = dict(SHOAL_OBJ, id=59736, name="<col=FFFF00>Yellowfin shoal</col>")
    assert dst.find_shoal([other], CFG, 1580, 3365) == (None, None)


def test_read_snapshot_folds_the_bridge_into_one_record(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1580, "y": 3365, "plane": 0})
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None: {"boatSpeed": 0.0, "navigating": True}
                        if path == "/sailing" else None)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [NET_OBJ, SHOAL_OBJ])
    monkeypatch.setattr(controller, "get_trawling",
                        lambda: {"netFish": 12, "cargo": {"used": 3, "capacity": 240},
                                 "nets": [{"slot": 0, "depth": 1, "hotspotId": 3},
                                          {"slot": 1, "depth": 0, "hotspotId": 0}]})

    snap = dst.read_snapshot(object(), CFG, ticks_since_sailing_xp=2.0)
    assert snap.net is NET_OBJ
    assert snap.shoal is SHOAL_OBJ
    assert snap.net_fish == 12
    assert snap.net_depth == 1          # the built net's slot, not slot 0 blindly
    assert snap.cargo_used == 3
    assert snap.navigating is True
    assert snap.in_route_area is True
    assert snap.ticks_since_sailing_xp == 2.0
    assert dst.derive_phase(snap, CFG) == "trawl"


def test_read_snapshot_reports_none_not_zero_when_the_net_container_is_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1580, "y": 3365, "plane": 0})
    monkeypatch.setattr(controller, "api_get", lambda path, params=None: {})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_trawling", lambda: {})
    snap = dst.read_snapshot(object(), CFG)
    assert snap.net_fish is None
    assert snap.net is None
    assert dst.derive_phase(snap, CFG) == "lost"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_deep_sea_trawling.py -q`
Expected: FAIL — `AttributeError: module 'deep_sea_trawling' has no attribute 'find_net'`.

- [ ] **Step 3: Implement**

Append to `ControllerAddon/routines/deep_sea_trawling.py`:

```python
# --------------------------------------------------------------------------- reading the world


def find_net(objects):
    """The trawling net facility on our own deck. Matched by ID first: /objects reports
    ObjectComposition.getName() raw, so a name can carry a <col=...> tag (the shoal's does), and
    the 12 net variants (4 tiers x 2x5 / 3x8-port / 3x8-starboard) are all tabulated. The name
    fragment is only a fallback for a variant the cache dump did not have."""
    for obj in objects or []:
        if obj.get("id") in NET_OBJECT_IDS:
            return obj
    for obj in objects or []:
        if NET_NAME_FRAGMENT in (obj.get("name") or "").lower():
            return obj
    return None


def find_shoal(objects, cfg, px, py):
    """The nearest shoal of the configured type, plus its distance from the player. Returns
    (None, None) when none is in the scene -- the shoal is a 6x6 loc out on the ocean and
    /objects walks the top-level scene as well as the boat's nested WorldView, so both the net
    and the shoal come out of one call in comparable top-level world coordinates."""
    wanted = shoal_ids(cfg)
    best = None
    best_dist = None
    for obj in objects or []:
        if obj.get("id") not in wanted:
            continue
        if px is None or obj.get("x") is None:
            continue
        dist = ((obj["x"] - px) ** 2 + (obj["y"] - py) ** 2) ** 0.5
        if best_dist is None or dist < best_dist:
            best, best_dist = obj, dist
    return best, best_dist


def _built_net_depth(trawling):
    """The depth of the net slot that is actually built. `nets` reports both facility slots and
    an unbuilt one reads hotspotId 0 -- reading slot 0 blindly would call a built starboard net
    'raised' forever."""
    for entry in (trawling.get("nets") or []):
        if entry.get("hotspotId"):
            return entry.get("depth")
    return None


def read_snapshot(ctx, cfg, ticks_since_sailing_xp: float = 0.0) -> TrawlSnapshot:
    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")

    sail = controller.api_get("/sailing") or {}
    objects = controller.get_objects() or []
    trawling = controller.get_trawling() or {}

    net = find_net(objects)
    shoal, shoal_dist = find_shoal(objects, cfg, px, py)
    cargo = trawling.get("cargo") or {}

    return TrawlSnapshot(
        px=px, py=py, plane=plane,
        boat_speed=float(sail.get("boatSpeed") or 0.0),
        navigating=bool(sail.get("navigating")),
        net=net,
        net_depth=_built_net_depth(trawling),
        shoal=shoal, shoal_distance=shoal_dist,
        net_fish=trawling.get("netFish"),
        cargo_used=cargo.get("used"), cargo_capacity=cargo.get("capacity"),
        ticks_since_sailing_xp=ticks_since_sailing_xp,
        in_route_area=(px is not None and trawling_routes.in_route_area(
            cfg["shoal"], cfg["route"], px, py)),
    )


def net_refresh_target(net):
    """A refresh_target for the net. The boat is stopped while trawling, so the net's top-level
    world tile is stable and /object/at can re-resolve its projection as the camera moves."""
    return controller.object_refresh_target(net["x"], net["y"], net.get("plane", 0), net["id"])
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_deep_sea_trawling.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/deep_sea_trawling.py \
        ControllerAddon/tests/test_deep_sea_trawling.py
git commit -m "deep_sea_trawling: find the net and the shoal, and fold them into one snapshot"
```

---

### Task 5: `trawl_burst` — the one-click-per-tick core

**Files:**
- Modify: `ControllerAddon/routines/deep_sea_trawling.py`
- Test: `ControllerAddon/tests/test_deep_sea_trawling.py`

**Interfaces:**
- Consumes: everything from Tasks 3-4; `outcome_waits.tick_pause`; `controller.click_human`, `controller.hover_entity`.
- Produces: `trawl_burst(ctx, cfg, net) -> str` returning one of
  `"out_of_range" | "net_full" | "interrupted" | "budget"`.

**Read before writing:** `ControllerAddon/outcome_waits.py` in full, and `bridge_events.EventStream.mark` / `.since` (around lines 291-306 of `bridge_events.py`).

**The one rule you must not get wrong.** `on_event` is dispatched by the engine loop and never
runs while a routine is holding `tick()`. This burst holds the tick for up to 90 seconds, so it
reads events itself with `ctx.events.since(mark)` — and it must take `mark` **once before the
loop** and advance it to `ctx.events.mark()` **immediately after every read**. Taking a fresh
`mark()` at the top of each iteration reads a sequence number the previous iteration's own tick
wait already pumped past, silently dropping every event that arrived during that wait — which
here is the entire heartbeat. (This was a Critical in the Hunllef fight's final review.)

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_deep_sea_trawling.py`:

```python
import bridge_events
import outcome_waits


class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(dst.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason

    def break_due(self, breaks_cfg):
        return False


class _Ctx:
    def __init__(self, events):
        self.events = events
        self.ser = object()
        self.state = _State()


def _burst_ctx(monkeypatch, tick_events):
    """A real EventStream fed by a scripted poll_udp: tick_events[i] is the list of
    (type, payload) delivered on the i-th poll. A real stream is used so await/since/mark
    semantics are what gets exercised, not a stand-in."""
    queue = list(tick_events)

    def poll_udp(_sock):
        return list(queue.pop(0)) if queue else []

    monkeypatch.setattr(controller, "poll_udp", poll_udp)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(dst, "_now", lambda: clock["t"])
    monkeypatch.setattr(dst, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ctx = _Ctx(bridge_events.EventStream(sock=None))
    ctx.clock = clock
    return ctx


def _stub_burst_io(monkeypatch, clicks, net_fish=lambda: 10, moved=lambda: False):
    monkeypatch.setattr(controller, "click_human", lambda ser: clicks.append(1))
    monkeypatch.setattr(controller, "hover_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": net_fish()})
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1581 if moved() else 1580, "y": 3365, "plane": 0})
    monkeypatch.setattr(dst, "_walk_to_net", lambda ctx, net: True)
    monkeypatch.setattr(dst, "_dismiss_dialogue_if_open", lambda ctx: False)
    monkeypatch.setattr(outcome_waits, "tick_pause",
                        lambda ctx, n=1, phase=None, **kw: dst._sleep(0.6 * n))


XP_SAIL = ("XP_GAINED", {"skill": "Sailing", "xp": 7})
XP_FISH = ("XP_GAINED", {"skill": "Fishing", "xp": 112})


def test_burst_clicks_once_per_tick_while_the_heartbeat_keeps_arriving(monkeypatch):
    # Sailing xp every other poll -- the 2-tick roll cadence a 1-tick click produces.
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL], [], [XP_SAIL, XP_FISH], [], [XP_SAIL], []] +
                     [[]] * 20)
    clicks = []
    _stub_burst_io(monkeypatch, clicks)
    monkeypatch.setattr(dst, "RANGE_LOST_TICKS", 6)

    result = dst.trawl_burst(ctx, CFG, NET_OBJ)

    assert result == "out_of_range"          # the heartbeat stops once the script runs dry
    assert len(clicks) >= 6                  # one per tick until then, not one per roll
    assert ctx.state.data["dst_catches"] >= 1


def test_burst_returns_net_full_at_the_configured_threshold(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    _stub_burst_io(monkeypatch, clicks, net_fish=lambda: CFG["netFullAt"])
    assert dst.trawl_burst(ctx, CFG, NET_OBJ) == "net_full"


def test_burst_advances_its_event_mark_and_so_never_reads_the_same_drop_twice(monkeypatch):
    """The C2 rule: mark is taken ONCE before the loop and advanced after every read. If it were
    re-taken at the top of each iteration the events delivered during the tick wait would be
    skipped and the heartbeat would look dead on the very first idle poll."""
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL, XP_SAIL], [XP_SAIL]] + [[]] * 20)
    clicks = []
    _stub_burst_io(monkeypatch, clicks)
    monkeypatch.setattr(dst, "RANGE_LOST_TICKS", 3)
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    # Three Sailing drops were delivered; each must have been counted exactly once.
    assert ctx.state.data["dst_rolls"] == 3


def test_burst_stops_for_a_safety_stop(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    _stub_burst_io(monkeypatch, clicks)

    def stop_soon(ser):
        clicks.append(1)
        if len(clicks) == 4:
            ctx.state.trigger_safety_stop("test")
    monkeypatch.setattr(controller, "click_human", stop_soon)

    assert dst.trawl_burst(ctx, CFG, NET_OBJ) == "interrupted"
    assert len(clicks) <= 6


def test_burst_reaims_when_the_player_has_moved(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    hovers = []
    _stub_burst_io(monkeypatch, clicks, moved=lambda: len(clicks) >= 3)
    monkeypatch.setattr(controller, "hover_entity", lambda *a, **k: hovers.append(1) or True)
    monkeypatch.setattr(dst, "BURST_MAX_S", 6.0)
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert len(hovers) >= 2                 # the initial aim plus at least one re-aim
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_deep_sea_trawling.py -q -k burst`
Expected: FAIL — `AttributeError: module 'deep_sea_trawling' has no attribute 'trawl_burst'`.

- [ ] **Step 3: Implement**

Append to `ControllerAddon/routines/deep_sea_trawling.py`:

```python
# --------------------------------------------------------------------------- the burst

# Ticks without a Sailing XP drop before we conclude the shoal has left. The game rolls every 3
# ticks unclicked and every 2 clicked, so 6 is two missed rolls' worth of slack -- tight enough
# to start chasing within ~4s of the shoal moving, loose enough to ride a dropped UDP packet.
RANGE_LOST_TICKS = 6
BURST_MAX_S = 120.0          # longer than any shoal's stop (krill 90s); a ceiling, not a timer
AIM_REFRESH_TICKS = 12       # re-aim on a schedule even if nothing looks like it moved
NET_POLL_TICKS = 8           # how often the burst spends an HTTP read on /trawling.netFish
CHECK_TICKS = 5              # how often pause / safety / break_due are re-checked
CLICK_PHASE_S = (0.02, 0.12)  # jitter after the tick boundary so clicks are not all at +0ms


def _walk_to_net(ctx, net):
    """Stand at the net. The deck of a sloop is a few tiles across, so this is one short click,
    not a route. Returns True once the click was issued -- the game queues the interaction and
    performs it on arrival, so the burst's first Operate can follow immediately."""
    return bool(controller.click_entity(ctx.ser, net, refresh_target=net_refresh_target(net),
                                        simulate_mistakes=False))


def _dismiss_dialogue_if_open(ctx):
    """The engine's own stray-dialogue check cannot run while the burst holds the tick, and a
    level-up ('click here to continue') swallows every click behind it. Returns True if one was
    dismissed."""
    state = controller.get_state() or {}
    if state.get("dialogOpen"):
        controller.key_tap(ctx.ser, "SPACE")
        return True
    return False


def _interrupted(ctx, cfg):
    state = ctx.state
    if getattr(state, "safety_stopped", False) or getattr(state, "paused", False):
        return True
    try:
        return bool(state.break_due(cfg.get("breaks")))
    except Exception:
        return False


def trawl_burst(ctx, cfg, net) -> str:
    """Operate the net once per game tick for as long as the shoal stays in range.

    Returns "out_of_range" (the heartbeat stopped -- the shoal has moved on), "net_full",
    "interrupted" (pause / safety stop / a break came due) or "budget".

    Three deliberate choices, each of which a previous routine paid for the hard way:
      - Aim ONCE, then click IN PLACE. The boat is stopped and the player is standing at the
        net, so its projection is static; a fresh click_entity flight per tick costs ~1s and
        cannot hold a 0.6s cadence (the mahogany-homes hotspot-burst lesson).
      - NO simulated misclick. A sloop deck is a few tiles across and MISCLICK_OFFSET_PX
        (20-45px) is wider than the net, so a "miss" lands on another facility or walks the
        player off the net -- the same rule banker.py's bank buttons and
        sailing._FIXTURE_CLICK_KWARGS follow.
      - The HEARTBEAT is the range test, not distance. Distance to a moving 6x6 object is a
        guess; a Sailing XP drop is the game's own answer to "is this net catching".
    """
    if not _walk_to_net(ctx, net):
        debug_log.log("trawling", "couldn't click the net to stand at it")
        return "interrupted"

    refresh = net_refresh_target(net)
    controller.hover_entity(ctx.ser, net, refresh_target=refresh)

    data = ctx.state.data
    data.setdefault("dst_rolls", 0)
    data.setdefault("dst_catches", 0)

    events = getattr(ctx, "events", None)
    mark = events.mark() if events is not None else None

    started = _now()
    ticks = 0
    ticks_since_roll = 0
    last_tile = (None, None)
    last_aim_tick = 0

    while _now() - started < BURST_MAX_S:
        outcome_waits.tick_pause(ctx, 1, phase=CLICK_PHASE_S, now_fn=_now, sleep_fn=_sleep)
        controller.click_human(ctx.ser)
        ticks += 1

        rolled = False
        if events is not None:
            batch = events.since(mark)
            mark = events.mark()
            for ev_type, payload in batch:
                if ev_type != "XP_GAINED":
                    continue
                skill = (payload or {}).get("skill") or ""
                if skill.lower() == "sailing":
                    rolled = True
                    data["dst_rolls"] += 1
                elif skill.lower() == "fishing":
                    data["dst_catches"] += 1
        ticks_since_roll = 0 if rolled else ticks_since_roll + 1

        if _interrupted(ctx, cfg):
            return "interrupted"

        if ticks % CHECK_TICKS == 0 and _dismiss_dialogue_if_open(ctx):
            controller.hover_entity(ctx.ser, net, refresh_target=refresh)
            last_aim_tick = ticks

        player = controller.get_player() or {}
        tile = (player.get("x"), player.get("y"))
        if tile != last_tile or (ticks - last_aim_tick) >= AIM_REFRESH_TICKS:
            if last_tile != (None, None) or (ticks - last_aim_tick) >= AIM_REFRESH_TICKS:
                controller.hover_entity(ctx.ser, net, refresh_target=refresh)
                last_aim_tick = ticks
            last_tile = tile

        if ticks % NET_POLL_TICKS == 0:
            fish = (controller.get_trawling() or {}).get("netFish")
            if fish is not None and fish >= cfg["netFullAt"]:
                debug_log.log("trawling", f"nets at {fish} -- collecting")
                return "net_full"

        if events is None:
            # No pushed heartbeat: fall back on the net count as the "still catching" signal.
            if ticks % NET_POLL_TICKS == 0:
                ticks_since_roll = 0
        elif ticks_since_roll >= RANGE_LOST_TICKS:
            debug_log.log("trawling",
                          f"no Sailing xp for {ticks_since_roll} ticks -- the shoal has moved")
            return "out_of_range"

    return "budget"
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_deep_sea_trawling.py -q`
Expected: all PASS. If `test_burst_reaims_when_the_player_has_moved` fails because the initial
`last_tile` sentinel suppresses the first re-aim, that is the bug the test is for — make the
re-aim fire whenever the tile *changes* after the first reading, without firing spuriously on
the very first iteration.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/deep_sea_trawling.py \
        ControllerAddon/tests/test_deep_sea_trawling.py
git commit -m "deep_sea_trawling: the one-click-per-tick net burst, gated on the Sailing xp heartbeat"
```

---

### Task 6: Phases, `tick`, `before_break`, startup checks and the stall watchdog

**Files:**
- Modify: `ControllerAddon/routines/deep_sea_trawling.py`
- Test: `ControllerAddon/tests/test_deep_sea_trawling.py`

**Interfaces:**
- Consumes: everything from Tasks 3-5; `sailing.sail_to`; `controller.click_menu_action`.
- Produces (the routine contract the engine calls):
  `setup(ctx)`, `tick(ctx)`, `on_event(ctx, event_type, payload)`, `before_break(ctx)`.
  Plus the phase handlers `travel_phase`, `approach_phase`, `lower_phase`, `trawl_phase`,
  `collect_phase`, `done_phase`, each `(ctx, cfg, snap) -> bool` where the return says whether
  the phase made progress worth chaining on.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_deep_sea_trawling.py`:

```python
import sailing


def test_lower_phase_clicks_the_menu_option_and_confirms_on_the_depth_varbit(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[("VARBIT_CHANGED", {"varbitId": 19206, "value": 1})]])
    calls = []
    depth = {"v": 0}
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_trawling",
                        lambda: {"nets": [{"slot": 0, "depth": depth["v"], "hotspotId": 3}]})

    def lower(ser, ent, option, **kw):
        calls.append(option)
        depth["v"] = 1
        return True
    monkeypatch.setattr(controller, "click_menu_action", lower)

    assert dst.lower_phase(ctx, CFG, _snap(net_depth=0)) is True
    assert calls == ["Lower"]


def test_lower_phase_stops_when_the_net_never_goes_down(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]] * 20)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_trawling",
                        lambda: {"nets": [{"slot": 0, "depth": 0, "hotspotId": 3}]})
    assert dst.lower_phase(ctx, CFG, _snap(net_depth=0)) is False
    assert ctx.state.safety_stopped


def test_collect_phase_takes_collect_from_and_confirms_the_net_emptied(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[("INVENTORY_CHANGED", {})]])
    fish = {"n": 240}
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": fish["n"]})
    monkeypatch.setattr(dst, "_answer_collect_dialogue", lambda ctx, cfg: fish.__setitem__("n", 0))
    seen = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **kw: seen.append(option) or True)

    assert dst.collect_phase(ctx, CFG, _snap(net_fish=240)) is True
    assert seen == ["Collect-from"]
    assert fish["n"] == 0


def test_approach_phase_sails_at_the_live_shoal_when_it_is_sitting_still(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    targets = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: targets.append(t) or True)
    snap = _snap(shoal_distance=30.0, shoal={"id": 59734, "x": 1574, "y": 3338, "plane": 0})
    assert dst.approach_phase(ctx, CFG, snap) is True
    assert targets == [{"x": 1574, "y": 3338, "plane": 0}]


def test_travel_phase_aims_at_the_stop_after_the_one_we_last_fished(monkeypatch):
    """The route table is an ACCELERATOR: with the shoal out of sight we head for where it is
    going next, so we arrive with it instead of trailing it."""
    ctx = _burst_ctx(monkeypatch, [[]])
    ctx.state.data["dst_last_stop_index"] = 3
    targets = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: targets.append(t) or True)
    assert dst.travel_phase(ctx, CFG, _snap(shoal=None, shoal_distance=None)) is True
    assert targets == [{"x": 1580, "y": 3379, "plane": 0}]   # Great Sound stop index 4


def test_travel_phase_with_no_history_aims_at_the_nearest_stop(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    targets = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: targets.append(t) or True)
    assert dst.travel_phase(ctx, CFG, _snap(shoal=None, shoal_distance=None,
                                            px=1576, py=3340)) is True
    assert targets == [{"x": 1574, "y": 3338, "plane": 0}]


def test_a_lost_snapshot_is_a_safety_stop_naming_the_boat(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    assert dst.tick_with_snapshot(ctx, CFG, _snap(net=None)) is False
    assert ctx.state.safety_stopped
    assert "boat" in ctx.state.safety_reason.lower()


def test_repeated_out_of_range_bursts_eventually_stop_the_run(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "trawl_burst", lambda c, cfg, net: "out_of_range")
    for _ in range(dst.APPROACH_MAX_TRIES):
        dst.trawl_phase(ctx, CFG, _snap())
    assert ctx.state.safety_stopped


def test_before_break_collects_the_nets_first(monkeypatch):
    """Fish in the nets are discarded on a forced logout, and a break is minutes long."""
    ctx = _burst_ctx(monkeypatch, [[]])
    collected = []
    monkeypatch.setattr(dst, "collect_phase", lambda c, cfg, s: collected.append(1) or True)
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg, **kw: _snap(net_fish=17))
    dst.before_break(ctx)
    assert collected == [1]

    collected.clear()
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg, **kw: _snap(net_fish=0))
    dst.before_break(ctx)
    assert collected == []


def test_stuck_watch_warns_then_stops(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    snap = _snap()
    dst._stuck_watch(ctx, CFG, snap, "trawl")
    ctx.clock["t"] += dst.STALL_STOP_S + 1
    dst._stuck_watch(ctx, CFG, snap, "trawl")
    assert ctx.state.safety_stopped
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_deep_sea_trawling.py -q -k "phase or break or stuck"`
Expected: FAIL — the phase functions do not exist.

- [ ] **Step 3: Implement**

Append to `ControllerAddon/routines/deep_sea_trawling.py`:

```python
# --------------------------------------------------------------------------- phases

SAIL_MAX_TRIES = 3
APPROACH_MAX_TRIES = 6
LOWER_TRIES = 3
COLLECT_TRIES = 3
NET_DEPTH_VARBIT = 19206
STALL_WARN_S = 180.0
STALL_STOP_S = 420.0
TICK_CHAIN_MAX = 4
COLLECT_CONFIRM_S = 6.0
LOWER_CONFIRM_S = 4.0


def _stop(ctx, reason):
    # trigger_safety_stop appends its own ". Automation paused -- fix the issue and restart."
    ctx.state.trigger_safety_stop(reason)


def travel_phase(ctx, cfg, snap) -> bool:
    """No shoal in the loaded scene. Head for a route stop -- the next one after the one we last
    fished if we know it (the table is an accelerator: we arrive with the shoal rather than
    behind it), otherwise the nearest."""
    if not snap.in_route_area:
        _stop(ctx, f"not on the {cfg['route']} route -- start this routine with the boat there")
        return False

    last = ctx.state.data.get("dst_last_stop_index")
    if last is None:
        idx, point, _dist = trawling_routes.nearest_stop(cfg["shoal"], cfg["route"],
                                                         snap.px, snap.py)
    else:
        idx, point = trawling_routes.next_stop(cfg["shoal"], cfg["route"], last)
    ctx.state.data["dst_heading_for"] = idx
    debug_log.log("trawling", f"no shoal in sight -- sailing to stop {idx} {point}")
    return _sail(ctx, cfg, {"x": point[0], "y": point[1], "plane": snap.plane or 0})


def approach_phase(ctx, cfg, snap) -> bool:
    """The shoal is in the scene but we are out of range, or the boat is still moving. Sail at
    the shoal's LIVE tile -- the object in /objects is the truth, the route table only fills in
    when the shoal is not visible at all (travel_phase)."""
    idx, _point, dist = trawling_routes.nearest_stop(cfg["shoal"], cfg["route"],
                                                     snap.shoal["x"], snap.shoal["y"])
    if dist <= cfg["trawlRange"]:
        ctx.state.data["dst_last_stop_index"] = idx
    target = {"x": snap.shoal["x"], "y": snap.shoal["y"], "plane": snap.plane or 0}
    return _sail(ctx, cfg, target)


def _sail(ctx, cfg, target) -> bool:
    data = ctx.state.data
    if sailing.sail_to(ctx, target):
        data["dst_sail_fails"] = 0
        return True
    data["dst_sail_fails"] = data.get("dst_sail_fails", 0) + 1
    debug_log.log("trawling",
                  f"couldn't sail to {target} ({data['dst_sail_fails']}/{SAIL_MAX_TRIES})")
    if data["dst_sail_fails"] >= SAIL_MAX_TRIES:
        _stop(ctx, "the boat couldn't reach the shoal after repeated attempts")
    return False


def lower_phase(ctx, cfg, snap) -> bool:
    """Put the net in the water. The net loc's op3 is Lower, so this is a named menu entry, not
    the left-click default (which is Operate)."""
    net = snap.net
    for _ in range(LOWER_TRIES):
        controller.click_menu_action(ctx.ser, net, "Lower",
                                     refresh_target=net_refresh_target(net), offset=(0, 0))
        out = outcome_waits.await_pre(
            ctx, ("VARBIT_CHANGED",), LOWER_CONFIRM_S,
            confirm=lambda: bool(_built_net_depth(controller.get_trawling() or {})),
            now_fn=_now, sleep_fn=_sleep)
        if out:
            debug_log.log("trawling", "net lowered")
            return True
    _stop(ctx, "the trawling net never went down")
    return False


def _answer_collect_dialogue(ctx, cfg):
    """Collect-from asks where the fish should go. Press the number key for the configured
    destination; a Space also advances the plain 'you collect N fish' line that follows.

    LIVE-VERIFY (spec checklist #7): the interface and the exact option text are UNCONFIRMED.
    Option 1 is assumed to be the inventory and option 2 the cargo hold; if the live dialogue
    disagrees, fix COLLECT_KEYS, not the caller."""
    key = COLLECT_KEYS.get(cfg["collectTo"], "2")
    controller.key_tap(ctx.ser, key)
    _sleep(controller.sample_human_delay(0.3, 0.5))
    controller.key_tap(ctx.ser, "SPACE")


COLLECT_KEYS = {"inventory": "1", "cargo": "2"}


def collect_phase(ctx, cfg, snap) -> bool:
    net = snap.net
    for _ in range(COLLECT_TRIES):
        controller.click_menu_action(ctx.ser, net, "Collect-from",
                                     refresh_target=net_refresh_target(net), offset=(0, 0))
        _answer_collect_dialogue(ctx, cfg)
        out = outcome_waits.await_pre(
            ctx, ("INVENTORY_CHANGED", "VARBIT_CHANGED"), COLLECT_CONFIRM_S,
            confirm=lambda: ((controller.get_trawling() or {}).get("netFish") or 0) == 0,
            now_fn=_now, sleep_fn=_sleep)
        if out:
            debug_log.log("trawling", "nets emptied")
            return True
    _stop(ctx, "couldn't empty the trawling nets")
    return False


def trawl_phase(ctx, cfg, snap) -> bool:
    result = trawl_burst(ctx, cfg, snap.net)
    data = ctx.state.data
    if result == "out_of_range":
        data["dst_approaches"] = data.get("dst_approaches", 0) + 1
        if data["dst_approaches"] >= APPROACH_MAX_TRIES:
            _stop(ctx, "the nets stopped catching and the boat couldn't get back to the shoal")
            return False
    else:
        data["dst_approaches"] = 0
    return result in ("net_full", "out_of_range", "budget")


def done_phase(ctx, cfg, snap) -> bool:
    controller.click_menu_action(ctx.ser, snap.net, "Raise",
                                 refresh_target=net_refresh_target(snap.net), offset=(0, 0))
    _stop(ctx, f"the cargo hold is full -- {ctx.state.data.get('dst_catches', 0)} catches this run")
    return False


PHASES = {
    "travel": travel_phase,
    "approach": approach_phase,
    "lower": lower_phase,
    "trawl": trawl_phase,
    "collect": collect_phase,
    "done": done_phase,
}


# --------------------------------------------------------------------------- watchdog


def _progress_signature(snap, phase):
    return (phase, snap.net_fish, snap.cargo_used, snap.px, snap.py)


def _stuck_watch(ctx, cfg, snap, phase):
    data = ctx.state.data
    sig = _progress_signature(snap, phase)
    if data.get("dst_sig") != sig:
        data["dst_sig"] = sig
        data["dst_sig_at"] = _now()
        data["dst_warned"] = False
        return
    stalled = _now() - data.get("dst_sig_at", _now())
    if stalled >= STALL_STOP_S:
        _stop(ctx, f"nothing has changed for {int(stalled)}s in phase {phase}")
    elif stalled >= STALL_WARN_S and not data.get("dst_warned"):
        data["dst_warned"] = True
        debug_log.log("trawling", f"[!] trawling: STUCK? {int(stalled)}s in phase {phase}")


# --------------------------------------------------------------------------- routine contract


def setup(ctx):
    ctx.state.data.setdefault("dst_started_at", _now())
    _startup_checks(ctx, ctx.state.config or DEFAULT_CONFIG)


def _startup_checks(ctx, cfg):
    """Deferred until the client answers with real data -- the first logged-in tick after an
    auto-login can serve an empty scene, and stopping on that is the GOTR 'no pickaxe with a
    pickaxe equipped' bug."""
    snap = read_snapshot(ctx, cfg)
    if snap.px is None:
        return
    if snap.net is None:
        _stop(ctx, "start this routine standing on your boat, which needs a trawling net built")
        return
    if not snap.navigating:
        _stop(ctx, "no crewmate is holding the helm -- assign one so the boat can be steered "
                   "from the net")
        return
    if not snap.in_route_area:
        _stop(ctx, f"not on the {cfg['route']} route -- start this routine with the boat there")


def on_event(ctx, event_type, payload):
    """Only used between bursts -- the engine never dispatches this while tick() is held, which
    is why trawl_burst reads ctx.events.since(mark) itself."""
    if event_type == "XP_GAINED" and (payload or {}).get("skill", "").lower() == "sailing":
        ctx.state.data["dst_last_sailing_xp_at"] = _now()


def tick(ctx):
    cfg = ctx.state.config or DEFAULT_CONFIG
    for _ in range(TICK_CHAIN_MAX):
        snap = read_snapshot(ctx, cfg, ticks_since_sailing_xp=_ticks_since_heartbeat(ctx))
        if not tick_with_snapshot(ctx, cfg, snap):
            return
        if _interrupted(ctx, cfg):
            return


def _ticks_since_heartbeat(ctx):
    at = ctx.state.data.get("dst_last_sailing_xp_at")
    return 0.0 if at is None else (_now() - at) / 0.6


def tick_with_snapshot(ctx, cfg, snap) -> bool:
    phase = derive_phase(snap, cfg)
    if phase != ctx.state.data.get("dst_phase"):
        debug_log.log("trawling", f"phase={phase} netFish={snap.net_fish} "
                                  f"shoalDist={snap.shoal_distance}")
        ctx.state.data["dst_phase"] = phase
    _stuck_watch(ctx, cfg, snap, phase)
    if getattr(ctx.state, "safety_stopped", False):
        return False
    if phase == "lost":
        _stop(ctx, "start this routine standing on your boat, which needs a trawling net built")
        return False
    return bool(PHASES[phase](ctx, cfg, snap))


def before_break(ctx):
    """Empty the nets before a break: fish left in them are DISCARDED on a forced logout or a
    disconnect, and a break is minutes long (same reason chinchompa_hunting picks its traps up
    here)."""
    cfg = ctx.state.config or DEFAULT_CONFIG
    snap = read_snapshot(ctx, cfg)
    if snap.net is not None and (snap.net_fish or 0) > 0:
        collect_phase(ctx, cfg, snap)
```

- [ ] **Step 4: Run the whole suite**

Run from `ControllerAddon/`: `python -m pytest tests/test_deep_sea_trawling.py tests/test_trawling_routes.py -q`
Expected: all PASS. Then `python -m py_compile routines/deep_sea_trawling.py trawling_routes.py`
(the CLAUDE.md rule: pytest alone does not catch a syntax error in a module no test imports).

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/deep_sea_trawling.py \
        ControllerAddon/tests/test_deep_sea_trawling.py
git commit -m "deep_sea_trawling: phases, tick chaining, before_break and the stall watchdog"
```

---

### Task 7: Wiring, documentation and a full-suite green

**Files:**
- Modify: `ControllerAddon/controller.py` (`ROUTINE_FILES`, around line 3159)
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `CLAUDE.md`
- Modify: `C:\Users\Kiyo\.claude\projects\c--Users-Kiyo-Documents-Httpbridge\memory\MEMORY.md` and a new memory file in that directory

**Interfaces:**
- Consumes: everything above.
- Produces: the routine is selectable from the RuneLite Controller Settings dropdown.

- [ ] **Step 1: Wire `ROUTINE_FILES`**

In `ControllerAddon/controller.py`, add to the `ROUTINE_FILES` dict, after the
`"KARAMBWAN_FISHING"` line:

```python
    "DEEP_SEA_TRAWLING": "routines/deep_sea_trawling.py",
```

- [ ] **Step 2: Wire `RoutineChoice`**

In `src/main/java/com/example/controller/RoutineChoice.java`, change the last enum constant's
terminating `;` to `,` and append:

```java
	DEEP_SEA_TRAWLING("Deep Sea Trawling");
```

so the tail reads `KARAMBWAN_FISHING("Karambwan Fishing (DKP + fish barrel)"),` then
`DEEP_SEA_TRAWLING("Deep Sea Trawling");`.

- [ ] **Step 3: Compile the Java and run the full Python suite**

Run from the repo root: `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`.

Run from `ControllerAddon/`: `python -m pytest tests/ -q`
Expected: all PASS. (The suite takes several minutes — `test_chinchompa_hunting.py` sleeps for
real.) Also run `python -m py_compile routines/deep_sea_trawling.py trawling_routes.py controller.py`.

- [ ] **Step 4: Add the CLAUDE.md section**

In `CLAUDE.md`, insert a new section immediately **before** `## Current state of routines/blast_furnace.py`:

```markdown
## Current state of routines/deep_sea_trawling.py

Written 2026-09-06 from the OSRS Wiki (Deep sea trawling rev 15297127, Giant krill shoal rev
15237362, Crew Management rev 15290062), the decoded cache (dump `5bc2ff1e`) and
`runelite-api-1.12.38`. Design: `docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md`,
whose "Live-verification checklist" is the first session's worklist. **There is no human
recording and NOTHING has been run live.** Needs a client rebuild (`GET /trawling` and the
Routine dropdown entry are both Java).

- **The point of the routine is one click per game tick.** The game rolls for a catch every 3
  ticks while a net operates in range; clicking the net every tick makes it roll every 2 -- a
  flat +50%. `trawl_burst` therefore aims once and then calls `click_human` (in place, ~92ms) on
  the tick clock; a fresh `click_entity` flight per tick costs ~1s and cannot hold the cadence.
  No simulated misclick in the burst: a sloop deck is a few tiles across and
  `MISCLICK_OFFSET_PX` is wider than the net.
- **The range test is the game's own Sailing XP drop, not distance.** Every roll grants a small
  Sailing XP drop (a successful one also grants Fishing XP), so a pushed `XP_GAINED` proves the
  net is operating in range; `RANGE_LOST_TICKS` (6) without one means the shoal has moved.
  Distance to a moving 6x6 object only ever *aims* the boat.
- **The burst holds `tick()` for up to 90s, so `on_event` never runs during it** -- it reads
  `ctx.events.since(mark)` and advances `mark` after every read (the Hunllef C2 rule; re-taking
  `mark()` per iteration drops everything the tick wait pumped past, i.e. the whole heartbeat).
- **Target is Giant krill / Great Sound, and that is a level decision.** At Fishing 75 /
  Sailing 64 the account can reach only shallow shoals (krill 69, haddock 73) and only the Rope
  net, so **v1 has no depth management at all**. Great Sound's 7 stops fit in ~56x49 tiles, i.e.
  inside one loaded 104x104 scene, so the shoal is essentially always in `/objects`; Haddock's
  routes span five regions and the shoal leaves the scene.
- **`trawling_routes.py` holds the Wiki's stop tables as raw `(regionId, rx, ry)` markers.** It
  is an ACCELERATOR, never truth -- the live shoal object decides where the shoal is; the table
  only says where to sail when none is visible. The stored order is the Wiki's own `label`
  field, and the pinned test that every leg is traversable at <= 2.5 tiles/tick is the evidence
  that label order *is* visiting order (implied speeds 1.18-1.77 across three routes).
- **`GET /trawling` (TrawlingProvider.java) exists for exactly two things**: item container 968
  (`sailing_trawling_net`, size 250, the per-player fish count) and 963..967 (the cargo hold).
  Nothing else on the bridge can read them and no m8aq reader covers sailing. `netFish` is
  `None`, not 0, when the container is missing -- that is "not aboard a boat with a net".
- **Hard precondition: a crewmate on the helm.** Helmsmanship lets a crewmate hold the helm so
  the player can stand at another facility while still clicking the heading (Wiki, Crew
  Management); without one the player must stand at the helm and cannot trawl at the same time.
  `_startup_checks` reads `/sailing.navigating` for this.
- **Never world-hop, never teleport, never disembark with fish in the nets** -- they are
  discarded without a warning. `before_break` empties the nets before a break starts.
- Out of scope for v1, deliberately: banking at a bank boat (the 240-slot hold lasts many hours
  at ~5 fish/min), chum-station baiting (+20% fish, but the station's `Operate` interface is
  unseen), depth management, fish crates, and a second net.
```

- [ ] **Step 5: Add the memory entry**

Create `C:\Users\Kiyo\.claude\projects\c--Users-Kiyo-Documents-Httpbridge\memory\deep-sea-trawling-state.md`:

```markdown
---
name: deep-sea-trawling-state
description: Deep sea trawling routine built 2026-09-06 from the Wiki/cache only, never run live; needs a client rebuild
metadata:
  type: project
---

`routines/deep_sea_trawling.py`, `trawling_routes.py` and `GET /trawling`
(`TrawlingProvider.java`) were written 2026-09-06 from the OSRS Wiki and the decoded cache.
**Nothing has been run live and there is no human recording.** A client rebuild
(`./gradlew.bat run`) is required before `/trawling` exists or the routine appears in the
Routine dropdown.

The first session's worklist is the "Live-verification checklist" in
`docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md`, in order: does `/objects` list
the shoal and under what name, how a moving shoal appears (one object or a despawn/spawn pair
per tick), what "in range" actually is, and whether the Sailing XP heartbeat really lands every
2 ticks under a one-click-per-tick burst -- that last one is the routine's whole premise.

**Why:** the design is entirely reasoned, so geometry and widget facts are the things most
likely to be wrong before any logic is. **How to apply:** treat every constant in
`DEFAULT_CONFIG` and the `trawl_burst` timing block as a first draft to be measured, and check
the spec's checklist before debugging the routine's logic. Related: [[gauntlet-routine-state]],
[[event-driven-outcomes-state]].
```

Then append one line to `MEMORY.md` in that same directory:

```markdown
- [Deep sea trawling state](deep-sea-trawling-state.md) — built 2026-09-06 from Wiki/cache only, never run live; 1-click-per-tick net burst on the Sailing xp heartbeat; rebuild needed
```

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/controller.py \
        src/main/java/com/example/controller/RoutineChoice.java CLAUDE.md
git commit -m "deep_sea_trawling: wire into the routine dropdown and document the build"
```

(The memory files live outside the repo and are not committed.)

---

## Notes for the executor

- **When a plan-authored test and a sourced fact disagree, check the source before changing the
  code to satisfy the test.** Every game fact in this plan traces to the spec, which cites the
  Wiki revision or the cache dump commit it came from. Plan-authored tests were wrong more often
  than the code they tested on the last project built this way.
- **Do not "unify" the two diagonal rules, the two collision maps, or anything else CLAUDE.md
  warns against.** None of them are in scope here, but the same instinct applies to this
  routine's own near-duplicates: `travel_phase` (no shoal visible → the route table) and
  `approach_phase` (shoal visible → its live tile) look alike and are deliberately different.
- **Never run two implementers in the same checkout**, and commit before dispatching one.
