<#
.SYNOPSIS
  Mirror the host's repo (seen through the VirtualBox share at Z:) into a guest-local working
  copy, and copy the guest's run artefacts back. Runs INSIDE the guest; idempotent; fast.

.DESCRIPTION
  Added 2026-09-07. The guest builds and runs from C:\work\Httpbridge, NOT from the share:
  host and guest Gradle runs would otherwise share one build\ and .gradle\ on a network drive
  and fight over locks and cached absolute paths. "Edit on the host, run in the guest" is
  therefore: edit, then run this script in the guest (a second or two -- robocopy copies only
  what changed), then run.

  Excluded from the mirror: .git, build, .gradle, data (5.5 GB of reference material; the
  routing map the controller needs is inside ControllerAddon\maps, which IS copied),
  recordings, logs, map-output, __pycache__. Without data\ the deob guard sees no rl-suite in
  the guest and only warns, which is the intended behaviour for a machine that cannot
  deobfuscate (deob_guard.py).

  Copied BACK to the host (new files only, nothing deleted): ControllerAddon\logs and
  ControllerAddon\recordings, so a guest run's logs and recorder output land where every
  tool on the host already looks for them.
#>
$ErrorActionPreference = "Continue"
$src = "Z:\Httpbridge"; $dst = "C:\work\Httpbridge"
$zlSrc = "Z:\ZombieLite"; $zlDst = "C:\work\ZombieLite"
$exclude = @(".git", "build", ".gradle", "data", "recordings", "logs", "map-output", "__pycache__", ".pytest_cache")

if (-not (Test-Path "$src\ControllerAddon\controller.py")) { throw "share not visible at $src" }
New-Item -ItemType Directory -Force $dst, $zlDst | Out-Null

Write-Host "== Httpbridge -> $dst"
robocopy $src $dst /MIR /XD $exclude /XF "*.log" /R:2 /W:1 /NFL /NDL /NJH /NP | Select-Object -Last 8
Write-Host "== ZombieLite -> $zlDst"
if (Test-Path "$zlSrc\build.gradle") {
    robocopy $zlSrc $zlDst /MIR /XD ".git" "build" ".gradle" /R:2 /W:1 /NFL /NDL /NJH /NP | Select-Object -Last 8
} else { Write-Host "   (no ZombieLite checkout on the share -- the client runs with the bridge alone)" }

Write-Host "== guest logs/recordings -> host"
foreach ($d in @("ControllerAddon\logs", "ControllerAddon\recordings")) {
    if (Test-Path "$dst\$d") {
        New-Item -ItemType Directory -Force "$src\$d" | Out-Null
        robocopy "$dst\$d" "$src\$d" /E /XO /R:2 /W:1 /NFL /NDL /NJH /NP | Select-Object -Last 3
    }
}
# robocopy exit codes < 8 are success variants; never let a "files copied" code fail the caller
exit 0
