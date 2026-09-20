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
#
# A SECOND transports source since 2026-09-20 (slayer sub-project 3, E-pre): Microbot's fork of
# the same table (vendor/microbot/transports.tsv, tools/vendor_microbot_tables.py, pinned commit in
# vendor/microbot/VENDORED.md) is newer and larger -- MEASURED 5731 rows against our 5168, 5043
# shared by (origin, dest, object id), 649 only there -- and has the Karuulm Slayer Dungeon, which
# ours lacks entirely. Its layout differs: the object cell is `Option;Target;id`, `Item IDs` are
# NUMERIC and every id in the cell is an ALTERNATIVE (its evaluator, PathfinderConfig
# .hasRequiredItems, is flatMap + anyMatch -- the first draft here read ';' as AND from the
# parser alone and would have demanded all seven Crafting Guild capes at once), and it adds
# `Currency` ("10 Coins"), `Varplayers` and `isMembers`. load_links() reads it through an adapter
# and APPENDS the rows whose key we do not already hold; every row of ours resolves exactly as
# before, and a Microbot row's object_info is normalised to our space form so every consumer
# (legs.trailing_int, crossfloor's keys, the logs) sees one format. The same physical edge can
# still appear twice when the two tables disagree on the object id (the Al Kharid toll gate is
# 44050 in ours, 2788 there) -- the executor finds the object by id, so the stale one fails
# cleanly and the other is taken. Item ids and currency FAIL CLOSED like every other requirement
# (`usable(owned_item_ids=, currency_held=)`); isMembers is recorded, not gated.
# Where BOTH tables hold a row and ours names its item symbolically (DUSTY_KEY=1 -- a NAME no
# inventory read can prove), the merged row BORROWS Microbot's numeric ids: 122 of our 190
# symbolic rows, MEASURED. Either proof passes. load_links(microbot=False) is left untouched.
#
# A door WITH a requirement (a key, a level, a quest, a toll) is a LINK, not a DOOR: the map
# only bakes plain doors open, and MEASURED the dusty-key gate is blocked on it.

import copy
import os
import re

from teleports import _cell, _columns, _parse_dest, _parse_int, _parse_varbits

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")

LINK = "link"      # changes plane, enters/leaves a dungeon, or crosses something walking cannot
DOOR = "door"      # "Open <x>" between adjacent same-plane tiles -- travel.py's job, not a route edge

SHORTEST_PATH = "shortest-path"   # Step.source: the Plugin Hub jar's table (vendor/*.tsv)
MICROBOT = "microbot"             # Step.source: Microbot's fork (vendor/microbot/*.tsv)
MICROBOT_SUBDIR = "microbot"

_OBJECT_COLUMN = "menuOption menuTarget objectID"
_DOOR_MAX_TILES = 2
_SKILL = re.compile(r"(\d+)\s+([A-Za-z ]+)")
_CURRENCY = re.compile(r"(\d+)\s+(.+)")
_UNSATISFIABLE_ITEM = -1      # a non-empty Item IDs cell with nothing numeric in it

# Column names per source. The two spell the varplayers column differently; only Microbot has
# numeric item ids and a currency column, and our jar copy's Items are symbolic names.
_LAYOUT = {
    SHORTEST_PATH: {"items": "Items", "item_ids": None, "currency": None, "varplayers": "VarPlayers",
                    "required": ("Origin", "Destination", _OBJECT_COLUMN)},
    MICROBOT: {"items": None, "item_ids": "Item IDs", "currency": "Currency", "varplayers": "Varplayers",
               "required": ("Origin", "Destination", _OBJECT_COLUMN, "Item IDs", "Currency", "Varplayers")},
}

_cache = {}


class Step:
    """One transport row: stand on `origin`, take `option` on the object, arrive at `dest`."""

    __slots__ = ("origin", "dest", "option", "target", "object_id", "object_info", "skills",
                 "items", "quests", "varbits", "varplayers", "duration", "display", "kind",
                 "item_ids", "currency", "source")

    def __init__(self, origin, dest, object_info, skills, items, quests, varbits, varplayers,
                 duration, display, *, item_ids=(), currency=None, source=SHORTEST_PATH):
        self.origin = origin
        self.dest = dest
        self.object_info = normalise_object_info(object_info)
        self.item_ids = item_ids          # (1590,) -- ANY one suffices; Microbot's, or borrowed
        self.currency = currency          # (amount, name) -- Microbot rows only
        self.source = source
        self.option, self.target, self.object_id = split_object_info(self.object_info)
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
        if (self.skills or self.items or self.item_ids or self.quests or self.varbits
                or self.varplayers or self.currency):
            return False          # a gate, not a plain door: the map may block it
        if self.origin[2] != self.dest[2]:
            return False
        return max(abs(self.origin[0] - self.dest[0]), abs(self.origin[1] - self.dest[1])) <= _DOOR_MAX_TILES

    @property
    def enters_wilderness_ditch(self):
        return self.target == "Wilderness Ditch"

    def __repr__(self):
        return f"Step({self.origin} -> {self.dest}, {self.object_info!r})"


