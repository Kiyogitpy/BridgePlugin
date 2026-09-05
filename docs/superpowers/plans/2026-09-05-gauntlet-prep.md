# Gauntlet Prep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The phases in front of `enter` for `routines/corrupted_gauntlet.py`: from the Gauntlet loading, walk the user's fixed route, gather what the kit ladder still needs, craft at the bowl/pump/range, take a perfected-weapon upgrade when a dragon or dark beast was revealed and time allows, and hand over to `enter` before the 7:30 timer.

**Architecture:** Two new pure modules carry every decision: `gauntlet_kit.py` (recipes, the kit ladder, deficits, the craft plan) and `gauntlet_maze.py` (the route relative to the start side, room geometry, node matching). The routine gains a prep state (`cg_t0`, start-room centre, outward direction, route progress), three phases (`explore_gather`, `craft`, `upgrade`), a gather ladder for monsters that reuses the fight's prayer/attack actions, and crafting actions that click the bowl interface by widget TEXT. Everything acted on is HTTP; `instance_nav.walk_to` does every walk.

**Tech Stack:** Python 3 (`pytest` from `ControllerAddon/`). No Java changes.

**Spec:** `docs/superpowers/specs/2026-09-05-gauntlet-prep-design.md`

## Global Constraints

- Route, for start room S adjacent to the boss B, OUTWARD = B -> S, RIGHT = OUTWARD rotated clockwise: enter (1,0); enter (1,1) and reveal (1,2); enter (0,1) and reveal (0,2); enter (-1,1) and reveal (-1,2); enter (-1,0), (-1,-1), (-1,-2), (0,-2), (1,-2), (1,-1); offsets are (right, outward) units from S; B is (0,-1). "Reveal" = light the node toward that room, never enter it.
- Costs (verbatim): T1 piece 1 ore + 1 bark + 1 linum + 50 shards; T2 helm/legs 1/1/1 + 50, T2 body 2/2/2 + 100; basic weapon = 1 weapon frame; attuned = +50 shards; perfected staff = +orb, perfected bow = +bowstring; vial 10 shards; corrupted paddlefish +10 shards; egniol = vial + water + grym leaf + 10 dust (dust = pestle on 10 shards).
- Time (config, seconds from `cg_t0`): `GATHER_DEADLINE_S` 270, `GATHER_HARD_DEADLINE_S` 330, `CRAFT_BY_S` 360, `UPGRADE_MIN_LEFT_S` 90, `ENTER_BY_S` 420, timer 450.
- Kit order: T1 helm, T1 body, T1 legs, attuned bow, attuned staff, 20 fish, 2 egniol; upgrades: T2 helm, T2 legs, T2 body, perfected staff, perfected bow. Never build a halberd.
- Bowl items are chosen by widget TEXT (name match), never by child index. Interface clicks pass `check_ui_blockers=False, simulate_mistakes=False, offset=(0, 0)`.
- Demi-bosses are never engaged in `explore_gather`; a bear is never engaged at all.
- Tests hermetic (stub `controller` and `instance_nav` per test); implementers run named test files only. Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File map

| File | Responsibility |
|---|---|
| `ControllerAddon/gauntlet_ids.py` | + item names, armour/weapon names by tier, monster attack styles, demi-boss drops, bowl item texts, start-room object ids. |
| `ControllerAddon/gauntlet_kit.py` (new) | Recipes, `owned_tier`, `Deficit`, `kit_deficit`, `craft_plan`, `gather_value`, `kit_minimum_met`. |
| `ControllerAddon/gauntlet_maze.py` (new) | `outward_from`, `right_of`, `route`, `room_centre`, `room_of`, `component`, `component_centre`, `node_between`. |
| `ControllerAddon/routines/corrupted_gauntlet.py` | Prep state, `time_left`, extended `derive_phase`, `explore_gather_phase`, `craft_phase`, `upgrade_phase`, gather ladder, crafting actions. |
| `ControllerAddon/tests/test_gauntlet_kit.py`, `test_gauntlet_maze.py` (new), `test_gauntlet_ids.py`, `test_corrupted_gauntlet.py` (append) | Tests. |
| `CLAUDE.md` | The routine's "Current state" section grows the prep. |

---

### Task 1: id and name tables

**Files:**
- Modify: `ControllerAddon/gauntlet_ids.py`
- Test: `ControllerAddon/tests/test_gauntlet_ids.py` (append)

**Interfaces (produces):**
```python
ITEM_ORE = "Corrupted ore"; ITEM_BARK = "Phren bark"; ITEM_LINUM = "Linum tirinum"; ITEM_GRYM = "Grym leaf"
ITEM_RAW_FISH = "Raw paddlefish"; ITEM_FISH = "Paddlefish"; ITEM_CFISH = "Corrupted paddlefish"
ITEM_SHARDS = "Corrupted shards"; ITEM_FRAME = "Weapon frame"; ITEM_ORB = "Corrupted orb"
ITEM_BOWSTRING = "Corrupted bowstring"; ITEM_SPIKE = "Corrupted spike"; ITEM_VIAL = "Vial"
ITEM_WATER_VIAL = "Water-filled vial"; ITEM_GRYM_UNF = "Grym potion (unf)"; ITEM_DUST = "Corrupted dust"
ITEM_PESTLE = "Pestle and mortar"; ITEM_SCEPTRE = "Corrupted sceptre"; ITEM_TELEPORT = "Corrupted teleport crystal"
ITEM_AXE = "Corrupted axe"; ITEM_PICKAXE = "Corrupted pickaxe"; ITEM_HARPOON = "Corrupted harpoon"
EGNIOL_DOSES = ("Egniol potion (1)", "Egniol potion (2)", "Egniol potion (3)", "Egniol potion (4)")
TIER_NAMES = {1: "basic", 2: "attuned", 3: "perfected"}
ARMOUR_KINDS = ("helm", "body", "legs")
def armour_name(kind, tier) -> "Corrupted helm (basic)" ...
def weapon_name(kind, tier) -> "Corrupted bow (attuned)" ...     # kind in ("bow", "staff")
BOWL_TEXT = {"helm": "Corrupted helm", "body": "Corrupted body", "legs": "Corrupted legs", "bow": "Corrupted bow", "staff": "Corrupted staff", "cfish": "Corrupted paddlefish", "vial": "Vial", "teleport": "Corrupted teleport crystal"}   # substring the bowl widget's text must contain
BOWL_GROUP = 270
RESOURCE_ACTION = {"Corrupt Deposit": "Mine", "Corrupt Phren Roots": "Chop", "Corrupt Linum Tirinum": "Pick", "Corrupt Grym Root": "Pick", "Corrupt Fishing Spot": "Fish"}
RESOURCE_ITEM = {"Corrupt Deposit": ITEM_ORE, "Corrupt Phren Roots": ITEM_BARK, "Corrupt Linum Tirinum": ITEM_LINUM, "Corrupt Grym Root": ITEM_GRYM, "Corrupt Fishing Spot": ITEM_RAW_FISH}
RESOURCE_YIELD = {"Corrupt Deposit": 3, "Corrupt Phren Roots": 3, "Corrupt Linum Tirinum": 3, "Corrupt Grym Root": 1, "Corrupt Fishing Spot": 4}
ATTACK_STYLE_BY_NPC_ID = {9040: "MELEE", 9041: "MELEE", 9042: "MELEE", 9043: "MELEE", 9044: "MELEE", 9045: "MELEE", 9046: "MELEE", 9047: "MAGIC"}   # dark beast: RANGED, id unknown -- add when seen
DEMI_BOSS_DROP = {9046: ITEM_SPIKE, 9047: ITEM_ORB}   # dark beast -> ITEM_BOWSTRING once its id is known
DEMI_BOSS_WANTED = {9047}                              # orb -> perfected staff; dark beast added with its id
PROTECT_FOR_STYLE = {"MELEE": "PROTECT_FROM_MELEE", "RANGED": "PROTECT_FROM_MISSILES", "MAGIC": "PROTECT_FROM_MAGIC"}
NODE_IDS = {35998, 35999}
START_ROOM_OBJECTS: the existing OBJ_ constants for Singing Bowl (35966), Tool Storage (35977), Range (35980), Water Pump (35981) -- keep whatever names sub-project 1 gave them; add any missing as OBJ_SINGING_BOWL, OBJ_TOOL_STORAGE, OBJ_RANGE, OBJ_WATER_PUMP.
```
`RESOURCE_NODES`/`RESOURCE_NODES_DEPLETED` (dicts id -> name) already exist; read them before adding anything and build `RESOURCE_ACTION` keys from the names they carry (the names above are the recording's; if the dicts spell a name differently, the dict wins and the test must use the dict's spelling).

- [ ] **Step 1: Write the failing test** (append to `tests/test_gauntlet_ids.py`)

```python
def test_item_and_tier_names():
    assert g.armour_name("helm", 2) == "Corrupted helm (attuned)"
    assert g.weapon_name("staff", 3) == "Corrupted staff (perfected)"
    assert g.weapon_name("bow", 1) == "Corrupted bow (basic)"
    assert g.EGNIOL_DOSES[2] == "Egniol potion (3)"
    assert g.ITEM_SHARDS == "Corrupted shards" and g.ITEM_FRAME == "Weapon frame"


def test_resource_tables_cover_every_live_node():
    live = {name for name in g.RESOURCE_NODES.values()}
    assert live == set(g.RESOURCE_ACTION) == set(g.RESOURCE_ITEM) == set(g.RESOURCE_YIELD)
    assert g.RESOURCE_ACTION[[n for n in live if "Deposit" in n][0]] == "Mine"
    assert g.RESOURCE_YIELD[[n for n in live if "Fishing" in n][0]] == 4


def test_monster_styles_and_demi_boss_drops():
    assert set(g.WEAK_MONSTERS) <= set(g.ATTACK_STYLE_BY_NPC_ID)
    assert g.ATTACK_STYLE_BY_NPC_ID[9047] == "MAGIC" and g.ATTACK_STYLE_BY_NPC_ID[9046] == "MELEE"
    assert g.DEMI_BOSS_DROP[9047] == g.ITEM_ORB and 9047 in g.DEMI_BOSS_WANTED and 9046 not in g.DEMI_BOSS_WANTED
    assert g.PROTECT_FOR_STYLE["MAGIC"] == "PROTECT_FROM_MAGIC"
    assert g.BOWL_GROUP == 270 and g.BOWL_TEXT["cfish"] == "Corrupted paddlefish"
    for c in (g.OBJ_SINGING_BOWL, g.OBJ_TOOL_STORAGE, g.OBJ_RANGE, g.OBJ_WATER_PUMP):
        assert isinstance(c, int)
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_gauntlet_ids.py -q`; expected `AttributeError`.

- [ ] **Step 3: Add the tables** (append to `gauntlet_ids.py`; source comments: wiki The Gauntlet rev 15273190 for yields/costs, REC for names/ids)

