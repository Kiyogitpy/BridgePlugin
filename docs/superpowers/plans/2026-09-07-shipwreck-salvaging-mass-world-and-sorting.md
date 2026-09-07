# Shipwreck Salvaging: Mass-World Mode, Cargo-Hold Sorting and a Keep-List — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On a mass world, stop sailing between wrecks; stow salvage in the boat's cargo hold, sort it at the on-boat salvaging station during respawn gaps and when the hold fills, and keep only whitelisted items.

**Architecture:** `routines/shipwreck_salvaging.py` gains a pure `derive_phase` state machine (`sort` / `stow` / `hook` / `wait`) driven entirely from live reads. A new `ControllerAddon/cargo_hold.py` drives interface 943. `sailing.py` gains `find_facility`, which filters `/objects` by a new `boatViewId` field so we click **our own** boat's fixtures rather than a neighbour's. Everything degrades to today's behaviour when the field is absent.

**Tech Stack:** Python 3.13 (`ControllerAddon/`), pytest; Java 11 RuneLite plugin (`src/main/java/com/example/providers/`), Gradle.

**Spec:** `docs/superpowers/specs/2026-09-07-shipwreck-salvaging-mass-world-and-sorting-design.md`

## Global Constraints

- **Sub-project 1 only.** Crew swapping (sidepanel 937 + dialogue 219) is explicitly **out of scope**; do not implement it.
- **No alching.** The keep-list decides keep-or-drop and nothing else.
- **Never edit anything under `src/main/java/net/runelite/client/plugins/m8aq/`.**
- **Every bridge read in a test must be stubbed.** `tests/test_shipwreck_salvaging.py` has an autouse `_no_live_bridge` fixture; new stubs go there. A test that reaches the running client is a plan failure.
- **Inventory-item and interface clicks pass `check_ui_blockers=False` and `simulate_mistakes=False`.** An interface element is always "behind a UI panel" by definition, and a simulated near-miss on a grid drops or deposits the neighbouring slot.
- **Degrade gracefully, never fail closed.** An unreadable bridge value means "cannot tell" and must preserve existing behaviour — never "no" (this exact bug cost a live session via `_boosted_sailing_level` returning 0).
- Measured constants that must be used verbatim: `SORT_SECONDS_PER_SALVAGE = 1.79`, station locs `59699 / 59700 / 59701`, sort animations `13599 / 13593`, hold interface `943` (children 1/3/4/5/10/13/14/15/20-24) and side panel `944`.
- Commit after every task. Do not push.

---

### Task 1: Stamp `boatViewId` on objects and the player (Java)

Without this, `/objects` cannot tell our boat's salvaging station from the 25 others belonging to neighbouring boats stacked on the same water — three of which sit on the player's exact tile, all `clickable: true`. Every later task's clicks depend on it.

**Files:**
- Modify: `src/main/java/com/example/providers/ObjectProvider.java`
- Modify: `src/main/java/com/example/providers/PlayerProvider.java`

**Interfaces:**
- Consumes: nothing.
- Produces: `/objects` entries gain `boatViewId` (int, or absent for top-level ocean-scene objects). `/player` gains `boatViewId` (int, or absent when not aboard a boat).

> **Why there is no unit test here.** This codebase only unit-tests provider logic that was first extracted into a pure value class with no `Client` reference (`RecordRing`, `BankSnapshot`). This change is three lines threaded through a client-thread scene walk; extracting it would be more risk than the change. It is verified by a live probe in Step 4, and by Task 2's pure tests which consume the field. Follow that existing convention — do **not** invent a mocked-`Client` test harness.

- [ ] **Step 1: Add the per-call field and thread it through the recursion**

In `ObjectProvider.java`, next to the existing `collisionMapsThisCall` / `interactableOnlyThisCall` per-call fields, add:

```java
	/** The id of the OUTERMOST non-top-level WorldView currently being walked -- i.e. the boat
	 *  whose deck these objects are on -- or null while walking the top-level ocean scene.
	 *
	 *  Outermost, not innermost, on purpose: walkWorldViewRecursively's own comment records that
	 *  the helm lives in a WorldEntity nested INSIDE the boat's main view, so keying on the
	 *  immediate view would split one boat's fixtures across several ids and defeat the whole
	 *  point. Depth 1 is the boat; anything deeper inherits it.
	 *
	 *  MEASURED 2026-09-07: on a mass world 26 "Salvaging station" objects from ~13 overlapping
	 *  boats appeared within a few tiles, three of them on the player's exact tile, all
	 *  clickable, with screen positions ~20px apart. Position cannot separate them; this can.
	 *
	 *  Only ever read/written on the client thread inside one computeObjects call, same as the
	 *  two fields above. */
	private Integer boatViewIdThisWalk;
```

- [ ] **Step 2: Set it when entering a depth-1 view, and restore it after**

In `walkWorldViewRecursively`, replace the child-recursion loop at the end of the method:

```java
		for (WorldEntity entity : view.worldEntities())
		{
			WorldView childView = entity.getWorldView();
			if (childView != null)
			{
				walkWorldViewRecursively(childView, false, out, depth + 1, radius, want);
			}
		}
```

with:

```java
		for (WorldEntity entity : view.worldEntities())
		{
			WorldView childView = entity.getWorldView();
			if (childView == null)
			{
				continue;
			}
			// depth 0 is the top-level ocean scene, so its direct children (depth 1) are the
			// boats. Deeper views inherit the boat's id rather than overwriting it.
			Integer previous = boatViewIdThisWalk;
			if (isTopLevel)
			{
				boatViewIdThisWalk = childView.getId();
			}
			walkWorldViewRecursively(childView, false, out, depth + 1, radius, want);
			boatViewIdThisWalk = previous;
		}
```

Also reset it at the top of `computeObjects(Integer radius, Target want, boolean interactableOnly)`, immediately after `interactableOnlyThisCall = interactableOnly;`:

```java
		boatViewIdThisWalk = null;
```

- [ ] **Step 3: Emit it in `buildObjectData`, and add the player's own**

In `buildObjectData`, immediately after `data.put("id", obj.getId());`, add:

```java
		if (boatViewIdThisWalk != null)
		{
			data.put("boatViewId", boatViewIdThisWalk);
		}
```

In `PlayerProvider.java`, the boat block already resolves the boat's WorldEntity for its hull size. Immediately after the existing `WorldEntityConfig boatConfig = boat != null ? boat.getConfig() : null;` line, add:

```java
			// The boat's own WorldView id, matching ObjectProvider's boatViewId stamp -- this is
			// how the Python side tells OUR fixtures from a neighbouring boat's on a mass world.
			if (boat != null && boat.getWorldView() != null)
			{
				data.put("boatViewId", boat.getWorldView().getId());
			}
```

- [ ] **Step 4: Rebuild and verify live**

Run: `./gradlew.bat run` (from the repo root), wait for `Bridge running on http://127.0.0.1:8080`, then with the character aboard the boat:

```bash
python -c "
import json,urllib.request
def g(p): return json.load(urllib.request.urlopen('http://127.0.0.1:8080'+p, timeout=8))
me = g('/player').get('boatViewId')
objs = g('/objects')
mine = [o for o in objs if o.get('name')=='Salvaging station' and o.get('boatViewId')==me]
others = [o for o in objs if o.get('name')=='Salvaging station']
print('player boatViewId:', me)
print('stations total:', len(others), 'on OUR boat:', len(mine))
"
```

Expected: `player boatViewId` is a non-None int, `on OUR boat: 1`, and `stations total` well above 1 when other boats are nearby. **If `on OUR boat` is 0 or above 1, stop and report — do not proceed to Task 2.**

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/providers/ObjectProvider.java src/main/java/com/example/providers/PlayerProvider.java
git commit -m "bridge: stamp boatViewId on objects and the player

On a mass world /objects lists every neighbouring boat's fixtures with real
world coordinates and real screen positions -- MEASURED 26 salvaging stations
from ~13 boats within a few tiles, three on the player's exact tile, all
clickable. Position cannot separate them, so sailing.deploy_hook's
first-object-matching-the-name pick is a coin flip on exactly the worlds
salvaging is done on.

boatViewId is the outermost non-top-level WorldView id -- the boat -- so
fixtures in the helm's deeper nested view still resolve to their own boat."
```

---

### Task 2: `sailing.find_facility` — click our own boat's fixtures

**Files:**
- Modify: `ControllerAddon/sailing.py`
- Test: `ControllerAddon/tests/test_shipwreck_salvaging.py`

**Interfaces:**
- Consumes: `boatViewId` from Task 1.
- Produces: `sailing.find_facility(name, action=None, player=None) -> dict | None` — the nearest object whose name contains `name` (case-insensitive) on **our own** boat, optionally offering `action`. `sailing.deploy_hook` now routes through it.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_shipwreck_salvaging.py`:

