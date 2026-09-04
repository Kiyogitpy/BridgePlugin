"""Where does a routine's time go? Gaps between consecutive timestamped log lines.

    python log_gaps.py logs/gotr_live76.log [--top 25]

Built for the 2026-09-04 event-driven-outcomes work (run 76 baseline: 48% of the round in
gaps over 4s). Every '[HH:MM:SS.mmm tick=N]' line is a sample; the line BEFORE a gap is what
the routine was doing while it waited."""
import re
import sys

_TS = re.compile(r"\[(\d\d):(\d\d):(\d\d)\.(\d\d\d)")


def gaps(lines):
    out, prev = [], None
    for i, line in enumerate(lines):
        m = _TS.match(line)
        if not m:
            continue
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000.0
        if prev is not None:
            out.append((t - prev[0], prev[1], lines[prev[1]]))
        prev = (t, i)
    out.sort(key=lambda g: -g[0])
    return out


def summary(gs, thresholds=(0.5, 1.0, 2.0, 4.0)):
    total = sum(g for g, _, _ in gs)
    return {"total_s": total, "count": len(gs),
            "over": {thr: sum(g for g, _, _ in gs if g > thr) for thr in thresholds}}


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    top = int(argv[argv.index("--top") + 1]) if "--top" in argv else 25
    lines = open(argv[0], encoding="utf-8", errors="replace").read().splitlines()
    gs = gaps(lines)
    s = summary(gs)
    print(f"total span {s['total_s']:.1f}s over {s['count']} gaps")
    for thr, secs in s["over"].items():
        pct = 100 * secs / s["total_s"] if s["total_s"] else 0
        print(f"gaps>{thr}s: {secs:.0f}s ({pct:.0f}%)")
    print(f"--- top {top} gaps: seconds, line before")
    for g, i, line in gs[:top]:
        print(f"{g:5.1f}  L{i + 1}: {line[:140]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
