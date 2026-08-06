# Right-Click Primitive + Menu-State Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the bot the ability to right-click and read back what a currently-open context menu contains, so a specific menu option (e.g. "Mark Safespot") can be triggered and verified end-to-end without a human or screen vision.

**Architecture:** A `button` parameter on the existing `click_entity` (Python) reuses all its aim/UI-blocker logic and only swaps the final click call; a new `MenuStateProvider.java` (Java) exposes `client.isMenuOpen()`/`getMenuEntries()` live over `GET /menu`, following the exact pattern every other bridge provider already uses.

**Tech Stack:** Python (`controller.py`), Java (RuneLite plugin API: `Client.isMenuOpen()`, `Client.getMenu()`, `Menu`/`MenuEntry`), Gradle.

## Global Constraints

- The Arduino firmware (`data/Firmware.c`) needs zero changes -- `"MRD"`/`"MRU"`/`"MRC"` are already parsed by `handleMouseButton()` identically to the existing `"MLD"`/`"MLU"`/`"MLC"`. Do not touch `data/Firmware.c` in this plan.
- `click_entity` gains a `button: str = "left"` parameter -- no parallel `right_click_entity` function, no duplicated aim/UI-blocker logic. Every existing call site is unaffected by the new default.
- `MenuStateProvider` reads live per-poll (`client.isMenuOpen()` + `client.getMenuEntries()`), not a cached subscription off `MenuOpened` -- `MenuOpened` fires on every hover, not just a real right-click, so caching it would expose stale/irrelevant menu contents most of the time.
- `option`/`target` text in the `/menu` response must be cleaned with `Text.removeTags(...)`, matching `MenuPriority`'s existing convention, so callers can match against plain strings.
- No submenu support, no changes to `MenuPriority`'s existing behavior, no visual/screenshot verification -- all explicitly out of scope per the spec.

---

### Task 1: Right-click primitive in controller.py

**Files:**
- Modify: `ControllerAddon/controller.py`

**Interfaces:**
- Consumes: nothing new (uses the existing `send_line`, `debug_log.log`, `get_cursor_pos`, `move_to_human`, `is_point_within_window`, `is_point_blocked_by_ui`, `rotate_camera_away_from_ui`, `_movement_abort` already in this file).
- Produces: `mouse_right_down(ser)`, `mouse_right_up(ser)`, `mouse_right_click(ser)`, `right_click_human(ser)` -- new module-level functions. `click_entity(ser, entity, offset=(-7, 7), refresh_target=None, check_ui_blockers=True, simulate_mistakes=True, button="left")` -- widened signature (new `button` parameter, default preserves current behavior for every existing call site).

- [ ] **Step 1: Add the right-button send functions**

In `ControllerAddon/controller.py`, find:

```python
def mouse_left_click(ser: serial.Serial) -> None:
    send_line(ser, "MLC")


def click_human(ser: serial.Serial) -> None:
    debug_log.log("action", f"click_human at {get_cursor_pos()}")
    mouse_left_down(ser)
    time.sleep(random.uniform(0.045, 0.11))
    mouse_left_up(ser)
    time.sleep(random.uniform(0.03, 0.09))
```

Replace with:

```python
def mouse_left_click(ser: serial.Serial) -> None:
    send_line(ser, "MLC")


def click_human(ser: serial.Serial) -> None:
    debug_log.log("action", f"click_human at {get_cursor_pos()}")
    mouse_left_down(ser)
    time.sleep(random.uniform(0.045, 0.11))
    mouse_left_up(ser)
    time.sleep(random.uniform(0.03, 0.09))


def mouse_right_down(ser: serial.Serial) -> None:
    send_line(ser, "MRD")


def mouse_right_up(ser: serial.Serial) -> None:
    send_line(ser, "MRU")


def mouse_right_click(ser: serial.Serial) -> None:
    send_line(ser, "MRC")


def right_click_human(ser: serial.Serial) -> None:
    debug_log.log("action", f"right_click_human at {get_cursor_pos()}")
    mouse_right_down(ser)
    time.sleep(random.uniform(0.045, 0.11))
    mouse_right_up(ser)
    time.sleep(random.uniform(0.03, 0.09))
```

- [ ] **Step 2: Widen click_entity with a button parameter**

In the same file, find:

```python
def click_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None,
                  check_ui_blockers=True, simulate_mistakes=True) -> bool:
```

Replace with:

```python
def click_entity(ser: serial.Serial, entity: dict, offset=(-7, 7), refresh_target=None,
                  check_ui_blockers=True, simulate_mistakes=True, button: str = "left") -> bool:
```

Then find the end of the same function:

```python
    debug_log.log("action", f"click_entity target={entity.get('name')}(id={entity.get('id')}) at ({aim_x},{aim_y})")
    move_to_human(
        ser,
        aim_x,
        aim_y,
        aim_radius=ENTITY_AIM_RADIUS,
        refresh_target=refresh_target,
    )
    if _movement_abort.is_set():
        return False
    click_human(ser)
    return True
```

