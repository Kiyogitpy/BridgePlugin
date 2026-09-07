# teleports.py
#
# "Which teleport gets me closest to where I'm going, out of the ones I actually own?"
#
# The data is Shortest Path's own `transports/teleportation_items.tsv`, vendored verbatim into
# vendor/ -- see vendor/VENDORED.md for provenance. USER 2026-09-07: "we jhust skip the handshake
# altogether and use the teleport data from the plugin itself". That is the better call and it is
# also this codebase's own rule (CLAUDE.md, the 231.6 dialogue-widget lesson): when a plugin
# already knows a fact, copy its SOURCE rather than your reconstruction of it. Two things here
# were previously hand-rolled reconstructions of rows in this very file --
# travel.RING_OF_DUELING_NAME's Ferox teleport and mahogany_homes.CITY_TELEPORTS.
#
# It replaced a PluginMessage handshake with the live plugin, which is NOT available any more:
# our ShortestPathProvider.java was deleted in 1e96c8d and the plugin is no longer an enabled
# Plugin Hub install. The handshake also carried a documented hazard -- its answers were governed
# by the user's own Shortest Path panel, which once produced a route whose first edge was a
# QUETZAL_WHISTLE to a tile 454 tiles away. A flat table has no such coupling.
#
# The table keys on ITEM IDS, and bank_memory reports item ids, so the two join directly.
#
# THIS MODULE IS PURE. It does no HTTP, reads no client state and executes nothing -- `choose`
# takes the routing function as an argument. Execution lives in banker.py, where a teleport that
# fails to fire falls back to walking.

import os


# vendor/, NOT data/ -- .gitignore line 11 is a bare `data/`, which matches at any depth and
# would silently swallow this table (it exists for the repo-root data/decompiled and data/raw
# trees). Caught before the first commit: the file would never have been committed and a fresh
# clone would crash here rather than degrade.
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "vendor", "teleportation_items.tsv")

# The column order differs BETWEEN Shortest Path's own transport files -- teleportation_spells.tsv
# puts Items second where this file puts it fourth -- so the header is asserted rather than
# assumed. A silent upstream re-order would otherwise mis-parse every row into plausible-looking
# nonsense instead of failing. Same discipline as M8aqProviderTest.knownPathsAreStable.
EXPECTED_HEADER = [
    "Destination", "menuOption menuTarget objectID", "Skills", "Items", "Quests",
    "Duration", "Display info", "Consumable", "Wilderness level", "Varbits", "VarPlayers",
]

# A teleport is not free: withdrawing it, the click, the dialogue and the ~4-tick animation cost
# real time, so it must beat walking by more than a rounding error before it is worth breaking
# one. Expressed in tiles because that is the unit the router answers in. FIRST-DRAFT GUESSES --
# both want a live session to fit, like every other budget here.
TELEPORT_COST_TILES = 25
MIN_SAVING_TILES = 40

# A player's house can be anywhere, so the coordinates the table carries for these are the
# author's, not a fact about us.
PLAYER_SPECIFIC = ("house",)

_cache = {}


class Teleport:
    """One row: somewhere you can go, and the items that take you there."""

    __slots__ = ("dest", "item_ids", "display", "item_name", "destination_name",
                 "duration", "max_wilderness_level", "consumable")

    def __init__(self, dest, item_ids, display, item_name, destination_name,
                 duration, max_wilderness_level, consumable):
        self.dest = dest
        self.item_ids = item_ids
        self.display = display
        self.item_name = item_name
        self.destination_name = destination_name
        self.duration = duration
        self.max_wilderness_level = max_wilderness_level
        self.consumable = consumable

    def __repr__(self):
        return f"<Teleport {self.display} -> {self.dest}>"


def _parse_items(field):
    """The Items column, as the set of ids any ONE of which is enough.

    MEASURED over the vendored table: zero rows use `&&`, so this is purely OR alternatives --
    typically the charge variants of one item (ring of dueling (8) down to (1)). Matching only
    the full-charge id would miss a part-used ring, which is the common case.
    """
    ids = set()
    for alternative in field.split("||"):
        alternative = alternative.strip()
        if not alternative:
            continue
        token = alternative.split("=")[0].strip()
        try:
            ids.add(int(token))
        except ValueError:
            continue
    return frozenset(ids)


def _parse_dest(field):
    parts = field.split()
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _parse_int(field, default=None):
    try:
        return int(field.strip())
    except (ValueError, AttributeError):
        return default


