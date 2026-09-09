# Vale Totems Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `vale_totems` routine that laps the eight Auburn Valley totem sites — claim, build, carve to match the site's spirits, decorate with fletched bows — banking logs when it runs out.

**Architecture:** All state comes from ONE existing endpoint, `GET /api/minigames/vale-totems` (the vendored m8aq `ValeTotems` reader, already on the build path — **no Java state work**). A pure `vale_totems.py` re-derives the predicates Gson drops, `vale_totem_route.py` holds the site table and the per-leg waypoints measured from the user's recording (worldmap has no collision data for Varlamore), and `routines/vale_totems.py` runs a per-site ladder. The group-270 menu helpers already live-verified in `routines/fletching.py` are extracted into a shared `skillmulti.py` and reused for BOTH the carve prompt and the fletch prompt.

**Tech Stack:** Python 3.13, pytest. Existing repo modules: `controller`, `outcome_waits`, `banker`, `travel`, `debug_log`. Java: RuneLite plugin (`RoutineChoice`, `ControllerConfig`) — dropdown only.

**Spec:** `docs/superpowers/specs/2026-09-08-vale-totems-design.md`

## Global Constraints

- **Reader methods are not serialized.** `isFullyCarved()`, `isFullyBuilt()`, `isFullyDecorated()`, `isOfferingCapped()`, `isStarted()`, `getMissingNearbySpirits()`, `getIncorrectCarvings()` are Java methods — Gson emits fields only. Re-derive every one in Python.
- **`controller.get_api` returns `None`** when the endpoint is unavailable and a dict with an `"error"` key when the reader throws. Neither means "empty" — treat both as "cannot verify".
- **Spirit numbering is 1 buffalo, 2 jaguar, 3 eagle, 4 snake, 5 scorpion**; a spirit's carved value is `nearbyValue + 9` (BUFFALO 10 … SCORPION 14).
- **Carve prompt and fletch prompt are both widget group 270.** Every keypress is gated on 270.5's text: `"What animal would you like to carve?"` vs `"What would you like to make?"`.
- **Never answer the fletch prompt with `Space`** — it repeats the last crafted item.
- **`worldmap.find_path` returns `None` everywhere in Varlamore.** Never call it in this routine. Walking is waypoint hops via `travel._hop_to`.
- Tests are hermetic: no test may reach the live bridge or the serial port.
- Site N's offerings object id is always `base_id + 4`.

---

### Task 1: Extract the group-270 menu helpers into `skillmulti.py`

`routines/fletching.py` already owns live-verified group-270 handling. Vale Totems needs the same code for a *different* prompt, so it moves to a shared module rather than being copied. This is a pure refactor plus one new function (the title gate), and `fletching.py` must keep working unchanged.

**Files:**
- Create: `ControllerAddon/skillmulti.py`
- Modify: `ControllerAddon/routines/fletching.py` (delete the moved functions, import them)
- Test: `ControllerAddon/tests/test_skillmulti.py`

**Interfaces:**
- Consumes: `controller.get_widgets_dump()`, `controller.get_widget(group, child)`
- Produces:
  - `skillmulti.GROUP = 270`, `FIRST_ITEM_CHILD = 15`, `LAST_ITEM_CHILD = 32`, `TITLE_CHILD = 5`
  - `skillmulti.TITLE_CARVE = "what animal would you like to carve?"`
  - `skillmulti.TITLE_MAKE = "what would you like to make?"`
  - `skillmulti.strip_tags(text: str) -> str`
  - `skillmulti.menu_label(product: str) -> str`
  - `skillmulti.item_widget(product: str) -> dict | None`
  - `skillmulti.option_number(widget: dict | None) -> int | None`
  - `skillmulti.is_open() -> bool`
  - `skillmulti.prompt_title() -> str`
  - `skillmulti.showing(title: str) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# ControllerAddon/tests/test_skillmulti.py
import pytest
import controller
import skillmulti


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)


def test_strip_tags_removes_colour_tags():
    assert skillmulti.strip_tags("<col=ff9040>Maple longbow</col>") == "Maple longbow"


def test_menu_label_drops_the_unstrung_suffix():
    assert skillmulti.menu_label("Yew longbow (u)") == "yew longbow"


def test_item_widget_matches_on_name_and_derives_the_option_number(monkeypatch):
    dump = [
        {"group": 270, "child": 15, "name": "", "text": "60 arrow shafts", "screenX": 10},
        {"group": 270, "child": 16, "name": "", "text": "<col=ff9040>Yew shortbow</col>",
         "screenX": 10},
        {"group": 270, "child": 17, "name": "", "text": "<col=ff9040>Yew longbow</col>",
         "screenX": 10},
    ]
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: dump)
    w = skillmulti.item_widget("Yew longbow (u)")
    assert w["child"] == 17
    assert skillmulti.option_number(w) == 3


def test_prompt_title_distinguishes_the_carve_prompt_from_the_fletch_prompt(monkeypatch):
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, index=None: {"text": "What animal would you like to carve?"})
    assert skillmulti.showing(skillmulti.TITLE_CARVE) is True
    assert skillmulti.showing(skillmulti.TITLE_MAKE) is False


def test_prompt_title_is_empty_when_the_widget_is_unavailable():
    assert skillmulti.prompt_title() == ""
    assert skillmulti.showing(skillmulti.TITLE_CARVE) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_skillmulti.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skillmulti'`

- [ ] **Step 3: Write the implementation**

Create `ControllerAddon/skillmulti.py`. Move `strip_tags`, `menu_label`, `menu_item_widget` (renamed `item_widget`), `menu_option_number` (renamed `option_number`) and `menu_is_open` (renamed `is_open`) out of `routines/fletching.py` **verbatim, docstrings included** — they carry the live measurements and must not be reworded. Add only the title gate:

```python
# ControllerAddon/skillmulti.py
"""The skillmulti make-menu (widget group 270), shared by every routine that drives it.

Extracted from routines/fletching.py 2026-09-08, where every function below was live-verified.
It is shared because Vale Totems drives the SAME widget group for a different prompt: the
carve menu and the fletch menu are both group 270 and can only be told apart by the title.
"""
import re

import controller

GROUP = 270                # cache: skillmulti.if3
FIRST_ITEM_CHILD = 15      # children 15..32 are the item buttons `a`..`r`
LAST_ITEM_CHILD = 32
TITLE_CHILD = 5            # runelite-api gameval: Skillmulti.TITLE == 270.5

# The two prompts this group serves, lower-cased for comparison. USER 2026-09-08.
TITLE_CARVE = "what animal would you like to carve?"
TITLE_MAKE = "what would you like to make?"

_TAG_RE = re.compile(r"<[^>]*>")

# ... strip_tags / menu_label / item_widget / option_number / is_open moved here verbatim ...


def prompt_title() -> str:
    """The make-menu's title text, lower-cased and tag-stripped; "" when it cannot be read.

    Empty means "cannot tell", never "no menu" -- an unavailable widget must not be allowed to
    look like a confirmed prompt, because the caller's next act is a keypress."""
    widget = controller.get_widget(GROUP, TITLE_CHILD)
    if not widget:
        return ""
    return strip_tags(widget.get("text") or "").strip().lower()


def showing(title: str) -> bool:
    """Whether the menu currently on screen is the one titled `title`.

    THE GATE FOR EVERY KEYPRESS. Carve and fletch are the same widget group, so pressing a
    number without checking this can answer the wrong prompt -- carving an animal into a totem
    when we meant to fletch a bow, or the reverse."""
    return prompt_title() == title
```

Then in `routines/fletching.py`, delete those five functions and add `import skillmulti` plus module-level aliases so the rest of that file and its tests keep working unchanged:

```python
import skillmulti

SKILLMULTI_GROUP = skillmulti.GROUP
SKILLMULTI_FIRST_ITEM_CHILD = skillmulti.FIRST_ITEM_CHILD
SKILLMULTI_LAST_ITEM_CHILD = skillmulti.LAST_ITEM_CHILD

strip_tags = skillmulti.strip_tags
menu_label = skillmulti.menu_label
menu_item_widget = skillmulti.item_widget
menu_option_number = skillmulti.option_number
menu_is_open = skillmulti.is_open
```

- [ ] **Step 4: Run both suites to verify the refactor changed nothing**

Run: `cd ControllerAddon && python -m pytest tests/test_skillmulti.py tests/test_fletching.py -v`
Expected: PASS — the new tests pass AND every existing fletching test still passes. If `tests/test_fletching.py` does not exist, run `python -c "import routines.fletching"` instead and confirm it imports.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/skillmulti.py ControllerAddon/routines/fletching.py ControllerAddon/tests/test_skillmulti.py
git commit -m "skillmulti: share the group-270 menu helpers, add the prompt-title gate"
```

---

### Task 2: `vale_totem_route.py` — the site table and the measured waypoints

Data only. No behaviour, so the tests are invariants that would catch a mistyped coordinate.

**Files:**
- Create: `ControllerAddon/vale_totem_route.py`
- Test: `ControllerAddon/tests/test_vale_totem_route.py`

**Interfaces:**
- Produces:
  - `vale_totem_route.SITES: dict[int, dict]` — each `{"base_id", "offerings_id", "totem", "offerings", "stand"}`, tiles as `(x, y)` 2-tuples, plane always 0
  - `vale_totem_route.RING: tuple = (6, 5, 4, 3, 2, 1, 8, 7)`
  - `vale_totem_route.LEGS: dict[tuple[int, int], tuple[tuple[int, int], ...]]`
  - `vale_totem_route.BANK: dict` — `{"object_id": 57330, "tile": (1416, 3351), "stand": (1416, 3350)}`
  - `vale_totem_route.next_in_ring(site: int) -> int`
  - `vale_totem_route.leg_waypoints(a: int, b: int) -> tuple`
  - `vale_totem_route.PLANE = 0`

- [ ] **Step 1: Write the failing test**

```python
# ControllerAddon/tests/test_vale_totem_route.py
import math