```python
# --- Items (REC inventory names) ---
ITEM_ORE, ITEM_BARK, ITEM_LINUM, ITEM_GRYM = "Corrupted ore", "Phren bark", "Linum tirinum", "Grym leaf"
ITEM_RAW_FISH, ITEM_FISH, ITEM_CFISH = "Raw paddlefish", "Paddlefish", "Corrupted paddlefish"
ITEM_SHARDS, ITEM_FRAME = "Corrupted shards", "Weapon frame"
ITEM_ORB, ITEM_BOWSTRING, ITEM_SPIKE = "Corrupted orb", "Corrupted bowstring", "Corrupted spike"
ITEM_VIAL, ITEM_WATER_VIAL, ITEM_GRYM_UNF, ITEM_DUST = "Vial", "Water-filled vial", "Grym potion (unf)", "Corrupted dust"
ITEM_PESTLE, ITEM_SCEPTRE, ITEM_TELEPORT = "Pestle and mortar", "Corrupted sceptre", "Corrupted teleport crystal"
ITEM_AXE, ITEM_PICKAXE, ITEM_HARPOON = "Corrupted axe", "Corrupted pickaxe", "Corrupted harpoon"
EGNIOL_DOSES = ("Egniol potion (1)", "Egniol potion (2)", "Egniol potion (3)", "Egniol potion (4)")
TIER_NAMES = {1: "basic", 2: "attuned", 3: "perfected"}
ARMOUR_KINDS = ("helm", "body", "legs")


def armour_name(kind, tier):
    return f"Corrupted {kind} ({TIER_NAMES[tier]})"


def weapon_name(kind, tier):
    return f"Corrupted {kind} ({TIER_NAMES[tier]})"


# --- Singing Bowl (REC: group 270; children matched by TEXT, never index) ---
BOWL_GROUP = 270
BOWL_TEXT = {"helm": "Corrupted helm", "body": "Corrupted body", "legs": "Corrupted legs",
             "bow": "Corrupted bow", "staff": "Corrupted staff", "cfish": "Corrupted paddlefish",
             "vial": "Vial", "teleport": "Corrupted teleport crystal"}

# --- Resource nodes (wiki yields; REC action names) keyed by the live node NAME ---
_N = {v: v for v in RESOURCE_NODES.values()}
def _node(sub):
    return next(n for n in RESOURCE_NODES.values() if sub in n)
RESOURCE_ACTION = {_node("Deposit"): "Mine", _node("Phren"): "Chop", _node("Linum"): "Pick",
                   _node("Grym"): "Pick", _node("Fishing"): "Fish"}
RESOURCE_ITEM = {_node("Deposit"): ITEM_ORE, _node("Phren"): ITEM_BARK, _node("Linum"): ITEM_LINUM,
                 _node("Grym"): ITEM_GRYM, _node("Fishing"): ITEM_RAW_FISH}
RESOURCE_YIELD = {_node("Deposit"): 3, _node("Phren"): 3, _node("Linum"): 3, _node("Grym"): 1,
                  _node("Fishing"): 4}
NODE_IDS = {35998, 35999}

# --- Monsters: attack style -> the protection prayer to hold while it hits us ---
ATTACK_STYLE_BY_NPC_ID = {9040: "MELEE", 9041: "MELEE", 9042: "MELEE", 9043: "MELEE", 9044: "MELEE",
                          9045: "MELEE", 9046: "MELEE", 9047: "MAGIC"}   # dark beast: RANGED; id unknown
DEMI_BOSS_DROP = {9046: ITEM_SPIKE, 9047: ITEM_ORB}      # + dark beast -> ITEM_BOWSTRING when its id is known
DEMI_BOSS_WANTED = {9047}                                # orb -> perfected staff; bear's spike is never wanted
PROTECT_FOR_STYLE = {"MELEE": "PROTECT_FROM_MELEE", "RANGED": "PROTECT_FROM_MISSILES", "MAGIC": "PROTECT_FROM_MAGIC"}
```
If `RESOURCE_NODES` maps the depleted ids too, use `RESOURCE_NODES` for live and `RESOURCE_NODES_DEPLETED` for depleted as they already are. Add `OBJ_SINGING_BOWL = 35966`, `OBJ_TOOL_STORAGE = 35977`, `OBJ_RANGE = 35980`, `OBJ_WATER_PUMP = 35981` only if constants with those values do not already exist under other names (if they do, alias: `OBJ_SINGING_BOWL = <existing>`).

- [ ] **Step 4: Run** -- `python -m pytest tests/test_gauntlet_ids.py -q`; all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/gauntlet_ids.py ControllerAddon/tests/test_gauntlet_ids.py
git commit -m "gauntlet_ids: item/tier names, resource tables, monster styles, bowl texts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `gauntlet_kit.py` -- recipes, deficits, craft plan

**Files:**
- Create: `ControllerAddon/gauntlet_kit.py`
- Test: `ControllerAddon/tests/test_gauntlet_kit.py`

**Interfaces (produces):**
```python
KIT = [("armour","helm",1),("armour","body",1),("armour","legs",1),("weapon","bow",2),("weapon","staff",2),("fish",20),("egniol",2)]
UPGRADES = [("armour","helm",2),("armour","legs",2),("armour","body",2),("weapon","staff",3),("weapon","bow",3)]
ARMOUR_COST = {("helm",1):(1,1,1,50),("body",1):(1,1,1,50),("legs",1):(1,1,1,50),("helm",2):(1,1,1,50),("legs",2):(1,1,1,50),("body",2):(2,2,2,100)}   # (ore, bark, linum, shards) for the step INTO that tier
def count(inventory, name) -> int            # sums qty over matching items
def owned_tier(kind, inventory, equipment) -> int   # 0..3, armour or weapon kind, inventory OR worn
@dataclass Deficit: ore, bark, linum, shards, frames, fish, grym, orb, bowstring (ints, >= 0)
def kit_deficit(inventory, equipment, cfg, targets=KIT) -> Deficit
def kit_minimum_met(inventory, equipment, cfg) -> bool        # every KIT entry satisfied (materials in hand count for armour/weapons not yet crafted)
def craft_plan(inventory, equipment, cfg) -> list[Step]       # Step = (op, arg): ("bowl","vial"), ("bowl","helm"), ("bowl","bow"), ("bowl","cfish"), ("pump",None), ("pestle",None), ("potion",None), ("cook",None), ("wear",name)
def gather_value(objects, npcs, deficit) -> int               # sum over live resource nodes in the room of min(yield, deficit of that item) * 3, + 1 per weak monster if frames are needed
```
cfg keys: `fish_target` (20), `egniol_target` (2), `shard_reserve` (0).

- [ ] **Step 1: Write the failing tests**

