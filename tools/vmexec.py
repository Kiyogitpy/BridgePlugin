"""Run a command inside the OSRS VM over SSH, from the host.

WHY THIS EXISTS (2026-09-08): `VBoxManage guestcontrol` stopped executing anything on this
setup -- both `run --wait-stdout` and the async `start` form hang or silently do nothing, with
the guest logged in (`GuestInfo/OS/LoggedInUsers = 1`) and Guest Additions at run level 3, and
after closing six piled-up `Status=[started]` sessions. Rebooting the guest did not fix it.

The OpenSSH server CLAUDE.md documents (`tools/vm-setup-ssh.ps1`, host 127.0.0.1:2222 -> guest
22) is unaffected: a plain `ssh` attempt returns
`Permission denied (publickey,password,keyboard-interactive)`, which is the server accepting the
connection and offering password auth. The only thing missing was a way to supply the password
without a TTY -- hence paramiko rather than the `ssh` binary.

Usage:
    python tools/vmexec.py "cd C:\\work\\Httpbridge && git log --oneline -1"
    python tools/vmexec.py --timeout 900 "gradlew.bat --no-daemon compileJava"
    python tools/vmexec.py --detach "start /b python -u controller.py routines/x.py"

Exit code is the guest command's own. stdout/stderr are streamed back.
"""

import argparse
import sys

HOST = "127.0.0.1"
PORT = 2222
USER = "kiyo"
PASSWORD = "osrs"        # documented in CLAUDE.md; this VM is a local, NAT-forwarded guest


def connect(timeout=20):
    import paramiko

    client = paramiko.SSHClient()
    # A forwarded localhost port to a throwaway local guest -- host-key pinning buys nothing
    # here, and CLAUDE.md's own ~/.ssh/config block for `osrs-vm` already disables it.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD,
                   timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                   look_for_keys=False, allow_agent=False)
    return client


def run(command, timeout=300, detach=False):
    client = connect()
    try:
        if detach:
            # Fire and forget. The guest's default shell is PowerShell 5.1, where `start` is
            # Start-Process and cmd's `start "" /b` syntax is a parse error -- so the command is
            # written to a .cmd on the SHARE (Z: in the guest IS this repo on the host, so the
            # host can author it directly with no transfer) and Start-Process launches that.
            # Quoting a long command line through SSH -> PowerShell -> cmd is how this goes wrong
            # otherwise; a file has no quoting to get wrong.
            import os
            here = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(here, "_vm_task.cmd"), "w", encoding="ascii") as fh:
                fh.write("@echo off\r\n" + command + "\r\n")
            command = ("Start-Process -FilePath 'cmd.exe' "
                       "-ArgumentList '/c','Z:\\Httpbridge\\tools\\_vm_task.cmd' "
                       "-WindowStyle Hidden")
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if out:
            sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
        return code
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description="Run a command inside the OSRS VM over SSH.")
    parser.add_argument("command", help="the command line to run in the guest")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--detach", action="store_true",
                        help="start it and return immediately (long-running things)")
    args = parser.parse_args()
    return run(args.command, timeout=args.timeout, detach=args.detach)


if __name__ == "__main__":
    sys.exit(main())