```python
# --- find_facility: our own boat vs. the neighbours' -------------------------------------
# Geometry MEASURED live 2026-09-07 on a mass world: 26 "Salvaging station" objects from ~13
# overlapping boats, three of them on the player's exact tile, all clickable, screen positions
# ~20px apart. Distance cannot separate them; boatViewId can.

def _station(boat_view_id, x=2681, y=2545, screen=(1209, 652)):
    return {"id": 59701, "name": "Salvaging station", "x": x, "y": y, "plane": 0,
            "visible": True, "clickable": True, "distance": 0,
            "screenX": screen[0], "screenY": screen[1],
            "actions": ["Sort-salvage", "Modify"], "boatViewId": boat_view_id}


def test_find_facility_picks_our_own_boats_station(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(2681, 2545),
                                                               boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _station(2001, screen=(1214, 672)),
        _station(1036, screen=(1209, 652)),
        _station(2002, screen=(1191, 648)),
    ])
    found = sailing.find_facility("salvaging station", action="Sort-salvage")
    assert found is not None
    assert found["boatViewId"] == 1036
    assert found["screenX"] == 1209


def test_find_facility_returns_none_when_only_other_boats_have_one(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(),
                                                               boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_station(2001), _station(2002)])
    assert sailing.find_facility("salvaging station") is None


def test_find_facility_filters_on_the_action_when_given(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(),
                                                               boatViewId=1036))
    hold = {"id": 60279, "name": "Mahogany cargo hold", "x": 2680, "y": 2545, "plane": 0,
            "visible": True, "screenX": 100, "screenY": 100,
            "actions": ["Open", "Deposit-all", "Modify"], "boatViewId": 1036}
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_station(1036), hold])
    assert sailing.find_facility("cargo hold", action="Open")["id"] == 60279
    assert sailing.find_facility("cargo hold", action="Sort-salvage") is None


def test_find_facility_falls_back_to_nearest_when_boat_view_id_is_absent(monkeypatch):
    """An un-rebuilt client serves no boatViewId. Behave exactly as before rather than
    refusing to work -- degrade gracefully, never fail closed."""
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(2681, 2545))
    near = {"id": 59701, "name": "Salvaging station", "x": 2681, "y": 2545, "plane": 0,
            "visible": True, "screenX": 1, "screenY": 1, "actions": ["Sort-salvage"]}
    far = {"id": 59701, "name": "Salvaging station", "x": 2690, "y": 2560, "plane": 0,
           "visible": True, "screenX": 2, "screenY": 2, "actions": ["Sort-salvage"]}
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [far, near])
    assert sailing.find_facility("salvaging station")["screenX"] == 1


def test_find_facility_ignores_invisible_objects(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(),
                                                               boatViewId=1036))
    hidden = dict(_station(1036), visible=False)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [hidden])
    assert sailing.find_facility("salvaging station") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k find_facility -v`
Expected: FAIL, `AttributeError: module 'sailing' has no attribute 'find_facility'`

- [ ] **Step 3: Implement `find_facility`**

In `ControllerAddon/sailing.py`, add above `deploy_hook`:

```python
def find_facility(name: str, action: str = None, player: dict = None):
    """The named facility ON OUR OWN BOAT, or None.

    MEASURED live 2026-09-07 on a mass world: /objects listed 26 "Salvaging station" objects
    from roughly 13 overlapping boats within a few tiles, three of them on the player's exact
    tile, every one reporting clickable:true, with screen positions about 20px apart.
    ObjectProvider recurses through every WorldEntity's WorldView by design (a boat's own
    fixtures live in nested views), so the neighbours' decks are genuinely in the scan and
    POSITION CANNOT SEPARATE THEM. Picking the nearest match -- what deploy_hook did until now
    -- is a coin flip between our hook and a stranger's on exactly the worlds this routine runs
    on.

    boatViewId (ObjectProvider/PlayerProvider, 2026-09-07) is the outermost non-top-level
    WorldView id, i.e. the boat. Matching it is exact.

    Falls back to nearest-by-name when either side lacks the field -- an un-rebuilt client must
    behave exactly as it did before, not refuse to work. That fallback keeps the neighbour-boat
    bug it already had; it does not introduce a new one.
    """
    needle = (name or "").lower()
    player = player if player is not None else controller.get_player()
    our_view = player.get("boatViewId")

    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))

    best, best_dist = None, None
    for obj in controller.get_objects(radius=BOAT_OBJECT_SEARCH_RADIUS):
        if needle not in (obj.get("name") or "").lower():
            continue
        if not obj.get("visible", True):
            continue
        if action is not None and action not in (obj.get("actions") or []):
            continue
        if our_view is not None and obj.get("boatViewId") is not None:
            if obj.get("boatViewId") != our_view:
                continue

        if origin_x is None or origin_y is None:
            return obj
        dist = max(abs((obj.get("x") or 0) - origin_x), abs((obj.get("y") or 0) - origin_y))
        if best_dist is None or dist < best_dist:
            best, best_dist = obj, dist

    return best
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k find_facility -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Move `deploy_hook` onto it**

In `ControllerAddon/sailing.py`, replace the body of `deploy_hook`'s lookup — the block reading:

```python
    hook = next(
        (o for o in controller.get_objects(radius=BOAT_OBJECT_SEARCH_RADIUS)
         if hook_name.lower() in (o.get("name") or "").lower() and o.get("visible", True)),
        None,
    )
```

with:

```python
    # find_facility, not a bare nearest-by-name scan: on a mass world the neighbours' hooks are
    # in the scan too, at the same coordinates (see find_facility's own docstring).
    hook = find_facility(hook_name, action="Deploy")
```

- [ ] **Step 6: Run the full suite**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -v`
Expected: PASS, including the pre-existing `test_deploy_hook_and_helm_clicks_skip_simulated_misclick`.

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/sailing.py ControllerAddon/tests/test_shipwreck_salvaging.py
git commit -m "sailing: find_facility picks our own boat's fixtures, not a neighbour's

deploy_hook picked the first object whose name matched within radius 20. On a
mass world that is a coin flip -- MEASURED 26 salvaging stations and many
hooks from ~13 overlapping boats, three stations on the player's exact tile,
all clickable. Filters on boatViewId, falling back to nearest-by-name on an
un-rebuilt client so nothing regresses."
```

---

### Task 3: The keep-list and generalised dropping

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py`
- Test: `ControllerAddon/tests/test_shipwreck_salvaging.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `shipwreck_salvaging.HARD_KEEP` (frozenset of lowercase substrings), `shipwreck_salvaging.keep_item(name, keep_items) -> bool`, `shipwreck_salvaging.drop_items(ctx, predicate) -> int`. `DEFAULT_CONFIG` gains `massWorld`, `stowSalvage`, `sortWhenIdle`, `holdFullSlots`, `keepItems`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_shipwreck_salvaging.py`:

