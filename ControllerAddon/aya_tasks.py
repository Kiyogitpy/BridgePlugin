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
    "monkeys": {
        "location": "Karamja, near the volcano",
        "target": (2873, 3148, 0),
        "areas": ((2863, 3138, 21, 21, 0),),
        "teleports": ("Ornate Jewellery Box: Karamja", "Glory amulet: Karamja"),
        "info": None,
        "npc_ids": (),
        "note": "user override of Kruk's Dungeon. UNVERIFIED: the tile is a wiki pin (4 spawns) and "
                "the plugin's npc id (7118) is the Kruk's Dungeon monkey, so targets fall back to the "
                "name 'Monkey' until the first visit confirms the id.",
    },
    "minotaurs": {
        "teleports": ("Ornate Jewellery Box: Cooking Guild", "Skull sceptre: Invoke"),
        "note": "user override: the ornate jewellery box's Cooking Guild teleport, then walk to the "
                "Stronghold of Security entrance in Barbarian Village.",
    },
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