def load(path=None):
    """Parse the vendored table. Cached per path; the file never changes at runtime."""
    path = path or DATA_PATH
    if path in _cache:
        return _cache[path]

    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    if not lines:
        raise ValueError(f"teleport table is empty: {path}")

    header = [c.strip() for c in lines[0].lstrip("#").split("\t")]
    if header[:len(EXPECTED_HEADER)] != EXPECTED_HEADER:
        raise ValueError(
            f"unexpected header in {path} -- Shortest Path may have re-ordered its columns, "
            f"which would mis-parse every row rather than fail. Got: {header}")

    out = []
    for line in lines[1:]:
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 9:
            continue

        dest = _parse_dest(cols[0])
        item_ids = _parse_items(cols[3])
        # 20 of the 340 rows are blanks/section markers carrying neither a destination nor an
        # item. A teleport we cannot locate, or cannot own, is not a candidate.
        if dest is None or not item_ids:
            continue

        display = cols[6].strip()
        # "Ring of dueling: Ferox Enclave" -- the part after the colon is also the text of the
        # destination option in the item's own dialogue, which is what makes the multi-destination
        # rows drivable at all. A row with no colon is a single-destination item.
        if ": " in display:
            item_name, destination_name = display.split(": ", 1)
        else:
            item_name, destination_name = display, None

        out.append(Teleport(
            dest=dest,
            item_ids=item_ids,
            display=display,
            item_name=item_name.strip(),
            destination_name=destination_name.strip() if destination_name else None,
            duration=_parse_int(cols[5], 0),
            max_wilderness_level=_parse_int(cols[8]),
            consumable=cols[7].strip().upper() == "T",
        ))

    _cache[path] = out
    return out


def owned(teleports, item_ids):
    """The subset we could actually use, given every item id we hold or have banked."""
    have = set(item_ids or ())
    return [t for t in teleports if t.item_ids & have]


def choose(candidates, route_len, walk_len, wilderness_level=0,
           teleport_cost_tiles=TELEPORT_COST_TILES, min_saving_tiles=MIN_SAVING_TILES):
    """The teleport that best beats walking, or None to just walk.

    `route_len(dest)` answers the remaining walk in tiles from a landing spot, or None when
    there is no route from it -- landing somewhere we cannot then walk out of is strictly worse
    than never teleporting. `walk_len` is the cost of walking the whole way from here.

    Pure by design: the caller supplies the router, so this is testable without a map, a bridge
    or a client.
    """
    best = None
    best_cost = None

    for tp in candidates:
        # A teleport does not fire above its own wilderness level, so the click would be spent
        # for nothing. Krystilia's tasks are IN the wilderness, so this is the live case.
        if (tp.max_wilderness_level is not None
                and wilderness_level > tp.max_wilderness_level):
            continue
        if any(word in tp.display.lower() for word in PLAYER_SPECIFIC):
            continue

        remaining = route_len(tp.dest)
        if remaining is None:
            continue

        cost = remaining + teleport_cost_tiles
        if best_cost is None or cost < best_cost:
            best, best_cost = tp, cost

    if best is None:
        return None
    # Walking is the default; a teleport has to earn the trip by a real margin, not a rounding
    # error, or every short hop burns a tablet.
    if walk_len - best_cost < min_saving_tiles:
        return None
    return best


# ---------------------------------------------------------------------------------------------
# The transport GRAPH.
#
# USER 2026-09-07, correcting the single-hop model above: "if you'd ask shortest path the answer
# would have been take house tablet then use the ordnate jewlry box to ferox enclave". That route
# cannot be expressed as "teleport from anywhere to X" at all, and the reason is one column:
# teleportation_items.tsv has no ORIGIN, while teleportation_boxes.tsv and
# teleportation_portals_poh.tsv do. Those rows are EDGES -- usable only from a particular tile --
# so the answer comes from chaining them, which is Shortest Path's pathfinder, not its data. We
# had copied the data and not the algorithm.
#
# What is copied here, verbatim in behaviour, from PathfinderConfig:
#   - checkJewelleryBoxTier: cumulative tiers, and an ornate box DISABLES the mounted glory.
#   - varbitChecks: generic `id op value` requirements, ANDed. The house tablet's ten rows are
#     not special-cased anywhere -- 2187 (house location) and 4744 (inside/outside) just fall
#     out of the same machinery that gates diary capes and quest teleports.
#   - the POH config surface (usePoh / usePohFairyRing / useTeleportationPortalsPoh /
#     pohJewelleryBoxTier / usePohMountedItems), because the client CANNOT see inside your house:
#     Shortest Path asks the user, and so must we.
#
# STILL PURE: `plan` takes `route_len` as an argument, so the whole search is testable with no
# map, no bridge and no client.

MAX_HOPS = 2          # house tablet -> jewellery box is two. Three has no known use and the
                      # search cost grows with the branching factor, so it is bounded here.

# Per-table column layouts differ, so columns are keyed by header NAME. A fixed-index parser
# reads two of the three tables wrong -- boxes has no Varbits column where poh_portals does.
_TABLES = (
    ("item", "teleportation_items.tsv"),
    ("box", "teleportation_boxes.tsv"),
    ("poh_portal", "teleportation_portals_poh.tsv"),
)

