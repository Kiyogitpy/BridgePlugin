"""Bench checks for firmware/Firmware.ino protocol v3 -- run after every reflash.

    python firmware_bench.py            # from ControllerAddon/

Exercises V (version), X (flush), R (release-all), W (wheel) against the live device and
prints PASS/FAIL per check. Requires the Arduino connected and NO controller.py running --
this script opens the same COM port (unlike mcp_debug_server.py, which never touches it).

Deliberately does not import controller.py: no DPI setup, no panic listener, no side effects.
The cursor is read via a local GetCursorPos, and the flush check drives real cursor motion --
keep the mouse still and let it run.

WARNING: start this script with the cursor at least ~400px clear of the RIGHT edge of the
screen. GetCursorPos clips at the screen boundary, so if the flush check's rightward motion
would carry the cursor off the edge, every reading past that point silently reads the same
clipped x and every measurement below (moved/drift_after/the baseline travel) is bogus.

WARNING: KD SHIFT is a v1/v2 command, so it presses the physical SHIFT key even on
un-reflashed firmware. R (release-all) is v3-ONLY and silently ignored pre-v3. The only
command that releases a held key on every protocol level is KU SHIFT (the v1/v2 key-up).
If you run this against pre-v3 firmware, KU SHIFT in the cleanup (in the finally block)
is what releases it; R and the watchdog have no effect there.
"""

import ctypes
import sys
import time

import serial

from settings import SERIAL_PORT, SERIAL_BAUD


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos():
    pt = _Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def shift_is_down():
    # VK_SHIFT = 0x10; high bit of GetAsyncKeyState = currently down.
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)


def send(ser, line):
    ser.write((line + "\n").encode("ascii"))


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return ok


