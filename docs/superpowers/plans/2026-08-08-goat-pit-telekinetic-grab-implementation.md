# Goat Pit Telekinetic Grab hunting implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `golem_crafting.py`'s wrong "plain-click lures/harvests goats" `hunt_phase` with the real mechanic: cast Telekinetic Grab on valid (non-north, in-range) goats until the pit is full, harvest it, drop the useless horns (and leftover spikes once the pouch is topped off), and skip the redundant bank trip once the fur pouch hits capacity.

**Architecture:** One new Java provider (`SpellbookProvider`, exposing Telekinetic Grab's widget position via the game's own generated `InterfaceID.MagicSpellbook.TELEGRAB` constant), one new Python spell-casting helper pair in `controller.py`, and a rewritten `hunt_phase` state machine in `golem_crafting.py` plus a small generalization of its existing exact-tile-walk helper.

**Tech Stack:** Python 3 (`ControllerAddon/`, no unit-test framework for routines -- see Global Constraints), Java 8 / RuneLite plugin API (Gradle), no new dependencies.

## Global Constraints

- **Source spec:** `docs/superpowers/specs/2026-08-08-goat-pit-telekinetic-grab-design.md` -- every task below implements one numbered section of it. Re-read it if a task's rationale is unclear; this plan does not repeat its "why" material.
- **No unit-test framework exists for routines in this codebase.** Verification is live, against the running bridge. Every Python task's "test" step is a syntax/import sanity check (`py_compile` + `pytest tests/`), NOT a behavioral test -- real behavioral verification is the dedicated live-verification task at the end of this plan (mirroring the spec's own Testing section). Do not invent a pytest suite for `golem_crafting.py` -- it doesn't have one today and inventing one here would be an unrequested, unprecedented pattern for this file.
- **Every Java change must compile** (`./gradlew.bat compileJava`) before being considered done -- this is this session's established verification bar for provider changes (no JUnit test exists for any provider in this codebase; they're all live-verified against the running bridge the same way).
- **`InterfaceID.MagicSpellbook.TELEGRAB` is confirmed to exist in `runelite-api-1.12.33.jar` but NOT yet confirmed to resolve to a real, visible widget once the Magic tab is actually open in this specific game** -- flag this with an inline `// ASSUMPTION, NOT YET VERIFIED LIVE` comment in `SpellbookProvider.java`, same convention `blast_furnace.py`/`banker.py` already use for unverified specifics. Do not remove this comment without a live confirmation.
- Deliberately **out of scope**: a general multi-spell lookup system (only Telekinetic Grab is needed -- YAGNI), rune restocking (out of runes is a safety-stop, not recoverable), choosing/switching spellbooks (assumes standard spellbook is already active).
- Match existing code style exactly: tabs in `.java` files, 4-space indents in `.py` files, Allman braces in Java, docstrings/comments in the same voice as the files you're extending (explain *why*, cite the spec section, flag assumptions).

---

## File Structure

- **Create `src/main/java/com/example/providers/SpellbookProvider.java`** -- new provider, `GET /spellbook` returns Telekinetic Grab's screen position (or `{"visible": false}` if the spellbook tab isn't open).
- **Modify `src/main/java/com/example/controller/ControllerPlugin.java`** -- register the new provider next to the other providers.
- **Modify `ControllerAddon/controller.py`** -- add `get_spellbook()` next to `get_tiles()`, and `open_spellbook_if_needed(ser)` / `cast_telekinetic_grab(ser, target) -> bool` next to the camera-nudge helpers.
- **Modify `ControllerAddon/routines/golem_crafting.py`** -- new constants, extend `on_event` for hunt-phase chat capture, add `is_valid_grab_target`/`goat_in_grab_range` geometry helpers, generalize `walk_to_golem_side` into `walk_to_exact_tile`, rewrite `hunt_phase`.

---

### Task 1: `SpellbookProvider.java`

