# Hunllef Fight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A routine that, started at the Hunllef's barrier with supplies in hand, passes the barrier, kills the Corrupted Hunllef unattended, opens the reward chest and stops.

**Architecture:** Pure fight logic lives in `ControllerAddon/hunllef.py` (a style tracker fed by the Hunllef's animations, a per-tick snapshot, safe-tile selection, and a priority ladder), all unit-tested offline including a replay of the human recording. `routines/corrupted_gauntlet.py` is the thin I/O layer: it reads one HTTP snapshot per game tick, runs the ladder, performs the top rung with the existing click primitives, and loops on tick boundaries without returning to the engine until the phase changes. Sub-project 1's instance-space coordinates, `instance_nav.InstanceMap`, and the NPC/animation events are consumed, not extended, except for one tiny `EventStream.since()` accessor.

**Tech Stack:** Python 3 controller (`pytest` from `ControllerAddon/`), one Java enum entry (`RoutineChoice`) needing a client rebuild.

**Spec:** `docs/superpowers/specs/2026-09-05-hunllef-fight-design.md` (read its "Facts this rests on" first; every number below comes from there).

## Global Constraints

- Every fact acted on comes from HTTP; UDP events only shorten waits or feed the tracker, and the tracker is cross-checked against snapshot animation transitions.
- Style model: `ANIM_ATTACK_LIKE = {8419, 8418}` count attacks; `ANIM_MARKER = {8754, 8755}` flip the style (RANGED <-> MAGIC) and reset the count; 8420 ignored; style starts RANGED; `next_style_due()` is true once 4 attacks have been counted since the last marker.
- Hunllef prayer = NPC id: 9035 MELEE, 9036 RANGED, 9037 MAGIC, 9038 DEAD (crystalline 9021-9024 in the same order, assumed).
- Stomp guard: the Hunllef footprint is Chebyshev <= 2 from its centre (size 5); every walk target the routine picks must be Chebyshev >= 4 from the centre (footprint + 1 margin); the escape rung fires when the centre is within 2 of us.
- Prayer names on the bridge are `Prayer` enum names (`PROTECT_FROM_MISSILES`, `PROTECT_FROM_MAGIC`, `DEADEYE`, `MYSTIC_MIGHT`, `RIGOUR`, `AUGURY`, ...): `/prayers.active` and `/prayers.icons` are both keyed by them; icons exist only while `tabOpen`.
- Interface clicks (prayer icons, inventory items) pass `check_ui_blockers=False, simulate_mistakes=False, offset=(0, 0)`; walk clicks to a ground tile are SHIFT-clicked through `travel._click_walk_point(ctx, point, shift=True)`.
- One action per game tick in the fight loop; the loop waits on `ctx.events.wait_ticks(1)` when events exist, else `time.sleep(0.6)`.
- Tests are hermetic: every test module that imports `controller` stubs the bridge calls it uses (monkeypatch), never reaching the live client. Do not run the full `tests/` directory per task (real sleeps); run the named files.
- Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File map

| File | Responsibility |
|---|---|
| `ControllerAddon/gauntlet_ids.py` | + `HUNLLEF_PRAYER_BY_ID`, `ANIM_MARKER`, `HUNLLEF_SIZE`, `FLOOR_WARNING`/`FLOOR_HIT`, item name tuples. |
| `ControllerAddon/bridge_events.py` | + `EventStream.since(seq)`. |
| `ControllerAddon/hunllef.py` (new) | Pure fight logic: `StyleTracker`, `FightSnapshot`, footprint/flood/`pick_safe_tile`/`in_danger`, decisions, `ladder`. |
| `ControllerAddon/routines/corrupted_gauntlet.py` (new) | The routine: config, snapshot I/O, actions, phases, fight loop. |
| `ControllerAddon/tests/test_gauntlet_ids.py`, `test_bridge_events.py`, `test_hunllef.py` (new), `test_corrupted_gauntlet.py` (new) | Tests. |
| `ControllerAddon/controller.py` | `ROUTINE_FILES["CORRUPTED_GAUNTLET"]`. |
| `src/main/java/com/example/controller/RoutineChoice.java` | `CORRUPTED_GAUNTLET("Corrupted Gauntlet")`. |
| `CLAUDE.md` | "Current state of routines/corrupted_gauntlet.py" section. |

---

### Task 1: id table additions

**Files:**
- Modify: `ControllerAddon/gauntlet_ids.py`
- Test: `ControllerAddon/tests/test_gauntlet_ids.py` (append)

**Interfaces:**
- Produces: `HUNLLEF_PRAYER_BY_ID: dict[int, str]` (values `"MELEE"|"RANGED"|"MAGIC"|"DEAD"`), `ANIM_MARKER = {8754, 8755}`, `HUNLLEF_SIZE = 5`, `FLOOR_WARNING = 36047`, `FLOOR_HIT = 36048`, `FISH_NAMES = ("Corrupted paddlefish", "Paddlefish")`, `EGNIOL_PREFIX = "Egniol potion"`, `BOW_NAMES`, `STAFF_NAMES` (tuples, perfected first), `BARRIER_QUICK_PASS = "Quick-pass"`.
- Existing (unchanged): `ANIM_ATTACK_LIKE = {8419, 8418}`, `HUNLLEF`, `TORNADO`, `OBJ_BARRIER`, `OBJ_REWARD_CHEST`, `VARBIT_FIGHTING_HUNLLEF`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gauntlet_ids.py`)

```python
def test_hunllef_prayer_by_id_covers_both_variants():
    assert g.HUNLLEF_PRAYER_BY_ID[9035] == "MELEE"
    assert g.HUNLLEF_PRAYER_BY_ID[9036] == "RANGED"
    assert g.HUNLLEF_PRAYER_BY_ID[9037] == "MAGIC"
    assert g.HUNLLEF_PRAYER_BY_ID[9038] == "DEAD"
    assert set(g.HUNLLEF_PRAYER_BY_ID) == g.HUNLLEF
    assert g.HUNLLEF_PRAYER_BY_ID[9022] == "RANGED"


def test_fight_constants():
    assert g.ANIM_MARKER == {g.ANIM_SWITCH_STYLE, g.ANIM_SPECIAL_TORNADO}
    assert not (g.ANIM_MARKER & g.ANIM_ATTACK_LIKE)
    assert g.HUNLLEF_SIZE == 5
    assert g.FLOOR_WARNING == 36047 and g.FLOOR_HIT == 36048
    assert g.HAZARD_TILES == {g.FLOOR_WARNING, g.FLOOR_HIT}
    assert g.FISH_NAMES[0] == "Corrupted paddlefish"
    assert g.BOW_NAMES[0] == "Corrupted bow (perfected)"
    assert g.STAFF_NAMES[0] == "Corrupted staff (perfected)"
```

- [ ] **Step 2: Run to verify it fails**

Run (from `ControllerAddon/`): `python -m pytest tests/test_gauntlet_ids.py -q`
Expected: 2 failures, `AttributeError: ... has no attribute 'HUNLLEF_PRAYER_BY_ID'`.

- [ ] **Step 3: Add the constants** (append to `gauntlet_ids.py`, after the existing NPC section)

```python
# --- Hunllef prayer state = NPC id (cache symbols crystal_hunllef_{melee,ranged,magic,death}_hm
#     for 9035-9038; the crystalline 9021-9024 are ASSUMED to follow the same order -- verify on a
#     regular-Gauntlet run before relying on them). REC: every weapon swap followed an id flip. ---
HUNLLEF_PRAYER_BY_ID = {
    9021: "MELEE", 9022: "RANGED", 9023: "MAGIC", 9024: "DEAD",
    9035: "MELEE", 9036: "RANGED", 9037: "MAGIC", 9038: "DEAD",
}
HUNLLEF_SIZE = 5                  # cache size=5: footprint is centre +-2

# --- Style-cycle markers (REC, measured in ticks): four ANIM_ATTACK_LIKE, then two ticks later
#     one of these, then the next style's first attack three ticks after it. 8754 ("switch
#     style") and 8755 (tornado cast) alternate as the marker. ---
ANIM_MARKER = {ANIM_SWITCH_STYLE, ANIM_SPECIAL_TORNADO}

# --- Floor (cache loc symbols prif_gauntlet_floor_tile_01_{warning,hit}_hm) ---
FLOOR_WARNING = 36047             # crimson: about to hurt
FLOOR_HIT = 36048                 # orange: 10-20 per tick while stood on

# --- Items (REC inventory deltas: bow 23857, staff 23854, paddlefish 23874, corrupted 25958,
#     egniol 23883/23884) -- matched by NAME, perfected first ---
FISH_NAMES = ("Corrupted paddlefish", "Paddlefish")
EGNIOL_PREFIX = "Egniol potion"
BOW_NAMES = ("Corrupted bow (perfected)", "Corrupted bow (attuned)", "Corrupted bow (basic)",
             "Crystal bow (perfected)", "Crystal bow (attuned)", "Crystal bow (basic)")
STAFF_NAMES = ("Corrupted staff (perfected)", "Corrupted staff (attuned)", "Corrupted staff (basic)",
               "Crystal staff (perfected)", "Crystal staff (attuned)", "Crystal staff (basic)")
BARRIER_QUICK_PASS = "Quick-pass"  # REC: the menu option on OBJ_BARRIER that starts the fight
```

Check `HAZARD_TILES` is still `{36047, 36048}` (it is defined earlier in the file); do not redefine it.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gauntlet_ids.py -q` -- expected: all pass (the earlier tests included).

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/gauntlet_ids.py ControllerAddon/tests/test_gauntlet_ids.py
git commit -m "gauntlet_ids: Hunllef prayer-by-id, style markers, floor and item names

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `EventStream.since(seq)`

**Files:**
- Modify: `ControllerAddon/bridge_events.py` (class `EventStream`, next to `mark()` ~line 291)
- Test: `ControllerAddon/tests/test_bridge_events.py` (append; the file exists since sub-project 1)

**Interfaces:**
- Produces: `EventStream.since(seq: int) -> list[tuple[str, dict | None]]` -- pumps once, then returns `(event_type, payload)` for every buffered event with sequence number > `seq`, oldest first. Pairs with `mark()`: `m = events.mark(); ...; for t, p in events.since(m)`.

Why: `routine.on_event` is dispatched by the engine loop (`controller.py` ~line 3226) and so never runs while `tick()` holds the fight loop. `pump()` returns only what arrived in that call, and `drain()` would steal main's dispatch queue. Reading `_recent` after a mark is the one non-destructive way for a routine to see every event of the last tick.

- [ ] **Step 1: Write the failing test**

Look at how `tests/test_bridge_events.py` builds an `EventStream` with a fake socket/`controller.poll_udp` (the sub-project 1 tests do this); reuse that fixture/helper. Then:

```python
def test_since_returns_everything_after_the_mark_oldest_first(monkeypatch):
    stream, feed = _make_stream(monkeypatch)   # the module's existing helper: feed(list_of_(type, payload))
    feed([("TICK", {"tick": 1}), ("NPC_SPAWNED", {"id": 9039})])
    m = stream.mark()
    feed([("NPC_TRACKED_ANIMATION_CHANGED", {"animationId": 8419}),
          ("CHAT_MESSAGE", {"message": "Your prayers have been disabled!"})])
    got = stream.since(m)
    assert [t for t, _ in got] == ["NPC_TRACKED_ANIMATION_CHANGED", "CHAT_MESSAGE"]
    assert got[0][1]["animationId"] == 8419
    assert stream.since(stream.mark()) == []
```

If the module has no `_make_stream` helper, write one at the top of the test file: monkeypatch `controller.poll_udp` to pop from a list the test appends to, and construct `EventStream` the way the existing tests do.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_bridge_events.py -q -k since` -- expected `AttributeError: 'EventStream' object has no attribute 'since'`.

- [ ] **Step 3: Implement** (in `EventStream`, after `mark()`)

```python
    def since(self, seq):
        """Every buffered event with a sequence number after `seq` (from mark()), oldest first,
        as (event_type, payload). Pumps once so the answer is current. Non-destructive: main()'s
        drain() still dispatches these to on_event/handle_generic_events later. Added 2026-09-05
        for the Hunllef fight loop, which holds tick() for the whole fight and therefore never
        receives on_event, but must see every tracked-animation event of the last tick."""
        self.pump()
        return [(event_type, payload) for s, event_type, payload in self._recent if s > seq]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_bridge_events.py -q` -- expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/bridge_events.py ControllerAddon/tests/test_bridge_events.py
git commit -m "bridge_events: EventStream.since(mark) for routines that hold the tick loop

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `StyleTracker` with the recording replay

**Files:**
- Create: `ControllerAddon/hunllef.py`
- Test: `ControllerAddon/tests/test_hunllef.py`

**Interfaces:**
- Produces: `RANGED = "RANGED"`, `MAGIC = "MAGIC"`, `PROTECT_FOR = {RANGED: "PROTECT_FROM_MISSILES", MAGIC: "PROTECT_FROM_MAGIC"}`, `class StyleTracker` with `style`, `attacks`, `markers`, `resyncs`, `observe(anim) -> "attack" | "marker" | None`, `observe_snapshot(anim)` (transition-only feed), `next_style_due() -> bool`, `upcoming_style() -> str`, `expected_prayer() -> str`.

- [ ] **Step 1: Write the failing tests**

```python
"""hunllef.py: pure Hunllef-fight logic. Spec: docs/superpowers/specs/2026-09-05-hunllef-fight-design.md."""
import hunllef as h
import gauntlet_ids as g

# The Corrupted Hunllef's animation sequence from recordings/2026-09-05_014024_corrupted-gauntlet-
# humanrun1 (ticks since varbit 9177 went to 1, animation id), and the human's protection-prayer
# clicks (tick, Prayer enum name). Copied here so the test is hermetic.
ANIMS = [(7.0, 8419), (11.9, 8419), (16.9, 8419), (22.0, 8419), (24.0, 8754), (27.0, 8419), (32.0, 8419), (37.0, 8419), (42.1, 8419), (44.0, 8755), (47.0, 8418), (52.0, 8419), (57.0, 8419), (62.0, 8419), (64.0, 8754), (67.0, 8419), (72.0, 8419), (77.0, 8419), (82.0, 8419), (84.0, 8755), (87.0, 8419), (92.0, 8419), (97.0, 8419), (102.0, 8419), (104.0, 8754), (107.0, 8418), (112.0, 8419), (117.0, 8419), (122.0, 8419), (124.0, 8755), (127.0, 8419), (132.0, 8419), (137.0, 8419), (142.0, 8419), (144.0, 8754), (147.0, 8419), (152.0, 8418), (157.0, 8419), (162.0, 8419), (164.0, 8755), (167.0, 8419), (172.0, 8419), (177.0, 8419), (182.0, 8419), (184.0, 8754), (187.0, 8419), (192.0, 8419), (197.0, 8419), (202.0, 8419), (204.0, 8755), (207.0, 8419), (212.0, 8419), (217.0, 8418), (222.0, 8419), (224.0, 8754), (227.0, 8419), (232.0, 8419), (237.0, 8419), (242.0, 8419), (244.0, 8755), (247.0, 8419), (252.0, 8419), (257.0, 8419), (262.0, 8419), (264.0, 8754), (267.0, 8419), (272.0, 8419), (277.0, 8419), (282.0, 8419), (284.0, 8755), (287.0, 8418), (292.0, 8419), (297.0, 8419), (302.0, 8420), (307.0, 8419), (309.0, 8754), (312.0, 8419), (317.0, 8419), (322.0, 8419), (327.0, 8419), (329.0, 8755), (332.0, 8419), (337.0, 8419), (342.0, 8419), (347.0, 8419), (349.0, 8754), (352.0, 8419), (357.0, 8418), (362.0, 8419), (367.0, 8419), (369.0, 8755), (372.0, 8419), (377.0, 8419), (382.1, 8419), (387.0, 8419), (389.0, 8754), (392.0, 8419), (397.0, 8419), (402.0, 8419), (407.0, 8419), (409.0, 8755), (412.0, 8419), (417.0, 8419), (422.0, 8418), (427.0, 8419), (429.0, 8754), (432.0, 8419), (437.0, 8419), (442.0, 8419), (447.0, 8419), (449.0, 8755), (452.0, 8419), (457.0, 8419), (462.0, 8419), (467.0, 8419), (469.0, 8754), (472.0, 8419), (477.0, 8419)]
PRAYER_CLICKS = [(24.1, "PROTECT_FROM_MAGIC"), (34.9, "PROTECT_FROM_MAGIC"), (45.3, "PROTECT_FROM_MISSILES"), (65.7, "PROTECT_FROM_MAGIC"), (86.5, "PROTECT_FROM_MISSILES"), (104.4, "PROTECT_FROM_MAGIC"), (125.4, "PROTECT_FROM_MISSILES"), (144.5, "PROTECT_FROM_MAGIC"), (164.8, "PROTECT_FROM_MISSILES"), (184.7, "PROTECT_FROM_MAGIC"), (189.5, "PROTECT_FROM_MAGIC"), (204.6, "PROTECT_FROM_MISSILES"), (226.9, "PROTECT_FROM_MAGIC"), (235.1, "PROTECT_FROM_MAGIC"), (236.1, "PROTECT_FROM_MAGIC"), (244.1, "PROTECT_FROM_MISSILES"), (265.0, "PROTECT_FROM_MAGIC"), (279.5, "PROTECT_FROM_MAGIC"), (285.0, "PROTECT_FROM_MISSILES"), (317.5, "PROTECT_FROM_MAGIC"), (329.2, "PROTECT_FROM_MISSILES"), (348.8, "PROTECT_FROM_MAGIC"), (355.1, "PROTECT_FROM_MAGIC"), (374.4, "PROTECT_FROM_MISSILES"), (389.5, "PROTECT_FROM_MAGIC"), (400.1, "PROTECT_FROM_MAGIC"), (409.4, "PROTECT_FROM_MISSILES"), (429.4, "PROTECT_FROM_MAGIC"), (449.6, "PROTECT_FROM_MISSILES"), (469.4, "PROTECT_FROM_MAGIC")]


def test_starts_ranged_and_flips_on_each_marker():
    t = h.StyleTracker()
    assert t.style == h.RANGED and t.expected_prayer() == "PROTECT_FROM_MISSILES"
    for _ in range(4):
        assert t.observe(8419) == "attack"
    assert t.next_style_due() and t.expected_prayer() == "PROTECT_FROM_MAGIC"
    assert t.observe(8754) == "marker"
    assert t.style == h.MAGIC and t.attacks == 0 and not t.next_style_due()
    for a in (8418, 8419, 8419, 8419):
        t.observe(a)
    assert t.observe(8755) == "marker"       # the tornado cast is a marker too
    assert t.style == h.RANGED and t.resyncs == 0


def test_stomp_and_idle_are_ignored_and_short_counts_resync():
    t = h.StyleTracker()
    assert t.observe(8420) is None and t.observe(-1) is None and t.attacks == 0
    t.observe(8419); t.observe(8419)
    t.observe(8754)                           # marker after only 2 attacks
    assert t.style == h.MAGIC and t.resyncs == 1 and t.attacks == 0


def test_observe_snapshot_counts_transitions_only():
    t = h.StyleTracker()
    t.observe_snapshot(8419); t.observe_snapshot(8419)     # same value twice: one attack
    assert t.attacks == 1
    t.observe_snapshot(-1); t.observe_snapshot(8419)       # back to idle then attack again
    assert t.attacks == 2


def test_replay_of_the_human_kill_predicts_every_prayer_click():
    """For every protection click the human made, the tracker fed with the Hunllef's animations
    up to that instant expects exactly that prayer -- including the predictive window (after the
    4th attack, before the marker) and the re-activations after prayer-disable hits."""
    t = h.StyleTracker()
    i = 0
    for tick, name in PRAYER_CLICKS:
        while i < len(ANIMS) and ANIMS[i][0] <= tick:
            t.observe(ANIMS[i][1])
            i += 1
        assert t.expected_prayer() == name, (tick, name, t.style, t.attacks)
    for tick, anim in ANIMS[i:]:
        t.observe(anim)
    assert t.markers == sum(1 for _, a in ANIMS if a in g.ANIM_MARKER)
    assert t.resyncs == 0


def test_predictive_window_opens_on_the_fourth_attack():
    t = h.StyleTracker()
    for tick, anim in ANIMS:
        t.observe(anim)
        if tick == 22.0:
            assert t.next_style_due() and t.upcoming_style() == h.MAGIC
            assert t.expected_prayer() == "PROTECT_FROM_MAGIC"
            break
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_hunllef.py -q` -- expected `ModuleNotFoundError: No module named 'hunllef'`.

- [ ] **Step 3: Write the module**

```python
"""Pure logic for the Corrupted Hunllef fight -- no bridge, no serial, no sleeps.

Spec: docs/superpowers/specs/2026-09-05-hunllef-fight-design.md. Every constant here was measured
in recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1 or read from the game cache; the
routine (routines/corrupted_gauntlet.py) does the I/O and calls in here for every decision, so
this module is where the fight is TESTED.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import gauntlet_ids as g

RANGED = "RANGED"
MAGIC = "MAGIC"
PROTECT_FOR = {RANGED: "PROTECT_FROM_MISSILES", MAGIC: "PROTECT_FROM_MAGIC"}
ATTACKS_PER_STYLE = 4


class StyleTracker:
    """Which attack style the Hunllef is on, from its animations.

    Measured shape (spec, "Style alternates at a MARKER animation"): four attack-like animations
    (8419 standard, 8418 prayer-disabling), then a marker two ticks later (8754 "switch style" or
    8755 tornado cast, alternating), then the next style's first attack three ticks after that.
    Ranged first. The stomp (8420) sits outside the count. So:
      observe(attack)  -> attacks += 1
      observe(marker)  -> style flips, attacks = 0 (a count other than 4 is logged as a resync)
    next_style_due() is true from the 4th attack until the marker: the coming style is known
    five ticks before its first attack lands, which is when the routine switches prayer.
    """

    def __init__(self):
        self.style = RANGED
        self.attacks = 0
        self.markers = 0
        self.resyncs = 0
        self._last_snapshot_anim = None

    def observe(self, anim) -> str | None:
        if anim in g.ANIM_ATTACK_LIKE:
            self.attacks += 1
            return "attack"
        if anim in g.ANIM_MARKER:
            if self.attacks != ATTACKS_PER_STYLE:
                self.resyncs += 1
            self.style = MAGIC if self.style == RANGED else RANGED
            self.attacks = 0
            self.markers += 1
            return "marker"
        return None

    def observe_snapshot(self, anim) -> str | None:
        """Feed from a per-tick /npcs read: only a CHANGE of animation counts (the same attack
        animation is reported for several ticks). Misses an attack when the idle (-1) between two
        attacks falls between two reads; the marker resyncs that."""
        if anim == self._last_snapshot_anim:
            return None
        self._last_snapshot_anim = anim
        return self.observe(anim)

    def next_style_due(self) -> bool:
        return self.attacks >= ATTACKS_PER_STYLE

    def upcoming_style(self) -> str:
        if self.next_style_due():
            return MAGIC if self.style == RANGED else RANGED
        return self.style

    def expected_prayer(self) -> str:
        return PROTECT_FOR[self.upcoming_style()]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_hunllef.py -q` -- expected 5 passed. If the replay test fails, print the failing `(tick, name, style, attacks)` tuple and check the ANIMS list against the spec's sequence before touching the tracker; the model is measured.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/hunllef.py ControllerAddon/tests/test_hunllef.py
git commit -m "hunllef: StyleTracker, replay-tested against the human kill

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `FightSnapshot`

**Files:**
- Modify: `ControllerAddon/hunllef.py`
- Test: `ControllerAddon/tests/test_hunllef.py` (append)

**Interfaces:**
- Produces: `@dataclass FightSnapshot(hp, prayer, me, plane, my_anim, hunllef, tornadoes, hazards, prayers_active, weapon, inventory, target_id)`; `FightSnapshot.from_bridge(player, npcs, objects, prayers, equipment, inventory, combat)`; methods `fish() -> list`, `egniol() -> list`, `weapon_kind() -> "bow"|"staff"|None`, `inventory_weapon(kind) -> dict|None`, `hunllef_centre() -> tuple|None`. `hunllef` is `None` or `{"id","index","x","y","anim","hp_ratio","entity"}` in instance space.
- Module constant `FOOTPRINT_ORIGIN = "centre"` (live checklist item 1 may change it to `"sw"`).

- [ ] **Step 1: Write the failing tests**

```python
def _player(**kw):
    p = {"hp": 80, "prayer": 50, "instX": 13100, "instY": 5700, "plane": 1, "animation": -1}
    p.update(kw); return p

def _npc(id_, x, y, anim=-1, index=61584):
    return {"id": id_, "index": index, "instX": x, "instY": y, "animationId": anim, "hpRatio": 90,
            "name": "Corrupted Hunllef" if id_ in g.HUNLLEF else "x", "screenX": 500, "screenY": 400}

def _obj(id_, x, y):
    return {"id": id_, "instX": x, "instY": y, "objectType": "GroundObject", "x": 1900, "y": 5600}

def _inv(*names):
    return [{"slot": i, "id": 1, "name": n, "qty": 1, "screenX": 10 * i, "screenY": 5} for i, n in enumerate(names)]

def _snap(**kw):
    args = dict(player=_player(), npcs=[_npc(9036, 13105, 5700, 8419)], objects=[], prayers={"active": {}},
                equipment={"weapon": {"name": "Corrupted bow (perfected)"}},
                inventory=_inv("Corrupted staff (perfected)", "Paddlefish", "Egniol potion (2)"),
                combat={"targetId": 9036})
    args.update(kw)
    return h.FightSnapshot.from_bridge(**args)


def test_snapshot_reads_the_hunllef_tornadoes_and_hazards_in_instance_space():
    s = _snap(npcs=[_npc(9036, 13105, 5700, 8419), _npc(9039, 13098, 5701, index=7), _npc(9040, 13110, 5710, index=8)],
              objects=[_obj(36048, 13100, 5700), _obj(36047, 13101, 5700), _obj(12345, 13102, 5700)])
    assert s.hunllef["id"] == 9036 and s.hunllef["anim"] == 8419 and s.hunllef_centre() == (13105, 5700)
    assert s.tornadoes == [(13098, 5701)]
    assert s.hazards == {(13100, 5700): 36048, (13101, 5700): 36047}
    assert s.me == (13100, 5700) and s.target_id == 9036


def test_snapshot_item_helpers():
    s = _snap()
    assert [i["name"] for i in s.fish()] == ["Paddlefish"]
    assert [i["name"] for i in s.egniol()] == ["Egniol potion (2)"]
    assert s.weapon_kind() == "bow"
    assert s.inventory_weapon("staff")["name"] == "Corrupted staff (perfected)"
    assert s.inventory_weapon("bow") is None
    assert _snap(equipment={"weapon": None}).weapon_kind() is None


def test_snapshot_without_hunllef_and_with_dead_id():
    assert _snap(npcs=[]).hunllef is None
    assert _snap(npcs=[_npc(9038, 1, 1)]).hunllef["id"] == 9038


def test_snapshot_sw_origin_shifts_the_centre():
    s = _snap()
    assert s.hunllef_centre(origin="sw") == (13107, 5702)
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_hunllef.py -q -k snapshot`; expected `AttributeError: module 'hunllef' has no attribute 'FightSnapshot'`.

- [ ] **Step 3: Implement** (append to `hunllef.py`)

```python
FOOTPRINT_ORIGIN = "centre"   # what /npcs instX/instY is for a size-5 NPC; live checklist item 1


@dataclass
class FightSnapshot:
    """One game tick's worth of bridge reads, in instance space."""
    hp: int
    prayer: int
    me: tuple
    plane: int
    my_anim: int
    hunllef: dict | None
    tornadoes: list
    hazards: dict
    prayers_active: dict
    weapon: str | None
    inventory: list
    target_id: int | None

    @classmethod
    def from_bridge(cls, player, npcs, objects, prayers, equipment, inventory, combat):
        hun = None
        tornadoes = []
        for n in npcs or []:
            nid = n.get("id")
            if nid in g.HUNLLEF and hun is None:
                hun = {"id": nid, "index": n.get("index"), "x": n.get("instX"), "y": n.get("instY"),
                       "anim": n.get("animationId"), "hp_ratio": n.get("hpRatio"), "entity": n}
            elif nid in g.TORNADO:
                tornadoes.append((n.get("instX"), n.get("instY")))
        hazards = {(o.get("instX"), o.get("instY")): o.get("id")
                   for o in objects or [] if o.get("id") in g.HAZARD_TILES}
        weapon = (equipment or {}).get("weapon") or {}
        return cls(
            hp=int(player.get("hp") or 0), prayer=int(player.get("prayer") or 0),
            me=(player.get("instX"), player.get("instY")), plane=int(player.get("plane") or 0),
            my_anim=player.get("animation", -1), hunllef=hun, tornadoes=tornadoes, hazards=hazards,
            prayers_active=(prayers or {}).get("active") or {}, weapon=weapon.get("name") or None,
            inventory=list(inventory or []), target_id=(combat or {}).get("targetId"))

    def hunllef_centre(self, origin=None):
        if self.hunllef is None or self.hunllef["x"] is None:
            return None
        x, y = self.hunllef["x"], self.hunllef["y"]
        if (origin or FOOTPRINT_ORIGIN) == "sw":
            half = g.HUNLLEF_SIZE // 2
            return (x + half, y + half)
        return (x, y)

    def fish(self):
        return [i for i in self.inventory if i.get("name") in g.FISH_NAMES]

    def egniol(self):
        return [i for i in self.inventory if str(i.get("name", "")).startswith(g.EGNIOL_PREFIX)]

    def weapon_kind(self):
        if self.weapon in g.BOW_NAMES:
            return "bow"
        if self.weapon in g.STAFF_NAMES:
            return "staff"
        return None

    def inventory_weapon(self, kind):
        names = g.BOW_NAMES if kind == "bow" else g.STAFF_NAMES
        for name in names:                       # perfected first
            for item in self.inventory:
                if item.get("name") == name:
                    return item
        return None
```

- [ ] **Step 4: Run** -- `python -m pytest tests/test_hunllef.py -q`; expected all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/hunllef.py ControllerAddon/tests/test_hunllef.py
git commit -m "hunllef: FightSnapshot from the per-tick bridge reads

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: footprint, danger, flood, `pick_safe_tile`

**Files:**
- Modify: `ControllerAddon/hunllef.py`
- Test: `ControllerAddon/tests/test_hunllef.py` (append)

**Interfaces:**
- Produces: `FOOTPRINT_RADIUS = 2`, `FOOTPRINT_MARGIN = 1`, `cheb(a, b)`, `forbidden_near_hunllef(tile, centre) -> bool` (Chebyshev <= 3), `in_danger(snap, cfg) -> str | None` (`"hazard"`, `"tornado"`, `"under_hunllef"`), `flood(imap, start, max_steps, blocked) -> dict[tile, steps]`, `pick_safe_tile(snap, imap, cfg, require_in_range=False) -> tuple | None`, `in_attack_range(tile, centre, attack_range) -> bool`.
- Consumes: `instance_nav.InstanceMap` (`standable`, `step_ok`), from sub-project 1.
- cfg keys used: `escape_radius` (6), `attack_range` (7), `tornado_danger` (2), `tornado_avoid` (3).

- [ ] **Step 1: Write the failing tests**

```python
import instance_nav
from travel import BLOCK_MOVEMENT_OBJECT

CFG = {"escape_radius": 6, "attack_range": 7, "tornado_danger": 2, "tornado_avoid": 3}
BX, BY = 13056, 5632

def _grid(size=32, blocked=()):
    flags = [0] * (size * size)
    for gx, gy in blocked:
        flags[gy * size + gx] |= BLOCK_MOVEMENT_OBJECT
    return instance_nav.InstanceMap(BX, BY, 1, size, flags)

def G(gx, gy):
    return (BX + gx, BY + gy)

def _fight(me=G(10, 10), hun=G(16, 10), tornadoes=(), hazards=()):
    npcs = [_npc(9036, *hun, 8419)] + [_npc(9039, x, y, index=100 + i) for i, (x, y) in enumerate(tornadoes)]
    objs = [_obj(36048, x, y) for x, y in hazards]
    return _snap(player=_player(instX=me[0], instY=me[1]), npcs=npcs, objects=objs)


def test_footprint_and_margin_are_forbidden():
    c = G(16, 10)
    assert h.forbidden_near_hunllef(G(16, 10), c) and h.forbidden_near_hunllef(G(18, 12), c)
    assert h.forbidden_near_hunllef(G(19, 10), c)          # margin tile, Chebyshev 3
    assert not h.forbidden_near_hunllef(G(20, 10), c)      # Chebyshev 4: allowed


def test_in_danger_reasons():
    assert h.in_danger(_fight(), CFG) is None
    assert h.in_danger(_fight(hazards=[G(10, 10)]), CFG) == "hazard"
    assert h.in_danger(_fight(tornadoes=[G(11, 11)]), CFG) == "tornado"
    assert h.in_danger(_fight(tornadoes=[G(13, 13)]), CFG) is None
    assert h.in_danger(_fight(me=G(15, 10)), CFG) == "under_hunllef"     # inside the 5x5
    assert h.in_danger(_fight(me=G(13, 10)), CFG) is None                # margin tile: not danger


def test_flood_respects_blocked_tiles_and_max_steps():
    # A 3-tall wall of blocked tiles at x=11, y=9..11, directly east of the start.
    imap = _grid(blocked=[(11, 9), (11, 10), (11, 11)])
    d = h.flood(imap, G(10, 10), 3, blocked=set())
    assert d[G(10, 10)] == 0
    assert G(11, 10) not in d                 # blocked tile never entered
    assert d[G(10, 13)] == 3                  # three straight steps north
    assert G(10, 14) not in d                 # four steps: past max_steps
    assert G(12, 10) not in d                 # rounding the wall takes four steps
    assert G(12, 12) in d                     # (10,10)->(10,11)->(11,12)->(12,12) is 3 (open corners)


def test_pick_safe_tile_avoids_hazards_footprint_and_tornadoes_and_stays_in_range():
    imap = _grid()
    snap = _fight(me=G(10, 10), hun=G(16, 10), tornadoes=[G(8, 10)], hazards=[G(10, 10), G(11, 10), G(9, 10)])
    t = h.pick_safe_tile(snap, imap, CFG)
    assert t is not None
    assert t not in snap.hazards
    assert not h.forbidden_near_hunllef(t, snap.hunllef_centre())
    assert h.cheb(t, G(8, 10)) >= CFG["tornado_avoid"]
    assert h.in_attack_range(t, snap.hunllef_centre(), CFG["attack_range"])
    assert h.cheb(t, G(10, 10)) <= CFG["escape_radius"]


def test_pick_safe_tile_prefers_distance_from_tornadoes_then_nearness():
    imap = _grid()
    snap = _fight(me=G(10, 10), hun=G(20, 10), tornadoes=[G(10, 12)])
    t = h.pick_safe_tile(snap, imap, CFG)
    assert t[1] < G(10, 10)[1]          # moved away from the tornado (north of us), not toward it


def test_pick_safe_tile_returns_none_when_everything_is_forbidden():
    imap = _grid(size=8)
    snap = _fight(me=G(3, 3), hun=G(3, 3))          # standing under it, and its footprint+margin covers the grid
    assert h.pick_safe_tile(snap, imap, CFG) is None


def test_require_in_range_filters():
    imap = _grid()
    snap = _fight(me=G(2, 2), hun=G(30, 30))
    assert h.pick_safe_tile(snap, imap, CFG, require_in_range=True) is None
    assert h.pick_safe_tile(snap, imap, CFG) is not None
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_hunllef.py -q -k "footprint or danger or flood or safe_tile or in_range"`; expected `AttributeError`.

- [ ] **Step 3: Implement** (append to `hunllef.py`)

```python
FOOTPRINT_RADIUS = g.HUNLLEF_SIZE // 2   # 2: the 5x5 is centre +-2
FOOTPRINT_MARGIN = 1                     # one tile more, so it can step toward us without a stomp
STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def forbidden_near_hunllef(tile, centre):
    """The stomp guard: inside the footprint OR on the margin ring. Every walk target the routine
    ever picks passes through here (user requirement 2026-09-05: a stomp is up to 68)."""
    return centre is not None and cheb(tile, centre) <= FOOTPRINT_RADIUS + FOOTPRINT_MARGIN


def in_attack_range(tile, centre, attack_range):
    return centre is not None and cheb(tile, centre) - FOOTPRINT_RADIUS <= attack_range


def in_danger(snap, cfg):
    """Why we must move THIS tick, or None."""
    if snap.me in snap.hazards:
        return "hazard"
    for t in snap.tornadoes:
        if cheb(snap.me, t) <= cfg["tornado_danger"]:
            return "tornado"
    c = snap.hunllef_centre()
    if c is not None and cheb(snap.me, c) <= FOOTPRINT_RADIUS:
        return "under_hunllef"
    return None


def flood(imap, start, max_steps, blocked):
    """Tiles reachable from start in <= max_steps 8-way steps over the live grid, never entering
    `blocked`. Returns {tile: steps}. The Hunllef room is small; this is a few hundred tiles."""
    dist = {start: 0}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        d = dist[(x, y)]
        if d >= max_steps:
            continue
        for dx, dy in STEPS:
            nxt = (x + dx, y + dy)
            if nxt in dist or nxt in blocked or not imap.step_ok(x, y, dx, dy):
                continue
            dist[nxt] = d + 1
            queue.append(nxt)
    return dist


def pick_safe_tile(snap, imap, cfg, require_in_range=False):
    """The tile to run to: reachable within escape_radius, off hazards, outside the footprint and
    its margin, at least tornado_avoid from every tornado; preferring distance from tornadoes,
    then being in attack range, then nearness to us. None when nothing qualifies."""
    centre = snap.hunllef_centre()
    blocked = set(snap.hazards)
    if centre is not None:
        r = FOOTPRINT_RADIUS + FOOTPRINT_MARGIN
        blocked |= {(centre[0] + dx, centre[1] + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)}
    reach = flood(imap, snap.me, cfg["escape_radius"], blocked)
    best, best_key = None, None
    for tile, steps in reach.items():
        if tile == snap.me or tile in blocked:
            continue
        tor = min((cheb(tile, t) for t in snap.tornadoes), default=99)
        if tor < cfg["tornado_avoid"]:
            continue
        ranged = in_attack_range(tile, centre, cfg["attack_range"]) if centre is not None else True
        if require_in_range and not ranged:
            continue
        key = (-min(tor, 6), 0 if ranged else 1, steps)
        if best_key is None or key < best_key:
            best, best_key = tile, key
    return best
```

- [ ] **Step 4: Run** -- `python -m pytest tests/test_hunllef.py -q`; expected all pass. If `test_pick_safe_tile_prefers_distance_from_tornadoes_then_nearness` fails, check the sign of the tornado term (it is `-min(tor, 6)`: larger distance sorts first).

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/hunllef.py ControllerAddon/tests/test_hunllef.py
git commit -m "hunllef: footprint guard, danger detection, flood and safe-tile selection

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: decisions and the ladder

**Files:**
- Modify: `ControllerAddon/hunllef.py`
- Test: `ControllerAddon/tests/test_hunllef.py` (append)

**Interfaces:**
- Produces: `desired_weapon(hunllef_id, cfg) -> "bow"|"staff"`, `offensive_prayer_for(kind, cfg, unusable) -> str|None`, `eat_due(snap, cfg) -> bool`, `drink_due(snap, cfg) -> bool`, `ladder(snap, tracker, cfg, unusable_prayers=frozenset()) -> tuple[str, object]` with rungs `"no_hunllef"`, `"escape"`, `"prayer"` (arg: prayer name), `"wield"` (arg: kind), `"eat"`, `"drink"`, `"attack"`, `"reposition"`, `"idle"`.
- cfg keys: `eat_at` (45), `egniol_at` (15), `melee_weapon` ("bow"), `offensive_prayers` ({"bow": [...], "staff": [...]}), `attack_range` (7).

- [ ] **Step 1: Write the failing tests**

```python
FULL_CFG = dict(CFG, eat_at=45, egniol_at=15, melee_weapon="bow",
                offensive_prayers={"bow": ["RIGOUR", "DEADEYE", "EAGLE_EYE"], "staff": ["AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"]})

def _ready(**kw):
    """A snapshot where nothing needs doing: praying right, right weapon, fed, attacking, in range."""
    base = dict(player=_player(hp=80, prayer=50, instX=BX + 10, instY=BY + 10),
                npcs=[_npc(9036, BX + 16, BY + 10, 8419)],
                prayers={"active": {"PROTECT_FROM_MISSILES": True, "AUGURY": True}},
                equipment={"weapon": {"name": "Corrupted staff (perfected)"}},
                inventory=_inv("Corrupted bow (perfected)", "Paddlefish", "Egniol potion (2)"),
                combat={"targetId": 9036}, objects=[])
    base.update(kw)
    return h.FightSnapshot.from_bridge(**base)


def test_desired_weapon_is_the_style_it_is_not_praying_against():
    assert h.desired_weapon(9036, FULL_CFG) == "staff"     # praying ranged -> hit it with magic
    assert h.desired_weapon(9037, FULL_CFG) == "bow"
    assert h.desired_weapon(9035, FULL_CFG) == "bow"       # melee prayer: config default
    assert h.desired_weapon(9038, FULL_CFG) == "bow"


def test_offensive_prayer_skips_unusable_ones():
    assert h.offensive_prayer_for("bow", FULL_CFG, set()) == "RIGOUR"
    assert h.offensive_prayer_for("bow", FULL_CFG, {"RIGOUR"}) == "DEADEYE"
    assert h.offensive_prayer_for("staff", FULL_CFG, {"AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"}) is None


def test_ladder_order():
    t = h.StyleTracker()                                   # RANGED -> expects PROTECT_FROM_MISSILES
    assert h.ladder(_ready(), t, FULL_CFG)[0] == "idle"
    assert h.ladder(_ready(npcs=[]), t, FULL_CFG)[0] == "no_hunllef"
    assert h.ladder(_ready(objects=[_obj(36048, BX + 10, BY + 10)]), t, FULL_CFG)[0] == "escape"
    assert h.ladder(_ready(prayers={"active": {"AUGURY": True}}), t, FULL_CFG) == ("prayer", "PROTECT_FROM_MISSILES")
    assert h.ladder(_ready(npcs=[_npc(9037, BX + 16, BY + 10)]), t, FULL_CFG) == ("wield", "bow")
    assert h.ladder(_ready(prayers={"active": {"PROTECT_FROM_MISSILES": True}}), t, FULL_CFG) == ("prayer", "AUGURY")
    assert h.ladder(_ready(player=_player(hp=40, instX=BX + 10, instY=BY + 10)), t, FULL_CFG)[0] == "eat"
    assert h.ladder(_ready(player=_player(prayer=10, instX=BX + 10, instY=BY + 10)), t, FULL_CFG)[0] == "drink"
    assert h.ladder(_ready(combat={"targetId": None}), t, FULL_CFG)[0] == "attack"
    assert h.ladder(_ready(player=_player(instX=BX + 13, instY=BY + 10)), t, FULL_CFG)[0] == "reposition"   # margin ring
    assert h.ladder(_ready(player=_player(instX=BX + 30, instY=BY + 10)), t, FULL_CFG)[0] == "reposition"   # out of range


def test_ladder_escape_beats_everything_and_prayer_beats_weapon():
    t = h.StyleTracker()
    s = _ready(objects=[_obj(36048, BX + 10, BY + 10)], prayers={"active": {}}, npcs=[_npc(9037, BX + 16, BY + 10)],
               player=_player(hp=20, instX=BX + 10, instY=BY + 10), combat={"targetId": None})
    assert h.ladder(s, t, FULL_CFG)[0] == "escape"


def test_ladder_predictive_prayer_switch():
    t = h.StyleTracker()
    for _ in range(4):
        t.observe(8419)
    assert h.ladder(_ready(), t, FULL_CFG) == ("prayer", "PROTECT_FROM_MAGIC")


def test_eat_and_drink_fallbacks():
    assert not h.eat_due(_ready(inventory=_inv("Corrupted bow (perfected)")), FULL_CFG)          # no fish
    low = _ready(player=_player(hp=30, instX=BX + 10, instY=BY + 10), inventory=_inv("Egniol potion (1)"))
    assert h.drink_due(low, FULL_CFG)                                                             # hp low, no fish -> egniol
    assert not h.drink_due(_ready(), FULL_CFG)
    assert h.ladder(_ready(inventory=_inv("Corrupted bow (perfected)"), player=_player(hp=30, instX=BX + 10, instY=BY + 10)), h.StyleTracker(), FULL_CFG)[0] == "idle"
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_hunllef.py -q -k "desired or offensive or ladder or fallbacks"`; expected `AttributeError`.

- [ ] **Step 3: Implement** (append to `hunllef.py`)

```python
def desired_weapon(hunllef_id, cfg):
    prayer = g.HUNLLEF_PRAYER_BY_ID.get(hunllef_id)
    if prayer == "RANGED":
        return "staff"
    if prayer == "MAGIC":
        return "bow"
    return cfg.get("melee_weapon", "bow")


def offensive_prayer_for(kind, cfg, unusable):
    for name in cfg.get("offensive_prayers", {}).get(kind, []):
        if name not in unusable:
            return name
    return None


def eat_due(snap, cfg):
    return snap.hp <= cfg["eat_at"] and bool(snap.fish())


def drink_due(snap, cfg):
    if not snap.egniol():
        return False
    return snap.prayer <= cfg["egniol_at"] or (snap.hp <= cfg["eat_at"] and not snap.fish())


def ladder(snap, tracker, cfg, unusable_prayers=frozenset()):
    """The one action for this tick. Order is the spec's: escape > protection prayer > weapon >
    offensive prayer > eat/drink > attack > reposition > idle."""
    if snap.hunllef is None:
        return ("no_hunllef", None)
    if in_danger(snap, cfg):
        return ("escape", in_danger(snap, cfg))
    expected = tracker.expected_prayer()
    if not snap.prayers_active.get(expected):
        return ("prayer", expected)
    want = desired_weapon(snap.hunllef["id"], cfg)
    if snap.weapon_kind() != want and snap.inventory_weapon(want) is not None:
        return ("wield", want)
    offensive = offensive_prayer_for(snap.weapon_kind() or want, cfg, unusable_prayers)
    if offensive and not snap.prayers_active.get(offensive):
        return ("prayer", offensive)
    if eat_due(snap, cfg):
        return ("eat", None)
    if drink_due(snap, cfg):
        return ("drink", None)
    if snap.target_id not in g.HUNLLEF:
        return ("attack", None)
    centre = snap.hunllef_centre()
    if centre is not None:
        d = cheb(snap.me, centre)
        if d <= FOOTPRINT_RADIUS + FOOTPRINT_MARGIN or not in_attack_range(snap.me, centre, cfg["attack_range"]):
            return ("reposition", None)
    return ("idle", None)
```

- [ ] **Step 4: Run** -- `python -m pytest tests/test_hunllef.py -q`; expected all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/hunllef.py ControllerAddon/tests/test_hunllef.py
git commit -m "hunllef: weapon/prayer/eat decisions and the priority ladder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: the routine

**Files:**
- Create: `ControllerAddon/routines/corrupted_gauntlet.py`
- Test: `ControllerAddon/tests/test_corrupted_gauntlet.py`

**Interfaces:**
- Consumes: everything in `hunllef.py` (Tasks 3-6), `gauntlet_ids` (Task 1), `EventStream.since`/`mark`/`wait_ticks` (Task 2, sub-project 1), `instance_nav.InstanceMap.load()`, `controller.get_player/get_npcs/get_objects/get_prayers/get_equipment/get_inventory/get_combat/get_ground_walk_point/get_minimap_walk_point/open_prayer_tab/open_inventory_tab/click_entity/click_menu_action/npc_index_refresh_target/human_delay`, `travel._click_walk_point`, `debug_log.log`, `ctx.state.trigger_safety_stop`, `ctx.state.safety_stopped`, `ctx.state.data`, `ctx.ser`, `ctx.events`.
- Produces: the routine contract (`DEFAULT_CONFIG`, `get_config`, `tick`, `VARBIT_SUBSCRIPTIONS`), plus testable module functions `derive_phase(data, npcs, objects) -> str`, `read_snapshot() -> FightSnapshot | None`, `feed_tracker(tracker, snap, events_since) -> None`, and the action functions `do_escape/do_prayer/do_wield/do_eat/do_drink/do_attack/do_reposition(ctx, snap, arg, cfg) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
"""routines/corrupted_gauntlet.py -- the I/O layer over hunllef.py. Bridge fully stubbed."""
import importlib
import pytest

import controller
import hunllef as h
import gauntlet_ids as g

cg = importlib.import_module("routines.corrupted_gauntlet")


@pytest.fixture(autouse=True)
def no_live_bridge(monkeypatch):
    for name in ("get_player", "get_npcs", "get_objects", "get_prayers", "get_equipment", "get_inventory",
                 "get_combat", "get_ground_walk_point", "get_minimap_walk_point", "get_collision_grid"):
        monkeypatch.setattr(controller, name, lambda *a, _n=name, **kw: pytest.fail(f"live bridge call {_n}"))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


class _State:
    def __init__(self):
        self.data = {}
        self.safety_stopped = False
        self.stops = []

    def trigger_safety_stop(self, msg):
        self.safety_stopped = True
        self.stops.append(msg)


class _Ctx:
    def __init__(self, events=None):
        self.ser = object()
        self.state = _State()
        self.events = events


def _npc(id_, x=13105, y=5700, anim=-1, index=1):
    return {"id": id_, "index": index, "instX": x, "instY": y, "animationId": anim, "hpRatio": 90,
            "name": "Corrupted Hunllef", "screenX": 500, "screenY": 400}


def test_derive_phase():
    data = {}
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_BARRIER}]) == "enter"
    assert cg.derive_phase(data, npcs=[_npc(9036)], objects=[]) == "fight"
    assert data["cg_fight_seen"] is True
    assert cg.derive_phase(data, npcs=[_npc(9038)], objects=[]) == "fight"         # dying, still the fight phase
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "loot"
    data["cg_chest_opened"] = True
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "done"
    assert cg.derive_phase({}, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "unknown"   # no fight seen this run
    assert cg.derive_phase({}, npcs=[], objects=[]) == "unknown"


def test_read_snapshot_composes_the_seven_reads(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 70, "prayer": 40, "instX": 1, "instY": 2, "plane": 1, "animation": -1})
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [_npc(9036)])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_prayers", lambda: {"active": {"PROTECT_FROM_MISSILES": True}})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (perfected)"}})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": 9036})
    s = cg.read_snapshot()
    assert s.hp == 70 and s.hunllef["id"] == 9036 and s.weapon_kind() == "bow" and s.target_id == 9036
    monkeypatch.setattr(controller, "get_player", lambda: {})
    assert cg.read_snapshot() is None


