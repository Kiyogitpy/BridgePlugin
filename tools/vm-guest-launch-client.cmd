@echo off
REM Runs INSIDE the guest on the DESKTOP (a scheduled task -- tools\vmexec.py --interactive),
REM because the client is a GUI process and an SSH session has no window station.
REM
REM It lives on the share as a .cmd so the host never has to quote a long command line, and
REM because Z: is a per-LOGON-SESSION drive mapping that need not exist in the session this
REM runs in. \\VBOXSVR\Docs is the same VirtualBox share with no mapping needed, so map it here
REM when it is missing -- vm-run-client.ps1 addresses the share as Z:.
REM
REM -NoSync: vm-session.ps1 runs vm-guest-sync.cmd as its own step, every session, whether or
REM not the client needs launching. Syncing here too would just do it twice.
if not exist Z:\Httpbridge\ControllerAddon\controller.py net use Z: \\VBOXSVR\Docs >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File Z:\Httpbridge\tools\vm-run-client.ps1 -NoSync