```python
import gauntlet_ids as g
import gauntlet_kit as k

CFG = {"fish_target": 20, "egniol_target": 2, "shard_reserve": 0}

def inv(**items):
    return [{"slot": i, "name": n, "qty": q, "screenX": 1, "screenY": 1} for i, (n, q) in enumerate(items.items())]

def named(pairs):
    return [{"slot": i, "name": n, "qty": q, "screenX": 1, "screenY": 1} for i, (n, q) in enumerate(pairs)]

EQ_NONE = {"weapon": {"name": g.ITEM_SCEPTRE}, "helmet": None, "body": None, "legs": None}
EQ_T1 = {"weapon": {"name": "Corrupted bow (attuned)"}, "helmet": {"name": "Corrupted helm (basic)"},
         "body": {"name": "Corrupted body (basic)"}, "legs": {"name": "Corrupted legs (basic)"}}


def test_owned_tier_reads_inventory_and_worn():
    assert k.owned_tier("helm", [], EQ_NONE) == 0
    assert k.owned_tier("helm", [], EQ_T1) == 1
    assert k.owned_tier("bow", [], EQ_T1) == 2
    assert k.owned_tier("staff", inv(**{"Corrupted staff (perfected)": 1}), EQ_T1) == 3


def test_deficit_at_entry_is_the_whole_kit():
    d = k.kit_deficit(inv(**{g.ITEM_SCEPTRE: 1, g.ITEM_PESTLE: 1, g.ITEM_AXE: 1, g.ITEM_PICKAXE: 1, g.ITEM_HARPOON: 1, g.ITEM_TELEPORT: 1}), EQ_NONE, CFG)
    assert (d.ore, d.bark, d.linum) == (3, 3, 3)
    assert d.frames == 2
    assert d.fish == 20 and d.grym == 2
    # 150 (T1) + 100 (two attuned) + 2 * (10 vial + 10 dust) = 290
    assert d.shards == 290


def test_deficit_after_the_recordings_gather_phase():
    # REC at ~160 s: 7 ore/bark/linum, 8 raw fish, 2 grym, 2 frames, ~590 shards
    d = k.kit_deficit(named([(g.ITEM_ORE, 7), (g.ITEM_BARK, 7), (g.ITEM_LINUM, 7), (g.ITEM_RAW_FISH, 8),
                             (g.ITEM_GRYM, 2), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 590)]), EQ_NONE, CFG)
    assert (d.ore, d.bark, d.linum, d.frames, d.grym, d.shards) == (0, 0, 0, 0, 0, 0)
    assert d.fish == 12                       # raw fish count toward the target
    assert not k.kit_minimum_met(named([(g.ITEM_ORE, 7)]), EQ_NONE, CFG)
    assert k.kit_minimum_met(named([(g.ITEM_ORE, 3), (g.ITEM_BARK, 3), (g.ITEM_LINUM, 3), (g.ITEM_FRAME, 2),
                                    (g.ITEM_SHARDS, 290), (g.ITEM_RAW_FISH, 20), (g.ITEM_GRYM, 2)]), EQ_NONE, CFG)


def test_deficit_counts_owned_pieces_and_potions():
    d = k.kit_deficit(named([("Egniol potion (3)", 1), (g.ITEM_FISH, 5), (g.ITEM_CFISH, 3)]), EQ_T1, CFG)
    assert (d.ore, d.bark, d.linum) == (0, 0, 0)         # T1 worn
    assert d.frames == 1 and d.shards == 50 + 10 + 10   # one attuned staff + one potion
    assert d.fish == 12 and d.grym == 1


def test_craft_plan_order_and_shard_budget():
    inv_ = named([(g.ITEM_ORE, 7), (g.ITEM_BARK, 7), (g.ITEM_LINUM, 7), (g.ITEM_RAW_FISH, 20),
                  (g.ITEM_GRYM, 2), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 590), (g.ITEM_PESTLE, 1)])
    plan = k.craft_plan(inv_, EQ_NONE, CFG)
    ops = [s for s in plan]
    # vials first (potions need two), then the T1 set, then both weapons basic->attuned, then T2 cheapest
    assert ops[:2] == [("bowl", "vial"), ("bowl", "vial")]
    assert ops[2:5] == [("bowl", "helm"), ("bowl", "body"), ("bowl", "legs")]
    assert ops[5:9] == [("bowl", "bow"), ("bowl", "bow"), ("bowl", "staff"), ("bowl", "staff")]
    assert ("pump", None) in ops and ("pestle", None) in ops and ("potion", None) in ops and ("cook", None) in ops
    t2 = [a for o, a in ops[9:] if o == "bowl" and a in ("helm", "legs", "body")]
    assert t2[:2] == ["helm", "legs"]             # cheapest T2 first; body (100) after
    spent = 20 + 150 + 100 + 50 + 50 + 100 + 20   # vials, T1, attuned x2, T2 helm/legs/body, dust
    assert spent <= 590
    assert ("wear", "Corrupted helm (attuned)") in ops or ("wear", "Corrupted helm (basic)") in ops


def test_craft_plan_never_overspends():
    inv_ = named([(g.ITEM_ORE, 3), (g.ITEM_BARK, 3), (g.ITEM_LINUM, 3), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 120)])
    plan = k.craft_plan(inv_, EQ_NONE, CFG)
    bowl = [a for o, a in plan if o == "bowl"]
    assert bowl == ["helm", "body"]               # 120 shards: two T1 pieces, nothing else
    assert ("bowl", "vial") not in plan           # no grym -> no potions


def test_gather_value_scores_deficit_only():
    d = k.Deficit(ore=3, bark=0, linum=1, shards=0, frames=1, fish=0, grym=0, orb=0, bowstring=0)
    deposit = next(n for n in g.RESOURCE_NODES.values() if "Deposit" in n)
    phren = next(n for n in g.RESOURCE_NODES.values() if "Phren" in n)
    objs = [{"name": deposit, "id": 1}, {"name": deposit, "id": 1}, {"name": phren, "id": 1}]
    assert k.gather_value(objs, [{"id": 9040}], d) == 3 * 3 + 1
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_gauntlet_kit.py -q`; `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Kit ladder, deficits and the craft plan for the Corrupted Gauntlet prep -- pure.

Spec: docs/superpowers/specs/2026-09-05-gauntlet-prep-design.md ("Kit ladder and deficits",
"Costs"). Costs are the wiki's (The Gauntlet rev 15273190), confirmed by the recording's
inventory deltas. Everything here takes plain bridge dicts (/inventory items, /equipment slots).
"""
from __future__ import annotations

from dataclasses import dataclass

import gauntlet_ids as g

KIT = [("armour", "helm", 1), ("armour", "body", 1), ("armour", "legs", 1),
       ("weapon", "bow", 2), ("weapon", "staff", 2), ("fish", 20), ("egniol", 2)]
UPGRADES = [("armour", "helm", 2), ("armour", "legs", 2), ("armour", "body", 2),
            ("weapon", "staff", 3), ("weapon", "bow", 3)]
ARMOUR_COST = {("helm", 1): (1, 1, 1, 50), ("body", 1): (1, 1, 1, 50), ("legs", 1): (1, 1, 1, 50),
               ("helm", 2): (1, 1, 1, 50), ("legs", 2): (1, 1, 1, 50), ("body", 2): (2, 2, 2, 100)}
ATTUNE_SHARDS = 50
VIAL_SHARDS = 10
DUST_SHARDS = 10
CFISH_SHARDS = 10
EQUIP_SLOT = {"helm": "helmet", "body": "body", "legs": "legs", "bow": "weapon", "staff": "weapon"}


def count(inventory, name):
    return sum(int(i.get("qty") or 1) for i in inventory or [] if i.get("name") == name)


def owned_tier(kind, inventory, equipment):
    namer = g.armour_name if kind in g.ARMOUR_KINDS else g.weapon_name
    worn = ((equipment or {}).get(EQUIP_SLOT[kind]) or {}).get("name")
    best = 0
    for tier in (3, 2, 1):
        name = namer(kind, tier)
        if worn == name or count(inventory, name):
            best = max(best, tier)
    return best


@dataclass
class Deficit:
    ore: int = 0
    bark: int = 0
    linum: int = 0
    shards: int = 0
    frames: int = 0
    fish: int = 0
    grym: int = 0
    orb: int = 0
    bowstring: int = 0


def _potions(inventory):
    return sum(count(inventory, n) for n in g.EGNIOL_DOSES)


def _fish(inventory):
    return count(inventory, g.ITEM_FISH) + count(inventory, g.ITEM_CFISH) + count(inventory, g.ITEM_RAW_FISH)


def kit_deficit(inventory, equipment, cfg, targets=KIT):
    d = Deficit()
    frames_free = count(inventory, g.ITEM_FRAME)
    for entry in targets:
        if entry[0] == "armour":
            _, kind, tier = entry
            for step in range(owned_tier(kind, inventory, equipment) + 1, tier + 1):
                ore, bark, linum, shards = ARMOUR_COST[(kind, step)]
                d.ore += ore; d.bark += bark; d.linum += linum; d.shards += shards
        elif entry[0] == "weapon":
            _, kind, tier = entry
            have = owned_tier(kind, inventory, equipment)
            if have == 0 and tier >= 1:
                if frames_free > 0:
                    frames_free -= 1
                else:
                    d.frames += 1
            if have < 2 and tier >= 2:
                d.shards += ATTUNE_SHARDS
            if have < 3 and tier >= 3:
                if kind == "staff" and not count(inventory, g.ITEM_ORB):
                    d.orb += 1
                if kind == "bow" and not count(inventory, g.ITEM_BOWSTRING):
                    d.bowstring += 1
        elif entry[0] == "fish":
            d.fish += max(0, cfg["fish_target"] - _fish(inventory))
        elif entry[0] == "egniol":
            need = max(0, cfg["egniol_target"] - _potions(inventory))
            d.grym += max(0, need - count(inventory, g.ITEM_GRYM) - count(inventory, g.ITEM_GRYM_UNF))
            d.shards += need * (VIAL_SHARDS + DUST_SHARDS)
    have = (count(inventory, g.ITEM_ORE), count(inventory, g.ITEM_BARK), count(inventory, g.ITEM_LINUM),
            count(inventory, g.ITEM_SHARDS))
    d.ore = max(0, d.ore - have[0]); d.bark = max(0, d.bark - have[1]); d.linum = max(0, d.linum - have[2])
    d.shards = max(0, d.shards - have[3])
    return d


def kit_minimum_met(inventory, equipment, cfg):
    d = kit_deficit(inventory, equipment, cfg)
    return not any((d.ore, d.bark, d.linum, d.shards, d.frames, d.fish, d.grym))


def _affordable(shards, cost):
    return shards >= cost


def craft_plan(inventory, equipment, cfg):
    """Ordered bowl/pump/pestle/potion/cook/wear steps from what is in the bag, within the shards
    held: potions' vials first (they need the pump trip), the T1 set, both weapons to attuned,
    then the potion chain, then T2 pieces cheapest first, then corrupted paddlefish, then
    wearing everything crafted."""
    shards = count(inventory, g.ITEM_SHARDS) - cfg.get("shard_reserve", 0)
    ore, bark, linum = count(inventory, g.ITEM_ORE), count(inventory, g.ITEM_BARK), count(inventory, g.ITEM_LINUM)
    frames = count(inventory, g.ITEM_FRAME)
    grym = count(inventory, g.ITEM_GRYM)
    plan = []
    potions = min(grym, max(0, cfg["egniol_target"] - _potions(inventory)))
    vials = 0
    for _ in range(potions):
        if shards >= VIAL_SHARDS + DUST_SHARDS:
            plan.append(("bowl", "vial")); shards -= VIAL_SHARDS; vials += 1
    tiers = {k: owned_tier(k, inventory, equipment) for k in ("helm", "body", "legs", "bow", "staff")}

    def armour_step(kind, tier):
        nonlocal ore, bark, linum, shards
        o, b, l, s = ARMOUR_COST[(kind, tier)]
        if tiers[kind] >= tier or ore < o or bark < b or linum < l or shards < s:
            return False
        ore -= o; bark -= b; linum -= l; shards -= s; tiers[kind] = tier
        plan.append(("bowl", kind)); return True

    for kind in ("helm", "body", "legs"):
        armour_step(kind, 1)
    for kind in ("bow", "staff"):
        if tiers[kind] == 0 and frames > 0:
            frames -= 1; tiers[kind] = 1; plan.append(("bowl", kind))
        if tiers[kind] == 1 and shards >= ATTUNE_SHARDS:
            shards -= ATTUNE_SHARDS; tiers[kind] = 2; plan.append(("bowl", kind))
    for kind, item in (("staff", g.ITEM_ORB), ("bow", g.ITEM_BOWSTRING)):
        if tiers[kind] == 2 and count(inventory, item):
            tiers[kind] = 3; plan.append(("bowl", kind))
    if vials:
        plan.append(("pump", None))
        for _ in range(vials):
            plan.append(("pestle", None)); shards -= DUST_SHARDS
        plan.append(("potion", None))
    for kind, tier in (("helm", 2), ("legs", 2), ("body", 2)):
        armour_step(kind, tier)
    if count(inventory, g.ITEM_RAW_FISH):
        plan.append(("cook", None))
    cfish = min(count(inventory, g.ITEM_RAW_FISH) + count(inventory, g.ITEM_FISH), shards // CFISH_SHARDS, 4)
    for _ in range(cfish):
        plan.append(("bowl", "cfish")); shards -= CFISH_SHARDS
    for kind in ("helm", "body", "legs"):
        if tiers[kind] > owned_tier(kind, [], equipment):
            plan.append(("wear", g.armour_name(kind, tiers[kind])))
    return plan


def gather_value(objects, npcs, deficit):
    want = {g.ITEM_ORE: deficit.ore, g.ITEM_BARK: deficit.bark, g.ITEM_LINUM: deficit.linum,
            g.ITEM_GRYM: deficit.grym, g.ITEM_RAW_FISH: deficit.fish}
    value = 0
    for o in objects or []:
        name = o.get("name")
        if name in g.RESOURCE_ITEM:
            item = g.RESOURCE_ITEM[name]
            take = min(g.RESOURCE_YIELD[name], want.get(item, 0))
            want[item] = want.get(item, 0) - take
            value += take * 3
    if deficit.frames:
        value += sum(1 for n in npcs or [] if n.get("id") in g.WEAK_MONSTERS)
    return value
```

- [ ] **Step 4: Run** -- `python -m pytest tests/test_gauntlet_kit.py -q`; expected all pass. If `test_craft_plan_order_and_shard_budget`'s `ops[5:9]` assertion fails, check the weapon loop emits basic then attuned for bow before staff.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/gauntlet_kit.py ControllerAddon/tests/test_gauntlet_kit.py
git commit -m "gauntlet_kit: kit ladder, deficits, craft plan, gather value

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `gauntlet_maze.py` -- route and room geometry

**Files:**
- Create: `ControllerAddon/gauntlet_maze.py`
- Test: `ControllerAddon/tests/test_gauntlet_maze.py`

**Interfaces (produces):**
```python
def outward_from(barrier, start_centre) -> (dx, dy)      # unit cardinal, dominant axis of start_centre - barrier
def right_of(outward) -> (dx, dy)                          # clockwise: (0,1)->(1,0), (1,0)->(0,-1), (0,-1)->(-1,0), (-1,0)->(0,1)
ROUTE = [((1,0),"enter",None), ((1,1),"enter",(1,2)), ((0,1),"enter",(0,2)), ((-1,1),"enter",(-1,2)), ((-1,0),"enter",None), ((-1,-1),"enter",None), ((-1,-2),"enter",None), ((0,-2),"enter",None), ((1,-2),"enter",None), ((1,-1),"enter",None)]
def route() -> list                                        # (room_offset, "enter", reveal_offset or None); offsets are (right, outward)
def room_centre(start_centre, pitch, outward, offset) -> (x, y)
def room_of(tile, start_centre, pitch, outward) -> offset
def component(imap, start, limit=2000) -> set[tile]        # flood over imap.step_ok
def component_centre(tiles) -> (x, y)                      # rounded mean
def node_between(nodes, centre_a, centre_b, pitch) -> node dict | None   # nearest node (dict with instX/instY) to the midpoint within pitch/2
def edge_open(imap, centre_a, centre_b) -> bool            # instance_nav.find_path exists between the two centres
```

- [ ] **Step 1: Write the failing tests**

