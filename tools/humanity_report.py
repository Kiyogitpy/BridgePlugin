"""Humanity report: the same input-behaviour metrics over a human recording and a bot recording.

    python tools/humanity_report.py recordings/human.jsonl [recordings/bot.jsonl]

Added 2026-09-07 for the telemetry mitigation spec (sub-project A2). The decompiled client
sends the whole cursor trajectory, every press with ms-since-previous, every key press with
ms-since-previous, and every focus change; a routine's own log cannot see any of that. This
tool reads a recorder .jsonl (ControllerAddon/recorder.py) and prints, per metric, a number that
the human recording sets the target for. Every function is pure over the record list; the CLI
only loads and formats. Stillness and busy-fraction are counted over ACTIVE play only (a click
within ACTIVE_PAD_MS on both sides -- the existing rule from the movement-realism fits), so a
scheduled break cannot pass for a human pause.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Iterable, Optional

ACTIVE_PAD_MS = 30_000
BUSY_BIN_MS = 1_000
BURST_GAP_MS = 1_500
TYPING_MAX_GAP_MS = 2_000
MODIFIER_KEYS = {"Shift", "Ctrl", "Alt", "Windows", "Caps Lock", "Context Menu"}
FUNCTION_KEYS = {f"F{i}" for i in range(1, 13)}


# ---------------------------------------------------------------- loading and basics

def load(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def percentile(values: Iterable[float], p: float) -> Optional[float]:
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def duration_ms(records: list[dict]) -> int:
    ts = [r["t"] for r in records if "t" in r]
    return (max(ts) - min(ts)) if ts else 0


def clicks(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("kind") == "click" and r.get("edge") == "down"]


def cursor_samples(records: list[dict]) -> list[tuple[int, int, int]]:
    """(t, x, y) for every mouse polyline point, time-sorted."""
    pts = []
    for r in records:
        if r.get("kind") == "mouse":
            for x, y, t in r.get("points") or []:
                pts.append((t, x, y))
    pts.sort()
    return pts


# ---------------------------------------------------------------- active play

def active_windows(records: list[dict], pad_ms: int = ACTIVE_PAD_MS) -> list[tuple[int, int]]:
    """Spans of active play: a moment is active when a click lies within pad_ms on BOTH sides,
    i.e. it sits between two consecutive clicks no more than 2*pad_ms apart. Adjacent spans
    merge."""
    ts = sorted(c["t"] for c in clicks(records))
    windows: list[tuple[int, int]] = []
    for a, b in zip(ts, ts[1:]):
        if b - a <= 2 * pad_ms:
            if windows and windows[-1][1] == a:
                windows[-1] = (windows[-1][0], b)
            else:
                windows.append((a, b))
    return windows


def _in_windows(t: int, windows: list[tuple[int, int]]) -> Optional[int]:
    """Index of the window containing t, or None."""
    starts = [w[0] for w in windows]
    i = bisect_right(starts, t) - 1
    if i >= 0 and windows[i][0] <= t <= windows[i][1]:
        return i
    return None


def still_segments(records: list[dict], windows: list[tuple[int, int]]) -> list[int]:
    """Gaps (ms) between consecutive cursor samples that fall inside the SAME active window --
    the stillness the client's 20 Hz recorder reports as 'no sample'."""
    gaps = []
    prev_t, prev_w = None, None
    for t, _, _ in cursor_samples(records):
        w = _in_windows(t, windows)
        if w is not None and prev_w == w:
            gaps.append(t - prev_t)
        prev_t, prev_w = t, w
    return gaps


def busy_fraction(records: list[dict], windows: list[tuple[int, int]]) -> Optional[float]:
    """Share of BUSY_BIN_MS bins inside active play that contain at least one cursor sample."""
    total = 0
    busy = 0
    samples = [t for t, _, _ in cursor_samples(records)]
    for start, end in windows:
        for b in range(start, end, BUSY_BIN_MS):
            total += 1
            lo = bisect_right(samples, b - 1)
            if lo < len(samples) and samples[lo] < b + BUSY_BIN_MS:
                busy += 1
    return (busy / total) if total else None


# ---------------------------------------------------------------- off-canvas and focus

def _spans(records: list[dict], open_kind: str, close_kind: str) -> list[tuple[int, int]]:
    """[open_t, close_t] pairs; an open with no close runs to the recording's last t."""
    end = max((r["t"] for r in records if "t" in r), default=0)
    spans, open_t = [], None
    for r in sorted((r for r in records if r.get("kind") in (open_kind, close_kind)), key=lambda r: r["t"]):
        if r["kind"] == open_kind and open_t is None:
            open_t = r["t"]
        elif r["kind"] == close_kind and open_t is not None:
            spans.append((open_t, r["t"]))
            open_t = None
    if open_t is not None:
        spans.append((open_t, end))
    return spans


def excursions(records: list[dict]) -> dict:
    """Times the cursor left the canvas (the client encodes this as -1 in EVENT_MOUSE_MOVE)."""
    spans = _spans(records, "canvasexit", "canvasenter")
    total = duration_ms(records)
    lengths = [b - a for a, b in spans]
    hours = total / 3_600_000 if total else 0
    return {"count": len(spans), "per_hour": (len(spans) / hours) if hours else None,
            "lengths_ms": lengths, "off_fraction": (sum(lengths) / total) if total else None}


