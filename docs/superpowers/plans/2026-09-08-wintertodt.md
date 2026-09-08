# Wintertodt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Wintertodt routine that chops Bruma roots, fletches them into kindling, feeds a brazier on a crowded official world, and keeps itself warm with rejuvenation potions made in the arena — never banking and never leaving region 6462.

**Architecture:** Three Python files plus one Java enum entry. `wintertodt_ids.py` holds the cache-derived id and chat tables and no logic. `wintertodt_state.py` holds pure functions over one state snapshot (the whole decision layer, testable with no client, no bridge and no serial port). `routines/wintertodt.py` is the engine-facing routine that clicks and confirms. Every decision is re-derived from live state each tick; nothing load-bearing is stored across ticks.

**Tech Stack:** Python 3.13, pytest. Bridge reads via `controller.get_api("minigames", "wintertodt")`, `controller.get_inventory()`, `controller.get_objects()`, `controller.get_varbit()`. Event waits via `outcome_waits` / `bridge_events`. Java: RuneLite plugin (one enum constant).

**Spec:** `docs/superpowers/specs/2026-09-08-wintertodt-design.md`

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include this section.

- **Region 6462** is the whole world of this routine. It owns no travel, no banking, no loadout.
- **Match braziers and crates by ID, never by name.** Nine objects here share three names: unlit brazier 29312 and broken brazier 29313 are both `Brazier`; six objects are called `Crate` (29316-29320 hand out tools, 29301-29305 are scenery with no ops at all).
- **`isOperational()` and `isRewardEligible()` are Java methods and are NOT in the JSON.** Python re-derives both. Verified against the live response.
- **`warmth` is a raw 0-1000 scale.** Percent is `warmth / 10`.
- **`points` and `energyPercent` read `-1` when the HUD is down**, which is a normal between-rounds state, not an error.
- **500 points is a cliff**: below it the round pays nothing, and leaving or logging out mid-round forfeits every point. So a break must never land mid-round and warmth must never reach zero.
- **Pure means pure:** every function in `wintertodt_state.py` takes plain data and returns plain data. No `controller.` calls, no HTTP, no clock reads. This is what makes the decision layer table-testable.
- **Tests are hermetic.** Stub every bridge read and fake the module clock, following `tests/test_motherlode_mine.py`. A test must never reach the running client.
- **Feed each event from the click that causes it** when scripting `EventStream` tests — queueing a later click's verdict up front delivers it during the earlier click's wait, which cannot happen live.
- Chat-line constants are copied verbatim from `WintertodtPlugin.onChatMessage`, which is the game's own vocabulary.

---

### Task 1: The id and chat tables

**Files:**
- Create: `ControllerAddon/wintertodt_ids.py`
- Test: `ControllerAddon/tests/test_wintertodt_ids.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `REGION`, `HUD_GROUP`, `OBJ_ROOTS`, `OBJ_BRAZIER_UNLIT`, `OBJ_BRAZIER_BROKEN`, `OBJ_BRAZIER_LIT`, `OBJ_SPROUTING_ROOTS`, `OBJ_SNOW`, `BRAZIER_IDS` (frozenset), `CRATE_TOOLS` (dict `id -> (action, item_substring)`), `SCENERY_CRATES` (frozenset), `ITEM_ROOT`, `ITEM_KINDLING`, `ITEM_UNF`, `ITEM_HERB`, `VARBIT_WARMTH`, `VARBIT_ROUND_TIMER`, `VARBIT_AREA_ATTACK`, `CHAT_INVENTORY_FULL`, `CHAT_OUT_OF_ROOTS`, `CHAT_LIT`, `CHAT_FIXED`, `CHAT_BRAZIER_OUT`, `CHAT_DAMAGE` (tuple), `POINTS_LIGHT`, `POINTS_ROOT`, `POINTS_KINDLING`, `POINTS_FIX`, `POINTS_HEAL`, `REWARD_THRESHOLD`.

- [ ] **Step 1: Write the failing test**

Create `ControllerAddon/tests/test_wintertodt_ids.py`:

```python
"""ControllerAddon/wintertodt_ids.py -- the cache-derived tables.

These are pinned because they came from cache dump 5bc2ff1e, not from memory, and because the
name collisions are a live trap: nine objects in this arena share three names.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wintertodt_ids as w


def test_the_two_unlit_and_broken_braziers_share_a_name_and_differ_only_by_action():
    # The shut-vs-open door lesson: matching on name here picks the wrong object.
    assert w.OBJ_BRAZIER_UNLIT == 29312
    assert w.OBJ_BRAZIER_BROKEN == 29313
    assert w.OBJ_BRAZIER_LIT == 29314
    assert w.BRAZIER_IDS == frozenset({29312, 29313, 29314})


def test_every_tool_crate_is_a_distinct_id_with_its_own_action():
    assert set(w.CRATE_TOOLS) == {29316, 29317, 29318, 29319, 29320}
    assert w.CRATE_TOOLS[29316][0] == "Take-hammer"
    assert w.CRATE_TOOLS[29317][0] == "Take-knife"
    assert w.CRATE_TOOLS[29318][0] == "Take-axe"
    assert w.CRATE_TOOLS[29319][0] == "Take-tinderbox"
    assert w.CRATE_TOOLS[29320][0] == "Take-concoction"


def test_scenery_crates_are_disjoint_from_tool_crates():
    # 29301-29305 are named "Crate" too and have no ops at all -- clicking one does nothing.
    assert w.SCENERY_CRATES.isdisjoint(set(w.CRATE_TOOLS))


def test_item_and_point_values_match_runelites_own_plugin():
    assert (w.ITEM_ROOT, w.ITEM_KINDLING) == (20695, 20696)
    assert (w.ITEM_UNF, w.ITEM_HERB) == (20697, 20698)
    assert (w.POINTS_ROOT, w.POINTS_KINDLING) == (10, 25)
    assert w.REWARD_THRESHOLD == 500


def test_chat_constants_are_lowercase_prefixes_for_substring_matching():
    # outcome_waits.chat_has lowercases the incoming message, so these must be lowercase or
    # every match silently fails.
    for line in (w.CHAT_INVENTORY_FULL, w.CHAT_OUT_OF_ROOTS, w.CHAT_LIT, w.CHAT_FIXED,
                 w.CHAT_BRAZIER_OUT) + tuple(w.CHAT_DAMAGE):
        assert line == line.lower(), line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_ids.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wintertodt_ids'`

- [ ] **Step 3: Write the implementation**

Create `ControllerAddon/wintertodt_ids.py`:

```python
"""Wintertodt ids, chat lines and point values.

Every object id came from cache dump `5bc2ff1e` (config/loc), every item id and chat prefix
from RuneLite's own decompiled WintertodtPlugin, and every point value from the wiki's Reward
points table. Nothing here was recalled from memory.

