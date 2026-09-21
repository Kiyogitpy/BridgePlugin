# slayer_routes.py -- a slayer task becomes an ordered list of legs.py legs. PURE: it reads the
# vendored data and the offline collision map, never the bridge; the caller hands in the
# player's tile and what is carried. Added 2026-09-19 (spec §6, §7.6), PHASE A of
# docs/superpowers/plans/2026-09-19-slayer-travel-bank-loadout.md.
#
# Phase A plans from WHERE THE PLAYER STANDS: Aya's teleports are tried in the Turael Skipping
# plugin's own order, and one is usable when it is a fairy ring code and an OVERWORLD ring is a
# walk away, or an item we carry and know how to use. The house (tablet -> POH ring / jewellery
# box) is phase B and Kuradal's sites are phase E; until then plan() answers None plus a reason
# naming every teleport it could not use, which the routine prints in its safety stop.
#
# FAIRY_EXPECT is the substring travel.hop_fairy_ring must find on the Confirm button before it
# teleports. Only AJQ is MEASURED ("Cave south of Dorgesh-Kaan", travel.py). The rest are the
# travel log's place names and are safe to get wrong: a mismatch is a safety stop BEFORE the
# hop, never a trip to the wrong continent.
#
# 2026-09-21: WALKING THE WHOLE WAY is a candidate too, not just a teleport's onward walk. The
# caveat commit 713ce7a recorded rather than hid: standing ON the Varrock sewer manhole with the
# rats task, plan() answered "no route" and the pack set off to a bank for a Varrock tablet, one
# transport from done. plan() had no "walk and take a transport from here" branch at all -- only a
# task's registered teleports and near_site's transport-free flood. walk_route() is that branch;
# WALK_MAX_TILES and the cost comparison below decide when it wins.
#
# PHASE B (2026-09-20): the house (tablet -> pool -> ornate box / house fairy ring) joins the
# teleport order as a fallback behind an overworld ring, never ahead of one -- see plan()'s own
# comment. `has_tablet` gates every house route UNLESS the player is already standing inside the
# instance (`player["inInstance"]`), in which case poh.enter's own no-op-when-already-inside
# makes a tablet moot -- and, per the phase B brief, `nearest_ring` must not even be CALLED in
# that case: an in-house player's template x/y are meaningless distances (see poh.py's own
# instX/instY note), so consulting it there would compare real tiles against instance geometry.

import runes
import re

import aya_tasks
import slayer_catalog
import crossfloor
import legs
import poh
import transports
import worldmap

RING_WALK_MAX = 150          # tiles, straight-line pre-filter before the real walk is costed
NEAR_SITE_TILES = 64         # within this WALK of the site, never plan a teleport -- just walk back

# --- walking the whole way ----------------------------------------------------------------------
#
# The furthest we will WALK to a site. USER's choice 2026-09-21: about 400 tiles, roughly two
# minutes of running. It caps BOTH jobs the walk candidate has -- the last resort when no
# registered teleport is usable, and the comparison below -- because a walk longer than this is
# not worth taking whatever the alternative is, and it runs inside ONE blocking tick, where no
# break and no before_break can fire. MEASURED from the Varrock sewer manhole: rats 16 tiles,
# goblins 384, wolves 976.
WALK_MAX_TILES = 400

# A teleport is not free -- the click, the dialogue and the ~4-tick animation cost real time -- so
# a route's cost is the walking it was measured at plus TELEPORT_COST_TILES a hop. Both numbers are
# teleports.py's, which has made the same judgement for bank returns since 2026-09-07 and calls
# them first-draft guesses; they want a live session here too.
#
# THE BIAS IS THE OTHER WAY ROUND FROM teleports.py, deliberately. There walking is the default and
# a teleport must beat it, because the teleport spends a consumable and the walk is the proven
# path. Here it is reversed: every teleport route has been built and reviewed for this account,
# while a long cross-country walk is the thing nothing has run yet -- so the WALK must beat the
# teleport by MIN_SAVING_TILES before it is taken. That is not hair-splitting. MEASURED 2026-09-21:
# read the other way, icefiends-from-Edgeville came down to ONE tile (159 walking against 68 plus
# two hops), and no map or vendored table should be trusted to a margin that thin. This way round
# the same decision has 81 tiles of slack, and the motivating case still walks (16 against 96).
TELEPORT_COST_TILES = 25
MIN_SAVING_TILES = 40

