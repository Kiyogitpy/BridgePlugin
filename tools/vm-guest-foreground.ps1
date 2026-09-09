# What window is in the FOREGROUND of the guest desktop right now, and the RuneLite window's
# own state. Diagnostic only, added 2026-09-09 while the vale_totems run sat on "window is
# not focused" after both focus helpers reported Ready. Run it on the DESKTOP session
# (vmexec.py --interactive) -- over plain SSH there is no window station and it reports nothing.
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class FgWin {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
}
"@
$h = [FgWin]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 512
[void][FgWin]::GetWindowText($h, $sb, 512)
$pid2 = 0
[void][FgWin]::GetWindowThreadProcessId($h, [ref]$pid2)
$name = try { (Get-Process -Id $pid2 -ErrorAction Stop).ProcessName } catch { "?" }
"foreground: hwnd=$h pid=$pid2 ($name) title='$($sb.ToString())'"
Get-Process | Where-Object { $_.MainWindowTitle } | ForEach-Object {
    "window: pid=$($_.Id) $($_.ProcessName) title='$($_.MainWindowTitle)' iconic=$([FgWin]::IsIconic($_.MainWindowHandle))"
}
