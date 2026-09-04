import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import log_gaps
import fit_reaction


def test_gaps_are_measured_between_timestamped_lines_and_sorted():
    lines = ["[07:00:00.000 tick=1] a", "junk", "[07:00:01.500 tick=2] b", "[07:00:06.000 tick=3] c"]
    g = log_gaps.gaps(lines)
    assert [round(x[0], 1) for x in g] == [4.5, 1.5]
    assert g[0][2].endswith(" b")          # the line BEFORE the biggest gap
    s = log_gaps.summary(g)
    assert s["total_s"] == 6.0 and s["over"][4.0] == 4.5 and s["over"][1.0] == 6.0


def test_reaction_deltas_pair_each_outcome_with_the_next_action():
    recs = [{"kind": "inv", "t": 1000}, {"kind": "mouse", "t": 1100},
            {"kind": "action", "t": 1250}, {"kind": "xp", "t": 5000},
            {"kind": "action", "t": 9500}, {"kind": "xp", "t": 9600}]
    assert fit_reaction.reaction_deltas(recs) == [250]      # 4500 is over max_ms; last has no action