#: Legs that cost a teleport's worth of WAITING -- what a route is charged for. A different
#: question from TELEPORT_LEG_KINDS below, which asks what a route SPENDS and so must be packed
#: for: the house pool is neither (free, and nothing to fetch), while breaking a house tablet is
#: both.
HOP_KINDS = ("item", "spell", "dialogue_item", "glider", "poh_mounted", "fairy", "poh_ring",
             "poh_box", "poh_enter")

#: Leg kinds that SPEND a teleport item, spell runes or a charge -- the ones slayer_loadout._teleport
#: exists to pack for. A walk or a transport (a ladder, a manhole) needs nothing carried, and the
#: house hub's own legs are paid for by the house tablets the pack always keeps.
TELEPORT_LEG_KINDS = ("item", "spell", "dialogue_item", "glider", "poh_mounted")

FAIRY_EXPECT = {
    "AJQ": "Dorgesh",        # MEASURED: "Cave south of Dorgesh-Kaan"
    "AJR": "Rellekka",       # Slayer cave south-east of Rellekka        (unverified)
    "BLR": "Legends",        # Legends' Guild                            (unverified)
    "DLQ": "Nardah",         # Kharidian Desert, north of Nardah         (unverified)
    "BIQ": "Kalphite",       # Near the Kalphite Hive                    (unverified)
    "CKS": "Canifis",        # Morytania: Canifis                        (unverified)
    # 2026-09-20, from the wiki's per-code Location text -- a usable source because AJQ's
    # ("Cave south of Dorgesh-Kaan") is exactly what was MEASURED on the Confirm button.
    "CJQ": "Conch",          # The Great Conch (gryphons)                (unverified)
    "CIR": "Karuulm",        # at the base of Mount Karuulm (wyrms)      (unverified)
}

# Carried items phase A knows how to use: registry text prefix -> (item name, menu option, landing).
#: Teleports whose landing an ACHIEVEMENT DIARY reward can move. MEASURED LIVE 2026-09-21 on this
#: account: the Varrock tablet broke and landed at the Grand Exchange, not Varrock square, so the
#: leg judged a working teleport a failure -- retrying and burning another tablet each time.
#: A redirect is a per-account setting, so BOTH landings are accepted rather than one chosen.
LANDING_REDIRECTS = {
    "varrock teleport": ((3164, 3487, 0),),
}


ITEMS = {
    # MEASURED LIVE 2026-09-21, the first real travel leg: the action is "Teleport". It said
    # "Empty" and the run stopped with the whole menu printed -- ['Cancel', 'Examine', 'Drop',
    # 'Use', 'Teleport'] -- so "Empty" had been a guess all along, under a comment calling this
    # the PROVEN row the others were modelled on. It had never been run.
    "ectophial": ("ectophial", "Teleport", (3660, 3522, 0)),
    # Added 2026-09-20 with the Kuradal catalog: inventory items whose teleport is a DIRECT menu
    # option. The Ectophial above was described here as proving the mechanism; it did not, so
    # every option below is still a GUESS. Options and landings are UNVERIFIED LIVE
    # (landings from the vendored teleportation_items.tsv); a wrong option is a withheld left-click,
    # then a menu miss, then a clean stop -- never a wrong teleport.
    "taverley teleport": ("taverley teleport", "Break", (2893, 3465, 0)),
    "teleport crystal": ("teleport crystal", "Lletya", (2330, 3171, 0)),
    "rada's blessing": ("rada's blessing", "Mount Karuulm", (1311, 3806, 0)),
    "mos le'harmless teleport": ("mos le'harmless teleport", "Teleport", (3701, 2996, 0)),
    # MEASURED, not guessed: routines/mahogany_homes.py takes "Xeric's Glade" straight off the
    # talisman's right-click menu (from a recording: "Xeric's Glade -> widget 149.0 [Xeric's
    # talisman]"; live for hours). "Xeric's Heart" is the sibling option. The left-click default
    # is NOT a destination, so the hover gate withholds it and the menu entry is taken.
    "xeric's talisman": ("xeric's talisman", "Xeric's Heart", (1644, 3673, 0)),
}
# An ITEMS row is ONE destination of an item that may have several. A registry line naming a
# different one (a talisman to Xeric's Glade) must not be sent to this row's landing.
_ITEM_DESTINATION = {"xeric's talisman": "xeric's heart"}