def focus_stats(records: list[dict]) -> dict:
    """Window focus changes (EVENT_APPLET_FOCUS) and the share of time spent unfocused."""
    events = sorted((r for r in records if r.get("kind") == "focus"), key=lambda r: r["t"])
    total = duration_ms(records)
    unfocused = 0
    lost_at = None
    for r in events:
        if not r.get("gained") and lost_at is None:
            lost_at = r["t"]
        elif r.get("gained") and lost_at is not None:
            unfocused += r["t"] - lost_at
            lost_at = None
    if lost_at is not None:
        unfocused += max((r["t"] for r in records if "t" in r), default=lost_at) - lost_at
    hours = total / 3_600_000 if total else 0
    return {"changes": len(events), "per_hour": (len(events) / hours) if hours else None,
            "unfocused_fraction": (unfocused / total) if total else None}


# ---------------------------------------------------------------- clicks and keys

def bursts(records: list[dict]) -> dict:
    """Consecutive presses no more than BURST_GAP_MS apart form a burst (the human's 0.20 s
    hotspot spam; EVENT_MOUSE_CLICK_V2 carries every one of these gaps)."""
    ts = sorted(c["t"] for c in clicks(records))
    groups: list[list[int]] = []
    for t in ts:
        if groups and t - groups[-1][-1] <= BURST_GAP_MS:
            groups[-1].append(t)
        else:
            groups.append([t])
    multi = [g for g in groups if len(g) >= 2]
    gaps = [b - a for g in multi for a, b in zip(g, g[1:])]
    return {"intra_gaps_ms": gaps, "lengths": [len(g) for g in multi],
            "burst_share": (len(multi) / len(groups)) if groups else None}


def shift_per_walk(records: list[dict]) -> dict:
    """SHIFT presses per viewport walk click. The bot presses SHIFT once per walk click (spec V5);
    a human holds it across a run, or never uses it."""
    shift = sum(1 for r in records if r.get("kind") == "key" and r.get("edge") == "down"
                and r.get("keyName") == "Shift")
    walks = sum(1 for r in records if r.get("kind") == "action" and r.get("menuAction") == "WALK")
    return {"shift_downs": shift, "walk_clicks": walks, "ratio": (shift / walks) if walks else None}


def typing(records: list[dict]) -> dict:
    """Intervals between consecutive character key presses (modifiers and F-keys excluded, gaps
    over TYPING_MAX_GAP_MS dropped as 'not typing'); the log-sigma is what a fitted cadence
    draw needs (spec B4)."""
    downs = sorted((r["t"] for r in records if r.get("kind") == "key" and r.get("edge") == "down"
                    and r.get("keyName") not in MODIFIER_KEYS and r.get("keyName") not in FUNCTION_KEYS))
    intervals = [b - a for a, b in zip(downs, downs[1:]) if 0 < b - a <= TYPING_MAX_GAP_MS]
    log_sigma = statistics.pstdev(math.log(i) for i in intervals) if len(intervals) >= 2 else None
    return {"intervals_ms": intervals, "p50": percentile(intervals, 0.5), "log_sigma": log_sigma}


# ---------------------------------------------------------------- the table

METRIC_ORDER = (
    "duration_min", "active_min",
    "still_p50_ms", "still_p90_ms", "still_max_ms", "busy_fraction",
    "excursions_per_hour", "excursion_p50_ms", "excursion_p90_ms", "off_canvas_fraction",
    "burst_gap_p25_ms", "burst_gap_p50_ms", "burst_gap_p75_ms", "burst_len_mean", "burst_share",
    "shift_per_walk", "typing_p50_ms", "typing_log_sigma",
    "focus_changes_per_hour", "unfocused_fraction",
)


def metrics(records: list[dict]) -> dict:
    windows = active_windows(records)
    still = still_segments(records, windows)
    ex = excursions(records)
    b = bursts(records)
    s = shift_per_walk(records)
    ty = typing(records)
    fs = focus_stats(records)
    total = duration_ms(records)
    active = sum(e - a for a, e in windows)
    m = {
        "duration_min": (total / 60_000) if total else None,
        "active_min": (active / 60_000) if active else None,
        "still_p50_ms": percentile(still, 0.5), "still_p90_ms": percentile(still, 0.9),
        "still_max_ms": (max(still) if still else None), "busy_fraction": busy_fraction(records, windows),
        "excursions_per_hour": ex["per_hour"], "excursion_p50_ms": percentile(ex["lengths_ms"], 0.5),
        "excursion_p90_ms": percentile(ex["lengths_ms"], 0.9), "off_canvas_fraction": ex["off_fraction"],
        "burst_gap_p25_ms": percentile(b["intra_gaps_ms"], 0.25), "burst_gap_p50_ms": percentile(b["intra_gaps_ms"], 0.5),
        "burst_gap_p75_ms": percentile(b["intra_gaps_ms"], 0.75),
        "burst_len_mean": (statistics.mean(b["lengths"]) if b["lengths"] else None), "burst_share": b["burst_share"],
        "shift_per_walk": s["ratio"], "typing_p50_ms": ty["p50"], "typing_log_sigma": ty["log_sigma"],
        "focus_changes_per_hour": fs["per_hour"], "unfocused_fraction": fs["unfocused_fraction"],
    }
    return {k: m[k] for k in METRIC_ORDER}


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 10 else f"{v:.1f}"
    return str(v)


def render(labels: list[str], tables: list[dict]) -> str:
    w = max(len(k) for k in METRIC_ORDER)
    head = f"{'metric':{w}}  " + "  ".join(f"{l:>14}" for l in labels)
    rows = [head, "-" * len(head)]
    for k in METRIC_ORDER:
        rows.append(f"{k:{w}}  " + "  ".join(f"{_fmt(t.get(k)):>14}" for t in tables))
    return "\n".join(rows)


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__.strip().splitlines()[2])
        return 2
    labels, tables = [], []
    for p in args:
        recs = load(p)
        labels.append(Path(p).stem[:14])
        tables.append(metrics(recs))
    print(render(labels, tables))
    return 0


if __name__ == "__main__":
    sys.exit(main())
