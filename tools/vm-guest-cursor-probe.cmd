@echo off
REM Cursor-landing probe, on the guest DESKTOP (vmexec.py --interactive).
set BRIDGE_SERIAL_PORT=COM3
set PYTHONDONTWRITEBYTECODE=1
if not exist Z:\Httpbridge\ControllerAddon\controller.py net use Z: \\VBOXSVR\Docs >nul 2>&1
cd /d Z:\Httpbridge\ControllerAddon || exit /b 1
set PYEXE=python
where python >nul 2>&1 || set PYEXE=py
%PYEXE% -u tools_vt_cursor_probe.py > logs\vt-cursor-probe.out 2>&1