```python
# --- keep-list ---------------------------------------------------------------------------

def test_hard_keep_survives_an_empty_keep_list():
    """keepItems is a list a human edits between sessions; a typo must never be able to drop
    the tools."""
    for tool in ["Chisel", "Rune pouch", "Coins", "Gem bag", "Plank sack", "Log basket"]:
        assert ss.keep_item(tool, []) is True


def test_hard_keep_cannot_be_overridden_by_an_empty_config():
    assert ss.keep_item("Chisel", []) is True
    assert ss.keep_item("Gold ring", []) is False


def test_salvage_is_always_kept():
    """Unsorted salvage is the routine's WORK -- it gets stowed or sorted, never dropped by the
    post-sort disposal."""
    assert ss.keep_item("Plundered salvage", []) is True
    assert ss.keep_item("Small salvage", []) is True


def test_keep_list_matches_case_insensitive_substrings():
    assert ss.keep_item("Diamond bracelet", ["diamond"]) is True
    assert ss.keep_item("Diamond ring", ["diamond"]) is True
    assert ss.keep_item("Ruby bracelet", ["RUBY"]) is True
    assert ss.keep_item("Emerald ring", ["diamond", "ruby"]) is False


def test_the_inert_amulet_is_matched_without_relying_on_the_apostrophe():
    """Cache obj 32398 is named "Sailors' amulet (inert)". The default list keys on
    "amulet (inert)" precisely so a differing apostrophe cannot silently drop it."""
    assert "amulet (inert)" in ss.DEFAULT_CONFIG["keepItems"]
    assert ss.keep_item("Sailors' amulet (inert)", ss.DEFAULT_CONFIG["keepItems"]) is True
    assert ss.keep_item("Sailors’ amulet (inert)", ss.DEFAULT_CONFIG["keepItems"]) is True


def test_the_default_keep_list_drops_what_the_user_asked_to_toss():
    keep = ss.DEFAULT_CONFIG["keepItems"]
    for tossed in ["Casket", "Oyster pearls", "Gold ring", "Sapphire ring",
                   "Emerald bracelet", "Mithril scimitar", "Mithril cannonball",
                   "Adamant cannonball"]:
        assert ss.keep_item(tossed, keep) is False, tossed


def test_the_default_keep_list_keeps_what_the_user_asked_to_keep():
    keep = ss.DEFAULT_CONFIG["keepItems"]
    for kept in ["Facility bottle (empty)", "Mahogany repair kit", "Diamond ring",
                 "Diamond bracelet", "Ruby bracelet", "Dragon cannonball",
                 "Rune scimitar", "Rune cannonball", "Sailors' amulet (inert)"]:
        assert ss.keep_item(kept, keep) is True, kept


# --- drop_items --------------------------------------------------------------------------

def test_drop_items_drops_only_what_the_predicate_selects(monkeypatch):
    inv = [{"name": "Gold ring", "screenX": 1, "screenY": 1},
           {"name": "Chisel", "screenX": 2, "screenY": 2},
           {"name": "Rune scimitar", "screenX": 3, "screenY": 3}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: clicked.append(item["name"]) or True)
    ctx = _Ctx()
    dropped = ss.drop_items(ctx, lambda name: name == "Gold ring")
    assert clicked == ["Gold ring"]
    assert dropped == 1


def test_drop_items_never_simulates_a_misclick(monkeypatch):
    """A simulated near-miss lands on the NEIGHBOURING slot -- which is a tool."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Gold ring", "screenX": 1, "screenY": 1}])
    seen = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: seen.update(kw) or True)
    ss.drop_items(_Ctx(), lambda name: True)
    assert seen.get("simulate_mistakes") is False
    assert seen.get("check_ui_blockers") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k "keep_item or keep_list or hard_keep or drop_items or inert_amulet or salvage_is_always" -v`
Expected: FAIL, `AttributeError: module 'shipwreck_salvaging' has no attribute 'keep_item'`

- [ ] **Step 3: Add the config keys**

In `ControllerAddon/routines/shipwreck_salvaging.py`, inside `DEFAULT_CONFIG`, immediately after the existing `"dropAtFreeSlots"` entry, add:

```python
    # --- mass-world salvaging + on-boat sorting (2026-09-07) ---------------------------------
    # See docs/superpowers/specs/2026-09-07-shipwreck-salvaging-mass-world-and-sorting-design.md
    "massWorld": True,        # never sail: an area's wrecks share a spawn pool and one rises as
                              # another sinks (wiki, Shipwreck salvaging rev 15290733), so on a
                              # busy world waiting beats sailing. Disables the far-wreck branch
                              # AND the won't-engage reposition.
    "stowSalvage": True,      # deposit salvage into the boat's cargo hold instead of dropping
                              # it on the deck. False restores the pre-2026-09-07 behaviour.
    "sortWhenIdle": True,     # spend a respawn gap sorting rather than idling
    "holdFullSlots": 4,       # treat the hold as full this many slots short of its capacity
    "keepItems": [            # case-insensitive SUBSTRING keep-list applied to SORTED output.
                              # Trap inherited from banker.py's same convention: "ring" would
                              # match Gold/Sapphire/Ruby ring alike, and "rune" would match every
                              # runite item -- hence the spelled-out entries below.
        "amulet (inert)",     # Sailors' amulet (inert), cache obj 32398. Keyed WITHOUT the
                              # apostrophe on purpose: that character is the one most likely to
                              # differ between the cache dump and the runtime item name, and a
                              # near-miss here fails silently IN THE DROP DIRECTION.
        "facility bottle",    # ~34.6k each, the most valuable single drop
        "mahogany repair kit",
        "diamond",            # Diamond ring + Diamond bracelet, ~2.1-2.3k each
        "ruby",               # Ruby bracelet ~1.4k
        "dragon cannonball",
        "rune scimitar",
        "rune cannonball",
    ],
```

- [ ] **Step 4: Add `HARD_KEEP` and `keep_item`**

Add near `_holds_salvage`:

```python
# Items the keep-list can NEVER drop, whatever the config says. keepItems is a list a human
# edits between sessions, and a typo there must not be able to throw away the chisel or the rune
# pouch -- same non-negotiable-floor reasoning banker.py applies to bank UI buttons and
# sailing._FIXTURE_CLICK_KWARGS applies to deck clicks. Lowercase substrings.
HARD_KEEP = frozenset({
    "chisel", "rune pouch", "coins", "gem bag", "plank sack", "log basket",
    "salvage",   # unsorted salvage is the WORK -- it gets stowed or sorted, never dropped here
})


def keep_item(name: str, keep_items) -> bool:
    """Whether an inventory item survives post-sort disposal.

    Case-insensitive substring match against HARD_KEEP first (non-negotiable), then the
    configured keep-list. Everything else is dropped."""
    lowered = (name or "").lower()
    if any(needle in lowered for needle in HARD_KEEP):
        return True
    return any((needle or "").lower() in lowered for needle in (keep_items or []))
```

- [ ] **Step 5: Generalise `drop_salvage` into `drop_items`**

Rename `drop_salvage` to `drop_items(ctx, predicate)`. **Keep its entire existing docstring verbatim** — every paragraph in it records a live-confirmed fact (the `check_ui_blockers=False` reasoning, the forced `w` press when `facilitiesTabOpen`, the Space fallback for the dialogue that eats it) and all of it still applies. Add one paragraph at the top of that docstring:

```
    """Drop every inventory item `predicate(name)` selects. `predicate` takes the item's name
    as the game reports it (NOT lowercased -- lowercase inside your predicate if you need to).

    <... the existing docstring, unchanged, follows ...>
```

Keep the whole facilities-tab / Space-fallback / `open_inventory_tab` preamble exactly as it is. Replace only its final loop:

```python
    controller.open_inventory_tab(ctx.ser)
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if name.endswith("salvage"):
```

so that the function becomes a thin wrapper and the loop moves into `drop_items`. Rename the function to `drop_items(ctx, predicate)`, change the loop to:

```python
    controller.open_inventory_tab(ctx.ser)
    dropped = 0
    for item in controller.get_inventory():
        name = item.get("name") or ""
        if predicate(name):
            # simulate_mistakes=False (2026-09-03): with Drop as the forced left-click, a
            # simulated near-miss lands on the NEIGHBOURING slot -- fine when that is more
            # salvage, but it drops whatever tool happens to sit there (chisel, rune pouch, ...)
            # when it is not. Same reasoning as banker.py's no-misclick rule for bank UI buttons.
            controller.click_entity(ctx.ser, item, check_ui_blockers=False,
                                    simulate_mistakes=False)
            dropped += 1
            controller.human_delay(0.06, 0.12)
    return dropped
```

and add the old name back as a wrapper immediately below it:

```python
def drop_salvage(ctx):
    """Drop every inventory item whose name ends in "salvage" -- the pre-2026-09-07 behaviour,
    still used when stowSalvage is off."""
    return drop_items(ctx, lambda name: name.lower().endswith("salvage"))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -v`
Expected: PASS, including the pre-existing `test_drop_salvage_never_simulates_a_misclick` and `test_tick_drops_before_the_inventory_is_completely_full`.

- [ ] **Step 7: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py ControllerAddon/tests/test_shipwreck_salvaging.py
git commit -m "salvaging: keep-list config, a non-negotiable hard-keep, generalised dropping

keepItems is a case-insensitive substring keep-list for sorted output, seeded
from the pirate money-making guide's own output table plus the user's own
Sailors' amulet (inert) -- which that table omits entirely, so the list is
known incomplete.