```python
import gauntlet_maze as m
import instance_nav

BX, BY = 13056, 5632

def test_outward_and_right():
    assert m.outward_from((100, 100), (100, 110)) == (0, 1)      # start north of the barrier
    assert m.outward_from((100, 100), (89, 101)) == (-1, 0)
    assert m.right_of((0, 1)) == (1, 0) and m.right_of((1, 0)) == (0, -1)
    assert m.right_of((0, -1)) == (-1, 0) and m.right_of((-1, 0)) == (0, 1)


def test_route_matches_the_spec_for_start_north_of_boss():
    # spec: S=(2,3) north of B=(3,3); rows down, cols right. outward=(0,1) means "up" = row-1.
    def grid(offset):
        r, o = offset
        return (2 - o, 3 + r)
    entered = [grid(off) for off, act, _ in m.route()]
    assert entered == [(2, 4), (1, 4), (1, 3), (1, 2), (2, 2), (3, 2), (4, 2), (4, 3), (4, 4), (3, 4)]
    reveals = [grid(rv) for _, _, rv in m.route() if rv]
    assert reveals == [(0, 4), (0, 3), (0, 2)]
    assert len(m.route()) == 10


def test_room_centre_rotates_with_outward():
    s = (1000, 1000)
    assert m.room_centre(s, 16, (0, 1), (1, 0)) == (1016, 1000)     # right of S when outward is north
    assert m.room_centre(s, 16, (0, 1), (0, 1)) == (1000, 1016)
    assert m.room_centre(s, 16, (1, 0), (1, 0)) == (1000, 984)      # outward east: right is south
    assert m.room_of((1015, 1001), s, 16, (0, 1)) == (1, 0)
    assert m.room_of(s, s, 16, (0, 1)) == (0, 0)


def test_component_and_centre():
    size = 16
    flags = [0] * (size * size)
    from travel import BLOCK_MOVEMENT_OBJECT
    for gy in range(size):
        flags[gy * size + 8] |= BLOCK_MOVEMENT_OBJECT        # a wall down the middle
    imap = instance_nav.InstanceMap(BX, BY, 1, size, flags)
    comp = m.component(imap, (BX + 2, BY + 2))
    assert (BX + 7, BY + 15) in comp and (BX + 9, BY + 2) not in comp
    assert len(comp) == 8 * 16
    assert m.component_centre(comp) == (BX + 3, BY + 7) or m.component_centre(comp) == (BX + 4, BY + 8)


def test_node_between_picks_the_midpoint_node():
    nodes = [{"instX": 100, "instY": 108, "id": 35999}, {"instX": 100, "instY": 92, "id": 35999}, {"instX": 130, "instY": 100, "id": 35999}]
    assert m.node_between(nodes, (100, 100), (100, 116), 16)["instY"] == 108
    assert m.node_between(nodes, (100, 100), (100, 84), 16)["instY"] == 92
    assert m.node_between(nodes, (100, 100), (116, 100), 16) is None       # nothing near (108,100)


def test_edge_open_uses_the_grid(monkeypatch):
    imap = instance_nav.InstanceMap(BX, BY, 1, 8, [0] * 64)
    assert m.edge_open(imap, (BX + 1, BY + 1), (BX + 6, BY + 6))
    monkeypatch.setattr(instance_nav, "find_path", lambda *a, **kw: None)
    assert not m.edge_open(imap, (BX + 1, BY + 1), (BX + 6, BY + 6))
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_gauntlet_maze.py -q`; `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Room geometry and the user's route for the Corrupted Gauntlet prep -- pure.

Spec: docs/superpowers/specs/2026-09-05-gauntlet-prep-design.md ("The route", "Room model").
Rooms are addressed as (right, outward) offsets from the start room S; the boss room is
(0, -1). OUTWARD is the unit cardinal from the Barrier toward S's centre, RIGHT is OUTWARD
rotated clockwise. Everything is instance space (sub-project 1): template coordinates repeat
between rooms and cannot address a room.
"""
from __future__ import annotations

import instance_nav
from hunllef import cheb, flood

# (room offset, action, reveal offset): the user's route, for any start side.
ROUTE = [((1, 0), "enter", None), ((1, 1), "enter", (1, 2)), ((0, 1), "enter", (0, 2)),
         ((-1, 1), "enter", (-1, 2)), ((-1, 0), "enter", None), ((-1, -1), "enter", None),
         ((-1, -2), "enter", None), ((0, -2), "enter", None), ((1, -2), "enter", None),
         ((1, -1), "enter", None)]


def route():
    return list(ROUTE)


def outward_from(barrier, start_centre):
    dx, dy = start_centre[0] - barrier[0], start_centre[1] - barrier[1]
    if abs(dx) >= abs(dy):
        return (1 if dx > 0 else -1, 0)
    return (0, 1 if dy > 0 else -1)


def right_of(outward):
    return (outward[1], -outward[0])


def room_centre(start_centre, pitch, outward, offset):
    r, o = offset
    right = right_of(outward)
    return (start_centre[0] + pitch * (r * right[0] + o * outward[0]),
            start_centre[1] + pitch * (r * right[1] + o * outward[1]))


def room_of(tile, start_centre, pitch, outward):
    dx, dy = tile[0] - start_centre[0], tile[1] - start_centre[1]
    right = right_of(outward)
    r = (dx * right[0] + dy * right[1]) / pitch
    o = (dx * outward[0] + dy * outward[1]) / pitch
    return (int(round(r)), int(round(o)))


def component(imap, start, limit=2000):
    return set(flood(imap, start, limit, blocked=set()))


def component_centre(tiles):
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    return (int(round(sum(xs) / len(xs))), int(round(sum(ys) / len(ys))))


def node_between(nodes, centre_a, centre_b, pitch):
    mid = ((centre_a[0] + centre_b[0]) // 2, (centre_a[1] + centre_b[1]) // 2)
    best, best_d = None, None
    for n in nodes or []:
        pos = (n.get("instX"), n.get("instY"))
        if pos[0] is None:
            continue
        d = cheb(pos, mid)
        if d <= pitch // 2 and (best_d is None or d < best_d):
            best, best_d = n, d
    return best


def edge_open(imap, centre_a, centre_b):
    return instance_nav.find_path(imap, centre_a, centre_b) is not None
```

- [ ] **Step 4: Run** -- `python -m pytest tests/test_gauntlet_maze.py -q`; all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/gauntlet_maze.py ControllerAddon/tests/test_gauntlet_maze.py
git commit -m "gauntlet_maze: the route relative to the start side, room geometry, node matching

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: prep state, timer, extended phase derivation

**Files:**
- Modify: `ControllerAddon/routines/corrupted_gauntlet.py`
- Test: `ControllerAddon/tests/test_corrupted_gauntlet.py` (append)

**Interfaces (produces):**
```python
PREP_CFG keys added to DEFAULT_CONFIG: "gather_deadline_s": 270, "gather_hard_deadline_s": 330, "craft_by_s": 360, "upgrade_min_left_s": 90, "enter_by_s": 420, "timer_s": 450, "room_pitch": 16, "fish_target": 20, "egniol_target": 2, "shard_reserve": 0
def time_left(data, now=None) -> float | None          # None before cg_t0 is known
def note_entry(data, player, now) -> None              # sets cg_t0 the first time regionId == REGION_CORRUPTED
def prep_phase(data, inventory, equipment, cfg, now) -> str   # "explore_gather" | "craft" | "upgrade" | "enter"
derive_phase(data, npcs, objects, inventory=None, equipment=None, cfg=None, now=None) -> str   # existing signature extended with optional args; returns the prep phase instead of "enter" while in the Gauntlet before the fight
```
Rules for `prep_phase`: if `time_left <= timer - enter_by_s` (i.e. elapsed >= enter_by_s) -> "enter". Else if `data["cg_route_done"]` is False and not (kit_minimum_met and elapsed >= gather_deadline_s) and elapsed < gather_hard_deadline_s -> "explore_gather". Else if `data.get("cg_crafted")` is False -> "craft". Else if a wanted demi-boss was revealed (`data["cg_demi_bosses"]`: {offset: npc id}) and time_left > upgrade_min_left_s and the perfected weapon is still missing -> "upgrade". Else "enter".

- [ ] **Step 1: Write the failing tests**

```python
def test_time_left_and_note_entry():
    data = {}
    assert cg.time_left(data, now=100.0) is None
    cg.note_entry(data, {"regionId": 12127}, now=100.0)
    assert "cg_t0" not in data
    cg.note_entry(data, {"regionId": g.REGION_CORRUPTED}, now=100.0)
    assert data["cg_t0"] == 100.0
    cg.note_entry(data, {"regionId": g.REGION_CORRUPTED}, now=200.0)   # never moves
    assert data["cg_t0"] == 100.0
    assert cg.time_left(data, now=160.0) == cg.DEFAULT_CONFIG["timer_s"] - 60


def _kit_inv():
    return [{"slot": i, "name": n, "qty": q, "screenX": 1, "screenY": 1} for i, (n, q) in enumerate(
        [(g.ITEM_ORE, 3), (g.ITEM_BARK, 3), (g.ITEM_LINUM, 3), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 300),
         (g.ITEM_RAW_FISH, 20), (g.ITEM_GRYM, 2)])]

EQ0 = {"weapon": {"name": g.ITEM_SCEPTRE}}


def test_prep_phase_ladder():
    cfg = cg.DEFAULT_CONFIG
    data = {"cg_t0": 0.0}
    assert cg.prep_phase(data, [], EQ0, cfg, now=10.0) == "explore_gather"
    # kit met but before the gather deadline: keep gathering (upgrades come from the ring)
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=100.0) == "explore_gather"
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=280.0) == "craft"
    # route done -> craft even if the kit is short
    data["cg_route_done"] = True
    assert cg.prep_phase(data, [], EQ0, cfg, now=100.0) == "craft"
    # hard deadline -> craft regardless
    data = {"cg_t0": 0.0}
    assert cg.prep_phase(data, [], EQ0, cfg, now=335.0) == "craft"
    # crafted, dragon revealed, time left -> upgrade; no time -> enter
    data = {"cg_t0": 0.0, "cg_route_done": True, "cg_crafted": True, "cg_demi_bosses": {(0, 2): 9047}}
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=300.0) == "upgrade"
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=400.0) == "enter"
    # crafted, nothing revealed -> enter
    data = {"cg_t0": 0.0, "cg_route_done": True, "cg_crafted": True, "cg_demi_bosses": {}}
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=300.0) == "enter"
    # enter_by wins over everything
    data = {"cg_t0": 0.0}
    assert cg.prep_phase(data, [], EQ0, cfg, now=421.0) == "enter"


def test_derive_phase_prefers_prep_before_the_fight():
    data = {"cg_t0": 0.0}
    objects = [{"id": g.OBJ_BARRIER}]
    assert cg.derive_phase(data, [], objects, inventory=[], equipment=EQ0, cfg=cg.DEFAULT_CONFIG, now=10.0) == "explore_gather"
    assert cg.derive_phase(data, [], objects, inventory=[], equipment=EQ0, cfg=cg.DEFAULT_CONFIG, now=425.0) == "enter"
    assert cg.derive_phase(data, [_npc(9036)], objects) == "fight"       # fight still wins
    assert cg.derive_phase({}, [], objects) == "enter"                    # no t0 (started at the barrier by hand): old behaviour
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_corrupted_gauntlet.py -q -k "time_left or prep_phase or prefers_prep"`; `AttributeError`.

- [ ] **Step 3: Implement** (in the routine; add `import gauntlet_kit as kit` and `import gauntlet_maze as maze` at the top)

Add to `DEFAULT_CONFIG`:
```python
    "gather_deadline_s": 270, "gather_hard_deadline_s": 330, "craft_by_s": 360,
    "upgrade_min_left_s": 90, "enter_by_s": 420, "timer_s": 450, "room_pitch": 16,
    "fish_target": 20, "egniol_target": 2, "shard_reserve": 0,
```
Then:
```python
def note_entry(data, player, now):
    """cg_t0 = the first moment the corrupted region is seen; the 7:30 timer counts from it."""
    if "cg_t0" not in data and (player or {}).get("regionId") == g.REGION_CORRUPTED:
        data["cg_t0"] = now


def time_left(data, now=None, cfg=DEFAULT_CONFIG):
    t0 = data.get("cg_t0")
    if t0 is None:
        return None
    now = time.time() if now is None else now
    return cfg["timer_s"] - (now - t0)


def prep_phase(data, inventory, equipment, cfg, now=None):
    now = time.time() if now is None else now
    elapsed = now - data["cg_t0"]
    if elapsed >= cfg["enter_by_s"]:
        return "enter"
    met = kit.kit_minimum_met(inventory, equipment, cfg)
    if (not data.get("cg_route_done") and elapsed < cfg["gather_hard_deadline_s"]
            and not (met and elapsed >= cfg["gather_deadline_s"])):
        return "explore_gather"
    if not data.get("cg_crafted"):
        return "craft"
    wanted = {off: nid for off, nid in (data.get("cg_demi_bosses") or {}).items() if nid in g.DEMI_BOSS_WANTED}
    if wanted and time_left(data, now, cfg) > cfg["upgrade_min_left_s"]:
        d = kit.kit_deficit(inventory, equipment, cfg, targets=kit.UPGRADES)
        if d.orb or d.bowstring:
            return "upgrade"
    return "enter"
```
Extend `derive_phase(data, npcs, objects, inventory=None, equipment=None, cfg=None, now=None)`: keep the fight/loot/done rules first; then, where it returned `"enter"` on a visible Barrier with no fight seen, return `prep_phase(...)` when `data.get("cg_t0") is not None and inventory is not None`, else `"enter"`. Also return `prep_phase(...)` when no Barrier is visible but `cg_t0` is set and no fight seen (the player is out in the maze). `tick()` passes `inventory=controller.get_inventory()`, `equipment=controller.get_equipment()`, `cfg`, and calls `note_entry(data, controller.get_player(), time.time())` before deriving.

