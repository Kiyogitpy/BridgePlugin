"""Judge a 3-tick recording the way the routine judges itself: WHICH TICK did each click land
in, and how many ticks after the fletch did the Mining xp arrive.

    python tick_report.py recordings/<stamp>_<label>.jsonl

This is the analysis that settled the mechanic on 2026-09-09 from USER's own recordings
(CLAUDE.md, granite_3t): a knife-on-log clicked during client tick N pays with the packet that
opens tick N+3 when the Mine click lands in the SAME tick, and at N+4 when it slips into the
next one. The recorder's own `tick` labels cannot show that -- an outcome record is written
during packet processing, ~20ms BEFORE the GameTick that bumps the label, so xp/chat/anim
records carry the previous tick's number. Everything here is re-derived from the `tick`
records' timestamps instead: a boundary is a tick record's `t` minus 20ms.

Also reports press-vs-release: `down->act` is the ms from the last mouse-down to the menu
action, `up->act` from the last mouse-up. A world click acts ~1-23ms after the DOWN; an
inventory click ~10ms after the UP (drag detection).
"""

import bisect
import json
import sys

GAMETICK_LAG_MS = 20        # GameTick fires ~20ms after the packet's records are dispatched


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def report(recs, out=print):
    ticks = [r for r in recs if r.get("kind") == "tick"]
    if not ticks:
        out("no tick records")
        return
    bt = [r["t"] - GAMETICK_LAG_MS for r in ticks]
    bn = [r["tick"] for r in ticks]

    def tick_of(t):
        i = bisect.bisect_right(bt, t) - 1
        return bn[i] if i >= 0 else None

    def phase(t):
        i = bisect.bisect_right(bt, t) - 1
        return t - bt[i] if i >= 0 else -1.0     # before the first tick record

    clicks = [r for r in recs if r.get("kind") == "click"]
    downs = [r["t"] for r in clicks if r.get("edge") == "down"]
    ups = [r["t"] for r in clicks if r.get("edge") == "up"]

    def before(lst, t):
        i = bisect.bisect_right(lst, t) - 1
        return lst[i] if i >= 0 else None

    last_fletch = None
    pays = {}
    for r in recs:
        k = r.get("kind")
        if k == "action":
            d, u = before(downs, r["t"]), before(ups, r["t"])
            label = r.get("option", "?")
            if r.get("targetKind") == "widget":
                label += " -> " + (r.get("itemName") or "")
            if r.get("option") == "Use" and r.get("menuAction") == "WIDGET_TARGET_ON_WIDGET":
                label = "FLETCH"
            if r.get("option") == "Mine":
                label = (f"Mine ({r.get('targetX')},{r.get('targetY')}) "
                         f"from ({r.get('playerX')},{r.get('playerY')})")
            tk = tick_of(r["t"])
            extra = ""
            if label == "FLETCH":
                if last_fletch is not None:
                    extra = f"  (+{tk - last_fletch} ticks since the last fletch)"
                last_fletch = tk
            out(f"{r['t']:7d} {label:36s} tick={tk} phase={phase(r['t']):4.0f}  "
                f"down->act={r['t'] - d if d else None} up->act={r['t'] - u if u else None}{extra}")
        elif k == "xp" and r.get("skill") == "Mining":
            tk = tick_of(r["t"])
            extra = ""
            if last_fletch is not None:
                pay = tk - last_fletch
                pays[pay] = pays.get(pay, 0) + 1
                extra = f"  (paid {pay} ticks after the fletch)"
            out(f"{r['t']:7d} {'XP +' + str(r.get('gained')):36s} tick={tk} "
                f"phase={phase(r['t']):4.0f}{extra}")
    out("")
    out("pay after the fletch, in ticks: " +
        ", ".join(f"{k}: {v}" for k, v in sorted(pays.items())))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    report(load(sys.argv[1]))
