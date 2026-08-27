# Firmware v3: Retractable Motion Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a queue-flush command to the Arduino firmware so a mid-flight re-aim can retract queued-but-unplayed motion (shrinking the irrevocable commit window from ~54ms to ~20ms), and future-proof the firmware protocol while we are paying the reflash cost: version query, scroll wheel, release-all reset, and a stuck-input watchdog.

**Architecture:** The firmware gains protocol v3 — four additive commands (`X` flush, `V` version query, `R` reset, `W` wheel) plus a watchdog; every v1/v2 command is untouched and unknown lines are still silently ignored, so old firmware and new Python coexist in either direction. Python probes the firmware version once at startup and only ever sends `X` when the probe confirmed v3; `human_move.move()` keeps a per-emission timeline log so that after a flush it can rewind its position/time bookkeeping to the last segment that actually plays.

**Tech Stack:** Arduino C++ (ATmega32U4, Mouse/Keyboard HID libraries), Python 3 (pyserial, pytest).

**Spec:** No separate spec file — bounded-path design agreed in chat 2026-08-27. The Design Summary section below is the authoritative statement of the design; the plan argues from it.

## Design Summary

Why this exists: `human_move.move()` queues segments `HM2_LOOKAHEAD_SEGMENTS` (3) × `HM2_SEGMENT_MS` (18ms) ≈ 54ms ahead of real time so a Python scheduler hiccup never starves the firmware (starvation = a mid-flight cursor freeze that wrecks the tuned velocity profile). But everything queued is irrevocable, so a re-aim decision toward a moving target is committed to up to 54ms of motion toward the *old* target. A flush command decouples the two concerns: smoothness can keep (or later deepen) the lookahead while a replan retracts what hasn't played.

Key firmware facts the design leans on (all verified against `firmware/Firmware.ino` 2026-08-27):

- The **active segment executes in place at `queue[q_head]`** (`serviceMotion` reads `queue[q_head]` every pass; `endSegment` pops it). A flush must therefore keep the head entry whenever `seg_active` is true — evicting it mid-play would interpolate from a freed slot.
- `handleLine` **silently ignores unknown lines**, so sending v3 commands to un-reflashed firmware degrades to a no-op. This is why the Python side must *gate on the probe*, not on graceful degradation: an ignored `X` means the queued motion plays, and Python's rewound bookkeeping would then be wrong.
- The firmware **never writes to serial unsolicited** (CDC `Serial.write` stalls when the host never drains — header comment, lines 39-41). `V` is the one solicited exception: a single short response cannot fill the 64-byte CDC TX buffer, so it cannot stall even against a host that never reads.
- Backpressure: `pumpSerial` runs only while `q_free() >= QUEUE_RESERVE` (3). With Python's 3-segment lookahead the queue holds ~4 entries of 16, so an `X` is read within one loop pass. If lookahead is ever deepened near 13+, an `X` can wait up to one segment (~18ms) for a slot to free. Bounded, and documented in the firmware header.
- Flush timing model, Python side: when `X` is sent at elapsed time `E` (ms since `t0`), serial+parse latency (~2-4ms) plus the active segment (≤18ms, always finishes) means motion up to roughly `E + HM2_FLUSH_COMMIT_MS` (22ms) still plays; everything queued beyond that is dropped. `move()` rewinds `cur`/`queued_ms` to the first logged emission at or past that commit point.

Safety rules established in design:

1. **Never flush across a queued click.** `X` drops queued button/key commands too. The contract: `X` is only legal inside `move()`'s flight loop, where only segments are ever queued. Stated in both the firmware header and the Python docstring.
2. **A flushed flight must not feed `_learn_scale`.** Emitted units include flushed (never-played) motion, so learning from such a flight would poison the persisted `.hm2_calibration.json`. `move()` skips learning when any flush occurred.
3. **The watchdog only fires when something is held.** Trigger = (a mouse button or key is down) AND (no serial line for 60s). Breaks (3-12 min of silence) hold nothing, so they never trip it; the longest legitimate hold (SHIFT around a walk click) is a few seconds of a flight that is constantly sending segments. What it protects against: the F1 panic path is a hard `os._exit` by design — a button or SHIFT held at that instant currently stays down *forever*.

Out of scope (separate follow-up work, agreed ordering in chat): the residual-correction-loop `refresh_target` re-read, intra-submovement re-aim checks, predictive target leading, and any `HM2_LOOKAHEAD_SEGMENTS` retuning.

## Global Constraints