- [ ] **Step 4: Run** -- `python -m pytest tests/test_corrupted_gauntlet.py -q`; all pass (the older `test_derive_phase` must still pass: it passes no inventory, so the old `"enter"` answer stands).

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/corrupted_gauntlet.py ControllerAddon/tests/test_corrupted_gauntlet.py
git commit -m "corrupted_gauntlet: entry timer, prep phase ladder, derive_phase in front of enter

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: explore and gather

**Files:**
- Modify: `ControllerAddon/routines/corrupted_gauntlet.py`
- Test: `ControllerAddon/tests/test_corrupted_gauntlet.py` (append)

**Interfaces (produces):**
```python
def prep_state(ctx, cfg) -> dict | None      # builds/returns data["cg_prep"]: {"start_centre", "barrier", "outward", "pitch", "step": int, "sub": str, "revealed": set(offsets), "entered": set(offsets)}; None when the grid/barrier cannot be read
def light_node(ctx, node, cfg) -> bool       # click_menu_action "Light", confirm: node id changed on that tile OR chat "light the nodes" OR the edge opens; NODE_BUDGET_S 6
def gather_node(ctx, obj, cfg) -> bool       # click with RESOURCE_ACTION[name]; loop: confirm the item count rises within GATHER_BUDGET_S (8 s) per unit; stop when the depleted id appears on that tile or yield reached; interrupted by an attacker
def pick_up_drops(ctx, cfg) -> int            # ground items named ITEM_FRAME/ITEM_SHARDS/ITEM_GRYM/ITEM_RAW_FISH/ITEM_TELEPORT within 8 tiles: click Take (menu), confirm inventory delta
def attacker(npcs, me) -> dict | None         # nearest npc with interactingPlayer True within 4 tiles, excluding demi-bosses handled by the caller
def gather_ladder(ctx, cfg) -> str            # "prayer" / "attack" / "eat" / "flee_demi" / "none": applies one action when something attacks us
def explore_gather_phase(ctx, cfg) -> None    # one step of the route state machine per call (tick)
```

The route state machine (`explore_gather_phase`), per call:
1. `st = prep_state(ctx, cfg)`; if None: log and return. If `st["step"] >= len(maze.route())`: `data["cg_route_done"] = True`; return.
2. `offset, action, reveal = maze.route()[st["step"]]`; `target = maze.room_centre(...)`.
3. If `st["sub"] == "to_node"`: find the node between the current room's centre (`maze.room_of(me)` -> centre) and `target` (`maze.node_between(nodes_from_/objects, ...)`); if none in `/objects`, walk toward `target` (walk_to with tolerance 4) and return. Else if `maze.edge_open(imap, me_room_centre, target)`: `sub = "to_room"`. Else if `cheb(me, node) > 2`: `walk_to(node)` (tolerance 2); return. Else `light_node(node)`; on success `sub = "to_room"`; on failure count `st["node_fails"]`, after 2 skip the step (`step += 1`, log `[!]`).
4. If `sub == "to_room"`: if `cheb(me, target) > 4`: `walk_to(target)`; return. Else `st["entered"].add(offset)`; `sub = "gather"`.
5. If `sub == "gather"`: first `gather_ladder` (an attacker takes priority: prayer, then attack it, eat if low); then `pick_up_drops`; then the room's live resource nodes (from `/objects`, `maze.room_of(node) == offset`) whose item is in deficit, nearest first: `gather_node` one; if none left: if `reveal`: `sub = "reveal"` else advance (`step += 1; sub = "to_node"`).
6. If `sub == "reveal"`: node between `target` and `room_centre(reveal)`; walk within 2 tiles, `light_node`; record any demi-boss now listed in `/npcs` whose `room_of` == reveal into `data["cg_demi_bosses"][reveal]`; `st["revealed"].add(reveal)`; advance.
Weak monsters that attack during any sub-state are handled by `gather_ladder` at the top of the call. A demi-boss attacking us -> `flee_demi`: walk to the previous room's centre.

- [ ] **Step 1: Write the failing tests** (stubs: `controller.get_player/get_npcs/get_objects/get_ground_items/get_inventory/get_equipment/get_prayers/get_collision_grid/click_entity/click_menu_action/open_prayer_tab/open_inventory_tab`, and `cg.instance_nav.walk_to`)

```python
def _player_at(x, y, hp=80, prayer=40):
    return {"hp": hp, "prayer": prayer, "instX": x, "instY": y, "plane": 1, "regionId": g.REGION_CORRUPTED, "animation": -1}

def _grid(size=64):
    return {"baseX": 13056, "baseY": 5632, "plane": 1, "size": size, "flags": [0] * (size * size)}

def _obj(id_, name, x, y):
    return {"id": id_, "name": name, "instX": x, "instY": y, "objectType": "GameObject", "screenX": 5, "screenY": 5, "actions": []}


def test_prep_state_derives_geometry_once(monkeypatch):
    ctx = _Ctx(); ctx.state.data["cg_t0"] = 0.0
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(13056 + 30, 5632 + 30))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_obj(g.OBJ_BARRIER, "Barrier", 13056 + 30, 5632 + 22)])
    st = cg.prep_state(ctx, cg.DEFAULT_CONFIG)
    assert st["outward"] == (0, 1) and st["barrier"] == (13086, 5654) and st["step"] == 0 and st["sub"] == "to_node"
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: pytest.fail("grid re-read"))
    assert cg.prep_state(ctx, cg.DEFAULT_CONFIG) is st


def test_attacker_and_gather_ladder_prayer_then_attack(monkeypatch):
    me = (100, 100)
    npcs = [{"id": 9045, "index": 3, "name": "Corrupted Wolf", "instX": 102, "instY": 100, "interactingPlayer": True, "screenX": 5, "screenY": 5},
            {"id": 9040, "index": 4, "name": "Corrupted Rat", "instX": 101, "instY": 100, "interactingPlayer": False, "screenX": 5, "screenY": 5}]
    assert cg.attacker(npcs, me)["id"] == 9045
    assert cg.attacker([], me) is None
    calls = []
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*me))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: npcs)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"active": {}, "tabOpen": True, "icons": {"PROTECT_FROM_MELEE": {"screenX": 1, "screenY": 1}}})
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": None})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(("click", entity.get("name") or "icon")) or True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: True)
    assert cg.gather_ladder(ctx, cg.DEFAULT_CONFIG) == "prayer"
    monkeypatch.setattr(controller, "get_prayers", lambda: {"active": {"PROTECT_FROM_MELEE": True}, "tabOpen": True, "icons": {}})
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda index: (lambda: None))
    assert cg.gather_ladder(ctx, cg.DEFAULT_CONFIG) == "attack"
    assert ("click", "Corrupted Wolf") in calls


def test_gather_ladder_flees_a_demi_boss(monkeypatch):
    ctx = _Ctx(); ctx.state.data["cg_prep"] = {"prev_centre": (90, 90)}
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [{"id": 9046, "index": 1, "name": "Corrupted Bear", "instX": 102, "instY": 100, "interactingPlayer": True}])
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: walks.append((x, y)) or True)
    assert cg.gather_ladder(ctx, cg.DEFAULT_CONFIG) == "flee_demi"
    assert walks == [(90, 90)]


def test_gather_node_loops_until_depleted(monkeypatch):
    ctx = _Ctx()
    deposit = next(n for n in g.RESOURCE_NODES.values() if "Deposit" in n)
    dep_id = next(i for i, n in g.RESOURCE_NODES.items() if n == deposit)
    depleted_id = next(i for i, n in g.RESOURCE_NODES_DEPLETED.items() if "Deposit" in n)
    node = _obj(dep_id, deposit, 100, 100)
    ore = {"n": 0}
    reads = {"k": 0}
    def objects(radius=None, fresh=False, interactable=False):
        reads["k"] += 1
        return [_obj(depleted_id if ore["n"] >= 3 else dep_id, deposit, 100, 100)]
    monkeypatch.setattr(controller, "get_objects", objects)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"slot": 0, "name": g.ITEM_ORE, "qty": ore["n"]}] if ore["n"] else [])
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(101, 100))
    def click(ser, entity, **kw):
        assert kw.get("expected_action") == "Mine"
        ore["n"] += 1
        return True
    monkeypatch.setattr(controller, "click_entity", click)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: (lambda: None), raising=False)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: check())
    monkeypatch.setattr(cg, "_wait_tick", lambda ctx: None)
    assert cg.gather_node(ctx, node, cg.DEFAULT_CONFIG)
    assert ore["n"] == 3


def test_explore_gather_phase_lights_then_enters_then_gathers(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data; data["cg_t0"] = 0.0
    S = (13056 + 30, 5632 + 30)
    pitch = cg.DEFAULT_CONFIG["room_pitch"]
    node_xy = (S[0] + pitch // 2, S[1])                  # between S and the room to the right (outward north)
    right_room = (S[0] + pitch, S[1])
    state = {"pos": S, "lit": False, "walks": [], "gathered": []}
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*state["pos"]))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    linum = next(n for n in g.RESOURCE_NODES.values() if "Linum" in n)
    linum_id = next(i for i, n in g.RESOURCE_NODES.items() if n == linum)
    def objects(radius=None, fresh=False, interactable=False):
        return [_obj(g.OBJ_BARRIER, "Barrier", S[0], S[1] - 8), _obj(35999, "Node", *node_xy),
                _obj(linum_id, linum, right_room[0] + 2, right_room[1])]
    monkeypatch.setattr(controller, "get_objects", objects)
    monkeypatch.setattr(cg.maze, "edge_open", lambda imap, a, b: state["lit"])
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: (state["walks"].append((x, y)), state.__setitem__("pos", (x, y)))[1] or True)
    monkeypatch.setattr(cg, "light_node", lambda ctx, node, cfg: state.__setitem__("lit", True) or True)
    monkeypatch.setattr(cg, "gather_node", lambda ctx, obj, cfg: state["gathered"].append(obj["name"]) or True)
    monkeypatch.setattr(cg, "gather_ladder", lambda ctx, cfg: "none")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 0)
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # walk to the node
    assert state["walks"] and cg.hunllef.cheb(state["walks"][-1], node_xy) <= 2
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # light it
    assert state["lit"] and data["cg_prep"]["sub"] == "to_room"
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # walk into the room
    assert state["walks"][-1] == right_room
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # gather the linum (deficit: yes)
    assert state["gathered"] == [linum]
```

- [ ] **Step 2: Run to verify it fails** -- `python -m pytest tests/test_corrupted_gauntlet.py -q -k "prep_state or attacker or flees or gather_node or explore_gather"`; `AttributeError`.

- [ ] **Step 3: Implement** (append to the routine; constants `NODE_BUDGET_S = 6.0`, `GATHER_BUDGET_S = 8.0`, `NODE_MAX_FAILS = 2`, `ATTACKER_RANGE = 4`, `DROP_RANGE = 8`, `ROOM_ARRIVAL_TOLERANCE = 4`)

