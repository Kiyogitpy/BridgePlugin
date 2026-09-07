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


def stamp(t=0):
    return {"kind": "recorder", "t": t, "schema": 2, "canvasEdges": True, "focus": True}


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
    recs = [click(0), mouse(100, 1, 1), mouse(160, 1, 1),                       # 60ms: NOT still
            mouse(600, 2, 2), mouse(5_600, 3, 3),                               # gaps 500, 5000
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
    recs = [stamp(), click(0), canvas(1_000, "canvasexit"), canvas(4_000, "canvasenter"),
            canvas(10_000, "canvasexit"), canvas(11_000, "canvasenter"), click(3_600_000)]
    ex = h.excursions(recs)
    assert ex["count"] == 2
    assert ex["lengths_ms"] == [3_000, 1_000]
    assert abs(ex["per_hour"] - 2.0) < 1e-6            # 1 hour of recording
    assert abs(ex["off_fraction"] - 4_000 / 3_600_000) < 1e-9


def test_excursion_still_open_at_the_end_counts_to_the_end():
    recs = [stamp(), click(0), canvas(1_000, "canvasexit"), click(5_000)]
    ex = h.excursions(recs)
    assert ex["count"] == 1 and ex["lengths_ms"] == [4_000]


def test_focus_stats_integrate_unfocused_time():
    recs = [stamp(), click(0), focus(1_000, False), focus(3_000, True), focus(8_000, False), click(10_000)]
    fs = h.focus_stats(recs)
    assert fs["changes"] == 3
    assert abs(fs["unfocused_fraction"] - (2_000 + 2_000) / 10_000) < 1e-9
    assert h.focus_stats([stamp(), click(0), click(10)])["changes"] == 0


def test_unstamped_recording_reports_excursions_and_focus_as_unmeasured():
    # No "recorder" schema stamp -- this recording predates canvas/focus capture, so a zero
    # excursion/focus count must read as "never measured" (None), not "measured, saw nothing".
    recs = [click(0), canvas(1_000, "canvasexit"), canvas(4_000, "canvasenter"),
            focus(2_000, False), focus(3_000, True), mouse(100, 1, 1), mouse(600, 2, 2), click(6_000)]
    ex = h.excursions(recs)
    assert ex["count"] is None and ex["per_hour"] is None and ex["off_fraction"] is None
    fs = h.focus_stats(recs)
    assert fs["changes"] is None and fs["per_hour"] is None and fs["unfocused_fraction"] is None
    m = h.metrics(recs)
    for k in ("excursions_per_hour", "excursion_p50_ms", "excursion_p90_ms", "off_canvas_fraction",
              "focus_changes_per_hour", "unfocused_fraction"):
        assert m[k] is None
    assert m["still_p50_ms"] is not None


def test_bursts_group_clicks_within_the_gap_and_report_intra_gaps():
    # Three quick clicks (200 ms apart), then a lone click 5 s later, then two more.
    recs = [click(0), click(200), click(400), click(5_400), click(9_000), click(9_300)]
    b = h.bursts(recs)
    assert b["lengths"] == [3, 2]              # bursts of >= 2 clicks only
    assert b["intra_gaps_ms"] == [200, 200, 300]
    assert abs(b["burst_share"] - 2 / 3) < 1e-9   # 2 of 3 click groups were bursts


def test_shift_per_walk_is_shift_downs_over_viewport_walk_actions():
    recs = [key(0, "Shift"), click(50), action(60, "WALK"), key(70, "Shift", "up"),
            key(1_000, "Shift"), click(1_050), action(1_060, "WALK"), key(1_070, "Shift", "up"),
            action(2_000, "GAME_OBJECT_FIRST_OPTION")]
    s = h.shift_per_walk(recs)
    assert s == {"shift_downs": 2, "walk_clicks": 2, "ratio": 1.0}
    assert h.shift_per_walk([click(0)])["ratio"] is None


def test_typing_ignores_modifiers_and_function_keys_and_long_pauses():
    recs = [key(0, "A"), key(120, "D"), key(250, "Shift"), key(300, "A"), key(5_000, "M"), key(5_090, "F1"), key(5_200, "Enter")]
    t = h.typing(recs)
    # A->D 120, D->A 180 (Shift skipped), A->M 4700 is > TYPING_MAX_GAP_MS so dropped, M->Enter 200 (F1 skipped)
    assert t["intervals_ms"] == [120, 180, 200]
    assert t["p50"] == 180
    assert t["log_sigma"] is not None and t["log_sigma"] > 0
    assert h.typing([key(0, "A")])["p50"] is None


def test_metrics_table_has_every_key_in_order_and_tolerates_an_empty_recording():
    m = h.metrics([])
    assert list(m) == list(h.METRIC_ORDER)
    assert all(v is None for v in m.values())


def test_render_prints_two_recordings_side_by_side():
    a = h.metrics([click(0), mouse(100, 1, 1), mouse(600, 2, 2), click(1_000)])
    b = h.metrics([click(0), click(300), click(5_000)])
    out = h.render(["human", "bot"], [a, b])
    assert "human" in out and "bot" in out
    assert "still_p90_ms" in out
    lines = out.splitlines()
    assert len(lines) >= len(h.METRIC_ORDER) + 1
