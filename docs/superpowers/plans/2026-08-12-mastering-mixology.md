# Mastering Mixology Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Tasks 1 and 2 are live/interactive, not background-dispatchable.** They require a running
> RuneLite client with the player's own account standing in the Mastering Mixology lab
> (Aldarin), with an active order, so their widget/graphics content actually has real data to
> inspect. Do not dispatch these to an autonomous background subagent — drive them the same way
> `CLAUDE.md`'s live-debugging workflow and the bridge-tick-cache-freshness plan's Task 6 handled
> live/account-touching verification: interactively, with the human partner present. Every other
> task is a normal, self-contained code task.

**Goal:** Build `routines/mastering_mixology.py`, automating Mastering Mixology under the
max-points-per-paste strategy (bank herbs → refine → hopper → read orders → mix → process →
deposit), plus the one new Java provider it needs.

**Architecture:** A phase state machine in `ctx.state.data["mm_phase"]`, the same shape as
`golem_crafting.py`'s. Six physical stations (refiner, 3 levers, hopper, mixing vessel, 3
processing stations, conveyor belt) are all directly clickable objects with known IDs from the
wiki — no new research needed there. The 3 active orders and hopper paste totals live in a HUD
widget with no existing RuneLite mapping; a new `MixologyProvider` exposes them once their real
IDs are found live (Task 1). Processing-station speedup timing cues are similarly undiscovered
(Task 2).

**Tech Stack:** Java (RuneLite plugin), Python (`ControllerAddon/`).

## Global Constraints

- Optimization strategy is max total points per paste only (see the design doc's "Optimization
  strategy" section) — no other strategy is built this pass.
- The game itself only ever offers orders the player has the Herblore level for (confirmed via
  the 2024-09-27 hotfix changelog in `data/raw/M/Mastering Mixology.txt`) — the routine does
  **not** implement its own order-level filtering; it trusts whatever `/mixology-orders` returns.
- **Always re-read hopper totals, vessel content, and active orders from the bridge rather than
  tracking them locally** — see the design doc's Error handling section for why (the vessel's
  "4th lever overrides the oldest paste" mechanic plus this codebase's simulated-misclick
  humanization make locally-tracked state actively dangerous here, not just imprecise).
- Known object IDs (from `data/raw/`, unverified live — confirm during Task 12's walkthrough,
  but click by **name**, not by these numeric IDs, matching every other routine's convention —
  the IDs below are for *reading which content variant is present*, e.g. the mixing vessel's
  current mix, not for clicking):
  - Refiner `54904` ("Operate"), Mox lever `54868` / Aga lever `54867` / Lye lever `54869`
    ("Operate"), Hopper `54903` ("Deposit"), Conveyor belt `54917` ("Fulfil-order").
  - Mixing vessel ("Take-from"): `54918` empty, `54919` Mammoth-might mix, `54920` Mystic mana
    amalgam, `54921` Marley's moonlight, `54922` Alco-augmentator, `54923` Aqualux amalgam,
    `54924` Azure aura mix, `54925` Liplack liquor, `54926` Anti-leech lotion, `54927` Megalite
    liquid, `54928` Mixalot.
  - Retort ("Concentrate-potion"): `54870`-`54880`, same 11-way content order as the vessel.
  - Agitator ("Homogenise-potion"): `54881`-`54891`, same order.
  - Alembic ("Crystalise-potion"): `54892`-`54902`, same order.
- Recipe table (paste combination per base, all mox/aga/lye counts sum to 3):
  AAA={mox:0,aga:3,lye:0}, MMM={mox:3,aga:0,lye:0}, LLL={mox:0,aga:0,lye:3},
  MMA={mox:2,aga:1,lye:0}, MML={mox:2,aga:0,lye:1}, AAM={mox:1,aga:2,lye:0},
  ALA={mox:0,aga:2,lye:1}, MLL={mox:1,aga:0,lye:2}, ALL={mox:0,aga:1,lye:2},
  MAL={mox:1,aga:1,lye:1}.
- Windows environment: Gradle via `gradlew.bat`, not `./gradlew`.

## File Structure

- Create: `src/main/java/com/example/providers/MixologyProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java` (register the provider)
- Modify: `src/main/java/com/example/controller/RoutineChoice.java` (new enum entry)
- Modify: `src/main/java/com/example/controller/ControllerConfig.java` (new config section)
- Modify: `src/main/java/com/example/controller/ConfigExporter.java` (new export function)
- Modify: `ControllerAddon/controller.py` (`ROUTINE_FILES` entry, config wrapper)
- Create: `ControllerAddon/routines/mastering_mixology.py`

