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
# PHASE B (2026-09-20): the house (tablet -> pool -> ornate box / house fairy ring) joins the
# teleport order as a fallback behind an overworld ring, never ahead of one -- see plan()'s own
# comment. `has_tablet` gates every house route UNLESS the player is already standing inside the
# instance (`player["inInstance"]`), in which case poh.enter's own no-op-when-already-inside
# makes a tablet moot -- and, per the phase B brief, `nearest_ring` must not even be CALLED in
# that case: an in-house player's template x/y are meaningless distances (see poh.py's own
# instX/instY note), so consulting it there would compare real tiles against instance geometry.

import re

import aya_tasks
import slayer_catalog
import crossfloor
import legs
import poh
import transports

RING_WALK_MAX = 150          # tiles, straight-line pre-filter before the real walk is costed
NEAR_SITE_TILES = 64         # within this WALK of the site, never plan a teleport -- just walk back

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
ITEMS = {
    "ectophial": ("ectophial", "Empty", (3660, 3522, 0)),
    # Added 2026-09-20 with the Kuradal catalog: inventory items whose teleport is a DIRECT menu
    # option, the mechanism the Ectophial already proves. Options and landings are UNVERIFIED LIVE
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

# Items MOUNTED in the house (legs.house_mounted): registry head -> (object name fragment, menu
# action, landing). USER 2026-09-20: "There is a digsite pendant mounted in poh". The row is the
# vendored teleportation_boxes.tsv's ("Digsite Digsite Pendant 33417" -> 3341 3445 0); the object
# is found by NAME + ACTION, not by that id. Like the box and the house ring it needs a tablet
# or being in the house already. UNVERIFIED LIVE.
MOUNTED = {
    "digsite pendant": ("digsite pendant", "Digsite", (3341, 3445, 0)),
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


def nearest_ring(player_tile, skill_levels=None):
    """The overworld fairy ring with the shortest WALK from `player_tile`, or None."""
    best = None
    for ring in transports.load_fairy_rings().entrances:
        ox, oy, plane = ring.origin
        if plane != player_tile[2]:
            continue
        if max(abs(ox - player_tile[0]), abs(oy - player_tile[1])) > RING_WALK_MAX:
            continue
        route = crossfloor.find_route(tuple(player_tile), ring.origin, skill_levels=skill_levels, max_links=0)
        if route is not None and (best is None or route.cost < best[0]):
            best = (route.cost, ring)
    return best[1] if best else None


def _unroutable(route, target, skill_levels, proven):
    """None when the walk after the LAST teleport in `route` can be made, else why not.

    Added 2026-09-20. A teleport is spent the moment it is clicked, and the walk leg after it
    fails only on arrival -- a strand with a task active. So before a route is offered, the walk
    from the teleport's landing to the site is proven with crossfloor, using what THIS account
    proves (its levels, and `proven` = legs.requirement_values()). A landing we do not know
    (dest None) cannot be checked and is let through, as before."""
    landing = next((leg.get("dest") for leg in reversed(route[:-1]) if leg.get("dest")), None)
    if not landing or len(landing) != 3:
        return None
    if crossfloor.find_route(tuple(landing), tuple(target), skill_levels=skill_levels, **(proven or {})) is None:
        return f"no walking route from its landing {tuple(landing)} to {tuple(target)} for this account"
    return None


def plan(task_name, player, carried, skill_levels=None, proven=None):
    """(legs, "") for the first usable teleport, else (None, why). `proven` is
    legs.requirement_values(): the varbits, item ids and coins that open gated transports."""
    x, y = (player or {}).get("x"), (player or {}).get("y")
    if x is None or y is None:
        return None, "the player's position is unknown"
    here = (x, y, player.get("plane", 0))
    task = aya_tasks.get(task_name) or slayer_catalog.get(task_name)
    if task is None or not task.target:
        return None, f"{task_name} has no site in the catalog yet"
    site = legs.walk(*task.target)
    # PHASE-A REVIEW: chasing a monster two tiles out of the kill rectangle flips the routine to
    # TRAVEL, and without this the "route" back was the whole journey again -- walk 41 tiles to
    # the AJQ ring and dial AJQ while standing in the AJQ cave; for Alice's farm, fire the
    # Ectophial. When the site is a short walk away, the route is that walk and nothing else.
    # Unchanged by phase B, including when the player happens to be in the house (its own
    # comment explains why THAT combination is not worth special-casing here).
    near = crossfloor.find_route(here, tuple(task.target), skill_levels=skill_levels, max_links=0)
    if near is not None and near.cost <= NEAR_SITE_TILES:
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
    def offer(route, text):
        problem = _unroutable(route, task.target, skill_levels, proven)
        if problem is not None:
            reasons.append(f"{text}: {problem}")
        return problem is None

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
            ring = None if in_house else nearest_ring(here, skill_levels)
            if ring is not None:
                route = [legs.walk(*ring.origin),
                         legs.fairy(value, FAIRY_EXPECT[value], dest=exit_tile,
                                    ring_tile=ring.origin, ring_id=ring.object_id),
                         site]
                if offer(route, text):
                    return route, ""
                continue
            if not has_tablet and not in_house:
                reasons.append(f"{text}: no fairy ring within a {RING_WALK_MAX}-tile walk of "
                               f"{here} and no house tablet")
                continue
            route = [legs.house(), legs.pool(),
                     legs.house_ring(value, FAIRY_EXPECT[value], dest=exit_tile), site]
            if offer(route, text):
                return route, ""
            continue
        if kind == "item":
            name, option, landing = item_row(value)
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            route = [legs.item(name, option, landing), site]
            if offer(route, text):
                return route, ""
            continue
        if kind == "spell":
            name, widget, landing = SPELLS[value]
            route = [legs.spell(name, widget, landing), site]
            if offer(route, text):
                return route, ""
            continue
        if kind == "dialogue_item":
            name, destination = value
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            label, needle, landing = DIALOGUE_ITEMS[name][destination]
            route = [legs.dialogue_item(name, label, needle, landing), site]
            if offer(route, text):
                return route, ""
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
            if crossfloor.find_route(ring_landing, pilot_tile, skill_levels=skill_levels,
                                     **(proven or {})) is None:
                reasons.append(f"{text}: no walking route from {ring_landing} up to {pilot} at {pilot_tile}")
                continue
            route = [legs.dialogue_item(ring, ring_label, needle, ring_landing), legs.walk(*pilot_tile),
                     legs.glider(pilot, label, widget, landing), site]
            if offer(route, text):
                return route, ""
            continue
        if kind == "poh_mounted":
            name, action, landing = MOUNTED[value]
            if not has_tablet and not in_house:
                reasons.append(f"{text}: needs a house tablet (it is mounted in the house)")
                continue
            route = [legs.house(), legs.pool(), legs.house_mounted(name, action, landing), site]
            if offer(route, text):
                return route, ""
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
                return route, ""
            continue
        reasons.append(f"{text}: no leg can run this teleport yet")
    return None, "; ".join(reasons) or f"{task_name} lists no teleport"