def test_feed_tracker_prefers_events_and_falls_back_to_snapshot_transitions():
    t = h.StyleTracker()
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036, anim=8419)], [], {}, {}, [], {})
    cg.feed_tracker(t, snap, [("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": -1}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9040, "animationId": 8419})])
    assert t.attacks == 2                                   # two Hunllef attacks; the rat's is ignored
    t2 = h.StyleTracker()
    cg.feed_tracker(t2, snap, None)                         # no events stream: snapshot transition
    assert t2.attacks == 1
    cg.feed_tracker(t2, snap, None)
    assert t2.attacks == 1                                  # same animation again: no new attack


def test_do_prayer_opens_the_tab_and_clicks_the_icon(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "icons": {"PROTECT_FROM_MAGIC": {"screenX": 1, "screenY": 2}}, "active": {"PROTECT_FROM_MAGIC": True}})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: clicks.append((entity, kw)) or True)
    ctx = _Ctx()
    assert cg.do_prayer(ctx, None, "PROTECT_FROM_MAGIC", cg.DEFAULT_CONFIG)
    entity, kw = clicks[0]
    assert entity["screenX"] == 1 and kw["check_ui_blockers"] is False and kw["simulate_mistakes"] is False and kw["offset"] == (0, 0)


def test_do_prayer_marks_a_missing_icon_unusable(monkeypatch):
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "icons": {}, "active": {}})
    ctx = _Ctx()
    assert not cg.do_prayer(ctx, None, "RIGOUR", cg.DEFAULT_CONFIG)
    assert "RIGOUR" in ctx.state.data["cg_unusable_prayers"]