_VAR_OPS = ("=", ">", "<")

_all_cache = {}


class Transport:
    """One edge. `origin is None` means "usable from anywhere" (an inventory teleport)."""

    __slots__ = ("origin", "dest", "item_ids", "varbits", "object_info", "display",
                 "item_name", "destination_name", "duration", "max_wilderness_level",
                 "consumable", "kind")

    def __init__(self, kind, origin, dest, item_ids, varbits, object_info, display,
                 item_name, destination_name, duration, max_wilderness_level, consumable):
        self.kind = kind
        self.origin = origin
        self.dest = dest
        self.item_ids = item_ids
        self.varbits = varbits
        self.object_info = object_info or ""
        self.display = display
        self.item_name = item_name
        self.destination_name = destination_name
        self.duration = duration
        self.max_wilderness_level = max_wilderness_level
        self.consumable = consumable

    def __repr__(self):
        where = "anywhere" if self.origin is None else self.origin
        return f"<{self.kind} {self.display} {where} -> {self.dest}>"


class PohConfig:
    """What is built in the player's house. The client cannot see inside a POH, so this is
    configuration, exactly as it is in Shortest Path -- see ShortestPathConfig's sectionPoh."""

    __slots__ = ("enabled", "box_tier", "portals", "fairy_ring", "spirit_tree",
                 "mounted_items", "obelisk")

    def __init__(self, enabled=True, box_tier="NONE", portals=False, fairy_ring=False,
                 spirit_tree=False, mounted_items=False, obelisk=False):
        self.enabled = enabled
        self.box_tier = (box_tier or "NONE").upper()
        self.portals = portals
        self.fairy_ring = fairy_ring
        self.spirit_tree = spirit_tree
        self.mounted_items = mounted_items
        self.obelisk = obelisk


_TIER_RANK = {"NONE": 0, "BASIC": 1, "FANCY": 2, "ORNATE": 3}


def _parse_varbits(field):
    """`2187=3;4744=0` -> [(2187, "=", 3), (4744, "=", 0)]. Semicolon is AND."""
    out = []
    for clause in (field or "").split(";"):
        clause = clause.strip()
        if not clause:
            continue
        for op in _VAR_OPS:
            if op in clause:
                left, _, right = clause.partition(op)
                try:
                    out.append((int(left.strip()), op, int(right.strip())))
                except ValueError:
                    pass
                break
    return tuple(out)


def _columns(header_line):
    return {name.strip(): i for i, name in enumerate(header_line.lstrip("#").split("\t"))}


def _cell(cols, row, name):
    i = cols.get(name)
    return row[i].strip() if i is not None and i < len(row) else ""


def load_all(directory=None):
    """Every transport edge across the vendored tables."""
    directory = directory or os.path.dirname(DATA_PATH)
    if directory in _all_cache:
        return _all_cache[directory]

    out = []
    for kind, filename in _TABLES:
        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        if not lines:
            continue
        cols = _columns(lines[0])
        if "Destination" not in cols:
            raise ValueError(f"{filename}: no Destination column -- upstream layout changed")

        for line in lines[1:]:
            if line.startswith("#") or not line.strip():
                continue
            row = line.split("\t")
            dest = _parse_dest(_cell(cols, row, "Destination"))
            if dest is None:
                continue
            origin = _parse_dest(_cell(cols, row, "Origin")) if "Origin" in cols else None
            item_ids = _parse_items(_cell(cols, row, "Items"))
            display = _cell(cols, row, "Display info")
            if ": " in display:
                item_name, destination_name = display.split(": ", 1)
            else:
                item_name, destination_name = display, None

            # An item teleport with no item cannot be owned, so it is not an edge we can take.
            # A box/portal edge legitimately needs no item -- the object is the requirement.
            if kind == "item" and not item_ids:
                continue

            out.append(Transport(
                kind=kind,
                origin=origin,
                dest=dest,
                item_ids=item_ids,
                varbits=_parse_varbits(_cell(cols, row, "Varbits")),
                object_info=_cell(cols, row, "menuOption menuTarget objectID"),
                display=display,
                item_name=item_name.strip(),
                destination_name=destination_name.strip() if destination_name else None,
                duration=_parse_int(_cell(cols, row, "Duration"), 0),
                max_wilderness_level=_parse_int(_cell(cols, row, "Wilderness level")),
                consumable=_cell(cols, row, "Consumable").upper() == "T",
            ))

    _all_cache[directory] = out
    return out


def varbits_satisfied(transport, varbit_values):
    """Shortest Path's varbitChecks. An UNKNOWN varbit fails the check rather than passing it:
    these gates exist to pick exactly one row out of ten (the house tablet), so guessing yes
    would make every house location simultaneously true."""
    for vid, op, want in transport.varbits:
        have = (varbit_values or {}).get(vid)
        if have is None:
            return False
        if op == "=" and have != want:
            return False
        if op == ">" and not have > want:
            return False
        if op == "<" and not have < want:
            return False
    return True


