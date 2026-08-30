import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import time

import pytest

import giants_foundry as gf
import controller
import banker


# ---------------------------------------------------------------------------
# Task 1: mould selection
# ---------------------------------------------------------------------------

ALL_MOULDS = list(gf.MOULDS)

# The account's unlocks during the 2026-08-31 recording: all 18 defaults plus the five
# purchasables its picks prove it owned (SPIKER notably absent).
RECORDING_UNLOCKS = [m for m, v in gf.MOULDS.items() if v[3] is False] + [
    "JUGGERNAUT_FORTE", "CHOPPER_FORTE_PLUS_ONE", "FLAMBERGE_BLADE",
    "SERPENT_BLADE", "NEEDLE_POINT",
]


def test_mould_table_shape():
    # 32 moulds, three parts, every score positive, display names unique
    assert len(gf.MOULDS) == 32
    parts = {"RICASSO": 0, "BLADE": 0, "TIP": 0}
    displays = set()
    for enum, (display, part, scores, purchasable) in gf.MOULDS.items():
        parts[part] += 1
        assert display not in displays
        displays.add(display)
        assert scores and all(v > 0 for v in scores.values())
    assert parts == {"RICASSO": 11, "BLADE": 11, "TIP": 10}


def test_best_moulds_narrow_flat_matches_recording():
    # MEASURED 2026-08-31 (recording, second sword): the human picked exactly these.
    # Needle Point ties The Point! at 22; stable max keeps table order (Needle Point first),
    # which is also what the human chose.
    best = gf.best_moulds(["NARROW", "FLAT"], ALL_MOULDS)
    assert best == {"RICASSO": "CHOPPER_FORTE_PLUS_ONE",
                    "BLADE": "SERPENT_BLADE",
                    "TIP": "NEEDLE_POINT"}


def test_best_moulds_narrow_spiked_matches_recording_unlocks():
    # First sword: SPIKER (the global best forte at 23) was not unlocked; with the
    # recording's unlock set the argmax is what the human picked.
    best = gf.best_moulds(["NARROW", "SPIKED"], RECORDING_UNLOCKS)
    assert best == {"RICASSO": "JUGGERNAUT_FORTE",
                    "BLADE": "FLAMBERGE_BLADE",
                    "TIP": "NEEDLE_POINT"}


def test_best_moulds_empty_usable_falls_back_to_all():
    # usableMoulds == [] means "cannot tell" from an un-rebuilt client, not "nothing usable".
    best = gf.best_moulds(["LIGHT", "FLAT"], [])
    assert set(best) == {"RICASSO", "BLADE", "TIP"}
