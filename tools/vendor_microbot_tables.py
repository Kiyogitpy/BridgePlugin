"""Copy Microbot's fork of the Shortest Path tables out of a sparse clone, byte for byte.

    python tools/vendor_microbot_tables.py <clone-root>            # copy + print the manifest
    python tools/vendor_microbot_tables.py <clone-root> --check    # only report differences

Microbot (github.com/chsami/Microbot, BSD-2) carries a NEWER and LARGER transports.tsv than the
Plugin Hub jar we extract with tools/extract_shortest_path_tables.py -- it has the Karuulm Slayer
Dungeon, a dusty-key gate row, blocked edges, dangerous tiles and tile restrictions. Its column
layout differs, so ControllerAddon/transports.py reads it through an adapter rather than as a
drop-in replacement. See ControllerAddon/vendor/microbot/VENDORED.md. Never hand-edit the copies:
re-run this against a newer clone and diff instead.

A sparse clone is enough (set core.longpaths on Windows):
    git -c core.longpaths=true clone --filter=blob:none --sparse --no-checkout https://github.com/chsami/Microbot microbot
    cd microbot && git sparse-checkout set runelite-client/src/main/resources/net/runelite/client/plugins/microbot/shortestpath
    git checkout <commit>
"""
import argparse
import hashlib
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(REPO, "ControllerAddon", "vendor", "microbot")
RESOURCES = os.path.join("runelite-client", "src", "main", "resources", "net", "runelite", "client",
                         "plugins", "microbot", "shortestpath")

# Only transports.tsv is READ by code today; the rest are kept so the next consumer (blocked
# edges, dangerous tiles, restrictions) starts from the same pinned commit.
TABLES = [
    "transports.tsv",
    "agility_shortcuts.tsv",
    "fairy_rings.tsv",
    "teleportation_items.tsv",
    "npcs.tsv",
    "quetzals.tsv",
    "blocked_edges.tsv",
    "dangerous_tiles.tsv",
    "restrictions.tsv",
]


def clone_commit(root):
    try:
        return subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clone_root", help="root of a Microbot checkout (sparse is fine)")
    parser.add_argument("--check", action="store_true", help="compare only; write nothing")
    args = parser.parse_args()

    source_dir = os.path.join(args.clone_root, RESOURCES)
    if not os.path.isdir(source_dir):
        raise SystemExit(f"no shortestpath resources under {source_dir}")
    print(f"clone: {args.clone_root} @ {clone_commit(args.clone_root)}")
    if not args.check:
        os.makedirs(VENDOR, exist_ok=True)
    differs = 0
    for name in TABLES:
        with open(os.path.join(source_dir, name), "rb") as handle:
            data = handle.read()
        path = os.path.join(VENDOR, name)
        rows = sum(1 for line in data.decode("utf-8").splitlines()[1:]
                   if line.strip() and not line.startswith("#"))
        same = os.path.isfile(path) and open(path, "rb").read() == data
        differs += 0 if same else 1
        if not args.check and not same:
            with open(path, "wb") as handle:      # bytes, so nothing re-encodes or re-wraps it
                handle.write(data)
        state = "same" if same else ("DIFFERS" if args.check else "written")
        print(f"  {name:24} {len(data):7} bytes  {rows:5} rows  sha256 {hashlib.sha256(data).hexdigest()[:16]}  {state}")
    return 1 if (args.check and differs) else 0


if __name__ == "__main__":
    sys.exit(main())
