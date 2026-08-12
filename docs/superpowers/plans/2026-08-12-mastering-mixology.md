# Mastering Mixology Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Task 10 (Live verification) is live/interactive, not background-dispatchable.** It requires a
> running RuneLite client with the player's own account standing in the Mastering Mixology lab
> (Aldarin), with an active order. Do not dispatch it to an autonomous background subagent — drive
> it the same way `CLAUDE.md`'s live-debugging workflow and the bridge-tick-cache-freshness plan's
> Task 6 handled live/account-touching verification: interactively, with the human partner
> present. Every other task is a normal, self-contained code task — this plan's earlier draft had
> two live-investigation tasks up front, but both were resolved during research (see the design
> doc's "Superseded research" section) using RuneLite's own generated `VarbitID`/`ObjectID`/
> `SpotanimID` constants, cross-checked directly against this project's cached RuneLite jar. What
> remains live is *confirming* known values against the real game, not discovering them.

**Goal:** Build `routines/mastering_mixology.py`, automating Mastering Mixology under the
max-points-per-paste strategy (bank herbs → refine → hopper → read orders → mix → process →
deposit), plus the one new Java provider it needs.

**Architecture:** A phase state machine in `ctx.state.data["mm_phase"]`, the same shape as
`golem_crafting.py`'s. Six physical stations (refiner, 3 levers, hopper, mixing vessel, 3
processing stations, conveyor belt) are directly clickable multiloc objects, found by name via
the existing `find_object` convention. `MixologyProvider` exposes active orders, hopper paste
totals, station potion/progress, and digweed corner state via direct `client.getVarbitValue(...)`
reads — no widget scraping, no new client-side detection infrastructure. Processing-station
speedup cues are detected via the already-existing `/graphics-objects` endpoint, filtered to two
specific, confirmed graphics-object ids.

**Tech Stack:** Java (RuneLite plugin), Python (`ControllerAddon/`).

## Global Constraints

