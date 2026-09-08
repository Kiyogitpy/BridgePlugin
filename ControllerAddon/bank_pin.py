"""The bank PIN, encrypted at rest with Windows DPAPI.

WHY THIS EXISTS (2026-09-08). A fresh client login prompts for the bank PIN on the first bank
access, and the booth click then opens the PIN keypad instead of the bank -- which stops every
banking routine dead (MEASURED in the VM: banker.open_bank retried the booth 15 times and
reported "could not open Bank booth", a message that sent me looking at aim and geometry that
were never wrong). Unattended running across a relogin needs the PIN available to the routine.

WHAT THE ENCRYPTION IS AND IS NOT. `CryptProtectData` with CRYPTPROTECT_LOCAL_MACHINE unset
ties the ciphertext to THIS WINDOWS USER on THIS MACHINE: the file is useless if it is copied
elsewhere, read out of a backup, synced to another host, or opened by another account. It is
NOT protection from code already running as this user -- that can call CryptUnprotectData just
as this module does. Nobody should mistake this for a secret store; it is the difference
between "a PIN sitting in plaintext in a shared folder" and "a blob only this account can
read", which is exactly the exposure that matters here, because ControllerAddon lives on the
VirtualBox share and is therefore visible to BOTH the host and the guest.

WHY IT IS KEYED BY MACHINE. The routine runs in the GUEST (the Arduino is captured there), but
the same file is reachable from the host through the share. A DPAPI blob made on one cannot be
decrypted by the other, so the store is a dict of {machine name: blob} and each machine writes
its own entry. A machine with no entry simply gets None -- never an error, never a fallback to
someone else's blob.

The file is `.bank_pin` next to this module (the same convention as .hm2_calibration.json) and
is gitignored. Nothing here is ever logged: `load()` returns the digits and every caller is
responsible for never printing them.

    python bank_pin.py --set       # prompts, echo off; the PIN never enters argv or history
    python bank_pin.py --check     # says only whether this machine can read a PIN
"""

import base64
import ctypes
import ctypes.wintypes
import json
import os
import platform

PIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bank_pin")

# Application-specific entropy, mixed into the DPAPI key. Not a secret (it is right here in the
# source); it only means a blob from this tool cannot be decrypted by an unrelated one that
# happens to run as the same user.
_ENTROPY = b"BridgePlugin/bank-pin/v1"

# The env var is the documented escape hatch, but note it is NOT how the VM gets its PIN: a
# guest scheduled task inherits no environment (CLAUDE.md), so run-vm.bat's routine would never
# see one. It exists for a host-side one-off.
ENV_VAR = "OSRS_BANK_PIN"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: _Blob) -> bytes:
    out = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return out


def _protect(plaintext: str) -> bytes:
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_to_blob(plaintext.encode("utf-8"))), None,
        ctypes.byref(_to_blob(_ENTROPY)), None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("CryptProtectData failed (error %d)" % ctypes.GetLastError())
    return _from_blob(out)


def _unprotect(ciphertext: bytes) -> str:
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_to_blob(ciphertext)), None,
        ctypes.byref(_to_blob(_ENTROPY)), None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("CryptUnprotectData failed (error %d)" % ctypes.GetLastError())
    return _from_blob(out).decode("utf-8")


def machine_key() -> str:
    """Which entry in the store belongs to the machine running this. The host and the guest
    are both "Kiyo" but different computers, and a DPAPI blob does not cross between them."""
    return "{0}\\{1}".format(platform.node() or "?", os.environ.get("USERNAME") or "?")


def _read_store() -> dict:
    try:
        with open(PIN_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Missing, empty, or hand-mangled -- all mean "no PIN available here", never an error
        # that stops a routine from starting.
        return {}


def load():
    """The PIN for this machine, or None. NEVER log the return value."""
    env = os.environ.get(ENV_VAR)
    if env and env.strip():
        return env.strip()
    entry = _read_store().get(machine_key())
    if not entry:
        return None
    try:
        return _unprotect(base64.b64decode(entry))
    except Exception:
        # A blob written by a different user/machine, or a corrupted file. Treat as absent.
        return None


def store(pin: str) -> None:
    """Encrypt `pin` for this machine and write it, preserving other machines' entries."""
    pin = (pin or "").strip()
    if not pin.isdigit():
        raise ValueError("a bank PIN is digits only")
    data = _read_store()
    data[machine_key()] = base64.b64encode(_protect(pin)).decode("ascii")
    tmp = PIN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, PIN_FILE)


def _main():
    import argparse
    import getpass

    ap = argparse.ArgumentParser(description="Store/check the bank PIN for THIS machine.")
    ap.add_argument("--set", action="store_true",
                    help="prompt for the PIN (echo off) and store it encrypted")
    ap.add_argument("--check", action="store_true",
                    help="report only whether a PIN can be read here -- never prints it")
    args = ap.parse_args()

    if args.set:
        # getpass, not argv: a PIN on a command line lands in shell history, in the process
        # list, and in any log that echoes the command.
        store(getpass.getpass("Bank PIN (not echoed): "))
        print("stored for %s" % machine_key())
    if args.check or not args.set:
        pin = load()
        print("machine: %s" % machine_key())
        print("PIN available here: %s%s" % (
            bool(pin), " (%d digits)" % len(pin) if pin else ""))
        print("store: %s" % ("present" if os.path.exists(PIN_FILE) else "absent"))
        known = list(_read_store().keys())
        if known:
            print("entries for: %s" % ", ".join(known))


if __name__ == "__main__":
    _main()
