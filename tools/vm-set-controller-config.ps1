# Set Controller plugin config keys in the GUEST's active RuneLite profile, from the guest.
#
# WHY THIS EXISTS (2026-09-08): a routine's target list and its when-full policy live in the
# RuneLite Controller Settings panel, not in Python -- gathering.get_config lets the bridge's
# answer win over DEFAULT_CONFIG, deliberately (a panel with nothing selected must mean an idle
# routine, never a surprise trip to the rune rocks). So "run woodcutting on yews, banking" is a
# CONFIG change, and from the host the only way to make one without clicking the panel is to
# write the profile properties file before the client starts.
#
# MUST run with the client STOPPED: RuneLite holds the profile in memory and rewrites the file
# on change/shutdown, so an edit made while it is up is silently discarded.
#
#   powershell -File Z:\Httpbridge\tools\vm-set-controller-config.ps1 treeNames="Yew tree" woodcuttingFullInventory=BANK
#
# Keys are the @ConfigItem keyName from ControllerConfig.java; the file stores them under the
# "controller" config group (ControllerConfig.GROUP), i.e. "controller.<keyName>=<value>".

param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Pairs)

$ErrorActionPreference = "Stop"

if (-not $Pairs) { throw "nothing to set -- pass key=value pairs" }

$dir = Join-Path $env:USERPROFILE ".runelite\profiles2"
if (-not (Test-Path $dir)) { throw "no profiles2 directory at $dir -- was the profile ever copied over?" }

# The active profile is the one profiles.json marks active; fall back to the only file there.
$file = $null
$json = Join-Path $dir "profiles.json"
if (Test-Path $json) {
    $active = (Get-Content $json -Raw | ConvertFrom-Json).profiles | Where-Object { $_.active }
    if ($active) { $file = Join-Path $dir ("{0}-{1}.properties" -f $active.name, $active.id) }
}
if (-not $file -or -not (Test-Path $file)) {
    $all = @(Get-ChildItem (Join-Path $dir "*.properties"))
    if ($all.Count -ne 1) { throw "cannot tell which profile is active ($($all.Count) candidates in $dir)" }
    $file = $all[0].FullName
}

$running = Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
    Where-Object { $_.CommandLine -match 'ExamplePluginTest' }
if ($running) { throw "the client is running (PID $($running.ProcessId)) -- it would overwrite this edit on exit" }

# Read as lines rather than ConvertFrom-StringData: the file holds ~8.5k lines including values
# with '=' and ':' in them, and rewriting it wholesale would reorder and re-escape the lot.
$lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $file)

foreach ($pair in $Pairs) {
    $i = $pair.IndexOf("=")
    if ($i -lt 1) { throw "expected key=value, got '$pair'" }
    $key = "controller." + $pair.Substring(0, $i).Trim()
    $value = $pair.Substring($i + 1)
    $line = "$key=$value"

    $at = -1
    for ($n = 0; $n -lt $lines.Count; $n++) {
        if ($lines[$n].StartsWith("$key=")) { $at = $n; break }
    }
    if ($at -ge 0) {
        if ($lines[$at] -eq $line) { Write-Host "unchanged  $line" ; continue }
        Write-Host ("was        " + $lines[$at])
        $lines[$at] = $line
    } else {
        $lines.Add($line) | Out-Null
    }
    Write-Host "set        $line"
}

Set-Content -LiteralPath $file -Value $lines -Encoding utf8
Write-Host "wrote $file"