import vale_totem_route as r


def test_every_site_is_present_with_the_offerings_id_four_above_the_base():
    assert sorted(r.SITES) == [1, 2, 3, 4, 5, 6, 7, 8]
    for n, site in r.SITES.items():
        assert site["offerings_id"] == site["base_id"] + 4, n


def test_the_ring_visits_every_site_once_and_wraps():
    assert sorted(r.RING) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert r.next_in_ring(6) == 5
    assert r.next_in_ring(1) == 8
    assert r.next_in_ring(7) == 6


def test_there_is_a_leg_for_every_ring_step():
    for a in r.RING:
        b = r.next_in_ring(a)
        assert r.leg_waypoints(a, b), "no waypoints for leg %d->%d" % (a, b)


def test_every_leg_starts_and_ends_beside_its_sites():
    # The recorded path starts where the human stood at A and ends where they stood at B.
    for (a, b), pts in r.LEGS.items():
        assert math.dist(pts[0], r.SITES[a]["stand"]) <= 4, (a, b)
        assert math.dist(pts[-1], r.SITES[b]["stand"]) <= 4, (a, b)


def test_no_hop_exceeds_minimap_range():
    # travel.WALK_HOP_MAX_TILES is 24 and the measured minimap reach is 28; the extracted
    # waypoints max out around 12, so anything near the cap means a typo.
    for leg, pts in r.LEGS.items():
        for p, q in zip(pts, pts[1:]):
            assert math.dist(p, q) <= 20, (leg, p, q)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totem_route.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vale_totem_route'`

- [ ] **Step 3: Write the implementation**

```python
# ControllerAddon/vale_totem_route.py
"""Where the eight Vale Totems sites are, and how the human walks between them.

An ACCELERATOR, never truth -- the same rule trawling_routes.py states. The live reader
(GET /api/minigames/vale-totems) decides WHAT to do at a site; this table only says where to
walk. Site ids and tiles were MEASURED from recordings/2026-09-08_135457_vale-totem.*.

WHY WAYPOINTS AND NOT worldmap.find_path: worldmap.py has NO usable collision data for any of
Varlamore. MEASURED 2026-09-08 -- reachable_from(radius=40) answers 1 tile at every totem site,
at the bank, at Civitas illa Fortis, at Aldarin and at the Hunter guild, and find_path between
any two sites answers None. So the routine walks the path the human actually walked: these are
their own tick-by-tick positions, simplified to ~10-tile steps, six of the eight legs
cross-checked against a second traversal.
"""

PLANE = 0

SITES = {
    1: {"base_id": 57016, "offerings_id": 57020,
        "totem": (1370, 3375), "offerings": (1370, 3374), "stand": (1369, 3375)},
    2: {"base_id": 57022, "offerings_id": 57026,
        "totem": (1346, 3319), "offerings": (1347, 3319), "stand": (1346, 3318)},
    3: {"base_id": 57028, "offerings_id": 57032,
        "totem": (1385, 3274), "offerings": (1385, 3275), "stand": (1386, 3274)},
    4: {"base_id": 57034, "offerings_id": 57038,
        "totem": (1413, 3286), "offerings": (1412, 3286), "stand": (1413, 3287)},
    5: {"base_id": 57040, "offerings_id": 57044,
        "totem": (1438, 3305), "offerings": (1438, 3306), "stand": (1437, 3305)},
    6: {"base_id": 57046, "offerings_id": 57050,
        "totem": (1477, 3332), "offerings": (1478, 3332), "stand": (1477, 3331)},
    7: {"base_id": 57052, "offerings_id": 57056,
        "totem": (1453, 3341), "offerings": (1452, 3341), "stand": (1453, 3340)},
    8: {"base_id": 57058, "offerings_id": 57062,
        "totem": (1398, 3329), "offerings": (1398, 3330), "stand": (1397, 3329)},
}

# The order the human walks, both laps: descending site number, wrapping 1 -> 8.
RING = (6, 5, 4, 3, 2, 1, 8, 7)

BANK = {"object_id": 57330, "tile": (1416, 3351), "stand": (1416, 3350)}

# Agility shortcuts standing ON the recorded path -- 56990 is one tile off leg 4->3's second
# waypoint. Recorded so a live failure there is recognisable; nothing reads them yet.
LOG_BALANCES = {56990: (1401, 3290), 56991: (1453, 3335)}

LEGS = {
    (6, 5): ((1477, 3330), (1473, 3319), (1463, 3314), (1453, 3313), (1443, 3315), (1438, 3307)),
    (5, 4): ((1438, 3307), (1435, 3317), (1424, 3318), (1422, 3307), (1420, 3297), (1412, 3288),
             (1412, 3287)),
    (4, 3): ((1412, 3287), (1402, 3290), (1396, 3281), (1387, 3275), (1386, 3275)),
    (3, 2): ((1386, 3275), (1381, 3285), (1372, 3291), (1361, 3296), (1355, 3305), (1350, 3314),
             (1347, 3318)),
    (2, 1): ((1347, 3318), (1351, 3328), (1359, 3335), (1368, 3343), (1368, 3354), (1365, 3365),
             (1370, 3373)),
    (1, 8): ((1370, 3374), (1377, 3365), (1385, 3359), (1385, 3349), (1393, 3340), (1398, 3331)),
    (8, 7): ((1398, 3331), (1405, 3339), (1412, 3347), (1422, 3348), (1433, 3346), (1443, 3345),
             (1451, 3341)),
    (7, 6): ((1451, 3341), (1453, 3331), (1461, 3324), (1473, 3322), (1478, 3331)),
}


def next_in_ring(site: int) -> int:
    """The site after `site` on the circuit."""
    return RING[(RING.index(site) + 1) % len(RING)]


def leg_waypoints(a: int, b: int) -> tuple:
    """The measured path from site `a` to site `b`, or () when that leg was never recorded."""
    return LEGS.get((a, b), ())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totem_route.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/vale_totem_route.py ControllerAddon/tests/test_vale_totem_route.py
git commit -m "vale totems: site table and the measured per-leg waypoints"
```

---

### Task 3: `vale_totems.py` — parse the reader and re-derive the dropped predicates

Pure. No HTTP, no client, no serial port.

**Files:**
- Create: `ControllerAddon/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems.py`

**Interfaces:**
- Produces:
  - `vale_totems.SPIRIT_VALUES: dict[str, int]` — `{"BUFFALO": 1, ..., "SCORPION": 5}`
  - `vale_totems.CARVED_OFFSET = 9`
  - `vale_totems.WOOD_DURABILITY: dict[str, int]`
  - `vale_totems.Site` — attrs `number, base_value, wood, base_carved, segment_values, decorations, nearby_spirits, durability_remaining, unclaimed_offerings`
  - `vale_totems.State` — attrs `sites: dict[int, Site], research_points, total_unclaimed, at_vale_totems`
  - `vale_totems.parse_state(payload: dict | None) -> State | None`
  - `vale_totems.carved_spirits(site) -> set[str]`
  - `vale_totems.missing_spirits(site) -> list[str]`
  - `vale_totems.needs_build(site) -> bool`
  - `vale_totems.needs_carve(site) -> bool`
  - `vale_totems.needs_decorate(site) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# ControllerAddon/tests/test_vale_totems.py
import vale_totems as vt


def _site(**kw):
    base = {
        "siteNumber": 1, "baseValue": 0, "wood": "NONE", "baseCarved": False,
        "bottom": {"value": 0, "spirit": "NONE"},
        "middle": {"value": 0, "spirit": "NONE"},
        "top": {"value": 0, "spirit": "NONE"},
        "decorations": 0, "nearbySpirits": ["BUFFALO", "JAGUAR", "SNAKE"],
        "nearbySpiritValues": [1, 2, 4],
        "durabilityRemaining": 0, "unclaimedOfferings": 0,
    }
    base.update(kw)
    return base


def _payload(*sites):
    return {"sites": list(sites), "researchPoints": 94, "totalUnclaimedOfferings": 385,
            "atValeTotems": True}


def test_parse_state_returns_none_when_the_endpoint_is_unavailable():
    assert vt.parse_state(None) is None


def test_parse_state_returns_none_when_the_reader_errored():
    assert vt.parse_state({"reader": "vale-totems", "error": "boom"}) is None


def test_an_unbuilt_site_needs_a_build_and_nothing_else():
    state = vt.parse_state(_payload(_site()))
    site = state.sites[1]
    assert vt.needs_build(site) is True
    assert site.unclaimed_offerings == 0


def test_missing_spirits_excludes_ones_already_carved():
    # BUFFALO carved (value 1 + 9 == 10) already sits in the bottom segment.
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW",
        bottom={"value": 10, "spirit": "BUFFALO"},
        nearbySpirits=["BUFFALO", "JAGUAR", "SNAKE"],
    ))).sites[1]
    assert vt.carved_spirits(site) == {"BUFFALO"}
    assert vt.missing_spirits(site) == ["JAGUAR", "SNAKE"]


def test_a_fully_carved_totem_still_needs_decorating_until_four_are_on():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=3))).sites[1]
    assert vt.needs_carve(site) is False
    assert vt.needs_decorate(site) is True

    done = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=4))).sites[1]
    assert vt.needs_decorate(done) is False


def test_base_carved_is_what_ends_carving_not_the_segment_count():
    # The server owns "carving is finished". A totem whose three segments are all carved but
    # whose baseCarved is still false is NOT done -- trusting the segments would stop early.
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=False,
        bottom={"value": 10, "spirit": "BUFFALO"},
        middle={"value": 11, "spirit": "JAGUAR"},
        top={"value": 13, "spirit": "SNAKE"}))).sites[1]
    assert vt.needs_carve(site) is True


def test_state_carries_the_globals():
    state = vt.parse_state(_payload(_site()))
    assert state.research_points == 94
    assert state.total_unclaimed == 385
    assert state.at_vale_totems is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vale_totems'`

