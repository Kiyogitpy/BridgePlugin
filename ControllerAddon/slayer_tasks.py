# slayer_tasks.py -- what the task loop does with a task: FIGHT it, SKIP it (30 points), or STOP.
# PURE. Added 2026-09-19 (slayer spec §6, §7.1, §7.3). Sub-project 2's slice of the catalog:
# dispositions and target names only. Sites, routes, gear and supplies per Kuradal task are
# sub-project 3; Aya's sites already exist in aya_tasks.py.
#
# The table below is the USER's, task by task (docs/slayer-sites/decisions.md). An UNKNOWN task
# name is STOP, never SKIP: a cancel costs 30 points, and a name we do not recognise (new
# content, a DB decode slip) must not be paid for on a guess.

from collections import namedtuple

import aya_tasks
import slayer_catalog

FIGHT, SKIP, STOP = "FIGHT", "SKIP", "STOP"

_KURADAL_FIGHT = (
    "aberrant spectres", "abyssal demons", "ankou", "basilisks", "black demons", "bloodveld",
    "cave horrors", "dagannoth", "dust devils", "elves", "fire giants", "gargoyles", "gryphons",
    "hellhounds", "kalphite", "kurask", "mutated zygomites", "nechryael", "suqahs", "trolls",
    "waterfiends", "wyrms",
)
_KURADAL_SKIP = (
    "boss", "dark beasts", "warped creatures", "vampyres", "araxytes", "drakes",
    "fossil island wyverns", "lizardmen", "red dragons", "aquanites", "aviansie", "black dragons",
    "blue dragons", "cave kraken", "frost dragons", "metal dragons", "skeletal wyverns",
    "smoke devils", "spiritual creatures", "tzhaar",
)
# Greater demons: the user does this task by hand on tormented demons, which count for it.
_KURADAL_STOP = ("greater demons",)

KURADAL = {**{t: FIGHT for t in _KURADAL_FIGHT}, **{t: SKIP for t in _KURADAL_SKIP},
           **{t: STOP for t in _KURADAL_STOP}}

# Task names that are NOT a substring of the monsters' names. combat_core matches each entry as
# a lowercased substring of the NPC name (and adds its own singular), so most tasks need nothing.
_TARGETS = {
    "birds": ["chicken", "rooster", "seagull", "duck", "penguin", "terrorbird"],
    # Aya's Zombies are killed at Alice's Farm, where they are UNDEAD COWS AND CHICKENS (ids 2992,
    # 2993 in the Turael Skipping data) -- and combat_core's singular of "zombies" is "zomby".
    "zombies": ["zombie", "undead cow", "undead chicken"],
    "dwarves": ["dwarf"],
    "wolves": ["wolf"],
    "elves": ["elf"],
    "aberrant spectres": ["aberrant spectre"],
    "mutated zygomites": ["zygomite"],
}


def _key(task_name) -> str:
    return (task_name or "").strip().lower()


def user_skips(tasks_cfg) -> set:
    """The panel's skip list. The bridge sends a LIST (ConfigExporter.parseList); a hand-written
    config may send the comma string. FINAL REVIEW, reproduced live: .split on the list killed the
    engine the moment the user typed one task -- hidden because an EMPTY list is falsy."""
    raw = (tasks_cfg or {}).get("skipTasks") or ""
    parts = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    return {str(part).strip().lower() for part in parts if str(part).strip()}


def disposition(task_name, tasks_cfg) -> str:
    key = _key(task_name)
    if not key:
        return STOP
    known = key in KURADAL or aya_tasks.get(key) is not None
    if known and key in user_skips(tasks_cfg):
        return SKIP
    if key in KURADAL:
        return KURADAL[key]
    aya = aya_tasks.get(key)
    if aya is not None:
        return FIGHT if aya.disposition == aya_tasks.FIGHT else STOP
    return STOP


def target_names(task_name) -> list:
    key = _key(task_name)
    if not key:
        return []
    return list(_TARGETS.get(key, [key]))


def target_ids(task_name) -> tuple:
    """The NPC ids this task's monsters really are, or () when we do not know.

    Aya's registry carries them (`npc_ids`, from the Turael Skipping plugin's own data) and
    Kuradal's catalog does not, so this restricts Aya's tasks and leaves Kuradal's exactly as
    they were. () means "no opinion" and must never be read as "match nothing" -- see
    combat_core.pick_target.
    """
    site = _site(task_name)
    return tuple(getattr(site, "npc_ids", ()) or ())


def _site(task_name):
    """The task's site record: Aya's registry first (unchanged), then Kuradal's catalog. Both
    carry target / areas / teleports / location, so every caller reads one shape."""
    key = _key(task_name)
    return aya_tasks.get(key) or slayer_catalog.get(key)


