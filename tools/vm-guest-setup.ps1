<#
.SYNOPSIS
  Provision the OSRS VirtualBox guest so it can run the RuneLite client, the bridge and
  controller.py against the passed-through Arduino. Runs INSIDE the guest as the ordinary
  user (no elevation needed); idempotent; safe to re-run.

.DESCRIPTION
  Added 2026-09-07 with the VM setup (CLAUDE.md "Running things" -> "The VM"). The host keeps
  the repo; the guest sees the host's Documents folder through the VirtualBox shared folder
  "Docs", which Windows mounted at Z: (D: went to the DVD drive), so Z:\Httpbridge and
  Z:\ZombieLite keep the ../ZombieLite relative layout the build needs.

  MEASURED in the guest 2026-09-07: Windows 10 Pro build 19045 (same as the host), SMBIOS UUID
  identical to the host's, no winget (a fresh 22H2 image ships without the App Installer
  update), the Leonardo enumerates as "USB Serial Device (COM3)" plus its HID mouse and
  keyboard, default 96 DPI. Hence: installers come from Z:\Httpbridge\data\vm-installers\
  (downloaded on the host; data\ is gitignored), per-user installs only, and
  BRIDGE_SERIAL_PORT=COM3.

  Python 3.13.7 (host's version) as a per-user install; Temurin JDK 11.0.30+7 -- the host's
  EXACT client JVM build, because java.vendor/java.version are in the login fingerprint --
  unzipped under %LOCALAPPDATA%\Programs\jdk-11.0.30+7 and exported as JAVA_HOME. Git is not
  installed: the repo lives on the host, and gradlew needs no git.
#>
$ErrorActionPreference = "Stop"
$repo = "Z:\Httpbridge"
$inst = "$repo\data\vm-installers"
$jdkVersion = "jdk-11.0.30+7"
$jdkHome = Join-Path $env:LOCALAPPDATA "Programs\$jdkVersion"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

Step "Shared folder"
if (-not (Test-Path "$repo\ControllerAddon\controller.py")) {
    throw "Repo not visible at $repo. VirtualBox > Devices > Shared Folders must show 'Docs' auto-mounted; log out and in once if Z: is missing."
}
if (-not (Test-Path "$inst\python-3.13.7-amd64.exe") -or -not (Test-Path "$inst\temurin-$jdkVersion.zip")) {
    throw "Installers missing under $inst -- download them on the host first (see this script's header)."
}

Step "Python 3.13"
$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
if (Test-Path $py) { "  already installed: " + (& $py --version) } else {
    Start-Process -Wait -FilePath "$inst\python-3.13.7-amd64.exe" -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", "Include_launcher=1"
    if (-not (Test-Path $py)) { throw "Python install did not produce $py" }
    "  installed: " + (& $py --version)
}

Step "Temurin $jdkVersion"
if (Test-Path "$jdkHome\bin\java.exe") { "  already present at $jdkHome" } else {
    $tmp = Join-Path $env:TEMP "jdk-extract"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path "$inst\temurin-$jdkVersion.zip" -DestinationPath $tmp
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force (Split-Path $jdkHome) | Out-Null
    Move-Item $inner.FullName $jdkHome
    Remove-Item -Recurse -Force $tmp
}
# java prints its version on stderr, and under $ErrorActionPreference = Stop PowerShell 5.1
# turns a native stderr line into a terminating error -- so go through cmd, which merges it.
"  " + ((& cmd.exe /c "`"$jdkHome\bin\java.exe`" -version 2>&1") | Select-Object -First 1)
[Environment]::SetEnvironmentVariable("JAVA_HOME", $jdkHome, "User")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$jdkHome\bin*") { [Environment]::SetEnvironmentVariable("Path", "$jdkHome\bin;$userPath", "User") }
"  JAVA_HOME=$jdkHome (user scope)"

Step "Serial port"
[Environment]::SetEnvironmentVariable("BRIDGE_SERIAL_PORT", "COM3", "User")
$ports = [System.IO.Ports.SerialPort]::getportnames() -join ","
"  BRIDGE_SERIAL_PORT=COM3 (user scope); ports present now: $ports"
if ($ports -notmatch "COM3") { Write-Warning "COM3 not present -- check VirtualBox > Devices > USB captured the Leonardo, then re-run." }

Step "pip requirements"
& $py -m pip install --disable-pip-version-check --quiet --upgrade pip
& $py -m pip install --disable-pip-version-check -r "$repo\ControllerAddon\requirements.txt" 2>&1 | Select-Object -Last 3

Step "Pointer settings"
# Windows' "Enhance pointer precision" (mouse acceleration) makes px-per-unit speed-dependent;
# controller.py warns at startup if it is on. Off = MouseSpeed 0, thresholds 0.
Set-ItemProperty 'HKCU:\Control Panel\Mouse' -Name MouseSpeed -Value "0"
Set-ItemProperty 'HKCU:\Control Panel\Mouse' -Name MouseThreshold1 -Value "0"
Set-ItemProperty 'HKCU:\Control Panel\Mouse' -Name MouseThreshold2 -Value "0"
"  Enhance pointer precision: off (takes effect at next logon)"

Step "Left, by hand"
@"
  1. Log out and back in once (PATH / JAVA_HOME / BRIDGE_SERIAL_PORT are user-scope variables).
  2. cd $repo ; .\gradlew.bat run   -- first run fills the guest's Gradle cache (~1 GB).
  3. Fingerprint parity (CPU count and heap): add to the run task's jvmArgs in the GUEST only
       -XX:ActiveProcessorCount=32   -Xmx<host's default heap>
     Host value: on the host,  java -XX:+PrintFlagsFinal -version | findstr MaxHeapSize
  4. View > Full-screen the VM on the second monitor at 100% guest DPI.
  5. cd $repo\ControllerAddon ; python -u controller.py --skip-deob-check
     (drop the flag once the guest's client has been rebuilt with /client-version).
"@
