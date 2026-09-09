@echo off
REM One Claim click at a Vale totem pile, on the guest DESKTOP (vmexec.py --interactive), for
REM the 2026-09-09 silent-claim diagnosis. Same shape as vm-guest-run-routine.cmd.
set BRIDGE_SERIAL_PORT=COM3
set PYTHONDONTWRITEBYTECODE=1
if not exist Z:\Httpbridge\ControllerAddon\controller.py net use Z: \\VBOXSVR\Docs >nul 2>&1
cd /d Z:\Httpbridge\ControllerAddon || exit /b 1
set PYEXE=python
where python >nul 2>&1 || set PYEXE=py
%PYEXE% -u tools_vt_claim_probe.py %1 > logs\vt-claim-probe.out 2>&1