HARD_KEEP is separate and unconfigurable: keepItems is edited by hand between
sessions and a typo must not be able to drop the chisel. The inert amulet is
keyed on 'amulet (inert)' rather than the apostrophe-bearing full name,
because a near-miss under a substring match fails silently in the DROP
direction."
```

---

### Task 4: `cargo_hold.py` — drive interface 943

**Files:**
- Create: `ControllerAddon/cargo_hold.py`
- Modify: `ControllerAddon/controller.py`
- Test: `ControllerAddon/tests/test_cargo_hold.py`

**Interfaces:**
- Consumes: `sailing.find_facility` (Task 2).
- Produces: `controller.continue_box_open() -> bool`, `controller.dismiss_continue_box(ser) -> bool`; and in `cargo_hold`: `is_open()`, `read_usage() -> (used, capacity)`, `salvage_rows() -> list`, `open_hold(ctx) -> bool`, `deposit_salvage(ctx) -> bool`, `withdraw_salvage(ctx) -> bool`, `close_hold(ctx) -> bool`.

> The routine file is already 852 lines. A separate module keeps the interface driver testable on its own and reusable — `deep_sea_trawling.py` will need the same hold one day.

- [ ] **Step 1: Write the failing tests**

Create `ControllerAddon/tests/test_cargo_hold.py`:

```python
"""Hermetic tests for cargo_hold.py (added 2026-09-07).

Every widget id here was captured live on 2026-09-07 from a 3x8 boat's cargo hold:
943 = InterfaceID.SAILING_BOAT_CARGOHOLD, 944 = ..._SIDE. Nothing here touches the client.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import cargo_hold
import controller


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = type("S", (), {"data": {}, "config": {}})()
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", lambda *a, **k: {"found": False})
    monkeypatch.setattr(controller, "get_widget_children", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)


def _widgets(mapping):
    """mapping: {(group, child): payload}"""
    def get_widget(group, child, index=None):
        return mapping.get((group, child), {"found": False})
    return get_widget


def test_is_open_reads_the_title_widget(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: Queen Death",
                   "screenX": 10, "screenY": 10},
    }))
    assert cargo_hold.is_open() is True


def test_is_open_is_false_when_the_interface_is_absent():
    assert cargo_hold.is_open() is False


def test_read_usage_parses_used_and_capacity(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 4): {"found": True, "text": "12"},
        (943, 5): {"found": True, "text": "160"},
    }))
    assert cargo_hold.read_usage() == (12, 160)


def test_read_usage_returns_none_when_unreadable():
    """Unreadable means 'cannot tell', never 'empty' -- the caller must not conclude the hold
    has room from a failed read."""
    assert cargo_hold.read_usage() == (None, None)


def test_read_usage_tolerates_thousands_separators(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 4): {"found": True, "text": "1,024"},
        (943, 5): {"found": True, "text": "2,000"},
    }))
    assert cargo_hold.read_usage() == (1024, 2000)


def test_salvage_rows_only_returns_salvage(monkeypatch):
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [
        {"index": 0, "itemName": "Plundered salvage", "itemQty": 40,
         "clickable": True, "screenX": 5, "screenY": 5},
        {"index": 1, "itemName": "Mahogany plank", "itemQty": 3,
         "clickable": True, "screenX": 6, "screenY": 6},
    ])
    rows = cargo_hold.salvage_rows()
    assert [r["itemName"] for r in rows] == ["Plundered salvage"]


def test_deposit_salvage_clicks_the_salvage_button_not_the_inventory_one(monkeypatch):
    """943.14 is Deposit Salvage; 943.15 is Deposit Inventory and would bank the chisel."""
    clicked = []
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
        (943, 15): {"found": True, "clickable": True, "screenX": 15, "screenY": 15},
    }))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, w, **kw: clicked.append(w["screenX"]) or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    assert cargo_hold.deposit_salvage(_Ctx()) is True
    assert clicked == [14]


def test_deposit_salvage_reports_failure_when_salvage_remains(monkeypatch):
    """The authoritative full signal: a Deposit Salvage that leaves salvage behind means the
    hold would not take it, whatever 943.4/943.5 say."""
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
    }))
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage"}])
    assert cargo_hold.deposit_salvage(_Ctx()) is False


def test_interface_clicks_skip_the_ui_blocker_check_and_misclicks(monkeypatch):
    seen = {}
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (943, 1): {"found": True, "clickable": True, "text": "Cargo Hold: X",
                   "screenX": 1, "screenY": 1},
        (943, 14): {"found": True, "clickable": True, "screenX": 14, "screenY": 14},
    }))
    monkeypatch.setattr(controller, "click_entity", lambda ser, w, **kw: seen.update(kw) or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    cargo_hold.deposit_salvage(_Ctx())
    assert seen.get("check_ui_blockers") is False
    assert seen.get("simulate_mistakes") is False


def test_continue_box_is_detected_by_presence_not_by_its_wording(monkeypatch):
    """The 229 text is tier-specific ("...any more PLUNDERED salvage"), so never match on it."""
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (229, 4): {"found": True, "clickable": True, "text": "Click here to continue",
                   "screenX": 1, "screenY": 1},
    }))
    assert controller.continue_box_open() is True


def test_continue_box_absent_when_the_widget_is_not_clickable(monkeypatch):
    monkeypatch.setattr(controller, "get_widget", _widgets({
        (229, 4): {"found": True, "clickable": False},
    }))
    assert controller.continue_box_open() is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_cargo_hold.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'cargo_hold'`

- [ ] **Step 3: Add the 229 helpers to `controller.py`**

In `ControllerAddon/controller.py`, immediately after `get_widget_children`, add:

```python
# The plain "click here to continue" message box. MEASURED live 2026-09-07 during shipwreck
# salvaging: 229.3 read "Your inventory is too full to hold any more plundered salvage." while
# GET /state's dialogOpen read FALSE -- GameStateProvider.dialogOpen only ORs the NPC/player/
# sprite dialogue widgets, so this box is invisible to it AND to the engine's own
# check_stray_dialog. It also eats tab hotkeys. karambwan_fishing.py documents the same trap.
CONTINUE_BOX_GROUP = 229
CONTINUE_BOX_CHILD = 4


def continue_box_open() -> bool:
    """Whether the plain message box is on screen. Detected by the CONTINUE widget's presence,
    never by 229.3's text -- that wording is tier-specific ("...any more plundered salvage")
    and would silently stop matching on a different salvage tier."""
    widget = get_widget(CONTINUE_BOX_GROUP, CONTINUE_BOX_CHILD)
    return bool(widget.get("found")) and bool(widget.get("clickable"))


def dismiss_continue_box(ser) -> bool:
    """Space the box away, confirming it actually closed. Returns True if nothing was blocking
    or it closed; False if it is still up."""
    if not continue_box_open():
        return True
    key_tap(ser, "SPACE")
    human_delay(0.3, 0.5)
    return not continue_box_open()
```

- [ ] **Step 4: Create `cargo_hold.py`**

Create `ControllerAddon/cargo_hold.py`:

```python
"""The boat's cargo hold, interface 943 (InterfaceID.SAILING_BOAT_CARGOHOLD).

Every id here was CAPTURED LIVE on 2026-09-07 from a 3x8 boat by a read-only widget watcher
polling /widgets/dump. See
docs/superpowers/specs/2026-09-07-shipwreck-salvaging-mass-world-and-sorting-design.md.

    943.1        title ("Cargo Hold: <boat name>"), and carries the Close action
    943.3        "Item stacking" toggle
    943.4        slots USED
    943.5        CAPACITY
    943.10       the hold's item grid -- Withdraw-All / -1 / -5 / -10 / -X / Examine
    943.13       Deposit Cargo
    943.14       Deposit SALVAGE          <- the only deposit this module ever clicks
    943.15       Deposit Inventory
    943.20..24   quantity buttons 1 / 5 / 10 / X / All
    944.1        the player's inventory side panel

Why 943.14 and never 943.15 or the hold object's own "Deposit-all": the salvaging setup carries
a chisel, a rune pouch and coins, and a whole-inventory deposit banks them. A salvage-only
button removes that failure mode entirely rather than guarding against it.

NOTE the cargo hold's ITEM CONTAINER is NOT readable: TrawlingProvider polls inv 963..967
(verified as the correct ids) and MEASURED null for the whole session, with carryingCargo
stuck at 0, while this interface showed 12 items. So fullness comes from 943.4/943.5, read
while the interface is open -- which costs nothing, since the hold is opened to deposit anyway.
"""

import controller
import debug_log
import sailing

HOLD_GROUP = 943
SIDE_GROUP = 944

TITLE_CHILD = 1
USED_CHILD = 4
CAPACITY_CHILD = 5
GRID_CHILD = 10
DEPOSIT_SALVAGE_CHILD = 14

OPEN_TRIES = 3
OPEN_POLL_S = 0.35

# Interface clicks: never blocker-checked (an interface element is "behind a UI panel" by
# definition, and rotating the camera can never clear it), and never misclicked (a near-miss on
# a grid deposits or withdraws the neighbouring slot).
_CLICK_KWARGS = {"check_ui_blockers": False, "simulate_mistakes": False}


def is_open() -> bool:
    widget = controller.get_widget(HOLD_GROUP, TITLE_CHILD)
    return bool(widget.get("found")) and bool(widget.get("clickable"))


def _read_int(child):
    widget = controller.get_widget(HOLD_GROUP, child)
    text = (widget.get("text") or "").strip().replace(",", "")
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def read_usage():
    """(used, capacity) off 943.4/943.5, or (None, None) when unreadable.

    Unreadable means "cannot tell", NEVER "empty" -- a caller must not conclude the hold has
    room from a failed read. Same degrade-gracefully stance as hover_matches."""
    return _read_int(USED_CHILD), _read_int(CAPACITY_CHILD)


def salvage_rows():
    """Every salvage stack in the hold's grid (943.10), newest-first as the game orders them."""
    rows = []
    for child in controller.get_widget_children(HOLD_GROUP, GRID_CHILD):
        if (child.get("itemName") or "").lower().endswith("salvage"):
            rows.append(child)
    return rows