def normalise_object_info(text):
    """Microbot's `Enter;Cave Entrance;3759` -> our `Enter Cave Entrance 3759`; ours unchanged.
    Safe because no Microbot option contains whitespace (pinned by test against the data)."""
    text = (text or "").strip()
    if ";" in text:
        return " ".join(part.strip() for part in text.split(";") if part.strip())
    return text


def parse_item_ids(field):
    """`1 2;3` -> (1, 2, 3): every id is an ALTERNATIVE, whichever separator sits between them
    (Microbot evaluates the cell with flatMap + anyMatch). A non-empty cell with nothing numeric
    in it becomes an id nobody owns, so it fails closed instead of reading as 'no requirement'."""
    text = (field or "").strip()
    out = []
    for token in text.replace(";", " ").split():
        try:
            out.append(int(token))
        except ValueError:
            pass
    if text and not out:
        return (_UNSATISFIABLE_ITEM,)
    return tuple(out)


def parse_currency(field):
    """`10 Coins` -> (10, "Coins"); empty -> None; anything else -> (None, text), unsatisfiable."""
    field = (field or "").strip()
    if not field:
        return None
    m = _CURRENCY.fullmatch(field)
    return (int(m.group(1)), m.group(2).strip()) if m else (None, field)


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


def _load_steps(filename, directory, source=SHORTEST_PATH):
    key = (filename, directory or VENDOR_DIR, source)
    if key in _cache:
        return _cache[key]
    layout = _LAYOUT[source]
    cols, rows = _read_table(filename, directory, layout["required"])
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
            items=_cell(cols, row, layout["items"]) if layout["items"] else "",
            quests=_parse_quests(_cell(cols, row, "Quests")),
            varbits=_parse_varbits(_cell(cols, row, "Varbits")),
            varplayers=_parse_varbits(_cell(cols, row, layout["varplayers"])),
            duration=_parse_int(_cell(cols, row, "Duration"), 0),
            display=_cell(cols, row, "Display info"),
            item_ids=parse_item_ids(_cell(cols, row, layout["item_ids"])) if layout["item_ids"] else (),
            currency=parse_currency(_cell(cols, row, layout["currency"])) if layout["currency"] else None,
            source=source,
        ))
    _cache[key] = steps
    return steps


def load_microbot_links(directory=None):
    """Microbot's transports.tsv alone (vendor/microbot/), every row, through the adapter."""
    return _load_steps("transports.tsv", os.path.join(directory or VENDOR_DIR, MICROBOT_SUBDIR), MICROBOT)


def _merge(ours, theirs):
    """Ours first and never reordered; a row of ours that names its item symbolically borrows the
    numeric ids of Microbot's row for the same key (a COPY -- the jar-only list stays as it was);
    then every Microbot row whose key we do not hold."""
    key = lambda s: (s.origin, s.dest, s.object_id)
    ids_by_key = {}
    for step in theirs:
        if step.item_ids:
            ids_by_key.setdefault(key(step), step.item_ids)
    merged = []
    for step in ours:
        ids = ids_by_key.get(key(step)) if step.items and not step.item_ids else None
        if ids:
            step = copy.copy(step)
            step.item_ids = ids
        merged.append(step)
    held = {key(s) for s in ours}
    return merged + [s for s in theirs if key(s) not in held]


def load_links(directory=None, include_doors=True, microbot=True):
    """transports.tsv: ladders, stairs, cave mouths, trapdoors, gates, ditches, ... -- our jar
    copy first, then every Microbot row whose (origin, dest, object id) we do not already hold.
    `microbot=False` is the jar table alone, exactly as before 2026-09-20."""
    steps = _load_steps("transports.tsv", directory)
    if microbot:
        key = ("merged", directory or VENDOR_DIR)
        if key not in _cache:
            _cache[key] = _merge(steps, load_microbot_links(directory))
        steps = _cache[key]
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


def _item_ids_met(item_ids, owned_item_ids):
    if owned_item_ids is None:
        return False
    owned = set(owned_item_ids)
    return any(i in owned for i in item_ids)


def _currency_met(currency, currency_held):
    if currency is None:
        return True
    amount, name = currency
    if amount is None or currency_held is None:
        return False
    return currency_held.get(name, 0) >= amount


def usable(step, skill_levels=None, completed_quests=None, varbit_values=None,
           varplayer_values=None, owned_item_names=None, owned_item_ids=None,
           currency_held=None) -> bool:
    """True only when EVERY requirement on the row is proven. Unknown means no.
    `owned_item_ids` (a set of ids) and `currency_held` ({"Coins": 10}) answer the numeric
    requirements. A row carrying BOTH a symbolic Items clause and numeric ids (a merged row that
    borrowed them) passes on either proof; a row with one kind needs that kind proven."""
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
    if not _currency_met(step.currency, currency_held):
        return False
    if step.items or step.item_ids:
        by_name = bool(step.items) and _items_met(step.items, owned_item_names)
        by_id = bool(step.item_ids) and _item_ids_met(step.item_ids, owned_item_ids)
        return by_name or by_id
    return True


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
