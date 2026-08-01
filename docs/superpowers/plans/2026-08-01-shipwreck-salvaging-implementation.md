# Shipwreck Salvaging Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `shipwreck_salvaging` automation routine that deploys the player's boat-mounted salvaging hook near a configured shipwreck tier, drops full-inventory salvage and keeps going, and repositions the boat (via a new coarse minimap-based `sailing.py` helper) when nothing is left in hook range.

**Architecture:** A new `SailingProvider.java` exposes the one bit of UI this needs that no existing provider covers (the Sailing Options panel's Take-down-sails control, plus whether the helm is currently engaged). A new `sailing.py` (parallel to `travel.py`) drives the helm and reuses the existing minimap-click machinery for movement (confirmed live: it works for boats too, just with coarser heading snapping). A new `routines/shipwreck_salvaging.py` ties it together with the same `DEFAULT_CONFIG`/`get_config`/`tick(ctx)` contract every other routine already follows.

**Tech Stack:** Java 11 (RuneLite plugin, Gradle), Python 3 (`ControllerAddon/`, no external test framework — this codebase verifies live against the bridge + Arduino, not with pytest).

## Global Constraints

- Full design rationale and every fact's confidence level lives in `docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md` — read it if anything below seems under-explained.
- RuneLite API is pinned to `latest.release` in `build.gradle` (`compileOnly group: 'net.runelite', name:'client', version: runeLiteVersion`), so `net.runelite.api.gameval.InterfaceID` constants used below (`SailingSidepanel`, `FossilquestSailing`) are expected to resolve at compile time — confirmed baseline: `./gradlew.bat compileJava` → `BUILD SUCCESSFUL` before any of this plan's changes.
- No unit test framework exists on either side. "Tests" in this plan are: `./gradlew.bat compileJava` for Java changes, and `python -m py_compile` / a standalone import for Python changes (confirmed baseline: both work cleanly against existing files). The final task is a manual, live, human-executed verification checklist — it cannot be automated.
- Every "TODO verify" comment placed in code below corresponds to a specific numbered item in the spec's "Still unverified" section — keep that pairing intact; don't silently resolve them by guessing.
- Exact strings confirmed live against the game cache (do not alter): shipwreck tier names ("Small shipwreck", etc.), salvaging hook names ("Bronze salvaging hook", etc.), helm name ("Ship's wheel").
- Routine config follows the existing no-bridge-panel convention (`DEFAULT_CONFIG` + `get_config(bridge_config)` returning it unconditionally), same as `mining.py`/`gemstone_crab.py` — there is no Controller Settings panel section for this routine yet.

---

### Task 1: `SailingProvider.java` — expose the Sailing Options panel

**Files:**
- Create: `src/main/java/com/example/providers/SailingProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:81` (registration)

**Interfaces:**
- Produces: `GET /sailing` → `{"navigating": bool, "facilitiesTabOpen": bool, "takeDownSails": {"screenX": int, "screenY": int} | {}, "setSail": {"screenX": int, "screenY": int} | {}}`. Later tasks (`sailing.py`) call this via a new `controller.get_sailing()`.

- [ ] **Step 1: Write `SailingProvider.java`**

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

import java.awt.Point;
import java.awt.Rectangle;
import java.util.HashMap;
import java.util.Map;

public class SailingProvider extends BridgeProvider
{
	// "Take down sails" / "Set sail" aren't fixed WidgetInfo/InterfaceID constants -- they're
	// dynamically built rows inside the Sailing Options panel's Facilities tab (same situation
	// BankProvider.java already handles for individual bank item slots: dynamic children,
	// matched by their own text at runtime, not a fixed component id). TODO verify in-game (see
	// docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md, "Still unverified" #2)
	// that this text match actually finds the right child -- the row's exact text/position
	// hasn't been confirmed live yet.
	private static final String TAKE_DOWN_SAILS_TEXT = "Take down sails";
	private static final String SET_SAIL_TEXT = "Set sail";

	public SailingProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/sailing", () -> onClient(this::getSailing));
	}

	private Object getSailing()
	{
		Map<String, Object> data = new HashMap<>();

		// FossilquestSailing (interface group 604) is the boat-piloting HUD that the wiki
		// describes opening when the helm's Navigate option is used -- its visibility is the
		// most direct signal for "is the helm currently engaged". TODO verify in-game (see spec
		// "Still unverified" #1) -- this interface group's real purpose was never confirmed live,
		// only inferred from its component names in a stale source checkout.
		Widget steeringHud = client.getWidget(InterfaceID.FossilquestSailing.UNIVERSAL);
		data.put("navigating", steeringHud != null && !steeringHud.isHidden());

		Widget facilitiesContent = client.getWidget(InterfaceID.SailingSidepanel.FACILITIES_CONTENT_CONTAINER);
		boolean facilitiesTabOpen = facilitiesContent != null && !facilitiesContent.isHidden();
		data.put("facilitiesTabOpen", facilitiesTabOpen);

		Map<String, Object> takeDownSails = new HashMap<>();
		Map<String, Object> setSail = new HashMap<>();
		if (facilitiesTabOpen)
		{
			Widget rows = client.getWidget(InterfaceID.SailingSidepanel.FACILITIES_ROWS);
			Widget[] children = rows != null ? rows.getDynamicChildren() : null;
			if (children != null)
			{
				for (Widget child : children)
				{
					if (child == null || child.isHidden())
					{
						continue;
					}

					String text = child.getText();
					if (text == null)
					{
						continue;
					}

					if (text.equalsIgnoreCase(TAKE_DOWN_SAILS_TEXT))
					{
						takeDownSails.putAll(boundsPoint(child));
					}
					else if (text.equalsIgnoreCase(SET_SAIL_TEXT))
					{
						setSail.putAll(boundsPoint(child));
					}
				}
			}
		}
		data.put("takeDownSails", takeDownSails);
		data.put("setSail", setSail);

		return data;
	}

	private Map<String, Object> boundsPoint(Widget widget)
	{
		Map<String, Object> point = new HashMap<>();

		Rectangle bounds = widget.getBounds();
		if (bounds == null)
		{
			return point;
		}

		Point screen = RuneLiteUtil.canvasToScreen(client, (int) bounds.getCenterX(), (int) bounds.getCenterY());
		point.put("screenX", screen.x);
		point.put("screenY", screen.y);
		return point;
	}
}
```

- [ ] **Step 2: Register the provider in `ControllerPlugin.java`**

Find this line (around line 81):

```java
		new BankProvider(client, clientThread, server).register();
		new NPCProvider(client, clientThread, server).register();