---

### Task 1 (LIVE): `MixologyProvider` — orders, hopper totals, digweed identifiers

**Files:**
- Create: `src/main/java/com/example/providers/MixologyProvider.java`

**Interfaces:**
- Produces: `GET /mixology-orders` → `{"orders": [{"name": str, "mox": int, "aga": int, "lye":
  int, "points": int}, ...], "hopper": {"mox": int, "aga": int, "lye": int}}` — consumed by
  Task 5 (`orders` phase). Also produces: the digweed object's real name (for Task 9's
  `find_object` lookup) and the exact chat message text announcing its spawn (for Task 9's
  `on_event` matcher).

- [ ] **Step 1: Get into position**

Launch the client (`gradlew.bat run` from the repo root, per `CLAUDE.md`'s live-debugging
workflow). With the player standing in the Mastering Mixology lab and at least one active order
showing on the HUD, enable RuneLite's built-in Developer Tools plugin (Configuration → search
"Developer Tools" → enable; it's off by default) and open its Widget Inspector.

- [ ] **Step 2: Find the order-HUD widget IDs**

Hover each of the 3 order slots in the Widget Inspector overlay. Record, for each: the widget
group ID, the child ID(s) that hold the order's name/combination/points text, and the exact text
format (e.g. is the combination shown as icons, as a "2x Mox paste" string, or as the 3-letter
mnemonic like "MMA"?). Note whether the 3 slots are separate top-level widgets or children of one
container widget.

- [ ] **Step 3: Find the hopper-totals widget IDs**

Same tool, hovering the hopper paste counters in the HUD. Record the group/child ID for each of
the 3 paste types' current count.

- [ ] **Step 4: Find digweed's real object name and spawn message**

Wait for (or otherwise trigger, if there's an in-game way to check current digweed state) a
digweed spawn. Record the exact chatbox message text (case-sensitive) and the object's real name
as reported by `GET /objects` while it's present (`curl http://localhost:8080/objects?radius=15`
filtered to something plant-shaped in a room corner).

- [ ] **Step 5: Implement `MixologyProvider.java`**

