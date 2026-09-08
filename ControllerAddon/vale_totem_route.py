"""Where the eight Vale Totems sites are, and how the human walks between them.

An ACCELERATOR, never truth -- the same rule trawling_routes.py states. The live reader
(GET /api/minigames/vale-totems) decides WHAT to do at a site; this table only says where to
walk. Site ids and tiles were MEASURED from recordings/2026-09-08_135457_vale-totem.*.

WHY WAYPOINTS AND NOT worldmap.find_path: worldmap.py has NO usable collision data for any of
Varlamore. MEASURED 2026-09-08 -- reachable_from(radius=40) answers 1 tile at every totem site,
at the bank, at Civitas illa Fortis, at Aldarin and at the Hunter guild, and find_path between
any two sites answers None. So the routine walks the path the human actually walked: these are
their own tick-by-tick positions, simplified to ~10-tile steps, six of the eight legs
cross-checked against a second traversal.
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

# Agility shortcuts standing ON the recorded path -- 56990 is one tile off leg 4->3's second
# waypoint. Recorded so a live failure there is recognisable; nothing reads them yet.
LOG_BALANCES = {56990: (1401, 3290), 56991: (1453, 3335)}

LEGS = {
    (6, 5): ((1477, 3330), (1473, 3319), (1463, 3314), (1453, 3313), (1443, 3315), (1438, 3307)),
    (5, 4): ((1438, 3307), (1435, 3317), (1424, 3318), (1422, 3307), (1420, 3297), (1412, 3288),
             (1412, 3287)),
    (4, 3): ((1412, 3287), (1402, 3290), (1396, 3281), (1387, 3275), (1386, 3275)),
    (3, 2): ((1386, 3275), (1381, 3285), (1372, 3291), (1361, 3296), (1355, 3305), (1350, 3314),
             (1347, 3318)),
    (2, 1): ((1347, 3318), (1351, 3328), (1359, 3335), (1368, 3343), (1368, 3354), (1365, 3365),
             (1370, 3373)),
    (1, 8): ((1370, 3374), (1377, 3365), (1385, 3359), (1385, 3349), (1393, 3340), (1398, 3331)),
    (8, 7): ((1398, 3331), (1405, 3339), (1412, 3347), (1422, 3348), (1433, 3346), (1443, 3345),
             (1451, 3341)),
    (7, 6): ((1451, 3341), (1453, 3331), (1461, 3324), (1473, 3322), (1478, 3331)),
}


def next_in_ring(site: int) -> int:
    """The site after `site` on the circuit."""
    return RING[(RING.index(site) + 1) % len(RING)]


def leg_waypoints(a: int, b: int) -> tuple:
    """The measured path from site `a` to site `b`, or () when that leg was never recorded."""
    return LEGS.get((a, b), ())
