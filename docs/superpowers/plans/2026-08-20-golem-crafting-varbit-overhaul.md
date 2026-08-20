# Golem Crafting Varbit Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `craft_phase`'s guess-and-check side tracking with authoritative varbit reads from a new `/golem` bridge endpoint, and remove the per-golem waste (extra walk, ground-loot scan, plinth-reset wait).

**Architecture:** A new `GolemProvider` mirrors the game's own golem-crafting varbits onto `GET /golem` — per-side `done`/`progress` for both plinths, plus each side's world tile. `craft_phase` becomes a deterministic function of that state: pick a station, compute a carve order that ends on the insert side, carve what is genuinely not done, insert. All decision logic is extracted into pure functions that are unit-tested without a client or serial port.

**Tech Stack:** Java 11 (RuneLite plugin, Gradle), Python 3 (`requests`, `pytest`), RuneLite `net.runelite.api.gameval.VarbitID`.

**Spec:** `docs/superpowers/specs/2026-08-20-golem-crafting-varbit-overhaul-design.md`

## Global Constraints

- **Java changes require a client rebuild + restart** (`./gradlew.bat run` from repo root) before the Python side can see them. Tasks 3–5 cannot be live-verified until Task 1 is built and running.
- **Run the tests from `ControllerAddon/`**: `python -m pytest tests/`.
- **`py_compile` every touched Python file before any live run.** Pytest alone is insufficient — some modules are not imported by any test, so a syntax error there surfaces only in a direct compile check.
- **Never use `client.getVarbitValue` off the client thread.** All reads go through `BridgeProvider.onClient(...)`.
- **`/golem` must NOT be `TickCache`-gated.** It is a confirmation signal and must read fresh every request.
- **Material cost is 4 raw sunstone + 1 core per golem.** `SUNSTONE_PER_TRIP = 20` and `CORES_PER_TRIP = 4` stay unchanged — they already balance exactly.
- **Station facts** (verbatim, do not re-derive):
  - north: object `62351`, golem tile `(2596, 2257)`, insert side `south`
  - south: object `62352`, golem tile `(2595, 2253)`, insert side `north`
- **Only `east` and `west` are valid carve start sides** for either plinth.
- Bash tool is Git Bash (POSIX sh). Use heredocs for multi-line commit messages, never PowerShell here-strings.
- **All line numbers in this plan refer to `golem_crafting.py` as it stands BEFORE Task 2.** Task 2 inserts ~90 lines after `find_item`, shifting everything below it. From Task 3 onward, locate code by function/constant name, not by line number.

---

### Task 1: `GolemProvider` → `GET /golem`, plus `craftingXp`

Java-only. Deliverable: the bridge reports live golem state. Per this codebase's convention, providers have no unit tests — verification is a live read.

**Files:**
- Create: `src/main/java/com/example/providers/GolemProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:123` (registration block)
- Modify: `src/main/java/com/example/providers/PlayerProvider.java:156` (after `thievingXp`)

**Interfaces:**
- Consumes: nothing.
- Produces: `GET /golem` returning `{inArea: bool, totalCrafted: int, busy: int, stations: [Station, Station]}` where `Station` is `{name: "north"|"south", objectId: int, x: int, y: int, plane: int, progress: int, insertSide: "north"|"south", sides: {north|east|south|west: {done: bool, progress: int, x: int, y: int, plane: int}}}`. Station order in the list is north, then south. Also adds `craftingXp` (int) to `GET /player`.

- [ ] **Step 1: Create the provider**

Create `src/main/java/com/example/providers/GolemProvider.java`:

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Mirrors Wyrmscraig's golem-crafting state onto GET /golem, read straight from RuneLite's own
 * generated VarbitID constants -- confirmed present in this project's runelite-api jar via direct
 * javap inspection, the same method MixologyProvider documents. Semantics were cross-referenced
 * against the decompiled "Golem Crafting" plugin-hub plugin (org.zhbot.golem_crafting); that
 * plugin is NOT a runtime dependency, it was documentation only.
 *
 * Deliberately NOT TickCache-gated (unlike /objects, /npcs and the other bulk list endpoints):
 * routines/golem_crafting.py polls this as its click-confirmation signal, so a stale read would
 * reintroduce exactly the lag the varbits are here to remove.
 *
 * Deliberately exposes no policy -- no "which side next", no material verdict. Which side to
 * carve depends on where the player is standing, which is the Python side's business. This is a
 * state mirror, same as every other provider here.
 */
public class GolemProvider extends BridgeProvider
{
	// Matches the reference plugin's own area test.
	private static final WorldPoint AREA_CENTRE = new WorldPoint(2590, 2250, 0);
	private static final int AREA_RADIUS = 15;

	// No named ObjectID constants exist for these in runelite-api 1.12.36 (checked by javap --
	// the GOLEM_* ObjectID entries are all Desert Treasure's Golem quest). Raw ids, from the
	// reference plugin's NorthGolem/SouthGolem.
	private static final int NORTH_STATION_OBJECT_ID = 62351;
	private static final int SOUTH_STATION_OBJECT_ID = 62352;
	private static final WorldPoint NORTH_GOLEM_TILE = new WorldPoint(2596, 2257, 0);
	private static final WorldPoint SOUTH_GOLEM_TILE = new WorldPoint(2595, 2253, 0);

	// Insert-core is accepted from exactly one side per station, fixed per plinth.
	private static final String NORTH_STATION_INSERT_SIDE = "south";
	private static final String SOUTH_STATION_INSERT_SIDE = "north";

	public GolemProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/golem", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("inArea", inArea());
		data.put("totalCrafted", client.getVarbitValue(VarbitID.GOLEM_CRAFTING_COUNT));
		// Raw generic "player is busy" varbit. Its exact meaning for this activity is NOT
		// verified -- exposed for diagnosis only; nothing on the Python side depends on it.
		data.put("busy", client.getVarbitValue(VarbitID.BUSY));

