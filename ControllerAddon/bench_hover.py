"""THROWAWAY BENCHMARK -- not part of any routine, safe to delete.

Measures how well move_to_human + /npc/by-index can track a MOVING npc (anchor: a Dashing
kebbit standing in front of the player). Two phases:

  Phase A (passive, no mouse)  -- polls /npc/by-index flat out and records round-trip latency
                                  plus how far the reported screen point drifts between polls.
                                  This is the floor: no aiming strategy can beat the staleness
                                  a single round-trip already costs.

  Phase B (hover, drives the Arduino) -- repeatedly flies the cursor onto the kebbit with
                                  move_to_human, cycling through refresh strategies ("arms")
                                  so the same kebbit motion is sampled by each one. A
                                  background thread samples cursor-vs-reported-position every
                                  SAMPLE_INTERVAL_S throughout, which is the continuous
                                  "where are we in relation to the kebbit" trace.

IMPORTANT CAVEAT on what "error" means here. /npc/by-index's screenX/screenY is
Perspective.localToCanvas of the npc's LocalPoint at ground plane -- i.e. the tile centre under
its feet, NOT its model's clickbox. Nothing in the bridge exposes a convex hull. So a 0px error
in this bench means "the cursor is exactly on the point we aim at", which is NOT the same as
"the game would offer Attack". If the numbers here look good but hovering still feels wrong,
that gap is the next thing to measure, and it needs a Java-side /hover endpoint.

Holds the Arduino serial port, so no controller.py routine may be running. F1 still panics out.

Usage (from ControllerAddon/):
    python -u bench_hover.py --name kebbit
    python -u bench_hover.py --index 12323 --cycles 40
"""

import argparse
import csv
import math
import os
import statistics
import threading
import time

import requests

import controller
from settings import SERIAL_PORT, SERIAL_BAUD

import serial


SAMPLE_INTERVAL_S = 0.05

# (label, refresh_legs). None => move_to_human's plain path (single snapshot, plus its own
# built-in single midflight checkpoint whenever refresh_target is supplied and the flight is
# long enough -- see MOVING_TARGET_MIN_DIST). "cold" passes no refresh_target at all, which is
# the true stale-snapshot baseline.
ARMS = [
    ("cold", None),
    ("midflight", None),
    ("legs3", 3),
    ("legs6", 6),
]