Replace with:

```python
    debug_log.log("action", f"click_entity target={entity.get('name')}(id={entity.get('id')}) at ({aim_x},{aim_y})")
    move_to_human(
        ser,
        aim_x,
        aim_y,
        aim_radius=ENTITY_AIM_RADIUS,
        refresh_target=refresh_target,
    )
    if _movement_abort.is_set():
        return False
    if button == "right":
        right_click_human(ser)
    else:
        click_human(ser)
    return True
```

- [ ] **Step 3: Verify it compiles**

Run: `python -m py_compile ControllerAddon/controller.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/controller.py
git commit -m "$(cat <<'EOF'
Add right-click primitive (mouse_right_*, click_entity button param)

The Arduino firmware already parses MRD/MRU/MRC identically to
MLD/MLU/MLC -- this was purely a missing Python-side sender. click_entity
gets a button parameter instead of a parallel function so it keeps
reusing the same aim/UI-blocker logic; every existing call site is
unaffected by the new "left" default.
EOF
)"
```

---

### Task 2: Menu-state bridge endpoint

**Files:**
- Create: `src/main/java/com/example/providers/MenuStateProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java`

**Interfaces:**
- Consumes: `com.example.providers.BridgeProvider` (existing base class -- constructor `(Client, ClientThread, BridgeHttpServer)`, abstract `register()`, `onClient(SupplierLike)` helper). `BridgeHttpServer.register(String path, Supplier<Object> provider)` (existing method every provider already calls in its own `register()`). `net.runelite.api.Client.isMenuOpen()`, `Client.getMenu()`, `Menu.getMenuEntries()`/`getMenuX()`/`getMenuY()`/`getMenuWidth()`/`getMenuHeight()`, `MenuEntry.getOption()`/`getTarget()` (all confirmed present on the RuneLite API via `javap`). `net.runelite.client.util.Text.removeTags(String)` (already used the same way in `MenuPriority.java`).
- Produces: `GET /menu` -- `{"open": false}` or `{"open": true, "x": int, "y": int, "width": int, "height": int, "entries": [{"index": int, "option": str, "target": str, "screenX": int, "screenY": int}, ...]}`. Task 3 (live verification) consumes this exact shape and may need to correct the row-height constant this task introduces.

- [ ] **Step 1: Create MenuStateProvider.java**

Create `src/main/java/com/example/providers/MenuStateProvider.java`:

```java
package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Menu;
import net.runelite.api.MenuEntry;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.util.Text;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Exposes the currently-open right-click context menu, if any, so the bot can read back what a
 * menu actually contains after right-clicking (see controller.py's right_click_human/
 * click_entity(button="right")) instead of needing a human or screen vision to confirm it.
 *
 * Reads live on every poll (client.isMenuOpen() + client.getMenu()), the same on-demand style
 * NPCProvider/GroundItemProvider already use for their own state -- deliberately NOT a cached
 * MenuOpened subscription, since MenuOpened fires on every hover during normal play, not just an
 * actual right-click (see MenuPriority's own docs for this same fact); caching off it would
 * expose stale/irrelevant menu contents most of the time. isMenuOpen() answers "is a menu
 * genuinely displayed on screen right now" directly.
 */
public class MenuStateProvider extends BridgeProvider
{
	// NOT verified against a live client -- Menu only exposes the popup's overall bounding box
	// (getMenuX/Y/Width/Height), not per-row positions (RuneLite's context menu is a
	// custom-rendered overlay, not built from widgets with their own queryable bounds), so
	// per-entry click coordinates have to be computed from an assumed row height instead of read
	// directly. CONFIRM/CORRECT this against a real right-click menu (see this plan's Task 3)
	// before relying on it to click a specific entry -- also confirm whether entry index 0
	// renders at the TOP of the popup (this code's assumption) or the bottom.
	private static final int MENU_ROW_HEIGHT_PX = 15;

	public MenuStateProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/menu", () -> onClient(this::getMenuState));
	}

	private Object getMenuState()
	{
		Map<String, Object> data = new HashMap<>();

		if (!client.isMenuOpen())
		{
			data.put("open", false);
			return data;
		}

		Menu menu = client.getMenu();
		MenuEntry[] entries = menu.getMenuEntries();
		int menuX = menu.getMenuX();
		int menuY = menu.getMenuY();

		data.put("open", true);
		data.put("x", menuX);
		data.put("y", menuY);
		data.put("width", menu.getMenuWidth());
		data.put("height", menu.getMenuHeight());

		List<Map<String, Object>> entryList = new ArrayList<>();
		for (int i = 0; i < entries.length; i++)
		{
			MenuEntry entry = entries[i];
			Map<String, Object> entryData = new HashMap<>();
			entryData.put("index", i);
			entryData.put("option", Text.removeTags(entry.getOption()));
			entryData.put("target", Text.removeTags(entry.getTarget()));
			entryData.put("screenX", menuX + menu.getMenuWidth() / 2);
			entryData.put("screenY", menuY + (i * MENU_ROW_HEIGHT_PX) + (MENU_ROW_HEIGHT_PX / 2));
			entryList.add(entryData);
		}
		data.put("entries", entryList);

		return data;
	}
}
```