		List<Map<String, Object>> stations = new ArrayList<>();
		stations.add(buildStation(
			"north", NORTH_STATION_OBJECT_ID, NORTH_GOLEM_TILE, NORTH_STATION_INSERT_SIDE,
			VarbitID.GOLEM_CRAFTING_STATION_1,
			VarbitID.GOLEM_CRAFTING_STATION_1_NORTH, VarbitID.GOLEM_CRAFTING_STATION_1_EAST,
			VarbitID.GOLEM_CRAFTING_STATION_1_SOUTH, VarbitID.GOLEM_CRAFTING_STATION_1_WEST,
			VarbitID.GOLEM_1_NORTH_PROGRESS, VarbitID.GOLEM_1_EAST_PROGRESS,
			VarbitID.GOLEM_1_SOUTH_PROGRESS, VarbitID.GOLEM_1_WEST_PROGRESS));
		stations.add(buildStation(
			"south", SOUTH_STATION_OBJECT_ID, SOUTH_GOLEM_TILE, SOUTH_STATION_INSERT_SIDE,
			VarbitID.GOLEM_CRAFTING_STATION_2,
			VarbitID.GOLEM_CRAFTING_STATION_2_NORTH, VarbitID.GOLEM_CRAFTING_STATION_2_EAST,
			VarbitID.GOLEM_CRAFTING_STATION_2_SOUTH, VarbitID.GOLEM_CRAFTING_STATION_2_WEST,
			VarbitID.GOLEM_2_NORTH_PROGRESS, VarbitID.GOLEM_2_EAST_PROGRESS,
			VarbitID.GOLEM_2_SOUTH_PROGRESS, VarbitID.GOLEM_2_WEST_PROGRESS));
		data.put("stations", stations);
		return data;
	}

	private boolean inArea()
	{
		Player local = client.getLocalPlayer();
		if (local == null)
		{
			return false;
		}
		// Wyrmscraig is not instanced, so the instance-template remapping trap documented in
		// CLAUDE.md does not apply here -- this is a plain proximity check.
		return RuneLiteUtil.getTopLevelWorldLocation(client, local).distanceTo(AREA_CENTRE) <= AREA_RADIUS;
	}

	private Map<String, Object> buildStation(
		String name, int objectId, WorldPoint tile, String insertSide, int progressVarbit,
		int northState, int eastState, int southState, int westState,
		int northProgress, int eastProgress, int southProgress, int westProgress)
	{
		Map<String, Object> station = new LinkedHashMap<>();
		station.put("name", name);
		station.put("objectId", objectId);
		station.put("x", tile.getX());
		station.put("y", tile.getY());
		station.put("plane", tile.getPlane());
		station.put("progress", client.getVarbitValue(progressVarbit));
		station.put("insertSide", insertSide);

		// Plain arithmetic on a constant tile -- NOT a LocalPoint.fromWorld() forward lookup, so
		// none of CLAUDE.md's forward-conversion caveats apply. OSRS world y increases northward.
		Map<String, Object> sides = new LinkedHashMap<>();
		sides.put("north", buildSide(northState, northProgress, tile.getX(), tile.getY() + 1, tile.getPlane()));
		sides.put("east", buildSide(eastState, eastProgress, tile.getX() + 1, tile.getY(), tile.getPlane()));
		sides.put("south", buildSide(southState, southProgress, tile.getX(), tile.getY() - 1, tile.getPlane()));
		sides.put("west", buildSide(westState, westProgress, tile.getX() - 1, tile.getY(), tile.getPlane()));
		station.put("sides", sides);
		return station;
	}

	private Map<String, Object> buildSide(int stateVarbit, int progressVarbit, int x, int y, int plane)
	{
		Map<String, Object> side = new LinkedHashMap<>();
		side.put("done", client.getVarbitValue(stateVarbit) != 0);
		// Raw 0-10 varbit value, not the reference plugin's /10.0 fraction -- this endpoint is a
		// state mirror, not a presentation layer.
		side.put("progress", client.getVarbitValue(progressVarbit));
		side.put("x", x);
		side.put("y", y);
		side.put("plane", plane);
		return side;
	}
}
```

- [ ] **Step 2: Register the provider**

In `src/main/java/com/example/controller/ControllerPlugin.java`, immediately after the `PyramidPlunderProvider` line (currently line 123), add:

```java
		new GolemProvider(client, clientThread, server).register();
```

- [ ] **Step 3: Add `craftingXp` to `PlayerProvider`**

In `src/main/java/com/example/providers/PlayerProvider.java`, immediately after the `thievingXp` line (currently line 156), add:

```java
		data.put("craftingXp", client.getSkillExperience(Skill.CRAFTING));