**Files:**
- Create: `src/main/java/com/example/providers/SpellbookProvider.java`
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:118` (insert registration line)

**Interfaces:**
- Produces: `GET /spellbook` -> `{"visible": bool, "screenX": int, "screenY": int}` (screenX/screenY only present when visible), consumed by Task 3's `controller.get_spellbook()`.

- [ ] **Step 1: Write `SpellbookProvider.java`**

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

/**
 * Telekinetic Grab's widget position, for golem_crafting.py's Goat Pit hunt phase (see
 * docs/superpowers/specs/2026-08-08-goat-pit-telekinetic-grab-design.md). Scoped to this one
 * spell rather than a general spellbook lookup -- nothing else in this codebase casts spells,
 * and InterfaceID.MagicSpellbook.TELEGRAB already gives a direct, stable widget id, so there's
 * no name-matching/iteration step that would need to generalize across all spells anyway.
 */
public class SpellbookProvider extends BridgeProvider
{
	public SpellbookProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/spellbook", () -> onClient(this::getTelekineticGrab));
	}

	private Object getTelekineticGrab()
	{
		Map<String, Object> data = new HashMap<>();

		// ASSUMPTION, NOT YET VERIFIED LIVE -- InterfaceID.MagicSpellbook.TELEGRAB is confirmed
		// to exist in the RuneLite API jar (javap inspection), but not yet confirmed to resolve
		// to a real widget once the Magic tab is actually open in this game. Verify via this
		// endpoint before trusting hunt_phase's Grab-casting unattended.
		Widget grab = client.getWidget(InterfaceID.MagicSpellbook.TELEGRAB);
		if (grab == null || grab.isHidden())
		{
			data.put("visible", false);
			return data;
		}

		Rectangle bounds = grab.getBounds();
		if (bounds == null || bounds.width <= 0 || bounds.height <= 0)
		{
			data.put("visible", false);
			return data;
		}

		Point screen = RuneLiteUtil.canvasToScreen(client, bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
		data.put("visible", true);
		data.put("screenX", screen.x);
		data.put("screenY", screen.y);
		return data;
	}
}
```

- [ ] **Step 2: Register the provider**

In `src/main/java/com/example/controller/ControllerPlugin.java`, immediately after the `MenuStateProvider` registration line (`new MenuStateProvider(client, clientThread, server).register();`), add:

```java
		new SpellbookProvider(client, clientThread, server).register();
```

- [ ] **Step 3: Compile**

Run: `./gradlew.bat compileJava --console=plain` (from repo root)
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/example/providers/SpellbookProvider.java src/main/java/com/example/controller/ControllerPlugin.java
git commit -m "Add SpellbookProvider exposing Telekinetic Grab's widget position"
```

---

### Task 2: Live-verify the bridge endpoint

**Files:** none (verification only, no code changes)

**Interfaces:**
- Consumes: `GET /spellbook` from Task 1.

- [ ] **Step 1: Rebuild and reload the plugin, then open the Magic tab**

Ask the user to rebuild/reload the RuneLite plugin (per this session's established workflow) and manually open the standard spellbook tab in-game.

- [ ] **Step 2: Query the endpoint**

Run: `curl -s http://localhost:8080/spellbook`
Expected: `{"visible":true,"screenX":<int>,"screenY":<int>}` with a plausible on-screen coordinate (inside the game window, within the spellbook panel area). If `{"visible":false}` comes back while the Magic tab is visibly open with Telekinetic Grab showing, `InterfaceID.MagicSpellbook.TELEGRAB` doesn't resolve as expected in this game version -- stop and re-investigate (do not proceed to Task 3 on a guess).

- [ ] **Step 3: Close the tab and re-query**

Run: `curl -s http://localhost:8080/spellbook`
Expected: `{"visible":false}` -- confirms the endpoint correctly reports "not open" rather than a stale cached position.

No commit for this task (verification only).

---

### Task 3: `controller.py` spell-casting helpers

**Files:**
- Modify: `ControllerAddon/controller.py:179` (insert `get_spellbook` after `get_tiles`)
- Modify: `ControllerAddon/controller.py:917` (insert `open_spellbook_if_needed`/`cast_telekinetic_grab` near `nudge_camera_yaw`/`rotate_camera_away_from_ui`)

**Interfaces:**
- Consumes: `api_get(path, params=None, timeout=0.3)` (existing), `key_tap(ser, key)` (existing, `controller.py:520`), `click_entity(ser, entity, offset=(-7,7), ..., check_ui_blockers=True) -> bool` (existing, `controller.py:952`).
- Produces: `controller.get_spellbook() -> dict`, `controller.cast_telekinetic_grab(ser, target: dict) -> bool` -- consumed by Task 5's `hunt_phase`.

- [ ] **Step 1: Add `get_spellbook()`**

In `controller.py`, immediately after `get_tiles()` (currently `return api_get("/tiles") or []`, ending at line 179), insert:

```python
def get_spellbook():
    return api_get("/spellbook") or {"visible": False}
```

- [ ] **Step 2: Add the casting helpers**

Immediately after `nudge_camera_yaw` (ends with `time.sleep(random.uniform(0.1, 0.2))` around line 914), insert:

```python
def open_spellbook_if_needed(ser: serial.Serial) -> None:
    """Idempotent -- taps T (this game's Magic-tab keybind) only if Telekinetic Grab isn't
    already visible, so a hunt_phase call that finds the tab already open doesn't needlessly
    re-tap it every single cast."""
    if get_spellbook().get("visible"):
        return
    key_tap(ser, "T")
    time.sleep(random.uniform(0.3, 0.5))


def cast_telekinetic_grab(ser: serial.Serial, target: dict) -> bool:
    """Click Telekinetic Grab, then click the target -- same two-click 'use X on Y' shape as
    any item-on-entity interaction elsewhere in this codebase. Returns False if the spell
    isn't visible (spellbook not open/wrong spellbook active) or either click misses; True
    only means both clicks landed, not that the cast actually resolved -- the caller (hunt_
    phase) still has to watch chat for the real confirmation/rejection, same reasoning
    golem_crafting.py's craft_phase already documents for why a click landing isn't the same
    as the game having accepted it."""
    open_spellbook_if_needed(ser)
    spell = get_spellbook()
    if not spell.get("visible"):
        return False
    if not click_entity(ser, spell, offset=(0, 0), check_ui_blockers=False):
        return False
    time.sleep(random.uniform(0.15, 0.3))
    return click_entity(ser, target)
```

- [ ] **Step 3: Syntax check**

Run (from `ControllerAddon/`): `python -m py_compile controller.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Run the test suite**

Run (from `ControllerAddon/`): `python -m pytest tests/`
Expected: all existing tests still pass (this change doesn't touch anything `test_mcp_debug_server.py` exercises, but the project's own workflow runs this before every relaunch regardless).

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/controller.py
git commit -m "Add get_spellbook/cast_telekinetic_grab helpers to controller.py"
```

---

### Task 4: `golem_crafting.py` constants, chat capture, and geometry helpers

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py:705` (insert new constants after `GOLEM_CHAT_NOT_ENOUGH_FUR`)
- Modify: `ControllerAddon/routines/golem_crafting.py:708-727` (`on_event` -- extend, don't replace)
- Modify: `ControllerAddon/routines/golem_crafting.py:333` (insert geometry helpers after `find_golem_object`, before `find_mining_target`)

**Interfaces:**
- Consumes: `ctx.state.data` (existing per-routine dict), `GOAT_NAME`/`GOAT_PIT_NAME`/`FUR_PRIORITY` (existing, lines 71-76 and 179-180).
- Produces: `GRAB_RANGE_TILES: int`, `GOAT_CHAT_PIT_FULL: str`, `GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS: tuple`, `GOAT_HORN_NAME: str`, `FUR_POUCH_CAPACITY: int`, `is_valid_grab_target(pit: dict, goat: dict) -> bool`, `goat_in_grab_range(player: dict, goat: dict) -> bool` -- all consumed by Task 6's rewritten `hunt_phase`. Also produces the side effect: `ctx.state.data["gc_last_hunt_chat"]` / `ctx.state.data["gc_last_hunt_chat_seq"]`, populated by the extended `on_event`.

- [ ] **Step 1: Add the new constants**

Immediately after `GOLEM_CHAT_NOT_ENOUGH_FUR = "you need to dress the golem in furs from hunted creatures."` (line 705), insert:

```python

# CONFIRMED live (user session) -- Goat Pit hunting mechanic, replacing the original wrong
# plain-click-lures/harvests assumption. See docs/superpowers/specs/2026-08-08-goat-pit-
# telekinetic-grab-design.md for the full mechanic writeup.
GOAT_CHAT_PIT_FULL = "the pit is now filled with goats."
# Rune name varies (law/air), so this matches on the stable surrounding text rather than a
# single fixed string -- both substrings must be present.
GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS = ("you do not have enough", "to cast this spell")
GOAT_HORN_NAME = "Goat horn"  # always drops per trapped goat, never useful -- dropped on harvest
FUR_POUCH_CAPACITY = 28  # large fur pouch's capacity -- once hit, further fur spills into loose
                         # inventory instead of the pouch, which is exactly the signal used below
                         # to know the pouch is full without any dedicated capacity-check action
GRAB_RANGE_TILES = 10  # Telekinetic Grab's cast range -- a farther target makes the character
                       # auto-walk toward it, pulling the player off the fixed standing tile the
                       # whole north-side-only geometry below depends on