def test_do_escape_shift_clicks_a_safe_tile_on_screen(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: {"baseX": 13056, "baseY": 5632, "plane": 1, "size": 32, "flags": [0] * 1024})
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, plane, inst=False: {"onScreen": True, "screenX": 5, "screenY": 6, "_t": (x, y, inst)})
    monkeypatch.setattr(cg.travel, "_click_walk_point", lambda ctx, point, shift: clicks.append((point, shift)))
    snap = h.FightSnapshot.from_bridge({"instX": 13066, "instY": 5642, "plane": 1, "hp": 50, "prayer": 30},
                                       [_npc(9036, 13072, 5642, 8419)], [{"id": 36048, "instX": 13066, "instY": 5642}], {}, {}, [], {})
    assert cg.do_escape(_Ctx(), snap, "hazard", cg.DEFAULT_CONFIG)
    point, shift = clicks[0]
    assert shift is True and point["_t"][2] is True
    tx, ty = point["_t"][0], point["_t"][1]
    assert not h.forbidden_near_hunllef((tx, ty), (13072, 5642)) and (tx, ty) != (13066, 5642)


def test_do_escape_returns_false_without_a_map(monkeypatch):
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: None)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2, "plane": 1}, [_npc(9036)], [], {}, {}, [], {})
    assert not cg.do_escape(_Ctx(), snap, "tornado", cg.DEFAULT_CONFIG)


