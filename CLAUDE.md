# BridgePlugin — live-debugging workflow

When debugging a `ControllerAddon/routines/*.py` routine live against the running RuneLite
client, use this loop by default. It was confirmed to work well over an hours-long session that
took `golem_crafting.py` from broken to reliably running unattended.

## The loop

1. **Check for a serial-port conflict first.** `controller.py` and the MCP debug server
   (`mcp_debug_server.py`) both need exclusive use of the Arduino's COM port — only one can hold
   it at a time. Before launching either, confirm no stray process still has it:
   `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"`.
2. **Launch the routine in the background, unbuffered, logged to a file:**
   `python -u controller.py routines/<name>.py *> <logfile> 2>&1`. Read-only bridge queries
   (`GET http://localhost:8080/...`) don't need the serial port, so they work for live
   cross-checking even while `controller.py` holds it.
3. **Poll the log with `Read`, not tight loops.** 20–90s gaps between checks are fine — this is a
   live session, not a race.
4. **Trust the user's live observations over your own read of the log.** If the log looks clean
   to you but the user reports something wrong, keep digging for the actual mechanism rather than
   concluding it's already fixed.
5. **Before every relaunch: `py_compile` every touched file, then run the test suite.** Pytest
   alone is not sufficient — some modules (e.g. `banker.py`) aren't imported by any test, so a
   syntax error there won't surface in `pytest`, only in a direct compile check.
6. **After relaunching, actually watch it work through the specific phase that was broken.**
   Compiling clean and "the reasoning sounds right" are not verification.
7. **Iterate in small, targeted fixes**, one reported symptom at a time, rather than large
   rewrites.

## Before any live click/mouse debugging

Verify all three (see memory `feedback-mcp-debug-preconditions` for the full incident this came
from):
- RuneLite window is focused (`GET /state` → `windowFocused`).
- Windows mouse acceleration ("Enhance pointer precision") is off.
- The process calling into `controller.py` is DPI-aware (`set_dpi_aware()` — already handled by
  `mcp_debug_server.py` at import time).

## Hardware vs. code

If `controller.py` crashes with a `serial.SerialException`, check whether the Arduino has
actually dropped off the USB bus before assuming it's a logic bug:
`[System.IO.Ports.SerialPort]::getportnames()` (empty = gone) and `Get-PnpDevice -Class Ports`
(look for "Status: Unknown" on the Arduino Leonardo). A reseat/power-cycle fixes this — no amount
of code changes will.