- [ ] **Step 3: Write the implementation**

```python
# ControllerAddon/vale_totems.py
"""Vale Totems state, re-derived from GET /api/minigames/vale-totems.

PURE: no HTTP, no client, no serial port, so every rule below is table-testable.

WHY THIS MODULE EXISTS AT ALL: the vendored m8aq reader already computes the interesting
questions -- isFullyCarved(), isFullyDecorated(), getMissingNearbySpirits() -- but they are
METHODS, and Gson serializes FIELDS. None of them reach the JSON. Same trap CLAUDE.md records
for GOTR's round phase and Wintertodt's isOperational.
"""
from dataclasses import dataclass, field

# nearbyValue, straight off the reader's Spirit enum -- and the same number the carve menu's
# options are ordered by. CONFIRMED three ways: the human recording's 14 builds, the cache's
# enum, and totem-fletching's fixed A..E option order.
SPIRIT_VALUES = {"BUFFALO": 1, "JAGUAR": 2, "EAGLE": 3, "SNAKE": 4, "SCORPION": 5}

# A carved segment stores the spirit as nearbyValue + 9 (BUFFALO 10 .. SCORPION 14). This is
# TotemService.getCarvedAnimalsStatus's `progressKey = animal + 9` and m8aq's carvedValue table.
CARVED_OFFSET = 9

# Ent visits a totem survives, by wood (reader's Wood.maximumDurability).
WOOD_DURABILITY = {"OAK": 2, "WILLOW": 3, "MAPLE": 4, "YEW": 5, "MAGIC": 6, "REDWOOD": 7}

MAX_DECORATIONS = 4


@dataclass
class Site:
    number: int
    base_value: int
    wood: str
    base_carved: bool
    segment_values: tuple
    decorations: int
    nearby_spirits: list = field(default_factory=list)
    durability_remaining: int = 0
    unclaimed_offerings: int = 0


@dataclass
class State:
    sites: dict
    research_points: int
    total_unclaimed: int
    at_vale_totems: bool


def parse_state(payload):
    """Parse the reader's snapshot, or None when it cannot be trusted.

    None covers three cases that must never be mistaken for "nothing to do": the endpoint
    being unavailable (get_api returns None on an un-rebuilt client or a bridge that is down),
    the reader throwing (an "error" key instead of its fields), and a malformed body."""
    if not isinstance(payload, dict) or "error" in payload:
        return None
    raw_sites = payload.get("sites")
    if not isinstance(raw_sites, list) or not raw_sites:
        return None
    sites = {}
    for raw in raw_sites:
        number = raw.get("siteNumber")
        if number is None:
            continue
        sites[number] = Site(
            number=number,
            base_value=raw.get("baseValue", 0),
            wood=raw.get("wood") or "NONE",
            base_carved=bool(raw.get("baseCarved")),
            segment_values=tuple((raw.get(k) or {}).get("value", 0)
                                 for k in ("bottom", "middle", "top")),
            decorations=raw.get("decorations", 0),
            nearby_spirits=[s for s in (raw.get("nearbySpirits") or [])
                            if s in SPIRIT_VALUES],
            durability_remaining=raw.get("durabilityRemaining", 0),
            unclaimed_offerings=raw.get("unclaimedOfferings", 0),
        )
    if not sites:
        return None
    return State(sites=sites,
                 research_points=payload.get("researchPoints", 0),
                 total_unclaimed=payload.get("totalUnclaimedOfferings", 0),
                 at_vale_totems=bool(payload.get("atValeTotems")))


def carved_spirits(site) -> set:
    """The spirits already carved into this totem's three segments."""
    carved = set()
    for name, value in SPIRIT_VALUES.items():
        if value + CARVED_OFFSET in site.segment_values:
            carved.add(name)
    return carved


def missing_spirits(site) -> list:
    """Nearby spirits not yet carved, in the reader's own order.

    The wiki is explicit that the nearby spirits can change WHILE the player is carving, so
    this must be re-derived from a fresh read before every carve rather than computed once at
    build time."""
    done = carved_spirits(site)
    return [s for s in site.nearby_spirits if s not in done]


def needs_build(site) -> bool:
    return site.base_value == 0


def needs_carve(site) -> bool:
    """The SERVER owns "carving is finished" -- baseCarved, not the segment count.

    A totem can show three carved segments while baseCarved is still false; trusting the
    segments would leave the totem unfinished and the decorate step would refuse."""
    return not needs_build(site) and not site.base_carved


def needs_decorate(site) -> bool:
    return (not needs_build(site) and site.base_carved
            and site.decorations < MAX_DECORATIONS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/vale_totems.py ControllerAddon/tests/test_vale_totems.py
git commit -m "vale totems: parse the reader and re-derive the predicates Gson drops"
```

---

### Task 4: The per-site ladder and the circuit decision

Still pure. This is where "what do I do here" and "where do I go next" are decided.

**Files:**
- Modify: `ControllerAddon/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems.py`

**Interfaces:**
- Consumes: Task 3's `Site`, predicates; Task 2's `vale_totem_route.RING`, `next_in_ring`
- Produces:
  - `vale_totems.LOGS_PER_TOTEM = 5`
  - `vale_totems.next_action(site, *, logs: int, bows: int) -> tuple[str, object]` — one of `("claim", None)`, `("build", None)`, `("carve", "<SPIRIT>")`, `("decorate", None)`, `("done", None)`
  - `vale_totems.site_is_finished(site) -> bool`
  - `vale_totems.logs_needed_for_lap(state) -> int`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems.py
import vale_totem_route as vtr


def test_claim_outranks_everything_so_offerings_are_never_left_behind():
    site = vt.parse_state(_payload(_site(unclaimedOfferings=65))).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("claim", None)


def test_build_is_chosen_once_the_pile_is_claimed():
    site = vt.parse_state(_payload(_site())).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("build", None)


def test_build_is_not_chosen_without_a_log():
    site = vt.parse_state(_payload(_site())).sites[1]
    assert vt.next_action(site, logs=0, bows=10) == ("done", None)


def test_carve_names_the_first_missing_spirit():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW",
        bottom={"value": 10, "spirit": "BUFFALO"},
        nearbySpirits=["BUFFALO", "JAGUAR", "SNAKE"]))).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("carve", "JAGUAR")


def test_decorate_is_skipped_with_no_bows_so_the_routine_moves_on_instead_of_spinning():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=1))).sites[1]
    assert vt.next_action(site, logs=10, bows=2) == ("decorate", None)
    assert vt.next_action(site, logs=10, bows=0) == ("done", None)


def test_a_finished_totem_is_done():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=4,
        durabilityRemaining=5))).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("done", None)
    assert vt.site_is_finished(site) is True


def test_logs_needed_for_a_lap_counts_only_the_work_still_outstanding():
    payload = _payload(
        _site(siteNumber=1),                                             # unbuilt: 1 + 4
        _site(siteNumber=2, baseValue=4, wood="YEW", baseCarved=True,
              decorations=4, durabilityRemaining=5),                     # finished: 0
    )
    assert vt.logs_needed_for_lap(vt.parse_state(payload)) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems.py -v`
Expected: FAIL — `AttributeError: module 'vale_totems' has no attribute 'next_action'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/vale_totems.py`:

```python
# One log builds the totem, four more become the decorations. Wiki: 40 logs for all eight.
LOGS_PER_TOTEM = 5


def site_is_finished(site) -> bool:
    """Built, carved and fully decorated -- nothing left to do but let the ents visit."""
    return (not needs_build(site) and site.base_carved
            and site.decorations >= MAX_DECORATIONS)