def _click(ctx, widget) -> bool:
    if not widget.get("clickable") or widget.get("screenX") is None:
        return False
    controller.click_entity(ctx.ser, widget, **_CLICK_KWARGS)
    return True


def open_hold(ctx) -> bool:
    """Click our own boat's cargo hold and confirm 943 opened."""
    if is_open():
        return True
    hold = sailing.find_facility("cargo hold", action="Open")
    if hold is None:
        debug_log.log("salvage", "no cargo hold on our own boat")
        return False
    controller.click_entity(ctx.ser, hold, offset=(0, 0), simulate_mistakes=False)
    for _ in range(OPEN_TRIES):
        controller.human_delay(OPEN_POLL_S, OPEN_POLL_S + 0.2)
        controller.dismiss_continue_box(ctx.ser)
        if is_open():
            return True
    debug_log.log("salvage", "the cargo hold did not open")
    return False


def deposit_salvage(ctx) -> bool:
    """Click 943.14 Deposit Salvage. Returns True only once no salvage remains in the
    inventory -- salvage LEFT BEHIND is the authoritative "the hold is full" signal, whatever
    943.4/943.5 say (the same rule deep_sea_trawling applies to a Collect-from that moved
    nothing, because a declared capacity is a ceiling, not a promise)."""
    if not is_open():
        return False
    if not _click(ctx, controller.get_widget(HOLD_GROUP, DEPOSIT_SALVAGE_CHILD)):
        debug_log.log("salvage", "Deposit Salvage button not clickable")
        return False
    controller.human_delay(0.35, 0.6)
    controller.dismiss_continue_box(ctx.ser)
    remaining = [i for i in controller.get_inventory()
                 if (i.get("name") or "").lower().endswith("salvage")]
    return not remaining


def withdraw_salvage(ctx) -> bool:
    """Withdraw-All the first salvage stack in the grid. Salvage does not stack in the
    inventory (MEASURED: 944 listed 14 separate Plundered salvage entries), so the game caps
    this at the free slot count on its own -- no quantity arithmetic needed."""
    if not is_open():
        return False
    rows = salvage_rows()
    if not rows:
        return False
    row = rows[0]
    # NOT **_CLICK_KWARGS: click_menu_action's signature is
    # (ser, entity, option, target_hint, offset, check_ui_blockers, refresh_target, refresh_legs)
    # -- it has no simulate_mistakes parameter (it never misclicks by construction), so splatting
    # the dict here is a TypeError.
    controller.click_menu_action(ctx.ser, row, "Withdraw-All", offset=(0, 0),
                                 check_ui_blockers=False)
    controller.human_delay(0.35, 0.6)
    controller.dismiss_continue_box(ctx.ser)
    return any((i.get("name") or "").lower().endswith("salvage")
               for i in controller.get_inventory())


def close_hold(ctx) -> bool:
    if not is_open():
        return True
    controller.key_tap(ctx.ser, "ESC")
    controller.human_delay(0.25, 0.4)
    if not is_open():
        return True
    _click(ctx, controller.get_widget(HOLD_GROUP, TITLE_CHILD))
    controller.human_delay(0.25, 0.4)
    return not is_open()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_cargo_hold.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/cargo_hold.py ControllerAddon/controller.py ControllerAddon/tests/test_cargo_hold.py
git commit -m "cargo_hold: drive interface 943, and a shared 229 continue-box guard

Widget ids captured live 2026-09-07 off a 3x8 boat. Fullness reads from
943.4/943.5 because the hold's ITEM CONTAINER is unreadable -- TrawlingProvider
polls the correct inv ids 963..967 and MEASURED null all session with 12 items
in the hold, and carryingCargo stayed 0 (it tracks courier cargo).

Deposits go through 943.14 Deposit SALVAGE, never 943.15 Deposit Inventory nor
the hold object's own Deposit-all, both of which would bank the chisel and rune
pouch.

controller.continue_box_open detects the group-229 box by the CONTINUE widget's
presence, never by 229.3's text -- that wording is salvage-tier-specific."
```

---

### Task 5: `derive_phase` — the state machine

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py`
- Test: `ControllerAddon/tests/test_shipwreck_salvaging.py`

**Interfaces:**
- Consumes: `DEFAULT_CONFIG` keys from Task 3.
- Produces: `shipwreck_salvaging.derive_phase(config, *, wreck_in_range, hold_full, inventory_salvage, free_slots, hold_has_salvage) -> str` returning one of `"sort"`, `"stow"`, `"hook"`, `"wait"`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_shipwreck_salvaging.py`:

```python
# --- derive_phase ------------------------------------------------------------------------

def _cfg(**over):
    cfg = dict(ss.DEFAULT_CONFIG)
    cfg.update(over)
    return cfg


def _phase(**kw):
    args = {"wreck_in_range": False, "hold_full": False, "inventory_salvage": 0,
            "free_slots": 20, "hold_has_salvage": False}
    cfg = kw.pop("config", _cfg())
    args.update(kw)
    return ss.derive_phase(cfg, **args)


def test_a_wreck_in_range_means_hook():
    assert _phase(wreck_in_range=True) == "hook"


def test_nothing_in_range_and_nothing_to_sort_means_wait():
    assert _phase() == "wait"


def test_a_full_inventory_of_salvage_means_stow():
    assert _phase(wreck_in_range=True, inventory_salvage=27, free_slots=1) == "stow"


def test_a_full_hold_outranks_a_wreck_in_range():
    """With the hold full there is nowhere for new salvage to go, so hooking would only fill
    the inventory and stall. This is the user's explicit exception to the interrupt rule."""
    assert _phase(wreck_in_range=True, hold_full=True, hold_has_salvage=True) == "sort"


def test_a_wreck_rising_mid_sort_returns_to_the_hook():
    """The user's interrupt rule: hooking out-earns sorting, so abandon the sort."""
    assert _phase(wreck_in_range=True, hold_has_salvage=True, hold_full=False) == "hook"


def test_an_idle_gap_with_salvage_in_the_hold_means_sort():
    assert _phase(wreck_in_range=False, hold_has_salvage=True) == "sort"


def test_an_idle_gap_with_salvage_in_the_inventory_means_sort():
    assert _phase(wreck_in_range=False, inventory_salvage=5) == "sort"


def test_sort_when_idle_off_leaves_an_idle_gap_as_wait():
    assert _phase(config=_cfg(sortWhenIdle=False), hold_has_salvage=True) == "wait"


def test_sort_when_idle_off_still_sorts_a_full_hold():
    """A full hold is not an optimisation, it is a blockage -- sortWhenIdle must not gate it."""
    assert _phase(config=_cfg(sortWhenIdle=False), hold_full=True,
                  hold_has_salvage=True) == "sort"


def test_an_unknown_hold_is_tried_once_rather_than_assumed_empty():
    """hold_has_salvage None = never read this session. Assuming 'no' means a restart during a
    respawn gap never sorts until the next stow; one read settles it."""
    assert _phase(hold_has_salvage=None) == "sort"


def test_stow_salvage_off_never_stows():
    assert _phase(config=_cfg(stowSalvage=False), wreck_in_range=True,
                  inventory_salvage=27, free_slots=1) == "hook"


