<#
.SYNOPSIS
  One command, from the HOST: bring the OSRS VM up, launch the client in it, wait until the
  character is actually standing in the game world, then start a routine in the guest.

.DESCRIPTION
  Added 2026-09-08. This is glue over three things that already existed and were run by hand:
  tools\vm-up.ps1 (host), tools\vm-run-client.ps1 (guest) and `python controller.py
  routines\<name>.py` (guest). Normally driven by run-vm.bat in the repo root.

  WHY THE ROUTINE RUNS IN THE GUEST: the Leonardo is captured by a USB filter, so it is the
  GUEST's mouse/keyboard and the host cannot see the COM port at all while the VM runs.

  HOW THE HOST DRIVES IT: tools\vmexec.py over SSH. NOT `VBoxManage guestcontrol` -- that
  stopped executing anything on this setup (commit 946e07f, 2026-09-08). Two forms matter and
  the difference is not cosmetic: plain SSH has NO WINDOW STATION, so anything touching the
  cursor or a GUI dies there with WinError 1459 -- the client and controller.py therefore go
  through `--interactive`, which runs them on the logged-on desktop via a scheduled task.
  Probes and the sync are plain SSH.

  THERE IS NO SYNC IN THE EDIT->RUN LOOP. The routine runs from Z:\Httpbridge\ControllerAddon,
  which IS the host's repo through the VirtualBox share, so a Python edit here is live in the
  guest immediately and its logs land in the host's own ControllerAddon\logs. C:\work exists
  only because host and guest GRADLE would fight over one build\ on a network drive, so the
  mirror is synced only when this script is about to (re)build and launch the CLIENT.

  THE LOGIN GATE is /login/status through the NAT forward, not GameState: `loggedIn` alone is
  already true while the CLICK-HERE-TO-PLAY screen still covers the game (LoginProvider.java,
  and login.is_fully_logged_in() makes exactly this distinction). So the gate is
  `loggedIn && no clickToPlay`.

  Every step is idempotent and has a deadline. Re-running with the client already up skips
  straight to the login gate; re-running with a routine already running refuses (one process
  per COM port).

.PARAMETER RoutineArgs
  The routine, plus any flags to forward to controller.py. "mining", "routines\mining.py" and
  "routines/mining.py" all mean the same file. OMIT IT for panel mode: controller.py is started
  with no routine argument, so the RuneLite plugin's Routine dropdown picks the routine and can
  switch it live. -NoRoutine is the one that starts no controller.py at all.

.EXAMPLE
  .\run-vm.bat                                 # panel mode -- choose the routine in the GUI
.EXAMPLE
  .\run-vm.bat motherlode_mine                 # pin the routine, ignore the dropdown
.EXAMPLE
  .\run-vm.bat karambwan_fishing --debug-log   # extra args go to controller.py
#>
param(
    # Position=0 matters: without it PowerShell treats every later parameter as positional
    # too, and `run-vm.bat mining` binds "mining" to -BridgeTimeoutMin and dies on the cast.
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$RoutineArgs,

    # Start the routine even if the client never reached the game world. login.py can click
    # through the Play Now / "Try again" screens itself, so this is a reasonable recovery when
    # RuneLite's own auto-login did not take.
    [switch]$SkipLoginGate,

    # Bring the VM and client up and stop there, starting no controller.py at all. (Passing no
    # routine is NOT this -- that is panel mode, where the plugin's dropdown picks the routine.)
    [switch]$NoRoutine,

    # Override the two "you are about to break something" refusals: a RuneLite client already
    # running on the HOST (two clients fight over one refreshing Jagex token) and a python.exe
    # already running in the guest (two controller.py cannot share the Arduino's COM port).
    [switch]$Force,

    [int]$BridgeTimeoutMin = 8,   # covers a cold gradle build in the guest
    [int]$LoginTimeoutMin  = 3
)

