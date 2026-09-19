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

import aya_tasks
import crossfloor
import legs
import transports

RING_WALK_MAX = 150          # tiles, straight-line pre-filter before the real walk is costed

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
}


def parse(teleport_text):
    text = (teleport_text or "").strip()
    head, _, tail = text.partition(":")
    key = head.strip().lower()
    if key == "fairy ring" and len(tail.strip()) == 3:
        return "fairy", tail.strip().upper()
    if key in ITEMS:
        return "item", key
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
    task = aya_tasks.get(task_name)
    if task is None or not task.target:
        return None, f"{task_name} has no site in the catalog yet"
    site = legs.walk(*task.target)
    reasons = []
    for text in task.teleports:
        kind, value = parse(text)
        if kind == "fairy":
            if value not in FAIRY_EXPECT:
                reasons.append(f"{text}: no Confirm-text check is known for {value}")
                continue
            exit_tile = transports.load_fairy_rings().exit_for(value)
            ring = nearest_ring(here, skill_levels)
            if exit_tile is None or ring is None:
                reasons.append(f"{text}: no fairy ring within a {RING_WALK_MAX}-tile walk of {here}")
                continue
            return [legs.walk(*ring.origin),
                    legs.fairy(value, FAIRY_EXPECT[value], dest=exit_tile,
                               ring_tile=ring.origin, ring_id=ring.object_id),
                    site], ""
        if kind == "item":
            name, option, landing = ITEMS[value]
            if not any(name in (held or "").lower() for held in (carried or {})):
                reasons.append(f"{text}: not carried")
                continue
            return [legs.item(name, option, landing), site], ""
        reasons.append(f"{text}: not supported until the house/bank phases")
    return None, "; ".join(reasons) or f"{task_name} lists no teleport"
