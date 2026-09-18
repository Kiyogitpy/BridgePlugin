"""Copy Shortest Path's transport tables out of the installed Plugin Hub jar, byte for byte.

    python tools/extract_shortest_path_tables.py            # extract + print the manifest
    python tools/extract_shortest_path_tables.py --check    # only report differences

The tables are the plugin's own data (Skretzo's shortest-path), shipped as plain TSV resources in
the jar. We copy them rather than reconstruct them -- see ControllerAddon/vendor/VENDORED.md.
Never hand-edit the copies: re-run this against a newer jar and diff instead.
"""
import argparse
import glob
import hashlib
import os
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(REPO, "ControllerAddon", "vendor")

# jar entry -> vendored file name. bank.tsv lives under destinations/ in the jar.
TABLES = {
    "transports/transports.tsv": "transports.tsv",
    "transports/agility_shortcuts.tsv": "agility_shortcuts.tsv",
    "transports/fairy_rings.tsv": "fairy_rings.tsv",
    "transports/spirit_trees.tsv": "spirit_trees.tsv",
    "transports/gnome_gliders.tsv": "gnome_gliders.tsv",
    "destinations/game_features/bank.tsv": "bank.tsv",
}


def find_jar():
    jars = sorted(glob.glob(os.path.expanduser(os.path.join("~", ".runelite", "plugins", "shortest-path_*.jar"))),
                  key=os.path.getmtime)
    if not jars:
        raise SystemExit("no shortest-path_*.jar under ~/.runelite/plugins -- install Shortest Path from the Plugin Hub")
    return jars[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare only; write nothing")
    args = parser.parse_args()

    jar = find_jar()
    print(f"jar: {os.path.basename(jar)}")
    differs = 0
    with zipfile.ZipFile(jar) as z:
        for entry, name in TABLES.items():
            data = z.read(entry)
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
