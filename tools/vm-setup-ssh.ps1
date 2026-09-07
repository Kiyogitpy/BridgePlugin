<#
.SYNOPSIS
  Install and start the OpenSSH server in the OSRS guest so the host's VSCode Remote-SSH (and a
  host terminal) can work inside the VM. Must run ELEVATED in the guest -- tools/deobfuscate is
  driven from the host via a scheduled task that carries the full admin token (guestcontrol's
  own token is UAC-filtered).

  Added 2026-09-07. Writes a marker + log so the host can confirm success without an interactive
  session.
#>
$ErrorActionPreference = "Continue"
$log = "C:\work\ssh-setup.log"
function L($m) { $m | Tee-Object -FilePath $log -Append }

L "== elevated? $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"

$cap = (Get-WindowsCapability -Online -Name 'OpenSSH.Server*')
L "== capability state before: $($cap.State)"
if ($cap.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
    L "== installed: $((Get-WindowsCapability -Online -Name 'OpenSSH.Server*').State)"
}

Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
L "== sshd service: $((Get-Service sshd).Status), startup Automatic"

# Firewall: the capability adds 'OpenSSH SSH Server (sshd)'; make sure it is enabled (guest is NAT,
# only reachable through the VBox port-forward anyway, but the rule must exist for the guest stack).
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH SSH Server (sshd)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    L "== created firewall rule"
} else {
    Enable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP'
    L "== firewall rule present, enabled"
}

# VSCode Remote-SSH on Windows works with either shell; PowerShell makes a nicer terminal.
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell `
    -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
L "== default ssh shell set to PowerShell"

L "== listening: $((Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count) socket(s) on :22"
# marker on the SHARE so the host can see success
"ok $(Get-Date -Format s)" | Set-Content "Z:\Httpbridge\data\vm-installers\ssh-ready.txt"
L "== DONE"