def test_do_eat_clicks_a_fish_after_opening_the_inventory(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: calls.append("inv") or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(("click", entity["name"], kw.get("expected_action"))) or True)
    inv = [{"slot": 0, "name": "Paddlefish", "screenX": 1, "screenY": 1}, {"slot": 1, "name": "Corrupted paddlefish", "screenX": 2, "screenY": 1}]
    snap = h.FightSnapshot.from_bridge({"hp": 30, "instX": 1, "instY": 2}, [_npc(9036)], [], {}, {}, inv, {})
    assert cg.do_eat(_Ctx(), snap, None, cg.DEFAULT_CONFIG)
    assert calls == ["inv", ("click", "Corrupted paddlefish", "Eat")]


def test_do_attack_escalates_a_withheld_click_to_the_menu(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda index: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append("click") or controller.CLICK_WITHHELD)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: calls.append(("menu", option)) or True)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036)], [], {}, {}, [], {})
    assert cg.do_attack(_Ctx(), snap, None, cg.DEFAULT_CONFIG)
    assert calls == ["click", ("menu", "Attack")]
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_corrupted_gauntlet.py -q`; expected `ModuleNotFoundError` (routines package: if `routines/` has no `__init__.py`, import the file the way other routine tests do -- check `tests/test_giants_foundry.py`'s import line and copy it).

- [ ] **Step 3: Write the routine**

```python
"""Corrupted Gauntlet -- sub-project 2: the Hunllef fight, door-start mode.

Spec: docs/superpowers/specs/2026-09-05-hunllef-fight-design.md. Start it standing in the room in
front of the Hunllef's Barrier with the loadout in hand (both perfected corrupted weapons, fish,
an egniol). It checks the loadout, turns the first protection prayer on, Quick-passes the
barrier, fights on a tick-chained priority ladder (hunllef.ladder), opens the Reward Chest in
the lobby, and stops. Sub-project 3 adds the maze prep in front of `enter`.

Every decision is in hunllef.py (pure, unit-tested, replay-tested). This file only reads the
bridge, performs the top rung with the existing click primitives, and waits for the next game
tick. The fight loop holds tick() for the whole fight: the engine loop's ~1.1 s per iteration
would eat the 3 s prayer window; the panic key lives on its own thread and keeps working.
"""
from __future__ import annotations

