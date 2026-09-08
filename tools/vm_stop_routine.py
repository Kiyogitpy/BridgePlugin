r"""Stop controller.py in the OSRS guest, from the host.  Added 2026-09-08.

    python tools/vm_stop_routine.py

WHY IT IS NOT JUST "kill python": a SAFETY STOP *pauses* controller.py, it does not exit it --
the process stays alive printing "Still stopped" and keeps holding COM3, so run-vm.bat then
refuses to start a replacement and a stopped run looks like a stuck one.  It is also what you
want before a GAME UPDATE: stop the Arduino driving input before the client is forced out,
rather than letting it click into a logout.

WHY THIS IS PYTHON AND NOT A .ps1 WRAPPER.  Two drafts died in the shell layers between the
host and the guest, and both failed in ways worth remembering:

* Passing the guest script's UNC path as an argument -- every shell on the way ate one of the
  leading backslashes, the guest answered "the argument '\VBOXSVR\...' does not exist", and
  the wrapper STILL EXITED 0.  A scheduled stop therefore reported success while the routine
  carried on running; it was caught only because the next call said "stopping PID 10872".
* Passing the command inline from PowerShell -- PowerShell strips double quotes when handing
  arguments to a native exe, so `-match "python"` reached the guest as `-match python` and was
  a parse error there.

vmexec is a Python module, so calling it from Python removes every one of those layers: the
command string is handed to paramiko exactly as written.  The same reasoning is why
vmexec.run_interactive builds its own script in Python rather than through a shell.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vmexec

# The guest's default shell is PowerShell 5.1.  `controller` rather than `controller.py`
# because the command line may spell it either way.
_FIND = ('Get-CimInstance Win32_Process | Where-Object '
         '{ $_.Name -match "python" -and $_.CommandLine -match "controller" }')

STOP = f'''$p = {_FIND}
if (-not $p) {{ "no controller.py running in the guest"; exit 0 }}
foreach ($x in $p) {{
    "stopping PID $($x.ProcessId)"
    Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue
}}
Start-Sleep -Seconds 2
'''

CHECK = f'$p = {_FIND}\nif ($p) {{ "ALIVE" }} else {{ "NONE" }}\n'


def stop(timeout=60):
    """Stop it, then PROVE it.  Returns 0 only on a confirmed 'nothing running' -- the whole
    point, since the bug this replaced was a stop that reported success and did nothing."""
    vmexec.run(STOP, timeout=timeout)

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        vmexec.run(CHECK, timeout=timeout)
    answer = buf.getvalue()

    if "ALIVE" in answer:
        print("FAILED: controller.py is still running in the guest")
        return 1
    if "NONE" not in answer:
        print(f"FAILED: could not confirm the guest state (got: {answer.strip()!r})")
        return 1
    print("confirmed: no controller.py running in the guest")
    return 0


if __name__ == "__main__":
    raise SystemExit(stop())
