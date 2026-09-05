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
#     -- a routine's actual question is "is this object I'm looking at ACTIVE or DEPLETED", i.e.
#     set/dict membership, not a single hardcoded id; see tests/test_gauntlet_ids.py's
#     completeness test for why this keeps every recorded id in exactly one place (an OBJ_<NAME>
#     constant here too would double-count them). "Depleted" is decided from the summary's own
#     "(Depleted)" name suffix. The Node ids (35998/35999, "Light" action) are handled separately
#     in NODE_IDS since they are not gatherable resources. ---
RESOURCE_NODES = {
    35967: "Corrupt Deposit",
    35969: "Corrupt Phren Roots",
    35971: "Corrupt Fishing Spot",
    35973: "Corrupt Grym Root",
    35975: "Corrupt Linum Tirinum",
}
RESOURCE_NODES_DEPLETED = {
    35968: "Corrupt Deposit (Depleted)",
    35970: "Corrupt Phren Roots (Depleted)",
    35972: "Corrupt Fishing Spot (Depleted)",
    35974: "Corrupt Grym Root (Depleted)",
    35976: "Corrupt Linum Tirinum (Depleted)",
}

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

# --- Resource gathering and crafting items (REC inventory names) ---
ITEM_ORE = "Corrupted ore"
ITEM_BARK = "Phren bark"
ITEM_LINUM = "Linum tirinum"
ITEM_GRYM = "Grym leaf"
ITEM_RAW_FISH = "Raw paddlefish"
ITEM_FISH = "Paddlefish"
ITEM_CFISH = "Corrupted paddlefish"
ITEM_SHARDS = "Corrupted shards"
ITEM_FRAME = "Weapon frame"
ITEM_ORB = "Corrupted orb"
ITEM_BOWSTRING = "Corrupted bowstring"
ITEM_SPIKE = "Corrupted spike"
ITEM_VIAL = "Vial"
ITEM_WATER_VIAL = "Water-filled vial"
ITEM_GRYM_UNF = "Grym potion (unf)"
ITEM_DUST = "Corrupted dust"
ITEM_PESTLE = "Pestle and mortar"
ITEM_SCEPTRE = "Corrupted sceptre"
ITEM_TELEPORT = "Corrupted teleport crystal"
ITEM_AXE = "Corrupted axe"
ITEM_PICKAXE = "Corrupted pickaxe"
ITEM_HARPOON = "Corrupted harpoon"

EGNIOL_DOSES = ("Egniol potion (1)", "Egniol potion (2)", "Egniol potion (3)", "Egniol potion (4)")

TIER_NAMES = {1: "basic", 2: "attuned", 3: "perfected"}
ARMOUR_KINDS = ("helm", "body", "legs")


def armour_name(kind, tier):
    """Return the display name for armour of the given kind and tier."""
    return f"Corrupted {kind} ({TIER_NAMES[tier]})"


def weapon_name(kind, tier):
    """Return the display name for a weapon of the given kind and tier."""
    return f"Corrupted {kind} ({TIER_NAMES[tier]})"


# --- Singing Bowl (REC: group 270; children matched by TEXT, never index) ---
BOWL_GROUP = 270
BOWL_TEXT = {
    "helm": "Corrupted helm",
    "body": "Corrupted body",
    "legs": "Corrupted legs",
    "bow": "Corrupted bow",
    "staff": "Corrupted staff",
    "cfish": "Corrupted paddlefish",
    "vial": "Vial",
    "teleport": "Corrupted teleport crystal"
}

# --- Resource nodes (wiki yields; REC action names) keyed by the live node NAME ---
def _node(sub):
    """Find the live node name containing the given substring."""
    return next(n for n in RESOURCE_NODES.values() if sub in n)

RESOURCE_ACTION = {
    _node("Deposit"): "Mine",
    _node("Phren"): "Chop",
    _node("Linum"): "Pick",
    _node("Grym"): "Pick",
    _node("Fishing"): "Fish"
}
RESOURCE_ITEM = {
    _node("Deposit"): ITEM_ORE,
    _node("Phren"): ITEM_BARK,
    _node("Linum"): ITEM_LINUM,
    _node("Grym"): ITEM_GRYM,
    _node("Fishing"): ITEM_RAW_FISH
}
RESOURCE_YIELD = {
    _node("Deposit"): 3,
    _node("Phren"): 3,
    _node("Linum"): 3,
    _node("Grym"): 1,
    _node("Fishing"): 4
}
# Node ids 35998/35999 ("Node", the "Light" action): the recording's summary gives BOTH ids
# the identical name "Node" with no "(Depleted)" suffix -- it does not say which is the lit
# state and which is unlit. WHICH IS LIT: VERIFY LIVE.
NODE_IDS = {35998, 35999}

# --- Monsters: attack style -> the protection prayer to hold while it hits us ---
ATTACK_STYLE_BY_NPC_ID = {
    9040: "MELEE", 9041: "MELEE", 9042: "MELEE", 9043: "MELEE", 9044: "MELEE",
    9045: "MELEE", 9046: "MELEE", 9047: "MAGIC"
}  # dark beast: RANGED, id unknown -- add when seen
DEMI_BOSS_DROP = {9046: ITEM_SPIKE, 9047: ITEM_ORB}      # dark beast -> ITEM_BOWSTRING once its id is known
DEMI_BOSS_WANTED = {9047}                                # orb -> perfected staff; dark beast added with its id (bowstring -> perfected bow); bear's spike never wanted
PROTECT_FOR_STYLE = {
    "MELEE": "PROTECT_FROM_MELEE",
    "RANGED": "PROTECT_FROM_MISSILES",
    "MAGIC": "PROTECT_FROM_MAGIC"
}
