"""tools/humanity_report.py: every metric on hand-built records with known answers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import humanity_report as h  # noqa: E402


# ---- record builders (the recorder's real shapes, minimal fields) ---------------------------

def mouse(t, x, y):
    return {"kind": "mouse", "t": t, "points": [[x, y, t]]}


def click(t, x=100, y=100):
    return {"kind": "click", "t": t, "edge": "down", "button": 1, "x": x, "y": y}


def key(t, name, edge="down"):
    return {"kind": "key", "t": t, "edge": edge, "keyName": name, "keyCode": 0}


def action(t, menu="WALK"):
    return {"kind": "action", "t": t, "menuAction": menu}


def canvas(t, kind):
    return {"kind": kind, "t": t, "x": 0, "y": 0}


def focus(t, gained):
    return {"kind": "focus", "t": t, "gained": gained}


def test_percentile_interpolates_and_handles_empty():
    assert h.percentile([], 0.5) is None
    assert h.percentile([10], 0.9) == 10
    assert h.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert h.percentile([1, 2, 3, 4], 1.0) == 4


def test_active_windows_are_the_spans_between_clicks_no_further_than_2x_pad_apart():
    # Two clicks 10s apart form one active span; a click 100s later starts a new one (with the
    # pad at 30s, a 100s gap has no click within 30s on both sides in its middle).
    recs = [click(0), click(10_000), click(110_000), click(115_000)]
    assert h.active_windows(recs, pad_ms=30_000) == [(0, 10_000), (110_000, 115_000)]
    assert h.active_windows([click(5)], pad_ms=30_000) == []          # one click: no span
    assert h.active_windows([], pad_ms=30_000) == []


def test_still_segments_are_gaps_between_cursor_samples_inside_active_play_only():
    recs = [click(0), mouse(100, 1, 1), mouse(600, 2, 2), mouse(5_600, 3, 3),   # gaps 500, 5000
            click(6_000),
            mouse(200_000, 4, 4), mouse(203_000, 5, 5)]                         # outside any window
    windows = h.active_windows(recs, pad_ms=30_000)                             # [(0, 6000)]
    assert h.still_segments(recs, windows) == [500, 5_000]


def test_busy_fraction_counts_one_second_bins_with_any_cursor_sample():
    recs = [click(0), mouse(100, 1, 1), mouse(2_500, 2, 2), click(4_000)]      # window (0, 4000)
    windows = h.active_windows(recs, pad_ms=30_000)
    # bins: [0,1s) has a sample, [1,2) none, [2,3) has, [3,4) none -> 2 of 4
    assert abs(h.busy_fraction(recs, windows) - 0.5) < 1e-9
    assert h.busy_fraction(recs, []) is None


def test_load_skips_malformed_lines(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"kind":"click","t":1}\nnot json\n{"kind":"mouse","t":2,"points":[]}\n', encoding="utf-8")
    assert [r["kind"] for r in h.load(p)] == ["click", "mouse"]


def test_excursions_pair_exit_with_the_next_enter():
    recs = [click(0), canvas(1_000, "canvasexit"), canvas(4_000, "canvasenter"),
            canvas(10_000, "canvasexit"), canvas(11_000, "canvasenter"), click(3_600_000)]
    ex = h.excursions(recs)
    assert ex["count"] == 2
    assert ex["lengths_ms"] == [3_000, 1_000]
    assert abs(ex["per_hour"] - 2.0) < 1e-6            # 1 hour of recording
    assert abs(ex["off_fraction"] - 4_000 / 3_600_000) < 1e-9


def test_excursion_still_open_at_the_end_counts_to_the_end():
    recs = [click(0), canvas(1_000, "canvasexit"), click(5_000)]
    ex = h.excursions(recs)
    assert ex["count"] == 1 and ex["lengths_ms"] == [4_000]


def test_focus_stats_integrate_unfocused_time():
    recs = [click(0), focus(1_000, False), focus(3_000, True), focus(8_000, False), click(10_000)]
    fs = h.focus_stats(recs)
    assert fs["changes"] == 3
    assert abs(fs["unfocused_fraction"] - (2_000 + 2_000) / 10_000) < 1e-9
    assert h.focus_stats([click(0), click(10)])["changes"] == 0
