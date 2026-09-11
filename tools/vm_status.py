r"""What is running in the OSRS guest, and is its bridge answering?  Added 2026-09-08.

    python tools/vm_status.py

Python rather than a .ps1 for the reason vm_stop_routine.py documents at length: vmexec is a
Python module, and every shell between the host and the guest mangles something -- backslashes
in a UNC path, or the double quotes a native exe's arguments need.  Calling vmexec from Python
hands the command to paramiko exactly as written.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vmexec

PROBE = r'''
$rows = Get-CimInstance Win32_Process | Where-Object { $_.Name -match "java|python" }
if (-not $rows) { "processes : none (no client, no routine)" }
foreach ($r in $rows) {
    $cl = $r.CommandLine
    if ($cl.Length -gt 90) { $cl = $cl.Substring(0, 90) }
    $what = "other"
    if ($cl -match "ExamplePluginTest|gradle") { $what = "CLIENT" }
    if ($cl -match "controller")               { $what = "ROUTINE" }
    "{0,-8} {1,-7} {2}" -f $what, $r.ProcessId, $cl
}
foreach ($path in @("/client-version", "/login/status")) {
    try {
        $j = Invoke-RestMethod -Uri ("http://127.0.0.1:8080" + $path) -TimeoutSec 4
        "bridge $path : " + ($j | ConvertTo-Json -Compress -Depth 2)
    } catch {
        "bridge $path : not answering"
    }
}
'''

if __name__ == "__main__":
    raise SystemExit(vmexec.run(PROBE, timeout=90))
