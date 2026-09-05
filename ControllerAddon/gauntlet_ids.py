"""Gauntlet ids, each with its source. Pure data -- sub-project 2 (the Hunllef fight) and 3 (the
maze) import from here and never re-derive an id.

Sources:
  ZL  = ZombieLite, C:/Users/Kiyo/Documents/ZombieLite/src/main/java/com/salvation/data/
        (NpcId, AnimationId, ProjectileId, RegionId) and plugins/gauntlet/GauntletPlugin.java
        (hazard tiles, tornado lifespan, varbit 9177) and npcs/Hunllef.java (attack cadence).
  REC = recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.summary (object/npc ids seen).

UDP event payload shapes (M, final-fix-brief.md; ControllerPlugin.java's sendNpcSpawnEvent /
onProjectileMoved are the senders, bridge_events.py's EventStream the reader):
  PROJECTILE_SPAWNED: {id, remainingCycles, targetIndex, x, y, instX, instY, plane, tick}
    -- targetIndex is the NPC index being aimed at (-2 = local player, -1 = none); x/y/instX/instY
    are the projectile's own position, not the target's.
  NPC_SPAWNED / NPC_DESPAWNED: {id, index, name, x, y, instX, instY, plane, tick}
    -- id/index are the npc's own (see NPCProvider's /npcs for what these mean generally); x/y is
    the template-corrected WorldPoint (WorldPoint.fromLocalInstance), instX/instY the instance-
    space twin (equal outside an instance; see instance_nav.py's own module docstring).
"""

# --- NPCs (ZL NpcId; REC confirms 9035-9038 as "Corrupted Hunllef", 9039 as the tornado) ---
HUNLLEF_CRYSTALLINE = {9021, 9022, 9023, 9024}
HUNLLEF_CORRUPTED = {9035, 9036, 9037, 9038}
HUNLLEF = HUNLLEF_CRYSTALLINE | HUNLLEF_CORRUPTED
TORNADO = {9025, 9039}                       # crystalline / corrupted
WEAK_MONSTERS = {9040, 9041, 9042, 9043, 9044, 9045}   # REC: rat, spider, bat, unicorn, scorpion, wolf
DEMI_BOSSES = {9046: "Corrupted Bear", 9047: "Corrupted Dragon"}   # REC; Dark Beast id: read off a
                                                                     # recording that meets one

# --- Hunllef animations (ZL AnimationId) ---
ANIM_ATTACK = 8419
ANIM_SPECIAL_PRAYER = 8418        # the prayer-disabling attack; counts as a standard attack
ANIM_SPAWN = 8753
ANIM_SWITCH_STYLE = 8754
ANIM_SPECIAL_TORNADO = 8755
ANIM_STOMP = 8420
ANIM_ATTACK_LIKE = {ANIM_ATTACK, ANIM_SPECIAL_PRAYER}

# --- Attack cadence (ZL Hunllef.onAnimationChanged: next attack at tick+5; style flips every
#     4 standard attacks, (count // 4) % 2 == 0 -> RANGED else MAGIC) ---
HUNLLEF_ATTACK_PERIOD_TICKS = 5
HUNLLEF_ATTACKS_PER_STYLE = 4
TORNADO_LIFESPAN_TICKS = 20       # ZL GauntletPlugin.TORNADO_LIFESPAN_TICKS

# --- Projectiles (ZL ProjectileId) ---
PROJ_BOW = 249
PROJ_STAFF = {1720, 1723}         # crystalline / corrupted
PROJ_PRAYER_ATTACK = {1713, 1714}

# --- Floor hazards (ZL GauntletPlugin.HAZARD_TILE_OBJECT_IDS; GroundObjects) ---
HAZARD_TILES = {36047, 36048}

# --- Varbits / regions ---
VARBIT_FIGHTING_HUNLLEF = 9177    # ZL: 1 while in the boss room
REGION_CORRUPTED = 7768           # ZL RegionId; REC: the whole run is this one region
REGION_NORMAL = 7512

# --- Lobby objects (REC summary) ---
OBJ_BARRIER = 37337
OBJ_ENTRANCE = 37340              # "The Gauntlet"
OBJ_REWARD_CHEST = 37341

# --- Arena utility objects (REC summary "object ids seen"; singular, no depleted counterpart) ---
OBJ_SINGING_BOWL = 35966          # "Sing-crystal" -- crafts weapons/armour from crystal shards
OBJ_TOOL_STORAGE = 35977          # "Take" -- pickaxe/axe/harpoon
OBJ_RANGE = 35980                 # "Cook"
OBJ_WATER_PUMP = 35981            # "Fill-from" -- fills a vial with water
OBJ_ILLUMINATED_SYMBOL = 35992    # maze crystal-lighting puzzle (sub-project 3), not a resource
OBJ_INACTIVE_SYMBOL = 35994       #   node -- kept as its own pair of constants rather than folded
                                   #   into RESOURCE_NODES/_DEPLETED, which are for gathering only

# --- Resource nodes (REC summary; the wiki's own name for this object group -- Corrupt Deposit/
#     Phren Roots/Fishing Spot/Grym Root/Linum Tirinum are the Gauntlet's five gatherable-resource
#     types). Deliberately id: name DICTS rather than individual OBJ_<NAME> ints for these ten
#     (plus the two Node ids below) -- a routine's actual question is "is this object I'm looking
#     at ACTIVE or DEPLETED", i.e. set/dict membership, not a single hardcoded id; see
#     tests/test_gauntlet_ids.py's completeness test for why this keeps every recorded id in
#     exactly one place (an OBJ_<NAME> constant here too would double-count them). "Depleted" is
#     decided from the summary's own "(Depleted)" name suffix. ---
RESOURCE_NODES = {
    35967: "Corrupt Deposit",
    35969: "Corrupt Phren Roots",
    35971: "Corrupt Fishing Spot",
    35973: "Corrupt Grym Root",
    35975: "Corrupt Linum Tirinum",
    # 35998/35999 ("Node", the "Light" action): the summary gives BOTH ids the identical name
    # "Node" with no "(Depleted)" suffix on either -- unlike every resource pair above, it does
    # not say which is the active state and which is exhausted. Per this task's own fallback
    # rule, both live in RESOURCE_NODES rather than guessing one into RESOURCE_NODES_DEPLETED.
    # WHICH IS DEPLETED: VERIFY LIVE.
    35998: "Node",
    35999: "Node",
}
RESOURCE_NODES_DEPLETED = {
    35968: "Corrupt Deposit (Depleted)",
    35970: "Corrupt Phren Roots (Depleted)",
    35972: "Corrupt Fishing Spot (Depleted)",
    35974: "Corrupt Grym Root (Depleted)",
    35976: "Corrupt Linum Tirinum (Depleted)",
}