```

- [ ] **Step 2: Extend `on_event` for hunt-phase chat capture**

Replace the existing `on_event` function (lines 708-727) with:

```python
def on_event(ctx, event_type, payload) -> None:
    """Captures the golem-crafting confirmation/rejection chat messages into
    ctx.state.data["gc_last_chat"]/["gc_last_chat_seq"] as they arrive -- controller.py's main
    loop drains UDP events and calls this before every tick() (see controller.py's main()), so
    a message that arrives between two tick() calls is caught here and available to craft_phase
    on its next call. gc_last_chat_seq is a monotonic counter, not just the text, so craft_phase
    can tell "no new message since I started waiting" apart from "a new message arrived that
    happens to repeat old text" -- comparing the text alone couldn't distinguish those. Ignores
    every other chat message (this routine has no other use for chat) so unrelated game/spam
    messages can't overwrite a still-unconsumed golem-crafting one.

    ADDED 2026-08-08 (Goat Pit hunting redesign): also captures hunt_phase's own two chat
    signals (pit-full, rune-shortage) into a SEPARATE gc_last_hunt_chat/_seq pair, not the
    golem-crafting one above -- controller.py only ever calls one on_event per routine module
    (see its own dispatch, `hasattr(routine, "on_event")`), so both concerns have to live in
    this one function, but craft_phase and hunt_phase are mutually exclusive via gc_phase and
    keeping their chat state in separate keys avoids any future ambiguity if that ever changes."""
    if event_type != "CHAT_MESSAGE" or not payload:
        return
    message = (payload.get("message") or "").lower()

    if any(known in message for known in (
        GOLEM_CHAT_SIDE_DONE, GOLEM_CHAT_SIDE_ALREADY_DONE, GOLEM_CHAT_NOT_ENOUGH_SUNSTONE,
        GOLEM_CHAT_WRONG_INSERT_SIDE, GOLEM_CHAT_COMPLETE, GOLEM_CHAT_NOT_ENOUGH_FUR,
    )):
        ctx.state.data["gc_last_chat"] = message
        ctx.state.data["gc_last_chat_seq"] = ctx.state.data.get("gc_last_chat_seq", 0) + 1

    is_pit_full = GOAT_CHAT_PIT_FULL in message
    is_rune_short = all(part in message for part in GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS)
    if is_pit_full or is_rune_short:
        ctx.state.data["gc_last_hunt_chat"] = message
        ctx.state.data["gc_last_hunt_chat_seq"] = ctx.state.data.get("gc_last_hunt_chat_seq", 0) + 1
```

- [ ] **Step 3: Add the geometry helpers**

Immediately after `find_golem_object`'s closing (ends `return candidates[0]` around line 333, before `def find_mining_target`), insert:

```python
def is_valid_grab_target(pit: dict, goat: dict) -> bool:
    """South, east, or west of the pit only -- north is the player's own fixed standing side
    (CONFIRMED live, account owner), so a goat there can never be a valid Grab target no matter
    how close it is."""
    return goat.get("y", 0) <= pit.get("y", 0)


def goat_in_grab_range(player: dict, goat: dict) -> bool:
    """Telekinetic Grab's cast range is 10 tiles (CONFIRMED live, account owner) -- a target
    beyond that makes the character auto-walk toward it instead of just casting in place, which
    would pull the player off the fixed north tile hunt_phase depends on for is_valid_grab_
    target's own north-side exclusion to keep meaning anything."""
    dx = abs(goat.get("x", 0) - player.get("x", 0))
    dy = abs(goat.get("y", 0) - player.get("y", 0))
    return max(dx, dy) <= GRAB_RANGE_TILES
```

- [ ] **Step 4: Syntax check**

Run (from `ControllerAddon/`): `python -m py_compile routines/golem_crafting.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Run the test suite**

Run (from `ControllerAddon/`): `python -m pytest tests/`
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Add Goat Pit hunt constants, chat capture, and Grab geometry helpers"
```

---

### Task 5: Generalize `walk_to_golem_side` into `walk_to_exact_tile`

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py:736` (rename function + generalize docstring)
- Modify: `ControllerAddon/routines/golem_crafting.py:1044` (update call site)
- Modify: `ControllerAddon/routines/golem_crafting.py:1101` (update call site)
- Modify: `ControllerAddon/routines/golem_crafting.py:969,980` (update two comment references, text only)

**Interfaces:**
- Produces: `walk_to_exact_tile(ctx, x: int, y: int, plane: int) -> bool` (same signature and behavior `walk_to_golem_side` already has -- pure rename plus a generalized docstring, no logic change) -- consumed by both `craft_phase` (existing call sites, updated in place) and Task 6's `hunt_phase` (new call site).