def test_stow_needs_actual_salvage_not_just_a_full_inventory():
    """An inventory full of sorted output is a disposal problem, not a stow one."""
    assert _phase(wreck_in_range=True, inventory_salvage=0, free_slots=0) == "hook"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k derive_phase -v`
Expected: FAIL, `AttributeError: module 'shipwreck_salvaging' has no attribute 'derive_phase'`

- [ ] **Step 3: Implement `derive_phase`**

Add to `ControllerAddon/routines/shipwreck_salvaging.py`, above `tick`:

```python
def derive_phase(config, *, wreck_in_range, hold_full, inventory_salvage, free_slots,
                 hold_has_salvage):
    """Which phase this tick should run, derived ENTIRELY from live state.

    Never from a stored phase: ctx.state.data is wiped on restart while the boat's real state
    is not, so a remembered phase and the world desync exactly when it matters. Same rule
    karambwan_fishing.derive_phase and corrupted_gauntlet.derive_phase follow.

    hold_has_salvage is True / False / None, where None means "the hold has never been read
    this session". Unknown is treated as "try once": assuming no would mean a restart during a
    respawn gap never sorts until the next stow, and assuming yes forever would spin. One read
    settles it and the answer is recorded.

    Order matters:
      1. A FULL HOLD outranks everything -- there is nowhere for new salvage to go, so hooking
         would only fill the inventory and stall. This is the user's stated exception to the
         interrupt rule, and it is deliberately NOT gated on sortWhenIdle: a full hold is a
         blockage, not an optimisation.
      2. An idle gap with something to sort, when sortWhenIdle is on.
      3. Stowing a salvage-laden inventory.
      4. Hooking.
    Because 4 is below 2, a wreck rising mid-sort returns us to the hook -- hooking out-earns
    sorting (the user's interrupt rule)."""
    something_to_sort = bool(inventory_salvage) or hold_has_salvage is not False

    if hold_full and something_to_sort:
        return "sort"
    if config.get("sortWhenIdle", True) and not wreck_in_range and something_to_sort:
        return "sort"
    if (config.get("stowSalvage", True) and inventory_salvage
            and free_slots <= config.get("dropAtFreeSlots", 1)):
        return "stow"
    if wreck_in_range:
        return "hook"
    return "wait"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k derive_phase -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py ControllerAddon/tests/test_shipwreck_salvaging.py
git commit -m "salvaging: derive_phase state machine (sort / stow / hook / wait)

Derived entirely from live state, never a stored phase -- ctx.state.data is
wiped on restart while the boat's real state is not.

Encodes the user's two rules: a wreck rising mid-sort returns to the hook
(hooking out-earns sorting), EXCEPT when the hold is full, where there is
nowhere for new salvage to go. An unread hold is 'try once' rather than
assumed empty."
```

---

### Task 6: `stow_phase`

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py`
- Test: `ControllerAddon/tests/test_shipwreck_salvaging.py`

**Interfaces:**
- Consumes: `cargo_hold` (Task 4), `drop_salvage` (Task 3).
- Produces: `shipwreck_salvaging.stow_phase(ctx) -> bool` (True when salvage left the inventory). Records `ctx.state.data["hold_used"]`, `["hold_capacity"]`, `["hold_has_salvage"]`, `["hold_full"]`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_shipwreck_salvaging.py`:

```python
# --- stow_phase --------------------------------------------------------------------------

class _FakeHold:
    def __init__(self, usage=(12, 160), deposit=True, rows=("Plundered salvage",)):
        self.usage, self.deposit_ok, self.rows = usage, deposit, rows
        self.calls = []

    def open_hold(self, ctx):
        self.calls.append("open"); return True

    def deposit_salvage(self, ctx):
        self.calls.append("deposit"); return self.deposit_ok

    def read_usage(self):
        return self.usage

    def salvage_rows(self):
        return [{"itemName": n} for n in self.rows]

    def close_hold(self, ctx):
        self.calls.append("close"); return True


def _install_hold(monkeypatch, fake):
    for name in ("open_hold", "deposit_salvage", "read_usage", "salvage_rows", "close_hold"):
        monkeypatch.setattr(ss.cargo_hold, name, getattr(fake, name))


def test_stow_deposits_and_records_the_hold_usage(monkeypatch):
    fake = _FakeHold(usage=(40, 160))
    _install_hold(monkeypatch, fake)
    ctx = _Ctx()
    assert ss.stow_phase(ctx) is True
    assert fake.calls == ["open", "deposit", "close"]
    assert ctx.state.data["hold_used"] == 40
    assert ctx.state.data["hold_capacity"] == 160
    assert ctx.state.data["hold_has_salvage"] is True
    assert ctx.state.data["hold_full"] is False


def test_stow_marks_the_hold_full_at_the_configured_margin(monkeypatch):
    _install_hold(monkeypatch, _FakeHold(usage=(157, 160)))   # holdFullSlots default 4
    ctx = _Ctx()
    ss.stow_phase(ctx)
    assert ctx.state.data["hold_full"] is True


def test_salvage_left_behind_marks_the_hold_full_whatever_the_numbers_say(monkeypatch):
    """The authoritative signal: a declared capacity is a ceiling, not a promise."""
    _install_hold(monkeypatch, _FakeHold(usage=(10, 160), deposit=False))
    ctx = _Ctx()
    assert ss.stow_phase(ctx) is False
    assert ctx.state.data["hold_full"] is True


def test_an_unreadable_usage_does_not_claim_the_hold_has_room(monkeypatch):
    """Unreadable means 'cannot tell', never 'empty'."""
    _install_hold(monkeypatch, _FakeHold(usage=(None, None)))
    ctx = _Ctx()
    ss.stow_phase(ctx)
    assert ctx.state.data.get("hold_full") is False
    assert ctx.state.data.get("hold_used") is None


def test_stow_falls_back_to_dropping_when_the_hold_cannot_be_opened(monkeypatch):
    fake = _FakeHold()
    fake.open_hold = lambda ctx: False
    _install_hold(monkeypatch, fake)
    dropped = []
    monkeypatch.setattr(ss, "drop_salvage", lambda ctx: dropped.append(True) or 1)
    assert ss.stow_phase(_Ctx()) is True
    assert dropped == [True]


def test_stow_salvage_off_drops_instead(monkeypatch):
    called = []
    monkeypatch.setattr(ss, "drop_salvage", lambda ctx: called.append(True) or 1)
    _install_hold(monkeypatch, _FakeHold())
    ctx = _Ctx()
    ctx.state.config = _cfg(stowSalvage=False)
    assert ss.stow_phase(ctx) is True
    assert called == [True]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k stow -v`
Expected: FAIL, `AttributeError: module 'shipwreck_salvaging' has no attribute 'stow_phase'`

- [ ] **Step 3: Implement `stow_phase`**

Add `import cargo_hold` to the routine's import block (alongside `import sailing`), then add above `tick`:

```python
def _record_hold(data, used, capacity, has_salvage, config, forced_full=False):
    """Record what the open hold reported. Unreadable numbers mean "cannot tell" -- they must
    never make the hold LOOK full or LOOK empty, so hold_full only goes True on a real reading
    or on the empirical forced_full signal."""
    data["hold_used"] = used
    data["hold_capacity"] = capacity
    data["hold_has_salvage"] = has_salvage
    if forced_full:
        data["hold_full"] = True
        return
    if used is None or capacity is None:
        data["hold_full"] = False
        return
    data["hold_full"] = used >= capacity - config.get("holdFullSlots", 4)


def stow_phase(ctx) -> bool:
    """Deposit the inventory's salvage into our own boat's cargo hold, recording its fullness
    while the interface is open (the only place it is readable -- see cargo_hold's docstring).

    Falls back to dropping on the deck whenever the hold cannot be opened, so a broken or
    missing hold degrades to the pre-2026-09-07 behaviour instead of stalling the run."""
    config = ctx.state.config
    data = ctx.state.data

    if not config.get("stowSalvage", True):
        return bool(drop_salvage(ctx))

    if not cargo_hold.open_hold(ctx):
        debug_log.log("salvage", "could not open the cargo hold -- dropping salvage instead")
        return bool(drop_salvage(ctx))

    deposited = cargo_hold.deposit_salvage(ctx)
    used, capacity = cargo_hold.read_usage()
    has_salvage = bool(cargo_hold.salvage_rows())
    _record_hold(data, used, capacity, has_salvage, config, forced_full=not deposited)

    if not deposited:
        debug_log.log("salvage", f"salvage left in the inventory after Deposit Salvage "
                                  f"(hold {used}/{capacity}) -- treating the hold as full")
    cargo_hold.close_hold(ctx)
    return deposited
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k stow -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py ControllerAddon/tests/test_shipwreck_salvaging.py
git commit -m "salvaging: stow_phase deposits salvage into the boat's cargo hold

Reads fullness off 943.4/943.5 while the interface is open -- the only place
it is readable. Salvage LEFT BEHIND after Deposit Salvage marks the hold full
whatever those numbers say, since a declared capacity is a ceiling not a
promise. An unreadable reading means 'cannot tell' and never makes the hold
look full or empty. A hold that will not open degrades to dropping."
```

---

### Task 7: `sort_phase`

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py`
- Test: `ControllerAddon/tests/test_shipwreck_salvaging.py`

