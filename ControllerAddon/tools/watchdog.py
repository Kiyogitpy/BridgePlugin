"""Unattended watchdog for a running controller.py routine (2026-09-13, user request:
"leave it running with a monitor for safety stops, bugs, idle etc and make sure it doesnt
get stuck").

READ-ONLY as far as the game is concerned: it polls the HTTP bridge and reads the routine's
log file. It never opens the serial port and never sends input, the same rationale as
mcp_debug_server.py/recorder.py -- so it is safe to run alongside controller.py, and it adds
no detection surface of its own.

    python -u tools/watchdog.py --log <routine-log> [--restart]

What it reports (each state change once, never repeated every poll):
  * the controller process dying          -- and relaunches it when --restart is given
  * a safety stop, traceback, or panic    -- scraped from the log
  * XP not moving for --idle-s            -- the real "is it actually working" signal, taken
                                             from /player rather than inferred from the log
  * the bridge going unreachable          -- client closed or plugin restarted
  * the window losing focus               -- the engine parks itself here, which looks like a
                                             hang but is not one

Writes a compact ALERT/OK line per event to stdout and to <log>.alerts, so a later reader can
reconstruct the run without scrolling the routine's own very chatty log.
"""

import argparse
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402  (after the path fix-up)

BASE = "http://localhost:8080"
POLL_S = 15.0

# Lines worth waking a human for. Deliberately NOT every "[!]": the deob-guard notice and the
# focus notices fire on a perfectly healthy run.
BAD_LINE = re.compile(
    r"(safety stop|safety_stop|Traceback|Unhandled|panic|SerialException|"
    r"could not open|no hammer|did not register)",
    re.IGNORECASE,
)


def log_alert(path, msg):
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def controller_running():
    """Is a controller.py process alive? tasklist rather than psutil (not a dependency here)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*controller.py*' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=20)
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception:
        return True          # never restart on a failed check -- that risks a second driver


def player():
    try:
        return requests.get(BASE + "/player", timeout=3).json() or {}
    except Exception:
        return None


def total_xp(p):
    """Sum every *Xp field /player exposes: any skilling progress at all moves this."""
    return sum(v for k, v in (p or {}).items()
               if k.endswith("Xp") and isinstance(v, (int, float)))


def focused():
    try:
        return bool(requests.get(BASE + "/state", timeout=3).json().get("windowFocused"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="the routine log to scrape")
    ap.add_argument("--idle-s", type=float, default=300.0,
                    help="alert when total XP has not moved for this long (default 300)")
    ap.add_argument("--restart", action="store_true",
                    help="relaunch controller.py if the process disappears")
    ap.add_argument("--routine", default="routines/motherlode_mine.py")
    args = ap.parse_args()

    alerts = args.log + ".alerts"
    log_alert(alerts, f"watchdog up: log={args.log} idle-s={args.idle_s} restart={args.restart}")

    pos = 0
    if os.path.exists(args.log):
        pos = os.path.getsize(args.log)      # only NEW lines matter

    last_xp = None
    last_xp_move = time.monotonic()
    warned_idle = False
    warned_down = False
    warned_bridge = False
    warned_focus = False

    while True:
        # --- the log ------------------------------------------------------------------
        try:
            size = os.path.getsize(args.log)
            if size < pos:                   # rotated/recreated
                pos = 0
            if size > pos:
                with open(args.log, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                for line in chunk.splitlines():
                    if BAD_LINE.search(line):
                        log_alert(alerts, "ALERT log: " + line.strip()[:200])
        except OSError:
            pass

        # --- the bridge ---------------------------------------------------------------
        p = player()
        if p is None:
            if not warned_bridge:
                warned_bridge = True
                log_alert(alerts, "ALERT bridge unreachable (client closed or plugin restarted?)")
        else:
            if warned_bridge:
                warned_bridge = False
                log_alert(alerts, "OK bridge back")

            xp = total_xp(p)
            if last_xp is None or xp != last_xp:
                if warned_idle:
                    log_alert(alerts, f"OK xp moving again (+{xp - (last_xp or xp):.0f})")
                    warned_idle = False
                last_xp, last_xp_move = xp, time.monotonic()
            elif not warned_idle and time.monotonic() - last_xp_move > args.idle_s:
                warned_idle = True
                log_alert(alerts, f"ALERT no xp gain for {args.idle_s:.0f}s -- possibly stuck "
                                  f"(pos={p.get('x')},{p.get('y')} plane={p.get('plane')})")

        f = focused()
        if f is False and not warned_focus:
            warned_focus = True
            log_alert(alerts, "note: RuneLite not focused -- the engine parks here by design")
        elif f and warned_focus:
            warned_focus = False
            log_alert(alerts, "OK window focused again")

        # --- the process --------------------------------------------------------------
        if not controller_running():
            if not warned_down:
                warned_down = True
                log_alert(alerts, "ALERT controller.py is NOT running")
            if args.restart:
                log_alert(alerts, f"restarting {args.routine}")
                try:
                    with open(args.log, "a", encoding="utf-8") as out:
                        subprocess.Popen(
                            [sys.executable, "-u", "controller.py", args.routine, "--debug-log"],
                            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            stdout=out, stderr=subprocess.STDOUT)
                    time.sleep(20)           # let it claim the serial port before re-checking
                    warned_down = False
                except Exception as exc:
                    log_alert(alerts, f"ALERT restart failed: {exc}")
        elif warned_down:
            warned_down = False
            log_alert(alerts, "OK controller.py running again")

        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