import time

import controller
import debug_log
import gauntlet_ids as g
import hunllef as h
import instance_nav
import travel

VARBIT_SUBSCRIPTIONS = (g.VARBIT_FIGHTING_HUNLLEF,)

DEFAULT_CONFIG = {
    "eat_at": 45,               # spec: correct-prayer hits are 2-10, tornado/stomp 21-38; human's low was 23
    "egniol_at": 15,
    "min_fish": 10,
    "min_egniol": 1,
    "melee_weapon": "bow",
    "offensive_prayers": {"bow": ["RIGOUR", "DEADEYE", "EAGLE_EYE"],
                          "staff": ["AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"]},
    "escape_radius": 6,
    "attack_range": 7,
    "tornado_danger": 2,
    "tornado_avoid": 3,
    "fight_max_s": 480.0,
    "objects_radius": 12,
}
NO_HUNLLEF_TICKS = 10          # consecutive fight ticks without a Hunllef before re-deriving the phase
NO_SNAPSHOT_TICKS = 20         # consecutive unreadable snapshots before a safety stop
TICK_S = 0.6
ACTION_BUDGET_S = 1.2          # confirm window for a prayer/wield/eat click (two ticks)
ENTER_BUDGET_S = 8.0


def get_config(bridge_config):
    return DEFAULT_CONFIG