- [ ] **Step 1: Rename the function and generalize its docstring**

Replace:

```python
def walk_to_golem_side(ctx, x: int, y: int, plane: int) -> bool:
    """Walk to the exact world tile (x, y) -- one of the golem's 4 adjacent sides -- and wait
    until standing exactly on it. Deliberately NOT travel.py's walk_to/_hop_to: those consider
    landing within WALK_ARRIVAL_TOLERANCE (3 tiles) of the target "arrived", which is useless
    here -- the golem's 4 adjacent sides are only 1-2 tiles apart from each other, so a
    3-tile-tolerant arrival could leave the player on the WRONG side while still reporting
    success. Returns False only if the tile can't be projected onto the screen at all (out of
    render range -- shouldn't happen for a tile adjacent to an already-visible golem); a
    timed-out wait still returns True, since the caller re-checks the golem/player position
    fresh afterward either way."""
```

with:

```python
def walk_to_exact_tile(ctx, x: int, y: int, plane: int) -> bool:
    """Walk to the exact world tile (x, y) and wait until standing exactly on it. Deliberately
    NOT travel.py's walk_to/_hop_to: those consider landing within WALK_ARRIVAL_TOLERANCE
    (3 tiles) of the target "arrived", which is useless for either of this function's two
    callers -- craft_phase's golem sides are only 1-2 tiles apart from each other (a 3-tile-
    tolerant arrival could leave the player on the WRONG side while still reporting success),
    and hunt_phase's fixed north-of-the-pit standing tile needs to be exact for its own
    north-side-exclusion geometry (is_valid_grab_target) to mean anything. Returns False only
    if the tile can't be projected onto the screen at all (out of render range -- shouldn't
    happen for a tile adjacent to an already-visible golem/pit); a timed-out wait still returns
    True, since callers re-check position fresh afterward either way.

    RENAMED 2026-08-08 from walk_to_golem_side -- originally craft_phase-specific, generalized
    (no logic change) once hunt_phase needed the exact same exact-arrival behavior for its own
    fixed standing tile rather than duplicating this polling loop."""
```

- [ ] **Step 2: Update the two call sites**

At line 1044 and line 1101 (both currently `if not walk_to_golem_side(ctx, golem["x"] + offset[0], golem["y"] + offset[1], golem.get("plane", 0)):`), rename the call to `walk_to_exact_tile`, keeping arguments identical:

```python
        if not walk_to_exact_tile(ctx, golem["x"] + offset[0], golem["y"] + offset[1], golem.get("plane", 0)):
```

- [ ] **Step 3: Update the two comment references**

At line 969 (`# walk_to_golem_side call below to GOLEM_ADJACENT_OFFSETS[0] specifically, since`) and line 980 (`# shaping/inserting (which always walk_to_golem_side first, landing adjacent and`), replace `walk_to_golem_side` with `walk_to_exact_tile` in both comments (text only, no behavior change).

- [ ] **Step 4: Syntax check**

Run (from `ControllerAddon/`): `python -m py_compile routines/golem_crafting.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Confirm no leftover references to the old name**

Run (from repo root): `grep -rn "walk_to_golem_side" ControllerAddon/`
Expected: no matches.

- [ ] **Step 6: Run the test suite**

Run (from `ControllerAddon/`): `python -m pytest tests/`
Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Generalize walk_to_golem_side into walk_to_exact_tile for hunt_phase reuse"
```

---