Using the IDs found in Steps 2-3, write the provider following this codebase's established
shape (plain `Map`/`List` JSON via `BridgeProvider`, always-live — no `TickCache`, this data
changes on player action, not a fixed tick cadence, matching `PlayerProvider`'s pattern, not the
six providers `TickCache` covers):

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class MixologyProvider extends BridgeProvider
{
	public MixologyProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/mixology-orders", () -> onClient(this::getOrders));
	}

	private Object getOrders()
	{
		Map<String, Object> data = new HashMap<>();
		data.put("orders", computeOrders());
		data.put("hopper", computeHopper());
		return data;
	}

	// TODO(Task 1 live investigation): replace with the real group/child widget IDs found in
	// Steps 2-3, and parse each order slot's actual text format (icons vs. "2x Mox paste" vs.
	// mnemonic) into {name, mox, aga, lye, points} using the RECIPE_TABLE constant this file
	// should define locally (same combination table as this plan's Global Constraints section).
	private List<Map<String, Object>> computeOrders()
	{
		throw new UnsupportedOperationException("widget IDs not yet found -- see Task 1");
	}

	// TODO(Task 1 live investigation): replace with the real group/child widget IDs found in
	// Step 3.
	private Map<String, Object> computeHopper()
	{
		throw new UnsupportedOperationException("widget IDs not yet found -- see Task 1");
	}
}
```

Replace both `TODO`/`throw` bodies with real `client.getWidget(groupId, childId)` reads and text
parsing once Steps 2-3's findings are in hand — this step is not complete until both methods
return real data, confirmed by querying `/mixology-orders` live against an active order and
checking the JSON matches what's actually on screen.

- [ ] **Step 6: Confirm live**

`curl http://localhost:8080/mixology-orders` (or `Invoke-RestMethod` in PowerShell) while
standing at the minigame with active orders. Confirm the returned `orders` array's 3 entries
match what's actually shown on the HUD (name, combination, points), and `hopper` matches the
displayed paste counts.

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/providers/MixologyProvider.java
git commit -m "Add MixologyProvider: active orders and hopper paste totals"
```

---

### Task 2 (LIVE): Processing-station speedup cue detection

**Files:**
- No file is mandated up front — this task's deliverable is a documented, live-confirmed
  detection method, consumed by Task 7 (`process` phase). If the method needs bridge support
  beyond what already exists (`/graphics-objects`, `/player`, a station's "Check" menu option's
  resulting chat message), extend the relevant existing provider rather than building a new one
  — `GraphicsObjectProvider`/`PlayerProvider` are both already general enough to cover an
  animation-id or graphics-object-id-based signal without any code change, only a new constant
  in the Python routine once the ID is known.

**Interfaces:**
- Produces: for each of the 3 stations, either (a) a specific `graphicsObjectId` to watch for via
  `GET /graphics-objects` at the station's tile, or (b) a specific animation ID to watch for via
  `GET /player`, or (c) confirmation that the "Check" menu option's resulting chat message is a
  reliable enough progress signal to poll instead. Consumed by Task 7.

- [ ] **Step 1: Alembic — find the 4th-pump signal**

Start crystallising a potion at the Alembic. While it pumps, poll `GET /graphics-objects`
(filtered to the Alembic's tile) and `GET /player` (watching `animationId`) once per tick,
logging every change. Identify what changes specifically on the 4th pump (a new graphics object
id appearing, an animation id change, or neither — in which case, test whether "Check"'s message
text includes a pump count).

- [ ] **Step 2: Agitator — find the fireworks cue**

Same method, homogenising a potion at the Agitator, watching for whatever signals the
sound/visual "fireworks" cue the wiki describes (graphics object appearing is the most likely
candidate, matching `GraphicsObjectProvider`'s own stated design intent for "a boss's telegraphed
AoE attack landing on a tile" — this is architecturally the same kind of transient tile-anchored
effect).

- [ ] **Step 3: Retort — confirm the every-tick click has no cue at all**

Per the wiki, the Retort's speedup is unconditional (click every tick while processing, no
timing window) — confirm live that `GET /player`'s `isIdle`/animation state (or the Retort
object's own id/still-being-`Concentrate-potion`-clickable state) is enough to know "processing
is still in progress, keep clicking," with no cue to detect at all.

- [ ] **Step 4: Document findings**

Record, in a comment block at the top of Task 7's `process_phase` function (written in Task 7,
not here) or in this plan's own progress ledger, the exact detection method for each station —
whichever constant/id/message-substring each needs.

---

### Task 3: `routines/mastering_mixology.py` scaffolding

**Files:**
- Create: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG`, `get_config(bridge_config)`, `find_item(name)`, `find_object(name)`,
  `check_level_requirement(ctx, config)`, `setup(ctx)` — consumed by every later task in this file.

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

import controller
import debug_log

MAX_OBJECT_DISTANCE = 15
HERBLORE_LEVEL_REQUIRED = 60

# name -> {mox, aga, lye, points} -- see this plan's Global Constraints for the full table.
RECIPE_TABLE = {
    "Alco-augmentator": {"mox": 0, "aga": 3, "lye": 0},
    "Mammoth-might mix": {"mox": 3, "aga": 0, "lye": 0},
    "Liplack liquor": {"mox": 0, "aga": 0, "lye": 3},
    "Mystic mana amalgam": {"mox": 2, "aga": 1, "lye": 0},
    "Marley's moonlight": {"mox": 2, "aga": 0, "lye": 1},
    "Azure aura mix": {"mox": 1, "aga": 2, "lye": 0},
    "Aqualux amalgam": {"mox": 0, "aga": 2, "lye": 1},
    "Megalite liquid": {"mox": 1, "aga": 0, "lye": 2},
    "Anti-leech lotion": {"mox": 0, "aga": 1, "lye": 2},
    "Mixalot": {"mox": 1, "aga": 1, "lye": 1},
}
SINGLE_PASTE_BASES = {"Alco-augmentator", "Mammoth-might mix", "Liplack liquor"}
MAL_BASE = "Mixalot"

# Which processing station each base needs -- see the design doc's "Recipe reference"; every
# base is either Alembic (crystalise), Retort (concentrate), or Agitator (homogenise) at
# different points in the minigame's own randomized assignment, NOT a fixed 1:1 per base name.
# ASSUMPTION, NOT YET VERIFIED LIVE: this needs confirming against /mixology-orders' actual
# fields once Task 1 lands -- if the process type is part of that endpoint's response, use it
# directly instead of a static table.

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
    exact-match idiom as golem_crafting.py's find_item (a substring match here would risk
    e.g. "Mox paste" matching an unrelated item with "paste" in its name)."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
        None,
    )