# Items whose destination is chosen in a CHAT MENU (legs.dialogue_item). registry text head ->
# {destination as the registries spell it, lowercased: (right-click sub-option label, dialogue
# needle, landing)}. Labels are cache obj 11866's own sub-options; landings are the vendored
# teleportation_items.tsv rows. Added 2026-09-20: the ring's paged menu was already proven live in
# golem_crafting, so 12 of Kuradal's 22 tasks no longer wait on a recording.
DIALOGUE_ITEMS = {
    "slayer ring": {
        "stronghold slayer cave": ("Stronghold", "stronghold", (2431, 3422, 0)),
        "slayer tower": ("Slayer Tower", "slayer tower", (3421, 3537, 0)),
        "fremennik slayer dungeon": ("Fremennik Dungeon", "fremennik", (2802, 9999, 0)),
    },
}

# Teleport SPELLS (legs.spell): registry text head -> (spell name, (group, child), landing). The
# widget id is cache interface 218 `tele_moonclan`; the same read gives npc_contact as 218:113,
# the constant already measured live, which is what makes the mapping trustworthy. Needs 1 law,
# 2 astral, 2 earth runes -- NOT checked here: a rune pouch is invisible to `carried`, and a cast
# without runes simply never lands, which fails the leg.
SPELLS = {
    "moonclan teleport": ("Moonclan Teleport", (218, 116), (2113, 3915, 0)),
}
# Runes per cast, from the cache (obj 9111: lawrune 1, astralrune 2, earthrune 2). `carried` is
# expected to include a CARRIED rune pouch's runes (routines/slayer._carried). A worn elemental
# staff stands in for its rune, the rule npc_contact already applies to air.
SPELL_RUNES = {
    "moonclan teleport": {"law rune": 1, "astral rune": 2, "earth rune": 2},
}
RUNE_STAVES = {
    "earth rune": ("staff of earth", "earth battlestaff", "mystic earth staff", "mud battlestaff",
                   "mystic mud staff", "dust battlestaff", "mystic dust staff", "lava battlestaff",
                   "mystic lava staff"),
}


def missing_runes(spell_key, carried) -> list:
    """['2 astral rune (have 1)', ...] for one cast of `spell_key`, or []."""
    held = {(n or "").lower(): int(q or 0) for n, q in (carried or {}).items()}
    out = []
    for rune, per_cast in SPELL_RUNES.get(spell_key, {}).items():
        if any(staff in name for name in held for staff in RUNE_STAVES.get(rune, ())):
            continue
        # Combination runes pay for their elements -- a dust rune IS an air rune. See runes.py for
        # the live case that found this: 11,477 dust and zero plain air read as "cannot cast".
        have = runes.available(held, rune)
        if have < per_cast:
            out.append(f"{per_cast} {rune} (have {have})")
    return out

# Items MOUNTED in the house (legs.house_mounted): registry head -> (object name fragment, menu
# action, landing). USER 2026-09-20: "There is a digsite pendant mounted in poh". The row is the
# vendored teleportation_boxes.tsv's ("Digsite Digsite Pendant 33417" -> 3341 3445 0); the object
# is found by NAME + ACTION, not by that id. Like the box and the house ring it needs a tablet
# or being in the house already. UNVERIFIED LIVE.
MOUNTED = {
    "digsite pendant": ("digsite pendant", "Digsite", (3341, 3445, 0)),
    # "Fairy ring: Zanaris" is not a code, it is the ring's own ZANARIS ACTION -- MEASURED LIVE
    # 2026-09-20 on the house ring (29228): "Zanaris / Configure / Last-destination / Favourites /
    # Remove". A named house object plus a menu action, exactly like the pendant. A three-letter
    # code is caught earlier in parse() and never reaches this row.
    "fairy ring": ("fairy ring", "Zanaris", (2412, 4434, 0)),
}

# Gnome gliders (legs.glider): destination -> (map widget, landing). Widgets are Microbot's packed
# ids decoded as (138 << 16) | child; landings are the vendored gnome_gliders.tsv. Every flight
# here starts from Ta Quir Priw: the pilot stands on TOP of the Grand Tree, a walk and three
# ladders from the Stronghold slayer ring's landing -- enterable only since
# crossfloor.pass_through_doors (the Tree Door). Needs The Grand Tree. UNVERIFIED LIVE.
GLIDER_PILOT = ("Captain Errdo", (2465, 3501, 3))
GLIDER_APPROACH = ("slayer ring", "stronghold slayer cave")        # a DIALOGUE_ITEMS entry
GLIDERS = {
    "sindarpos": ("Sindarpos", (138, 7), (2850, 3498, 0)),
    "gandius": ("Gandius", (138, 16), (2971, 2968, 0)),
    "kar-hewo": ("Kar-Hewo", (138, 13), (3284, 3210, 0)),
    "lemanto andra": ("Lemanto Andra", (138, 10), (3321, 3429, 0)),
}

