# transports.py -- Shortest Path's ladders, stairs, cave entrances, agility shortcuts, fairy
# rings and banks, as data. PURE: no HTTP, no client state, no execution -- the same contract as
# teleports.py, which loads the three teleport tables from the same jar.
#
# Added 2026-09-19 for the slayer task loop (spec §8.1). Every walking primitive we have
# (worldmap.find_path, travel.walk_to, travel.route_to) stays on ONE plane, and the first design
# filled that gap with a hand-written entrance step per slayer site. The installed Shortest Path
# plugin already ships the answer: transports.tsv is 5,168 rows of
#     Origin | Destination | menuOption menuTarget objectID | Skills | Items | Quests | Varbits |
#     VarPlayers | Duration | Display info
# MEASURED against the jar: the Stronghold Slayer Cave, the Catacombs statue, the Slayer Tower
# basement ladder (which lands on plane 3, not 0) and the Taverley ladder are all in it.
#
# What this module deliberately does NOT do: search a route or click anything. The route search
# that combines worldmap walking with these edges, and the one generic "walk to Origin, click
# the object, confirm the landing" executor, are the next piece of work (spec §8.1).
#
# Three things about the data worth knowing before consuming it:
# - ~350 rows are plain DOORS ("Open Door 9398", adjacent tiles, same plane). worldmap bakes doors
#   open and travel.open_door_toward opens the shut one on the route, so a route search wants
#   them left out: kind == DOOR, load_links(include_doors=False).
# - ~740 rows are the WILDERNESS DITCH. Flagged (enters_wilderness_ditch) so a search can refuse.
# - Requirements FAIL CLOSED, the rule teleports.varbits_satisfied already follows: an unread
#   varbit, an unknown quest state, unproven levels or an Items clause we cannot check all mean
#   "not usable". Items are SYMBOLIC names upstream ("MACHETE=1", "SHANTAY_PASS=1|COINS=5"), not
#   ids, so they only pass when the caller supplies owned names in that same vocabulary.
#
# The header of every table is asserted by column NAME (layouts differ between Shortest Path's own
# files -- see teleports.py). Never hand-edit the TSVs: tools/extract_shortest_path_tables.py
# re-extracts them from a newer jar, then diff.

import os
import re

from teleports import _cell, _columns, _parse_dest, _parse_int, _parse_varbits

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")

LINK = "link"      # changes plane, enters/leaves a dungeon, or crosses something walking cannot
DOOR = "door"      # "Open <x>" between adjacent same-plane tiles -- travel.py's job, not a route edge

_OBJECT_COLUMN = "menuOption menuTarget objectID"
_DOOR_MAX_TILES = 2
_SKILL = re.compile(r"(\d+)\s+([A-Za-z ]+)")

_cache = {}


class Step:
    """One transport row: stand on `origin`, take `option` on the object, arrive at `dest`."""

    __slots__ = ("origin", "dest", "option", "target", "object_id", "object_info", "skills",
                 "items", "quests", "varbits", "varplayers", "duration", "display", "kind")

    def __init__(self, origin, dest, object_info, skills, items, quests, varbits, varplayers,
                 duration, display):
        self.origin = origin
        self.dest = dest
        self.object_info = object_info
        self.option, self.target, self.object_id = split_object_info(object_info)
        self.skills = skills
        self.items = items
        self.quests = quests
        self.varbits = varbits
        self.varplayers = varplayers
        self.duration = duration
        self.display = display
        self.kind = DOOR if self._is_door() else LINK

    def _is_door(self):
        if self.option != "Open" or self.origin is None or self.dest is None:
            return False
        if self.origin[2] != self.dest[2]:
            return False
        return max(abs(self.origin[0] - self.dest[0]), abs(self.origin[1] - self.dest[1])) <= _DOOR_MAX_TILES

    @property
    def enters_wilderness_ditch(self):
        return self.target == "Wilderness Ditch"

    def __repr__(self):
        return f"Step({self.origin} -> {self.dest}, {self.object_info!r})"


def split_object_info(text):
    """`Climb-up Vine ladder 28894` -> ("Climb-up", "Vine ladder", 28894).

    The option is the FIRST token (upstream hyphenates multi-word options: Climb-down,
    Squeeze-through, Pass-Through), the object id the trailing integer, the target whatever is
    between. 14 rows carry no id at all ("Climb-up Staircase") -- object_id stays None there
    rather than being guessed, and an executor has to find the object by name at the tile.
    """
    tokens = (text or "").split()
    if not tokens:
        return None, None, None
    object_id = None
    if len(tokens) > 1 and re.fullmatch(r"-?\d+", tokens[-1]):
        object_id = int(tokens[-1])
        tokens = tokens[:-1]
    return tokens[0], (" ".join(tokens[1:]) or None), object_id