def find_object(name: str):
    """Closest visible object whose name contains `name`, within MAX_OBJECT_DISTANCE -- same
    shape as golem_crafting.py's find_object, including its radius fix (see that function's own
    docstring for why an unbounded /objects call is a measurable source of client stutter and
    api_get timeouts)."""
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
    reasoning as golem_crafting.py's check_level_requirement (querying /skills from setup()
    would run pre-login and false-trigger a permanent safety stop)."""
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

### Task 4: `bank_herbs` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `find_object` (Task 3), `banker.bank_up(ctx, config)` (existing, unmodified —
  `ControllerAddon/banker.py:592`), `controller.get_mixology_orders()` (new Python wrapper this
  step adds, calling Task 1's `/mixology-orders`).
- Produces: `bank_herbs_phase(ctx, config)`, setting `ctx.state.data["mm_phase"]` to `"orders"`
  on completion.

- [ ] **Step 1: Add the `get_mixology_orders` wrapper to `controller.py`**

In `ControllerAddon/controller.py`, alongside the other `get_*` wrappers:

```python
def get_mixology_orders():
    return api_get("/mixology-orders") or {"orders": [], "hopper": {"mox": 0, "aga": 0, "lye": 0}}
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
    /mixology-orders' hopper totals reaches hopperTarget, or safety-stops if the bank has none
    of a paste type's configured herb candidates left at all."""
    hopper = controller.get_mixology_orders().get("hopper", {})
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

- [ ] **Step 3: Add the `import banker` this phase needs**

Add `import banker` near the top of `mastering_mixology.py` (alongside the existing `import
controller`) — Step 2 uses `banker.find_nearby_bank`/`open_bank`/`deposit_inventory`/
`withdraw_loadout`/`close_bank`.

- [ ] **Step 4: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py controller.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py ControllerAddon/controller.py
git commit -m "Add bank_herbs phase: withdraw/refine/hopper herbs to a configured paste target"
```

---

### Task 5: `orders` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `controller.get_mixology_orders()` (Task 4), `RECIPE_TABLE`/`SINGLE_PASTE_BASES`/
  `MAL_BASE` (Task 3).
- Produces: `orders_phase(ctx, config)`, writing `ctx.state.data["mm_chosen_orders"]` (a list of
  the order dicts to actually make this cycle) and setting `mm_phase` to `"mix"` — consumed by
  Task 6.

- [ ] **Step 1: Write the failing-order-selection unit-style check (manual, no test framework)**

This codebase has no unit-test framework for routine logic (per `golem_crafting-design.md`'s
Testing section — verification here is live). Before wiring this into the phase machine, sanity
check the selection rule against the wiki's own worked cases by hand:
- 3 orders, none MAL, one AAA present → make only the two non-single-paste orders, skip AAA.
- 3 orders, one is MAL → make all 3, including any single-paste orders present.
- All 3 orders are single-paste → make only the most-wanted single-paste type (see Step 2 for
  what "most wanted" means here).

- [ ] **Step 2: Write `orders_phase`**

```python
def _choose_orders(orders: list) -> list:
    """Max-points-per-paste selection: make every non-single-paste order, plus every
    single-paste order too if an MAL order is present in this same set of 3. If ALL 3 are
    single-paste, make only the one whose paste type currently has the lowest hopper stock
    (spends down whichever paste is most abundant... no -- spends down whichever is SCARCEST,
    to keep the three roughly balanced over time, since single-paste orders are the only lever
    this routine has for correcting an imbalance; skipping the other two loses no points since
    single-paste orders are net-negative on their own per the wiki's reward-ratio table)."""
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
    data = controller.get_mixology_orders()
    orders = data.get("orders", [])
    hopper = data.get("hopper", {})

    if len(orders) < 3:
        # Bridge/widget read came back short -- don't act on a partial order set.
        return

    chosen = _choose_orders(orders)
    if chosen and all(o.get("name") in SINGLE_PASTE_BASES for o in chosen):
        # All single-paste: keep only the one whose paste type the hopper has the LEAST of, per
        # _choose_orders' docstring.
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

### Task 6: `mix` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `ctx.state.data["mm_chosen_orders"]` (Task 5), `find_object` (Task 3).
- Produces: `mix_phase(ctx, config)`, setting `mm_phase` to `"process"` once every chosen order's
  base has been taken from the vessel into inventory.