```

`Skill` is already imported in that file (it is used for `Skill.HITPOINTS` on the preceding lines). No new import is needed.

- [ ] **Step 4: Build**

Run from the repo root: `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`. If `VarbitID.GOLEM_CRAFTING_STATION_1` does not resolve, the `runeLiteVersion` in `gradle.properties` is older than 1.12.36 — stop and report rather than substituting raw integers.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/providers/GolemProvider.java \
        src/main/java/com/example/controller/ControllerPlugin.java \
        src/main/java/com/example/providers/PlayerProvider.java
git commit -F - <<'EOF'
Add GolemProvider exposing golem-crafting varbits on GET /golem

Mirrors both Wyrmscraig plinths' per-side done/progress varbits, overall
progress, insert side and each side's world tile. Deliberately not
TickCache-gated: routines/golem_crafting.py will poll this as its click
confirmation signal.

Also exposes craftingXp on /player, per CLAUDE.md's sub-tick XP pattern.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Pure planning logic (TDD)

All decision logic, as pure functions with no bridge or serial access. This is the whole reason the rewrite is testable.

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (add functions near the other module-level helpers, after `find_item`)
- Create: `ControllerAddon/tests/test_golem_crafting.py`

**Interfaces:**
- Consumes: the `Station` dict shape produced by Task 1.
- Produces, all in `routines/golem_crafting.py`:
  - `SIDES: tuple` = `("north", "east", "south", "west")`
  - `SUNSTONE_PER_GOLEM: int` = `4`, `CORES_PER_GOLEM: int` = `1`
  - `side_order(start: str, insert_side: str) -> list`
  - `can_end_on_insert(start: str, insert_side: str) -> bool`
  - `choose_start_side(station: dict, player_x: int, player_y: int) -> str`
  - `carve_plan(station: dict, player_x: int, player_y: int) -> list`
  - `choose_station(stations: list, last_station, player_x: int, player_y: int) -> dict`
  - `has_start_materials(raw_sunstone: int, cores: int) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_golem_crafting.py`:

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import golem_crafting as gc


def _station(name="north", x=2596, y=2257, insert_side="south", progress=0, done=()):
    """A /golem station dict with the real tile geometry, `done` naming carved sides."""
    offsets = {"north": (0, 1), "east": (1, 0), "south": (0, -1), "west": (-1, 0)}
    return {
        "name": name, "objectId": 62351 if name == "north" else 62352,
        "x": x, "y": y, "plane": 0, "progress": progress, "insertSide": insert_side,
        "sides": {
            side: {"done": side in done, "progress": 10 if side in done else 0,
                   "x": x + dx, "y": y + dy, "plane": 0}
            for side, (dx, dy) in offsets.items()
        },
    }


# --- side_order / can_end_on_insert -------------------------------------------------

@pytest.mark.parametrize("insert_side", ["south", "north"])
@pytest.mark.parametrize("start", ["east", "west"])
def test_east_west_starts_end_on_the_insert_side(start, insert_side):
    assert gc.side_order(start, insert_side)[-1] == insert_side


@pytest.mark.parametrize("insert_side", ["south", "north"])
def test_starting_on_or_opposite_the_insert_side_cannot_end_there(insert_side):
    opposite = {"north": "south", "south": "north"}[insert_side]
    assert not gc.can_end_on_insert(insert_side, insert_side)
    assert not gc.can_end_on_insert(opposite, insert_side)


def test_only_east_and_west_are_valid_starts_for_either_plinth():
    """The constraint that justifies restricting the start side at all: a four-step rotation
    lands on start+/-1, so start must neighbour the insert side. Asserted directly so a later
    'simplification' cannot quietly reintroduce a walk before Insert-core."""
    for insert_side in ("south", "north"):
        valid = [s for s in gc.SIDES if gc.can_end_on_insert(s, insert_side)]
        assert sorted(valid) == ["east", "west"]


def test_side_order_covers_all_four_sides_exactly_once():
    assert sorted(gc.side_order("east", "south")) == sorted(gc.SIDES)


# --- choose_start_side --------------------------------------------------------------

def test_choose_start_side_picks_the_nearer_of_east_and_west():
    station = _station()  # golem tile (2596, 2257); east (2597, 2257), west (2595, 2257)
    assert gc.choose_start_side(station, 2600, 2257) == "east"
    assert gc.choose_start_side(station, 2590, 2257) == "west"


def test_choose_start_side_never_returns_a_north_south_side():
    station = _station()
    for py in (2270, 2240):
        assert gc.choose_start_side(station, 2596, py) in ("east", "west")


# --- carve_plan ---------------------------------------------------------------------

def test_carve_plan_on_a_fresh_golem_lists_four_sides_ending_on_insert():
    station = _station()
    plan = gc.carve_plan(station, 2600, 2257)
    assert len(plan) == 4
    assert plan[-1] == "south"


def test_carve_plan_skips_already_done_sides():
    station = _station(done=("east", "north"))
    plan = gc.carve_plan(station, 2600, 2257)
    assert "east" not in plan and "north" not in plan
    assert sorted(plan) == ["south", "west"]


def test_carve_plan_returns_empty_when_all_sides_done():
    station = _station(done=gc.SIDES)
    assert gc.carve_plan(station, 2600, 2257) == []


def test_carve_plan_resumed_with_insert_side_already_done_does_not_end_on_it():
    """A resumed golem may not admit an east/west start. This is legal, not an error --
    craft_phase walks to insertSide afterwards."""
    station = _station(done=("south",))
    plan = gc.carve_plan(station, 2600, 2257)
    assert "south" not in plan
    assert len(plan) == 3


# --- choose_station -----------------------------------------------------------------

def test_choose_station_prefers_a_station_already_in_progress():
    idle = _station("north", progress=0)
    active = _station("south", 2595, 2253, "north", progress=2)
    assert gc.choose_station([idle, active], None, 2596, 2257)["name"] == "south"


def test_choose_station_prefers_the_more_advanced_when_both_in_progress():
    a = _station("north", progress=1)
    b = _station("south", 2595, 2253, "north", progress=3)
    assert gc.choose_station([a, b], None, 2596, 2257)["name"] == "south"


def test_choose_station_alternates_away_from_the_last_one_when_both_idle():
    north = _station("north")
    south = _station("south", 2595, 2253, "north")
    assert gc.choose_station([north, south], "north", 2596, 2257)["name"] == "south"
    assert gc.choose_station([north, south], "south", 2596, 2257)["name"] == "north"


def test_choose_station_falls_back_to_nearest_without_a_last_station():
    north = _station("north")
    south = _station("south", 2595, 2253, "north")
    assert gc.choose_station([north, south], None, 2596, 2257)["name"] == "north"
    assert gc.choose_station([north, south], None, 2595, 2250)["name"] == "south"


def test_choose_station_returns_none_for_an_empty_list():
    assert gc.choose_station([], None, 2596, 2257) is None


# --- has_start_materials ------------------------------------------------------------

def test_start_materials_needs_four_sunstone_not_merely_one():
    """The bug being fixed: the old gate checked existence, so 1-3 raw fired a doomed click."""
    assert not gc.has_start_materials(3, 1)
    assert gc.has_start_materials(4, 1)


def test_start_materials_needs_a_core():
    assert not gc.has_start_materials(20, 0)
    assert gc.has_start_materials(20, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `ControllerAddon/`: `python -m pytest tests/test_golem_crafting.py -v`
Expected: FAIL — `AttributeError: module 'golem_crafting' has no attribute 'side_order'`.

- [ ] **Step 3: Implement the pure functions**

In `ControllerAddon/routines/golem_crafting.py`, add after `find_item` (currently ends line 293):

```python
# The four sides in clockwise order. Index arithmetic below depends on this ordering.
SIDES = ("north", "east", "south", "west")

