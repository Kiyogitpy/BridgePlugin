# slayer_catalog.py -- Kuradal's 22 FIGHT tasks as DATA: where, how to get there, what to bring.
# PURE: no HTTP, no client state. Added 2026-09-20 (slayer sub-project 3, task E1; spec §6, §6.1),
# written AHEAD of the phase C/D live runs at the user's say-so because it is data over code that
# is already tested -- nothing here clicks anything.
#
# A TaskSpec deliberately carries the SAME fields the routine already reads off an Aya task
# (aya_tasks.AyaTask: target, areas, teleports, location) in the SAME teleport grammar
# (slayer_routes.parse), so slayer_tasks.at_site / where and slayer_routes.plan simply fall
# through to it for a task Aya does not know. There is no second code path to keep in step.
#
# WHERE EVERY NUMBER CAME FROM, so none of it is re-derived or trusted past its source:
# - `target` and the site choice: docs/slayer-sites/decisions.md (the USER's, task by task) and
#   the kuradal-1a/1b/2a/2b.md research files.
# - `areas`: MEASURED 2026-09-20 from the osrs-wiki spawn table -- the bounding box of the
#   monster's own spawns within 40 tiles of the target on its plane, padded 4 tiles a side
#   (a fight chases monsters out of a tight rectangle; the phase-A review paid for that).
#   tools/slayer_site_audit.py re-checks every target against that table.
# - `landing`: the tile the FIRST teleport lands on (vendored Shortest Path tables; the slayer
#   ring and box tiles are the ones docs/slayer-sites/routes.md was generated from).
# - `routable`: tests/test_slayer_catalog.py asserts a real crossfloor route from `landing` to
#   inside `areas` for every entry marked True, with what the user's account proves today.
# - `profile`: decisions.md's supply table. ONLY nechryael (15 food) and the 5-6 full prayer
#   potions are the user's own figures; the rest are PROPOSED there and are theirs to correct.
#
# WHAT IS NOT HERE YET, on purpose:
# - The DESERT AMULET's Kalphite cave teleport has no measured mechanism anywhere in the repo;
#   kalphite falls through to fairy ring BIQ, and with neither usable slayer_routes.plan answers
#   None naming them and the routine stops. Everything else has a leg: the SLAYER RING (12 of the
#   22) through legs.dialogue_item (golem_crafting proved its paged menu live), XERIC'S TALISMAN
#   through the plain item leg (mahogany_homes takes its destinations off the right-click menu,
#   from a recording). UNVERIFIED for these destinations; each hub is proven by one live task.
# - Gear the research left open (plan E0): a one-handed weapon beside the mirror shield, a ranged
#   kit for waterfiends, heat boots for wyrms. `worn` names the REQUIREMENT, not the user's item.

from collections import namedtuple

LOOT_RESERVE_SLOTS = 4        # what the loot hook wants free (plan phase C); see slots_used
STAPLE_SLOTS = 2              # a house tablet and an escape ring of dueling, every trip

TaskSpec = namedtuple("TaskSpec", (
    "key", "name", "location", "target", "areas", "teleports", "landing", "profile",
    "carry", "worn", "routable", "wilderness", "task_only", "note", "protect"), defaults=(None,))

# `protect`: the PROTECTION PRAYER this task is fought under, as combat_core.PRAYER_LEVELS names
# it, or None to leave the panel's own Defensive Prayer alone.
#
# USER 2026-09-21, watching the first gargoyle task fight with the panel's prayer set to NONE:
# "this is a dangerous task, i cant configure this for every task so it needs to be decided for
# this task specifically we need pray for melee." The panel has ONE defensive prayer for every
# task, and which prayer is right is a property of the MONSTER -- so it belongs here, beside the
# monster's site and its supply profile, not in a setting the user would have to change by hand
# between tasks and would get wrong the moment the routine moved on unattended.
#
# Filled only where a source states it: four of these were already written in this catalog's own
# notes, and gargoyles is the user's live call. Everything else stays None and behaves exactly as
# before -- an invented prayer is worse than the panel's.

# Teleports that are NOT an inventory slot: a fairy ring and the house box are places, the
# Moonclan teleport is a Lunar spell the account already has.
_NOT_AN_ITEM = ("fairy ring", "ornate jewellery box", "moonclan teleport")

