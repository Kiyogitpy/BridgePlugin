@echo off
REM Runs INSIDE the guest on the DESKTOP (a scheduled task -- tools\vmexec.py --interactive).
REM That is not an optimisation: controller.py drives a physical cursor through Win32, and an
REM SSH session has no window station, so the same command over plain SSH dies with
REM WinError 1459 (see vmexec.py's own note).
REM
REM Usage:  vm-guest-run-routine.cmd <logfile> [args passed to controller.py ...]
REM         vm-guest-run-routine.cmd vm_panel_20260908_1200.log
REM         vm-guest-run-routine.cmd vm_mining_20260908_1200.log routines\mining.py --debug-log
REM
REM The log name is argument 1 rather than an environment variable because a scheduled task
REM inherits none of the caller's environment. No argument may contain a space.
REM
REM With NO controller.py arguments this is PANEL MODE: controller.py then re-reads the
REM RuneLite plugin's Routine dropdown every loop and hot-swaps when it changes.
REM
REM IT RUNS FROM THE SHARE (Z:), NOT FROM THE C:\work MIRROR, and that is the point: Z:
REM \Httpbridge IS the host's repo, so a Python edit on the host is live here with no sync at
REM all (MEASURED 2026-09-08: import worldmap+controller+travel off the share 0.31s, a route
REM 0.026s). C:\work exists only because host and guest GRADLE would fight over one build\ on
REM a network drive -- Python has no build step. The logs therefore land on the host's own
REM ControllerAddon\logs too, readable without a copy-back.
setlocal
if "%~1"=="" (echo vm-guest-run-routine.cmd: first argument must be the log file name & exit /b 2)
set ROUTINE_LOG=%~1
shift

REM Rebuild the remaining arguments: %* still holds the ORIGINAL list after shift, so it cannot
REM be used here -- the log name would be passed to controller.py as the routine path.
set ARGS=
:collect
if "%~1"=="" goto run
set ARGS=%ARGS% %~1
shift
goto collect

:run
REM The Arduino is COM3 in the guest (COM6 on the host, which loses the device to the VM's USB
REM filter while the VM runs). controller.py reads BRIDGE_SERIAL_PORT (settings.py).
set BRIDGE_SERIAL_PORT=COM3
REM Never scatter __pycache__ into the host's repo: two machines would be writing it.
set PYTHONDONTWRITEBYTECODE=1
if not exist Z:\Httpbridge\ControllerAddon\controller.py net use Z: \\VBOXSVR\Docs >nul 2>&1
cd /d Z:\Httpbridge\ControllerAddon || exit /b 1
if not exist logs mkdir logs
REM Python 3.13 is a per-user install in the guest; fall back to the py launcher if this
REM session did not pick up the user PATH.
set PYEXE=python
where python >nul 2>&1 || set PYEXE=py
REM MINIMISED, and that is load-bearing rather than tidy. MEASURED live 2026-09-09
REM (vale_totems in the VM): this console sits on the guest desktop for the whole run,
REM and when it covers the 3D viewport the client reports canvas mouse (-1,-1) for every
REM point under it -- so every WORLD click is swallowed while inventory and sidebar clicks
REM (further right, uncovered) keep landing perfectly. It reads as "claim did not land"
REM at site after site, with `windowFocused` reporting TRUE throughout, so neither the
REM engine's focus gate nor the session script's focus step catches it. Focus is not the
REM same question as z-order: raising the client alone did NOT fix it, minimising this
REM console did (clean A/B, runs 3 and 4).
start "osrs-routine" /min cmd /c "%PYEXE% -u controller.py%ARGS% > logs\%ROUTINE_LOG% 2>&1"