- [ ] **Step 1: Write `mix_phase`**

```python
# Mixing vessel object ids by content -- see this plan's Global Constraints for the full table.
# Used to confirm the vessel's actual content after pulling levers, rather than trusting the
# pull count alone (see Global Constraints' "always re-read... rather than tracking locally").
VESSEL_ID_TO_BASE = {
    54919: "Mammoth-might mix", 54920: "Mystic mana amalgam", 54921: "Marley's moonlight",
    54922: "Alco-augmentator", 54923: "Aqualux amalgam", 54924: "Azure aura mix",
    54925: "Liplack liquor", 54926: "Anti-leech lotion", 54927: "Megalite liquid",
    54928: "Mixalot",
}
VESSEL_EMPTY_ID = 54918

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
```

- [ ] **Step 2: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add mix phase: lever pulls with vessel-content reconciliation"
```

---

### Task 7: `process` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: Task 2's live-confirmed speedup detection method (fill in the `TODO`s below with
  its findings — this task cannot be finished until Task 2 is), `find_item`/`find_object`
  (Task 3).
- Produces: `process_phase(ctx, config)`, setting `mm_phase` to `"deposit"` once every unprocessed
  base currently held has been processed.

- [ ] **Step 1: Write `process_phase`**

```python
# Which station each base is processed at is part of a given order's own randomized assignment
# per the wiki (a base isn't fixed to one station across all orders), so this reads it from
# whatever /mixology-orders reports for the order that produced each held base, rather than a
# static base->station table. ASSUMPTION, NOT YET VERIFIED LIVE: confirm during Task 12 that
# /mixology-orders' order entries actually carry a process-type field (Alembic/Retort/
# Agitator) -- if not, Task 1 needs a follow-up to add one, since nothing else exposes it.
STATION_ORDER = ["Alembic", "Retort", "Agitator"]  # wiki's own recommended pathing order


def process_phase(ctx, config: dict) -> None:
    unprocessed = [i for i in controller.get_inventory() if i.get("name") in RECIPE_TABLE]
    if not unprocessed:
        ctx.state.data["mm_phase"] = "deposit"
        return

    for station_name in STATION_ORDER:
        station = find_object(station_name)
        if station is None:
            continue
        due = [i for i in unprocessed if _station_for(i.get("name")) == station_name]
        for potion in due:
            _process_one(ctx, config, station_name, station, potion)

    ctx.state.data["mm_phase"] = "deposit"


def _station_for(base_name: str) -> str:
    """TODO(Task 7, blocked on Task 1): look up the process type from /mixology-orders' own
    order entry for this base once that field exists, rather than guessing. Placeholder returns
    Alembic for everything so this compiles; MUST be replaced before this phase is trusted live."""
    return "Alembic"


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
    """TODO(Task 2 finding): click every tick while the Retort is processing -- no timing
    window per the wiki, confirm live whether some readable state (isIdle, the station's own
    still-clickable Concentrate-potion option) tells us to stop clicking, or whether a fixed
    tick-count loop matching this potion's known processing time is simpler."""
    raise NotImplementedError("fill in from Task 2's live findings")


def _process_agitator_speedup(ctx, station: dict) -> None:
    """TODO(Task 2 finding): click once, then click again on whatever signal Task 2 found for
    the "fireworks" cue (graphics object id at the station's tile, most likely -- see Task 2)."""
    raise NotImplementedError("fill in from Task 2's live findings")


def _process_alembic_speedup(ctx, station: dict) -> None:
    """TODO(Task 2 finding): click once, then click again on the 4th pump per whatever signal
    Task 2 found (graphics object id, animation id, or a pump counter parsed from "Check")."""
    raise NotImplementedError("fill in from Task 2's live findings")
```

- [ ] **Step 2: Replace every `TODO`/`NotImplementedError` with Task 2's live findings**

This step is not optional scaffolding — the task is not done while any of `_station_for`,
`_process_retort_speedup`, `_process_agitator_speedup`, `_process_alembic_speedup` still contain
a placeholder. Use Task 2's documented detection method for each.

- [ ] **Step 3: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add process phase: station routing with configurable speedup clicks"
```

---

### Task 8: `deposit` phase

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `find_object` (Task 3).
- Produces: `deposit_phase(ctx, config)`, setting `mm_phase` to `"orders"`.

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

