@echo off
REM Runs INSIDE the guest, over SSH via tools\vmexec.py. Mirrors the host share into the guest's
REM build/run copy at C:\work and copies guest logs back -- the whole of vm-sync.ps1.
REM
REM It exists as a .cmd on the share so the host passes ONE unquoted token, and because
REM vm-sync.ps1 addresses the share as Z: while a drive letter is per-LOGON-SESSION: the SSH
REM session is not the interactive one, so Z: need not be mapped there. \\VBOXSVR\Docs is the
REM same share with no mapping needed, so map it first when it is missing.
if not exist Z:\Httpbridge\ControllerAddon\controller.py net use Z: \\VBOXSVR\Docs >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File Z:\Httpbridge\tools\vm-sync.ps1