```

Change it to:

```java
		new BankProvider(client, clientThread, server).register();
		new SailingProvider(client, clientThread, server).register();
		new NPCProvider(client, clientThread, server).register();
```

- [ ] **Step 3: Compile**

Run: `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL` (confirmed baseline before this change already passes; this step must still pass with `SailingProvider.java` added and referencing `InterfaceID.SailingSidepanel`/`InterfaceID.FossilquestSailing`)

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/providers/SailingProvider.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Add SailingProvider exposing the Sailing Options panel's Take-down-sails control"
```

---

### Task 2: `controller.py` — add the `get_sailing()` bridge accessor

**Files:**
- Modify: `ControllerAddon/controller.py:149-151`

**Interfaces:**
- Consumes: `GET /sailing` from Task 1.
- Produces: `controller.get_sailing() -> dict` with keys `navigating`, `facilitiesTabOpen`, `takeDownSails`, `setSail` (safe defaults if the bridge hasn't responded). Task 3 (`sailing.py`) calls this directly.

- [ ] **Step 1: Add `get_sailing()` next to the other bridge accessors**

Find this block (around line 149):

```python
def get_bank():
    return api_get("/bank") or {"open": False}


def get_equipment():
    return api_get("/equipment") or {}
```

Change it to:

```python
def get_bank():
    return api_get("/bank") or {"open": False}


def get_sailing():
    return api_get("/sailing") or {
        "navigating": False,
        "facilitiesTabOpen": False,
        "takeDownSails": {},
        "setSail": {},
    }


def get_equipment():
    return api_get("/equipment") or {}
```

- [ ] **Step 2: Verify Python syntax**

Run: `cd ControllerAddon && python -m py_compile controller.py`
Expected: no output, exit code 0 (confirmed this is how a clean compile behaves against `travel.py` as a baseline)

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/controller.py
git commit -m "Add controller.get_sailing() bridge accessor"
```

---

### Task 3: `sailing.py` — boat navigation helper

**Files:**
- Create: `ControllerAddon/sailing.py`

**Interfaces:**
- Consumes: `controller.get_sailing()` (Task 2), `controller.get_objects()`, `controller.get_player()`, `controller.get_minimap_walk_point(x, y, plane)`, `controller.click_entity(ser, entity, offset=...)`, `controller.key_tap(ser, key)` (all pre-existing).
- Produces, for Task 4 to consume:
  - `use_helm(ctx) -> bool`
  - `steer_toward(ctx, target: dict) -> bool` (`target` is `{"x": int, "y": int, "plane": int}`, the same shape `get_objects()` entries already carry)
  - `stop_boat(ctx) -> bool`
  - `deploy_hook(ctx, hook_name: str) -> bool`
  - `sail_to(ctx, target: dict) -> bool` (orchestrates `use_helm` → `steer_toward` → `stop_boat`; does **not** deploy the hook afterward — that's the caller's decision)

- [ ] **Step 1: Write `sailing.py`**

```python
# sailing.py
#
# Boat-specific navigation, parallel to travel.py's foot-travel helpers. On open ocean there's
# no click-to-walk pathing -- moving the boat needs the helm engaged first. Steering itself
# reuses the same minimap-click mechanism travel.py already uses for foot travel (confirmed live
# by the user: minimap clicks drive the boat once the helm is engaged), just with coarser
# heading -- it snaps to a handful of compass directions instead of plotting a precise line to
# the clicked point, hence the shorter hop distance and wider arrival tolerance below compared
# to travel.py's own WALK_HOP_MIN/MAX_TILES and WALK_ARRIVAL_TOLERANCE.
#
# ASSUMPTIONS TO VERIFY IN-GAME (same convention as travel.py's own header; numbers refer to
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md's "Still unverified" section):
#   - SAIL_HOP_MIN/MAX_TILES and SAIL_ARRIVAL_TOLERANCE below are a starting guess at how coarse
#     the boat's heading snapping actually is -- tune once observed live (#1).
#   - SAILING_OPTIONS_TAB_KEY is a guess at the keybind that reveals the Sailing Options panel,
#     same idempotent-tap idea as controller.py's INVENTORY_TAB_KEY/PRAYER_TAB_KEY (#5).
#   - HELM_NAME assumes a plain left-click on the helm resolves to "Navigate" -- if not, add
#     "Navigate" to Controller Settings -> Clicking -> Forced Actions (#5).
#   - SailingProvider.java's "Take down sails" widget lookup is a best-effort text match, not yet
#     confirmed against the live client (#2).

import math
import random
import time

import controller

HELM_NAME = "ship's wheel"
SAILING_OPTIONS_TAB_KEY = "q"  # TODO verify -- see module header

SAIL_HOP_MIN_TILES = 6       # shorter than travel.WALK_HOP_MIN_TILES -- coarse heading means
SAIL_HOP_MAX_TILES = 10      # overshooting a long hop is more likely to need correcting
SAIL_ARRIVAL_TOLERANCE = 5   # tiles; wider than travel.WALK_ARRIVAL_TOLERANCE for the same reason
SAIL_HOP_TIMEOUT_S = 10.0
SAIL_MAX_HOPS = 40
NAVIGATE_TIMEOUT_S = 8.0
STOP_TIMEOUT_S = 15.0


def open_sailing_options_tab(ctx) -> None:
    """Tap the Sailing Options tab hotkey unless it's already open -- same idempotent-tap idea
    as controller.py's open_inventory_tab/open_prayer_tab."""
    if controller.get_sailing().get("facilitiesTabOpen"):
        return
    controller.key_tap(ctx.ser, SAILING_OPTIONS_TAB_KEY)
    time.sleep(random.uniform(0.3, 0.6))


def use_helm(ctx) -> bool:
    """Click the boat's own helm ("Ship's wheel"). Confirmed by the user: this both retrieves
    any currently-deployed salvaging hook and engages steering in one action -- no separate
    stop/retrieve step needed first."""
    helm = next(
        (o for o in controller.get_objects()
         if HELM_NAME in (o.get("name") or "").lower() and o.get("visible", True)),
        None,
    )
    if helm is None:
        print("[sailing] helm not found nearby")
        return False

    print("[sailing] engaging the helm")
    if not controller.click_entity(ctx.ser, helm):
        return False

    waited = 0.0
    while waited < NAVIGATE_TIMEOUT_S:
        time.sleep(0.5)
        waited += 0.5
        if controller.get_sailing().get("navigating"):
            return True

    print("[sailing] clicked the helm but never saw navigate mode engage")
    return False


def steer_toward(ctx, target: dict) -> bool:
    """Drive the boat to within SAIL_ARRIVAL_TOLERANCE tiles of target ({"x", "y", "plane"}),
    same hop-and-click-minimap loop shape as travel._walk_greedy -- confirmed live that minimap
    clicks steer the boat once the helm is engaged, just with coarser (compass-snapped) heading
    than foot travel."""
    target_x, target_y, target_plane = target["x"], target["y"], target["plane"]

    for _ in range(SAIL_MAX_HOPS):
        player = controller.get_player()
        px, py, plane = player.get("x"), player.get("y"), player.get("plane")
        if px is None:
            return False

        if plane != target_plane:
            print("[sailing] target is on a different plane -- can't sail there")
            return False

        if _within_tolerance(px, py, target_x, target_y):
            return True

        dist = math.hypot(target_x - px, target_y - py)
        hop_dist = min(dist, random.randint(SAIL_HOP_MIN_TILES, SAIL_HOP_MAX_TILES))
        hop_x = int(round(px + (target_x - px) / dist * hop_dist))
        hop_y = int(round(py + (target_y - py) / dist * hop_dist))

        if not _hop_to(ctx, hop_x, hop_y, plane):
            return False

    return False


def _hop_to(ctx, x: int, y: int, plane: int) -> bool:
    point = controller.get_minimap_walk_point(x, y, plane)
    if not point.get("onMinimap"):
        print("[sailing] hop point isn't on the minimap, giving up")
        return False

    controller.click_entity(ctx.ser, point, offset=(0, 0))

    waited = 0.0
    while waited < SAIL_HOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        px, py = player.get("x"), player.get("y")
        if px is not None and _within_tolerance(px, py, x, y):
            break

    return True


def _within_tolerance(x1: float, y1: float, x2: float, y2: float) -> bool:
    return math.hypot(x2 - x1, y2 - y1) <= SAIL_ARRIVAL_TOLERANCE


def stop_boat(ctx) -> bool:
    """Open the Sailing Options panel, click Take down sails, and wait for the boat to actually
    stop moving."""
    open_sailing_options_tab(ctx)

    sailing_state = controller.get_sailing()
    button = sailing_state.get("takeDownSails") or {}
    if button.get("screenX") is None:
        print("[sailing] Take down sails button not found")
        return False

    print("[sailing] taking down sails")
    controller.click_entity(ctx.ser, button, offset=(0, 0))

    waited = 0.0
    last_pos = None
    while waited < STOP_TIMEOUT_S:
        time.sleep(0.6)
        waited += 0.6
        player = controller.get_player()
        pos = (player.get("x"), player.get("y"))
        if pos[0] is not None and pos == last_pos:
            return True
        last_pos = pos

    print("[sailing] never saw the boat settle -- continuing anyway")
    return True


def deploy_hook(ctx, hook_name: str) -> bool:
    """Click the boat's own salvaging hook facility object by name (e.g. "Bronze salvaging
    hook") -- this is the actual salvaging interaction; the shipwreck object itself only has an
    Inspect option."""
    hook = next(
        (o for o in controller.get_objects()
         if hook_name.lower() in (o.get("name") or "").lower() and o.get("visible", True)),
        None,
    )
    if hook is None:
        print(f"[sailing] {hook_name} not found nearby")
        return False

    print(f"[sailing] deploying {hook.get('name')}")
    return controller.click_entity(ctx.ser, hook)


def sail_to(ctx, target: dict) -> bool:
    """Full reposition: engage the helm, steer to target, take down sails. Does not redeploy the
    hook afterward -- the caller decides when that's appropriate."""
    if not use_helm(ctx):
        return False
    if not steer_toward(ctx, target):
        return False
    return stop_boat(ctx)
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd ControllerAddon && python -c "import sailing; print('sailing.py imports OK')"`
Expected: `sailing.py imports OK` (confirmed this standalone-import pattern works cleanly against `travel.py` as a baseline)

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/sailing.py
git commit -m "Add sailing.py: helm/steering/stop/deploy-hook helpers"
```

---

### Task 4: `routines/shipwreck_salvaging.py` — the routine itself, and wiring

**Files:**
- Create: `ControllerAddon/routines/shipwreck_salvaging.py`
- Modify: `ControllerAddon/controller.py:1110-1115` (`ROUTINE_FILES`)
- Modify: `src/main/java/com/example/controller/RoutineChoice.java:9-15`

**Interfaces:**
- Consumes: `sailing.use_helm/steer_toward/stop_boat/deploy_hook/sail_to` (Task 3), `controller.get_inventory()`, `controller.INVENTORY_SIZE`, `controller.get_objects()`, `controller.click_entity()` (all pre-existing).
- Produces: `DEFAULT_CONFIG`, `get_config(bridge_config)`, `tick(ctx)` — the standard routine contract every routine in `ROUTINE_FILES` implements (see `controller.py`'s module docstring).

- [ ] **Step 1: Write `routines/shipwreck_salvaging.py`**

```python
# routines/shipwreck_salvaging.py
#
# Shipwreck Salvaging routine: deploy the boat's own salvaging hook near a matching shipwreck
# and keep it running, sailing to a new wreck when nothing is left in range. Unlike every other
# routine here, the wreck itself is never clicked -- it only has an Inspect option. The real
# interaction is the salvaging hook facility built on the player's own boat -- see
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md for the full research behind
# this and the list of assumptions still needing in-game verification.
#
# Run with:
#     python controller.py routines/shipwreck_salvaging.py
#
# ASSUMPTIONS TO VERIFY IN-GAME (see the spec's "Still unverified" section for the full list):
#   - Re-clicking an already-deployed hook might not be safe (its default click option may swap
#     once deployed, #3) -- this routine deploys once per arrival and never re-clicks later.
#   - Whether the hook keeps working on its own once the current wreck sinks and a new one rises
#     in the same spot, or needs re-deploying (#4) -- assumed yes (do nothing) until proven
#     otherwise.

import random
import time

import controller
import sailing

DEFAULT_CONFIG = {
    "enabled": True,
    "wreckNames": ["Small shipwreck"],
    "hookName": "Bronze salvaging hook",
    "maxDistance": 15,       # hook-operating range -- gathering happens here, no boat movement
    "maxSailDistance": 60,   # a farther wreck worth repositioning the boat for

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
        "stopOnInventoryFull": False,   # routine drops salvage itself, see drop_salvage
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # This routine doesn't need anything from the Controller Settings panel.
    return DEFAULT_CONFIG


WAITING_MESSAGE_INTERVAL_S = 30.0


def tick(ctx):
    config = ctx.state.config

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        drop_salvage(ctx)
        return

    near_wreck = find_nearby_wreck(config, config.get("maxDistance", 15))
    if near_wreck is not None:
        if not ctx.state.data.get("hook_deployed"):
            if sailing.deploy_hook(ctx, config.get("hookName", "")):
                ctx.state.data["hook_deployed"] = True
        return

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60), require_visible=False)
    if far_wreck is not None:
        ctx.state.data["hook_deployed"] = False
        if sailing.sail_to(ctx, far_wreck):
            if sailing.deploy_hook(ctx, config.get("hookName", "")):
                ctx.state.data["hook_deployed"] = True
        return

    now = time.time()
    if now - ctx.state.data.get("last_waiting_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
        print("[salvage] no shipwreck in range -- waiting")
        ctx.state.data["last_waiting_message_at"] = now


def drop_salvage(ctx):
    """Drop every inventory item whose name ends in "salvage" (covers all 8 tiers -- Small,
    Fishy, Barracuda, Large, Plundered, Martial, Fremennik, Opulent -- without needing a config
    list). Left-click-to-drop needs "Drop" in Controller Settings -> Clicking -> Forced Actions,
    same convention as the looting bag's "Empty" elsewhere in this codebase. Does NOT touch
    ctx.state.data["hook_deployed"] -- dropping items doesn't retrieve the hook (only clicking
    away from it, e.g. the helm, does that -- confirmed by the user), so re-deploying here would
    be an unnecessary, possibly-unsafe re-click (see module header)."""
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if name.endswith("salvage"):
            controller.click_entity(ctx.ser, item)
            time.sleep(random.uniform(0.2, 0.4))


def find_nearby_wreck(config: dict, max_dist: float, require_visible: bool = True):
    """Nearest object matching config["wreckNames"] within max_dist tiles, or None.
    require_visible=True (the default, used for the hook-operating-range check) also requires
    the object to be on-screen-projectable right now. require_visible=False (used for the
    farther sail-range check) only needs its world x/y/plane -- ObjectProvider always sets those
    from the object's WorldPoint regardless of whether it's currently on-screen, which is exactly
    what sailing.sail_to needs to steer toward it."""
    names = {n.lower() for n in config.get("wreckNames", [])}

    candidates = [
        o for o in controller.get_objects()
        if (o.get("name") or "").lower() in names
        and (not require_visible or o.get("visible", True))
        and o.get("x") is not None
        and 0 <= o.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
```

- [ ] **Step 2: Wire it into `ROUTINE_FILES` in `controller.py`**

Find this block (around line 1110):

```python
ROUTINE_FILES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
    "GEMSTONE_CRAB": "routines/gemstone_crab.py",
    "TORMENTED_DEMON": "routines/tormented_demon.py",
}
```

Change it to:

```python
ROUTINE_FILES = {
    "SLAYER": "routines/slayer.py",
    "MINING": "routines/mining.py",
    "GEMSTONE_CRAB": "routines/gemstone_crab.py",
    "TORMENTED_DEMON": "routines/tormented_demon.py",
    "SHIPWRECK_SALVAGING": "routines/shipwreck_salvaging.py",
}
```

- [ ] **Step 3: Add the enum entry in `RoutineChoice.java`**

Find this block:

```java
public enum RoutineChoice
{
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon");
```

Change it to:

```java
public enum RoutineChoice
{
	NONE("None"),
	SLAYER("Slayer / Combat"),
	MINING("Mining"),
	GEMSTONE_CRAB("Gemstone Crab"),
	TORMENTED_DEMON("Tormented Demon"),
	SHIPWRECK_SALVAGING("Shipwreck Salvaging");
```

- [ ] **Step 4: Verify the routine loads the same way `controller.py` itself loads routines**

Run:
```bash
cd ControllerAddon && python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('routine', 'routines/shipwreck_salvaging.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert hasattr(module, 'tick'), 'missing tick(ctx)'
assert hasattr(module, 'DEFAULT_CONFIG'), 'missing DEFAULT_CONFIG'
assert hasattr(module, 'get_config'), 'missing get_config'
print('routine loads OK, config keys:', sorted(module.DEFAULT_CONFIG.keys()))
"
```
Expected: `routine loads OK, config keys: ['breaks', 'enabled', 'hookName', 'maxDistance', 'maxSailDistance', 'safety', 'wreckNames']` (this mirrors `controller.load_routine()`'s own loading mechanism exactly, so a pass here means the routine is loadable the same way the real engine loads it)

- [ ] **Step 5: Verify `controller.py` still compiles after the `ROUTINE_FILES` edit**

Run: `python -m py_compile controller.py`
Expected: no output, exit code 0

- [ ] **Step 6: Verify Java still compiles after the `RoutineChoice.java` edit**

Run (from repo root): `./gradlew.bat compileJava`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py ControllerAddon/controller.py src/main/java/com/example/controller/RoutineChoice.java
git commit -m "Add shipwreck_salvaging routine and wire it into ROUTINE_FILES/RoutineChoice"
```

---

### Task 5: Manual live verification (human-executed, not automatable)

**Files:** none (no code changes)

**Interfaces:** none

This task cannot be done by an agent — it needs the real RuneLite client, the Arduino, and a boat positioned near a shipwreck hotspot. Work through it in order; each step either confirms one of the spec's "Still unverified" items or catches a real bug before an unattended run.

- [ ] **Step 1: Confirm the helm click and steering HUD**

In-game, on your own boat: left-click the helm. Confirm it engages steering (resolves "Still unverified" #5 — if it doesn't, add "Navigate" to Controller Settings -> Clicking -> Forced Actions before continuing). While there, open RuneLite's Widget Inspector devtool and check whether `InterfaceID.FossilquestSailing.UNIVERSAL`'s widget actually becomes visible — if `SailingProvider.getSailing()`'s `navigating` field doesn't track reality, `sailing.use_helm()`'s poll loop in Task 3 will always time out. Fix `SailingProvider.java` now if so (this is a one-widget-id change, not a redesign).

- [ ] **Step 2: Confirm minimap steering and tune hop constants**

Run `python controller.py routines/shipwreck_salvaging.py` with the boat already near a shipwreck hotspot. Watch a `[sailing] engaging the helm` → hop-click sequence in the console. Note how far a single hop actually moves the boat and how coarse the heading snapping is (spec "Still unverified" #1). If hops consistently overshoot or stall, adjust `SAIL_HOP_MIN_TILES`/`SAIL_HOP_MAX_TILES`/`SAIL_ARRIVAL_TOLERANCE` in `sailing.py` (Task 3) accordingly and re-run.

- [ ] **Step 3: Confirm the Take-down-sails button**

While the routine is mid-reposition (or by manually driving through the same steps), open the Widget Inspector on the Sailing Options panel's Facilities tab and confirm a child widget's text actually reads "Take down sails" (or correct `TAKE_DOWN_SAILS_TEXT` in `SailingProvider.java`, Task 1, if it reads something else — spec "Still unverified" #2). Confirm `stop_boat()` actually stops the boat rather than timing out.

- [ ] **Step 4: Confirm hook deploy/re-deploy safety**

Let the routine deploy the hook and run through one full wreck-sink-and-reposition cycle. Watch for:
- Whether the hook keeps collecting after the routine's tick loop passes over it repeatedly without re-clicking (confirms the "deploy once, never re-click" assumption, #3, was the right call — or reveals it needs re-clicking periodically after all).
- Whether it survives onto a newly-risen wreck in the same spot without any explicit action (#4), or whether `find_nearby_wreck`'s near-tier check needs to also trigger a fresh `deploy_hook` call when the specific wreck object changes underneath it.

- [ ] **Step 5: Soak test**

Only once steps 1-4 look right: let the routine run unattended for an extended session (start with 30-60 minutes), watching for `[!] Safety stop` messages, unexpected drops, or the boat getting stuck mid-reposition.

- [ ] **Step 6: Record findings**

Update `docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md`'s "Still unverified" section with what was actually confirmed (or fix code + re-test if something didn't hold up), then commit:

```bash
git add docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md
git commit -m "Record live-verification results for shipwreck salvaging routine"
```
