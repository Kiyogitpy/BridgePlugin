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
if ($state -notmatch 'running') {
    Write-Host "starting $vm ..."
    & $vb startvm $vm --type gui | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 4
        $r = & $vb guestcontrol $vm run --username Kiyo --password osrs --exe "C:\Windows\System32\cmd.exe" --wait-stdout --timeout 15000 -- cmd.exe /c "echo up" 2>&1
        if ($r -match 'up') { Write-Host "guest ready after $((($i+1)*4))s"; break }
    }
} else { Write-Host "$vm already running" }

foreach ($rule in @("ssh,tcp,127.0.0.1,2222,,22", "bridge,tcp,127.0.0.1,18080,,8080")) {
    $name = $rule.Split(",")[0]
    $have = (& $vb showvminfo $vm --machinereadable | Select-String "Forwarding.*`"$name,").Line
    if ($have) { Write-Host "forward '$name' already present" }
    else { & $vb controlvm $vm natpf1 $rule 2>&1 | Out-Null; Write-Host "added forward '$name'" }
}

Write-Host ""
Write-Host "VM ready. Next:"
Write-Host "  * SSH / VSCode Remote-SSH:  host 'osrs-vm' (127.0.0.1:2222, user kiyo, password osrs)"
Write-Host "  * Start the client in the guest (once per session):"
Write-Host "      ssh osrs-vm  ->  powershell C:\work\Httpbridge\tools\vm-run-client.ps1"
Write-Host "  * Run a routine in the guest:"
Write-Host "      cd Z:\Httpbridge\ControllerAddon ; py controller.py routines\mining.py   (F1 stops)"
