"""The finishing-blow table is DATA ported from Slayer Best in Bank; these tests keep it honest.

Plan E0. The facts under test came from three places that AGREE, and the tests say so explicitly
so a future edit cannot quietly drift from all three at once:
  - the mechanic and the item: SBIB's SmartSupplyAdvisor "Finisher" rules (survey §5, §6);
  - max hp and the hitpoint threshold: the OSRS Wiki;
  - the resulting hpRatio: Microbot's live-proven constants (gargoyles < 3, lizards < 5).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import slayer_catalog
import slayer_finishers


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


def test_gargoyle_ratio_matches_microbots_live_proven_constant():
    """MEASURED live 2026-09-21: a gargoyle mid-fight reads hpRatio 23 of hpScale 30. The wiki
    puts the smash at 9 of 105 hp. Our arithmetic must land on Microbot's measured `< 3`."""
    f = slayer_finishers.get("gargoyles")
    assert (f.max_hp, f.smash_at) == (105, 9)
    assert f.ratio == 2, "30 * 9 // 105 == 2, i.e. Microbot's `healthRatio < 3`"


def test_every_finisher_item_resolves_to_real_item_names():
    """A label with no entry in REQUIREMENT_ITEMS would make the hook search for an item by a
    name we invented -- the exact failure the phase-B review caught in required_items."""
    for name, f in slayer_finishers.FINISHERS.items():
        names = slayer_catalog.REQUIREMENT_ITEMS.get(f.item)
        assert names, f"{name}: finisher item {f.item!r} is not in REQUIREMENT_ITEMS"


def test_the_two_health_sentinels_never_trigger_a_smash():
    """hpRatio 0 is DEAD and -1 is NEVER DAMAGED. They mean opposite things and neither is a
    reason to smash; confusing them is the whole risk this table carries."""
    f = slayer_finishers.get("gargoyles")
    assert slayer_finishers.should_finish(f, {"hpRatio": 0}) is False, "0 is dead, not low"
    assert slayer_finishers.should_finish(f, {"hpRatio": -1}) is False, "-1 is undamaged, not low"


def test_smash_fires_only_inside_the_window():
    f = slayer_finishers.get("gargoyles")
    assert slayer_finishers.should_finish(f, {"hpRatio": 1}) is True
    assert slayer_finishers.should_finish(f, {"hpRatio": 2}) is True, "inclusive upper bound"
    assert slayer_finishers.should_finish(f, {"hpRatio": 3}) is False
    # The real reading taken off the live client, to pin that a healthy monster is left alone.
    assert slayer_finishers.should_finish(f, {"hpRatio": 23, "hpScale": 30}) is False


def test_a_task_with_no_finisher_is_none_and_never_smashes():
    """Every other task must pay nothing for this table existing."""
    for name in ("hellhounds", "abyssal demons", "cows", "", None):
        assert slayer_finishers.get(name) is None
    assert slayer_finishers.should_finish(None, {"hpRatio": 1}) is False


def test_names_resolve_the_way_the_bridge_spells_them():
    """GET /slayer sends the task name as the game spells it; aya_tasks and slayer_catalog both
    forgive a singular/plural slip, and so must this."""
    assert slayer_finishers.get("Gargoyles") is slayer_finishers.get("gargoyle")
    assert slayer_finishers.get("Mutated zygomites") is slayer_finishers.get("zygomites")
    assert slayer_finishers.get("Desert lizards") is slayer_finishers.get("lizards")


def test_zygomites_is_a_kuradal_task_and_carries_its_finisher_already():
    """The catalog's `carry` and this table must name the SAME item for a task in both, or the
    routine would pack one thing and try to smash with another."""
    spec = slayer_catalog.get("mutated zygomites")
    f = slayer_finishers.get("mutated zygomites")
    assert f.item in spec.carry
    garg = slayer_catalog.get("gargoyles")
    assert slayer_finishers.get("gargoyles").item in garg.carry


# ---------------- the autokill unlock gate ----------------
# MEASURED live 2026-09-21: this account reads 4027=1 (gargoyles), 4028=1 (rockslugs),
# 4029=1 (desert lizards), 4030=0 (zygomites). Three of the four are already automatic.

LIVE_UNLOCKS = {4027: 1, 4028: 1, 4029: 1, 4030: 0}


def test_an_owned_unlock_means_no_click():
    """Smashing a monster the game already finishes for us wastes a click on EVERY kill, for
    hours, unattended. This is the guard against that."""
    for task in ("gargoyles", "rockslugs", "lizards"):
        f = slayer_finishers.get(task)
        assert slayer_finishers.needed(f, LIVE_UNLOCKS) is False, f"{task} is auto on this account"


def test_zygomites_is_the_one_that_still_needs_a_click_here():
    f = slayer_finishers.get("mutated zygomites")
    assert slayer_finishers.needed(f, LIVE_UNLOCKS) is True


def test_the_gate_fails_open_when_the_varbit_cannot_be_read():
    """Unlike most gates here, unreadable means DO the click: a wasted click costs one click, a
    skipped one means the monster never dies and the task never advances."""
    f = slayer_finishers.get("gargoyles")
    assert slayer_finishers.needed(f, {}) is True
    assert slayer_finishers.needed(f, None) is True
    assert slayer_finishers.needed(f, {4027: None}) is True


def test_the_point_total_is_never_consulted():
    """The perk is a one-time purchase, so current points cannot answer this -- an earlier draft
    reasoned '99 points < 120, therefore no perk' and was wrong while the account killed gargoyles
    perfectly well. Only the varbit decides."""
    f = slayer_finishers.get("gargoyles")
    assert slayer_finishers.needed(f, {4027: 1}) is False, "owned perk, regardless of points"


def test_no_finisher_never_needs_a_click():
    assert slayer_finishers.needed(None, LIVE_UNLOCKS) is False
