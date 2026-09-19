"""Convert the Turael Skipping registry dump into the JSON the controller loads.

    python tools/turael_registry_to_json.py

Input : docs/slayer-sites/aya-turael-skipping-registry.txt -- a reflective dump of the plugin's
        own SlayerTaskRegistry.TURAEL_TASKS (see DumpTuraelSkippingRegistry.java beside it).
Output: ControllerAddon/vendor/turael_skipping_tasks.json
The plugin's data, not a reconstruction. Never hand-edit the JSON: re-dump, then re-run this.
"""
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "docs", "slayer-sites", "aya-turael-skipping-registry.txt")
OUT = os.path.join(REPO, "ControllerAddon", "vendor", "turael_skipping_tasks.json")

_AREA = re.compile(r"\[x=(-?\d+),y=(-?\d+),w=(\d+),h=(\d+),p=(\d+)\]")


def _point(text):
    text = text.strip()
    if not text or text == "null":
        return None
    return [int(v) for v in text.split(",")]


def parse(text):
    tasks = {}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## key="):
            key, _, name = line[len("## key="):].partition(" | task=")
            current = {"key": key.strip(), "name": name.strip(), "npcIds": [], "info": None,
                       "target": None, "worldMap": [], "locations": []}
            tasks[current["key"]] = current
        elif current is None:
            continue
        elif line.startswith("npcIds:"):
            current["npcIds"] = [int(v) for v in re.findall(r"-?\d+", line)]
        elif line.startswith("info:"):
            value = line[len("info:"):].strip()
            current["info"] = None if value == "null" else value
        elif line.startswith("shortestPath:"):
            current["target"] = _point(line[len("shortestPath:"):])
        elif line.startswith("worldMap:"):
            current["worldMap"] = [_point(p) for p in line[len("worldMap:"):].split() if p.strip()]
        elif line.strip().startswith("loc:"):
            name, _, rest = line.strip()[len("loc:"):].partition(" | teleports=")
            teleports_text, _, areas_text = rest.partition(" | areas=")
            teleports = [t.strip() for t in teleports_text.strip().strip("[]").split(", ") if t.strip()]
            areas = [[int(v) for v in m.groups()] for m in _AREA.finditer(areas_text)]
            current["locations"].append({"name": name.strip(), "teleports": teleports, "areas": areas})
    return tasks


def main():
    with open(SRC, encoding="utf-8-sig") as handle:
        tasks = parse(handle.read())
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"source": "turael-skipping Plugin Hub jar, SlayerTaskRegistry.TURAEL_TASKS",
                   "tasks": tasks}, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"{len(tasks)} tasks -> {OUT}")


if __name__ == "__main__":
    main()