# CONFIRMED by the user 2026-08-20: "Start-golem" deducts 4 raw sunstone on that first click,
# not 1 -- this module's header comment previously claimed 1 (self-flagged there as n=1) and was
# wrong. Matches the reference plugin's own hasGolemMaterials() gate (sunstoneCount < 4 blocks a
# start). Each golem also needs 1 core, itself 1 raw sunstone chiselled 1-for-1, so a golem
# costs 5 sunstone-equivalents and SUNSTONE_PER_TRIP=20 / CORES_PER_TRIP=4 balances exactly.
SUNSTONE_PER_GOLEM = 4
CORES_PER_GOLEM = 1


def side_order(start: str, insert_side: str) -> list:
    """All four sides from `start`, rotating in whichever direction reaches `insert_side` last.

    Direction rule is the reference plugin's isClockwise: clockwise when the clockwise distance
    to the insert side is at least the counter-clockwise one. Note this returns all four sides
    regardless of whether they are carved -- carve_plan does the filtering."""
    start_index = SIDES.index(start)
    insert_index = SIDES.index(insert_side)
    clockwise = (insert_index - start_index) % 4
    counter = (start_index - insert_index) % 4
    step = 1 if clockwise >= counter else 3
    return [SIDES[(start_index + i * step) % 4] for i in range(4)]


def can_end_on_insert(start: str, insert_side: str) -> bool:
    """True iff a full four-side rotation from `start` finishes on `insert_side`.

    Four steps from `start` land on `start + 3` (clockwise) or `start + 1` (counter-clockwise),
    so this holds only when `start` is one of `insert_side`'s two neighbours -- starting ON the
    insert side, or opposite it, makes ending there impossible. Both plinths insert from a
    north/south side, so east and west are the only valid starts for either. That is exactly why
    the reference plugin's StartDirection enum contains only EAST and WEST; the restriction reads
    as arbitrary until this is derived."""
    return side_order(start, insert_side)[-1] == insert_side


def choose_start_side(station: dict, player_x: int, player_y: int) -> str:
    """Whichever valid start side (east/west, see can_end_on_insert) is nearer to the player.

    Both produce a correct four-side order, so the choice is free -- making it adaptive is what
    keeps plinth alternation cheap: arriving from the other plinth, carving begins on the near
    side instead of walking around the golem first."""
    candidates = [s for s in SIDES if can_end_on_insert(s, station["insertSide"])]
    return min(
        candidates,
        key=lambda s: max(
            abs(station["sides"][s]["x"] - player_x),
            abs(station["sides"][s]["y"] - player_y),
        ),
    )


def carve_plan(station: dict, player_x: int, player_y: int) -> list:
    """The sides still needing carving, in the order to carve them.

    Reading `done` from the varbits is what makes a resumed run correct for free: whatever the
    game says is uncarved is what gets carved, with no dependence on ctx.state.data surviving a
    restart. On a resumed golem whose insert side is already carved the plan will NOT end on the
    insert side -- that is legal, and craft_phase walks there afterwards. Ending on the insert
    side is a best-effort saving on fresh golems, never a correctness requirement."""
    order = side_order(choose_start_side(station, player_x, player_y), station["insertSide"])
    return [side for side in order if not station["sides"][side]["done"]]


def choose_station(stations: list, last_station, player_x: int, player_y: int):
    """Which plinth to work on next, or None if `stations` is empty.

    Finishing an in-progress golem takes priority, and the more advanced of two in-progress ones
    goes first -- its 4 sunstone are already spent and unrecoverable, so the shorter the window
    before it completes, the less a crash can strand. When neither is in progress, alternate away
    from the just-completed plinth (see the routine's alternation note) rather than waiting out
    its reset. `last_station` being None -- a fresh start or a restart -- needs no reconciliation:
    either plinth is correct, so fall through to whichever is nearer."""
    if not stations:
        return None
    in_progress = [s for s in stations if s.get("progress", 0) > 0]
    if in_progress:
        return max(in_progress, key=lambda s: s.get("progress", 0))
    if last_station is not None:
        others = [s for s in stations if s.get("name") != last_station]
        if others:
            return others[0]
    return min(stations, key=lambda s: max(abs(s["x"] - player_x), abs(s["y"] - player_y)))


def has_start_materials(raw_sunstone: int, cores: int) -> bool:
    """Whether a NEW golem can be started. Counts, not mere existence -- see SUNSTONE_PER_GOLEM:
    holding 1-3 raw sunstone passed the old existence check, fired a doomed "Start-golem" and
    burned a full confirmation cycle before the rejection routed to mine."""
    return raw_sunstone >= SUNSTONE_PER_GOLEM and cores >= CORES_PER_GOLEM
```

- [ ] **Step 4: Run the tests to verify they pass**

Run from `ControllerAddon/`: `python -m pytest tests/test_golem_crafting.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Run the full suite and compile check**

```bash
python -m pytest tests/
python -m py_compile routines/golem_crafting.py
```
Expected: all pass, no output from `py_compile`.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py ControllerAddon/tests/test_golem_crafting.py
git commit -F - <<'EOF'
Add pure golem carve-planning logic with tests

side_order/carve_plan/choose_station/has_start_materials, all pure and
unit-tested without a bridge or serial port.

Includes a direct assertion that only east/west starts can end on the
insert side -- the constraint that justifies restricting the start side
at all, so a later simplification cannot quietly reintroduce a walk
before Insert-core.

has_start_materials checks counts, not existence: a golem costs 4 raw
sunstone, so 1-3 previously passed the gate and fired a doomed click.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 3: `controller.get_golem()` and the `craft_phase` rewrite

The integration. Replaces the guess-and-check state machine with reads against `/golem`.

**Files:**
- Modify: `ControllerAddon/controller.py` (add getter beside `get_mixology_state`, currently line 429)
- Modify: `ControllerAddon/routines/golem_crafting.py` (replace `craft_phase`, lines 909-1294)
- Modify: `ControllerAddon/tests/test_golem_crafting.py` (add getter test)