# A `carry` / `worn` LABEL -> the item-name fragments that satisfy it, best first. A label that
# maps to () is the user's OPEN decision (plan E0: which ranged kit) and is shown as a note, never
# enforced as an item name we made up. "lit light source" is special: a lit and an unlit lantern
# share one NAME, so slayer_tasks checks it by item id (its own LIT_IDS rule).
REQUIREMENT_ITEMS = {
    "rock hammer": ("rock hammer", "granite hammer", "rock thrownhammer"),
    "antipoison": ("antipoison", "superantipoison", "antidote", "anti-venom"),
    "fungicide spray": ("fungicide spray",),
    "nose peg or slayer helmet": ("nose peg", "slayer helmet"),
    "facemask or slayer helmet": ("facemask", "slayer helmet"),
    "mirror shield": ("mirror shield", "v's shield"),
    "witchwood icon": ("witchwood icon",),
    "leaf-bladed weapon or broad ammo": ("leaf-bladed", "broad arrow", "broad bolt", "amethyst broad"),
    "boots of stone, brimstone or granite": ("boots of stone", "boots of brimstone", "granite boots"),
    # Finishing-blow items (slayer_finishers.py, plan E0). A granite hammer EQUIPPED smashes
    # gargoyles by itself, which is why it stays first in the "rock hammer" row above.
    "ice cooler": ("ice cooler",),
    "bag of salt": ("bag of salt",),
    "ranged weapon and ammo": (),
}
LIGHT_LABEL = "lit light source"

RING_STRONGHOLD = (2431, 3422, 0)
RING_TOWER = (3421, 3537, 0)
RING_FREMENNIK = (2802, 9999, 0)
XERICS_HEART = (1644, 3673, 0)


def _spec(key, location, target, area, teleports, landing, profile, carry=(), worn=(),
          routable=True, task_only=False, note="", protect=None):
    return TaskSpec(key=key, name=key.capitalize(), location=location, target=target,
                    areas=(area,), teleports=tuple(teleports), landing=landing, profile=profile,
                    carry=tuple(carry), worn=tuple(worn), routable=routable, wilderness=False,
                    task_only=task_only, note=note, protect=protect)


_STRONGHOLD = ("Slayer ring: Stronghold Slayer Cave",)
_TOWER = ("Slayer ring: Slayer Tower",)
_FREMENNIK = ("Slayer ring: Fremennik Slayer Dungeon",)
_CATACOMBS = ("Xeric's talisman: Xeric's Heart",)

