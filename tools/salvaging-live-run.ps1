# Live run for the shipwreck-salvaging mass-world/sorting branch (2026-09-07).
#
# The branch's Java half (boatViewId on /objects and /player) needs a CLIENT REBUILD before any
# of it can be trusted: without that field sailing.find_facility falls back to nearest-by-name,
# and on a mass world 26 salvaging stations from ~13 overlapping boats sit within a few tiles --
# three on the player's own tile. Picking the nearest is a coin flip between our fixtures and a
# stranger's.
#
# Usage (from the repo root):
#     powershell -ExecutionPolicy Bypass -File tools\salvaging-live-run.ps1
#
# It stops the running client, rebuilds and relaunches it, waits for the bridge, runs the
# boatViewId gate, and only starts the routine if the gate passes.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Write-Host "== 1. stopping the running client ==" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
    Where-Object { $_.CommandLine -match 'GradleWrapperMain|ExamplePluginTest|Httpbridge\\build' } |
    ForEach-Object {
        Write-Host "  stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 4

Write-Host "== 2. rebuilding + relaunching (this blocks; the client window opens) ==" -ForegroundColor Cyan
$client = Start-Process -FilePath "cmd.exe" `
    -ArgumentList '/c', 'gradlew.bat --no-daemon run > logs\client-relaunch.log 2>&1' `
    -WorkingDirectory $repo -PassThru -WindowStyle Minimized
Write-Host "  launched as PID $($client.Id); log: logs\client-relaunch.log"

Write-Host "== 3. waiting for the bridge ==" -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(6)
$up = $false
while ((Get-Date) -lt $deadline) {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8080/state' -TimeoutSec 3 | Out-Null
        $up = $true
        break
    } catch { Start-Sleep -Seconds 5 }
}
if (-not $up) {
    Write-Host "bridge never came up -- check logs\client-relaunch.log" -ForegroundColor Red
    exit 1
}
Write-Host "  bridge is up" -ForegroundColor Green

Write-Host ""
Write-Host "== 4. LOG IN and get aboard the boat, then press Enter ==" -ForegroundColor Yellow
Write-Host "   (RuneLite signs in from credentials.properties, but the boat needs you)"
Read-Host "   Enter when the character is standing on the boat"

Write-Host "== 5. boatViewId gate ==" -ForegroundColor Cyan
python tools\salvaging_gate.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "GATE FAILED -- not starting the routine." -ForegroundColor Red
    Write-Host "Every station/hold/hook click would fall back to nearest-by-name, which on a"
    Write-Host "mass world can target a neighbouring boat's fixtures." -ForegroundColor Red
    exit 1
}

Write-Host "== 6. starting the routine ==" -ForegroundColor Cyan
Write-Host "   F1 = panic stop.  Log: ControllerAddon\logs\salvaging_live1.log"
Set-Location (Join-Path $repo 'ControllerAddon')
if (-not (Test-Path logs)) { New-Item -ItemType Directory logs | Out-Null }
python -u controller.py routines/shipwreck_salvaging.py *> logs\salvaging_live1.log
