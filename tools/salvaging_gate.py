"""Task 1's live gate + a pre-flight readiness check for the salvaging run.

Read-only. Never touches the serial port. Run after the client is rebuilt.
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8080"


def g(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.load(r)


def main():
    problems = []

    player = g("/player")
    me = player.get("boatViewId")
    print(f"player boatViewId : {me}")
    if me is None:
        problems.append("player has no boatViewId -- client not rebuilt, or not aboard a boat")

    objs = g("/objects")

    def mine(name):
        hits = [o for o in objs if name.lower() in (o.get("name") or "").lower()]
        ours = [o for o in hits if o.get("boatViewId") == me] if me is not None else hits
        return hits, ours

    for label, needle, want in [
        ("Salvaging station", "salvaging station", 1),
        ("Cargo hold", "cargo hold", 1),
        ("Salvaging hook", "salvaging hook", 2),
    ]:
        hits, ours = mine(needle)
        ids = sorted({o.get("id") for o in ours})
        print(f"{label:18}: {len(hits):3d} in scan | {len(ours):2d} on OUR boat  ids={ids}")
        if me is not None and len(ours) != want:
            problems.append(f"{label}: expected {want} on our boat, found {len(ours)}")

    # The gate itself: exactly one station must resolve to us.
    _, our_stations = mine("salvaging station")
    if me is not None and len(our_stations) == 1:
        print("\nGATE: PASS -- boatViewId separates our boat from the neighbours'")
    else:
        print("\nGATE: FAIL")

    inv = g("/inventory")
    salv = [i for i in inv if (i.get("name") or "").lower().endswith("salvage")]
    print(f"\ninventory: {len(inv)}/28 used, {len(salv)} salvage")
    print("non-salvage held:", sorted({(i.get('name') or '') for i in inv if i not in salv}))

    try:
        skills = g("/skills")
        sail = (skills.get("Sailing") or {})
        print(f"Sailing: level {sail.get('boosted')} xp {sail.get('xp')}")
    except Exception as exc:
        problems.append(f"/skills unreadable: {exc}")

    state = g("/state")
    print(f"windowFocused: {state.get('windowFocused')}  dialogOpen: {state.get('dialogOpen')}")
    if not state.get("windowFocused"):
        problems.append("client window is not focused -- the engine will not click")

    print("\n" + ("PROBLEMS:" if problems else "No problems found."))
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
