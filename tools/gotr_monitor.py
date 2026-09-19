r"""Supervise a live guardians_of_the_rift.py run: watch for safety stops, stuck/idle states,
crashes and (per USER instruction, 2026-09-18) an unexpected loss of window focus -- the operator
will not alt-tab away themselves, so any focus loss is treated as a bug to flag loudly, not a
normal condition to wait out quietly. Restarts the routine automatically on a stop or crash and
confirms it comes back up.

    python tools/gotr_monitor.py

Runs forever (until a stop file appears or the process is killed) -- launch it detached so it
outlives the originating session. Everything it does is written to its own timestamped log under
ControllerAddon/logs/, plus stdout.

Stop it cleanly by creating ControllerAddon/logs/gotr_monitor.stop (any content) -- checked once
per cycle -- or just kill the process; it does not hold the Arduino COM port itself (controller.py
does, and this script only ever starts/stops that process, never opens the port directly).

DIRECT-LOCAL, NOT THE VM SETUP CLAUDE.md DESCRIBES. CLAUDE.md documents controller.py, the
Arduino and the RuneLite client all living inside a VirtualBox guest reached over SSH
(tools/vmexec.py). CHECKED LIVE 2026-09-18 on this machine before writing this script: no
VBoxManage.exe anywhere on the box, no Z:\Httpbridge share, but COM3 answers as an Arduino
Leonardo directly and http://localhost:8080/state answers directly too (already loggedIn, already
windowFocused) -- i.e. everything settings.py already defaults to (SERIAL_PORT=COM3,
API_BASE=http://localhost:8080) with no BRIDGE_SERIAL_PORT/BRIDGE_API_BASE override needed. So
whatever this machine's actual setup is right now, it is a direct one, and this monitor drives
controller.py as a normal local child process (subprocess.Popen) rather than over SSH into a
guest. If a VM is reintroduced later, this needs vmexec-based launch/kill/probe again -- see git
history for the VM-oriented version this replaced.

SIGNAL DESIGN (why each check exists, and why some require TWO agreeing signals before acting):

- Log staleness is the primary crash/hang detector, not a fallback. debug_log.tick(ctx) is the
  FIRST line of controller.py's main loop, unconditionally -- before the login gate, the focus
  gate, the safety-stop continue, the break continue. So with --debug-log on (this monitor always
  launches with it), a live process writes a [snapshot] line every iteration (well under a
  second apart) no matter what state it is in, INCLUDING mid-break and mid-safety-stop. A routine
  whose tick() hangs (blocks forever inside a single call) also goes stale, because the loop never
  comes back around to log again. So "no new bytes for STALE_LOG_S" is high-confidence on its own
  and needs no corroboration.
- "Safety stop: <reason>" is controller.py's own ControllerState.trigger_safety_stop -- the
  process stays alive (paused, printing a reminder every 30s), so this alone would NOT trip the
  staleness detector. Treated as an explicit stop condition.
- "possibly stuck" (debug_log's own stall detector: position AND animation both unchanged for
  STALL_THRESHOLD_S=10s) is logged loudly but does NOT by itself trigger a restart, and repeat
  firings don't either. MEASURED LIVE 2026-09-18: GOTR legitimately holds still for 10+ seconds
  very often (imbuing at the altar, assembling at an essence pile, crafting) -- treating a second
  firing within a few minutes as "confirmed stuck" fired about once every 60-150s of normal play,
  burned through MAX_RESTARTS inside the first 20 minutes, and tripped the restart-storm guard --
  which then blocked the monitor from acting on a REAL safety stop that recurred for the next
  1h45m. A single stationary-action signal is not a reliable stuck indicator for this routine;
  same-spot clicking (below) is the corroborated one that actually catches the documented failure
  mode.
- Same-spot clicking is checked independently, because the built-in stall detector only looks at
  player position/animation, not at what the mouse is being told to do -- and the documented GOTR
  run 39 bug (minimap drawn over the viewport eating every click) had the player NOT moving *and*
  the routine re-clicking one pixel every tick for two minutes, i.e. both signals present at once.
  A run of identical click coordinates (from [action] "... at (x,y)" lines) is only escalated to
  a restart when it is CORROBORATED by frozen [snapshot] position+animation over the same window
  -- clicking a stationary rock repeatedly while mining is normal and must not trip this alone.
- Focus loss is flagged AND corrected. controller.py's own main loop already pauses gracefully
  while unfocused and resumes on its own once focus returns (it never crashes or stalls from
  this alone), so no restart is needed for it -- but it also never gets back on its own without
  a click somewhere. USER, 2026-09-19: "If you run into an issue with window focus you have
  permission to use win32 to refocus again" -- explicit authorization for exactly the
  SetForegroundWindow this monitor previously refused to do on the user's real desktop. See
  refocus_runelite(): reuses controller.py's own find_runelite_window() window-matching
  convention (EnumWindows + a "RuneLite" title substring), tried once per cycle for as long as
  the bridge keeps reporting unfocused, logged either way.

Everything else (bridge unreachable, controller.py process gone) is checked every cycle via the
local process handle and the direct /state poll, and treated as an immediate restart condition --
see ensure_running(). An unexpected logout is the one exception: it gets LOGOUT_GRACE_S to
self-heal via controller.py's own reconnect logic before this monitor restarts anything -- see the
constant's own note.
"""