```python
def _me():
    p = controller.get_player() or {}
    return (p.get("instX"), p.get("instY")), p


def prep_state(ctx, cfg):
    data = ctx.state.data
    st = data.get("cg_prep")
    if st is not None:
        return st
    imap = _cached_imap(ctx)
    me, _ = _me()
    if imap is None or me[0] is None:
        return None
    objects = controller.get_objects(radius=cfg["objects_radius"], fresh=True)
    barrier = next((o for o in objects if o.get("id") == g.OBJ_BARRIER), None)
    if barrier is None:
        return None
    comp = maze.component(imap, me)
    centre = maze.component_centre(comp)
    bpos = (barrier["instX"], barrier["instY"])
    st = {"start_centre": centre, "barrier": bpos, "outward": maze.outward_from(bpos, centre),
          "pitch": cfg["room_pitch"], "step": 0, "sub": "to_node", "revealed": set(), "entered": {(0, 0)},
          "node_fails": 0, "prev_centre": centre}
    data["cg_prep"] = st
    data.setdefault("cg_demi_bosses", {})
    debug_log.log("cg", f"prep: start centre {centre}, barrier {bpos}, outward {st['outward']}")
    return st


def _centre(st, offset):
    return maze.room_centre(st["start_centre"], st["pitch"], st["outward"], offset)


def _room(st, tile):
    return maze.room_of(tile, st["start_centre"], st["pitch"], st["outward"])


def light_node(ctx, node, cfg):
    before = (node.get("id"), node.get("instX"), node.get("instY"))
    mark = ctx.events.mark() if ctx.events is not None else None
    controller.click_menu_action(ctx.ser, node, "Light", target_hint="Node", offset=(0, 0))

    def lit():
        for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True):
            if (o.get("instX"), o.get("instY")) == before[1:] and o.get("id") != before[0]:
                return True
        if ctx.events is not None:
            for t, p in ctx.events.since(mark):
                if t == "CHAT_MESSAGE" and "light the nodes" in str((p or {}).get("message", "")):
                    return True
        return False
    ok = _confirm(ctx, lit, NODE_BUDGET_S)
    debug_log.log("cg", f"light node at {before[1:]}: {'ok' if ok else 'no change'}")
    return ok


def gather_node(ctx, obj, cfg):
    name = obj.get("name")
    action = g.RESOURCE_ACTION.get(name)
    item = g.RESOURCE_ITEM.get(name)
    if action is None:
        return False
    pos = (obj.get("instX"), obj.get("instY"))
    got = 0
    for _ in range(g.RESOURCE_YIELD[name] + 2):
        live = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                     if (o.get("instX"), o.get("instY")) == pos), None)
        if live is None or live.get("id") in g.RESOURCE_NODES_DEPLETED:
            break
        me, _ = _me()
        if cg_attacker_present(me):
            return got > 0
        before = kit.count(controller.get_inventory(), item)
        controller.click_entity(ctx.ser, live, expected_action=action)
        if _confirm(ctx, lambda: kit.count(controller.get_inventory(), item) > before, GATHER_BUDGET_S):
            got += 1
        else:
            break
        _wait_tick(ctx)
    return got > 0


def cg_attacker_present(me):
    return attacker(controller.get_npcs(fresh=True), me) is not None


def attacker(npcs, me):
    best = None
    for n in npcs or []:
        if not n.get("interactingPlayer"):
            continue
        pos = (n.get("instX"), n.get("instY"))
        if pos[0] is None or hunllef.cheb(pos, me) > ATTACKER_RANGE:
            continue
        if best is None or hunllef.cheb(pos, me) < hunllef.cheb((best["instX"], best["instY"]), me):
            best = n
    return best


def pick_up_drops(ctx, cfg):
    wanted = {g.ITEM_FRAME, g.ITEM_SHARDS, g.ITEM_GRYM, g.ITEM_RAW_FISH, g.ITEM_TELEPORT, g.ITEM_ORB, g.ITEM_BOWSTRING}
    me, _ = _me()
    taken = 0
    for item in controller.get_ground_items(fresh=True) or []:
        if item.get("name") not in wanted or hunllef.cheb((item.get("instX"), item.get("instY")), me) > DROP_RANGE:
            continue
        before = len(controller.get_inventory())
        controller.click_menu_action(ctx.ser, item, "Take", target_hint=item.get("name"), offset=(0, 0))
        if _confirm(ctx, lambda: len(controller.get_inventory()) != before or kit.count(controller.get_inventory(), item.get("name")) > 0, 4.0):
            taken += 1
    return taken


def gather_ladder(ctx, cfg):
    me, player = _me()
    npcs = controller.get_npcs(fresh=True)
    foe = attacker(npcs, me)
    if foe is None:
        return "none"
    if foe.get("id") in g.DEMI_BOSSES or foe.get("id") in g.DEMI_BOSS_DROP:
        prev = (ctx.state.data.get("cg_prep") or {}).get("prev_centre")
        if prev:
            debug_log.log("cg", f"[!] demi-boss {foe.get('name')} attacking -- leaving toward {prev}")
            instance_nav.walk_to(ctx, prev[0], prev[1])
        return "flee_demi"
    style = g.ATTACK_STYLE_BY_NPC_ID.get(foe.get("id"), "MELEE")
    prayer = g.PROTECT_FOR_STYLE[style]
    if not (controller.get_prayers().get("active") or {}).get(prayer):
        do_prayer(ctx, None, prayer, cfg)
        return "prayer"
    if int(player.get("hp") or 0) <= cfg["eat_at"]:
        inv = controller.get_inventory()
        fish = [i for i in inv if i.get("name") in (g.ITEM_FISH, g.ITEM_CFISH)]
        if fish:
            _click_item(ctx, fish[0], "Eat")
            return "eat"
    if (controller.get_combat() or {}).get("targetId") != foe.get("id"):
        controller.click_entity(ctx.ser, foe, refresh_target=controller.npc_index_refresh_target(foe.get("index")),
                                refresh_legs=2, expected_action="Attack")
    return "attack"


def _nodes(objects):
    return [o for o in objects if o.get("id") in g.NODE_IDS]


def explore_gather_phase(ctx, cfg):
    data = ctx.state.data
    st = prep_state(ctx, cfg)
    if st is None:
        debug_log.log("cg", "prep: cannot read the grid/barrier yet")
        return
    steps = maze.route()
    if st["step"] >= len(steps):
        data["cg_route_done"] = True
        return
    if gather_ladder(ctx, cfg) != "none":
        return
    offset, _, reveal = steps[st["step"]]
    target = _centre(st, offset)
    me, _ = _me()
    imap = _cached_imap(ctx)
    objects = controller.get_objects(radius=cfg["objects_radius"], fresh=True)
    here_centre = _centre(st, _room(st, me))

    def advance():
        st["step"] += 1
        st["sub"] = "to_node"
        st["node_fails"] = 0
        st["prev_centre"] = here_centre

    if st["sub"] == "to_node":
        node = maze.node_between(_nodes(objects), here_centre, target, st["pitch"])
        if maze.edge_open(imap, here_centre, target):
            st["sub"] = "to_room"
        elif node is None:
            instance_nav.walk_to(ctx, target[0], target[1], tolerance=ROOM_ARRIVAL_TOLERANCE)
        elif hunllef.cheb(me, (node["instX"], node["instY"])) > 2:
            instance_nav.walk_to(ctx, node["instX"], node["instY"], tolerance=2)
        elif light_node(ctx, node, cfg):
            st["sub"] = "to_room"
        else:
            st["node_fails"] += 1
            if st["node_fails"] >= NODE_MAX_FAILS:
                debug_log.log("cg", f"[!] node toward {offset} will not light -- skipping the step")
                advance()
        return
    if st["sub"] == "to_room":
        if hunllef.cheb(me, target) > ROOM_ARRIVAL_TOLERANCE:
            instance_nav.walk_to(ctx, target[0], target[1], tolerance=ROOM_ARRIVAL_TOLERANCE)
            return
        st["entered"].add(offset)
        st["sub"] = "gather"
        return
    if st["sub"] == "gather":
        pick_up_drops(ctx, cfg)
        deficit = kit.kit_deficit(controller.get_inventory(), controller.get_equipment(), cfg)
        want = {g.ITEM_ORE: deficit.ore, g.ITEM_BARK: deficit.bark, g.ITEM_LINUM: deficit.linum,
                g.ITEM_GRYM: deficit.grym, g.ITEM_RAW_FISH: deficit.fish}
        nodes = [o for o in objects if o.get("id") in g.RESOURCE_NODES and _room(st, (o["instX"], o["instY"])) == offset
                 and want.get(g.RESOURCE_ITEM.get(o.get("name")), 0) > 0]
        if nodes:
            nodes.sort(key=lambda o: hunllef.cheb((o["instX"], o["instY"]), me))
            gather_node(ctx, nodes[0], cfg)
            return
        if reveal and reveal not in st["revealed"]:
            st["sub"] = "reveal"
            return
        advance()
        return
    if st["sub"] == "reveal":
        rc = _centre(st, reveal)
        node = maze.node_between(_nodes(objects), target, rc, st["pitch"])
        if node is None:
            debug_log.log("cg", f"[!] no node toward the reveal room {reveal}")
            st["revealed"].add(reveal); advance(); return
        if hunllef.cheb(me, (node["instX"], node["instY"])) > 2:
            instance_nav.walk_to(ctx, node["instX"], node["instY"], tolerance=2)
            return
        light_node(ctx, node, cfg)
        for n in controller.get_npcs(fresh=True):
            if n.get("id") in g.DEMI_BOSS_DROP and _room(st, (n.get("instX"), n.get("instY"))) == reveal:
                data["cg_demi_bosses"][reveal] = n["id"]
                debug_log.log("cg", f"revealed {n.get('name')} ({n['id']}) in {reveal}")
        st["revealed"].add(reveal)
        advance()
```
`import hunllef` at the top of the routine (it already imports `hunllef as h`; use `h` consistently -- the code above says `hunllef.`; write whichever alias the file uses). `tick()`: dispatch `"explore_gather"` to `explore_gather_phase`.

- [ ] **Step 4: Run** -- `python -m pytest tests/test_corrupted_gauntlet.py -q`; all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/corrupted_gauntlet.py ControllerAddon/tests/test_corrupted_gauntlet.py
git commit -m "corrupted_gauntlet: explore/gather on the route -- nodes, rooms, resources, drops, monsters

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: crafting

**Files:**
- Modify: `ControllerAddon/routines/corrupted_gauntlet.py`
- Test: `ControllerAddon/tests/test_corrupted_gauntlet.py` (append)

**Interfaces (produces):**
```python
def teleport_home(ctx, cfg) -> bool           # click ITEM_TELEPORT "Activate" (expected_action), confirm: within 6 tiles of OBJ_SINGING_BOWL in /objects within 8 s; if no crystal: instance_nav.walk_to(start_centre)
def bowl_make(ctx, key, cfg) -> bool          # walk to the bowl if > 3 tiles; click Sing-crystal; wait for a group-270 widget whose text contains BOWL_TEXT[key] (get_widgets_dump filtered); click it (check_ui_blockers False); confirm inventory delta within 4 s
def use_on(ctx, item_a_name, item_b_name) -> bool   # open inventory tab; click item A (expected_action "Use"); click item B; confirm both counts changed within 3 s
def cook_all(ctx, cfg) -> bool                # click OBJ_RANGE "Cook"; confirm raw count falls; poll until raw == 0 or no change for 5 s
def pump_fill(ctx, cfg) -> bool               # click OBJ_WATER_PUMP "Fill-from"; confirm water vials >= vials
def wear(ctx, name) -> bool                   # open inventory tab; click item (expected_action in ("Wear","Wield")); confirm /equipment
def craft_phase(ctx, cfg) -> None             # teleport_home once (data["cg_home"]), then run kit.craft_plan one step per call, re-planning from the live inventory after each step; when the plan is empty: data["cg_crafted"] = True
```

- [ ] **Step 1: Write the failing tests**

