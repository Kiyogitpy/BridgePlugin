# Minimise every console window on the guest desktop, then raise and maximise RuneLite.
#
# WHY THIS EXISTS -- MEASURED 2026-09-09, and it is a whole class of silent failure, not a
# cosmetic one. A routine launched by tools\vmexec.py --interactive runs as a scheduled task
# on the logged-on desktop, which opens a CONSOLE window there. When that console sits over
# the 3D viewport, the client reports canvas mouse (-1,-1) for every point under it, so every
# WORLD click is swallowed while inventory and sidebar clicks (further right, uncovered) keep
# working perfectly. In routines/vale_totems.py that looked like "claim did not land" at site
# after site with the build/carve/decorate clicks on the same object landing fine.
#
# The tell, if it happens again: move the cursor to an object's reported screenX/screenY and
# compare /state's own mouseScreenX/Y. Equal means the client is seeing the cursor; (-2,21)
# (canvas -1,-1 converted to screen) means something is covering the canvas there.
# `windowFocused` does NOT catch this -- it was true throughout.
$sig = @"
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
"@
$u = Add-Type -MemberDefinition $sig -Name HideWin -Namespace Vt -PassThru

$SW_MINIMIZE = 6
$SW_SHOWMAXIMIZED = 3

$hidden = @()
foreach ($p in Get-Process | Where-Object {
        $_.MainWindowHandle -ne 0 -and
        $_.ProcessName -match '^(cmd|conhost|WindowsTerminal|powershell|pwsh|python|py)$' }) {
    [void]$u::ShowWindow($p.MainWindowHandle, $SW_MINIMIZE)
    $hidden += "$($p.ProcessName)($($p.Id))"
}
Write-Host "minimised: $($hidden -join ', ')"

$client = Get-Process | Where-Object {
    $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match 'RuneLite' } | Select-Object -First 1
if ($client) {
    [void]$u::ShowWindow($client.MainWindowHandle, $SW_SHOWMAXIMIZED)
    [void]$u::SetForegroundWindow($client.MainWindowHandle)
    Write-Host "raised: $($client.MainWindowTitle)"
} else {
    Write-Host "no RuneLite window found"
}