def poh_allows(transport, poh):
    """PathfinderConfig.checkJewelleryBoxTier, ported. Applies to POH-interior edges only."""
    if transport.kind not in ("box", "poh_portal"):
        return True
    if poh is None or not poh.enabled:
        return False

    if transport.kind == "poh_portal":
        return bool(poh.portals)

    info = transport.object_info or ""
    mounted_glory = "Amulet of Glory" in info
    mounted = (mounted_glory or "Xeric's Talisman" in info
               or "Digsite" in info or "Mythical cape" in info)
    if mounted:
        # An ornate box supersedes the mounted glory, so offering both would plan a route
        # through something you would never actually use. Straight from the reference.
        if mounted_glory and poh.box_tier == "ORNATE":
            return False
        return bool(poh.mounted_items)

    tier = _TIER_RANK.get(poh.box_tier, 0)
    if tier == 0:
        return False
    if "Basic Jewellery Box" in info:
        return True
    if "Fancy Jewellery Box" in info:
        return tier >= _TIER_RANK["FANCY"]
    if "Ornate Jewellery Box" in info:
        return tier >= _TIER_RANK["ORNATE"]
    return False


def usable(transport, owned_ids, varbit_values, poh, wilderness_level=0):
    """Everything that must hold before this edge can be taken."""
    if transport.item_ids and not (transport.item_ids & set(owned_ids or ())):
        return False
    if not varbits_satisfied(transport, varbit_values):
        return False
    if not poh_allows(transport, poh):
        return False
    if (transport.max_wilderness_level is not None
            and wilderness_level > transport.max_wilderness_level):
        return False
    return True


def plan(start, goal, owned_ids, route_len, varbit_values=None, poh=None,
         wilderness_level=0, teleport_cost_tiles=TELEPORT_COST_TILES,
         min_saving_tiles=MIN_SAVING_TILES, max_hops=None):
    """The cheapest chain of transports from `start` to `goal`, or None to just walk.

    `route_len(a, b)` is the walking distance in tiles between two tiles, or None when there is
    no walking route -- the caller supplies it, so this stays pure. `start`/`goal`/every
    origin and destination are (x, y, plane).

    None means "walk": either walking is already good enough, or nothing we own helps. The
    caller must not read it as "impossible" -- see `unreachable` for that question.
    """
    max_hops = MAX_HOPS if max_hops is None else max_hops
    edges = [t for t in load_all()
             if usable(t, owned_ids, varbit_values, poh, wilderness_level)]

    walk_direct = route_len(start, goal)
    best_cost = walk_direct if walk_direct is not None else None
    best_legs = None

    # Breadth-first over hops. The frontier is small (a few dozen usable edges), and each
    # expansion asks route_len only about tiles we have actually landed on.
    frontier = [(start, [], 0.0)]
    seen = {start[:2]: 0.0}

    for _ in range(max_hops):
        nxt = []
        for pos, legs, spent in frontier:
            for t in edges:
                # An origin-ful edge (a jewellery box) is only usable if we can WALK to its
                # origin from where we are standing. That single condition is what makes
                # "house tablet, then box" work and "box from anywhere" not.
                if t.origin is None:
                    approach = 0.0
                else:
                    if t.origin[2] != pos[2]:
                        continue
                    a = route_len(pos, t.origin)
                    if a is None:
                        continue
                    approach = a

                cost = spent + approach + teleport_cost_tiles
                key = t.dest[:2]
                if key in seen and seen[key] <= cost:
                    continue
                seen[key] = cost

                chain = legs + [t]
                remaining = route_len(t.dest, goal)
                if remaining is not None:
                    total = cost + remaining
                    if best_cost is None or total < best_cost:
                        best_cost, best_legs = total, chain
                nxt.append((t.dest, chain, cost))
        frontier = nxt
        if not frontier:
            break

    if best_legs is None:
        return None
    # Walking is the default; a chain has to beat it by a real margin, or every short hop
    # burns a tablet. When walking is impossible walk_direct is None and any chain wins.
    if walk_direct is not None and walk_direct - best_cost < min_saving_tiles:
        return None
    return best_legs


def unreachable(start, goal, owned_ids, route_len, **kw):
    """True when there is NEITHER a walking route NOR a usable chain -- i.e. staying put is the
    only option and the caller should say so rather than attempt a walk.

    `plan` returning None conflates "walking is better" with "nothing works"; at Ferox those
    demand opposite actions, so the two questions are separated here.
    """
    if route_len(start, goal) is not None:
        return False
    return plan(start, goal, owned_ids, route_len, **kw) is None
