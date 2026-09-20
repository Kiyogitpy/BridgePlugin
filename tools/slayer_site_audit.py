"""Check every site in tools/slayer_route_report.py against the local spawn database.

    python tools/slayer_site_audit.py [path/to/wiki.sqlite]

Written 2026-09-20 after the black demons site turned out to be a transcription error ~50 tiles
north of every spawn: crossfloor answered NO ROUTE, the search got the blame, and the user had to
point out that the obstacle pipe was right there. A site is only as good as its coordinate, so
before trusting a NO ROUTE -- and before a catalog entry inherits a research coordinate -- check
that the monster actually spawns near it, on that plane.

Reads `spawns` from the osrs-wiki MCP server's database (the path in .mcp.json), read-only. The
database is NOT in git (data/decompiled/ is local), so this is a tool, not a test. Exit code 1
when any site is further than MAX_TILES from every spawn of its monster on its own plane.
"""
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

from slayer_route_report import SITES  # noqa: E402

MAX_TILES = 20
DEFAULT_DB = os.path.join("data", "decompiled", "osrs-wiki-cache-utils-main", "wiki.sqlite")

# report label -> exact NPC names in the spawn table (case-insensitive). A label missing here is
# reported as UNMAPPED rather than skipped, so a new site cannot dodge the audit silently.
NPC_NAMES = {
    "Aberrant spectres": ["Aberrant spectre"],
    "Abyssal demons": ["Abyssal demon"],
    "Ankou": ["Ankou"],
    "Basilisks": ["Basilisk"],
    "Black demons": ["Black demon"],
    "Bloodveld": ["Bloodveld"],
    "Cave horrors": ["Cave horror"],
    "Dagannoth": ["Dagannoth"],
    "Dust devils": ["Dust devil"],
    "Elves": ["Elf Warrior", "Elf Archer"],
    "Fire giants": ["Fire giant"],
    "Gargoyles": ["Gargoyle"],
    "Gryphons": ["Gryphon"],
    "Hellhounds": ["Hellhound"],
    "Kalphite": ["Kalphite Worker", "Kalphite Soldier"],
    "Kurask": ["Kurask"],
    "Mutated zygomites": ["Zygomite", "Mutated zygomite"],
    "Nechryael": ["Nechryael"],
    "Suqahs": ["Suqah"],
    "Trolls": ["Mountain troll"],
    "Waterfiends": ["Waterfiend"],
    "Wyrms": ["Wyrm"],
    "Banshees (Aya)": ["Banshee"],
    "Crawling hands (Aya)": ["Crawling Hand"],
    "Cave crawlers (Aya)": ["Cave crawler"],
    "Ghosts (Aya)": ["Ghost"],
    "Zombies / Birds / Cows (Aya)": ["Undead cow", "Undead chicken"],
    "Minotaurs (Aya)": ["Minotaur"],
    "Scorpions (Aya)": ["Scorpion"],
}


def find_db(argument):
    candidates = [argument] if argument else []
    candidates.append(os.path.join(REPO, DEFAULT_DB))
    # A git worktree has no data/decompiled of its own; the main checkout sits three levels up.
    candidates.append(os.path.join(REPO, "..", "..", "..", DEFAULT_DB))
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    raise SystemExit("wiki.sqlite not found -- pass its path (see the osrs-wiki entry in .mcp.json)")


def main():
    path = find_db(sys.argv[1] if len(sys.argv) > 1 else None)
    db = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
    print(f"spawns from {path}")
    print(f"{'task':30} {'site':20} {'nearest spawn':20} {'dist':>5} {'<=30':>5}")
    bad = 0
    for task, _, _, site in SITES:
        names = NPC_NAMES.get(task)
        if not names:
            print(f"{task:30} {str(site):20} UNMAPPED -- add it to NPC_NAMES")
            bad += 1
            continue
        rows = []
        for name in names:
            rows += db.execute("select x, y, plane from spawns where entity_type = 'npc' "
                               "and lower(name) = lower(?) and x is not null", (name,)).fetchall()
        on_plane = [(max(abs(x - site[0]), abs(y - site[1])), int(x), int(y)) for x, y, p in rows if p == site[2]]
        if not on_plane:
            print(f"{task:30} {str(site):20} NO SPAWN of {names} on plane {site[2]}  <-- CHECK")
            bad += 1
            continue
        dist, x, y = min(on_plane)
        near = sum(1 for d, _, _ in on_plane if d <= 30)
        flag = "" if dist <= MAX_TILES else "  <-- CHECK"
        bad += 1 if flag else 0
        print(f"{task:30} {str(site):20} {str((x, y)):20} {int(dist):5} {near:5}{flag}")
    print("all sites sit on their monster" if not bad else f"{bad} site(s) to check")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
