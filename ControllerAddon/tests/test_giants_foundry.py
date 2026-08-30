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
    # 33 moulds, three parts, every score positive, display names unique
    assert len(gf.MOULDS) == 33
    parts = {"RICASSO": 0, "BLADE": 0, "TIP": 0}
    displays = set()
    for enum, (display, part, scores, purchasable) in gf.MOULDS.items():
        parts[part] += 1
        assert display not in displays
        displays.add(display)
        assert scores and all(v > 0 for v in scores.values())
    assert parts == {"RICASSO": 11, "BLADE": 11, "TIP": 11}


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


# ---------------------------------------------------------------------------
# Task 2: fill planner
# ---------------------------------------------------------------------------

FILL = DEFAULT_FILL = [
    {"item": "Mithril platebody", "metal": "MITHRIL", "bars_each": 4, "target_bars": 14},
    {"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 14},
]


def test_plan_fill_from_empty_matches_recording():
    # MEASURED: 14 mith via 4-bar bodies quantizes to 4 items (16 bars) -> 2 bars of
    # planned overflow at the 28 cap, which the human accepted. Loss 2 <= cap 2 -> ok.
    steps, err = gf.plan_fill(FILL, {}, max_overflow_loss=2)
    assert err is None
    assert steps == [
        {"item": "Mithril platebody", "bars_each": 4, "count": 4},
        {"item": "Adamant platelegs", "bars_each": 2, "count": 7},
    ]


def test_plan_fill_rejects_overflow_beyond_cap():
    steps, err = gf.plan_fill(FILL, {}, max_overflow_loss=1)
    assert steps == []
    assert "overflow" in err


def test_plan_fill_resumes_partial_crucible():
    # Mid-fill restart: 12 mithril + 14 adamant already in -> one more body finishes it.
    steps, err = gf.plan_fill(FILL, {"MITHRIL": 12, "ADAMANT": 14}, max_overflow_loss=2)
    assert err is None
    assert steps == [{"item": "Mithril platebody", "bars_each": 4, "count": 1}]


def test_plan_fill_full_crucible_is_done():
    steps, err = gf.plan_fill(FILL, {"MITHRIL": 14, "ADAMANT": 14}, max_overflow_loss=2)
    assert steps == [] and err is None


def test_plan_fill_short_config_errors():
    short = [{"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 20}]
    steps, err = gf.plan_fill(short, {}, max_overflow_loss=2)
    assert steps == []
    assert "20/28" in err


# ---------------------------------------------------------------------------
# Task 3: heat targeting
# ---------------------------------------------------------------------------

def test_tool_stats_are_the_measured_constants():
    # easy-giantsfoundry Stage.java, CONFIRMED live in the recording (hammer +20/-25 per
    # 5 ticks at 916->726; polish +10 per 2 ticks).
    assert gf.TOOL_STATS["TRIP_HAMMER"] == {"cycle_ticks": 5, "progress": 20, "heat": -25, "object": 44619}
    assert gf.TOOL_STATS["GRINDSTONE"] == {"cycle_ticks": 2, "progress": 10, "heat": 15, "object": 44620}
    assert gf.TOOL_STATS["POLISHING_WHEEL"] == {"cycle_ticks": 2, "progress": 10, "heat": -17, "object": 44621}


def test_actions_left_in_section():
    # 5 sections -> 200 per section. At completion 140, 60 to go: 3 hammer actions
    # (20 each) or 6 grind actions (10 each).
    assert gf.actions_left_in_section(140, 5, "TRIP_HAMMER") == 3
    assert gf.actions_left_in_section(140, 5, "GRINDSTONE") == 6
    # Exactly on a boundary: the whole next section remains.
    assert gf.actions_left_in_section(200, 5, "TRIP_HAMMER") == 10
    # No preform data -> 0, never a division by zero.
    assert gf.actions_left_in_section(0, 0, "TRIP_HAMMER") == 0


def test_heat_stop_target_enters_band_at_the_working_end():
    # Hammer cools (-25/action): enter near the TOP of the band so the whole section
    # rides down through it. 8 actions from min 830 -> 830+200=1030, clamped to max.
    assert gf.heat_stop_target("TRIP_HAMMER", 830, 1000, 8) == 1000
    # 3 actions -> 830+75 = 905, inside the band -> unclamped.
    assert gf.heat_stop_target("TRIP_HAMMER", 830, 1000, 3) == 905
    # Grindstone heats (+15): enter near the BOTTOM. 4 actions from max 590 -> 530.
    assert gf.heat_stop_target("GRINDSTONE", 410, 590, 4) == 530
    # Never below min.
    assert gf.heat_stop_target("GRINDSTONE", 410, 590, 20) == 410