**Interfaces:**
- Consumes: `cargo_hold` (Task 4), `keep_item` / `drop_items` (Task 3), `sailing.find_facility` (Task 2), `_record_hold` (Task 6). The tests below also reuse the `_FakeHold` class and `_install_hold` helper defined in **Task 6's** test block — if you are executing tasks out of order, add Task 6's test helpers first.
- Produces: `shipwreck_salvaging.SORT_ANIMATIONS`, `SORT_SECONDS_PER_SALVAGE`, `SALVAGING_STATION_IDS`, `SALVAGING_STATION_NAME`, `SORT_ACTION`, `shipwreck_salvaging._inventory_salvage_count()`, `_await_sort_done(ctx, count)`, `shipwreck_salvaging.sort_phase(ctx) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_shipwreck_salvaging.py`:

```python
# --- sort_phase --------------------------------------------------------------------------

def test_sort_animations_are_separate_from_the_hook_channel():
    """Merging them would make the hook's idle-reclick logic treat sorting as hooking."""
    assert 13599 in ss.SORT_ANIMATIONS and 13593 in ss.SORT_ANIMATIONS
    assert not (ss.SORT_ANIMATIONS & ss.SALVAGE_CHANNEL_ANIMATIONS)


def test_sort_rate_is_the_measured_one():
    assert ss.SORT_SECONDS_PER_SALVAGE == 1.79


def _station_obj():
    return {"id": 59701, "name": "Salvaging station", "x": 1, "y": 1, "plane": 0,
            "visible": True, "screenX": 9, "screenY": 9,
            "actions": ["Sort-salvage", "Modify"], "boatViewId": 1036}


def test_sort_disposes_before_withdrawing(monkeypatch):
    """The deadlock: enter sort holding a full inventory of the PREVIOUS batch's output, find
    no salvage to sort, and try to withdraw into an inventory with no free slots."""
    order = []
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage",
                        lambda ctx: order.append("withdraw") or True)
    monkeypatch.setattr(ss, "drop_items",
                        lambda ctx, pred: order.append("dispose") or 1)
    monkeypatch.setattr(sailing, "find_facility", lambda *a, **k: _station_obj())
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    ss.sort_phase(_Ctx())
    assert order[0] == "dispose"
    assert "withdraw" in order


def test_sort_clicks_our_own_stations_sort_salvage(monkeypatch):
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: True)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    seen = {}
    monkeypatch.setattr(sailing, "find_facility",
                        lambda name, **kw: seen.update({"name": name, **kw}) or _station_obj())
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage", "screenX": 1, "screenY": 1}])
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.append(e.get("id")) or True)
    monkeypatch.setattr(ss, "_await_sort_done", lambda ctx, count: True)
    assert ss.sort_phase(_Ctx()) is True
    assert seen["action"] == "Sort-salvage"
    assert clicked == [59701]


def test_sort_stops_when_no_salvage_is_anywhere(monkeypatch):
    _install_hold(monkeypatch, _FakeHold(rows=()))
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: False)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    assert ss.sort_phase(ctx) is False
    assert ctx.state.data["hold_has_salvage"] is False


def test_sort_disposal_respects_the_keep_list(monkeypatch):
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: True)
    monkeypatch.setattr(sailing, "find_facility", lambda *a, **k: _station_obj())
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(ss, "_await_sort_done", lambda ctx, count: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage", "screenX": 1, "screenY": 1}])
    predicates = []
    monkeypatch.setattr(ss, "drop_items",
                        lambda ctx, pred: predicates.append(pred) or 0)
    ss.sort_phase(_Ctx())
    pred = predicates[0]
    assert pred("Gold ring") is True          # not on the default keep-list
    assert pred("Rune scimitar") is False     # on it
    assert pred("Chisel") is False            # hard-keep
    assert pred("Plundered salvage") is False # never dropped by disposal


def test_sort_reports_a_missing_station(monkeypatch):
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: True)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    monkeypatch.setattr(sailing, "find_facility", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage", "screenX": 1, "screenY": 1}])
    assert ss.sort_phase(_Ctx()) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k sort -v`
Expected: FAIL, `AttributeError: module 'shipwreck_salvaging' has no attribute 'SORT_ANIMATIONS'`

- [ ] **Step 3: Add the constants**

In `ControllerAddon/routines/shipwreck_salvaging.py`, immediately after the `SALVAGE_CHANNEL_ANIMATIONS` / `HELM_ANIMATIONS` block, add:

```python
# The on-boat salvaging station (cache locs; port stations are 60462/60463 and behave the same).
SALVAGING_STATION_IDS = frozenset({59699, 59700, 59701})
SALVAGING_STATION_NAME = "salvaging station"
SORT_ACTION = "Sort-salvage"

# The Sort-salvage channel's own animations, DELIBERATELY SEPARATE from
# SALVAGE_CHANNEL_ANIMATIONS -- merging them would make the hook's idle-reclick logic read
# sorting as hooking and never redeploy. 13599 CONFIRMED live 2026-09-07 on a 3x8 boat, despite
# runelite-api naming it ..._KANDARIN_1X3_INTERACT01; 13593 is LARGE01_INTERACT01. RuneLite's
# own IdleNotifier labels both "Sort-salvage".
SORT_ANIMATIONS = frozenset({13599, 13593})

# MEASURED live 2026-09-07: 12 salvage sorted in 19.7s, a steady 3 game ticks each. Matches the
# wiki's "close to 1800 salvages per hour" for the port station. The channel ENDS ITSELF when
# the inventory runs out, so this is a budget, not a stop signal.
SORT_SECONDS_PER_SALVAGE = 1.79
SORT_BUDGET_SLACK_S = 6.0
SORT_POLL_S = 0.4
```

- [ ] **Step 4: Implement `_await_sort_done` and `sort_phase`**

Add above `tick`:

```python
def _inventory_salvage_count():
    return sum(1 for i in controller.get_inventory()
               if (i.get("name") or "").lower().endswith("salvage"))


def _await_sort_done(ctx, count) -> bool:
    """Poll until no salvage is left in the inventory. The channel ends itself, so the budget
    only bounds a channel that never started (a hijacked click, a station out of reach)."""
    deadline = time.time() + count * SORT_SECONDS_PER_SALVAGE + SORT_BUDGET_SLACK_S
    while time.time() < deadline:
        controller.human_delay(SORT_POLL_S, SORT_POLL_S + 0.2)
        controller.dismiss_continue_box(ctx.ser)
        if _inventory_salvage_count() == 0:
            return True
    debug_log.log("salvage", f"sorting {count} salvage did not finish inside its budget")
    return False


def sort_phase(ctx) -> bool:
    """Sort the hold's salvage at our own boat's salvaging station, keeping only what the
    keep-list selects.

    Step 0 -- disposal -- runs FIRST and unconditionally. Without it the loop deadlocks in a
    real, reachable state: enter sort holding an inventory full of the PREVIOUS batch's sorted
    output, find no salvage to sort, and try to withdraw into an inventory with no free slots.
    Disposal is idempotent, so running it every pass costs nothing when there is nothing to
    drop."""
    config = ctx.state.config
    data = ctx.state.data
    keep = config.get("keepItems", [])

    drop_items(ctx, lambda name: not keep_item(name, keep))

    if _inventory_salvage_count() == 0:
        if not cargo_hold.open_hold(ctx):
            return False
        withdrew = cargo_hold.withdraw_salvage(ctx)
        used, capacity = cargo_hold.read_usage()
        _record_hold(data, used, capacity, bool(cargo_hold.salvage_rows()), config)
        cargo_hold.close_hold(ctx)
        if not withdrew:
            debug_log.log("salvage", "no salvage left in the hold -- nothing to sort")
            data["hold_has_salvage"] = False
            return False

    count = _inventory_salvage_count()
    if count == 0:
        return False

    station = sailing.find_facility(SALVAGING_STATION_NAME, action=SORT_ACTION)
    if station is None:
        debug_log.log("salvage", "no salvaging station on our own boat")
        return False

    debug_log.log("salvage", f"sorting {count} salvage (~{count * SORT_SECONDS_PER_SALVAGE:.0f}s)")
    controller.click_entity(ctx.ser, station, offset=(0, 0), simulate_mistakes=False)
    finished = _await_sort_done(ctx, count)
    drop_items(ctx, lambda name: not keep_item(name, keep))
    return finished
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k sort -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py ControllerAddon/tests/test_shipwreck_salvaging.py
git commit -m "salvaging: sort_phase -- withdraw, Sort-salvage, keep-list disposal

Sort-salvage opens NO interface (a widget watcher across a full sort recorded
no new group); it is a plain channel, animation 13599 confirmed on a 3x8 hull,
1.79s per salvage MEASURED, ending itself when the inventory runs dry.

SORT_ANIMATIONS is deliberately separate from SALVAGE_CHANNEL_ANIMATIONS --
merging them would make the hook's idle-reclick logic read sorting as hooking.

Disposal runs FIRST and unconditionally: entering the phase holding a full
inventory of the previous batch's output otherwise deadlocks, with no salvage
to sort and no free slot to withdraw into."
```