- All commits on branch `dev`. Every commit message ends with the line: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Firmware: AVR `int` is 16-bit — any parsed value goes through the existing `parseLong` (`int32_t`). The interpolator's `(total_q * elapsed_ms)` int32 bound means `MAX_SEG_Q`/`MAX_SEG_MS` must not be raised.
- Firmware: protocol changes are ADDITIVE only. Every v1/v2 command keeps its exact meaning. Unknown lines stay silently ignored.
- Firmware: no unsolicited serial writes, ever. `V`'s single-line solicited response is the only write in the file.
- Python: `X` is sent only when `probe_firmware_protocol` returned >= 3, and only from inside `move()`'s flight loop. Every new Python behavior must degrade to today's exact behavior against pre-v3 firmware.
- Python tests: run `python -m pytest tests/ -q` from `ControllerAddon/`. Never let a test write the real `.hm2_calibration.json` (the existing autouse `_isolate_calibration` fixture in `tests/test_human_move.py` handles this — new move() tests belong in that file).
- Before any live run: `python -m py_compile controller.py human_move.py settings.py firmware_bench.py` from `ControllerAddon/` (pytest alone does not import every module).
- Do not modify `HM2_LOOKAHEAD_SEGMENTS`, `HM2_SEGMENT_MS`, or any measured-distribution constant in `settings.py`.

---

### Task 1: Firmware — `X` flush and `V` version query (protocol v3 core)

**Files:**
- Modify: `firmware/Firmware.ino`

**Interfaces:**
- Produces: serial command `X` (immediate: drop all queued entries, active segment finishes), serial command `V` (immediate: respond `HB 3\n`). Task 5's Python probe parses exactly the string `HB 3`.

There is no firmware unit-test harness; verification is Task 3's bench script run at the Task 4 flash checkpoint. This task must still compile-check if `arduino-cli` is available.

- [ ] **Step 1: Update the protocol header comment**

In the big header comment block (lines 4-42), after the v2 paragraph, add:

```cpp
// PROTOCOL v3 (2026-08-27). Again purely ADDITIVE -- v1/v2 commands are untouched, and a v2
// firmware silently ignores every v3 command, so either side can be upgraded first. The Python
// side MUST feature-detect via `V` before relying on `X` (an ignored X means queued motion
// plays anyway, which silently corrupts the sender's position bookkeeping).
//
//   X    Flush: discard every queued command NOW. The active segment always finishes (it is
//        executed in place at queue[q_head]; see flushQueue). Immediate, not queued -- a queued
//        flush could not retract anything ahead of itself. NOTE: X drops queued button/key
//        commands too, so the sender must only flush while nothing but segments are queued
//        (controller.py only sends it from inside a mouse flight). Worst-case latency: while
//        the queue lacks QUEUE_RESERVE free slots, serial is not read, so an X can wait up to
//        one segment (~18 ms) before being seen.
//   V    Version query. Responds "HB 3\n" -- the ONE solicited serial write in this firmware.
//        A single short line cannot fill the 64-byte CDC TX buffer, so it cannot stall even if
//        the host never reads it; the never-write rule below still holds for UNSOLICITED output.
//   R    Reset: flush everything INCLUDING the active segment, zero the sub-pixel carry and
//        chain phase, release every held mouse button and key. For startup cleanup after a
//        crashed sender and for safety tooling.
//   W <n>  Scroll wheel, n detents (positive = away from user), clamped to +/-127. Queued, so
//          it keeps program order with motion and clicks like every other queued command.
//
// WATCHDOG (v3): if any button/key is held and no serial line has arrived for 60 s, everything
// is released. The panic path on the host is a hard process kill that cannot send key-ups; this
// is the only thing standing between that and a button held forever. It never fires when
// nothing is held, so multi-minute idle periods (scheduled breaks) are unaffected.
```

- [ ] **Step 2: Implement `flushQueue()` and the `X`/`V` handling**

Add near the queue helpers (after `q_pop`, around line 179):

```cpp
// Drop everything queued but NEVER the active segment: serviceMotion executes the active
// command in place at queue[q_head] and endSegment pops it, so evicting the head mid-play
// would leave the interpolator reading a slot q_push is free to overwrite.
static void flushQueue() {
  q_count = seg_active ? 1 : 0;
}
```

At the very top of `handleLine` (immediately after the `if (!line[0]) return;`), add the immediate commands — before every queued-command parse, so they act now rather than in queue order:

```cpp
  // v3 immediate commands -- these act NOW, not in queue order. X exists to retract queued
  // motion, so queueing it would defeat its purpose; V is a pure query.
  if (line[0] == 'X' && !line[1]) {
    flushQueue();
    return;
  }
  if (line[0] == 'V' && !line[1]) {
    Serial.print("HB 3\n");   // solicited, single short line: cannot stall (see header)
    return;
  }
```

- [ ] **Step 3: Compile-check if arduino-cli is available**