```python
def test_bowl_make_clicks_the_widget_by_text(monkeypatch):
    ctx = _Ctx()
    calls = []
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_obj(g.OBJ_SINGING_BOWL, "Singing Bowl", 101, 100)])
    inv = {"n": 0}
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"slot": 0, "name": "Corrupted helm (basic)", "qty": 1}] if inv["n"] else [])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append((entity.get("name") or entity.get("text"), kw.get("expected_action"))) or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 17, "text": "Corrupted helm", "screenX": 3, "screenY": 3, "actions": ["Make"]},
        {"group": 270, "child": 18, "text": "Corrupted body", "screenX": 4, "screenY": 3, "actions": ["Make"]},
        {"group": 162, "child": 1, "text": "Corrupted helm", "screenX": 9, "screenY": 9, "actions": []}])
    def confirm(ctx, check, budget_s=1.0):
        inv["n"] = 1
        return check()
    monkeypatch.setattr(cg, "_confirm", confirm)
    assert cg.bowl_make(ctx, "helm", cg.DEFAULT_CONFIG)
    assert calls[0] == ("Singing Bowl", "Sing-crystal")
    assert calls[1][0] == "Corrupted helm"           # the group-270 widget, not the chatbox line


def test_craft_phase_runs_the_plan_and_marks_crafted(monkeypatch):
    ctx = _Ctx(); ctx.state.data.update({"cg_t0": 0.0, "cg_home": True})
    steps = []
    plans = [[("bowl", "helm"), ("cook", None)], [("cook", None)], []]
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(cg.kit, "craft_plan", lambda inv, eq, cfg: plans.pop(0))
    monkeypatch.setattr(cg, "bowl_make", lambda ctx, key, cfg: steps.append(("bowl", key)) or True)
    monkeypatch.setattr(cg, "cook_all", lambda ctx, cfg: steps.append(("cook", None)) or True)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    assert steps == [("bowl", "helm"), ("cook", None)]
    assert ctx.state.data["cg_crafted"] is True


def test_use_on_clicks_both_items(monkeypatch):
    ctx = _Ctx()
    calls = []
    inv = [{"slot": 0, "name": g.ITEM_GRYM, "qty": 1, "screenX": 1, "screenY": 1},
           {"slot": 1, "name": g.ITEM_WATER_VIAL, "qty": 1, "screenX": 2, "screenY": 1}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append((entity["name"], kw.get("expected_action"))) or True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: True)
    assert cg.use_on(ctx, g.ITEM_GRYM, g.ITEM_WATER_VIAL)
    assert calls == [(g.ITEM_GRYM, "Use"), (g.ITEM_WATER_VIAL, None)]
```

- [ ] **Step 2: Run to verify it fails** -- `-k "bowl_make or craft_phase or use_on"`; `AttributeError`.

- [ ] **Step 3: Implement** (append; constants `BOWL_BUDGET_S = 4.0`, `HOME_BUDGET_S = 8.0`, `COOK_STALL_S = 5.0`)

```python
def _find_object(cfg, obj_id):
    return next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True) if o.get("id") == obj_id), None)


def _near(ctx, obj, tiles):
    me, _ = _me()
    pos = (obj.get("instX"), obj.get("instY"))
    if h.cheb(me, pos) > tiles:
        instance_nav.walk_to(ctx, pos[0], pos[1], tolerance=tiles, goal_adjacent=True)
        return False
    return True


def teleport_home(ctx, cfg):
    st = ctx.state.data.get("cg_prep") or {}
    crystal = next((i for i in controller.get_inventory() if i.get("name") == g.ITEM_TELEPORT), None)
    if crystal is not None:
        _click_item(ctx, crystal, "Activate")
        if _confirm(ctx, lambda: _find_object(cfg, g.OBJ_SINGING_BOWL) is not None, HOME_BUDGET_S):
            return True
    if st.get("start_centre"):
        return instance_nav.walk_to(ctx, st["start_centre"][0], st["start_centre"][1], tolerance=3)
    return False


def _bowl_widget(key):
    want = g.BOWL_TEXT[key]
    for w in controller.get_widgets_dump():
        if w.get("group") == g.BOWL_GROUP and want in str(w.get("text") or "") and w.get("screenX") is not None:
            return w
    return None


def bowl_make(ctx, key, cfg):
    bowl = _find_object(cfg, g.OBJ_SINGING_BOWL)
    if bowl is None or not _near(ctx, bowl, 3):
        return False
    controller.click_entity(ctx.ser, bowl, expected_action="Sing-crystal")
    if not _confirm(ctx, lambda: _bowl_widget(key) is not None, BOWL_BUDGET_S):
        debug_log.log("cg", f"bowl: no widget for {key}")
        return False
    widget = _bowl_widget(key)
    before = [(i.get("name"), i.get("qty")) for i in controller.get_inventory()]
    controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    ok = _confirm(ctx, lambda: [(i.get("name"), i.get("qty")) for i in controller.get_inventory()] != before, BOWL_BUDGET_S)
    debug_log.log("cg", f"bowl {key}: {'ok' if ok else 'no inventory change'}")
    return ok


def use_on(ctx, name_a, name_b):
    inv = controller.get_inventory()
    a = next((i for i in inv if i.get("name") == name_a), None)
    b = next((i for i in inv if i.get("name") == name_b), None)
    if a is None or b is None:
        return False
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, a, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False, expected_action="Use")
    controller.click_entity(ctx.ser, b, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    return _confirm(ctx, lambda: kit.count(controller.get_inventory(), name_a) < kit.count(inv, name_a), 3.0)


def cook_all(ctx, cfg):
    rng = _find_object(cfg, g.OBJ_RANGE)
    if rng is None or not _near(ctx, rng, 3):
        return False
    controller.click_entity(ctx.ser, rng, expected_action="Cook")
    last = kit.count(controller.get_inventory(), g.ITEM_RAW_FISH)
    stall = time.time() + COOK_STALL_S
    while time.time() < stall:
        _wait_tick(ctx)
        raw = kit.count(controller.get_inventory(), g.ITEM_RAW_FISH)
        if raw == 0:
            return True
        if raw < last:
            last, stall = raw, time.time() + COOK_STALL_S
    return last == 0


def pump_fill(ctx, cfg):
    pump = _find_object(cfg, g.OBJ_WATER_PUMP)
    if pump is None or not _near(ctx, pump, 3):
        return False
    vials = kit.count(controller.get_inventory(), g.ITEM_VIAL)
    controller.click_entity(ctx.ser, pump, expected_action="Fill-from")
    return _confirm(ctx, lambda: kit.count(controller.get_inventory(), g.ITEM_WATER_VIAL) >= vials, 6.0)


def wear(ctx, name):
    item = next((i for i in controller.get_inventory() if i.get("name") == name), None)
    if item is None:
        return False
    _click_item(ctx, item, ("Wear", "Wield"))
    return _confirm(ctx, lambda: any((v or {}).get("name") == name for v in (controller.get_equipment() or {}).values() if isinstance(v, dict)), 3.0)


def _pestle(ctx):
    return use_on(ctx, g.ITEM_PESTLE, g.ITEM_SHARDS)


def _potion(ctx):
    ok = True
    while kit.count(controller.get_inventory(), g.ITEM_WATER_VIAL) and kit.count(controller.get_inventory(), g.ITEM_GRYM):
        ok = use_on(ctx, g.ITEM_GRYM, g.ITEM_WATER_VIAL) and ok
    while kit.count(controller.get_inventory(), g.ITEM_GRYM_UNF) and kit.count(controller.get_inventory(), g.ITEM_DUST) >= 10:
        ok = use_on(ctx, g.ITEM_DUST, g.ITEM_GRYM_UNF) and ok
    return ok


def craft_phase(ctx, cfg):
    data = ctx.state.data
    if not data.get("cg_home"):
        if teleport_home(ctx, cfg):
            data["cg_home"] = True
        return
    plan = kit.craft_plan(controller.get_inventory(), controller.get_equipment(), cfg)
    if not plan:
        data["cg_crafted"] = True
        return
    op, arg = plan[0]
    ok = {"bowl": lambda: bowl_make(ctx, arg, cfg), "pump": lambda: pump_fill(ctx, cfg),
          "pestle": lambda: _pestle(ctx), "potion": lambda: _potion(ctx),
          "cook": lambda: cook_all(ctx, cfg), "wear": lambda: wear(ctx, arg)}[op]()
    data["cg_craft_fails"] = 0 if ok else data.get("cg_craft_fails", 0) + 1
    if data["cg_craft_fails"] >= 3:
        debug_log.log("cg", f"[!] craft step {op} {arg} failed 3 times -- skipping the rest")
        data["cg_crafted"] = True
```
`_click_item`'s `expected_action` parameter must accept a tuple (it forwards to `click_entity`, which does). `tick()`: dispatch `"craft"` to `craft_phase`.

- [ ] **Step 4: Run** -- `python -m pytest tests/test_corrupted_gauntlet.py -q`; all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/corrupted_gauntlet.py ControllerAddon/tests/test_corrupted_gauntlet.py
git commit -m "corrupted_gauntlet: craft phase -- teleport home, bowl by widget text, pump, pestle, potions, cook, wear

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: upgrade phase (demi-boss)

**Files:**
- Modify: `ControllerAddon/routines/corrupted_gauntlet.py`
- Test: `ControllerAddon/tests/test_corrupted_gauntlet.py` (append)