def _parse_skills(field):
    """`60 Agility` / `70 Strength;70 Agility` -> {"Agility": 60}."""
    out = {}
    for clause in (field or "").split(";"):
        m = _SKILL.fullmatch(clause.strip())
        if m:
            out[m.group(2).strip()] = int(m.group(1))
    return out


def _parse_quests(field):
    return tuple(q.strip() for q in (field or "").split(";") if q.strip())


def _read_table(filename, directory, required):
    directory = directory or VENDOR_DIR
    path = os.path.join(directory, filename)
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    if not lines:
        raise ValueError(f"{filename}: empty table")
    cols = _columns(lines[0])
    for name in required:
        if name not in cols:
            raise ValueError(f"{filename}: no {name} column -- upstream layout changed")
    rows = [line.split("\t") for line in lines[1:] if line.strip() and not line.startswith("#")]
    return cols, rows


def _load_steps(filename, directory):
    key = (filename, directory or VENDOR_DIR)
    if key in _cache:
        return _cache[key]
    cols, rows = _read_table(filename, directory, ("Origin", "Destination", _OBJECT_COLUMN))
    steps = []
    for row in rows:
        origin = _parse_dest(_cell(cols, row, "Origin"))
        dest = _parse_dest(_cell(cols, row, "Destination"))
        if origin is None or dest is None:
            continue
        steps.append(Step(
            origin=origin,
            dest=dest,
            object_info=_cell(cols, row, _OBJECT_COLUMN),
            skills=_parse_skills(_cell(cols, row, "Skills")),
            items=_cell(cols, row, "Items"),
            quests=_parse_quests(_cell(cols, row, "Quests")),
            varbits=_parse_varbits(_cell(cols, row, "Varbits")),
            varplayers=_parse_varbits(_cell(cols, row, "VarPlayers")),
            duration=_parse_int(_cell(cols, row, "Duration"), 0),
            display=_cell(cols, row, "Display info"),
        ))
    _cache[key] = steps
    return steps


def load_links(directory=None, include_doors=True):
    """transports.tsv: ladders, stairs, cave mouths, trapdoors, gates, ditches, ..."""
    steps = _load_steps("transports.tsv", directory)
    return steps if include_doors else [s for s in steps if s.kind != DOOR]


def load_agility_shortcuts(directory=None):
    """agility_shortcuts.tsv: same shape, every row carries its Agility level in `skills`."""
    return _load_steps("agility_shortcuts.tsv", directory)


def _vars_met(clauses, values):
    for vid, op, want in clauses:
        have = (values or {}).get(vid)
        if have is None:
            return False
        if op == "=" and have != want:
            return False
        if op == ">" and not have > want:
            return False
        if op == "<" and not have < want:
            return False
    return True


def _items_met(field, owned_item_names):
    """`A=1&B=1` is AND, `A=1|B=5` is OR. Quantities are not checked (the caller's bank memory
    answers that); an empty `owned_item_names` fails any non-empty clause."""
    if not field:
        return True
    owned = {n.upper() for n in (owned_item_names or ())}
    for conjunct in field.split("&"):
        alternatives = [alt.split("=")[0].strip().upper() for alt in conjunct.split("|") if alt.strip()]
        if not any(alt in owned for alt in alternatives):
            return False
    return True


def usable(step, skill_levels=None, completed_quests=None, varbit_values=None,
           varplayer_values=None, owned_item_names=None) -> bool:
    """True only when EVERY requirement on the row is proven. Unknown means no."""
    if step.skills:
        if skill_levels is None:
            return False
        if any(skill_levels.get(name, 0) < level for name, level in step.skills.items()):
            return False
    if step.quests:
        if completed_quests is None or any(q not in completed_quests for q in step.quests):
            return False
    if not _vars_met(step.varbits, varbit_values):
        return False
    if not _vars_met(step.varplayers, varplayer_values):
        return False
    return _items_met(step.items, owned_item_names)


# ---------------------------------------------------------------- fairy rings

class Requirements:
    __slots__ = ("quests", "varbits")

    def __init__(self, quests, varbits):
        self.quests = quests
        self.varbits = varbits