Run (PowerShell, repo root): `arduino-cli version`
- If it exists: `arduino-cli compile --fqbn arduino:avr:leonardo firmware` — expect a clean compile.
- If the command is not found: note it in the commit body; the compile check happens in the Arduino IDE at the Task 4 flash checkpoint instead. Do NOT install arduino-cli unprompted.

- [ ] **Step 4: Commit**

```powershell
git add firmware/Firmware.ino
git commit -m @'
firmware: protocol v3 core -- X (flush queued motion) and V (version query)

X lets the Python mover retract queued-but-unplayed segments when a mid-flight
re-aim changes the plan, shrinking the irrevocable commit window from ~54ms
(3-segment lookahead) to ~20ms (serial latency + the active segment, which
always finishes -- it executes in place at queue[q_head] and must not be
evicted). V gives the host a way to feature-detect v3: a pre-v3 firmware
ignores unknown lines, and an ignored X would silently corrupt the sender's
position bookkeeping, so Python gates flushing on this probe.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

---

### Task 2: Firmware — held-input tracking, `R` reset, `W` wheel, stuck-input watchdog

**Files:**
- Modify: `firmware/Firmware.ino`

**Interfaces:**
- Consumes: `flushQueue()` from Task 1.
- Produces: serial commands `R` (immediate release-all + full reset) and `W <n>` (queued wheel scroll); automatic release of held inputs after 60s of serial silence. Task 5 sends `R` once at startup; Task 3's bench exercises all of it.

- [ ] **Step 1: Add held-input state and the wheel command type**

Next to the other live-state globals (after `carry_x_q/carry_y_q`, around line 86):

```cpp
// v3: what WE are currently holding down, for R and the stuck-input watchdog. Mouse buttons
// are the MOUSE_* bitmask values (1/2/4), so a mask works; keys are just a depth counter --
// enough to know "something is held", which is all the watchdog needs.
static uint8_t held_buttons = 0;
static uint8_t held_keys = 0;
static uint32_t last_line_us = 0;
static const uint32_t STUCK_INPUT_TIMEOUT_US = 60000000UL;  // 60 s; longest legit hold is ~5 s
```

Extend the command-type enum (line 62):

```cpp
enum : uint8_t { CMD_SEG = 0, CMD_BTN = 1, CMD_KEY = 2, CMD_WHEEL = 3 };
```

- [ ] **Step 2: Implement `releaseAllInputs()` and the `R` command**

Below `flushQueue()`:

```cpp
static void releaseAllInputs() {
  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
  Mouse.release(MOUSE_MIDDLE);
  Keyboard.releaseAll();
  held_buttons = 0;
  held_keys = 0;
}
```

In `handleLine`, alongside the Task 1 immediate commands:

```cpp
  if (line[0] == 'R' && !line[1]) {
    // Hard reset: unlike X, this abandons the ACTIVE segment too, and forgets the sub-pixel
    // carry and chain phase -- the whole point is a clean slate after a crashed sender, and
    // chaining onto a dead session's timeline would be wrong. q_head is left as-is: with
    // q_count = 0 and seg_active false, no path reads the abandoned slot.
    q_count = 0;
    seg_active = false;
    carry_x_q = 0;
    carry_y_q = 0;
    chain_valid = false;
    releaseAllInputs();
    return;
  }
```

- [ ] **Step 3: Implement `W` parsing and execution**

In `handleLine`, next to the `S` parser (note this must come BEFORE any future single-char `W` — and note the guard mirrors `S`'s `line[1]` space check so a bare `W` is ignored):

```cpp
  // "W <n>" -- scroll wheel, n detents. Queued so it keeps program order with motion/clicks.
  if (line[0] == 'W' && (line[1] == ' ' || line[1] == '\t')) {
    const char *p = line + 1;
    bool ok;
    int32_t n = parseLong(p, ok);
    if (ok && n != 0) {
      q_push(CMD_WHEEL, 0, 0, 0, (uint8_t)(int8_t)clampLong(n, -127, 127), 0);
    }
    return;
  }
```

In `serviceQueue`, extend the non-motion drain (the `if (c.type == CMD_BTN) ... else if (c.type == CMD_KEY) ...` chain):

```cpp
    } else if (c.type == CMD_WHEEL) {
      Mouse.move(0, 0, (int8_t)c.p0);
    }
```

- [ ] **Step 4: Track held inputs at execution time**

Still in `serviceQueue`, the button and key branches become:

```cpp
    if (c.type == CMD_BTN) {
      if (c.p1 == BTN_PRESS) {
        Mouse.press(c.p0);
        held_buttons |= c.p0;
      } else {
        Mouse.release(c.p0);
        held_buttons &= (uint8_t)~c.p0;
      }
    } else if (c.type == CMD_KEY) {
      if (c.p1 == KEY_WRITE) {
        Keyboard.write(c.p0);
      } else if (c.p1 == KEY_PRESS) {
        Keyboard.press(c.p0);
        if (held_keys < 255) held_keys++;
      } else {
        Keyboard.release(c.p0);
        if (held_keys) held_keys--;
      }
    }