# ----------------------------------------------------------------------------- reads

def read_snapshot(cfg=DEFAULT_CONFIG):
    player = controller.get_player()
    if not player or player.get("instX") is None:
        return None
    return h.FightSnapshot.from_bridge(
        player=player,
        npcs=controller.get_npcs(fresh=True),
        objects=controller.get_objects(radius=cfg["objects_radius"], fresh=True),
        prayers=controller.get_prayers(),
        equipment=controller.get_equipment(),
        inventory=controller.get_inventory(),
        combat=controller.get_combat())


def derive_phase(data, npcs, objects):
    """Phase from live state, never from a remembered default (the mahogany_homes lesson)."""
    ids = {o.get("id") for o in objects or []}
    if any(n.get("id") in g.HUNLLEF for n in npcs or []):
        data["cg_fight_seen"] = True
        return "fight"
    if g.OBJ_BARRIER in ids and not data.get("cg_fight_seen"):
        return "enter"
    if g.OBJ_REWARD_CHEST in ids and data.get("cg_fight_seen"):
        return "done" if data.get("cg_chest_opened") else "loot"
    return "unknown"


def feed_tracker(tracker, snap, events_since):
    """Events (every Hunllef animation change of the last tick) when the stream exists; the
    snapshot's animation transition otherwise. HTTP stays the truth for everything acted on --
    the tracker only decides WHEN to switch prayer, and a wrong switch costs one 40+ hit, which
    the marker resync then corrects."""
    if events_since is None:
        if snap.hunllef is not None:
            tracker.observe_snapshot(snap.hunllef["anim"])
        return
    for event_type, payload in events_since:
        if event_type == "NPC_TRACKED_ANIMATION_CHANGED" and payload and payload.get("id") in g.HUNLLEF:
            tracker.observe(payload.get("animationId"))