# NOT "Stop": every guest call here is a NATIVE command, and PowerShell 5.1 turns a native
# command's stderr into a terminating NativeCommandError under Stop -- so one transient
# paramiko line ("Error reading SSH protocol banner" while the guest boots) would kill the run
# rather than be retried. MEASURED 2026-09-08 on the first cold start. Every step below checks
# its own outcome (the bridge answers, the task started) and exits explicitly, so nothing here
# depended on exceptions.
$ErrorActionPreference = "Continue"

$base   = "http://127.0.0.1:18080"                 # the natpf1 forward vm-up.ps1 (re)adds
$share  = "\\VBOXSVR\Docs\Httpbridge\tools"        # the Docs share, reachable without a mapped drive
$repo   = Split-Path -Parent $PSScriptRoot
$vmexec = Join-Path $PSScriptRoot "vmexec.py"

if (-not (Test-Path $vmexec)) { Write-Host "tools\vmexec.py not found" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- helpers

function Get-Bridge([string]$path) {
    try { return Invoke-RestMethod -Uri "$base$path" -TimeoutSec 3 } catch { return $null }
}

# Run a command in the guest over plain SSH and return its stdout. The guest's shell is
# PowerShell 5.1. No window station here -- probes and file work only.
function Invoke-Guest([string]$command, [double]$timeoutS = 120) {
    $out = & python $vmexec --timeout $timeoutS $command 2>&1
    return ($out | Out-String)
}

# Run a command on the guest's DESKTOP (scheduled task, logged-on session). Required for
# anything with a window or a cursor. Returns immediately; the task keeps running.
function Start-GuestDesktop([string]$command, [string]$task) {
    & python $vmexec --interactive --task $task --timeout 120 $command 2>&1 |
        ForEach-Object { if ("$_".Trim()) { Write-Host "  $_" -ForegroundColor DarkGray } }
}

# "mining" / "routines\mining.py" / "routines/mining.py" -> "routines\mining.py", verified to exist.
function Resolve-Routine([string]$name) {
    $rel = $name -replace '/', '\'
    if ($rel -notmatch '\.py$')      { $rel = "$rel.py" }
    if ($rel -notmatch '\\')         { $rel = "routines\$rel" }
    if (-not (Test-Path (Join-Path $repo "ControllerAddon\$rel"))) { return $null }
    return $rel
}

function Show-Routines {
    Write-Host "Available routines:" -ForegroundColor Yellow
    Get-ChildItem (Join-Path $repo "ControllerAddon\routines") -Filter *.py |
        Where-Object { $_.Name -ne "__init__.py" } |
        ForEach-Object { "  " + $_.BaseName } | Sort-Object | Write-Host
}

# ---------------------------------------------------------------- 0. what are we running?

$routine   = $null    # a pinned routine path; $null with $panelMode means "the GUI decides"
$extra     = @()      # anything else on the command line, forwarded to controller.py
$panelMode = $false

if ($NoRoutine) {
    Write-Host "-NoRoutine: bringing the VM and client up and stopping there" -ForegroundColor Yellow
}
elseif ($RoutineArgs -and $RoutineArgs.Count -gt 0 -and $RoutineArgs[0] -notmatch '^-') {
    $routine = Resolve-Routine $RoutineArgs[0]
    if (-not $routine) {
        Write-Host "No routine file matches '$($RoutineArgs[0])'." -ForegroundColor Red
        Show-Routines
        exit 1
    }
    if ($RoutineArgs.Count -gt 1) { $extra = $RoutineArgs[1..($RoutineArgs.Count - 1)] }
    Write-Host "routine: $routine $($extra -join ' ')  (pinned; the panel dropdown is ignored)" -ForegroundColor Cyan
}
else {
    # Panel mode -- the default. controller.py started with NO routine argument re-reads the
    # plugin's Routine dropdown every loop (ROUTINE_FILES lookup, controller.py) and hot-swaps
    # when it changes, with Controller Enabled gating the tick. So the GUI picks the routine,
    # and switching the dropdown switches routine without restarting anything.
    # A leading-dash first argument lands here too: it is a controller.py flag, not a routine.
    $panelMode = $true
    $extra = if ($RoutineArgs) { @($RoutineArgs) } else { @() }
    Write-Host "panel mode: the plugin's Routine dropdown picks the routine $($extra -join ' ')" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- 1. VM + port forwards

Write-Host ""
Write-Host "== 1. VM ==" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "vm-up.ps1")

# ---------------------------------------------------------------- 2. client

Write-Host ""
Write-Host "== 2. client ==" -ForegroundColor Cyan
if (Get-Bridge "/state") {
    Write-Host "  bridge already answering -- leaving the running client alone" -ForegroundColor Green
} else {
    # CLAUDE.md: the account signs in from credentials.properties and the JX_ tokens
    # self-refresh into whichever .runelite wrote them last, so two clients on one account
    # fight over a single refresh token. Refuse rather than break the guest's auto-login.
    $hostClient = Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'ExamplePluginTest|GradleWrapperMain|Httpbridge\\build' }
    if ($hostClient -and -not $Force) {
        Write-Host "  a RuneLite client is running ON THE HOST (PID $($hostClient[0].ProcessId))." -ForegroundColor Red
        Write-Host "  Close it first: both sign in from the same credentials.properties and would" -ForegroundColor Red
        Write-Host "  fight over one self-refreshing token. (-Force to launch anyway.)" -ForegroundColor Red
        exit 1
    }
    # The CLIENT is the only thing that needs the C:\work mirror (gradle). Sync it here and
    # nowhere else -- the routine runs straight off the share.
    Write-Host "  syncing the share into C:\work (the client builds from there)"
    $syncOut = Invoke-Guest "cmd /c $share\vm-guest-sync.cmd" 600
    ($syncOut -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 3) |
        ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

    Write-Host "  launching the client on the guest desktop (gradlew run)"
    Start-GuestDesktop "cmd /c $share\vm-guest-launch-client.cmd" "OSRSClient"

    $deadline = (Get-Date).AddMinutes($BridgeTimeoutMin)
    $up = $false
    while ((Get-Date) -lt $deadline) {
        if (Get-Bridge "/state") { $up = $true; break }
        Start-Sleep -Seconds 5
        Write-Host "." -NoNewline
    }
    Write-Host ""
    if (-not $up) {
        Write-Host "  bridge never came up within $BridgeTimeoutMin min." -ForegroundColor Red
        Write-Host "  Read the guest's build log:" -ForegroundColor Red
        Write-Host "    python tools\vmexec.py `"type C:\work\run.log`""
        exit 1
    }
    Write-Host "  bridge is up" -ForegroundColor Green
}

# ---------------------------------------------------------------- 3. in the game world?

Write-Host ""
Write-Host "== 3. waiting for the game world ==" -ForegroundColor Cyan
$inGame   = $false
$deadline = (Get-Date).AddMinutes($LoginTimeoutMin)
$lastSeen = ""
while ((Get-Date) -lt $deadline) {
    $s = Get-Bridge "/login/status"
    if ($s) {
        # clickToPlay is only present while that screen is up -- see LoginProvider.java.
        $clickToPlay = $null -ne $s.PSObject.Properties['clickToPlay']
        if ($s.loggedIn -and -not $clickToPlay) { $inGame = $true; break }
        $where = if ($clickToPlay) { "click-to-play screen" } else { "$($s.gameState) (loginIndex $($s.loginIndex))" }
        if ($where -ne $lastSeen) { Write-Host "  $where"; $lastSeen = $where }
    }
    Start-Sleep -Seconds 3
}
if ($inGame) {
    Write-Host "  in the game world" -ForegroundColor Green
} elseif ($SkipLoginGate) {
    Write-Host "  not in the game world -- starting anyway (-SkipLoginGate)" -ForegroundColor Yellow
} else {
    Write-Host "  still not in the game world after $LoginTimeoutMin min." -ForegroundColor Red
    Write-Host "  RuneLite signs in from credentials.properties; if it is sitting on Play Now or" -ForegroundColor Red
    Write-Host "  'Failed to login', login.py can click through that itself -- re-run with -SkipLoginGate." -ForegroundColor Red
    exit 1
}

if (-not $routine -and -not $panelMode) {
    Write-Host ""
    Write-Host "Client is up. Start a routine with:  .\run-vm.bat <routine>  (or bare, for the panel)" -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------- 4. routine

Write-Host ""
Write-Host "== 4. routine ==" -ForegroundColor Cyan

# One process per COM port (CLAUDE.md's Arduino gotcha) -- a second controller.py wedges it.
# Quote-free on purpose: this string crosses PowerShell -> python -> SSH -> PowerShell.
$running = Invoke-Guest "Get-Process -Name python -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count" 60
if ($running -match '[1-9]' -and -not $Force) {
    Write-Host "  python.exe is ALREADY running in the guest -- refusing to start a second one." -ForegroundColor Red
    Write-Host "  Only one process can hold the Arduino's COM port. Stop it (F1 in the VM window)" -ForegroundColor Red
    Write-Host "  or pass -Force if you know it is something else." -ForegroundColor Red
    exit 1
}

# FOCUS THE CLIENT. Not a nicety: a client launched from a scheduled task comes up UNFOCUSED,
# and MEASURED 2026-09-08 that silently defeats the whole run -- controller.py's login path
# (login.attempt_login, which runs BEFORE the engine's own windowFocused gate) clicked Play Now
# with the physical Arduino into a window not receiving input, once a tick, forever. Focusing
# it made the very next click land and the account logged in.
$st = Get-Bridge "/state"
if ($st -and -not $st.windowFocused) {
    Write-Host "  client window is not focused -- focusing it"
    Start-GuestDesktop "cmd /c $share\vm-guest-focus-client.cmd" "OSRSFocus"
    Start-Sleep -Seconds 3
    $st = Get-Bridge "/state"
}
if ($st -and -not $st.windowFocused) {
    Write-Host "  STILL not focused. controller.py will not click into an unfocused window --" -ForegroundColor Yellow
    Write-Host "  click the game window in the VM." -ForegroundColor Yellow
}

$name = if ($panelMode) { "panel" } else { [IO.Path]::GetFileNameWithoutExtension($routine) }
$log  = "vm_{0}_{1}.log" -f $name, (Get-Date -Format "yyyyMMdd_HHmmss")

# Every token is space-free (log name, routine path, flags), so this survives the
# PowerShell -> python -> SSH -> scheduled-task -> cmd chain without any quoting.
$argv = @("cmd", "/c", "$share\vm-guest-run-routine.cmd", $log)
if (-not $panelMode) { $argv += $routine }   # no routine argument at all == follow the dropdown
$argv += $extra

Start-GuestDesktop ($argv -join " ") "OSRSRoutine"

Write-Host "  started: python -u controller.py $(if (-not $panelMode) { "$routine " })$($extra -join ' ')" -ForegroundColor Green
if ($panelMode) {
    Write-Host "  Now pick it in RuneLite: Controller Settings -> Routine, and tick Controller" -ForegroundColor Yellow
    Write-Host "  Enabled. Both are re-read every loop, so the dropdown switches routine live." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  STOP IT with F1 -- pressed inside the VM window. The panic key is polled in the" -ForegroundColor Yellow
Write-Host "  guest, so a host keypress never reaches it." -ForegroundColor Yellow
Write-Host ""
# The routine runs from the share, so its log is a host file -- no copy-back, no vmexec.
Write-Host "  Log: ControllerAddon\logs\$log   (on THIS machine -- the guest writes it through the share)"
Write-Host "  Watch it:  Get-Content -Wait ControllerAddon\logs\$log"