```

- [ ] **Step 5: Feed the watchdog and add it to `loop()`**

In `pumpSerial`, inside the `if (c == '\n')` branch, before `handleLine(buf)`:

```cpp
      last_line_us = micros();
```

In `loop()`, after `serviceMotion();`:

```cpp
  // Stuck-input watchdog (v3). The host's panic key is a hard process kill that cannot send
  // key-ups, so anything held at that instant would otherwise stay down FOREVER. Fires only
  // when something is actually held: breaks (minutes of silence, nothing held) never trip it,
  // and any legitimate hold happens inside a flight that streams segments constantly.
  // micros() wraps every ~71 min; the unsigned subtraction handles the wrap.
  if ((held_buttons || held_keys) &&
      (uint32_t)(micros() - last_line_us) > STUCK_INPUT_TIMEOUT_US) {
    releaseAllInputs();
  }
```

- [ ] **Step 6: Compile-check (same as Task 1 Step 3) and commit**

```powershell
git add firmware/Firmware.ino
git commit -m @'
firmware: v3 safety and future-proofing -- R reset, W wheel, stuck-input watchdog

R releases everything and abandons all motion state, giving the host a clean
slate after a crashed predecessor (controller.py will send it once at startup).
W queues a scroll-wheel report, closing the one HID capability the protocol
lacked. The watchdog releases held buttons/keys after 60s of serial silence:
the F1 panic path is a hard os._exit that cannot send releases, and this is
the only backstop. It fires only while something is held, so multi-minute
scheduled breaks never trip it.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

---

### Task 3: Bench-test script for flash-day verification

**Files:**
- Create: `ControllerAddon/firmware_bench.py`

**Interfaces:**
- Consumes: firmware commands `V`, `X`, `R`, `W`, `S`, `KD`/`KU` (Tasks 1-2); `SERIAL_PORT`/`SERIAL_BAUD` from `settings.py`.
- Produces: a manual PASS/FAIL checklist run at the Task 4 checkpoint, and kept in the repo for every future reflash.

Like `recorder.py` and `mcp_debug_server.py`, this deliberately does NOT import `controller.py` (no import-time side effects) — but unlike them it DOES open the serial port, so it must never run while `controller.py` is running. It says so, loudly, at startup.

- [ ] **Step 1: Write the script**

```python
"""Bench checks for firmware/Firmware.ino protocol v3 -- run after every reflash.

    python firmware_bench.py            # from ControllerAddon/

Exercises V (version), X (flush), R (release-all), W (wheel) against the live device and
prints PASS/FAIL per check. Requires the Arduino connected and NO controller.py running --
this script opens the same COM port (unlike mcp_debug_server.py, which never touches it).

Deliberately does not import controller.py: no DPI setup, no panic listener, no side effects.
The cursor is read via a local GetCursorPos, and the flush check drives real cursor motion --
keep the mouse still and let it run.
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

    print("\nALL PASS" if all(results) else "\nFAILURES -- see above")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Compile-check it**

Run from `ControllerAddon/`: `python -m py_compile firmware_bench.py` — expect silence.

(It cannot be *run* yet: the connected device still speaks v2 until Task 4. Running it now against v2 firmware is harmless but every check will fail — that is the expected pre-flash state, not a bug.)

- [ ] **Step 3: Commit**

```powershell
git add ControllerAddon/firmware_bench.py
git commit -m @'
Add firmware_bench.py -- flash-day verification for firmware protocol v3

Standalone (does not import controller.py, same rationale as recorder.py) but
DOES open the COM port, so it must never run alongside controller.py. Kept in
the repo so every future reflash gets the same checklist.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

---

### Task 4: CHECKPOINT — flash the firmware and run the bench (needs the human)

**Files:** none (hardware step).

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: a device that answers `HB 3` — the precondition for the Python side ever sending `X` live. Tasks 5-7 are safe to land BEFORE this checkpoint (the probe gates everything), but no live flush behavior exists until it passes.

- [ ] **Step 1: Ask the user to flash**

Confirm no `controller.py` is running (`Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"`), then ask the user to flash `firmware/Firmware.ino` to the Pro Micro/Leonardo via the Arduino IDE (or `arduino-cli upload --fqbn arduino:avr:leonardo -p COM6 firmware` if arduino-cli is set up). This is a manual hardware step — wait for their confirmation.

- [ ] **Step 2: Run the bench**

