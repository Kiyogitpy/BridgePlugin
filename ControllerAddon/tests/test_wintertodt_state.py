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


def test_choose_brazier_keeps_current_when_unlit_with_alive_pyromancer():
    # Branch 2 positive case: current is down but fixable (we can light it).
    snap = reader(braziers={
        "NORTH_EAST": {"status": "UNLIT", "pyromancerStatus": "ALIVE"},
        "SOUTH_WEST": {"status": "LIT", "pyromancerStatus": "ALIVE"}
    })
    assert ws.choose_brazier(snap, current="NORTH_EAST") == "NORTH_EAST"


def test_choose_brazier_prefers_non_first_preferred_over_first_key():
    # Test hygiene: preferred should be respected even when it's not SOUTH_WEST.
    snap = reader(braziers={key: {"status": "BROKEN", "pyromancerStatus": "ALIVE"}
                            for key in ws.BRAZIER_KEYS})
    assert ws.choose_brazier(snap, current=None, preferred="NORTH_EAST") == "NORTH_EAST"


def test_choose_brazier_regression_prefers_alive_over_preferred_incapacitated():
    # Finding 1 regression: three incapacitated (including preferred), one alive.
    snap = reader(braziers={
        "SOUTH_WEST": {"status": "UNLIT", "pyromancerStatus": "INCAPACITATED"},
        "NORTH_WEST": {"status": "UNLIT", "pyromancerStatus": "INCAPACITATED"},
        "SOUTH_EAST": {"status": "UNLIT", "pyromancerStatus": "INCAPACITATED"},
        "NORTH_EAST": {"status": "BROKEN", "pyromancerStatus": "ALIVE"}
    })
    # Must return NORTH_EAST (the alive one), not SOUTH_WEST (preferred but incapacitated).
    assert ws.choose_brazier(snap, current=None, preferred="SOUTH_WEST") == "NORTH_EAST"


def test_reward_eligible_uses_the_threshold():
    assert ws.reward_eligible(reader(points=500)) is True
    assert ws.reward_eligible(reader(points=501)) is True
    assert ws.reward_eligible(reader(points=499)) is False
    assert ws.reward_eligible(reader(points=0)) is False
    assert ws.reward_eligible({}) is False


def inv(*names):
    """An inventory of {'id','name'} dicts. Dose counts ride in the name, as the game shows."""
    out = []
    for n in names:
        if n == "root":
            out.append({"id": ws.w.ITEM_ROOT, "name": "Bruma root"})
        elif n == "kindling":
            out.append({"id": ws.w.ITEM_KINDLING, "name": "Bruma kindling"})
        else:
            out.append({"id": 0, "name": n})
    return out


def test_kit_deficit_names_every_missing_tool():
    assert set(ws.kit_deficit(inv(), min_doses=0)) == {"axe", "knife", "tinderbox", "hammer"}
    full = inv("Bronze axe", "Knife", "Tinderbox", "Hammer")
    assert ws.kit_deficit(full, min_doses=0) == []


def test_potion_doses_counts_doses_not_potions():
    items = inv("Rejuvenation potion(4)", "Rejuvenation potion(2)")
    assert ws.potion_doses(items) == 6


def test_an_undosed_potion_name_counts_as_one_dose():
    assert ws.potion_doses(inv("Rejuvenation potion")) == 1


def test_kit_deficit_reports_potions_when_under_the_minimum():
    items = inv("Bronze axe", "Knife", "Tinderbox", "Hammer", "Rejuvenation potion(1)")
    assert ws.kit_deficit(items, min_doses=4) == ["potion"]
    assert ws.kit_deficit(items, min_doses=1) == []


def test_needs_drink_is_false_when_warmth_cannot_be_read():
    # Unreadable must never fire the emergency rung -- see warmth_percent's None contract.
    assert ws.needs_drink({}, drink_at=45) is False
    assert ws.needs_drink(reader(warmth=400), drink_at=45) is True
    assert ws.needs_drink(reader(warmth=600), drink_at=45) is False


def test_feed_item_prefers_kindling():
    items = inv("root", "root", "kindling")
    assert ws.feed_item(items, fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_KINDLING


def test_feed_item_accepts_roots_when_fletching_is_off():
    # THE DEADLOCK GUARD. With fletch=False nothing else will ever spend a root: chopping is
    # blocked by the full inventory and the fletch rung is disabled. feed and fletch must
    # between them accept every state a full inventory can be in.
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=False, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_ROOT


def test_feed_item_accepts_roots_near_the_round_end():
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=10,
                        feed_roots_near_end=True, feed_roots_below_energy=15) == ws.w.ITEM_ROOT


def test_feed_item_holds_roots_back_mid_round_when_fletching():
    items = inv(*(["root"] * 28))
    assert ws.feed_item(items, fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None


def test_feed_item_returns_none_with_nothing_feedable():
    assert ws.feed_item(inv("Knife"), fletch=True, energy_percent=80,
                        feed_roots_near_end=True, feed_roots_below_energy=15) is None
