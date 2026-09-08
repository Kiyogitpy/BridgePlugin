@echo off
REM Runs INSIDE the guest on the DESKTOP (tools\vmexec.py --interactive). See the .ps1 for why.
if not exist Z:\Httpbridge\ControllerAddon\controller.py net use Z: \\VBOXSVR\Docs >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File Z:\Httpbridge\tools\vm-guest-focus-client.ps1