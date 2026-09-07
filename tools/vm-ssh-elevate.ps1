# Raises a UAC consent prompt in the guest's interactive session for the SSH installer.
# Run NON-elevated via guestcontrol; the user clicks Yes on the VM screen. Added 2026-09-07.
Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File Z:\Httpbridge\tools\vm-setup-ssh.ps1'
