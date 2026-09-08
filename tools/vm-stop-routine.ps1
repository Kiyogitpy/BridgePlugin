# Stop controller.py in the OSRS guest, from the HOST. Added 2026-09-08.
#
# WHY IT IS NOT JUST "kill python": a SAFETY STOP PAUSES controller.py, it does not exit it --
# the process stays alive printing "Still stopped" and keeps holding COM3, so run-vm.bat then
# refuses to start a replacement and a stopped run looks like a stuck one. This is also what
# you want before a GAME UPDATE: stop the Arduino driving input before the client is forced
# out, rather than letting it click into a logout.
#
#   powershell -File tools\vm-stop-routine.ps1
$ErrorActionPreference = "Continue"
$repo = Split-Path $PSScriptRoot -Parent
& python (Join-Path $PSScriptRoot "vmexec.py") `
    "powershell -NoProfile -ExecutionPolicy Bypass -File \VBOXSVR\Docs\Httpbridge\tools\_vm_kill_routine.ps1"
exit $LASTEXITCODE
