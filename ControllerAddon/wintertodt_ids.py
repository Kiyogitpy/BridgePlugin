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
