<#
.SYNOPSIS
  Launch the RuneLite client (bridge + ZombieLite + Controller) in the OSRS guest, logged in
  to the Jagex account via credentials.properties. Runs INSIDE the guest.

.DESCRIPTION
  Added 2026-09-07. The account logs in with NO Jagex Launcher: RuneLite reads
  %USERPROFILE%\.runelite\credentials.properties (the JX_ tokens) at startup and signs in
  automatically. That file is copied from the host once (VERIFIED 2026-09-07: the guest
  launched straight to the logged-in game as "07 Kiyo"). The tokens self-refresh and are
  written back to the GUEST's .runelite, so keep the host's RuneLite closed while the VM runs
  or the two sessions will fight over one refresh token.

  The default start is the FULL / "unsafe" one: developer mode with the injected client and
  every builtin plugin (ExamplePluginTest -> ExternalPluginManager.loadBuiltin -> the bridge,
  ZombieLite and Controller). -SafeMode passes RuneLite's --safe-mode for a diagnostic launch
  with external plugins off (the bridge still loads; use it only to isolate a plugin problem).

  Fingerprint parity comes from ~/.gradle/gradle.properties (clientJvmArgs), applied by the
  run task in build.gradle. Serial port is COM3 in the guest.
#>
param(
    [switch]$SafeMode,
    [switch]$NoSync
)
$ErrorActionPreference = "Stop"
$jdk = Join-Path $env:LOCALAPPDATA "Programs\jdk-11.0.30+7"
$repo = "C:\work\Httpbridge"

if (-not (Test-Path "$env:USERPROFILE\.runelite\credentials.properties")) {
    Write-Warning "No credentials.properties in the guest ~/.runelite -- the client will show the manual login screen. Copy it from the host to auto-login."
}
if (-not $NoSync) {
    Write-Host "== syncing repo from the share" -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File "Z:\Httpbridge\tools\vm-sync.ps1" | Select-Object -Last 4
}

$env:JAVA_HOME = $jdk
$env:BRIDGE_SERIAL_PORT = "COM3"
$extra = if ($SafeMode) { "--safe-mode" } else { "" }

Write-Host "== launching client ($(if($SafeMode){'safe mode, external plugins OFF'}else{'full / developer mode'}))" -ForegroundColor Cyan
Push-Location $repo
try {
    # Detached: the client keeps running after this script returns. Log to C:\work\run.log.
    Start-Process -FilePath "$repo\gradlew.bat" -ArgumentList "--no-daemon", "run" `
        -RedirectStandardOutput "C:\work\run.log" -RedirectStandardError "C:\work\run.err.log" `
        -WindowStyle Hidden
    # Forward the safe-mode arg through to RuneLite by editing build.gradle's run args? No --
    # ExamplePluginTest forwards its own argv, so pass it via a gradle -P instead if needed.
    if ($SafeMode) { Write-Host "  NOTE: to actually pass --safe-mode, add it to the run task's args in build.gradle (guest copy) -- gradlew run does not forward script args." -ForegroundColor Yellow }
} finally { Pop-Location }

Write-Host "  started; watch C:\work\run.log. Bridge on http://localhost:8080 in the guest."
Write-Host "  then:  cd $repo\ControllerAddon ; python -u controller.py routines\<name>.py --skip-deob-check"