import collections
import datetime
import glob
import json
import win32gui
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ADDON_DIR = os.path.join(REPO, "ControllerAddon")
LOGS_DIR = os.path.join(ADDON_DIR, "logs")
STOP_FILE = os.path.join(LOGS_DIR, "gotr_monitor.stop")

BRIDGE = "http://localhost:8080"

ROUTINE_ARG = "routines/guardians_of_the_rift.py"
EXTRA_ARGS = ["--debug-log"]

POLL_INTERVAL_S = 30
STALE_LOG_S = 90          # debug_log.tick() fires well under 1s apart whenever the process is alive
BRIDGE_DOWN_GRACE_CYCLES = 3   # ~90s of /state failures before treating the client as gone
FOCUS_LOST_GRACE_S = 5     # matches the engine's own "say so after a few seconds" threshold
PROCESS_START_GRACE_S = 10  # ignore a "process died" reading in the first few seconds after spawn
LOGOUT_GRACE_S = 90        # controller.py's own login.attempt_login retries CONNECTION_LOST on
                           # its own (LOGIN_RETRY_INTERVAL_S-paced) -- CONFIRMED LIVE 2026-09-19:
                           # a transient CONNECTION_LOST self-healed within one retry cycle, but
                           # this monitor restarted the whole process before it got the chance,
                           # burning a restart for nothing a few seconds would have fixed on its
                           # own. Same grace-then-act shape as BRIDGE_DOWN_GRACE_CYCLES.

CLICK_WINDOW_MIN_SPAN_S = 45    # same-spot clicks must span at least this long to count
CLICK_WINDOW_MIN_COUNT = 6
CLICK_TOLERANCE_PX = 3
SNAPSHOT_WINDOW_MIN_COUNT = 6

MAX_RESTARTS = 6
RESTART_WINDOW_S = 3600

UNKNOWN_EXIT_CODE = -1   # _AdoptedProc.poll()'s sentinel -- see its own note on why it is not 0

SAFETY_STOP_RE = re.compile(r"\[!\] Safety stop: (.+)")
STUCK_RE = re.compile(r"\[!\] possibly stuck: (.+)")
EXCEPTION_RE = re.compile(r"EXCEPTION\b|Traceback \(most recent call last\)")
ACTION_CLICK_RE = re.compile(r"\[action\].*?\bat \((-?\d+),\s*(-?\d+)\)")
SNAPSHOT_RE = re.compile(r"\[snapshot\] pos=\((-?\d+),(-?\d+),(-?\d+)\) .*?anim=(-?\d+)")
DEBUG_LOG_PATH_RE = re.compile(r"\[debug_log\] logging this run to (.+\.log)\s*$")

FIND_STRAY_PS = (
    'Get-CimInstance Win32_Process | Where-Object '
    '{ $_.Name -match "python" -and $_.CommandLine -match "controller" } | '
    'Select-Object -ExpandProperty ProcessId'
)