- [ ] **Step 2: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add deposit phase: fulfil orders via the conveyor belt"
```

---

### Task 9: Digweed `on_event` handler

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: Task 1's live-confirmed digweed object name and spawn-message text.
- Produces: `on_event(ctx, event_type, payload)`, the routine contract's optional UDP-event hook.

- [ ] **Step 1: Write the handler**

```python
# TODO(Task 1 live finding): replace with the exact chat message text and object name found
# during Task 1 Step 4.
DIGWEED_SPAWN_MESSAGE_SUBSTRING = "REPLACE ME"
DIGWEED_OBJECT_NAME = "REPLACE ME"


def on_event(ctx, event_type: str, payload: dict) -> None:
    if event_type != "CHAT_MESSAGE":
        return
    config = ctx.state.config or DEFAULT_CONFIG
    if not config.get("digweed", {}).get("enabled", True):
        return

    message = (payload or {}).get("message", "")
    if DIGWEED_SPAWN_MESSAGE_SUBSTRING.lower() not in message.lower():
        return

    plant = find_object(DIGWEED_OBJECT_NAME)
    if plant is None:
        return  # already gone/withered before we could path to it -- not an error

    controller.click_entity(ctx.ser, plant, check_ui_blockers=False)
    time.sleep(random.uniform(0.5, 0.8))
    ctx.state.data["mm_have_digweed"] = True
```

- [ ] **Step 2: Wire digweed into `mix_phase`**

In Task 6's `mix_phase`, right after a base is successfully taken from the vessel (the
`ctx.state.data["mm_made_bases"] = made + [...]` line), add:

```python
    if ctx.state.data.get("mm_have_digweed") and target_order.get("name") == MAL_BASE:
        digweed = find_item(DIGWEED_OBJECT_NAME)
        base_item = find_item(MAL_BASE)
        if digweed is not None and base_item is not None:
            controller.click_entity(ctx.ser, digweed, check_ui_blockers=False)
            time.sleep(random.uniform(0.15, 0.3))
            controller.click_entity(ctx.ser, base_item, check_ui_blockers=False)
            time.sleep(random.uniform(0.3, 0.5))
            ctx.state.data["mm_have_digweed"] = False
```

- [ ] **Step 3: Replace the `REPLACE ME` placeholders with Task 1's findings**

This task is not done while `DIGWEED_SPAWN_MESSAGE_SUBSTRING`/`DIGWEED_OBJECT_NAME` still read
`"REPLACE ME"`.

- [ ] **Step 4: Byte-compile**

Run (from `ControllerAddon/`): `python -m py_compile routines/mastering_mixology.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Add digweed CHAT_MESSAGE interrupt: collect and save for the next MAL base"
```

---

### Task 10: Phase dispatcher (`tick`)

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: every phase function from Tasks 4-8, `check_level_requirement` (Task 3).
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

### Task 11: Controller/RuneLite wiring

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

### Task 12 (LIVE): Verification

Same staged-verification convention as the design doc's Testing section and
`golem_crafting.py`'s own precedent — this has more unverified surface than a typical routine.

**Files:** none (verification only).

- [ ] **Step 1: Confirm the `_station_for` assumption**

Before anything else, confirm live whether `/mixology-orders`' order entries actually need a
process-type field (per Task 7's flagged assumption). If they don't carry one, this blocks
`process_phase` from working correctly and needs a follow-up to `MixologyProvider` before
continuing.

- [ ] **Step 2: One manual cycle**

Walk through bank → refine → hopper → read one order set → mix → process → deposit by hand,
confirming every object name/menu option in this plan resolves as expected (per this plan's
Global Constraints table).

- [ ] **Step 3: Short live run**

Launch per `CLAUDE.md`'s live-debugging workflow (`python -u controller.py
routines/mastering_mixology.py`, logged to a file, polled with `Read`). Watch phase transitions
in the debug log through several full `orders → mix → process → deposit` cycles.

- [ ] **Step 4: Confirm the max-points selection rule live**

Watch at least one cycle with a single-paste order present (confirm it's skipped when no MAL is
present) and, if one appears during the session, one cycle with an MAL order present (confirm
all 3, including any single-paste orders, get made).

- [ ] **Step 5: Confirm digweed end-to-end**

Let a digweed spawn occur naturally during the run (or wait for one deliberately). Confirm the
`CHAT_MESSAGE` interrupt fires, the plant is collected, and it gets mixed into the next MAL base
made.

- [ ] **Step 6: Longer unattended soak run**

Only after Steps 1-5 all pass clean.
