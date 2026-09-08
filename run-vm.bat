@echo off
REM One command, from the HOST, for a whole session in the OSRS VM: start the VM, launch the
REM client in it, wait until the character is actually in the game world, then start
REM controller.py in the guest (where the Arduino lives). Added 2026-09-08.
REM
REM   run-vm.bat                      panel mode: the RuneLite Controller Settings "Routine"
REM                                   dropdown picks the routine, and switching it switches
REM                                   routine live (controller.py re-reads it every loop)
REM   run-vm.bat mining               pin routines\mining.py instead, ignoring the dropdown
REM   run-vm.bat karambwan_fishing --debug-log      extra args go to controller.py
REM   run-vm.bat -NoRoutine           just bring the VM + client up, start no controller.py
REM   run-vm.bat mining -SkipLoginGate              start even if it never reached the world
REM
REM F1 stops the routine -- pressed INSIDE the VM window, since the panic key is polled in
REM the guest. All the real work is in tools\vm-session.ps1; this only exists so the thing is
REM double-clickable.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\vm-session.ps1" %*
set RC=%ERRORLEVEL%
REM Hold the window open in the two cases where it would otherwise vanish unread: a
REM double-click from Explorer (which can never pass arguments) and any failure.
if "%~1"=="" (pause) else if not "%RC%"=="0" pause
exit /b %RC%