class _AdoptedProc:
    """A controller.py this monitor did not spawn -- started by hand (or by a resume after a
    manual F1 stop, see paused_for_manual_stop) -- given the same .pid/.poll()/.terminate()/.wait()
    shape as a subprocess.Popen so the rest of the code (stop_current, ensure_running) does not
    need to know which kind it has. Liveness and killing both go through the guest-less local
    process table (tasklist/taskkill) since there is no Popen handle to ask."""

    def __init__(self, pid):
        self.pid = pid

    def _alive(self):
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {self.pid}", "/NH"],
                                  capture_output=True, text=True, timeout=10).stdout
        except (subprocess.SubprocessError, OSError):
            return True   # unknown -- don't falsely report it gone
        return str(self.pid) in out

    def poll(self):
        # UNKNOWN_EXIT_CODE, never 0: tasklist cannot report a real exit code, and a hardcoded 0
        # here is actively wrong, not just imprecise -- CONFIRMED LIVE 2026-09-18, it made a
        # deliberate `Stop-Process` (to force-reload a code fix) look EXACTLY like the panic key's
        # os._exit(0) signature, so cycle()'s clean-exit handling paused the monitor instead of
        # relaunching, and it then sat idle until a human noticed. An adopted process's exit
        # reason can never be trusted to be "deliberate manual stop" -- only a process THIS
        # monitor spawned itself (a real Popen, a real .returncode) earns that treatment.
        return None if self._alive() else UNKNOWN_EXIT_CODE

    def terminate(self):
        subprocess.run(["taskkill", "/PID", str(self.pid), "/F"],
                        capture_output=True, text=True, timeout=15)

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        deadline = time.time() + (timeout if timeout is not None else 1e9)
        while time.time() < deadline:
            if not self._alive():
                return
            time.sleep(0.3)
        raise subprocess.TimeoutExpired(cmd=f"PID {self.pid}", timeout=timeout)


