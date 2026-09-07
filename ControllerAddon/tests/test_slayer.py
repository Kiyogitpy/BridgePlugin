import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import banker
import controller
from routines import slayer


class _State:
    def __init__(self, config):
        self.config = config
        self.data = {}
        self.stops = []

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self, config):
        self.ser = None
        self.state = _State(config)


@pytest.fixture
def quiet_tick(monkeypatch):
    """Stub everything tick() touches except the thing under test, and record what ran."""
    ran = []
    monkeypatch.setattr(controller, "get_ammo_count", lambda: None)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": True, "inCombat": False})
    monkeypatch.setattr(slayer, "maintain_defensive_prayer",
                        lambda ctx, combat: ran.append("defensive_prayer"))
    monkeypatch.setattr(slayer, "maintain_offensive_prayer",
                        lambda ctx, combat: ran.append("offensive_prayer"))
    monkeypatch.setattr(slayer, "maintain_supplies", lambda ctx: ran.append("supplies"))
    monkeypatch.setattr(slayer, "maybe_loot", lambda ctx: ran.append("loot"))
    monkeypatch.setattr(slayer, "maintain_safespot", lambda ctx: ran.append("safespot"))
    monkeypatch.setattr(slayer, "engage_target", lambda ctx, combat: ran.append("engage"))
    monkeypatch.setattr(banker, "return_to_spot",
                        lambda ctx: ran.append("return_to_spot") or True)
    return ran


def test_tick_spends_a_retry_on_the_walk_home_before_fighting_anything(quiet_tick):
    """USER-REPORTED 2026-09-07 ("ends up stuck at Ferox"). banker.bank_up walks home itself,
    but a walk that gives up partway now KEEPS its anchor rather than discarding it -- which is
    only worth anything if some later tick actually spends the retry. Without this the routine
    just fought on from wherever the walk stopped, which is the reported symptom."""
    ctx = _Ctx({"bank": {"enabled": False}, "loot": {"enabled": True}})
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3200, "y": 3200, "plane": 0}
    ctx.state.data["current_target_index"] = 41

    slayer.tick(ctx)

    assert "return_to_spot" in quiet_tick
    # Food and prayer must not be starved by a return trip...
    assert "supplies" in quiet_tick and "defensive_prayer" in quiet_tick
    # ...but nothing commits us to a fight at the bank we're walking away from.
    assert "engage" not in quiet_tick and "safespot" not in quiet_tick
    assert ctx.state.data["current_target_index"] is None


def test_tick_is_unaffected_when_no_walk_home_is_owed(quiet_tick):
    """The common case: no anchor recorded, so the check costs nothing and the normal
    loot -> safespot -> engage ladder runs exactly as before."""
    ctx = _Ctx({"bank": {"enabled": False}, "loot": {"enabled": True}})

    slayer.tick(ctx)

    assert "return_to_spot" not in quiet_tick
    assert quiet_tick == ["defensive_prayer", "offensive_prayer", "supplies",
                          "loot", "safespot", "engage"]