# Ornate jewellery box destination -> landing tile. UNVERIFIED coordinates -- the 12-tile
# await_arrival radius (legs.ARRIVAL_TILES) absorbs small errors. The box serves every teleport
# item that also names one of these destinations (a glory amulet, a skills necklace, ...), not
# just the box itself -- see parse()'s own stripping of the item name before the colon.
BOX_LANDINGS = {
    "Cooking Guild": (3143, 3442, 0),
    "Draynor Village": (3105, 3251, 0),
    "Karamja": (2918, 3176, 0),
    "Burthorpe": (2899, 3553, 0),
    "Barbarian Outpost": (2520, 3571, 0),
    "Ferox Enclave": (3151, 3635, 0),
    "Castle Wars": (2441, 3090, 0),
    "Edgeville": (3087, 3496, 0),
    "Al Kharid": (3293, 3163, 0),
    "Mining Guild": (3046, 9735, 0),      # teleportation_boxes.tsv; the ITEMS table says (3049,9763)
    "Fishing Guild": (2611, 3390, 0),
    "Crafting Guild": (2933, 3295, 0),
    "Grand Exchange": (3164, 3478, 0),
    "Falador Park": (2996, 3375, 0),
    "Warriors' Guild": (2882, 3548, 0),
    "Champions' Guild": (3191, 3368, 0),
    # The combat bracelet's third entry, and ALREADY HERE -- the gap was never this table. USER's
    # screenshot of the box 2026-09-21 confirms the label: "C: Monastery", where C is the KEYBIND.
    # (The vendored teleportation_boxes.tsv calls the row "C: Edgeville Monastery", which is
    # Shortest Path's naming and not what the interface says.) MEASURED 71 tiles from here to the
    # icefiends, against 160 from Edgeville -- see aya_tasks._EXTRA_TELEPORTS["icefiends"].
    "Monastery": (3052, 3488, 0),
    "Ranging Guild": (2655, 3441, 0),
    "Woodcutting Guild": (1662, 3505, 0),
    "Farming Guild": (1249, 3718, 0),
}
def consumable(key):
    """A plain teleport TABLET or SCROLL named by a registry line with no colon ("Varrock
    teleport", "Lassar teleport", "Digsite teleport") -> (item name, option, landing), or None.

    The vendored teleportation_items.tsv calls a tablet "<X> tablet" while the in-game item is
    "<X> teleport", whose default action is Break (MEASURED in routines/mahogany_homes.py); a
    scroll keeps its own name and its action is Teleport. Only what that table knows is accepted,
    and its landing is the table's. A tablet redirected elsewhere (Varrock -> GE) lands outside
    the arrival radius and fails the leg cleanly."""
    key = (key or "").strip().lower()
    if not key.endswith(" teleport"):
        return None
    import teleports                       # local: the table is only needed for these few lines
    tablet = key[:-len(" teleport")] + " tablet"
    for row in teleports.load():
        display = (row.display or "").strip().lower()
        if display == tablet:
            return key, "Break", tuple(row.dest)
        if display == key:
            return key, "Teleport", tuple(row.dest)
    return None


def item_row(key):
    """ITEMS first (hand-written, measured options), then a tablet/scroll the table knows."""
    return ITEMS.get(key) or consumable(key)


_BOX_LANDINGS_BY_LOWER = {name.lower(): name for name in BOX_LANDINGS}
_TRAILING_OPTION = re.compile(r"\s*\(option\s+\w+\)\s*$", re.IGNORECASE)   # "(Option 3)" AND "(Option Q)"


def parse(teleport_text):
    """("fairy", CODE) | ("item", key) | ("box", destination) | ("unsupported", text).

    fairy/item are checked FIRST and are unchanged from phase A. "box" is new: whatever the item
    NAME before the colon is (the box itself, a glory amulet, a skills necklace, ...), the text
    AFTER the colon -- with any trailing " (Option N)" paging suffix stripped -- names a
    BOX_LANDINGS destination. The box serves all of those, so the item name is not checked."""
    text = (teleport_text or "").strip()
    head, _, tail = text.partition(":")
    key = head.strip().lower()
    if key == "fairy ring" and len(tail.strip()) == 3:
        return "fairy", tail.strip().upper()
    if key in ITEMS and _ITEM_DESTINATION.get(key, "") in tail.lower():
        return "item", key
    if key in SPELLS:
        return "spell", key
    if key == "gnome glider" and _TRAILING_OPTION.sub("", tail).strip().lower() in GLIDERS:
        return "glider", _TRAILING_OPTION.sub("", tail).strip().lower()
    # "Digsite pendant (Option 1)" has NO colon: the paging suffix sits on the head.
    bare = _TRAILING_OPTION.sub("", key).strip()
    if bare in MOUNTED and (not tail.strip() or MOUNTED[bare][1].lower() in tail.lower()):
        return "poh_mounted", bare
    if not tail.strip() and consumable(key) is not None:
        return "item", key
    destination = _TRAILING_OPTION.sub("", tail).strip()
    # CONTAINMENT, not equality: Aya's registry writes "Morytania Slayer Tower" for the ring's
    # "Slayer Tower" (found by sweeping her 24 tasks live, 2026-09-20).
    known = next((d for d in DIALOGUE_ITEMS.get(key, {}) if d in destination.lower()), None)
    if known is not None:
        return "dialogue_item", (key, known)
    canonical = _BOX_LANDINGS_BY_LOWER.get(destination.lower())
    if canonical is not None:
        return "box", canonical
    return "unsupported", text


