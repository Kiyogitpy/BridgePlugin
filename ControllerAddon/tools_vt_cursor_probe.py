"""Does the physical cursor land where the bridge says to click, in the guest?

Moves to a few known points and compares the request with /state's own mouseScreenX/Y (what
the client sees). Diagnostic for the 2026-09-09 VM session: /hover answered "Cancel" over a
Vale offerings pile's reported point AND for 40px around it, which is dead space rather than a
near miss -- so the question is whether the cursor is arriving at all.
"""
import json
import os
import time

import serial

import controller
import input_state
from settings import SERIAL_PORT, SERIAL_BAUD

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
notes = []


def note(msg):
    print(msg, flush=True)
    notes.append(str(msg))


def raise_client():
    """Bring the RuneLite window to the front. A probe (or a routine) launched from a
    scheduled task opens a CONSOLE on the same desktop, and a console sitting over the 3D
    viewport makes the client report canvas mouse (-1,-1) for every point under it -- which
    reads exactly like a click that missed."""
    import ctypes
    user32 = ctypes.windll.user32
    hwnds = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "RuneLite" in buf.value:
                hwnds.append(hwnd)
        return True

    user32.EnumWindows(each, None)
    for h in hwnds:
        user32.ShowWindow(h, 3)          # SW_SHOWMAXIMIZED
        user32.SetForegroundWindow(h)
    return hwnds


with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
    time.sleep(2.0)
    note("raised RuneLite windows: %s" % (raise_client(),))
    time.sleep(1.0)
    controller.FIRMWARE_PROTOCOL = controller.probe_firmware_protocol(ser)
    input_state.firmware_protocol = controller.FIRMWARE_PROTOCOL

    st = controller.get_state() or {}
    note("dpi_scale=%s" % (controller.get_dpi_scale(),))
    note("canvas=%sx%s at (%s,%s) window=%sx%s at (%s,%s)" % (
        st.get("canvasWidth"), st.get("canvasHeight"),
        st.get("canvasScreenX"), st.get("canvasScreenY"),
        st.get("windowWidth"), st.get("windowHeight"),
        st.get("windowX"), st.get("windowY")))
    note("viewport=%sx%s real=%sx%s" % (
        st.get("viewportWidth"), st.get("viewportHeight"),
        st.get("realWidth"), st.get("realHeight")))

    pile = None
    for o in controller.get_objects(interactable=True) or []:
        if o.get("name") == "Offerings" and o.get("onScreen"):
            if pile is None or (o.get("distance") or 99) < (pile.get("distance") or 99):
                pile = o
    targets = [("canvas centre", (st.get("canvasScreenX", 0) + st.get("canvasWidth", 0) // 2,
                                  st.get("canvasScreenY", 0) + st.get("canvasHeight", 0) // 2))]
    if pile:
        note("pile: %s" % json.dumps({k: pile.get(k) for k in
                                      ("id", "x", "y", "screenX", "screenY", "distance")}))
        targets.append(("pile", (pile["screenX"], pile["screenY"])))
    targets.append(("inventory-ish", (1600, 440)))

    for name, (tx, ty) in targets:
        controller.move_to_human(ser, tx, ty, aim_radius=0)
        time.sleep(0.4)
        st2 = controller.get_state() or {}
        hover = controller.get_hover() or {}
        note("%-14s asked (%s,%s) -> client sees (%s,%s)  hover=%r/%r" % (
            name, tx, ty, st2.get("mouseScreenX"), st2.get("mouseScreenY"),
            hover.get("option"), hover.get("target")))

open(os.path.join(OUT, "vt-cursor-probe.txt"), "w", encoding="utf-8").write("\n".join(notes))