def next_action(site, *, logs: int, bows: int):
    """The single next thing to do at `site`, as (action, argument).

    A LADDER, re-derived from live state every call rather than a script -- which is what lets
    a still-standing totem with durability left fall straight through to ("done", None) and be
    passed. The recording rebuilds every site every lap only because a yew totem's 5 durability
    is spent by the time the ~4.4-minute lap comes round; that is a property of the lap time,
    not a rule.

    Claim outranks everything: the pile is already earned, and a build on a capped pile is
    refused by the game anyway.

    Running out of a material yields ("done", None), NOT a retry -- the caller walks on and the
    bank rung deals with it. A rung that cannot progress must never hold the routine in place.
    """
    if site.unclaimed_offerings > 0:
        return ("claim", None)
    if needs_build(site):
        return ("build", None) if logs > 0 else ("done", None)
    if needs_carve(site):
        missing = missing_spirits(site)
        if missing:
            return ("carve", missing[0])
        # baseCarved is false but nothing nearby is missing: the spirits rotated under us.
        # Fall through rather than guess -- the next read will show the new trio.
        return ("done", None)
    if needs_decorate(site):
        return ("decorate", None) if bows > 0 else ("done", None)
    return ("done", None)


def logs_needed_for_lap(state) -> int:
    """Logs required to finish every site that still has work outstanding.

    Used only to decide whether a bank trip is due, so it counts the WORK LEFT rather than
    assuming a full lap: a site already built needs its decorations but not its build log."""
    total = 0
    for site in state.sites.values():
        if site_is_finished(site):
            continue
        if needs_build(site):
            total += LOGS_PER_TOTEM
        else:
            total += max(0, MAX_DECORATIONS - site.decorations)
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/vale_totems.py ControllerAddon/tests/test_vale_totems.py
git commit -m "vale totems: the per-site ladder and the lap log budget"
```

---

### Task 5: Routine skeleton — config, state read, and the outside-the-valley stop

**Files:**
- Create: `ControllerAddon/routines/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems_routine.py`

**Interfaces:**
- Consumes: `vale_totems.parse_state`, `controller.get_api`, `debug_log`
- Produces:
  - `routines.vale_totems.DEFAULT_CONFIG: dict`
  - `routines.vale_totems.WOODS: dict[str, dict]` — per wood `{"log", "bow", "level"}`
  - `routines.vale_totems.get_config(bridge_config) -> dict`
  - `routines.vale_totems.read_state() -> vale_totems.State | None`
  - `routines.vale_totems.setup(ctx) -> None`
  - `routines.vale_totems.tick(ctx) -> None`
  - `routines.vale_totems._now`, `_sleep` (test seams, blast_furnace convention)

- [ ] **Step 1: Write the failing test**

```python
# ControllerAddon/tests/test_vale_totems_routine.py
import pytest

import controller
from routines import vale_totems as vtr_routine


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)


def test_default_config_is_yew():
    cfg = vtr_routine.get_config(None)
    assert cfg["wood"] == "YEW"
    assert cfg["log"] == "Yew logs"
    assert cfg["bow"] == "Yew longbow (u)"


def test_the_bridge_config_wins_over_the_default():
    cfg = vtr_routine.get_config({"valeTotemsWood": "MAPLE"})
    assert cfg["wood"] == "MAPLE"
    assert cfg["log"] == "Maple logs"
    assert cfg["bow"] == "Maple longbow (u)"


def test_an_unknown_wood_falls_back_to_the_default_rather_than_crashing():
    cfg = vtr_routine.get_config({"valeTotemsWood": "PLASTIC"})
    assert cfg["wood"] == "YEW"


def test_read_state_returns_none_when_the_endpoint_is_unavailable():
    assert vtr_routine.read_state() is None


def test_tick_safety_stops_when_the_player_is_outside_the_valley(monkeypatch):
    payload = {"sites": [{"siteNumber": n, "baseValue": 0, "wood": "NONE",
                          "baseCarved": False,
                          "bottom": {"value": 0}, "middle": {"value": 0}, "top": {"value": 0},
                          "decorations": 0, "nearbySpirits": [], "durabilityRemaining": 0,
                          "unclaimedOfferings": 0} for n in range(1, 9)],
               "researchPoints": 0, "totalUnclaimedOfferings": 0, "atValeTotems": False}
    monkeypatch.setattr(controller, "get_api", lambda g, r: payload)
    stops = []
    ctx = _ctx(monkeypatch, stops)
    vtr_routine.tick(ctx)
    assert stops, "a player outside the valley must stop, not improvise -- there is no router"


def _ctx(monkeypatch, stops):
    class _State:
        def __init__(self):
            self.data = {}

        def trigger_safety_stop(self, reason):
            stops.append(reason)

    class _Ctx:
        def __init__(self):
            self.ser = object()
            self.state = _State()
            self.events = None
            self.config = vtr_routine.get_config(None)

    return _Ctx()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'routines.vale_totems'`

- [ ] **Step 3: Write the implementation**

Create `ControllerAddon/routines/vale_totems.py` with the module docstring recording the measured facts (copy the spec's "what the activity is", the 270-title gate and the worldmap finding), then:

```python
import time

import controller
import debug_log
import skillmulti
import vale_totems as vt
import vale_totem_route as route

LOG = "vale_totems"

# Per wood: the inventory log name, the inventory name of the decoration item, and the
# Fletching level the wiki requires to build that totem. The MENU label is derived from the
# bow name by skillmulti.menu_label -- the menu names the finished item ("Yew longbow"), the
# inventory receives the unstrung one ("Yew longbow (u)").
WOODS = {
    "OAK":     {"log": "Oak logs",     "bow": "Oak longbow (u)",     "level": 20},
    "WILLOW":  {"log": "Willow logs",  "bow": "Willow longbow (u)",  "level": 35},
    "MAPLE":   {"log": "Maple logs",   "bow": "Maple longbow (u)",   "level": 50},
    "YEW":     {"log": "Yew logs",     "bow": "Yew longbow (u)",     "level": 65},
    "MAGIC":   {"log": "Magic logs",   "bow": "Magic longbow (u)",   "level": 80},
}

DEFAULT_CONFIG = {"wood": "YEW"}

_now = time.monotonic
_sleep = time.sleep


def get_config(bridge_config):
    wood = (bridge_config or {}).get("valeTotemsWood") or DEFAULT_CONFIG["wood"]
    wood = str(wood).upper()
    if wood not in WOODS:
        debug_log.log(LOG, "unknown wood %r -- falling back to %s"
                      % (wood, DEFAULT_CONFIG["wood"]))
        wood = DEFAULT_CONFIG["wood"]
    cfg = dict(WOODS[wood])
    cfg["wood"] = wood
    return cfg


def read_state():
    """The whole minigame in one request. None means "cannot verify", never "nothing to do"."""
    return vt.parse_state(controller.get_api("minigames", "vale-totems"))


def setup(ctx):
    debug_log.log(LOG, "setup: %s totems, circuit %s"
                  % (ctx.config["wood"], "->".join(str(n) for n in route.RING)))


def tick(ctx):
    state = read_state()
    if state is None:
        debug_log.log(LOG, "no vale-totems reader -- is the client up?")
        return
    if not state.at_vale_totems:
        ctx.state.trigger_safety_stop(
            "not in the Auburn Valley. This routine has no router -- worldmap has no "
            "collision data for Varlamore -- so it cannot walk back. Start it in the valley.")
        return
    # Later tasks add the ladder here.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/vale_totems.py ControllerAddon/tests/test_vale_totems_routine.py
git commit -m "vale totems: routine skeleton, wood config and the outside-the-valley stop"
```

---

### Task 6: Claim and Build

**Files:**
- Modify: `ControllerAddon/routines/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems_routine.py`

**Interfaces:**
- Consumes: `controller.get_object_at`, `controller.object_refresh_target`, `controller.click_entity`, `outcome_waits.await_pre`
- Produces:
  - `routines.vale_totems.site_object(site_number, which) -> dict | None` — `which` is `"totem"` or `"offerings"`
  - `routines.vale_totems.claim(ctx, site) -> bool`
  - `routines.vale_totems.build(ctx, site) -> bool`
  - `routines.vale_totems.CLAIM_BUDGET_S = 6.0`, `BUILD_BUDGET_S = 6.0`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems_routine.py
import vale_totems as vt
import vale_totem_route as route


def _one_site(**kw):
    base = {"siteNumber": 6, "baseValue": 0, "wood": "NONE", "baseCarved": False,
            "bottom": {"value": 0}, "middle": {"value": 0}, "top": {"value": 0},
            "decorations": 0, "nearbySpirits": ["BUFFALO", "JAGUAR", "SNAKE"],
            "durabilityRemaining": 0, "unclaimedOfferings": 0}
    base.update(kw)
    return vt.parse_state({"sites": [base], "researchPoints": 0,
                           "totalUnclaimedOfferings": 0, "atValeTotems": True}).sites[6]


def test_site_object_looks_up_the_offerings_pile_by_its_own_id(monkeypatch):
    seen = {}

    def fake(x, y, plane, obj_id, radius=None, inst=False):
        seen.update(x=x, y=y, obj_id=obj_id)
        return {"screenX": 1, "screenY": 2, "id": obj_id}

    monkeypatch.setattr(controller, "get_object_at", fake)
    obj = vtr_routine.site_object(6, "offerings")
    assert seen["obj_id"] == route.SITES[6]["offerings_id"] == 57050
    assert (seen["x"], seen["y"]) == route.SITES[6]["offerings"]
    assert obj is not None


def test_claim_confirms_on_the_pile_emptying_not_on_the_click(monkeypatch):
    site = _one_site(unclaimedOfferings=65)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    reads = iter([65, 65, 0])
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            unclaimedOfferings=next(reads))}})())
    assert vtr_routine.claim(_ctx_only(), site) is True


def test_claim_reports_failure_when_the_pile_never_empties(monkeypatch):
    site = _one_site(unclaimedOfferings=65)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            unclaimedOfferings=65)}})())
    assert vtr_routine.claim(_ctx_only(), site) is False


def test_build_confirms_on_the_base_value_leaving_zero(monkeypatch):
    site = _one_site()
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            baseValue=4, wood="YEW")}})())
    assert vtr_routine.build(_ctx_only(), site) is True


def test_a_refused_click_is_not_reported_as_success(monkeypatch):
    site = _one_site()
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: False)
    assert vtr_routine.build(_ctx_only(), site) is False


def _ctx_only():
    class _State:
        def __init__(self):
            self.data = {}

        def trigger_safety_stop(self, reason):
            raise AssertionError("unexpected safety stop: %s" % reason)

    class _Ctx:
        def __init__(self):
            self.ser = object()
            self.state = _State()
            self.events = None
            self.config = vtr_routine.get_config(None)

    return _Ctx()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: FAIL — `AttributeError: module 'routines.vale_totems' has no attribute 'site_object'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/vale_totems.py` (add `import outcome_waits` at the top):

```python
CLAIM_BUDGET_S = 6.0
BUILD_BUDGET_S = 6.0