def spends_a_teleport(route) -> bool:
    """Does this route actually TELEPORT, or is it just walking?

    FOUND LIVE 2026-09-21: standing ON the Varrock sewer manhole, the pack set off 40 tiles to a
    bank for another Varrock tablet -- one transport from done. The first fix asked "did the
    planner find a route", which is the wrong question, because a walk is a route. This is the
    right one.

    Lived in routines/slayer.py until 2026-09-21, and moved here so slayer_preflight can ask it
    too: the tool exists to print what the routine WOULD do, and without this it printed a bank
    trip the routine was not going to make."""
    return any((leg or {}).get("kind") in TELEPORT_LEG_KINDS for leg in route or ())


def nearest_ring_costed(player_tile, skill_levels=None):
    """(ring, walk in tiles) for the nearest overworld fairy ring, or (None, None).

    The cost was always measured here and thrown away; since 2026-09-21 it is returned, because
    the walk to the ring is part of what a ring-and-hop route costs when it is compared against
    walking the whole way."""
    best = None
    for ring in transports.load_fairy_rings().entrances:
        ox, oy, plane = ring.origin
        if plane != player_tile[2]:
            continue
        if max(abs(ox - player_tile[0]), abs(oy - player_tile[1])) > RING_WALK_MAX:
            continue
        route = crossfloor.find_route(tuple(player_tile), ring.origin, skill_levels=skill_levels, max_links=0)
        if route is None:
            continue
        # CROSSFLOOR REACHES WITHIN GOAL_TOLERANCE, NOT THE GOAL -- and that is the whole bug.
        #
        # FOUND LIVE 2026-09-21 on bears. This offered the BLR ring at (3082,3206) on the
        # strength of a find_route that came back with cost 107; the game then said "I can't
        # reach that!" three times while the leg re-clicked a Configure that registered
        # perfectly every time (/last-action confirmed each one). Reading the Route rather than
        # its truthiness settles it: its single step is `Walk(107 tiles -> (3091, 3215, 0))`,
        # nine tiles from the ring, and crossfloor.GOAL_TOLERANCE is 12. find_route did exactly
        # what it documents -- "a route to WITHIN GOAL_TOLERANCE of goal" -- because site
        # coordinates from research are cluster centres and often not walkable at all.
        #
        # That tolerance is right for a SITE and wrong for a fairy ring, which has to be stood
        # next to. worldmap is asked because it has no tolerance: it either reaches the ring or
        # it does not. This can only ever REJECT a route, so the worst case is falling back to
        # the house ring -- which is what this task's route was before the walk branch existed,
        # and which works.
        # ADJACENT, not the tile itself: a fairy ring BLOCKS its own tile in the static map, the
        # same fact legs.walk_across's step-off exists for. Asking for the tile rejects every
        # ring in the game (measured: the perfectly good BLR ring at (2740,3351) answers None
        # that way and 70 steps this way), which three existing tests caught immediately.
        if worldmap.find_path(player_tile[0], player_tile[1], player_tile[2],
                              ring.origin[0], ring.origin[1], goal_adjacent=True) is None:
            continue
        if best is None or route.cost < best[0]:
            best = (route.cost, ring)
    return (best[1], best[0]) if best else (None, None)


def nearest_ring(player_tile, skill_levels=None):
    """The overworld fairy ring with the shortest WALK from `player_tile`, or None."""
    return nearest_ring_costed(player_tile, skill_levels)[0]