def at_site(task_name, player, margin: int = 0):
    """True/False for a task whose site we know (Aya's, today); None when we know no site.
    `margin` widens every kill rectangle by that many tiles: the plugin's rectangles are tight
    (cave slimes: 14x8 with the target 2 tiles from an edge) and a fight chases monsters out of
    them, which must not read as "no longer at the site" (phase-A review)."""
    aya = _site(task_name)
    if aya is None or not aya.areas:
        return None
    x, y = (player or {}).get("x"), (player or {}).get("y")
    if x is None or y is None:
        return False
    plane = player.get("plane", 0)
    return any(ax - margin <= x < ax + w + margin and ay - margin <= y < ay + h + margin and plane == p
               for ax, ay, w, h, p in aya.areas)


def where(task_name) -> str:
    aya = _site(task_name)
    if aya is None or not aya.location:
        return ""
    via = f" (via {aya.teleports[0]})" if aya.teleports else ""
    return f"{aya.location}{via}, around {aya.target}"


# ---------------------------------------------------------------- what a task needs carried
#
# From the Turael Skipping registry's own gear note per task (aya_tasks.AyaTask.info). A dark
# cave without a light is not a slow task, it is constant swarm damage -- so a missing item is a
# STOP that names it, checked BEFORE any teleport is spent (phase A); the bank phase (C) will
# fetch it instead once it exists.

Need = namedtuple("Need", ("label", "any_of", "quantity", "ids"), defaults=((),))

# A LIT and an UNLIT lantern share ONE NAME (phase-A review, checked in the wiki dump: "Bullseye
# lantern" is 4548 unlit and 4550 lit; "Mining helmet" is 5014 unlit and 5013 lit). A name can
# therefore never prove a light. So the check is by ITEM ID where the item has an unlit twin, and
# by name only for things that are always lit. `carried` may carry ids as "#<id>" keys.
LIT_IDS = (4550,            # Bullseye lantern (lit)
           5013,            # Mining helmet (lit)
           4539,            # Oil lantern (lit)
           4531, 4534,      # Candle lantern (lit, white / black candle)
           4702, 9065,      # Sapphire / Emerald lantern (lit)
           33, 32)          # Lit candle / Lit black candle        (all VERIFIED in the wiki dump)
ALWAYS_LIT = ("kandarin headgear", "firemaking cape", "max cape", "bruma torch")
LIGHT_SOURCES = ("bullseye lantern",) + ALWAYS_LIT      # display order for the stop message
_NOT_LIT = ("unlit", "empty", "(u)")


def required_items(task_name, count) -> list:
    """What must be carried OR worn before a teleport is spent. Aya's registry supplies a gear
    note; Kuradal's catalog supplies `carry` and `worn`. A task both masters assign (kalphite) is
    held to BOTH lists -- the stricter reading, whoever assigned it.

    SELF-REVIEW 2026-09-20: until then only Aya's note was read, so every Kuradal task reported
    nothing missing and the routine would have teleported to basilisks with no mirror shield."""
    needs = []
    aya = aya_tasks.get(_key(task_name))
    note = ((aya.info if aya else None) or "").lower()
    if "light source" in note:
        needs.append(Need("a light source", ALWAYS_LIT, 1, LIT_IDS))
    if "ice cooler" in note:
        needs.append(Need("ice coolers", ("ice cooler",), int(count or 0) + 5))
    if "rope" in note:
        needs.append(Need("a rope", ("rope",), 1))
    spec = slayer_catalog.get(_key(task_name))
    for label in (tuple(spec.carry) + tuple(spec.worn)) if spec else ():
        if label == slayer_catalog.LIGHT_LABEL:
            if not any(n.ids for n in needs):
                needs.append(Need("a light source", ALWAYS_LIT, 1, LIT_IDS))
            continue
        options = slayer_catalog.REQUIREMENT_ITEMS.get(label, ())
        if options:                                  # () = the user's open decision: never enforced
            needs.append(Need(label, options, 1))
    return needs


def _have(need, carried) -> int:
    """`carried` maps a lowercased item NAME to a quantity, and "#<item id>" to a quantity."""
    total = 0
    for name, quantity in (carried or {}).items():
        lowered = (name or "").lower()
        if lowered.startswith("#"):
            if lowered[1:].isdigit() and int(lowered[1:]) in need.ids:
                total += int(quantity or 0)
            continue
        if any(bad in lowered for bad in _NOT_LIT):
            continue
        if any(option in lowered for option in need.any_of):
            total += int(quantity or 0)
    return total


def missing_items(task_name, count, carried) -> list:
    out = []
    for need in required_items(task_name, count):
        have = _have(need, carried)
        if have >= need.quantity:
            continue
        out.append(f"{need.label} (e.g. {LIGHT_SOURCES[0] if need.ids else need.any_of[0]}, LIT)" if need.ids
                   else f"{need.label} (e.g. {need.any_of[0]})" if need.quantity == 1
                   else f"{need.label} x{need.quantity} (have {have})")
    return out