### Task 6: Rewrite `hunt_phase`

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py:1417-1528` (replace the entire function body)

**Interfaces:**
- Consumes: `_ensure_wooden_spikes(ctx, config) -> bool` (existing, line 1370), `find_object(name) -> dict|None` (existing, line 281), `handle_missing_target(ctx, config, location_key, miss_counter_key, walk_counter_key) -> bool` (existing, line 421), `walk_to_exact_tile(ctx, x, y, plane) -> bool` (Task 5), `is_valid_grab_target`/`goat_in_grab_range` (Task 4), `controller.cast_telekinetic_grab(ser, target) -> bool` (Task 3), `controller.get_player()`/`get_npcs()`/`get_inventory()`/`click_entity()` (all existing), `GOAT_NAME`/`GOAT_PIT_NAME`/`WOODEN_SPIKES_NAME`/`FUR_PRIORITY`/`GOAT_NO_GOAT_CLEAR_STREAK` (all existing), `GOAT_CHAT_PIT_FULL`/`GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS`/`GOAT_HORN_NAME`/`FUR_POUCH_CAPACITY` (Task 4).
- Produces: no new public interface -- `hunt_phase(ctx, config) -> None` keeps its existing signature (called from `PHASES["hunt"]`, already wired).

- [ ] **Step 1: Replace `hunt_phase`**

Replace the entire existing function (from `def hunt_phase(ctx, config: dict) -> None:` at line 1417 through its final `time.sleep(random.uniform(0.6, 1.0))` at line 1528) with:

```python
def hunt_phase(ctx, config: dict) -> None:
    """Entered only when bank_phase finds no usable fur at all. Traps Wyrmscraig goats by
    casting Telekinetic Grab on them (not by clicking them directly -- see this function's own
    history below for the wrong assumption this replaces), harvests the pit once full, and
    repeats (relining every single cycle) until either no valid goats remain nearby or the fur
    pouch hits capacity. One bounded action per call, same shape as mine_phase/chisel_phase.

    CORRECTED 2026-08-08 (user session, live-verified): replaces this function's entire
    original model. The original assumed a plain left-click on a wild goat resolves to "Lure"
    (and the same click on a trapped goat resolves to "Harvest") -- CONFIRMED WRONG live. The
    real mechanic (see docs/superpowers/specs/2026-08-08-goat-pit-telekinetic-grab-design.md
    for the full writeup):
      1. Line the pit with wooden spikes (unchanged mechanically, now happens after walking to
         the fixed standing tile rather than before, so lining always happens from the same
         spot the whole cycle depends on).
      2. Stand on the tile 2 tiles north of the pit -- the player's own fixed side. Only goats
         to the south, east, or west (is_valid_grab_target) within 10 tiles
         (goat_in_grab_range) are ever valid Grab targets; a farther target would make the
         character auto-walk toward it, abandoning the fixed tile.
      3. Cast Telekinetic Grab (controller.cast_telekinetic_grab) on the nearest valid goat,
         repeatedly, until the chat message "The pit is now filled with goats." arrives
         (captured by on_event into gc_last_hunt_chat/_seq, same chat-driven-confirmation
         idiom craft_phase already uses for golem-crafting messages).
      4. Click the pit to harvest (not individual goats) -- always yields a Goat horn (useless,
         dropped) and a 1/4-per-trapped-goat chance of Wyrmscraig goat fur (routed into the
         already-open fur pouch, same as before).
      5. Relines every single cycle (not once per hunt visit) before repeating from step 3.
      6. If the fur pouch is at capacity (28), any further fur spills into loose inventory
         instead of the pouch -- CONFIRMED live this is the signal used to know it's full,
         rather than any dedicated capacity-check action. Once seen, drops any leftover wooden
         spikes (no longer needed this trip) and skips bank_phase entirely, going straight to
         mine_phase (fully re-stocked already -- nothing to deposit or withdraw for fur).

    Rune handling: no pre-check of the rune pouch's contents (it isn't inventory-visible the
    way find_item can see loose runes -- see the spec's Research method section for why).
    Instead, casting is attempted and the "you do not have enough ... to cast this spell"
    rejection (GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS) safety-stops immediately, same pattern as the
    fur/sunstone shortage handling in craft_phase.

    Still bounded by MAX_HUNT_ACTIONS/gc_hunt_actions, same safety net the original click-based
    version had -- a Grab cast that never actually resolves is the same "clicks forever with no
    progress" risk class a bad click resolution always was, so the cap carries over unchanged.
    """
    if not _ensure_wooden_spikes(ctx, config):
        return

    pit = find_object(GOAT_PIT_NAME)
    if pit is None:
        if handle_missing_target(ctx, config, "goatPit", "gc_pit_misses", "gc_pit_walks"):
            return
        ctx.state.trigger_safety_stop("Goat Pit not found nearby")
        return
    ctx.state.data["gc_pit_misses"] = 0
    ctx.state.data["gc_pit_walks"] = 0

    if not ctx.state.data.get("gc_hunt_standing"):
        target = {"x": pit["x"], "y": pit["y"] + 2, "plane": pit.get("plane", 0)}
        if not walk_to_exact_tile(ctx, target["x"], target["y"], target["plane"]):
            debug_log.log("golem crafting", "couldn't walk to the hunt standing tile -- retrying next tick")
            return
        ctx.state.data["gc_hunt_standing"] = True

    if not ctx.state.data.get("gc_pit_lined"):
        spikes = find_item(WOODEN_SPIKES_NAME)
        debug_log.log("golem crafting", "lining the Goat Pit with wooden spikes")
        spikes_clicked = controller.click_entity(ctx.ser, spikes, check_ui_blockers=False)
        time.sleep(random.uniform(0.15, 0.3))
        pit_clicked = controller.click_entity(ctx.ser, pit)
        time.sleep(random.uniform(0.6, 1.0))
        if not (spikes_clicked and pit_clicked):
            debug_log.log("golem crafting", "lining click missed/blocked -- retrying next tick")
            return
        ctx.state.data["gc_pit_lined"] = True
        ctx.state.data["gc_pit_filled"] = False

        if banker.has_fur_pouch() and not ctx.state.data.get("gc_pouch_opened_this_hunt"):
            pouch = find_item(banker.FUR_POUCH_NAME.title()) or next(
                (i for i in controller.get_inventory() if banker.FUR_POUCH_NAME in (i.get("name") or "").lower()),
                None,
            )
            if pouch is not None:
                debug_log.log("golem crafting", "opening fur pouch so catches route straight in")
                controller.click_entity(ctx.ser, pouch, check_ui_blockers=False)
                time.sleep(random.uniform(0.3, 0.5))
            ctx.state.data["gc_pouch_opened_this_hunt"] = True
        return

    if not ctx.state.data.get("gc_pit_filled"):
        player = controller.get_player()
        goats = [
            n for n in controller.get_npcs()
            if GOAT_NAME in (n.get("name") or "").lower()
            and n.get("visible", True)
            and is_valid_grab_target(pit, n)
            and goat_in_grab_range(player, n)
        ]
        if not goats:
            streak = ctx.state.data.get("gc_hunt_no_goat_streak", 0) + 1
            ctx.state.data["gc_hunt_no_goat_streak"] = streak
            if streak < GOAT_NO_GOAT_CLEAR_STREAK:
                debug_log.log("golem crafting", f"no valid goats visible ({streak}/{GOAT_NO_GOAT_CLEAR_STREAK}) -- confirming the area is actually cleared")
                return
            debug_log.log("golem crafting", "no valid goats left nearby -- moving to bank phase")
            ctx.state.data["gc_phase"] = "bank"
            ctx.state.data["gc_pit_lined"] = False
            ctx.state.data["gc_pit_filled"] = False
            ctx.state.data["gc_hunt_standing"] = False
            ctx.state.data["gc_pouch_opened_this_hunt"] = False
            ctx.state.data["gc_hunt_no_goat_streak"] = 0
            ctx.state.data["gc_hunt_actions"] = 0
            return
        ctx.state.data["gc_hunt_no_goat_streak"] = 0

        # Same safety net the original click-based version had (MAX_HUNT_ACTIONS/gc_hunt_actions
        # existed to guard "a click that never actually resolves clicks forever with no
        # progress") -- a Grab cast that never actually lands (spellbook not opening, wrong
        # widget, etc.) is the exact same risk class for this mechanic, so the cap carries over
        # unchanged rather than being dropped along with the click-based approach it originally
        # guarded.
        actions = ctx.state.data.get("gc_hunt_actions", 0) + 1
        ctx.state.data["gc_hunt_actions"] = actions
        if actions > MAX_HUNT_ACTIONS:
            ctx.state.trigger_safety_stop(
                f"hunt phase exceeded {MAX_HUNT_ACTIONS} Grab-cast attempts without the pit "
                "filling -- Telekinetic Grab may not be resolving as expected (see hunt_phase's "
                "docstring)"
            )
            return

        goats.sort(key=lambda n: n.get("distance", 9999))
        target = goats[0]
        debug_log.log("golem crafting", f"casting Telekinetic Grab on {target.get('name')} (dist={target.get('distance')})")
        controller.cast_telekinetic_grab(ctx.ser, target)
        time.sleep(random.uniform(0.6, 1.0))

        seq = ctx.state.data.get("gc_last_hunt_chat_seq", 0)
        if seq > ctx.state.data.get("gc_hunt_chat_seen_seq", 0):
            ctx.state.data["gc_hunt_chat_seen_seq"] = seq
            message = ctx.state.data.get("gc_last_hunt_chat", "")
            if all(part in message for part in GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS):
                ctx.state.trigger_safety_stop("out of law/air runes for Telekinetic Grab")
                return
            if GOAT_CHAT_PIT_FULL in message:
                debug_log.log("golem crafting", "pit is now filled with goats -- harvesting")
                ctx.state.data["gc_pit_filled"] = True
                ctx.state.data["gc_hunt_actions"] = 0
        return

    # Pit is full -- harvest.
    pit = find_object(GOAT_PIT_NAME)  # fresh lookup, same idiom as craft_phase's re-checks
    if pit is None:
        debug_log.log("golem crafting", "pit no longer found before harvesting -- retrying next tick")
        return
    controller.click_entity(ctx.ser, pit)
    time.sleep(random.uniform(0.6, 1.0))

    for horn in [i for i in controller.get_inventory()
                 if GOAT_HORN_NAME.lower() in (i.get("name") or "").lower()]:
        controller.click_entity(ctx.ser, horn, check_ui_blockers=False)  # "Drop" (confirmed
                                                                          # live Forced Action)
        time.sleep(random.uniform(0.2, 0.4))

    spilled_fur = any(
        (i.get("name") or "").lower() in {f.lower() for f in FUR_PRIORITY}
        for i in controller.get_inventory()
    )
    ctx.state.data["gc_pit_lined"] = False
    ctx.state.data["gc_pit_filled"] = False
    if spilled_fur:
        debug_log.log("golem crafting", "fur pouch full -- skipping bank, heading to mine phase")
        for spikes in [i for i in controller.get_inventory()
                       if WOODEN_SPIKES_NAME.lower() in (i.get("name") or "").lower()]:
            controller.click_entity(ctx.ser, spikes, check_ui_blockers=False)  # "Drop"
            time.sleep(random.uniform(0.2, 0.4))
        ctx.state.data["gc_fur_stock"] = FUR_POUCH_CAPACITY
        ctx.state.data["gc_hunt_standing"] = False
        ctx.state.data["gc_pouch_opened_this_hunt"] = False
        ctx.state.data["gc_hunt_actions"] = 0
        ctx.state.data["gc_phase"] = "mine"