def walk_goal(task):
    """Where the last walk goes: the task's `target`, unless that tile lies OUTSIDE every kill
    area -- then the first area's centre.

    FOUND 2026-09-20 auditing all 24 Aya sites against the spawn table: the Turael Skipping
    registry's target for SKELETONS is the SURFACE tile by the Digsite Dungeon's entrance, while
    its kill area (ten skeleton spawns) is underground. Walking to the target meant arriving,
    reading 'not at the site', and stopping with the teleport already spent."""
    target = tuple(task.target)
    for ax, ay, w, h, p in task.areas or ():
        if ax <= target[0] < ax + w and ay <= target[1] < ay + h and target[2] == p:
            return target
    if not task.areas:
        return target
    ax, ay, w, h, p = task.areas[0]
    return (ax + w // 2, ay + h // 2, p)


def near_site(task_name, player, skill_levels=None) -> bool:
    """Is the player a short WALK (no transports) from the task's site? The test plan() uses to
    walk back instead of re-teleporting, and the one routines/slayer._pack uses to leave a task in
    progress alone."""
    x, y = (player or {}).get("x"), (player or {}).get("y")
    task = aya_tasks.get(task_name) or slayer_catalog.get(task_name)
    if x is None or y is None or task is None or not task.target:
        return False
    near = crossfloor.find_route((x, y, player.get("plane", 0)), walk_goal(task),
                                 skill_levels=skill_levels, max_links=0)
    return near is not None and near.cost <= NEAR_SITE_TILES


def walk_route(here, goal, skill_levels=None, proven=None):
    """The whole journey ON FOOT from `here`: worldmap's floods joined by every transport this
    account may take (ladders, manholes, cave mouths). A crossfloor.Route, or None.

    This is the question near_site deliberately does not ask -- it passes max_links=0, so no
    transport is ever on the table and a site one manhole away reads as unreachable. FOUND LIVE
    2026-09-21 with the rats task: standing ON the Varrock sewer manhole, plan() answered "Varrock
    teleport: not carried" and the pack walked 40 tiles to a bank for one, while the site was 16
    tiles and a single manhole away.

    MEASURED: 0.07s when a route exists, up to ~2s when none does -- an impossible search spends
    crossfloor's whole expansion budget. plan() pays that once.

    WALK_MAX_TILES is applied by the CALLER, not here, so a refused walk can still be named with
    its real length in the safety stop."""
    return crossfloor.find_route(tuple(here), tuple(goal), skill_levels=skill_levels,
                                 **(proven or {}))


def straight_line(here, goal) -> int:
    """A LOWER BOUND on walking from `here` to `goal`, in tiles: the Chebyshev distance, with
    OSRS's dungeon convention folded in the way crossfloor's own heuristic folds it -- a dungeon is
    drawn at (x, y + 6400), so a cave under our feet must not read as 6400 tiles away.

    crossfloor.heuristic would do this and also penalise a plane mismatch by 40 a floor, which is
    not a bound on anything -- it exists to ORDER that search. Overstating the distance here would
    skip a real short walk (the Slayer Tower basement is three floors up from its own entrance), so
    the penalty is left out and this stays a true lower bound.

    Why it exists: a search that finds nothing spends crossfloor's whole expansion budget, ~1.3 s
    MEASURED, and most slayer sites are thousands of tiles away -- Mount Karuulm is 1926 from the
    Varrock manhole. No route can be shorter than this, so when the bound alone is past
    WALK_MAX_TILES there is nothing to look for."""
    dy = min(abs(here[1] - goal[1]),
             abs(here[1] + crossfloor.UNDERGROUND_DY - goal[1]),
             abs(here[1] - crossfloor.UNDERGROUND_DY - goal[1]))
    return max(abs(here[0] - goal[0]), dy)


def walk_leg(goal, route):
    """The whole journey as ONE walk leg. legs.walk_across re-plans from the LIVE tile after every
    transport, so nothing about `route` is baked into the leg -- the measured cost and transport
    count go in the display, where the routine's own travel log prints them."""
    leg = legs.walk(*goal)
    links = len(route.links)
    leg["display"] += f" on foot ({route.cost} tiles, {links} transport{'' if links == 1 else 's'})"
    return leg


def _hops(route) -> int:
    """How many legs of `route` cost a teleport's worth of waiting."""
    return sum(1 for leg in route or () if (leg or {}).get("kind") in HOP_KINDS)


def _onward(route, target, skill_levels, proven):
    """(walk in tiles from the LAST teleport's landing to the site, problem). `problem` is None
    when that walk can be made, else why not.

    Added 2026-09-20 as _unroutable, which answered the problem alone. A teleport is spent the
    moment it is clicked, and the walk leg after it fails only on arrival -- a strand with a task
    active. So before a route is offered, the walk from the teleport's landing to the site is
    proven with crossfloor, using what THIS account proves (its levels, and `proven` =
    legs.requirement_values()). A landing we do not know (dest None) cannot be checked and is let
    through, as before -- with a cost of None, which plan() reads as "cannot compare" and keeps the
    teleport.

    Since 2026-09-21 it returns the cost it already measured, so comparing the route against
    walking the whole way costs no second search."""
    landing = next((leg.get("dest") for leg in reversed(route[:-1]) if leg.get("dest")), None)
    if not landing or len(landing) != 3:
        return None, None
    onward = crossfloor.find_route(tuple(landing), tuple(target), skill_levels=skill_levels,
                                   **(proven or {}))
    if onward is None:
        return None, f"no walking route from its landing {tuple(landing)} to {tuple(target)} for this account"
    return onward.cost, None


def plan(task_name, player, carried, skill_levels=None, proven=None):
    """(legs, "") for the best route we can run, else (None, why). `proven` is
    legs.requirement_values(): the varbits, item ids and coins that open gated transports.

    In order: a short walk back when we are already near the site; else the first usable
    teleport from the task's own list, UNLESS walking the whole way beats it by
    MIN_SAVING_TILES; else that walk on its own when it is inside WALK_MAX_TILES; else None,
    naming every teleport that could not be used and how far the walk would have been."""
    x, y = (player or {}).get("x"), (player or {}).get("y")
    if x is None or y is None:
        return None, "the player's position is unknown"
    here = (x, y, player.get("plane", 0))
    task = aya_tasks.get(task_name) or slayer_catalog.get(task_name)
    if task is None or not task.target:
        return None, f"{task_name} has no site in the catalog yet"
    goal = walk_goal(task)
    site = legs.walk(*goal)
    # PHASE-A REVIEW: chasing a monster two tiles out of the kill rectangle flips the routine to
    # TRAVEL, and without this the "route" back was the whole journey again -- walk 41 tiles to
    # the AJQ ring and dial AJQ while standing in the AJQ cave; for Alice's farm, fire the
    # Ectophial. When the site is a short walk away, the route is that walk and nothing else.
    # Unchanged by phase B, including when the player happens to be in the house (its own
    # comment explains why THAT combination is not worth special-casing here).
    if near_site(task_name, player, skill_levels=skill_levels):
        return [site], ""
    # The house, not just "an instance": a raid, Pest Control or a Sailing boat is an instance too
    # and must not be offered a tablet-less house route (phase-B review).
    in_house = poh.is_house_player(player)
    has_tablet = any("teleport to house" in (held or "").lower() for held in (carried or {}))
    reasons = []
    # The near-site walk above costs nothing, so it stays available. Past it, a catalog entry
    # marked not routable is REFUSED before any teleport: its last walk cannot succeed (gryphons:
    # the cave entrance is in no table), and spending the hop would strand us with a task active.
    spec = slayer_catalog.get(task_name)
    if spec is not None and not spec.routable and aya_tasks.get(task_name) is None:
        return None, f"{task_name} is not routable yet -- {spec.note}"
    # A task BOTH masters assign (kalphite) is offered both registries' teleports, Aya's first:
    # same cave, and Aya's list alone stopped at a desert amulet no leg can run.
    other = slayer_catalog.get(task_name)
    teleports = list(dict.fromkeys(tuple(task.teleports) + tuple(other.teleports if other else ())))
    # The first usable teleport, costed -- a list so the closure fills it without `nonlocal`.
    chosen = []

    def offer(route, text, extra=0):
        """True when `route` can be run, and then it is THE teleport candidate (the loop breaks).

        `extra` is walking this branch has already measured -- the walk to a fairy ring, the climb
        to the glider pilot. The onward walk and the hops are costed here. A cost of None means
        the landing is unknown, and plan() then keeps the teleport rather than guess."""
        onward, problem = _onward(route, goal, skill_levels, proven)
        if problem is not None:
            reasons.append(f"{text}: {problem}")
            return False
        chosen.append((None if onward is None else int(extra) + onward + TELEPORT_COST_TILES * _hops(route),
                       route))
        return True

    for text in teleports:
        kind, value = parse(text)
        if kind == "fairy":
            if value not in FAIRY_EXPECT:
                reasons.append(f"{text}: no Confirm-text check is known for {value}")
                continue
            exit_tile = transports.load_fairy_rings().exit_for(value)
            if exit_tile is None:
                reasons.append(f"{text}: no exit tile known for {value}")
                continue
            # An overworld ring within walking range is preferred over the house -- never
            # consulted at all while already inside the house, where template coordinates are
            # meaningless distances (phase B brief).
            ring, ring_walk = (None, None) if in_house else nearest_ring_costed(here, skill_levels)
            if ring is not None:
                route = [legs.walk(*ring.origin),
                         legs.fairy(value, FAIRY_EXPECT[value], dest=exit_tile,
                                    ring_tile=ring.origin, ring_id=ring.object_id),
                         site]
                if offer(route, text, extra=ring_walk):
                    break
                continue
            if not has_tablet and not in_house:
                reasons.append(f"{text}: no fairy ring within a {RING_WALK_MAX}-tile walk of "
                               f"{here} and no house tablet")
                continue
            route = [legs.house(), legs.pool(),
                     legs.house_ring(value, FAIRY_EXPECT[value], dest=exit_tile), site]
            if offer(route, text):
                break
            continue
        if kind == "item":
            name, option, landing = item_row(value)
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            route = [legs.item(name, option, landing), site]
            if offer(route, text):
                break
            continue
        if kind == "spell":
            name, widget, landing = SPELLS[value]
            short = missing_runes(value, carried)
            if short:
                reasons.append(f"{text}: needs " + ", ".join(short))
                continue
            route = [legs.spell(name, widget, landing), site]
            if offer(route, text):
                break
            continue
        if kind == "dialogue_item":
            name, destination = value
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            label, needle, landing = DIALOGUE_ITEMS[name][destination]
            route = [legs.dialogue_item(name, label, needle, landing), site]
            if offer(route, text):
                break
            continue
        if kind == "glider":
            label, widget, landing = GLIDERS[value]
            ring, ring_dest = GLIDER_APPROACH
            if not any(ring in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: needs a {ring} to reach the pilot on the Grand Tree -- not carried")
                continue
            ring_label, needle, ring_landing = DIALOGUE_ITEMS[ring][ring_dest]
            pilot, pilot_tile = GLIDER_PILOT
            # The climb is part of the route, so it is proven too: ring landing -> the pilot.
            climb = crossfloor.find_route(ring_landing, pilot_tile, skill_levels=skill_levels,
                                          **(proven or {}))
            if climb is None:
                reasons.append(f"{text}: no walking route from {ring_landing} up to {pilot} at {pilot_tile}")
                continue
            route = [legs.dialogue_item(ring, ring_label, needle, ring_landing), legs.walk(*pilot_tile),
                     legs.glider(pilot, label, widget, landing), site]
            if offer(route, text, extra=climb.cost):
                break
            continue
        if kind == "poh_mounted":
            name, action, landing = MOUNTED[value]
            if not has_tablet and not in_house:
                reasons.append(f"{text}: needs a house tablet (it is mounted in the house)")
                continue
            route = [legs.house(), legs.pool(), legs.house_mounted(name, action, landing), site]
            if offer(route, text):
                break
            continue
        if kind == "box":
            landing = BOX_LANDINGS.get(value)
            if landing is None:
                reasons.append(f"{text}: no landing tile known for {value}")
                continue
            if not has_tablet and not in_house:
                reasons.append(f"{text}: needs a house tablet")
                continue
            route = [legs.house(), legs.pool(), legs.house_box(value, landing), site]
            if offer(route, text):
                break
            continue
        reasons.append(f"{text}: no leg can run this teleport yet")

    # WALKING THE WHOLE WAY, measured once and used by both outcomes: as the thing a usable
    # teleport has to beat, and as the last resort when none is usable. Never from inside the
    # house -- template x/y are meaningless distances there, the rule nearest_ring already follows.
    # `straight_line` is a lower bound, so a site further off than the cap skips the search
    # altogether: a search that finds nothing is the expensive one (~1.3 s), and most sites are
    # thousands of tiles away.
    bound = straight_line(here, goal)
    walk = None if in_house or bound > WALK_MAX_TILES else walk_route(here, goal, skill_levels, proven)
    walkable = walk is not None and walk.cost <= WALK_MAX_TILES
    if chosen:
        cost, route = chosen[0]
        if not walkable or cost is None or walk.cost + MIN_SAVING_TILES > cost:
            return route, ""
        return [walk_leg(goal, walk)], ""
    if walkable:
        return [walk_leg(goal, walk)], ""
    if walk is not None:
        reasons.append(f"walking the whole way is {walk.cost} tiles, over the {WALK_MAX_TILES}-tile cap")
    elif not in_house and bound > WALK_MAX_TILES:
        reasons.append(f"the site is at least {bound} tiles away on foot, over the "
                       f"{WALK_MAX_TILES}-tile cap")
    return None, "; ".join(reasons) or f"{task_name} lists no teleport"