def main():
    print(f"Opening {SERIAL_PORT} @ {SERIAL_BAUD} (close controller.py first!)")
    results = []
    with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
        time.sleep(2.0)  # Pro Micro reset on open
        home = cursor_pos()  # record starting cursor position for cleanup
        px_per_unit = None  # learned below; used to make every cursor restore scale-correct

        try:
            # --- V: version query -------------------------------------------------------------
            ser.reset_input_buffer()
            send(ser, "V")
            line = ser.readline().decode("ascii", "replace").strip()
            results.append(check("V responds 'HB 3'", line == "HB 3", f"got {line!r}"))

            # --- X: flush stops queued motion early -------------------------------------------
            # Queue 2000 ms of slow rightward motion (10 x "S 5000 0 200" segments). S moves in
            # raw HID mouse units, not screen px -- Windows' pointer-speed setting decides the
            # px-per-unit ratio, and it varies by machine (measured ~0.737 px/unit on the
            # reference machine, i.e. this 500-unit move is ~369 px there, not 500 px). An
            # absolute pixel window is therefore not portable: on a slower-pointer machine a
            # genuinely-working flush can undershoot it, and on this machine an IGNORED flush
            # samples inside it purely by the timing coincidence of when `mid` happens to be
            # read. Flush at ~500 ms into the stream.
            start = cursor_pos()
            for _ in range(10):
                send(ser, "S 5000 0 200")
            time.sleep(0.5)
            send(ser, "X")
            time.sleep(0.4)          # let the active segment finish
            mid = cursor_pos()
            time.sleep(0.6)          # would still be moving if the flush was ignored
            end = cursor_pos()
            # `moved` uses `end`, not `mid`: a working flush lands ~3 of 10 segments in and is
            # already settled by `mid` (~0.3 of the full travel, unchanged by `end`), but an
            # IGNORED flush is still running the whole time -- by `mid` it's only ~0.45 of the
            # full travel (inside a naive "under half" bound almost by accident), and only by
            # `end` does it clearly separate (~0.75). See the report for the worked arithmetic.
            moved = end[0] - start[0]
            drift_after = abs(end[0] - mid[0])

            # Drain whatever's left of the 2000 ms stream before measuring a clean baseline --
            # if X was ignored, up to ~500 ms of it is still running at this point, and starting
            # the baseline burst on top of that would contaminate F. An extra X here is free
            # insurance (a no-op on a working device, since the queue is already empty by now).
            time.sleep(0.6)
            send(ser, "X")
            drained = cursor_pos()

            # Baseline: replay the identical 10 segments uninterrupted and let all 2000 ms play
            # out, measuring the full travel F. This makes the check scale-free -- it no longer
            # depends on this machine's px-per-unit ratio or on Windows pointer acceleration.
            for _ in range(10):
                send(ser, "S 5000 0 200")
            time.sleep(2.1)
            baseline_end = cursor_pos()
            full_travel = baseline_end[0] - drained[0]

            if full_travel > 0:
                fraction = moved / full_travel
                results.append(check(
                    "X stopped motion early", 0 < moved <= 0.5 * full_travel,
                    f"moved {moved}px flushed vs {full_travel}px uninterrupted baseline "
                    f"(fraction {fraction:.2f}, bound 0.50)"))
                px_per_unit = full_travel / 500.0  # 10 x 50-unit segments = 500 units total
            else:
                results.append(check(
                    "X stopped motion early", False,
                    f"moved {moved}px, but uninterrupted baseline travel was {full_travel}px "
                    "-- baseline measurement itself is broken, treat as FAIL"))
            results.append(check("no motion after flush", drift_after <= 2,
                                 f"{drift_after}px after"))

            # Restore the cursor using the just-measured px-per-unit ratio. Sending the raw
            # pixel delta as if it were unit-for-unit (the old approach) only walks the cursor
            # back by however many pixels one unit happens to be worth on this machine -- ~74%
            # of the real displacement at the reference calibration.
            total_dx = baseline_end[0] - start[0]
            total_dy = baseline_end[1] - start[1]
            ratio = px_per_unit or 1.0
            send(ser, f"S {round(-total_dx / ratio * 100)} {round(-total_dy / ratio * 100)} 300")
            time.sleep(0.5)          # put the cursor back

            # --- R: releases a held key --------------------------------------------------------
            send(ser, "KD SHIFT")
            time.sleep(0.3)
            held = shift_is_down()
            send(ser, "R")
            time.sleep(0.3)
            released = not shift_is_down()
            results.append(check("KD SHIFT held", held))
            results.append(check("R released it", released))

            # --- W: wheel (observed, not asserted) ----------------------------------------------
            print("  [....] sending W 3 then W -3 -- watch any scrollable window under the cursor")
            send(ser, "W 3")
            time.sleep(0.4)
            send(ser, "W -3")

            # --- Watchdog (optional, 60+ s) -----------------------------------------------------
            if "--watchdog" in sys.argv:
                print("  [....] KD SHIFT then 65 s of silence -- shift should auto-release")
                send(ser, "KD SHIFT")
                time.sleep(65.0)
                results.append(check("watchdog released shift", not shift_is_down()))
            else:
                print("  [skip] watchdog (rerun with --watchdog for the 65 s check)")
        finally:
            # Cleanup: release all inputs and restore cursor position.
            # R is v3-only (silently ignored pre-v3), but KU SHIFT is v1/v2 and works
            # on every protocol level.
            try:
                send(ser, "R")
                send(ser, "KU SHIFT")
                time.sleep(0.2)
                now = cursor_pos()
                dx = now[0] - home[0]
                dy = now[1] - home[1]
                if abs(dx) > 3 or abs(dy) > 3:
                    # Cursor moved; send one segment to walk it back home. Use the px-per-unit
                    # ratio learned during the flush/baseline check above when we have it --
                    # sending the raw pixel delta as if it were unit-for-unit (ratio 1.0) only
                    # walks back however many pixels one unit happens to be worth on this
                    # machine's pointer-speed setting, e.g. ~74% of the real distance at the
                    # reference calibration. Falls back to that old best-effort 1:1 assumption
                    # only if an exception cut the run short before the ratio was ever measured.
                    ratio = px_per_unit or 1.0
                    send(ser, f"S {round(-dx / ratio * 100)} {round(-dy / ratio * 100)} 300")
                    time.sleep(0.5)
            except Exception:
                pass  # Suppress exceptions during cleanup to preserve the original exception

    print("\nALL PASS" if all(results) else "\nFAILURES -- see above")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
