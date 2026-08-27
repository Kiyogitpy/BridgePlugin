"""Bench checks for firmware/Firmware.ino protocol v3 -- run after every reflash.

    python firmware_bench.py            # from ControllerAddon/

Exercises V (version), X (flush), R (release-all), W (wheel) against the live device and
prints PASS/FAIL per check. Requires the Arduino connected and NO controller.py running --
this script opens the same COM port (unlike mcp_debug_server.py, which never touches it).

Deliberately does not import controller.py: no DPI setup, no panic listener, no side effects.
The cursor is read via a local GetCursorPos, and the flush check drives real cursor motion --
keep the mouse still and let it run.

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

        try:
            # --- V: version query -------------------------------------------------------------
            ser.reset_input_buffer()
            send(ser, "V")
            line = ser.readline().decode("ascii", "replace").strip()
            results.append(check("V responds 'HB 3'", line == "HB 3", f"got {line!r}"))

            # --- X: flush stops queued motion early -------------------------------------------
            # Queue 2000 ms of slow rightward motion (10 x 50 px over 200 ms each), flush at
            # ~500 ms. Expect clearly less than the full 500 px, and no further motion afterward.
            start = cursor_pos()
            for _ in range(10):
                send(ser, "S 5000 0 200")
            time.sleep(0.5)
            send(ser, "X")
            time.sleep(0.4)          # let the active segment finish
            mid = cursor_pos()
            time.sleep(0.6)          # would still be moving if the flush was ignored
            end = cursor_pos()
            moved = mid[0] - start[0]
            drift_after = abs(end[0] - mid[0])
            results.append(check("X stopped motion early", 100 <= moved <= 350,
                                 f"moved {moved}px of 500 before stopping"))
            results.append(check("no motion after flush", drift_after <= 2,
                                 f"{drift_after}px after"))
            send(ser, f"S {-(end[0] - start[0]) * 100} {-(end[1] - start[1]) * 100} 300")
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
                dx = abs(now[0] - home[0])
                dy = abs(now[1] - home[1])
                if dx > 3 or dy > 3:
                    # Cursor moved; send one segment to walk it back home
                    send(ser, f"S {-(now[0] - home[0]) * 100} {-(now[1] - home[1]) * 100} 300")
                    time.sleep(0.5)
            except Exception:
                pass  # Suppress exceptions during cleanup to preserve the original exception

    print("\nALL PASS" if all(results) else "\nFAILURES -- see above")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