From `ControllerAddon/`: `python firmware_bench.py` — expect `ALL PASS`. If time allows, rerun once with `--watchdog`. If the flush check FAILS (motion doesn't stop): stop here — do not proceed to enabling anything live until it passes, and debug on the bench, not in a routine.

---

### Task 5: Python — firmware version probe, startup reset, wheel helper

**Files:**
- Modify: `ControllerAddon/controller.py` (near `send_line`, line ~1153, and in `main()` at the `serial.Serial` open, line ~2808)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: firmware `V`/`R` (Tasks 1-2); existing `send_line(ser, s)`.
- Produces: `probe_firmware_protocol(ser) -> int` (returns 3 for v3, 2 for anything else — never raises), module global `FIRMWARE_PROTOCOL: int` (default 2, set once in `main()`), `scroll_wheel(ser, clicks: int) -> None`. Task 7 reads `FIRMWARE_PROTOCOL` to decide whether to hand `move()` a flush callable.

- [ ] **Step 1: Write the failing tests**

Append to `ControllerAddon/tests/test_controller.py`:

```python
# ---- firmware v3 protocol probe ----

class _FakeSer:
    def __init__(self, response=b""):
        self._response = response
        self.written = []

    def write(self, data):
        self.written.append(bytes(data))

    def reset_input_buffer(self):
        pass

    def readline(self):
        return self._response


def test_probe_firmware_protocol_v3():
    ser = _FakeSer(b"HB 3\n")
    assert controller.probe_firmware_protocol(ser) == 3
    assert b"V\n" in ser.written


def test_probe_firmware_protocol_legacy_times_out():
    # Pre-v3 firmware ignores unknown lines entirely; readline returns b"" on timeout.
    assert controller.probe_firmware_protocol(_FakeSer(b"")) == 2


def test_probe_firmware_protocol_garbage_is_legacy():
    assert controller.probe_firmware_protocol(_FakeSer(b"\x00\xffnoise\n")) == 2


def test_probe_firmware_protocol_never_raises():
    class Broken:
        def reset_input_buffer(self):
            raise OSError("port died")
    assert controller.probe_firmware_protocol(Broken()) == 2
```

(If `tests/test_controller.py` does not already import `controller` at module scope, add the same three-line `sys.path` bootstrap used at the top of `tests/test_human_move.py`.)

- [ ] **Step 2: Run tests to verify they fail**

From `ControllerAddon/`: `python -m pytest tests/test_controller.py -q -k probe_firmware`
Expected: FAIL with `AttributeError: module 'controller' has no attribute 'probe_firmware_protocol'`.

- [ ] **Step 3: Implement**

In `controller.py`, directly under `send_line` (after line 1154):

```python
# Firmware protocol level, set ONCE by probe_firmware_protocol() in main(). 2 = pre-V legacy
# (the firmware ignores unknown lines and answers nothing). Everything v3-only -- the X flush
# in human_move.move, scroll_wheel, the startup R -- must gate on this rather than on graceful
# degradation: an IGNORED X means the queued motion plays anyway, which would silently corrupt
# the mover's rewound position bookkeeping.
FIRMWARE_PROTOCOL = 2


def probe_firmware_protocol(ser) -> int:
    """Ask the firmware its protocol level ("V" -> "HB <n>"). Called once at startup, never
    mid-flight (it consumes the input buffer). The negative path is the 1 s read timeout set
    at port open; any failure at all reports legacy rather than raising."""
    try:
        ser.reset_input_buffer()
        send_line(ser, "V")
        line = ser.readline().decode("ascii", "replace").strip()
        if line.startswith("HB "):
            return max(2, int(line.split()[1]))
    except Exception:
        pass
    return 2


def scroll_wheel(ser: serial.Serial, clicks: int) -> None:
    """Scroll the wheel `clicks` detents (positive = away from the user). Queued firmware-side,
    so it keeps program order with motion and clicks. Silently unavailable pre-v3."""
    if FIRMWARE_PROTOCOL >= 3 and clicks:
        send_line(ser, f"W {max(-127, min(127, int(clicks)))}")
```

In `main()`, right after `time.sleep(2.0)  # Pro Micro reset` (line ~2809):

```python
        global FIRMWARE_PROTOCOL
        FIRMWARE_PROTOCOL = probe_firmware_protocol(ser)
        debug_log.log("serial", f"firmware protocol v{FIRMWARE_PROTOCOL}"
                                + ("" if FIRMWARE_PROTOCOL >= 3 else " (no flush/wheel/reset)"))
        if FIRMWARE_PROTOCOL >= 3:
            send_line(ser, "R")   # clean slate: releases anything a crashed predecessor left held
```

(`debug_log.log(tag, message)` is the verified signature — `debug_log.py:71`.)

- [ ] **Step 4: Run tests to verify they pass**

`python -m pytest tests/test_controller.py -q -k probe_firmware` — expect 4 passed.
Then the full suite: `python -m pytest tests/ -q` — expect no new failures.

- [ ] **Step 5: Commit**

```powershell
git add ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m @'
controller: probe firmware protocol at startup; startup R; scroll_wheel helper

The probe ("V" -> "HB 3", 1s timeout as the legacy negative path) is what gates
every v3 feature: an un-reflashed firmware ignores unknown lines, so sending X
without this check would let queued motion play while Python rewinds its
bookkeeping. On v3, one R at startup releases anything a crashed predecessor
left held.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

---

### Task 6: Python — flush-on-replan in `human_move.move()` with bookkeeping rewind

**Files:**
- Modify: `ControllerAddon/human_move.py` (the `move()` function, lines ~407-539)
- Modify: `ControllerAddon/settings.py` (one new constant, near the other HM2 values)
- Test: `ControllerAddon/tests/test_human_move.py`

**Interfaces:**
- Consumes: nothing new from other tasks (fully unit-testable without firmware — `flush_queue` is injected like every other dependency).
- Produces: `move(ser, x, y, *, get_cursor_pos, send_line, abort=None, duration_scale=1.0, refresh_target=None, flush_queue=None, rng=random)` — `flush_queue` is a zero-arg callable that retracts queued firmware motion, or `None` (default) for exact current behavior. `settings.HM2_FLUSH_COMMIT_MS`. Task 7 passes the callable.

- [ ] **Step 1: Add the settings constant**

In `settings.py`, next to the other HM2 constants:

```python
# Motion that is already committed at the instant an X flush reaches the firmware: serial +
# parse latency (~2-4 ms) plus the active segment, which ALWAYS finishes (it executes in place
# at queue[q_head] and is never evicted -- see firmware/Firmware.ino's flushQueue). move()
# rewinds its position/time bookkeeping to the first emission at or past elapsed + this margin;
# a rewind short of reality by up to one segment is absorbed by the residual corrections.
HM2_FLUSH_COMMIT_MS = 22.0
```

- [ ] **Step 2: Write the failing tests**

Append to `ControllerAddon/tests/test_human_move.py` (the autouse `_isolate_calibration` and `_pin_tempo` fixtures already apply):

```python
# ---- v3 flush (retractable firmware queue) ----

class FakeClock:
    """move() paces itself with time.perf_counter/time.sleep; this replaces both so a test
    controls elapsed time exactly and runs instantly."""
    def __init__(self):
        self.t = 0.0

    def perf_counter(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, seconds)


class FakeFirmware:
    """Minimal model of Firmware.ino's queue: S segments play back-to-back on the fake clock,
    and X drops every segment not yet STARTED at that instant -- the active one finishes,
    mirroring flushQueue() keeping queue[q_head]. Positions are in pixels because the isolated
    calibration starts _px_per_unit at 1.0 and a flushed flight never updates it."""
    def __init__(self, clk):
        self.clk = clk
        self.segs = []        # [start_ms, end_ms, dx_px, dy_px]
        self.t_tail = 0.0     # timeline position of the last queued segment's end
        self.flushes = 0

    def _now_ms(self):
        return self.clk.t * 1000.0

    def send(self, ser, line):
        if line == "X":
            self.flushes += 1
            self.segs = [s for s in self.segs if s[0] <= self._now_ms()]
            self.t_tail = self.segs[-1][1] if self.segs else self._now_ms()
            return
        if not line.startswith("S "):
            return
        _, dxq, dyq, dur = line.split()
        start = max(self.t_tail, self._now_ms())
        end = start + max(1.0, float(dur))
        self.segs.append([start, end, int(dxq) / 100.0, int(dyq) / 100.0])
        self.t_tail = end

    def pos(self):
        now, x, y = self._now_ms(), 0.0, 0.0
        for start, end, dx, dy in self.segs:
            if now >= end:
                x += dx
                y += dy
            elif now > start:
                frac = (now - start) / (end - start)
                x += dx * frac
                y += dy * frac
        return (round(x), round(y))


def _flush_rig(monkeypatch):
    clk = FakeClock()
    monkeypatch.setattr(human_move.time, "perf_counter", clk.perf_counter)
    monkeypatch.setattr(human_move.time, "sleep", clk.sleep)
    return clk, FakeFirmware(clk)


def test_flush_fires_on_replan_and_flight_still_lands(monkeypatch):
    """A big mid-flight target jump must (a) send X to retract queued motion and (b) still
    finish on the NEW target -- proving the post-flush bookkeeping rewind is consistent with
    what the firmware actually played."""
    clk, fw = _flush_rig(monkeypatch)
    state = {"target": (800, 0), "calls": 0}

    def refresh():
        state["calls"] += 1
        if state["calls"] == 1:
            state["target"] = (800, 260)
        return state["target"]

    human_move.move(None, 800, 0,
                    get_cursor_pos=fw.pos, send_line=fw.send,
                    refresh_target=refresh,
                    flush_queue=lambda: fw.send(None, "X"),
                    rng=random.Random(6))
    clk.t += 5.0   # let every queued segment play out
    assert fw.flushes >= 1
    fx, fy = fw.pos()
    miss = math.hypot(fx - 800, fy - 260)
    assert miss <= settings.HM2_CORRECTION_TOLERANCE_PX + 4, (
        f"landed {miss:.1f}px off the moved target -- post-flush rewind is inconsistent "
        f"with played motion")


def test_no_flush_without_replan(monkeypatch):
    """A stationary target must never pay a flush."""
    clk, fw = _flush_rig(monkeypatch)
    human_move.move(None, 600, 300,
                    get_cursor_pos=fw.pos, send_line=fw.send,
                    refresh_target=lambda: (600, 300),
                    flush_queue=lambda: fw.send(None, "X"),
                    rng=random.Random(3))
    assert fw.flushes == 0


def test_flushed_flight_skips_scale_learning(monkeypatch):
    """Emitted units on a flushed flight include motion that never played, so feeding them to
    _learn_scale would poison the persisted calibration. A clean flight must still learn."""
    clk, fw = _flush_rig(monkeypatch)
    state = {"target": (800, 0), "calls": 0}

    def refresh():
        state["calls"] += 1
        if state["calls"] == 1:
            state["target"] = (800, 260)
        return state["target"]

    before = human_move._scale_samples
    human_move.move(None, 800, 0,
                    get_cursor_pos=fw.pos, send_line=fw.send,
                    refresh_target=refresh,
                    flush_queue=lambda: fw.send(None, "X"),
                    rng=random.Random(6))
    assert human_move._scale_samples == before, "flushed flight fed the calibration"

    clk2, fw2 = _flush_rig(monkeypatch)
    human_move.move(None, 500, 200,
                    get_cursor_pos=fw2.pos, send_line=fw2.send,
                    rng=random.Random(7))
    assert human_move._scale_samples == before + 1, "clean flight failed to learn"
```

- [ ] **Step 3: Run tests to verify they fail**

`python -m pytest tests/test_human_move.py -q -k flush`
Expected: FAIL with `TypeError: move() got an unexpected keyword argument 'flush_queue'`.

- [ ] **Step 4: Implement in `human_move.py`**

Import the new constant (extend the existing `settings` import block): add `HM2_FLUSH_COMMIT_MS`.

Signature (line 407-408):

```python
def move(ser, x: int, y: int, *, get_cursor_pos, send_line, abort=None,
         duration_scale: float = 1.0, refresh_target=None, flush_queue=None,
         rng: random.Random = random) -> None:
```

Extend the docstring:

```python
    flush_queue, if given, is a zero-arg callable that retracts every QUEUED (unplayed) firmware
    segment -- the active one finishes (see firmware/Firmware.ino's X command). It is invoked
    only when a re-aim replans the flight, and only when enough motion is queued to be worth
    retracting; the emission log below is what lets the bookkeeping rewind to the last segment
    that actually plays. Callers MUST pass None unless the firmware probe confirmed protocol
    v3: against older firmware X is silently ignored, the queued motion plays anyway, and the
    rewound bookkeeping would be wrong. A flushed flight never feeds _learn_scale -- its
    emitted units include motion that never happened.
```

State, added where `queued_ms`/`t0` are initialized (line ~436):

```python
    emit_log = []      # (cumulative queued ms after this emission, planned x, planned y)
    flushed = False
```

In the emission loop, right after `queued_ms += dur` (line 507):

```python
            emit_log.append((queued_ms, cur[0], cur[1]))
```

In the re-aim block, inside `if math.hypot(...) > HM2_REPLAN_MIN_DRIFT_PX:` — immediately after `target = moved` and BEFORE the `points = points[:idx] + plan_submovements(cur, target, rng)` replan (line ~467), so the replan sees the rewound `cur`:

```python
                    # Retract what hasn't played. Everything queued past elapsed + the commit
                    # margin (serial latency + the active segment, which always finishes) is
                    # motion toward the OLD target that the firmware will now never perform, so
                    # rewind cur/queued_ms to the last emission that does play and let the
                    # replan start from there. Skipped when there's less than a segment's worth
                    # to reclaim -- a flush that saves nothing is just serial noise.
                    if flush_queue is not None:
                        elapsed_ms = (time.perf_counter() - t0) * 1000.0
                        commit_ms = elapsed_ms + HM2_FLUSH_COMMIT_MS
                        if queued_ms - commit_ms > HM2_SEGMENT_MS:
                            flush_queue()
                            flushed = True
                            for i, (t_ms, ex, ey) in enumerate(emit_log):
                                if t_ms >= commit_ms:
                                    queued_ms = t_ms
                                    cur = (ex, ey)
                                    del emit_log[i + 1:]
                                    break
```

Guard the calibration (line ~538):

```python
    if not flushed:
        _learn_scale(math.hypot(units_x, units_y),
                     math.hypot(cur[0] - start[0], cur[1] - start[1]))
```

Note what is deliberately NOT rewound: `units_x`/`units_y` and `carry_x`/`carry_y`. The units are only consumed by `_learn_scale`, which the `flushed` flag now skips entirely; the sub-pixel carries diverge from the firmware's own by under a pixel, which the residual corrections absorb. Say exactly this in a comment next to the `flushed` flag.

- [ ] **Step 5: Run the new tests, then the whole suite**

`python -m pytest tests/test_human_move.py -q -k flush` — expect 3 passed.
`python -m pytest tests/ -q` — expect no new failures (the existing move() tests all pass `flush_queue` implicitly as None and must be bit-for-bit unaffected).

- [ ] **Step 6: Commit**

```powershell
git add ControllerAddon/human_move.py ControllerAddon/settings.py ControllerAddon/tests/test_human_move.py
git commit -m @'
human_move: retract queued motion on replan via firmware X (flush_queue)

When a re-aim replans the flight, everything queued past elapsed +
HM2_FLUSH_COMMIT_MS (serial latency + the active segment, which always
finishes) is motion toward the old target the firmware will now never
perform. A per-emission log lets cur/queued_ms rewind to the last segment
that actually plays, so the replan starts from reality instead of from a
position up to 54ms in a retracted future. Flushed flights skip
_learn_scale -- their emitted units include motion that never happened.
flush_queue=None (the default) reproduces prior behavior exactly.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

---

### Task 7: Python — wire the flush into `move_to_human`'s v2 dispatch; final verification

**Files:**
- Modify: `ControllerAddon/controller.py` (the v2 dispatch inside `move_to_human`, lines ~1669-1688)
- Test: existing suites (no new test file; the seam was tested in Tasks 5 and 6)

**Interfaces:**
- Consumes: `FIRMWARE_PROTOCOL` (Task 5), `move(..., flush_queue=...)` (Task 6), existing `send_line`.
- Produces: every v2 flight with a `refresh_target` automatically gains flush-on-replan when the firmware supports it. No routine changes anywhere.

- [ ] **Step 1: Pass the callable**

In the v2 branch of `move_to_human` (line ~1685), extend the call:

```python
        flush = None
        if FIRMWARE_PROTOCOL >= 3:
            def flush():
                send_line(ser, "X")
        human_move.move(ser, aim[0], aim[1], get_cursor_pos=get_cursor_pos, send_line=send_line,
                        abort=_movement_abort, duration_scale=duration_scale,
                        refresh_target=v2_refresh, flush_queue=flush)
```

Also check `tests/test_human_move.py:294` — the v2-dispatch test that asserts on captured `v2_calls[0]` kwargs. If it captures kwargs strictly, it may need `flush_queue` added to its expectations; follow whatever the test actually asserts.

- [ ] **Step 2: Full verification**

From `ControllerAddon/`:
- `python -m py_compile controller.py human_move.py settings.py firmware_bench.py`
- `python -m pytest tests/ -q`

Expected: clean compile, full suite green.

- [ ] **Step 3: Commit**

```powershell
git add ControllerAddon/controller.py ControllerAddon/tests/test_human_move.py
git commit -m @'
controller: hand move_to_human's v2 flights the firmware flush when probed v3

Gated on FIRMWARE_PROTOCOL >= 3 so an un-reflashed device keeps today's exact
behavior; no routine changes anywhere -- every click_entity call site inherits
it through the one v2 dispatch branch.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

- [ ] **Step 4: Note the live-verification debt**

This lands UNVERIFIED live (same convention as refresh_legs and the hover adoptions — see CLAUDE.md). The next live session after the Task 4 flash should watch a moving-target routine (slayer) and confirm in the log that flights with flushes still land on target; the measurable success signal is `_settle_on_target_before_click`'s corrective move firing less often. Record that observation in CLAUDE.md when it happens.

---

## Execution order note

Tasks 1→2→3 (firmware+bench), then 5→6→7 (Python) — Task 4 (the flash checkpoint) can happen any time after Task 3, including after Task 7, because the probe gate keeps the Python side inert until the device answers `HB 3`. If the human is available early, flash early; nothing blocks on it.