THE NAME COLLISIONS ARE THE POINT OF THIS FILE. Nine objects in the arena share three names:
the unlit brazier (29312) and the broken brazier (29313) are BOTH called "Brazier" and differ
only in their action, and SIX different objects are called "Crate" -- five hand out different
tools, and 29301-29305 are pure scenery with no ops at all. This is the shut-vs-open door
lesson (CLAUDE.md: "a shut door and an open one are both WallObjects named Door ... only the
Open action separates them") applied five ways over. Always match on id.
"""

REGION = 6462
HUD_GROUP = 396                       # InterfaceID.WintStatus, 0x018c

# --- objects (config/loc) -----------------------------------------------------------------
OBJ_ROOTS = 29311                     # "Bruma roots", op1=Chop, 2x2
OBJ_BRAZIER_UNLIT = 29312             # "Brazier",         op1=Light, 3x3
OBJ_BRAZIER_BROKEN = 29313            # "Brazier",         op1=Fix,   3x3
OBJ_BRAZIER_LIT = 29314               # "Burning brazier", op1=Feed,  3x3
OBJ_SPROUTING_ROOTS = 29315           # "Sprouting Roots", op1=Pick
OBJ_SNOW = 29325                      # the 3x3 area-attack warning; active=no, so unclickable

BRAZIER_IDS = frozenset({OBJ_BRAZIER_UNLIT, OBJ_BRAZIER_BROKEN, OBJ_BRAZIER_LIT})

# id -> (menu action, substring identifying the item it hands out)
CRATE_TOOLS = {
    29316: ("Take-hammer", "hammer"),
    29317: ("Take-knife", "knife"),
    29318: ("Take-axe", "axe"),
    29319: ("Take-tinderbox", "tinderbox"),
    29320: ("Take-concoction", "rejuvenation potion (unf)"),
}
# The game's word for the unfinished potion in its menu is "concoction"; 29320 also offers
# "Take-5 concoctions" and "Take-10 concoctions", which is how a restock is one click.
CRATE_TAKE_5 = "Take-5 concoctions"
CRATE_TAKE_10 = "Take-10 concoctions"

SCENERY_CRATES = frozenset({29301, 29302, 29303, 29304, 29305})

REWARD_CART_IDS = frozenset({55411, 55412, 55413, 55414, 55415, 55416})

# --- items --------------------------------------------------------------------------------
ITEM_ROOT = 20695                     # Bruma root,     10 points fed
ITEM_KINDLING = 20696                 # Bruma kindling, 25 points fed
ITEM_UNF = 20697                      # Rejuvenation potion (unf)
ITEM_HERB = 20698                     # Bruma herb

TOOL_NAMES = ("axe", "knife", "tinderbox", "hammer")
POTION_NAME = "rejuvenation potion"

# --- varbits (readable today over GET /varbit?id=, no Java change) -------------------------
VARBIT_WARMTH = 11434
VARBIT_REWARD_POOL = 11435
VARBIT_NEEDS_WARNING = 11436
VARBIT_AREA_ATTACK = 5354
VARBIT_ROUND_TIMER = 7980             # seconds = value * 30 / 50

# --- animations (WintertodtPlugin.onAnimationChanged) --------------------------------------
ANIM_FLETCH = 1248
ANIM_FEED = 832
ANIM_LIGHT = 733
ANIM_FIX = (3676, 8912)

# --- chat, verbatim prefixes from WintertodtPlugin.onChatMessage, lowercased ---------------
# Lowercase because outcome_waits.chat_has lowercases the incoming message before comparing;
# an uppercase constant here would silently never match.
CHAT_INVENTORY_FULL = "your inventory is too full"
CHAT_OUT_OF_ROOTS = "you have run out of bruma roots"
CHAT_LIT = "you light the brazier"
CHAT_FIXED = "you fix the brazier"
CHAT_BRAZIER_OUT = "the brazier has gone out."
CHAT_FLETCHING = "you carefully fletch the root"
CHAT_DAMAGE = (
    "the cold of",
    "the freezing cold attack",
    "the brazier is broken and shrapnel",
)

# --- points (wiki, Reward points) ----------------------------------------------------------
POINTS_LIGHT = 25
POINTS_ROOT = 10
POINTS_KINDLING = 25
POINTS_FIX = 25
POINTS_HEAL = 75                      # out of scope in v1; recorded because it is the largest
REWARD_THRESHOLD = 500                # a CLIFF: below it the round pays nothing at all
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_ids.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/wintertodt_ids.py ControllerAddon/tests/test_wintertodt_ids.py
git commit -m "wintertodt: the cache-derived id, chat and points tables"
```

---

### Task 2: Snapshot normalisation and brazier choice

**Files:**
- Create: `ControllerAddon/wintertodt_state.py`
- Test: `ControllerAddon/tests/test_wintertodt_state.py`

**Interfaces:**
- Consumes: `wintertodt_ids` (Task 1).
- Produces: `warmth_percent(reader) -> float`, `round_active(reader) -> bool`, `brazier_operational(entry) -> bool`, `choose_brazier(reader, current=None, preferred="SOUTH_WEST") -> str | None`, `brazier_status(reader, key) -> str`, `pyromancer_status(reader, key) -> str`, `BRAZIER_KEYS` (tuple).

- [ ] **Step 1: Write the failing test**

Create `ControllerAddon/tests/test_wintertodt_state.py`:

```python
"""ControllerAddon/wintertodt_state.py -- the pure decision layer.

Pure means these tests need no client, no bridge and no serial port: every function takes plain
data and returns plain data. This is where the first live session's corrections will land, so
each fact is pinned to its source in the spec.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wintertodt_state as ws


def reader(**over):
    """A /api/minigames/wintertodt snapshot with everything nominal, overridable per test."""
    base = {
        "atWintertodt": True,
        "hudVisible": True,
        "energyVisible": True,
        "warmthVisible": True,
        "brazierStatusVisible": True,
        "roundActive": True,
        "points": 120,
        "energyPercent": 80,
        "warmth": 1000,
        "respawnDelay": 0,
        "secondsUntilRoundStart": 0,
        "rewardPool": 3,
        "warningNeeded": False,
        "areaAttackActive": False,
        "braziers": {
            key: {"brazier": key, "status": "LIT", "pyromancerStatus": "ALIVE"}
            for key in ws.BRAZIER_KEYS
        },
    }
    braziers = over.pop("braziers", None)
    base.update(over)
    if braziers:
        for key, entry in braziers.items():
            base["braziers"][key] = {"brazier": key, **entry}
    return base


def test_warmth_is_a_0_to_1000_scale_not_a_percentage():
    assert ws.warmth_percent(reader(warmth=1000)) == 100.0
    assert ws.warmth_percent(reader(warmth=455)) == 45.5


def test_warmth_percent_of_an_unreadable_snapshot_is_none_not_zero():
    # Zero would read as "about to die" and fire the emergency rung against a bridge blip.
    assert ws.warmth_percent({}) is None
    assert ws.warmth_percent(None) is None


def test_operational_is_recomputed_in_python_because_gson_omits_the_java_method():
    assert ws.brazier_operational({"status": "LIT", "pyromancerStatus": "ALIVE"}) is True
    assert ws.brazier_operational({"status": "LIT", "pyromancerStatus": "INCAPACITATED"}) is False
    assert ws.brazier_operational({"status": "UNLIT", "pyromancerStatus": "ALIVE"}) is False
    assert ws.brazier_operational(None) is False


def test_choose_brazier_sticks_with_the_one_we_are_already_working():
    snap = reader()
    assert ws.choose_brazier(snap, current="NORTH_EAST") == "NORTH_EAST"


def test_choose_brazier_leaves_a_brazier_whose_pyromancer_is_down():
    # A downed pyromancer means the brazier CANNOT be relit (wiki), and healing is out of scope,
    # so staying put would idle us for the rest of the round.
    snap = reader(braziers={"NORTH_EAST": {"status": "UNLIT",
                                           "pyromancerStatus": "INCAPACITATED"}})
    assert ws.choose_brazier(snap, current="NORTH_EAST") != "NORTH_EAST"


def test_choose_brazier_falls_back_to_the_preferred_one_when_none_is_operational():
    snap = reader(braziers={key: {"status": "BROKEN", "pyromancerStatus": "ALIVE"}
                            for key in ws.BRAZIER_KEYS})
    assert ws.choose_brazier(snap, current=None, preferred="SOUTH_WEST") == "SOUTH_WEST"


def test_choose_brazier_returns_none_when_the_hud_says_nothing():
    assert ws.choose_brazier({"braziers": {}}, current=None) is None


def test_round_active_is_the_readers_own_field_not_our_guess():
    assert ws.round_active(reader(roundActive=True)) is True
    assert ws.round_active(reader(roundActive=False)) is False
    assert ws.round_active({}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wintertodt_state'`

- [ ] **Step 3: Write the implementation**

Create `ControllerAddon/wintertodt_state.py`:

```python
"""Pure decision functions for routines/wintertodt.py.

PURE MEANS PURE: every function here takes plain data and returns plain data -- no controller
calls, no HTTP, no clock. That is what lets the whole decision layer be table-tested with no
client, the way hunllef.StyleTracker and the Gauntlet's craft_plan are, and it is where the
first live session's corrections belong.

Design: docs/superpowers/specs/2026-09-08-wintertodt-design.md
"""

import wintertodt_ids as w

BRAZIER_KEYS = ("SOUTH_WEST", "NORTH_WEST", "NORTH_EAST", "SOUTH_EAST")


def _braziers(reader):
    return ((reader or {}).get("braziers") or {})


def brazier_status(reader, key) -> str:
    return str((_braziers(reader).get(key) or {}).get("status") or "UNKNOWN")


def pyromancer_status(reader, key) -> str:
    return str((_braziers(reader).get(key) or {}).get("pyromancerStatus") or "UNKNOWN")


def brazier_operational(entry) -> bool:
    """The reader's Java isOperational(), recomputed.

    Gson serializes FIELDS, not the reader's helper METHODS, so `operational` is simply absent
    from the JSON -- verified against the live response. Same trap CLAUDE.md records for GOTR's
    round phase."""
    entry = entry or {}
    return (entry.get("status") == "LIT"
            and entry.get("pyromancerStatus") == "ALIVE")


def warmth_percent(reader):
    """Warmth as a percentage, or None when it cannot be read.

    `warmth` is a raw 0-1000 scale. None rather than 0 for an unreadable snapshot is
    load-bearing: 0 means "about to die" and would fire the emergency rung against a bridge
    blip. This is the m8aq readers' own None-is-not-empty rule."""
    if not isinstance(reader, dict):
        return None
    raw = reader.get("warmth")
    if raw is None:
        return None
    return raw / 10.0


def round_active(reader) -> bool:
    return bool((reader or {}).get("roundActive"))


def reward_eligible(reader) -> bool:
    """The reader's Java isRewardEligible(), recomputed -- also absent from the JSON."""
    points = (reader or {}).get("points")
    return points is not None and points >= w.REWARD_THRESHOLD


def choose_brazier(reader, current=None, preferred="SOUTH_WEST"):
    """Which brazier to work, from the HUD alone -- no scene scan.

    Order: the one we are already working (stickiness beats churn), then any operational one,
    then the configured preference. A brazier whose pyromancer is INCAPACITATED cannot be relit
    at all (wiki), and healing is out of scope, so we abandon it rather than idle beside it."""
    braziers = _braziers(reader)
    if not braziers:
        return None
    if current and brazier_operational(braziers.get(current)):
        return current
    if current and pyromancer_status(reader, current) != "INCAPACITATED" \
            and brazier_status(reader, current) in ("UNLIT", "BROKEN"):
        # Ours is down but fixable by us -- keep it rather than walking to another.
        return current
    for key in BRAZIER_KEYS:
        if brazier_operational(braziers.get(key)):
            return key
    for key in BRAZIER_KEYS:
        if pyromancer_status(reader, key) != "INCAPACITATED":
            return preferred if preferred in braziers else key
    return preferred if preferred in braziers else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_state.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/wintertodt_state.py ControllerAddon/tests/test_wintertodt_state.py
git commit -m "wintertodt: snapshot normalisation and brazier choice"
```

---

### Task 3: Kit deficit, warmth policy and what to feed

**Files:**
- Modify: `ControllerAddon/wintertodt_state.py` (append)
- Test: `ControllerAddon/tests/test_wintertodt_state.py` (append)

**Interfaces:**
- Consumes: `wintertodt_ids`, and Task 2's module.
- Produces: `count_item(items, item_id) -> int`, `has_tool(items, name) -> bool`, `potion_doses(items) -> int`, `kit_deficit(items, min_doses) -> list[str]`, `needs_drink(reader, drink_at) -> bool`, `feed_item(items, *, fletch, energy_percent, feed_roots_near_end, feed_roots_below_energy) -> int | None`, `inventory_full(items, size=28) -> bool`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_wintertodt_state.py`:

```python
def inv(*names):
    """An inventory of {'id','name'} dicts. Dose counts ride in the name, as the game shows."""
    out = []
    for n in names:
        if n == "root":
            out.append({"id": ws.w.ITEM_ROOT, "name": "Bruma root"})
        elif n == "kindling":
            out.append({"id": ws.w.ITEM_KINDLING, "name": "Bruma kindling"})
        else:
            out.append({"id": 0, "name": n})
    return out


def test_kit_deficit_names_every_missing_tool():
    assert set(ws.kit_deficit(inv(), min_doses=0)) == {"axe", "knife", "tinderbox", "hammer"}
    full = inv("Bronze axe", "Knife", "Tinderbox", "Hammer")
    assert ws.kit_deficit(full, min_doses=0) == []


def test_potion_doses_counts_doses_not_potions():
    items = inv("Rejuvenation potion(4)", "Rejuvenation potion(2)")
    assert ws.potion_doses(items) == 6


def test_an_undosed_potion_name_counts_as_one_dose():
    assert ws.potion_doses(inv("Rejuvenation potion")) == 1


def test_kit_deficit_reports_potions_when_under_the_minimum():
    items = inv("Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(1)")
    assert ws.kit_deficit(items, min_doses=4) == ["potion"]
    assert ws.kit_deficit(items, min_doses=1) == []


def test_needs_drink_is_false_when_warmth_cannot_be_read():
    # Unreadable must never fire the emergency rung -- see warmth_percent's None contract.
    assert ws.needs_drink({}, drink_at=45) is False
    assert ws.needs_drink(reader(warmth=400), drink_at=45) is True
    assert ws.needs_drink(reader(warmth=600), drink_at=45) is False


def test_feed_item_prefers_kindling():
    items = inv("root", "root", "kindling")
    assert ws.feed_item(items, fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_KINDLING


def test_feed_item_accepts_roots_when_fletching_is_off():
    # THE DEADLOCK GUARD. With fletch=False nothing else will ever spend a root: chopping is
    # blocked by the full inventory and the fletch rung is disabled. feed and fletch must
    # between them accept every state a full inventory can be in.
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=False, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_ROOT


def test_feed_item_accepts_roots_near_the_round_end():
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=10,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_ROOT


def test_feed_item_holds_roots_back_mid_round_when_fletching():
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None


def test_feed_item_returns_none_with_nothing_feedable():
    assert ws.feed_item(inv("Knife"), fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_state.py -v`
Expected: FAIL — `AttributeError: module 'wintertodt_state' has no attribute 'kit_deficit'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/wintertodt_state.py`:

```python
import re

_DOSE_RE = re.compile(r"\((\d)\)\s*$")

INVENTORY_SIZE = 28


def count_item(items, item_id) -> int:
    return sum(1 for i in (items or []) if i.get("id") == item_id)


def has_tool(items, name) -> bool:
    """Substring match on the item NAME, the banker.py keepItems convention.

    Deliberately loose: "axe" also matches a Dragon axe or a battleaxe, and any of those chops
    bruma roots. The arena crate hands out a Bronze axe, so the exact name is not knowable
    offline anyway."""
    needle = name.lower()
    return any(needle in (i.get("name") or "").lower() for i in (items or []))


def potion_doses(items) -> int:
    """Total rejuvenation DOSES held, not potions.

    Each potion holds 4 and each dose restores 30% warmth, so doses are the unit that matters.
    The game writes the dose count into the name as "Rejuvenation potion(3)"; a name with no
    suffix counts as one, which is the safe direction (it under-counts, so we restock early)."""
    total = 0
    for item in (items or []):
        name = (item.get("name") or "").lower()
        if w.POTION_NAME not in name or "(unf)" in name:
            continue
        match = _DOSE_RE.search(name)
        total += int(match.group(1)) if match else 1
    return total


def kit_deficit(items, min_doses) -> list:
    """Everything missing from the kit, as names the routine can act on.

    Ordered tools-first so the axe (which gates the whole loop) is fetched before potions."""
    missing = [tool for tool in w.TOOL_NAMES if not has_tool(items, tool)]
    if potion_doses(items) < min_doses:
        missing.append("potion")
    return missing


def needs_drink(reader, drink_at) -> bool:
    """True only when warmth is READABLE and below the threshold.

    Unreadable answers False: warmth_percent returns None there, and treating None as 0 would
    fire the emergency rung on a bridge blip."""
    percent = warmth_percent(reader)
    return percent is not None and percent < drink_at


def inventory_full(items, size=INVENTORY_SIZE) -> bool:
    return len(items or []) >= size


def feed_item(items, *, fletch, energy_percent, feed_roots_near_end, feed_roots_below_energy):
    """Which item id to feed the brazier, or None when nothing should be fed yet.

    THE DEADLOCK GUARD LIVES HERE, and it is the invariant the spec calls out: `feed` and
    `fletch` must between them accept every state a full inventory can be in. A feed rung that
    accepted only kindling, paired with fletch=False, would strand a full inventory of roots --
    chopping blocked by the full inventory, fletching disabled, and nothing willing to spend
    them. So roots are feedable whenever fletching will not happen: either it is switched off,
    or the round is too near its end to be worth the fletch (a root is 10 points, and 10 beats
    the 0 an unfed root is worth when the round ends)."""
    if count_item(items, w.ITEM_KINDLING):
        return w.ITEM_KINDLING
    if not count_item(items, w.ITEM_ROOT):
        return None
    if not fletch:
        return w.ITEM_ROOT
    near_end = (feed_roots_near_end
                and energy_percent is not None
                and 0 <= energy_percent <= feed_roots_below_energy)
    return w.ITEM_ROOT if near_end else None
```

Note the `import re` and `INVENTORY_SIZE` belong at the top of the file with the other imports; move them there rather than leaving them mid-file.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_state.py -v`
Expected: PASS, 18 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/wintertodt_state.py ControllerAddon/tests/test_wintertodt_state.py
git commit -m "wintertodt: kit deficit, warmth policy and the feed/fletch deadlock guard"
```

---

### Task 4: `derive_phase` — the ladder

**Files:**
- Modify: `ControllerAddon/wintertodt_state.py` (append)
- Test: `ControllerAddon/tests/test_wintertodt_state.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 2-3.
- Produces: `derive_phase(*, reader, items, config, brazier, snow_on_tile, round_over_streak) -> str` returning one of `"stop" | "kit" | "warmth" | "dodge" | "light" | "fix" | "feed" | "fletch" | "chop" | "between_rounds"`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_wintertodt_state.py`:

```python
CFG = {
    "preferredBrazier": "SOUTH_WEST",
    "drinkAtWarmth": 45,
    "minDoses": 4,
    "takeConcoctions": 10,
    "fletch": True,
    "feedRootsNearRoundEnd": True,
    "feedRootsBelowEnergy": 15,
    "dodgeSnowfall": True,
    "roundEndReads": 3,
}

KIT = ["Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(4)"]


def phase(**over):
    args = {
        "reader": over.pop("reader", reader()),
        "items": over.pop("items", inv(*KIT)),
        "config": over.pop("config", CFG),
        "brazier": over.pop("brazier", "SOUTH_WEST"),
        "snow_on_tile": over.pop("snow_on_tile", False),
        "round_over_streak": over.pop("round_over_streak", 0),
    }
    args.update(over)
    return ws.derive_phase(**args)


def test_outside_the_arena_is_a_stop_because_this_routine_owns_no_travel():
    assert phase(reader=reader(atWintertodt=False)) == "stop"


def test_warmth_outranks_a_full_inventory_and_a_dead_brazier():
    # 0% warmth is instant death and forfeits the whole round's points, so nothing outranks it
    # except restocking the potion it needs.
    cold = reader(warmth=200, braziers={"SOUTH_WEST": {"status": "UNLIT",
                                                       "pyromancerStatus": "ALIVE"}})
    assert phase(reader=cold, items=inv(*KIT, *(["root"] * 23))) == "warmth"


def test_kit_outranks_warmth_when_there_is_no_potion_to_drink():
    cold = reader(warmth=200)
    assert phase(reader=cold, items=inv("Bronze axe", "Knife", "Tinderbox", "Hammer")) == "kit"


def test_dodge_beats_working_but_only_when_enabled():
    assert phase(snow_on_tile=True) == "dodge"
    cfg = dict(CFG, dodgeSnowfall=False)
    assert phase(snow_on_tile=True, config=cfg) != "dodge"


def test_an_unlit_brazier_is_relit_even_mid_inventory():
    # Wiki: "The brazier should be relit immediately any time it is extinguished while you are
    # nearby ... even if it interrupts cutting or fletching."
    snap = reader(braziers={"SOUTH_WEST": {"status": "UNLIT", "pyromancerStatus": "ALIVE"}})
    assert phase(reader=snap, items=inv(*KIT, *(["root"] * 23))) == "light"


def test_an_unlit_brazier_with_a_downed_pyromancer_is_not_relightable():
    snap = reader(braziers={"SOUTH_WEST": {"status": "UNLIT",
                                           "pyromancerStatus": "INCAPACITATED"}})
    assert phase(reader=snap, items=inv(*KIT)) != "light"


def test_a_broken_brazier_is_fixed():
    snap = reader(braziers={"SOUTH_WEST": {"status": "BROKEN", "pyromancerStatus": "ALIVE"}})
    assert phase(reader=snap) == "fix"


def test_holding_kindling_feeds():
    assert phase(items=inv(*KIT, "kindling")) == "feed"


def test_a_full_inventory_of_roots_fletches():
    assert phase(items=inv(*KIT, *(["root"] * 23))) == "fletch"


def test_a_full_inventory_of_roots_feeds_when_fletching_is_off():
    # The deadlock guard again, this time through the ladder rather than feed_item alone.
    cfg = dict(CFG, fletch=False)
    assert phase(items=inv(*KIT, *(["root"] * 23)), config=cfg) == "feed"


def test_an_empty_inventory_chops():
    assert phase(items=inv(*KIT)) == "chop"


def test_the_round_end_test_is_sustained_not_a_single_read():
    # The transitional-snapshot family: a teleport/HUD blip reads exactly like a round end for
    # a tick or two. GOTR paid for this six separate times.
    over = reader(roundActive=False, points=-1, energyPercent=-1)
    assert phase(reader=over, round_over_streak=1) != "between_rounds"
    assert phase(reader=over, round_over_streak=3) == "between_rounds"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_state.py -v`
Expected: FAIL — `AttributeError: module 'wintertodt_state' has no attribute 'derive_phase'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/wintertodt_state.py`:

```python
def derive_phase(*, reader, items, config, brazier, snow_on_tile, round_over_streak) -> str:
    """The ladder, as a pure function of live state. Never reads a stored phase.

    Order is the design, and each rung's position has a reason:

      stop      -- we are not in the arena and this routine owns no travel.
      kit       -- a missing axe idles the whole loop, and a missing potion disarms `warmth`.
                   Runs mid-round as well as between rounds for exactly that reason.
      warmth    -- 0% is instant death and forfeits every point of the round. Outranks all
                   work below it. Only `kit` beats it, and only because you cannot drink a
                   potion you do not have.
      dodge     -- cheap, and the 3x3 attack is the biggest single warmth hit.
      light/fix -- 25 points each, and the wiki says relighting is worth interrupting anything.
      feed      -- spend what we hold.
      fletch    -- turn a full inventory of roots into kindling.
      chop      -- the default during a round.

    `round_over_streak` is a SUSTAINED count of consecutive inactive reads, not a boolean: a
    single transitional snapshot (HUD down, points -1) looks exactly like a round end and is
    the failure family that cost GOTR six live bugs."""
    if not (reader or {}).get("atWintertodt"):
        return "stop"

    deficit = kit_deficit(items, config.get("minDoses", 4))
    cold = needs_drink(reader, config.get("drinkAtWarmth", 45))

    # Kit first only when it would otherwise disarm the warmth rung, or when a tool is missing.
    if deficit and (not cold or "potion" in deficit):
        return "kit"
    if cold:
        return "warmth"

    if snow_on_tile and config.get("dodgeSnowfall", True):
        return "dodge"

    if not round_active(reader):
        if round_over_streak >= config.get("roundEndReads", 3):
            return "between_rounds"
        return "chop"          # ride out a transitional read rather than acting on it

    if brazier:
        status = brazier_status(reader, brazier)
        alive = pyromancer_status(reader, brazier) != "INCAPACITATED"
        if status == "BROKEN" and alive:
            return "fix"
        if status == "UNLIT" and alive:
            return "light"

    feed = feed_item(
        items,
        fletch=config.get("fletch", True),
        energy_percent=(reader or {}).get("energyPercent"),
        feed_roots_near_end=config.get("feedRootsNearRoundEnd", True),
        feed_roots_below_energy=config.get("feedRootsBelowEnergy", 15),
    )
    if feed is not None and brazier_status(reader, brazier) == "LIT":
        return "feed"

    if config.get("fletch", True) and count_item(items, w.ITEM_ROOT) and inventory_full(items):
        return "fletch"

    return "chop"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt_state.py -v`
Expected: PASS, 30 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/wintertodt_state.py ControllerAddon/tests/test_wintertodt_state.py
git commit -m "wintertodt: derive_phase, the ladder as a pure function"
```

---

### Task 5: Routine scaffold — config, clock, live reads

**Files:**
- Create: `ControllerAddon/routines/wintertodt.py`
- Test: `ControllerAddon/tests/test_wintertodt.py`

**Interfaces:**
- Consumes: `wintertodt_ids`, `wintertodt_state`, `controller`, `outcome_waits`.
- Produces: `DEFAULT_CONFIG`, `VARBIT_SUBSCRIPTIONS`, `get_config(bridge_config)`, `_now()`, `_sleep(s)`, `_reader()`, `_inventory()`, `_objects(ids=None, action=None, fresh=False)`, `_nearest(objs)`, `_refresh(obj)`, `_d(ctx)`, `snapshot(ctx)`, `setup(ctx)`, `on_event(ctx, event_type, payload)`.

- [ ] **Step 1: Write the failing test**

Create `ControllerAddon/tests/test_wintertodt.py`:

```python
"""routines/wintertodt.py.

Hermetic: every bridge read is stubbed and the module clock is faked, so a budget that would
wait seconds live expires instantly (the test_blast_furnace / test_motherlode_mine pattern).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import wintertodt as wt
import wintertodt_ids as w


class _State:
    def __init__(self, config=None):
        self.config = dict(config or wt.DEFAULT_CONFIG)
        self.data = {}
        self.safety_stopped = False
        self.stop_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.stop_reason = reason


class _Ctx:
    def __init__(self, config=None):
        self.state = _State(config)
        self.ser = object()
        self.events = None


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    """No test may reach the running client, and no test may really sleep."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(wt, "_now", lambda: clock["t"])
    monkeypatch.setattr(wt, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(controller, "get_api", lambda *a, **k: {})
    monkeypatch.setattr(controller, "get_inventory", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 1, "y": 2, "plane": 0})
    monkeypatch.setattr(controller, "get_varbit", lambda *a, **k: 0)
    return clock


def test_default_config_carries_every_key_derive_phase_reads():
    for key in ("preferredBrazier", "drinkAtWarmth", "minDoses", "takeConcoctions",
                "fletch", "feedRootsNearRoundEnd", "feedRootsBelowEnergy", "dodgeSnowfall",
                "roundEndReads"):
        assert key in wt.DEFAULT_CONFIG, key


def test_get_config_ignores_the_bridge_because_there_is_no_config_section():
    assert wt.get_config({"anything": 1}) == wt.DEFAULT_CONFIG


def test_varbit_subscriptions_cover_warmth_and_the_round_timer():
    assert w.VARBIT_WARMTH in wt.VARBIT_SUBSCRIPTIONS
    assert w.VARBIT_ROUND_TIMER in wt.VARBIT_SUBSCRIPTIONS


def test_objects_filters_by_id_and_action_never_by_name(monkeypatch):
    # The name collision guard: an unlit brazier and a broken one are both "Brazier".
    scene = [
        {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"], "distance": 3},
        {"id": w.OBJ_BRAZIER_BROKEN, "name": "Brazier", "actions": ["Fix"], "distance": 1},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    found = wt._objects(ids={w.OBJ_BRAZIER_UNLIT})
    assert [o["id"] for o in found] == [w.OBJ_BRAZIER_UNLIT]


def test_nearest_picks_the_smallest_distance():
    objs = [{"distance": 7}, {"distance": 2}, {"distance": 5}]
    assert wt._nearest(objs)["distance"] == 2
    assert wt._nearest([]) is None


def test_on_event_records_a_snowfall_spawn_on_our_own_tile(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 10, "y": 20, "plane": 0})
    wt.on_event(ctx, "OBJECT_SPAWNED", {"id": w.OBJ_SNOW, "x": 10, "y": 20, "plane": 0})
    assert wt._snow_on_tile(ctx) is True


def test_on_event_ignores_a_snowfall_spawn_somewhere_else(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 10, "y": 20, "plane": 0})
    wt.on_event(ctx, "OBJECT_SPAWNED", {"id": w.OBJ_SNOW, "x": 99, "y": 99, "plane": 0})
    assert wt._snow_on_tile(ctx) is False


def test_a_recorded_snowfall_expires_so_a_missed_despawn_cannot_pin_the_dodge_rung(hermetic):
    ctx = _Ctx()
    ctx.state.data["wt_snow_tile"] = (10, 20, 0)
    ctx.state.data["wt_snow_at"] = wt._now()
    hermetic["t"] += wt.SNOW_MEMORY_S + 1
    assert wt._snow_on_tile(ctx) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wintertodt'`

- [ ] **Step 3: Write the implementation**

Create `ControllerAddon/routines/wintertodt.py`:

```python
"""Wintertodt -- chop, fletch to kindling, feed, on a group/official world.

Design: docs/superpowers/specs/2026-09-08-wintertodt-design.md
NOT YET RUN LIVE. Every budget below is a first-draft guess; the spec's live-verification
checklist is the first session's worklist.

Three facts shape this routine:

1. Every tool is FREE from crates in the arena (axe, knife, tinderbox, hammer, concoctions),
   and USER chose never to search the Reward Cart, so there is NO banking and NO loadout. The
   routine never leaves region 6462.
2. Chopping, fletching and feeding are CONTINUOUS actions that run until the game interrupts
   them with a chat line. One inventory is about three clicks, so this is mostly an interrupt
   handler rather than a click loop.
3. Fletching is a deliberate POINTS-over-XP trade (USER's choice). The wiki is explicit that
   fletching is SLOWER Firemaking XP; it buys reward-cart rolls. A low FM/hr is the design, not
   a bug.

The decision layer is wintertodt_state.py and is pure; this file does the clicking.
"""

import time

import controller
import outcome_waits
import wintertodt_ids as w
import wintertodt_state as ws
from debug_log import log

VARBIT_SUBSCRIPTIONS = [w.VARBIT_WARMTH, w.VARBIT_ROUND_TIMER, w.VARBIT_AREA_ATTACK]

CLICK_RANGE = 10                 # walk first, click close (motherlode's measured rule)
SNOW_MEMORY_S = 3.0              # how long a recorded snowfall tile stays believed

DEFAULT_CONFIG = {
    "preferredBrazier": "SOUTH_WEST",     # the conventional group brazier -- UNVERIFIED
    "drinkAtWarmth": 45,                  # a dose restores 30%, so 45 cannot overheal
    "minDoses": 4,                        # one potion in reserve
    "takeConcoctions": 10,                # one Take-10 click per restock
    "fletch": True,                       # USER's choice; False = the faster-XP roots method
    "feedRootsNearRoundEnd": True,
    "feedRootsBelowEnergy": 15,           # what "nearly over" means -- a guess, fit it live
    "dodgeSnowfall": True,
    "roundEndReads": 3,                   # SUSTAINED: one inactive read is transitional
    "safety": {"stopOnInventoryFull": False},   # a full inventory is normal here, not a fault
}


def get_config(bridge_config):
    """No ControllerConfig section: this routine reads nothing from the bridge's /config.

    Same pattern as gemstone_crab.py and mining.py, which CLAUDE.md says needs nothing beyond
    the RoutineChoice enum entry and the ROUTINE_FILES key."""
    return DEFAULT_CONFIG


# --- module clock (the fake-clock fixture patches these) ----------------------------------

def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


# --- live reads ---------------------------------------------------------------------------

def _reader():
    """GET /api/minigames/wintertodt -- the whole HUD in one read, no client rebuild needed."""
    return controller.get_api("minigames", "wintertodt") or {}


def _inventory():
    return controller.get_inventory() or []


def _objects(ids=None, action=None, fresh=False):
    """Scene objects filtered by ID and action -- NEVER by name.

    Nine objects in this arena share three names (both the unlit and broken braziers are
    "Brazier"; six things are called "Crate"). Matching on name picks the wrong one."""
    out = []
    for obj in (controller.get_objects(fresh=fresh) or []):
        if ids is not None and obj.get("id") not in ids:
            continue
        if action is not None:
            actions = [a for a in (obj.get("actions") or []) if a]
            if not any(action.lower() == str(a).lower() for a in actions):
                continue
        out.append(obj)
    return out


def _nearest(objs):
    objs = [o for o in (objs or []) if o is not None]
    if not objs:
        return None
    return min(objs, key=lambda o: o.get("distance") if o.get("distance") is not None else 1e9)


def _refresh(obj):
    """A refresh_target so a click re-aims while the player walks -- CLAUDE.md's standing rule
    that a click's DEPARTURE is aimed too, not only its landing."""
    if not obj:
        return None
    return controller.object_refresh_target(obj.get("x"), obj.get("y"),
                                            obj.get("plane", 0), obj.get("id"))


def _walk_budget(obj):
    return outcome_waits.walk_budget(obj)


def _player_tile():
    player = controller.get_player() or {}
    return (player.get("x"), player.get("y"), player.get("plane", 0))


def _d(ctx):
    return ctx.state.data


# --- snowfall -----------------------------------------------------------------------------

def _snow_on_tile(ctx) -> bool:
    """Whether a 29325 area-attack warning is standing on us right now.

    An ACCELERATOR, never a source of truth: if 29325 never reaches us (checklist item 1) this
    simply stays False and warmth is healed reactively instead. The memory expires so a missed
    despawn cannot pin the dodge rung forever."""
    data = _d(ctx)
    tile = data.get("wt_snow_tile")
    if not tile:
        return False
    if _now() - float(data.get("wt_snow_at") or 0) > SNOW_MEMORY_S:
        data.pop("wt_snow_tile", None)
        return False
    return tuple(tile) == _player_tile()


def on_event(ctx, event_type, payload):
    payload = payload or {}
    if event_type == "OBJECT_SPAWNED" and payload.get("id") == w.OBJ_SNOW:
        tile = (payload.get("x"), payload.get("y"), payload.get("plane", 0))
        if tile == _player_tile():
            _d(ctx)["wt_snow_tile"] = tile
            _d(ctx)["wt_snow_at"] = _now()
    elif event_type == "OBJECT_DESPAWNED" and payload.get("id") == w.OBJ_SNOW:
        _d(ctx).pop("wt_snow_tile", None)


# --- snapshot -----------------------------------------------------------------------------

def snapshot(ctx):
    """One reader read plus one inventory read, and the brazier chosen from them."""
    reader = _reader()
    items = _inventory()
    data = _d(ctx)
    config = ctx.state.config or DEFAULT_CONFIG
    brazier = ws.choose_brazier(reader, current=data.get("wt_brazier"),
                                preferred=config.get("preferredBrazier", "SOUTH_WEST"))
    if brazier:
        data["wt_brazier"] = brazier
    if ws.round_active(reader):
        data["wt_round_over_streak"] = 0
    else:
        data["wt_round_over_streak"] = int(data.get("wt_round_over_streak") or 0) + 1
    return reader, items, brazier


def setup(ctx):
    log("wintertodt", "setup: group-world fletch loop, no banking, region %d" % w.REGION)
    _d(ctx).clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/wintertodt.py ControllerAddon/tests/test_wintertodt.py
git commit -m "wintertodt: routine scaffold -- config, clock, live reads, snowfall memory"
```

---

### Task 6: The `kit` and `warmth` rungs

**Files:**
- Modify: `ControllerAddon/routines/wintertodt.py` (append)
- Test: `ControllerAddon/tests/test_wintertodt.py` (append)

**Interfaces:**
- Consumes: Task 5's module.
- Produces: `_click_object(ctx, obj, action, budget_s, confirm, signals=None) -> bool`, `kit_phase(ctx, items) -> bool`, `warmth_phase(ctx, items) -> bool`, `KIT_MAX_TAKES`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_wintertodt.py`:

```python
def test_kit_phase_clicks_the_crate_whose_id_hands_out_the_missing_tool(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": cid, "name": "Crate", "actions": [act], "distance": 2,
              "x": 1, "y": 1, "plane": 0}
             for cid, (act, _item) in w.CRATE_TOOLS.items()]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    clicked = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        clicked["id"] = obj["id"]
        clicked["action"] = action
        return True

    monkeypatch.setattr(wt, "_click_object", fake_click)
    # Everything present except the knife.
    items = [{"id": 0, "name": n} for n in
             ("Bronze axe", "Tinderbox", "Hammer", "Rejuvenation potion(4)")]
    assert wt.kit_phase(ctx, items) is True
    assert clicked["id"] == 29317
    assert clicked["action"] == "Take-knife"


def test_kit_phase_takes_concoctions_in_bulk(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": 29320, "name": "Crate", "actions": [w.CRATE_TAKE_10], "distance": 2,
              "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        seen["action"] = action
        return True                      # a real True: kit_phase's result is asserted `is True`

    monkeypatch.setattr(wt, "_click_object", fake_click)
    items = [{"id": 0, "name": n} for n in ("Bronze axe", "Knife", "Tinderbox", "Hammer")]
    assert wt.kit_phase(ctx, items) is True
    assert seen["action"] == w.CRATE_TAKE_10


def test_kit_phase_stops_the_run_after_too_many_fruitless_takes(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    for _ in range(wt.KIT_MAX_TAKES + 1):
        wt.kit_phase(ctx, [])
    assert ctx.state.safety_stopped is True
    assert "kit" in ctx.state.stop_reason.lower()


def test_warmth_phase_drinks_a_dose_and_confirms_on_warmth_rising(monkeypatch):
    ctx = _Ctx()
    calls = {"n": 0}
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(wt, "_click_inventory_item",
                        lambda c, item, action, budget_s, confirm: calls.__setitem__("n", 1) or True)
    items = [{"id": 0, "name": "Rejuvenation potion(4)", "slot": 3}]
    assert wt.warmth_phase(ctx, items) is True
    assert calls["n"] == 1


def test_warmth_phase_with_no_potion_is_a_safety_stop_not_a_silent_loop(monkeypatch):
    ctx = _Ctx()
    assert wt.warmth_phase(ctx, []) is False
    assert ctx.state.safety_stopped is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: FAIL — `AttributeError: module 'wintertodt' has no attribute 'kit_phase'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/wintertodt.py`:

```python
KIT_MAX_TAKES = 12               # a ceiling, because a cooldown only sets the RATE of a loop
DRINK_BUDGET_S = 3.0
TAKE_BUDGET_S = 4.0


def _click_object(ctx, obj, action, budget_s, confirm, signals=None):
    """Click a world object and confirm it on a real outcome.

    NO expected_action hover gate. /hover reports what is under the cursor RIGHT NOW, and these
    clicks are issued while the player is still walking up to the target -- the gate withholds
    every one of them. That trap cost the Gauntlet ~8s per node and the Motherlode routine every
    single ladder click. The outcome confirm is what proves the click landed."""
    if not obj:
        return False
    if _too_far(obj):
        return _walk_to(ctx, obj)
    result = controller.click_entity(ctx.ser, obj, refresh_target=_refresh(obj))
    if not result:
        log("wintertodt", "click %s on %d was refused or withheld" % (action, obj.get("id")))
        return False
    outcome = outcome_waits.await_pre(
        ctx, signals or [], budget_s + _walk_budget(obj), confirm,
        now_fn=_now, sleep_fn=_sleep)
    return bool(outcome and outcome.ok)


def _too_far(obj) -> bool:
    """WALK FIRST, CLICK CLOSE -- and onScreen is the load-bearing half.

    Motherlode MEASURED a click aimed at a projection 21 tiles away with onScreen false: it did
    nothing at all and /last-action still showed the previous action."""
    if obj.get("onScreen") is False:
        return True
    distance = obj.get("distance")
    return distance is not None and distance > CLICK_RANGE


def _walk_to(ctx, obj) -> bool:
    import travel
    return bool(travel.walk_to(ctx, obj.get("x"), obj.get("y"), obj.get("plane", 0)))


def _click_inventory_item(ctx, item, action, budget_s, confirm):
    """An inventory click. Never simulates a mistake and never checks UI blockers.

    banker.py's interface-click rule: a misclick plus its corrective reclick would spend the
    NEIGHBOURING slot, and here that neighbour is a bruma root or the knife.

    An item dict from controller.get_inventory() already carries screenX/screenY, so it IS the
    entity -- there is no separate point lookup. offset=(0,0) because the default (-7,+7) nudge
    is enough to slide off a 36px inventory cell. This is the shape corrupted_gauntlet.use_on
    uses."""
    controller.open_inventory_tab(ctx.ser)
    result = controller.click_entity(ctx.ser, item, offset=(0, 0), check_ui_blockers=False,
                                     simulate_mistakes=False)
    if not result:
        return False
    outcome = outcome_waits.await_pre(ctx, ["INVENTORY_CHANGED"], budget_s, confirm,
                                      now_fn=_now, sleep_fn=_sleep)
    return bool(outcome and outcome.ok)


def _use_on(ctx, item_a, item_b) -> bool:
    """Use item A on item B: two interface clicks, the first carrying the "Use" hover gate.

    Copied from corrupted_gauntlet.use_on -- there is NO controller-level use_on helper, and
    inventing one mid-task is how two routines end up with different semantics."""
    if not item_a or not item_b:
        return False
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, item_a, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False, expected_action="Use")
    controller.click_entity(ctx.ser, item_b, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False)
    return True


def kit_phase(ctx, items) -> bool:
    """Restock whatever the kit is missing, from the arena crates.

    There is no bank trip anywhere in this routine: axe, knife, tinderbox, hammer and the
    unfinished potions are all free from crates a few tiles away."""
    data = _d(ctx)
    config = ctx.state.config or DEFAULT_CONFIG
    missing = ws.kit_deficit(items, config.get("minDoses", 4))
    if not missing:
        data["wt_kit_takes"] = 0
        return True

    data["wt_kit_takes"] = int(data.get("wt_kit_takes") or 0) + 1
    if data["wt_kit_takes"] > KIT_MAX_TAKES:
        ctx.state.trigger_safety_stop(
            "wintertodt: could not restock the kit (%s) after %d attempts"
            % (", ".join(missing), KIT_MAX_TAKES))
        return False

    want = missing[0]
    if want == "potion":
        return _take_concoctions(ctx, items)

    for crate_id, (action, item_needle) in w.CRATE_TOOLS.items():
        if item_needle != want:
            continue
        crate = _nearest(_objects(ids={crate_id}))
        if not crate:
            log("wintertodt", "kit: no crate %d in view for %s" % (crate_id, want))
            return False
        before = len(items)
        return _click_object(ctx, crate, action, TAKE_BUDGET_S,
                             lambda: len(_inventory()) > before,
                             signals=["INVENTORY_CHANGED"])
    return False


def _take_concoctions(ctx, items) -> bool:
    """Take unfinished potions in bulk, then brew them with a bruma herb.

    Herblore 75 on this account means Druidic Ritual is complete, so the Brew'ma NPC fallback
    (which mixes for players without the quest) is not needed."""
    config = ctx.state.config or DEFAULT_CONFIG
    crate = _nearest(_objects(ids={29320}))
    if crate:
        want = int(config.get("takeConcoctions", 10))
        action = w.CRATE_TAKE_10 if want >= 10 else (
            w.CRATE_TAKE_5 if want >= 5 else "Take-concoction")
        available = [str(a) for a in (crate.get("actions") or []) if a]
        if not any(action.lower() == a.lower() for a in available):
            action = "Take-concoction"
        before = ws.count_item(items, w.ITEM_UNF)
        if _click_object(ctx, crate, action, TAKE_BUDGET_S,
                         lambda: ws.count_item(_inventory(), w.ITEM_UNF) > before,
                         signals=["INVENTORY_CHANGED"]):
            return True

    if ws.count_item(items, w.ITEM_UNF) and not ws.count_item(items, w.ITEM_HERB):
        roots = _nearest(_objects(ids={w.OBJ_SPROUTING_ROOTS}))
        before = ws.count_item(items, w.ITEM_HERB)
        return _click_object(ctx, roots, "Pick", TAKE_BUDGET_S,
                             lambda: ws.count_item(_inventory(), w.ITEM_HERB) > before,
                             signals=["INVENTORY_CHANGED"])

    if ws.count_item(items, w.ITEM_UNF) and ws.count_item(items, w.ITEM_HERB):
        before = ws.potion_doses(items)
        herb = next((i for i in items if i.get("id") == w.ITEM_HERB), None)
        unf = next((i for i in items if i.get("id") == w.ITEM_UNF), None)
        if _use_on(ctx, herb, unf):
            outcome = outcome_waits.await_pre(
                ctx, ["INVENTORY_CHANGED"], 4.0,
                lambda: ws.potion_doses(_inventory()) > before,
                now_fn=_now, sleep_fn=_sleep)
            return bool(outcome and outcome.ok)
    return False


def warmth_phase(ctx, items) -> bool:
    """Drink one dose. Outranks all work below it: 0% warmth is instant death and forfeits
    every point of the round."""
    dose = next((i for i in items
                 if w.POTION_NAME in (i.get("name") or "").lower()
                 and "(unf)" not in (i.get("name") or "").lower()), None)
    if not dose:
        ctx.state.trigger_safety_stop(
            "wintertodt: warmth is low and there is no rejuvenation potion to drink")
        return False
    before = ws.warmth_percent(_reader()) or 0.0
    return _click_inventory_item(
        ctx, dose, "Drink", DRINK_BUDGET_S,
        lambda: (ws.warmth_percent(_reader()) or 0.0) > before + 5)
```

Note, already verified against `controller.py` — do not "fix" these back:
- There is **no** `controller.get_inventory_item_point`. An inventory item dict from `controller.get_inventory()` already carries `screenX`/`screenY`, so it is passed to `click_entity` directly.
- There is **no** `controller.use_on`. The only `use_on` in the repo is routine-local (`routines/corrupted_gauntlet.py:1242`), so `_use_on` above is a local copy of that shape rather than a shared helper. Extracting one shared helper across the two routines is a reasonable follow-up, but not mid-task.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/wintertodt.py ControllerAddon/tests/test_wintertodt.py
git commit -m "wintertodt: the kit and warmth rungs, both ceilinged"
```

---

### Task 7: The `chop`, `fletch` and `feed` rungs

**Files:**
- Modify: `ControllerAddon/routines/wintertodt.py` (append)
- Test: `ControllerAddon/tests/test_wintertodt.py` (append)

**Interfaces:**
- Consumes: Tasks 5-6.
- Produces: `chop_phase(ctx, items) -> bool`, `fletch_phase(ctx, items) -> bool`, `feed_phase(ctx, reader, items, brazier) -> bool`, `CHOP_BUDGET_S`, `FLETCH_BUDGET_S`, `FEED_BUDGET_S`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_wintertodt.py`:

```python
def test_chop_phase_confirms_on_the_games_own_inventory_full_line(monkeypatch):
    ctx = _Ctx()
    roots = {"id": w.OBJ_ROOTS, "name": "Bruma roots", "actions": ["Chop"],
             "distance": 2, "x": 5, "y": 5, "plane": 0}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [roots])
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        seen["signals"] = signals
        seen["action"] = action
        return True

    monkeypatch.setattr(wt, "_click_object", fake_click)
    assert wt.chop_phase(ctx, []) is True
    assert seen["action"] == "Chop"
    # The chat line is the pushed signal; the inventory read is the confirm.
    assert any("CHAT" in str(s) for s in seen["signals"])


def test_chop_phase_with_no_roots_in_view_does_not_stop_the_run(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    assert wt.chop_phase(ctx, []) is False
    assert ctx.state.safety_stopped is False


def test_fletch_phase_uses_the_knife_on_a_root(monkeypatch):
    ctx = _Ctx()
    used = {}
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(wt, "_use_knife_on_root",
                        lambda c, knife, root: used.setdefault("ok", True) or True)
    monkeypatch.setattr(wt, "_await", lambda *a, **k: True)
    items = [{"id": 0, "name": "Knife", "slot": 0},
             {"id": w.ITEM_ROOT, "name": "Bruma root", "slot": 1}]
    assert wt.fletch_phase(ctx, items) is True
    assert used["ok"] is True


def test_fletch_phase_without_a_knife_falls_through_rather_than_looping(monkeypatch):
    ctx = _Ctx()
    items = [{"id": w.ITEM_ROOT, "name": "Bruma root", "slot": 1}]
    assert wt.fletch_phase(ctx, items) is False


def test_feed_phase_clicks_the_lit_brazier_and_never_the_unlit_twin(monkeypatch):
    ctx = _Ctx()
    scene = [
        {"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
         "distance": 1, "x": 1, "y": 1, "plane": 0},
        {"id": w.OBJ_BRAZIER_LIT, "name": "Burning brazier", "actions": ["Feed"],
         "distance": 4, "x": 9, "y": 9, "plane": 0},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}

    def fake_click(ctx_, obj, action, budget_s, confirm, signals=None):
        seen["id"] = obj["id"]
        return True                      # feed_phase returns this straight through

    monkeypatch.setattr(wt, "_click_object", fake_click)
    items = [{"id": w.ITEM_KINDLING, "name": "Bruma kindling"}]
    assert wt.feed_phase(ctx, {}, items, "SOUTH_WEST") is True
    # The NEARER object is the unlit one; picking by name or by distance would take it.
    assert seen["id"] == w.OBJ_BRAZIER_LIT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: FAIL — `AttributeError: module 'wintertodt' has no attribute 'chop_phase'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/wintertodt.py`:

```python
CHOP_BUDGET_S = 90.0             # a whole inventory of roots; a guess, fit it live
FLETCH_BUDGET_S = 90.0           # a whole inventory fletched; a guess, fit it live
FEED_BUDGET_S = 60.0             # a whole inventory fed; a guess, fit it live


def _await(ctx, signals, budget_s, confirm, since=None):
    outcome = outcome_waits.await_pre(ctx, signals, budget_s, confirm, since=since,
                                      now_fn=_now, sleep_fn=_sleep)
    return bool(outcome and outcome.ok)


def chop_phase(ctx, items) -> bool:
    """Chop until the inventory is full.

    ONE CLICK, not one per root: chopping is a CONTINUOUS action and the game ends it itself
    with "Your inventory is too full". Re-clicking mid-action would cancel and restart it."""
    roots = _nearest(_objects(ids={w.OBJ_ROOTS}, action="Chop"))
    if not roots:
        log("wintertodt", "chop: no Bruma roots in view")
        return False
    return _click_object(
        ctx, roots, "Chop", CHOP_BUDGET_S,
        lambda: ws.inventory_full(_inventory()),
        signals=["CHAT_MESSAGE"])


def _use_knife_on_root(ctx, knife, root) -> bool:
    return _use_on(ctx, knife, root)


def fletch_phase(ctx, items) -> bool:
    """Fletch the whole inventory of roots into kindling.

    Also CONTINUOUS: one knife-on-root and the game fletches the lot, ending with "You have run
    out of bruma roots"."""
    knife = next((i for i in items if "knife" in (i.get("name") or "").lower()), None)
    root = next((i for i in items if i.get("id") == w.ITEM_ROOT), None)
    if not knife or not root:
        return False
    controller.open_inventory_tab(ctx.ser)
    if not _use_knife_on_root(ctx, knife, root):
        return False
    return _await(ctx, ["CHAT_MESSAGE", "INVENTORY_CHANGED"], FLETCH_BUDGET_S,
                  lambda: ws.count_item(_inventory(), w.ITEM_ROOT) == 0)


def feed_phase(ctx, reader, items, brazier) -> bool:
    """Feed the lit brazier until we are empty.

    The brazier is found by the LIT id (29314) specifically. Its unlit and broken twins are
    both named "Brazier" and one of them is usually nearer, so a nearest-by-name pick takes the
    wrong object -- the shut-vs-open door lesson."""
    lit = _nearest(_objects(ids={w.OBJ_BRAZIER_LIT}, action="Feed"))
    if not lit:
        log("wintertodt", "feed: no burning brazier in view")
        return False
    return _click_object(
        ctx, lit, "Feed", FEED_BUDGET_S,
        lambda: (ws.count_item(_inventory(), w.ITEM_KINDLING) == 0
                 and ws.count_item(_inventory(), w.ITEM_ROOT) == 0),
        signals=["CHAT_MESSAGE", "INVENTORY_CHANGED"])
```

Note, already verified: event types are plain **strings** in `bridge_events`, not module constants — there is no `bridge_events.CHAT_MESSAGE`. Every signal list in this plan uses `"CHAT_MESSAGE"` / `"INVENTORY_CHANGED"` / `"OBJECT_SPAWNED"` as strings, matching every other routine.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: PASS, 18 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/wintertodt.py ControllerAddon/tests/test_wintertodt.py
git commit -m "wintertodt: chop, fletch and feed as single continuous actions"
```

---

### Task 8: The `light`, `fix` and `dodge` rungs

**Files:**
- Modify: `ControllerAddon/routines/wintertodt.py` (append)
- Test: `ControllerAddon/tests/test_wintertodt.py` (append)

**Interfaces:**
- Consumes: Tasks 5-7.
- Produces: `light_phase(ctx, reader, brazier) -> bool`, `fix_phase(ctx, reader, brazier) -> bool`, `dodge_phase(ctx) -> bool`, `LIGHT_MAX_CLICKS`, `FIX_MAX_CLICKS`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_wintertodt.py`:

```python
def test_light_phase_targets_the_unlit_id_and_escalates_to_the_menu_when_hijacked(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": w.OBJ_BRAZIER_UNLIT, "name": "Brazier", "actions": ["Light"],
              "distance": 2, "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    tries = {"bare": 0, "menu": 0}
    monkeypatch.setattr(wt, "_click_object",
                        lambda *a, **k: tries.__setitem__("bare", tries["bare"] + 1) or False)
    monkeypatch.setattr(wt, "_click_menu",
                        lambda *a, **k: tries.__setitem__("menu", tries["menu"] + 1) or True)
    # _await must be stubbed too, or the menu branch falls through to the real
    # bridge_events.await_outcome and spins against a fake clock for the whole budget.
    monkeypatch.setattr(wt, "_await", lambda *a, **k: True)
    assert wt.light_phase(ctx, {}, "SOUTH_WEST") is True
    # A Wintertodt brazier on an official world is the most crowded object in the game;
    # golem_crafting MEASURED a 30-40% bare-click hijack rate on a far quieter one.
    assert tries["menu"] == 1


def test_light_phase_stops_after_its_ceiling(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    for _ in range(wt.LIGHT_MAX_CLICKS + 1):
        wt.light_phase(ctx, {}, "SOUTH_WEST")
    assert ctx.state.safety_stopped is True


def test_fix_phase_targets_the_broken_id(monkeypatch):
    ctx = _Ctx()
    scene = [{"id": w.OBJ_BRAZIER_BROKEN, "name": "Brazier", "actions": ["Fix"],
              "distance": 2, "x": 1, "y": 1, "plane": 0}]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: scene)
    seen = {}
    monkeypatch.setattr(wt, "_click_object",
                        lambda c, o, action, budget_s, confirm, signals=None:
                        seen.setdefault("id", o["id"]) or True)
    assert wt.fix_phase(ctx, {}, "SOUTH_WEST") is True
    assert seen["id"] == w.OBJ_BRAZIER_BROKEN


def test_dodge_phase_steps_off_the_tile_and_forgets_the_snow(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["wt_snow_tile"] = (10, 20, 0)
    ctx.state.data["wt_snow_at"] = wt._now()
    monkeypatch.setattr(controller, "get_player", lambda *a, **k: {"x": 10, "y": 20, "plane": 0})
    walked = {}
    monkeypatch.setattr(wt, "_force_walk",
                        lambda c, x, y, p: walked.setdefault("to", (x, y)) or True)
    assert wt.dodge_phase(ctx) is True
    assert walked["to"] != (10, 20)
    assert "wt_snow_tile" not in ctx.state.data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: FAIL — `AttributeError: module 'wintertodt' has no attribute 'light_phase'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/wintertodt.py`:

```python
LIGHT_MAX_CLICKS = 10
FIX_MAX_CLICKS = 10
BRAZIER_ACTION_BUDGET_S = 6.0
DODGE_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1))


def _click_menu(ctx, obj, option) -> bool:
    """The right-click escalation, immune to whatever is DRAWN over the aim pixel.

    golem_crafting MEASURED a 30-40% bare-click hijack rate from other players' models covering
    a plinth, and a Wintertodt brazier on an official world is far more crowded than that.
    Re-resolve the object first -- click_menu_action right-clicks exactly the pixel it is
    given, and a stale one produces a menu of pure ground options."""
    fresh = _nearest(_objects(ids={obj.get("id")}, fresh=True))
    return bool(controller.click_menu_action(ctx.ser, fresh or obj, option, offset=(0, 0),
                                             refresh_target=_refresh(fresh or obj)))


def _brazier_action(ctx, ctr_key, obj_id, option, want_status, brazier) -> bool:
    """Shared body for light and fix: bare click, then the menu, then a ceiling."""
    data = _d(ctx)
    obj = _nearest(_objects(ids={obj_id}, action=option))
    data[ctr_key] = int(data.get(ctr_key) or 0) + 1
    if data[ctr_key] > (LIGHT_MAX_CLICKS if option == "Light" else FIX_MAX_CLICKS):
        ctx.state.trigger_safety_stop(
            "wintertodt: %s never registered on the %s brazier after %d attempts"
            % (option, brazier, data[ctr_key] - 1))
        return False
    if not obj:
        return False

    confirm = lambda: ws.brazier_status(_reader(), brazier) == want_status
    signals = ["CHAT_MESSAGE"]
    if _click_object(ctx, obj, option, BRAZIER_ACTION_BUDGET_S, confirm, signals=signals):
        data[ctr_key] = 0
        return True
    if _click_menu(ctx, obj, option):
        if _await(ctx, signals, BRAZIER_ACTION_BUDGET_S + _walk_budget(obj), confirm):
            data[ctr_key] = 0
        return True
    return False


def light_phase(ctx, reader, brazier) -> bool:
    """25 points, and the wiki says it is worth interrupting anything else for.

    `reader` is accepted (the _PHASES dispatcher passes it) but not used: the confirm re-reads
    the HUD itself, because the snapshot that chose this rung is by then a click old."""
    return _brazier_action(ctx, "wt_light_clicks", w.OBJ_BRAZIER_UNLIT, "Light",
                           "LIT", brazier)


def fix_phase(ctx, reader, brazier) -> bool:
    """25 points. The wiki warns braziers are "fixed extremely quickly on Wintertodt worlds",
    so losing this race is normal and is a no-op, not a failure."""
    return _brazier_action(ctx, "wt_fix_clicks", w.OBJ_BRAZIER_BROKEN, "Fix",
                           "LIT", brazier)


def _force_walk(ctx, x, y, plane) -> bool:
    """A SHIFT-clicked walk. CLAUDE.md's standing rule: always shift+click a plain ground tile,
    or a nearby interactive object's own action can swallow the click."""
    import travel
    return bool(travel.walk_to_exact_tile(ctx, x, y, plane))


def dodge_phase(ctx) -> bool:
    """Step one tile out of the 3x3 area attack.

    An ACCELERATOR: if 29325 never reaches /objects (checklist item 1) this rung simply never
    fires and warmth is healed reactively instead. Nothing else depends on it."""
    px, py, plane = _player_tile()
    if px is None:
        return False
    _d(ctx).pop("wt_snow_tile", None)
    for dx, dy in DODGE_STEPS:
        if _force_walk(ctx, px + dx * 2, py + dy * 2, plane):
            log("wintertodt", "dodge: stepped off the snowfall tile")
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: PASS, 22 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/wintertodt.py ControllerAddon/tests/test_wintertodt.py
git commit -m "wintertodt: light, fix and dodge, with the crowded-brazier menu escalation"
```

---

### Task 9: `tick`, `before_break`, and the stuck watch

**Files:**
- Modify: `ControllerAddon/routines/wintertodt.py` (append)
- Test: `ControllerAddon/tests/test_wintertodt.py` (append)

**Interfaces:**
- Consumes: Tasks 5-8.
- Produces: `tick(ctx)`, `before_break(ctx)`, `between_rounds_phase(ctx, reader, items)`, `_progress_signature(reader, items)`, `_stuck_watch(ctx, signature)`, `STALL_WARN_S`, `STALL_STOP_S`, `BREAK_DEFER_MAX_S`.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_wintertodt.py`:

```python
def test_tick_dispatches_to_the_phase_derive_phase_chose(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(wt, "snapshot", lambda c: ({"atWintertodt": True}, [], "SOUTH_WEST"))
    monkeypatch.setattr(wt.ws, "derive_phase", lambda **k: "chop")
    called = {}
    monkeypatch.setattr(wt, "chop_phase", lambda c, items: called.setdefault("chop", True))
    wt.tick(ctx)
    assert called.get("chop") is True


def test_tick_outside_the_arena_safety_stops(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(wt, "snapshot", lambda c: ({"atWintertodt": False}, [], None))
    wt.tick(ctx)
    assert ctx.state.safety_stopped is True


def test_the_stuck_watch_warns_then_stops_on_an_unchanged_signature(hermetic):
    ctx = _Ctx()
    wt._stuck_watch(ctx, "same")
    hermetic["t"] += wt.STALL_WARN_S + 1
    wt._stuck_watch(ctx, "same")
    assert ctx.state.safety_stopped is False       # warn first
    hermetic["t"] += wt.STALL_STOP_S
    wt._stuck_watch(ctx, "same")
    assert ctx.state.safety_stopped is True


def test_the_stuck_watch_resets_when_anything_moves(hermetic):
    ctx = _Ctx()
    wt._stuck_watch(ctx, "a")
    hermetic["t"] += wt.STALL_STOP_S + 1
    wt._stuck_watch(ctx, "b")
    assert ctx.state.safety_stopped is False


def test_progress_signature_moves_when_points_move():
    a = wt._progress_signature({"points": 100, "energyPercent": 50}, [])
    b = wt._progress_signature({"points": 125, "energyPercent": 50}, [])
    assert a != b


def test_before_break_plays_an_active_round_out(monkeypatch, hermetic):
    # Points are all-or-nothing at 500, so a break landing mid-round throws the round away.
    ctx = _Ctx()
    rounds = {"active": True}
    ticks = {"n": 0}

    def fake_tick(c):
        ticks["n"] += 1
        hermetic["t"] += 1.0
        if ticks["n"] >= 3:
            rounds["active"] = False

    monkeypatch.setattr(wt, "tick", fake_tick)
    monkeypatch.setattr(wt, "_reader", lambda: {"atWintertodt": True,
                                                "roundActive": rounds["active"]})
    wt.before_break(ctx)
    assert ticks["n"] >= 3


def test_before_break_gives_up_at_its_ceiling(monkeypatch, hermetic):
    ctx = _Ctx()
    monkeypatch.setattr(wt, "tick", lambda c: hermetic.__setitem__("t", hermetic["t"] + 5.0))
    monkeypatch.setattr(wt, "_reader", lambda: {"atWintertodt": True, "roundActive": True})
    wt.before_break(ctx)     # must return rather than spin forever
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: FAIL — `AttributeError: module 'wintertodt' has no attribute 'tick'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/wintertodt.py`:

```python
STALL_WARN_S = 60.0
STALL_STOP_S = 180.0
BREAK_DEFER_MAX_S = 420.0        # a round averages ~5 min including the 60s gap
# NB: how many inactive reads make a round end is the CONFIG key `roundEndReads`, read by
# derive_phase. Do not add a module constant beside it -- two sources for one number is how
# they drift apart.


def _progress_signature(reader, items):
    """Everything that moves when the routine is working. Unchanged for STALL_STOP_S means
    stuck -- and CLAUDE.md's standing lesson is that a silent loop is worse than a crash."""
    reader = reader or {}
    return "|".join(str(x) for x in (
        reader.get("points"),
        reader.get("energyPercent"),
        reader.get("warmth"),
        len(items or []),
        ws.count_item(items, w.ITEM_ROOT),
        ws.count_item(items, w.ITEM_KINDLING),
    ))


def _stuck_watch(ctx, signature):
    data = _d(ctx)
    if data.get("wt_sig") != signature:
        data["wt_sig"] = signature
        data["wt_sig_at"] = _now()
        data["wt_sig_warned"] = False
        return
    elapsed = _now() - float(data.get("wt_sig_at") or _now())
    if elapsed >= STALL_STOP_S:
        ctx.state.trigger_safety_stop(
            "wintertodt: no progress for %.0fs" % elapsed)
    elif elapsed >= STALL_WARN_S and not data.get("wt_sig_warned"):
        data["wt_sig_warned"] = True
        log("wintertodt", "[!] wintertodt: STUCK? no progress for %.0fs" % elapsed)


def between_rounds_phase(ctx, reader, items) -> bool:
    """The ~60s gap. Top up kit and potions, and be standing at the brazier ready to light the
    instant the timer hits 0:00 -- worth 25 points plus Firemaking level * 6 XP, the wiki's
    single best-value action of the round."""
    config = ctx.state.config or DEFAULT_CONFIG
    if ws.kit_deficit(items, config.get("minDoses", 4)):
        return kit_phase(ctx, items)
    seconds = reader.get("secondsUntilRoundStart")
    if seconds is not None and seconds > 5:
        brazier = _nearest(_objects(ids=w.BRAZIER_IDS))
        if brazier and _too_far(brazier):
            return _walk_to(ctx, brazier)
    outcome_waits.tick_pause(ctx, 1, now_fn=_now, sleep_fn=_sleep)
    return True


_PHASES = {
    "kit": lambda ctx, reader, items, brazier: kit_phase(ctx, items),
    "warmth": lambda ctx, reader, items, brazier: warmth_phase(ctx, items),
    "dodge": lambda ctx, reader, items, brazier: dodge_phase(ctx),
    "light": lambda ctx, reader, items, brazier: light_phase(ctx, reader, brazier),
    "fix": lambda ctx, reader, items, brazier: fix_phase(ctx, reader, brazier),
    "feed": lambda ctx, reader, items, brazier: feed_phase(ctx, reader, items, brazier),
    "fletch": lambda ctx, reader, items, brazier: fletch_phase(ctx, items),
    "chop": lambda ctx, reader, items, brazier: chop_phase(ctx, items),
    "between_rounds": lambda ctx, reader, items, brazier:
        between_rounds_phase(ctx, reader, items),
}


def tick(ctx):
    reader, items, brazier = snapshot(ctx)
    config = ctx.state.config or DEFAULT_CONFIG
    data = _d(ctx)

    phase = ws.derive_phase(
        reader=reader, items=items, config=config, brazier=brazier,
        snow_on_tile=_snow_on_tile(ctx),
        round_over_streak=int(data.get("wt_round_over_streak") or 0),
    )

    if phase != data.get("wt_phase"):
        log("wintertodt", "phase=%s brazier=%s points=%s warmth=%s energy=%s"
            % (phase, brazier, reader.get("points"), reader.get("warmth"),
               reader.get("energyPercent")))
        data["wt_phase"] = phase

    if phase == "stop":
        ctx.state.trigger_safety_stop(
            "wintertodt: not in the Wintertodt arena (region %d) -- this routine owns no travel"
            % w.REGION)
        return

    _stuck_watch(ctx, _progress_signature(reader, items))
    handler = _PHASES.get(phase)
    if handler:
        handler(ctx, reader, items, brazier)


def before_break(ctx):
    """Play the round out before the break starts.

    Points are all-or-nothing at 500 and every point is forfeited by leaving mid-round, so a
    break landing mid-round throws away the whole round. GOTR's own measured lesson, and the
    reason `before_break` exists in the routine contract at all. The break begins the moment
    this returns, so the ceiling matters as much as the loop."""
    started = _now()
    while _now() - started < BREAK_DEFER_MAX_S:
        reader = _reader()
        if not ws.round_active(reader):
            log("wintertodt", "before_break: round over, break may start")
            return
        tick(ctx)
    log("wintertodt", "before_break: gave up waiting for the round to end after %.0fs"
        % BREAK_DEFER_MAX_S)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -v`
Expected: PASS, 29 tests

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/wintertodt.py ControllerAddon/tests/test_wintertodt.py
git commit -m "wintertodt: tick dispatch, stuck watch and a round-respecting before_break"
```

---

### Task 10: GUI wiring and documentation

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `ControllerAddon/controller.py` (the `ROUTINE_FILES` dict, around line 3350)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Task 9's finished routine.
- Produces: nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Write the failing test**

Append to `ControllerAddon/tests/test_wintertodt.py`:

```python
def test_the_routine_is_wired_into_the_gui_dropdown():
    # A routine file alone is only launchable from the command line. Both the Java enum entry
    # and this key are required, per CLAUDE.md's routine contract.
    assert controller.ROUTINE_FILES["WINTERTODT"] == "routines/wintertodt.py"


def test_the_java_enum_has_a_matching_constant():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / \
        "src/main/java/com/example/controller/RoutineChoice.java"
    assert "WINTERTODT(" in src.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py -k wired -v`
Expected: FAIL — `KeyError: 'WINTERTODT'`

- [ ] **Step 3: Write the implementation**

In `src/main/java/com/example/controller/RoutineChoice.java`, change the last enum constant's terminating semicolon to a comma and add the new constant:

```java
	DEEP_SEA_TRAWLING("Deep Sea Trawling"),
	WINTERTODT("Wintertodt (group, fletch)");
```

In `ControllerAddon/controller.py`, add to the `ROUTINE_FILES` dict (after the `DEEP_SEA_TRAWLING` entry):

```python
    "WINTERTODT": "routines/wintertodt.py",
```

In `CLAUDE.md`, add a section after the deep-sea-trawling one:

```markdown
## Current state of routines/wintertodt.py

Written 2026-09-08 from the OSRS Wiki (Wintertodt rev 15276053, Strategies rev 15278826), the
cache dump `5bc2ff1e`, RuneLite's own decompiled `WintertodtPlugin` and live bridge reads.
Design: `docs/superpowers/specs/2026-09-08-wintertodt-design.md`, whose live-verification
checklist is the first session's worklist. **There is no human recording and NOTHING has been
run live.** Needs a client rebuild (the `RoutineChoice` entry is Java).

- **`GET /api/minigames/wintertodt` already serves the whole HUD** -- points, energy, warmth
  (raw 0-1000), `secondsUntilRoundStart`, and per-brazier LIT/UNLIT/BROKEN plus pyromancer
  ALIVE/INCAPACITATED. VERIFIED live 2026-09-08. **No Java change for state.** But
  `isOperational()` and `isRewardEligible()` are Java METHODS, so Gson omits them and
  `wintertodt_state` re-derives both -- the same trap CLAUDE.md records for GOTR.
- **NEVER MATCH A BRAZIER OR CRATE BY NAME.** Nine objects share three names: the unlit
  brazier (29312) and the broken one (29313) are both `Brazier` and differ only in their
  action, and SIX objects are called `Crate` -- 29316-29320 hand out hammer/knife/axe/
  tinderbox/concoctions while 29301-29305 are scenery with no ops at all. This is the
  shut-vs-open door lesson five ways over.
- **There is NO banking and NO loadout.** Every tool is free from the arena crates and USER
  chose never to search the Reward Cart (it holds 8,000, so nothing is lost). The routine never
  leaves region 6462, which is why `derive_phase` returns `stop` rather than trying to travel.
- **Chop, fletch and feed are CONTINUOUS actions**, ended by the game's own chat lines
  (`Your inventory is too full`, `You have run out of bruma roots`). One inventory is about
  three clicks, so this routine is an interrupt handler, not a click loop. Re-clicking a
  running action cancels it.
- **Fletching is a deliberate POINTS-over-XP trade** (USER's choice). The wiki is explicit that
  fletching is SLOWER Firemaking XP; it buys reward-cart rolls. A low FM/hr is the design, not
  a bug. `fletch=False` is the one-key switch to the faster-XP roots-only method, and
  `feed_item`/`derive_phase` are written so that switch cannot deadlock a full inventory of
  roots -- pinned by test.
- **500 points is a CLIFF**: below it the round pays nothing and leaving mid-round forfeits
  everything. Hence warmth outranks all work below it in the ladder, and `before_break` plays
  the round out (GOTR's lesson).
- **The 3x3 area-attack warning is a GameObject (29325), not a spotanim**, so it arrives as an
  ordinary `OBJECT_SPAWNED` event. The dodge rung is an accelerator only: if it never arrives,
  warmth is healed reactively instead and nothing else breaks.
- **Expect the brazier to be contested.** golem_crafting MEASURED a 30-40% bare-click hijack
  rate from other players' models on a far quieter object; the light/fix rungs escalate to
  `click_menu_action` after a failed bare click for exactly that reason.
- **No `expected_action` hover gate on any world click.** `/hover` reports what is under the
  cursor RIGHT NOW and these clicks are issued while walking -- the gate withholds every one
  (the Gauntlet and Motherlode both paid for this).
```

- [ ] **Step 4: Run the full suite**

Run: `cd ControllerAddon && python -m pytest tests/test_wintertodt.py tests/test_wintertodt_ids.py tests/test_wintertodt_state.py -v`
Expected: PASS, all tests

Then confirm nothing else regressed and the Java still compiles:

```bash
cd ControllerAddon && python -m pytest tests/ -q
cd .. && ./gradlew.bat compileJava
```

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/controller/RoutineChoice.java ControllerAddon/controller.py CLAUDE.md ControllerAddon/tests/test_wintertodt.py
git commit -m "wintertodt: wire into the Routine dropdown and document the routine"
```

---

## Deliberately deferred to the first live session

**The spec's "Where to stand" behaviour is not implemented by any task above, and that is a
decision, not an oversight.** The wiki names two good standing spots — hugging the wall beside
the bruma roots while fletching, and two tiles off the brazier while feeding — to dodge the 3x3
attack and the shrapnel from a shattering brazier. Both are *tile* facts, and the arena's tile
geometry is exactly what checklist item 3 measures; picking coordinates offline would be
inventing them. The routine's `_too_far` / `_walk_to` pair already brings the player into click
range of each target, which is the correctness requirement; standing *optimally* is a warmth
optimisation on top of it, and the `dodge` rung plus the `warmth` rung already cover the damage
this would avoid. Implement it in the follow-up pass once the tiles are measured.

Two other spec items are recorded as out of scope in the spec itself and correctly have no
task: healing pyromancers (75 points, the largest single award, and the obvious first
extension once the base loop is proven) and searching the Reward Cart.

## After the plan

Nothing here has been run against a live client. The first session is the spec's
live-verification checklist, in order — starting with whether `/objects` lists `active=no`
locs at all (the dodge rung depends on it) and whether the arena is instanced.

Three helper names in this plan's first draft did not exist, and all three are now corrected
against the real `controller.py` and `bridge_events.py` — each is called out inline at its task
so nobody "fixes" them back. For the record: there is no `controller.get_inventory_item_point`
(an inventory item dict already carries `screenX`/`screenY`, so it *is* the clickable entity),
no `controller.use_on` (the only one is routine-local, `routines/corrupted_gauntlet.py:1242`),
and no `bridge_events.CHAT_MESSAGE` (event types are plain strings). The general lesson, which
this repo has paid for before: a plan that invents a helper name reads perfectly and fails on
contact.
