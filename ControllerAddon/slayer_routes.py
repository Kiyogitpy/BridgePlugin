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
}

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
    if key in ITEMS:
        return "item", key
    destination = _TRAILING_OPTION.sub("", tail).strip()
    if destination.lower() in DIALOGUE_ITEMS.get(key, {}):
        return "dialogue_item", (key, destination.lower())
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


def plan(task_name, player, carried, skill_levels=None):
    """(legs, "") for the first usable teleport, else (None, why)."""
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
    for text in task.teleports:
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
                return [legs.walk(*ring.origin),
                        legs.fairy(value, FAIRY_EXPECT[value], dest=exit_tile,
                                   ring_tile=ring.origin, ring_id=ring.object_id),
                        site], ""
            if not has_tablet and not in_house:
                reasons.append(f"{text}: no fairy ring within a {RING_WALK_MAX}-tile walk of "
                               f"{here} and no house tablet")
                continue
            return [legs.house(), legs.pool(),
                    legs.house_ring(value, FAIRY_EXPECT[value], dest=exit_tile), site], ""
        if kind == "item":
            name, option, landing = ITEMS[value]
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            return [legs.item(name, option, landing), site], ""
        if kind == "dialogue_item":
            name, destination = value
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            label, needle, landing = DIALOGUE_ITEMS[name][destination]
            return [legs.dialogue_item(name, label, needle, landing), site], ""
        if kind == "box":
            landing = BOX_LANDINGS.get(value)
            if landing is None:
                reasons.append(f"{text}: no landing tile known for {value}")
                continue
            if not has_tablet and not in_house:
                reasons.append(f"{text}: needs a house tablet")
                continue
            return [legs.house(), legs.pool(), legs.house_box(value, landing), site], ""
        reasons.append(f"{text}: no leg can run this teleport yet")
    return None, "; ".join(reasons) or f"{task_name} lists no teleport"
