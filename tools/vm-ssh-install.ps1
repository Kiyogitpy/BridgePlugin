<#
  Registrar (runs NON-elevated in the guest via guestcontrol). Registers a scheduled task that
  runs vm-setup-ssh.ps1 as Kiyo with RunLevel Highest -- an admin's own task gets the full
  (unfiltered) token at run time, which is how we install OpenSSH without an interactive UAC
  click. Added 2026-09-07.
#>
$ErrorActionPreference = "Stop"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File Z:\Httpbridge\tools\vm-setup-ssh.ps1'
Register-ScheduledTask -TaskName 'SshSetup' -Action $action `
    -User 'kiyo' -Password 'osrs' -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName 'SshSetup'
"registered and started SshSetup"