**Interfaces:**
- Consumes: `GET /golem` (Task 1); `side_order`/`carve_plan`/`choose_station`/`has_start_materials` (Task 2).
- Produces: `controller.get_golem() -> dict | None`; a rewritten `craft_phase(ctx, config) -> None` reading `ctx.state.data["gc_last_station"]`.

- [ ] **Step 1: Write the failing test for the getter**

First add `import controller` to the import block at the **top** of `ControllerAddon/tests/test_golem_crafting.py`, directly below `import golem_crafting as gc`. Then append the tests:

```python
def test_get_golem_returns_none_when_the_endpoint_is_missing(monkeypatch):
    """A 404 (client not rebuilt with GolemProvider) must surface as None, not an empty dict --
    craft_phase safety-stops on None rather than silently degrading to a blind path."""
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_golem() is None


def test_get_golem_passes_through_the_payload(monkeypatch):
    payload = {"inArea": True, "stations": []}
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: payload)
    assert controller.get_golem() == payload
```

- [ ] **Step 2: Run it to verify it fails**

Run from `ControllerAddon/`: `python -m pytest tests/test_golem_crafting.py -k get_golem -v`
Expected: FAIL — `AttributeError: module 'controller' has no attribute 'get_golem'`.

- [ ] **Step 3: Add the getter**

In `ControllerAddon/controller.py`, immediately after `get_mixology_state` (currently ends line 434):

```python
def get_golem():
    """Live golem-crafting state from GolemProvider, or None if the endpoint is unavailable.

    Deliberately returns None rather than an empty default (unlike get_mixology_state above):
    routines/golem_crafting.py treats a missing /golem as a hard stop with a "rebuild the client"
    message, because the alternative -- falling back to the old blind guess-and-check path --
    would mean two divergent code paths for the same phase, which is exactly what produced the
    desync bug this endpoint exists to fix."""
    return api_get("/golem")
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_golem_crafting.py -k get_golem -v`
Expected: PASS.

- [ ] **Step 5: Add the new timing constants**

Do this before the rewrite below — the new `craft_phase` references all four. In `ControllerAddon/routines/golem_crafting.py`, replace the `GOLEM_ACTION_TIMEOUT_S = 15.0` block (and its comment) with:

```python
# How long to wait for a side's `done` varbit to flip after a Shape-golem click. Far shorter than
# the 15s chat-message timeout this replaces: the varbit moves on the tick the server processes
# the action, so anything beyond a couple of seconds means the click genuinely didn't land.
GOLEM_SIDE_TIMEOUT_S = 4.0

# Insert-core gets a little longer -- it consumes a core and a fur and plays a longer animation
# before the station's progress varbit resets.
GOLEM_INSERT_TIMEOUT_S = 6.0

# How often to re-read /golem while awaiting a confirmation. Well under the 0.6s game tick, since
# the endpoint is deliberately not TickCache-gated (see GolemProvider's own comment).
GOLEM_CONFIRM_POLL_S = 0.2

# Consecutive failures at the SAME side before treating it as a real anomaly. With ground truth
# available a stuck side is genuinely abnormal, not the routine desync the old 16-attempt budget
# existed to absorb, so this is an assertion rather than a retry allowance.
GOLEM_SIDE_MISS_LIMIT = 5
```

- [ ] **Step 6: Replace `craft_phase`**

In `ControllerAddon/routines/golem_crafting.py`, replace the whole of `craft_phase` — from `def craft_phase(ctx, config: dict) -> None:` up to but NOT including the `# Never deposited during a golem-crafting bank trip` comment that precedes `BANK_KEEP_ITEMS` — with:

```python
def _station_by_name(state: dict, name: str):
    return next((s for s in state.get("stations", []) if s.get("name") == name), None)


def _find_station_object(station: dict):
    """The plinth GameObject for `station`, matched by object id.

    Matched by id rather than by name: the object's name changes as the golem progresses (Empty
    plinth -> Unfinished golem -> Unpowered golem) while its id does not, so an id match needs no
    GOLEM_STATE_NAMES table and cannot be confused by a state transition mid-lookup."""
    return next(
        (o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
         if o.get("id") == station["objectId"]),
        None,
    )


def _click_station(ctx, station) -> bool:
    """Click the plinth, hopping closer first if it isn't on screen. Returns False if the click
    did not go out, in which case the caller should retry next tick.

    A plain left-click is enough -- its default action already resolves to Start-golem /
    Shape-golem / Insert-core in each respective state, so there's no need for
    click_menu_action's right-click + menu-read + select round trip."""
    obj = _find_station_object(station)
    if obj is None:
        _hop_toward_point(ctx, station["x"], station["y"], station.get("plane", 0))
        return False
    refresh = controller.object_refresh_target(
        obj.get("x"), obj.get("y"), obj.get("plane", 0), station["objectId"],
        radius=MAX_OBJECT_DISTANCE,
    )
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh):
        _hop_toward_point(ctx, station["x"], station["y"], station.get("plane", 0))
        return False
    return True


def _await_side_done(station_name: str, side: str, timeout_s: float) -> bool:
    """Poll /golem until `side` reads done, or timeout. True if it flipped.

    This replaces the old 15s chat-message wait. The varbit flips on the tick the server
    processes the action, so a real success is seen almost immediately and a timeout genuinely
    means the click did not land -- most often a deliberate MISTAKE_MISCLICK_CHANCE near-miss,
    which produces no game action at all (see CLAUDE.md). Retrying is safe precisely because the
    varbit says unambiguously whether the previous click counted."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(GOLEM_CONFIRM_POLL_S)
        state = controller.get_golem()
        if state is None:
            return False
        station = _station_by_name(state, station_name)
        if station and station["sides"][side]["done"]:
            return True
    return False


def _await_golem_complete(station_name: str, timeout_s: float) -> bool:
    """Poll /golem until the station's overall progress returns to 0 (golem finished)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(GOLEM_CONFIRM_POLL_S)
        state = controller.get_golem()
        if state is None:
            return False
        station = _station_by_name(state, station_name)
        if station and station.get("progress", 0) == 0:
            return True
    return False


def craft_phase(ctx, config: dict) -> None:
    """Carve and complete one golem per call, driven entirely by /golem's varbits.

    REWRITTEN 2026-08-20 (see docs/superpowers/specs/2026-08-20-golem-crafting-varbit-overhaul-
    design.md). The previous implementation tracked carved sides in ctx.state.data, which resets
    on every process restart and every edit to this file while the plinth's real progress
    persists server-side. That desync was absorbed by a 16-attempt retry budget, camera nudges
    and eventually abandoning the golem -- and since Start-golem's 4 sunstone are spent up front,
    each abandonment destroyed a full golem's worth of material. Reading each side's `done`
    varbit removes the desync outright, so none of that machinery is needed.

    Three savings per golem come with it: the carve order ends on the insert side (no extra walk
    before Insert-core), confirmation is a varbit poll rather than a 15s chat timeout, and a
    completed plinth is left immediately for the other one rather than waited on (its gem goes
    straight to the gem bag, so there is nothing to pick up -- see the gem-bag handling)."""
    state = controller.get_golem()
    if state is None:
        ctx.state.trigger_safety_stop(
            "GET /golem unavailable -- rebuild and restart the RuneLite client "
            "(./gradlew.bat run) so GolemProvider is loaded"
        )
        return

    stations = state.get("stations", [])
    if not stations:
        if handle_missing_target(ctx, config, "plinth", "gc_plinth_misses", "gc_plinth_walks"):
            return
        ctx.state.trigger_safety_stop("no golem stations reported by /golem")
        return
    ctx.state.data["gc_plinth_misses"] = 0
    ctx.state.data["gc_plinth_walks"] = 0

    player = controller.get_player()
    player_x, player_y = player.get("x", 0), player.get("y", 0)
    station = choose_station(
        stations, ctx.state.data.get("gc_last_station"), player_x, player_y
    )
    fresh_golem = station.get("progress", 0) == 0

    if fresh_golem:
        raw = _count_named(RAW_SUNSTONE_NAME)
        cores = _count_named(SUNSTONE_CORE_NAME)
        if not has_start_materials(raw, cores):
            fur_stock = ctx.state.data.get("gc_fur_stock", 0)
            if fur_stock < CORES_PER_TRIP:
                debug_log.log("golem crafting", f"batch done ({raw} sunstone, {cores} cores), fur low ({fur_stock}) -- banking")
                ctx.state.data["gc_phase"] = "bank"
            else:
                debug_log.log("golem crafting", f"batch done ({raw} sunstone, {cores} cores), fur ok ({fur_stock}) -- mining")
                ctx.state.data["gc_phase"] = "mine"
            return
        if ctx.state.data.get("gc_fur_stock", 0) <= 0:
            debug_log.log("golem crafting", "out of usable fur for a new golem -- moving to bank phase")
            ctx.state.data["gc_phase"] = "bank"
            return

    plan = carve_plan(station, player_x, player_y)
    if plan:
        side = plan[0]
        tile = station["sides"][side]
        if not walk_to_exact_tile(ctx, tile["x"], tile["y"], tile.get("plane", 0)):
            debug_log.log("golem crafting", f"couldn't walk to {side} side -- retrying next tick")
            return
        verb = "starting golem" if fresh_golem else "shaping golem"
        debug_log.log("golem crafting", f"{verb} from {side} side of {station['name']} plinth ({4 - len(plan)}/4 done)")
        if not _click_station(ctx, station):
            return
        if not _await_side_done(station["name"], side, GOLEM_SIDE_TIMEOUT_S):
            debug_log.log("golem crafting", f"{side} side didn't register within {GOLEM_SIDE_TIMEOUT_S}s -- retrying next tick")
            ctx.state.data["gc_side_misses"] = ctx.state.data.get("gc_side_misses", 0) + 1
            if ctx.state.data["gc_side_misses"] >= GOLEM_SIDE_MISS_LIMIT:
                ctx.state.trigger_safety_stop(
                    f"{station['name']} plinth's {side} side never registered after "
                    f"{GOLEM_SIDE_MISS_LIMIT} attempts; station state: {station}"
                )
            return
        ctx.state.data["gc_side_misses"] = 0
        return

    # All four sides carved -- insert the core. The carve order ended here on a fresh golem, so
    # this walk is normally a no-op; it only actually moves on a resumed golem whose insert side
    # was already carved before this session started.
    insert_side = station["insertSide"]
    tile = station["sides"][insert_side]
    if not walk_to_exact_tile(ctx, tile["x"], tile["y"], tile.get("plane", 0)):
        debug_log.log("golem crafting", f"couldn't walk to {insert_side} insert side -- retrying next tick")
        return

    if find_item(SUNSTONE_CORE_NAME) is None:
        debug_log.log("golem crafting", "sunstone core went missing before inserting -- moving to mine phase")
        ctx.state.data["gc_phase"] = "mine"
        return
    if ctx.state.data.get("gc_fur_stock", 0) <= 0:
        debug_log.log("golem crafting", "fur ran out before inserting -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        return

    debug_log.log("golem crafting", f"inserting core + fur from {insert_side} side of {station['name']} plinth")
    if not _click_station(ctx, station):
        return
    if not _await_golem_complete(station["name"], GOLEM_INSERT_TIMEOUT_S):
        debug_log.log("golem crafting", f"golem didn't complete within {GOLEM_INSERT_TIMEOUT_S}s -- retrying next tick")
        return

    debug_log.log("golem crafting", f"golem finished on {station['name']} plinth")
    # Alternate: record which plinth just finished so choose_station picks the other one next
    # call, rather than waiting out this one's reset.
    ctx.state.data["gc_last_station"] = station["name"]
    ctx.state.data["gc_fur_stock"] = max(0, ctx.state.data.get("gc_fur_stock", 0) - 1)
```

- [ ] **Step 7: Delete the now-dead constants and helpers**

Delete these constants together with their comment blocks: `GOLEM_ADJACENT_OFFSETS`, `GOLEM_INSERT_OFFSET_BY_ID`, `GOLEM_EXTRA_SIDE_ATTEMPTS_CAP`, `GOLEM_EXTRA_SIDE_MISSES_BEFORE_ROTATE`, `GOLEM_STATE_NAMES`, and the now-unused `find_golem_object` function.

