"""Measure how much of a 600ms game tick one 3-tick-granite critical window actually costs.

The whole 3-tick design (docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md)
rests on an ARITHMETIC claim: an in-place click (~92ms measured in giants_foundry) plus one
short flight (~170ms median at 52px per settings.HM2_MED_REF_MS) plus a click (~85ms median
per CLICK_HOLD_RANGE_S) is ~347ms median and ~560ms at p90, so it fits a 600ms tick. This
script replaces that arithmetic with a measurement on THIS machine.

It never touches the game: it moves the physical cursor between two screen points you give it
and times the sequence. Run it with the client in the foreground but the cursor over empty
desktop, or accept that it will click whatever is under those points.

NEVER run this while controller.py is running -- one process per COM port.

    python tick_bench.py --a 1700 800 --b 1750 700 --cycles 40
"""

import argparse
import statistics
import time

import serial

import controller
import settings


def _percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def one_window(ser, log_point, rock_point):
    """One critical tick: click in place on the parked point, fly to the rock, click.
    Returns (total_ms, in_place_ms, flight_and_click_ms)."""
    t0 = time.perf_counter()
    controller.click_human(ser)                       # the log click -- cursor already parked
    t1 = time.perf_counter()
    controller.move_to_human(ser, rock_point[0], rock_point[1], aim_radius=3)
    controller.click_human(ser)
    t2 = time.perf_counter()
    return (t2 - t0) * 1000.0, (t1 - t0) * 1000.0, (t2 - t1) * 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=settings.SERIAL_PORT)
    ap.add_argument("--baud", type=int, default=settings.SERIAL_BAUD)
    ap.add_argument("--a", nargs=2, type=int, required=True, metavar=("X", "Y"),
                    help="the parked point (stands in for the log's inventory slot)")
    ap.add_argument("--b", nargs=2, type=int, required=True, metavar=("X", "Y"),
                    help="the target point (stands in for a granite rock)")
    ap.add_argument("--cycles", type=int, default=40)
    args = ap.parse_args()

    controller.set_dpi_aware()
    controller.warn_if_pointer_acceleration()

    dist = ((args.a[0] - args.b[0]) ** 2 + (args.a[1] - args.b[1]) ** 2) ** 0.5
    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        time.sleep(2.0)                       # Leonardo reset after the port opens
        controller.FIRMWARE_PROTOCOL = controller.probe_firmware_protocol(ser)
        totals, in_place, flights = [], [], []
        for _ in range(args.cycles):
            controller.move_to_human(ser, args.a[0], args.a[1], aim_radius=0)  # park
            time.sleep(0.4)                   # stand in for the dead ticks
            total, ip, fl = one_window(ser, args.a, args.b)
            totals.append(total)
            in_place.append(ip)
            flights.append(fl)

    print(f"\ndistance {dist:.0f}px over {args.cycles} cycles")
    print(f"  in-place click : median {statistics.median(in_place):6.1f}ms  "
          f"p90 {_percentile(in_place, 0.90):6.1f}ms")
    print(f"  flight + click : median {statistics.median(flights):6.1f}ms  "
          f"p90 {_percentile(flights, 0.90):6.1f}ms")
    print(f"  WHOLE WINDOW   : median {statistics.median(totals):6.1f}ms  "
          f"p90 {_percentile(totals, 0.90):6.1f}ms  max {max(totals):6.1f}ms")
    p90 = _percentile(totals, 0.90)
    budget = 600.0 - 60.0                     # tick minus the spec's logClickPhaseMs
    print(f"\n  budget at logClickPhaseMs=60 is {budget:.0f}ms -- "
          f"{'PASS' if p90 <= budget else 'FAIL'} at p90")
    if p90 > budget:
        print("  3-tick is NOT viable at this geometry. Either shorten the distance "
              "(zoom in / smaller client) or fall back to the 4-tick celastrus variant.")


if __name__ == "__main__":
    main()