class FairyRings:
    """fairy_rings.tsv holds TWO row types in one file: ring ENTRANCES (Origin + the ring object,
    no destination -- you can dial from here) and code EXITS (Destination + the code in
    `Display info`, e.g. "A J Q").

    ONE exit row is not a code but a SEQUENCE: "A I R - D L R - D J Q - A J S" is the Fairy
    Queen's hideout, reached by dialling those four codes one after another. AIR and DLR are
    ordinary rings of their own, so a sequence row must never be read as aliases -- the first
    draft of this parser did, and it silently re-pointed AIR at the hideout (caught by test).
    Sequences live in `sequences`, keyed by the tuple of codes."""

    def __init__(self, entrances, exits, requirements, sequences):
        self.entrances = entrances
        self._exits = exits
        self._requirements = requirements
        self.sequences = sequences

    @staticmethod
    def _norm(code):
        return re.sub(r"[^A-Za-z]", "", code or "").upper()

    def exit_for(self, code):
        return self._exits.get(self._norm(code))

    def requirements_for(self, code):
        return self._requirements.get(self._norm(code))

    def codes(self):
        return sorted(self._exits)


def load_fairy_rings(directory=None):
    key = ("fairy_rings.tsv", directory or VENDOR_DIR)
    if key in _cache:
        return _cache[key]
    cols, rows = _read_table("fairy_rings.tsv", directory, ("Origin", "Destination", "Display info"))
    entrances, exits, requirements, sequences = [], {}, {}, {}
    for row in rows:
        origin = _parse_dest(_cell(cols, row, "Origin"))
        dest = _parse_dest(_cell(cols, row, "Destination"))
        quests = _parse_quests(_cell(cols, row, "Quests"))
        varbits = _parse_varbits(_cell(cols, row, "Varbits"))
        if origin is not None:
            entrances.append(Step(origin, None, _cell(cols, row, _OBJECT_COLUMN), {}, "", quests,
                                  varbits, (), _parse_int(_cell(cols, row, "Duration"), 0), ""))
        elif dest is not None:
            codes = [FairyRings._norm(c) for c in _cell(cols, row, "Display info").split(" - ")]
            codes = [c for c in codes if c]
            if len(codes) == 1:
                exits[codes[0]] = dest
                requirements[codes[0]] = Requirements(quests, varbits)
            elif codes:
                sequences[tuple(codes)] = dest
    rings = FairyRings(entrances, exits, requirements, sequences)
    _cache[key] = rings
    return rings


# ---------------------------------------------------------------- banks

class Bank:
    __slots__ = ("tile", "info", "skills", "quests", "varbits", "varplayers")

    def __init__(self, tile, info, skills, quests, varbits, varplayers):
        self.tile = tile
        self.info = info
        self.skills = skills
        self.quests = quests
        self.varbits = varbits
        self.varplayers = varplayers

    def __repr__(self):
        return f"Bank({self.info!r} {self.tile})"


def load_banks(directory=None):
    """bank.tsv: one row per bank TILE (a bank with three booths is three rows)."""
    key = ("bank.tsv", directory or VENDOR_DIR)
    if key in _cache:
        return _cache[key]
    cols, rows = _read_table("bank.tsv", directory, ("Destination", "Info"))
    banks = []
    for row in rows:
        tile = _parse_dest(_cell(cols, row, "Destination"))
        if tile is None:
            continue
        banks.append(Bank(tile, _cell(cols, row, "Info"), _parse_skills(_cell(cols, row, "Skills")),
                          _parse_quests(_cell(cols, row, "Quests")),
                          _parse_varbits(_cell(cols, row, "Varbits")),
                          _parse_varbits(_cell(cols, row, "VarPlayers"))))
    _cache[key] = banks
    return banks


def nearest_bank(x, y, plane, directory=None):
    """The closest bank tile ON THIS PLANE by straight-line (Chebyshev) distance, or None.
    Geometry only: whether you can WALK there is the router's question, not this table's.
    MEASURED 2026-09-19: from the Stronghold Slayer Cave landing (2431,3422,0) this answers
    Barbarian Outpost, 150 tiles away, because the Gnome Stronghold bank beside Nieve is on
    plane 1. A route search over load_links() is what finds that one; this cannot."""
    best, best_d = None, None
    for bank in load_banks(directory):
        if bank.tile[2] != plane:
            continue
        d = max(abs(bank.tile[0] - x), abs(bank.tile[1] - y))
        if best_d is None or d < best_d:
            best, best_d = bank, d
    return best