# ----------------------------------------------------------------------------- actions

def _unusable(ctx):
    return ctx.state.data.setdefault("cg_unusable_prayers", set())


def do_prayer(ctx, snap, name, cfg):
    if not controller.open_prayer_tab(ctx.ser):
        debug_log.log("cg", "could not open the prayer tab")
        return False
    icon = (controller.get_prayers().get("icons") or {}).get(name)
    if not icon:
        debug_log.log("cg", f"no icon for {name} -- marking it unusable this session")
        _unusable(ctx).add(name)
        return False
    controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    ok = _confirm(ctx, lambda: (controller.get_prayers().get("active") or {}).get(name))
    if not ok:
        debug_log.log("cg", f"{name} did not activate")
    return bool(ok)


def do_wield(ctx, snap, kind, cfg):
    item = snap.inventory_weapon(kind)
    if item is None:
        return False
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, item, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False,
                            expected_action="Wield")
    names = g.BOW_NAMES if kind == "bow" else g.STAFF_NAMES
    ok = _confirm(ctx, lambda: ((controller.get_equipment() or {}).get("weapon") or {}).get("name") in names)
    debug_log.log("cg", f"wield {kind}: {'ok' if ok else 'not confirmed'}")
    return bool(ok)


def _click_item(ctx, item, action):
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, item, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False,
                            expected_action=action)


def do_eat(ctx, snap, arg, cfg):
    fish = snap.fish()
    if not fish:
        return False
    fish.sort(key=lambda i: g.FISH_NAMES.index(i["name"]))     # corrupted first
    _click_item(ctx, fish[0], "Eat")
    return True                                                  # the next tick's hp/inventory read is the confirmation


def do_drink(ctx, snap, arg, cfg):
    pots = snap.egniol()
    if not pots:
        return False
    _click_item(ctx, pots[0], "Drink")
    return True


def do_attack(ctx, snap, arg, cfg):
    npc = snap.hunllef["entity"]
    refresh = controller.npc_index_refresh_target(snap.hunllef["index"])
    result = controller.click_entity(ctx.ser, npc, refresh_target=refresh, refresh_legs=2,
                                     expected_action="Attack", expected_target="Corrupted Hunllef")
    if getattr(result, "withheld", False) or not result:
        debug_log.log("cg", "attack click withheld/failed -- right-click menu")
        result = controller.click_menu_action(ctx.ser, npc, "Attack", target_hint="Hunllef", offset=(0, 0),
                                              refresh_target=refresh)
    return bool(result)


def _walk_to_tile(ctx, tile, plane):
    point = controller.get_ground_walk_point(tile[0], tile[1], plane, inst=True)
    if point.get("onScreen"):
        travel._click_walk_point(ctx, point, shift=True)
        return True
    point = controller.get_minimap_walk_point(tile[0], tile[1], plane, inst=True)
    if point.get("onMinimap"):
        travel._click_walk_point(ctx, point, shift=False)
        return True
    debug_log.log("cg", f"tile {tile} is neither on screen nor on the minimap")
    return False


def do_escape(ctx, snap, reason, cfg, require_in_range=False):
    imap = instance_nav.InstanceMap.load()
    if imap is None:
        debug_log.log("cg", "[!] no collision grid -- cannot pick an escape tile")
        return False
    tile = h.pick_safe_tile(snap, imap, cfg, require_in_range=require_in_range)
    if tile is None:
        debug_log.log("cg", f"[!] no safe tile ({reason})")
        return False
    debug_log.log("cg", f"{reason}: moving {snap.me} -> {tile}")
    return _walk_to_tile(ctx, tile, imap.plane)


def do_reposition(ctx, snap, arg, cfg):
    return do_escape(ctx, snap, "reposition", cfg, require_in_range=True)


ACTIONS = {"escape": do_escape, "prayer": do_prayer, "wield": do_wield, "eat": do_eat,
           "drink": do_drink, "attack": do_attack, "reposition": do_reposition}


def _confirm(ctx, check, budget_s=ACTION_BUDGET_S):
    """Poll `check` until truthy or the budget passes -- interface clicks have no pushed signal
    worth waiting on beyond the HTTP read itself."""
    deadline = time.time() + budget_s
    while True:
        if check():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


def _wait_tick(ctx):
    if ctx.events is not None:
        ctx.events.wait_ticks(1)
    else:
        time.sleep(TICK_S)


# ----------------------------------------------------------------------------- phases

def _log_rung(data, rung, arg, snap, tracker):
    key = (rung, arg)
    now = time.time()
    if key != data.get("cg_last_rung") or now - data.get("cg_last_rung_log", 0) > 10:
        data["cg_last_rung"] = key
        data["cg_last_rung_log"] = now
        hun = snap.hunllef
        debug_log.log("cg", f"rung={rung} arg={arg} hp={snap.hp} pray={snap.prayer} style={tracker.style} "
                            f"attacks={tracker.attacks} hunllef={hun['id'] if hun else None} "
                            f"hpRatio={hun['hp_ratio'] if hun else None} tornadoes={len(snap.tornadoes)} "
                            f"hazards={len(snap.hazards)}")