```

- [ ] **Step 2: Syntax check**

Run (from `ControllerAddon/`): `python -m py_compile routines/golem_crafting.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Run the test suite**

Run (from `ControllerAddon/`): `python -m pytest tests/`
Expected: all existing tests still pass.

- [ ] **Step 4: Confirm no leftover references to the old assumption**

Run (from repo root): `grep -n "Lure\|lure/harvest\|ASSUMPTION, NOT YET VERIFIED LIVE" ControllerAddon/routines/golem_crafting.py`
Expected: no remaining references to the old plain-click Lure/Harvest assumption in `hunt_phase`'s vicinity (the module's other, unrelated ASSUMPTION comments elsewhere are fine and untouched). `MAX_HUNT_ACTIONS`/`GOAT_NO_GOAT_CLEAR_STREAK` constants (lines 1408/1414, just above the old function) are still used by the new version -- confirm they weren't accidentally deleted.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Rewrite hunt_phase to use Telekinetic Grab instead of plain-click lure/harvest"
```

---

### Task 7: Live verification

**Files:** none (verification only, no code changes)

Per the spec's own Testing section and this codebase's established live-debugging workflow (`CLAUDE.md`): launch the routine (`python -u controller.py routines/golem_crafting.py`) with the account already positioned near the Goat Pit with an empty/low fur pouch, and confirm, one at a time:

- [ ] **Step 1:** Wooden spikes are taken from the Spikes supply if not already carried.
- [ ] **Step 2:** The character walks to and stops exactly 2 tiles north of the pit (not just "close").
- [ ] **Step 3:** The pit gets lined (spikes + pit click) only after arriving at that tile.
- [ ] **Step 4:** The fur pouch opens (if owned) once, not repeatedly.
- [ ] **Step 5:** Telekinetic Grab is cast only on goats south/east/west of the pit and within 10 tiles -- confirm a goat directly north, or one beyond 10 tiles, is never targeted.
- [ ] **Step 6:** The character does NOT run off toward a goat mid-cast (confirms the range filter is working).
- [ ] **Step 7:** Once "The pit is now filled with goats." appears in chat, casting stops and the pit is clicked to harvest.
- [ ] **Step 8:** Goat horns are dropped after harvesting; a genuine fur drop (1/4 chance per goat) lands in the pouch, not loose inventory.
- [ ] **Step 9:** The pit is relined and the cycle repeats for at least 2 full fill/harvest cycles without manual intervention.
- [ ] **Step 10:** Once the fur pouch is full (confirm by watching for a fur item to spill into loose inventory), leftover wooden spikes are dropped and the routine transitions straight to `mine_phase` (not `bank_phase`).
- [ ] **Step 11:** If reachable to test safely, confirm the rune-shortage safety-stop message and wording once law/air runes actually run out.

No commit for this task. If any step fails, return to the relevant earlier task, fix, re-verify from Step 1 of this task (not just the failed step), and only then continue.