def site_object(site_number: int, which: str):
    """The totem base or the offerings pile at `site_number`, by ID on its own known tile.

    BY ID, NEVER BY NAME: the base multiloc is called "Totem site" before it is built and
    "Yew totem" both while it needs carving AND while it needs decorating, so the name cannot
    separate the last two states -- the Wintertodt brazier lesson. The tile and the id are both
    fixed and measured, so /object/at answers exactly one thing.
    """
    entry = route.SITES[site_number]
    x, y = entry["totem"] if which == "totem" else entry["offerings"]
    obj_id = entry["base_id"] if which == "totem" else entry["offerings_id"]
    return controller.get_object_at(x, y, route.PLANE, obj_id)


def _click_site(ctx, site_number, which, budget_s, confirm):
    """Click a site object and confirm on the READER, never on the click landing.

    The aim is re-resolved through object_refresh_target for the whole flight: these clicks are
    issued while the player is still walking up, and a click that departs toward a tick-old
    projection is the "flick back" CLAUDE.md documents."""
    entry = route.SITES[site_number]
    x, y = entry["totem"] if which == "totem" else entry["offerings"]
    obj_id = entry["base_id"] if which == "totem" else entry["offerings_id"]
    obj = site_object(site_number, which)
    if not obj:
        debug_log.log(LOG, "site %d: %s object not in view" % (site_number, which))
        return False
    refresh = controller.object_refresh_target(x, y, route.PLANE, obj_id)
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh):
        return False
    outcome = outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"], budget_s,
                                      confirm, now_fn=_now, sleep_fn=_sleep)
    return bool(outcome)


def _site_now(site_number):
    state = read_state()
    return None if state is None else state.sites.get(site_number)


def claim(ctx, site) -> bool:
    """Take the offerings pile. Confirmed on the pile reading empty."""
    n = site.number

    def done():
        fresh = _site_now(n)
        return fresh is not None and fresh.unclaimed_offerings == 0

    return _click_site(ctx, n, "offerings", CLAIM_BUDGET_S, done)


def build(ctx, site) -> bool:
    """Erect a totem. Confirmed on baseValue leaving 0 -- one log, and 15% run energy back."""
    n = site.number

    def done():
        fresh = _site_now(n)
        return fresh is not None and fresh.base_value != 0

    return _click_site(ctx, n, "totem", BUILD_BUDGET_S, done)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/vale_totems.py ControllerAddon/tests/test_vale_totems_routine.py
git commit -m "vale totems: claim and build, confirmed on the reader"
```

---

### Task 7: Carve — the title gate and the spirit menu

The most failure-prone step in the routine. The prompt shares its widget group with the fletch prompt, and the spirits can rotate between carves.

**Files:**
- Modify: `ControllerAddon/routines/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems_routine.py`

**Interfaces:**
- Consumes: `skillmulti.showing`, `skillmulti.TITLE_CARVE`, `skillmulti.item_widget`, `skillmulti.option_number`, `controller.key_tap`
- Produces:
  - `routines.vale_totems.CARVE_MENU_BUDGET_S = 4.0`, `CARVE_BUDGET_S = 6.0`
  - `routines.vale_totems.choose_spirit(ctx, spirit: str) -> bool`
  - `routines.vale_totems.carve(ctx, site) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems_routine.py
import skillmulti


def test_choose_spirit_presses_the_number_derived_from_the_matched_widget(monkeypatch):
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_CARVE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 17, "screenX": 5, "screenY": 6})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is True
    assert keys == ["3"], "option 3 == child 17, derived from the widget not from a table"


def test_choose_spirit_refuses_when_the_fletch_prompt_is_the_one_on_screen(monkeypatch):
    # THE trap: both prompts are widget group 270. Pressing a number here would fletch.
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_MAKE)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is False
    assert keys == []


def test_choose_spirit_refuses_when_no_prompt_can_be_read(monkeypatch):
    monkeypatch.setattr(skillmulti, "showing", lambda t: False)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is False
    assert keys == []


def test_choose_spirit_clicks_the_widget_when_no_number_can_be_derived(monkeypatch):
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_CARVE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 99, "screenX": 5, "screenY": 6})
    keys = []
    clicks = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    monkeypatch.setattr(controller, "click_entity",
                        lambda *a, **k: clicks.append(a) or True)
    assert vtr_routine.choose_spirit(_ctx_only(), "EAGLE") is True
    assert keys == [] and clicks


def test_carve_rereads_the_spirits_before_choosing(monkeypatch):
    # The wiki says the trio can rotate mid-carve. carve() must pick from a FRESH read, not
    # from the site object it was handed.
    stale = _one_site(baseValue=4, wood="YEW",
                      nearbySpirits=["BUFFALO", "JAGUAR", "SNAKE"])
    fresh = _one_site(baseValue=4, wood="YEW",
                      nearbySpirits=["EAGLE", "SCORPION", "SNAKE"])
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: fresh}})())
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    chosen = []
    monkeypatch.setattr(vtr_routine, "choose_spirit",
                        lambda ctx, s: chosen.append(s) or True)
    monkeypatch.setattr(vtr_routine, "_await_menu", lambda ctx: True)
    vtr_routine.carve(_ctx_only(), stale)
    assert chosen == ["EAGLE"], "carve used the stale trio instead of re-reading"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: FAIL — `AttributeError: module 'routines.vale_totems' has no attribute 'choose_spirit'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/vale_totems.py`:

```python
CARVE_MENU_BUDGET_S = 4.0
CARVE_BUDGET_S = 6.0


def _await_menu(ctx) -> bool:
    """Wait for the CARVE prompt specifically."""
    outcome = outcome_waits.await_pre(
        ctx, ["WIDGET_LOADED"], CARVE_MENU_BUDGET_S,
        lambda: skillmulti.showing(skillmulti.TITLE_CARVE),
        now_fn=_now, sleep_fn=_sleep)
    return bool(outcome)


def choose_spirit(ctx, spirit: str) -> bool:
    """Pick `spirit` off the carve menu.

    TWO SAFEGUARDS, both load-bearing:

    1. The TITLE GATE. The carve prompt and the fletch prompt are the SAME widget group (270),
       so a number pressed without checking 270.5's text can answer the wrong one -- fletching
       a bow when we meant to carve, or carving when we meant to fletch. An unreadable title
       counts as "not the carve prompt" and refuses: withholding costs a tick, guessing costs
       a wrong carve, and a wrong animal is -25% XP and far fewer offerings for the whole totem.

    2. The number is DERIVED FROM THE MATCHED WIDGET, never from a table. skillmulti.item_widget
       finds the option by name and option_number turns its child into the digit, so the key
       stays tied to the animal's NAME. That is fletching.py's own rule ("the human's '3' is
       only correct for the product they happened to pick") applied to this menu.
    """
    if not skillmulti.showing(skillmulti.TITLE_CARVE):
        debug_log.log(LOG, "carve menu not on screen (title=%r) -- withholding"
                      % skillmulti.prompt_title())
        return False
    widget = skillmulti.item_widget(spirit.capitalize())
    if not widget:
        debug_log.log(LOG, "carve menu has no %s option" % spirit)
        return False
    option = skillmulti.option_number(widget)
    if option is not None:
        controller.key_tap(ctx.ser, str(option))
        return True
    return bool(controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                        check_ui_blockers=False, simulate_mistakes=False))


def carve(ctx, site) -> bool:
    """Carve ONE spirit into the totem.

    Re-reads the site immediately before choosing, because the wiki says the nearby trio can
    change WHILE the player is carving -- so the spirit picked at build time may already be
    wrong by the third carve. One carve per call; the caller loops on needs_carve.
    """
    n = site.number
    fresh = _site_now(n) or site
    missing = vt.missing_spirits(fresh)
    if not missing:
        return False
    spirit = missing[0]

    obj = site_object(n, "totem")
    if not obj:
        debug_log.log(LOG, "site %d: totem not in view" % n)
        return False
    entry = route.SITES[n]
    refresh = controller.object_refresh_target(entry["totem"][0], entry["totem"][1],
                                              route.PLANE, entry["base_id"])
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh):
        return False
    if not _await_menu(ctx):
        debug_log.log(LOG, "site %d: carve menu never opened" % n)
        return False
    if not choose_spirit(ctx, spirit):
        return False

    before = set(fresh.segment_values)

    def landed():
        after = _site_now(n)
        return after is not None and set(after.segment_values) != before

    return bool(outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"],
                                        CARVE_BUDGET_S, landed,
                                        now_fn=_now, sleep_fn=_sleep))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/vale_totems.py ControllerAddon/tests/test_vale_totems_routine.py
git commit -m "vale totems: carve, gated on the prompt title and re-reading the spirits"
```

