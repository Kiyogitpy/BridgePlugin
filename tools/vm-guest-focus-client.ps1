<#
  Focus AND MAXIMIZE the RuneLite window IN THE GUEST. Runs on the guest DESKTOP
  (tools\vmexec.py --interactive), never over plain SSH -- an SSH session has no window station,
  and it does not even see the real display (it reports a phantom 1024x768 "WinDisc" screen).

  Why focus: a client launched from a scheduled task does NOT come up focused, and MEASURED
  2026-09-08 that is fatal rather than cosmetic -- /state read windowFocused:False, and
  controller.py's login path (login.attempt_login, which runs BEFORE the engine's own focus
  gate) clicked Play Now with the physical Arduino into a window not receiving input, once a
  tick, forever. Focusing it made the very next click land and the account logged in.

  Why SW_SHOWMAXIMIZED and not SW_RESTORE: RESTORE on an ALREADY-MAXIMIZED window shrinks it
  back to its pre-maximised size -- the opposite of what is wanted. Maximised is also simply
  the right state: the guest desktop is the ceiling on game area, so anything smaller wastes it.
#>
$sig = '[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
        [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);'
$u = Add-Type -MemberDefinition $sig -Name Win -Namespace Fg -PassThru
$p = Get-Process |
     Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match 'RuneLite' } |
     Select-Object -First 1
if (-not $p) { Write-Host "no RuneLite window found"; exit 1 }

$SW_SHOWMAXIMIZED = 3
[void]$u::ShowWindow($p.MainWindowHandle, $SW_SHOWMAXIMIZED)
[void]$u::SetForegroundWindow($p.MainWindowHandle)
Start-Sleep -Milliseconds 500
Add-Type -AssemblyName System.Windows.Forms
$scr = [System.Windows.Forms.Screen]::PrimaryScreen
Write-Host "focused + maximized: $($p.MainWindowTitle) | zoomed=$($u::IsZoomed($p.MainWindowHandle)) | desktop=$($scr.Bounds.Width)x$($scr.Bounds.Height) work=$($scr.WorkingArea.Width)x$($scr.WorkingArea.Height)"