---

### Task 8: Wire `tick()` to the phase machine and gate `massWorld`

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py`
- Test: `ControllerAddon/tests/test_shipwreck_salvaging.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `tick()` dispatching on `derive_phase`; `massWorld` suppressing both sail paths.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_shipwreck_salvaging.py`:

```python
# --- massWorld gating --------------------------------------------------------------------

def _explode(*a, **k):
    raise AssertionError("massWorld must never sail")


def test_mass_world_never_sails_to_a_far_wreck(monkeypatch):
    monkeypatch.setattr(sailing, "sail_to", _explode)
    monkeypatch.setattr(ss, "sail_to_pair", _explode)
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    # A far wreck exists and nothing is in hook range.
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3040, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ss.tick(ctx)   # must not raise


def test_mass_world_off_still_sails(monkeypatch):
    called = []
    monkeypatch.setattr(ss, "sail_to_pair", lambda ctx, cfg, w: called.append(True) or False)
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3040, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=False, sortWhenIdle=False, stowSalvage=False)
    ss.tick(ctx)
    assert called == [True]


def test_mass_world_does_not_reposition_when_the_hook_will_not_engage(monkeypatch):
    monkeypatch.setattr(sailing, "sail_to", _explode)
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    deployed = []
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda ctx, name: deployed.append(True) or True)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    # Force the engage timeout to have already expired.
    ctx.state.data["hook_engage_attempt_at"] = 0.0
    ss.tick(ctx)   # must not raise
    assert deployed


def test_tick_dispatches_to_the_sort_phase_when_idle(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    called = []
    monkeypatch.setattr(ss, "sort_phase", lambda ctx: called.append(True) or True)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=True)
    ctx.state.data["hold_has_salvage"] = True
    ss.tick(ctx)
    assert called == [True]


def test_tick_dispatches_to_the_stow_phase_when_the_inventory_fills(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage"}] * 27)
    called = []
    monkeypatch.setattr(ss, "stow_phase", lambda ctx: called.append(True) or True)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ctx.state.data["hold_has_salvage"] = False
    ss.tick(ctx)
    assert called == [True]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py -k "mass_world or dispatches" -v`
Expected: FAIL — `tick` still drops instead of stowing, and calls `sail_to_pair` under `massWorld`.

- [ ] **Step 3: Replace `tick`'s opening dispatch**

In `ControllerAddon/routines/shipwreck_salvaging.py`, replace the inventory-full block at the top of `tick` — from `inventory = controller.get_inventory()` through the `data["last_no_salvage_message_at"] = now` line — with:

```python
    inventory = controller.get_inventory()
    free_slots = controller.INVENTORY_SIZE - len(inventory)
    inventory_salvage = sum(1 for i in inventory
                            if (i.get("name") or "").lower().endswith("salvage"))

    near_exclude = _active_giveup(data, "hook_engage_giveup_key", "hook_engage_giveup_until", now)
    near_wreck = find_wreck_in_hook_range(config, exclude=near_exclude)

    phase = derive_phase(
        config,
        wreck_in_range=near_wreck is not None,
        hold_full=bool(data.get("hold_full")),
        inventory_salvage=inventory_salvage,
        free_slots=free_slots,
        hold_has_salvage=data.get("hold_has_salvage"),
    )
    if phase != data.get("salvage_phase"):
        debug_log.log("salvage", f"phase -> {phase}")
        data["salvage_phase"] = phase

    if phase == "sort":
        sort_phase(ctx)
        return
    if phase == "stow":
        if inventory_salvage:
            stow_phase(ctx)
        elif now - data.get("last_no_salvage_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
            debug_log.log("salvage", "inventory is full of things that are not salvage")
            data["last_no_salvage_message_at"] = now
        return
```

Then delete the now-duplicated `near_exclude` / `near_wreck` lines further down (the block whose comment begins "A wreck this routine already gave up repositioning for") — keep that comment attached to the lines you moved up.

- [ ] **Step 4: Gate the two sail paths on `massWorld`**

Inside the near-wreck branch, wrap the reposition. Replace:

```python
                approach_point = _approach_point_from_here(near_wreck)
                if approach_point is not None:
```

with:

```python
                approach_point = (None if config.get("massWorld", True)
                                  else _approach_point_from_here(near_wreck))
                if config.get("massWorld", True):
                    # massWorld: never move. The reposition BUDGET still counts, so a wreck
                    # whose hook genuinely never engages is still given up for the usual
                    # cooldown -- it just is not approached first.
                    debug_log.log("salvage", "hook isn't engaging -- waiting rather than "
                                              f"repositioning (attempt {reposition_count}/"
                                              f"{HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS})")
                elif approach_point is not None:
```

And immediately after the near-wreck branch's `return` and the three `data.pop(...)` cleanup lines, before `far_exclude = ...`, add:

```python
    if config.get("massWorld", True):
        # An area's wrecks share a spawn pool and one rises as another sinks (wiki, Shipwreck
        # salvaging rev 15290733), so on a busy world waiting beats sailing. derive_phase has
        # already decided this tick is "wait" -- there is nothing else to do.
        if now - data.get("last_waiting_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
            debug_log.log("salvage", "no shipwreck in range -- waiting for a respawn")
            data["last_waiting_message_at"] = now
        return
```

- [ ] **Step 5: Run the full suite**

Run: `cd ControllerAddon && python -m pytest tests/test_shipwreck_salvaging.py tests/test_cargo_hold.py -v`
Expected: PASS, all tests including every pre-existing one.

- [ ] **Step 6: Compile-check every touched file**

Run:
```bash
cd ControllerAddon && python -m py_compile routines/shipwreck_salvaging.py sailing.py cargo_hold.py controller.py && echo OK
```
Expected: `OK`. (`banker.py`-style modules are not imported by any test, so a compile check is not optional — see CLAUDE.md's live-debugging workflow.)

- [ ] **Step 7: Run the whole Python suite for regressions**

Run: `cd ControllerAddon && python -m pytest tests/ -x -q`
Expected: PASS. (The full run takes several minutes — `chinchompa` tests use real sleeps.)

- [ ] **Step 8: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py ControllerAddon/tests/test_shipwreck_salvaging.py
git commit -m "salvaging: tick dispatches on derive_phase; massWorld never sails

massWorld suppresses both sail paths -- the far-wreck branch and the
won't-engage reposition -- while keeping the reposition BUDGET, so a wreck
whose hook never engages is still given up for its cooldown rather than
retried forever. 'No wreck in range' becomes the wait phase, which
sortWhenIdle turns into useful work.

Every other measured behaviour is untouched: HOOK_CLICK_COOLDOWN_S, the
sunk-chat redeploy, the hazard blacklist and the XP watchdog."
```

---

## Live-verification checklist

Not part of the plan's tasks — this is the first live session's worklist, copied from the spec so the executor does not have to open it. Run it after Task 8, on a rebuilt client.

1. `find_facility` returns exactly one station, one hold and our own hooks while parked among other boats.
2. `943.14 Deposit Salvage` leaves the chisel, rune pouch and coins in the inventory.
3. `943.4`/`943.5` are used/capacity and both update after a deposit.
4. `Withdraw-All` on `943.10` fills the inventory and stops at the free-slot count.
5. `SORT_SECONDS_PER_SALVAGE` against a full 28-salvage batch, not the 12 measured.
6. The 229 box is detected on a salvage tier other than Plundered.
7. Whether the hold's grid distinguishes salvage tiers worth sorting in a particular order.
8. Whether a respawn gap actually fits a withdraw-plus-sort cycle, and whether the interrupt returns to the hook fast enough not to lose a wreck.
9. **Log every dropped item name** and diff it against `keepItems` — the wiki's table is known incomplete (it omits `Sailors' amulet (inert)` entirely).
10. Whether the cargo-hold item container (`963..967`) ever populates; if it does, `/trawling` becomes a cheaper fullness read and `deep_sea_trawling.py` needs the same answer.