def fight_loop(ctx, cfg):
    data = ctx.state.data
    tracker = data.get("cg_tracker")
    if tracker is None:
        tracker = data["cg_tracker"] = h.StyleTracker()
    deadline = time.time() + cfg["fight_max_s"]
    misses = 0
    no_hunllef = 0
    while not ctx.state.safety_stopped:
        if time.time() >= deadline:
            ctx.state.trigger_safety_stop("cg: fight ran past fight_max_s")
            return
        mark = ctx.events.mark() if ctx.events is not None else None
        snap = read_snapshot(cfg)
        if snap is None:
            misses += 1
            if misses >= NO_SNAPSHOT_TICKS:
                ctx.state.trigger_safety_stop("cg: bridge unreadable for 20 ticks")
                return
            _wait_tick(ctx)
            continue
        misses = 0
        if snap.hp <= 0:
            ctx.state.trigger_safety_stop("cg: died to the Hunllef (nothing lost -- read the log)")
            return
        feed_tracker(tracker, snap, ctx.events.since(mark) if ctx.events is not None else None)
        rung, arg = h.ladder(snap, tracker, cfg, _unusable(ctx))
        _log_rung(data, rung, arg, snap, tracker)
        if rung == "no_hunllef":
            no_hunllef += 1
            if no_hunllef >= NO_HUNLLEF_TICKS:
                data.pop("cg_tracker", None)
                return                                   # the phase is re-derived by tick()
        else:
            no_hunllef = 0
            if rung != "idle":
                ACTIONS[rung](ctx, snap, arg, cfg)
        _wait_tick(ctx)


def enter_phase(ctx, cfg):
    snap = read_snapshot(cfg)
    if snap is None:
        return
    missing = []
    if snap.inventory_weapon("bow") is None and snap.weapon_kind() != "bow":
        missing.append("bow")
    if snap.inventory_weapon("staff") is None and snap.weapon_kind() != "staff":
        missing.append("staff")
    if len(snap.fish()) < cfg["min_fish"]:
        missing.append(f"fish ({len(snap.fish())}/{cfg['min_fish']})")
    if len(snap.egniol()) < cfg["min_egniol"]:
        missing.append("egniol")
    if missing:
        ctx.state.trigger_safety_stop("cg: loadout missing " + ", ".join(missing))
        return
    if not snap.prayers_active.get("PROTECT_FROM_MISSILES"):
        do_prayer(ctx, snap, "PROTECT_FROM_MISSILES", cfg)          # the first style is ranged
    offensive = h.offensive_prayer_for(snap.weapon_kind() or "bow", cfg, _unusable(ctx))
    if offensive and not snap.prayers_active.get(offensive):
        do_prayer(ctx, snap, offensive, cfg)
    barrier = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                    if o.get("id") == g.OBJ_BARRIER), None)
    if barrier is None:
        return
    debug_log.log("cg", "quick-passing the barrier")
    controller.click_menu_action(ctx.ser, barrier, g.BARRIER_QUICK_PASS, target_hint="Barrier", offset=(0, 0))
    _confirm(ctx, lambda: any(n.get("id") in g.HUNLLEF for n in controller.get_npcs(fresh=True)), ENTER_BUDGET_S)


def loot_phase(ctx, cfg):
    chest = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                  if o.get("id") == g.OBJ_REWARD_CHEST), None)
    if chest is None:
        return
    controller.click_entity(ctx.ser, chest, expected_action="Open")
    ctx.state.data["cg_chest_opened"] = True
    controller.human_delay(2.0, 3.0)


def tick(ctx):
    cfg = get_config(None)
    data = ctx.state.data
    npcs = controller.get_npcs(fresh=True)
    objects = controller.get_objects(radius=cfg["objects_radius"], fresh=True)
    phase = derive_phase(data, npcs, objects)
    if phase != data.get("cg_phase"):
        debug_log.log("cg", f"phase {data.get('cg_phase')} -> {phase}")
        data["cg_phase"] = phase
    if phase == "enter":
        enter_phase(ctx, cfg)
    elif phase == "fight":
        fight_loop(ctx, cfg)
    elif phase == "loot":
        loot_phase(ctx, cfg)
    elif phase == "done":
        ctx.state.trigger_safety_stop("cg: chest opened -- run complete")
    else:
        if not data.get("cg_unknown_logged"):
            debug_log.log("cg", "not at the Hunllef barrier, in the fight, or at the chest -- start at the barrier")
            data["cg_unknown_logged"] = True
        controller.human_delay(1.0, 2.0)
```

- [ ] **Step 4: Run** -- `python -m pytest tests/test_corrupted_gauntlet.py tests/test_hunllef.py -q`; expected all pass. `python -m py_compile routines/corrupted_gauntlet.py`.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/corrupted_gauntlet.py ControllerAddon/tests/test_corrupted_gauntlet.py
git commit -m "routines/corrupted_gauntlet: door-start Hunllef fight on a tick-chained ladder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: wiring, docs, full suites

**Files:**
- Modify: `ControllerAddon/controller.py` (`ROUTINE_FILES`, ~line 3094)
- Modify: `src/main/java/com/example/controller/RoutineChoice.java`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Wire the routine**

`controller.py`: add `"CORRUPTED_GAUNTLET": "routines/corrupted_gauntlet.py",` to `ROUTINE_FILES`. `RoutineChoice.java`: add `CORRUPTED_GAUNTLET("Corrupted Gauntlet")` before the final entry (keep the `;` on the last one).

- [ ] **Step 2: CLAUDE.md section**

Insert before "## Current state of routines/guardians_of_the_rift.py":

```
## Current state of routines/corrupted_gauntlet.py

Sub-project 2 of the Corrupted Gauntlet (spec `docs/superpowers/specs/2026-09-05-hunllef-fight-
design.md`), written 2026-09-05 from the human recording
`recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.*` and the cache. **Door-start mode,
not yet run live**: start it in front of the Hunllef's Barrier with both perfected weapons, fish
and an egniol; it quick-passes, fights, opens the chest, stops. The prep (sub-project 3) does
not exist yet. Facts that shape it, all MEASURED: the Hunllef attacks every 5 ticks; four
attacks then a marker animation (8754 "switch style" / 8755 tornado cast, alternating) flip the
style, ranged first -- `hunllef.StyleTracker`, replay-tested against every prayer click in the
recording; its NPC id IS its overhead prayer (9035 melee / 9036 ranged / 9037 magic / 9038
death, cache symbols); size 5, so every walk target is filtered against centre +-2 plus one
margin tile (`hunllef.forbidden_near_hunllef` -- the user's stomp concern); floor tiles are
GroundObjects 36047 warning / 36048 hit, polled from `/objects`; "Your prayers have been
disabled!" is the prayer-disable tell. The fight loop holds `tick()` on `wait_ticks(1)` and
reads events with `EventStream.since(mark)` because `on_event` is dispatched by the engine loop
and never runs while a routine holds the tick. First live session's worklist is the spec's
checklist: `instX/instY` centre-vs-corner for a size-5 NPC, 8754's meaning, the ranged/magic
projectile ids, snapshot cost per tick.
```

- [ ] **Step 3: Full suites**

From `ControllerAddon/`: `python -m pytest tests/ -q` (about a minute) -- all pass. `python -m py_compile controller.py routines/corrupted_gauntlet.py hunllef.py gauntlet_ids.py bridge_events.py`. From the repo root: `./gradlew.bat test --console=plain -q` -- exit 0 (the enum change compiles).

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/controller.py src/main/java/com/example/controller/RoutineChoice.java CLAUDE.md
git commit -m "corrupted_gauntlet: routine wiring (ROUTINE_FILES, RoutineChoice) and CLAUDE.md state

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand-off to the live checklist** (user-run)

Rebuild the client (`./gradlew.bat run`), which also delivers sub-project 1's bridge fields. Then, with the user hand-prepping to the barrier: `python -u controller.py routines/corrupted_gauntlet.py --routine CORRUPTED_GAUNTLET *> logs/cg_live1.log`, and record against the spec's checklist -- in particular item 1 (centre vs corner: compare the Hunllef's `instX/instY` with where its model visibly stands; if it is the south-west corner, set `hunllef.FOOTPRINT_ORIGIN = "sw"`), and the `[cg] rung=` log lines' cadence against the 0.6 s tick.

---

## Self-review

- **Spec coverage:** phases (Task 7 `derive_phase`/`tick`); snapshot (Task 4 + `read_snapshot`); style tracker incl. replay test (Task 3); Hunllef prayer/weapon (Tasks 1, 6); ladder rungs 1-7 (Task 6 `ladder`, Task 7 actions); stomp guard (Task 5 `forbidden_near_hunllef`, used by `pick_safe_tile`, which every walk target goes through); entry and loot (Task 7); config (Task 7 `DEFAULT_CONFIG`; the bridge config section is deliberately not added, per spec); error handling (unreadable snapshot, death, no safe tile, no fish, no events, fight ceiling -- all in Task 7's loop and actions); `EventStream.since` (Task 2, the one piece the spec's "Available primitives" list lacked, explained there); wiring + CLAUDE.md (Task 8); live checklist (Task 8 Step 5).
- **Placeholders:** none. The flood test's uncertain assertion was replaced by geometry-guaranteed ones in the task text itself.
- **Type consistency:** `FightSnapshot.from_bridge(player, npcs, objects, prayers, equipment, inventory, combat)` keyword order matches `read_snapshot` and every test; `ladder` returns `(rung, arg)` and `ACTIONS[rung](ctx, snap, arg, cfg)` matches every `do_*` signature (`do_escape` has an extra keyword-only-by-position `require_in_range` defaulting False, called positionally only by `do_reposition`); `pick_safe_tile(snap, imap, cfg, require_in_range)` matches Task 5's definition and Task 7's call; cfg keys used by `hunllef.py` (`escape_radius`, `attack_range`, `tornado_danger`, `tornado_avoid`, `eat_at`, `egniol_at`, `melee_weapon`, `offensive_prayers`) all exist in `DEFAULT_CONFIG`; `EventStream.since(seq)` returns `(type, payload)` pairs, which `feed_tracker` consumes.
