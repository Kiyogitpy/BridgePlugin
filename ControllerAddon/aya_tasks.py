# aya_tasks.py -- where and what to kill for each task Aya (= Turael) assigns. PURE data.
# Added 2026-09-19 (slayer spec §6.1).
#
# The source of truth is the Turael Skipping Plugin Hub plugin the user runs, vendored as
# vendor/turael_skipping_tasks.json: a reflective dump of its own SlayerTaskRegistry (its data,
# not a reconstruction -- see docs/slayer-sites/). Per task: the NPC IDS that count, a target
# tile, the kill area as WorldArea rectangles (x, y, width, height, plane), a gear note, and the
# plugin's teleports in its own preference order. The orchestrator tries those teleports in
# order and takes the first the account owns.
#
# The user's overrides (docs/slayer-sites/decisions.md) are applied here and nowhere else:
#   dogs      -- BLOCKED at Aya, so never assigned; kept only so the name resolves.
#   monkeys   -- killed on Karamja ("closer"), not in Kruk's Dungeon.
#   minotaurs -- same site, reached by the ornate jewellery box to the Cooking Guild.

import json
import os
from collections import namedtuple

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "turael_skipping_tasks.json")

FIGHT = "FIGHT"
BLOCKED = "BLOCKED"

AyaTask = namedtuple("AyaTask", ("key", "name", "npc_ids", "target", "location", "teleports",
                                 "areas", "info", "disposition", "note"))

_OVERRIDES = {
    "dogs": {"disposition": BLOCKED, "note": "blocked at Aya by the user; never assigned"},
    # USER 2026-09-20: "Aya blocklist atm is: Dwarves, Cave Crawlers, Lizards, Skeletons,
    # Kalphites". A blocked task is never assigned; if one turns up anyway the routine STOPS rather
    # than travel (slayer_tasks.disposition). Their sites and teleports stay in the data so the
    # name resolves and so unblocking one is a one-line change. Dogs are not on that list but were
    # recorded as blocked on 2026-09-19 and stay so until the user says otherwise -- a task wrongly
    # marked blocked costs a stop, never a bad trip. NOTE kalphite is ALSO one of Kuradal's 22
    # FIGHT tasks: slayer_tasks reads Kuradal's table first, so it is still fought for her.
    "dwarves": {"disposition": BLOCKED, "note": "blocked at Aya by the user (2026-09-20); never assigned"},
    "cave crawlers": {"disposition": BLOCKED, "note": "blocked at Aya by the user (2026-09-20); never assigned"},
    "lizards": {"disposition": BLOCKED, "note": "blocked at Aya by the user (2026-09-20); never assigned"},
    "skeletons": {"disposition": BLOCKED, "note": "blocked at Aya by the user (2026-09-20); never assigned. "
                  "(Its kill area is the Digsite Dungeon, which no transport table can reach.)"},
    "kalphite": {"disposition": BLOCKED, "note": "blocked at Aya by the user (2026-09-20); never assigned "
                 "BY AYA. Kuradal still assigns it and it is fought for her."},
    "monkeys": {
        "location": "Karamja, near the volcano",
        "target": (2873, 3148, 0),
        "areas": ((2863, 3138, 21, 27, 0),),
        "teleports": ("Ornate Jewellery Box: Karamja", "Glory amulet: Karamja"),
        "info": None,
        "npc_ids": (2848,),
        "note": "user override of Kruk's Dungeon. CHECKED 2026-09-20 against the wiki spawn table: "
                "Monkey id 2848 stands on exactly this tile, with two more at (2877,3154) and "
                "(2879,3161) -- the area was grown 6 tiles north to cover the third. The plugin's own "
                "id (7118) is the Kruk's Dungeon monkey. Still UNVERIFIED LIVE.",
    },
    "minotaurs": {
        "teleports": ("Ornate Jewellery Box: Cooking Guild", "Skull sceptre: Invoke"),
        "note": "user override: the ornate jewellery box's Cooking Guild teleport, then walk to the "
                "Stronghold of Security entrance in Barbarian Village.",
    },
}

# Teleports APPENDED after the plugin's own -- NOT a user override of anything: the plugin's
# order and the site are untouched, these only give the planner something a leg can run when the
# plugin's picks have none. Each is proven by crossfloor from its landing before it is offered.
_EXTRA_TELEPORTS = {
    # White Wolf Tunnel pub. The plugin lists a construction cape and a re-directed house tablet.
    # MEASURED 2026-09-20: 59 tiles from the Taverley tablet's landing, 137 from the house box's
    # Burthorpe, both through 'Climb-down Stairs 57', gated on varplayer 11 == 5 (Fishing Contest).
    "dwarves": ("Taverley teleport: Taverley", "Ornate Jewellery Box: Burthorpe"),
}

_cache = {}


def _build(raw):
    location = (raw.get("locations") or [{}])[0]
    fields = {
        "key": raw["key"],
        "name": raw["name"],
        "npc_ids": tuple(raw.get("npcIds") or ()),
        "target": tuple(raw["target"]) if raw.get("target") else None,
        "location": location.get("name"),
        "teleports": tuple(location.get("teleports") or ()),
        "areas": tuple(tuple(a) for a in location.get("areas") or ()),
        "info": raw.get("info"),
        "disposition": FIGHT,
        "note": "",
    }
    fields.update(_OVERRIDES.get(raw["key"], {}))
    extra = tuple(t for t in _EXTRA_TELEPORTS.get(raw["key"], ()) if t not in fields["teleports"])
    fields["teleports"] = tuple(fields["teleports"]) + extra
    return AyaTask(**fields)


def load(path=None) -> dict:
    path = path or DATA_PATH
    if path not in _cache:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)["tasks"]
        _cache[path] = {key: _build(task) for key, task in raw.items()}
    return _cache[path]


def get(task_name, path=None):
    """Case-insensitive; accepts the game's name, the registry's key, and a singular/plural slip
    ("cave slime" / "cave slimes", "crawling hand" / "crawling hands")."""
    tasks = load(path)
    key = (task_name or "").strip().lower()
    for candidate in (key, key + "s", key[:-1] if key.endswith("s") else key):
        if candidate in tasks:
            return tasks[candidate]
    return None


def contains(task, x, y, plane) -> bool:
    """Is (x, y, plane) inside the task's kill area? The 'am I at the site' test."""
    return any(ax <= x < ax + w and ay <= y < ay + h and plane == p for ax, ay, w, h, p in task.areas)
