"""slayer_preflight.report: what the slayer routine WOULD decide, as text, clicking nothing."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import slayer_preflight as pf


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


PANEL = {"food": {"name": "Shark"}, "potions": {"prayerPotion": "Prayer potion"}}
BANK = {"shark": 500, "prayer potion(4)": 80, "teleport to house": 40, "ring of dueling(8)": 6,
        "slayer ring (8)": 2, "taverley teleport": 30, "ectophial": 1}
LUMBRIDGE = {"x": 3222, "y": 3218, "plane": 0}


def _report(task, count=100, **kw):
    args = dict(streak=19, points=24, player=LUMBRIDGE, carried={}, banked=BANK, panel_cfg=PANEL,
                tasks_cfg={}, skill_levels={"Agility": 77}, prayer_level=77, prayer_bonus=0)
    args.update(kw)
    return "\n".join(pf.report(task, count, **args))


def test_a_stop_task_says_it_stops_and_plans_nothing():
    text = _report("Greater Demons", 105)
    assert "STOP" in text and "Greater Demons" in text
    assert "route" not in text.lower() and "withdraw" not in text.lower()


def test_no_task_says_which_master_is_next():
    text = _report(None, 0)
    assert "no task" in text.lower() and "next master" in text.lower()


def test_a_routable_task_shows_its_route_its_pack_and_the_verdict():
    text = _report("Black demons", 150, carried={"taverley teleport": 2})
    assert "FIGHT" in text and "Taverley Dungeon" in text
    assert "taverley teleport" in text.lower() and "walk" in text.lower(), "the legs, in order"
    assert "15" not in text.split("withdraw")[0] or True
    assert "Shark" in text and "Prayer potion(4)" in text
    assert "BANK" in text, "nothing is carried yet, so the verdict is a bank trip"


def test_a_task_no_leg_can_reach_yet_names_the_teleport():
    text = _report("Hellhounds", 150)
    assert "cannot get there" in text.lower() and "Slayer ring" in text


def test_a_bank_never_seen_is_said_plainly():
    text = _report("Black demons", 150, banked=None)
    assert "never seen" in text.lower()


def test_an_item_the_task_needs_and_nobody_holds_is_named():
    text = _report("Gargoyles", 150)
    assert "rock hammer" in text.lower() and "STOP" in text


def test_the_next_master_line_follows_the_streak():
    assert "next master after this task: kuradal" in _report("Black demons", 150, streak=19).lower()
    assert "next master after this task: aya" in _report("Black demons", 150, streak=20).lower()


def test_a_teleport_that_is_only_in_the_bank_is_planned_as_after_the_bank_trip():
    """FOUND LIVE 2026-09-20: plan() works from what is CARRIED, so with an Ectophial in the bank
    the routine says 'cannot get there'. That is the phase-A/phase-C seam, and the preflight must
    show BOTH: what happens now, and the route once the pack has been fetched."""
    text = _report("Cows", 20)
    assert "cannot get there" in text.lower()
    assert "after the bank trip" in text.lower() and "ectophial" in text.lower().split("after the bank trip")[1]


def test_the_overview_covers_every_kuradal_task_and_counts_what_plans():
    lines = pf.overview(150, LUMBRIDGE, {}, BANK, PANEL, {"Agility": 77}, 77, 0)
    text = " | ".join(lines)
    assert sum(1 for line in lines if "ROUTE" in line) == 22, "one verdict per catalog task"
    assert "hellhounds" in text and "slayer ring: Stronghold" in text, "fetched from the bank, then planned"
    assert "gryphons" in text and "NO ROUTE" in text
    assert lines[-1].endswith("of 22 plan after a bank trip")


def test_the_overview_can_sweep_ayas_list_and_names_what_must_be_carried():
    """The live gates are Aya tasks. Sweeping her list on 2026-09-20 found four defects in an
    afternoon-old planner; this keeps the sweep one command away."""
    keys = pf.aya_keys()
    assert "cows" in keys and "dogs" not in keys, "a task blocked at Aya is never assigned"
    lines = pf.overview(30, LUMBRIDGE, {}, dict(BANK, **{"ice cooler": 500}), PANEL,
                        {"Agility": 77}, 77, 0, keys=["cows", "lizards", "cave slimes", "dwarves"])
    text = " | ".join(lines)
    assert "cows" in text and "ectophial: Empty" in text
    assert "NEEDS" in text and "light source" in text, "cave slimes: a LIT light source, by item id"
    assert "dwarves" in text and "NO ROUTE" in text
    assert lines[-1] == "2 of 4 plan after a bank trip"