class Monitor:
    def __init__(self):
        os.makedirs(LOGS_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = os.path.join(LOGS_DIR, f"gotr_monitor_{stamp}.log")
        self._log_fh = open(self._log_path, "a", encoding="utf-8")

        self.proc = None                 # subprocess.Popen of the current controller.py
        self.proc_started_at = None
        self.routine_log_path = None
        self._routine_log_fh = None      # kept open for the child's stdout/stderr redirect
        self._tail_pos = 0

        # debug_log.py's own file (logs/debug_<timestamp>.log) -- SEPARATE from the redirected
        # stdout above. debug_log.tick()'s per-tick [snapshot] line (position/animation, used to
        # corroborate same-spot clicking) is written with _write_file() directly, not log(), so
        # it never reaches stdout/console at all -- only log()-tagged lines ([action], [!], ...)
        # do both. controller.py prints the debug-log path once at startup ("[debug_log] logging
        # this run to ...") which IS on stdout, so that's how this path is discovered.
        self.debug_log_path = None
        self._debug_tail_pos = 0

        self.restart_times = collections.deque()
        self.restarts_disabled = False

        self.click_history = collections.deque(maxlen=30)     # (wall_time, x, y)
        self.snapshot_history = collections.deque(maxlen=30)  # (wall_time, x, y, plane, anim)

        self.consecutive_bridge_failures = 0
        self.unfocused_since = None
        self.was_focused = True
        self.logged_out_since = None

        # Set on a CLEAN exit (return code 0) -- the panic key's os._exit(0) signature. CONFIRMED
        # 2026-09-18 (USER: "yes I hit F1 to stop it"): auto-restarting over a deliberate manual
        # stop overrides the operator's own intervention, which is worse than doing nothing. While
        # paused, this monitor does not relaunch -- it only watches for controller.py running
        # again (started by hand, or by a future Claude session) and resumes monitoring THAT
        # process once it sees one, rather than starting a competing one of its own.
        self.paused_for_manual_stop = False

    # ---------------------------------------------------------------- logging

    def log(self, tag, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{tag}] {msg}"
        print(line, flush=True)
        self._log_fh.write(line + "\n")
        self._log_fh.flush()

    # ---------------------------------------------------------------- bridge (direct, local)

    def get_state(self):
        try:
            with urllib.request.urlopen(f"{BRIDGE}/state", timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None

    @staticmethod
    def refocus_runelite(title_substring="RuneLite"):
        """Bring the RuneLite window to the foreground. Same window-matching convention as
        controller.py's own find_runelite_window (EnumWindows + a title substring) -- reused
        rather than reinvented. Returns True on a call that didn't raise; SetForegroundWindow
        can still be silently refused by Windows (a background process isn't always allowed to
        steal focus), so the caller re-checks via the next /state poll rather than trusting this
        return value alone."""
        hwnds = []

        def enum_windows(hwnd, _):
            if title_substring in win32gui.GetWindowText(hwnd):
                hwnds.append(hwnd)

        try:
            win32gui.EnumWindows(enum_windows, None)
            if not hwnds:
                return False
            hwnd = hwnds[0]
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, 9)   # SW_RESTORE -- un-minimize before focusing
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- local process control

    def find_stray_pids(self):
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", FIND_STRAY_PS],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            self.log("!", f"stray-process check failed: {exc!r}")
            return []
        return [int(p) for p in out.split() if p.strip().isdigit()]

    def kill_stray_processes(self):
        """Kill any controller.py process this monitor is not already tracking as self.proc --
        a previous crashed/orphaned run, or one started by hand while the monitor wasn't looking.
        Confirms, rather than assumes, the same lesson tools/vm_stop_routine.py documents at
        length for the VM setup: a safety-stopped controller.py stays alive holding the COM port,
        so "I sent a kill" is not the same as "it is actually gone"."""
        tracked_pid = self.proc.pid if self.proc else None
        pids = [p for p in self.find_stray_pids() if p != tracked_pid]
        if not pids:
            return True
        self.log("restart", f"killing stray controller.py process(es): {pids}")
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                            capture_output=True, text=True, timeout=15)
        time.sleep(2)
        remaining = [p for p in self.find_stray_pids() if p != tracked_pid]
        if remaining:
            self.log("!", f"could not confirm stray process(es) gone: {remaining}")
            return False
        return True

    def stop_current(self):
        if self.proc is not None and self.proc.poll() is None:
            self.log("restart", f"terminating controller.py (PID {self.proc.pid})")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.log("!", "process did not exit within 15s of terminate() -- killing")
                self.proc.kill()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.log("!", "process still alive after kill() -- giving up on this PID")
        self.proc = None
        if self._routine_log_fh is not None:
            try:
                self._routine_log_fh.close()
            except OSError:
                pass
            self._routine_log_fh = None
        self.kill_stray_processes()

    # ---------------------------------------------------------------- (re)launch

    def launch(self):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_name = f"gotr_run_{stamp}.log"
        self.routine_log_path = os.path.join(LOGS_DIR, log_name)
        self.log("launch", f"starting: python -u controller.py {ROUTINE_ARG} "
                            f"{' '.join(EXTRA_ARGS)}  (log: {self.routine_log_path})")

        self._routine_log_fh = open(self.routine_log_path, "a", encoding="utf-8", errors="replace")
        try:
            self.proc = subprocess.Popen(
                ["python", "-u", "controller.py", ROUTINE_ARG] + EXTRA_ARGS,
                cwd=ADDON_DIR,
                stdout=self._routine_log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.log("!", f"failed to spawn controller.py: {exc!r}")
            self.proc = None
            return False

        self.proc_started_at = time.time()
        self._tail_pos = 0
        self.debug_log_path = None
        self._debug_tail_pos = 0
        self.click_history.clear()
        self.snapshot_history.clear()
        self.log("launch", f"controller.py started, PID {self.proc.pid}")
        return True

    @staticmethod
    def find_active_run_log():
        """The most recently written gotr_run_*.log -- this monitor is the only thing that ever
        creates that filename pattern, so a freshly-touched one strongly implies it belongs to a
        controller.py that is currently running (adoption path only, see adopt())."""
        candidates = sorted(glob.glob(os.path.join(LOGS_DIR, "gotr_run_*.log")),
                            key=os.path.getmtime, reverse=True)
        for path in candidates:
            if time.time() - os.path.getmtime(path) < 90:
                return path
        return None

    def adopt(self, pid):
        """Pick up an already-running controller.py this monitor did not start -- the resume path
        after a manual F1 stop (paused_for_manual_stop). Tails its log from EOF, not from the
        start, so it does not replay old events (an old safety stop, old clicks) as if they just
        happened."""
        log_path = self.find_active_run_log()
        if not log_path:
            return False
        self.proc = _AdoptedProc(pid)
        self.proc_started_at = time.time() - PROCESS_START_GRACE_S - 1
        self.routine_log_path = log_path
        self._tail_pos = os.path.getsize(log_path)
        self.debug_log_path = None
        self._debug_tail_pos = 0
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            head = ""
        m = DEBUG_LOG_PATH_RE.search(head)
        if m:
            self.debug_log_path = m.group(1).strip()
            if os.path.exists(self.debug_log_path):
                self._debug_tail_pos = os.path.getsize(self.debug_log_path)
        self.click_history.clear()
        self.snapshot_history.clear()
        self.log("launch", f"adopted already-running controller.py, PID {pid}, log {log_path}")
        return True

    def record_restart(self):
        now = time.time()
        self.restart_times.append(now)
        while self.restart_times and now - self.restart_times[0] > RESTART_WINDOW_S:
            self.restart_times.popleft()
        if len(self.restart_times) > MAX_RESTARTS:
            self.restarts_disabled = True
            self.log("!", f"CRITICAL: {len(self.restart_times)} restarts within "
                           f"{RESTART_WINDOW_S / 60:.0f} min -- this looks like a persistent "
                           "problem, not a transient one. Disabling further auto-restarts. "
                           "A human needs to look at this run.")

    def ensure_running(self, reason):
        if self.restarts_disabled:
            self.log("!", f"would restart ({reason}) but auto-restart is disabled -- see above")
            return False
        self.log("restart", f"restarting -- reason: {reason}")
        self.stop_current()
        time.sleep(2)
        ok = self.launch()
        self.record_restart()
        if ok:
            self.log("restart", "controller.py relaunched")
        else:
            self.log("!", "restart attempt did not spawn a new process -- will retry next cycle")
        return ok

    # ---------------------------------------------------------------- log tailing

    def scan_new_lines(self):
        if not self.routine_log_path or not os.path.exists(self.routine_log_path):
            return []
        try:
            with open(self.routine_log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._tail_pos)
                new_text = fh.read()
                self._tail_pos = fh.tell()
        except OSError as exc:
            self.log("!", f"could not read routine log: {exc!r}")
            return []
        if not new_text:
            return []
        return new_text.splitlines()

    def scan_debug_log_snapshots(self):
        """Tail debug_log.py's own file for [snapshot] lines -- see the note in __init__ on why
        this is a second file, not a second read of the same one."""
        if not self.debug_log_path or not os.path.exists(self.debug_log_path):
            return
        try:
            with open(self.debug_log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._debug_tail_pos)
                new_text = fh.read()
                self._debug_tail_pos = fh.tell()
        except OSError as exc:
            self.log("!", f"could not read debug log: {exc!r}")
            return
        if not new_text:
            return
        now = time.time()
        for line in new_text.splitlines():
            m = SNAPSHOT_RE.search(line)
            if m:
                self.snapshot_history.append(
                    (now, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))))

    def process_new_lines(self, lines):
        now = time.time()
        events = {"safety_stop": None, "exception": False}

        for line in lines:
            if self.debug_log_path is None:
                m = DEBUG_LOG_PATH_RE.search(line)
                if m:
                    self.debug_log_path = m.group(1).strip()
                    self.log("launch", f"debug log: {self.debug_log_path}")

            m = SAFETY_STOP_RE.search(line)
            if m:
                events["safety_stop"] = m.group(1)

            if EXCEPTION_RE.search(line):
                events["exception"] = True

            m = STUCK_RE.search(line)
            if m:
                # Informational only -- see the module docstring on why a repeat firing of this
                # alone is not treated as confirmed-stuck for this routine.
                self.log("stall", m.group(1))

            m = ACTION_CLICK_RE.search(line)
            if m:
                self.click_history.append((now, int(m.group(1)), int(m.group(2))))

            m = SNAPSHOT_RE.search(line)
            if m:
                self.snapshot_history.append(
                    (now, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))))

        return events

    def same_spot_click_confirmed(self):
        """Same-spot clicking corroborated by a frozen player position+animation over the same
        window -- see the module docstring for why this needs two signals, not one."""
        if len(self.click_history) < CLICK_WINDOW_MIN_COUNT:
            return False
        recent = list(self.click_history)[-CLICK_WINDOW_MIN_COUNT:]
        span = recent[-1][0] - recent[0][0]
        if span < CLICK_WINDOW_MIN_SPAN_S:
            return False
        xs = [p[1] for p in recent]
        ys = [p[2] for p in recent]
        if max(xs) - min(xs) > CLICK_TOLERANCE_PX or max(ys) - min(ys) > CLICK_TOLERANCE_PX:
            return False

        if len(self.snapshot_history) < SNAPSHOT_WINDOW_MIN_COUNT:
            return False
        window_start = recent[0][0]
        snaps_in_window = [s for s in self.snapshot_history if s[0] >= window_start]
        if len(snaps_in_window) < SNAPSHOT_WINDOW_MIN_COUNT:
            return False
        positions = {(s[1], s[2], s[3]) for s in snaps_in_window}
        anims = {s[4] for s in snaps_in_window}
        return len(positions) == 1 and len(anims) == 1

    # ---------------------------------------------------------------- one cycle

    def cycle(self):
        if self.paused_for_manual_stop:
            pids = self.find_stray_pids()
            if pids:
                if len(pids) > 1:
                    self.log("!", f"multiple controller.py processes found while paused "
                                   f"({pids}) -- adopting the first, watch for a duplicate")
                if self.adopt(pids[0]):
                    self.paused_for_manual_stop = False
                    self.log("monitor", "controller.py is running again -- resuming monitoring")
                else:
                    self.log("!", f"PID {pids[0]} is running but its log could not be found -- "
                                   "still paused, will keep checking")
            else:
                self.log("monitor", "paused (manual stop) -- waiting for controller.py to be "
                                     "started again; not relaunching it myself")
                return

        if self.proc is not None:
            rc = self.proc.poll()
            if rc is not None and time.time() - self.proc_started_at > PROCESS_START_GRACE_S:
                if rc == 0 and isinstance(self.proc, subprocess.Popen):
                    # os._exit(0) is the panic key's exact signature. CONFIRMED 2026-09-18 (USER:
                    # "yes I hit F1 to stop it") -- auto-restarting here would override a
                    # deliberate manual stop, which is worse than doing nothing. Pause instead;
                    # the top of this method picks it back up once controller.py runs again.
                    self.log("!", "controller.py exited cleanly (return code 0) -- this is the "
                                   "panic key's signature, not a crash. Treating it as a "
                                   "deliberate manual stop and pausing rather than restarting.")
                    self.proc = None
                    self.paused_for_manual_stop = True
                    return
                self.log("!", f"controller.py exited on its own (return code {rc})")
                self.ensure_running(f"process exited with code {rc}")
                return

        state = self.get_state()

        if state is None:
            self.consecutive_bridge_failures += 1
            self.log("!", f"bridge not answering on {BRIDGE} "
                           f"({self.consecutive_bridge_failures}/{BRIDGE_DOWN_GRACE_CYCLES})")
            if self.consecutive_bridge_failures >= BRIDGE_DOWN_GRACE_CYCLES:
                self.log("!", "bridge still down -- this is the RuneLite client's own HTTP "
                               "server, which this monitor cannot restart on its own (the client "
                               "is not something it launched). Flagging, not restarting "
                               "controller.py for this -- a dead client needs a human to relaunch "
                               "RuneLite.")
                self.consecutive_bridge_failures = 0
            return
        self.consecutive_bridge_failures = 0

        focused = bool(state.get("windowFocused"))
        logged_in = bool(state.get("loggedIn"))

        if not focused:
            if self.unfocused_since is None:
                self.unfocused_since = time.time()
            elapsed = time.time() - self.unfocused_since
            if elapsed >= FOCUS_LOST_GRACE_S:
                self.log("!", f"BUG: RuneLite window has lost focus (unfocused for "
                               f"{elapsed:.0f}s) -- this was not expected to happen. "
                               "Refocusing it (user-authorized 2026-09-19).")
                refocused = self.refocus_runelite()
                self.log("focus", f"SetForegroundWindow call {'issued' if refocused else 'FAILED (window not found or call raised)'} "
                                   "-- confirming on the next /state poll")
        else:
            if not self.was_focused:
                self.log("focus", "window focus recovered")
            self.unfocused_since = None
        self.was_focused = focused

        if not logged_in:
            if self.logged_out_since is None:
                self.logged_out_since = time.time()
                self.log("!", "unexpected logout detected (was logged in, now is not) -- "
                               f"giving controller.py's own reconnect logic {LOGOUT_GRACE_S:.0f}s "
                               "before restarting")
            elif time.time() - self.logged_out_since >= LOGOUT_GRACE_S:
                self.log("!", f"still logged out after {LOGOUT_GRACE_S:.0f}s -- restarting")
                self.ensure_running("logout did not self-heal within the grace window")
                self.logged_out_since = None
                return
        else:
            if self.logged_out_since is not None:
                self.log("!", f"logged back in on its own after "
                               f"{time.time() - self.logged_out_since:.0f}s -- no restart needed")
            self.logged_out_since = None

        lines = self.scan_new_lines()
        if lines:
            events = self.process_new_lines(lines)
        else:
            events = {"safety_stop": None, "exception": False}
        self.scan_debug_log_snapshots()

        if events["safety_stop"]:
            self.ensure_running(f"safety stop: {events['safety_stop']}")
            return
        if events["exception"]:
            self.ensure_running("uncaught exception in the routine log")
            return
        if self.same_spot_click_confirmed():
            self.log("!", "confirmed stuck: identical click coordinates with frozen "
                           "position+animation over the same window")
            self.ensure_running("same-spot clicking with no player movement")
            return

        # Staleness must be judged against the DEBUG log, not the redirected-stdout one: a
        # legitimate break (3-12 minutes, config-controlled) produces no stdout output at all for
        # its whole duration -- only debug_log.tick()'s per-iteration [snapshot] line (written to
        # its own file, see scan_debug_log_snapshots) proves the loop is still turning over. Before
        # that path is known (the first couple of seconds of startup) fall back to the stdout log,
        # which is the only thing written yet.
        stale_check_path = self.debug_log_path or self.routine_log_path
        if stale_check_path and os.path.exists(stale_check_path):
            mtime = os.path.getmtime(stale_check_path)
            age = time.time() - mtime
            since_start = time.time() - (self.proc_started_at or 0)
            if age > STALE_LOG_S and since_start > STALE_LOG_S:
                self.ensure_running(f"log has not grown in {age:.0f}s "
                                     f"(> {STALE_LOG_S}s threshold) -- hung or dead process")
                return

        self.log("cycle", f"ok -- focused={focused} loggedIn={logged_in} "
                           f"pid={self.proc.pid if self.proc else None} "
                           f"restarts_last_hour={len(self.restart_times)}")

    # ---------------------------------------------------------------- main loop

    def run(self):
        self.log("monitor", f"starting -- routine={ROUTINE_ARG} extra_args={EXTRA_ARGS} "
                             f"poll_interval={POLL_INTERVAL_S}s monitor_log={self._log_path}")

        # Adopt a single already-running, still-logging controller.py rather than killing it --
        # this monitor may itself be getting restarted to pick up a code fix while a perfectly
        # healthy session is mid-run (happened twice on 2026-09-18), and killing that gratuitously
        # loses real progress for no reason. Ambiguous cases (more than one candidate, or one with
        # no findable active log) fall back to the old, safe default: kill everything and start
        # clean, same as always.
        pids = self.find_stray_pids()
        adopted = len(pids) == 1 and self.adopt(pids[0])
        if pids and not adopted:
            self.log("!", f"found controller.py process(es) {pids} but could not cleanly adopt "
                           "one -- killing and starting fresh instead")
        if not adopted:
            self.kill_stray_processes()
            if not self.launch():
                self.log("!", "initial launch failed -- will keep retrying every cycle until it "
                               "succeeds or the restart-storm guard trips")
                self.record_restart()

        while True:
            if os.path.exists(STOP_FILE):
                self.log("monitor", f"stop file found ({STOP_FILE}) -- exiting cleanly")
                try:
                    os.remove(STOP_FILE)
                except OSError:
                    pass
                return
            try:
                self.cycle()
            except Exception as exc:
                import traceback
                self.log("!", f"monitor cycle raised an unexpected exception: {exc!r}")
                self._log_fh.write(traceback.format_exc() + "\n")
                self._log_fh.flush()
            time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    Monitor().run()