- Optimization strategy is max total points per paste only (see the design doc's "Optimization
  strategy" section) — no other strategy is built this pass.
- The game itself only ever offers orders the player has the Herblore level for (confirmed via
  the 2024-09-27 hotfix changelog in `data/raw/M/Mastering Mixology.txt`) — the routine does
  **not** implement its own order-level filtering; it trusts whatever `/mixology-state` returns.
- **Always re-read hopper totals, vessel content, station state, and active orders from the
  bridge rather than tracking them locally** — see the design doc's Error handling section.
- Confirmed varbit IDs (`net.runelite.api.gameval.VarbitID`, verified present in
  `runelite-api-1.12.35.jar`): `MM_LAB_ORDER_{1,2,3}_TYPE`/`_MODIFIER` (`11315`-`11320`,
  1-indexed), `MM_LAB_{AGITATOR,RETORT,ALEMBIC}_POTION` (`11340`-`11342`, 1-indexed),
  `MM_{AGITATOR,RETORT,ALEMBIC}_PROGRESS` (`11327`-`11329`), `MM_HERB_READY_{1,2,3,4}`
  (`11330`-`11333`), `MM_LAB_HIT_SKILLSHOT_{AGITATOR,ALEMBIC}` (`11337`-`11338`).
  `MM_AVAILABLE_{MOX,AGA,LYE}` (`11431`-`11433`) exist but are lower-confidence (inferred from
  naming, not confirmed by any consuming code) — confirm live per Task 10.
- Confirmed graphics-object ids (`net.runelite.api.gameval.SpotanimID`):
  `VFX_MACHINERY_ALCHEMY01_AGITATOR01` = `2954`, `VFX_MACHINERY_ALCHEMY01_ALEMBIC01` = `2955`.
- Confirmed object ids (`net.runelite.api.gameval.ObjectID`/`ObjectID1`), click by **name** per
  this codebase's convention, not by these ids — they're for reading which content variant is
  present, not for clicking:
  - Refiner `54904`, Mox lever `54868`, Aga lever `54867`, Lye lever `54869`, Hopper `54903`,
    Conveyor belt `54917`.
  - Mixing vessel content variants (from `Mixing vessel.txt`'s own `id1`-`id11` infobox fields —
    **this ordering is NOT the same as the varbit type index below**, it differs at two
    positions; see the note after this list): `54918` empty, `54919` Mammoth-might mix, `54920`
    Mystic mana amalgam, `54921` Marley's moonlight, `54922` Alco-augmentator, `54923` Aqualux
    amalgam, `54924` Azure aura mix, `54925` Liplack liquor, `54926` Anti-leech lotion, `54927`
    Megalite liquid, `54928` Mixalot.
  - Retort content variants `54870` empty through `54880`, Agitator `54881` empty through
    `54891`, Alembic `54892` empty through `54902` — same order as the vessel's above in each
    case (confirmed independently from each object's own wiki infobox).

  **This physical-object-id order genuinely differs from the varbit type-index order below**, at
  two positions: Aqualux amalgam/Azure aura mix are swapped, and Anti-leech lotion/Megalite
  liquid are swapped. These are two unrelated numbering systems (scenery-object content variants
  vs. varbit-encoded type indices) that don't have to agree, and here they don't — use the table
  immediately above for anything reading an object's `id` (Task 5's vessel-content check), and
  the table below for anything reading a varbit value (Task 1's provider, orders, station
  potions).
- Recipe table (paste combination per base, matching the varbit type index):
  0=Mammoth-might mix{mox:3,aga:0,lye:0}, 1=Mystic mana amalgam{mox:2,aga:1,lye:0},
  2=Marley's moonlight{mox:2,aga:0,lye:1}, 3=Alco-augmentator{mox:0,aga:3,lye:0},
  4=Azure aura mix{mox:1,aga:2,lye:0}, 5=Aqualux amalgam{mox:0,aga:2,lye:1},
  6=Liplack liquor{mox:0,aga:0,lye:3}, 7=Megalite liquid{mox:1,aga:0,lye:2},
  8=Anti-leech lotion{mox:0,aga:1,lye:2}, 9=Mixalot{mox:1,aga:1,lye:1}.
  Modifier index (matching the varbit modifier index): 0=Homogenised(Agitator),
  1=Concentrated(Retort), 2=Crystalised(Alembic).
- Windows environment: Gradle via `gradlew.bat`, not `./gradlew`.

## File Structure

- Create: `src/main/java/com/example/providers/MixologyProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (register the provider)
- Modify: `src/main/java/com/example/controller/RoutineChoice.java` (new enum entry)
- Modify: `src/main/java/com/example/controller/ControllerConfig.java` (new config section)
- Modify: `src/main/java/com/example/controller/ConfigExporter.java` (new export function)
- Modify: `ControllerAddon/controller.py` (`ROUTINE_FILES` entry, config/state wrappers)
- Create: `ControllerAddon/routines/mastering_mixology.py`

---

### Task 1: `MixologyProvider`

**Files:**
- Create: `src/main/java/com/example/providers/MixologyProvider.java`

**Interfaces:**
- Produces: `GET /mixology-state` → `{"inLab": bool, "orders": [{"name": str, "mox": int, "aga":
  int, "lye": int, "station": str}, ...], "hopper": {"mox": int, "aga": int, "lye": int},
  "stations": {"retort"/"agitator"/"alembic": {"potionName": str|null, "progress": int}},
  "digweed": {"northEast"/"southEast"/"southWest"/"northWest": bool}}` — consumed by every later
  task in this file.

- [ ] **Step 1: Write `MixologyProvider.java`**

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Reads Mastering Mixology's live state directly from RuneLite's own generated VarbitID
 * constants -- confirmed present in this project's runelite-api jar via direct javap inspection,
 * and cross-referenced against the hex-agon/mastering-mixology reference plugin's usage of the
 * same constants (see docs/superpowers/specs/2026-08-12-mastering-mixology-design.md's Research
 * method section). No widget reading, no dependency on that plugin at runtime -- this file
 * reimplements the handful of lookup tables it needs directly.
 */
public class MixologyProvider extends BridgeProvider
{
	private static final int LABS_REGION_ID = 5521;
	private static final int LABS_REGION_PLANE = 0;

	// Declared order matches PotionType's order in the reference plugin, which is also the
	// 0-indexed value the MM_LAB_ORDER_*_TYPE / MM_LAB_*_POTION varbits encode (real varbit
	// values are 1-indexed; index = varbit value - 1).
	private static final String[] POTION_NAMES = {
		"Mammoth-might mix", "Mystic mana amalgam", "Marley's moonlight", "Alco-augmentator",
		"Azure aura mix", "Aqualux amalgam", "Liplack liquor", "Megalite liquid",
		"Anti-leech lotion", "Mixalot",
	};

	// {mox, aga, lye} per POTION_NAMES entry, from the wiki's recipe table.
	private static final int[][] POTION_RECIPES = {
		{3, 0, 0}, {2, 1, 0}, {2, 0, 1}, {0, 3, 0}, {1, 2, 0},
		{0, 2, 1}, {0, 0, 3}, {1, 0, 2}, {0, 1, 2}, {1, 1, 1},
	};

	// Declared order matches PotionModifier's order in the reference plugin: 0=Homogenised
	// (Agitator), 1=Concentrated (Retort), 2=Crystalised (Alembic). Same 1-indexed varbit
	// convention as the potion-type varbits above.
	private static final String[] MODIFIER_STATIONS = {"Agitator", "Retort", "Alembic"};

	public MixologyProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/mixology-state", () -> onClient(this::getState));
	}

	private Object getState()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("inLab", isInLab());
		data.put("orders", computeOrders());
		data.put("hopper", computeHopper());
		data.put("stations", computeStations());
		data.put("digweed", computeDigweed());
		return data;
	}

	private boolean isInLab()
	{
		Player local = client.getLocalPlayer();
		if (local == null)
		{
			return false;
		}
		return local.getWorldLocation().getRegionID() == LABS_REGION_ID
			&& local.getWorldLocation().getPlane() == LABS_REGION_PLANE;
	}

	private List<Map<String, Object>> computeOrders()
	{
		List<Map<String, Object>> orders = new ArrayList<>();
		addOrderIfPresent(orders, VarbitID.MM_LAB_ORDER_1_TYPE, VarbitID.MM_LAB_ORDER_1_MODIFIER);
		addOrderIfPresent(orders, VarbitID.MM_LAB_ORDER_2_TYPE, VarbitID.MM_LAB_ORDER_2_MODIFIER);
		addOrderIfPresent(orders, VarbitID.MM_LAB_ORDER_3_TYPE, VarbitID.MM_LAB_ORDER_3_MODIFIER);
		return orders;
	}

	private void addOrderIfPresent(List<Map<String, Object>> orders, int typeVarbit, int modifierVarbit)
	{
		int typeIdx = client.getVarbitValue(typeVarbit) - 1;
		int modifierIdx = client.getVarbitValue(modifierVarbit) - 1;
		if (typeIdx < 0 || typeIdx >= POTION_NAMES.length || modifierIdx < 0 || modifierIdx >= MODIFIER_STATIONS.length)
		{
			return;
		}

		Map<String, Object> order = new HashMap<>();
		order.put("name", POTION_NAMES[typeIdx]);
		order.put("mox", POTION_RECIPES[typeIdx][0]);
		order.put("aga", POTION_RECIPES[typeIdx][1]);
		order.put("lye", POTION_RECIPES[typeIdx][2]);
		order.put("station", MODIFIER_STATIONS[modifierIdx]);
		orders.add(order);
	}

	private Map<String, Object> computeHopper()
	{
		Map<String, Object> hopper = new HashMap<>();
		hopper.put("mox", client.getVarbitValue(VarbitID.MM_AVAILABLE_MOX));
		hopper.put("aga", client.getVarbitValue(VarbitID.MM_AVAILABLE_AGA));
		hopper.put("lye", client.getVarbitValue(VarbitID.MM_AVAILABLE_LYE));
		return hopper;
	}

	private Map<String, Object> computeStations()
	{
		Map<String, Object> stations = new HashMap<>();
		stations.put("retort", buildStation(VarbitID.MM_LAB_RETORT_POTION, VarbitID.MM_RETORT_PROGRESS));
		stations.put("agitator", buildStation(VarbitID.MM_LAB_AGITATOR_POTION, VarbitID.MM_AGITATOR_PROGRESS));
		stations.put("alembic", buildStation(VarbitID.MM_LAB_ALEMBIC_POTION, VarbitID.MM_ALEMBIC_PROGRESS));
		return stations;
	}

	private Map<String, Object> buildStation(int potionVarbit, int progressVarbit)
	{
		Map<String, Object> station = new HashMap<>();
		int typeIdx = client.getVarbitValue(potionVarbit) - 1;
		station.put("potionName", (typeIdx >= 0 && typeIdx < POTION_NAMES.length) ? POTION_NAMES[typeIdx] : null);
		station.put("progress", client.getVarbitValue(progressVarbit));
		return station;
	}

	private Map<String, Object> computeDigweed()
	{
		Map<String, Object> digweed = new HashMap<>();
		digweed.put("northEast", client.getVarbitValue(VarbitID.MM_HERB_READY_1) != 0);
		digweed.put("southEast", client.getVarbitValue(VarbitID.MM_HERB_READY_2) != 0);
		digweed.put("southWest", client.getVarbitValue(VarbitID.MM_HERB_READY_3) != 0);
		digweed.put("northWest", client.getVarbitValue(VarbitID.MM_HERB_READY_4) != 0);
		return digweed;
	}
}
```

- [ ] **Step 2: Compile clean**

Run: `gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL, no errors. This confirms every `VarbitID.MM_*` constant referenced
above actually exists in the project's dependency jar (already checked via `javap` during
research, but the compiler is the authoritative check).

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/example/providers/MixologyProvider.java
git commit -m "Add MixologyProvider: orders, hopper totals, station state, digweed via varbits"
```

---

### Task 2: `routines/mastering_mixology.py` scaffolding

**Files:**
- Create: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG`, `get_config(bridge_config)`, `find_item(name)`, `find_object(name)`,
  `check_level_requirement(ctx, config)` — consumed by every later task in this file.

- [ ] **Step 1: Write the scaffolding**

```python
"""routines/mastering_mixology.py

Automates Mastering Mixology under the max-total-points-per-paste strategy (see
docs/superpowers/specs/2026-08-12-mastering-mixology-design.md): bank herbs -> refine -> hopper
-> read the 3 active orders -> mix -> process -> deposit, skipping single-paste orders (AAA/MMM/
LLL) unless an MAL order is present in the same set of 3.
"""

import random
import time

import banker
import controller
import debug_log

MAX_OBJECT_DISTANCE = 15
HERBLORE_LEVEL_REQUIRED = 60

# name -> {mox, aga, lye} -- matches the varbit type-index order MixologyProvider reports (see
# this plan's Global Constraints), NOT alphabetical or wiki-page order.
RECIPE_TABLE = {
    "Mammoth-might mix": {"mox": 3, "aga": 0, "lye": 0},
    "Mystic mana amalgam": {"mox": 2, "aga": 1, "lye": 0},
    "Marley's moonlight": {"mox": 2, "aga": 0, "lye": 1},
    "Alco-augmentator": {"mox": 0, "aga": 3, "lye": 0},
    "Azure aura mix": {"mox": 1, "aga": 2, "lye": 0},
    "Aqualux amalgam": {"mox": 0, "aga": 2, "lye": 1},
    "Liplack liquor": {"mox": 0, "aga": 0, "lye": 3},
    "Megalite liquid": {"mox": 1, "aga": 0, "lye": 2},
    "Anti-leech lotion": {"mox": 0, "aga": 1, "lye": 2},
    "Mixalot": {"mox": 1, "aga": 1, "lye": 1},
}
SINGLE_PASTE_BASES = {"Alco-augmentator", "Mammoth-might mix", "Liplack liquor"}
MAL_BASE = "Mixalot"

DEFAULT_CONFIG = {
    "enabled": True,
    "herbPriority": {
        "mox": ["Guam leaf", "Marrentill", "Tarromin", "Harralander"],
        "aga": ["Huasca", "Irit leaf", "Cadantine", "Lantadyme", "Dwarf weed", "Torstol"],
        "lye": ["Ranarr weed", "Toadflax", "Avantoe", "Kwuarm", "Snapdragon"],
    },
    "hopperTarget": 1000,
    "hopperLowWater": 150,
    "digweed": {"enabled": True},
    "speedups": {"retort": True, "agitator": True, "alembic": True},
    "maxObjectDistance": MAX_OBJECT_DISTANCE,
    "bank": {"teleportFallback": False},
    "breaks": {
        "enabled": True, "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12, "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}


def get_config(bridge_config):
    config = controller.get_mastering_mixology_config()
    return config if config is not None else DEFAULT_CONFIG


def find_item(name: str):
    """First inventory item whose name matches `name` exactly (case-insensitive) -- same
    exact-match idiom as golem_crafting.py's find_item."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
        None,
    )


def find_object(name: str):
    """Closest visible object whose name contains `name`, within MAX_OBJECT_DISTANCE -- same
    shape (including the radius fix) as golem_crafting.py's find_object. Every Mastering
    Mixology fixture is a multiloc/impostor object, so this resolves correctly regardless of
    which content variant (empty, or holding a specific potion) is currently active -- see the
    design doc's "Physical objects are multiloc" note."""
    name_lower = name.lower()
    candidates = [
        o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
        if name_lower in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check -- same shape and same setup()-vs-tick()
    reasoning as golem_crafting.py's check_level_requirement."""
    herblore = controller.get_skills().get("Herblore") or {}
    level = herblore.get("level")
    if level is not None and level < HERBLORE_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Herblore level {level} is below the {HERBLORE_LEVEL_REQUIRED} required for Mastering Mixology"
        )
```

- [ ] **Step 2: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add mastering_mixology.py scaffolding: config, recipe table, shared helpers"
```

---

### Task 3: `bank_herbs` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`
- Modify: `ControllerAddon/controller.py`

**Interfaces:**
- Consumes: `find_object` (Task 2), `banker.find_nearby_bank`/`open_bank`/`deposit_inventory`/
  `withdraw_loadout`/`close_bank` (existing, unmodified).
- Produces: `bank_herbs_phase(ctx, config)`, setting `ctx.state.data["mm_phase"]` to `"orders"`
  on completion. Adds `controller.get_mixology_state()`, consumed by every later task.

- [ ] **Step 1: Add the `get_mixology_state` wrapper to `controller.py`**

In `ControllerAddon/controller.py`, alongside the other `get_*` wrappers:

```python
def get_mixology_state():
    return api_get("/mixology-state") or {
        "inLab": False, "orders": [], "hopper": {"mox": 0, "aga": 0, "lye": 0},
        "stations": {}, "digweed": {},
    }
```

- [ ] **Step 2: Write `bank_herbs_phase`**

`banker.withdraw_loadout` (`ControllerAddon/banker.py:401`) matches each `withdraw` entry's
`name` via substring search against the bank's current contents — it takes one string per entry,
not a candidate list, so "first configured candidate actually present" has to be resolved after
the bank is open, not before. `controller.get_bank()` only returns real contents while the bank
interface is open (`BankProvider.computeBank` returns `{"open": false}` otherwise) — so this
can't use the all-in-one `banker.bank_up` (which withdraws before returning control), and instead
opens the bank with the same lower-level calls `bank_up` itself uses internally, resolves
candidates in between, then withdraws and closes directly:

```python
def bank_herbs_phase(ctx, config: dict) -> None:
    """Withdraw herbs per herbPriority (first candidate actually in the bank, per paste type),
    refine them, deposit the resulting paste into the hopper. Repeats (the phase re-enters
    itself on the next tick if the hopper target isn't reached yet) until every paste type in
    /mixology-state's hopper totals reaches hopperTarget, or safety-stops if the bank has none
    of a paste type's configured herb candidates left at all."""
    hopper = controller.get_mixology_state().get("hopper", {})
    target = config.get("hopperTarget", 1000)
    if all(hopper.get(paste, 0) >= target for paste in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper stocked ({hopper}) -- moving to orders phase")
        ctx.state.data["mm_phase"] = "orders"
        return

    herb_priority = config.get("herbPriority", DEFAULT_CONFIG["herbPriority"])
    needed_types = [p for p in ("mox", "aga", "lye") if hopper.get(p, 0) < target]
    if not needed_types:
        ctx.state.data["mm_phase"] = "orders"
        return

    bank_cfg = {"teleportFallback": config.get("bank", {}).get("teleportFallback", False)}
    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is None:
        ctx.state.trigger_safety_stop("no bank found near Mastering Mixology (and no teleport fallback configured)")
        return
    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank")
        return

    banker.deposit_inventory(ctx, {"keepItems": []})

    # fresh=True not strictly needed here (the bank was just opened this same call), but matches
    # this plan's Global Constraints convention of never trusting a possibly-stale read for a
    # decision that's about to drive a withdrawal.
    bank_contents = controller.get_bank(fresh=True).get("items", [])
    withdraw = []
    for paste_type in needed_types:
        candidates = herb_priority.get(paste_type, [])
        chosen = next(
            (name for name in candidates
             if any(name.lower() in (i.get("name") or "").lower() for i in bank_contents)),
            None,
        )
        if chosen is None:
            banker.close_bank(ctx)
            ctx.state.trigger_safety_stop(
                f"none of the configured {paste_type} herbs ({candidates}) are in the bank"
            )
            return
        withdraw.append({"name": chosen, "quantity": 14})

    banker.withdraw_loadout(ctx, {"withdraw": withdraw})
    banker.close_bank(ctx)

    _refine_and_hopper_inventory(ctx)


def _refine_and_hopper_inventory(ctx) -> None:
    """Deposit every clean herb / unfinished potion currently held into the refiner (clicking
    every tick to double refine speed, per the wiki), then deposit the resulting paste into the
    hopper. Loops until nothing refinable is left in the inventory."""
    refiner = find_object("Refiner")
    if refiner is None:
        ctx.state.trigger_safety_stop("no refiner found near the player")
        return

    all_herb_names = {name for names in DEFAULT_CONFIG["herbPriority"].values() for name in names}
    deadline = time.time() + 60.0  # a full inventory of herbs refines in well under this at 1/tick
    while time.time() < deadline:
        held = [i for i in controller.get_inventory() if (i.get("name") or "") in all_herb_names]
        if not held:
            break
        controller.click_entity(ctx.ser, refiner, check_ui_blockers=False)
        time.sleep(random.uniform(0.55, 0.75))  # one game tick, matches the refiner's own
                                                  # per-tick processing cadence when clicked

    hopper_obj = find_object("Hopper")
    if hopper_obj is None:
        ctx.state.trigger_safety_stop("no hopper found near the player")
        return
    controller.click_entity(ctx.ser, hopper_obj, check_ui_blockers=False)
    time.sleep(random.uniform(0.4, 0.7))
```

- [ ] **Step 3: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py controller.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py ControllerAddon/controller.py
git commit -m "Add bank_herbs phase: withdraw/refine/hopper herbs to a configured paste target"
```

---

### Task 4: `orders` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `controller.get_mixology_state()` (Task 3), `RECIPE_TABLE`/`SINGLE_PASTE_BASES`/
  `MAL_BASE` (Task 2).
- Produces: `orders_phase(ctx, config)`, writing `ctx.state.data["mm_chosen_orders"]` (a list of
  the order dicts to actually make this cycle) and setting `mm_phase` to `"mix"` — consumed by
  Task 5.

- [ ] **Step 1: Sanity-check the selection rule by hand before wiring it in**

This codebase has no unit-test framework for routine logic (per `golem_crafting-design.md`'s
Testing section — verification here is live). Check the rule against the wiki's own worked
cases: (a) 3 orders, none MAL, one single-paste present → make only the two non-single-paste
orders. (b) 3 orders, one is MAL → make all 3, including any single-paste orders present. (c)
All 3 orders single-paste → make only the one whose paste type the hopper has the least of.

- [ ] **Step 2: Write `orders_phase`**

```python
def _choose_orders(orders: list) -> list:
    """Max-points-per-paste selection: make every non-single-paste order, plus every
    single-paste order too if an MAL order is present in this same set of 3. If ALL 3 are
    single-paste, make only the one whose paste type currently has the lowest hopper stock
    (spends down whichever is SCARCEST, to keep the three roughly balanced over time, since
    single-paste orders are the only lever this routine has for correcting an imbalance;
    skipping the other two loses no points since single-paste orders are net-negative on their
    own per the wiki's reward-ratio table)."""
    has_mal = any(o.get("name") == MAL_BASE for o in orders)
    if has_mal:
        return list(orders)

    non_single = [o for o in orders if o.get("name") not in SINGLE_PASTE_BASES]
    if non_single:
        return non_single

    # All 3 are single-paste -- caller (orders_phase) picks which one via hopper stock, since
    # that needs a live hopper read this pure function shouldn't fetch itself.
    return orders


def orders_phase(ctx, config: dict) -> None:
    data = controller.get_mixology_state()
    orders = data.get("orders", [])
    hopper = data.get("hopper", {})

    if len(orders) < 3:
        # Bridge read came back short (e.g. not actually in the lab, or a fresh spawn not yet
        # populated) -- don't act on a partial order set.
        return

    chosen = _choose_orders(orders)
    if chosen and all(o.get("name") in SINGLE_PASTE_BASES for o in chosen):
        chosen = [min(chosen, key=lambda o: hopper.get(_paste_type_of(o), 0))]

    needed = {"mox": 0, "aga": 0, "lye": 0}
    for order in chosen:
        for paste in ("mox", "aga", "lye"):
            needed[paste] += order.get(paste, 0) * 10  # each lever pull uses 10 paste

    low_water = config.get("hopperLowWater", 150)
    if any(hopper.get(p, 0) - needed[p] < low_water for p in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper would drop below low-water fulfilling {[o.get('name') for o in chosen]} -- rebanking")
        ctx.state.data["mm_phase"] = "bank_herbs"
        return

    ctx.state.data["mm_chosen_orders"] = chosen
    debug_log.log("mixology", f"chosen orders this cycle: {[o.get('name') for o in chosen]}")
    ctx.state.data["mm_phase"] = "mix"


def _paste_type_of(order: dict) -> str:
    """The single paste type a single-paste order (AAA/MMM/LLL) needs -- whichever of
    mox/aga/lye is non-zero on that order dict."""
    return next(p for p in ("mox", "aga", "lye") if order.get(p, 0) > 0)
```

- [ ] **Step 3: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add orders phase: max-points-per-paste order selection"
```

---

### Task 5: `mix` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `ctx.state.data["mm_chosen_orders"]` (Task 4), `find_object` (Task 2).
- Produces: `mix_phase(ctx, config)`, setting `mm_phase` to `"process"` once every chosen order's
  base has been taken from the vessel into inventory.

- [ ] **Step 1: Write `mix_phase`**

```python
# Mixing vessel object ids by content, confirmed via direct ObjectID/ObjectID1 jar lookup (see
# this plan's Global Constraints) -- used to confirm the vessel's actual content after pulling
# levers, rather than trusting the pull count alone (see Global Constraints' "always re-read...
# rather than tracking locally").
VESSEL_ID_TO_BASE = {
    # This is the wiki-infobox object-id order, NOT the varbit type-index order RECIPE_TABLE
    # uses -- they genuinely differ (Aqualux amalgam/Azure aura mix and Anti-leech lotion/
    # Megalite liquid are swapped between the two). See this plan's Global Constraints.
    54919: "Mammoth-might mix", 54920: "Mystic mana amalgam", 54921: "Marley's moonlight",
    54922: "Alco-augmentator", 54923: "Aqualux amalgam", 54924: "Azure aura mix",
    54925: "Liplack liquor", 54926: "Anti-leech lotion", 54927: "Megalite liquid",
    54928: "Mixalot",
}

LEVER_NAMES = {"mox": "Mox lever", "aga": "Aga lever", "lye": "Lye lever"}


def mix_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])
    made = ctx.state.data.get("mm_made_bases", [])

    if len(made) >= len(chosen):
        ctx.state.data["mm_made_bases"] = []
        ctx.state.data["mm_phase"] = "process"
        return

    target_order = chosen[len(made)]
    pulls = []
    for paste in ("mox", "aga", "lye"):
        pulls.extend([paste] * target_order.get(paste, 0))

    for paste in pulls:
        lever = find_object(LEVER_NAMES[paste])
        if lever is None:
            ctx.state.trigger_safety_stop(f"no {LEVER_NAMES[paste]} found near the player")
            return
        controller.click_entity(ctx.ser, lever, check_ui_blockers=False)
        time.sleep(random.uniform(0.5, 0.7))  # one game tick -- levers resolve per-tick

    vessel = find_object("Mixing vessel")
    if vessel is None:
        ctx.state.trigger_safety_stop("no mixing vessel found near the player")
        return

    actual_base = VESSEL_ID_TO_BASE.get(vessel.get("id"))
    if actual_base != target_order.get("name"):
        # Vessel content doesn't match what this phase just tried to build -- a misclick on one
        # of the lever pulls above is the likely cause (see Global Constraints). Don't take a
        # wrong potion; log and retry the same target_order next tick with the vessel as it
        # actually is now, not what pulls we think we made.
        debug_log.log("mixology", f"vessel shows '{actual_base}', expected '{target_order.get('name')}' -- retrying")
        return

    controller.click_entity(ctx.ser, vessel, check_ui_blockers=False)
    time.sleep(random.uniform(0.4, 0.6))
    ctx.state.data["mm_made_bases"] = made + [target_order.get("name")]

    if ctx.state.data.get("mm_have_digweed") and target_order.get("name") == MAL_BASE:
        digweed_item = find_item("Digweed")
        base_item = find_item(MAL_BASE)
        if digweed_item is not None and base_item is not None:
            controller.click_entity(ctx.ser, digweed_item, check_ui_blockers=False)
            time.sleep(random.uniform(0.15, 0.3))
            controller.click_entity(ctx.ser, base_item, check_ui_blockers=False)
            time.sleep(random.uniform(0.3, 0.5))
            ctx.state.data["mm_have_digweed"] = False
```

(The digweed-into-MAL step at the end is written here, not deferred to a later task, because it
needs to run at the exact moment a MAL base is taken from the vessel — see Task 7 for how
`mm_have_digweed` gets set to `True` in the first place. `find_item("Digweed")` uses a substring
match, consistent with `find_item`'s general convention — the collected item's exact display
name (e.g. plain "Digweed" vs. something more specific) should be confirmed during Task 10's
live walkthrough; substring matching means a slightly-off guess still likely resolves correctly.)

- [ ] **Step 2: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add mix phase: lever pulls with vessel-content reconciliation and digweed use"
```

---

### Task 6: `process` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `controller.get_mixology_state()` (Task 3), `controller.get_graphics_objects()`
  (existing), `find_object`/`find_item` (Task 2).
- Produces: `process_phase(ctx, config)`, setting `mm_phase` to `"deposit"` once every unprocessed
  base currently held has been processed.

- [ ] **Step 1: Write `process_phase`**

```python
# Confirmed graphics-object ids for each station's speedup click-now cue (see this plan's
# Global Constraints) -- read via the existing /graphics-objects endpoint, no new Java needed.
AGITATOR_CUE_GRAPHICS_ID = 2954
ALEMBIC_CUE_GRAPHICS_ID = 2955

STATION_ORDER = ["Alembic", "Retort", "Agitator"]  # wiki's own recommended pathing order
STATION_STATE_KEY = {"Alembic": "alembic", "Retort": "retort", "Agitator": "agitator"}


def process_phase(ctx, config: dict) -> None:
    unprocessed = [i for i in controller.get_inventory() if i.get("name") in RECIPE_TABLE]
    if not unprocessed:
        ctx.state.data["mm_phase"] = "deposit"
        return

    # Which station a base needs is per-order, not fixed per potion name (see Global
    # Constraints/design doc) -- read from the order that's still in ctx.state.data rather than
    # guessing, since mm_chosen_orders already carries each chosen order's "station" field.
    chosen = ctx.state.data.get("mm_chosen_orders", [])
    station_for_name = {o.get("name"): o.get("station") for o in chosen}

    for station_name in STATION_ORDER:
        station = find_object(station_name)
        if station is None:
            continue
        due = [i for i in unprocessed if station_for_name.get(i.get("name")) == station_name]
        for potion in due:
            _process_one(ctx, config, station_name, station, potion)

    ctx.state.data["mm_phase"] = "deposit"


def _process_one(ctx, config: dict, station_name: str, station: dict, potion: dict) -> None:
    controller.click_entity(ctx.ser, station, check_ui_blockers=False)
    time.sleep(random.uniform(0.4, 0.6))

    speedups = config.get("speedups", DEFAULT_CONFIG["speedups"])
    if station_name == "Retort" and speedups.get("retort"):
        _process_retort_speedup(ctx, station)
    elif station_name == "Agitator" and speedups.get("agitator"):
        _process_agitator_speedup(ctx, station)
    elif station_name == "Alembic" and speedups.get("alembic"):
        _process_alembic_speedup(ctx, station)

    # Wait for the station to finish and the processed potion to land back in inventory --
    # polled via inventory count, same confirmation idiom as golem_crafting.py's chisel_phase.
    deadline = time.time() + 20.0
    starting_count = len(controller.get_inventory())
    while time.time() < deadline:
        time.sleep(0.3)
        if len(controller.get_inventory()) != starting_count:
            return


def _process_retort_speedup(ctx, station: dict) -> None:
    """No timing window (confirmed: the reference plugin tracks no varbit/graphics cue for the
    Retort at all, consistent with the wiki's "click every tick" description) -- click every
    tick while /mixology-state still reports a potion loaded in the Retort."""
    deadline = time.time() + 20.0
    while time.time() < deadline:
        state = controller.get_mixology_state()
        if not state.get("stations", {}).get("retort", {}).get("potionName"):
            return
        controller.click_entity(ctx.ser, station, check_ui_blockers=False)
        time.sleep(random.uniform(0.55, 0.75))  # one game tick


def _process_agitator_speedup(ctx, station: dict) -> None:
    """Click again when graphics-object id 2954 (VFX_MACHINERY_ALCHEMY01_AGITATOR01) appears at
    the Agitator's tile -- the visual "fireworks" cue. Bounded wait: per the wiki, a
    consistently-missed window is worse than not attempting the speedup, so this gives up
    cleanly rather than risking a late click against a closed window."""
    deadline = time.time() + 15.0
    while time.time() < deadline:
        graphics = controller.get_graphics_objects()
        cue = next(
            (g for g in graphics
             if g.get("id") == AGITATOR_CUE_GRAPHICS_ID
             and g.get("x") == station.get("x") and g.get("y") == station.get("y")),
            None,
        )
        if cue is not None:
            controller.click_entity(ctx.ser, station, check_ui_blockers=False)
            return
        time.sleep(0.15)


def _process_alembic_speedup(ctx, station: dict) -> None:
    """Same shape as the Agitator's speedup -- click again when graphics-object id 2955
    (VFX_MACHINERY_ALCHEMY01_ALEMBIC01) appears at the Alembic's tile."""
    deadline = time.time() + 15.0
    while time.time() < deadline:
        graphics = controller.get_graphics_objects()
        cue = next(
            (g for g in graphics
             if g.get("id") == ALEMBIC_CUE_GRAPHICS_ID
             and g.get("x") == station.get("x") and g.get("y") == station.get("y")),
            None,
        )
        if cue is not None:
            controller.click_entity(ctx.ser, station, check_ui_blockers=False)
            return
        time.sleep(0.15)
```

- [ ] **Step 2: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add process phase: station routing with graphics-cue-based speedup clicks"
```

---

### Task 7: `deposit` phase and digweed collection

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `find_object` (Task 2), `controller.get_mixology_state()` (Task 3).
- Produces: `deposit_phase(ctx, config)` (sets `mm_phase` to `"orders"`),
  `maybe_collect_digweed(ctx, config)` (sets `ctx.state.data["mm_have_digweed"]`, consumed by
  Task 5's `mix_phase`).

- [ ] **Step 1: Write `deposit_phase`**

```python
def deposit_phase(ctx, config: dict) -> None:
    belt = find_object("Conveyor belt")
    if belt is None:
        ctx.state.trigger_safety_stop("no conveyor belt found near the player")
        return

    controller.click_entity(ctx.ser, belt, check_ui_blockers=False)
    time.sleep(random.uniform(0.5, 0.8))
    ctx.state.data["mm_phase"] = "orders"
```

- [ ] **Step 2: Write `maybe_collect_digweed`**

Polled every tick regardless of phase (wired into `tick` in Task 8), rather than tied to one
specific phase — a digweed corner can go ready at any point in the cycle, and `/mixology-state`'s
`digweed` field is cheap enough to check unconditionally:

```python
DIGWEED_CORNERS = ["northEast", "southEast", "southWest", "northWest"]  # /mixology-state's
                                                                          # digweed dict keys


def maybe_collect_digweed(ctx, config: dict) -> None:
    if ctx.state.data.get("mm_have_digweed"):
        return  # already holding one -- per the wiki, holding it suppresses new spawns anyway
    if not config.get("digweed", {}).get("enabled", True):
        return

    state = controller.get_mixology_state()
    digweed = state.get("digweed", {})
    if not any(digweed.get(corner) for corner in DIGWEED_CORNERS):
        return

    plant = find_object("Digweed")
    if plant is None:
        return  # ready per the varbit but not yet visible/already collected by the time we look

    controller.click_entity(ctx.ser, plant, check_ui_blockers=False)
    time.sleep(random.uniform(0.5, 0.8))
    if find_item("Digweed") is not None:
        ctx.state.data["mm_have_digweed"] = True
```

- [ ] **Step 3: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add deposit phase and digweed collection (varbit-polled, not chat-message-driven)"
```

---

### Task 8: Phase dispatcher (`tick`)

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: every phase function from Tasks 3, 4, 5, 6, 7, `check_level_requirement` (Task 2),
  `maybe_collect_digweed` (Task 7).
- Produces: `tick(ctx)`, `setup(ctx)` — the routine contract's two required/optional entry points
  `controller.py` calls directly.

- [ ] **Step 1: Write the dispatcher**

```python
PHASES = {
    "bank_herbs": bank_herbs_phase,
    "orders": orders_phase,
    "mix": mix_phase,
    "process": process_phase,
    "deposit": deposit_phase,
}


def setup(ctx) -> None:
    ctx.state.data.setdefault("mm_phase", "bank_herbs")


def tick(ctx) -> None:
    config = ctx.state.config
    if not ctx.state.data.get("mm_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["mm_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    maybe_collect_digweed(ctx, config)

    phase = ctx.state.data.get("mm_phase", "bank_herbs")
    handler = PHASES.get(phase)
    if handler is None:
        ctx.state.trigger_safety_stop(f"unknown mastering_mixology phase '{phase}'")
        return
    handler(ctx, config)
```

- [ ] **Step 2: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add mastering_mixology.py phase dispatcher (tick/setup)"
```

---

### Task 9: Controller/RuneLite wiring

**Files:**
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `src/main/java/com/example/controller/ControllerConfig.java`
- Modify: `src/main/java/com/example/controller/ConfigExporter.java`
- Modify: `ControllerAddon/controller.py`

**Interfaces:**
- Consumes: `MixologyProvider` (Task 1).
- Produces: a fully wired routine, selectable from the Controller plugin's settings panel and
  runnable via `python controller.py routines/mastering_mixology.py`.

- [ ] **Step 1: Register the provider in `ControllerPlugin.java`**

Add, alongside the other provider registrations in `startUp()`:

```java
		new MixologyProvider(client, clientThread, server).register();
```

- [ ] **Step 2: Add the routine choice**

In `RoutineChoice.java`, add a new enum constant after `GOLEM_CRAFTING`:

```java
	GOLEM_CRAFTING("Golem Crafting"),
	MASTERING_MIXOLOGY("Mastering Mixology");
```

(Adjust the preceding line's trailing comma/semicolon to match — `GOLEM_CRAFTING` is currently
the last entry, terminated with `;`.)

- [ ] **Step 3: Add the config section**

In `ControllerConfig.java`, following the `golemCraftingSection` pattern exactly:

```java
	@ConfigSection(name = "Mastering Mixology", description = "Mastering Mixology routine settings", position = 14)
	String masteringMixologySection = "masteringMixology";

	// ============================================================
	// Mastering Mixology
	// ============================================================

	@ConfigItem(
		keyName = "masteringMixologyEnabled",
		name = "Mastering Mixology Enabled",
		description = "Secondary switch on top of launching controller.py with routines/mastering_mixology.py.",
		section = masteringMixologySection,
		position = 0
	)
	default boolean masteringMixologyEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "masteringMixologyBankTeleportFallback",
		name = "Teleport If No Bank Nearby",
		description = "Use the configured teleport fallback if no bank is within walking distance of the lab.",
		section = masteringMixologySection,
		position = 1
	)
	default boolean masteringMixologyBankTeleportFallback()
	{
		return false;
	}

	@ConfigItem(
		keyName = "masteringMixologyDigweedEnabled",
		name = "Collect Digweed",
		description = "Collect and use digweed spawns when they appear.",
		section = masteringMixologySection,
		position = 2
	)
	default boolean masteringMixologyDigweedEnabled()
	{
		return true;
	}

	@ConfigItem(
		keyName = "masteringMixologySpeedupsEnabled",
		name = "Processing Speedups",
		description = "Perform the timing-based speedup clicks at each processing station.",
		section = masteringMixologySection,
		position = 3
	)
	default boolean masteringMixologySpeedupsEnabled()
	{
		return true;
	}
```

(This exposes one combined speedups toggle, not three per-station toggles — the per-station
`speedups.{retort,agitator,alembic}` dict in `DEFAULT_CONFIG` stays a Python-side default the
player edits directly if they want to disable just one station, same reasoning as
`golem_crafting.py`'s `furPriority` not being Java-exposed. `ConfigExporter` maps this one
Java toggle to all three Python dict keys.)

Position numbers 0-3 assume no other item currently occupies `masteringMixologySection` —
confirm no collision with existing position numbers in that section before finalizing (there
shouldn't be any, since this is a brand new section).

- [ ] **Step 4: Add the export function**

In `ConfigExporter.java`, following `exportGolemCrafting` exactly:

```java
	public static Map<String, Object> exportMasteringMixology(ControllerConfig config)
	{
		Map<String, Object> data = new LinkedHashMap<>();
		data.put("enabled", config.masteringMixologyEnabled());

		Map<String, Object> bank = new LinkedHashMap<>();
		bank.put("teleportFallback", config.masteringMixologyBankTeleportFallback());
		data.put("bank", bank);

		Map<String, Object> digweed = new LinkedHashMap<>();
		digweed.put("enabled", config.masteringMixologyDigweedEnabled());
		data.put("digweed", digweed);

		Map<String, Object> speedups = new LinkedHashMap<>();
		boolean speedupsEnabled = config.masteringMixologySpeedupsEnabled();
		speedups.put("retort", speedupsEnabled);
		speedups.put("agitator", speedupsEnabled);
		speedups.put("alembic", speedupsEnabled);
		data.put("speedups", speedups);

		data.put("breaks", sharedBreaks(config));
		data.put("safety", sharedSafety(config));
		return data;
	}
```

- [ ] **Step 5: Register the export endpoint**

In `ControllerPlugin.java`, alongside the other `server.register("/*-config", ...)` calls:

```java
		server.register("/mastering-mixology-config", () -> ConfigExporter.exportMasteringMixology(config));
```

- [ ] **Step 6: Wire the Python side**

In `ControllerAddon/controller.py`:

```python
def get_mastering_mixology_config():
    return api_get("/mastering-mixology-config")
```

And in `ROUTINE_FILES`:

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
    "MASTERING_MIXOLOGY": "routines/mastering_mixology.py",
}
```

- [ ] **Step 7: Compile clean**

Run: `gradlew.bat compileJava`
Expected: BUILD SUCCESSFUL, no errors.

- [ ] **Step 8: Run the full test suites**

Run: `gradlew.bat test` (expect existing suite unaffected, no new Java tests added by this task)
and `python -m py_compile controller.py routines/mastering_mixology.py` (from `ControllerAddon/`)
and `pytest tests/` (from `ControllerAddon/`, expect existing suite unaffected).

- [ ] **Step 9: Commit**

```bash
git add src/main/java/com/example/controller/ControllerPlugin.java \
        src/main/java/com/example/controller/RoutineChoice.java \
        src/main/java/com/example/controller/ControllerConfig.java \
        src/main/java/com/example/controller/ConfigExporter.java \
        ControllerAddon/controller.py
git commit -m "Wire Mastering Mixology into ControllerPlugin, RoutineChoice, and controller.py"
```

---

### Task 10 (LIVE): Verification

Every value this plan uses is a confirmed constant (direct jar lookup, cross-referenced against
a real published plugin's usage), so this is a much smaller live pass than a typical new-routine
plan in this codebase — confirming *behavior*, not discovering unknowns.

**Files:** none (verification only).

- [ ] **Step 1: Confirm `/mixology-state` against what's on screen**

Launch the client (`gradlew.bat run`), stand in the lab with active orders, and compare
`GET /mixology-state`'s `orders`/`hopper`/`stations`/`digweed` fields against what's actually
displayed. This is the one step that can invalidate downstream assumptions if it's wrong — in
particular, confirm `hopper` (`MM_AVAILABLE_MOX`/`_AGA`/`_LYE`, this plan's lowest-confidence
constants) reads a sane number matching the displayed hopper amount.

- [ ] **Step 2: Confirm the speedup cue detection**

Trigger the Agitator's and Alembic's processing, and watch `/graphics-objects` for id
`2954`/`2955` appearing at the right moment (cross-reference against the in-game visual/sound
cue). Confirm `_process_retort_speedup`'s `/mixology-state` polling correctly detects when the
Retort's potion clears.

- [ ] **Step 3: One manual cycle**

Walk through bank → refine → hopper → read one order set → mix → process → deposit by hand,
confirming every object name/menu option in this plan resolves as expected. Specifically confirm
`VESSEL_ID_TO_BASE`'s ordering (Task 5): mix at least an Aqualux amalgam or Azure aura mix, and
an Anti-leech lotion or Megalite liquid, checking `/objects`' reported id against the table —
these are the two positions where the wiki-infobox order and the varbit type-index order
diverge, so they're the ones actually worth confirming rather than assuming the wiki got it
right.

- [ ] **Step 4: Short live run**

Launch per `CLAUDE.md`'s live-debugging workflow (`python -u controller.py
routines/mastering_mixology.py`, logged to a file, polled with `Read`). Watch phase transitions
in the debug log through several full `orders → mix → process → deposit` cycles.

- [ ] **Step 5: Confirm the max-points selection rule live**

Watch at least one cycle with a single-paste order present (confirm it's skipped when no MAL is
present) and, if one appears during the session, one cycle with an MAL order present (confirm
all 3, including any single-paste orders, get made).

- [ ] **Step 6: Confirm digweed end-to-end**

Let a digweed spawn occur naturally during the run (or wait for one deliberately). Confirm
`maybe_collect_digweed` fires, the plant is collected (confirm `find_item("Digweed")`'s
substring match resolves to the item's real name), and it gets mixed into the next MAL base made.

- [ ] **Step 7: Longer unattended soak run**

Only after Steps 1-6 all pass clean.