Also delete these chat constants, superseded by varbit reads: `GOLEM_CHAT_SIDE_DONE`, `GOLEM_CHAT_SIDE_ALREADY_DONE`, `GOLEM_CHAT_NOT_ENOUGH_SUNSTONE`, `GOLEM_CHAT_WRONG_INSERT_SIDE`. **Keep** `GOLEM_CHAT_COMPLETE` and `GOLEM_CHAT_NOT_ENOUGH_FUR` — Task 4 uses them.

In `on_event`, remove the four deleted names from the golem-crafting capture tuple, leaving `GOLEM_CHAT_COMPLETE` and `GOLEM_CHAT_NOT_ENOUGH_FUR`.

- [ ] **Step 8: Verify nothing still references the deleted names**

```bash
grep -n "GOLEM_ADJACENT_OFFSETS\|GOLEM_INSERT_OFFSET_BY_ID\|GOLEM_EXTRA_SIDE\|GOLEM_STATE_NAMES\|find_golem_object\|gc_craft_sides_done\|gc_craft_insert_tried\|gc_craft_awaiting\|gc_craft_extra_side_attempts\|GOLEM_ACTION_TIMEOUT_S\|GOLEM_CHAT_SIDE_DONE\|GOLEM_CHAT_SIDE_ALREADY_DONE\|GOLEM_CHAT_NOT_ENOUGH_SUNSTONE\|GOLEM_CHAT_WRONG_INSERT_SIDE" routines/golem_crafting.py
```
Expected: no output. Any hit is a leftover reference — fix it before continuing.

- [ ] **Step 9: Run the suite and compile check**

```bash
python -m pytest tests/
python -m py_compile routines/golem_crafting.py controller.py
```
Expected: all pass, no output from `py_compile`.

- [ ] **Step 10: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/routines/golem_crafting.py ControllerAddon/tests/test_golem_crafting.py
git commit -F - <<'EOF'
Rewrite craft_phase against /golem varbits

Replaces the ctx.state.data side tracking -- which reset on every restart
while the plinth's real progress persisted -- with per-side `done` varbit
reads. That removes the desync outright, so the 16-attempt retry budget,
the camera-nudge escalation and the abandon-the-golem fallback all go.

Confirmation is now a varbit poll (4s) rather than a 15s chat timeout,
the carve order ends on the insert side so no walk is needed before
Insert-core, and a finished plinth is left immediately for the other one
instead of being waited on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Gem-bag loot handling

The gift goes straight into the gem bag, so the ground scan is gone — but a full bag still drops it, and that must not silently lose loot.

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (`on_event`, lines 816-849; plus a check in `craft_phase`)
- Modify: `ControllerAddon/tests/test_golem_crafting.py`

**Interfaces:**
- Consumes: `craft_phase` from Task 3.
- Produces: `GOLEM_LOOT_GROUND_MARKER: str`; `ctx.state.data["gc_gem_bag_full"]` (bool) set by `on_event`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_golem_crafting.py`:

```python
class _FakeState:
    def __init__(self):
        self.data = {}

    def trigger_safety_stop(self, reason):
        self.data["_stopped"] = reason


class _FakeCtx:
    def __init__(self):
        self.state = _FakeState()
        self.ser = None


def _loot_event(ctx, destination):
    gc.on_event(ctx, "CHAT_MESSAGE", {
        "message": f"As you complete the golem it leaves a gift {destination} for you: 1 x Uncut ruby."
    })


def test_gem_bag_destination_does_not_flag_a_full_bag():
    ctx = _FakeCtx()
    _loot_event(ctx, "in your gem bag")
    assert not ctx.state.data.get("gc_gem_bag_full")


def test_ground_destination_flags_a_full_gem_bag():
    """The only route by which loot can now be permanently lost, so it must be detected from the
    message rather than by scanning the ground after every golem."""
    ctx = _FakeCtx()
    _loot_event(ctx, "on the ground")
    assert ctx.state.data["gc_gem_bag_full"]


def test_gem_sack_destination_does_not_flag_a_full_bag():
    ctx = _FakeCtx()
    _loot_event(ctx, "in your gem sack")
    assert not ctx.state.data.get("gc_gem_bag_full")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_golem_crafting.py -k gem -v`
Expected: FAIL on `test_ground_destination_flags_a_full_gem_bag` — `KeyError: 'gc_gem_bag_full'`.

- [ ] **Step 3: Implement**

In `ControllerAddon/routines/golem_crafting.py`, add beside the other chat constants:

```python
# CONFIRMED by the user 2026-08-20: the gift a finished golem awards goes straight into the gem
# bag -- there is nothing on the ground to pick up, so the old three-attempt ground-item retry
# loop (and its 2.0s settle) is gone. The game's own message names the destination, though, and
# a FULL gem bag drops the gift to the ground instead. Detecting that from the message costs
# nothing on the common path, where scanning the ground every golem cost ~2s and up to 3 clicks.
GOLEM_LOOT_GROUND_MARKER = "leaves a gift on the ground"
```

In `on_event`, add after the existing golem-crafting chat capture block:

```python
    if GOLEM_LOOT_GROUND_MARKER in message:
        # Gem bag is full -- the gift fell on the ground. craft_phase picks it up once and banks.
        ctx.state.data["gc_gem_bag_full"] = True
```

In `craft_phase`, immediately after the `ctx.state.data["gc_last_station"] = station["name"]` line, add:

```python
    if ctx.state.data.get("gc_gem_bag_full"):
        debug_log.log("golem crafting", "gem bag full -- collecting the dropped gift and banking")
        for drop in [
            i for i in controller.get_ground_items()
            if abs(i.get("x", 9999) - station["x"]) <= 1 and abs(i.get("y", 9999) - station["y"]) <= 1
        ]:
            controller.click_menu_action(ctx.ser, drop, "take", target_hint=drop.get("name"), offset=(0, 0))
            controller.human_delay(0.6, 1.0)
        ctx.state.data["gc_gem_bag_full"] = False
        ctx.state.data["gc_phase"] = "bank"
        return
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_golem_crafting.py -k gem -v`
Expected: PASS.

- [ ] **Step 5: Run the suite and compile check**

```bash
python -m pytest tests/
python -m py_compile routines/golem_crafting.py
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py ControllerAddon/tests/test_golem_crafting.py
git commit -F - <<'EOF'
Detect a full gem bag from the loot message instead of scanning the ground

