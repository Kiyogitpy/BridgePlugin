# Stop any controller.py running in the guest (a safety stop PAUSES the routine, it does not
# exit the process -- so the COM port stays held and run-vm.bat refuses to start another).
$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'controller\.py' }
if (-not $procs) { "no controller.py running in the guest"; exit 0 }
foreach ($p in $procs) {
    "stopping PID $($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
$left = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'controller\.py' }
if ($left) { "STILL RUNNING: $($left.ProcessId)"; exit 1 } else { "guest routine stopped"; exit 0 }
