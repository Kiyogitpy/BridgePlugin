"""Where the eight Vale Totems sites are, and how the human walks between them.

An ACCELERATOR, never truth -- the same rule trawling_routes.py states. The live reader
(GET /api/minigames/vale-totems) decides WHAT to do at a site; this table only says where to
walk. Site ids and tiles were MEASURED from recordings/2026-09-08_135457_vale-totem.*; the
legs are USER's ground markers (2026-09-10).

WHY WAYPOINTS AND NOT worldmap.find_path: worldmap.py has NO usable collision data for any of
Varlamore. MEASURED 2026-09-08 -- reachable_from(radius=40) answers 1 tile at every totem site,
at the bank, at Civitas illa Fortis, at Aldarin and at the Hunter guild, and find_path between
any two sites answers None. So the routine walks a fixed table of click targets: USER's own
ground markers of the optimal lap (2026-09-10), which replaced the human recording's
tick-by-tick positions.
"""

PLANE = 0

SITES = {
    1: {"base_id": 57016, "offerings_id": 57020,
        "totem": (1370, 3375), "offerings": (1370, 3374), "stand": (1369, 3375)},
    2: {"base_id": 57022, "offerings_id": 57026,
        "totem": (1346, 3319), "offerings": (1347, 3319), "stand": (1346, 3318)},
    3: {"base_id": 57028, "offerings_id": 57032,
        "totem": (1385, 3274), "offerings": (1385, 3275), "stand": (1386, 3274)},
    4: {"base_id": 57034, "offerings_id": 57038,
        "totem": (1413, 3286), "offerings": (1412, 3286), "stand": (1413, 3287)},
    5: {"base_id": 57040, "offerings_id": 57044,
        "totem": (1438, 3305), "offerings": (1438, 3306), "stand": (1437, 3305)},
    6: {"base_id": 57046, "offerings_id": 57050,
        "totem": (1477, 3332), "offerings": (1478, 3332), "stand": (1477, 3331)},
    7: {"base_id": 57052, "offerings_id": 57056,
        "totem": (1453, 3341), "offerings": (1452, 3341), "stand": (1453, 3340)},
    8: {"base_id": 57058, "offerings_id": 57062,
        "totem": (1398, 3329), "offerings": (1398, 3330), "stand": (1397, 3329)},
}

# The order the human walks, both laps: descending site number, wrapping 1 -> 8.
RING = (6, 5, 4, 3, 2, 1, 8, 7)

BANK = {"object_id": 57330, "tile": (1416, 3351), "stand": (1416, 3350)}

# ONE CLICK PER STRETCH: USER's own ground markers of "the most optimal path" (2026-09-10),
# decoded from region coordinates. Each tuple is the sequence of click targets from site a's
# stand tile, the last one being site b's stand tile itself; every stretch is at most 24
# tiles, travel.WALK_HOP_MAX_TILES, so any of them is one minimap click or one viewport
# click with the camera facing the leg. The (3, 2) leg is the markers' "LOG BASKET / NO BANK"
# line -- the Nemus Retreat Bank buffalo branch ("11 - BANK" / "DETOUR EXIT") is deliberately
# not used: the basket carries the lap, and banking stays at the Auburnvale booth.
LEGS = {
    (7, 6): ((1453, 3336), (1466, 3320), (1477, 3331)),
    (6, 5): ((1468, 3314), (1455, 3313), (1437, 3305)),
    (5, 4): ((1422, 3307), (1413, 3287)),
    (4, 3): ((1401, 3291), (1386, 3274)),
    (3, 2): ((1369, 3294), (1346, 3318)),
    (2, 1): ((1362, 3337), (1368, 3354), (1369, 3375)),
    (1, 8): ((1383, 3363), (1385, 3346), (1397, 3329)),
    (8, 7): ((1415, 3339), (1435, 3346), (1453, 3340)),
}


def next_in_ring(site: int) -> int:
    """The site after `site` on the circuit."""
    return RING[(RING.index(site) + 1) % len(RING)]


def leg_waypoints(a: int, b: int) -> tuple:
    """The measured path from site `a` to site `b`, or () when that leg was never recorded."""
    return LEGS.get((a, b), ())
