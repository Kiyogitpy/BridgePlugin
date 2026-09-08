"""ControllerAddon/wintertodt_state.py -- the pure decision layer.

Pure means these tests need no client, no bridge and no serial port: every function takes plain
data and returns plain data. This is where the first live session's corrections will land, so
each fact is pinned to its source in the spec.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wintertodt_state as ws


def reader(**over):
    """A /api/minigames/wintertodt snapshot with everything nominal, overridable per test."""
    base = {
        "atWintertodt": True,
        "hudVisible": True,
        "energyVisible": True,
        "warmthVisible": True,
        "brazierStatusVisible": True,
        "roundActive": True,
        "points": 120,
        "energyPercent": 80,
        "warmth": 1000,
        "respawnDelay": 0,
        "secondsUntilRoundStart": 0,
        "rewardPool": 3,
        "warningNeeded": False,
        "areaAttackActive": False,
        "braziers": {
            key: {"brazier": key, "status": "LIT", "pyromancerStatus": "ALIVE"}
            for key in ws.BRAZIER_KEYS
        },
    }
    braziers = over.pop("braziers", None)
    base.update(over)
    if braziers:
        for key, entry in braziers.items():
            base["braziers"][key] = {"brazier": key, **entry}
    return base


def test_warmth_is_a_0_to_1000_scale_not_a_percentage():
    assert ws.warmth_percent(reader(warmth=1000)) == 100.0
    assert ws.warmth_percent(reader(warmth=455)) == 45.5


def test_warmth_percent_of_an_unreadable_snapshot_is_none_not_zero():
    # Zero would read as "about to die" and fire the emergency rung against a bridge blip.
    assert ws.warmth_percent({}) is None
    assert ws.warmth_percent(None) is None


def test_operational_is_recomputed_in_python_because_gson_omits_the_java_method():
    assert ws.brazier_operational({"status": "LIT", "pyromancerStatus": "ALIVE"}) is True
    assert ws.brazier_operational({"status": "LIT", "pyromancerStatus": "INCAPACITATED"}) is False
    assert ws.brazier_operational({"status": "UNLIT", "pyromancerStatus": "ALIVE"}) is False
    assert ws.brazier_operational(None) is False


def test_choose_brazier_sticks_with_the_one_we_are_already_working():
    snap = reader()
    assert ws.choose_brazier(snap, current="NORTH_EAST") == "NORTH_EAST"


def test_choose_brazier_leaves_a_brazier_whose_pyromancer_is_down():
    # A downed pyromancer means the brazier CANNOT be relit (wiki), and healing is out of scope,
    # so staying put would idle us for the rest of the round.
    snap = reader(braziers={"NORTH_EAST": {"status": "UNLIT",
                                           "pyromancerStatus": "INCAPACITATED"}})
    assert ws.choose_brazier(snap, current="NORTH_EAST") != "NORTH_EAST"


def test_choose_brazier_falls_back_to_the_preferred_one_when_none_is_operational():
    snap = reader(braziers={key: {"status": "BROKEN", "pyromancerStatus": "ALIVE"}
                            for key in ws.BRAZIER_KEYS})
    assert ws.choose_brazier(snap, current=None, preferred="SOUTH_WEST") == "SOUTH_WEST"


def test_choose_brazier_returns_none_when_the_hud_says_nothing():
    assert ws.choose_brazier({"braziers": {}}, current=None) is None


def test_round_active_is_the_readers_own_field_not_our_guess():
    assert ws.round_active(reader(roundActive=True)) is True
    assert ws.round_active(reader(roundActive=False)) is False
    assert ws.round_active({}) is False