class Bench:
    def __init__(self, npc_index: int, out_dir: str):
        self.npc_index = npc_index
        self.session = requests.Session()  # own session: the sampler thread must not share
        self.dpi = controller.get_dpi_scale()  # controller's module-level requests.Session
        self.rows = []
        self.rows_lock = threading.Lock()
        self.stop = threading.Event()
        self.phase = "idle"
        self.arm = "-"
        self.out_dir = out_dir

    # ---- bridge access (mirrors controller.api_get, minus the shared session) ----
    def npc(self):
        """(rtt_ms, data|None). data is DPI-scaled the same way controller.api_get scales it."""
        t0 = time.perf_counter()
        try:
            resp = self.session.get(
                f"{controller.API_BASE}/npc/by-index",
                params={"index": self.npc_index},
                timeout=0.3,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return (time.perf_counter() - t0) * 1000.0, None
        rtt_ms = (time.perf_counter() - t0) * 1000.0
        if not data.get("found"):
            return rtt_ms, None
        return rtt_ms, controller._scale_screen_coords(data, self.dpi)

    def npc_point(self):
        rtt_ms, data = self.npc()
        if data is None:
            return rtt_ms, None
        x, y = data.get("screenX"), data.get("screenY")
        if x is None or y is None:
            return rtt_ms, None
        return rtt_ms, (int(x), int(y))

    def record(self, **kw):
        with self.rows_lock:
            self.rows.append(kw)

    # ---- phase A: passive drift + latency ----
    def passive(self, seconds: float):
        print(f"\n=== Phase A: passive sampling for {seconds:.0f}s (no mouse) ===")
        self.phase = "passive"
        rtts, gaps, drifts = [], [], []
        prev_pt = None
        prev_t = None
        misses = 0
        t_end = time.perf_counter() + seconds

        while time.perf_counter() < t_end:
            t = time.perf_counter()
            rtt_ms, pt = self.npc_point()
            rtts.append(rtt_ms)
            if pt is None:
                misses += 1
                prev_pt = None
                continue
            if prev_pt is not None:
                dt = t - prev_t
                d = math.hypot(pt[0] - prev_pt[0], pt[1] - prev_pt[1])
                gaps.append(dt * 1000.0)
                if dt > 0:
                    drifts.append(d / dt)  # px/s
            self.record(
                t=t, phase="passive", arm="-", rtt_ms=rtt_ms,
                npc_x=pt[0], npc_y=pt[1], cur_x="", cur_y="", err_px="",
            )
            prev_pt, prev_t = pt, t

        print(f"  polls: {len(rtts)}  misses (not found / http fail): {misses}")
        _stat("  round-trip ms", rtts)
        _stat("  poll gap ms  ", gaps)
        _stat("  npc drift px/s", drifts)
        if rtts and drifts:
            stale = statistics.median(rtts) / 1000.0 * statistics.median(drifts)
            print(f"  => staleness floor: a median round-trip costs ~{stale:.1f}px of drift")

    # ---- background sampler for phase B ----
    def sampler(self):
        while not self.stop.is_set():
            t = time.perf_counter()
            rtt_ms, pt = self.npc_point()
            cur = controller.get_cursor_pos()
            if pt is not None:
                err = math.hypot(cur[0] - pt[0], cur[1] - pt[1])
                self.record(
                    t=t, phase=self.phase, arm=self.arm, rtt_ms=rtt_ms,
                    npc_x=pt[0], npc_y=pt[1], cur_x=cur[0], cur_y=cur[1],
                    err_px=round(err, 1),
                )
            self.stop.wait(SAMPLE_INTERVAL_S)

    # ---- phase B: hover cycles ----
    def hover(self, ser, cycles: int):
        print(f"\n=== Phase B: {cycles} hover cycles across {len(ARMS)} arms ===")
        print(f"{'#':>3} {'arm':>10} {'flight_ms':>10} {'drift_px':>9} "
              f"{'settle_err':>10} {'err@+300ms':>11}")
        results = {label: {"settle": [], "later": [], "flight": [], "drift": []}
                   for label, _ in ARMS}

        for i in range(cycles):
            label, legs = ARMS[i % len(ARMS)]
            self.arm = label
            self.phase = "hover"

            _, start_pt = self.npc_point()
            if start_pt is None:
                print(f"{i:>3} {label:>10}  npc not found -- skipping")
                time.sleep(0.3)
                continue

            # Park the cursor away from the target first, so every cycle measures a real
            # flight rather than a 2px no-op (move_to_human returns early under 2px).
            controller.move_to_human(ser, *_park_point(start_pt), fps=190)
            time.sleep(0.15)

            _, aim_pt = self.npc_point()
            if aim_pt is None:
                continue

            refresh = None if label == "cold" else controller.npc_index_refresh_target(self.npc_index)
            t0 = time.perf_counter()
            controller.move_to_human(
                ser, aim_pt[0], aim_pt[1],
                fps=190,
                aim_radius=controller.ENTITY_AIM_RADIUS,
                overshoot_chance=0.0,  # keep the measurement about tracking, not humanization
                refresh_target=refresh,
                refresh_legs=legs,
            )
            flight_ms = (time.perf_counter() - t0) * 1000.0

            # Settle error: where the cursor landed vs where the npc is RIGHT NOW.
            cur = controller.get_cursor_pos()
            _, now_pt = self.npc_point()
            if now_pt is None:
                continue
            settle = math.hypot(cur[0] - now_pt[0], cur[1] - now_pt[1])
            drift = math.hypot(now_pt[0] - aim_pt[0], now_pt[1] - aim_pt[1])

            # And 300ms later, with the cursor held still -- how fast does a landed hover rot?
            time.sleep(0.3)
            cur2 = controller.get_cursor_pos()
            _, later_pt = self.npc_point()
            later = (math.hypot(cur2[0] - later_pt[0], cur2[1] - later_pt[1])
                     if later_pt is not None else float("nan"))

            r = results[label]
            r["settle"].append(settle)
            r["later"].append(later)
            r["flight"].append(flight_ms)
            r["drift"].append(drift)
            print(f"{i:>3} {label:>10} {flight_ms:>10.0f} {drift:>9.1f} "
                  f"{settle:>10.1f} {later:>11.1f}")

            self.phase = "between"
            self.arm = "-"
            time.sleep(0.2)

        return results

    def write_csv(self, name: str):
        path = os.path.join(self.out_dir, name)
        with self.rows_lock:
            rows = list(self.rows)
        if not rows:
            return None
        fields = ["t", "phase", "arm", "rtt_ms", "npc_x", "npc_y", "cur_x", "cur_y", "err_px"]
        t0 = rows[0]["t"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for row in rows:
                row = dict(row)
                row["t"] = round((row["t"] - t0) * 1000.0, 1)  # ms since start
                w.writerow(row)
        return path


def _park_point(pt):
    """A point ~250px away from the target, clamped onto the primary screen, so each cycle
    starts from a comparable distance instead of wherever the last one ended."""
    import ctypes
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    x = pt[0] - 250 if pt[0] > sw // 2 else pt[0] + 250
    y = pt[1] - 120 if pt[1] > sh // 2 else pt[1] + 120
    return max(5, min(sw - 5, x)), max(5, min(sh - 5, y))


def _stat(label: str, values):
    if not values:
        print(f"{label}: (no samples)")
        return
    values = sorted(values)
    p90 = values[int(len(values) * 0.9) - 1] if len(values) >= 10 else values[-1]
    print(f"{label}: n={len(values):<5} median={statistics.median(values):>7.1f} "
          f"mean={statistics.mean(values):>7.1f} p90={p90:>7.1f} max={values[-1]:>7.1f}")


def resolve_index(name: str) -> int:
    data = controller.api_get("/npc/nearest", params={"name": name})
    if not data or not data.get("found"):
        raise SystemExit(f"no npc matching name '{name}' found near the player")
    print(f"anchor: {data.get('name')} id={data.get('id')} index={data.get('index')} "
          f"dist={data.get('distance')} screen=({data.get('screenX')},{data.get('screenY')})")
    return int(data["index"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="kebbit", help="npc name substring to anchor on")
    ap.add_argument("--index", type=int, default=None, help="exact npc index (skips --name)")
    ap.add_argument("--passive-secs", type=float, default=8.0)
    ap.add_argument("--cycles", type=int, default=24)
    ap.add_argument("--out", default=None, help="directory for the CSV trace")
    args = ap.parse_args()

    controller.set_dpi_aware()
    controller.start_panic_listener()  # F1 still hard-exits

    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
    os.makedirs(out_dir, exist_ok=True)

    npc_index = args.index if args.index is not None else resolve_index(args.name)
    bench = Bench(npc_index, out_dir)

    bench.passive(args.passive_secs)

    print(f"\nopening {SERIAL_PORT} ...")
    with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
        time.sleep(2.0)  # Pro Micro reset
        t = threading.Thread(target=bench.sampler, daemon=True)
        t.start()
        try:
            results = bench.hover(ser, args.cycles)
        finally:
            bench.stop.set()
            t.join(timeout=2.0)

    print("\n=== Per-arm summary (px from the reported npc point) ===")
    for label, _ in ARMS:
        r = results[label]
        if not r["settle"]:
            continue
        print(f"\n[{label}]")
        _stat("  flight ms      ", r["flight"])
        _stat("  drift in flight", r["drift"])
        _stat("  settle error   ", r["settle"])
        _stat("  error @ +300ms ", [v for v in r["later"] if v == v])

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    path = bench.write_csv(f"{stamp}_bench-hover.csv")
    if path:
        print(f"\ntrace: {path}")


if __name__ == "__main__":
    main()
