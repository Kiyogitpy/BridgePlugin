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
        if tiers[kind] == 0 and frames > 0 and shards >= ATTUNE_SHARDS:
            frames -= 1; tiers[kind] = 1; plan.append(("bowl", kind))
            shards -= ATTUNE_SHARDS; tiers[kind] = 2; plan.append(("bowl", kind))
        elif tiers[kind] == 1 and shards >= ATTUNE_SHARDS:
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
