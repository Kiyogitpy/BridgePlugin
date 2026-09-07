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
