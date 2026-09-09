"""Is the screen point the bridge reports for a Vale offerings pile actually ON the pile?

Moves the cursor to the reported point (and to a small grid around it) and reads /hover, which
names what the game would act on RIGHT NOW. Diagnostic for the 2026-09-09 VM session, where
every routine Claim resolved to "Walk here" while the same click from a settled standstill
worked. Runs IN THE GUEST on the desktop session; never alongside controller.py.
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


def nearest_pile():
    best = None
    for o in controller.get_objects(interactable=True) or []:
        if o.get("name") != "Offerings" or not o.get("onScreen"):
            continue
        if best is None or (o.get("distance") or 99) < (best.get("distance") or 99):
            best = o
    return best


with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
    time.sleep(2.0)
    controller.FIRMWARE_PROTOCOL = controller.probe_firmware_protocol(ser)
    input_state.firmware_protocol = controller.FIRMWARE_PROTOCOL

    pile = nearest_pile()
    note("pile: %s" % json.dumps({k: pile.get(k) for k in
                                  ("name", "id", "x", "y", "screenX", "screenY", "distance",
                                   "onScreen", "clickable", "usedClickbox")}))
    px, py = pile["screenX"], pile["screenY"]
    hits = []
    for dx, dy in [(0, 0), (0, -20), (0, 20), (-20, 0), (20, 0),
                   (0, -40), (0, 40), (-40, 0), (40, 0), (0, -60), (0, 60)]:
        controller.move_to_human(ser, px + dx, py + dy, aim_radius=0)
        time.sleep(0.35)
        hover = controller.get_hover() or {}
        target = (hover.get("target") or "").strip()
        option = (hover.get("option") or "").strip()
        note("  offset %+4d,%+4d -> %s / %s" % (dx, dy, option, target))
        if "offering" in target.lower():
            hits.append((dx, dy))
    note("ON-PILE offsets: %s" % hits)

open(os.path.join(OUT, "vt-hover-probe.txt"), "w", encoding="utf-8").write("\n".join(notes))
