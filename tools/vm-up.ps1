<#
.SYNOPSIS
  Bring the OSRS VM to a ready state from the HOST: start it if needed and (re)apply the NAT
  port-forwards. Run this instead of double-clicking the VM, OR after a GUI start.

.DESCRIPTION
  Added 2026-09-07. Everything durable about the VM lives in its own config and persists across
  a power-off: the Arduino USB filter, the Docs shared folder (Z: in the guest), the copied
  SMBIOS hardware UUID, and -- inside the guest -- the RuneLite profile, the Jagex
  credentials.properties, the OpenSSH server, and ~/.gradle/gradle.properties (the JVM
  fingerprint-parity flags). The ONE thing that does NOT persist is the two NAT port-forwards,
  because they were added at runtime (VBoxManage controlvm natpf1); this script re-adds them
  every start, idempotently.

  Forwards: host 127.0.0.1:2222 -> guest 22 (SSH, for VSCode Remote-SSH / a host terminal) and
  host 127.0.0.1:18080 -> guest 8080 (read-only bridge probing from the host).
#>
$vb = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
$vm = "OSRS"

$state = (& $vb showvminfo $vm --machinereadable | Select-String '^VMState=').Line
$wasDown = $state -notmatch 'running'
if ($wasDown) {
    Write-Host "starting $vm ..."
    & $vb startvm $vm --type gui | Out-Null
} else { Write-Host "$vm already running" }

# The forwards go on BEFORE the readiness probe, because that probe is now SSH -- which arrives
# through the 2222 forward. (It used to be `guestcontrol`, which died on this setup 2026-09-08;
# see tools/vmexec.py.) controlvm natpf1 can refuse for a second or two right after startvm.
foreach ($rule in @("ssh,tcp,127.0.0.1,2222,,22", "bridge,tcp,127.0.0.1,18080,,8080")) {
    $name = $rule.Split(",")[0]
    $have = (& $vb showvminfo $vm --machinereadable | Select-String "Forwarding.*`"$name,").Line
    if ($have) { Write-Host "forward '$name' already present"; continue }
    for ($try = 0; $try -lt 5; $try++) {
        & $vb controlvm $vm natpf1 $rule 2>&1 | Out-Null
        $have = (& $vb showvminfo $vm --machinereadable | Select-String "Forwarding.*`"$name,").Line
        if ($have) { Write-Host "added forward '$name'"; break }
        Start-Sleep 2
    }
    if (-not $have) { Write-Host "could not add forward '$name'" -ForegroundColor Red }
}

# Guest readiness: Windows booted, auto-logged in (AutoAdminLogon=1) and sshd answering. Only
# worth waiting for on a cold start -- everything downstream (vmexec, the bridge) needs it.
if ($wasDown) {
    $vmexec = Join-Path $PSScriptRoot "vmexec.py"
    # MEASURED 2026-09-08: while the guest is still booting, paramiko writes "Error reading SSH
    # protocol banner" to STDERR -- and PowerShell 5.1 turns a native command's stderr into a
    # NativeCommandError, which is TERMINATING under ErrorActionPreference=Stop (which this
    # script inherits from vm-session.ps1). That killed the whole cold start on the first
    # probe. A readiness loop must survive its own failures: preference relaxed, stderr dropped.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $t0 = Get-Date
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep 4
        $r = & python $vmexec --timeout 15 "hostname" 2>$null
        if ("$r" -match 'osrs-vm') {
            Write-Host ("guest ready after {0:n0}s" -f ((Get-Date) - $t0).TotalSeconds)
            break
        }
    }
    $ErrorActionPreference = $prevEAP
    if ("$r" -notmatch 'osrs-vm') { Write-Host "guest never answered over SSH -- is it booting?" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "VM ready. Next:"
Write-Host "  * SSH / VSCode Remote-SSH:  host 'osrs-vm' (127.0.0.1:2222, user kiyo, password osrs)"
Write-Host "  * Or do the whole session in one command from the host:  .\run-vm.bat [routine]"
Write-Host "  * Start the client in the guest by hand (once per session):"
Write-Host "      ssh osrs-vm  ->  powershell C:\work\Httpbridge\tools\vm-run-client.ps1"
Write-Host "  * Run a routine in the guest -- from the SHARE, so no sync is needed:"
Write-Host "      cd Z:\Httpbridge\ControllerAddon ; py controller.py routines\mining.py   (F1 stops)"
Write-Host "  * Run any command in the guest from here:  python tools\vmexec.py `"<command>`""