CATALOG = {spec.key: spec for spec in (
    # --- Stronghold Slayer Cave (5) ------------------------------------------------------------
    _spec("aberrant spectres", "Stronghold Slayer Cave", (2440, 9790, 0), (2437, 9770, 39, 29, 0),
          _STRONGHOLD, RING_STRONGHOLD, "PRAYER", worn=("nose peg or slayer helmet",),
          protect="PROTECT_FROM_MAGIC", note="Protect from Magic; safespots."),
    _spec("ankou", "Stronghold Slayer Cave", (2470, 9805, 0), (2464, 9793, 24, 20, 0),
          _STRONGHOLD, RING_STRONGHOLD, "PRAYER"),
    _spec("bloodveld", "Stronghold Slayer Cave", (2450, 9820, 0), (2430, 9813, 64, 26, 0),
          _STRONGHOLD, RING_STRONGHOLD, "PRAYER", protect="PROTECT_FROM_MELEE",
          note="Protect from Melee negates them."),
    _spec("fire giants", "Stronghold Slayer Cave", (2400, 9780, 0), (2387, 9765, 33, 25, 0),
          _STRONGHOLD, RING_STRONGHOLD, "PRAYER", task_only=True),
    _spec("hellhounds", "Stronghold Slayer Cave", (2420, 9785, 0), (2405, 9766, 33, 40, 0),
          _STRONGHOLD, RING_STRONGHOLD, "PRAYER"),
    # --- Slayer Tower basement (3) -- the ladder lands on PLANE 3, not 0 -------------------------
    _spec("abyssal demons", "Slayer Tower basement", (3438, 9965, 3), (3423, 9950, 27, 29, 3),
          _TOWER, RING_TOWER, "PRAYER", task_only=True,
          note="Slayer 85. Their teleport can move the player a few tiles; at_site's margin covers "
               "it. Target is the MEDIAN SPAWN: the research tile (3420,9965) sat 3 tiles west of "
               "the whole cluster, caught by the target-in-its-own-area test."),
    _spec("gargoyles", "Slayer Tower basement", (3434, 9938, 3), (3423, 9924, 26, 29, 3),
          _TOWER, RING_TOWER, "PRAYER", carry=("rock hammer",), protect="PROTECT_FROM_MELEE",
          note="Rock hammer finish, or a granite hammer as the weapon, or the auto-smash unlock."),
    _spec("nechryael", "Slayer Tower basement", (3412, 9962, 3), (3399, 9950, 30, 26, 3),
          _TOWER, RING_TOWER, "PRAYER_PLUS_DAMAGE", task_only=True,
          protect="PROTECT_FROM_MELEE",
          note="USER: at least 15 food -- the death spawns hit through Protect from Melee."),
    # --- Fremennik Slayer Dungeon (2) -----------------------------------------------------------
    _spec("basilisks", "Fremennik Slayer Dungeon", (2738, 10008, 0), (2734, 9998, 17, 25, 0),
          _FREMENNIK, RING_FREMENNIK, "PRAYER", worn=("mirror shield",),
          note="80-point unlock. OPEN (E0): a one-handed weapon beside the mirror shield."),
    _spec("kurask", "Fremennik Slayer Dungeon", (2705, 9980, 0), (2690, 9955, 31, 51, 0),
          _FREMENNIK, RING_FREMENNIK, "PRAYER", worn=("leaf-bladed weapon or broad ammo",),
          note="Magic Dart is standard spellbook and the account is on Lunars. 355 tiles without "
               "the 43/61 Agility shortcuts, 140 with them."),
    # --- Catacombs of Kourend (2) ---------------------------------------------------------------
    _spec("dagannoth", "Catacombs of Kourend", (1665, 9995, 0), (1652, 9990, 27, 17, 0),
          _CATACOMBS, XERICS_HEART, "PRAYER", note="Horror from the Deep. Melee-only variant here."),
    _spec("dust devils", "Catacombs of Kourend", (1710, 10020, 0), (1704, 10007, 19, 33, 0),
          _CATACOMBS, XERICS_HEART, "PRAYER", worn=("facemask or slayer helmet",),
          note="User chose the Catacombs over the Smoke Dungeon."),
    # --- the singles ----------------------------------------------------------------------------
    _spec("black demons", "Taverley Dungeon", (2852, 9784, 0), (2822, 9759, 58, 47, 0),
          ("Taverley teleport: Taverley", "Ornate Jewellery Box: Falador Park"), (2893, 3465, 0),
          "PRAYER",
          note="USER 2026-09-20: the obstacle pipe (70 Agility; the account has 77) to the blue "
               "dragons, then run. crossfloor routes it: ladder 16680, pipe 16509. Under 70 Agility "
               "the only way is the dusty-key gate (item 1590)."),
    _spec("cave horrors", "Mos Le'Harmless Cave", (3777, 9449, 0), (3733, 9408, 86, 63, 0),
          ("Mos le'harmless teleport: Mos Le'Harmless",), (3701, 2996, 0), "PRAYER",
          carry=("lit light source",), worn=("witchwood icon",),
          note="Cabin Fever. No close item teleport was found in research; the scroll lands at the "
               "north gate, then the cave entrance 3650 at (3749,2973)."),
    _spec("elves", "Lletya", (2333, 3172, 0), (2325, 3166, 25, 14, 0),
          ("Teleport crystal: Lletya",), (2330, 3171, 0), "PRAYER_PLUS_DAMAGE",
          note="USER chose Lletya although it has only 3 elves (1 archer, 2 warriors) on the ground "
               "floor: expect a slow task. Archers and warriors use different styles."),
    _spec("gryphons", "Great Conch, south-eastern task-only cave", (3225, 8860, 0),
          (3206, 8837, 44, 45, 0), ("Fairy ring: CJQ",), (3178, 2447, 0), "PRAYER_PLUS_DAMAGE",
          routable=False, task_only=True,
          note="NOT ROUTABLE until one recording: the cave entrance is in NO table -- not the jar's, "
               "not Microbot's fork, not upstream master (checked 2026-09-20) -- and the wiki gives "
               "no tile. Slayer 51, Sailing 45, Troubled Tortugans, 80-point unlock. Keep equipped "
               "weight >= ~30 kg against the knockback; no documented safespot."),
    _spec("kalphite", "Kalphite Cave", (3309, 9526, 0), (3268, 9482, 85, 60, 0),
          ("Desert amulet 4: Kalphite cave", "Fairy ring: BIQ"), (3322, 3122, 0),
          "PRAYER_PLUS_DAMAGE", carry=("antipoison",), task_only=True,
          note="Soldier/guardian poison ignores prayer. Same site as Aya's kalphite task."),
    _spec("mutated zygomites", "Zanaris, near the Cosmic Altar", (2416, 4373, 0),
          (2408, 4366, 18, 16, 0), ("Fairy ring: Zanaris",), (2412, 4434, 0), "SAFESPOT",
          carry=("fungicide spray",),
          note="Lost City. Pillar safespot. 152 tiles without the 66 Agility jutting wall, 98 with."),
    _spec("suqahs", "Lunar Isle, south-west group", (2085, 3868, 0), (2060, 3852, 65, 39, 0),
          ("Moonclan teleport: Moonclan",), (2113, 3915, 0), "PRAYER",
          note="A Lunar SPELL the account already has, not an item. Melee only, single combat."),
    _spec("trolls", "Death Plateau", (2864, 3590, 0), (2848, 3580, 33, 20, 0),
          ("Ornate Jewellery Box: Burthorpe", "Games necklace: Burthorpe"), (2899, 3553, 0),
          "PRAYER",
          note="UNVERIFIED ROUTE: 185 tiles climbing from the west and north -- may be the "
               "quest-gated back way. The user is asked to look at it (docs/slayer-handoff.md)."),
    _spec("waterfiends", "Ancient Cavern", (1750, 5350, 0), (1734, 5336, 38, 32, 0),
          ("Ornate Jewellery Box: Barbarian Outpost", "Games necklace: Barbarian Outpost"),
          (2520, 3571, 0), "SAFESPOT", worn=("ranged weapon and ammo",),
          note="The whirlpool needs varbit 3759 > 1 (the account reads 3). OPEN (E0): the ranged "
               "kit for the across-the-river safespot."),
    _spec("wyrms", "Karuulm Slayer Dungeon, task-only south batch", (1265, 10170, 0),
          (1248, 10143, 37, 59, 0), ("Rada's blessing: Mount Karuulm", "Fairy ring: CIR"),
          (1311, 3806, 0), "PRAYER", worn=("boots of stone, brimstone or granite",), task_only=True,
          note="The elevator and rock climbs come from Microbot's transports table. OPEN (E0): heat "
               "boots unless the elite Kourend diary is done. Protect from Magic at range.",
          protect="PROTECT_FROM_MAGIC"),
)}


def get(task_name):
    """Case-insensitive, with the same singular/plural slip aya_tasks.get forgives."""
    key = (task_name or "").strip().lower()
    for candidate in (key, key + "s", key[:-1] if key.endswith("s") else key):
        if candidate in CATALOG:
            return CATALOG[candidate]
    return None


def contains(spec, x, y, plane) -> bool:
    return any(ax <= x < ax + w and ay <= y < ay + h and plane == p for ax, ay, w, h, p in spec.areas)


def first_teleport_is_an_item(spec) -> bool:
    head = spec.teleports[0].partition(":")[0].strip().lower()
    return head not in _NOT_AN_ITEM


def slots_used(spec) -> int:
    """Inventory slots one trip needs: the profile's food and FULL prayer potions, what the task
    needs carried, the ONE teleport item actually used (alternatives stay in the bank), and the
    staples. Worn requirements cost nothing here; displaced base gear is phase C's arithmetic."""
    import supplies                     # local: keeps this module importable on its own
    profile = supplies.PROFILES[spec.profile]
    return (profile.food + profile.prayer_potions + len(spec.carry)
            + (1 if first_teleport_is_an_item(spec) else 0) + STAPLE_SLOTS)
