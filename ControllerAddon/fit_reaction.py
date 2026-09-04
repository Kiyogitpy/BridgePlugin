"""Human reaction time from a recorder .jsonl: ms from each outcome record (inventory delta,
XP drop) to the human's NEXT click. Feeds settings.REACTION_DELAY_S.

    python fit_reaction.py recordings/2026-09-03_061331_guaridianoftherift-benchmark.jsonl
"""
import json
import sys

OUTCOME_KINDS = ("inv", "xp")


def reaction_deltas(records, max_ms=3000):
    actions = sorted(r["t"] for r in records if r.get("kind") == "action")
    out = []
    for r in records:
        if r.get("kind") not in OUTCOME_KINDS:
            continue
        nxt = next((a for a in actions if a > r["t"]), None)
        if nxt is not None and nxt - r["t"] <= max_ms:
            out.append(nxt - r["t"])
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    records = []
    for line in open(argv[0], encoding="utf-8"):
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    d = sorted(reaction_deltas(records))
    if not d:
        print("no outcome->action pairs found")
        return 1
    q = lambda p: d[min(len(d) - 1, int(p * len(d)))]
    print(f"n={len(d)} p10={q(0.10)}ms p25={q(0.25)}ms median={q(0.5)}ms p75={q(0.75)}ms p90={q(0.90)}ms")
    print("suggested REACTION_DELAY_S = (%.2f, %.2f)  # p25..p75" % (q(0.25) / 1000, q(0.75) / 1000))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