- [ ] **Step 2: Register it in ControllerPlugin**

In `src/main/java/com/example/controller/ControllerPlugin.java`, find:

```java
		new ConfigProvider(client, clientThread, server, config).register();
		new ConsumablesProvider(client, clientThread, server, config).register();
		new LoginProvider(client, clientThread, server).register();
```

Replace with:

```java
		new ConfigProvider(client, clientThread, server, config).register();
		new ConsumablesProvider(client, clientThread, server, config).register();
		new LoginProvider(client, clientThread, server).register();
		new MenuStateProvider(client, clientThread, server).register();
```

- [ ] **Step 3: Compile and verify**

Run: `./gradlew compileJava`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/providers/MenuStateProvider.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "$(cat <<'EOF'
Add GET /menu bridge endpoint exposing the live right-click menu state

Reads client.isMenuOpen()/getMenu() live on each poll, same on-demand
style every other provider uses -- not a MenuOpened-cached mechanism,
since that event fires on every hover, not just a real right-click.
Per-entry screenX/screenY are computed from an unverified row-height
constant, flagged for live confirmation.
EOF
)"
```

---

### Task 3: Live verification -- close the autonomous loop end-to-end

**Files:** none (verification only, no changes -- though Step 3 may loop back into Task 2's `MENU_ROW_HEIGHT_PX` constant if it's wrong; if that happens, treat it as amending Task 2's commit with a follow-up fix commit, not a new task)

**Interfaces:**
- Consumes: `right_click_human`/`click_entity(..., button="right")` from Task 1, `GET /menu` from Task 2.
- Produces: nothing (last task) -- but this is the actual point of the whole plan: proof the agent can trigger and verify a menu-driven action on its own.

This task is meant to be run by the agent directly (not delegated to a subagent, and not requiring the human to click anything) -- it's the capability this plan exists to build, so exercising it here is the real test.

- [ ] **Step 1: Confirm the dev client is running the new code**

Check the running RuneLite dev client's process start time is after Task 1/Task 2's commits (e.g. via `Get-CimInstance Win32_Process -Filter "Name = 'java.exe'"` on Windows, comparing `CreationDate` against `git log -1 --format=%cI` for Task 2's commit). If it predates the commits, stop and tell the human partner the client needs restarting before this task can proceed -- do not attempt to verify against stale code.

- [ ] **Step 2: Right-click the ground and poll /menu**

From `ControllerAddon/`, in a Python shell or small script using the existing `controller` module:

```python
import time
import controller

state = controller.get_state()
# Pick a point known to be on screen -- the canvas center is always safe.
x = state.get("canvasScreenX", 0) + state.get("canvasWidth", 0) // 2
y = state.get("canvasScreenY", 0) + state.get("canvasHeight", 0) // 2

with controller.serial.Serial(controller.SERIAL_PORT, controller.SERIAL_BAUD, timeout=1) as ser:
    controller.move_to_human(ser, x, y, aim_radius=2)
    controller.right_click_human(ser)
    time.sleep(0.3)
    menu = controller.api_get("/menu")
    print(menu)
```

Expected: `menu["open"]` is `True`, and `menu["entries"]` contains at least `"Walk here"` (and `"Mark Safespot"`, from the earlier safespot work, if the plugin is loaded).

- [ ] **Step 3: Verify the computed screenX/screenY actually land on the right row**

Find the entry whose `option` is `"Mark Safespot"`. Left-click at its reported `screenX`/`screenY` via `controller.click_human` (after `controller.move_to_human` to that point), then poll `GET /slayer-config` (or re-check `GET /menu` -- it should now report `open: false` since selecting an entry closes the menu) and confirm the click actually landed on that row and not an adjacent one -- e.g. re-open the menu immediately after and confirm the safespot's `x`/`y`/`plane` in the config changed to match the player's current position (from `GET /player`), the same way Task 3 of the safespot plan intended to verify but couldn't do without a human.

If the click lands on the wrong row (confirmed by the config either not updating, or a visibly different menu action having fired), adjust `MENU_ROW_HEIGHT_PX` in `MenuStateProvider.java` and/or the index-to-row direction (`menuY + (i * MENU_ROW_HEIGHT_PX)` vs. counting from the bottom instead), re-run `./gradlew compileJava`, and repeat this step. This is expected iteration, not a plan failure -- the constant was explicitly flagged as unverified in Task 2.

- [ ] **Step 4: Record the outcome**

Once a click at a computed `screenX`/`screenY` reliably lands on the intended entry across at least two different menus (e.g. "Mark Safespot" on the ground, and any other multi-entry menu, like right-clicking an NPC), consider the loop closed. Report back to the human partner: the final confirmed `MENU_ROW_HEIGHT_PX` value and index direction, and that `GET /menu` -> pick an entry -> click it -> verify via existing state endpoints now works without any human involvement.