**Interfaces (produces):**
```python
def upgrade_phase(ctx, cfg) -> None
```
Behaviour, one step per call: pick the wanted demi-boss room (`data["cg_demi_bosses"]`, id in `DEMI_BOSS_WANTED`, drop not yet in inventory); sub-states in `data["cg_upgrade"]`: `"go"` walk to the middle-ring room below it (`offset minus one outward`), light the node toward it if the edge is closed, walk in; `"fight"`: hold `PROTECT_FOR_STYLE[style]`, wield the best weapon (attuned bow for a dragon since it prays nothing; the fight uses `do_wield`/`do_prayer`/`do_attack`-style clicks against this npc), eat at `eat_at`, until the npc is gone from `/npcs`; `"loot"`: `pick_up_drops`; `"home"`: `teleport_home` (or walk); `"craft"`: `bowl_make` the perfected weapon; then `data["cg_upgrade_done"] = True` (`prep_phase` returns `"enter"` once the deficit's orb/bowstring is 0 or this flag is set). Time guard on every call: if `time_left < cfg["upgrade_min_left_s"] - 30` abandon: teleport home and set the flag.

- [ ] **Step 1: Write the failing test**

```python
def test_upgrade_phase_walks_fights_loots_and_crafts(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data
    data.update({"cg_t0": 0.0, "cg_demi_bosses": {(0, 2): 9047}})
    S = (13056 + 30, 5632 + 30); pitch = cg.DEFAULT_CONFIG["room_pitch"]
    data["cg_prep"] = {"start_centre": S, "barrier": (S[0], S[1] - 8), "outward": (0, 1), "pitch": pitch,
                       "step": 10, "sub": "to_node", "revealed": {(0, 2)}, "entered": set(), "node_fails": 0, "prev_centre": S}
    state = {"pos": S, "dragon": True, "walks": [], "t": 100.0}
    monkeypatch.setattr(cg.time, "time", lambda: state["t"])
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*state["pos"]))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [{"id": 9047, "index": 9, "name": "Corrupted Dragon", "instX": S[0], "instY": S[1] + 2 * pitch, "interactingPlayer": True, "screenX": 5, "screenY": 5}] if state["dragon"] else [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (attuned)"}})
    monkeypatch.setattr(cg.maze, "edge_open", lambda imap, a, b: True)
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: (state["walks"].append((x, y)), state.__setitem__("pos", (x, y)))[1] or True)
    monkeypatch.setattr(cg, "fight_npc_step", lambda ctx, npc, cfg: state.__setitem__("dragon", False) or "attack")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 1)
    monkeypatch.setattr(cg, "teleport_home", lambda ctx, cfg: True)
    monkeypatch.setattr(cg, "bowl_make", lambda ctx, key, cfg: True)
    for _ in range(6):
        cg.upgrade_phase(ctx, cg.DEFAULT_CONFIG)
    assert state["walks"][0] == (S[0], S[1] + pitch)          # the middle-ring room below the dragon's
    assert state["walks"][1] == (S[0], S[1] + 2 * pitch)      # then into the dragon's room
    assert data.get("cg_upgrade_done") is True
```

- [ ] **Step 2: Run to verify it fails** -- `-k upgrade_phase`; `AttributeError`.

- [ ] **Step 3: Implement**

```python
def fight_npc_step(ctx, npc, cfg):
    """One tick of fighting an ordinary npc or demi-boss: prayer for its style, best weapon, eat,
    attack. Returns the rung taken."""
    style = g.ATTACK_STYLE_BY_NPC_ID.get(npc.get("id"), "MELEE")
    prayer = g.PROTECT_FOR_STYLE[style]
    player = controller.get_player() or {}
    if not (controller.get_prayers().get("active") or {}).get(prayer):
        do_prayer(ctx, None, prayer, cfg); return "prayer"
    inv = controller.get_inventory(); eq = controller.get_equipment()
    best = max((("bow", kit.owned_tier("bow", inv, eq)), ("staff", kit.owned_tier("staff", inv, eq))), key=lambda t: t[1])
    if best[1] > 0 and kit.owned_tier(best[0], [], eq) < best[1]:
        wear(ctx, g.weapon_name(best[0], best[1])); return "wield"
    if int(player.get("hp") or 0) <= cfg["eat_at"]:
        fish = [i for i in inv if i.get("name") in (g.ITEM_FISH, g.ITEM_CFISH)]
        if fish:
            _click_item(ctx, fish[0], "Eat"); return "eat"
    if (controller.get_combat() or {}).get("targetId") != npc.get("id"):
        controller.click_entity(ctx.ser, npc, refresh_target=controller.npc_index_refresh_target(npc.get("index")),
                                refresh_legs=2, expected_action="Attack")
    return "attack"


def upgrade_phase(ctx, cfg):
    data = ctx.state.data
    st = data.get("cg_prep")
    up = data.setdefault("cg_upgrade", {"sub": "go"})
    left = time_left(data, cfg=cfg)
    if st is None or left is None or (left < cfg["upgrade_min_left_s"] - 30 and up["sub"] in ("go", "fight")):
        debug_log.log("cg", "[!] upgrade abandoned -- out of time")
        teleport_home(ctx, cfg); data["cg_upgrade_done"] = True; return
    wanted = [(off, nid) for off, nid in data.get("cg_demi_bosses", {}).items() if nid in g.DEMI_BOSS_WANTED]
    if not wanted:
        data["cg_upgrade_done"] = True; return
    offset, nid = wanted[0]
    room = _centre(st, offset)
    below = _centre(st, (offset[0], offset[1] - 1))
    me, _ = _me()
    imap = _cached_imap(ctx)
    if up["sub"] == "go":
        if h.cheb(me, below) > ROOM_ARRIVAL_TOLERANCE and h.cheb(me, room) > ROOM_ARRIVAL_TOLERANCE:
            instance_nav.walk_to(ctx, below[0], below[1], tolerance=ROOM_ARRIVAL_TOLERANCE); return
        if not maze.edge_open(imap, below, room):
            node = maze.node_between(_nodes(controller.get_objects(radius=cfg["objects_radius"], fresh=True)), below, room, st["pitch"])
            if node is None or not light_node(ctx, node, cfg):
                data["cg_upgrade_done"] = True; return
        if h.cheb(me, room) > ROOM_ARRIVAL_TOLERANCE:
            instance_nav.walk_to(ctx, room[0], room[1], tolerance=ROOM_ARRIVAL_TOLERANCE); return
        up["sub"] = "fight"; return
    if up["sub"] == "fight":
        npc = next((n for n in controller.get_npcs(fresh=True) if n.get("id") == nid), None)
        if npc is None:
            up["sub"] = "loot"; return
        fight_npc_step(ctx, npc, cfg); return
    if up["sub"] == "loot":
        pick_up_drops(ctx, cfg); up["sub"] = "home"; return
    if up["sub"] == "home":
        if teleport_home(ctx, cfg):
            up["sub"] = "craft"
        return
    if up["sub"] == "craft":
        drop = g.DEMI_BOSS_DROP[nid]
        key = "staff" if drop == g.ITEM_ORB else "bow"
        bowl_make(ctx, key, cfg)
        wear(ctx, g.weapon_name(key, 3))
        data["cg_upgrade_done"] = True
```
In `prep_phase`, add `and not data.get("cg_upgrade_done")` to the upgrade condition. `tick()`: dispatch `"upgrade"` to `upgrade_phase`. `gather_ladder`'s ordinary-attacker branch can now call `fight_npc_step` instead of its own prayer/eat/attack block (keep its return values: map `fight_npc_step`'s rung through).

- [ ] **Step 4: Run** -- `python -m pytest tests/test_corrupted_gauntlet.py -q`; all pass.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/corrupted_gauntlet.py ControllerAddon/tests/test_corrupted_gauntlet.py
git commit -m "corrupted_gauntlet: upgrade phase -- revealed dragon/dark beast, drop, perfected weapon

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: tick wiring, tools, stuck watch, docs, suites

**Files:**
- Modify: `ControllerAddon/routines/corrupted_gauntlet.py`, `CLAUDE.md`
- Test: `ControllerAddon/tests/test_corrupted_gauntlet.py` (append)

- [ ] **Step 1: Tests**

```python
def test_tick_dispatches_prep_phases(monkeypatch):
    ctx = _Ctx(); ctx.state.data["cg_t0"] = 0.0
    monkeypatch.setattr(cg.time, "time", lambda: 10.0)
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [{"id": g.OBJ_BARRIER}])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    called = []
    monkeypatch.setattr(cg, "explore_gather_phase", lambda ctx, cfg: called.append("explore"))
    cg.tick(ctx)
    assert called == ["explore"] and ctx.state.data["cg_phase"] == "explore_gather"


def test_ensure_tools_takes_from_storage(monkeypatch):
    ctx = _Ctx()
    calls = []
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"slot": 0, "name": g.ITEM_SCEPTRE, "qty": 1}])
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_obj(g.OBJ_TOOL_STORAGE, "Tool Storage", 101, 100)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(kw.get("expected_action")) or True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: True)
    assert cg.ensure_tools(ctx, cg.DEFAULT_CONFIG)
    assert calls == ["Take"]


def test_stuck_watch_stops_after_no_progress(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0)
    cg.stuck_watch(ctx, ("explore_gather", (100, 100), 5))
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0 + cg.STUCK_STOP_S + 1)
    cg.stuck_watch(ctx, ("explore_gather", (100, 100), 5))
    assert ctx.state.safety_stopped
    ctx2 = _Ctx()
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0)
    cg.stuck_watch(ctx2, ("explore_gather", (100, 100), 5))
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0 + cg.STUCK_STOP_S + 1)
    cg.stuck_watch(ctx2, ("explore_gather", (101, 100), 5))     # moved: no stop
    assert not ctx2.state.safety_stopped
```

- [ ] **Step 2: Implement** (`STUCK_WARN_S = 20.0`, `STUCK_STOP_S = 60.0`)

```python
def ensure_tools(ctx, cfg):
    """Sceptre, axe, pickaxe, harpoon, pestle -- all given at entry; a missing one is re-taken
    from the Tool Storage before leaving the start room."""
    names = {g.ITEM_SCEPTRE, g.ITEM_AXE, g.ITEM_PICKAXE, g.ITEM_HARPOON, g.ITEM_PESTLE}
    have = {i.get("name") for i in controller.get_inventory()} | {((controller.get_equipment() or {}).get("weapon") or {}).get("name")}
    if names <= have:
        return True
    storage = _find_object(cfg, g.OBJ_TOOL_STORAGE)
    if storage is None or not _near(ctx, storage, 3):
        return False
    controller.click_entity(ctx.ser, storage, expected_action="Take")
    return _confirm(ctx, lambda: names <= {i.get("name") for i in controller.get_inventory()}, 4.0)


def stuck_watch(ctx, signature):
    """signature = (phase, player tile, inventory size). No change for STUCK_WARN_S logs [!];
    STUCK_STOP_S safety-stops -- except in enter/fight/loot, which have their own ceilings."""
    data = ctx.state.data
    now = time.time()
    if signature != data.get("cg_stuck_sig"):
        data["cg_stuck_sig"] = signature
        data["cg_stuck_since"] = now
        data["cg_stuck_warned"] = False
        return
    idle = now - data.get("cg_stuck_since", now)
    if idle >= STUCK_STOP_S and signature[0] in ("explore_gather", "craft", "upgrade"):
        ctx.state.trigger_safety_stop(f"cg: no progress for {int(idle)}s in {signature[0]}")
    elif idle >= STUCK_WARN_S and not data.get("cg_stuck_warned"):
        debug_log.log("cg", f"[!] cg: STUCK? {int(idle)}s without progress in {signature[0]}")
        data["cg_stuck_warned"] = True
```
`tick()`: after `note_entry` and phase derivation, when the phase is a prep phase: call `ensure_tools` once (`data["cg_tools_ok"]`), then dispatch `explore_gather_phase` / `craft_phase` / `upgrade_phase`, then `stuck_watch(ctx, (phase, me, len(inventory)))`. Keep `enter/fight/loot/done` dispatch as is. `before_break`: also play out `upgrade` (walk home first) -- simplest: if the phase is a prep phase, call `teleport_home` so the break happens in the start room.

- [ ] **Step 3: CLAUDE.md** -- extend the "Current state of routines/corrupted_gauntlet.py" section with a paragraph: sub-project 3 adds `explore_gather` / `craft` / `upgrade` in front of `enter`; the route is the user's (relative to the start side, three border reveals from the middle ring, full inner-ring circle); rooms are addressed in instance space as (right, outward) offsets from the start room with a configurable pitch (16, unverified); the bowl is driven by widget TEXT in group 270; time gates 270/330/360/420 s; **not yet run live** -- the spec's live checklist is the first session's worklist, starting with whether `/objects` lists unopened rooms and the real room pitch.

- [ ] **Step 4: Suites** -- from `ControllerAddon/`: `python -m pytest tests/ -q` (all pass) and `python -m py_compile routines/corrupted_gauntlet.py gauntlet_kit.py gauntlet_maze.py gauntlet_ids.py`. No Java change; `./gradlew.bat test` need not run.

- [ ] **Step 5: Commit**

```bash
git add ControllerAddon/routines/corrupted_gauntlet.py ControllerAddon/tests/test_corrupted_gauntlet.py CLAUDE.md
git commit -m "corrupted_gauntlet: prep dispatch, tool storage, stuck watch; CLAUDE.md state

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** route (Task 3 `ROUTE`, Task 5 state machine incl. the three reveals and demi-boss recording); room model (Task 3 geometry with a configurable pitch, Task 5 `prep_state`); phases and time control (Task 4 `prep_phase`, `time_left`, `note_entry`; Task 8 dispatch and stuck watch); kit ladder/deficits/craft plan (Task 2); monsters during gathering (Task 5 `gather_ladder`, Task 7 `fight_npc_step`, demi-boss flee); crafting mechanics (Task 6); upgrade (Task 7); error handling (node fails -> skip step; craft fails -> skip; tools from storage; no crystal -> walk home; stuck watch); tests per task; live checklist stays in the spec and CLAUDE.md.
- **Placeholders:** none. Two facts are deliberately unknown and left as config/comments: the room pitch (16, live-verified) and the dark beast id (tables say where to add it).
- **Type consistency:** `kit.kit_deficit(inventory, equipment, cfg, targets=)` used in Tasks 4, 5; `Deficit` field names (`ore, bark, linum, shards, frames, fish, grym, orb, bowstring`) match `gather_value` and Task 5's `want` map; `maze.route()` entries are `(offset, "enter", reveal)` and Task 5 unpacks three; `maze.room_centre(start_centre, pitch, outward, offset)` argument order matches every call; `_centre/_room` wrappers in Task 5 are used by Task 7; `light_node(ctx, node, cfg)`, `gather_node(ctx, obj, cfg)`, `pick_up_drops(ctx, cfg)`, `bowl_make(ctx, key, cfg)`, `teleport_home(ctx, cfg)`, `fight_npc_step(ctx, npc, cfg)` signatures match their tests' monkeypatch lambdas; `_confirm(ctx, check, budget_s)` is the existing helper (positional budget); `h` is the routine's existing alias for `hunllef` -- Task 5's code uses `hunllef.`; the implementer writes `h.` (noted in the task).