The golem's gift goes straight into the gem bag, so the per-golem ground
scan guarded a case that does not normally occur. A full bag does drop it,
though, and that is now the only way loot can be permanently lost -- so it
is detected from the message's own destination wording, which costs
nothing on the common path.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Correct the stale documentation, then verify live

The module header still describes the old (wrong) model. Correcting it is part of the change, not an afterthought — the next person to debug this file will read it first.

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py` (header comment, lines 19-51)

- [ ] **Step 1: Correct the header comment**

In `ControllerAddon/routines/golem_crafting.py`, replace the two bullet points in the header that describe the material cost and the Insert-core side (lines 31-43) with:

```python
#   - Material cost is front-loaded: the FIRST side ("Start-golem") consumes 4 raw Sunstone, all
#     deducted on that one click -- CONFIRMED by the user 2026-08-20, correcting this comment's
#     earlier claim of 1 (which was self-flagged as n=1 and was wrong). The remaining 3 sides
#     ("Shape-golem") consume nothing further. A golem therefore costs 4 raw sunstone + 1 core,
#     and since a core is 1 raw sunstone chiselled 1-for-1, 5 sunstone-equivalents in total --
#     which is why SUNSTONE_PER_TRIP (20) and CORES_PER_TRIP (4) balance to exactly 4 golems.
#     Note this means abandoning a half-carved golem destroys 4 sunstone.
#   - The finishing move is "Insert-core", which auto-consumes both 1 Sunstone core AND the best
#     available fur (straight from the fur pouch) in one click, and awards Crafting XP. It is
#     accepted from exactly ONE side per plinth, fixed: the northern plinth (object 62351) takes
#     it from its south side, the southern one (62352) from its north. This is no longer
#     discovered by trial and error -- GolemProvider reports it as `insertSide` on GET /golem.
#   - Which sides are already carved is read from per-side varbits via GET /golem, NOT tracked
#     locally. See docs/superpowers/specs/2026-08-20-golem-crafting-varbit-overhaul-design.md.
#   - The gem a finished golem awards goes straight into the gem bag; nothing is left on the
#     ground unless the bag is full (see GOLEM_LOOT_GROUND_MARKER).
```

Also update the "Deliberately NOT implemented" line (line 49-51) — it is unchanged in substance, keep it as-is.

- [ ] **Step 2: Compile check and full suite**

```bash
python -m pytest tests/
python -m py_compile routines/golem_crafting.py controller.py banker.py travel.py
```
Expected: all pass. (`banker.py`/`travel.py` are compiled explicitly because no test imports them, so a syntax error there would not surface in pytest — see CLAUDE.md.)

- [ ] **Step 3: Commit the doc correction**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -F - <<'EOF'
Correct the golem_crafting header to match the real mechanics

Records the 4-sunstone start cost (the previous 1 was self-flagged n=1
and wrong), the fixed per-plinth insert side now reported by /golem, that
side state is read from varbits rather than tracked locally, and that
loot goes to the gem bag.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

- [ ] **Step 4: Build and launch the client**

Check first that no stray process holds the Arduino serial port:
```bash
powershell -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\""
```
Then from the repo root: `./gradlew.bat run`

- [ ] **Step 5: Verify the endpoint before running the routine**

With the client logged in and standing at Wyrmscraig:
```bash
curl -s http://localhost:8080/golem
```
Expected: `inArea` true; two stations with objectIds 62351/62352 and tiles (2596,2257)/(2595,2253); each side carrying plausible `done`/`progress`. **Cross-check against what you can see in-game** — if a side you know is carved reads `done: false`, stop and report rather than proceeding.

Also confirm `curl -s http://localhost:8080/player` now includes `craftingXp`.

- [ ] **Step 6: Run the routine and watch a full golem**

```bash
cd ControllerAddon && python -u controller.py routines/golem_crafting.py *> golem_run.log 2>&1
```
Poll the log with `Read` at 20-90s intervals — not tight loops. Confirm all four:
1. The player does **not** walk after the final side before Insert-core.
2. The player does **not** linger at the completed plinth (no ground scan, no 2s settle).
3. The gem arrives in the bag with nothing left on the ground.
4. Confirmation is fast — sides register in well under the old 15s.

- [ ] **Step 7: Verify alternation across two golems**

Keep watching: golem A completes, B starts immediately, and A is reused for the third. Confirm the reset is never actually waited on.

- [ ] **Step 8: Verify the resumed-run case — the one that matters most**

Kill the routine mid-golem (Ctrl-C), then restart it with the same command. Confirm it carves **only** the genuinely remaining sides rather than re-attempting carved ones. This is the exact scenario the previous implementation could not handle.

Then repeat with a golem whose **insert side is already carved**, confirming the routine still finishes and simply walks to the insert side rather than erroring.

- [ ] **Step 9: Report results**

Report what was actually observed, including any step that did not behave as expected. Per CLAUDE.md: compiling clean and "the reasoning sounds right" are not verification — and if the user reports something wrong that the log looks clean for, keep digging for the mechanism rather than concluding it is already fixed.

---

## Notes for the executor

- **Task 1 must be built and the client restarted before Tasks 3-5 can be live-verified.** Tasks 2-4 can be written and unit-tested without a running client.
- **Do not "fix" `has_start_materials` to check for mere existence** if a live run shows an unexpected bail to `mine`. The 4-sunstone cost is user-confirmed; an unexpected bail means the *budget* needs investigating, not the gate.
- **The gem-bag-full path (Task 4) is the only route by which loot can now be permanently lost.** If it cannot be provoked live, say so explicitly in the report rather than reporting it as verified.
- Follow-on work, each needing its own spec, is listed in the design doc's Decomposition section: `mine_phase` (rock momentum), and `bank`/fur/`hunt` (authoritative fur count, plus the user-reported hunting problems which are not yet characterised).