---

### Task 8: Decorate, and the fletch that overlaps the walk

**Files:**
- Modify: `ControllerAddon/routines/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems_routine.py`

**Interfaces:**
- Consumes: `skillmulti.showing`/`item_widget`/`option_number`, `controller.get_inventory`, `controller.open_inventory_tab`, `controller.click_entity`
- Produces:
  - `routines.vale_totems.DECORATE_BUDGET_S = 6.0`, `FLETCH_MENU_BUDGET_S = 4.0`
  - `routines.vale_totems.count_item(name) -> int`
  - `routines.vale_totems.find_item(name) -> dict | None`
  - `routines.vale_totems.decorate(ctx, site) -> bool`
  - `routines.vale_totems.start_fletching(ctx) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems_routine.py
def test_count_item_counts_slots_not_distinct_names(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Yew logs"}, {"name": "Yew logs"},
                                 {"name": "Knife"}])
    assert vtr_routine.count_item("Yew logs") == 2


def test_decorate_confirms_on_the_decoration_count_rising(monkeypatch):
    site = _one_site(baseValue=4, wood="YEW", baseCarved=True, decorations=1)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(vtr_routine, "read_state",
                        lambda: type("S", (), {"sites": {6: _one_site(
                            baseValue=4, wood="YEW", baseCarved=True,
                            decorations=2)}})())
    assert vtr_routine.decorate(_ctx_only(), site) is True


def test_start_fletching_answers_the_make_prompt_never_the_carve_prompt(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Yew logs", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_CARVE)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.start_fletching(_ctx_only()) is False
    assert keys == [], "answered the carve prompt as if it were the fletch prompt"


def test_start_fletching_derives_the_bow_option_from_the_menu(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Yew logs", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_MAKE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 17, "screenX": 5, "screenY": 6})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    assert vtr_routine.start_fletching(_ctx_only()) is True
    assert keys == ["3"]


def test_start_fletching_never_presses_space(monkeypatch):
    # Space repeats the LAST CRAFTED item -- session state we do not own. A fresh login or one
    # manual fletch of something else and it silently makes the wrong thing all run.
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Knife", "screenX": 1, "screenY": 1},
                                 {"name": "Yew logs", "screenX": 2, "screenY": 2}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(skillmulti, "showing", lambda t: t == skillmulti.TITLE_MAKE)
    monkeypatch.setattr(skillmulti, "item_widget",
                        lambda name: {"child": 17, "screenX": 5, "screenY": 6})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    vtr_routine.start_fletching(_ctx_only())
    assert "space" not in [k.lower() for k in keys]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: FAIL — `AttributeError: module 'routines.vale_totems' has no attribute 'count_item'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/vale_totems.py`:

```python
DECORATE_BUDGET_S = 6.0
FLETCH_MENU_BUDGET_S = 4.0


def count_item(name: str) -> int:
    """Slots holding `name`. Counts SLOTS, not distinct names -- 11 logs is 11 slots."""
    want = name.lower()
    return sum(1 for i in (controller.get_inventory() or [])
               if (i.get("name") or "").lower() == want)


def find_item(name: str):
    want = name.lower()
    for item in controller.get_inventory() or []:
        if (item.get("name") or "").lower() == want:
            return item
    return None


def decorate(ctx, site) -> bool:
    """Attach one decoration. Confirmed on the reader's decoration count rising."""
    n = site.number
    before = site.decorations

    def landed():
        fresh = _site_now(n)
        return fresh is not None and fresh.decorations > before

    return _click_site(ctx, n, "totem", DECORATE_BUDGET_S, landed)


def start_fletching(ctx) -> bool:
    """Knife a log and pick the bow, then RETURN -- the walk overlaps the fletching.

    MEASURED 2026-09-08: fletching continues while running (five bows delivered across a
    30-tile sprint), one product every 1.8s. So this is fired as the last act at a site and the
    leg walk runs underneath it; nothing waits here.

    NEVER `Space`: it repeats the last crafted item, which is session state we do not own.
    The option is matched by NAME and its digit derived from the matched widget, so a wood
    whose menu differs cannot silently make the wrong thing.
    """
    knife = find_item("Knife")
    log = find_item(ctx.config["log"])
    if not knife or not log:
        return False
    controller.open_inventory_tab(ctx.ser)
    if not controller.click_entity(ctx.ser, knife, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False, expected_action="Use"):
        return False
    if not controller.click_entity(ctx.ser, log, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False):
        return False
    outcome = outcome_waits.await_pre(
        ctx, ["WIDGET_LOADED"], FLETCH_MENU_BUDGET_S,
        lambda: skillmulti.showing(skillmulti.TITLE_MAKE),
        now_fn=_now, sleep_fn=_sleep)
    if not outcome:
        debug_log.log(LOG, "make menu never opened (title=%r)" % skillmulti.prompt_title())
        return False
    widget = skillmulti.item_widget(ctx.config["bow"])
    if not widget:
        debug_log.log(LOG, "make menu has no %s option" % ctx.config["bow"])
        return False
    option = skillmulti.option_number(widget)
    if option is not None:
        controller.key_tap(ctx.ser, str(option))
        return True
    return bool(controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                        check_ui_blockers=False, simulate_mistakes=False))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/vale_totems.py ControllerAddon/tests/test_vale_totems_routine.py
git commit -m "vale totems: decorate, and the fletch that overlaps the walk"
```

---

### Task 9: Walking a leg by waypoint hops

**Files:**
- Modify: `ControllerAddon/routines/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems_routine.py`

**Interfaces:**
- Consumes: `travel._hop_to`, `controller.get_player`, `vale_totem_route.leg_waypoints`
- Produces:
  - `routines.vale_totems.ARRIVAL_TOLERANCE = 4`
  - `routines.vale_totems.at_site(site_number) -> bool`
  - `routines.vale_totems.walk_leg(ctx, a: int, b: int) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems_routine.py
import travel


def test_at_site_is_true_within_tolerance_of_the_stand_tile(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1477, "y": 3331, "plane": 0})
    assert vtr_routine.at_site(6) is True
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1400, "y": 3300, "plane": 0})
    assert vtr_routine.at_site(6) is False


def test_walk_leg_hops_every_recorded_waypoint_in_order(monkeypatch):
    hops = []
    monkeypatch.setattr(travel, "_hop_to",
                        lambda ctx, x, y, plane, **k: hops.append((x, y)) or True)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1438, "y": 3307, "plane": 0})
    assert vtr_routine.walk_leg(_ctx_only(), 6, 5) is True
    assert hops == list(route.leg_waypoints(6, 5))


def test_walk_leg_stops_at_the_first_hop_that_fails(monkeypatch):
    hops = []

    def hop(ctx, x, y, plane, **k):
        hops.append((x, y))
        return len(hops) < 3

    monkeypatch.setattr(travel, "_hop_to", hop)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 0, "y": 0, "plane": 0})
    assert vtr_routine.walk_leg(_ctx_only(), 6, 5) is False
    assert len(hops) == 3


def test_walk_leg_refuses_a_leg_with_no_recorded_path(monkeypatch):
    monkeypatch.setattr(travel, "_hop_to",
                        lambda *a, **k: pytest.fail("should not hop an unrecorded leg"))
    assert vtr_routine.walk_leg(_ctx_only(), 6, 3) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: FAIL — `AttributeError: module 'routines.vale_totems' has no attribute 'at_site'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/vale_totems.py` (add `import travel` at the top):

```python
ARRIVAL_TOLERANCE = 4


def at_site(site_number: int) -> bool:
    player = controller.get_player() or {}
    x, y = player.get("x"), player.get("y")
    if x is None or y is None:
        return False
    sx, sy = route.SITES[site_number]["stand"]
    return abs(x - sx) <= ARRIVAL_TOLERANCE and abs(y - sy) <= ARRIVAL_TOLERANCE


def walk_leg(ctx, a: int, b: int) -> bool:
    """Walk site `a` -> site `b` by hopping the recorded waypoints.

    NO ROUTER: worldmap has no collision data for Varlamore, so travel.walk_to cannot be used
    here at all. These are the human's own tick-by-tick positions, so the path is known-good;
    the risk is only that a discrete hop lands differently than a continuous run, which is what
    the live checklist watches at the two log balances.
    """
    points = route.leg_waypoints(a, b)
    if not points:
        debug_log.log(LOG, "no recorded path for leg %d->%d" % (a, b))
        return False
    for x, y in points:
        if not travel._hop_to(ctx, x, y, route.PLANE):
            debug_log.log(LOG, "leg %d->%d: hop to (%d,%d) failed" % (a, b, x, y))
            return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/vale_totems.py ControllerAddon/tests/test_vale_totems_routine.py
git commit -m "vale totems: walk a leg by the recorded waypoints"
```

