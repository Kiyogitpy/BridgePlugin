"""Gauntlet ids, each with its source. Pure data -- sub-project 2 (the Hunllef fight) and 3 (the
maze) import from here and never re-derive an id.

Sources:
  ZL  = ZombieLite, C:/Users/Kiyo/Documents/ZombieLite/src/main/java/com/salvation/data/
        (NpcId, AnimationId, ProjectileId, RegionId) and plugins/gauntlet/GauntletPlugin.java
        (hazard tiles, tornado lifespan, varbit 9177) and npcs/Hunllef.java (attack cadence).
  REC = recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.summary (object/npc ids seen).
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
