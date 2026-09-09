"""ONE Claim click at a Vale totem offerings pile, then a look at what the game did.

Diagnostic for the 2026-09-09 VM session: every routine Claim registered as
`Claim Offerings` in the recorder and then silently did nothing, while the same code path
had worked on the host an hour earlier. Runs IN THE GUEST (the Arduino is captured there), on
the desktop session (vmexec.py --interactive), never alongside controller.py (one process per
COM port). Writes its findings next to the routine logs on the share.

    python tools_vt_claim_probe.py <site_number>
"""
import json
import os
import shutil
import sys
import time

import serial

import controller
import input_state
import travel
import vale_totem_route as route
from settings import SERIAL_PORT, SERIAL_BAUD

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
site = int(sys.argv[1]) if len(sys.argv) > 1 else 8
entry = route.SITES[site]
notes = []


def note(msg):
    print(msg, flush=True)
    notes.append(msg)


def offerings_count():
    for item in controller.get_inventory() or []:
        if (item.get("name") or "").lower() == "vale offerings":
            return item.get("qty")
    return None


with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
    time.sleep(2.0)
    controller.FIRMWARE_PROTOCOL = controller.probe_firmware_protocol(ser)
    input_state.firmware_protocol = controller.FIRMWARE_PROTOCOL
    note("firmware v%d" % controller.FIRMWARE_PROTOCOL)

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.ser = ser
    ctx.events = None

    sx, sy = entry["stand"]
    player = controller.get_player() or {}
    note("player at (%s,%s), stand tile %s" % (player.get("x"), player.get("y"), (sx, sy)))
    if max(abs((player.get("x") or 0) - sx), abs((player.get("y") or 0) - sy)) > 3:
        note("walking to the stand tile")
        for _ in range(3):
            travel._hop_to(ctx, sx, sy, route.PLANE)
            player = controller.get_player() or {}
            if max(abs(player["x"] - sx), abs(player["y"] - sy)) <= 3:
                break
        controller.wait_until_player_idle(timeout_s=8.0)

    before = offerings_count()
    ox, oy = entry["offerings"]
    obj = controller.get_object_at(ox, oy, route.PLANE, entry["offerings_id"])
    note("pile object: %s" % json.dumps(obj)[:300])
    if not obj:
        note("no pile object in view -- stopping")
        sys.exit(1)
    state = controller.get_state()
    note("state before: focused=%s dialog=%s invTab=%s" % (
        state.get("windowFocused"), state.get("dialogOpen"), state.get("inventoryTabOpen")))
    refresh = controller.object_refresh_target(ox, oy, route.PLANE, entry["offerings_id"])
    ok = controller.click_entity(ser, obj, refresh_target=refresh)
    t0 = time.time()
    note("click_entity -> %r" % (ok,))
    time.sleep(1.0)
    note("+1.0s last-action: %s" % json.dumps(controller.get_last_action())[:300])
    time.sleep(1.5)
    shot = controller.api_get("/screenshot", {"name": "vt-claim-probe"}, timeout=6) or {}
    note("+2.5s screenshot: %s" % shot.get("path"))
    try:
        shutil.copy(shot["path"], os.path.join(OUT, "vt-claim-probe.png"))
    except Exception as e:
        note("copy failed: %s" % e)
    dump = controller.get_widgets_dump() or []
    texts = [(w.get("group"), w.get("child"), (w.get("text") or "")[:80])
             for w in dump if (w.get("text") or "").strip()
             and w.get("group") not in (161, 160)]
    note("visible texts: %s" % json.dumps(texts)[:1500])
    for i in range(6):
        time.sleep(1.0)
        after = offerings_count()
        note("+%.1fs offerings %s -> %s, player %s" % (
            time.time() - t0, before, after,
            {k: (controller.get_player() or {}).get(k) for k in ("x", "y", "animation")}))
        if after != before:
            break

open(os.path.join(OUT, "vt-claim-probe.txt"), "w", encoding="utf-8").write("\n".join(notes))