---

### Task 10: Banking — the log basket in both directions

**Files:**
- Modify: `ControllerAddon/routines/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems_routine.py`

**Interfaces:**
- Consumes: `banker.open_bank`, `banker.withdraw_all_shift`, `controller.click_menu_action`, `controller.key_tap`
- Produces:
  - `routines.vale_totems.BASKET_NAMES = ("Log basket", "Open log basket")`
  - `routines.vale_totems.BANK_ROUNDS = 3`
  - `routines.vale_totems.find_basket() -> dict | None`
  - `routines.vale_totems.empty_basket(ctx) -> bool`
  - `routines.vale_totems.fill_basket(ctx) -> bool`
  - `routines.vale_totems.bank_trip(ctx) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems_routine.py
import banker


def test_find_basket_matches_either_open_or_shut(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    assert vtr_routine.find_basket() is not None
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Log basket", "screenX": 1, "screenY": 1}])
    assert vtr_routine.find_basket() is not None
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Knife"}])
    assert vtr_routine.find_basket() is None


def test_empty_basket_uses_the_named_menu_option_not_a_bare_click(monkeypatch):
    # A bare left click races Fill / Wear / Check, and Fill is a silent no-op that looks like
    # success -- the same reason banker.empty_log_basket selects its option by name.
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Open log basket", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, opt, **k: calls.append(opt) or True)
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 5)
    vtr_routine.empty_basket(_ctx_only())
    # A SEQUENCE of candidate names, the banker.empty_log_basket idiom -- click_menu_action
    # tries each against the one menu it reads back.
    assert calls and any("empty" in o.lower() for o in calls[0])


def test_empty_basket_is_a_no_op_without_a_basket(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Knife"}])
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: pytest.fail("clicked with no basket"))
    assert vtr_routine.empty_basket(_ctx_only()) is False


def test_bank_trip_withdraws_and_fills_for_every_round(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1, "screenY": 2})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **k: True)
    withdrawals = []
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, fresh=False: withdrawals.append(name) or True)
    fills = []
    monkeypatch.setattr(vtr_routine, "fill_basket",
                        lambda ctx: fills.append(1) or True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    assert vtr_routine.bank_trip(_ctx_only()) is True
    assert withdrawals == ["Yew logs"] * vtr_routine.BANK_ROUNDS
    # The last round has nowhere to put the logs, so it does not fill.
    assert len(fills) == vtr_routine.BANK_ROUNDS - 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: FAIL — `AttributeError: module 'routines.vale_totems' has no attribute 'find_basket'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/vale_totems.py` (add `import banker`):

```python
# Cache: the shut basket is "Log basket" (28140) and the open one "Open log basket" (28142).
BASKET_NAMES = ("Log basket", "Open log basket")
BANK_ROUNDS = 3          # recording: withdraw/fill three times -> ~28 in the basket, ~23 loose
BASKET_BUDGET_S = 4.0


def find_basket():
    for item in controller.get_inventory() or []:
        if (item.get("name") or "") in BASKET_NAMES:
            return item
    return None


def _basket_option(ctx, options, expect_change) -> bool:
    """Select a NAMED option on the basket, never a bare left click.

    The basket offers Fill / Wear / Check / Open|Close / Destroy and a bare click races them --
    and Fill is a SILENT NO-OP that looks like success. banker.empty_log_basket already selects
    by name for exactly this reason, and `options` is passed as a SEQUENCE for the same reason
    it does: click_menu_action takes one, tries each against the single menu it reads back, and
    the exact wording is the thing the live checklist has to confirm.

    Note click_menu_action has no simulate_mistakes parameter -- it reads the real menu and
    clicks a named entry, so there is no aim to miss."""
    basket = find_basket()
    if not basket:
        return False
    controller.open_inventory_tab(ctx.ser)
    if not controller.click_menu_action(ctx.ser, basket, options, offset=(0, 0),
                                        check_ui_blockers=False):
        return False
    return bool(outcome_waits.await_pre(ctx, ["INVENTORY_CHANGED"], BASKET_BUDGET_S,
                                        expect_change, now_fn=_now, sleep_fn=_sleep))


def empty_basket(ctx) -> bool:
    """Basket -> inventory, in the field ("You empty as many logs as you can carry.")."""
    before = count_item(ctx.config["log"])
    return _basket_option(ctx, ("empty", "empty-to-inventory"),
                          lambda: count_item(ctx.config["log"]) > before)


def fill_basket(ctx) -> bool:
    """Inventory -> basket, at the bank."""
    before = count_item(ctx.config["log"])
    return _basket_option(ctx, ("fill",),
                          lambda: count_item(ctx.config["log"]) < before)


def bank_trip(ctx) -> bool:
    """Withdraw-All logs, close, Fill the basket -- three rounds, the recording's own sequence.

    "You don't have enough inventory space to withdraw that many." is NORMAL here and must not
    be treated as a failure; fletching.py records the same thing for the same click.
    """
    entry = route.BANK
    booth = controller.get_object_at(entry["tile"][0], entry["tile"][1], route.PLANE,
                                     entry["object_id"])
    if not booth:
        debug_log.log(LOG, "bank booth not in view")
        return False
    for round_index in range(BANK_ROUNDS):
        if not banker.open_bank(ctx, booth):
            return False
        if not banker.withdraw_all_shift(ctx, ctx.config["log"], fresh=True):
            return False
        controller.key_tap(ctx.ser, "escape")
        if round_index < BANK_ROUNDS - 1:
            fill_basket(ctx)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: PASS (28 tests)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/vale_totems.py ControllerAddon/tests/test_vale_totems_routine.py
git commit -m "vale totems: bank trip, and the log basket driven both ways"
```

---

### Task 11: The tick loop, the stuck watch and `before_break`

**Files:**
- Modify: `ControllerAddon/routines/vale_totems.py`
- Test: `ControllerAddon/tests/test_vale_totems_routine.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `routines.vale_totems.SITE_MAX_ACTIONS = 12`, `STALL_STOP_TICKS = 40`
  - `routines.vale_totems.work_site(ctx, site_number) -> bool`
  - `routines.vale_totems.progress_signature(state) -> tuple`
  - `routines.vale_totems.before_break(ctx) -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems_routine.py
def test_work_site_runs_the_ladder_until_the_site_is_done(monkeypatch):
    calls = []
    seq = iter([("claim", None), ("build", None), ("carve", "EAGLE"),
                ("decorate", None), ("done", None)])
    monkeypatch.setattr(vt, "next_action", lambda site, **k: next(seq))
    monkeypatch.setattr(vtr_routine, "claim", lambda c, s: calls.append("claim") or True)
    monkeypatch.setattr(vtr_routine, "build", lambda c, s: calls.append("build") or True)
    monkeypatch.setattr(vtr_routine, "carve", lambda c, s: calls.append("carve") or True)
    monkeypatch.setattr(vtr_routine, "decorate",
                        lambda c, s: calls.append("decorate") or True)
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: _one_site())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 10)
    vtr_routine.work_site(_ctx_only(), 6)
    assert calls == ["claim", "build", "carve", "decorate"]


def test_work_site_stops_after_a_failed_action_rather_than_retrying_forever(monkeypatch):
    calls = []
    monkeypatch.setattr(vt, "next_action", lambda site, **k: ("build", None))
    monkeypatch.setattr(vtr_routine, "build", lambda c, s: calls.append("build") or False)
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: _one_site())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 10)
    vtr_routine.work_site(_ctx_only(), 6)
    assert len(calls) == 1


def test_work_site_is_bounded_even_when_every_action_reports_success(monkeypatch):
    calls = []
    monkeypatch.setattr(vt, "next_action", lambda site, **k: ("carve", "EAGLE"))
    monkeypatch.setattr(vtr_routine, "carve", lambda c, s: calls.append(1) or True)
    monkeypatch.setattr(vtr_routine, "_site_now", lambda n: _one_site())
    monkeypatch.setattr(vtr_routine, "count_item", lambda name: 10)
    vtr_routine.work_site(_ctx_only(), 6)
    assert len(calls) == vtr_routine.SITE_MAX_ACTIONS


def test_progress_signature_changes_when_any_site_advances():
    a = vt.parse_state({"sites": [{"siteNumber": 1, "baseValue": 0, "wood": "NONE",
                                   "baseCarved": False, "bottom": {"value": 0},
                                   "middle": {"value": 0}, "top": {"value": 0},
                                   "decorations": 0, "nearbySpirits": [],
                                   "durabilityRemaining": 0, "unclaimedOfferings": 0}],
                        "researchPoints": 0, "totalUnclaimedOfferings": 0,
                        "atValeTotems": True})
    b = vt.parse_state({"sites": [{"siteNumber": 1, "baseValue": 4, "wood": "YEW",
                                   "baseCarved": False, "bottom": {"value": 0},
                                   "middle": {"value": 0}, "top": {"value": 0},
                                   "decorations": 0, "nearbySpirits": [],
                                   "durabilityRemaining": 5, "unclaimedOfferings": 0}],
                        "researchPoints": 0, "totalUnclaimedOfferings": 0,
                        "atValeTotems": True})
    assert vtr_routine.progress_signature(a) != vtr_routine.progress_signature(b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -v`
