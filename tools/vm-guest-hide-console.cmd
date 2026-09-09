@echo off
REM Minimise guest consoles and raise the client. See vm-guest-hide-console.ps1 for why.
if not exist Z:\Httpbridge\ControllerAddon\controller.py net use Z: \\VBOXSVR\Docs >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File \\VBOXSVR\Docs\Httpbridge\tools\vm-guest-hide-console.ps1 > Z:\Httpbridge\ControllerAddon\logs\vt-hide-console.out 2>&1