Expected: FAIL — `AttributeError: module 'routines.vale_totems' has no attribute 'work_site'`

- [ ] **Step 3: Write the implementation**

Append to `ControllerAddon/routines/vale_totems.py`, and replace the `tick` body from Task 5:

```python
SITE_MAX_ACTIONS = 12     # claim + build + 3 carves + 4 decorates is 9; 12 is a ceiling
STALL_STOP_TICKS = 40

# Action name -> the module-level function that performs it. Resolved through globals() at
# CALL time rather than bound into a dict at import time, so that monkeypatching
# routines.vale_totems.build (which every test of this loop does) actually takes effect. A
# dict built at import captures the original function objects and silently ignores the patch.
_ACTIONS = ("claim", "build", "carve", "decorate")


def work_site(ctx, site_number: int) -> bool:
    """Run the ladder at one site until it reports done, an action fails, or the ceiling.

    The ceiling is load-bearing: every action here confirms against the reader, so an action
    that reports success without advancing the totem would otherwise loop forever. A failed
    action ends the visit rather than retrying -- the caller walks on and the next lap sees the
    site again, which is cheaper than spinning in front of it.
    """
    for _ in range(SITE_MAX_ACTIONS):
        site = _site_now(site_number)
        if site is None:
            return False
        action, argument = vt.next_action(
            site, logs=count_item(ctx.config["log"]), bows=count_item(ctx.config["bow"]))
        if action == "done":
            return True
        handler = globals().get(action) if action in _ACTIONS else None
        if handler is None or not handler(ctx, site):
            debug_log.log(LOG, "site %d: %s did not land" % (site_number, action))
            return False
    debug_log.log(LOG, "site %d: hit the action ceiling" % site_number)
    return False


def progress_signature(state):
    """What "the routine is getting somewhere" looks like.

    Deliberately EXCLUDES unclaimedOfferings: ents add to those piles on their own every 48s,
    so including them would let a wedged routine look busy -- the same reason Wintertodt's
    signature excludes passively-regenerating warmth."""
    return tuple((s.number, s.base_value, s.base_carved, s.segment_values, s.decorations)
                 for s in sorted(state.sites.values(), key=lambda s: s.number))


def tick(ctx):
    state = read_state()
    if state is None:
        debug_log.log(LOG, "no vale-totems reader -- is the client up?")
        return
    if not state.at_vale_totems:
        ctx.state.trigger_safety_stop(
            "not in the Auburn Valley. This routine has no router -- worldmap has no "
            "collision data for Varlamore -- so it cannot walk back. Start it in the valley.")
        return

    data = ctx.state.data
    signature = progress_signature(state)
    if signature == data.get("vt_signature"):
        data["vt_stalled"] = data.get("vt_stalled", 0) + 1
        if data["vt_stalled"] >= STALL_STOP_TICKS:
            ctx.state.trigger_safety_stop(
                "no totem progress in %d ticks" % STALL_STOP_TICKS)
            return
    else:
        data["vt_signature"] = signature
        data["vt_stalled"] = 0

    current = data.get("vt_site")
    if current is None:
        current = min(route.RING, key=lambda n: 0 if at_site(n) else 1)
        data["vt_site"] = current

    if not at_site(current):
        previous = data.get("vt_from", route.RING[route.RING.index(current) - 1])
        if not walk_leg(ctx, previous, current):
            ctx.state.trigger_safety_stop(
                "could not walk leg %s->%s" % (previous, current))
        return

    if count_item(ctx.config["log"]) == 0:
        if find_basket() and empty_basket(ctx):
            return
        if not bank_trip(ctx):
            ctx.state.trigger_safety_stop("out of %s and the bank trip failed"
                                          % ctx.config["log"])
        return

    work_site(ctx, current)
    start_fletching(ctx)
    data["vt_from"] = current
    data["vt_site"] = route.next_in_ring(current)


def before_break(ctx):
    """Finish the site we are standing at before the break starts.

    Cheap insurance rather than a play-out: an unfinished totem simply earns nothing until the
    next lap, so there is no GOTR-style round to protect -- but leaving mid-carve with the
    make menu open would have the break start on an open interface."""
    current = ctx.state.data.get("vt_site")
    if current is not None and at_site(current):
        work_site(ctx, current)
```

- [ ] **Step 4: Run the whole suite**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems.py tests/test_vale_totems_routine.py tests/test_vale_totem_route.py tests/test_skillmulti.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/vale_totems.py ControllerAddon/tests/test_vale_totems_routine.py
git commit -m "vale totems: the tick loop, the stuck watch and before_break"
```

---

### Task 12: Wire it into the GUI, and document it

Per CLAUDE.md, a routine file alone is only launchable from the command line. Both the enum and the map are required, and the Java half needs a client restart.

**Files:**
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Create: `src/main/java/com/example/controller/ValeTotemWood.java`
- Modify: `src/main/java/com/example/controller/ControllerConfig.java`
- Modify: `src/main/java/com/example/controller/ConfigExporter.java`
- Modify: `ControllerAddon/controller.py` (the `ROUTINE_FILES` map)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `routines.vale_totems.get_config`'s `valeTotemsWood` key
- Produces: `RoutineChoice.VALE_TOTEMS`, config key `valeTotemsWood`

- [ ] **Step 1: Write the failing test**

```python
# append to ControllerAddon/tests/test_vale_totems_routine.py
def test_the_routine_is_wired_into_the_gui_map():
    import controller as c
    assert c.ROUTINE_FILES.get("VALE_TOTEMS") == "routines/vale_totems.py"


def test_the_enum_and_the_map_agree():
    import pathlib
    import re
    import controller as c
    java = pathlib.Path(__file__).resolve().parents[2] / "src/main/java/com/example/controller/RoutineChoice.java"
    names = set(re.findall(r"^\t([A-Z_]+)\(", java.read_text(), re.M))
    assert "VALE_TOTEMS" in names
    assert set(c.ROUTINE_FILES) <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_vale_totems_routine.py -k gui -v`
Expected: FAIL — `assert None == 'routines/vale_totems.py'`

- [ ] **Step 3: Write the implementation**

In `RoutineChoice.java`, add before the closing `;`:

```java
	VALE_TOTEMS("Vale Totems (Forestry)"),
```

In `controller.py`'s `ROUTINE_FILES`, add:

```python
    "VALE_TOTEMS": "routines/vale_totems.py",
```

Create `src/main/java/com/example/controller/ValeTotemWood.java`. Only the five woods the routine
has names for — REDWOOD is deliberately absent because redwood logs cannot be fletched into the
bows the decorate step needs:

```java
package com.example.controller;

/** Which log routines/vale_totems.py builds and decorates totems with. */
public enum ValeTotemWood
{
	OAK("Oak"),
	WILLOW("Willow"),
	MAPLE("Maple"),
	YEW("Yew"),
	MAGIC("Magic");

	private final String label;

	ValeTotemWood(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}
```

In `ControllerConfig.java`, add the section beside the existing `@ConfigSection` declarations
(take the next free `position`) and the item itself:

```java
	@ConfigSection(name = "Vale Totems", description = "Forestry totem circuit", position = 20)
	String valeTotemsSection = "valeTotems";

	@ConfigItem(
		keyName = "valeTotemsWood",
		name = "Totem wood",
		description = "Which log to build and decorate Vale Totems with.",
		section = valeTotemsSection
	)
	default ValeTotemWood valeTotemsWood()
	{
		return ValeTotemWood.YEW;
	}
```

In `ConfigExporter.java`, export it as the enum NAME beside the other routine keys — the Python
side upper-cases whatever arrives and looks it up in `WOODS`, falling back to YEW on anything it
does not recognise, so the two sides cannot disagree fatally:

```java
		data.put("valeTotemsWood", config.valeTotemsWood().name());
```

Then add a "Current state of routines/vale_totems.py" section to `CLAUDE.md` recording: the reader-not-methods trap, the shared 270 group and its title gate, never-`Space`, the Varlamore collision-map gap, and the spec's live-verification checklist.

- [ ] **Step 4: Verify**

Run: `cd ControllerAddon && python -m pytest tests/ -q`
Expected: PASS. Then `./gradlew.bat compileJava` from the repo root — expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example ControllerAddon/controller.py ControllerAddon/tests/test_vale_totems_routine.py CLAUDE.md
git commit -m "vale totems: wire into the Routine dropdown, add the wood config, document"
```

---

## Post-implementation: the first live session

Nothing in this plan has touched a running client. The spec's live-verification checklist is the
first session's worklist, in this order (each one blocks the next):

1. `GET /api/minigames/vale-totems` still answers after the rebuild, and `atValeTotems` is true.
2. The two prompt titles read exactly as expected via `/widget?group=270&child=5`.
3. One site completes: claim -> build -> 3 carves -> 4 decorates, with the reader confirming each.
4. One leg walks cleanly, then the two legs carrying the log balances (4->3 and 8->7).
5. A bank trip fills the basket, and a field `Empty` refills the inventory.
6. A full lap unattended.
